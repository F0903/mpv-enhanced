# mpv-enhanced

An opinionated, portable mpv setup with uosc, thumbfast, automatic cropping,
and optional RIFE frame interpolation.

## Platforms

| Platform | Profiles | mpv |
| --- | --- | --- |
| Windows 10/11 x86-64 | `core`, `rife-cuda`, `rife-tensorrt` | Official mpv 0.42.0 build, downloaded and hash-checked |
| Linux x86-64 | `core`, `rife-cuda`, `rife-tensorrt` | Your distribution's `mpv` (mpv publishes no Linux builds) |
| macOS arm64 / x86-64 | `core` | Official mpv 0.42.0 app bundle. **Installs, but untested.** |

On Linux, `core` needs mpv 0.36 or newer. The RIFE profiles need mpv **0.42 or
newer built with the `vapoursynth` filter**, because VapourSynth R74+ is loaded
at runtime. As of November 2026 most distributions still ship 0.41 or older;
the installer checks this before downloading anything. RIFE is not available
on macOS because vs-rife needs CUDA.

## Install

Download the release zip (or clone the repository), extract it, and run:

- **Windows:** `install.cmd`
- **Linux / macOS:** `./install.sh` (also works from Git Bash, MSYS2, or Cygwin
  on Windows, where it hands over to `install.cmd`)

Both scripts download a pinned, SHA-256-checked `uv`, which installs the
installer's own Python and runs it. Nothing else needs to be preinstalled.
Run interactively, the installer offers these profiles:

| Profile | Contents |
| --- | --- |
| `core` | mpv, the enhanced configuration, uosc, thumbfast, and autocrop |
| `rife-cuda` | `core` plus a locked Python, VapourSynth, PyTorch CUDA, vs-rife, and model |
| `rife-tensorrt` | The RIFE stack with locked TensorRT acceleration |

For an unattended install, make the choice explicit:

```sh
install.cmd install --profile core --install-dir "%LOCALAPPDATA%\Programs\mpv-enhanced" --yes
./install.sh install --profile core --install-dir ~/.local/share/mpv-enhanced --yes
```

The `rife-tensorrt` profile is governed by NVIDIA's
[TensorRT SLA](https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html).
An interactive install displays that agreement and requires typing `accept`.
After reviewing it, unattended installs must pass `--accept-nvidia-license`
explicitly; `--yes` never accepts third-party terms.

Other useful commands:

```sh
install.cmd list-profiles
install.cmd doctor --install-dir "%LOCALAPPDATA%\Programs\mpv-enhanced"
```

`--cache-dir` keeps verified downloads and the RIFE uv cache in a reusable
location. `--offline` forbids downloads; it works for `core` once its
downloads are cached, but not for the RIFE profiles.

## Running

- **Windows `core`:** run `mpv.exe`; it picks up `portable_config` by itself.
- **Windows RIFE:** run `mpv-enhanced.cmd`. It points mpv at the app-local
  VapourSynth (`VSSCRIPT_PATH`) without touching the registry or global
  environment variables. VapourSynth itself records which Python each install
  uses in `%APPDATA%\vapoursynth\vapoursynth.toml` (one entry per install,
  keyed by path); on Linux that file stays inside the install.
- **Linux / macOS:** run `./mpv-enhanced`, which starts mpv with
  `--config-dir` set to the installed `portable_config`.

To move a RIFE install, reinstall it at the new location: its Python
environment records its own absolute path. `core` installs can be moved freely.

RIFE requires a compatible NVIDIA GPU and driver. TensorRT engines are specific
to the resolution, settings, GPU, and TensorRT version, so the first playback at
a new resolution builds one (under a minute at 720p on an RTX 4080). Engines
are cached in `portable_config/cache/tensorrt` and kept across upgrades.

## Configuration

Managed files can be replaced during an upgrade. Put personal mpv settings in
`portable_config/mpv-local.conf` and personal RIFE settings in
`portable_config/rife_config_local.py`; the installer creates both from their
tracked `.example` files and preserves them on upgrades. Upgrades also keep
`portable_config/watch_later` (resume positions) and `portable_config/cache`
(mpv's caches and compiled TensorRT engines).

RIFE can be toggled with `Ctrl+Shift+R`. Automatic cropping can be toggled with
`Shift+C`.

## Locking and integrity

- `deps.lock.toml` pins every download (official mpv builds, the RIFE model)
  by versioned URL, size, and SHA-256.
- `install.cmd` and `install.sh` pin uv by version and SHA-256.
- The root `uv.lock` pins the installer environment; `runtime/rife/uv.lock`
  pins the complete RIFE Python environment, including the CUDA indexes.
- Downloads are verified before extraction. Installation happens in a sibling
  staging directory and is promoted only after every component succeeds.
- A receipt in the installed tree records the profile and component versions
  for `doctor`.

To update mpv, change the URLs, sizes, and SHA-256 values in `deps.lock.toml`
to a newer [mpv release](https://github.com/mpv-player/mpv/releases); GitHub
shows each asset's SHA-256 digest.

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

Pushing a `v*.*.*` tag runs the tests on Windows, Linux, and macOS (including a
real `core` install on each), then publishes `git archive` of the tag as
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
