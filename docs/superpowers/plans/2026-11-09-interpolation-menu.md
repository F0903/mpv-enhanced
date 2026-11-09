# Interpolation Methods, Menu and Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Choose interpolation methods in the installer, manage them with target overrides from an MPV Enhanced uosc menu that applies live, and suggest targets with a local benchmark.

**Architecture:** `manifest.toml` gains a `methods` table and the installer writes one generated JSON file describing what's installed. A directory script in `portable_config/scripts/mpv_enhanced/` holds pure Lua modules (matching, edits, menu) plus thin mpv glue, and passes the resolved choice to `interpolation.vpy` through the filter's `user-data`. A Python benchmark drives the same filter headlessly and writes targets to the settings JSON.

**Tech Stack:** Python 3.12 stdlib, uv, mpv 0.42 Lua, uosc script messages, VapourSynth; `lupa` as a dev-only dependency to test Lua from `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-09-interpolation-menu-design.md`

## Global Constraints

- Tests: `uv run python -m unittest discover -s tests`. Lint: `uv run ruff check .` and `uv run ruff format --check .`.
- Work on branch `v3`. End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push unless the owner asks.
- Tests must never touch the real user PATH, `~/.config`, `/etc`, or show a window. mpv runs use `--vo=null --ao=null`.
- No benchmark figures in the repository, including tests and docs.
- `portable_config/scripts/uosc/` is not modified.
- Single sources of truth: option lists and methods in `manifest.toml`; matching in `targets.lua`.
- Method id `none` means "don't interpolate this" and is never installed or written to the install info.
- Quality order: RTX High, RTX Medium, RIFE 4.25 TensorRT, RIFE 4.25-lite TensorRT, RIFE 4.25, RIFE 4.25-lite, RTX Low.
- Benchmark usable threshold: 1.1× real time. Suggested multiplier: 2. Default FPS cutoff: 60.

## Review Focus

- A source with no usable frame rate, such as cover art, a still image, or unknown `container-fps`: plays with no filter and no error (test in Task 5, image check in Task 7).
- Portrait video such as 1080×1920: fits a 1080p limit rather than jumping to 4K (test in Task 5).
- A hand-edited or malformed `mpv_enhanced.json`: the menu uses defaults with a warning, and the installer leaves the file untouched (tests in Tasks 6 and 8).
- Opening the menu with nothing playing: no status row, and Apply saves without touching a filter (test in Task 6).
- An install path containing spaces, parentheses or non-ASCII: the filter is reached by a relative path from `cwd`, never through mpv option syntax (test in Task 10).

---

### Task 1: Methods in the manifest

**Files:**
- Modify: `manifest.toml`, `src/mpv_enhanced_installer/manifest.py`
- Test: `tests/test_manifest.py`, `tests/test_installer.py`

**Interfaces:**
- Produces in `manifest.py`: frozen dataclasses `Resolution(label: str, width: int, height: int)`; `InterpolationOptions(resolutions: tuple[Resolution, ...], frame_rates: tuple[int, ...], multipliers: tuple[int, ...], fps_cutoffs: tuple[int, ...], default_fps_cutoff: int)`; `MethodChoice(id: str, title: str, rank: int, settings: Mapping[str, object])`; `Method(name, description, components, extras, platforms, extends: str | None, choices, preselect: bool)`.
- `Manifest.methods: Mapping[str, Method]`, `Manifest.interpolation: InterpolationOptions | None`.
- `Manifest.available_methods(platform) -> tuple[Method, ...]`, in manifest order.
- `Manifest.method_choices(names: Sequence[str]) -> tuple[MethodChoice, ...]`, deduplicated by id, sorted by rank, including choices inherited through `extends`.
- `Manifest.resolve_profile(name, platform=None, *, methods=(), optional=())`; `ResolvedProfile` gains `methods` and `choices`, loses `settings`. Unknown method raises `ManifestError`; a method not offered on the platform raises `UnsupportedPlatformError`.
- Removes: `Profile.extras`, `Profile.settings`, `Component.config_key`.

- [ ] **Step 1: Commit the pending pin and design first**

```bash
git add manifest.toml THIRD_PARTY_NOTICES.md docs/superpowers/specs docs/superpowers/plans
git commit -m "docs: design interpolation methods, menu and benchmark; pin mpvfin 0.1.0"
```

- [ ] **Step 2: Rewrite the real-manifest tests against the new shape**

In `tests/test_manifest.py`, replace the `rtx-high`, `rife-cuda` and `rife-tensorrt` profile tests with:

```python
def test_core_is_the_only_profile_and_the_default(self):
    manifest = Manifest.load(ROOT / "manifest.toml")
    self.assertEqual(list(manifest.profiles), ["core"])
    self.assertEqual(manifest.default_profile, "core")


def test_rtx_method_adds_its_components_and_extra(self):
    resolved = load().resolve_profile("core", WINDOWS, methods=("rtx",))
    self.assertEqual(
        [c.name for c in resolved.components],
        ["mpv", "portable-config", "nvidia-rtx-gpu", "rife-runtime"],
    )
    self.assertEqual(resolved.extras, ("rtx",))
    self.assertEqual(
        [c.id for c in resolved.choices], ["rtx-high", "rtx-medium", "rtx-low"]
    )


def test_tensorrt_method_inherits_rife_and_adds_its_choices(self):
    resolved = load().resolve_profile("core", WINDOWS, methods=("rife-tensorrt",))
    self.assertIn("rife-model-full", [c.name for c in resolved.components])
    self.assertEqual(resolved.extras, ("tensorrt",))
    self.assertEqual(
        [c.id for c in resolved.choices],
        [
            "rife-4.25-tensorrt",
            "rife-4.25-lite-tensorrt",
            "rife-4.25",
            "rife-4.25-lite",
        ],
    )


def test_method_choices_follow_the_quality_order(self):
    ids = [c.id for c in load().method_choices(("rtx", "rife-tensorrt"))]
    self.assertEqual(
        ids,
        [
            "rtx-high",
            "rtx-medium",
            "rife-4.25-tensorrt",
            "rife-4.25-lite-tensorrt",
            "rife-4.25",
            "rife-4.25-lite",
            "rtx-low",
        ],
    )


def test_methods_reject_unknown_names_and_platforms(self):
    with self.assertRaises(ManifestError):
        load().resolve_profile("core", WINDOWS, methods=("nope",))
    with self.assertRaises(UnsupportedPlatformError):
        load().resolve_profile("core", LINUX, methods=("rtx",))


def test_only_tensorrt_is_not_preselected(self):
    self.assertEqual(
        [m.name for m in load().methods.values() if not m.preselect], ["rife-tensorrt"]
    )


def test_option_lists_are_in_the_manifest(self):
    options = load().interpolation
    self.assertEqual(
        [r.label for r in options.resolutions], ["720p", "1080p", "1440p", "4K", "8K"]
    )
    self.assertEqual(
        (options.resolutions[3].width, options.resolutions[3].height), (4096, 2160)
    )
    self.assertEqual(options.frame_rates, (24, 30, 48, 60, 120))
    self.assertEqual(options.multipliers, (2, 3, 4))
    self.assertEqual(options.fps_cutoffs, (30, 48, 60, 90, 120))
    self.assertEqual(options.default_fps_cutoff, 60)
```

Define `load`, `WINDOWS` and `LINUX` as module helpers. Keep the model-path, macOS and Linux `core` tests, changing `rife-cuda` to `methods=("rife",)`. In `tests/test_installer.py`, remove `extras` from the `local_manifest` fixture's `rife-tensorrt` profile and delete the `_write_managed_profile` test (replaced in Task 2).

- [ ] **Step 3: Run to verify failure**

Run: `uv run python -m unittest tests.test_manifest`
Expected: FAIL, with `TypeError` on the `methods` keyword.

- [ ] **Step 4: Rewrite `manifest.toml` and implement the dataclasses, parsing and resolution**

Remove all profiles except `core`, set `default_profile = "core"`, drop `config_key` from `rife-model`. Add:

```toml
[interpolation]
resolutions = [
    { label = "720p", width = 1280, height = 720 },
    { label = "1080p", width = 1920, height = 1080 },
    { label = "1440p", width = 2560, height = 1440 },
    { label = "4K", width = 4096, height = 2160 },
    { label = "8K", width = 8192, height = 4320 },
]
frame_rates = [24, 30, 48, 60, 120]
multipliers = [2, 3, 4]
fps_cutoffs = [30, 48, 60, 90, 120]
default_fps_cutoff = 60
```

`[methods.rtx]` has description "RTX Video Frame Generation in High, Medium and Low; needs an RTX 40 series or newer", components `["nvidia-rtx-gpu", "rife-runtime"]`, extras `["rtx"]`, `os.windows.arch = ["x86_64"]`, and choices (id, title, rank, settings) `rtx-high`/"RTX High"/1/`{interpolator="rtx", rtx_mode="HIGH"}`, `rtx-medium`/2, `rtx-low`/7.

`[methods.rife]` has description "RIFE with the 4.25-lite and 4.25 models; needs an NVIDIA GPU", components `["system-mpv-vapoursynth", "nvidia-gpu", "rife-runtime", "rife-model", "rife-model-full"]`, windows and linux x86_64, and choices `rife-4.25`/"RIFE 4.25"/5/`{interpolator="rife", rife_model="4.25", tensorrt=false}`, `rife-4.25-lite`/"RIFE 4.25-lite"/6/`rife_model="4.25.lite"`.

`[methods.rife-tensorrt]` has `extends = "rife"`, description "Faster RIFE using NVIDIA TensorRT; engines are compiled on first use", extras `["tensorrt"]`, `preselect = false`, and choices `rife-4.25-tensorrt`/"RIFE 4.25 TensorRT"/3 and `rife-4.25-lite-tensorrt`/"RIFE 4.25-lite TensorRT"/4, with `tensorrt=true`.

`extends` is one level, as for components. A child inherits components, extras, platforms and choices, and adds its own.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run python -m unittest tests.test_manifest`
Expected: PASS.

- [ ] **Step 6: Commit** with message `feat(manifest): describe interpolation methods separately from profiles`. Other test modules may fail until Tasks 2 and 3 update the callers of removed fields; each task's named test modules must pass.

### Task 2: Install the ticked methods and describe them for the player

**Files:**
- Modify: `src/mpv_enhanced_installer/installer.py`, `receipt.py`, `manager.py`, `doctor.py`, `portable_config/rife_config.py`, `.gitignore`
- Test: `tests/test_installer.py`, `tests/test_doctor.py`

**Interfaces:**
- Consumes: `resolve_profile(..., methods=, optional=)`, `ResolvedProfile.choices`.
- Produces: `InstallationReceipt.methods: tuple[str, ...] = ()`, written as `methods = [...]`, loaded with `.get("methods", [])`; `create(..., methods=())`.
- `Layout.methods(version) -> tuple[str, ...]`.
- `Installer.install(profile, dir, *, offline=False, carry_from=None, methods=(), optional=(), answers=None)`.
- Constant in `installer.py`: `INSTALL_INFO = "portable_config/script-opts/mpv_enhanced_install.json"`, JSON keys `choices` (list of `{id, title, settings}` in rank order), `options` (the `InterpolationOptions` fields, with resolutions as objects) and `benchmark_command` (list of strings).

- [ ] **Step 1: Write the failing tests**

```python
def test_install_info_lists_choices_options_and_benchmark_command(self):
    # Installer on a manifest with a method "m" with choices a(rank 2), b(rank 1)
    result = installer.install("core", target, methods=("m",))
    info = json.loads((target / INSTALL_INFO).read_text("utf-8"))
    self.assertEqual([c["id"] for c in info["choices"]], ["b", "a"])
    self.assertEqual(
        info["benchmark_command"][1:3], ["-m", "mpv_enhanced_installer.cli"]
    )
    self.assertIn(str(target), info["benchmark_command"])
    self.assertEqual(result.receipt.methods, ("m",))


def test_install_without_methods_writes_empty_choices(self): ...  # choices == []


def test_settings_file_is_preserved(self):
    self.assertIn(
        "portable_config/script-opts/mpv_enhanced.json", DEFAULT_PRESERVED_PATHS
    )
```

In `tests/test_doctor.py` add `test_installed_methods_are_expected_in_the_receipt`: a receipt with `methods=("m",)` and the method's components gives `receipt:components` ok.

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_installer tests.test_doctor`
Expected: FAIL on the `methods` keyword.

- [ ] **Step 3: Implement**

Replace `_write_managed_profile` with `_write_install_info(staging, target, profile)`. `benchmark_command` is `[sys.executable, "-m", "mpv_enhanced_installer.cli", "--manifest", str(self.source_root / "manifest.toml"), "benchmark", "--install-dir", str(target)]`. Create `script-opts/` if missing. Skip the file when the staged tree has no `portable_config`. Add `if __name__ == "__main__": raise SystemExit(main())` to `cli.py`. Add `portable_config/script-opts/mpv_enhanced.json` to `DEFAULT_PRESERVED_PATHS`. In `doctor.py`, resolve with `methods=receipt.methods`. In `rife_config.py` delete the `rife_profile` import block, and in `.gitignore` replace `portable_config/rife_profile.py` with `portable_config/script-opts/mpv_enhanced*.json`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_installer tests.test_doctor`
Expected: PASS.

- [ ] **Step 5: Commit** with message `feat(installer): install chosen methods and describe them for the player`.

### Task 3: Methods screen and shared tick screen

**Files:**
- Modify: `src/mpv_enhanced_installer/cli.py`
- Test: `tests/test_cli.py`, `tests/test_manager.py`

**Interfaces:**
- Consumes: `Manifest.available_methods`, `Layout.methods`, `Installer.install(methods=)`.
- Produces: `_tick_screen(title: str, rows: Sequence[TickRow], chosen: set[str], stdin, stdout) -> set[str]` with `TickRow(name, description, unavailable: str | None)`; `_choose_methods(args, manifest, platform, layout, stdin, stdout) -> tuple[str, ...]`; `--method NAME` (repeatable, `dest="methods"`) on `install` and `profile`.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_runnable_method_starts_ticked(self):  # fixture with rtx, rife
    chosen, output = self.choose_methods("\n", unavailable={"rtx": "needs RTX 40"})
    self.assertEqual(chosen, ("rife",))
    self.assertIn("  -  rtx — unavailable: needs RTX 40", output)
    self.assertIn("  1. [x] rife — ", output)


def test_tensorrt_is_never_preticked(self): ...  # only rife-tensorrt runnable: ()
def test_numbers_toggle_methods(self): ...  # "1\n" unticks the pre-ticked one
def test_unattended_fresh_install_gets_no_methods(self): ...
def test_existing_install_keeps_its_methods_without_asking(self): ...  # update path
def test_profile_command_shows_screen_with_installed_marked(self): ...
def test_method_flag_replaces_selection(self): ...
```

Convert every `_install_args("rife-tensorrt", ...)` in `test_cli.py` to `_install_args("core", ...)` plus `--method rife-tensorrt`, and add one extra leading `\n` to terminal inputs for the new screen. `mpve` flows that use other profile names in `test_manager.py` become `core`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_cli tests.test_manager`
Expected: FAIL on missing `_choose_methods`.

- [ ] **Step 3: Implement**

Extract `_tick_screen` from `_choose_optional`, keeping its output format and errors. Rows use `— unavailable: <reason>` for both screens. Title is `Choose interpolation methods:`. A method's reason comes from a `_unavailable_method_reason` built like `_unavailable_reason`, resolving `core` with that method. Screen order in `main`: profile, GPU check on resolved methods, methods, optional, setup, licences. `_require_nvidia_license_acceptance` takes the chosen methods. `_list_profiles` also prints `name\tmethod\tdescription`. The rule for when to show or keep matches `_choose_optional`.

- [ ] **Step 4: Run all tests**

Run: `uv run python -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 5: Commit** with message `feat(cli): add the interpolation methods screen`.

### Task 4: Filter takes its choice from user-data

**Files:**
- Create: `portable_config/vs_script/choice.py`
- Delete: `portable_config/vs_script/target_fps_mode.py`
- Modify: `portable_config/interpolation.vpy`, `portable_config/rife_config.py`, `portable_config/rife_config_local.py.example`, `portable_config/mpv.conf`
- Test: `tests/test_filter_choice.py`

**Interfaces:**
- Produces: `parse_user_data(text: str) -> tuple[str, Fraction]`, with format `<choice-id>/<multiplier>` and `ValueError` on anything else; `choice_settings(info_path: Path, choice_id: str) -> Mapping[str, object]`, `KeyError` if unknown. `choice.py` must not import vapoursynth.

- [ ] **Step 1: Write the failing test**, adding `portable_config` to `sys.path`

```python
def test_parses_id_and_multiplier(self):
    self.assertEqual(parse_user_data("rtx-high/2"), ("rtx-high", Fraction(2)))
    self.assertEqual(
        parse_user_data("rife-4.25-lite/3"), ("rife-4.25-lite", Fraction(3))
    )


def test_rejects_malformed_data(self):
    for text in ("", "rtx-high", "rtx-high/0", "rtx-high/x", "/2"):
        with self.assertRaises(ValueError):
            parse_user_data(text)


def test_reads_settings_for_a_choice(self):  # JSON with one choice
    self.assertEqual(choice_settings(path, "a")["rtx_mode"], "HIGH")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_filter_choice`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

In `interpolation.vpy`, parse `user_data` and read `script-opts/mpv_enhanced_install.json` next to `script_dir`. On `ValueError` or `KeyError`, log and output `video_in` unchanged. Pass `settings` and `multiplier` into `interpolate`, replacing `config.target_mode`, `config.rife_model` and `config.tensorrt`. Dispatch on `settings["interpolator"]`, using `settings["rtx_mode"]` with the existing `interpolate_rtx(clip, multiplier, source_fps * multiplier, mode)`. Remove the `disable_fps_threshold` branch. In `rife_config.py` delete `interpolator`, `rtx_mode`, `target_mode`, `disable_fps_threshold`, `tensorrt` and the `TargetFpsMode` import. Keep `tensorrt_*` tuning. Update the `.example` to drop `rtx_mode`, `rife_model` and `tensorrt`. Delete the `[interpolation]` block from `mpv.conf`, keeping `[default]`.

- [ ] **Step 4: Verify the real filter with no window**

Using an existing install with RTX, copy the changed `portable_config` files over a scratch copy, then from its `portable_config` run:
`..\mpv.com --no-config --vo=null --ao=null --frames=4 --vf=vapoursynth=interpolation.vpy:user-data=rtx-high/2 "av://lavfi:testsrc2=s=640x360:r=24"`
Expected: exits 0 with no `Traceback`.

- [ ] **Step 5: Run tests and commit** with message `feat(filter): take the method and multiplier from the filter's user-data`.

### Task 5: Matching in Lua

**Files:**
- Create: `portable_config/scripts/mpv_enhanced/targets.lua`, `tests/lua_runtime.py`, `tests/test_lua_targets.py`
- Modify: `pyproject.toml`, `uv.lock`

**Interfaces:**
- Produces: `tests/lua_runtime.py` with `load(name) -> module` (LuaJIT via `lupa.luajit21`, `package.path` set to the script directory), `to_lua(value)`, `to_py(table)`.
- `targets.lua` returns `{NONE, weight, sort, covers, match, resolve}`. A target is `{max_width, max_height, max_fps, method, multiplier}`.
  - `weight(t) -> number`: `max_width * max_height * max_fps`.
  - `sort(list)`: in place, heaviest first, ties by larger `max_width * max_height`.
  - `covers(t, w, h, fps) -> bool`: compares long edge to long edge and short edge to short edge, with `fps <= max_fps + 0.01`.
  - `match(list, w, h, fps) -> target | nil`: lowest-weight covering target, else the heaviest.
  - `resolve(settings, w, h, fps) -> {method, multiplier, target} | nil`: `nil` means no filter.

- [ ] **Step 1: Add the dev dependency**

Run: `uv add --dev lupa`, then pin it exactly in `pyproject.toml` as `ruff` is pinned, and run `uv lock`.
Expected: `uv lock --check` passes.

- [ ] **Step 2: Write the failing tests**

```python
LIST = [
    t(4096, 2160, 30, "rtx-medium"),
    t(1920, 1080, 60, "rtx-high"),
    t(1920, 1080, 30, "rtx-high", 3),
]


def test_sorted_by_pixels_per_second_heaviest_first(self):
    ...
    # 4K/24 sorts above 1080p/60


def test_tightest_covering_target_wins(self):
    self.assertEqual(match(LIST, 1920, 1080, 23.976)["multiplier"], 3)


def test_file_beyond_every_target_uses_the_highest(self):
    self.assertEqual(match(LIST, 7680, 4320, 24)["method"], "rtx-medium")


def test_file_below_the_weakest_uses_the_weakest(self):
    self.assertEqual(match(LIST, 640, 360, 24)["multiplier"], 3)


def test_dci_and_cropped_video_count_as_4k(self):
    self.assertEqual(match(LIST, 4096, 1716, 24)["method"], "rtx-medium")


def test_portrait_video_fits_its_landscape_limit(self):
    self.assertEqual(match(LIST, 1080, 1920, 24)["multiplier"], 3)


def test_at_or_above_the_cutoff_gets_no_filter(self):
    self.assertIsNone(resolve(settings(cutoff=60), 1920, 1080, 60))


def test_cutoff_zero_means_off(self):
    self.assertIsNotNone(resolve(settings(cutoff=0), 1920, 1080, 120))


def test_unknown_fps_is_not_interpolated(self):
    for fps in (None, 0):
        self.assertIsNone(resolve(settings(), 1920, 1080, fps))


def test_no_targets_uses_the_default(self):
    self.assertEqual(
        resolve(settings(targets=[]), 1920, 1080, 24)["method"], "rtx-high"
    )


def test_none_method_gets_no_filter(self):
    self.assertIsNone(
        resolve(settings(targets=[t(4096, 2160, 30, "none")]), 3840, 2160, 24)
    )
    self.assertIsNone(
        resolve(settings(targets=[], default_method="none"), 1920, 1080, 24)
    )
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run python -m unittest tests.test_lua_targets`
Expected: FAIL because `targets` isn't found.

- [ ] **Step 4: Implement `targets.lua`**. It must not reference `mp`.

- [ ] **Step 5: Run to verify it passes, then commit** with message `feat(menu): match files to target overrides`.

### Task 6: Pure draft edits and menu building

**Files:**
- Create: `portable_config/scripts/mpv_enhanced/draft.lua`, `portable_config/scripts/mpv_enhanced/menu.lua`, `tests/test_lua_menu.py`

**Interfaces:**
- Consumes: `targets.sort`; install info `{choices, options, benchmark_command}`.
- Settings table: `{enabled, default_method, default_multiplier, fps_cutoff, targets}`. Each runtime target also has a stable `id` (`"t1"`, ...), stripped on save.
- `draft.lua` returns:
  - `normalize(raw, info) -> settings, warning | nil`: fills defaults (`enabled=false`, first choice or `none`, ×2, `options.default_fps_cutoff`, no targets), drops malformed targets and those with an unknown method, and returns a warning if anything was dropped or `raw` isn't a table.
  - `copy(settings)`, `serialize(settings) -> table` (no ids), `change_count(saved, edited) -> int`.
  - `apply(edited, action, info) -> error_message | nil`, which mutates and re-sorts.
  - `replace_targets(edited, targets)`.
- Actions are arrays: `{"set","default_method",id}`, `{"set","default_multiplier",n}`, `{"set","fps_cutoff",n}`, `{"add_target"}`, `{"set_target",id,"resolution",label}`, `{"set_target",id,"max_fps",n}`, `{"set_target",id,"method",id}`, `{"set_target",id,"multiplier",n}`, `{"delete_target",id}`, plus main-only `{"toggle"}`, `{"apply"}`, `{"discard"}`, `{"benchmark"}`.
- `menu.lua` returns `build(saved, edited, info, status) -> MenuData` with `type = "mpv-enhanced"`, `title = "MPV Enhanced"`, `callback = {"mpv_enhanced", "menu-event"}`, `keep_open = true`. `status` is `{source, using}` strings or `nil`.

- [ ] **Step 1: Write the failing tests**

```python
def test_normalize_fills_defaults_for_missing_file(
    self,
): ...  # raw=None: defaults, no warning
def test_normalize_warns_and_drops_bad_targets(self):
    settings, warning = normalize(
        {"targets": [{"max_width": "x"}, good_target()]}, INFO
    )
    self.assertEqual(len(to_py(settings["targets"])), 1)
    self.assertIsNotNone(warning)


def test_normalize_survives_a_non_table(self): ...  # raw="oops": defaults + warning
def test_identical_limits_are_refused(self):
    self.assertIn(
        "already", apply(draft_with_two(), ["set_target", "t2", "resolution", "4K"])
    )


def test_edits_resort_the_list_and_keep_ids(self): ...
def test_change_count_counts_one_edit_as_one(self):
    self.assertEqual(change_count(saved, after_one_method_edit), 1)


def test_serialize_strips_ids(self): ...
def test_add_target_uses_the_first_free_limits(self): ...
def test_add_target_reports_when_every_combination_is_used(self): ...


def test_menu_has_the_approved_rows(self):
    titles = [i["title"] for i in items(build(...))]
    for title in (
        "Interpolation",
        "Default method",
        "Default multiplier",
        "FPS cutoff",
        "Target overrides",
        "Run benchmark",
        "Apply",
        "Discard changes",
    ):
        self.assertIn(title, titles)


def test_defaults_are_muted_only_while_targets_exist(self): ...
def test_apply_shows_pending_count_and_is_disabled_at_zero(self): ...
def test_method_list_has_installed_choices_and_none(self):
    titles = method_titles(build(...))
    self.assertEqual(titles[-1], "None")
    self.assertEqual(titles[0], "RTX High")


def test_none_greys_out_the_multiplier(self): ...
def test_no_methods_hides_interpolation_rows(self): ...
def test_idle_menu_has_no_status_row(self): ...  # status=None
def test_target_rows_are_sorted_and_mark_the_current_one(self): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_lua_menu`
Expected: FAIL because `draft` isn't found.

- [ ] **Step 3: Implement `draft.lua` and `menu.lua`**

Neither references `mp`. Row titles and hints follow the approved mockup: `Interpolation` hint `On`/`Off`; the note `Only used while there are no target overrides` as a non-selectable muted item; `Target overrides` hint is the count; the current target row title starts with `● `; target titles read `Up to 4K · 30 fps`, hints read `RTX Medium ×2`; a None target's hint is `None`; `Apply` hint is `1 change`/`N changes`; `Add target`, `Delete target`. The FPS cutoff list is `options.fps_cutoffs` plus `Off`, stored as 0. Submenu items use the target's `id` so that uosc keeps its place after a re-sort. Selected list entries set `active = true`. Items not needing the menu closed inherit `keep_open`.

- [ ] **Step 4: Run to verify it passes, then commit** with message `feat(menu): build the MPV Enhanced menu and its edits`.

### Task 7: mpv glue and the uosc entry

**Files:**
- Create: `portable_config/scripts/mpv_enhanced/main.lua`
- Delete: `portable_config/scripts/toggle_interpolation.lua`
- Modify: `portable_config/input.conf`
- Test: `tests/test_input_conf.py`

**Interfaces:**
- Consumes: Tasks 5 and 6; `script-opts/mpv_enhanced.json`; `script-opts/mpv_enhanced_install.json`.
- Produces: script messages `open-menu`, `toggle`, `menu-event` (script name `mpv_enhanced`).

- [ ] **Step 1: Write the failing test**

```python
def test_declares_every_default_uosc_entry_and_ours(self):
    titles = [line.split("#!")[1].strip() for line in lines if "#!" in line]
    for title in (
        "Subtitles",
        "Audio tracks",
        "Stream quality",
        "Playlist",
        "Chapters",
        "Navigation > Next",
        "Navigation > Open file",
        "Utils > Aspect ratio > 16:9",
        "Utils > Open config folder",
        "MPV Enhanced",
        "Quit",
    ):
        self.assertIn(title, titles)


def test_keeps_the_interpolation_hotkey(self):
    self.assertIn("ctrl+shift+r script-message-to mpv_enhanced toggle", text)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_input_conf`
Expected: FAIL.

- [ ] **Step 3: Write `input.conf`**

One `#!` line per item of `create_default_menu_items` (`scripts/uosc/main.lua:305`), in that order, using `#` as the key, and `# script-message-to mpv_enhanced open-menu #! MPV Enhanced` placed before Utils, with `#! ---` separators around it.

- [ ] **Step 4: Implement `main.lua`**

- Load settings with `utils.parse_json`, reading `<file>.tmp` if the main file is missing. Run `draft.normalize` and show the warning with `mp.osd_message`.
- Save: write `<file>.tmp`, then `os.rename`; if that fails, `os.remove` the target and rename again (Windows can't rename over a file).
- `edited` lives in memory for the whole mpv session, so closing the menu keeps pending edits.
- `apply_filter()`: return when nothing is playing. Remove any filter and skip when `current-tracks/video/image` or `albumart` is set. Otherwise call `targets.resolve` with `width`, `height`, `container-fps`. Compute `<id>/<multiplier>`, return if unchanged. Run `vf remove @interpolation` if present **before** `vf add @interpolation:vapoursynth=file=~~/interpolation.vpy:user-data=<id>/<multiplier>`. Show `Interpolation: <title> ×<n>`, or `Interpolation: None`, and on command failure show `Interpolation failed` and clear the remembered value.
- Before a TensorRT choice loads, show `Building TensorRT engine, this can take minutes`.
- `file-loaded` calls `apply_filter`. `toggle` flips and saves `enabled` immediately, independent of pending edits.
- `menu-event` parses the JSON event, dispatches `event.value`, and for draft actions re-sends `update-menu` with `menu.build`. `apply` saves, replaces `saved`, and calls `apply_filter`. `discard` copies `saved`.

- [ ] **Step 5: Verify in real mpv with no window**

In a scratch install with `portable_config` updated, run with `--vo=null --ao=null --idle=no`, a lavfi source, and a throwaway harness script that sends `toggle`, waits 3 s, then prints `vf`.
Expected: printed `vf` contains a filter labelled `interpolation` with the right `user-data`, and exit 0. Repeat with a PNG file as the source: `vf` stays empty and there's no error.

- [ ] **Step 6: Run tests and commit** with message `feat(menu): add the MPV Enhanced menu to uosc`.

### Task 8: Settings from the installer

**Files:**
- Create: `src/mpv_enhanced_installer/settings.py`, `tests/test_settings.py`
- Modify: `src/mpv_enhanced_installer/cli.py`

**Interfaces:**
- Produces: `SETTINGS = "portable_config/script-opts/mpv_enhanced.json"`; `load_settings(app: Path) -> dict | None` (`None` if missing or malformed); `save_settings(app, data)` via `os.replace`; `set_enabled(app, enabled: bool)`; `set_targets(app, targets: list[dict])`; `reconcile_targets(app, installed_ids: Sequence[str]) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reconcile_rewrites_targets_of_removed_methods(self):
    save(
        app,
        {
            "targets": [target("old"), target("none"), target("keep")],
            "default_method": "old",
        },
    )
    reconcile_targets(app, ["keep", "other"])
    self.assertEqual(
        [t["method"] for t in load(app)["targets"]], ["keep", "none", "keep"]
    )
    self.assertEqual(load(app)["default_method"], "keep")


def test_reconcile_leaves_a_malformed_file_alone(self):
    (app / SETTINGS).write_text("{oops")
    reconcile_targets(app, ["keep"])
    self.assertEqual((app / SETTINGS).read_text(), "{oops")


def test_reconcile_with_no_methods_changes_nothing(self): ...
def test_set_enabled_creates_the_file_when_missing(self): ...
def test_set_targets_keeps_other_settings(self): ...


def test_first_install_asks_start_state(
    self,
): ...  # output has "Start with interpolation"
def test_enter_defaults_to_off_without_benchmark(self): ...
def test_enter_defaults_to_on_after_accepted_benchmark(self): ...
def test_unattended_and_updates_do_not_ask(self): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_settings`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Add `_finish_interpolation(args, app, methods, manifest, layout_was_active, stdin, stdout)` in `cli.py`, called after a successful install. It runs `reconcile_targets`, then the benchmark offer (Task 10 fills this in; for now a parameter `offer_benchmark` returning `False`), and on a first interactive install with methods asks `Start with interpolation on or off? [on/OFF]:`, capitalising the default. Unattended first installs write `enabled = false`.

- [ ] **Step 4: Run to verify it passes, then commit** with message `feat(installer): set interpolation's starting state`.

### Task 9: Suggesting targets from measurements

**Files:**
- Create: `src/mpv_enhanced_installer/benchmark.py`, `tests/test_benchmark_suggest.py`

**Interfaces:**
- Consumes: `InterpolationOptions`, `tests/lua_runtime.py`.
- Produces: `USABLE_MARGIN = 1.1`; `SUGGESTED_MULTIPLIER = 2`; `LEVEL_FRAME_RATES = (24, 30)`; `LEVEL_RESOLUTIONS = ("720p", "1080p", "1440p", "4K")`; `suggest_targets(capacities: Mapping[str, float], order: Sequence[str], options: InterpolationOptions) -> list[dict]`. `capacities` are output pixels per second by choice id; `order` is best first. Targets use the settings JSON target shape.

- [ ] **Step 1: Write the failing tests**, using made-up capacities only

```python
def test_picks_the_best_usable_choice_per_level(self): ...
def test_usable_needs_the_margin(self):
    # capacity exactly 1.0x and 1.09x of the 1080p30x2 need: not usable; 1.1x: usable
def test_levels_beyond_the_pc_become_none(self):
    methods = [t["method"] for t in suggest_targets({"a": capacity_for_1080p30()}, ["a"], OPTIONS)]
    self.assertEqual(methods[0], "none")
    self.assertIn("a", methods)
def test_nothing_usable_is_a_single_none_target(self):
    self.assertEqual([t["method"] for t in suggest_targets({}, [], OPTIONS)], ["none"])
def test_uniform_result_merges_to_one_target(self): ...
def test_merged_list_matches_every_level_like_the_full_list(self):
    # For each level, targets.lua's match over the merged list gives the same
    # method as the unmerged assignment for that level.
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_benchmark_suggest`
Expected: FAIL.

- [ ] **Step 3: Implement `suggest_targets`**

A level's need is `(height * 16 // 9) * height * fps * SUGGESTED_MULTIPLIER`, so 4K measures as 3840 wide. A level takes the first id in `order` with `capacity >= need * USABLE_MARGIN`, otherwise `"none"`. Merge lightest first: drop a level when the next-heavier remaining level that covers it has the same method. Return heaviest first.

- [ ] **Step 4: Run to verify it passes, then commit** with message `feat(benchmark): suggest targets from measured capacity`.

### Task 10: Measuring and `mpve benchmark`

**Files:**
- Create: `src/mpv_enhanced_installer/benchmark_probe.lua`, `tests/test_benchmark.py`
- Modify: `src/mpv_enhanced_installer/benchmark.py`, `cli.py`, `downloads.py`

**Interfaces:**
- Consumes: `suggest_targets`, `set_targets`, `load_settings`, `INSTALL_INFO`.
- Produces: `bar(done, total) -> str` in `downloads.py`, used by `progress_line`; `mpv_command(app, choice_id, platform, width, height, frames, probe: Path) -> list[str]`; `measure_choice(app, choice, platform, *, run=subprocess.run) -> float` (0.0 on failure or `timeout=90`); `run_benchmark(app, choices, platform, *, measure=measure_choice, progress=lambda done, total, name: None) -> dict[str, float]`.
- CLI: `benchmark [--install-dir DIR] [--suggest-to FILE] [--progress-file FILE]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_command_is_headless_and_uses_a_relative_filter(self):
    command = mpv_command(Path(r"C:\my (test) dir é\app"), "rtx-high", WINDOWS, 3840, 2160, 96, PROBE)
    self.assertIn("--vo=null", command)
    self.assertIn("--untimed", command)
    self.assertIn("--vf=vapoursynth=interpolation.vpy:user-data=rtx-high/2", command)

def test_measure_runs_inside_portable_config(self): ...      # run's cwd == app/portable_config
def test_measure_reads_the_probe_output(self):
    # stdout "MPVE_BENCH frames=96 seconds=2.0" at 3840x2160 gives 3840*2160*48.0
def test_failed_or_timed_out_measure_scores_zero(self): ...
def test_tensorrt_choices_are_skipped(self): ...             # measure not called; absent from result
def test_progress_counts_every_step(self): ...

def test_existing_targets_ask_and_default_to_no(self): ...
def test_suggest_to_writes_a_file_and_leaves_settings_alone(self): ...
def test_progress_file_is_written(self): ...
def test_benchmark_without_methods_reports_it(self): ...
def test_update_never_offers_the_benchmark(self): ...
def test_first_install_offer_can_be_declined(self): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m unittest tests.test_benchmark`
Expected: FAIL.

- [ ] **Step 3: Implement**

`mpv_command` uses `--no-config --vo=null --ao=null --untimed --frames=<warmup+n>`, the `--script=<probe>` path, and `av://lavfi:testsrc2=s=<w>x<h>:r=24,format=yuv420p10le`. It uses `mpv.com` on Windows and `mpv` on Linux, with `cwd = app/portable_config`. On Linux, set `VSSCRIPT_PATH` to the install's `libvsscript.so` in `env`. RTX choices measure at 3840×2160; others at 1920×1080. The probe observes `estimated-frame-number`, starts its clock after 48 frames, and on `end-file` prints `MPVE_BENCH frames=<n> seconds=<s>`. Capacity is `width * height * n / s`. The package data file loads through `importlib.resources`. Skip choices whose settings have `tensorrt` true, and tell the user so.

Briefing text, printed before running:

```text
The benchmark tests each interpolation method on this PC to suggest the best
settings for different videos. It takes a few minutes at most, and no window
opens. Close games and other GPU-heavy programs first, or the results will be low.
```

`--suggest-to` writes the targets as JSON without touching settings. Otherwise, when targets exist, ask `Replace your existing targets? [y/N]:`. Fill in `offer_benchmark` from Task 8 with `Run the benchmark now? [Y/n]:`, on a first install or `mpve profile` only.

- [ ] **Step 4: Run tests, then verify for real**

Run in a scratch install: `mpve benchmark --install-dir <scratch app>`.
Expected: briefing, bar, no window, finishes in a few minutes, and `mpv_enhanced.json` has targets.

- [ ] **Step 5: Commit** with message `feat(benchmark): measure methods and add mpve benchmark`.

### Task 11: Run benchmark from the menu

**Files:**
- Modify: `portable_config/scripts/mpv_enhanced/main.lua`

**Interfaces:**
- Consumes: `benchmark_command`, `--suggest-to`, `--progress-file`, `draft.replace_targets`.

- [ ] **Step 1: Implement the `benchmark` action**

Remember the pause state and pause. Remove the `@interpolation` filter so the benchmark has the GPU memory. Start `mp.command_native_async` with `subprocess`, `benchmark_command` plus the two file flags, using temporary files in `script-opts/`. A periodic timer reads the progress file and shows `Benchmark: <done>/<total>` on the OSD. On completion, read the suggestions, call `draft.replace_targets`, update the menu, delete the temporary files, restore the pause state and re-apply the filter. On failure, show `Benchmark failed` and leave the edits alone. Ignore the action if one is already running.

- [ ] **Step 2: Verify in real mpv with no window**

Using the Task 7 harness, send the `benchmark` event, wait for completion, and print the pending change count.
Expected: count above 0, the saved file unchanged until Apply.

- [ ] **Step 3: Commit** with message `feat(menu): run the benchmark from the menu`.

### Task 12: Documentation and final check

**Files:**
- Modify: `README.md`, `TODO.md`, `.github/workflows/package_and_release.yml` only if a step names removed profiles

- [ ] **Step 1: Update the docs**

`README.md`: platforms table and profile section describe `core` plus methods; add the methods screen, `--method`, the menu, target overrides, `mpve benchmark`, and that personal settings live in `mpv_enhanced.json`. Remove `rife_model = "4.25"` advice. Remove both finished items from `TODO.md`, and delete the file if nothing is left.

- [ ] **Step 2: Run everything**

Run: `uv run python -m unittest discover -s tests`, `uv run ruff check .`, `uv run ruff format --check .`, `uv lock --check`, `uv lock --check --project runtime/rife`.
Expected: all pass. Run the unit tests in WSL too, with `UV_PROJECT_ENVIRONMENT` pointing outside the project.

- [ ] **Step 3: Owner's acceptance on their PC**

Fresh interactive install: tick methods, accept the benchmark, then during playback change a target's method, press Apply, and confirm the picture changes without restarting and interpolation stays on.

- [ ] **Step 4: Commit** with message `docs: describe interpolation methods, the menu and the benchmark`.
