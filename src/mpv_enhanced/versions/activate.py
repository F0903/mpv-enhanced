"""Making an installed version the one ``mpv`` runs, and rolling back."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

from ..errors import InstallerError
from ..platforms import PlatformInfo
from .layout import Layout
from .posix import _config_link, _is_ours, link_version
from .windows import set_start_menu_shortcut, set_user_path


def is_in_use(layout: Layout) -> bool:
    """Whether ``mpve use`` has already pointed this system at an installed version."""
    if os.name != "nt":
        return _is_ours(_config_link(Path.home()), layout)
    found = shutil.which("mpv")
    return found is not None and Path(found).is_relative_to(layout.live)


def use(
    layout: Layout,
    version: str | None,
    platform: PlatformInfo,
    *,
    add_to_path: Callable[[list[Path], Path], None] = set_user_path,
    add_shortcut: Callable[[Path, Path], None] = set_start_menu_shortcut,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Activate an installed version and make it the one ``mpv`` and ``mpve`` run."""
    version = version or layout.active()
    if version is None:
        raise InstallerError("nothing is installed yet; run mpve install first")
    if version not in layout.versions():
        installed = ", ".join(layout.versions()) or "none"
        raise InstallerError(f"{version} is not installed (installed: {installed})")
    home = home or Path.home()
    messages = [f"Now using {version}"]
    if platform.system == "windows":
        directory = layout.app(version)
        add_to_path(layout.path_entries(version), layout.live)
        add_shortcut(layout.player(version, platform), layout.live)
        messages.append(
            f"{directory} is on your PATH. Open a new terminal to use mpv and mpve."
        )
        messages.append("MPV Enhanced is in the Start menu.")
    else:
        link_version(layout, version, platform, home)
        directory = home / ".local" / "bin"
        if str(directory) not in os.environ.get("PATH", "").split(os.pathsep):
            names = "mpve" if platform.system == "linux" else "mpv and mpve"
            messages.append(f"Add {directory} to your PATH to use {names}.")
    layout.activate(version)
    existing = which("mpv")
    # On Linux the system mpv is the one this configures.
    if (
        existing
        and platform.system != "linux"
        and Path(existing).parent != directory
        and not Path(existing).is_relative_to(layout.live)
    ):
        messages.append(f"Note: {existing} is also on your PATH and may run instead.")
    return messages


def rollback(layout: Layout) -> str:
    """Switch to the previous installed version and return it."""
    previous = layout.previous()
    if previous is None:
        raise InstallerError("there is no previous version to roll back to")
    layout.activate(previous)
    return previous
