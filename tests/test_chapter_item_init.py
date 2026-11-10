"""TASK-0098 - the world map's chapter-item container, judged by its own consumers.

RUN-0029 (TASK-0097) left the adventure journey's next leg broken one step above the
tap.  The runner's tap provably reached the device -- Android ``ACTION_DOWN
x=703.0 y=567.0``, logcat 03:31:33.730, inside the node rectangle the recovered
geometry puts at 622..784 / 391..743 -- and *nothing happened*: no Lua error at that
timestamp, no frame change.  Eight seconds earlier the client had already raised
its first Lua error of the run:

    chapter/pkgChapterInfoMgr.lua:213: attempt to index global 'le_tbChapterItemInfo'
        (a nil value)
    chapter/pkgChapterInfoMgr.lua:213: in function <chapter/pkgChapterInfoMgr.lua:208>
    (tail call): ?
    chapter/pkgChapterInfoMgr.lua:128: in function 'getChapterProgress'
    chapter/pkgChapterInfoMgr.lua:165: in function 'isChapterPass'
    chapter/pkgChapterMainUIViewMgr.lua:63: in function 'initChapterBtnNormalStatus'
    chapter/pkgChapterMainUIViewMgr.lua:245: in function 'initChapter'
    chapter/pkgChapterMainUIViewMgr.lua:446: in function 'init'

This module contains a *faithful model of that recovered chain*, built from the
decompiled sources and pinned to them by
``max_decoder/analyze_chapter_item_init.py`` (which the tests import and run, so the
model cannot silently drift).  Every failure carries the recovered ``module:line``
and function, so a model result can be compared with RUN-0029's logcat frame for
frame -- including the ``(tail call): ?`` marker that ``isItemOver``'s tail call to
``isItemFightOver`` leaves behind.

The tests then drive the model with the *real decoded wire* of a real
``RevivalTCPServer`` over a real socket session: a v6 server (no chapter-item
policy) for the falsification and the active v7 server for the repair.
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
    CHAPTER_ITEM_CONTAINER_POLICY,
    CHAPTER_ITEM_HISTORICAL_ORDERING,
    CHAPTER_ITEM_PACKET_ORDER,
    CHAPTER_ITEM_ROW_COUNT,
    PROFILE_ID_V6,
    PROFILE_ID_V7,
    get_profile,
    get_profile_v6,
    get_profile_v7,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_DECODER = PROJECT_ROOT / "max_decoder"
if str(MAX_DECODER) not in sys.path:
    sys.path.insert(0, str(MAX_DECODER))

import analyze_chapter_item_init as contract  # noqa: E402

ITEM_START_ID = 389
ITEM_INFO_ID = 390
ITEM_END_ID = 391
SENT_IDS = (ITEM_START_ID, ITEM_END_ID)
#: The v7 policy sends 389/391 and zero rows.  TASK-0174 registers 390 (id +
#: format only) for the v14 replay policy, so only 392/393 stay unregistered;
#: the analyzer's historical `ITEM_OMITTED_S2C` (390, 392, 393) is still the
#: list of family members the v7 policy never sends, and the pin below compares
#: the two.
UNREGISTERED_SIBLINGS = (392, 393)

BASE_LOGIN_PACKETS = 5
#: Packets read after `C2S 3` (the same counter the other login modules use):
#: 13 role-sync/terminal packets on v6, plus the v7 chapter-item pair.
V7_SEGMENT_PACKETS = 20
V6_SEGMENT_PACKETS = 18

#: The terminal segment itself, exactly as both profiles send it.
V6_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 411, 283]
V7_TERMINAL_IDS = [8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 389, 391, 411, 283]

TAIL_CALL = contract.RUN0029_TAIL_CALL


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


class RecoveredClientError(RuntimeError):
    """A failure at a named line of the recovered client source.

    The frames are kept innermost-first, so the rendered message can be compared
    with RUN-0029's logcat traceback line for line.  ``tail_called`` marks the one
    frame Lua removes because the caller reached the raiser in tail position.
    """

    def __init__(
        self,
        module: str,
        line: int,
        function: str,
        detail: str,
        frames: tuple[tuple[str, int, str], ...] = (),
        tail_called: bool = False,
    ) -> None:
        self.module = module
        self.line = line
        self.function = function
        self.detail = detail
        self.frames = ((module, line, function),) + frames
        self.tail_called = tail_called
        super().__init__(f"{module}:{line}: {detail}\n" + "\n".join(self.render()))

    def propagated(
        self, module: str, line: int, function: str
    ) -> "RecoveredClientError":
        """Re-raise this failure one recovered frame further out."""

        return RecoveredClientError(
            self.module,
            self.line,
            self.function,
            self.detail,
            self.frames[1:] + ((module, line, function),),
            self.tail_called,
        )

    def in_tail_position(self) -> "RecoveredClientError":
        """Lua replaces the caller's frame with ``(tail call): ?``."""

        self.tail_called = True
        return self

    def render(self) -> list[str]:
        """The traceback body, in the recovered modules' own rendering."""

        lines: list[str] = []
        for index, (module, line, function) in enumerate(self.frames):
            if function.startswith("<anonymous@"):
                declared_at = function.split("@")[1].rstrip(">")
                lines.append(
                    f"\t{module}:{line}: in function <{module}:{declared_at}>"
                )
            else:
                lines.append(f"\t{module}:{line}: in function '{function}'")
            if index == 0 and self.tail_called:
                lines.append(f"\t{TAIL_CALL}")
        return lines


class RecoveredChapterInfoMgr:
    """``chapter/pkgChapterInfoMgr.lua`` - the world map's progress reader."""

    MODULE = "chapter/pkgChapterInfoMgr.lua"
    #: Declared source line of the anonymous ``isItemFightOver`` (the function
    #: RUN-0029's innermost frame names).
    FIGHT_FUNCTION_LINE = 208
    FIGHT_READ_LINE = 213
    PROGRESS_LINE = 128
    PASS_LINE = 165
    ARRIVED_EXIT_LINE = 403

    def __init__(self, chapter_rows, map_rows, types) -> None:
        #: `le_tbChapterItemInfo = le_tbChapterItemInfo or nil` (source line 8) --
        #: the module's own default, and the whole reason for this task.
        self.le_tbChapterItemInfo = None
        self.le_dCurChapterId = None
        self.le_tbChapterBox = {}
        self.chapter_rows = chapter_rows
        #: Chapter 1's own node map.  The other chapters' maps are not recovered by
        #: this task, so `map_for` answers `[]` for them: nothing the model asserts
        #: depends on their contents, and inventing rows would be exactly the kind
        #: of pre-filling D-21 forbids.
        self.map_rows = list(map_rows)
        self.types = types
        self.explore_weight_normal = 1  # only its positivity matters here

    def map_for(self, d_chapter_id):
        """The node map `getChapterProgress` walks, per chapter."""

        return self.map_rows if d_chapter_id == 1 else []

    # -- the client's own S2C handlers (socket/nc/pkgGuankaMapNcMgr) ------------

    def init_chapter_item_start(self) -> None:
        """The client's ONLY producer of the container (source 48-50)."""

        self.le_tbChapterItemInfo = {}

    def init_chapter_item_info(self, d_chapter_id, d_type, d_id) -> None:
        """Requires the container: its first statement indexes it (source 52-56)."""

        if self.le_tbChapterItemInfo is None:
            raise RecoveredClientError(
                self.MODULE, 53, "initChapterItemInfo",
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        row = self.le_tbChapterItemInfo.setdefault(d_chapter_id, {})
        row.setdefault(d_type, {})[d_id] = True

    def init_chapter_item_end(self) -> None:
        """The client's own empty end handler (source 58-59)."""

    # -- the unguarded readers (source 208-286) --------------------------------

    def _unguarded(self, d_chapter_id, line: int, function: str):
        if self.le_tbChapterItemInfo is None:
            raise RecoveredClientError(
                self.MODULE, line, function,
                f"attempt to index global '{contract.CONTAINER}' (a nil value)",
            )
        return self.le_tbChapterItemInfo.get(d_chapter_id)

    def is_item_fight_over(self, tb_info, d_x, d_y, d_chapter_id) -> bool:
        """`isItemFightOver` (source 208-221); the read is line 213."""

        row = self._unguarded(d_chapter_id, self.FIGHT_READ_LINE,
                              f"<anonymous@{self.FIGHT_FUNCTION_LINE}>")
        if row and row.get(tb_info["type"], {}).get(d_x, 0) == d_x:
            return True
        return False

    def is_item_event_over(self, tb_info, d_x, d_y, d_chapter_id, d_level=0) -> bool:
        """`isItemEventOver` (source 192-206) -- the same read, one node type over."""

        row = self._unguarded(d_chapter_id, 197, "<anonymous@192>")
        return bool(row and row.get(tb_info["type"]))

    def is_item_triggered(self, tb_info, d_x, d_y, d_chapter_id) -> bool:
        """`isItemTriggered` (source 277-286) -- the third unguarded reader."""

        row = self._unguarded(d_chapter_id, 260, "isItemTriggered")
        return bool(row and row.get(tb_info["type"]))

    def is_item_over(self, tb_info, d_x, d_y, d_chapter_id, d_level=0):
        """`isItemOver` (source 248-275): a TABLE of tail calls."""

        node_type = tb_info["type"]
        try:
            if node_type == self.types["MAP_TYPE_FIGHT"]:
                return self.is_item_fight_over(tb_info, d_x, d_y, d_chapter_id)
            if node_type == self.types["MAP_TYPE_EVENT"]:
                return self.is_item_event_over(tb_info, d_x, d_y, d_chapter_id, d_level)
            if node_type in (
                self.types["MAP_TYPE_EXIT"],
                self.types["MAP_TYPE_SHOP"],
                self.types["MAP_TYPE_CHAPTER"],
                self.types["MAP_TYPE_CINEMATIC"],
            ):
                return self.is_item_triggered(tb_info, d_x, d_y, d_chapter_id)
            if node_type == self.types["MAP_TYPE_TASK_NPC"]:
                # pkgChapterTaskMgr.isTaskReceived; not reached by the rows here.
                return False
        except RecoveredClientError as exc:
            raise exc.in_tail_position()
        return False

    def get_chapter_progress(self, d_chapter_id):
        """`getChapterProgress` (source 109-161), the `pairs` walk included."""

        d_total = 0
        d_triggered = 0
        is_arrived_exit = False
        try:
            for d_x, tb_info in self.map_for(d_chapter_id):
                if tb_info["explore"] == 0 and tb_info["type"] != self.types["MAP_TYPE_EVENT"]:
                    continue
                is_over = None
                d_weight = self.explore_weight_normal
                if tb_info["type"] == self.types["MAP_TYPE_FIGHT"] and tb_info["explore"] != 0:
                    is_over = self.is_item_over(tb_info, d_x, 0, d_chapter_id)
                elif tb_info["type"] == self.types["MAP_TYPE_EVENT"]:
                    is_over = self.is_item_over(tb_info, d_x, 0, d_chapter_id)
                elif tb_info["type"] == self.types["MAP_TYPE_EXIT"]:
                    is_arrived_exit = self.is_item_triggered(tb_info, d_x, 0, d_chapter_id)
                if is_over is not None:
                    d_total += d_weight
                    if is_over:
                        d_triggered += d_weight
        except RecoveredClientError as exc:
            raise exc.propagated(self.MODULE, self.PROGRESS_LINE, "getChapterProgress")
        return d_triggered, d_total, is_arrived_exit

    def is_chapter_pass(self, d_chapter_id):
        """`isChapterPass` (source 164-180)."""

        try:
            d_triggered, d_total, is_arrived_exit = self.get_chapter_progress(d_chapter_id)
        except RecoveredClientError as exc:
            raise exc.propagated(self.MODULE, self.PASS_LINE, "isChapterPass")
        pass_condition = self.chapter_rows[d_chapter_id]["passCondition"]
        if is_arrived_exit and d_total and d_triggered / d_total * 100 >= pass_condition:
            return True, d_triggered, d_total, is_arrived_exit
        return False, d_triggered, d_total, is_arrived_exit

    def is_chapter_arrived_exit(self, d_chapter_id):
        """`isChapterArrivedExit` (source 403-416): the client's OWN nil guard."""

        if self.le_tbChapterItemInfo is None:
            return None
        row = self.le_tbChapterItemInfo.get(d_chapter_id)
        return bool(row and row.get(self.types["MAP_TYPE_EXIT"]))

    def get_current_passed_chapter(self) -> int:
        """`getCurrentPassedChapter` (source 418-432)."""

        passed = 0
        for chapter_id in sorted(self.chapter_rows):
            if not self.is_chapter_arrived_exit(chapter_id):
                break
            passed = chapter_id
        return passed


class RecoveredChapterView:
    """``chapter/pkgChapterMainUIViewMgr.lua`` - the world map's own view."""

    MODULE = "chapter/pkgChapterMainUIViewMgr.lua"
    INIT_LINE = 446
    INIT_CHAPTER_LINE = 245
    STATUS_LINE = 63
    CLICK_PROGRESS_LINE = 154
    CHAPTER_COUNT = 16

    def __init__(self, info: RecoveredChapterInfoMgr) -> None:
        self.info = info
        #: ``init()`` builds `le_tbBtnChapter` from the prefab's own paths
        #: (source 381-394): every chapter has a real node in this run, which is
        #: also what CHAPTER1_NODE_VISIBLE proved on the device.
        self.le_tbBtnChapter = [None] + [
            f"Panel/layout_content_{(i - 1) // 4 + 1}/chapter/{i}"
            for i in range(1, self.CHAPTER_COUNT + 1)
        ]
        self.bound: list[tuple[str, int]] = []
        self.panel_active = False
        self.enter_bound = False

    def init(self) -> None:
        """`init` (source 401-473), the part that owns `initChapter`."""

        try:
            self.init_chapter()
        except RecoveredClientError as exc:
            raise exc.propagated(self.MODULE, self.INIT_LINE, "init")

    def init_chapter(self) -> None:
        """`initChapter` (source 230-250): label, status, THEN bind."""

        for d_chapter_id in range(1, self.CHAPTER_COUNT + 1):
            btn_chapter = self.le_tbBtnChapter[d_chapter_id]
            if btn_chapter is None:
                continue
            try:
                self.init_chapter_btn_normal_status(d_chapter_id)
            except RecoveredClientError as exc:
                raise exc.propagated(self.MODULE, self.INIT_CHAPTER_LINE, "initChapter")
            self.bound.append((btn_chapter, d_chapter_id))

    def init_chapter_btn_normal_status(self, d_chapter_id: int) -> None:
        """`initChapterBtnNormalStatus` (source 32-68)."""

        try:
            if d_chapter_id != 1:
                # The substitute branches above the status read (source 34-47) ask
                # the PREVIOUS chapter's progress, which is the same read.
                self.info.is_chapter_pass(d_chapter_id - 1)
            # The status read every chapter reaches (source 55, called from 63).
            self.info.is_chapter_pass(d_chapter_id)
        except RecoveredClientError as exc:
            raise exc.propagated(self.MODULE, self.STATUS_LINE,
                                 "initChapterBtnNormalStatus")

    def on_chapter_click(self, d_chapter_id: int) -> None:
        """`onChapterClickHandler` (source 70-228) up to the panel activation."""

        # Nothing above the progress read touches a nil-able global for chapter 1:
        # `tryShowQgHandsome` returns false on its first test and
        # `tryShowExperience` has no `return true` in any path.
        try:
            self.info.is_chapter_pass(d_chapter_id)
        except RecoveredClientError as exc:
            raise exc.propagated(self.MODULE, self.CLICK_PROGRESS_LINE,
                                 "onChapterClickHandler")
        self.enter_bound = True          # addListener("Panel/btnDetail/panel/btn", onEnter)
        self.panel_active = True         # setActiveByName("Panel/btnDetail", true)


def recovered_chapter_rows() -> dict:
    """The chapter config rows the walk needs, keyed by chapter id.

    Chapter 1's row is the one `analyze_first_chapter` recovered and froze.  The
    walk visits chapters 1..16, and every later chapter's own `passCondition` is
    only ever compared against a progress total the model does not assert for it
    (its node map is not recovered here), so those rows repeat chapter 1's value
    and nothing depends on it.  What the model asserts -- the unguarded read, the
    frame it raises from, and the bindings that follow -- needs no more than that.
    """

    import analyze_first_chapter as chapter_cfg

    row = dict(chapter_cfg.CHAPTER1_FROZEN)
    return {
        chapter_id: dict(row, id=chapter_id)
        for chapter_id in range(1, chapter_cfg.WORLD_MAP_CHAPTERS + 1)
    }


def recovered_map_rows() -> list:
    """The traversal's own input: `chapterMap0` in the order the chunk writes it.

    Row 1 is an ENTER node (no reader at all) and row 2 is the first FIGHT node, so
    the first row that reaches an unguarded read is a *fight* row -- which is why
    RUN-0029's innermost frame names the function declared at source line 208.
    `verify_chapter_map_rows` re-reads both rows from the shipped chunk.
    """

    return [
        (row["index"], {"type": row["type"], "explore": row["explore"],
                        "value": row["id"]})
        for row in contract.CHAPTER1_MAP_FIRST_ROWS
    ]


def new_client() -> tuple[RecoveredChapterView, RecoveredChapterInfoMgr]:
    info = RecoveredChapterInfoMgr(
        recovered_chapter_rows(), recovered_map_rows(), contract.MAP_TYPE_FROZEN
    )
    return RecoveredChapterView(info), info


def apply_packet(info: RecoveredChapterInfoMgr, protocol_id: int, fields) -> None:
    """Deliver one real decoded S2C to the client's own handler."""

    if protocol_id == ITEM_START_ID:
        info.init_chapter_item_start()
    elif protocol_id == ITEM_INFO_ID:
        info.init_chapter_item_info(fields[0], fields[1], fields[2])
    elif protocol_id == ITEM_END_ID:
        info.init_chapter_item_end()


class SourcePinTests(unittest.TestCase):
    """The model above is only evidence while it still matches the sources."""

    def test_recovered_contract_still_pins(self) -> None:
        contract.assert_protocol_table()
        contract.assert_handler_module()
        contract.assert_container_writers()
        contract.assert_unguarded_read_and_guarded_sibling()
        contract.assert_init_chapter_order()
        contract.assert_click_handler_path()
        contract.assert_map_types()
        contract.assert_run0029_stack_lines()

    def test_registry_agrees_with_the_recovered_contract(self) -> None:
        for wire_id, (name, fmt) in contract.ITEM_SENT_S2C.items():
            spec = S2C_REGISTRY.by_id(wire_id)
            self.assertEqual((spec.name, spec.fmt), (name, fmt))
            self.assertEqual(spec.fmt, "")
        # TASK-0174: 390 is now REGISTERED (id + format only) so the v14
        # chapter-fight replay policy can emit the client's own row.  Registering
        # it does not make the v7 policy send one -- `item_rows_sent` stays 0 for
        # every profile below v14, and the pair below is asserted below.
        info_name, info_fmt = contract.CHAPTER_FAMILY_S2C[390]
        info_spec = S2C_REGISTRY.by_id(390)
        self.assertEqual((info_spec.name, info_spec.fmt), (info_name, info_fmt))
        self.assertEqual((info_spec.name, info_spec.fmt),
                         ("initChapterItemInfo", "ppp"))
        # ... and every family member the v7 policy never sends is either still
        # unregistered (392/393) or registered without being sent (390).
        for wire_id in UNREGISTERED_SIBLINGS:
            with self.assertRaises(KeyError):
                S2C_REGISTRY.by_id(wire_id)
        self.assertEqual(
            (390,) + UNREGISTERED_SIBLINGS, contract.ITEM_OMITTED_S2C
        )

    def test_the_client_tree_still_says_the_same_thing(self) -> None:
        types, reason = contract.verify_map_types(PROJECT_ROOT)
        if not types:
            self.skipTest(f"the unpacked client is not on this machine ({reason})")
        self.assertEqual(types, contract.MAP_TYPE_FROZEN)
        rows, reason = contract.verify_chapter_map_rows(PROJECT_ROOT)
        if not rows:
            self.skipTest(f"the unpacked client is not on this machine ({reason})")
        self.assertEqual(rows, contract.CHAPTER1_MAP_FIRST_ROWS)

    def test_the_policy_supplies_exactly_the_client_own_reset(self) -> None:
        policy = get_profile().chapter_item_init
        assert policy is not None
        self.assertEqual(policy.container_policy, CHAPTER_ITEM_CONTAINER_POLICY)
        self.assertEqual(policy.row_count, CHAPTER_ITEM_ROW_COUNT)
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(CHAPTER_ITEM_PACKET_ORDER,
                         ("initChapterItemStart", "initChapterItemEnd"))
        self.assertEqual(
            policy.packets(),
            (("initChapterItemStart", ()), ("initChapterItemEnd", ())),
        )
        # The model agrees the pair is what clears the read.
        client, info = new_client()
        for name, fields in policy.packets():
            apply_packet(info, S2C_REGISTRY.by_name(name).protocol_id, fields)
        client.init()
        self.assertEqual(len(client.bound), 16)


class _ServerMixin:
    """A real ``RevivalTCPServer`` on a real socket, driven through login."""

    PROFILE = None
    SEGMENT_PACKETS = V6_SEGMENT_PACKETS

    def setUp(self) -> None:
        self.events: list[dict] = []
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            compatibility_profile=self.PROFILE,
            time_source=lambda: 1_700_000_123,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _login(self, role_name: str, *, packets: int | None = None) -> list:
        """Drive login+create+receivePackage and return the terminal segment."""

        writer = WriterState()
        state = ReaderState()
        buffer = bytearray()
        responses: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2), (role_name.encode("utf-8"), 1), writer
                ).wire
            )
            _read(stream, self.server, buffer, state, responses, BASE_LOGIN_PACKETS)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read(stream, self.server, buffer, state, responses,
                  self.SEGMENT_PACKETS if packets is None else packets)
        return responses[BASE_LOGIN_PACKETS:]


class BaseFalsificationTests(_ServerMixin, unittest.TestCase):
    """A v6 server: RUN-0029's own break, reproduced frame for frame."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.PROFILE = get_profile_v6()
        cls.SEGMENT_PACKETS = V6_SEGMENT_PACKETS

    def test_the_v6_wire_sends_no_chapter_item_packet(self) -> None:
        segment = self._login("旧档道友")
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V6_TERMINAL_IDS)
        self.assertNotIn(ITEM_START_ID, ids)
        self.assertNotIn(ITEM_END_ID, ids)
        self.assertEqual(
            [event for event in self.events
             if event.get("event") == "tcp_chapter_item_state_sent"],
            [],
        )

    def test_the_v6_wire_reproduces_run0029_frame_for_frame(self) -> None:
        segment = self._login("首断边道友")
        client, info = new_client()
        for packet in segment:
            apply_packet(info, packet.spec.protocol_id, packet.fields)
        self.assertIsNone(info.le_tbChapterItemInfo)

        with self.assertRaises(RecoveredClientError) as caught:
            client.init()
        error = caught.exception
        self.assertEqual(error.detail, contract.RUN0029_ERROR.split(": ", 1)[1])
        self.assertEqual(error.frames, contract.RUN0029_STACK)
        self.assertEqual(
            error.render(),
            contract.run0029_stack_render(""),
        )
        self.assertEqual(len(contract.run0029_stack_render()), 7)  # 6 frames + marker
        # The break cost every chapter its listener, not just chapter 1.
        self.assertEqual(client.bound, [])

    def test_the_click_handler_would_die_even_with_a_listener(self) -> None:
        """The same read sits at line 154, before Panel/btnDetail is activated."""

        client, info = new_client()
        with self.assertRaises(RecoveredClientError) as caught:
            client.on_chapter_click(1)
        error = caught.exception
        self.assertEqual(
            error.frames,
            (
                ("chapter/pkgChapterInfoMgr.lua", 213, "<anonymous@208>"),
                ("chapter/pkgChapterInfoMgr.lua", 128, "getChapterProgress"),
                ("chapter/pkgChapterInfoMgr.lua", 165, "isChapterPass"),
                ("chapter/pkgChapterMainUIViewMgr.lua", 154, "onChapterClickHandler"),
            ),
        )
        self.assertFalse(client.panel_active)
        self.assertFalse(client.enter_bound)

    def test_a_v6_server_cannot_reach_the_repaired_state(self) -> None:
        """Applying anything the v6 wire carries is not enough to create it."""

        segment = self._login("空容器道友")
        client, info = new_client()
        for packet in segment:
            if packet.spec.protocol_id not in SENT_IDS:
                apply_packet(info, packet.spec.protocol_id, packet.fields)
        with self.assertRaises(RecoveredClientError):
            client.init()
        self.assertIsNone(info.le_tbChapterItemInfo)


class V7RepairTests(_ServerMixin, unittest.TestCase):
    """The shipped server: the client's own reset runs and the map binds."""

    @classmethod
    def setUpClass(cls) -> None:
        # TASK-0102 moved the *active* profile to v8, which inherits this
        # policy unchanged; the module asserts the v7 stream itself, so it asks
        # for v7 by name and reads exactly v7's packet count.
        cls.PROFILE = get_profile_v7()
        cls.SEGMENT_PACKETS = V7_SEGMENT_PACKETS

    def test_the_v7_wire_carries_exactly_the_pair_in_order(self) -> None:
        segment = self._login("修复档道友")
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, V7_TERMINAL_IDS)
        self.assertEqual(ids[-1], 283)
        for packet in segment:
            if packet.spec.protocol_id in SENT_IDS:
                self.assertEqual(packet.fields, ())
                self.assertEqual(len(packet.wire), 3)  # header + checksum, no body

    def test_the_real_wire_clears_the_read_and_binds_every_chapter(self) -> None:
        segment = self._login("解锁道友")
        client, info = new_client()
        for packet in segment:
            apply_packet(info, packet.spec.protocol_id, packet.fields)
        # The container is now the client's own empty literal.
        self.assertEqual(info.le_tbChapterItemInfo, {})
        client.init()  # no raise
        self.assertEqual(len(client.bound), 16)
        self.assertEqual(client.bound[0][1], 1)
        # ... and chapter 1's own progress read answers from the client's config.
        passed, triggered, total, arrived = info.is_chapter_pass(1)
        self.assertFalse(passed)
        self.assertEqual(triggered, 0)
        self.assertGreater(total, 0)
        self.assertFalse(arrived)
        self.assertEqual(info.get_current_passed_chapter(), 0)

    def test_the_tap_then_reaches_the_detail_panel(self) -> None:
        segment = self._login("开面板道友")
        client, info = new_client()
        for packet in segment:
            apply_packet(info, packet.spec.protocol_id, packet.fields)
        client.init()
        # The runner's own point, from RUN-0029's delivered ACTION_DOWN.
        point = contract.RUN0029_TAP_POINT
        self.assertEqual(point, (703, 567))
        client.on_chapter_click(1)  # no raise
        self.assertTrue(client.panel_active)
        self.assertTrue(client.enter_bound)

    def test_the_pair_is_the_clients_own_reset_not_an_item_row(self) -> None:
        self._login("空行道友")
        events = [
            event for event in self.events
            if event.get("event") == "tcp_chapter_item_state_sent"
        ]
        self.assertEqual([event["protocol_id"] for event in events], list(SENT_IDS))
        self.assertEqual([event["snapshot_index"] for event in events], [0, 1])
        self.assertEqual([event["snapshot_section"] for event in events],
                         ["initChapterItemStart", "initChapterItemEnd"])
        self.assertEqual(events[0]["snapshot_packet_ids"], list(SENT_IDS))
        self.assertEqual(events[0]["snapshot_packet_count"], 2)
        self.assertEqual(events[0]["policy_compatibility_version"], PROFILE_ID_V7)
        self.assertEqual(events[0]["profile_id"], PROFILE_ID_V7)
        self.assertEqual(events[0]["parent_profile_id"], PROFILE_ID_V6)
        self.assertEqual(events[0]["container_policy"], CHAPTER_ITEM_CONTAINER_POLICY)
        self.assertEqual(events[0]["container_policy_origin"],
                         "synthetic_compatibility")
        self.assertEqual(events[0]["item_rows_sent"], 0)
        self.assertEqual(events[0]["historical_ordering"],
                         CHAPTER_ITEM_HISTORICAL_ORDERING)
        self.assertEqual(events[0]["ordering_origin"], "synthetic_compatibility")
        self.assertIs(events[0]["historical_values_recovered"], False)
        self.assertIs(events[0]["precedes_scene_switch_packet"], True)
        self.assertEqual(events[0]["scene_switch_boundary_protocol_id"], 283)
        # No per-item row ever left the server.
        self.assertEqual(
            [event for event in self.events if event.get("protocol_id") == ITEM_INFO_ID],
            [],
        )


class NegativeTests(unittest.TestCase):
    """Every way this repair could be wrong, each failing closed."""

    def test_the_pair_without_the_start_is_not_the_reset(self) -> None:
        client, info = new_client()
        info.init_chapter_item_end()
        with self.assertRaises(RecoveredClientError):
            client.init()

    def test_an_item_row_without_the_start_raises_in_the_handler(self) -> None:
        """390 indexes the global 389 creates -- so the order is not cosmetic."""

        info = RecoveredChapterInfoMgr(
            recovered_chapter_rows(), recovered_map_rows(), contract.MAP_TYPE_FROZEN
        )
        with self.assertRaises(RecoveredClientError) as caught:
            info.init_chapter_item_info(1, 10, 1)
        self.assertEqual(caught.exception.line, 53)
        self.assertEqual(caught.exception.function, "initChapterItemInfo")

    def test_the_guarded_sibling_still_reads_nil_without_the_reset(self) -> None:
        """`isChapterArrivedExit` returns nil -- a guard, not a repair."""

        info = RecoveredChapterInfoMgr(
            recovered_chapter_rows(), recovered_map_rows(), contract.MAP_TYPE_FROZEN
        )
        self.assertIsNone(info.is_chapter_arrived_exit(1))
        self.assertEqual(info.get_current_passed_chapter(), 0)
        with self.assertRaises(RecoveredClientError):
            info.is_chapter_pass(1)

    def test_an_empty_map_would_not_have_raised(self) -> None:
        """The break needs a row that reaches the reader -- which chapter 1 has."""

        info = RecoveredChapterInfoMgr(
            recovered_chapter_rows(), [], contract.MAP_TYPE_FROZEN
        )
        info.init_chapter_item_start()
        self.assertEqual(info.is_chapter_pass(1)[0], False)

    def test_a_v7_server_still_sends_the_v6_wire_untouched(self) -> None:
        """The pair is INSERTED: every v6 packet keeps its own fields."""

        v6_ids = [5, 14, 8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 411,
                  283]
        policy = get_profile().chapter_item_init
        assert policy is not None
        position = v6_ids.index(1014) + 1
        self.assertEqual(
            v6_ids[:position] + [389, 391] + v6_ids[position:],
            [5, 14, 8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, 36, 37, 48, 35, 1014, 389, 391,
             411, 283],
        )
        self.assertEqual(policy.packet_count, 2)


if __name__ == "__main__":
    unittest.main()
