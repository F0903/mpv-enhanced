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

from vs_script.cuts import following, mark_cuts

core = vs.core

# (Kr, Kb) by the matrix number mpv reports in _ColorSpace.
LUMA_WEIGHTS = {
    1: (0.2126, 0.0722),
    5: (0.299, 0.114),
    6: (0.299, 0.114),
    9: (0.2627, 0.0593),
    10: (0.2627, 0.0593),
}

# Room either side of 0..1, so colours just outside RGB (grain below black,
# saturated edges) survive the trip to RGB and back.
HEADROOM = 0.05


def to_rgb(
    y_code: torch.Tensor, chroma_code: torch.Tensor, props: Mapping
) -> torch.Tensor:
    """Pack 10-bit Y and half-size U/V planes into the RGB10A2 the model reads."""
    kr, kb = _luma_weights(props, y_code.shape[0])
    luma_black, luma_range, chroma_range = _levels(props)
    y = (y_code - luma_black) / luma_range
    # Repeating each chroma sample makes the averaging in to_yuv its exact inverse.
    chroma = (chroma_code - 512.0) / chroma_range
    chroma = F.interpolate(chroma[None], size=y.shape, mode="nearest")[0]
    u, v = chroma[0], chroma[1]
    red = y + 2.0 * (1.0 - kr) * v
    blue = y + 2.0 * (1.0 - kb) * u
    green = (y - kr * red - kb * blue) / (1.0 - kr - kb)
    channels = [
        ((channel + HEADROOM) / (1.0 + 2.0 * HEADROOM))
        .clamp(0.0, 1.0)
        .mul(1023.0)
        .round()
        .to(torch.int32)
        for channel in (red, green, blue)
    ]
    packed = channels[0] | (channels[1] << 10) | (channels[2] << 20) | -1073741824
    return packed.view(torch.uint32).contiguous()


def to_yuv(packed: torch.Tensor, props: Mapping) -> list[torch.Tensor]:
    """The 10-bit Y, U and V planes of an RGB10A2 frame from to_rgb or the model."""
    kr, kb = _luma_weights(props, packed.shape[0])
    luma_black, luma_range, chroma_range = _levels(props)
    values = packed.view(torch.int32)
    red, green, blue = (
        values.bitwise_right_shift(shift).bitwise_and(0x3FF).to(torch.float32)
        / 1023.0
        * (1.0 + 2.0 * HEADROOM)
        - HEADROOM
        for shift in (0, 10, 20)
    )
    y = kr * red + (1.0 - kr - kb) * green + kb * blue
    chroma = torch.stack(
        ((blue - y) / (2.0 * (1.0 - kb)), (red - y) / (2.0 * (1.0 - kr)))
    )
    chroma = F.avg_pool2d(chroma[None], 2)[0] * chroma_range + 512.0
    planes = (y * luma_range + luma_black, chroma[0], chroma[1])
    return [data.round().clamp(0.0, 1023.0) for data in planes]


def interpolate(
    source: vs.VideoNode,
    multiplier: Fraction,
    output_fps: Fraction,
    mode: str,
) -> vs.VideoNode:
    """Raise ``source`` to ``multiplier`` times its frame rate."""
    if source.format.id != vs.YUV420P10:
        # Bicubic averages chroma it subsamples; RGB sources need a matrix to convert.
        matrix = {"matrix_s": "709"} if source.format.color_family == vs.RGB else {}
        source = core.resize.Bicubic(
            source, format=vs.YUV420P10, dither_type="error_diffusion", **matrix
        )
    source = mark_cuts(source)

    device = torch.device("cuda", 0)
    effect = VideoFrameGeneration(
        mode=VideoFrameGeneration.Mode[mode],
        # NVIDIA's own detector mistakes fast motion for cuts.
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
        chroma = torch.stack((upload(frame, 1), upload(frame, 2)))
        return to_rgb(upload(frame, 0), chroma, frame.props)

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
        for plane, data in enumerate(to_yuv(packed, output.props)):
            np.copyto(np.asarray(output[plane]), data.to(torch.uint16).cpu().numpy())

    @torch.inference_mode()
    def generate(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
        position = n * multiplier.denominator
        timestep = Fraction(position % multiplier.numerator, multiplier.numerator)
        if timestep == 0:
            return f[0]  # Source frames pass through untouched.
        if f[0].props["_SceneChangeNext"]:
            # At a cut, show the nearer source frame as it is, not two shots blended.
            return f[0] if timestep < Fraction(1, 2) else f[1]
        index = position // multiplier.numerator
        previous = packed_source(index, f[0])
        current = packed_source(index + 1, f[1])
        with run_lock:
            result = effect.run_at_timestep(previous, current, float(timestep))
            # The library reuses its output buffer on the next run.
            generated = torch.from_dlpack(result.image).clone()
        output = f[0].copy()
        write(generated, output)
        return output

    def expanded(clip: vs.VideoNode) -> vs.VideoNode:
        repeated = core.std.Interleave([clip] * multiplier.numerator)
        return repeated[:: multiplier.denominator]

    clips = [expanded(source), expanded(following(source))]
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
