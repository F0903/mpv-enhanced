import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced_installer.downloads import ArtifactCache
from mpv_enhanced_installer.errors import (
    InstallerError,
    OfflineRuntimeUnsupportedError,
)
from mpv_enhanced_installer.installer import (
    Installer,
    check_gpu,
    check_system_program,
)
from mpv_enhanced_installer.manifest import GpuRequirement, Manifest, SystemProgram
from mpv_enhanced_installer.platforms import PlatformInfo
from mpv_enhanced_installer.receipt import InstallationReceipt
from mpv_enhanced_installer.version import VERSION


def local_manifest(path: str = "portable_config") -> Manifest:
    return Manifest.from_data(
        {
            "schema_version": 1,
            "components": {
                "config": {
                    "kind": "local",
                    "version": "test",
                    "path": path,
                    "destination": "portable_config",
                }
            },
            "profiles": {
                "core": {"components": ["config"]},
                "rife-tensorrt": {
                    "extends": "core",
                    "components": [],
                    "extras": ["tensorrt"],
                },
            },
        },
    )


class GpuCheckTests(unittest.TestCase):
    REQUIRED = (
        Manifest.load(Path(__file__).parents[1] / "manifest.toml")
        .components["nvidia-rtx-gpu"]
        .gpu
    )

    def check(self, names: str, tool: str | None = "nvidia-smi") -> str:
        assert isinstance(self.REQUIRED, GpuRequirement)
        return check_gpu(self.REQUIRED, run=lambda _: names, which=lambda _: tool)

    def test_supported_cards_are_accepted(self) -> None:
        for name in (
            "NVIDIA GeForce RTX 4060 Laptop GPU",
            "NVIDIA GeForce RTX 4080",
            "NVIDIA GeForce RTX 5090",
            "NVIDIA RTX 6000 Ada Generation",
            "NVIDIA RTX PRO 6000 Blackwell",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.check(name), name)

    def test_any_supported_card_in_the_system_is_enough(self) -> None:
        names = "NVIDIA GeForce GTX 1050\nNVIDIA GeForce RTX 4070\n"
        self.assertEqual(self.check(names), "NVIDIA GeForce RTX 4070")

    def test_older_and_missing_cards_are_rejected_with_a_way_forward(self) -> None:
        for names, tool in (
            ("NVIDIA GeForce RTX 3080", "nvidia-smi"),
            ("NVIDIA GeForce GTX 1660", "nvidia-smi"),
            ("Quadro RTX 4000", "nvidia-smi"),
            ("", None),
        ):
            with (
                self.subTest(names=names),
                self.assertRaisesRegex(InstallerError, "choose another profile"),
            ):
                self.check(names, tool)


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
            self.assertEqual(receipt.release, VERSION)
            self.assertEqual(receipt.profile, "core")
            self.assertEqual([item.name for item in receipt.components], ["config"])

            (target / "portable_config" / "mpv-local.conf").write_text(
                "my overrides", encoding="utf-8"
            )
            # mpv resume points and cached TensorRT engines are user state too.
            engines = target / "portable_config" / "cache" / "tensorrt"
            engines.mkdir(parents=True)
            (engines / "720p.ts").write_bytes(b"engine")
            (target / "portable_config" / "watch_later").mkdir()
            (target / "portable_config" / "watch_later" / "ABC").write_text("start=42")
            (source / "mpv.conf").write_text("managed-v2", encoding="utf-8")
            second = installer.install("core", target)
            self.assertEqual(second.previous_dir, root / "installed.previous")
            self.assertEqual((engines / "720p.ts").read_bytes(), b"engine")
            self.assertEqual(
                (target / "portable_config" / "watch_later" / "ABC").read_text(),
                "start=42",
            )
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

    def test_component_config_key_writes_its_version_to_the_profile(self) -> None:
        artifact = {"url": "https://example.invalid/m", "sha256": "0" * 64}
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "model": {
                        "kind": "download",
                        "version": "9.9.lite",
                        "config_key": "rife_model",
                        "os": {"windows": {"arch": {"x86_64": artifact}}},
                    }
                },
                "profiles": {"rife": {"components": ["model"]}},
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "portable_config").mkdir()
            installer = Installer(
                manifest,
                ArtifactCache(root / "cache"),
                source_root=root,
                platform=PlatformInfo("windows", "x86_64"),
            )
            installer._write_managed_profile(root, manifest.resolve_profile("rife"))
            text = (root / "portable_config" / "rife_profile.py").read_text()
        self.assertIn("rife_model = '9.9.lite'", text)
        self.assertIn("tensorrt = False", text)

    def test_offline_uv_runtime_is_rejected_before_staging(self) -> None:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "runtime": {
                        "kind": "uv-runtime",
                        "project": "runtime/rife",
                    }
                },
                "profiles": {"rife": {"components": ["runtime"]}},
            },
            python_version="3.12.13",
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

    def test_linux_core_checks_system_mpv_first_and_writes_a_launcher(self) -> None:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "system-mpv": {
                        "kind": "system",
                        "command": "mpv",
                        "min_version": "0.36",
                    },
                    "config": {
                        "kind": "local",
                        "path": "portable_config",
                        "destination": "portable_config",
                    },
                },
                "profiles": {"core": {"components": ["system-mpv", "config"]}},
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source" / "portable_config").mkdir(parents=True)

            def missing_mpv(_program):
                raise InstallerError("'mpv' was not found on PATH")

            failing = Installer(
                manifest,
                ArtifactCache(root / "cache"),
                source_root=root / "source",
                platform=PlatformInfo("linux", "x86_64"),
                system_check=missing_mpv,
            )
            with self.assertRaisesRegex(InstallerError, "not found on PATH"):
                failing.install("core", root / "installed")
            self.assertFalse((root / "installed").exists())

            installer = Installer(
                manifest,
                ArtifactCache(root / "cache"),
                source_root=root / "source",
                platform=PlatformInfo("linux", "x86_64"),
                system_check=lambda _program: ("/usr/bin/mpv", "0.42.0"),
            )
            result = installer.install("core", root / "installed")
            self.assertIn(
                "--config-dir=", (root / "installed" / "mpv-enhanced").read_text()
            )
            recorded = result.receipt.components[0]
            self.assertEqual(
                (recorded.kind, recorded.version, recorded.source),
                ("system", "0.42.0", "/usr/bin/mpv"),
            )

    def test_downloaded_file_replaces_only_an_empty_placeholder(self) -> None:
        model = b"model-bytes"
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "placeholder": {
                        "kind": "local",
                        "path": "model.pkl",
                        "destination": "models/model.pkl",
                    },
                    "model": {
                        "kind": "download",
                        "os": {
                            "windows": {
                                "arch": {
                                    "x86_64": {
                                        "url": "https://example.invalid/model.pkl",
                                        "sha256": hashlib.sha256(model).hexdigest(),
                                        "archive": "none",
                                        "destination": "models/model.pkl",
                                    }
                                }
                            }
                        },
                    },
                },
                "profiles": {"rife": {"components": ["placeholder", "model"]}},
            },
        )
        for placeholder, should_install in ((b"", True), (b"real", False)):
            with (
                self.subTest(placeholder=placeholder),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                (root / "source").mkdir()
                (root / "source" / "model.pkl").write_bytes(placeholder)
                installer = Installer(
                    manifest,
                    ArtifactCache(
                        root / "cache",
                        downloader=lambda _url, path: path.write_bytes(model),
                    ),
                    source_root=root / "source",
                    platform=PlatformInfo("windows", "x86_64"),
                )
                target = root / "installed"
                if should_install:

                    def hardlink(source: Path, destination: Path) -> None:
                        # Stand-in for uv, which hardlinks venv files to its cache.
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        os.link(source, destination)

                    with patch(
                        "mpv_enhanced_installer.installer._copy_local", hardlink
                    ):
                        installer.install("rife", target)
                    self.assertEqual(
                        (target / "models" / "model.pkl").read_bytes(), model
                    )
                    # The "cache" side of the hardlink must stay untouched.
                    self.assertEqual((root / "source" / "model.pkl").read_bytes(), b"")
                else:
                    with self.assertRaisesRegex(InstallerError, "collides"):
                        installer.install("rife", target)


class SystemProgramTests(unittest.TestCase):
    program = SystemProgram(
        command="mpv",
        min_version=(0, 42, 0),
        check_args=("--vf=help",),
        check_contains="vapoursynth",
    )

    def check(self, outputs: dict[str, str], path: str | None = "/usr/bin/mpv"):
        return check_system_program(
            self.program,
            run=lambda command: outputs[command[-1]],
            which=lambda _name: path,
        )

    def test_accepts_new_enough_mpv_with_the_filter(self) -> None:
        found = self.check(
            {
                "--version": "mpv v0.42.0-dev-g1a2b3c Copyright",
                "--vf=help": "  vapoursynth  VapourSynth bridge",
            }
        )
        self.assertEqual(found, ("/usr/bin/mpv", "0.42.0"))

    def test_rejects_missing_old_or_featureless_mpv(self) -> None:
        cases = {
            "not found on PATH": ({}, None),
            "0.41.0; this profile needs mpv 0.42.0": (
                {"--version": "mpv 0.41.0-2 Copyright"},
                "/usr/bin/mpv",
            ),
            "without 'vapoursynth'": (
                {"--version": "mpv v0.42.0", "--vf=help": "  crop  Crop"},
                "/usr/bin/mpv",
            ),
        }
        for message, (outputs, path) in cases.items():
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(InstallerError, message),
            ):
                self.check(outputs, path)


if __name__ == "__main__":
    unittest.main()
