"""Finds cuts between shots, so neither method blends two shots together.

A cut changes both the pixels and the colours in them. Motion changes the
pixels but mostly keeps the colours, so the score multiplies the two.
"""

from __future__ import annotations

import numpy as np

# Tuned on hand-labelled cuts in a live-action film and an animated trailer.
SCORE_MINIMUM = 0.004
SPIKE = 4.0
SIZE = (256, 144)
BINS = 64
# Chroma rarely strays further than this from neutral, so the bins cover only this span.
CHROMA_SPAN = 0.25
_FULL = 65535.0


def _histograms(frame: np.ndarray) -> np.ndarray:
    scaled = frame / _FULL
    luma = (scaled[0] - 16 / 255) / (219 / 255)
    chroma = (scaled[1:] - (0.5 - CHROMA_SPAN)) / (2 * CHROMA_SPAN)
    counts = [
        np.bincount(np.clip((plane * BINS).astype(np.int64), 0, BINS - 1).ravel(), minlength=BINS)
        for plane in (luma, *chroma)
    ]
    return np.stack(counts) / frame[0].size


def pair_score(a: np.ndarray, b: np.ndarray) -> float:
    """How strongly two 16-bit YUV 4:4:4 frames look like different shots."""
    luma_change = np.abs(a[0].astype(np.float32) - b[0]).mean() / _FULL
    colour_change = np.abs(_histograms(a) - _histograms(b)).sum() / 6
    return float(luma_change * colour_change)


def is_cut(score: float, before: float, after: float) -> bool:
    """A cut is a large change that the neighbouring frame pairs don't share."""
    return score > SCORE_MINIMUM and score > SPIKE * max(before, after)


def following(clip):
    """Frame n is frame n + 1 of ``clip``; the last frame repeats."""
    return clip.std.DuplicateFrames(clip.num_frames - 1)[1:]


def mark_cuts(clip):
    """Set _SceneChangeNext on every frame that a cut follows."""
    import vapoursynth as vs  # Imported here so the scoring is testable without it.

    extra = {"matrix_s": "709"} if clip.format.color_family == vs.RGB else {}
    small = vs.core.resize.Bilinear(clip, *SIZE, format=vs.YUV444P16, **extra)

    def planes(frame: vs.VideoFrame) -> np.ndarray:
        return np.stack([np.asarray(frame[plane]) for plane in range(3)])

    def score(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
        out = f[0].copy()
        out.props["CutScore"] = pair_score(planes(f[0]), planes(f[1]))
        return out

    scores = small.std.ModifyFrame([small, following(small)], score)

    def decide(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
        current, before, after = (frame.props["CutScore"] for frame in f)
        out = f[0].copy()
        out.props["_SceneChangeNext"] = int(is_cut(current, before, after))
        return out

    previous = scores.std.DuplicateFrames(0)
    decided = scores.std.ModifyFrame([scores, previous, following(scores)], decide)
    return clip.std.CopyFrameProps(decided, "_SceneChangeNext")
