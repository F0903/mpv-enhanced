"""Archive extraction with traversal, link, and collision protection."""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO

from .errors import UnsafeArchiveError

# (name, is_directory, is_link_or_special, mode, open_member)
_Entry = tuple[str, bool, bool, int, Callable[[], AbstractContextManager[IO[bytes]]]]


def _member_parts(name: str) -> tuple[str, ...]:
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
    return tuple(part for part in posix.parts if part not in ("", "."))


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
    exclude: Sequence[str] = (),
) -> None:
    """Safely extract a zip, or a zip wrapping one tar.gz, into a directory."""
    archive_path = Path(archive).resolve()
    destination_path = Path(destination).resolve()
    destination_path.mkdir(parents=True, exist_ok=True)
    exclusions = tuple(parts for item in exclude if (parts := _member_parts(item)))
    try:
        with zipfile.ZipFile(archive_path) as source:
            if archive_type == "zip":
                entries = (
                    (
                        info.filename,
                        info.is_dir(),
                        stat.S_ISLNK(info.external_attr >> 16),
                        info.external_attr >> 16,
                        lambda info=info: source.open(info),
                    )
                    for info in source.infolist()
                )
                _write_entries(entries, destination_path, exclusions)
            elif archive_type == "zip+tar.gz":
                _extract_wrapped_tar(source, archive_path, destination_path, exclusions)
            else:
                raise ValueError(f"unsupported archive type: {archive_type!r}")
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        raise UnsafeArchiveError(f"cannot extract archive {archive}: {exc}") from exc


def _extract_wrapped_tar(
    source: zipfile.ZipFile,
    archive: Path,
    destination: Path,
    exclusions: tuple[tuple[str, ...], ...],
) -> None:
    # Official macOS mpv builds are a zip holding a single mpv.tar.gz.
    members = [info for info in source.infolist() if not info.is_dir()]
    if len(members) != 1 or not members[0].filename.endswith(".tar.gz"):
        raise UnsafeArchiveError(f"expected exactly one .tar.gz inside {archive}")
    with tempfile.TemporaryFile() as inner:
        with source.open(members[0]) as stream:
            shutil.copyfileobj(stream, inner)
        inner.seek(0)
        with tarfile.open(fileobj=inner, mode="r:gz") as tar:
            entries = (
                (
                    member.name,
                    member.isdir(),
                    not (member.isfile() or member.isdir()),
                    member.mode,
                    lambda member=member: tar.extractfile(member),
                )
                for member in tar.getmembers()
            )
            _write_entries(entries, destination, exclusions)


def _write_entries(
    entries: Iterable[_Entry],
    destination: Path,
    exclusions: tuple[tuple[str, ...], ...],
) -> None:
    # Validate every entry before writing anything.
    claimed: set[str] = set()
    plans: list[
        tuple[
            tuple[str, ...], bool, int, Callable[[], AbstractContextManager[IO[bytes]]]
        ]
    ] = []
    for name, is_directory, is_special, mode, open_member in entries:
        parts = _member_parts(name)
        if not parts or _is_excluded(parts, exclusions):
            continue
        if is_special:
            raise UnsafeArchiveError(
                f"archive contains a link or special file: {name!r}"
            )
        _claim(parts, claimed)
        plans.append((parts, is_directory, mode, open_member))
    for parts, is_directory, mode, open_member in plans:
        target = _target(destination, parts)
        if is_directory:
            if target.exists() and not target.is_dir():
                raise UnsafeArchiveError(
                    f"archive directory collides with a file: {target}"
                )
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            raise UnsafeArchiveError(f"archive file already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open_member() as input_stream, target.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
        if os.name != "nt" and mode & 0o111:
            target.chmod(target.stat().st_mode | (mode & 0o111))


def _is_excluded(
    parts: tuple[str, ...], exclusions: tuple[tuple[str, ...], ...]
) -> bool:
    folded = tuple(part.casefold() for part in parts)
    return any(
        folded[: len(rule)] == tuple(part.casefold() for part in rule)
        for rule in exclusions
    )
