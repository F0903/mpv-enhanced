"""A temporary ``live/`` folder for the tests of version management."""

import tempfile
import unittest
from pathlib import Path

from mpv_enhanced.install.receipt import InstallationReceipt
from mpv_enhanced.versions.layout import Layout
from tests.support import WINDOWS


class ManagerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.layout = Layout(Path(temporary.name).resolve())

    def install(self, version: str, profile: str = "core") -> None:
        app = self.layout.app(version)
        app.mkdir(parents=True)
        InstallationReceipt.create(
            release=version, profile=profile, platform=WINDOWS.key, components=()
        ).write(app)
