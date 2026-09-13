import importlib.util
import os
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PW155_WEB_CSRF_SECRET", "t" * 32)
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("role_operations", ROOT / "role_operations.py")
role_operations = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(role_operations)

class RoleOperationTests(unittest.TestCase):
    def test_coin_rpc_is_allowlisted_delta(self):
        response = struct.pack(">IIq", 0xFFFFFFFF, 0, 12_000_000)
        with patch.object(role_operations, "request", return_value=(8005, response)) as request:
            result = role_operations.add_pocket_coins(33, 5_000_000)
        opcode, payload = request.call_args.args
        self.assertEqual(8005, opcode)
        self.assertEqual(33, struct.unpack_from(">I", payload, 4)[0])
        self.assertEqual(role_operations.POCKET_MONEY, struct.unpack_from(">I", payload, 8)[0])
        self.assertEqual(5_000_000, struct.unpack_from(">i", payload, 24)[0])
        self.assertEqual(12_000_000, result["total_money"])

    def test_unstuck_preserves_status_except_position(self):
        status = bytearray(range(90))
        struct.pack_into(">fffI", status, 29, 1.0, 2.0, 3.0, 9)
        responses = [(3015, struct.pack(">II", 0xFFFFFFFF, 0) + status),
                     (3014, struct.pack(">II", 0xFFFFFFFF, 0))]
        with patch.object(role_operations, "request", side_effect=responses) as request:
            result = role_operations.move_to_safe_point(33)
        written = request.call_args_list[1].args[1][8:]
        self.assertEqual(status[:29], written[:29])
        self.assertEqual(status[45:], written[45:])
        self.assertEqual(role_operations.SAFE_WORLD_TAG,
                         struct.unpack_from(">I", written, 41)[0])
        self.assertEqual(9, result["previous"]["world_tag"])

if __name__ == "__main__":
    unittest.main()
