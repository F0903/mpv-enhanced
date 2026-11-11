"""Pointing a Linux or macOS user's mpv at a version with symlinks."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import InstallerError
from ..platforms import PlatformInfo

if TYPE_CHECKING:
    from .layout import Layout

LOADER_CONFIG = Path("/etc/ld.so.conf.d/mpv-enhanced.conf")


def config_link(home: Path) -> Path:
    """Where mpv looks for its configuration on Linux and macOS."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "mpv"


def is_ours(link: Path, layout: Layout) -> bool:
    return link.is_symlink() and Path(os.readlink(link)).is_relative_to(layout.root)


def _replace_link(link: Path, target: Path) -> None:
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}-{uuid.uuid4().hex}")
    temporary.symlink_to(target)
    os.replace(temporary, link)


def _point_loader_at(folder: Path, run: Callable[..., Any]) -> None:
    """Let mpv find libvsscript.so, which it loads by bare name."""
    try:
        if LOADER_CONFIG.read_text("utf-8") == f"{folder}\n":
            return
    except FileNotFoundError:
        pass
    script = 'printf "%s\\n" "$1" > "$2" && ldconfig'
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    command = [*sudo, "sh", "-c", script, "sh", str(folder), str(LOADER_CONFIG)]
    if run(command, check=False).returncode:
        raise InstallerError(f"could not update {LOADER_CONFIG}; mpv needs it for RIFE")


def link_version(
    layout: Layout,
    version: str,
    platform: PlatformInfo,
    home: Path,
    *,
    only_if_present: bool = False,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    """Point this user's mpv at a version with symlinks, so it needs no launcher.

    With ``only_if_present``, does nothing until ``mpve use`` has set this up.
    """
    app = layout.app(version)
    config = config_link(home)
    if only_if_present and not is_ours(config, layout):
        return
    links = {
        config: app / "portable_config",
        home / ".local" / "bin" / "mpve": layout.root / "mpve.sh",
    }
    if platform.system == "macos":
        links[home / ".local" / "bin" / "mpv"] = (
            app / "mpv.app" / "Contents" / "MacOS" / "mpv"
        )
    for link in links:
        if (link.exists() or link.is_symlink()) and not is_ours(link, layout):
            raise InstallerError(f"{link} already exists; move it away first")
    pattern = "runtime/*/lib/python*/site-packages/vapoursynth/libvsscript.so"
    if library := next(app.glob(pattern), None):
        _point_loader_at(library.parent, run)
    for link, target in links.items():
        _replace_link(link, target)
