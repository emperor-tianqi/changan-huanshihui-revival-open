"""Explicit dev-play entry state for the 神器副本 (artifact_dgn) UI (TASK-0258).

The edge this module serves is the *entry page's data plane*, not a tower run.
On a fresh permanent archive the client's own entry gate can never open:

* ``functionOpen[43]`` ``artifact_dgn`` has ``openLevel = 60``, so
  ``pkgArtifactDgnMgr.sysIsOpen()`` is false while the client's level (the 5th
  ``S2C 10 returnRoleInfo`` value) is 1 (D-208);
* the entry is the ``tab/artifact`` tab of the 装备战斗面板
  ``equip_fight/pkgEquipFightPanel`` whose own ``functionOpen[35]`` requires
  level 38; the panel's ``show()`` sends **C2S 744 artifactDgnOpenUI**, and the
  entry view ``artifactDgn/pkgUIArtifactDgnEnter`` draws from two server
  packets: **S2C 1526 artifactDgnSendWeekAndOpen** (``NNyyyyy``) and
  **S2C 1527 artifactDgnSendRaceAndTower** (``NNNNNN``).

A real archive cannot be edited to satisfy this, and the permanent QA profile
must not be written.  This module therefore owns one **explicit,
profile-scoped synthetic overlay** for a separate dev-play profile
(``dev-artifact-main``):

* the archive holds one ``meta`` value (no new table, no migration, no
  ``SCHEMA_VERSION`` change), written only by the ``seed`` command below;
* the server applies it only when three independent conditions hold: the archive
  *is* ``dev-artifact-main``, the server was started with the explicit
  ``--dev-play-artifact`` flag, and the meta value is present and valid.  Missing
  any one of them keeps the frozen login stream and never answers an
  artifactDgn packet;
* the one wire value the overlay itself carries is the session-scoped
  ``level = 60`` in ``S2C 10`` (the role row has no level column and is not
  written).

The two answer packets are not arbitrary values:

* ``S2C 1526`` field 1 is ``pkgCfgArtifactDgn.getCurWeek(server_now)`` and field
  2 is ``getCurWeek4Shop(server_now)`` -- both formulas are re-derived from the
  shipped chunk (``zys_scAll__cfg__artifactDgn__pkgCfgArtifactDgn.lua`` lines
  111-126 / 144-152) and computed from the **same** server clock ``S2C 0``
  uses.  Fields 3..7 are ``cfgArtifactDgnWeek[week].open1..5`` as booleans: the
  registration width is ``y``, and Lua treats the integer ``0`` as *true*, so
  the closed weeks must go out as ``false``.
* ``S2C 1527`` fields 1..5 are the zero initial state
  (``dCurRace/dCurTower/dTowerCount/dCardCount/dIsHard``), labelled
  ``synthetic_compatibility`` / ``replaceable`` / not historical.  Its 6th
  ``N`` is the registration-only extra field **U-3**: sent as ``0`` and never
  given a meaning.

Nothing here implements C2S 745/746, S2C 1528/1529/1530/1531/1537/1538, the
tower view, the team view, the fabao inventory or any battle/settlement edge,
and nothing here writes a role level, a wallet, an inventory row or the
permanent ``qa-changan-main`` archive.

Usage::

    python -m revival_stub.dev_play_artifact seed   --profile dev-artifact-main
    python -m revival_stub.dev_play_artifact status --profile dev-artifact-main
    python -m revival_stub.dev_play_artifact remove --profile dev-artifact-main
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .play_profile import (
    PROFILE_MODE_RESUME,
    PlayProfileError,
    select_play_profile,
)
from .card_growth import (
    LEVEL_EXP,
    advance_max_level,
    card_star_rare,
    max_level,
)
from .role_store import RoleStore, RoleStoreError
from .team_power import card_fight


# --------------------------------------------------------------- the profile

#: The only play profile this overlay may serve.  The permanent QA archive is
#: never written by this module, and no other name is accepted.
DEV_PLAY_ARTIFACT_PROFILE_ID = "dev-artifact-main"

#: The one ``meta`` key (structure 1) that carries the overlay.
DEV_PLAY_ARTIFACT_META_KEY = "dev_play_artifact_overlay"

#: The stored payload's own domain marker and structure version.
DEV_PLAY_ARTIFACT_META_FORMAT = "revival-dev-play-artifact-overlay"
DEV_PLAY_ARTIFACT_META_FORMAT_VERSION = 1

#: Provenance of the overlay.  It says this is a local simulator dev play state,
#: never an original-server record.
DEV_PLAY_ARTIFACT_ORIGIN = "revival_local_dev_play_artifact_overlay"

#: The reason written into the login event's ``role_level_overlay_reason``.
DEV_PLAY_ARTIFACT_LEVEL_OVERLAY_REASON = "dev_play_artifact_overlay"

#: ``VALUE``/``SELECTION`` provenance words already used by the project.
PROVENANCE_DETERMINISTIC_RECOVERED = "deterministic_recovered"
PROVENANCE_OFFICIAL_RECOVERED = "official_recovered"
PROVENANCE_SYNTHETIC_COMPATIBILITY = "synthetic_compatibility"


# ------------------------------------------------------------- the frozen values

#: ``functionOpen[43]`` (D-208): ``artifact_dgn``, ``openLevel = 60``,
#: ``chapter = 0``, ``mainUIPath = ""``.  The number is recovered; announcing a
#: dev-play level is synthetic.
ARTIFACT_DGN_OPEN_LEVEL = 60

#: ``functionOpen[35]`` (D-208): the 装备战斗面板's own level gate.
EQUIP_FIGHT_OPEN_LEVEL = 38

#: The macro and view names the recovered entry chain uses (D-208).
ARTIFACT_DGN_MACRO = "artifact_dgn"
ARTIFACT_DGN_FUNCTION_OPEN_ID = 43
EQUIP_FIGHT_FUNCTION_OPEN_ID = 35
EQUIP_FIGHT_PANEL_VIEW = "equip_fight/pkgEquipFightPanel"
ARTIFACT_DGN_ENTRY_VIEW = "artifactDgn/pkgUIArtifactDgnEnter"

#: ``pkgCfgArtifactDgn.getCurWeek`` constants (shipped chunk lines 111-126):
#: ``dNow = dNow - 316800``; the loop is ``3 * 24 * 60 * 60`` seconds and the
#: week is ``math.fmod(math.modf(dNow / loop)[1], 5) + 1``.
ARTIFACT_WEEK_EPOCH_OFFSET_S = 316800
ARTIFACT_WEEK_LOOP_DAYS = 3
ARTIFACT_WEEK_LOOP_S = ARTIFACT_WEEK_LOOP_DAYS * 24 * 60 * 60
ARTIFACT_WEEK_CYCLE = 5

#: ``pkgCfgArtifactDgn.getCurWeek4Shop`` (lines 144-152): the shop loops once a
#: day and ``math.fmod(dInt, 4) + 1``.
ARTIFACT_SHOP_LOOP_DAYS = 1
ARTIFACT_SHOP_LOOP_S = ARTIFACT_SHOP_LOOP_DAYS * 24 * 60 * 60
ARTIFACT_SHOP_CYCLE = 4

#: ``cfgArtifactDgnWeek`` open1..5, 5 rows, re-derived from the shipped chunk by
#: ``max_decoder/analyze_artifact_dgn_entry.py`` (contract
#: ``reports/task_0257_artifact_dgn_contract.json`` ->
#: ``config.relations.week.open_matrix``).  1 = open, 0 = closed.
ARTIFACT_WEEK_OPEN: dict[int, tuple[int, int, int, int, int]] = {
    1: (1, 1, 0, 0, 0),
    2: (0, 0, 1, 1, 0),
    3: (1, 0, 0, 0, 1),
    4: (0, 1, 1, 0, 0),
    5: (0, 0, 0, 1, 1),
}

#: Where the matrix above is re-derived from (report/documentation pointer).
ARTIFACT_WEEK_OPEN_SOURCE = (
    "apk_unpacked/assets/luaScript/artifactDgn/cfgArtifactDgnWeek.max "
    "via analyze_artifact_dgn_entry.py::recover_config"
)

#: Provenance of the two 1526-derived values.
ARTIFACT_WEEK_VALUE_PROVENANCE = PROVENANCE_DETERMINISTIC_RECOVERED
ARTIFACT_OPEN_CFG_PROVENANCE = PROVENANCE_OFFICIAL_RECOVERED
ARTIFACT_OPEN_RULE_PROVENANCE = PROVENANCE_DETERMINISTIC_RECOVERED

#: S2C 1527's five business values are the entry page's first-enter state.  The
#: client's own handlers store them in ``le_dRace/le_dTower/le_dTowerCount/
#: le_dCardCount/le_dIsHard`` and dispatch ``pkgUIArtifactDgnEnter_update``.
ARTIFACT_RACE_TOWER_INITIAL_STATE: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
ARTIFACT_RACE_TOWER_VALUE_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY

#: S2C 1527's 6th ``N`` is registration-only (registration 6 N, handler 5
#: business values): U-3 in the TASK-0257 contract.  The wire value is 0 and no
#: meaning is assigned to it.
ARTIFACT_RACE_TOWER_FIELD_6 = 0

#: TASK-0282 -- the dev-play pair that is LEGAL and OPEN this week.
#:
#: The zero state above cannot be played: the first device run logged
#: "pkgArtifactDgnMgr.lua:106: attempt to index local 'cfg' (a nil value)" because
#: getDgnDescCfg(le_dRace) indexes cfgArtifactDgnDesc[dCurRace] and row 0 does not exist,
#: and tbTmp[dCurTower] is nil for tower 0 -- show() then aborts while evaluating its own
#: argument, leaving the tower view in its prefab default state (TASK-0280 contract 1 and 5).
#: cfgArtifactDgnWeek[2] = [0,0,1,1,0] leaves races 3 (yin dian) and 4 (shan luan) open, and
#: every race has towers 1..5, so (3, 1) renders the title "阴殿-第一层" with
#: "当前层数/总层数：1/5".
ARTIFACT_RACE_TOWER_DEV_PLAY_STATE: tuple[int, int, int, int, int] = (3, 1, 0, 0, 0)
ARTIFACT_RACE_TOWER_DEV_PLAY_TITLE = "阴殿-第一层"
#: S2C 1551 artifactDgnAchInfo (NNa): the ONLY writer of the entry page score
#: (pkgArtifactDgnAchievement.le_dScore; no C2S requests it).  Week 2 matches the 1526 push;
#: score 0 is the honest "no achievements yet" value (the real account score is UNKNOWN U-5),
#: and an empty reward csv makes the client split(",", "") return {}.
ARTIFACT_ACH_INFO_START_WEEK = 2
ARTIFACT_ACH_INFO_SCORE = 0
ARTIFACT_ACH_INFO_FIELDS: tuple[int, int, str] = (
    ARTIFACT_ACH_INFO_START_WEEK, ARTIFACT_ACH_INFO_SCORE, "")
ARTIFACT_RACE_TOWER_FIELD_6_SEMANTICS = "unknown_placeholder"
ARTIFACT_RACE_TOWER_FIELD_6_PROVENANCE = "unknown"
ARTIFACT_RACE_TOWER_FIELD_6_UNKNOWN_ID = "U-3"

#: C2S 744's one registered ``N``: all six client send sites pass only
#: ``e_tbSocket``.  U-1 -- the raw frame has never been captured, so no meaning
#: is assigned and the registration format stays the wire authority.
ARTIFACT_OPEN_UI_ARGUMENT_SEMANTICS = "unknown_placeholder"
ARTIFACT_OPEN_UI_UNKNOWN_ID = "U-1"


# ------------------------------------------------- the 装备战斗面板 data plane (TASK-0263)
#
# The artifact dungeon has no main-city button: its only client entrance is the
# 装备战斗面板's artifact TAB.  That panel is driven by exactly two server packets:
#
#   * S2C 1320 `syncEquipFightStageToCg` `paayapp` -- reader
#     `pkgS2CEquipFightMgr.lua:8`; `e_tbOpenFbIds = table_ext.values2key(
#     pkgStringMgr.split2Number("#", strOpenFbIds))` is the ONLY thing that decides
#     whether an fb cell is unlocked (`pkgEquipFightModel.lua:118`), and
#     `e_dNextFreshTime = dDataResetTime` is what stops the 1 Hz C2S 653 re-send
#     (`pkgEquipFightPanel.lua:77` compares it with `pkgServerTimeMgr.getCurrentServerTime()`).
#   * S2C 1321 `syncEquipFightRandomDataToCg` `paa` -- reader
#     `pkgS2CEquipFightMgr.lua:20`; `e_tbScoreRuleIds` MUST cover every stage the
#     player can open, because `getScoreBuffIds` feeds `getStageRuleIds` straight into
#     `ipairs` (`pkgEquipFightModel.lua:221-224`) and nil is a Lua error.
#
# Every value below is a dev-play CHOICE, not a historical fact: the real values live
# in an unrecovered account state, and the two reset-time epochs are U-C in the
# TASK-0261 contract (`reports/task_0261_equip_fight_contract.json`).

#: `pkgEquipFightConst.const_MaxFbCount = 5` (shipped chunk line 16) and
#: `getFbIdByStageId(stageId) = math.floor(stageId / 100)` (line 26-28) fix the fb id
#: space to 1..5; `pkgEquipFightPanel._initFbCellView` walks the same 1..5.
EQUIP_FIGHT_PANEL_OPEN_FB_IDS: tuple[int, ...] = (1, 2, 3, 4, 5)

#: How far in the future the two refresh instants are placed.  Any instant strictly
#: greater than the client's own server time stops the 1 Hz re-send; one hour keeps the
#: panel quiet for a real session without pretending to know the original reset rule.
EQUIP_FIGHT_PANEL_REFRESH_WINDOW_S = 3600

#: Provenance of the 1320/1321 values as a whole (TASK-0261: "synthetic_compatibility
#: for every value; replaceable=true; historical=false").  TASK-0311 refines the claim:
#: the stage/rule *ids* inside the four strings are shipped cfg bytes
#: (`EQUIP_FIGHT_PANEL_STAGE_ROWS_PROVENANCE`); "these stages are open, score 0" is
#: still this dev-play choice.
EQUIP_FIGHT_PANEL_VALUE_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY
EQUIP_FIGHT_PANEL_SOURCE = (
    "reports/task_0261_equip_fight_contract.json next_implementation + "
    "max_decoder/forensics_equip_fight/ (pkgS2CEquipFightMgr, pkgEquipFightModel, "
    "pkgEquipFightConst) + max_decoder/analyze_equip_fight_stage_rows.py (the two "
    "stage tables and two rule tables, TASK-0311)"
)
#: U-A/U-B/U-C are the zero-field C2S 653 writer width, the 1320/1321 ordering and the
#: reset-time epoch.  The stage/rule *ids* add no unknown (TASK-0311: every one of the 45
#: stage rows and every rule/buff id resolved from cfg).
EQUIP_FIGHT_PANEL_UNKNOWN_IDS: tuple[str, ...] = ("U-A", "U-B", "U-C")

#: TASK-0311 -- the empty stage/clear/buff/rule tables were NOT safe, and RUN17
#: (`reports/run17_equip_stage_detail_2026-10-26.md`) proved it: the stage detail page
#: always looks up the rules of the stage it renders, so `getScoreBuffIds` feeds the nil
#: `e_tbScoreRuleIds[dStageId]` into `ipairs` and Lua raises
#: `pkgEquipFightModel.lua:213`.  `e_tbScoreRuleIds`/`e_tbBuffIds` only get keys from
#: S2C 1321, so both tables must describe *every* stage id the panel can ask about, not
#: just the one a player has reached.
#:
#: Ids, the fb->stage lists and the two rule-id lists are shipped config bytes, not a
#: choice: `max_decoder/analyze_equip_fight_stage_rows.py` re-derives them from
#: `apk_unpacked/assets/config/equip_fight/cfgEquipFight{Stage,Fb,Score,Buff}.max`
#: (frozen rows sha256 `a73e312f36974a43da4f30fc1481a361b09a77635459ed3c4181e8fe9630dbda`):
#:
#:   fb1 阆风巅 101..109 | fb2 承渊谷 201..209 | fb3 龙池 301..309
#:   fb4 昆仑宫 401..409 | fb5 玄圃堂 501..509
#:
#: Every one of those 45 rows declares `scoreRuleId = "10001#10002"` and
#: `buffRuleId = "21001"`; 10001/10002 exist in cfgEquipFightScore and 21001 in
#: cfgEquipFightBuff, so no id is invented and no list is empty (`stageId:` is never
#: needed).  The panel's cell list is `cfgEquipFightFb[fb].stage`, and
#: `pkgEquipFightStagePanel._layoutFbStageCellView` adds the tap listener with no lock
#: guard, so all nine cells of an open fb can ask for rules.
#:
#: The two numbers are 0, and that is the dev-play choice: absent keys already read as 0
#: (`getStageScore`/`getStageScoreNoClear` use `or 0`), 0 is "never fought", and
#: `cfgEquipFightScoreToLevel` maps 0 to level C for every level id while
#: `pkgEquipFightConst.isStagePass` accepts only S/SS/SSS -- **45 open rows are not 45
#: cleared stages** and this unlocks no stage gate by itself.
EQUIP_FIGHT_PANEL_STAGE_ROWS_SOURCE = (
    "apk_unpacked/assets/config/equip_fight/cfgEquipFightFb.max + "
    "cfgEquipFightStage.max + cfgEquipFightScore.max + cfgEquipFightBuff.max via "
    "max_decoder/analyze_equip_fight_stage_rows.py"
)
#: The ids are shipped bytes (`deterministic_recovered`); the 0 scores are the choice.
EQUIP_FIGHT_PANEL_STAGE_ROWS_PROVENANCE = PROVENANCE_DETERMINISTIC_RECOVERED
EQUIP_FIGHT_PANEL_STAGE_VALUE_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY

#: Every stage id of the five open fbs, ascending: `cfgEquipFightFb[fb].stage`.
EQUIP_FIGHT_PANEL_STAGE_IDS: tuple[int, ...] = (
    101, 102, 103, 104, 105, 106, 107, 108, 109,
    201, 202, 203, 204, 205, 206, 207, 208, 209,
    301, 302, 303, 304, 305, 306, 307, 308, 309,
    401, 402, 403, 404, 405, 406, 407, 408, 409,
    501, 502, 503, 504, 505, 506, 507, 508, 509,
)

#: S2C 1320 `strEquipFightStage`: `stageId:number` joined by hash
#: (`pkgEquipFightConst.s2tScoreRecords` -> `_s2tEFCommonList(str, true)`).
EQUIP_FIGHT_PANEL_STAGE_TABLE = (
    "101:0#102:0#103:0#104:0#105:0#106:0#107:0#108:0#109:0#"
    "201:0#202:0#203:0#204:0#205:0#206:0#207:0#208:0#209:0#"
    "301:0#302:0#303:0#304:0#305:0#306:0#307:0#308:0#309:0#"
    "401:0#402:0#403:0#404:0#405:0#406:0#407:0#408:0#409:0#"
    "501:0#502:0#503:0#504:0#505:0#506:0#507:0#508:0#509:0"
)

#: S2C 1320 `strScoreNoClear`: the same key set with the same honest 0 (nothing cleared).
EQUIP_FIGHT_PANEL_SCORE_NO_CLEAR_TABLE = (
    "101:0#102:0#103:0#104:0#105:0#106:0#107:0#108:0#109:0#"
    "201:0#202:0#203:0#204:0#205:0#206:0#207:0#208:0#209:0#"
    "301:0#302:0#303:0#304:0#305:0#306:0#307:0#308:0#309:0#"
    "401:0#402:0#403:0#404:0#405:0#406:0#407:0#408:0#409:0#"
    "501:0#502:0#503:0#504:0#505:0#506:0#507:0#508:0#509:0"
)

#: S2C 1321 `strBuffIds`: `stageId:id,id`, one cfg `buffRuleId` per stage.
EQUIP_FIGHT_PANEL_BUFF_TABLE = (
    "101:21001#102:21001#103:21001#104:21001#105:21001#106:21001#107:21001#108:21001#109:21001#"
    "201:21001#202:21001#203:21001#204:21001#205:21001#206:21001#207:21001#208:21001#209:21001#"
    "301:21001#302:21001#303:21001#304:21001#305:21001#306:21001#307:21001#308:21001#309:21001#"
    "401:21001#402:21001#403:21001#404:21001#405:21001#406:21001#407:21001#408:21001#409:21001#"
    "501:21001#502:21001#503:21001#504:21001#505:21001#506:21001#507:21001#508:21001#509:21001"
)

#: S2C 1321 `strScoreRules`: `stageId:id,id` (comma inside a stage, hash between stages).
EQUIP_FIGHT_PANEL_SCORE_RULE_TABLE = (
    "101:10001,10002#102:10001,10002#103:10001,10002#104:10001,10002#105:10001,10002#106:10001,10002#107:10001,10002#108:10001,10002#109:10001,10002#"
    "201:10001,10002#202:10001,10002#203:10001,10002#204:10001,10002#205:10001,10002#206:10001,10002#207:10001,10002#208:10001,10002#209:10001,10002#"
    "301:10001,10002#302:10001,10002#303:10001,10002#304:10001,10002#305:10001,10002#306:10001,10002#307:10001,10002#308:10001,10002#309:10001,10002#"
    "401:10001,10002#402:10001,10002#403:10001,10002#404:10001,10002#405:10001,10002#406:10001,10002#407:10001,10002#408:10001,10002#409:10001,10002#"
    "501:10001,10002#502:10001,10002#503:10001,10002#504:10001,10002#505:10001,10002#506:10001,10002#507:10001,10002#508:10001,10002#509:10001,10002"
)


# ------------------------------- equip / god-weapon instances (TASK-0318)
#
# TASK-0316 contract: a maxed card's fight is cfg.fight x 1.90 + equip + godWeapon, where four
# star-6 equips contribute 1600 (+700 for the 幽泉怀渊 suit) and one god weapon contributes up to
# 10760 from its five feature ids.  Both need INSTANCES on the wire; the card row alone scores 0.
# The ids below come from the same ordinal+144 registration inference that produced the wrong
# 276/277/278, so the whole block is OFF by default and a single device run is its acceptance.
DEV_PLAY_PUSH_INSTANCES = False
EQUIP_INSTANCE_IDS = ("eq0318a", "eq0318b", "eq0318c", "eq0318d")
EQUIP_INSTANCE_CFG_IDS = (6122, 6222, 6322, 6422)
EQUIP_INSTANCE_LEVEL = 15
EQUIP_INSTANCE_FIGHT = 400
GOD_WEAPON_INSTANCE_ID = "gw0318a"
GOD_WEAPON_INSTANCE_CFG_ID = 1101
GOD_WEAPON_LEVEL = 31
GOD_WEAPON_RARE = 5
GOD_WEAPON_FEATURES = (66508, 66507, 68228, 68224, 68220)


def equip_instance_rows():
    """The four star-6 equip rows for S2C 1265 (44 values each)."""

    rows = []
    for instance_id, cfg_id in zip(EQUIP_INSTANCE_IDS, EQUIP_INSTANCE_CFG_IDS):
        row = [instance_id, int(cfg_id), EQUIP_INSTANCE_LEVEL, EQUIP_INSTANCE_FIGHT, 0,
               False, 100, 0, False]
        row.extend([0] * 35)
        rows.append(tuple(row))
    return tuple(rows)


def god_weapon_instance_row():
    """The god-weapon row for S2C 1625 (12 values, standalone)."""

    features = list(GOD_WEAPON_FEATURES)[:5]
    while len(features) < 5:
        features.append(0)
    return (GOD_WEAPON_INSTANCE_ID, GOD_WEAPON_INSTANCE_CFG_ID, GOD_WEAPON_LEVEL, 0,
            GOD_WEAPON_RARE, False, False, *features)


def instance_packets():
    """(name, fields) for the equip block, then the standalone god-weapon add."""

    packets = [("returnEquipStart", ())]
    for row in equip_instance_rows():
        packets.append(("returnEquipInfo", row))
    packets.append(("returnEquipEnd", ()))
    packets.append(("returnAddSingleGodWeapon", god_weapon_instance_row()))
    return tuple(packets)

def equip_fight_panel_fields(d_now: int | float) -> tuple[tuple, tuple]:
    """The (`S2C 1320`, `S2C 1321`) field tuples for one C2S 653 reply.

    Both are pure functions of the server clock, so the 1 Hz client re-send is answered
    with the same instant range every time and the panel never flickers between
    "refresh due" and "not due".
    """
    try:
        now = float(d_now)
    except (TypeError, ValueError) as exc:
        raise DevPlayArtifactError(
            REASON_WEEK_OUT_OF_RANGE, f"unreadable server time: {d_now!r}"
        ) from exc
    reset = now + float(EQUIP_FIGHT_PANEL_REFRESH_WINDOW_S)
    open_ids = "#".join(str(fb_id) for fb_id in EQUIP_FIGHT_PANEL_OPEN_FB_IDS)
    stage_fields = (
        now,                                  # refreshTime  (p) e_tbStageRefreshTime
        EQUIP_FIGHT_PANEL_STAGE_TABLE,        # strEquipFightStage   (a)
        EQUIP_FIGHT_PANEL_SCORE_NO_CLEAR_TABLE,  # strScoreNoClear    (a)
        False,                                # isDebugAllOpen       (y)
        open_ids,                             # strOpenFbIds         (a)
        reset,                                # dDataResetTime       (p) -> e_dNextFreshTime
        reset,                                # dDataFbResetTime     (p) -> e_dFbRefreshTime
    )
    random_fields = (
        now,                                  # refreshTime (p), unused by the handler
        EQUIP_FIGHT_PANEL_BUFF_TABLE,         # strBuffIds   (a)
        EQUIP_FIGHT_PANEL_SCORE_RULE_TABLE,   # strScoreRules(a)
    )
    return stage_fields, random_fields


# ------------------------------------------------------------------- failures

#: Closed refusal vocabulary.  Every failure names exactly one of these.
REASON_PROFILE_NOT_ALLOWED = "profile_not_allowed"
REASON_META_INVALID_JSON = "overlay_meta_invalid_json"
REASON_META_NOT_AN_OBJECT = "overlay_meta_not_an_object"
REASON_FORMAT_UNKNOWN = "overlay_format_unknown"
REASON_FORMAT_VERSION_UNSUPPORTED = "overlay_format_version_unsupported"
REASON_PROFILE_MISMATCH = "overlay_profile_mismatch"
REASON_ORIGIN_MISMATCH = "overlay_origin_mismatch"
REASON_VALUES_MISMATCH = "overlay_values_mismatch"
REASON_FLAGS_MISMATCH = "overlay_flags_mismatch"
REASON_NEVER_WRITE_MISMATCH = "overlay_never_write_mismatch"
REASON_SEEDED_AT_UNREADABLE = "overlay_seeded_at_unreadable"

#: The wire-value guard.  A server clock before the artifact epoch would yield a
#: week outside ``cfgArtifactDgnWeek`` (1..5); sending it would index a nil row
#: client-side, so the request is refused by name instead.
REASON_WEEK_OUT_OF_RANGE = "artifact_week_out_of_range"

DEV_PLAY_ARTIFACT_REFUSAL_REASONS = (
    REASON_PROFILE_NOT_ALLOWED,
    REASON_META_INVALID_JSON,
    REASON_META_NOT_AN_OBJECT,
    REASON_FORMAT_UNKNOWN,
    REASON_FORMAT_VERSION_UNSUPPORTED,
    REASON_PROFILE_MISMATCH,
    REASON_ORIGIN_MISMATCH,
    REASON_VALUES_MISMATCH,
    REASON_FLAGS_MISMATCH,
    REASON_NEVER_WRITE_MISMATCH,
    REASON_SEEDED_AT_UNREADABLE,
    REASON_WEEK_OUT_OF_RANGE,
)

#: The one payload key set, kept as data so validation and the report agree.
DEV_PLAY_ARTIFACT_PAYLOAD_KEYS = frozenset(
    {
        "format",
        "format_version",
        "profile_id",
        "origin",
        "seeded_at",
        "role_level",
        "entry",
        "provenance",
        "replaceable",
        "historical",
        "never_write",
    }
)

#: The canonical ``entry`` block.  Every value is a recovered shipped fact; the
#: block says *which* entry the overlay opens, never a wire value.
DEV_PLAY_ARTIFACT_ENTRY: dict[str, Any] = {
    "macro": ARTIFACT_DGN_MACRO,
    "function_open_id": ARTIFACT_DGN_FUNCTION_OPEN_ID,
    "open_level": ARTIFACT_DGN_OPEN_LEVEL,
    "panel_view": EQUIP_FIGHT_PANEL_VIEW,
    "entry_view": ARTIFACT_DGN_ENTRY_VIEW,
}

#: The canonical provenance block.  ``role_level`` is the synthetic choice; the
#: level *threshold* is the shipped row; the two 1526 formula values are
#: re-derived; the open flags are shipped config bytes plus the ``openN == 1``
#: rule; the 1527 state is a dev-play choice.
DEV_PLAY_ARTIFACT_PROVENANCE: dict[str, str] = {
    "role_level": PROVENANCE_SYNTHETIC_COMPATIBILITY,
    "role_level_threshold": PROVENANCE_OFFICIAL_RECOVERED,
    "week": ARTIFACT_WEEK_VALUE_PROVENANCE,
    "open_flags": (
        f"{ARTIFACT_OPEN_CFG_PROVENANCE}+{ARTIFACT_OPEN_RULE_PROVENANCE}"
    ),
    "race_tower_state": ARTIFACT_RACE_TOWER_VALUE_PROVENANCE,
}


class DevPlayArtifactError(RuntimeError):
    """The overlay cannot be applied.  ``reason`` is one closed-vocabulary word."""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in DEV_PLAY_ARTIFACT_REFUSAL_REASONS:  # pragma: no cover
            raise ValueError(f"unknown dev-play artifact refusal reason {reason!r}")
        super().__init__(message)
        self.reason = reason


def require_profile(profile_id: object) -> str:
    """Return ``dev-artifact-main``, or refuse every other name (incl. the QA one)."""

    if profile_id != DEV_PLAY_ARTIFACT_PROFILE_ID:
        raise DevPlayArtifactError(
            REASON_PROFILE_NOT_ALLOWED,
            f"the dev-play artifact overlay is restricted to "
            f"{DEV_PLAY_ARTIFACT_PROFILE_ID!r}; refusing {profile_id!r} "
            f"(the permanent qa-changan-main archive is never written)",
        )
    return DEV_PLAY_ARTIFACT_PROFILE_ID


# -------------------------------------------------------------- the week math


def get_cur_week(d_now: int | float) -> tuple[int, int, float, float]:
    """``pkgCfgArtifactDgn.getCurWeek`` re-implemented exactly (Lua doubles).

    The shipped body is::

        dNow = dNow - 316800
        aWeek = 3 * 24 * 60 * 60
        dInt = math.modf(dNow / aWeek)
        dUseSec = math.fmod(dNow, aWeek)
        dRestSec = aWeek - dUseSec
        dLoop = math.fmod(dInt, 5) + 1
        return dLoop, dInt, dUseSec, dRestSec

    ``math.modf`` truncates toward zero and ``math.fmod`` is C ``fmod`` (the
    remainder takes the dividend's sign), so this mirrors the client for the
    same server clock even outside the positive range.
    """

    shifted = float(d_now) - float(ARTIFACT_WEEK_EPOCH_OFFSET_S)
    integral = math.modf(shifted / float(ARTIFACT_WEEK_LOOP_S))[1]
    use_sec = math.fmod(shifted, float(ARTIFACT_WEEK_LOOP_S))
    rest_sec = float(ARTIFACT_WEEK_LOOP_S) - use_sec
    week = int(math.fmod(integral, float(ARTIFACT_WEEK_CYCLE))) + 1
    return week, int(integral), use_sec, rest_sec


def get_cur_week4_shop(d_now: int | float) -> tuple[int, int]:
    """``pkgCfgArtifactDgn.getCurWeek4Shop`` re-implemented exactly.

    The shipped body is::

        dNow = dNow - 316800
        aWeek = 1 * 24 * 60 * 60
        dInt = math.modf(dNow / aWeek)
        dLoop = math.fmod(dInt, 4) + 1
        return dLoop, dInt
    """

    shifted = float(d_now) - float(ARTIFACT_WEEK_EPOCH_OFFSET_S)
    integral = math.modf(shifted / float(ARTIFACT_SHOP_LOOP_S))[1]
    day = int(math.fmod(integral, float(ARTIFACT_SHOP_CYCLE))) + 1
    return day, int(integral)


def week_open_flags(week: int) -> tuple[bool, bool, bool, bool, bool]:
    """``cfgArtifactDgnWeek[week].open1..5`` as the ``y``-width booleans.

    A week outside 1..5 is not a closed row: the shipped table has no such key,
    so the caller must refuse rather than send five invented flags.
    """

    row = ARTIFACT_WEEK_OPEN.get(int(week))
    if row is None:
        raise DevPlayArtifactError(
            REASON_WEEK_OUT_OF_RANGE,
            f"artifact week {week!r} is outside cfgArtifactDgnWeek "
            f"{sorted(ARTIFACT_WEEK_OPEN)}; refusing to send open flags",
        )
    return tuple(bool(value) for value in row)


def week_and_open_fields(
    now_unix: int | float,
) -> tuple[int, int, bool, bool, bool, bool, bool]:
    """The 7 fields of ``S2C 1526 artifactDgnSendWeekAndOpen``.

    ``dWeek``/``dWeek4Shop`` are the client's own formulas evaluated on the
    server clock; nothing is pinned to a week literal.  ``open1..5`` must be
    booleans: the registration width is ``y`` and the client's
    ``getDgnsByWeek`` / ``artifactDgnSendWeekAndOpen`` store them as-is, so an
    integer ``0`` would still be true in Lua.
    """

    week, _integral, _use_sec, _rest_sec = get_cur_week(now_unix)
    shop_week, _shop_integral = get_cur_week4_shop(now_unix)
    # TASK-0488: the Owner wants every 昆仑 equipment dungeon open, so the
    # dev-play overlay announces all five towers regardless of the weekday row.
    flags = (True, True, True, True, True)
    return (week, shop_week, *flags)


def race_and_tower_fields() -> tuple[int, int, int, int, int, int]:
    """The 6 fields of S2C 1527 artifactDgnSendRaceAndTower for the dev-play overlay.

    TASK-0282: fields 1..5 announce the legal-and-open dev-play pair (race 3, tower 1)
    instead of the unplayable zero state -- see ARTIFACT_RACE_TOWER_DEV_PLAY_STATE.
    Field 6 is U-1, still a registration-only extra sent as 0 with no meaning.
    """

    return (*ARTIFACT_RACE_TOWER_DEV_PLAY_STATE, ARTIFACT_RACE_TOWER_FIELD_6)


def ach_info_fields() -> tuple[int, int, str]:
    """The 3 fields of S2C 1551 artifactDgnAchInfo (NNa)."""

    return ARTIFACT_ACH_INFO_FIELDS


# -------------------------------------------------------------------- overlay


@dataclass(frozen=True, slots=True)
class ArtifactOverlay:
    """One validated overlay.  Its values are canonical, never caller-chosen."""

    profile_id: str
    seeded_at: int

    @property
    def role_level(self) -> int:
        return ARTIFACT_DGN_OPEN_LEVEL

    @property
    def entry(self) -> dict[str, Any]:
        return dict(DEV_PLAY_ARTIFACT_ENTRY)

    def week_and_open_fields(
        self, now_unix: int | float
    ) -> tuple[int, int, bool, bool, bool, bool, bool]:
        return week_and_open_fields(now_unix)

    @property
    def race_tower_fields(self) -> tuple[int, int, int, int, int, int]:
        return race_and_tower_fields()

    def to_payload(self) -> dict[str, Any]:
        return overlay_payload(self.profile_id, self.seeded_at)


def overlay_payload(profile_id: str, seeded_at: int) -> dict[str, Any]:
    """The one canonical meta payload.  Provenance travels with every value."""

    return {
        "format": DEV_PLAY_ARTIFACT_META_FORMAT,
        "format_version": DEV_PLAY_ARTIFACT_META_FORMAT_VERSION,
        "profile_id": profile_id,
        "origin": DEV_PLAY_ARTIFACT_ORIGIN,
        "seeded_at": int(seeded_at),
        "role_level": ARTIFACT_DGN_OPEN_LEVEL,
        "entry": dict(DEV_PLAY_ARTIFACT_ENTRY),
        "provenance": dict(DEV_PLAY_ARTIFACT_PROVENANCE),
        "replaceable": True,
        "historical": False,
        "never_write": "qa-changan-main",
    }


def _int_value(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DevPlayArtifactError(
            REASON_VALUES_MISMATCH,
            f"{field} must be an int, not {value!r}",
        )
    return value


def parse_overlay_payload(raw: str, *, profile_id: str) -> ArtifactOverlay:
    """Validate one stored payload, or refuse with a named reason.

    The stored value is a *marker for the canonical dev-play state*, never a
    source of arbitrary wire values: a different level, a different entry view,
    a reworded provenance, an extra/missing key or a missing ``never_write`` is
    refused rather than sent or partially applied.
    """

    require_profile(profile_id)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise DevPlayArtifactError(
            REASON_META_INVALID_JSON, f"overlay meta is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise DevPlayArtifactError(
            REASON_META_NOT_AN_OBJECT,
            f"overlay meta must be a JSON object, not {type(payload).__name__}",
        )
    if payload.get("format") != DEV_PLAY_ARTIFACT_META_FORMAT:
        raise DevPlayArtifactError(
            REASON_FORMAT_UNKNOWN,
            f"overlay meta carries format {payload.get('format')!r}, not "
            f"{DEV_PLAY_ARTIFACT_META_FORMAT!r}",
        )
    if payload.get("format_version") != DEV_PLAY_ARTIFACT_META_FORMAT_VERSION:
        raise DevPlayArtifactError(
            REASON_FORMAT_VERSION_UNSUPPORTED,
            f"overlay meta format_version {payload.get('format_version')!r} is "
            f"not {DEV_PLAY_ARTIFACT_META_FORMAT_VERSION}",
        )
    if payload.get("profile_id") != profile_id:
        raise DevPlayArtifactError(
            REASON_PROFILE_MISMATCH,
            f"overlay meta names profile {payload.get('profile_id')!r}, not "
            f"{profile_id!r}",
        )
    if payload.get("origin") != DEV_PLAY_ARTIFACT_ORIGIN:
        raise DevPlayArtifactError(
            REASON_ORIGIN_MISMATCH,
            f"overlay meta origin {payload.get('origin')!r} is not "
            f"{DEV_PLAY_ARTIFACT_ORIGIN!r}",
        )
    if payload.get("never_write") != "qa-changan-main":
        raise DevPlayArtifactError(
            REASON_NEVER_WRITE_MISMATCH,
            "overlay meta must keep never_write='qa-changan-main', got "
            f"{payload.get('never_write')!r}",
        )
    if payload.get("replaceable") is not True or payload.get("historical") is not False:
        raise DevPlayArtifactError(
            REASON_FLAGS_MISMATCH,
            "overlay meta must be replaceable=true and historical=false",
        )
    seeded_at = _int_value(payload.get("seeded_at"), "seeded_at")
    if seeded_at < 0:
        raise DevPlayArtifactError(
            REASON_SEEDED_AT_UNREADABLE,
            f"overlay meta has a negative seeded_at {seeded_at}",
        )
    role_level = _int_value(payload.get("role_level"), "role_level")
    if role_level != ARTIFACT_DGN_OPEN_LEVEL:
        raise DevPlayArtifactError(
            REASON_VALUES_MISMATCH,
            f"overlay role_level {role_level} is not the frozen "
            f"{ARTIFACT_DGN_OPEN_LEVEL}",
        )
    if payload.get("entry") != DEV_PLAY_ARTIFACT_ENTRY:
        raise DevPlayArtifactError(
            REASON_VALUES_MISMATCH,
            f"overlay entry {payload.get('entry')!r} is not the frozen "
            f"{DEV_PLAY_ARTIFACT_ENTRY!r}",
        )
    if payload.get("provenance") != DEV_PLAY_ARTIFACT_PROVENANCE:
        raise DevPlayArtifactError(
            REASON_VALUES_MISMATCH,
            "overlay provenance must stay "
            f"{DEV_PLAY_ARTIFACT_PROVENANCE!r}; got {payload.get('provenance')!r}",
        )
    if set(payload) != DEV_PLAY_ARTIFACT_PAYLOAD_KEYS:
        raise DevPlayArtifactError(
            REASON_VALUES_MISMATCH,
            "overlay payload keys changed: "
            f"missing={sorted(DEV_PLAY_ARTIFACT_PAYLOAD_KEYS - set(payload))} "
            f"extra={sorted(set(payload) - DEV_PLAY_ARTIFACT_PAYLOAD_KEYS)}",
        )
    return ArtifactOverlay(profile_id=profile_id, seeded_at=seeded_at)


def load_overlay(store: RoleStore) -> ArtifactOverlay | None:
    """The stored overlay, ``None`` when absent, or a refusal if unusable.

    Absence is the normal "not enabled" case (the caller sends the frozen login
    stream and answers no artifactDgn packet).  A *present* value that does not
    validate is not absence: it raises so the caller can record the named
    refusal instead of hiding it.
    """

    raw = store.get_meta(DEV_PLAY_ARTIFACT_META_KEY)
    if raw is None:
        return None
    return parse_overlay_payload(
        raw, profile_id=DEV_PLAY_ARTIFACT_PROFILE_ID
    )


def seed_overlay(
    store: RoleStore,
    *,
    profile_id: str,
    time_source: Callable[[], float] | None = None,
) -> tuple[ArtifactOverlay, bool]:
    """Write the canonical overlay once (idempotent); return ``(overlay, created)``."""

    require_profile(profile_id)
    clock = time_source or time.time
    previous = load_overlay(store)
    if previous is not None:
        return previous, False
    overlay = ArtifactOverlay(profile_id=profile_id, seeded_at=int(clock()))
    store.set_meta(
        DEV_PLAY_ARTIFACT_META_KEY,
        json.dumps(overlay.to_payload(), ensure_ascii=False, sort_keys=True),
    )
    return overlay, True


def remove_overlay(store: RoleStore) -> bool:
    """Delete only this module's key; every other meta row is untouched."""

    return store.delete_meta(DEV_PLAY_ARTIFACT_META_KEY)


def describe_overlay(store: RoleStore) -> dict[str, Any] | None:
    """The validated overlay as a bounded dict, or ``None`` when absent."""

    overlay = load_overlay(store)
    if overlay is None:
        return None
    return overlay.to_payload()


# ------------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m revival_stub.dev_play_artifact",
        description=(
            "Seed, inspect or remove the explicit synthetic 神器副本 entry "
            "state of the separate dev-play archive dev-artifact-main.  The "
            "permanent qa-changan-main archive is never opened or written."
        ),
    )
    parser.add_argument(
        "command",
        choices=("seed", "status", "remove"),
        help="seed: write the overlay once; status: print it; remove: delete it",
    )
    parser.add_argument(
        "--profile",
        required=True,
        metavar="PROFILE-ID",
        help=f"the named durable play profile; only {DEV_PLAY_ARTIFACT_PROFILE_ID}",
    )
    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_profile(args.profile)
    except DevPlayArtifactError as exc:
        print(f"dev_play_artifact: refusing: {exc}", file=sys.stderr)
        return 2
    # Lazy import: `app` imports `tcp_stub`, which imports this module.
    from .app import default_data_dir

    try:
        profile = select_play_profile(
            args.profile,
            mode=PROFILE_MODE_RESUME,
            default_archive_dir=default_data_dir(),
        )
        store = profile.open_store()
    except (PlayProfileError, RoleStoreError) as exc:
        print(f"dev_play_artifact: refusing: {exc}", file=sys.stderr)
        return 2
    try:
        try:
            if args.command == "seed":
                overlay, created = seed_overlay(
                    store, profile_id=args.profile
                )
                _print_json(
                    {
                        "command": "seed",
                        "profile_id": args.profile,
                        "created": created,
                        "overlay": overlay.to_payload(),
                    }
                )
                return 0
            if args.command == "status":
                _print_json(
                    {
                        "command": "status",
                        "profile_id": args.profile,
                        "overlay": describe_overlay(store),
                    }
                )
                return 0
            removed = remove_overlay(store)
            _print_json(
                {
                    "command": "remove",
                    "profile_id": args.profile,
                    "removed": removed,
                }
            )
            return 0
        except DevPlayArtifactError as exc:
            # A stored value that does not validate is never overwritten by
            # `seed` and never guessed by `status`: name the reason, refuse.
            print(
                f"dev_play_artifact: refusing: {exc.reason}: {exc}",
                file=sys.stderr,
            )
            return 2
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover - the CLI entry itself
    raise SystemExit(main())


# ------------------------------------------- artifact tower entry (TASK-0270)
#
# The tower needs three things before its first node can be tapped, all proven statically by
# TASK-0264 / TASK-0268:
#   1. a fabao (法宝) in one of the team's three magic slots, because
#      pkgArtifactDgnMgr.isFabaoOK passes only for star >= 6 AND the run's race;
#   2. at least six race-1 (风) cards in the team, because both qualifying fabao rows carry
#      raceNumLimit = 6 and isCanSelectFabao counts them (pkgArtifactDgnMod.lua:160-219);
#   3. a legal path/node/team table, because an all-zero path makes showTower return early
#      and setArrow then indexes NEXT_NODE[0] without a guard (TASK-0264 correction 2).
#
# Every value below is a dev-play CHOICE: provenance synthetic_compatibility, replaceable,
# never historical.  The card ids are a confirmed wind template (TASK-0268 verification D1);
# six distinct instances of it satisfy the count without inventing unproven cfg ids.

TOWER_FABAO_INSTANCE_ID = "1"
# TASK-0292: the tower is race 3 (阴殿), and doEvtButton's isFabaoOK requires the team's fabao to
# share the tower's race: "if dCurRace == dRace and isActive and dStar >= 6".  224 干将莫邪 is
# star 6 but race 1, so the race test failed and the client never sent C2S 752 (run8's
# 限制条件 dialog).  cfg 214 落魂钟 is star 6 / race 3 / raceNumLimit 6 and is the fix.
TOWER_FABAO_CFG_ID = 214          # 落魂钟: star 6 / race 3 (阴) / raceNumLimit 6
TOWER_FABAO_ALTERNATE_CFG_ID = 215  # 九鬼迷魂幡: the other star-6 race-3 row
ARTIFACT_TOWER_RACE = 3
#: Verified race-3 card cfgs (TASK-0291 contract §4; 76 exist, these are the recovered sample).
#: isCanSelectFabao needs getRaceCardNum(3) >= 6, so at least six team slots must be these.
ARTIFACT_RACE3_CARD_CFG_IDS = frozenset(
    {1105, 2212, 2213, 2214, 2215, 2216, 3307, 3308, 3309, 3310})
TOWER_WIND_CARD_CFG_ID = 1101     # templete race == 1 (葱油包)
TOWER_CARD_INSTANCE_PREFIX = "wind"
TOWER_CARD_INSTANCE_COUNT = 6
TOWER_CARD_ORIGIN = "revival_local_dev_play_artifact_overlay"
#: (dCode, dLayer, dSn, evtId) triples: wipe, layer-1 entry (evt 150), layer-2 配表怪 (evt 56)
#: TASK-0351: the WHOLE tower 1 grid, straight from cfg (reports/task_0317...: 25 rows over
#: layers 1..12). Only three rows were ever sent, so every other tile was empty by design; the
#: client draws a slot only when a node row exists for it.
TOWER_NODE_EVENTS = (
    (1, 0, 0, 0),
    (2, 1, 1, 150),  # 入口 node 31011
    (2, 2, 1, 56),  # 配表怪 node 31021
    (2, 2, 2, 57),  # 配表怪 node 31022
    (2, 3, 1, 1),  # 精英怪 node 31031
    (2, 3, 2, 181),  # 捉迷藏 node 31032
    (2, 3, 3, 2),  # 精英怪 node 31033
    (2, 4, 1, 3),  # 精英怪 node 31041
    (2, 4, 2, 4),  # 精英怪 node 31042
    (2, 5, 1, 151),  # 马车 node 31051
    (2, 5, 2, 58),  # 配表怪 node 31052
    (2, 5, 3, 151),  # 马车 node 31053
    (2, 6, 1, 59),  # 配表怪 node 31061
    (2, 6, 2, 60),  # 配表怪 node 31062
    (2, 7, 1, 5),  # 精英怪 node 31071
    (2, 7, 2, 151),  # 马车 node 31072
    (2, 7, 3, 6),  # 精英怪 node 31073
    (2, 8, 1, 66),  # 配表怪 node 31081
    (2, 8, 2, 67),  # 配表怪 node 31082
    (2, 9, 1, 154),  # 迷宫 node 31091
    (2, 9, 2, 7),  # 精英怪 node 31092
    (2, 9, 3, 68),  # 配表怪 node 31093
    (2, 10, 1, 152),  # 温泉 node 31101
    (2, 10, 2, 153),  # 神秘巫师 node 31102
    (2, 11, 1, 196),  # Boss node 31111
    (2, 12, 1, 156),  # 通关宝箱 node 31121
)
#: 26 registered N; the client binds 19 path slots, the rest are U-F and sent as 0.
#: TASK-0351: the path row is the per-layer progress cursor. Only layer 1 has been walked on a fresh
#: archive, so slot 1 = 1 and the rest are 0; the node grid above is what fills the tiles.
TOWER_PATH_VALUES = (1,) + (0,) * 25
TOWER_TEAM_ORIGIN = "synthetic_compatibility"


def tower_card_instance_ids():
    return tuple(TOWER_CARD_INSTANCE_PREFIX + str(i + 1)
                 for i in range(TOWER_CARD_INSTANCE_COUNT))


def tower_team_csv(card_instance_ids):
    """The 18-value packTeam CSV: soul, 10 cards, 3 magic, 2 runes, talent, talentPage."""
    cards = list(card_instance_ids)[:10]
    while len(cards) < 10:
        cards.append("0")
    return ",".join(["0"] + cards + [TOWER_FABAO_INSTANCE_ID, "0", "0", "0", "0", "0", "0"])


def tower_magic_packets():
    """The fabao inventory push (45/46/47) the tower's own isFabaoOK gate resolves against.

    TASK-0285: the pre-commit tower frame (player opens the view from the entry page) runs
    showLimit -> isFabaoOK -> getFabaoCfgID(magic slot instance). With the team preview but no
    fabao push the client asserted "err strID:1" in pkgFabaoDataMgr.lua:106 because that
    instance was not known yet; sending the same 45/46/47 first makes it resolvable. None of
    these three opens a view.
    """

    fabao = (TOWER_FABAO_INSTANCE_ID, TOWER_FABAO_CFG_ID, 0, 0, 0) + (0,) * 16
    return (("returnMagicStart", ()),
            ("returnMagicInfo", fabao),
            ("returnMagicEnd", ()))


def tower_data_packets(path_values=None):
    """The path/node rows the tower view reads on its own (TASK-0283).

    The player can open the tower view from the entry page ("继续探索") BEFORE a team commit,
    and the device run showed that window throws pkgUIArtifactDgnTower.lua:219 in setArrow
    because NEXT_NODE/le_tbPath were still empty. These are pure data packets -- none of them
    opens a view -- so they can travel with the 744 reply without re-introducing the 312 ms
    auto-jump that 1529 caused (TASK-0281).
    """

    packets = [("artifactDgnSendPath", tuple(path_values or TOWER_PATH_VALUES))]
    for node in TOWER_NODE_EVENTS:
        packets.append(("artifactDgnSendNode", node))
    return tuple(packets)


def tower_entry_packets(card_instance_ids, path_values=None, team_csv=None):
    """The packets the team commit (745/746) sends, in order (1529 LAST)."""
    fabao = (TOWER_FABAO_INSTANCE_ID, TOWER_FABAO_CFG_ID, 0, 0, 0) + (0,) * 16
    packets = [
        ("returnMagicStart", ()),
        ("returnMagicInfo", fabao),
        ("returnMagicEnd", ()),
    ]
    # TASK-0283: the path/node rows now ride with the 744 reply, but the commit resends them
    # so a player who commits without opening the entry view still gets a complete tower.
    packets.extend(tower_data_packets(path_values))
    packets.append(("artifactDgnSendTeam", (team_csv or tower_team_csv(card_instance_ids),)))
    # 1529 opens the tower view and synchronously reads le_dRace/le_tbPath/le_tbCurTeam,
    # so every value above must already be on the wire (TASK-0264).
    packets.append(("artifactDgnSetRace", (0,)))
    return tuple(packets)


def card_row_cfg_ids(store, account_key, role_id, instance_ids):
    """{instance_id: card_cfg_id} for the given instances of this role (TASK-0295)."""

    if not instance_ids:
        return {}
    placeholders = ",".join("?" for _ in instance_ids)
    rows = store._conn.execute(
        "SELECT instance_id, card_cfg_id FROM card_instances "
        "WHERE account_key = ? AND role_id = ? AND instance_id IN (" + placeholders + ")",
        (account_key, role_id, *instance_ids),
    ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


def card_rows_for_team(store, account_key, role_id, instance_ids, team_csv):
    """The S2C 1537 rows for one team CSV: (dCode, a_type, strIns, maxHp, hpRate, tmpCard).

    le_tbCards is keyed by the CSV slot token, so a row's strIns must be that exact token and
    strTmpCard must be the card cfg id (a "0" there makes the client raise instead of alert).
    """

    slots = [slot for slot in str(team_csv).split(",")[1:11] if slot and slot != "0"]
    cfg_by_instance = card_row_cfg_ids(store, account_key, role_id, tuple(slots))
    rows = [(1, 0, "", 0, 0, "0")]
    for slot in slots:
        cfg_id = cfg_by_instance.get(slot)
        if cfg_id is None:
            continue
        rows.append((2, 0, slot, 10000, 10000, str(cfg_id)))
    return tuple(rows)


def boost_team(store, account_key, instance_ids, *, group_id=1):
    """Write a full game-team preset so a type-66 battle fields more than three cards.

    TASK-0303 contract: a type-66 battle uses pkgTeamDataMgr.tbTeamCard[getCurGroupID()] -- the
    GAME team -- fed by S2C 37 returnTeamInfo, which _send_team_snapshot copies verbatim from the
    archive's team_presets row. The artifact tower team (S2C 1538) is NOT on the roster path,
    which is why a ten-member tower team still fought with the three cards this row held.
    Constraints: at most eleven slots, and every id must already be in the client's tbCard
    (i.e. delivered by S2C 30). Returns the number of non-empty slots written.
    """

    slots = [str(instance_id) for instance_id in instance_ids if instance_id][:11]
    while len(slots) < 11:
        slots.append("")
    store._conn.execute(
        "UPDATE team_presets SET card_id1 = ?, card_id2 = ?, card_id3 = ?, card_id4 = ?, "
        "card_id5 = ?, card_id6 = ?, card_id7 = ?, card_id8 = ?, card_id9 = ?, "
        "card_id10 = ?, card_id11 = ?, origin = ?, updated_at = ? "
        "WHERE account_key = ? AND group_id = ?",
        (*slots, DEV_PLAY_BOOST_ORIGIN, int(time.time()), account_key, int(group_id)),
    )
    store._conn.commit()
    return len([slot for slot in slots if slot])

def commission_tower_cards(store, record, *, now=None):
    """Own TOWER_CARD_INSTANCE_COUNT wind cards for this dev-play role (idempotent)."""
    created = []
    for instance_id in tower_card_instance_ids():
        row, outcome = store.record_card_instance(
            record=record, instance_id=instance_id, card_cfg_id=TOWER_WIND_CARD_CFG_ID,
            origin=TOWER_CARD_ORIGIN)
        created.append((instance_id, outcome))
    return tuple(created)


# ------------------------------------ artifact tower: enemy team info (TASK-0278)
#
# The first node tap is C2S 752 artifactDgnEnemyTeamInfo "NNNN" (race, tower, layer, sn). It
# registers with two arguments, so it has NO lock partner: an unanswered one costs nothing but
# the tap. The answer is S2C 1543 artifactDgnEnemyTeamInfo "NNNNaa" and its strTeam decides the
# client's branch: "default" makes the client open pkgUIArtifactDgnTeamInfo4Conf, whose confirm
# sends C2S 69 (PVETYPE_GOD_FIGHT=66) directly -- no 747/1539. That default branch needs
# cfgArtifactDgnEvent[evtId].defaultStageId > 0, which holds exactly for the layer-2 nodes of
# towers 1/2 (TASK-0264), so "default" is the shortest legal route to the first artifact fight.
ARTIFACT_ENEMY_TEAM_DEFAULT = "default"
ARTIFACT_ENEMY_TEAM_OPP_HP = "0"
ARTIFACT_ENEMY_TEAM_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY


# ------------------------------- god fight (type 66) payload (TASK-0296)
#
# RUN9 showed initGodFightData cloning nil: S2C 89 position 8 (p1) is the STAGE id, and the stub
# echoed the request's tower id (1) into it.  pkgPveEnterData:699 does
# getStage(p1) -> cfgArtifactDgnStage[dStageId] (bare lookup), so 1 is not a row.
# For this tower the client's own config derives it:
#   cfgArtifactDgnNode[tower][layer][sn].eventGroup -> cfgArtifactDgnEvent[group].defaultStageId
# TASK-0294 contract: (tower 1, layer 2, sn 1) -> eventGroup 56 -> event 56 (eventType 12) ->
# defaultStageId 101, whose row has five monsters (ids 86325/86330/86331/86332/86336).
# Position 18 (s2) is the enemy HP table: an EMPTY string sets currHpPercent 0 for every slot,
# and pkgBattleDataInc only instances a monster when hp/percent is non-zero, so the battle would
# load with zero enemies.  10000 per slot = full HP.
ARTIFACT_GOD_FIGHT_STAGE_BY_NODE = {
    (1, 2, 1): 101,
    (1, 2, 2): 101,
    (1, 2, 3): 101,
    (2, 2, 1): 101,
    (2, 2, 2): 101,
    (2, 2, 3): 101,
}
ARTIFACT_GOD_FIGHT_DEFAULT_STAGE = 101
ARTIFACT_GOD_FIGHT_MONSTER_COUNT = 5
ARTIFACT_GOD_FIGHT_PROVENANCE = PROVENANCE_DETERMINISTIC_RECOVERED


# ------------------------------ win reward (TASK-0299, TASK-0298 contract)
#
# S2C 96 is a pure no-op for pveOrPvpType 66 but it is the ONLY unlock of the locked C2S 67
# (15 s lock), so it must still be sent.  The reward travels on the generic gain-info trio gated
# on dGainType 596 (ARTIFACT_DGN): 81 start -> 83 per slot -> 82 end, and the only display site
# is pkgArtifactDgnMgr.warEnd -> doCheckYiwuAndReward -> pkgGainInfoMgr.getInfoAndClear(596).
# cfgArtifactDgnEvent[56].rewardId = 60001, whose three slots are the values below; stage 101
# itself carries no reward column.
ARTIFACT_GAIN_TYPE = 596
ARTIFACT_REWARD_ID = 60001
ARTIFACT_REWARD_SLOTS = ((171, 0, "", 30), (8886, 300, "", 1), (7, 0, "", 210))
ARTIFACT_REWARD_PROVENANCE = PROVENANCE_DETERMINISTIC_RECOVERED


def artifact_grant_key(role_id, tower, layer, sn):
    """The meta key that makes one (tower, layer, sn) pay at most once."""

    return f"artifact_dgn_grant:{int(role_id)}:{int(tower)}:{int(layer)}:{int(sn)}"


def artifact_reward_rows():
    """The S2C 83 rows for reward 60001: (dRewardType, dCfgId, strInstanceId, dNum)."""

    return ARTIFACT_REWARD_SLOTS

# ------------------------------- advance-piece sync (TASK-0312)
#
# The bag's 其他 page renders 金色碎片 (cfg 1000) and 银色碎片 (cfg 2000) out of
# pkgAdvanceMaterialInfo.tbAdvPiece, a NAMESPACE global that only S2C 276 can fill (the cfg manager
# writes its own namespace, so the shipped advancePiece.max can never reach it). Without 276 the
# page raises on nil and no row exists to tap, which is what RUN16 measured.
ADV_PIECE_ROWS = ((1000, 5), (2000, 3))
#: TASK-0315: the 276/277/278 ids were anchored, not captured, and RUN18's login abort appeared
#: only after this block joined the stream. Off until a device run proves the ids.
PUSH_ADV_PIECE_ON_LOGIN = False
ADV_PIECE_PROVENANCE = PROVENANCE_DETERMINISTIC_RECOVERED


def adv_piece_packets():
    """(name, fields) for the advance-piece sync: start, one info row per material, end."""

    packets = [("returnAdvPieceStart", ())]
    for cfg_id, count in ADV_PIECE_ROWS:
        packets.append(("returnAdvPieceInfo", (int(cfg_id), int(count))))
    packets.append(("returnAdvPieceEnd", ()))
    return tuple(packets)

def god_fight_stage_id(tower, layer, sn):
    """The stage id the type-66 battle must carry in S2C 89 position 8."""

    return ARTIFACT_GOD_FIGHT_STAGE_BY_NODE.get(
        (int(tower), int(layer), int(sn)), ARTIFACT_GOD_FIGHT_DEFAULT_STAGE)


#: Dev-play enemy HP scale for the artifact fight, in the same 10000 = 100 % unit the client uses.
#:
#: TASK-0307: the first fight's DPS maths (reports/run10 and run13) makes stage 101 unwinnable for
#: this archive: three starters at the legal ceiling do about 6 000 damage against five level-10
#: monsters at full HP, and the reserves that would help only enter on a later full-round check
#: (TASK-0305), which a two-round fight never reaches.  A server-declared enemy HP value is the one
#: lever that changes the fight without fabricating any client state, so the dev-play overlay lowers
#: it.  It stays labelled: the reward path, the settlement pairing and the tower logic are unchanged.
ARTIFACT_GOD_FIGHT_HP_PERCENT = 2500


def god_fight_hp_csv(count=ARTIFACT_GOD_FIGHT_MONSTER_COUNT, percent=None):
    """S2C 89 position 18: one 'slot,percent' pair per monster (10000 = full HP)."""

    value = ARTIFACT_GOD_FIGHT_HP_PERCENT if percent is None else int(percent)
    return ";".join(f"{index},{int(value)}" for index in range(1, int(count) + 1))

#: TASK-0352: evtId -> cfg eventType for the whole tower-1 grid (contract task_0317). The 747
#: answer branches on this; an id missing here must NOT be treated as a battle (live session
#: 2026-10-27 00:2x: every non-battle tile started a fight because the map only knew three ids
#: and the handler defaulted to 'battle').
ARTIFACT_EVT_TYPES = {
    150: 1,  # 入口 (layer 1 sn 1)
    56: 12,  # 配表怪 (layer 2 sn 1)
    57: 12,  # 配表怪 (layer 2 sn 2)
    1: 6,  # 精英怪 (layer 3 sn 1)
    181: 15,  # 捉迷藏 (layer 3 sn 2)
    2: 6,  # 精英怪 (layer 3 sn 3)
    3: 6,  # 精英怪 (layer 4 sn 1)
    4: 6,  # 精英怪 (layer 4 sn 2)
    151: 7,  # 马车 (layer 5 sn 1)
    58: 12,  # 配表怪 (layer 5 sn 2)
    151: 7,  # 马车 (layer 5 sn 3)
    59: 12,  # 配表怪 (layer 6 sn 1)
    60: 12,  # 配表怪 (layer 6 sn 2)
    5: 6,  # 精英怪 (layer 7 sn 1)
    151: 7,  # 马车 (layer 7 sn 2)
    6: 6,  # 精英怪 (layer 7 sn 3)
    66: 12,  # 配表怪 (layer 8 sn 1)
    67: 12,  # 配表怪 (layer 8 sn 2)
    154: 13,  # 迷宫 (layer 9 sn 1)
    7: 6,  # 精英怪 (layer 9 sn 2)
    68: 12,  # 配表怪 (layer 9 sn 3)
    152: 8,  # 温泉 (layer 10 sn 1)
    153: 9,  # 神秘巫师 (layer 10 sn 2)
    196: 10,  # Boss (layer 11 sn 1)
    156: 11,  # 通关宝箱 (layer 12 sn 1)
}
ARTIFACT_BATTLE_EVT_TYPES = frozenset({6, 10, 12})
ARTIFACT_PATH_WIDTH = 26


def event_type_for(evt_id):
    """The cfg eventType of one evtId, or None when this map does not cover it."""

    return ARTIFACT_EVT_TYPES.get(int(evt_id))


def path_fields_with(layer, sn, base=None):
    """The 26-value S2C 1531 path with path[layer] = sn (1-based)."""

    values = list(base or TOWER_PATH_VALUES)
    while len(values) < ARTIFACT_PATH_WIDTH:
        values.append(0)
    values = values[:ARTIFACT_PATH_WIDTH]
    index = int(layer) - 1
    if 0 <= index < ARTIFACT_PATH_WIDTH:
        values[index] = int(sn)
    return tuple(values)


def do_evt_fields(tower, layer, sn, *, is_fight=0):
    """The 7 fields of S2C 1532 artifactDgnDoEvt."""

    return (0, int(tower), int(layer), int(sn), 0, 0, int(is_fight))

def artifact_enemy_team_info_fields(layer, sn, cur_sn=0, *, code=0):
    """S2C 1543 artifactDgnEnemyTeamInfo "NNNNaa": (code, layer, sn, curSn, team, oppHp)."""
    return (int(code), int(layer), int(sn), int(cur_sn),
            ARTIFACT_ENEMY_TEAM_DEFAULT, ARTIFACT_ENEMY_TEAM_OPP_HP)
# ------------------------------------------------- dev-play boost (TASK-0286)
#
# The Owner asked for the tower fight to be forced through on the disposable dev-play copy:
# "raise the member values to 999999 and push through".  This only ever touches the
# dev-artifact-main copy (the caller passes its own store), never qa-changan-main, and every
# boosted row keeps its existing origin column so the archive itself still says where the card
# came from.  The level cap checks of apply_card_strength/apply_card_advance are deliberately
# NOT used here: this is a dev-play overlay value, not a legal game progression.
#
# TASK-0289 correction: a literal 999999 is NOT playable. RUN6 logged 369 x
# "pkgCardDataMgr.lua:612 getCardExpByCfgId: attempt to index field (a nil value)" -- one per
# card -- and then pkgMainUIMgr.init aborted, so the main city had no power number and every
# button was dead. The client only has the same LEVEL_EXP rows this module already carries, so
# the legal ceiling per card is the length of its star row. The boost therefore clamps to that
# ceiling by default and never writes a level the client has no config for.
DEV_PLAY_BOOST_LEVEL = None  # None = the legal ceiling per card
DEV_PLAY_BOOST_ORIGIN = "revival_local_dev_play_boost"


def legal_boost_level(card_cfg_id):
    """The highest card level the client cardLv table has for this card star."""

    star = int(card_star_rare(int(card_cfg_id))[0])
    # TASK-0290: the ceiling is max_level(star), NOT len(LEVEL_EXP[star]).  cardLv.max has the
    # eleven exp values for levels 0..10, so len(table) == 11 is one past the legal last level:
    # RUN7 wrote 11 and BOTH sides rejected it -- the client logged 369 x getCardExpByCfgId nil,
    # and this server refused its own login push with
    # "card cfg 4526 level 11 advance 0 has no recovered cardFightRatio row".
    return max(1, max_level(star))


def race_card_instance_ids(store, account_key, role_id, limit=10):
    """Owned instances whose card cfg is race 3 (阴), best level first (TASK-0292)."""

    if not ARTIFACT_RACE3_CARD_CFG_IDS:
        return ()
    placeholders = ",".join("?" for _ in ARTIFACT_RACE3_CARD_CFG_IDS)
    rows = store._conn.execute(
        "SELECT instance_id FROM card_instances WHERE account_key = ? AND role_id = ? "
        "AND card_cfg_id IN (" + placeholders + ") ORDER BY level DESC, instance_id LIMIT ?",
        (account_key, role_id, *sorted(ARTIFACT_RACE3_CARD_CFG_IDS), int(limit)),
    ).fetchall()
    return tuple(row[0] for row in rows)

def top_card_instance_ids(store, account_key, role_id, limit=10):
    """The role's highest-level card instances, best first (up to ``limit``)."""

    rows = store._conn.execute(
        "SELECT instance_id FROM card_instances WHERE account_key = ? AND role_id = ? "
        "ORDER BY level DESC, instance_id LIMIT ?",
        (account_key, role_id, int(limit)),
    ).fetchall()
    return tuple(row[0] for row in rows)


# ------------------------------- card HP/ATK boost (TASK-0314)
#
# The Owner asked for the cards' HP and ATK to be pushed to 999999 instead of the level, and the
# client proves that is the safe lever: pkgCardDetailHelper.getCardHpAndAtk adds the wire's learn
# values DIRECTLY --
#     hp0 = pkgCfgCardMgr.getHp(cfg, level, advance, add) + (dLearnHp or 0)
#     atk0 = ... + (dLearnAtk or 0)
# -- so they never index a per-level table the way level 999999 and advance 4 did (both killed
# pkgMainUIMgr.init on device). S2C 30 fields 17/18 (0-based 16/17) are exactly learn_atk and
# learn_hp (role_store.card_wire_row), so the archive write alone reaches the client at login.
DEV_PLAY_CARD_HP_ATK = 999999


def boost_card_hp_atk(store, account_key, role_id, *, value=DEV_PLAY_CARD_HP_ATK):
    """Set every card learn_atk and learn_hp to value (dev-play only). Returns row count."""

    amount = max(0, int(value))
    cursor = store._conn.execute(
        "UPDATE card_instances SET learn_atk = ?, learn_hp = ?, origin = ? "
        "WHERE account_key = ? AND role_id = ?",
        (amount, amount, DEV_PLAY_BOOST_ORIGIN, account_key, role_id),
    )
    store._conn.commit()
    return int(cursor.rowcount or 0)

def legal_advance_level(card_cfg_id):
    """The highest advance level BOTH tables can price, verified by probing card_fight.

    TASK-0301: RUN11 proved advance_max_level(star)=4 is NOT client-legal. The client's
    pkgCfgCardMgr.getCardFightRatio ignores level once advance > 0 and looks up
    cardFightRatio[1]["adv"..adv], whose rows are adv1..adv3 only, so an advance of 4 makes
    pkgCardDataMgr.getCardFight assert "card no level ratio 10" on the first card of the main-city
    init: no power number, badge stuck at 30, and the screen stops accepting touches.
    The probe below asks this server's own recovered table (team_power.ADVANCE_RATIO, which
    mirrors that client row), so a pair that cannot be priced can never be written again.
    """

    star = int(card_star_rare(int(card_cfg_id))[0])
    level = legal_boost_level(card_cfg_id)
    best = 0
    for advance in range(0, int(advance_max_level(star)) + 1):
        try:
            card_fight(int(card_cfg_id), level, advance)
        except Exception:
            break
        best = advance
    return best


def boost_cards(store, account_key, role_id, *, level=DEV_PLAY_BOOST_LEVEL,
                advance=True):
    """Raise every card of this role to the highest LEGAL level (dev-play only).

    ``level`` is clamped per card to legal_boost_level(); None means that ceiling.
    Returns the number of rows touched.
    """

    rows = store._conn.execute(
        "SELECT instance_id, card_cfg_id FROM card_instances "
        "WHERE account_key = ? AND role_id = ?",
        (account_key, role_id),
    ).fetchall()
    touched = 0
    for instance_id, card_cfg_id in rows:
        ceiling = legal_boost_level(card_cfg_id)
        value = ceiling if level is None else min(max(1, int(level)), ceiling)
        advance_value = legal_advance_level(card_cfg_id) if advance else None
        if advance_value is None:
            store._conn.execute(
                "UPDATE card_instances SET level = ?, exp = 0, origin = ? "
                "WHERE account_key = ? AND role_id = ? AND instance_id = ?",
                (value, DEV_PLAY_BOOST_ORIGIN, account_key, role_id, instance_id),
            )
        else:
            store._conn.execute(
                "UPDATE card_instances SET level = ?, exp = 0, advance_level = ?, origin = ? "
                "WHERE account_key = ? AND role_id = ? AND instance_id = ?",
                (value, advance_value, DEV_PLAY_BOOST_ORIGIN, account_key, role_id,
                 instance_id),
            )
        touched += 1
    store._conn.commit()
    return touched

# TASK-0348: the event a cleared layer's successor reuses. A node row carries only
# (dCode, dLayer, dSn, evtId) and the client resolves the event by id, so reusing the known-battle
# event keeps the next layer playable without inventing config.
TOWER_REPEAT_EVT_ID = 56


def path_fields_for_layer(layer, sn):
    """The 26-value S2C 1531 path with path[layer] = sn, everything else from the base row."""

    values = list(TOWER_PATH_VALUES)
    index = int(layer) - 1
    if 0 <= index < len(values):
        values[index] = int(sn)
    return tuple(values)


def tower_node_rows_for_layer(layer):
    """The S2C 1530 rows of one layer, from the cfg grid (TASK-0352)."""

    rows = [row for row in TOWER_NODE_EVENTS
            if row[0] == 2 and int(row[1]) == int(layer)]
    return tuple(rows)


# TASK-0353: cleared-layer persistence. The tower's progress is (layer, sn) rows the client
# walks; keeping them in the archive meta table is what makes a cleared layer survive a relogin.
ARTIFACT_CLEAR_META_PREFIX = "artifact_dgn_clear"


def artifact_clear_key(role_id, tower, layer, sn):
    return (f"{ARTIFACT_CLEAR_META_PREFIX}:{int(role_id)}:{int(tower)}:"
            f"{int(layer)}:{int(sn)}")


def record_clear(store, role_id, tower, layer, sn):
    """Write one cleared node; returns True when it was new."""

    key = artifact_clear_key(role_id, tower, layer, sn)
    if store.get_meta(key) is not None:
        return False
    store.set_meta(key, "1")
    return True


def cleared_path(store, role_id, tower):
    """The 26-slot path row built from the archive: path[layer] = highest cleared sn."""

    values = [0] * len(TOWER_PATH_VALUES)
    # Two journals feed the same path: the explicit clear rows written from TASK-0353 on, and
    # the reward-grant ledger that already existed. An archive that cleared layers before the
    # clear rows existed must not look untouched (live session: grants for layers 2/4/6/8/11 and
    # nothing to show for them).
    prefixes = (f"{ARTIFACT_CLEAR_META_PREFIX}:{int(role_id)}:{int(tower)}:",
                f"artifact_dgn_grant:{int(role_id)}:{int(tower)}:")
    for prefix in prefixes:
        for key, _value in store.meta_with_prefix(prefix):
            _, _rid, _tower, layer_text, sn_text = key.rsplit(":", 4)
            try:
                layer = int(layer_text)
                sn = int(sn_text)
            except ValueError:
                continue
            if 1 <= layer <= len(values):
                values[layer - 1] = max(values[layer - 1], sn)
    # TASK-0355: a fresh archive has no clear rows, and an all-zero path makes the tower view
    # render nothing at all (live report 00:5x: "the map is empty"). Slot 1 is the layer-1
    # entry and is walkable from the very first login.
    if values and values[0] < 1:
        values[0] = 1
    return tuple(values)


def max_sn_for_layer(layer):
    """The highest sn the cfg grid gives this layer (TASK-0354)."""

    sns = [int(row[2]) for row in TOWER_NODE_EVENTS
           if row[0] == 2 and int(row[1]) == int(layer)]
    return max(sns) if sns else 0


def path_after_clear(store, role_id, tower, layer, sn):
    """The path row a clear of (layer, sn) should produce.

    Clearing a node does NOT skip the rest of its layer: layer 2 has two nodes, so clearing sn 1
    must leave the player choosing sn 2 in the SAME layer. Only the last node of a layer opens
    the next one (live session 2026-10-27 00:4x: the old code jumped the player past layer 2).
    """

    # The slot means "how many nodes of this layer are finished", nothing else. Writing sn+1
    # there made a half-cleared layer look complete and the view jumped to the next one (live
    # report 01:0x). Only finishing the layer's LAST node opens the next layer.
    # TASK-0363: the slot counts FINISHED nodes of its own layer and nothing else. Writing a 1
    # into the next layer pre-opened it, and the client's getNextLayer (highest non-zero slot
    # + 1) then behaved as if a node up there were already done -- the view jumped after every
    # clear (live 02:5x). Opening the next layer needs no write at all: once path[layer] equals
    # the layer's node count, getNextLayer simply returns layer + 1.
    values = list(cleared_path(store, role_id, tower))
    index = int(layer) - 1
    if 0 <= index < len(values):
        values[index] = max(values[index], int(sn))
    return tuple(values)


# TASK-0367: (layer, sn) -> the stage a battle node actually declares. The live session fought
# the same monsters everywhere because the 89 reply always used stage 101; cfg gives each
# 配表怪 node its own stage (101/102/103/104/105/111/112/113), so the fights differ.
ARTIFACT_NODE_STAGES = {
    (2, 1): 101,
    (2, 2): 102,
    (5, 2): 103,
    (6, 1): 104,
    (6, 2): 105,
    (8, 1): 111,
    (8, 2): 112,
    (9, 3): 113,
}


#: TASK-0424: cfgArtifactDgnNode carries no stage at all (columns are id/race/tower/layer/sn/
#: eventGroup/isHard) and the elite and boss events declare defaultStageId 0, so the original
#: server must have picked one.  Rather than fighting stage 101 on every elite floor, a node with
#: no declared stage takes the stage whose id matches its layer (101..112 are all real rows); the
#: pick is a documented stand-in, never a claim about the original.
ARTIFACT_STAGE_FIRST = 101
ARTIFACT_STAGE_LAST = 190


def layer_stage_id(layer):
    """The stand-in stage for a node whose own event declares none."""

    stage = ARTIFACT_STAGE_FIRST + max(0, int(layer) - 1)
    return min(stage, ARTIFACT_STAGE_LAST)


def node_stage_id(layer, sn, fallback=None):
    declared = ARTIFACT_NODE_STAGES.get((int(layer), int(sn)))
    if declared is not None:
        return int(declared)
    if fallback is not None:
        return int(fallback)
    return layer_stage_id(layer)

#: TASK-0375c: cfg soul 101 sfmId1 (contract task_0374).
PROTAGONIST_SOUL_SKILL_ID = 11001


# TASK-0376: where each artifact reward row is stored (contract task_0375).
#   type 7    MONEY        -> role_wallet value_name 'money'      (typed home exists)
#   type 8886 SPECIAL_CARD -> card_special_inventory cfg 300      (typed home exists)
#   type 171  ARTIFACT DGN POINT -> no typed table yet; kept in the archive meta table
#             under ARTIFACT_POINT_META_KEY (the same side-channel pattern goodsUse uses)
ARTIFACT_REWARD_MONEY_TYPE = 7
ARTIFACT_REWARD_SPECIAL_CARD_TYPE = 8886
ARTIFACT_REWARD_POINT_TYPE = 171
ARTIFACT_SPECIAL_CARD_CFG_ID = 300
ARTIFACT_POINT_META_KEY = "artifact_dgn_point"


def artifact_reward_writes(rows):
    """Split the gain rows into (money, special_card, point) totals."""

    money = special = point = 0
    for row in rows:
        reward_type, _cfg_id, _instance, num = row
        if int(reward_type) == ARTIFACT_REWARD_MONEY_TYPE:
            money += int(num)
        elif int(reward_type) == ARTIFACT_REWARD_SPECIAL_CARD_TYPE:
            special += int(num)
        elif int(reward_type) == ARTIFACT_REWARD_POINT_TYPE:
            point += int(num)
    return money, special, point

# TASK-0387: the reward each tower node actually declares (contract task_0384).
# (layer, sn) -> cfg slots (rewardType, itemId, num); the client filters type>0 and num>0.
ARTIFACT_REWARD_BY_NODE = {
    (2, 1): ((171, 0, 30), (8886, 300, 1), (7, 0, 210)),
    (2, 2): ((171, 0, 30), (8886, 300, 1), (7, 0, 210)),
    (3, 1): ((171, 0, 35), (7, 0, 220)),
    (3, 3): ((171, 0, 35), (7, 0, 220)),
    (4, 1): ((171, 0, 40), (8886, 301, 1), (7, 0, 230)),
    (4, 2): ((171, 0, 40), (8886, 301, 1), (7, 0, 230)),
    (5, 2): ((171, 0, 45), (8886, 300, 1), (7, 0, 240)),
    (6, 1): ((171, 0, 50), (7, 0, 250)),
    (6, 2): ((171, 0, 50), (7, 0, 250)),
    (7, 1): ((171, 0, 55), (8886, 301, 1), (7, 0, 260)),
    (7, 3): ((171, 0, 55), (8886, 301, 1), (7, 0, 260)),
    (8, 1): ((171, 0, 60), (8886, 300, 1), (7, 0, 270)),
    (8, 2): ((171, 0, 60), (8886, 300, 1), (7, 0, 270)),
    (9, 2): ((171, 0, 65), (7, 0, 280)),
    (9, 3): ((171, 0, 65), (7, 0, 280)),
    (11, 1): ((164, 120, 1), (7, 0, 1000)),
    (12, 1): ((171, 0, 150), (8886, 302, 1), (173, 0, 200)),
}

#: TASK-0387: the entry/minigame fallback row the contract names for a node whose event
#: carries rewardId 0 (artifactReward[50007] = money 1000 + special card 301 x1).
ARTIFACT_FALLBACK_REWARD_ID = 50007
ARTIFACT_FALLBACK_SLOTS = ((7, 0, 1000), (8886, 301, 1))


def artifact_reward_rows_for(layer, sn, *, fallback=False):
    """The cfg reward slots of one tower node (contract task_0384).

    rewardId 0 means the node pays nothing on its own; a non-battle node may fall back to
    the entry/minigame row when the caller asks for it."""

    slots = ARTIFACT_REWARD_BY_NODE.get((int(layer), int(sn)))
    if slots is None:
        slots = ARTIFACT_FALLBACK_SLOTS if fallback else ()
    return tuple((int(t), int(i), "", int(n)) for t, i, n in slots)

#: TASK-0394: reward type 9 = PIECE (a card 碎片).
UNCHARTED_PIECE_REWARD_TYPE = 9

# TASK-0401: artifact-dungeon shop shelves (contract task_0392, cfgArtifactDgnShopGoods).
# Each shelf keeps EVERY row: C2S 756 carries the goods id it was offered, so the buy
# must resolve (shelf, goods) instead of assuming one item per shelf.
ARTIFACT_SHOP_SHELVES = (101, 102, 103, 104, 105, 106, 107, 108)
ARTIFACT_SHOP_REFRESH_SECONDS = 86400

#: shelf -> tuple of (goods id, reward type, cfg id, god-weapon id, cost type, cost, name)
ARTIFACT_SHOP_GOODS = {
    101: (
        (1, 164, 123, 1101, 171, 1800, '华胥剑'),
        (6, 164, 124, 1111, 171, 1800, '风雨之息'),
        (11, 164, 125, 1121, 171, 1800, '青龙偃月刀'),
        (16, 164, 126, 1131, 171, 1800, '夜魔角'),
        (33, 164, 156, 1331, 171, 1800, '赤朱绫 '),
        (42, 164, 199, 1481, 171, 1800, '汐风镜'),
    ),
    102: (
        (2, 164, 127, 1141, 171, 1800, '不死药'),
        (7, 164, 128, 1151, 171, 1800, '琉璃灯'),
        (12, 164, 129, 1161, 171, 1800, '射日箭'),
        (17, 164, 130, 1291, 171, 1800, '齐天旗'),
        (34, 164, 181, 1391, 171, 1800, '玉竹情'),
    ),
    103: (
        (3, 164, 131, 1171, 171, 1800, '天狐尾'),
        (8, 164, 132, 1181, 171, 1800, '缚魂锁'),
        (13, 164, 133, 1191, 171, 1800, '神乐铃'),
        (18, 164, 134, 1201, 171, 1800, '鬼军印'),
        (25, 164, 143, 1301, 171, 1800, '杀生刃'),
        (41, 164, 194, 1461, 171, 1800, '判官笔'),
    ),
    104: (
        (4, 164, 135, 1211, 171, 1800, '天羽衣'),
        (9, 164, 136, 1221, 171, 1800, '封印之石'),
        (14, 164, 137, 1231, 171, 1800, '火生莲'),
        (19, 164, 138, 1241, 171, 1800, '菩提果'),
        (26, 164, 144, 1311, 171, 1800, '狍鸮叉'),
    ),
    105: (
        (5, 164, 139, 1251, 171, 1800, '金箍'),
        (10, 164, 140, 1261, 171, 1800, '罪龙鳞'),
        (15, 164, 141, 1271, 171, 1800, '血之羽'),
        (20, 164, 142, 1281, 171, 1800, '天魔刃'),
        (27, 164, 145, 1321, 171, 1800, '御兽诀'),
    ),
    106: (
        (21, 8886, 303, 0, 171, 500, 'None'),
        (22, 8886, 302, 0, 171, 300, 'None'),
        (23, 8886, 301, 0, 171, 150, 'None'),
        (24, 8886, 300, 0, 171, 80, 'None'),
    ),
    107: (
        (28, 164, 147, 1341, 173, 1500, '金雕翎'),
        (29, 164, 148, 1351, 173, 1500, '缥缈葫'),
        (30, 164, 149, 1361, 173, 1500, '月影寒刃'),
        (31, 164, 150, 1371, 173, 1500, '天问录'),
        (32, 164, 151, 1381, 173, 1500, '弑魂锏'),
    ),
    108: (
        (35, 164, 182, 1401, 173, 1500, '火焰印'),
        (36, 164, 183, 1411, 173, 1500, '冰晶剑齿'),
        (37, 164, 184, 1421, 173, 1500, '雨霖伞'),
        (38, 164, 185, 1431, 173, 1500, '金刚枪'),
        (39, 164, 186, 1441, 173, 1500, '麒麟角'),
        (40, 164, 187, 1451, 173, 1500, '御灵铛'),
        (43, 164, 200, 1471, 173, 1500, '白骨爪'),
        (44, 164, 201, 1491, 173, 1500, '影武刃'),
        (45, 164, 202, 1501, 173, 1500, '修罗角'),
    ),
}


def shop_entry(shelf, goods):
    """The row a buy names, or None when the shelf does not offer that goods id."""

    for row in ARTIFACT_SHOP_GOODS.get(int(shelf), ()):
        if int(row[0]) == int(goods):
            return row
    return None


def shop_rows(now_unix, buy_counts=None, goods_by_shelf=None):
    """One S2C 1547 row per shelf: (dCode, shelf, goods, buyCount, nextTime).

    goods_by_shelf lets a refreshed shelf keep the goods the server rolled for
    it; without it the shelf's first goods is used.
    """

    counts = buy_counts or {}
    picked = goods_by_shelf or {}
    rows = []
    for shelf in ARTIFACT_SHOP_SHELVES:
        goods = int(picked.get(shelf) or picked.get(str(shelf))
                    or ARTIFACT_SHOP_GOODS[shelf][0][0])
        rows.append(
            (2, shelf, goods, int(counts.get(shelf, 1)),
             int(now_unix) + ARTIFACT_SHOP_REFRESH_SECONDS)
        )
    return tuple(rows)

# TASK-0408: the artifact-dungeon relic pool (contract task_0403).
# A relic is a 祝福 (zhufu) feature: its zhufuId goes into S2C 89 field 17, where
# pkgPveEnterData parses a colon-separated id list into tbZhufuFeatureSets.  Tower 1
# easy only ever offers group 1, so the pool below is that group, in cfg order.
ARTIFACT_RELIC_OFFER_SIZE = 3
#: relic id -> its zhufu feature id (the value S2C 89 carries)
ARTIFACT_RELIC_ZHUFU = {
    1: 48296,
    2: 48297,
    3: 48298,
    4: 48299,
    5: 48300,
    6: 48301,
    7: 48302,
    9: 48304,
    10: 48305,
    12: 48307,
    14: 48309,
    15: 48310,
    16: 48311,
    17: 48312,
    18: 48313,
    19: 48314,
    20: 48315,
    21: 48316,
    23: 48318,
    24: 48319,
    25: 48320,
    26: 48321,
    28: 48323,
    32: 48327,
    35: 48330,
    36: 48331,
    37: 48332,
    38: 48333,
    39: 48334,
    40: 48335,
    41: 48336,
    42: 48337,
    43: 48338,
    44: 48339,
    47: 48342,
    48: 48343,
    49: 48344,
    50: 48345,
    51: 48346,
    52: 48347,
    53: 48348,
    54: 48349,
    55: 48350,
    56: 48351,
    57: 48352,
    58: 48353,
    59: 48354,
    60: 48355,
    61: 48208,
    62: 48209,
    63: 48210,
    64: 48211,
    65: 48212,
    67: 48214,
    68: 48215,
    70: 48217,
    71: 48218,
    72: 48219,
    73: 48220,
    74: 48221,
    75: 48222,
    76: 48223,
    77: 48224,
    78: 48225,
    79: 48226,
    80: 48227,
    82: 48229,
    83: 48230,
    85: 48232,
    86: 48233,
    87: 48234,
    88: 48235,
    89: 48237,
    95: 48244,
    96: 48246,
    97: 48247,
    104: 48255,
    107: 48260,
    109: 48263,
    110: 48264,
    111: 48265,
    112: 48266,
    114: 48502,
    115: 48503,
    116: 48504,
    117: 48505,
    118: 48506,
    119: 48507,
    121: 48509,
    122: 48510,
    124: 48512,
    126: 48514,
    127: 48515,
    128: 48516,
    129: 48517,
    130: 48518,
    131: 48519,
    132: 48520,
    133: 48521,
    135: 48523,
    136: 48524,
    137: 48525,
    140: 48528,
    147: 48535,
    148: 48536,
    149: 48537,
    150: 48538,
    151: 48539,
    153: 48541,
    155: 48543,
    156: 48544,
    159: 48547,
    160: 48548,
    161: 48549,
    162: 48550,
    163: 48551,
    164: 48552,
    165: 48553,
    166: 48554,
    167: 48555,
    168: 48556,
    169: 48557,
    170: 48558,
    171: 48559,
    172: 48560,
    174: 48562,
    175: 48563,
    176: 48564,
    177: 48565,
    178: 48566,
    179: 48567,
    181: 48569,
    182: 48570,
    184: 48572,
    186: 48574,
    187: 48575,
    188: 48576,
    189: 48577,
    190: 48578,
    191: 48579,
    192: 48580,
    193: 48581,
    195: 48583,
    196: 48584,
    197: 48585,
    200: 48588,
    207: 48595,
    208: 48596,
    209: 48597,
    210: 48598,
    211: 48599,
    212: 48600,
    213: 48601,
    215: 48603,
    216: 48604,
    219: 48607,
    220: 48608,
    221: 48609,
    222: 48610,
    223: 48611,
    224: 48612,
    225: 48613,
    226: 48614,
    227: 48615,
    228: 48616,
    229: 48617,
    230: 48618,
    231: 48619,
    232: 48620,
}

#: the offer pool: group-1 relic ids in cfg order
ARTIFACT_RELIC_POOL = (1, 2, 3, 4, 5, 6, 7, 9, 10, 12, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 28, 32, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 67, 68, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 82, 83, 85, 86, 87, 88, 89, 95, 96, 97, 104, 107, 109, 110, 111, 112, 114, 115, 116, 117, 118, 119, 121, 122, 124, 126, 127, 128, 129, 130, 131, 132, 133, 135, 136, 137, 140, 147, 148, 149, 150, 151, 153, 155, 156, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 172, 174, 175, 176, 177, 178, 179, 181, 182, 184, 186, 187, 188, 189, 190, 191, 192, 193, 195, 196, 197, 200, 207, 208, 209, 210, 211, 212, 213, 215, 216, 219, 220, 221, 222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232)


def relic_offer(seed):
    """A deterministic offer of ARTIFACT_RELIC_OFFER_SIZE relics from the pool."""

    pool = ARTIFACT_RELIC_POOL
    start = int(seed) % len(pool)
    return tuple(pool[(start + i) % len(pool)] for i in range(ARTIFACT_RELIC_OFFER_SIZE))


def relic_zhufu_csv(relic_ids):
    """The colon-separated zhufu id list S2C 89 field 17 carries."""

    ids = [str(ARTIFACT_RELIC_ZHUFU[int(rid)]) for rid in relic_ids
           if int(rid) in ARTIFACT_RELIC_ZHUFU]
    return ":".join(ids)

#: TASK-0409: the tower-1 nodes whose event is T_MONSTER_BIG with yiwuGroup 1 -- winning
#: one of them offers a relic (contract task_0403).
ARTIFACT_RELIC_NODES = frozenset({(3, 1), (3, 3), (4, 1), (4, 2), (7, 1), (7, 3), (9, 2)})
