"""Recovered chapter-map battle rewards and the game-over result code (CN 0.1.20.1).

Two facts this module freezes, both re-derived from the shipped client:

1. **Card EXP per chapter fight.**  ``pkgBattleUIGameOver.show1`` does not read a
   field from ``tbCards``; it reads the local ``tbGqCfgData`` that
   ``pkgPveEnterData.initData`` built from
   ``pkgCfgGuankaMapMgr.getFightMonsterByIndex(fightId)``.  For the chapter-map
   branch (``pveOrPvpType == 86``) the row is ``guankaMonster[fightId]`` and the
   two columns are:

   * ``cardExp`` -- awarded to every non-protagonist card in the player's group;
   * ``exp``     -- awarded to the protagonist (not a card instance, so this
     module exposes it but the settlement slice does not apply it).

   Both values are flat: no star, level, team-size or reward-table scaling.
   ``guankaReward``/``guankaMapFight`` carry item rewards only and no EXP
   column.  ``max_decoder/analyze_battle_rewards.py`` re-derives this table from
   ``guankaMonster.max`` and prints :data:`CARD_EXP_DIGEST`; it also re-derives
   the game-over constants below from the client's own ranges.

2. **The C2S 67 win/loss code.**  ``pkgSmartGameOverResult.encryptResult``
   builds nineteen values from ``ass.random`` and folds the result code into
   slot 12::

       tbResult[12] = getEncryptResult0()[12] + result * n

   ``ass.random`` is ``native.randvc``: a 32-bit MSVC LCG (``state = state *
   0x343FD + 0x269EC3``), whose raw value is ``(state >> 16) & 0x7FFF`` and
   whose range mapping is ``lo + floor((raw % 32767) / 32767.0 * (hi-lo+1))``.
   ``getGameOverResultHandler`` is not defined in the shipped Lua corpus, but
   the S2C 89 ``dRawRandomSeed`` is fixed by profile v11 (3364), and two
   independent device victories with different fights produce the same slot-12
   value, so the handler is deterministic for this archive.  Solving the LCG
   from the ten slot values the client sent (RUN-0049's fight 101010 and the
   2026-10-18 tavern fight 400011) gives one observable state: the seed
   :data:`GAME_OVER_RESULT_SEED`, the multiplier :data:`GAME_OVER_RESULT_N` and
   the base :data:`GAME_OVER_RESULT_BASE_SLOT_12`.  (Bit 31 of the LCG state is
   invisible to the 15-bit output, so two seeds differing only there reproduce
   the same packet; both yield the same ``n``/``base[12]``, and the frozen seed
   is the representative the analyzer checks.)

   A real **loss** sample exists and confirms the mapping: manual session
   ``20261017-2212`` (``netGameOver: 2 0 4``) sent slot 12 ``6193484``, which is
   ``base + 2 * n`` -- ``GAMEOVERRESULT_LOSE``.  A victory sample sent
   ``6186266``, which is ``base + 1 * n`` -- ``GAMEOVERRESULT_WIN``.

   Scope: these constants are tied to the archive's fixed S2C 89
   ``dRawRandomSeed``
   (``compatibility_profile.BATTLE_START_RAW_RANDOM_SEED``, re-asserted by the
   focused tests).  A different seed invalidates them and requires
   re-derivation; the decoder then fails closed (``None``) rather than
   guessing.
"""

from __future__ import annotations

import hashlib
import math


#: ``guankaMonster`` rows: ``(fightId, cardExp, exp)``, sorted by fight id.
#: Every shipped row is frozen, including the rows whose EXP is 0, so an
#: unknown fight is a refusal rather than a guess.  The analyzer prints the
#: same digest.
_ROWS: tuple[tuple[int, int, int], ...] = (
    (101010, 5, 5),
    (101020, 5, 5),
    (101031, 5, 20),
    (101051, 10, 35),
    (101080, 5, 30),
    (101111, 5, 30),
    (102010, 10, 0),
    (102031, 10, 40),
    (102040, 10, 40),
    (102041, 15, 45),
    (102060, 10, 40),
    (102071, 15, 65),
    (102080, 10, 0),
    (102100, 10, 40),
    (103010, 15, 40),
    (103041, 20, 65),
    (103051, 20, 65),
    (103060, 15, 40),
    (103061, 20, 0),
    (103070, 15, 40),
    (103080, 15, 60),
    (103090, 15, 40),
    (103091, 20, 0),
    (104010, 20, 40),
    (104020, 20, 40),
    (104030, 20, 40),
    (104040, 20, 40),
    (104050, 20, 40),
    (104060, 20, 40),
    (104070, 20, 40),
    (104080, 25, 60),
    (104091, 25, 60),
    (104101, 25, 60),
    (105010, 20, 40),
    (105020, 20, 40),
    (105030, 20, 40),
    (105040, 20, 40),
    (105050, 20, 40),
    (105060, 20, 40),
    (105070, 20, 40),
    (105080, 25, 60),
    (105091, 25, 60),
    (105101, 25, 60),
    (106010, 20, 40),
    (106020, 20, 40),
    (106030, 20, 40),
    (106040, 20, 40),
    (106050, 20, 40),
    (106060, 20, 40),
    (106070, 20, 40),
    (106080, 25, 60),
    (106091, 25, 60),
    (106101, 25, 60),
    (107010, 20, 40),
    (107020, 20, 40),
    (107030, 20, 40),
    (107040, 20, 40),
    (107050, 20, 40),
    (107060, 20, 40),
    (107070, 20, 40),
    (107080, 25, 60),
    (107091, 25, 60),
    (107101, 25, 60),
    (108010, 20, 40),
    (108020, 20, 40),
    (108030, 20, 40),
    (108040, 20, 40),
    (108050, 20, 40),
    (108060, 20, 40),
    (108070, 20, 40),
    (108080, 25, 60),
    (108091, 25, 60),
    (108101, 25, 60),
    (109010, 20, 40),
    (109020, 20, 40),
    (109030, 20, 40),
    (109040, 20, 40),
    (109050, 20, 40),
    (109060, 20, 40),
    (109070, 20, 40),
    (109080, 25, 60),
    (109091, 25, 60),
    (109101, 25, 60),
    (110010, 20, 40),
    (110020, 20, 40),
    (110030, 20, 40),
    (110040, 20, 40),
    (110050, 20, 40),
    (110060, 20, 40),
    (110070, 20, 40),
    (110080, 25, 60),
    (110091, 25, 60),
    (110101, 25, 60),
    (111010, 20, 40),
    (111020, 20, 40),
    (111030, 20, 40),
    (111040, 20, 40),
    (111050, 20, 40),
    (111060, 20, 40),
    (111070, 20, 40),
    (111080, 25, 60),
    (111091, 25, 60),
    (111101, 25, 60),
    (112010, 20, 40),
    (112020, 20, 40),
    (112030, 20, 40),
    (112040, 20, 40),
    (112050, 20, 40),
    (112060, 20, 40),
    (112070, 20, 40),
    (112080, 20, 40),
    (112090, 20, 40),
    (112101, 25, 60),
    (112111, 25, 60),
    (112121, 25, 60),
    (112131, 25, 60),
    (112141, 25, 60),
    (113010, 20, 40),
    (113020, 20, 40),
    (113030, 20, 40),
    (113040, 20, 40),
    (113050, 20, 40),
    (113060, 20, 40),
    (113070, 20, 40),
    (113080, 20, 40),
    (113090, 20, 40),
    (113101, 25, 60),
    (113111, 25, 60),
    (113121, 25, 60),
    (113131, 25, 60),
    (113141, 25, 60),
    (114010, 20, 40),
    (114020, 20, 40),
    (114030, 20, 40),
    (114040, 20, 40),
    (114050, 20, 40),
    (114060, 20, 40),
    (114070, 20, 40),
    (114080, 20, 40),
    (114090, 20, 40),
    (114101, 25, 60),
    (114111, 25, 60),
    (114121, 25, 60),
    (114131, 25, 60),
    (114141, 25, 60),
    (200000, 0, 0),
    (300000, 0, 0),
    (331901, 0, 0),
    (331902, 0, 0),
    (400011, 10, 0),
    (400021, 20, 0),
    (400031, 25, 0),
    (400041, 0, 0),
    (400051, 0, 0),
    (400061, 0, 0),
    (400071, 0, 0),
    (400081, 0, 0),
    (400091, 0, 0),
    (400101, 0, 0),
    (400111, 0, 0),
    (400121, 0, 0),
    (400131, 0, 0),
    (400141, 0, 0),
    (502040, 0, 0),
    (502060, 0, 0),
    (502080, 0, 0),
    (503010, 0, 0),
    (503060, 0, 0),
    (503080, 0, 0),
    (504050, 0, 0),
    (504060, 0, 0),
    (504070, 0, 0),
    (505050, 0, 0),
    (505060, 0, 0),
    (505070, 0, 0),
    (506050, 0, 0),
    (506060, 0, 0),
    (506070, 0, 0),
    (507050, 0, 0),
    (507060, 0, 0),
    (507070, 0, 0),
    (508050, 0, 0),
    (508060, 0, 0),
    (508070, 0, 0),
    (509050, 0, 0),
    (509060, 0, 0),
    (509070, 0, 0),
    (510050, 0, 0),
    (510060, 0, 0),
    (510070, 0, 0),
    (511050, 0, 0),
    (511060, 0, 0),
    (511070, 0, 0),
    (603001, 20, 40),
    (603002, 20, 40),
    (603003, 20, 40),
    (603004, 20, 40),
    (603005, 20, 40),
    (603006, 20, 40),
    (603007, 20, 40),
    (603008, 20, 40),
    (603009, 20, 40),
    (603010, 20, 40),
    (603011, 20, 40),
    (603012, 20, 40),
    (603013, 20, 40),
    (603014, 20, 40),
    (603015, 20, 40),
    (603016, 20, 40),
    (603017, 20, 40),
    (603018, 20, 40),
    (603019, 20, 40),
    (603020, 20, 40),
    (603021, 20, 40),
    (603022, 20, 40),
    (603031, 25, 60),
    (603032, 25, 60),
    (603033, 25, 60),
    (603034, 25, 60),
    (603035, 25, 60),
    (603036, 25, 60),
    (603037, 25, 60),
    (603038, 25, 60),
    (603039, 25, 60),
    (603040, 25, 60),
    (603061, 25, 60),
    (603062, 25, 60),
    (603063, 25, 60),
    (603064, 25, 60),
    (603065, 25, 60),
    (603081, 30, 80),
    (603082, 30, 80),
    (603083, 30, 80),
    (603084, 30, 80),
    (603085, 30, 80),
    (603086, 30, 80),
    (603087, 30, 80),
    (603088, 30, 80),
    (603089, 30, 80),
    (603101, 25, 60),
    (603102, 25, 60),
    (603103, 25, 60),
    (603201, 30, 80),
    (603202, 30, 80),
    (603203, 30, 80),
    (603204, 30, 80),
    (603205, 30, 80),
    (603206, 30, 80),
    (603207, 30, 80),
    (603208, 30, 80),
    (603209, 30, 80),
    (603210, 30, 80),
    (603221, 30, 80),
    (603222, 30, 80),
    (603223, 30, 80),
    (603224, 30, 80),
    (603225, 30, 80),
    (603226, 30, 80),
    (603227, 30, 80),
    (603228, 30, 80),
    (603229, 30, 80),
    (603230, 30, 80),
    (703301, 0, 0),
    (703302, 0, 0),
    (703305, 0, 0),
    (703307, 0, 0),
    (703308, 0, 0),
    (703309, 0, 0),
    (703310, 0, 0),
    (703311, 0, 0),
    (703312, 0, 0),
    (703313, 0, 0),
    (703314, 0, 0),
    (703315, 0, 0),
    (703317, 0, 0),
    (703318, 0, 0),
    (703319, 0, 0),
    (703320, 0, 0),
    (703321, 0, 0),
    (703322, 0, 0),
    (703326, 0, 0),
    (703327, 0, 0),
    (704401, 0, 0),
    (704402, 0, 0),
    (704403, 0, 0),
    (704404, 0, 0),
    (704405, 0, 0),
    (704406, 0, 0),
    (704407, 0, 0),
    (704408, 0, 0),
    (704409, 0, 0),
    (704410, 0, 0),
    (704411, 0, 0),
    (704412, 0, 0),
    (704413, 0, 0),
    (704414, 0, 0),
    (704415, 0, 0),
    (704416, 0, 0),
    (704417, 0, 0),
    (704418, 0, 0),
    (704419, 0, 0),
    (704420, 0, 0),
    (704421, 0, 0),
    (704422, 0, 0),
    (704423, 0, 0),
    (704424, 0, 0),
    (704425, 0, 0),
    (704426, 0, 0),
    (704427, 0, 0),
    (704428, 0, 0),
    (704429, 0, 0),
    (704430, 0, 0),
    (704431, 0, 0),
    (704432, 0, 0),
    (704433, 0, 0),
    (704434, 0, 0),
    (704435, 0, 0),
    (704436, 0, 0),
    (704437, 0, 0),
    (704438, 0, 0),
    (704439, 0, 0),
    (704440, 0, 0),
    (704441, 0, 0),
    (704442, 0, 0),
    (704443, 0, 0),
    (704444, 0, 0),
    (704445, 0, 0),
    (704446, 0, 0),
    (704447, 0, 0),
    (704448, 0, 0),
    (704449, 0, 0),
    (705501, 0, 0),
    (705502, 0, 0),
    (705503, 0, 0),
    (705504, 0, 0),
    (705505, 0, 0),
    (705506, 0, 0),
    (705507, 0, 0),
    (705508, 0, 0),
    (705509, 0, 0),
    (705510, 0, 0),
    (705511, 0, 0),
    (705512, 0, 0),
    (705513, 0, 0),
    (705514, 0, 0),
    (705515, 0, 0),
    (705516, 0, 0),
    (705517, 0, 0),
    (705518, 0, 0),
    (705519, 0, 0),
    (705520, 0, 0),
    (705523, 0, 0),
    (705525, 0, 0),
    (705526, 0, 0),
    (705527, 0, 0),
    (705528, 0, 0),
    (705529, 0, 0),
    (705530, 0, 0),
    (705531, 0, 0),
    (801010, 0, 0),
    (801011, 0, 0),
    (801020, 0, 0),
    (802011, 0, 0),
    (802021, 0, 0),
    (803011, 0, 0),
    (803021, 0, 0),
    (803031, 0, 0),
    (804011, 0, 0),
    (804021, 0, 0),
    (804031, 0, 0),
    (805011, 0, 0),
    (805021, 0, 0),
    (805031, 0, 0),
    (806011, 0, 0),
    (806021, 0, 0),
    (806031, 0, 0),
    (807011, 0, 0),
    (807021, 0, 0),
    (807031, 0, 0),
    (808011, 0, 0),
    (808021, 0, 0),
    (808031, 0, 0),
    (808041, 0, 0),
    (808051, 0, 0),
    (809011, 0, 0),
    (809021, 0, 0),
    (809031, 0, 0),
    (809041, 0, 0),
    (809051, 0, 0),
    (901010, 5, 20),
    (901011, 0, 0),
    (901012, 0, 0),
    (901013, 0, 0),
    (901014, 0, 0),
    (901015, 0, 0),
    (901016, 0, 0),
    (901017, 0, 0),
    (901018, 0, 0),
    (901019, 0, 0),
    (901020, 0, 0),
    (901021, 0, 0),
    (901022, 0, 0),
    (901023, 0, 0),
    (901024, 0, 0),
    (901025, 0, 0),
    (901026, 0, 0),
    (901030, 10, 40),
    (901040, 10, 40),
    (901051, 15, 60),
    (902010, 0, 0),
)

CARD_EXP_BY_FIGHT: dict[int, int] = {row[0]: row[1] for row in _ROWS}
PROTAGONIST_EXP_BY_FIGHT: dict[int, int] = {row[0]: row[2] for row in _ROWS}


def card_exp_digest(rows: tuple[tuple[int, int, int], ...] | None = None) -> str:
    """Canonical sha256 over ``fightId:cardExp:exp`` (the analyzer's digest)."""

    table = _ROWS if rows is None else rows
    canonical = "|".join(f"{fight}:{card_exp}:{player_exp}"
                         for fight, card_exp, player_exp in table)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Re-derived by ``max_decoder/analyze_battle_rewards.py`` from
#: ``config/guankaMap/guankaMonster.max``; frozen so a client update cannot move
#: a value silently.
CARD_EXP_DIGEST = "eb30f2446d2a299dbab2f2a56850c0cb74a84eb40dd2703ba5d7cb62d9ce1fa8"


def card_exp_for_fight(fight_id: int) -> int | None:
    """The shipped ``guankaMonster[fight_id].cardExp``, or ``None`` if unknown."""

    try:
        return CARD_EXP_BY_FIGHT[int(fight_id)]
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# C2S 67 result code
#
# `pkgSmartGameOverResult.encryptResult` folds `tbGameOverResult.result` into
# slot 12 (`tbResult[12] = base[12] + result * n`).  The generator is
# `native.randvc` (see the module docstring).  The three constants below were
# solved from ten slot values sent by two device victories (RUN-0049, fight
# 101010, and the 2026-10-18 tavern fight 400011); the seed reproduces every
# one of them.  A recorded loss (`20261017-2212`, `netGameOver: 2 0 4`) sent
# `base + 2*n`, so the result code is decoded, not guessed.
#
# The archive's S2C 89 `dRawRandomSeed` is fixed by the v11 policy.  These
# constants are valid only for that seed; the analyzer asserts the tie.

#: The representative value the analyzer recovers for the client's
#: `getGameOverResultHandler` state (`0x80D27CE2`).  Bit 31 of the LCG state is
#: invisible to the 15-bit output, so `0x00D27CE2` reproduces the same packet;
#: both give the same `n` and `base[12]`, which are the values that decode the
#: result.
GAME_OVER_RESULT_SEED = 2161278178

#: `ass.random(handle, 10, 9876)`'s first draw.
GAME_OVER_RESULT_N = 7218

#: `getEncryptResult0()[12]` -- the unshifted base the result is folded onto.
GAME_OVER_RESULT_BASE_SLOT_12 = 6179048

#: `pkgBattleConst` GAMEOVERRESULT_* constants (recovered from the shipped
#: chunk; `GAMEOVERRESULT_WIN` is the only value this server awards on).
GAMEOVERRESULT_NONE = 0
GAMEOVERRESULT_WIN = 1
GAMEOVERRESULT_LOSE = 2
GAMEOVERRESULT_BOTH_LOSE = 3
GAMEOVERRESULT_ERROR_BOTH_LOSE = 4
GAMEOVERRESULT_SYS_FORCE_STOP = 5
GAMEOVERRESULT_NAMES = {
    GAMEOVERRESULT_NONE: "GAMEOVERRESULT_NONE",
    GAMEOVERRESULT_WIN: "GAMEOVERRESULT_WIN",
    GAMEOVERRESULT_LOSE: "GAMEOVERRESULT_LOSE",
    GAMEOVERRESULT_BOTH_LOSE: "GAMEOVERRESULT_BOTH_LOSE",
    GAMEOVERRESULT_ERROR_BOTH_LOSE: "GAMEOVERRESULT_ERROR_BOTH_LOSE",
    GAMEOVERRESULT_SYS_FORCE_STOP: "GAMEOVERRESULT_SYS_FORCE_STOP",
}

#: Device observations that bind the constants above.  RUN-0049 and the
#: 2026-10-18 tavern session are victories (the client printed 胜利); the
#: 2026-10-17 22:12 session is a loss (`netGameOver: 2 0 4`).
GAME_OVER_RESULT_VICTORY_SAMPLES = (6186266, 6186266)
GAME_OVER_RESULT_LOSS_SAMPLES = (6193484,)


def decode_game_over_result(value: object) -> int | None:
    """Decode C2S 67 slot 12 into a ``GAMEOVERRESULT_*`` code.

    ``None`` means the value is not a finite number, is not congruent to the
    recovered base modulo ``n``, or does not name one of the six packaged
    codes.  A ``None`` must fail closed: never award on an undecoded value.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    rounded = int(round(float(value)))
    delta = rounded - GAME_OVER_RESULT_BASE_SLOT_12
    if delta % GAME_OVER_RESULT_N:
        return None
    code = delta // GAME_OVER_RESULT_N
    if code in GAMEOVERRESULT_NAMES:
        return code
    return None


def is_victory(value: object) -> bool:
    """True only for the decoded ``GAMEOVERRESULT_WIN`` value."""

    return decode_game_over_result(value) == GAMEOVERRESULT_WIN
