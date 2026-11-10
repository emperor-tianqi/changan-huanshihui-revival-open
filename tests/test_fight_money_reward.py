"""WP-PLAY-001 S4: the chapter fight's shipped money drop, paid atomically.

The chapter-1 ``guankaReward`` rows carry no money (every reachable row's
``money`` column is 0).  The money a fight pays is shipped on the fight's own
``guankaMonster`` row (``moneyLower``/``moneyUpper``); for all four chapter-1
FIGHT nodes both bounds are one value (40/40, 40/40, 60/60, 60/60), so the drop
is deterministic.  C2S 67 settlement writes that value to the existing
``role_wallet`` table *inside the same transaction* as the clear row and pushes
the absolute wallet through the recovered S2C 23 ``returnOneValue_p(7, v)``
channel the client maps to ``roleInfo.v.money`` (``pkgValueDef.lua`` line 71,
``pkgCgValueMgr.setValue``).

These tests pin the frozen rows/digest, the one-transaction grant, replay /
relogin / file-reopen idempotency, the named refusals (loss, undecoded result,
fight outside the frozen table) and the rollback of the clear row when the
wallet write refuses.  They are offline tests: the device step is
DEVICE_PENDING and nothing here is CLIENT_VISIBLE.
"""

from __future__ import annotations

import socket
import tempfile
import time
import unittest
from pathlib import Path

from revival_stub.codec import NeedMoreData, decode_packet
from revival_stub.registry import S2C_REGISTRY
from revival_stub.recovered_rewards import (
    FIGHT_MONEY_101010,
    FIGHT_MONEY_101020,
    FIGHT_MONEY_101031,
    FIGHT_MONEY_101051,
    FIGHT_MONEY_DIGEST,
    FIGHT_MONEY_ORIGIN,
    FIGHT_MONEY_REWARDS,
    FIGHT_MONEY_RULE_PROVENANCE,
    FIGHT_MONEY_SOURCE,
    FIGHT_MONEY_VALUE_PROVENANCE,
    money_reward_for_fight,
)
from revival_stub.role_store import RoleStore, RoleStoreError

from tests.test_card_team_loop import ACCOUNT_KEY, SERVER_STARTED_UNIX, _Server
from tests.test_codec import BATTLE_GAME_OVER_RUN0048_FIELDS, BATTLE_START_FIELDS

WIN_GAME_OVER = BATTLE_GAME_OVER_RUN0048_FIELDS

#: The recorded loss's slot 12 (`base + 2 * n`, manual session 20261017-2212).
LOSS_GAME_OVER = WIN_GAME_OVER[:13] + (6193484.0,) + WIN_GAME_OVER[14:]

#: A slot-12 value that is not congruent to the recovered base modulo `n`.
UNDECODED_GAME_OVER = WIN_GAME_OVER[:13] + (12345.0,) + WIN_GAME_OVER[14:]


def _battle_start(fight_id: int, map_index: int, chapter_id: int = 1) -> tuple:
    fields = list(BATTLE_START_FIELDS)
    fields[3] = float(fight_id)
    fields[4] = float(map_index)
    fields[6] = float(chapter_id)
    return tuple(fields)


def _drain(stream, buffer, reader, packets, seconds: float = 0.5) -> None:
    """Read whatever the server sends for ``seconds`` and append it in order."""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            packet = decode_packet(buffer, S2C_REGISTRY, reader)
        except NeedMoreData:
            try:
                chunk = stream.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return
            buffer.extend(chunk)
            continue
        del buffer[: packet.consumed]
        packets.append(packet)


class FrozenMoneyRewardTests(unittest.TestCase):
    def test_the_four_chapter_one_fights_are_frozen(self) -> None:
        self.assertEqual(
            FIGHT_MONEY_DIGEST,
            "4d87957ef20e2d69525e865e4360d1950a0a5a44a9cd210f0e657d84d00e833f",
        )
        self.assertEqual(
            sorted(FIGHT_MONEY_REWARDS), [101010, 101020, 101031, 101051]
        )
        self.assertIs(money_reward_for_fight(101010), FIGHT_MONEY_101010)
        self.assertIs(money_reward_for_fight(101020), FIGHT_MONEY_101020)
        self.assertIs(money_reward_for_fight(101031), FIGHT_MONEY_101031)
        self.assertIs(money_reward_for_fight(101051), FIGHT_MONEY_101051)
        self.assertEqual(
            {fight: row.amount for fight, row in FIGHT_MONEY_REWARDS.items()},
            {101010: 40, 101020: 40, 101031: 60, 101051: 60},
        )
        self.assertTrue(all(row.deterministic for row in FIGHT_MONEY_REWARDS.values()))
        self.assertIsNone(money_reward_for_fight(103041))
        self.assertIsNone(money_reward_for_fight(999999))
        self.assertIn("guankaMonster", FIGHT_MONEY_SOURCE)
        self.assertEqual(FIGHT_MONEY_VALUE_PROVENANCE, "official_recovered")
        self.assertEqual(FIGHT_MONEY_RULE_PROVENANCE, "deterministic_recovered")


class MoneySettlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server(grant=None)
        self.addCleanup(self.server.close)
        self.key = self.server.store.account_key(**ACCOUNT_KEY)

    def _settle(
        self,
        fight_id: int,
        map_index: int,
        *,
        chapter_id: int = 1,
        game_over: tuple = WIN_GAME_OVER,
        create: bool = True,
    ) -> list:
        stream, writer, reader, buffer = self.server.connect(create=create)
        try:
            packets = self.server.read_until(stream, buffer, reader, 27)
            self.server.send(
                stream, writer, 69, _battle_start(fight_id, map_index, chapter_id)
            )
            packets += self.server.read_until(stream, buffer, reader, 89)
            self.server.send(stream, writer, 67, game_over)
            packets += self.server.read_until(stream, buffer, reader, 96)
            _drain(stream, buffer, reader, packets)
        finally:
            stream.close()
        return packets

    def _money(self) -> float:
        return self.server.store.role_wallet_values(
            account_key=self.key
        ).get("money", 0.0)

    def _events(self, name: str) -> list[dict]:
        return [
            item for item in self.server.events if item.get("event") == name
        ]

    def test_a_decoded_victory_pays_the_shipped_money(self) -> None:
        packets = self._settle(101010, 2)
        money_packets = [
            packet for packet in packets if packet.spec.protocol_id == 23
        ]
        self.assertEqual(
            [(packet.fields[0], packet.fields[1]) for packet in money_packets],
            [(7, 40.0)],
        )
        self.assertEqual(self._money(), 40.0)

        event = self.server.wait_event("tcp_chapter_fight_money_granted")
        self.assertEqual(event["fight_id"], 101010)
        self.assertEqual(event["chapter_id"], 1)
        self.assertEqual(event["map_index"], 2)
        self.assertEqual(event["money_amount"], 40.0)
        self.assertEqual(event["money_lower"], 40)
        self.assertEqual(event["money_upper"], 40)
        self.assertEqual(event["money_before"], 0.0)
        self.assertEqual(event["money_after"], 40.0)
        self.assertEqual(event["money_granted"], 40.0)
        self.assertIs(event["state_changed"], True)
        self.assertIs(event["granted"], True)
        self.assertIsNone(event["reward_id"])
        self.assertIn("guankaMonster", event["reward_row"])
        self.assertEqual(event["reward_source"], FIGHT_MONEY_SOURCE)
        self.assertEqual(event["reward_digest"], FIGHT_MONEY_DIGEST)
        self.assertEqual(
            event["money_value_provenance"], "official_recovered"
        )
        self.assertEqual(
            event["money_rule_provenance"], "deterministic_recovered"
        )
        self.assertIn("chapter_fight_clear", event["idempotency_key"])
        self.assertIs(event["win_loss_decoded"], True)
        self.assertEqual(event["battle_result_code"], 1)

    def test_a_fight_without_a_card_reward_still_pays_its_money(self) -> None:
        # 101031 names no `fightRewardId` (no card/dex packets on this road),
        # but its own `guankaMonster` money row is 60/60.
        self._settle(101031, 7)
        self.assertEqual(self._money(), 60.0)
        event = self.server.wait_event("tcp_chapter_fight_money_granted")
        self.assertEqual(event["fight_id"], 101031)
        self.assertEqual(event["money_amount"], 60.0)
        self.assertEqual(
            self._events("tcp_chapter_fight_reward_granted"), []
        )

    def test_a_replayed_settlement_does_not_pay_again(self) -> None:
        self._settle(101010, 2)
        self._settle(101010, 2, create=False)
        self.assertEqual(self._money(), 40.0)
        self.assertEqual(
            len(self._events("tcp_chapter_fight_money_granted")), 1
        )
        replay = self.server.wait_event("tcp_chapter_fight_money_already_present")
        self.assertIs(replay["state_changed"], False)
        self.assertEqual(replay["money_granted"], 0.0)
        self.assertEqual(replay["money_before"], 40.0)
        self.assertEqual(replay["money_after"], 40.0)
        self.assertEqual(
            replay["reason"], "chapter_fight_clear_already_recorded"
        )

    def test_the_wallet_replays_in_the_login_value_vector(self) -> None:
        self._settle(101010, 2)
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            packets = self.server.read_until(stream, buffer, reader, 27)
        finally:
            stream.close()
        login = next(
            packet for packet in packets if packet.spec.protocol_id == 27
        )
        self.assertEqual(login.fields[0], 40.0)
        self.assertEqual(self._money(), 40.0)
        self.assertEqual(
            len(self._events("tcp_chapter_fight_money_granted")), 1
        )

    def test_a_loss_records_the_clear_but_pays_nothing(self) -> None:
        self._settle(101010, 2, game_over=LOSS_GAME_OVER)
        self.assertEqual(self._money(), 0.0)
        self.assertEqual(
            self._events("tcp_chapter_fight_money_granted"), []
        )
        event = self.server.wait_event("tcp_chapter_fight_money_not_granted")
        self.assertEqual(event["reason"], "battle_not_a_victory")
        self.assertEqual(event["battle_result_code"], 2)
        self.assertIs(event["state_changed"], False)
        self.assertIsNotNone(
            self.server.store.chapter_fight_clear(
                account_key=self.key, chapter_id=1, fight_id=101010
            )
        )

    def test_an_undecoded_result_pays_nothing(self) -> None:
        self._settle(101010, 2, game_over=UNDECODED_GAME_OVER)
        self.assertEqual(self._money(), 0.0)
        event = self.server.wait_event("tcp_chapter_fight_money_not_granted")
        self.assertEqual(event["reason"], "result_code_undecoded")
        self.assertIs(event["win_loss_decoded"], False)
        self.assertIsNone(event["battle_result_code"])

    def test_a_fight_outside_the_frozen_table_pays_nothing(self) -> None:
        self._settle(103041, 4, chapter_id=3)
        self.assertEqual(self._money(), 0.0)
        event = self.server.wait_event("tcp_chapter_fight_money_not_granted")
        self.assertEqual(
            event["reason"], "fight_not_in_recovered_money_table"
        )
        self.assertIsNone(event["money_lower"])
        self.assertIsNone(event["money_upper"])

    def test_a_wallet_refusal_rolls_back_the_clear_row(self) -> None:
        record, _ = self.server.store.create_or_get(
            account_key=self.key,
            server_id=1.0,
            role_name="回滚",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id="cn-0.1.20.1-revival-v18",
        )
        with self.assertRaises(RoleStoreError):
            self.server.store.record_chapter_fight_clear_with_money(
                record=record,
                chapter_id=1,
                fight_id=101010,
                map_index=2,
                observed_at=SERVER_STARTED_UNIX,
                request_sequence=1,
                settlement_sequence=2,
                reply_sequence=3,
                wallet_money=40,
                wallet_provenance="",  # refused inside the transaction
            )
        self.assertIsNone(
            self.server.store.chapter_fight_clear(
                account_key=self.key, chapter_id=1, fight_id=101010
            )
        )
        self.assertEqual(
            self.server.store.role_wallet_values(account_key=self.key), {}
        )


class FileBackedMoneyTests(unittest.TestCase):
    def test_a_file_archive_reopens_with_the_money_and_does_not_pay_twice(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            path = Path(td) / "store"
            store = RoleStore.at_directory(
                path, time_source=lambda: SERVER_STARTED_UNIX
            )
            try:
                key = store.account_key(**ACCOUNT_KEY)
                record, _ = store.create_or_get(
                    account_key=key,
                    server_id=1.0,
                    role_name="钱包重开",
                    career=1,
                    create_time=SERVER_STARTED_UNIX,
                    compatibility_profile_id="cn-0.1.20.1-revival-v18",
                )
                outcome = store.record_chapter_fight_clear_with_money(
                    record=record,
                    chapter_id=1,
                    fight_id=101010,
                    map_index=2,
                    observed_at=SERVER_STARTED_UNIX,
                    request_sequence=1,
                    settlement_sequence=2,
                    reply_sequence=3,
                    wallet_money=40,
                    wallet_provenance=FIGHT_MONEY_ORIGIN,
                )
                self.assertTrue(outcome.created)
                self.assertEqual(
                    (outcome.money_before, outcome.money_after,
                     outcome.money_granted),
                    (0.0, 40.0, 40.0),
                )
            finally:
                store.close()

            reopened = RoleStore.at_directory(
                path, time_source=lambda: SERVER_STARTED_UNIX
            )
            try:
                self.assertEqual(
                    reopened.role_wallet_values(account_key=key),
                    {"money": 40.0},
                )
                replay = reopened.record_chapter_fight_clear_with_money(
                    record=record,
                    chapter_id=1,
                    fight_id=101010,
                    map_index=2,
                    observed_at=SERVER_STARTED_UNIX,
                    request_sequence=4,
                    settlement_sequence=5,
                    reply_sequence=6,
                    wallet_money=40,
                    wallet_provenance=FIGHT_MONEY_ORIGIN,
                )
                self.assertFalse(replay.created)
                self.assertEqual(replay.money_before, 40.0)
                self.assertEqual(replay.money_after, 40.0)
                self.assertEqual(replay.money_granted, 0.0)
                self.assertEqual(
                    reopened.role_wallet_values(account_key=key),
                    {"money": 40.0},
                )
            finally:
                reopened.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
