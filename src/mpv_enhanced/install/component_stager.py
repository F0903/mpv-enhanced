"""Putting one component into a staging directory."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import final

from ..archives import extract_archive
from ..downloads import ArtifactCache
from ..errors import InstallerError
from ..manifest import Component, GpuRequirement, SystemProgram
from ..platforms import PlatformInfo
from .receipt import InstalledComponent
from .runtime import UvRuntimeBuilder
from .system import check_gpu, check_system_program


@final
class ComponentStager:
    """Copy, download, build, or check a component, and say what was installed."""

    def __init__(
        self,
        cache: ArtifactCache,
        *,
        source_root: Path,
        platform: PlatformInfo,
        runtime_builder: UvRuntimeBuilder,
        system_check: Callable[[SystemProgram], tuple[str, str]] = check_system_program,
        gpu_check: Callable[[GpuRequirement], str] = check_gpu,
    ) -> None:
        self.cache = cache
        self.source_root = source_root
        self.platform = platform
        self.runtime_builder = runtime_builder
        self.system_check = system_check
        self.gpu_check = gpu_check

    def stage(
        self,
        component: Component,
        staging: Path,
        *,
        offline: bool,
    ) -> InstalledComponent:
        if component.kind == "local":
            assert component.path is not None
            source = self._local_source(component.path, component.name)
            destination = inside(staging, component.destination)
            _copy_local(source, destination)
            return InstalledComponent(
                name=component.name,
                version=component.version,
                kind="local",
                source=component.path,
            )

        if component.kind == "system":
            assert component.system is not None
            path, version = self.system_check(component.system)
            return InstalledComponent(
                name=component.name, version=version, kind="system", source=path
            )

        if component.kind == "gpu":
            assert component.gpu is not None
            return InstalledComponent(
                name=component.name,
                version=self.gpu_check(component.gpu),
                kind="gpu",
            )

        if component.kind == "uv-runtime":
            assert component.runtime is not None
            source = self._local_source(component.runtime.project, component.name)
            destination = inside(staging, component.destination)
            _copy_local(source, destination)
            self.runtime_builder.build(
                component.runtime,
                staging_root=staging,
                project_directory=destination,
                platform=self.platform,
                cache_root=self.cache.root,
                offline=offline,
            )
            return InstalledComponent(
                name=component.name,
                version=component.version,
                kind="uv-runtime",
                source=component.runtime.project,
            )

        artifact = component.artifact_for(self.platform)
        cached = self.cache.fetch(artifact, offline=offline)
        destination = inside(staging, artifact.destination)
        if artifact.archive == "none":
            # vsrife's wheel ships 0-byte model placeholders; only those may be
            # replaced, so a real collision between components still fails.
            if destination.exists() and not (
                destination.is_file() and destination.stat().st_size == 0
            ):
                raise InstallerError(
                    f"component {component.name!r} collides with {artifact.destination}"
                )
            # Unlink first: uv hardlinks venv files to its cache, and writing
            # through the link would corrupt the cached wheel.
            destination.unlink(missing_ok=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, destination)
        else:
            extract_archive(
                cached,
                destination,
                archive_type=artifact.archive,
                exclude=artifact.exclude,
            )
        return InstalledComponent(
            name=component.name,
            version=component.version,
            kind="download",
            sha256=artifact.sha256,
            source=artifact.url,
        )

    def _local_source(self, relative: str, component_name: str) -> Path:
        unresolved = self.source_root.joinpath(*relative.split("/"))
        if unresolved.is_symlink():
            raise InstallerError(
                f"local component {component_name!r} is a symbolic link: {unresolved}"
            )
        source = unresolved.resolve()
        try:
            source.relative_to(self.source_root)
        except ValueError as exc:
            raise InstallerError(
                f"local component {component_name!r} escapes the source root"
            ) from exc
        if not source.exists():
            raise InstallerError(
                f"local component {component_name!r} does not exist: {source}"
            )
        return source


def inside(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*relative.split("/"))
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise InstallerError(f"path escapes installation root: {relative!r}") from exc
    return candidate


def _copy_local(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise InstallerError(f"local component contains a symbolic link: {source}")
    if source.is_file():
        if destination.exists():
            raise InstallerError(f"local component collides with {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return
    if not source.is_dir():
        raise InstallerError(f"unsupported local component path: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        is_junction = getattr(item, "is_junction", lambda: False)()
        if item.is_symlink() or is_junction:
            raise InstallerError(f"local component contains a link or junction: {item}")
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            if target.exists() and not target.is_dir():
                raise InstallerError(f"local directory collides with {target}")
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            if target.exists():
                raise InstallerError(f"local file collides with {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
