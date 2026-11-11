import unittest

from mpv_enhanced.errors import InstallerError
from tests.versions.support import ManagerTestCase


class LayoutTests(ManagerTestCase):
    def test_versions_sort_numerically_and_previous_skips_the_active(self) -> None:
        for version in ("0.9.0", "0.10.0", "0.2.0"):
            self.install(version)
        self.layout.activate("0.10.0")
        self.assertEqual(self.layout.versions(), ["0.10.0", "0.9.0", "0.2.0"])
        self.assertEqual(self.layout.previous(), "0.9.0")
        self.assertEqual(self.layout.profile("0.9.0"), "core")

    def test_finish_install_keeps_two_versions_and_writes_no_launcher(self) -> None:
        for version in ("0.1.0", "0.2.0", "0.3.0"):
            self.install(version)
        self.layout.finish_install("0.3.0")
        self.assertEqual(self.layout.versions(), ["0.3.0", "0.2.0"])
        self.assertEqual(self.layout.active(), "0.3.0")
        self.assertEqual(
            sorted(item.name for item in self.layout.root.iterdir()), ["live"]
        )

    def test_rollback_switches_and_needs_something_to_switch_to(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        with self.assertRaisesRegex(InstallerError, "no previous version"):
            self.layout.rollback()
        self.install("0.2.0")
        self.layout.activate("0.2.0")
        self.assertEqual(self.layout.rollback(), "0.1.0")
        self.assertEqual(self.layout.active(), "0.1.0")

    def test_store_release_copies_only_release_files(self) -> None:
        source = self.layout.root / "source"
        (source / "src").mkdir(parents=True)
        (source / "src" / "code.py").write_text("x")
        (source / "live" / "0.1.0").mkdir(parents=True)
        (source / ".venv").mkdir()
        self.layout.store_release("0.2.0", source)
        stored = self.layout.release("0.2.0")
        self.assertTrue((stored / "src" / "code.py").is_file())
        self.assertFalse((stored / "live").exists())
        self.assertFalse((stored / ".venv").exists())

    def test_user_files_carry_over_only_between_different_versions(self) -> None:
        self.install("0.1.0")
        self.layout.activate("0.1.0")
        self.assertEqual(self.layout.carry_source("0.2.0"), self.layout.app("0.1.0"))
        self.assertIsNone(self.layout.carry_source("0.1.0"))


if __name__ == "__main__":
    unittest.main()
