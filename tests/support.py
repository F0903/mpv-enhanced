"""Fixtures the tests share."""

from io import StringIO
from pathlib import Path

from mpv_enhanced.platforms import PlatformInfo

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = PlatformInfo("windows", "x86_64")
LINUX = PlatformInfo("linux", "x86_64")


class Terminal(StringIO):
    """Input that says it's a terminal, so the prompts appear."""

    def isatty(self) -> bool:
        return True
