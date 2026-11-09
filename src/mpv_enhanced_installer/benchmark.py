"""Measures interpolation methods on this PC and suggests target overrides."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .manifest import InterpolationOptions
from .settings import NO_METHOD

USABLE_MARGIN = 1.1
SUGGESTED_MULTIPLIER = 2
LEVEL_FRAME_RATES = (24, 30)
LEVEL_RESOLUTIONS = ("720p", "1080p", "1440p", "4K")


def _covers(heavier: Mapping[str, Any], lighter: Mapping[str, Any]) -> bool:
    return all(
        heavier[key] >= lighter[key] for key in ("max_width", "max_height", "max_fps")
    )


def _weight(target: Mapping[str, Any]) -> int:
    return target["max_width"] * target["max_height"] * target["max_fps"]


def suggest_targets(
    capacities: Mapping[str, float],
    order: Sequence[str],
    options: InterpolationOptions,
) -> list[dict[str, Any]]:
    """Target overrides from each choice's measured output pixels per second.

    ``order`` lists choice ids best first. Levels the PC can't handle get the
    ``none`` method, so files that large play normally instead of stuttering.
    """
    levels = []
    for resolution in options.resolutions:
        if resolution.label not in LEVEL_RESOLUTIONS:
            continue
        for fps in LEVEL_FRAME_RATES:
            # Measured against 16:9, so the DCI-wide 4K limit isn't over-counted.
            width = resolution.height * 16 // 9
            required = width * resolution.height * fps * SUGGESTED_MULTIPLIER
            method = next(
                (
                    choice
                    for choice in order
                    if capacities.get(choice, 0.0) >= required * USABLE_MARGIN
                ),
                NO_METHOD,
            )
            levels.append(
                {
                    "max_width": resolution.width,
                    "max_height": resolution.height,
                    "max_fps": fps,
                    "method": method,
                    "multiplier": SUGGESTED_MULTIPLIER,
                }
            )

    levels.sort(key=_weight)
    merged: list[dict[str, Any]] = []
    for index, level in enumerate(levels):
        # A level is redundant if the next one up that covers it does the same.
        next_covering = next(
            (t for t in levels[index + 1 :] if _covers(t, level)), None
        )
        if next_covering is None or next_covering["method"] != level["method"]:
            merged.append(level)
    return sorted(merged, key=_weight, reverse=True)
