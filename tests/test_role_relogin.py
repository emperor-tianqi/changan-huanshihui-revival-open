"""End-to-end role continuation over real loopback TCP.

The point of this module is the edge the revival could not cross before: a role
created once, the serving process gone, and the same account logging in again
must come back as *the same role* -- with no `noRole`, no second character and
no locally re-issued role ID.

Every archive here lives in its own temporary directory.  Nothing in this module
may read, write or delete a real per-user archive.
"""

from __future__ import annotations

import contextlib
import io
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from revival_stub.app import (
    DATA_DIR_ENV,
    ROLE_MODE_FRESH_TEST,
    ROLE_MODE_RESUME,
    build_parser,
    build_role_store,
    default_data_dir,
    run,
)
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub import dev_play_resources as dpr
from revival_stub.compatibility_profile import PROFILE_ID, get_profile_v3
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import (
    KIND_FRESH_TEST,
    KIND_PERSISTENT,
    SCHEMA_VERSION,
    STORE_FILENAME,
    RoleStore,
    RoleStoreUnusableError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


SERVER_STARTED_UNIX = 1_700_000_000

#: The recovered S2C ids of one complete first-time session, create included.
#: TASK-0092 / profile v5: the Team + fabao-select login snapshot, sent
#: immediately before S2C411 by the terminal login segment.
#: TASK-0094 / profile v6: ONE recharge-activity packet, sent after the
#: team block and before S2C411.
TEAM_SNAPSHOT_CHAIN = [36, 37, 48, 35]
#: TASK-0245 / profile v22: the client's own team power, right after S2C 35.
TEAM_MAX_FIGHT_CHAIN = [39]
RECHARGE_ACTIVITY_CHAIN = [1014]
CHAPTER_TASK_CHAIN = [385, 387, 386]  # v9 pair + TASK-0180 v15 row  # TASK-0104 / profile v9
CHAPTER_ITEM_CHAIN = [389, 391]  # TASK-0098 / profile v7
CHAPTER_MACHINE_CHAIN = [400, 402]  # TASK-0109 / profile v10 (after the item pair)
EXPERIENCE_TASK_CHAIN = [1307, 1309]  # TASK-0102 / profile v8
TERMINAL_CHAIN = [
    *TEAM_SNAPSHOT_CHAIN, *TEAM_MAX_FIGHT_CHAIN, *RECHARGE_ACTIVITY_CHAIN,
    *CHAPTER_TASK_CHAIN, *CHAPTER_ITEM_CHAIN, *CHAPTER_MACHINE_CHAIN,
    *EXPERIENCE_TASK_CHAIN,
]
#: TASK-0167 / profile v13: ONE prison-fight boss snapshot (S2C 1370), sent
#: after the S2C411/412 story state and immediately before S2C283.
PRISON_FIGHT_CHAIN = [1370]
#: TASK-0247 / profile v24: ONE inactive bitRabbit state (S2C 1435
#: syncBitRabbit), appended after S2C 555 returnTalentSoulSkill.
BIT_RABBIT_CHAIN = [1435]
NEW_ROLE_CHAIN = [5, 14, 8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, *TERMINAL_CHAIN, 411, *PRISON_FIGHT_CHAIN, 283, 555, *BIT_RABBIT_CHAIN, 13, 85]
#: The same chain minus S2C5 `noRole` and minus S2C14 `returnCreateRoleResult`:
#: the two packets that exist only because the client asked to create a role.
RESUMED_ROLE_CHAIN = [8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, *TERMINAL_CHAIN, 411, *PRISON_FIGHT_CHAIN, 283, 555, *BIT_RABBIT_CHAIN, 13, 85]

#: Index of each remembered packet inside its own chain.
NEW_SNAPSHOT_INDEX = NEW_ROLE_CHAIN.index(10)
RESUMED_SNAPSHOT_INDEX = RESUMED_ROLE_CHAIN.index(10)

#: How far the session has got after each phase, counted over the whole chain:
#: login answer + create answer + base segment, then the terminal index-40
#: segment, then the two responses to the post-scene C2S block.
#: TASK-0242 / profile v21 inserts the two empty mail-container packets after
#: S2C 10, so the base segment is two packets longer.
NEW_AFTER_CREATE = 7
NEW_AFTER_TERMINAL = 15 + len(TERMINAL_CHAIN) + len(PRISON_FIGHT_CHAIN)
NEW_COMPLETE = len(NEW_ROLE_CHAIN)
RESUMED_AFTER_LOGIN = 5
RESUMED_AFTER_TERMINAL = 13 + len(TERMINAL_CHAIN) + len(PRISON_FIGHT_CHAIN)
RESUMED_COMPLETE = len(RESUMED_ROLE_CHAIN)

ROLE_NAME = "千灯镇客人"
ACCOUNT = {
    "server_id": 1.0,
    "platform": "android",
    "channel_id": "local-channel",
    "channel_sub_id": "0",
    "sdk_identity": "local-user",
}


def login_fields(
    *,
    server_id: float = 1.0,
    sdk_user_id: bytes = b"local-user",
    device_id: bytes = b"local-device",
) -> list:
    fields = list(DEFAULT_LOGIN_FIELDS)
    fields[0] = server_id
    fields[1] = sdk_user_id
    fields[8] = device_id
    return fields


class _Session:
    """One client connection with its own rolling codec state."""

    def __init__(self, port: int) -> None:
        self.stream = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.stream.settimeout(5)
        self.writer = WriterState()
        self.reader = ReaderState()
        self.buffer = bytearray()
        self.responses: list = []

    def send(self, protocol_id: int, fields: tuple) -> None:
        packet = encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, self.writer)
        self.stream.sendall(packet.wire)

    def read(self, count: int) -> list:
        while len(self.responses) < count:
            try:
                packet = decode_packet(self.buffer, S2C_REGISTRY, self.reader)
            except NeedMoreData:
                chunk = self.stream.recv(65536)
                if not chunk:
                    raise AssertionError(
                        f"server closed after {len(self.responses)} response(s); "
                        f"ids={self.ids()}"
                    )
                self.buffer.extend(chunk)
                continue
            del self.buffer[: packet.consumed]
            self.responses.append(packet)
        return self.responses

    def read_until(self, protocol_id: int) -> list:
        """Read one more packet at a time until ``protocol_id`` has arrived."""

        while not any(p.spec.protocol_id == protocol_id for p in self.responses):
            self.read(len(self.responses) + 1)
        return self.responses

    def ids(self) -> list:
        return [packet.spec.protocol_id for packet in self.responses]

    def quiet_state(self) -> str:
        """``quiet`` (nothing arrived and the connection is open), else the reason."""

        self.stream.settimeout(0.2)
        try:
            chunk = self.stream.recv(1)
        except socket.timeout:
            return "quiet"
        finally:
            self.stream.settimeout(5)
        return "closed" if not chunk else "unexpected_data"

    def close(self) -> None:
        self.stream.close()


class RoleReloginTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"
        self.events: list[dict] = []
        self._running: list[tuple[RevivalTCPServer, threading.Thread]] = []
        self.clock_now = SERVER_STARTED_UNIX

    def tearDown(self) -> None:
        self._stop_all()
        self._tmp.cleanup()

    # ------------------------------------------------------------- lifecycle

    def _boot(
        self, store: RoleStore, *, profile=None, dev_play_resources: bool = False
    ) -> int:
        """Start one server instance on the given archive; return its port."""

        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: self.clock_now,
            role_store=store,
            compatibility_profile=profile,
            dev_play_resources=dev_play_resources,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._running.append((server, thread))
        return int(server.server_address[1])

    def _stop_all(self) -> None:
        for server, thread in self._running:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self._running.clear()

    def _restart(self, *, profile=None) -> tuple[RoleStore, int]:
        """Simulate a process restart: drop every instance, reopen the archive."""

        self._stop_all()
        store = RoleStore.at_directory(self.data_dir)
        return store, self._boot(store, profile=profile)

    def _key(self, store: RoleStore, **overrides) -> str:
        values = dict(ACCOUNT)
        values.update(overrides)
        return store.account_key(**values)

    # ---------------------------------------------------------------- flows

    def _drive_immediate_block(self, session: _Session) -> None:
        """The recovered post-scene C2S block and its two answers."""

        session.send(217, (12345.0, b"apk-md5"))
        session.send(4, (False,))
        session.send(22, ())
        session.send(37, ())
        session.send(39, ())
        session.send(579, (True,))

    def _create_role(self, session: _Session) -> list:
        """login -> noRole -> createRole -> full sync -> city entry."""

        session.send(0, login_fields())
        session.read(1)
        session.send(2, (ROLE_NAME.encode("utf-8"), 1))
        session.read(NEW_AFTER_CREATE)
        session.send(3, (1, False))
        session.read(NEW_AFTER_TERMINAL)
        self._drive_immediate_block(session)
        session.read(NEW_COMPLETE)
        session.send(25, ())
        session.send(26, ())
        return session.responses

    def _resume_role(self, session: _Session) -> list:
        session.send(0, login_fields())
        session.read(RESUMED_AFTER_LOGIN)
        session.send(3, (1, False))
        session.read(RESUMED_AFTER_TERMINAL)
        self._drive_immediate_block(session)
        session.read(RESUMED_COMPLETE)
        session.send(25, ())
        session.send(26, ())
        return session.responses

    # ------------------------------------------------------------ the edge

    def test_create_then_restart_then_resume_keeps_the_same_role(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        created = _Session(port)
        try:
            responses = self._create_role(created)
            self.assertEqual([p.spec.protocol_id for p in responses], NEW_ROLE_CHAIN)
            creation_snapshot = responses[NEW_SNAPSHOT_INDEX].fields
            self.assertEqual(responses[NEW_SNAPSHOT_INDEX].spec.name, "returnRoleInfo")
            role_id = creation_snapshot[1]
            self.assertEqual(creation_snapshot[2], ROLE_NAME.encode("utf-8"))
            self.assertEqual(creation_snapshot[3], 1)
            self.assertEqual(created.quiet_state(), "quiet")
        finally:
            created.close()
            store.close()

        # The serving process is gone; only the archive remains.
        store, port = self._restart()
        try:
            self.assertEqual(store.count(), 1)
            resumed = _Session(port)
            try:
                responses = self._resume_role(resumed)
                self.assertEqual(
                    [p.spec.protocol_id for p in responses], RESUMED_ROLE_CHAIN
                )
                self.assertNotIn(5, resumed.ids())
                self.assertNotIn(14, resumed.ids())
                snapshot = responses[RESUMED_SNAPSHOT_INDEX].fields
                self.assertEqual(snapshot[1], role_id)
                self.assertEqual(snapshot[:4], creation_snapshot[:4])
                self.assertEqual(snapshot[14], creation_snapshot[14])
                self.assertEqual(len(snapshot), len(creation_snapshot))
                # The session reached the main-city switch and stayed up.
                self.assertEqual(
                    responses[RESUMED_ROLE_CHAIN.index(283)].spec.name,
                    "returnSceneInfo",
                )
                self.assertEqual(responses[-2].spec.protocol_id, 13)
                self.assertEqual(responses[-1].spec.protocol_id, 85)
                self.assertEqual(resumed.quiet_state(), "quiet")
            finally:
                resumed.close()
            self.assertEqual(store.count(), 1)
        finally:
            store.close()

    def test_resumed_login_answers_the_sync_ack_and_reaches_the_city(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        first = _Session(port)
        try:
            self._create_role(first)
        finally:
            first.close()
            store.close()
        store, port = self._restart()
        try:
            session = _Session(port)
            try:
                session.send(0, login_fields())
                session.read(5)
                self.assertEqual(session.ids(), [8, 10, 255, 256, 9])
                self.assertEqual(session.responses[0].fields, (1, False))
                self.assertEqual(session.responses[4].fields, (1, False))
                self.assertEqual(session.responses[4].spec.name, "returnLoginInfoEnd")
                # The client's own recovered handler answers a non-terminal
                # index with C2S3, so the terminal segment is server-driven.
                session.send(3, (1, False))
                # The terminal segment now also carries the four-packet Team
                # snapshot, so the "through S2C283" prefix is chain-derived.
                through_scene = RESUMED_ROLE_CHAIN.index(283) + 1
                session.read(through_scene)
                self.assertEqual(session.ids(), RESUMED_ROLE_CHAIN[:through_scene])
                self.assertEqual(session.responses[5].fields, (40, False))
                self.assertEqual(session.responses[10].fields, (40, False))
                session.send(4, (False,))
                # TASK-0247: the terminal segment now also carries the one
                # S2C 1435 bitRabbit state after S2C 555, so this read is one
                # packet longer (through S2C 13 enterGameSuccess).
                session.read(through_scene + 3)
                self.assertEqual(
                    session.responses[RESUMED_ROLE_CHAIN.index(13)].spec.name,
                    "enterGameSuccess",
                )
                session.send(26, ())
                self.assertEqual(session.quiet_state(), "quiet")
            finally:
                session.close()
            self.assertEqual(store.count(), 1)
        finally:
            store.close()

    def test_a_resumed_session_emits_the_resume_provenance(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        first = _Session(port)
        try:
            self._create_role(first)
        finally:
            first.close()
            store.close()
        store, port = self._restart()
        try:
            created_events = [
                event
                for event in self.events
                if event.get("event") == "tcp_role_created"
            ]
            self.assertEqual(len(created_events), 1)
            self.assertEqual(
                created_events[0]["identity_origin"],
                "revival_local_create_role_transaction",
            )
            self.events.clear()
            session = _Session(port)
            try:
                self._resume_role(session)
            finally:
                session.close()
            resumed = [
                event for event in self.events if event.get("event") == "tcp_role_resumed"
            ]
            self.assertEqual(len(resumed), 1)
            event = resumed[0]
            self.assertEqual(event["role_origin"], "resumed_existing_binding")
            self.assertFalse(event["no_role_sent"])
            self.assertFalse(event["create_role_result_sent"])
            self.assertFalse(event["second_role_created"])
            self.assertFalse(event["stale_profile"])
            self.assertEqual(event["role_store_kind"], KIND_PERSISTENT)
            self.assertFalse(event["account_identity_is_authenticated_player_identity"])
            self.assertEqual(event["account_identity_policy"], "salted_local_derivation")
            # Only a short prefix of the salted key may reach the log.
            self.assertEqual(len(event["account_key_short"]), 8)
            self.assertNotIn("local-user", str(self.events))
            self.assertNotIn("local-device", str(self.events))
            initialized = [
                e for e in self.events if e.get("event") == "tcp_compat_role_initialized"
            ]
            self.assertEqual(len(initialized), 1)
            self.assertEqual(initialized[0]["role_origin"], "resumed_existing_binding")
            self.assertEqual(initialized[0]["profile_id"], PROFILE_ID)
        finally:
            store.close()

    def test_the_store_ready_event_states_the_honesty_boundary(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        try:
            self._boot(store)
        finally:
            store.close()
        ready = next(e for e in self.events if e.get("event") == "role_store_ready")
        self.assertEqual(ready["role_store_kind"], KIND_PERSISTENT)
        self.assertEqual(ready["role_store_schema_version"], SCHEMA_VERSION)
        self.assertEqual(ready["account_identity_policy"], "salted_local_derivation")
        self.assertFalse(ready["account_identity_is_authenticated_player_identity"])
        self.assertFalse(ready["sdk_token_verified"])
        self.assertEqual(ready["historical_role_records_migrated"], 0)
        # A brand-new archive was created, not upgraded: nothing was copied and
        # no pre-migration snapshot exists.
        self.assertFalse(ready["role_store_structure_migrated"])
        self.assertIsNone(ready["role_store_pre_migration_version"])
        self.assertIsNone(ready["role_store_migration_snapshot"])

    def test_an_unbound_account_on_the_same_archive_still_gets_no_role(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        first = _Session(port)
        try:
            self._create_role(first)
        finally:
            first.close()
        other = _Session(port)
        try:
            # Same device, different SDK identity: it must not inherit the role.
            other.send(0, login_fields(sdk_user_id=b"someone-else"))
            other.read(1)
            self.assertEqual(other.ids(), [5])
            self.assertEqual(other.responses[0].spec.name, "noRole")
            self.assertEqual(store.count(), 1)
        finally:
            other.close()
            store.close()

    def test_no_credential_from_a_real_login_reaches_the_archive(self) -> None:
        """The whole loginByToken payload, driven for real, leaves no secret."""

        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        session = _Session(port)
        try:
            self._create_role(session)
            self.assertEqual(store.count(), 1)
        finally:
            session.close()
            store.close()
        secrets_in = {
            "sdk_user_id": DEFAULT_LOGIN_FIELDS[1],
            "sdk_verify_md5": DEFAULT_LOGIN_FIELDS[2],
            "device_id": DEFAULT_LOGIN_FIELDS[8],
            "sdk_username": DEFAULT_LOGIN_FIELDS[9],
        }
        blob = b"".join(
            path.read_bytes() for path in self.data_dir.iterdir() if path.is_file()
        )
        for name, value in secrets_in.items():
            assert isinstance(value, bytes)
            self.assertNotIn(value, blob, f"{name} must not be at rest")
        # The role name is the client's own character name and is the one
        # string that must round-trip through the archive.
        self.assertIn(ROLE_NAME.encode("utf-8"), blob)
        # Neither does any of it reach the event log.
        self.assertNotIn("local-device", str(self.events))
        self.assertNotIn("local-token-md5", str(self.events))

    def test_a_login_without_any_identity_is_refused_not_answered(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        session = _Session(port)
        try:
            fields = login_fields()
            fields[1] = b""
            fields[9] = b""
            session.send(0, tuple(fields))
            self.assertEqual(session.quiet_state(), "closed")
        finally:
            session.close()
            store.close()
        self.assertEqual(
            [
                event.get("classification")
                for event in self.events
                if event.get("event") == "tcp_error"
            ],
            ["account_identity_missing"],
        )
        self.assertEqual(
            [e for e in self.events if e.get("event") == "tcp_packet_sent"], []
        )

    def test_a_role_is_scoped_to_one_server(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        first = _Session(port)
        try:
            self._create_role(first)
            first_role_id = first.responses[NEW_SNAPSHOT_INDEX].fields[1]
        finally:
            first.close()
        second_server = _Session(port)
        try:
            second_server.send(0, login_fields(server_id=2.0))
            second_server.read(1)
            self.assertEqual(second_server.ids(), [5])
            second_server.send(2, (ROLE_NAME.encode("utf-8"), 1))
            second_server.read(5)
            second_role_id = second_server.responses[NEW_SNAPSHOT_INDEX].fields[1]
            self.assertEqual(second_role_id % 100_000, 2)
            self.assertNotEqual(second_role_id, first_role_id)
            self.assertEqual(store.count(), 2)
        finally:
            second_server.close()
            store.close()

    def test_a_replayed_create_role_returns_the_existing_role(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        session = _Session(port)
        try:
            session.send(0, login_fields())
            session.read(1)
            self.assertEqual(session.ids(), [5])
            # The account becomes bound between the noRole answer and the
            # createRole request (a replay, or a second device).
            existing, created = store.create_or_get(
                account_key=self._key(store),
                server_id=1.0,
                role_name="bound-elsewhere",
                career=1,
                create_time=SERVER_STARTED_UNIX,
                compatibility_profile_id=PROFILE_ID,
            )
            self.assertTrue(created)
            session.send(2, (ROLE_NAME.encode("utf-8"), 1))
            session.read(5)
            self.assertEqual(session.responses[1].fields, (True,))
            snapshot = session.responses[NEW_SNAPSHOT_INDEX].fields
            self.assertEqual(snapshot[1], existing.role_id)
            self.assertEqual(snapshot[2], b"bound-elsewhere")
            self.assertEqual(store.count(), 1)
        finally:
            session.close()
            store.close()
        absorbed = [
            event
            for event in self.events
            if event.get("event") == "tcp_create_role_replay_absorbed"
        ]
        self.assertEqual(len(absorbed), 1)
        self.assertTrue(absorbed[0]["role_id_reused"])
        self.assertFalse(absorbed[0]["second_role_created"])
        self.assertEqual(
            [
                event
                for event in self.events
                if event.get("event") == "tcp_role_created"
            ],
            [],
        )

    def test_a_lone_story_finish_is_recorded_but_never_written_to_the_archive(
        self,
    ) -> None:
        # D-53 §1 (kept by TASK-0073): a lone `C2S233(1008)` is the client
        # *opening* story 1008's dialogue, so it is recorded and forgotten.  Only
        # the recovered 1008 -> 1100 pair is completion evidence, and its own
        # tests live in `test_story_completion.py`.
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        session = _Session(port)
        try:
            self._create_role(session)
            key = self._key(store)
            before = store.lookup(key)
            session.send(233, (1008,))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("event") == "tcp_story_finish_recorded"
                for event in self.events
            ):
                time.sleep(0.01)
            finish = next(
                event
                for event in self.events
                if event.get("event") == "tcp_story_finish_recorded"
            )
            self.assertEqual(finish["story_id"], 1008)
            self.assertFalse(finish["response_sent"])
            self.assertEqual(finish["persistence_policy"], "record_only_no_persistence")
            self.assertEqual(store.lookup(key), before)
            self.assertEqual(store.count(), 1)
        finally:
            session.close()
            store.close()

    def test_a_stale_compatibility_profile_never_moves_the_role_identity(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        first = _Session(port)
        try:
            self._create_role(first)
            created_role_id = first.responses[NEW_SNAPSHOT_INDEX].fields[1]
        finally:
            first.close()
            store.close()
        self.events.clear()
        store, port = self._restart(profile=get_profile_v3())
        try:
            session = _Session(port)
            try:
                session.send(0, login_fields())
                session.read(3)
                snapshot = session.responses[RESUMED_SNAPSHOT_INDEX].fields
                self.assertEqual(snapshot[1], created_role_id)
                self.assertEqual(snapshot[2], ROLE_NAME.encode("utf-8"))
                self.assertEqual(snapshot[3], 1)
            finally:
                session.close()
            resumed = next(
                event for event in self.events if event.get("event") == "tcp_role_resumed"
            )
            self.assertTrue(resumed["stale_profile"])
            self.assertEqual(resumed["creation_compatibility_profile_id"], PROFILE_ID)
            initialized = next(
                e for e in self.events if e.get("event") == "tcp_compat_role_initialized"
            )
            self.assertEqual(initialized["profile_id"], get_profile_v3().profile_id)
            self.assertEqual(store.count(), 1)
        finally:
            store.close()

    def test_a_restarted_process_does_not_reuse_a_role_id(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        first = _Session(port)
        try:
            self._create_role(first)
            created_role_id = first.responses[NEW_SNAPSHOT_INDEX].fields[1]
        finally:
            first.close()
            store.close()
        store, port = self._restart()
        try:
            # A different SDK identity on the same server must receive a *new*
            # ID; before this change both processes started at the same serial.
            session = _Session(port)
            try:
                session.send(0, login_fields(sdk_user_id=b"second-player"))
                session.read(1)
                self.assertEqual(session.ids(), [5])
                session.send(2, (b"second-role", 1))
                session.read(5)
                self.assertNotEqual(
                    session.responses[NEW_SNAPSHOT_INDEX].fields[1], created_role_id
                )
            finally:
                session.close()
            self.assertEqual(store.count(), 2)
        finally:
            store.close()

    def test_a_dev_draw_spends_the_ticket_ledger_and_a_restart_keeps_it(self) -> None:
        """TASK-0235: the spent 祈灵券 survive a full process restart."""

        def dev_store() -> RoleStore:
            store = RoleStore.at_directory(self.data_dir)
            store._profile_fields = {
                "play_profile_id": dpr.DEV_PLAY_RESOURCES_PROFILE_ID
            }
            return store

        store = dev_store()
        dpr.seed_overlay(
            store, profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        )
        dpr.seed_ticket_balance(
            store, profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        )
        port = self._boot(store, dev_play_resources=True)
        first = _Session(port)
        try:
            self._create_role(first)
            initial = next(p for p in first.responses if p.spec.protocol_id == 27)
            self.assertEqual((initial.fields[18], initial.fields[36]), (30, 30))
            first.send(274, (10, 0))
            drawn = first.read_until(24)
            values = [p.fields for p in drawn if p.spec.protocol_id == 24]
            self.assertEqual(values, [(252, 20)])
        finally:
            first.close()
            store.close()

        # The serving process is gone; only the archive remains.
        store = dev_store()
        port = self._boot(store, dev_play_resources=True)
        try:
            resumed = _Session(port)
            try:
                self._resume_role(resumed)
                snapshot = next(
                    p for p in resumed.responses if p.spec.protocol_id == 27
                )
                self.assertEqual((snapshot.fields[18], snapshot.fields[36]), (20, 30))
                resumed.send(274, (10, 0))
                drawn = resumed.read_until(24)
                self.assertEqual(
                    [p.fields for p in drawn if p.spec.protocol_id == 24],
                    [(252, 10)],
                )
            finally:
                resumed.close()
            self.assertEqual(store.count(), 1)
        finally:
            store.close()


class RoleModeSelectionTests(unittest.TestCase):
    """The typed mode, not a task title, decides which archive is served."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"
        self._env = dict(os.environ)
        # No test in this module may open, create or remove the archive a real
        # run would use, so the resolved default is redirected at its own
        # documented override.
        os.environ[DATA_DIR_ENV] = str(Path(self._tmp.name) / "user-data")
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_default_mode_is_resume_against_the_persistent_directory(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.role_mode, ROLE_MODE_RESUME)
        self.assertIsNone(args.data_dir)
        store = build_role_store(args)
        try:
            self.assertEqual(store.kind, KIND_PERSISTENT)
            self.assertEqual(
                Path(store._location), default_data_dir() / STORE_FILENAME
            )
        finally:
            store.close()

    def test_an_explicit_data_dir_is_honoured(self) -> None:
        args = build_parser().parse_args(["--data-dir", str(self.data_dir)])
        store = build_role_store(args)
        try:
            self.assertEqual(Path(store._location), self.data_dir / STORE_FILENAME)
            self.assertTrue(Path(store._location).is_file())
        finally:
            store.close()

    def test_fresh_role_test_gets_a_throwaway_archive(self) -> None:
        args = build_parser().parse_args(["--role-mode", ROLE_MODE_FRESH_TEST])
        store = build_role_store(args)
        location = Path(store._location)
        try:
            self.assertEqual(store.kind, KIND_FRESH_TEST)
            # The throwaway archive is nowhere near the long-lived one.
            self.assertNotIn(default_data_dir(), location.parents)
            self.assertFalse(
                (default_data_dir() / STORE_FILENAME).exists(),
                "a fresh-role test must not create the durable archive",
            )
        finally:
            store.close()
        self.assertFalse(location.parent.exists())

    def test_fresh_role_test_refuses_an_explicit_data_dir(self) -> None:
        args = build_parser().parse_args(
            ["--role-mode", ROLE_MODE_FRESH_TEST, "--data-dir", str(self.data_dir)]
        )
        with self.assertRaises(SystemExit):
            build_role_store(args)

    def test_an_unknown_role_mode_is_refused_before_anything_opens(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["--role-mode", "fresh_role_test_guess"])

    def test_a_damaged_archive_stops_the_launcher_before_a_socket_opens(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        archive = self.data_dir / STORE_FILENAME
        archive.write_bytes(b"\x00" * 64)
        args = build_parser().parse_args(
            ["--data-dir", str(self.data_dir), "--http-port", "0", "--tcp-port", "0"]
        )
        with self.assertRaises(RoleStoreUnusableError):
            build_role_store(args)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                run(args)
        self.assertIn("refusing to start", stderr.getvalue())
        self.assertEqual(archive.read_bytes(), b"\x00" * 64)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
