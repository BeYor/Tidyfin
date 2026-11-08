import json
import tempfile
import unittest

from tidyfin.routes import COOKIE, Request, Router
from tests.test_services import make_app


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.app, self.f, self.tmp = make_app()
        self.router = Router(self.app)
        self.cookies = {COOKIE: self.app.login("admin", "pass")["token"]}

    def tearDown(self):
        self.app.store.close(); self.tmp.cleanup()

    def call(self, method, path, body=None, query=None, auth=True):
        raw = json.dumps(body).encode() if body is not None else b""
        return self.router.dispatch(Request(method, path, query or {}, raw, self.cookies if auth else {}, "127.0.0.1"))

    def test_public_and_protected_endpoints(self):
        self.assertEqual(self.call("GET", "/api/health", auth=False).body["setup_complete"], True)
        self.assertEqual(self.call("GET", "/api/options", auth=False).status, 200)
        for path in ("/api/status", "/api/queue", "/api/settings", "/api/library", "/api/history", "/api/poster/movie_10"):
            r = self.call("GET", path, auth=False)
            self.assertEqual((r.status, r.body["code"]), (401, "unauthorized"), path)

    def test_login_sets_a_cookie_and_logout_clears_it(self):
        r = self.call("POST", "/api/auth/login", {"username": "admin", "password": "pass"}, auth=False)
        self.assertEqual((r.status, r.set_cookie[0]), (200, COOKIE))
        bad = self.call("POST", "/api/auth/login", {"username": "admin", "password": "x"}, auth=False)
        self.assertEqual((bad.status, bad.body["code"]), (401, "invalid_credentials"))
        out = self.call("POST", "/api/auth/logout", auth=True)
        self.assertEqual(out.delete_cookie, COOKIE)
        self.assertEqual(self.call("GET", "/api/auth/me", auth=True).status, 401)   # session is gone

    def test_errors_carry_status_and_code(self):
        r = self.call("POST", "/api/items/movie_404/protect")
        self.assertEqual((r.status, r.body["code"]), (404, "not_in_library"))
        self.assertEqual(self.call("POST", "/api/items/bad-key/protect").body["code"], "invalid_key")
        self.assertEqual(self.call("POST", "/api/items/movie_10/schedule", {"days": "x"}).body["code"], "days_required")
        self.assertEqual(self.call("PUT", "/api/settings", None).status, 200)               # empty body is a no-op patch
        r = self.router.dispatch(Request("POST", "/api/items/movie_10/schedule", {}, b"{not json", self.cookies))
        self.assertEqual((r.status, r.body["code"]), (400, "bad_json"))
        self.assertEqual(self.call("GET", "/api/nope").status, 404)
        self.assertEqual(self.call("DELETE", "/api/status").status, 405)

    def test_item_workflow_through_the_api(self):
        self.assertEqual(self.call("POST", "/api/items/movie_10/protect").status, 200)
        lib = self.call("GET", "/api/library", query={"status": "protected"}).body
        self.assertEqual([i["key"] for i in lib["items"]], ["movie_10"])
        self.assertEqual(self.call("GET", "/api/protected").body[0]["title"], "Old movie")
        self.assertEqual(self.call("DELETE", "/api/items/movie_10/protect").status, 200)
        self.assertEqual(self.call("POST", "/api/items/series_500/postpone", {"days": 5}).status, 200)
        bulk = self.call("POST", "/api/items/bulk", {"action": "postpone", "keys": ["series_500", "movie_1"], "params": {"days": 2}}).body
        self.assertEqual((bulk["ok"], bulk["failed"]), (1, 1))
        self.assertEqual(self.call("GET", "/api/items/movie_10").body["title"], "Old movie")

    def test_library_query_parameters(self):
        r = self.call("GET", "/api/library", query={"type": "series", "q": "sh", "sort": "size", "order": "desc", "limit": "5"})
        self.assertEqual([i["key"] for i in r.body["items"]], ["series_500"])
        self.assertEqual(self.call("GET", "/api/library", query={"limit": "abc"}).status, 200)   # junk falls back to defaults

    def test_images_and_json_are_distinguished(self):
        r = self.call("GET", "/api/poster/movie_10", query={"w": "200"})
        self.assertEqual((r.content_type, r.raw[:2]), ("image/jpeg", b"\xff\xd8"))
        p = self.call("POST", "/api/badges/preview", {"badges": [{"text": "HI", "corner": "top-left"}]})
        self.assertEqual((p.content_type, p.status), ("image/jpeg", 200))

    def test_run_is_refused_while_a_cycle_runs(self):
        self.app.scheduler.running = True
        r = self.call("POST", "/api/run", {})
        self.assertEqual((r.status, r.body["code"]), (409, "already_running"))
        self.app.scheduler.running = False

    def test_log_stream_replays_recent_lines_then_keeps_alive(self):
        from tidyfin.logbus import log, setup_logging
        setup_logging()
        log.info("hello from the test")
        r = self.call("GET", "/api/logs/stream", query={"after": "0"})
        self.assertEqual((r.content_type, r.status), ("text/event-stream", 200))
        first = next(r.stream)
        self.assertTrue(first.startswith("data: ") and "msg" in first)
        r.stream.close()

    def test_unexpected_exceptions_do_not_leak_details(self):
        self.app.status = lambda: 1 / 0
        r = self.call("GET", "/api/status")
        self.assertEqual((r.status, r.body["code"]), (500, "internal_error"))
        self.assertNotIn("division", json.dumps(r.body))

    def test_wizard_route_on_a_fresh_install(self):
        from tests.helpers import FakeFactory
        from tidyfin.services import TidyfinApp
        with tempfile.TemporaryDirectory() as d:
            fresh = TidyfinApp(d, FakeFactory())
            router = Router(fresh)
            body = json.dumps({"url": "jf:8096", "username": "admin", "password": "pass", "locale": "pl", "languages": ["pol"]}).encode()
            r = router.dispatch(Request("POST", "/api/setup/jellyfin", {}, body))
            self.assertEqual((r.status, r.set_cookie[0]), (200, COOKIE))
            again = router.dispatch(Request("POST", "/api/setup/jellyfin", {}, body))
            self.assertEqual((again.status, again.body["code"]), (400, "setup_done"))
            fresh.store.close()


if __name__ == "__main__":
    unittest.main()
