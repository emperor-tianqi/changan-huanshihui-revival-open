"""TASK-0290 -- the boost ceiling is max_level(star), and the server's own tables accept it.

RUN7 wrote level 11 (len(LEVEL_EXP[star])) and every one of the 369 cards was rejected twice: the
client's getCardExpByCfgId nilled per card, and this server refused its own login push with
"card cfg 4526 level 11 advance 0 has no recovered cardFightRatio row".
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import dev_play_artifact as dpa  # noqa: E402
from revival_stub.card_growth import LEVEL_EXP, card_star_rare, max_level  # noqa: E402
from revival_stub.team_power import LEVEL_RATIO, card_fight  # noqa: E402


class BoostCeilingTests(unittest.TestCase):
    def test_ceiling_is_max_level_of_the_card_star(self):
        for cfg_id in (3312, 4516, 4517):
            star = int(card_star_rare(cfg_id)[0])
            self.assertEqual(dpa.legal_boost_level(cfg_id), max_level(star))

    def test_ceiling_is_never_one_past_the_table(self):
        # len(LEVEL_EXP[star]) == 11 is what RUN7 wrote; it must never be the ceiling again.
        for cfg_id in (3312, 4516):
            star = int(card_star_rare(cfg_id)[0])
            self.assertNotEqual(dpa.legal_boost_level(cfg_id), len(LEVEL_EXP[star]))
            self.assertLess(dpa.legal_boost_level(cfg_id), len(LEVEL_EXP[star]))

    def test_both_sides_have_a_row_for_the_ceiling(self):
        # LEVEL_RATIO is keyed 0..10 (cardFightRatio.levelN); LEVEL_EXP holds the eleven per-level
        # exp costs, so the level must be checked against the ratio keys, not the tuple members.
        for cfg_id in (3312, 4516):
            level = dpa.legal_boost_level(cfg_id)
            self.assertIn(level, LEVEL_RATIO)

    def test_advance_is_clamped_to_a_priceable_pair(self):
        # TASK-0301: RUN11 proved advance 4 has no cardFightRatio row and kills the main-city init.
        for cfg_id in (3312, 4516):
            advance = dpa.legal_advance_level(cfg_id)
            self.assertLessEqual(advance, 3)
            level = dpa.legal_boost_level(cfg_id)
            self.assertGreater(card_fight(cfg_id, level, advance), 0)
            with self.assertRaises(Exception):
                card_fight(cfg_id, level, advance + 1)

    def test_the_default_boost_level_is_none_meaning_the_ceiling(self):
        self.assertIsNone(dpa.DEV_PLAY_BOOST_LEVEL)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
