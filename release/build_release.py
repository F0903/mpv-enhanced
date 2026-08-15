"""Build verified Windows release archives using only the standard library.

The release workflow deliberately keeps network access and installation logic in
the Python installer. This helper only prepares release-specific manifests,
stages a self-contained bootstrap tree, and writes release ZIP files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

UV_VERSION = "0.12.5"
UV_EXE_SHA256 = "8da6cedef60c27ac997ebf400fbfc6d373c5b0a7ae6a299b9d52be7fe63723fb"
CONTROLLER_PYTHON = "3.12.13"
CONTROLLER_BUILD = "20260807"
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?\Z")


class ReleaseError(RuntimeError):
    """A release tree is incomplete, unsafe, or inconsistent."""


def _resolved_directory(value: str | Path, label: str, *, exists: bool) -> Path:
    path = Path(value).resolve()
    if exists and not path.is_dir():
        raise ReleaseError(f"{label} is not a directory: {path}")
    if path.parent == path:
        raise ReleaseError(f"{label} cannot be the filesystem root")
    return path


def _validated_version(value: str) -> str:
    if not _VERSION.fullmatch(value):
        raise ReleaseError(
            "version must be SemVer-like and omit the leading 'v', for example "
            "1.2.3 or 1.2.3-rc.1"
        )
    return value


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _is_python_bytecode(relative: str) -> bool:
    path = PurePosixPath(relative)
    return any(part.casefold() == "__pycache__" for part in path.parts) or (
        path.suffix.casefold() == ".pyc"
    )


def _iter_files(
    root: Path,
    *,
    excluded_roots: tuple[str, ...] = (),
):
    excluded = {Path(item).as_posix().strip("/") for item in excluded_roots}
    for directory, directory_names, file_names in os.walk(root, topdown=True):
        current = Path(directory)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            child = current / name
            relative = child.relative_to(root).as_posix()
            if _is_python_bytecode(relative) or any(
                relative == item or relative.startswith(f"{item}/") for item in excluded
            ):
                continue
            if _is_link(child):
                raise ReleaseError(
                    f"release input contains a link or junction: {child}"
                )
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names):
            child = current / name
            relative = child.relative_to(root).as_posix()
            if _is_python_bytecode(relative) or any(
                relative == item or relative.startswith(f"{item}/") for item in excluded
            ):
                continue
            if _is_link(child):
                raise ReleaseError(
                    f"release input contains a link or junction: {child}"
                )
            if not child.is_file():
                raise ReleaseError(f"release input is not a regular file: {child}")
            yield child


def _copy_file(source: Path, destination: Path) -> None:
    if _is_link(source) or not source.is_file():
        raise ReleaseError(f"release source is not a regular file: {source}")
    if destination.exists():
        raise ReleaseError(f"release destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path) -> None:
    if _is_link(source) or not source.is_dir():
        raise ReleaseError(f"release source is not a directory: {source}")
    if destination.exists():
        raise ReleaseError(f"release destination already exists: {destination}")
    destination.mkdir(parents=True)
    for item in _iter_files(source):
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def _write_text_exclusive(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
    except FileExistsError as exc:
        raise ReleaseError(f"release destination already exists: {path}") from exc


def _release_manifest(source: Path, version: str) -> str:
    lines = source.read_text(encoding="utf-8").splitlines()
    in_release = False
    replacements = 0
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_release = stripped == "[release]"
        if in_release and re.match(r"^\s*version\s*=", line):
            line = f"version = {json.dumps(version)}"
            replacements += 1
        rendered.append(line)
    if replacements != 1:
        raise ReleaseError(
            f"expected exactly one [release].version in {source}, found {replacements}"
        )
    return "\n".join(rendered) + "\n"


def prepare(args: argparse.Namespace) -> None:
    version = _validated_version(args.version)
    repository = _resolved_directory(args.repo_root, "repository root", exists=True)
    work = _resolved_directory(args.work_dir, "work directory", exists=False)
    if work.exists():
        raise ReleaseError(f"work directory must not already exist: {work}")
    work.mkdir(parents=True)
    manifests = work / "manifests"
    manifests.mkdir()
    _write_text_exclusive(
        manifests / "deps.lock.toml",
        _release_manifest(repository / "deps.lock.toml", version),
    )
    _copy_file(repository / "profiles.toml", manifests / "profiles.toml")
    print(manifests)


def _load_receipt(core: Path, version: str) -> None:
    receipt_path = core / ".mpv-enhanced-receipt.toml"
    try:
        with receipt_path.open("rb") as stream:
            receipt = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(f"cannot read assembled core receipt: {exc}") from exc
    if receipt.get("release") != version or receipt.get("profile") != "core":
        raise ReleaseError(
            "assembled core receipt does not match the requested release/core profile"
        )
    component_names = {
        item.get("name")
        for item in receipt.get("components", [])
        if isinstance(item, dict)
    }
    if not {"mpv", "portable-config"}.issubset(component_names):
        raise ReleaseError(
            "assembled core receipt is missing the mpv or portable-config component"
        )
    if not (core / "mpv.exe").is_file():
        raise ReleaseError("assembled core does not contain mpv.exe")


def _assert_no_rife_model(root: Path) -> None:
    matches = [
        path
        for path in _iter_files(root)
        if path.name.lower().startswith("flownet_") and path.suffix.lower() == ".pkl"
    ]
    if matches:
        raise ReleaseError(
            "RIFE model redistribution is disabled; unexpected model: "
            + str(matches[0])
        )


def _toml_value(value: object, label: str) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "[" + ", ".join(json.dumps(item) for item in value) + "]"
    raise ReleaseError(f"cannot render unsupported {label} value: {value!r}")


def _render_upstream_component(name: str, data: object) -> str:
    if not isinstance(data, dict):
        raise ReleaseError(f"upstream component {name!r} is not a TOML table")
    lines = [f"[components.{name}]"]
    artifacts = data.get("artifacts")
    for key, value in data.items():
        if key == "artifacts":
            continue
        lines.append(f"{key} = {_toml_value(value, f'components.{name}.{key}')}")
    if artifacts is not None:
        if not isinstance(artifacts, dict):
            raise ReleaseError(f"upstream artifacts for {name!r} are not a table")
        for platform_name, artifact in artifacts.items():
            if not isinstance(artifact, dict):
                raise ReleaseError(
                    f"upstream artifact {name!r}/{platform_name!r} is not a table"
                )
            lines.extend(("", f"[components.{name}.artifacts.{platform_name}]"))
            for key, value in artifact.items():
                lines.append(
                    f"{key} = "
                    f"{_toml_value(value, f'components.{name}.artifacts.{platform_name}.{key}')}"
                )
    return "\n".join(lines) + "\n"


def _bootstrap_dependencies(version: str, upstream_path: Path) -> str:
    try:
        with upstream_path.open("rb") as stream:
            upstream = tomllib.load(stream)
        components = upstream["components"]
        uv_version = components["uv"]["version"]
        selected = {
            name: components[name]
            for name in (
                "rife-runtime",
                "rife-tensorrt",
                "rife-model-4-25-lite",
            )
        }
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(
            f"cannot copy RIFE metadata from {upstream_path}: {exc}"
        ) from exc
    if str(uv_version) != UV_VERSION:
        raise ReleaseError(
            f"upstream uv component is {uv_version}, expected pinned {UV_VERSION}"
        )
    for runtime_name in ("rife-runtime", "rife-tensorrt"):
        if selected[runtime_name].get("kind") != "uv-runtime":
            raise ReleaseError(f"upstream {runtime_name} is not a uv-runtime component")
    if selected["rife-model-4-25-lite"].get("kind") != "download":
        raise ReleaseError("upstream RIFE model is not a direct download component")

    header = f"""schema_version = 1

[release]
version = {json.dumps(version)}

[metadata]
target = "windows-x86_64"
hash_algorithm = "sha256"
distribution = "embedded-core"

[components.core-payload]
kind = "local"
version = {json.dumps(version)}
description = "Preassembled, verified mpv-enhanced core payload"
path = "payload/core"
destination = "."

[components.uv-runtime-binary]
kind = "local"
version = {json.dumps(str(uv_version))}
description = "Bundled uv executable for optional RIFE runtime assembly"
path = "uv.exe"
destination = "runtime/uv/uv.exe"
"""
    copied = "\n".join(
        _render_upstream_component(name, selected[name])
        for name in (
            "rife-runtime",
            "rife-tensorrt",
            "rife-model-4-25-lite",
        )
    )
    return header + "\n" + copied


def _bootstrap_profiles() -> str:
    return """schema_version = 1

[profiles.core]
description = "Embedded mpv-enhanced core payload (no upstream downloads)"
components = ["core-payload"]
platforms = ["windows-x86_64"]

[profiles.rife-cuda]
description = "Core plus locked CUDA RIFE runtime; requires network access to package/model origins"
extends = "core"
components = ["uv-runtime-binary", "rife-runtime", "rife-model-4-25-lite"]
platforms = ["windows-x86_64"]

[profiles.rife-tensorrt]
description = "Core plus locked TensorRT RIFE runtime; requires network access to package/model origins"
extends = "core"
components = ["uv-runtime-binary", "rife-tensorrt", "rife-model-4-25-lite"]
platforms = ["windows-x86_64"]
"""


def _core_readme(version: str) -> str:
    return f"""# mpv-enhanced core {version}

This is the ready-to-run Windows x86-64 core package. Run `mpv.exe` directly
or create a shortcut to it. The enhanced configuration is already installed in
`portable_config`.

This package intentionally has no `install.cmd`, Python controller, or RIFE
runtime. To choose an install directory or add a RIFE profile, download the
`mpv-enhanced-bootstrap-windows-x86_64-*.zip` asset from the same release and
run its `install.cmd` instead.

Put personal settings in `portable_config/mpv-local.conf`. Release metadata is
under `locks`; licensing and source details are in `LICENSES` and
`THIRD_PARTY_NOTICES.md`.
"""


def _verify_uv(executable: Path) -> None:
    actual_hash = _sha256(executable)
    if actual_hash != UV_EXE_SHA256:
        raise ReleaseError(
            f"uv.exe checksum mismatch: expected {UV_EXE_SHA256}, got {actual_hash}"
        )
    completed = subprocess.run(
        [str(executable), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    reported = completed.stdout.strip()
    if completed.returncode or not re.match(
        rf"uv {re.escape(UV_VERSION)}(?:\s|$)", reported
    ):
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseError(f"unexpected bundled uv version: {detail or '<empty>'}")


def _remove_uv_python_alias(bootstrap: Path) -> None:
    python_root = bootstrap / "runtime" / "controller-python"
    major_minor = ".".join(CONTROLLER_PYTHON.split(".")[:2])
    alias = python_root / f"cpython-{major_minor}-windows-x86_64-none"
    exact = python_root / f"cpython-{CONTROLLER_PYTHON}-windows-x86_64-none"
    if not alias.exists():
        return
    if not getattr(alias, "is_junction", lambda: False)():
        raise ReleaseError(f"managed Python alias is not a junction: {alias}")
    if not exact.is_dir() or alias.resolve(strict=True) != exact.resolve(strict=True):
        raise ReleaseError(f"managed Python alias has an unexpected target: {alias}")
    alias.rmdir()


def _verify_controller_python(bootstrap: Path) -> None:
    python_root = bootstrap / "runtime" / "controller-python"
    exact = python_root / f"cpython-{CONTROLLER_PYTHON}-windows-x86_64-none"
    if not (exact / "python.exe").is_file():
        raise ReleaseError(f"bootstrap managed Python {CONTROLLER_PYTHON} is missing")

    build_file = exact / "BUILD"
    actual_build = build_file.read_text(encoding="utf-8").strip()
    if actual_build != CONTROLLER_BUILD:
        raise ReleaseError(
            f"managed Python build is {actual_build!r}, expected {CONTROLLER_BUILD}"
        )

    runtime_license = exact / "LICENSE.txt"
    bundled_license = (
        bootstrap
        / "LICENSES"
        / f"Python-standalone-{CONTROLLER_PYTHON}+{CONTROLLER_BUILD}.txt"
    )
    if not runtime_license.is_file() or not bundled_license.is_file():
        raise ReleaseError("managed Python or bundled standalone license is missing")
    runtime_license_text = runtime_license.read_text(encoding="utf-8")
    bundled_license_text = bundled_license.read_text(encoding="utf-8")
    if runtime_license_text != bundled_license_text:
        raise ReleaseError(
            "managed Python license does not match the committed standalone license "
            "after newline normalization"
        )


def stage(args: argparse.Namespace) -> None:
    version = _validated_version(args.version)
    repository = _resolved_directory(args.repo_root, "repository root", exists=True)
    work = _resolved_directory(args.work_dir, "work directory", exists=True)
    core = _resolved_directory(args.core_dir, "assembled core", exists=True)
    uv_executable = Path(args.uv_executable).resolve()
    _verify_uv(uv_executable)
    _load_receipt(core, version)
    _assert_no_rife_model(core)

    core_package = work / "core-package"
    _copy_tree(core, core_package)
    _write_text_exclusive(
        core_package / "MPV_ENHANCED_README.md", _core_readme(version)
    )
    _copy_file(
        repository / "THIRD_PARTY_NOTICES.md",
        core_package / "THIRD_PARTY_NOTICES.md",
    )
    _copy_tree(repository / "LICENSES", core_package / "LICENSES")
    core_locks = core_package / "locks"
    _copy_file(work / "manifests" / "deps.lock.toml", core_locks / "deps.lock.toml")
    _copy_file(work / "manifests" / "profiles.toml", core_locks / "profiles.toml")

    bootstrap = work / "bootstrap"
    bootstrap.mkdir()
    for name in (
        ".python-version",
        "install.cmd",
        "pyproject.toml",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "uv.lock",
    ):
        _copy_file(repository / name, bootstrap / name)
    _copy_tree(repository / "src", bootstrap / "src")
    _copy_tree(repository / "LICENSES", bootstrap / "LICENSES")
    _copy_file(uv_executable, bootstrap / "uv.exe")
    _copy_tree(core_package, bootstrap / "payload" / "core")
    _write_text_exclusive(
        bootstrap / "deps.lock.toml",
        _bootstrap_dependencies(version, repository / "deps.lock.toml"),
    )
    _write_text_exclusive(bootstrap / "profiles.toml", _bootstrap_profiles())

    _copy_tree(repository / "runtime" / "rife", bootstrap / "runtime" / "rife")

    locks = bootstrap / "locks"
    _copy_file(
        work / "manifests" / "deps.lock.toml",
        locks / "upstream-deps.lock.toml",
    )
    _copy_file(
        work / "manifests" / "profiles.toml",
        locks / "upstream-profiles.toml",
    )
    _assert_no_rife_model(bootstrap)
    print(bootstrap)


def _zip_tree(
    source: Path,
    destination: Path,
    *,
    excluded_roots: tuple[str, ...] = (),
) -> None:
    if destination.exists():
        raise ReleaseError(f"release artifact already exists: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        raise ReleaseError(f"temporary release artifact already exists: {temporary}")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for source_file in _iter_files(source, excluded_roots=excluded_roots):
                relative = source_file.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                mode = stat.S_IMODE(source_file.stat().st_mode)
                info.external_attr = (stat.S_IFREG | mode) << 16
                with (
                    source_file.open("rb") as input_stream,
                    archive.open(info, mode="w", force_zip64=True) as output_stream,
                ):
                    shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _assert_archive_clean(path: Path) -> None:
    forbidden: list[str] = []
    with zipfile.ZipFile(path, mode="r") as archive:
        for info in archive.infolist():
            name = info.filename
            parsed = PurePosixPath(name)
            if (
                _is_python_bytecode(name)
                or any(part.casefold() == ".uv-cache" for part in parsed.parts)
                or (
                    parsed.name.casefold().startswith("flownet_")
                    and parsed.suffix.casefold() == ".pkl"
                )
            ):
                forbidden.append(name)
    if forbidden:
        raise ReleaseError(
            f"release archive contains forbidden generated content: {forbidden[0]}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def finalize(args: argparse.Namespace) -> None:
    version = _validated_version(args.version)
    work = _resolved_directory(args.work_dir, "work directory", exists=True)
    output = _resolved_directory(args.output_dir, "output directory", exists=False)
    output.mkdir(parents=True, exist_ok=True)
    core = work / "core-package"
    bootstrap = work / "bootstrap"
    if not (bootstrap / ".venv" / "Scripts" / "python.exe").is_file():
        raise ReleaseError("bootstrap controller .venv has not been populated")
    _verify_controller_python(bootstrap)
    _remove_uv_python_alias(bootstrap)
    _verify_uv(bootstrap / "uv.exe")
    _assert_no_rife_model(core)
    _assert_no_rife_model(bootstrap)

    slug = re.sub(r"[^0-9A-Za-z.-]", "_", version)
    core_zip = output / f"mpv-enhanced-core-windows-x86_64-v{slug}.zip"
    bootstrap_zip = output / f"mpv-enhanced-bootstrap-windows-x86_64-v{slug}.zip"
    _zip_tree(core, core_zip)
    # The populated controller no longer needs uv's download/build cache. The
    # CI smoke test verifies this after extracting this exact ZIP offline.
    _zip_tree(bootstrap, bootstrap_zip, excluded_roots=(".uv-cache",))
    _assert_archive_clean(core_zip)
    _assert_archive_clean(bootstrap_zip)

    sums = output / "SHA256SUMS"
    _write_text_exclusive(
        sums,
        "".join(
            f"{_sha256(path)} *{path.name}\n" for path in (bootstrap_zip, core_zip)
        ),
    )
    print(core_zip)
    print(bootstrap_zip)
    print(sums)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare", help="create release-versioned manifests"
    )
    prepare_parser.add_argument("--repo-root", required=True)
    prepare_parser.add_argument("--work-dir", required=True)
    prepare_parser.add_argument("--version", required=True)
    prepare_parser.set_defaults(action=prepare)

    stage_parser = subparsers.add_parser(
        "stage", help="stage the direct core and bootstrap trees"
    )
    stage_parser.add_argument("--repo-root", required=True)
    stage_parser.add_argument("--work-dir", required=True)
    stage_parser.add_argument("--core-dir", required=True)
    stage_parser.add_argument("--uv-executable", required=True)
    stage_parser.add_argument("--version", required=True)
    stage_parser.set_defaults(action=stage)

    finalize_parser = subparsers.add_parser(
        "finalize", help="write release ZIP files and checksums"
    )
    finalize_parser.add_argument("--work-dir", required=True)
    finalize_parser.add_argument("--output-dir", required=True)
    finalize_parser.add_argument("--version", required=True)
    finalize_parser.set_defaults(action=finalize)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.action(args)
    except (OSError, ReleaseError, subprocess.SubprocessError) as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
