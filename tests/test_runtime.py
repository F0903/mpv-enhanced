import os
import tempfile
import unittest
from pathlib import Path

from mpv_enhanced.install.runtime import CommandResult, UvRuntimeBuilder
from mpv_enhanced.manifest import UvRuntime
from mpv_enhanced.platforms import PlatformInfo


class UvRuntimeTests(unittest.TestCase):
    def test_runtime_is_app_local_locked_and_relocatable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            project = staging / "runtime" / "rife"
            project.mkdir(parents=True)
            (project / "uv.lock").write_text("version = 1", encoding="utf-8")
            calls: list[tuple[list[str], Path, dict[str, str]]] = []

            def runner(command, cwd, environment):
                calls.append((list(command), cwd, dict(environment)))
                if command[-1] == "get-vsscript":
                    return CommandResult(0, "C:/app/vsscript.dll\n", "")
                return CommandResult(0, "", "")

            builder = UvRuntimeBuilder(uv_executable="pinned-uv", runner=runner)
            runtime = UvRuntime(
                project="runtime/rife",
                environment="runtime/venv",
                python_version="3.12.13",
                extras=("tensorrt",),
            )

            result = builder.build(
                runtime,
                staging_root=staging,
                project_directory=project,
                platform=PlatformInfo("windows", "x86_64"),
                cache_root=root / "cache",
                offline=False,
            )

            self.assertEqual(result, "C:/app/vsscript.dll")
            self.assertEqual(calls[0][0][0], "pinned-uv")
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
            # Registering at build time would leave a stale staging entry behind.
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[2][0][-2:], ["vapoursynth", "get-vsscript"])

            final = root / "final"
            builder.register_vsscript(runtime, final, PlatformInfo("windows", "x86_64"))
            self.assertEqual(calls[3][0][-1], str(final / "VSScript.dll"))
            self.assertEqual(calls[3][1], final)

            builder.register_vsscript(runtime, final, PlatformInfo("linux", "x86_64"))
            self.assertEqual(calls[4][0][-2:], ["vapoursynth", "config"])

    def test_retarget_points_the_venv_at_the_promoted_python(self) -> None:
        runtime = UvRuntime(
            project="runtime/rife",
            environment="runtime/venv",
            python_version="3.12.13",
            extras=(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            staging, target = root / ".app.staging-1", root / "app"
            base = staging / "runtime" / "python" / "cpython" / "bin"
            environment = staging / "runtime" / "venv"
            (environment / "bin").mkdir(parents=True)
            base.mkdir(parents=True)
            (base / "python3.12").write_text("")
            (environment / "pyvenv.cfg").write_text(
                f"home = {base.parent}\nrelocatable = true\n", encoding="utf-8"
            )
            sysconfig = base.parent / "lib" / "python3.12" / "_sysconfigdata__linux.py"
            sysconfig.parent.mkdir(parents=True)
            sysconfig.write_text(f"build_time_vars = {{'LIBDIR': '{base.parent}/lib'}}")
            linux = PlatformInfo("linux", "x86_64")
            if os.name != "nt":
                (environment / "bin" / "python").symlink_to(base / "python3.12")
                (base.parent.parent / "cpython-3.12").symlink_to(base.parent)

            UvRuntimeBuilder().retarget(
                runtime=runtime,
                staging_root=staging,
                target_root=target,
                platform=linux,
            )

            config = (environment / "pyvenv.cfg").read_text(encoding="utf-8")
            promoted_python = target / "runtime" / "python" / "cpython"
            self.assertIn(f"home = {promoted_python}", config)
            self.assertIn(f"'LIBDIR': '{promoted_python}/lib'", sysconfig.read_text())
            if os.name != "nt":
                link = environment / "bin" / "python"
                self.assertFalse(Path(os.readlink(link)).is_absolute())
                staging.rename(target)
                self.assertTrue(
                    (target / "runtime" / "venv" / "bin" / "python").exists()
                )
                self.assertTrue(
                    (target / "runtime" / "python" / "cpython-3.12").exists()
                )


if __name__ == "__main__":
    unittest.main()
