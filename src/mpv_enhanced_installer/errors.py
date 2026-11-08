"""Typed errors raised by the installer."""


class InstallerError(Exception):
    """Base class for expected, user-facing installer failures."""


class ManifestError(InstallerError):
    """The manifest is invalid."""


class UnsupportedPlatformError(InstallerError):
    """A profile or component has no artifact for this platform."""


class DownloadError(InstallerError):
    """An artifact could not be downloaded or read from cache."""


class OfflineCacheMissError(DownloadError):
    """A required artifact is absent while offline mode is active."""


class OfflineRuntimeUnsupportedError(InstallerError):
    """Offline assembly was requested for a managed runtime profile."""


class HashMismatchError(DownloadError):
    """An artifact does not have the checksum recorded in the manifest."""


class UnsafeArchiveError(InstallerError):
    """An archive contains an unsafe or ambiguous path."""


class PromotionError(InstallerError):
    """A staged installation could not be promoted or rolled back."""


class RuntimeSetupError(InstallerError):
    """A managed uv runtime could not be assembled or validated."""
