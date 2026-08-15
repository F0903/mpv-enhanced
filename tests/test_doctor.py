import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced_installer.doctor import run_doctor
from mpv_enhanced_installer.manifest import Manifest
from mpv_enhanced_installer.platforms import PlatformInfo
from mpv_enhanced_installer.receipt import (
    InstallationReceipt,
    InstalledComponent,
)

SEVENZIP = b"locked-sevenzip"
MODEL = b"locked-model"


def test_manifest() -> Manifest:
    def artifact(
        content: bytes,
        *,
        archive: str,
        destination: str,
    ) -> dict[str, object]:
        return {
            "url": f"https://example.invalid/{destination}",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "archive": archive,
            "destination": destination,
        }

    return Manifest.from_data(
        {
            "schema_version": 1,
            "release": {"version": "test"},
            "components": {
                "sevenzip": {
                    "kind": "download",
                    "artifacts": {
                        "windows-x86_64": artifact(
                            SEVENZIP,
                            archive="none",
                            destination="tools/7zr.exe",
                        )
                    },
                },
                "mpv": {
                    "kind": "download",
                    "artifacts": {
                        "windows-x86_64": artifact(
                            b"archive",
                            archive="7z",
                            destination=".",
                        )
                    },
                },
                "portable-config": {
                    "kind": "local",
                    "path": "portable_config",
                    "destination": "portable_config",
                },
                "uv": {
                    "kind": "download",
                    "artifacts": {
                        "windows-x86_64": artifact(
                            b"uv-archive",
                            archive="zip",
                            destination="runtime/uv",
                        )
                    },
                },
                "rife-runtime": {
                    "kind": "uv-runtime",
                    "project": "runtime/rife",
                    "lockfile": "runtime/rife/uv.lock",
                    "python_version": "3.12.13",
                },
                "model": {
                    "kind": "download",
                    "artifacts": {
                        "windows-x86_64": artifact(
                            MODEL,
                            archive="none",
                            destination=(
                                "runtime/venv/Lib/site-packages/vsrife/models/model.pkl"
                            ),
                        )
                    },
                },
            },
        },
        {
            "schema_version": 1,
            "profiles": {
                "core": {
                    "components": ["sevenzip", "mpv", "portable-config"],
                    "platforms": ["windows-x86_64"],
                },
                "rife": {
                    "extends": "core",
                    "components": ["uv", "rife-runtime", "model"],
                    "platforms": ["windows-x86_64"],
                },
            },
        },
    )


def prepare_source(root: Path) -> Path:
    source = root / "source"
    (source / "portable_config").mkdir(parents=True)
    (source / "portable_config" / "mpv.conf").write_text("config")
    (source / "runtime" / "rife").mkdir(parents=True)
    (source / "runtime" / "rife" / "uv.lock").write_text("lock")
    return source


def write_receipt(install: Path, profile: str, components: list[str]) -> None:
    InstallationReceipt.create(
        release="test",
        profile=profile,
        platform="windows-x86_64",
        components=tuple(
            InstalledComponent(name=name, version="test", kind="test")
            for name in components
        ),
    ).write(install)


class DoctorTests(unittest.TestCase):
    def test_opaque_root_payload_requires_core_product_inventory(self) -> None:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "release": {"version": "payload-test"},
                "components": {
                    "arbitrary-release-bundle-name": {
                        "kind": "local",
                        "path": "payload/core",
                        "destination": ".",
                    }
                },
            },
            {
                "schema_version": 1,
                "profiles": {"core": {"components": ["arbitrary-release-bundle-name"]}},
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "payload" / "core").mkdir(parents=True)
            install = root / "install"
            install.mkdir()
            InstallationReceipt.create(
                release="payload-test",
                profile="core",
                platform="windows-x86_64",
                components=(
                    InstalledComponent(
                        name="arbitrary-release-bundle-name",
                        version="test",
                        kind="local",
                    ),
                ),
            ).write(install)

            checks = run_doctor(
                manifest,
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
            by_name = {check.name: check for check in checks}
            self.assertFalse(by_name["installed:mpv"].ok)
            self.assertFalse(by_name["installed:portable-config"].ok)

            (install / "mpv.exe").write_bytes(b"mpv")
            (install / "portable_config").mkdir()
            (install / "portable_config" / "mpv.conf").write_text("config")
            checks = run_doctor(
                manifest,
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
            self.assertTrue(all(check.ok for check in checks), checks)

    def test_profile_is_inferred_and_core_files_and_hashes_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = prepare_source(root)
            install = root / "install"
            (install / "tools").mkdir(parents=True)
            (install / "portable_config").mkdir()
            (install / "tools" / "7zr.exe").write_bytes(SEVENZIP)
            (install / "mpv.exe").write_bytes(b"mpv")
            (install / "portable_config" / "mpv.conf").write_text("config")
            write_receipt(
                install,
                "core",
                ["sevenzip", "mpv", "portable-config"],
            )

            checks = run_doctor(
                test_manifest(),
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )

            self.assertTrue(all(check.ok for check in checks), checks)
            self.assertIn("profile:core", {check.name for check in checks})
            self.assertIn("installed:sevenzip", {check.name for check in checks})
            self.assertNotIn("profile:rife", {check.name for check in checks})

            (install / "tools" / "7zr.exe").write_bytes(b"tampered")
            checks = run_doctor(
                test_manifest(),
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
            sevenzip = next(
                check for check in checks if check.name == "installed:sevenzip"
            )
            self.assertFalse(sevenzip.ok)

    def test_rife_audit_requires_runtime_launcher_uv_python_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = prepare_source(root)
            install = root / "install"
            required = [
                install / "tools" / "7zr.exe",
                install / "mpv.exe",
                install / "portable_config" / "mpv.conf",
                install / "runtime" / "uv" / "uv.exe",
                install / "runtime" / "venv" / "Scripts" / "python.exe",
                install / "mpv-enhanced.cmd",
            ]
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(SEVENZIP if path.name == "7zr.exe" else b"present")
            model = (
                install
                / "runtime"
                / "venv"
                / "Lib"
                / "site-packages"
                / "vsrife"
                / "models"
                / "model.pkl"
            )
            model.parent.mkdir(parents=True)
            model.write_bytes(MODEL)
            write_receipt(
                install,
                "rife",
                [
                    "sevenzip",
                    "mpv",
                    "portable-config",
                    "uv",
                    "rife-runtime",
                    "model",
                ],
            )

            checks = run_doctor(
                test_manifest(),
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
            self.assertTrue(all(check.ok for check in checks), checks)

            model.unlink()
            checks = run_doctor(
                test_manifest(),
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
            self.assertFalse(next(c for c in checks if c.name == "installed:model").ok)

    def test_locked_helpers_make_source_doctor_independent_of_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = prepare_source(Path(temporary))
            with (
                patch.dict(
                    "os.environ",
                    {"PATH": ""},
                    clear=True,
                ),
                patch("mpv_enhanced_installer.doctor.shutil.which", return_value=None),
            ):
                checks = run_doctor(
                    test_manifest(),
                    platform=PlatformInfo("windows", "x86_64"),
                    source_root=source,
                    profile_name="rife",
                )
            self.assertTrue(all(check.ok for check in checks), checks)
            details = {check.name: check.detail for check in checks}
            self.assertIn("locked sevenzip", details["archive:7z"])
            self.assertIn("locked uv", details["runtime:uv"])


if __name__ == "__main__":
    unittest.main()
