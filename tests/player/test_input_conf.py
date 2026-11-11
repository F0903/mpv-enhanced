import unittest

from tests.support import ROOT

INPUT_CONF = ROOT / "portable_config" / "input.conf"


class InputConfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = INPUT_CONF.read_text(encoding="utf-8")
        cls.titles = [
            line.split("#!")[1].strip()
            for line in cls.text.splitlines()
            if "#!" in line
        ]

    def test_declares_every_default_uosc_entry_and_ours(self) -> None:
        for title in (
            "Subtitles",
            "Audio tracks",
            "Stream quality",
            "Playlist",
            "Chapters",
            "Navigation > Next",
            "Navigation > Prev",
            "Navigation > Delete file & Next",
            "Navigation > Delete file & Prev",
            "Navigation > Delete file & Quit",
            "Navigation > Open file",
            "Utils > Aspect ratio > Default",
            "Utils > Aspect ratio > 16:9",
            "Utils > Aspect ratio > 4:3",
            "Utils > Aspect ratio > 2.35:1",
            "Utils > Audio devices",
            "Utils > Editions",
            "Utils > Screenshot",
            "Utils > Key bindings",
            "Utils > Show in directory",
            "Utils > Open config folder",
            "MPV Enhanced",
            "Quit",
        ):
            with self.subTest(title=title):
                self.assertIn(title, self.titles)

    def test_our_entry_opens_our_menu(self) -> None:
        self.assertRegex(
            self.text, r"script-message-to mpv_enhanced open-menu\s+#! MPV Enhanced"
        )

    def test_keeps_the_interpolation_hotkey(self) -> None:
        self.assertIn("ctrl+shift+r script-message-to mpv_enhanced toggle", self.text)


class MainScriptTests(unittest.TestCase):
    def test_filter_changes_are_asynchronous(self) -> None:
        # A synchronous command runs on the script's own thread, Python then
        # initialises there, and mpv crashes at exit once that thread is gone.
        script = (INPUT_CONF.parent / "scripts/mpv_enhanced/main.lua").read_text(
            "utf-8"
        )
        self.assertNotRegex(script, r'commandv\(\s*"vf"')
        self.assertIn("command_native_async", script)


if __name__ == "__main__":
    unittest.main()
