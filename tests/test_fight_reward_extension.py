"""WP-PLAY-001 S1/S2: the recovered chapter-fight rewards and their idempotency.

The first slice extends the existing `guankaMapFight.fightRewardId` producer from
fight 101010 to the two other fights whose shipped config names a KAPAI reward;
D-167 recovered the `SPECIAL_CARD` (8886) meaning of fight 102071's reward row,
so this file pins that the row now pays one 1★ dog food into the permanent
`card_special_inventory`.  The duplicate-risk paths the Owner named are all
covered: the same settlement twice, a relogin, and a server restart with the
same archive.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from revival_stub.eventlog import EventLogger
from revival_stub.recovered_rewards import (
    FIGHT_REWARD_DIGEST,
    FIGHT_REWARD_101010,
    FIGHT_REWARD_101020,
    FIGHT_REWARD_103041,
    FIGHT_SPECIAL_CARD_102071,
    FIGHT_SPECIAL_CARD_DIGEST,
    FIGHT_SPECIAL_CARD_ORIGIN,
    FIGHT_SPECIAL_CARD_RULE_PROVENANCE,
    FIGHT_SPECIAL_CARD_SOURCE,
    FIGHT_SPECIAL_CARD_VALUE_PROVENANCE,
    UNKNOWN_REWARD_DIGEST,
    reward_for_fight,
    special_card_reward_for_fight,
    unknown_reward_for_fight,
)
from revival_stub.role_store import RoleStore, RoleStoreError
from revival_stub.tcp_stub import RevivalTCPServer
from tests.test_card_team_loop import (
    ACCOUNT_KEY,
    SERVER_STARTED_UNIX,
    _Server,
)
from tests.test_codec import BATTLE_GAME_OVER_RUN0048_FIELDS, BATTLE_START_FIELDS
from tests.test_fight_money_reward import (
    LOSS_GAME_OVER,
    UNDECODED_GAME_OVER,
    WIN_GAME_OVER,
)


def _battle_start(fight_id: int, map_index: int, chapter_id: int = 1) -> tuple:
    fields = list(BATTLE_START_FIELDS)
    fields[3] = float(fight_id)
    fields[4] = float(map_index)
    fields[6] = float(chapter_id)
    return tuple(fields)


class FrozenExtendedRewardTests(unittest.TestCase):
    def test_the_frozen_table_and_unknown_row(self) -> None:
        self.assertEqual(
            FIGHT_REWARD_DIGEST,
            "7b660d8177fae1605707af32efbc6d6954ad3b86d9b94b309aaacbbe20282616",
        )
        # D-167 recovered the only row the UNKNOWN table ever held (fight
        # 102071's type 8886), so the table is empty and its digest is the
        # SHA-256 of the empty canonical string.
        self.assertEqual(
            UNKNOWN_REWARD_DIGEST,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertIs(reward_for_fight(101020), FIGHT_REWARD_101020)
        self.assertIs(reward_for_fight(103041), FIGHT_REWARD_103041)
        self.assertEqual(FIGHT_REWARD_101020.card_id, 2209)
        self.assertEqual(FIGHT_REWARD_101020.star, 2)
        self.assertEqual(FIGHT_REWARD_103041.card_id, 3312)
        self.assertEqual(FIGHT_REWARD_103041.star, 3)
        self.assertEqual(FIGHT_REWARD_101010.card_id, 1103)
        self.assertEqual(
            FIGHT_REWARD_101020.instance_id, "revival-reward-101020-01"
        )
        self.assertEqual(
            FIGHT_REWARD_103041.instance_id, "revival-reward-103041-01"
        )
        self.assertIsNone(reward_for_fight(999999))

        self.assertIsNone(unknown_reward_for_fight(102071))
        self.assertIsNone(unknown_reward_for_fight(101020))

    def test_the_special_card_row_is_the_d167_mapping(self) -> None:
        self.assertEqual(
            FIGHT_SPECIAL_CARD_DIGEST,
            "75216ec04ad66700f0811bb824e6e2c9a2a5a4bf064ac7dcbaeb76bc60f24fdd",
        )
        special = special_card_reward_for_fight(102071)
        self.assertIs(special, FIGHT_SPECIAL_CARD_102071)
        self.assertEqual(special.reward_id, 501)
        self.assertEqual(special.slot, 1)
        self.assertEqual(special.cfg_id, 1)
        self.assertEqual(special.count, 1)
        self.assertEqual(special.star, 1)
        self.assertEqual(special.value_provenance, FIGHT_SPECIAL_CARD_VALUE_PROVENANCE)
        self.assertEqual(special.rule_provenance, FIGHT_SPECIAL_CARD_RULE_PROVENANCE)
        self.assertIn("specialCard.max", FIGHT_SPECIAL_CARD_SOURCE)
        self.assertIn("SPECIAL_CARD", FIGHT_SPECIAL_CARD_SOURCE)
        self.assertIsNone(special_card_reward_for_fight(101020))
        self.assertIsNone(special_card_reward_for_fight(999999))


class ExtendedSettlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server(grant=None)
        self.addCleanup(self.server.close)

    def _settle(
        self,
        fight_id: int,
        map_index: int,
        *,
        chapter_id: int = 1,
        create: bool = True,
        wait_dex: bool = True,
        game_over: tuple = BATTLE_GAME_OVER_RUN0048_FIELDS,
    ) -> list:
        stream, writer, reader, buffer = self.server.connect(create=create)
        try:
            responses = self.server.read_until(stream, buffer, reader, 27)
            self.server.send(
                stream, writer, 69, _battle_start(fight_id, map_index, chapter_id)
            )
            responses += self.server.read_until(stream, buffer, reader, 89)
            self.server.send(stream, writer, 67, game_over)
            responses += self.server.read_until(stream, buffer, reader, 96)
            responses += self.server.read_until(
                stream, buffer, reader, 751, timeout=1.0 if wait_dex else 0.2
            )
        finally:
            stream.close()
        return responses

    def _events(self, name: str) -> list:
        return [item for item in self.server.events if item.get("event") == name]

    def _card_rows(self) -> list:
        key = self.server.store.account_key(**ACCOUNT_KEY)
        return self.server.store.card_instances(account_key=key)

    def test_101020_grants_card_2209_once_and_replays_on_relogin(self) -> None:
        responses = self._settle(101020, 5)
        reward_packets = [
            packet for packet in responses
            if packet.spec.protocol_id == 30
            and packet.fields[0] == b"revival-reward-101020-01"
        ]
        self.assertEqual(len(reward_packets), 1)
        self.assertEqual(reward_packets[0].fields[1], 2209)
        dex = [
            packet for packet in responses
            if packet.spec.protocol_id == 751 and packet.fields[0] == 2209
        ]
        self.assertEqual(len(dex), 1)
        self.assertTrue(dex[0].fields[4], "the first grant must carry bNewCard=true")
        event = self.server.wait_event("tcp_chapter_fight_reward_granted")
        self.assertEqual(event["fight_id"], 101020)
        self.assertEqual(event["reward_id"], 202)
        self.assertEqual(event["card_id"], 2209)
        self.assertTrue(event["historical_values_recovered"])
        self.assertEqual(
            [row.instance_id for row in self._card_rows()],
            ["revival-reward-101020-01"],
        )

        # The same settlement on another connection must not create a second card.
        self._settle(101020, 5, create=False, wait_dex=False)
        self.assertEqual(len(self._events("tcp_chapter_fight_reward_granted")), 1)
        self.assertEqual(
            [row.instance_id for row in self._card_rows()],
            ["revival-reward-101020-01"],
        )

        # An ordinary login replays the card row, not a second grant.
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        replayed = [
            packet for packet in packets
            if packet.spec.protocol_id == 30
            and packet.fields[0] == b"revival-reward-101020-01"
        ]
        self.assertEqual(len(replayed), 1)
        self.assertEqual(
            [row.instance_id for row in self._card_rows()],
            ["revival-reward-101020-01"],
        )

    def test_103041_grants_card_3312(self) -> None:
        responses = self._settle(103041, 4, chapter_id=3)
        reward_packets = [
            packet for packet in responses
            if packet.spec.protocol_id == 30
            and packet.fields[0] == b"revival-reward-103041-01"
        ]
        self.assertEqual(len(reward_packets), 1)
        self.assertEqual(reward_packets[0].fields[1], 3312)
        event = self.server.wait_event("tcp_chapter_fight_reward_granted")
        self.assertEqual(event["fight_id"], 103041)
        self.assertEqual(event["reward_id"], 248)
        self.assertEqual(
            [row.instance_id for row in self._card_rows()],
            ["revival-reward-103041-01"],
        )

    def _special_counts(self) -> dict[int, int]:
        key = self.server.store.account_key(**ACCOUNT_KEY)
        return self.server.store.card_special_counts(account_key=key)

    def test_102071_grants_one_star_dog_food_once_and_replays_on_relogin(self) -> None:
        responses = self._settle(102071, 7, chapter_id=2, wait_dex=False)
        self.assertEqual(self._special_counts(), {1: 1})
        event = self.server.wait_event("tcp_chapter_fight_special_card_granted")
        self.assertEqual(event["fight_id"], 102071)
        self.assertEqual(event["reward_id"], 501)
        self.assertEqual(event["reward_slot"], 1)
        self.assertEqual(event["reward_type"], 8886)
        self.assertEqual(event["special_card_cfg_id"], 1)
        self.assertEqual(event["count"], 1)
        self.assertEqual(event["star"], 1)
        self.assertEqual(event["inventory_count_after"], 1)
        self.assertEqual(event["inventory_granted"], {1: 1})
        self.assertIs(event["granted"], True)
        self.assertIs(event["persisted"], True)
        self.assertIs(event["state_changed"], True)
        self.assertEqual(event["grant_origin"], FIGHT_SPECIAL_CARD_ORIGIN)
        self.assertEqual(
            event["provenance"],
            "value=official_recovered;rule=deterministic_recovered",
        )
        self.assertEqual(event["reward_source"], FIGHT_SPECIAL_CARD_SOURCE)
        self.assertEqual(event["display"], "not_sent_until_proven")
        self.assertEqual(
            event["client_replay"], "login_returnSpecialCard_snapshot"
        )
        # No battle-scene gain-info/update or crystal packet is invented; the
        # only S2C 10 here is this connection's ordinary login role snapshot.
        response_ids = [packet.spec.protocol_id for packet in responses]
        self.assertEqual(response_ids.count(10), 1)
        self.assertEqual(
            [
                protocol_id for protocol_id in response_ids
                if protocol_id in {68, 81, 82, 83, 504, 505, 506}
            ],
            [],
        )
        self.assertEqual(self._events("tcp_chapter_fight_reward_unknown_type"), [])
        self.assertEqual(self._events("tcp_chapter_fight_reward_granted"), [])
        self.assertEqual(list(self._card_rows()), [])

        # The same settlement on another connection must not add a second food.
        self._settle(102071, 7, chapter_id=2, create=False, wait_dex=False)
        self.assertEqual(self._special_counts(), {1: 1})
        self.assertEqual(
            len(self._events("tcp_chapter_fight_special_card_granted")), 1
        )
        replay = self.server.wait_event(
            "tcp_chapter_fight_special_card_already_present"
        )
        self.assertIs(replay["granted"], False)
        self.assertIs(replay["state_changed"], False)
        self.assertEqual(replay["inventory_count_after"], 1)
        self.assertEqual(
            replay["reason"], "chapter_fight_clear_already_recorded"
        )

        # An ordinary login replays the inventory through the client's own
        # returnSpecialCard chain, and never announces a second grant.
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        start = [
            packet for packet in packets if packet.spec.protocol_id == 504
        ]
        info = [
            packet for packet in packets if packet.spec.protocol_id == 506
        ]
        end = [
            packet for packet in packets if packet.spec.protocol_id == 505
        ]
        self.assertEqual((len(start), len(info), len(end)), (1, 1, 1))
        self.assertEqual((info[0].fields[0], info[0].fields[1]), (1, 1))
        self.assertLess(
            packets.index(start[0]),
            packets.index(info[0]),
        )
        self.assertLess(
            packets.index(info[0]),
            packets.index(end[0]),
        )
        self.assertEqual(
            [packet.spec.protocol_id for packet in packets
             if packet.spec.protocol_id in {68, 81, 82, 83}],
            [],
        )
        self.assertEqual(self._special_counts(), {1: 1})

    def test_a_loss_records_the_clear_but_grants_no_dog_food(self) -> None:
        self._settle(
            102071, 7, chapter_id=2, game_over=LOSS_GAME_OVER, wait_dex=False
        )
        self.assertEqual(self._special_counts(), {})
        event = self.server.wait_event(
            "tcp_chapter_fight_special_card_not_granted"
        )
        self.assertEqual(event["reason"], "battle_not_a_victory")
        self.assertIs(event["granted"], False)
        self.assertIs(event["persisted"], False)
        self.assertIs(event["state_changed"], False)
        self.assertEqual(event["inventory_count_after"], 0)
        self.assertEqual(
            self._events("tcp_chapter_fight_special_card_granted"), []
        )
        record = self.server.store.chapter_fight_clear(
            account_key=self.server.store.account_key(**ACCOUNT_KEY),
            chapter_id=2,
            fight_id=102071,
        )
        self.assertIsNotNone(record)

    def test_an_undecoded_result_grants_no_dog_food(self) -> None:
        self._settle(
            102071, 7, chapter_id=2, game_over=UNDECODED_GAME_OVER,
            wait_dex=False,
        )
        self.assertEqual(self._special_counts(), {})
        event = self.server.wait_event(
            "tcp_chapter_fight_special_card_not_granted"
        )
        self.assertEqual(event["reason"], "result_code_undecoded")

    def test_an_inventory_refusal_rolls_back_the_clear(self) -> None:
        with mock.patch.object(
            RoleStore,
            "_add_card_special_locked",
            side_effect=RoleStoreError("forced dog-food refusal"),
        ):
            self._settle(102071, 7, chapter_id=2, wait_dex=False)
        key = self.server.store.account_key(**ACCOUNT_KEY)
        self.assertIsNone(
            self.server.store.chapter_fight_clear(
                account_key=key, chapter_id=2, fight_id=102071
            )
        )
        self.assertEqual(self._special_counts(), {})
        error = self.server.wait_event("tcp_error")
        self.assertEqual(
            error["classification"], "chapter_fight_clear_write_failed"
        )
        self.assertEqual(
            self._events("tcp_chapter_fight_special_card_granted"), []
        )


class _PersistentServer(_Server):
    """The same harness, but on the file-backed archive a restart actually reopens."""

    def __init__(self, data_dir: Path) -> None:
        self.events: list[dict] = []
        self.store = RoleStore.at_directory(
            data_dir, time_source=lambda: SERVER_STARTED_UNIX
        )
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=self.store,
            test_grant=None,
            draw_odds="deterministic_fixture",
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()


class RestartBackfillTests(unittest.TestCase):
    def test_a_clear_recorded_before_the_reward_table_was_extended_backfills_once(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            data_dir = Path(td) / "store"

            first = _PersistentServer(data_dir)
            key = first.store.account_key(**ACCOUNT_KEY)
            record, _ = first.store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="重启回填",
                career=1,
                create_time=SERVER_STARTED_UNIX,
                compatibility_profile_id="cn-0.1.20.1-revival-v15",
            )
            first.store.record_chapter_fight_clear(
                record=record,
                chapter_id=1,
                fight_id=101020,
                map_index=5,
                observed_at=SERVER_STARTED_UNIX,
                request_sequence=1,
                settlement_sequence=2,
                reply_sequence=3,
            )
            first.close()

            second = _PersistentServer(data_dir)
            try:
                stream, writer, reader, buffer = second.connect(create=False)
                try:
                    packets = second.read_until(stream, buffer, reader, 283)
                finally:
                    stream.close()
                replayed = [
                    packet for packet in packets
                    if packet.spec.protocol_id == 30
                    and packet.fields[0] == b"revival-reward-101020-01"
                ]
                self.assertEqual(len(replayed), 1)
                event = second.wait_event("tcp_chapter_fight_reward_backfilled")
                self.assertEqual(event["fight_id"], 101020)
                self.assertEqual(event["reward_id"], 202)
                self.assertEqual(event["card_id"], 2209)
                self.assertEqual(
                    event["reward_basis"], "existing_recorded_fight_clear"
                )
                self.assertFalse(event["win_loss_decoded"])
                key = second.store.account_key(**ACCOUNT_KEY)
                self.assertEqual(
                    [row.instance_id
                     for row in second.store.card_instances(account_key=key)],
                    ["revival-reward-101020-01"],
                )
            finally:
                second.close()

            # A second process reopening the same archive must neither rewrite the row
            # nor announce a second backfill.
            third = _PersistentServer(data_dir)
            try:
                stream, writer, reader, buffer = third.connect(create=False)
                try:
                    packets = third.read_until(stream, buffer, reader, 283)
                finally:
                    stream.close()
                self.assertTrue(any(
                    packet.spec.protocol_id == 30
                    and packet.fields[0] == b"revival-reward-101020-01"
                    for packet in packets
                ))
                self.assertEqual(
                    [item for item in third.events
                     if item.get("event") == "tcp_chapter_fight_reward_backfilled"],
                    [],
                )
                key = third.store.account_key(**ACCOUNT_KEY)
                self.assertEqual(
                    [row.instance_id
                     for row in third.store.card_instances(account_key=key)],
                    ["revival-reward-101020-01"],
                )
            finally:
                third.close()


class SpecialCardRestartTests(unittest.TestCase):
    """The dog food survives a file-backed restart and never duplicates."""

    def _login(self, server) -> list:
        stream, writer, reader, buffer = server.connect(create=False)
        try:
            return server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()

    def _settle(self, server, *, create: bool = False) -> None:
        stream, writer, reader, buffer = server.connect(create=create)
        try:
            server.read_until(stream, buffer, reader, 27)
            server.send(stream, writer, 69, _battle_start(102071, 7, 2))
            server.read_until(stream, buffer, reader, 89)
            server.send(stream, writer, 67, WIN_GAME_OVER)
            server.read_until(stream, buffer, reader, 96)
        finally:
            stream.close()

    @staticmethod
    def _snapshot(packets) -> list:
        start = [packet for packet in packets if packet.spec.protocol_id == 504]
        info = [packet for packet in packets if packet.spec.protocol_id == 506]
        end = [packet for packet in packets if packet.spec.protocol_id == 505]
        rows = [(packet.fields[0], packet.fields[1]) for packet in info]
        return [
            len(start), len(info), len(end), rows,
            packets.index(start[0]) < packets.index(info[0]) < packets.index(end[0])
            if start and info and end else False,
        ]

    def test_a_file_archive_replays_the_food_and_never_grants_it_twice(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            data_dir = Path(td) / "store"

            first = _PersistentServer(data_dir)
            key = first.store.account_key(**ACCOUNT_KEY)
            first.store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="狗粮重启",
                career=1,
                create_time=SERVER_STARTED_UNIX,
                compatibility_profile_id="cn-0.1.20.1-revival-v15",
            )
            try:
                self._settle(first)
                self.assertEqual(
                    first.wait_event(
                        "tcp_chapter_fight_special_card_granted"
                    )["inventory_count_after"],
                    1,
                )
                self.assertEqual(
                    first.store.card_special_counts(account_key=key), {1: 1}
                )
            finally:
                first.close()

            second = _PersistentServer(data_dir)
            try:
                self.assertEqual(
                    self._snapshot(self._login(second)),
                    [1, 1, 1, [(1, 1)], True],
                )
                self.assertEqual(
                    second.store.card_special_counts(account_key=key), {1: 1}
                )
                self.assertEqual(
                    [item for item in second.events
                     if item.get("event")
                     == "tcp_chapter_fight_special_card_granted"],
                    [],
                )
                self._settle(second)
                replay = second.wait_event(
                    "tcp_chapter_fight_special_card_already_present"
                )
                self.assertEqual(replay["inventory_count_after"], 1)
                self.assertEqual(
                    second.store.card_special_counts(account_key=key), {1: 1}
                )
            finally:
                second.close()

            # A third process reopening the same archive still sends exactly
            # the stored row and announces no grant.
            third = _PersistentServer(data_dir)
            try:
                self.assertEqual(
                    self._snapshot(self._login(third)),
                    [1, 1, 1, [(1, 1)], True],
                )
                self.assertEqual(
                    third.store.card_special_counts(account_key=key), {1: 1}
                )
            finally:
                third.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
