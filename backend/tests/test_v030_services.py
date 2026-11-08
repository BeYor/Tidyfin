import json
import unittest
from io import BytesIO

from PIL import Image

from tidyfin.errors import Conflict, Forbidden, NotFound, UpstreamError
from tidyfin.routes import Request, Router
from tests.test_services import make_app


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_summary_counts_reclaimed_space_and_saved_titles(self):
        st = self.app.store
        st.add_history("deleted", "movie_1", "A", "movie", "auto", {"size_bytes": 4_000})
        st.add_history("deleted", "series_2", "B", "series", "manual", {"size_bytes": 1_000})
        st.add_history("dequeued", "movie_3", "C", "movie", "auto", {"reason": "watched", "size_bytes": 700})
        st.add_history("dequeued", "movie_4", "D", "movie", "auto", {"reason": "favorited", "size_bytes": 300})
        st.add_history("dequeued", "movie_5", "E", "movie", "auto", {"reason": "no_longer_qualifies"})   # not a "save"
        st.add_history("protected", "movie_6", "F", "movie", "dashboard")
        s = self.app.summary(30)
        self.assertEqual(s["deleted"], {"count": 2, "bytes": 5_000, "movies": 1, "series": 1})
        self.assertEqual((s["saved"]["count"], s["saved"]["bytes"]), (3, 1_000))
        self.assertEqual(s["saved"]["by_reason"], {"watched": 1, "favorited": 1, "protected": 1})

    def test_old_events_fall_outside_the_period(self):
        self.app.store.add_history("deleted", "movie_1", "A", "movie", "auto", {"size_bytes": 9})
        with self.app.store._tx() as db:
            db.execute("UPDATE history SET ts='2020-01-01T00:00:00+00:00'")
        self.assertEqual(self.app.summary(30)["deleted"]["count"], 0)


class LanguageTagTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_tags_are_written_replacing_the_profiles_earlier_ones(self):
        self.f.jf.items_by_id["jm10"]["Tags"] = ["Lektor-PL", "other"]
        r = self.app.set_language_tags("movie_10", "pol", ["dub", "subs"])
        self.assertEqual(r["tags"], ["dubbing-pl", "napisy-pl"])
        self.assertEqual(self.f.jf.tag_updates[-1][2].count("lektor-pl"), 1)
        self.assertEqual(self.f.jf.items_by_id["jm10"]["Tags"], ["other", "dubbing-pl", "napisy-pl"])

    def test_dismiss_writes_only_the_dismiss_tag(self):
        self.assertEqual(self.app.set_language_tags("movie_10", "pol", ["dub"], dismiss=True)["tags"], ["brak-pl"])

    def test_errors(self):
        with self.assertRaises(NotFound):
            self.app.set_language_tags("movie_10", "xx", [])
        self.f.jf.items_by_id.pop("jm10")
        with self.assertRaises(UpstreamError):
            self.app.set_language_tags("movie_10", "pol", ["dub"])


class ItemLanguageInfoTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_item_dialog_shows_what_the_last_cycle_detected(self):
        self.f.jf.items_by_id["jm10"].update({"Tags": ["napisy-pl"], "Path": "/m/old.mkv"})
        self.app.scheduler.run_once("test")
        d = self.app.item_detail("movie_10")
        pol = [p for p in d["language"]["profiles"] if p["code"] == "pol"][0]
        self.assertEqual((pol["subs"], pol["sources"], pol["badge"]), (True, ["tags"], "subs"))

    def test_dialog_and_badge_come_from_the_same_detection(self):
        self.f.jf.items_by_id["jm10"].update({"Tags": ["lektor-pl", "napisy-pl"], "Path": "/m/old.mkv"})
        self.app.scheduler.run_once("test")
        pol = [p for p in self.app.item_detail("movie_10")["language"]["profiles"] if p["code"] == "pol"][0]
        self.assertEqual((pol["audio"], pol["subs"], pol["badge"], pol["text"]), (["voiceover"], True, "audio", "POLSKI"))

    def test_a_title_with_nothing_detected_has_an_empty_list_not_a_second_guess(self):
        self.f.jf.items_by_id["jm10"].update({"Tags": [], "Path": "/m/plain.mkv", "MediaStreams": []})
        self.app.scheduler.run_once("test")
        self.assertEqual(self.app.item_detail("movie_10")["language"], {"profiles": []})

    def test_a_title_without_a_jellyfin_id_has_no_language_info(self):
        self.app.scheduler.run_once("test")
        self.app.store.update_library_item("movie_10", jellyfin_id=None)
        self.assertIsNone(self.app.item_detail("movie_10")["language"])


class NoPosterTests(unittest.TestCase):
    def test_a_title_without_a_poster_is_reported_once_not_every_cycle(self):
        import tempfile
        from pathlib import Path
        from tidyfin.logbus import log
        from tidyfin.posters import PosterService
        from tests.helpers import FakeJellyfin
        jf, tmp = FakeJellyfin(), tempfile.TemporaryDirectory()
        jf.add_movie(1, "a", "No poster"); jf.posters.pop("a")
        svc = PosterService(jf, Path(tmp.name))
        with self.assertLogs("tidyfin", level="INFO") as logs:
            for _ in range(3):
                self.assertFalse(svc.render_item_badges("movie_1", "a", []))
            log.info("sentinel")
        self.assertEqual(sum("has no poster" in m for m in logs.output), 1)
        tmp.cleanup()


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_preview_uses_the_titles_poster_when_asked_and_falls_back_to_the_sample(self):
        badge = [{"enabled": True, "text": "X", "corner": "top-left", "style": {}}]
        with_key = self.app.badge_preview(badge, 300, "movie_10")
        sample = self.app.badge_preview(badge, 300, "")
        self.assertNotEqual(with_key, sample)
        self.assertEqual(self.app.badge_preview(badge, 300, "movie_99999"), sample)       # unknown title -> sample
        self.assertEqual(self.app.badge_preview(badge, 300, "../x"), sample)              # invalid key -> sample
        Image.open(BytesIO(with_key)).verify()


if __name__ == "__main__":
    unittest.main()
