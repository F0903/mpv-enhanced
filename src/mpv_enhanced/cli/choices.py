"""What this machine can install, and the screens for choosing it."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from ..errors import InstallerError
from ..install.system import check_gpu, check_system_program
from ..manifest import Manifest
from ..platforms import PlatformInfo
from ..versions.layout import Layout


@dataclass(frozen=True, slots=True)
class TickRow:
    name: str
    description: str
    unavailable: str | None = None


def _unavailable_reason(
    manifest: Manifest,
    name: str,
    platform: PlatformInfo,
    methods: tuple[str, ...] = (),
) -> str | None:
    """Explain why a profile or method can't be installed on this machine, if so."""
    try:
        profile = manifest.resolve_profile(name, platform, methods=methods)
        # Hardware and programs can only be probed on the real host platform.
        if platform == PlatformInfo.detect():
            for component in profile.components:
                if component.gpu is not None:
                    check_gpu(component.gpu)
                if component.system is not None:
                    check_system_program(component.system)
    except InstallerError as exc:
        return str(exc)
    return None


def _require_gpus(
    manifest: Manifest,
    profile_name: str,
    platform: PlatformInfo,
    methods: tuple[str, ...],
) -> None:
    """Stop if a chosen method needs a graphics card this machine lacks."""
    for component in manifest.resolve_profile(
        profile_name, platform, methods=methods
    ).components:
        if component.gpu is not None:
            check_gpu(component.gpu)


def _list_profiles(manifest: Manifest, platform: PlatformInfo, output: TextIO) -> int:
    for name, profile in manifest.profiles.items():
        reason = _unavailable_reason(manifest, name, platform)
        if reason is None:
            count = len(manifest.resolve_profile(name, platform).components)
            status = f"{count} components"
        else:
            status = f"unavailable: {reason}"
        print(f"{name}\t{status}\t{profile.description}", file=output)
    for method in manifest.available_methods(platform):
        print(f"{method.name}\tmethod\t{method.description}", file=output)
    for component in manifest.optional_components(platform):
        print(f"{component.name}\toptional\t{component.description}", file=output)
    return 0


def _choose_optional(
    args: argparse.Namespace,
    manifest: Manifest,
    platform: PlatformInfo,
    layout: Layout,
    stdin: TextIO,
    stdout: TextIO,
) -> tuple[str, ...]:
    """Pick optional components; updates keep what the active version has."""
    if args.optional is not None:
        return tuple(args.optional)
    active = None if args.install_dir else layout.active()
    installed = layout.components(active) if active else ()
    names = [item.name for item in manifest.optional_components(platform)]
    chosen = {name for name in names if name in installed}
    if _should_ask(args, active, stdin):
        rows = [
            TickRow(
                item.name,
                item.description,
                None if item.name in names else f"not on {platform.key}",
            )
            for item in manifest.components.values()
            if item.optional
        ]
        chosen = _tick_screen(
            "Choose optional components:", rows, chosen, stdin, stdout
        )
    return tuple(name for name in names if name in chosen)


def _choose_methods(
    args: argparse.Namespace,
    manifest: Manifest,
    platform: PlatformInfo,
    layout: Layout,
    profile_name: str,
    stdin: TextIO,
    stdout: TextIO,
) -> tuple[str, ...]:
    """Pick interpolation methods; updates keep what the active version has."""
    if args.methods is not None:
        return tuple(args.methods)
    active = None if args.install_dir else layout.active()
    installed = layout.methods(active) if active else ()
    if not _should_ask(args, active, stdin):
        return tuple(name for name in manifest.methods if name in installed)
    rows = [
        TickRow(
            item.name,
            item.description,
            _unavailable_reason(manifest, profile_name, platform, methods=(item.name,)),
        )
        for item in manifest.methods.values()
    ]
    runnable = [row.name for row in rows if row.unavailable is None]
    chosen = {name for name in runnable if name in installed}
    if not active:
        first = next((n for n in runnable if manifest.methods[n].preselect), None)
        chosen = {first} if first else set()
    chosen = _tick_screen("Choose interpolation methods:", rows, chosen, stdin, stdout)
    return tuple(name for name in manifest.methods if name in chosen)


def _should_ask(args: argparse.Namespace, active: str | None, stdin: TextIO) -> bool:
    """Screens appear on a first install and for ``mpve profile``, never on updates."""
    return stdin.isatty() and not args.yes and (not active or args.command == "profile")


def _tick_screen(
    title: str,
    rows: Sequence[TickRow],
    chosen: set[str],
    stdin: TextIO,
    stdout: TextIO,
) -> set[str]:
    """Show numbered tick boxes; typed numbers toggle, Enter continues."""
    names = [row.name for row in rows if row.unavailable is None]
    if not names:
        return chosen
    print(title, file=stdout)
    for row in rows:
        if row.unavailable is not None:
            print(f"  -  {row.name} — unavailable: {row.unavailable}", file=stdout)
            continue
        mark = "x" if row.name in chosen else " "
        print(
            f"  {names.index(row.name) + 1}. [{mark}] {row.name} — {row.description}",
            file=stdout,
        )
    print(
        "Numbers to toggle, separated by spaces (Enter to continue): ",
        end="",
        flush=True,
        file=stdout,
    )
    selected = set(chosen)
    for number in stdin.readline().replace(",", " ").split():
        if not number.isdecimal() or not 1 <= int(number) <= len(names):
            raise InstallerError(f"invalid component selection: {number!r}")
        selected ^= {names[int(number) - 1]}
    return selected


def _install_profile(
    args: argparse.Namespace,
    manifest: Manifest,
    platform: PlatformInfo,
    stdin: TextIO,
    stdout: TextIO,
) -> str:
    if args.profile_option and args.profile_positional:
        raise InstallerError(
            "specify a profile either positionally or with --profile, not both"
        )
    selected = args.profile_option or args.profile_positional
    if selected:
        return selected
    if not stdin.isatty():
        raise InstallerError("--profile is required when input is not interactive")
    reasons = {
        name: _unavailable_reason(manifest, name, platform)
        for name in manifest.profiles
    }
    names = [name for name, reason in reasons.items() if reason is None]
    if not names:
        raise InstallerError(
            "no profile can be installed on this machine:\n  "
            + "\n  ".join(f"{name}: {reason}" for name, reason in reasons.items())
        )
    default = (
        manifest.default_profile if manifest.default_profile in names else names[0]
    )
    print("Choose an installation profile:", file=stdout)
    for name, reason in reasons.items():
        if reason is not None:
            print(f"  -  {name} — unavailable: {reason}", file=stdout)
            continue
        marker = " (default)" if name == default else ""
        print(
            f"  {names.index(name) + 1}. {name}{marker} — "
            f"{manifest.profiles[name].description}",
            file=stdout,
        )
    print("Profile number: ", end="", flush=True, file=stdout)
    answer = stdin.readline().strip()
    if not answer:
        return default
    try:
        index = int(answer)
        if index < 1:
            raise ValueError
        return names[index - 1]
    except (ValueError, IndexError) as exc:
        raise InstallerError(f"invalid profile selection: {answer!r}") from exc
