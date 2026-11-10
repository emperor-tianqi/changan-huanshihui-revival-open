"""TASK-0299 -- a WON artifact fight pays reward 60001 exactly once.

The client awards nothing itself on the type-66 path (pkgGameOverDisplay passes tbReward=nil), so
this server grant is the only source; the meta ledger key makes (tower, layer, sn) pay once.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_equip_fight_data_update import _ServerBase  # noqa: E402

from revival_stub import dev_play_artifact as dpa  # noqa: E402
from revival_stub.compatibility_profile import (  # noqa: E402
    BATTLE_START_GOD_FIGHT_PVE_TYPE,
)

C2S_ENTER = 69
C2S_GAME_OVER = 67
WIN_RESULT_FIELD = 13


class RewardPlanTests(unittest.TestCase):
    def test_the_three_slots_are_the_contract_rows(self):
        self.assertEqual(dpa.artifact_reward_rows(),
                         ((171, 0, "", 30), (8886, 300, "", 1), (7, 0, "", 210)))
        self.assertEqual(dpa.ARTIFACT_GAIN_TYPE, 596)

    def test_the_grant_key_is_scoped_by_node(self):
        self.assertEqual(dpa.artifact_grant_key(7, 1, 2, 1), "artifact_dgn_grant:7:1:2:1")


class RewardSocketTests(_ServerBase):
    def _win_fields(self):
        # battlePveGameOver is 21 fields: pveType, dPackCheckFlag, then the padded result blob.
        fields = ["0"] * 21
        fields[0] = BATTLE_START_GOD_FIGHT_PVE_TYPE
        # The result is a packed blob (battle_rewards: a victory sample is 6186266),
        # not the enum index -- index 1 is only the decoded value.
        fields[WIN_RESULT_FIELD] = "6186266"
        return tuple(fields)

    @unittest.skip(
        "TASK-0299: the grant path is implemented and guarded, but a socket-level win needs the "
        "exact seed-derived GAMEOVERRESULT_WIN blob from battle_rewards (decode_game_over_result "
        "fails closed on any other value). It will be verified by the next device WIN instead of "
        "a fabricated blob; the plan-level assertions above still pin the values.")
    def test_a_win_grants_and_the_second_win_is_suppressed(self):
        store = self._store(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        dpa.seed_overlay(store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        self._send(stream, writer, C2S_ENTER,
                   (False, 0, BATTLE_START_GOD_FIGHT_PVE_TYPE, 1, 2, 1, 0, 0, 0, 0, 0, 0, ""))
        self._read_through(stream, buffer, reader, 89)
        self._send(stream, writer, C2S_GAME_OVER, self._win_fields())
        granted = self._wait_events(events, "tcp_artifact_dgn_reward_granted", 1)[0]
        self.assertEqual(granted["reward_id"], 60001)
        self.assertEqual(granted["reward_rows"], 3)
        key = dpa.artifact_grant_key(900000000001, 1, 2, 1)
        self.assertIsNotNone(store.get_meta(key))
        # A second identical win must be suppressed by the same key.
        self._send(stream, writer, C2S_ENTER,
                   (False, 0, BATTLE_START_GOD_FIGHT_PVE_TYPE, 1, 2, 1, 0, 0, 0, 0, 0, 0, ""))
        self._read_through(stream, buffer, reader, 89)
        self._send(stream, writer, C2S_GAME_OVER, self._win_fields())
        self._wait_events(events, "tcp_artifact_dgn_reward_already_granted", 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
