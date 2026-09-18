#!/usr/bin/env python3
"""Kolektor status lokal PW155; tidak membaca konfigurasi atau secret."""

import json
import os
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MONITOR_DIR = Path(os.environ.get("PW155_MONITOR_DIR", "/var/lib/pw155-monitor"))
PID_DIR = Path(os.environ.get("PW155_PID_DIR", "/srv/pw155/runtime/pids"))
MAX_EVENTS = 500
GAME_SERVICES = (
    ("logservice", "Log Service", 11101),
    ("authd", "Auth", 29200),
    ("uniquenamed", "Unique Name", 29401),
    ("gamedbd", "GameDB", 29400),
    ("gacd", "Anti-Cheat", 29702),
    ("gfactiond", "Faction", 29500),
    ("gdeliveryd", "Delivery", 29100),
    ("glinkd", "Login Gateway", 29000),
    ("gs01", "World Utama", None),
    ("is61", "Celestial Vale", None),
)
SYSTEMD_SERVICES = (
    ("pw155-web.service", "Web Portal"),
    ("pw155-game-control.service", "Rate, Item & Safe Shutdown"),
    ("pw155-character-sync.timer", "Sinkronisasi Karakter"),
    ("pw155-db-backup.timer", "Backup Database"),
    ("pw155-host-export.timer", "Export ke Host"),
)


def timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def pid_status(name):
    pidfile = PID_DIR / f"{name}.pid"
    try:
        pid = int(pidfile.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        return True, pid
    except (OSError, ValueError):
        return False, None


def port_status(port):
    if port is None:
        return True
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def unit_status(unit):
    result = subprocess.run(
        ["/bin/systemctl", "is-active", unit], capture_output=True,
        text=True, timeout=3, check=False,
    )
    state = result.stdout.strip() or "unknown"
    return state == "active", state


def collect_services():
    services = []
    for name, label, port in GAME_SERVICES:
        process_ok, pid = pid_status(name)
        network_ok = port_status(port)
        running = process_ok and network_ok
        services.append({
            "name": name, "label": label, "kind": "daemon", "running": running,
            "pid": pid, "port": port,
            "detail": "aktif" if running else (
                "proses tidak aktif" if not process_ok else f"port {port} tidak merespons"
            ),
        })
    for unit, label in SYSTEMD_SERVICES:
        running, state = unit_status(unit)
        services.append({
            "name": unit, "label": label, "kind": "systemd", "running": running,
            "pid": None, "port": None, "detail": state,
        })
    return services


def transition_events(previous, current, checked_at):
    old_states = {item["name"]: bool(item["running"])
                  for item in previous.get("services", [])}
    events = []
    for item in current:
        old = old_states.get(item["name"])
        new = bool(item["running"])
        if old is None or old != new:
            events.append({
                "timestamp": checked_at,
                "service": item["name"],
                "label": item["label"],
                "status": "online" if new else "offline",
                "detail": item["detail"],
            })
    return events


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return fallback


def atomic_json(path, value):
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(MONITOR_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temporary, 0o640)
        try:
            import grp
            os.chown(temporary, 0, grp.getgrnam("pwmonitor").gr_gid)
        except (ImportError, KeyError, PermissionError):
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def update_monitor():
    status_path = MONITOR_DIR / "status.json"
    events_path = MONITOR_DIR / "events.json"
    previous = load_json(status_path, {"services": []})
    events = load_json(events_path, [])
    checked_at = timestamp()
    services = collect_services()
    events.extend(transition_events(previous, services, checked_at))
    events = events[-MAX_EVENTS:]
    snapshot = {
        "checked_at": checked_at,
        "online": sum(1 for item in services if item["running"]),
        "total": len(services),
        "services": services,
    }
    atomic_json(status_path, snapshot)
    atomic_json(events_path, events)
    return snapshot, events


if __name__ == "__main__":
    state, _ = update_monitor()
    print(f"PW155 monitor: {state['online']}/{state['total']} layanan aktif")
