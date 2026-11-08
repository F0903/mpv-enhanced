import sys
import unittest
from contextlib import chdir
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from mpv_enhanced_installer.cli import NVIDIA_TENSORRT_SLA_URL, main

ROOT = Path(__file__).resolve().parents[1]


class TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


class CliTests(unittest.TestCase):
    def test_list_profiles_accepts_manifest_flags_after_subcommand(self) -> None:
        output = StringIO()
        errors = StringIO()
        code = main(
            [
                "list-profiles",
                "--deps-manifest",
                str(ROOT / "deps.lock.toml"),
                "--profiles-manifest",
                str(ROOT / "profiles.toml"),
                "--platform",
                "windows-x86_64",
            ],
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(code, 0, errors.getvalue())
        self.assertIn("core\t", output.getvalue())
        self.assertIn("rife-tensorrt\t", output.getvalue())

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
                patch("mpv_enhanced_installer.cli.ArtifactCache") as artifact_cache,
                patch("mpv_enhanced_installer.cli.Installer") as installer,
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
            with patch("mpv_enhanced_installer.cli.ArtifactCache") as artifact_cache:
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
                patch("mpv_enhanced_installer.cli.ArtifactCache"),
                patch("mpv_enhanced_installer.cli.Installer") as installer,
            ):
                installer.return_value.install.return_value = self._install_result(
                    target, "rife-tensorrt"
                )
                code = main(
                    self._install_args("rife-tensorrt", target, cache),
                    stdin=TtyStringIO("accept\n"),
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
            with patch("mpv_enhanced_installer.cli.ArtifactCache") as artifact_cache:
                code = main(
                    self._install_args("rife-tensorrt", target, cache),
                    stdin=TtyStringIO("yes\n"),
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
            (target / "mpv-enhanced.cmd").write_text("")
            with (
                patch("mpv_enhanced_installer.cli.ArtifactCache"),
                patch("mpv_enhanced_installer.cli.Installer") as installer,
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
            # RIFE installs must be started through the launcher, not mpv.exe.
            self.assertIn(
                f"Start mpv-enhanced with: {target / 'mpv-enhanced.cmd'}",
                output.getvalue(),
            )

    @staticmethod
    def _install_args(profile: str, target: Path, cache: Path) -> list[str]:
        return [
            "install",
            "--deps-manifest",
            str(ROOT / "deps.lock.toml"),
            "--profiles-manifest",
            str(ROOT / "profiles.toml"),
            "--platform",
            "windows-x86_64",
            "--profile",
            profile,
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
