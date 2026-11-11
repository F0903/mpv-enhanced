"""The install and profile commands, with their setup questions and licence prompt."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import TextIO

from .. import VERSION
from ..downloads import ArtifactCache, Downloader
from ..errors import InstallerError
from ..install.installer import Installer
from ..manifest import Manifest
from ..platforms import PlatformInfo, running_in_wsl
from ..versions.activate import is_in_use
from ..versions.layout import Layout
from .benchmark import _finish_interpolation
from .choices import _choose_methods, _choose_optional, _install_profile, _require_gpus
from .manage import _active_app

NVIDIA_TENSORRT_SLA_URL = (
    "https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html"
)
NVIDIA_SOFTWARE_LICENSE_URL = (
    "https://www.nvidia.com/en-us/agreements/enterprise-software/"
    "nvidia-software-license-agreement/"
)
NVIDIA_LICENSES = {
    "tensorrt": ("NVIDIA TensorRT SLA", NVIDIA_TENSORRT_SLA_URL),
    "rtx": ("NVIDIA Software License Agreement", NVIDIA_SOFTWARE_LICENSE_URL),
}


def _install_command(
    args: argparse.Namespace,
    manifest: Manifest,
    platform: PlatformInfo,
    layout: Layout,
    source_root: Path,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    downloader: Downloader,
) -> int:
    """Run ``mpve install`` or ``mpve profile``: ask what to install, then install it."""
    if args.command == "profile" and layout.active() is None:
        raise InstallerError("nothing is installed yet; run mpve install first")
    if running_in_wsl():
        print(
            "note: this is WSL, so the Linux version will be installed "
            "inside WSL. To install for Windows, run mpve.cmd from "
            "Windows instead.",
            file=stderr,
        )
    profile_name = _install_profile(args, manifest, platform, stdin, stdout)
    methods = _choose_methods(
        args, manifest, platform, layout, profile_name, stdin, stdout
    )
    # Before the licence prompt: no point accepting terms for a GPU that can't run it.
    _require_gpus(manifest, profile_name, platform, methods)
    optional = _choose_optional(args, manifest, platform, layout, stdin, stdout)
    answers = _setup_answers(args, manifest, optional, layout, stdin, stdout)
    _require_nvidia_license_acceptance(
        args,
        manifest,
        profile_name,
        platform,
        stdin,
        stdout,
        methods,
    )
    installer = Installer(
        manifest,
        ArtifactCache(args.cache_dir, downloader=downloader),
        source_root=source_root,
        platform=platform,
        report=lambda message: print(message, file=stdout, flush=True),
    )
    managed = args.install_dir is None
    if managed:
        layout.store_release(VERSION, source_root)
    result = installer.install(
        profile_name,
        args.install_dir or layout.app(VERSION),
        offline=args.offline,
        carry_from=layout.carry_source(VERSION) if managed else None,
        methods=methods,
        optional=optional,
        answers=answers,
    )
    print(
        f"Installed {result.receipt.release} ({result.receipt.profile}) to "
        f"{result.install_dir}",
        file=stdout,
    )
    if managed:
        layout.finish_install(VERSION)
        # Older versions are the rollback path, so this copy isn't needed.
        if result.previous_dir is not None:
            shutil.rmtree(result.previous_dir, ignore_errors=True)
        _finish_interpolation(
            args, result.install_dir, manifest, methods, stdin, stdout
        )
        print(
            f"Start mpv-enhanced with: {layout.player(VERSION, platform)}",
            file=stdout,
        )
        if not is_in_use(layout):
            print("Run mpve use to make mpv use this install.", file=stdout)
        return 0
    if result.previous_dir is not None:
        print(
            f"Previous installation retained at {result.previous_dir}",
            file=stdout,
        )
    _finish_interpolation(args, result.install_dir, manifest, methods, stdin, stdout)
    start = next(
        (
            path
            for name in ("mpv.exe", "mpv.app/Contents/MacOS/mpv")
            if (path := result.install_dir / name).is_file()
        ),
        "mpv",
    )
    # Only Windows mpv finds a configuration beside itself.
    config = (
        ""
        if platform.system == "windows"
        else f' --config-dir="{result.install_dir / "portable_config"}"'
    )
    print(f"Start mpv-enhanced with: {start}{config}", file=stdout)
    return 0


def _setup_answers(
    args: argparse.Namespace,
    manifest: Manifest,
    optional: tuple[str, ...],
    layout: Layout,
    stdin: TextIO,
    stdout: TextIO,
) -> dict[str, str]:
    """Go through the chosen components that still need settings."""
    answers: dict[str, str] = {}
    if args.yes or not stdin.isatty():
        return answers
    existing = args.install_dir or _active_app(layout)
    pending = [
        component
        for component in (manifest.components.get(name) for name in optional)
        if component is not None
        and component.setup is not None
        and not (existing and Path(existing, component.setup.file).exists())
    ]
    if pending:
        print("Set up optional components:", file=stdout)
    for index, component in enumerate(pending, 1):
        setup = component.setup
        assert setup is not None
        print(
            f"  [{index}/{len(pending)}] {component.name} — {component.description}",
            file=stdout,
        )
        while True:
            print(
                f"  {setup.question} (Enter to skip): ", end="", flush=True, file=stdout
            )
            answer = stdin.readline().strip()
            if not answer or re.fullmatch(setup.matches, answer):
                break
            print(f"  That doesn't look like a {setup.question}.", file=stdout)
        if answer:
            answers[component.name] = answer
    return answers


def _require_nvidia_license_acceptance(
    args: argparse.Namespace,
    manifest: Manifest,
    profile_name: str,
    platform: PlatformInfo,
    stdin: TextIO,
    stdout: TextIO,
    methods: tuple[str, ...] = (),
) -> None:
    """Require distinct, explicit consent before installing NVIDIA's packages."""
    profile = manifest.resolve_profile(profile_name, platform, methods=methods)
    licenses = [
        NVIDIA_LICENSES[extra] for extra in profile.extras if extra in NVIDIA_LICENSES
    ]
    if not licenses or args.accept_nvidia_license:
        return

    names = " and ".join(name for name, _ in licenses)
    if args.yes or not stdin.isatty():
        urls = ", ".join(url for _, url in licenses)
        raise InstallerError(
            f"this install requires acceptance of the {names} at "
            f"{urls}; review it and re-run with --accept-nvidia-license "
            "(--yes does not accept third-party licenses)"
        )

    print("The chosen methods install proprietary NVIDIA packages.", file=stdout)
    for name, url in licenses:
        print(f"Review the {name}: {url}", file=stdout)
    print(
        "Type 'accept' to accept and continue: ",
        end="",
        flush=True,
        file=stdout,
    )
    if stdin.readline().strip() != "accept":
        raise InstallerError(f"{names} was not accepted; installation cancelled")
