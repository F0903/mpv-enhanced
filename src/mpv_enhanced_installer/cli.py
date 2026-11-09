"""Profile discovery, transactional installation, and diagnostics CLI."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .doctor import run_doctor
from .downloads import ArtifactCache, Downloader, download_url
from .errors import InstallerError
from .installer import Installer, check_gpu, check_system_program
from .manager import Layout, is_in_use, rollback, update, use
from .manifest import Manifest
from .platforms import PlatformInfo, running_in_wsl
from .version import VERSION

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


def _manifest_arguments(
    parser: argparse.ArgumentParser,
    *,
    subcommand: bool = False,
) -> None:
    default = argparse.SUPPRESS if subcommand else None
    parser.add_argument(
        "--manifest",
        type=Path,
        default=default if subcommand else Path("manifest.toml"),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=default,
        help="root for local components (defaults to the manifest's directory)",
    )
    parser.add_argument(
        "--platform",
        dest="platform_key",
        default=default,
        help="override host detection, for example windows-x86_64",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpv-enhanced")
    _manifest_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_profiles = subparsers.add_parser(
        "list-profiles", help="list available installation profiles"
    )
    _manifest_arguments(list_profiles, subcommand=True)

    for name, description in (
        ("install", "assemble and install a profile"),
        ("profile", "switch the active version to another profile"),
    ):
        install = subparsers.add_parser(name, help=description)
        _manifest_arguments(install, subcommand=True)
        install.add_argument("profile_positional", nargs="?", metavar="PROFILE")
        install.add_argument("--profile", dest="profile_option")
        install.add_argument(
            "--install-dir",
            type=Path,
            help="install here instead of under live/, outside version management",
        )
        install.add_argument(
            "--with",
            dest="optional",
            action="append",
            metavar="COMPONENT",
            help="add an optional component (see list-profiles); can be repeated",
        )
        install.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
        install.add_argument(
            "--offline",
            action="store_true",
            help="forbid downloads; currently supported for core/non-uv profiles only",
        )
        install.add_argument(
            "--yes",
            action="store_true",
            help="non-interactive confirmation; never accepts third-party licenses",
        )
        install.add_argument(
            "--accept-nvidia-license",
            action="store_true",
            help="confirm acceptance of NVIDIA's license when required",
        )

    doctor = subparsers.add_parser("doctor", help="run read-only environment checks")
    _manifest_arguments(doctor, subcommand=True)
    doctor.add_argument("--profile")
    doctor.add_argument("--install-dir", type=Path)

    for name, description in (
        ("status", "show the active version and profile"),
        ("update", "install the newest mpv-enhanced release"),
        ("rollback", "switch back to the previous version"),
    ):
        _manifest_arguments(
            subparsers.add_parser(name, help=description), subcommand=True
        )

    use_parser = subparsers.add_parser(
        "use", help="make an installed version active and put mpv on the PATH"
    )
    _manifest_arguments(use_parser, subcommand=True)
    use_parser.add_argument("version", nargs="?", help="defaults to the active version")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    downloader: Downloader = download_url,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    try:
        layout = Layout.from_environment()
        arguments = list(sys.argv[1:] if argv is None else argv)
        if not arguments:
            chosen = _manager_menu(layout, stdin, stdout)
            if chosen is None:
                return 0
            arguments = chosen
        args = parser.parse_args(arguments)
        manifest = Manifest.load(args.manifest)
        source_root = (args.source_root or args.manifest.parent).resolve()
        platform = (
            PlatformInfo.parse(args.platform_key)
            if args.platform_key
            else PlatformInfo.detect()
        )
        if args.command == "list-profiles":
            return _list_profiles(manifest, platform, stdout)
        if args.command == "doctor":
            checks = run_doctor(
                manifest,
                platform=platform,
                source_root=source_root,
                profile_name=args.profile,
                install_dir=args.install_dir or _active_app(layout),
            )
            for check in checks:
                marker = "ok" if check.ok else "FAIL"
                print(f"[{marker}] {check.name}: {check.detail}", file=stdout)
            return 0 if all(item.ok for item in checks) else 1
        if args.command == "status":
            return _status(layout, platform, stdout)
        if args.command == "use":
            for message in use(layout, args.version, platform):
                print(message, file=stdout)
            return 0
        if args.command == "rollback":
            print(f"Now using {rollback(layout)}", file=stdout)
            return 0
        if args.command == "update":
            updated = update(layout)
            print(
                f"Updated to {updated}" if updated else "Already up to date",
                file=stdout,
            )
            return 0
        if args.command == "profile" and layout.active() is None:
            raise InstallerError("nothing is installed yet; run mpve install first")
        if args.command in ("install", "profile"):
            if running_in_wsl():
                print(
                    "note: this is WSL, so the Linux version will be installed "
                    "inside WSL. To install for Windows, run mpve.cmd from "
                    "Windows instead.",
                    file=stderr,
                )
            profile_name = _install_profile(args, manifest, platform, stdin, stdout)
            # Before the licence prompt: no point accepting terms for a GPU that can't run it.
            for component in manifest.resolve_profile(
                profile_name, platform
            ).components:
                if component.gpu is not None:
                    check_gpu(component.gpu)
            optional = _choose_optional(args, manifest, platform, layout, stdin, stdout)
            answers = _setup_answers(args, manifest, optional, layout, stdin, stdout)
            _require_nvidia_license_acceptance(
                args,
                manifest,
                profile_name,
                platform,
                stdin,
                stdout,
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
        parser.error(f"unknown command: {args.command}")
    except InstallerError as exc:
        print(f"error: {exc}", file=stderr)
        return 2


def _active_app(layout: Layout) -> Path | None:
    active = layout.active()
    return layout.app(active) if active else None


def _status(layout: Layout, platform: PlatformInfo, output: TextIO) -> int:
    active = layout.active()
    if active is None:
        print("Nothing is installed yet. Run mpve to install.", file=output)
        return 0
    print(f"mpv-enhanced {active}", file=output)
    print(f"Profile: {layout.profile(active) or 'unknown'}", file=output)
    print(f"Start with: {layout.player(active, platform)}", file=output)
    previous = layout.previous()
    if previous:
        print(f"Previous version: {previous} (mpve rollback)", file=output)
    return 0


def _manager_menu(
    layout: Layout,
    stdin: TextIO,
    stdout: TextIO,
) -> list[str] | None:
    """Pick a command interactively. Returns None to quit."""
    active = layout.active()
    if active is None or not stdin.isatty():
        return ["install"]
    print(f"mpv-enhanced {active} ({layout.profile(active) or 'unknown'})", file=stdout)
    choices: list[tuple[str, list[str] | None]] = [
        ("Change profile", ["profile"]),
        ("Check for updates", ["update"]),
    ]
    previous = layout.previous()
    if previous:
        choices.append((f"Roll back to {previous}", ["rollback"]))
    choices += [("Check this installation", ["doctor"]), ("Quit", None)]
    for index, (label, _) in enumerate(choices, 1):
        print(f"  {index}. {label}", file=stdout)
    print("Choice: ", end="", flush=True, file=stdout)
    answer = stdin.readline().strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(choices):
        raise InstallerError(f"invalid choice: {answer!r}")
    return choices[int(answer) - 1][1]


def _unavailable_reason(
    manifest: Manifest,
    name: str,
    platform: PlatformInfo,
) -> str | None:
    """Explain why a profile can't be installed on this machine, if it can't."""
    try:
        profile = manifest.resolve_profile(name, platform)
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


def _list_profiles(manifest: Manifest, platform: PlatformInfo, output: TextIO) -> int:
    for name, profile in manifest.profiles.items():
        reason = _unavailable_reason(manifest, name, platform)
        if reason is None:
            count = len(manifest.resolve_profile(name, platform).components)
            status = f"{count} components"
        else:
            status = f"unavailable: {reason}"
        print(f"{name}\t{status}\t{profile.description}", file=output)
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
    ask = stdin.isatty() and not args.yes and (not active or args.command == "profile")
    if names and ask:
        print("Choose optional components:", file=stdout)
        for component in manifest.components.values():
            if not component.optional:
                continue
            if component.name not in names:
                print(
                    f"  -  {component.name} — unavailable on {platform.key}",
                    file=stdout,
                )
                continue
            mark = "x" if component.name in chosen else " "
            print(
                f"  {names.index(component.name) + 1}. [{mark}] {component.name} — "
                f"{component.description}",
                file=stdout,
            )
        print(
            "Numbers to toggle, separated by spaces (Enter to continue): ",
            end="",
            flush=True,
            file=stdout,
        )
        for number in stdin.readline().replace(",", " ").split():
            if not number.isdecimal() or not 1 <= int(number) <= len(names):
                raise InstallerError(f"invalid component selection: {number!r}")
            chosen ^= {names[int(number) - 1]}
    return tuple(name for name in names if name in chosen)


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


def _require_nvidia_license_acceptance(
    args: argparse.Namespace,
    manifest: Manifest,
    profile_name: str,
    platform: PlatformInfo,
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    """Require distinct, explicit consent before installing NVIDIA's packages."""
    profile = manifest.resolve_profile(profile_name, platform)
    licenses = [
        NVIDIA_LICENSES[extra] for extra in profile.extras if extra in NVIDIA_LICENSES
    ]
    if not licenses or args.accept_nvidia_license:
        return

    names = " and ".join(name for name, _ in licenses)
    if args.yes or not stdin.isatty():
        urls = ", ".join(url for _, url in licenses)
        raise InstallerError(
            f"profile {profile_name!r} requires acceptance of the {names} at "
            f"{urls}; review it and re-run with --accept-nvidia-license "
            "(--yes does not accept third-party licenses)"
        )

    print("This profile installs proprietary NVIDIA packages.", file=stdout)
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


def _default_cache_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "mpv-enhanced" / "cache"
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "mpv-enhanced"
    return Path.home() / ".cache" / "mpv-enhanced"


if __name__ == "__main__":
    raise SystemExit(main())
