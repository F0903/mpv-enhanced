# Third-party notices

mpv-enhanced combines configuration authored in this repository with software
and assets maintained by other projects. Their licenses remain controlling.
Release archives retain the license files shipped by each upstream artifact
and the applicable texts collected in [`LICENSES/`](LICENSES/). This summary is
not a replacement for those license texts.

| Component | Pinned version | Source | License |
| --- | --- | --- | --- |
| mpv Windows build | `20260814-git-7b8915bc1d` | [shinchiro/mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake) and [mpv-player/mpv](https://github.com/mpv-player/mpv) | mpv is GPL-2.0-or-later by default; bundled libraries have their own terms |
| 7-Zip reduced console | `26.02` | [ip7z/7zip](https://github.com/ip7z/7zip) | LGPL-2.1-or-later, BSD-3-Clause, and unRAR restriction for applicable portions; see `LICENSES/7zip-License.txt` |
| uv | `0.12.5` | [astral-sh/uv](https://github.com/astral-sh/uv) | Apache-2.0 or MIT; see `LICENSES/uv-APACHE-2.0.txt` and `LICENSES/uv-MIT.txt` |
| Python | `3.12.13` | [python/cpython](https://github.com/python/cpython) | Python Software Foundation License; see `LICENSES/Python-3.12.13.txt` |
| python-build-standalone payload | `3.12.13+20260807` | [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone/tree/20260807) | The builder is MPL-2.0; the produced Python distribution includes CPython and native dependencies under their respective terms. See `LICENSES/python-build-standalone-MPL-2.0.txt`, the payload's combined `LICENSE.txt` mirrored as `LICENSES/Python-standalone-3.12.13+20260807.txt`, and the exact upstream dependency texts in `LICENSES/python-build-standalone-20260807/` |
| VapourSynth | `77` | [vapoursynth/vapoursynth](https://github.com/vapoursynth/vapoursynth) | LGPL-2.1 |
| vs-rife | `5.7.0` | [HolyWu/vs-rife](https://github.com/HolyWu/vs-rife) | MIT |
| PyTorch | `2.12.1+cu130` | [pytorch/pytorch](https://github.com/pytorch/pytorch) | BSD-3-Clause; binary distributions include additional notices |
| Torch-TensorRT | `2.12.1+cu130` | [pytorch/TensorRT](https://github.com/pytorch/TensorRT) | BSD-3-Clause; NVIDIA dependencies have additional terms |
| TensorRT Python packages | `10.16.1.11` | [NVIDIA/TensorRT](https://github.com/NVIDIA/TensorRT) | NVIDIA software license and third-party notices |
| uosc | `5.12.0` | [tomasklaen/uosc](https://github.com/tomasklaen/uosc) | LGPL-2.1; see `LICENSES/uosc-LGPL-2.1.txt` |
| thumbfast | bundled snapshot | [po5/thumbfast](https://github.com/po5/thumbfast) | MPL-2.0; see `LICENSES/thumbfast-MPL-2.0.txt` |
| autocrop.lua | modified bundled snapshot | [mpv-player/mpv `TOOLS/lua/autocrop.lua`](https://github.com/mpv-player/mpv/blob/v0.41.0/TOOLS/lua/autocrop.lua) | GPL-2.0-or-later; see `LICENSES/autocrop-and-mpv-GPL-2.0.txt` |

The RIFE 4.25-lite model is pinned separately in `deps.lock.toml` by URL and
SHA-256. Its redistribution terms must be confirmed before a public workflow
re-hosts it. Likewise, a release that redistributes an mpv binary must include
the corresponding source materials and notices required by that exact build.
