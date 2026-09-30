import copy
import io
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

import droid_tier.core as m

NOW = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.timezone.utc)
FUTURE = "2026-10-05T04:35:21.628Z"
PAST = "2026-09-01T00:00:00Z"

OC_URL = "https://opencode.ai/zen/go/v1"
# The user's defaults: Standard, with no effort set for the validator.
SETTINGS = {
    "trustedFolders": {"/home/x": {}},
    "sessionDefaultSettings": {"model": "claude-sonnet-5-5", "reasoningEffort": "medium",
                               "autonomyMode": "auto-high", "specModeModel": "claude-opus-5-5",
                               "specModeReasoningEffort": "high"},
    "missionModelSettings": {"workerModel": "claude-sonnet-5-5", "validationWorkerModel": "gpt-6-luna",
                             "skipUserTesting": True},
    "imageGenerationModel": "gemini-3-pro-image-preview",
    "customModels": [
        {"model": "glm-5.3-flash", "id": "custom:OC-GLM-5.3-Flash-7", "baseUrl": OC_URL + "/", "apiKey": "secret"},
        {"model": "glm-5.3", "id": "custom:OC-GLM-5.3-6", "baseUrl": OC_URL, "apiKey": "secret"},
        {"model": "deepseek-v4.1-flash", "id": "custom:OC-DeepSeek-V4.1-Flash-2", "baseUrl": OC_URL},
        {"model": "glm-5.3", "id": "custom:ZAI-GLM-5.3-0", "baseUrl": "https://api.z.ai/api/anthropic"},
    ],
}


def bucket(pct, end=FUTURE):
    return {"usedPercent": pct, "windowEnd": end}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings_path = os.path.join(self.dir.name, "settings.json")
        self.write_settings(SETTINGS)
        state = os.path.join(self.dir.name, "state")
        for attr, name in (("STATE_DIR", ""), ("HOME_FILE", "home.json"),
                           ("PIN_FILE", "pin"), ("LOG_FILE", "log")):
            patched = os.path.join(state, name) if name else state
            original = getattr(m, attr)
            setattr(m, attr, patched)
            self.addCleanup(setattr, m, attr, original)

    def write_settings(self, s):
        with open(self.settings_path, "w") as f:
            json.dump(s, f, indent=2)

    def config(self, text):
        path = os.path.join(self.dir.name, "config.toml")
        with open(path, "w") as f:
            # top-level keys must come before any table in TOML
            f.write(f'settings = "{self.settings_path.replace(os.sep, "/")}"\n' + text)
        return m.load_config(path)

    def example(self):
        return self.config(m.EXAMPLE_CONFIG)


class ConfigTest(Base):
    def test_example_loads_and_resolves(self):
        cfg = self.example()
        self.assertEqual([fb["name"] for fb in cfg["fallbacks"]], ["droid", "oc"])
        r = m.resolve_all(cfg, SETTINGS)
        self.assertEqual(r["droid"]["spec"], ("glm-5.3", "high"))
        self.assertEqual(r["oc"]["validator"], ("custom:OC-DeepSeek-V4.1-Flash-2", "high"))

    def test_provider_resolves_by_base_url_not_index(self):
        cfg = self.config('''
[providers.oc]
base_url = "https://opencode.ai/zen/go/v1"
[[fallback]]
name = "oc"
provider = "oc"
session = "glm-5.3-flash@high"
spec = "glm-5.3"
''')
        r = m.resolve_all(cfg, SETTINGS)["oc"]
        self.assertEqual(r["session"], ("custom:OC-GLM-5.3-Flash-7", "high"))
        # the same model name on Z.AI must not be confused
        self.assertEqual(r["spec"], ("custom:OC-GLM-5.3-6", None))

    def test_missing_custom_model_is_error(self):
        cfg = self.config('''
[providers.oc]
base_url = "https://opencode.ai/zen/go/v1"
[[fallback]]
name = "oc"
provider = "oc"
validator = "kimi-k3@high"
''')
        with self.assertRaisesRegex(m.ConfigError, "kimi-k3"):
            m.resolve_all(cfg, SETTINGS)

    def test_validation_errors(self):
        cases = {
            'x = 1': "no \\[\\[fallback",
            'home_pool = "gold"\n[[fallback]]\nname = "a"\nsession = "a"': "home_pool",
            '[[fallback]]\nsession = "a"': "has no name",
            '[[fallback]]\nname = "home"\nsession = "a"': "reserved",
            '[[fallback]]\nname = "a"\nsession = "a"\n[[fallback]]\nname = "a"\nsession = "b"': "duplicated",
            '[[fallback]]\nname = "a"\npool = "gold"\nsession = "a"': "pool must be",
            '[[fallback]]\nname = "a"\nprovider = "nope"\nsession = "a"': "is not in",
            '[[fallback]]\nname = "a"\nsesion = "a"': "unknown fields",
            '[[fallback]]\nname = "a"': "defines no role",
        }
        for text, msg in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(m.ConfigError, msg):
                self.config(text)


class UrlTest(Base):
    """API keys and messages must not travel over plain http (except localhost)."""

    def test_factory_api_must_be_https(self):
        with self.assertRaisesRegex(m.ConfigError, "https"):
            self.config('factory_api = "http://app.factory.ai"\n[[fallback]]\nname = "a"\nsession = "a"')
        cfg = self.config('factory_api = "http://localhost:8080"\n[[fallback]]\nname = "a"\nsession = "a"')
        self.assertEqual(cfg["api"], "http://localhost:8080")

    def test_provider_base_url_must_be_https(self):
        head = '[providers.oc]\nbase_url = "%s"\n[[fallback]]\nname = "oc"\nprovider = "oc"\nsession = "a"'
        with self.assertRaisesRegex(m.ConfigError, "https"):
            self.config(head % "http://opencode.ai/zen/go/v1")
        self.config(head % "http://127.0.0.1:11434/v1")  # loopback: local Ollama/vLLM stays allowed


class PoolTest(unittest.TestCase):
    def test_infer_pool(self):
        self.assertEqual(m.infer_pool({"a": ("claude-opus-5-5", None), "b": ("glm-5.3", None)}), "standard")
        self.assertEqual(m.infer_pool({"a": ("glm-5.3", "high"), "b": ("deepseek-v4.1-flash", None)}), "core")
        self.assertIsNone(m.infer_pool({"a": ("custom:OC-GLM-5.3-0", None), "b": (None, None)}))

    def test_malformed_window_end_counts_as_hit(self):
        # Must not crash the scheduled run, and must not read as "reset":
        # restoring out-of-quota defaults is the worse mistake.
        pool = {"weekly": {"usedPercent": 99, "windowEnd": "not-a-date"}}
        self.assertEqual(m.pool_hits(pool, 95, now=NOW), ["weekly 99%"])


class PickTest(Base):
    def pick(self, limits, hpool="standard"):
        return m.pick_tier(self.example(), limits, hpool, NOW)[0]

    def test_all_free_stays_home(self):
        self.assertEqual(self.pick({"standard": {"weekly": bucket(10)}, "core": {"weekly": bucket(0)}}), "home")

    def test_standard_over_threshold(self):
        self.assertEqual(self.pick({"standard": {"fiveHour": bucket(96)}, "core": {"weekly": bucket(5)}}), "droid")

    def test_both_exhausted(self):
        self.assertEqual(self.pick({"standard": {"monthly": bucket(100)}, "core": {"weekly": bucket(99)}}), "oc")

    def test_expired_window_ignored(self):
        self.assertEqual(self.pick({"standard": {"fiveHour": bucket(100, PAST)}}), "home")

    def test_missing_core_counts_as_free(self):
        self.assertEqual(self.pick({"standard": {"monthly": bucket(100)}}), "droid")

    def test_core_home_skips_core_fallback(self):
        limits = {"standard": {"weekly": bucket(0)}, "core": {"weekly": bucket(100)}}
        self.assertEqual(self.pick(limits, hpool="core"), "oc")

    def test_custom_home_never_leaves(self):
        limits = {"standard": {"weekly": bucket(100)}, "core": {"weekly": bucket(100)}}
        self.assertEqual(self.pick(limits, hpool=None), "home")

    def test_no_fallback_without_pool_stays_on_last(self):
        cfg = self.config('[[fallback]]\nname = "c"\npool = "core"\nsession = "b"')
        limits = {"standard": {"weekly": bucket(100)}, "core": {"weekly": bucket(100)}}
        self.assertEqual(m.pick_tier(cfg, limits, "standard", NOW)[0], "c")


class CycleTest(Base):
    def test_leave_and_restore_home_exactly(self):
        cfg = self.example()
        original = copy.deepcopy(m.load_settings(self.settings_path))

        d = m.Droid(cfg)
        self.assertEqual(d.current(), "home")
        self.assertEqual(m.home_pool(cfg, d.home), "standard")
        self.assertTrue(d.go("droid"))
        self.assertTrue(os.path.exists(m.HOME_FILE))
        after = m.load_settings(self.settings_path)
        self.assertEqual(after["sessionDefaultSettings"]["model"], "glm-5.3-flash")
        self.assertEqual(after["sessionDefaultSettings"]["autonomyMode"], "auto-high")
        self.assertEqual(after["customModels"], SETTINGS["customModels"])
        self.assertEqual(after["imageGenerationModel"], SETTINGS["imageGenerationModel"])
        if os.name == "posix":
            self.assertEqual(os.stat(self.settings_path).st_mode & 0o777, 0o600)

        # Switching between fallbacks must not overwrite the saved defaults.
        d = m.Droid(cfg)
        self.assertEqual(d.current(), "droid")
        self.assertTrue(d.go("oc"))
        self.assertEqual(m.read_home()["session"], ("claude-sonnet-5-5", "medium"))

        d = m.Droid(cfg)
        self.assertEqual(d.current(), "oc")
        self.assertEqual(m.home_pool(cfg, d.home), "standard")  # comes from the saved copy, not the settings
        self.assertTrue(d.go("home"))
        self.assertFalse(os.path.exists(m.HOME_FILE))
        self.assertEqual(m.load_settings(self.settings_path), original)

    def test_noop_when_already_there(self):
        cfg = self.example()
        m.Droid(cfg).go("droid")
        self.assertFalse(m.Droid(cfg).go("droid"))
        self.assertFalse(m.Droid(cfg).go("home") and m.Droid(cfg).go("home"))

    def test_refuses_to_save_fallback_as_home(self):
        cfg = self.example()
        d = m.Droid(cfg)
        m.set_roles(d.s, d.fallbacks["oc"])
        self.write_settings(d.s)
        d = m.Droid(cfg)
        self.assertEqual(d.current(), "home (matches fallback oc)")
        with self.assertRaisesRegex(m.ConfigError, "no defaults are saved"):
            d.go("droid")
        self.assertFalse(os.path.exists(m.HOME_FILE))




class WriteRetryTest(Base):
    def test_retries_when_file_is_locked(self):
        from unittest import mock
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError("em uso")
            real(src, dst)

        with mock.patch.object(m.os, "replace", flaky), mock.patch.object(m.time, "sleep"):
            m.write_settings(self.settings_path, {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(m.load_settings(self.settings_path), {"ok": True})

    def test_gives_up_and_cleans_temp(self):
        from unittest import mock
        with mock.patch.object(m.os, "replace", side_effect=PermissionError("em uso")), \
                mock.patch.object(m.time, "sleep"), self.assertRaises(PermissionError):
            m.write_settings(self.settings_path, {"ok": True})
        leftovers = [f for f in os.listdir(self.dir.name) if f.startswith(".settings.droid-tier.")]
        self.assertEqual(leftovers, [])




class KeyFileTest(Base):
    @unittest.skipIf(os.name != "posix", "POSIX permissions")
    def test_warns_once_about_loose_key_file(self):
        keyfile = os.path.join(self.dir.name, "factory-api-key.env")
        with open(keyfile, "w") as f:
            f.write("FACTORY_API_KEY=secret\n")
        os.chmod(keyfile, 0o644)
        with mock.patch.object(m, "KEY_FILE", keyfile), mock.patch.object(m, "log") as log:
            self.assertEqual(m.api_key(), "secret")
            self.assertEqual(m.api_key(), "secret")  # the 5-min schedule must not spam the log
        self.assertEqual(len(log.call_args_list), 1)
        self.assertIn("chmod 600", log.call_args[0][0])

    @unittest.skipIf(os.name != "posix", "POSIX permissions")
    def test_no_warning_for_0600(self):
        keyfile = os.path.join(self.dir.name, "factory-api-key.env")
        with open(keyfile, "w") as f:
            f.write("FACTORY_API_KEY=secret\n")
        os.chmod(keyfile, 0o600)
        with mock.patch.object(m, "KEY_FILE", keyfile), mock.patch.object(m, "log") as log:
            self.assertEqual(m.api_key(), "secret")
        self.assertFalse(log.called)


class ValidateLimitsTest(unittest.TestCase):
    def test_accepts_real_shape(self):
        data = {"limits": {"standard": {"fiveHour": {"usedPercent": 0}, "weekly": bucket(100)},
                           "core": {"weekly": bucket(40)}}, "overagePreference": None}
        self.assertEqual(m.validate_limits(data)["standard"]["weekly"]["usedPercent"], 100)
        # account without Droid Core
        m.validate_limits({"limits": {"standard": {"weekly": bucket(10)}}})

    def test_rejects_shapes_that_would_look_like_free_limits(self):
        for data in ({}, {"limits": None}, {"limits": {}}, {"limits": {"standard": {}}},
                     {"limits": {"standard": {"weekly": {"used": 100}}}},
                     {"limits": {"standard": {"weekly": bucket(1)}, "core": "x"}}, []):
            with self.subTest(data=data), self.assertRaises(ValueError):
                m.validate_limits(data)

    def test_run_keeps_settings_when_api_changes(self):
        base = Base("setUp")
        base.setUp()
        try:
            cfg = base.example()
            before = open(base.settings_path).read()
            with mock.patch.object(m.urllib.request, "urlopen") as op, \
                    mock.patch.object(m, "api_key", return_value="k"), mock.patch.object(m, "ECHO", False):
                op.return_value.__enter__.return_value = io.BytesIO(b'{"limits": {}}')
                with self.assertRaises(m.LimitsError):
                    m.run(cfg)
            self.assertEqual(open(base.settings_path).read(), before)
        finally:
            base.doCleanups()


if __name__ == "__main__":
    unittest.main()
