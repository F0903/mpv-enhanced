"""A file a component downloads, and how ``manifest.toml`` describes it."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import ManifestError
from ..parsed import as_list
from .fields import (
    expand,
    platform_key,
    read_string,
    read_string_list,
    read_table,
    validate_relative_path,
)
from .uv_runtime import site_packages


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


_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_ARCHIVES = frozenset({"zip", "zip+tar.gz", "none"})
_ARTIFACT_FIELDS = ("url", "sha256", "archive", "exclude")


def parse_artifacts(
    name: str,
    data: Mapping[str, object],
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
    for system, entry in read_table(data.get("os"), label).items():
        arch_label = f"{label}.{system}.arch"
        raw = read_table(entry, f"{label}.{system}").get("arch")
        listed = as_list(raw)
        architectures: Mapping[str, object] = (
            read_table(raw, arch_label)
            if listed is None
            else {str(arch): {} for arch in listed}
        )
        for arch, overrides in architectures.items():
            artifact_label = f"{arch_label}.{arch}"
            key = platform_key(system, arch, artifact_label)
            values: dict[str, str] = {}
            if "version" in data:
                values["version"] = str(data["version"])
            if python_version is not None:
                values["site_packages"] = site_packages(
                    key.split("-")[0], python_version
                )
            artifacts[key] = _parse_artifact(
                {**shared, **read_table(overrides, artifact_label)},
                artifact_label,
                default_destination,
                values,
            )
    return artifacts


def _parse_artifact(
    data: Mapping[str, object],
    label: str,
    default_destination: str,
    values: Mapping[str, str],
) -> Artifact:
    sha256 = read_string(data.get("sha256"), f"{label}.sha256").lower()
    if not _SHA256.fullmatch(sha256):
        raise ManifestError(f"{label}.sha256 must be 64 hexadecimal characters")
    archive = read_string(
        data.get("archive"), f"{label}.archive", default="none"
    ).lower()
    if archive not in _ARCHIVES:
        raise ManifestError(
            f"{label}.archive must be one of: " + ", ".join(sorted(_ARCHIVES))
        )
    return Artifact(
        url=expand(
            read_string(data.get("url"), f"{label}.url"), values, f"{label}.url"
        ),
        sha256=sha256,
        archive=archive,
        destination=validate_relative_path(
            expand(
                read_string(
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
            for item in read_string_list(data.get("exclude"), f"{label}.exclude")
        ),
    )
