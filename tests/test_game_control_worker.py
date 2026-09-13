import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


WORKER_PATH = Path(__file__).parents[1] / "game_control_worker.py"
SPEC = importlib.util.spec_from_file_location("pw155_game_control_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class GameControlWorkerTests(unittest.TestCase):
    def test_cuint_protocol_boundaries(self):
        self.assertEqual(b"\x3f", worker.cuint(0x3F))
        self.assertEqual(struct.pack("!H", 0x8040), worker.cuint(0x40))
        self.assertEqual(struct.pack("!I", 0xC0004000), worker.cuint(0x4000))

    def test_world_chat_packet_contains_channel_role_and_utf16_message(self):
        packet = worker.public_chat_packet("Server test")
        opcode = worker.cuint(worker.WORLD_CHAT_OPCODE)
        self.assertEqual(opcode, packet[:len(opcode)])
        self.assertIn(struct.pack("!BBI", 9, 0, 0xFFFFFFFF), packet)
        self.assertIn("Server test".encode("utf-16le"), packet)

    def test_request_validation_rejects_unknown_or_unsafe_content(self):
        with self.assertRaises(ValueError):
            worker.validate_request({"action": "shell"})
        with self.assertRaises(ValueError):
            worker.validate_request({"action": "broadcast", "message": "bad\nline"})
        with self.assertRaises(ValueError):
            worker.validate_request({"action": "schedule-shutdown", "seconds": 5,
                                     "reason": "Maintenance"})

    def test_schedule_then_cancel_is_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            requests = root / "requests"
            requests.mkdir()
            schedule_path = requests / "schedule.json"
            schedule_path.write_text(json.dumps({
                "id": "schedule", "action": "schedule-shutdown", "actor": "admin",
                "requested_at": "now", "seconds": 300, "reason": "Maintenance",
            }), encoding="utf-8")
            state = worker.default_state()
            with patch.object(worker, "CONTROL_DIR", root), \
                    patch.object(worker, "REQUEST_DIR", requests), \
                    patch.object(worker, "broadcast") as send:
                worker.process_queue(state)
                self.assertIsNotNone(state["scheduled"])
                self.assertEqual("scheduled", state["last_action"]["status"])
                cancel_path = requests / "cancel.json"
                cancel_path.write_text(json.dumps({
                    "id": "cancel", "action": "cancel-shutdown", "actor": "admin",
                    "requested_at": "now",
                }), encoding="utf-8")
                worker.process_queue(state)
                self.assertIsNone(state["scheduled"])
                self.assertEqual(2, send.call_count)

    def test_manual_broadcast_uses_server_notice_format(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = root / "broadcast.json"
            request.write_text(json.dumps({
                "id": "broadcast", "action": "broadcast", "actor": "admin",
                "requested_at": "now", "message": "Welcome to the event",
            }), encoding="utf-8")
            state = worker.default_state()
            with patch.object(worker, "CONTROL_DIR", root), \
                    patch.object(worker, "broadcast") as send:
                worker.process_request(request, state)
            send.assert_called_once_with("Welcome to the event")
            self.assertEqual("completed", state["last_action"]["status"])

    def test_expired_schedule_stops_maps_and_core(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = worker.default_state()
            state["scheduled"] = {
                "id": "shutdown", "actor": "admin", "reason": "Maintenance",
                "countdown_seconds": 10, "execute_at": 0, "announced": [],
            }
            result = Mock(returncode=0, stdout="stopped", stderr="")
            with patch.object(worker, "CONTROL_DIR", root), \
                    patch.object(worker, "SERVICE_SCRIPT", Path("/fixed/service.sh")), \
                    patch.object(worker, "broadcast"), \
                    patch.object(worker.time, "sleep"), \
                    patch.object(worker.subprocess, "run", return_value=result) as run:
                worker.tick_shutdown(state)
            run.assert_called_once()
            self.assertEqual([str(Path("/fixed/service.sh")), "stop-core"], run.call_args.args[0])
            self.assertIsNone(state["scheduled"])
            self.assertEqual("completed", state["last_action"]["status"])


if __name__ == "__main__":
    unittest.main()
