import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from typing import Any

from mpv_enhanced.cli.arguments import Arguments
from mpv_enhanced.cli.benchmark import finish_interpolation
from mpv_enhanced.interpolation.settings import (
    SETTINGS,
    load_settings,
    reconcile_targets,
    save_settings,
    set_enabled,
    set_targets,
)
from mpv_enhanced.manifest import Manifest
from tests.support import ROOT, Terminal, saved_settings


def target(method: str) -> dict[str, object]:
    return {
        "max_width": 1920,
        "max_height": 1080,
        "max_fps": 30,
        "method": method,
        "multiplier": 2,
    }


class SettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Path(temporary.name) / "app"


class SettingsFileTests(SettingsTestCase):
    def test_reconcile_rewrites_targets_of_removed_methods(self) -> None:
        save_settings(
            self.app,
            {
                "targets": [target("old"), target("none"), target("keep")],
                "default_method": "old",
            },
        )
        reconcile_targets(self.app, ["keep", "other"])
        saved = saved_settings(self.app)
        self.assertEqual(
            [item["method"] for item in saved["targets"]], ["keep", "none", "keep"]
        )
        self.assertEqual(saved["default_method"], "keep")

    def test_reconcile_leaves_a_malformed_file_alone(self) -> None:
        path = self.app / SETTINGS
        path.parent.mkdir(parents=True)
        path.write_text("{oops", encoding="utf-8")
        reconcile_targets(self.app, ["keep"])
        self.assertEqual(path.read_text(encoding="utf-8"), "{oops")

    def test_reconcile_leaves_oddly_shaped_targets_alone(self) -> None:
        save_settings(self.app, {"targets": {"not": "a list"}})
        reconcile_targets(self.app, ["keep"])
        self.assertEqual(saved_settings(self.app)["targets"], {"not": "a list"})

    def test_reconcile_with_no_methods_changes_nothing(self) -> None:
        save_settings(self.app, {"targets": [target("old")], "default_method": "old"})
        reconcile_targets(self.app, [])
        self.assertEqual(saved_settings(self.app)["default_method"], "old")

    def test_load_returns_none_for_missing_or_malformed(self) -> None:
        self.assertIsNone(load_settings(self.app))
        path = self.app / SETTINGS
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2]", encoding="utf-8")
        self.assertIsNone(load_settings(self.app))

    def test_set_enabled_creates_the_file_when_missing(self) -> None:
        set_enabled(self.app, True)
        self.assertEqual(load_settings(self.app), {"enabled": True})

    def test_set_targets_keeps_other_settings(self) -> None:
        save_settings(self.app, {"enabled": True, "fps_cutoff": 30})
        set_targets(self.app, [target("a")])
        saved = saved_settings(self.app)
        self.assertEqual(saved["fps_cutoff"], 30)
        self.assertEqual(saved["targets"][0]["method"], "a")

    def test_save_is_valid_json_and_leaves_no_temporary_files(self) -> None:
        save_settings(self.app, {"enabled": False})
        path = self.app / SETTINGS
        self.assertEqual(json.loads(path.read_text("utf-8")), {"enabled": False})
        self.assertEqual([item.name for item in path.parent.iterdir()], [path.name])


class StartStateTests(SettingsTestCase):
    MANIFEST = Manifest.load(ROOT / "manifest.toml")

    def finish(
        self,
        typed: str = "",
        *,
        methods=("rtx",),
        benchmark=False,
        terminal=True,
        **args,
    ) -> str:
        fields: dict[str, Any] = {"yes": False, "command": "install", **args}
        output = StringIO()
        finish_interpolation(
            Arguments(**fields),
            self.app,
            self.MANIFEST,
            methods,
            (Terminal if terminal else StringIO)(typed),
            output,
            offer_benchmark=lambda *_: benchmark,
        )
        return output.getvalue()

    def test_first_install_asks_start_state(self) -> None:
        self.assertIn("Start with interpolation on or off?", self.finish("\n"))

    def test_enter_defaults_to_off_without_benchmark(self) -> None:
        output = self.finish("\n")
        self.assertIn("[on/OFF]", output)
        self.assertFalse(saved_settings(self.app)["enabled"])

    def test_enter_defaults_to_on_after_accepted_benchmark(self) -> None:
        output = self.finish("\n", benchmark=True)
        self.assertIn("[ON/off]", output)
        self.assertTrue(saved_settings(self.app)["enabled"])

    def test_answer_overrides_the_default(self) -> None:
        self.finish("on\n")
        self.assertTrue(saved_settings(self.app)["enabled"])

    def test_unattended_first_install_starts_off_without_asking(self) -> None:
        self.assertEqual(self.finish(terminal=False), "")
        self.assertFalse(saved_settings(self.app)["enabled"])

    def test_yes_starts_off_without_asking(self) -> None:
        self.assertEqual(self.finish(yes=True), "")
        self.assertFalse(saved_settings(self.app)["enabled"])

    def test_updates_do_not_ask_or_change_the_state(self) -> None:
        save_settings(self.app, {"enabled": True})
        self.assertEqual(self.finish(""), "")
        self.assertTrue(saved_settings(self.app)["enabled"])

    def test_no_methods_writes_nothing(self) -> None:
        self.assertEqual(self.finish("", methods=()), "")
        self.assertFalse((self.app / SETTINGS).exists())

    def test_saved_targets_follow_a_changed_selection(self) -> None:
        save_settings(self.app, {"targets": [target("rife-4.25")]})
        self.finish("")
        self.assertEqual(saved_settings(self.app)["targets"][0]["method"], "rtx-high")


if __name__ == "__main__":
    unittest.main()
