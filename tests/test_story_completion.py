"""TASK-0073 — the durable Story 1008 completion and its login replay.

`C2S 233 finishStory(X)` is the client *opening* X's dialogue (D-53 §1), so it is
never a completion on its own.  The one piece of positive evidence this build
accepts is the ordered pair the recovered close callback produces: 1008's own
`closeCallback` is the only caller of `pkgStoryTrigger_1100.triggerStory()` in
the whole corpus, so a `C2S233(1100)` that directly follows a `C2S233(1008)` in
one session means 1008's dialogue was created and then destroyed.

This module tests the three layers that fact travels through:

* the recovered client's own reading of the row we replay (a model pinned to the
  decompiled Lua, driven by the bytes the server really sent);
* the archive: what is stored, what is refused, and what a structural upgrade
  from the older archive does to a player's role;
* the product edge: a named play profile, two real server restarts, and a login
  stream that stays byte-identical when nothing has been completed.

Every archive here lives in its own temporary directory.  Nothing in this module
may read, write or delete a real per-user archive.
"""

from __future__ import annotations

import hashlib
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from revival_stub import app as app_module
from revival_stub.app import (
    DATA_DIR_ENV,
    ROLE_MODE_PLAY_PROFILE,
    build_parser,
    build_role_store,
)
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import PROFILE_ID as COMPATIBILITY_PROFILE_ID
from revival_stub.eventlog import EventLogger
from revival_stub.play_profile import (
    PROFILE_MODE_INIT,
    PROFILE_MODE_RESUME,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_state import STORY_STATE_FINISH
from revival_stub.role_store import (
    STORE_FILENAME,
    STORY_ORIGIN_LOCAL_OBSERVATION,
    RoleStore,
    RoleStoreError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import (
    RevivalTCPServer,
    STORY_COMPLETION_REFUSAL_ARCHIVE,
    STORY_COMPLETION_REFUSAL_NOT_A_PAIR,
    STORY_COMPLETION_REFUSAL_NO_PREDECESSOR,
    STORY_COMPLETION_REFUSAL_ROLE_CHANGED,
    STORY_COMPLETION_REFUSAL_UNBOUND_ROLE,
    STORY_EVIDENCE_ORDERED_SUCCESSOR,
    STORY_REPLAY_REFUSAL_NOT_REPLAYABLE,
    STORY_REPLAY_REFUSAL_ROLE_MISMATCH,
    STORY_REPLAY_REFUSAL_STATE,
    STORY_REPLAY_REFUSAL_TIME,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORENSICS = PROJECT_ROOT / "max_decoder" / "forensics_story_authority"

FIXED_NOW = 1_700_000_000
PROFILE_ID = "story-save"
ROLE_NAME = "灯下客"
ACCOUNT = {
    "server_id": 1.0,
    "platform": "android",
    "channel_id": "local-channel",
    "channel_sub_id": "0",
    "sdk_identity": "local-user",
}

#: The recovered S2C ids of one complete create session and of the resumed one
#: that follows it, with no story completion stored: byte-for-byte the chains
#: `test_role_relogin.py` and `test_play_profile.py` already pin.
#: TASK-0092 / profile v5: the Team + fabao-select login snapshot, sent
#: immediately before S2C411 by the terminal login segment.
TEAM_SNAPSHOT_CHAIN = [36, 37, 48, 35]
#: TASK-0245 / profile v22: the client's own team power, right after S2C 35.
TEAM_MAX_FIGHT_CHAIN = [39]
#: TASK-0094 / profile v6: ONE recharge-activity packet, sent after the team
#: block and before S2C411.  It carries the client's own initRechargeStart()
#: reset and no per-task, reward or claim row.
RECHARGE_ACTIVITY_CHAIN = [1014]
#: TASK-0104 / profile v9: the chapter-task pair, sent after the recharge packet
#: and immediately before the chapter-item pair.
CHAPTER_TASK_CHAIN = [385, 387, 386]  # v9 pair + TASK-0180 v15 row
#: TASK-0098 / profile v7: the chapter-item pair, sent after the chapter-task pair
#: and before S2C411.
CHAPTER_ITEM_CHAIN = [389, 391]
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
RESUMED_ROLE_CHAIN = [8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, *TERMINAL_CHAIN, 411, *PRISON_FIGHT_CHAIN, 283, 555, *BIT_RABBIT_CHAIN, 13, 85]
#: The same resumed session of an archive that *has* completed 1008: one extra
#: S2C412 between the snapshot and the scene packet, and nothing else moves.
COMPLETED_RESUMED_CHAIN = [8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, *TERMINAL_CHAIN, 411, 412, *PRISON_FIGHT_CHAIN, 283, 555, *BIT_RABBIT_CHAIN, 13, 85]

#: TASK-0242 / profile v21 inserts the two empty mail-container packets after
#: S2C 10, so the base segment is two packets longer.
NEW_AFTER_CREATE = 7
NEW_AFTER_TERMINAL = 15 + len(TERMINAL_CHAIN) + len(PRISON_FIGHT_CHAIN)
NEW_COMPLETE = len(NEW_ROLE_CHAIN)
RESUMED_AFTER_LOGIN = 5
RESUMED_AFTER_TERMINAL = 13 + len(TERMINAL_CHAIN) + len(PRISON_FIGHT_CHAIN)
NEW_SNAPSHOT_INDEX = NEW_ROLE_CHAIN.index(10)
RESUMED_SNAPSHOT_INDEX = RESUMED_ROLE_CHAIN.index(10)

#: The server's own clock when the completion evidence is observed.  The row's
#: ``observed_at`` and therefore S2C412's ``dTime`` come from here, never from a
#: reconstructed historical completion time.
EVIDENCE_NOW = 1_700_000_500

#: Frozen hashes of the whole resumed login response stream over the
#: deterministic fixture of this module (fixed clock, first role of a fresh
#: archive, `local-user` on server 1).  The first is the ACTIVE profile's stream
#: for an archive with no completion -- it must not drift from here on; the
#: second is the same stream with the one replayed row inserted.
#:
#: Re-frozen seven times: TASK-0092 inserted the four-packet Team snapshot into
#: the terminal login segment, TASK-0094 inserted the single recharge-activity
#: packet after it, TASK-0098 inserted the chapter-item pair after that,
#: TASK-0102 the experience-task pair after that, TASK-0104 the chapter-task
#: pair before the chapter-item pair, TASK-0109 the chapter-machine pair
#: after the chapter-item pair, and TASK-0167 the v13 prison-fight snapshot
#: between S2C411/412 and S2C283.
#: Each insertion is itself pinned, packet by packet, by
#: ``test_tcp_integration``, ``test_team_login_snapshot``,
#: ``test_recharge_activity_login_state``, ``test_chapter_item_init``,
#: ``test_experience_task_init``, ``test_chapter_task_init``,
#: ``test_chapter_machine_init``, ``test_prison_fight_snapshot`` and
#: ``test_drawcard_unlock``, and the
#: additivity itself is measured on two real servers' bytes
#: (``test_the_v10_stream_is_the_v9_stream_plus_one_pair``).  The
#: pre-TASK-0092 v4 values were
#: ``c2eda454...`` and ``68253efa...``, and a v4-profile server still produces
#: exactly them (``test_v4_profile_transaction_replays_v4_wire``); the v5 values
#: were ``eb3e3dcaf7cc98db...`` and ``8404f5102f210077...``, and a v5-profile
#: server still produces exactly those (``test_v5_profile_transaction``).  The
#: pre-v13 values were
#: ``9cff151b560b8e2f4bc47b07767ff6638dd3569656101e5afeecea40e0428fb7`` and
#: ``3a8c0644a91d100a483fab2b4a618d3b12103ec476570169a61c2b48b36ffdb5``.
#: TASK-0242 / profile v21 re-froze these values: the base segment now carries
#: the two empty mail-container packets after S2C 10.  The pre-v21 values
#: (measured at base commit 3f5ad48) were
#: ``b5b1818cc4868bbbbe2f191c00c049e4cbde7f214c5162af8e2b5ce0131d5741`` and
#: ``612b9a307f5e5668df0bfe4b8d0bd9d4f9a1929728ce2206c87a12b01125bcf9``.
#: TASK-0245 / profile v22 re-froze them again: the terminal segment now carries
#: the one S2C 39 returnTeamMaxFight after S2C 35.  The pre-v22 values (measured
#: at base commit 5088f79) were
#: ``bd88f8284fa995de6d0e893cd01cf052be03d43c570f42da1d27b84313cf5580`` and
#: ``6a670df8b83924eca15afea9669b4ca14e326f21abb29fc751c4bff23f0b5eae``.
#: TASK-0247 / profile v24 re-froze them again: the terminal segment now carries
#: the one S2C 1435 syncBitRabbit inactive state after S2C 555.  The pre-v24
#: values (measured at base commit 976f62d) were
#: ``56fd536e7d90b149118396d0068e9a2b0ef12ac79119cc8af7a2dbb0f06f6d87`` and
#: ``44fe5ef63f2ab985c137acb64f4d568ad465d2945c98abf85a22e5e99730577c``.
FROZEN_RESUME_SHA256 = (
    "6f2e3799cd683a469c7fa9a5b0e14cca670f9151a98e8047c8cb0608dd140b4c"
)
FROZEN_COMPLETED_RESUME_SHA256 = (
    "ceafd58a85aa174ae47818de25010916eb08c43cb6525415736a1adac0650b1f"
)


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


def sha256_wire(packets) -> str:
    return hashlib.sha256(b"".join(packet.wire for packet in packets)).hexdigest()


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

    def ids(self) -> list:
        return [packet.spec.protocol_id for packet in self.responses]

    def quiet_state(self) -> str:
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


# --------------------------------------------------------------------- model


class RecoveredClientStoryModel:
    """The recovered client's story state, as the *wire* drives it.

    Every method mirrors one decompiled function verbatim; the test class below
    pins each of them back to the recovered source so the model cannot drift
    away from the client it claims to be.
    """

    NPC_ID_1008 = 8
    STORY_ID_1008 = 1008

    def __init__(self, *, is_need_guide: bool | None = None) -> None:
        # pkgStoryModelMgr.le_story_list starts nil; only S2C411 creates it.
        self.le_story_list: dict | None = None
        self.is_need_guide = is_need_guide

    # -- pkgStoryModelMgr ---------------------------------------------------
    def return_story_start(self) -> None:
        self.le_story_list = {}

    def return_story(self, d_story_id, d_state, d_time) -> None:
        assert self.le_story_list is not None, (
            "s2c.returnStory indexed a nil le_story_list: S2C412 without S2C411"
        )
        single = self.le_story_list.get(d_story_id)
        if single is None:
            single = {}
            self.le_story_list[d_story_id] = single
        single["dTime"] = d_time
        single["dState"] = d_state
        single["dStoryId"] = d_story_id

    def get_story_data(self, d_story_id):
        # Indexing a nil table is the client's own error; indexing an existing
        # table with an absent key is simply nil.
        assert self.le_story_list is not None, (
            "getStoryData indexed a nil le_story_list: S2C412 without S2C411"
        )
        return self.le_story_list.get(d_story_id)

    def check_story_finish(self, d_story_id) -> bool:
        if self.is_need_guide is not None and self.is_need_guide is False:
            return True
        single = self.get_story_data(d_story_id)
        return single is not None and single["dState"] == STORY_STATE_FINISH

    # -- pkgStoryTrigger_1008 ----------------------------------------------
    def is_trigger_story_1008(
        self, d_npc_id, *, talk_list_available: bool = True
    ) -> bool:
        """The recovered gate that decides whether the first CG plays at all."""

        if d_npc_id != self.NPC_ID_1008:
            return False
        if not talk_list_available:
            return False
        if self.check_story_finish(self.STORY_ID_1008):
            return False
        return True

    # -- pkgStoryNcMgr.s2c --------------------------------------------------
    def apply(self, packets) -> None:
        """Feed real S2C packets through the recovered handlers."""

        for packet in packets:
            if packet.spec.protocol_id == 411:
                self.return_story_start()
            elif packet.spec.protocol_id == 412:
                self.return_story(*packet.fields)
            elif packet.spec.protocol_id == 413:
                # `function s2c.returnStoryEnd(tbSocket) end` -- an empty body.
                pass


class RecoveredClientStoryModelTests(unittest.TestCase):
    """The model says what the recovered client says, and it says it still."""

    @staticmethod
    def _source(name: str) -> str:
        path = FORENSICS / name
        if not path.is_file():  # pragma: no cover - the files are committed
            raise unittest.SkipTest(f"recovered source missing: {path}")
        return path.read_text(encoding="utf-8")

    def test_the_model_mirrors_the_recovered_predicates(self) -> None:
        model = self._source("luaScript__story__pkgStoryModelMgr.lua")
        # returnStoryStart: the empty table is the client's own "nothing done".
        self.assertIn("le_story_list = {}", model)
        # returnStory: the three fields, and the row created on demand.
        self.assertIn("tbSingleStory.dTime = dTime", model)
        self.assertIn("tbSingleStory.dState = dState", model)
        self.assertIn("tbSingleStory.dStoryId = dStoryId", model)
        self.assertIn("le_story_list[dStoryId] = tbSingleStory", model)
        # getStoryData: the unguarded reader, so a nil container is a real error.
        self.assertIn("return le_story_list[dStoryId]", model)
        # checkStoryFinish: the kill switch, then the state comparison.
        self.assertIn(
            "if tbSingleStory and tbSingleStory.dState == "
            "pkgGuideConst.STATE_FINISH then",
            model,
        )
        # updateStoryStatusByClient: the client's own local FINISH write, with
        # the same state value this server replays.
        self.assertIn(
            "returnStory(dStoryId, pkgGuideConst.STATE_FINISH, os.time())", model
        )

        trigger = self._source("luaScript__story__trigger__pkgStoryTrigger_1008.lua")
        self.assertIn("NPC_ID = 8", trigger)
        self.assertIn("pkgStoryConst.STORY_ID_1008", trigger)
        self.assertIn("if pkgStoryModelMgr.checkStoryFinish(le_stroy_id) then", trigger)
        self.assertIn("return false", trigger)
        # …and the chain the completion evidence rests on.
        self.assertIn("pkgStoryTrigger_1100.triggerStory()", trigger)

        const = self._source("zys_scAll__const__pkgGuideConst.lua")
        self.assertIn(f"STATE_FINISH = {STORY_STATE_FINISH}", const)

    def test_the_first_cg_plays_without_a_completion_row(self) -> None:
        model = RecoveredClientStoryModel()
        model.apply(_story_snapshot_packets())
        self.assertEqual(model.le_story_list, {})
        self.assertFalse(model.check_story_finish(1008))
        # The empty snapshot leaves story 1008 armed: this is the RUN-0014 state.
        self.assertTrue(model.is_trigger_story_1008(8))
        # …for NPC 8 only, and only while the config has a talk list.
        self.assertFalse(model.is_trigger_story_1008(1))

    def test_a_replayed_completion_row_stops_the_first_cg(self) -> None:
        model = RecoveredClientStoryModel()
        model.apply(_story_snapshot_packets(completed_at=FIXED_NOW))
        self.assertEqual(
            model.le_story_list[1008],
            {"dTime": FIXED_NOW, "dState": STORY_STATE_FINISH, "dStoryId": 1008},
        )
        self.assertTrue(model.check_story_finish(1008))
        # The whole point: the NPC-8 model-load trigger can no longer open 1008.
        self.assertFalse(model.is_trigger_story_1008(8))

    def test_the_kill_switch_and_the_container_contract_stay_visible(self) -> None:
        # `_isNeedGuide == false` short-circuits everything to "finished".
        killed = RecoveredClientStoryModel(is_need_guide=False)
        killed.apply(_story_snapshot_packets())
        self.assertTrue(killed.check_story_finish(1008))
        # A completion row for another story does not finish 1008.
        other = RecoveredClientStoryModel()
        other.apply(_story_snapshot_packets(rows=((1100, STORY_STATE_FINISH, 1),)))
        self.assertFalse(other.check_story_finish(1008))
        self.assertTrue(other.is_trigger_story_1008(8))
        # S2C412 without the snapshot would index a nil container, which is the
        # client's own error -- the server must never emit that pair.
        bare = RecoveredClientStoryModel()
        with self.assertRaises(AssertionError):
            bare.apply(_story_snapshot_packets(rows=((1008, 2, 1),), snapshot=False))


def _story_snapshot_packets(
    *, completed_at: int | None = None, rows=(), snapshot: bool = True
):
    """Real S2C411/412 packets, encoded by the runtime codec."""

    writer = WriterState()
    packets = []
    if snapshot:
        packets.append(
            encode_packet(S2C_REGISTRY.by_id(411), (), writer)
        )
    if completed_at is not None:
        rows = rows + ((1008, STORY_STATE_FINISH, completed_at),)
    for story_id, state, d_time in rows:
        packets.append(
            encode_packet(S2C_REGISTRY.by_id(412), (story_id, state, d_time), writer)
        )
    return packets


# ------------------------------------------------------------- the wire side


class _StoryServerCase(unittest.TestCase):
    """A real loopback server over its own temporary archive."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"
        self.events: list[dict] = []
        self._running: list[tuple[RevivalTCPServer, threading.Thread]] = []
        self.clock_now = FIXED_NOW

    def tearDown(self) -> None:
        self._stop_all()
        self._tmp.cleanup()

    def _boot(self, store: RoleStore) -> int:
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: self.clock_now,
            role_store=store,
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

    def _restart(self) -> tuple[RoleStore, int]:
        self._stop_all()
        store = RoleStore.at_directory(self.data_dir)
        return store, self._boot(store)

    def _key(self, store: RoleStore) -> str:
        return store.account_key(**ACCOUNT)

    def _drive_immediate_block(self, session: _Session) -> None:
        session.send(217, (12345.0, b"apk-md5"))
        session.send(4, (False,))
        session.send(22, ())
        session.send(37, ())
        session.send(39, ())
        session.send(579, (True,))

    def _create_role(self, session: _Session) -> list:
        session.send(0, login_fields())
        session.read(1)
        session.send(2, (ROLE_NAME.encode("utf-8"), 1))
        session.read(NEW_AFTER_CREATE)
        session.send(3, (1, False))
        session.read(NEW_AFTER_TERMINAL)
        self._drive_immediate_block(session)
        session.read(NEW_COMPLETE)
        return session.responses

    def _resume_role(self, session: _Session, chain=RESUMED_ROLE_CHAIN) -> list:
        session.send(0, login_fields())
        session.read(RESUMED_AFTER_LOGIN)
        session.send(3, (1, False))
        session.read(RESUMED_AFTER_TERMINAL)
        self._drive_immediate_block(session)
        session.read(len(chain))
        return session.responses

    def _send_stories(self, session: _Session, *story_ids: int) -> None:
        """Send the ids and wait until the server has recorded every one."""

        before = len(self._finish_events())
        for story_id in story_ids:
            session.send(233, (story_id,))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if len(self._finish_events()) >= before + len(story_ids):
                return
            time.sleep(0.01)
        raise AssertionError("the server did not record every C2S 233")

    def _finish_events(self) -> list[dict]:
        return [
            event
            for event in self.events
            if event.get("event") == "tcp_story_finish_recorded"
        ]


class StoryCompletionEvidenceTests(_StoryServerCase):
    """What does and does not become a durable completion."""

    def setUp(self) -> None:
        super().setUp()
        self.store = RoleStore.at_directory(self.data_dir)
        self.port = self._boot(self.store)
        self.session = _Session(self.port)
        self._create_role(self.session)
        self.key = self._key(self.store)

    def tearDown(self) -> None:
        self.session.close()
        self.store.close()
        super().tearDown()

    def _stored(self):
        return self.store.story_completions(account_key=self.key)

    def test_the_recovered_pair_records_one_completion(self) -> None:
        self.clock_now = EVIDENCE_NOW
        self._send_stories(self.session, 1008, 1100)
        rows = self._stored()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.story_id, 1008)
        self.assertEqual(row.state, STORY_STATE_FINISH)
        self.assertEqual(row.observed_at, EVIDENCE_NOW)
        self.assertEqual(row.evidence_kind, STORY_EVIDENCE_ORDERED_SUCCESSOR)
        self.assertEqual(row.evidence_story_id, 1100)
        self.assertEqual(row.origin, STORY_ORIGIN_LOCAL_OBSERVATION)
        self.assertEqual(row.account_key, self.key)
        role = self.store.lookup(self.key)
        self.assertEqual(row.role_id, role.role_id)
        self.assertEqual(row.server_id, role.server_id)
        # The two sequences are the raw observation.  Nothing was sent between
        # the two ids here, so they are also adjacent packets -- the next test
        # shows the evidence does not require that.
        self.assertEqual(row.successor_sequence, row.predecessor_sequence + 1)
        first, second = self._finish_events()
        self.assertEqual(
            first["story_completion_refusal"], STORY_COMPLETION_REFUSAL_NO_PREDECESSOR
        )
        self.assertIs(second["story_completion_recorded"], True)
        self.assertIs(second["server_story_state_written"], True)
        self.assertIs(second["historical_completion_time_recovered"], False)
        # The client got no reply to either packet and the session stayed up.
        self.assertEqual(self.session.ids(), NEW_ROLE_CHAIN)
        self.assertEqual(self.session.quiet_state(), "quiet")

    def test_traffic_between_the_pair_does_not_break_the_evidence(self) -> None:
        """The real client walks to the next NPC (and keeps heartbeating) first."""

        self.clock_now = EVIDENCE_NOW
        self.session.send(233, (1008,))
        self.session.send(25, ())  # onLine
        self.session.send(26, ())  # onLine_forNetState
        self.session.send(23, (float(EVIDENCE_NOW), 0.0, False))  # speedHeart
        self.session.send(233, (1100,))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(self._finish_events()) < 2:
            time.sleep(0.01)
        rows = self._stored()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].story_id, 1008)
        self.assertEqual(
            rows[0].successor_sequence, rows[0].predecessor_sequence + 4
        )
        self.assertEqual(self.session.ids(), NEW_ROLE_CHAIN)
        self.assertEqual(self.session.quiet_state(), "quiet")

    def test_a_lone_finish_story_is_never_a_completion(self) -> None:
        # D-53 §1: `C2S233(1008)` is the client *opening* 1008's dialogue.
        self._send_stories(self.session, 1008)
        self.assertEqual(self._stored(), ())
        event = self._finish_events()[0]
        self.assertIs(event["server_story_state_written"], False)
        self.assertEqual(event["persistence_policy"], "record_only_no_persistence")

    def test_a_lone_successor_is_never_a_completion(self) -> None:
        self._send_stories(self.session, 1100)
        self.assertEqual(self._stored(), ())
        self.assertEqual(
            self._finish_events()[0]["story_completion_refusal"],
            STORY_COMPLETION_REFUSAL_NO_PREDECESSOR,
        )

    def test_a_reversed_pair_is_never_a_completion(self) -> None:
        self._send_stories(self.session, 1100, 1008)
        self.assertEqual(self._stored(), ())
        self.assertEqual(
            self._finish_events()[1]["story_completion_refusal"],
            STORY_COMPLETION_REFUSAL_NOT_A_PAIR,
        )

    def test_a_story_outside_the_recovered_pair_is_never_a_completion(self) -> None:
        # 1100 -> 1009 is a real client-local chain, and this build deliberately
        # does not claim it: only the pair with its own close-callback proof is
        # evidence.
        self._send_stories(self.session, 1100, 1009)
        self.assertEqual(self._stored(), ())

    def test_a_repeat_is_idempotent_and_never_rewrites_the_first_sighting(self) -> None:
        self.clock_now = EVIDENCE_NOW
        self._send_stories(self.session, 1008, 1100)
        first = self._stored()[0]
        self.clock_now = EVIDENCE_NOW + 3600
        self._send_stories(self.session, 1008, 1100)
        rows = self._stored()
        self.assertEqual(rows, (first,))
        self.assertEqual(rows[0].observed_at, EVIDENCE_NOW)
        second = self._finish_events()[-1]
        self.assertIs(second["story_completion_recorded"], False)
        self.assertIs(second["story_completion_already_present"], True)
        self.assertIs(second["server_story_state_written"], False)

    def test_a_completion_never_crosses_a_connection(self) -> None:
        # 1008 in one session, 1100 in another: the ordering is per session, and
        # nothing is inferred across a disconnect.
        self._send_stories(self.session, 1008)
        self.session.close()
        other = _Session(self.port)
        try:
            other.send(0, login_fields())
            other.read(RESUMED_AFTER_LOGIN)
            self._send_stories(other, 1100)
            self.assertEqual(self._stored(), ())
        finally:
            other.close()

    def test_a_completion_never_crosses_an_account_on_one_connection(self) -> None:
        # A second login on the same connection re-establishes who the session
        # is: an arm that belonged to the first account may not be credited to
        # the second, even though the pair itself is well formed.
        other_identity = {**ACCOUNT, "sdk_identity": "someone-else"}
        other_key = self.store.account_key(**other_identity)
        self.store.create_or_get(
            account_key=other_key,
            server_id=1.0,
            role_name="别人",
            career=1,
            create_time=FIXED_NOW,
            compatibility_profile_id=COMPATIBILITY_PROFILE_ID,
        )
        self._send_stories(self.session, 1008)
        self.session.send(0, login_fields(sdk_user_id=b"someone-else"))
        self.session.read(3)
        self._send_stories(self.session, 1100)
        self.assertEqual(self._stored(), ())
        self.assertEqual(self.store.story_completions(account_key=other_key), ())
        event = self._finish_events()[-1]
        self.assertIs(event["server_story_state_written"], False)
        self.assertEqual(
            event["story_completion_refusal"],
            STORY_COMPLETION_REFUSAL_NO_PREDECESSOR,
        )

    def test_an_arm_from_before_the_role_existed_is_not_its_evidence(self) -> None:
        # The arm must belong to the role the completion would be written for.
        other_store = RoleStore.at_directory(Path(self._tmp.name) / "arming")
        port = self._boot(other_store)
        session = _Session(port)
        try:
            session.send(0, login_fields(sdk_user_id=b"late-role"))
            session.read(1)
            self.assertEqual(session.ids(), [5])
            # Armed before any role exists, then the client creates one.
            self._send_stories(session, 1008)
            session.send(2, (ROLE_NAME.encode("utf-8"), 1))
            session.read(NEW_AFTER_CREATE)
            self._send_stories(session, 1100)
            key = other_store.account_key(**{**ACCOUNT, "sdk_identity": "late-role"})
            self.assertEqual(other_store.story_completions(account_key=key), ())
            self.assertEqual(
                self._finish_events()[-1]["story_completion_refusal"],
                STORY_COMPLETION_REFUSAL_ROLE_CHANGED,
            )
        finally:
            session.close()
            other_store.close()

    def test_an_unbound_session_writes_nothing(self) -> None:
        other_store = RoleStore.at_directory(Path(self._tmp.name) / "unbound")
        port = self._boot(other_store)
        session = _Session(port)
        try:
            session.send(0, login_fields(sdk_user_id=b"nobody"))
            session.read(1)
            self.assertEqual(session.ids(), [5])
            self._send_stories(session, 1008, 1100)
            self.assertEqual(other_store.count(), 0)
            self.assertEqual(
                other_store.story_completions(
                    account_key=other_store.account_key(
                        **{**ACCOUNT, "sdk_identity": "nobody"}
                    )
                ),
                (),
            )
            self.assertEqual(
                self._finish_events()[-1]["story_completion_refusal"],
                STORY_COMPLETION_REFUSAL_UNBOUND_ROLE,
            )
        finally:
            session.close()
            other_store.close()

    def test_a_failed_transaction_rolls_back_and_says_so(self) -> None:
        with unittest.mock.patch.object(
            self.store,
            "record_story_completion",
            side_effect=RoleStoreError("disk on fire"),
        ):
            self._send_stories(self.session, 1008, 1100)
        self.assertEqual(self._stored(), ())
        event = self._finish_events()[-1]
        self.assertIs(event["story_completion_recorded"], False)
        self.assertEqual(
            event["story_completion_refusal"], STORY_COMPLETION_REFUSAL_ARCHIVE
        )
        errors = [
            e for e in self.events if e.get("event") == "tcp_error"
        ]
        self.assertEqual(
            [e["classification"] for e in errors], ["story_completion_write_failed"]
        )
        # The client only sent a notification, so the session survives our own
        # bookkeeping failure.
        self.assertEqual(self.session.quiet_state(), "quiet")


class StoryReplayTests(_StoryServerCase):
    """The stored fact comes back on the wire, and only for the proven story."""

    def _complete_1008(self, store: RoleStore, port: int) -> None:
        session = _Session(port)
        try:
            self._create_role(session)
            self.clock_now = EVIDENCE_NOW
            self._send_stories(session, 1008, 1100)
        finally:
            session.close()

    def test_a_completed_story_is_replayed_between_the_snapshot_and_the_scene(
        self,
    ) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        try:
            self._complete_1008(store, port)
        finally:
            store.close()
        store, port = self._restart()
        try:
            session = _Session(port)
            try:
                responses = self._resume_role(session, COMPLETED_RESUMED_CHAIN)
                self.assertEqual(session.ids(), COMPLETED_RESUMED_CHAIN)
                # Positions come from the chain itself, so the Team snapshot
                # block (TASK-0092) cannot shift them silently.
                replay = responses[COMPLETED_RESUMED_CHAIN.index(412)]
                self.assertEqual(replay.spec.name, "returnStory")
                self.assertEqual(replay.fields, (1008, STORY_STATE_FINISH, EVIDENCE_NOW))
                start = responses[COMPLETED_RESUMED_CHAIN.index(411)]
                self.assertEqual(start.spec.name, "returnStoryStart")
                self.assertEqual(start.fields, ())
                # Adjacency is the boundary: the row lands after the snapshot
                # that clears the container and before the scene switch packet.
                scene = responses[COMPLETED_RESUMED_CHAIN.index(283)]
                self.assertEqual(scene.spec.name, "returnSceneInfo")
                self.assertEqual(self._resume_wire_is_exactly_one_row(responses), 1)
                self.assertEqual(session.quiet_state(), "quiet")
            finally:
                session.close()
            replay_events = [
                e for e in self.events if e.get("event") == "tcp_story_completion_replayed"
            ]
            self.assertEqual(len(replay_events), 1)
            event = replay_events[0]
            self.assertEqual(event["protocol_id"], 412)
            self.assertEqual(event["story_id"], 1008)
            self.assertEqual(event["story_state"], STORY_STATE_FINISH)
            self.assertEqual(event["story_observed_at"], EVIDENCE_NOW)
            self.assertEqual(event["story_evidence_id"], 1100)
            self.assertEqual(event["story_completion_origin"], STORY_ORIGIN_LOCAL_OBSERVATION)
            self.assertIs(event["replayed"], True)
            self.assertEqual(
                event["completion_time_policy"],
                "observed_evidence_time_not_historical",
            )
            self.assertIs(event["historical_completion_time_recovered"], False)
        finally:
            store.close()

    @staticmethod
    def _resume_wire_is_exactly_one_row(responses) -> int:
        return len([p for p in responses if p.spec.protocol_id == 412])

    def test_a_login_without_a_completion_is_the_unchanged_stream(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        session = _Session(port)
        try:
            self._create_role(session)
        finally:
            session.close()
            store.close()
        store, port = self._restart()
        try:
            session = _Session(port)
            try:
                responses = self._resume_role(session)
                self.assertEqual([p.spec.protocol_id for p in responses], RESUMED_ROLE_CHAIN)
                self.assertNotIn(412, session.ids())
                # The snapshot itself is untouched: three bytes, no body.
                snapshot = responses[RESUMED_ROLE_CHAIN.index(411)]
                self.assertEqual(snapshot.fields, ())
                self.assertEqual(len(snapshot.wire), 3)
            finally:
                session.close()
            self.assertEqual(
                [
                    e
                    for e in self.events
                    if e.get("event") in {"tcp_story_completion_replayed",
                                          "tcp_story_completion_not_replayed"}
                ],
                [],
            )
        finally:
            store.close()

    def test_the_completed_and_uncompleted_streams_differ_by_one_insert(self) -> None:
        """The frozen pair: the completion is an insert, and nothing else moves."""

        store = RoleStore.at_directory(self.data_dir)
        port = self._boot(store)
        try:
            self._complete_1008(store, port)
        finally:
            store.close()
        store, port = self._restart()
        try:
            session = _Session(port)
            try:
                completed_packets = self._resume_role(session, COMPLETED_RESUMED_CHAIN)
            finally:
                session.close()
        finally:
            store.close()

        other_dir = Path(self._tmp.name) / "plain"
        store = RoleStore.at_directory(other_dir)
        port = self._boot(store)
        session = _Session(port)
        try:
            self._create_role(session)
        finally:
            session.close()
            store.close()
        store, port = self._restart()
        try:
            session = _Session(port)
            try:
                plain_packets = self._resume_role(session)
            finally:
                session.close()
        finally:
            store.close()

        # Every packet before the row is byte-identical (same rolling key, same
        # sequence, same payloads) -- the row is inserted, not substituted.
        self.assertEqual(
            [p.wire for p in completed_packets][:10],
            [p.wire for p in plain_packets][:10],
        )
        # And the frozen vectors: the no-completion login stream is the one the
        # project already pinned; the completed one differs only by that packet.
        self.assertEqual(sha256_wire(plain_packets), FROZEN_RESUME_SHA256)
        self.assertEqual(sha256_wire(completed_packets), FROZEN_COMPLETED_RESUME_SHA256)
        self.assertNotEqual(FROZEN_RESUME_SHA256, FROZEN_COMPLETED_RESUME_SHA256)


class StoryReplayRefusalTests(_StoryServerCase):
    """A stored row this build cannot vouch for is reported, never sent."""

    def setUp(self) -> None:
        super().setUp()
        self.store = RoleStore.at_directory(self.data_dir)
        self.port = self._boot(self.store)
        session = _Session(self.port)
        try:
            self._create_role(session)
        finally:
            session.close()
        self.key = self._key(self.store)
        self.role = self.store.lookup(self.key)

    def tearDown(self) -> None:
        self.store.close()
        super().tearDown()

    def _insert(self, **overrides) -> None:
        """Write a row straight into the archive, as another tool might."""

        row = {
            "account_key": self.key,
            "role_id": self.role.role_id,
            "server_id": self.role.server_id,
            "story_id": 1008,
            "state": STORY_STATE_FINISH,
            "observed_at": EVIDENCE_NOW,
            "evidence_kind": STORY_EVIDENCE_ORDERED_SUCCESSOR,
            "evidence_story_id": 1100,
            "predecessor_sequence": 0,
            "successor_sequence": 1,
            "recorded_at": EVIDENCE_NOW,
            "origin": STORY_ORIGIN_LOCAL_OBSERVATION,
        }
        row.update(overrides)
        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            connection.execute(
                "INSERT INTO story_completions ("
                + ", ".join(row)
                + ") VALUES ("
                + ", ".join("?" for _ in row)
                + ")",
                tuple(row.values()),
            )
            connection.commit()
        finally:
            connection.close()

    def _login_and_collect(self) -> tuple[list, list]:
        self.events.clear()
        store, port = self._restart_keeping_store()
        session = _Session(port)
        try:
            responses = self._resume_role(session)
        finally:
            session.close()
        return responses, [
            e for e in self.events if e.get("event") == "tcp_story_completion_not_replayed"
        ]

    def _restart_keeping_store(self):
        self._stop_all()
        return self.store, self._boot(self.store)

    def test_a_story_outside_the_proven_set_is_never_replayed(self) -> None:
        self._insert(story_id=1009, evidence_story_id=1008)
        responses, refused = self._login_and_collect()
        self.assertNotIn(412, [p.spec.protocol_id for p in responses])
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["reason"], STORY_REPLAY_REFUSAL_NOT_REPLAYABLE)
        self.assertEqual(refused[0]["story_id"], 1009)
        self.assertIs(refused[0]["replayed"], False)

    def test_a_row_for_another_role_is_never_replayed(self) -> None:
        self._insert(role_id=self.role.role_id + 100_000)
        responses, refused = self._login_and_collect()
        self.assertNotIn(412, [p.spec.protocol_id for p in responses])
        self.assertEqual(refused[0]["reason"], STORY_REPLAY_REFUSAL_ROLE_MISMATCH)

    def test_a_row_that_is_not_finish_is_never_replayed(self) -> None:
        self._insert(state=1)
        responses, refused = self._login_and_collect()
        self.assertNotIn(412, [p.spec.protocol_id for p in responses])
        self.assertEqual(refused[0]["reason"], STORY_REPLAY_REFUSAL_STATE)

    def test_a_row_whose_time_cannot_be_encoded_is_never_replayed(self) -> None:
        self._insert(observed_at=2**32)
        responses, refused = self._login_and_collect()
        self.assertNotIn(412, [p.spec.protocol_id for p in responses])
        self.assertEqual(refused[0]["reason"], STORY_REPLAY_REFUSAL_TIME)


class StoryDurableProductTests(unittest.TestCase):
    """The whole product edge on a named profile: two real restarts, one story."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.profile_root = self.root / "profiles"
        self.default_archive = self.root / "user-data"
        self._env = dict(os.environ)
        os.environ[DATA_DIR_ENV] = str(self.default_archive)
        self.addCleanup(self._restore_env)
        self.events: list[dict] = []
        self._running: list = []
        self.clock_now = FIXED_NOW
        self._args = build_parser().parse_args(
            [
                "--role-mode", ROLE_MODE_PLAY_PROFILE,
                "--play-profile", PROFILE_ID,
                "--profile-root", str(self.profile_root),
                "--bind-host", "127.0.0.1",
                "--advertise-host", "127.0.0.1",
                "--http-port", "0",
                "--tcp-port", "0",
            ]
        )

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def tearDown(self) -> None:
        self._stop_all()
        self._tmp.cleanup()

    def _boot(self, mode: str) -> tuple[RoleStore, object]:
        store = build_role_store(
            build_parser().parse_args(
                [
                    "--role-mode", ROLE_MODE_PLAY_PROFILE,
                    "--play-profile", PROFILE_ID,
                    "--profile-mode", mode,
                    "--profile-root", str(self.profile_root),
                ]
            )
        )
        servers = app_module.build_servers(
            self._args, EventLogger(stream=None, sink=self.events.append), store
        )
        threads = [
            threading.Thread(target=servers.tcp.serve_forever, daemon=True),
            threading.Thread(target=servers.http.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        self._running.append((servers, threads))
        return store, servers

    def _stop_all(self) -> None:
        for servers, threads in self._running:
            servers.tcp.shutdown()
            servers.http.shutdown()
            servers.close()
            for thread in threads:
                thread.join(timeout=2)
        self._running.clear()

    def test_the_same_profile_keeps_its_role_and_replays_its_story_twice(self) -> None:
        store, servers = self._boot(PROFILE_MODE_INIT)
        created = _Session(servers.tcp_port)
        try:
            created.send(0, login_fields())
            created.read(1)
            created.send(2, (ROLE_NAME.encode("utf-8"), 1))
            created.read(NEW_AFTER_CREATE)
            created.send(3, (1, False))
            created.read(NEW_AFTER_TERMINAL)
            created.send(217, (12345.0, b"apk-md5"))
            created.send(4, (False,))
            created.send(22, ())
            created.send(37, ())
            created.send(39, ())
            created.send(579, (True,))
            created.read(len(NEW_ROLE_CHAIN))
            self.assertEqual(created.ids(), NEW_ROLE_CHAIN)
            snapshot = created.responses[NEW_SNAPSHOT_INDEX].fields
            identity = (snapshot[1], snapshot[2], snapshot[3], snapshot[14])
            # The recovered chain, in the recovered order, on the real entry.
            # `build_servers` takes no clock, so this epoch reads the real one:
            # the observation time is bracketed rather than fixed.
            observed_before = int(time.time())
            created.send(233, (1008,))
            created.send(233, (1100,))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and len(
                [e for e in self.events if e.get("event") == "tcp_story_finish_recorded"]
            ) < 2:
                time.sleep(0.01)
            observed_after = int(time.time())
            self.assertEqual(created.quiet_state(), "quiet")
        finally:
            created.close()
            store.close()

        for round_number in (1, 2):
            store, servers = self._boot(PROFILE_MODE_RESUME)
            session = _Session(servers.tcp_port)
            try:
                session.send(0, login_fields())
                session.read(RESUMED_AFTER_LOGIN)
                self.assertEqual(
                    session.ids(), RESUMED_ROLE_CHAIN[:RESUMED_AFTER_LOGIN]
                )
                session.send(3, (1, False))
                session.read(RESUMED_AFTER_TERMINAL)
                session.send(217, (12345.0, b"apk-md5"))
                session.send(4, (False,))
                session.send(22, ())
                session.send(37, ())
                session.send(39, ())
                session.send(579, (True,))
                session.read(len(COMPLETED_RESUMED_CHAIN))
                self.assertEqual(
                    session.ids(),
                    COMPLETED_RESUMED_CHAIN,
                    f"round {round_number} drifted",
                )
                resumed_snapshot = session.responses[RESUMED_SNAPSHOT_INDEX].fields
                # Same role, same name, same career, same creation time: the
                # completion is additive and moves no identity.
                self.assertEqual(
                    (
                        resumed_snapshot[1],
                        resumed_snapshot[2],
                        resumed_snapshot[3],
                        resumed_snapshot[14],
                    ),
                    identity,
                )
                row = session.responses[COMPLETED_RESUMED_CHAIN.index(412)]
                self.assertEqual(row.spec.name, "returnStory")
                self.assertEqual(row.fields[0:2], (1008, STORY_STATE_FINISH))
                self.assertTrue(
                    observed_before <= row.fields[2] <= observed_after,
                    f"replayed dTime {row.fields[2]} is not the observed one "
                    f"({observed_before}..{observed_after})",
                )
                self.assertNotIn(5, session.ids())
                self.assertNotIn(14, session.ids())
                self.assertFalse(
                    any(e.get("event") == "tcp_error" for e in self.events)
                )
                session.send(25, ())
                self.assertEqual(session.quiet_state(), "quiet")
                self.assertEqual(store.count(), 1)
                self.assertIsNotNone(store.lookup(self._key(store)))
            finally:
                session.close()
                store.close()

        store, servers = self._boot(PROFILE_MODE_RESUME)
        try:
            rows = store.story_completions(account_key=self._key(store))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].story_id, 1008)
            self.assertEqual(rows[0].state, STORY_STATE_FINISH)
            self.assertTrue(
                observed_before <= rows[0].observed_at <= observed_after
            )
            self.assertEqual(store.count(), 1)
        finally:
            store.close()

    def _key(self, store: RoleStore) -> str:
        return store.account_key(**ACCOUNT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
