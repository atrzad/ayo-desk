import unittest

from ayo_desk.music import cava, fonts


class CavaTests(unittest.TestCase):
    def test_config_uses_raw_ascii_output(self):
        text = cava.config_text(32)
        self.assertIn("bars = 32", text)
        self.assertIn("method = raw", text)
        self.assertIn("data_format = ascii", text)

    def test_parse_frames(self):
        self.assertEqual(cava.parse("0;500;1000;\n"), [0.0, 0.5, 1.0])
        self.assertEqual(cava.parse("garbage"), [])
        self.assertEqual(cava.parse("2000;"), [1.0])


class FontTests(unittest.TestCase):
    def test_package_for_scripts(self):
        self.assertEqual(fonts.package_for("い"), "noto-fonts-cjk")
        self.assertEqual(fonts.package_for("한"), "noto-fonts-cjk")
        self.assertEqual(fonts.package_for("😀"), "noto-fonts-emoji")
        self.assertEqual(fonts.package_for("ש"), "noto-fonts-extra")
        self.assertEqual(fonts.package_for("𝐌"), "noto-fonts-extra")
        self.assertEqual(fonts.package_for("ç"), "noto-fonts")


if __name__ == "__main__":
    unittest.main()
