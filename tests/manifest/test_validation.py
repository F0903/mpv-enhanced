import unittest

from mpv_enhanced.errors import ManifestError, UnsupportedPlatformError
from mpv_enhanced.manifest import Manifest
from mpv_enhanced.platforms import PlatformInfo
from tests.support import ROOT


class ManifestValidationTests(unittest.TestCase):
    def test_profile_cycle_is_rejected(self) -> None:
        data = {
            "schema_version": 1,
            "components": {
                "local": {"kind": "local", "path": "files"},
            },
            "profiles": {
                "one": {"extends": "two", "components": ["local"]},
                "two": {"extends": "one", "components": []},
            },
        }
        with self.assertRaisesRegex(ManifestError, "inheritance cycle"):
            Manifest.from_data(data)

    def test_local_path_traversal_is_rejected(self) -> None:
        data = {
            "schema_version": 1,
            "components": {
                "local": {"kind": "local", "path": "../outside"},
            },
            "profiles": {"core": {"components": ["local"]}},
        }
        with self.assertRaisesRegex(ManifestError, "install tree"):
            Manifest.from_data(data)

    def test_unknown_component_is_rejected_while_loading(self) -> None:
        data = {
            "schema_version": 1,
            "components": {"local": {"kind": "local", "path": "files"}},
            "profiles": {"core": {"components": ["missing"]}},
        }
        with self.assertRaisesRegex(ManifestError, "unknown component"):
            Manifest.from_data(data)

    def test_schema_version_is_required_and_must_equal_one(self) -> None:
        data = {
            "schema_version": 2,
            "components": {"local": {"kind": "local", "path": "files"}},
            "profiles": {"core": {"components": ["local"]}},
        }
        with self.assertRaisesRegex(ManifestError, "schema_version must be 1"):
            Manifest.from_data(data)

        del data["schema_version"]
        with self.assertRaisesRegex(ManifestError, "schema_version must be 1"):
            Manifest.from_data(data)

    def test_os_and_arch_entries_are_validated(self) -> None:
        for os_table, message in (
            ({"window": {"arch": ["x86_64"]}}, "invalid platform key"),
            ({"windows": {"arch": ["mystery"]}}, "unsupported CPU architecture"),
            ({"windows": {"arch": []}}, "at least one architecture"),
            ({}, "at least one operating system"),
        ):
            data = {
                "schema_version": 1,
                "components": {"local": {"kind": "local", "path": "files"}},
                "profiles": {"core": {"components": ["local"], "os": os_table}},
            }
            with (
                self.subTest(os_table=os_table),
                self.assertRaisesRegex(ManifestError, message),
            ):
                Manifest.from_data(data)

    def test_extends_inherits_a_components_fields(self) -> None:
        manifest = Manifest.load(ROOT / "manifest.toml")
        windows = PlatformInfo("windows", "x86_64")
        lite = manifest.components["rife-model"]
        full = manifest.components["rife-model-full"]
        self.assertEqual(full.version, "4.25")
        self.assertTrue(full.artifact_for(windows).url.endswith("/flownet_v4.25.pkl"))
        self.assertTrue(
            full.artifact_for(windows).destination.endswith("/flownet_v4.25.pkl")
        )
        self.assertNotEqual(
            full.artifact_for(windows).sha256, lite.artifact_for(windows).sha256
        )

    def test_choice_ids_must_be_safe_and_not_reserved(self) -> None:
        for identifier in ("none", "has space", "a:b", "UPPER"):
            data = {
                "schema_version": 1,
                "components": {"local": {"kind": "local", "path": "files"}},
                "profiles": {"core": {"components": ["local"]}},
                "methods": {
                    "m": {
                        "description": "d",
                        "components": ["local"],
                        "choices": [
                            {"id": identifier, "title": "T", "rank": 1, "settings": {}}
                        ],
                    }
                },
            }
            with (
                self.subTest(identifier=identifier),
                self.assertRaisesRegex(ManifestError, "usable choice id"),
            ):
                Manifest.from_data(data)

    def test_extending_an_unknown_component_is_rejected(self) -> None:
        data = {
            "schema_version": 1,
            "components": {
                "child": {"extends": "missing", "kind": "local", "path": "x"}
            },
            "profiles": {"core": {"components": ["child"]}},
        }
        with self.assertRaisesRegex(ManifestError, "extends unknown component"):
            Manifest.from_data(data)

    def test_default_profile_must_exist(self) -> None:
        data = {
            "schema_version": 1,
            "default_profile": "missing",
            "components": {"local": {"kind": "local", "path": "files"}},
            "profiles": {"core": {"components": ["local"]}},
        }
        with self.assertRaisesRegex(ManifestError, "unknown default profile"):
            Manifest.from_data(data)

    def test_placeholders_must_have_a_value(self) -> None:
        for component in (
            {"version": "1.0", "url": "https://example.invalid/{nope}"},
            {"url": "https://example.invalid/{version}"},
        ):
            data = {
                "schema_version": 1,
                "components": {
                    "tool": {
                        "kind": "download",
                        "sha256": "0" * 64,
                        "os": {"windows": {"arch": ["x86_64"]}},
                        **component,
                    }
                },
                "profiles": {"core": {"components": ["tool"]}},
            }
            with (
                self.subTest(component=component),
                self.assertRaisesRegex(ManifestError, "cannot expand"),
            ):
                Manifest.from_data(data)

    def test_arch_entries_override_the_components_shared_fields(self) -> None:
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "tool": {
                        "kind": "download",
                        "url": "https://example.invalid/shared",
                        "sha256": "0" * 64,
                        "os": {
                            "windows": {
                                "arch": {
                                    "x86_64": {"url": "https://example.invalid/own"}
                                }
                            },
                            "linux": {"arch": ["x86_64"]},
                        },
                    }
                },
                "profiles": {"core": {"components": ["tool"]}},
            },
        )
        tool = manifest.components["tool"]
        self.assertTrue(
            tool.artifact_for(PlatformInfo("windows", "x86_64")).url.endswith("/own")
        )
        self.assertTrue(
            tool.artifact_for(PlatformInfo("linux", "x86_64")).url.endswith("/shared")
        )

    def test_other_os_is_skipped_but_missing_arch_is_an_error(self) -> None:
        artifact = {"url": "https://example.invalid/tool", "sha256": "0" * 64}
        manifest = Manifest.from_data(
            {
                "schema_version": 1,
                "components": {
                    "tool": {
                        "kind": "download",
                        "os": {"windows": {"arch": {"x86_64": artifact}}},
                    }
                },
                "profiles": {"core": {"components": ["tool"]}},
            },
        )
        linux = manifest.resolve_profile("core", PlatformInfo("linux", "x86_64"))
        self.assertEqual(linux.components, ())
        with self.assertRaisesRegex(UnsupportedPlatformError, "'tool'"):
            manifest.resolve_profile("core", PlatformInfo("windows", "aarch64"))


if __name__ == "__main__":
    unittest.main()
