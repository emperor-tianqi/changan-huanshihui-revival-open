"""Free-play protocol tolerance: localize a known-unimplemented request.

The live failure (2026-10-18, manual session 20261018-0126): after the client
sent C2S 647 `sendToGetLimitShopOpenTime` (limit shop) and C2S 653
`sendToEquipFightDataUpate` (equip fight), the stub read an id its implemented
registry did not know and closed the whole socket.  The client then tried
C2S 10 `reconnect`, which the same strict path also rejected.

HISTORY NOTE (TASK-0259): C2S 653 is now IMPLEMENTED -- registered in
``registry.py`` and handled by ``tcp_stub._handle_equip_fight_data_update`` as a
record-only request -- so it is no longer a valid "known but unimplemented"
sample.  The tolerant-path test below uses C2S 656 `compositeTreasureMap`
instead (zero format, no lock partner, still unregistered), keeping the same
assertions.  C2S 647's strict/tolerant cases are unchanged.

These tests pin the two-mode contract:

* strict (default) is unchanged: known-but-unimplemented still fails closed;
* free_play_tolerant decodes through the read-only client catalog, records the
  request, keeps the socket, and sends only the one evidence-backed safe no-op
  (C2S 647 -> S2C 1295 N=0); a truly unknown id still fails closed.  C2S 10
  reconnect is now a real handler (S2C 15 + stored-role rehydration), so its
  refusal path here is only the missing-identity case;
* no role, reward, currency or progress state is touched.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest

from revival_stub import app, play
from revival_stub.codec import NeedMoreData, ReaderState, WriterState, decode_packet, encode_packet
from revival_stub.eventlog import EventLogger
from revival_stub.protocol_catalog import catalog_spec
from revival_stub.protocol_policy import (
    PROTOCOL_POLICY_FREE_PLAY_TOLERANT,
    PROTOCOL_POLICY_STRICT,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY, ProtocolRegistry
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer

SERVER_STARTED_UNIX = 1_700_000_000


def _test_s2c_registry() -> ProtocolRegistry:
    registry = ProtocolRegistry("S2C-test")
    for spec in S2C_REGISTRY:
        registry.register(spec.protocol_id, spec.name, spec.fmt)
    try:
        registry.by_id(1295)
    except KeyError:
        # The free-play safe reply is a local spec, not a production registry
        # row; the test reader must still be able to decode it.
        registry.register(1295, "returnLimitShopOpenTimeUpdated", "N")
    return registry


class FreePlayProtocolPolicyTests(unittest.TestCase):
    def _start(self, policy: str):
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            time_source=lambda: SERVER_STARTED_UNIX,
            protocol_policy=policy,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, events

    def _open_and_login(self, server):
        writer = WriterState()
        reader = ReaderState()
        registry = _test_s2c_registry()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(C2S_REGISTRY.by_id(2), (b"FreePlay", 1), writer)
        stream = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=2)
        stream.settimeout(2)
        self.addCleanup(stream.close)
        stream.sendall(login.wire + create.wire)
        buffer = bytearray()
        responses = []
        while len(responses) < 5:
            try:
                packet = decode_packet(buffer, registry, reader)
            except NeedMoreData:
                chunk = stream.recv(4096)
                self.assertTrue(chunk)
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            responses.append(packet)
        return stream, writer, buffer, reader, registry

    def _send(self, stream, writer, spec, fields):
        packet = encode_packet(spec, fields, writer)
        stream.sendall(packet.wire)

    def _read_until(self, stream, buffer, reader, registry, protocol_id, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, registry, reader)
            except NeedMoreData:
                chunk = stream.recv(4096)
                if not chunk:
                    return None
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            if packet.spec.protocol_id == protocol_id:
                return packet
        return None

    def _wait_for(self, events, name, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in events:
                if event.get("event") == name:
                    return event
            time.sleep(0.01)
        return None

    def test_tolerant_known_notification_keeps_connection_and_heartbeat(self):
        server, events = self._start(PROTOCOL_POLICY_FREE_PLAY_TOLERANT)
        stream, writer, buffer, reader, registry = self._open_and_login(server)
        # C2S 656 `compositeTreasureMap` -- empty format, no lock partner, and
        # still unregistered (TASK-0259 replaced the historical 653 sample,
        # which is now implemented as a record-only request).
        self._send(stream, writer, catalog_spec(656), ())
        self._send(stream, writer, C2S_REGISTRY.by_id(25), ())
        self._send(stream, writer, C2S_REGISTRY.by_id(725), ())
        reply = self._read_until(stream, buffer, reader, registry, 1500)
        self.assertIsNotNone(reply, "the recovered month-sign reply must still arrive")
        self.assertEqual(reply.spec.name, "responseMonthSignData")

        event = self._wait_for(events, "tcp_free_play_unsupported_known")
        self.assertIsNotNone(event)
        self.assertEqual(event["protocol_id"], 656)
        self.assertEqual(event["protocol_name"], "compositeTreasureMap")
        self.assertEqual(event["protocol_fmt"], "")
        self.assertEqual(event["classification"], "known_unimplemented")
        self.assertEqual(event["fallback_action"], "no_response")
        self.assertFalse(event["response_sent"])
        self.assertFalse(event["state_changed"])
        self.assertTrue(event["connection_kept_alive"])
        self.assertEqual(event["lock_partner"], [])
        self.assertFalse(any(
            item.get("event") == "tcp_error" for item in events
        ))
        self.assertTrue(any(
            item.get("event") == "tcp_packet_ignored"
            and item.get("protocol_id") == 25
            for item in events
        ))

    def test_tolerant_lock_send_gets_only_the_proven_safe_reply(self):
        server, events = self._start(PROTOCOL_POLICY_FREE_PLAY_TOLERANT)
        stream, writer, buffer, reader, registry = self._open_and_login(server)
        self._send(stream, writer, catalog_spec(647), (1,))
        reply = self._read_until(stream, buffer, reader, registry, 1295)
        self.assertIsNotNone(reply, "the lock partner must unlock the client")
        self.assertEqual(reply.spec.name, "returnLimitShopOpenTimeUpdated")
        self.assertEqual(reply.fields, (0,))

        event = self._wait_for(events, "tcp_free_play_unsupported_known")
        self.assertIsNotNone(event)
        self.assertEqual(event["protocol_id"], 647)
        self.assertEqual(event["protocol_name"], "sendToGetLimitShopOpenTime")
        self.assertEqual(event["fallback_action"], "safe_noop_response")
        self.assertTrue(event["response_sent"])
        self.assertEqual(event["response_protocol_name"], "returnLimitShopOpenTimeUpdated")
        self.assertEqual(event["response_fields"], [0])
        self.assertFalse(event["state_changed"])
        self.assertTrue(event["connection_kept_alive"])
        self.assertEqual(
            event["lock_partner"], ["returnLimitShopOpenTimeUpdated"]
        )
        self.assertFalse(any(item.get("event") == "tcp_error" for item in events))

        # The same connection still serves recovered requests.
        self._send(stream, writer, C2S_REGISTRY.by_id(725), ())
        self.assertIsNotNone(self._read_until(stream, buffer, reader, registry, 1500))

    def test_strict_known_unimplemented_still_fails_closed(self):
        server, events = self._start(PROTOCOL_POLICY_STRICT)
        stream, writer, buffer, _reader, _registry = self._open_and_login(server)
        self._send(stream, writer, catalog_spec(647), (1,))
        event = self._wait_for(events, "tcp_error")
        self.assertIsNotNone(event)
        self.assertEqual(event["classification"], "malformed_packet")
        self.assertEqual(event["message"], "unknown protocol id 647")
        self.assertTrue(any(
            item.get("event") == "tcp_disconnected" for item in events
        ))
        self.assertFalse(any(
            item.get("event") == "tcp_free_play_unsupported_known"
            for item in events
        ))

    def test_tolerant_truly_unknown_still_fails_closed(self):
        server, events = self._start(PROTOCOL_POLICY_FREE_PLAY_TOLERANT)
        stream, writer, _buffer, _reader, _registry = self._open_and_login(server)
        # id 9999 is not in the client catalog: no trustworthy packet length.
        packet = encode_packet(_unknown_spec(), (), writer)
        stream.sendall(packet.wire)
        event = self._wait_for(events, "tcp_error")
        self.assertIsNotNone(event)
        self.assertEqual(event["classification"], "malformed_packet")
        self.assertEqual(event["message"], "unknown protocol id 9999")
        self.assertTrue(any(
            item.get("event") == "tcp_disconnected" for item in events
        ))

    def test_reconnect_without_identity_fails_closed(self):
        server, events = self._start(PROTOCOL_POLICY_FREE_PLAY_TOLERANT)
        stream, writer, _buffer, _reader, _registry = self._open_and_login(server)
        self._send(
            stream,
            writer,
            catalog_spec(10),
            (0.0, b"", 0, b"", b"0.1.20.1", b"", b"", b""),
        )
        event = self._wait_for(events, "tcp_error")
        self.assertIsNotNone(event)
        self.assertEqual(event["classification"], "reconnect_identity_missing")
        self.assertFalse(any(
            item.get("event") == "tcp_reconnect_accepted" for item in events
        ))

    def test_strict_is_the_default_and_play_opts_in(self):
        events: list[dict] = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
        )
        self.addCleanup(server.server_close)
        self.assertEqual(server.protocol_policy, PROTOCOL_POLICY_STRICT)
        self.assertIs(server.decode_c2s_registry, server.c2s_registry)
        self.assertEqual(
            app.build_parser().parse_args([]).protocol_policy,
            PROTOCOL_POLICY_STRICT,
        )
        self.assertEqual(
            play.build_parser().parse_args(["dev-main"]).protocol_policy,
            PROTOCOL_POLICY_FREE_PLAY_TOLERANT,
        )


def _unknown_spec():
    from revival_stub.registry import ProtocolSpec

    return ProtocolSpec(9999, "not_a_client_protocol", "")


if __name__ == "__main__":
    unittest.main()
