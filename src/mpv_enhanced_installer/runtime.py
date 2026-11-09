"""Assembly of a locked, relocatable uv/VapourSynth runtime."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import RuntimeSetupError
from .manifest import UvRuntime
from .platforms import PlatformInfo


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[Sequence[str], Path, Mapping[str, str]], CommandResult]
VSSCRIPT_DLL = "VSScript.dll"


def run_command(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(environment),
            check=False,
            stdout=subprocess.PIPE,
            # On a terminal, uv draws its own progress bars on stderr.
            stderr=None if sys.stderr.isatty() else subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise RuntimeSetupError(f"cannot run {command[0]!r}: {exc}") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr or "")


class UvRuntimeBuilder:
    """Create a locked app-local Python environment without global changes."""

    def __init__(
        self,
        *,
        uv_executable: str | None = None,
        runner: CommandRunner = run_command,
    ) -> None:
        self.uv_executable = uv_executable
        self.runner = runner

    def build(
        self,
        runtime: UvRuntime,
        *,
        staging_root: Path,
        project_directory: Path,
        platform: PlatformInfo,
        cache_root: Path,
        offline: bool,
    ) -> str:
        staged_lock = project_directory / "uv.lock"
        if not staged_lock.is_file():
            raise RuntimeSetupError(
                f"the staged uv project has no uv.lock at {staged_lock}"
            )

        environment_directory = _inside(staging_root, runtime.environment)
        command_environment = dict(os.environ)
        command_environment["UV_PROJECT_ENVIRONMENT"] = str(environment_directory)
        command_environment["UV_PYTHON_INSTALL_DIR"] = str(
            staging_root / "runtime" / "python"
        )
        command_environment["UV_PYTHON_NO_REGISTRY"] = "1"
        command_environment["UV_CACHE_DIR"] = str(cache_root / "uv")
        command_environment["XDG_CONFIG_HOME"] = str(
            staging_root / "runtime" / "config"
        )
        uv_executable = self._find_uv(staging_root, platform)

        create = [
            uv_executable,
            "venv",
            str(environment_directory),
            "--python",
            runtime.python_version,
            "--relocatable",
        ]
        if offline:
            create.append("--offline")
        self._checked(create, project_directory, command_environment, "create runtime")

        sync = [
            uv_executable,
            "sync",
            "--project",
            str(project_directory),
            "--locked",
            "--no-dev",
        ]
        for extra in runtime.extras:
            sync.extend(("--extra", extra))
        if offline:
            sync.append("--offline")
        self._checked(sync, project_directory, command_environment, "sync runtime")

        interpreter = _venv_python(environment_directory, platform)
        if self.runner is run_command and not interpreter.is_file():
            raise RuntimeSetupError(
                f"uv completed without creating the runtime interpreter: {interpreter}"
            )
        # No `vapoursynth config` here: on Windows it would register this staging
        # path in %APPDATA%; register_vsscript records the final path instead.
        result = self._checked(
            (str(interpreter), "-m", "vapoursynth", "get-vsscript"),
            project_directory,
            command_environment,
            "locate VapourSynth",
        )
        vsscript_path = result.stdout.strip()
        if not vsscript_path and self.runner is run_command:
            raise RuntimeSetupError("VapourSynth returned an empty VSSCRIPT_PATH")
        if platform.system == "windows" and self.runner is run_command:
            # mpv loads a VSScript.dll found beside mpv.exe, so it needs no launcher.
            shutil.copy2(vsscript_path, staging_root / VSSCRIPT_DLL)
        return vsscript_path

    def register_vsscript(
        self, runtime: UvRuntime, target_root: Path, platform: PlatformInfo
    ) -> None:
        """Record which Python mpv's VSScript library should load."""
        python = _venv_python(_inside(target_root, runtime.environment), platform)
        command = (str(python), "-m", "vapoursynth", "config")
        if platform.system == "windows":
            # VapourSynth's own writer keys its entry by get_vsscript(), and uv.lock
            # pins the version whose private helpers this relies on.
            script = (
                "import sys, vapoursynth._utils as u; "
                "u.get_vsscript = lambda: sys.argv[1]; u.vapoursynth_config()"
            )
            command = (str(python), "-c", script, str(target_root / VSSCRIPT_DLL))
        result = self._checked(command, target_root, os.environ, "register VapourSynth")
        # vapoursynth_config reports failure in its output and still exits 0.
        if self.runner is run_command and "successfully" not in result.stdout:
            raise RuntimeSetupError(
                f"could not register VapourSynth: {result.stdout.strip()}"
            )

    def _find_uv(self, staging_root: Path, platform: PlatformInfo) -> str:
        # `uv run` exports its own path as UV, so this is the pinned bootstrap uv.
        uv = self.uv_executable or os.environ.get("UV") or shutil.which("uv")
        if not uv:
            raise RuntimeSetupError("uv is unavailable; run mpve.cmd or mpve.sh")
        return uv

    def retarget(
        self,
        *,
        runtime: UvRuntime,
        staging_root: Path,
        target_root: Path,
        platform: PlatformInfo,
    ) -> None:
        """Rewrite absolute staging paths to their post-promotion location.

        ``--relocatable`` does not cover ``pyvenv.cfg``'s ``home``. uv also bakes
        the install path into the base Python's sysconfig, which VapourSynth
        reads on Linux to find libpython. On POSIX some symlinks are absolute.
        """
        environment = _inside(staging_root, runtime.environment)
        config = environment / "pyvenv.cfg"
        if not config.is_file():
            if self.runner is run_command:
                raise RuntimeSetupError(f"the runtime has no pyvenv.cfg at {config}")
            return
        staged = str(staging_root).encode("utf-8")
        final = str(target_root).encode("utf-8")
        if staged not in config.read_bytes():
            raise RuntimeSetupError(f"cannot relocate runtime; unexpected {config}")
        python_root = staging_root / "runtime" / "python"
        for path in (config, *python_root.glob("*/lib/python3*/_sysconfigdata_*.py")):
            path.write_bytes(path.read_bytes().replace(staged, final))
        if platform.system == "windows":
            return
        links = (*(environment / "bin").iterdir(), *python_root.iterdir())
        for link in links:
            if link.is_symlink():
                destination = Path(os.readlink(link))
                if destination.is_absolute() and destination.is_relative_to(
                    staging_root
                ):
                    link.unlink()
                    link.symlink_to(os.path.relpath(destination, link.parent))

    def _checked(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        action: str,
    ) -> CommandResult:
        result = self.runner(command, cwd, environment)
        if result.returncode:
            detail = (
                result.stderr.strip() or result.stdout.strip() or "no captured output"
            )
            raise RuntimeSetupError(
                f"could not {action} (exit {result.returncode}): {detail}"
            )
        return result


def _inside(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*relative.split("/")).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeSetupError(f"runtime path escapes its root: {relative!r}") from exc
    return candidate


def _venv_python(environment: Path, platform: PlatformInfo) -> Path:
    if platform.system == "windows":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"
