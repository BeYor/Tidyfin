import unittest
from datetime import datetime, timedelta, timezone

from tidyfin.cleanup import CleanupEngine, build_queue_view
from tidyfin.settings import DiskThreshold, Settings
from tidyfin.store import Store
from tests.helpers import FakeArr, FakeJellyfin


def utc(days_ago=0):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.s = Settings(dry_run=False, manual_user_id="admin", cleanup_start_percent=0)
        self.jf = FakeJellyfin()
        self.radarr, self.sonarr = FakeArr("radarr"), FakeArr("sonarr")
        self.jf.add_movie(10, "jm10", "Old movie")
        self.radarr.add_movie(1, 10, "Old movie", file_days_ago=100)

    def engine(self):
        return CleanupEngine(self.s, self.store, self.jf, self.radarr, self.sonarr)

    def make_due(self, key="movie_10"):
        st = self.store.load_auto_state()
        st[key]["delete_at"] = utc(1).isoformat()
        self.store.save_auto_state(st)


class AutoCleanupTests(Base):
    def test_old_unwatched_enters_queue_with_midnight_deadline(self):
        out = self.engine().process_automatic_cleanup()
        self.assertIn("movie_10", out)
        info = self.store.load_auto_state()["movie_10"]
        delete_at = datetime.fromisoformat(info["delete_at"])
        self.assertEqual((delete_at.hour, delete_at.minute), (0, 0))
        self.assertEqual(delete_at.date(), (datetime.now(timezone.utc) + timedelta(days=14)).date())
        self.assertEqual((info["size_bytes"], info["arr_id"]), (5_000_000_000, 1))
        self.assertEqual(self.store.list_history()["items"][0]["event"], "queued")

    def test_recent_file_or_recent_play_is_not_queued(self):
        self.radarr._items[0]["movieFile"]["dateAdded"] = utc(5).isoformat()
        self.assertEqual(self.engine().process_automatic_cleanup(), {})
        self.radarr._items[0]["movieFile"]["dateAdded"] = utc(100).isoformat()
        self.jf.last_played["jm10"] = utc(3)
        self.assertEqual(self.engine().process_automatic_cleanup(), {})

    def test_protection_tag_is_case_insensitive_and_includes_protect_tag(self):
        self.radarr._tags = {7: "Do-Not-Delete"}
        self.radarr._items[0]["tags"] = [7]
        self.assertEqual(self.engine().process_automatic_cleanup(), {})
        self.radarr._tags = {8: "Custom-Keep"}
        self.radarr._items[0]["tags"] = [8]
        self.s.protect_tag = "custom-keep"
        self.assertEqual(self.engine().process_automatic_cleanup(), {})

    def test_due_item_is_deleted_and_reported(self):
        self.engine().process_automatic_cleanup()
        self.make_due()
        e = self.engine()
        out = e.process_automatic_cleanup()
        self.assertEqual((self.radarr.deleted, self.store.load_auto_state(), "movie_10" in out), ([1], {}, False))
        self.assertEqual(e.stats["deleted"], 1)
        self.assertEqual([ev["type"] for ev in e.events], ["deleted"])
        self.assertEqual(self.store.list_history()["items"][0]["event"], "deleted")

    def test_dry_run_never_deletes(self):
        self.s.dry_run = True
        self.engine().process_automatic_cleanup()
        self.make_due()
        e = self.engine()
        e.process_automatic_cleanup()
        self.assertEqual(self.radarr.deleted, [])
        self.assertIn("movie_10", self.store.load_auto_state())
        self.assertEqual((e.stats["would_delete"], e.events[0]["type"]), (1, "would_delete"))

    def test_failed_delete_keeps_the_item(self):
        self.engine().process_automatic_cleanup()
        self.make_due()
        self.radarr.delete_ok = False
        e = self.engine()
        e.process_automatic_cleanup()
        self.assertIn("movie_10", self.store.load_auto_state())
        self.assertEqual(e.stats["delete_failed"], 1)

    def test_watched_item_is_dequeued(self):
        self.engine().process_automatic_cleanup()
        self.jf.last_played["jm10"] = utc(1)
        e = self.engine()
        e.process_automatic_cleanup()
        self.assertEqual((self.store.load_auto_state(), e.stats["dequeued"]), ({}, 1))

    def test_radarr_outage_keeps_queue_and_blocks_deletion(self):
        self.engine().process_automatic_cleanup()
        self.make_due()
        self.radarr.fail = True
        out = self.engine().process_automatic_cleanup()
        self.assertIn("movie_10", self.store.load_auto_state())
        self.assertEqual(self.radarr.deleted, [])
        self.assertIn("movie_10", out)            # badge / collection stay stable

    def test_jellyfin_outages_block_deletion(self):
        self.engine().process_automatic_cleanup()
        self.make_due()
        self.jf.index_ok = False
        self.engine().process_automatic_cleanup()
        self.jf.index_ok, self.jf.users_ok = True, False
        self.engine().process_automatic_cleanup()
        self.assertEqual(self.radarr.deleted, [])
        self.assertIn("movie_10", self.store.load_auto_state())

    def test_series_age_comes_from_oldest_episode_file(self):
        self.jf.add_series(500, "js500", "Show")
        self.sonarr.add_series(5, 500, "Show", file_days_ago=90)
        self.assertIn("series_500", self.engine().process_automatic_cleanup())

    def test_unconfigured_sonarr_leaves_series_state_alone(self):
        self.jf.add_series(500, "js500", "Show")
        self.sonarr.add_series(5, 500, "Show", file_days_ago=90)
        self.engine().process_automatic_cleanup()
        self.sonarr.configured = False
        out = self.engine().process_automatic_cleanup()
        self.assertIn("series_500", self.store.load_auto_state())
        self.assertIn("series_500", out)

    def test_preview_changes_nothing(self):
        pv = self.engine().preview()
        self.assertEqual([i["key"] for i in pv["items"]], ["movie_10"])
        self.assertEqual((pv["total_size_bytes"], self.store.load_auto_state()), (5_000_000_000, {}))


class DiskThresholdTests(Base):
    def setUp(self):
        super().setUp()
        self.jf.add_series(500, "js500", "Show")
        self.sonarr.add_series(5, 500, "Show", file_days_ago=90)
        self.s.movies.cleanup_delay_days, self.s.series.cleanup_delay_days = 7, 14
        self.s.disk_path = "/"                           # exists everywhere, so usage is always known
        self.s.disk_thresholds = [DiskThreshold(usage_percent=0.1, movies_days=2, series_days=5)]

    def days(self, key):
        d = datetime.fromisoformat(self.store.load_auto_state()[key]["delete_at"])
        return (d.date() - datetime.now(timezone.utc).date()).days

    def test_movies_and_series_get_their_own_cap(self):
        self.engine().process_automatic_cleanup()
        self.assertEqual((self.days("movie_10"), self.days("series_500")), (2, 5))

    def test_cap_never_lengthens(self):
        self.s.disk_thresholds = [DiskThreshold(usage_percent=0.1, movies_days=30, series_days=30)]
        self.engine().process_automatic_cleanup()
        self.assertEqual((self.days("movie_10"), self.days("series_500")), (7, 14))

    def test_below_threshold_keeps_the_normal_delay(self):
        self.s.disk_thresholds = [DiskThreshold(usage_percent=100, movies_days=1, series_days=1)]
        self.engine().process_automatic_cleanup()
        self.assertEqual((self.days("movie_10"), self.days("series_500")), (7, 14))

    def test_each_type_can_live_on_its_own_disk(self):
        self.s.disk_path_movies = "/definitely/not/mounted"      # unknown usage -> no shortening for movies
        self.engine().process_automatic_cleanup()
        self.assertEqual((self.days("movie_10"), self.days("series_500")), (7, 5))

    def test_existing_queue_is_shortened_only_when_enabled(self):
        self.s.disk_thresholds = []
        self.engine().process_automatic_cleanup()
        self.s.disk_thresholds = [DiskThreshold(usage_percent=0.1, movies_days=1, series_days=1)]
        before = self.store.load_auto_state()["movie_10"]["delete_at"]
        self.engine().process_automatic_cleanup()
        self.assertEqual(self.store.load_auto_state()["movie_10"]["delete_at"], before)     # default: untouched
        self.s.shorten_existing_queue = True
        e = self.engine()
        e.process_automatic_cleanup()
        info = self.store.load_auto_state()["movie_10"]
        self.assertLess(info["delete_at"], before)
        self.assertTrue(info["accelerated"])
        self.assertEqual(e.stats["accelerated"], 2)          # the movie and the series
        self.assertIn("accelerated", [h["event"] for h in self.store.list_history()["items"]])

    def test_a_postponed_item_is_not_pulled_back(self):
        self.s.disk_thresholds = []
        self.engine().process_automatic_cleanup()
        st = self.store.load_auto_state()
        st["movie_10"]["postponed"] = True
        self.store.save_auto_state(st)
        before = st["movie_10"]["delete_at"]
        self.s.disk_thresholds = [DiskThreshold(usage_percent=0.1, movies_days=1, series_days=1)]
        self.s.shorten_existing_queue = True
        self.engine().process_automatic_cleanup()
        self.assertEqual(self.store.load_auto_state()["movie_10"]["delete_at"], before)


class StartGateTests(Base):
    """Automatic cleanup waits until the disk is at least cleanup_start_percent full."""

    def setUp(self):
        super().setUp()
        self.s.disk_path = "/"            # exists everywhere; its real usage is somewhere between 0 and 100

    def test_below_the_threshold_nothing_is_queued(self):
        self.s.cleanup_start_percent = 100
        e = self.engine()
        self.assertEqual(e.process_automatic_cleanup(), {})
        self.assertEqual(self.store.load_auto_state(), {})
        self.assertEqual(e.paused["movie"]["reason"], "below_threshold")

    def test_at_or_above_the_threshold_cleanup_runs(self):
        for start in (0, 0.1):
            self.store.save_auto_state({})
            self.s.cleanup_start_percent = start
            e = self.engine()
            self.assertIn("movie_10", e.process_automatic_cleanup())
            self.assertEqual(e.paused, {})

    def test_a_waiting_queue_is_left_exactly_as_it_is(self):
        self.engine().process_automatic_cleanup()
        self.make_due()
        self.jf.last_played["jm10"] = utc(1)            # would normally dequeue it
        before = self.store.load_auto_state()
        self.s.cleanup_start_percent = 100
        out = self.engine().process_automatic_cleanup()
        self.assertEqual(self.store.load_auto_state(), before)     # not dequeued
        self.assertEqual(self.radarr.deleted, [])                  # and, even overdue, not deleted
        self.assertIn("movie_10", out)                             # badge and collection stay stable

    def test_an_unreadable_disk_pauses_cleanup_instead_of_guessing(self):
        self.s.disk_path = "/definitely/not/mounted"
        self.s.cleanup_start_percent = 40
        e = self.engine()
        self.assertEqual(e.process_automatic_cleanup(), {})
        self.assertEqual(e.paused["movie"]["reason"], "unknown_usage")
        self.s.cleanup_start_percent = 0                 # 0 never needs the disk
        self.assertIn("movie_10", self.engine().process_automatic_cleanup())

    def test_each_type_is_gated_by_its_own_disk(self):
        self.jf.add_series(500, "js500", "Show")
        self.sonarr.add_series(5, 500, "Show", file_days_ago=90)
        self.s.cleanup_start_percent = 0.1
        self.s.disk_path_movies = "/definitely/not/mounted"
        e = self.engine()
        out = e.process_automatic_cleanup()
        self.assertEqual((sorted(e.paused), "series_500" in out, "movie_10" in out), (["movie"], True, False))

    def test_manual_deletions_are_not_gated(self):
        self.s.cleanup_start_percent = 100
        state = {"movie_10": {"title": "X", "media_type": "movie", "tmdb_id": 10, "delete_at": utc(1).isoformat()}}
        self.engine().process_manual_due_items(state)
        self.assertEqual((self.radarr.deleted, state), ([1], {}))

    def test_preview_shows_what_would_qualify_and_why_nothing_happens_yet(self):
        self.s.cleanup_start_percent = 100
        pv = self.engine().preview()
        self.assertEqual([i["key"] for i in pv["items"]], ["movie_10"])
        self.assertEqual(pv["paused"]["movie"]["reason"], "below_threshold")
        self.s.cleanup_start_percent = 0
        self.assertEqual(self.engine().preview()["paused"], {})


class ManualTests(Base):
    star = {"Id": "jm10", "Name": "Old movie", "Type": "Movie", "ProviderIds": {"Tmdb": "10"}}

    def test_star_marks_unstars_and_deletes_after_grace(self):
        self.jf.favorites_by_user["admin"] = [dict(self.star)]
        e, state = self.engine(), {}
        e.process_manual_new_favorites(state)
        self.assertEqual((state["movie_10"]["origin"], self.jf.unfavorited), ("star", ["jm10"]))
        d = datetime.fromisoformat(state["movie_10"]["delete_at"])
        self.assertEqual(d.date(), (datetime.now(timezone.utc) + timedelta(days=14)).date())
        state["movie_10"]["delete_at"] = utc(1).isoformat()
        e.process_manual_due_items(state)
        self.assertEqual((self.radarr.deleted, state), ([1], {}))

    def test_no_manual_user_means_no_stars(self):
        self.s.manual_user_id = ""
        self.jf.favorites_by_user["admin"] = [dict(self.star)]
        state = {}
        self.engine().process_manual_new_favorites(state)
        self.assertEqual(state, {})

    def test_manual_dry_run(self):
        self.s.dry_run = True
        state = {"movie_10": {"title": "X", "media_type": "movie", "tmdb_id": 10, "delete_at": utc(1).isoformat()}}
        self.engine().process_manual_due_items(state)
        self.assertEqual((self.radarr.deleted, "movie_10" in state), ([], True))

    def test_protection_added_after_scheduling_is_honored(self):
        self.radarr._tags = {1: "do-not-delete"}
        self.radarr._items[0]["tags"] = [1]
        state = {"movie_10": {"title": "X", "media_type": "movie", "tmdb_id": 10, "delete_at": utc(1).isoformat()}}
        self.engine().process_manual_due_items(state)
        self.assertEqual((self.radarr.deleted, state), ([], {}))
        self.assertEqual(self.store.list_history()["items"][0]["details"], {"reason": "protected"})


class QueueViewTests(Base):
    def test_merges_both_sources(self):
        self.engine().process_automatic_cleanup()
        self.store.save_manual_state({"series_1": {"title": "S", "media_type": "series", "jellyfin_id": "x", "origin": "dashboard",
                                                    "delete_at": (utc() + timedelta(days=3)).isoformat()}})
        view = build_queue_view(self.store)
        self.assertEqual([(v["source"], v["origin"]) for v in view], [("manual", "dashboard"), ("auto", "auto")])
        self.assertFalse(view[0]["due"])


if __name__ == "__main__":
    unittest.main()
