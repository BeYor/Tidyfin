import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from tidyfin.errors import AuthError, BadRequest, Conflict, Forbidden, NotFound, RateLimited, SetupError
from tidyfin.services import TidyfinApp
from tests.helpers import FakeFactory, make_jpeg


def make_app(configured=True):
    tmp = tempfile.TemporaryDirectory()
    f = FakeFactory()
    f.jf.add_movie(10, "jm10", "Old movie", path="/m/Old [Lektor PL].mkv")
    f.radarr.add_movie(1, 10, "Old movie", file_days_ago=100)
    f.jf.add_series(500, "js500", "Show")
    f.sonarr.add_series(5, 500, "Show", file_days_ago=90)
    app = TidyfinApp(tmp.name, f)
    if configured:
        app.setup_jellyfin("http://jf:8096", "admin", "pass", languages=["pol"])
        app.update_settings({"radarr": {"url": "http://r", "api_key": "k"}, "sonarr": {"url": "http://s", "api_key": "k"},
                             "cleanup_start_percent": 0})   # the start gate depends on the machine's disk, so tests switch it off
        app.scheduler.run_once("test")
    return app, f, tmp


class SetupAndLoginTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app(configured=False)

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_wizard_applies_locale_defaults_languages_and_locks_itself(self):
        res = self.app.setup_jellyfin("jf:8096/", "admin", "pass", locale="pl", languages=["pol", "ger", "nope"])
        s = self.app.settings()
        self.assertEqual((s.setup_complete, s.jellyfin.url, s.jellyfin.api_key, s.manual_user_id),
                         (True, "http://jf:8096", "generated-api-key", "u-admin"))
        self.assertEqual((s.locale, s.badges.countdown.text, s.leaving_movies_collection_name), ("pl", "DOSTĘPNE DO: {date}", "Wkrótce znikają – filmy"))
        self.assertEqual([p.code for p in s.language.profiles], ["pol", "ger"])     # unknown preset ignored
        self.assertTrue(s.dry_run)                                                   # safe start
        self.assertEqual(self.app.session_user(res["token"])["name"], "admin")
        with self.assertRaises(SetupError) as ctx:
            self.app.setup_jellyfin("jf:8096", "admin", "pass")
        self.assertEqual(ctx.exception.code, "setup_done")

    def test_wizard_rejects_non_admins_and_bad_passwords(self):
        with self.assertRaises(Forbidden) as ctx:
            self.app.setup_jellyfin("jf", "ola", "pass")
        self.assertEqual(ctx.exception.code, "not_admin")
        with self.assertRaises(AuthError):
            self.app.setup_jellyfin("jf", "admin", "wrong")
        self.assertFalse(self.app.settings().setup_complete)

    def test_login_admin_only_with_rate_limit_and_logout(self):
        self.app.setup_jellyfin("jf", "admin", "pass")
        token = self.app.login("admin", "pass", "1.2.3.4")["token"]
        self.assertTrue(self.app.session_user(token))
        with self.assertRaises(Forbidden):
            self.app.login("ola", "pass", "1.2.3.4")
        self.app.limiter.max_attempts = 3
        for _ in range(3):
            with self.assertRaises(AuthError):
                self.app.login("admin", "bad", "9.9.9.9")
        with self.assertRaises(RateLimited):
            self.app.login("admin", "pass", "9.9.9.9")
        self.app.logout(token)
        self.assertIsNone(self.app.session_user(token))

    def test_login_before_setup_is_refused(self):
        with self.assertRaises(SetupError):
            self.app.login("admin", "pass")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_patch_keeps_secrets_blocks_setup_flag_and_never_returns_secrets(self):
        self.app.update_settings({"radarr": {"url": "radarr:7878/", "api_key": ""}, "setup_complete": False, "dry_run": False,
                                  "movies": {"cleanup_delay_days": "7"}})
        s = self.app.settings()
        self.assertEqual((s.radarr.url, s.radarr.api_key, s.setup_complete, s.dry_run, s.movies.cleanup_delay_days),
                         ("http://radarr:7878", "k", True, False, 7))
        pub = self.app.public_settings()
        self.assertTrue(pub["radarr"]["api_key_set"])
        self.assertNotIn("api_key", pub["radarr"])

    def test_export_has_no_secrets_and_import_round_trips(self):
        self.app.update_settings({"notifications": {"webhook_url": "http://secret/hook", "enabled": True}, "grace_days": 21})
        exported = self.app.export_settings()
        self.assertNotIn("secret", str(exported))
        self.assertNotIn("jellyfin", exported["settings"])
        self.app.update_settings({"grace_days": 3})
        self.app.import_settings(exported)
        s = self.app.settings()
        self.assertEqual((s.grace_days, s.notifications.webhook_url), (21, "http://secret/hook"))   # secrets stay local
        with self.assertRaises(BadRequest):
            self.app.import_settings({"nope": 1})

    def test_connection_tests_and_user_list(self):
        self.assertTrue(self.app.test_connection("jellyfin")["ok"])
        self.assertTrue(self.app.test_connection("radarr", "http://x", "k")["ok"])
        self.f.radarr.fail = True
        bad = self.app.test_connection("radarr")
        self.assertEqual((bad["ok"], bad["code"]), (False, "arr_unreachable"))
        self.assertEqual(self.app.test_connection("radarr", "", "")["ok"], False if not self.app.settings().radarr.api_key else bad["ok"])
        with self.assertRaises(NotFound):
            self.app.test_connection("plex")
        self.assertEqual([u["name"] for u in self.app.jellyfin_users()], ["admin", "ola"])

    def test_options_contain_presets(self):
        o = self.app.options()
        self.assertIn("glass", o["badge_presets"])
        self.assertEqual(len(o["language_presets"]), 9)
        self.assertEqual(o["suggested_for_locale"]["pl"], "pol")


class ItemActionTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_library_snapshot_filters_and_search(self):
        self.assertEqual(self.app.library()["total"], 2)
        self.assertEqual([i["key"] for i in self.app.library(media_type="series")["items"]], ["series_500"])
        self.assertEqual(self.app.library(q="OLD")["total"], 1)
        self.assertEqual(self.app.library(status="queued")["total"], 2)
        self.assertEqual(self.app.library(status="free")["total"], 0)
        self.assertEqual(self.app.library(sort="size", descending=True)["items"][0]["key"], "series_500")

    def test_protect_adds_the_tag_in_the_arr_and_clears_the_queue(self):
        res = self.app.protect("movie_10")
        self.assertEqual(res["title"], "Old movie")
        self.assertEqual(self.f.radarr.tag_calls[-1][2], "add")
        self.assertEqual(self.f.radarr._tags, {1: "do-not-delete"})
        self.assertNotIn("movie_10", self.app.store.load_auto_state())
        row = self.app.store.get_library_item("movie_10")
        self.assertTrue(row["protected"] and "do-not-delete" in row["tags"])
        self.assertEqual([p["key"] for p in self.app.protected()], ["movie_10"])
        # the next cycle must not queue it again - the tag is what protects it
        self.app.scheduler.run_once("t")
        self.assertNotIn("movie_10", self.app.store.load_auto_state())
        self.assertTrue(self.app.library(status="protected")["total"] == 1)

    def test_unprotect_removes_every_protection_tag(self):
        self.app.update_settings({"exclude_tags": ["do-not-delete", "extra"]})
        self.app.protect("movie_10")
        self.f.radarr._tags[2] = "extra"
        self.f.radarr._items[0]["tags"] = [1, 2]
        self.app.store.update_library_item("movie_10", tags=["do-not-delete", "extra"])
        self.app.unprotect("movie_10")
        self.assertEqual(self.f.radarr._items[0]["tags"], [])
        self.assertFalse(self.app.store.get_library_item("movie_10")["protected"])

    def test_protect_failures_are_reported_and_change_nothing(self):
        self.f.radarr.fail = True
        with self.assertRaises(Exception) as ctx:
            self.app.protect("movie_10")
        self.assertEqual(ctx.exception.code, "arr_unreachable")
        self.assertFalse(self.app.store.get_library_item("movie_10")["protected"])
        self.assertIn("movie_10", self.app.store.load_auto_state())

    def test_schedule_from_the_dashboard(self):
        self.app.store.save_auto_state({})                       # not in the auto queue
        res = self.app.schedule("movie_10", days=5)
        entry = self.app.store.load_manual_state()["movie_10"]
        self.assertEqual((entry["origin"], entry["arr_id"], entry["delete_at"]), ("dashboard", 1, res["delete_at"]))
        self.assertEqual(datetime.fromisoformat(res["delete_at"]).date(), (datetime.now(timezone.utc) + timedelta(days=5)).date())
        self.assertNotIn("movie_10", self.app.store.load_auto_state())    # the manual entry takes over
        date = (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y-%m-%d")
        self.assertTrue(self.app.schedule("movie_10", date=date)["delete_at"].startswith(date))
        for bad in ({"date": "2001-01-01"}, {"date": "tomorrow"}, {"days": -1}, {"days": "x"}, {}):
            with self.assertRaises(BadRequest):
                self.app.schedule("movie_10", **bad)
        self.assertEqual(self.app.unschedule("movie_10")["key"], "movie_10")
        with self.assertRaises(NotFound):
            self.app.unschedule("movie_10")

    def test_scheduling_a_protected_item_is_refused(self):
        self.app.protect("movie_10")
        with self.assertRaises(Conflict) as ctx:
            self.app.schedule("movie_10", days=3)
        self.assertEqual(ctx.exception.code, "item_protected")

    def test_auto_items_cannot_be_cancelled_only_kept_or_postponed(self):
        with self.assertRaises(Conflict) as ctx:
            self.app.unschedule("movie_10")
        self.assertEqual(ctx.exception.code, "auto_item")

    def test_postpone_by_days_and_by_date_marks_the_entry(self):
        before = self.app.store.load_auto_state()["movie_10"]["delete_at"]
        new = self.app.postpone("movie_10", days=7)["delete_at"]
        self.assertEqual(datetime.fromisoformat(new) - datetime.fromisoformat(before), timedelta(days=7))
        self.assertTrue(self.app.store.load_auto_state()["movie_10"]["postponed"])
        date = (datetime.now(timezone.utc) + timedelta(days=40)).strftime("%Y-%m-%d")
        self.assertTrue(self.app.postpone("movie_10", date=date)["delete_at"].startswith(date))
        for bad in ({"days": 0}, {"days": 99999}, {}):
            with self.assertRaises(BadRequest):
                self.app.postpone("movie_10", **bad)
        with self.assertRaises(NotFound):
            self.app.postpone("movie_404", days=1)

    def test_invalid_keys_never_reach_the_filesystem(self):
        for bad in ("../etc/passwd", "movie_5/../x", "film_1", "movie_", "movie_1.jpg"):
            with self.assertRaises(BadRequest):
                self.app.poster(bad)
            with self.assertRaises(BadRequest):
                self.app.protect(bad)

    def test_bulk_reports_each_item(self):
        out = self.app.bulk("postpone", ["movie_10", "series_500", "movie_404"], {"days": 3})
        self.assertEqual((out["ok"], out["failed"]), (2, 1))
        self.assertEqual(out["results"]["movie_404"]["code"], "not_in_queue")
        with self.assertRaises(BadRequest):
            self.app.bulk("explode", ["movie_10"])
        with self.assertRaises(BadRequest):
            self.app.bulk("protect", [])

    def test_item_detail_includes_last_played_and_queue(self):
        self.f.jf.last_played["jm10"] = datetime.now(timezone.utc) - timedelta(days=60)
        d = self.app.item_detail("movie_10")
        self.assertEqual((d["last_played_checked"], d["queue"]["source"]), (True, "auto"))
        self.assertTrue(d["last_played"])
        with self.assertRaises(NotFound):
            self.app.item_detail("movie_404")

    def test_status_reports_whether_cleanup_is_active_or_waiting(self):
        self.assertEqual(self.app.status()["cleanup"]["types"]["movie"]["active"], True)      # make_app sets the start gate to 0
        self.app.update_settings({"cleanup_start_percent": 100, "disk_path": "/"})
        c = self.app.status()["cleanup"]
        self.assertEqual((c["start_percent"], c["types"]["movie"]["active"], c["types"]["movie"]["reason"]), (100.0, False, "below_threshold"))
        self.app.update_settings({"disk_path": "/definitely/not/mounted"})
        self.assertEqual(self.app.status()["cleanup"]["types"]["series"]["reason"], "unknown_usage")

    def test_status_summarises_everything(self):
        st = self.app.status()
        self.assertEqual((st["queue"]["total"], st["library"]["total"], st["connections"]["radarr"]), (2, 2, True))
        self.assertEqual(st["language"]["labeled"], 1)


class PosterAndPreviewTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def test_poster_prefers_the_backup_of_the_original_and_resizes(self):
        backup = Path(self.tmp.name) / "poster_backups" / "movie_10.jpg"
        backup.parent.mkdir(exist_ok=True)
        backup.write_bytes(make_jpeg(900, 1350))
        self.assertEqual(Image.open(BytesIO(self.app.poster("movie_10", 300))).size, (300, 450))

    def test_poster_falls_back_to_jellyfin(self):
        (Path(self.tmp.name) / "poster_backups" / "movie_10.jpg").unlink(missing_ok=True)
        self.f.jf.posters["jm10"] = make_jpeg(400, 600)
        self.assertEqual(Image.open(BytesIO(self.app.poster("movie_10", 200))).size, (200, 300))
        with self.assertRaises(NotFound):
            self.app.poster("movie_999")

    def test_badge_preview_renders_the_given_draft(self):
        base = self.app.badge_preview([])
        one = self.app.badge_preview([{"text": "UNTIL {date}", "corner": "top-right", "style": {"preset": "solid", "bg": "#FF4967"}}])
        off = self.app.badge_preview([{"text": "X", "enabled": False}])
        self.assertEqual((one != base, off), (True, base))
        self.assertEqual(Image.open(BytesIO(one)).size, (360, 540))
        with self.assertRaises(BadRequest):
            self.app.badge_preview("nope")

    def test_restore_all_posters(self):
        original = (Path(self.tmp.name) / "poster_backups" / "movie_10.jpg").read_bytes()   # the cycle in make_app made it
        self.assertTrue(self.f.jf.posters["jm10"] != original)              # badges are on the poster
        self.assertGreaterEqual(self.app.restore_all_posters()["restored"], 1)
        self.assertTrue(self.f.jf.posters["jm10"] == original)              # (not assertEqual: it would dump the bytes)


if __name__ == "__main__":
    unittest.main()
