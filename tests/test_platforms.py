import unittest

from mpv_enhanced.errors import UnsupportedPlatformError
from mpv_enhanced.platforms import PlatformInfo


class PlatformTests(unittest.TestCase):
    def test_common_windows_values_are_normalized(self) -> None:
        platform = PlatformInfo.detect(system="Windows", machine="AMD64")
        self.assertEqual(platform.key, "windows-x86_64")

    def test_unknown_architecture_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedPlatformError):
            PlatformInfo.detect(system="Windows", machine="mystery-cpu")


if __name__ == "__main__":
    unittest.main()
