import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from tidyfin.cycle import Context, run_cycle
from tidyfin.posters import PosterService
from tidyfin.profiles import preset_profile
from tidyfin.settings import LanguageSettings, Settings
from tidyfin.store import Store
from tests.helpers import FakeArr, FakeJellyfin


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(":memory:")
        self.s = Settings(dry_run=False, setup_complete=True, cleanup_start_percent=0, leaving_movies_collection_id="colM", leaving_tv_collection_id="colT",
                          language=LanguageSettings(deep_scan=False, profiles=[preset_profile("pol")]))
        self.jf, self.radarr, self.sonarr = FakeJellyfin(), FakeArr("radarr"), FakeArr("sonarr")
        self.posters = PosterService(self.jf, Path(self.tmp.name) / "backups")
        self.jf.add_movie(10, "jm10", "Old", path="/m/Old [Lektor PL].mkv")
        self.radarr.add_movie(1, 10, "Old", file_days_ago=100)
        self.jf.add_movie(11, "jm11", "New", path="/m/New.mkv")
        self.radarr.add_movie(2, 11, "New", file_days_ago=3)

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self):
        return Context(self.s, self.store, self.jf, self.radarr, self.sonarr, self.posters)

    def test_full_cycle_badges_collection_snapshot_and_report(self):
        original = self.jf.posters["jm10"]
        summary = run_cycle(self.ctx())
        self.assertEqual((summary["auto_queue"], summary["language_labeled"], summary["library"]["movie"]), (1, 1, 2))
        self.assertEqual(len([u for u in self.jf.uploads if u[0] == "jm10"]), 1)    # one upload carrying every badge
        self.assertNotEqual(self.jf.posters["jm10"], original)
        self.assertTrue(self.posters.backup_path_for("movie_10").exists())
        self.assertEqual(self.jf.collections["colM"], {"jm10"})
        self.assertEqual(self.store.get_json("language_report")["by_profile"], {"pol": {"voiceover": 1}})
        self.assertEqual(self.store.library_count(), 2)

    def test_dequeued_item_keeps_only_its_language_badge(self):
        run_cycle(self.ctx())
        self.jf.last_played["jm10"] = datetime.now(timezone.utc)
        run_cycle(self.ctx())
        self.assertEqual(self.jf.collections["colM"], set())
        self.assertTrue(self.posters.backup_path_for("movie_10").exists())   # language badge still active

    def test_poster_restored_after_dequeue_even_without_a_language_label(self):
        """Regression: the original script lost the Jellyfin id of such items,
        deleted the backup and left 'AVAILABLE UNTIL' on the poster forever."""
        self.jf.movies["10"]["Path"] = "/m/Old.mkv"
        original = self.jf.posters["jm10"]
        run_cycle(self.ctx())
        self.assertNotEqual(self.jf.posters["jm10"], original)
        self.jf.last_played["jm10"] = datetime.now(timezone.utc)
        run_cycle(self.ctx())
        self.assertEqual(self.jf.posters["jm10"], original)
        self.assertFalse(self.posters.backup_path_for("movie_10").exists())

    def test_disabling_badges_restores_every_poster(self):
        original = self.jf.posters["jm10"]
        run_cycle(self.ctx())
        self.s.badges.enabled = False
        run_cycle(self.ctx())
        self.assertEqual(self.jf.posters["jm10"], original)

    def test_badge_text_corner_and_disabled_badges_follow_the_settings(self):
        self.s.badges.countdown.enabled = False
        self.s.language.profiles[0].badges["voiceover"].text = "VOICEOVER"
        captured = []
        real = self.posters.render_item_badges
        self.posters.render_item_badges = lambda key, jid, badges: captured.append((key, list(badges))) or real(key, jid, badges)
        run_cycle(self.ctx())
        texts = [b.text for _, badges in captured for b in badges]
        self.assertEqual(texts, ["VOICEOVER"])               # countdown disabled, language text customised

    def _countdown_texts(self):
        captured = []
        self.posters.render_item_badges = lambda key, jid, badges: captured.append((key, [b.text for b in badges])) or True
        run_cycle(self.ctx())
        return dict(captured)

    def test_countdown_appears_only_within_the_configured_window(self):
        self.s.badges.countdown.text = "LEFT {days}"
        self.s.language.profiles = []
        self.s.movies.cleanup_delay_days = 30
        self.assertEqual(self._countdown_texts(), {})                          # 30 days away: no badge yet (window is 14)
        self.s.badges.countdown_show_within_days = 30
        self.store.save_auto_state({})
        self.assertRegex(self._countdown_texts()["movie_10"][0], r"^LEFT (29|30)$")   # inside a 30 day window

    def test_a_window_of_zero_means_always(self):
        self.s.badges.countdown_show_within_days = 0
        self.s.movies.cleanup_delay_days = 90
        self.s.language.profiles = []
        self.assertIn("movie_10", self._countdown_texts())

    def test_language_badges_ignore_the_countdown_window(self):
        self.s.movies.cleanup_delay_days = 90          # far outside the window
        texts = self._countdown_texts()
        self.assertEqual(texts["movie_10"], ["LEKTOR PL"])                       # only the language badge

    def test_countdown_text_template_is_rendered(self):
        self.s.badges.countdown.text = "LEFT {days}d until {date}"
        self.s.badges.date_format = "%Y-%m-%d"
        captured = []
        self.posters.render_item_badges = lambda key, jid, badges: captured.append(list(badges)) or True
        run_cycle(self.ctx())
        text = next(b.text for badges in captured for b in badges if b.text.startswith("LEFT"))
        self.assertRegex(text, r"^LEFT 1[34]d until \d{4}-\d{2}-\d{2}$")

    def test_manual_queue_overrides_the_auto_countdown(self):
        self.s.manual_user_id = "admin"
        self.jf.favorites_by_user["admin"] = [{"Id": "jm10", "Name": "Old", "Type": "Movie", "ProviderIds": {"Tmdb": "10"}}]
        summary = run_cycle(self.ctx())
        self.assertEqual(summary["manual_queue"], 1)
        self.assertEqual(self.jf.collections["colM"], {"jm10"})

    def test_collections_are_created_by_name_when_no_id_is_set(self):
        self.s.leaving_movies_collection_id = self.s.leaving_tv_collection_id = ""
        run_cycle(self.ctx())
        self.assertEqual(self.jf.collections[f"col-{self.s.leaving_movies_collection_name}"], {"jm10"})

    def test_library_snapshot_survives_a_radarr_outage(self):
        run_cycle(self.ctx())
        self.radarr.fail = True
        summary = run_cycle(self.ctx())
        self.assertIsNone(summary["library"]["movie"])
        self.assertEqual(self.store.library_count(), 2)       # old rows kept

    def test_notifications_get_the_cycle_events(self):
        with mock.patch("tidyfin.cycle.notify_cycle") as notify:
            run_cycle(self.ctx())
        events = notify.call_args.args[1]
        self.assertEqual([(e["type"], e["title"]) for e in events], [("queued", "Old")])

    def test_a_notification_failure_never_fails_the_cycle(self):
        with mock.patch("tidyfin.cycle.notify_cycle", side_effect=RuntimeError("boom")):
            self.assertEqual(run_cycle(self.ctx())["auto_queue"], 1)


if __name__ == "__main__":
    unittest.main()
