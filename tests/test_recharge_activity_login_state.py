"""TASK-0094 - the recharge-activity red point, judged by its own consumers.

RUN-0027's first Lua error is raised from inside the main-city UI init, one
frame after the function buttons were bound:

    pkgUIRechargeACTaskView.lua:70  in function 'getTask2HadGet'
    pkgKoiDataMgr.lua:88            in function 'getGiftState'
    pkgUIMainUIControl.lua:502      in function 'checkRedPointRechargeActivity'
    pkgUIMainUIControl.lua:204      in function 'checkRedPointOnMainUI'
    pkgMainUIMgr.lua:962            in function 'checkRedPoint'
    pkgMainUIMgr.lua:870            in function 'initBtnReplace'
    pkgMainUIMgr.lua:916            in function 'initBtn'
    pkgMainUIMgr.lua:1133           in function 'init'

This module contains a *faithful model of that recovered chain*, built from the
decompiled sources and pinned to them by
``max_decoder/analyze_recharge_activity_red_point.py`` (which the tests import
and run, so the model cannot silently drift).  Every failure it reports carries
the recovered ``module:line`` and function name, so a model result can be
compared with the RUN-0027 logcat stack frame for frame.

The tests then drive the model with the *real decoded wire* of a real
``RevivalTCPServer`` over a real socket session -- a v5 server (no policy) for
the falsification and the active v6 server for the repair.
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
    PROFILE_ID_V5,
    PROFILE_ID_V6,
    RECHARGE_ACTIVITY_RECHARGE_START,
    get_profile,
    get_profile_v5,
    get_profile_v6,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import S2C_REGISTRY
from revival_stub.tcp_stub import RevivalTCPServer
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.registry import C2S_REGISTRY


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_DECODER = PROJECT_ROOT / "max_decoder"
if str(MAX_DECODER) not in sys.path:
    sys.path.insert(0, str(MAX_DECODER))

import analyze_recharge_activity_red_point as contract  # noqa: E402

RECHARGE_START_ID = 1014
UNREGISTERED_SIBLINGS = (1013, 1015, 1016, 1017, 1024)

BASE_LOGIN_PACKETS = 5
#: 13 role-sync packets + the v5 team block + the v6 recharge packet.
V6_SEGMENT_PACKETS = 18
#: The same segment on a v5 server: no recharge packet.
V5_SEGMENT_PACKETS = 17


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


def lua_truthy(value) -> bool:
    """Lua truthiness: only ``nil`` and ``false`` are false -- ``0`` is true."""

    return value is not None and value is not False


class RecoveredClientError(RuntimeError):
    """A failure at a named line of the recovered client source.

    The frames are kept from the raising statement outward, so the rendered
    message can be compared with the RUN-0027 logcat stack frame for frame.
    """

    def __init__(
        self,
        module: str,
        line: int,
        function: str,
        detail: str,
        frames: tuple[tuple[str, int, str], ...] = (),
    ) -> None:
        self.module = module
        self.line = line
        self.function = function
        self.detail = detail
        self.frames = ((module, line, function),) + frames
        rendered = "\n".join(
            f"\t{frame_module}:{frame_line}: in function '{frame_function}'"
            for frame_module, frame_line, frame_function in self.frames
        )
        super().__init__(f"{module}:{line}: {detail}\nstack traceback:\n{rendered}")

    @property
    def frame(self) -> str:
        """The statement that actually raised."""

        return f"{self.module}:{self.line}"

    @property
    def outermost_frame(self) -> str:
        """The last recovered frame the failure propagated out of."""

        module, line, _ = self.frames[-1]
        return f"{module}:{line}"

    def propagated(self, module: str, line: int, function: str) -> "RecoveredClientError":
        """Re-raise this failure one recovered frame further out."""

        return RecoveredClientError(
            self.module,
            self.line,
            self.function,
            self.detail,
            self.frames[1:] + ((module, line, function),),
        )


class RecoveredRechargeModel:
    """``rechargeActivity/pkgRechargeActivityModel.lua`` (source 120-132)."""

    MODULE = "rechargeActivity/pkgRechargeActivityModel.lua"
    #: ``TaskConfig_2 = { 1003 }`` in pkgUIRechargeACTaskView (source line 8).
    TASK2_ID = contract.TASK2_ID

    def __init__(self) -> None:
        #: ``_createRechargeActivity``: `tbGetRecharge = {}`, and `_init()` runs
        #: at chunk load.
        self.e_tb_recharge_activity = {
            "dRechargeStart": -1,
            "tbGetRecharge": {},
            "tbOneRecharge": {},
            "tbDiamondPackage": {},
            "tbLevelPack": {},
            "tbRechargeOnce": {},
            "tbRechargeACInfo": {},
        }

    def set_recharge_start(self, value) -> None:
        """``setRechargeStart`` (27-29)."""

        self.e_tb_recharge_activity["dRechargeStart"] = value

    def init_recharge_start(self, config_ids) -> None:
        """``initRechargeStart`` (43-49): one row per CLIENT CONFIG id."""

        self.e_tb_recharge_activity["tbGetRecharge"] = {
            cid: {"bEnough": False, "bHadGet": False} for cid in config_ids
        }

    def set_ac_recharge_data(self, d_id, b_enough, b_had_get) -> None:
        """``setACRechargeData`` (51-57): only mutates an existing row."""

        row = self.e_tb_recharge_activity["tbGetRecharge"].get(d_id)
        if row is not None:
            row["bEnough"] = b_enough
            row["bHadGet"] = b_had_get

    def get_recharge_show_value(self) -> int:
        """``getRechargeShowValue`` (31-41): every value <= 0 clamps to 0."""

        value = int(self.e_tb_recharge_activity["dRechargeStart"] or 0)
        return value if value > 0 else 0


class RecoveredRechargeCfgMgr:
    """``zys_scAll/cfg/rechargeActivity/pkgRechargeActivityCfgMgr.lua``."""

    MODULE = "zys_scAll/cfg/rechargeActivity/pkgRechargeActivityCfgMgr.lua"

    def __init__(self, ids) -> None:
        #: ``e_rechargeActivityTask`` -- the client's own static config table.
        self.e_recharge_activity_task = {
            cid: {"id": cid, "value": 100 * cid} for cid in ids
        }

    def get_recharge_task_by_id(self, d_id):
        """``getRechargeTaskById`` (29-31) -- unguarded table read."""

        return self.e_recharge_activity_task.get(d_id)


class RecoveredRechargeACTaskView:
    """``rechargeActivity/pkgUIRechargeACTaskView.lua`` (source 67-71)."""

    MODULE = "rechargeActivity/pkgUIRechargeACTaskView.lua"

    def __init__(self, cfg, model) -> None:
        self.cfg = cfg
        self.model = model

    def get_task2_had_get(self) -> bool:
        """``getTask2HadGet`` -- the UNGUARDED ``tbInfo.bHadGet`` at line 70."""

        tb_cfg_data = self.cfg.get_recharge_task_by_id(
            RecoveredRechargeModel.TASK2_ID
        )
        if tb_cfg_data is None:  # pragma: no cover - proven present by RUN-0027
            raise RecoveredClientError(
                self.MODULE, 68, "getTask2HadGet",
                "pkgRechargeActivityCfgMgr.getRechargeTaskById returned nil",
            )
        tb_info = self.model.e_tb_recharge_activity["tbGetRecharge"].get(
            tb_cfg_data["id"]
        )
        if tb_info is None:
            raise RecoveredClientError(
                self.MODULE,
                contract.TASK2_UNGUARDED_LINE,
                "getTask2HadGet",
                "attempt to index local 'tbInfo' (a nil value)",
            )
        return bool(tb_info["bHadGet"])


class RecoveredKoiDataMgr:
    """``koi/pkgKoiDataMgr.lua`` (source 83-89)."""

    MODULE = "koi/pkgKoiDataMgr.lua"

    def __init__(self, view) -> None:
        self.view = view
        #: pkgKoiDataMgr source line 34 -- `le_tbSelf.dChargeTime = ... or 0`.
        self.le_tb_self = {
            "dLv": 0,
            "dExp": 0,
            "dChargeTime": 0,
            "bUnlockState": False,
        }

    def get_gift_state(self) -> bool:
        """``getGiftState`` -- the guard cannot fire, 0 is truthy in Lua."""

        if lua_truthy(self.le_tb_self) and lua_truthy(self.le_tb_self["dChargeTime"]):
            try:
                had_get = self.view.get_task2_had_get()
            except RecoveredClientError as exc:
                raise exc.propagated(
                    self.MODULE, contract.GIFT_STATE_BODY_LINE, "getGiftState"
                ) from exc
            return not (had_get or not (self.le_tb_self["dChargeTime"] >= 1))
        return False


class RecoveredMainUiControl:
    """``mainUI/pkgUIMainUIControl.lua`` (source 195-231, 419-615)."""

    MODULE = "mainUI/pkgUIMainUIControl.lua"

    def __init__(self, koi, view) -> None:
        self.koi = koi
        self.view = view
        self.red_points: dict[str, bool] = {}

    def check_red_point_recharge_activity(self) -> None:
        """``checkRedPointRechargeActivity`` -- line 502 is the koi read."""

        try:
            show_red_point = self.koi.get_gift_state()
        except RecoveredClientError as exc:
            # The recovered statement that raised is the line-502 read.
            raise exc.propagated(
                self.MODULE,
                contract.GET_GIFT_STATE_LINE,
                "checkRedPointRechargeActivity",
            ) from exc
        had_get = self.view.get_task2_had_get()
        self.red_points["red_point_recharge_task2"] = bool(
            show_red_point and not had_get
        )

    def check_red_point_on_main_ui(self) -> None:
        """``checkRedPointOnMainUI`` -- 37 checks, this one is call 9."""

        try:
            self.check_red_point_recharge_activity()
        except RecoveredClientError as exc:
            raise exc.propagated(
                self.MODULE, 204, "checkRedPointOnMainUI"
            ) from exc


class RecoveredMainUiMgr:
    """``mainUI/pkgMainUIMgr.lua`` (source 646-664, 855-872, 898-924, 1125-1179)."""

    MODULE = "mainUI/pkgMainUIMgr.lua"

    #: ``pkgCfgFunctionOpenMgr.getAllCfgData()`` -- the client's own table.  The
    #: adventure entry is the row TASK-0091/TASK-0087 recovered
    #: (mainUIPath `btnRisk`, macro `chapter`).
    FUNCTION_ROW = {"mainUIPath": "btnRisk", "macro": "chapter", "audioName": ""}

    def __init__(self, control, view) -> None:
        self.control = control
        self.view = view
        self.bound_buttons: list[tuple[str, str]] = []
        self.le_game_object = {"transform": {"btnRisk": {"activeSelf": True}}}
        self.stages: list[str] = []
        self.update_btn_calls = 0
        self.init_btn_test_calls = 0

    # -------------------------------------------------------------- initBtn
    def init_btn(self) -> None:
        """``initBtn`` (898-924): bind first (loop ends 912), replace at 916."""

        self.stages.append("initBtn:enter")
        for row in (self.FUNCTION_ROW,):
            if row["mainUIPath"] == "":
                continue
            button = self.le_game_object["transform"].get(row["mainUIPath"])
            if button is None:
                continue
            callback = f"pkgFunctionMgr.getFunctionShow({row['macro']})"
            self.bound_buttons.append((row["mainUIPath"], callback))
        self.stages.append(f"initBtn:bound={len(self.bound_buttons)}")
        self.check_extra_download_btn()
        # Source line 916 -- RUN-0027's frame.
        try:
            self.init_btn_replace()
        except RecoveredClientError as exc:
            raise exc.propagated(
                self.MODULE, contract.INIT_BTN_REPLACE_LINE, "initBtn"
            ) from exc
        self.init_btn_test()

    def check_extra_download_btn(self) -> None:
        """``checkExtraDownloadBtn`` (874-896) -- inert for this archive."""

        self.stages.append("checkExtraDownloadBtn")

    def init_btn_replace(self) -> None:
        """``initBtnReplace`` (855-872): checkRedPoint at 870, updateBtn at 871."""

        self.stages.append("initBtnReplace:enter")
        try:
            self.check_red_point()
        except RecoveredClientError as exc:
            raise exc.propagated(
                self.MODULE,
                contract.INIT_BTN_REPLACE_CHECK_RED_POINT_LINE,
                "initBtnReplace",
            ) from exc
        self.update_btn()

    def init_btn_test(self) -> None:
        """``initBtnTest`` (822-844): reached only when 916 did not raise."""

        self.init_btn_test_calls += 1
        self.stages.append("initBtnTest")

    def check_red_point(self) -> None:
        """``checkRedPoint`` (953-969)."""

        try:
            self.control.check_red_point_on_main_ui()
        except RecoveredClientError as exc:
            raise exc.propagated(
                self.MODULE, contract.CHECK_RED_POINT_ON_MAIN_UI_LINE, "checkRedPoint"
            ) from exc

    def update_btn(self) -> None:
        """``updateBtn``: called from updateInfo AND from initBtnReplace:871."""

        self.update_btn_calls += 1
        self.stages.append("updateBtn")

    # ------------------------------------------------------------- the wire
    def apply(self, protocol_id: int, fields: tuple) -> bool:
        """Dispatch one decoded S2C the way the recovered handler would."""

        if protocol_id != RECHARGE_START_ID:
            return False
        self.control.koi.view.model.set_recharge_start(int(fields[0]))
        if fields[1]:
            self.control.koi.view.model.init_recharge_start(
                self.control.koi.view.cfg.e_recharge_activity_task
            )
        self.stages.append("returnACRechargeDataStart")
        return True


class RecoveredClient:
    """The whole recovered chain, in one object."""

    #: The client's own config ids.  `1003` is the one the unguarded read uses;
    #: that it exists is proven by RUN-0027 reaching line 70 rather than line 68.
    CONFIG_IDS = (1001, 1002, 1003, 2001, 2002)

    def __init__(self) -> None:
        self.cfg = RecoveredRechargeCfgMgr(self.CONFIG_IDS)
        self.model = RecoveredRechargeModel()
        self.view = RecoveredRechargeACTaskView(self.cfg, self.model)
        self.koi = RecoveredKoiDataMgr(self.view)
        self.control = RecoveredMainUiControl(self.koi, self.view)
        self.main_ui = RecoveredMainUiMgr(self.control, self.view)

    def main_city_init(self) -> None:
        """``pkgMainUIMgr.init`` line 1132 ``updateInfo()`` -> ... -> ``initBtn``.

        ``updateInfo`` itself reaches ``updateBtn()`` (statement 8) before
        ``initBtn`` runs, so the button state is set either way; what the
        recharge failure costs is the SECOND ``updateBtn()``, ``initBtnTest()``
        and the whole tail of ``init``.
        """

        self.main_ui.update_btn()          # updateInfo's own updateBtn()
        try:
            self.main_ui.init_btn()        # source line 1133
        except RecoveredClientError as exc:
            raise exc.propagated(
                RecoveredMainUiMgr.MODULE, contract.INIT_INIT_BTN_LINE, "init"
            ) from exc


class SourcePinTests(unittest.TestCase):
    """The model above is only evidence while it still matches the sources."""

    def test_recovered_contract_still_pins(self) -> None:
        contract.assert_protocol_table()
        contract.assert_registration_source(contract._read(contract.SC_PROTOCOL))
        contract.assert_stack_lines()
        contract.assert_init_btn_binds_before_the_error()
        contract.assert_chain_statements()
        contract.assert_koi_guard_is_not_a_guard()
        contract.assert_unguarded_read()
        contract.assert_container_producers()
        contract.assert_bytecode_lines()

    def test_registry_agrees_with_the_recovered_contract(self) -> None:
        spec = S2C_REGISTRY.by_id(RECHARGE_START_ID)
        self.assertEqual(spec.name, "returnACRechargeDataStart")
        self.assertEqual(spec.fmt, "py")
        for wire_id in UNREGISTERED_SIBLINGS:
            with self.subTest(protocol=wire_id):
                with self.assertRaises(KeyError):
                    S2C_REGISTRY.by_id(wire_id)

    def test_the_policy_supplies_exactly_the_recovered_payload(self) -> None:
        policy = get_profile().recharge_activity
        assert policy is not None
        self.assertEqual(policy.start_fields(), (RECHARGE_ACTIVITY_RECHARGE_START, True))
        # bReset must be the TRUE literal: the client's own branch is
        # `if bReset then initRechargeStart() else dispatch end`.
        self.assertIs(policy.start_fields()[1], True)


class BaseFalsificationTests(unittest.TestCase):
    """Without the policy, the RUN-0027 frame is reproduced exactly."""

    def test_the_untouched_container_raises_at_the_run_0027_line(self) -> None:
        client = RecoveredClient()
        with self.assertRaises(RecoveredClientError) as caught:
            client.main_city_init()
        # Frame for frame, the RUN-0027 logcat stack is this model's stack.
        self.assertEqual(
            caught.exception.frame, "rechargeActivity/pkgUIRechargeACTaskView.lua:70"
        )
        self.assertEqual(
            caught.exception.outermost_frame, "mainUI/pkgMainUIMgr.lua:1133"
        )
        self.assertIn("rechargeActivity/pkgUIRechargeACTaskView.lua:70", str(caught.exception))
        self.assertIn("koi/pkgKoiDataMgr.lua:88", str(caught.exception))
        self.assertIn("mainUI/pkgUIMainUIControl.lua:502", str(caught.exception))
        self.assertIn("mainUI/pkgUIMainUIControl.lua:204", str(caught.exception))
        self.assertIn("mainUI/pkgMainUIMgr.lua:962", str(caught.exception))
        self.assertIn("mainUI/pkgMainUIMgr.lua:870", str(caught.exception))
        self.assertIn("mainUI/pkgMainUIMgr.lua:916", str(caught.exception))

    def test_the_adventure_button_is_bound_before_the_raise(self) -> None:
        """The error cannot block the function-button path: bindings come first."""

        client = RecoveredClient()
        with self.assertRaises(RecoveredClientError):
            client.main_city_init()
        self.assertEqual(
            client.main_ui.bound_buttons,
            [("btnRisk", "pkgFunctionMgr.getFunctionShow(chapter)")],
        )
        self.assertIn("initBtn:bound=1", client.main_ui.stages)
        # What the failure DOES cost: the second updateBtn(), initBtnTest() and
        # everything after them in init().
        self.assertEqual(client.main_ui.update_btn_calls, 1)
        self.assertEqual(client.main_ui.init_btn_test_calls, 0)

    def test_a_lone_recharge_state_read_does_not_fix_anything(self) -> None:
        """`setACRechargeData` alone cannot create the row it wants."""

        client = RecoveredClient()
        client.model.set_ac_recharge_data(1003, True, False)
        self.assertEqual(client.model.e_tb_recharge_activity["tbGetRecharge"], {})
        with self.assertRaises(RecoveredClientError) as caught:
            client.view.get_task2_had_get()
        self.assertEqual(caught.exception.frame, "rechargeActivity/pkgUIRechargeACTaskView.lua:70")

    def test_the_guard_that_was_meant_to_stop_it_is_defeated_by_zero(self) -> None:
        client = RecoveredClient()
        self.assertEqual(client.koi.le_tb_self["dChargeTime"], 0)
        self.assertTrue(lua_truthy(client.koi.le_tb_self["dChargeTime"]))  # 0 is truthy
        with self.assertRaises(RecoveredClientError):
            client.koi.get_gift_state()


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

    def _login(self, role_name: str) -> list:
        writer = WriterState()
        state = ReaderState()
        buffer = bytearray()
        packets: list = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
                + encode_packet(
                    C2S_REGISTRY.by_id(2), (role_name.encode("utf-8"), 1), writer
                ).wire
            )
            _read(stream, self.server, buffer, state, packets, BASE_LOGIN_PACKETS)
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
            )
            _read(stream, self.server, buffer, state, packets, self.SEGMENT_PACKETS)
        return packets[BASE_LOGIN_PACKETS:]


class V6RepairTests(_ServerMixin, unittest.TestCase):
    """The v6 server: the real wire runs the client's own initializer.

    Pinned to the v6 profile itself (TASK-0098), not to whatever the active
    profile is: every expectation below is a v6-stream fact (the segment's own
    packet order, the event's v6 ancestry).  Later profiles inherit this policy's
    object unchanged, and their own streams are asserted by their own modules.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.PROFILE = get_profile_v6()

    def test_real_wire_completes_the_whole_red_point_pass(self) -> None:
        segment = self._login("充值道友")
        ids = [packet.spec.protocol_id for packet in segment]
        # The v5 team block, then TASK-0094's single packet, then the story
        # snapshot and the S2C283 whose dispatch starts the main-city switch.
        self.assertEqual(ids[-4:], [35, RECHARGE_START_ID, 411, 283])
        self.assertEqual(ids[-1], 283)

        client = RecoveredClient()
        for packet in segment:
            client.main_ui.apply(packet.spec.protocol_id, packet.fields)
        # The container is now the client's own config-driven row set.
        self.assertEqual(
            sorted(client.model.e_tb_recharge_activity["tbGetRecharge"]),
            sorted(RecoveredClient.CONFIG_IDS),
        )
        for cid, row in client.model.e_tb_recharge_activity["tbGetRecharge"].items():
            with self.subTest(task=cid):
                self.assertEqual(row, {"bEnough": False, "bHadGet": False})
        self.assertEqual(client.model.e_tb_recharge_activity["dRechargeStart"], 0)
        # And the chain completes where it used to raise.
        client.main_city_init()
        self.assertEqual(client.main_ui.init_btn_test_calls, 1)
        self.assertEqual(client.main_ui.update_btn_calls, 2)
        self.assertEqual(
            client.control.red_points["red_point_recharge_task2"], False
        )

    def test_the_raise_is_gone_on_the_repaired_session(self) -> None:
        segment = self._login("无报错道友")
        client = RecoveredClient()
        # Pre-load every packet that is NOT the recharge state: the chain still
        # raises, which is what makes the next assertion meaningful.
        for packet in segment:
            if packet.spec.protocol_id != RECHARGE_START_ID:
                client.main_ui.apply(packet.spec.protocol_id, packet.fields)
        with self.assertRaises(RecoveredClientError):
            client.main_city_init()

        client = RecoveredClient()
        for packet in segment:
            client.main_ui.apply(packet.spec.protocol_id, packet.fields)
        client.main_city_init()  # no raise

    def test_the_packet_is_the_clients_own_reset_not_a_reward_row(self) -> None:
        self._login("空活动道友")
        event = next(
            e
            for e in self.events
            if e.get("event") == "tcp_recharge_activity_state_sent"
        )
        self.assertEqual(event["protocol_id"], RECHARGE_START_ID)
        self.assertEqual(event["recharge_start"], 0)
        self.assertIs(event["reset_rows"], True)
        self.assertIs(event["rows_created_by_client_config"], True)
        self.assertEqual(event["snapshot_packet_count"], 1)
        self.assertEqual(event["policy_compatibility_version"], PROFILE_ID_V6)
        self.assertEqual(event["profile_id"], PROFILE_ID_V6)
        self.assertEqual(event["parent_profile_id"], PROFILE_ID_V5)
        self.assertEqual(event["container_policy_origin"], "synthetic_compatibility")
        self.assertEqual(event["recharge_start_origin"], "synthetic_compatibility")
        self.assertEqual(event["historical_ordering"], "unknown")
        self.assertEqual(event["ordering_origin"], "synthetic_compatibility")
        self.assertIs(event["historical_values_recovered"], False)
        self.assertIs(event["precedes_scene_switch_packet"], True)
        self.assertEqual(event["scene_switch_boundary_protocol_id"], 283)
        # No reward, claim or per-task flag packet leaves the server.
        sent = {
            e.get("protocol_id")
            for e in self.events
            if e.get("event") == "tcp_packet_sent"
        }
        self.assertFalse(set(UNREGISTERED_SIBLINGS) & sent)
        self.assertFalse(
            any(e.get("event") == "tcp_error" for e in self.events)
        )

    def test_the_v5_team_block_is_untouched(self) -> None:
        segment = self._login("队伍不变道友")
        ids = [packet.spec.protocol_id for packet in segment]
        first = ids.index(36)
        self.assertEqual(ids[first:first + 4], [36, 37, 48, 35])
        self.assertEqual(ids[first + 4], RECHARGE_START_ID)
        team_events = [
            e for e in self.events if e.get("event") == "tcp_team_snapshot_sent"
        ]
        self.assertEqual([e["protocol_id"] for e in team_events], [36, 37, 48, 35])
        for event in team_events:
            self.assertEqual(event["policy_compatibility_version"], PROFILE_ID_V5)
            self.assertEqual(event["parent_profile_id"], PROFILE_ID_V5)
            self.assertEqual(event["current_group"], 1)


class V5FalsificationTests(_ServerMixin, unittest.TestCase):
    """The same session on the previous profile inserts nothing."""

    PROFILE = None
    SEGMENT_PACKETS = V5_SEGMENT_PACKETS

    @classmethod
    def setUpClass(cls) -> None:
        cls.PROFILE = get_profile_v5()

    def test_a_v5_server_sends_no_recharge_packet_and_no_event(self) -> None:
        segment = self._login("回退充值道友")
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertNotIn(RECHARGE_START_ID, ids)
        self.assertEqual(ids[-3:], [35, 411, 283])
        self.assertFalse(
            any(
                e.get("event") == "tcp_recharge_activity_state_sent"
                for e in self.events
            )
        )
        # ... and the client model still reproduces the RUN-0027 abort.
        client = RecoveredClient()
        with self.assertRaises(RecoveredClientError) as caught:
            client.main_city_init()
        self.assertEqual(
            caught.exception.frame, "rechargeActivity/pkgUIRechargeACTaskView.lua:70"
        )
        self.assertEqual(
            caught.exception.outermost_frame, "mainUI/pkgMainUIMgr.lua:1133"
        )

    def test_every_earlier_profile_has_no_recharge_state(self) -> None:
        from revival_stub.compatibility_profile import (
            get_profile_v1,
            get_profile_v2,
            get_profile_v3,
            get_profile_v4,
        )

        for profile in (
            get_profile_v1(),
            get_profile_v2(),
            get_profile_v3(),
            get_profile_v4(),
            get_profile_v5(),
        ):
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.recharge_activity)
                self.assertNotEqual(profile.profile_id, PROFILE_ID_V6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
