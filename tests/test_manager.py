import hashlib
import os
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced_installer.cli import main
from mpv_enhanced_installer.errors import InstallerError
from mpv_enhanced_installer.manager import (
    Layout,
    add_to_user_path,
    rollback,
    update,
    use,
)
from mpv_enhanced_installer.platforms import PlatformInfo
from mpv_enhanced_installer.receipt import InstallationReceipt
from mpv_enhanced_installer.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = PlatformInfo("windows", "x86_64")


class ManagerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.layout = Layout(Path(temporary.name).resolve())

    def install(self, version: str, profile: str = "core") -> None:
        app = self.layout.app(version)
        app.mkdir(parents=True)
        InstallationReceipt.create(
            release=version, profile=profile, platform=WINDOWS.key, components=()
        ).write(app)


class LayoutTests(ManagerTestCase):
    def test_versions_sort_numerically_and_previous_skips_the_active(self) -> None:
        for version in ("0.9.0", "0.10.0", "0.2.0"):
            self.install(version)
        self.layout.activate("0.10.0")
        self.assertEqual(self.layout.versions(), ["0.10.0", "0.9.0", "0.2.0"])
        self.assertEqual(self.layout.previous(), "0.9.0")
        self.assertEqual(self.layout.profile("0.9.0"), "core")

    def test_finish_install_keeps_two_versions_and_writes_the_launcher(self) -> None:
        for version in ("0.1.0", "0.2.0", "0.3.0"):
            self.install(version)
        self.layout.finish_install("0.3.0", WINDOWS)
        self.assertEqual(self.layout.versions(), ["0.3.0", "0.2.0"])
        self.assertEqual(self.layout.active(), "0.3.0")
        launcher = self.layout.launcher(WINDOWS).read_text(encoding="utf-8")
        self.assertIn(r"live\current", launcher)
        self.assertNotIn("(", launcher)

    def test_rollback_switches_and_needs_something_to_switch_to(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        with self.assertRaisesRegex(InstallerError, "no previous version"):
            rollback(self.layout)
        self.install("0.2.0")
        self.layout.activate("0.2.0")
        self.assertEqual(rollback(self.layout), "0.1.0")
        self.assertEqual(self.layout.active(), "0.1.0")

    def test_store_release_copies_only_release_files(self) -> None:
        source = self.layout.root / "source"
        (source / "src").mkdir(parents=True)
        (source / "src" / "code.py").write_text("x")
        (source / "live" / "0.1.0").mkdir(parents=True)
        (source / ".venv").mkdir()
        self.layout.store_release("0.2.0", source)
        stored = self.layout.release("0.2.0")
        self.assertTrue((stored / "src" / "code.py").is_file())
        self.assertFalse((stored / "live").exists())
        self.assertFalse((stored / ".venv").exists())

    def test_user_files_carry_over_only_between_different_versions(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        self.assertEqual(self.layout.carry_source("0.2.0"), self.layout.app("0.1.0"))
        self.assertIsNone(self.layout.carry_source("0.1.0"))


class UseTests(ManagerTestCase):
    def use(self, version, platform=WINDOWS, **kwargs):
        options = {"add_to_path": lambda _directory: True, "which": lambda _name: None}
        return use(self.layout, version, platform, **{**options, **kwargs})

    def test_activates_the_version_and_adds_windows_shims_to_the_path(self) -> None:
        self.install("0.1.0")
        self.install("0.2.0")
        self.layout.activate("0.2.0")
        added: list[Path] = []
        messages = self.use("0.1.0", add_to_path=lambda d: added.append(d) or True)
        self.assertEqual(self.layout.active(), "0.1.0")
        shims = self.layout.root / "shims"
        self.assertEqual(added, [shims])
        self.assertIn("mpv-enhanced.cmd", (shims / "mpv.cmd").read_text())
        self.assertIn("mpve.cmd", (shims / "mpve.cmd").read_text())
        self.assertIn("Now using 0.1.0", messages)
        self.assertTrue(self.layout.launcher(WINDOWS).is_file())

    def test_defaults_to_the_active_version(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        self.assertIn("Now using 0.1.0", self.use(None))

    def test_rejects_a_version_that_is_not_installed(self) -> None:
        self.install("0.1.0")
        with self.assertRaisesRegex(InstallerError, r"0\.9\.0 is not installed"):
            self.use("0.9.0")
        with self.assertRaisesRegex(InstallerError, "nothing is installed"):
            Layout(self.layout.root / "empty").live.parent.mkdir()
            use(
                Layout(self.layout.root / "empty"),
                None,
                WINDOWS,
                add_to_path=lambda _directory: True,
            )

    def test_warns_about_another_mpv_on_the_path(self) -> None:
        self.install("0.1.0")
        messages = self.use("0.1.0", which=lambda _name: r"C:\other\mpv.exe")
        self.assertTrue(any("also on your PATH" in item for item in messages))

    def test_posix_shims_avoid_calling_themselves(self) -> None:
        self.install("0.1.0")
        linux = PlatformInfo("linux", "x86_64")
        home = self.layout.root / "home"
        self.use("0.1.0", platform=linux, home=home)
        shim = (home / ".local" / "bin" / "mpv").read_text(encoding="utf-8")
        self.assertIn('grep -vxF "$here"', shim)
        self.assertIn("mpv-enhanced", shim)

    def test_posix_shims_never_replace_someone_elses_mpv(self) -> None:
        self.install("0.1.0")
        home = self.layout.root / "home"
        directory = home / ".local" / "bin"
        directory.mkdir(parents=True)
        (directory / "mpv").write_text("#!/bin/sh\nexec /opt/my-mpv\n")
        with self.assertRaisesRegex(InstallerError, "already exists"):
            self.use("0.1.0", platform=PlatformInfo("linux", "x86_64"), home=home)
        self.assertIn("my-mpv", (directory / "mpv").read_text())

    @unittest.skipUnless(os.name == "nt", "uses the Windows registry")
    def test_user_path_is_updated_once_and_keeps_existing_entries(self) -> None:
        import winreg

        subkey = rf"Software\mpve-test-{uuid.uuid4().hex}"
        self.addCleanup(winreg.DeleteKey, winreg.HKEY_CURRENT_USER, subkey)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            winreg.SetValueEx(
                key, "Path", 0, winreg.REG_EXPAND_SZ, r"%HOME%\tools;C:\a"
            )
        directory = Path(r"C:\mpve\shims")
        self.assertTrue(add_to_user_path(directory, subkey))
        self.assertFalse(add_to_user_path(directory, subkey))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            value, kind = winreg.QueryValueEx(key, "Path")
        self.assertEqual(value, r"C:\mpve\shims;%HOME%\tools;C:\a")
        self.assertEqual(kind, winreg.REG_EXPAND_SZ)


class UpdateTests(ManagerTestCase):
    def release_files(self, version: str, digest: str | None = None):
        name = f"mpv-enhanced-v{version}.zip"
        staging = self.layout.root / name
        with zipfile.ZipFile(staging, "w") as archive:
            archive.writestr(f"mpv-enhanced-v{version}/pyproject.toml", "[project]")
        actual = hashlib.sha256(staging.read_bytes()).hexdigest()
        sums = f"{digest or actual}  {name}\n"

        def download(url: str, destination: Path) -> None:
            if url == "zip":
                destination.write_bytes(staging.read_bytes())
            else:
                destination.write_text(sums, encoding="utf-8")

        release = {
            "tag_name": f"v{version}",
            "assets": [
                {"name": name, "browser_download_url": "zip"},
                {"name": "SHA256SUMS", "browser_download_url": "sums"},
            ],
        }
        return release, download

    def run_update(
        self, release, download, returncode: int = 0, activates: bool = True
    ):
        calls: list[list[str]] = []
        version = str(release["tag_name"]).removeprefix("v")

        def run(command, **_kwargs):
            calls.append(command)
            if returncode == 0 and activates:
                self.install(version, "rtx-high")
                self.layout.activate(version)
            return subprocess.CompletedProcess(command, returncode)

        with patch.dict("os.environ", {"UV": "pinned-uv"}):
            result = update(
                self.layout,
                fetch=lambda _repository: release,
                repository=lambda: "owner/repo",
                download=download,
                run=run,
            )
        return result, calls

    def test_newer_release_is_installed_by_its_own_manager(self) -> None:
        self.install("0.1.0", "rtx-high")
        self.layout.activate("0.1.0")
        release, download = self.release_files("0.2.0")
        result, calls = self.run_update(release, download)
        self.assertEqual(result, "0.2.0")
        self.assertTrue((self.layout.release("0.2.0") / "pyproject.toml").is_file())
        self.assertEqual(calls[0][0], "pinned-uv")
        self.assertEqual(
            calls[0][calls[0].index("--project") + 1], str(self.layout.release("0.2.0"))
        )
        self.assertEqual(calls[0][-2:], ["--profile", "rtx-high"])

    def test_same_or_older_release_changes_nothing(self) -> None:
        self.install("0.2.0")
        self.layout.activate("0.2.0")
        for version in ("0.2.0", "0.1.0"):
            release, download = self.release_files(version)
            self.assertEqual(self.run_update(release, download), (None, []))

    def test_bad_checksum_never_reaches_the_live_folder(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        release, download = self.release_files("0.2.0", digest="0" * 64)
        with self.assertRaisesRegex(InstallerError, "failed its checksum"):
            self.run_update(release, download)
        self.assertFalse((self.layout.live / "0.2.0").exists())
        self.assertEqual(self.layout.active(), "0.1.0")
        self.assertEqual(list(self.layout.live.glob(".update-*")), [])

    def test_failed_build_leaves_the_active_version_alone(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        release, download = self.release_files("0.2.0")
        with self.assertRaisesRegex(InstallerError, "0.1.0 is still active"):
            self.run_update(release, download, returncode=1)
        self.assertFalse((self.layout.live / "0.2.0").exists())
        self.assertEqual(self.layout.active(), "0.1.0")

    def test_git_checkouts_refuse_to_update(self) -> None:
        (self.layout.root / ".git").mkdir()
        with self.assertRaisesRegex(InstallerError, "git pull"):
            update(self.layout)


class ManagedCliTests(ManagerTestCase):
    def setUp(self) -> None:
        super().setUp()
        environment = patch.dict("os.environ", {"MPVE_ROOT": str(self.layout.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def run_cli(
        self, *arguments: str, cache: Path | None = None
    ) -> tuple[int, str, str]:
        output = StringIO()
        errors = StringIO()
        code = main(
            [
                arguments[0],
                "--manifest",
                str(ROOT / "manifest.toml"),
                "--platform",
                WINDOWS.key,
                *arguments[1:],
            ],
            stdin=StringIO(),
            stdout=output,
            stderr=errors,
        )
        return code, output.getvalue(), errors.getvalue()

    def test_install_goes_to_the_versioned_folder_and_activates_it(self) -> None:
        def install(_profile, target, **_kwargs):
            Path(target).mkdir(parents=True)
            previous = Path(target).with_name("app.previous")
            previous.mkdir()
            return type(
                "Result",
                (),
                {
                    "receipt": type("R", (), {"release": VERSION, "profile": "core"}),
                    "install_dir": Path(target),
                    "previous_dir": previous,
                },
            )

        with (
            patch("mpv_enhanced_installer.cli.ArtifactCache"),
            patch("mpv_enhanced_installer.cli.Installer") as installer,
        ):
            installer.return_value.install.side_effect = install
            code, output, errors = self.run_cli(
                "install",
                "--profile",
                "core",
                "--cache-dir",
                str(self.layout.root / "cache"),
            )
        self.assertEqual(code, 0, errors)
        call = installer.return_value.install.call_args
        self.assertEqual(call.args[1], self.layout.app(VERSION))
        self.assertEqual(self.layout.active(), VERSION)
        self.assertFalse(self.layout.app(VERSION).with_name("app.previous").exists())
        self.assertIn(
            f"Start mpv-enhanced with: {self.layout.launcher(WINDOWS)}", output
        )

    def test_profile_needs_an_existing_install(self) -> None:
        code, _, errors = self.run_cli("profile", "core")
        self.assertEqual(code, 2)
        self.assertIn("nothing is installed yet", errors)

    def test_status_and_rollback(self) -> None:
        self.install("0.1.0")
        self.install("0.2.0", "rtx-high")
        self.layout.activate("0.2.0")
        code, output, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("mpv-enhanced 0.2.0", output)
        self.assertIn("Profile: rtx-high", output)
        self.assertIn("Previous version: 0.1.0", output)
        code, output, _ = self.run_cli("rollback")
        self.assertEqual(code, 0)
        self.assertIn("Now using 0.1.0", output)


if __name__ == "__main__":
    unittest.main()
