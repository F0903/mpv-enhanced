import io
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from mpv_enhanced_installer.archives import extract_archive
from mpv_enhanced_installer.errors import UnsafeArchiveError


class SafeZipTests(unittest.TestCase):
    def test_extracts_with_stripped_top_level_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "payload.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("release/bin/mpv.exe", b"mpv")
                output.writestr("release/portable_config/mpv.conf", b"config")

            destination = root / "result"
            extract_archive(
                archive, destination, archive_type="zip", strip_components=1
            )

            self.assertEqual((destination / "bin" / "mpv.exe").read_bytes(), b"mpv")
            self.assertEqual(
                (destination / "portable_config" / "mpv.conf").read_bytes(),
                b"config",
            )

    def test_rejects_parent_traversal_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escaped.txt", b"bad")
            destination = root / "result"

            with self.assertRaises(UnsafeArchiveError):
                extract_archive(archive, destination, archive_type="zip")
            self.assertFalse((root / "escaped.txt").exists())

    def test_rejects_backslash_traversal_and_symlinks(self) -> None:
        for member in ("..\\escaped.txt", "C:\\escaped.txt"):
            with (
                self.subTest(member=member),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(member, b"bad")
                with self.assertRaises(UnsafeArchiveError):
                    extract_archive(archive, root / "result", archive_type="zip")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "link.zip"
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(info, "outside")
            with self.assertRaises(UnsafeArchiveError):
                extract_archive(archive, root / "result", archive_type="zip")


def _wrapped_tar(path: Path, members: list[tarfile.TarInfo], data: bytes = b"") -> None:
    """Write a zip holding one tar.gz, the layout of official macOS mpv builds."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for member in members:
            tar.addfile(member, io.BytesIO(data) if member.isfile() else None)
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("mpv.tar.gz", buffer.getvalue())


def _tar_file(name: str, mode: int, data: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info


class WrappedTarTests(unittest.TestCase):
    def test_extracts_app_bundle_keeping_exec_bits_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "mpv-macos.zip"
            _wrapped_tar(
                archive,
                [
                    _tar_file("mpv.app/Contents/MacOS/mpv", 0o755, b"mpv"),
                    _tar_file("mpv.app/Contents/Resources/mpv.conf", 0o644, b"mpv"),
                ],
                b"mpv",
            )
            destination = root / "result"
            extract_archive(
                archive,
                destination,
                archive_type="zip+tar.gz",
                exclude=("mpv.app/Contents/Resources",),
            )
            binary = destination / "mpv.app" / "Contents" / "MacOS" / "mpv"
            self.assertEqual(binary.read_bytes(), b"mpv")
            self.assertFalse(
                (destination / "mpv.app" / "Contents" / "Resources").exists()
            )
            if os.name != "nt":
                self.assertTrue(binary.stat().st_mode & stat.S_IXUSR)

    def test_rejects_links_and_traversal_inside_the_tarball(self) -> None:
        link = tarfile.TarInfo("mpv.app/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        for member in (link, _tar_file("../escaped", 0o644, b"")):
            with (
                self.subTest(member=member.name),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                archive = root / "bad.zip"
                _wrapped_tar(archive, [member])
                with self.assertRaises(UnsafeArchiveError):
                    extract_archive(archive, root / "result", archive_type="zip+tar.gz")
                self.assertFalse((root / "escaped").exists())

    def test_requires_exactly_one_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("a.tar.gz", b"")
                output.writestr("b.tar.gz", b"")
            with self.assertRaisesRegex(UnsafeArchiveError, "exactly one"):
                extract_archive(archive, root / "result", archive_type="zip+tar.gz")


if __name__ == "__main__":
    unittest.main()
