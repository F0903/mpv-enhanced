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
from ..versions.activate import rollback, use
from ..versions.layout import Layout
from ..versions.update import update
from .benchmark import _benchmark_command
from .choices import _list_profiles
from .install import _install_command
from .manage import _active_app, _manager_menu, _status
from .parser import build_parser


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
        if args.command == "benchmark":
            return _benchmark_command(args, manifest, platform, layout, stdin, stdout)
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
        if args.command in ("install", "profile"):
            return _install_command(
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
