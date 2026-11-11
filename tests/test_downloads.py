import hashlib
import tempfile
import unittest
from pathlib import Path

from mpv_enhanced.downloads import ArtifactCache, progress_line
from mpv_enhanced.errors import HashMismatchError, OfflineCacheMissError
from mpv_enhanced.manifest import Artifact


def artifact_for(content: bytes) -> Artifact:
    return Artifact(
        url="https://example.invalid/payload.bin",
        sha256=hashlib.sha256(content).hexdigest(),
        archive="none",
        destination="payload.bin",
    )


class ProgressLineTests(unittest.TestCase):
    def test_shows_a_bar_when_the_size_is_known(self) -> None:
        line = progress_line("mpv.zip", 50_000_000, 100_000_000)
        self.assertIn(f"[{'#' * 12}{'-' * 12}]  50%", line)
        self.assertTrue(line.endswith("100.0 MB"))

    def test_finished_download_fills_the_bar_and_stays_within_80_columns(self) -> None:
        line = progress_line("a-very-long-file-name-" * 4, 2_000_000_000, 2_000_000_000)
        self.assertIn(f"[{'#' * 24}] 100%", line)
        self.assertLessEqual(len(line), 80)

    def test_shows_only_the_amount_when_the_size_is_unknown(self) -> None:
        self.assertTrue(progress_line("mpv.zip", 1_500_000, None).endswith("1.5 MB"))


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

    def test_corrupt_cached_object_is_rejected_while_offline(self) -> None:
        artifact = artifact_for(b"expected")
        with tempfile.TemporaryDirectory() as temporary:
            cache = ArtifactCache(Path(temporary))
            cache.objects_dir.mkdir(parents=True)
            cache.cached_path(artifact).write_bytes(b"wrong")

            with self.assertRaises(OfflineCacheMissError):
                cache.fetch(artifact, offline=True)
            self.assertFalse(cache.cached_path(artifact).exists())


if __name__ == "__main__":
    unittest.main()
