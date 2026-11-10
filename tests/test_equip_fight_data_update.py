"""TASK-0263 -- C2S 653 is answered with the 装备战斗面板 data plane (1320 -> 1321).

The artifact dungeon has no main-city button: its only client entrance is the
equip-fight panel artifact TAB (pkgEquipFightPanel._addMenu, source lines 38-42).
show() sends the zero-field C2S 653 and then C2S 744 on the same connection
(lines 62-77), and the panel is driven by exactly two server packets:

* S2C 1320 syncEquipFightStageToCg -- unlocks fb cells (only strOpenFbIds writes
  e_tbOpenFbIds, read at pkgEquipFightModel.lua:118) and stops the 1 Hz re-send of
  C2S 653 (_onUpdateTimer compares e_dNextFreshTime with the client own server time).
* S2C 1321 syncEquipFightRandomDataToCg -- must arrive before a stage detail
  renders, because getScoreBuffIds feeds getStageRuleIds into ipairs
  (pkgEquipFightModel.lua:221-224).

Every value is a dev-play CHOICE (synthetic_compatibility, replaceable, not
historical).  A session without the explicit dev-play artifact overlay keeps the
TASK-0259 record-only contract: no packet, nothing written, socket kept.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest

from revival_stub import dev_play_artifact as dpa
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.eventlog import EventLogger
from revival_stub.protocol_catalog import LOCK_PARTNERS
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import RoleStore
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


FIXED_UNIX = 1_790_000_000.0
READ_TIMEOUT_S = 8.0

C2S_DATA_UPDATE_ID = 653
MONTH_SIGN_ACK_ID = 1500
STAGE_TO_CG_ID = 1320
RANDOM_DATA_ID = 1321
WEEK_AND_OPEN_ID = 1526
RACE_AND_TOWER_ID = 1527

#: len(S2C_REGISTRY) frozen at TASK-0263 (91 at TASK-0259 + 1320/1321);
#: TASK-0266 adds the bag goodsUse unlock S2C 820.
#: TASK-0331: +22 rows today -- 1264/1265/1266/1621/1622/1623/1625 (equip+god weapon), 1532
#: (non-battle doEvt), 484/485/486 (uncharted buildings), 435/436/437 (unlock area), 442/443/444
#: (completed), 446/447/448 + 450/451/452 + 454/455/456 (buff/task/npc-hp), 468/469/470/471
#: (boss select). Two zero-field C2S ids (525/698) were added in the same change.
#: TASK-0428: +2 rows -- S2C 220/221 battleTransferBegin/Base, the transfer pair
#: that materialises le_tbMatchInfo for the 封妖 layer challenge buttons.  The
#: previous pin (217) was stale against the shipped 228 rows; the measured total
#: after this change is 230; TASK-0429 adds S2C 223 battleTransferCard -> 231;
#: TASK-0430 adds S2C 222 soul + 228 shenfu -> 233.
S2C_REGISTRY_ROWS = 249

#: Equip-fight family members this slice must still leave unimplemented.
UNIMPLEMENTED_S2C = (1322, 1323, 1328)  # returnEquipFightDataUpdate/Error/OneKey
UNIMPLEMENTED_C2S = (654,)  # sendEquipFightOneKey


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class _ServerBase(unittest.TestCase):
    """One in-memory archive plus one live strict server per test."""

    def _store(self, profile_id: str) -> RoleStore:
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        store._profile_fields = {"play_profile_id": profile_id}
        self.addCleanup(store.close)
        return store

    def start_server(self, store: RoleStore, *, dev_play_artifact: bool = False):
        events: list[dict] = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append),
            time_source=lambda: FIXED_UNIX,
            role_store=store,
            dev_play_artifact=dev_play_artifact,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)
        return server, events

    def _login_keep_open(self, server: RevivalTCPServer):
        writer = WriterState()
        reader = ReaderState()
        payload = bytearray(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(2), (b"EquipFight", 1), writer).wire
        )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
        stream = socket.create_connection(
            ("127.0.0.1", server.server_address[1]), timeout=READ_TIMEOUT_S
        )
        stream.settimeout(READ_TIMEOUT_S)
        self.addCleanup(stream.close)
        stream.sendall(bytes(payload))
        buffer = bytearray()
        packets = []
        deadline = time.monotonic() + READ_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                chunk = stream.recv(65536)
                if not chunk:
                    self.fail(
                        "server closed the login stream after "
                        f"{len(packets)} packets"
                    )
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == 283:  # returnSceneInfo
                return stream, buffer, reader, writer
        self.fail("login never reached S2C 283")

    @staticmethod
    def _send(stream, writer, protocol_id: int, fields) -> None:
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, writer).wire
        )

    def _read_through(self, stream, buffer, reader, protocol_id: int):
        packets = []
        deadline = time.monotonic() + READ_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                chunk = stream.recv(65536)
                if not chunk:
                    self.fail(f"server closed the socket before S2C {protocol_id}")
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == protocol_id:
                return packets
        self.fail(f"no S2C {protocol_id} answer arrived")

    def _drain(self, stream, buffer, reader, window: float = 0.5):
        packets = []
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                stream.settimeout(max(0.01, deadline - time.monotonic()))
                try:
                    chunk = stream.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
        stream.settimeout(READ_TIMEOUT_S)
        return packets

    @staticmethod
    def _wait_events(
        events: list[dict], name: str, count: int, timeout: float = 3.0
    ) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [item for item in events if item.get("event") == name]
            if len(matches) >= count:
                return matches
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} #{count} was not emitted")

    @staticmethod
    def _meta_rows(store: RoleStore) -> list[tuple[str, str]]:
        return list(
            store._conn.execute("SELECT key, value FROM meta ORDER BY key")
        )

    @staticmethod
    def _assert_no_error(events: list[dict]) -> None:
        offenders = [
            item for item in events
            if item.get("event") in ("tcp_error", "tcp_disconnected")
        ]
        assert not offenders, offenders


class PanelFieldsTests(unittest.TestCase):
    """The two field tuples are pure functions of the server clock."""

    def test_stage_fields_are_the_contract_layout(self) -> None:
        stage, random = dpa.equip_fight_panel_fields(FIXED_UNIX)
        self.assertEqual(len(stage), 7, "S2C 1320 is paayapp")
        self.assertEqual(len(random), 3, "S2C 1321 is paa")
        self.assertAlmostEqual(stage[0], FIXED_UNIX)
        # TASK-0311: the empty-table contract was replaced by the cfg-derived rows.
        # RUN17 raised `pkgEquipFightModel.lua:213 ipairs(nil)` because S2C 1320/1321
        # carried no stage/rule keys, so both 1320 tables now describe all 45 stage
        # rows of fbs 1..5 (cfgEquipFightStage), each with the cfg rule ids.  The exact
        # literals are pinned by tests/test_equip_fight_stage_rows.py; here each field must
        # be the table its own slot names -- 1320[1] stage scores, 1320[2] no-clear scores,
        # 1321[1] buff ids, 1321[2] score-rule ids (the first two share the same bytes).
        self.assertEqual(stage[1], dpa.EQUIP_FIGHT_PANEL_STAGE_TABLE)
        self.assertEqual(stage[2], dpa.EQUIP_FIGHT_PANEL_SCORE_NO_CLEAR_TABLE)
        self.assertIs(stage[3], False, "isDebugAllOpen must be a Lua false, not 0")
        self.assertEqual(stage[4], "1#2#3#4#5")
        self.assertGreater(stage[5], FIXED_UNIX)
        self.assertGreater(stage[6], FIXED_UNIX)
        self.assertEqual(random[1], dpa.EQUIP_FIGHT_PANEL_BUFF_TABLE)
        self.assertEqual(random[2], dpa.EQUIP_FIGHT_PANEL_SCORE_RULE_TABLE)

    def test_values_are_stable_for_the_same_instant(self) -> None:
        self.assertEqual(
            dpa.equip_fight_panel_fields(FIXED_UNIX),
            dpa.equip_fight_panel_fields(FIXED_UNIX),
        )

    def test_refresh_window_is_positive(self) -> None:
        self.assertGreater(dpa.EQUIP_FIGHT_PANEL_REFRESH_WINDOW_S, 0)
        self.assertEqual(dpa.EQUIP_FIGHT_PANEL_OPEN_FB_IDS, (1, 2, 3, 4, 5))

    def test_bad_clock_is_refused_by_name(self) -> None:
        with self.assertRaises(dpa.DevPlayArtifactError):
            dpa.equip_fight_panel_fields("not-a-clock")


class EquipFightPanelOnOverlayTests(_ServerBase):
    """The live socket contract with the explicit dev-play overlay applied."""

    def test_653_is_answered_1320_then_1321_before_744(self) -> None:
        store = self._store(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        dpa.seed_overlay(store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        server, events = self.start_server(store, dev_play_artifact=True)
        self.assertIn(C2S_DATA_UPDATE_ID, server.implemented_c2s_ids)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        roles_before = store.count()
        meta_before = self._meta_rows(store)

        self._send(stream, writer, C2S_DATA_UPDATE_ID, ())
        self._send(stream, writer, 744, (0,))
        packets = self._read_through(stream, buffer, reader, RACE_AND_TOWER_ID)
        self.assertEqual(
            [packet.spec.protocol_id for packet in packets],
            [STAGE_TO_CG_ID, RANDOM_DATA_ID, WEEK_AND_OPEN_ID, RACE_AND_TOWER_ID],
            "the panel data plane must arrive first, then the artifact entry pair",
        )

        stage = packets[0]
        self.assertEqual(stage.spec.fmt, "paayapp")
        self.assertAlmostEqual(stage.fields[0], FIXED_UNIX)
        # TASK-0311: not empty any more -- the 45 cfg stage rows and their rule ids;
        # the literals live in tests/test_equip_fight_stage_rows.py.
        self.assertEqual(_text(stage.fields[1]), dpa.EQUIP_FIGHT_PANEL_STAGE_TABLE)
        self.assertEqual(_text(stage.fields[2]), dpa.EQUIP_FIGHT_PANEL_SCORE_NO_CLEAR_TABLE)
        self.assertIs(stage.fields[3], False)
        self.assertEqual(_text(stage.fields[4]), "1#2#3#4#5")
        self.assertGreater(stage.fields[5], FIXED_UNIX)
        self.assertGreater(stage.fields[6], FIXED_UNIX)

        random = packets[1]
        self.assertEqual(random.spec.fmt, "paa")
        self.assertEqual(_text(random.fields[1]), dpa.EQUIP_FIGHT_PANEL_BUFF_TABLE)
        self.assertEqual(_text(random.fields[2]), dpa.EQUIP_FIGHT_PANEL_SCORE_RULE_TABLE)

        received = self._wait_events(
            events, "tcp_equip_fight_data_update_received", 1
        )[0]
        self.assertTrue(received["overlay_applied"])
        self.assertEqual(received["request_field_count"], 0)
        self.assertEqual(received["reply_ordering_unknown_id"], "U-B")

        stage_event = self._wait_events(events, "tcp_equip_fight_stage_sent", 1)[0]
        self.assertEqual(stage_event["protocol_id"], STAGE_TO_CG_ID)
        self.assertEqual(stage_event["strOpenFbIds"], "1#2#3#4#5")
        self.assertEqual(stage_event["open_fb_ids"], [1, 2, 3, 4, 5])
        self.assertEqual(
            stage_event["value_provenance"], dpa.PROVENANCE_SYNTHETIC_COMPATIBILITY
        )
        self.assertFalse(stage_event["historical"])
        self.assertTrue(stage_event["replaceable"])
        self.assertFalse(stage_event["state_changed"])
        self.assertFalse(stage_event["role_store_written"])

        random_event = self._wait_events(events, "tcp_equip_fight_random_sent", 1)[0]
        self.assertEqual(random_event["protocol_id"], RANDOM_DATA_ID)
        self.assertEqual(
            random_event["packet_order"],
            ["syncEquipFightStageToCg", "syncEquipFightRandomDataToCg"],
        )
        self._assert_no_error(events)

        # Synthetic data must never be persisted as role state.
        self.assertEqual(store.count(), roles_before)
        self.assertEqual(self._meta_rows(store), meta_before)

    def test_a_repeated_request_is_answered_again(self) -> None:
        store = self._store(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        dpa.seed_overlay(store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)

        self._send(stream, writer, C2S_DATA_UPDATE_ID, ())
        first = self._read_through(stream, buffer, reader, RANDOM_DATA_ID)
        self.assertEqual(
            [packet.spec.protocol_id for packet in first],
            [STAGE_TO_CG_ID, RANDOM_DATA_ID],
        )
        self._send(stream, writer, C2S_DATA_UPDATE_ID, ())
        second = self._read_through(stream, buffer, reader, RANDOM_DATA_ID)
        self.assertEqual(
            [packet.spec.protocol_id for packet in second],
            [STAGE_TO_CG_ID, RANDOM_DATA_ID],
        )
        self.assertEqual(
            [tuple(packet.fields) for packet in first],
            [tuple(packet.fields) for packet in second],
            "the same server instant must answer the same values",
        )
        events_out = self._wait_events(
            events, "tcp_equip_fight_data_update_received", 2
        )
        self.assertFalse(events_out[0]["repeated_request"])
        self.assertTrue(events_out[1]["repeated_request"])
        self._assert_no_error(events)


class EquipFightPanelWithoutOverlayTests(_ServerBase):
    """No overlay: the TASK-0259 record-only contract is unchanged."""

    def test_strict_653_sends_nothing_and_keeps_the_connection(self) -> None:
        store = self._store("qa-changan-main")
        server, events = self.start_server(store, dev_play_artifact=False)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        roles_before = store.count()
        meta_before = self._meta_rows(store)

        self._send(stream, writer, C2S_DATA_UPDATE_ID, ())
        self._send(stream, writer, 725, ())
        packets = self._read_through(stream, buffer, reader, MONTH_SIGN_ACK_ID)
        ids = [packet.spec.protocol_id for packet in packets]
        self.assertEqual(ids, [1499, MONTH_SIGN_ACK_ID])
        self.assertNotIn(STAGE_TO_CG_ID, ids)
        self.assertNotIn(RANDOM_DATA_ID, ids)
        self.assertNotIn(WEEK_AND_OPEN_ID, ids)
        self.assertNotIn(RACE_AND_TOWER_ID, ids)

        received = self._wait_events(
            events, "tcp_equip_fight_data_update_received", 1
        )[0]
        self.assertFalse(received["overlay_applied"])
        recorded = self._wait_events(
            events, "tcp_equip_fight_data_update_recorded", 1
        )[0]
        self.assertFalse(recorded["response_sent"])
        self.assertEqual(recorded["response_protocol_ids"], [])
        self.assertEqual(
            recorded["reason"], "dev_play_artifact_overlay_not_applied"
        )
        self.assertTrue(recorded["connection_kept_alive"])
        self.assertFalse(recorded["state_changed"])
        self.assertFalse(recorded["role_store_written"])
        self._assert_no_error(events)

        self.assertEqual(store.count(), roles_before)
        self.assertEqual(self._meta_rows(store), meta_before)


class EquipFightRegistryTests(unittest.TestCase):
    """Exactly two S2C rows are added; the rest of the family stays absent."""

    def test_the_c2s_registration_is_the_client_row(self) -> None:
        spec = C2S_REGISTRY.by_id(C2S_DATA_UPDATE_ID)
        self.assertEqual(spec.name, "sendToEquipFightDataUpate")
        self.assertEqual(spec.fmt, "")
        self.assertEqual(0, len(spec.fmt))
        self.assertEqual(LOCK_PARTNERS.get("sendToEquipFightDataUpate", ()), ())

    def test_the_two_panel_rows_are_registered(self) -> None:
        self.assertEqual(len(S2C_REGISTRY), S2C_REGISTRY_ROWS)
        stage = S2C_REGISTRY.by_id(STAGE_TO_CG_ID)
        self.assertEqual(stage.name, "syncEquipFightStageToCg")
        self.assertEqual(stage.fmt, "paayapp")
        random = S2C_REGISTRY.by_id(RANDOM_DATA_ID)
        self.assertEqual(random.name, "syncEquipFightRandomDataToCg")
        self.assertEqual(random.fmt, "paa")
        for name in (stage.name, random.name):
            self.assertEqual(LOCK_PARTNERS.get(name, ()), ())

    def test_the_rest_of_the_family_stays_unimplemented(self) -> None:
        for protocol_id in UNIMPLEMENTED_S2C:
            with self.assertRaises(KeyError):
                S2C_REGISTRY.by_id(protocol_id)
        for name in (
            "returnEquipFightDataUpdate",
            "returnEquipFightError",
            "returnEquipFightOneKey",
        ):
            with self.assertRaises(KeyError):
                S2C_REGISTRY.by_name(name)
        for protocol_id in UNIMPLEMENTED_C2S:
            with self.assertRaises(KeyError):
                C2S_REGISTRY.by_id(protocol_id)

    def test_the_strict_reader_knows_exactly_the_registered_ids(self) -> None:
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        self.addCleanup(store.close)
        records: list[dict] = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=records.append),
            time_source=lambda: FIXED_UNIX,
            role_store=store,
        )
        self.addCleanup(server.server_close)
        self.assertIs(server.decode_c2s_registry, server.c2s_registry)
        self.assertEqual(
            server.implemented_c2s_ids,
            frozenset(spec.protocol_id for spec in C2S_REGISTRY),
        )
        self.assertIn(C2S_DATA_UPDATE_ID, server.implemented_c2s_ids)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
