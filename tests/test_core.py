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
# Far in the future: pool_hits() ignores windows that already reset, so a stale
# date here would make the limits look free and break quotas/notify tests.
FUTURE = "2099-01-05T04:35:21.628Z"
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


HOME_CONFIG = '''
[[fallback]]
name = "droid"
pool = "core"
session = "glm-5.3-flash@high"
'''


class HomeConfigTest(Base):
    def test_home_loads_and_resolves(self):
        cfg = self.config(HOME_CONFIG + '''
[home]
pool = "core"
session = "glm-5.3@high"
''')
        d = m.Droid(cfg)
        self.assertEqual(d.home_only["session"], ("glm-5.3", "high"))
        self.assertEqual(m.home_pool(cfg, d.home), "core")

    def test_home_without_pool_is_inferred(self):
        # Inference runs on the effective home: [home] overrides every role, so
        # an all-core [home] without a pool is still read as the core pool.
        cfg = self.config(HOME_CONFIG + '''
[home]
session = "glm-5.3@high"
spec = "deepseek-v4.1-flash"
subagent_light = "glm-5.3-flash"
subagent_medium = "glm-5.3-flash"
subagent_heavy = "glm-5.3-flash"
orchestrator = "glm-5.3"
worker = "glm-5.3"
validator = "glm-5.3"
''')
        self.assertEqual(m.home_pool(cfg, m.Droid(cfg).home), "core")

    def test_home_none_pool(self):
        cfg = self.config(HOME_CONFIG + '\n[home]\npool = "none"\nsession = "custom:X"\n')
        self.assertIsNone(m.home_pool(cfg, m.Droid(cfg).home))

    def test_home_validation_errors(self):
        cases = {
            '[[fallback]]\nname = "a"\nsession = "a"\n[home]\nproviderr = "p"': "unknown fields",
            '[[fallback]]\nname = "a"\nsession = "a"\n[home]\npool = "gold"': "pool must be",
            '[[fallback]]\nname = "a"\nsession = "a"\n[home]\nname = "x"': "unknown fields",
            '[[fallback]]\nname = "a"\nsession = "a"\n[home]\npool = "core"': "defines no role",
        }
        for text, msg in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(m.ConfigError, msg):
                self.config(text)

    def test_home_pool_conflicts_with_top_level(self):
        text = 'home_pool = "core"\n[[fallback]]\nname = "a"\nsession = "a"\n[home]\npool = "standard"\nsession = "b"'
        with self.assertRaisesRegex(m.ConfigError, "not both"):
            self.config(text)


class HomeCycleTest(Base):
    """The [home] roles from the config are enforced on top of the snapshot."""

    def test_apply_and_current(self):
        cfg = self.config(HOME_CONFIG + '''
[home]
session = "glm-5.3@high"
validator = "glm-5.3"
''')
        d = m.Droid(cfg)
        self.assertEqual(d.current(), "mixed")  # the settings don't match [home] yet
        self.assertTrue(d.go("home"))
        s = m.load_settings(self.settings_path)
        self.assertEqual(s["sessionDefaultSettings"]["model"], "glm-5.3")
        self.assertEqual(s["sessionDefaultSettings"]["reasoningEffort"], "high")
        self.assertEqual(s["missionModelSettings"]["validationWorkerModel"], "glm-5.3")
        # untouched roles keep their other settings.json keys
        self.assertEqual(s["sessionDefaultSettings"]["autonomyMode"], "auto-high")
        self.assertEqual(m.Droid(cfg).current(), "home")
        # the schedule can now enforce it: applying again is a no-op
        self.assertFalse(m.Droid(cfg).go("home"))

    def test_restores_snapshot_with_home_on_top(self):
        cfg = self.config(HOME_CONFIG)
        d = m.Droid(cfg)
        original = copy.deepcopy(m.load_settings(self.settings_path))
        self.assertTrue(d.go("droid"))
        # The user edits [home] while on the fallback: the config wins.
        cfg = self.config(HOME_CONFIG + '\n[home]\nvalidator = "glm-5.3"\n')
        d = m.Droid(cfg)
        self.assertEqual(d.home["session"], ("claude-sonnet-5-5", "medium"))  # from the snapshot
        self.assertEqual(d.home["validator"], ("glm-5.3", None))  # from [home]
        self.assertTrue(d.go("home"))
        self.assertFalse(os.path.exists(m.HOME_FILE))
        s = m.load_settings(self.settings_path)
        self.assertEqual(s["sessionDefaultSettings"]["model"], "claude-sonnet-5-5")
        self.assertEqual(s["missionModelSettings"]["validationWorkerModel"], "glm-5.3")

    def test_leaving_for_a_fallback_does_not_raise(self):
        # The settings are already on a fallback and no defaults are saved:
        # with [home] the config defines them, so saving the snapshot is safe.
        cfg = self.config(HOME_CONFIG + '\n[home]\nsession = "glm-5.3@high"\n')
        d = m.Droid(cfg)
        m.set_roles(d.s, d.fallbacks["droid"])
        self.write_settings(d.s)
        d = m.Droid(cfg)
        self.assertFalse(d.go("droid"))  # no ConfigError; already on it, just saves the snapshot
        self.assertEqual(m.read_home()["session"], ("glm-5.3", "high"))
        self.assertTrue(d.go("home"))
        self.assertEqual(m.load_settings(self.settings_path)["sessionDefaultSettings"]["model"], "glm-5.3")


class SessionsTest(Base):
    """With sessions = true, recently active sessions follow the tier."""

    def write_session(self, sid, s, age_hours=0):
        sdir = os.path.join(os.path.dirname(self.settings_path), "sessions", "proj")
        os.makedirs(sdir, exist_ok=True)
        path = os.path.join(sdir, f"{sid}.settings.json")
        with open(path, "w") as f:
            json.dump(s, f)
        if age_hours:
            old = os.stat(path).st_mtime - age_hours * 3600
            os.utime(path, (old, old))
        return path

    def read_session(self, sid):
        path = os.path.join(os.path.dirname(self.settings_path), "sessions", "proj", f"{sid}.settings.json")
        with open(path) as f:
            return json.load(f)

    def session(self, model="claude-sonnet-5-5", effort="medium", spec="claude-opus-5-5", seffort="high"):
        return {"model": model, "reasoningEffort": effort, "specModeModel": spec,
                "specModeReasoningEffort": seffort, "toolExecutionModeModelId": model}

    def test_active_sessions_follow_the_switch(self):
        cfg = self.config('sessions = true\n' + m.EXAMPLE_CONFIG)
        recent = self.write_session("r", self.session())
        self.write_session("old", self.session(), age_hours=30)
        self.write_session("bak", self.session())
        os.rename(os.path.join(os.path.dirname(self.settings_path), "sessions", "proj", "bak.settings.json"),
                  os.path.join(os.path.dirname(self.settings_path), "sessions", "proj", "bak.settings.json.bak"))
        # a session the user /model'd to something else stays alone
        self.write_session("manual", self.session(model="kimi-k3", effort="high"))

        d = m.Droid(cfg)
        self.assertTrue(d.go("droid"))

        moved = self.read_session("r")
        self.assertEqual(moved["model"], "glm-5.3-flash")
        self.assertEqual(moved["reasoningEffort"], "high")  # effort followed: medium was the home's
        self.assertEqual(moved["specModeModel"], "glm-5.3")
        self.assertEqual(moved["specModeReasoningEffort"], "high")
        self.assertEqual(moved["toolExecutionModeModelId"], "glm-5.3-flash")
        self.assertEqual(self.read_session("old")["model"], "claude-sonnet-5-5")  # too old
        self.assertEqual(self.read_session("manual")["model"], "kimi-k3")  # user's choice
        with open(os.path.join(os.path.dirname(self.settings_path), "sessions", "proj",
                               "bak.settings.json.bak")) as f:
            self.assertEqual(json.load(f)["model"], "claude-sonnet-5-5")

        # and back when the limits free up
        d = m.Droid(cfg)
        self.assertTrue(d.go("home"))
        self.assertEqual(self.read_session("r")["model"], "claude-sonnet-5-5")

    def test_keeps_a_custom_effort(self):
        cfg = self.config('sessions = true\n' + m.EXAMPLE_CONFIG)
        self.write_session("r", self.session(effort="low"))
        m.Droid(cfg).go("droid")
        moved = self.read_session("r")
        self.assertEqual(moved["model"], "glm-5.3-flash")
        self.assertEqual(moved["reasoningEffort"], "low")  # not the home's medium, so kept

    def test_off_by_default(self):
        cfg = self.example()
        self.write_session("r", self.session())
        self.assertTrue(m.Droid(cfg).go("droid"))
        self.assertEqual(self.read_session("r")["model"], "claude-sonnet-5-5")

    def test_rewrites_even_without_a_tier_change(self):
        # Enforcement: the Droid process may write the session file back from
        # memory, so every run re-applies the tier to active sessions.
        cfg = self.config('sessions = true\n' + m.EXAMPLE_CONFIG)
        d = m.Droid(cfg)
        self.assertTrue(d.go("droid"))
        self.write_session("r", self.session())  # as if Droid wrote it back
        self.assertFalse(m.Droid(cfg).go("droid"))
        self.assertEqual(self.read_session("r")["model"], "glm-5.3-flash")

    def test_sessions_must_be_bool(self):
        with self.assertRaisesRegex(m.ConfigError, "sessions"):
            self.config('sessions = "yes"\n[[fallback]]\nname = "a"\nsession = "a"')




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
