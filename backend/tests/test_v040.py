"""v0.4: keep until a date, connection alerts, trying thresholds before saving."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from tidyfin.routes import Request, Router
from tests.test_services import make_app


class KeepUntilTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()
        self.app.update_settings({"dry_run": False})

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_keep_for_a_while_records_the_date_and_shows_it(self):
        r = self.app.protect("movie_10", days=5)
        self.assertIsNotNone(r["until"])
        self.assertEqual(self.app.item_detail("movie_10")["keep_until"], r["until"])
        self.assertEqual([h["details"]["until"] for h in self.app.store.list_history()["items"] if h["event"] == "protected"], [r["until"]])

    def test_keep_without_a_date_is_forever_and_clears_an_earlier_date(self):
        self.app.protect("movie_10", days=5)
        self.app.protect("movie_10")
        self.assertIsNone(self.app.item_detail("movie_10")["keep_until"])

    def test_route_accepts_an_empty_body_and_a_days_body(self):
        router = Router(self.app)
        cookies = {"tidyfin_session": self.app.login("admin", "pass")["token"]}
        r = router.dispatch(Request("POST", "/api/items/movie_10/protect", {}, b"", cookies))
        self.assertEqual((r.status, r.body["until"]), (200, None))
        r = router.dispatch(Request("POST", "/api/items/series_500/protect", {}, json.dumps({"days": 3}).encode(), cookies))
        self.assertEqual((r.status, bool(r.body["until"])), (200, True))

    def test_the_protection_tag_comes_off_when_the_date_passes(self):
        self.app.protect("movie_10", days=2)
        self.assertTrue(self.app.store.get_library_item("movie_10")["protected"])
        self.app.scheduler.run_once("test")
        self.assertTrue(self.app.store.get_library_item("movie_10")["protected"])       # not yet
        keeps = self.app.store.get_json("keep_until")
        self.app.store.set_json("keep_until", {k: (datetime.now(timezone.utc) - timedelta(days=1)).isoformat() for k in keeps})
        self.app.scheduler.run_once("test")
        self.assertFalse(self.app.store.get_library_item("movie_10")["protected"])
        self.assertEqual(self.app.store.get_json("keep_until"), {})
        ev = [h for h in self.app.store.list_history()["items"] if h["event"] == "unprotected"][0]
        self.assertEqual((ev["source"], ev["key"]), ("expired", "movie_10"))
        self.assertIn(("remove",), [(c[2],) for c in self.f.radarr.tag_calls])

    def test_manual_unprotect_forgets_the_date(self):
        self.app.protect("movie_10", days=2)
        self.app.unprotect("movie_10")
        self.assertEqual(self.app.store.get_json("keep_until"), {})

    def test_an_entry_for_a_title_that_lost_its_protection_elsewhere_is_dropped(self):
        self.app.protect("movie_10", days=2)
        self.f.radarr._items[0]["tags"] = []
        self.app.scheduler.run_once("test")
        self.assertEqual(self.app.store.get_json("keep_until"), {})


class ConnectionAlertTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()
        self.sent = []
        import tidyfin.cycle as cycle
        self._orig = cycle.notify_connection
        cycle.notify_connection = lambda s, service, n, lost, session=None: self.sent.append((service, n, lost))

    def tearDown(self):
        import tidyfin.cycle as cycle
        cycle.notify_connection = self._orig
        self.app.store.close(); self.tmp.cleanup()

    def test_three_failed_cycles_raise_one_alert_and_recovery_clears_it(self):
        self.f.radarr.fail = True
        for _ in range(2):
            self.app.scheduler.run_once("test")
        self.assertEqual((self.app.status()["alerts"], self.sent), ([], []))
        self.app.scheduler.run_once("test")
        self.assertEqual(self.app.status()["alerts"], [{"service": "radarr", "cycles": 3}])
        self.app.scheduler.run_once("test")
        self.assertEqual(self.sent, [("radarr", 3, True)])                       # said once, not every cycle
        self.f.radarr.fail = False
        self.app.scheduler.run_once("test")
        self.assertEqual((self.app.status()["alerts"], self.sent[-1]), ([], ("radarr", 0, False)))

    def test_a_single_hiccup_is_not_an_alert(self):
        self.f.jf.users_ok = False
        self.app.scheduler.run_once("test")
        self.f.jf.users_ok = True
        self.app.scheduler.run_once("test")
        self.app.scheduler.run_once("test")
        self.assertEqual((self.app.status()["alerts"], self.sent), ([], []))


class TryThresholdsTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_preview_with_unsaved_values_changes_the_answer_but_not_the_settings(self):
        saved = self.app.preview()
        self.assertTrue(saved["items"])
        strict = self.app.preview({"movies": {"content_age_threshold_days": 5000}, "series": {"content_age_threshold_days": 5000}})
        self.assertEqual(strict["items"], [])
        self.assertEqual(self.app.settings().movies.content_age_threshold_days, 45)
        self.assertEqual(self.app.preview()["items"], saved["items"])


class DemoArtworkTests(unittest.TestCase):
    def test_posters_are_deterministic_valid_and_differ_by_title(self):
        from io import BytesIO
        from PIL import Image
        from tidyfin.devtools.artwork import poster
        a, b = poster("Neon Orchard"), poster("Paper Moons")
        self.assertEqual(a, poster("Neon Orchard"))
        self.assertNotEqual(a, b)
        im = Image.open(BytesIO(a))
        self.assertEqual((im.size, im.format), ((300, 450), "JPEG"))
        self.assertEqual(Image.open(BytesIO(poster("X", (120, 180)))).size, (120, 180))


class NotifyTextTests(unittest.TestCase):
    def test_connection_messages_exist_in_both_languages(self):
        from tidyfin.notify import _TEXT
        for loc in ("en", "pl"):
            self.assertIn("{service}", _TEXT[loc]["conn_lost"])
            self.assertTrue(_TEXT[loc]["conn_back"])


if __name__ == "__main__":
    unittest.main()
