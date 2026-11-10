"""TASK-0296 -- the type-66 S2C 89 carries the stage id and the enemy HP table.

RUN9 died in initGodFightData because position 8 echoed the request's tower id (1) instead of the
stage id (101), and position 18 was empty, which zeroes every monster slot.
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
    BATTLE_START_TYPE_POSITION,
)

C2S_ENTER_BATTLE = 69
S2C_START_BATTLE = 89


class GodFightPlanTests(unittest.TestCase):
    def test_stage_is_derived_from_the_node(self):
        for node in ((1, 2, 1), (1, 2, 2), (2, 2, 1)):
            self.assertEqual(dpa.god_fight_stage_id(*node), 101)

    def test_hp_table_covers_five_monsters_at_the_dev_play_percent(self):
        # TASK-0307: the dev-play overlay lowers enemy HP (server-declared, client state untouched);
        # percent=10000 keeps the historical full-HP table for tests that want it.
        self.assertEqual(dpa.god_fight_hp_csv(),
                         "1,2500;2,2500;3,2500;4,2500;5,2500")
        self.assertEqual(dpa.god_fight_hp_csv(percent=10000),
                         "1,10000;2,10000;3,10000;4,10000;5,10000")


class GodFightSocketTests(_ServerBase):
    def test_the_type_66_reply_carries_stage_and_hp(self):
        store = self._store(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        dpa.seed_overlay(store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        self._send(stream, writer, C2S_ENTER_BATTLE,
                   (False, 0, BATTLE_START_GOD_FIGHT_PVE_TYPE, 1, 2, 1, 0, 0, 0, 0, 0, 0, ""))
        packets = self._read_through(stream, buffer, reader, S2C_START_BATTLE)
        reply = [packet for packet in packets if packet.spec.protocol_id == S2C_START_BATTLE][-1]
        self.assertEqual(int(reply.fields[BATTLE_START_TYPE_POSITION - 1]),
                         BATTLE_START_GOD_FIGHT_PVE_TYPE)
        self.assertEqual(int(reply.fields[7]), 101)
        self.assertEqual(int(reply.fields[8]), 0)
        self.assertEqual(int(reply.fields[9]), 0)
        hp = reply.fields[17]
        self.assertEqual(hp.decode() if isinstance(hp, bytes) else hp,
                         dpa.god_fight_hp_csv())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
