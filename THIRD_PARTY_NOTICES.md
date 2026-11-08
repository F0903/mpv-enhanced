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

| Component | Pinned version | Publisher and terms |
| --- | --- | --- |
| mpv (Windows, macOS) | `0.42.0` official builds, SHA-256 pinned in `deps.lock.toml` | [mpv release](https://github.com/mpv-player/mpv/releases/tag/v0.42.0); GPL. Source is published with the same release. On Linux the distribution's mpv is used instead. |
| uv | `0.12.5`, SHA-256 pinned in `install.cmd` / `install.sh` | [Astral release](https://github.com/astral-sh/uv/releases/tag/0.12.5); Apache-2.0 or MIT. |
| CPython (installer and RIFE runtime) | `3.12.13` | Downloaded by uv from python-build-standalone; PSF and bundled-component terms. |
| VapourSynth | `77` | [PyPI](https://pypi.org/project/VapourSynth/77/); LGPL-2.1-or-later. |
| vs-rife | `5.7.0` | [PyPI](https://pypi.org/project/vsrife/5.7.0/); MIT. |
| RIFE 4.25-lite model | `flownet_v4.25.lite.pkl`, SHA-256 `81cdba223fe72a120130cc8552e5d2ecac824259d406f0c15323b3decf96b8b1` | [HolyWu model release](https://github.com/HolyWu/vs-rife/releases/tag/model); Practical-RIFE places its trained models under [MIT](https://github.com/hzwer/Practical-RIFE#trained-model). |
| PyTorch CUDA | `2.12.1+cu130` | BSD-3-Clause; the wheels include NVIDIA CUDA libraries under the [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html). |
| Torch-TensorRT | `2.12.1+cu130` | BSD-style license; it does not replace the terms of the proprietary TensorRT dependencies. |
| TensorRT | `10.16.1.11` | Proprietary; [TensorRT SLA](https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html). The installer requires explicit acceptance before downloading it; `--yes` is not acceptance. |
