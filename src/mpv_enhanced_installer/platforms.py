"""Host-platform normalization used by manifests and diagnostics."""

from __future__ import annotations

import platform as stdlib_platform
from dataclasses import dataclass

from .errors import UnsupportedPlatformError

_SYSTEMS = {
    "windows": "windows",
    "win32": "windows",
    "linux": "linux",
    "darwin": "macos",
    "mac": "macos",
    "macos": "macos",
}
_ARCHITECTURES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "x86_64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "x86": "x86",
    "i386": "x86",
    "i686": "x86",
}


def running_in_wsl() -> bool:
    return "microsoft" in stdlib_platform.release().lower()


@dataclass(frozen=True, slots=True)
class PlatformInfo:
    """A normalized operating system and CPU architecture pair."""

    system: str
    architecture: str

    @classmethod
    def detect(
        cls,
        *,
        system: str | None = None,
        machine: str | None = None,
    ) -> PlatformInfo:
        raw_system = (system or stdlib_platform.system()).strip().lower()
        raw_machine = (machine or stdlib_platform.machine()).strip().lower()
        try:
            normalized_system = _SYSTEMS[raw_system]
        except KeyError as exc:
            raise UnsupportedPlatformError(
                f"unsupported operating system: {raw_system or '<unknown>'}"
            ) from exc
        try:
            normalized_machine = _ARCHITECTURES[raw_machine]
        except KeyError as exc:
            raise UnsupportedPlatformError(
                f"unsupported CPU architecture: {raw_machine or '<unknown>'}"
            ) from exc
        return cls(normalized_system, normalized_machine)

    @classmethod
    def parse(cls, value: str) -> PlatformInfo:
        """Parse a key such as ``windows-x86_64``."""
        value = value.strip().lower()
        for system in ("windows", "linux", "macos"):
            prefix = f"{system}-"
            if value.startswith(prefix):
                return cls.detect(system=system, machine=value[len(prefix) :])
        raise UnsupportedPlatformError(f"invalid platform key: {value!r}")

    @property
    def key(self) -> str:
        return f"{self.system}-{self.architecture}"
