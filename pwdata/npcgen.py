"""Lossless reader/writer for PW npcgen.data spawn groups.

The implementation intentionally exposes only the NPC/monster existence section.
Resource, dynamic-object, and trigger sections remain byte-for-byte untouched.
"""

from __future__ import annotations

import dataclasses
import struct
from pathlib import Path


GROUP_BASE_SIZE = 59


@dataclasses.dataclass
class SpawnEntry:
    npc_id: int
    amount: int
    respawn: int
    dead_amount: int
    aggression: int
    water_offset: float
    terrain_offset: float
    group: int
    help_sender: int
    help_needer: int
    need_help: int
    faction: int
    faction_helper: int
    faction_accept: int
    path: int
    path_type: int
    speed: int
    dead_time: int
    refresh_lower: int = 0

    @classmethod
    def unpack(cls, data: bytes, offset: int, version: int):
        numeric = struct.unpack_from("<iiiii ff iii 4B iiii", data, offset)
        refresh = struct.unpack_from("<i", data, offset + 60)[0] if version >= 11 else 0
        return cls(*numeric, refresh), offset + (64 if version >= 11 else 60)

    def pack(self, version: int) -> bytes:
        values = (
            self.npc_id, self.amount, self.respawn, self.dead_amount,
            self.aggression, self.water_offset, self.terrain_offset,
            self.group, self.help_sender, self.help_needer, self.need_help,
            self.faction, self.faction_helper, self.faction_accept,
            self.path, self.path_type, self.speed, self.dead_time,
        )
        output = struct.pack("<iiiii ff iii 4B iiii", *values)
        if version >= 11:
            output += struct.pack("<i", self.refresh_lower)
        return output


@dataclasses.dataclass
class SpawnGroup:
    location: int
    x: float
    y: float
    z: float
    direction_x: float
    direction_y: float
    direction_z: float
    random_x: float
    random_y: float
    random_z: float
    spawn_type: int
    group_type: int
    init_gen: int
    auto_revive: int
    valid_once: int
    generator_id: int
    trigger_id: int
    lifetime: int
    max_respawn_time: int
    entries: list[SpawnEntry]

    @classmethod
    def unpack(cls, data: bytes, offset: int, version: int):
        location, count = struct.unpack_from("<ii", data, offset)
        numbers = struct.unpack_from("<9fii3Bi", data, offset + 8)
        cursor = offset + GROUP_BASE_SIZE
        trigger_id = lifetime = max_respawn = 0
        if version > 6:
            trigger_id, lifetime, max_respawn = struct.unpack_from("<iii", data, cursor)
            cursor += 12
        entries = []
        for _ in range(count):
            entry, cursor = SpawnEntry.unpack(data, cursor, version)
            entries.append(entry)
        return cls(location, *numbers, trigger_id, lifetime, max_respawn, entries), cursor

    def pack(self, version: int) -> bytes:
        output = bytearray(struct.pack("<ii", self.location, len(self.entries)))
        output.extend(struct.pack(
            "<9fii3Bi", self.x, self.y, self.z,
            self.direction_x, self.direction_y, self.direction_z,
            self.random_x, self.random_y, self.random_z,
            self.spawn_type, self.group_type, self.init_gen,
            self.auto_revive, self.valid_once, self.generator_id,
        ))
        if version > 6:
            output.extend(struct.pack("<iii", self.trigger_id, self.lifetime, self.max_respawn_time))
        for entry in self.entries:
            output.extend(entry.pack(version))
        return bytes(output)


class NpcGen:
    def __init__(self, version: int, resource_count: int, dynamic_count: int,
                 trigger_count: int, groups: list[SpawnGroup], tail: bytes):
        self.version = version
        self.resource_count = resource_count
        self.dynamic_count = dynamic_count
        self.trigger_count = trigger_count
        self.groups = groups
        self.tail = tail

    @classmethod
    def load(cls, path: Path) -> "NpcGen":
        data = Path(path).read_bytes()
        if len(data) < 16:
            raise ValueError("npcgen.data terlalu pendek")
        version, group_count, resources, dynamics = struct.unpack_from("<iiii", data, 0)
        if version < 1 or version > 64 or group_count < 0 or group_count > 2_000_000:
            raise ValueError("Header npcgen.data tidak valid")
        cursor = 16
        triggers = 0
        if version > 6:
            triggers = struct.unpack_from("<i", data, cursor)[0]
            cursor += 4
        groups = []
        for _ in range(group_count):
            group, cursor = SpawnGroup.unpack(data, cursor, version)
            groups.append(group)
        return cls(version, resources, dynamics, triggers, groups, data[cursor:])

    def to_bytes(self) -> bytes:
        output = bytearray(struct.pack(
            "<iiii", self.version, len(self.groups), self.resource_count, self.dynamic_count
        ))
        if self.version > 6:
            output.extend(struct.pack("<i", self.trigger_count))
        for group in self.groups:
            output.extend(group.pack(self.version))
        output.extend(self.tail)
        return bytes(output)

    def save(self, path: Path) -> None:
        Path(path).write_bytes(self.to_bytes())

    def find(self, query: str, names: dict[int, str] | None = None, limit: int = 100):
        names = names or {}
        needle = query.strip().casefold()
        matches = []
        for group_index, group in enumerate(self.groups):
            for entry_index, entry in enumerate(group.entries):
                name = names.get(entry.npc_id, f"NPC {entry.npc_id}")
                if needle and needle not in name.casefold() and needle != str(entry.npc_id):
                    continue
                matches.append((group_index, entry_index, group, entry, name))
                if len(matches) >= limit:
                    return matches
        return matches

    def clone_group(self, group_index: int, npc_id: int, x: float, y: float, z: float,
                    trigger_id: int = 0) -> int:
        if not 0 <= group_index < len(self.groups):
            raise ValueError("Index spawn group tidak valid")
        source = self.groups[group_index]
        if not source.entries:
            raise ValueError("Spawn group sumber kosong")
        entry = dataclasses.replace(source.entries[0], npc_id=npc_id)
        group = dataclasses.replace(
            source, x=x, y=y, z=z, trigger_id=trigger_id,
            generator_id=max((item.generator_id for item in self.groups), default=0) + 1,
            entries=[entry],
        )
        self.groups.append(group)
        return len(self.groups) - 1
