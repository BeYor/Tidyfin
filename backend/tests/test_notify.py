import json
import unittest

from tidyfin.notify import Notifier, format_events, notify_cycle, notify_error
from tidyfin.settings import NotifySettings, Settings
from tests.helpers import http_server, webhook_server


def events():
    return [{"type": "queued", "title": f"Movie {i}", "size_bytes": 2_000_000_000} for i in range(12)] + \
           [{"type": "deleted", "title": "Gone", "size_bytes": 5_000_000_000}, {"type": "delete_failed", "title": "Stuck", "size_bytes": 0}]


class FormatTests(unittest.TestCase):
    def test_groups_sizes_and_truncation(self):
        title, body = format_events("en", events(), False, NotifySettings(enabled=True))
        self.assertEqual(title, "Tidyfin")
        self.assertIn("Queued for deletion: 12 (24.0 GB)", body)
        self.assertIn("and 2 more", body)
        self.assertIn("Deleted: 1 (5.0 GB)", body)
        self.assertIn("Deletion failed: 1", body)

    def test_event_filters_and_dry_run_label(self):
        cfg = NotifySettings(enabled=True, on_queued=False, on_deleted=False, on_errors=True)
        self.assertEqual(format_events("en", events(), False, cfg)[1], "Deletion failed: 1\n  - Stuck")
        self.assertIsNone(format_events("en", [{"type": "queued", "title": "x"}], False, cfg))
        self.assertIn("(dry run)", format_events("en", events(), True, NotifySettings())[0])

    def test_polish_texts(self):
        self.assertIn("Dodano do kolejki usunięć", format_events("pl", events(), False, NotifySettings())[1])


class ChannelTests(unittest.TestCase):
    def test_webhook_receives_json_with_events(self):
        got = []
        with http_server(webhook_server(got)) as srv:
            cfg = NotifySettings(enabled=True, webhook_url=srv.url + "/hook")
            s = Settings(notifications=cfg)
            notify_cycle(s, events(), {"queued": 12})
        body = json.loads(got[0]["body"])
        self.assertEqual((got[0]["path"], body["app"], body["event"], body["summary"]), ("/hook", "tidyfin", "cycle", {"queued": 12}))
        self.assertEqual(len(body["events"]), 14)

    def test_discord_and_ntfy_payloads(self):
        got = []
        with http_server(webhook_server(got)) as srv:
            n = Notifier(NotifySettings(discord_url=srv.url + "/discord", ntfy_url=srv.url + "/topic", ntfy_token="tok"))
            res = n.send("Title", "Body ąę")
        self.assertEqual(res, {"discord": True, "ntfy": True})
        self.assertIn("Body", json.loads(got[0]["body"])["content"])
        self.assertEqual(got[1]["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(got[1]["body"].decode("utf-8"), "Title\nBody ąę")          # UTF-8 body, ASCII-only headers

    def test_a_failing_channel_is_reported_and_does_not_stop_the_others(self):
        got, bad = [], []
        with http_server(webhook_server(got)) as ok, http_server(webhook_server(bad, status=500)) as broken:
            res = Notifier(NotifySettings(discord_url=broken.url, webhook_url=ok.url)).send("t", "b")
        self.assertEqual((res["discord"], res["webhook"]), ("rejected", True))
        import requests
        res = Notifier(NotifySettings(webhook_url="http://127.0.0.1:1/x"), session=requests.Session()).send("t", "b")
        self.assertNotEqual(res["webhook"], True)                                   # connection error text, no exception

    def test_disabled_or_channel_less_settings_send_nothing(self):
        got = []
        with http_server(webhook_server(got)) as srv:
            notify_cycle(Settings(notifications=NotifySettings(enabled=False, webhook_url=srv.url)), events(), {})
            notify_cycle(Settings(notifications=NotifySettings(enabled=True)), events(), {})
            notify_error(Settings(notifications=NotifySettings(enabled=True, on_errors=False, webhook_url=srv.url)), "boom")
        self.assertEqual(got, [])

    def test_error_notification(self):
        got = []
        with http_server(webhook_server(got)) as srv:
            notify_error(Settings(notifications=NotifySettings(enabled=True, webhook_url=srv.url)), "boom")
        self.assertEqual(json.loads(got[0]["body"])["event"], "error")

    def test_channel_detection(self):
        self.assertEqual(NotifySettings(telegram_token="t").channels, [])            # needs a chat id too
        self.assertEqual(NotifySettings(telegram_token="t", telegram_chat_id="1", ntfy_url="http://x").channels, ["ntfy", "telegram"])


if __name__ == "__main__":
    unittest.main()
