"""TASK-0288 -- the artifact/uncharted dev-play overlays make S2C 89 echo the request type.

Without the echo the stock reply is the fixed chapter-map 86/3364, the client settles with 86, and
the TASK-0287 66/88 settlement pairing can never fire.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_equip_fight_data_update import _ServerBase, FIXED_UNIX  # noqa: E402

from revival_stub import dev_play_artifact as dpa  # noqa: E402
from revival_stub import dev_play_babel as dpb  # noqa: E402
from revival_stub.compatibility_profile import (  # noqa: E402
    BATTLE_START_GOD_FIGHT_PVE_TYPE,
    BATTLE_START_TYPE_POSITION,
    BATTLE_START_UNCHARTED_PVE_TYPE,
)

C2S_ENTER_BATTLE = 69
S2C_START_BATTLE = 89


def _enter_fields(pve_type: int, p1: int = 1, p2: int = 1, p3: int = 1, p4: int = 0):
    # yNNpppppppppa: isRetry, dSyncCount, pveOrPvpType, p1..p9, a1
    return (False, 0, pve_type, p1, p2, p3, p4, 0, 0, 0, 0, 0, "")


class BattleTypeEchoTests(_ServerBase):
    def _artifact_login(self):
        store = self._store(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        dpa.seed_overlay(store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        return store, server, events, stream, buffer, reader, writer

    def test_artifact_overlay_echoes_type_66(self):
        store, server, events, stream, buffer, reader, writer = self._artifact_login()
        self._send(stream, writer, C2S_ENTER_BATTLE,
                   _enter_fields(BATTLE_START_GOD_FIGHT_PVE_TYPE, 1, 2, 1))
        packets = self._read_through(stream, buffer, reader, S2C_START_BATTLE)
        reply = [packet for packet in packets if packet.spec.protocol_id == S2C_START_BATTLE][-1]
        self.assertEqual(int(reply.fields[BATTLE_START_TYPE_POSITION - 1]),
                         BATTLE_START_GOD_FIGHT_PVE_TYPE)
        echoed = self._wait_events(events, "tcp_battle_start_type_echoed", 1)[0]
        self.assertEqual(echoed["request_pve_type"], BATTLE_START_GOD_FIGHT_PVE_TYPE)
        self.assertEqual(echoed["echo_provenance"], "dev_play_overlay")

    def test_without_the_overlay_the_reply_stays_chapter_86(self):
        store = self._store("qa-changan-main")
        server, events = self.start_server(store, dev_play_artifact=False)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        self._send(stream, writer, C2S_ENTER_BATTLE,
                   _enter_fields(BATTLE_START_GOD_FIGHT_PVE_TYPE, 1, 2, 1))
        packets = self._read_through(stream, buffer, reader, S2C_START_BATTLE)
        reply = [packet for packet in packets if packet.spec.protocol_id == S2C_START_BATTLE][-1]
        self.assertEqual(int(reply.fields[BATTLE_START_TYPE_POSITION - 1]), 86)
        self.assertEqual(
            [event for event in events if event.get("event") == "tcp_battle_start_type_echoed"],
            [])


class UnchartedBattleIdentityTests(unittest.TestCase):
    """TASK-0336 -- a type-88 reply carries the server's cell/room/boss identity.

    RUN37: the client's own C2S 69 sends p8=0 (roomId) and p9=0 (bossId); echoing them made
    pkgPveEnterData:85 index a nil tbEventOptionCfg 735 times and the battle never initialised.
    """

    def test_the_identity_is_a_real_room_boss_pair(self):
        import sys
        sys.path.insert(0, str(TESTS))
        from revival_stub import dev_play_uncharted as dpu

        cell, room, boss = dpu.BATTLE_IDENTITY
        self.assertEqual(room, dpu.UNCHARTED_EXIT_ROOM)
        self.assertEqual((cell, room, boss), (2, 106, 1))
        self.assertGreaterEqual(boss, 1, "getCfgBossById(0) is nil in the client")

    def test_the_stub_fills_positions_5_8_9(self):
        source = (TESTS.parent / "revival_stub" / "tcp_stub.py").read_text(encoding="utf-8")
        self.assertIn("dev_play_uncharted.BATTLE_IDENTITY", source)
        # TASK-0337: the client reads p5/p8/p9 from tuple indices 11/14/15, NOT 4/7/8 --
        # writing at 4 clobbered the battle type and the client asserted (RUN38).
        from revival_stub import tcp_stub as ts

        self.assertEqual((ts.BATTLE_START_P5_INDEX, ts.BATTLE_START_P8_INDEX,
                          ts.BATTLE_START_P9_INDEX), (11, 14, 15))
        self.assertEqual(ts.BATTLE_START_TYPE_POSITION, 5)
        self.assertIn("echoed[BATTLE_START_P5_INDEX] = uncharted_cell", source)
        self.assertIn("echoed[BATTLE_START_P8_INDEX] = uncharted_room", source)
        self.assertIn("echoed[BATTLE_START_P9_INDEX] = uncharted_boss", source)
        self.assertNotIn("echoed[4] = uncharted_cell", source)


class UnchartedEchoPlanTests(unittest.TestCase):
    def test_the_88_echo_is_wired_to_the_babel_overlay(self):
        # The uncharted device path runs on dev-babel-main (TASK-0271/0272), so the 88 echo is
        # gated there. The socket-level 66 case above proves the mechanism; this pins the value
        # the stub switches on so a future rename cannot silently drop the uncharted echo.
        self.assertEqual(BATTLE_START_UNCHARTED_PVE_TYPE, 88)
        source = (TESTS.parent / "revival_stub" / "tcp_stub.py").read_text(encoding="utf-8")
        self.assertIn("BATTLE_START_UNCHARTED_PVE_TYPE", source)
        self.assertIn("dev_play_babel_overlay", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
