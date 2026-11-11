import json
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced.benchmark import (
    measure_choice,
    mpv_command,
    run_benchmark,
)
from mpv_enhanced.cli import main
from mpv_enhanced.downloads import bar
from mpv_enhanced.install.installer import INSTALL_INFO
from mpv_enhanced.platforms import PlatformInfo
from mpv_enhanced.settings import SETTINGS, load_settings, save_settings

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = PlatformInfo("windows", "x86_64")
LINUX = PlatformInfo("linux", "x86_64")
PROBE = Path("probe.lua")
RTX = {
    "id": "rtx-high",
    "title": "RTX High",
    "settings": {"interpolator": "rtx", "rtx_mode": "HIGH"},
}
RIFE = {
    "id": "rife-4.25",
    "title": "RIFE 4.25",
    "settings": {"interpolator": "rife", "rife_model": "4.25", "tensorrt": False},
}
TENSORRT = {
    "id": "rife-4.25-tensorrt",
    "title": "RIFE 4.25 TensorRT",
    "settings": {"interpolator": "rife", "rife_model": "4.25", "tensorrt": True},
}


class Terminal(StringIO):
    def isatty(self) -> bool:
        return True


class CommandTests(unittest.TestCase):
    def test_command_is_headless_and_uses_a_relative_filter(self) -> None:
        app = Path(r"C:\my (test) dir é\app")
        command = mpv_command(app, "rtx-high", WINDOWS, 3840, 2160, 96, PROBE)
        self.assertIn("--vo=null", command)
        self.assertIn("--ao=null", command)
        self.assertIn("--untimed", command)
        self.assertIn("--no-config", command)
        self.assertIn(
            "--vf=vapoursynth=interpolation.vpy:user-data=rtx-high/2", command
        )
        self.assertIn(
            "av://lavfi:testsrc2=s=3840x2160:r=24,format=yuv420p10le", command
        )
        self.assertFalse(any(str(app) in item for item in command[1:]))

    def test_uses_this_installs_mpv_on_windows_and_the_system_mpv_on_linux(
        self,
    ) -> None:
        app = Path("app")
        self.assertEqual(
            Path(mpv_command(app, "a", WINDOWS, 1, 1, 1, PROBE)[0]).parent, app
        )
        self.assertEqual(mpv_command(app, "a", LINUX, 1, 1, 1, PROBE)[0], "mpv")


class MeasureTests(unittest.TestCase):
    def measure(self, choice=RTX, stdout="", error=None, returncode=0):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if error:
                raise error
            return subprocess.CompletedProcess(command, returncode, stdout=stdout)

        return measure_choice(Path("app"), choice, WINDOWS, run=run), calls

    def test_measure_runs_inside_portable_config(self) -> None:
        _, calls = self.measure(stdout="MPVE_BENCH frames=96 seconds=2.0")
        self.assertEqual(Path(calls[0][1]["cwd"]), Path("app") / "portable_config")

    def test_measure_reads_the_probe_output(self) -> None:
        capacity, _ = self.measure(stdout="x\nMPVE_BENCH frames=96 seconds=2.0\n")
        self.assertEqual(capacity, 3840 * 2160 * 48.0)

    def test_rife_is_measured_at_1080p(self) -> None:
        capacity, calls = self.measure(RIFE, stdout="MPVE_BENCH frames=96 seconds=2.0")
        self.assertEqual(capacity, 1920 * 1080 * 48.0)
        self.assertTrue(any("s=1920x1080" in item for item in calls[0][0]))

    def test_failed_or_timed_out_measure_scores_zero(self) -> None:
        self.assertEqual(self.measure(stdout="no result")[0], 0.0)
        self.assertEqual(
            self.measure(stdout="MPVE_BENCH frames=96 seconds=2.0", returncode=1)[0],
            0.0,
        )
        self.assertEqual(self.measure(error=OSError("missing"))[0], 0.0)
        self.assertEqual(
            self.measure(error=subprocess.TimeoutExpired("mpv", 90))[0], 0.0
        )

    def test_a_zero_length_measurement_scores_zero(self) -> None:
        self.assertEqual(self.measure(stdout="MPVE_BENCH frames=0 seconds=0")[0], 0.0)


class RunBenchmarkTests(unittest.TestCase):
    def test_tensorrt_choices_are_skipped(self) -> None:
        measured = []
        result = run_benchmark(
            Path("app"),
            [RTX, TENSORRT],
            WINDOWS,
            measure=lambda _a, choice, _p: measured.append(choice["id"]) or 5.0,
        )
        self.assertEqual(measured, ["rtx-high"])
        self.assertEqual(result, {"rtx-high": 5.0})

    def test_progress_counts_every_step(self) -> None:
        steps = []
        run_benchmark(
            Path("app"),
            [RTX, RIFE],
            WINDOWS,
            measure=lambda *_: 1.0,
            progress=lambda done, total, _name: steps.append((done, total)),
        )
        self.assertEqual(steps, [(0, 2), (1, 2), (2, 2)])

    def test_bar_shows_how_far_along_it_is(self) -> None:
        self.assertEqual(bar(1, 2), f"[{'#' * 12}{'-' * 12}]")


class CommandLineTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / "app"
        self.write_info([RTX])
        patcher = patch(
            "mpv_enhanced.cli.run_benchmark",
            side_effect=self.fake_benchmark,
        )
        self.benchmark = patcher.start()
        self.addCleanup(patcher.stop)

    def write_info(self, choices) -> None:
        path = self.app / INSTALL_INFO
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"choices": choices}), encoding="utf-8")

    @staticmethod
    def fake_benchmark(_app, choices, _platform, progress=lambda *_: None):
        progress(0, 1, "x")
        progress(1, 1, "x")
        return {choice["id"]: 1e12 for choice in choices}

    def run_cli(self, *extra, stdin=None):
        output = StringIO()
        errors = StringIO()
        code = main(
            [
                "benchmark",
                "--manifest",
                str(ROOT / "manifest.toml"),
                "--platform",
                "windows-x86_64",
                "--install-dir",
                str(self.app),
                *extra,
            ],
            stdin=stdin or Terminal(""),
            stdout=output,
            stderr=errors,
        )
        return code, output.getvalue(), errors.getvalue()

    def test_briefs_then_writes_targets(self) -> None:
        code, output, errors = self.run_cli(stdin=Terminal(""))
        self.assertEqual(code, 0, errors)
        self.assertIn("It takes a few minutes", output)
        self.assertIn("Close games", output)
        self.assertEqual(load_settings(self.app)["targets"][-1]["method"], "rtx-high")

    def test_existing_targets_ask_and_default_to_no(self) -> None:
        save_settings(self.app, {"targets": [{"method": "mine"}]})
        code, output, _ = self.run_cli(stdin=Terminal("\n"))
        self.assertEqual(code, 0)
        self.assertIn("Replace your existing targets? [y/N]", output)
        self.benchmark.assert_not_called()
        self.assertEqual(load_settings(self.app)["targets"], [{"method": "mine"}])

    def test_existing_targets_can_be_replaced(self) -> None:
        save_settings(self.app, {"targets": [{"method": "mine"}]})
        self.run_cli(stdin=Terminal("y\n"))
        self.assertEqual(load_settings(self.app)["targets"][-1]["method"], "rtx-high")

    def test_suggest_to_writes_a_file_and_leaves_settings_alone(self) -> None:
        save_settings(self.app, {"targets": [{"method": "mine"}]})
        out = self.root / "suggested.json"
        code, _, errors = self.run_cli("--suggest-to", str(out), stdin=StringIO())
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(out.read_text("utf-8"))[-1]["method"], "rtx-high")
        self.assertEqual(load_settings(self.app)["targets"], [{"method": "mine"}])

    def test_progress_file_is_written(self) -> None:
        progress = self.root / "progress.txt"
        self.run_cli(
            "--suggest-to",
            str(self.root / "s.json"),
            "--progress-file",
            str(progress),
            stdin=StringIO(),
        )
        self.assertEqual(progress.read_text("utf-8").strip(), "1 1")

    def test_a_benchmark_that_measured_nothing_saves_nothing(self) -> None:
        self.benchmark.side_effect = lambda *_a, **_k: {"rtx-high": 0.0}
        save_settings(self.app, {"enabled": True})
        code, _, errors = self.run_cli(stdin=Terminal(""))
        self.assertEqual(code, 2)
        self.assertIn("could not measure", errors)
        self.assertNotIn("targets", load_settings(self.app))

    def test_a_method_that_could_not_be_measured_is_named(self) -> None:
        self.write_info([RTX, RIFE])
        self.benchmark.side_effect = lambda *_a, **_k: {
            "rtx-high": 0.0,
            "rife-4.25": 1e12,
        }
        code, output, errors = self.run_cli(stdin=Terminal(""))
        self.assertEqual(code, 0, errors)
        self.assertIn("Could not measure RTX High", output)
        self.assertEqual(load_settings(self.app)["targets"][-1]["method"], "rife-4.25")

    def test_a_malformed_settings_file_is_not_overwritten(self) -> None:
        path = self.app / SETTINGS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{oops", encoding="utf-8")
        code, _, errors = self.run_cli(stdin=Terminal(""))
        self.assertEqual(code, 2)
        self.assertIn(str(path), errors)
        self.assertEqual(path.read_text(encoding="utf-8"), "{oops")
        self.benchmark.assert_not_called()

    def test_says_so_when_the_pc_cannot_keep_up_with_anything(self) -> None:
        self.benchmark.side_effect = lambda *_a, **_k: {"rtx-high": 1.0}
        code, output, errors = self.run_cli(stdin=Terminal(""))
        self.assertEqual(code, 0, errors)
        self.assertIn("couldn't keep up", output)
        self.assertEqual(
            [item["method"] for item in load_settings(self.app)["targets"]], ["none"]
        )

    def test_benchmark_without_methods_reports_it(self) -> None:
        self.write_info([])
        code, _, errors = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("no interpolation methods", errors)

    def test_benchmark_needs_a_terminal_unless_suggesting(self) -> None:
        code, _, errors = self.run_cli(stdin=StringIO())
        self.assertEqual(code, 2)
        self.assertIn("terminal", errors)


class InstallOfferTests(CommandLineTests):
    def finish(self, typed, **fields):
        from types import SimpleNamespace

        from mpv_enhanced.cli import _finish_interpolation
        from mpv_enhanced.manifest import Manifest

        output = StringIO()
        with patch("mpv_enhanced.cli.PlatformInfo.detect", return_value=WINDOWS):
            _finish_interpolation(
                SimpleNamespace(**{"yes": False, "command": "install", **fields}),
                self.app,
                Manifest.load(ROOT / "manifest.toml"),
                ("rtx",),
                Terminal(typed),
                output,
            )
        return output.getvalue()

    def test_first_install_offer_can_be_declined(self) -> None:
        output = self.finish("n\n\n")
        self.assertIn("Run the benchmark now? [Y/n]", output)
        self.benchmark.assert_not_called()
        self.assertFalse(load_settings(self.app)["enabled"])

    def test_accepting_the_offer_writes_targets_and_defaults_on(self) -> None:
        output = self.finish("\n\n")
        self.assertIn("[ON/off]", output)
        settings = load_settings(self.app)
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["targets"][-1]["method"], "rtx-high")

    def test_a_failed_benchmark_does_not_end_the_install_flow(self) -> None:
        self.benchmark.side_effect = lambda *_a, **_k: {"rtx-high": 0.0}
        output = self.finish("\n\n")
        self.assertIn("could not measure", output)
        self.assertIn("Start with interpolation on or off?", output)
        self.assertIn("[on/OFF]", output)
        self.assertFalse(load_settings(self.app)["enabled"])

    def test_update_never_offers_the_benchmark(self) -> None:
        save_settings(self.app, {"enabled": True})
        output = self.finish("")
        self.assertNotIn("benchmark", output)
        self.benchmark.assert_not_called()

    def test_profile_offer_warns_before_replacing_targets(self) -> None:
        save_settings(self.app, {"targets": [{"method": "mine"}]})
        output = self.finish("\n\n", command="profile")
        self.assertIn("Replace your existing targets? [y/N]", output)
        self.benchmark.assert_not_called()


if __name__ == "__main__":
    unittest.main()
