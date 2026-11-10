"""Recovered chapter-fight card rewards (CN 0.1.20.1).

The first chapter's fight node 101010 (奎木狼) names its reward in the shipped
client config:

* ``config/guankaMap/guankaMapFight.max`` row 101010 -> ``fightRewardId = 201``;
* ``config/guankaMap/guankaReward.max`` row 201 -> ``type1 = 3``
  (``pkgRewardConstMgr.TB_REWARD_TYPE.KAPAI``), ``itemId1 = 1103``,
  ``num1 = 1``;
* ``config/card/card.max`` row 1103 -> star 1, ``sfmId1 = 30104``,
  ``sfmId2 = 30144``.

This is the real reward producer the Owner asked for: a player who settles the
first chapter fight receives the card through the client's own ``addCard`` and
handbook-new-card channels, instead of a hand-issued synthetic grant.

The server now *can* decode the client's packed result: C2S 67 slot 12 is
``base + result * n`` under the recovered ``native.randvc`` stream
(``battle_rewards.py``).  This reward's historical rule is unchanged, though:
it is granted on the first observed settlement of the fight, exactly like the
chapter-fight clear record already is, and the event labels that basis.  The
``battle_rewards`` slice gates only the new card-EXP producer on the decoded
victory; changing this item reward's gate is a separate decision.  It remains
idempotent: the instance id is fixed, so a replay or a second settlement never
creates a second card.

`max_decoder/analyze_fight_rewards.py` re-derives every value from the APK and
prints `FIGHT_REWARD_DIGEST`; the frozen constants below are asserted against it
by the focused tests.

The module also freezes the *money* producer the same fights pay: the shipped
`guankaMonster.moneyLower/moneyUpper` columns of chapter 1's four FIGHT nodes
(`FIGHT_MONEY_REWARDS`, `FIGHT_MONEY_DIGEST`).  Every reachable `guankaReward`
row's own `money` column is 0; the monster row is where the fight's money drop
is shipped, and its two bounds are equal for all four fights, so the amount is
determined rather than sampled.  See the block above `FightMoneyReward` for the
provenance split (official shipped value vs. deterministic settlement rule).

The same module freezes the one *special-card* reward D-167's recovered type
map reaches: fight 102071 -> reward 501 slot 1 -> `specialCard` cfg id 1 (the
1★ dog food) x1 (`FIGHT_SPECIAL_CARD_REWARDS`, `FIGHT_SPECIAL_CARD_DIGEST`).
It shares the settlement transaction with the clear row, and it has no
battle-scene display packet of its own: the client sees it in the login
`returnSpecialCard*` snapshot (S2C 504/506/505).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class FightCardReward:
    """One `guankaMapFight.fightRewardId` -> `guankaReward` card row."""

    fight_id: int
    reward_id: int
    card_id: int
    count: int
    star: int
    sfm_id1: int
    sfm_id2: int
    level: int = 1

    @property
    def instance_id(self) -> str:
        """Fixed instance id: the same fight reward can never duplicate."""

        return f"revival-reward-{self.fight_id}-01"

    def canonical(self) -> str:
        return (
            f"{self.fight_id}:{self.reward_id}:{self.card_id}:{self.count}:"
            f"{self.star}:{self.sfm_id1}:{self.sfm_id2}:{self.level}"
        )


@dataclass(frozen=True)
class UnknownFightReward:
    """One fight whose `guankaReward` row has no recovered semantics.

    The value is deliberately *not* guessed.  The shipped row is recorded verbatim
    (fight -> reward -> slot -> type/item/count) so settlement can emit a named
    `UNKNOWN` event instead of silently dropping the reward or inventing a grant.
    """

    fight_id: int
    reward_id: int
    slot: int
    reward_type: int
    item_id: int
    count: int
    semantics: str = "UNKNOWN"
    source: str = ""

    def canonical(self) -> str:
        return (f"{self.fight_id}:{self.reward_id}:{self.slot}:"
                f"{self.reward_type}:{self.item_id}:{self.count}:{self.semantics}")


FIGHT_REWARD_SOURCE = (
    "config/guankaMap/guankaMapFight.max fightRewardId + "
    "config/guankaMap/guankaReward.max KAPAI row + config/card/card.max"
)

#: The first chapter's fight 101010 -> card 1103 (奎木狼).
FIGHT_REWARD_101010 = FightCardReward(
    fight_id=101010,
    reward_id=201,
    card_id=1103,
    count=1,
    star=1,
    sfm_id1=30104,
    sfm_id2=30144,
    level=1,
)

#: Chapter fight 101020 -> card 2209 (狐仙); same KAPAI rule, recovered from the
#: shipped `guankaMapFight` -> `guankaReward` -> `card` chain.
FIGHT_REWARD_101020 = FightCardReward(
    fight_id=101020,
    reward_id=202,
    card_id=2209,
    count=1,
    star=2,
    sfm_id1=30451,
    sfm_id2=30210,
    level=1,
)

#: Chapter fight 103041 -> card 3312 (琉璃盏).
FIGHT_REWARD_103041 = FightCardReward(
    fight_id=103041,
    reward_id=248,
    card_id=3312,
    count=1,
    star=3,
    sfm_id1=10092,
    sfm_id2=30277,
    level=1,
)

FIGHT_REWARDS: dict[int, FightCardReward] = {
    reward.fight_id: reward
    for reward in (FIGHT_REWARD_101010, FIGHT_REWARD_101020, FIGHT_REWARD_103041)
}

#: The unknown table is kept (with its digest) as the fail-closed home for any
#: future fight whose shipped reward row has no recovered semantics.  D-165 put
#: fight 102071's row 501 slot 1 here; D-167 recovered that row's type (8886 =
#: ``SPECIAL_CARD``), so it now lives in :data:`FIGHT_SPECIAL_CARD_REWARDS`
#: below and this table is empty.
UNKNOWN_FIGHT_REWARDS: dict[int, UnknownFightReward] = {}


def fight_reward_digest(rewards: dict[int, FightCardReward] | None = None) -> str:
    table = FIGHT_REWARDS if rewards is None else rewards
    canonical = "|".join(
        table[fight].canonical() for fight in sorted(table)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


FIGHT_REWARD_DIGEST = fight_reward_digest()


def unknown_reward_digest(rewards: dict[int, UnknownFightReward] | None = None) -> str:
    table = UNKNOWN_FIGHT_REWARDS if rewards is None else rewards
    canonical = "|".join(table[fight].canonical() for fight in sorted(table))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


UNKNOWN_REWARD_DIGEST = unknown_reward_digest()


def reward_for_fight(fight_id: int) -> FightCardReward | None:
    """The recovered card reward for one fight, or ``None`` when none is known."""

    return FIGHT_REWARDS.get(int(fight_id))


def unknown_reward_for_fight(fight_id: int) -> UnknownFightReward | None:
    """The recorded-but-unrecovered reward row for one fight, if any."""

    return UNKNOWN_FIGHT_REWARDS.get(int(fight_id))


# --------------------------------------------------- the special-card reward
#
# D-167 (TASK-0202) recovered the dispatcher meaning of reward type 8886 from
# the shipped client:
#
# * ``pkgRewardConstMgr.TB_REWARD_TYPE.SPECIAL_CARD = 8886``;
# * ``pkgBagOtherView.lua:108`` calls ``getIconInfo(SPECIAL_CARD, dCfgId,
#   dNum)``, so a ``guankaReward.itemId<N>`` in a SPECIAL_CARD slot is a
#   ``specialCard.max`` cfg id (``num<N>`` is the count);
# * ``specialCard.max`` rows 1..5 are the five star-graded dog foods, with
#   ``star = 1..5``; row 1 (the one 8886/item 1 names) is the 1★ food.
#
# Fight 102071's shipped ``fightRewardId`` is 501, and row 501's only non-empty
# slot is slot 1: ``type=8886, item=1, num=1``.  That is one ``specialCard``
# cfg id 1 (1★ dog food).  The value is official shipped config; the rule --
# a decoded victory pays it once, on the first recorded settlement, into the
# same archive transaction as the clear row -- is deterministic_recovered.

FIGHT_SPECIAL_CARD_SOURCE = (
    "config/guankaMap/guankaMapFight.max fightRewardId + "
    "config/guankaMap/guankaReward.max SPECIAL_CARD row + "
    "config/card/specialCard.max"
)

#: `pkgRewardConstMgr.TB_REWARD_TYPE.SPECIAL_CARD`.
FIGHT_SPECIAL_CARD_REWARD_TYPE = 8886

#: The shipped value's provenance (read from the client's own config tables).
FIGHT_SPECIAL_CARD_VALUE_PROVENANCE = "official_recovered"

#: The settlement rule's provenance (first decoded victory, same transaction).
FIGHT_SPECIAL_CARD_RULE_PROVENANCE = "deterministic_recovered"

#: The provenance recorded on the ``card_special_inventory`` row.
FIGHT_SPECIAL_CARD_ORIGIN = "revival_local_c2s67_chapter_fight_special_card"


@dataclass(frozen=True)
class FightSpecialCardReward:
    """One ``guankaReward`` SPECIAL_CARD slot and its ``specialCard`` row."""

    fight_id: int
    reward_id: int
    slot: int
    cfg_id: int
    count: int
    star: int
    value_provenance: str = FIGHT_SPECIAL_CARD_VALUE_PROVENANCE
    rule_provenance: str = FIGHT_SPECIAL_CARD_RULE_PROVENANCE

    def canonical(self) -> str:
        return (
            f"{self.fight_id}:{self.reward_id}:{self.slot}:"
            f"{self.cfg_id}:{self.count}:{self.star}"
        )


#: Chapter fight 102071 -> reward 501 slot 1 -> specialCard cfg id 1 (1★) ×1.
FIGHT_SPECIAL_CARD_102071 = FightSpecialCardReward(
    fight_id=102071,
    reward_id=501,
    slot=1,
    cfg_id=1,
    count=1,
    star=1,
)

FIGHT_SPECIAL_CARD_REWARDS: dict[int, FightSpecialCardReward] = {
    FIGHT_SPECIAL_CARD_102071.fight_id: FIGHT_SPECIAL_CARD_102071,
}


def special_card_reward_digest(
    rewards: dict[int, FightSpecialCardReward] | None = None,
) -> str:
    table = FIGHT_SPECIAL_CARD_REWARDS if rewards is None else rewards
    canonical = "|".join(
        table[fight].canonical() for fight in sorted(table)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


FIGHT_SPECIAL_CARD_DIGEST = special_card_reward_digest()


def special_card_reward_for_fight(
    fight_id: int,
) -> FightSpecialCardReward | None:
    """The recovered special-card (dog-food) reward of one fight, if any."""

    return FIGHT_SPECIAL_CARD_REWARDS.get(int(fight_id))


# ----------------------------------------------------------- the money reward
#
# The ``guankaReward`` rows above are item rewards; none of the rows reachable
# from the chapter-1 map carries a ``money`` column value (all are 0, re-derived
# by ``max_decoder/analyze_fight_rewards.py``).  The money a chapter fight pays
# is shipped on the fight's own monster row instead:
#
# * ``config/guankaMap/chapterMap/chapterMap0.max`` rows 2/4/7/16 are the
#   chapter's FIGHT nodes (``type = 10``, ``explore ~= 0``) and name fights
#   101010 / 101020 / 101031 / 101051;
# * ``config/guankaMap/guankaMonster.max`` rows for those ids carry
#   ``moneyLower`` / ``moneyUpper`` = 40/40, 40/40, 60/60, 60/60.
#
# ``pkgPveEnterData.initData`` stores exactly that row as ``tbGqCfgData`` when
# the battle starts (chapter-map branch, source lines 9-10), and the client
# consumes its siblings ``cardExp`` / ``exp`` at game over.  The shipped table
# itself uses one value for both bounds on every chapter-1 fight, so the drop
# amount is determined by the client's own table rather than sampled here.  A
# row whose bounds differ would be a random drop whose distribution this
# archive has not recovered; :attr:`FightMoneyReward.amount` is then ``None``
# and settlement must refuse it by name instead of inventing a bound.
#
# The client never reads these two columns (a whole-APK string scan finds no
# consumer), so the *amount* is official shipped config while the *rule* --
# a decoded chapter-map victory pays that row once, on the first recorded
# settlement -- is the deterministic_recovered label carried by the events.
# The wallet is written through the recovered S2C 23 ``returnOneValue_p(7, v)``
# channel (``pkgValueDef.lua`` line 71 maps reward type MONEY to value name
# ``money`` with VVT_p; ``pkgCgValueMgr.setValue`` writes ``roleInfo.v.money``).

FIGHT_MONEY_SOURCE = (
    "config/guankaMap/guankaMonster.max moneyLower/moneyUpper for the "
    "chapterMap0 FIGHT nodes"
)

#: The shipped value's provenance (read from the client's own config table).
FIGHT_MONEY_VALUE_PROVENANCE = "official_recovered"

#: The settlement rule's provenance: lower == upper is a single shipped value.
FIGHT_MONEY_RULE_PROVENANCE = "deterministic_recovered"

#: The provenance recorded on the wallet row the settlement writes.
FIGHT_MONEY_ORIGIN = "revival_local_c2s67_chapter_fight_money"


@dataclass(frozen=True)
class FightMoneyReward:
    """One chapter-map fight's shipped ``moneyLower``/``moneyUpper`` row."""

    fight_id: int
    money_lower: int
    money_upper: int

    @property
    def deterministic(self) -> bool:
        """True only when the shipped range is a single value."""

        return self.money_lower == self.money_upper

    @property
    def amount(self) -> int | None:
        """The determined money amount, or ``None`` for an unrecovered range."""

        return self.money_lower if self.deterministic else None

    def canonical(self) -> str:
        return f"{self.fight_id}:{self.money_lower}:{self.money_upper}"


#: Chapter 1's four FIGHT nodes, in map-index order (2, 4, 7, 16).  Only the
#: fights this archive can actually reach are frozen; any other fight is a
#: named ``fight_not_in_recovered_money_table`` refusal, never a guess.
FIGHT_MONEY_101010 = FightMoneyReward(101010, 40, 40)
FIGHT_MONEY_101020 = FightMoneyReward(101020, 40, 40)
FIGHT_MONEY_101031 = FightMoneyReward(101031, 60, 60)
FIGHT_MONEY_101051 = FightMoneyReward(101051, 60, 60)

FIGHT_MONEY_REWARDS: dict[int, FightMoneyReward] = {
    reward.fight_id: reward
    for reward in (
        FIGHT_MONEY_101010,
        FIGHT_MONEY_101020,
        FIGHT_MONEY_101031,
        FIGHT_MONEY_101051,
    )
}


def fight_money_digest(
    rewards: dict[int, FightMoneyReward] | None = None,
) -> str:
    table = FIGHT_MONEY_REWARDS if rewards is None else rewards
    canonical = "|".join(table[fight].canonical() for fight in sorted(table))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


FIGHT_MONEY_DIGEST = fight_money_digest()


def money_reward_for_fight(fight_id: int) -> FightMoneyReward | None:
    """The frozen money row of one reachable chapter fight, if any."""

    return FIGHT_MONEY_REWARDS.get(int(fight_id))
