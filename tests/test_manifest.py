import unittest
from pathlib import Path

from mpv_enhanced_installer.errors import ManifestError, UnsupportedPlatformError
from mpv_enhanced_installer.manifest import Manifest
from mpv_enhanced_installer.platforms import PlatformInfo

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
WINDOWS = PlatformInfo("windows", "x86_64")
LINUX = PlatformInfo("linux", "x86_64")


def load() -> Manifest:
    return Manifest.load(ROOT / "manifest.toml")


class RealManifestTests(unittest.TestCase):
    def test_repository_manifests_load_and_resolve_in_order(self) -> None:
        manifest = Manifest.load(ROOT / "manifest.toml")
        resolved = manifest.resolve_profile(
            "core", PlatformInfo("windows", "x86_64"), methods=("rife-tensorrt",)
        )

        self.assertEqual(
            [component.name for component in resolved.components],
            [
                "mpv",
                "portable-config",
                "nvidia-gpu",
                "rife-runtime",
                "rife-model",
                "rife-model-full",
            ],
        )
        runtime = resolved.components[3].runtime
        self.assertIsNotNone(runtime)
        assert runtime is not None
        self.assertEqual(runtime.python_version, PYTHON_VERSION)
        self.assertEqual(runtime.environment, "runtime/venv")
        self.assertEqual(runtime.extras, ("tensorrt",))
        self.assertEqual(resolved.extras, ("tensorrt",))
        mpv = manifest.components["mpv"]
        mpv_artifact = mpv.artifact_for(PlatformInfo("windows", "x86_64"))
        self.assertIn(f"/v{mpv.version}/mpv-v{mpv.version}-", mpv_artifact.url)
        self.assertIn("mpv-register.bat", mpv_artifact.exclude)

    def test_core_is_the_only_profile_and_the_default(self) -> None:
        manifest = load()
        self.assertEqual(list(manifest.profiles), ["core"])
        self.assertEqual(manifest.default_profile, "core")

    def test_rtx_method_adds_its_components_and_extra(self) -> None:
        resolved = load().resolve_profile("core", WINDOWS, methods=("rtx",))
        self.assertEqual(
            [item.name for item in resolved.components],
            ["mpv", "portable-config", "nvidia-rtx-gpu", "rife-runtime"],
        )
        self.assertEqual(resolved.extras, ("rtx",))
        self.assertEqual(
            [item.id for item in resolved.choices],
            ["rtx-high", "rtx-medium", "rtx-low"],
        )
        self.assertEqual(
            resolved.choices[0].settings, {"interpolator": "rtx", "rtx_mode": "HIGH"}
        )

    def test_tensorrt_method_inherits_rife_and_adds_its_choices(self) -> None:
        manifest = load()
        rife = manifest.resolve_profile("core", WINDOWS, methods=("rife",))
        resolved = manifest.resolve_profile("core", WINDOWS, methods=("rife-tensorrt",))
        self.assertIn("rife-model-full", [item.name for item in resolved.components])
        self.assertEqual(
            [item.name for item in rife.components],
            [item.name for item in resolved.components],
        )
        self.assertEqual(rife.extras, ())
        self.assertEqual(resolved.extras, ("tensorrt",))
        self.assertEqual(
            [item.id for item in resolved.choices],
            [
                "rife-4.25-tensorrt",
                "rife-4.25-lite-tensorrt",
                "rife-4.25",
                "rife-4.25-lite",
            ],
        )
        with self.assertRaises(UnsupportedPlatformError):
            manifest.resolve_profile(
                "core", PlatformInfo("macos", "aarch64"), methods=("rife-tensorrt",)
            )

    def test_method_choices_follow_the_quality_order(self) -> None:
        ids = [item.id for item in load().method_choices(("rtx", "rife-tensorrt"))]
        self.assertEqual(
            ids,
            [
                "rtx-high",
                "rtx-medium",
                "rife-4.25-tensorrt",
                "rife-4.25-lite-tensorrt",
                "rife-4.25",
                "rife-4.25-lite",
                "rtx-low",
            ],
        )

    def test_methods_reject_unknown_names_and_platforms(self) -> None:
        with self.assertRaises(ManifestError):
            load().resolve_profile("core", WINDOWS, methods=("nope",))
        with self.assertRaises(UnsupportedPlatformError):
            load().resolve_profile("core", LINUX, methods=("rtx",))

    def test_only_tensorrt_is_not_preselected(self) -> None:
        self.assertEqual(
            [item.name for item in load().methods.values() if not item.preselect],
            ["rife-tensorrt"],
        )

    def test_available_methods_depend_on_the_platform(self) -> None:
        manifest = load()
        self.assertEqual(
            [item.name for item in manifest.available_methods(WINDOWS)],
            ["rtx", "rife", "rife-tensorrt"],
        )
        self.assertEqual(
            [item.name for item in manifest.available_methods(LINUX)],
            ["rife", "rife-tensorrt"],
        )

    def test_option_lists_are_in_the_manifest(self) -> None:
        options = load().interpolation
        assert options is not None
        self.assertEqual(
            [item.label for item in options.resolutions],
            ["720p", "1080p", "1440p", "4K", "8K"],
        )
        self.assertEqual(
            (options.resolutions[3].width, options.resolutions[3].height),
            (4096, 2160),
        )
        self.assertEqual(options.frame_rates, (24, 30, 48, 60, 120))
        self.assertEqual(options.multipliers, (2, 3, 4))
        self.assertEqual(options.fps_cutoffs, (30, 48, 60, 90, 120))
        self.assertEqual(options.default_fps_cutoff, 60)

    def test_model_paths_are_built_from_its_version_and_python(self) -> None:
        manifest = Manifest.load(ROOT / "manifest.toml")
        model = manifest.components["rife-model"]
        name = f"flownet_v{model.version}.pkl"
        windows = model.artifact_for(PlatformInfo("windows", "x86_64"))
        linux = model.artifact_for(PlatformInfo("linux", "x86_64"))
        self.assertTrue(windows.url.endswith(f"/{name}"))
        self.assertEqual((windows.url, windows.sha256), (linux.url, linux.sha256))
        self.assertEqual(
            windows.destination,
            f"runtime/venv/Lib/site-packages/vsrife/models/{name}",
        )
        python = ".".join(PYTHON_VERSION.split(".")[:2])
        self.assertEqual(
            linux.destination,
            f"runtime/venv/lib/python{python}/site-packages/vsrife/models/{name}",
        )

    def test_linux_uses_system_mpv_and_checks_vapoursynth_for_rife(self) -> None:
        manifest = Manifest.load(ROOT / "manifest.toml")
        linux = PlatformInfo("linux", "x86_64")
        core = manifest.resolve_profile("core", linux)
        self.assertEqual(
            [item.name for item in core.components], ["system-mpv", "portable-config"]
        )
        rife = manifest.resolve_profile("core", linux, methods=("rife",))
        check = manifest.components["system-mpv-vapoursynth"].system
        assert check is not None
        self.assertIn("system-mpv-vapoursynth", [c.name for c in rife.components])
        self.assertEqual(check.min_version, (0, 42, 0))
        self.assertEqual(check.check_contains, "vapoursynth")

    def test_macos_installs_core_only(self) -> None:
        manifest = Manifest.load(ROOT / "manifest.toml")
        mac = PlatformInfo("macos", "aarch64")
        core = manifest.resolve_profile("core", mac)
        self.assertEqual(
            [item.name for item in core.components], ["mpv", "portable-config"]
        )
        self.assertEqual(
            manifest.components["mpv"].artifact_for(mac).archive, "zip+tar.gz"
        )
        with self.assertRaises(UnsupportedPlatformError):
            manifest.resolve_profile("core", mac, methods=("rife",))

    def test_repository_profiles_reject_other_platforms(self) -> None:
        manifest = Manifest.load(ROOT / "manifest.toml")
        with self.assertRaises(UnsupportedPlatformError):
            manifest.resolve_profile("core", PlatformInfo("linux", "aarch64"))


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


class PlatformTests(unittest.TestCase):
    def test_common_windows_values_are_normalized(self) -> None:
        platform = PlatformInfo.detect(system="Windows", machine="AMD64")
        self.assertEqual(platform.key, "windows-x86_64")

    def test_unknown_architecture_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedPlatformError):
            PlatformInfo.detect(system="Windows", machine="mystery-cpu")


if __name__ == "__main__":
    unittest.main()
