# mpv-enhanced

An opinionated, portable mpv setup with uosc, thumbfast, automatic cropping,
and optional frame interpolation using RTX Video Frame Generation or RIFE.

## Platforms

| Platform             | Interpolation methods            | mpv                                                       |
| -------------------- | -------------------------------- | --------------------------------------------------------- |
| Windows 10/11 x86-64 | `rtx`, `rife`, `rife-tensorrt`   | Official mpv build, downloaded and hash-checked           |
| Linux x86-64         | `rife`, `rife-tensorrt`          | Your distribution's `mpv` (mpv publishes no Linux builds) |
| macOS arm64 / x86-64 | None                             | Official mpv app bundle. **Installs, but untested.**      |

On Linux, the installer checks your `mpv` against the minimum versions in
`manifest.toml` before downloading anything. The RIFE methods need a recent mpv
**built with the `vapoursynth` filter**, because VapourSynth R74+ is loaded at
runtime, and many distributions don't ship one yet. RIFE is not available on
macOS because vs-rife needs CUDA.

## Install

Download the release zip (or clone the repository), extract it, and run:

- **Windows:** `mpve.cmd`
- **Linux / macOS:** `./mpve.sh` (also works from Git Bash, MSYS2, or Cygwin
  on Windows, where it hands over to `mpve.cmd`)

`mpve` is short for mpv-enhanced. Both scripts download a pinned, SHA-256-checked
`uv`, which installs the manager's own Python and runs it. Nothing else needs to
be preinstalled. The first run asks you to pick a profile, then the interpolation
methods you want, and you can tick as many as you like. The `core` profile is mpv,
the enhanced configuration, uosc, thumbfast, and autocrop.

| Method          | Contents                                                                 |
| --------------- | ------------------------------------------------------------------------ |
| `rtx`           | RTX Video Frame Generation in High, Medium, and Low                      |
| `rife`          | A locked Python, VapourSynth, PyTorch CUDA, vs-rife, and two RIFE models |
| `rife-tensorrt` | Everything in `rife`, plus faster RIFE using TensorRT                    |

`rtx` needs an NVIDIA RTX 40 series or newer GPU, and the RIFE methods need any
NVIDIA GPU that can run PyTorch CUDA. The screen marks methods this machine can't
run as unavailable, with the reason. The first method it can run starts ticked,
except `rife-tensorrt`, which needs a licence. Ticking nothing gives you plain
`core`.

For an unattended install, make the choice explicit:

```sh
mpve.cmd install --profile core --method rtx --accept-nvidia-license --yes
./mpve.sh install --profile core --method rife --yes
```

`--method` can be repeated. Without it, a fresh install has no interpolation.

Optional components can be added to any profile. The first install offers them,
`mpve profile` lets you change them, and updates keep what you have.

| Component | Contents                                                                                          |
| --------- | ------------------------------------------------------------------------------------------------- |
| `mpvfin`  | [Jellyfin cast target](https://github.com/F0903/mpvfin), so you can play to mpv from any Jellyfin app. Windows only. |

Add one to an unattended install with `--with mpvfin`, then set `server=` in
`portable_config/script-opts/mpvfin.conf`; an interactive install asks for the
server URL. mpvfin logs in with Quick Connect the first time mpv starts. To have
mpv wait for a cast, start it with `mpv --idle --force-window`.

NVIDIA's software is proprietary and is downloaded directly from NVIDIA by each
user. The `rife-tensorrt` method is governed by the
[TensorRT SLA](https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html),
and the `rtx` method by the
[NVIDIA Software License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/).
An interactive install displays the agreement and requires typing `accept`.
After reviewing it, unattended installs must pass `--accept-nvidia-license`
explicitly; `--yes` never accepts third-party terms.

## Managing an install

Each version installs to `live/<version>/`, holding its own copy of the manager
(`release/`) and the installed player (`app/`). `live/current` names the active
version. Running `mpve` with no arguments opens a menu, or use the commands:

```sh
mpve.cmd status             # active version and profile
mpve.cmd profile            # change the active version's profile, methods, and components
mpve.cmd update             # install the newest release and switch to it
mpve.cmd rollback           # switch back to the previous version
mpve.cmd use 3.0.0          # make an installed version active, and put mpv on the PATH
mpve.cmd benchmark          # measure the installed methods and suggest targets
mpve.cmd doctor             # check the active install
mpve.cmd list-profiles
```

An update builds the new version alongside the current one and only switches
once it has installed successfully, keeping your personal settings. The current
and previous versions are kept. In a git checkout, use `git pull` instead of
`mpve update`.

`mpve use` makes `mpv` run the active version, and later updates and rollbacks
keep it pointed there. Without a version it keeps the active one. It changes:

- **Windows:** your user PATH gains the active version's folder, holding the real
  `mpv.exe`, and this project's folder for `mpve`. Terminals already open keep the
  version they started with. The Start menu gains an **MPV Enhanced** shortcut to
  the active `mpv.exe`.
- **Linux:** your system `mpv` stays, and `~/.config/mpv` becomes a symlink to the
  active version's configuration. `mpve` is linked into `~/.local/bin`. RIFE
  methods also add `/etc/ld.so.conf.d/mpv-enhanced.conf`, so `sudo` asks for your
  password when the version changes.
- **macOS:** as on Linux, plus `~/.local/bin/mpv` links to the downloaded mpv.

If `~/.config/mpv` or one of those links already exists and isn't from
mpv-enhanced, `mpve use` stops without changing anything, so move yours away first.

`--install-dir` installs somewhere else, outside this version management; on
Linux and macOS, start it with `--config-dir` pointing at its `portable_config`.
`--cache-dir` keeps verified downloads and the interpolation uv cache in a
reusable location. `--offline` forbids downloads; it works for `core` once its
downloads are cached, but not with interpolation methods.

## Running

After `mpve use`, start the player with `mpv`, or on Windows from the Start menu.
On Windows and macOS, `mpve status` shows the full path, which changes with each
version, so your own shortcuts and file associations should be pointed at the new
one after an update.

On Windows, `mpv.exe` is started directly with no launcher: a copy of
`VSScript.dll` beside it loads the app-local VapourSynth, and the installer
records which Python it uses in `%APPDATA%\vapoursynth\vapoursynth.toml` (one
entry per install, keyed by path). No launcher is used on Linux or macOS either:
mpv reads its configuration through the symlink from `mpve use`, and on Linux it
finds the app-local VapourSynth through the system library path, with the same
per-install entry in `~/.config/vapoursynth/vapoursynth.toml`.

To move an RTX or RIFE install, reinstall it at the new location: its Python
environment records its own absolute path. `core` installs can be moved freely.

RIFE requires a compatible NVIDIA GPU and driver. TensorRT engines are specific
to the resolution, settings, GPU, and TensorRT version, so the first playback at
a new resolution builds one, which can take minutes. Engines
are cached in `portable_config/cache/tensorrt` and kept across upgrades.

## Configuration

Managed files can be replaced during an upgrade. Put personal mpv settings in
`portable_config/mpv-local.conf` and personal interpolation settings in
`portable_config/rife_config_local.py`; the installer creates both from their
tracked `.example` files and preserves them on upgrades. Upgrades also keep
`portable_config/watch_later` (resume positions), `portable_config/cache`
(mpv's caches and compiled TensorRT engines), mpvfin's server and saved login, and
the menu's settings in `portable_config/script-opts/mpv_enhanced.json`.

## The MPV Enhanced menu

Open the uosc menu and choose **MPV Enhanced** to manage interpolation while
watching. Changes wait until you press **Apply**, and then the video that's playing
switches straight away. The on/off switch is the exception and acts instantly.

- **Default method and multiplier** are used while there are no target overrides.
- **Target overrides** map a source to a method and multiplier: for example, up to
  4K at 30 fps uses RTX Medium ×2. A file uses the smallest target that covers it,
  and a file beyond them all uses the largest. The **None** method plays a file
  without interpolation.
- **FPS cutoff** leaves files at or above that frame rate alone.
- **Run benchmark** measures the methods you installed on your PC and suggests
  targets, which wait for Apply. The installer offers the same benchmark.

Frame interpolation can be toggled with `Ctrl+Shift+R`. Automatic cropping can be toggled with
`Shift+C`.

## Locking and integrity

- `manifest.toml`, maintained by hand, defines the profiles and methods and pins every
  download (official mpv builds, the RIFE model) by versioned URL and SHA-256.
- `uv.pin` holds the uv version and per-platform SHA-256 for `mpve.cmd`,
  `mpve.sh`, and CI.
- `.python-version` sets the Python for both the installer and the RIFE runtime.
- The root `uv.lock` pins the installer environment; `runtime/rife/uv.lock`
  pins the complete RIFE Python environment, including the CUDA indexes.
- Downloads are verified before extraction. Installation happens in a sibling
  staging directory and is promoted only after every component succeeds.
- A receipt in the installed tree records the profile, methods, and component versions
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
