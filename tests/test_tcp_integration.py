from __future__ import annotations

import hashlib
import io
import json
import socket
import struct
import threading
import time
import unittest

from revival_stub.codec import NeedMoreData, ReaderState, WriterState, decode_packet, encode_packet
from revival_stub.compatibility_profile import (
    PROFILE_ID,
    PROFILE_ID_V1,
    PROFILE_ID_V2,
    PROFILE_ID_V3,
    PROFILE_ID_V4,
    PROFILE_ID_V5,
    PROFILE_ID_V11,
    PROFILE_ID_V12,
    PROFILE_ID_V13,
    get_profile,
    get_profile_v1,
    get_profile_v2,
    get_profile_v3,
    get_profile_v4,
    get_profile_v5,
    get_profile_v12,
    get_profile_v13,
    get_profile_v16,
    get_profile_v17,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, ProtocolRegistry
from revival_stub.role_state import ROLE_INFO_FIELD_NAMES
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS, run_simulated_login
from revival_stub.tcp_stub import RevivalTCPServer

#: TASK-0135: the C2S 69 vector RUN-0040's own packet decoded to.  Imported, not
#: copied -- the recovered field tuple lives next to the frozen wire bytes in
#: test_codec.py, and a second copy here would drift away from them.  TASK-0140
#: added the reply side next to it: the 19 values the profile assembles for that
#: request, and the wire length the recovered format implies for them.  TASK-0148
#: imports RUN-0045's own C2S 232 guideId from the same place, for the same
#: reason.
from tests.test_codec import (
    BATTLE_GAME_OVER_RUN0048_FIELDS,
    BATTLE_START_FIELDS,
    BATTLE_START_REPLY_WIRE_LENGTH,
    GUIDE_TASK_RUN0045_GUIDE_ID,
    expected_battle_start_reply,
)


SERVER_STARTED_UNIX = 1_700_000_000
SEND_NOW_UNIX = 1_700_000_123

#: TASK-0092 / profile v5: the Team + fabao-select snapshot is four packets
#: (S2C 36 returnTeamStart, 37 returnTeamInfo, 48 returnSelectMagicStart,
#: 35 returnGroupId) inserted into the terminal login segment immediately
#: before S2C411.  Named so a whole-segment read count never silently absorbs
#: a change in the snapshot's size.
TEAM_SNAPSHOT_PACKETS = 4
TEAM_SNAPSHOT_PROTOCOL_IDS = [36, 37, 48, 35]

#: TASK-0094 / profile v6: ONE recharge-activity packet (S2C 1014
#: returnACRechargeDataStart) inserted after the team block and before S2C411,
#: carrying the client's own `initRechargeStart()` reset.
RECHARGE_ACTIVITY_PACKETS = 1
RECHARGE_ACTIVITY_PROTOCOL_IDS = [1014]

#: TASK-0104 / profile v9: TWO chapter-task packets (S2C 385
#: initChapterTaskStart, 387 initChapterTaskEnd) inserted after the recharge
#: packet and immediately BEFORE the v7 chapter-item pair, carrying the client's
#: own `le_tbChapterTaskInfo = {}` reset.
CHAPTER_TASK_PACKETS = 2
CHAPTER_TASK_PROTOCOL_IDS = [385, 387]

#: TASK-0180 / profile v15: ONE chapter-task row (S2C 386
#: initChapterTaskInfo) immediately AFTER the v9 pair.  It is the labelled
#: synthetic state the client's own 祈灵 gate reads:
#: isTaskCompleted(1001, 1) -> le_tbChapterTaskInfo[1][10001001].
DRAWCARD_UNLOCK_PACKETS = 1
DRAWCARD_UNLOCK_PROTOCOL_IDS = [386]

#: TASK-0098 / profile v7: TWO chapter-item packets (S2C 389
#: initChapterItemStart, 391 initChapterItemEnd) inserted after the chapter-task
#: pair and before S2C411, carrying the client's own chapter-map item
#: container reset.
CHAPTER_ITEM_PACKETS = 2
CHAPTER_ITEM_PROTOCOL_IDS = [389, 391]

#: TASK-0102 / profile v8: TWO experience-task packets (S2C 1307
#: returnTaskStart, 1309 returnTaskEnd) inserted after the chapter-item pair and
#: before S2C411, carrying the client's own `le_tbTaskByChapterId = {}` reset.
EXPERIENCE_TASK_PACKETS = 2
EXPERIENCE_TASK_PROTOCOL_IDS = [1307, 1309]

#: TASK-0109 / profile v10: TWO chapter-machine packets (S2C 400
#: initChapterMachineStart, 402 initChapterMachineEnd) inserted after the v7
#: chapter-item pair and before S2C411, carrying the client's own
#: `le_tbChapterMachineInfo = {}` reset -- the container RUN-0033 raised on.
CHAPTER_MACHINE_PACKETS = 2
CHAPTER_MACHINE_PROTOCOL_IDS = [400, 402]

#: TASK-0242 / profile v21: the base role segment carries the two empty
#: mail-container packets (S2C 255 returnMailStart, 256 returnMailEnd) between
#: S2C 10 and S2C 9.  The default-profile reads below add this constant; the
#: frozen v1..v17 profile transactions do not.
MAIL_LOGIN_PACKETS = 2

#: TASK-0245 / profile v22: ONE S2C 39 returnTeamMaxFight immediately after the
#: v5 team snapshot's S2C 35, carrying the client's own team power so the
#: battle-entry 战力 gate (`fightLimitFight`) stops refusing a stronger team.
#: The frozen v5 transaction below does NOT add this constant.
TEAM_MAX_FIGHT_PACKETS = 1
TEAM_MAX_FIGHT_PROTOCOL_IDS = [39]

#: TASK-0247 / profile v24: ONE S2C 1435 syncBitRabbit carrying the
#: client's own inactive six-zero state, appended AFTER S2C 555
#: returnTalentSoulSkill (the last default-profile login packet).
BIT_RABBIT_STATE_PACKETS = 1
BIT_RABBIT_STATE_PROTOCOL_IDS = [1435]

#: Everything the compatibility policies insert between the terminal S2C9(40)
#: and S2C411, as ONE number: the segment reads below are all expressed with it,
#: so a policy that grows the segment can never be silently absorbed.
POLICY_PACKETS = (
    TEAM_SNAPSHOT_PACKETS + TEAM_MAX_FIGHT_PACKETS
    + RECHARGE_ACTIVITY_PACKETS + CHAPTER_TASK_PACKETS
    + DRAWCARD_UNLOCK_PACKETS + CHAPTER_ITEM_PACKETS + CHAPTER_MACHINE_PACKETS
    + EXPERIENCE_TASK_PACKETS
)

#: The whole terminal segment of a default-profile login, in wire order, after
#: the terminal S2C9(40): used by the segment-shape assertions below.
TERMINAL_SEGMENT_PROTOCOL_IDS = (
    TEAM_SNAPSHOT_PROTOCOL_IDS
    + TEAM_MAX_FIGHT_PROTOCOL_IDS
    + RECHARGE_ACTIVITY_PROTOCOL_IDS
    + CHAPTER_TASK_PROTOCOL_IDS
    + DRAWCARD_UNLOCK_PROTOCOL_IDS
    + CHAPTER_ITEM_PROTOCOL_IDS
    + CHAPTER_MACHINE_PROTOCOL_IDS
    + EXPERIENCE_TASK_PROTOCOL_IDS
)

#: The v12..v17 replay tests run an OLDER profile: their terminal segment has
#: no v22 team-power packet (v14 also has no v15 drawcard-unlock row), so they
#: must not borrow the active counts.
POLICY_PACKETS_BEFORE_V22 = POLICY_PACKETS - TEAM_MAX_FIGHT_PACKETS
V14_POLICY_PACKETS = (
    POLICY_PACKETS_BEFORE_V22 - DRAWCARD_UNLOCK_PACKETS
)
V14_TERMINAL_SEGMENT_PROTOCOL_IDS = (
    TEAM_SNAPSHOT_PROTOCOL_IDS
    + RECHARGE_ACTIVITY_PROTOCOL_IDS
    + CHAPTER_TASK_PROTOCOL_IDS
    + CHAPTER_ITEM_PROTOCOL_IDS
    + CHAPTER_MACHINE_PROTOCOL_IDS
    + EXPERIENCE_TASK_PROTOCOL_IDS
)

#: TASK-0167 / profile v13: ONE prison-fight boss snapshot
#: (S2C 1370 synPrisonFightBossToCg), sent after the S2C411/412 story state and
#: immediately before S2C283.  It is NOT part of the pre-411 policy block, so it
#: has its own constant and every whole-segment read below adds it explicitly.
PRISON_FIGHT_SNAPSHOT_PACKETS = 1
PRISON_FIGHT_SNAPSHOT_PROTOCOL_IDS = [1370]

#: Where each policy's first packet lands in the responses of a whole-segment
#: read: the terminal S2C9(40) is response 10 + MAIL_LOGIN_PACKETS (the v21 mail
#: pair) and S2C411 follows the policies.
RECHARGE_RESPONSE_INDEX = (
    11 + MAIL_LOGIN_PACKETS + TEAM_SNAPSHOT_PACKETS + TEAM_MAX_FIGHT_PACKETS
)
CHAPTER_TASK_RESPONSE_INDEX = RECHARGE_RESPONSE_INDEX + RECHARGE_ACTIVITY_PACKETS
DRAWCARD_UNLOCK_RESPONSE_INDEX = (
    CHAPTER_TASK_RESPONSE_INDEX + CHAPTER_TASK_PACKETS
)
CHAPTER_ITEM_RESPONSE_INDEX = (
    DRAWCARD_UNLOCK_RESPONSE_INDEX + DRAWCARD_UNLOCK_PACKETS
)
CHAPTER_MACHINE_RESPONSE_INDEX = CHAPTER_ITEM_RESPONSE_INDEX + CHAPTER_ITEM_PACKETS
EXPERIENCE_TASK_RESPONSE_INDEX = CHAPTER_MACHINE_RESPONSE_INDEX + CHAPTER_MACHINE_PACKETS
STORY_RESPONSE_INDEX = EXPERIENCE_TASK_RESPONSE_INDEX + EXPERIENCE_TASK_PACKETS
SCENE_RESPONSE_INDEX = (
    STORY_RESPONSE_INDEX + 1 + PRISON_FIGHT_SNAPSHOT_PACKETS
)


class TcpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict] = []
        self.logger = EventLogger(stream=None, sink=self.events.append)
        # Fixed injectable clock: the server captures SERVER_STARTED_UNIX at
        # construction; tests may advance `clock_now` before a send.
        self.clock_now = SERVER_STARTED_UNIX
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            self.logger,
            time_source=lambda: self.clock_now,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _read_responses(
        self,
        stream: socket.socket,
        buffer: bytearray,
        state: ReaderState,
        responses: list,
        target_count: int,
        *,
        registry=None,
    ) -> None:
        # The registry defaults to this test's own server; a test that started a
        # second server passes that one's (TASK-0148).
        registry = self.server.s2c_registry if registry is None else registry
        while len(responses) < target_count:
            try:
                response = decode_packet(
                    buffer, registry, state
                )
            except NeedMoreData:
                chunk = stream.recv(4096)
                self.assertTrue(chunk)
                buffer.extend(chunk)
                continue
            del buffer[: response.consumed]
            responses.append(response)

    def _restart_with_a_real_log(self, *, hex_dump: bool = True) -> io.StringIO:
        """Rebuild the server with the logger the device RUNs actually use.

        Every other logger in this file is `EventLogger(stream=None, ...)`, so
        `EventLogger.emit`'s `json.dumps` never ran in a test -- and RUN-0044
        lost a live connection to the `TypeError` it raises on a `bytes` record
        (`.autopilot/device_runs/RUN-0044/server.log:540`).  The sink stays
        attached, because the assertions below read events from it; `hex_dump`
        defaults to the forensic posture the RUNs use (D-07), so the record
        shapes under test are the production ones.
        """

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.events.clear()
        self.clock_now = SERVER_STARTED_UNIX
        log_stream = io.StringIO()
        self.logger = EventLogger(
            stream=log_stream, sink=self.events.append, hex_dump=hex_dump
        )
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            self.logger,
            time_source=lambda: self.clock_now,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        return log_stream

    @staticmethod
    def _raw_bytes_path(value: object, path: str = "record") -> str | None:
        """Where an event record carries a `bytes` value, or None.

        A record with one cannot be written at all: `eventlog.emit` logs every
        event as a single `json.dumps` line, and the raise travels out of
        `handle()`, closing the connection under it (RUN-0044).
        """

        if isinstance(value, (bytes, bytearray)):
            return path
        if isinstance(value, dict):
            for name, item in value.items():
                found = TcpIntegrationTests._raw_bytes_path(item, f"{path}[{name!r}]")
                if found is not None:
                    return found
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                found = TcpIntegrationTests._raw_bytes_path(item, f"{path}[{index}]")
                if found is not None:
                    return found
        return None

    def test_login_by_token_to_no_role_end_to_end(self) -> None:
        result = run_simulated_login("127.0.0.1", self.port)
        self.assertEqual(result.login_wire[:3], bytes.fromhex("93 71 93"))
        self.assertEqual(result.response_wire, bytes.fromhex("96 71 93"))
        self.assertEqual(result.response_protocol_id, 5)
        self.assertEqual(result.response_protocol_name, "noRole")
        self.assertEqual(result.response_fields, ())
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not any(
            event["event"] == "tcp_packet_sent" for event in self.events
        ):
            time.sleep(0.01)
        received = next(
            event for event in self.events
            if event["event"] == "tcp_packet_received"
        )
        self.assertEqual(received["protocol_id"], 0)
        self.assertEqual(received["decoded_fields"]["client_version"], "0.1.20.1")
        self.assertNotIn("local-token-md5", str(self.events))
        sent = next(
            event for event in self.events
            if event["event"] == "tcp_packet_sent"
        )
        self.assertEqual(sent["protocol_id"], 5)
        self.assertEqual(sent["response_protocol"], "noRole")

    def test_wrong_client_version_is_rejected(self) -> None:
        fields = list(DEFAULT_LOGIN_FIELDS)
        fields[4] = b"9.9.9.9"
        packet = encode_packet(C2S_REGISTRY.by_id(0), fields, WriterState())
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(packet.wire)
            self.assertEqual(stream.recv(16), b"")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not any(
            event.get("classification") == "version_mismatch" for event in self.events
        ):
            time.sleep(0.01)
        self.assertTrue(
            any(event.get("classification") == "version_mismatch" for event in self.events)
        )

    def test_login_no_role_then_net_state_notification_keeps_connection(self) -> None:
        from revival_stub.codec import WriterState, encode_packet

        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        heartbeat = encode_packet(C2S_REGISTRY.by_id(26), (), writer)
        heartbeat2 = encode_packet(C2S_REGISTRY.by_id(26), (), writer)
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + heartbeat.wire)
            response = stream.recv(64)
            self.assertEqual(response, bytes.fromhex("96 71 93"))
            stream.sendall(heartbeat2.wire)

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and sum(
            event.get("event") == "tcp_packet_ignored" for event in self.events
        ) < 2:
            time.sleep(0.01)
        ignored = [
            event for event in self.events
            if event.get("event") == "tcp_packet_ignored"
        ]
        self.assertEqual(len(ignored), 2)
        for event in ignored:
            self.assertEqual(event["protocol_id"], 26)
            self.assertEqual(event["protocol_name"], "onLine_forNetState")
            self.assertEqual(event["classification"], "ok_no_response")

    def test_login_no_role_then_online_heartbeat_keeps_connection(self) -> None:
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        online = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + online.wire)
            self.assertEqual(stream.recv(64), bytes.fromhex("96 71 93"))

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not any(
            event.get("protocol_id") == 25 for event in self.events
        ):
            time.sleep(0.01)
        ignored = [
            event for event in self.events
            if event.get("event") == "tcp_packet_ignored"
        ]
        self.assertEqual(len(ignored), 1)
        self.assertEqual(ignored[0]["protocol_id"], 25)
        self.assertEqual(ignored[0]["protocol_name"], "onLine")
        self.assertEqual(ignored[0]["classification"], "ok_no_response")
        self.assertEqual(ignored[0]["reason"], "client online heartbeat")

    def test_create_role_is_recorded_and_gets_true_result(self) -> None:
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("单机道友".encode("utf-8"), 1), writer
        )
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            response_buffer = bytearray()
            response_state = ReaderState()
            responses = []
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )

            receive_package = encode_packet(
                C2S_REGISTRY.by_id(3), (1, False), writer
            )
            stream.sendall(receive_package.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 14 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            )

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses],
            [5, 14, 8, 10, 255, 256, 9, 8, 554, 27, 0, 487, 9]
            + TERMINAL_SEGMENT_PROTOCOL_IDS
            + [411, *PRISON_FIGHT_SNAPSHOT_PROTOCOL_IDS, 283, 555, *BIT_RABBIT_STATE_PROTOCOL_IDS],
        )
        self.assertEqual(responses[1].spec.name, "returnCreateRoleResult")
        self.assertEqual(responses[1].fields, (True,))
        self.assertEqual(responses[2].fields, (1, False))
        role_fields = responses[3].fields
        self.assertEqual(len(role_fields), 21)
        self.assertEqual(role_fields[0], b"local-player")
        self.assertEqual(role_fields[2], "单机道友".encode("utf-8"))
        self.assertEqual(role_fields[3], 1.0)
        self.assertEqual(role_fields[4:9], (1.0, 0.0, 0.0, 180.0, 0.0))
        self.assertEqual(role_fields[15:], (0.0,) * 6)
        self.assertEqual(int(role_fields[1]) % 100_000, 1)
        self.assertLess(role_fields[1], 2**53)
        # TASK-0242: the v21 mail pair shifts every response after S2C 10 by two.
        self.assertEqual(responses[4].spec.protocol_id, 255)
        self.assertEqual(responses[5].spec.protocol_id, 256)
        self.assertEqual(responses[6].fields, (1, False))
        self.assertEqual(responses[7].fields, (40, False))
        self.assertEqual(responses[8].fields, (101,))
        self.assertEqual(len(responses[9].fields), 252)
        self.assertTrue(all(value == 0 for value in responses[9].fields))
        self.assertEqual(responses[10].fields, (
            SERVER_STARTED_UNIX, 0, 0, SERVER_STARTED_UNIX,
        ))
        self.assertEqual(responses[11].fields[0], 1)
        self.assertEqual(
            responses[11].fields[1], responses[10].fields[0],
        )  # exact S2C0.dTime reuse
        self.assertEqual(responses[12].fields, (40, False))
        self.assertEqual(responses[13].spec.name, "returnTeamStart")
        self.assertEqual(
            responses[RECHARGE_RESPONSE_INDEX].spec.name,
            "returnACRechargeDataStart",
        )
        # TASK-0104 / TASK-0180: the v9 pair keeps its empty bodies and is
        # followed immediately by the ONE v15 drawcard-unlock row.
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[CHAPTER_TASK_RESPONSE_INDEX:
                                       CHAPTER_ITEM_RESPONSE_INDEX]],
            CHAPTER_TASK_PROTOCOL_IDS + DRAWCARD_UNLOCK_PROTOCOL_IDS,
        )
        self.assertEqual(
            responses[DRAWCARD_UNLOCK_RESPONSE_INDEX].fields,
            (1, 10001001),
        )
        # TASK-0098: the chapter-item pair sits between the chapter-task pair and
        # the chapter-machine pair, with empty bodies and nothing else.
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[CHAPTER_ITEM_RESPONSE_INDEX:
                                       CHAPTER_MACHINE_RESPONSE_INDEX]],
            CHAPTER_ITEM_PROTOCOL_IDS,
        )
        # TASK-0109: the chapter-machine pair follows it -- the client's own
        # registration table puts the machine family (399-403) after the item
        # family (389-393), and both containers are read in the same pass of the
        # same finish callback (the machine one one statement later).
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[CHAPTER_MACHINE_RESPONSE_INDEX:
                                       EXPERIENCE_TASK_RESPONSE_INDEX]],
            CHAPTER_MACHINE_PROTOCOL_IDS,
        )
        # TASK-0102: the experience-task pair follows it, in the client's own order.
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[EXPERIENCE_TASK_RESPONSE_INDEX:STORY_RESPONSE_INDEX]],
            EXPERIENCE_TASK_PROTOCOL_IDS,
        )
        for response in responses[CHAPTER_ITEM_RESPONSE_INDEX:STORY_RESPONSE_INDEX]:
            self.assertEqual(response.fields, ())
        self.assertEqual(
            responses[STORY_RESPONSE_INDEX].spec.name, "returnStoryStart"
        )
        self.assertEqual(responses[STORY_RESPONSE_INDEX].fields, ())
        self.assertEqual(responses[SCENE_RESPONSE_INDEX].fields, (0, 0, 0, 0))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not any(
            event.get("event") == "tcp_role_created" for event in self.events
        ):
            time.sleep(0.01)
        created = next(
            event for event in self.events if event.get("event") == "tcp_role_created"
        )
        self.assertEqual(created["role"]["role_name"], "单机道友")
        self.assertEqual(created["role"]["career"], 1)
        self.assertEqual(created["role"]["level"], 1)
        self.assertEqual(created["role"]["power"], 180)
        self.assertEqual(created["role"]["role_id"] % 100_000, 1)
        sent = [
            event for event in self.events
            if event.get("event") == "tcp_packet_sent"
            and event.get("response_protocol") == "returnCreateRoleResult"
        ]
        self.assertEqual(sent[0]["decoded_fields"], (True,))
        self.assertTrue(
            any(
                event.get("event") == "tcp_login_sync_segment_sent"
                and event.get("sync_index") == 1
                for event in self.events
            )
        )

    def test_the_chapter_event_reward_is_ledgered_without_showing_it(self) -> None:
        """TASK-0222: C2S 224 -> S2C 398 -> permanent ledger; the client stays at 0.

        The shipped `guankaReward[25101]` row names no crystal bucket, so the
        ledger stores NULL/unknown and the S2C 10 vector, the updateCrystal
        channel (S2C 68) and the gain-info family are all untouched.
        """

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
                    C2S_REGISTRY.by_id(2), ("账本集成道友".encode("utf-8"), 1), writer
                ).wire
            )
            self._read_responses(stream, buffer, reader, responses, 5 + MAIL_LOGIN_PACKETS)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            self._read_responses(
                stream,
                buffer,
                reader,
                responses,
                14 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS,
            )
            event_start = len(responses)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(224), (1, 36, 0, 1, 0, 0.0), writer
                ).wire
                + encode_packet(C2S_REGISTRY.by_id(725), (), writer).wire
            )
            # TASK-0246 / profile v23: C2S 725 is now answered by the S2C 1499
            # month-sign state and then the empty S2C 1500 ack.
            self._read_responses(stream, buffer, reader, responses, len(responses) + 3)

        event_packets = responses[event_start:]
        self.assertEqual(
            [packet.spec.protocol_id for packet in event_packets],
            [398, 1499, 1500],
        )
        ids = {packet.spec.protocol_id for packet in responses}
        self.assertFalse(ids & {68, 81, 82, 83})
        login = next(
            packet for packet in responses if packet.spec.protocol_id == 10
        )
        named = dict(zip(ROLE_INFO_FIELD_NAMES, login.fields, strict=True))
        self.assertEqual(named["crystal"], 0)
        self.assertEqual(
            [named[name] for name in ROLE_INFO_FIELD_NAMES
             if name.startswith("crystal_")],
            [0, 0, 0, 0, 0, 0],
        )

        key = self.server.role_store.account_key(
            server_id=1.0,
            platform="android",
            channel_id="local-channel",
            channel_sub_id="0",
            sdk_identity="local-user",
        )
        self.assertEqual(
            self.server.role_store.chapter_event_reward_ledger_total(
                account_key=key
            ),
            10,
        )
        self.assertEqual(
            self.server.role_store.role_wallet_values(account_key=key), {}
        )
        recorded = [
            event for event in self.events
            if event.get("event") == "tcp_chapter_event_clear_recorded"
        ]
        self.assertEqual(len(recorded), 1)
        authority = recorded[0]["reward_authority"]
        self.assertIs(authority["granted"], False)
        self.assertIs(authority["ledger_recorded"], True)
        self.assertIs(authority["applied_to_client_balance"], False)
        self.assertEqual(authority["client_replay"], "not_implemented")
        self.assertIsNone(authority["crystal_bucket"])
        self.assertEqual(authority["bucket_provenance"], "unknown")

    def test_a_fight_special_card_is_granted_and_replayed_at_login(self) -> None:
        """TASK-0223: 102071's 8886 reward becomes stored dog food, not a packet.

        C2S 67 for fight 102071 pays the recovered ``specialCard`` cfg id 1
        (1★ dog food) into ``card_special_inventory`` in the same transaction as
        the clear row.  The battle path sends neither a money S2C 23 (that fight
        has no recovered money row) nor a dog-food display packet; the next
        login replays the inventory as S2C 504/506/505, and the S2C 10 crystal
        vector stays exactly what the first login sent.
        """

        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        responses: list = []

        def read_until(protocol_id: int, timeout: float = 3.0) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    packet = decode_packet(
                        buffer, self.server.s2c_registry, reader
                    )
                except NeedMoreData:
                    try:
                        chunk = stream.recv(65536)
                    except socket.timeout:
                        self.fail(
                            f"S2C {protocol_id} not received (timeout; "
                            f"tail={[item.get('event') for item in self.events[-6:]]})"
                        )
                    if not chunk:
                        self.fail(
                            f"S2C {protocol_id} not received (closed; "
                            f"tail={self.events[-6:]})"
                        )
                    buffer.extend(chunk)
                    continue
                del buffer[: packet.consumed]
                responses.append(packet)
                if packet.spec.protocol_id == protocol_id:
                    return
            self.fail(f"S2C {protocol_id} not received")

        def drain(seconds: float = 0.3) -> list:
            drained: list = []
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    packet = decode_packet(
                        buffer, self.server.s2c_registry, reader
                    )
                except NeedMoreData:
                    try:
                        chunk = stream.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        return drained
                    buffer.extend(chunk)
                    continue
                del buffer[: packet.consumed]
                responses.append(packet)
                drained.append(packet)
            return drained

        def settle(fight_id: int, map_index: int, chapter_id: int) -> list:
            start = len(responses)
            fields = list(BATTLE_START_FIELDS)
            fields[3] = float(fight_id)
            fields[4] = float(map_index)
            fields[6] = float(chapter_id)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(69), tuple(fields), writer).wire
            )
            read_until(89)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(67),
                    BATTLE_GAME_OVER_RUN0048_FIELDS,
                    writer,
                ).wire
            )
            read_until(96)
            return responses[start:]

        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as stream:
            stream.settimeout(3)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
                ).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2),
                    ("狗粮集成道友".encode("utf-8"), 1),
                    writer,
                ).wire
            )
            read_until(9)
            first_login = [
                packet for packet in responses if packet.spec.protocol_id == 10
            ]
            self.assertEqual(len(first_login), 1)
            # An empty dog-food inventory keeps sending zero snapshot packets.
            self.assertFalse(
                {packet.spec.protocol_id for packet in responses}
                & {504, 505, 506}
            )
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            read_until(27)

            # 101010 pays its recovered money; the S2C 23 is the existing road.
            settle(101010, 2, 1)
            read_until(23)
            money = [
                packet for packet in responses if packet.spec.protocol_id == 23
            ]
            self.assertEqual(
                [(packet.fields[0], packet.fields[1]) for packet in money],
                [(7, 40.0)],
            )

            # 102071 pays the recovered special card only.
            drain(0.2)
            segment_start = len(responses)
            settle(102071, 7, 2)
            drained = drain()
            segment = responses[segment_start:] + drained
            self.assertEqual(
                [packet.spec.protocol_id for packet in segment], [89, 96]
            )

        granted = [
            event for event in self.events
            if event.get("event") == "tcp_chapter_fight_special_card_granted"
        ]
        self.assertEqual(len(granted), 1)
        self.assertEqual(granted[0]["fight_id"], 102071)
        self.assertEqual(granted[0]["reward_id"], 501)
        self.assertEqual(granted[0]["reward_type"], 8886)
        self.assertEqual(granted[0]["special_card_cfg_id"], 1)
        self.assertEqual(granted[0]["count"], 1)
        self.assertEqual(granted[0]["inventory_count_after"], 1)
        self.assertEqual(granted[0]["display"], "not_sent_until_proven")

        key = self.server.role_store.account_key(
            server_id=1.0,
            platform="android",
            channel_id="local-channel",
            channel_sub_id="0",
            sdk_identity="local-user",
        )
        self.assertEqual(
            self.server.role_store.card_special_counts(account_key=key), {1: 1}
        )

        # Next login: a new connection has its own sequence state, and the
        # client's own dog-food snapshot replays the stored row.
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as stream:
            stream.settimeout(3)
            stream.sendall(
                encode_packet(
                    C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
                ).wire
                + encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            read_until(283)

        second_login = [
            packet for packet in responses if packet.spec.protocol_id == 10
        ]
        self.assertEqual(len(second_login), 2)
        self.assertEqual(second_login[1].fields, first_login[0].fields)
        ids = [packet.spec.protocol_id for packet in responses]
        self.assertIn(504, ids)
        self.assertIn(506, ids)
        self.assertIn(505, ids)
        info = [
            packet for packet in responses if packet.spec.protocol_id == 506
        ]
        self.assertEqual(
            [(packet.fields[0], packet.fields[1]) for packet in info], [(1, 1)]
        )
        self.assertLess(ids.index(504), ids.index(506))
        self.assertLess(ids.index(506), ids.index(505))
        self.assertFalse(set(ids) & {68, 81, 82, 83})
        self.assertEqual(
            self.server.role_store.card_special_counts(account_key=key), {1: 1}
        )

    def test_full_compatibility_scene_entry_transaction(self) -> None:
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        heartbeat_before_create = encode_packet(
            C2S_REGISTRY.by_id(26), (), writer
        )
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("田听双".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses = []

        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + heartbeat_before_create.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )

            self.clock_now = SEND_NOW_UNIX
            receive_package = encode_packet(
                C2S_REGISTRY.by_id(3), (1, False), writer
            )
            stream.sendall(receive_package.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 14 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            )

            immediate = (
                encode_packet(C2S_REGISTRY.by_id(217), (12345.0, b"apk-md5"), writer),
                encode_packet(C2S_REGISTRY.by_id(4), (False,), writer),
                encode_packet(C2S_REGISTRY.by_id(22), (), writer),
                encode_packet(C2S_REGISTRY.by_id(37), (), writer),
                encode_packet(C2S_REGISTRY.by_id(39), (), writer),
                encode_packet(C2S_REGISTRY.by_id(579), (True,), writer),
            )
            stream.sendall(b"".join(packet.wire for packet in immediate))
            self._read_responses(
                stream, response_buffer, response_state, responses, 16 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            )

            heartbeat_online = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            heartbeat_net_state = encode_packet(C2S_REGISTRY.by_id(26), (), writer)
            stream.sendall(heartbeat_online.wire + heartbeat_net_state.wire)
            stream.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses],
            [5, 14, 8, 10, 255, 256, 9, 8, 554, 27, 0, 487, 9]
            + TERMINAL_SEGMENT_PROTOCOL_IDS
            + [411, *PRISON_FIGHT_SNAPSHOT_PROTOCOL_IDS, 283, 555, *BIT_RABBIT_STATE_PROTOCOL_IDS, 13, 85],
        )
        self.assertEqual(responses[8].fields, (101,))
        self.assertEqual(len(responses[9].fields), 252)
        self.assertEqual(
            responses[10].fields, (SEND_NOW_UNIX, 0, 0, SERVER_STARTED_UNIX)
        )
        self.assertEqual(responses[11].fields[0], 1)  # refreshShopStart dType
        self.assertEqual(
            responses[11].fields[1], responses[10].fields[0]
        )  # exact S2C0.dTime reuse: no second clock read
        self.assertEqual(responses[12].fields, (40, False))
        # TASK-0092 / profile v5: the recovered Team + fabao-select snapshot is
        # one contiguous four-packet block that follows the terminal sync
        # segment.  Every slot is the client's own empty-slot literal.
        self.assertEqual(
            [packet.spec.name for packet in responses[13:17]],
            [
                "returnTeamStart",
                "returnTeamInfo",
                "returnSelectMagicStart",
                "returnGroupId",
            ],
        )
        self.assertEqual(responses[13].fields, ())
        self.assertEqual(len(responses[13].wire), 3)
        self.assertEqual(responses[14].fields, (1,) + (b"",) * 16)
        self.assertEqual(responses[15].fields, ())
        self.assertEqual(len(responses[15].wire), 3)
        self.assertEqual(responses[16].fields, (1,))
        # TASK-0245 / profile v22: ONE S2C 39 right after S2C 35, carrying the
        # client's own team power.  This fresh role has no stored preset, so the
        # value is the profile's active soul fight alone (soul 101 -> 100).
        self.assertEqual(responses[17].spec.name, "returnTeamMaxFight")
        self.assertEqual(responses[17].fields, (100,))
        # TASK-0094: one recharge-activity packet, then the story snapshot.
        self.assertEqual(
            responses[RECHARGE_RESPONSE_INDEX].spec.name,
            "returnACRechargeDataStart",
        )
        self.assertEqual(responses[RECHARGE_RESPONSE_INDEX].fields, (0, True))
        # Synthetic compatibility ordering (v4/TASK-0035): the empty story
        # snapshot is the LAST packet before the one that starts the main-city
        # switch, and it follows the terminal sync segment.
        self.assertEqual(
            responses[STORY_RESPONSE_INDEX].spec.name, "returnStoryStart"
        )
        # TASK-0104 / TASK-0180: the chapter-task pair, then the one v15
        # drawcard-unlock row, before the chapter-item pair.
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[CHAPTER_TASK_RESPONSE_INDEX:
                                       CHAPTER_ITEM_RESPONSE_INDEX]],
            CHAPTER_TASK_PROTOCOL_IDS + DRAWCARD_UNLOCK_PROTOCOL_IDS,
        )
        self.assertEqual(
            responses[DRAWCARD_UNLOCK_RESPONSE_INDEX].fields,
            (1, 10001001),
        )
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[CHAPTER_ITEM_RESPONSE_INDEX:
                                       CHAPTER_MACHINE_RESPONSE_INDEX]],
            CHAPTER_ITEM_PROTOCOL_IDS,
        )
        # TASK-0109: the chapter-machine pair, after the item pair.
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[CHAPTER_MACHINE_RESPONSE_INDEX:
                                       EXPERIENCE_TASK_RESPONSE_INDEX]],
            CHAPTER_MACHINE_PROTOCOL_IDS,
        )
        # TASK-0102: the experience-task pair follows it, in the client's own order.
        self.assertEqual(
            [response.spec.protocol_id
             for response in responses[EXPERIENCE_TASK_RESPONSE_INDEX:STORY_RESPONSE_INDEX]],
            EXPERIENCE_TASK_PROTOCOL_IDS,
        )
        self.assertEqual(responses[STORY_RESPONSE_INDEX].fields, ())
        self.assertEqual(len(responses[STORY_RESPONSE_INDEX].wire), 3)
        self.assertEqual(responses[SCENE_RESPONSE_INDEX].fields, (0, 0, 0, 0))
        # TASK-0247: S2C 555 is followed by the v24 S2C 1435 bitRabbit state
        # (six zeros) before S2C 13 enterGameSuccess.
        self.assertEqual(responses[SCENE_RESPONSE_INDEX + 1].fields, (0,))
        self.assertEqual(
            responses[SCENE_RESPONSE_INDEX + 2].spec.protocol_id, 1435
        )
        self.assertEqual(responses[SCENE_RESPONSE_INDEX + 2].fields, (0,) * 6)
        self.assertEqual(responses[SCENE_RESPONSE_INDEX + 3].fields, ())
        sign_fields = responses[SCENE_RESPONSE_INDEX + 4].fields
        self.assertEqual(sign_fields[:5], (False, 0, 0, 0, False))
        self.assertEqual(sign_fields[7], b"")
        self.assertEqual(sign_fields[9:], (b"", 1))
        self.assertEqual(
            [packet.sequence for packet in responses],
            list(range(len(responses))),
        )
        for previous, current in zip(responses, responses[1:]):
            self.assertEqual(previous.key_after, current.key_before)

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and sum(
            event.get("event") == "tcp_packet_received" for event in self.events
        ) < 12:
            time.sleep(0.01)

        sent_names = [
            event["protocol_name"]
            for event in self.events
            if event.get("event") == "tcp_packet_sent"
        ]
        self.assertEqual(
            sent_names,
            [
                "noRole",
                "returnCreateRoleResult",
                "returnLoginInfoStart",
                "returnRoleInfo",
                # TASK-0242 / profile v21: the empty mail bracket.
                "returnMailStart",
                "returnMailEnd",
                "returnLoginInfoEnd",
                "returnLoginInfoStart",
                "returnTalentSoulId",
                "returnAllValue",
                "serverTime",
                "refreshShopStart",
                "returnLoginInfoEnd",
                "returnTeamStart",
                "returnTeamInfo",
                "returnSelectMagicStart",
                "returnGroupId",
                # TASK-0245 / profile v22: the team-power value after S2C 35.
                "returnTeamMaxFight",
                "returnACRechargeDataStart",
                "initChapterTaskStart",
                "initChapterTaskEnd",
                # TASK-0180 / profile v15: the one-row drawcard unlock follows
                # the v9 pair and precedes the chapter-item pair.
                "initChapterTaskInfo",
                "initChapterItemStart",
                "initChapterItemEnd",
                "initChapterMachineStart",
                "initChapterMachineEnd",
                "returnTaskStart",
                "returnTaskEnd",
                "returnStoryStart",
                # TASK-0167 / profile v13: the prison-fight boss snapshot sits
                # between the story state and the scene packet.
                "synPrisonFightBossToCg",
                "returnSceneInfo",
                # v16: the empty soul-skill selection follows the scene packet.
                "returnTalentSoulSkill",
                # TASK-0247 / profile v24: the inactive bitRabbit state is the
                # last login packet.
                "syncBitRabbit",
                "enterGameSuccess",
                "returnSignInReward",
            ],
        )
        received = [
            (event["protocol_id"], event["sequence"])
            for event in self.events
            if event.get("event") == "tcp_packet_received"
        ]
        self.assertEqual(
            [protocol_id for protocol_id, _ in received],
            [0, 26, 2, 3, 217, 4, 22, 37, 39, 579, 25, 26],
        )
        self.assertEqual([sequence for _, sequence in received], list(range(12)))

        expected_events = {
            "compatibility_profile_loaded",
            "tcp_compat_role_initialized",
            "tcp_active_soul_sent",
            "tcp_role_values_sent",
            "tcp_server_time_sent",
            "tcp_mystery_shop_state_sent",
            "tcp_scene_state_sent",
            "tcp_enter_game_ack_sent",
            "tcp_sign_state_sent",
        }
        present_events = {event.get("event") for event in self.events}
        self.assertTrue(expected_events <= present_events)
        for event in self.events:
            if event.get("event") in expected_events - {"compatibility_profile_loaded"}:
                self.assertEqual(event["profile_id"], PROFILE_ID)
        scene_event = next(
            event for event in self.events if event.get("event") == "tcp_scene_state_sent"
        )
        self.assertEqual(scene_event["origin"], "synthetic_compatibility")
        self.assertEqual(scene_event["protocol_id"], 283)
        self.assertNotEqual(scene_event["protocol_id"], 138)
        sign_event = next(
            event for event in self.events if event.get("event") == "tcp_sign_state_sent"
        )
        self.assertEqual(sign_event["account_state_origin"], "synthetic_compatibility")
        self.assertEqual(
            set(sign_event["catalog_origin"].values()),
            {"official_recovered"},
        )
        time_event = next(
            event for event in self.events if event.get("event") == "tcp_server_time_sent"
        )
        self.assertEqual(time_event["protocol_id"], 0)
        self.assertEqual(time_event["protocol_name"], "serverTime")
        self.assertEqual(time_event["profile_id"], PROFILE_ID)
        # TASK-0094: the active profile is v6; every earlier policy carries
        # forward under it, so the active profile's parent is v5.
        # The event reports the stream that carried it: the ancestry is the
        # serving profile's own parent (TASK-0098 moved it to v7).
        self.assertEqual(
            time_event["parent_profile_id"], get_profile().parent_profile_id
        )
        # The ServerTime policy was introduced in v2 and stays v2 under v3/v4.
        self.assertEqual(time_event["policy_compatibility_version"], PROFILE_ID_V2)
        self.assertEqual(time_event["historical_ordering"], "unknown")
        self.assertEqual(time_event["ordering_origin"], "synthetic_compatibility")
        self.assertEqual(time_event["field_policies"], {
            "dTime": "current_unix_time_at_send",
            "dOpenTime": "revival_process_start_unix",
        })
        self.assertEqual(time_event["field_origins"], {
            "dTime": "deterministic_recovered",
            "dClock": "synthetic_compatibility",
            "dPowerRecoverTime": "synthetic_compatibility",
            "dOpenTime": "synthetic_compatibility",
        })
        self.assertEqual(time_event["server_time_values"], {
            "dTime": SEND_NOW_UNIX,
            "dClock": 0,
            "dPowerRecoverTime": 0,
            "dOpenTime": SERVER_STARTED_UNIX,
        })
        self.assertFalse(time_event["historical_values_recovered"])
        shop_event = next(
            event for event in self.events
            if event.get("event") == "tcp_mystery_shop_state_sent"
        )
        self.assertEqual(shop_event["protocol_id"], 487)
        self.assertEqual(shop_event["protocol_name"], "refreshShopStart")
        self.assertEqual(shop_event["profile_id"], PROFILE_ID)
        # The event reports the stream that carried it: the ancestry is the
        # serving profile's own parent (TASK-0098 moved it to v7).
        self.assertEqual(
            shop_event["parent_profile_id"], get_profile().parent_profile_id
        )
        self.assertEqual(shop_event["policy_compatibility_version"], PROFILE_ID_V3)
        self.assertEqual(shop_event["shop_type"], 1)
        self.assertEqual(shop_event["shop_type_origin"], "deterministic_recovered")
        self.assertEqual(shop_event["shop_time_values"], {
            "dType": 1,
            "dTime": SEND_NOW_UNIX,
        })
        self.assertEqual(shop_event["d_time_policy"], "reuse_server_time_dtime")
        self.assertEqual(shop_event["d_time_policy_origin"], "synthetic_compatibility")
        self.assertEqual(shop_event["availability"], "closed")
        self.assertEqual(shop_event["availability_origin"], "synthetic_compatibility")
        self.assertEqual(shop_event["goods_count"], 0)
        self.assertEqual(shop_event["goods_origin"], "synthetic_compatibility")
        self.assertFalse(shop_event["snapshot_end_sent"])
        self.assertEqual(shop_event["historical_ordering"], "unknown")
        self.assertEqual(shop_event["ordering_origin"], "synthetic_compatibility")
        self.assertTrue(shop_event["server_time_dtime_reused"])
        self.assertFalse(shop_event["historical_values_recovered"])
        role_values_event = next(
            event for event in self.events if event.get("event") == "tcp_role_values_sent"
        )
        self.assertEqual(role_values_event["field_count"], 252)
        self.assertEqual(
            role_values_event["class_counts"],
            get_profile().class_counts(),
        )
        ignored_ids = [
            event["protocol_id"]
            for event in self.events
            if event.get("event") == "tcp_packet_ignored"
        ]
        for protocol_id in (217, 22, 37, 579, 25, 26):
            self.assertIn(protocol_id, ignored_ids)
        self.assertTrue(
            any(
                event.get("event") == "tcp_login_sync_complete_sent"
                and event.get("sync_index") == 40
                for event in self.events
            )
        )

    def test_speed_heart_after_scene_entry_is_recorded_without_response(self) -> None:
        # TASK-0001: pkgHeartMgr arms a 200 ms timer at pkgEnterGameMgr.init and
        # sends C2S 23 speedHeart 'ppy' ~3 s later, then every 3 s.  Before this
        # registration every send hit the unregistered-protocol path (D-08:
        # malformed_packet + disconnect) and killed the first main-city session.
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("心跳道友".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            self.clock_now = SEND_NOW_UNIX
            receive_package = encode_packet(
                C2S_REGISTRY.by_id(3), (1, False), writer
            )
            stream.sendall(receive_package.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 14 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            )

            # First speedHeart carries bClearOld=true (the scene name differs
            # from the still-nil le_last_scene_name); later sends carry false.
            first = encode_packet(
                C2S_REGISTRY.by_id(23), (SEND_NOW_UNIX + 3, 12.5, True), writer
            )
            second = encode_packet(
                C2S_REGISTRY.by_id(23), (SEND_NOW_UNIX + 6, 11.0, False), writer
            )
            # Liveness probe: the same socket must still decode a later
            # heartbeat, i.e. no D-08 disconnect happened at C2S 23.
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(first.wire + second.wire + heartbeat.wire)

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("protocol_id") == 25 for event in self.events
            ):
                time.sleep(0.01)

            # No response is reconstructed for C2S 23: nothing follows the
            # frozen 12-packet transaction.
            stream.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses],
            [5, 14, 8, 10, 255, 256, 9, 8, 554, 27, 0, 487, 9]
            + TERMINAL_SEGMENT_PROTOCOL_IDS
            + [411, *PRISON_FIGHT_SNAPSHOT_PROTOCOL_IDS, 283, 555, *BIT_RABBIT_STATE_PROTOCOL_IDS],
        )
        received = [
            (event["protocol_id"], event["sequence"])
            for event in self.events
            if event.get("event") == "tcp_packet_received"
        ]
        self.assertEqual(
            received,
            [(0, 0), (2, 1), (3, 2), (23, 3), (23, 4), (25, 5)],
        )
        decoded = [
            event["decoded_fields"]
            for event in self.events
            if event.get("event") == "tcp_packet_received"
            and event.get("protocol_id") == 23
        ]
        # decoded_fields is logged as a list (raw wire order: unix seconds,
        # measured net delta, first-send-after-scene-change flag).
        self.assertEqual(
            decoded,
            [
                [SEND_NOW_UNIX + 3, 12.5, True],
                [SEND_NOW_UNIX + 6, 11.0, False],
            ],
        )
        ignored = [
            event for event in self.events
            if event.get("event") == "tcp_packet_ignored"
        ]
        self.assertEqual(len(ignored), 3)
        speed_heart_events = [
            event for event in ignored if event["protocol_id"] == 23
        ]
        self.assertEqual(len(speed_heart_events), 2)
        for event in speed_heart_events:
            self.assertEqual(event["protocol_name"], "speedHeart")
            self.assertEqual(event["classification"], "ok_no_response")
            self.assertEqual(
                event["reason"],
                "recovered periodic client speed-heart notification; "
                "the registration contains no S2C response",
            )
        self.assertFalse(any(
            event.get("event") == "tcp_error" for event in self.events
        ))
        self.assertFalse(any(
            event.get("protocol_name") == "speedHeart"
            and event.get("event") == "tcp_packet_sent"
            for event in self.events
        ))

    def test_battle_start_is_answered_once_per_request_and_keeps_connection(self) -> None:
        # TASK-0135 wired C2S 69 into the dispatch chain and stopped at
        # "decode + record + NO response".  TASK-0140 answers it: the packet is a
        # LOCK-SEND whose only unlock is S2C 89 `startPveBattle`, and TASK-0139
        # proved which reply branch the client can play through on its own
        # (`isUseSfSvr = false` -> the local single driver; no battle C2S ever
        # reaches the socket).  The contract asserted here:
        #   * one request -> exactly ONE reply, on the same connection;
        #   * the 19 values are the profile's, position by position;
        #   * a second request in the same session gets a DIFFERENT sfStartTime
        #     (the client drops a reply whose sfStartTime, dRawRandomSeed and
        #     pveOrPvpType all equal the previous one's);
        #   * nothing about the unknown-protocol fallback happens anywhere.
        writer = WriterState()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("战斗起点道友".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            first_request = encode_packet(
                C2S_REGISTRY.by_id(69), BATTLE_START_FIELDS, writer
            )
            stream.sendall(first_request.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 6 + MAIL_LOGIN_PACKETS
            )
            second_request = encode_packet(
                C2S_REGISTRY.by_id(69), BATTLE_START_FIELDS, writer
            )
            stream.sendall(second_request.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 7 + MAIL_LOGIN_PACKETS
            )
            # Liveness probe: the same socket must still decode a later
            # heartbeat, i.e. answering the battle start may not close anything.
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(heartbeat.wire)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("protocol_id") == 25 for event in self.events
            ):
                time.sleep(0.01)

            # Every assertion below is made while the client still owns this
            # socket: the `tcp_disconnected` that appears after this block is
            # this test's own close, never the stub's.
            self.assertFalse(any(
                event.get("event") == "tcp_disconnected" for event in self.events
            ))
            self.assertFalse(any(
                event.get("classification") == "unsupported_protocol"
                or event.get("classification") == "malformed_packet"
                for event in self.events
            ))
            self.assertFalse(any(
                event.get("event") == "tcp_error" for event in self.events
            ))

            # One reply per request, and both replies are S2C 89.
            # TASK-0242: the v21 base segment is 7 packets, so the two S2C 89
            # replies land on indices 7 and 8 (was 5 and 6).
            first_reply, second_reply = responses[7], responses[8]
            for reply in (first_reply, second_reply):
                self.assertEqual(reply.spec.protocol_id, 89)
                self.assertEqual(reply.spec.name, "startPveBattle")
                self.assertEqual(reply.spec.fmt, "NyyNNNNpppppppppaaa")
                self.assertEqual(len(reply.fields), 19)
                self.assertEqual(
                    len(reply.wire), BATTLE_START_REPLY_WIRE_LENGTH
                )
                self.assertEqual(reply.consumed, len(reply.wire))
            # The 19 values, position by position: the three branch/constant
            # choices, the packaged pveOrPvpType, the archive's fixed seed, and
            # the request's own values echoed back.
            self.assertEqual(
                first_reply.fields,
                expected_battle_start_reply(SERVER_STARTED_UNIX + 1),
            )
            self.assertEqual(
                second_reply.fields,
                expected_battle_start_reply(SERVER_STARTED_UNIX + 2),
            )
            # ... and the two replies differ in exactly the values that have to
            # differ, so a second battle is not discarded as a duplicate packet.
            # TASK-0182: position 1 (sfStartTime) and position 7 (dSyncCount)
            # both carry the per-reply counter; the client drops the packet when
            # `dPveSyncCount == dSyncCount`.
            self.assertNotEqual(first_reply.fields[0], second_reply.fields[0])
            self.assertNotEqual(first_reply.fields[6], second_reply.fields[6])
            self.assertEqual(first_reply.fields[6], first_reply.fields[0])
            self.assertEqual(second_reply.fields[6], second_reply.fields[0])
            self.assertEqual(
                first_reply.fields[1:6] + first_reply.fields[7:],
                second_reply.fields[1:6] + second_reply.fields[7:],
            )
            # The client's own guard, simulated: the first reply replaces the
            # remembered -1, and the second must not equal what was stored.
            remembered_sync_count = -1
            self.assertNotEqual(first_reply.fields[6], remembered_sync_count)
            remembered_sync_count = first_reply.fields[6]
            self.assertNotEqual(second_reply.fields[6], remembered_sync_count)

            received = [
                event for event in self.events
                if event.get("event") == "tcp_packet_received"
                and event.get("protocol_id") == 69
            ]
            self.assertEqual(len(received), 2)
            self.assertEqual(received[0]["protocol_name"], "netEnterPveBattle")
            # The RUN-0040 vector, on this connection's own writer state: bytes
            # fields become text the way this file already logs them.
            self.assertEqual(
                received[0]["decoded_fields"],
                [
                    "" if isinstance(value, bytes) else value
                    for value in BATTLE_START_FIELDS
                ],
            )
            # Nothing is recorded as ignored any more: the C2S 69 branch answers.
            self.assertFalse(any(
                event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 69
                for event in self.events
            ))
            sent = [
                event for event in self.events
                if event.get("event") == "tcp_packet_sent"
                and event.get("protocol_id") == 89
            ]
            self.assertEqual(len(sent), 2)
            segment = next(
                event for event in self.events
                if event.get("event") == "tcp_login_sync_segment_sent"
            )
            for event in sent:
                self.assertEqual(event["response_protocol"], "startPveBattle")
                self.assertEqual(
                    event["classification"], "profile_backed_battle_start_reply"
                )
                self.assertEqual(
                    len(event["decoded_fields"]), 19
                )
                self.assertEqual(event["role_id"], segment["role_id"])
                self.assertEqual(event["profile_id"], PROFILE_ID)
                # The lock this reply releases is the request that asked for it.
                self.assertEqual(event["lock_partner"], "netEnterPveBattle")
                self.assertEqual(event["unlock_only_for"], "netEnterPveBattle")
                self.assertEqual(
                    event["reply_trigger"], "one_reply_per_client_request"
                )
            # The two answer events carry the whole value set and its provenance.
            replied = [
                event for event in self.events
                if event.get("event") == "tcp_battle_start_replied"
            ]
            self.assertEqual(len(replied), 2)
            # The event log renders byte strings as text, exactly as it does for
            # the received request above, so both sides are compared that way.
            for event, reply in ((replied[0], first_reply), (replied[1], second_reply)):
                self.assertEqual(
                    event["reply_fields"],
                    [
                        "" if isinstance(value, bytes) else value
                        for value in reply.fields
                    ],
                )
                self.assertEqual(
                    event["reply_field_names"],
                    ["sfStartTime", "isUseSfSvr", "(unnamed)", "selfGroup",
                     "pveOrPvpType", "dRawRandomSeed", "dSyncCount",
                     "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9",
                     "s1", "s2", "s3"],
                )
            self.assertEqual(replied[0]["sf_start_time"], SERVER_STARTED_UNIX + 1)
            self.assertEqual(replied[1]["sf_start_time"], SERVER_STARTED_UNIX + 2)
            # TASK-0182: position 7 carries the same per-reply counter, and the
            # event separates provenance origins from value origins.
            self.assertEqual(
                replied[0]["sync_count_reply"], SERVER_STARTED_UNIX + 1
            )
            self.assertEqual(
                replied[1]["sync_count_reply"], SERVER_STARTED_UNIX + 2
            )
            self.assertEqual(
                replied[0]["field_origins"]["dSyncCount"],
                "synthetic_compatibility",
            )
            self.assertEqual(
                replied[0]["field_value_origins"]["dSyncCount"],
                "reply_counter",
            )
            self.assertEqual(
                replied[0]["field_value_origins"]["sfStartTime"],
                "reply_counter",
            )
            self.assertEqual(
                replied[0]["field_value_origins"]["p1"], "request_echo"
            )
            self.assertEqual(replied[0]["requests_answered"], 1)
            self.assertEqual(replied[1]["requests_answered"], 2)
            self.assertIs(replied[0]["repeated_request"], False)
            self.assertIs(replied[1]["repeated_request"], True)
            self.assertIs(replied[0]["branch_is_use_sf_svr"], False)
            self.assertIs(replied[0]["branch_transfer_prepare_data"], False)
            self.assertEqual(
                replied[0]["battle_drive"], "client_local_single_dvr"
            )
            self.assertIs(replied[0]["battle_c2s_reaches_socket"], False)
            self.assertEqual(
                replied[0]["field_origins"]["pveOrPvpType"],
                "deterministic_recovered",
            )
            self.assertEqual(
                replied[0]["field_origins"]["selfGroup"], "synthetic_compatibility"
            )
            self.assertEqual(
                replied[0]["field_origins"]["p1"], "request_echo"
            )
            self.assertEqual(replied[0]["historical_ordering"], "unknown")
            self.assertIs(replied[0]["historical_values_recovered"], False)
            self.assertEqual(replied[0]["profile_id"], PROFILE_ID)
            self.assertEqual(
                replied[0]["policy_compatibility_version"], PROFILE_ID_V11
            )
            # The heartbeat after it all is still an ordinary ignored packet.
            heartbeats = [
                event for event in self.events
                if event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 25
            ]
            self.assertEqual(len(heartbeats), 1)
            self.assertEqual(heartbeats[0]["classification"], "ok_no_response")
            self.assertEqual(heartbeats[0]["reason"], "client online heartbeat")
            # No further response follows: both requests got their one reply.
            stream.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses],
            [5, 14, 8, 10, 255, 256, 9, 89, 89],
        )

    def test_battle_end_is_answered_once_per_request_and_keeps_connection(self) -> None:
        # TASK-0155.  RUN-0048 put C2S 67 on the wire after the client settled
        # its own local battle, and the stub -- which had no registration for it
        # -- answered `unknown protocol id 67` and closed the connection
        # (server.log:439/440).  This is a LOCK-SEND of the same family as C2S 69
        # (pkgBattleProtoDef row 7 names `battlePveGameOverEnd` as the unlock of
        # both `battlePveGameOver` and `battlePveGameOver_svrVer`), so the
        # contract asserted here:
        #   * one request -> exactly ONE reply, S2C 96, on the same connection;
        #   * the 2 values are the profile's, and neither is derived from the
        #     request (the chapter branch reads neither slot);
        #   * the connection stays usable afterwards;
        #   * the unregistered neighbours (C2S 68, S2C 97) still fail closed.
        writer = WriterState()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("战斗结算道友".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            first_request = encode_packet(
                C2S_REGISTRY.by_id(67), BATTLE_GAME_OVER_RUN0048_FIELDS, writer
            )
            stream.sendall(first_request.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 6 + MAIL_LOGIN_PACKETS
            )
            second_request = encode_packet(
                C2S_REGISTRY.by_id(67), BATTLE_GAME_OVER_RUN0048_FIELDS, writer
            )
            stream.sendall(second_request.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 7 + MAIL_LOGIN_PACKETS
            )
            # Liveness probe: the same socket must still carry a later heartbeat,
            # i.e. answering the battle end may not close anything.
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(heartbeat.wire)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("protocol_id") == 25 for event in self.events
            ):
                time.sleep(0.01)

            # Every assertion below is made while the client still owns this
            # socket: the `tcp_disconnected` that appears after this block is
            # this test's own close, never the stub's.
            self.assertFalse(any(
                event.get("event") == "tcp_disconnected" for event in self.events
            ))
            self.assertFalse(any(
                event.get("classification") in {"unsupported_protocol",
                                                "malformed_packet"}
                for event in self.events
            ))
            self.assertFalse(any(
                event.get("event") == "tcp_error" for event in self.events
            ))

            # TASK-0242: the v21 base segment is 7 packets, so the two S2C 89
            # replies land on indices 7 and 8 (was 5 and 6).
            first_reply, second_reply = responses[7], responses[8]
            for reply in (first_reply, second_reply):
                self.assertEqual(reply.spec.protocol_id, 96)
                self.assertEqual(reply.spec.name, "battlePveGameOverEnd")
                self.assertEqual(reply.spec.fmt, "Na")
                self.assertEqual(reply.fields, (0, b""))
                self.assertEqual(reply.consumed, len(reply.wire))
                self.assertEqual(len(reply.wire), 9)
            # Nothing in the reply is a function of the request: two requests get
            # the same two values, unlike the S2C 89 counter.  (The wire BYTES
            # differ, because the rolling key and the sequence advance -- what is
            # constant is the decoded value set.)
            self.assertEqual(first_reply.fields, second_reply.fields)

            received = [
                event for event in self.events
                if event.get("event") == "tcp_packet_received"
                and event.get("protocol_id") == 67
            ]
            self.assertEqual(len(received), 2)
            self.assertEqual(received[0]["protocol_name"], "battlePveGameOver")
            # The RUN-0048 vector, on this connection's own writer state: the
            # client's own packed result is recorded verbatim as text, never
            # interpreted and never answered from.  (Unlike the battle-start
            # vector, one of the `a` slots here is NOT empty, so the event log's
            # own `_text` spelling -- decode, replace -- is what to compare with.)
            self.assertEqual(
                received[0]["decoded_fields"],
                [
                    value.decode("utf-8", errors="replace")
                    if isinstance(value, bytes) else value
                    for value in BATTLE_GAME_OVER_RUN0048_FIELDS
                ],
            )
            self.assertEqual(received[0]["decoded_fields"][0], 86)
            self.assertFalse(any(
                event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 67
                for event in self.events
            ))
            sent = [
                event for event in self.events
                if event.get("event") == "tcp_packet_sent"
                and event.get("protocol_id") == 96
            ]
            self.assertEqual(len(sent), 2)
            segment = next(
                event for event in self.events
                if event.get("event") == "tcp_login_sync_segment_sent"
            )
            for event in sent:
                self.assertEqual(
                    event["response_protocol"], "battlePveGameOverEnd"
                )
                self.assertEqual(
                    event["classification"],
                    "profile_backed_battle_game_over_reply",
                )
                self.assertEqual(len(event["decoded_fields"]), 2)
                self.assertEqual(event["role_id"], segment["role_id"])
                self.assertEqual(event["profile_id"], PROFILE_ID)
                self.assertEqual(event["lock_partner"], "battlePveGameOver")
                self.assertEqual(event["unlock_only_for"], "battlePveGameOver")
                self.assertEqual(
                    event["reply_trigger"], "one_reply_per_client_request"
                )
            replied = [
                event for event in self.events
                if event.get("event") == "tcp_battle_game_over_replied"
            ]
            self.assertEqual(len(replied), 2)
            for event in replied:
                self.assertEqual(event["reply_fields"], [0, ""])
                self.assertEqual(event["reply_field_names"],
                                 ["(unnamed) 1", "(unnamed) 2"])
                self.assertEqual(
                    event["field_origins"],
                    {"(unnamed) 1": "synthetic_compatibility",
                     "(unnamed) 2": "synthetic_compatibility"},
                )
                self.assertEqual(
                    event["unlock_names"],
                    ["battlePveGameOver", "battlePveGameOver_svrVer"],
                )
                self.assertEqual(event["answered_request"], "battlePveGameOver")
                self.assertEqual(event["historical_ordering"], "unknown")
                self.assertIs(event["historical_values_recovered"], False)
                self.assertEqual(event["profile_id"], PROFILE_ID)
                self.assertEqual(
                    event["policy_compatibility_version"], PROFILE_ID_V12
                )
                # The event reports the ACTIVE stream's ancestry; the active
                # profile inherits the v12 policy through its own parent chain,
                # so the parent is the ACTIVE profile's parent, not a frozen
                # historical version.
                self.assertEqual(
                    event["parent_profile_id"], get_profile().parent_profile_id
                )
                # The request is recorded whole -- all 21 values, including the
                # client's own packed result string -- and none of it is echoed.
                self.assertEqual(len(event["request_fields"]), 21)
            self.assertEqual(replied[0]["requests_answered"], 1)
            self.assertEqual(replied[1]["requests_answered"], 2)
            self.assertIs(replied[0]["repeated_request"], False)
            self.assertIs(replied[1]["repeated_request"], True)
            # The heartbeat after it all is still an ordinary ignored packet.
            heartbeats = [
                event for event in self.events
                if event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 25
            ]
            self.assertEqual(len(heartbeats), 1)
            self.assertEqual(heartbeats[0]["classification"], "ok_no_response")
            # No further response follows: both requests got their one reply.
            stream.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses],
            [5, 14, 8, 10, 255, 256, 9, 96, 96],
        )

    def test_unregistered_battle_end_neighbours_still_fail_closed(self) -> None:
        """D-18/D-08: only the row RUN-0048 really sent is registered.

        C2S 68 `battlePveGameOver_svrVer` is the server-driven variant (a battle
        this archive has no producer for) and S2C 97 `battlePveGameOverError` makes
        the client dispatch FIGHT_ERROR_RETURN instead of settling the battle.
        Neither is registered, so both must still take the unknown-id path --
        `unknown protocol id` plus the closed connection -- exactly as C2S 67
        itself did before this slice.
        """

        writer = WriterState()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("隔壁邻居道友".encode("utf-8"), 1), writer
        )
        # C2S 68 is 'NN': 3-byte header + 8 bytes, NOT the 245-byte 67 frame.
        # The frame is built by hand because the id has no registration to encode
        # through -- which is exactly the point of the test -- using the codec's
        # own header rule (`le16(id ^ key) || seq ^ key`).
        key_before = writer.key
        neighbour = (
            struct.pack("<H", 68 ^ (key_before & 0xFFFF))
            + bytes([writer.sequence ^ (key_before & 0xFF)])
            + struct.pack("<II", 0, 0)
        )
        writer.advance(68)

        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5
            )
            stream.sendall(bytes(neighbour))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("event") == "tcp_error" for event in self.events
            ):
                time.sleep(0.01)

        errors = [
            event for event in self.events if event.get("event") == "tcp_error"
        ]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["classification"], "malformed_packet")
        self.assertEqual(errors[0]["message"], "unknown protocol id 68")
        self.assertEqual(errors[0]["protocol_id"], 68)
        self.assertTrue(any(
            event.get("event") == "tcp_disconnected" for event in self.events
        ))

    def test_s2c89_reply_reaches_a_real_event_log_and_keeps_the_connection(
        self,
    ) -> None:
        # RUN-0044, on the device: the client's own `C2S 69 netEnterPveBattle`
        # arrived at `2026-10-16T18:46:25.988Z` (`server.log:494`), the stub put
        # the S2C 89 reply on the wire, and the NEXT statement -- the
        # `tcp_packet_sent` record of that reply -- raised `TypeError: Object of
        # type bytes is not JSON serializable / when serializing dict item 's1'`
        # (`server.log:540-542`).  The raise left `handle()`, so the connection
        # died in that same second (`logcat.txt:17091` `--> 服务器连接断开`, then
        # the client's own modal "network disconnected" prompt) and every later
        # C2S went unanswered.
        #
        # The offending value is the reply's `s1`: position 17 is a
        # `request_echo` of the `a`-typed C2S 69 field, which the codec decodes
        # as `bytes` (`b""` in RUN-0044).  This test runs the same exchange with
        # the production log path attached (a real stream, so `json.dumps` really
        # runs) and holds it to what the RUN needed: the reply is logged, the log
        # is JSON, `s1` is spelled the way the received request already spells the
        # same byte, and the connection stays up for the next heartbeat.
        log_stream = self._restart_with_a_real_log()
        writer = WriterState()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("战斗日志道友".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            request = encode_packet(
                C2S_REGISTRY.by_id(69), BATTLE_START_FIELDS, writer
            )
            stream.sendall(request.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 6 + MAIL_LOGIN_PACKETS
            )
            # Liveness probe, exactly as in the test above: the reply may not
            # have closed anything, so the same socket must still take a
            # heartbeat and have it answered (ignored, no lock).
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(heartbeat.wire)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("protocol_id") == 25 for event in self.events
            ):
                time.sleep(0.01)
            # Made while the client still owns this socket: the only
            # `tcp_disconnected` this session may produce is this test's own
            # close, which happens after the block.
            self.assertFalse(any(
                event.get("event") == "tcp_disconnected" for event in self.events
            ))
            self.assertFalse(any(
                event.get("event") == "tcp_error" for event in self.events
            ))
            self.assertEqual(responses[7].spec.protocol_id, 89)

        # The production property the RUN lost: every event that reached the sink
        # also reached the log, as one line each, and each line is JSON.
        lines = [json.loads(line) for line in log_stream.getvalue().splitlines()]
        self.assertEqual(len(lines), len(self.events))

        def logged(event: str, **criteria) -> dict:
            matches = [
                line for line in lines
                if line.get("event") == event
                and all(line.get(key) == value for key, value in criteria.items())
            ]
            self.assertEqual(len(matches), 1)
            return matches[0]

        request_record = logged("tcp_packet_received", protocol_id=69)
        sent_record = logged("tcp_packet_sent", protocol_id=89)
        # The answer event names the request it answers rather than carrying a
        # bare `protocol_id` of its own.
        replied_record = logged("tcp_battle_start_replied", request_protocol_id=69)
        self.assertEqual(replied_record["response_protocol_id"], 89)
        # Both directions spell the byte string the same way: the received
        # request's own `decoded_fields` (the long-standing convention) and the
        # reply's `decoded_fields`/`reply_fields` that echo it.
        self.assertEqual(request_record["decoded_fields"][-1], "")
        self.assertEqual(sent_record["decoded_fields"]["s1"], "")
        self.assertEqual(replied_record["reply_fields"][16], "")
        self.assertEqual(replied_record["reply_field_names"][16], "s1")
        # The field is still there -- spelled as text, never dropped.
        self.assertEqual(len(sent_record["decoded_fields"]), 19)
        self.assertEqual(
            sent_record["classification"], "profile_backed_battle_start_reply"
        )
        self.assertEqual(replied_record["reply_count"], 1)
        self.assertEqual(replied_record["lock_partner"], "netEnterPveBattle")
        self.assertIs(replied_record["branch_is_use_sf_svr"], False)
        # ... and the heartbeat after the reply is still an ordinary ignored packet.
        heartbeats = [
            line for line in lines
            if line.get("event") == "tcp_packet_ignored"
            and line.get("protocol_id") == 25
        ]
        self.assertEqual(len(heartbeats), 1)
        self.assertEqual(heartbeats[0]["classification"], "ok_no_response")

    def test_no_event_that_reaches_a_real_log_carries_raw_bytes(self) -> None:
        # The general guard the RUN-0044 crash asks for: an event record carrying
        # `bytes` cannot be written, and the raise closes the connection under it
        # -- so no event of any session may carry one, and no event may reach the
        # sink without reaching the log.  The session below drives the families a
        # chapter-map journey produces (login, create, the policy segment, the
        # client-reload unlock, the story chain, the battle start, heartbeats)
        # through the production log path with the forensic hex dump on.
        log_stream = self._restart_with_a_real_log()
        writer = WriterState()
        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("日志守卫道友".encode("utf-8"), 1), writer
            )
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            self.clock_now = SEND_NOW_UNIX
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            self._read_responses(
                stream, response_buffer, response_state, responses,
                14 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS,
            )
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(4), (False,), writer).wire
            )
            self._read_responses(
                stream, response_buffer, response_state, responses,
                15 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS,
            )
            stream.sendall(
                b"".join(
                    encode_packet(C2S_REGISTRY.by_id(233), (story_id,), writer).wire
                    for story_id in (1008, 1100)
                )
                + encode_packet(
                    C2S_REGISTRY.by_id(69), BATTLE_START_FIELDS, writer
                ).wire
                + encode_packet(C2S_REGISTRY.by_id(25), (), writer).wire
            )
            self._read_responses(
                stream, response_buffer, response_state, responses,
                16 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS,
            )
            self.assertEqual(responses[-1].spec.protocol_id, 89)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("protocol_id") == 25 for event in self.events
            ):
                time.sleep(0.01)
            self.assertFalse(any(
                event.get("event") in {"tcp_disconnected", "tcp_error"}
                for event in self.events
            ))

        lines = [json.loads(line) for line in log_stream.getvalue().splitlines()]
        self.assertEqual(len(lines), len(self.events))
        self.assertTrue(any(
            line.get("event") == "tcp_packet_sent"
            and line.get("protocol_id") == 89
            for line in lines
        ))
        for event in self.events:
            self.assertIsNone(self._raw_bytes_path(event), event.get("event"))

    def test_client_reload_request_gets_one_empty_unlock_and_keeps_connection(self) -> None:
        # TASK-0015: C2S 720 receiveClientReload 'N' is the lock-send the client's
        # S2C 13 enterGameSuccess handler provokes unconditionally.  The
        # compatibility policy answers with exactly one EMPTY S2C 1487
        # initClientReloadEnd (the only unlock) and keeps the connection open:
        # no 1485/1486/1488, no Lua payload, no synthetic reload info.
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("热重载道友".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            self.clock_now = SEND_NOW_UNIX
            receive_package = encode_packet(
                C2S_REGISTRY.by_id(3), (1, False), writer
            )
            stream.sendall(receive_package.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 14 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            )

            # The real client's S2C 13 handler: enterGame(4) -> C2S 720(1).
            enter_game = encode_packet(C2S_REGISTRY.by_id(4), (False,), writer)
            reload_request = encode_packet(C2S_REGISTRY.by_id(720), (1,), writer)
            stream.sendall(enter_game.wire + reload_request.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 19 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            )
            self.assertEqual(responses[14 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS].spec.protocol_id, 13)
            base = MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            self.assertEqual(responses[15 + base].spec.protocol_id, 1487)
            self.assertEqual(responses[15 + base].spec.name, "initClientReloadEnd")
            self.assertEqual(responses[15 + base].fields, ())
            self.assertEqual(len(responses[15 + base].wire), 3)
            # TASK-0428: the reload completion is also the first point at which the
            # client's main city is up, so the stub appends the 封妖 prime here
            # (239/246/244).  pkgFairyWayMgr.openFairyWay reads dLayerId before any
            # C2S exists, so without this push 活动 -> 封妖 -> 进入 dies on a nil
            # comparison and sends nothing.
            self.assertEqual([responses[15 + base + i].spec.protocol_id for i in (1, 2, 3)], [239, 246, 244])
            # Exactly one completion per request: no other reload-family member
            # (1485/1486/1488) follows the unlock.
            stream.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

            # A repeated/paged request gets its own single completion, and the
            # connection is still alive (the heartbeat after it is decoded).
            stream.settimeout(2)
            repeat = encode_packet(C2S_REGISTRY.by_id(720), (2,), writer)
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(repeat.wire + heartbeat.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 23 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS
            )
            self.assertEqual(
                responses[19 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS].spec.protocol_id, 1487
            )
            self.assertEqual(responses[19 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS].fields, ())
            stream.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses],
            [5, 14, 8, 10, 255, 256, 9, 8, 554, 27, 0, 487, 9]
            + TERMINAL_SEGMENT_PROTOCOL_IDS
            + [411, *PRISON_FIGHT_SNAPSHOT_PROTOCOL_IDS, 283, 555, *BIT_RABBIT_STATE_PROTOCOL_IDS,
               13, 1487, 239, 246, 244, 1487, 239, 246, 244],
        )
        self.assertEqual(
            [packet.sequence for packet in responses],
            list(range(23 + MAIL_LOGIN_PACKETS + POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS)),
        )
        for previous, current in zip(responses, responses[1:]):
            self.assertEqual(previous.key_after, current.key_before)
        received = [
            (event["protocol_id"], event["sequence"])
            for event in self.events
            if event.get("event") == "tcp_packet_received"
        ]
        self.assertEqual(
            received, [(0, 0), (2, 1), (3, 2), (4, 3), (720, 4), (720, 5), (25, 6)]
        )
        # No unsupported family member ever leaves the stub, and no D-08 error
        # is emitted for the once-unknown id 720.
        sent_names = {
            event.get("protocol_name")
            for event in self.events
            if event.get("event") == "tcp_packet_sent"
        }
        self.assertFalse(
            {"initClientReloadStart", "initClientReloadInfo", "addClientReload"}
            & sent_names
        )
        self.assertFalse(
            any(event.get("event") == "tcp_error" for event in self.events)
        )
        completions = [
            event for event in self.events
            if event.get("event") == "tcp_client_reload_completion_sent"
        ]
        self.assertEqual(
            [event["requested_reload_index"] for event in completions], [1, 2]
        )
        for event in completions:
            self.assertEqual(event["profile_id"], PROFILE_ID)
            self.assertEqual(event["request_protocol_id"], 720)
            self.assertEqual(event["request_protocol_name"], "receiveClientReload")
            self.assertEqual(event["response_protocol_id"], 1487)
            self.assertEqual(event["response_protocol_name"], "initClientReloadEnd")
            self.assertEqual(event["response_wire_length"], 3)
            self.assertEqual(
                event["reload_policy"], "minimal_empty_reload_completion"
            )
            self.assertEqual(
                event["reload_policy_origin"], "synthetic_compatibility"
            )
            self.assertFalse(event["historical_reload_script_recovered"])
            self.assertFalse(event["reload_start_sent"])
            self.assertFalse(event["reload_info_sent"])
            self.assertFalse(event["add_reload_sent"])
            self.assertTrue(event["response_wire_sha256"])
        unlock_sent = [
            event for event in self.events
            if event.get("event") == "tcp_packet_sent"
            and event.get("protocol_name") == "initClientReloadEnd"
        ]
        self.assertEqual(len(unlock_sent), 2)
        for event in unlock_sent:
            self.assertEqual(
                event["classification"], "compatibility_empty_reload_completion"
            )

    def test_v1_profile_transaction_omits_server_time(self) -> None:
        # Inheritance guard: a v1-profile server must replay the pre-v2 wire
        # order exactly; S2C 0 exists only in the v2 ServerTime policy.
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        v1_server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            compatibility_profile=get_profile_v1(),
            time_source=lambda: SERVER_STARTED_UNIX,
        )
        thread = threading.Thread(target=v1_server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("v1-replay".encode("utf-8"), 1), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(v1_server.server_address, timeout=2) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5
                )
                receive_package = encode_packet(
                    C2S_REGISTRY.by_id(3), (1, False), writer
                )
                stream.sendall(receive_package.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 10
                )
            self.assertEqual(
                [packet.spec.protocol_id for packet in responses],
                [5, 14, 8, 10, 9, 8, 554, 27, 9, 283],
            )
            self.assertFalse(any(
                event.get("event") == "tcp_server_time_sent" for event in events
            ))
        finally:
            v1_server.shutdown()
            v1_server.server_close()
            thread.join(timeout=2)

    def test_v2_profile_transaction_replays_v2_wire(self) -> None:
        # Inheritance guard: a v2-profile server must replay the exact v2 wire
        # frozen at 9b53f07 -- S2C0 present with its frozen hash, S2C487 absent.
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        clock_now = SERVER_STARTED_UNIX
        v2_server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            compatibility_profile=get_profile_v2(),
            time_source=lambda: clock_now,
        )
        thread = threading.Thread(target=v2_server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("v2-replay".encode("utf-8"), 1), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(v2_server.server_address, timeout=2) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5
                )
                clock_now = SEND_NOW_UNIX
                receive_package = encode_packet(
                    C2S_REGISTRY.by_id(3), (1, False), writer
                )
                stream.sendall(receive_package.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 11
                )
            self.assertEqual(
                [packet.spec.protocol_id for packet in responses],
                [5, 14, 8, 10, 9, 8, 554, 27, 0, 9, 283],
            )
            self.assertEqual(
                responses[8].fields, (SEND_NOW_UNIX, 0, 0, SERVER_STARTED_UNIX)
            )
            frozen_v2_hashes = {
                "serverTime": (
                    "2dcc839d1e9e637fd5ca3ac0b44461f2153fa03e057f6eb96b66dd2203ac9cbd"
                ),
                "returnSceneInfo": (
                    "57073c04cdc10a0e8b8aedafe7b9508616187239c5a981fb8460488cbf0f4c78"
                ),
            }
            by_name = {packet.spec.name: packet for packet in responses}
            for name, digest in frozen_v2_hashes.items():
                self.assertEqual(
                    hashlib.sha256(by_name[name].wire).hexdigest(), digest
                )
            self.assertFalse(any(
                packet.spec.protocol_id == 487 for packet in responses
            ))
            self.assertFalse(any(
                event.get("event") == "tcp_mystery_shop_state_sent" for event in events
            ))
        finally:
            v2_server.shutdown()
            v2_server.server_close()
            thread.join(timeout=2)

    def test_v3_profile_transaction_replays_v3_wire(self) -> None:
        # Inheritance guard (TASK-0035): a v3-profile server must replay the
        # exact v3 wire that was frozen while v3 was the active profile -- no
        # S2C411 story snapshot, and the v3 sequences/hashes below are the ones
        # committed in reports/compatibility_wire_audit.json for
        # cn-0.1.20.1-revival-v3.
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        clock_now = SERVER_STARTED_UNIX
        v3_server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            compatibility_profile=get_profile_v3(),
            time_source=lambda: clock_now,
        )
        thread = threading.Thread(target=v3_server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("v3-replay".encode("utf-8"), 1), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(v3_server.server_address, timeout=2) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5
                )
                clock_now = SEND_NOW_UNIX
                receive_package = encode_packet(
                    C2S_REGISTRY.by_id(3), (1, False), writer
                )
                stream.sendall(receive_package.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 12
                )
            self.assertEqual(
                [packet.spec.protocol_id for packet in responses],
                [5, 14, 8, 10, 9, 8, 554, 27, 0, 487, 9, 283],
            )
            frozen_v3_hashes = {
                "serverTime": (
                    8,
                    "2dcc839d1e9e637fd5ca3ac0b44461f2153fa03e057f6eb96b66dd2203ac9cbd",
                ),
                "refreshShopStart": (
                    9,
                    "fd6db9c5b431feb60f3ed5ad942b21ec527a0ca353a7ce57f2a274c1d7f50981",
                ),
                "returnSceneInfo": (
                    11,
                    "ee43bebdd2227fad0729bc13c21193dcfaf816d0a0cd5dd7f675efc028eb4e88",
                ),
            }
            by_name = {packet.spec.name: packet for packet in responses}
            for name, (sequence, digest) in frozen_v3_hashes.items():
                with self.subTest(protocol=name):
                    self.assertEqual(by_name[name].sequence, sequence)
                    self.assertEqual(
                        hashlib.sha256(by_name[name].wire).hexdigest(), digest
                    )
            self.assertFalse(any(
                packet.spec.protocol_id == 411 for packet in responses
            ))
            self.assertFalse(any(
                event.get("event") == "tcp_story_start_sent" for event in events
            ))
        finally:
            v3_server.shutdown()
            v3_server.server_close()
            thread.join(timeout=2)

    def test_v4_profile_transaction_replays_v4_wire(self) -> None:
        # Inheritance guard (TASK-0092): a v4-profile server must replay the
        # exact v4 wire that was frozen while v4 was the active profile -- no
        # Team snapshot at all, and the v4 sequences/hashes below are the ones
        # committed in reports/compatibility_wire_audit.json for
        # cn-0.1.20.1-revival-v4 before TASK-0092.  This is what "the old
        # protocols do not drift" means for a stateful rolling-key transport:
        # the packets *before* the insertion point stay byte-identical, and a
        # server that does not carry the v5 policy never inserts anything.
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        clock_now = SERVER_STARTED_UNIX
        v4_server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            compatibility_profile=get_profile_v4(),
            time_source=lambda: clock_now,
        )
        thread = threading.Thread(target=v4_server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("v4-replay".encode("utf-8"), 1), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(v4_server.server_address, timeout=2) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5
                )
                clock_now = SEND_NOW_UNIX
                receive_package = encode_packet(
                    C2S_REGISTRY.by_id(3), (1, False), writer
                )
                stream.sendall(receive_package.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 13
                )
            self.assertEqual(
                [packet.spec.protocol_id for packet in responses],
                [5, 14, 8, 10, 9, 8, 554, 27, 0, 487, 9, 411, 283],
            )
            frozen_v4_hashes = {
                "returnTalentSoulId": (
                    6,
                    "00d429111d7b96f4d376f3f23721e89bba047cd9e120f64359d68c3f36423804",
                ),
                "returnAllValue": (
                    7,
                    "94cb263839c105be620e286898375444da68640f3c99ad87ea507d02dbe55cda",
                ),
                "serverTime": (
                    8,
                    "2dcc839d1e9e637fd5ca3ac0b44461f2153fa03e057f6eb96b66dd2203ac9cbd",
                ),
                "refreshShopStart": (
                    9,
                    "fd6db9c5b431feb60f3ed5ad942b21ec527a0ca353a7ce57f2a274c1d7f50981",
                ),
                "returnStoryStart": (
                    11,
                    "829d9916206f9fa66e208f2c9ad74153d3aa2d6b8950c3628e5dd9c845a1f5a7",
                ),
                "returnSceneInfo": (
                    12,
                    "2ab949876b6e9f9c072b05904c3195bd6c3e7a0645d5509fdf8bbce5fe8735c1",
                ),
            }
            by_name = {packet.spec.name: packet for packet in responses}
            for name, (sequence, digest) in frozen_v4_hashes.items():
                with self.subTest(protocol=name):
                    self.assertEqual(by_name[name].sequence, sequence)
                    self.assertEqual(
                        hashlib.sha256(by_name[name].wire).hexdigest(), digest
                    )
            self.assertFalse(any(
                packet.spec.protocol_id in (35, 36, 37, 48)
                for packet in responses
            ))
            self.assertFalse(any(
                event.get("event") == "tcp_team_snapshot_sent" for event in events
            ))
        finally:
            v4_server.shutdown()
            v4_server.server_close()
            thread.join(timeout=2)

    def test_v5_profile_transaction_replays_v5_wire(self) -> None:
        # Inheritance guard (TASK-0094): a v5-profile server must replay the
        # exact v5 wire that was frozen while v5 was the active profile -- the
        # four Team packets, the v5 sequences and the v5 hashes, and NOT the
        # recharge-activity packet TASK-0094 added on top.  This is what "the
        # old profiles do not drift" means for a stateful rolling-key transport:
        # a server that does not carry the v6 policy inserts nothing, and the
        # packets *before* the new insertion point stay byte-identical.
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        clock_now = SERVER_STARTED_UNIX
        v5_server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            compatibility_profile=get_profile_v5(),
            time_source=lambda: clock_now,
        )
        thread = threading.Thread(target=v5_server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("v5-replay".encode("utf-8"), 1), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(v5_server.server_address, timeout=2) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5
                )
                clock_now = SEND_NOW_UNIX
                receive_package = encode_packet(
                    C2S_REGISTRY.by_id(3), (1, False), writer
                )
                stream.sendall(receive_package.wire)
                self._read_responses(
                    stream,
                    response_buffer,
                    response_state,
                    responses,
                    13 + TEAM_SNAPSHOT_PACKETS,
                )
            self.assertEqual(
                [packet.spec.protocol_id for packet in responses],
                [5, 14, 8, 10, 9, 8, 554, 27, 0, 487, 9]
                + TEAM_SNAPSHOT_PROTOCOL_IDS
                + [411, 283],
            )
            frozen_v5_hashes = {
                "returnTalentSoulId": (
                    6,
                    "00d429111d7b96f4d376f3f23721e89bba047cd9e120f64359d68c3f36423804",
                ),
                "returnAllValue": (
                    7,
                    "94cb263839c105be620e286898375444da68640f3c99ad87ea507d02dbe55cda",
                ),
                "serverTime": (
                    8,
                    "2dcc839d1e9e637fd5ca3ac0b44461f2153fa03e057f6eb96b66dd2203ac9cbd",
                ),
                "refreshShopStart": (
                    9,
                    "fd6db9c5b431feb60f3ed5ad942b21ec527a0ca353a7ce57f2a274c1d7f50981",
                ),
                # The four v5 Team packets: unchanged by TASK-0094.
                "returnTeamStart": (
                    11,
                    "87e32dc38a94f9f0de77d198bfe14d3d185357c9f2d74d3cdd530192f50489bd",
                ),
                "returnTeamInfo": (
                    12,
                    "dd0dbefa3a95449fc013526b692132071aa08059ce42e5acce7c77899757c063",
                ),
                "returnSelectMagicStart": (
                    13,
                    "7bd799278fbdf4f8c1b8ba3bb94f9c9c44a978dfa4e5c82dd52eeddbdbddae53",
                ),
                "returnGroupId": (
                    14,
                    "ea9175f4db425376372ead2ec36c840300f48bfa32fb75dba45240277dcb547c",
                ),
                "returnStoryStart": (
                    15,
                    "1fcef5665622744e29816f79a01b73b91b6d65b67140f8b84aaecb3e81c98e88",
                ),
                "returnSceneInfo": (
                    16,
                    "b8c5426ca6556cc428b82d7ab0ebb5daf4ff1142d6ed6a8297f05ded34ac436a",
                ),
            }
            by_name = {packet.spec.name: packet for packet in responses}
            for name, (sequence, digest) in frozen_v5_hashes.items():
                with self.subTest(protocol=name):
                    self.assertEqual(by_name[name].sequence, sequence)
                    self.assertEqual(
                        hashlib.sha256(by_name[name].wire).hexdigest(), digest
                    )
            self.assertFalse(
                any(
                    packet.spec.protocol_id == RECHARGE_ACTIVITY_PROTOCOL_IDS[0]
                    for packet in responses
                )
            )
            self.assertFalse(
                any(
                    event.get("event") == "tcp_recharge_activity_state_sent"
                    for event in events
                )
            )
        finally:
            v5_server.shutdown()
            v5_server.server_close()
            thread.join(timeout=2)

    def test_v12_profile_transaction_replays_v12_wire(self) -> None:
        # TASK-0167 inheritance guard: a v12-profile server carries no
        # PrisonFightBossSnapshot policy, so it must replay exactly the v12 login
        # stream -- every policy packet and S2C283, but NO S2C1370.  The whole
        # stream hash was MEASURED on this commit against the fixed clock, not
        # guessed; it is the value the old active profile produced before v13.
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        clock_now = SERVER_STARTED_UNIX
        v12_server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            compatibility_profile=get_profile_v12(),
            time_source=lambda: clock_now,
        )
        thread = threading.Thread(target=v12_server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("v12-replay".encode("utf-8"), 1), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(v12_server.server_address, timeout=2) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5
                )
                clock_now = SEND_NOW_UNIX
                receive_package = encode_packet(
                    C2S_REGISTRY.by_id(3), (1, False), writer
                )
                stream.sendall(receive_package.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses,
                    13 + V14_POLICY_PACKETS,
                )
            self.assertEqual(
                [packet.spec.protocol_id for packet in responses],
                [5, 14, 8, 10, 9, 8, 554, 27, 0, 487, 9]
                + V14_TERMINAL_SEGMENT_PROTOCOL_IDS
                + [411, 283],
            )
            self.assertNotIn(1370, [packet.spec.protocol_id for packet in responses])
            self.assertFalse(any(
                event.get("event") == "tcp_prison_fight_snapshot_sent"
                for event in events
            ))
            self.assertEqual(
                hashlib.sha256(
                    b"".join(packet.wire for packet in responses)
                ).hexdigest(),
                "a056b66a114029a9c09ce0ae4a47e47e6d522ee7374c804e608a509c2e6584ac",
            )
        finally:
            v12_server.shutdown()
            v12_server.server_close()
            thread.join(timeout=2)

    def test_v13_profile_transaction_replays_v13_wire(self) -> None:
        # TASK-0167: the active v13 stream.  It is the v12 stream plus exactly
        # one S2C1370 between S2C411 and S2C283, and the whole-stream hash below
        # was MEASURED on this commit against the fixed clock.  Every packet
        # before the insertion keeps its own bytes (and therefore its old hash);
        # S2C283/13/85 necessarily get new rolling keys.
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        clock_now = SERVER_STARTED_UNIX
        v13_server = RevivalTCPServer(
            ("127.0.0.1", 0),
            logger,
            compatibility_profile=get_profile_v13(),
            time_source=lambda: clock_now,
        )
        thread = threading.Thread(target=v13_server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), ("v13-replay".encode("utf-8"), 1), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(v13_server.server_address, timeout=2) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5
                )
                clock_now = SEND_NOW_UNIX
                receive_package = encode_packet(
                    C2S_REGISTRY.by_id(3), (1, False), writer
                )
                stream.sendall(receive_package.wire)
                self._read_responses(
                    stream, response_buffer, response_state, responses,
                    13 + V14_POLICY_PACKETS + PRISON_FIGHT_SNAPSHOT_PACKETS,
                )
            self.assertEqual(
                [packet.spec.protocol_id for packet in responses],
                [5, 14, 8, 10, 9, 8, 554, 27, 0, 487, 9]
                + V14_TERMINAL_SEGMENT_PROTOCOL_IDS
                + [411, *PRISON_FIGHT_SNAPSHOT_PROTOCOL_IDS, 283],
            )
            snapshot = next(
                packet for packet in responses if packet.spec.protocol_id == 1370
            )
            policy = get_profile_v13().prison_fight
            assert policy is not None
            self.assertEqual(snapshot.fields, policy.snapshot_fields())
            sent = [
                event for event in events
                if event.get("event") == "tcp_prison_fight_snapshot_sent"
            ]
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["profile_id"], PROFILE_ID_V13)
            self.assertEqual(
                hashlib.sha256(
                    b"".join(packet.wire for packet in responses)
                ).hexdigest(),
                "e72b43ab4644995137d22bdd5a80ec8aff241370b1b8059bce3e3354ba5952f3",
            )
        finally:
            v13_server.shutdown()
            v13_server.server_close()
            thread.join(timeout=2)

    def test_v17_login_stream_is_byte_for_byte_the_v16_stream(self) -> None:
        # TASK-0182: v17 changes only the request-triggered S2C 89 reply.  No
        # login policy is replaced, so a v17 server must replay the v16 login
        # segment packet for packet and byte for byte (the wire audit freezes
        # this too, but here the two profiles are compared directly).
        def login_segment(profile) -> tuple[list[int], bytes]:
            events: list[dict] = []
            logger = EventLogger(stream=None, sink=events.append)
            clock_now = [SERVER_STARTED_UNIX]
            server = RevivalTCPServer(
                ("127.0.0.1", 0),
                logger,
                compatibility_profile=profile,
                time_source=lambda: clock_now[0],
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                writer = WriterState()
                login = encode_packet(
                    C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
                )
                create = encode_packet(
                    C2S_REGISTRY.by_id(2), ("v17-login-replay".encode("utf-8"), 1),
                    writer,
                )
                response_buffer = bytearray()
                response_state = ReaderState()
                responses: list = []
                with socket.create_connection(
                    server.server_address, timeout=2
                ) as stream:
                    stream.settimeout(2)
                    stream.sendall(login.wire + create.wire)
                    self._read_responses(
                        stream, response_buffer, response_state, responses, 5,
                        registry=server.s2c_registry,
                    )
                    clock_now[0] = SEND_NOW_UNIX
                    stream.sendall(
                        encode_packet(
                            C2S_REGISTRY.by_id(3), (1, False), writer
                        ).wire
                    )
                    self._read_responses(
                        stream, response_buffer, response_state, responses,
                        14 + POLICY_PACKETS_BEFORE_V22
                        + PRISON_FIGHT_SNAPSHOT_PACKETS,
                        registry=server.s2c_registry,
                    )
                return (
                    [packet.spec.protocol_id for packet in responses],
                    b"".join(packet.wire for packet in responses),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        ids_v16, wire_v16 = login_segment(get_profile_v16())
        ids_v17, wire_v17 = login_segment(get_profile_v17())
        self.assertEqual(ids_v17, ids_v16)
        self.assertEqual(wire_v17, wire_v16)
        # ... and the one value that differs is not on the login path: the two
        # battle-start policies differ only at position 7.
        v16_policy = get_profile_v16().battle_start_reply
        v17_policy = get_profile_v17().battle_start_reply
        assert v16_policy is not None and v17_policy is not None
        self.assertEqual(v17_policy.reply_format, v16_policy.reply_format)
        self.assertEqual(
            [field.wire_type for field in v17_policy.fields],
            [field.wire_type for field in v16_policy.fields],
        )

    def _enter_game_with_story_snapshot(self, *, role_name: str) -> list:
        """Drive the v4 fresh-role transaction through the 千灯镇 story chain."""

        writer = WriterState()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(
            C2S_REGISTRY.by_id(2), (role_name.encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses,
                5 + MAIL_LOGIN_PACKETS,
            )
            self.clock_now = SEND_NOW_UNIX
            receive_package = encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer)
            stream.sendall(receive_package.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses,
                13 + MAIL_LOGIN_PACKETS + POLICY_PACKETS
                + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS,
            )
            # The client's own post-scene sends, in the recovered 千灯镇 order:
            # S2C13 provokes C2S720; story 1008/1100/1009 each send C2S233.
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(4), (False,), writer).wire
            )
            self._read_responses(
                stream, response_buffer, response_state, responses,
                15 + MAIL_LOGIN_PACKETS + POLICY_PACKETS
                + PRISON_FIGHT_SNAPSHOT_PACKETS + BIT_RABBIT_STATE_PACKETS,
            )
            story_packets = [
                encode_packet(C2S_REGISTRY.by_id(233), (story_id,), writer)
                for story_id in (1008, 1100, 1009)
            ]
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(
                b"".join(packet.wire for packet in story_packets) + heartbeat.wire
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("protocol_id") == 25 for event in self.events
            ):
                time.sleep(0.01)
            stream.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                stream.recv(1)
        return responses

    def test_finish_story_chain_is_recorded_without_response_and_keeps_connection(
        self,
    ) -> None:
        # TASK-0035 / D-42: with the empty story snapshot the fresh-role 千灯镇
        # chain is deterministic -- story 1008 (NPC 8) fires with no gate and its
        # first network action is C2S233 finishStory.  All three chain ids must be
        # decoded, recorded and left unanswered, and the connection must survive:
        # before this task every one of them was a D-08 malformed_packet +
        # disconnect (the RUN-0011 C2S720 failure class, moved to "story").
        responses = self._enter_game_with_story_snapshot(role_name="剧情道友")

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses],
            [5, 14, 8, 10, 255, 256, 9, 8, 554, 27, 0, 487, 9]
            + TERMINAL_SEGMENT_PROTOCOL_IDS
            + [411, *PRISON_FIGHT_SNAPSHOT_PROTOCOL_IDS, 283, 555, *BIT_RABBIT_STATE_PROTOCOL_IDS, 13],
        )
        self.assertFalse(any(event.get("event") == "tcp_error" for event in self.events))
        # Nothing resembling a story reply is ever sent.
        sent_names = {
            event.get("protocol_name")
            for event in self.events
            if event.get("event") == "tcp_packet_sent"
        }
        self.assertFalse(
            {"returnStory", "returnStoryEnd", "updateStoryStatus", "resetStory"}
            & sent_names
        )
        records = [
            event for event in self.events
            if event.get("event") == "tcp_story_finish_recorded"
        ]
        self.assertEqual([event["story_id"] for event in records], [1008, 1100, 1009])
        for event in records:
            self.assertEqual(event["protocol_id"], 233)
            self.assertEqual(event["protocol_name"], "finishStory")
            self.assertEqual(event["classification"], "ok_no_response")
            self.assertIs(event["response_sent"], False)
            self.assertIsNone(event["lock_partner"])
            self.assertIs(event["historical_persistence_recovered"], False)
            self.assertEqual(event["profile_id"], PROFILE_ID)
            self.assertTrue(event["connection_id"].startswith("conn-"))
        # TASK-0073: the recovered close chain makes this exact session the
        # first positive completion evidence, and only for the pair it proves.
        # 1008 opens its dialogue (arming, not finishing), 1100 is the story
        # 1008's own close callback chains into (so 1008's dialogue provably
        # closed), and 1009 is the successor of 1100 -- a pair this build does
        # NOT claim, so it stores nothing.
        first, second, third = records
        self.assertIsNone(first["story_completion_candidate_story_id"])
        self.assertEqual(
            first["story_completion_refusal"], "no_predecessor_c2s233"
        )
        self.assertIs(first["server_story_state_written"], False)
        self.assertEqual(first["persistence_policy"], "record_only_no_persistence")
        self.assertEqual(first["persistence_origin"], "synthetic_compatibility")
        self.assertEqual(second["story_completion_candidate_story_id"], 1008)
        self.assertIs(second["story_completion_recorded"], True)
        self.assertIs(second["server_story_state_written"], True)
        self.assertEqual(second["story_completion_story_id"], 1008)
        self.assertEqual(second["story_state"], 2)
        self.assertEqual(second["story_evidence_id"], 1100)
        self.assertEqual(second["persistence_policy"], "ordered_successor_evidence_only")
        self.assertEqual(
            second["persistence_origin"], "revival_local_c2s233_successor_evidence"
        )
        self.assertIs(second["historical_completion_time_recovered"], False)
        self.assertEqual(
            third["story_completion_refusal"], "no_recovered_successor_pair"
        )
        self.assertIs(third["server_story_state_written"], False)
        self.assertEqual(third["persistence_policy"], "record_only_no_persistence")
        # The decoded storyId is also on the ordinary receive event, so the
        # device log carries it without needing the new event name.
        decoded = [
            event["decoded_fields"]
            for event in self.events
            if event.get("event") == "tcp_packet_received"
            and event.get("protocol_id") == 233
        ]
        self.assertEqual(decoded, [[1008], [1100], [1009]])
        self.assertEqual(
            [event["sequence"] for event in records], [4, 5, 6]
        )

    def test_finish_guide_task_is_recorded_without_response_and_keeps_connection(
        self,
    ) -> None:
        # TASK-0148 / RUN-0045: after the S2C 89 battle start the client played a
        # real battle on its own and then sent C2S 232 finishGuideTask ('N', one
        # guideId).  The id was registered nowhere, so the stub answered it with
        # `malformed_packet` + `tcp_disconnected` (RUN-0045 server.log:421-422)
        # and the player saw 网络断开.  The contract asserted here is the
        # C2S 22/23/233 one -- decode + record + NO response + keep the connection
        # -- and, right beside it, that the D-08 unknown-id path is unchanged.
        writer = WriterState()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("指引道友".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            # The guide id RUN-0045's own packet carried, then a liveness probe:
            # the same socket must still decode a later heartbeat, i.e. this
            # packet may not close anything.
            guide_task = encode_packet(
                C2S_REGISTRY.by_id(232), (GUIDE_TASK_RUN0045_GUIDE_ID,), writer
            )
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(guide_task.wire + heartbeat.wire)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("protocol_id") == 25 for event in self.events
            ):
                time.sleep(0.01)

            # Every assertion below is made while the client still owns this
            # socket: the `tcp_disconnected` that appears after this block is
            # this test's own close, never the stub's.
            self.assertFalse(any(
                event.get("event") == "tcp_disconnected" for event in self.events
            ))
            self.assertFalse(any(
                event.get("event") == "tcp_error" for event in self.events
            ))
            self.assertFalse(any(
                event.get("classification") in {"unsupported_protocol", "malformed_packet"}
                for event in self.events
            ))

            received = [
                event for event in self.events
                if event.get("event") == "tcp_packet_received"
                and event.get("protocol_id") == 232
            ]
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["protocol_name"], "finishGuideTask")
            # The decoded guideId is on the ordinary receive event, so the device
            # log carries it without needing a new event name.
            self.assertEqual(
                received[0]["decoded_fields"], [GUIDE_TASK_RUN0045_GUIDE_ID]
            )

            ignored = [
                event for event in self.events
                if event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 232
            ]
            self.assertEqual(len(ignored), 1)
            self.assertEqual(ignored[0]["protocol_name"], "finishGuideTask")
            self.assertEqual(ignored[0]["classification"], "ok_no_response")
            self.assertEqual(
                ignored[0]["reason"],
                "recovered guidance-chain completion notification; "
                "no S2C response and no lock partner are registered",
            )
            # No response is reconstructed for C2S 232 and there is no lock to
            # release, so neither a reply nor a lock parther is recorded for it.
            self.assertFalse(any(
                event.get("event") == "tcp_packet_sent"
                and event.get("protocol_name") == "finishGuideTask"
                for event in self.events
            ))
            self.assertNotIn("lock_partner", ignored[0])
            self.assertNotIn("response_sent", ignored[0])

            # The probe after it is still an ordinary ignored heartbeat.
            heartbeats = [
                event for event in self.events
                if event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 25
            ]
            self.assertEqual(len(heartbeats), 1)
            self.assertEqual(heartbeats[0]["reason"], "client online heartbeat")

            # No further response follows: the whole session is the frozen
            # 5-packet login transaction.
            stream.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses], [5, 14, 8, 10, 255, 256, 9]
        )
        # Wire order on the reader side: login, create, the guide task, the probe.
        self.assertEqual(
            [
                (event["protocol_id"], event["sequence"])
                for event in self.events
                if event.get("event") == "tcp_packet_received"
            ],
            [(0, 0), (2, 1), (232, 2), (25, 3)],
        )

    def test_offline_chat_request_is_recorded_without_response_and_keeps_connection(
        self,
    ) -> None:
        # TASK-0249 / RUN-0078: with the bitRabbit Lua error gone (TASK-0247),
        # the client's main-city UI loading-complete callback
        # (`pkgMainUIMgr.lua:1234` -> `pkgFriendChatFileMgr.getOfflineChatData`,
        # PROTO 0.5 source 61-63) sent this packet for the first time.  Its whole
        # body is `c2s.getOfflineChatData(e_tbSocket)`: no payload, no callback,
        # no lock timer, no S2C wait.  The id was registered nowhere, so the
        # strict D-08 path answered `malformed_packet` + `tcp_disconnected`
        # (RUN-0078 server.log:605-609) and the main city that had just become
        # visible went unstable.
        #
        # Part 1 replays RUN-0078's own 3-byte frame against that connection's
        # reader state: `key_after` of C2S 720 (0x20997D02) with sequence 12, so
        # the next expected sequence is 13.  0x7DA0 ^ 0x7D02 = 162 and
        # 0x0F ^ 0x02 = 13 -- an aligned frame with an empty payload, not a
        # stream desync.
        state = ReaderState(key=0x20997D02, sequence=13)
        frame = bytes.fromhex("a0 7d 0f")
        packet = decode_packet(frame, C2S_REGISTRY, state)
        self.assertEqual(packet.spec.protocol_id, 162)
        self.assertEqual(packet.spec.name, "getOfflineChatData")
        self.assertEqual(packet.fields, ())
        self.assertEqual(packet.consumed, 3)
        self.assertEqual(packet.wire, frame)
        self.assertEqual(packet.sequence, 13)
        self.assertEqual(packet.key_before, 0x20997D02)
        self.assertEqual(state.sequence, 14)
        self.assertEqual(state.key, (0x20997D02 * 14 + 162) & 0xFFFFFFFF)

        # Part 2: the same contract on a live connection -- decode, record, no
        # S2C, no write, and the socket survives to decode the next packet.
        writer = WriterState()
        login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("离线道友".encode("utf-8"), 1), writer
        )
        response_buffer = bytearray()
        response_state = ReaderState()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            self._read_responses(
                stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS
            )
            # `_send_s2c_spec` writes the wire before it emits the event, so the
            # client can read the last packet before its send event exists.
            # Wait for the frozen transaction's event count before snapshotting.
            deadline = time.monotonic() + 2
            login_sent = 5 + MAIL_LOGIN_PACKETS
            while time.monotonic() < deadline and len([
                event for event in self.events
                if event.get("event") == "tcp_packet_sent"
            ]) < login_sent:
                time.sleep(0.005)
            # Every S2C of the frozen login transaction has already been sent.
            sent_before = [
                (event["protocol_id"], event["sequence"])
                for event in self.events
                if event.get("event") == "tcp_packet_sent"
            ]
            self.assertEqual(len(sent_before), login_sent)
            offline = encode_packet(C2S_REGISTRY.by_id(162), (), writer)
            # An empty recovered format is exactly the 3-byte header, the same
            # shape RUN-0078's reader saw.
            self.assertEqual(len(offline.wire), 3)
            # Liveness probe: the same socket must still decode a later packet.
            heartbeat = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
            stream.sendall(offline.wire + heartbeat.wire)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 25
                for event in self.events
            ):
                time.sleep(0.01)

            # Neither D-08 failure happened: the connection is intact.
            self.assertFalse(any(
                event.get("event") in {"tcp_error", "tcp_disconnected"}
                for event in self.events
            ))
            received = [
                event for event in self.events
                if event.get("event") == "tcp_packet_received"
                and event.get("protocol_id") == 162
            ]
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["protocol_name"], "getOfflineChatData")
            self.assertEqual(received[0]["decoded_fields"], [])
            self.assertEqual(received[0]["sequence"], 2)

            ignored = [
                event for event in self.events
                if event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 162
            ]
            self.assertEqual(len(ignored), 1)
            self.assertEqual(ignored[0]["protocol_name"], "getOfflineChatData")
            self.assertEqual(ignored[0]["classification"], "ok_no_response")
            self.assertEqual(
                ignored[0]["reason"],
                "recovered main-city offline-chat request; "
                "the client own registration has no S2C response",
            )
            self.assertNotIn("lock_partner", ignored[0])
            self.assertNotIn("response_sent", ignored[0])

            # No S2C is reconstructed for C2S 162, so the write side never
            # advanced: the frozen login transaction is still the whole set of
            # sent packets, with the same protocol ids and sequences.
            self.assertFalse(any(
                event.get("event") == "tcp_packet_sent"
                and event.get("protocol_id") == 162
                for event in self.events
            ))
            self.assertEqual(
                [
                    (event["protocol_id"], event["sequence"])
                    for event in self.events
                    if event.get("event") == "tcp_packet_sent"
                ],
                sent_before,
            )

            # The probe after it is an ordinary ignored heartbeat.
            heartbeats = [
                event for event in self.events
                if event.get("event") == "tcp_packet_ignored"
                and event.get("protocol_id") == 25
            ]
            self.assertEqual(len(heartbeats), 1)
            self.assertEqual(heartbeats[0]["reason"], "client online heartbeat")

            # No further response follows: the whole session is the frozen
            # 7-packet login transaction.
            stream.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                stream.recv(1)

        self.assertEqual(
            [packet.spec.protocol_id for packet in responses], [5, 14, 8, 10, 255, 256, 9]
        )
        # Wire order on the reader side: login, create, the offline-chat
        # request, the probe.
        self.assertEqual(
            [
                (event["protocol_id"], event["sequence"])
                for event in self.events
                if event.get("event") == "tcp_packet_received"
            ],
            [(0, 0), (2, 1), (162, 2), (25, 3)],
        )

    def test_unregistered_neighbour_of_the_guide_task_still_fails_closed(
        self,
    ) -> None:
        # TASK-0148, negative control: registering C2S 232 must not widen the
        # D-08 path.  231 chapterMapEventTest and 234 addFriend are recovered
        # registrations this archive has no business supporting, and they sit
        # immediately next to 232 in the client's own table -- so nothing about
        # the new registration may make them decode, be ignored, or keep the
        # connection alive.
        logger = EventLogger(stream=None, sink=self.events.append, hex_dump=True)
        server = RevivalTCPServer(("127.0.0.1", 0), logger)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for unknown_id in (231, 234):
                with self.subTest(unknown_id=unknown_id):
                    self.events.clear()
                    writer = WriterState()
                    login = encode_packet(
                        C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
                    )
                    create = encode_packet(
                        C2S_REGISTRY.by_id(2), (b"neighbour", 1), writer
                    )
                    # The connection's own reader state after those two packets:
                    # the client's writer and the stub's reader advance with the
                    # same key/sequence arithmetic, so this header can only be
                    # refused by the registry, never by a wrong sequence.
                    key, sequence = writer.key, writer.sequence
                    header = (
                        (unknown_id ^ (key & 0xFFFF)).to_bytes(2, "little")
                        + bytes([(sequence & 0xFF) ^ (key & 0xFF)])
                    )
                    with socket.create_connection(
                        ("127.0.0.1", server.server_address[1]), timeout=2
                    ) as stream:
                        stream.settimeout(2)
                        stream.sendall(login.wire + create.wire + header + b"\xAA\xBB")
                        deadline = time.monotonic() + 1
                        while time.monotonic() < deadline and not any(
                            event.get("event") == "tcp_disconnected"
                            for event in self.events
                        ):
                            time.sleep(0.01)
                    errors = [
                        event for event in self.events
                        if event.get("event") == "tcp_error"
                    ]
                    self.assertTrue(errors)
                    self.assertEqual(errors[0]["classification"], "malformed_packet")
                    self.assertEqual(errors[0]["protocol_id"], unknown_id)
                    self.assertEqual(errors[0]["raw_header_hex"], header.hex(" "))
                    # Nothing about the new registration made this id ignorable...
                    self.assertFalse(any(
                        event.get("event") == "tcp_packet_ignored"
                        and event.get("protocol_id") == unknown_id
                        for event in self.events
                    ))
                    # ... and the stub, not the client, closed the connection.
                    self.assertTrue(any(
                        event.get("event") == "tcp_disconnected"
                        for event in self.events
                    ))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_without_the_registration_run0045_s_frame_is_reproduced(self) -> None:
        # TASK-0148, the other half of the same fact: on a registry that lacks
        # the new registration the session dies exactly the way RUN-0045's did --
        # `malformed_packet` / "unknown protocol id 232" and then the stub's own
        # `tcp_disconnected` (server.log:421-422), which is what the player saw as
        # 网络断开.  The default registry no longer contains that id, so this is
        # the pre-task state rebuilt around it.
        base = ProtocolRegistry("C2S")
        for spec in C2S_REGISTRY:
            if spec.protocol_id == 232:
                continue
            base.register(spec.protocol_id, spec.name, spec.fmt)
        with self.assertRaises(KeyError):
            base.by_id(232)

        events: list[dict] = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append, hex_dump=True),
            c2s_registry=base,
            time_source=lambda: self.clock_now,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            writer = WriterState()
            login = encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer)
            create = encode_packet(
                C2S_REGISTRY.by_id(2), (b"base-registry", 1), writer
            )
            guide_task = encode_packet(
                C2S_REGISTRY.by_id(232), (GUIDE_TASK_RUN0045_GUIDE_ID,), writer
            )
            response_buffer = bytearray()
            response_state = ReaderState()
            responses: list = []
            with socket.create_connection(
                ("127.0.0.1", server.server_address[1]), timeout=2
            ) as stream:
                stream.settimeout(2)
                stream.sendall(login.wire + create.wire + guide_task.wire)
                # The login transaction is still answered in full...
                self._read_responses(
                    stream, response_buffer, response_state, responses, 5 + MAIL_LOGIN_PACKETS,
                    registry=server.s2c_registry,
                )
                # ... and then the stub closes the connection under the client,
                # which reads EOF instead of anything for the guide task.
                stream.settimeout(2)
                self.assertEqual(stream.recv(1), b"")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        errors = [event for event in events if event.get("event") == "tcp_error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["classification"], "malformed_packet")
        self.assertEqual(errors[0]["message"], "unknown protocol id 232")
        self.assertEqual(errors[0]["protocol_id"], 232)
        self.assertEqual(errors[0]["raw_buffer_length"], 7)
        self.assertEqual(errors[0]["raw_header_hex"], guide_task.wire[:3].hex(" "))
        self.assertFalse(any(
            event.get("event") == "tcp_packet_received" and event.get("protocol_id") == 232
            for event in events
        ))
        self.assertFalse(any(
            event.get("event") == "tcp_packet_ignored" and event.get("protocol_id") == 232
            for event in events
        ))
        self.assertTrue(any(
            event.get("event") == "tcp_disconnected" for event in events
        ))

    def test_story_start_snapshot_is_one_empty_packet_before_the_scene_switch(
        self,
    ) -> None:
        # TASK-0035 / D-42 + profile v4: exactly ONE S2C411 per transaction, and
        # from v13 (TASK-0167) the prison-fight snapshot follows it and S2C283 --
        # the packet whose dispatch starts the main-city switch -- is the last
        # packet.  Historical ordering is unknown; this is an explicit
        # synthetic compatibility ordering, and the event must say so.
        responses = self._enter_game_with_story_snapshot(role_name="快照道友")

        stories = [packet for packet in responses if packet.spec.protocol_id == 411]
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0].spec.name, "returnStoryStart")
        self.assertEqual(stories[0].fields, ())
        self.assertEqual(len(stories[0].wire), 3)  # empty format: header only
        # TASK-0242: the two mail containers precede the terminal S2C9, so the
        # whole post-login block is two sequences later.
        self.assertEqual(stories[0].sequence, 13 + POLICY_PACKETS)
        snapshot = next(
            packet for packet in responses if packet.spec.protocol_id == 1370
        )
        scene = next(
            packet for packet in responses if packet.spec.protocol_id == 283
        )
        # Adjacency is the point: the story snapshot is followed by exactly the
        # v13 prison-fight snapshot, and nothing but the scene packet follows
        # that (TASK-0167 inserted it after the story state and before S2C283).
        self.assertEqual(snapshot.sequence, stories[0].sequence + 1)
        self.assertEqual(snapshot.key_before, stories[0].key_after)
        self.assertEqual(scene.sequence, snapshot.sequence + 1)
        self.assertEqual(scene.key_before, snapshot.key_after)

        event = next(
            event for event in self.events
            if event.get("event") == "tcp_story_start_sent"
        )
        self.assertEqual(event["protocol_id"], 411)
        self.assertEqual(event["protocol_name"], "returnStoryStart")
        self.assertEqual(event["profile_id"], PROFILE_ID)
        # TASK-0098 moved the active profile to v7, but the story snapshot is
        # still the v4 policy carried forward unchanged: its own provenance
        # version stays v4, and the serving profile's parent is whatever the
        # previous version is (the event reports the stream, not the policy).
        self.assertEqual(event["parent_profile_id"], get_profile().parent_profile_id)
        self.assertEqual(event["policy_compatibility_version"], PROFILE_ID_V4)
        self.assertEqual(event["story_list_policy"], "empty")
        self.assertEqual(event["story_list_policy_origin"], "synthetic_compatibility")
        self.assertEqual(event["initialises_client_container"], "le_story_list")
        self.assertEqual(event["story_rows_sent"], 0)
        self.assertEqual(event["historical_ordering"], "unknown")
        self.assertEqual(event["ordering_origin"], "synthetic_compatibility")
        self.assertFalse(event["historical_values_recovered"])
        self.assertTrue(event["ordering_reason"])
        # The snapshot is a labelled assertion, never a recovered value.
        policy = get_profile().story_start
        assert policy is not None
        self.assertIn(
            "NOT a recovered historical snapshot", policy.snapshot_provenance.reason
        )

    def test_profile_rejects_unapproved_career_without_success_response(self) -> None:
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
        )
        create = encode_packet(
            C2S_REGISTRY.by_id(2), ("未批准职业".encode("utf-8"), 2), writer
        )
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(login.wire + create.wire)
            response_buffer = bytearray()
            response_state = ReaderState()
            responses = []
            self._read_responses(
                stream, response_buffer, response_state, responses, 1
            )
            self.assertEqual(responses[0].spec.name, "noRole")
            self.assertEqual(stream.recv(16), b"")

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not any(
            event.get("classification") == "unsupported_compatibility_career"
            for event in self.events
        ):
            time.sleep(0.01)
        error = next(
            event for event in self.events
            if event.get("classification") == "unsupported_compatibility_career"
        )
        self.assertEqual(error["career"], 2)
        self.assertEqual(error["profile_id"], PROFILE_ID)
        self.assertFalse(any(
            event.get("response_protocol") == "returnCreateRoleResult"
            for event in self.events
        ))

    def test_unknown_protocol_error_keeps_header_and_optional_buffer(self) -> None:
        logger = EventLogger(stream=None, sink=self.events.append, hex_dump=True)
        server = RevivalTCPServer(("127.0.0.1", 0), logger)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            key = 0xFD987193
            unknown_id = 99
            encrypted_id = unknown_id ^ (key & 0xFFFF)
            header = encrypted_id.to_bytes(2, "little") + bytes((key & 0xFF,))
            with socket.create_connection(server.server_address, timeout=2) as stream:
                stream.sendall(header + b"\xAA\xBB")
                stream.shutdown(socket.SHUT_WR)
                stream.recv(16)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not any(
                event.get("event") == "tcp_error" for event in self.events
            ):
                time.sleep(0.01)
            error = next(event for event in self.events if event.get("event") == "tcp_error")
            self.assertEqual(error["protocol_id"], unknown_id)
            self.assertEqual(error["raw_header_hex"], header.hex(" "))
            self.assertEqual(error["raw_buffer_hex"], (header + b"\xAA\xBB").hex(" "))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

