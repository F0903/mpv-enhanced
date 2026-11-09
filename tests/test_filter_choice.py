import json
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable_config"))

from vs_script.choice import choice_settings, parse_user_data


class ParseUserDataTests(unittest.TestCase):
    def test_parses_id_and_multiplier(self) -> None:
        self.assertEqual(parse_user_data("rtx-high/2"), ("rtx-high", Fraction(2)))
        self.assertEqual(
            parse_user_data("rife-4.25-lite/3"), ("rife-4.25-lite", Fraction(3))
        )

    def test_rejects_malformed_data(self) -> None:
        for text in ("", "rtx-high", "rtx-high/0", "rtx-high/-2", "rtx-high/x", "/2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_user_data(text)


class ChoiceSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "info.json"
        self.path.write_text(
            json.dumps(
                {
                    "choices": [
                        {"id": "a", "title": "A", "settings": {"rtx_mode": "HIGH"}}
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_reads_settings_for_a_choice(self) -> None:
        self.assertEqual(choice_settings(self.path, "a")["rtx_mode"], "HIGH")

    def test_unknown_choice_is_an_error(self) -> None:
        with self.assertRaises(KeyError):
            choice_settings(self.path, "missing")


if __name__ == "__main__":
    unittest.main()
