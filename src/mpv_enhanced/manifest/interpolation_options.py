"""The ``[interpolation]`` table: the values the player's menu offers."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import ManifestError
from .fields import read_integer, read_integer_list, read_string, read_table


@dataclass(frozen=True, slots=True)
class Resolution:
    label: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class InterpolationOptions:
    """The values the player's menu offers."""

    resolutions: tuple[Resolution, ...]
    frame_rates: tuple[int, ...]
    multipliers: tuple[int, ...]
    fps_cutoffs: tuple[int, ...]
    default_fps_cutoff: int


def parse_options(value: object) -> InterpolationOptions:
    label = "interpolation"
    data = read_table(value, label)
    raw_resolutions = data.get("resolutions")
    if not isinstance(raw_resolutions, list) or not raw_resolutions:
        raise ManifestError(f"{label}.resolutions must list at least one resolution")
    resolutions = []
    for index, item in enumerate(raw_resolutions):
        entry = read_table(item, f"{label}.resolutions[{index}]")
        resolutions.append(
            Resolution(
                label=read_string(
                    entry.get("label"), f"{label}.resolutions[{index}].label"
                ),
                width=read_integer(
                    entry.get("width"), f"{label}.resolutions[{index}].width"
                ),
                height=read_integer(
                    entry.get("height"), f"{label}.resolutions[{index}].height"
                ),
            )
        )
    return InterpolationOptions(
        resolutions=tuple(resolutions),
        frame_rates=read_integer_list(data.get("frame_rates"), f"{label}.frame_rates"),
        multipliers=read_integer_list(data.get("multipliers"), f"{label}.multipliers"),
        fps_cutoffs=read_integer_list(data.get("fps_cutoffs"), f"{label}.fps_cutoffs"),
        default_fps_cutoff=read_integer(
            data.get("default_fps_cutoff"), f"{label}.default_fps_cutoff"
        ),
    )
