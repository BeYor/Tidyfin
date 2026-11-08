import tempfile
import unittest

from tidyfin.devtools.devserver import build
from tidyfin.routes import COOKIE, Request, Router


class DevServerSmokeTest(unittest.TestCase):
    """The demo data must stay valid: it feeds the screenshots and manual UI work."""

    def test_demo_install_runs_a_full_cycle_with_every_feature_visible(self):
        with tempfile.TemporaryDirectory() as d:
            app = build(d, empty=False)
            self.assertIsNotNone(app.scheduler.run_once("startup"))
            router = Router(app)
            login = router.dispatch(Request("POST", "/api/auth/login", raw_body=b'{"username":"admin","password":"pass"}'))
            cookies = {COOKIE: login.set_cookie[1]}
            get = lambda path, **q: router.dispatch(Request("GET", path, q, b"", cookies)).body
            status = get("/api/status")
            self.assertGreater(status["queue"]["total"], 5)
            self.assertGreater(status["library"]["total"], 20)
            report = get("/api/language/report")
            self.assertEqual(set(report["by_profile"]), {"pol", "ger", "fre"})      # three languages detected
            self.assertTrue(report["uncertain"])                                    # and some need manual review
            self.assertEqual(get("/api/library", status="protected")["total"], 2)   # tagged in the fake Radarr/Sonarr
            app.store.close()

    def test_fresh_install_starts_with_the_wizard(self):
        with tempfile.TemporaryDirectory() as d:
            app = build(d, empty=True)
            self.assertFalse(app.settings().setup_complete)
            app.store.close()


if __name__ == "__main__":
    unittest.main()
