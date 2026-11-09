"""Transactional assembly, promotion, preservation, and rollback."""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .archives import extract_archive
from .downloads import ArtifactCache
from .errors import InstallerError, OfflineRuntimeUnsupportedError, PromotionError
from .manifest import (
    Component,
    GpuRequirement,
    Manifest,
    ResolvedProfile,
    SystemProgram,
    parse_version,
    validate_relative_path,
)
from .platforms import PlatformInfo
from .receipt import InstallationReceipt, InstalledComponent
from .runtime import UvRuntimeBuilder
from .version import VERSION

DEFAULT_PRESERVED_PATHS = (
    "portable_config/mpv-local.conf",
    "portable_config/rife_config_local.py",
    # mpvfin's server, and its saved login and device ID.
    "portable_config/script-opts/mpvfin.conf",
    "portable_config/script-opts/mpvfin-state.json",
    # mpv resume points, and caches such as TensorRT engines (slow to rebuild).
    "portable_config/watch_later",
    "portable_config/cache",
)


def _run_output(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(command), check=False, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallerError(f"cannot run {command[0]!r}: {exc}") from exc
    return completed.stdout + completed.stderr


def check_system_program(
    program: SystemProgram,
    *,
    run: Callable[[Sequence[str]], str] = _run_output,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, str]:
    """Return the path and version of a required host program, or raise."""
    path = which(program.command)
    if path is None:
        raise InstallerError(
            f"{program.command!r} was not found on PATH; install it with your "
            "system package manager first"
        )
    version = parse_version(run((path, "--no-config", "--version")))
    required = ".".join(map(str, program.min_version))
    if version is None or version < program.min_version:
        found = ".".join(map(str, version)) if version else "an unknown version"
        raise InstallerError(
            f"{path} is {found}; this profile needs {program.command} {required} "
            "or newer"
        )
    if program.check_contains and program.check_contains not in run(
        (path, "--no-config", *program.check_args)
    ):
        raise InstallerError(
            f"{path} was built without {program.check_contains!r} support, which "
            "this profile needs"
        )
    return path, ".".join(map(str, version))


def check_gpu(
    gpu: GpuRequirement,
    *,
    run: Callable[[Sequence[str]], str] = _run_output,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Return the name of a graphics card meeting the requirement, or raise."""
    advice = "choose another profile"
    tool = which("nvidia-smi")
    if tool is None:
        raise InstallerError(
            f"this profile needs {gpu.requirement}, but no NVIDIA driver was "
            f"found; {advice}"
        )
    output = run((tool, "--query-gpu=name", "--format=csv,noheader"))
    names = [line.strip() for line in output.splitlines() if line.strip()]
    for name in names:
        if re.search(gpu.matches, name):
            return name
    found = ", ".join(names) or "no GPU"
    raise InstallerError(
        f"this profile needs {gpu.requirement}, but found {found}; {advice}"
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
        system_check: Callable[[SystemProgram], tuple[str, str]] = check_system_program,
        gpu_check: Callable[[GpuRequirement], str] = check_gpu,
        preserved_paths: tuple[str, ...] = DEFAULT_PRESERVED_PATHS,
        report: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self.manifest = manifest
        self.cache = cache
        self.source_root = Path(source_root).resolve()
        self.platform = platform or PlatformInfo.detect()
        self.runtime_builder = runtime_builder or UvRuntimeBuilder()
        self.system_check = system_check
        self.gpu_check = gpu_check
        self.report = report
        self.preserved_paths = tuple(
            validate_relative_path(item, "preserved path") for item in preserved_paths
        )

    def install(
        self,
        profile_name: str,
        install_dir: str | Path,
        *,
        offline: bool = False,
        carry_from: Path | None = None,
        optional: tuple[str, ...] = (),
        answers: Mapping[str, str] | None = None,
    ) -> InstallResult:
        profile = self.manifest.resolve_profile(
            profile_name, self.platform, optional=optional
        )
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
        # Kept short: Python inside the staging folder can hit Windows' path limit.
        staging = target.parent / f".{target.name}.staging-{uuid.uuid4().hex[:8]}"
        staging.mkdir()
        installed_components: list[InstalledComponent] = []
        try:
            total = len(profile.components)
            for index, component in enumerate(profile.components, 1):
                self.report(f"[{index}/{total}] {component.name}")
                installed_components.append(
                    self._stage_component(component, staging, offline=offline)
                )
            runtime = next(
                (item.runtime for item in profile.components if item.runtime), None
            )
            if runtime is not None:
                self.runtime_builder.retarget(
                    runtime=runtime,
                    staging_root=staging,
                    target_root=target,
                    platform=self.platform,
                )
            self._write_managed_profile(staging, profile)
            self._preserve_user_files(carry_from or target, staging)
            self._apply_setup(staging, profile, answers or {})
            receipt = InstallationReceipt.create(
                release=VERSION,
                profile=profile.name,
                platform=self.platform.key,
                components=tuple(installed_components),
            )
            receipt.write(staging)
            previous = promote_staging(staging, target)
            if runtime is not None:
                self.runtime_builder.register_vsscript(runtime, target, self.platform)
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
            destination = _inside(staging, component.destination)
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
        destination = _inside(staging, artifact.destination)
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

    def _write_managed_profile(self, staging: Path, profile: ResolvedProfile) -> None:
        portable_config = staging / "portable_config"
        if not portable_config.is_dir():
            return
        settings = {
            "tensorrt": "tensorrt" in profile.extras,
            **profile.settings,
            **{c.config_key: c.version for c in profile.components if c.config_key},
        }
        destination = portable_config / "rife_profile.py"
        destination.write_text(
            "# Generated by mpv-enhanced; edit rife_config_local.py instead.\n"
            + "".join(f"{key} = {value!r}\n" for key, value in settings.items()),
            encoding="utf-8",
            newline="\n",
        )

    def _apply_setup(
        self,
        staging: Path,
        profile: ResolvedProfile,
        answers: Mapping[str, str],
    ) -> None:
        for component in profile.components:
            setup = component.setup
            if setup is None:
                continue
            path = _inside(staging, setup.file)
            if component.name in answers:
                path.parent.mkdir(parents=True, exist_ok=True)
                text = setup.line.format(answer=answers[component.name])
                path.write_text(text, encoding="utf-8")
            elif not path.exists():
                self.report(
                    f"Note: {component.name} has no {setup.question} yet; "
                    f"set it in {setup.file}"
                )

    def _preserve_user_files(self, current: Path, staging: Path) -> None:
        for relative in self.preserved_paths:
            old_path = _inside(current, relative)
            new_path = _inside(staging, relative)
            if old_path.is_dir() and not old_path.is_symlink():
                shutil.copytree(old_path, new_path, symlinks=True, dirs_exist_ok=True)
                continue
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
