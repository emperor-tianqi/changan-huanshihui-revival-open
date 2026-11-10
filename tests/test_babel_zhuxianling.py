"""TASK-0322 -- a babel skip must cost 诛仙令, and the debit must survive a relogin.

RUN23 proved the skip worked but never debited the currency (free skipping). The count lives in the
archive meta table and is re-seeded into the login value vector.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import dev_play_babel as dpb  # noqa: E402
from revival_stub.role_store import RoleStore  # noqa: E402

NOW = 1793000000.0


class ZhuxianlingTests(unittest.TestCase):
    def setUp(self):
        self.store = RoleStore.in_memory(time_source=lambda: NOW)
        self.addCleanup(self.store.close)

    def test_the_default_is_the_dev_play_seed(self):
        self.assertEqual(dpb.zhuxianling(self.store), 5)

    def test_a_skip_debits_and_persists(self):
        before, after = dpb.spend_zhuxianling(self.store)
        self.assertEqual((before, after), (5, 3))
        self.assertEqual(self.store.get_meta(dpb.DEV_PLAY_BABEL_ZHUXIANLING_META_KEY), "3")
        self.assertEqual(dpb.zhuxianling(self.store), 3)

    def test_the_count_never_goes_negative(self):
        self.store.set_meta(dpb.DEV_PLAY_BABEL_ZHUXIANLING_META_KEY, "1")
        before, after = dpb.spend_zhuxianling(self.store, 5)
        self.assertEqual((before, after), (1, 0))

    def test_an_unreadable_value_falls_back_to_the_default(self):
        self.store.set_meta(dpb.DEV_PLAY_BABEL_ZHUXIANLING_META_KEY, "garbage")
        self.assertEqual(dpb.zhuxianling(self.store), 5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
