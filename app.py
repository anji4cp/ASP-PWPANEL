#!/usr/bin/env python3
"""Web lokal PW 1.5.5 tanpa dependency pihak ketiga."""

import base64
import binascii
import copy
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pwdata.gshop import GShop
from pwdata.npcgen import NpcGen
from pwdata.elements156 import Elements156
from pwdata.npcservices import (clone_npc_bundle, clone_record, item_summary,
                                npc_record, recipe_record, row_by_id,
                                service_slots, set_item_price,
                                set_recipe_material, set_service_slot, set_value,
                                value as service_value)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_DIR = BASE_DIR / "downloads"
PATCH_DIR = Path(os.environ.get("PW155_PATCH_DIR", str(DOWNLOAD_DIR / "CPW")))
DB_CONFIG = os.environ.get("PW155_WEB_DB_CONFIG", "/etc/pw155-web/db.cnf")
BIND_HOST = os.environ.get("PW155_WEB_HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("PW155_WEB_PORT", "8080"))
CSRF_SECRET = os.environ.get("PW155_WEB_CSRF_SECRET", "")
MONITOR_DIR = Path(os.environ.get("PW155_MONITOR_DIR", "/var/lib/pw155-monitor"))
GAME_CONFIG_DIR = Path(os.environ.get(
    "PW155_GAME_CONFIG_DIR", "/srv/pw155/staging/pw155/gamed/config"
))
CLIENT_DATA_DIR = Path(os.environ.get(
    "PW155_CLIENT_DATA_DIR", "/srv/pw155/staging/client-data"
))
DATA_EDITOR_DIR = Path(os.environ.get(
    "PW155_DATA_EDITOR_DIR", "/var/lib/pw155-editor"
))
MAP_CONTROL_DIR = Path(os.environ.get(
    "PW155_MAP_CONTROL_DIR", "/var/lib/pw155-map-control"
))
CPW_CONTROL_DIR = Path(os.environ.get(
    "PW155_CPW_CONTROL_DIR", "/var/lib/asp-cpw-control"
))
BACKUP_CONTROL_DIR = Path(os.environ.get(
    "PW155_BACKUP_CONTROL_DIR", "/var/lib/pw155-backup-control"
))
NPCGEN_PATH = Path(os.environ.get(
    "PW155_NPCGEN_PATH", "/var/lib/pw155-editor/sources/a61/npcgen.data"
))
ELEMENTS_PATH = Path(os.environ.get(
    "PW155_ELEMENTS_PATH", "/var/lib/pw155-editor/sources/elements.data"
))
ELEMENTS_CONFIG_PATH = Path(os.environ.get(
    "PW155_ELEMENTS_CONFIG_PATH", "/var/lib/pw155-editor/sources/PW_1.5.5_v156.cfg"
))
EQUIPMENT_TYPES = {
    "weapon": {"suffix": "WEAPON_ESSENCE", "label": "Weapon", "stats": (
        ("damage_low", "Physical ATK minimum"), ("damage_high_min", "Physical ATK maksimum minimum"),
        ("damage_high_max", "Physical ATK maksimum maksimum"), ("magic_damage_low", "Magic ATK minimum"),
        ("magic_damage_high_min", "Magic ATK maksimum minimum"),
        ("magic_damage_high_max", "Magic ATK maksimum maksimum"),
    )},
    "armor": {"suffix": "ARMOR_ESSENCE", "label": "Armor", "stats": (
        ("defence_low", "Physical DEF minimum"), ("defence_high", "Physical DEF maksimum"),
        ("hp_enhance_low", "HP bonus minimum"), ("hp_enhance_high", "HP bonus maksimum"),
    )},
    "decoration": {"suffix": "DECORATION_ESSENCE", "label": "Accessory", "stats": (
        ("damage_low", "Physical ATK minimum"), ("damage_high", "Physical ATK maksimum"),
        ("magic_damage_low", "Magic ATK minimum"), ("magic_damage_high", "Magic ATK maksimum"),
        ("defence_low", "Physical DEF minimum"), ("defence_high", "Physical DEF maksimum"),
    )},
}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_.-]{6,32}$")
SERVER_PORTS = {
    "Login": 29000,
    "Auth": 29200,
    "Database": 3306,
    "GameDB": 29400,
}


class RateLimiter:
    def __init__(self, limit=5, window_seconds=600):
        self.limit = limit
        self.window = window_seconds
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self.lock:
            bucket = self.attempts[key]
            while bucket and bucket[0] <= now - self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


REGISTRATION_LIMITER = RateLimiter(limit=5, window_seconds=600)
LOGIN_LIMITER = RateLimiter(limit=10, window_seconds=600)
SESSION_TTL = 8 * 60 * 60
CLASS_NAMES = {
    0: "Blademaster", 1: "Wizard", 2: "Psychic", 3: "Venomancer",
    4: "Barbarian", 5: "Assassin", 6: "Archer", 7: "Cleric",
    8: "Seeker", 9: "Mystic", 10: "Duskblade", 11: "Stormbringer",
}
ADMIN_SEARCH_RE = re.compile(r"^[A-Za-z0-9_]{0,20}$")
DOWNLOAD_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.zip$")
BACKUP_FILENAME_RE = re.compile(r"^PW155-database-[0-9]{8}T[0-9]{6}Z\.tar\.gz$")
NEWS_STATUSES = {"draft", "published", "archived"}


def boutique_catalog(search="", limit=100):
    """Read a bounded Boutique catalog from the active client-format shop."""
    shop = GShop.load(CLIENT_DATA_DIR / "gshop.data")
    query = search.strip()[:80]
    rows = []
    for index, item in shop.find(query, max(1, min(int(limit), 100))):
        category = shop.categories[item.category]
        subcategory = (
            category.subcategories[item.subcategory]
            if 0 <= item.subcategory < len(category.subcategories) else "-"
        )
        active_sales = [sale for sale in item.sales if sale.price > 0]
        rows.append({
            "index": index,
            "place": item.place,
            "item_id": item.item_id,
            "name": item.name or f"Item {item.item_id}",
            "amount": item.amount,
            "category": category.name or f"Kategori {item.category + 1}",
            "subcategory": subcategory or "-",
            "price_cash": active_sales[0].price if active_sales else 0,
            "sale_count": len(active_sales),
        })
    return {
        "timestamp": shop.timestamp,
        "total": len(shop.items),
        "category_count": len(shop.categories),
        "rows": rows,
    }


def load_boutique_pair():
    client = GShop.load(CLIENT_DATA_DIR / "gshop.data")
    server = GShop.load(CLIENT_DATA_DIR / "gshopsev.data", client=False)
    if len(client.items) != len(server.items):
        raise ValueError("Jumlah record gshop client dan server berbeda")
    if any(left.item_id != right.item_id for left, right in zip(client.items, server.items)):
        raise ValueError("Urutan Item ID gshop client dan server tidak cocok")
    return client, server


def boutique_item(index):
    client, _ = load_boutique_pair()
    if not 0 <= int(index) < len(client.items):
        raise ValueError("Index item Boutique tidak valid")
    item = client.items[int(index)]
    return client, item


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _parse_boutique_form(fields, categories):
    values = {}
    for key in ("item_id", "amount", "category", "subcategory", "price_cash"):
        text = fields.get(key, [""])[0].strip()
        if not text.isdigit():
            raise ValueError(f"Field {key} harus berupa angka")
        values[key] = int(text)
    values["name"] = fields.get("name", [""])[0].strip()
    values["description"] = fields.get("description", [""])[0].strip()
    values["icon"] = fields.get("icon", [""])[0].strip()
    if not 1 <= values["item_id"] <= 2_147_483_647:
        raise ValueError("Item ID tidak valid")
    if not 1 <= values["amount"] <= 999_999:
        raise ValueError("Jumlah item harus 1–999.999")
    if not 0 <= values["category"] < len(categories):
        raise ValueError("Kategori tidak valid")
    if not 0 <= values["subcategory"] < len(categories[values["category"]].subcategories):
        raise ValueError("Subkategori tidak valid")
    if not 1 <= values["price_cash"] <= 2_000_000_000:
        raise ValueError("Harga harus minimal 1 cash")
    if not values["name"] or len(values["name"].encode("utf-16le")) > 62:
        raise ValueError("Nama wajib dan maksimum 31 karakter")
    if len(values["description"].encode("utf-16le")) > 1022:
        raise ValueError("Deskripsi terlalu panjang")
    try:
        icon_bytes = values["icon"].encode("gbk")
    except UnicodeEncodeError as error:
        raise ValueError("Path icon mengandung karakter yang tidak didukung") from error
    if len(icon_bytes) > 127:
        raise ValueError("Path icon terlalu panjang")
    return values


def build_boutique_draft(actor, index, fields, clone=False):
    client, server = load_boutique_pair()
    index = int(index)
    if not 0 <= index < len(client.items):
        raise ValueError("Index item Boutique tidak valid")
    values = _parse_boutique_form(fields, client.categories)
    if clone:
        client_item = copy.deepcopy(client.items[index])
        server_item = copy.deepcopy(server.items[index])
        client_item.place = max(item.place for item in client.items) + 1
        server_item.place = max(item.place for item in server.items) + 1
        client.items.append(client_item)
        server.items.append(server_item)
        target_index = len(client.items) - 1
    else:
        client_item = client.items[index]
        server_item = server.items[index]
        target_index = index

    for item in (client_item, server_item):
        item.item_id = values["item_id"]
        item.amount = values["amount"]
        item.category = values["category"]
        item.subcategory = values["subcategory"]
        item.sales[0].price = values["price_cash"]
    client_item.name = values["name"]
    client_item.description = values["description"]
    client_item.icon = values["icon"]
    now = int(time.time())
    client.timestamp = now
    server.timestamp = now

    draft = DATA_EDITOR_DIR / "boutique" / "draft"
    draft.mkdir(parents=True, exist_ok=True)
    client_path = draft / "gshop.data"
    server_path = draft / "gshopsev.data"
    client.save(client_path)
    server.save(server_path, client=False)
    verified_client = GShop.load(client_path)
    verified_server = GShop.load(server_path, client=False)
    if verified_client.items[target_index].item_id != values["item_id"]:
        raise ValueError("Verifikasi draft client gagal")
    if verified_server.items[target_index].item_id != values["item_id"]:
        raise ValueError("Verifikasi draft server gagal")
    if len(verified_client.items) != len(verified_server.items):
        raise ValueError("Jumlah record draft client/server berbeda")

    manifest = {
        "created_at": now,
        "actor_id": int(actor[0]),
        "actor": actor[1],
        "operation": "clone" if clone else "edit",
        "source_index": index,
        "target_index": target_index,
        "item_id": values["item_id"],
        "name": values["name"],
        "client_source_sha256": _sha256_file(CLIENT_DATA_DIR / "gshop.data"),
        "server_source_sha256": _sha256_file(CLIENT_DATA_DIR / "gshopsev.data"),
        "client_draft_sha256": _sha256_file(client_path),
        "server_draft_sha256": _sha256_file(server_path),
        "record_count": len(verified_client.items),
        "status": "draft",
    }
    (draft / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def boutique_draft_manifest():
    path = DATA_EDITOR_DIR / "boutique" / "draft" / "manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def npc_catalog(search="", limit=100):
    """Bounded catalog of a61 NPC/monster spawns; names stay in elements.data."""
    npcgen = NpcGen.load(NPCGEN_PATH)
    query = search.strip()[:80]
    rows = []
    for group_index, entry_index, group, entry, name in npcgen.find(query, limit=limit):
        rows.append({
            "group_index": group_index, "entry_index": entry_index,
            "npc_id": entry.npc_id, "name": name, "amount": entry.amount,
            "respawn": entry.respawn, "trigger": group.trigger_id,
            "x": group.x, "y": group.y, "z": group.z,
        })
    return {"version": npcgen.version, "total_groups": len(npcgen.groups), "rows": rows}


def npc_spawn_item(group_index, entry_index):
    npcgen = NpcGen.load(NPCGEN_PATH)
    if not 0 <= int(group_index) < len(npcgen.groups):
        raise ValueError("Index spawn group tidak valid")
    group = npcgen.groups[int(group_index)]
    if not 0 <= int(entry_index) < len(group.entries):
        raise ValueError("Index NPC dalam group tidak valid")
    return npcgen, group, group.entries[int(entry_index)]


def _npc_form_values(fields):
    try:
        npc_id = int(fields.get("npc_id", [""])[0])
        amount = int(fields.get("amount", [""])[0])
        respawn = int(fields.get("respawn", [""])[0])
        trigger_id = int(fields.get("trigger_id", [""])[0])
        x = float(fields.get("x", [""])[0])
        y = float(fields.get("y", [""])[0])
        z = float(fields.get("z", [""])[0])
    except (TypeError, ValueError) as error:
        raise ValueError("ID, jumlah, respawn, trigger, dan koordinat harus valid") from error
    if not 1 <= npc_id <= 2_147_483_647 or not 1 <= amount <= 1_000_000:
        raise ValueError("NPC ID atau jumlah spawn tidak valid")
    if not 0 <= respawn <= 2_147_483_647 or not 0 <= trigger_id <= 2_147_483_647:
        raise ValueError("Respawn atau trigger tidak valid")
    if any(not -1_000_000 <= value <= 1_000_000 for value in (x, y, z)):
        raise ValueError("Koordinat di luar batas aman")
    return {"npc_id": npc_id, "amount": amount, "respawn": respawn,
            "trigger_id": trigger_id, "x": x, "y": y, "z": z}


def build_npc_draft(actor, group_index, entry_index, fields, clone=False):
    npcgen, group, entry = npc_spawn_item(group_index, entry_index)
    values = _npc_form_values(fields)
    if clone:
        target_group = npcgen.clone_group(int(group_index), values["npc_id"],
                                          values["x"], values["y"], values["z"],
                                          values["trigger_id"])
        target_entry = 0
        draft_entry = npcgen.groups[target_group].entries[target_entry]
    else:
        target_group, target_entry, draft_entry = int(group_index), int(entry_index), entry
        group.x, group.y, group.z = values["x"], values["y"], values["z"]
        group.trigger_id = values["trigger_id"]
    draft_entry.npc_id = values["npc_id"]
    draft_entry.amount = values["amount"]
    draft_entry.respawn = values["respawn"]
    draft = DATA_EDITOR_DIR / "npc" / "draft" / "a61"
    draft.mkdir(parents=True, exist_ok=True)
    path = draft / "npcgen.data"
    npcgen.save(path)
    verified, verified_group, verified_entry = npc_spawn_item_from_path(path, target_group, target_entry)
    if (verified_entry.npc_id, verified_entry.amount, verified_entry.respawn) != (
            values["npc_id"], values["amount"], values["respawn"]):
        raise ValueError("Verifikasi draft NPC gagal")
    if (verified_group.x, verified_group.y, verified_group.z, verified_group.trigger_id) != (
            values["x"], values["y"], values["z"], values["trigger_id"]):
        raise ValueError("Verifikasi lokasi draft NPC gagal")
    now = int(time.time())
    manifest = {
        "created_at": now, "actor_id": int(actor[0]), "actor": actor[1],
        "operation": "clone" if clone else "edit", "source_group": int(group_index),
        "source_entry": int(entry_index), "target_group": target_group,
        "target_entry": target_entry, "npc_id": values["npc_id"],
        "coordinates": {key: values[key] for key in ("x", "y", "z")},
        "trigger_id": values["trigger_id"], "group_count": len(verified.groups),
        "source_sha256": _sha256_file(NPCGEN_PATH), "draft_sha256": _sha256_file(path),
        "status": "draft",
    }
    (draft.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def npc_spawn_item_from_path(path, group_index, entry_index):
    npcgen = NpcGen.load(path)
    group = npcgen.groups[int(group_index)]
    return npcgen, group, group.entries[int(entry_index)]


def npc_draft_manifest():
    path = DATA_EDITOR_DIR / "npc" / "draft" / "manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _equipment_value(item_list, row, field):
    index = item_list.field_index(field)
    value = row[index]
    return Elements156.text(value, item_list.types[index])


def equipment_catalog(search="", equipment_type="all", limit=100):
    source = _elements_draft_path() if _elements_draft_path().is_file() else ELEMENTS_PATH
    elements = Elements156.load(source, ELEMENTS_CONFIG_PATH)
    if equipment_type != "all" and equipment_type not in EQUIPMENT_TYPES:
        raise ValueError("Tipe equipment tidak valid")
    query = search.strip()[:80].casefold()
    selected = EQUIPMENT_TYPES if equipment_type == "all" else {equipment_type: EQUIPMENT_TYPES[equipment_type]}
    rows = []
    total = sum(len(elements.list_named(definition["suffix"]).rows)
                for definition in selected.values())
    for type_key, definition in selected.items():
        item_list = elements.list_named(definition["suffix"])
        for row_index, row in enumerate(item_list.rows):
            item_id = int(row[item_list.field_index("ID")])
            name = _equipment_value(item_list, row, "Name") or f"Equipment {item_id}"
            if query and query not in name.casefold() and query != str(item_id):
                continue
            rows.append({
                "type": type_key, "type_label": definition["label"], "index": row_index,
                "item_id": item_id, "name": name,
                "level": int(row[item_list.field_index("level")]),
                "require_level": int(row[item_list.field_index("require_level")]),
            })
            if len(rows) >= max(1, min(int(limit), 100)):
                return {"rows": rows, "total": total, "version": elements.version}
    return {"rows": rows, "total": total, "version": elements.version}


def equipment_item(type_key, row_index, path=None):
    if type_key not in EQUIPMENT_TYPES:
        raise ValueError("Tipe equipment tidak valid")
    source = path or (_elements_draft_path() if _elements_draft_path().is_file() else ELEMENTS_PATH)
    elements = Elements156.load(source, ELEMENTS_CONFIG_PATH)
    item_list = elements.list_named(EQUIPMENT_TYPES[type_key]["suffix"])
    if not 0 <= int(row_index) < len(item_list.rows):
        raise ValueError("Index equipment tidak valid")
    return elements, item_list, item_list.rows[int(row_index)]


EQUIPMENT_COMMON_FIELDS = (
    "ID", "Name", "file_icon", "level", "require_level", "require_strength",
    "require_agility", "require_energy", "require_tili", "require_reputation",
    "price", "shop_price", "durability_min", "durability_max",
)


def _equipment_form_values(type_key, fields):
    definition = EQUIPMENT_TYPES[type_key]
    values = {
        "Name": fields.get("Name", [""])[0].strip(),
        "file_icon": fields.get("file_icon", [""])[0].strip(),
    }
    numeric = [field for field in EQUIPMENT_COMMON_FIELDS if field not in ("Name", "file_icon")]
    numeric.extend(field for field, _ in definition["stats"])
    for field in numeric:
        text = fields.get(field, [""])[0].strip()
        try:
            values[field] = int(text)
        except ValueError as error:
            raise ValueError(f"Field {field} harus berupa angka") from error
        if values[field] < 0 or values[field] > 2_147_483_647:
            raise ValueError(f"Field {field} di luar batas aman")
    if values["ID"] < 1 or not values["Name"]:
        raise ValueError("Equipment ID dan nama wajib diisi")
    return values


def _elements_id_exists(elements, item_id, except_list=None, except_index=None):
    equipment_suffixes = tuple(definition["suffix"] for definition in EQUIPMENT_TYPES.values())
    for item_list in elements.lists:
        if not item_list.name.endswith(equipment_suffixes):
            continue
        id_index = item_list.field_index("ID")
        for index, row in enumerate(item_list.rows):
            if item_list is except_list and index == except_index:
                continue
            if row[id_index] == item_id:
                return True
    return False


def build_equipment_draft(actor, type_key, row_index, fields, clone=False):
    elements, item_list, source_row = equipment_item(type_key, row_index)
    values = _equipment_form_values(type_key, fields)
    if _elements_id_exists(elements, values["ID"], None if clone else item_list,
                           None if clone else int(row_index)):
        raise ValueError(f'ID {values["ID"]} sudah digunakan dalam elements.data')
    if clone:
        target_index, row = elements.clone_row(EQUIPMENT_TYPES[type_key]["suffix"], int(row_index))
    else:
        target_index, row = int(row_index), source_row
    editable = list(EQUIPMENT_COMMON_FIELDS) + [field for field, _ in EQUIPMENT_TYPES[type_key]["stats"]]
    for field in editable:
        index = item_list.field_index(field)
        type_name = item_list.types[index]
        value = values[field]
        row[index] = Elements156.encoded_text(value, type_name) if isinstance(value, str) else value
    path = _elements_draft_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    elements.save(path)
    verified, verified_list, verified_row = equipment_item(type_key, target_index, path)
    if int(verified_row[verified_list.field_index("ID")]) != values["ID"]:
        raise ValueError("Verifikasi draft equipment gagal")
    now = int(time.time())
    manifest = {
        "created_at": now, "actor_id": int(actor[0]), "actor": actor[1],
        "operation": "clone" if clone else "edit", "type": type_key,
        "source_index": int(row_index), "target_index": target_index,
        "item_id": values["ID"], "name": values["Name"],
        "record_count": len(verified_list.rows), "source_sha256": _sha256_file(ELEMENTS_PATH),
        "draft_sha256": _sha256_file(path), "status": "draft",
    }
    (path.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def equipment_draft_manifest():
    path = _elements_draft_path().parent / "manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _elements_draft_path():
    return DATA_EDITOR_DIR / "elements" / "draft" / "elements.data"


def _service_draft_path():
    return _elements_draft_path()


def load_service_elements(use_draft=True):
    path = _elements_draft_path() if use_draft and _elements_draft_path().is_file() else ELEMENTS_PATH
    return Elements156.load(path, ELEMENTS_CONFIG_PATH), path


def npc_service_catalog(search="", limit=100):
    elements, _ = load_service_elements()
    item_list = elements.list_named("NPC_ESSENCE")
    query = search.strip()[:80].casefold()
    rows = []
    for index, row in enumerate(item_list.rows):
        npc_id = int(service_value(elements, item_list, row, "ID"))
        name = str(service_value(elements, item_list, row, "Name"))
        sell_id = int(service_value(elements, item_list, row, "id_sell_service"))
        make_id = int(service_value(elements, item_list, row, "id_make_service"))
        if query and query not in name.casefold() and query != str(npc_id):
            continue
        rows.append({"index": index, "id": npc_id, "name": name,
                     "sell_service": sell_id, "make_service": make_id})
        if len(rows) >= max(1, min(int(limit), 100)):
            break
    return {"version": elements.version, "total": len(item_list.rows), "rows": rows,
            "draft": service_draft_manifest()}


def npc_service_detail(npc_id):
    elements, source_path = load_service_elements()
    npc = npc_record(elements, npc_id)
    merchant, crafting = [], []
    sell_id = npc["services"]["id_sell_service"]
    if sell_id:
        _, _, _, slots = service_slots(elements, "NPC_SELL_SERVICE", sell_id)
        for slot in slots:
            try:
                item = item_summary(elements, slot["id"])
            except ValueError:
                item = {"id": slot["id"], "name": f'Item {slot["id"]}', "price": None}
            merchant.append({**slot, **item})
    make_id = npc["services"]["id_make_service"]
    if make_id:
        _, _, _, slots = service_slots(elements, "NPC_MAKE_SERVICE", make_id)
        for slot in slots:
            try:
                recipe = recipe_record(elements, slot["id"])
                for target in recipe["targets"]:
                    try:
                        target["item"] = item_summary(elements, target["item_id"])
                    except ValueError:
                        target["item"] = {"id": target["item_id"], "name": f'Item {target["item_id"]}'}
                for material in recipe["materials"]:
                    try:
                        material["item"] = item_summary(elements, material["item_id"])
                    except ValueError:
                        material["item"] = {"id": material["item_id"], "name": f'Item {material["item_id"]}'}
                crafting.append({**slot, "recipe": recipe})
            except ValueError:
                crafting.append({**slot, "recipe": None})
    return {"elements": elements, "source_path": source_path, "npc": npc,
            "merchant": merchant, "crafting": crafting}


def _save_service_draft(elements, actor, operation, details):
    path = _service_draft_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    elements.save(path)
    verified = Elements156.load(path, ELEMENTS_CONFIG_PATH)
    now = int(time.time())
    manifest = {
        "created_at": now, "actor_id": int(actor[0]), "actor": actor[1],
        "operation": operation, "details": details,
        "source_sha256": _sha256_file(ELEMENTS_PATH), "draft_sha256": _sha256_file(path),
        "version": verified.version, "status": "draft",
    }
    (path.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def service_draft_manifest():
    path = _service_draft_path().parent / "manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def build_merchant_service_draft(actor, npc_id, page, slot, item_id, price, delete=False):
    detail = npc_service_detail(npc_id)
    elements, npc = detail["elements"], detail["npc"]
    sell_id = npc["services"]["id_sell_service"]
    if not sell_id:
        raise ValueError("NPC ini belum memiliki Sell Service")
    if delete:
        set_service_slot(elements, "NPC_SELL_SERVICE", sell_id, page, slot, 0)
        description = {"npc_id": npc_id, "service_id": sell_id, "page": page,
                       "slot": slot, "action": "delete"}
    else:
        item_summary(elements, item_id)
        set_service_slot(elements, "NPC_SELL_SERVICE", sell_id, page, slot, item_id)
        set_item_price(elements, item_id, price)
        description = {"npc_id": npc_id, "service_id": sell_id, "page": page,
                       "slot": slot, "item_id": item_id, "price": price, "action": "save"}
    return _save_service_draft(elements, actor, "merchant.item", description)


def build_recipe_service_draft(actor, npc_id, recipe_id, fields):
    detail = npc_service_detail(npc_id)
    elements, npc = detail["elements"], detail["npc"]
    make_id = npc["services"]["id_make_service"]
    if not make_id:
        raise ValueError("NPC ini belum memiliki Make Service")
    _, _, _, slots = service_slots(elements, "NPC_MAKE_SERVICE", make_id)
    if int(recipe_id) not in {entry["id"] for entry in slots}:
        raise ValueError("Recipe tidak terhubung ke Make Service NPC ini")
    recipe_list, _, row = row_by_id(elements, "RECIPE_ESSENCE", recipe_id)
    recipe_name = fields.get("Name", [""])[0].strip()
    if not recipe_name:
        raise ValueError("Nama recipe wajib diisi")
    set_value(elements, recipe_list, row, "Name", recipe_name)
    numeric_fields = ("targets_1_id_to_make", "num_to_make", "price", "duration")
    for field in numeric_fields:
        try:
            number = int(fields.get(field, [""])[0])
        except ValueError as error:
            raise ValueError(f"Field {field} harus berupa angka") from error
        if number < 0 or number > 2_147_483_647:
            raise ValueError(f"Field {field} tidak valid")
        set_value(elements, recipe_list, row, field, number)
    try:
        probability = float(fields.get("targets_1_probability", [""])[0])
    except ValueError as error:
        raise ValueError("Peluang hasil harus berupa angka") from error
    if not 0 <= probability <= 1:
        raise ValueError("Peluang hasil harus antara 0 dan 1")
    set_value(elements, recipe_list, row, "targets_1_probability", probability)
    for number in range(1, 33):
        try:
            material_id = int(fields.get(f"material_{number}_id", ["0"])[0] or 0)
            amount = int(fields.get(f"material_{number}_amount", ["0"])[0] or 0)
        except ValueError as error:
            raise ValueError(f"Bahan slot {number} tidak valid") from error
        if material_id:
            item_summary(elements, material_id)
        set_recipe_material(elements, recipe_id, number, material_id, amount)
    return _save_service_draft(elements, actor, "craft.recipe", {
        "npc_id": int(npc_id), "service_id": make_id, "recipe_id": int(recipe_id),
        "output_item_id": int(fields["targets_1_id_to_make"][0]),
    })


def build_make_service_slot_draft(actor, npc_id, page, slot, fields, detach=False):
    detail = npc_service_detail(npc_id)
    elements, npc = detail["elements"], detail["npc"]
    make_id = npc["services"]["id_make_service"]
    if not make_id:
        raise ValueError("NPC ini belum memiliki Make Service")
    if detach:
        set_service_slot(elements, "NPC_MAKE_SERVICE", make_id, page, slot, 0)
        details = {"npc_id": int(npc_id), "service_id": make_id, "page": page,
                   "slot": slot, "action": "detach"}
    else:
        try:
            source_recipe_id = int(fields.get("source_recipe_id", [""])[0])
            new_recipe_id = int(fields.get("new_recipe_id", [""])[0])
        except ValueError as error:
            raise ValueError("Recipe ID sumber/baru tidak valid") from error
        name = fields.get("new_recipe_name", [""])[0].strip()
        if not name or source_recipe_id < 1 or new_recipe_id < 1:
            raise ValueError("Recipe ID dan nama wajib diisi")
        clone_record(elements, "RECIPE_ESSENCE", source_recipe_id, new_recipe_id, name)
        set_service_slot(elements, "NPC_MAKE_SERVICE", make_id, page, slot, new_recipe_id)
        details = {"npc_id": int(npc_id), "service_id": make_id, "page": page,
                   "slot": slot, "source_recipe_id": source_recipe_id,
                   "new_recipe_id": new_recipe_id, "name": name, "action": "clone"}
    return _save_service_draft(elements, actor, "craft.slot", details)


def build_npc_clone_draft(actor, source_npc_id, fields):
    elements, _ = load_service_elements()
    try:
        new_npc_id = int(fields.get("new_npc_id", [""])[0])
        sell_text = fields.get("new_sell_service_id", [""])[0].strip()
        make_text = fields.get("new_make_service_id", [""])[0].strip()
        new_sell = int(sell_text) if sell_text else None
        new_make = int(make_text) if make_text else None
    except ValueError as error:
        raise ValueError("ID duplikasi NPC/service tidak valid") from error
    name = fields.get("new_name", [""])[0].strip()
    if not name or new_npc_id < 1:
        raise ValueError("NPC ID dan nama baru wajib diisi")
    clone = clone_npc_bundle(elements, source_npc_id, new_npc_id, name, new_sell, new_make)
    return _save_service_draft(elements, actor, "npc.clone", {
        "source_npc_id": int(source_npc_id), "new_npc_id": new_npc_id,
        "name": name, "sell_service_id": new_sell, "make_service_id": new_make,
        "services": clone["services"],
    })


def validate_registration(username, password, confirmation):
    if not USERNAME_RE.fullmatch(username):
        return "Username harus 3–20 karakter: huruf, angka, atau underscore."
    if not PASSWORD_RE.fullmatch(password):
        return "Sandi harus 6–32 karakter: huruf, angka, _, titik, atau tanda hubung."
    if password != confirmation:
        return "Konfirmasi sandi tidak sama."
    return None


def sql_literal(value):
    """Defence-in-depth; input juga dibatasi regex sebelum mencapai fungsi ini."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def run_db(query, timeout=5):
    result = subprocess.run(
        ["/usr/bin/mariadb", f"--defaults-extra-file={DB_CONFIG}",
         "--batch", "--skip-column-names"],
        input=query, text=True, capture_output=True, timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Database portal tidak tersedia")
    return result.stdout.splitlines()


def hash_web_password(password, salt=None):
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt_bytes,
                            n=16384, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt_bytes).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_web_password(password, encoded):
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error, OverflowError):
        return False


def game_password_hash(username, password):
    digest = hashlib.md5((username.lower() + password).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def portal_identity(username):
    rows = run_db(
        "SELECT account_id, username, password_hash FROM pw_portal.accounts "
        f"WHERE username = {sql_literal(username.lower())} LIMIT 1;"
    )
    if not rows:
        return None
    account_id, account_name, password_hash = rows[0].split("\t", 2)
    return int(account_id), account_name, password_hash


def save_portal_identity(account_id, username, password_hash):
    run_db(
        "INSERT IGNORE INTO pw_portal.accounts(account_id, username, password_hash) VALUES ("
        f"{int(account_id)}, {sql_literal(username.lower())}, {sql_literal(password_hash)});"
    )


def authenticate_account(username, password):
    account = username.lower()
    identity = portal_identity(account)
    if identity:
        account_id, account_name, password_hash = identity
        if not verify_web_password(password, password_hash):
            return None
    else:
        legacy_hash = game_password_hash(account, password)
        rows = run_db(
            f"CALL pw_portal.verify_game_login({sql_literal(account)}, "
            f"{sql_literal(legacy_hash)});"
        )
        if not rows:
            return None
        account_id, account_name = rows[-1].split("\t", 1)
        account_id = int(account_id)
        save_portal_identity(account_id, account_name, hash_web_password(password))
    run_db(
        "UPDATE pw_portal.accounts SET last_login_at=CURRENT_TIMESTAMP WHERE "
        f"account_id={int(account_id)};"
    )
    return account_id, account_name


def change_account_password(account_id, username, new_password):
    modern_hash = hash_web_password(new_password)
    legacy_hash = game_password_hash(username, new_password)
    run_db(
        f"CALL pw_portal.change_game_password({int(account_id)}, "
        f"{sql_literal(legacy_hash)});\n"
        "UPDATE pw_portal.accounts SET "
        f"password_hash={sql_literal(modern_hash)} WHERE account_id={int(account_id)};"
    )


def account_profile(account_id):
    rows = run_db(
        "SELECT u.ID, u.name, DATE_FORMAT(u.creatime, '%Y-%m-%d %H:%i'), "
        "COALESCE(DATE_FORMAT(MAX(p.lastlogin), '%Y-%m-%d %H:%i'), '-') "
        "FROM pw.users u LEFT JOIN pw.point p ON p.uid=u.ID "
        f"WHERE u.ID={int(account_id)} GROUP BY u.ID,u.name,u.creatime;"
    )
    if not rows:
        return None
    account_value, username, created_at, last_game_login = rows[0].split("\t", 3)
    return {
        "account_id": int(account_value), "username": username,
        "created_at": created_at, "last_game_login": last_game_login,
    }


def account_characters(account_id):
    rows = run_db(
        "SELECT role_id, role_name, role_level, role_occupation, role_gender, "
        "faction_name FROM pw.roles "
        f"WHERE account_id={int(account_id)} ORDER BY role_level DESC, role_name;"
    )
    characters = []
    for row in rows:
        role_id, name, level, occupation, gender, faction = row.split("\t", 5)
        characters.append({
            "role_id": int(role_id), "name": name, "level": int(level),
            "class_name": CLASS_NAMES.get(int(occupation), f"Class {occupation}"),
            "gender": "Pria" if int(gender) == 0 else "Wanita",
            "faction": faction or "-",
        })
    return characters


def ranking_characters(limit=100):
    safe_limit = min(max(int(limit), 1), 100)
    rows = run_db(
        "SELECT role_name, role_level, role_occupation, faction_name "
        "FROM pw.roles ORDER BY role_level DESC, role_name ASC "
        f"LIMIT {safe_limit};"
    )
    ranking = []
    for position, row in enumerate(rows, 1):
        name, level, occupation, faction = row.split("\t", 3)
        ranking.append({
            "position": position, "name": name, "level": int(level),
            "class_name": CLASS_NAMES.get(int(occupation), f"Class {occupation}"),
            "faction": faction or "-",
        })
    return ranking


def is_panel_admin(account_id):
    rows = run_db(
        "SELECT COUNT(*) FROM pw_portal.admins "
        f"WHERE account_id={int(account_id)};"
    )
    return rows == ["1"]


def admin_accounts(search=""):
    search = search.strip().lower()
    if not ADMIN_SEARCH_RE.fullmatch(search):
        return []
    where = ""
    if search:
        where = f"WHERE LOWER(u.name) LIKE {sql_literal('%' + search + '%')} "
    rows = run_db(
        "SELECT u.ID, u.name, DATE_FORMAT(u.creatime, '%Y-%m-%d %H:%i'), "
        "COUNT(DISTINCT r.role_id), COALESCE(MAX(r.role_level), 0), "
        "COUNT(DISTINCT a.rid) FROM pw.users u "
        "LEFT JOIN pw.roles r ON r.account_id=u.ID "
        "LEFT JOIN pw.auth a ON a.userid=u.ID AND a.zoneid=1 "
        f"{where}GROUP BY u.ID,u.name,u.creatime ORDER BY u.ID DESC LIMIT 100;"
    )
    accounts = []
    for row in rows:
        account_id, username, created_at, character_count, max_level, gm_permissions = row.split("\t", 5)
        accounts.append({
            "account_id": int(account_id), "username": username,
            "created_at": created_at, "character_count": int(character_count),
            "max_level": int(max_level), "gm_permissions": int(gm_permissions),
        })
    return accounts


def admin_totals():
    rows = run_db(
        "SELECT (SELECT COUNT(*) FROM pw.users), "
        "(SELECT COUNT(*) FROM pw.roles), "
        "(SELECT COUNT(DISTINCT userid) FROM pw.auth WHERE zoneid=1);"
    )
    total_accounts, total_characters, total_gm = rows[0].split("\t", 2)
    return int(total_accounts), int(total_characters), int(total_gm)


def admin_audit(limit=30):
    rows = run_db(
        "SELECT DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s'), actor_username, "
        "action_name, target_username, details FROM pw_portal.audit_log "
        f"ORDER BY id DESC LIMIT {min(max(int(limit), 1), 100)};"
    )
    return [dict(zip(
        ("created_at", "actor", "action", "target", "details"),
        row.split("\t", 4),
    )) for row in rows]


def set_account_gm(actor_id, target_id, enabled, client_ip):
    action = 1 if enabled else 0
    run_db(
        f"CALL pw_portal.set_full_gm({int(actor_id)}, {int(target_id)}, "
        f"{action}, {sql_literal(client_ip[:45])});"
    )


def grant_boutique_gold(actor_id, target_username, gold, client_ip):
    rows = run_db(
        f"CALL pw_portal.grant_boutique_gold({int(actor_id)}, "
        f"{sql_literal(target_username.lower())}, {int(gold)}, "
        f"{sql_literal(client_ip[:45])});"
    )
    if not rows:
        raise RuntimeError("Database tidak mengonfirmasi kiriman Gold")
    target_id, username, gold_value, cash_value = rows[-1].split("\t", 3)
    return {"account_id": int(target_id), "username": username,
            "gold": int(gold_value), "cash": int(cash_value)}


def boutique_pending(limit=20):
    rows = run_db(
        "SELECT u.name, n.cash DIV 100, n.status, "
        "DATE_FORMAT(n.creatime, '%Y-%m-%d %H:%i') FROM pw.usecashnow n "
        "INNER JOIN pw.users u ON u.ID=n.userid WHERE n.zoneid=1 AND n.sn>=0 "
        f"ORDER BY n.creatime DESC LIMIT {min(max(int(limit), 1), 50)};"
    )
    return [dict(zip(("username", "gold", "status", "created_at"),
                     row.split("\t", 3))) for row in rows]


def published_news(limit=20):
    rows = run_db(
        "SELECT id, title, REPLACE(TO_BASE64(body), CHAR(10), ''), "
        "DATE_FORMAT(published_at, '%Y-%m-%d'), author_username "
        "FROM pw_portal.news WHERE status='published' AND published_at<=CURRENT_TIMESTAMP "
        f"ORDER BY published_at DESC, id DESC LIMIT {min(max(int(limit), 1), 50)};"
    )
    news = []
    for row in rows:
        news_id, title, encoded_body, date, author = row.split("\t", 4)
        body = base64.b64decode(encoded_body).decode("utf-8")
        news.append({"id": news_id, "title": title, "body": body,
                     "date": date, "author": author})
    return news


def admin_news(limit=50):
    rows = run_db(
        "SELECT id, title, status, DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i'), "
        "COALESCE(DATE_FORMAT(published_at, '%Y-%m-%d %H:%i'), '-') "
        "FROM pw_portal.news ORDER BY updated_at DESC, id DESC "
        f"LIMIT {min(max(int(limit), 1), 100)};"
    )
    return [dict(zip(("id", "title", "status", "updated_at", "published_at"),
                     row.split("\t", 4))) for row in rows]


def admin_news_item(news_id):
    rows = run_db(
        "SELECT id, title, REPLACE(TO_BASE64(body), CHAR(10), ''), status FROM pw_portal.news "
        f"WHERE id={int(news_id)} LIMIT 1;"
    )
    if not rows:
        return None
    news_value, title, encoded_body, status = rows[0].split("\t", 3)
    body = base64.b64decode(encoded_body).decode("utf-8")
    return {"id": int(news_value), "title": title, "body": body, "status": status}


def save_news(actor_id, news_id, title, body, published, client_ip):
    rows = run_db(
        f"CALL pw_portal.save_news({int(actor_id)}, {int(news_id)}, "
        f"{sql_literal(title)}, {sql_literal(body)}, {1 if published else 0}, "
        f"{sql_literal(client_ip[:45])});"
    )
    return int(rows[-1]) if rows else 0


def set_news_status(actor_id, news_id, status, client_ip):
    if status not in ("published", "archived"):
        raise ValueError("Status berita tidak valid")
    run_db(
        f"CALL pw_portal.set_news_status({int(actor_id)}, {int(news_id)}, "
        f"{sql_literal(status)}, {sql_literal(client_ip[:45])});"
    )


def monitor_state():
    fallback = {"checked_at": "Belum tersedia", "online": 0, "total": 0,
                "services": [], "events": []}
    try:
        status = json.loads((MONITOR_DIR / "status.json").read_text(encoding="utf-8"))
        events = json.loads((MONITOR_DIR / "events.json").read_text(encoding="utf-8"))
        services = status.get("services", [])
        if not isinstance(services, list) or not isinstance(events, list):
            return fallback
        return {
            "checked_at": str(status.get("checked_at", "-")),
            "online": int(status.get("online", 0)),
            "total": int(status.get("total", len(services))),
            "services": services,
            "events": list(reversed(events[-30:])),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def support_monitor_state(monitor):
    """Exclude game daemons and maps already shown by Map Control."""
    optional_names = {"pw155-host-export.timer"}
    services = [
        item for item in monitor.get("services", [])
        if item.get("kind") != "daemon" and str(item.get("name", "")) not in optional_names
    ]
    hidden_names = {
        str(item.get("name", ""))
        for item in monitor.get("services", [])
        if item.get("kind") == "daemon" or str(item.get("name", "")) in optional_names
    }
    events = [
        item for item in monitor.get("events", [])
        if str(item.get("service", "")) not in hidden_names
    ]
    return {
        "checked_at": str(monitor.get("checked_at", "-")),
        "online": sum(1 for item in services if item.get("running")),
        "total": len(services),
        "services": services,
        "events": events,
    }


def map_control_state():
    """Read the root worker's sanitized catalog and current map state."""
    fallback_catalog = {"max_selection": 6, "maps": [
        {"alias": "gs01", "label": "World Utama", "kind": "world",
         "tag": 1, "config": "world", "recommended": True},
        {"alias": "is61", "label": "Celestial Vale", "kind": "instance",
         "tag": 161, "config": "a61", "recommended": True},
    ]}
    try:
        catalog = json.loads((MAP_CONTROL_DIR / "catalog.json").read_text(encoding="utf-8"))
        status = json.loads((MAP_CONTROL_DIR / "status.json").read_text(encoding="utf-8"))
        if not isinstance(catalog.get("maps"), list) or not isinstance(status, dict):
            raise ValueError("Format status map tidak valid")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        catalog, status = fallback_catalog, {
            "checked_at": "Belum tersedia", "busy": False, "active": [],
            "maps": {}, "core": {}, "core_active": [], "core_online": False,
            "core_total": 8, "last_action": None,
        }
    return {
        "catalog": catalog["maps"],
        "max_selection": max(1, min(int(catalog.get("max_selection", 6)), 6)),
        "checked_at": str(status.get("checked_at", "-")),
        "busy": bool(status.get("busy")),
        "active": list(status.get("active", [])),
        "states": status.get("maps", {}) if isinstance(status.get("maps", {}), dict) else {},
        "core": status.get("core", {}) if isinstance(status.get("core", {}), dict) else {},
        "core_active": list(status.get("core_active", [])),
        "core_online": bool(status.get("core_online", False)),
        "core_total": max(1, int(status.get("core_total", 8))),
        "last_action": status.get("last_action") if isinstance(status.get("last_action"), dict) else None,
    }


def queue_map_action(account, action, aliases, client_ip):
    """Queue an allowlisted request; the web process never executes shell commands."""
    state = map_control_state()
    allowed = {item.get("alias") for item in state["catalog"]}
    selected = list(dict.fromkeys(str(item) for item in aliases))
    if action not in ("start", "stop", "start-core", "stop-core"):
        raise ValueError("Aksi server tidak valid")
    if action in ("start-core", "stop-core") and selected:
        raise ValueError("Aksi daemon inti tidak menerima pilihan map")
    if action in ("start", "stop") and not 1 <= len(selected) <= state["max_selection"]:
        raise ValueError(f'Pilih 1 sampai {state["max_selection"]} map')
    if action in ("start", "stop") and any(
            not re.fullmatch(r"(?:gs|is|arena|bg|ms|rand)[0-9]{2}", item)
            or item not in allowed for item in selected):
        raise ValueError("Daftar map berisi alias yang tidak diizinkan")
    if state["busy"]:
        raise ValueError("Aksi map sebelumnya masih berjalan")
    request_id = secrets.token_hex(12)
    request = {
        "id": request_id, "action": action, "maps": selected,
        "actor_id": int(account[0]), "actor": str(account[1])[:20],
        "client_ip": str(client_ip)[:45],
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    request_dir = MAP_CONTROL_DIR / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    if next(request_dir.glob("*.json"), None) is not None:
        raise ValueError("Masih ada permintaan map dalam antrean")
    path = request_dir / f"{request_id}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o640)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(request, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    return request


def backup_control_state():
    """Read only sanitized backup metadata prepared by the root worker."""
    fallback = {"busy": False, "updated_at": "Belum tersedia", "backups": [],
                "last_action": None, "error": None}
    try:
        status = json.loads((BACKUP_CONTROL_DIR / "status.json").read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            raise ValueError("Format status backup tidak valid")
        backups = []
        for item in status.get("backups", []):
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename", ""))
            if not BACKUP_FILENAME_RE.fullmatch(filename):
                continue
            backups.append({
                "filename": filename,
                "created_at": str(item.get("created_at", "-"))[:32],
                "bytes": max(0, int(item.get("bytes", 0))),
            })
        return {
            "busy": bool(status.get("busy")),
            "updated_at": str(status.get("updated_at", "-"))[:40],
            "backups": backups[:30],
            "last_action": status.get("last_action")
            if isinstance(status.get("last_action"), dict) else None,
            "error": str(status.get("error"))[:1200] if status.get("error") else None,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def queue_backup_action(account, action, client_ip):
    """Queue a fixed backup action; the web process cannot execute root commands."""
    if action != "create":
        raise ValueError("Aksi backup tidak valid")
    state = backup_control_state()
    if state["busy"]:
        raise ValueError("Backup lain masih sedang dibuat")
    request_dir = BACKUP_CONTROL_DIR / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    if next(request_dir.glob("*.json"), None) is not None:
        raise ValueError("Permintaan backup sudah berada dalam antrean")
    request_id = secrets.token_hex(12)
    request = {
        "id": request_id, "action": "create",
        "actor_id": int(account[0]), "actor": str(account[1])[:20],
        "client_ip": str(client_ip)[:45],
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = request_dir / f"{request_id}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(request, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    return request


def resolve_backup_download(filename):
    """Resolve only a regular allowlisted archive inside the backup download directory."""
    if not BACKUP_FILENAME_RE.fullmatch(filename or ""):
        return None
    root = (BACKUP_CONTROL_DIR / "files").resolve()
    candidate = root / filename
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.resolve().parent != root:
            return None
    except OSError:
        return None
    return candidate


def cpw_control_state():
    """Read status prepared by the privileged ASP CPW worker."""
    fallback = {"busy": False, "updated_at": "Not available", "releases": [],
                "current": None, "counts": {item: 0 for item in ("element", "launcher", "patcher")},
                "total": 0, "verification": {}, "error": None}
    try:
        status = json.loads((CPW_CONTROL_DIR / "status.json").read_text(encoding="utf-8"))
        snapshot = json.loads((CPW_CONTROL_DIR / "snapshot.json").read_text(encoding="utf-8"))
        if not isinstance(status, dict) or not isinstance(snapshot, dict):
            raise ValueError("Invalid ASP CPW status")
        result = dict(fallback)
        result.update(snapshot)
        result.update({key: status.get(key, result.get(key))
                       for key in ("busy", "updated_at", "error")})
        if isinstance(status.get("last_result"), dict):
            result["last_result"] = status["last_result"]
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def queue_cpw_action(account, action, release, client_ip):
    """Queue only fixed CPW actions; no command text reaches the root worker."""
    if action not in ("publish", "verify", "rollback"):
        raise ValueError("Invalid patch action")
    if action == "rollback":
        if not re.fullmatch(r"[A-Za-z0-9._-]+", release or ""):
            raise ValueError("Invalid release")
    elif release:
        raise ValueError("This action does not accept a release")
    state = cpw_control_state()
    if state.get("busy"):
        raise ValueError("Another patch operation is still running")
    request_dir = CPW_CONTROL_DIR / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    if next(request_dir.glob("*.json"), None) is not None:
        raise ValueError("A patch request is already queued")
    request_id = secrets.token_hex(12)
    request = {"id": request_id, "action": action, "release": release,
               "actor_id": int(account[0]), "actor": str(account[1])[:20],
               "client_ip": str(client_ip)[:45],
               "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = request_dir / f"{request_id}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(request, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    return request


def render_patch_manager(account, state, message="", level=""):
    template = (BASE_DIR / "patch_manager.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    counts = state.get("counts", {}) if isinstance(state.get("counts"), dict) else {}
    verification = state.get("verification", {}) if isinstance(state.get("verification"), dict) else {}
    version_rows = []
    for kind in ("element", "launcher", "patcher"):
        item = verification.get(kind, {}) if isinstance(verification.get(kind), dict) else {}
        version_rows.append(f'<tr><td>{kind.title()}</td><td>{html.escape(str(item.get("version", "-")))}</td>'
                            f'<td>{html.escape(str(item.get("files", "-")))}</td></tr>')
    releases = []
    for name in state.get("releases", []) if isinstance(state.get("releases"), list) else []:
        escaped = html.escape(str(name))
        current = str(name) == str(state.get("current"))
        action = "" if current else (f'<form method="post" action="/admin/patch/action" class="inline-form">'
                 f'<input type="hidden" name="csrf" value="{html.escape(token, quote=True)}">'
                 f'<input type="hidden" name="release" value="{html.escape(str(name), quote=True)}">'
                 '<button class="small-button danger" name="action" value="rollback">Rollback</button></form>')
        releases.append(f'<tr><td><strong>{escaped}{" · CURRENT" if current else ""}</strong></td><td>{action}</td></tr>')
    notice = f'<div class="notice {html.escape(level)}">{html.escape(message)}</div>' if message else ""
    replacements = {
        "{{CSRF}}": html.escape(token, quote=True), "{{USERNAME}}": html.escape(str(account[1])),
        "{{NOTICE}}": notice, "{{BUSY}}": "BUSY" if state.get("busy") else "READY",
        "{{UPDATED}}": html.escape(str(state.get("updated_at", "-"))),
        "{{CURRENT}}": html.escape(str(state.get("current") or "No release published")),
        "{{TOTAL}}": str(int(state.get("total", 0) or 0)),
        "{{ELEMENT_COUNT}}": str(int(counts.get("element", 0) or 0)),
        "{{LAUNCHER_COUNT}}": str(int(counts.get("launcher", 0) or 0)),
        "{{PATCHER_COUNT}}": str(int(counts.get("patcher", 0) or 0)),
        "{{VERSION_ROWS}}": "".join(version_rows),
        "{{RELEASE_ROWS}}": "".join(releases) or '<tr><td colspan="2">No release yet.</td></tr>',
        "{{ERROR}}": html.escape(str(state.get("error") or "None")),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def create_game_account(username, password):
    account = username.lower()
    account_sql = sql_literal(account)
    password_sql = sql_literal(password)
    query = f"""
SET @account_name = {account_sql};
SET @account_password = {password_sql};
SET @account_hash = TO_BASE64(UNHEX(MD5(CONCAT(@account_name, @account_password))));
CALL pw.adduser(
  @account_name, @account_hash, '0', '0', 'Web Registration', '0', '',
  '0', '0', '0', '0', '0', '0', '0', '1990-01-01', '0', @account_hash
);
SELECT 'CREATED', id, name FROM pw.users WHERE LOWER(name) = @account_name;
"""

    # Periksa keberadaan terlebih dahulu agar stored procedure tidak dipanggil
    # untuk username yang sudah ada.
    check = run_db(
        f"SELECT COUNT(*) FROM pw.users WHERE LOWER(name) = {account_sql};"
    )
    if check != ["0"]:
        return "exists", None

    result = run_db(query, timeout=8)
    created = next((line for line in result
                    if line.startswith("CREATED\t")), None)
    if not created:
        raise RuntimeError("Database tidak mengonfirmasi akun baru")
    _, account_id, account_name = created.split("\t", 2)
    save_portal_identity(int(account_id), account_name,
                         hash_web_password(password))
    return "created", account


def tcp_available(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return True
    except OSError:
        return False


def server_status():
    services = {name: tcp_available(port) for name, port in SERVER_PORTS.items()}
    return {
        "online": all(services.values()),
        "services": services,
        "checked_at": int(time.time()),
    }


def csrf_signature(token):
    return hmac.new(CSRF_SECRET.encode(), token.encode(), "sha256").hexdigest()


def new_csrf_token():
    token = secrets.token_urlsafe(24)
    return token + "." + csrf_signature(token)


def valid_csrf_token(value):
    try:
        token, supplied = value.rsplit(".", 1)
    except ValueError:
        return False
    expected = csrf_signature(token)
    return bool(token) and hmac.compare_digest(supplied, expected)


def make_session(account_id, username):
    payload = json.dumps({
        "id": int(account_id), "username": username,
        "expires": int(time.time()) + SESSION_TTL,
        "nonce": secrets.token_hex(8),
    }, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(CSRF_SECRET.encode(), encoded.encode(), "sha256").hexdigest()
    return encoded + "." + signature


def parse_session(value):
    try:
        encoded, supplied = value.rsplit(".", 1)
        expected = hmac.new(CSRF_SECRET.encode(), encoded.encode(), "sha256").hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(
            encoded + "=" * (-len(encoded) % 4)).decode())
        if int(payload["expires"]) < int(time.time()):
            return None
        if not USERNAME_RE.fullmatch(payload["username"]):
            return None
        return int(payload["id"]), payload["username"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error):
        return None


def notice_html(message, level):
    if not message:
        return ""
    return (f'<div class="notice {html.escape(level)}" role="status">'
            f'{html.escape(message)}</div>')


def render_home(message="", level="", username=""):
    template = (BASE_DIR / "index.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    notice = notice_html(message, level)
    return (template.replace("{{NOTICE}}", notice)
            .replace("{{USERNAME}}", html.escape(username, quote=True))
            .replace("{{CSRF}}", html.escape(token, quote=True))), token


def render_login(message="", username=""):
    template = (BASE_DIR / "login.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    return (template.replace("{{NOTICE}}", notice_html(message, "error"))
            .replace("{{USERNAME}}", html.escape(username, quote=True))
            .replace("{{CSRF}}", html.escape(token, quote=True))), token


def render_ranking(ranking):
    template = (BASE_DIR / "ranking.html").read_text(encoding="utf-8")
    podium = []
    for item in ranking[:3]:
        podium.append(
            f'<article class="podium-card rank-{item["position"]}">'
            f'<span>RANK #{item["position"]}</span>'
            f'<h3>{html.escape(item["name"])}</h3>'
            f'<p>Level {item["level"]} · {html.escape(item["class_name"])}</p>'
            f'<small>Faction: {html.escape(item["faction"])}</small>'
            '</article>'
        )
    rows = []
    for item in ranking:
        rows.append(
            '<tr>'
            f'<td><strong>#{item["position"]}</strong></td>'
            f'<td><strong>{html.escape(item["name"])}</strong></td>'
            f'<td>{html.escape(item["class_name"])}</td>'
            f'<td><span class="level-pill">LVL {item["level"]}</span></td>'
            f'<td>{html.escape(item["faction"])}</td>'
            '</tr>'
        )
    empty = '<div class="empty-state"><strong>Belum ada karakter dalam ranking</strong><p>Data akan tampil setelah sinkronisasi karakter berikutnya.</p></div>'
    replacements = {
        "{{PODIUM}}": "".join(podium) if podium else empty,
        "{{RANKING_ROWS}}": "".join(rows) if rows else '<tr><td colspan="5" class="table-empty">Belum ada karakter.</td></tr>',
        "{{CHARACTER_COUNT}}": str(len(ranking)),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def format_news_date(value):
    months = ("", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
              "Juli", "Agustus", "September", "Oktober", "November", "Desember")
    try:
        year, month, day = (int(part) for part in value.split("-", 2))
        return f"{day} {months[month]} {year}"
    except (ValueError, IndexError, AttributeError):
        return "Tanggal tidak tersedia"


def render_news(news_items):
    template = (BASE_DIR / "news.html").read_text(encoding="utf-8")
    cards = []
    for position, item in enumerate(news_items):
        body = html.escape(item["body"]).replace("\n", "<br>")
        cards.append(
            f'<article class="content-card {"featured" if position == 0 else ""}">'
            f'<time datetime="{html.escape(item["date"], quote=True)}">'
            f'{html.escape(format_news_date(item["date"]))}</time>'
            f'<h2>{html.escape(item["title"])}</h2><p>{body}</p>'
            f'<small>Oleh {html.escape(item["author"])}</small></article>'
        )
    if not cards:
        cards.append('<div class="empty-state"><strong>Belum ada berita terbit</strong>'
                     '<p>Berita baru akan muncul setelah diterbitkan oleh admin.</p></div>')
    return template.replace("{{NEWS_CARDS}}", "".join(cards))


def load_download_manifest():
    """Load public metadata without trusting filenames from the request path."""
    try:
        data = json.loads((DOWNLOAD_DIR / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"downloads": []}
    downloads = data.get("downloads", [])
    return {"downloads": downloads if isinstance(downloads, list) else []}


def format_file_size(byte_count):
    try:
        size = max(0, int(byte_count))
    except (TypeError, ValueError):
        size = 0
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.2f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f} KB"
    return f"{size} B"


def resolve_download(filename, manifest=None):
    if not DOWNLOAD_FILENAME_RE.fullmatch(filename):
        return None
    for item in (manifest or load_download_manifest())["downloads"]:
        if item.get("available") is True and item.get("filename") == filename:
            candidate = DOWNLOAD_DIR / filename
            return candidate if candidate.is_file() else None
    return None


def resolve_patch_file(relative_path):
    """Resolve one CPW patch artifact without allowing traversal or arbitrary files."""
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        return None
    parts = relative_path.split("/")
    if any(not part or part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9+=_.-]+", part)
           for part in parts):
        return None
    if parts[0] == "info":
        allowed = parts == ["info", "pid"]
    elif parts[0] in ("element", "launcher", "patcher"):
        artifact = "/".join(parts[1:])
        allowed = bool(re.fullmatch(
            rf"(?:version|files[.]md5|v-[1-9][0-9]*[.]inc|{parts[0]}/[A-Za-z0-9+=_.-]+(?:/[A-Za-z0-9+=_.-]+)*)",
            artifact))
    else:
        allowed = False
    if not allowed:
        return None
    candidate = PATCH_DIR.joinpath(*parts)
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def render_downloads(manifest=None):
    template = (BASE_DIR / "downloads.html").read_text(encoding="utf-8")
    cards = []
    for item in (manifest or load_download_manifest())["downloads"]:
        title = html.escape(str(item.get("title", "Paket")))
        version = html.escape(str(item.get("version", "-")))
        description = html.escape(str(item.get("description", "")))
        digest = html.escape(str(item.get("sha256", "-")))
        size = format_file_size(item.get("bytes", 0))
        filename = str(item.get("filename") or "")
        available = item.get("available") is True and resolve_download(filename, manifest)
        action = (f'<a class="download-button" href="/downloads/file/{html.escape(filename, quote=True)}">Unduh ZIP</a>'
                  if available else '<span class="download-button disabled">Belum tersedia</span>')
        cards.append(
            f'<article class="download-card {"available" if available else "unavailable"}">'
            f'<span class="availability">{"TERSEDIA" if available else "BELUM DIDISTRIBUSIKAN"}</span>'
            f'<h2>{title}</h2><p>{description}</p>'
            f'<dl><div><dt>Versi</dt><dd>{version}</dd></div><div><dt>Ukuran</dt><dd>{size}</dd></div></dl>'
            f'<small>SHA-256</small><code class="checksum">{digest}</code>{action}</article>'
        )
    if not cards:
        cards.append('<div class="empty-state"><strong>Belum ada paket download</strong></div>')
    return template.replace("{{DOWNLOAD_CARDS}}", "".join(cards))


def render_panel(profile, characters, message="", level=""):
    template = (BASE_DIR / "panel.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    if characters:
        cards = []
        for character in characters:
            cards.append(
                '<article class="character-card">'
                f'<span class="character-level">LVL {character["level"]}</span>'
                f'<h3>{html.escape(character["name"])}</h3>'
                f'<p>{html.escape(character["class_name"])} · '
                f'{html.escape(character["gender"])}</p>'
                f'<small>Faction: {html.escape(character["faction"])}</small>'
                '</article>'
            )
        characters_html = "".join(cards)
        character_note = ""
    else:
        characters_html = (
            '<div class="empty-state"><strong>Cache karakter belum tersedia</strong>'
            '<p>Karakter game tersimpan aman di gamedbd. Adapter sinkronisasi '
            'akan ditambahkan pada tahap berikutnya.</p></div>'
        )
        character_note = '<span class="cache-state">Menunggu sinkronisasi</span>'
    output = template
    replacements = {
        "{{NOTICE}}": notice_html(message, level),
        "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(profile["username"]),
        "{{ACCOUNT_ID}}": str(profile["account_id"]),
        "{{CREATED_AT}}": html.escape(profile["created_at"]),
        "{{LAST_GAME_LOGIN}}": html.escape(profile["last_game_login"]),
        "{{CHARACTER_COUNT}}": str(len(characters)),
        "{{CHARACTERS}}": characters_html,
        "{{CHARACTER_NOTE}}": character_note,
        "{{ADMIN_LINK}}": (
            '<a class="text-link" href="/admin">Admin Panel</a>'
            if profile.get("is_admin") else ""
        ),
    }
    for key, value in replacements.items():
        output = output.replace(key, value)
    return output, token


def render_admin(account, accounts, totals, audit_rows, monitor, news_items,
                 editor=None, boutique_queue=None, maps=None, search="", message="", level="",
                 backups=None):
    template = (BASE_DIR / "admin.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    monitor = support_monitor_state(monitor)
    account_rows = []
    for item in accounts:
        enabled = item["gm_permissions"] > 0
        action = "revoke" if enabled else "grant"
        button_text = "Cabut GM" if enabled else "Jadikan GM"
        status = f'GM · {item["gm_permissions"]} izin' if enabled else "Player"
        account_rows.append(
            '<tr>'
            f'<td><strong>{html.escape(item["username"])}</strong><small>#{item["account_id"]}</small></td>'
            f'<td>{item["character_count"]}<small>Level tertinggi {item["max_level"]}</small></td>'
            f'<td><span class="role-state {"gm" if enabled else ""}">{status}</span></td>'
            f'<td>{html.escape(item["created_at"])}</td>'
            '<td><form method="post" action="/admin/gm" class="inline-form admin-action">'
            f'<input type="hidden" name="csrf" value="{html.escape(token, quote=True)}">'
            f'<input type="hidden" name="target_id" value="{item["account_id"]}">'
            f'<input type="hidden" name="action" value="{action}">'
            f'<input type="hidden" name="search" value="{html.escape(search, quote=True)}">'
            f'<button type="submit" class="small-button {"danger" if enabled else ""}">{button_text}</button>'
            '</form></td></tr>'
        )
    if not account_rows:
        account_rows.append('<tr><td colspan="5" class="table-empty">Akun tidak ditemukan.</td></tr>')
    audit_html = []
    for item in audit_rows:
        audit_html.append(
            '<li>'
            f'<time>{html.escape(item["created_at"])}</time>'
            f'<strong>{html.escape(item["actor"])}</strong> '
            f'{html.escape(item["details"])} '
            f'<em>{html.escape(item["target"])}</em>'
            '</li>'
        )
    if not audit_html:
        audit_html.append('<li class="table-empty">Belum ada perubahan administratif.</li>')
    service_cards = []
    for item in monitor["services"]:
        running = bool(item.get("running"))
        service_cards.append(
            f'<article class="monitor-card {"online" if running else "offline"}">'
            f'<span>{"ONLINE" if running else "OFFLINE"}</span>'
            f'<h3>{html.escape(str(item.get("label", item.get("name", "Layanan"))))}</h3>'
            f'<p>{html.escape(str(item.get("detail", "-")))}</p>'
            '</article>'
        )
    if not service_cards:
        service_cards.append('<div class="empty-state"><strong>Data monitoring belum tersedia</strong></div>')
    service_events = []
    for item in monitor["events"]:
        online = item.get("status") == "online"
        service_events.append(
            f'<li class="monitor-event {"online" if online else "offline"}">'
            f'<time>{html.escape(str(item.get("timestamp", "-")))}</time>'
            f'<strong>{html.escape(str(item.get("label", item.get("service", "Layanan"))))}</strong>'
            f'<span>{"kembali online" if online else "menjadi offline"}</span>'
            f'<em>{html.escape(str(item.get("detail", "-")))}</em>'
            '</li>'
        )
    if not service_events:
        service_events.append('<li class="table-empty">Belum ada perubahan status.</li>')
    news_rows = []
    status_labels = {"draft": "Draft", "published": "Terbit", "archived": "Arsip"}
    for item in news_items:
        status = item["status"] if item["status"] in NEWS_STATUSES else "draft"
        next_status = "archived" if status == "published" else "published"
        action_label = "Arsipkan" if status == "published" else "Terbitkan"
        news_rows.append(
            '<tr>'
            f'<td><strong>{html.escape(item["title"])}</strong><small>#{item["id"]}</small></td>'
            f'<td><span class="role-state {"gm" if status == "published" else ""}">{status_labels[status]}</span></td>'
            f'<td>{html.escape(item["updated_at"])}</td>'
            f'<td><div class="news-actions"><a class="small-link" href="/admin?news={item["id"]}#news-editor">Edit</a>'
            '<form method="post" action="/admin/news/status" class="inline-form">'
            f'<input type="hidden" name="csrf" value="{html.escape(token, quote=True)}">'
            f'<input type="hidden" name="news_id" value="{item["id"]}">'
            f'<input type="hidden" name="status" value="{next_status}">'
            f'<button type="submit" class="small-button">{action_label}</button></form></div></td></tr>'
        )
    if not news_rows:
        news_rows.append('<tr><td colspan="4" class="table-empty">Belum ada berita.</td></tr>')
    editor = editor or {"id": 0, "title": "", "body": "", "status": "draft"}
    editor_published = " checked" if editor.get("status") == "published" else ""
    editor_title = "Edit berita" if editor.get("id") else "Tulis berita"
    boutique_rows = []
    for item in (boutique_queue or []):
        gold_label = f'{int(item["gold"]):,}'.replace(",", ".")
        boutique_rows.append(
            '<tr>'
            f'<td><strong>{html.escape(item["username"])}</strong></td>'
            f'<td>{gold_label} Gold</td>'
            f'<td><span class="role-state">Pending · status {int(item["status"])}</span></td>'
            f'<td>{html.escape(item["created_at"])}</td></tr>'
        )
    if not boutique_rows:
        boutique_rows.append('<tr><td colspan="4" class="table-empty">Tidak ada kiriman pending.</td></tr>')
    maps = maps or map_control_state()
    map_rows = []
    states = maps.get("states", {})
    for item in sorted(maps.get("catalog", []),
                       key=lambda value: (not value.get("recommended", False), value.get("alias", ""))):
        alias = str(item.get("alias", ""))
        running = bool(states.get(alias, {}).get("running"))
        tag = item.get("tag")
        metadata = f'{item.get("kind", "map")} · tag {tag if tag is not None else "-"} · {item.get("config", "-")}'
        map_rows.append(
            f'<label class="map-option {"online" if running else "offline"}">'
            f'<input type="checkbox" name="maps" value="{html.escape(alias, quote=True)}">'
            '<span>'
            f'<strong>{html.escape(str(item.get("label", alias)))}</strong>'
            f'<small>{html.escape(alias)} · {html.escape(metadata)}</small>'
            f'</span><em>{"AKTIF" if running else "NONAKTIF"}</em></label>'
        )
    if not map_rows:
        map_rows.append('<div class="empty-state"><strong>Katalog map belum tersedia</strong></div>')
    last = maps.get("last_action")
    if last:
        action_labels = {
            "start": "mengaktifkan map", "stop": "menghentikan map",
            "start-core": "mengaktifkan daemon inti",
            "stop-core": "menghentikan seluruh map dan daemon inti",
        }
        action_label = action_labels.get(last.get("action"), "menjalankan aksi server")
        selected_label = ", ".join(str(value) for value in last.get("maps", [])) or "-"
        last_action = (
            f'<strong>{html.escape(str(last.get("actor", "admin")))}</strong> {action_label} '
            f'<em>{html.escape(selected_label)}</em> · ' if last.get("maps") else
            f'<strong>{html.escape(str(last.get("actor", "admin")))}</strong> {action_label} · '
        ) + (
            f'<span>{html.escape(str(last.get("status", "unknown")))}</span>'
        )
    else:
        last_action = "Belum ada aksi map dari panel."
    map_state = "SEDANG DIPROSES" if maps.get("busy") else f'{len(maps.get("active", []))} MAP AKTIF'
    core_active = len(maps.get("core_active", []))
    core_total = int(maps.get("core_total", 8))
    core_state = "AKTIF" if maps.get("core_online") else (
        "NONAKTIF" if core_active == 0 else f"SEBAGIAN · {core_active}/{core_total}"
    )
    core_cards = []
    for name, item in maps.get("core", {}).items():
        running = bool(item.get("running"))
        core_cards.append(
            f'<span class="core-pill {"online" if running else "offline"}">'
            f'{html.escape(str(item.get("label", name)))} · {"ON" if running else "OFF"}</span>'
        )
    if not core_cards:
        core_cards.append('<span class="core-pill offline">Status daemon belum tersedia</span>')
    backups = backups or backup_control_state()
    backup_rows = []
    for item in backups.get("backups", []):
        filename = str(item.get("filename", ""))
        if not BACKUP_FILENAME_RE.fullmatch(filename):
            continue
        created = str(item.get("created_at", "-"))
        if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", created):
            created = (f"{created[6:8]}-{created[4:6]}-{created[0:4]} "
                       f"{created[9:11]}:{created[11:13]}:{created[13:15]} UTC")
        backup_rows.append(
            '<tr>'
            f'<td><strong>{html.escape(filename)}</strong><small>Backup database PW + portal</small></td>'
            f'<td>{html.escape(created)}</td>'
            f'<td>{html.escape(format_file_size(item.get("bytes", 0)))}</td>'
            f'<td><a class="small-link" href="/admin/backups/download/{html.escape(filename, quote=True)}">Download</a></td>'
            '</tr>'
        )
    if not backup_rows:
        backup_rows.append('<tr><td colspan="4" class="table-empty">Belum ada backup manual yang dapat diunduh.</td></tr>')
    last_backup = backups.get("last_action")
    if last_backup:
        backup_last_action = (
            f'<strong>{html.escape(str(last_backup.get("actor", "admin")))}</strong> · '
            f'<span>{html.escape(str(last_backup.get("status", "unknown")))}</span> · '
            f'{html.escape(str(last_backup.get("message", "-")))}'
        )
    else:
        backup_last_action = "Belum ada permintaan backup manual."
    backup_state = "SEDANG MEMBUAT BACKUP" if backups.get("busy") else "SIAP"
    backup_error = (
        f'<div class="notice">{html.escape(str(backups.get("error")))}</div>'
        if backups.get("error") else ""
    )
    replacements = {
        "{{NOTICE}}": notice_html(message, level),
        "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]),
        "{{SEARCH}}": html.escape(search, quote=True),
        "{{TOTAL_ACCOUNTS}}": str(totals[0]),
        "{{TOTAL_CHARACTERS}}": str(totals[1]),
        "{{TOTAL_GM}}": str(totals[2]),
        "{{ACCOUNT_ROWS}}": "".join(account_rows),
        "{{AUDIT_ROWS}}": "".join(audit_html),
        "{{MONITOR_ONLINE}}": str(monitor["online"]),
        "{{MONITOR_TOTAL}}": str(monitor["total"]),
        "{{MONITOR_UPDATED}}": html.escape(monitor["checked_at"]),
        "{{SERVICE_CARDS}}": "".join(service_cards),
        "{{SERVICE_EVENTS}}": "".join(service_events),
        "{{NEWS_ROWS}}": "".join(news_rows),
        "{{NEWS_ID}}": str(int(editor.get("id", 0))),
        "{{NEWS_TITLE}}": html.escape(str(editor.get("title", "")), quote=True),
        "{{NEWS_BODY}}": html.escape(str(editor.get("body", ""))),
        "{{NEWS_PUBLISHED}}": editor_published,
        "{{NEWS_EDITOR_TITLE}}": editor_title,
        "{{BOUTIQUE_ROWS}}": "".join(boutique_rows),
        "{{MAP_ROWS}}": "".join(map_rows),
        "{{MAP_STATE}}": html.escape(map_state),
        "{{MAP_UPDATED}}": html.escape(str(maps.get("checked_at", "-"))),
        "{{MAP_LAST_ACTION}}": last_action,
        "{{MAP_MAX_SELECTION}}": str(int(maps.get("max_selection", 6))),
        "{{CORE_STATE}}": html.escape(core_state),
        "{{CORE_CARDS}}": "".join(core_cards),
        "{{BACKUP_STATE}}": html.escape(backup_state),
        "{{BACKUP_UPDATED}}": html.escape(str(backups.get("updated_at", "-"))),
        "{{BACKUP_ROWS}}": "".join(backup_rows),
        "{{BACKUP_LAST_ACTION}}": backup_last_action,
        "{{BACKUP_ERROR}}": backup_error,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_data_editor(account, catalog, search="", message="", level=""):
    template = (BASE_DIR / "data_editor.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    rows = []
    for item in catalog["rows"]:
        price = f'{int(item["price_cash"]) / 100:,.2f}'.replace(",", "_").replace(".", ",").replace("_", ".")
        rows.append(
            '<tr>'
            f'<td><strong>{html.escape(item["name"])}</strong><small>Item ID {item["item_id"]} · slot {item["place"]}</small></td>'
            f'<td>{html.escape(item["category"])}<small>{html.escape(item["subcategory"])}</small></td>'
            f'<td>{item["amount"]}</td>'
            f'<td>{price} Gold<small>{item["sale_count"]} opsi aktif</small></td>'
            f'<td><a class="small-link" href="/admin/data/boutique?item={item["index"]}">Edit</a></td>'
            '</tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="table-empty">Item Boutique tidak ditemukan.</td></tr>')
    draft = catalog.get("draft")
    draft_html = ""
    if draft:
        draft_html = (
            '<div class="notice success draft-notice"><strong>Draft Boutique siap</strong>'
            f'<span>{html.escape(str(draft.get("name", "Item")))} · {draft.get("record_count", 0)} record</span>'
            '<div><a class="small-link" href="/admin/data/download/boutique/gshop.data">Unduh client</a>'
            '<a class="small-link" href="/admin/data/download/boutique/gshopsev.data">Unduh server</a></div></div>'
        )
    replacements = {
        "{{NOTICE}}": notice_html(message, level),
        "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]),
        "{{SEARCH}}": html.escape(search, quote=True),
        "{{BOUTIQUE_TOTAL}}": str(catalog["total"]),
        "{{CATEGORY_TOTAL}}": str(catalog["category_count"]),
        "{{BOUTIQUE_ROWS}}": "".join(rows),
        "{{DRAFT_NOTICE}}": draft_html,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_npc_editor(account, catalog, search="", message="", level=""):
    template = (BASE_DIR / "npc_editor.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    rows = []
    for item in catalog["rows"]:
        trigger = "Selalu aktif" if item["trigger"] == 0 else f'Trigger {item["trigger"]}'
        rows.append(
            "<tr>"
            f'<td><strong>{html.escape(item["name"])}</strong><small>NPC ID {item["npc_id"]}</small></td>'
            f'<td>Group {item["group_index"]}<small>Entry {item["entry_index"]}</small></td>'
            f'<td>{item["x"]:.1f}, {item["y"]:.1f}, {item["z"]:.1f}</td>'
            f'<td>{item["amount"]}<small>respawn {item["respawn"]} dtk</small></td>'
            f'<td>{trigger}</td>'
            f'<td><a class="small-link" href="/admin/data/npc/spawn?group={item["group_index"]}&entry={item["entry_index"]}">Edit</a></td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6" class="table-empty">Spawn NPC tidak ditemukan.</td></tr>')
    draft = npc_draft_manifest()
    draft_html = ""
    if draft:
        draft_html = (
            '<div class="notice success draft-notice"><strong>Draft NPC siap</strong>'
            f'<span>NPC ID {draft.get("npc_id", "-")} · group {draft.get("target_group", "-")}</span>'
            '<div><a class="small-link" href="/admin/data/download/npc/npcgen.data">Unduh npcgen.data</a></div></div>'
        )
    replacements = {
        "{{NOTICE}}": notice_html(message, level), "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]), "{{SEARCH}}": html.escape(search, quote=True),
        "{{NPCGEN_VERSION}}": str(catalog["version"]), "{{GROUP_TOTAL}}": str(catalog["total_groups"]),
        "{{NPC_ROWS}}": "".join(rows), "{{DRAFT_NOTICE}}": draft_html,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_npc_spawn_editor(account, group_index, entry_index, group, entry, message="", level=""):
    template = (BASE_DIR / "npc_spawn_editor.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    replacements = {
        "{{NOTICE}}": notice_html(message, level), "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]), "{{GROUP_INDEX}}": str(group_index),
        "{{ENTRY_INDEX}}": str(entry_index), "{{NPC_ID}}": str(entry.npc_id),
        "{{AMOUNT}}": str(entry.amount), "{{RESPAWN}}": str(entry.respawn),
        "{{TRIGGER_ID}}": str(group.trigger_id), "{{X}}": f"{group.x:.3f}",
        "{{Y}}": f"{group.y:.3f}", "{{Z}}": f"{group.z:.3f}",
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_equipment_editor(account, catalog, search="", equipment_type="all", message="", level=""):
    template = (BASE_DIR / "equipment_editor.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    rows = []
    for item in catalog["rows"]:
        rows.append(
            "<tr>"
            f'<td><strong>{html.escape(item["name"])}</strong><small>Equipment ID {item["item_id"]}</small></td>'
            f'<td>{html.escape(item["type_label"])}</td><td>{item["level"]}</td>'
            f'<td>Level {item["require_level"]}</td>'
            f'<td><a class="small-link" href="/admin/data/equipment/item?type={item["type"]}&index={item["index"]}">Edit</a></td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="table-empty">Equipment tidak ditemukan.</td></tr>')
    draft = equipment_draft_manifest()
    draft_html = ""
    if draft:
        draft_html = (
            '<div class="notice success draft-notice"><strong>Draft Equipment siap</strong>'
            f'<span>{html.escape(str(draft.get("name", "Equipment")))} · ID {draft.get("item_id", "-")}</span>'
            '<div><a class="small-link" href="/admin/data/download/equipment/elements.data">Unduh elements.data</a></div></div>'
        )
    options = ['<option value="all">Semua tipe</option>']
    for key, definition in EQUIPMENT_TYPES.items():
        selected = " selected" if equipment_type == key else ""
        options.append(f'<option value="{key}"{selected}>{definition["label"]}</option>')
    if equipment_type == "all":
        options[0] = '<option value="all" selected>Semua tipe</option>'
    replacements = {
        "{{NOTICE}}": notice_html(message, level), "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]), "{{SEARCH}}": html.escape(search, quote=True),
        "{{TYPE_OPTIONS}}": "".join(options), "{{ELEMENTS_VERSION}}": str(catalog["version"]),
        "{{EQUIPMENT_TOTAL}}": str(catalog["total"]), "{{EQUIPMENT_ROWS}}": "".join(rows),
        "{{DRAFT_NOTICE}}": draft_html,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_equipment_item_editor(account, type_key, row_index, item_list, row, message="", level=""):
    template = (BASE_DIR / "equipment_item_editor.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    values = {field: _equipment_value(item_list, row, field) for field in EQUIPMENT_COMMON_FIELDS}
    stat_inputs = []
    for field, label in EQUIPMENT_TYPES[type_key]["stats"]:
        stat_inputs.append(
            f'<label>{html.escape(label)}<input type="number" name="{field}" min="0" max="2147483647" '
            f'value="{html.escape(_equipment_value(item_list, row, field), quote=True)}" required></label>'
        )
    replacements = {
        "{{NOTICE}}": notice_html(message, level), "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]), "{{TYPE}}": type_key,
        "{{TYPE_LABEL}}": EQUIPMENT_TYPES[type_key]["label"], "{{ROW_INDEX}}": str(row_index),
        "{{STAT_INPUTS}}": "".join(stat_inputs),
    }
    replacements.update({f"{{{{{field}}}}}": html.escape(str(value), quote=True)
                         for field, value in values.items()})
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_npc_service_catalog(account, catalog, search="", message="", level=""):
    template = (BASE_DIR / "npc_service_catalog.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    rows = []
    for item in catalog["rows"]:
        rows.append(
            "<tr>"
            f'<td><strong>{html.escape(item["name"])}</strong><small>NPC ID {item["id"]}</small></td>'
            f'<td>{item["sell_service"] or "-"}</td><td>{item["make_service"] or "-"}</td>'
            f'<td><a class="small-link" href="/admin/data/npc/services/detail?npc={item["id"]}">Kelola service</a></td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="4" class="table-empty">NPC tidak ditemukan.</td></tr>')
    draft = catalog.get("draft")
    draft_html = ""
    if draft:
        draft_html = (
            '<div class="notice success draft-notice"><strong>Draft NPC Service aktif</strong>'
            f'<span>Operasi terakhir: {html.escape(str(draft.get("operation", "-")))}</span>'
            '<div><a class="small-link" href="/admin/data/download/npc-services/elements.data">Unduh elements.data</a></div></div>'
        )
    replacements = {
        "{{NOTICE}}": notice_html(message, level), "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]), "{{SEARCH}}": html.escape(search, quote=True),
        "{{NPC_TOTAL}}": str(catalog["total"]), "{{NPC_ROWS}}": "".join(rows),
        "{{DRAFT_NOTICE}}": draft_html,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_npc_service_detail(account, detail, message="", level=""):
    template = (BASE_DIR / "npc_service_detail.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    npc = detail["npc"]
    merchant_rows = []
    for item in detail["merchant"]:
        price = "-" if item.get("price") is None else f'{int(item["price"]):,}'.replace(",", ".")
        form_id = f'merchant-{item["page"]}-{item["slot"]}'
        merchant_rows.append(
            '<tr>'
            f'<td>Page {item["page"]} · Slot {item["slot"]}</td>'
            f'<td><strong>{html.escape(str(item["name"]))}</strong><small>Item ID {item["id"]}</small></td>'
            f'<td><form id="{form_id}" method="post" action="/admin/data/npc/services/merchant/save" class="inline-form">'
            f'<input type="hidden" name="csrf" value="{html.escape(token, quote=True)}"><input type="hidden" name="npc_id" value="{npc["id"]}">'
            f'<input type="hidden" name="page" value="{item["page"]}"><input type="hidden" name="slot" value="{item["slot"]}">'
            f'<input type="hidden" name="item_id" value="{item["id"]}"><input class="table-number-input" type="number" name="price" min="0" max="2147483647" value="{item.get("price") or 0}" aria-label="Harga koin">'
            f'<small>{price} koin saat ini</small></form></td><td><button form="{form_id}" type="submit" name="action" value="save" class="text-button">Simpan harga</button>'
            f'<button form="{form_id}" type="submit" name="action" value="delete" class="text-button danger-text">Hapus</button></td></tr>'
        )
    if not merchant_rows:
        merchant_rows.append('<tr><td colspan="4" class="table-empty">NPC tidak memiliki item merchant.</td></tr>')
    craft_rows = []
    for entry in detail["crafting"]:
        recipe = entry["recipe"]
        if recipe:
            output = recipe["targets"][0]["item"]["name"] if recipe["targets"] else "Belum ada output"
            materials = ", ".join(f'{m["item"]["name"]} ×{m["amount"]}' for m in recipe["materials"][:4]) or "Tanpa bahan"
            craft_rows.append(
                f'<tr><td>Page {entry["page"]} · Slot {entry["slot"]}</td><td><strong>{html.escape(recipe["name"])}</strong>'
                f'<small>Recipe ID {recipe["id"]}</small></td><td>{html.escape(str(output))}<small>{html.escape(materials)}</small></td>'
                f'<td><a class="small-link" href="/admin/data/npc/services/recipe?npc={npc["id"]}&recipe={recipe["id"]}">Edit recipe</a>'
                f'<form method="post" action="/admin/data/npc/services/craft/slot" class="inline-form"><input type="hidden" name="csrf" value="{html.escape(token, quote=True)}">'
                f'<input type="hidden" name="npc_id" value="{npc["id"]}"><input type="hidden" name="page" value="{entry["page"]}"><input type="hidden" name="slot" value="{entry["slot"]}">'
                '<button type="submit" name="action" value="detach" class="text-button danger-text">Lepas</button></form></td></tr>'
            )
        else:
            craft_rows.append(f'<tr><td>Page {entry["page"]} · Slot {entry["slot"]}</td><td>Recipe ID {entry["id"]}</td><td colspan="2">Record tidak ditemukan</td></tr>')
    if not craft_rows:
        craft_rows.append('<tr><td colspan="4" class="table-empty">NPC tidak memiliki recipe crafting.</td></tr>')
    replacements = {
        "{{NOTICE}}": notice_html(message, level), "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]), "{{NPC_ID}}": str(npc["id"]),
        "{{NPC_NAME}}": html.escape(npc["name"], quote=True),
        "{{SELL_SERVICE}}": str(npc["services"]["id_sell_service"] or "-"),
        "{{MAKE_SERVICE}}": str(npc["services"]["id_make_service"] or "-"),
        "{{MERCHANT_ROWS}}": "".join(merchant_rows), "{{CRAFT_ROWS}}": "".join(craft_rows),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_recipe_editor(account, npc_id, recipe, message="", level=""):
    template = (BASE_DIR / "npc_recipe_editor.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    target = recipe["targets"][0] if recipe["targets"] else {"item_id": 0, "probability": 1.0}
    material_by_slot = {item["slot"]: item for item in recipe["materials"]}
    material_inputs = []
    for number in range(1, 33):
        material = material_by_slot.get(number, {"item_id": 0, "amount": 0})
        material_inputs.append(
            f'<div class="material-row"><span>{number:02d}</span><label>Item ID<input type="number" name="material_{number}_id" min="0" max="2147483647" value="{material["item_id"]}"></label>'
            f'<label>Jumlah<input type="number" name="material_{number}_amount" min="0" max="2147483647" value="{material["amount"]}"></label></div>'
        )
    replacements = {
        "{{NOTICE}}": notice_html(message, level), "{{CSRF}}": html.escape(token, quote=True),
        "{{NPC_ID}}": str(npc_id), "{{RECIPE_ID}}": str(recipe["id"]),
        "{{RECIPE_NAME}}": html.escape(recipe["name"], quote=True),
        "{{OUTPUT_ID}}": str(target["item_id"]), "{{PROBABILITY}}": str(target["probability"]),
        "{{NUM_TO_MAKE}}": str(recipe["num_to_make"]), "{{PRICE}}": str(recipe["price"]),
        "{{DURATION}}": str(recipe["duration"]), "{{MATERIAL_INPUTS}}": "".join(material_inputs),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


def render_boutique_editor(account, shop, item, index, message="", level=""):
    template = (BASE_DIR / "boutique_editor.html").read_text(encoding="utf-8")
    token = new_csrf_token()
    categories = [{"name": entry.name, "subcategories": entry.subcategories}
                  for entry in shop.categories]
    category_options = []
    for number, category in enumerate(shop.categories):
        selected = " selected" if number == item.category else ""
        category_options.append(
            f'<option value="{number}"{selected}>{html.escape(category.name or f"Kategori {number + 1}")}</option>'
        )
    subcategory_options = []
    for number, name in enumerate(shop.categories[item.category].subcategories):
        selected = " selected" if number == item.subcategory else ""
        subcategory_options.append(
            f'<option value="{number}"{selected}>{html.escape(name or f"Subkategori {number + 1}")}</option>'
        )
    price_cash = next((sale.price for sale in item.sales if sale.price > 0), 1)
    replacements = {
        "{{NOTICE}}": notice_html(message, level),
        "{{CSRF}}": html.escape(token, quote=True),
        "{{USERNAME}}": html.escape(account[1]),
        "{{ITEM_INDEX}}": str(index),
        "{{ITEM_ID}}": str(item.item_id),
        "{{ITEM_NAME}}": html.escape(item.name, quote=True),
        "{{ITEM_AMOUNT}}": str(item.amount),
        "{{ITEM_PRICE}}": str(price_cash),
        "{{ITEM_ICON}}": html.escape(item.icon, quote=True),
        "{{ITEM_DESCRIPTION}}": html.escape(item.description),
        "{{CATEGORY_OPTIONS}}": "".join(category_options),
        "{{SUBCATEGORY_OPTIONS}}": "".join(subcategory_options),
        "{{CATEGORY_DATA}}": html.escape(json.dumps(categories, ensure_ascii=False), quote=True),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template, token


class PWHandler(BaseHTTPRequestHandler):
    server_version = "PW155Web/0.2"

    def log_message(self, fmt, *args):
        # Tidak pernah mencatat body form atau sandi.
        super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self'; script-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        super().end_headers()

    def client_key(self):
        return self.client_address[0]

    def cookies(self):
        return SimpleCookie(self.headers.get("Cookie", ""))

    def session_account(self):
        session_cookie = self.cookies().get("pwsession")
        return parse_session(session_cookie.value) if session_cookie else None

    def read_form(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if content_type != "application/x-www-form-urlencoded" or not 0 < length <= 4096:
            return None
        try:
            return parse_qs(self.rfile.read(length).decode("utf-8", "strict"),
                            keep_blank_values=True)
        except UnicodeDecodeError:
            return None

    def valid_form_csrf(self, fields):
        csrf = fields.get("csrf", [""])[0]
        cookie_csrf = self.cookies().get("pwcsrf")
        return bool(cookie_csrf and hmac.compare_digest(cookie_csrf.value, csrf)
                    and valid_csrf_token(csrf))

    def send_bytes(self, status, body, content_type, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_download(self, filename):
        source = resolve_download(filename)
        if source is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(source.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        try:
            with source.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_patch_file(self, relative_path):
        source = resolve_patch_file(relative_path)
        if source is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(source.stat().st_size))
        cache = "public, max-age=31536000, immutable" if source.parent.name in (
            "element", "launcher", "patcher") else "no-store"
        self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            with source.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def redirect_home(self, message, level="error", username=""):
        body, token = render_home(message, level, username)
        encoded = body.encode("utf-8")
        self.send_bytes(HTTPStatus.OK, encoded, "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def redirect(self, location, cookie=None):
        headers = {"Location": location}
        if cookie:
            headers["Set-Cookie"] = cookie
        self.send_bytes(HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8", headers)

    def send_login(self, message="", username=""):
        body, token = render_login(message, username)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"),
                        "text/html; charset=utf-8", {
                            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
                        })

    def send_panel(self, account, message="", level=""):
        try:
            profile = account_profile(account[0])
            characters = account_characters(account[0])
            if profile:
                profile["is_admin"] = is_panel_admin(account[0])
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE,
                            "Data player panel sedang tidak tersedia")
            return
        if not profile:
            self.redirect("/login", "pwsession=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
            return
        body, token = render_panel(profile, characters, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"),
                        "text/html; charset=utf-8", {
                        "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
                        })

    def send_admin(self, account, search="", message="", level="", news_id=0):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            accounts = admin_accounts(search)
            totals = admin_totals()
            audit_rows = admin_audit()
            monitor = monitor_state()
            news_items = admin_news()
            editor = admin_news_item(news_id) if news_id else None
            boutique_queue = boutique_pending()
            maps = map_control_state()
            backups = backup_control_state()
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE,
                            "Admin panel sedang tidak tersedia")
            return
        body, token = render_admin(account, accounts, totals, audit_rows, monitor,
                                   news_items, editor, boutique_queue, maps,
                                   search, message, level, backups=backups)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"),
                        "text/html; charset=utf-8", {
                            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
                        })

    def send_data_editor(self, account, search="", message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            catalog = boutique_catalog(search)
            catalog["draft"] = boutique_draft_manifest()
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE,
                            "Editor data sedang tidak tersedia")
            return
        body, token = render_data_editor(account, catalog, search, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"),
                        "text/html; charset=utf-8", {
                            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
                        })

    def send_boutique_editor(self, account, index, message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            shop, item = boutique_item(index)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError):
            self.send_error(HTTPStatus.BAD_REQUEST, "Item Boutique tidak valid")
            return
        body, token = render_boutique_editor(account, shop, item, int(index), message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"),
                        "text/html; charset=utf-8", {
                            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
                        })

    def send_boutique_draft(self, account, filename):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if filename not in ("gshop.data", "gshopsev.data"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = DATA_EDITOR_DIR / "boutique" / "draft" / filename
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Draft belum tersedia")
            return
        self.send_bytes(HTTPStatus.OK, path.read_bytes(), "application/octet-stream", {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        })

    def send_npc_editor(self, account, search="", message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            catalog = npc_catalog(search)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Editor NPC sedang tidak tersedia")
            return
        body, token = render_npc_editor(account, catalog, search, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def send_npc_spawn_editor(self, account, group_index, entry_index, message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            _, group, entry = npc_spawn_item(group_index, entry_index)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError, IndexError):
            self.send_error(HTTPStatus.BAD_REQUEST, "Spawn NPC tidak valid")
            return
        body, token = render_npc_spawn_editor(account, int(group_index), int(entry_index),
                                              group, entry, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def send_npc_draft(self, account):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        path = DATA_EDITOR_DIR / "npc" / "draft" / "a61" / "npcgen.data"
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Draft belum tersedia")
            return
        self.send_bytes(HTTPStatus.OK, path.read_bytes(), "application/octet-stream", {
            "Content-Disposition": 'attachment; filename="a61-npcgen.data"', "Cache-Control": "no-store",
        })

    def send_equipment_editor(self, account, search="", equipment_type="all", message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            catalog = equipment_catalog(search, equipment_type)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError, KeyError):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Editor Equipment sedang tidak tersedia")
            return
        body, token = render_equipment_editor(account, catalog, search, equipment_type, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def send_equipment_item_editor(self, account, type_key, row_index, message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            _, item_list, row = equipment_item(type_key, row_index)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError, KeyError):
            self.send_error(HTTPStatus.BAD_REQUEST, "Equipment tidak valid")
            return
        body, token = render_equipment_item_editor(account, type_key, int(row_index),
                                                   item_list, row, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def send_equipment_draft(self, account):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        path = DATA_EDITOR_DIR / "equipment" / "draft" / "elements.data"
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Draft belum tersedia")
            return
        self.send_bytes(HTTPStatus.OK, path.read_bytes(), "application/octet-stream", {
            "Content-Disposition": 'attachment; filename="elements.data"', "Cache-Control": "no-store",
        })

    def send_npc_service_catalog(self, account, search="", message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            catalog = npc_service_catalog(search)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError, KeyError):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "NPC Service Editor sedang tidak tersedia")
            return
        body, token = render_npc_service_catalog(account, catalog, search, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def send_npc_service_detail(self, account, npc_id, message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            detail = npc_service_detail(npc_id)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError, KeyError):
            self.send_error(HTTPStatus.BAD_REQUEST, "NPC atau service tidak valid")
            return
        body, token = render_npc_service_detail(account, detail, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def send_recipe_editor(self, account, npc_id, recipe_id, message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
            detail = npc_service_detail(npc_id)
            valid_ids = {item["id"] for item in detail["crafting"]}
            if int(recipe_id) not in valid_ids:
                raise ValueError("Recipe bukan milik Make Service NPC")
            recipe = recipe_record(detail["elements"], recipe_id)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError, KeyError):
            self.send_error(HTTPStatus.BAD_REQUEST, "Recipe tidak valid")
            return
        body, token = render_recipe_editor(account, int(npc_id), recipe, message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def send_npc_service_draft(self, account):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        path = _service_draft_path()
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Draft belum tersedia")
            return
        self.send_bytes(HTTPStatus.OK, path.read_bytes(), "application/octet-stream", {
            "Content-Disposition": 'attachment; filename="elements-npc-services.data"',
            "Cache-Control": "no-store",
        })

    def send_backup_download(self, account, filename):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        source = resolve_backup_download(filename)
        if source is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Backup tidak ditemukan")
            return
        try:
            size = source.stat().st_size
            stream = source.open("rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "Backup tidak tersedia")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with stream:
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            body, token = render_home()
            self.send_bytes(HTTPStatus.OK, body.encode("utf-8"),
                            "text/html; charset=utf-8", {
                                "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
                            })
            return
        if path == "/api/status":
            body = json.dumps(server_status(), separators=(",", ":")).encode()
            self.send_bytes(HTTPStatus.OK, body, "application/json; charset=utf-8")
            return
        if path == "/api/downloads":
            body = json.dumps(load_download_manifest(), separators=(",", ":")).encode()
            self.send_bytes(HTTPStatus.OK, body, "application/json; charset=utf-8")
            return
        if path == "/news":
            try:
                body = render_news(published_news()).encode("utf-8")
            except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE,
                                "Berita sedang tidak tersedia")
                return
            self.send_bytes(HTTPStatus.OK, body, "text/html; charset=utf-8")
            return
        if path == "/guide":
            template = BASE_DIR / f"{path[1:]}.html"
            self.send_bytes(HTTPStatus.OK, template.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/downloads":
            body = render_downloads().encode("utf-8")
            self.send_bytes(HTTPStatus.OK, body, "text/html; charset=utf-8")
            return
        if path.startswith("/downloads/file/"):
            self.send_download(path[len("/downloads/file/"):])
            return
        if path.startswith("/patch/"):
            self.send_patch_file(path[len("/patch/"):])
            return
        if path == "/ranking":
            try:
                ranking = ranking_characters()
            except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE,
                                "Ranking sedang tidak tersedia")
                return
            body = render_ranking(ranking).encode("utf-8")
            self.send_bytes(HTTPStatus.OK, body, "text/html; charset=utf-8")
            return
        if path == "/login":
            if self.session_account():
                self.redirect("/panel")
            else:
                self.send_login()
            return
        if path == "/panel":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                self.send_panel(account)
            return
        if path == "/admin":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                search = parse_qs(parsed.query).get("q", [""])[0].strip()
                news_text = parse_qs(parsed.query).get("news", ["0"])[0]
                news_id = int(news_text) if news_text.isdigit() else 0
                self.send_admin(account, search, news_id=news_id)
            return
        if path == "/admin/patch":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                self.send_patch_manager(account)
            return
        if path.startswith("/admin/backups/download/"):
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                self.send_backup_download(account, path.rsplit("/", 1)[1])
            return
        if path == "/admin/data":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                search = parse_qs(parsed.query).get("q", [""])[0].strip()
                self.send_data_editor(account, search)
            return
        if path == "/admin/data/boutique":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                item_text = parse_qs(parsed.query).get("item", [""])[0]
                if not item_text.isdigit():
                    self.send_error(HTTPStatus.BAD_REQUEST, "Index item tidak valid")
                else:
                    self.send_boutique_editor(account, int(item_text))
            return
        if path == "/admin/data/npc":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                search = parse_qs(parsed.query).get("q", [""])[0].strip()
                self.send_npc_editor(account, search)
            return
        if path == "/admin/data/npc/spawn":
            account = self.session_account()
            group = parse_qs(parsed.query).get("group", [""])[0]
            entry = parse_qs(parsed.query).get("entry", [""])[0]
            if not account:
                self.redirect("/login")
            elif not group.isdigit() or not entry.isdigit():
                self.send_error(HTTPStatus.BAD_REQUEST, "Record NPC tidak valid")
            else:
                self.send_npc_spawn_editor(account, int(group), int(entry))
            return
        if path == "/admin/data/npc/services":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                search = parse_qs(parsed.query).get("q", [""])[0].strip()
                self.send_npc_service_catalog(account, search)
            return
        if path == "/admin/data/npc/services/detail":
            account = self.session_account()
            npc_text = parse_qs(parsed.query).get("npc", [""])[0]
            if not account:
                self.redirect("/login")
            elif not npc_text.isdigit():
                self.send_error(HTTPStatus.BAD_REQUEST, "NPC ID tidak valid")
            else:
                self.send_npc_service_detail(account, int(npc_text))
            return
        if path == "/admin/data/npc/services/recipe":
            account = self.session_account()
            query = parse_qs(parsed.query)
            npc_text, recipe_text = query.get("npc", [""])[0], query.get("recipe", [""])[0]
            if not account:
                self.redirect("/login")
            elif not npc_text.isdigit() or not recipe_text.isdigit():
                self.send_error(HTTPStatus.BAD_REQUEST, "Recipe tidak valid")
            else:
                self.send_recipe_editor(account, int(npc_text), int(recipe_text))
            return
        if path == "/admin/data/equipment":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                query = parse_qs(parsed.query)
                self.send_equipment_editor(account, query.get("q", [""])[0].strip(),
                                           query.get("type", ["all"])[0])
            return
        if path == "/admin/data/equipment/item":
            account = self.session_account()
            query = parse_qs(parsed.query)
            type_key, row_index = query.get("type", [""])[0], query.get("index", [""])[0]
            if not account:
                self.redirect("/login")
            elif type_key not in EQUIPMENT_TYPES or not row_index.isdigit():
                self.send_error(HTTPStatus.BAD_REQUEST, "Record Equipment tidak valid")
            else:
                self.send_equipment_item_editor(account, type_key, int(row_index))
            return
        if path.startswith("/admin/data/download/boutique/"):
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                self.send_boutique_draft(account, path.rsplit("/", 1)[1])
            return
        if path == "/admin/data/download/npc/npcgen.data":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                self.send_npc_draft(account)
            return
        if path == "/admin/data/download/equipment/elements.data":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                self.send_equipment_draft(account)
            return
        if path == "/admin/data/download/npc-services/elements.data":
            account = self.session_account()
            if not account:
                self.redirect("/login")
            else:
                self.send_npc_service_draft(account)
            return
        if path in ("/static/style.css", "/static/app.js"):
            filename = path.rsplit("/", 1)[1]
            content_type = "text/css; charset=utf-8" if filename.endswith(".css") else "text/javascript; charset=utf-8"
            body = (STATIC_DIR / filename).read_bytes()
            self.send_bytes(HTTPStatus.OK, body, content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path not in ("/register", "/login", "/logout", "/change-password",
                             "/admin/gm", "/admin/news/save", "/admin/news/status",
                             "/admin/boutique/grant", "/admin/maps/action", "/admin/patch/action",
                             "/admin/backups/create",
                             "/admin/data/boutique/save",
                             "/admin/data/npc/spawn/save", "/admin/data/equipment/item/save"):
            allowed_extra = ("/admin/data/npc/services/merchant/save",
                             "/admin/data/npc/services/recipe/save",
                             "/admin/data/npc/services/clone",
                             "/admin/data/npc/services/craft/slot")
            if self.path not in allowed_extra:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        fields = self.read_form()
        if fields is None:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if not self.valid_form_csrf(fields):
            self.send_error(HTTPStatus.FORBIDDEN, "Token formulir tidak valid")
            return
        if self.path == "/register":
            self.handle_register(fields)
        elif self.path == "/login":
            self.handle_login(fields)
        elif self.path == "/logout":
            self.redirect("/", "pwsession=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        elif self.path == "/admin/gm":
            self.handle_admin_gm(fields)
        elif self.path == "/admin/news/save":
            self.handle_admin_news_save(fields)
        elif self.path == "/admin/news/status":
            self.handle_admin_news_status(fields)
        elif self.path == "/admin/boutique/grant":
            self.handle_admin_boutique_grant(fields)
        elif self.path == "/admin/maps/action":
            self.handle_admin_maps_action(fields)
        elif self.path == "/admin/patch/action":
            self.handle_admin_patch_action(fields)
        elif self.path == "/admin/backups/create":
            self.handle_admin_backup_create(fields)
        elif self.path == "/admin/data/boutique/save":
            self.handle_admin_boutique_save(fields)
        elif self.path == "/admin/data/npc/spawn/save":
            self.handle_admin_npc_spawn_save(fields)
        elif self.path == "/admin/data/equipment/item/save":
            self.handle_admin_equipment_save(fields)
        elif self.path == "/admin/data/npc/services/merchant/save":
            self.handle_admin_merchant_service_save(fields)
        elif self.path == "/admin/data/npc/services/recipe/save":
            self.handle_admin_recipe_service_save(fields)
        elif self.path == "/admin/data/npc/services/clone":
            self.handle_admin_npc_service_clone(fields)
        elif self.path == "/admin/data/npc/services/craft/slot":
            self.handle_admin_make_service_slot(fields)
        else:
            self.handle_change_password(fields)

    def handle_register(self, fields):
        if not REGISTRATION_LIMITER.allow(self.client_key()):
            self.redirect_home("Terlalu banyak percobaan. Tunggu 10 menit.")
            return
        username = fields.get("username", [""])[0].strip()
        password = fields.get("password", [""])[0]
        confirmation = fields.get("confirmation", [""])[0]
        error = validate_registration(username, password, confirmation)
        if error:
            self.redirect_home(error, "error", username)
            return
        try:
            outcome, account = create_game_account(username, password)
        except (RuntimeError, subprocess.TimeoutExpired):
            self.redirect_home("Registrasi sedang tidak tersedia. Coba lagi nanti.")
            return
        if outcome == "exists":
            self.redirect_home("Username tersebut sudah digunakan.", "error", username)
            return
        self.redirect_home(
            f"Akun {account} berhasil dibuat. Anda sekarang dapat login di client dan player panel.",
            "success")

    def handle_login(self, fields):
        if not LOGIN_LIMITER.allow(self.client_key()):
            self.send_login("Terlalu banyak percobaan. Tunggu 10 menit.")
            return
        username = fields.get("username", [""])[0].strip().lower()
        password = fields.get("password", [""])[0]
        if not USERNAME_RE.fullmatch(username) or not PASSWORD_RE.fullmatch(password):
            self.send_login("Username atau sandi tidak benar.", username)
            return
        try:
            account = authenticate_account(username, password)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            self.send_login("Login web sedang tidak tersedia.", username)
            return
        if not account:
            self.send_login("Username atau sandi tidak benar.", username)
            return
        session = make_session(*account)
        self.redirect("/panel",
                      f"pwsession={session}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict")

    def handle_change_password(self, fields):
        account = self.session_account()
        if not account:
            self.redirect("/login")
            return
        current = fields.get("current_password", [""])[0]
        new_password = fields.get("new_password", [""])[0]
        confirmation = fields.get("confirmation", [""])[0]
        if not PASSWORD_RE.fullmatch(new_password):
            self.send_panel(account, "Sandi baru harus 6–32 karakter yang diizinkan.", "error")
            return
        if new_password != confirmation:
            self.send_panel(account, "Konfirmasi sandi baru tidak sama.", "error")
            return
        try:
            identity = portal_identity(account[1])
            if not identity or not verify_web_password(current, identity[2]):
                self.send_panel(account, "Sandi saat ini tidak benar.", "error")
                return
            change_account_password(account[0], account[1], new_password)
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_panel(account, "Perubahan sandi sedang tidak tersedia.", "error")
            return
        self.send_panel(account,
                        "Sandi web dan client berhasil diperbarui.", "success")

    def handle_admin_gm(self, fields):
        account = self.session_account()
        if not account:
            self.redirect("/login")
            return
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE,
                            "Admin panel sedang tidak tersedia")
            return
        target_text = fields.get("target_id", [""])[0]
        action = fields.get("action", [""])[0]
        search = fields.get("search", [""])[0].strip()
        if not target_text.isdigit() or action not in ("grant", "revoke"):
            self.send_error(HTTPStatus.BAD_REQUEST, "Aksi admin tidak valid")
            return
        try:
            target_id = int(target_text)
            set_account_gm(account[0], target_id, action == "grant",
                           self.client_key())
            target = account_profile(target_id)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            self.send_admin(account, search,
                            "Perubahan status GM gagal diterapkan.", "error")
            return
        if not target:
            self.send_admin(account, search, "Akun target tidak ditemukan.", "error")
            return
        verb = "diberikan kepada" if action == "grant" else "dicabut dari"
        self.send_admin(account, search,
                        f"Hak GM berhasil {verb} {target['username']}.", "success")

    def require_admin(self):
        account = self.session_account()
        if not account:
            self.redirect("/login")
            return None
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Akses admin diperlukan")
                return None
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE,
                            "Admin panel sedang tidak tersedia")
            return None
        return account

    def send_patch_manager(self, account, message="", level=""):
        try:
            if not is_panel_admin(account[0]):
                self.send_error(HTTPStatus.FORBIDDEN, "Admin access required")
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        body, token = render_patch_manager(account, cpw_control_state(), message, level)
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8", {
            "Set-Cookie": f"pwcsrf={token}; Path=/; SameSite=Strict; HttpOnly",
        })

    def handle_admin_patch_action(self, fields):
        account = self.require_admin()
        if not account:
            return
        action = fields.get("action", [""])[0]
        release = fields.get("release", [""])[0]
        confirm = fields.get("confirm", [""])[0]
        if action == "publish" and confirm != "yes":
            self.send_patch_manager(account, "Confirm the publish action first.", "error")
            return
        try:
            queue_cpw_action(account, action, release, self.client_key())
        except (OSError, ValueError) as exc:
            self.send_patch_manager(account, str(exc), "error")
            return
        self.send_patch_manager(account, "Patch request queued. Refresh this page in a few seconds.", "success")

    def handle_admin_backup_create(self, fields):
        account = self.require_admin()
        if not account:
            return
        if fields.get("confirm", [""])[0] != "yes":
            self.send_admin(account, message="Centang konfirmasi sebelum membuat backup.",
                            level="error")
            return
        try:
            queue_backup_action(account, "create", self.client_key())
        except (OSError, ValueError) as error:
            self.send_admin(account, message=f"Permintaan backup ditolak: {error}",
                            level="error")
            return
        self.send_admin(
            account,
            message=("Backup database masuk antrean. Muat ulang halaman setelah beberapa saat; "
                     "tombol Download akan muncul setelah backup selesai."),
            level="success",
        )

    def handle_admin_news_save(self, fields):
        account = self.require_admin()
        if not account:
            return
        news_text = fields.get("news_id", ["0"])[0]
        title = fields.get("title", [""])[0].strip()
        body = fields.get("body", [""])[0].strip()
        published = fields.get("published", [""])[0] == "1"
        if not news_text.isdigit() or not 5 <= len(title) <= 100 or not 20 <= len(body) <= 800:
            self.send_admin(account, message="Judul harus 5–100 karakter dan isi 20–800 karakter.",
                            level="error", news_id=int(news_text) if news_text.isdigit() else 0)
            return
        try:
            saved_id = save_news(account[0], int(news_text), title, body,
                                 published, self.client_key())
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            self.send_admin(account, message="Berita gagal disimpan.", level="error")
            return
        state = "diterbitkan" if published else "disimpan sebagai draft"
        self.send_admin(account, message=f"Berita #{saved_id} berhasil {state}.",
                        level="success", news_id=saved_id)

    def handle_admin_news_status(self, fields):
        account = self.require_admin()
        if not account:
            return
        news_text = fields.get("news_id", [""])[0]
        status = fields.get("status", [""])[0]
        if not news_text.isdigit() or status not in ("published", "archived"):
            self.send_error(HTTPStatus.BAD_REQUEST, "Aksi berita tidak valid")
            return
        try:
            set_news_status(account[0], int(news_text), status, self.client_key())
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            self.send_admin(account, message="Status berita gagal diubah.", level="error")
            return
        label = "diterbitkan" if status == "published" else "diarsipkan"
        self.send_admin(account, message=f"Berita berhasil {label}.", level="success")

    def handle_admin_boutique_grant(self, fields):
        account = self.require_admin()
        if not account:
            return
        username = fields.get("target_username", [""])[0].strip().lower()
        gold_text = fields.get("gold", [""])[0].strip()
        confirmed = fields.get("confirm", [""])[0] == "yes"
        if (not USERNAME_RE.fullmatch(username) or not gold_text.isdigit()
                or not 1 <= int(gold_text) <= 10000 or not confirmed):
            self.send_admin(
                account,
                message="Isi username, jumlah 1–10.000 Gold, dan centang konfirmasi.",
                level="error",
            )
            return
        try:
            result = grant_boutique_gold(account[0], username, int(gold_text),
                                         self.client_key())
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            self.send_admin(
                account,
                message="Kiriman gagal. Periksa username atau tunggu transaksi pending selesai.",
                level="error",
            )
            return
        amount = f'{result["gold"]:,}'.replace(",", ".")
        self.send_admin(
            account,
            message=f'{amount} Gold Boutique masuk antrean untuk {result["username"]}.',
            level="success",
        )

    def handle_admin_maps_action(self, fields):
        account = self.require_admin()
        if not account:
            return
        action = fields.get("action", [""])[0]
        selected = fields.get("maps", [])
        confirmed = fields.get("confirm", [""])[0] == "yes"
        if not confirmed:
            self.send_admin(account, message="Centang konfirmasi sebelum mengubah map.",
                            level="error")
            return
        try:
            request = queue_map_action(account, action, selected, self.client_key())
        except (ValueError, OSError) as error:
            self.send_admin(account, message=f"Permintaan map ditolak: {error}",
                            level="error")
            return
        labels = {
            "start": "Map akan diaktifkan",
            "stop": "Map akan dihentikan",
            "start-core": "Daemon inti akan diaktifkan",
            "stop-core": "Seluruh map lalu daemon inti akan dihentikan dengan aman",
        }
        aliases = ", ".join(request["maps"])
        target = f" ({aliases})" if aliases else ""
        self.send_admin(
            account,
            message=(f"{labels.get(action, 'Aksi server')}{target}; permintaan sudah masuk antrean. "
                     "Status diperbarui otomatis; muat ulang halaman setelah beberapa saat."),
            level="success",
        )

    def handle_admin_boutique_save(self, fields):
        account = self.require_admin()
        if not account:
            return
        index_text = fields.get("item_index", [""])[0].strip()
        mode = fields.get("mode", ["edit"])[0]
        confirmed = fields.get("confirm", [""])[0] == "yes"
        if not index_text.isdigit() or mode not in ("edit", "clone") or not confirmed:
            self.send_error(HTTPStatus.BAD_REQUEST, "Form Boutique tidak valid")
            return
        try:
            manifest = build_boutique_draft(
                account, int(index_text), fields, clone=mode == "clone"
            )
        except (ValueError, OSError, UnicodeError) as error:
            self.send_boutique_editor(
                account, int(index_text), f"Draft gagal: {error}", "error"
            )
            return
        action = "duplikasi" if mode == "clone" else "perubahan"
        self.send_data_editor(
            account,
            message=(f'Draft {action} untuk {manifest["name"]} berhasil dibuat. '
                     "File aktif belum diubah."),
            level="success",
        )

    def handle_admin_npc_spawn_save(self, fields):
        account = self.require_admin()
        if not account:
            return
        group_text = fields.get("group_index", [""])[0].strip()
        entry_text = fields.get("entry_index", [""])[0].strip()
        mode = fields.get("mode", ["edit"])[0]
        confirmed = fields.get("confirm", [""])[0] == "yes"
        if not group_text.isdigit() or not entry_text.isdigit() or mode not in ("edit", "clone") or not confirmed:
            self.send_error(HTTPStatus.BAD_REQUEST, "Form NPC tidak valid")
            return
        try:
            manifest = build_npc_draft(account, int(group_text), int(entry_text), fields,
                                       clone=mode == "clone")
        except (ValueError, OSError, IndexError) as error:
            self.send_npc_spawn_editor(account, int(group_text), int(entry_text),
                                       f"Draft gagal: {error}", "error")
            return
        action = "duplikasi" if mode == "clone" else "perubahan"
        self.send_npc_editor(
            account,
            message=(f'Draft {action} NPC ID {manifest["npc_id"]} berhasil dibuat. '
                     "Map aktif belum diubah."),
            level="success",
        )

    def handle_admin_equipment_save(self, fields):
        account = self.require_admin()
        if not account:
            return
        type_key = fields.get("type", [""])[0]
        index_text = fields.get("row_index", [""])[0].strip()
        mode = fields.get("mode", ["edit"])[0]
        confirmed = fields.get("confirm", [""])[0] == "yes"
        if type_key not in EQUIPMENT_TYPES or not index_text.isdigit() or mode not in ("edit", "clone") or not confirmed:
            self.send_error(HTTPStatus.BAD_REQUEST, "Form Equipment tidak valid")
            return
        try:
            manifest = build_equipment_draft(account, type_key, int(index_text), fields,
                                             clone=mode == "clone")
        except (ValueError, OSError, KeyError, UnicodeError) as error:
            self.send_equipment_item_editor(account, type_key, int(index_text),
                                            f"Draft gagal: {error}", "error")
            return
        action = "duplikasi" if mode == "clone" else "perubahan"
        self.send_equipment_editor(
            account, equipment_type=type_key,
            message=(f'Draft {action} untuk {manifest["name"]} berhasil dibuat. '
                     "File aktif belum diubah."), level="success",
        )

    def handle_admin_merchant_service_save(self, fields):
        account = self.require_admin()
        if not account:
            return
        try:
            npc_id = int(fields.get("npc_id", [""])[0])
            page = int(fields.get("page", [""])[0])
            slot = int(fields.get("slot", [""])[0])
            item_id = int(fields.get("item_id", ["0"])[0])
            price = int(fields.get("price", ["0"])[0])
            delete = fields.get("action", ["save"])[0] == "delete"
            build_merchant_service_draft(account, npc_id, page, slot, item_id, price, delete)
        except (ValueError, OSError, KeyError, UnicodeError) as error:
            self.send_npc_service_detail(account, fields.get("npc_id", [0])[0],
                                         f"Draft merchant gagal: {error}", "error")
            return
        self.send_npc_service_detail(account, npc_id,
                                     "Item merchant berhasil disimpan ke draft.", "success")

    def handle_admin_recipe_service_save(self, fields):
        account = self.require_admin()
        if not account:
            return
        npc_text, recipe_text = fields.get("npc_id", [""])[0], fields.get("recipe_id", [""])[0]
        confirmed = fields.get("confirm", [""])[0] == "yes"
        if not npc_text.isdigit() or not recipe_text.isdigit() or not confirmed:
            self.send_error(HTTPStatus.BAD_REQUEST, "Form Recipe tidak valid")
            return
        npc_id, recipe_id = int(npc_text), int(recipe_text)
        try:
            build_recipe_service_draft(account, npc_id, recipe_id, fields)
        except (ValueError, OSError, KeyError, UnicodeError) as error:
            self.send_recipe_editor(account, npc_id, recipe_id,
                                    f"Draft recipe gagal: {error}", "error")
            return
        self.send_npc_service_detail(account, npc_id,
                                     "Recipe dan bahan berhasil disimpan ke draft.", "success")

    def handle_admin_npc_service_clone(self, fields):
        account = self.require_admin()
        if not account:
            return
        source_text = fields.get("source_npc_id", [""])[0]
        if not source_text.isdigit():
            self.send_error(HTTPStatus.BAD_REQUEST, "NPC sumber tidak valid")
            return
        source_id = int(source_text)
        try:
            manifest = build_npc_clone_draft(account, source_id, fields)
        except (ValueError, OSError, KeyError, UnicodeError) as error:
            self.send_npc_service_detail(account, source_id,
                                         f"Duplikasi NPC gagal: {error}", "error")
            return
        new_id = manifest["details"]["new_npc_id"]
        self.send_npc_service_detail(account, new_id,
                                     "NPC dan service berhasil diduplikasi ke draft.", "success")

    def handle_admin_make_service_slot(self, fields):
        account = self.require_admin()
        if not account:
            return
        try:
            npc_id = int(fields.get("npc_id", [""])[0])
            page = int(fields.get("page", [""])[0])
            slot = int(fields.get("slot", [""])[0])
            detach = fields.get("action", ["clone"])[0] == "detach"
            build_make_service_slot_draft(account, npc_id, page, slot, fields, detach)
        except (ValueError, OSError, KeyError, UnicodeError) as error:
            self.send_npc_service_detail(account, fields.get("npc_id", [0])[0],
                                         f"Perubahan Make Service gagal: {error}", "error")
            return
        self.send_npc_service_detail(account, npc_id,
                                     "Daftar recipe Make Service berhasil diperbarui.", "success")


def main():
    if len(CSRF_SECRET) < 32:
        raise SystemExit("PW155_WEB_CSRF_SECRET minimal 32 karakter")
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), PWHandler)
    print(f"PW155 web listening on {BIND_HOST}:{BIND_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
