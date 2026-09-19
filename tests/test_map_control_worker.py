import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


WORKER_PATH = Path(__file__).parents[1] / "map_control_worker.py"
SPEC = importlib.util.spec_from_file_location("pw155_map_control", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class MapNameTests(unittest.TestCase):
    def test_discovered_maps_use_names_without_changing_aliases(self):
        config = """[General]
world_servers = gs01;is63
instance_servers = is61;is31;bg01;rand03
[World_gs01]
base_path = world/
tag = 1
[World_is63]
base_path = a63/
tag = 163
[Instance_is61]
base_path = a61/
tag = 161
[Instance_is31]
base_path = a30/
tag = 131
[Instance_bg01]
base_path = b01/
tag = 101
[Instance_rand03]
base_path = random03/
tag = 303
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gs.conf"
            path.write_text(config, encoding="utf-8")
            with patch.object(worker, "GS_CONFIG", path):
                maps = worker.discover_maps()
        labels = {item["alias"]: item["label"] for item in maps}
        self.assertEqual(labels, {
            "gs01": "World Utama", "is63": "Primal World",
            "is61": "Celestial Vale", "is31": "Twilight Temple",
            "bg01": "Territory War Lv. 3 PvP", "rand03": "Quicksand Maze",
        })
        self.assertEqual(next(item for item in maps if item["alias"] == "is31")["config"], "a30")

    def test_baseline_configured_map_aliases_all_have_names(self):
        baseline = (
            "gs01 arena01 arena02 arena03 arena04 is01 is02 is12 is13 is18 is19 "
            "is20 is21 is22 is32 is33 is34 is37 is40 is50 ms01 is63 is68 "
            "is70 is77 is05 is06 is07 is08 is09 is10 is11 is14 is15 is16 "
            "is17 is23 is24 is25 is26 is27 is28 is29 bg01 bg02 bg03 bg04 "
            "bg05 bg06 is31 is35 is38 is39 is41 is42 is43 is44 is45 is46 "
            "is47 is48 is49 is61 is62 is66 is67 is69 is71 is72 is73 is74 "
            "is75 is76 is80 is81 is82 is83 rand03 rand04"
        ).split()
        self.assertEqual(set(baseline) - worker.KNOWN_LABELS.keys(), set())


if __name__ == "__main__":
    unittest.main()
