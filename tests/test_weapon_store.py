"""TASK-0397 -- a 神器 instance is real archive state.

The shop's 41 weapon shelves name a concrete 6-star weapon through gdId, and a random-artifact box
must resolve into one; both end as a S2C 1625 instance, so the archive has to mint it.
"""

import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub.role_store import KIND_PERSISTENT, RoleStore  # noqa: E402


class GodWeaponStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RoleStore(Path(self._tmp.name) / "s.sqlite3", kind=KIND_PERSISTENT)
        self.addCleanup(self.store.close)

    def test_a_granted_weapon_is_readable(self):
        instance_id = self.store.add_god_weapon(
            account_key="acct", role_id=900000000001, cfg_id=1101, star=6)
        self.assertTrue(instance_id.startswith("gw-900000000001-1101-"))
        self.assertEqual(self.store.god_weapon_rows(account_key="acct"),
                         ((instance_id, 1101, 1, 0, 6),))

    def test_two_grants_get_distinct_instances(self):
        first = self.store.add_god_weapon(
            account_key="acct", role_id=900000000001, cfg_id=1101)
        second = self.store.add_god_weapon(
            account_key="acct", role_id=900000000001, cfg_id=1101)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.store.god_weapon_rows(account_key="acct")), 2)

    def test_the_structure_is_fourteen(self):
        from revival_stub import role_store
        self.assertEqual(role_store.SCHEMA_VERSION, 14)
        self.assertIn(13, role_store._MIGRATIONS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
