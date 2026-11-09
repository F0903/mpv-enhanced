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
from mpv_enhanced_installer.version import VERSION

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
            "archive": archive,
            "destination": destination,
        }

    return Manifest.from_data(
        {
            "schema_version": 1,
            "components": {
                "mpv": {
                    "kind": "download",
                    "os": {
                        "windows": {
                            "arch": {
                                "x86_64": artifact(
                                    b"archive",
                                    archive="zip",
                                    destination=".",
                                )
                            }
                        }
                    },
                },
                "system-mpv": {
                    "kind": "system",
                    "command": "mpv",
                    "min_version": "0.36.0",
                    "os": {"linux": {"arch": ["x86_64"]}},
                },
                "portable-config": {
                    "kind": "local",
                    "path": "portable_config",
                    "destination": "portable_config",
                },
                "rife-runtime": {
                    "kind": "uv-runtime",
                    "project": "runtime/rife",
                },
                "model": {
                    "kind": "download",
                    "os": {
                        "windows": {
                            "arch": {
                                "x86_64": artifact(
                                    MODEL,
                                    archive="none",
                                    destination=(
                                        "runtime/venv/Lib/site-packages/"
                                        "vsrife/models/model.pkl"
                                    ),
                                )
                            }
                        }
                    },
                },
            },
            "profiles": {
                "core": {
                    "components": ["system-mpv", "mpv", "portable-config"],
                    "os": {
                        "windows": {"arch": ["x86_64"]},
                        "linux": {"arch": ["x86_64"]},
                    },
                },
                "rife": {
                    "extends": "core",
                    "components": ["rife-runtime", "model"],
                    "os": {"windows": {"arch": ["x86_64"]}},
                },
            },
        },
        python_version="3.12.13",
    )


def prepare_source(root: Path) -> Path:
    source = root / "source"
    (source / "portable_config").mkdir(parents=True)
    (source / "portable_config" / "mpv.conf").write_text("config")
    (source / "runtime" / "rife").mkdir(parents=True)
    (source / "runtime" / "rife" / "uv.lock").write_text("lock")
    return source


def write_receipt(
    install: Path,
    profile: str,
    components: list[str],
    platform: str = "windows-x86_64",
) -> None:
    InstallationReceipt.create(
        release=VERSION,
        profile=profile,
        platform=platform,
        components=tuple(
            InstalledComponent(name=name, version="test", kind="test")
            for name in components
        ),
    ).write(install)


class DoctorTests(unittest.TestCase):
    def test_installed_methods_are_expected_in_the_receipt(self) -> None:
        data = {
            "schema_version": 1,
            "components": {
                "config": {
                    "kind": "local",
                    "path": "portable_config",
                    "destination": "portable_config",
                },
                "extra": {
                    "kind": "local",
                    "path": "portable_config",
                    "destination": "extra",
                },
            },
            "profiles": {"core": {"components": ["config"]}},
            "methods": {
                "m": {
                    "description": "A method",
                    "components": ["extra"],
                    "choices": [{"id": "a", "title": "A", "rank": 1, "settings": {}}],
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = prepare_source(root)
            install = root / "install"
            (install / "portable_config").mkdir(parents=True)
            InstallationReceipt.create(
                release=VERSION,
                profile="core",
                platform="windows-x86_64",
                components=tuple(
                    InstalledComponent(name=name, version="test", kind="test")
                    for name in ("config", "extra")
                ),
                methods=("m",),
            ).write(install)
            checks = run_doctor(
                Manifest.from_data(data),
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
        by_name = {check.name: check for check in checks}
        self.assertTrue(by_name["receipt:components"].ok, by_name["receipt:components"])

    def test_profile_is_inferred_and_core_files_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = prepare_source(root)
            install = root / "install"
            (install / "portable_config").mkdir(parents=True)
            (install / "portable_config" / "mpv.conf").write_text("config")
            write_receipt(install, "core", ["mpv", "portable-config"])

            checks = run_doctor(
                test_manifest(),
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
            by_name = {check.name: check for check in checks}
            self.assertFalse(by_name["installed:mpv"].ok)
            self.assertIn("profile:core", by_name)
            self.assertNotIn("profile:rife", by_name)
            self.assertNotIn("installed:launcher", by_name)

            (install / "mpv.exe").write_bytes(b"mpv")
            checks = run_doctor(
                test_manifest(),
                platform=PlatformInfo("windows", "x86_64"),
                source_root=source,
                install_dir=install,
            )
            self.assertTrue(all(check.ok for check in checks), checks)

    def test_rife_audit_requires_runtime_vsscript_python_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = prepare_source(root)
            install = root / "install"
            required = [
                install / "mpv.exe",
                install / "portable_config" / "mpv.conf",
                install / "runtime" / "venv" / "Scripts" / "python.exe",
                install / "VSScript.dll",
            ]
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"present")
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
                ["mpv", "portable-config", "rife-runtime", "model"],
            )

            with patch.dict("os.environ", {"UV": "pinned-uv"}):
                checks = run_doctor(
                    test_manifest(),
                    platform=PlatformInfo("windows", "x86_64"),
                    source_root=source,
                    install_dir=install,
                )
                self.assertTrue(all(check.ok for check in checks), checks)

                model.write_bytes(b"tampered")
                checks = run_doctor(
                    test_manifest(),
                    platform=PlatformInfo("windows", "x86_64"),
                    source_root=source,
                    install_dir=install,
                )
            self.assertFalse(next(c for c in checks if c.name == "installed:model").ok)

    def test_missing_uv_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = prepare_source(Path(temporary))
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("mpv_enhanced_installer.doctor.shutil.which", return_value=None),
            ):
                checks = run_doctor(
                    test_manifest(),
                    platform=PlatformInfo("windows", "x86_64"),
                    source_root=source,
                    profile_name="rife",
                )
            uv = next(check for check in checks if check.name == "runtime:uv")
            self.assertFalse(uv.ok)

    def test_linux_checks_system_mpv_and_needs_no_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = prepare_source(root)
            install = root / "install"
            (install / "portable_config").mkdir(parents=True)
            (install / "portable_config" / "mpv.conf").write_text("config")
            write_receipt(
                install, "core", ["system-mpv", "portable-config"], "linux-x86_64"
            )
            with patch(
                "mpv_enhanced_installer.doctor.check_system_program",
                return_value=("/usr/bin/mpv", "0.42.0"),
            ):
                checks = run_doctor(
                    test_manifest(),
                    platform=PlatformInfo("linux", "x86_64"),
                    source_root=source,
                    install_dir=install,
                )
                by_name = {check.name: check for check in checks}
                self.assertTrue(by_name["system:system-mpv"].ok)
                self.assertNotIn("installed:mpv", by_name)
                self.assertNotIn("installed:launcher", by_name)
            self.assertTrue(all(check.ok for check in checks), checks)


if __name__ == "__main__":
    unittest.main()
