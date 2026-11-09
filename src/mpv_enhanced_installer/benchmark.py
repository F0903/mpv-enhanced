"""Measures interpolation methods on this PC and suggests target overrides."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from .installer import INSTALL_INFO
from .manifest import InterpolationOptions
from .platforms import PlatformInfo
from .settings import NO_METHOD

USABLE_MARGIN = 1.1
SUGGESTED_MULTIPLIER = 2
LEVEL_FRAME_RATES = (24, 30)
LEVEL_RESOLUTIONS = ("720p", "1080p", "1440p", "4K")
WARMUP_FRAMES = 48
MEASURED_FRAMES = 96
MEASURE_TIMEOUT = 90
_RESULT = re.compile(r"MPVE_BENCH frames=(\d+) seconds=([\d.]+)")

Choice = Mapping[str, Any]


def install_choices(app: Path) -> list[dict[str, Any]]:
    """The installed interpolation choices, best first."""
    try:
        info = json.loads((app / INSTALL_INFO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return info.get("choices", [])


def mpv_command(
    app: Path,
    choice_id: str,
    platform: PlatformInfo,
    width: int,
    height: int,
    frames: int,
    probe: Path,
) -> list[str]:
    """A headless run. The filter path is relative to the working directory,
    because mpv's filter syntax can't hold an arbitrary absolute path."""
    return [
        "mpv" if platform.system == "linux" else str(app / "mpv.exe"),
        "--no-config",
        "--vo=null",
        "--ao=null",
        "--untimed",
        "--msg-level=all=error,benchmark_probe=info",
        f"--frames={WARMUP_FRAMES + frames}",
        f"--script={probe}",
        f"--vf=vapoursynth=interpolation.vpy:user-data={choice_id}/{SUGGESTED_MULTIPLIER}",
        f"av://lavfi:testsrc2=s={width}x{height}:r=24,format=yuv420p10le",
    ]


def _environment(app: Path, platform: PlatformInfo) -> dict[str, str]:
    environment = dict(os.environ)
    if platform.system == "linux":
        # The system library path may point at another version during an update.
        library = next(
            app.glob("runtime/*/lib/python*/site-packages/vapoursynth/libvsscript.so"),
            None,
        )
        if library:
            environment["VSSCRIPT_PATH"] = str(library)
    return environment


def measure_choice(
    app: Path,
    choice: Choice,
    platform: PlatformInfo,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> float:
    """Output pixels per second this choice manages, or 0.0 if it couldn't run."""
    is_rtx = choice["settings"]["interpolator"] == "rtx"
    width, height = (3840, 2160) if is_rtx else (1920, 1080)
    probe_file = files(__package__) / "benchmark_probe.lua"
    try:
        with as_file(probe_file) as probe:
            completed = run(
                mpv_command(
                    app, choice["id"], platform, width, height, MEASURED_FRAMES, probe
                ),
                cwd=app / "portable_config",
                env=_environment(app, platform),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=MEASURE_TIMEOUT,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    match = _RESULT.search(completed.stdout or "")
    if completed.returncode != 0 or match is None:
        return 0.0
    frames, seconds = int(match[1]), float(match[2])
    return width * height * frames / seconds if seconds > 0 else 0.0


def run_benchmark(
    app: Path,
    choices: Sequence[Choice],
    platform: PlatformInfo,
    *,
    measure: Callable[[Path, Choice, PlatformInfo], float] = measure_choice,
    progress: Callable[[int, int, str], None] = lambda _done, _total, _name: None,
) -> dict[str, float]:
    """Measure each choice. TensorRT is skipped: its engines take minutes to build."""
    measurable = [item for item in choices if not item["settings"].get("tensorrt")]
    capacities: dict[str, float] = {}
    for index, choice in enumerate(measurable):
        progress(index, len(measurable), choice["title"])
        capacities[choice["id"]] = measure(app, choice, platform)
    progress(len(measurable), len(measurable), "Done")
    return capacities


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
    merged.sort(key=_weight, reverse=True)
    if merged[0]["method"] != NO_METHOD:
        # Nothing above the measured levels was tested, so leave such files alone.
        largest = options.resolutions[-1]
        merged.insert(
            0,
            {
                "max_width": largest.width,
                "max_height": largest.height,
                "max_fps": max(options.frame_rates),
                "method": NO_METHOD,
                "multiplier": SUGGESTED_MULTIPLIER,
            },
        )
    return merged
