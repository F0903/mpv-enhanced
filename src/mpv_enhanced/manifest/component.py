"""A component: a download, local files, a Python runtime, or a host requirement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..errors import ManifestError, UnsupportedPlatformError
from ..platforms import PlatformInfo
from .artifact import Artifact, _parse_artifacts
from .fields import (
    _expand,
    _pattern,
    _platforms,
    _string,
    _string_list,
    _table,
    parse_version,
    validate_relative_path,
)
from .uv_runtime import _RUNTIME_ENVIRONMENT, UvRuntime


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


def _with_inherited_fields(
    name: str,
    components: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Apply a component's one-level ``extends``."""
    table = _table(components[name], f"components.{name}")
    parent = table.get("extends")
    if parent is None:
        return table
    if parent not in components:
        raise ManifestError(f"component {name!r} extends unknown component {parent!r}")
    return {**_table(components[parent], f"components.{parent}"), **table}


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
    optional = data.get("optional", False)
    if not isinstance(optional, bool):
        raise ManifestError(f"components.{name}.optional must be true or false")
    return Component(
        name=name,
        version=version,
        kind=kind,
        destination=destination,
        path=None,
        artifacts=MappingProxyType(artifacts),
        platforms=tuple(artifacts),
        optional=optional,
        description=(
            _string(data.get("description"), f"components.{name}.description")
            if optional
            else ""
        ),
        setup=_parse_setup(name, data["setup"]) if "setup" in data else None,
    )


def _parse_setup(name: str, value: object) -> ComponentSetup:
    label = f"components.{name}.setup"
    data = _table(value, label)
    line = _string(data.get("line"), f"{label}.line")
    if _expand(line, {"answer": ""}, f"{label}.line") == line:
        raise ManifestError(f"{label}.line must contain {{answer}}")
    # _string strips, and the line's own newline has to survive.
    return ComponentSetup(
        question=_string(data.get("question"), f"{label}.question"),
        file=validate_relative_path(
            _string(data.get("file"), f"{label}.file"), f"{label}.file"
        ),
        line=str(data["line"]),
        matches=_pattern(data.get("matches"), f"{label}.matches"),
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
    matches = _pattern(data.get("matches"), f"{label}.matches")
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
