"""Protocol smoke test: login -> createRole -> relogin (persistence).

Run against a throwaway profile:

    python -m revival_stub --role-mode play_profile --play-profile smoke \
        --profile-mode init --profile-root <temp> --http-port 47083 --tcp-port 47002
    python scripts/protocol_smoke.py --port 47002

It never touches a real save: point it only at a disposable profile.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from revival_stub.codec import NeedMoreData, ReaderState, WriterState, decode_packet, encode_packet
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS


def _send(stream, writer, name, fields):
    packet = encode_packet(C2S_REGISTRY.by_name(name), fields, writer)
    stream.sendall(packet.wire)


def _recv(stream, reader, buffer):
    while True:
        try:
            return decode_packet(buffer, S2C_REGISTRY, reader)
        except NeedMoreData:
            chunk = stream.recv(65536)
            if not chunk:
                raise ConnectionError("server closed")
            buffer.extend(chunk)


def _login(port, timeout=5.0):
    writer = WriterState()
    reader = ReaderState()
    buffer = bytearray()
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as stream:
        stream.settimeout(timeout)
        _send(stream, writer, "loginByToken", DEFAULT_LOGIN_FIELDS)
        first = _recv(stream, reader, buffer)
        print("login ->", first.spec.name, first.fields[:3])
        if first.spec.name == "noRole":
            _send(stream, writer, "createRole", ("SmokeTester", 1))
            second = _recv(stream, reader, buffer)
            print("createRole ->", second.spec.name, second.fields[:3])
            return first, second
        return first, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    first, second = _login(args.port)
    if first.spec.name != "noRole":
        print("SKIP: profile already has a role; use a fresh profile for this test")
        return
    if second is None:
        raise SystemExit("FAIL: createRole produced no response")

    # Second connection: the role must now exist (persistence).
    writer = WriterState()
    reader = ReaderState()
    buffer = bytearray()
    with socket.create_connection(("127.0.0.1", args.port), timeout=5.0) as stream:
        stream.settimeout(5.0)
        _send(stream, writer, "loginByToken", DEFAULT_LOGIN_FIELDS)
        again = _recv(stream, reader, buffer)
        print("relogin ->", again.spec.name, again.fields[:3])
        if again.spec.name == "noRole":
            raise SystemExit("FAIL: role did not persist")
    print("OK: login, createRole and persistence all responded")


if __name__ == "__main__":
    main()
