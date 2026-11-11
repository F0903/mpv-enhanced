"""Telling tables and arrays apart in parsed TOML and JSON, where a value is an ``object``."""

from __future__ import annotations

from typing import cast


def as_table(value: object) -> dict[str, object] | None:
    """``value`` if it is a table, else None. TOML and JSON keys are always strings."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def as_list(value: object) -> list[object] | None:
    """``value`` if it is an array, else None."""
    return cast("list[object]", value) if isinstance(value, list) else None
