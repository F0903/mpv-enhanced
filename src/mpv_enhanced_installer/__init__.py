"""Installer core for mpv-enhanced."""

from .errors import InstallerError
from .installer import Installer, InstallResult
from .manifest import Artifact, Component, Manifest, Profile, ResolvedProfile
from .platforms import PlatformInfo
from .receipt import InstallationReceipt

__all__ = [
    "Artifact",
    "Component",
    "InstallResult",
    "InstallationReceipt",
    "Installer",
    "InstallerError",
    "Manifest",
    "PlatformInfo",
    "Profile",
    "ResolvedProfile",
]

__version__ = "0.1.0"
