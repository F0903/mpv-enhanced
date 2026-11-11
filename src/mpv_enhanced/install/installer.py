"""Assembly of an installation in a staging directory, keeping the user's files."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import final

from .. import VERSION
from ..downloads import ArtifactCache
from ..errors import InstallerError, OfflineRuntimeUnsupportedError
from ..manifest import (
    GpuRequirement,
    Manifest,
    ResolvedProfile,
    SystemProgram,
    validate_relative_path,
)
from ..platforms import PlatformInfo
from .component_stager import ComponentStager, inside
from .receipt import InstallationReceipt, InstalledComponent
from .runtime import UvRuntimeBuilder
from .staging import promote_staging, rollback_installation
from .system import check_gpu, check_system_program

INSTALL_INFO = "portable_config/script-opts/mpv_enhanced_install.json"
DEFAULT_PRESERVED_PATHS = (
    "portable_config/mpv-local.conf",
    # The MPV Enhanced menu's settings.
    "portable_config/script-opts/mpv_enhanced.json",
    "portable_config/rife_config_local.py",
    # mpvfin's server, and its saved login and device ID.
    "portable_config/script-opts/mpvfin.conf",
    "portable_config/script-opts/mpvfin-state.json",
    # mpv resume points, and caches such as TensorRT engines (slow to rebuild).
    "portable_config/watch_later",
    "portable_config/cache",
)


@dataclass(frozen=True, slots=True)
class InstallResult:
    install_dir: Path
    previous_dir: Path | None
    receipt: InstallationReceipt


@final
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
        self.source_root = Path(source_root).resolve()
        self.platform = platform or PlatformInfo.detect()
        self.runtime_builder = runtime_builder or UvRuntimeBuilder()
        self.stager = ComponentStager(
            cache,
            source_root=self.source_root,
            platform=self.platform,
            runtime_builder=self.runtime_builder,
            system_check=system_check,
            gpu_check=gpu_check,
        )
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
        methods: tuple[str, ...] = (),
        optional: tuple[str, ...] = (),
        answers: Mapping[str, str] | None = None,
    ) -> InstallResult:
        profile = self.manifest.resolve_profile(
            profile_name, self.platform, methods=methods, optional=optional
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
                    self.stager.stage(component, staging, offline=offline)
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
            self._write_install_info(staging, target, profile)
            self._preserve_user_files(carry_from or target, staging)
            self._apply_setup(staging, profile, answers or {})
            receipt = InstallationReceipt.create(
                release=VERSION,
                profile=profile.name,
                platform=self.platform.key,
                components=tuple(installed_components),
                methods=profile.methods,
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

    def _write_install_info(
        self, staging: Path, target: Path, profile: ResolvedProfile
    ) -> None:
        """Tell the player what's installed and how to run the benchmark."""
        if not (staging / "portable_config").is_dir():
            return
        options = self.manifest.interpolation
        info = {
            "choices": [
                {"id": item.id, "title": item.title, "settings": dict(item.settings)}
                for item in profile.choices
            ],
            "options": asdict(options) if options else None,
            "benchmark_command": [
                sys.executable,
                "-m",
                "mpv_enhanced",
                "--manifest",
                str(self.source_root / "manifest.toml"),
                "benchmark",
                "--install-dir",
                str(target),
            ],
        }
        destination = inside(staging, INSTALL_INFO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(info, indent=2), encoding="utf-8", newline="\n"
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
            path = inside(staging, setup.file)
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
            old_path = inside(current, relative)
            new_path = inside(staging, relative)
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
