"""TASK-0369 -- the carriage offer stream, without a device.

Two live failures came from this one handler: an unregistered S2C name closed the socket, and a
packet reference inside a helper that never received it raised NameError, so the tile did
nothing at all. This test drives the handler with a fake transport and asserts the rows the
client needs: code 1, three code-2 rows keyed (sn-1)*3+1.., and a code-4 row whose key is the sn.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import tcp_stub  # noqa: E402


class _FakeStore:
    def card_offer_rows(self, role_id, limit=3):
        return [("inst-1", 1105, 10, 0), ("inst-2", 2212, 10, 0), ("inst-3", 3307, 10, 0)]


class _FakeLogger:
    def __init__(self):
        self.records = []

    def emit(self, event, **fields):
        self.records.append((event, fields))


class _FakeServer:
    def __init__(self):
        self.role_store = _FakeStore()
        self.event_logger = _FakeLogger()


class _Harness:
    def __init__(self):
        self.sent = []
        self.server = _FakeServer()
        self.client_address = ("127.0.0.1", 12345)

    def _resend_tower_grid(self, connection_id, writer, session_state):
        self.sent.append(("grid", ()))

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


class CarriageOfferTests(unittest.TestCase):
    def _offer(self, sn):
        h = _Harness()
        role = types.SimpleNamespace(role_id=900000000001)
        tcp_stub.RevivalTCPHandler._send_artifact_card_offer(h, "c1", None, role, sn)
        return h.sent

    def test_the_stream_is_start_rows_end(self):
        sent = self._offer(2)
        self.assertEqual(len(sent), 5)
        self.assertEqual(sent[0][1][0], 1)
        self.assertEqual(sent[-1][1][0], 4)

    def test_keys_are_grouped_by_sn_and_the_end_key_is_the_sn(self):
        sent = self._offer(2)
        keys = [fields[1] for _, fields in sent]
        self.assertEqual(tuple(keys[1:4]), (4, 5, 6))
        self.assertEqual(sent[-1][1][1], 2)

    def test_each_row_carries_a_real_card(self):
        sent = self._offer(1)
        cfg_ids = [fields[3] for _, fields in sent if fields[0] == 2]
        self.assertEqual(cfg_ids, [1105, 2212, 3307])
        previews = [fields[6] for _, fields in sent if fields[0] == 2]
        self.assertTrue(all(p != "0" for p in previews))


class _PickStore(_FakeStore):
    def __init__(self):
        self.added = []
        self.meta = {}

    def add_card_instance(self, role_id, account_key, cfg_id, **kwargs):
        self.added.append((role_id, cfg_id, kwargs))
        return f"artifact-{role_id}-{cfg_id}-001"

    def get_meta(self, key):
        return self.meta.get(key)

    def set_meta(self, key, value):
        self.meta[key] = value

    def meta_with_prefix(self, prefix):
        return [(k, v) for k, v in self.meta.items() if k.startswith(prefix)]



    def test_the_offer_handler_does_not_raise(self):
        """The handler must survive on its own: run it and assert an offer was emitted."""
        h = _Harness()
        session = {"role": types.SimpleNamespace(role_id=900000000001)}
        packet = types.SimpleNamespace(fields=(3, 1, 5, 3, 0),
                                       spec=types.SimpleNamespace(name="artifactDgnSelCard"),
                                       sequence=3)
        tcp_stub.RevivalTCPHandler._handle_artifact_dgn_sel_card(h, "c1", None, packet, session)
        self.assertTrue(h.sent, "the handler sent nothing -- an exception was swallowed")
        self.assertEqual(h.sent[0][0], "artifactDgnSelCard")
        self.assertEqual(session.get("artifact_card_node"), (5, 3))

class CarriagePickTests(unittest.TestCase):
    def test_the_pick_grants_the_card_and_moves_the_tower(self):
        h = _Harness()
        h.server.role_store = _PickStore()
        offered = [("inst-1", 1105, 10, 0), ("inst-2", 2212, 10, 0)]
        session = {
            "role": types.SimpleNamespace(role_id=900000000001),
            "role_record": types.SimpleNamespace(account_key="acct"),
            "artifact_card_offer": offered,
        }
        packet = types.SimpleNamespace(fields=(2, 2, 0), spec=types.SimpleNamespace(name="artifactDgnDoSelCard"), sequence=7)
        tcp_stub.RevivalTCPHandler._handle_artifact_dgn_do_sel_card(h, "c1", None, packet, session)
        # 1) the SECOND offered card is the one that was granted
        self.assertEqual(h.server.role_store.added[0][1], 2212)
        names = [name for name, _ in h.sent]
        self.assertIn("addCard", names)
        self.assertIn("artifactDgnDoSelCard", names)
        self.assertIn("grid", names, "the tower grid is resent so the node moves on")
        add_card = [f for n, f in h.sent if n == "addCard"][0]
        self.assertEqual(add_card[0], "artifact-900000000001-2212-001")
        self.assertEqual(add_card[1], 2212)
        # 2) the node is recorded so it stops coming back
        self.assertTrue(any(k.startswith("artifact_dgn_clear:") for k in h.server.role_store.meta))



if __name__ == "__main__":  # pragma: no cover
    unittest.main()