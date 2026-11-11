"""The Windows user PATH and Start menu shortcut for the active version."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..errors import InstallerError


def set_user_path(
    directories: list[Path],
    versions: Path,
    *,
    only_if_present: bool = False,
    subkey: str = "Environment",
) -> None:
    """Put folders first on the Windows user PATH, replacing any inside ``versions``.

    With ``only_if_present``, does nothing unless a folder inside ``versions`` is
    already there, so that switching versions never adds mpv to PATH by itself.
    """
    import ctypes
    import winreg

    def normal(entry: Path | str) -> str:
        return str(entry).rstrip("\\").casefold()

    prefix = normal(versions) + "\\"
    wanted = {normal(directory) for directory in directories}
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE
    ) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in value.split(";") if entry]
        if only_if_present and not any(
            normal(entry).startswith(prefix) for entry in entries
        ):
            return
        kept = [
            entry
            for entry in entries
            if normal(entry) not in wanted and not normal(entry).startswith(prefix)
        ]
        result = [*map(str, directories), *kept]
        if result == entries:
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(result))
    # Tells Explorer, so that terminals opened from now on see the new PATH.
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 2, 5000, None
    )


def set_start_menu_shortcut(
    player: Path,
    versions: Path,
    *,
    only_if_present: bool = False,
    shortcut: Path | None = None,
) -> None:
    """Point the Start menu's MPV Enhanced shortcut at ``player``.

    With ``only_if_present``, does nothing unless the shortcut already opens a
    version inside ``versions``.
    """
    shortcut = shortcut or (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "MPV Enhanced.lnk"
    )
    if only_if_present:
        try:
            # A shortcut keeps its working folder as UTF-16 text, so ours contain this.
            if str(versions).encode("utf-16-le") not in shortcut.read_bytes():
                return
        except FileNotFoundError:
            return
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MPVE_LINK);"
        "$link.TargetPath = $env:MPVE_PLAYER;"
        "$link.WorkingDirectory = $env:MPVE_FOLDER;"
        "$link.Save()"
    )
    paths = {"MPVE_LINK": shortcut, "MPVE_PLAYER": player, "MPVE_FOLDER": player.parent}
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, **{name: str(path) for name, path in paths.items()}},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise InstallerError(f"could not create {shortcut}: {result.stderr.strip()}")
