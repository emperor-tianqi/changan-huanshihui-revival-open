"""TASK-0425 -- 房间副本 pushes its config before it can be entered.

Contract task_0423: the chain is S2C-FIRST.  1359 reads le_tbCfg.strInstanceId, so 1362 must
arrive before it; 1363/1366 must precede their Process packets or the tables are nil.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import tcp_stub  # noqa: E402


class _Store:
    def __init__(self):
        self.meta = {}

    def get_meta(self, key):
        return self.meta.get(key)

    def set_meta(self, key, value):
        self.meta[key] = value


class _Handler:
    ROOM_DUNGEON_ROWS = 7
    ROOM_DUNGEON_SEALS = (11001, 11002, 11003, 11004)

    def __init__(self):
        self.server = types.SimpleNamespace(
            role_store=_Store(),
            event_logger=types.SimpleNamespace(emit=lambda *a, **k: None))
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _session():
    return {"role": types.SimpleNamespace(role_id=900000000001)}


class RoomDungeonTests(unittest.TestCase):
    def setUp(self):
        self.h = _Handler()
        self.cls = tcp_stub.RevivalTCPHandler

    def test_the_cfg_arrives_before_the_enter_ack(self):
        packet = types.SimpleNamespace(fields=("", 241),
                                       spec=types.SimpleNamespace(name="roomDungeonEnter"))
        self.cls._room_dungeon_enter(self.h, "c1", None, packet, _session())
        names = [n for n, _ in self.h.sent]
        self.assertEqual(names[0], "returnRoomDungeonCfg", "1362 must come first")
        self.assertEqual(names[-1], "roomDungeonEnter", "1359 is last")
        self.assertEqual(names[1], "returnRoomDungeonDataStart")
        self.assertEqual(names[-2], "returnRoomDungeonCompletedEnd")

    def test_the_data_lists_the_four_seals(self):
        packet = types.SimpleNamespace(fields=("", 241),
                                       spec=types.SimpleNamespace(name="roomDungeonEnter"))
        self.cls._room_dungeon_enter(self.h, "c1", None, packet, _session())
        rows = [f for n, f in self.h.sent if n == "returnRoomDungeonDataProcess"]
        self.assertEqual([row[1] for row in rows], [11001, 11002, 11003, 11004])

    def test_an_event_answer_marks_it_completed(self):
        packet = types.SimpleNamespace(fields=(5, 11001, 1001, 1001),
                                       spec=types.SimpleNamespace(name="roomDungeonEventDone"))
        self.cls._room_dungeon_event_done(self.h, "c1", None, packet, _session())
        names = [n for n, _ in self.h.sent]
        self.assertEqual(names, ["roomDungeonEventDone", "updateRoomDungeonCompleted"])
        self.assertEqual(self.h.sent[0][1][0], 1, "EVENT_STATUS_SUCCESS")
        self.assertEqual(self.h.sent[1][1], (5, 11001))

    def test_finishing_answers_1361(self):
        self.cls._room_dungeon_finish(self.h, "c1", None, _session())
        self.assertEqual(self.h.sent, [("roomDungeonFinish", (0,))])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
