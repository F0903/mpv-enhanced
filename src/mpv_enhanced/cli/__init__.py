"""The ``mpve`` command line: reads the arguments and runs the command."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO

from ..downloads import Downloader, download_url
from ..errors import InstallerError
from ..install.doctor import run_doctor
from ..manifest import Manifest
from ..platforms import PlatformInfo
from ..versions.layout import Layout
from ..versions.update import update
from .arguments import Arguments, build_parser
from .benchmark import benchmark_command
from .choices import list_profiles
from .install import install_command
from .manage import manager_menu, status


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    downloader: Downloader = download_url,
) -> int:
    streams: tuple[TextIO, TextIO, TextIO] = (
        stdin or sys.stdin,
        stdout or sys.stdout,
        stderr or sys.stderr,
    )
    stdin, stdout, stderr = streams
    parser = build_parser()
    try:
        layout = Layout.from_environment()
        arguments = list(sys.argv[1:] if argv is None else argv)
        if not arguments:
            chosen = manager_menu(layout, stdin, stdout)
            if chosen is None:
                return 0
            arguments = chosen
        args = parser.parse_args(arguments, namespace=Arguments())
        manifest = Manifest.load(args.manifest)
        source_root = (args.source_root or args.manifest.parent).resolve()
        platform = (
            PlatformInfo.parse(args.platform_key)
            if args.platform_key
            else PlatformInfo.detect()
        )
        if args.command == "list-profiles":
            return list_profiles(manifest, platform, stdout)
        if args.command == "doctor":
            checks = run_doctor(
                manifest,
                platform=platform,
                source_root=source_root,
                profile_name=args.profile,
                install_dir=args.install_dir or layout.active_app(),
            )
            for check in checks:
                marker = "ok" if check.ok else "FAIL"
                print(f"[{marker}] {check.name}: {check.detail}", file=stdout)
            return 0 if all(item.ok for item in checks) else 1
        if args.command == "status":
            return status(layout, platform, stdout)
        if args.command == "benchmark":
            return benchmark_command(args, manifest, platform, layout, stdin, stdout)
        if args.command == "use":
            for message in layout.use(args.version, platform):
                print(message, file=stdout)
            return 0
        if args.command == "rollback":
            print(f"Now using {layout.rollback()}", file=stdout)
            return 0
        if args.command == "update":
            updated = update(layout)
            print(
                f"Updated to {updated}" if updated else "Already up to date",
                file=stdout,
            )
            return 0
        if args.command in ("install", "profile"):
            return install_command(
                args,
                manifest,
                platform,
                layout,
                source_root,
                stdin,
                stdout,
                stderr,
                downloader,
            )
        parser.error(f"unknown command: {args.command}")
    except InstallerError as exc:
        print(f"error: {exc}", file=stderr)
        return 2
