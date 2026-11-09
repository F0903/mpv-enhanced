# Interpolation methods, MPV Enhanced menu, and benchmark

Status: design, not built. Covers the last two `TODO.md` items and the menu idea.
Items marked **(call)** were not decided by the project owner and can be overridden.
Everything else is what the owner asked for or confirmed.

## Why

Changing how video is interpolated currently means reinstalling a profile or
editing Python. What suits a machine also varies by file: a high-end card may run
the best method at 4K, but other cards won't. The owner wants to manage
this from inside the player, while watching, and wants a new user with an unknown
GPU to get a sensible setup without guessing.

## Pieces, in build order

1. Installer: interpolation methods move out of profiles onto their own screen.
2. Menu: an MPV Enhanced entry in uosc with target overrides and live Apply.
3. Benchmark: offered by the installer, and it suggests target overrides.

An HDR menu and installer screen detection are a separate, later project.

## Goals

- Tick any number of interpolation methods at install time and switch between them
  in the player.
- Turn interpolation on and off from the menu.
- Build a list of target overrides in the menu, each mapping a source limit to a
  method and multiplier.
- Press Apply and have the video already playing switch immediately.
- Give a new user suggested targets, measured on their own PC, in a few minutes.

## Non-goals

- HDR settings.
- The Vulkan RIFE plugin for AMD and Intel. It becomes a new method entry later.
- Menu entries for deep RIFE and TensorRT tuning, which stay in
  `rife_config_local.py`.
- Typing free-form values into the menu; values come from fixed lists **(call)**.
- Fixed-frame-rate targets such as "always 60 fps". Targets use multipliers, so
  `TargetFpsMode.fixed_fps` goes away **(call)**.

## Anti-goals

- A menu edit taking effect before Apply. The on/off switch is the one exception.
- Any video window appearing during the benchmark.
- Losing the entries uosc's menu has today.
- Sending benchmark results anywhere, or quoting them in this repository, which is
  public. They stay on the PC: NVIDIA's agreement for `nvidia-vfx` forbids disclosing
  benchmark results without written permission.
- A second source of truth for what's installed or what's selected.

## 1. Installer

### Screens, in order

1. Profile picker, with `core` as its only entry. It is still shown, so that it can
   gain entries later.
2. Interpolation methods (new).
3. Optional components (exists).
4. Optional component setup (exists).
5. Licences needed by the ticked methods.
6. Install.
7. Benchmark offer, if any method was ticked.
8. Whether interpolation starts on or off, if any method was ticked.

### Methods screen

It works like the optional components screen: numbered tick boxes with a short
description, numbers toggle, Enter continues. Methods this machine can't run are
listed as unavailable with the reason and have no number.

| Method | Includes | Needs | Menu choices |
| --- | --- | --- | --- |
| `rtx` | `rife-runtime` with extra `rtx` | Windows x86-64, RTX 40 series or newer, NVIDIA licence | RTX High, RTX Medium, RTX Low |
| `rife` | `rife-runtime`, both RIFE models | Windows or Linux x86-64, NVIDIA GPU, on Linux mpv 0.42+ with VapourSynth | RIFE 4.25-lite, RIFE 4.25 |
| `rife-tensorrt` | as `rife`, plus extra `tensorrt` | as `rife`, plus TensorRT licence | Everything `rife` offers, plus RIFE 4.25-lite TensorRT and RIFE 4.25 TensorRT **(call)** |

`runtime/rife`'s extras don't conflict, so any combination of ticks can be locked
and installed together.

The first method that can run starts ticked, in the order `rtx`, then `rife`.
`rife-tensorrt` is never pre-ticked, because it needs a licence **(call)**. Ticking
nothing is today's `core`.

### Behaviour

- `mpve profile` reopens these screens, with the current selection marked.
- The start-state question appears only on a first install. Enter picks On if
  benchmark results were accepted and Off otherwise. Unattended installs start Off
  **(call)**.
- Updates keep the selection without asking, the same rule optional components use.
- Unattended installs use a repeatable `--method NAME`. Without it, a fresh install
  gets no methods and an existing install keeps its own.
- `manifest.toml` gains a `methods` table, and the `rtx-*` and `rife-*` profiles are
  removed. `default_profile` becomes `core`. The receipt records the ticked methods.
- 3.0.0 is unreleased, so no migration from the old profiles is needed **(call)**.
- If a selection change removes a method that a saved target uses, the installer
  rewrites that target to the first installed choice, so the player never meets an
  unknown method **(call)**.

## 2. Menu

### Entry point

uosc stops generating its default menu as soon as `input.conf` has any `#!` line
(`portable_config/scripts/uosc/lib/menus.lua:697`), so `input.conf` must declare all
of `create_default_menu_items` (`scripts/uosc/main.lua:305`) as well as the new
entry. The entry sends a script message to a new `scripts/mpv_enhanced.lua`, which
opens its menu through uosc's `open-menu` message with nested `items`, and redraws
with `update-menu`. No uosc source changes **(call)**. `scripts/toggle_interpolation.lua`
is replaced, and `Ctrl+Shift+R` keeps toggling interpolation.

### Contents

The layout the owner approved, plus the two entries added afterwards:

- **MPV Enhanced**
  - Interpolation: On/Off
  - Default method ▸ and Default multiplier ▸ (greyed, with a note that they are
    only used while there are no target overrides)
  - FPS cutoff ▸
  - Target overrides ▸ with a count
  - Run benchmark
  - A read-only note: what's playing and which target it uses
  - Apply, showing the number of pending changes
  - Discard changes
- **Target overrides**: sorted, highest first, each row showing its limits, method
  and multiplier, with a dot on the one the current file uses, then Add target.
- **One target**: Max resolution ▸, Max frame rate ▸, Method ▸, Multiplier ▸, Delete.

If no methods are installed, the interpolation entries are left out. Method lists
contain the installed choices plus **None**, which means "don't interpolate this".
It is a method, not the on/off switch: the switch stays on, and a file matched to
None plays without the filter. With None selected, Multiplier doesn't apply and is
greyed out. The default method can be None too **(call)**.

Value lists **(call)**: resolution 720p, 1080p, 1440p, 4K, 8K; frame rate 24, 30,
48, 60, 120; multiplier ×2, ×3, ×4; FPS cutoff 30, 48, 60, 90, 120, Off. Default
cutoff is 60, as it is today.

### Matching rules

- A target's weight is pixels per second: its max resolution × its max frame rate.
  The list is always sorted by weight, highest first. Ties go to the larger
  resolution **(call)**.
- A file uses the lowest-weight target that covers both its resolution and frame
  rate. If none covers it, it uses the highest target. A file below the weakest
  target is therefore covered by the weakest.
- With no targets, the file uses the default method and multiplier.
- A file at or above the FPS cutoff is never interpolated, whatever the targets say.
- 24 and 30 include 23.976 and 29.97. A file fits a resolution when both width and
  height fit, and the 4K limit is 4096×2160, so cropped and DCI video count as 4K
  **(call)**.
- Two targets may not have identical limits. The edit is refused with a message
  **(call)**.

### Applying

- Edits are held in memory and marked pending. Apply saves them, re-matches the
  current file, and swaps the filter immediately if interpolation is on.
- Discard drops pending edits. Closing the menu keeps them until mpv exits **(call)**.
- The on/off switch acts instantly and is remembered, so mpv starts the way it was
  left **(call)**.
- Each new file is re-matched, and the filter changes only if the result differs.
- A swap removes the old filter before adding the new one, so two GPU sessions
  never coexist. An OSD message confirms the result, such as
  `Interpolation: RTX Medium ×2`.
- A TensorRT choice whose engine isn't built yet builds it when the filter loads, as
  it does today, so that switch can take minutes. An OSD message says so first. No
  prebuilding or background builds **(call)**.
- mpv's VapourSynth filter has a `user-data` option that reaches the script as
  `user_data`. Lua does the matching and passes the resolved choice through it, so
  `interpolation.vpy` only applies what it's told and matching exists in one place.

### Settings

- `portable_config/script-opts/mpv_enhanced.json` holds the on/off state, defaults,
  cutoff and targets. The menu and the benchmark write it atomically, and updates
  preserve it. A missing or malformed file falls back to defaults with an OSD
  warning **(call)**.
- A file generated at install lists the installed choices and how to run `mpve`, and
  replaces the method fields of `rife_profile.py` **(call)**.
- `interpolator`, `rtx_mode`, `rife_model`, `tensorrt`, `target_mode` and
  `disable_fps_threshold` stop being settings in `rife_config.py`. Everything else
  there, including `rife_config_local.py` overrides, stays.

## 3. Benchmark

- `mpve benchmark` is a new command. The installer offers it after a first install
  or `mpve profile` when a method is ticked, never during `mpve update`, and it runs
  only interactively. The offer can be declined.
- Results replace the whole target list, never merge into it. If targets already
  exist, the installer's offer and `mpve benchmark` both say so and default to no,
  so a hand-built list can't be lost by accident **(call)**.
- It first explains what it does, roughly how long it takes, and that games and other
  GPU-heavy work should be closed, then shows a progress bar. It takes a few minutes
  at most, in total.
- It runs each installed choice through the real filter path, as fast as possible,
  with `--untimed` and `--vo=null`. Results from solid-colour clips were badly
  misleading in earlier testing, so the clip must be generated moving content.
- A choice counts as usable at a level if it clears real time by at least 1.1×
  **(call)**.
- It may measure a few points and estimate the rest from pixels per second, because
  the owner asked for an estimate. Suggested targets use ×2 **(call)**.
- The suggestion is the best usable choice at each level, best first in this order:
  RTX High, RTX Medium, RIFE 4.25 TensorRT, RIFE 4.25-lite TensorRT, RIFE 4.25,
  RIFE 4.25-lite, RTX Low **(call)**. Adjacent levels with the same result are merged.
- Levels the PC can't handle get a None target. There is one above the highest
  usable level, so files beyond it play normally instead of stuttering. If nothing
  is usable anywhere, the result is a single None target and a message saying so.
- From the installer, results become the targets directly.
- From the menu, Run benchmark pauses playback, shows progress on screen, and the
  results replace the list as pending edits that wait for Apply, so Discard restores
  what you had **(call)**.
- TensorRT engines can take minutes to build per resolution, so TensorRT choices are
  measured only if they fit the time limit, and skipped otherwise **(call)**.

## Left to the builder

- Manifest field names for `methods`, and the JSON layout of the two files.
- How `mpve benchmark` reports progress to the Lua script.
- Which levels to measure and how to spend the time budget.
- Whether `TargetFpsMode` is removed outright or reduced to a multiplier.
- Keeping the matching logic testable, and how to test Lua in this repo.

## Done when

- A fresh interactive install on the owner's PC ticks methods, offers the benchmark,
  and ends with working suggested targets.
- During playback, switching a target's method and pressing Apply changes the
  picture without restarting the file, and interpolation stays on.
- `README.md` describes the methods screen, menu and benchmark, and `TODO.md` no
  longer lists the two finished items.
- Python tests and lint pass on Windows and Linux.
