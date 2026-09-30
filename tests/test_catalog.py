import os
import tempfile
import unittest
from unittest import mock

import droid_tier.core as core
from droid_tier import catalog, configedit

HELP = """\
Usage: droid exec [options] [prompt]

Available Models:
  claude-opus-5-5                        Opus 5.5
  gpt-6-sol                              GPT-6 Sol (default)
  glm-5.3-flash                          GLM-5.3-Flash
  deepseek-v4-pro                        DeepSeek V4 Pro [Deprecated]

Model details:
  - Opus 5.5: supports reasoning
"""

MD = {
    "opencode-go": {
        "name": "OpenCode Go", "api": "https://opencode.ai/zen/go/v1", "npm": "@ai-sdk/openai-compatible",
        "models": {
            "glm-5.3": {"name": "GLM-5.3", "limit": {"context": 1000000, "output": 131072},
                        "modalities": {"input": ["text"]}},
            "glm-5.3-flash": {"name": "GLM-5.3-Flash", "limit": {"context": 1000000, "output": 131072},
                              "modalities": {"input": ["text", "image"]}},
            "grok-4.5": {"name": "Grok 4.5", "limit": {"context": 256000}},
        },
    },
    "anthropic": {"name": "Anthropic", "npm": "@ai-sdk/anthropic", "models": {}},
    "semapi": {"name": "Sem API", "npm": "@ai-sdk/openai-compatible", "models": {}},
}

OC = catalog.Provider("opencode-go", "OpenCode Go", "https://opencode.ai/zen/go/v1")


class NativeTest(unittest.TestCase):
    def test_parse_help(self):
        models = catalog.parse_native_models(HELP)
        self.assertEqual([m.id for m in models], ["claude-opus-5-5", "gpt-6-sol", "glm-5.3-flash", "deepseek-v4-pro"])
        self.assertEqual(models[1].name, "GPT-6 Sol")
        self.assertTrue(models[3].deprecated)
        self.assertEqual([m.pool for m in models], ["standard", "standard", "core", "core"])


class CatalogTest(unittest.TestCase):
    def test_providers(self):
        ps = {p.id: p for p in catalog.providers(MD)}
        self.assertEqual(ps["anthropic"].base_url, "https://api.anthropic.com")
        self.assertEqual(ps["anthropic"].kind, "anthropic")
        self.assertEqual(ps["opencode-go"].kind, "generic-chat-completion-api")
        self.assertNotIn("semapi", ps)

    def test_live_list_wins_metadata_from_models_dev(self):
        with mock.patch.object(catalog, "fetch_live_models", return_value=["glm-5.3", "deepseek-flash"]):
            models, live = catalog.list_models(OC, "k", MD)
        self.assertTrue(live)
        self.assertEqual([m.id for m in models], ["deepseek-flash", "glm-5.3"])  # grok-4.5 so no catalogo
        self.assertEqual(models[1].context, 1000000)
        self.assertIsNone(models[0].context)

    def test_falls_back_to_models_dev(self):
        with mock.patch.object(catalog, "fetch_live_models", return_value=None):
            models, live = catalog.list_models(OC, "k", MD)
        self.assertFalse(live)
        self.assertEqual(len(models), 3)


class SyncTest(unittest.TestCase):
    def settings(self):
        return {"customModels": [
            {"model": "glm-5.3", "id": "custom:Z.AI-GLM-5.3-0", "baseUrl": "https://api.z.ai/api/anthropic"},
            {"model": "glm-5.3", "id": "custom:OC-GLM-5.3-0", "index": 1, "baseUrl": OC.base_url, "apiKey": "old"},
            {"model": "mimo-manual", "id": "custom:Mimo-0", "baseUrl": OC.base_url + "/", "apiKey": "old"},
            {"model": "grok-4.5", "id": "custom:OC-Grok-4.5-0", "baseUrl": OC.base_url, "apiKey": "old"},
        ]}

    def test_add_keep_remove(self):
        s = self.settings()
        meta = catalog.md_models(MD, "opencode-go")
        selected = [meta["glm-5.3"], meta["glm-5.3-flash"]]
        added, removed = catalog.sync_custom_models(s, OC, selected, "new", "OC", managed={"glm-5.3", "grok-4.5"})
        self.assertEqual(added, ["glm-5.3-flash"])
        self.assertEqual(removed, ["grok-4.5"])
        by_model = {(e["baseUrl"], e["model"]): e for e in s["customModels"]}
        # existente mantem o id e ganha a key nova
        kept = by_model[(OC.base_url, "glm-5.3")]
        self.assertEqual((kept["id"], kept["apiKey"]), ("custom:OC-GLM-5.3-0", "new"))
        # manual (nao gerenciado) fica, mesmo nao marcado
        self.assertIn((OC.base_url + "/", "mimo-manual"), by_model)
        # outro provider intocado
        self.assertIn(("https://api.z.ai/api/anthropic", "glm-5.3"), by_model)
        new = by_model[(OC.base_url, "glm-5.3-flash")]
        self.assertEqual(new["id"], "custom:OC-GLM-5.3-Flash-0")
        self.assertEqual(new["displayName"], "OC GLM-5.3-Flash")
        self.assertEqual(new["maxContextLimit"], 1000000)
        self.assertFalse(new["noImageSupport"])
        self.assertEqual(new["extraHeaders"], {"x-opencode-session": "droid-tier-glm-5.3-flash"})
        self.assertEqual([e["index"] for e in s["customModels"]], list(range(len(s["customModels"]))))

    def test_duplicate_display_name_gets_next_suffix(self):
        s = {"customModels": [{"model": "x", "id": "custom:OC-GLM-5.3-0", "displayName": "OC GLM-5.3",
                               "baseUrl": "https://outro"}]}
        catalog.sync_custom_models(s, OC, [catalog.md_models(MD, "opencode-go")["glm-5.3"]], "k", "OC", set())
        self.assertEqual(s["customModels"][1]["id"], "custom:OC-GLM-5.3-1")

    def test_in_use(self):
        s = {"sessionDefaultSettings": {"model": "custom:OC-GLM-5.3-0"}}
        home = {"worker": ("custom:OC-Grok-4.5-0", "high")}
        ids = ["custom:OC-GLM-5.3-0", "custom:OC-Grok-4.5-0", "custom:Nada-0"]
        self.assertEqual(catalog.in_use(s, home, ids), ["custom:OC-GLM-5.3-0", "custom:OC-Grok-4.5-0"])


class ConfigEditTest(unittest.TestCase):
    def test_round_trip_keeps_comments_and_validates(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            doc = configedit.load_doc(path)  # novo
            configedit.upsert_provider(doc, "opencode-go", name="OpenCode Go", base_url=OC.base_url,
                                       kind=OC.kind, prefix="OC", models=["glm-5.3", "glm-5.3-flash"])
            configedit.set_fallbacks(doc, [
                {"name": "droid", "pool": "core", "session": "glm-5.3-flash@high"},
                {"name": "oc", "provider": "opencode-go", "session": "glm-5.3-flash@high", "spec": "glm-5.3"},
            ])
            configedit.save_doc(doc, path)
            text = open(path, encoding="utf-8").read()
            self.assertIn("# Troca quando", text)
            cfg = core.load_config(path)
            self.assertEqual([fb["name"] for fb in cfg["fallbacks"]], ["droid", "oc"])
            self.assertEqual(cfg["providers"]["opencode-go"]["models"], ["glm-5.3", "glm-5.3-flash"])

            # reordenar e remover provider mantem o resto
            doc = configedit.load_doc(path)
            fbs = configedit.get_fallbacks(doc)
            configedit.set_fallbacks(doc, list(reversed(fbs)))
            self.assertEqual(configedit.fallback_users(doc, "opencode-go"), ["oc"])
            configedit.save_doc(doc, path)
            self.assertEqual([fb["name"] for fb in core.load_config(path)["fallbacks"]], ["oc", "droid"])


if __name__ == "__main__":
    unittest.main()
