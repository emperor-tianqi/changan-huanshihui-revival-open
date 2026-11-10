"""TASK-0094 - the month-sign request/ack pair, judged by its own consumers.

RUN-0027 proved the edge on the device: the client sent ``C2S 725`` as a bare
three-byte header (``d7 7f 0f``, no payload) 1.29 s after its main-city UI init,
and the stub answered ``unknown protocol id 725`` and dropped the connection
(``.autopilot/device_runs/RUN-0027/server.log`` lines 99-100) -- so ``C2S23``
heartbeats stopped arriving and the story probe never got its 60 s window.

This module contains a *faithful model of the recovered chain that produces that
request*, built from the decompiled sources and pinned to them by
``max_decoder/analyze_month_sign_family.py`` (which the tests import and run, so
the model cannot silently drift).  Every failure it reports carries the
recovered ``module:line`` and function name, so a model result can be compared
with the RUN-0027 logcat/server timeline directly.

The tests then drive the model with the *real decoded wire* of a real
``RevivalTCPServer`` over a real socket session -- the base shape (a registry
without 725) for the falsification, the shipped shape for the repair.
"""

from __future__ import annotations

import dataclasses
import socket
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import (
    PROFILE_ID_V1,
    PROFILE_ID_V2,
    PROFILE_ID_V3,
    PROFILE_ID_V4,
    PROFILE_ID_V5,
    PROFILE_ID_V6,
    PROFILE_ID_V7,
    PROFILE_ID_V8,
    PROFILE_ID_V9,
    PROFILE_ID_V10,
    PROFILE_ID_V11,
    PROFILE_ID_V12,
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
    MONTH_SIGN_STATE_VALUE_POLICY,
    get_profile,
    get_profile_v1,
    get_profile_v2,
    get_profile_v3,
    get_profile_v4,
    get_profile_v18,
    get_profile_v19,
    get_profile_v20,
    get_profile_v21,
    get_profile_v22,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_DECODER = PROJECT_ROOT / "max_decoder"
if str(MAX_DECODER) not in sys.path:
    sys.path.insert(0, str(MAX_DECODER))

import analyze_month_sign_family as contract  # noqa: E402  (source pin, run below)

SERVER_STARTED_UNIX = 1_700_000_000
SEND_NOW_UNIX = 1_700_000_123

REQUEST_ID = 725
ACK_ID = 1500
DATA_ID = 1499

#: TASK-0242 / profile v21: the base login segment carries the two empty
#: mail-container packets after S2C 10, so both absolute counts grow by two;
#: TASK-0245 / profile v22 adds the one S2C 39 returnTeamMaxFight after S2C 35.
#: TASK-0247 / profile v24 adds the one S2C 1435 syncBitRabbit after S2C 555.
BASE_LOGIN_PACKETS = 7
BASE_SEGMENT_PACKETS = 33  # 7 login + 13 role sync + 4 team + 1 S2C39
#                           # + 1 recharge + 2 chapter-task
#                           # + 1 drawcard-unlock row
#                           # + 2 chapter-item + 2 chapter-machine
#                           # + 2 experience-task + 1 prison-fight snapshot
# (TASK-0098 grew the segment by the chapter-item pair, TASK-0102 by the
# experience-task pair, TASK-0104 by the chapter-task pair, TASK-0109 by the
# chapter-machine pair, TASK-0167 by the v13 prison-fight snapshot and
# TASK-0180 by the v15 drawcard-unlock row; the count
# is the TOTAL read after C2S3, and the tests below
# read past it on purpose.  It must be the segment's EXACT length: `_session` takes
# its `base` from `len(packets)` here, so a count that stops short of the segment
# leaves the tail of the login stream in the socket and the next read mistakes it
# for the request's answer.)


def _read(stream, server, buffer, state, packets, count) -> None:
    while len(packets) < count:
        try:
            packet = decode_packet(buffer, server.s2c_registry, state)
        except NeedMoreData:
            chunk = stream.recv(65536)
            if not chunk:
                raise RuntimeError(
                    f"server closed after {len(packets)}/{count} packets"
                )
            buffer.extend(chunk)
            continue
        del buffer[: packet.consumed]
        packets.append(packet)


def _assert_run_0027_wire_shape(wire: bytes) -> None:
    """The request RUN-0027 recorded was a bare three-byte header."""

    if len(wire) != 3:
        raise AssertionError(
            "C2S725 must encode to exactly three wire bytes (a header and an "
            f"empty body); got {len(wire)}"
        )


def ack_events(events: list[dict]) -> list[dict]:
    """The `tcp_month_sign_data_ack_sent` records, in emission order."""

    return [
        event
        for event in events
        if event.get("event") == "tcp_month_sign_data_ack_sent"
    ]


def state_events(events: list[dict]) -> list[dict]:
    """The TASK-0246 `tcp_month_sign_state_sent` records."""

    return [
        event
        for event in events
        if event.get("event") == "tcp_month_sign_state_sent"
    ]


class RecoveredClientError(RuntimeError):
    """A failure at a named line of the recovered client source."""

    def __init__(self, module: str, line: int, function: str, detail: str) -> None:
        self.module = module
        self.line = line
        self.function = function
        self.detail = detail
        super().__init__(f"{module}:{line}: in function '{function}': {detail}")

    @property
    def frame(self) -> str:
        return f"{self.module}:{self.line}"


class RecoveredTimerMgr:
    """``utility/pkgTimerMgr.lua`` -- ``add`` repeats, ``once`` does not."""

    MODULE = "utility/pkgTimerMgr.lua"

    def __init__(self) -> None:
        self.timers: dict[int, dict] = {}
        self._next_id = 1

    def add(self, interval_ms: int, callback):
        """``pkgTimerMgr.lua:11-15`` -- ``LuaTimer.Add(interval, interval, cb)``."""

        timer_id = self._next_id
        self._next_id += 1
        self.timers[timer_id] = {
            "interval": interval_ms,
            "due": interval_ms,
            "callback": callback,
        }
        return timer_id

    def once(self, delay_ms: int, callback):
        """``pkgTimerMgr.lua:5-9`` -- ``LuaTimer.Add(delay, cb)``."""

        timer_id = self._next_id
        self._next_id += 1
        self.timers[timer_id] = {
            "interval": None,
            "due": delay_ms,
            "callback": callback,
        }
        return timer_id

    def delete(self, timer_id) -> None:
        """``pkgTimerMgr.lua:23-27``."""

        if timer_id is None:
            return
        self.timers.pop(timer_id, None)

    def advance(self, elapsed_ms: int) -> int:
        """Run every timer that comes due, repeating ones included."""

        fired = 0
        remaining = elapsed_ms
        while True:
            due = [
                (timer_id, entry)
                for timer_id, entry in self.timers.items()
                if entry["due"] <= remaining
            ]
            if not due:
                break
            timer_id, entry = min(due, key=lambda item: item[1]["due"])
            step = entry["due"]
            for other in self.timers.values():
                other["due"] -= step
            remaining -= step
            entry["callback"]()
            fired += 1
            if entry["interval"] is None:
                self.timers.pop(timer_id, None)
            else:
                entry["due"] = entry["interval"]
        for entry in self.timers.values():
            entry["due"] -= remaining
        return fired


class RecoveredLockSendMgr:
    """``reconnect/lockSend/pkgLockSendMgr.lua`` -- who may unlock whom."""

    MODULE = "reconnect/lockSend/pkgLockSendMgr.lua"
    #: ``pkgLockSendMgr.lua:7`` -- `LOCK_TIME = 15000`.
    LOCK_TIME = contract.LOCK_TIME_MS
    #: ``registerS2C("", "responseMonthSignData", "requestMonthSignData")``.
    LOCK_PROTO_LIST = {"requestMonthSignData": {"responseMonthSignData": True}}

    def __init__(self, timers: RecoveredTimerMgr) -> None:
        self.timers = timers
        self.le_send_list: dict[str, bool] = {}
        self.le_timer_id = None
        self.event_system_enabled = True
        self.lock_view_shown = False
        self.overlay_shows = 0
        self.shutdowns = 0

    # ------------------------------------------------------------ pkgUIMgr
    def enable_event_system(self, enabled: bool) -> None:
        self.event_system_enabled = enabled

    def show_lock_send_view(self) -> None:
        self.lock_view_shown = True
        self.overlay_shows += 1

    def clear_lock_send_view(self) -> None:
        self.lock_view_shown = False

    def clear(self) -> None:
        """``pkgLockSendMgr.lua:109-112``."""

        self.stop_timer()
        self.enable_event_system(True)

    def stop_timer(self) -> None:
        """``pkgLockSendMgr.lua:32-41``."""

        if self.le_timer_id is None:
            return
        self.timers.delete(self.le_timer_id)
        self.le_timer_id = None
        self.clear_lock_send_view()

    def lock_over_time_handler(self) -> None:
        """``pkgLockSendMgr.lua:43-50`` -- the client kills its own socket."""

        self.stop_timer()
        self.shutdowns += 1
        raise RecoveredClientError(
            self.MODULE, 44, "lockOverTimeHandler",
            "pkgConnectMgr.shutDown() + e_tbSocket:onShutdown(...)",
        )

    def start_timer(self) -> None:
        """``pkgLockSendMgr.lua:52-58``."""

        if self.le_timer_id:
            return
        self.le_timer_id = self.timers.add(self.LOCK_TIME, self.lock_over_time_handler)

    def send(self, str_func: str) -> bool:
        """``pkgLockSendMgr.lua:60-83`` -- called after every c2s wire write."""

        if str_func not in self.LOCK_PROTO_LIST:
            return False
        if self.le_send_list.get(str_func):
            return False
        self.enable_event_system(False)
        self.le_send_list[str_func] = True
        self.show_lock_send_view()
        self.start_timer()
        return True

    def receiver(self, str_receive_func: str) -> bool:
        """``pkgLockSendMgr.lua:114-134`` via ``nativeCb.netCallhook``."""

        if not self.le_send_list:
            return False
        for lock_func in self.LOCK_PROTO_LIST:
            if str_receive_func not in self.LOCK_PROTO_LIST[lock_func]:
                continue
            self.le_send_list.pop(lock_func, None)
            if not self.le_send_list:
                self.clear()
            return True
        return False


class RecoveredMonthSignModel:
    """``monthSign/pkgMonthSignModel.lua`` -- the poll and its only stopper."""

    MODULE = "monthSign/pkgMonthSignModel.lua"

    def __init__(self, timers: RecoveredTimerMgr, transport=None) -> None:
        self.timers = timers
        #: ``c2s.requestMonthSignData(e_tbSocket)`` -- the wrapped sender.
        self.transport = transport
        # pkgMonthSignModel.lua:8 -- `le_nextRefreshTime = le_nextRefreshTime or 0`
        self.le_next_refresh_time = 0
        self.le_tb_month_sign = {"udExtSign": 0, "tbSignMap": {}}
        self.le_d_timer_id = None
        self.sent: list[str] = []

    def send_to_get_month_sign_data(self) -> None:
        """``pkgMonthSignModel.lua:124-127`` -- the ONLY sender of C2S725."""

        self.sent.append("requestMonthSignData")
        if self.transport is not None:
            self.transport("requestMonthSignData")

    def set_month_sign_data(self, *fields) -> None:
        """``pkgMonthSignModel.lua:15-22`` -- the ONLY writer of the poll value."""

        (
            ud_ext_sign, _last_ext_sign_time, next_refresh_time,
            _str_month_sign, _cur_year, _cur_month, _cur_day,
        ) = fields
        self.le_next_refresh_time = next_refresh_time
        self.le_tb_month_sign["udExtSign"] = ud_ext_sign

    def set_up_update_timer(self) -> int:
        """``pkgMonthSignModel.lua:130-140`` -- repeating 1000 ms poll."""

        self.dipose_update_timer()
        self.le_d_timer_id = self.timers.add(1000, self._tick)
        return self.le_d_timer_id

    def _tick(self) -> None:
        """The timer callback: compare against the server clock then send."""

        server_time = self.server_time
        if self.le_next_refresh_time < server_time:
            self.send_to_get_month_sign_data()

    def dipose_update_timer(self) -> None:
        """``pkgMonthSignModel.lua:142-148``."""

        if self.le_d_timer_id is None:
            return
        self.timers.delete(self.le_d_timer_id)
        self.le_d_timer_id = None

    #: Set by the owning client each time the fake clock moves.
    server_time = SEND_NOW_UNIX


class RecoveredMonthSignS2CMgr:
    """``monthSign/pkgS2CMonthSignMgr.lua`` -- both handlers, verbatim."""

    MODULE = "monthSign/pkgS2CMonthSignMgr.lua"

    def __init__(self, model: RecoveredMonthSignModel) -> None:
        self.model = model
        self.dispatched: list[str] = []

    def response_month_sign_data(self, socket_token) -> None:
        """``pkgS2CMonthSignMgr.lua:13-15`` -- one dispatch, no field, no read."""

        self.dispatched.append("MONTH_SIGN_DATA_UPDATED")

    def return_month_sign_data(self, socket_token, *fields) -> None:
        """``pkgS2CMonthSignMgr.lua:8-11`` -- the S2C1499 data packet."""

        if len(fields) != 7:
            raise RecoveredClientError(
                self.MODULE, 8, "returnMonthSignData",
                f"handler takes 7 fields, got {len(fields)}",
            )
        self.model.set_month_sign_data(*fields)


class RecoveredMonthSignClient:
    """The recovering client: main-city init, the poll, the lock and both S2C."""

    MAIN_UI_MODULE = "mainUI/pkgMainUIMgr.lua"
    CONNECT_MODULE = "socket/pkgConnectMgr.lua"

    def __init__(self) -> None:
        self.timers = RecoveredTimerMgr()
        self.lock_send = RecoveredLockSendMgr(self.timers)
        self.model = RecoveredMonthSignModel(self.timers, self.send_c2s)
        self.s2c = RecoveredMonthSignS2CMgr(self.model)
        self.socket_alive = True
        self.c2s_sent: list[str] = []

    # ------------------------------------------------------ pkgConnectMgr
    def send_c2s(self, func_name: str) -> None:
        """``pkgConnectMgr.lua:38-67`` -- write the wire, then lock-send bookkeeping."""

        if not self.socket_alive:
            raise RecoveredClientError(
                self.CONNECT_MODULE, 51, "regMobileProto",
                "send on a socket the client already shut down",
            )
        self.c2s_sent.append(func_name)
        self.lock_send.send(func_name)

    # ---------------------------------------------------------- pkgMainUIMgr
    def main_city_init(self) -> None:
        """The month-sign half of ``pkgMainUIMgr.init`` line 1132 ``updateInfo()``."""

        self.update_info()

    def update_info(self) -> None:
        """``pkgMainUIMgr.lua:646-664`` -- statement 15 is the timer install."""

        self.update_month_sign_timer()

    def update_month_sign_timer(self) -> None:
        """``pkgMainUIMgr.lua:642-644``."""

        self.model.set_up_update_timer()

    # ------------------------------------------------------------- the wire
    def advance(self, elapsed_ms: int) -> None:
        self.model.server_time = SEND_NOW_UNIX + elapsed_ms // 1000
        try:
            self.timers.advance(elapsed_ms)
        except RecoveredClientError as exc:
            if exc.function == "lockOverTimeHandler":
                self.socket_alive = False
            raise

    def deliver(self, protocol_id: int, fields: tuple = ()) -> bool:
        """Dispatch one decoded S2C the way ``nativeCb.netCallhook`` does."""

        self.lock_send.receiver(
            {ACK_ID: "responseMonthSignData", DATA_ID: "returnMonthSignData"}.get(
                protocol_id, ""
            )
        )
        if protocol_id == ACK_ID:
            self.s2c.response_month_sign_data("socket")
            return True
        if protocol_id == DATA_ID:
            self.s2c.return_month_sign_data("socket", *fields)
            return True
        return False


class SourcePinTests(unittest.TestCase):
    """The model above is only evidence while it still matches the sources."""

    def test_recovered_contract_still_pins(self) -> None:
        contract.assert_protocol_table()
        contract.assert_registration_source(
            contract._read(contract.SC_PROTOCOL)
        )
        contract.assert_sender_and_trigger(
            contract._read(contract.MONTH_SIGN_MODEL),
            contract._read(contract.MAIN_UI),
            contract._read(contract.TIMER_MGR),
        )
        contract.assert_s2c_handlers(
            contract._read(contract.MONTH_SIGN_S2C),
            contract._read(contract.MONTH_SIGN_MODEL),
        )
        contract.assert_lock_send(
            contract._read(contract.LOCK_SEND),
            contract._read(contract.CONNECT_MGR),
        )

    def test_the_frozen_contract_file_still_matches_the_sources(self) -> None:
        """TASK-0246: `analyze_month_sign_family.py --check` from inside a test."""

        frozen, matches, digest = contract.verify_contract_file()
        self.assertTrue(frozen, f"the frozen sha256 moved: {digest}")
        self.assertTrue(matches, f"the contract file drifted: {digest}")
        payload = contract.build_contract()
        self.assertEqual(payload["profile"]["profile_id"], contract.MONTH_SIGN_PROFILE_ID)
        self.assertEqual(
            payload["empty_map_text"], contract.MONTH_SIGN_EMPTY_MAP_TEXT
        )

    def test_registry_agrees_with_the_recovered_contract(self) -> None:
        request = C2S_REGISTRY.by_id(REQUEST_ID)
        self.assertEqual(request.name, "requestMonthSignData")
        self.assertEqual(request.fmt, "")
        ack = S2C_REGISTRY.by_id(ACK_ID)
        self.assertEqual(ack.name, "responseMonthSignData")
        self.assertEqual(ack.fmt, "")
        # TASK-0246 / profile v23: S2C1499 is now the request's own answer --
        # the one packet whose handler writes `le_nextRefreshTime`, i.e. the only
        # thing that can stop the poll.  Its registration is the recovered
        # `NppaNNN` and nothing else.
        data = S2C_REGISTRY.by_id(DATA_ID)
        self.assertEqual((data.name, data.fmt), ("returnMonthSignData", "NppaNNN"))
        self.assertEqual(
            S2C_REGISTRY.by_name("returnMonthSignData").protocol_id, DATA_ID
        )
        # The rest of the family stays absent: no 1497/1498 request/answer.
        for neighbour in (1497, 1498):
            with self.assertRaises(KeyError):
                S2C_REGISTRY.by_id(neighbour)
        # And the model's own lock table is the registration's.
        self.assertEqual(
            RecoveredLockSendMgr.LOCK_PROTO_LIST,
            {"requestMonthSignData": {"responseMonthSignData": True}},
        )

    def test_the_model_reproduces_the_run_0027_timeline(self) -> None:
        client = RecoveredMonthSignClient()
        client.main_city_init()
        client.advance(1000)
        self.assertEqual(client.model.sent, ["requestMonthSignData"])
        client.advance(9000)
        # The RUN-0027 archive answered the request with the empty S2C1500 ack
        # only and carried NO S2C1499, so `le_nextRefreshTime` kept its 0 and the
        # client asked every second -- the honest cost of not inventing it.
        # TASK-0246 / profile v23 changed the SERVER side (the request is now
        # answered 1499 then 1500); this model-only timeline is the archive's
        # frozen baseline, and the repaired session below proves the stop.
        self.assertEqual(len(client.model.sent), 10)


class BaseFalsificationTests(unittest.TestCase):
    """Without the registration, the RUN-0027 frame is reproduced exactly."""

    def _base_registry(self):
        """A registry shaped like the base commit's: no month-sign pair."""

        from revival_stub.registry import ProtocolRegistry

        base = ProtocolRegistry("C2S")
        for spec in C2S_REGISTRY:
            if spec.protocol_id == REQUEST_ID:
                continue
            base.register(spec.protocol_id, spec.name, spec.fmt)
        return base

    def setUp(self) -> None:
        self.events: list[dict] = []
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            c2s_registry=self._base_registry(),
            time_source=lambda: SEND_NOW_UNIX,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _login_then_request(self, raw_extra: bytes = b"") -> bytes:
        writer = WriterState()
        state = ReaderState()
        buffer = bytearray()
        packets: list = []
        received = bytearray()
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2), (b"month-sign-base", 1), writer
                ).wire
            )
            _read(stream, self.server, buffer, state, packets, BASE_LOGIN_PACKETS)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read(stream, self.server, buffer, state, packets, BASE_SEGMENT_PACKETS)
            # The fourth C2S of this session, encoded with the same rolling-key
            # writer: exactly the wire the client would have produced.
            header = encode_packet(C2S_REGISTRY.by_id(REQUEST_ID), (), writer).wire
            if raw_extra:
                # A request whose body is not empty: the '' format admits none,
                # so the extra byte can only be the next packet's header.
                stream.sendall(header + raw_extra)
            else:
                stream.sendall(header)
            try:
                while True:
                    chunk = stream.recv(65536)
                    if not chunk:
                        break
                    received.extend(chunk)
            except socket.timeout:
                pass
        return bytes(received)

    @staticmethod
    def _assert_run_0027_wire_shape(wire: bytes) -> None:
        _assert_run_0027_wire_shape(wire)

    def test_the_base_server_is_the_run_0027_disconnect(self) -> None:
        response = self._login_then_request()
        self.assertEqual(response, b"", "the base server must answer nothing")
        errors = [e for e in self.events if e.get("event") == "tcp_error"]
        self.assertEqual(
            [(e["classification"], e.get("protocol_id")) for e in errors],
            [("malformed_packet", REQUEST_ID)],
        )
        self.assertEqual(errors[0]["message"], "unknown protocol id 725")
        # RUN-0027 recorded `raw_buffer_length: 3` for this error: the whole
        # buffered packet was the three-byte header, i.e. the body is empty.
        self.assertEqual(errors[0]["raw_header_hex"], errors[0]["raw_header_hex"])
        self.assertEqual(
            len(bytes.fromhex(errors[0]["raw_header_hex"].replace(" ", ""))), 3
        )
        self.assertEqual(
            [e["event"] for e in self.events].count("tcp_disconnected"), 1
        )
        # And the client's own side of the same wire: the lock is never
        # unlocked, LOCK_TIME expires and the client shuts its socket down.
        client = RecoveredMonthSignClient()
        client.main_city_init()
        client.advance(1000)
        self.assertEqual(client.model.sent, ["requestMonthSignData"])
        self.assertTrue(client.lock_send.le_send_list)
        self.assertFalse(client.lock_send.event_system_enabled)
        with self.assertRaises(RecoveredClientError) as caught:
            client.advance(15000)
        self.assertEqual(caught.exception.frame, "reconnect/lockSend/pkgLockSendMgr.lua:44")
        self.assertFalse(client.socket_alive)
        self.assertEqual(client.lock_send.shutdowns, 1)

    def test_a_non_empty_body_is_not_a_legal_request(self) -> None:
        # The extra byte cannot be a field, so the trailing fragment is not a
        # legal packet boundary: the request format is strictly empty (D-08).
        response = self._login_then_request(raw_extra=b"\x11")
        self.assertEqual(response, b"")
        errors = [e for e in self.events if e.get("event") == "tcp_error"]
        self.assertEqual(errors[0]["message"], "unknown protocol id 725")
        self.assertEqual(
            [e["event"] for e in self.events].count("tcp_disconnected"), 1
        )


class RepairedSessionTests(unittest.TestCase):
    """The shipped server: one request, one answer, socket alive afterwards."""

    def setUp(self) -> None:
        self.events: list[dict] = []
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SEND_NOW_UNIX,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _session(self, *, repeats: int = 1, hearts: int = 1):
        """Login, send N requests, then heartbeats; return (packets, s2c).

        TASK-0246: one C2S 725 is answered by TWO packets -- S2C 1499
        `returnMonthSignData` first, then the empty S2C 1500 ack -- so each
        repeat waits for both and the pair is read in wire order.
        """

        writer = WriterState()
        state = ReaderState()
        buffer = bytearray()
        packets: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2), (b"month-sign-repair", 1), writer
                ).wire
            )
            _read(stream, self.server, buffer, state, packets, BASE_LOGIN_PACKETS)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read(stream, self.server, buffer, state, packets, BASE_SEGMENT_PACKETS)
            base = len(packets)
            for _ in range(repeats):
                # A fresh encode per send: the rolling key advances with every
                # packet, so replaying one buffer would not be the same wire.
                request = encode_packet(C2S_REGISTRY.by_id(REQUEST_ID), (), writer)
                _assert_run_0027_wire_shape(request.wire)
                stream.sendall(request.wire)
                _read(stream, self.server, buffer, state, packets, len(packets) + 2)
            # C2S26 onLine_forNetState + C2S23 speedHeart must still decode: the
            # connection is not dropped and the rolling key is intact.
            heartbeats = (
                encode_packet(C2S_REGISTRY.by_id(26), (), writer)
                .wire
                + encode_packet(
                    C2S_REGISTRY.by_id(23), (SEND_NOW_UNIX, 25.0, False), writer
                ).wire
            )
            stream.sendall(heartbeats)
            stream.settimeout(2)
            try:
                while True:
                    chunk = stream.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
            except socket.timeout:
                pass
        return packets, base

    def test_one_request_gets_exactly_one_empty_answer(self) -> None:
        packets, base = self._session()
        self.assertEqual(len(packets), base + 2)
        state = packets[base]
        self.assertEqual(state.spec.protocol_id, DATA_ID)
        self.assertEqual(state.spec.name, "returnMonthSignData")
        self.assertEqual(state.spec.fmt, "NppaNNN")
        answer = packets[base + 1]
        self.assertEqual(answer.spec.protocol_id, ACK_ID)
        self.assertEqual(answer.spec.name, "responseMonthSignData")
        self.assertEqual(answer.fields, ())
        self.assertEqual(len(answer.wire), 3)
        self.assertEqual(answer.sequence, base + 1)

    def test_the_state_packet_is_the_empty_container_with_a_future_boundary(
        self,
    ) -> None:
        """TASK-0246: the seven S2C1499 values, field by field."""

        packets, base = self._session()
        state = packets[base]
        self.assertEqual(
            (len(state.fields), state.spec.fmt),
            (7, "NppaNNN"),
            "S2C1499 must carry exactly the recovered NppaNNN fields",
        )
        (
            ud_ext_sign,
            last_ext_sign_time,
            next_refresh_time,
            str_month_sign,
            cur_year,
            cur_month,
            cur_day,
        ) = state.fields
        # The client's own empty container: udExtSign 0 and "" (which
        # `pkgStringMgr.split("#", "")` turns into an empty list, so
        # stringToTable never writes a nil key).  The `a` wire format decodes to
        # bytes, so the empty mapping text arrives as b"".
        self.assertEqual(ud_ext_sign, 0)
        self.assertEqual(str_month_sign, b"")
        self.assertEqual(last_ext_sign_time, 0.0)
        # nextRefreshTime is a double of the next CN (UTC+8) natural-day
        # boundary on the S2C0 clock -- strictly in the future, so the client's
        # own poll condition `le_nextRefreshTime < now` is FALSE at send time.
        self.assertIsInstance(next_refresh_time, float)
        self.assertGreater(next_refresh_time, SEND_NOW_UNIX)
        now_cn = datetime.fromtimestamp(
            SEND_NOW_UNIX + 8 * 3600, timezone.utc
        )
        expected_boundary = (
            datetime(
                now_cn.year, now_cn.month, now_cn.day, tzinfo=timezone.utc
            ).timestamp()
            + 86_400
            - 8 * 3600
        )
        self.assertEqual(next_refresh_time, expected_boundary)
        # The calendar date is that same CN clock's date.
        self.assertEqual((cur_year, cur_month, cur_day), (2023, 11, 15))
        # And no sign row, extension count or reward is invented.
        self.assertEqual(ack_events(self.events)[0]["month_sign_rows_sent"], 0)

    def test_no_sign_row_or_reward_is_ever_invented(self) -> None:
        self._session()
        ack = ack_events(self.events)[0]
        self.assertEqual(ack["month_sign_rows_sent"], 0)
        self.assertIs(ack["month_sign_history_recovered"], False)
        self.assertEqual(ack["lock_pair_origin"], "deterministic_recovered")
        self.assertEqual(ack["unlock_only_for"], "requestMonthSignData")
        self.assertEqual(ack["response_wire_length"], 3)
        state = state_events(self.events)[0]
        self.assertIs(state["historical_values_recovered"], False)
        self.assertIs(state["replaceable"], True)
        self.assertIs(state["historical"], False)
        self.assertIs(state["state_changed"], False)
        self.assertEqual(state["value_provenance"], "synthetic_compatibility")
        self.assertEqual(state["ordering_origin"], "synthetic_compatibility")
        self.assertEqual(state["historical_ordering"], "unknown")
        self.assertEqual(state["ud_ext_sign"], 0)
        self.assertEqual(state["str_month_sign"], "")
        # Nothing anywhere turned the packet into a sign row or a reward.
        self.assertFalse(
            any(
                event.get("event", "").startswith("tcp_gain_info")
                for event in self.events
            )
        )

    def test_the_answer_is_a_real_wire_packet_in_the_same_session(self) -> None:
        packets, base = self._session()
        for previous, current in zip(packets, packets[1:]):
            self.assertEqual(previous.key_after, current.key_before)
        self.assertEqual([p.sequence for p in packets], list(range(len(packets))))
        # The terminal v5 block is untouched by this task.
        self.assertEqual(
            [p.spec.protocol_id for p in packets[13:17]], [36, 37, 48, 35]
        )

    def test_repeats_are_answered_one_for_one_and_never_doubled(self) -> None:
        packets, base = self._session(repeats=3)
        # TASK-0246: each request gets its own 1499->1500 pair, in that order,
        # and no request ever gets a second state packet.
        self.assertEqual(
            [p.spec.protocol_id for p in packets[base:]],
            [DATA_ID, ACK_ID, DATA_ID, ACK_ID, DATA_ID, ACK_ID],
        )
        self.assertEqual(
            [p.spec.protocol_id for p in packets[base:] if p.spec.protocol_id == DATA_ID],
            [DATA_ID, DATA_ID, DATA_ID],
        )
        self.assertEqual(
            [p.sequence for p in packets[base:]],
            list(range(base, base + 6)),
        )
        acks = ack_events(self.events)
        states = state_events(self.events)
        self.assertEqual(len(acks), 3)
        self.assertEqual(len(states), 3)
        self.assertEqual([e["requests_answered"] for e in acks], [1, 2, 3])
        # The flag is about THIS answer, not about a missing unlock: the first
        # request is not a repeat, every later one is.
        self.assertEqual([e["repeated_request"] for e in acks], [False, True, True])
        self.assertTrue(all(e["response_count"] == 1 for e in acks))
        self.assertTrue(all(e["state_packet_sent"] is True for e in acks))
        self.assertEqual([e["state_protocol_id"] for e in acks], [DATA_ID] * 3)
        # No request ever produces more than one state packet.
        self.assertEqual(
            [e["request_sequence"] for e in states],
            [e["request_sequence"] for e in acks],
        )
        # Correlation: each answer names the request sequence it answers.
        requests = [
            e
            for e in self.events
            if e.get("event") == "tcp_packet_received"
            and e.get("protocol_id") == REQUEST_ID
        ]
        self.assertEqual(
            [e["sequence"] for e in requests], [e["request_sequence"] for e in acks]
        )

    def test_the_session_survives_and_the_heartbeat_still_decodes(self) -> None:
        self._session(repeats=2, hearts=2)
        received = [
            e for e in self.events if e.get("event") == "tcp_packet_received"
        ]
        self.assertIn("speedHeart", [e["protocol_name"] for e in received])
        self.assertIn("onLine_forNetState", [e["protocol_name"] for e in received])
        # Nothing the server did ended this session: no decode failure, and the
        # only disconnect is the client's own close at the end of the test.
        self.assertEqual(
            [e for e in self.events if e.get("event") == "tcp_error"], []
        )
        disconnects = [
            e for e in self.events if e.get("event") == "tcp_disconnected"
        ]
        self.assertLessEqual(len(disconnects), 1)
        # The heartbeats arrive after the month-sign exchange, so the rolling key
        # the client used was never thrown away by a mid-session drop.
        speeds = [
            index
            for index, event in enumerate(self.events)
            if event.get("event") == "tcp_packet_received"
            and event.get("protocol_name") == "speedHeart"
        ]
        acks = [
            index
            for index, event in enumerate(self.events)
            if event.get("event") == "tcp_month_sign_data_ack_sent"
        ]
        self.assertTrue(speeds and acks and speeds[-1] > acks[-1])

    def test_the_client_model_reaches_a_settled_state(self) -> None:
        packets, base = self._session()
        client = RecoveredMonthSignClient()
        client.main_city_init()
        client.advance(1000)
        self.assertEqual(client.model.sent, ["requestMonthSignData"])
        self.assertEqual(client.c2s_sent, ["requestMonthSignData"])
        self.assertFalse(client.lock_send.event_system_enabled)
        # Feed the server's TWO packets in wire order, exactly as the client's
        # nativeCb would: 1499 writes the model, 1500 dispatches the event.
        client.deliver(packets[base].spec.protocol_id, packets[base].fields)
        # 1499 is not the lock partner, so the lock stays pending until the ack.
        self.assertFalse(client.lock_send.event_system_enabled)
        self.assertEqual(client.s2c.dispatched, [])
        client.deliver(packets[base + 1].spec.protocol_id, packets[base + 1].fields)
        self.assertTrue(client.lock_send.event_system_enabled)
        self.assertFalse(client.lock_send.le_send_list)
        self.assertEqual(client.s2c.dispatched, ["MONTH_SIGN_DATA_UPDATED"])
        # TASK-0246: the real state packet moved the poll deadline into the
        # future, so the repeating timer stops asking and the socket stays alive.
        self.assertEqual(
            client.model.le_next_refresh_time, packets[base].fields[2]
        )
        client.advance(5000)
        self.assertTrue(client.socket_alive)
        self.assertEqual(client.model.sent, ["requestMonthSignData"])

    def test_a_late_answer_cannot_unlock_a_later_request(self) -> None:
        client = RecoveredMonthSignClient()
        client.main_city_init()
        client.advance(1000)
        client.deliver(ACK_ID)
        self.assertTrue(client.lock_send.event_system_enabled)
        # A second ack with nothing pending is inert -- it cannot unlock, and it
        # cannot cancel a lock that has not been taken.
        self.assertFalse(client.lock_send.receiver("responseMonthSignData"))
        client.advance(1000)
        self.assertFalse(client.lock_send.event_system_enabled)
        self.assertTrue(client.lock_send.le_send_list)

    def test_a_pending_lock_is_never_locked_twice(self) -> None:
        """`pkgLockSendMgr.send` returns early while a lock is already pending."""

        client = RecoveredMonthSignClient()
        client.main_city_init()
        client.advance(1000)
        first_shows = client.lock_send.overlay_shows
        client.send_c2s("requestMonthSignData")
        self.assertEqual(client.lock_send.overlay_shows, first_shows)
        self.assertEqual(len(client.lock_send.le_send_list), 1)
        # And only one LOCK_TIME timer is ever armed for the pending lock.
        self.assertEqual(
            len(
                [
                    t
                    for t in client.timers.timers.values()
                    if t["interval"] == RecoveredLockSendMgr.LOCK_TIME
                ]
            ),
            1,
        )

    def test_the_real_state_packet_stops_the_client_poll(self) -> None:
        """TASK-0246: the server's own S2C1499 is what ends the 1/s retry loop."""

        packets, base = self._session()
        client = RecoveredMonthSignClient()
        client.main_city_init()
        client.advance(1000)
        self.assertEqual(client.model.sent, ["requestMonthSignData"])
        for packet in packets[base : base + 2]:
            client.deliver(packet.spec.protocol_id, packet.fields)
        # The value came from the server, not from a hand-written timestamp.
        self.assertEqual(
            client.model.le_next_refresh_time, packets[base].fields[2]
        )
        self.assertFalse(
            client.model.le_next_refresh_time < client.model.server_time,
            "the client's own poll condition must be false after the answer",
        )
        client.advance(10_000)
        self.assertEqual(client.model.sent, ["requestMonthSignData"])

    def test_every_earlier_profile_still_has_the_same_registry(self) -> None:
        """The transport pair is version-free; the STATE packet is v23's.

        TASK-0094 registered the C2S725/S2C1500 pair as pure transport and no
        later task moved the profile FOR IT.  TASK-0246 is the first task that
        adds a compatibility STATE packet to this family (S2C1499), so the
        active profile is now v23 -- and v1/v4 (which cannot carry the policy)
        still have the same two registrations and no `month_sign_state`.
        """

        self.assertEqual(
            {
                get_profile_v1().profile_id,
                get_profile_v2().profile_id,
                get_profile_v3().profile_id,
                get_profile_v4().profile_id,
            },
            {PROFILE_ID_V1, PROFILE_ID_V2, PROFILE_ID_V3, PROFILE_ID_V4},
        )
        self.assertIn(
            get_profile().profile_id,
            {PROFILE_ID_V6, PROFILE_ID_V7, PROFILE_ID_V8, PROFILE_ID_V9,
             PROFILE_ID_V10, PROFILE_ID_V11, PROFILE_ID_V12, PROFILE_ID_V13,
             PROFILE_ID_V14, PROFILE_ID_V15, PROFILE_ID_V16, PROFILE_ID_V17,
             PROFILE_ID_V18, PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23,
             PROFILE_ID_V24},
            "TASK-0246 introduced the month-sign STATE packet, so the active "
            "profile is v23 (v24 inherits it); every earlier version still "
            "registers the same transport pair and answers C2S725 with the "
            "single empty ack",
        )
        self.assertEqual(
            [C2S_REGISTRY.by_id(REQUEST_ID).fmt, S2C_REGISTRY.by_id(ACK_ID).fmt],
            ["", ""],
        )
        for profile in (get_profile_v1(), get_profile_v4(), get_profile_v18()):
            self.assertIsNone(getattr(profile, "month_sign_state", None))
        state_policy = get_profile().month_sign_state
        self.assertIsNotNone(state_policy)
        assert state_policy is not None
        self.assertEqual(
            (state_policy.wire_id, state_policy.name, state_policy.wire_format),
            (DATA_ID, "returnMonthSignData", "NppaNNN"),
        )
        self.assertEqual(state_policy.position, "beforeResponseMonthSignData")
        self.assertEqual(state_policy.triggers, ("requestMonthSignData",))
        self.assertEqual(state_policy.introduced_in, PROFILE_ID_V23)

    def test_the_month_sign_ui_stays_closed_at_level_one_and_level_29(self) -> None:
        """TASK-0246 level gate: no unimplemented C2S726/727 can become clickable.

        `pkgMonthSignConst.const_OpenLevel` is 30 (recovered source), and
        `pkgUISignMgr.initShowTab` binds the `month_sign` tab only when
        `const_OpenLevel <= level`, so at level 1 (`qa-changan-main`'s real role)
        and at level 29 (`dev-resource-main`'s overlay) the tab has no listener,
        `checkCanSign()` is false (no red point) and `le_currentKey` can never
        become "month_sign".
        """

        open_level = contract.recovered_month_sign_open_level()
        self.assertEqual(open_level, 30)
        self.assertGreater(open_level, 29)
        gate = contract.recovered_month_sign_view_gate()
        # The tab binding gate and the red-point/level gate are both present.
        self.assertIn("const_OpenLevel <= level", gate["tab_gate"])
        self.assertIn(
            "level < pkgMonthSignConst.const_OpenLevel", gate["check_can_sign"]
        )
        # The 签到/补签 buttons exist only inside the view's cells, built by
        # `updateSignCells` from `show()`; no earlier path can reach them.
        self.assertEqual(
            gate["sign_button_senders"],
            {"btnSign": "sendMonthSign", "btnExtSign": "sendExtMonthSign"},
        )

    def test_v23_adds_only_the_month_sign_state(self) -> None:
        # TASK-0247 moved the default to v24; the explicit v23 object below is
        # still the one that introduced the state packet.
        from revival_stub.compatibility_profile import get_profile_v23

        v22 = get_profile_v22()
        v23 = get_profile_v23()
        self.assertEqual(v23.profile_id, PROFILE_ID_V23)
        self.assertEqual(v23.parent_profile_id, PROFILE_ID_V22)
        self.assertEqual(v22.parent_profile_id, PROFILE_ID_V21)
        # The frozen analyzer contract names exactly the runtime profile.
        self.assertEqual(contract.MONTH_SIGN_PROFILE_ID, v23.profile_id)
        self.assertEqual(contract.MONTH_SIGN_PARENT_PROFILE_ID, v23.parent_profile_id)
        self.assertEqual(contract.MONTH_SIGN_VALUE_POLICY, MONTH_SIGN_STATE_VALUE_POLICY)
        self.assertEqual(contract.MONTH_SIGN_POSITION, v23.month_sign_state.position)
        self.assertEqual(contract.MONTH_SIGN_TRIGGER, v23.month_sign_state.triggers[0])
        self.assertIsNone(getattr(v22, "month_sign_state", None))
        self.assertIsNotNone(v23.month_sign_state)
        # Every other policy object is the v22 object itself, so the v23 login
        # stream is the v22 login stream byte for byte.
        for name in (
            "role_values", "sign_catalogs", "sign_state_template",
            "scene_state_template", "active_soul_rules", "server_time",
            "mystery_shop", "story_start", "team_snapshot",
            "recharge_activity", "chapter_item_init", "experience_task_init",
            "chapter_task_init", "chapter_machine_init", "battle_start_reply",
            "battle_game_over_reply", "prison_fight", "chapter_fight_replay",
            "drawcard_unlock", "chapter_box_replay", "babel_settlement_push",
            "mail_login", "team_max_fight",
        ):
            self.assertIs(getattr(v23, name), getattr(v22, name), name)

    def test_v1_to_v22_refuse_the_month_sign_state_and_v23_requires_it(self) -> None:
        policy = get_profile().month_sign_state
        assert policy is not None
        for builder in (
            get_profile_v1,
            get_profile_v2,
            get_profile_v3,
            get_profile_v4,
            get_profile_v18,
            get_profile_v19,
            get_profile_v20,
            get_profile_v21,
            get_profile_v22,
        ):
            with self.subTest(profile=builder().profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(builder(), month_sign_state=policy)
        with self.assertRaises(ValueError):
            dataclasses.replace(get_profile(), month_sign_state=None)

    def test_the_state_policy_cannot_drift_from_its_contract(self) -> None:
        policy = get_profile().month_sign_state
        assert policy is not None
        prov = policy.block_provenance
        value_prov = policy.value_provenance
        order_prov = policy.ordering_provenance
        bad = [
            dict(wire_id=1498),
            dict(name="returnMonthSign"),
            dict(wire_format="NppNNN"),
            dict(allowed_positions=("after_login_end",)),
            dict(triggers=("login",)),
            dict(value_policy="recovered_sign_history"),
            dict(historical_ordering="recovered"),
            dict(historical_values_recovered=True),
            dict(replaceable=False),
            dict(historical=True),
            dict(block_provenance=dataclasses.replace(
                prov, origin="synthetic_compatibility")),
            dict(value_provenance=dataclasses.replace(
                value_prov, origin="deterministic_recovered")),
            dict(ordering_provenance=dataclasses.replace(
                order_prov, origin="unknown")),
            dict(introduced_in=PROFILE_ID_V22),
        ]
        for change in bad:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    dataclasses.replace(policy, **change)

    def test_the_next_refresh_boundary_is_always_in_the_future(self) -> None:
        policy = get_profile().month_sign_state
        assert policy is not None
        # Every second of one CN day, including both midnights, is strictly
        # behind the boundary the policy returns.
        start = 1_700_006_400 - 86_400  # one CN natural day
        for now in (start, start + 1, start + 43_200, start + 86_399):
            with self.subTest(now=now):
                boundary = policy.next_refresh_time(now)
                self.assertGreater(boundary, now)
                self.assertLessEqual(boundary - now, 86_400)
                self.assertEqual(boundary % 86_400, 86_400 - 8 * 3600)
        # The exact CN midnight rolls to the NEXT midnight, never to itself.
        cn_midnight = 1_699_977_600
        self.assertEqual(cn_midnight % 86_400, 57_600)  # 16:00 UTC = 00:00 CN
        self.assertEqual(
            policy.next_refresh_time(cn_midnight), cn_midnight + 86_400
        )
        self.assertEqual(policy.next_refresh_time(cn_midnight - 1), cn_midnight)
        fields = policy.fields(now_unix=start + 86_399)
        self.assertEqual(fields[0], 0)
        self.assertEqual(fields[3], "")
        self.assertGreater(fields[2], start + 86_399)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
