"""TASK-0412 -- 挑战BOSS opens, lists six stage slots and pays a chest once.

Contract task_0406: C2S 664 fills the view (S2C 1350 with '&'-joined stage/hurt/kill rows), C2S 665
claims one of three chests (S2C 1352 = 1 on success) and the reward is cfgChallengeBossReward[1102].
"""

import json
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

    def adjust_role_wallet(self, **kwargs):
        return kwargs.get("delta", 0)

    def add_special_card(self, **kwargs):
        return 1


class _Handler:
    CHALLENGE_BOSS_ACT_ID = 1001
    CHALLENGE_BOSS_STAGES = (101, 102, 103, 201, 202, 203)
    CHALLENGE_BOSS_DURATION_MINUTES = 420
    CHALLENGE_BOSS_REWARD_ROWS = ((7, 0, 6000), (252, 0, 1), (257, 0, 8), (203, 0, 30))

    def __init__(self):
        self.server = types.SimpleNamespace(
            role_store=_Store(),
            event_logger=types.SimpleNamespace(emit=lambda *a, **k: None),
            now_unix=lambda: 1000,
        )
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _session():
    return {
        "role": types.SimpleNamespace(role_id=900000000001),
        "role_record": types.SimpleNamespace(account_key="acct"),
    }


class ChallengeBossTests(unittest.TestCase):
    def setUp(self):
        self.h = _Handler()
        self.cls = tcp_stub.RevivalTCPHandler

    def test_the_act_info_names_a_window_and_an_act(self):
        self.cls._challenge_boss_act_info(self.h, "c1", None)
        name, fields = self.h.sent[0]
        self.assertEqual(name, "returnChallengeBossActInfo")
        start, end, act = fields
        self.assertLess(start, end)
        self.assertEqual(act, self.cls.CHALLENGE_BOSS_ACT_ID)

    def test_the_view_lists_six_stage_slots(self):
        session = _session()
        self.cls._challenge_boss_data(self.h, "c1", None, session)
        name, fields = self.h.sent[0]
        self.assertEqual(name, "returnChallengeBossData")
        bosses = fields[4].split("&")
        self.assertEqual(len(bosses), 6)
        self.assertEqual([row.split(",")[0] for row in bosses],
                         ["101", "102", "103", "201", "202", "203"])
        rewards = fields[5].split("&")
        self.assertEqual(len(rewards), 3)

    def test_a_chest_pays_once(self):
        session = _session()
        self.cls._challenge_boss_data(self.h, "c1", None, session)
        packet = types.SimpleNamespace(fields=(1,),
                                       spec=types.SimpleNamespace(name="challengeBossGetReward"))
        self.cls._challenge_boss_claim(self.h, "c1", None, packet, session)
        names = [n for n, _ in self.h.sent]
        self.assertIn("returnGainInfoStart", names)
        self.assertEqual(self.h.sent[-1], ("returnChallengeBossGetReward", (1,)))
        # the second claim of the same chest is refused
        self.h.sent.clear()
        self.cls._challenge_boss_claim(self.h, "c1", None, packet, session)
        self.assertEqual(self.h.sent, [("returnChallengeBossGetReward", (0,))])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
