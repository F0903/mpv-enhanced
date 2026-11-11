import hashlib
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced.errors import InstallerError
from mpv_enhanced.versions.update import update
from tests.versions.support import ManagerTestCase


class UpdateTests(ManagerTestCase):
    def release_files(self, version: str, digest: str | None = None):
        name = f"mpv-enhanced-v{version}.zip"
        staging = self.layout.root / name
        with zipfile.ZipFile(staging, "w") as archive:
            archive.writestr(f"mpv-enhanced-v{version}/pyproject.toml", "[project]")
        actual = hashlib.sha256(staging.read_bytes()).hexdigest()
        sums = f"{digest or actual}  {name}\n"

        def download(url: str, destination: Path) -> None:
            if url == "zip":
                destination.write_bytes(staging.read_bytes())
            else:
                destination.write_text(sums, encoding="utf-8")

        release = {
            "tag_name": f"v{version}",
            "assets": [
                {"name": name, "browser_download_url": "zip"},
                {"name": "SHA256SUMS", "browser_download_url": "sums"},
            ],
        }
        return release, download

    def run_update(
        self, release, download, returncode: int = 0, activates: bool = True
    ):
        calls: list[list[str]] = []
        version = str(release["tag_name"]).removeprefix("v")

        def run(command, **_kwargs):
            calls.append(command)
            if returncode == 0 and activates:
                self.install(version, "rtx-high")
                self.layout.activate(version)
            return subprocess.CompletedProcess(command, returncode)

        with patch.dict("os.environ", {"UV": "pinned-uv"}):
            result = update(
                self.layout,
                fetch=lambda _repository: release,
                repository=lambda: "owner/repo",
                download=download,
                run=run,
            )
        return result, calls

    def test_newer_release_is_installed_by_its_own_manager(self) -> None:
        self.install("0.1.0", "rtx-high")
        self.layout.activate("0.1.0")
        release, download = self.release_files("0.2.0")
        result, calls = self.run_update(release, download)
        self.assertEqual(result, "0.2.0")
        self.assertTrue((self.layout.release("0.2.0") / "pyproject.toml").is_file())
        self.assertEqual(calls[0][0], "pinned-uv")
        self.assertEqual(
            calls[0][calls[0].index("--project") + 1], str(self.layout.release("0.2.0"))
        )
        self.assertEqual(calls[0][-2:], ["--profile", "rtx-high"])

    def test_same_or_older_release_changes_nothing(self) -> None:
        self.install("0.2.0")
        self.layout.activate("0.2.0")
        for version in ("0.2.0", "0.1.0"):
            release, download = self.release_files(version)
            self.assertEqual(self.run_update(release, download), (None, []))

    def test_bad_checksum_never_reaches_the_live_folder(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        release, download = self.release_files("0.2.0", digest="0" * 64)
        with self.assertRaisesRegex(InstallerError, "failed its checksum"):
            self.run_update(release, download)
        self.assertFalse((self.layout.live / "0.2.0").exists())
        self.assertEqual(self.layout.active(), "0.1.0")
        self.assertEqual(list(self.layout.live.glob(".update-*")), [])

    def test_failed_build_leaves_the_active_version_alone(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        release, download = self.release_files("0.2.0")
        with self.assertRaisesRegex(InstallerError, "0.1.0 is still active"):
            self.run_update(release, download, returncode=1)
        self.assertFalse((self.layout.live / "0.2.0").exists())
        self.assertEqual(self.layout.active(), "0.1.0")

    def test_git_checkouts_refuse_to_update(self) -> None:
        (self.layout.root / ".git").mkdir()
        with self.assertRaisesRegex(InstallerError, "git pull"):
            update(self.layout)


if __name__ == "__main__":
    unittest.main()
