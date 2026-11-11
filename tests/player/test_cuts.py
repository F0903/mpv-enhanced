import sys
import unittest

import numpy as np

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "portable_config"))

from vs_script.cuts import is_cut, pair_score


def frame(seed: int, chroma: int) -> np.ndarray:
    """A textured 16-bit YUV 4:4:4 frame with the given chroma level."""
    rng = np.random.default_rng(seed)
    y = rng.integers(16 << 8, 235 << 8, (144, 256))
    u = np.full((144, 256), chroma << 8) + rng.integers(0, 4 << 8, (144, 256))
    return np.stack([y, u, np.full((144, 256), 128 << 8)]).astype(np.uint16)


class PairScoreTests(unittest.TestCase):
    def test_identical_frames_score_zero(self) -> None:
        a = frame(1, 128)
        self.assertEqual(pair_score(a, a), 0.0)

    def test_motion_that_keeps_the_colours_scores_zero(self) -> None:
        a = frame(1, 128)
        moved = np.roll(a, 40, axis=2)
        self.assertEqual(pair_score(a, moved), 0.0)

    def test_a_change_of_shot_scores_high(self) -> None:
        self.assertGreater(pair_score(frame(1, 110), frame(2, 150)), 0.004)


class IsCutTests(unittest.TestCase):
    def test_an_isolated_spike_is_a_cut(self) -> None:
        self.assertTrue(is_cut(0.02, before=0.001, after=0.004))

    def test_sustained_change_is_not_a_cut(self) -> None:
        self.assertFalse(is_cut(0.02, before=0.006, after=0.004))
        self.assertFalse(is_cut(0.02, before=0.001, after=0.006))

    def test_small_changes_are_not_cuts(self) -> None:
        self.assertFalse(is_cut(0.003, before=0.0, after=0.0))


if __name__ == "__main__":
    unittest.main()
