import sys
import unittest
from contextlib import chdir
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced_installer.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_list_profiles_accepts_manifest_flags_after_subcommand(self) -> None:
        output = StringIO()
        errors = StringIO()
        code = main(
            [
                "list-profiles",
                "--deps-manifest",
                str(ROOT / "deps.lock.toml"),
                "--profiles-manifest",
                str(ROOT / "profiles.toml"),
                "--platform",
                "windows-x86_64",
            ],
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(code, 0, errors.getvalue())
        self.assertIn("core\t", output.getvalue())
        self.assertIn("rife-tensorrt\t", output.getvalue())

    def test_empty_argv_defaults_to_interactive_install_command(self) -> None:
        output = StringIO()
        errors = StringIO()
        with patch.object(sys, "argv", ["mpv-enhanced"]), chdir(ROOT):
            code = main(stdin=StringIO(), stdout=output, stderr=errors)
        self.assertEqual(code, 2)
        self.assertIn("--profile is required", errors.getvalue())
        self.assertNotIn("arguments are required", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
