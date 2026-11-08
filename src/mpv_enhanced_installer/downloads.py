"""Content-addressed, checksum-verifying artifact cache."""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from .errors import DownloadError, HashMismatchError, OfflineCacheMissError
from .manifest import Artifact
from .version import VERSION

Downloader = Callable[[str, Path], None]


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def download_url(url: str, destination: Path) -> None:
    """Download *url* to *destination* without interpreting its contents."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"mpv-enhanced-installer/{VERSION}"},
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            destination.open("xb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        raise DownloadError(f"failed to download {url}: {exc}") from exc


class ArtifactCache:
    """A content-addressed cache whose keys are pinned SHA-256 values."""

    def __init__(self, root: str | Path, downloader: Downloader = download_url) -> None:
        self.root = Path(root)
        self.downloader = downloader

    @property
    def objects_dir(self) -> Path:
        return self.root / "objects"

    def cached_path(self, artifact: Artifact) -> Path:
        return self.objects_dir / artifact.cache_key

    def fetch(self, artifact: Artifact, *, offline: bool = False) -> Path:
        cached = self.cached_path(artifact)
        if cached.is_file():
            if sha256_file(cached) == artifact.sha256:
                return cached
            try:
                cached.unlink(missing_ok=True)
            except OSError as exc:
                raise DownloadError(
                    f"cannot remove corrupt cache object {cached}: {exc}"
                ) from exc
        if offline:
            raise OfflineCacheMissError(
                f"artifact {artifact.sha256} is not cached while offline"
            )

        self.objects_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.objects_dir / f".{artifact.sha256}.{uuid.uuid4().hex}.part"
        try:
            self.downloader(artifact.url, temporary)
            if not temporary.is_file():
                raise DownloadError(
                    f"downloader returned without creating an artifact for {artifact.url}"
                )
            actual = sha256_file(temporary)
            if actual != artifact.sha256:
                raise HashMismatchError(
                    f"checksum mismatch for {artifact.url}: expected "
                    f"{artifact.sha256}, got {actual}"
                )
            # Concurrent installers may race here. Both temporary files have the
            # same verified bytes, so replacing this content-addressed path is safe.
            os.replace(temporary, cached)
            return cached
        except (DownloadError, OSError) as exc:
            if isinstance(exc, DownloadError):
                raise
            raise DownloadError(f"cannot cache {artifact.url}: {exc}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
