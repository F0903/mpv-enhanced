import hashlib
import os
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced import VERSION
from mpv_enhanced.cli import main
from mpv_enhanced.errors import InstallerError
from mpv_enhanced.install.receipt import InstallationReceipt, InstalledComponent
from mpv_enhanced.platforms import PlatformInfo
from mpv_enhanced.versions.activate import rollback, use
from mpv_enhanced.versions.layout import Layout
from mpv_enhanced.versions.posix import link_version
from mpv_enhanced.versions.update import update
from mpv_enhanced.versions.windows import set_start_menu_shortcut, set_user_path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = PlatformInfo("windows", "x86_64")
LINUX = PlatformInfo("linux", "x86_64")


class TerminalInput(StringIO):
    def isatty(self) -> bool:
        return True


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

    def test_finish_install_keeps_two_versions_and_writes_no_launcher(self) -> None:
        for version in ("0.1.0", "0.2.0", "0.3.0"):
            self.install(version)
        self.layout.finish_install("0.3.0")
        self.assertEqual(self.layout.versions(), ["0.3.0", "0.2.0"])
        self.assertEqual(self.layout.active(), "0.3.0")
        self.assertEqual(
            sorted(item.name for item in self.layout.root.iterdir()), ["live"]
        )

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
    @contextmanager
    def home(self):
        home = self.layout.root / "home"
        with (
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}),
            patch("pathlib.Path.home", return_value=home),
        ):
            yield home

    def use(self, version, platform=WINDOWS, **kwargs):
        options = {
            "add_to_path": lambda *_: None,
            "add_shortcut": lambda *_: None,
            "which": lambda _name: None,
        }
        return use(self.layout, version, platform, **{**options, **kwargs})

    def test_activates_the_version_and_puts_the_real_mpv_on_the_path(self) -> None:
        self.install("0.1.0")
        self.install("0.2.0")
        self.layout.activate("0.2.0")
        added: list[tuple[list[Path], Path]] = []
        shortcuts: list[tuple[Path, Path]] = []
        messages = self.use(
            "0.1.0",
            add_to_path=lambda *args: added.append(args),
            add_shortcut=lambda *args: shortcuts.append(args),
        )
        self.assertEqual(self.layout.active(), "0.1.0")
        self.assertEqual(
            added, [([self.layout.app("0.1.0"), self.layout.root], self.layout.live)]
        )
        self.assertEqual(
            shortcuts, [(self.layout.app("0.1.0") / "mpv.exe", self.layout.live)]
        )
        self.assertIn("Now using 0.1.0", messages)
        self.assertEqual(
            sorted(item.name for item in self.layout.root.iterdir()), ["live"]
        )

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
                add_to_path=lambda *_: None,
            )

    def test_warns_about_another_mpv_on_the_path(self) -> None:
        self.install("0.1.0")
        messages = self.use("0.1.0", which=lambda _name: r"C:\other\mpv.exe")
        self.assertTrue(any("also on your PATH" in item for item in messages))

    def test_an_older_version_of_this_install_is_not_another_mpv(self) -> None:
        self.install("0.1.0")
        older = str(self.layout.app("0.0.9") / "mpv.exe")
        messages = self.use("0.1.0", which=lambda _name: older)
        self.assertFalse(any("also on your PATH" in item for item in messages))

    @unittest.skipUnless(os.name == "posix", "creates symbolic links")
    def test_linux_links_the_config_and_mpve_but_leaves_mpv_alone(self) -> None:
        self.install("0.1.0")
        with self.home() as home:
            self.use("0.1.0", platform=LINUX, home=home)
        self.assertEqual(
            os.readlink(home / ".config" / "mpv"),
            str(self.layout.app("0.1.0") / "portable_config"),
        )
        self.assertEqual(
            os.readlink(home / ".local" / "bin" / "mpve"),
            str(self.layout.root / "mpve.sh"),
        )
        self.assertFalse((home / ".local" / "bin" / "mpv").is_symlink())
        self.assertEqual(self.layout.active(), "0.1.0")

    @unittest.skipUnless(os.name == "posix", "creates symbolic links")
    def test_macos_also_links_the_downloaded_mpv(self) -> None:
        self.install("0.1.0")
        with self.home() as home:
            self.use("0.1.0", platform=PlatformInfo("macos", "aarch64"), home=home)
        self.assertEqual(
            os.readlink(home / ".local" / "bin" / "mpv"),
            str(self.layout.app("0.1.0") / "mpv.app" / "Contents" / "MacOS" / "mpv"),
        )

    @unittest.skipUnless(os.name == "posix", "creates symbolic links")
    def test_activating_follows_the_links_only_once_they_are_set_up(self) -> None:
        self.install("0.1.0")
        self.install("0.2.0")
        with self.home() as home:
            self.layout.activate("0.2.0")
            self.assertFalse((home / ".config" / "mpv").is_symlink())
            self.use("0.1.0", platform=LINUX, home=home)
            self.layout.activate("0.2.0")
        self.assertEqual(
            os.readlink(home / ".config" / "mpv"),
            str(self.layout.app("0.2.0") / "portable_config"),
        )

    @unittest.skipUnless(os.name == "posix", "creates symbolic links")
    def test_never_replaces_someone_elses_config_and_stops_before_switching(
        self,
    ) -> None:
        self.install("0.1.0")
        with self.home() as home:
            config = home / ".config" / "mpv"
            config.mkdir(parents=True)
            (config / "mpv.conf").write_text("mine")
            with self.assertRaisesRegex(InstallerError, "already exists"):
                self.use("0.1.0", platform=LINUX, home=home)
        self.assertEqual((config / "mpv.conf").read_text(), "mine")
        self.assertFalse((home / ".local" / "bin" / "mpve").exists())
        self.assertIsNone(self.layout.active())

    @unittest.skipUnless(os.name == "posix", "creates symbolic links")
    def test_rife_tells_the_system_loader_and_a_failure_changes_nothing(self) -> None:
        self.install("0.1.0")
        library = (
            self.layout.app("0.1.0")
            / "runtime/venv/lib/python3.12/site-packages/vapoursynth/libvsscript.so"
        )
        library.parent.mkdir(parents=True)
        library.touch()
        commands: list[list[str]] = []

        def run(command, check):
            commands.append(command)
            return subprocess.CompletedProcess(command, len(commands) - 1)

        with (
            self.home() as home,
            patch(
                "mpv_enhanced.versions.posix.LOADER_CONFIG",
                self.layout.root / "loader.conf",
            ),
        ):
            link_version(self.layout, "0.1.0", LINUX, home, run=run)
            self.assertIn(str(library.parent), commands[0])
            self.assertTrue((home / ".config" / "mpv").is_symlink())
            (home / ".config" / "mpv").unlink()
            with self.assertRaisesRegex(InstallerError, "could not update"):
                link_version(self.layout, "0.1.0", LINUX, home, run=run)
            self.assertFalse((home / ".config" / "mpv").is_symlink())

    @unittest.skipUnless(os.name == "nt", "uses the Windows registry")
    def test_user_path_swaps_versions_and_keeps_other_entries(self) -> None:
        import winreg

        subkey = rf"Software\mpve-test-{uuid.uuid4().hex}"
        self.addCleanup(winreg.DeleteKey, winreg.HKEY_CURRENT_USER, subkey)

        def write(value: str) -> None:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)

        def read() -> tuple[str, int]:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                return winreg.QueryValueEx(key, "Path")

        root = Path(r"C:\mpve")
        live = root / "live"
        first = [live / "1.0.0" / "app", root]
        second = [live / "2.0.0" / "app", root]

        write(r"%HOME%\tools;C:\live\x;C:\a")
        set_user_path(second, live, only_if_present=True, subkey=subkey)
        self.assertEqual(read()[0], r"%HOME%\tools;C:\live\x;C:\a")

        expected = r"C:\mpve\live\1.0.0\app;C:\mpve;%HOME%\tools;C:\live\x;C:\a"
        set_user_path(first, live, subkey=subkey)
        set_user_path(first, live, subkey=subkey)
        self.assertEqual(read(), (expected, winreg.REG_EXPAND_SZ))

        set_user_path(second, live, only_if_present=True, subkey=subkey)
        self.assertEqual(read()[0], expected.replace("1.0.0", "2.0.0"))

    @unittest.skipUnless(os.name == "nt", "creates a Windows shortcut")
    def test_start_menu_shortcut_follows_only_versions_of_this_install(self) -> None:
        shortcut = self.layout.root / "Start Menu" / "MPV Enhanced.lnk"
        live = self.layout.live
        first, second = (live / item / "app" / "mpv.exe" for item in ("1.0", "2.0"))
        for player in (first, second):
            player.parent.mkdir(parents=True)
            player.touch()

        def target() -> str:
            script = "(New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK).TargetPath"
            return subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                env={**os.environ, "LINK": str(shortcut)},
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        set_start_menu_shortcut(first, live, only_if_present=True, shortcut=shortcut)
        self.assertFalse(shortcut.exists())
        set_start_menu_shortcut(first, live, shortcut=shortcut)
        self.assertEqual(target(), str(first))
        elsewhere = self.layout.root / "elsewhere"
        set_start_menu_shortcut(
            second, elsewhere, only_if_present=True, shortcut=shortcut
        )
        self.assertEqual(target(), str(first))
        set_start_menu_shortcut(second, live, only_if_present=True, shortcut=shortcut)
        self.assertEqual(target(), str(second))


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
        self, *arguments: str, cache: Path | None = None, stdin: StringIO | None = None
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
            stdin=stdin or StringIO(),
            stdout=output,
            stderr=errors,
        )
        return code, output.getvalue(), errors.getvalue()

    def optional_chosen(
        self, *arguments: str, stdin: StringIO | None = None
    ) -> tuple[dict[str, object], str]:
        """Run an install with mpvfin already active and return the install options."""
        app = self.layout.app("0.1.0")
        app.mkdir(parents=True)
        InstallationReceipt.create(
            release="0.1.0",
            profile="core",
            platform=WINDOWS.key,
            components=(InstalledComponent("mpvfin", "0.1.0", "download"),),
        ).write(app)
        self.layout.activate("0.1.0")

        def install(_profile, target, **_kwargs):
            return type(
                "Result",
                (),
                {
                    "receipt": type("R", (), {"release": VERSION, "profile": "core"}),
                    "install_dir": Path(target),
                    "previous_dir": None,
                },
            )

        with (
            patch("mpv_enhanced.cli.install.ArtifactCache"),
            patch("mpv_enhanced.cli.install.Installer") as installer,
        ):
            installer.return_value.install.side_effect = install
            code, output, errors = self.run_cli(*arguments, stdin=stdin)
        self.assertEqual(code, 0, errors)
        return installer.return_value.install.call_args.kwargs, output

    def test_updates_keep_optional_components_without_asking(self) -> None:
        options, output = self.optional_chosen(
            "install", "--profile", "core", stdin=TerminalInput("")
        )
        self.assertEqual(options["optional"], ("mpvfin",))
        self.assertNotIn("Choose optional components", output)

    def test_changing_profile_shows_the_screen_with_what_is_installed_marked(
        self,
    ) -> None:
        options, output = self.optional_chosen(
            "profile", "core", stdin=TerminalInput("\n1\n")
        )
        self.assertEqual(options["optional"], ())
        self.assertIn("Choose optional components", output)
        self.assertIn("1. [x] mpvfin — ", output)

    def test_with_flag_replaces_what_is_installed(self) -> None:
        options, _ = self.optional_chosen("install", "--profile", "core", "--with", "x")
        self.assertEqual(options["optional"], ("x",))

    def test_a_wrong_server_url_is_asked_again(self) -> None:
        options, output = self.optional_chosen(
            "profile",
            "core",
            stdin=TerminalInput("\n\njellyfin.local\nhttp://jellyfin.local:8096\n"),
        )
        self.assertEqual(options["answers"], {"mpvfin": "http://jellyfin.local:8096"})
        self.assertIn("doesn't look like", output)

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
            patch("mpv_enhanced.cli.install.ArtifactCache"),
            patch("mpv_enhanced.cli.install.Installer") as installer,
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
            f"Start mpv-enhanced with: {self.layout.player(VERSION, WINDOWS)}",
            output,
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
