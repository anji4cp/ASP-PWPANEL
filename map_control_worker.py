#!/usr/bin/env python3
"""Root-side, allowlisted map controller for the local PW155 admin panel."""

import argparse
import configparser
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


CONTROL_DIR = Path(os.environ.get("PW155_MAP_CONTROL_DIR", "/var/lib/pw155-map-control"))
REQUEST_DIR = CONTROL_DIR / "requests"
PID_DIR = Path(os.environ.get("PW155_PID_DIR", "/srv/pw155/runtime/pids"))
GS_CONFIG = Path(os.environ.get(
    "PW155_GS_CONFIG", "/srv/pw155/staging/pw155/gamed/gs.conf"
))
SERVICE_SCRIPT = Path(os.environ.get(
    "PW155_SERVICE_SCRIPT", "/srv/pw155/tools/pw155-service.sh"
))
RECOMMENDED = {"gs01", "is61", "is62", "is69", "is72"}
KNOWN_LABELS = {
    "gs01": "World Utama",
    "is61": "Celestial Vale",
}
MAX_MAPS = 6
CORE_SERVICES = (
    ("logservice", "Log Service"),
    ("authd", "Auth"),
    ("uniquenamed", "Unique Name"),
    ("gamedbd", "GameDB"),
    ("gacd", "Anti-Cheat"),
    ("gfactiond", "Faction"),
    ("gdeliveryd", "Delivery"),
    ("glinkd", "Login Gateway"),
)


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_json(path, value):
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(CONTROL_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temporary, 0o640)
        try:
            import grp
            os.chown(temporary, 0, grp.getgrnam("pwmap").gr_gid)
        except (ImportError, KeyError, PermissionError):
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def discover_maps():
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    with GS_CONFIG.open("r", encoding="utf-8", errors="replace") as handle:
        parser.read_file(handle)
    if not parser.has_section("General"):
        raise RuntimeError("Bagian General tidak ditemukan di gs.conf")
    kinds = {}
    for key, kind in (("world_servers", "world"), ("instance_servers", "instance")):
        for alias in parser.get("General", key, fallback="").split(";"):
            alias = alias.strip()
            if alias:
                kinds[alias] = kind
    result = []
    for alias in sorted(kinds, key=lambda value: (value != "gs01", value)):
        section = next((name for name in (f"World_{alias}", f"Instance_{alias}")
                        if parser.has_section(name)), None)
        if not section:
            continue
        base_path = parser.get(section, "base_path", fallback="").strip().rstrip("/")
        tag = parser.get(section, "tag", fallback="").strip()
        result.append({
            "alias": alias,
            "label": KNOWN_LABELS.get(alias, f"Map {alias}"),
            "kind": kinds[alias],
            "tag": int(tag) if tag.isdigit() else None,
            "config": base_path or "-",
            "recommended": alias in RECOMMENDED,
        })
    if not result:
        raise RuntimeError("Tidak ada map valid yang ditemukan di gs.conf")
    return result


def pid_running(alias):
    try:
        pid = int((PID_DIR / f"{alias}.pid").read_text(encoding="ascii").strip())
        return Path(f"/proc/{pid}").exists(), pid
    except (OSError, ValueError):
        return False, None


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return fallback


def refresh(extra=None):
    catalog = discover_maps()
    previous = load_json(CONTROL_DIR / "status.json", {})
    active = []
    states = {}
    for item in catalog:
        running, pid = pid_running(item["alias"])
        states[item["alias"]] = {"running": running, "pid": pid}
        if running:
            active.append(item["alias"])
    core = {}
    core_active = []
    for name, label in CORE_SERVICES:
        running, pid = pid_running(name)
        core[name] = {"label": label, "running": running, "pid": pid}
        if running:
            core_active.append(name)
    status = {
        "checked_at": now(),
        "busy": bool(extra.get("busy")) if extra and "busy" in extra else bool(previous.get("busy", False)),
        "active": active,
        "maps": states,
        "core": core,
        "core_active": core_active,
        "core_online": len(core_active) == len(CORE_SERVICES),
        "core_total": len(CORE_SERVICES),
        "last_action": previous.get("last_action"),
    }
    if extra:
        status.update(extra)
    atomic_json(CONTROL_DIR / "catalog.json", {"maps": catalog, "max_selection": MAX_MAPS})
    atomic_json(CONTROL_DIR / "status.json", status)
    return status


def validate_request(request, allowed):
    action = request.get("action")
    if action not in ("start", "stop", "start-core", "stop-core"):
        raise ValueError("Aksi server tidak valid")
    maps = request.get("maps")
    if not isinstance(maps, list):
        raise ValueError("Daftar map tidak valid")
    if action in ("start-core", "stop-core"):
        if maps:
            raise ValueError("Aksi daemon inti tidak menerima pilihan map")
        return action, maps
    if not 1 <= len(maps) <= MAX_MAPS:
        raise ValueError(f"Pilih 1 sampai {MAX_MAPS} map")
    if len(set(maps)) != len(maps) or any(item not in allowed for item in maps):
        raise ValueError("Daftar map tidak valid atau berisi duplikasi")
    return action, maps


def process_request(path):
    catalog = discover_maps()
    allowed = {item["alias"] for item in catalog}
    request = load_json(path, None)
    if not isinstance(request, dict):
        raise ValueError("Berkas permintaan bukan JSON object")
    action, maps = validate_request(request, allowed)
    actor = str(request.get("actor", "admin"))[:20]
    action_state = {
        "id": str(request.get("id", path.stem))[:64],
        "action": action,
        "maps": maps,
        "actor": actor,
        "requested_at": str(request.get("requested_at", "-"))[:40],
        "started_at": now(),
        "status": "running",
    }
    refresh({"busy": True, "last_action": action_state})
    command = [str(SERVICE_SCRIPT)]
    if action == "start":
        command.extend(["start-selected", *maps])
        timeout = 75 + (50 * len(maps))
    elif action == "start-core":
        command.append("start-core")
        timeout = 180
    elif action == "stop-core":
        command.append("stop-core")
        timeout = 180
    else:
        command.extend(["stop-map", maps[0]])
        timeout = 30
    try:
        if action == "stop" and len(maps) > 1:
            outputs = []
            for alias in maps:
                result = subprocess.run(
                    [str(SERVICE_SCRIPT), "stop-map", alias], capture_output=True,
                    text=True, timeout=30, check=False,
                )
                outputs.append(result.stdout + result.stderr)
                if result.returncode:
                    raise RuntimeError(outputs[-1].strip() or f"Gagal menghentikan {alias}")
            output = "\n".join(outputs)
        else:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=timeout, check=False)
            output = result.stdout + result.stderr
            if result.returncode:
                raise RuntimeError(output.strip() or "Perintah map gagal")
        action_state.update({"status": "completed", "finished_at": now(),
                             "message": (output.strip()[-1200:] or "Aksi selesai")})
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
        action_state.update({"status": "failed", "finished_at": now(),
                             "message": str(error)[-1200:]})
    refresh({"busy": False, "last_action": action_state})


def process_queue():
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(REQUEST_DIR.glob("*.json")):
        try:
            process_request(path)
        except Exception as error:
            refresh({"busy": False, "last_action": {
                "id": path.stem, "status": "failed", "finished_at": now(),
                "message": str(error)[-1200:],
            }})
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("initialize", "refresh", "process"))
    args = parser.parse_args()
    if args.mode in ("initialize", "refresh"):
        refresh()
    else:
        process_queue()


if __name__ == "__main__":
    main()
