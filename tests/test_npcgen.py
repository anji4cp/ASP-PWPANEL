import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
from pwdata.npcgen import NpcGen


ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "runtime/fortune-treasure-v3/a61/npcgen.data"


class NpcGenTests(unittest.TestCase):
    def setUp(self):
        if not SOURCE.is_file():
            self.skipTest("optional proprietary PW npcgen fixture is not installed")

    def test_round_trip_is_byte_identical(self):
        npcgen = NpcGen.load(SOURCE)
        self.assertEqual(SOURCE.read_bytes(), npcgen.to_bytes())
        self.assertGreater(len(npcgen.groups), 0)

    def test_finds_nosta_and_clones_spawn(self):
        npcgen = NpcGen.load(SOURCE)
        found = npcgen.find("48055")
        self.assertEqual(1, len(found))
        group_index = found[0][0]
        new_index = npcgen.clone_group(group_index, 48055, 1.5, 2.5, 3.5)
        self.assertEqual(0, npcgen.groups[new_index].trigger_id)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "npcgen.data"
            npcgen.save(output)
            verified = NpcGen.load(output)
        self.assertEqual(len(npcgen.groups), len(verified.groups))
        self.assertAlmostEqual(1.5, verified.groups[new_index].x)


if __name__ == "__main__":
    unittest.main()
