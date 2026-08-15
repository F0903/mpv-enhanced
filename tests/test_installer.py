import tempfile
import unittest
from pathlib import Path

from mpv_enhanced_installer.downloads import ArtifactCache
from mpv_enhanced_installer.errors import (
    InstallerError,
    OfflineRuntimeUnsupportedError,
)
from mpv_enhanced_installer.installer import Installer
from mpv_enhanced_installer.manifest import Manifest
from mpv_enhanced_installer.platforms import PlatformInfo
from mpv_enhanced_installer.receipt import InstallationReceipt


def local_manifest(path: str = "portable_config") -> Manifest:
    return Manifest.from_data(
        {
            "schema_version": 1,
            "release": {"version": "1.2.3"},
            "components": {
                "config": {
                    "kind": "local",
                    "version": "test",
                    "path": path,
                    "destination": "portable_config",
                }
            },
        },
        {
            "schema_version": 1,
            "profiles": {
                "core": {"components": ["config"]},
                "rife-tensorrt": {
                    "extends": "core",
                    "components": [],
                },
            },
        },
    )


class TransactionalInstallerTests(unittest.TestCase):
    def test_install_preserves_user_files_writes_receipt_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "portable_config"
            source.mkdir(parents=True)
            (source / "mpv.conf").write_text("managed-v1", encoding="utf-8")
            (source / "mpv-local.conf.example").write_text(
                "local-default", encoding="utf-8"
            )
            (source / "rife_config_local.py.example").write_text(
                "scale = 1", encoding="utf-8"
            )
            installer = Installer(
                local_manifest(),
                ArtifactCache(root / "cache"),
                source_root=root / "source",
                platform=PlatformInfo("windows", "x86_64"),
            )
            target = root / "installed"

            first = installer.install("core", target, offline=True)
            self.assertIsNone(first.previous_dir)
            self.assertEqual(
                (target / "portable_config" / "mpv-local.conf").read_text(
                    encoding="utf-8"
                ),
                "local-default",
            )
            self.assertIn(
                "tensorrt = False",
                (target / "portable_config" / "rife_profile.py").read_text(
                    encoding="utf-8"
                ),
            )
            receipt = InstallationReceipt.load(target)
            self.assertEqual(receipt.release, "1.2.3")
            self.assertEqual(receipt.profile, "core")
            self.assertEqual([item.name for item in receipt.components], ["config"])

            (target / "portable_config" / "mpv-local.conf").write_text(
                "my overrides", encoding="utf-8"
            )
            (source / "mpv.conf").write_text("managed-v2", encoding="utf-8")
            second = installer.install("core", target)
            self.assertEqual(second.previous_dir, root / "installed.previous")
            self.assertEqual(
                (target / "portable_config" / "mpv-local.conf").read_text(
                    encoding="utf-8"
                ),
                "my overrides",
            )
            self.assertEqual(
                (target / "portable_config" / "mpv.conf").read_text(encoding="utf-8"),
                "managed-v2",
            )
            self.assertEqual(
                (
                    root / "installed.previous" / "portable_config" / "mpv.conf"
                ).read_text(encoding="utf-8"),
                "managed-v1",
            )

            Installer.rollback(target)
            self.assertEqual(
                (target / "portable_config" / "mpv.conf").read_text(encoding="utf-8"),
                "managed-v1",
            )
            self.assertEqual(
                (
                    root / "installed.previous" / "portable_config" / "mpv.conf"
                ).read_text(encoding="utf-8"),
                "managed-v2",
            )

    def test_failed_staging_leaves_existing_install_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installed"
            target.mkdir()
            (target / "sentinel.txt").write_text("keep", encoding="utf-8")
            installer = Installer(
                local_manifest("missing"),
                ArtifactCache(root / "cache"),
                source_root=root / "source",
                platform=PlatformInfo("windows", "x86_64"),
            )

            with self.assertRaises(InstallerError):
                installer.install("core", target)

            self.assertEqual((target / "sentinel.txt").read_text(), "keep")
            self.assertFalse((root / "installed.previous").exists())
            self.assertEqual(list(root.glob(".installed.staging-*")), [])

    def test_tensorrt_profile_writes_managed_true_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "portable_config"
            source.mkdir(parents=True)
            installer = Installer(
                local_manifest(),
                ArtifactCache(root / "cache"),
                source_root=root / "source",
                platform=PlatformInfo("windows", "x86_64"),
            )
            target = root / "installed"
            installer.install("rife-tensorrt", target)
            self.assertIn(
                "tensorrt = True",
                (target / "portable_config" / "rife_profile.py").read_text(),
            )

    def test_offline_uv_runtime_is_rejected_before_staging(self) -> None:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "runtime": {
                        "kind": "uv-runtime",
                        "project": "runtime/rife",
                        "lockfile": "runtime/rife/uv.lock",
                        "python_version": "3.12.13",
                    }
                },
            },
            {
                "schema_version": 1,
                "profiles": {"rife": {"components": ["runtime"]}},
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = Installer(
                manifest,
                ArtifactCache(root / "cache"),
                source_root=root / "source",
                platform=PlatformInfo("windows", "x86_64"),
            )
            target = root / "nested" / "installed"

            with self.assertRaisesRegex(
                OfflineRuntimeUnsupportedError,
                "--offline currently supports only profiles",
            ):
                installer.install("rife", target, offline=True)

            self.assertFalse(target.parent.exists())
            self.assertFalse((root / "cache").exists())


if __name__ == "__main__":
    unittest.main()
