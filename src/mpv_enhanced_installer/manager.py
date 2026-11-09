"""Versioned installs under ``live/``: activation, rollback, and updates."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import metadata
from pathlib import Path
from typing import Any

from .archives import extract_archive
from .downloads import Downloader, download_url, sha256_file
from .errors import InstallerError
from .manifest import parse_version
from .platforms import PlatformInfo
from .receipt import RECEIPT_FILENAME, InstallationReceipt
from .version import VERSION

KEPT_VERSIONS = 2
SHIM_MARKER = "# mpv-enhanced shim"
NOT_RELEASE_FILES = (
    "live",
    "shims",
    ".git",
    ".venv",
    ".uv-cache",
    ".bootstrap",
    "controller-python",
    "__pycache__",
    "mpv-enhanced",
    "mpv-enhanced.cmd",
)


def _version_key(version: str) -> tuple[int, ...]:
    return parse_version(version) or ()


@dataclass(frozen=True, slots=True)
class Layout:
    """Where versions live: ``live/<version>/{release,app}`` and ``live/current``."""

    root: Path

    @classmethod
    def from_environment(cls) -> Layout:
        return cls(Path(os.environ.get("MPVE_ROOT") or Path.cwd()).resolve())

    @property
    def live(self) -> Path:
        return self.root / "live"

    @property
    def current_file(self) -> Path:
        return self.live / "current"

    @property
    def is_checkout(self) -> bool:
        return (self.root / ".git").exists()

    def app(self, version: str) -> Path:
        return self.live / version / "app"

    def release(self, version: str) -> Path:
        return self.live / version / "release"

    def launcher(self, platform: PlatformInfo) -> Path:
        name = "mpv-enhanced.cmd" if platform.system == "windows" else "mpv-enhanced"
        return self.root / name

    def active(self) -> str | None:
        try:
            return self.current_file.read_text("utf-8").strip() or None
        except FileNotFoundError:
            return None

    def versions(self) -> list[str]:
        """Installed versions, newest first."""
        if not self.live.is_dir():
            return []
        found = [item.name for item in self.live.iterdir() if (item / "app").is_dir()]
        return sorted(found, key=_version_key, reverse=True)

    def previous(self) -> str | None:
        """The newest installed version other than the active one."""
        active = self.active()
        return next((item for item in self.versions() if item != active), None)

    def profile(self, version: str) -> str | None:
        try:
            receipt = InstallationReceipt.load(self.app(version) / RECEIPT_FILENAME)
        except InstallerError:
            return None
        return receipt.profile

    def activate(self, version: str) -> None:
        temporary = self.current_file.with_name(f".current-{uuid.uuid4().hex}")
        temporary.write_text(version, "utf-8")
        os.replace(temporary, self.current_file)

    def store_release(self, version: str, source: Path) -> None:
        """Keep the release that installed this version, so it can manage it later."""
        destination = self.release(version)
        if self.is_checkout or destination.exists() or source == destination:
            return
        shutil.copytree(
            source, destination, ignore=shutil.ignore_patterns(*NOT_RELEASE_FILES)
        )

    def carry_source(self, version: str) -> Path | None:
        """The install whose user files a new install of ``version`` should keep."""
        active = self.active()
        if active is None or active == version or not self.app(active).is_dir():
            return None
        return self.app(active)

    def finish_install(self, version: str, platform: PlatformInfo) -> None:
        self.activate(version)
        self._write_launcher(platform)
        kept = [version, *(item for item in self.versions() if item != version)]
        for stale in kept[KEPT_VERSIONS:]:
            shutil.rmtree(self.live / stale, ignore_errors=True)

    def _write_launcher(self, platform: PlatformInfo) -> None:
        launcher = self.launcher(platform)
        if platform.system == "windows":
            # No parenthesized block: a ")" in the install path would end it early.
            lines = [
                "@echo off",
                "setlocal",
                'set /p ACTIVE=<"%~dp0live\\current"',
                'set "APP=%~dp0live\\%ACTIVE%\\app"',
                (
                    'if exist "%APP%\\mpv-enhanced.cmd" '
                    'call "%APP%\\mpv-enhanced.cmd" %* & exit /b'
                ),
                '"%APP%\\mpv.exe" %*',
            ]
            launcher.write_text(
                "\n".join(lines) + "\n", encoding="utf-8", newline="\r\n"
            )
            return
        lines = [
            "#!/bin/sh",
            'root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1',
            'exec "$root/live/$(cat "$root/live/current")/app/mpv-enhanced" "$@"',
        ]
        launcher.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        launcher.chmod(launcher.stat().st_mode | 0o111)


def _write_windows_shims(layout: Layout) -> Path:
    shims = layout.root / "shims"
    shims.mkdir(exist_ok=True)
    for name, target in (("mpv", "mpv-enhanced.cmd"), ("mpve", "mpve.cmd")):
        (shims / f"{name}.cmd").write_text(
            f'@call "%~dp0..\\{target}" %*\n', encoding="utf-8", newline="\r\n"
        )
    return shims


def _write_posix_shims(layout: Layout, home: Path) -> Path:
    directory = home / ".local" / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    for name, target in (("mpv", "mpv-enhanced"), ("mpve", "mpve.sh")):
        shim = directory / name
        if shim.exists() and SHIM_MARKER not in shim.read_text(
            encoding="utf-8", errors="ignore"
        ):
            raise InstallerError(f"{shim} already exists; remove it first")
        lines = [
            "#!/bin/sh",
            SHIM_MARKER,
            # On Linux the launcher runs the system mpv by name, not this shim.
            'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)',
            "PATH=$(printf '%s' \"$PATH\" | tr ':' '\\n' | grep -vxF \"$here\" | paste -sd: -)",
            "export PATH",
            f'exec {shlex.quote(str(layout.root / target))} "$@"',
        ]
        shim.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        shim.chmod(0o755)
    return directory


def add_to_user_path(directory: Path, subkey: str = "Environment") -> bool:
    """Add a directory to the Windows user PATH. Returns False if already there."""
    import ctypes
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE
    ) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in value.split(";") if entry]
        wanted = str(directory).rstrip("\\").casefold()
        if any(entry.rstrip("\\").casefold() == wanted for entry in entries):
            return False
        winreg.SetValueEx(key, "Path", 0, kind, ";".join([str(directory), *entries]))
    # Tells Explorer, so that terminals opened from now on see the new PATH.
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 2, 5000, None
    )
    return True


def use(
    layout: Layout,
    version: str | None,
    platform: PlatformInfo,
    *,
    add_to_path: Callable[[Path], bool] = add_to_user_path,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Activate an installed version and put ``mpv`` and ``mpve`` on the PATH."""
    version = version or layout.active()
    if version is None:
        raise InstallerError("nothing is installed yet; run mpve install first")
    if version not in layout.versions():
        installed = ", ".join(layout.versions()) or "none"
        raise InstallerError(f"{version} is not installed (installed: {installed})")
    layout.activate(version)
    layout._write_launcher(platform)

    messages = [f"Now using {version}"]
    if platform.system == "windows":
        directory = _write_windows_shims(layout)
        if add_to_path(directory):
            messages.append(
                f"Added {directory} to your PATH. "
                "Open a new terminal to use mpv and mpve."
            )
    else:
        directory = _write_posix_shims(layout, home or Path.home())
        if str(directory) not in os.environ.get("PATH", "").split(os.pathsep):
            messages.append(f"Add {directory} to your PATH to use mpv and mpve.")
    existing = which("mpv")
    if existing and Path(existing).parent != directory:
        messages.append(f"Note: {existing} is also on your PATH and may run instead.")
    return messages


def rollback(layout: Layout) -> str:
    """Switch to the previous installed version and return it."""
    previous = layout.previous()
    if previous is None:
        raise InstallerError("there is no previous version to roll back to")
    layout.activate(previous)
    return previous


def _repository() -> str:
    for item in metadata("mpv-enhanced-installer").get_all("Project-URL") or []:
        label, _, url = item.partition(",")
        if label.strip() == "Repository":
            return url.strip().removesuffix(".git").split("github.com/", 1)[1]
    raise InstallerError("the installer has no repository URL")


def _fetch_latest_release(repository: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"mpv-enhanced-installer/{VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise InstallerError("no releases have been published yet") from exc
        raise InstallerError(f"cannot check for updates: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise InstallerError(f"cannot check for updates: {exc}") from exc


def _asset_url(release: dict[str, Any], suffix: str) -> tuple[str, str]:
    for asset in release.get("assets", []):
        if asset["name"].endswith(suffix):
            return asset["name"], asset["browser_download_url"]
    raise InstallerError(f"the latest release has no {suffix} file")


def update(
    layout: Layout,
    *,
    fetch: Callable[[str], dict[str, Any]] = _fetch_latest_release,
    repository: Callable[[], str] = _repository,
    download: Downloader = download_url,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    """Install the newest release and return its version, or None if up to date."""
    if layout.is_checkout:
        raise InstallerError("this is a git checkout; use git pull to update it")
    active = layout.active()
    profile = layout.profile(active) if active else None
    if active is None or profile is None:
        raise InstallerError("nothing is installed yet; run mpve install first")

    release = fetch(repository())
    version = str(release["tag_name"]).removeprefix("v")
    if _version_key(version) <= _version_key(active):
        return None

    workspace = layout.live / f".update-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True)
    try:
        zip_name, zip_url = _asset_url(release, ".zip")
        _, sums_url = _asset_url(release, "SHA256SUMS")
        archive = workspace / zip_name
        sums = workspace / "SHA256SUMS"
        download(zip_url, archive)
        download(sums_url, sums)
        expected = next(
            (
                line.split()[0].lower()
                for line in sums.read_text("utf-8").splitlines()
                if line.split()[-1].lstrip("*") == zip_name
            ),
            None,
        )
        if expected != sha256_file(archive):
            raise InstallerError(f"{zip_name} failed its checksum")
        extract_archive(archive, workspace / "extracted", archive_type="zip")
        tops = list((workspace / "extracted").iterdir())
        if len(tops) != 1 or not (tops[0] / "pyproject.toml").is_file():
            raise InstallerError(f"{zip_name} is not an mpv-enhanced release")

        destination = layout.release(version)
        shutil.rmtree(layout.live / version, ignore_errors=True)
        destination.parent.mkdir(parents=True)
        shutil.move(str(tops[0]), destination)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    # Run the new release's own manager; it activates the version when it succeeds.
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None:
        raise InstallerError("uv is unavailable; run mpve.cmd or mpve.sh")
    environment = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    environment["MPVE_ROOT"] = str(layout.root)
    result = run(
        [
            uv,
            "run",
            "--project",
            str(destination),
            "--locked",
            "--no-dev",
            "--no-editable",
            "mpv-enhanced",
            "install",
            "--profile",
            profile,
        ],
        cwd=destination,
        env=environment,
        check=False,
    )
    if result.returncode != 0 or layout.active() != version:
        shutil.rmtree(layout.live / version, ignore_errors=True)
        raise InstallerError(f"installing {version} failed; {active} is still active")
    return version
