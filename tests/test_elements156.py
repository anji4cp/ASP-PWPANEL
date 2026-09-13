import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from pwdata.elements156 import Elements156


ROOT = Path(__file__).parents[2]
DATA = ROOT / "runtime/fortune-treasure-v2/elements.data"
CONFIG = ROOT / "tools/1.5.x/sELedit++/configs/PW_1.5.5_v156.cfg"


class Elements156Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DATA.is_file() or not CONFIG.is_file():
            raise unittest.SkipTest("optional proprietary PW elements fixtures are not installed")
        cls.elements = Elements156.load(DATA, CONFIG)

    def test_round_trip_is_byte_identical(self):
        self.assertEqual(DATA.read_bytes(), self.elements.to_bytes())

    def test_equipment_lists_and_text_encoding(self):
        weapons = self.elements.list_named("WEAPON_ESSENCE")
        self.assertEqual(2742, len(weapons.rows))
        name_index = weapons.field_index("Name")
        encoded = self.elements.encoded_text("Test Weapon", weapons.types[name_index])
        self.assertEqual("Test Weapon", self.elements.text(encoded, weapons.types[name_index]))


if __name__ == "__main__":
    unittest.main()
