#!/usr/bin/env python3
"""Allowlisted in-game broadcast and cancellable safe-shutdown worker."""

import argparse
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
WORLD_CHAT_OPCODE = int(os.environ.get("PW155_WORLD_CHAT_OPCODE", "120"))
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


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} hour(s)"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} minute(s)"
    return f"{seconds} second(s)"


def validate_request(request):
    action = request.get("action")
    if action not in ("broadcast", "schedule-shutdown", "cancel-shutdown"):
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
    else:
        schedule = state.get("scheduled")
        if not schedule:
            raise ValueError("No safe shutdown is scheduled")
        state["scheduled"] = None
        broadcast("The scheduled shutdown has been cancelled.")
        state["last_action"] = action_record(request, "completed", "Shutdown cancelled")
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
    parser.add_argument("mode", choices=("initialize", "process-once", "run"))
    args = parser.parse_args()
    if args.mode == "initialize":
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
