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
        # No `vapoursynth config` here: on Windows it would register this staging
        # path in %APPDATA%; the launcher registers the final path on each start.
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
        # install.cmd/install.sh export their pinned uv here.
        uv = (
            self.uv_executable
            or os.environ.get("MPV_ENHANCED_UV")
            or shutil.which("uv")
        )
        if not uv:
            raise RuntimeSetupError(
                "uv is unavailable; run install.cmd or install.sh, "
                "or set MPV_ENHANCED_UV"
            )
        return uv

    def write_launcher(
        self,
        *,
        staging_root: Path,
        runtime: UvRuntime | None,
        platform: PlatformInfo,
    ) -> Path:
        """Write ``mpv-enhanced(.cmd)``; RIFE lines are added only with a runtime."""
        if platform.system == "windows":
            lines = ["@echo off", "setlocal"]
            if runtime is not None:
                environment = runtime.environment.replace("/", "\\")
                python = f'"%~dp0{environment}\\Scripts\\python.exe"'
                # VapourSynth on Windows always registers the runtime in
                # %APPDATA%\vapoursynth\vapoursynth.toml, keyed by DLL path.
                # Re-running config keeps that entry right if the install moves.
                lines += [
                    f"{python} -m vapoursynth config >nul || exit /b 1",
                    (
                        f'for /f "usebackq delims=" %%I in (`{python} -m vapoursynth '
                        'get-vsscript`) do set "VSSCRIPT_PATH=%%I"'
                    ),
                    "if not defined VSSCRIPT_PATH exit /b 1",
                ]
            lines.append('"%~dp0mpv.exe" %*')
            launcher = staging_root / "mpv-enhanced.cmd"
            launcher.write_text(
                "\n".join(lines) + "\n", encoding="utf-8", newline="\r\n"
            )
            return launcher

        lines = [
            "#!/bin/sh",
            'SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1',
        ]
        if runtime is not None:
            python = f'"$SCRIPT_DIR/{runtime.environment}/bin/python"'
            lines += [
                'XDG_CONFIG_HOME="$SCRIPT_DIR/runtime/config"',
                "export XDG_CONFIG_HOME",
                f"{python} -m vapoursynth config >/dev/null || exit 1",
                f"VSSCRIPT_PATH=$({python} -m vapoursynth get-vsscript) || exit 1",
                "export VSSCRIPT_PATH",
            ]
        # Linux uses the distribution mpv; macOS uses the downloaded app bundle.
        mpv = (
            '"$SCRIPT_DIR/mpv.app/Contents/MacOS/mpv"'
            if platform.system == "macos"
            else "mpv"
        )
        lines.append(f'exec {mpv} --config-dir="$SCRIPT_DIR/portable_config" "$@"')
        launcher = staging_root / "mpv-enhanced"
        launcher.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        launcher.chmod(launcher.stat().st_mode | 0o111)
        return launcher

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
