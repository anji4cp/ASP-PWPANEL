import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from pwdata.elements156 import Elements156
from pwdata.npcservices import (clone_npc_bundle, item_summary, npc_record,
                                recipe_record, service_slots, set_recipe_material,
                                set_service_slot)


ROOT = Path(__file__).parents[2]
DATA = ROOT / "runtime/fortune-treasure-v2/elements.data"
CONFIG = ROOT / "tools/1.5.x/sELedit++/configs/PW_1.5.5_v156.cfg"


class NpcServiceTests(unittest.TestCase):
    def setUp(self):
        if not DATA.is_file() or not CONFIG.is_file():
            self.skipTest("optional proprietary PW elements fixtures are not installed")
        self.elements = Elements156.load(DATA, CONFIG)

    def test_merchant_service_and_item_resolution(self):
        npc = npc_record(self.elements, 2165)
        self.assertEqual(2673, npc["services"]["id_sell_service"])
        _, _, _, slots = service_slots(self.elements, "NPC_SELL_SERVICE", 2673)
        self.assertGreater(len(slots), 0)
        item = item_summary(self.elements, slots[0]["id"])
        self.assertIsNotNone(item["price"])

    def test_nosta_crafting_recipe_material_edit(self):
        npc = npc_record(self.elements, 48055)
        self.assertEqual(48059, npc["services"]["id_make_service"])
        _, _, _, slots = service_slots(self.elements, "NPC_MAKE_SERVICE", 48059)
        recipe = recipe_record(self.elements, slots[0]["id"])
        set_recipe_material(self.elements, recipe["id"], 1, 11208, 3)
        changed = recipe_record(self.elements, recipe["id"])
        self.assertEqual({"slot": 1, "item_id": 11208, "amount": 3}, changed["materials"][0])

    def test_clone_npc_with_own_services(self):
        clone = clone_npc_bundle(self.elements, 2161, 600000, "Test Blacksmith", 600001, 600002)
        self.assertEqual(600001, clone["services"]["id_sell_service"])
        self.assertEqual(600002, clone["services"]["id_make_service"])


if __name__ == "__main__":
    unittest.main()
