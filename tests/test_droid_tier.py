import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("droid_tier", os.path.join(HERE, "..", "droid-tier"))
_spec = importlib.util.spec_from_loader("droid_tier", _loader)
dt_mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(dt_mod)

NOW = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.timezone.utc)
FUTURE = "2026-10-05T04:35:21.628Z"
PAST = "2026-09-01T00:00:00Z"

OC_URL = "https://opencode.ai/zen/go/v1"
SETTINGS = {
    "trustedFolders": {"/home/x": {}},
    "sessionDefaultSettings": {"model": "old", "reasoningEffort": "low", "autonomyMode": "auto-high"},
    "customModels": [
        {"model": "glm-5.3-flash", "id": "custom:OC-GLM-5.3-Flash-7", "baseUrl": OC_URL + "/", "apiKey": "segredo"},
        {"model": "glm-5.3", "id": "custom:OC-GLM-5.3-6", "baseUrl": OC_URL, "apiKey": "segredo"},
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
        with open(self.settings_path, "w") as f:
            json.dump(SETTINGS, f)

    def config(self, text):
        path = os.path.join(self.dir.name, "config.toml")
        with open(path, "w") as f:
            # chaves de topo precisam vir antes de qualquer tabela no TOML
            f.write(f'settings = "{self.settings_path.replace(os.sep, "/")}"\n' + text)
        return dt_mod.load_config(path)

    def example(self):
        return self.config(dt_mod.EXAMPLE_CONFIG)


class ConfigTest(Base):
    def test_example_loads_and_resolves(self):
        cfg = self.example()
        self.assertEqual([t["name"] for t in cfg["tiers"]], ["standard", "droid", "oc"])
        r = dt_mod.resolve_all(cfg, SETTINGS)
        self.assertEqual(r["standard"]["session"], ("gpt-6-luna", "high"))
        self.assertEqual(r["droid"]["spec"], ("glm-5.3", "high"))

    def test_provider_resolves_by_base_url_not_index(self):
        cfg = self.config('''
[providers.oc]
base_url = "https://opencode.ai/zen/go/v1"
[[tier]]
name = "oc"
provider = "oc"
session = "glm-5.3-flash@high"
spec = "glm-5.3"
''')
        r = dt_mod.resolve_all(cfg, SETTINGS)["oc"]
        self.assertEqual(r["session"], ("custom:OC-GLM-5.3-Flash-7", "high"))
        # mesmo nome de modelo na Z.AI nao pode ser confundido
        self.assertEqual(r["spec"], ("custom:OC-GLM-5.3-6", None))

    def test_missing_custom_model_is_error(self):
        cfg = self.config('''
[providers.oc]
base_url = "https://opencode.ai/zen/go/v1"
[[tier]]
name = "oc"
provider = "oc"
validator = "kimi-k3@high"
''')
        with self.assertRaisesRegex(dt_mod.ConfigError, "kimi-k3"):
            dt_mod.resolve_all(cfg, SETTINGS)

    def test_validation_errors(self):
        cases = {
            'x = 1': "nenhum",
            '[[tier]]\nsession = "a"': "sem name",
            '[[tier]]\nname = "a"\nsession = "a"\n[[tier]]\nname = "a"\nsession = "b"': "repetido",
            '[[tier]]\nname = "a"\npool = "gold"\nsession = "a"': "pool deve ser",
            '[[tier]]\nname = "a"\nprovider = "nope"\nsession = "a"': "nao esta em",
            '[[tier]]\nname = "a"\nsesion = "a"': "desconhecidos",
            '[[tier]]\nname = "a"': "nenhum papel",
        }
        for text, msg in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(dt_mod.ConfigError, msg):
                self.config(text)


class PickTest(Base):
    def pick(self, limits):
        return dt_mod.pick_tier(self.example(), limits, NOW)[0]

    def test_all_free(self):
        self.assertEqual(self.pick({"standard": {"weekly": bucket(10)}, "core": {"weekly": bucket(0)}}), "standard")

    def test_standard_over_threshold(self):
        self.assertEqual(self.pick({"standard": {"fiveHour": bucket(96)}, "core": {"weekly": bucket(5)}}), "droid")

    def test_both_exhausted(self):
        self.assertEqual(self.pick({"standard": {"monthly": bucket(100)}, "core": {"weekly": bucket(99)}}), "oc")

    def test_expired_window_ignored(self):
        self.assertEqual(self.pick({"standard": {"fiveHour": bucket(100, PAST)}}), "standard")

    def test_missing_core_counts_as_free(self):
        self.assertEqual(self.pick({"standard": {"monthly": bucket(100)}}), "droid")

    def test_no_fallback_stays_on_last(self):
        cfg = self.config('[[tier]]\nname = "s"\npool = "standard"\nsession = "a"\n'
                          '[[tier]]\nname = "c"\npool = "core"\nsession = "b"')
        limits = {"standard": {"weekly": bucket(100)}, "core": {"weekly": bucket(100)}}
        self.assertEqual(dt_mod.pick_tier(cfg, limits, NOW)[0], "c")


class ApplyTest(Base):
    def test_apply_writes_only_roles_and_keeps_rest(self):
        cfg = self.example()
        s = dt_mod.load_settings(self.settings_path)
        resolved = dt_mod.resolve_all(cfg, s)
        self.assertIsNone(dt_mod.current_tier(s, resolved))
        self.assertTrue(dt_mod.apply(self.settings_path, s, resolved["oc"]))

        after = dt_mod.load_settings(self.settings_path)
        self.assertEqual(dt_mod.current_tier(after, resolved), "oc")
        self.assertEqual(after["sessionDefaultSettings"]["model"], "custom:OC-GLM-5.3-Flash-7")
        self.assertEqual(after["sessionDefaultSettings"]["autonomyMode"], "auto-high")
        self.assertEqual(after["trustedFolders"], SETTINGS["trustedFolders"])
        self.assertEqual(after["customModels"], SETTINGS["customModels"])
        self.assertEqual(after["missionOrchestratorModel"], "custom:OC-GLM-5.3-6")
        if os.name == "posix":
            self.assertEqual(os.stat(self.settings_path).st_mode & 0o777, 0o600)

    def test_apply_is_noop_when_already_there(self):
        cfg = self.example()
        s = dt_mod.load_settings(self.settings_path)
        resolved = dt_mod.resolve_all(cfg, s)
        dt_mod.apply(self.settings_path, s, resolved["droid"])
        s = dt_mod.load_settings(self.settings_path)
        self.assertFalse(dt_mod.apply(self.settings_path, s, resolved["droid"]))


if __name__ == "__main__":
    unittest.main()
