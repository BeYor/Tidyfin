"""v0.3: titles removed outside Tidyfin, collection window, other users' stars and
"Continue watching" (time-limited), and why a title was saved."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tidyfin.cleanup import CleanupEngine
from tidyfin.cycle import Context, run_cycle
from tidyfin.posters import PosterService
from tidyfin.profiles import preset_profile
from tidyfin.settings import LanguageSettings, Settings
from tidyfin.store import Store
from tests.helpers import FakeArr, FakeJellyfin


def utc(days_ago=0):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(":memory:")
        self.s = Settings(dry_run=False, setup_complete=True, cleanup_start_percent=0, manual_user_id="u-admin",
                          leaving_movies_collection_id="colM", leaving_tv_collection_id="colT",
                          language=LanguageSettings(deep_scan=False, profiles=[]))
        self.jf, self.radarr, self.sonarr = FakeJellyfin(), FakeArr("radarr"), FakeArr("sonarr")
        self.posters = PosterService(self.jf, Path(self.tmp.name) / "backups")
        self.jf.add_movie(10, "jm10", "Old")
        self.radarr.add_movie(1, 10, "Old", file_days_ago=100)
        self.jf.add_movie(11, "jm11", "Other")
        self.radarr.add_movie(2, 11, "Other", file_days_ago=100)

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self):
        return Context(self.s, self.store, self.jf, self.radarr, self.sonarr, self.posters)

    def engine(self):
        return CleanupEngine(self.s, self.store, self.jf, self.radarr, self.sonarr)


class GoneTitleTests(Base):
    """The 404 / 400 in the log: titles deleted by hand in Radarr while still queued."""

    def schedule_manual(self, key, rid, tmdb, jid, title):
        st = self.store.load_manual_state()
        st[key] = {"title": title, "media_type": "movie", "tmdb_id": tmdb, "tvdb_id": None, "arr_id": rid, "jellyfin_id": jid,
                   "size_bytes": 5, "marked_at": utc().isoformat(), "delete_at": (utc() + timedelta(days=3)).isoformat(), "origin": "dashboard"}
        self.store.save_manual_state(st)

    def test_manual_entry_of_a_title_removed_in_radarr_is_dropped_quietly(self):
        self.s.badges.countdown_show_within_days = 0
        self.schedule_manual("movie_10", 1, 10, "jm10", "Old")
        self.schedule_manual("movie_11", 2, 11, "jm11", "Other")
        self.radarr._items = [i for i in self.radarr._items if i["id"] != 1]      # deleted by hand
        del self.jf.items_by_id["jm10"], self.jf.movies["10"]
        self.jf.posters.pop("jm10")
        self.jf.uploads.clear()
        run_cycle(self.ctx())
        self.assertEqual(list(self.store.load_manual_state()), ["movie_11"])
        self.assertEqual([u[0] for u in self.jf.uploads], ["jm11"])                 # no upload to the dead id
        self.assertEqual(self.jf.collections["colM"], {"jm11"})                      # the valid one still lands in the collection
        event = [h for h in self.store.list_history()["items"] if h["event"] == "dequeued"][0]
        self.assertEqual(event["details"]["reason"], "removed_elsewhere")

    def test_a_dead_jellyfin_id_never_reaches_posters_or_collections(self):
        """Radarr still lists the title (e.g. Jellyfin has not rescanned yet)."""
        self.s.badges.countdown_show_within_days = 0
        self.schedule_manual("movie_10", 1, 10, "jm10", "Old")
        self.schedule_manual("movie_11", 2, 11, "jm11", "Other")
        del self.jf.items_by_id["jm10"], self.jf.movies["10"]
        self.jf.posters.pop("jm10")
        self.jf.uploads.clear()
        run_cycle(self.ctx())
        self.assertEqual({u[0] for u in self.jf.uploads}, {"jm11"})
        self.assertEqual(self.jf.collections["colM"], {"jm11"})

    def test_a_moved_title_gets_its_new_jellyfin_id(self):
        self.s.badges.countdown_show_within_days = 0
        self.schedule_manual("movie_10", 1, 10, "old-id", "Old")
        run_cycle(self.ctx())
        self.assertEqual(self.store.load_manual_state()["movie_10"]["jellyfin_id"], "jm10")
        self.assertIn("jm10", self.jf.collections["colM"])

    def test_nothing_is_dropped_when_jellyfin_cannot_be_asked(self):
        self.s.badges.countdown_show_within_days = 0
        self.schedule_manual("movie_11", 2, 11, "jm11", "Other")
        self.jf.exists_ok = False
        run_cycle(self.ctx())
        self.assertIn("jm11", self.jf.collections["colM"])

    def test_radarr_outage_never_empties_the_manual_queue(self):
        self.schedule_manual("movie_11", 2, 11, "jm11", "Other")
        self.radarr.fail = True
        run_cycle(self.ctx())
        self.assertIn("movie_11", self.store.load_manual_state())


class GatedQueueTests(Base):
    """The Nobody / Nobody 2 case: removed by hand in Radarr while the disk-usage gate keeps the queue frozen."""

    def queue_up(self):
        self.s.badges.countdown_show_within_days = 0
        run_cycle(self.ctx())
        self.assertEqual(set(self.store.load_auto_state()), {"movie_10", "movie_11"})

    def test_removed_titles_leave_the_queue_even_while_the_gate_is_closed(self):
        self.queue_up()
        self.s.cleanup_start_percent = 100                     # the gate is now closed: the queue is otherwise untouched
        self.radarr._items = [i for i in self.radarr._items if i["id"] != 1]
        del self.jf.items_by_id["jm10"], self.jf.movies["10"]
        run_cycle(self.ctx())
        self.assertEqual(set(self.store.load_auto_state()), {"movie_11"})                      # movie_10 is gone
        ev = [h for h in self.store.list_history()["items"] if h["event"] == "dequeued"][0]
        self.assertEqual((ev["key"], ev["details"]["reason"]), ("movie_10", "removed_elsewhere"))

    def test_the_gate_still_freezes_everything_else(self):
        self.queue_up()
        self.s.cleanup_start_percent = 100
        run_cycle(self.ctx())
        self.assertEqual(set(self.store.load_auto_state()), {"movie_10", "movie_11"})

    def test_a_title_that_lost_its_file_is_dropped_too(self):
        self.queue_up()
        self.s.cleanup_start_percent = 100
        for i in self.radarr._items:
            if i["id"] == 1:
                i["hasFile"] = False
        run_cycle(self.ctx())
        self.assertEqual(set(self.store.load_auto_state()), {"movie_11"})

    def test_an_unreachable_radarr_prunes_nothing(self):
        self.queue_up()
        self.radarr.fail = True
        self.radarr._items = []
        run_cycle(self.ctx())
        self.assertEqual(set(self.store.load_auto_state()), {"movie_10", "movie_11"})

    def test_no_repeated_log_line_for_a_title_missing_in_jellyfin(self):
        self.queue_up()
        del self.jf.items_by_id["jm10"], self.jf.movies["10"]
        with self.assertLogs("tidyfin", level="INFO") as logs:
            for _ in range(3):
                run_cycle(self.ctx())
        self.assertLessEqual(sum("movie_10 is queued but not in Jellyfin" in m for m in logs.output), 1)


class CollectionWindowTests(Base):
    def test_title_joins_the_collection_only_inside_the_countdown_window(self):
        self.s.movies.cleanup_delay_days = 30
        self.s.badges.countdown_show_within_days = 14
        run_cycle(self.ctx())
        self.assertEqual(self.jf.collections.get("colM", set()), set())                 # deadline 30 days away
        st = self.store.load_auto_state()
        for info in st.values():
            info["delete_at"] = (utc() + timedelta(days=5)).isoformat()
        self.store.save_auto_state(st)
        run_cycle(self.ctx())
        self.assertEqual(self.jf.collections["colM"], {"jm10", "jm11"})

    def test_window_zero_means_always(self):
        self.s.movies.cleanup_delay_days = 30
        self.s.badges.countdown_show_within_days = 0
        run_cycle(self.ctx())
        self.assertEqual(self.jf.collections["colM"], {"jm10", "jm11"})


class OtherUsersActivityTests(Base):
    def setUp(self):
        super().setUp()
        self.s.favorites_protect_days = 60
        self.s.resume_protect_days = 30

    def queued(self):
        return set(self.engine().process_automatic_cleanup())

    def test_a_star_of_another_user_protects_for_a_limited_time(self):
        self.jf.favorites_by_user["u-ola"] = [{"Id": "jm10", "ProviderIds": {"Tmdb": "10"}, "Type": "Movie"}]
        self.assertEqual(self.queued(), {"movie_11"})
        seen = self.store.get_json("favorite_first_seen")
        self.store.set_json("favorite_first_seen", {k: utc(61).isoformat() for k in seen})      # starred 61 days ago
        self.assertEqual(self.queued(), {"movie_10", "movie_11"})

    def test_the_star_user_does_not_protect_their_stars_are_deletion_marks(self):
        self.jf.favorites_by_user["u-admin"] = [{"Id": "jm10", "ProviderIds": {"Tmdb": "10"}, "Type": "Movie"}]
        self.assertEqual(self.queued(), {"movie_10", "movie_11"})

    def test_unstarring_forgets_the_date(self):
        self.jf.favorites_by_user["u-ola"] = [{"Id": "jm10", "Type": "Movie"}]
        self.queued()
        self.jf.favorites_by_user["u-ola"] = []
        self.queued()
        self.assertEqual(self.store.get_json("favorite_first_seen"), {})

    def test_continue_watching_protects_while_recent(self):
        self.jf.resume_by_user["u-ola"] = [{"item_id": "jm10", "last_played": utc(10)}]
        self.assertEqual(self.queued(), {"movie_11"})
        self.jf.resume_by_user["u-ola"] = [{"item_id": "jm10", "last_played": utc(31)}]
        self.assertEqual(self.queued(), {"movie_10", "movie_11"})

    def test_protection_can_be_switched_off(self):
        self.s.favorites_protect_days = self.s.resume_protect_days = 0
        self.jf.favorites_by_user["u-ola"] = [{"Id": "jm10", "Type": "Movie"}]
        self.jf.resume_by_user["u-ola"] = [{"item_id": "jm11", "last_played": utc(1)}]
        self.assertEqual(self.queued(), {"movie_10", "movie_11"})

    def test_unreadable_activity_means_no_new_candidates_and_an_untouched_queue(self):
        before = self.queued()
        self.jf.activity_ok = False
        self.assertEqual(self.queued(), before)                       # queue as it was, nothing dequeued
        self.s.dry_run = False
        self.radarr.add_movie(3, 12, "Late", file_days_ago=100)
        self.jf.add_movie(12, "jm12", "Late")
        self.assertNotIn("movie_12", self.queued())                   # not judged while we cannot tell

    def test_a_queued_title_that_gets_protected_leaves_the_queue_with_the_reason(self):
        self.queued()
        self.jf.favorites_by_user["u-ola"] = [{"Id": "jm10", "Type": "Movie"}]
        self.queued()
        self.assertNotIn("movie_10", self.store.load_auto_state())
        ev = [h for h in self.store.list_history()["items"] if h["event"] == "dequeued"][0]
        self.assertEqual((ev["key"], ev["details"]["reason"]), ("movie_10", "favorited"))

    def test_watching_is_recorded_as_the_reason(self):
        self.queued()
        self.jf.last_played["jm10"] = utc(2)
        self.queued()
        ev = [h for h in self.store.list_history()["items"] if h["event"] == "dequeued"][0]
        self.assertEqual(ev["details"]["reason"], "watched")


if __name__ == "__main__":
    unittest.main()
