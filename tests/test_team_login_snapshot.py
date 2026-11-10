"""TASK-0092 — the Team login snapshot, judged by the client's own consumers.

The client's main-city UI init aborts before it binds a single function button:
``pkgMainUIMgr.lua:1130`` calls ``updateInfo()``, whose sixth statement
(``:652``) is ``updateFight()`` (``:300``) -> ``pkgTeamDataMgr.getCurFight()``
-> ``caclGroupFight`` -> ``pkgPlayerInfoMgr.caclRoleFight()`` ->
``pkgEquipModelMgr.getTeamEquipFight(getCurGroupID())`` ->
``getRoleEquipInfoList`` -> ``getGroupEquips``, whose whole body is the
unguarded ``tbTeamCard[dGroupId].tbEquips``.  ``initBtn()`` -- the only binder
of the main-city function buttons -- is at ``:1133``.

This module contains a *faithful model of that recovered chain*, built from the
decompiled sources and pinned to them by
``max_decoder/analyze_team_login_snapshot.py`` (which the tests import and run,
so the model cannot silently drift from the sources it claims to model).  Every
failure it reports carries the recovered ``module:line`` and function name, so a
model result can be compared with the RUN-0026 logcat stack frame for frame.

The tests then drive the model with the *real decoded wire* of a real
``RevivalTCPServer`` over a real socket session -- v4 (no snapshot) for the
falsification and v5 (the shipped snapshot) for the repair.
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
    PROFILE_ID_V4,
    PROFILE_ID_V5,
    get_profile,
    get_profile_v1,
    get_profile_v2,
    get_profile_v3,
    get_profile_v4,
    get_profile_v5,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_DECODER = PROJECT_ROOT / "max_decoder"
if str(MAX_DECODER) not in sys.path:
    sys.path.insert(0, str(MAX_DECODER))

import analyze_team_login_snapshot as contract  # noqa: E402  (source pin, run below)

SERVER_STARTED_UNIX = 1_700_000_000
SEND_NOW_UNIX = 1_700_000_123

TEAM_START_ID = 36
TEAM_INFO_ID = 37
SELECT_MAGIC_START_ID = 48
GROUP_ID_ID = 35
SNAPSHOT_IDS = [TEAM_START_ID, TEAM_INFO_ID, SELECT_MAGIC_START_ID, GROUP_ID_ID]
#: TASK-0094's single recharge-activity packet, which follows the v5 block.
RECHARGE_START_ID = 1014

D_MAX_TEAM = 5
EMPTY = ""
#: The recovered terminal segment before the snapshot: 5 sync packets for
#: login+create, then 8 for the receivePackage sync that carries S2C283.
BASE_LOGIN_PACKETS = 5
BASE_SEGMENT_PACKETS = 13


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


class RecoveredTeamClient:
    """The recovered team/fabao containers and the `getCurFight()` chain.

    Only the modules the recovered chain actually touches are modelled, and
    every modelled statement is asserted against its decompiled source by
    ``analyze_team_login_snapshot.py``.
    """

    #: ``pkgTeamDataMgr.lua:13`` -- `dMaxTeam = 5`.
    D_MAX_TEAM = D_MAX_TEAM

    TEAM_MODULE = "team/pkgTeamDataMgr.lua"
    FABAO_MODULE = "fabao/pkgFabaoDataMgr.lua"
    EQUIP_MODULE = "equip/pkgEquipModelMgr.lua"
    CARD_MODULE = "card/pkgCardDataMgr.lua"
    ROLE_MODULE = "role/pkgPlayerInfoMgr.lua"
    GOD_MODULE = "godWeapon/pkgGodWeaponMgr.lua"

    def __init__(self) -> None:
        # pkgTeamDataMgr.lua:8-10
        self.tb_team_card: dict[int, dict[str, object]] = {}
        self.tb_team_fabao: dict[int, list[str]] = {}
        self.d_team_group_id: int | None = None
        # pkgEquipModelMgr / pkgGodWeaponMgr / pkgRuneMgr module-level tables:
        # this archive holds no equipment, god-weapon or rune rows at all.
        self.le_equip_dict: dict[str, object] = {}
        self.le_tb_god_weapons: dict[str, object] = {}
        self.le_tb_rune_select: dict[int, dict[int, int]] = {}
        # pkgEventMgr.dispatch is recorded, never fanned out (see below).
        self.dispatched: list[tuple[str, tuple]] = []

    # ------------------------------------------------------- pkgEventMgr
    def dispatch(self, event: str, *args) -> None:
        """``pkgEventMgr.dispatch`` -- recorded, deliberately not fanned out.

        During the terminal login segment the main-city UI does not exist yet:
        its listener table is filled by ``pkgMainUIMgr.init``'s own
        ``addListener()`` (source line 1129), which runs from the scene-switch
        completion callback *after* S2C283 has been dispatched.  So no listener
        runs while the snapshot arrives.  ``change_team_listener`` below
        reproduces what the recovered ``CHANGE_TEAM -> updateFight`` row would
        do if it were alive -- which is exactly why the group id is sent last.
        """

        self.dispatched.append((event, args))

    def change_team_listener(self) -> int:
        """``pkgMainUIMgr``'s recovered ``CHANGE_TEAM -> updateFight`` row."""

        return self.get_cur_fight()

    # ------------------------------------------------- writers (S2C handlers)
    def init_team_card_data(self) -> None:
        """``pkgTeamDataMgr.lua:33-45`` -- `for i = 1, dMaxTeam`."""

        for i in range(1, self.D_MAX_TEAM + 1):
            self.tb_team_card[i] = {"tbCards": [], "tbEquips": []}

    def init_team_fabao_data(self) -> None:
        """``pkgTeamDataMgr.lua:211-215`` -- three slots per preset."""

        for i in range(1, self.D_MAX_TEAM + 1):
            self.tb_team_fabao[i] = [EMPTY, EMPTY, EMPTY]

    def set_team_card_data(
        self, gid: int, equips: list[str], cards: list[str], god: str, reconnect: bool
    ) -> None:
        """``pkgTeamDataMgr.lua:104-118`` -- the record is REPLACED first."""

        self.tb_team_card[gid] = {
            "tbEquips": list(equips),
            "tbCards": list(cards),
            "strGodWeaponId": god,
        }
        # pkgEventType.TEAM_UPDATE -> pkgMainUIMgr.updateTeam -> updateFight.
        self.dispatch("TEAM_UPDATE", gid, reconnect)

    def set_cur_group_id(self, gid: int) -> None:
        """``pkgTeamDataMgr.lua:74-79`` -- dispatches CHANGE_TEAM on a change."""

        if gid != self.d_team_group_id:
            self.d_team_group_id = gid
            self.dispatch("CHANGE_TEAM", gid)

    # -------------------------------------------------- s2c handlers (wire)
    def s2c(self, protocol_id: int, fields: tuple) -> None:
        if protocol_id == TEAM_START_ID:  # s2c.returnTeamStart
            self.init_team_card_data()
        elif protocol_id == TEAM_INFO_ID:  # s2c.returnTeamInfo
            gid, *slots = fields
            equips, god, cards = slots[:4], slots[4], slots[5:]
            self.set_team_card_data(gid, equips, cards, god, True)
        elif protocol_id == SELECT_MAGIC_START_ID:  # s2c.returnSelectMagicStart
            self.init_team_fabao_data()
        elif protocol_id == GROUP_ID_ID:  # s2c.returnGroupId
            self.set_cur_group_id(fields[0])
        else:  # pragma: no cover - the snapshot sends exactly these four
            raise AssertionError(f"not a team-snapshot packet: {protocol_id}")

    def apply(self, packet) -> None:
        fields = tuple(
            value.decode("utf-8") if isinstance(value, bytes) else value
            for value in packet.fields
        )
        self.s2c(packet.spec.protocol_id, fields)

    # ------------------------------------------------------------ accessors
    def get_cur_group_id(self) -> int:
        """``pkgTeamDataMgr.lua:81-86`` -- the -1 unset sentinel."""

        if self.d_team_group_id is None:
            return -1
        return self.d_team_group_id

    def get_group_equips(self, gid: int) -> list[str]:
        """``pkgTeamDataMgr.lua:88-90`` -- UNGUARDED."""

        container = self.tb_team_card.get(gid)
        if container is None:
            raise RecoveredClientError(
                self.TEAM_MODULE,
                89,
                "getGroupEquips",
                f"attempt to index field '?' (a nil value) [tbTeamCard[{gid}]]",
            )
        return container["tbEquips"]  # type: ignore[return-value]

    def get_team_god_weapon_id(self, gid: int) -> str | None:
        """``pkgTeamDataMgr.lua:92-94`` -- UNGUARDED (nil when the field is
        absent, which `initTeamCardData`'s record shape leaves it)."""

        container = self.tb_team_card.get(gid)
        if container is None:
            raise RecoveredClientError(
                self.TEAM_MODULE,
                93,
                "getTeamGodWeaponId",
                f"attempt to index field '?' (a nil value) [tbTeamCard[{gid}]]",
            )
        return container.get("strGodWeaponId")  # type: ignore[return-value]

    def get_team_card_data(self, gid: int | None) -> list[str]:
        """``pkgTeamDataMgr.lua:112-118`` -- RANGE-guarded, else 11 x ''.

        The guard is on the RANGE only: for a legal group whose container is
        missing, the very next statement (source line 114) indexes
        ``tbTeamCard[gid]`` and raises -- one frame EARLIER than the RUN-0026
        abort.  Naming a preset before its record exists is therefore worse
        than naming none.
        """

        if gid is not None and 0 < gid <= self.D_MAX_TEAM:
            container = self.tb_team_card.get(gid)
            if container is None:
                raise RecoveredClientError(
                    self.TEAM_MODULE,
                    114,
                    "getTeamCardData",
                    f"attempt to index field '?' (a nil value) [tbTeamCard[{gid}]]",
                )
            return container["tbCards"]  # type: ignore[return-value]
        return [EMPTY] * 11

    def get_team_fabao_data(self, gid: int | None) -> list[str] | None:
        """``pkgTeamDataMgr.lua:221-226`` -- RANGE-guarded, but it returns the
        raw container: a *legal* group whose container is missing yields nil."""

        if gid is not None and 0 < gid <= self.D_MAX_TEAM:
            return self.tb_team_fabao.get(gid)  # type: ignore[return-value]
        return [EMPTY, EMPTY, EMPTY]

    def get_all_cards(self) -> list[str]:
        """``pkgTeamDataMgr.lua:293-302`` -- `getGroupCards(i)` for
        `1..dMaxTeam`, and `getGroupCards` is itself unguarded."""

        cards: list[str] = []
        for i in range(1, self.D_MAX_TEAM + 1):
            container = self.tb_team_card.get(i)
            if container is None:
                raise RecoveredClientError(
                    self.TEAM_MODULE,
                    292,
                    "getGroupCards",
                    f"attempt to index field '?' (a nil value) [tbTeamCard[{i}]]",
                )
            cards.extend(list(container["tbCards"]))  # type: ignore[arg-type]
        return cards

    def get_rune_select_by_group_id(self, gid: int | None):
        """``pkgRuneMgr.lua:25-27`` -- read of a module-level table."""

        return self.le_tb_rune_select.get(gid)  # type: ignore[arg-type]

    def get_god_weapon(self, instance_id: str | None):
        """``pkgGodWeaponMgr.lua:195-200`` -- nil-guarded."""

        if instance_id is None:
            return None
        return self.le_tb_god_weapons.get(instance_id)

    # ------------------------------------------------- the recovered chain
    def get_role_equip_info_list(self, gid: int) -> list[dict]:
        """``pkgEquipModelMgr.lua:286-303``."""

        equip_ids = self.get_group_equips(gid)
        infos: list[dict] = []
        for str_id in equip_ids:
            info = self.le_equip_dict.get(str_id)  # pkgEquipModelMgr:301-303
            if info:
                infos.append(info)  # type: ignore[arg-type]
        return infos

    def get_team_equip_fight(self, gid: int) -> int:
        """``pkgEquipModelMgr.lua:305-309``."""

        return len(self.get_role_equip_info_list(gid))

    def get_team_god_weapon_fight(self, gid: int) -> int:
        """``pkgGodWeaponMgr.lua:719-730``."""

        god = self.get_god_weapon(self.get_team_god_weapon_id(gid))
        return 0 if god is None else 1

    def cacl_role_fight(self) -> int:
        """``pkgPlayerInfoMgr.lua:442-451``."""

        gid = self.get_cur_group_id()
        return self.get_team_equip_fight(gid) + self.get_team_god_weapon_fight(gid)

    def get_card_fight(self, card_id: str) -> int:
        """``pkgCardDataMgr.lua:796`` -- reads the role's OWN card row."""

        raise RecoveredClientError(
            self.CARD_MODULE,
            796,
            "getCardFight",
            f"no card record for instance id {card_id!r}: this archive holds no "
            "card rows, so a non-empty card slot is not computable",
        )

    def cacl_select_fight(self) -> int:
        """``pkgFabaoDataMgr.lua:688-702`` -- `pairs(getTeamFabaoData(gid))`."""

        gid = self.get_cur_group_id()
        selected = self.get_team_fabao_data(gid)
        if selected is None:
            raise RecoveredClientError(
                self.FABAO_MODULE,
                690,
                "caclSelectFight",
                "bad argument #1 to 'pairs' (table expected, got nil) "
                f"[tbTeamFabao[{gid}] was never created]",
            )
        total = 0
        for instance_id in selected:
            if instance_id != EMPTY:
                total += 1
        return total

    def cacl_group_fight(self, group: list[str]) -> int:
        """``pkgTeamDataMgr.lua:226-248``, statement order preserved."""

        fight = self.cacl_role_fight()
        for instance_id in group:
            if len(instance_id) > 0:
                fight += self.get_card_fight(instance_id)
        fight += self.cacl_select_fight()
        rune_ids = self.get_rune_select_by_group_id(self.get_cur_group_id())
        if rune_ids:
            fight += len(rune_ids)
        return fight

    def get_cur_fight(self) -> int:
        """``pkgTeamDataMgr.lua:250-255`` -- the statement at pkgMainUIMgr:300."""

        gid = self.get_cur_group_id()
        group = self.get_team_card_data(gid)
        return self.cacl_group_fight(group)

    def init_btn_can_run(self) -> bool:
        """The recovered statement at ``pkgMainUIMgr.lua:1130`` completes.

        ``init`` runs ``updateInfo()`` at 1130, ``setLayoutResBtn()`` at 1131,
        ``updateInfo()`` at 1132 and ``initBtn()`` at 1133, so the statement the
        RUN-0026 logcat names is the one this models -- nothing more is
        claimed for the remaining statements of ``updateInfo``.
        """

        self.get_cur_fight()
        return True


def _read(stream, server, buffer, state, packets, count) -> None:
    while len(packets) < count:
        try:
            packet = decode_packet(buffer, server.s2c_registry, state)
        except NeedMoreData:
            chunk = stream.recv(4096)
            if not chunk:  # pragma: no cover - the stub never closes mid-segment
                raise AssertionError("server closed while reading the login stream")
            buffer.extend(chunk)
            continue
        del buffer[: packet.consumed]
        packets.append(packet)


class _ServerMixin:
    """A real ``RevivalTCPServer`` on a real socket, driven through login."""

    #: Set by each subclass; the whole point of the two classes below is that
    #: the SAME session is replayed on two different profiles.
    PROFILE = None

    def setUp(self) -> None:
        self.events: list[dict] = []
        self.clock = [SERVER_STARTED_UNIX]
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            compatibility_profile=self.PROFILE,
            time_source=lambda: self.clock[0],
        )
        self.clock[0] = SEND_NOW_UNIX
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _login(self, role_name: str, *, snapshot: bool) -> list:
        """Drive login+create+receivePackage and return the terminal segment."""

        writer = WriterState()
        state = ReaderState()
        buffer = bytearray()
        packets: list = []
        profile = self.PROFILE or get_profile()
        terminal = BASE_SEGMENT_PACKETS
        if snapshot:
            terminal += len(SNAPSHOT_IDS)
        if profile.recharge_activity is not None:
            terminal += 1
        # TASK-0098: the chapter-item pair, when the serving profile carries it.
        if profile.chapter_item_init is not None:
            terminal += profile.chapter_item_init.packet_count
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
            _read(stream, self.server, buffer, state, packets, terminal)
        return packets[BASE_LOGIN_PACKETS:]

    def _feed(self, segment: list) -> RecoveredTeamClient:
        client = RecoveredTeamClient()
        for packet in segment:
            if packet.spec.protocol_id in SNAPSHOT_IDS:
                client.apply(packet)
        return client


class SourcePinTests(unittest.TestCase):
    """The model above is only evidence while it still matches the sources."""

    def test_recovered_contract_still_pins(self) -> None:
        contract.assert_protocol_table()
        contract.assert_handler_writers()
        contract.assert_container_shapes()
        contract.assert_accessors()
        contract.assert_main_ui_chain()

    def test_registry_agrees_with_the_recovered_contract(self) -> None:
        self.assertEqual(sorted(contract.SENT_S2C), [35, 36, 37, 48])
        self.assertEqual(SNAPSHOT_IDS, [36, 37, 48, 35])
        for wire_id, (name, fmt) in contract.SENT_S2C.items():
            spec = S2C_REGISTRY.by_id(wire_id)
            self.assertEqual((spec.name, spec.fmt), (name, fmt))
        # ... and every deliberately omitted family member stays unregistered;
        # 39 returnTeamMaxFight IS registered by TASK-0245 / profile v22, but the
        # v5 snapshot above still sends exactly four packets (never 39).
        for wire_id in contract.OMITTED_S2C:
            with self.assertRaises(KeyError):
                S2C_REGISTRY.by_id(wire_id)
        for wire_id, (name, fmt) in contract.REGISTERED_LATER_S2C.items():
            spec = S2C_REGISTRY.by_id(wire_id)
            self.assertEqual((spec.name, spec.fmt), (name, fmt))
            self.assertNotIn(wire_id, SNAPSHOT_IDS)

    def test_the_policy_supplies_exactly_the_recovered_payloads(self) -> None:
        policy = get_profile().team_snapshot
        assert policy is not None
        self.assertEqual(policy.team_start_fields(), ())
        self.assertEqual(policy.select_magic_start_fields(), ())
        self.assertEqual(
            policy.team_info_fields(),
            (policy.current_group,) + (EMPTY,) * 16,
        )
        self.assertEqual(policy.group_id_fields(), (policy.current_group,))
        # And the model agrees the snapshot is enough for both unguarded reads.
        client = RecoveredTeamClient()
        client.s2c(TEAM_START_ID, policy.team_start_fields())
        client.s2c(TEAM_INFO_ID, policy.team_info_fields())
        client.s2c(SELECT_MAGIC_START_ID, policy.select_magic_start_fields())
        client.s2c(GROUP_ID_ID, policy.group_id_fields())
        self.assertEqual(client.get_group_equips(1), [EMPTY] * 4)
        self.assertEqual(client.get_team_god_weapon_id(1), EMPTY)


class BaseFalsificationTests(unittest.TestCase):
    """Without the snapshot the recovered chain raises -- RUN-0026's frame."""

    def test_pre_snapshot_state_raises_at_the_run_0026_line(self) -> None:
        client = RecoveredTeamClient()
        with self.assertRaises(RecoveredClientError) as caught:
            client.get_cur_fight()
        error = caught.exception
        self.assertEqual(error.frame, "team/pkgTeamDataMgr.lua:89")
        self.assertEqual(error.function, "getGroupEquips")
        # The -1 sentinel is the whole cause: getCurGroupID() names no container.
        self.assertEqual(client.get_cur_group_id(), -1)

    def test_either_half_alone_leaves_the_client_broken(self) -> None:
        # (a) the group id without the container: the abort moves EARLIER, to
        # getTeamCardData's own unguarded index at source line 114.
        only_group = RecoveredTeamClient()
        only_group.s2c(GROUP_ID_ID, (1,))
        with self.assertRaises(RecoveredClientError) as caught:
            only_group.get_cur_fight()
        self.assertEqual(caught.exception.frame, "team/pkgTeamDataMgr.lua:114")
        self.assertEqual(caught.exception.function, "getTeamCardData")

        # (b) the container without the group id
        only_container = RecoveredTeamClient()
        only_container.s2c(TEAM_START_ID, ())
        only_container.s2c(TEAM_INFO_ID, (1,) + (EMPTY,) * 16)
        only_container.s2c(SELECT_MAGIC_START_ID, ())
        with self.assertRaises(RecoveredClientError) as caught:
            only_container.get_cur_fight()
        self.assertEqual(caught.exception.frame, "team/pkgTeamDataMgr.lua:89")

    def test_the_fabao_container_is_a_second_required_producer(self) -> None:
        # D-33 section 3, measured: with a legal group id and the team container
        # but no S2C48, the error simply moves one statement later.
        client = RecoveredTeamClient()
        client.s2c(TEAM_START_ID, ())
        client.s2c(TEAM_INFO_ID, (1,) + (EMPTY,) * 16)
        client.s2c(GROUP_ID_ID, (1,))
        self.assertEqual(client.get_group_equips(1), [EMPTY] * 4)
        with self.assertRaises(RecoveredClientError) as caught:
            client.get_cur_fight()
        error = caught.exception
        self.assertEqual(error.frame, "fabao/pkgFabaoDataMgr.lua:690")
        self.assertEqual(error.function, "caclSelectFight")
        self.assertIn("pairs", error.detail)

    def test_a_non_empty_card_slot_is_not_computable_for_this_archive(self) -> None:
        # The snapshot's empty slots are not merely conventional: a non-empty
        # card id reaches pkgCardDataMgr.getCardFight, whose producer this
        # archive does not have, so filling a slot would invent a card row.
        client = RecoveredTeamClient()
        client.s2c(TEAM_START_ID, ())
        client.s2c(
            TEAM_INFO_ID,
            (1, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, "900001") + (EMPTY,) * 10,
        )
        client.s2c(SELECT_MAGIC_START_ID, ())
        client.s2c(GROUP_ID_ID, (1,))
        with self.assertRaises(RecoveredClientError) as caught:
            client.get_cur_fight()
        self.assertEqual(caught.exception.frame, "card/pkgCardDataMgr.lua:796")

    def test_out_of_range_group_ids_are_not_a_legal_current_group(self) -> None:
        for gid in (0, -1, D_MAX_TEAM + 1):
            client = RecoveredTeamClient()
            client.s2c(TEAM_START_ID, ())
            client.s2c(SELECT_MAGIC_START_ID, ())
            client.s2c(GROUP_ID_ID, (gid,))
            with self.subTest(group=gid):
                with self.assertRaises(RecoveredClientError) as caught:
                    client.get_cur_fight()
                self.assertEqual(
                    caught.exception.frame, "team/pkgTeamDataMgr.lua:89"
                )

    def test_group_id_sent_first_would_be_read_before_its_container_exists(self) -> None:
        # The recovered `setCurGroupID` dispatches CHANGE_TEAM, and pkgMainUIMgr
        # maps CHANGE_TEAM straight to updateFight -> getCurFight.  During login
        # no listener is registered yet, so nothing fires -- but a group id sent
        # before its container would be unsafe the moment one is.  This is why
        # the snapshot names the current preset LAST.
        client = RecoveredTeamClient()
        client.s2c(GROUP_ID_ID, (1,))
        self.assertEqual(client.dispatched, [("CHANGE_TEAM", (1,))])
        with self.assertRaises(RecoveredClientError) as caught:
            client.change_team_listener()
        self.assertEqual(caught.exception.frame, "team/pkgTeamDataMgr.lua:114")
        self.assertEqual(caught.exception.function, "getTeamCardData")

        ordered = RecoveredTeamClient()
        for protocol_id, fields in (
            (TEAM_START_ID, ()),
            (TEAM_INFO_ID, (1,) + (EMPTY,) * 16),
            (SELECT_MAGIC_START_ID, ()),
            (GROUP_ID_ID, (1,)),
        ):
            ordered.s2c(protocol_id, fields)
        self.assertEqual(ordered.change_team_listener(), 0)

    def test_snapshot_without_the_group_id_leaves_the_sentinel(self) -> None:
        # The last packet is the one that makes the preset current: without it
        # the containers exist but nothing names them.
        client = RecoveredTeamClient()
        client.s2c(TEAM_START_ID, ())
        client.s2c(TEAM_INFO_ID, (1,) + (EMPTY,) * 16)
        client.s2c(SELECT_MAGIC_START_ID, ())
        self.assertEqual(client.get_cur_group_id(), -1)
        with self.assertRaises(RecoveredClientError) as caught:
            client.get_cur_fight()
        self.assertEqual(caught.exception.frame, "team/pkgTeamDataMgr.lua:89")


class V5SnapshotTests(_ServerMixin, unittest.TestCase):
    """The shipped snapshot, driven by the real decoded wire.

    Pinned to the v5 profile itself (TASK-0098), not to whatever the active
    profile is: what this class asserts is "a v5 server emits this block with
    these bytes and this provenance", and later profiles only ever INHERIT the
    block unchanged, so leaving it on the active profile would silently re-point
    every frozen expectation at a different server's stream.
    """

    PROFILE = None  # set in setUpClass: the v5 profile this class is about

    @classmethod
    def setUpClass(cls) -> None:
        cls.PROFILE = get_profile_v5()

    def test_real_wire_clears_the_recovered_chain(self) -> None:
        segment = self._login("队伍道友", snapshot=True)
        self.assertEqual(
            [packet.spec.protocol_id for packet in segment],
            [8, 554, 27, 0, 487, 9] + SNAPSHOT_IDS + [411, 283],
        )
        client = self._feed(segment)

        # The three containers the recovered consumers read are all present and
        # iterable, and every slot is the client's own empty literal.
        self.assertEqual(client.get_cur_group_id(), 1)
        self.assertEqual(client.get_group_equips(1), [EMPTY] * 4)
        self.assertEqual(client.get_team_god_weapon_id(1), EMPTY)
        self.assertEqual(client.get_team_card_data(1), [EMPTY] * 11)
        self.assertEqual(client.get_team_fabao_data(1), [EMPTY] * 3)
        # getAllCards() walks all five presets through the unguarded
        # getGroupCards(); the named preset carries the client's own 11 empty
        # slots and the other four the empty tables initTeamCardData built.
        self.assertEqual(client.get_all_cards(), [EMPTY] * 11)
        self.assertEqual(client.cacl_select_fight(), 0)

        # ... and the statement pkgMainUIMgr:1130 runs now completes, where the
        # v4 session below reaches getGroupEquips at line 89 instead.
        self.assertTrue(client.init_btn_can_run())

    def test_the_block_is_contiguous_and_precedes_the_story_snapshot(self) -> None:
        segment = self._login("顺序道友", snapshot=True)
        ids = [packet.spec.protocol_id for packet in segment]
        # The four team packets are adjacent, and S2C411 is still the LAST
        # packet before the S2C283 whose dispatch starts the main-city switch.
        first = ids.index(TEAM_START_ID)
        self.assertEqual(ids[first:first + len(SNAPSHOT_IDS)], SNAPSHOT_IDS)
        # On a v5 server nothing sits between the block and the story snapshot;
        # the later profiles' insertions (TASK-0094's 1014, TASK-0098's 389/391)
        # go in the same gap and are asserted by their own modules.
        self.assertEqual(ids[first + len(SNAPSHOT_IDS)], 411)
        self.assertEqual(ids[-1], 283)
        self.assertEqual(ids[-2], 411)
        self.assertEqual(ids.index(283), ids.index(411) + 1)

    def test_the_provenance_event_carries_every_field_the_policy_claims(self) -> None:
        self._login("证据道友", snapshot=True)
        events = [
            event
            for event in self.events
            if event.get("event") == "tcp_team_snapshot_sent"
        ]
        self.assertEqual([event["protocol_id"] for event in events], SNAPSHOT_IDS)
        self.assertEqual(
            [event["snapshot_section"] for event in events],
            [
                "team_preset_reset",
                "team_preset_record",
                "fabao_select_container",
                "current_group",
            ],
        )
        policy = get_profile_v5().team_snapshot
        assert policy is not None
        for index, event in enumerate(events):
            with self.subTest(protocol=event["protocol_id"]):
                # This is the v5 server's own stream: the block's provenance
                # version and the serving profile both stay v5.  Every later
                # profile inherits the very same object, so a v5 server still
                # emits this block byte for byte.
                self.assertEqual(event["profile_id"], get_profile_v5().profile_id)
                self.assertEqual(event["parent_profile_id"], PROFILE_ID_V4)
                self.assertEqual(
                    event["policy_compatibility_version"], PROFILE_ID_V5
                )
                self.assertEqual(event["snapshot_index"], index)
                self.assertEqual(event["snapshot_packet_count"], len(SNAPSHOT_IDS))
                self.assertEqual(event["current_group"], policy.current_group)
                self.assertEqual(
                    event["current_group_policy"], policy.current_group_policy
                )
                self.assertEqual(
                    event["current_group_origin"], "synthetic_compatibility"
                )
                self.assertEqual(
                    event["container_policy_origin"], "synthetic_compatibility"
                )
                self.assertEqual(event["empty_slot"], EMPTY)
                self.assertEqual(event["historical_ordering"], "unknown")
                self.assertEqual(event["ordering_origin"], "synthetic_compatibility")
                self.assertIs(event["historical_values_recovered"], False)
        self.assertEqual(events[1]["empty_slot_count"], 16)
        self.assertEqual(events[1]["snapshot_section"], "team_preset_record")
        self.assertEqual(events[3]["snapshot_section"], "current_group")

    def test_no_unsupported_family_member_is_ever_sent(self) -> None:
        self._login("克制道友", snapshot=True)
        sent = {
            event.get("protocol_name")
            for event in self.events
            if event.get("event") == "tcp_packet_sent"
        }
        self.assertFalse(
            {
                *(
                    name
                    for name, _fmt in contract.OMITTED_S2C.values()
                ),
                *(
                    name
                    for name, _fmt in contract.REGISTERED_LATER_S2C.values()
                ),
                "updateTeam",
                "updateTeamError",
            }
            & sent
        )
        self.assertFalse(
            any(event.get("event") == "tcp_error" for event in self.events)
        )


class V4FalsificationTests(_ServerMixin, unittest.TestCase):
    """The same session on the previous profile still reproduces the abort."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.PROFILE = get_profile_v4()

    def test_v4_stream_leaves_the_client_at_the_run_0026_line(self) -> None:
        segment = self._login("回退道友", snapshot=False)
        ids = [packet.spec.protocol_id for packet in segment]
        self.assertEqual(ids, [8, 554, 27, 0, 487, 9, 411, 283])
        self.assertFalse(any(wire_id in SNAPSHOT_IDS for wire_id in ids))

        client = self._feed(segment)
        with self.assertRaises(RecoveredClientError) as caught:
            client.get_cur_fight()
        # Frame for frame, the RUN-0026 logcat stack ends where this model does.
        self.assertEqual(caught.exception.frame, "team/pkgTeamDataMgr.lua:89")
        self.assertEqual(caught.exception.function, "getGroupEquips")

    def test_no_team_packet_or_event_leaves_a_v4_server(self) -> None:
        self._login("沉默道友", snapshot=False)
        self.assertFalse(
            any(
                event.get("event") == "tcp_team_snapshot_sent"
                for event in self.events
            )
        )
        sent_ids = {
            event.get("protocol_id")
            for event in self.events
            if event.get("event") == "tcp_packet_sent"
        }
        self.assertFalse(set(SNAPSHOT_IDS) & sent_ids)

    def test_every_earlier_profile_has_no_snapshot_at_all(self) -> None:
        for profile in (
            get_profile_v1(),
            get_profile_v2(),
            get_profile_v3(),
            get_profile_v4(),
        ):
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.team_snapshot)
                self.assertNotEqual(profile.profile_id, PROFILE_ID_V5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
