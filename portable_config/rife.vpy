import sys
import os
from fractions import Fraction
import vapoursynth as vs
from vsrife import rife

# Add the portable_config directory to Python path to allow importing from vs_script
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import rife_config as config
from vs_script.expensive_clip_mode import ExpensiveClipMode


# Variables provided by MPV VapourSynth filter at runtime
# We can't declare these without overwriting them, so we'll use type ignore comments
# video_in: vs.VideoNode (provided by MPV)
# container_fps: float (provided by MPV)

core = vs.core

def print_log(*values: object):
    if not config.log:
        return
    print(*values, file=config.log)
    config.log.flush()  # Force flush to ensure output is written immediately

def mark_scene_changes(clip: vs.VideoNode, threshold: float) -> vs.VideoNode:
    """Set _SceneChangeNext like misc.SCDetect, using only core filters.

    vsrife's own sc_threshold path needs the misc plugin, which VapourSynth no
    longer ships; with the prop already set, rife() is called with sc_threshold=None.
    """
    gray = clip.resize.Bicubic(format=vs.GRAY8, matrix_s="709")
    stats = core.std.PlaneStats(gray, gray[1:] + gray[-1])

    def set_next(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        fout = f.copy()
        fout.props["_SceneChangeNext"] = int(f.props["PlaneStatsDiff"] > threshold)
        return fout

    marked = core.std.ModifyFrame(stats, stats, set_next)
    return clip.std.CopyFrameProps(marked, "_SceneChangeNext")


def interpolate(clip: vs.VideoNode) -> vs.VideoNode:
    os.makedirs(os.path.join(script_dir, "cache", "tensorrt"), exist_ok=True)
    original_size = (clip.width, clip.height)
    original_format = clip.format

    print_log(f"Original size: {original_size}")
    print_log(f"Original format: {original_format}")

    if (
        clip.width > config.expensive_res_threshold[0]
        and clip.height > config.expensive_res_threshold[1]
    ):
        # Clip is considered "expensive"
        match config.expensive_clip_handling:
            case ExpensiveClipMode.DOWNSCALE:
                print_log(f"Clip is too expensive. Downscaling to {config.downscale_res}...")
                clip = core.resize.Lanczos(
                    clip=clip,
                    width=config.downscale_res[0],
                    height=config.downscale_res[1],
                )
            case ExpensiveClipMode.SKIP:
                print_log("Clip is too expensive. Skipping interpolation...")
                return clip
            case ExpensiveClipMode.NORMAL:
                print_log("Clip is considered expensive, but ExpensiveClipHandling is set to NORMAL. Interpolating normally...")

    container_fps_frac = Fraction(container_fps).limit_denominator(100)  # type: ignore[name-defined]
    print_log(
        f"Container FPS fraction: {container_fps_frac.numerator}/{container_fps_frac.denominator}={float(container_fps_frac)}"
    )

    target_fps_frac = config.target_mode.get_fps_fraction(
        container_fps_frac
    ).limit_denominator(100)
    [target_num, target_den] = target_fps_frac.as_integer_ratio()
    print_log(
        f"Target FPS fraction: {target_fps_frac.numerator}/{target_fps_frac.denominator}={float(target_fps_frac)}"
    )

    dest_fps_frac = container_fps_frac * target_fps_frac
    dest_fps_frac = dest_fps_frac.limit_denominator(100)
    [dest_num, dest_den] = dest_fps_frac.as_integer_ratio()
    print_log(
        f"Destination FPS fraction: {dest_fps_frac.numerator}/{dest_fps_frac.denominator}={float(dest_fps_frac)}"
    )

    print_log(
        f"Using settings:\nscale: {config.scale},ensemble={config.ensemble},gpu_index={config.gpu_index},gpu_tensorrt={config.tensorrt}"
    )

    # Change format for RIFE
    clip = core.resize.Lanczos(
        clip=clip,
        format=config.gpu_format,
        matrix_in=config.output_colorspace,
        transfer_in=config.output_transfer,
        primaries_in=config.output_primaries,
    )
    if config.sc:
        clip = mark_scene_changes(clip, config.sc_threshold)
    clip = rife(
        clip=clip,
        model=config.rife_model,
        factor_num=target_num,
        factor_den=target_den,
        device_index=config.gpu_index,
        scale=config.scale,
        ensemble=config.ensemble,
        sc=config.sc,
        sc_threshold=None,
        trt=config.tensorrt,
        trt_optimization_level=config.tensorrt_optimization,
        trt_static_shape=config.tensorrt_static_shape,
        trt_min_shape=config.tensorrt_min_shape,
        trt_opt_shape=config.tensorrt_opt_shape,
        trt_max_shape=config.tensorrt_max_shape,
        # Outside the runtime so engines survive reinstalls (the installer keeps cache/).
        trt_cache_dir=os.path.join(script_dir, "cache", "tensorrt"),
        # The installer places the hash-verified model in the vsrife package.
        # Keeping this disabled prevents mutable network access during playback.
        auto_download=False,
    )
    clip = core.resize.Lanczos(
        clip,
        format=config.output_format,
        matrix=config.output_colorspace,
        transfer=config.output_transfer,
        primaries=config.output_primaries,
    )

    clip = core.std.AssumeFPS(
        clip=clip,
        fpsnum=dest_num,
        fpsden=dest_den,
    )
    return clip

# Main interpolation logic
if container_fps < config.disable_fps_threshold:  # type: ignore[name-defined]
    print_log(
        f"Containers FPS '{container_fps}' is below {config.disable_fps_threshold}. Interpolating..."  # type: ignore[name-defined]
    )

    clip = video_in  # type: ignore[name-defined]
    clip = interpolate(clip)
    print_log(f"Final clip: {clip}")
    clip.set_output()

else:
    print_log("Video is above FPS threshold. Ignoring...")

    clip = video_in  # type: ignore[name-defined]
    clip.set_output()
