import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BootstrapScriptTests(unittest.TestCase):
    def test_install_scripts_pin_the_same_uv_version(self) -> None:
        cmd = (ROOT / "install.cmd").read_text(encoding="utf-8")
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        cmd_version = re.search(r'set "UV_VERSION=([^"]+)"', cmd)
        sh_version = re.search(r"^uv_version=(\S+)$", sh, re.MULTILINE)
        assert cmd_version and sh_version
        self.assertEqual(cmd_version.group(1), sh_version.group(1))


if __name__ == "__main__":
    unittest.main()
