"""Profile discovery, transactional installation, and diagnostics CLI."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .doctor import run_doctor
from .downloads import ArtifactCache, Downloader, download_url
from .errors import InstallerError
from .installer import Installer
from .manifest import Manifest
from .platforms import PlatformInfo


def _manifest_arguments(
    parser: argparse.ArgumentParser,
    *,
    subcommand: bool = False,
) -> None:
    default = argparse.SUPPRESS if subcommand else None
    parser.add_argument(
        "--deps",
        "--deps-manifest",
        dest="deps",
        type=Path,
        default=default if subcommand else Path("deps.lock.toml"),
    )
    parser.add_argument(
        "--profiles",
        "--profiles-manifest",
        dest="profiles",
        type=Path,
        default=default if subcommand else Path("profiles.toml"),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=default,
        help="root for local components (defaults to the deps lock directory)",
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

    install = subparsers.add_parser("install", help="assemble and install a profile")
    _manifest_arguments(install, subcommand=True)
    install.add_argument("profile_positional", nargs="?", metavar="PROFILE")
    install.add_argument("--profile", dest="profile_option")
    install.add_argument(
        "--install-dir", type=Path, default=Path.cwd() / "mpv-enhanced"
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
        help="accepted by non-interactive bootstrappers; explicit installs do not prompt",
    )

    doctor = subparsers.add_parser("doctor", help="run read-only environment checks")
    _manifest_arguments(doctor, subcommand=True)
    doctor.add_argument("--profile")
    doctor.add_argument("--install-dir", type=Path)
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
        arguments = list(sys.argv[1:] if argv is None else argv)
        if not arguments:
            arguments = ["install"]
        args = parser.parse_args(arguments)
        manifest = Manifest.load(args.deps, args.profiles)
        source_root = (args.source_root or args.deps.parent).resolve()
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
                install_dir=args.install_dir,
            )
            for check in checks:
                marker = "ok" if check.ok else "FAIL"
                print(f"[{marker}] {check.name}: {check.detail}", file=stdout)
            return 0 if all(item.ok for item in checks) else 1
        if args.command == "install":
            profile_name = _install_profile(args, manifest, stdin, stdout)
            installer = Installer(
                manifest,
                ArtifactCache(args.cache_dir, downloader=downloader),
                source_root=source_root,
                platform=platform,
            )
            result = installer.install(
                profile_name,
                args.install_dir,
                offline=args.offline,
            )
            print(
                f"Installed {result.receipt.release} ({result.receipt.profile}) to "
                f"{result.install_dir}",
                file=stdout,
            )
            if result.previous_dir is not None:
                print(
                    f"Previous installation retained at {result.previous_dir}",
                    file=stdout,
                )
            return 0
        parser.error(f"unknown command: {args.command}")
    except InstallerError as exc:
        print(f"error: {exc}", file=stderr)
        return 2
    return 2


def _list_profiles(manifest: Manifest, platform: PlatformInfo, output: TextIO) -> int:
    for name, profile in manifest.profiles.items():
        try:
            resolved = manifest.resolve_profile(name, platform)
            status = f"{len(resolved.components)} components"
        except InstallerError as exc:
            status = f"unavailable: {exc}"
        print(f"{name}\t{status}\t{profile.description}", file=output)
    return 0


def _install_profile(
    args: argparse.Namespace,
    manifest: Manifest,
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
    names = list(manifest.profiles)
    print("Choose an installation profile:", file=stdout)
    for index, name in enumerate(names, 1):
        print(
            f"  {index}. {name} — {manifest.profiles[name].description}",
            file=stdout,
        )
    print("Profile number: ", end="", flush=True, file=stdout)
    answer = stdin.readline().strip()
    try:
        index = int(answer)
        if index < 1:
            raise ValueError
        return names[index - 1]
    except (ValueError, IndexError) as exc:
        raise InstallerError(f"invalid profile selection: {answer!r}") from exc


def _default_cache_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "mpv-enhanced" / "cache"
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "mpv-enhanced"
    return Path.home() / ".cache" / "mpv-enhanced"
