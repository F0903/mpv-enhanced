"""Where versions live under ``live/``, and which one is active."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..errors import InstallerError
from ..install.receipt import RECEIPT_FILENAME, InstallationReceipt
from ..manifest import parse_version
from ..platforms import PlatformInfo
from .posix import link_version
from .windows import set_start_menu_shortcut, set_user_path

KEPT_VERSIONS = 2
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
