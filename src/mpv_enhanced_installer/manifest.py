"""Parsing and validation for locked components and install profiles."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any

from .errors import ManifestError, UnsupportedPlatformError
from .platforms import PlatformInfo

_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_ARCHIVES = frozenset({"zip", "zip+tar.gz", "none"})
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _table(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a TOML table")
    return value


def _string(value: object, label: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{label} must be a non-empty string")
    return value.strip()


def _string_list(
    value: object,
    label: str,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ManifestError(f"{label} must be a string or an array of strings")
    return tuple(value)


def validate_relative_path(value: str, label: str) -> str:
    """Normalize a manifest path, rejecting absolute and traversal paths."""
    if not value or "\x00" in value:
        raise ManifestError(f"{label} must be a non-empty relative path")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    if (
        windows.is_absolute()
        or windows.drive
        or posix.is_absolute()
        or ".." in posix.parts
    ):
        raise ManifestError(f"{label} must stay within the install tree: {value!r}")
    parts = tuple(part for part in posix.parts if part not in ("", "."))
    return "/".join(parts) if parts else "."


@dataclass(frozen=True, slots=True)
class Artifact:
    platform: str
    url: str
    sha256: str
    size: int
    archive: str
    strip_components: int = 0
    destination: str = "."
    exclude: tuple[str, ...] = ()

    @property
    def cache_key(self) -> str:
        return self.sha256


@dataclass(frozen=True, slots=True)
class UvRuntime:
    project: str
    lockfile: str
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


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return the first ``major.minor[.patch]`` found in ``text``."""
    match = _VERSION.search(text)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _supports(platforms: tuple[str, ...], platform: PlatformInfo) -> bool:
    return not platforms or any(key in platform.artifact_keys for key in platforms)


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

    def supports(self, platform: PlatformInfo) -> bool:
        return _supports(self.platforms, platform)

    def artifact_for(self, platform: PlatformInfo) -> Artifact:
        for key in platform.artifact_keys:
            if artifact := self.artifacts.get(key):
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

    def supports(self, platform: PlatformInfo) -> bool:
        return _supports(self.platforms, platform)


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    name: str
    description: str
    components: tuple[Component, ...]


@dataclass(frozen=True, slots=True)
class Manifest:
    """Validated union of ``deps.lock.toml`` and ``profiles.toml``."""

    version: str
    components: Mapping[str, Component]
    profiles: Mapping[str, Profile]
    dependencies_path: Path
    profiles_path: Path

    @classmethod
    def load(
        cls,
        dependencies_path: str | Path,
        profiles_path: str | Path,
    ) -> Manifest:
        dependencies_path = Path(dependencies_path)
        profiles_path = Path(profiles_path)
        try:
            with dependencies_path.open("rb") as stream:
                dependencies_data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ManifestError(f"cannot read {dependencies_path}: {exc}") from exc
        try:
            with profiles_path.open("rb") as stream:
                profiles_data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ManifestError(f"cannot read {profiles_path}: {exc}") from exc
        return cls.from_data(
            dependencies_data,
            profiles_data,
            dependencies_path=dependencies_path,
            profiles_path=profiles_path,
        )

    @classmethod
    def from_data(
        cls,
        dependencies_data: Mapping[str, Any],
        profiles_data: Mapping[str, Any],
        *,
        dependencies_path: Path = Path("deps.lock.toml"),
        profiles_path: Path = Path("profiles.toml"),
    ) -> Manifest:
        _validate_schema_version(dependencies_data, "dependency lock")
        _validate_schema_version(profiles_data, "profiles manifest")
        raw_components = _table(dependencies_data.get("components"), "components")
        raw_profiles = _table(profiles_data.get("profiles"), "profiles")
        components = {
            name: _parse_component(name, _table(data, f"components.{name}"))
            for name, data in raw_components.items()
        }
        profiles = {
            name: _parse_profile(name, _table(data, f"profiles.{name}"))
            for name, data in raw_profiles.items()
        }
        if not components:
            raise ManifestError("the dependency lock contains no components")
        if not profiles:
            raise ManifestError("the profiles manifest contains no profiles")
        manifest = cls(
            version=_manifest_version(dependencies_data),
            components=MappingProxyType(components),
            profiles=MappingProxyType(profiles),
            dependencies_path=Path(dependencies_path),
            profiles_path=Path(profiles_path),
        )
        # Fail before touching an installation if any profile has an unknown
        # parent/component or an inheritance cycle.
        for name in profiles:
            manifest.resolve_profile(name)
        return manifest

    def resolve_profile(
        self,
        name: str,
        platform: PlatformInfo | None = None,
    ) -> ResolvedProfile:
        if name not in self.profiles:
            choices = ", ".join(sorted(self.profiles))
            raise ManifestError(
                f"unknown profile {name!r}; available profiles: {choices}"
            )
        ordered_names: list[str] = []
        visited: set[str] = set()

        def visit(profile_name: str, stack: tuple[str, ...]) -> None:
            if profile_name in stack:
                cycle = " -> ".join((*stack, profile_name))
                raise ManifestError(f"profile inheritance cycle: {cycle}")
            if profile_name in visited:
                return
            try:
                profile = self.profiles[profile_name]
            except KeyError as exc:
                raise ManifestError(f"unknown parent profile {profile_name!r}") from exc
            for parent in profile.extends:
                visit(parent, (*stack, profile_name))
            for component_name in profile.components:
                if component_name not in self.components:
                    raise ManifestError(
                        f"profile {profile_name!r} references unknown component "
                        f"{component_name!r}"
                    )
                if component_name not in ordered_names:
                    ordered_names.append(component_name)
            visited.add(profile_name)

        visit(name, ())
        profile = self.profiles[name]
        components = tuple(self.components[item] for item in ordered_names)
        if platform is not None:
            if not profile.supports(platform):
                raise UnsupportedPlatformError(
                    f"profile {name!r} does not support {platform.key}"
                )
            components = tuple(item for item in components if item.supports(platform))
            for component in components:
                if component.kind == "download":
                    component.artifact_for(platform)
        return ResolvedProfile(
            name=name,
            description=profile.description,
            components=components,
        )


def _manifest_version(data: Mapping[str, Any]) -> str:
    release = data.get("release")
    if isinstance(release, dict) and release.get("version") is not None:
        return _string(release["version"], "release.version")
    for key in ("version", "lock_version", "manifest_version"):
        if data.get(key) is not None:
            value = data[key]
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value)
            raise ManifestError(f"{key} must be a string or integer")
    return "unversioned"


def _parse_component(name: str, data: Mapping[str, Any]) -> Component:
    kind = _string(
        data.get("kind"), f"components.{name}.kind", default="download"
    ).lower()
    if kind not in {"download", "local", "uv-runtime", "system"}:
        raise ManifestError(
            f"components.{name}.kind must be 'download', 'local', 'uv-runtime', "
            "or 'system'"
        )
    version = str(data.get("version", "local" if kind == "local" else "unknown"))
    platforms = _platform_list(data.get("platforms"), f"components.{name}.platforms")
    if kind == "uv-runtime":
        return _parse_runtime_component(name, version, data, platforms)
    if kind == "system":
        return _parse_system_component(name, data, platforms)
    destination = validate_relative_path(
        _string(data.get("destination"), f"components.{name}.destination", default="."),
        f"components.{name}.destination",
    )
    if kind == "local":
        source_path = validate_relative_path(
            _string(data.get("path"), f"components.{name}.path"),
            f"components.{name}.path",
        )
        return Component(
            name=name,
            version=version,
            kind=kind,
            destination=destination,
            path=source_path,
            artifacts=MappingProxyType({}),
            platforms=platforms,
        )

    raw_artifacts = _table(data.get("artifacts"), f"components.{name}.artifacts")
    artifacts: dict[str, Artifact] = {}
    for platform_key, raw_artifact in raw_artifacts.items():
        artifact_data = _table(
            raw_artifact, f"components.{name}.artifacts.{platform_key}"
        )
        sha256 = _string(
            artifact_data.get("sha256"),
            f"components.{name}.artifacts.{platform_key}.sha256",
        ).lower()
        if not _SHA256.fullmatch(sha256):
            raise ManifestError(
                f"components.{name}.artifacts.{platform_key}.sha256 must be "
                "64 hexadecimal characters"
            )
        size = artifact_data.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ManifestError(
                f"components.{name}.artifacts.{platform_key}.size must be a "
                "positive integer"
            )
        archive = _string(
            artifact_data.get("archive"),
            f"components.{name}.artifacts.{platform_key}.archive",
            default="none",
        ).lower()
        if archive not in _ARCHIVES:
            raise ManifestError(
                f"components.{name}.artifacts.{platform_key}.archive must be one of: "
                + ", ".join(sorted(_ARCHIVES))
            )
        strip_components = artifact_data.get("strip_components", 0)
        if (
            not isinstance(strip_components, int)
            or isinstance(strip_components, bool)
            or strip_components < 0
        ):
            raise ManifestError(
                f"components.{name}.artifacts.{platform_key}.strip_components "
                "must be a non-negative integer"
            )
        artifact_destination = validate_relative_path(
            _string(
                artifact_data.get("destination"),
                f"components.{name}.artifacts.{platform_key}.destination",
                default=destination,
            ),
            f"components.{name}.artifacts.{platform_key}.destination",
        )
        artifacts[platform_key.lower()] = Artifact(
            platform=platform_key.lower(),
            url=_string(
                artifact_data.get("url"),
                f"components.{name}.artifacts.{platform_key}.url",
            ),
            sha256=sha256,
            size=size,
            archive=archive,
            strip_components=strip_components,
            destination=artifact_destination,
            exclude=tuple(
                validate_relative_path(
                    item,
                    f"components.{name}.artifacts.{platform_key}.exclude",
                )
                for item in _string_list(
                    artifact_data.get("exclude"),
                    f"components.{name}.artifacts.{platform_key}.exclude",
                )
            ),
        )
    if not artifacts:
        raise ManifestError(f"component {name!r} contains no artifacts")
    return Component(
        name=name,
        version=version,
        kind=kind,
        destination=destination,
        path=None,
        artifacts=MappingProxyType(artifacts),
        platforms=platforms,
    )


def _parse_system_component(
    name: str,
    data: Mapping[str, Any],
    platforms: tuple[str, ...],
) -> Component:
    label = f"components.{name}"
    min_version = parse_version(
        _string(data.get("min_version"), f"{label}.min_version")
    )
    if min_version is None:
        raise ManifestError(f"{label}.min_version must look like 1.2.3")
    check_contains = data.get("check_contains")
    if check_contains is not None:
        check_contains = _string(check_contains, f"{label}.check_contains")
    return Component(
        name=name,
        version="system",
        kind="system",
        destination=".",
        path=None,
        artifacts=MappingProxyType({}),
        platforms=platforms,
        system=SystemProgram(
            command=_string(data.get("command"), f"{label}.command"),
            min_version=min_version,
            check_args=_string_list(data.get("check_args"), f"{label}.check_args"),
            check_contains=check_contains,
        ),
    )


def _platform_list(value: object, label: str) -> tuple[str, ...]:
    return tuple(item.lower() for item in _string_list(value, label))


def _parse_runtime_component(
    name: str,
    version: str,
    data: Mapping[str, Any],
    platforms: tuple[str, ...],
) -> Component:
    project = validate_relative_path(
        _string(data.get("project"), f"components.{name}.project"),
        f"components.{name}.project",
    )
    destination = validate_relative_path(
        _string(
            data.get("destination"),
            f"components.{name}.destination",
            default=project,
        ),
        f"components.{name}.destination",
    )
    lockfile = validate_relative_path(
        _string(
            data.get("lockfile"),
            f"components.{name}.lockfile",
            default=f"{project}/uv.lock",
        ),
        f"components.{name}.lockfile",
    )
    environment = validate_relative_path(
        _string(
            data.get("environment"),
            f"components.{name}.environment",
            default="runtime/venv",
        ),
        f"components.{name}.environment",
    )
    runtime = UvRuntime(
        project=project,
        lockfile=lockfile,
        environment=environment,
        python_version=_string(
            data.get("python_version"), f"components.{name}.python_version"
        ),
        extras=_string_list(data.get("extras"), f"components.{name}.extras"),
    )
    return Component(
        name=name,
        version=version,
        kind="uv-runtime",
        destination=destination,
        path=project,
        artifacts=MappingProxyType({}),
        runtime=runtime,
        platforms=platforms,
    )


def _parse_profile(name: str, data: Mapping[str, Any]) -> Profile:
    return Profile(
        name=name,
        description=_string(
            data.get("description"), f"profiles.{name}.description", default=name
        ),
        components=_string_list(data.get("components"), f"profiles.{name}.components"),
        extends=_string_list(data.get("extends"), f"profiles.{name}.extends"),
        platforms=_platform_list(data.get("platforms"), f"profiles.{name}.platforms"),
    )


def _validate_schema_version(data: Mapping[str, Any], label: str) -> None:
    value = data.get("schema_version")
    if not isinstance(value, int) or isinstance(value, bool) or value != 1:
        raise ManifestError(f"{label} schema_version must be 1")
