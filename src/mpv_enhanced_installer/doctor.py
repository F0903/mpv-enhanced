"""Read-only environment and manifest diagnostics."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .downloads import sha256_file
from .errors import InstallerError
from .manifest import Component, Manifest, ResolvedProfile
from .platforms import PlatformInfo
from .receipt import RECEIPT_FILENAME, InstallationReceipt


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
            "manifests",
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
            if receipt.release != manifest.version:
                checks.append(
                    DoctorCheck(
                        "receipt:release",
                        False,
                        f"receipt records {receipt.release}, manifests describe {manifest.version}",
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
    reported_7z = False
    reported_uv = False
    ordered_components = tuple(selected_components.values())
    for component_index, component in enumerate(ordered_components):
        if component.kind in {"local", "uv-runtime"}:
            path = source_root / (component.path or "")
            checks.append(
                DoctorCheck(f"local:{component.name}", path.exists(), str(path))
            )
        if component.kind == "uv-runtime" and not reported_uv:
            uv_path = _find_existing_uv(
                platform=platform,
                source_root=source_root,
                install_dir=install_root,
            )
            helper_index = _component_index(
                ordered_components,
                platform,
                destination="runtime/uv",
            )
            bundled_first = helper_index is not None and helper_index < component_index
            checks.append(
                DoctorCheck(
                    "runtime:uv",
                    uv_path is not None or bundled_first,
                    uv_path
                    or (
                        "locked uv component precedes the runtime"
                        if bundled_first
                        else "uv is unavailable"
                    ),
                )
            )
            reported_uv = True
        if component.kind == "download":
            try:
                artifact = component.artifact_for(platform)
            except InstallerError:
                continue
            if artifact.archive == "7z" and not reported_7z:
                executable = _find_existing_seven_zip(
                    platform=platform,
                    source_root=source_root,
                    install_dir=Path(install_dir) if install_dir is not None else None,
                )
                helper_index = _component_index(
                    ordered_components,
                    platform,
                    destination="tools/7zr.exe",
                )
                bundled_first = (
                    helper_index is not None and helper_index < component_index
                )
                available = executable is not None or bundled_first
                checks.append(
                    DoctorCheck(
                        "archive:7z",
                        available,
                        executable
                        or (
                            "locked sevenzip component precedes the archive"
                            if bundled_first
                            else "native 7zr helper is unavailable"
                        ),
                    )
                )
                reported_7z = True
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


def _component_index(
    components: tuple[Component, ...],
    platform: PlatformInfo,
    *,
    destination: str,
) -> int | None:
    for index, component in enumerate(components):
        if component.kind != "download":
            continue
        artifact = component.artifact_for(platform)
        if artifact.destination.casefold().rstrip("/") == destination.casefold():
            return index
    return None


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

    for component in profile.components:
        if component.name == "mpv":
            require("installed:mpv", install_root / "mpv.exe")
        if (
            component.kind == "local"
            and component.destination.casefold() == "portable_config"
        ):
            require(
                "installed:portable-config",
                install_root / "portable_config" / "mpv.conf",
            )
        if component.kind == "local" and component.destination == ".":
            # Release bootstraps replace the separately named upstream
            # components with one opaque, preassembled root payload. Audit its
            # product inventory from placement semantics, not a mutable name.
            require("installed:mpv", install_root / "mpv.exe")
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
                        expected_size=artifact.size,
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
                if platform.system == "windows"
                else environment / "bin" / "python"
            )
            launcher = (
                install_root / "mpv-enhanced.cmd"
                if platform.system == "windows"
                else install_root / "mpv-enhanced"
            )
            checks.append(_required_file("installed:runtime-python", python))
            checks.append(_required_file("installed:launcher", launcher))
    if has_runtime:
        uv = (
            install_root / "runtime" / "uv" / "uv.exe"
            if platform.system == "windows"
            else install_root / "runtime" / "uv" / "uv"
        )
        checks.append(_required_file("installed:runtime-uv", uv))
    return tuple(checks)


def _required_file(name: str, path: Path) -> DoctorCheck:
    return DoctorCheck(name, path.is_file(), str(path))


def _locked_file_check(
    name: str,
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> DoctorCheck:
    try:
        actual_size = path.stat().st_size
        actual_sha256 = sha256_file(path)
    except OSError as exc:
        return DoctorCheck(name, False, f"{path}: {exc}")
    ok = actual_size == expected_size and actual_sha256 == expected_sha256
    detail = (
        str(path)
        if ok
        else (
            f"{path}: expected {expected_size} bytes/{expected_sha256}, got "
            f"{actual_size} bytes/{actual_sha256}"
        )
    )
    return DoctorCheck(name, ok, detail)


def _find_existing_seven_zip(
    *,
    platform: PlatformInfo,
    source_root: Path,
    install_dir: Path | None,
) -> str | None:
    configured = os.environ.get("MPV_ENHANCED_7ZR")
    if configured:
        return configured
    filename = "7zr.exe" if platform.system == "windows" else "7zr"
    roots = [source_root]
    if install_dir is not None:
        roots.insert(0, install_dir)
    bundled = next(
        (
            root / "tools" / filename
            for root in roots
            if (root / "tools" / filename).is_file()
        ),
        None,
    )
    return (
        str(bundled)
        if bundled is not None
        else shutil.which("7zr") or shutil.which("7z")
    )


def _find_existing_uv(
    *,
    platform: PlatformInfo,
    source_root: Path,
    install_dir: Path | None,
) -> str | None:
    configured = os.environ.get("MPV_ENHANCED_UV")
    if configured:
        return configured
    filename = "uv.exe" if platform.system == "windows" else "uv"
    roots = [source_root]
    if install_dir is not None:
        roots.insert(0, install_dir)
    bundled = next(
        (
            root / "runtime" / "uv" / filename
            for root in roots
            if (root / "runtime" / "uv" / filename).is_file()
        ),
        None,
    )
    return str(bundled) if bundled is not None else shutil.which("uv")
