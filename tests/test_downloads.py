import hashlib
import tempfile
import unittest
from pathlib import Path

from mpv_enhanced_installer.downloads import ArtifactCache
from mpv_enhanced_installer.errors import (
    HashMismatchError,
    OfflineCacheMissError,
    SizeMismatchError,
)
from mpv_enhanced_installer.manifest import Artifact


def artifact_for(content: bytes) -> Artifact:
    return Artifact(
        platform="windows-x86_64",
        url="https://example.invalid/payload.bin",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        archive="none",
        destination="payload.bin",
    )


class ArtifactCacheTests(unittest.TestCase):
    def test_download_is_verified_and_reused(self) -> None:
        payload = b"locked payload"
        calls: list[str] = []

        def downloader(url: str, destination: Path) -> None:
            calls.append(url)
            destination.write_bytes(payload)

        with tempfile.TemporaryDirectory() as temporary:
            cache = ArtifactCache(Path(temporary), downloader)
            artifact = artifact_for(payload)
            first = cache.fetch(artifact)
            second = cache.fetch(artifact, offline=True)

            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), payload)
            self.assertEqual(calls, [artifact.url])

    def test_bad_download_is_not_cached(self) -> None:
        expected = artifact_for(b"expected")

        def downloader(_url: str, destination: Path) -> None:
            destination.write_bytes(b"tampered")

        with tempfile.TemporaryDirectory() as temporary:
            cache = ArtifactCache(Path(temporary), downloader)
            with self.assertRaises(HashMismatchError):
                cache.fetch(expected)
            self.assertFalse(cache.cached_path(expected).exists())
            self.assertEqual(list(cache.objects_dir.glob("*.part")), [])

    def test_offline_cache_miss_never_calls_downloader(self) -> None:
        called = False

        def downloader(_url: str, _destination: Path) -> None:
            nonlocal called
            called = True

        with tempfile.TemporaryDirectory() as temporary:
            cache = ArtifactCache(Path(temporary), downloader)
            with self.assertRaises(OfflineCacheMissError):
                cache.fetch(artifact_for(b"missing"), offline=True)
            self.assertFalse(called)

    def test_size_mismatch_is_rejected_before_hashing_into_cache(self) -> None:
        artifact = artifact_for(b"expected-size")

        def downloader(_url: str, destination: Path) -> None:
            destination.write_bytes(b"short")

        with tempfile.TemporaryDirectory() as temporary:
            cache = ArtifactCache(Path(temporary), downloader)
            with self.assertRaisesRegex(SizeMismatchError, "size mismatch"):
                cache.fetch(artifact)
            self.assertFalse(cache.cached_path(artifact).exists())

    def test_wrong_sized_existing_object_is_rejected_while_offline(self) -> None:
        artifact = artifact_for(b"expected-size")
        with tempfile.TemporaryDirectory() as temporary:
            cache = ArtifactCache(Path(temporary))
            cache.objects_dir.mkdir(parents=True)
            cache.cached_path(artifact).write_bytes(b"wrong")

            with self.assertRaisesRegex(SizeMismatchError, "cached artifact size"):
                cache.fetch(artifact, offline=True)
            self.assertFalse(cache.cached_path(artifact).exists())


if __name__ == "__main__":
    unittest.main()
