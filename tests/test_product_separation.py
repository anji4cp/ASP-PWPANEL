import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ProductSeparationTests(unittest.TestCase):
    def test_pwpanel_installer_does_not_install_cpw(self):
        installer = (ROOT / "installer" / "install-asp-pwpanel.sh").read_text(encoding="utf-8")
        self.assertNotIn("/opt/asp-cpw", installer)
        self.assertNotIn("install-asp-cpw", installer)
        self.assertIn("pw155-web.service", installer)
        self.assertIn("groupadd --system aspcpw", installer)
        self.assertIn("-/var/lib/asp-cpw-control/requests", installer)
        self.assertIn("install -d -o root -g aspcpw -m 0770", installer)

    def test_desktop_launcher_and_installer_are_present(self):
        self.assertTrue((ROOT / "ASP-PWPANEL-DESKTOP.cmd").is_file())
        self.assertTrue((ROOT / "installer" / "install-from-windows.ps1").is_file())

    def test_launcher_news_assets_are_installed(self):
        installer = (ROOT / "installer" / "install-asp-pwpanel.sh").read_text(encoding="utf-8")
        self.assertIn("launcher-news.html", installer)

    def test_game_control_worker_is_installed_as_separate_service(self):
        installer = (ROOT / "installer" / "install-asp-pwpanel.sh").read_text(encoding="utf-8")
        self.assertIn("game_control_worker.py", installer)
        self.assertIn('"$source_dir/material_catalog.json" "$install_dir/material_catalog.json"', installer)
        self.assertIn("pw155-game-control.service", installer)
        self.assertIn("PW155_PROVIDER_PORT=29300", installer)
        self.assertIn("PW155_DELIVERY_PORT=29100", installer)
        self.assertIn("PW155_WORLD_CHAT_OPCODE=120", installer)
        monitor = (ROOT / "monitor_services.py").read_text(encoding="utf-8")
        self.assertIn('("pw155-game-control.service", "Rate, Item & Safe Shutdown")', monitor)


if __name__ == "__main__":
    unittest.main()
