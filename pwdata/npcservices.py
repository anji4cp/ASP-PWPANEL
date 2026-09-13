"""Structured helpers for NPC, merchant, crafting-service, and recipe records."""

from __future__ import annotations

import copy
import re

from .elements156 import ElementList, Elements156


SELL_SLOT_RE = re.compile(r"^pages_(\d+)_goods_(\d+)_id$")
MAKE_SLOT_RE = re.compile(r"^pages_(\d+)_id_goods_(\d+)$")
MATERIAL_RE = re.compile(r"^materials_(\d+)_id$")


def value(elements: Elements156, item_list: ElementList, row, field: str):
    index = item_list.field_index(field)
    raw = row[index]
    return Elements156.text(raw, item_list.types[index]) if isinstance(raw, bytes) else raw


def set_value(elements: Elements156, item_list: ElementList, row, field: str, new_value):
    index = item_list.field_index(field)
    type_name = item_list.types[index]
    row[index] = Elements156.encoded_text(str(new_value), type_name) if isinstance(row[index], bytes) else new_value


def row_by_id(elements: Elements156, suffix: str, record_id: int):
    item_list = elements.list_named(suffix)
    id_index = item_list.field_index("ID")
    matches = [(index, row) for index, row in enumerate(item_list.rows) if row[id_index] == int(record_id)]
    if len(matches) != 1:
        raise ValueError(f"ID {record_id} pada {suffix} ditemukan {len(matches)} kali")
    index, row = matches[0]
    return item_list, index, row


def clone_record(elements: Elements156, suffix: str, source_id: int, new_id: int, name: str | None = None):
    item_list, _, source = row_by_id(elements, suffix, source_id)
    id_index = item_list.field_index("ID")
    if any(row[id_index] == int(new_id) for row in item_list.rows):
        raise ValueError(f"ID {new_id} sudah digunakan pada {suffix}")
    row = copy.deepcopy(source)
    row[id_index] = int(new_id)
    if name is not None and "Name" in item_list.fields:
        set_value(elements, item_list, row, "Name", name)
    item_list.rows.append(row)
    return len(item_list.rows) - 1, row


def npc_record(elements: Elements156, npc_id: int):
    item_list, index, row = row_by_id(elements, "NPC_ESSENCE", npc_id)
    service_fields = [field for field in item_list.fields if field.startswith("id_") and field.endswith("_service")]
    return {
        "index": index,
        "id": int(value(elements, item_list, row, "ID")),
        "name": str(value(elements, item_list, row, "Name")),
        "services": {field: int(value(elements, item_list, row, field)) for field in service_fields},
    }


def service_slots(elements: Elements156, suffix: str, service_id: int):
    item_list, index, row = row_by_id(elements, suffix, service_id)
    pattern = SELL_SLOT_RE if suffix == "NPC_SELL_SERVICE" else MAKE_SLOT_RE
    slots = []
    for field in item_list.fields:
        match = pattern.match(field)
        if not match:
            continue
        record_id = int(value(elements, item_list, row, field))
        if record_id:
            slots.append({"page": int(match.group(1)), "slot": int(match.group(2)),
                          "field": field, "id": record_id})
    return item_list, index, row, slots


def set_service_slot(elements: Elements156, suffix: str, service_id: int,
                     page: int, slot: int, record_id: int):
    item_list, _, row = row_by_id(elements, suffix, service_id)
    field = (f"pages_{page}_goods_{slot}_id" if suffix == "NPC_SELL_SERVICE"
             else f"pages_{page}_id_goods_{slot}")
    if field not in item_list.fields:
        raise ValueError("Page atau slot service tidak valid")
    set_value(elements, item_list, row, field, int(record_id))


def resolve_item(elements: Elements156, item_id: int):
    matches = []
    excluded = ("NPC_", "MONSTER_", "FACE_", "RECIPE_", "TASK_")
    for item_list in elements.lists:
        if not item_list.name.endswith("_ESSENCE") or item_list.name.split(" - ", 1)[-1].startswith(excluded):
            continue
        if not all(field in item_list.fields for field in ("ID", "Name")):
            continue
        id_index = item_list.field_index("ID")
        for index, row in enumerate(item_list.rows):
            if row[id_index] == int(item_id):
                matches.append((item_list, index, row))
    if not matches:
        raise ValueError(f"Item ID {item_id} tidak ditemukan")
    priced = [match for match in matches if "price" in match[0].fields]
    return (priced or matches)[0]


def item_summary(elements: Elements156, item_id: int):
    item_list, index, row = resolve_item(elements, item_id)
    return {
        "list": item_list.name, "index": index, "id": int(item_id),
        "name": str(value(elements, item_list, row, "Name")),
        "price": int(value(elements, item_list, row, "price")) if "price" in item_list.fields else None,
    }


def set_item_price(elements: Elements156, item_id: int, price: int):
    if not 0 <= int(price) <= 2_147_483_647:
        raise ValueError("Harga item tidak valid")
    item_list, _, row = resolve_item(elements, item_id)
    if "price" not in item_list.fields:
        raise ValueError("Template item ini tidak memiliki harga merchant")
    set_value(elements, item_list, row, "price", int(price))


def recipe_record(elements: Elements156, recipe_id: int):
    item_list, index, row = row_by_id(elements, "RECIPE_ESSENCE", recipe_id)
    targets = []
    for number in range(1, 5):
        item_id = int(value(elements, item_list, row, f"targets_{number}_id_to_make"))
        probability = float(value(elements, item_list, row, f"targets_{number}_probability"))
        if item_id:
            targets.append({"slot": number, "item_id": item_id, "probability": probability})
    materials = []
    for number in range(1, 33):
        item_id = int(value(elements, item_list, row, f"materials_{number}_id"))
        amount = int(value(elements, item_list, row, f"materials_{number}_num"))
        if item_id:
            materials.append({"slot": number, "item_id": item_id, "amount": amount})
    return {
        "index": index, "id": int(recipe_id), "name": str(value(elements, item_list, row, "Name")),
        "num_to_make": int(value(elements, item_list, row, "num_to_make")),
        "price": int(value(elements, item_list, row, "price")),
        "duration": int(value(elements, item_list, row, "duration")),
        "targets": targets, "materials": materials,
    }


def set_recipe_material(elements: Elements156, recipe_id: int, slot: int, item_id: int, amount: int):
    if not 1 <= int(slot) <= 32 or int(item_id) < 0 or int(amount) < 0:
        raise ValueError("Bahan recipe tidak valid")
    item_list, _, row = row_by_id(elements, "RECIPE_ESSENCE", recipe_id)
    set_value(elements, item_list, row, f"materials_{slot}_id", int(item_id))
    set_value(elements, item_list, row, f"materials_{slot}_num", int(amount) if item_id else 0)


def clone_npc_bundle(elements: Elements156, source_npc_id: int, new_npc_id: int, new_name: str,
                     new_sell_service_id: int | None = None, new_make_service_id: int | None = None):
    _, npc_row = clone_record(elements, "NPC_ESSENCE", source_npc_id, new_npc_id, new_name)
    npc_list = elements.list_named("NPC_ESSENCE")
    source = npc_record(elements, source_npc_id)
    if new_sell_service_id is not None:
        source_id = source["services"]["id_sell_service"]
        if not source_id:
            raise ValueError("NPC sumber tidak memiliki Sell Service")
        clone_record(elements, "NPC_SELL_SERVICE", source_id, new_sell_service_id, f"{new_name} Shop")
        set_value(elements, npc_list, npc_row, "id_sell_service", int(new_sell_service_id))
    if new_make_service_id is not None:
        source_id = source["services"]["id_make_service"]
        if not source_id:
            raise ValueError("NPC sumber tidak memiliki Make Service")
        clone_record(elements, "NPC_MAKE_SERVICE", source_id, new_make_service_id, f"{new_name} Craft")
        set_value(elements, npc_list, npc_row, "id_make_service", int(new_make_service_id))
    return npc_record(elements, new_npc_id)
