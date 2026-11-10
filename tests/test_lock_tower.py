"""TASK-0416 -- 锁妖塔 opens, lists its floor ladder and remembers the pass floor.

Contract task_0414: C2S 377 answers S2C 655(code, bStart); the client then asks C2S 378 and the
view fills from S2C 656/659/660 (one gq row per floor).  The fight is the ordinary PVE pair with
pveOrPvpType 93; the server names the stage inside the floor span.
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
    cls = tcp_stub.RevivalTCPHandler
    LOCK_TOWER_MAX_FLOOR = 15
    LOCK_TOWER_LIFE = 5

    def __init__(self):
        self.server = types.SimpleNamespace(
            role_store=_Store(),
            event_logger=types.SimpleNamespace(emit=lambda *a, **k: None))
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _lock_tower_stage(self, floor):
        return self.cls._lock_tower_stage(self, floor)

    def _lock_tower_pass(self, session_state):
        return self.cls._lock_tower_pass(self, session_state)

    def _lock_tower_record_win(self, session_state, floor):
        return self.cls._lock_tower_record_win(self, session_state, floor)

    def _lock_tower_life(self, session_state):
        return self.cls._lock_tower_life(self, session_state)

    def _lock_tower_settlement(self, session_state, packet):
        return self.cls._lock_tower_settlement(self, session_state, packet)

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _session():
    return {"role": types.SimpleNamespace(role_id=900000000001)}


class LockTowerTests(unittest.TestCase):
    def setUp(self):
        self.h = _Handler()
        self.cls = tcp_stub.RevivalTCPHandler

    def test_entering_answers_start_true(self):
        self.cls._lock_tower_enter(self.h, "c1", None, _session())
        self.assertEqual(self.h.sent, [("enterLockTower", (0, True))])

    def test_the_ladder_names_one_stage_per_floor(self):
        self.cls._push_lock_tower_info(self.h, "c1", None, _session())
        names = [n for n, _ in self.h.sent]
        self.assertEqual(names[0], "returnTowerInfoStart")
        self.assertEqual(names[-1], "returnTowerInfoEnd")
        rows = [f for n, f in self.h.sent if n == "returnTowerGqInfo"]
        self.assertTrue(rows, "no floor rows")
        self.assertEqual(rows[0], (6, 1), "floor 1 owns stage 6")
        self.assertEqual(rows[1], (11, 2), "floor 2 owns stage 11")

    def test_a_win_moves_the_pass_floor_forward_only(self):
        session = _session()
        self.h._lock_tower_record_win(session, 3)
        self.assertEqual(self.h._lock_tower_pass(session), 3)
        self.h._lock_tower_record_win(session, 2)
        self.assertEqual(self.h._lock_tower_pass(session), 3)

    def test_the_base_data_sets_life_and_pass_before_the_view_ends(self):
        # RUN-0079: without 678 the client's dTowerLife stays 0 and its own enter
        # gate answers 剩余生命不足, so C2S 69 is never sent.
        self.cls._push_lock_tower_info(self.h, "c1", None, _session())
        names = [n for n, _ in self.h.sent]
        self.assertIn("returnLockTowerBaseData", names)
        base = [f for n, f in self.h.sent if n == "returnLockTowerBaseData"]
        self.assertEqual(base[0], (5, 0, False, 0))
        self.assertLess(names.index("returnLockTowerBaseData"), names.index("returnTowerInfoEnd"))

    def test_a_settled_type_93_victory_records_the_floor(self):
        session = _session()
        session["lock_tower_floor"] = 3
        fields = [93] + [0] * 12 + [6186266]
        packet = types.SimpleNamespace(fields=fields, spec=types.SimpleNamespace(name="battlePveGameOver"))
        self.h._lock_tower_settlement(session, packet)
        self.assertEqual(self.h._lock_tower_pass(session), 3)

    def test_a_loss_or_a_foreign_type_does_not_move_the_floor(self):
        session = _session()
        session["lock_tower_floor"] = 3
        loss = [93] + [0] * 12 + [6193484]
        self.h._lock_tower_settlement(session, types.SimpleNamespace(fields=loss))
        self.assertEqual(self.h._lock_tower_pass(session), 0)
        chapter = [86] + [0] * 12 + [6186266]
        self.h._lock_tower_settlement(session, types.SimpleNamespace(fields=chapter))
        self.assertEqual(self.h._lock_tower_pass(session), 0)

    def test_the_stage_span_is_five_wide(self):
        self.assertEqual(self.h._lock_tower_stage(1), 6)
        self.assertEqual(self.h._lock_tower_stage(2), 11)
        self.assertEqual(self.h._lock_tower_stage(15), 76)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
