#!/usr/bin/env python3
"""Sinkronisasi read-only karakter gamedbd ke cache MariaDB pw.roles."""

import argparse
import json
import os
import socket
import struct
import subprocess
import sys


GAMEDBD_HOST = os.environ.get("PW155_GAMEDBD_HOST", "127.0.0.1")
GAMEDBD_PORT = int(os.environ.get("PW155_GAMEDBD_PORT", "29400"))
DB_CONFIG = os.environ.get("PW155_SYNC_DB_CONFIG", "/etc/pw155-sync/db.cnf")
GET_USER_ROLES = 0xD49
GET_ROLE_BASE = 0x1F43
MAX_PACKET = 1024 * 1024
MAX_CHARACTERS = 16


class ProtocolError(RuntimeError):
    pass


def encode_cuint(value):
    if value < 0:
        raise ValueError("CUInt tidak menerima nilai negatif")
    if value < 64:
        return struct.pack("B", value)
    if value < 16384:
        return struct.pack(">H", value | 0x8000)
    if value < 536870912:
        return struct.pack(">I", value | 0xC0000000)
    if value <= 0xFFFFFFFF:
        return b"\xE0" + struct.pack(">I", value)
    raise ValueError("CUInt terlalu besar")


def decode_cuint(data, offset=0):
    if offset >= len(data):
        raise ProtocolError("CUInt terpotong")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    if first < 0xC0:
        if offset + 2 > len(data):
            raise ProtocolError("CUInt16 terpotong")
        return struct.unpack_from(">H", data, offset)[0] & 0x3FFF, offset + 2
    if first < 0xE0:
        if offset + 4 > len(data):
            raise ProtocolError("CUInt32 terpotong")
        return struct.unpack_from(">I", data, offset)[0] & 0x1FFFFFFF, offset + 4
    if offset + 5 > len(data):
        raise ProtocolError("CUInt panjang terpotong")
    return struct.unpack_from(">I", data, offset + 1)[0], offset + 5


class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def take(self, length):
        if length < 0 or self.offset + length > len(self.data):
            raise ProtocolError("Respons gamedbd terpotong")
        value = self.data[self.offset:self.offset + length]
        self.offset += length
        return value

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return struct.unpack(">I", self.take(4))[0]

    def f32(self):
        return struct.unpack(">f", self.take(4))[0]

    def cuint(self):
        value, self.offset = decode_cuint(self.data, self.offset)
        return value

    def octets(self):
        size = self.cuint()
        if size > MAX_PACKET:
            raise ProtocolError("Octets melewati batas")
        return self.take(size)

    def ustring(self):
        raw = self.octets()
        if len(raw) % 2:
            raise ProtocolError("UTF-16 memiliki panjang ganjil")
        return raw.decode("utf-16le", "strict").rstrip("\x00")


def frame(opcode, payload):
    return encode_cuint(opcode) + encode_cuint(len(payload)) + payload


def receive_frame(sock):
    data = bytearray()
    expected = None
    header_size = None
    while len(data) < MAX_PACKET:
        chunk = sock.recv(8192)
        if not chunk:
            break
        data.extend(chunk)
        if expected is None:
            try:
                _, first_end = decode_cuint(data, 0)
                payload_length, second_end = decode_cuint(data, first_end)
            except ProtocolError:
                continue
            if payload_length > MAX_PACKET:
                raise ProtocolError("Respons gamedbd terlalu besar")
            header_size = second_end
            expected = header_size + payload_length
        if expected is not None and len(data) >= expected:
            break
    if expected is None or len(data) < expected:
        raise ProtocolError("Respons gamedbd tidak lengkap")
    opcode, offset = decode_cuint(data, 0)
    payload_length, offset = decode_cuint(data, offset)
    return opcode, bytes(data[offset:offset + payload_length])


def request(opcode, payload):
    with socket.create_connection((GAMEDBD_HOST, GAMEDBD_PORT), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall(frame(opcode, payload))
        return receive_frame(sock)


def user_roles(account_id):
    response_opcode, payload = request(
        GET_USER_ROLES, struct.pack(">II", 0xFFFFFFFF, int(account_id)))
    if response_opcode != GET_USER_ROLES:
        raise ProtocolError(f"Opcode GetUserRoles tidak cocok: {response_opcode}")
    reader = Reader(payload)
    reader.u32()  # handle/request id
    retcode = reader.u32()
    if retcode != 0:
        raise ProtocolError(f"GetUserRoles retcode={retcode}")
    count = reader.cuint()
    if count > MAX_CHARACTERS:
        raise ProtocolError(f"Jumlah karakter tidak wajar: {count}")
    roles = []
    for _ in range(count):
        role_id = reader.u32()
        role_name = reader.ustring()
        if not 0 < role_id <= 0x7FFFFFFF or not 1 <= len(role_name) <= 64:
            raise ProtocolError("Identitas karakter tidak valid")
        roles.append({"role_id": role_id, "role_name": role_name})
    return roles


def role_base(role_id):
    response_opcode, payload = request(
        GET_ROLE_BASE, struct.pack(">II", 0xFFFFFFFF, int(role_id)))
    if response_opcode != GET_ROLE_BASE:
        raise ProtocolError(f"Opcode GetRoleBase tidak cocok: {response_opcode}")
    reader = Reader(payload)
    reader.u32()  # handle/request id
    retcode = reader.u32()
    if retcode != 0:
        raise ProtocolError(f"GetRoleBase retcode={retcode}")
    reader.u8()
    reader.u32()
    base_name = reader.ustring()
    reader.u32()
    raw_class = reader.u32()
    gender = reader.u8()
    reader.octets()
    reader.octets()
    reader.u32()
    status = reader.u8()
    delete_time = reader.u32()
    reader.u32()
    last_login = reader.u32()
    forbid_count = reader.cuint()
    if forbid_count > 128:
        raise ProtocolError("Jumlah forbid tidak wajar")
    for _ in range(forbid_count):
        reader.u8()
        reader.u32()
        reader.u32()
        reader.ustring()
    reader.octets()
    reader.u32()
    reader.u32()
    reader.octets()
    reader.u8()
    reader.u8()
    reader.u8()
    reader.u8()
    level = reader.u32()
    cultivation = reader.u32()
    if level > 200 or raw_class > 32 or gender > 1:
        raise ProtocolError("Atribut karakter melewati batas")
    return {
        "base_name": base_name,
        "raw_class": raw_class,
        "gender": gender,
        "level": level,
        "cultivation": cultivation,
        "status": status,
        "delete_time": delete_time,
        "last_login": last_login,
        "forbid_count": forbid_count,
    }


def fetch_account(account_id):
    characters = []
    for role in user_roles(account_id):
        detail = role_base(role["role_id"])
        if detail["base_name"] and detail["base_name"] != role["role_name"]:
            raise ProtocolError("Nama karakter berbeda antara dua respons")
        characters.append({**role, **detail})
    return characters


def sql_literal(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def database_accounts(account_id=None):
    query = "SELECT ID FROM pw.users"
    if account_id is not None:
        query += f" WHERE ID={int(account_id)}"
    query += " ORDER BY ID;"
    result = subprocess.run(
        ["/usr/bin/mariadb", f"--defaults-extra-file={DB_CONFIG}",
         "--batch", "--skip-column-names"], input=query, text=True,
        capture_output=True, timeout=5, check=False)
    if result.returncode != 0:
        raise RuntimeError("Tidak dapat membaca daftar akun")
    return [int(line) for line in result.stdout.splitlines() if line.strip()]


def replace_cache(account_id, characters):
    statements = ["START TRANSACTION;",
                  f"DELETE FROM pw.roles WHERE account_id={int(account_id)};"]
    for character in characters:
        # Field yang belum tersedia dari dua RPC read-only disimpan sebagai nilai netral.
        values = [
            int(account_id), int(character["role_id"]),
            sql_literal(character["role_name"]), int(character["level"]),
            0, int(character["raw_class"]), int(character["gender"]),
            0, 0, "''", 0, "''", 0, 0, 0, 0,
        ]
        statements.append(
            "INSERT INTO pw.roles(account_id,role_id,role_name,role_level,"
            "role_race,role_occupation,role_gender,role_spouse,faction_id,"
            "faction_name,faction_level,faction_domains,role_faction_rank,"
            "pvp_time,pvp_kills,pvp_deads) VALUES ({});".format(
                ",".join(map(str, values))))
    statements.append("COMMIT;")
    result = subprocess.run(
        ["/usr/bin/mariadb", f"--defaults-extra-file={DB_CONFIG}",
         "--batch", "--skip-column-names"], input="\n".join(statements),
        text=True, capture_output=True, timeout=8, check=False)
    if result.returncode != 0:
        raise RuntimeError("Gagal memperbarui cache karakter")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.account_id is None and not args.all:
        parser.error("pilih --account-id ID atau --all")
    account_ids = ([args.account_id] if args.account_id is not None
                   else database_accounts())
    synced = []
    for account_id in account_ids:
        characters = fetch_account(account_id)
        if not args.dry_run:
            replace_cache(account_id, characters)
        synced.append({"account_id": account_id, "characters": characters})
    if args.dry_run:
        print(json.dumps(synced, ensure_ascii=False, indent=2))
    else:
        print(f"SYNCED accounts={len(synced)} characters="
              f"{sum(len(item['characters']) for item in synced)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProtocolError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"SYNC_ERROR {error}", file=sys.stderr)
        raise SystemExit(1)
