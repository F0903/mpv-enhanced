import subprocess
import unittest
from pathlib import Path

from mpv_enhanced.downloads import bar
from mpv_enhanced.interpolation.benchmark import (
    measure_choice,
    mpv_command,
    run_benchmark,
)
from tests.support import LINUX, WINDOWS

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


if __name__ == "__main__":
    unittest.main()
