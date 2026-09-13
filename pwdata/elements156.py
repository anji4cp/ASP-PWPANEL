"""Lossless PW elements.data v156 reader used by the local equipment editor."""

from __future__ import annotations

import copy
import dataclasses
import struct
from pathlib import Path


@dataclasses.dataclass
class ElementList:
    name: str
    offset: bytes
    fields: list[str]
    types: list[str]
    rows: list[list[object]]

    def field_index(self, name: str) -> int:
        try:
            return self.fields.index(name)
        except ValueError as error:
            raise KeyError(f"Field {name} tidak ada dalam {self.name}") from error


def _read_config(path: Path):
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    count, conversation = int(lines[0]), int(lines[1])
    cursor, definitions = 2, []
    for _ in range(count):
        while cursor < len(lines) and not lines[cursor]:
            cursor += 1
        name, offset = lines[cursor], lines[cursor + 1]
        fields, types = lines[cursor + 2].split(";"), lines[cursor + 3].split(";")
        if len(fields) != len(types):
            raise ValueError(f"Konfigurasi field/type tidak cocok: {name}")
        definitions.append((name, offset, fields, types))
        cursor += 4
    return conversation, definitions


def _size(type_name: str) -> int:
    sizes = {"int16": 2, "int32": 4, "int64": 8, "float": 4, "double": 8}
    return sizes[type_name] if type_name in sizes else int(type_name.split(":", 1)[1])


def _read_value(data: bytes, cursor: int, type_name: str):
    formats = {"int16": "<h", "int32": "<i", "int64": "<q", "float": "<f", "double": "<d"}
    size = _size(type_name)
    raw = data[cursor:cursor + size]
    if len(raw) != size:
        raise ValueError(f"elements.data terpotong pada 0x{cursor:X}")
    value = struct.unpack(formats[type_name], raw)[0] if type_name in formats else raw
    return value, cursor + size


def _dynamic_offset(data: bytes, cursor: int, list_index: int):
    start = cursor
    if list_index in (0, 100):
        cursor += 8 + struct.unpack_from("<i", data, cursor + 4)[0]
    elif list_index == 20:
        cursor += 12 + struct.unpack_from("<i", data, cursor + 4)[0]
    return data[start:cursor], cursor


class Elements156:
    def __init__(self, version, signature, conversation, lists):
        self.version, self.signature = version, signature
        self.conversation, self.lists = conversation, lists

    @classmethod
    def load(cls, data_path: Path, config_path: Path):
        data = Path(data_path).read_bytes()
        version, signature = struct.unpack_from("<hh", data, 0)
        if version != 156:
            raise ValueError(f"elements.data harus v156, ditemukan v{version}")
        conversation, definitions = _read_config(config_path)
        cursor, lists = 4, []
        for index, (name, offset_spec, fields, types) in enumerate(definitions):
            if offset_spec == "AUTO":
                offset, cursor = _dynamic_offset(data, cursor, index)
            else:
                length = int(offset_spec)
                offset, cursor = data[cursor:cursor + length], cursor + length
            if index == conversation:
                marker = data.find("facedata\\".encode("gbk"), cursor)
                if marker < 0:
                    raise ValueError("Batas conversation elements.data tidak ditemukan")
                length = marker - cursor - 72
                types = list(types)
                types[0] = f"byte:{length}"
                value, cursor = _read_value(data, cursor, types[0])
                rows = [[value]]
            else:
                row_count = struct.unpack_from("<i", data, cursor)[0]
                cursor += 4
                if not 0 <= row_count <= 10_000_000:
                    raise ValueError(f"Jumlah record tidak valid pada {name}")
                rows = []
                for _ in range(row_count):
                    row = []
                    for type_name in types:
                        value, cursor = _read_value(data, cursor, type_name)
                        row.append(value)
                    rows.append(row)
            lists.append(ElementList(name, offset, list(fields), list(types), rows))
        if cursor != len(data):
            raise ValueError("Parser elements.data tidak mencapai akhir file")
        return cls(version, signature, conversation, lists)

    def to_bytes(self) -> bytes:
        formats = {"int16": "<h", "int32": "<i", "int64": "<q", "float": "<f", "double": "<d"}
        output = bytearray(struct.pack("<hh", self.version, self.signature))
        for index, item_list in enumerate(self.lists):
            output.extend(item_list.offset)
            if index != self.conversation:
                output.extend(struct.pack("<i", len(item_list.rows)))
            for row in item_list.rows:
                for value, type_name in zip(row, item_list.types):
                    if type_name in formats:
                        output.extend(struct.pack(formats[type_name], value))
                    else:
                        if not isinstance(value, bytes) or len(value) != _size(type_name):
                            raise ValueError(f"Ukuran field {item_list.name}/{type_name} berubah")
                        output.extend(value)
        return bytes(output)

    def save(self, path: Path):
        Path(path).write_bytes(self.to_bytes())

    def list_named(self, suffix: str) -> ElementList:
        matches = [item for item in self.lists if item.name.endswith(suffix)]
        if len(matches) != 1:
            raise KeyError(f"List {suffix} tidak unik")
        return matches[0]

    @staticmethod
    def text(value: object, type_name: str) -> str:
        if not isinstance(value, bytes):
            return str(value)
        encoding = "utf-16le" if type_name.startswith("wstring:") else "gbk"
        return value.decode(encoding, errors="replace").split("\0", 1)[0]

    @staticmethod
    def encoded_text(text: str, type_name: str) -> bytes:
        size = _size(type_name)
        encoding = "utf-16le" if type_name.startswith("wstring:") else "gbk"
        raw = text.encode(encoding)
        terminator = b"\0\0" if encoding == "utf-16le" else b"\0"
        if len(raw) + len(terminator) > size:
            raise ValueError(f"Teks terlalu panjang; maksimum field {size} byte")
        return raw + terminator + bytes(size - len(raw) - len(terminator))

    def clone_row(self, suffix: str, row_index: int):
        item_list = self.list_named(suffix)
        if not 0 <= row_index < len(item_list.rows):
            raise ValueError("Index equipment tidak valid")
        item_list.rows.append(copy.deepcopy(item_list.rows[row_index]))
        return len(item_list.rows) - 1, item_list.rows[-1]
