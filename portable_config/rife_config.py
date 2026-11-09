"""
RIFE Configuration Settings

This file contains all the configurable parameters for the RIFE interpolation script.
Modify these values to customize the behavior according to your needs.
"""

import vapoursynth as vs
from vs_script.expensive_clip_mode import ExpensiveClipMode


# The method, model, multiplier and FPS cutoff are chosen in the MPV Enhanced menu.

# =============================================================================
# RIFE Model Settings
# =============================================================================

# Interpolation scale
scale = 1

# Produces better results at a decently heavy cost. NOTE: NOT SUPPORTED BY ALL MODELS
ensemble = False


# =============================================================================
# Video Output Format Settings
# =============================================================================

# You can change these to better match your display or source media
output_format = vs.YUV420P10
output_colorspace = vs.MATRIX_BT2020_NCL
output_transfer = vs.TRANSFER_BT2020_10
output_primaries = vs.PRIMARIES_BT2020


# =============================================================================
# Resolution and Performance Settings
# =============================================================================

# Resolution threshold for what determines if a clip is "expensive"
# Anything ABOVE this resolution will be considered expensive.
expensive_res_threshold = (3840, 2160)

# How do we handle expensive clips?
expensive_clip_handling = ExpensiveClipMode.DOWNSCALE

# Resolution to downscale to if expensive_clip_handling is "downscale"
downscale_res = (1920, 1080)

# Detect cuts between shots, so RIFE doesn't blend two shots together
sc = True

# =============================================================================
# GPU and TensorRT Settings
# =============================================================================

# Which GPU to use
gpu_index = 0

# RGBH is faster, RGBS is more accurate
gpu_format = vs.RGBH

# TensorRT is chosen per method in the menu. It builds an engine for each
# resolution and config, which can take minutes the first time.

# 0 is min - 5 is max. This will increase the time it takes to build the RT engine
tensorrt_optimization = 3

# Dynamic shapes allows TensorRT to build a single engine for multiple resolutions.
# Meaning that you only have to compile the engine once, and it will work for all resolutions within the min-max range.
# The downside is worse performance and memory usage than static shapes.
# You should set opt_shape to the resolution you will be using most of the time.
tensorrt_static_shape = True

# Min size of dynamic shape
tensorrt_min_shape = [128, 128]

# Optimized size of dynamic shape
tensorrt_opt_shape = [3840, 2160]

# Max size of dynamic shape
tensorrt_max_shape = [
    expensive_res_threshold[0],
    expensive_res_threshold[1],
]


# =============================================================================
# Logging Settings
# =============================================================================

# Log file path. Set to None for no log, or to an output stream to a log file
log = None #open("./rife_log.txt", "w")


# Keep personal settings in rife_config_local.py. It is imported last so user
# choices take precedence over both defaults and the selected profile.
try:
    from rife_config_local import *  # type: ignore[import-not-found]  # noqa: F403
except ModuleNotFoundError as error:
    if error.name != "rife_config_local":
        raise
