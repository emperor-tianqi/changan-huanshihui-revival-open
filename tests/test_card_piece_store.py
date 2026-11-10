"""TASK-0395 -- a card 碎片 must survive a relogin.

card_pieces is structure 13; the login list is S2C 72/73/74 and one update is 75. Without the
list the piece exists in the archive but the bag shows nothing after a relaunch.
"""

import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub.role_store import KIND_PERSISTENT, RoleStore  # noqa: E402


class CardPieceStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RoleStore(Path(self._tmp.name) / "s.sqlite3", kind=KIND_PERSISTENT)
        self.addCleanup(self.store.close)

    def test_a_grant_is_readable_back(self):
        total = self.store.add_card_piece(account_key="acct", card_cfg_id=4438, count=5)
        self.assertEqual(total, 5)
        self.assertEqual(self.store.card_piece_counts(account_key="acct"), {4438: 5})

    def test_two_grants_accumulate(self):
        self.store.add_card_piece(account_key="acct", card_cfg_id=4438, count=5)
        self.store.add_card_piece(account_key="acct", card_cfg_id=4438, count=2)
        self.assertEqual(self.store.card_piece_counts(account_key="acct"), {4438: 7})

    def test_another_account_is_not_mixed_in(self):
        self.store.add_card_piece(account_key="acct", card_cfg_id=4438, count=5)
        self.assertEqual(self.store.card_piece_counts(account_key="other"), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()