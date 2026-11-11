"""Checks that this machine has what a component needs: a program or a graphics card."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence

from ..errors import InstallerError
from ..manifest import (
    GpuRequirement,
    SystemProgram,
    parse_version,
)


def _run_output(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(command), check=False, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallerError(f"cannot run {command[0]!r}: {exc}") from exc
    return completed.stdout + completed.stderr


def check_system_program(
    program: SystemProgram,
    *,
    run: Callable[[Sequence[str]], str] = _run_output,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, str]:
    """Return the path and version of a required host program, or raise."""
    path = which(program.command)
    if path is None:
        raise InstallerError(
            f"{program.command!r} was not found on PATH; install it with your "
            "system package manager first"
        )
    version = parse_version(run((path, "--no-config", "--version")))
    required = ".".join(map(str, program.min_version))
    if version is None or version < program.min_version:
        found = ".".join(map(str, version)) if version else "an unknown version"
        raise InstallerError(
            f"{path} is {found}; this profile needs {program.command} {required} "
            "or newer"
        )
    if program.check_contains and program.check_contains not in run(
        (path, "--no-config", *program.check_args)
    ):
        raise InstallerError(
            f"{path} was built without {program.check_contains!r} support, which "
            "this profile needs"
        )
    return path, ".".join(map(str, version))


def check_gpu(
    gpu: GpuRequirement,
    *,
    run: Callable[[Sequence[str]], str] = _run_output,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Return the name of a graphics card meeting the requirement, or raise."""
    advice = "choose another profile"
    tool = which("nvidia-smi")
    if tool is None:
        raise InstallerError(
            f"this profile needs {gpu.requirement}, but no NVIDIA driver was "
            f"found; {advice}"
        )
    output = run((tool, "--query-gpu=name", "--format=csv,noheader"))
    names = [line.strip() for line in output.splitlines() if line.strip()]
    for name in names:
        if re.search(gpu.matches, name):
            return name
    found = ", ".join(names) or "no GPU"
    raise InstallerError(
        f"this profile needs {gpu.requirement}, but found {found}; {advice}"
    )
