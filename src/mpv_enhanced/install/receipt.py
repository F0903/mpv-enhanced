"""Installation receipt serialization."""

from __future__ import annotations

import json
import os
import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..errors import InstallerError

RECEIPT_FILENAME = ".mpv-enhanced-receipt.toml"


@dataclass(frozen=True, slots=True)
class InstalledComponent:
    name: str
    version: str
    kind: str
    sha256: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class InstallationReceipt:
    release: str
    profile: str
    platform: str
    installed_at: str
    components: tuple[InstalledComponent, ...]
    schema_version: int = 1
    methods: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        release: str,
        profile: str,
        platform: str,
        components: tuple[InstalledComponent, ...],
        methods: tuple[str, ...] = (),
    ) -> InstallationReceipt:
        return cls(
            release=release,
            profile=profile,
            platform=platform,
            installed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            components=components,
            methods=methods,
        )

    @classmethod
    def load(cls, path: str | Path) -> InstallationReceipt:
        path = Path(path)
        if path.is_dir():
            path /= RECEIPT_FILENAME
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
            components = tuple(
                InstalledComponent(
                    name=str(item["name"]),
                    version=str(item["version"]),
                    kind=str(item["kind"]),
                    sha256=str(item["sha256"]) if "sha256" in item else None,
                    source=str(item["source"]) if "source" in item else None,
                )
                for item in data.get("components", [])
            )
            return cls(
                schema_version=int(data["schema_version"]),
                release=str(data["release"]),
                profile=str(data["profile"]),
                platform=str(data["platform"]),
                installed_at=str(data["installed_at"]),
                components=components,
                methods=tuple(str(item) for item in data.get("methods", [])),
            )
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            tomllib.TOMLDecodeError,
        ) as exc:
            raise InstallerError(
                f"cannot read installation receipt {path}: {exc}"
            ) from exc

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        if (
            path.exists()
            and path.is_dir()
            or path.name != RECEIPT_FILENAME
            and not path.suffix
        ):
            path /= RECEIPT_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(self.to_toml(), encoding="utf-8", newline="\n")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def to_toml(self) -> str:
        lines = [
            f"schema_version = {self.schema_version}",
            f"release = {_quote(self.release)}",
            f"profile = {_quote(self.profile)}",
            f"platform = {_quote(self.platform)}",
            f"installed_at = {_quote(self.installed_at)}",
            f"methods = [{', '.join(_quote(item) for item in self.methods)}]",
        ]
        for component in self.components:
            lines.extend(
                [
                    "",
                    "[[components]]",
                    f"name = {_quote(component.name)}",
                    f"version = {_quote(component.version)}",
                    f"kind = {_quote(component.kind)}",
                ]
            )
            if component.sha256 is not None:
                lines.append(f"sha256 = {_quote(component.sha256)}")
            if component.source is not None:
                lines.append(f"source = {_quote(component.source)}")
        return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    # JSON basic strings are valid TOML basic strings for these scalar values.
    return json.dumps(value, ensure_ascii=False)
