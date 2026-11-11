import os
import subprocess
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mpv_enhanced.errors import InstallerError
from mpv_enhanced.platforms import PlatformInfo
from mpv_enhanced.versions.layout import Layout
from mpv_enhanced.versions.posix import link_version
from mpv_enhanced.versions.windows import set_start_menu_shortcut, set_user_path
from tests.support import LINUX, WINDOWS
from tests.versions.support import ManagerTestCase


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
        options: dict[str, Any] = {
            "add_to_path": lambda *_: None,
            "add_shortcut": lambda *_: None,
            "which": lambda _name: None,
        }
        return self.layout.use(version, platform, **{**options, **kwargs})

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
            Layout(self.layout.root / "empty").use(
                None, WINDOWS, add_to_path=lambda *_: None
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
            link_version(
                self.layout.root, self.layout.app("0.1.0"), LINUX, home, run=run
            )
            self.assertIn(str(library.parent), commands[0])
            self.assertTrue((home / ".config" / "mpv").is_symlink())
            (home / ".config" / "mpv").unlink()
            with self.assertRaisesRegex(InstallerError, "could not update"):
                link_version(
                    self.layout.root, self.layout.app("0.1.0"), LINUX, home, run=run
                )
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


if __name__ == "__main__":
    unittest.main()
