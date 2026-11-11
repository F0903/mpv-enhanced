"""What ``manifest.toml`` describes: components, profiles, and methods."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import UnsupportedPlatformError
from ..platforms import PlatformInfo


@dataclass(frozen=True, slots=True)
class Artifact:
    url: str
    sha256: str
    archive: str
    destination: str = "."
    exclude: tuple[str, ...] = ()

    @property
    def cache_key(self) -> str:
        return self.sha256


@dataclass(frozen=True, slots=True)
class UvRuntime:
    project: str
    environment: str
    python_version: str
    extras: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SystemProgram:
    """A program the host must already provide, such as a distribution's mpv."""

    command: str
    min_version: tuple[int, ...]
    check_args: tuple[str, ...] = ()
    check_contains: str | None = None


@dataclass(frozen=True, slots=True)
class GpuRequirement:
    """A graphics card the host must have, matched against its reported name."""

    matches: str
    requirement: str


@dataclass(frozen=True, slots=True)
class Resolution:
    label: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class InterpolationOptions:
    """The values the player's menu offers."""

    resolutions: tuple[Resolution, ...]
    frame_rates: tuple[int, ...]
    multipliers: tuple[int, ...]
    fps_cutoffs: tuple[int, ...]
    default_fps_cutoff: int


@dataclass(frozen=True, slots=True)
class MethodChoice:
    """One entry in the player's method list."""

    id: str
    title: str
    rank: int
    settings: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Method:
    """An interpolation method the installer can add, with any inheritance applied."""

    name: str
    description: str
    components: tuple[str, ...]
    extras: tuple[str, ...]
    platforms: tuple[str, ...]
    choices: tuple[MethodChoice, ...]
    preselect: bool = True


@dataclass(frozen=True, slots=True)
class ComponentSetup:
    """One value to ask the user for, saved to a file in the install."""

    question: str
    file: str
    line: str
    matches: str


@dataclass(frozen=True, slots=True)
class Component:
    name: str
    version: str
    kind: str
    destination: str
    path: str | None
    artifacts: Mapping[str, Artifact]
    runtime: UvRuntime | None = None
    platforms: tuple[str, ...] = ()
    system: SystemProgram | None = None
    gpu: GpuRequirement | None = None
    optional: bool = False
    description: str = ""
    setup: ComponentSetup | None = None

    def supports(self, platform: PlatformInfo) -> bool:
        # shortcut: a download missing a whole OS is skipped rather than an error,
        # which is how `mpv` opts out of Linux; add an explicit opt-out if that bites.
        return not self.platforms or any(
            key.startswith(f"{platform.system}-") for key in self.platforms
        )

    def artifact_for(self, platform: PlatformInfo) -> Artifact:
        if artifact := self.artifacts.get(platform.key):
            return artifact
        raise UnsupportedPlatformError(
            f"component {self.name!r} has no artifact for {platform.key}"
        )


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    description: str
    components: tuple[str, ...]
    extends: tuple[str, ...]
    platforms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    name: str
    description: str
    components: tuple[Component, ...]
    extras: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    choices: tuple[MethodChoice, ...] = ()
