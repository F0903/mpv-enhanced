"""Archive extraction with traversal, link, and collision protection."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .errors import MissingArchiveBackendError, UnsafeArchiveError


@dataclass(frozen=True, slots=True)
class ArchiveCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


ArchiveCommandRunner = Callable[[Sequence[str], Path], ArchiveCommandResult]


def run_archive_command(command: Sequence[str], cwd: Path) -> ArchiveCommandResult:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
    except (OSError, UnicodeError) as exc:
        raise UnsafeArchiveError(f"cannot run {command[0]!r}: {exc}") from exc
    return ArchiveCommandResult(
        completed.returncode,
        completed.stdout,
        completed.stderr,
    )


def _member_parts(name: str, strip_components: int) -> tuple[str, ...] | None:
    if not name or "\x00" in name:
        raise UnsafeArchiveError(f"archive contains an invalid path: {name!r}")
    normalized = name.replace("\\", "/")
    windows = PureWindowsPath(name)
    posix = PurePosixPath(normalized)
    if (
        windows.is_absolute()
        or windows.drive
        or posix.is_absolute()
        or ".." in posix.parts
    ):
        raise UnsafeArchiveError(f"archive path escapes its destination: {name!r}")
    parts = tuple(part for part in posix.parts if part not in ("", "."))
    if len(parts) <= strip_components:
        return None
    return parts[strip_components:]


def _target(root: Path, parts: tuple[str, ...]) -> Path:
    target = root.joinpath(*parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise UnsafeArchiveError(
            f"archive path escapes its destination: {'/'.join(parts)!r}"
        ) from exc
    return target


def _claim(parts: tuple[str, ...], claimed: set[str]) -> None:
    # Case folding prevents output that becomes ambiguous when transported to a
    # case-insensitive filesystem.
    key = "/".join(parts).casefold()
    if key in claimed:
        raise UnsafeArchiveError(
            f"archive contains duplicate output path: {'/'.join(parts)!r}"
        )
    claimed.add(key)


def extract_archive(
    archive: str | Path,
    destination: str | Path,
    *,
    archive_type: str,
    strip_components: int = 0,
    exclude: Sequence[str] = (),
    seven_zip_executable: str | Path | None = None,
    runner: ArchiveCommandRunner = run_archive_command,
) -> None:
    """Safely extract a zip or 7z archive into an existing/new directory."""
    if strip_components < 0:
        raise ValueError("strip_components must be non-negative")
    # Native extractors run with a temporary working directory. Resolve both
    # paths before changing that working directory so relative cache paths keep
    # referring to the verified artifact.
    archive_path = Path(archive).resolve()
    destination_path = Path(destination).resolve()
    destination_path.mkdir(parents=True, exist_ok=True)
    exclusion_parts = tuple(
        parts for item in exclude if (parts := _member_parts(item, 0)) is not None
    )
    if archive_type == "zip":
        _extract_zip(
            archive_path,
            destination_path,
            strip_components,
            exclusion_parts,
        )
    elif archive_type == "7z":
        _extract_7z_native(
            archive_path,
            destination_path,
            strip_components,
            exclusion_parts,
            executable=seven_zip_executable,
            runner=runner,
        )
    else:
        raise ValueError(f"unsupported archive type: {archive_type!r}")


def _extract_zip(
    archive: Path,
    destination: Path,
    strip_components: int,
    exclusions: tuple[tuple[str, ...], ...],
) -> None:
    claimed: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as source:
            plans: list[tuple[zipfile.ZipInfo, tuple[str, ...], int]] = []
            for info in source.infolist():
                original_parts = _member_parts(info.filename, 0)
                if original_parts is None or _is_excluded(original_parts, exclusions):
                    continue
                parts = _member_parts(info.filename, strip_components)
                if parts is None:
                    continue
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise UnsafeArchiveError(
                        f"archive contains a symbolic link: {info.filename!r}"
                    )
                _claim(parts, claimed)
                plans.append((info, parts, mode))
            for info, parts, mode in plans:
                target = _target(destination, parts)
                if info.is_dir():
                    if target.exists() and not target.is_dir():
                        raise UnsafeArchiveError(
                            f"archive directory collides with a file: {target}"
                        )
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.exists():
                    raise UnsafeArchiveError(f"archive file already exists: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with (
                    source.open(info) as input_stream,
                    target.open("xb") as output_stream,
                ):
                    shutil.copyfileobj(input_stream, output_stream)
                if os.name != "nt" and mode & 0o111:
                    target.chmod(target.stat().st_mode | (mode & 0o111))
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnsafeArchiveError(
            f"cannot extract zip archive {archive}: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class _SevenZipEntry:
    original: tuple[str, ...]
    output: tuple[str, ...]
    is_directory: bool


def _extract_7z_native(
    archive: Path,
    destination: Path,
    strip_components: int,
    exclusions: tuple[tuple[str, ...], ...],
    *,
    executable: str | Path | None,
    runner: ArchiveCommandRunner,
) -> None:
    seven_zip = _find_seven_zip(executable)
    listing = _checked_archive_command(
        (
            seven_zip,
            "l",
            "-slt",
            "-ba",
            "-sccUTF-8",
            str(archive),
        ),
        destination.parent,
        runner,
        "list 7z archive",
    )
    entries = _preflight_7z_listing(
        listing.stdout,
        strip_components=strip_components,
        exclusions=exclusions,
    )
    if not entries:
        return
    with tempfile.TemporaryDirectory(
        prefix=".mpv-enhanced-7z-", dir=destination.parent
    ) as temporary:
        temporary_path = Path(temporary)
        _checked_archive_command(
            (
                seven_zip,
                "x",
                str(archive),
                f"-o{temporary_path}",
                "-y",
                "-bb0",
                "-bd",
                "-sccUTF-8",
            ),
            temporary_path,
            runner,
            "extract 7z archive",
        )
        _copy_native_extraction(
            temporary_path,
            destination,
            entries,
            strip_components=strip_components,
            exclusions=exclusions,
        )


def _find_seven_zip(executable: str | Path | None) -> str:
    if executable is not None:
        return str(executable)
    configured = os.environ.get("MPV_ENHANCED_7ZR")
    if configured:
        return configured
    discovered = shutil.which("7zr") or shutil.which("7z")
    if discovered:
        return discovered
    raise MissingArchiveBackendError(
        "7z extraction requires the locked 7zr helper component or MPV_ENHANCED_7ZR"
    )


def _checked_archive_command(
    command: Sequence[str],
    cwd: Path,
    runner: ArchiveCommandRunner,
    action: str,
) -> ArchiveCommandResult:
    result = runner(command, cwd)
    if result.returncode:
        detail = (
            result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        )
        raise UnsafeArchiveError(
            f"could not {action} (exit {result.returncode}): {detail}"
        )
    return result


def _parse_slt(output: str) -> tuple[dict[str, str], ...]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in (*output.splitlines(), ""):
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        if " = " not in line:
            raise UnsafeArchiveError(f"unexpected 7zr listing output: {line!r}")
        key, value = line.split(" = ", 1)
        if key in current:
            raise UnsafeArchiveError(f"duplicate field in 7zr listing: {key!r}")
        current[key] = value
    return tuple(records)


def _preflight_7z_listing(
    output: str,
    *,
    strip_components: int,
    exclusions: tuple[tuple[str, ...], ...],
) -> dict[tuple[str, ...], _SevenZipEntry]:
    records = _parse_slt(output)
    if not records:
        raise UnsafeArchiveError("7zr returned an empty technical listing")
    claimed: set[str] = set()
    entries: dict[tuple[str, ...], _SevenZipEntry] = {}
    for record in records:
        name = record.get("Path")
        if name is None:
            raise UnsafeArchiveError("7zr listing contains an entry without a Path")
        # Exclusion rules control what is copied into the install tree; they must
        # never suppress structural safety checks on the archive itself.
        if _record_is_link(record):
            raise UnsafeArchiveError(
                f"7z archive contains a link or reparse point: {name!r}"
            )
        original = _member_parts(name, 0)
        if original is None or _is_excluded(original, exclusions):
            continue
        output_parts = _member_parts(name, strip_components)
        if output_parts is None:
            continue
        _claim(output_parts, claimed)
        attributes = record.get("Attributes", "").upper()
        is_directory = record.get("Folder") == "+" or attributes.startswith("D")
        entries[original] = _SevenZipEntry(original, output_parts, is_directory)
    return entries


def _record_is_link(record: dict[str, str]) -> bool:
    for field in ("Symbolic Link", "Hard Link", "Junction", "Reparse Point"):
        value = record.get(field)
        if value and value not in {"-", "0"}:
            return True
    attributes = record.get("Attributes", "").upper()
    return "L" in attributes or "REPARSE" in attributes


def _copy_native_extraction(
    temporary: Path,
    destination: Path,
    entries: dict[tuple[str, ...], _SevenZipEntry],
    *,
    strip_components: int,
    exclusions: tuple[tuple[str, ...], ...],
) -> None:
    directories: list[tuple[Path, Path]] = []
    files: list[tuple[Path, Path, tuple[str, ...]]] = []
    observed_files: set[tuple[str, ...]] = set()
    planned_originals = tuple(entries)
    for base, directory_names, file_names in os.walk(temporary, followlinks=False):
        base_path = Path(base)
        for name in (*directory_names, *file_names):
            source = base_path / name
            is_junction = getattr(source, "is_junction", lambda: False)()
            if source.is_symlink() or is_junction:
                raise UnsafeArchiveError(
                    f"7z extraction produced a link or junction: {source}"
                )
            original = _member_parts(source.relative_to(temporary).as_posix(), 0)
            if original is None or _is_excluded(original, exclusions):
                continue
            if len(original) <= strip_components:
                continue
            output_parts = original[strip_components:]
            target = _target(destination, output_parts)
            entry = entries.get(original)
            if source.is_dir():
                if entry is not None and not entry.is_directory:
                    raise UnsafeArchiveError(
                        f"7zr produced a directory for file entry: {source}"
                    )
                if entry is None and not any(
                    item[: len(original)] == original for item in planned_originals
                ):
                    raise UnsafeArchiveError(
                        f"7zr produced an undeclared directory: {source}"
                    )
                directories.append((source, target))
            elif source.is_file():
                if entry is None or entry.is_directory:
                    raise UnsafeArchiveError(
                        f"7zr produced an undeclared file: {source}"
                    )
                observed_files.add(original)
                files.append((source, target, original))
            else:
                raise UnsafeArchiveError(
                    f"7zr produced an unsupported filesystem entry: {source}"
                )

    missing = [
        entry.original
        for entry in entries.values()
        if not entry.is_directory and entry.original not in observed_files
    ]
    if missing:
        raise UnsafeArchiveError(
            "7zr did not produce expected entries: "
            + ", ".join("/".join(parts) for parts in sorted(missing))
        )
    for _source, target in sorted(directories, key=lambda item: len(item[1].parts)):
        if target.exists() and not target.is_dir():
            raise UnsafeArchiveError(
                f"archive directory collides with a file: {target}"
            )
    for _source, target, _original in files:
        if target.exists():
            raise UnsafeArchiveError(f"archive file already exists: {target}")
    for _source, target in sorted(directories, key=lambda item: len(item[1].parts)):
        target.mkdir(parents=True, exist_ok=True)
    for source, target, _original in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _is_excluded(
    parts: tuple[str, ...], exclusions: tuple[tuple[str, ...], ...]
) -> bool:
    folded = tuple(part.casefold() for part in parts)
    return any(
        folded[: len(rule)] == tuple(part.casefold() for part in rule)
        for rule in exclusions
    )
