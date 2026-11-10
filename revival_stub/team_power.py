"""The client's own team-fight formula, computed from this server's stored rows.

`pkgTeamDataMgr.caclGroupFight` (source lines 226-248) is what the shipped
client adds up for one preset::

    pkgPlayerInfoMgr.caclRoleFight()                 -- role/soul/equip/god
    + sum(pkgCardDataMgr.getCardFight(id) for id in tbGroup)
    + pkgFabaoDataMgr.caclSelectFight()
    + sum(pkgCalcFightHelper.getOneShenFuFight(id) for id in runes)

`pkgPlayerInfoMgr.caclRoleFight` (source lines 442-451) is::

    math.floor(
        pkgCfgTalentMgr.getTalentTotalFight(pkgTalentMgr.getSoulId(),
                                            pkgTalentMgr.getTalent())
        + pkgEquipModelMgr.getTeamEquipFight(getCurGroupID())
        + pkgGodWeaponMgr.getTeamGodWeaponFight(getCurGroupID()))

and `pkgCardDataMgr.getCardFight` (source lines 796-811) is::

    math.floor(cfgCard.fight * getCardFightRatio(level, advanceLevel)
               + pkgEquipModelMgr.getCardEquipFight(id)
               + pkgGodWeaponMgr.getCardGodWeaponFight(id))

Every term that this archive has no producer for is **proved** to be zero with
the state this server actually sends, not assumed:

* talents: `pkgTalentMgr.le_tbTalent = le_tbTalent or {}` (module line 10) and
  this server sends no talent row, so the loop in
  `pkgCfgTalentMgr.getTalentTotalFight` (decoded PROTO 0.7, source lines
  104-115) iterates nothing; the soul term is `_cfg.soul[dSoulId].fight` with a
  nil guard (`pkgCfgSoulMgr.getSoulCfgData`, decoded PROTO 0.1, source line 50).
  The server sends S2C 554 with the profile's active soul (101 for career 1), so
  that term is the frozen `ACTIVE_SOUL_FIGHT` below.
* team equipment: `pkgEquipModelMgr.getTeamEquipFight` ->
  `getRoleEquipInfoList` -> `ipairs(getGroupEquips(gid))` -> `getEquipInfo(id)`;
  this server sends no equipment row, so every stored id resolves to nil and
  `pkgCalcFightHelper.getEquipFight` (decoded PROTO 0.1, source lines 19-47)
  sums nothing (its `TEST R0` returns 0 for an empty list).
* team god weapon: `pkgGodWeaponMgr.getTeamGodWeaponFight` guards
  `tbGodWeapon` (source lines 719-731) and `getGodWeapon` reads
  `le_tbGodWeapons[id]`; this server sends no god-weapon row, so it is nil.
* fabao: `pkgFabaoDataMgr.caclSelectFight` (source lines 688-702) iterates
  `getTeamFabaoData(gid)`, which the v5 login snapshot initialises to three
  empty slots; every slot is skipped.
* runes/shenfu: `pkgRuneMgr.getRuneSelectByGroupId` (source lines 25-27) reads
  `le_tbRuneSelect[gid]`, which this server never fills, so the
  `getOneShenFuFight` loop never runs.

The two remaining inputs are the per-card values: a card's base `fight` and the
level/advance ratio.  Both are frozen from the shipped chunks and re-derived by
``max_decoder/analyze_team_max_fight.py``; the analyzer also proves the five
zero terms above from the recovered sources and the decode of
``pkgCfgTalentMgr``/``pkgCfgSoulMgr``/``pkgCalcFightHelper``.

This module computes a value the original server also had to compute; whether
the original server used this exact formula, and when it sent S2C 39
``returnTeamMaxFight``, is UNKNOWN.  The value is therefore this build's
compatibility choice (``synthetic_compatibility``) built from
``deterministic_recovered`` client code and shipped config, never a claim about
the historical wire.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

#: `card.max` cfg id -> base `fight`, frozen from the shipped chunk.
_FROZEN_CARD_FIGHT = (
    "1101:100,1102:100,1103:100,1104:100,1105:100,1107:100,2201:200,2202:200,2203:200,2204:200",
    "2205:200,2206:200,2207:200,2208:200,2209:200,2211:200,2212:200,2213:200,2214:200,2215:200",
    "2216:200,2217:200,2218:200,2219:200,2220:200,2221:200,2222:200,2223:200,2224:200,2225:200",
    "3301:350,3302:350,3305:350,3307:350,3308:350,3309:350,3310:350,3311:350,3312:350,3313:350",
    "3314:350,3315:350,3317:350,3318:350,3319:350,3320:350,3321:350,3322:350,3326:350,3327:350",
    "4301:500,4302:500,4305:500,4307:500,4308:500,4309:500,4310:500,4311:500,4312:500,4313:500",
    "4314:500,4315:500,4317:500,4318:500,4319:500,4320:500,4321:500,4322:500,4326:500,4327:500",
    "4401:550,4402:500,4403:500,4404:550,4405:500,4406:550,4407:500,4408:500,4409:500,4410:550",
    "4411:500,4412:500,4413:550,4414:500,4415:500,4416:550,4417:550,4418:550,4419:550,4420:550",
    "4421:550,4422:550,4423:550,4424:550,4425:550,4426:600,4427:600,4428:600,4429:600,4430:600",
    "4431:600,4432:600,4433:600,4434:600,4435:600,4436:600,4437:600,4438:600,4439:600,4440:600",
    "4441:600,4442:600,4443:500,4444:500,4445:500,4446:500,4447:500,4448:500,4449:500,4501:900",
    "4502:900,4503:900,4504:900,4505:900,4506:900,4507:900,4508:900,4509:900,4510:900,4511:900",
    "4512:900,4513:900,4514:900,4515:900,4516:900,4517:900,4518:900,4519:900,4520:900,4521:900",
    "4522:900,4523:900,4524:900,4525:900,4526:900,4527:900,4528:900,4529:900,4530:900,4531:900",
    "5301:800,5302:800,5305:800,5307:800,5308:800,5309:800,5310:800,5311:800,5312:800,5313:800",
    "5314:800,5315:800,5317:800,5318:800,5319:800,5320:800,5321:800,5322:800,5326:800,5327:800",
    "5401:900,5402:800,5403:800,5404:900,5405:800,5406:900,5407:800,5408:800,5409:800,5410:900",
    "5411:800,5412:800,5413:900,5414:800,5415:800,5416:900,5417:900,5418:900,5419:900,5420:900",
    "5421:900,5422:900,5423:900,5424:900,5425:900,5426:1000,5427:1000,5428:1000,5429:1000,5430:1000",
    "5431:1000,5432:1000,5433:1000,5434:1000,5435:1000,5436:1000,5437:1000,5438:1000,5439:1000,5440:1000",
    "5441:1000,5442:1000,5443:800,5444:800,5445:800,5446:800,5447:800,5448:800,5449:800,5501:1300",
    "5502:1300,5503:1300,5504:1300,5505:1300,5506:1300,5507:1300,5508:1300,5509:1300,5510:1300,5511:1300",
    "5512:1300,5513:1300,5514:1300,5515:1300,5516:1300,5517:1300,5518:1300,5519:1300,5520:1300,5521:1300",
    "5522:1300,5523:1300,5524:1300,5525:1300,5526:1300,5527:1300,5528:1300,5529:1300,5530:1300,5531:1300",
    "6301:1200,6302:1200,6305:1200,6307:1200,6308:1200,6309:1200,6310:1200,6311:1200,6312:1200,6313:1200",
    "6314:1200,6315:1200,6317:1200,6318:1200,6319:1200,6320:1200,6321:1200,6322:1200,6326:1200,6327:1200",
    "6401:1350,6402:1200,6403:1200,6404:1350,6405:1200,6406:1350,6407:1200,6408:1200,6409:1200,6410:1350",
    "6411:1200,6412:1200,6413:1350,6414:1200,6415:1200,6416:1350,6417:1350,6418:1350,6419:1350,6420:1350",
    "6421:1350,6422:1350,6423:1350,6424:1350,6425:1350,6426:1500,6427:1500,6428:1500,6429:1500,6430:1500",
    "6431:1500,6432:1500,6433:1500,6434:1500,6435:1500,6436:1500,6437:1500,6438:1500,6439:1500,6440:1500",
    "6441:1500,6442:1500,6443:1200,6444:1200,6445:1200,6446:1200,6447:1200,6448:1200,6449:1200,6501:1800",
    "6502:1800,6503:1800,6504:1800,6505:1800,6506:1800,6507:1800,6508:1800,6509:1800,6510:1800,6511:1800",
    "6512:1800,6513:1800,6514:1800,6515:1800,6516:1800,6517:1800,6518:1800,6519:1800,6520:1800,6521:1800",
    "6522:1800,6523:1800,6524:1800,6525:1800,6526:1800,6527:1800,6528:1800,6529:1800,6530:1800,6531:1800",
    "33333:0,44444:0,55555:0,99999:0",
)

#: `cardFightRatio[1].levelN`, frozen from the shipped chunk (levels 0..10).
_FROZEN_LEVEL_RATIO = (
    "0:1.0,1:1.02,2:1.04,3:1.06,4:1.08,5:1.26,"
    "6:1.3,7:1.34,8:1.38,9:1.42,10:1.66"
)

#: `cardFightRatio[1].advN`, frozen from the shipped chunk (advance 1..3).
_FROZEN_ADVANCE_RATIO = "1:1.74,2:1.82,3:1.9"

#: `soul.max[101].fight`; 101 is the profile's active soul for career 1
#: (`ActiveSoulRule`), and S2C 554 sends it every login.
ACTIVE_SOUL_FIGHT: dict[int, int] = {101: 100}

#: Canonical digest over the four frozen inputs; the analyzer re-derives it.
TEAM_POWER_DIGEST = (
    "1bc3165e314c3adb37b6ab7cd3819623dddc7b4882178433b81523825ca3d53c"
)

TEAM_POWER_SOURCE = (
    "apk_unpacked/assets/config/card/card.max fight + "
    "config/card/cardFightRatio.max levelN/advN + config/soul/soul.max[101].fight"
)
TEAM_POWER_VALUE_PROVENANCE = "official_recovered"
TEAM_POWER_RULE_PROVENANCE = "deterministic_recovered"
TEAM_POWER_ORIGIN = "revival_local_team_max_fight"


class TeamPowerError(ValueError):
    """This server cannot reproduce the client's own number for this input."""


def _parse_pairs(text: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for pair in text.split(","):
        key, value = pair.split(":")
        result[int(key)] = int(value)
    return result


def _parse_ratios(text: str) -> dict[int, float]:
    result: dict[int, float] = {}
    for pair in text.split(","):
        key, value = pair.split(":")
        result[int(key)] = float(value)
    return result


CARD_BASE_FIGHT: dict[int, int] = _parse_pairs(",".join(_FROZEN_CARD_FIGHT))
LEVEL_RATIO: dict[int, float] = _parse_ratios(_FROZEN_LEVEL_RATIO)
ADVANCE_RATIO: dict[int, float] = _parse_ratios(_FROZEN_ADVANCE_RATIO)


def digest(
    *,
    card_base_fight: Mapping[int, int] | None = None,
    level_ratio: Mapping[int, float] | None = None,
    advance_ratio: Mapping[int, float] | None = None,
    active_soul_fight: Mapping[int, int] | None = None,
) -> str:
    """sha256 over the canonical JSON of the four frozen inputs."""

    payload = {
        "card_base_fight": {
            str(key): value
            for key, value in sorted((card_base_fight or CARD_BASE_FIGHT).items())
        },
        "level_ratio": {
            str(key): value
            for key, value in sorted((level_ratio or LEVEL_RATIO).items())
        },
        "advance_ratio": {
            str(key): value
            for key, value in sorted((advance_ratio or ADVANCE_RATIO).items())
        },
        "active_soul_fight": {
            str(key): value
            for key, value in sorted(
                (active_soul_fight or ACTIVE_SOUL_FIGHT).items()
            )
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def card_fight(cfg_id: int, level: int, advance_level: int) -> int:
    """``pkgCardDataMgr.getCardFight`` for a card with no equipment/god weapon."""

    try:
        base = CARD_BASE_FIGHT[int(cfg_id)]
    except KeyError as exc:
        raise TeamPowerError(f"card cfg {cfg_id!r} has no recovered fight base") from exc
    try:
        if int(advance_level) > 0:
            ratio = ADVANCE_RATIO[int(advance_level)]
        else:
            ratio = LEVEL_RATIO[int(level)]
    except KeyError as exc:
        raise TeamPowerError(
            f"card cfg {cfg_id!r} level {level!r} advance {advance_level!r} has "
            "no recovered cardFightRatio row"
        ) from exc
    return math.floor(base * ratio)


def _slot(record: Any, name: str) -> str:
    value = getattr(record, name, "")
    return "" if value is None else str(value)


def team_power(
    *,
    card_ids: Iterable[str],
    card_rows: Mapping[str, Any],
    soul_id: int,
) -> int:
    """The current preset's fight, exactly as the client's `getCurFight` adds it.

    ``card_rows`` maps instance id -> a `role_store.CardInstanceRecord` (or any
    object with the same fields).  Every non-empty slot must resolve; a card
    with equipment or a god weapon cannot be reproduced from this archive's
    data and is refused rather than counted as zero.
    """

    try:
        total = ACTIVE_SOUL_FIGHT[int(soul_id)]
    except KeyError as exc:
        raise TeamPowerError(
            f"active soul {soul_id!r} has no recovered soul.max fight"
        ) from exc
    for instance_id in card_ids:
        value = str(instance_id or "")
        if not value:
            continue
        record = card_rows.get(value)
        if record is None:
            raise TeamPowerError(
                f"team card {value!r} has no stored card row; the client's "
                "getCardFight cannot be reproduced"
            )
        for slot in ("equip_id1", "equip_id2", "equip_id3", "equip_id4",
                     "god_weapon_id"):
            if _slot(record, slot):
                raise TeamPowerError(
                    f"team card {value!r} carries {slot}={_slot(record, slot)!r}; "
                    "this archive holds no equipment/god-weapon fight rows"
                )
        total += card_fight(
            int(getattr(record, "card_cfg_id")),
            int(getattr(record, "level")),
            int(getattr(record, "advance_level")),
        )
    return total
