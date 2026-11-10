"""Deterministic local simulated client used by integration tests and smoke runs."""

from __future__ import annotations

import argparse
import socket
from dataclasses import dataclass
from typing import Any, Sequence

from .codec import NeedMoreData, ReaderState, WriterState, decode_packet, encode_packet
from .registry import C2S_REGISTRY, S2C_REGISTRY


DEFAULT_LOGIN_FIELDS: tuple[Any, ...] = (
    1.0,
    b"local-user",
    b"local-token-md5",
    1700000000.0,
    b"0.1.20.1",
    b"local-channel",
    b"0",
    b"android",
    b"local-device",
    b"local-player",
    0.0,
)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    login_wire: bytes
    response_wire: bytes
    response_protocol_id: int
    response_protocol_name: str
    response_fields: tuple[Any, ...]


def run_simulated_login(
    host: str,
    port: int,
    fields: Sequence[Any] = DEFAULT_LOGIN_FIELDS,
    *,
    timeout: float = 3.0,
) -> SimulationResult:
    writer = WriterState()
    reader = ReaderState()
    login = encode_packet(C2S_REGISTRY.by_name("loginByToken"), fields, writer)
    with socket.create_connection((host, port), timeout=timeout) as stream:
        stream.settimeout(timeout)
        stream.sendall(login.wire)
        buffer = bytearray()
        while True:
            try:
                response = decode_packet(buffer, S2C_REGISTRY, reader)
                break
            except NeedMoreData:
                chunk = stream.recv(65536)
                if not chunk:
                    raise ConnectionError("server closed before a complete response")
                buffer.extend(chunk)
    return SimulationResult(
        login_wire=login.wire,
        response_wire=response.wire,
        response_protocol_id=response.spec.protocol_id,
        response_protocol_name=response.spec.name,
        response_fields=response.fields,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one local simulated loginByToken")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=16001)
    args = parser.parse_args()
    result = run_simulated_login(args.host, args.port)
    print(f"login header: {result.login_wire[:3].hex(' ')}")
    print(f"response: {result.response_protocol_name}({result.response_protocol_id})")
    print(f"response wire: {result.response_wire.hex(' ')}")


if __name__ == "__main__":
    main()


