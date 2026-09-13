import hashlib
import sys
import unittest
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))

from pwdata.gshop import GShop


class GShopTests(unittest.TestCase):
    def setUp(self):
        if not (PROJECT_DIR / "vendor/Perfect_World_Server_1.5.5/gamed/config/gshop.data").is_file():
            self.skipTest("optional proprietary PW gshop fixtures are not installed")

    def test_client_round_trip_is_byte_identical(self):
        source = PROJECT_DIR / "vendor/Perfect_World_Server_1.5.5/gamed/config/gshop.data"
        original = source.read_bytes()
        shop = GShop.load(source)
        self.assertEqual(2004, len(shop.items))
        self.assertEqual(8, len(shop.categories))
        self.assertEqual(hashlib.sha256(original).digest(), hashlib.sha256(shop.to_bytes()).digest())

    def test_server_round_trip_is_byte_identical(self):
        source = PROJECT_DIR / "vendor/Perfect_World_Server_1.5.5/gamed/config/gshopsev.data"
        original = source.read_bytes()
        shop = GShop.load(source, client=False)
        self.assertEqual(2004, len(shop.items))
        self.assertEqual(hashlib.sha256(original).digest(), hashlib.sha256(shop.to_bytes()).digest())

    def test_client_and_server_records_match(self):
        client = GShop.load(PROJECT_DIR / "vendor/Perfect_World_Server_1.5.5/gamed/config/gshop.data")
        server = GShop.load(PROJECT_DIR / "vendor/Perfect_World_Server_1.5.5/gamed/config/gshopsev.data", client=False)
        self.assertEqual(len(client.items), len(server.items))
        for left, right in zip(client.items, server.items):
            self.assertEqual(left.item_id, right.item_id)
            self.assertEqual(left.amount, right.amount)
            self.assertEqual(left.category, right.category)
            self.assertEqual(left.subcategory, right.subcategory)
            self.assertEqual(left.sales, right.sales)


if __name__ == "__main__":
    unittest.main()
