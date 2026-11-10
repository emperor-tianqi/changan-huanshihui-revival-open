"""TASK-0474: 洗练 (awakening-skill reroll) pool and roll.

The candidate pool is not the global feature list: every card's template
(card/templete.max) names up to six feature types with a draw weight, a
level limit and a core count.  pkgUIRefineCardSkillDetailView lists exactly
those types (_tbFeatureTypeDrawList), so the wash must draw from them.

`refine_pool.json` is generated from the shipped chunks:

* templates: card cfg id -> {types: [[featureType, weight, limit], ...], core}
* features:  featureType -> {level: feature id}  (battle/feature.max)

Rolling itself has no capture; the server rolls with SystemRandom and reports
the outcome through the client's own S2C 404 contract.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

# level -> (success weight, lost level on failure, max feature level, costs)
REFINE_LEVELS: dict[int, tuple[float, int, int, int, int, int]] = {
    1: (0.30, 0, 5, 10, 100, 20),
    2: (0.25, 0, 7, 10, 100, 20),
    3: (0.23, 0, 10, 10, 100, 20),
    4: (0.20, 0, 15, 10, 100, 20),
    5: (0.18, 0, 18, 10, 100, 20),
    6: (0.15, 5, 99999, 10, 100, 20),
    7: (0.15, 5, 99999, 10, 100, 20),
    8: (0.15, 5, 99999, 10, 100, 20),
    9: (0.15, 5, 99999, 10, 100, 20),
    10: (1.0, 10, 99999, 100, 100, 20),
}

#: existing-skill count -> skill-lock cost (card/refineSave.max).
SAVE_COSTS: dict[int, int] = {
    1: 1, 2: 2, 3: 4, 4: 8, 5: 12,
    6: 16, 7: 20, 8: 24, 9: 28, 10: 32,
}

MAX_REFINE_LEVEL = 10

_POOL_PATH = Path(__file__).with_name("refine_pool.json")
_POOL: dict = json.loads(_POOL_PATH.read_text(encoding="utf-8"))
_TEMPLATES: dict = _POOL["templates"]
_FEATURES: dict = _POOL["features"]

#: fallback when a card cfg has no template row (unknown/special cards)
_FALLBACK_TYPES = [
    [30001, 40.0, 5], [30003, 20.0, 5], [30005, 20.0, 5], [30008, 20.0, 5],
]


def _rng(rng: random.Random | None = None) -> random.Random:
    return rng if rng is not None else random.SystemRandom()


def _template(card_cfg_id: int | None) -> dict:
    if card_cfg_id is not None:
        tpl = _TEMPLATES.get(str(int(card_cfg_id)))
        if tpl:
            return tpl
    return {"types": _FALLBACK_TYPES, "core": 2}


def _pick_type(types: list, rng: random.Random, exclude: set[int]) -> list:
    pool = [t for t in types if int(t[0]) not in exclude] or types
    total = sum(float(t[1]) for t in pool) or float(len(pool))
    roll = rng.random() * total
    acc = 0.0
    for item in pool:
        acc += float(item[1])
        if roll <= acc:
            return item
    return pool[-1]


def roll_candidates(
    card_cfg_id: int | None,
    level: int,
    rng: random.Random | None = None,
) -> tuple[list[int], list[bool]]:
    """Three feature ids from the card's own draw list, capped by wash tier.

    TASK-0481: the candidate skill level follows the wash level (a level-1 wash
    cannot roll a level-7 skill): cap = min(10, wash_level + 2).  Types and
    weights come from card/templete.max; the first coreFeatureNum types are the
    rare ones the client highlights.
    """

    picker = _rng(rng)
    tpl = _template(card_cfg_id)
    types = [list(t) for t in tpl["types"]]
    core_num = int(tpl.get("core") or 0)
    core_types = {int(t[0]) for t in types[:core_num]}
    cap = max(1, min(10, max(1, min(MAX_REFINE_LEVEL, int(level))) + 2))
    chosen: list[int] = []
    cores: list[bool] = []
    used: set[int] = set()
    for _ in range(3):
        item = _pick_type(types, picker, used)
        feature_type = int(item[0])
        table = _FEATURES.get(str(feature_type)) or {}
        avail = sorted(int(k) for k in table if 1 <= int(k) <= cap)
        if not avail:
            avail = sorted(int(k) for k in table)
        if not avail:
            chosen.append(0)
            cores.append(feature_type in core_types)
            used.add(feature_type)
            continue
        pick_level = picker.choice(avail)
        chosen.append(int(table[str(pick_level)]))
        cores.append(feature_type in core_types)
        used.add(feature_type)
    return chosen, cores


def roll_refine(
    card_cfg_id: int | None,
    level: int,
    rng: random.Random | None = None,
) -> dict:
    """One wash: success/failure, level change, candidates and core flags.

    TASK-0481 rules (Owner correction):
    * success: tier +1;
    * failure at 1..6: no level change (blessing handled by the server);
    * failure at 7..9: tier -1, never below 7;
    * tier 10 succeeds by the shipped weight (1.0).
    """

    picker = _rng(rng)
    level = max(1, min(MAX_REFINE_LEVEL, int(level)))
    weight, _lost, _max_fu, _c1, _c2, _c3 = REFINE_LEVELS[level]
    candidates, cores = roll_candidates(card_cfg_id, level, picker)
    if picker.random() < float(weight):
        return {
            "code": 0,
            "lost_level": 0,
            "candidates": candidates,
            "cores": cores,
            "new_level": min(MAX_REFINE_LEVEL, level + 1),
        }
    if 7 <= level <= 9:
        return {
            "code": 2,
            "lost_level": max(7, level - 1),
            "candidates": candidates,
            "cores": cores,
            "new_level": max(7, level - 1),
        }
    return {
        "code": 1,
        "lost_level": 0,
        "candidates": candidates,
        "cores": cores,
        "new_level": level,
    }


def save_cost(existing_count: int) -> int:
    return SAVE_COSTS.get(max(1, min(10, int(existing_count) + 1)), 32)


_STARS: dict = _POOL.get("stars") or {}


def initial_four(card_cfg_id: int | None) -> int:
    """The 4th skill a 6-star card should start with (0 when not applicable)."""

    if card_cfg_id is None:
        return 0
    if int(_STARS.get(str(int(card_cfg_id)), 0)) < 6:
        return 0
    tpl = _TEMPLATES.get(str(int(card_cfg_id))) or {}
    four = [int(v) for v in (tpl.get("four") or ()) if int(v) > 0]
    return four[0] if four else 0


_SKILLS: dict = _POOL.get("skills") or {}


def base_skills(card_cfg_id: int | None) -> list[int]:
    """The card's shipped sfmId1..3 (card/card.max)."""

    if card_cfg_id is None:
        return [0, 0, 0]
    return list(_SKILLS.get(str(int(card_cfg_id))) or [0, 0, 0])


def card_star(card_cfg_id: int | None) -> int:
    if card_cfg_id is None:
        return 0
    return int(_STARS.get(str(int(card_cfg_id)), 0))
