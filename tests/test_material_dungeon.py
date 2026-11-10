"""TASK-0418 -- 材料副本 enters, lists its 17 NPCs and answers a fight request with a seed.

Contract task_0417: C2S 330 -> S2C 595(act, code 1) + 596(act, count, state) + 594 per NPC;
C2S 329 -> S2C 592(act, npc, seed, count), and the client only fights when count > 0.
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
    DGN_FOOD_NPCS = tuple(range(1, 18))
    DGN_FOOD_ACT = 1
    DGN_FOOD_ATTEMPTS = 3
    DGN_FOOD_STATE_OPEN = 1

    def __init__(self):
        self.server = types.SimpleNamespace(
            role_store=_Store(),
            event_logger=types.SimpleNamespace(emit=lambda *a, **k: None))
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _dgn_food_attempts(self, session_state):
        return tcp_stub.RevivalTCPHandler._dgn_food_attempts(self, session_state)

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _session():
    return {"role": types.SimpleNamespace(role_id=900000000001)}


class MaterialDungeonTests(unittest.TestCase):
    def setUp(self):
        self.h = _Handler()
        self.cls = tcp_stub.RevivalTCPHandler

    def test_entering_lists_the_act_and_every_npc(self):
        packet = types.SimpleNamespace(fields=(1,), spec=types.SimpleNamespace(name="dgnEnter"))
        self.cls._dgn_food_enter(self.h, "c1", None, packet, _session())
        names = [n for n, _ in self.h.sent]
        self.assertEqual(names.count("dgnNpcInfo"), 17)
        self.assertEqual(self.h.sent[0], ("dgnEnter", (1, 1)))
        self.assertEqual(self.h.sent[1][0], "dgnState")
        self.assertEqual(self.h.sent[1][1][1], 3, "three attempts per cycle")

    def test_a_fight_request_answers_a_seed_and_the_remaining_count(self):
        packet = types.SimpleNamespace(fields=(1, 5, 0),
                                       spec=types.SimpleNamespace(name="dgnFoodStartFight"))
        self.cls._dgn_food_start_fight(self.h, "c1", None, packet, _session())
        name, fields = self.h.sent[0]
        self.assertEqual(name, "dgnFoodSynSeed")
        act, npc, seed, count = fields
        self.assertEqual((act, npc), (1, 5))
        self.assertGreater(seed, 0)
        self.assertEqual(count, 3)

    def test_a_spent_act_answers_seed_zero(self):
        session = _session()
        self.h.server.role_store.set_meta("dgn_food_used:900000000001", "3")
        packet = types.SimpleNamespace(fields=(1, 5, 0),
                                       spec=types.SimpleNamespace(name="dgnFoodStartFight"))
        self.cls._dgn_food_start_fight(self.h, "c1", None, packet, session)
        act, npc, seed, count = self.h.sent[0][1]
        self.assertEqual((seed, count), (0, 0), "the client only fights when count > 0")

    def test_each_fight_is_counted_once(self):
        session = _session()
        for _ in range(4):
            self.cls._dgn_food_record_fight(self.h, session)
        self.assertEqual(self.h._dgn_food_attempts(session), 0, "never below zero")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
