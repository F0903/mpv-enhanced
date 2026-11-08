# mpv-enhanced

An opinionated, portable mpv setup with uosc, thumbfast, automatic cropping,
and optional frame interpolation using RTX Video Frame Generation or RIFE.

## Platforms

| Platform             | Profiles                                                                 | mpv                                                       |
| -------------------- | ------------------------------------------------------------------------ | --------------------------------------------------------- |
| Windows 10/11 x86-64 | `rtx-high`, `rtx-medium`, `rtx-low`, `rife-cuda`, `rife-tensorrt`, `core` | Official mpv build, downloaded and hash-checked           |
| Linux x86-64         | `rife-cuda`, `rife-tensorrt`, `core`                                     | Your distribution's `mpv` (mpv publishes no Linux builds) |
| macOS arm64 / x86-64 | `core`                                                                   | Official mpv app bundle. **Installs, but untested.**      |

On Linux, the installer checks your `mpv` against the minimum versions in
`manifest.toml` before downloading anything. The RIFE profiles need a recent mpv
**built with the `vapoursynth` filter**, because VapourSynth R74+ is loaded at
runtime, and many distributions don't ship one yet. RIFE is not available on
macOS because vs-rife needs CUDA.

## Install

Download the release zip (or clone the repository), extract it, and run:

- **Windows:** `install.cmd`
- **Linux / macOS:** `./install.sh` (also works from Git Bash, MSYS2, or Cygwin
  on Windows, where it hands over to `install.cmd`)

Both scripts download a pinned, SHA-256-checked `uv`, which installs the
installer's own Python and runs it. Nothing else needs to be preinstalled.
When run interactively, the installer offers these profiles, and pressing Enter
selects the default, `rtx-high`:

| Profile         | Contents                                                                   |
| --------------- | -------------------------------------------------------------------------- |
| `rtx-high`      | `core` plus RTX Video Frame Generation at its highest quality              |
| `rtx-medium`    | RTX Video Frame Generation with a lighter model                            |
| `rtx-low`       | RTX Video Frame Generation with the lightest model                         |
| `rife-cuda`     | `core` plus a locked Python, VapourSynth, PyTorch CUDA, vs-rife, and model |
| `rife-tensorrt` | The RIFE stack with locked TensorRT acceleration                           |
| `core`          | mpv, the enhanced configuration, uosc, thumbfast, and autocrop             |

The `rtx-*` profiles need an NVIDIA RTX 40 series or newer GPU, and the RIFE
profiles need any NVIDIA GPU that can run PyTorch CUDA. The menu marks profiles
this machine can't run as unavailable, with the reason, and if the default is
one of them, the first available profile becomes the default.

For an unattended install, make the choice explicit:

```sh
install.cmd install --profile core --install-dir "%LOCALAPPDATA%\Programs\mpv-enhanced" --yes
./install.sh install --profile core --install-dir ~/.local/share/mpv-enhanced --yes
```

NVIDIA's software is proprietary and is downloaded directly from NVIDIA by each
user. The `rife-tensorrt` profile is governed by the
[TensorRT SLA](https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html),
and the `rtx-*` profiles by the
[NVIDIA Software License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/).
An interactive install displays the agreement and requires typing `accept`.
After reviewing it, unattended installs must pass `--accept-nvidia-license`
explicitly; `--yes` never accepts third-party terms.

Other useful commands:

```sh
install.cmd list-profiles
install.cmd doctor --install-dir "%LOCALAPPDATA%\Programs\mpv-enhanced"
```

`--cache-dir` keeps verified downloads and the interpolation uv cache in a
reusable location. `--offline` forbids downloads; it works for `core` once its
downloads are cached, but not for the interpolation profiles.

## Running

- **Windows `core`:** run `mpv.exe`; it picks up `portable_config` by itself.
- **Windows RTX and RIFE:** run `mpv-enhanced.cmd`. It points mpv at the app-local
  VapourSynth (`VSSCRIPT_PATH`) without touching the registry or global
  environment variables. VapourSynth itself records which Python each install
  uses in `%APPDATA%\vapoursynth\vapoursynth.toml` (one entry per install,
  keyed by path); on Linux that file stays inside the install.
- **Linux / macOS:** run `./mpv-enhanced`, which starts mpv with
  `--config-dir` set to the installed `portable_config`.

To move an RTX or RIFE install, reinstall it at the new location: its Python
environment records its own absolute path. `core` installs can be moved freely.

RIFE requires a compatible NVIDIA GPU and driver. TensorRT engines are specific
to the resolution, settings, GPU, and TensorRT version, so the first playback at
a new resolution builds one (under a minute at 720p on an RTX 4080). Engines
are cached in `portable_config/cache/tensorrt` and kept across upgrades.

## Configuration

Managed files can be replaced during an upgrade. Put personal mpv settings in
`portable_config/mpv-local.conf` and personal interpolation settings in
`portable_config/rife_config_local.py`; the installer creates both from their
tracked `.example` files and preserves them on upgrades. Upgrades also keep
`portable_config/watch_later` (resume positions) and `portable_config/cache`
(mpv's caches and compiled TensorRT engines).

The RIFE profiles default to the fast `4.25.lite` model and also install the
full `4.25` model, the release upstream says significantly improved anime
scenes. To use it, set `rife_model = "4.25"` in `portable_config/rife_config_local.py`.

Frame interpolation can be toggled with `Ctrl+Shift+R`. Automatic cropping can be toggled with
`Shift+C`.

## Locking and integrity

- `manifest.toml`, maintained by hand, defines the profiles and pins every
  download (official mpv builds, the RIFE model) by versioned URL and SHA-256.
- `uv.pin` holds the uv version and per-platform SHA-256 for `install.cmd`,
  `install.sh`, and CI.
- `.python-version` sets the Python for both the installer and the RIFE runtime.
- The root `uv.lock` pins the installer environment; `runtime/rife/uv.lock`
  pins the complete RIFE Python environment, including the CUDA indexes.
- Downloads are verified before extraction. Installation happens in a sibling
  staging directory and is promoted only after every component succeeds.
- A receipt in the installed tree records the profile and component versions
  for `doctor`.

To update mpv, change its `version` and SHA-256 values in `manifest.toml` to a
newer [mpv release](https://github.com/mpv-player/mpv/releases); GitHub shows
each asset's SHA-256 digest. The download URLs use `{version}`.

## Development

Install [uv](https://docs.astral.sh/uv/), then run:

```text
uv sync --locked
uv run mpv-enhanced list-profiles
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv lock --check --project runtime/rife
```

## Releases

The version lives in `pyproject.toml`; bump it with `uv version <new>`, which
also updates `uv.lock`. Pushing a matching `v*.*.*` tag runs the tests on
Windows, Linux, and macOS (including a real `core` install on each), then
publishes `git archive` of the tag as
`mpv-enhanced-<tag>.zip` with a `SHA256SUMS` file. The zip contains only this
repository's files; mpv, uv, Python, and the RIFE stack are downloaded from
their publishers at install time.

## License

Project-authored code and configuration are GPL-2.0-or-later. Bundled files
that carry their own notices remain under those terms. See `LICENSE` and
`THIRD_PARTY_NOTICES.md`.

## Included projects

mpv-enhanced builds on [mpv](https://mpv.io/),
[uosc](https://github.com/tomasklaen/uosc),
[thumbfast](https://github.com/po5/thumbfast),
[VapourSynth](https://www.vapoursynth.com/), and
[vs-rife](https://github.com/HolyWu/vs-rife).
