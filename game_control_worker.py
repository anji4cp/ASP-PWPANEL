#!/usr/bin/env python3
"""Allowlisted PW155 gameplay administration and safe-shutdown worker."""

import argparse
import hashlib
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


CONTROL_DIR = Path(os.environ.get("PW155_GAME_CONTROL_DIR", "/var/lib/pw155-game-control"))
REQUEST_DIR = CONTROL_DIR / "requests"
SERVICE_SCRIPT = Path(os.environ.get(
    "PW155_SERVICE_SCRIPT", "/srv/pw155/tools/pw155-service.sh"
))
PROVIDER_HOST = os.environ.get("PW155_PROVIDER_HOST", "127.0.0.1")
PROVIDER_PORT = int(os.environ.get("PW155_PROVIDER_PORT", "29300"))
DELIVERY_HOST = os.environ.get("PW155_DELIVERY_HOST", "127.0.0.1")
DELIVERY_PORT = int(os.environ.get("PW155_DELIVERY_PORT", "29100"))
WORLD_CHAT_OPCODE = int(os.environ.get("PW155_WORLD_CHAT_OPCODE", "120"))
ANNOUNCE_LINK_TYPE_OPCODE = int(os.environ.get("PW155_ANNOUNCE_LINK_TYPE_OPCODE", "372"))
GM_SET_GAME_ATTR_OPCODE = int(os.environ.get("PW155_GM_SET_ATTR_OPCODE", "377"))
SYS_SEND_MAIL_OPCODE = int(os.environ.get("PW155_SYS_SEND_MAIL_OPCODE", "4214"))
SYS_SEND_MAIL_RESPONSE_OPCODE = int(os.environ.get(
    "PW155_SYS_SEND_MAIL_RESPONSE_OPCODE", "4215"
))
EXP_RATE_ATTRIBUTE = 204
DOUBLE_MONEY_ATTRIBUTE = 211
EXP_MULTIPLIERS = {1, 2, 3, 4, 5, 6, 8, 10}
GOLD_MULTIPLIERS = {1, 2}
TEST_MATERIAL_ID = 21652
TEST_ROLE_ID = 1024
TEST_MATERIAL_MAX_COUNT = 1000
TEST_ELEMENTS_SHA256 = "db4dcd45fb2d77ea845e8e859f1874024b11836bf459119003d0b7b620235c97"
TEST_ELEMENTS_PATH = Path("/srv/pw155/staging/pw155/gamed/config/elements.data")
MATERIAL_CATALOG_PATH = Path(__file__).with_name("material_catalog.json")
POLL_SECONDS = max(0.2, float(os.environ.get("PW155_GAME_CONTROL_POLL", "1")))
COUNTDOWN_MARKS = {
    86400, 43200, 21600, 10800, 7200, 3600, 1800, 900, 600, 300,
    180, 120, 60, 30, 20, 15, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1,
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path, fallback):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def atomic_json(path, value):
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(CONTROL_DIR))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temporary, 0o640)
        try:
            import grp
            os.chown(temporary, 0, grp.getgrnam("pwgamectl").gr_gid)
        except (ImportError, KeyError, PermissionError):
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def default_state():
    return {
        "updated_at": now_iso(), "busy": False, "scheduled": None,
        "last_action": None, "error": None,
        "rates": {"exp": 1, "gold": 1},
    }


def save_state(state):
    state["updated_at"] = now_iso()
    atomic_json(CONTROL_DIR / "status.json", state)


def cuint(value):
    value = int(value)
    if value < 0 or value > 0xFFFFFFFF:
        raise ValueError("cuint is outside the supported range")
    if value < 0x40:
        return bytes((value,))
    if value < 0x4000:
        return struct.pack("!H", value | 0x8000)
    if value < 0x20000000:
        return struct.pack("!I", value | 0xC0000000)
    return b"\xE0" + struct.pack("!I", value)


def public_chat_packet(message, channel=9, role_id=-1):
    encoded = str(message).encode("utf-16le")
    payload = (
        struct.pack("!BBI", int(channel), 0, int(role_id) & 0xFFFFFFFF)
        + cuint(len(encoded)) + encoded + cuint(0)
    )
    return cuint(WORLD_CHAT_OPCODE) + cuint(len(payload)) + payload


def broadcast(message, role_id=-1):
    packet = public_chat_packet(message, role_id=role_id)
    with socket.create_connection((PROVIDER_HOST, PROVIDER_PORT), timeout=3) as connection:
        connection.sendall(packet)


def decode_cuint(data, offset=0):
    if offset >= len(data):
        raise RuntimeError("Provider returned a truncated CUInt")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    if first < 0xC0:
        if offset + 2 > len(data):
            raise RuntimeError("Provider returned a truncated CUInt16")
        return struct.unpack_from("!H", data, offset)[0] & 0x3FFF, offset + 2
    if first < 0xE0:
        if offset + 4 > len(data):
            raise RuntimeError("Provider returned a truncated CUInt32")
        return struct.unpack_from("!I", data, offset)[0] & 0x1FFFFFFF, offset + 4
    if offset + 5 > len(data):
        raise RuntimeError("Provider returned a truncated long CUInt")
    return struct.unpack_from("!I", data, offset + 1)[0], offset + 5


def receive_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise RuntimeError("Provider response is incomplete")
        data.extend(chunk)
    return bytes(data)


def receive_cuint(connection):
    first = receive_exact(connection, 1)
    marker = first[0]
    if marker < 0x80:
        return marker
    if marker < 0xC0:
        return struct.unpack("!H", first + receive_exact(connection, 1))[0] & 0x3FFF
    if marker < 0xE0:
        return struct.unpack("!I", first + receive_exact(connection, 3))[0] & 0x1FFFFFFF
    return struct.unpack("!I", receive_exact(connection, 4))[0]


def receive_frame(connection, limit=1024 * 1024):
    # Read exactly one frame. Provider can concatenate unsolicited status frames
    # with the requested response, so bytes belonging to the next frame must not
    # be consumed and discarded here.
    opcode = receive_cuint(connection)
    length = receive_cuint(connection)
    if length > limit:
        raise RuntimeError("Provider response exceeds the size limit")
    return opcode, receive_exact(connection, length)


def delivery_request(opcode, payload, expected_opcode=None):
    wanted = opcode if expected_opcode is None else expected_opcode
    skipped = []
    with socket.create_connection((DELIVERY_HOST, DELIVERY_PORT), timeout=5) as connection:
        connection.settimeout(8)
        # iWeb identifies itself to GDeliveryServer with link_type 0 before it
        # sends GM RPC or system-mail protocols. Port 29300 is the separate
        # provider endpoint used by game servers and one-way broadcasts.
        announce = bytes((0,))
        connection.sendall(
            cuint(ANNOUNCE_LINK_TYPE_OPCODE) + cuint(len(announce)) + announce
        )
        connection.sendall(cuint(opcode) + cuint(len(payload)) + payload)
        for _ in range(32):
            try:
                response_opcode, response = receive_frame(connection)
            except socket.timeout as error:
                detail = f" after skipping {skipped}" if skipped else ""
                raise RuntimeError(
                    f"Delivery response {wanted} timed out{detail}"
                ) from error
            if response_opcode == wanted:
                return response
            skipped.append(response_opcode)
    raise RuntimeError(
        f"Delivery response {wanted} was not received; skipped opcodes {skipped}"
    )


def set_game_attribute(attribute, value):
    if not 0 <= int(attribute) <= 255 or not 0 <= int(value) <= 255:
        raise ValueError("Game attribute is outside the byte range")
    # RPC handle, GM role, local session, attribute byte, and one-byte Octets value.
    payload = (
        struct.pack("!III", 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
        + struct.pack("!B", int(attribute))
        + cuint(1)
        + struct.pack("!B", int(value))
    )
    response = delivery_request(GM_SET_GAME_ATTR_OPCODE, payload)
    if len(response) < 8:
        raise RuntimeError("GMSetGameAttri response is truncated")
    retcode = struct.unpack_from("!I", response, 4)[0]
    if retcode != 0:
        raise RuntimeError(f"GMSetGameAttri retcode={retcode}")


def set_game_rates(exp_multiplier, gold_multiplier):
    exp_multiplier = int(exp_multiplier)
    gold_multiplier = int(gold_multiplier)
    if exp_multiplier not in EXP_MULTIPLIERS or gold_multiplier not in GOLD_MULTIPLIERS:
        raise ValueError("Unsupported EXP or gold multiplier")
    # This PW155 branch stores EXP in tenths (10 = x1, 20 = x2).
    set_game_attribute(EXP_RATE_ATTRIBUTE, exp_multiplier * 10)
    set_game_attribute(DOUBLE_MONEY_ATTRIBUTE, 1 if gold_multiplier == 2 else 0)
    return {"exp": exp_multiplier, "gold": gold_multiplier}


def octets(value):
    raw = str(value).encode("utf-8")
    return cuint(len(raw)) + raw


def send_item_mail(role_id, item_id, count, proctype=0):
    if not all(type(value) is int for value in (role_id, item_id, count, proctype)):
        raise ValueError("Field pengiriman material harus bilangan bulat")
    if not 0 < role_id <= 0x7FFFFFFF or proctype != 0:
        raise ValueError("Role ID atau proctype tidak valid")
    max_count, asset_proctype = verified_material(item_id, count)
    return transmit_material_mail(
        role_id, item_id, count, max_count, asset_proctype,
        "Hadiah Admin PWKU", "Ambil material ini dari mailbox ke tas karakter.",
    )


def verified_material(item_id, count):
    if type(item_id) is not int or type(count) is not int:
        raise ValueError("Item ID dan jumlah harus bilangan bulat")
    catalog = load_json(MATERIAL_CATALOG_PATH, None)
    if (not isinstance(catalog, dict) or catalog.get("version") != 156
            or catalog.get("category") != "MATERIAL_ESSENCE"
            or catalog.get("elements_sha256") != TEST_ELEMENTS_SHA256
            or not isinstance(catalog.get("items"), dict)):
        raise ValueError("Katalog material tidak tersedia atau tidak cocok")
    entry = catalog["items"].get(str(item_id))
    if not isinstance(entry, dict):
        raise ValueError("Item ID bukan material yang didukung; equipment ditolak")
    max_count, asset_proctype = entry.get("max_count"), entry.get("proctype")
    if (type(max_count) is not int or not 1 <= max_count <= 32767
            or asset_proctype != 0):
        raise ValueError("Definisi material tidak valid")
    if not 1 <= count <= min(max_count, 9999):
        raise ValueError(f"Jumlah material harus 1 sampai {min(max_count, 9999)}")
    if sha256_file(TEST_ELEMENTS_PATH) != TEST_ELEMENTS_SHA256:
        raise ValueError("elements.data VM berbeda dari katalog material")
    return max_count, asset_proctype


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transmit_material_mail(role_id, item_id, count, max_count, proctype,
                           title, context):
    transaction_id = int(time.time() * 1000) & 0x7FFFFFFF
    # PW155 qiweb GRoleInventory: id, pos, timestamp_low, count,
    # timestamp_high, max_count, data, proctype, expire_date, guid1, guid2.
    inventory = (
        struct.pack("!IIhhhh", item_id, 0, 0, count, 0, max_count)
        + cuint(0)
        + struct.pack("!IIIII", proctype, 0, 0, 0, 0)
    )
    payload = (
        struct.pack("!IIBI", transaction_id, 32, 3, role_id)
        + octets(title) + octets(context)
        + inventory + struct.pack("!I", 0)  # SysSendMail.attach_money
    )
    response = delivery_request(
        SYS_SEND_MAIL_OPCODE, payload, SYS_SEND_MAIL_RESPONSE_OPCODE
    )
    if len(response) != 6:
        raise RuntimeError("SysSendMail response length is invalid")
    retcode, returned_id = struct.unpack("!hI", response)
    if returned_id != transaction_id:
        raise RuntimeError("SysSendMail transaction ID does not match")
    if retcode != 0:
        raise RuntimeError(f"SysSendMail retcode={retcode}")
    return {"role_id": role_id, "item_id": item_id, "count": count,
            "max_count": max_count, "proctype": proctype,
            "result": "mail_accepted_not_claimed"}


def verified_test_material(item_id):
    """One known v156 material; never accept equipment or a changed asset."""
    if item_id != TEST_MATERIAL_ID:
        raise ValueError("Percobaan hanya mendukung material ID 21652")
    if sha256_file(TEST_ELEMENTS_PATH) != TEST_ELEMENTS_SHA256:
        raise ValueError("elements.data VM berbeda dari definisi material yang diperiksa")
    return TEST_MATERIAL_MAX_COUNT


def send_test_material_mail(role_id, item_id=TEST_MATERIAL_ID, count=1):
    """Explicit, material-only trial. A successful reply means mail accepted only."""
    if role_id != TEST_ROLE_ID:
        raise ValueError("Percobaan dibatasi untuk role ID 1024")
    if count != 1:
        raise ValueError("Percobaan dibatasi satu material per surat")
    max_count = verified_test_material(item_id)
    return transmit_material_mail(
        role_id, item_id, count, max_count, 0,
        "Uji Material PWKU", "Percobaan ID 21652; verifikasi saat diambil dari mailbox.",
    )


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} hour(s)"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} minute(s)"
    return f"{seconds} second(s)"


def validate_request(request):
    action = request.get("action")
    if action not in ("broadcast", "schedule-shutdown", "cancel-shutdown",
                      "set-rates", "send-item"):
        raise ValueError("Unsupported game-control action")
    if action == "broadcast":
        message = str(request.get("message", "")).strip()
        if not 1 <= len(message) <= 200 or any(ord(char) < 32 for char in message):
            raise ValueError("Broadcast must contain 1-200 printable characters")
    elif action == "schedule-shutdown":
        seconds = request.get("seconds")
        if not isinstance(seconds, int) or not 10 <= seconds <= 86400:
            raise ValueError("Shutdown countdown must be 10-86400 seconds")
        reason = str(request.get("reason", "")).strip()
        if not 3 <= len(reason) <= 120 or any(ord(char) < 32 for char in reason):
            raise ValueError("Shutdown reason must contain 3-120 printable characters")
    elif action == "set-rates":
        if request.get("exp_multiplier") not in EXP_MULTIPLIERS:
            raise ValueError("Unsupported EXP multiplier")
        if request.get("gold_multiplier") not in GOLD_MULTIPLIERS:
            raise ValueError("Unsupported gold multiplier")
    elif action == "send-item":
        role_id, item_id, count, proctype = (
            request.get("role_id"), request.get("item_id"),
            request.get("count"), request.get("proctype", 0),
        )
        if type(role_id) is not int or not 0 < role_id <= 0x7FFFFFFF:
            raise ValueError("Role ID tidak valid")
        if proctype != 0:
            raise ValueError("proctype dari panel tidak boleh diubah")
        verified_material(item_id, count)
    return action


def action_record(request, status, message):
    return {
        "id": str(request.get("id", ""))[:64],
        "action": str(request.get("action", ""))[:32],
        "actor": str(request.get("actor", "admin"))[:20],
        "requested_at": str(request.get("requested_at", "-"))[:40],
        "finished_at": now_iso(), "status": status, "message": str(message)[:1200],
    }


def process_request(path, state):
    request = load_json(path, None)
    if not isinstance(request, dict):
        raise ValueError("Request is not a JSON object")
    action = validate_request(request)
    if action == "broadcast":
        rendered = str(request["message"]).strip()
        broadcast(rendered)
        state["last_action"] = action_record(
            request, "completed", f"Broadcast sent: {rendered}"
        )
        state["error"] = None
    elif action == "schedule-shutdown":
        if state.get("scheduled"):
            raise ValueError("A safe shutdown is already scheduled")
        seconds = request["seconds"]
        schedule = {
            "id": str(request.get("id", path.stem))[:64],
            "actor": str(request.get("actor", "admin"))[:20],
            "reason": str(request["reason"])[:120],
            "countdown_seconds": seconds,
            "created_at": now_iso(), "execute_at": int(time.time()) + seconds,
            "announced": [],
        }
        state["scheduled"] = schedule
        broadcast(
            f"Safe shutdown in {format_duration(seconds)}. "
            f"Reason: {schedule['reason']}"
        )
        schedule["announced"].append(seconds)
        state["last_action"] = action_record(
            request, "scheduled", f"Shutdown scheduled in {seconds} seconds"
        )
        state["error"] = None
    elif action == "cancel-shutdown":
        schedule = state.get("scheduled")
        if not schedule:
            raise ValueError("No safe shutdown is scheduled")
        state["scheduled"] = None
        broadcast("The scheduled shutdown has been cancelled.")
        state["last_action"] = action_record(request, "completed", "Shutdown cancelled")
        state["error"] = None
    elif action == "set-rates":
        rates = set_game_rates(request["exp_multiplier"], request["gold_multiplier"])
        state["rates"] = rates
        state["last_action"] = action_record(
            request, "completed",
            f"Game rates updated: EXP x{rates['exp']}, gold x{rates['gold']}"
        )
        state["error"] = None
    else:
        delivered = send_item_mail(
            request["role_id"], request["item_id"], request["count"],
            request.get("proctype", 0),
        )
        state["last_action"] = action_record(
            request, "mail-accepted",
            (f"Material {delivered['item_id']} x{delivered['count']} diterima "
             f"sistem surat untuk role {delivered['role_id']}; "
             "belum terverifikasi di tas"),
        )
        state["error"] = None
    save_state(state)


def process_queue(state):
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(REQUEST_DIR.glob("*.json")):
        state["busy"] = True
        save_state(state)
        try:
            process_request(path, state)
        except Exception as error:
            request = load_json(path, {"id": path.stem, "action": "unknown"})
            state["last_action"] = action_record(request, "failed", error)
            state["error"] = str(error)[:1200]
        finally:
            state["busy"] = False
            save_state(state)
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def tick_shutdown(state):
    schedule = state.get("scheduled")
    if not isinstance(schedule, dict):
        return
    remaining = max(0, int(schedule.get("execute_at", 0) - time.time() + 0.999))
    announced = set(int(value) for value in schedule.get("announced", []) if isinstance(value, int))
    crossed_marks = {
        mark for mark in COUNTDOWN_MARKS
        if remaining <= mark < int(schedule.get("countdown_seconds", 0))
        and mark not in announced
    }
    if crossed_marks:
        try:
            broadcast(f"Safe shutdown in {format_duration(remaining)}.")
            announced.update(crossed_marks)
            schedule["announced"] = sorted(announced, reverse=True)
            save_state(state)
        except OSError as error:
            state["error"] = f"Countdown broadcast failed: {error}"
            save_state(state)
    if remaining > 0:
        return
    state["busy"] = True
    save_state(state)
    try:
        try:
            broadcast("Server is shutting down now. Please log in again later.")
            time.sleep(1)
        except OSError:
            pass
        result = subprocess.run(
            [str(SERVICE_SCRIPT), "stop-core"], capture_output=True, text=True,
            timeout=240, check=False,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode:
            raise RuntimeError(output or "pw155-service.sh stop-core failed")
        state["last_action"] = {
            "id": str(schedule.get("id", "")), "action": "safe-shutdown",
            "actor": str(schedule.get("actor", "admin")), "status": "completed",
            "finished_at": now_iso(),
            "message": (output[-1200:] or "Maps and core daemons stopped"),
        }
        state["error"] = None
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
        state["last_action"] = {
            "id": str(schedule.get("id", "")), "action": "safe-shutdown",
            "actor": str(schedule.get("actor", "admin")), "status": "failed",
            "finished_at": now_iso(), "message": str(error)[-1200:],
        }
        state["error"] = str(error)[-1200:]
    finally:
        state["scheduled"] = None
        state["busy"] = False
        save_state(state)


def run_forever():
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    state = load_json(CONTROL_DIR / "status.json", default_state())
    state["busy"] = False
    save_state(state)
    while True:
        process_queue(state)
        tick_shutdown(state)
        time.sleep(POLL_SECONDS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("initialize", "process-once", "run", "test-material"))
    parser.add_argument("--role-id", type=int)
    parser.add_argument("--item-id", type=int, default=TEST_MATERIAL_ID)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.mode == "test-material":
        if args.role_id is None:
            parser.error("test-material requires --role-id")
        if args.role_id != TEST_ROLE_ID:
            parser.error("test-material is limited to role ID 1024")
        if args.count != 1:
            parser.error("test-material is limited to --count 1")
        max_count = verified_test_material(args.item_id)
        if not args.apply:
            print(f"DRY RUN: role={args.role_id} material={args.item_id} "
                  f"count=1 max_count={max_count}; no mail sent")
        else:
            print(json.dumps(send_test_material_mail(
                args.role_id, args.item_id, args.count
            ), ensure_ascii=False))
    elif args.mode == "initialize":
        state = load_json(CONTROL_DIR / "status.json", default_state())
        state["busy"] = False
        save_state(state)
    elif args.mode == "process-once":
        state = load_json(CONTROL_DIR / "status.json", default_state())
        process_queue(state)
        tick_shutdown(state)
    else:
        run_forever()


if __name__ == "__main__":
    main()
