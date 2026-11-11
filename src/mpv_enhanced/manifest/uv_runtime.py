"""The Python environment a ``uv-runtime`` component builds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UvRuntime:
    project: str
    environment: str
    python_version: str
    extras: tuple[str, ...]


_RUNTIME_ENVIRONMENT = "runtime/venv"


def _site_packages(system: str, python_version: str) -> str:
    if system == "windows":
        return f"{_RUNTIME_ENVIRONMENT}/Lib/site-packages"
    major_minor = ".".join(python_version.split(".")[:2])
    return f"{_RUNTIME_ENVIRONMENT}/lib/python{major_minor}/site-packages"
