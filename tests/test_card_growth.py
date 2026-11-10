"""The card-growth loop: 分解 (split) and 强化 (strength), server side.

Pinned here against the client's own handlers:

* ``pkgCardSplitMgr`` -> C2S 218/219/220, lock partner S2C 376; the reward
  popup reads the gain-info family, and the client's wallet mirrors the two
  ``returnOneValue_N`` deltas;
* ``pkgStrengthCardMgr`` -> C2S 14/15/16/17, lock partner S2C 70; the client's
  own ``calculateLevel`` math decides the new level, and ``updateCard`` is the
  full 21-value row the bag and team views re-read;
* both persist: a second login replays the wallet in S2C 27 and the dog-food
  inventory in S2C 504/506/505.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest

from revival_stub import card_growth
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.eventlog import EventLogger
from revival_stub.owner_draw_test import (
    GROWTH_TEST_FLAG,
    OwnerGrowthTestGrant,
    parse_growth_grant,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import (
    CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
    RoleStore,
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


class FrozenGrowthConfigTests(unittest.TestCase):
    def test_frozen_tables_match_their_digest(self) -> None:
        self.assertEqual(card_growth.frozen_digest(), card_growth.CARD_GROWTH_DIGEST)
        self.assertEqual(len(card_growth.CARD_STAR_RARE), 350)
        self.assertEqual(len(card_growth.SPLIT_REWARDS), 51)
        self.assertEqual(card_growth.SPECIAL_CARD_STARS, {1: 1, 2: 2, 3: 3, 4: 4, 5: 5})

    def test_level_math_matches_the_recovered_client(self) -> None:
        # 1103 star 1 starts at level 1 with sumExp 1+2 = 3.
        self.assertEqual(card_growth.sum_exp(1, 1, 0), 3)
        self.assertEqual(card_growth.calculate_level(1, 3), (1, 0))
        # 1+2+5+6+7+8 = 29 reaches the star-1 level cap of 5.
        self.assertEqual(card_growth.calculate_level(1, 29), (5, 0))
        self.assertEqual(card_growth.max_level(1), 5)
        self.assertEqual(card_growth.max_level(3), 10)

    def test_split_lookup_uses_star_rare_and_advance_level(self) -> None:
        self.assertEqual(card_growth.split_reward_for_card(1103, 0), (5, 0))
        self.assertEqual(card_growth.split_reward_for_card(3301, 0), (180, 36))
        self.assertEqual(card_growth.split_reward(4, 4.1, 2), (3480, 696))
        with self.assertRaises(KeyError):
            card_growth.split_reward(1, 5.0, 0)

    def test_dog_food_values(self) -> None:
        self.assertEqual(card_growth.dog_food_exp_for_cfg(1), 5)
        self.assertEqual(card_growth.dog_food_exp_for_cfg(3), 20)
        self.assertEqual(card_growth.dog_food_exp_for_cfg(5), 100)


class GrowthGrantParseTests(unittest.TestCase):
    def test_default_and_explicit_forms(self) -> None:
        grant = parse_growth_grant(GROWTH_TEST_FLAG)
        self.assertEqual(grant.food, {3: 9, 2: 3, 1: 3})
        explicit = parse_growth_grant("owner-growth:3=9,4=2")
        self.assertEqual(explicit.food, {3: 9, 4: 2})
        self.assertIsNone(parse_growth_grant(None))

    def test_refuses_out_of_range_or_empty(self) -> None:
        for value in ("owner-growth:6=1", "owner-growth:3=0", "other"):
            with self.assertRaises(ValueError):
                parse_growth_grant(value)


class DrawSerialContinuityTests(unittest.TestCase):
    """A restarted server must not reuse a persisted draw instance id.

    Observed 2026-10-18: a second Dev Server process started its serial at 1
    again and overwrote ``revival-draw-0001-*`` rows from the previous session.
    """

    def test_serial_continues_after_the_stored_maximum(self) -> None:
        store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)
        key = store.account_key(**ACCOUNT_KEY)
        record, _ = store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="序列连续性",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id="cn-0.1.20.1-revival-v16",
        )
        for instance_id in (
            "revival-draw-0007-01",
            "revival-fixture-draw-0012-03",
        ):
            store.record_card_instance(
                record=record,
                instance_id=instance_id,
                card_cfg_id=1103,
                level=1,
                sfm_id1=0,
                origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
            )
        self.assertEqual(store.max_draw_instance_serial(), 12)
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=lambda _item: None),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=store,
        )
        try:
            self.assertEqual(server.next_draw_fixture_serial(), 13)
        finally:
            server.server_close()
            store.close()


class _Server:
    def __init__(self, *, growth_grant=None) -> None:
        self.events: list[dict] = []
        self.store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)
        if growth_grant is not None:
            # The overlay is profile-scoped exactly like the ticket grant; the
            # in-memory archive has no play profile, so the test states it.
            self.store._profile_fields = {"play_profile_id": "qa-changan-main"}
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=self.store,
            growth_grant=growth_grant,
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for item in self.events:
                if item.get("event") == name:
                    return item
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} was not emitted")

    def seed_role(self):
        key = self.store.account_key(**ACCOUNT_KEY)
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="成长闭环",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id="cn-0.1.20.1-revival-v16",
        )
        return key, record

    def seed_card(self, record, instance_id: str, cfg_id: int, level=1):
        return self.store.record_card_instance(
            record=record,
            instance_id=instance_id,
            card_cfg_id=cfg_id,
            level=level,
            sfm_id1=0,
            origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
        )[0]

    def connect(self):
        writer = WriterState()
        reader = ReaderState()
        payload = bytearray(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(2), (b"GrowthRole", 1), writer).wire
        )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
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


class CardSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server()
        self.addCleanup(self.server.close)
        self.key, self.record = self.server.seed_role()
        self.server.seed_card(self.record, "split-a", 1103)
        self.server.seed_card(self.record, "split-b", 1103)

    def test_split_consumes_cards_and_credits_the_wallet(self) -> None:
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 218, ())
            self.server.send(stream, writer, 219, (b"split-a",))
            self.server.send(stream, writer, 219, (b"split-b",))
            self.server.send(stream, writer, 220, (0,))
            packets = self.server.read_until(stream, buffer, reader, 376)
        finally:
            stream.close()
        result = next(p for p in packets if p.spec.protocol_id == 376)
        self.assertEqual(result.fields, (1,))
        gains = [p for p in packets if p.spec.protocol_id == 83]
        self.assertEqual([gain.fields for gain in gains], [(28, 0, b"", 10)])
        value = [p for p in packets if p.spec.protocol_id == 24]
        self.assertEqual([p.fields for p in value], [(28, 10), (29, 0)])
        deleted = [p for p in packets if p.spec.protocol_id == 33]
        self.assertEqual(
            sorted(p.fields[0] for p in deleted), [b"split-a", b"split-b"]
        )
        self.assertEqual(
            self.server.store.card_instances(account_key=self.key), ()
        )
        self.assertEqual(
            self.server.store.role_wallet_values(account_key=self.key),
            {"yinpo": 10.0, "jinpo": 0.0},
        )
        event = self.server.wait_event("tcp_card_split_applied")
        self.assertEqual(event["yinpo"], 10)
        self.assertEqual(event["reward_source"], "shipped_cardSplit_max")

    def test_split_refuses_a_deployed_card_without_writing(self) -> None:
        self.server.store.record_team_preset(
            record=self.record,
            group_id=1,
            equip_ids=("", "", "", ""),
            god_weapon_id="",
            card_ids=("split-a",) + ("",) * 10,
            origin="revival_local_c2s_update",
        )
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 218, ())
            self.server.send(stream, writer, 219, (b"split-a",))
            self.server.send(stream, writer, 220, (0,))
            packets = self.server.read_until(stream, buffer, reader, 376)
        finally:
            stream.close()
        result = next(p for p in packets if p.spec.protocol_id == 376)
        self.assertEqual(result.fields, (2,))
        self.assertEqual(
            len(self.server.store.card_instances(account_key=self.key)), 2
        )
        self.assertEqual(
            self.server.store.role_wallet_values(account_key=self.key), {}
        )

    def test_wallet_replays_in_s2c27_on_the_next_login(self) -> None:
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 218, ())
            self.server.send(stream, writer, 219, (b"split-a",))
            self.server.send(stream, writer, 220, (0,))
            self.server.read_until(stream, buffer, reader, 376)
        finally:
            stream.close()
        stream, writer, reader, buffer = self.server.connect()
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        values = next(p for p in packets if p.spec.protocol_id == 27)
        self.assertEqual(values.fields[6], 5.0)
        self.assertEqual(values.fields[7], 0.0)


class CardStrengthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server(growth_grant=OwnerGrowthTestGrant({3: 9, 2: 3}))
        self.addCleanup(self.server.close)
        self.key, self.record = self.server.seed_role()
        self.server.seed_card(self.record, "target-card", 1103, level=1)

    def _strengthen(self, food=((3, 1),)) -> list:
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 14, (b"target-card",))
            for cfg_id, count in food:
                self.server.send(stream, writer, 16, (cfg_id, count))
            self.server.send(stream, writer, 17, ())
            packets = self.server.read_until(stream, buffer, reader, 70)
        finally:
            stream.close()
        return packets

    def test_strength_levels_the_card_with_client_math_and_persists(self) -> None:
        packets = self._strengthen(food=((3, 1),))
        result = next(p for p in packets if p.spec.protocol_id == 70)
        self.assertEqual(result.fields, (True, b"target-card"))
        updates = [p for p in packets if p.spec.protocol_id == 34]
        self.assertEqual(len(updates), 1)
        # 1103: sumExp(level 1) = 3; one 3-star dog food adds 20 -> 23.
        # 1+2+5+6+7 = 21 pays through level 4; 2 is left toward level 5.
        self.assertEqual(updates[0].fields[2], 4)  # dLevel
        self.assertEqual(updates[0].fields[3], 2)  # dExp
        food = [p for p in packets if p.spec.protocol_id == 503]
        self.assertEqual(food[-1].fields, (3, 8))
        row = self.server.store.card_instance(
            account_key=self.key, instance_id="target-card"
        )
        self.assertEqual((row.level, row.exp), (4, 2))
        self.assertEqual(
            self.server.store.card_special_counts(account_key=self.key)[3], 8
        )
        event = self.server.wait_event("tcp_card_strength_applied")
        self.assertEqual(event["level_math"], "client_calculateLevel")

    def test_strength_replays_food_and_card_on_the_next_login(self) -> None:
        self._strengthen(food=((3, 1),))
        stream, writer, reader, buffer = self.server.connect()
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        card = next(
            p
            for p in packets
            if p.spec.protocol_id == 30 and p.fields[0] == b"target-card"
        )
        self.assertEqual(card.fields[2], 4)  # level
        food = [p for p in packets if p.spec.protocol_id == 506]
        self.assertTrue(
            any(p.fields == (3, 9) or p.fields == (3, 8) for p in food)
        )

    def test_strength_without_food_is_refused_but_releases_the_lock(self) -> None:
        packets = self._strengthen(food=())
        result = next(p for p in packets if p.spec.protocol_id == 70)
        self.assertEqual(result.fields, (False, b"target-card"))
        row = self.server.store.card_instance(
            account_key=self.key, instance_id="target-card"
        )
        self.assertEqual((row.level, row.exp), (1, 0))
        self.assertEqual(
            self.server.store.card_special_counts(account_key=self.key)[3], 9
        )


if __name__ == "__main__":
    unittest.main()
