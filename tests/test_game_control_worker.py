import importlib.util
import hashlib
import json
import socket
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

    def test_delivery_request_identifies_iweb_and_skips_status_frame(self):
        client, server = socket.socketpair()
        unsolicited = worker.cuint(513) + worker.cuint(1) + b"S"
        expected = worker.cuint(4215) + worker.cuint(6) + b"result"
        server.sendall(unsolicited + expected)
        try:
            with patch.object(worker.socket, "create_connection", return_value=client) as connect:
                response = worker.delivery_request(4214, b"mail", 4215)
            connect.assert_called_once_with(
                (worker.DELIVERY_HOST, worker.DELIVERY_PORT), timeout=5
            )
            sent = server.recv(1024)
        finally:
            server.close()
        self.assertEqual(b"result", response)
        announce = (worker.cuint(worker.ANNOUNCE_LINK_TYPE_OPCODE)
                    + worker.cuint(1) + b"\x00")
        request = worker.cuint(4214) + worker.cuint(4) + b"mail"
        self.assertEqual(announce + request, sent)

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
        with self.assertRaises(ValueError):
            worker.validate_request({"action": "set-rates", "exp_multiplier": 99,
                                     "gold_multiplier": 2})
        with self.assertRaises(ValueError):
            worker.validate_request({"action": "send-item", "role_id": 33,
                                     "item_id": 0, "count": 1, "proctype": 0})

    def test_game_rates_use_pw155_provider_attributes(self):
        with patch.object(worker, "set_game_attribute") as set_attribute:
            result = worker.set_game_rates(5, 2)
        self.assertEqual({"exp": 5, "gold": 2}, result)
        self.assertEqual([
            (worker.EXP_RATE_ATTRIBUTE, 50),
            (worker.DOUBLE_MONEY_ATTRIBUTE, 1),
        ], [entry.args for entry in set_attribute.call_args_list])

    def test_material_mail_accepts_catalog_item_and_rejects_equipment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            asset = root / "elements.data"
            asset.write_bytes(b"verified-v156-test")
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            catalog = root / "material_catalog.json"
            catalog.write_text(json.dumps({
                "version": 156, "category": "MATERIAL_ESSENCE",
                "elements_sha256": digest,
                "items": {"21652": {"max_count": 1000, "proctype": 0}},
            }), encoding="utf-8")

            def delivery(opcode, payload, expected_opcode=None):
                self.assertEqual(worker.SYS_SEND_MAIL_OPCODE, opcode)
                self.assertEqual(worker.SYS_SEND_MAIL_RESPONSE_OPCODE, expected_opcode)
                expected_item = (
                    struct.pack("!IIhhhh", 21652, 0, 0, 1, 0, 1000)
                    + worker.cuint(0)
                    + struct.pack("!IIIIII", 0, 0, 0, 0, 0, 0)
                )
                self.assertTrue(payload.endswith(expected_item))
                return struct.pack("!hI", 0, 1234)

            with patch.object(worker, "TEST_ELEMENTS_PATH", asset), \
                    patch.object(worker, "TEST_ELEMENTS_SHA256", digest), \
                    patch.object(worker, "MATERIAL_CATALOG_PATH", catalog), \
                    patch.object(worker.time, "time", return_value=1.234), \
                    patch.object(worker, "delivery_request", side_effect=delivery) as send:
                self.assertEqual("send-item", worker.validate_request({
                    "action": "send-item", "role_id": 1024, "item_id": 21652,
                    "count": 1, "proctype": 0,
                }))
                result = worker.send_item_mail(1024, 21652, 1)
                self.assertEqual("mail_accepted_not_claimed", result["result"])
                with self.assertRaisesRegex(ValueError, "bukan material"):
                    worker.send_item_mail(1024, 11212, 1)
                with self.assertRaisesRegex(ValueError, "Jumlah material"):
                    worker.send_item_mail(1024, 21652, 1001)
                asset.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "berbeda"):
                    worker.send_item_mail(1024, 21652, 1)
                self.assertEqual(1, send.call_count)

    def test_material_trial_uses_verified_asset_and_nonzero_max_count(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = Path(folder) / "elements.data"
            asset.write_bytes(b"test-v156-elements")
            expected_sha = hashlib.sha256(asset.read_bytes()).hexdigest()
            transaction_id = 1234

            def delivery(opcode, payload, expected_opcode=None):
                self.assertEqual(worker.SYS_SEND_MAIL_OPCODE, opcode)
                self.assertEqual(worker.SYS_SEND_MAIL_RESPONSE_OPCODE, expected_opcode)
                expected_item = (
                    struct.pack("!IIhhhh", 21652, 0, 0, 1, 0, 1000)
                    + worker.cuint(0)
                    + struct.pack("!IIIIII", 0, 0, 0, 0, 0, 0)
                )
                self.assertTrue(payload.endswith(expected_item))
                return struct.pack("!hI", 0, transaction_id)

            with patch.object(worker, "TEST_ELEMENTS_PATH", asset), \
                    patch.object(worker, "TEST_ELEMENTS_SHA256", expected_sha), \
                    patch.object(worker.time, "time", return_value=1.234), \
                    patch.object(worker, "delivery_request", side_effect=delivery) as send:
                result = worker.send_test_material_mail(1024)
                self.assertEqual("mail_accepted_not_claimed", result["result"])
                self.assertEqual(1000, result["max_count"])
                asset.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "berbeda"):
                    worker.send_test_material_mail(1024)
                with self.assertRaisesRegex(ValueError, "hanya mendukung"):
                    worker.send_test_material_mail(1024, 11212)
                self.assertEqual(1, send.call_count)

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
