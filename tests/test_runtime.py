import tempfile
import unittest
from pathlib import Path

from mpv_enhanced_installer.manifest import UvRuntime
from mpv_enhanced_installer.platforms import PlatformInfo
from mpv_enhanced_installer.runtime import CommandResult, UvRuntimeBuilder


class UvRuntimeTests(unittest.TestCase):
    def test_runtime_is_app_local_locked_and_relocatable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_project = source_root / "runtime" / "rife"
            source_project.mkdir(parents=True)
            (source_project / "uv.lock").write_text("version = 1", encoding="utf-8")
            staging = root / "staging"
            project = staging / "runtime" / "rife"
            project.mkdir(parents=True)
            (project / "uv.lock").write_text("version = 1", encoding="utf-8")
            bundled_uv = staging / "runtime" / "uv" / "uv.exe"
            bundled_uv.parent.mkdir(parents=True)
            bundled_uv.write_bytes(b"fake")
            calls: list[tuple[list[str], Path, dict[str, str]]] = []

            def runner(command, cwd, environment):
                calls.append((list(command), cwd, dict(environment)))
                if command[-1] == "get-vsscript":
                    return CommandResult(0, "C:/app/vsscript.dll\n", "")
                return CommandResult(0, "", "")

            builder = UvRuntimeBuilder(runner=runner)
            runtime = UvRuntime(
                project="runtime/rife",
                lockfile="runtime/rife/uv.lock",
                environment="runtime/venv",
                python_version="3.12.13",
                extras=("tensorrt",),
            )

            result = builder.build(
                runtime,
                source_root=source_root,
                staging_root=staging,
                project_directory=project,
                platform=PlatformInfo("windows", "x86_64"),
                cache_root=root / "cache",
                offline=False,
            )

            self.assertEqual(result, "C:/app/vsscript.dll")
            self.assertEqual(calls[0][0][0], str(bundled_uv))
            self.assertIn("--relocatable", calls[0][0])
            self.assertNotIn("--offline", calls[0][0])
            self.assertIn("--locked", calls[1][0])
            self.assertIn("--extra", calls[1][0])
            self.assertIn("tensorrt", calls[1][0])
            environment = calls[0][2]
            self.assertEqual(
                environment["UV_PROJECT_ENVIRONMENT"], str(staging / "runtime" / "venv")
            )
            self.assertEqual(
                environment["UV_PYTHON_INSTALL_DIR"],
                str(staging / "runtime" / "python"),
            )
            self.assertEqual(environment["UV_PYTHON_NO_REGISTRY"], "1")
            self.assertEqual(environment["UV_CACHE_DIR"], str(root / "cache" / "uv"))
            self.assertEqual(
                environment["XDG_CONFIG_HOME"], str(staging / "runtime" / "config")
            )
            self.assertEqual(calls[2][0][-2:], ["vapoursynth", "config"])
            self.assertEqual(calls[3][0][-2:], ["vapoursynth", "get-vsscript"])

            launcher = builder.write_launcher(
                staging_root=staging,
                runtime=runtime,
                platform=PlatformInfo("windows", "x86_64"),
            )
            contents = launcher.read_text(encoding="utf-8")
            self.assertIn("MPV_ENHANCED_UV=%~dp0runtime\\uv\\uv.exe", contents)
            self.assertIn("MPV_ENHANCED_7ZR=%~dp0tools\\7zr.exe", contents)
            self.assertIn("XDG_CONFIG_HOME=%~dp0runtime\\config", contents)
            self.assertIn("-m vapoursynth config", contents)
            self.assertIn("-m vapoursynth get-vsscript", contents)
            self.assertIn("VSSCRIPT_PATH", contents)


if __name__ == "__main__":
    unittest.main()
