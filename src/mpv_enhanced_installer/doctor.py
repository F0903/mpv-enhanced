"""Read-only environment and manifest diagnostics."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .downloads import sha256_file
from .errors import InstallerError
from .installer import check_gpu, check_system_program
from .manifest import Component, Manifest, ResolvedProfile
from .platforms import PlatformInfo
from .receipt import RECEIPT_FILENAME, InstallationReceipt
from .version import VERSION


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def run_doctor(
    manifest: Manifest,
    *,
    platform: PlatformInfo,
    source_root: str | Path,
    profile_name: str | None = None,
    install_dir: str | Path | None = None,
) -> tuple[DoctorCheck, ...]:
    source_root = Path(source_root).resolve()
    install_root = Path(install_dir).resolve() if install_dir is not None else None
    checks: list[DoctorCheck] = [
        DoctorCheck("platform", True, platform.key),
        DoctorCheck(
            "manifest",
            True,
            f"{len(manifest.components)} components, {len(manifest.profiles)} profiles",
        ),
    ]
    receipt: InstallationReceipt | None = None
    if install_root is not None:
        try:
            receipt = InstallationReceipt.load(install_root / RECEIPT_FILENAME)
            checks.append(
                DoctorCheck(
                    "receipt",
                    True,
                    f"{receipt.release} / {receipt.profile} / {receipt.platform}",
                )
            )
            if profile_name is None:
                profile_name = receipt.profile
            elif profile_name != receipt.profile:
                checks.append(
                    DoctorCheck(
                        "receipt:profile",
                        False,
                        f"requested {profile_name}, receipt records {receipt.profile}",
                    )
                )
            if receipt.platform != platform.key:
                checks.append(
                    DoctorCheck(
                        "receipt:platform",
                        False,
                        f"receipt records {receipt.platform}, current target is {platform.key}",
                    )
                )
            if receipt.release != VERSION:
                checks.append(
                    DoctorCheck(
                        "receipt:release",
                        False,
                        f"receipt records {receipt.release}, this installer is {VERSION}",
                    )
                )
        except InstallerError as exc:
            checks.append(DoctorCheck("receipt", False, str(exc)))

    profile_names = (profile_name,) if profile_name else tuple(manifest.profiles)
    selected_components: dict[str, Component] = {}
    resolved_install_profile: ResolvedProfile | None = None
    for name in profile_names:
        try:
            resolved = manifest.resolve_profile(name, platform)
            if install_root is not None and name == profile_name:
                resolved_install_profile = resolved
            checks.append(DoctorCheck(f"profile:{name}", True, "supported"))
            selected_components.update(
                (item.name, item) for item in resolved.components
            )
        except InstallerError as exc:
            checks.append(DoctorCheck(f"profile:{name}", False, str(exc)))
    reported_uv = False
    for component in selected_components.values():
        if component.kind in {"local", "uv-runtime"}:
            path = source_root / (component.path or "")
            checks.append(
                DoctorCheck(f"local:{component.name}", path.exists(), str(path))
            )
        if component.kind == "system":
            assert component.system is not None
            try:
                found, version = check_system_program(component.system)
                checks.append(
                    DoctorCheck(f"system:{component.name}", True, f"{found} {version}")
                )
            except InstallerError as exc:
                checks.append(DoctorCheck(f"system:{component.name}", False, str(exc)))
        if component.kind == "gpu":
            assert component.gpu is not None
            try:
                checks.append(
                    DoctorCheck(f"gpu:{component.name}", True, check_gpu(component.gpu))
                )
            except InstallerError as exc:
                checks.append(DoctorCheck(f"gpu:{component.name}", False, str(exc)))
        if component.kind == "uv-runtime" and not reported_uv:
            uv_path = os.environ.get("UV") or shutil.which("uv")
            checks.append(
                DoctorCheck(
                    "runtime:uv", uv_path is not None, uv_path or "uv is unavailable"
                )
            )
            reported_uv = True
    if install_root is not None and resolved_install_profile is not None:
        if receipt is not None:
            expected = tuple(item.name for item in resolved_install_profile.components)
            recorded = tuple(item.name for item in receipt.components)
            checks.append(
                DoctorCheck(
                    "receipt:components",
                    recorded == expected,
                    f"expected {list(expected)}, recorded {list(recorded)}",
                )
            )
        checks.extend(
            _audit_installed_files(
                install_root,
                resolved_install_profile,
                platform,
            )
        )
    return tuple(checks)


def _audit_installed_files(
    install_root: Path,
    profile: ResolvedProfile,
    platform: PlatformInfo,
) -> tuple[DoctorCheck, ...]:
    checks: list[DoctorCheck] = []
    checked_names: set[str] = set()
    has_runtime = False

    def require(name: str, path: Path) -> None:
        if name not in checked_names:
            checks.append(_required_file(name, path))
            checked_names.add(name)

    windows = platform.system == "windows"
    for component in profile.components:
        if component.name == "mpv":
            mpv = "mpv.exe" if windows else "mpv.app/Contents/MacOS/mpv"
            require("installed:mpv", install_root / mpv)
        if (
            component.kind == "local"
            and component.destination.casefold() == "portable_config"
        ):
            require(
                "installed:portable-config",
                install_root / "portable_config" / "mpv.conf",
            )
        if component.kind == "download":
            artifact = component.artifact_for(platform)
            if artifact.archive == "none":
                checks.append(
                    _locked_file_check(
                        f"installed:{component.name}",
                        install_root.joinpath(*artifact.destination.split("/")),
                        expected_sha256=artifact.sha256,
                    )
                )
        if component.kind == "uv-runtime":
            assert component.runtime is not None
            has_runtime = True
            environment = install_root.joinpath(
                *component.runtime.environment.split("/")
            )
            python = (
                environment / "Scripts" / "python.exe"
                if windows
                else environment / "bin" / "python"
            )
            checks.append(_required_file("installed:runtime-python", python))
    if has_runtime or not windows:
        launcher = "mpv-enhanced.cmd" if windows else "mpv-enhanced"
        checks.append(_required_file("installed:launcher", install_root / launcher))
    return tuple(checks)


def _required_file(name: str, path: Path) -> DoctorCheck:
    return DoctorCheck(name, path.is_file(), str(path))


def _locked_file_check(
    name: str,
    path: Path,
    *,
    expected_sha256: str,
) -> DoctorCheck:
    try:
        actual_sha256 = sha256_file(path)
    except OSError as exc:
        return DoctorCheck(name, False, f"{path}: {exc}")
    ok = actual_sha256 == expected_sha256
    detail = (
        str(path) if ok else f"{path}: expected {expected_sha256}, got {actual_sha256}"
    )
    return DoctorCheck(name, ok, detail)
