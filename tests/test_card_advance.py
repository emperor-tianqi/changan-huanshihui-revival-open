"""Card advance (进阶), C2S 18-21: the wallet sink and its persistence.

Pinned against the shipped client:

* ``pkgAdvanceCardMgr`` sends ``advanceCardStart``, one ``advanceCard`` per
  fodder instance, one ``advanceAdvCard`` per advance-card cfg id and
  ``advanceCardEnd(selectId, pkgSyncCounterMgr.getSyncCounter(ADVANCE_CARD))``;
* ``pkgCfgAdvanceCardMgr.getAdvanceCardSD`` charges the shipped
  ``cardAdvance.max`` row, and ``pkgAdvanceCardConst.checkAdvance`` admits only
  a max-strength card of star 3-5 whose ``advanceLevel`` is below the star max;
* ``pkgAdvanceCardMgr.returnAdvanceCardResult`` is the lock partner of C2S 21,
  and ``pkgCardNcMgr.updateCard`` is what rewrites the bag row.

The tests below run a real socket against the stub: success, every refusal
class, one-transaction rollback, duplicate replay, and the relogin snapshot.
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest

from revival_stub import card_growth
from revival_stub.codec import (
    MalformedPacket,
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, ProtocolSpec, S2C_REGISTRY
from revival_stub.role_store import (
    CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
    RoleStore,
    RoleStoreError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer

SERVER_STARTED_UNIX = 1_700_000_000

ACCOUNT_KEY = dict(
    server_id=1.0,
    platform="android",
    channel_id="local-channel",
    channel_sub_id="0",
    sdk_identity="local-user",
)
OTHER_ACCOUNT_KEY = dict(ACCOUNT_KEY, sdk_identity="other-user")

#: A 3-star normal card: star 3 / rare 3.0 has the four shipped advance rows.
CARD_CFG_ID = 3312
#: A 4-star card whose target level 4 row charges silver pieces.
CARD_CFG_4STAR = 4301


class FrozenAdvanceConfigTests(unittest.TestCase):
    def test_frozen_table_matches_its_digest(self) -> None:
        self.assertEqual(
            card_growth.advance_digest(), card_growth.ADVANCE_COST_DIGEST
        )
        self.assertEqual(len(card_growth.ADVANCE_COSTS), 44)
        self.assertEqual(
            card_growth.ADVANCE_MAX_LEVEL_BY_STAR,
            {3: 4, 4: 4, 5: 4, 6: 0},
        )

    def test_lookups_are_the_shipped_rows(self) -> None:
        self.assertEqual(card_growth.advance_cost(3, 3.0, 1), (200, 50, 0, 0, 0, 0))
        self.assertEqual(card_growth.advance_cost(3, 3.0, 4), (600, 0, 0, 0, 0, 1))
        self.assertEqual(
            card_growth.advance_cost(5, 5.0, 1), (400, 2400, 480, 6, 4, 0)
        )
        with self.assertRaises(KeyError):
            card_growth.advance_cost(2, 2.0, 1)
        with self.assertRaises(KeyError):
            card_growth.advance_cost(3, 3.0, 5)

    def test_only_stars_three_to_five_have_rows(self) -> None:
        for star in (1, 2, 6):
            self.assertEqual(card_growth.advance_max_level(star), 0)
        for star in (3, 4, 5):
            self.assertEqual(card_growth.advance_max_level(star), 4)


class _Server:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=self.store,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()

    def wait_event(self, name: str, timeout: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for item in self.events:
                if item.get("event") == name:
                    return item
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} was not emitted")

    def seed_role(self, key_fields=None):
        key = self.store.account_key(**(key_fields or ACCOUNT_KEY))
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="进阶闭环",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id="cn-0.1.20.1-revival-v18",
        )
        return key, record

    def seed_card(
        self,
        record,
        instance_id: str,
        cfg_id: int = CARD_CFG_ID,
        *,
        level: int = 10,
        advance_level: int = 0,
    ):
        return self.store.record_card_instance(
            record=record,
            instance_id=instance_id,
            card_cfg_id=cfg_id,
            level=level,
            advance_level=advance_level,
            sfm_id1=0,
            origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
        )[0]

    def seed_wallet(self, record, *, money: float = 0, yinpo: int = 0, jinpo: int = 0):
        for name, amount in (("money", money), ("yinpo", yinpo), ("jinpo", jinpo)):
            if amount:
                self.store.adjust_role_wallet(
                    record=record,
                    value_name=name,
                    delta=amount,
                    provenance="deterministic_test_seed",
                )

    def wallet(self, key) -> dict:
        return self.store.role_wallet_values(account_key=key)

    def connect(self):
        writer = WriterState()
        reader = ReaderState()
        payload = bytearray(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(2), (b"AdvanceRole", 1), writer).wire
        )
        payload.extend(encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire)
        stream = socket.create_connection(
            ("127.0.0.1", self.server.server_address[1]), timeout=2
        )
        stream.settimeout(2)
        stream.sendall(bytes(payload))
        return stream, writer, reader, bytearray()

    @staticmethod
    def send(stream, writer, protocol_id, fields):
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, writer).wire
        )

    @staticmethod
    def read_until(stream, buffer, reader, protocol_id, timeout=3.0):
        deadline = time.monotonic() + timeout
        packets = []
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                try:
                    chunk = stream.recv(65536)
                except socket.timeout:
                    return packets
                if not chunk:
                    return packets
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == protocol_id:
                return packets
        return packets


class CardAdvanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server()
        self.addCleanup(self.server.close)
        self.key, self.record = self.server.seed_role()
        self.target = self.server.seed_card(self.record, "advance-target")

    def wait_refusal(self, reason: str, timeout: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for item in self.server.events:
                if (
                    item.get("event") == "tcp_card_advance_refused"
                    and item.get("refusal") == reason
                ):
                    return item
            time.sleep(0.01)
        raise AssertionError(f"refusal {reason!r} was not emitted")

    def advance(
        self, stream, writer, reader, buffer, counter, fields=None, terminal=294
    ):
        self.server.send(stream, writer, 18, ())
        for field in fields or ():
            self.server.send(stream, writer, 19, (field.encode(),))
        self.server.send(stream, writer, 21, (b"advance-target", counter))
        return self.server.read_until(stream, buffer, reader, terminal)

    def test_success_charges_the_wallet_and_updates_advance_level(self) -> None:
        self.server.seed_wallet(self.record, money=1000, yinpo=100)
        stream, writer, reader, buffer = self.server.connect()
        try:
            packets = self.advance(stream, writer, reader, buffer, 0)
        finally:
            stream.close()
        ids = [packet.spec.protocol_id for packet in packets]
        self.assertIn(34, ids)
        self.assertIn(71, ids)
        card = next(packet for packet in packets if packet.spec.protocol_id == 34)
        self.assertEqual(card.fields[0], b"advance-target")
        self.assertEqual(card.fields[4], 1)  # dAdvanceLevel
        result = next(packet for packet in packets if packet.spec.protocol_id == 71)
        self.assertEqual(result.fields, (b"advance-target", True))
        values = {
            (packet.fields[0], packet.fields[1])
            for packet in packets
            if packet.spec.protocol_id in (23, 24)
        }
        self.assertIn((7, 800.0), values)
        self.assertIn((28, 50), values)
        counter = next(
            packet for packet in packets if packet.spec.protocol_id == 294
        )
        self.assertEqual(counter.fields, (110, 1))
        row = self.server.store.card_instance(
            account_key=self.key, instance_id="advance-target"
        )
        self.assertEqual(row.advance_level, 1)
        self.assertEqual(row.level, 10)
        self.assertEqual(self.server.wallet(self.key), {"money": 800.0, "yinpo": 50.0})
        event = self.server.wait_event("tcp_card_advance_applied")
        self.assertEqual(event["advance_level_before"], 0)
        self.assertEqual(event["advance_level_after"], 1)
        self.assertEqual(event["cost_source"], "shipped_cardAdvance_max")

    def test_level_four_spends_one_same_card_fodder(self) -> None:
        self.server.store.record_card_instance(
            record=self.record,
            instance_id="advance-target",
            card_cfg_id=CARD_CFG_ID,
            level=10,
            advance_level=3,
            sfm_id1=0,
            origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
        )
        self.server.seed_card(self.record, "advance-fodder", level=1)
        self.server.seed_wallet(self.record, money=1000)
        stream, writer, reader, buffer = self.server.connect()
        try:
            packets = self.advance(
                stream, writer, reader, buffer, 0, fields=("advance-fodder",)
            )
        finally:
            stream.close()
        result = next(packet for packet in packets if packet.spec.protocol_id == 71)
        self.assertEqual(result.fields, (b"advance-target", True))
        row = self.server.store.card_instance(
            account_key=self.key, instance_id="advance-target"
        )
        # TASK-0439: max advance level is the star-up: cfg+1000, advance 0.
        self.assertEqual(row.card_cfg_id, CARD_CFG_ID + 1000)
        self.assertEqual(row.advance_level, 0)
        self.assertIsNone(
            self.server.store.card_instance(
                account_key=self.key, instance_id="advance-fodder"
            )
        )
        self.assertEqual(self.server.wallet(self.key), {"money": 400.0})

    def test_insufficient_wallet_is_refused_without_writing(self) -> None:
        self.server.seed_wallet(self.record, money=100)
        stream, writer, reader, buffer = self.server.connect()
        try:
            packets = self.advance(
                stream, writer, reader, buffer, 0, terminal=71
            )
        finally:
            stream.close()
        result = next(packet for packet in packets if packet.spec.protocol_id == 71)
        self.assertEqual(result.fields, (b"advance-target", False))
        self.assertEqual(
            self.server.store.card_instance(
                account_key=self.key, instance_id="advance-target"
            ).advance_level,
            0,
        )
        self.assertEqual(self.server.wallet(self.key), {"money": 100.0})
        event = self.server.wait_event("tcp_card_advance_refused")
        self.assertEqual(event["refusal"], "insufficient_money")

    def test_unknown_and_foreign_cards_are_refused(self) -> None:
        other_key, other_record = self.server.seed_role(OTHER_ACCOUNT_KEY)
        self.server.seed_card(other_record, "other-target")
        for target in ("does-not-exist", "other-target"):
            stream, writer, reader, buffer = self.server.connect()
            try:
                self.server.send(stream, writer, 18, ())
                self.server.send(stream, writer, 21, (target.encode(), 0))
                packets = self.server.read_until(stream, buffer, reader, 71)
            finally:
                stream.close()
            result = next(
                packet for packet in packets if packet.spec.protocol_id == 71
            )
            self.assertEqual(result.fields, (target.encode(), False))
        self.assertEqual(self.server.wallet(self.key), {})

    def test_not_max_level_and_max_advance_are_refused(self) -> None:
        cases = (
            ("level-nine", dict(level=9, advance_level=0), "target_not_max_strength_level"),
            ("already-max", dict(level=10, advance_level=4), "advance_level_max"),
        )
        for instance_id, fields, refusal in cases:
            self.server.seed_card(
                self.record,
                instance_id,
                level=fields["level"],
                advance_level=fields["advance_level"],
            )
            stream, writer, reader, buffer = self.server.connect()
            try:
                self.server.send(stream, writer, 18, ())
                self.server.send(stream, writer, 21, (instance_id.encode(), 0))
                packets = self.server.read_until(stream, buffer, reader, 71)
            finally:
                stream.close()
            self.assertIn(
                (instance_id.encode(), False),
                [
                    packet.fields
                    for packet in packets
                    if packet.spec.protocol_id == 71
                ],
            )
            self.wait_refusal(refusal)

    def test_star_and_piece_cost_rows_are_refused(self) -> None:
        self.server.seed_card(self.record, "two-star", cfg_id=2201, level=10)
        self.server.seed_card(
            self.record, "four-star", cfg_id=CARD_CFG_4STAR, advance_level=3
        )
        cases = (
            ("two-star", "advance_star_out_of_range"),
            ("four-star", "insufficient_adv_piece"),
        )
        for instance_id, refusal in cases:
            stream, writer, reader, buffer = self.server.connect()
            try:
                self.server.send(stream, writer, 18, ())
                self.server.send(stream, writer, 21, (instance_id.encode(), 0))
                self.server.read_until(stream, buffer, reader, 71)
            finally:
                stream.close()
            self.wait_refusal(refusal)
        self.assertEqual(self.server.wallet(self.key), {})

    def test_a_four_star_max_advance_evolves_to_five_star(self) -> None:
        # TASK-0440: 4★+3 + 1 fodder + 6 silver pieces = 5★+0 (cfg+1000).
        self.server.seed_wallet(self.record, money=1000)
        self.server.seed_card(self.record, "evolve-target", cfg_id=CARD_CFG_4STAR, advance_level=3)
        self.server.seed_card(self.record, "evolve-fodder", cfg_id=CARD_CFG_4STAR)
        self.server.store.set_meta(
            f"adv_piece:{self.record.role_id}",
            '{"1000": 10, "2000": 10}',
        )
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 18, ())
            self.server.send(stream, writer, 19, (b"evolve-fodder",))
            self.server.send(stream, writer, 21, (b"evolve-target", 0))
            packets = self.server.read_until(stream, buffer, reader, 71)
        finally:
            stream.close()
        self.assertEqual(packets[-1].fields, (b"evolve-target", True))
        card = self.server.store.card_instance(
            account_key=self.key, instance_id="evolve-target"
        )
        self.assertEqual(card.card_cfg_id, CARD_CFG_4STAR + 1000)
        self.assertEqual(card.advance_level, 0)
        self.assertEqual(
            json.loads(self.server.store.get_meta(f"adv_piece:{self.record.role_id}")),
            {"1000": 10, "2000": 6},
        )

    def test_five_star_max_advance_evolves_to_six_star(self) -> None:
        # TASK-0451: 5★+3 + 1 fodder + 10 gold pieces = 6★+0 (cfg+1000).
        self.server.seed_wallet(self.record, money=5000)
        self.server.seed_card(
            self.record, "six-target", cfg_id=5516, advance_level=3
        )
        self.server.seed_card(self.record, "six-fodder", cfg_id=5516, level=1)
        self.server.store.set_meta(
            f"adv_piece:{self.record.role_id}",
            '{"1000": 20, "2000": 20}',
        )
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 18, ())
            self.server.send(stream, writer, 19, (b"six-fodder",))
            self.server.send(stream, writer, 21, (b"six-target", 0))
            packets = self.server.read_until(stream, buffer, reader, 71)
        finally:
            stream.close()
        self.assertEqual(packets[-1].fields, (b"six-target", True))
        card = self.server.store.card_instance(
            account_key=self.key, instance_id="six-target"
        )
        self.assertEqual(card.card_cfg_id, 6516)
        self.assertEqual(card.advance_level, 0)
        self.assertEqual(
            json.loads(self.server.store.get_meta(f"adv_piece:{self.record.role_id}")),
            {"1000": 10, "2000": 20},
        )

    def test_advance_card_selection_without_units_is_refused(self) -> None:
        # TASK-0433: the level-4 row needs one fodder card; a 妖魂拓片 selection
        # substitutes it for 30 units, and with no inventory the request is
        # refused before any wallet or card write.
        self.server.seed_wallet(self.record, money=1000, yinpo=100)
        self.server.seed_card(self.record, "rubbing-target", advance_level=3)
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 18, ())
            self.server.send(stream, writer, 20, (100001,))
            self.server.send(stream, writer, 21, (b"rubbing-target", 0))
            self.server.read_until(stream, buffer, reader, 71)
        finally:
            stream.close()
        event = self.server.wait_event("tcp_card_advance_refused")
        self.assertEqual(event["refusal"], "insufficient_special_card_units")
        self.assertEqual(
            self.server.store.card_instance(
                account_key=self.key, instance_id="rubbing-target"
            ).advance_level,
            3,
        )
        self.assertEqual(self.server.wallet(self.key), {"money": 1000.0, "yinpo": 100.0})

    def test_a_30_piece_rubbing_substitutes_one_fodder(self) -> None:
        self.server.seed_wallet(self.record, money=1000, yinpo=100)
        self.server.seed_card(self.record, "rubbing-target", advance_level=3)
        self.server.store.add_special_card(
            account_key=self.key,
            cfg_id=100001,
            count=30,
            provenance="deterministic_test_seed",
        )
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 18, ())
            self.server.send(stream, writer, 20, (100001,))
            self.server.send(stream, writer, 21, (b"rubbing-target", 0))
            packets = self.server.read_until(stream, buffer, reader, 71)
        finally:
            stream.close()
        self.assertEqual(packets[-1].fields, (b"rubbing-target", True))
        card = self.server.store.card_instance(
            account_key=self.key, instance_id="rubbing-target"
        )
        self.assertEqual(card.card_cfg_id, CARD_CFG_ID + 1000)
        self.assertEqual(card.advance_level, 0)
        self.assertEqual(
            self.server.store.card_special_counts(account_key=self.key).get(100001, 0),
            0,
        )
        self.assertEqual(self.server.wallet(self.key)["money"], 400.0)

    def test_duplicate_request_replays_success_without_charging(self) -> None:
        self.server.seed_wallet(self.record, money=1000, yinpo=100)
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.advance(stream, writer, reader, buffer, 0)
            # The transport resends the same C2S 21 (same counter, same target).
            self.server.send(stream, writer, 18, ())
            self.server.send(stream, writer, 21, (b"advance-target", 0))
            replay = self.server.read_until(stream, buffer, reader, 71)
        finally:
            stream.close()
        result = next(packet for packet in replay if packet.spec.protocol_id == 71)
        self.assertEqual(result.fields, (b"advance-target", True))
        row = self.server.store.card_instance(
            account_key=self.key, instance_id="advance-target"
        )
        self.assertEqual(row.advance_level, 1)
        self.assertEqual(self.server.wallet(self.key), {"money": 800.0, "yinpo": 50.0})
        self.server.wait_event("tcp_card_advance_replayed")
        self.assertEqual(
            self.server.store.next_advance_sync_counter(
                account_key=self.key, role_id=self.record.role_id
            ),
            1,
        )

    def test_counter_ahead_is_refused_and_resynced(self) -> None:
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 18, ())
            self.server.send(stream, writer, 21, (b"advance-target", 7))
            packets = self.server.read_until(stream, buffer, reader, 294)
        finally:
            stream.close()
        self.assertIn(
            (b"advance-target", False),
            [
                packet.fields
                for packet in packets
                if packet.spec.protocol_id == 71
            ],
        )
        counter = next(packet for packet in packets if packet.spec.protocol_id == 294)
        self.assertEqual(counter.fields, (110, 0))
        self.assertTrue(
            [
                item
                for item in self.server.events
                if item.get("event") == "tcp_card_advance_refused"
                and item.get("refusal") == "sync_counter_ahead"
            ]
        )

    def test_relogin_keeps_the_advance_level_and_restores_the_counter(self) -> None:
        self.server.seed_wallet(self.record, money=2000, yinpo=500)
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.advance(stream, writer, reader, buffer, 0)
        finally:
            stream.close()
        stream, writer, reader, buffer = self.server.connect()
        try:
            # S2C 283 is the terminal packet of the post-create login segment;
            # S2C 9 alone is also paid at the pre-create probe.
            login = self.server.read_until(stream, buffer, reader, 283)
            # The client's own counter is restored, and the card row carries
            # the new advance level.
            counters = [
                packet.fields
                for packet in login
                if packet.spec.protocol_id == 293
            ]
            self.assertEqual(counters, [(110, 1)])
            cards = [
                packet.fields
                for packet in login
                if packet.spec.protocol_id == 30
                and packet.fields[0] == b"advance-target"
            ]
            self.assertEqual(cards[0][4], 1)
            # The next legitimate advance carries the restored counter.
            self.server.send(stream, writer, 18, ())
            self.server.send(stream, writer, 21, (b"advance-target", 1))
            packets = self.server.read_until(stream, buffer, reader, 294)
        finally:
            stream.close()
        self.assertIn(
            (b"advance-target", True),
            [
                packet.fields
                for packet in packets
                if packet.spec.protocol_id == 71
            ],
        )
        self.assertEqual(
            self.server.store.card_instance(
                account_key=self.key, instance_id="advance-target"
            ).advance_level,
            2,
        )

    def test_a_role_without_advance_gets_no_counter_packet(self) -> None:
        stream, writer, reader, buffer = self.server.connect()
        try:
            login = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        self.assertFalse(
            [packet for packet in login if packet.spec.protocol_id == 293]
        )


class CardAdvanceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server()
        self.addCleanup(self.server.close)
        self.key, self.record = self.server.seed_role()
        self.server.seed_card(self.record, "target", advance_level=0)

    def test_short_wallet_rolls_the_whole_transaction_back(self) -> None:
        self.server.seed_wallet(self.record, money=100)
        before = self.server.wallet(self.key)
        with self.assertRaises(RoleStoreError):
            self.server.store.apply_card_advance(
                record=self.record,
                target_instance_id="target",
                expected_advance_level=0,
                new_advance_level=1,
                fodder_ids=(),
                money=200,
                yinpo=50,
                jinpo=0,
                sync_counter=0,
                provenance="deterministic_test",
            )
        self.assertEqual(self.server.wallet(self.key), before)
        row = self.server.store.card_instance(
            account_key=self.key, instance_id="target"
        )
        self.assertEqual(row.advance_level, 0)
        self.assertIsNone(
            self.server.store.card_advance_request(
                account_key=self.key, role_id=self.record.role_id, sync_counter=0
            )
        )

    def test_unowned_fodder_rolls_back_before_the_wallet_is_touched(self) -> None:
        self.server.seed_wallet(self.record, money=1000, yinpo=100)
        with self.assertRaises(RoleStoreError):
            self.server.store.apply_card_advance(
                record=self.record,
                target_instance_id="target",
                expected_advance_level=0,
                new_advance_level=1,
                fodder_ids=("not-owned",),
                money=200,
                yinpo=50,
                jinpo=0,
                sync_counter=0,
                provenance="deterministic_test",
            )
        self.assertEqual(self.server.wallet(self.key), {"money": 1000.0, "yinpo": 100.0})
        self.assertEqual(
            self.server.store.card_instance(
                account_key=self.key, instance_id="target"
            ).advance_level,
            0,
        )

    def test_a_second_insert_under_the_same_counter_is_refused(self) -> None:
        self.server.seed_wallet(self.record, money=1000, yinpo=100)
        self.server.store.apply_card_advance(
            record=self.record,
            target_instance_id="target",
            expected_advance_level=0,
            new_advance_level=1,
            fodder_ids=(),
            money=200,
            yinpo=50,
            jinpo=0,
            sync_counter=0,
            provenance="deterministic_test",
        )
        with self.assertRaises(RoleStoreError):
            self.server.store.apply_card_advance(
                record=self.record,
                target_instance_id="target",
                expected_advance_level=1,
                new_advance_level=2,
                fodder_ids=(),
                money=300,
                yinpo=0,
                jinpo=0,
                sync_counter=0,
                provenance="deterministic_test",
            )
        row = self.server.store.card_instance(
            account_key=self.key, instance_id="target"
        )
        self.assertEqual(row.advance_level, 1)
        self.assertEqual(self.server.wallet(self.key), {"money": 800.0, "yinpo": 50.0})


class CardAdvanceRestartTests(unittest.TestCase):
    def test_file_archive_keeps_the_level_and_counter_across_a_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = RoleStore.at_directory(td)
            key = store.account_key(**ACCOUNT_KEY)
            record, _ = store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="进阶重启",
                career=1,
                create_time=SERVER_STARTED_UNIX,
                compatibility_profile_id="cn-0.1.20.1-revival-v18",
            )
            store.record_card_instance(
                record=record,
                instance_id="restart-target",
                card_cfg_id=CARD_CFG_ID,
                level=10,
                sfm_id1=0,
                origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
            )
            store.adjust_role_wallet(
                record=record,
                value_name="money",
                delta=1000,
                provenance="deterministic_test_seed",
            )
            store.adjust_role_wallet(
                record=record,
                value_name="yinpo",
                delta=100,
                provenance="deterministic_test_seed",
            )
            store.apply_card_advance(
                record=record,
                target_instance_id="restart-target",
                expected_advance_level=0,
                new_advance_level=1,
                fodder_ids=(),
                money=200,
                yinpo=50,
                jinpo=0,
                sync_counter=0,
                provenance="deterministic_test",
            )
            store.close()

            reopened = RoleStore.at_directory(td)
            try:
                row = reopened.card_instance(
                    account_key=key, instance_id="restart-target"
                )
                self.assertEqual(row.advance_level, 1)
                self.assertEqual(row.level, 10)
                self.assertEqual(
                    reopened.role_wallet_values(account_key=key),
                    {"money": 800.0, "yinpo": 50.0},
                )
                self.assertEqual(
                    reopened.next_advance_sync_counter(
                        account_key=key, role_id=record.role_id
                    ),
                    1,
                )
                request = reopened.card_advance_request(
                    account_key=key, role_id=record.role_id, sync_counter=0
                )
                self.assertEqual(request.instance_id, "restart-target")
                self.assertEqual(request.advance_level_to, 1)
            finally:
                reopened.close()


class CardAdvanceFailClosedTests(unittest.TestCase):
    def test_a_wrong_field_count_never_reaches_the_wire(self) -> None:
        with self.assertRaises(MalformedPacket):
            encode_packet(C2S_REGISTRY.by_id(21), (b"only-one",), WriterState())

    def test_an_unregistered_neighbour_still_fails_closed(self) -> None:
        server = _Server()
        self.addCleanup(server.close)
        server.seed_role()
        stream, writer, reader, buffer = server.connect()
        try:
            # C2S 30 startRefineAdvancePiece is in the client's own catalog but
            # still unregistered; it must kill the connection, never be skipped.
            stream.sendall(
                encode_packet(
                    ProtocolSpec(30, "startRefineAdvancePiece", "N"),
                    (1,),
                    writer,
                ).wire
            )
            deadline = time.monotonic() + 2.0
            error = None
            while time.monotonic() < deadline and error is None:
                for item in server.events:
                    if (
                        item.get("event") == "tcp_error"
                        and item.get("classification") == "malformed_packet"
                        and item.get("protocol_id") == 30
                    ):
                        error = item
                        break
                time.sleep(0.01)
            self.assertIsNotNone(error, "the unregistered request was not refused")
        finally:
            stream.close()


class CardAdvanceDerivationTests(unittest.TestCase):
    def test_runtime_ids_match_the_recovered_registration(self) -> None:
        expected = {
            18: ("advanceCardStart", ""),
            19: ("advanceCard", "a"),
            20: ("advanceAdvCard", "N"),
            21: ("advanceCardEnd", "aN"),
        }
        for protocol_id, (name, fmt) in expected.items():
            spec = C2S_REGISTRY.by_id(protocol_id)
            self.assertEqual((spec.name, spec.fmt), (name, fmt))
        result = S2C_REGISTRY.by_id(71)
        self.assertEqual((result.name, result.fmt), ("returnAdvanceCardResult", "ay"))
        self.assertEqual(
            (S2C_REGISTRY.by_id(293).name, S2C_REGISTRY.by_id(293).fmt),
            ("returnSyncCounter", "NN"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(294).name, S2C_REGISTRY.by_id(294).fmt),
            ("updateSyncCounter", "NN"),
        )
