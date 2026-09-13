#!/usr/bin/env python3
"""Small, allow-listed PW GameDB mutations used by ASP PWPanel.

Only two operations are exposed: add pocket coins and move an offline role to
the configured safe point.  The caller is responsible for ownership/offline
checks; GameDB remains the source of truth.
"""

import os
import struct

from sync_characters import ProtocolError, request


DB_MODIFY_ROLE_DATA = 8005
GET_ROLE_STATUS = 3015
PUT_ROLE_STATUS = 3014
POCKET_MONEY = 0x000004
SAFE_WORLD_TAG = int(os.environ.get("PW155_SAFE_WORLD_TAG", "1"))
SAFE_X = float(os.environ.get("PW155_SAFE_X", "1286.669"))
SAFE_Y = float(os.environ.get("PW155_SAFE_Y", "219.375"))
SAFE_Z = float(os.environ.get("PW155_SAFE_Z", "1044.339"))
MAX_COIN_GRANT = 200_000_000


def _retcode(payload, operation):
    if len(payload) < 8:
        raise ProtocolError(f"Respons {operation} terpotong")
    retcode = struct.unpack_from(">I", payload, 4)[0]
    if retcode != 0:
        raise ProtocolError(f"{operation} retcode={retcode}")
    return retcode


def add_pocket_coins(role_id, amount):
    role_id, amount = int(role_id), int(amount)
    if not 0 < role_id <= 0x7FFFFFFF:
        raise ValueError("Role ID tidak valid")
    if not 0 < amount <= MAX_COIN_GRANT:
        raise ValueError("Jumlah coin di luar batas")
    # RPC handle, roleid, mask, level, exp, pocket/store money, and remaining
    # allow-listed DBModifyRoleData fields. All integers are network byte order.
    payload = struct.pack(
        ">IIIiqiiiiii",
        0xFFFFFFFF, role_id, POCKET_MONEY, 0, 0, amount, 0, 0, 0, 0, 0,
    )
    opcode, response = request(DB_MODIFY_ROLE_DATA, payload)
    if opcode != DB_MODIFY_ROLE_DATA:
        raise ProtocolError(f"Opcode DBModifyRoleData tidak cocok: {opcode}")
    _retcode(response, "DBModifyRoleData")
    total = struct.unpack_from(">q", response, 8)[0] if len(response) >= 16 else None
    return {"role_id": role_id, "amount": amount, "total_money": total}


def move_to_safe_point(role_id):
    role_id = int(role_id)
    if not 0 < role_id <= 0x7FFFFFFF:
        raise ValueError("Role ID tidak valid")
    opcode, response = request(
        GET_ROLE_STATUS, struct.pack(">II", 0xFFFFFFFF, role_id))
    if opcode != GET_ROLE_STATUS:
        raise ProtocolError(f"Opcode GetRoleStatus tidak cocok: {opcode}")
    _retcode(response, "GetRoleStatus")
    status = bytearray(response[8:])
    # GRoleStatus starts with version byte + seven ints. Coordinates and
    # worldtag follow at offsets 29, 33, 37, and 41 respectively.
    if len(status) < 45:
        raise ProtocolError("GRoleStatus terpotong")
    previous = {
        "x": struct.unpack_from(">f", status, 29)[0],
        "y": struct.unpack_from(">f", status, 33)[0],
        "z": struct.unpack_from(">f", status, 37)[0],
        "world_tag": struct.unpack_from(">I", status, 41)[0],
    }
    struct.pack_into(">fffI", status, 29, SAFE_X, SAFE_Y, SAFE_Z, SAFE_WORLD_TAG)
    opcode, put_response = request(
        PUT_ROLE_STATUS, struct.pack(">II", 0xFFFFFFFF, role_id) + status)
    if opcode != PUT_ROLE_STATUS:
        raise ProtocolError(f"Opcode PutRoleStatus tidak cocok: {opcode}")
    _retcode(put_response, "PutRoleStatus")
    return {
        "role_id": role_id,
        "previous": previous,
        "destination": {"x": SAFE_X, "y": SAFE_Y, "z": SAFE_Z,
                        "world_tag": SAFE_WORLD_TAG},
    }
