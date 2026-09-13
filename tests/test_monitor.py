import importlib.util
import unittest
from pathlib import Path


MONITOR_PATH = Path(__file__).parents[1] / "monitor_services.py"
SPEC = importlib.util.spec_from_file_location("pw155_monitor", MONITOR_PATH)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_initial_snapshot_creates_baseline_events(self):
        services = [{"name": "glinkd", "label": "Login", "running": True,
                     "detail": "aktif"}]
        events = monitor.transition_events({"services": []}, services, "now")
        self.assertEqual(events[0]["status"], "online")

    def test_unchanged_state_creates_no_event(self):
        previous = {"services": [{"name": "glinkd", "running": True}]}
        current = [{"name": "glinkd", "label": "Login", "running": True,
                    "detail": "aktif"}]
        self.assertEqual(monitor.transition_events(previous, current, "now"), [])

    def test_failure_transition_is_recorded(self):
        previous = {"services": [{"name": "is61", "running": True}]}
        current = [{"name": "is61", "label": "Celestial Vale", "running": False,
                    "detail": "proses tidak aktif"}]
        events = monitor.transition_events(previous, current, "now")
        self.assertEqual(events[0]["status"], "offline")
        self.assertEqual(events[0]["service"], "is61")


if __name__ == "__main__":
    unittest.main()
