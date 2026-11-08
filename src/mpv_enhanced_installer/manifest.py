"""Parsing and validation for ``manifest.toml``."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any

from .errors import ManifestError, UnsupportedPlatformError
from .platforms import PlatformInfo

_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_ARCHIVES = frozenset({"zip", "zip+tar.gz", "none"})
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_RUNTIME_ENVIRONMENT = "runtime/venv"
_ARTIFACT_FIELDS = ("url", "sha256", "archive", "exclude")


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


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return the first ``major.minor[.patch]`` found in ``text``."""
    match = _VERSION.search(text)
    return tuple(int(part or 0) for part in match.groups()) if match else None


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
    config_key: str | None = None
    gpu: GpuRequirement | None = None

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
    extras: tuple[str, ...] = ()
    settings: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    name: str
    description: str
    components: tuple[Component, ...]
    extras: tuple[str, ...] = ()
    settings: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Manifest:
    """Validated contents of ``manifest.toml``."""

    components: Mapping[str, Component]
    profiles: Mapping[str, Profile]
    default_profile: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        path = Path(path)
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
            python_version = (path.parent / ".python-version").read_text("utf-8")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ManifestError(f"cannot read {path}: {exc}") from exc
        return cls.from_data(data, python_version=python_version.strip())

    @classmethod
    def from_data(
        cls,
        data: Mapping[str, Any],
        *,
        python_version: str | None = None,
    ) -> Manifest:
        _validate_schema_version(data)
        raw_components = _table(data.get("components"), "components")
        raw_profiles = _table(data.get("profiles"), "profiles")
        components = {
            name: _parse_component(
                name, _with_inherited_fields(name, raw_components), python_version
            )
            for name in raw_components
        }
        profiles = {
            name: _parse_profile(name, _table(table, f"profiles.{name}"))
            for name, table in raw_profiles.items()
        }
        if not components:
            raise ManifestError("the manifest contains no components")
        if not profiles:
            raise ManifestError("the manifest contains no profiles")
        default_profile = data.get("default_profile")
        if default_profile is not None and default_profile not in profiles:
            raise ManifestError(f"unknown default profile {default_profile!r}")
        manifest = cls(
            components=MappingProxyType(components),
            profiles=MappingProxyType(profiles),
            default_profile=default_profile,
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
        extras: list[str] = []
        settings: dict[str, object] = {}
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
            extras.extend(item for item in profile.extras if item not in extras)
            settings.update(profile.settings)
            visited.add(profile_name)

        visit(name, ())
        profile = self.profiles[name]
        components = tuple(
            replace(item, runtime=replace(item.runtime, extras=tuple(extras)))
            if item.runtime
            else item
            for item in (self.components[item] for item in ordered_names)
        )
        if platform is not None:
            platforms = self._profile_platforms(name)
            if platforms and platform.key not in platforms:
                raise UnsupportedPlatformError(
                    f"profile {name!r} does not support {platform.key}"
                )
            components = tuple(item for item in components if item.supports(platform))
            for component in components:
                if component.platforms and platform.key not in component.platforms:
                    raise UnsupportedPlatformError(
                        f"component {component.name!r} does not support {platform.key}"
                    )
        return ResolvedProfile(
            name=name,
            description=profile.description,
            components=components,
            extras=tuple(extras),
            settings=settings,
        )

    def _profile_platforms(self, name: str) -> tuple[str, ...]:
        """A profile's own ``os`` table, or else the first one it inherits."""
        profile = self.profiles[name]
        return profile.platforms or next(
            filter(None, (self._profile_platforms(p) for p in profile.extends)), ()
        )


def _with_inherited_fields(
    name: str,
    components: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Apply a component's one-level ``extends``; ``config_key`` is never inherited."""
    table = _table(components[name], f"components.{name}")
    parent = table.get("extends")
    if parent is None:
        return table
    if parent not in components:
        raise ManifestError(f"component {name!r} extends unknown component {parent!r}")
    inherited = _table(components[parent], f"components.{parent}")
    return {**{k: v for k, v in inherited.items() if k != "config_key"}, **table}


def _parse_component(
    name: str,
    data: Mapping[str, Any],
    python_version: str | None,
) -> Component:
    kind = _string(
        data.get("kind"), f"components.{name}.kind", default="download"
    ).lower()
    if kind not in {"download", "local", "uv-runtime", "system", "gpu"}:
        raise ManifestError(
            f"components.{name}.kind must be 'download', 'local', 'uv-runtime', "
            "'system', or 'gpu'"
        )
    version = str(
        data.get(
            "version", {"local": "local", "uv-runtime": "locked"}.get(kind, "unknown")
        )
    )
    # Under a download, os.<name>.arch holds artifacts rather than a list.
    platforms = () if kind == "download" else _platforms(data, f"components.{name}")
    if kind == "uv-runtime":
        return _parse_runtime_component(name, version, data, platforms, python_version)
    if kind == "system":
        return _parse_system_component(name, data, platforms)
    if kind == "gpu":
        return _parse_gpu_component(name, data, platforms)
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

    artifacts = _parse_artifacts(name, data, destination, python_version)
    if not artifacts:
        raise ManifestError(f"component {name!r} contains no artifacts")
    config_key = data.get("config_key")
    return Component(
        name=name,
        version=version,
        kind=kind,
        destination=destination,
        path=None,
        artifacts=MappingProxyType(artifacts),
        platforms=tuple(artifacts),
        config_key=(
            _string(config_key, f"components.{name}.config_key")
            if config_key is not None
            else None
        ),
    )


def _parse_artifacts(
    name: str,
    data: Mapping[str, Any],
    default_destination: str,
    python_version: str | None,
) -> dict[str, Artifact]:
    """Read ``os.<name>.arch``, keyed by ``<name>-<arch>``.

    An array uses the component's own fields for each listed architecture; a
    table of ``<arch>`` entries can add to or override them per architecture.
    """
    label = f"components.{name}.os"
    shared = {key: data[key] for key in _ARTIFACT_FIELDS if key in data}
    artifacts: dict[str, Artifact] = {}
    for system, entry in _table(data.get("os"), label).items():
        arch_label = f"{label}.{system}.arch"
        raw = _table(entry, f"{label}.{system}").get("arch")
        architectures = (
            {arch: {} for arch in raw}
            if isinstance(raw, list)
            else _table(raw, arch_label)
        )
        for arch, overrides in architectures.items():
            artifact_label = f"{arch_label}.{arch}"
            key = _platform_key(system, arch, artifact_label)
            values = {}
            if "version" in data:
                values["version"] = str(data["version"])
            if python_version is not None:
                values["site_packages"] = _site_packages(
                    key.split("-")[0], python_version
                )
            artifacts[key] = _parse_artifact(
                {**shared, **_table(overrides, artifact_label)},
                artifact_label,
                default_destination,
                values,
            )
    return artifacts


def _site_packages(system: str, python_version: str) -> str:
    if system == "windows":
        return f"{_RUNTIME_ENVIRONMENT}/Lib/site-packages"
    major_minor = ".".join(python_version.split(".")[:2])
    return f"{_RUNTIME_ENVIRONMENT}/lib/python{major_minor}/site-packages"


def _expand(text: str, values: Mapping[str, str], label: str) -> str:
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        raise ManifestError(f"{label}: cannot expand {text!r}: {exc!r}") from exc


def _parse_artifact(
    data: Mapping[str, Any],
    label: str,
    default_destination: str,
    values: Mapping[str, str],
) -> Artifact:
    sha256 = _string(data.get("sha256"), f"{label}.sha256").lower()
    if not _SHA256.fullmatch(sha256):
        raise ManifestError(f"{label}.sha256 must be 64 hexadecimal characters")
    archive = _string(data.get("archive"), f"{label}.archive", default="none").lower()
    if archive not in _ARCHIVES:
        raise ManifestError(
            f"{label}.archive must be one of: " + ", ".join(sorted(_ARCHIVES))
        )
    return Artifact(
        url=_expand(_string(data.get("url"), f"{label}.url"), values, f"{label}.url"),
        sha256=sha256,
        archive=archive,
        destination=validate_relative_path(
            _expand(
                _string(
                    data.get("destination"),
                    f"{label}.destination",
                    default=default_destination,
                ),
                values,
                f"{label}.destination",
            ),
            f"{label}.destination",
        ),
        exclude=tuple(
            validate_relative_path(item, f"{label}.exclude")
            for item in _string_list(data.get("exclude"), f"{label}.exclude")
        ),
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


def _parse_gpu_component(
    name: str,
    data: Mapping[str, Any],
    platforms: tuple[str, ...],
) -> Component:
    label = f"components.{name}"
    matches = _string(data.get("matches"), f"{label}.matches")
    try:
        re.compile(matches)
    except re.error as exc:
        raise ManifestError(f"{label}.matches is not a valid pattern: {exc}") from exc
    return Component(
        name=name,
        version="gpu",
        kind="gpu",
        destination=".",
        path=None,
        artifacts=MappingProxyType({}),
        platforms=platforms,
        gpu=GpuRequirement(
            matches=matches,
            requirement=_string(data.get("requirement"), f"{label}.requirement"),
        ),
    )


def _platform_key(system: str, arch: str, label: str) -> str:
    try:
        return PlatformInfo.parse(f"{system}-{arch}").key
    except UnsupportedPlatformError as exc:
        raise ManifestError(f"{label}: {exc}") from exc


def _platforms(data: Mapping[str, Any], label: str) -> tuple[str, ...]:
    """Read ``os.<name>.arch = [...]`` as ``<name>-<arch>`` keys; no ``os`` means any."""
    if data.get("os") is None:
        return ()
    systems = _table(data["os"], f"{label}.os")
    if not systems:
        raise ManifestError(f"{label}.os must list at least one operating system")
    platforms: list[str] = []
    for system, entry in systems.items():
        arch_label = f"{label}.os.{system}.arch"
        architectures = _string_list(
            _table(entry, f"{label}.os.{system}").get("arch"), arch_label
        )
        if not architectures:
            raise ManifestError(f"{arch_label} must list at least one architecture")
        platforms += [_platform_key(system, arch, arch_label) for arch in architectures]
    return tuple(platforms)


def _parse_runtime_component(
    name: str,
    version: str,
    data: Mapping[str, Any],
    platforms: tuple[str, ...],
    python_version: str | None,
) -> Component:
    if python_version is None:
        raise ManifestError(f"component {name!r} needs a Python version")
    project = validate_relative_path(
        _string(data.get("project"), f"components.{name}.project"),
        f"components.{name}.project",
    )
    runtime = UvRuntime(
        project=project,
        environment=_RUNTIME_ENVIRONMENT,
        python_version=python_version,
        extras=(),
    )
    return Component(
        name=name,
        version=version,
        kind="uv-runtime",
        destination=project,
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
        platforms=_platforms(data, f"profiles.{name}"),
        extras=_string_list(data.get("extras"), f"profiles.{name}.extras"),
        settings=dict(_table(data.get("settings", {}), f"profiles.{name}.settings")),
    )


def _validate_schema_version(data: Mapping[str, Any]) -> None:
    value = data.get("schema_version")
    if not isinstance(value, int) or isinstance(value, bool) or value != 1:
        raise ManifestError("manifest schema_version must be 1")
