"""TASK-0109 -- the chapter-machine container, and the statement v9 did not reach.

RUN-0032 (TASK-0103) broke at ``pkgChapterSceneMgr.lua:112``; the v9 chapter-task
container (TASK-0104/D-92) was supposed to clear it.  RUN-0033 (TASK-0108) shows it
did -- and that the callback's very next statement broke on a *second* container::

    23:50:40.744  Android ACTION_DOWN x[0]=1310.0 y[0]=645.0   (the confirm tap)
    23:50:40.840  switchScene  chapter_qiandengzhen_1  3  nil
    23:50:41.372  ABScene  ab_chapter_qiandengzhen_1
    23:50:41.968  -- static combile --
    23:50:41.980  ABScene  ab_chapter_qiandengzhen_1_juqing
    23:50:42.305  LUA ERROR: chapter/pkgChapterMachineMgr.lua:46
                  attempt to index global 'le_tbChapterMachineInfo' (a nil value)

The load **completed** again -- the traceback's outer frames are the loader's own
completion -- and what raised is the finish callback ``pkgSceneMgr.doFinishSwitch``
calls at its line 875.  Only the failing *line inside the callback* differs::

    RUN-0032  chapter/pkgChapterSceneMgr.lua:112  le_finishCallBack  (initIndex2TypeId)
    RUN-0033  chapter/pkgChapterSceneMgr.lua:114  le_finishCallBack  (initScene)
                  -> chapter/pkgChapterObjectMgr.lua:287
                  -> chapter/pkgChapterMachineMgr.lua:312 initAllMachineObj
                  -> chapter/pkgChapterMachineMgr.lua:46  isMachineCompleted

``le_tbChapterMachineInfo`` is created by exactly one recovered function, and the
owning module registers it ITSELF: ``chapter/pkgChapterMachineMgr.lua`` writes
``s2c.initChapterMachineStart = function() le_tbChapterMachineInfo = {} end``
(lines 18-20) straight into the shared ``s2c`` table -- unlike the task and item
families, which route through ``socket/nc/pkgGuankaMapNcMgr``.

This module contains a faithful model of that recovered chain, pinned to the shipped
chunks by ``max_decoder/analyze_chapter_machine_init.py`` (which the tests import and
run, so the model cannot silently drift).  Every failure carries the recovered
``module:line`` and function, so a model result can be compared with RUN-0033's
logcat frame for frame.

The tests then drive the model with the *real decoded wire* of a real
``RevivalTCPServer`` over a real socket session: a v9 server (the profile RUN-0033
ran, which clears RUN-0032's break and stops one statement short of this one) for the
falsification and the active v10 server for the repair.
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
    CHAPTER_MACHINE_CONTAINER_POLICY,
    CHAPTER_MACHINE_HISTORICAL_ORDERING,
    CHAPTER_MACHINE_PACKET_ORDER,
    CHAPTER_MACHINE_ROW_COUNT,
    PROFILE_ID_V1,
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

import analyze_chapter_machine_init as contract  # noqa: E402

MACHINE_START_ID = 400
MACHINE_INFO_ID = 401
MACHINE_END_ID = 402
MACHINE_UPDATE_ID = 403
MACHINE_MAP_ID = 399
SENT_IDS = (MACHINE_START_ID, MACHINE_END_ID)
UNREGISTERED_SIBLINGS = (MACHINE_INFO_ID, MACHINE_UPDATE_ID)

#: The terminal segment of a v9 login (the profile RUN-0033 ran) and of the active
#: v10 login: v10 INSERTS the chapter-machine pair immediately AFTER the v7
#: chapter-item pair, so every v9 packet keeps its own position relative to the
#: others and only two bytes of header-plus-checksum are added in front of 1307.
V9_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 385, 387,
                   389, 391, 1307, 1309, 411, 283]
V10_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 385, 387,
                    389, 391, 400, 402, 1307, 1309, 411, 283]

#: Read counts that get the whole login + terminal segment off the wire.
BASE_LOGIN_PACKETS = 5
V9_SEGMENT_PACKETS = 24
V10_SEGMENT_PACKETS = 26

#: RUN-0033's own switch and the scene it asked for.
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


class RecoveredChapterMachineMgr:
    """``chapter/pkgChapterMachineMgr.lua`` -- the container's only owner."""

    MODULE = "chapter/pkgChapterMachineMgr.lua"
    DECLARE_LINE = 6
    START_DECLARE_LINE = 18
    START_CONTAINER_LINE = 19
    START_LAST_LINE = 20
    INFO_REQUIRES_LINE = 23
    INFO_FIRST_LINE = 22
    INFO_LAST_LINE = 25
    END_FIRST_LINE = 27
    END_LAST_LINE = 29
    UPDATE_FIRST_LINE = 31
    UPDATE_LAST_LINE = 35
    COMPLETED_FIRST_LINE = 44
    COMPLETED_CHAPTER_LINE = 45
    COMPLETED_READ_LINE = 46
    COMPLETED_LAST_LINE = 47
    ALL_FIRST_LINE = 296
    ALL_ASK_LINE = 312
    ALL_LAST_LINE = 332

    def __init__(self) -> None:
        #: `le_tbChapterMachineInfo = le_tbChapterMachineInfo or nil` (chunk line 6)
        #: -- the module's own default, and the whole reason for this task.
        self.le_tbChapterMachineInfo = None
        #: `le_tbCtrlObj` (line 8) -- written by `initAllMachineObj` at 325 and NOT
        #: read by anything on this path; kept so the model matches the chunk.
        self.le_tbCtrlObj = None

    # -- the client's own S2C handlers (registered by this very module) ---------

    def init_chapter_machine_start(self) -> None:
        """The client's ONLY creator of the container (lines 18-20)."""

        self.le_tbChapterMachineInfo = {}

    def init_chapter_machine_info(self, d_chapter_id, d_machine_id) -> None:
        """Requires the container: it is indexed at line 23, unguarded."""

        if self.le_tbChapterMachineInfo is None:
            raise RecoveredClientError(
                self.MODULE, self.INFO_REQUIRES_LINE, "initChapterMachineInfo",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        self.le_tbChapterMachineInfo.setdefault(d_chapter_id, {})[d_machine_id] = True

    def init_chapter_machine_end(self) -> None:
        """The client's own empty end handler (lines 27-29)."""

    def update_chapter_machine(self, d_chapter_id, d_machine_id) -> None:
        """Lines 31-35: the per-machine push, guarded the same way at 33."""

        if self.le_tbChapterMachineInfo is None:
            raise RecoveredClientError(
                self.MODULE, 33, "updateChapterMachine",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        self.le_tbChapterMachineInfo.setdefault(d_chapter_id, {})[d_machine_id] = True

    # -- the unguarded reader and the walk that reaches it ---------------------

    def is_machine_completed(self, d_chapter_id, d_machine_id) -> bool:
        """Lines 44-47: the chapter id at 45, then `[dChapterId]` at 46.

        The raise RUN-0033's logcat carries is the SECOND statement's own
        ``GETTABLE`` on the nil global -- Lua names the global, not the local.
        """

        if self.le_tbChapterMachineInfo is None:
            raise RecoveredClientError(
                self.MODULE, self.COMPLETED_READ_LINE, "isMachineCompleted",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        return (
            self.le_tbChapterMachineInfo.get(d_chapter_id, {}).get(d_machine_id)
            is not None
        )

    def init_all_machine_obj(self, d_chapter_id, machine_ids) -> list:
        """Lines 296-332: walks the chapter's own machineList, asking per machine.

        ``tbMachineInfo`` is checked for nil at 311 before the 312 call, which is
        why the read is reached at all on a chapter whose config exists -- and the
        answer decides ``replaceModel`` (313) vs the interactive build (315-325).
        """

        replaced = []
        for machine_id in machine_ids:
            if self.is_machine_completed(d_chapter_id, machine_id):
                replaced.append(machine_id)
        return replaced


class RecoveredChapterObjectMgr:
    """``chapter/pkgChapterObjectMgr.lua`` -- the scene's own initializer."""

    MODULE = "chapter/pkgChapterObjectMgr.lua"
    INIT_SCENE_FIRST_LINE = 272
    INIT_INDEX_2_TYPE_ID_LINE = 385
    INIT_SCENE_TASK_LINE = 286
    INIT_SCENE_MACHINE_LINE = 287
    INIT_SCENE_LAST_LINE = 290

    def __init__(self, machines: RecoveredChapterMachineMgr,
                 tasks=None) -> None:
        self.machines = machines
        self.tasks = tasks
        #: Every `initAllMachineObj` call this scene made, in order.
        self.init_all_machine_obj_calls: list[int] = []
        self.init_all_task_calls: list[int] = []

    def init_index_2_type_id(self, d_chapter_id):
        """Lines 382-390: RUN-0032's own break, cleared by the v9 container."""

        if self.tasks is not None:
            # `pkgChapterInfoMgr.getActiveMap` -> `isItemOver` -> `isTaskReceived`:
            # the v9 container's unguarded read, still reached from here.
            self.tasks.is_task_received(d_chapter_id, d_chapter_id)
        return {"chapter_id": d_chapter_id}

    def init_scene(self, d_chapter_id, machine_ids):
        """Lines 272-290: the machine walk is reached at 287, AFTER 286."""

        if self.tasks is not None:
            self.init_all_task_calls.append(d_chapter_id)
            self.tasks.init_all_task()
        self.init_all_machine_obj_calls.append(d_chapter_id)
        return self.machines.init_all_machine_obj(d_chapter_id, machine_ids)


class RecoveredChapterSceneMgr:
    """``chapter/pkgChapterSceneMgr.lua`` -- the callback's own module."""

    MODULE = "chapter/pkgChapterSceneMgr.lua"
    ENTER_CHAPTER_FIRST_LINE = 56
    RUN0032_CALL_LINE = 112
    STORY_TRIGGER_CALL_LINE = 113
    RUN0033_CALL_LINE = 114
    CLOSURE_LINE = 131
    CALLBACK_FIRST_LINE = 102
    CALLBACK_LAST_LINE = 131
    SWITCH_SCENE_LINE = 133
    ENTER_CHAPTER_LAST_LINE = 134

    def __init__(self, objects: RecoveredChapterObjectMgr) -> None:
        self.objects = objects
        self.scene = None
        #: Every statement of `le_finishCallBack` that actually executed, in order.
        self.statements_run: list[int] = []
        self.story_enter_calls: list[int] = []

    def enter_chapter(self, d_chapter_id: int) -> None:
        """Lines 56-134: it closes over the finish callback at 131 and hands it to
        `pkgSceneMgr.switchScene` at 133."""

        self.scene.switch_scene(
            CHAPTER1_SCENE, 3,
            lambda: self.le_finish_callback(d_chapter_id, (9001, 9002)),
        )

    def le_finish_callback(self, d_chapter_id: int, machine_ids) -> None:
        """Lines 102-131 -- the function Lua names after the global it is stored in.

        RUN-0032's frame reads ``in function 'le_finishCallBack'`` but the frame's
        module is *this* one, because `pkgSceneMgr.switchScene` stores the closure
        in the global of that name.  The statements run in source order, so a raise
        at 114 is proof that 112 and 113 both returned.
        """

        self.statements_run = [103, 105, 106, 108, 109, 111]
        self.statements_run.append(self.RUN0032_CALL_LINE)
        self.objects.init_index_2_type_id(d_chapter_id)  # line 112
        self.statements_run.append(self.STORY_TRIGGER_CALL_LINE)
        self.story_enter_calls.append(d_chapter_id)  # line 113
        self.statements_run.append(self.RUN0033_CALL_LINE)
        self.objects.init_scene(d_chapter_id, machine_ids)  # line 114 -- the raise
        self.statements_run.extend([117, 119, 121, 122, 124, 125, 128, 129])


class RecoveredTaskMgr:
    """``chapter/pkgChapterTaskMgr.lua`` -- the v9 container's module, for 286."""

    MODULE = "chapter/pkgChapterTaskMgr.lua"

    def __init__(self) -> None:
        self.le_tbChapterTaskInfo = None
        self.init_all_task_calls = 0

    def init_chapter_task_start(self) -> None:
        self.le_tbChapterTaskInfo = {}

    def init_chapter_task_end(self) -> None:
        pass

    def is_task_received(self, d_chapter_id, d_task_id) -> bool:
        """Line 62 -- RUN-0032's own break, kept so the model can still show it."""

        if self.le_tbChapterTaskInfo is None:
            raise RecoveredClientError(
                self.MODULE, 62, "isTaskReceived",
                "attempt to index global 'le_tbChapterTaskInfo' (a nil value)",
            )
        return self.le_tbChapterTaskInfo.get(d_chapter_id, {}).get(d_task_id) is not None

    def init_all_task(self) -> None:
        """Line 286's callee: it reads the same container, and it returns once the
        v9 pair has run."""

        self.init_all_task_calls += 1
        self.is_task_received(1, 1)


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
        self.scene_name = ""
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
        """The lines the runner's own predicates read, as this model prints them."""

        lines = [f"switchScene  {CHAPTER1_SCENE}  3  nil"]
        if self.finish_switch_printed:
            lines.append("-- doFinishSwitch")
        if self.error is not None:
            lines.append(f"LUA ERROR: {self.error}")
        return "\n".join(lines) + "\n"


class ModelWorld:
    """The four recovered modules wired the way the client wires them."""

    def __init__(self) -> None:
        self.machines = RecoveredChapterMachineMgr()
        self.tasks = RecoveredTaskMgr()
        self.objects = RecoveredChapterObjectMgr(self.machines, self.tasks)
        self.chapter_scene = RecoveredChapterSceneMgr(self.objects)
        self.scene = RecoveredSceneMgr()
        self.chapter_scene.scene = self.scene
        self.scene_name = CHAPTER1_SCENE

    # -- the wire, applied through the client's OWN registered handlers ---------

    def apply_packet(self, protocol_id: int, fields: tuple) -> None:
        if protocol_id == MACHINE_START_ID:
            self.machines.init_chapter_machine_start()
        elif protocol_id == MACHINE_INFO_ID:
            self.machines.init_chapter_machine_info(*fields)
        elif protocol_id == MACHINE_END_ID:
            self.machines.init_chapter_machine_end()
        elif protocol_id == 385:
            self.tasks.init_chapter_task_start()
        elif protocol_id == 387:
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
            raise AssertionError("the tap must be the one RUN-0033 delivered")
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
                C2S_REGISTRY.by_id(2), ("t0109".encode("utf-8"), 1), writer).wire)
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
            ["break_moved", "container_lifecycle", "declared_ranges",
             "do_finish_switch", "finish_chain", "protocol_table",
             "run0033_stack", "run0033_timeline", "run0033_v9_live",
             "self_registration", "shared_chunks_agree", "unguarded_reader",
             "whole_tree_writers"],
        )
        for name, lines in sections.items():
            with self.subTest(section=name):
                self.assertTrue(lines, f"{name} produced no evidence")

    def test_the_two_sent_ids_are_the_clients_own_handlers(self) -> None:
        self.assertEqual(
            [(S2C_REGISTRY.by_id(wire_id).name, S2C_REGISTRY.by_id(wire_id).fmt)
             for wire_id in SENT_IDS],
            [("initChapterMachineStart", ""), ("initChapterMachineEnd", "")],
        )
        # The row writer and the push stay unregistered on the server side: a row
        # would assert chapter-machine state this archive has no producer for (D-21).
        for wire_id in UNREGISTERED_SIBLINGS:
            with self.subTest(wire_id=wire_id):
                with self.assertRaises(KeyError):
                    S2C_REGISTRY.by_id(wire_id)
        # The `chapterMapMachine` push that opens the family is unregistered too.
        with self.assertRaises(KeyError):
            S2C_REGISTRY.by_id(MACHINE_MAP_ID)
        self.assertEqual(contract.MACHINE_OMITTED_S2C, UNREGISTERED_SIBLINGS)
        self.assertEqual(
            [name for name, _ in contract.MACHINE_SENT_S2C.values()],
            list(CHAPTER_MACHINE_PACKET_ORDER),
        )

    def test_the_container_and_its_only_creator_are_the_recovered_ones(self) -> None:
        self.assertEqual(contract.CONTAINER, "le_tbChapterMachineInfo")
        self.assertEqual(contract.CONTAINER_MODULE, RecoveredChapterMachineMgr.MODULE)
        self.assertEqual(contract.START_HANDLER, "initChapterMachineStart")
        self.assertEqual(contract.END_HANDLER, "initChapterMachineEnd")

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
        # The four containers of this repair family share the shape.
        self.assertEqual(
            sorted(contract.EXPECTED_CONTAINER_WRITERS),
            sorted(contract.PREVIOUS_CONTAINERS + (contract.CONTAINER,)),
        )
        self.assertEqual(
            sorted(contract.EXPECTED_CONTAINER_WRITERS),
            ["le_tbChapterItemInfo", "le_tbChapterMachineInfo",
             "le_tbChapterTaskInfo", "le_tbTaskByChapterId"],
        )

    def test_the_machine_family_registers_itself_and_has_no_nc_wrapper(self) -> None:
        """The one structural difference from the other three containers.

        `pkgChapterTaskMgr`/`pkgChapterInfoMgr` expose module globals that
        `socket/nc/pkgGuankaMapNcMgr` wraps; `pkgChapterMachineMgr` writes
        `s2c.<name>` itself and is the only chunk in the tree that names any of the
        five handlers.
        """

        self.assertEqual(
            RecoveredChapterMachineMgr.START_DECLARE_LINE,
            contract.EXPECTED_RANGES["MACHINE_MGR:" + contract.START_HANDLER][1],
        )
        sections = contract.assert_self_registration(contract.read_chunks())
        self.assertTrue(
            any("written by the owning module itself" in line for line in sections),
            sections,
        )


class ModelTests(unittest.TestCase):
    """The recovered chain, driven the way RUN-0033 drove it."""

    def test_the_nil_container_reproduces_the_run_0033_frame_for_frame(self) -> None:
        world = ModelWorld()
        # The v9 pair ran (RUN-0033's own server.log proves it), so 112 is cleared
        # and the break is the very next statement's callee.
        world.apply_packet(385, ())
        world.apply_packet(387, ())
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        outcome = world.finish_switch()

        self.assertIsNotNone(outcome.error)
        assert outcome.error is not None
        self.assertEqual(
            (outcome.error.module, outcome.error.line, outcome.error.function),
            ("chapter/pkgChapterMachineMgr.lua", 46, "isMachineCompleted"),
        )
        self.assertIn(
            "attempt to index global 'le_tbChapterMachineInfo' (a nil value)",
            outcome.error.detail,
        )
        # The frame RUN-0033's logcat carries, as a string.
        self.assertIn(
            "chapter/pkgChapterMachineMgr.lua:46: attempt to index global "
            "'le_tbChapterMachineInfo' (a nil value)",
            outcome.error.args[0],
        )
        # ... and the two model call sites its traceback's upper frames name.
        self.assertEqual(world.objects.init_all_machine_obj_calls, [1])
        self.assertEqual(world.objects.init_all_task_calls, [1])

    def test_the_break_moved_two_statements_past_the_line_v9_cleared(self) -> None:
        """The whole point of RUN-0033: same callback, later line."""

        world = ModelWorld()
        world.apply_packet(385, ())
        world.apply_packet(387, ())
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        outcome = world.finish_switch()

        assert outcome.error is not None
        # 112 and 113 both RAN, which is only possible if the v9 container existed.
        self.assertEqual(
            world.chapter_scene.statements_run,
            [103, 105, 106, 108, 109, 111, 112, 113, 114],
        )
        self.assertEqual(world.chapter_scene.story_enter_calls, [1])
        # The two break POINTS, which are the callback's own lines -- the innermost
        # frame's line is the callee's (46), and it is the statement list above that
        # pins which callback line reached it.
        self.assertEqual(contract.RUN0032_BREAK[1], 112)
        self.assertEqual(contract.RUN0033_BREAK[1], 114)
        self.assertEqual(
            contract.RUN0033_BREAK[1], contract.RUN0032_BREAK[1] + 2
        )
        self.assertEqual(world.chapter_scene.RUN0033_CALL_LINE,
                         contract.RUN0033_BREAK[1])
        self.assertEqual(world.chapter_scene.RUN0032_CALL_LINE,
                         contract.RUN0032_BREAK[1])

    def test_without_v9_the_run_0032_break_is_reproduced_instead(self) -> None:
        """The falsification: what RUN-0033 would have shown had v9 not shipped."""

        world = ModelWorld()
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        outcome = world.finish_switch()

        self.assertIsNotNone(outcome.error)
        assert outcome.error is not None
        self.assertEqual(
            (outcome.error.module, outcome.error.line, outcome.error.function),
            ("chapter/pkgChapterTaskMgr.lua", 62, "isTaskReceived"),
        )
        self.assertEqual(world.chapter_scene.statements_run, [
            103, 105, 106, 108, 109, 111, 112,
        ])
        # The machine walk was never reached: 114 is after 112.
        self.assertEqual(world.objects.init_all_machine_obj_calls, [])

    def test_the_raise_skips_every_statement_after_line_875(self) -> None:
        """`-- doFinishSwitch` is printed at 882 -- seven statements too late."""

        world = ModelWorld()
        world.apply_packet(385, ())
        world.apply_packet(387, ())
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
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
        world.apply_packet(385, ())
        world.apply_packet(387, ())
        world.apply_packet(MACHINE_START_ID, ())
        self.assertEqual(world.machines.le_tbChapterMachineInfo, {})
        world.apply_packet(MACHINE_END_ID, ())
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        outcome = world.finish_switch()

        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.finish_switch)
        self.assertTrue(outcome.enter_scene_dispatched)
        self.assertEqual(outcome.enter_scene_calls, [CHAPTER1_SCENE])
        self.assertTrue(outcome.loading_torn_down)
        self.assertEqual(outcome.statements_run, [
            875, 876, 880, 881, 882, 884, 885, 886, 890,
        ])
        # The callback ran all the way to its own end.
        self.assertEqual(world.chapter_scene.statements_run[-1], 129)

    def test_an_empty_container_answers_no_for_every_machine_of_every_chapter(
        self,
    ) -> None:
        """The honest cost of an empty container, stated rather than hidden."""

        world = ModelWorld()
        world.apply_packet(MACHINE_START_ID, ())
        for chapter_id in range(1, 5):
            with self.subTest(chapter=chapter_id):
                self.assertFalse(
                    world.machines.is_machine_completed(chapter_id, 9001)
                )
        # ... so nothing is swapped to its finished model.
        self.assertEqual(
            world.machines.init_all_machine_obj(1, (9001, 9002)), []
        )

    def test_an_unknown_chapter_is_never_written_and_never_read(self) -> None:
        world = ModelWorld()
        world.apply_packet(385, ())
        world.apply_packet(MACHINE_START_ID, ())
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        world.finish_switch()
        # The finish callback asks for chapter 1 and only chapter 1; the container
        # is still the client's own empty literal afterwards.
        self.assertEqual(world.machines.le_tbChapterMachineInfo, {})

    def test_the_wrong_point_is_not_a_tap(self) -> None:
        world = ModelWorld()
        with self.assertRaises(AssertionError):
            world.confirm_tap((1310, 700), contract.RUN0033_CONFIRM_RECT)
        self.assertFalse(world.scene.scene_name)


class WireTests(unittest.TestCase):
    """The real decoded wire of a real server, v9 (broken) and v10 (repaired)."""

    def test_the_v9_wire_carries_no_chapter_machine_packet(self) -> None:
        """The profile RUN-0033 ran: the switch is issued and still never finishes."""

        server, events = new_server(profile=get_profile_v9())
        segment = terminal_segment(login(server, V9_SEGMENT_PACKETS))
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V9_TERMINAL_IDS)
        for wire_id in (MACHINE_START_ID, MACHINE_END_ID, MACHINE_INFO_ID,
                        MACHINE_UPDATE_ID, MACHINE_MAP_ID):
            self.assertNotIn(wire_id, ids)

        world = ModelWorld()
        world.apply_segment(segment)
        self.assertIsNone(world.machines.le_tbChapterMachineInfo)
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        outcome = world.finish_switch()
        self.assertTrue(outcome.switch_seen)
        self.assertFalse(outcome.finish_switch)
        self.assertEqual(outcome.statements_run, [875])
        # And the v9 container really was created by that very segment, which is
        # why this run reached 114 at all instead of stopping at 112.
        self.assertEqual(world.tasks.le_tbChapterTaskInfo, {})
        assert outcome.error is not None
        self.assertEqual(outcome.error.line, 46)

    def test_the_v10_wire_carries_exactly_the_pair_in_order(self) -> None:
        # The v10 stream explicitly: the expectations below are v10-terminal
        # facts, and TASK-0167 moved the ACTIVE profile to v13 (whose stream
        # carries one more packet before S2C283).
        server, events = new_server(profile=get_profile_v10())
        segment = terminal_segment(login(server, V10_SEGMENT_PACKETS))
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V10_TERMINAL_IDS)
        # AFTER the v7 chapter-item pair -- the client's own registration table puts
        # the machine family (399-403) after the item family (389-393).
        self.assertEqual(ids.index(391) + 1, ids.index(MACHINE_START_ID))
        self.assertEqual(ids.index(MACHINE_START_ID) + 1, ids.index(MACHINE_END_ID))
        self.assertEqual(ids.index(MACHINE_END_ID) + 1, ids.index(1307))
        # The insertion really only inserted.
        self.assertEqual(
            [wire_id for wire_id in ids if wire_id not in SENT_IDS],
            V9_TERMINAL_IDS,
        )
        for packet in segment:
            if packet.spec.protocol_id in SENT_IDS:
                self.assertEqual(packet.fields, ())
                self.assertEqual(len(packet.wire), 3)  # header + checksum, no body

    def test_the_real_wire_completes_the_switch_the_run_0033_could_not(self) -> None:
        # Pinned to the v10 profile for the same reason as the wire-shape test
        # above: the login read count is the v10 terminal segment's.
        server, events = new_server(profile=get_profile_v10())
        segment = terminal_segment(login(server, V10_SEGMENT_PACKETS))
        world = ModelWorld()
        world.apply_segment(segment)
        self.assertEqual(world.machines.le_tbChapterMachineInfo, {})
        self.assertEqual(world.tasks.le_tbChapterTaskInfo, {})
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
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

    def test_the_pair_is_the_clients_own_reset_not_a_machine_row(self) -> None:
        # The v10 stream explicitly, so this stays a statement about the stream
        # that introduced the pair: TASK-0140 made v11 active and a later bump may
        # never re-version what v10 sent.
        server, events = new_server(profile=get_profile_v10())
        login(server, V10_SEGMENT_PACKETS)
        events = [event for event in events
                  if event.get("event") == "tcp_chapter_machine_state_sent"]
        self.assertEqual([event["protocol_id"] for event in events], list(SENT_IDS))
        self.assertEqual([event["snapshot_index"] for event in events], [0, 1])
        self.assertEqual([event["snapshot_section"] for event in events],
                         ["initChapterMachineStart", "initChapterMachineEnd"])
        self.assertEqual(events[0]["snapshot_packet_ids"], list(SENT_IDS))
        self.assertEqual(events[0]["snapshot_packet_count"], 2)
        self.assertEqual(events[0]["policy_compatibility_version"], PROFILE_ID_V10)
        self.assertEqual(events[0]["profile_id"], PROFILE_ID_V10)
        self.assertEqual(events[0]["parent_profile_id"], PROFILE_ID_V9)
        self.assertEqual(events[0]["container_policy"],
                         CHAPTER_MACHINE_CONTAINER_POLICY)
        self.assertEqual(events[0]["container_policy_origin"],
                         "synthetic_compatibility")
        self.assertEqual(events[0]["machine_rows_sent"], 0)
        self.assertEqual(events[0]["historical_ordering"],
                         CHAPTER_MACHINE_HISTORICAL_ORDERING)
        self.assertEqual(events[0]["ordering_origin"], "synthetic_compatibility")
        self.assertIs(events[0]["historical_values_recovered"], False)
        # The client reads the container when it finishes entering the chapter, i.e.
        # long after this login segment; the S2C283 is the packet whose dispatch
        # starts the main-city switch that precedes the whole journey.
        self.assertIs(events[0]["precedes_scene_switch_packet"], True)
        self.assertEqual(events[0]["scene_switch_boundary_protocol_id"], 283)
        # No chapter-machine row ever left the server.
        for wire_id in UNREGISTERED_SIBLINGS + (MACHINE_MAP_ID,):
            self.assertEqual(
                [event for event in events if event.get("protocol_id") == wire_id],
                [],
            )


class NegativeTests(unittest.TestCase):
    """Every way this repair could be wrong, each failing closed."""

    def test_a_row_without_the_start_raises_in_the_handler(self) -> None:
        """401 indexes the global 400 creates -- so the order is not cosmetic."""

        world = ModelWorld()
        with self.assertRaises(RecoveredClientError) as caught:
            world.machines.init_chapter_machine_info(1, 9001)
        self.assertEqual(caught.exception.line, 23)
        self.assertEqual(caught.exception.function, "initChapterMachineInfo")

    def test_the_update_without_the_start_raises_too(self) -> None:
        world = ModelWorld()
        with self.assertRaises(RecoveredClientError) as caught:
            world.machines.update_chapter_machine(1, 9001)
        self.assertEqual(caught.exception.line, 33)
        self.assertEqual(caught.exception.function, "updateChapterMachine")

    def test_the_end_alone_is_not_the_reset(self) -> None:
        world = ModelWorld()
        world.apply_packet(385, ())
        world.apply_packet(MACHINE_END_ID, ())
        self.assertIsNone(world.machines.le_tbChapterMachineInfo)
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        self.assertEqual(world.finish_switch().statements_run, [875])

    def test_the_start_alone_already_clears_the_read(self) -> None:
        """The end handler only closes the block: it is not what creates it."""

        world = ModelWorld()
        world.apply_packet(385, ())
        world.apply_packet(MACHINE_START_ID, ())
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        outcome = world.finish_switch()
        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.finish_switch)

    def test_the_machine_pair_alone_is_not_enough(self) -> None:
        """The two containers are independent: 114's callee is v9's own module."""

        world = ModelWorld()
        world.apply_packet(MACHINE_START_ID, ())
        world.apply_packet(MACHINE_END_ID, ())
        world.confirm_tap(contract.RUN0033_TAP_POINT, contract.RUN0033_CONFIRM_RECT)
        outcome = world.finish_switch()
        assert outcome.error is not None
        self.assertEqual(outcome.error.line, 62)
        self.assertEqual(outcome.error.function, "isTaskReceived")

    def test_the_policy_refuses_a_foreign_shape(self) -> None:
        policy = get_profile().chapter_machine_init
        assert policy is not None
        self.assertEqual(policy.row_count, CHAPTER_MACHINE_ROW_COUNT)
        self.assertEqual(policy.row_count, 0)
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(policy.packets(),
                         (("initChapterMachineStart", ()),
                          ("initChapterMachineEnd", ())))
        self.assertEqual(CHAPTER_MACHINE_PACKET_ORDER,
                         ("initChapterMachineStart", "initChapterMachineEnd"))
        self.assertEqual(
            [name for name, _ in policy.packets()],
            [name for _, (name, _) in sorted(contract.MACHINE_SENT_S2C.items())],
        )
        for provenance in (policy.container_provenance, policy.ordering_provenance):
            with self.subTest(provenance=provenance.source[:40]):
                self.assertEqual(provenance.origin, "synthetic_compatibility")
                self.assertEqual(provenance.compatibility_version, PROFILE_ID_V10)
                self.assertTrue(provenance.replaceable)
                self.assertTrue(provenance.reason)
        # The "no row" half of the decision is the container's, and it is stated:
        # what the client's own initialiser creates is an EMPTY container, and a row
        # would assert chapter-machine state this archive has no producer for (D-21).
        self.assertIn("D-21", policy.container_provenance.reason)
        self.assertIn("NO machine row is sent", policy.container_provenance.reason)
        self.assertIn("le_tbChapterMachineInfo = {}",
                      policy.container_provenance.reason)
        self.assertIn("pkgChapterMachineMgr.lua:312",
                      policy.container_provenance.source)
        self.assertIn(":46 isMachineCompleted", policy.container_provenance.source)
        self.assertIn("doFinishSwitch", policy.container_provenance.source)
        # The ordering half says which members are sent and which are not.
        self.assertIn("401 and 403 stay unregistered",
                      policy.ordering_provenance.reason)

    def test_every_earlier_profile_carries_no_chapter_machine_state(self) -> None:
        """A v1-v9 server's stream is byte-for-byte what it was."""

        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.chapter_machine_init)
        self.assertIsNotNone(get_profile().chapter_machine_init)

    def test_a_v9_server_really_sends_the_v9_segment(self) -> None:
        """The falsification is a real server, not a profile object."""

        server, events = new_server(profile=get_profile_v9())
        segment = terminal_segment(login(server, V9_SEGMENT_PACKETS))
        self.assertEqual(
            [event for event in events
             if event.get("event") == "tcp_chapter_machine_state_sent"],
            [],
        )
        # ... and it is still the v9 stream: the chapter-task pair it added is there,
        # the experience-task pair is there, and the story snapshot still follows the
        # whole policy block.
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids.index(385) + 1, ids.index(387))
        self.assertEqual(ids.index(387) + 1, ids.index(389))
        self.assertEqual(ids.index(391) + 1, ids.index(1307))
        self.assertEqual(ids.index(1309) + 1, ids.index(411))
        self.assertEqual(ids.index(411) + 1, ids.index(283))

    def test_the_frozen_v1_domain_is_untouched(self) -> None:
        """The v10 policy is additive: not one core provenance moved."""

        # The frozen v10 profile by name -- TASK-0140 made v11 active, and this
        # test is about what v10 is, not about which version is active.
        profile = get_profile_v10()
        for provenance in profile._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        self.assertEqual(profile.parent_profile_id, PROFILE_ID_V9)
        self.assertEqual(get_profile_v9().parent_profile_id, PROFILE_ID_V8)
        # The three earlier container policies are inherited as the SAME objects,
        # so nothing a v7/v8/v9 server sent can drift under this bump.
        v9 = get_profile_v9()
        self.assertIs(profile.chapter_item_init, v9.chapter_item_init)
        self.assertIs(profile.experience_task_init, v9.experience_task_init)
        self.assertIs(profile.chapter_task_init, v9.chapter_task_init)
        self.assertIsNone(v9.chapter_machine_init)

    def test_the_v10_stream_is_the_v9_stream_plus_one_pair(self) -> None:
        """Additivity, measured on two real servers' own bytes."""

        v9_server, _ = new_server(profile=get_profile_v9())
        v9_ids = [p.spec.protocol_id for p in
                  terminal_segment(login(v9_server, V9_SEGMENT_PACKETS))]
        v10_server, _ = new_server(profile=get_profile_v10())
        v10_ids = [p.spec.protocol_id for p in
                   terminal_segment(login(v10_server, V10_SEGMENT_PACKETS))]
        self.assertEqual(v9_ids, V9_TERMINAL_IDS)
        self.assertEqual(v10_ids, V10_TERMINAL_IDS)
        self.assertEqual(
            [wire_id for wire_id in v10_ids if wire_id not in SENT_IDS], v9_ids
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
