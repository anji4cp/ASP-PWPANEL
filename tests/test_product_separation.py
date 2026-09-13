import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ProductSeparationTests(unittest.TestCase):
    def test_pwpanel_installer_does_not_install_cpw(self):
        installer = (ROOT / "installer" / "install-asp-pwpanel.sh").read_text(encoding="utf-8")
        self.assertNotIn("/opt/asp-cpw", installer)
        self.assertNotIn("install-asp-cpw", installer)
        self.assertIn("pw155-web.service", installer)

    def test_desktop_launcher_and_installer_are_present(self):
        self.assertTrue((ROOT / "ASP-PWPANEL-DESKTOP.cmd").is_file())
        self.assertTrue((ROOT / "installer" / "install-from-windows.ps1").is_file())


if __name__ == "__main__":
    unittest.main()
