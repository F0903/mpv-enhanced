"""Readers for single manifest values, each naming the field it rejects."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ..errors import ManifestError, UnsupportedPlatformError
from ..platforms import PlatformInfo

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _table(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a TOML table")
    return value


def _string(value: object, label: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{label} must be a non-empty string")
    return value.strip()


def _string_list(
    value: object,
    label: str,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ManifestError(f"{label} must be a string or an array of strings")
    return tuple(value)


def validate_relative_path(value: str, label: str) -> str:
    """Normalize a manifest path, rejecting absolute and traversal paths."""
    if not value or "\x00" in value:
        raise ManifestError(f"{label} must be a non-empty relative path")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    if (
        windows.is_absolute()
        or windows.drive
        or posix.is_absolute()
        or ".." in posix.parts
    ):
        raise ManifestError(f"{label} must stay within the install tree: {value!r}")
    parts = tuple(part for part in posix.parts if part not in ("", "."))
    return "/".join(parts) if parts else "."


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return the first ``major.minor[.patch]`` found in ``text``."""
    match = _VERSION.search(text)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestError(f"{label} must be an integer")
    return value


def _integer_list(value: object, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{label} must list at least one number")
    return tuple(_integer(item, label) for item in value)


def _pattern(value: object, label: str) -> str:
    pattern = _string(value, label)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ManifestError(f"{label} is not a valid pattern: {exc}") from exc
    return pattern


def _expand(text: str, values: Mapping[str, str], label: str) -> str:
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        raise ManifestError(f"{label}: cannot expand {text!r}: {exc!r}") from exc


def _platform_key(system: str, arch: str, label: str) -> str:
    try:
        return PlatformInfo.parse(f"{system}-{arch}").key
    except UnsupportedPlatformError as exc:
        raise ManifestError(f"{label}: {exc}") from exc


def _platforms(data: Mapping[str, Any], label: str) -> tuple[str, ...]:
    """Read ``os.<name>.arch = [...]`` as ``<name>-<arch>`` keys; no ``os`` means any."""
    if data.get("os") is None:
        return ()
    systems = _table(data["os"], f"{label}.os")
    if not systems:
        raise ManifestError(f"{label}.os must list at least one operating system")
    platforms: list[str] = []
    for system, entry in systems.items():
        arch_label = f"{label}.os.{system}.arch"
        architectures = _string_list(
            _table(entry, f"{label}.os.{system}").get("arch"), arch_label
        )
        if not architectures:
            raise ManifestError(f"{arch_label} must list at least one architecture")
        platforms += [_platform_key(system, arch, arch_label) for arch in architectures]
    return tuple(platforms)
