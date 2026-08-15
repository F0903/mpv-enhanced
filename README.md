# mpv-enhanced

An opinionated, portable mpv setup with uosc, thumbfast, automatic cropping,
and optional RIFE frame interpolation.

The supported installation target is currently **Windows x86-64**. The
installer is written in Python, bootstrapped by a pinned standalone `uv.exe`,
and does not require PowerShell or a preinstalled Python. The configuration is
path-independent, so installs no longer have to live in `C:\Program Files`.

## Install

Each Windows release has two distinct archives:

| Archive | Purpose |
| --- | --- |
| `mpv-enhanced-bootstrap-windows-x86_64-v*.zip` | Installer with `install.cmd`, the embedded `core` payload, and all profile choices |
| `mpv-enhanced-core-windows-x86_64-v*.zip` | Ready-to-run `core` build containing `mpv.exe` and `portable_config` |

For a guided or customizable install, download the **bootstrap** archive,
extract it, and run `install.cmd`. When run in a terminal it offers these
profiles:

| Profile | Contents |
| --- | --- |
| `core` | mpv, the enhanced configuration, uosc, thumbfast, and autocrop |
| `rife-cuda` | `core` plus a locked Python, VapourSynth, PyTorch CUDA, vs-rife, and model |
| `rife-tensorrt` | The RIFE stack with locked TensorRT acceleration |

If you only want the `core` setup, download the **core** archive, extract it,
and run `mpv.exe` directly. It is already assembled and intentionally does not
contain `install.cmd` or the optional RIFE runtime.

For an unattended install, make the choice explicit:

```bat
install.cmd install --profile core --install-dir "%LOCALAPPDATA%\Programs\mpv-enhanced" --yes
```

Other useful commands are:

```bat
install.cmd list-profiles
install.cmd doctor --install-dir "%LOCALAPPDATA%\Programs\mpv-enhanced"
```

`--cache-dir` keeps verified app downloads and the RIFE uv cache in a reusable
location. `--offline` forbids network access after the Python installer starts.
The release bootstrap carries its own controller environment; a source checkout
must be synced once before it can be used offline. The bootstrap's `core`
profile installs fully offline. RIFE profiles currently require network access
for their locked Python packages and hash-pinned model; a reusable cache can
reduce downloads during online reinstalls, but fully offline RIFE installation
is not currently supported.

RIFE requires a compatible NVIDIA GPU and driver. TensorRT engines are specific
to the selected settings and GPU, so the first playback at a new resolution can
take a while. Use the generated `mpv-enhanced.cmd` launcher for RIFE installs;
it exposes the app-local VapourSynth runtime to mpv without changing the
registry or global environment variables.

## Configuration

Managed files can be replaced during an upgrade. Put personal mpv settings in
`portable_config/mpv-local.conf` and personal RIFE settings in
`portable_config/rife_config_local.py`; the installer creates both from their
tracked `.example` files and preserves them on upgrades.

RIFE can be toggled with `Ctrl+Shift+R`. Automatic cropping can be toggled with
`Shift+C`.

## Locking and integrity model

- `deps.lock.toml` pins native artifacts and models by versioned URL and SHA-256.
- The root `uv.lock` pins the installer environment.
- `runtime/rife/uv.lock` pins the complete RIFE Python environment, including
  the selected CUDA package indexes.
- Downloads are verified before extraction. Installation happens in a sibling
  staging directory and is promoted only after all components succeed.
- A receipt in the installed tree records the profile and exact component
  versions, making `doctor` and rollback diagnosis possible.

Profiles are assets of one versioned release, not separate release lines. The
interactive prompt is a convenience; the same choices remain deterministic
CLI flags for package managers and automation.

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

The old `install-mpv-enhanced.ps1` is retained temporarily as a migration
reference. New development should use the Python installer and the lockfiles.

## Release builds

The tag workflow assembles and smoke-tests both ZIPs even while publication is
disabled. Uploading either an Actions artifact or a GitHub Release additionally
requires the repository variable `BINARY_REDISTRIBUTION_APPROVED_RELEASE` to
equal the exact tag and 40-character commit SHA being built, for example
`v1.2.3@0123456789abcdef0123456789abcdef01234567`. Set it only for the audited
release after completing the corresponding-source/redistribution review in
`THIRD_PARTY_NOTICES.md` and choosing a project license, then change or remove
it before approving another release.

## Included projects

mpv-enhanced builds on [mpv](https://mpv.io/),
[uosc](https://github.com/tomasklaen/uosc),
[thumbfast](https://github.com/po5/thumbfast),
[VapourSynth](https://www.vapoursynth.com/), and
[vs-rife](https://github.com/HolyWu/vs-rife). See `THIRD_PARTY_NOTICES.md` in
release archives for version, source, and license information.
