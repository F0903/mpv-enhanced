"""The Python environment a ``uv-runtime`` component builds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UvRuntime:
    project: str
    environment: str
    python_version: str
    extras: tuple[str, ...]


RUNTIME_ENVIRONMENT = "runtime/venv"


def site_packages(system: str, python_version: str) -> str:
    if system == "windows":
        return f"{RUNTIME_ENVIRONMENT}/Lib/site-packages"
    major_minor = ".".join(python_version.split(".")[:2])
    return f"{RUNTIME_ENVIRONMENT}/lib/python{major_minor}/site-packages"
