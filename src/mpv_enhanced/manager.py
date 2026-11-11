"""Versioned installs under ``live/``: activation, rollback, and updates."""

from __future__ import annotations

import json
import os
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

from . import VERSION
from .archives import extract_archive
from .downloads import Downloader, download_url, sha256_file
from .errors import InstallerError
from .manifest import parse_version
from .platforms import PlatformInfo
from .receipt import RECEIPT_FILENAME, InstallationReceipt

KEPT_VERSIONS = 2
LOADER_CONFIG = Path("/etc/ld.so.conf.d/mpv-enhanced.conf")
NOT_RELEASE_FILES = (
    "live",
    ".git",
    ".venv",
    ".uv-cache",
    ".bootstrap",
    "controller-python",
    "__pycache__",
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

    def player(self, version: str, platform: PlatformInfo) -> Path:
        """What starts this version; Linux uses the system's mpv."""
        if platform.system == "linux":
            return Path("mpv")
        if platform.system == "macos":
            return self.app(version) / "mpv.app" / "Contents" / "MacOS" / "mpv"
        return self.app(version) / "mpv.exe"

    def path_entries(self, version: str) -> list[Path]:
        """The Windows PATH folders for this version: mpv, then mpve at the root."""
        return [self.app(version), self.root]

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

    def _receipt(self, version: str) -> InstallationReceipt | None:
        try:
            return InstallationReceipt.load(self.app(version) / RECEIPT_FILENAME)
        except InstallerError:
            return None

    def profile(self, version: str) -> str | None:
        receipt = self._receipt(version)
        return receipt.profile if receipt else None

    def methods(self, version: str) -> tuple[str, ...]:
        receipt = self._receipt(version)
        return receipt.methods if receipt else ()

    def components(self, version: str) -> tuple[str, ...]:
        receipt = self._receipt(version)
        return tuple(item.name for item in receipt.components) if receipt else ()

    def activate(self, version: str) -> None:
        # First, so that a failure here leaves the previous version active.
        if os.name == "nt":
            set_user_path(self.path_entries(version), self.live, only_if_present=True)
            set_start_menu_shortcut(
                self.app(version) / "mpv.exe", self.live, only_if_present=True
            )
        else:
            link_version(
                self, version, PlatformInfo.detect(), Path.home(), only_if_present=True
            )
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

    def finish_install(self, version: str) -> None:
        self.activate(version)
        kept = [version, *(item for item in self.versions() if item != version)]
        for stale in kept[KEPT_VERSIONS:]:
            shutil.rmtree(self.live / stale, ignore_errors=True)


def _config_link(home: Path) -> Path:
    """Where mpv looks for its configuration on Linux and macOS."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "mpv"


def _is_ours(link: Path, layout: Layout) -> bool:
    return link.is_symlink() and Path(os.readlink(link)).is_relative_to(layout.root)


def _replace_link(link: Path, target: Path) -> None:
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}-{uuid.uuid4().hex}")
    temporary.symlink_to(target)
    os.replace(temporary, link)


def _point_loader_at(folder: Path, run: Callable[..., Any]) -> None:
    """Let mpv find libvsscript.so, which it loads by bare name."""
    try:
        if LOADER_CONFIG.read_text("utf-8") == f"{folder}\n":
            return
    except FileNotFoundError:
        pass
    script = 'printf "%s\\n" "$1" > "$2" && ldconfig'
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    command = [*sudo, "sh", "-c", script, "sh", str(folder), str(LOADER_CONFIG)]
    if run(command, check=False).returncode:
        raise InstallerError(f"could not update {LOADER_CONFIG}; mpv needs it for RIFE")


def link_version(
    layout: Layout,
    version: str,
    platform: PlatformInfo,
    home: Path,
    *,
    only_if_present: bool = False,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    """Point this user's mpv at a version with symlinks, so it needs no launcher.

    With ``only_if_present``, does nothing until ``mpve use`` has set this up.
    """
    app = layout.app(version)
    config = _config_link(home)
    if only_if_present and not _is_ours(config, layout):
        return
    links = {
        config: app / "portable_config",
        home / ".local" / "bin" / "mpve": layout.root / "mpve.sh",
    }
    if platform.system == "macos":
        links[home / ".local" / "bin" / "mpv"] = (
            app / "mpv.app" / "Contents" / "MacOS" / "mpv"
        )
    for link in links:
        if (link.exists() or link.is_symlink()) and not _is_ours(link, layout):
            raise InstallerError(f"{link} already exists; move it away first")
    pattern = "runtime/*/lib/python*/site-packages/vapoursynth/libvsscript.so"
    if library := next(app.glob(pattern), None):
        _point_loader_at(library.parent, run)
    for link, target in links.items():
        _replace_link(link, target)


def is_in_use(layout: Layout) -> bool:
    """Whether ``mpve use`` has already pointed this system at an installed version."""
    if os.name != "nt":
        return _is_ours(_config_link(Path.home()), layout)
    found = shutil.which("mpv")
    return found is not None and Path(found).is_relative_to(layout.live)


def set_user_path(
    directories: list[Path],
    versions: Path,
    *,
    only_if_present: bool = False,
    subkey: str = "Environment",
) -> None:
    """Put folders first on the Windows user PATH, replacing any inside ``versions``.

    With ``only_if_present``, does nothing unless a folder inside ``versions`` is
    already there, so that switching versions never adds mpv to PATH by itself.
    """
    import ctypes
    import winreg

    def normal(entry: Path | str) -> str:
        return str(entry).rstrip("\\").casefold()

    prefix = normal(versions) + "\\"
    wanted = {normal(directory) for directory in directories}
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE
    ) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in value.split(";") if entry]
        if only_if_present and not any(
            normal(entry).startswith(prefix) for entry in entries
        ):
            return
        kept = [
            entry
            for entry in entries
            if normal(entry) not in wanted and not normal(entry).startswith(prefix)
        ]
        result = [*map(str, directories), *kept]
        if result == entries:
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(result))
    # Tells Explorer, so that terminals opened from now on see the new PATH.
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 2, 5000, None
    )


def set_start_menu_shortcut(
    player: Path,
    versions: Path,
    *,
    only_if_present: bool = False,
    shortcut: Path | None = None,
) -> None:
    """Point the Start menu's MPV Enhanced shortcut at ``player``.

    With ``only_if_present``, does nothing unless the shortcut already opens a
    version inside ``versions``.
    """
    shortcut = shortcut or (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "MPV Enhanced.lnk"
    )
    if only_if_present:
        try:
            # A shortcut keeps its working folder as UTF-16 text, so ours contain this.
            if str(versions).encode("utf-16-le") not in shortcut.read_bytes():
                return
        except FileNotFoundError:
            return
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MPVE_LINK);"
        "$link.TargetPath = $env:MPVE_PLAYER;"
        "$link.WorkingDirectory = $env:MPVE_FOLDER;"
        "$link.Save()"
    )
    paths = {"MPVE_LINK": shortcut, "MPVE_PLAYER": player, "MPVE_FOLDER": player.parent}
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, **{name: str(path) for name, path in paths.items()}},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise InstallerError(f"could not create {shortcut}: {result.stderr.strip()}")


def use(
    layout: Layout,
    version: str | None,
    platform: PlatformInfo,
    *,
    add_to_path: Callable[[list[Path], Path], None] = set_user_path,
    add_shortcut: Callable[[Path, Path], None] = set_start_menu_shortcut,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Activate an installed version and make it the one ``mpv`` and ``mpve`` run."""
    version = version or layout.active()
    if version is None:
        raise InstallerError("nothing is installed yet; run mpve install first")
    if version not in layout.versions():
        installed = ", ".join(layout.versions()) or "none"
        raise InstallerError(f"{version} is not installed (installed: {installed})")
    home = home or Path.home()
    messages = [f"Now using {version}"]
    if platform.system == "windows":
        directory = layout.app(version)
        add_to_path(layout.path_entries(version), layout.live)
        add_shortcut(layout.player(version, platform), layout.live)
        messages.append(
            f"{directory} is on your PATH. Open a new terminal to use mpv and mpve."
        )
        messages.append("MPV Enhanced is in the Start menu.")
    else:
        link_version(layout, version, platform, home)
        directory = home / ".local" / "bin"
        if str(directory) not in os.environ.get("PATH", "").split(os.pathsep):
            names = "mpve" if platform.system == "linux" else "mpv and mpve"
            messages.append(f"Add {directory} to your PATH to use {names}.")
    layout.activate(version)
    existing = which("mpv")
    # On Linux the system mpv is the one this configures.
    if (
        existing
        and platform.system != "linux"
        and Path(existing).parent != directory
        and not Path(existing).is_relative_to(layout.live)
    ):
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
    for item in metadata("mpv-enhanced").get_all("Project-URL") or []:
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
            "User-Agent": f"mpv-enhanced/{VERSION}",
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
