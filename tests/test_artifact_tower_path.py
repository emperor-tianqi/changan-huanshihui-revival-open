"""TASK-0358 -- the tower path: encoding, archive replay, and the go-next answer.

The client computes its current layer as "highest slot with a non-zero value, plus one"
(pkgArtifactDgnMgr.getNextLayer). Two bugs came out of the live session:

* the go-next answer called path_fields_for_layer(layer, sn) with the CLIENT's sn (0), which
  zeroed every slot and threw the player back to layer 1 ("clicked next layer, told to clear
  first");
* a slot means "nodes finished in this layer", so writing sn+1 made a half-cleared layer look
  complete and the view skipped its remaining node.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import dev_play_artifact as dpa  # noqa: E402
from revival_stub.role_store import RoleStore  # noqa: E402

ROLE = 900000000001
TOWER = 1


class TowerPathTests(unittest.TestCase):
    def setUp(self):
        self.store = RoleStore.in_memory()
        self.addCleanup(self.store.close)

    def path(self):
        return dpa.cleared_path(self.store, ROLE, TOWER)

    def test_a_fresh_archive_keeps_only_the_entry_open(self):
        path = self.path()
        self.assertEqual(path[0], 1, "layer 1 must stay walkable")
        self.assertEqual(sum(path[1:]), 0)

    def test_a_clear_is_replayed_as_a_finished_node(self):
        dpa.record_clear(self.store, ROLE, TOWER, 2, 1)
        path = self.path()
        self.assertEqual(path[:3], (1, 1, 0),
                         "one finished node in layer 2, layer 3 still closed")

    def test_the_last_node_of_a_layer_does_not_preopen_the_next(self):
        # TASK-0363: the client computes its layer as "highest non-zero slot + 1", so writing
        # a 1 into the next slot made it jump there as if a node were already finished.
        dpa.record_clear(self.store, ROLE, TOWER, 2, 1)
        dpa.record_clear(self.store, ROLE, TOWER, 2, 2)
        path = self.path()
        self.assertEqual(path[:3], (1, 2, 0),
                         "layer 2 complete, layer 3 still untouched")
        after = dpa.path_after_clear(self.store, ROLE, TOWER, 2, 2)
        self.assertEqual(after[:3], (1, 2, 0),
                         "no slot is written ahead of the player")

    def test_a_half_cleared_layer_does_not_look_complete(self):
        after = dpa.path_after_clear(self.store, ROLE, TOWER, 2, 1)
        self.assertEqual(after[:3], (1, 1, 0),
                         "clearing sn 1 of a two-node layer must not open layer 3")

    def test_the_plan_ships_that_path(self):
        dpa.record_clear(self.store, ROLE, TOWER, 2, 1)
        packets = dict(dpa.tower_data_packets(self.path()))
        self.assertEqual(packets["artifactDgnSendPath"][:3], (1, 1, 0))
        self.assertEqual(len(packets["artifactDgnSendPath"]), 26)

    def test_a_legacy_grant_row_still_counts(self):
        self.store.set_meta(f"artifact_dgn_grant:{ROLE}:{TOWER}:4:1", "60001")
        self.assertEqual(self.path()[3], 1, "an old reward ledger entry is progress too")


    def test_a_node_without_a_declared_stage_takes_its_layer_stage(self):
        """TASK-0424: elite and boss events declare stage 0; 101 everywhere made them clones."""

        self.assertEqual(dpa.node_stage_id(2, 1), 101, "a declared stage wins")
        self.assertEqual(dpa.node_stage_id(3, 1), 103, "layer 3 elite loses the 101 clone")
        self.assertEqual(dpa.node_stage_id(11, 1), 111, "layer 11 boss likewise")
        self.assertGreaterEqual(dpa.layer_stage_id(1), dpa.ARTIFACT_STAGE_FIRST)
        self.assertLessEqual(dpa.layer_stage_id(99), dpa.ARTIFACT_STAGE_LAST)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
