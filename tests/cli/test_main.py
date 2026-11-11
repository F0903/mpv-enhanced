import sys
import unittest
from contextlib import chdir
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from mpv_enhanced.cli import main
from mpv_enhanced.cli.install import (
    NVIDIA_SOFTWARE_LICENSE_URL,
    NVIDIA_TENSORRT_SLA_URL,
)
from mpv_enhanced.errors import InstallerError
from mpv_enhanced.platforms import PlatformInfo
from tests.support import ROOT, Terminal


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
        user_input = Terminal("accept\n")
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
                    stdin=Terminal("\naccept\n"),
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
                    stdin=Terminal("\nyes\n"),
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
                code = main(args, stdin=Terminal(""), stdout=output, stderr=errors)
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
                    stdin=Terminal("\n"),
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
                    stdin=Terminal("\n\n\n"),
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
