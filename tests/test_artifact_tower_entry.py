"""TASK-0270 -- the artifact tower entry plan (offline, no device).

The tower cannot open on an all-zero path (TASK-0264), and its first node cannot be tapped
without a 6-star fabao of the run's race *and* at least six race-1 cards in the team
(TASK-0268). This suite pins the packets that satisfy both, in send order, and the fact that
1529 -- the packet that opens the view -- goes last.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_equip_fight_data_update import _ServerBase, FIXED_UNIX  # noqa: E402

from revival_stub import dev_play_artifact as dpa  # noqa: E402
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY  # noqa: E402
from revival_stub.role_store import RoleStore  # noqa: E402

WEEK_AND_OPEN_ID = 1526
RACE_AND_TOWER_ID = 1527
MAGIC_START_ID = 45
MAGIC_INFO_ID = 46
MAGIC_END_ID = 47
TOWER_OPEN_ID = 1529
NODE_ID = 1530

#: TASK-0351: the plan sends one 1530 per grid row; deriving the count
#: from the builder keeps these assertions honest when the grid grows.
NODE_ROWS = len([1 for _n, _f in dpa.tower_entry_packets(dpa.tower_card_instance_ids()) if _n == "artifactDgnSendNode"])
PATH_ID = 1531
TEAM_ID = 1538
ACH_INFO_ID = 1551
PATH_ID = 1531
NODE_ID = 1530

#: TASK-0281: the plan is sent by the team commit (745/746), not by 744 -- the device run
#: showed 1529 inside the 744 reply jumping the client into the tower 312 ms after the panel
#: opened. 744 answers only 1526/1527.
#: TASK-0351: the plan carries one 1530 per grid row (the first row is the wipe marker), so the
#: expected order is derived instead of hardcoded -- the grid comes straight from cfg.
#: A team commit may ALSO re-send the game-team snapshot (36/37/48/35) when the profile
#: carries a team_snapshot policy; the socket tests here run on a profile without one, so
#: the plan below is the packets this fixture must produce.
#: TASK-0396: the tower entry also pushes one S2C 1547 row per shop shelf.
SHOP_SHELF_ID = 1547

EXPECTED_PLAN_ORDER = (
    [SHOP_SHELF_ID] * 8 + [MAGIC_START_ID, MAGIC_INFO_ID, MAGIC_END_ID, PATH_ID]
    + [NODE_ID] * NODE_ROWS
    + [TEAM_ID, TOWER_OPEN_ID]
)

#: TASK-0389: the commit path first re-sends the game-team snapshot (36/37/48/35) and the
#: equipment push (39), so the client tbTeamCard follows the committed roster.
EXPECTED_COMMIT_ORDER = [36, 37, 48, 35, 39] + EXPECTED_PLAN_ORDER


class TowerEntryPlanTests(unittest.TestCase):
    def test_registration_rows(self):
        self.assertEqual(S2C_REGISTRY.by_id(MAGIC_START_ID).fmt, "")
        self.assertEqual(S2C_REGISTRY.by_id(MAGIC_INFO_ID).fmt, "a" + "N" * 20)
        self.assertEqual(S2C_REGISTRY.by_id(MAGIC_END_ID).fmt, "")
        self.assertEqual(S2C_REGISTRY.by_id(TOWER_OPEN_ID).fmt, "N")
        self.assertEqual(S2C_REGISTRY.by_id(NODE_ID).fmt, "NNNN")
        self.assertEqual(S2C_REGISTRY.by_id(PATH_ID).fmt, "N" * 26)
        self.assertEqual(S2C_REGISTRY.by_id(TEAM_ID).fmt, "a")
        self.assertEqual(C2S_REGISTRY.by_id(745).fmt, "a")
        self.assertEqual(C2S_REGISTRY.by_id(746).fmt, "N")

    def test_plan_order_and_last_packet(self):
        plan = dpa.tower_entry_packets(dpa.tower_card_instance_ids())
        names = [name for name, _fields in plan]
        self.assertEqual(names[-1], "artifactDgnSetRace")
        self.assertEqual(names[0], "returnMagicStart")
        self.assertIn("artifactDgnSendPath", names)
        # TASK-0350: five node rows now (layers 2 and 3), so the plan is 11 packets.
        self.assertEqual(names.count("artifactDgnSendNode"),
                         len(dpa.TOWER_NODE_EVENTS))
        self.assertEqual(len(plan), 4 + len(dpa.TOWER_NODE_EVENTS) + 2)

    def test_plan_values(self):
        ids = dpa.tower_card_instance_ids()
        plan = dict(dpa.tower_entry_packets(ids))
        self.assertEqual(plan["returnMagicStart"], ())
        self.assertEqual(plan["returnMagicEnd"], ())
        fabao = plan["returnMagicInfo"]
        self.assertEqual(len(fabao), 21)
        self.assertEqual(fabao[0], dpa.TOWER_FABAO_INSTANCE_ID)
        self.assertEqual(fabao[1], dpa.TOWER_FABAO_CFG_ID)
        # TASK-0292: the tower is race 3 (阴殿), so the fabao must be a star-6 RACE-3 row --
        # 214 落魂钟 or 215 九鬼迷魂幡 (TASK-0291 contract §3). 224/225 are race 1 and made the
        # client's isFabaoOK race test fail, which is why run8 never sent C2S 752.
        self.assertIn(dpa.TOWER_FABAO_CFG_ID, (214, 215),
                      "TASK-0291: the only star-6 race-3 fabao rows")
        path = plan["artifactDgnSendPath"]
        self.assertEqual(len(path), 26)
        self.assertEqual(path[0], 1, "slot 1 seeds the layer-1 entry node")
        self.assertEqual(path[0], 1,
                         "TASK-0351: only layer 1 has been walked on a fresh archive")
        self.assertEqual(sum(path[3:]), 0)
        nodes = [fields for name, fields in dpa.tower_entry_packets(ids)
                 if name == "artifactDgnSendNode"]
        # TASK-0350: the plan carries layers 2 and 3.
        self.assertEqual(nodes, list(dpa.TOWER_NODE_EVENTS))
        self.assertEqual(plan["artifactDgnSetRace"], (0,))

    def test_team_csv_is_eighteen_values(self):
        csv = dpa.tower_team_csv(dpa.tower_card_instance_ids())
        values = csv.split(",")
        self.assertEqual(len(values), 18)
        self.assertEqual(values[0], "0", "dSoulId")
        self.assertEqual(values[1:7], list(dpa.tower_card_instance_ids())[:6])
        self.assertEqual(values[11], dpa.TOWER_FABAO_INSTANCE_ID, "strMagicId1")
        self.assertEqual(values[12:14], ["0", "0"])

    def test_packet_names_exist_in_the_registry(self):
        for name, _fields in dpa.tower_entry_packets(dpa.tower_card_instance_ids()):
            self.assertIsNotNone(S2C_REGISTRY.by_name(name))


class TowerEntrySocketTests(_ServerBase):
    def _overlay_server(self):
        store = self._store(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        dpa.seed_overlay(store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        return store, server, events, stream, buffer, reader, writer

    def test_744_answers_only_the_two_entry_packets(self):
        # TASK-0281: the device run showed that 1529 inside the 744 reply jumped the client
        # into the tower 312 ms after the panel opened. 744 now answers 1526/1527 only.
        store, server, events, stream, buffer, reader, writer = self._overlay_server()
        self._send(stream, writer, 744, (0,))
        packets = self._read_through(stream, buffer, reader, ACH_INFO_ID)
        # TASK-0283: three node rows follow the path row, one 1530 each.
        for _ in range(NODE_ROWS - 1):
            packets += self._read_through(stream, buffer, reader, NODE_ID)
        # TASK-0285: fabao push (45/46/47) then the team row (1538) ride as read-only previews.
        packets += self._read_through(stream, buffer, reader, MAGIC_START_ID)
        packets += self._read_through(stream, buffer, reader, MAGIC_INFO_ID)
        packets += self._read_through(stream, buffer, reader, MAGIC_END_ID)
        packets += self._read_through(stream, buffer, reader, TEAM_ID)
        # TASK-0282 pushes 1551 (the entry page's score source); TASK-0283 adds the path/node
        # rows so a player who opens the tower from the entry page finds NEXT_NODE filled.
        self.assertEqual([packet.spec.protocol_id for packet in packets],
                         [WEEK_AND_OPEN_ID, RACE_AND_TOWER_ID, ACH_INFO_ID, PATH_ID]
                         + [NODE_ID] * NODE_ROWS + [
                          MAGIC_START_ID, MAGIC_INFO_ID, MAGIC_END_ID, TEAM_ID])
        self.assertEqual(self._drain(stream, buffer, reader), [])
        self.assertEqual(
            [event for event in events if event.get("event") == "tcp_artifact_tower_entry_sent"],
            [])
        self._assert_no_error(events)

    def test_the_team_commit_sends_the_whole_plan_ending_with_1529(self):
        store, server, events, stream, buffer, reader, writer = self._overlay_server()
        team = dpa.tower_team_csv(dpa.tower_card_instance_ids())
        self._send(stream, writer, 745, (team,))
        packets = self._read_through(stream, buffer, reader, TOWER_OPEN_ID)
        # TASK-0297: the S2C 1537 member rows travel inside this plan (before 1529).
        self.assertEqual([packet.spec.protocol_id for packet in packets
                          if packet.spec.protocol_id != 1537], EXPECTED_COMMIT_ORDER)
        self.assertEqual(packets[-1].spec.protocol_id, TOWER_OPEN_ID)
        event = self._wait_events(events, "tcp_artifact_tower_entry_sent", 1)[0]
        self.assertEqual(event["packet_order"][-1], "artifactDgnSetRace")
        self.assertEqual(event["sent_by"], "team_commit")
        self.assertEqual(event["fabao_cfg_id"], dpa.TOWER_FABAO_CFG_ID)
        self.assertEqual(event["card_cfg_id"], dpa.TOWER_WIND_CARD_CFG_ID)
        self.assertFalse(event["historical"])
        self.assertTrue(event["replaceable"])
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM card_instances").fetchone()[0]
        self.assertEqual(rows, dpa.TOWER_CARD_INSTANCE_COUNT)
        self._assert_no_error(events)

    def test_a_second_commit_does_not_duplicate_cards(self):
        store, server, events, stream, buffer, reader, writer = self._overlay_server()
        team = dpa.tower_team_csv(dpa.tower_card_instance_ids())
        self._send(stream, writer, 745, (team,))
        self._read_through(stream, buffer, reader, TOWER_OPEN_ID)
        self._send(stream, writer, 746, (1,))
        self._read_through(stream, buffer, reader, TOWER_OPEN_ID)
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM card_instances").fetchone()[0]
        self.assertEqual(rows, dpa.TOWER_CARD_INSTANCE_COUNT)

    def test_745_and_746_are_answered_with_the_whole_plan(self):
        store, server, events, stream, buffer, reader, writer = self._overlay_server()
        team = dpa.tower_team_csv(dpa.tower_card_instance_ids())
        self._send(stream, writer, 745, (team,))
        packets = self._read_through(stream, buffer, reader, TOWER_OPEN_ID)
        self.assertEqual([packet.spec.protocol_id for packet in packets
                          if packet.spec.protocol_id != 1537], EXPECTED_COMMIT_ORDER)
        self._send(stream, writer, 746, (1,))
        packets = self._read_through(stream, buffer, reader, TOWER_OPEN_ID)
        self.assertEqual([packet.spec.protocol_id for packet in packets
                          if packet.spec.protocol_id != 1537], EXPECTED_PLAN_ORDER)
        self._assert_no_error(events)

    def test_without_the_overlay_745_and_746_answer_nothing(self):
        store = self._store("qa-changan-main")
        server, events = self.start_server(store, dev_play_artifact=False)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        self._send(stream, writer, 746, (1,))
        self._send(stream, writer, 725, ())
        packets = self._read_through(stream, buffer, reader, 1500)
        self.assertEqual([packet.spec.protocol_id for packet in packets], [1499, 1500])
        self._assert_no_error(events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
