"""mpv-enhanced's manager: installs the player and switches between its versions."""

from importlib.metadata import version

# Read from the package metadata, which pyproject.toml sets.
VERSION = version("mpv-enhanced")
