"""Explicit Owner draw-test overlay.

This is **not** a compatibility profile and not a probability implementation.
It exists only when the Owner starts the Dev Server with ``--test-grant
owner-draw``; it mutates the in-memory S2C 27 ticket fields for the
``qa-changan-main`` play profile and lets the draw request handlers return a
deterministic card fixture.  Device Acceptance and every other run start
without the flag and see the unchanged v15 stream.

The grant is deliberately non-persistent for tickets: every login rebuilds the
252-value vector from the frozen profile, and the flag re-applies the counts.
Card instances created by the deterministic fixture are persisted in the
``card_instances`` table with a dedicated provenance and are replayed only while
this overlay is active.  The RNG/weight/pity authority remains unresolved
(D-147); no draw result here is a probability result.
"""

from __future__ import annotations

from dataclasses import dataclass


OWNER_DRAW_TEST_FLAG = "owner-draw"
OWNER_DRAW_TEST_PROFILE_ID = "qa-changan-main"
OWNER_DRAW_TEST_ORIGIN = "synthetic_test_grant"
DETERMINISTIC_FIXTURE_ORIGIN = "revival_local_deterministic_test_fixture"
DETERMINISTIC_FIXTURE_KIND = "deterministic_draw_result"

#: S2C 27 wire positions (1-based) and their client value names.
LOTTERY_TICKET_WIRE_POSITION = 19
LOTTERY_TICKET_ADVANCED_WIRE_POSITION = 37
LOTTERY_TICKET_VALUE_INDEX = 18
LOTTERY_TICKET_ADVANCED_VALUE_INDEX = 36

#: Reward-type ids recovered from pkgRewardConstMgr (the config/cg_vt keys).
LOTTERY_TICKET_REWARD_TYPE = 252
LOTTERY_TICKET_ADVANCED_REWARD_TYPE = 262

#: Card config 1101 (`葱油包`, star 1) and its shipped sfm ids.  This is a
#: fixture choice, not a recovered historical card and not a draw result.
FIXTURE_CARD_CFG_ID = 1101
FIXTURE_CARD_SFM_ID1 = 30479
FIXTURE_CARD_SFM_ID2 = 30261
FIXTURE_CARD_LEVEL = 1


@dataclass(frozen=True, slots=True)
class OwnerDrawTestGrant:
    """The bounded counts and fixture identity one Dev Server session may use."""

    lottery_ticket: int = 30
    lottery_ticket_advanced: int = 30
    fixture_id: str = "drawcard-result"

    def __post_init__(self) -> None:
        for name, value in (
            ("lottery_ticket", self.lottery_ticket),
            ("lottery_ticket_advanced", self.lottery_ticket_advanced),
        ):
            if not 0 <= int(value) <= 0xFFFFFFFF:
                raise ValueError(f"{name} must be a uint32, got {value!r}")
        if not self.fixture_id:
            raise ValueError("fixture_id must be non-empty")


def parse_test_grant(value: str | None) -> OwnerDrawTestGrant | None:
    """Parse ``--test-grant``: ``owner-draw`` or ``owner-draw:N/M``.

    ``N`` is the normal prayer-ticket count and ``M`` the advanced count.  The
    flag is the only way to enable the overlay; an unknown value is refused
    rather than silently ignored.
    """

    if value is None or value == "":
        return None
    if value == OWNER_DRAW_TEST_FLAG:
        return OwnerDrawTestGrant()
    prefix = OWNER_DRAW_TEST_FLAG + ":"
    if value.startswith(prefix):
        body = value[len(prefix):]
        parts = body.split("/")
        if len(parts) != 2:
            raise ValueError(
                f"--test-grant {value!r}: expected owner-draw:N/M"
            )
        try:
            normal, advanced = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError(
                f"--test-grant {value!r}: counts must be integers"
            ) from exc
        return OwnerDrawTestGrant(
            lottery_ticket=normal, lottery_ticket_advanced=advanced
        )
    raise ValueError(
        f"unknown --test-grant {value!r}; the only grant is "
        f"{OWNER_DRAW_TEST_FLAG} or {OWNER_DRAW_TEST_FLAG}:N/M"
    )


# ---------------------------------------------------------------- growth grant
#
# The 强化 window reads dog-food SPECIAL CARDS (``pkgSpecialCardDataMgr``, cfg
# ids 1-5).  Their real producers in CN 0.1.20.1 are chapter milestone / box
# rewards that this archive cannot reach yet (chapter 1 is 1/11).  So the Dev
# Server may seed a bounded, explicitly labelled food inventory, exactly like
# the ticket overlay: only for ``qa-changan-main``, only with the flag, and
# never on Device Acceptance.  The grant tops the stored count up to the target
# on each login; every row it writes carries ``synthetic_test_grant`` as its
# provenance, and the client's own strength chain does the real work.

GROWTH_TEST_FLAG = "owner-growth"
GROWTH_TEST_ORIGIN = "synthetic_test_grant"
GROWTH_TEST_FIXTURE_ID = "owner-growth-food"


@dataclass(frozen=True, slots=True)
class OwnerGrowthTestGrant:
    """Bounded dog-food counts (cfg id -> target count) for one Dev session."""

    food: dict[int, int]

    def __post_init__(self) -> None:
        cleaned: dict[int, int] = {}
        for cfg_id, count in self.food.items():
            numeric_cfg = int(cfg_id)
            numeric_count = int(count)
            if numeric_cfg not in (1, 2, 3, 4, 5):
                raise ValueError(
                    f"growth grant cfg id must be 1..5, got {cfg_id!r}"
                )
            if not 0 <= numeric_count <= 999:
                raise ValueError(
                    f"growth grant count must be 0..999, got {count!r}"
                )
            if numeric_count:
                cleaned[numeric_cfg] = numeric_count
        if not cleaned:
            raise ValueError("growth grant needs at least one non-zero count")
        object.__setattr__(self, "food", cleaned)


def parse_growth_grant(value: str | None) -> OwnerGrowthTestGrant | None:
    """Parse ``--growth-grant``: ``owner-growth:3=9,2=3`` or ``owner-growth``.

    The bare form seeds the deterministic small set below.  Counts are per
    SPECIAL-CARD config id (1..5 star dog food).
    """

    if value is None or value == "":
        return None
    if value == GROWTH_TEST_FLAG:
        return OwnerGrowthTestGrant(
            {3: 9, 2: 3, 1: 3}
        )
    prefix = GROWTH_TEST_FLAG + ":"
    if not value.startswith(prefix):
        raise ValueError(
            f"unknown --growth-grant {value!r}; expected "
            f"{GROWTH_TEST_FLAG} or {GROWTH_TEST_FLAG}:ID=N,ID=N"
        )
    body = value[len(prefix):]
    food: dict[int, int] = {}
    for part in body.split(","):
        if "=" not in part:
            raise ValueError(
                f"--growth-grant {value!r}: expected ID=N entries"
            )
        cfg_id, count = part.split("=", 1)
        try:
            food[int(cfg_id)] = int(count)
        except ValueError as exc:
            raise ValueError(
                f"--growth-grant {value!r}: entries must be integers"
            ) from exc
    return OwnerGrowthTestGrant(food)
