"""TASK-0184 -- the recovered `C2S 224 chapterMapEvent` contract.

The player-visible break: on 2026-10-18 the real client opened the first
chapter's 「散乱的货物」 node, sent `C2S 224 chapterMapEvent`, and the stub had
no registration for the id.  The free-play path recorded it as
`known_unimplemented`, sent nothing, and the client's own LOCK-SEND timer closed
the socket after 15 s -- the player saw 「正在努力载入 请稍候...」 the whole time.

What is asserted here, per layer:

* the captured 31-byte frame splits exactly under the client's own
  `registerC2S("NNNNNp", "chapterMapEvent")` and re-encodes byte-for-byte;
* the runtime ids 224 / 398 are the D-02 registration simulation's answer;
* the only accepted request is chapter 1 / index 36 / option 1, and every other
  shape is refused by name with no reply;
* one request produces exactly one `S2C 398`, the connection stays open, the
  action is stored once, and the next login replays the client's own
  `initChapterItemInfo(1, MAP_TYPE_EVENT, 36)` row between 389/391;
* the schema upgrade 9 -> 10 is additive and keeps every existing row.

The reward value is recovered (option 1 -> `rewardId1 = 25101` ->
`guankaReward[25101].crystal = 10`) but deliberately NOT granted: the display
path is the separate gain-info family and the persisted wallet has no crystal
column, so the client's own nil-reward branch is used.  See the report.
"""

from __future__ import annotations

import dataclasses
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from revival_stub import chapter_map_event as cme
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import PROFILE_ID_V14, get_profile_v13
from revival_stub.eventlog import EventLogger
from revival_stub.protocol_catalog import LOCK_PARTNERS
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_state import ROLE_INFO_FIELD_NAMES
from revival_stub.role_store import (
    CHAPTER_EVENT_EVIDENCE_CLIENT_ACTION,
    CHAPTER_EVENT_ORIGIN_LOCAL_OBSERVATION,
    CHAPTER_EVENT_REPLAY_MAP_TYPE,
    CRYSTAL_BUCKET_NAMES,
    SCHEMA_VERSION,
    STORE_FILENAME,
    RoleStore,
    RoleStoreError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer

SERVER_STARTED_UNIX = 1_700_000_000
ITEM_START_ID = 389
ITEM_INFO_ID = 390
ITEM_END_ID = 391
CHAPTER_EVENT_S2C_ID = 398
CHAPTER_EVENT_C2S_ID = 224

#: `.autopilot/manual_sessions/20261018-move/server.log:11716` -- the one real
#: `C2S 224` this slice stands on.  Header `40 29 1b`, key before
#: `0x211629A0`, sequence 187, key after `0x4C469260` (the next line's
#: `key_before`).
CHAPTER_EVENT_WIRE_HEX = (
    "40 29 1b a1 29 16 21 84 29 16 21 a0 29 16 21 a1 29 16 21 "
    "a0 29 16 21 a0 29 16 21 00 00 00 00"
)
CHAPTER_EVENT_KEY_BEFORE = 0x211629A0
CHAPTER_EVENT_SEQUENCE = 187
CHAPTER_EVENT_KEY_AFTER = 0x4C469260
CHAPTER_EVENT_FIELDS = (1, 36, 0, 1, 0, 0.0)


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


class RecoveredChapterMapEventVectorTests(unittest.TestCase):
    """The one C2S 224 packet the 2026-10-18 session really produced."""

    def test_the_registered_format_splits_the_session_packet_exactly(self) -> None:
        data = bytes.fromhex(CHAPTER_EVENT_WIRE_HEX)
        self.assertEqual(len(data), 31)
        state = ReaderState(
            key=CHAPTER_EVENT_KEY_BEFORE, sequence=CHAPTER_EVENT_SEQUENCE
        )
        decoded = decode_packet(data, C2S_REGISTRY, state)
        self.assertEqual(
            (decoded.spec.protocol_id, decoded.spec.name, decoded.spec.fmt),
            (CHAPTER_EVENT_C2S_ID, "chapterMapEvent", "NNNNNp"),
        )
        self.assertEqual(decoded.fields, CHAPTER_EVENT_FIELDS)
        self.assertEqual(decoded.consumed, len(data))
        self.assertEqual(decoded.key_before, CHAPTER_EVENT_KEY_BEFORE)
        self.assertEqual(decoded.key_after, CHAPTER_EVENT_KEY_AFTER)
        self.assertEqual(
            (state.key, state.sequence),
            (CHAPTER_EVENT_KEY_AFTER, (CHAPTER_EVENT_SEQUENCE + 1) & 0xFF),
        )

    def test_the_recovered_values_re_encode_to_the_same_bytes(self) -> None:
        writer = WriterState(
            key=CHAPTER_EVENT_KEY_BEFORE, sequence=CHAPTER_EVENT_SEQUENCE
        )
        encoded = encode_packet(
            C2S_REGISTRY.by_id(CHAPTER_EVENT_C2S_ID),
            CHAPTER_EVENT_FIELDS,
            writer,
        )
        self.assertEqual(encoded.wire, bytes.fromhex(CHAPTER_EVENT_WIRE_HEX))
        self.assertEqual(encoded.key_after, CHAPTER_EVENT_KEY_AFTER)


class ChapterMapEventContractTests(unittest.TestCase):
    def test_the_contract_and_node_digests_are_frozen(self) -> None:
        cme.verify_contract_digest()
        cme.verify_node_digest()
        self.assertEqual(
            (cme.C2S_PROTOCOL_ID, cme.C2S_FORMAT), (224, "NNNNNp")
        )
        self.assertEqual(
            (cme.S2C_PROTOCOL_ID, cme.S2C_FORMAT), (398, "pppppp")
        )
        self.assertEqual(cme.MAP_TYPE_EVENT, 13)
        self.assertEqual(cme.EVENT_STATUS_SUCCESS, 1)
        self.assertEqual(CHAPTER_EVENT_REPLAY_MAP_TYPE, cme.MAP_TYPE_EVENT)
        self.assertEqual(
            (C2S_REGISTRY.by_id(224).name, C2S_REGISTRY.by_id(224).fmt),
            ("chapterMapEvent", "NNNNNp"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(398).name, S2C_REGISTRY.by_id(398).fmt),
            ("chapterMapEvent", "pppppp"),
        )
        # The third argument of the client's own registerS2C call is the lock
        # partner, so this pair is the unlock of the C2S 224 lock-send.
        self.assertEqual(LOCK_PARTNERS.get("chapterMapEvent"), ("chapterMapEvent",))

    def test_the_recovered_node_is_the_one_observed_on_the_wire(self) -> None:
        node = cme.CHAPTER_ONE_EVENT_NODE
        self.assertEqual(
            (node.chapter_id, node.map_index, node.event_cfg_id),
            (1, 36, 812003),
        )
        self.assertEqual(
            (node.option_index, node.option_type, node.option_description),
            (1, cme.EVENT_TYPE_REWARD, "翻找"),
        )
        self.assertEqual(node.event_name, "散乱的货物")
        self.assertEqual((node.reward_id, node.reward_crystal), (25101, 10))
        self.assertEqual(node.event_subtype, cme.EVENT_SUBTYPE_NORMAL)
        self.assertEqual(node.is_delete, 0)

    def test_only_the_recovered_request_is_accepted(self) -> None:
        accepted = cme.classify_request(CHAPTER_EVENT_FIELDS)
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.refusal, "")
        self.assertEqual(
            accepted.request,
            cme.ChapterMapEventRequest(
                chapter_id=1, map_index=36, option_index=1, sync_count=0.0
            ),
        )
        self.assertEqual(accepted.request.replay_row(), (1, 13, 36))

        refusals = {
            (1, 36, 0, 1, 0): cme.REFUSAL_FIELD_COUNT,
            (1, 36.5, 0, 1, 0, 0.0): cme.REFUSAL_NON_INTEGRAL,
            (1, True, 0, 1, 0, 0.0): cme.REFUSAL_NON_INTEGRAL,
            (1, 36, 0, 1, 0, "x"): cme.REFUSAL_SYNC_COUNT_TYPE,
            (1, 36, 1, 1, 0, 0.0): cme.REFUSAL_CONSTANT_POSITION,
            (1, 36, 0, 1, 1, 0.0): cme.REFUSAL_CONSTANT_POSITION,
            (2, 36, 0, 1, 0, 0.0): cme.REFUSAL_UNKNOWN_NODE,
            (1, 24, 0, 0, 0, 0.0): cme.REFUSAL_UNKNOWN_NODE,
            (1, 36, 0, 2, 0, 0.0): cme.REFUSAL_UNKNOWN_OPTION,
        }
        for fields, expected in refusals.items():
            with self.subTest(fields=fields):
                decision = cme.classify_request(fields)
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.refusal, expected)
                self.assertIn(decision.refusal, cme.REFUSAL_CODES)

    def test_the_success_reply_echoes_the_request_and_sets_the_status(self) -> None:
        request = cme.classify_request(CHAPTER_EVENT_FIELDS).request
        assert request is not None
        reply = cme.success_reply_fields(request)
        self.assertEqual(reply, (1, 36, 0, 1, 0, 1.0))
        # Positions 2 and 3 are what the client hands to checkEventObjValid.
        self.assertEqual(reply[1:3], (request.map_index, 0))
        # Position 6 is the shipped EVENT_STATUS_SUCCESS constant.
        self.assertEqual(reply[5], float(cme.EVENT_STATUS_SUCCESS))
        self.assertEqual(
            cme.EVENT_STATUS_NAMES[cme.EVENT_STATUS_SUCCESS], "SUCCESS"
        )

    def test_a_refusal_reply_echoes_the_request_and_carries_illegal(self) -> None:
        # TASK-0243 / D-186: C2S 224 is a LOCK-SEND, so a refused request must
        # still be unlocked.  The status is a non-SUCCESS EVENT_STATUS_*.
        reply, padded = cme.refusal_reply_fields((1, 24, 0, 0, 0, 0.0))
        self.assertEqual(padded, [])
        self.assertEqual(reply, (1, 24, 0, 0, 0, float(cme.EVENT_STATUS_ILLEGAL)))
        self.assertEqual(len(reply), len(cme.S2C_FORMAT))
        self.assertNotEqual(reply[5], float(cme.EVENT_STATUS_SUCCESS))
        self.assertEqual(cme.REFUSAL_REPLY_STATUS, cme.EVENT_STATUS_ILLEGAL)
        self.assertEqual(cme.REFUSAL_REPLY_STATUS_NAME, "ILLEGAL")
        self.assertEqual(
            cme.REFUSAL_REPLY_STATUS_PROVENANCE, "synthetic_compatibility"
        )
        # Even a request whose constant positions are wrong is echoed verbatim;
        # the client reads none of the five positions on a non-SUCCESS status.
        reply, padded = cme.refusal_reply_fields((1, 36, 1, 1, 1, 0.0))
        self.assertEqual(padded, [])
        self.assertEqual(reply[:5], (1, 36, 1, 1, 1))
        # A non-integral value still echoes: it is the client's own number.
        reply, padded = cme.refusal_reply_fields((1, 36.5, 0, 1, 0, 0.0))
        self.assertEqual(padded, [])
        self.assertEqual(reply[1], 36.5)

    def test_a_request_that_cannot_be_echoed_is_padded_and_named(self) -> None:
        # Unreachable over the wire (`NNNNNp` always decodes six values), but a
        # direct caller can hand over fewer: those positions are filled with 0
        # and reported, never guessed.
        for fields in ((), (1, True, 0, 1, 0, 0.0)):
            with self.subTest(fields=fields):
                reply, padded = cme.refusal_reply_fields(fields)
                self.assertEqual(reply[5], float(cme.EVENT_STATUS_ILLEGAL))
                self.assertTrue(padded)
                for position in padded:
                    self.assertEqual(reply[position - 1], 0)
        reply, padded = cme.refusal_reply_fields((1, 36, 0, 1, 0))
        self.assertEqual(reply, (1, 36, 0, 1, 0, float(cme.EVENT_STATUS_ILLEGAL)))
        self.assertEqual(padded, [])
        reply, padded = cme.refusal_reply_fields(())
        self.assertEqual(reply, (0, 0, 0, 0, 0, float(cme.EVENT_STATUS_ILLEGAL)))
        self.assertEqual(padded, [1, 2, 3, 4, 5])
        reply, padded = cme.refusal_reply_fields((1, True, 0, 1, 0, 0.0))
        self.assertEqual(padded, [2])
        self.assertEqual(reply[1], 0)


class ChapterEventStoreTests(unittest.TestCase):
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
            sdk_identity="chapter-event-store",
        )
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="事件道友",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id=PROFILE_ID_V14,
        )
        return key, record

    def test_the_action_is_stored_once_and_a_second_sighting_is_idempotent(
        self,
    ) -> None:
        key, record = self._role()
        row, created = self.store.record_chapter_event_clear(
            record=record,
            chapter_id=1,
            map_index=36,
            event_cfg_id=812003,
            option_index=1,
            observed_at=SERVER_STARTED_UNIX + 5,
            request_sequence=187,
            reply_sequence=188,
        )
        self.assertTrue(created)
        self.assertEqual(row.replay_row(), (1, 13, 36))
        self.assertEqual(
            (row.chapter_id, row.map_index, row.event_cfg_id, row.option_index),
            (1, 36, 812003, 1),
        )
        self.assertEqual(
            (row.request_sequence, row.reply_sequence), (187, 188)
        )
        self.assertEqual(row.origin, CHAPTER_EVENT_ORIGIN_LOCAL_OBSERVATION)
        again, created_again = self.store.record_chapter_event_clear(
            record=record,
            chapter_id=1,
            map_index=36,
            event_cfg_id=812003,
            option_index=1,
            observed_at=SERVER_STARTED_UNIX + 90,
            request_sequence=300,
            reply_sequence=301,
        )
        self.assertFalse(created_again)
        self.assertEqual(again, row)
        self.assertEqual(
            self.store.chapter_event_clear(
                account_key=key, chapter_id=1, map_index=36
            ),
            row,
        )
        self.assertEqual(
            [item.map_index for item in self.store.chapter_event_clears(
                account_key=key
            )],
            [36],
        )

    def test_values_the_client_could_not_have_sent_are_refused(self) -> None:
        _, record = self._role()
        for kwargs in (
            {"chapter_id": 0, "map_index": 1, "event_cfg_id": 1, "option_index": 1},
            {"chapter_id": 1, "map_index": 0, "event_cfg_id": 1, "option_index": 1},
            {"chapter_id": 1, "map_index": 1, "event_cfg_id": 0, "option_index": 1},
            {"chapter_id": 1, "map_index": 1, "event_cfg_id": 1, "option_index": -1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(RoleStoreError):
                    self.store.record_chapter_event_clear(
                        record=record,
                        observed_at=SERVER_STARTED_UNIX,
                        request_sequence=1,
                        reply_sequence=2,
                        **kwargs,
                    )
        self.assertEqual(self.store.chapter_event_clears(account_key="x"), ())

    def test_a_row_not_attached_to_a_stored_role_is_never_written(self) -> None:
        _, record = self._role()
        with self.assertRaises(RoleStoreError):
            self.store.record_chapter_event_clear(
                record=dataclasses.replace(record, role_id=record.role_id + 1),
                chapter_id=1,
                map_index=36,
                event_cfg_id=812003,
                option_index=1,
                observed_at=SERVER_STARTED_UNIX,
                request_sequence=1,
                reply_sequence=2,
            )
        self.assertEqual(
            self.store.chapter_event_clears(account_key=record.account_key), ()
        )

    def test_a_version_9_archive_gains_the_table_and_keeps_every_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = RoleStore.at_directory(data_dir)
            try:
                key = store.account_key(
                    server_id=1,
                    platform="android",
                    channel_id="test",
                    channel_sub_id="0",
                    sdk_identity="chapter-event-migrate",
                )
                record, _ = store.create_or_get(
                    account_key=key,
                    server_id=1.0,
                    role_name="迁移事件道友",
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
                store.record_chapter_fight_clear(
                    record=record,
                    chapter_id=1,
                    fight_id=101010,
                    map_index=2,
                    observed_at=SERVER_STARTED_UNIX,
                    request_sequence=31,
                    settlement_sequence=57,
                    reply_sequence=58,
                )
            finally:
                store.close()
            archive = data_dir / STORE_FILENAME
            connection = sqlite3.connect(str(archive))
            try:
                connection.execute("DROP TABLE chapter_event_clears")
                connection.execute("DROP TABLE chapter_box_accepts")
                connection.execute("DROP TABLE chapter_event_reward_grants")
                connection.execute(
                    "UPDATE meta SET value = '9' WHERE key = 'schema_version'"
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
                    [row.fight_id for row in reopened.chapter_fight_clears(
                        account_key=key
                    )],
                    [101010],
                )
                self.assertEqual(
                    reopened.chapter_event_clears(account_key=key), ()
                )
            finally:
                reopened.close()


class ChapterMapEventSegmentTests(unittest.TestCase):
    """A real socket: one accepted action, then the next login replays its row."""

    def setUp(self) -> None:
        self.events: list[dict] = []
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RoleStore.at_directory(
            Path(self._tmp.name) / "roles",
            time_source=lambda: SERVER_STARTED_UNIX,
        )
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
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self._tmp.cleanup()

    def _account_key(self) -> str:
        return self.store.account_key(
            server_id=1.0,
            platform="android",
            channel_id="local-channel",
            channel_sub_id="0",
            sdk_identity="local-user",
        )

    def _login(self, *, create: bool) -> list:
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as stream:
            stream.settimeout(3)
            packets = encode_packet(
                C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
            ).wire
            if create:
                packets += encode_packet(
                    C2S_REGISTRY.by_id(2),
                    ("事件道友".encode("utf-8"), 1),
                    writer,
                ).wire
            stream.sendall(packets)
            _read_until(stream, self.server, buffer, reader, responses, 9)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 283)
        return responses

    def _send_event(self, fields=CHAPTER_EVENT_FIELDS) -> list:
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as stream:
            stream.settimeout(3)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
                ).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2),
                    ("事件道友".encode("utf-8"), 1),
                    writer,
                ).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 9)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 283)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(CHAPTER_EVENT_C2S_ID), fields, writer
                ).wire
                # The month-sign request is answered unconditionally, so a frame
                # that came BEFORE its S2C 1500 is a reply to the event; a
                # refused event must leave no such frame at all.
                + encode_packet(C2S_REGISTRY.by_id(725), (), writer).wire
            )
            _read_until(stream, self.server, buffer, reader, responses, 1500)
        return responses

    def _wait_event(self, name: str, count: int = 1) -> list:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            found = [event for event in self.events if event.get("event") == name]
            if len(found) >= count:
                return found
            time.sleep(0.01)
        return [event for event in self.events if event.get("event") == name]

    def test_one_request_one_reply_and_the_next_login_replays_the_row(self) -> None:
        self._login(create=True)
        responses = self._send_event()
        ids = [packet.spec.protocol_id for packet in responses]
        self.assertEqual(ids.count(CHAPTER_EVENT_S2C_ID), 1)
        reply = next(
            packet for packet in responses
            if packet.spec.protocol_id == CHAPTER_EVENT_S2C_ID
        )
        self.assertEqual(
            (reply.spec.name, reply.spec.fmt), ("chapterMapEvent", "pppppp")
        )
        self.assertEqual(tuple(reply.fields), (1, 36, 0, 1, 0, 1.0))

        replied = self._wait_event("tcp_chapter_event_replied")
        self.assertEqual(len(replied), 1)
        event = replied[0]
        self.assertEqual(event["reply_count"], 1)
        self.assertEqual(event["requests_answered"], 1)
        self.assertIs(event["repeated_request"], False)
        self.assertEqual(event["event_status"], 1)
        self.assertEqual(event["event_status_name"], "SUCCESS")
        self.assertEqual(event["lock_partner"], "chapterMapEvent")
        self.assertEqual(event["unlock_only_for"], "chapterMapEvent")
        self.assertEqual(event["client_unread_positions"], [1, 4, 5])
        self.assertIs(event["historical_values_recovered"], False)

        recorded = self._wait_event("tcp_chapter_event_clear_recorded")
        self.assertEqual(len(recorded), 1)
        record = recorded[0]
        self.assertIs(record["recorded"], True)
        self.assertEqual(record["chapter_event_chapter_id"], 1)
        self.assertEqual(record["chapter_event_map_index"], 36)
        self.assertEqual(record["chapter_event_cfg_id"], 812003)
        self.assertEqual(record["chapter_event_option_index"], 1)
        self.assertEqual(record["replay_row"], (1, 13, 36))
        self.assertEqual(record["evidence_kind"], CHAPTER_EVENT_EVIDENCE_CLIENT_ACTION)
        authority = record["reward_authority"]
        self.assertIs(authority["granted"], False)
        self.assertIs(authority["ledger_recorded"], True)
        self.assertIs(authority["ledger_created"], True)
        self.assertEqual(authority["ledger_total_after"], 10)
        self.assertIsNone(authority["crystal_bucket"])
        self.assertEqual(authority["bucket_provenance"], "unknown")
        self.assertIs(authority["applied_to_client_balance"], False)
        self.assertEqual(authority["client_replay"], "not_implemented")
        self.assertNotIn("wallet_total_after", authority)

        second = self._login(create=False)
        ids = [packet.spec.protocol_id for packet in second]
        self.assertEqual(ids.count(ITEM_INFO_ID), 1)
        info_index = ids.index(ITEM_INFO_ID)
        self.assertEqual(ids[info_index - 1], ITEM_START_ID)
        self.assertEqual(ids[info_index + 1], ITEM_END_ID)
        info = second[info_index]
        self.assertEqual(tuple(int(value) for value in info.fields), (1, 13, 36))
        replayed = self._wait_event("tcp_chapter_event_clear_replayed")
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0]["row_values"], (1, 13, 36))
        self.assertEqual(replayed[0]["row_format"], "ppp")
        self.assertEqual(replayed[0]["row_origin"], "deterministic_recovered")
        self.assertEqual(replayed[0]["value_origin"], "request_echo")
        self.assertEqual(
            replayed[0]["login_position"],
            "between_initChapterItemStart_and_End",
        )

    def test_the_ten_crystals_are_ledgered_but_never_shown_to_the_client(
        self,
    ) -> None:
        """TASK-0222: the reward authority is stored; the client balance is not moved.

        The S2C 10 vector, the updateCrystal channel and the gain-info family are
        all left untouched because the shipped reward row names no crystal
        bucket and the client keeps ``dCrystal`` equal to the six buckets' sum.
        """

        self._login(create=True)
        responses = self._send_event()
        ids = {packet.spec.protocol_id for packet in responses}
        self.assertFalse(ids & {81, 82, 83})
        self.assertNotIn(68, ids)  # updateCrystal (D-02: S2C 68), unregistered

        key = self._account_key()
        self.assertEqual(
            self.store.chapter_event_reward_ledger_total(account_key=key), 10
        )
        self.assertEqual(self.store.role_wallet_values(account_key=key), {})
        grants = self.store.chapter_event_reward_grants(account_key=key)
        self.assertEqual(len(grants), 1)
        self.assertEqual(
            (grants[0].reward_id, grants[0].crystal), (cme.REWARD_ID, 10)
        )
        self.assertIsNone(grants[0].crystal_bucket)
        self.assertEqual(grants[0].bucket_provenance, "unknown")

        # The next login's S2C 10 is the same zero vector as a fresh role's:
        # crystal 0 and all six buckets 0.
        second = self._login(create=False)
        login = next(
            packet for packet in second if packet.spec.protocol_id == 10
        )
        named = dict(zip(ROLE_INFO_FIELD_NAMES, login.fields, strict=True))
        self.assertEqual(named["crystal"], 0)
        for bucket in CRYSTAL_BUCKET_NAMES:
            self.assertEqual(named[bucket], 0)
        self.assertFalse(
            {packet.spec.protocol_id for packet in second} & {81, 82, 83}
        )
        self.assertNotIn(68, {packet.spec.protocol_id for packet in second})

        # A repeat of the same C2S 224 replays the stored ledger row.
        self._send_event()
        recorded = self._wait_event("tcp_chapter_event_clear_recorded", count=2)
        self.assertEqual([event["recorded"] for event in recorded], [True, False])
        replay_authority = recorded[1]["reward_authority"]
        self.assertIs(replay_authority["ledger_created"], False)
        self.assertEqual(replay_authority["ledger_total_after"], 10)
        self.assertIs(replay_authority["granted"], False)
        self.assertIs(replay_authority["applied_to_client_balance"], False)
        self.assertEqual(
            len(self.store.chapter_event_reward_grants(account_key=key)), 1
        )

    def test_a_server_restart_does_not_grant_the_event_reward_again(self) -> None:
        self._login(create=True)
        self._send_event()
        key = self._account_key()

        # Stop this process-equivalent and open the same archive in a new one.
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.events.clear()
        self.store = RoleStore.at_directory(
            Path(self._tmp.name) / "roles",
            time_source=lambda: SERVER_STARTED_UNIX,
        )
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
        self.port = self.server.server_address[1]

        self._login(create=False)
        self._send_event()
        recorded = self._wait_event("tcp_chapter_event_clear_recorded")
        self.assertEqual(len(recorded), 1)
        self.assertIs(recorded[0]["recorded"], False)
        authority = recorded[0]["reward_authority"]
        self.assertIs(authority["ledger_created"], False)
        self.assertEqual(authority["ledger_total_after"], 10)
        self.assertIs(authority["applied_to_client_balance"], False)
        self.assertEqual(
            len(self.store.chapter_event_reward_grants(account_key=key)), 1
        )
        self.assertEqual(
            self.store.chapter_event_reward_ledger_total(account_key=key), 10
        )

    def test_an_unknown_node_is_refused_by_name_and_still_answered_once(
        self,
    ) -> None:
        # TASK-0243 / D-186: the refusal is named, but the lock-send must still
        # be unlocked -- the client would otherwise close the socket after 15 s.
        self._login(create=True)
        fields = (1, 24, 0, 0, 0, 0.0)  # a real chapter-1 chest, not this slice
        responses = self._send_event(fields)
        replies = [
            packet for packet in responses
            if packet.spec.protocol_id == CHAPTER_EVENT_S2C_ID
        ]
        self.assertEqual(len(replies), 1)
        self.assertEqual(
            replies[0].fields, (1, 24, 0, 0, 0, float(cme.EVENT_STATUS_ILLEGAL))
        )
        self.assertNotEqual(replies[0].fields[5], float(cme.EVENT_STATUS_SUCCESS))
        refused = self._wait_event("tcp_chapter_event_refused")
        self.assertEqual(len(refused), 1)
        self.assertEqual(
            refused[0]["refusal"], cme.REFUSAL_UNKNOWN_NODE
        )
        self.assertIs(refused[0]["response_sent"], True)
        self.assertEqual(refused[0]["response_protocol_id"], CHAPTER_EVENT_S2C_ID)
        self.assertEqual(refused[0]["response_protocol_name"], "chapterMapEvent")
        self.assertEqual(refused[0]["response_fields"], list(replies[0].fields))
        self.assertEqual(refused[0]["event_status"], float(cme.EVENT_STATUS_ILLEGAL))
        self.assertEqual(refused[0]["event_status_name"], "ILLEGAL")
        self.assertEqual(
            refused[0]["event_status_provenance"], "synthetic_compatibility"
        )
        self.assertEqual(refused[0]["padded_positions"], [])
        self.assertEqual(refused[0]["echo_provenance"], "request_echo")
        self.assertIs(refused[0]["state_changed"], False)
        self.assertIs(refused[0]["reward_granted"], False)
        self.assertIs(refused[0]["row_recorded"], False)
        self.assertIs(refused[0]["success_claimed"], False)
        self.assertEqual(refused[0]["reply_count"], 1)
        self.assertIs(refused[0]["connection_kept_alive"], True)
        self.assertFalse(
            any(
                event.get("event") == "tcp_chapter_event_clear_recorded"
                for event in self.events
            )
        )

    def test_every_refusal_class_is_answered_exactly_once(self) -> None:
        # One representative per refusal code that a real client could actually
        # put on the wire: the registered format `NNNNNp` fixes the field count
        # and makes every value numeric, so the sync-count-type case is only
        # reachable from a direct caller (covered by the contract test above).
        cases = {
            (1, 36, 1, 1, 0, 0.0): cme.REFUSAL_CONSTANT_POSITION,
            (1, 36, 0, 1, 1, 0.0): cme.REFUSAL_CONSTANT_POSITION,
            (2, 36, 0, 1, 0, 0.0): cme.REFUSAL_UNKNOWN_NODE,
            (1, 36, 0, 2, 0, 0.0): cme.REFUSAL_UNKNOWN_OPTION,
        }
        self._login(create=True)
        for fields, expected in cases.items():
            with self.subTest(fields=fields):
                self.events.clear()
                responses = self._send_event(fields)
                replies = [
                    packet for packet in responses
                    if packet.spec.protocol_id == CHAPTER_EVENT_S2C_ID
                ]
                self.assertEqual(len(replies), 1)
                self.assertEqual(
                    replies[0].fields[-1], float(cme.EVENT_STATUS_ILLEGAL)
                )
                refused = self._wait_event("tcp_chapter_event_refused")
                self.assertEqual(len(refused), 1)
                self.assertEqual(refused[0]["refusal"], expected)
                self.assertIs(refused[0]["response_sent"], True)
                self.assertFalse(
                    any(
                        event.get("event") == "tcp_chapter_event_clear_recorded"
                        for event in self.events
                    )
                )


class ChapterMapEventV13NegativeTests(unittest.TestCase):
    """A v13 server has no item-replay policy: it answers but stores no row."""

    def test_a_v13_server_answers_and_stores_nothing(self) -> None:
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
                        ("事件道友".encode("utf-8"), 1),
                        writer,
                    ).wire
                )
                _read_until(stream, server, buffer, reader, responses, 9)
                stream.sendall(
                    encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
                )
                _read_until(stream, server, buffer, reader, responses, 283)
                stream.sendall(
                    encode_packet(
                        C2S_REGISTRY.by_id(CHAPTER_EVENT_C2S_ID),
                        CHAPTER_EVENT_FIELDS,
                        writer,
                    ).wire
                )
                _read_until(
                    stream, server, buffer, reader, responses,
                    CHAPTER_EVENT_S2C_ID,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        replied = [e for e in events if e.get("event") == "tcp_chapter_event_replied"]
        self.assertEqual(len(replied), 1)
        refused = [
            e for e in events
            if e.get("event") == "tcp_chapter_event_clear_not_recorded"
        ]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["reason"], "no_chapter_item_replay_policy")
        self.assertFalse(
            any(
                e.get("event") == "tcp_chapter_event_clear_recorded"
                for e in events
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
