import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from mpv_enhanced_installer.archives import ArchiveCommandResult, extract_archive
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


class NativeSevenZipTests(unittest.TestCase):
    def test_preflights_extracts_in_temp_and_applies_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "payload.7z"
            archive.write_bytes(b"the runner is injected")
            commands: list[list[str]] = []

            def runner(command, _cwd):
                commands.append(list(command))
                if command[1] == "l":
                    return ArchiveCommandResult(
                        0,
                        "Path = mpv.exe\n"
                        "Size = 3\n"
                        "Attributes = A\n\n"
                        "Path = installer/updater.ps1\n"
                        "Size = 7\n"
                        "Attributes = A\n",
                    )
                output = Path(
                    next(item[2:] for item in command if item.startswith("-o"))
                )
                (output / "installer").mkdir(parents=True)
                (output / "mpv.exe").write_bytes(b"mpv")
                (output / "installer" / "updater.ps1").write_text("mutable")
                return ArchiveCommandResult(0)

            destination = root / "result"
            extract_archive(
                archive,
                destination,
                archive_type="7z",
                exclude=("installer",),
                seven_zip_executable="C:/locked/7zr.exe",
                runner=runner,
            )
            self.assertEqual((destination / "mpv.exe").read_bytes(), b"mpv")
            self.assertFalse((destination / "installer").exists())
            self.assertEqual([command[1] for command in commands], ["l", "x"])

    def test_rejects_unsafe_listing_before_extraction(self) -> None:
        extraction_called = False

        def runner(command, _cwd):
            nonlocal extraction_called
            if command[1] == "x":
                extraction_called = True
            return ArchiveCommandResult(
                0,
                "Path = ../outside.exe\nSize = 3\nAttributes = A\n",
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(UnsafeArchiveError):
                extract_archive(
                    root / "bad.7z",
                    root / "result",
                    archive_type="7z",
                    seven_zip_executable="7zr.exe",
                    runner=runner,
                )
            self.assertFalse(extraction_called)

    def test_rejects_link_metadata_before_extraction(self) -> None:
        extraction_called = False

        def runner(_command, _cwd):
            nonlocal extraction_called
            if _command[1] == "x":
                extraction_called = True
            return ArchiveCommandResult(
                0,
                "Path = installer/link\n"
                "Size = 0\n"
                "Attributes = A\n"
                "Symbolic Link = ../../outside\n",
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(UnsafeArchiveError, "link or reparse"):
                extract_archive(
                    root / "bad.7z",
                    root / "result",
                    archive_type="7z",
                    exclude=("installer",),
                    seven_zip_executable="7zr.exe",
                    runner=runner,
                )
            self.assertFalse(extraction_called)


if __name__ == "__main__":
    unittest.main()
