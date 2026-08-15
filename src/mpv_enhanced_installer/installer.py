"""Transactional assembly, promotion, preservation, and rollback."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from .archives import ArchiveCommandRunner, extract_archive, run_archive_command
from .downloads import ArtifactCache
from .errors import InstallerError, OfflineRuntimeUnsupportedError, PromotionError
from .manifest import Component, Manifest, validate_relative_path
from .platforms import PlatformInfo
from .receipt import InstallationReceipt, InstalledComponent
from .runtime import UvRuntimeBuilder

DEFAULT_PRESERVED_PATHS = (
    "portable_config/mpv-local.conf",
    "portable_config/rife_config_local.py",
)


@dataclass(frozen=True, slots=True)
class InstallResult:
    install_dir: Path
    previous_dir: Path | None
    receipt: InstallationReceipt


class Installer:
    """Build an installation in a sibling staging directory, then promote it."""

    def __init__(
        self,
        manifest: Manifest,
        cache: ArtifactCache,
        *,
        source_root: str | Path,
        platform: PlatformInfo | None = None,
        runtime_builder: UvRuntimeBuilder | None = None,
        archive_runner: ArchiveCommandRunner = run_archive_command,
        preserved_paths: tuple[str, ...] = DEFAULT_PRESERVED_PATHS,
    ) -> None:
        self.manifest = manifest
        self.cache = cache
        self.source_root = Path(source_root).resolve()
        self.platform = platform or PlatformInfo.detect()
        self.runtime_builder = runtime_builder or UvRuntimeBuilder()
        self.archive_runner = archive_runner
        self.preserved_paths = tuple(
            validate_relative_path(item, "preserved path") for item in preserved_paths
        )

    def install(
        self,
        profile_name: str,
        install_dir: str | Path,
        *,
        offline: bool = False,
    ) -> InstallResult:
        profile = self.manifest.resolve_profile(profile_name, self.platform)
        if offline and any(
            component.kind == "uv-runtime" for component in profile.components
        ):
            raise OfflineRuntimeUnsupportedError(
                f"profile {profile.name!r} contains a uv-runtime component; "
                "--offline currently supports only profiles whose complete payload "
                "is already component-cached (for example, core)"
            )
        target = Path(install_dir).resolve()
        if target.parent == target:
            raise InstallerError(
                "the filesystem root cannot be an installation directory"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.staging-{uuid.uuid4().hex}"
        staging.mkdir()
        installed_components: list[InstalledComponent] = []
        try:
            for component in profile.components:
                installed_components.append(
                    self._stage_component(component, staging, offline=offline)
                )
            self._write_managed_profile(staging, profile.name)
            self._preserve_user_files(target, staging)
            receipt = InstallationReceipt.create(
                release=self.manifest.version,
                profile=profile.name,
                platform=self.platform.key,
                components=tuple(installed_components),
            )
            receipt.write(staging)
            previous = promote_staging(staging, target)
            return InstallResult(target, previous, receipt)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    def _stage_component(
        self,
        component: Component,
        staging: Path,
        *,
        offline: bool,
    ) -> InstalledComponent:
        if component.kind == "local":
            assert component.path is not None
            source = self._local_source(component.path, component.name)
            destination = _inside(staging, component.destination)
            _copy_local(source, destination)
            return InstalledComponent(
                name=component.name,
                version=component.version,
                kind="local",
                source=component.path,
            )

        if component.kind == "uv-runtime":
            assert component.runtime is not None
            source = self._local_source(component.runtime.project, component.name)
            destination = _inside(staging, component.destination)
            _copy_local(source, destination)
            self.runtime_builder.build(
                component.runtime,
                source_root=self.source_root,
                staging_root=staging,
                project_directory=destination,
                platform=self.platform,
                cache_root=self.cache.root,
                offline=offline,
            )
            self.runtime_builder.write_launcher(
                staging_root=staging,
                runtime=component.runtime,
                platform=self.platform,
            )
            return InstalledComponent(
                name=component.name,
                version=component.version,
                kind="uv-runtime",
                source=component.runtime.project,
            )

        artifact = component.artifact_for(self.platform)
        cached = self.cache.fetch(artifact, offline=offline)
        destination = _inside(staging, artifact.destination)
        if artifact.archive == "none":
            if artifact.strip_components:
                raise InstallerError(
                    f"non-archive component {component.name!r} cannot strip components"
                )
            if destination.exists():
                raise InstallerError(
                    f"component {component.name!r} collides with {artifact.destination}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, destination)
        else:
            extract_archive(
                cached,
                destination,
                archive_type=artifact.archive,
                strip_components=artifact.strip_components,
                exclude=artifact.exclude,
                seven_zip_executable=self._staged_seven_zip(staging),
                runner=self.archive_runner,
            )
        return InstalledComponent(
            name=component.name,
            version=component.version,
            kind="download",
            sha256=artifact.sha256,
            source=artifact.url,
        )

    def _staged_seven_zip(self, staging: Path) -> Path | None:
        filename = "7zr.exe" if self.platform.system == "windows" else "7zr"
        candidate = staging / "tools" / filename
        return candidate if candidate.is_file() else None

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

    def _write_managed_profile(self, staging: Path, profile_name: str) -> None:
        portable_config = staging / "portable_config"
        if not portable_config.is_dir():
            return
        destination = portable_config / "rife_profile.py"
        destination.write_text(
            "# Generated by mpv-enhanced; edit rife_config_local.py instead.\n"
            f"tensorrt = {profile_name == 'rife-tensorrt'}\n",
            encoding="utf-8",
            newline="\n",
        )

    def _preserve_user_files(self, current: Path, staging: Path) -> None:
        for relative in self.preserved_paths:
            old_path = _inside(current, relative)
            new_path = _inside(staging, relative)
            if old_path.is_file():
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_path, new_path)
                continue
            if new_path.exists():
                continue
            example = new_path.with_name(f"{new_path.name}.example")
            if example.is_file():
                shutil.copy2(example, new_path)

    @staticmethod
    def rollback(install_dir: str | Path) -> None:
        rollback_installation(Path(install_dir).resolve())


def promote_staging(staging: Path, target: Path) -> Path | None:
    """Promote *staging* and retain exactly one sibling ``.previous`` tree."""
    if not staging.is_dir():
        raise PromotionError(f"staging directory does not exist: {staging}")
    if staging.parent.resolve() != target.parent.resolve():
        raise PromotionError("staging and installation directories must be siblings")
    previous = target.with_name(f"{target.name}.previous")
    stale: Path | None = None
    target_moved = False
    try:
        if target.exists():
            if previous.exists():
                stale = previous.with_name(f".{previous.name}.stale-{uuid.uuid4().hex}")
                previous.rename(stale)
            target.rename(previous)
            target_moved = True
        staging.rename(target)
    except OSError as exc:
        try:
            if target_moved and previous.exists() and not target.exists():
                previous.rename(target)
            if stale is not None and stale.exists() and not previous.exists():
                stale.rename(previous)
        except OSError as recovery_exc:
            raise PromotionError(
                f"promotion failed ({exc}) and recovery failed ({recovery_exc})"
            ) from recovery_exc
        raise PromotionError(f"could not promote staged installation: {exc}") from exc
    if stale is not None and stale.exists():
        # Promotion is already complete. A locked file in an older backup must
        # not turn a successful install into a reported failure.
        shutil.rmtree(stale, ignore_errors=True)
    return previous if target_moved else None


def rollback_installation(target: Path) -> None:
    """Atomically swap the current and previous installation trees."""
    previous = target.with_name(f"{target.name}.previous")
    if not previous.is_dir():
        raise PromotionError(f"no previous installation exists at {previous}")
    if not target.exists():
        try:
            previous.rename(target)
            return
        except OSError as exc:
            raise PromotionError(
                f"could not restore previous installation: {exc}"
            ) from exc
    temporary = target.with_name(f".{target.name}.rollback-{uuid.uuid4().hex}")
    try:
        target.rename(temporary)
    except OSError as exc:
        raise PromotionError(f"could not move the current installation: {exc}") from exc
    try:
        previous.rename(target)
    except OSError as exc:
        try:
            temporary.rename(target)
        except OSError as recovery_exc:
            raise PromotionError(
                f"rollback failed ({exc}) and recovery failed ({recovery_exc})"
            ) from recovery_exc
        raise PromotionError(f"could not restore previous installation: {exc}") from exc
    try:
        temporary.rename(previous)
    except OSError as exc:
        try:
            target.rename(previous)
            temporary.rename(target)
        except OSError as recovery_exc:
            raise PromotionError(
                f"rollback swap failed ({exc}) and recovery failed ({recovery_exc})"
            ) from recovery_exc
        raise PromotionError(
            f"could not retain the replaced installation: {exc}"
        ) from exc


def _inside(root: Path, relative: str) -> Path:
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
