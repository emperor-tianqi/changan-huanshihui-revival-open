"""NPC 悬赏榜 empty index (TASK-0447)."""

from __future__ import annotations

import unittest

from tests.test_card_advance import _Server


class BountyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server()
        self.addCleanup(self.server.close)
        self.key, self.record = self.server.seed_role()

    def test_open_daily_task_sends_the_task_snapshot(self) -> None:
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 397, ())
            packets = self.server.read_until(stream, buffer, reader, 703)
        finally:
            stream.close()
        ids = [packet.spec.protocol_id for packet in packets]
        self.assertIn(702, ids)
        self.assertIn(701, ids)
        self.assertEqual(ids.count(699), 8)
        self.assertEqual(packets[-1].fields, (0,))

    def test_tutorial_npc_task_completes_on_accept_in_dev_session(self) -> None:
        self.server.server.dev_play_gacha = True
        self.server.server.dev_play_gacha_profile_matches = True
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 294, (1001,))
            packets = self.server.read_until(stream, buffer, reader, 566)
        finally:
            stream.close()
        self.assertEqual(packets[-1].fields, (1001, 21))

    def test_open_bounty_returns_an_empty_index(self) -> None:
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.server.send(stream, writer, 267, ())
            packets = self.server.read_until(stream, buffer, reader, 509)
        finally:
            stream.close()
        ids = [packet.spec.protocol_id for packet in packets]
        self.assertIn(507, ids)
        self.assertIn(509, ids)
        self.assertNotIn(508, ids)


if __name__ == "__main__":
    unittest.main()
