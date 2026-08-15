"""Assembly of a locked, relocatable uv/VapourSynth runtime."""

from __future__ import annotations

import os
import shutil
import subprocess
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
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeSetupError(f"cannot run {command[0]!r}: {exc}") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


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
        source_root: Path,
        staging_root: Path,
        project_directory: Path,
        platform: PlatformInfo,
        cache_root: Path,
        offline: bool,
    ) -> str:
        source_lock = _inside(source_root, runtime.lockfile)
        if not source_lock.is_file():
            raise RuntimeSetupError(f"runtime lockfile does not exist: {source_lock}")
        source_project = _inside(source_root, runtime.project)
        try:
            lock_relative = source_lock.relative_to(source_project)
        except ValueError as exc:
            raise RuntimeSetupError(
                "the uv runtime lockfile must be inside its project directory"
            ) from exc
        staged_lock = project_directory / lock_relative
        if not staged_lock.is_file() or staged_lock.name != "uv.lock":
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
        self._checked(
            (str(interpreter), "-m", "vapoursynth", "config"),
            project_directory,
            command_environment,
            "configure VapourSynth",
        )
        result = self._checked(
            (str(interpreter), "-m", "vapoursynth", "get-vsscript"),
            project_directory,
            command_environment,
            "locate VapourSynth",
        )
        vsscript_path = result.stdout.strip()
        if not vsscript_path and self.runner is run_command:
            raise RuntimeSetupError("VapourSynth returned an empty VSSCRIPT_PATH")
        return vsscript_path

    def _find_uv(self, staging_root: Path, platform: PlatformInfo) -> str:
        if self.uv_executable:
            return self.uv_executable
        filename = "uv.exe" if platform.system == "windows" else "uv"
        bundled = staging_root / "runtime" / "uv" / filename
        if bundled.is_file():
            return str(bundled)
        configured = os.environ.get("MPV_ENHANCED_UV")
        if configured:
            return configured
        discovered = shutil.which("uv")
        if discovered:
            return discovered
        raise RuntimeSetupError(
            f"uv is unavailable; expected the bundled executable at {bundled}"
        )

    def write_launcher(
        self,
        *,
        staging_root: Path,
        runtime: UvRuntime,
        platform: PlatformInfo,
    ) -> Path:
        relative_environment = runtime.environment.replace("/", "\\")
        if platform.system == "windows":
            launcher = staging_root / "mpv-enhanced.cmd"
            launcher.write_text(
                "@echo off\n"
                "setlocal\n"
                'set "MPV_ENHANCED_UV=%~dp0runtime\\uv\\uv.exe"\n'
                'set "MPV_ENHANCED_7ZR=%~dp0tools\\7zr.exe"\n'
                'set "XDG_CONFIG_HOME=%~dp0runtime\\config"\n'
                f'"%~dp0{relative_environment}\\Scripts\\python.exe" '
                "-m vapoursynth config >nul || exit /b 1\n"
                f'for /f "usebackq delims=" %%I in (`"%~dp0{relative_environment}'
                '\\Scripts\\python.exe" -m vapoursynth get-vsscript`) do set "VSSCRIPT_PATH=%%I"\n'
                "if not defined VSSCRIPT_PATH exit /b 1\n"
                '"%~dp0mpv.exe" %*\n',
                encoding="utf-8",
                newline="\r\n",
            )
            return launcher

        launcher = staging_root / "mpv-enhanced"
        relative_environment = runtime.environment
        launcher.write_text(
            "#!/bin/sh\n"
            'SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1\n'
            'MPV_ENHANCED_UV="$SCRIPT_DIR/runtime/uv/uv"\n'
            "export MPV_ENHANCED_UV\n"
            'MPV_ENHANCED_7ZR="$SCRIPT_DIR/tools/7zr"\n'
            "export MPV_ENHANCED_7ZR\n"
            'XDG_CONFIG_HOME="$SCRIPT_DIR/runtime/config"\n'
            "export XDG_CONFIG_HOME\n"
            f'"$SCRIPT_DIR/{relative_environment}/bin/python" '
            "-m vapoursynth config >/dev/null || exit 1\n"
            f'VSSCRIPT_PATH=$("$SCRIPT_DIR/{relative_environment}/bin/python" '
            "-m vapoursynth get-vsscript) || exit 1\n"
            "export VSSCRIPT_PATH\n"
            'exec "$SCRIPT_DIR/mpv" "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        launcher.chmod(launcher.stat().st_mode | 0o111)
        return launcher

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
                result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
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
