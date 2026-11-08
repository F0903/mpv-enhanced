"""RTX Video Frame Generation as an mpv VapourSynth filter."""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from threading import Lock

import numpy as np
import torch
import torch.nn.functional as F
import vapoursynth as vs
from nvvfx import ImageEncoding, VideoFrameGeneration

core = vs.core

# A cut is a large frame difference that its neighbours don't share. Fast motion
# is large too, but stays large, and NVIDIA's own detector mistakes it for a cut.
CUT_MINIMUM = 0.06
CUT_SPIKE = 2.5

# (Kr, Kb) by the matrix number mpv reports in _ColorSpace.
LUMA_WEIGHTS = {
    1: (0.2126, 0.0722),
    5: (0.299, 0.114),
    6: (0.299, 0.114),
    9: (0.2627, 0.0593),
    10: (0.2627, 0.0593),
}


def interpolate(
    source: vs.VideoNode,
    multiplier: Fraction,
    output_fps: Fraction,
    mode: str,
) -> vs.VideoNode:
    """Raise ``source`` to ``multiplier`` times its frame rate."""
    if source.format.id != vs.YUV420P10:
        source = core.resize.Point(source, format=vs.YUV420P10)

    device = torch.device("cuda", 0)
    effect = VideoFrameGeneration(
        mode=VideoFrameGeneration.Mode[mode],
        automatic_shot_change_detection_enabled=False,
        device=0,
        image_encoding=ImageEncoding.RGB10A2,
    )
    effect.input_width = source.width
    effect.input_height = source.height
    effect.load()

    run_lock = Lock()
    cache_lock = Lock()
    packed_frames: dict[int, torch.Tensor] = {}

    def upload(frame: vs.VideoFrame, plane: int) -> torch.Tensor:
        data = torch.from_numpy(np.array(frame[plane], copy=True))
        return data.to(device, non_blocking=True).to(torch.float32)

    def pack(frame: vs.VideoFrame) -> torch.Tensor:
        kr, kb = _luma_weights(frame.props, source.height)
        luma_black, luma_range, chroma_range = _levels(frame.props)
        y = (upload(frame, 0) - luma_black) / luma_range
        chroma = (torch.stack((upload(frame, 1), upload(frame, 2))) - 512.0) / chroma_range
        chroma = F.interpolate(
            chroma[None], size=y.shape, mode="bilinear", align_corners=False
        )[0]
        u, v = chroma[0], chroma[1]
        red = y + 2.0 * (1.0 - kr) * v
        blue = y + 2.0 * (1.0 - kb) * u
        green = (y - kr * red - kb * blue) / (1.0 - kr - kb)
        channels = [
            (channel.clamp(0.0, 1.0) * 1023.0).round().to(torch.int32)
            for channel in (red, green, blue)
        ]
        packed = channels[0] | (channels[1] << 10) | (channels[2] << 20) | -1073741824
        return packed.view(torch.uint32).contiguous()

    def packed_source(index: int, frame: vs.VideoFrame) -> torch.Tensor:
        # Every source frame is in two neighbouring pairs, so upload it only once.
        with cache_lock:
            cached = packed_frames.get(index)
        if cached is not None:
            return cached
        packed = pack(frame)
        with cache_lock:
            packed_frames[index] = packed
            for stale in [key for key in packed_frames if key < index - 4]:
                del packed_frames[stale]
        return packed

    def write(packed: torch.Tensor, output: vs.VideoFrame) -> None:
        kr, kb = _luma_weights(output.props, source.height)
        luma_black, luma_range, chroma_range = _levels(output.props)
        values = packed.view(torch.int32)
        red, green, blue = (
            values.bitwise_right_shift(shift).bitwise_and(0x3FF).to(torch.float32)
            / 1023.0
            for shift in (0, 10, 20)
        )
        y = kr * red + (1.0 - kr - kb) * green + kb * blue
        chroma = torch.stack(
            ((blue - y) / (2.0 * (1.0 - kb)), (red - y) / (2.0 * (1.0 - kr)))
        )
        chroma = F.avg_pool2d(chroma[None], 2)[0] * chroma_range + 512.0
        planes = (y * luma_range + luma_black, chroma[0], chroma[1])
        for plane, data in enumerate(planes):
            array = data.round().clamp(0.0, 1023.0).to(torch.uint16).cpu().numpy()
            np.copyto(np.asarray(output[plane]), array)

    @torch.inference_mode()
    def generate(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
        position = n * multiplier.denominator
        timestep = Fraction(position % multiplier.numerator, multiplier.numerator)
        if timestep == 0:
            return f[0]  # Source frames pass through untouched.
        index = position // multiplier.numerator
        difference = f[2].props["PlaneStatsDiff"]
        neighbours = max(f[3].props["PlaneStatsDiff"], f[4].props["PlaneStatsDiff"])
        is_cut = difference > CUT_MINIMUM and difference > CUT_SPIKE * neighbours
        previous = packed_source(index, f[0])
        current = packed_source(index + 1, f[1])
        with run_lock:
            result = effect.run_at_timestep(
                previous, current, float(timestep), shot_change=is_cut
            )
            # The library reuses its output buffer on the next run.
            generated = torch.from_dlpack(result.image).clone()
        output = f[0].copy()
        write(generated, output)
        return output

    def expanded(clip: vs.VideoNode) -> vs.VideoNode:
        repeated = core.std.Interleave([clip] * multiplier.numerator)
        return repeated[:: multiplier.denominator]

    def following(clip: vs.VideoNode) -> vs.VideoNode:
        return clip.std.DuplicateFrames(clip.num_frames - 1)[1:]

    luma = core.resize.Bilinear(source, 480, 270, format=vs.GRAY8)
    stats = core.std.PlaneStats(luma, following(luma))
    clips = [
        expanded(source),
        expanded(following(source)),
        expanded(stats),
        expanded(stats.std.DuplicateFrames(0)),
        expanded(following(stats)),
    ]
    result = clips[0].std.FrameEval(
        lambda n: clips[0].std.ModifyFrame(clips, generate), clip_src=clips
    )
    return core.std.AssumeFPS(
        result, fpsnum=output_fps.numerator, fpsden=output_fps.denominator
    )


def _luma_weights(props: Mapping, height: int) -> tuple[float, float]:
    default = 1 if height >= 720 else 6
    return LUMA_WEIGHTS.get(props.get("_ColorSpace", default), LUMA_WEIGHTS[default])


def _levels(props: Mapping) -> tuple[float, float, float]:
    """Return luma black, luma range and chroma range for 10-bit video."""
    # mpv sets _ColorRange to 1 for full range and 0 for limited.
    if props.get("_ColorRange", 0) == 1:
        return 0.0, 1023.0, 1023.0
    return 64.0, 876.0, 896.0
