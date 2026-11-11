import unittest

from mpv_enhanced.errors import InstallerError
from mpv_enhanced.install.system import check_gpu, check_system_program
from mpv_enhanced.manifest import GpuRequirement, Manifest, SystemProgram
from tests.support import ROOT


class GpuCheckTests(unittest.TestCase):
    REQUIRED = Manifest.load(ROOT / "manifest.toml").components["nvidia-rtx-gpu"].gpu

    def check(self, names: str, tool: str | None = "nvidia-smi") -> str:
        assert isinstance(self.REQUIRED, GpuRequirement)
        return check_gpu(self.REQUIRED, run=lambda _: names, which=lambda _: tool)

    def test_supported_cards_are_accepted(self) -> None:
        for name in (
            "NVIDIA GeForce RTX 4060 Laptop GPU",
            "NVIDIA GeForce RTX 4080",
            "NVIDIA GeForce RTX 5090",
            "NVIDIA RTX 6000 Ada Generation",
            "NVIDIA RTX PRO 6000 Blackwell",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.check(name), name)

    def test_any_supported_card_in_the_system_is_enough(self) -> None:
        names = "NVIDIA GeForce GTX 1050\nNVIDIA GeForce RTX 4070\n"
        self.assertEqual(self.check(names), "NVIDIA GeForce RTX 4070")

    def test_older_and_missing_cards_are_rejected_with_a_way_forward(self) -> None:
        for names, tool in (
            ("NVIDIA GeForce RTX 3080", "nvidia-smi"),
            ("NVIDIA GeForce GTX 1660", "nvidia-smi"),
            ("Quadro RTX 4000", "nvidia-smi"),
            ("", None),
        ):
            with (
                self.subTest(names=names),
                self.assertRaisesRegex(InstallerError, "choose another profile"),
            ):
                self.check(names, tool)


class SystemProgramTests(unittest.TestCase):
    program = SystemProgram(
        command="mpv",
        min_version=(0, 42, 0),
        check_args=("--vf=help",),
        check_contains="vapoursynth",
    )

    def check(self, outputs: dict[str, str], path: str | None = "/usr/bin/mpv"):
        return check_system_program(
            self.program,
            run=lambda command: outputs[command[-1]],
            which=lambda _name: path,
        )

    def test_accepts_new_enough_mpv_with_the_filter(self) -> None:
        found = self.check(
            {
                "--version": "mpv v0.42.0-dev-g1a2b3c Copyright",
                "--vf=help": "  vapoursynth  VapourSynth bridge",
            }
        )
        self.assertEqual(found, ("/usr/bin/mpv", "0.42.0"))

    def test_rejects_missing_old_or_featureless_mpv(self) -> None:
        cases = {
            "not found on PATH": ({}, None),
            "0.41.0; this profile needs mpv 0.42.0": (
                {"--version": "mpv 0.41.0-2 Copyright"},
                "/usr/bin/mpv",
            ),
            "without 'vapoursynth'": (
                {"--version": "mpv v0.42.0", "--vf=help": "  crop  Crop"},
                "/usr/bin/mpv",
            ),
        }
        for message, (outputs, path) in cases.items():
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(InstallerError, message),
            ):
                self.check(outputs, path)


if __name__ == "__main__":
    unittest.main()
