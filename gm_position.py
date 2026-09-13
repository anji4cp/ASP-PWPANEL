#!/usr/bin/env python3
"""Read-only saved GM position helper for PW Data Studio."""

import argparse
import datetime
import json
import struct
import sys

try:
    from sync_characters import ProtocolError, request
except ModuleNotFoundError:
    # User-scoped SSH installs remain standalone while reusing the audited
    # read-only gamedbd protocol implementation deployed with the web portal.
    sys.path.insert(0, "/opt/pw155-web")
    from sync_characters import ProtocolError, request


GET_ROLE_STATUS = 3015


def parse_role_status(payload, role_id):
    from sync_characters import Reader

    reader = Reader(payload)
    reader.u32()  # handle/request id
    retcode = reader.u32()
    if retcode != 0:
        raise ProtocolError(f"GetRoleStatus retcode={retcode}")
    status_version = reader.u8()
    level = reader.u32()
    reader.u32()  # level2/cultivation
    reader.u32()  # exp
    reader.u32()  # spirit
    reader.u32()  # stat points
    reader.u32()  # hp
    reader.u32()  # mp
    # Decode floats here so this standalone helper also works beside older
    # deployed sync_characters.py versions that do not expose Reader.f32().
    x, y, z = (struct.unpack(">f", reader.take(4))[0] for _ in range(3))
    world_tag = reader.u32()
    if not 0 < role_id <= 0x7FFFFFFF or not 0 < world_tag <= 0x7FFFFFFF:
        raise ProtocolError("Role ID atau world tag tersimpan tidak valid")
    return {
        "roleId": role_id,
        "worldTag": world_tag,
        "x": x,
        "y": y,
        "z": z,
        "capturedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "saved": True,
        "statusVersion": status_version,
        "level": level,
    }


def saved_position(role_id):
    opcode, payload = request(GET_ROLE_STATUS, struct.pack(">II", 0xFFFFFFFF, role_id))
    if opcode != GET_ROLE_STATUS:
        raise ProtocolError(f"Opcode GetRoleStatus tidak cocok: {opcode}")
    return parse_role_status(payload, role_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-id", required=True, type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not 0 < args.role_id <= 0x7FFFFFFF:
        parser.error("role-id tidak valid")
    print(json.dumps(saved_position(args.role_id), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProtocolError) as error:
        print(f"GM_POSITION_ERROR {error}", file=sys.stderr)
        raise SystemExit(1)
