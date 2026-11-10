"""TASK-0373 -- granting a card must actually land in the archive.

The carriage pick first failed with a NameError, then with "27 values for 28 columns", then with
the fixture_kind CHECK. Each one was only visible on the device. This test drives the real
insert against a throw-away store so the whole statement is exercised locally.
"""

import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub.role_store import KIND_PERSISTENT, RoleStore  # noqa: E402


class AddCardInstanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RoleStore(Path(self._tmp.name) / "store.sqlite3", kind=KIND_PERSISTENT)
        self.addCleanup(self.store.close)

    def test_the_row_is_written_with_the_given_values(self):
        instance_id = self.store.add_card_instance(
            900000000001, "acct", 4526, level=10, exp=7)
        self.assertTrue(instance_id.startswith("artifact-900000000001-4526-"))
        row = self.store._conn.execute(
            "SELECT account_key, role_id, card_cfg_id, level, exp, origin "
            "FROM card_instances WHERE instance_id = ?", (instance_id,)).fetchone()
        self.assertEqual(tuple(row)[:5], ("acct", 900000000001, 4526, 10, 7))
        self.assertEqual(row[5], "artifact_dgn_clear")

    def test_two_grants_of_the_same_card_get_distinct_ids(self):
        first = self.store.add_card_instance(900000000001, "acct", 4526)
        second = self.store.add_card_instance(900000000001, "acct", 4526)
        self.assertNotEqual(first, second)
        count = self.store._conn.execute(
            "SELECT COUNT(*) FROM card_instances WHERE card_cfg_id = 4526").fetchone()[0]
        self.assertEqual(count, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()