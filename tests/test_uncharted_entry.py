"""TASK-0272 -- 秘境 uncharted entry data plane (offline, no device).

C2S 244 is a LOCK-SEND: its registration pairs it with S2C 458, so the server must answer it
even when it refuses. Under the dev-play babel overlay it answers the whole first-clear chain
(434 -> 439 -> 440.. -> 441 -> 458); otherwise only 458(status=0) and nothing is created.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_equip_fight_data_update import _ServerBase, FIXED_UNIX  # noqa: E402

from revival_stub import dev_play_babel as dpb  # noqa: E402
from revival_stub import dev_play_uncharted as dpu  # noqa: E402
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY  # noqa: E402
from revival_stub.role_store import RoleStore  # noqa: E402

U_ENTER = 244
U_BUILDING = 260
U_CFG = 434
U_START = 439
U_PROCESS = 440
U_END = 441
U_STATUS = 458
MONTH_SIGN_ACK = 1500


class UnchartedPlanTests(unittest.TestCase):
    def test_registration_rows(self):
        self.assertEqual(C2S_REGISTRY.by_id(U_ENTER).name, "unchartedEnter")
        self.assertEqual(C2S_REGISTRY.by_id(U_ENTER).fmt, "Ny")
        self.assertEqual(C2S_REGISTRY.by_id(U_BUILDING).fmt, "")
        self.assertEqual(S2C_REGISTRY.by_id(U_CFG).fmt, "NNaNyyy")
        self.assertEqual(S2C_REGISTRY.by_id(U_START).fmt, "")
        self.assertEqual(S2C_REGISTRY.by_id(U_PROCESS).fmt, "NNNN")
        self.assertEqual(S2C_REGISTRY.by_id(U_END).fmt, "")
        self.assertEqual(S2C_REGISTRY.by_id(U_STATUS).fmt, "NN")

    def test_cfg_fields_are_the_run_identity(self):
        fields = dpu.return_uncharted_cfg_fields(3)
        self.assertEqual(len(fields), 7)
        self.assertEqual(fields[0], 3)
        self.assertIsInstance(fields[3], int)
        self.assertEqual(fields[4:], (False, False, False))

    def test_plan_order_and_last_packet(self):
        # TASK-0326: 439 must precede 434 (the cfg handler runs findHideRoomEnter, which reads the
        # hide data 439 creates), the completed bracket follows the rooms, and 458 stays last.
        plan = dpu.uncharted_entry_packets(1)
        names = [name for name, _fields in plan]
        self.assertEqual(names[0], "returnUnchrtedDataStart")
        self.assertEqual(names[1], "returnUnchartedCfg")
        self.assertEqual([name for name in names if name == "returnUnchrtedDataProcess"],
                         ["returnUnchrtedDataProcess"] * len(dpu.UNCHARTED_MINIMAL_ROOMS))
        self.assertIn("returnUnchrtedCompletedStart", names)
        self.assertIn("returnUnchrtedCompletedEnd", names)
        self.assertEqual(names[-2], "returnUnchrtedNpcHpRecordEnd")
        self.assertEqual(names[-1], "unchartedEnter", "458 unlocks the lock-send")
        self.assertEqual(plan[-1][1], (dpu.UNCHARTED_STATUS_ENTER, dpu.UNCHARTED_ENTRY_LAYER))

    def test_minimal_matrix_has_at_least_three_rooms(self):
        # The client asserts (room count - 1) > 1 for the first area (RUN27 terrain assert).
        rows = dpu.uncharted_room_rows()
        self.assertGreaterEqual(len(rows), 3)
        # TASK-0335: room 1 IS the exit room (106) -- the only room that hosts the exit NPC's 斩
        # control -- because the bridge is not walkable; room 2 keeps the entrance room 101.
        self.assertEqual(rows[0], (1, 1, 1, dpu.UNCHARTED_EXIT_ROOM))
        self.assertEqual(rows[1][3], dpu.UNCHARTED_FIRST_FIGHT_ROOM)
        self.assertEqual(dpu.UNCHARTED_PVE_TYPE, 88)


class UnchartedSocketTests(_ServerBase):
    def start_server(self, store, *, dev_play_babel=False):
        """The shared harness only knows dev_play_artifact; this suite needs the babel flag."""
        import threading

        from revival_stub.eventlog import EventLogger
        from revival_stub.tcp_stub import RevivalTCPServer

        events = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append),
            time_source=lambda: FIXED_UNIX,
            role_store=store,
            dev_play_babel=dev_play_babel,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)
        return server, events

    def _overlay_login(self):
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        store._profile_fields = {"play_profile_id": dpb.DEV_PLAY_BABEL_PROFILE_ID}
        self.addCleanup(store.close)
        dpb.seed_overlay(store, profile_id=dpb.DEV_PLAY_BABEL_PROFILE_ID)
        server, events = self.start_server(store, dev_play_babel=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        return store, server, events, stream, buffer, reader, writer

    def test_overlay_answers_the_whole_entry_chain(self):
        store, server, events, stream, buffer, reader, writer = self._overlay_login()
        self._send(stream, writer, U_ENTER, (1, False))
        packets = self._read_through(stream, buffer, reader, U_STATUS)
        rooms = len(dpu.UNCHARTED_MINIMAL_ROOMS)
        # TASK-0327: after the completed bracket the scene needs the buff / task / npc-hp
        # containers too; 458 stays last.
        self.assertEqual(
            [packet.spec.protocol_id for packet in packets],
            [U_START, U_CFG] + [U_PROCESS] * rooms
            + [U_END, 468, 469, 470, 435, 436, 437, 442, 444,
               446, 448, 450, 452, 454, 456, U_STATUS],
        )
        self.assertEqual(packets[1].fields[0], 1)
        self.assertEqual(packets[2].fields, (1, 1, 1, dpu.UNCHARTED_EXIT_ROOM))
        self.assertEqual(packets[-1].fields,
                         (dpu.UNCHARTED_STATUS_ENTER, dpu.UNCHARTED_ENTRY_LAYER))
        event = self._wait_events(events, "tcp_uncharted_entry_sent", 1)[0]
        self.assertEqual(event["packet_order"][-1], "unchartedEnter")
        self.assertEqual(event["room_rows"],
                         [list(row) for row in dpu.uncharted_room_rows()])
        self.assertEqual(event["pve_type"], 88)
        self.assertFalse(event["historical"])
        self._assert_no_error(events)

    def test_building_sync_is_recorded_without_a_reply(self):
        store, server, events, stream, buffer, reader, writer = self._overlay_login()
        self._send(stream, writer, U_BUILDING, ())
        self._send(stream, writer, 725, ())
        packets = self._read_through(stream, buffer, reader, MONTH_SIGN_ACK)
        ids = [packet.spec.protocol_id for packet in packets]
        # The month-sign pair proves the reader still answers; 1499 may already have been
        # consumed with the login tail, so only the ack is required here.
        self.assertIn(MONTH_SIGN_ACK, ids)
        for uncharted_id in (U_CFG, U_START, U_PROCESS, U_END, U_STATUS):
            self.assertNotIn(uncharted_id, ids)
        received = self._wait_events(
            events, "tcp_uncharted_building_sync_received", 1)[0]
        self.assertFalse(received["response_sent"])
        self._assert_no_error(events)

    def test_without_the_overlay_the_lock_send_is_still_released(self):
        store = self._store("qa-changan-main")
        server, events = self.start_server(store, dev_play_babel=False)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        self._send(stream, writer, U_ENTER, (1, False))
        packets = self._read_through(stream, buffer, reader, U_STATUS)
        self.assertEqual([packet.spec.protocol_id for packet in packets], [U_STATUS])
        self.assertEqual(packets[0].fields[0], dpu.UNCHARTED_STATUS_REFUSED)
        refused = self._wait_events(events, "tcp_uncharted_enter_refused", 1)[0]
        self.assertFalse(refused["state_changed"])
        self.assertFalse(refused["role_store_written"])
        # The socket still serves the client afterwards.
        self._send(stream, writer, 725, ())
        packets = self._read_through(stream, buffer, reader, MONTH_SIGN_ACK)
        self.assertEqual([packet.spec.protocol_id for packet in packets],
                         [1499, MONTH_SIGN_ACK])
        self._assert_no_error(events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
