"""Fixtures the tests share."""

from io import StringIO
from pathlib import Path
from typing import Any

from mpv_enhanced.interpolation.settings import load_settings
from mpv_enhanced.platforms import PlatformInfo

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = PlatformInfo("windows", "x86_64")
LINUX = PlatformInfo("linux", "x86_64")


def saved_settings(app: Path) -> dict[str, Any]:
    """The player's saved settings, which the test expects to exist."""
    saved = load_settings(app)
    assert saved is not None
    return saved


class Terminal(StringIO):
    """Input that says it's a terminal, so the prompts appear."""

    def isatty(self) -> bool:
        return True
