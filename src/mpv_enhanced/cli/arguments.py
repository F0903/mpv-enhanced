"""What the ``mpve`` command line asked for, and the parser that reads it."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_cache_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "mpv-enhanced" / "cache"
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "mpv-enhanced"
    return Path.home() / ".cache" / "mpv-enhanced"


@dataclass
class Arguments:
    """The parsed command line.

    ``build_parser`` fills this in. An option that belongs to another command
    keeps the value it has here.
    """

    command: str = ""
    manifest: Path = Path("manifest.toml")
    source_root: Path | None = None
    platform_key: str | None = None
    # install and profile
    profile_positional: str | None = None
    profile_option: str | None = None
    install_dir: Path | None = None
    methods: list[str] | None = None
    optional: list[str] | None = None
    cache_dir: Path = field(default_factory=_default_cache_dir)
    offline: bool = False
    yes: bool = False
    accept_nvidia_license: bool = False
    # doctor
    profile: str | None = None
    # benchmark
    suggest_to: Path | None = None
    progress_file: Path | None = None
    # use
    version: str | None = None


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
            "--method",
            dest="methods",
            action="append",
            metavar="METHOD",
            help="add an interpolation method (see list-profiles); can be repeated",
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

    benchmark = subparsers.add_parser(
        "benchmark", help="measure interpolation methods and suggest target overrides"
    )
    _manifest_arguments(benchmark, subcommand=True)
    benchmark.add_argument("--install-dir", type=Path)
    benchmark.add_argument(
        "--suggest-to", type=Path, help="write suggestions here without saving them"
    )
    benchmark.add_argument("--progress-file", type=Path)

    use_parser = subparsers.add_parser(
        "use", help="make an installed version active and put mpv on the PATH"
    )
    _manifest_arguments(use_parser, subcommand=True)
    use_parser.add_argument("version", nargs="?", help="defaults to the active version")
    return parser
