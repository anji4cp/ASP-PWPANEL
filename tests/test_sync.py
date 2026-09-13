import importlib.util
import struct
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "sync_characters.py"
SPEC = importlib.util.spec_from_file_location("pw155_sync", MODULE_PATH)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class ProtocolTests(unittest.TestCase):
    def test_cuint_roundtrip_boundaries(self):
        for value in (0, 63, 64, 16383, 16384, 536870911, 536870912, 0xFFFFFFFF):
            encoded = sync.encode_cuint(value)
            decoded, offset = sync.decode_cuint(encoded)
            self.assertEqual(decoded, value)
            self.assertEqual(offset, len(encoded))

    def test_frame_header(self):
        payload = struct.pack(">II", 0xFFFFFFFF, 1024)
        packet = sync.frame(sync.GET_USER_ROLES, payload)
        opcode, offset = sync.decode_cuint(packet)
        length, offset = sync.decode_cuint(packet, offset)
        self.assertEqual(opcode, sync.GET_USER_ROLES)
        self.assertEqual(length, len(payload))
        self.assertEqual(packet[offset:], payload)

    def test_reader_ustring(self):
        raw = "Karakter".encode("utf-16le")
        reader = sync.Reader(sync.encode_cuint(len(raw)) + raw)
        self.assertEqual(reader.ustring(), "Karakter")

    def test_rejects_truncated_cuint(self):
        with self.assertRaises(sync.ProtocolError):
            sync.decode_cuint(b"\x80")


if __name__ == "__main__":
    unittest.main()
