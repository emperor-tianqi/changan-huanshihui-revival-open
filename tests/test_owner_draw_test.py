"""Owner draw-test overlay: ticket grant, deterministic fixture, persistence.

D-147 authorises the mechanical draw chain but still pauses probability/RNG.
These tests pin exactly that boundary:

* the explicit grant lands on S2C 27 wire positions 19/37 for
  qa-changan-main only, and is absent without the flag;
* C2S 274/33 are answered with the client's own Start/Result/End family and a
  labelled deterministic card from the frozen 妖灵 test pool, never with a
  random result;
* the fixture card is persisted in ``card_instances`` and replayed at the next
  login as S2C 29/30/31;
* with the overlay off the stub answers the client's FAILURE result so the 15 s
  lock is released and no connection is killed.
"""

from __future__ import annotations

import random
import socket
import threading
import time
import unittest

from revival_stub.codec import NeedMoreData, ReaderState, WriterState, decode_packet, encode_packet
from revival_stub.eventlog import EventLogger
from revival_stub.owner_draw_test import (
    DETERMINISTIC_FIXTURE_KIND,
    DETERMINISTIC_FIXTURE_ORIGIN,
    FIXTURE_CARD_CFG_ID,
    LOTTERY_TICKET_ADVANCED_VALUE_INDEX,
    LOTTERY_TICKET_VALUE_INDEX,
    OWNER_DRAW_TEST_PROFILE_ID,
    OwnerDrawTestGrant,
    parse_test_grant,
)
from revival_stub.owner_draw_pool import (
    POOL_CARDS,
    POOL_DIGEST,
    POOL_SOURCE,
    pool_card_at,
    pool_index_at,
)
from revival_stub.draw_odds import TIER_CARD_IDS
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import RoleStore
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer

SERVER_STARTED_UNIX = 1_700_000_000


class GrantParsingTests(unittest.TestCase):
    def test_parse_forms(self) -> None:
        self.assertIsNone(parse_test_grant(None))
        self.assertEqual(parse_test_grant("owner-draw"), OwnerDrawTestGrant())
        self.assertEqual(
            parse_test_grant("owner-draw:12/34"),
            OwnerDrawTestGrant(lottery_ticket=12, lottery_ticket_advanced=34),
        )
        with self.assertRaises(ValueError):
            parse_test_grant("owner-draw:12")
        with self.assertRaises(ValueError):
            parse_test_grant("other")


class DrawPoolTests(unittest.TestCase):
    def test_pool_is_frozen_client_data(self) -> None:
        self.assertEqual(len(POOL_CARDS), 130)
        self.assertEqual(
            POOL_DIGEST,
            "44bf41f069e6773b15616d0f2689d8eb3121e6b887dffb7f25be4a4b1fe3b9f0",
        )
        self.assertEqual(len({card.card_id for card in POOL_CARDS}), 130)
        self.assertEqual(len({card.name for card in POOL_CARDS}), 130)
        self.assertTrue(all(1 <= card.star <= 4 for card in POOL_CARDS))
        self.assertTrue(all(card.sfm_id1 > 0 and card.sfm_id2 > 0 for card in POOL_CARDS))
        self.assertFalse({1, 2, 3, 4, 5} & {card.card_id for card in POOL_CARDS})
        self.assertEqual(
            [card.name for card in POOL_CARDS].count("葱油包"), 1
        )

    def test_stride_is_deterministic_and_distinct(self) -> None:
        ten = [pool_card_at(1, index) for index in range(10)]
        self.assertEqual(len({card.card_id for card in ten}), 10)
        self.assertEqual(pool_card_at(1, 0), pool_card_at(1, 0))
        self.assertEqual(
            [pool_index_at(1, index) for index in range(10)],
            [(53 + index) % 130 for index in range(10)],
        )
        self.assertNotEqual(pool_card_at(1, 0), pool_card_at(2, 0))


class CardStoreTests(unittest.TestCase):
    def test_record_and_read_fixture_row(self) -> None:
        store = RoleStore.in_memory()
        self.addCleanup(store.close)
        record, created = store.create_or_get(
            account_key="a" * 64,
            server_id=1,
            role_name="OwnerDraw",
            career=1,
            create_time=0,
            compatibility_profile_id="cn-0.1.20.1-revival-v15",
        )
        self.assertTrue(created)
        row, outcome = store.record_card_instance(
            record=record,
            instance_id="revival-fixture-draw-0001-01",
            card_cfg_id=FIXTURE_CARD_CFG_ID,
            level=1,
            sfm_id1=30479,
            sfm_id2=30261,
            origin=DETERMINISTIC_FIXTURE_ORIGIN,
            fixture_id="drawcard-result-0001",
            fixture_kind=DETERMINISTIC_FIXTURE_KIND,
        )
        self.assertEqual(outcome, "created")
        self.assertEqual(row.origin, DETERMINISTIC_FIXTURE_ORIGIN)
        self.assertEqual(row.fixture_kind, DETERMINISTIC_FIXTURE_KIND)
        self.assertEqual(len(row.return_card_info_fields()), 21)
        rows = store.card_instances(account_key="a" * 64)
        self.assertEqual([item.instance_id for item in rows],
                         ["revival-fixture-draw-0001-01"])
        again, outcome = store.record_card_instance(
            record=record,
            instance_id="revival-fixture-draw-0001-01",
            card_cfg_id=FIXTURE_CARD_CFG_ID,
            level=2,
            origin=DETERMINISTIC_FIXTURE_ORIGIN,
            fixture_id="drawcard-result-0001",
            fixture_kind=DETERMINISTIC_FIXTURE_KIND,
        )
        self.assertEqual(outcome, "updated")
        self.assertEqual(again.level, 2)


class _Server:
    def __init__(
        self,
        *,
        grant,
        profile_id=OWNER_DRAW_TEST_PROFILE_ID,
        draw_odds="deterministic_fixture",
        draw_rng=None,
    ):
        self.events: list[dict] = []
        self.store = RoleStore.in_memory()
        self.store._profile_fields = {"play_profile_id": profile_id}
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=self.store,
            test_grant=grant,
            draw_odds=draw_odds,
            draw_rng=draw_rng,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()

    def wait_event(self, name: str, timeout: float = 2.0) -> dict:
        """Wait for one emitted event (the send thread emits after the packet)."""

        return self.wait_events(name, 1, timeout)[0]

    def wait_events(
        self, name: str, count: int, timeout: float = 2.0
    ) -> list[dict]:
        """Wait until at least ``count`` events of one name exist."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [
                item for item in self.events if item.get("event") == name
            ]
            if len(matches) >= count:
                return matches
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} #{count} was not emitted")

    def connect_login(self, *, create=True):
        writer = WriterState()
        reader = ReaderState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        payload = bytearray(login.wire)
        if create:
            payload.extend(
                encode_packet(
                    C2S_REGISTRY.by_id(2), (b"OwnerDraw", 1), writer
                ).wire
            )
        # The base login segment ends at S2C 9(1, false); the client's own
        # `receivePackage(1, false)` is what releases the terminal segment
        # (including S2C 27 and the card snapshot).
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
        stream = socket.create_connection(
            ("127.0.0.1", self.server.server_address[1]), timeout=2
        )
        stream.settimeout(2)
        stream.sendall(bytes(payload))
        return stream, writer, reader, bytearray()

    def send(self, stream, writer, protocol_id, fields):
        stream.sendall(encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, writer).wire)

    def read_until(self, stream, buffer, reader, protocol_id, timeout=3.0):
        deadline = time.monotonic() + timeout
        packets = []
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                chunk = stream.recv(65536)
                if not chunk:
                    return packets
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == protocol_id:
                return packets
        return packets


class OwnerDrawOverlayTests(unittest.TestCase):
    def _server(
        self,
        grant=OwnerDrawTestGrant(),
        profile_id=OWNER_DRAW_TEST_PROFILE_ID,
        **kwargs,
    ):
        server = _Server(grant=grant, profile_id=profile_id, **kwargs)
        self.addCleanup(server.close)
        return server

    def test_ticket_grant_lands_on_s2c27_positions(self) -> None:
        server = self._server()
        stream, writer, reader, buffer = server.connect_login()
        try:
            packets = server.read_until(stream, buffer, reader, 27)
            role_info = next(
                p for p in packets if p.spec.protocol_id == 10
            )
            self.assertEqual(role_info.fields[4], 1)
            role_value = [p for p in packets if p.spec.protocol_id == 27][-1]
            self.assertEqual(role_value.fields[LOTTERY_TICKET_VALUE_INDEX], 30)
            self.assertEqual(
                role_value.fields[LOTTERY_TICKET_ADVANCED_VALUE_INDEX], 30
            )
        finally:
            stream.close()
        event = server.wait_event("synthetic_test_grant_applied")
        self.assertEqual(event["lottery_ticket"], 30)
        self.assertEqual(event["lottery_ticket_advanced"], 30)
        self.assertEqual(event["wire_positions"], [19, 37])
        self.assertFalse(event["persisted"])

    def test_ticket_draw_returns_labelled_fixture_and_persists(self) -> None:
        server = self._server()
        stream, writer, reader, buffer = server.connect_login()
        try:
            server.read_until(stream, buffer, reader, 27)
            server.send(stream, writer, 274, (1, 0))
            packets = server.read_until(stream, buffer, reader, 539)
            self.assertTrue(any(p.spec.protocol_id == 30 for p in packets))
            self.assertTrue(any(p.spec.protocol_id == 537 for p in packets))
            results = [p for p in packets if p.spec.protocol_id == 538]
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].fields[0].startswith(b"revival-fixture-draw-"))
            expected = pool_card_at(1, 0)
            card_infos = [p for p in packets if p.spec.protocol_id == 30]
            self.assertEqual([info.fields[1] for info in card_infos],
                             [expected.card_id])
            end = [p for p in packets if p.spec.protocol_id == 539][-1]
            self.assertEqual(end.fields, (1,))
        finally:
            stream.close()
        event = server.wait_event("tcp_draw_test_fixture_sent")
        self.assertEqual(event["family"], "lottery")
        self.assertEqual(event["result_count"], 1)
        self.assertEqual(event["probability_authority"],
                         "deterministic_test_fixture_not_rng")
        self.assertFalse(event["real_probability"])
        self.assertTrue(event["persisted"])
        self.assertEqual(
            event["origin"], DETERMINISTIC_FIXTURE_ORIGIN
        )
        self.assertEqual(event["card_cfg_ids"], [expected.card_id])
        self.assertEqual(event["card_names"], [expected.name])
        self.assertEqual(event["pool_source"], POOL_SOURCE)
        self.assertEqual(event["pool_size"], len(POOL_CARDS))
        self.assertEqual(event["pool_indexes"], [pool_index_at(1, 0)])

    def test_ten_diamond_draw_and_relogin_replay(self) -> None:
        server = self._server()
        expected = [pool_card_at(1, index) for index in range(10)]
        expected_ids = [card.card_id for card in expected]
        stream, writer, reader, buffer = server.connect_login()
        try:
            server.read_until(stream, buffer, reader, 27)
            server.send(stream, writer, 33, (10, 262, 0, 0))
            packets = server.read_until(stream, buffer, reader, 499)
            results = [p for p in packets if p.spec.protocol_id == 498]
            card_infos = [p for p in packets if p.spec.protocol_id == 30]
            self.assertEqual(len(results), 10)
            self.assertEqual(len(card_infos), 10)
            self.assertEqual([info.fields[1] for info in card_infos], expected_ids)
            self.assertEqual(len(set(expected_ids)), 10)
            self.assertTrue(any(p.spec.protocol_id == 500 for p in packets))
            self.assertEqual(
                [p for p in packets if p.spec.protocol_id == 499][-1].fields,
                (1,),
            )
        finally:
            stream.close()

        # A fresh login sees the same fixture rows through 29/30/31.
        stream, writer, reader, buffer = server.connect_login(create=False)
        try:
            server.read_until(stream, buffer, reader, 27)
            packets = server.read_until(stream, buffer, reader, 31)
            card_infos = [p for p in packets if p.spec.protocol_id == 30]
            self.assertEqual(len(card_infos), 10)
            self.assertEqual([info.fields[1] for info in card_infos], expected_ids)
            self.assertTrue(any(p.spec.protocol_id == 29 for p in packets))
        finally:
            stream.close()
        event = server.wait_event("tcp_draw_test_fixture_sent")
        self.assertEqual(event["card_cfg_ids"], expected_ids)
        self.assertEqual(event["card_names"], [card.name for card in expected])
        self.assertEqual(event["pool_indexes"],
                         [pool_index_at(1, index) for index in range(10)])
        snapshot = server.wait_event("tcp_card_snapshot_sent")
        self.assertEqual(snapshot["rows_sent"], 10)
        self.assertEqual(snapshot["origins"], [DETERMINISTIC_FIXTURE_ORIGIN])
        self.assertEqual(snapshot["suppressed_fixture_rows"], 0)

    def test_reconnect_resumes_role_and_draw_overlay(self) -> None:
        server = self._server()
        stream, writer, reader, buffer = server.connect_login()
        try:
            server.read_until(stream, buffer, reader, 27)
        finally:
            stream.close()

        # A new socket after backgrounding: the client sends C2S 10 as the
        # first packet and expects the empty S2C 15 acknowledgement.
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        stream = socket.create_connection(
            ("127.0.0.1", server.server.server_address[1]), timeout=2
        )
        stream.settimeout(2)
        reconnect_fields = (
            1.0,
            b"local-user",
            0,
            b"",
            b"0.1.20.1",
            b"local-channel",
            b"0",
            b"android",
        )
        stream.sendall(
            encode_packet(
                C2S_REGISTRY.by_id(10), reconnect_fields, writer
            ).wire
        )
        packets = server.read_until(stream, buffer, reader, 15)
        ack = [p for p in packets if p.spec.protocol_id == 15][-1]
        self.assertEqual(ack.fields, ())
        # `pkgReconnectMgr.reconnectSuccess` calls sendEnterGame(); the
        # resumed session must accept it without a fresh login segment.
        server.send(stream, writer, 4, (False,))
        packets = server.read_until(stream, buffer, reader, 13)
        self.assertTrue(any(p.spec.protocol_id == 13 for p in packets))
        server.send(stream, writer, 33, (1, 262, 0, 0))
        packets = server.read_until(stream, buffer, reader, 499)
        self.assertTrue(any(p.spec.protocol_id == 498 for p in packets))
        stream.close()

        event = server.wait_event("tcp_reconnect_accepted")
        self.assertEqual(event["role_origin"], "reconnected_existing_binding")
        self.assertGreater(event["role_id"], 0)
        draw = server.wait_event("tcp_draw_test_fixture_sent")
        self.assertEqual(draw["pool_indexes"], [53])
        self.assertEqual(draw["card_cfg_ids"], [pool_card_at(1, 0).card_id])

    def test_reconstructed_probability_draw_and_pity_persistence(self) -> None:
        class _NoLegendRng:
            def random(self) -> float:
                return 0.999

            def choice(self, sequence):
                return sequence[0]

        server = self._server(
            draw_odds="reconstructed_rate_table", draw_rng=_NoLegendRng()
        )
        stream, writer, reader, buffer = server.connect_login()
        try:
            server.read_until(stream, buffer, reader, 27)
            server.send(stream, writer, 33, (10, 262, 0, 0))
            packets = server.read_until(stream, buffer, reader, 499)
            self.assertEqual(
                len([p for p in packets if p.spec.protocol_id == 498]), 10
            )
        finally:
            stream.close()
        first = server.wait_event("tcp_draw_probability_result_sent")
        self.assertTrue(first["real_probability"])
        self.assertEqual(
            first["probability_authority"], "reconstructed_client_help_text"
        )
        self.assertEqual(first["pool"], "advanced")
        self.assertEqual(len(first["card_cfg_ids"]), 10)
        self.assertTrue(
            set(first["card_cfg_ids"]) <= set().union(*TIER_CARD_IDS.values())
        )
        self.assertEqual(first["tiers"][:9], ["normal"] * 9)
        self.assertEqual(first["tiers"][9], "rare")
        self.assertEqual(first["forced"][9], "rare_guarantee_10")
        self.assertEqual(
            first["pity_after"],
            {"since_legend": 10, "since_rare": 0, "since_featured_legend": 0},
        )

        # A fresh login continues from the persisted counters.
        stream, writer, reader, buffer = server.connect_login(create=False)
        try:
            server.read_until(stream, buffer, reader, 27)
            server.send(stream, writer, 33, (1, 262, 0, 0))
            server.read_until(stream, buffer, reader, 499)
        finally:
            stream.close()
        events = server.wait_events("tcp_draw_probability_result_sent", 2)
        second = events[-1]
        self.assertEqual(second["pity_before"], first["pity_after"])
        self.assertEqual(
            second["pity_after"],
            {"since_legend": 11, "since_rare": 1, "since_featured_legend": 0},
        )

    def test_hot_spot_snapshot_and_featured_legend(self) -> None:
        class _AlwaysLegendRng:
            def random(self) -> float:
                return 0.0

            def choice(self, sequence):
                return sequence[0]

        server = self._server(
            draw_odds="reconstructed_rate_table", draw_rng=_AlwaysLegendRng()
        )
        stream, writer, reader, buffer = server.connect_login()
        try:
            packets = server.read_until(stream, buffer, reader, 27)
            role_info = next(
                p for p in packets if p.spec.protocol_id == 10
            )
            self.assertEqual(role_info.fields[4], 15)
            packets = server.read_until(stream, buffer, reader, 1170)
            infos = [p for p in packets if p.spec.protocol_id == 1168]
            self.assertEqual([p.fields[0] for p in infos], [1015, 1016, 1017])
            self.assertTrue(
                any(p.spec.protocol_id == 1167 for p in packets)
            )
            server.send(stream, writer, 33, (10, 262, 1016, 0))
            server.read_until(stream, buffer, reader, 499)
        finally:
            stream.close()
        event = server.wait_event("tcp_draw_probability_result_sent")
        self.assertEqual(event["pool"], "limited")
        self.assertEqual(event["hot_spot_id"], 1016)
        self.assertEqual(event["featured_card_id"], 4527)
        self.assertIn(4527, event["card_cfg_ids"])
        self.assertGreaterEqual(event["forced"].count("featured_legend"), 2)
        snapshot = server.wait_event("tcp_hot_spot_snapshot_sent")
        self.assertEqual(snapshot["banners"], [1015, 1016, 1017])
        self.assertFalse(snapshot["historical_schedule_recovered"])

        # The per-banner counter survives a relogin.
        stream, writer, reader, buffer = server.connect_login(create=False)
        try:
            server.read_until(stream, buffer, reader, 27)
            server.send(stream, writer, 33, (1, 262, 1016, 0))
            server.read_until(stream, buffer, reader, 499)
        finally:
            stream.close()
        events = server.wait_events("tcp_draw_probability_result_sent", 2)
        self.assertEqual(
            events[-1]["pity_before"]["since_featured_legend"],
            event["pity_after"]["since_featured_legend"],
        )

    def test_overlay_off_returns_failure_and_grant_refusal(self) -> None:
        server = self._server(grant=None)
        stream, writer, reader, buffer = server.connect_login()
        try:
            packets = server.read_until(stream, buffer, reader, 27)
            role_value = [p for p in packets if p.spec.protocol_id == 27][-1]
            self.assertEqual(role_value.fields[LOTTERY_TICKET_VALUE_INDEX], 0)
            self.assertEqual(
                role_value.fields[LOTTERY_TICKET_ADVANCED_VALUE_INDEX], 0
            )
            server.send(stream, writer, 274, (1, 0))
            packets = server.read_until(stream, buffer, reader, 539)
            self.assertTrue(any(p.spec.protocol_id == 537 for p in packets))
            self.assertEqual(
                [p for p in packets if p.spec.protocol_id == 539][-1].fields,
                (2,),
            )
            self.assertFalse(any(p.spec.protocol_id == 30 for p in packets))
        finally:
            stream.close()
        refused = server.wait_event("tcp_draw_test_refused")
        self.assertEqual(refused["reason"], "overlay_disabled")

    def test_grant_is_refused_for_another_profile(self) -> None:
        server = self._server(profile_id="not-qa-changan-main")
        stream, writer, reader, buffer = server.connect_login()
        try:
            packets = server.read_until(stream, buffer, reader, 27)
            role_value = [p for p in packets if p.spec.protocol_id == 27][-1]
            self.assertEqual(role_value.fields[LOTTERY_TICKET_VALUE_INDEX], 0)
        finally:
            stream.close()
        server.wait_event("synthetic_test_grant_refused")
        self.assertIsNone(server.server.owner_draw_test_grant)


if __name__ == "__main__":
    unittest.main()
