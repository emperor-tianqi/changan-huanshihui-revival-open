"""TASK-0410 -- a relic node offers three relics, the pick resolves by INDEX, and the
owned relic rides S2C 89 field 17 as its zhufu id.

Contract task_0403: C2S 748 carries the 1-based row index of the offer, never the relic id, so the
server has to remember the order it sent and resolve against it.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import dev_play_artifact as dpa  # noqa: E402
from revival_stub import tcp_stub  # noqa: E402


class _Store:
    def __init__(self):
        self.meta = {}

    def get_meta(self, key):
        return self.meta.get(key)

    def set_meta(self, key, value):
        self.meta[key] = value

    def meta_with_prefix(self, prefix):
        return [(k, v) for k, v in self.meta.items() if k.startswith(prefix)]


class _Handler:
    def __init__(self):
        self.server = types.SimpleNamespace(
            role_store=_Store(),
            event_logger=types.SimpleNamespace(emit=lambda *a, **k: None),
        )
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _session():
    return {"role": types.SimpleNamespace(role_id=900000000001)}


class RelicTests(unittest.TestCase):
    def setUp(self):
        self.helper = _Handler()
        self.cls = tcp_stub.RevivalTCPHandler

    def test_a_relic_node_offers_three_pool_relics(self):
        session = _session()
        self.cls._offer_artifact_relic(self.helper, "c1", None, session, 3, 1)
        self.assertEqual([n for n, _ in self.helper.sent], ["artifactDgnSelYiwu"] * 4)
        self.assertEqual(self.helper.sent[0][1][0], 1, "code 1 resets the list")
        offered = [fields[1] for _n, fields in self.helper.sent[1:]]
        self.assertEqual(len(offered), dpa.ARTIFACT_RELIC_OFFER_SIZE)
        for relic_id in offered:
            self.assertIn(relic_id, dpa.ARTIFACT_RELIC_POOL)
            self.assertIn(relic_id, dpa.ARTIFACT_RELIC_ZHUFU)

    def test_the_pick_is_an_index_not_an_id(self):
        session = _session()
        self.cls._offer_artifact_relic(self.helper, "c1", None, session, 3, 1)
        offered = [fields[1] for _n, fields in self.helper.sent[1:]]
        packet = types.SimpleNamespace(
            fields=(2, 0), spec=types.SimpleNamespace(name="artifactDgnDoSelYiwu"))
        picked = self.cls._resolve_relic_pick(self.helper, session, packet)
        self.assertEqual(picked, offered[1], "index 2 selects the second offered relic")
        self.assertEqual(self.cls._owned_relic_ids(self.helper, session), (picked,))

    def test_an_index_outside_the_offer_grants_nothing(self):
        session = _session()
        self.cls._offer_artifact_relic(self.helper, "c1", None, session, 3, 1)
        packet = types.SimpleNamespace(
            fields=(9, 0), spec=types.SimpleNamespace(name="artifactDgnDoSelYiwu"))
        self.assertEqual(self.cls._resolve_relic_pick(self.helper, session, packet), 0)
        self.assertEqual(self.cls._owned_relic_ids(self.helper, session), ())

    def test_a_non_relic_node_offers_nothing(self):
        session = _session()
        self.cls._offer_artifact_relic(self.helper, "c1", None, session, 2, 1)
        self.assertEqual(self.helper.sent, [])

    def test_the_battle_reply_carries_the_zhufu_id(self):
        self.assertEqual(dpa.relic_zhufu_csv((1,)), "48296")
        self.assertIn(48296, [dpa.ARTIFACT_RELIC_ZHUFU[rid] for rid in dpa.ARTIFACT_RELIC_POOL])


    def test_the_relic_string_lands_in_a_string_slot(self):
        """S2C 89 is "NyyNNNNpppppppppaaa": index 16 is the first string (a1 = tbParams.s1)."""

        from revival_stub.registry import S2C_REGISTRY
        spec = S2C_REGISTRY.by_id(89)
        self.assertEqual(spec.fmt, "NyyNNNNpppppppppaaa")
        self.assertEqual(spec.fmt[16], "a", "index 16 must be a string field")
        self.assertEqual(spec.fmt[17], "a", "index 17 carries the HP table")

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
