# Third-party notices

mpv-enhanced is licensed under GPL-2.0-or-later; see [`LICENSE`](LICENSE).
Files identified below remain under their upstream terms. The license texts
collected in [`LICENSES/`](LICENSES/) are part of the repository and release
zip. This is an engineering summary, not legal advice.

## Files included in this repository and its release zip

| Component | Pinned identity | License and retained notice | Source |
| --- | --- | --- | --- |
| uosc, Ziggy, and `uosc_textures.ttf` | uosc `5.12.0`, tag commit `748bb238a962f8aecd0af51ee4b46651b58f6c51` | LGPL-2.1-or-later; see `LICENSES/LGPL-2.1.txt`. Ziggy's linked Go-component notices and grants are in `LICENSES/Ziggy-Go-NOTICES.txt` and `LICENSES/Ziggy-Go-PATENTS.txt`. Local changes that disable uosc's network updater are marked in the modified source files. | [Exact source](https://github.com/tomasklaen/uosc/tree/5.12.0). The Windows, Linux, and macOS Ziggy binaries are uosc's unmodified upstream builds of `src/ziggy`. |
| Material Icons Round / `uosc_icons.otf` | uosc's renamed `MaterialIconsRound-Regular.otf`, byte-identical to Google commit `1c575764826fa5752ddc2bdc6b31ef7cc3233371` | Apache-2.0; see `LICENSES/Material-Icons-NOTICE.txt` and `LICENSES/Apache-2.0.txt`. | [Exact font](https://github.com/google/material-design-icons/blob/1c575764826fa5752ddc2bdc6b31ef7cc3233371/font/MaterialIconsRound-Regular.otf) |
| thumbfast and reused FFI code | thumbfast commit `9deb0733c4e36938cf90e42ddfb7a19a8b2f4641`, reformatted without functional changes; uosc `lib/utils.lua` marks FFI portions copied from thumbfast | MPL-2.0; see the retained source headers and `LICENSES/thumbfast-MPL-2.0.txt`. | [Exact source](https://github.com/po5/thumbfast/tree/9deb0733c4e36938cf90e42ddfb7a19a8b2f4641) |
| autocrop.lua | Modified from mpv `TOOLS/lua/autocrop.lua` at v0.41.0 | GPL-2.0-or-later; see the file header and `LICENSE`. | [Upstream source](https://github.com/mpv-player/mpv/blob/v0.41.0/TOOLS/lua/autocrop.lua) |
| fzy Lua implementation | `portable_config/scripts/uosc/lib/fzy.lua` | MIT; the copyright and permission notice are retained in the file. | Distributed as source within uosc. |

## Downloaded for the user at install time

These are not redistributed by mpv-enhanced. The installer fetches each locked
artifact from its publisher, verifies it, and installs it for the user.

| Component | Pinned in | Publisher and terms |
| --- | --- | --- |
| mpv (Windows, macOS) | `manifest.toml` | [mpv releases](https://github.com/mpv-player/mpv/releases); GPL. Source is published with each release. On Linux the distribution's mpv is used instead. |
| mpvfin (optional, Windows) | `manifest.toml` | [mpvfin releases](https://github.com/F0903/mpvfin/releases), by this project's author; GPL-3.0-or-later. Downloaded only if chosen. |
| uv | `uv.pin` | [Astral releases](https://github.com/astral-sh/uv/releases); Apache-2.0 or MIT. |
| CPython (installer and RIFE runtime) | `.python-version` | Downloaded by uv from python-build-standalone; PSF and bundled-component terms. |
| VapourSynth | `runtime/rife/uv.lock` | [PyPI](https://pypi.org/project/VapourSynth/); LGPL-2.1-or-later. |
| vs-rife | `runtime/rife/uv.lock` | [PyPI](https://pypi.org/project/vsrife/); MIT. |
| RIFE models | `manifest.toml` | [HolyWu model release](https://github.com/HolyWu/vs-rife/releases/tag/model); Practical-RIFE places its trained models under [MIT](https://github.com/hzwer/Practical-RIFE#trained-model). |
| PyTorch CUDA | `runtime/rife/uv.lock` | BSD-3-Clause; the wheels include NVIDIA CUDA libraries under the [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html). |
| Torch-TensorRT | `runtime/rife/uv.lock` | BSD-style license; it does not replace the terms of the proprietary TensorRT dependencies. |
| NVIDIA Video Effects SDK (`nvidia-vfx`) | `runtime/rife/uv.lock` | Proprietary; [NVIDIA Software License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/). Used by the `rtx-*` profiles. Each user downloads it from NVIDIA after explicit acceptance; `--yes` is not acceptance. |
| TensorRT | `runtime/rife/uv.lock` | Proprietary; [TensorRT SLA](https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html). The installer requires explicit acceptance before downloading it; `--yes` is not acceptance. |
