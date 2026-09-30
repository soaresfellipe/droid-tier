import base64
import http.server
import json
import threading
import unittest
from unittest import mock

import droid_tier.core as core
from droid_tier import notify

from test_core import Base, bucket


class Capture(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Capture.received.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


class ChannelsTest(unittest.TestCase):
    def setUp(self):
        Capture.received = []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def test_ntfy_and_webhook(self):
        cfg = {"notify": {"ntfy": self.url + "/topic", "webhook": self.url + "/hook"}}
        failed = notify.send(cfg, "droid-tier: home → oc", "New sessions use oc.", {"event": "tier_changed"})
        self.assertEqual(failed, [])
        by_path = {p: (h, b) for p, h, b in Capture.received}
        headers, body = by_path["/topic"]
        self.assertEqual(body.decode(), "New sessions use oc.")
        encoded = headers["Title"]
        self.assertTrue(encoded.startswith("=?UTF-8?B?"))
        self.assertEqual(base64.b64decode(encoded[10:-2]).decode(), "droid-tier: home → oc")
        payload = json.loads(by_path["/hook"][1])
        self.assertEqual((payload["source"], payload["event"]), ("droid-tier", "tier_changed"))

    def test_failure_is_logged_not_raised(self):
        cfg = {"notify": {"webhook": "http://127.0.0.1:9/nothing"}}
        with mock.patch.object(core, "log") as log:
            self.assertEqual(notify.send(cfg, "t", "m"), ["webhook"])
        self.assertIn("webhook notification failed", log.call_args[0][0])

    def test_validate(self):
        self.assertEqual(notify.validate(None), {})
        for bad, msg in (({"email": "x"}, "unknown fields"), ({"ntfy": "ntfy.sh/x"}, "URL"), ("x", "table")):
            with self.subTest(bad=bad), self.assertRaisesRegex(core.ConfigError, msg):
                notify.validate(bad)


class RunNotifiesTest(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(core, "ECHO", False)
        p.start()
        self.addCleanup(p.stop)

    def run_with(self, limits_or_error):
        cfg = self.example()
        kw = {"side_effect": limits_or_error} if isinstance(limits_or_error, Exception) else \
            {"return_value": limits_or_error}
        with mock.patch.object(core, "fetch_limits", **kw), mock.patch.object(notify, "send") as send:
            try:
                core.run(cfg)
            except core.LimitsError:
                pass
        return send

    def test_tier_change_and_back(self):
        send = self.run_with({"standard": {"weekly": bucket(100)}, "core": {"weekly": bucket(10)}})
        title, text, event = send.call_args[0][1:]
        self.assertEqual(title, "droid-tier: home → droid")
        self.assertIn("standard weekly 100%", text)
        self.assertEqual((event["from"], event["to"]), ("home", "droid"))

        send = self.run_with({"standard": {"weekly": bucket(10)}})
        self.assertEqual(send.call_args[0][1], "droid-tier: droid → home")
        self.assertIn("back on your defaults", send.call_args[0][2])

        # no change, no notification
        self.assertFalse(self.run_with({"standard": {"weekly": bucket(10)}}).called)

    def test_api_error_warns_once_and_on_recovery(self):
        self.assertEqual(self.run_with(OSError("timeout")).call_args[0][3]["event"], "api_error")
        self.assertFalse(self.run_with(OSError("timeout")).called)
        send = self.run_with({"standard": {"weekly": bucket(10)}})
        self.assertEqual(send.call_args_list[0][0][3]["event"], "api_ok")


if __name__ == "__main__":
    unittest.main()
