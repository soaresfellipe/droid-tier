import asyncio
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

import droid_tier.core as core
from droid_tier import catalog
from droid_tier.tui import (Confirm, DroidTierApp, FallbackEditScreen, FallbacksScreen, HomeEditScreen, KeyScreen,
                            MainScreen, ModelPickScreen, PickTier, ProviderPickScreen, ProvidersScreen, until)
from textual.widgets import Input, OptionList, Select, SelectionList, Static

from test_catalog import HELP, MD


async def choose(app, pilot, option_id, widget="#menu"):
    ol = app.screen.query_one(widget, OptionList)
    ol.focus()
    ol.highlighted = ol.get_option_index(option_id)
    await pilot.press("enter")
    await pilot.pause()


class UntilTest(unittest.TestCase):
    def test_malformed_or_missing_end_shows_nothing(self):
        now = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(until("not-a-date", now), "")
        self.assertEqual(until(None), "")


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

    def test_pool_fallback_with_typed_model(self):
        async def flow():
            app = DroidTierApp(self.config)
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.pause()
                app.push_screen(FallbackEditScreen(None))
                await app.workers.wait_for_complete()
                await pilot.pause()
                scr = app.screen
                scr.query_one("#name", Input).value = "droid"
                scr.query_one("#source", Select).value = "pool:core"
                await pilot.pause()
                models = scr.query_one("#m-session", Select)
                self.assertIn("glm-5.3-flash", [v for _, v in models._options])
                # claude-opus-5-5 is in the settings, but it's Standard: not offered for the core pool
                self.assertNotIn("claude-opus-5-5", [v for _, v in models._options])
                await pilot.click("#extra")
                await pilot.press(*"deepseek-v4.1-flash", "enter")
                await pilot.pause()
                scr.query_one("#m-session", Select).value = "glm-5.3-flash"
                scr.query_one("#m-validator", Select).value = "deepseek-v4.1-flash"
                scr.query_one("#e-validator", Select).value = "high"
                await pilot.pause()
                # the choice made before typing the extra ID survives the refresh
                self.assertEqual(scr.query_one("#m-session", Select).value, "glm-5.3-flash")
                await pilot.press("ctrl+s")
                await pilot.pause()

        asyncio.run(flow())
        fb = core.load_config(self.config)["fallbacks"][0]
        self.assertEqual((fb["pool"], fb["session"], fb["validator"]),
                         ("core", "glm-5.3-flash", "deepseek-v4.1-flash@high"))

    async def flow(self):
        app = DroidTierApp(self.config)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, MainScreen)
            await choose(app, pilot, "providers")
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
            ol.highlighted = 1  # 0 = custom URL
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
            # filter, check one, clear the filter and check another: the selection survives filtering
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
            await choose(app, pilot, "fallbacks")
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


    def home_config(self):
        # [home] needs a valid config: at least one fallback must exist
        with open(self.config, "a") as f:
            f.write('\n[[fallback]]\nname = "droid"\npool = "core"\nsession = "glm-5.3-flash@high"\n')

    def test_edit_home_defaults(self):
        self.home_config()

        async def flow():
            app = DroidTierApp(self.config)
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.pause()
                await choose(app, pilot, "home")
                self.assertIsInstance(app.screen, HomeEditScreen)
                await app.workers.wait_for_complete()
                await pilot.pause()
                scr = app.screen
                # the roles come prefilled with the settings' defaults
                self.assertEqual(scr.query_one("#m-session", Select).value, "claude-opus-5-5")
                scr.query_one("#source", Select).value = "pool:standard"
                await pilot.pause()
                scr.query_one("#m-spec", Select).value = "claude-opus-5-5"
                scr.query_one("#e-spec", Select).value = "high"
                await pilot.pause()
                await pilot.press("ctrl+s")
                await pilot.pause()
                # the Confirm asks to apply now; answer no: only the config is written
                await pilot.click("#no")
                await pilot.pause()

        asyncio.run(flow())
        home = core.load_config(self.config)["home"]
        self.assertEqual(home["pool"], "standard")
        self.assertEqual(home["session"], "claude-opus-5-5")  # prefilled from the settings
        self.assertEqual(home["spec"], "claude-opus-5-5@high")

    def test_edit_home_and_apply_now(self):
        self.home_config()

        async def flow():
            app = DroidTierApp(self.config)
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.pause()
                app.push_screen(HomeEditScreen())
                await app.workers.wait_for_complete()
                await pilot.pause()
                scr = app.screen
                scr.query_one("#m-validator", Select).value = "glm-5.3-flash"
                await pilot.pause()
                await pilot.press("ctrl+s")
                await pilot.pause()
                await pilot.click("#yes")
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()

        asyncio.run(flow())
        with open(self.settings) as f:
            s = json.load(f)
        self.assertEqual(s["missionModelSettings"]["validationWorkerModel"], "glm-5.3-flash")
        self.assertEqual(s["sessionDefaultSettings"]["model"], "claude-opus-5-5")  # untouched role
        home = core.load_config(self.config)["home"]
        self.assertEqual(home["validator"], "glm-5.3-flash")


class StatusPanelTest(unittest.TestCase):
    FUTURE = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4, hours=6, minutes=30)).isoformat()
    LIMITS = {"standard": {"fiveHour": {"usedPercent": 12, "windowEnd": FUTURE},
                           "weekly": {"usedPercent": 100, "windowEnd": FUTURE}},
              "core": {"weekly": {"usedPercent": 40, "windowEnd": FUTURE}}}

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings = os.path.join(self.dir.name, "settings.json")
        with open(self.settings, "w") as f:
            json.dump({"sessionDefaultSettings": {"model": "claude-opus-5-5", "reasoningEffort": "high"},
                       "customModels": [
                           {"model": m, "id": f"custom:OC-{m}-0", "baseUrl": "https://opencode.ai/zen/go/v1"}
                           for m in ("glm-5.3", "glm-5.3-flash", "deepseek-v4.1-flash")]}, f)
        self.config = os.path.join(self.dir.name, "config.toml")
        with open(self.config, "w", encoding="utf-8") as f:
            f.write(f'settings = "{self.settings.replace(os.sep, "/")}"\n' + core.EXAMPLE_CONFIG)
        state = os.path.join(self.dir.name, "state")
        patches = [mock.patch.object(core, "fetch_limits", return_value=self.LIMITS),
                   mock.patch.object(core, "STATE_DIR", state),
                   mock.patch.object(core, "PIN_FILE", os.path.join(state, "pin")),
                   mock.patch.object(core, "HOME_FILE", os.path.join(state, "home.json")),
                   mock.patch.object(core, "LOG_FILE", os.path.join(state, "log")),
                   mock.patch.object(core, "ECHO", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def model(self):
        with open(self.settings) as f:
            return json.load(f)["sessionDefaultSettings"]["model"]

    def test_panel_pin_and_restore(self):
        async def flow():
            app = DroidTierApp(self.config)
            async with app.run_test(size=(120, 50)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                tier = str(app.screen.query_one("#tier", Static).render())
                self.assertIn("Current tier: home", tier)
                self.assertIn("From the limits: droid", tier)
                self.assertIn("switches to droid", tier)
                limits = str(app.screen.query_one("#limits", Static).render())
                self.assertIn("weekly", limits)
                self.assertIn("100%", limits)
                self.assertIn("resets in 4d 6h", limits)

                await choose(app, pilot, "pin")
                self.assertIsInstance(app.screen, PickTier)
                await choose(app, pilot, "oc", widget="#tiers")
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(self.model(), "custom:OC-glm-5.3-flash-0")
                self.assertEqual(core.read_pin(), "oc")
                self.assertIn("pinned to oc", str(app.screen.query_one("#tier", Static).render()))
                self.assertIsNotNone(app.screen.query_one("#menu", OptionList).get_option("unpin"))

                await choose(app, pilot, "restore")
                self.assertIsInstance(app.screen, Confirm)
                await pilot.click("#yes")
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(self.model(), "claude-opus-5-5")
                self.assertIsNone(core.read_pin())

        asyncio.run(flow())

    def test_until(self):
        now = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(until("2026-09-30T12:12:00Z", now), "12min")
        self.assertEqual(until("2026-09-30T14:13:00Z", now), "2h13")
        self.assertEqual(until("2026-10-04T18:00:00Z", now), "4d 6h")
        self.assertEqual(until("2026-09-30T11:00:00Z", now), "reset")
        self.assertEqual(until(None, now), "")


if __name__ == "__main__":
    unittest.main()
