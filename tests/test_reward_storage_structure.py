"""TASK-0377 -- structure 13 adds the two reward tables and upgrades a 12 archive.

Contract task_0375: the artifact tower point (reward type 171) and a card 碎片 (type 9) had no
typed home, because role_wallet is frozen to money/yinpo/jinpo and a piece is neither a card
instance nor a special card. The step is purely additive, so a version-12 archive must keep every
row it already had.
"""

import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import role_store  # noqa: E402
from revival_stub.role_store import KIND_PERSISTENT, RoleStore  # noqa: E402


class RewardStorageStructureTests(unittest.TestCase):
    def test_the_structure_is_thirteen_with_a_twelve_step(self):
        self.assertEqual(role_store.SCHEMA_VERSION, 14)
        self.assertIn(12, role_store._MIGRATIONS)

    def test_a_fresh_archive_has_both_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RoleStore(Path(tmp) / "s.sqlite3", kind=KIND_PERSISTENT)
            try:
                names = {row[0] for row in store._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
                self.assertIn("role_values", names)
                self.assertIn("card_pieces", names)
            finally:
                store.close()

    def test_the_statements_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RoleStore(Path(tmp) / "s.sqlite3", kind=KIND_PERSISTENT)
            try:
                for statement in role_store._REWARD_STORAGE_STATEMENTS:
                    store._conn.execute(statement)
            finally:
                store.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()