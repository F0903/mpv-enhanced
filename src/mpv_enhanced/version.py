"""The installer's version, read from package metadata (set in pyproject.toml)."""

from importlib.metadata import version

VERSION = version("mpv-enhanced")
