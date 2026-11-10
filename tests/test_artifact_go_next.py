"""TASK-0391 -- the next-layer answer must survive a finished tower.

The live crash was an UnboundLocalError: the handler read its two request ints inside the
"tower not complete" branch, so pressing the button on a CLEARED tower left `fields` unbound and
the event emit took the socket down. This test drives both states through the real handler.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import tcp_stub  # noqa: E402


class _Store:
    def __init__(self, cleared):
        self.meta = {f"artifact_dgn_clear:900000000001:1:{layer}:1": "1" for layer in cleared}

    def get_meta(self, key):
        return self.meta.get(key)

    def meta_with_prefix(self, prefix):
        return [(k, v) for k, v in self.meta.items() if k.startswith(prefix)]

    def delete_meta_prefix(self, prefix):
        keys = [k for k in self.meta if k.startswith(prefix)]
        for key in keys:
            del self.meta[key]
        return len(keys)

    def set_meta(self, key, value):
        self.meta[key] = value


class _Logger:
    def __init__(self):
        self.records = []

    def emit(self, event, **fields):
        self.records.append((event, fields))


class _Server:
    def __init__(self, cleared):
        self.role_store = _Store(cleared)
        self.event_logger = _Logger()


class _Handler:
    def __init__(self, cleared):
        self.server = _Server(cleared)
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))

    def _resend_tower_grid(self, connection_id, writer, session_state):
        self.sent.append(("grid", ()))


class GoNextTests(unittest.TestCase):
    def _press(self, cleared):
        h = _Handler(cleared)
        session = {"role": types.SimpleNamespace(role_id=900000000001)}
        packet = types.SimpleNamespace(
            spec=types.SimpleNamespace(name="artifactDgnGoNext"),
            fields=(1, 0), sequence=5)
        tcp_stub.RevivalTCPHandler._handle_artifact_dgn_go_next(h, "c1", None, packet, session)
        return h

    def test_a_finished_tower_restarts_without_raising(self):
        h = self._press(list(range(1, 13)))
        paths = [fields for name, fields in h.sent if name == "artifactDgnSendPath"]
        self.assertTrue(paths, "the handler sent no path")
        self.assertEqual(paths[0][0], 1, "a restarted run walks layer 1")
        self.assertEqual(paths[0][1:], (0,) * 25, "every other layer is closed")

    def test_an_unfinished_tower_keeps_its_progress(self):
        h = self._press([1, 2, 3])
        paths = [fields for name, fields in h.sent if name == "artifactDgnSendPath"]
        self.assertTrue(paths)
        self.assertEqual(paths[0][3], 1, "layer 4 stays walkable")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()