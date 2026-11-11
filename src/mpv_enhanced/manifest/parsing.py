"""Turns the tables of ``manifest.toml`` into components, profiles, and methods."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from ..errors import ManifestError
from .fields import (
    _expand,
    _integer,
    _integer_list,
    _pattern,
    _platform_key,
    _platforms,
    _string,
    _string_list,
    _table,
    parse_version,
    validate_relative_path,
)
from .model import (
    Artifact,
    Component,
    ComponentSetup,
    GpuRequirement,
    InterpolationOptions,
    Method,
    MethodChoice,
    Profile,
    Resolution,
    SystemProgram,
    UvRuntime,
)

_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_CHOICE_ID = re.compile(r"[a-z0-9][a-z0-9.-]*\Z")
_ARCHIVES = frozenset({"zip", "zip+tar.gz", "none"})
_RUNTIME_ENVIRONMENT = "runtime/venv"
_ARTIFACT_FIELDS = ("url", "sha256", "archive", "exclude")


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


def _with_inherited_method(
    name: str,
    methods: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Apply a method's one-level ``extends``: a child adds to its parent's lists."""
    table = _table(methods[name], f"methods.{name}")
    parent = table.get("extends")
    if parent is None:
        return table
    if parent not in methods:
        raise ManifestError(f"method {name!r} extends unknown method {parent!r}")
    inherited = _table(methods[parent], f"methods.{parent}")
    merged = {"os": inherited.get("os"), **table}
    for key in ("components", "extras", "choices"):
        merged[key] = [*inherited.get(key, []), *table.get(key, [])]
    return merged


def _parse_method(name: str, data: Mapping[str, Any]) -> Method:
    label = f"methods.{name}"
    preselect = data.get("preselect", True)
    if not isinstance(preselect, bool):
        raise ManifestError(f"{label}.preselect must be true or false")
    raw_choices = data.get("choices")
    if not isinstance(raw_choices, list) or not raw_choices:
        raise ManifestError(f"{label}.choices must list at least one choice")
    return Method(
        name=name,
        description=_string(data.get("description"), f"{label}.description"),
        components=_string_list(data.get("components"), f"{label}.components"),
        extras=_string_list(data.get("extras"), f"{label}.extras"),
        platforms=_platforms(data, label),
        choices=tuple(
            _parse_choice(item, f"{label}.choices[{index}]")
            for index, item in enumerate(raw_choices)
        ),
        preselect=preselect,
    )


def _parse_choice(value: object, label: str) -> MethodChoice:
    data = _table(value, label)
    identifier = _string(data.get("id"), f"{label}.id")
    # The id is passed through mpv's filter options, and "none" is reserved.
    if not _CHOICE_ID.fullmatch(identifier) or identifier == "none":
        raise ManifestError(f"{label}.id is not a usable choice id: {identifier!r}")
    return MethodChoice(
        id=identifier,
        title=_string(data.get("title"), f"{label}.title"),
        rank=_integer(data.get("rank"), f"{label}.rank"),
        settings=MappingProxyType(
            dict(_table(data.get("settings"), f"{label}.settings"))
        ),
    )


def _parse_options(value: object) -> InterpolationOptions:
    label = "interpolation"
    data = _table(value, label)
    raw_resolutions = data.get("resolutions")
    if not isinstance(raw_resolutions, list) or not raw_resolutions:
        raise ManifestError(f"{label}.resolutions must list at least one resolution")
    resolutions = []
    for index, item in enumerate(raw_resolutions):
        entry = _table(item, f"{label}.resolutions[{index}]")
        resolutions.append(
            Resolution(
                label=_string(
                    entry.get("label"), f"{label}.resolutions[{index}].label"
                ),
                width=_integer(
                    entry.get("width"), f"{label}.resolutions[{index}].width"
                ),
                height=_integer(
                    entry.get("height"), f"{label}.resolutions[{index}].height"
                ),
            )
        )
    return InterpolationOptions(
        resolutions=tuple(resolutions),
        frame_rates=_integer_list(data.get("frame_rates"), f"{label}.frame_rates"),
        multipliers=_integer_list(data.get("multipliers"), f"{label}.multipliers"),
        fps_cutoffs=_integer_list(data.get("fps_cutoffs"), f"{label}.fps_cutoffs"),
        default_fps_cutoff=_integer(
            data.get("default_fps_cutoff"), f"{label}.default_fps_cutoff"
        ),
    )


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


def _parse_profile(name: str, data: Mapping[str, Any]) -> Profile:
    return Profile(
        name=name,
        description=_string(
            data.get("description"), f"profiles.{name}.description", default=name
        ),
        components=_string_list(data.get("components"), f"profiles.{name}.components"),
        extends=_string_list(data.get("extends"), f"profiles.{name}.extends"),
        platforms=_platforms(data, f"profiles.{name}"),
    )


def _validate_schema_version(data: Mapping[str, Any]) -> None:
    value = data.get("schema_version")
    if not isinstance(value, int) or isinstance(value, bool) or value != 1:
        raise ManifestError("manifest schema_version must be 1")
