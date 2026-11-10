"""TASK-0415 -- 城市挑战 pushes its view data and pays an index reward once.

Contract task_0413: C2S 290 is never sent by the client, so the view fills only from a server push
of S2C 562 ("city,index,card" rows).  C2S 292 claims one index reward (S2C 564).
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

    def adjust_role_value(self, **kwargs):
        return 1


class _Handler:
    CITY_CHALLENGE_INDEXES = {1: 5, 2: 10}
    CITY_CHALLENGE_STAGES = {(1, 1): 101}
    CITY_CHALLENGE_INDEX_REWARD = {(1, 1): 20100, (1, 2): 20200}

    cls = tcp_stub.RevivalTCPHandler

    def __init__(self):
        self.server = types.SimpleNamespace(
            role_store=_Store(),
            event_logger=types.SimpleNamespace(emit=lambda *a, **k: None))
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _push_city_challenge(self, connection_id, writer, session_state):
        self.cls._push_city_challenge(self, connection_id, writer, session_state)

    def _city_challenge_state(self, session_state):
        role = session_state.get("role")
        raw = self.server.role_store.get_meta("city_challenge:%d" % role.role_id)
        return json.loads(raw) if raw else {}

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _session():
    return {
        "role": types.SimpleNamespace(role_id=900000000001),
        "role_record": types.SimpleNamespace(account_key="acct"),
    }


class CityChallengeTests(unittest.TestCase):
    def setUp(self):
        self.h = _Handler()
        self.cls = tcp_stub.RevivalTCPHandler

    def test_the_push_names_every_city_and_its_current_index(self):
        session = _session()
        self.cls._push_city_challenge(self.h, "c1", None, session)
        name, fields = self.h.sent[0]
        self.assertEqual(name, "returnCityChallengeData")
        rows = fields[0].split(";")
        self.assertEqual(rows, ["1,1,1", "2,1,1"])

    def test_a_cleared_index_moves_the_pushed_index(self):
        session = _session()
        self.h.server.role_store.set_meta(
            "city_challenge:900000000001", json.dumps({"city1": 1}))
        self.cls._push_city_challenge(self.h, "c1", None, session)
        self.assertEqual(self.h.sent[0][1][0].split(";")[0], "1,2,1")

    def test_an_index_reward_is_granted_once(self):
        session = _session()
        self.h.server.role_store.set_meta(
            "city_challenge:900000000001", json.dumps({"city1": 1}))
        packet = types.SimpleNamespace(fields=(1, 1),
                                       spec=types.SimpleNamespace(name="getIndexReward"))
        self.cls._city_challenge_claim(self.h, "c1", None, packet, session)
        rewards = [f for n, f in self.h.sent if n == "returnIndexReward"]
        self.assertEqual(rewards, [(1, 1, True)])
        self.h.sent.clear()
        self.cls._city_challenge_claim(self.h, "c1", None, packet, session)
        rewards = [f for n, f in self.h.sent if n == "returnIndexReward"]
        self.assertEqual(rewards, [(1, 1, False)], "the second claim is refused")

    def test_an_unreached_index_cannot_be_claimed(self):
        session = _session()
        packet = types.SimpleNamespace(fields=(1, 1),
                                       spec=types.SimpleNamespace(name="getIndexReward"))
        self.cls._city_challenge_claim(self.h, "c1", None, packet, session)
        rewards = [f for n, f in self.h.sent if n == "returnIndexReward"]
        self.assertEqual(rewards, [(1, 1, False)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
