import unittest
from pathlib import Path

from mpv_enhanced_installer.errors import ManifestError, UnsupportedPlatformError
from mpv_enhanced_installer.manifest import Manifest
from mpv_enhanced_installer.platforms import PlatformInfo

ROOT = Path(__file__).resolve().parents[1]


class RealManifestTests(unittest.TestCase):
    def test_repository_manifests_load_and_resolve_in_order(self) -> None:
        manifest = Manifest.load(ROOT / "deps.lock.toml", ROOT / "profiles.toml")
        resolved = manifest.resolve_profile(
            "rife-tensorrt", PlatformInfo("windows", "x86_64")
        )

        self.assertEqual(
            [component.name for component in resolved.components],
            [
                "sevenzip",
                "mpv",
                "portable-config",
                "uv",
                "rife-tensorrt",
                "rife-model-4-25-lite",
            ],
        )
        runtime = manifest.components["rife-tensorrt"].runtime
        self.assertIsNotNone(runtime)
        assert runtime is not None
        self.assertEqual(runtime.python_version, "3.12.13")
        self.assertEqual(runtime.environment, "runtime/venv")
        self.assertEqual(runtime.extras, ("tensorrt",))
        mpv_artifact = manifest.components["mpv"].artifact_for(
            PlatformInfo("windows", "x86_64")
        )
        self.assertEqual(
            mpv_artifact.exclude,
            ("installer", "updater.bat", "mpv-register.bat", "mpv-unregister.bat"),
        )

    def test_repository_profiles_reject_other_platforms(self) -> None:
        manifest = Manifest.load(ROOT / "deps.lock.toml", ROOT / "profiles.toml")
        with self.assertRaises(UnsupportedPlatformError):
            manifest.resolve_profile("core", PlatformInfo("linux", "x86_64"))


class ManifestValidationTests(unittest.TestCase):
    def test_profile_cycle_is_rejected(self) -> None:
        dependencies = {
            "schema_version": 1,
            "components": {
                "local": {"kind": "local", "path": "files"},
            },
        }
        profiles = {
            "schema_version": 1,
            "profiles": {
                "one": {"extends": "two", "components": ["local"]},
                "two": {"extends": "one", "components": []},
            },
        }
        with self.assertRaisesRegex(ManifestError, "inheritance cycle"):
            Manifest.from_data(dependencies, profiles)

    def test_local_path_traversal_is_rejected(self) -> None:
        dependencies = {
            "schema_version": 1,
            "components": {
                "local": {"kind": "local", "path": "../outside"},
            },
        }
        profiles = {
            "schema_version": 1,
            "profiles": {"core": {"components": ["local"]}},
        }
        with self.assertRaisesRegex(ManifestError, "install tree"):
            Manifest.from_data(dependencies, profiles)

    def test_unknown_component_is_rejected_while_loading(self) -> None:
        dependencies = {
            "schema_version": 1,
            "components": {"local": {"kind": "local", "path": "files"}},
        }
        profiles = {
            "schema_version": 1,
            "profiles": {"core": {"components": ["missing"]}},
        }
        with self.assertRaisesRegex(ManifestError, "unknown component"):
            Manifest.from_data(dependencies, profiles)

    def test_both_schema_versions_are_required_and_must_equal_one(self) -> None:
        dependencies = {
            "schema_version": 2,
            "components": {"local": {"kind": "local", "path": "files"}},
        }
        profiles = {
            "schema_version": 1,
            "profiles": {"core": {"components": ["local"]}},
        }
        with self.assertRaisesRegex(ManifestError, "dependency lock schema_version"):
            Manifest.from_data(dependencies, profiles)

        dependencies["schema_version"] = 1
        del profiles["schema_version"]
        with self.assertRaisesRegex(ManifestError, "profiles manifest schema_version"):
            Manifest.from_data(dependencies, profiles)

    def test_download_artifact_size_must_be_positive(self) -> None:
        dependencies = {
            "schema_version": 1,
            "components": {
                "download": {
                    "kind": "download",
                    "artifacts": {
                        "any": {
                            "url": "https://example.invalid/file",
                            "sha256": "0" * 64,
                            "size": 0,
                            "archive": "none",
                            "destination": "file",
                        }
                    },
                }
            },
        }
        profiles = {
            "schema_version": 1,
            "profiles": {"core": {"components": ["download"]}},
        }
        with self.assertRaisesRegex(ManifestError, "size must be a positive integer"):
            Manifest.from_data(dependencies, profiles)


class PlatformTests(unittest.TestCase):
    def test_common_windows_values_are_normalized(self) -> None:
        platform = PlatformInfo.detect(system="Windows", machine="AMD64")
        self.assertEqual(platform.key, "windows-x86_64")
        self.assertEqual(platform.artifact_keys, ("windows-x86_64", "windows", "any"))

    def test_unknown_architecture_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedPlatformError):
            PlatformInfo.detect(system="Windows", machine="mystery-cpu")


if __name__ == "__main__":
    unittest.main()
