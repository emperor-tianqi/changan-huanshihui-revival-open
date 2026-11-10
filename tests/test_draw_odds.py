"""The reconstructed draw probability model: rates, pity, tiers, persistence.

The model is reconstructed from the client help text (cfgHelpNote K67/K91) and
the Owner's D-147 90-pull ruling; it is not the original server RNG.  These
tests pin the documented rates, the advanced 10-pull rare guarantee, the
89->90 legend pity, the frozen tier tables and the persisted counter row.
"""

from __future__ import annotations

import random
import unittest
from collections import Counter

from revival_stub.draw_odds import (
    ADVANCED_RATES,
    CARDS_BY_ID,
    FOUR_STAR_CARD_IDS,
    HOT_SPOT_ONLY_IDS,
    LEGEND_CARD_IDS,
    LEGEND_PITY_DRAWS,
    NORMAL_CARD_IDS,
    NORMAL_RATES,
    RARE_CARD_IDS,
    RARE_GUARANTEE_DRAWS,
    TIER_DIGEST,
    DrawOddsState,
    draw_outcomes,
)
from revival_stub.role_store import RoleStore


class _FixedRng:
    """A deterministic stand-in for the production SystemRandom."""

    def __init__(self, roll: float, pick: int = 0) -> None:
        self.roll = roll
        self.pick = pick

    def random(self) -> float:
        return self.roll

    def choice(self, sequence):
        return sequence[self.pick % len(sequence)]


class TierTableTests(unittest.TestCase):
    def test_tiers_are_frozen_client_data(self) -> None:
        self.assertEqual(len(LEGEND_CARD_IDS), 21)
        self.assertEqual(len(RARE_CARD_IDS), 32)
        self.assertEqual(len(FOUR_STAR_CARD_IDS), 17)
        self.assertEqual(len(NORMAL_CARD_IDS), 50)
        self.assertEqual(
            TIER_DIGEST,
            "73ea09ca6400598926e60a43fad3234ceffdde5d3c8de6e9ccfcef873ab15aa5",
        )
        everything = set(LEGEND_CARD_IDS) | set(RARE_CARD_IDS) | set(
            FOUR_STAR_CARD_IDS
        ) | set(NORMAL_CARD_IDS)
        self.assertEqual(len(everything), 120)
        self.assertTrue(everything <= set(CARDS_BY_ID))
        # 4520/4521 are weight 0 in drawLegendCard; 4524+ are hot-spot-only.
        self.assertFalse(everything & set(HOT_SPOT_ONLY_IDS))


class RateTests(unittest.TestCase):
    SAMPLES = 200_000

    def _frequencies(self, pool: str) -> Counter:
        rng = random.Random(20261018)
        counts: Counter = Counter()
        for _ in range(self.SAMPLES):
            # A fresh state per draw keeps the advanced pity from firing, so
            # this measures the raw rate table rather than the guarantees.
            outcomes, _ = draw_outcomes(pool, 1, DrawOddsState(), rng)
            counts[outcomes[0].tier] += 1
        return counts

    def test_normal_rates(self) -> None:
        counts = self._frequencies("normal")
        self.assertAlmostEqual(
            counts["legend"] / self.SAMPLES, 0.0001, delta=0.00008
        )
        self.assertAlmostEqual(counts["rare"] / self.SAMPLES, 0.02, delta=0.003)
        self.assertAlmostEqual(
            counts["four_star"] / self.SAMPLES, 0.08, delta=0.005
        )
        self.assertEqual(
            counts["normal"], self.SAMPLES - counts["legend"] - counts["rare"]
            - counts["four_star"],
        )

    def test_advanced_rates(self) -> None:
        counts = self._frequencies("advanced")
        self.assertAlmostEqual(
            counts["legend"] / self.SAMPLES, 0.016, delta=0.002
        )
        self.assertAlmostEqual(counts["rare"] / self.SAMPLES, 0.11, delta=0.005)
        self.assertAlmostEqual(
            counts["four_star"] / self.SAMPLES, 0.08, delta=0.005
        )

    def test_limited_rates(self) -> None:
        counts = self._frequencies("limited")
        self.assertAlmostEqual(
            counts["legend"] / self.SAMPLES, 0.016, delta=0.002
        )
        self.assertAlmostEqual(counts["rare"] / self.SAMPLES, 0.10, delta=0.005)
        self.assertAlmostEqual(
            counts["four_star"] / self.SAMPLES, 0.08, delta=0.005
        )


class FeaturedLegendTests(unittest.TestCase):
    def test_third_legend_is_the_banner_card(self) -> None:
        banner_card = 4527
        outcomes, state = draw_outcomes(
            "limited",
            3,
            DrawOddsState(since_featured_legend=2),
            _FixedRng(0.0, pick=0),
            featured_card_id=banner_card,
        )
        # The state already holds two non-featured legends, so the first
        # legend of this batch is forced to the banner card and resets.
        self.assertEqual(outcomes[0].card_id, banner_card)
        self.assertEqual(outcomes[0].forced, "featured_legend")
        self.assertNotEqual(outcomes[1].card_id, banner_card)
        self.assertEqual(state.since_featured_legend, 2)

    def test_natural_featured_resets_and_others_count(self) -> None:
        banner_card = 4527
        outcomes, state = draw_outcomes(
            "limited",
            2,
            DrawOddsState(),
            _FixedRng(0.0, pick=0),
            featured_card_id=banner_card,
        )
        # The fixed picker never returns the featured card (it is appended at
        # the end of the legend tuple), so two legends count twice.
        self.assertNotEqual(outcomes[0].card_id, banner_card)
        self.assertEqual(state.since_featured_legend, 2)

    def test_banner_map_matches_the_six_later_cards(self) -> None:
        from revival_stub.draw_odds import HOT_SPOT_BANNERS

        self.assertEqual(HOT_SPOT_BANNERS[1015], 4526)  # 罗罗
        self.assertEqual(HOT_SPOT_BANNERS[1016], 4527)  # 三娘
        self.assertEqual(HOT_SPOT_BANNERS[1017], 4528)  # 江鱼儿
        self.assertEqual(HOT_SPOT_BANNERS[1019], 4530)  # 天狼星·汐风
        self.assertEqual(HOT_SPOT_BANNERS[1020], 4531)  # 炎煌


class PityTests(unittest.TestCase):
    def test_ten_pull_rare_guarantee(self) -> None:
        outcomes, state = draw_outcomes(
            "advanced", 10, DrawOddsState(), _FixedRng(0.999)
        )
        self.assertEqual([outcome.tier for outcome in outcomes[:9]], ["normal"] * 9)
        self.assertEqual(outcomes[9].tier, "rare")
        self.assertEqual(
            outcomes[9].forced, f"rare_guarantee_{RARE_GUARANTEE_DRAWS}"
        )
        self.assertEqual(state.since_rare, 0)
        self.assertEqual(state.since_legend, 10)

    def test_legend_pity_at_90(self) -> None:
        outcomes, state = draw_outcomes(
            "advanced",
            1,
            DrawOddsState(since_legend=LEGEND_PITY_DRAWS - 1, since_rare=4),
            _FixedRng(0.999),
        )
        self.assertEqual(outcomes[0].tier, "legend")
        self.assertEqual(
            outcomes[0].forced, f"legend_pity_{LEGEND_PITY_DRAWS}"
        )
        self.assertEqual(state.since_legend, 0)
        self.assertEqual(state.since_rare, 0)

    def test_legend_and_rare_reset_their_counters(self) -> None:
        outcomes, state = draw_outcomes(
            "advanced",
            1,
            DrawOddsState(since_legend=50, since_rare=7),
            _FixedRng(0.0),
        )
        self.assertEqual(outcomes[0].tier, "legend")
        self.assertEqual((state.since_legend, state.since_rare), (0, 0))
        outcomes, state = draw_outcomes(
            "advanced",
            1,
            DrawOddsState(since_legend=50, since_rare=7),
            _FixedRng(0.05),
        )
        self.assertEqual(outcomes[0].tier, "rare")
        self.assertEqual((state.since_legend, state.since_rare), (51, 0))

    def test_normal_pool_has_no_pity(self) -> None:
        outcomes, state = draw_outcomes(
            "normal", 12, DrawOddsState(since_legend=500, since_rare=500),
            _FixedRng(0.999),
        )
        self.assertTrue(all(outcome.tier == "normal" for outcome in outcomes))
        self.assertEqual(state.since_legend, 500)


class DrawPityStoreTests(unittest.TestCase):
    def test_roundtrip_and_upsert(self) -> None:
        store = RoleStore.in_memory()
        self.addCleanup(store.close)
        record, created = store.create_or_get(
            account_key="a" * 64,
            server_id=1,
            role_name="Pity",
            career=1,
            create_time=0,
            compatibility_profile_id="cn-0.1.20.1-revival-v15",
        )
        self.assertTrue(created)
        self.assertIsNone(
            store.draw_pity(account_key=record.account_key, pool="advanced")
        )
        first = store.record_draw_pity(
            record=record,
            pool="advanced",
            since_legend=12,
            since_rare=3,
            provenance="test",
        )
        self.assertEqual((first.since_legend, first.since_rare), (12, 3))
        second = store.record_draw_pity(
            record=record,
            pool="advanced",
            since_legend=13,
            since_rare=4,
            provenance="test",
        )
        self.assertEqual((second.since_legend, second.since_rare), (13, 4))
        self.assertEqual(
            store.draw_pity(account_key=record.account_key, pool="advanced")
            .since_legend,
            13,
        )

    def test_hotspot_roundtrip_and_upsert(self) -> None:
        store = RoleStore.in_memory()
        self.addCleanup(store.close)
        record, _ = store.create_or_get(
            account_key="b" * 64,
            server_id=1,
            role_name="Banner",
            career=1,
            create_time=0,
            compatibility_profile_id="cn-0.1.20.1-revival-v15",
        )
        self.assertIsNone(
            store.hotspot_draw_pity(
                account_key=record.account_key, hotspot_id=1016
            )
        )
        first = store.record_hotspot_draw_pity(
            record=record,
            hotspot_id=1016,
            since_featured_legend=1,
            provenance="test",
        )
        self.assertEqual(first.since_featured_legend, 1)
        second = store.record_hotspot_draw_pity(
            record=record,
            hotspot_id=1016,
            since_featured_legend=0,
            provenance="test",
        )
        self.assertEqual(second.since_featured_legend, 0)
        self.assertEqual(
            store.hotspot_draw_pity(
                account_key=record.account_key, hotspot_id=1016
            ).since_featured_legend,
            0,
        )


if __name__ == "__main__":
    unittest.main()
