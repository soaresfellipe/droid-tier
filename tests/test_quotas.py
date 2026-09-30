import http.server
import json
import threading
import unittest
from unittest import mock

import droid_tier.core as core
from droid_tier import notify, quotas

from test_core import Base, bucket

REAL_CHECK = quotas.check  # conftest replaces quotas.check during each test

FUTURE = "2099-01-01T00:00:00Z"
OC_USAGE = {"usage": {
    "rolling": {"status": "ok", "percent": 36, "resetsAt": FUTURE},
    "weekly": {"status": "ok", "percent": 14, "resetsAt": FUTURE},
    "monthly": {"status": "ok", "percent": 7, "resetsAt": FUTURE},
}}


class Usage(http.server.BaseHTTPRequestHandler):
    body = OC_USAGE
    seen_auth = None

    def do_GET(self):
        Usage.seen_auth = self.headers.get("Authorization")
        payload = json.dumps(Usage.body).encode()
        self.send_response(200 if self.path.endswith("/usage") else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


class AdaptersTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Usage)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}/zen/go/v1"
        Usage.body = OC_USAGE

    def test_opencode_go(self):
        w = quotas.opencode_go(self.base, "k")
        self.assertEqual(Usage.seen_auth, "Bearer k")
        self.assertEqual(w["rolling"], {"usedPercent": 36.0, "windowEnd": FUTURE, "status": "ok"})
        self.assertEqual(sorted(w), ["monthly", "rolling", "weekly"])

    def test_opencode_go_rejects_unexpected_shape(self):
        for body in ({}, {"usage": {}}, {"usage": {"weekly": {"status": "ok"}}}):
            Usage.body = body
            with self.subTest(body=body), self.assertRaises(quotas.QuotaError):
                quotas.opencode_go(self.base, "k")

    def test_openrouter(self):
        with mock.patch.object(quotas, "_get", return_value={"data": {"limit": 20, "limit_remaining": 1}}):
            self.assertEqual(quotas.openrouter("https://openrouter.ai/api/v1", "k")["key limit"]["usedPercent"], 95.0)
        with mock.patch.object(quotas, "_get", return_value={"data": {"limit": None, "limit_remaining": None}}):
            self.assertEqual(quotas.openrouter("https://openrouter.ai/api/v1", "k"), {})

    def test_deepseek(self):
        with mock.patch.object(quotas, "_get", return_value={"is_available": False, "balance_infos": []}):
            w = quotas.deepseek("https://api.deepseek.com", "k")
        self.assertEqual(quotas.hits(w, 95), ["balance 100%"])

    def test_adapter_for(self):
        self.assertIs(quotas.adapter_for("https://opencode.ai/zen/go/v1"), quotas.opencode_go)
        self.assertIsNone(quotas.adapter_for("https://opencode.ai/zen/v1"))  # Zen (pay as you go), not Go
        self.assertIs(quotas.adapter_for("https://openrouter.ai/api/v1"), quotas.openrouter)
        self.assertIsNone(quotas.adapter_for("https://api.z.ai/api/paas/v4"))

    def test_hits_counts_status(self):
        w = {"weekly": {"usedPercent": 40.0, "windowEnd": FUTURE, "status": "limited"}}
        self.assertEqual(quotas.hits(w, 95), ["weekly limited"])
        self.assertEqual(quotas.hits({"error": "x"}, 95), [])
        self.assertEqual(quotas.hits(None, 95), [])


THREE = '''
[providers.opencode-go]
base_url = "https://opencode.ai/zen/go/v1"
[providers.zai]
base_url = "https://api.z.ai/api/paas/v4"

[[fallback]]
name = "droid"
pool = "core"
session = "glm-5.3-flash@high"

[[fallback]]
name = "oc"
provider = "opencode-go"
session = "glm-5.3-flash@high"

[[fallback]]
name = "zai"
provider = "zai"
session = "glm-5.3"
'''

EXHAUSTED = {"standard": {"weekly": bucket(100)}, "core": {"weekly": bucket(100)}}


class PickWithQuotasTest(Base):
    def test_skips_exhausted_provider(self):
        cfg = self.config(THREE)
        tier, hits = core.pick_tier(cfg, EXHAUSTED, "standard", provider_hits={"opencode-go": ["rolling 97%"]})
        self.assertEqual(tier, "zai")
        self.assertEqual(hits["opencode-go"], ["rolling 97%"])

    def test_provider_with_room_or_unknown_is_used(self):
        cfg = self.config(THREE)
        self.assertEqual(core.pick_tier(cfg, EXHAUSTED, "standard", provider_hits={"opencode-go": []})[0], "oc")
        self.assertEqual(core.pick_tier(cfg, EXHAUSTED, "standard")[0], "oc")

    def test_all_exhausted_stays_on_last(self):
        cfg = self.config(THREE)
        tier, _ = core.pick_tier(cfg, EXHAUSTED, "standard",
                                 provider_hits={"opencode-go": ["weekly 99%"], "zai": ["balance 100%"]})
        self.assertEqual(tier, "zai")

    def test_check_uses_adapter_and_settings_key(self):
        cfg = self.config(THREE)
        settings = {"customModels": [
            {"model": "glm-5.3-flash", "baseUrl": "https://opencode.ai/zen/go/v1/", "apiKey": "oc-key"},
            {"model": "glm-5.3", "baseUrl": "https://api.z.ai/api/paas/v4", "apiKey": "zai-key"}]}
        seen = []

        def fake_oc(base_url, key):
            seen.append(key)
            return {"rolling": {"usedPercent": 97.0, "windowEnd": FUTURE, "status": "ok"}}

        with mock.patch.dict(quotas.ADAPTERS, {("opencode.ai", "/zen/go"): fake_oc}):
            result = REAL_CHECK(cfg, settings)
        self.assertEqual(seen, ["oc-key"])
        self.assertEqual(result["zai"], None)  # no adapter
        self.assertEqual(quotas.hits(result["opencode-go"], 95), ["rolling 97%"])

        def broken(base_url, key):
            raise quotas.QuotaError("timeout")

        with mock.patch.dict(quotas.ADAPTERS, {("opencode.ai", "/zen/go"): broken}):
            self.assertEqual(REAL_CHECK(cfg, settings)["opencode-go"], {"error": "timeout"})


class RunWithQuotasTest(Base):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(core, "ECHO", False), mock.patch.object(notify, "send")):
            self.send = p.start()
            self.addCleanup(p.stop)

    def test_run_moves_past_exhausted_provider_and_logs_quota_errors_once(self):
        with open(self.settings_path) as f:
            s = json.load(f)
        s["customModels"].append({"model": "glm-5.3", "id": "custom:ZAI-GLM-5.3-0",
                                  "baseUrl": "https://api.z.ai/api/paas/v4", "apiKey": "z"})
        self.write_settings(s)
        cfg = self.config(THREE)
        quota = {"opencode-go": {"rolling": {"usedPercent": 99.0, "windowEnd": FUTURE, "status": "ok"}}, "zai": None}
        with mock.patch.object(core, "fetch_limits", return_value=EXHAUSTED), \
                mock.patch.object(quotas, "check", return_value=quota):
            msg = core.run(cfg)
        self.assertIn("-> zai", msg)
        self.assertIn("opencode-go rolling 99%", msg)
        self.assertEqual(self.send.call_args[0][3]["to"], "zai")

        broken = {"opencode-go": {"error": "timeout"}, "zai": None}
        with mock.patch.object(core, "fetch_limits", return_value=EXHAUSTED), \
                mock.patch.object(quotas, "check", return_value=broken), \
                mock.patch.object(core, "log") as log:
            core.run(cfg)
            core.run(cfg)
        quota_lines = [c for c in log.call_args_list if "couldn't read the opencode-go quota" in c[0][0]]
        self.assertEqual(len(quota_lines), 1)


if __name__ == "__main__":
    unittest.main()
