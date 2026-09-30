import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import droid_tier.core as core
from droid_tier import catalog
from droid_tier.tui import (DroidTierApp, FallbackEditScreen, FallbacksScreen, KeyScreen, MainScreen,
                            ModelPickScreen, ProviderPickScreen, ProvidersScreen)
from textual.widgets import Input, OptionList, Select, SelectionList

from test_catalog import HELP, MD


class TuiFlowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings = os.path.join(self.dir.name, "settings.json")
        with open(self.settings, "w") as f:
            json.dump({"sessionDefaultSettings": {"model": "claude-opus-5-5"}, "customModels": []}, f)
        self.config = os.path.join(self.dir.name, "config.toml")
        with open(self.config, "w") as f:
            f.write(f'settings = "{self.settings.replace(os.sep, "/")}"\nthreshold = 95\n')
        patches = [
            mock.patch.object(catalog, "load_models_dev", return_value=MD),
            mock.patch.object(catalog, "fetch_live_models", return_value=["glm-5.3", "glm-5.3-flash", "kimi-k3"]),
            mock.patch.object(catalog, "native_models", return_value=catalog.parse_native_models(HELP)),
            mock.patch.object(core, "HOME_FILE", os.path.join(self.dir.name, "home.json")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_add_provider_pick_models_and_build_fallback(self):
        asyncio.run(self.flow())

        with open(self.settings) as f:
            s = json.load(f)
        models = {m["model"]: m for m in s["customModels"]}
        self.assertEqual(sorted(models), ["glm-5.3", "glm-5.3-flash"])
        self.assertEqual(models["glm-5.3"]["apiKey"], "oc-key")
        self.assertEqual(models["glm-5.3"]["id"], "custom:OC-GLM-5.3-0")
        self.assertTrue(os.path.exists(self.settings + ".droid-tier.bak"))

        cfg = core.load_config(self.config)
        self.assertEqual(cfg["providers"]["opencode-go"]["models"], ["glm-5.3", "glm-5.3-flash"])
        fb = cfg["fallbacks"][0]
        self.assertEqual((fb["name"], fb["provider"]), ("oc", "opencode-go"))
        self.assertEqual(fb["session"], "glm-5.3-flash@high")
        self.assertEqual(fb["spec"], "glm-5.3")
        resolved = core.resolve_all(cfg, s)["oc"]
        self.assertEqual(resolved["session"], ("custom:OC-GLM-5.3-Flash-0", "high"))

    async def flow(self):
        app = DroidTierApp(self.config)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, MainScreen)
            await pilot.press("enter")  # Providers e modelos
            self.assertIsInstance(app.screen, ProvidersScreen)
            await pilot.press("enter")  # + Adicionar provider
            self.assertIsInstance(app.screen, ProviderPickScreen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.click("#search")
            await pilot.press(*"opencode")
            await pilot.pause()
            ol = app.screen.query_one("#list", OptionList)
            ol.focus()
            ol.highlighted = 1  # 0 = URL personalizada
            await pilot.press("enter")
            self.assertIsInstance(app.screen, KeyScreen)
            app.screen.query_one("#key", Input).value = "oc-key"
            app.screen.query_one("#prefix", Input).value = "OC"
            await pilot.click("#go")
            await pilot.pause()
            self.assertIsInstance(app.screen, ModelPickScreen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            sl = app.screen.query_one("#list", SelectionList)
            self.assertEqual(sl.option_count, 3)
            # filtra, marca um, limpa o filtro e marca outro: a selecao sobrevive ao filtro
            app.screen.query_one("#search", Input).value = "flash"
            await pilot.pause()
            sl.focus()
            await pilot.press("space")
            app.screen.query_one("#search", Input).value = ""
            await pilot.pause()
            sl.focus()
            sl.highlighted = 0  # glm-5.3
            await pilot.press("space")
            await pilot.pause()
            self.assertEqual(app.screen.chosen, {"glm-5.3", "glm-5.3-flash"})
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertIsInstance(app.screen, ProvidersScreen)

            await pilot.press("escape")
            menu = app.screen.query_one("#menu", OptionList)
            menu.focus()
            menu.highlighted = 1
            await pilot.press("enter")  # Fallbacks
            self.assertIsInstance(app.screen, FallbacksScreen)
            await pilot.press("enter")  # + Novo fallback
            self.assertIsInstance(app.screen, FallbackEditScreen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            scr = app.screen
            scr.query_one("#name", Input).value = "oc"
            scr.query_one("#source", Select).value = "provider:opencode-go"
            await pilot.pause()
            scr.query_one("#m-session", Select).value = "glm-5.3-flash"
            scr.query_one("#e-session", Select).value = "high"
            scr.query_one("#m-spec", Select).value = "glm-5.3"
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertIsInstance(app.screen, FallbacksScreen)


if __name__ == "__main__":
    unittest.main()
