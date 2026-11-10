"""TASK-0104 -- the chapter-task container, and the finish callback that raises on it.

RUN-0032 (TASK-0103) is the first run in which the client actually *started* the
chapter scene: the confirm tap was delivered on 探索 (``Android ACTION_DOWN
x[0]=1310.0 y[0]=645.0``, logcat 07:17:46.155, inside the recovered 确认 rectangle
1172..1448 / 591..699) and the client's own switch followed 0.086 s later::

    07:17:46.241  switchScene  chapter_qiandengzhen_1  3  nil
    07:17:46.364  -- loadEmptyScene
    07:17:46.426  -- loadEmptyScene onComplete
    07:17:46.879  ABScene  ab_chapter_qiandengzhen_1
    07:17:47.719  -- static combile --
    07:17:47.732  ABScene  ab_chapter_qiandengzhen_1_juqing
    07:17:47.733  LUA ERROR: chapter/pkgChapterTaskMgr.lua:62
                  attempt to index global 'le_tbChapterTaskInfo' (a nil value)

The load **completed** -- the traceback's outer frames are the loader's own
completion -- and what raised is the finish callback ``pkgSceneMgr.doFinishSwitch``
calls at its line 875::

    chapter/pkgChapterTaskMgr.lua:62   isTaskReceived
    chapter/pkgChapterInfoMgr.lua:262  isItemOver
    chapter/pkgChapterInfoMgr.lua:309  getActiveMap
    chapter/pkgChapterObjectMgr.lua:385 initIndex2TypeId
    chapter/pkgChapterSceneMgr.lua:112 le_finishCallBack
    scene/pkgSceneMgr.lua:875          doFinishSwitch

``doFinishSwitch`` dispatches ENTER_SCENE at 880, calls
``pkgSyncSceneMgr.enterScene`` at 881, prints ``-- doFinishSwitch`` at 882 and tears
the loading UI down at 884-890 -- all **after** the 875 that raised.  That is why the
run's whole logcat carries ``-- doFinishSwitch`` exactly twice (the main city and the
world map) and never for the chapter, and why the run ends on the client's own loading
screen.

This module contains a faithful model of that recovered chain, pinned to the shipped
chunks by ``max_decoder/analyze_chapter_task_init.py`` (which the tests import and run,
so the model cannot silently drift).  Every failure carries the recovered
``module:line`` and function, so a model result can be compared with RUN-0032's logcat
frame for frame.

The tests then drive the model with the *real decoded wire* of a real
``RevivalTCPServer`` over a real socket session: a v8 server (no chapter-task policy,
the profile RUN-0032 ran) for the falsification and the active v9 server for the repair.
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
    CHAPTER_TASK_CONTAINER_POLICY,
    CHAPTER_TASK_HISTORICAL_ORDERING,
    CHAPTER_TASK_PACKET_ORDER,
    CHAPTER_TASK_ROW_COUNT,
    PROFILE_ID_V1,
    PROFILE_ID_V7,
    PROFILE_ID_V8,
    PROFILE_ID_V9,
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
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_DECODER = PROJECT_ROOT / "max_decoder"
if str(MAX_DECODER) not in sys.path:
    sys.path.insert(0, str(MAX_DECODER))

import analyze_chapter_task_init as contract  # noqa: E402

TASK_START_ID = 385
TASK_INFO_ID = 386
TASK_END_ID = 387
TASK_UPDATE_ID = 388
SENT_IDS = (TASK_START_ID, TASK_END_ID)
#: TASK-0180: the v15 profile registers 386 (id + format only) and sends exactly
#: one row, while the frozen v9 policy still sends none.  388 stays unregistered.
V15_ROW_ID = TASK_INFO_ID
UNREGISTERED_SIBLINGS = (TASK_UPDATE_ID,)

#: The terminal segment of a v8 login (the profile RUN-0032 ran) and of the FROZEN
#: v9 login this task audits: v9 INSERTS the chapter-task pair immediately before
#: the v7 chapter-item pair, so every v8 packet keeps its own position relative to
#: the others and only two bytes of header-plus-checksum are added in front of 389.
#:
#: TASK-0109 made v10 the active profile, so every server below that means "v9" now
#: asks for `get_profile_v9()` explicitly.  That is the point of the version bump:
#: v9's wire is frozen and this module keeps checking the exact bytes v9 sent.
V8_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 389, 391,
                   1307, 1309, 411, 283]
V9_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 385, 387,
                   389, 391, 1307, 1309, 411, 283]

#: Read counts that get the whole login + terminal segment off the wire.
BASE_LOGIN_PACKETS = 5
V8_SEGMENT_PACKETS = 22
V9_SEGMENT_PACKETS = 24

#: RUN-0032's own switch and the scene it asked for.
CHAPTER1_SCENE = contract.CHAPTER1_SCENE_NAME


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

    @property
    def frame(self) -> str:
        return f"{self.module}:{self.line}: in function '{self.function}'"


# ---------------------------------------------------------------- the modules


class RecoveredChapterTaskMgr:
    """``chapter/pkgChapterTaskMgr.lua`` -- the container's only owner."""

    MODULE = "chapter/pkgChapterTaskMgr.lua"
    DECLARE_LINE = 6
    START_FIRST_LINE = 21
    START_CONTAINER_LINE = 22
    START_LAST_LINE = 23
    INFO_REQUIRES_LINE = 26
    INFO_FIRST_LINE = 25
    INFO_LAST_LINE = 28
    END_FIRST_LINE = 30
    END_LAST_LINE = 32
    RECEIVED_FIRST_LINE = 59
    RECEIVED_READ_LINE = 62
    RECEIVED_LAST_LINE = 63
    COMPLETED_READ_LINE = 69

    def __init__(self) -> None:
        #: `le_tbChapterTaskInfo = le_tbChapterTaskInfo or nil` (chunk line 6) --
        #: the module's own default, and the whole reason for this task.
        self.le_tbChapterTaskInfo = None

    # -- the client's own S2C handlers (registered by this very module) ---------

    def init_chapter_task_start(self) -> None:
        """The client's ONLY creator of the container (lines 21-23)."""

        self.le_tbChapterTaskInfo = {}

    def init_chapter_task_info(self, d_chapter_id, d_task_id) -> None:
        """Requires the container: it is indexed at line 26, unguarded."""

        if self.le_tbChapterTaskInfo is None:
            raise RecoveredClientError(
                self.MODULE, self.INFO_REQUIRES_LINE, "initChapterTaskInfo",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        self.le_tbChapterTaskInfo.setdefault(d_chapter_id, {})[d_task_id] = 1

    def init_chapter_task_end(self) -> None:
        """The client's own empty end handler (lines 30-32)."""

    # -- the unguarded readers --------------------------------------------------

    def is_task_received(self, d_chapter_id, d_task_id) -> bool:
        """Lines 59-63: `le_tbChapterTaskInfo[dChapterId][dTaskId] ~= nil`."""

        if self.le_tbChapterTaskInfo is None:
            raise RecoveredClientError(
                self.MODULE, self.RECEIVED_READ_LINE, "isTaskReceived",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        return self.le_tbChapterTaskInfo.get(d_chapter_id, {}).get(d_task_id) is not None

    def is_task_completed(self, d_chapter_id, d_task_id) -> bool:
        """Lines 66-70: the sibling that would break next (read at 69)."""

        if self.le_tbChapterTaskInfo is None:
            raise RecoveredClientError(
                self.MODULE, self.COMPLETED_READ_LINE, "isTaskCompleted",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        return self.le_tbChapterTaskInfo.get(d_chapter_id, {}).get(d_task_id) == 2


class RecoveredChapterInfoMgr:
    """``chapter/pkgChapterInfoMgr.lua`` -- the map's item/task gate."""

    MODULE = "chapter/pkgChapterInfoMgr.lua"
    IS_ITEM_OVER_LINE = 262
    GET_ACTIVE_MAP_LINE = 309

    def __init__(self, tasks: RecoveredChapterTaskMgr) -> None:
        self.tasks = tasks

    def is_item_over(self, d_chapter_id, d_item_id) -> bool:
        """Lines 248-275: the item's task is asked for at line 262."""

        return self.tasks.is_task_received(d_chapter_id, d_item_id)

    def get_active_map(self, d_chapter_id):
        """Lines 302-317: walks the chapter's items through isItemOver (309)."""

        return {
            "chapter_id": d_chapter_id,
            "over": self.is_item_over(d_chapter_id, d_chapter_id),
        }


class RecoveredChapterObjectMgr:
    """``chapter/pkgChapterObjectMgr.lua`` -- the node/type index builder."""

    MODULE = "chapter/pkgChapterObjectMgr.lua"
    INIT_INDEX_2_TYPE_ID_LINE = 385

    def __init__(self, info: RecoveredChapterInfoMgr) -> None:
        self.info = info

    def init_index_2_type_id(self, d_chapter_id):
        """Lines 382-390: the active map is read at line 385."""

        return self.info.get_active_map(d_chapter_id)


class RecoveredChapterSceneMgr:
    """``chapter/pkgChapterSceneMgr.lua`` -- the callback's own module."""

    MODULE = "chapter/pkgChapterSceneMgr.lua"
    ENTER_CHAPTER_CALLBACK_LINE = 112
    CLOSURE_LINE = 131
    SWITCH_SCENE_LINE = 133

    def __init__(self, objects: RecoveredChapterObjectMgr) -> None:
        self.objects = objects
        self.scene = None
        self.lua = None
        self.enter_chapter_calls: list[int] = []

    def enter_chapter(self, d_chapter_id: int) -> None:
        """Lines 56-134: it closes over the finish callback at 131 and hands it to
        `pkgSceneMgr.switchScene` at 133."""

        self.enter_chapter_calls.append(d_chapter_id)
        self.scene.switch_scene(
            CHAPTER1_SCENE, 3, lambda: self.le_finish_callback(d_chapter_id)
        )

    def le_finish_callback(self, d_chapter_id: int):
        """Lines 102-131 -- the function Lua names after the global it is stored in.

        RUN-0032's frame reads ``in function 'le_finishCallBack'`` but the frame's
        module is *this* one, because `pkgSceneMgr.switchScene` stores the closure
        in the global of that name.
        """

        return self.objects.init_index_2_type_id(d_chapter_id)


class RecoveredSceneMgr:
    """``scene/pkgSceneMgr.lua`` -- `switchScene` and `doFinishSwitch`."""

    MODULE = "scene/pkgSceneMgr.lua"
    SWITCH_SCENE_FIRST_LINE = 770
    STORE_CALLBACK_LINE = 771
    SWITCH_SCENE_LAST_LINE = 844
    FINISH_FIRST_LINE = 846
    CALLBACK_LINE = 875
    CLEAR_CALLBACK_LINE = 876
    ENTER_SCENE_LINE = 880
    ENTER_SCENE_CALL_LINE = 881
    PRINT_LINE = 882
    TEARDOWN_LINES = (884, 885, 886, 890)
    FINISH_LAST_LINE = 892

    def __init__(self) -> None:
        self.le_finishCallBack = None
        self.le_loadingGo = "loading"
        #: Every `doFinishSwitch` statement that actually executed, in order.
        self.statements_run: list[int] = []
        self.enter_scene_calls: list[str] = []
        self.enter_scene_dispatched = False
        self.finish_switch_printed = False
        self.loading_torn_down = False

    def switch_scene(self, scene_name: str, scene_type: int, callback) -> None:
        """Lines 770-844: the callback is stored in the global at line 771."""

        self.le_finishCallBack = callback  # line 771
        self.scene_name = scene_name

    def do_finish_switch(self, scene_name: str) -> None:
        """Lines 846-892: the callback at 875, and everything else after it."""

        self.statements_run = []
        self.enter_scene_calls = []
        self.enter_scene_dispatched = False
        self.finish_switch_printed = False
        self.loading_torn_down = False
        self.statements_run.append(self.CALLBACK_LINE)
        self.le_finishCallBack()  # line 875 -- the raise escapes the whole function
        self.statements_run.append(self.CLEAR_CALLBACK_LINE)
        self.le_finishCallBack = None
        self.statements_run.append(self.ENTER_SCENE_LINE)
        self.enter_scene_dispatched = True
        self.statements_run.append(self.ENTER_SCENE_CALL_LINE)
        self.enter_scene_calls.append(scene_name)
        self.statements_run.append(self.PRINT_LINE)
        self.finish_switch_printed = True
        for line in self.TEARDOWN_LINES:
            self.statements_run.append(line)
        self.le_loadingGo = None
        self.loading_torn_down = True


# ------------------------------------------------------------------ the world


class FinishOutcome:
    """What one ``doFinishSwitch`` did -- the run's own two logcat facts."""

    def __init__(self, scene: RecoveredSceneMgr, error: RecoveredClientError | None):
        self.error = error
        self.statements_run = list(scene.statements_run)
        self.enter_scene_calls = list(scene.enter_scene_calls)
        self.enter_scene_dispatched = scene.enter_scene_dispatched
        self.finish_switch_printed = scene.finish_switch_printed
        self.loading_torn_down = scene.loading_torn_down

    @property
    def switch_seen(self) -> bool:
        """`switchScene  chapter_qiandengzhen_1` is in the run's logcat."""

        return True  # the switch is what called doFinishSwitch at all

    @property
    def finish_switch(self) -> bool:
        """`-- doFinishSwitch` is printed only at pkgSceneMgr.lua:882."""

        return self.finish_switch_printed

    @property
    def logcat(self) -> str:
        """The two lines the runner's own predicates read, as this model prints them."""

        lines = [f"switchScene  {CHAPTER1_SCENE}  3  nil"]
        if self.finish_switch_printed:
            lines.append("-- doFinishSwitch")
        if self.error is not None:
            lines.append(f"LUA ERROR: {self.error}")
        return "\n".join(lines) + "\n"


class ModelWorld:
    """The five recovered modules wired the way the client wires them."""

    def __init__(self) -> None:
        self.tasks = RecoveredChapterTaskMgr()
        self.info = RecoveredChapterInfoMgr(self.tasks)
        self.objects = RecoveredChapterObjectMgr(self.info)
        self.chapter_scene = RecoveredChapterSceneMgr(self.objects)
        self.scene = RecoveredSceneMgr()
        self.chapter_scene.scene = self.scene
        self.scene_name = CHAPTER1_SCENE

    # -- the wire, applied through the client's OWN registered handlers ---------

    def apply_packet(self, protocol_id: int, fields: tuple) -> None:
        if protocol_id == TASK_START_ID:
            self.tasks.init_chapter_task_start()
        elif protocol_id == TASK_INFO_ID:
            self.tasks.init_chapter_task_info(*fields)
        elif protocol_id == TASK_END_ID:
            self.tasks.init_chapter_task_end()

    def apply_segment(self, segment) -> None:
        for packet in segment:
            self.apply_packet(packet.spec.protocol_id, packet.fields)

    # -- the switch the confirm tap starts --------------------------------------

    def confirm_tap(self, point, rect) -> bool:
        """The delivered tap: inside the recovered 确认 rectangle, always."""

        x, y = point
        x0, y0, x1, y1 = rect
        if not (x0 < x < x1 and y0 < y < y1):
            raise AssertionError("the tap must be the one RUN-0032 delivered")
        self.chapter_scene.enter_chapter(1)
        return True

    def finish_switch(self) -> FinishOutcome:
        """The loader's completion -> `doFinishSwitch`, with the raise recorded."""

        try:
            self.scene.do_finish_switch(self.scene_name)
        except RecoveredClientError as exc:
            return FinishOutcome(self.scene, exc)
        return FinishOutcome(self.scene, None)


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
                C2S_REGISTRY.by_id(2), ("t0104".encode("utf-8"), 1), writer).wire)
            _read(stream, server, buffer, state, responses, BASE_LOGIN_PACKETS)
            stream.sendall(encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire)
            _read(stream, server, buffer, state, responses, packets)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return responses


def terminal_segment(responses: list) -> list:
    """The role-sync/terminal segment, minus the sign-in reward the harness pulls."""

    return [packet for packet in responses[BASE_LOGIN_PACKETS:]
            if packet.spec.protocol_id != 85]


# ----------------------------------------------------------------- the tests


class ProtocolTableTests(unittest.TestCase):
    """The wire ids and formats are the registration table's, not a count."""

    def test_the_recovered_contract_still_holds(self) -> None:
        sections = contract.verify_against_source()
        self.assertEqual(
            sorted(sections),
            ["container_lifecycle", "declared_ranges", "finish_chain",
             "handler_module", "protocol_table", "run0032_stack",
             "run0032_tap", "run0032_timeline", "unguarded_readers",
             "whole_tree_writers"],
        )
        for name, lines in sections.items():
            with self.subTest(section=name):
                self.assertTrue(lines, f"{name} produced no evidence")

    def test_the_two_sent_ids_are_the_clients_own_handlers(self) -> None:
        self.assertEqual(
            [(S2C_REGISTRY.by_id(wire_id).name, S2C_REGISTRY.by_id(wire_id).fmt)
             for wire_id in SENT_IDS],
            [("initChapterTaskStart", ""), ("initChapterTaskEnd", "")],
        )
        # TASK-0180: the v9 policy still sends ZERO rows, but the registry now
        # carries 386's recovered id + format for the v15 DrawCardUnlock policy.
        self.assertEqual(
            (S2C_REGISTRY.by_id(V15_ROW_ID).name, S2C_REGISTRY.by_id(V15_ROW_ID).fmt),
            ("initChapterTaskInfo", "pp"),
        )
        # The push stays unregistered on the server side: it has no producer here.
        for wire_id in UNREGISTERED_SIBLINGS:
            with self.subTest(wire_id=wire_id):
                with self.assertRaises(KeyError):
                    S2C_REGISTRY.by_id(wire_id)
        # The ANALYZER's v9 contract is unchanged: v9 omits both 386 and 388.
        self.assertEqual(contract.TASK_OMITTED_S2C, (TASK_INFO_ID, TASK_UPDATE_ID))
        self.assertEqual(
            [name for name, _ in contract.TASK_SENT_S2C.values()],
            list(CHAPTER_TASK_PACKET_ORDER),
        )

    def test_the_container_and_its_only_creator_are_the_recovered_ones(self) -> None:
        self.assertEqual(contract.CONTAINER, "le_tbChapterTaskInfo")
        self.assertEqual(contract.CONTAINER_MODULE, RecoveredChapterTaskMgr.MODULE)
        self.assertEqual(contract.START_HANDLER, "initChapterTaskStart")
        self.assertEqual(contract.END_HANDLER, "initChapterTaskEnd")

    def test_no_other_chunk_of_the_shipped_tree_can_create_the_container(self) -> None:
        """The nil has no local recovery path -- proved over all 1827 chunks.

        This is the claim the whole repair rests on, and it is the one claim a
        single chunk cannot carry: the walk is explicit and its result frozen.
        """

        lines = contract.assert_whole_tree_writers()
        self.assertTrue(any("exactly one writer chunk" in line for line in lines), lines)
        for name, (writer,) in contract.EXPECTED_CONTAINER_WRITERS.items():
            with self.subTest(container=name):
                self.assertEqual(
                    contract.EXPECTED_CONTAINER_WRITERS[name], (writer,),
                    f"{name} must have exactly one writer chunk",
                )
        # The three containers of this repair family share the shape.
        self.assertEqual(
            sorted(contract.EXPECTED_CONTAINER_WRITERS),
            sorted(contract.PREVIOUS_CONTAINERS + (contract.CONTAINER,)),
        )
        self.assertEqual(
            sorted(contract.EXPECTED_CONTAINER_WRITERS),
            ["le_tbChapterItemInfo", "le_tbChapterTaskInfo", "le_tbTaskByChapterId"],
        )


class ModelTests(unittest.TestCase):
    """The recovered chain, driven the way RUN-0032 drove it."""

    def test_the_nil_container_reproduces_the_run_0032_frame_for_frame(self) -> None:
        world = ModelWorld()
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        outcome = world.finish_switch()

        self.assertIsNotNone(outcome.error)
        assert outcome.error is not None
        self.assertEqual(
            (outcome.error.module, outcome.error.line, outcome.error.function),
            ("chapter/pkgChapterTaskMgr.lua", 62, "isTaskReceived"),
        )
        self.assertIn("attempt to index global 'le_tbChapterTaskInfo' (a nil value)",
                      outcome.error.detail)
        # The frame RUN-0032's logcat carries, as a string.
        self.assertIn(
            "chapter/pkgChapterTaskMgr.lua:62: attempt to index global "
            "'le_tbChapterTaskInfo' (a nil value)",
            outcome.error.args[0],
        )

    def test_the_raise_skips_every_statement_after_line_875(self) -> None:
        """`-- doFinishSwitch` is printed at 882 -- seven statements too late."""

        world = ModelWorld()
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        outcome = world.finish_switch()

        self.assertEqual(outcome.statements_run, [875])
        self.assertFalse(outcome.finish_switch_printed)
        self.assertFalse(outcome.enter_scene_dispatched)
        self.assertEqual(outcome.enter_scene_calls, [])
        self.assertFalse(outcome.loading_torn_down)
        # ... and the two facts the runner's own predicates read.
        self.assertTrue(outcome.switch_seen)
        self.assertFalse(outcome.finish_switch)

    def test_the_clients_own_start_creates_the_container_and_the_finish_completes(
        self,
    ) -> None:
        world = ModelWorld()
        world.apply_packet(TASK_START_ID, ())
        self.assertEqual(world.tasks.le_tbChapterTaskInfo, {})
        world.apply_packet(TASK_END_ID, ())
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        outcome = world.finish_switch()

        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.finish_switch)
        self.assertTrue(outcome.enter_scene_dispatched)
        self.assertEqual(outcome.enter_scene_calls, [CHAPTER1_SCENE])
        self.assertTrue(outcome.loading_torn_down)
        self.assertEqual(outcome.statements_run, [
            875, 876, 880, 881, 882, 884, 885, 886, 890,
        ])

    def test_an_empty_container_answers_no_for_every_task_of_every_chapter(self) -> None:
        """The honest cost of an empty container, stated rather than hidden."""

        world = ModelWorld()
        world.apply_packet(TASK_START_ID, ())
        for chapter_id in range(1, 5):
            with self.subTest(chapter=chapter_id):
                self.assertFalse(world.tasks.is_task_received(chapter_id, chapter_id))
                self.assertFalse(world.tasks.is_task_completed(chapter_id, chapter_id))
                self.assertFalse(world.info.is_item_over(chapter_id, chapter_id))

    def test_an_unknown_chapter_is_never_written_and_never_read(self) -> None:
        world = ModelWorld()
        world.apply_packet(TASK_START_ID, ())
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        world.finish_switch()
        # The finish callback asks for chapter 1 and only chapter 1; the container
        # is still the client's own empty literal afterwards.
        self.assertEqual(world.tasks.le_tbChapterTaskInfo, {})

    def test_the_wrong_point_is_not_a_tap(self) -> None:
        world = ModelWorld()
        with self.assertRaises(AssertionError):
            world.confirm_tap((1310, 700), contract.RUN0032_CONFIRM_RECT)
        self.assertEqual(world.chapter_scene.enter_chapter_calls, [])


class WireTests(unittest.TestCase):
    """The real decoded wire of a real server, v8 (broken) and v9 (repaired)."""

    def test_the_v8_wire_carries_no_chapter_task_packet(self) -> None:
        """The profile RUN-0032 ran: the switch is issued and never finishes."""

        server, events = new_server(profile=get_profile_v8())
        segment = terminal_segment(login(server, V8_SEGMENT_PACKETS))
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V8_TERMINAL_IDS)
        self.assertNotIn(TASK_START_ID, ids)
        self.assertNotIn(TASK_END_ID, ids)

        world = ModelWorld()
        world.apply_segment(segment)
        self.assertIsNone(world.tasks.le_tbChapterTaskInfo)
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        outcome = world.finish_switch()
        self.assertTrue(outcome.switch_seen)
        self.assertFalse(outcome.finish_switch)
        self.assertEqual(outcome.statements_run, [875])

    def test_the_v9_wire_carries_exactly_the_pair_in_order(self) -> None:
        server, events = new_server(profile=get_profile_v9())
        segment = terminal_segment(login(server, V9_SEGMENT_PACKETS))
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V9_TERMINAL_IDS)
        # Before the v7 chapter-item pair -- the client's own registration table
        # puts the task family (385-388) immediately before the item family.
        self.assertEqual(ids.index(TASK_START_ID) + 1, ids.index(TASK_END_ID))
        self.assertEqual(ids.index(TASK_END_ID) + 1, ids.index(389))
        # The insertion really only inserted.
        self.assertEqual(
            [wire_id for wire_id in ids if wire_id not in SENT_IDS],
            V8_TERMINAL_IDS,
        )
        for packet in segment:
            if packet.spec.protocol_id in SENT_IDS:
                self.assertEqual(packet.fields, ())
                self.assertEqual(len(packet.wire), 3)  # header + checksum, no body

    def test_the_real_wire_completes_the_switch_the_run_0032_could_not(self) -> None:
        server, events = new_server(profile=get_profile_v9())
        segment = terminal_segment(login(server, V9_SEGMENT_PACKETS))
        world = ModelWorld()
        world.apply_segment(segment)
        self.assertEqual(world.tasks.le_tbChapterTaskInfo, {})
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        outcome = world.finish_switch()

        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.switch_seen)
        self.assertTrue(outcome.finish_switch)
        self.assertEqual(outcome.enter_scene_calls, [CHAPTER1_SCENE])
        self.assertTrue(outcome.loading_torn_down)
        # The two lines the runner's own predicates read, on the model's own logcat.
        self.assertIn(f"switchScene  {CHAPTER1_SCENE}", outcome.logcat)
        self.assertIn("-- doFinishSwitch", outcome.logcat)
        self.assertNotIn("LUA ERROR", outcome.logcat)

    def test_the_pair_is_the_clients_own_reset_not_a_task_row(self) -> None:
        server, events = new_server(profile=get_profile_v9())
        login(server, V9_SEGMENT_PACKETS)
        events = [event for event in events
                  if event.get("event") == "tcp_chapter_task_state_sent"]
        self.assertEqual([event["protocol_id"] for event in events], list(SENT_IDS))
        self.assertEqual([event["snapshot_index"] for event in events], [0, 1])
        self.assertEqual([event["snapshot_section"] for event in events],
                         ["initChapterTaskStart", "initChapterTaskEnd"])
        self.assertEqual(events[0]["snapshot_packet_ids"], list(SENT_IDS))
        self.assertEqual(events[0]["snapshot_packet_count"], 2)
        self.assertEqual(events[0]["policy_compatibility_version"], PROFILE_ID_V9)
        self.assertEqual(events[0]["profile_id"], PROFILE_ID_V9)
        self.assertEqual(events[0]["parent_profile_id"], PROFILE_ID_V8)
        self.assertEqual(events[0]["container_policy"], CHAPTER_TASK_CONTAINER_POLICY)
        self.assertEqual(events[0]["container_policy_origin"], "synthetic_compatibility")
        self.assertEqual(events[0]["item_rows_sent"], 0)
        self.assertEqual(events[0]["historical_ordering"],
                         CHAPTER_TASK_HISTORICAL_ORDERING)
        self.assertEqual(events[0]["ordering_origin"], "synthetic_compatibility")
        self.assertIs(events[0]["historical_values_recovered"], False)
        # The client reads the container when it finishes entering the chapter, i.e.
        # long after this login segment; the S2C283 is the packet whose dispatch
        # starts the main-city switch that precedes the whole journey.
        self.assertIs(events[0]["precedes_scene_switch_packet"], True)
        self.assertEqual(events[0]["scene_switch_boundary_protocol_id"], 283)
        # No chapter-task row ever left the server.
        for wire_id in UNREGISTERED_SIBLINGS:
            self.assertEqual(
                [event for event in events if event.get("protocol_id") == wire_id],
                [],
            )


class NegativeTests(unittest.TestCase):
    """Every way this repair could be wrong, each failing closed."""

    def test_a_row_without_the_start_raises_in_the_handler(self) -> None:
        """386 indexes the global 385 creates -- so the order is not cosmetic."""

        world = ModelWorld()
        with self.assertRaises(RecoveredClientError) as caught:
            world.tasks.init_chapter_task_info(1, 1001)
        self.assertEqual(caught.exception.line, 26)
        self.assertEqual(caught.exception.function, "initChapterTaskInfo")

    def test_the_end_alone_is_not_the_reset(self) -> None:
        world = ModelWorld()
        world.apply_packet(TASK_END_ID, ())
        self.assertIsNone(world.tasks.le_tbChapterTaskInfo)
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        self.assertEqual(world.finish_switch().statements_run, [875])

    def test_the_start_alone_already_clears_the_read(self) -> None:
        """The end handler only closes the block: it is not what creates it."""

        world = ModelWorld()
        world.apply_packet(TASK_START_ID, ())
        world.confirm_tap(contract.RUN0032_TAP_POINT, contract.RUN0032_CONFIRM_RECT)
        outcome = world.finish_switch()
        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.finish_switch)

    def test_the_policy_refuses_a_foreign_shape(self) -> None:
        policy = get_profile().chapter_task_init
        assert policy is not None
        self.assertEqual(policy.row_count, CHAPTER_TASK_ROW_COUNT)
        self.assertEqual(policy.row_count, 0)
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(policy.packets(),
                         (("initChapterTaskStart", ()), ("initChapterTaskEnd", ())))
        self.assertEqual(CHAPTER_TASK_PACKET_ORDER,
                         ("initChapterTaskStart", "initChapterTaskEnd"))
        self.assertEqual(
            [name for name, _ in policy.packets()],
            [name for _, (name, _) in sorted(contract.TASK_SENT_S2C.items())],
        )
        for provenance in (policy.container_provenance, policy.ordering_provenance):
            with self.subTest(provenance=provenance.source[:40]):
                self.assertEqual(provenance.origin, "synthetic_compatibility")
                self.assertEqual(provenance.compatibility_version, PROFILE_ID_V9)
                self.assertTrue(provenance.replaceable)
                self.assertTrue(provenance.reason)
        # The "no row" half of the decision is the container's, and it is stated:
        # what the client's own initialiser creates is an EMPTY container, and a row
        # would assert chapter-task state this archive has no producer for (D-21).
        self.assertIn("D-21", policy.container_provenance.reason)
        self.assertIn("NO task row is sent", policy.container_provenance.reason)
        self.assertIn("le_tbChapterTaskInfo = {}",
                      policy.container_provenance.reason)
        # The ordering half says which members are sent and which are not.
        self.assertIn("386 and 388 stay unregistered",
                      policy.ordering_provenance.reason)

    def test_every_earlier_profile_carries_no_chapter_task_state(self) -> None:
        """A v1-v8 server's stream is byte-for-byte what it was."""

        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.chapter_task_init)
        # v9 introduced it and v10 inherits it unchanged; every later version sees
        # the very same object, so the pair can never drift under a bump.
        self.assertIsNotNone(get_profile_v9().chapter_task_init)
        self.assertIs(
            get_profile().chapter_task_init, get_profile_v9().chapter_task_init
        )

    def test_a_v8_server_really_sends_the_v8_segment(self) -> None:
        """The falsification is a real server, not a profile object."""

        server, events = new_server(profile=get_profile_v8())
        segment = terminal_segment(login(server, V8_SEGMENT_PACKETS))
        self.assertEqual(
            [event for event in events
             if event.get("event") == "tcp_chapter_task_state_sent"],
            [],
        )
        # ... and it is still the v8 stream: the experience-task pair it added is
        # there, and the story snapshot still follows the whole policy block.
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids.index(1307) + 1, ids.index(1309))
        self.assertEqual(ids.index(1309) + 1, ids.index(411))

    def test_the_frozen_v1_domain_is_untouched(self) -> None:
        """The v9 policy is additive: not one core provenance moved."""

        profile = get_profile_v9()
        for provenance in profile._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        self.assertEqual(profile.parent_profile_id, PROFILE_ID_V8)
        self.assertEqual(get_profile_v8().parent_profile_id, PROFILE_ID_V7)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
