import itertools
import unittest
from pathlib import Path

from mpv_enhanced_installer.benchmark import suggest_targets
from mpv_enhanced_installer.manifest import Manifest
from tests.lua_runtime import load, to_lua, to_py

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = Manifest.load(ROOT / "manifest.toml").interpolation
# (long edge of the resolution as measured, height) for each level.
LEVELS = [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)]
FRAME_RATES = (24, 30)


def need(width: int, height: int, fps: int) -> float:
    return width * height * fps * 2


def method_at(targets, width, height, fps):
    match = load("targets").match(to_lua(targets), width, height, fps)
    return to_py(match)["method"]


class SuggestTargetsTests(unittest.TestCase):
    def test_picks_the_best_usable_choice_per_level(self) -> None:
        capacities = {
            "best": need(1920, 1080, 30) * 1.2,
            "ok": need(3840, 2160, 30) * 1.2,
        }
        targets = suggest_targets(capacities, ["best", "ok"], OPTIONS)
        self.assertEqual(method_at(targets, 1920, 1080, 24), "best")
        self.assertEqual(method_at(targets, 3840, 2160, 24), "ok")

    def test_usable_needs_the_margin(self) -> None:
        required = need(1920, 1080, 30)
        below = suggest_targets({"a": required * 1.09}, ["a"], OPTIONS)
        self.assertEqual(method_at(below, 1920, 1080, 30), "none")
        at_margin = suggest_targets({"a": required * 1.1}, ["a"], OPTIONS)
        self.assertEqual(method_at(at_margin, 1920, 1080, 30), "a")

    def test_levels_beyond_the_pc_become_none(self) -> None:
        capacity = need(1920, 1080, 30) * 1.2
        targets = suggest_targets({"a": capacity}, ["a"], OPTIONS)
        methods = [item["method"] for item in targets]
        self.assertEqual(methods[0], "none")
        self.assertIn("a", methods)
        self.assertEqual(method_at(targets, 7680, 4320, 24), "none")

    def test_nothing_usable_is_a_single_none_target(self) -> None:
        self.assertEqual(
            [item["method"] for item in suggest_targets({}, [], OPTIONS)], ["none"]
        )

    def test_uniform_result_merges_to_one_target(self) -> None:
        targets = suggest_targets({"a": 1e12}, ["a"], OPTIONS)
        self.assertEqual(len(targets), 1)
        self.assertEqual(
            targets[0],
            {
                "max_width": 4096,
                "max_height": 2160,
                "max_fps": 30,
                "method": "a",
                "multiplier": 2,
            },
        )

    def test_merged_list_matches_every_level_like_the_full_list(self) -> None:
        points = [
            need(width, height, fps) * factor
            for (width, height), fps, factor in itertools.product(
                LEVELS, FRAME_RATES, (1.05, 1.15)
            )
        ]
        for best, ok in itertools.combinations_with_replacement(sorted(points), 2):
            capacities = {"best": best, "ok": ok}
            targets = suggest_targets(capacities, ["best", "ok"], OPTIONS)
            for (width, height), fps in itertools.product(LEVELS, FRAME_RATES):
                required = need(width, height, fps) * 1.1
                expected = next(
                    (n for n in ("best", "ok") if capacities[n] >= required), "none"
                )
                with self.subTest(capacities=capacities, level=(width, height, fps)):
                    self.assertEqual(method_at(targets, width, height, fps), expected)


if __name__ == "__main__":
    unittest.main()
