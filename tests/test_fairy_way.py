"""TASK-0422 -- 仙途 pushes its info before the panel, starts, and rounds a reward.

Contract task_0420: le_tbFairyWayInfo starts as {} and openFairyWay compares dLayerId, so the
server has to PUSH S2C 239 (+246/+244) before the panel exists.  There is no grid -- three layers.
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


class _Handler:
    cls = tcp_stub.RevivalTCPHandler
    FAIRY_WAY_MAX_LAYER = 3
    FAIRY_WAY_START_RESETS = 3
    FAIRY_WAY_START_MAGIC = 5.0
    FAIRY_WAY_OPPONENT_NAME = "封妖幻影"
    FAIRY_WAY_OPPONENT_LEVEL = 60
    FAIRY_WAY_OPPONENT_FIGHT = 14134
    FAIRY_WAY_PREPARE_CARD_CFG = 1101
    FAIRY_WAY_PREPARE_CARD_LEVEL = 10
    FAIRY_WAY_PREPARE_CARD_COUNT = 5
    FAIRY_WAY_PREPARE_LEVEL = 60
    FAIRY_WAY_PREPARE_FIGHT = 4660
    FAIRY_WAY_GAIN_TYPE = 110
    FAIRY_WAY_LAYER_REWARDS = {
        1: ((7, 0, 3000),),
        2: ((7, 0, 3000),),
        3: ((112, 2000, 3), (7, 0, 6000)),
    }
    FAIRY_WAY_PLAYER_SOUL_CFG = 101
    FAIRY_WAY_PLAYER_SOUL_LEVEL = 60
    FAIRY_WAY_FALLBACK_CARD_CFG = 1101
    FAIRY_WAY_OPPONENT_ROWS = {
        1: {"level": 25, "soul": 2202,
            "cards": ((2202, 5), (2208, 5), (2209, 5), (2211, 5), (2224, 5)),
            "runes": (2012, 2002)},
        2: {"level": 25, "soul": 2202,
            "cards": ((2211, 5), (2224, 5), (2218, 5), (2224, 5), (2225, 5)),
            "runes": (2012, 2002)},
        3: {"level": 21, "soul": 2202,
            "cards": ((2208, 5), (2209, 5), (2211, 5), (2224, 5), (2218, 5)),
            "runes": (2012, 2002)},
    }

    def __init__(self):
        self.server = types.SimpleNamespace(
            role_store=_Store(),
            event_logger=types.SimpleNamespace(emit=lambda *a, **k: None))
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _push_fairy_way(self, connection_id, writer, session_state):
        return self.cls._push_fairy_way(self, connection_id, writer, session_state)

    def _fairy_way_read(self, session_state):
        return self.cls._fairy_way_read(self, session_state)

    def _fairy_way_write(self, session_state, state):
        return self.cls._fairy_way_write(self, session_state, state)

    def _fairy_way_push_match(self, connection_id, writer, session_state):
        return self.cls._fairy_way_push_match(self, connection_id, writer, session_state)

    def _fairy_way_battle_prepare(self, connection_id, writer, session_state):
        return self.cls._fairy_way_battle_prepare(self, connection_id, writer, session_state)

    def _fairy_way_layer(self, session_state):
        return self.cls._fairy_way_layer(self, session_state)

    def _fairy_way_player_cards(self, session_state):
        return self.cls._fairy_way_player_cards(self, session_state)

    def _fairy_way_player_side(self, session_state):
        return self.cls._fairy_way_player_side(self, session_state)

    def _fairy_way_opponent_side(self, layer, role_id=0.0, career=1):
        return self.cls._fairy_way_opponent_side(self, layer, role_id, career)

    def _send_fairy_way_side(self, *args, **kwargs):
        return self.cls._send_fairy_way_side(self, *args, **kwargs)

    def _fairy_way_prime(self, connection_id, writer, session_state):
        return self.cls._fairy_way_prime(self, connection_id, writer, session_state)

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _session():
    return {"role": types.SimpleNamespace(role_id=900000000001)}


class FairyWayTests(unittest.TestCase):
    def setUp(self):
        self.h = _Handler()
        self.cls = tcp_stub.RevivalTCPHandler

    def test_the_push_names_the_current_layer(self):
        session = _session()
        self.h.server.role_store.set_meta("fairy_way:900000000001", json.dumps({"layer": 2}))
        self.cls._push_fairy_way(self.h, "c1", None, session)
        names = [n for n, _ in self.h.sent]
        self.assertEqual(names, ["returnFairyWayInfo", "returnFairyWayBuyResetTimes",
                                 "returnFairyWayMaxLayer"])
        self.assertEqual(self.h.sent[0][1][0], 2, "layer id comes from the archive")

    def test_starting_releases_the_lock_and_repushes(self):
        self.cls._fairy_way_start(self.h, "c1", None, _session())
        self.assertEqual(self.h.sent[0], ("returnFairyWayStartResult", (1,)))
        self.assertIn("returnFairyWayInfo", [n for n, _ in self.h.sent])

    def test_the_prime_sends_a_usable_reset_before_any_click(self):
        # RUN-0079: the first click reached openFairyWay (the red point was removed)
        # but never sent C2S 46; the panic is the nil dLayerId comparison.  A prime
        # that says reset 0 would then hit startFairyWay's own 次数不足 gate.
        self.cls._fairy_way_prime(self.h, "c1", None, _session())
        fields = dict(self.h.sent)["returnFairyWayInfo"]
        self.assertEqual(fields[0], 0)
        self.assertEqual(fields[2], 3)
        self.assertIn("returnFairyWayMaxLayer", [n for n, _ in self.h.sent])

    def test_the_battle_prepare_fills_both_pvp_slots(self):
        # RUN-0080: S2C 89 said hasPrepareData=false, the client read no team, and
        # createPvpDataInc died on tbPrepareData nil.  The battle path reads
        # transferType 1 (S2C_BATTLE), not the type-3 match preview, and needs
        # BOTH lstData slots.
        self.cls._fairy_way_battle_prepare(self.h, "c1", None, _session())
        names = [n for n, _ in self.h.sent]
        begins = [f for n, f in self.h.sent if n == "battleTransferBegin"]
        self.assertEqual(len(begins), 2)
        self.assertEqual([f[0] for f in begins], [1, 1])
        self.assertEqual([f[2] for f in begins], [1, 2])
        self.assertEqual(names.count("battleTransferBase"), 2)
        cards = [f for n, f in self.h.sent if n == "battleTransferCard"]
        self.assertEqual(len(cards), 10)
        self.assertEqual(cards[0][2], 1101)   # player fallback (no archive in fake)
        self.assertEqual(cards[0][9], 10000)
        self.assertEqual(cards[5][2], 2202)   # opponent row 1, first card
        souls = [f for n, f in self.h.sent if n == "battleTransferSoul"]
        self.assertEqual([f[2] for f in souls], [101, 2202])
        shenfu = [f for n, f in self.h.sent if n == "battleTransferShenFu"]
        self.assertEqual([f[3] for f in shenfu], [2012, 2002])

    def test_the_match_push_carries_the_real_opponent_team(self):
        # TASK-0430: the 详情 view was blank because only 220/221 were sent.
        # The preview reads lstData[1], so the opponent row goes there under
        # transferType 3: soul + five cards + two runes, then 242.
        self.cls._fairy_way_push_match(self.h, "c1", None, _session())
        names = [n for n, _ in self.h.sent]
        self.assertEqual(names[0], "battleTransferBegin")
        self.assertEqual(self.h.sent[0][1], (3, 0.0, 1))
        self.assertEqual(names[-1], "returnfairyWayMatchEnd")
        soul = [f for n, f in self.h.sent if n == "battleTransferSoul"][0]
        self.assertEqual(soul[2], 2202)
        cards = [f for n, f in self.h.sent if n == "battleTransferCard"]
        self.assertEqual([f[2] for f in cards], [2202, 2208, 2209, 2211, 2224])
        shenfu = [f for n, f in self.h.sent if n == "battleTransferShenFu"]
        self.assertEqual([f[3] for f in shenfu], [2012, 2002])

    def test_starting_spends_one_reset_and_enters_layer_one(self):
        session = _session()
        self.cls._fairy_way_start(self.h, "c1", None, session)
        state = json.loads(self.h.server.role_store.get_meta("fairy_way:900000000001"))
        self.assertEqual(state["reset"], 2)
        self.assertEqual(state["layer"], 1)
        self.assertEqual(state["magic"], 5.0)
        self.assertEqual(self.h.sent[0], ("returnFairyWayStartResult", (1,)))

    def test_a_pending_layer_one_reward_grants_money_and_moves_to_layer_two(self):
        session = _session()
        self.h.server.role_store.set_meta(
            "fairy_way:900000000001",
            json.dumps({"layer": 1, "reward": 1, "reset": 2, "max": 1}))
        packet = types.SimpleNamespace(fields=(0,),
                                       spec=types.SimpleNamespace(name="dispatchReward"))
        self.cls._fairy_way_reward(self.h, "c1", None, packet, session)
        names = [n for n, _ in self.h.sent]
        self.assertEqual(names[:4], ["returnGainInfoStart", "returnGainInfo",
                                     "returnGainInfoEnd", "returnFairyWayRewardStatus"])
        gain = [f for n, f in self.h.sent if n == "returnGainInfo"][0]
        self.assertEqual(gain, (7, 0, "", 3000))
        self.assertEqual([f for n, f in self.h.sent
                          if n == "returnFairyWayRewardStatus"], [(2,)])
        state = json.loads(self.h.server.role_store.get_meta("fairy_way:900000000001"))
        self.assertEqual(state["reward"], 0)
        self.assertEqual(state["layer"], 2)
        self.assertEqual(state["max"], 1)

    def test_only_the_last_layer_asks_for_a_race(self):
        session = _session()
        self.h.server.role_store.set_meta(
            "fairy_way:900000000001",
            json.dumps({"layer": 3, "reward": 3, "reset": 2, "max": 3}))
        packet = types.SimpleNamespace(fields=(0,),
                                       spec=types.SimpleNamespace(name="dispatchReward"))
        self.cls._fairy_way_reward(self.h, "c1", None, packet, session)
        self.assertEqual(self.h.sent, [("returnFairyWayRewardStatus", (3,))])
        state = json.loads(self.h.server.role_store.get_meta("fairy_way:900000000001"))
        self.assertEqual(state["reward"], 3)

    def test_the_last_layer_race_pick_ends_the_run(self):
        session = _session()
        self.h.server.role_store.set_meta(
            "fairy_way:900000000001",
            json.dumps({"layer": 3, "reward": 3, "reset": 2, "max": 3}))
        packet = types.SimpleNamespace(fields=(2,),
                                       spec=types.SimpleNamespace(name="dispatchReward"))
        self.cls._fairy_way_reward(self.h, "c1", None, packet, session)
        gains = [f for n, f in self.h.sent if n == "returnGainInfo"]
        self.assertEqual(gains, [(112, 2000, "", 3), (7, 0, "", 6000)])
        self.assertEqual([f for n, f in self.h.sent
                          if n == "returnFairyWayRewardStatus"], [(2,)])
        state = json.loads(self.h.server.role_store.get_meta("fairy_way:900000000001"))
        self.assertEqual(state["layer"], 0)
        self.assertEqual(state["reward"], 0)
        self.assertEqual(state["max"], 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
