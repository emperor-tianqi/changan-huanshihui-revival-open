"""The chapter-fight card-EXP producer.

The client's victory view only *displays* ``guankaMonster[fightId].cardExp``;
the card rows are written exclusively by S2C 34 ``updateCard``.  This slice
turns the client's own C2S 67 settlement into that producer:

* the fight id comes from the same-connection C2S 69;
* the result code is decoded from C2S 67 slot 12 (``base + result * n``) with
  ``revival_stub.battle_rewards``' recovered randvc constants;
* the team is the preset captured at C2S 69;
* the level/EXP math is the client's own ``calculateLevel`` over ``cardLv``;
* every changed card is replayed with a full 21-value ``updateCard`` row.

Nothing here is a device observation: the real-device part is NOT_VERIFIED.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest

from revival_stub.battle_rewards import (
    CARD_EXP_BY_FIGHT,
    CARD_EXP_DIGEST,
    GAMEOVERRESULT_LOSE,
    GAMEOVERRESULT_NONE,
    GAMEOVERRESULT_WIN,
    card_exp_digest,
    card_exp_for_fight,
    decode_game_over_result,
)
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import (
    BATTLE_START_RAW_RANDOM_SEED,
    get_profile,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY
from revival_stub.role_store import (
    TEAM_CARD_SLOTS,
    RoleStore,
    RoleStoreError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer

from tests.test_codec import BATTLE_GAME_OVER_RUN0048_FIELDS, BATTLE_START_FIELDS

SERVER_STARTED_UNIX = 1_700_000_000
ACCOUNT_KEY = dict(
    server_id=1.0,
    platform="android",
    channel_id="local-channel",
    channel_sub_id="0",
    sdk_identity="local-user",
)

#: The 2026-10-18 tavern settlement (fight 400011, map 6), verbatim.  Its
#: slot 12 is the victory value; the encoded tbExtra values are the client's
#: own (chapter 1, x=6, y=0, reward index 3, level 0, fight 400011).
TAVERN_BATTLE_START = (False, 0, 86, 400011.0, 6.0, 0.0, 1.0,
                       3.0, 0.0, 0.0, 0.0, 0.0, b"")
TAVERN_GAME_OVER = (
    86, 0,
    6177140.0, 6455473.0, 4144142.0, 7597300.0, 2383275.0, 5354778.0,
    4904306.0, 5038514.0, 2277793.0, 4350656.0, 4251968.0, 6186266.0,
    b"", b"", b"",
    b"4,1,1902,1200,0|1_101_588_630_1_15_1_11001_;"
    b"0_1103_1080_462_1_1_1_30104,30144_;"
    b"0_4525_3861_2984_1_1_4_27005,37091,37100_;"
    b"0_4524_307_200_0_1_4_30323,30152,30537_;"
    b"0_4523_2185_300_0_1_4_36186,36187,39002_;"
    b"0_4522_1902_1216_0_1_4_36133,36134,36135_^^|"
    b"0_5304_2877_3861_1_3_3_10084,30098,20005_;"
    b"0_1203_1852_3793_1_5_2_30229,30274_;"
    b"0_2303_1063_1677_1_3_2_30381,30037_;"
    b"0_10416_0_592_1_5_1_30104,30144_^^",
    3391332.0, 5817451.0, 4941819.0,
)

#: The recorded loss (manual session 20261017-2212, `netGameOver: 2 0 4`).
LOSS_GAME_OVER = TAVERN_GAME_OVER[:13] + (6193484.0,) + TAVERN_GAME_OVER[14:]


def _team_cards() -> tuple[str, ...]:
    """The QA team: one recovered 1-star reward and four 4-star fixtures."""

    ids = (
        "revival-reward-101010-01",
        "revival-fixture-draw-0007-10",
        "revival-fixture-draw-0007-09",
        "revival-fixture-draw-0007-07",
        "revival-fixture-draw-0007-06",
    )
    return ids + ("",) * (TEAM_CARD_SLOTS - len(ids))


def _read_until(
    stream: socket.socket,
    server: RevivalTCPServer,
    buffer: bytearray,
    state: ReaderState,
    packets: list,
    protocol_id: int,
) -> list:
    while not any(packet.spec.protocol_id == protocol_id for packet in packets):
        try:
            packet = decode_packet(buffer, server.s2c_registry, state)
        except NeedMoreData:
            chunk = stream.recv(65536)
            if not chunk:
                raise AssertionError(
                    f"server closed before S2C {protocol_id}: "
                    f"{[p.spec.protocol_id for p in packets]}"
                )
            buffer.extend(chunk)
            continue
        del buffer[: packet.consumed]
        packets.append(packet)
    return packets


def _read_for(
    stream: socket.socket,
    server: RevivalTCPServer,
    buffer: bytearray,
    state: ReaderState,
    packets: list,
    seconds: float,
) -> list:
    deadline = time.monotonic() + seconds
    stream.settimeout(0.1)
    while time.monotonic() < deadline:
        try:
            packet = decode_packet(buffer, server.s2c_registry, state)
        except NeedMoreData:
            try:
                chunk = stream.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer.extend(chunk)
            continue
        del buffer[: packet.consumed]
        packets.append(packet)
    stream.settimeout(3)
    return packets


class BattleRewardConstantsTests(unittest.TestCase):
    def test_the_card_exp_table_is_the_shipped_guanka_monster_table(self) -> None:
        self.assertEqual(len(CARD_EXP_BY_FIGHT), 410)
        self.assertEqual(card_exp_digest(), CARD_EXP_DIGEST)
        self.assertEqual(card_exp_for_fight(101010), 5)
        self.assertEqual(card_exp_for_fight(400011), 10)
        self.assertEqual(card_exp_for_fight(400041), 0)
        self.assertIsNone(card_exp_for_fight(999999))

    def test_the_result_code_is_decoded_not_guessed(self) -> None:
        self.assertEqual(decode_game_over_result(6186266.0), GAMEOVERRESULT_WIN)
        self.assertEqual(decode_game_over_result(6193484.0), GAMEOVERRESULT_LOSE)
        self.assertEqual(decode_game_over_result(6179048.0), GAMEOVERRESULT_NONE)
        self.assertEqual(
            decode_game_over_result(6179048.0 + 3 * 7218.0), 3
        )
        for bad in (None, True, float("nan"), float("inf"), 123.0, "6186266"):
            with self.subTest(value=bad):
                self.assertIsNone(decode_game_over_result(bad))

    def test_the_decoder_is_scoped_to_the_fixed_battle_start_seed(self) -> None:
        # The recovered seed/n/base are only valid for the v11 policy's fixed
        # S2C 89 dRawRandomSeed.  A change must re-derive them, not reuse them.
        self.assertEqual(BATTLE_START_RAW_RANDOM_SEED, 3364)


class BattleCardExpStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)
        key = self.store.account_key(**ACCOUNT_KEY)
        self.record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="经验道友",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id=get_profile().profile_id,
        )
        for instance_id, cfg_id, origin in (
            ("star1", 1103, "revival_local_recovered_fight_reward"),
            ("star4", 4522, "revival_local_deterministic_test_fixture"),
        ):
            self.store.record_card_instance(
                record=self.record,
                instance_id=instance_id,
                card_cfg_id=cfg_id,
                origin=origin,
            )

    def tearDown(self) -> None:
        self.store.close()

    def test_the_client_level_math_is_applied_per_star(self) -> None:
        rows = self.store.apply_card_battle_exp(
            record=self.record,
            instance_ids=("star1", "star4"),
            card_exp=10,
            provenance="test",
        )
        by_id = {row.instance_id: row for row in rows}
        # star 1: 1 + 2 + 10 = level 2 with 5 left over.
        self.assertEqual((by_id["star1"].level, by_id["star1"].exp), (2, 5))
        # star 4: exp0..exp2 = 4 + 8 + 10 = exactly level 2 with 0 left.
        self.assertEqual((by_id["star4"].level, by_id["star4"].exp), (2, 0))

    def test_an_unowned_or_absent_id_is_a_refusal_not_a_partial_write(self) -> None:
        with self.assertRaises(RoleStoreError):
            self.store.apply_card_battle_exp(
                record=self.record,
                instance_ids=("star1", "not-owned"),
                card_exp=10,
                provenance="test",
            )
        row = self.store.card_instance(
            account_key=self.record.account_key, instance_id="star1"
        )
        self.assertEqual((row.level, row.exp), (1, 0))


class _BattleServer:
    """A pre-seeded role + team, then the two-packet battle handshake."""

    def __init__(self, *, team: bool = True) -> None:
        self.events: list[dict] = []
        self.store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=self.store,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.key = self.store.account_key(**ACCOUNT_KEY)
        self.record, _ = self.store.create_or_get(
            account_key=self.key,
            server_id=1.0,
            role_name="经验道友",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id=get_profile().profile_id,
        )
        for instance_id, cfg_id, origin in (
            ("revival-reward-101010-01", 1103,
             "revival_local_recovered_fight_reward"),
            ("revival-fixture-draw-0007-10", 4525,
             "revival_local_deterministic_test_fixture"),
            ("revival-fixture-draw-0007-09", 4524,
             "revival_local_deterministic_test_fixture"),
            ("revival-fixture-draw-0007-07", 4523,
             "revival_local_deterministic_test_fixture"),
            ("revival-fixture-draw-0007-06", 4522,
             "revival_local_deterministic_test_fixture"),
        ):
            self.store.record_card_instance(
                record=self.record,
                instance_id=instance_id,
                card_cfg_id=cfg_id,
                origin=origin,
            )
        if team:
            self.store.record_team_preset(
                record=self.record,
                group_id=1,
                equip_ids=("", "", "", ""),
                god_weapon_id="",
                card_ids=_team_cards(),
            )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()

    def login(self):
        stream = socket.create_connection(
            ("127.0.0.1", self.server.server_address[1]), timeout=3
        )
        stream.settimeout(3)
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        packets: list = []
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        _read_until(stream, self.server, buffer, reader, packets, 9)
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
        _read_until(stream, self.server, buffer, reader, packets, 283)
        return stream, writer, reader, buffer, packets

    def settle(self, *, battle_start, game_over):
        stream, writer, reader, buffer, packets = self.login()
        try:
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(69), battle_start, writer
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, packets, 89)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(67), game_over, writer
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, packets, 96)
            # The reply unlocks the client first; the updateCard rows follow.
            _read_for(stream, self.server, buffer, reader, packets, 0.5)
        finally:
            stream.close()
        return packets

    def wait_event(self, name: str, timeout: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events:
                if event.get("event") == name:
                    return event
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} was not emitted")


class ChapterBattleCardExpSegmentTests(unittest.TestCase):
    def test_a_victory_updates_the_team_and_replays_on_the_next_login(self) -> None:
        battle = _BattleServer()
        try:
            packets = battle.settle(
                battle_start=TAVERN_BATTLE_START, game_over=TAVERN_GAME_OVER
            )
            updates = [p for p in packets if p.spec.protocol_id == 34]
            self.assertEqual(len(updates), 5)
            ids = {p.fields[0] for p in updates}
            self.assertEqual(
                ids,
                {
                    b"revival-reward-101010-01",
                    b"revival-fixture-draw-0007-10",
                    b"revival-fixture-draw-0007-09",
                    b"revival-fixture-draw-0007-07",
                    b"revival-fixture-draw-0007-06",
                },
            )
            for packet in updates:
                self.assertEqual(packet.fields[2], 2)  # level
                expected_exp = 5 if packet.fields[0].startswith(b"revival-reward") else 0
                self.assertEqual(packet.fields[3], expected_exp)

            applied = battle.wait_event("tcp_battle_card_exp_applied")
            self.assertIs(applied["awarded"], True)
            self.assertIs(applied["persisted"], True)
            self.assertEqual(applied["fight_id"], 400011)
            self.assertEqual(applied["card_exp"], 10)
            self.assertEqual(applied["battle_result_code"], 1)
            self.assertEqual(applied["battle_result_name"], "GAMEOVERRESULT_WIN")
            self.assertEqual(len(applied["awards"]), 5)

            rows = {
                row.instance_id: row
                for row in battle.store.card_instances(account_key=battle.key)
            }
            self.assertEqual(
                (rows["revival-reward-101010-01"].level,
                 rows["revival-reward-101010-01"].exp),
                (2, 5),
            )
            for instance_id in (
                "revival-fixture-draw-0007-10",
                "revival-fixture-draw-0007-09",
                "revival-fixture-draw-0007-07",
                "revival-fixture-draw-0007-06",
            ):
                self.assertEqual(
                    (rows[instance_id].level, rows[instance_id].exp), (2, 0)
                )

            # A fresh login replays the stored level/EXP through returnCardInfo.
            # Fixture rows are replayed only under the explicit Owner draw-test
            # grant; the recovered 101010 reward is ordinary saved state.
            stream, writer, reader, buffer, packets = battle.login()
            try:
                infos = {
                    p.fields[0]: p.fields
                    for p in packets
                    if p.spec.protocol_id == 30
                }
            finally:
                stream.close()
            self.assertEqual(infos[b"revival-reward-101010-01"][2:4], (2, 5))
        finally:
            battle.close()

    def test_a_loss_is_decoded_and_awards_nothing(self) -> None:
        battle = _BattleServer()
        try:
            packets = battle.settle(
                battle_start=TAVERN_BATTLE_START, game_over=LOSS_GAME_OVER
            )
            self.assertEqual(
                [p for p in packets if p.spec.protocol_id == 34], []
            )
            refused = battle.wait_event("tcp_battle_card_exp_not_awarded")
            self.assertEqual(refused["reason"], "battle_not_a_victory")
            self.assertEqual(refused["battle_result_code"], 2)
            rows = battle.store.card_instances(account_key=battle.key)
            self.assertEqual({(row.level, row.exp) for row in rows}, {(1, 0)})
        finally:
            battle.close()

    def test_a_zero_exp_fight_awards_nothing(self) -> None:
        battle = _BattleServer()
        fields = list(TAVERN_BATTLE_START)
        fields[3] = 400041.0  # a shipped fight whose cardExp is 0
        try:
            packets = battle.settle(
                battle_start=tuple(fields), game_over=TAVERN_GAME_OVER
            )
            self.assertEqual(
                [p for p in packets if p.spec.protocol_id == 34], []
            )
            refused = battle.wait_event("tcp_battle_card_exp_not_awarded")
            self.assertEqual(refused["reason"], "fight_card_exp_is_zero")
            rows = battle.store.card_instances(account_key=battle.key)
            self.assertEqual({(row.level, row.exp) for row in rows}, {(1, 0)})
        finally:
            battle.close()

    def test_a_battle_without_a_stored_team_awards_nothing(self) -> None:
        battle = _BattleServer(team=False)
        try:
            packets = battle.settle(
                battle_start=TAVERN_BATTLE_START, game_over=TAVERN_GAME_OVER
            )
            self.assertEqual(
                [p for p in packets if p.spec.protocol_id == 34], []
            )
            refused = battle.wait_event("tcp_battle_card_exp_not_awarded")
            self.assertEqual(refused["reason"], "no_stored_team_cards")
            rows = battle.store.card_instances(account_key=battle.key)
            self.assertEqual({(row.level, row.exp) for row in rows}, {(1, 0)})
        finally:
            battle.close()

    def test_the_run0048_victory_vector_also_decodes_as_a_win(self) -> None:
        battle = _BattleServer()
        try:
            packets = battle.settle(
                battle_start=BATTLE_START_FIELDS,
                game_over=BATTLE_GAME_OVER_RUN0048_FIELDS,
            )
            # Fight 101010's cardExp is 5, so the same five rows move too.
            self.assertEqual(
                len([p for p in packets if p.spec.protocol_id == 34]), 5
            )
            applied = battle.wait_event("tcp_battle_card_exp_applied")
            self.assertEqual(applied["fight_id"], 101010)
            self.assertEqual(applied["card_exp"], 5)
        finally:
            battle.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
