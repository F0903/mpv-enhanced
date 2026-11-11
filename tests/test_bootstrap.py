import unittest

from tests.support import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "package_and_release.yml"


class BootstrapScriptTests(unittest.TestCase):
    def test_uv_is_pinned_only_in_uv_pin(self) -> None:
        pin = dict(
            line.split("=", 1)
            for line in (ROOT / "uv.pin").read_text(encoding="utf-8").splitlines()
        )
        version = pin.pop("version")
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(
            set(pin),
            {
                "x86_64-pc-windows-msvc",
                "x86_64-unknown-linux-gnu",
                "aarch64-apple-darwin",
                "x86_64-apple-darwin",
            },
        )
        for digest in pin.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

        for path in (ROOT / "mpve.cmd", ROOT / "mpve.sh", WORKFLOW):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"\b[0-9a-f]{64}\b")
                self.assertNotIn(version, text)


if __name__ == "__main__":
    unittest.main()
