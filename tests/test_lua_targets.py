import unittest

from tests.lua_runtime import load, to_lua, to_py


def t(width, height, fps, method, multiplier=2):
    return {
        "max_width": width,
        "max_height": height,
        "max_fps": fps,
        "method": method,
        "multiplier": multiplier,
    }


LIST = [
    t(4096, 2160, 30, "rtx-medium"),
    t(1920, 1080, 60, "rtx-high"),
    t(1920, 1080, 30, "rtx-high", 3),
]


def settings(cutoff=60, targets=None, default_method="rtx-high"):
    return to_lua(
        {
            "fps_cutoff": cutoff,
            "default_method": default_method,
            "default_multiplier": 2,
            "targets": LIST if targets is None else targets,
        }
    )


class MatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.targets = load("targets")

    def match(self, width, height, fps):
        return to_py(self.targets.match(to_lua(LIST), width, height, fps))

    def resolve(self, value, width, height, fps):
        return to_py(self.targets.resolve(value, width, height, fps))

    def test_sorted_by_pixels_per_second_heaviest_first(self) -> None:
        items = to_lua([t(1920, 1080, 60, "a"), t(4096, 2160, 24, "b")])
        self.targets.sort(items)
        self.assertEqual([item["method"] for item in to_py(items)], ["b", "a"])

    def test_equal_weights_put_the_larger_resolution_first(self) -> None:
        items = to_lua([t(1280, 720, 60, "small"), t(2560, 1440, 15, "large")])
        self.targets.sort(items)
        self.assertEqual([item["method"] for item in to_py(items)], ["large", "small"])

    def test_tightest_covering_target_wins(self) -> None:
        self.assertEqual(self.match(1920, 1080, 23.976)["multiplier"], 3)

    def test_file_beyond_every_target_uses_the_highest(self) -> None:
        self.assertEqual(self.match(7680, 4320, 24)["method"], "rtx-medium")

    def test_file_below_the_weakest_uses_the_weakest(self) -> None:
        self.assertEqual(self.match(640, 360, 24)["multiplier"], 3)

    def test_dci_and_cropped_video_count_as_4k(self) -> None:
        self.assertEqual(self.match(4096, 1716, 24)["method"], "rtx-medium")

    def test_portrait_video_fits_its_landscape_limit(self) -> None:
        self.assertEqual(self.match(1080, 1920, 24)["multiplier"], 3)

    def test_frame_rate_tolerates_rounding(self) -> None:
        self.assertEqual(self.match(1920, 1080, 30.004)["multiplier"], 3)

    def test_at_or_above_the_cutoff_gets_no_filter(self) -> None:
        self.assertIsNone(self.resolve(settings(cutoff=60), 1920, 1080, 60))

    def test_just_below_the_cutoff_is_interpolated(self) -> None:
        self.assertIsNotNone(self.resolve(settings(cutoff=60), 1920, 1080, 59.94))

    def test_cutoff_zero_means_off(self) -> None:
        self.assertIsNotNone(self.resolve(settings(cutoff=0), 1920, 1080, 120))

    def test_unknown_fps_is_not_interpolated(self) -> None:
        for fps in (None, 0):
            with self.subTest(fps=fps):
                self.assertIsNone(self.resolve(settings(), 1920, 1080, fps))

    def test_no_targets_uses_the_default(self) -> None:
        resolved = self.resolve(settings(targets=[]), 1920, 1080, 24)
        self.assertEqual(resolved["method"], "rtx-high")
        self.assertEqual(resolved["multiplier"], 2)

    def test_none_method_gets_no_filter(self) -> None:
        none_target = settings(targets=[t(4096, 2160, 30, "none")])
        self.assertIsNone(self.resolve(none_target, 3840, 2160, 24))
        self.assertIsNone(
            self.resolve(settings(targets=[], default_method="none"), 1920, 1080, 24)
        )


if __name__ == "__main__":
    unittest.main()
