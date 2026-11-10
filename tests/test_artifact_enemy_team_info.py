"""TASK-0278 -- the artifact tower node tap is answered with S2C 1543 team="default".

That branch is the shortest legal route to a first artifact fight: the client opens
pkgUIArtifactDgnTeamInfo4Conf and its confirm sends C2S 69 (PVETYPE_GOD_FIGHT=66) directly.
C2S 752 has no lock partner, so a session without the overlay is simply answered with nothing.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_equip_fight_data_update import _ServerBase, FIXED_UNIX  # noqa: E402

from revival_stub import dev_play_artifact as dpa  # noqa: E402
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY  # noqa: E402

U_TAP = 752
U_TEAM_INFO = 1543
MONTH_SIGN_ACK = 1500


class EnemyTeamInfoPlanTests(unittest.TestCase):
    def test_registration_rows(self):
        self.assertEqual(C2S_REGISTRY.by_id(U_TAP).name, "artifactDgnEnemyTeamInfo")
        self.assertEqual(C2S_REGISTRY.by_id(U_TAP).fmt, "NNNN")
        self.assertEqual(S2C_REGISTRY.by_id(U_TEAM_INFO).name, "artifactDgnEnemyTeamInfo")
        self.assertEqual(S2C_REGISTRY.by_id(U_TEAM_INFO).fmt, "NNNNaa")

    def test_fields_are_the_contract_layout(self):
        fields = dpa.artifact_enemy_team_info_fields(2, 1)
        self.assertEqual(len(fields), 6)
        self.assertEqual(fields[0], 0)
        self.assertEqual(fields[1:4], (2, 1, 0))
        self.assertEqual(fields[4], "default")
        self.assertEqual(fields[5], "0")


class EnemyTeamInfoSocketTests(_ServerBase):
    def _overlay_login(self):
        store = self._store(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        dpa.seed_overlay(store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        return store, server, events, stream, buffer, reader, writer

    def test_overlay_answers_with_the_default_team_branch(self):
        store, server, events, stream, buffer, reader, writer = self._overlay_login()
        self._send(stream, writer, U_TAP, (1, 1, 2, 1))
        packets = self._read_through(stream, buffer, reader, U_TEAM_INFO)
        self.assertEqual([packet.spec.protocol_id for packet in packets], [U_TEAM_INFO])
        fields = packets[0].fields
        self.assertEqual(fields[0], 0)
        self.assertEqual((fields[1], fields[2], fields[3]), (2, 1, 0))
        self.assertEqual(fields[4].decode() if isinstance(fields[4], bytes) else fields[4],
                         "default")
        sent = self._wait_events(events, "tcp_artifact_dgn_enemy_team_info_sent", 1)[0]
        self.assertEqual(sent["branch"], "default")
        self.assertFalse(sent["historical"])
        self.assertFalse(sent["role_store_written"])
        self._assert_no_error(events)

    def test_without_the_overlay_nothing_is_sent_and_the_socket_stays(self):
        store = self._store("qa-changan-main")
        server, events = self.start_server(store, dev_play_artifact=False)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        self._send(stream, writer, U_TAP, (1, 1, 2, 1))
        self._send(stream, writer, 725, ())
        packets = self._read_through(stream, buffer, reader, MONTH_SIGN_ACK)
        ids = [packet.spec.protocol_id for packet in packets]
        self.assertNotIn(U_TEAM_INFO, ids)
        self.assertIn(MONTH_SIGN_ACK, ids)
        received = self._wait_events(
            events, "tcp_artifact_dgn_enemy_team_info_received", 1)[0]
        self.assertFalse(received["overlay_applied"])
        self._assert_no_error(events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
