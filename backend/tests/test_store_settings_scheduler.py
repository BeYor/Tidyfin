import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tidyfin.scheduler import Scheduler
from tidyfin.settings import Settings, apply_patch, load_settings, save_settings
from tidyfin.store import Store, normalize_title


class SettingsTests(unittest.TestCase):
    def test_validation_clamps_and_coerces(self):
        s = Settings.from_dict({"run_interval_seconds": 5, "grace_days": "abc", "dry_run": "no", "exclude_tags": "a, b ,",
                                "movies": {"cleanup_delay_days": -4}, "locale": "xx",
                                "disk_thresholds": [{"usage_percent": 90, "movies_days": 1, "series_days": 2},
                                                    {"usage_percent": 70, "movies_days": 5, "series_days": 6}, "junk"]})
        self.assertEqual((s.run_interval_seconds, s.grace_days, s.dry_run, s.locale), (60, 14, False, "en"))
        self.assertEqual(s.exclude_tags, ["a", "b", "do-not-delete"])          # the protect tag is always included
        self.assertEqual(s.movies.cleanup_delay_days, 0)
        self.assertEqual([(t.usage_percent, t.movies_days, t.series_days) for t in s.disk_thresholds], [(70, 5, 6), (90, 1, 2)])

    def test_protected_tags_are_lowercase_and_include_the_protect_tag(self):
        s = Settings.from_dict({"exclude_tags": ["Keep"], "protect_tag": "Hold"})
        self.assertEqual(s.protected_tags, {"keep", "hold"})

    def test_new_defaults_and_validation(self):
        s = Settings()
        self.assertEqual((s.cleanup_start_percent, s.badges.countdown_show_within_days), (40.0, 14))
        s = Settings.from_dict({"cleanup_start_percent": 250, "badges": {"countdown_show_within_days": -5}})
        self.assertEqual((s.cleanup_start_percent, s.badges.countdown_show_within_days), (100.0, 0))
        again = Settings.from_dict(s.to_dict())
        self.assertEqual((again.cleanup_start_percent, again.badges.countdown_show_within_days), (100.0, 0))

    def test_locale_defaults(self):
        pl = Settings.from_dict({"locale": "pl"})
        self.assertEqual((pl.badges.countdown.text, pl.leaving_tv_collection_name), ("DOSTĘPNE DO: {date}", "Wkrótce znikają – seriale"))
        self.assertEqual(Settings().badges.countdown.text, "AVAILABLE UNTIL {date}")

    def test_secrets_roundtrip_but_never_reach_the_public_dict(self):
        store = Store(":memory:")
        s = apply_patch(Settings(), {"jellyfin": {"url": "jf", "api_key": "SECRET"}, "notifications": {"webhook_url": "http://h/SECRET2"}})
        save_settings(store, s)
        loaded = load_settings(store)
        self.assertEqual((loaded.jellyfin.api_key, loaded.notifications.webhook_url), ("SECRET", "http://h/SECRET2"))
        dumped = json.dumps(loaded.to_public_dict())
        self.assertNotIn("SECRET", dumped)
        self.assertTrue(loaded.to_public_dict()["notifications"]["webhook_url_set"])

    def test_empty_secret_keeps_and_clear_secrets_wipes_only_notification_secrets(self):
        s = apply_patch(Settings(), {"jellyfin": {"api_key": "K"}, "notifications": {"webhook_url": "http://x", "ntfy_token": "t"}})
        s = apply_patch(s, {"jellyfin": {"api_key": ""}, "notifications": {"webhook_url": ""}})
        self.assertEqual((s.jellyfin.api_key, s.notifications.webhook_url), ("K", "http://x"))
        s = apply_patch(s, {"_clear_secrets": ["notifications.webhook_url", "jellyfin.api_key"]})
        self.assertEqual((s.jellyfin.api_key, s.notifications.webhook_url, s.notifications.ntfy_token), ("K", "", "t"))

    def test_setup_flag_is_not_patchable(self):
        self.assertFalse(apply_patch(Settings(), {"setup_complete": True}).setup_complete)

    def test_profiles_dedupe_and_survive_a_roundtrip(self):
        s = Settings.from_dict({"language": {"profiles": [{"code": "pol", "name": "Polish"}, {"code": "POL"}, {"code": "ger"}]}})
        self.assertEqual([p.code for p in s.language.profiles], ["pol", "ger"])
        again = Settings.from_dict(s.to_dict())
        self.assertEqual(again.language.profiles[0].badges["dub"].to_dict(), s.language.profiles[0].badges["dub"].to_dict())


class StoreTests(unittest.TestCase):
    def test_state_replace_semantics(self):
        st = Store(":memory:")
        st.save_auto_state({"a": {"x": 1}, "b": {"x": 2}})
        st.save_auto_state({"b": {"x": 3}})
        self.assertEqual(st.load_auto_state(), {"b": {"x": 3}})

    def test_title_normalisation_folds_polish_and_accents(self):
        self.assertEqual(normalize_title("Żółć Ąę Café"), "zolc ae cafe")

    def test_library_snapshot_is_replaced_per_type(self):
        st = Store(":memory:")
        st.replace_library([{"key": "movie_1", "media_type": "movie", "title": "A"}, {"key": "series_2", "media_type": "series", "title": "B"}])
        st.replace_library([{"key": "movie_9", "media_type": "movie", "title": "C"}], media_types={"movie"})
        self.assertEqual(sorted(i["key"] for i in st.query_library()["items"]), ["movie_9", "series_2"])
        st.update_library_item("movie_9", protected=True, tags=["x"])
        self.assertEqual((st.get_library_item("movie_9")["protected"], st.get_library_item("movie_9")["tags"]), (True, ["x"]))
        with self.assertRaises(ValueError):
            st.update_library_item("movie_9", title="hack")

    def test_library_queue_join_and_pagination(self):
        st = Store(":memory:")
        st.replace_library([{"key": f"movie_{i}", "media_type": "movie", "title": f"T{i:02d}", "size_bytes": i} for i in range(25)])
        st.save_manual_state({"movie_3": {"delete_at": "2030-01-01"}})
        page = st.query_library(limit=10, offset=20)
        self.assertEqual((page["total"], len(page["items"])), (25, 5))
        q = st.query_library(status="queued")["items"]
        self.assertEqual((len(q), q[0]["queue"]["source"]), (1, "manual"))
        self.assertEqual(st.query_library(sort="size", descending=True)["items"][0]["key"], "movie_24")
        self.assertEqual(st.query_library(q="t0")["total"], 10)

    def test_sessions_expire(self):
        st = Store(":memory:")
        self.assertIsNone(st.get_session(st.create_session("u", "n", True, ttl_days=-1)))
        tok = st.create_session("u", "n", True)
        self.assertEqual(st.get_session(tok)["user_name"], "n")
        st.delete_session(tok)
        self.assertIsNone(st.get_session(tok))

    def test_interrupted_runs_are_marked_on_start(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "t.db")
            st.start_run("manual", False)
            st.close()
            self.assertEqual(Store(Path(d) / "t.db").last_run()["status"], "interrupted")

    def test_history_pagination_and_filter(self):
        st = Store(":memory:")
        for i in range(5):
            st.add_history("queued" if i % 2 else "deleted", f"k{i}", f"T{i}", "movie", "auto", {"i": i})
        page = st.list_history(limit=2)
        self.assertEqual((page["total"], len(page["items"]), page["items"][0]["details"]), (5, 2, {"i": 4}))
        self.assertEqual(st.list_history(event="deleted")["total"], 3)


class SchedulerTests(unittest.TestCase):
    def make(self, runner, settings=None):
        store = Store(":memory:")
        base = apply_patch(Settings(), {"jellyfin": {"url": "http://x", "api_key": "k"}})
        base.setup_complete = True
        save_settings(store, settings or base)
        return store, Scheduler(store, runner)

    def test_run_once_records_the_run_and_a_dry_run_override_is_not_persisted(self):
        seen = []
        store, sch = self.make(lambda s: seen.append(s.dry_run) or {"deleted": 0})
        summary = sch.run_once("manual", dry_run_override=False)
        self.assertEqual((seen, summary["deleted"]), ([False], 0))
        run = store.last_run()
        self.assertEqual((run["status"], run["trigger"], run["dry_run"]), ("ok", "manual", False))
        self.assertTrue(load_settings(store).dry_run)

    def test_errors_are_recorded_and_do_not_raise(self):
        def boom(_):
            raise RuntimeError("kaboom")
        store, sch = self.make(boom)
        self.assertIsNone(sch.run_once())
        self.assertEqual((store.last_run()["status"], sch.running), ("error", False))
        self.assertIn("kaboom", store.last_run()["error"])

    def test_skips_until_setup_is_complete(self):
        called = []
        store, sch = self.make(lambda s: called.append(1) or {}, Settings())
        self.assertIsNone(sch.run_once())
        self.assertEqual((called, store.last_run()), ([], None))

    def test_no_overlapping_runs(self):
        gate, started = threading.Event(), threading.Event()

        def slow(_):
            started.set(); gate.wait(5); return {}
        _, sch = self.make(slow)
        t = threading.Thread(target=sch.run_once)
        t.start()
        self.assertTrue(started.wait(2))
        self.assertIsNone(sch.run_once())
        self.assertFalse(sch.trigger())
        gate.set(); t.join(3)

    def test_background_loop_runs_at_start_and_on_trigger(self):
        runs = []
        _, sch = self.make(lambda s: runs.append(1) or {})
        sch.start()
        try:
            deadline = time.time() + 3
            while len(runs) < 1 and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(runs), 1)
            while sch.running and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(sch.trigger(dry_run=True))
            deadline = time.time() + 3
            while len(runs) < 2 and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(runs), 2)
        finally:
            sch.stop()


if __name__ == "__main__":
    unittest.main()
