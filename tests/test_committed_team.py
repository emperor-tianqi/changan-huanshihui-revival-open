"""TASK-0389 -- a committed team must land in the archive and beat the synthetic pick.

Two live failures came from this one path: the helper referenced an undefined connection_id (its
failure event raised NameError, so nothing was written), and the entry echo overrode the committed
CSV with our own synthetic choice, so the fight never used the player's roster.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import dev_play_artifact, tcp_stub  # noqa: E402


class _Store:
    def __init__(self):
        self.meta = {}
        self.boosted = None

    def set_meta(self, key, value):
        self.meta[key] = value

    def get_meta(self, key):
        return self.meta.get(key)

    def adjust_role_wallet(self, **kwargs):
        return 0


class _Logger:
    def __init__(self):
        self.records = []

    def emit(self, event, **fields):
        self.records.append((event, fields))


class _Server:
    def __init__(self):
        self.role_store = _Store()
        self.event_logger = _Logger()


class _Handler:
    def __init__(self):
        self.server = _Server()
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _send_team_snapshot(self, connection_id, writer, role, session_state):
        self.sent.append(("returnTeamInfo", ()))

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


class CommittedTeamTests(unittest.TestCase):
    def test_the_commit_is_written_to_the_archive(self):
        h = _Handler()
        written = {}
        original = dev_play_artifact.boost_team
        dev_play_artifact.boost_team = lambda store, account, ids, **kw: written.update(
            {"account": account, "ids": list(ids)})
        try:
            session = {
                "role": types.SimpleNamespace(role_id=900000000001),
                "role_record": types.SimpleNamespace(account_key="acct"),
            }
            packet = types.SimpleNamespace(
                spec=types.SimpleNamespace(name="artifactDgnSetTeam"),
                fields=("101,artifact-1,revival-x,0,0",),
            )
            tcp_stub.RevivalTCPHandler._remember_artifact_team(h, "c1", None, packet, session)
        finally:
            dev_play_artifact.boost_team = original
        self.assertEqual(written.get("ids"), ["artifact-1", "revival-x"])
        self.assertEqual(session["artifact_team_csv"], "101,artifact-1,revival-x,0,0")
        self.assertEqual(h.server.role_store.meta["artifact_team_csv:900000000001"],
                         "101,artifact-1,revival-x,0,0")
        self.assertFalse([r for r in h.server.event_logger.records
                          if r[0] == "tcp_artifact_dgn_team_preset_failed"],
                         "the write path must not raise")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()