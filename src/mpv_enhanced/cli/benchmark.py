"""The benchmark command, and settling interpolation once an install finishes."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from ..downloads import bar
from ..errors import InstallerError
from ..interpolation.benchmark import (
    Target,
    install_choices,
    run_benchmark,
    suggest_targets,
)
from ..interpolation.settings import (
    NO_METHOD,
    SETTINGS,
    load_settings,
    reconcile_targets,
    set_enabled,
    set_targets,
)
from ..manifest import Manifest
from ..platforms import PlatformInfo
from ..versions.layout import Layout
from .arguments import Arguments

BENCHMARK_BRIEFING = """\
The benchmark tests each interpolation method on this PC to suggest the best
settings for different videos. It takes a few minutes at most, and no window
opens. Close games and other GPU-heavy programs first, or the results will be low."""


def _measure_and_suggest(
    app: Path,
    manifest: Manifest,
    platform: PlatformInfo,
    stdout: TextIO,
    progress_file: Path | None = None,
) -> list[Target]:
    """Run the benchmark and turn its measurements into target overrides."""
    assert manifest.interpolation is not None
    choices = install_choices(app)

    def progress(done: int, total: int, name: str) -> None:
        if progress_file is not None:
            progress_file.write_text(f"{done} {total}\n", encoding="utf-8")
            return
        print(
            f"\r{name[:28].ljust(28)}  {bar(done, total)} {done}/{total}",
            end="" if done < total else "\n",
            flush=True,
            file=stdout,
        )

    capacities = run_benchmark(app, choices, platform, progress=progress)
    # A failed run says nothing about speed, so it must not become "None everywhere".
    if not any(capacities.values()):
        raise InstallerError(
            "the benchmark could not measure any method; close GPU-heavy programs, "
            "including other mpv windows, and try again"
        )
    for item in choices:
        if capacities.get(item["id"]) == 0.0:
            print(f"Could not measure {item['title']}.", file=stdout)
    if any(item["settings"].get("tensorrt") for item in choices):
        print(
            "TensorRT methods were skipped: building their engines takes too long.",
            file=stdout,
        )
    targets = suggest_targets(
        capacities, [item["id"] for item in choices], manifest.interpolation
    )
    if all(target["method"] == NO_METHOD for target in targets):
        print(
            "This PC couldn't keep up with any method, so they're all set to None.",
            file=stdout,
        )
    return targets


def _benchmark_and_save(
    app: Path,
    manifest: Manifest,
    platform: PlatformInfo,
    stdin: TextIO,
    stdout: TextIO,
) -> bool:
    """Run the benchmark and save its suggestions; returns whether it did."""
    if (app / SETTINGS).exists() and load_settings(app) is None:
        raise InstallerError(f"{app / SETTINGS} isn't valid; fix or delete it first")
    if (load_settings(app) or {}).get("targets"):
        print("Replace your existing targets? [y/N]: ", end="", flush=True, file=stdout)
        if stdin.readline().strip().lower() not in ("y", "yes"):
            return False
    set_targets(app, _measure_and_suggest(app, manifest, platform, stdout))
    print("Saved the suggested targets.", file=stdout)
    return True


def _offer_benchmark(
    app: Path,
    manifest: Manifest,
    _methods: tuple[str, ...],
    stdin: TextIO,
    stdout: TextIO,
) -> bool:
    if not install_choices(app):
        return False
    print(BENCHMARK_BRIEFING, file=stdout)
    print("Run the benchmark now? [Y/n]: ", end="", flush=True, file=stdout)
    if stdin.readline().strip().lower() not in ("", "y", "yes"):
        return False
    return _benchmark_and_save(app, manifest, PlatformInfo.detect(), stdin, stdout)


def benchmark_command(
    args: Arguments,
    manifest: Manifest,
    platform: PlatformInfo,
    layout: Layout,
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    app = args.install_dir or layout.active_app()
    if app is None:
        raise InstallerError("nothing is installed yet; run mpve install first")
    if not install_choices(app):
        raise InstallerError(
            "no interpolation methods are installed; run mpve profile to add one"
        )
    if args.suggest_to is not None:
        targets = _measure_and_suggest(
            app, manifest, platform, stdout, args.progress_file
        )
        args.suggest_to.write_text(json.dumps(targets), encoding="utf-8")
        return 0
    if not stdin.isatty():
        raise InstallerError("run mpve benchmark from a terminal")
    print(BENCHMARK_BRIEFING, file=stdout)
    _benchmark_and_save(app, manifest, platform, stdin, stdout)
    return 0


def finish_interpolation(
    args: Arguments,
    app: Path,
    manifest: Manifest,
    methods: tuple[str, ...],
    stdin: TextIO,
    stdout: TextIO,
    offer_benchmark: Callable[..., bool] | None = None,
) -> None:
    """Settle the player's settings once the chosen methods are installed."""
    if not methods:
        return
    reconcile_targets(app, [item.id for item in manifest.method_choices(methods)])
    first_install = not (app / SETTINGS).exists()
    interactive = stdin.isatty() and not args.yes
    accepted = interactive and (first_install or args.command == "profile")
    offer = offer_benchmark or _offer_benchmark
    if accepted:
        try:
            accepted = offer(app, manifest, methods, stdin, stdout)
        except InstallerError as exc:
            # The install itself succeeded, so carry on without suggestions.
            print(f"Skipping the benchmark: {exc}", file=stdout)
            accepted = False
    if not first_install:
        return
    enabled = False
    if interactive:
        print(
            "Start with interpolation on or off? "
            f"[{'ON/off' if accepted else 'on/OFF'}]: ",
            end="",
            flush=True,
            file=stdout,
        )
        answer = stdin.readline().strip().lower()
        enabled = answer == "on" if answer in ("on", "off") else accepted
    set_enabled(app, enabled)
