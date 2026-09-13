"""Lossless reader/writer for PW 1.5.5 Boutique gshop v5 files."""

from __future__ import annotations

import copy
import io
import struct
from dataclasses import dataclass, field
from pathlib import Path


VERSION = 5
SALE_COUNT = 4
CATEGORY_COUNT = 8


def _read_i32(stream: io.BufferedIOBase) -> int:
    data = stream.read(4)
    if len(data) != 4:
        raise ValueError("gshop.data terpotong")
    return struct.unpack("<i", data)[0]


def _write_i32(stream: io.BufferedIOBase, value: int) -> None:
    stream.write(struct.pack("<i", int(value)))


def _decode_fixed(data: bytes, encoding: str) -> str:
    if encoding == "utf-16le":
        end = len(data)
        for index in range(0, len(data) - 1, 2):
            if data[index:index + 2] == b"\0\0":
                end = index
                break
        return data[:end].decode(encoding, errors="replace")
    return data.split(b"\0", 1)[0].decode(encoding, errors="replace")


def _encode_fixed(value: str, size: int, encoding: str) -> bytes:
    encoded = value.encode(encoding)
    terminator = b"\0\0" if encoding == "utf-16le" else b"\0"
    if len(encoded) + len(terminator) > size:
        raise ValueError(f"Teks terlalu panjang untuk field {size} byte")
    return encoded + terminator + bytes(size - len(encoded) - len(terminator))


@dataclass
class SaleOption:
    price: int = 0
    selling_end_time: int = 0
    duration: int = 0
    selling_start_time: int = 0
    control: int = 0
    day: int = 0
    status: int = 0
    flags: int = 0
    vip_level: int = 0

    @classmethod
    def read(cls, stream: io.BufferedIOBase) -> "SaleOption":
        return cls(*(_read_i32(stream) for _ in range(9)))

    def write(self, stream: io.BufferedIOBase) -> None:
        for value in (
            self.price, self.selling_end_time, self.duration,
            self.selling_start_time, self.control, self.day,
            self.status, self.flags, self.vip_level,
        ):
            _write_i32(stream, value)


@dataclass
class ShopItem:
    place: int
    category: int
    subcategory: int
    item_id: int
    amount: int
    sales: list[SaleOption] = field(default_factory=list)
    gift_id: int = 0
    gift_amount: int = 0
    gift_duration: int = 0
    log_price: int = 0
    owner_npcs: list[int] = field(default_factory=lambda: [0] * 8)
    period_limit: int = 0
    available_frequency: int = 0
    icon: str = ""
    description: str = ""
    name: str = ""

    @classmethod
    def read(cls, stream: io.BufferedIOBase, client: bool) -> "ShopItem":
        place = _read_i32(stream)
        category = _read_i32(stream)
        subcategory = _read_i32(stream)
        icon = _decode_fixed(stream.read(128), "gbk") if client else ""
        item_id = _read_i32(stream)
        amount = _read_i32(stream)
        sales = [SaleOption.read(stream) for _ in range(SALE_COUNT)]
        description = _decode_fixed(stream.read(1024), "utf-16le") if client else ""
        name = _decode_fixed(stream.read(64), "utf-16le") if client else ""
        gift_id = _read_i32(stream)
        gift_amount = _read_i32(stream)
        gift_duration = _read_i32(stream)
        log_price = _read_i32(stream)
        owner_npcs = [_read_i32(stream) for _ in range(8)]
        period_limit = _read_i32(stream)
        available_frequency = _read_i32(stream)
        return cls(
            place, category, subcategory, item_id, amount, sales,
            gift_id, gift_amount, gift_duration, log_price, owner_npcs,
            period_limit, available_frequency, icon, description, name,
        )

    def write(self, stream: io.BufferedIOBase, client: bool) -> None:
        for value in (self.place, self.category, self.subcategory):
            _write_i32(stream, value)
        if client:
            stream.write(_encode_fixed(self.icon, 128, "gbk"))
        _write_i32(stream, self.item_id)
        _write_i32(stream, self.amount)
        if len(self.sales) != SALE_COUNT:
            raise ValueError("Setiap item Boutique harus memiliki empat opsi harga")
        for sale in self.sales:
            sale.write(stream)
        if client:
            stream.write(_encode_fixed(self.description, 1024, "utf-16le"))
            stream.write(_encode_fixed(self.name, 64, "utf-16le"))
        for value in (self.gift_id, self.gift_amount, self.gift_duration, self.log_price):
            _write_i32(stream, value)
        if len(self.owner_npcs) != 8:
            raise ValueError("owner_npcs harus tepat delapan entry")
        for value in self.owner_npcs:
            _write_i32(stream, value)
        _write_i32(stream, self.period_limit)
        _write_i32(stream, self.available_frequency)


@dataclass
class Category:
    name: str
    subcategories: list[str]

    @classmethod
    def read(cls, stream: io.BufferedIOBase) -> "Category":
        name = _decode_fixed(stream.read(128), "utf-16le")
        count = _read_i32(stream)
        if not 0 <= count <= 9:
            raise ValueError(f"Jumlah subkategori tidak valid: {count}")
        return cls(name, [_decode_fixed(stream.read(128), "utf-16le") for _ in range(count)])

    def write(self, stream: io.BufferedIOBase) -> None:
        if len(self.subcategories) > 9:
            raise ValueError("Maksimum sembilan subkategori")
        stream.write(_encode_fixed(self.name, 128, "utf-16le"))
        _write_i32(stream, len(self.subcategories))
        for value in self.subcategories:
            stream.write(_encode_fixed(value, 128, "utf-16le"))


@dataclass
class GShop:
    timestamp: int
    items: list[ShopItem]
    categories: list[Category] = field(default_factory=list)
    client: bool = True

    @classmethod
    def load(cls, path: Path | str, client: bool = True) -> "GShop":
        with Path(path).open("rb") as stream:
            timestamp = _read_i32(stream)
            count = _read_i32(stream)
            if not 0 <= count <= 100_000:
                raise ValueError(f"Jumlah item Boutique tidak valid: {count}")
            items = [ShopItem.read(stream, client) for _ in range(count)]
            categories = [Category.read(stream) for _ in range(CATEGORY_COUNT)] if client else []
            if stream.read(1):
                raise ValueError("Ada byte sisa; versi gshop bukan v5 atau file rusak")
        result = cls(timestamp, items, categories, client)
        result.validate()
        return result

    def validate(self) -> None:
        if self.client and len(self.categories) != CATEGORY_COUNT:
            raise ValueError("gshop client harus memiliki delapan kategori")
        for index, item in enumerate(self.items):
            if not 0 <= item.category < CATEGORY_COUNT:
                raise ValueError(f"Kategori item #{index} tidak valid")
            if item.item_id <= 0 or item.amount <= 0:
                raise ValueError(f"ID/jumlah item #{index} tidak valid")
            if self.client and item.subcategory >= len(self.categories[item.category].subcategories):
                raise ValueError(f"Subkategori item #{index} tidak tersedia")

    def to_bytes(self, client: bool | None = None) -> bytes:
        client = self.client if client is None else client
        self.validate()
        stream = io.BytesIO()
        _write_i32(stream, self.timestamp)
        _write_i32(stream, len(self.items))
        for item in self.items:
            item.write(stream, client)
        if client:
            for category in self.categories:
                category.write(stream)
        return stream.getvalue()

    def save(self, path: Path | str, client: bool | None = None) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.to_bytes(client))

    def clone_item(self, index: int, *, item_id: int, name: str, place: int | None = None) -> ShopItem:
        source = self.items[index]
        item = copy.deepcopy(source)
        item.item_id = int(item_id)
        item.name = name
        item.place = max((entry.place for entry in self.items), default=-1) + 1 if place is None else int(place)
        self.items.append(item)
        self.validate()
        return item

    def find(self, query: str, limit: int = 100) -> list[tuple[int, ShopItem]]:
        text = query.strip().casefold()
        matches = []
        for index, item in enumerate(self.items):
            if not text or text in str(item.item_id) or text in item.name.casefold():
                matches.append((index, item))
                if len(matches) >= limit:
                    break
        return matches
