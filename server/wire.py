"""Minimal protobuf wire-format helpers that keep field order and repeated fields."""
import gzip


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def varint(value: int) -> bytes:
    value &= (1 << 64) - 1
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def parse(data: bytes) -> list[tuple[int, int, object]]:
    """Return [(field, wire_type, value)]; value is int for varint/fixed, bytes for length-delimited."""
    fields, pos = [], 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        num, wt = tag >> 3, tag & 7
        if wt == 0:
            val, pos = read_varint(data, pos)
        elif wt == 2:
            n, pos = read_varint(data, pos)
            val, pos = data[pos:pos + n], pos + n
        elif wt == 1:
            val, pos = int.from_bytes(data[pos:pos + 8], "little"), pos + 8
        elif wt == 5:
            val, pos = int.from_bytes(data[pos:pos + 4], "little"), pos + 4
        else:
            raise ValueError(f"wire type {wt}")
        fields.append((num, wt, val))
    return fields


def build(fields: list[tuple[int, int, object]]) -> bytes:
    out = bytearray()
    for num, wt, val in fields:
        out += varint((num << 3) | wt)
        if wt == 0:
            out += varint(val)
        elif wt == 2:
            out += varint(len(val)) + val
        elif wt == 1:
            out += val.to_bytes(8, "little")
        elif wt == 5:
            out += val.to_bytes(4, "little")
    return bytes(out)


def get(fields, num, default=None):
    return next((v for n, _, v in fields if n == num), default)


def replace(fields, num, value):
    return [(n, wt, value if n == num else v) for n, wt, v in fields]


def set_varint(fields, num, value):
    """Set a varint field, adding it if absent (proto3 omits zero values)."""
    if any(n == num for n, _, _ in fields):
        return replace(fields, num, value)
    return sorted(fields + [(num, 0, value)], key=lambda f: f[0])


def body_to_pb(body: bytes, flags: int) -> bytes:
    """Packet body (after decryption) -> protobuf bytes."""
    if flags & 0x02:
        body = gzip.decompress(body)
    return body[1:] if body[:1] == b"\x00" else body
