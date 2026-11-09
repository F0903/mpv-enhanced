"""Reads the interpolation choice the MPV Enhanced menu passes to the filter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path


def parse_user_data(text: str) -> tuple[str, Fraction]:
    """Split ``<choice-id>/<multiplier>``, raising ValueError if it isn't that."""
    choice_id, separator, multiplier_text = text.rpartition("/")
    if not choice_id or not separator:
        raise ValueError(f"not a choice and multiplier: {text!r}")
    multiplier = Fraction(multiplier_text)
    if multiplier <= 0:
        raise ValueError(f"not a usable multiplier: {text!r}")
    return choice_id, multiplier


def choice_settings(info_path: Path, choice_id: str) -> Mapping[str, object]:
    """Return an installed choice's settings, raising KeyError if it isn't installed."""
    info = json.loads(info_path.read_text(encoding="utf-8"))
    for choice in info["choices"]:
        if choice["id"] == choice_id:
            return choice["settings"]
    raise KeyError(choice_id)
