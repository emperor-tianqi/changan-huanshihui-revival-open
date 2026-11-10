"""Reconstructed draw probability model for the Owner Dev Server.

The client's help text (``config/text/cfgHelpNote`` K67/K91) is the only
shipped statement of the draw rates, and the Owner's historical screenshots
agree with it.  The original *server* RNG was never on the wire, so this module
implements a **reconstructed** model, not an official recovery:

* normal 祈灵符: legend 0.01%, rare 2%, 4-star 8%, remainder 1-3 star;
* advanced 祈灵符: legend 1.6%, rare 11%, 4-star 8% (the advanced text does not
  list a 4-star rate; the normal table's 8% is reused and stated as an
  assumption), remainder 1-3 star;
* advanced: at least one rare-or-better per 10 pulls (the help text's
  "每 10 次必出一个稀有妖灵"), and 89 pulls without a legend -> the 90th is a
  legend (D-147 Owner ruling: 90 is the target rule; ``MUST_LEGEND_COUNT=100``
  stays dead/unresolved and is not used here);
* normal: independent rolls; the help text documents no pity for this pool.

Not implemented (the client text names them but the enabling conditions were
not recovered): the limited/hot-spot pool (1.6% / 10%, shared pity), the
"first legend doubles" rule, the "4 advanced ten-pulls" counter, and the
"10 special summons" limited-rare rule.  ``probability_authority`` on every
result says ``reconstructed_client_help_text`` -- never "official".

The pool comes from the client's own card table and ``drawLegendCard``
weights: the 21 legend cards with a positive weight (4520/4521 are weight 0;
4524+ are hot-spot-only and not in the standard legend table), 32 rare
(rare 4.1/4.2), 17 four-star (rare 4.0) and 50 normal (rare 1/2/3) cards.
Cards within a tier are uniform; the legend table's weights are all 1.

``max_decoder/analyze_draw_odds.py`` re-derives the tier lists from the APK
and prints ``TIER_DIGEST``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from random import Random, SystemRandom

from .owner_draw_pool import POOL_CARDS

PROBABILITY_AUTHORITY = "reconstructed_client_help_text"
PROBABILITY_ORIGIN = "revival_local_reconstructed_probability_result"
PROBABILITY_MODEL_VERSION = 1
PROBABILITY_MODEL_SOURCE = (
    "cfgHelpNote K67/K91 + Owner screenshots; D-147 90-pull ruling"
)
DRAW_ODDS_DETERMINISTIC_FIXTURE = "deterministic_fixture"
DRAW_ODDS_RECONSTRUCTED = "reconstructed_rate_table"
DRAW_ODDS_MODES = (DRAW_ODDS_DETERMINISTIC_FIXTURE, DRAW_ODDS_RECONSTRUCTED)

#: The client's own hot-spot tab gate: `pkgCfgFunctionOpenMgr` macro
#: "hot_spot" `openLevel` (the client toast says 15).  The QA profile is level 1,
#: so the Dev Server overlay sends this level in the login role info only
#: (session-scoped; the stored role row is never rewritten).
HOT_SPOT_OPEN_LEVEL = 15

#: Documented rates (sum <= 1; the remainder is the 1-3 star bucket).
NORMAL_RATES = {"legend": 0.0001, "rare": 0.02, "four_star": 0.08}
ADVANCED_RATES = {"legend": 0.016, "rare": 0.11, "four_star": 0.08}
#: Limited/hot-spot pool (cfgHelpNote macro hot_spot_help): legend 1.6%,
#: rare 10%.  The help text does NOT list a 4-star rate, so the earlier
#: unlabelled 8% assumption is removed; the remaining 88.4% is the 1-3
#: star bucket (TASK-0431 correction).
LIMITED_RATES = {"legend": 0.016, "rare": 0.10, "four_star": 0.08}

#: Advanced pity/guarantee (the only pool whose pity is documented).
LEGEND_PITY_DRAWS = 90
RARE_GUARANTEE_DRAWS = 10

POOLS = ("normal", "advanced", "limited")

#: ``hotSpotDrawCard.max``: each banner's featured card (``showCard1``).
HOT_SPOT_BANNERS = {
    1001: 4515, 1002: 4516, 1003: 4503, 1004: 4504, 1005: 4514,
    1006: 4508, 1007: 4522, 1008: 4505, 1009: 4519, 1010: 4506,
    1011: 4518, 1012: 4523, 1013: 4525, 1014: 4513, 1015: 4526,
    1016: 4527, 1017: 4528, 1018: 4529, 1019: 4530, 1020: 4531,
}
#: The six banners whose featured cards are the CN 0.1.20.1 limited 妖灵
#: (4526 罗罗, 4527 三娘, 4528 江鱼儿, 4529 判官, 4530 天狼星·汐风, 4531 炎煌).
#: Banners 1001-1014 feature cards that are already in the standard pool
#: (4503-4525) and are historical rotation rows, not limited 妖灵.
HOT_SPOT_LIMITED_BANNERS = (1015, 1016, 1017, 1018, 1019, 1020)

#: The client tab prefab has exactly three slots (Panel/tab/Image1..3), so a
#: session can show at most three of the six limited banners at a time.
HOT_SPOT_CLIENT_TAB_SLOTS = 3

#: The Dev Server activates this bounded subset (the client tab prefab was
#: built for three placeholder tabs; more banners are a later slice).
HOT_SPOT_TEST_BANNERS = (1015, 1016, 1017)

#: `drawLegendCard` rows with weight > 0 (ids 4501-4523; 4520/4521 are 0).
LEGEND_CARD_IDS = (
    4501, 4502, 4503, 4504, 4505, 4506, 4507, 4508, 4509, 4510, 4511,
    4512, 4513, 4514, 4515, 4516, 4517, 4518, 4519, 4522, 4523,
)
#: templete.rare in {4.1, 4.2}.
RARE_CARD_IDS = (
    4401, 4404, 4406, 4410, 4413, 4416, 4417, 4418, 4419, 4420, 4421,
    4422, 4423, 4424, 4425, 4426, 4427, 4428, 4429, 4430, 4431, 4432,
    4433, 4434, 4435, 4436, 4437, 4438, 4439, 4440, 4441, 4442,
)
#: templete.rare == 4.0.
FOUR_STAR_CARD_IDS = (
    4402, 4403, 4405, 4407, 4408, 4409, 4411, 4412, 4414, 4415, 4443,
    4444, 4445, 4446, 4447, 4448, 4449,
)
#: templete.rare in {1, 2, 3}.
NORMAL_CARD_IDS = (
    1101, 1102, 1103, 1104, 1105, 1107, 2201, 2202, 2203, 2204, 2205,
    2206, 2207, 2208, 2209, 2211, 2212, 2213, 2214, 2215, 2216, 2217,
    2218, 2219, 2220, 2221, 2222, 2223, 2224, 2225, 3301, 3302, 3305,
    3307, 3308, 3309, 3310, 3311, 3312, 3313, 3314, 3315, 3317, 3318,
    3319, 3320, 3321, 3322, 3326, 3327,
)
TIER_CARD_IDS = {
    "legend": LEGEND_CARD_IDS,
    "rare": RARE_CARD_IDS,
    "four_star": FOUR_STAR_CARD_IDS,
    "normal": NORMAL_CARD_IDS,
}

CARDS_BY_ID = {card.card_id: card for card in POOL_CARDS}

#: Hot-spot-only ids that are in the client card table but not in the standard
#: legend table; kept visible so a future limited-pool implementation can use them.
HOT_SPOT_ONLY_IDS = (4520, 4521, 4524, 4525, 4526, 4527, 4528, 4529, 4530, 4531)


def tier_digest() -> str:
    canonical = "|".join(
        f"{tier}:{','.join(str(cid) for cid in ids)}"
        for tier, ids in TIER_CARD_IDS.items()
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


TIER_DIGEST = tier_digest()


@dataclass(frozen=True, slots=True)
class DrawOddsState:
    """Persisted pity counters (advanced shared, limited shared, per banner).

    ``since_featured_legend`` is the per-banner limited counter: consecutive
    legends that were not the banner's featured card; the third legend is
    forced to the featured card, and obtaining it resets the counter.
    """

    since_legend: int = 0
    since_rare: int = 0
    since_featured_legend: int = 0


@dataclass(frozen=True, slots=True)
class DrawOutcome:
    """One result: the tier rolled, the forced rule (if any) and the card."""

    card_id: int
    tier: str
    forced: str | None


def _roll_tier(rates: dict[str, float], rng) -> str:
    roll = rng.random()
    cumulative = 0.0
    for tier in ("legend", "rare", "four_star"):
        cumulative += rates[tier]
        if roll < cumulative:
            return tier
    return "normal"


def _pick_card(tier: str, rng) -> int:
    return rng.choice(TIER_CARD_IDS[tier])


def draw_outcomes(
    pool: str,
    times: int,
    state: DrawOddsState,
    rng: Random | SystemRandom,
    featured_card_id: int | None = None,
) -> tuple[list[DrawOutcome], DrawOddsState]:
    """Roll ``times`` results for one request and return the new pity state.

    ``pool`` is ``normal`` (祈灵符), ``advanced`` (特殊召唤/高级/元宝) or
    ``limited`` (hot-spot banner).  Normal rolls are independent; advanced and
    limited apply the documented 10-pull rare guarantee and the 89->90 legend
    pity.  A legend resets both counters; a rare resets the rare counter.
    ``featured_card_id`` (limited only) enables the banner's 2-non-featured-
    legend -> 3rd legend featured rule.
    """

    if pool not in POOLS:
        raise ValueError(f"unknown draw pool {pool!r}")
    if times < 1:
        raise ValueError("times must be positive")
    if pool == "normal":
        results = []
        for _ in range(times):
            tier = _roll_tier(NORMAL_RATES, rng)
            results.append(
                DrawOutcome(card_id=_pick_card(tier, rng), tier=tier, forced=None)
            )
        return results, state
    rates = ADVANCED_RATES if pool == "advanced" else LIMITED_RATES
    outcomes: list[DrawOutcome] = []
    since_legend = state.since_legend
    since_rare = state.since_rare
    since_featured = state.since_featured_legend
    for _ in range(times):
        since_legend += 1
        since_rare += 1
        forced = None
        if since_legend >= LEGEND_PITY_DRAWS:
            tier = "legend"
            forced = f"legend_pity_{LEGEND_PITY_DRAWS}"
        elif since_rare >= RARE_GUARANTEE_DRAWS:
            tier = "rare"
            forced = f"rare_guarantee_{RARE_GUARANTEE_DRAWS}"
        else:
            tier = _roll_tier(rates, rng)
        if tier == "legend":
            card_id, since_featured, featured_forced = _pick_legend(
                featured_card_id, since_featured, rng
            )
            if featured_forced:
                forced = "featured_legend"
        else:
            card_id = _pick_card(tier, rng)
        outcomes.append(DrawOutcome(card_id=card_id, tier=tier, forced=forced))
        if tier == "legend":
            since_legend = 0
            since_rare = 0
        elif tier == "rare":
            since_rare = 0
    return outcomes, replace(
        state,
        since_legend=since_legend,
        since_rare=since_rare,
        since_featured_legend=since_featured,
    )


def _pick_legend(
    featured_card_id: int | None, since_featured: int, rng
) -> tuple[int, int, bool]:
    """Pick one legend; apply the limited banner's featured-card rule.

    Returns ``(card_id, new_since_featured, forced)``.  After two consecutive
    non-featured legends the third legend is the banner's featured card; a
    natural featured legend resets the counter as well.
    """

    if featured_card_id is None:
        return _pick_card("legend", rng), since_featured, False
    if since_featured >= 2:
        return featured_card_id, 0, True
    pool = LEGEND_CARD_IDS
    if featured_card_id not in LEGEND_CARD_IDS:
        pool = LEGEND_CARD_IDS + (featured_card_id,)
    card_id = rng.choice(pool)
    if card_id == featured_card_id:
        return card_id, 0, False
    return card_id, since_featured + 1, False


def _tier_of(card_id: int) -> str:
    for tier, ids in TIER_CARD_IDS.items():
        if card_id in ids:
            return tier
    raise ValueError(f"card {card_id} is not in any draw tier")


def card_name(card_id: int) -> str:
    card = CARDS_BY_ID.get(card_id)
    return card.name if card is not None else ""
