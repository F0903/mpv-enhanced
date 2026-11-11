import sys
import unittest
from contextlib import chdir
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from mpv_enhanced.cli import main
from mpv_enhanced.cli.choices import _choose_methods, _choose_optional
from mpv_enhanced.cli.install import (
    NVIDIA_SOFTWARE_LICENSE_URL,
    NVIDIA_TENSORRT_SLA_URL,
    _setup_answers,
)
from mpv_enhanced.errors import InstallerError
from mpv_enhanced.manifest import Manifest
from mpv_enhanced.platforms import PlatformInfo
from mpv_enhanced.versions.layout import Layout

ROOT = Path(__file__).resolve().parents[1]


class TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


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
        chosen = _choose_optional(
            SimpleNamespace(
                optional=None, install_dir=None, yes=False, command="install"
            ),
            manifest,
            PlatformInfo("windows", "x86_64"),
            Layout(Path("not-installed")),
            TtyStringIO(typed),
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
            chosen = _choose_methods(
                SimpleNamespace(
                    methods=flag, install_dir=None, yes=False, command=command
                ),
                manifest,
                PlatformInfo("windows", "x86_64"),
                layout,
                "core",
                (TtyStringIO if terminal else StringIO)(typed),
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
                TtyStringIO(typed),
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


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        for target in ("check_gpu", "check_system_program"):
            patcher = patch(f"mpv_enhanced.cli.choices.{target}")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_list_profiles_accepts_manifest_flags_after_subcommand(self) -> None:
        output = StringIO()
        errors = StringIO()
        code = main(
            [
                "list-profiles",
                "--manifest",
                str(ROOT / "manifest.toml"),
                "--platform",
                "windows-x86_64",
            ],
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(code, 0, errors.getvalue())
        self.assertIn("core\t", output.getvalue())
        self.assertIn("rife-tensorrt\tmethod\t", output.getvalue())

    def test_empty_argv_defaults_to_interactive_install_command(self) -> None:
        output = StringIO()
        errors = StringIO()
        with patch.object(sys, "argv", ["mpv-enhanced"]), chdir(ROOT):
            code = main(stdin=StringIO(), stdout=output, stderr=errors)
        self.assertEqual(code, 2)
        self.assertIn("--profile is required", errors.getvalue())
        self.assertNotIn("arguments are required", errors.getvalue())

    def test_tensorrt_noninteractive_requires_explicit_license_flag(self) -> None:
        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            cache = Path(temporary) / "cache"
            with (
                patch("mpv_enhanced.cli.install.ArtifactCache") as artifact_cache,
                patch("mpv_enhanced.cli.install.Installer") as installer,
            ):
                code = main(
                    self._install_args("rife-tensorrt", target, cache),
                    stdin=StringIO(),
                    stdout=output,
                    stderr=errors,
                )

            self.assertEqual(code, 2)
            self.assertIn("--accept-nvidia-license", errors.getvalue())
            self.assertIn(NVIDIA_TENSORRT_SLA_URL, errors.getvalue())
            artifact_cache.assert_not_called()
            installer.assert_not_called()
            self.assertFalse(target.exists())
            self.assertFalse(cache.exists())

    def test_yes_never_implies_nvidia_license_acceptance(self) -> None:
        output = StringIO()
        errors = StringIO()
        user_input = TtyStringIO("accept\n")
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            cache = Path(temporary) / "cache"
            with patch("mpv_enhanced.cli.install.ArtifactCache") as artifact_cache:
                code = main(
                    [*self._install_args("rife-tensorrt", target, cache), "--yes"],
                    stdin=user_input,
                    stdout=output,
                    stderr=errors,
                )

            self.assertEqual(code, 2)
            self.assertIn("--yes does not accept", errors.getvalue())
            self.assertEqual(user_input.tell(), 0)
            artifact_cache.assert_not_called()
            self.assertFalse(target.exists())
            self.assertFalse(cache.exists())

    def test_tensorrt_interactive_acceptance_precedes_install(self) -> None:
        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            cache = Path(temporary) / "cache"
            with (
                patch("mpv_enhanced.cli.install.ArtifactCache"),
                patch("mpv_enhanced.cli.install.Installer") as installer,
            ):
                installer.return_value.install.return_value = self._install_result(
                    target, "rife-tensorrt"
                )
                code = main(
                    self._install_args("rife-tensorrt", target, cache),
                    stdin=TtyStringIO("\naccept\n"),
                    stdout=output,
                    stderr=errors,
                )

            self.assertEqual(code, 0, errors.getvalue())
            self.assertIn(NVIDIA_TENSORRT_SLA_URL, output.getvalue())
            self.assertIn("Type 'accept'", output.getvalue())
            installer.return_value.install.assert_called_once()

    def test_tensorrt_interactive_input_must_be_accept(self) -> None:
        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            cache = Path(temporary) / "cache"
            with patch("mpv_enhanced.cli.install.ArtifactCache") as artifact_cache:
                code = main(
                    self._install_args("rife-tensorrt", target, cache),
                    stdin=TtyStringIO("\nyes\n"),
                    stdout=output,
                    stderr=errors,
                )

            self.assertEqual(code, 2)
            self.assertIn(NVIDIA_TENSORRT_SLA_URL, output.getvalue())
            self.assertIn("was not accepted", errors.getvalue())
            artifact_cache.assert_not_called()
            self.assertFalse(target.exists())
            self.assertFalse(cache.exists())

    def test_tensorrt_explicit_flag_allows_noninteractive_install(self) -> None:
        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            cache = Path(temporary) / "cache"
            target.mkdir()
            (target / "mpv.exe").write_bytes(b"")
            with (
                patch("mpv_enhanced.cli.install.ArtifactCache"),
                patch("mpv_enhanced.cli.install.Installer") as installer,
            ):
                installer.return_value.install.return_value = self._install_result(
                    target, "rife-tensorrt"
                )
                code = main(
                    [
                        *self._install_args("rife-tensorrt", target, cache),
                        "--yes",
                        "--accept-nvidia-license",
                    ],
                    stdin=StringIO(),
                    stdout=output,
                    stderr=errors,
                )

            self.assertEqual(code, 0, errors.getvalue())
            self.assertNotIn(NVIDIA_TENSORRT_SLA_URL, output.getvalue())
            installer.return_value.install.assert_called_once()
            self.assertIn(
                f"Start mpv-enhanced with: {target / 'mpv.exe'}",
                output.getvalue(),
            )

    def test_unsupported_gpu_stops_before_the_licence_prompt(self) -> None:
        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            args = self._install_args(
                "rtx", Path(temporary) / "target", Path(temporary) / "cache"
            )
            with (
                patch(
                    "mpv_enhanced.cli.choices.check_gpu",
                    side_effect=InstallerError("choose a RIFE profile instead"),
                ),
                patch("mpv_enhanced.cli.install.ArtifactCache") as artifact_cache,
            ):
                code = main(args, stdin=TtyStringIO(""), stdout=output, stderr=errors)
        self.assertEqual(code, 2)
        self.assertIn("choose a RIFE profile", errors.getvalue())
        self.assertNotIn("Type 'accept'", output.getvalue())
        artifact_cache.assert_not_called()

    def test_rtx_method_requires_its_own_licence(self) -> None:
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            args = self._install_args(
                "rtx", Path(temporary) / "target", Path(temporary) / "cache"
            )
            with patch("mpv_enhanced.cli.choices.check_gpu"):
                code = main(args, stdin=StringIO(), stdout=StringIO(), stderr=errors)
        self.assertEqual(code, 2)
        self.assertIn(NVIDIA_SOFTWARE_LICENSE_URL, errors.getvalue())

    def test_pressing_enter_at_the_menu_selects_the_default_profile(self) -> None:
        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            cache = Path(temporary) / "cache"
            args = [
                item
                for item in self._install_args("core", target, cache)
                if item not in ("--profile", "core")
            ]
            with (
                patch("mpv_enhanced.cli.choices.check_gpu"),
                patch("mpv_enhanced.cli.install.ArtifactCache"),
                patch("mpv_enhanced.cli.install.Installer") as installer,
            ):
                installer.return_value.install.return_value = self._install_result(
                    target, "core"
                )
                code = main(
                    [*args, "--accept-nvidia-license"],
                    stdin=TtyStringIO("\n"),
                    stdout=output,
                    stderr=errors,
                )
        self.assertEqual(code, 0, errors.getvalue())
        self.assertIn("core (default)", output.getvalue())
        call = installer.return_value.install.call_args
        self.assertEqual(call.args[0], "core")
        self.assertEqual(call.kwargs["methods"], ("rtx",))

    def test_screen_marks_unavailable_methods_and_ticks_the_first_it_can_run(
        self,
    ) -> None:
        def only_older_nvidia(gpu: object) -> str:
            if "RTX 40" in gpu.requirement:  # type: ignore[attr-defined]
                raise InstallerError("needs a newer GPU")
            return "NVIDIA GeForce RTX 3080"

        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            args = [
                item
                for item in self._install_args("core", target, Path(temporary))
                if item not in ("--profile", "core")
            ]
            with (
                patch(
                    "mpv_enhanced.cli.choices.check_gpu",
                    side_effect=only_older_nvidia,
                ),
                patch(
                    "mpv_enhanced.platforms.PlatformInfo.detect",
                    return_value=PlatformInfo("windows", "x86_64"),
                ),
                patch("mpv_enhanced.cli.install.ArtifactCache"),
                patch("mpv_enhanced.cli.install.Installer") as installer,
            ):
                installer.return_value.install.return_value = self._install_result(
                    target, "core"
                )
                code = main(
                    args,
                    stdin=TtyStringIO("\n\n\n"),
                    stdout=output,
                    stderr=errors,
                )
        self.assertEqual(code, 0, errors.getvalue())
        self.assertIn("-  rtx — unavailable: needs a newer GPU", output.getvalue())
        self.assertIn("1. [x] rife — ", output.getvalue())
        self.assertEqual(
            installer.return_value.install.call_args.kwargs["methods"], ("rife",)
        )

    def test_install_inside_wsl_points_at_install_cmd(self) -> None:
        errors = StringIO()
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            cache = Path(temporary) / "cache"
            with (
                patch("mpv_enhanced.cli.install.running_in_wsl", return_value=True),
                patch("mpv_enhanced.cli.install.ArtifactCache"),
                patch("mpv_enhanced.cli.install.Installer") as installer,
            ):
                installer.return_value.install.return_value = self._install_result(
                    target, "core"
                )
                code = main(
                    self._install_args("core", target, cache),
                    stdin=StringIO(),
                    stdout=StringIO(),
                    stderr=errors,
                )

        self.assertEqual(code, 0, errors.getvalue())
        self.assertIn("mpve.cmd", errors.getvalue())

    @staticmethod
    def _install_args(selection: str, target: Path, cache: Path) -> list[str]:
        """Install arguments for ``core`` or for ``core`` with this method."""
        method = [] if selection == "core" else ["--method", selection]
        return [
            "install",
            "--manifest",
            str(ROOT / "manifest.toml"),
            "--platform",
            "windows-x86_64",
            "--profile",
            "core",
            *method,
            "--install-dir",
            str(target),
            "--cache-dir",
            str(cache),
        ]

    @staticmethod
    def _install_result(target: Path, profile: str) -> SimpleNamespace:
        return SimpleNamespace(
            receipt=SimpleNamespace(release="test", profile=profile),
            install_dir=target,
            previous_dir=None,
        )


if __name__ == "__main__":
    unittest.main()
