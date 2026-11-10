"""TASK-0102 - the chapter detail panel's 确认 tap, and the 1 Hz tick that hid it.

RUN-0031 (TASK-0101) moved the journey's first break one step past the node tap:
the node opened the detail panel (``CHAPTER1_DETAIL_VISIBLE`` PASS,
``panel_change=57.06``) and the runner issued **one** tap on 确认/探索 at the centre
of the recovered rectangle -- ``Android ACTION_DOWN x[0]=1310.0 y[0]=645.0``,
logcat 05:22:28.225, inside ``(1172, 591, 1448, 699)``.  Nothing followed: no Lua
error, no ``switchScene chapter_qiandengzhen_1``, no ``-- doFinishSwitch``, and the
run's own final frame shows the same panel-less world map the tap was derived from.

The client had already closed the panel by itself, once per second, from the moment
the chapter UI existed:

    pkgExperienceMainUIMgr.lua:62  onCountdown        (1,000 ms repeating timer,
                                                       started by initExperience:169)
    pkgExperienceMainUIMgr.lua:73  pkgExperienceMgr.CloseActivityByClient()
    pkgExperienceMgr.lua:178       pkgEventMgr.dispatch(EXPERIENCE_STATUS_CHANGE, ...)
    pkgExperienceMainUIMgr.lua:199 setActiveByName(gameObject, "Panel/btnDetail",
                                                   false)       <-- the panel
    pkgExperienceMainUIMgr.lua:214 changeChapterBtnExperienceStatus
    pkgExperienceMainUIMgr.lua:643 pkgExperienceMgr.GetTaskListByChapterId(...)
    pkgExperienceMgr.lua:203       le_tbTaskByChapterId[dChapterId]  <-- nil: raise

The raise happens *inside* the dispatch, so ``onCountdown``'s own cleanup -- ``if
le_dTimer then pkgTimerMgr.delete(le_dTimer); le_dTimer = nil end`` (lines 74-76) --
never runs: the timer is never deleted and the panel is switched off again a second
later, for the rest of the session (logcat: 58 errors, one per second, the first at
05:21:55.457).

This module contains a faithful model of that recovered chain, pinned to the shipped
chunks by ``max_decoder/analyze_experience_task_init.py`` (which the tests import and
run, so the model cannot silently drift).  Every failure carries the recovered
``module:line`` and function, so a model result can be compared with RUN-0031's logcat
frame for frame.

The tests then drive the model with the *real decoded wire* of a real
``RevivalTCPServer`` over a real socket session: a v7 server (no experience-task
policy) for the falsification and the active v8 server for the repair.
"""

from __future__ import annotations

import socket
import sys
import threading
import unittest
from pathlib import Path

from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import (
    EXPERIENCE_TASK_CONTAINER_POLICY,
    EXPERIENCE_TASK_HISTORICAL_ORDERING,
    EXPERIENCE_TASK_PACKET_ORDER,
    EXPERIENCE_TASK_ROW_COUNT,
    PROFILE_ID_V7,
    PROFILE_ID_V8,
    PROFILE_ID_V9,
    PROFILE_ID_V10,
    get_profile,
    get_profile_v1,
    get_profile_v2,
    get_profile_v3,
    get_profile_v4,
    get_profile_v5,
    get_profile_v6,
    get_profile_v7,
    get_profile_v8,
    get_profile_v9,
    get_profile_v10,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_DECODER = PROJECT_ROOT / "max_decoder"
if str(MAX_DECODER) not in sys.path:
    sys.path.insert(0, str(MAX_DECODER))

import analyze_experience_task_init as contract  # noqa: E402

TASK_START_ID = 1307
TASK_INFO_ID = 1308
TASK_END_ID = 1309
SENT_IDS = (TASK_START_ID, TASK_END_ID)
UNREGISTERED_SIBLINGS = (1308, 1310, 1311, 1312)

#: TASK-0104 / profile v9 owns the sibling chapter-task family, which the v9
#: terminal segment inserts immediately before the v7 chapter-item pair.  This
#: module only needs the two ids to prove the INSERTION is additive.
CHAPTER_TASK_START_ID = 385
CHAPTER_TASK_END_ID = 387

#: The terminal segment as each profile sends it.  V7 is the profile RUN-0031 ran
#: and V8 the profile RUN-0032 ran; V9 (TASK-0104) inserts the chapter-task pair
#: 385/387 immediately BEFORE the chapter-item pair, because the client's own
#: registration table puts the task family (385-388) immediately before the item
#: family (389-392) and both containers are read in the same pass of the same
#: finish callback.
V7_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 389, 391, 411, 283]
V8_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 389, 391,
                   1307, 1309, 411, 283]
V9_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 385, 387,
                   389, 391, 1307, 1309, 411, 283]
#: TASK-0109 / profile v10 inserts the chapter-machine pair 400/402 immediately
#: AFTER the chapter-item pair -- the client's own registration table puts the
#: machine family (399-403) after the item family (389-393), and both containers
#: are read in the same pass of the same finish callback.
V10_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 385, 387,
                    389, 391, 400, 402, 1307, 1309, 411, 283]

#: Read counts that get the whole login + terminal segment off the wire.
BASE_LOGIN_PACKETS = 5
V7_SEGMENT_PACKETS = 20
V8_SEGMENT_PACKETS = 22
V9_SEGMENT_PACKETS = 24
V10_SEGMENT_PACKETS = 26


def _read(stream, server, buffer, state, packets, count) -> None:
    while len(packets) < count:
        try:
            packet = decode_packet(buffer, server.s2c_registry, state)
        except NeedMoreData:
            chunk = stream.recv(65536)
            if not chunk:
                raise RuntimeError(f"server closed after {len(packets)}/{count} packets")
            buffer.extend(chunk)
            continue
        del buffer[: packet.consumed]
        packets.append(packet)


class RecoveredClientError(RuntimeError):
    """A failure at a named line of the recovered client source."""

    def __init__(self, module: str, line: int, function: str, detail: str) -> None:
        self.module = module
        self.line = line
        self.function = function
        self.detail = detail
        super().__init__(f"{module}:{line}: {detail}")

    def frame(self) -> str:
        return f"{self.module}:{self.line}: in function '{self.function}'"


class RecoveredEventMgr:
    """``event/pkgEventMgr.lua`` - the dispatcher that calls the listener."""

    MODULE = "event/pkgEventMgr.lua"
    DISPATCH_LINE = 62

    def __init__(self, experience_ui) -> None:
        self.experience_ui = experience_ui
        self.listeners: dict[str, list] = {}

    def add(self, event: str, callback) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def dispatch(self, event: str, *args) -> None:
        """`obj[callback](obj, ...)` (line 62): the FIRST argument is the owner."""

        for callback in self.listeners.get(event, []):
            try:
                callback(self.experience_ui, *args)
            except RecoveredClientError as exc:  # the raise escapes the dispatch
                raise exc


class RecoveredExperienceMgr:
    """``experience/pkgExperienceMgr.lua`` - the activity/container module."""

    MODULE = "experience/pkgExperienceMgr.lua"
    DECLARE_LINE = 17
    START_FIRST_LINE = 63
    START_CONTAINER_LINE = 64
    INFO_REQUIRES_LINE = 74
    END_LINE = 117
    READ_LINE = 203
    DISPATCH_LINE = 178

    def __init__(self, event_mgr: RecoveredEventMgr) -> None:
        #: `le_tbTaskByChapterId = le_tbTaskByChapterId or nil` (chunk line 17) --
        #: the module's own default, and the whole reason for this task.
        self.le_tbTaskByChapterId = None
        self.le_tbTask = None
        self.le_dTaskCount = 0
        self.le_dActivityStatus = 0
        self.events = event_mgr
        self.status_not_open = 0

    # -- the client's own S2C handlers (registered by this very module) ---------

    def return_task_start(self) -> None:
        """The client's ONLY producer of the container (lines 62-66)."""

        self.le_tbTask = {}
        self.le_tbTaskByChapterId = {}
        self.le_dTaskCount = 0

    def return_task_info(self, d_chapter_id, d_task_id, d_status, d_progress) -> None:
        """Requires the container: its first statement indexes it (line 74)."""

        if self.le_tbTaskByChapterId is None:
            raise RecoveredClientError(
                self.MODULE, self.INFO_REQUIRES_LINE, "returnTaskInfo",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        self.le_tbTaskByChapterId.setdefault(d_chapter_id, {})[d_task_id] = d_status

    def return_task_end(self) -> None:
        """The client's own empty end handler (lines 116-118)."""

    # -- the unguarded readers --------------------------------------------------

    def get_task_list_by_chapter_id(self, d_chapter_id):
        """`return le_tbTaskByChapterId[dChapterId]` (line 203)."""

        if self.le_tbTaskByChapterId is None:
            raise RecoveredClientError(
                self.MODULE, self.READ_LINE, "GetTaskListByChapterId",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        return self.le_tbTaskByChapterId.get(d_chapter_id)

    def get_pet_res_id(self, d_chapter_id):
        """`GetPetResId` answers from the same container (enterChapter's first call)."""

        return (self.get_task_list_by_chapter_id(d_chapter_id) or {}).get("pet")

    def close_activity_by_client(self) -> None:
        """Lines 174-179: status reset, then the dispatch at 178."""

        self.le_dActivityStatus = self.status_not_open
        try:
            self.events.dispatch("EXPERIENCE_STATUS_CHANGE", self.le_dActivityStatus)
        except RecoveredClientError as exc:
            raise exc


class RecoveredExperienceMainUIMgr:
    """``experience/pkgExperienceMainUIMgr.lua`` - the panel's other owner."""

    MODULE = "experience/pkgExperienceMainUIMgr.lua"
    ON_COUNTDOWN_LINE = 62
    CLOSE_LINE = 73
    TIMER_CLEAR_LINE = 76
    CREATE_TIMER_LINE = 123
    INIT_LINE = 169
    STATUS_LINE = 198
    PANEL_OFF_LINE = 199
    WALK_LINE = 214
    CHANGE_READ_LINE = 643

    def __init__(self, experience_mgr: RecoveredExperienceMgr, chapter_ui) -> None:
        self.experience = experience_mgr
        self.chapter_ui = chapter_ui
        self.le_dTimer = None
        self.ticks = 0
        self.tick_errors: list[RecoveredClientError] = []

    # -- lifecycle --------------------------------------------------------------

    def init_experience(self) -> None:
        """Lines 161-178: called by pkgChapterMainUIViewMgr.init line 412."""

        self.init_listener()
        self.create_timer()

    def init_listener(self) -> None:
        self.experience.events.add("EXPERIENCE_STATUS_CHANGE", self.experience_status_change)

    def create_timer(self) -> None:
        """Lines 118-124: `pkgTimerMgr.addWithoutDelay(1000, onCountdown)`."""

        self.le_dTimer = "timer@1000ms"

    # -- the 1 Hz tick ----------------------------------------------------------

    def on_countdown(self) -> None:
        """Lines 62-116: close the activity when its window has passed."""

        self.ticks += 1
        # `le_dEndTime` is 0 for this role (no experience status was ever pushed),
        # so `dLeft = 0 - now < 0` and the "activity is over" arm is always taken.
        try:
            self.experience.close_activity_by_client()
        except RecoveredClientError as exc:
            # The raise escapes the dispatch AND this function, so the three
            # statements below -- the timer's own deletion -- never run.
            self.tick_errors.append(exc)
            raise
        if self.le_dTimer is not None:
            self.le_dTimer = None

    def ticks_while_running(self, seconds: int) -> list[RecoveredClientError]:
        """Drive the timer the way the device did: one tick per second, forever."""

        errors: list[RecoveredClientError] = []
        for _ in range(seconds):
            if self.le_dTimer is None:
                break
            try:
                self.on_countdown()
            except RecoveredClientError as exc:
                errors.append(exc)
        return errors

    def experience_status_change(self, _owner, d_status=None) -> None:
        """Lines 198-220: the panel is switched off FIRST, then the chapters walk."""

        self.chapter_ui.panel_active = False  # line 199
        for d_chapter_id in range(1, 5):
            self.change_chapter_btn_experience_status(d_chapter_id)  # line 214

    def change_chapter_btn_experience_status(self, d_chapter_id) -> None:
        """Lines 640-716: the container read at 643, gated by isActivityValid."""

        if d_chapter_id not in self.chapter_ui.btn_chapter:
            return  # line 642
        tasks = self.experience.get_task_list_by_chapter_id(d_chapter_id)  # line 643
        if not self.is_activity_valid() or not tasks:
            return
        raise AssertionError("the model only needs the empty-container path")

    def is_activity_valid(self) -> bool:
        return self.experience.le_dActivityStatus != self.experience.status_not_open


class RecoveredChapterMainUIViewMgr:
    """``chapter/pkgChapterMainUIViewMgr.lua`` - the panel and its 确认 button."""

    MODULE = "chapter/pkgChapterMainUIViewMgr.lua"
    BIND_LINE = 182
    ACTIVATE_LINE = 195
    CLOSE_LINE = 405

    def __init__(self) -> None:
        self.btn_chapter = {1: "chapterNode1"}
        self.panel_active = False
        self.enter_bound = False
        self.enter_taps = 0
        self.enter_chapter_calls: list[int] = []

    # -- init() calls pkgExperienceMainUIMgr.init(gameObject) at line 412 -------

    def on_chapter_click(self, d_chapter_id: int) -> None:
        """`onChapterClickHandler` (70-228) down to the panel activation."""

        if d_chapter_id not in self.btn_chapter:
            return
        self.enter_bound = True  # line 182: addListener(panel/btn, onEnter)
        self.panel_active = True  # line 195: setActiveByName(Panel/btnDetail, true)

    def on_confirm_tap(self, point, rect) -> bool:
        """A tap reaches `onEnter` only when the panel is still active.

        The tap is delivered by Android either way -- the runner's own
        ``ACTION_DOWN x[0]=1310.0 y[0]=645.0`` is in RUN-0031's logcat -- so the
        only thing that can make it a no-op is the panel's own active state,
        exactly as Unity's raycast sees it.
        """

        x, y = point
        x0, y0, x1, y1 = rect
        if not (x0 < x < x1 and y0 < y < y1):
            raise AssertionError("the tap must be the one RUN-0031 delivered")
        if not self.panel_active:
            return False  # the raycast finds no 确认 button at all
        self.enter_taps += 1
        self.on_enter()
        return True

    def on_enter(self) -> None:
        """`onEnter` (111-177) -> pkgChapterSceneMgr.enterChapter(1)."""

        self.enter_chapter_calls.append(1)


class ModelWorld:
    """The four recovered modules wired the way the client wires them."""

    def __init__(self) -> None:
        self.chapter_ui = RecoveredChapterMainUIViewMgr()
        self.events = RecoveredEventMgr(None)
        self.experience = RecoveredExperienceMgr(self.events)
        self.experience_ui = RecoveredExperienceMainUIMgr(self.experience, self.chapter_ui)
        self.events.experience_ui = self.experience_ui

    def build_chapter_ui(self) -> None:
        """`pkgChapterMainUIViewMgr.init()` line 412 -> `initExperience()`."""

        self.experience_ui.init_experience()

    def apply_packet(self, protocol_id: int, fields: tuple) -> None:
        """Apply one decoded S2C packet through the module's own handler."""

        if protocol_id == TASK_START_ID:
            self.experience.return_task_start()
        elif protocol_id == TASK_INFO_ID:
            self.experience.return_task_info(*fields)
        elif protocol_id == TASK_END_ID:
            self.experience.return_task_end()


def new_server(profile=None) -> tuple[RevivalTCPServer, list]:
    """A real server on a real socket, with its own event sink."""

    events: list[dict] = []
    server = RevivalTCPServer(
        ("127.0.0.1", 0),
        EventLogger(stream=None, sink=events.append),
        compatibility_profile=profile,
        time_source=lambda: 1_700_000_123,
    )
    return server, events


def login(server, packets: int) -> list:
    """Drive one full login against a real server and return the S2C segment."""

    responses: list = []
    buffer = bytearray()
    state = ReaderState()
    writer = WriterState()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(encode_packet(
                C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire)
            _read(stream, server, buffer, state, responses, 1)
            stream.sendall(encode_packet(
                C2S_REGISTRY.by_id(2), ("t0102".encode("utf-8"), 1), writer).wire)
            _read(stream, server, buffer, state, responses, BASE_LOGIN_PACKETS)
            stream.sendall(encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire)
            _read(stream, server, buffer, state, responses, packets)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return responses


def terminal_segment(responses: list) -> list:
    """The role-sync/terminal segment, exactly as both profiles send it.

    Everything after the five packets of the login handshake, minus the sign-in
    reward (id 85) the harness's own heartbeats pull -- the segment every other
    login module asserts on.
    """

    return [packet for packet in responses[BASE_LOGIN_PACKETS:]
            if packet.spec.protocol_id != 85]


class ProtocolTableTests(unittest.TestCase):
    """The wire ids and formats are the registration table's, not a count."""

    def test_the_recovered_contract_still_holds(self) -> None:
        sections = contract.verify_against_source()
        self.assertEqual(
            sorted(sections),
            ["container_lifecycle", "countdown_chain", "declared_ranges",
             "protocol_table", "run0031_stack", "run0031_tap", "unguarded_read"],
        )
        for name, lines in sections.items():
            with self.subTest(section=name):
                self.assertTrue(lines, f"{name} produced no lines")

    def test_the_pair_is_registered_and_the_row_writer_is_not(self) -> None:
        for wire_id, name in ((1307, "returnTaskStart"), (1309, "returnTaskEnd")):
            spec = S2C_REGISTRY.by_id(wire_id)
            self.assertEqual(spec.name, name)
            self.assertEqual(spec.fmt, "")
        with self.assertRaises(KeyError):
            S2C_REGISTRY.by_id(TASK_INFO_ID)
        for wire_id in UNREGISTERED_SIBLINGS[1:]:
            with self.assertRaises(KeyError):
                S2C_REGISTRY.by_id(wire_id)

    def test_the_two_packets_carry_no_field_at_all(self) -> None:
        table = {(proto.direction, proto.name): proto for proto in
                 contract.protocol_table()}
        for name in ("returnTaskStart", "returnTaskEnd"):
            self.assertEqual(table[("S2C", name)].fmt, "")


class ModelTests(unittest.TestCase):
    """The model reproduces RUN-0031 -- and the repaired run -- at the same lines."""

    def test_the_nil_container_reproduces_run0031_frame_for_frame(self) -> None:
        world = ModelWorld()
        world.build_chapter_ui()
        errors = world.experience_ui.ticks_while_running(3)
        self.assertEqual(len(errors), 3)
        error = errors[0]
        self.assertEqual(
            (error.module, error.line, error.function),
            ("experience/pkgExperienceMgr.lua", 203, "GetTaskListByChapterId"),
        )
        self.assertEqual(
            str(error),
            "experience/pkgExperienceMgr.lua:203: attempt to index global "
            "'le_tbTaskByChapterId' (a nil value)",
        )

    def test_the_tick_never_deletes_its_timer_while_the_read_raises(self) -> None:
        world = ModelWorld()
        world.build_chapter_ui()
        world.experience_ui.ticks_while_running(5)
        self.assertEqual(world.experience_ui.ticks, 5)
        self.assertIsNotNone(world.experience_ui.le_dTimer)

    def test_the_confirm_tap_finds_no_panel(self) -> None:
        """The delivered tap is a no-op: `Panel/btnDetail` is already inactive."""

        world = ModelWorld()
        world.build_chapter_ui()
        world.chapter_ui.on_chapter_click(1)
        self.assertTrue(world.chapter_ui.panel_active)
        # The panel is opened between two ticks, then the next tick hides it.
        world.experience_ui.ticks_while_running(1)
        self.assertFalse(world.chapter_ui.panel_active)
        reached = world.chapter_ui.on_confirm_tap(
            contract.RUN0031_TAP_POINT, contract.RUN0031_CONFIRM_RECT)
        self.assertFalse(reached)
        self.assertEqual(world.chapter_ui.enter_taps, 0)
        self.assertEqual(world.chapter_ui.enter_chapter_calls, [])

    def test_the_empty_container_makes_the_tick_delete_itself_and_stop(self) -> None:
        world = ModelWorld()
        world.build_chapter_ui()
        world.experience.return_task_start()
        world.experience.return_task_end()
        self.assertEqual(world.experience.le_tbTaskByChapterId, {})
        self.assertEqual(world.experience_ui.ticks_while_running(3), [])
        self.assertEqual(world.experience_ui.ticks, 1)
        self.assertIsNone(world.experience_ui.le_dTimer)

    def test_the_panel_then_survives_and_the_tap_reaches_enter_chapter(self) -> None:
        world = ModelWorld()
        world.build_chapter_ui()
        world.experience.return_task_start()
        world.experience.return_task_end()
        world.experience_ui.ticks_while_running(3)  # the tick closes once and dies
        world.chapter_ui.on_chapter_click(1)
        self.assertTrue(world.chapter_ui.panel_active)
        self.assertTrue(
            world.chapter_ui.on_confirm_tap(
                contract.RUN0031_TAP_POINT, contract.RUN0031_CONFIRM_RECT)
        )
        self.assertEqual(world.chapter_ui.enter_chapter_calls, [1])

    def test_the_timer_must_be_started_for_the_tick_to_run_at_all(self) -> None:
        """`initExperience` line 169 is what starts it; without it nothing ticks."""

        world = ModelWorld()
        self.assertIsNone(world.experience_ui.le_dTimer)
        self.assertEqual(world.experience_ui.ticks_while_running(3), [])
        self.assertEqual(world.experience_ui.ticks, 0)


class WireTests(unittest.TestCase):
    """The real decoded wire of a real server, v7 (broken) and v8 (repaired)."""

    def test_the_v7_wire_carries_no_experience_task_packet(self) -> None:
        server, events = new_server(profile=get_profile_v7())
        segment = terminal_segment(login(server, V7_SEGMENT_PACKETS))
        self.assertEqual([packet.spec.protocol_id for packet in segment],
                         V7_TERMINAL_IDS)
        self.assertNotIn(TASK_START_ID, [p.spec.protocol_id for p in segment])
        world = ModelWorld()
        world.build_chapter_ui()
        for packet in segment:
            world.apply_packet(packet.spec.protocol_id, packet.fields)
        self.assertIsNone(world.experience.le_tbTaskByChapterId)
        errors = world.experience_ui.ticks_while_running(1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].line, 203)

    def test_the_v7_wire_is_the_old_stream_untouched(self) -> None:
        """A v7 server's stream is what it always was: the pair is INSERTED."""

        server, events = new_server(profile=get_profile_v7())
        segment = terminal_segment(login(server, V7_SEGMENT_PACKETS))
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids.index(389) + 1, ids.index(391))
        self.assertEqual(ids.index(391) + 1, ids.index(411))

    def test_the_v8_wire_carries_exactly_the_pair_in_order(self) -> None:
        """The profile RUN-0032 ran: the experience pair, and no chapter-task pair."""

        server, events = new_server(profile=get_profile_v8())
        segment = terminal_segment(login(server, V8_SEGMENT_PACKETS))
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V8_TERMINAL_IDS)
        self.assertNotIn(CHAPTER_TASK_START_ID, ids)
        self.assertNotIn(CHAPTER_TASK_END_ID, ids)
        for packet in segment:
            if packet.spec.protocol_id in SENT_IDS:
                self.assertEqual(packet.fields, ())
                self.assertEqual(len(packet.wire), 3)  # header + checksum, no body

    def test_the_v9_wire_inserts_the_chapter_task_pair_before_the_item_pair(
        self,
    ) -> None:
        """TASK-0104: the v9 profile INSERTS 385/387; nothing else moves.

        TASK-0109 made v10 active, so this asks for the frozen v9 profile
        explicitly -- which is the point of the bump: v9's own stream keeps being
        checked byte for byte.
        """

        server, events = new_server(profile=get_profile_v9())
        segment = terminal_segment(login(server, V9_SEGMENT_PACKETS))
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V9_TERMINAL_IDS)
        # The v8 experience-task pair is still exactly where it was, relative to
        # the S2C411 story snapshot that follows the whole policy block.
        self.assertEqual(ids.index(1307) + 1, ids.index(1309))
        self.assertEqual(ids.index(1309) + 1, ids.index(411))
        # ... and the new pair sits immediately BEFORE the v7 chapter-item pair.
        self.assertEqual(ids.index(CHAPTER_TASK_START_ID) + 1,
                         ids.index(CHAPTER_TASK_END_ID))
        self.assertEqual(ids.index(CHAPTER_TASK_END_ID) + 1, ids.index(389))
        # The insertion really only inserted: every v8 packet is still there, in
        # the same order, once the new pair is removed.
        self.assertEqual(
            [wire_id for wire_id in ids
             if wire_id not in (CHAPTER_TASK_START_ID, CHAPTER_TASK_END_ID)],
            V8_TERMINAL_IDS,
        )
        for packet in segment:
            if packet.spec.protocol_id in (
                CHAPTER_TASK_START_ID, CHAPTER_TASK_END_ID,
            ):
                self.assertEqual(packet.fields, ())
                self.assertEqual(len(packet.wire), 3)  # header + checksum, no body

    def test_the_real_wire_creates_the_container_and_the_panel_survives(self) -> None:
        # The v10 stream explicitly: the expectations below are v10-terminal
        # facts, and TASK-0167 moved the ACTIVE profile to v13 (whose stream
        # carries one more packet before S2C283).  A v10 server still replays the
        # exact v10 wire.
        server, events = new_server(profile=get_profile_v10())
        segment = terminal_segment(login(server, V10_SEGMENT_PACKETS))
        self.assertEqual(
            [packet.spec.protocol_id for packet in segment], V10_TERMINAL_IDS
        )
        world = ModelWorld()
        world.build_chapter_ui()
        for packet in segment:
            world.apply_packet(packet.spec.protocol_id, packet.fields)
        self.assertEqual(world.experience.le_tbTaskByChapterId, {})
        self.assertEqual(world.experience.le_dTaskCount, 0)
        # The tick now completes once, deletes its own timer, and never runs again.
        self.assertEqual(world.experience_ui.ticks_while_running(5), [])
        self.assertEqual(world.experience_ui.ticks, 1)
        self.assertIsNone(world.experience_ui.le_dTimer)
        # ... and the panel opened after it stays open for the confirm tap.
        world.chapter_ui.on_chapter_click(1)
        self.assertTrue(
            world.chapter_ui.on_confirm_tap(
                contract.RUN0031_TAP_POINT, contract.RUN0031_CONFIRM_RECT)
        )
        self.assertEqual(world.chapter_ui.enter_chapter_calls, [1])

    def test_the_pair_is_the_clients_own_reset_not_a_task_row(self) -> None:
        # The v10 stream explicitly, so this stays a statement about the stream
        # that introduced the pair: TASK-0140 made v11 active and a later bump may
        # never re-version what v8 sent.
        server, events = new_server(profile=get_profile_v10())
        login(server, V10_SEGMENT_PACKETS)
        events = [event for event in events
                  if event.get("event") == "tcp_experience_task_state_sent"]
        self.assertEqual([event["protocol_id"] for event in events], list(SENT_IDS))
        self.assertEqual([event["snapshot_index"] for event in events], [0, 1])
        self.assertEqual([event["snapshot_section"] for event in events],
                         ["returnTaskStart", "returnTaskEnd"])
        self.assertEqual(events[0]["snapshot_packet_ids"], list(SENT_IDS))
        self.assertEqual(events[0]["snapshot_packet_count"], 2)
        # The policy keeps the version at which it was introduced even though the
        # active server profile has since moved on three times (TASK-0104 made v9
        # active, TASK-0109 v10, TASK-0140 v11), and the event still names the
        # stream that carried it and that stream's parent.  A later profile bump
        # may never re-version this pair.
        self.assertEqual(events[0]["policy_compatibility_version"], PROFILE_ID_V8)
        active = get_profile_v10()
        self.assertEqual(events[0]["profile_id"], active.profile_id)
        self.assertEqual(events[0]["parent_profile_id"], active.parent_profile_id)
        self.assertEqual(active.parent_profile_id, PROFILE_ID_V9)
        self.assertEqual(events[0]["container_policy"], EXPERIENCE_TASK_CONTAINER_POLICY)
        self.assertEqual(events[0]["container_policy_origin"], "synthetic_compatibility")
        self.assertEqual(events[0]["task_rows_sent"], 0)
        self.assertEqual(events[0]["historical_ordering"],
                         EXPERIENCE_TASK_HISTORICAL_ORDERING)
        self.assertEqual(events[0]["ordering_origin"], "synthetic_compatibility")
        self.assertIs(events[0]["historical_values_recovered"], False)
        self.assertIs(events[0]["precedes_scene_switch_packet"], True)
        self.assertEqual(events[0]["scene_switch_boundary_protocol_id"], 283)
        # No task row ever left the server.
        self.assertEqual(
            [event for event in events if event.get("protocol_id") == TASK_INFO_ID],
            [],
        )
        for wire_id in UNREGISTERED_SIBLINGS[1:]:
            self.assertEqual(
                [event for event in events if event.get("protocol_id") == wire_id],
                [],
            )


class NegativeTests(unittest.TestCase):
    """Every way this repair could be wrong, each failing closed."""

    def test_a_row_without_the_start_raises_in_the_handler(self) -> None:
        """1308 indexes the global 1307 creates -- so the order is not cosmetic."""

        world = ModelWorld()
        with self.assertRaises(RecoveredClientError) as caught:
            world.experience.return_task_info(1, 1001, 1, 0)
        self.assertEqual(caught.exception.line, 74)
        self.assertEqual(caught.exception.function, "returnTaskInfo")

    def test_the_end_alone_is_not_the_reset(self) -> None:
        world = ModelWorld()
        world.experience.return_task_end()
        self.assertIsNone(world.experience.le_tbTaskByChapterId)
        world.build_chapter_ui()
        self.assertEqual(len(world.experience_ui.ticks_while_running(1)), 1)

    def test_the_policy_refuses_a_foreign_shape(self) -> None:
        policy = get_profile().experience_task_init
        assert policy is not None
        self.assertEqual(policy.row_count, EXPERIENCE_TASK_ROW_COUNT)
        self.assertEqual(policy.row_count, 0)
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(policy.packets(),
                         (("returnTaskStart", ()), ("returnTaskEnd", ())))
        self.assertEqual(EXPERIENCE_TASK_PACKET_ORDER, ("returnTaskStart", "returnTaskEnd"))
        self.assertEqual(
            [name for name, _ in policy.packets()],
            [name for _, (name, _) in sorted(contract.TASK_SENT_S2C.items())],
        )

    def test_every_earlier_profile_still_sends_nothing_here(self) -> None:
        builders = (get_profile_v1, get_profile_v2, get_profile_v3, get_profile_v4,
                    get_profile_v5, get_profile_v6, get_profile_v7)
        for builder in builders:
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.experience_task_init)
        self.assertIsNotNone(get_profile().experience_task_init)

    def test_a_second_tap_on_the_same_control_is_refused_by_the_closed_panel(self) -> None:
        """One control, one tap: after `onEnter` the panel is gone from the raycast.

        The runner's rule is "one tap per control"; the model shows the client's own
        half of it -- a panel that is no longer active cannot take a second tap.
        """

        world = ModelWorld()
        world.build_chapter_ui()
        world.experience.return_task_start()
        world.chapter_ui.on_chapter_click(1)
        self.assertTrue(world.chapter_ui.on_confirm_tap(
            contract.RUN0031_TAP_POINT, contract.RUN0031_CONFIRM_RECT))
        world.chapter_ui.panel_active = False  # what the client does on entering
        self.assertFalse(world.chapter_ui.on_confirm_tap(
            contract.RUN0031_TAP_POINT, contract.RUN0031_CONFIRM_RECT))
        self.assertEqual(world.chapter_ui.enter_taps, 1)

    def test_an_unknown_chapter_never_reads_the_container(self) -> None:
        """`changeChapterBtnExperienceStatus` returns first if the node is missing."""

        world = ModelWorld()
        world.experience_ui.change_chapter_btn_experience_status(17)  # no raise

    def test_a_wrong_coordinate_is_not_the_delivered_tap(self) -> None:
        """The model only accepts the point RUN-0031 actually delivered."""

        world = ModelWorld()
        world.build_chapter_ui()
        world.experience.return_task_start()
        world.chapter_ui.on_chapter_click(1)
        with self.assertRaises(AssertionError):
            world.chapter_ui.on_confirm_tap((703, 567), contract.RUN0031_CONFIRM_RECT)

    def test_a_stopped_tick_leaves_the_panel_alone(self) -> None:
        """STOP/PAUSE is the runner's; on the client side a dead timer is the same."""

        world = ModelWorld()
        world.build_chapter_ui()
        world.experience.return_task_start()
        world.chapter_ui.on_chapter_click(1)
        world.experience_ui.le_dTimer = None  # the timer self-deleted
        self.assertEqual(world.experience_ui.ticks_while_running(5), [])
        self.assertTrue(world.chapter_ui.panel_active)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
