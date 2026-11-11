import unittest

from mpv_enhanced.errors import ManifestError, UnsupportedPlatformError
from mpv_enhanced.manifest import Manifest
from mpv_enhanced.platforms import PlatformInfo
from tests.support import LINUX, ROOT, WINDOWS

PYTHON_VERSION = (ROOT / ".python-version").read_text(encoding="utf-8").strip()


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


if __name__ == "__main__":
    unittest.main()
