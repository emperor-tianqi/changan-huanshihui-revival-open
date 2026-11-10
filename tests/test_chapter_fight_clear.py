"""TASK-0174 -- persist an observed chapter-fight settlement and replay its row.

The Owner-visible edge: after the first chapter's boss is beaten and the alert
confirm has returned the player to the main city, re-entering the chapter map
still shows the same fight model and the route behind it is blocked, because the
server never recorded the settlement (every login sends the empty
``le_tbChapterItemInfo``).

This slice records only a transaction the wire itself carried on ONE connection
-- the client's ``C2S 69 netEnterPveBattle`` (chapter ``p4``, fight ``p1``, map
index ``p2``), its ``C2S 67 battlePveGameOver`` settlement and this server's
single ``S2C 96 battlePveGameOverEnd`` answer -- and replays it as the client's
own ``S2C 390 initChapterItemInfo(dChapterId, dType, dId)`` row between
``S2C 389`` and ``S2C 391``.  The row is the client's own completed-item triple:
``dType`` is the shipped ``MAP_TYPE_FIGHT = 10`` and ``dId`` is
``itemFightId(dX, dY) = dX``, the key ``isItemFightOver`` reads.

Nothing here is a device observation: the real-device part is NOT_VERIFIED.
"""

from __future__ import annotations

import dataclasses
import socket
import sqlite3
import threading
import unittest

from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import (
    CHAPTER_FIGHT_REPLAY_CONTAINER_POLICY,
    CHAPTER_FIGHT_REPLAY_HISTORICAL_ORDERING,
    CHAPTER_FIGHT_REPLAY_MAP_TYPE,
    CHAPTER_FIGHT_REPLAY_ROW_FORMAT,
    PROFILE_ID_V1,
    PROFILE_ID_V13,
    PROFILE_ID_V14,
    PROFILE_ID_V15,
    PROFILE_ID_V16,
    PROFILE_ID_V17,
    PROFILE_ID_V18,
    PROFILE_ID_V21,
    PROFILE_ID_V22,
    PROFILE_ID_V23,
    PROFILE_ID_V24,
    get_profile,
    get_profile_v1,
    get_profile_v13,
    get_profile_v14,
    get_profile_v17,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import (
    CHAPTER_FIGHT_EVIDENCE_SETTLED_TRANSACTION,
    CHAPTER_FIGHT_ORIGIN_LOCAL_OBSERVATION,
    SCHEMA_VERSION,
    RoleStore,
    RoleStoreError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer

from tests.test_codec import (
    BATTLE_GAME_OVER_RUN0048_FIELDS,
    BATTLE_START_FIELDS,
)

SERVER_STARTED_UNIX = 1_700_000_000
SNAPSHOT_ID = 1370
ITEM_START_ID = 389
ITEM_INFO_ID = 390
ITEM_END_ID = 391


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


class ChapterFightStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)

    def tearDown(self) -> None:
        self.store.close()

    def _role(self):
        key = self.store.account_key(
            server_id=1,
            platform="android",
            channel_id="test",
            channel_sub_id="0",
            sdk_identity="chapter-fight-store",
        )
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="记录道友",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id=PROFILE_ID_V14,
        )
        return key, record

    def test_only_the_observed_triple_is_stored_and_a_second_sighting_is_idempotent(
        self,
    ) -> None:
        key, record = self._role()
        row, created = self.store.record_chapter_fight_clear(
            record=record,
            chapter_id=1,
            fight_id=101010,
            map_index=2,
            observed_at=SERVER_STARTED_UNIX + 5,
            request_sequence=31,
            settlement_sequence=57,
            reply_sequence=58,
        )
        self.assertTrue(created)
        self.assertEqual(
            (row.chapter_id, row.fight_id, row.map_index),
            (1, 101010, 2),
        )
        self.assertEqual(
            (row.request_sequence, row.settlement_sequence, row.reply_sequence),
            (31, 57, 58),
        )
        self.assertEqual(row.origin, CHAPTER_FIGHT_ORIGIN_LOCAL_OBSERVATION)
        self.assertEqual(row.observed_at, SERVER_STARTED_UNIX + 5)
        # The first observation is the fact: a second settlement cannot rewrite
        # its time or its sequences.
        again, created_again = self.store.record_chapter_fight_clear(
            record=record,
            chapter_id=1,
            fight_id=101010,
            map_index=2,
            observed_at=SERVER_STARTED_UNIX + 90,
            request_sequence=90,
            settlement_sequence=91,
            reply_sequence=92,
        )
        self.assertFalse(created_again)
        self.assertEqual(again, row)
        self.assertEqual(
            self.store.chapter_fight_clear(
                account_key=key, chapter_id=1, fight_id=101010
            ),
            row,
        )
        self.assertEqual(
            [item.fight_id for item in self.store.chapter_fight_clears(
                account_key=key
            )],
            [101010],
        )

    def test_values_the_client_could_not_have_sent_are_refused(self) -> None:
        _, record = self._role()
        for kwargs in (
            {"chapter_id": 0, "fight_id": 1, "map_index": 1},
            {"chapter_id": 1, "fight_id": 0, "map_index": 1},
            {"chapter_id": 1, "fight_id": 1, "map_index": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(RoleStoreError):
                    self.store.record_chapter_fight_clear(
                        record=record,
                        observed_at=SERVER_STARTED_UNIX,
                        request_sequence=1,
                        settlement_sequence=2,
                        reply_sequence=3,
                        **kwargs,
                    )
        self.assertEqual(self.store.chapter_fight_clears(account_key="x"), ())

    def test_a_row_that_is_not_attached_to_a_stored_role_is_never_written(self) -> None:
        _, record = self._role()
        with self.assertRaises(RoleStoreError):
            self.store.record_chapter_fight_clear(
                record=dataclasses.replace(record, role_id=record.role_id + 1),
                chapter_id=1,
                fight_id=101010,
                map_index=2,
                observed_at=SERVER_STARTED_UNIX,
                request_sequence=1,
                settlement_sequence=2,
                reply_sequence=3,
            )
        self.assertEqual(
            self.store.chapter_fight_clears(account_key=record.account_key), ()
        )

    def test_a_version_3_archive_gains_the_table_and_keeps_every_row(self) -> None:
        import tempfile
        from pathlib import Path

        from revival_stub.role_store import STORE_FILENAME

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = RoleStore.at_directory(data_dir)
            try:
                key = store.account_key(
                    server_id=1,
                    platform="android",
                    channel_id="test",
                    channel_sub_id="0",
                    sdk_identity="migrate",
                )
                record, _ = store.create_or_get(
                    account_key=key,
                    server_id=1.0,
                    role_name="迁移道友",
                    career=1,
                    create_time=SERVER_STARTED_UNIX,
                    compatibility_profile_id=PROFILE_ID_V14,
                )
                store.record_story_completion(
                    record=record,
                    story_id=1008,
                    state=2,
                    observed_at=SERVER_STARTED_UNIX,
                    evidence_kind="c2s233_ordered_successor",
                    evidence_story_id=1100,
                    predecessor_sequence=4,
                    successor_sequence=5,
                )
            finally:
                store.close()
            archive = data_dir / STORE_FILENAME
            connection = sqlite3.connect(str(archive))
            try:
                connection.execute("DROP TABLE chapter_fight_clears")
                connection.execute("DROP TABLE card_instances")
                connection.execute("DROP TABLE draw_pity")
                connection.execute("DROP TABLE hotspot_draw_pity")
                connection.execute("DROP TABLE team_presets")
                connection.execute("DROP TABLE team_state")
                connection.execute("DROP TABLE card_special_inventory")
                connection.execute("DROP TABLE role_wallet")
                connection.execute("DROP TABLE chapter_event_clears")
                connection.execute("DROP TABLE chapter_box_accepts")
                connection.execute("DROP TABLE chapter_event_reward_grants")
                connection.execute(
                    "UPDATE meta SET value = '3' WHERE key = 'schema_version'"
                )
                connection.commit()
            finally:
                connection.close()
            reopened = RoleStore.at_directory(data_dir)
            try:
                self.assertEqual(
                    reopened._meta("schema_version"), str(SCHEMA_VERSION)
                )
                self.assertTrue(
                    reopened.describe()["role_store_structure_migrated"]
                )
                self.assertEqual(
                    [row.story_id for row in reopened.story_completions(
                        account_key=key
                    )],
                    [1008],
                )
                self.assertEqual(
                    reopened.chapter_fight_clears(account_key=key), ()
                )
            finally:
                reopened.close()


class ChapterFightReplayPolicyTests(unittest.TestCase):
    def test_v14_policy_is_on_the_active_v18_successor(self) -> None:
        v1 = get_profile_v1()
        v13 = get_profile_v13()
        v14 = get_profile_v14()
        active = get_profile()
        self.assertEqual(v14.profile_id, PROFILE_ID_V14)
        # TASK-0247: the active profile is v24 (v23 + the S2C 1435 bitRabbit
        # state); the v23 parent still carries the S2C 1499 month-sign state.
        self.assertEqual(active.profile_id, PROFILE_ID_V24)
        self.assertEqual(active.parent_profile_id, PROFILE_ID_V23)
        # TASK-0182 / v17 is the one later profile that replaces a policy object:
        # only `battle_start_reply` differs from v16, so v14's copy of every other
        # domain must still be the active profile's very same object.  v18 adds
        # one login-only policy object (`chapter_box_replay`) and inherits
        # everything else from v17 unchanged.
        self.assertIsNot(active.battle_start_reply, v14.battle_start_reply)
        self.assertIs(
            active.battle_start_reply,
            get_profile_v17().battle_start_reply,
        )
        self.assertIsNone(get_profile_v17().chapter_box_replay)
        self.assertIsNotNone(active.chapter_box_replay)
        for attribute in (
            "role_values",
            "sign_catalogs",
            "sign_state_template",
            "scene_state_template",
            "active_soul_rules",
            "server_time",
            "mystery_shop",
            "story_start",
            "team_snapshot",
            "recharge_activity",
            "chapter_item_init",
            "experience_task_init",
            "chapter_task_init",
            "chapter_machine_init",
            "battle_game_over_reply",
            "prison_fight",
            "chapter_fight_replay",
        ):
            self.assertIs(
                getattr(active, attribute),
                getattr(v14, attribute),
                msg=f"{attribute} must be the very same v14 object",
            )
        for attribute in (
            "role_values",
            "sign_catalogs",
            "sign_state_template",
            "scene_state_template",
            "active_soul_rules",
            "server_time",
            "mystery_shop",
            "story_start",
            "team_snapshot",
            "recharge_activity",
            "chapter_item_init",
            "experience_task_init",
            "chapter_task_init",
            "chapter_machine_init",
            "battle_start_reply",
            "battle_game_over_reply",
            "prison_fight",
        ):
            self.assertIs(
                getattr(v14, attribute),
                getattr(v13, attribute),
                msg=f"{attribute} must be the very same v13 object",
            )
        self.assertIs(active.role_values, v1.role_values)
        self.assertIsNotNone(v14.chapter_fight_replay)
        self.assertIsNone(v13.chapter_fight_replay)
        # Only v14+ may carry the replay policy; every earlier profile refuses it
        # so its login stream stays byte-for-byte what it was.  v15 inherits the
        # very same policy object.
        with self.assertRaises(ValueError):
            dataclasses.replace(v13, chapter_fight_replay=v14.chapter_fight_replay)
        with self.assertRaises(ValueError):
            dataclasses.replace(active, chapter_fight_replay=None)
        self.assertIs(active.chapter_fight_replay, v14.chapter_fight_replay)

    def test_the_policy_emits_only_the_clients_own_row_between_its_own_pair(
        self,
    ) -> None:
        policy = get_profile().chapter_fight_replay
        assert policy is not None
        self.assertEqual(policy.row_format, CHAPTER_FIGHT_REPLAY_ROW_FORMAT)
        self.assertEqual(policy.row_format, "ppp")
        self.assertEqual(
            policy.container_policy, CHAPTER_FIGHT_REPLAY_CONTAINER_POLICY
        )
        self.assertEqual(
            policy.historical_ordering, CHAPTER_FIGHT_REPLAY_HISTORICAL_ORDERING
        )
        self.assertIs(policy.historical_values_recovered, False)
        self.assertIs(policy.replaceable, True)
        self.assertEqual(
            (policy.row_provenance.origin, policy.value_provenance.origin,
             policy.ordering_provenance.origin),
            ("deterministic_recovered", "request_echo", "synthetic_compatibility"),
        )
        for provenance in (
            policy.row_provenance,
            policy.value_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V14)
            self.assertIs(provenance.replaceable, True)
        # No rows -> exactly the v7 pair, so a v14 archive that never settled a
        # chapter fight produces the v13 stream.
        self.assertEqual(
            policy.packets(()),
            (("initChapterItemStart", ()), ("initChapterItemEnd", ())),
        )
        # The one row is (dChapterId, MAP_TYPE_FIGHT, itemFightId(dX,dY)).
        self.assertEqual(
            policy.packets(((1, 2),)),
            (
                ("initChapterItemStart", ()),
                ("initChapterItemInfo", (1, 10, 2)),
                ("initChapterItemEnd", ()),
            ),
        )
        self.assertEqual(CHAPTER_FIGHT_REPLAY_MAP_TYPE, 10)
        for bad in ((0, 2), (1, 0)):
            with self.subTest(row=bad):
                with self.assertRaises(ValueError):
                    policy.packets((bad,))
        with self.assertRaises(ValueError):
            policy.row_fields(chapter_id=True, map_index=2)
        with self.assertRaises(ValueError):
            policy.row_fields(chapter_id=1, map_index=2.5)

    def test_the_registry_names_the_clients_own_row_writer(self) -> None:
        spec = S2C_REGISTRY.by_id(ITEM_INFO_ID)
        self.assertEqual((spec.name, spec.fmt), ("initChapterItemInfo", "ppp"))


class ChapterFightReplaySegmentTests(unittest.TestCase):
    """A real socket: one observed battle, then the next login replays its row."""

    def setUp(self) -> None:
        self.events: list[dict] = []
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _settle_one_battle(self, *, battle_start_fields=BATTLE_START_FIELDS) -> list:
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as stream:
            stream.settimeout(3)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2), ("持久化道友".encode("utf-8"), 1), writer
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 9)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 283)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(69), battle_start_fields, writer
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 89)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(67),
                    BATTLE_GAME_OVER_RUN0048_FIELDS,
                    writer,
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 96)
        return responses

    def _second_login(self) -> list:
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as stream:
            stream.settimeout(3)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2), ("持久化道友".encode("utf-8"), 1), writer
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 9)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 283)
        return responses

    def _wait_event(self, name: str, count: int = 1) -> list:
        """Wait (bounded) until the handler thread has emitted `count` events."""

        import time

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            found = [event for event in self.events if event.get("event") == name]
            if len(found) >= count:
                return found
            time.sleep(0.01)
        return [event for event in self.events if event.get("event") == name]

    def test_the_settlement_is_stored_once_and_replayed_on_the_next_login(self) -> None:
        # The first login has to create the role; the second resumes it.  Both
        # use the same DEFAULT_LOGIN_FIELDS, so the account key is the same.
        first = self._settle_one_battle()
        self.assertEqual(first[-1].spec.protocol_id, 96)
        recorded = self._wait_event("tcp_chapter_fight_clear_recorded")
        self.assertEqual(len(recorded), 1)
        event = recorded[0]
        self.assertIs(event["recorded"], True)
        self.assertEqual(event["chapter_fight_chapter_id"], 1)
        self.assertEqual(event["chapter_fight_fight_id"], 101010)
        self.assertEqual(event["chapter_fight_map_index"], 2)
        self.assertEqual(event["evidence_kind"], CHAPTER_FIGHT_EVIDENCE_SETTLED_TRANSACTION)
        self.assertEqual(event["replay_row"], (1, 10, 2))
        self.assertIs(event["win_loss_decoded"], True)
        self.assertEqual(event["battle_result_code"], 1)
        self.assertEqual(event["battle_result_name"], "GAMEOVERRESULT_WIN")
        self.assertIs(event["historical_completion_time_recovered"], False)
        self.assertEqual(
            event["chapter_fight_sequences"],
            (event["chapter_fight_sequences"][0], event["settlement_sequence"],
             event["reply_sequence"]),
        )

        second = self._second_login()
        ids = [packet.spec.protocol_id for packet in second]
        self.assertEqual(ids.count(ITEM_INFO_ID), 1)
        info_index = ids.index(ITEM_INFO_ID)
        self.assertEqual(ids[info_index - 1], ITEM_START_ID)
        self.assertEqual(ids[info_index + 1], ITEM_END_ID)
        info = second[info_index]
        self.assertEqual(
            tuple(int(value) for value in info.fields), (1, 10, 2)
        )
        replayed = self._wait_event("tcp_chapter_fight_clear_replayed")
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0]["row_values"], (1, 10, 2))
        self.assertEqual(replayed[0]["row_format"], "ppp")
        self.assertEqual(replayed[0]["row_origin"], "deterministic_recovered")
        self.assertEqual(replayed[0]["value_origin"], "request_echo")
        self.assertEqual(
            replayed[0]["login_position"],
            "between_initChapterItemStart_and_End",
        )

    def test_a_settlement_without_its_request_is_named_and_stores_nothing(self) -> None:
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as stream:
            stream.settimeout(3)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2), ("持久化道友".encode("utf-8"), 1), writer
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 9)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 283)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(67),
                    BATTLE_GAME_OVER_RUN0048_FIELDS,
                    writer,
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 96)
        refused = self._wait_event("tcp_chapter_fight_clear_not_recorded")
        self.assertEqual(len(refused), 1)
        self.assertEqual(
            refused[0]["reason"], "no_pending_battle_start_on_this_connection"
        )
        self.assertFalse(
            any(
                event.get("event") == "tcp_chapter_fight_clear_recorded"
                for event in self.events
            )
        )

    def test_a_non_chapter_battle_start_is_not_a_candidate(self) -> None:
        fields = list(BATTLE_START_FIELDS)
        fields[2] = 80  # not PVETYPE_GUANKA_CHAPTER_MAP
        self._settle_one_battle(battle_start_fields=tuple(fields))
        refused = self._wait_event("tcp_chapter_fight_clear_not_recorded")
        self.assertEqual(len(refused), 1)
        self.assertEqual(
            refused[0]["reason"], "no_pending_battle_start_on_this_connection"
        )
        self.assertFalse(
            any(
                event.get("event") == "tcp_chapter_fight_clear_recorded"
                for event in self.events
            )
        )


class ChapterFightV13NegativeTests(unittest.TestCase):
    """A v13 server keeps the v7 pair: no pending state, no row, no new event."""

    def test_a_v13_server_still_sends_the_pair_only(self) -> None:
        events: list[dict] = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append),
            compatibility_profile=get_profile_v13(),
            time_source=lambda: SERVER_STARTED_UNIX,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            reader = ReaderState()
            buffer = bytearray()
            responses: list = []
            with socket.create_connection(
                ("127.0.0.1", server.server_address[1]), timeout=3
            ) as stream:
                stream.settimeout(3)
                stream.sendall(
                    encode_packet(
                        C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
                    ).wire
                    + encode_packet(
                        C2S_REGISTRY.by_id(2),
                        ("持久化道友".encode("utf-8"), 1),
                        writer,
                    ).wire
                )
                _read_until(stream, server, buffer, reader, responses, 9)
                stream.sendall(
                    encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
                )
                _read_until(stream, server, buffer, reader, responses, 283)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        ids = [packet.spec.protocol_id for packet in responses]
        self.assertEqual(ids.count(ITEM_INFO_ID), 0)
        chapter_events = [
            event
            for event in events
            if event.get("event") == "tcp_chapter_item_state_sent"
        ]
        self.assertEqual(
            [event["protocol_id"] for event in chapter_events],
            [ITEM_START_ID, ITEM_END_ID],
        )
        self.assertEqual({event["item_rows_sent"] for event in chapter_events}, {0})
        self.assertFalse(
            any(
                event.get("event")
                in (
                    "tcp_chapter_fight_clear_replayed",
                    "tcp_chapter_fight_clear_recorded",
                )
                for event in events
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
