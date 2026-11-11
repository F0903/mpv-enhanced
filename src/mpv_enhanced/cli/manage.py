"""The active version: its status, and the menu ``mpve`` shows without a command."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from ..errors import InstallerError
from ..platforms import PlatformInfo
from ..versions.layout import Layout


def _active_app(layout: Layout) -> Path | None:
    active = layout.active()
    return layout.app(active) if active else None


def _status(layout: Layout, platform: PlatformInfo, output: TextIO) -> int:
    active = layout.active()
    if active is None:
        print("Nothing is installed yet. Run mpve to install.", file=output)
        return 0
    print(f"mpv-enhanced {active}", file=output)
    print(f"Profile: {layout.profile(active) or 'unknown'}", file=output)
    print(f"Start with: {layout.player(active, platform)}", file=output)
    previous = layout.previous()
    if previous:
        print(f"Previous version: {previous} (mpve rollback)", file=output)
    return 0


def _manager_menu(
    layout: Layout,
    stdin: TextIO,
    stdout: TextIO,
) -> list[str] | None:
    """Pick a command interactively. Returns None to quit."""
    active = layout.active()
    if active is None or not stdin.isatty():
        return ["install"]
    print(f"mpv-enhanced {active} ({layout.profile(active) or 'unknown'})", file=stdout)
    choices: list[tuple[str, list[str] | None]] = [
        ("Change profile", ["profile"]),
        ("Check for updates", ["update"]),
    ]
    previous = layout.previous()
    if previous:
        choices.append((f"Roll back to {previous}", ["rollback"]))
    choices += [("Check this installation", ["doctor"]), ("Quit", None)]
    for index, (label, _) in enumerate(choices, 1):
        print(f"  {index}. {label}", file=stdout)
    print("Choice: ", end="", flush=True, file=stdout)
    answer = stdin.readline().strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(choices):
        raise InstallerError(f"invalid choice: {answer!r}")
    return choices[int(answer) - 1][1]
