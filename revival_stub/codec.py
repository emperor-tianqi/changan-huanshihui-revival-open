"""Stateful codec for the recovered raw-TCP protocol.

The reader and writer deliberately own separate key/sequence states. Packet
boundaries are derived from the registered protocol format; there is no length
field in the wire header.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Sequence

from .registry import ProtocolRegistry, ProtocolSpec


INITIAL_KEY = 0xFD987193
DEFAULT_MAX_PACKET_SIZE = 1024 * 1024


class CodecError(ValueError):
    """Base class for deterministic wire errors."""


class NeedMoreData(CodecError):
    def __init__(self, required: int, available: int) -> None:
        super().__init__(f"truncated packet: need {required} bytes, have {available}")
        self.required = required
        self.available = available


class SequenceMismatch(CodecError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"sequence mismatch: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


class UnknownProtocol(CodecError):
    def __init__(
        self,
        protocol_id: int,
        *,
        raw_header: bytes = b"",
        raw_buffer: bytes = b"",
    ) -> None:
        super().__init__(f"unknown protocol id {protocol_id}")
        self.protocol_id = protocol_id
        # Without a registered format there is no trustworthy packet boundary.
        # Retain the header and the currently buffered bytes for diagnostics.
        self.raw_header = bytes(raw_header)
        self.raw_buffer = bytes(raw_buffer)


class MalformedPacket(CodecError):
    pass


@dataclass(slots=True)
class WireState:
    key: int = INITIAL_KEY
    sequence: int = 0

    def advance(self, protocol_id: int) -> tuple[int, int]:
        key_before = self.key
        sequence_before = self.sequence
        self.key = (
            self.key * (self.sequence + 1) + protocol_id
        ) & 0xFFFFFFFF
        self.sequence = (self.sequence + 1) & 0xFF
        return key_before, sequence_before


class ReaderState(WireState):
    """Incoming stream state."""


class WriterState(WireState):
    """Outgoing stream state."""


@dataclass(frozen=True, slots=True)
class EncodedPacket:
    spec: ProtocolSpec
    fields: tuple[Any, ...]
    sequence: int
    key_before: int
    key_after: int
    wire: bytes


@dataclass(frozen=True, slots=True)
class DecodedPacket:
    spec: ProtocolSpec
    fields: tuple[Any, ...]
    sequence: int
    key_before: int
    key_after: int
    consumed: int
    wire: bytes


def _require(data: bytes | bytearray | memoryview, required: int) -> None:
    if len(data) < required:
        raise NeedMoreData(required, len(data))


def _xor_string_bytes(data: bytes, key: int) -> bytes:
    key_bytes = struct.pack("<I", key)
    return bytes(byte ^ key_bytes[index & 3] for index, byte in enumerate(data))


def _encode_scalar(fmt: str, value: Any, key: int) -> bytes:
    if fmt == "T":
        raw = struct.pack("<B", int(value))
        return bytes((raw[0] ^ (key & 0xFF),))
    if fmt == "t":
        raw = struct.pack("<b", int(value))
        return bytes((raw[0] ^ (key & 0xFF),))
    if fmt == "W":
        raw = struct.pack("<H", int(value))
        number = struct.unpack("<H", raw)[0] ^ (key & 0xFFFF)
        return struct.pack("<H", number)
    if fmt == "w":
        raw = struct.pack("<h", int(value))
        number = struct.unpack("<H", raw)[0] ^ (key & 0xFFFF)
        return struct.pack("<H", number)
    if fmt == "N":
        raw = struct.pack("<I", int(value))
        number = struct.unpack("<I", raw)[0] ^ key
        return struct.pack("<I", number)
    if fmt == "n":
        raw = struct.pack("<i", int(value))
        number = struct.unpack("<I", raw)[0] ^ key
        return struct.pack("<I", number)
    if fmt == "p":
        raw = bytearray(struct.pack("<d", float(value)))
        low_word = struct.unpack_from("<I", raw, 0)[0] ^ key
        struct.pack_into("<I", raw, 0, low_word)
        return bytes(raw)
    if fmt == "y":
        raw = 1 if bool(value) else 0
        return bytes((raw ^ (key & 0xFF),))
    if fmt == "a":
        payload = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(payload) > 0xFFFF:
            raise MalformedPacket(f"string too long: {len(payload)} bytes")
        encoded_length = len(payload) ^ (key & 0xFFFF)
        return struct.pack("<H", encoded_length) + _xor_string_bytes(payload, key)
    raise AssertionError(f"unhandled format {fmt!r}")


def encode_packet(
    spec: ProtocolSpec, fields: Sequence[Any], state: WriterState
) -> EncodedPacket:
    if len(fields) != len(spec.fmt):
        raise MalformedPacket(
            f"{spec.name} expects {len(spec.fmt)} fields, got {len(fields)}"
        )
    key_before = state.key
    sequence = state.sequence
    wire = bytearray()
    wire.extend(struct.pack("<H", spec.protocol_id ^ (key_before & 0xFFFF)))
    wire.append(sequence ^ (key_before & 0xFF))
    for fmt, value in zip(spec.fmt, fields, strict=True):
        try:
            wire.extend(_encode_scalar(fmt, value, key_before))
        except (OverflowError, ValueError, TypeError, struct.error) as exc:
            raise MalformedPacket(
                f"cannot encode {spec.name} field {fmt!r}: {exc}"
            ) from exc
    state.advance(spec.protocol_id)
    return EncodedPacket(
        spec=spec,
        fields=tuple(fields),
        sequence=sequence,
        key_before=key_before,
        key_after=state.key,
        wire=bytes(wire),
    )


def _decode_scalar(
    fmt: str,
    data: bytes | bytearray | memoryview,
    offset: int,
    key: int,
    max_packet_size: int,
) -> tuple[Any, int]:
    sizes = {"T": 1, "t": 1, "W": 2, "w": 2, "N": 4, "n": 4, "p": 8, "y": 1}
    if fmt in sizes:
        end = offset + sizes[fmt]
        _require(data, end)
        raw = bytes(data[offset:end])
        if fmt in ("T", "t", "y"):
            value = raw[0] ^ (key & 0xFF)
            if fmt == "t" and value >= 0x80:
                value -= 0x100
            elif fmt == "y":
                value = bool(value)
        elif fmt in ("W", "w"):
            value = struct.unpack("<H", raw)[0] ^ (key & 0xFFFF)
            if fmt == "w" and value >= 0x8000:
                value -= 0x10000
        elif fmt in ("N", "n"):
            value = struct.unpack("<I", raw)[0] ^ key
            if fmt == "n" and value >= 0x80000000:
                value -= 0x100000000
        else:
            decoded = bytearray(raw)
            low_word = struct.unpack_from("<I", decoded, 0)[0] ^ key
            struct.pack_into("<I", decoded, 0, low_word)
            value = struct.unpack("<d", decoded)[0]
        return value, end

    if fmt == "a":
        _require(data, offset + 2)
        encoded_length = struct.unpack_from("<H", data, offset)[0]
        length = encoded_length ^ (key & 0xFFFF)
        end = offset + 2 + length
        if end > max_packet_size:
            raise MalformedPacket(
                f"schema-derived packet length {end} exceeds cap {max_packet_size}"
            )
        _require(data, end)
        payload = _xor_string_bytes(bytes(data[offset + 2 : end]), key)
        return payload, end

    raise AssertionError(f"unhandled format {fmt!r}")


def decode_packet(
    data: bytes | bytearray | memoryview,
    registry: ProtocolRegistry,
    state: ReaderState,
    *,
    max_packet_size: int = DEFAULT_MAX_PACKET_SIZE,
) -> DecodedPacket:
    _require(data, 3)
    key_before = state.key
    protocol_id = struct.unpack_from("<H", data, 0)[0] ^ (key_before & 0xFFFF)
    actual_sequence = data[2] ^ (key_before & 0xFF)
    if actual_sequence != state.sequence:
        raise SequenceMismatch(state.sequence, actual_sequence)
    try:
        spec = registry.by_id(protocol_id)
    except KeyError as exc:
        raise UnknownProtocol(
            protocol_id,
            raw_header=bytes(data[:3]),
            raw_buffer=bytes(data[:max_packet_size]),
        ) from exc

    offset = 3
    fields: list[Any] = []
    for fmt in spec.fmt:
        value, offset = _decode_scalar(fmt, data, offset, key_before, max_packet_size)
        fields.append(value)
    if offset > max_packet_size:
        raise MalformedPacket(
            f"schema-derived packet length {offset} exceeds cap {max_packet_size}"
        )

    packet_wire = bytes(data[:offset])
    sequence = state.sequence
    state.advance(protocol_id)
    return DecodedPacket(
        spec=spec,
        fields=tuple(fields),
        sequence=sequence,
        key_before=key_before,
        key_after=state.key,
        consumed=offset,
        wire=packet_wire,
    )

