import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from mpv_enhanced.cli.choices import choose_methods, choose_optional
from mpv_enhanced.cli.install import _setup_answers
from mpv_enhanced.errors import InstallerError
from mpv_enhanced.manifest import Manifest
from mpv_enhanced.platforms import PlatformInfo
from mpv_enhanced.versions.layout import Layout
from tests.support import Terminal


class OptionalScreenTests(unittest.TestCase):
    @staticmethod
    def component(description: str, system: str) -> dict[str, object]:
        return {
            "kind": "download",
            "optional": True,
            "description": description,
            "url": "https://example.invalid/file",
            "sha256": "0" * 64,
            "os": {system: {"arch": ["x86_64"]}},
        }

    def choose(self, typed: str) -> tuple[tuple[str, ...], str]:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "first": self.component("The first one", "windows"),
                    "linux-only": self.component("Not here", "linux"),
                    "second": self.component("The second one", "windows"),
                },
                "profiles": {"core": {"components": []}},
            },
        )
        output = StringIO()
        chosen = choose_optional(
            SimpleNamespace(
                optional=None, install_dir=None, yes=False, command="install"
            ),
            manifest,
            PlatformInfo("windows", "x86_64"),
            Layout(Path("not-installed")),
            Terminal(typed),
            output,
        )
        return chosen, output.getvalue()

    def test_lists_each_component_and_marks_the_unavailable_ones(self) -> None:
        chosen, output = self.choose("\n")
        self.assertEqual(chosen, ())
        self.assertIn("  1. [ ] first — The first one", output)
        self.assertIn("  2. [ ] second — The second one", output)
        self.assertIn("  -  linux-only — unavailable: not on windows-x86_64", output)

    def test_several_can_be_picked_and_keep_manifest_order(self) -> None:
        self.assertEqual(self.choose("2, 1\n")[0], ("first", "second"))

    def test_rejects_a_number_that_is_not_listed(self) -> None:
        for typed in ("3\n", "0\n", "first\n"):
            with self.assertRaisesRegex(InstallerError, "invalid component selection"):
                self.choose(typed)


class MethodsScreenTests(unittest.TestCase):
    @staticmethod
    def method(name: str, *, preselect: bool = True) -> dict[str, object]:
        return {
            "description": f"About {name}",
            "components": [],
            "preselect": preselect,
            "choices": [{"id": name, "title": name, "rank": 1, "settings": {}}],
        }

    def choose(
        self,
        typed: str,
        *,
        unavailable: dict[str, str] | None = None,
        installed: tuple[str, ...] | None = None,
        command: str = "install",
        flag: list[str] | None = None,
        terminal: bool = True,
    ) -> tuple[tuple[str, ...], str]:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {"local": {"kind": "local", "path": "files"}},
                "profiles": {"core": {"components": ["local"]}},
                "methods": {
                    "rtx": self.method("rtx"),
                    "rife": self.method("rife"),
                    "rife-tensorrt": self.method("rife-tensorrt", preselect=False),
                },
            },
        )
        reasons = unavailable or {}
        output = StringIO()
        layout = SimpleNamespace(
            active=lambda: "1.0.0" if installed is not None else None,
            methods=lambda _version: installed or (),
        )
        with patch(
            "mpv_enhanced.cli.choices._unavailable_reason",
            side_effect=lambda _m, _n, _p, methods=(): reasons.get(methods[0]),
        ):
            chosen = choose_methods(
                SimpleNamespace(
                    methods=flag, install_dir=None, yes=False, command=command
                ),
                manifest,
                PlatformInfo("windows", "x86_64"),
                layout,
                "core",
                (Terminal if terminal else StringIO)(typed),
                output,
            )
        return chosen, output.getvalue()

    def test_first_runnable_method_starts_ticked(self) -> None:
        chosen, output = self.choose("\n", unavailable={"rtx": "needs RTX 40"})
        self.assertEqual(chosen, ("rife",))
        self.assertIn("Choose interpolation methods:", output)
        self.assertIn("  -  rtx — unavailable: needs RTX 40", output)
        self.assertIn("  1. [x] rife — About rife", output)
        self.assertIn("  2. [ ] rife-tensorrt — About rife-tensorrt", output)

    def test_tensorrt_is_never_preticked(self) -> None:
        chosen, _ = self.choose("\n", unavailable={"rtx": "no", "rife": "no"})
        self.assertEqual(chosen, ())

    def test_numbers_toggle_methods(self) -> None:
        self.assertEqual(self.choose("1\n")[0], ())
        self.assertEqual(self.choose("2\n")[0], ("rtx", "rife"))

    def test_unattended_fresh_install_gets_no_methods(self) -> None:
        chosen, output = self.choose("", terminal=False)
        self.assertEqual((chosen, output), ((), ""))

    def test_existing_install_keeps_its_methods_without_asking(self) -> None:
        chosen, output = self.choose("", installed=("rife",))
        self.assertEqual((chosen, output), (("rife",), ""))

    def test_profile_command_shows_screen_with_installed_marked(self) -> None:
        chosen, output = self.choose("\n", installed=("rife",), command="profile")
        self.assertEqual(chosen, ("rife",))
        self.assertIn("  1. [ ] rtx — ", output)
        self.assertIn("  2. [x] rife — ", output)

    def test_method_flag_replaces_selection(self) -> None:
        chosen, output = self.choose("", installed=("rife",), flag=["rtx"])
        self.assertEqual((chosen, output), (("rtx",), ""))

    def test_a_method_dropped_from_the_manifest_is_not_kept(self) -> None:
        self.assertEqual(self.choose("", installed=("gone", "rife"))[0], ("rife",))


class SetupScreenTests(unittest.TestCase):
    @staticmethod
    def component(name: str, *, setup: bool = True) -> dict[str, object]:
        table: dict[str, object] = {
            **OptionalScreenTests.component(f"About {name}", "windows")
        }
        if setup:
            table["setup"] = {
                "question": "Server",
                "file": f"{name}.conf",
                "line": "server={answer}\n",
                "matches": "https?://.+",
            }
        return table

    def run_screen(
        self, optional: tuple[str, ...], typed: str
    ) -> tuple[dict[str, str], str]:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "first": self.component("first"),
                    "configured": self.component("configured"),
                    "no-setup": self.component("no-setup", setup=False),
                    "last": self.component("last"),
                },
                "profiles": {"core": {"components": []}},
            },
        )
        output = StringIO()
        with TemporaryDirectory() as temporary:
            (Path(temporary) / "configured.conf").write_text("server=http://kept")
            answers = _setup_answers(
                SimpleNamespace(install_dir=temporary, yes=False),
                manifest,
                optional,
                Layout(Path("not-installed")),
                Terminal(typed),
                output,
            )
        return answers, output.getvalue()

    def test_goes_through_each_component_that_still_needs_input(self) -> None:
        answers, output = self.run_screen(
            ("first", "configured", "no-setup", "last"), "http://first\n\n"
        )
        self.assertEqual(answers, {"first": "http://first"})
        self.assertIn("Set up optional components:", output)
        self.assertIn("[1/2] first — About first", output)
        self.assertIn("[2/2] last — About last", output)
        self.assertNotIn("configured", output)
        self.assertNotIn("no-setup", output)

    def test_does_not_appear_when_nothing_needs_input(self) -> None:
        self.assertEqual(self.run_screen(("configured", "no-setup"), ""), ({}, ""))


if __name__ == "__main__":
    unittest.main()
