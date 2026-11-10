"""Immutable compatibility profile and mutable per-role compatibility state.

The profile is an audited revival-local template for CN 0.1.20.1.  Synthetic
values are never described as historical defaults.  RoleValue identities and
their field-level provenance are loaded from the reviewed design artifact and
validated against the canonical recovered wire mapping.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable


PROFILE_ID_V1 = "cn-0.1.20.1-revival-v1"
PROFILE_ID_V2 = "cn-0.1.20.1-revival-v2"
PROFILE_ID_V3 = "cn-0.1.20.1-revival-v3"
PROFILE_ID_V4 = "cn-0.1.20.1-revival-v4"
PROFILE_ID_V5 = "cn-0.1.20.1-revival-v5"
PROFILE_ID_V6 = "cn-0.1.20.1-revival-v6"
PROFILE_ID_V7 = "cn-0.1.20.1-revival-v7"
PROFILE_ID_V8 = "cn-0.1.20.1-revival-v8"
PROFILE_ID_V9 = "cn-0.1.20.1-revival-v9"
PROFILE_ID_V10 = "cn-0.1.20.1-revival-v10"
PROFILE_ID_V11 = "cn-0.1.20.1-revival-v11"
PROFILE_ID_V12 = "cn-0.1.20.1-revival-v12"
PROFILE_ID_V13 = "cn-0.1.20.1-revival-v13"
PROFILE_ID_V14 = "cn-0.1.20.1-revival-v14"
PROFILE_ID_V15 = "cn-0.1.20.1-revival-v15"
PROFILE_ID_V16 = "cn-0.1.20.1-revival-v16"
PROFILE_ID_V17 = "cn-0.1.20.1-revival-v17"
PROFILE_ID_V18 = "cn-0.1.20.1-revival-v18"
PROFILE_ID_V19 = "cn-0.1.20.1-revival-v19"
PROFILE_ID_V20 = "cn-0.1.20.1-revival-v20"
PROFILE_ID_V21 = "cn-0.1.20.1-revival-v21"
PROFILE_ID_V22 = "cn-0.1.20.1-revival-v22"
PROFILE_ID_V23 = "cn-0.1.20.1-revival-v23"
PROFILE_ID_V24 = "cn-0.1.20.1-revival-v24"
# The active runtime profile.  V1..V17 remain constructible and immutable; V18 is
# the additive successor of V17 and changes exactly one login-time block: when
# (and only when) the archive holds chapter milestone chests this role accepted,
# it replays the client's own `initChapterBoxStart`/`Info`/`End` rows before the
# chapter-task family.  With no accepted chest it sends ZERO packets, so the v18
# login stream is byte for byte the v17 stream, and every request-triggered
# policy (including v17's per-reply S2C 89 counter) is inherited unchanged.
#
# TASK-0237: V19 is an OPT-IN successor of V18, never the default.  It replaces
# exactly one request-triggered policy -- `battle_start_reply` -- so that the
# S2C 89 `startPveBattle` answer echoes the C2S 69 `pveOrPvpType` (position 5)
# and derives the seed from that replied type (position 6).  The server selects
# it only for a `dev-babel-main` session whose dev-play overlay is applied, so
# `qa-changan-main` and every other session keep the frozen v18 answer.  No
# login-time object changes: the v19 login stream is the v18 stream packet for
# packet.
#
# TASK-0238: V20 is an OPT-IN successor of V19 that ADDS exactly one
# request-triggered policy object -- `babel_settlement_push`, the single S2C 580
# `returnBabelInfo` push that follows a paired 73 (locktower) victory.  It is
# still never the default: `PROFILE_ID`/`get_profile()` stay V18, and only the
# same dev-babel-main overlay session selects V20.  Every other policy object is
# V19's object by identity, including the type-aware `battle_start_reply`, so a
# V20 login stream carries exactly the packets a V19 login carried.
#
# TASK-0242: V21 is the DEFAULT successor of V18 (not of V19/V20) and adds
# exactly one login-time policy object -- `mail_login`, the empty
# `returnMailStart` + `returnMailEnd` pair the stub never sent.  Its parent is
# V18 on purpose: the opt-in v19 battle-start reply and v20 settlement push are
# dev-babel-main concerns and must not leak into the default `qa-changan-main`
# stream.  Every other policy object is V18's object by identity, so the v21
# login stream is the v18 stream plus exactly two packets between S2C 10 and
# S2C 9.  V18/V19/V20 refuse the mail policy, so their streams stay frozen.
# TASK-0245: V22 is the DEFAULT successor of V21 and adds exactly one login-time
# policy object -- `team_max_fight`, the S2C 39 `returnTeamMaxFight` value the
# client's battle entry reads through `pkgTeamDataMgr.getMaxFight()`.  The
# client's own `caclGroupFight` chain and the shipped `card`/`cardFightRatio`/
# `soul` values produce the number; only WHERE it is sent and the decision to
# send the CURRENT preset's power are compatibility choices.  Every other policy
# object is V21's object by identity, so the v22 login stream is the v21 stream
# plus exactly one S2C 39 after S2C 35.  V18..V21 refuse the policy, so their
# streams stay frozen.
# TASK-0246: V23 is the DEFAULT successor of V22 and adds exactly one
# request-triggered policy object -- `month_sign_state`, the S2C 1499
# `returnMonthSignData` answer to C2S 725 `requestMonthSignData`.  It is sent
# ONLY as that request's answer (before the empty S2C 1500 ack), never at login,
# so the v23 login stream is the v22 stream byte for byte.  Every other policy
# object is V22's object by identity.  V1..V22 refuse the policy, so their
# request answers stay exactly the single empty ack they sent before.
# TASK-0247: V24 is the DEFAULT successor of V23 and adds exactly one login-time
# policy object -- `bit_rabbit_state`, the S2C 1435 `syncBitRabbit` push whose
# absent field (`le_tbBitRabbitData.dFightCountLeft`, nil until a server write)
# made `pkgBitRabbitDataMgr.checkAddRabbit` throw at source line 102 during the
# main-city red-point refresh and skip `checkChapterRedPoint` /
# `updateFriendUnreadNum`.  The six values are the client's own inactive state
# (all zeros); the packet and its runtime id are recovered, the values and the
# login position are labelled compatibility choices.  Every other policy object
# is V23's object by identity.  V1..V23 refuse the policy, so their login
# streams stay frozen.
PROFILE_ID = PROFILE_ID_V24

PROFILE_LINEAGE: dict[str, str | None] = {
    PROFILE_ID_V1: None,
    PROFILE_ID_V2: PROFILE_ID_V1,
    PROFILE_ID_V3: PROFILE_ID_V2,
    PROFILE_ID_V4: PROFILE_ID_V3,
    PROFILE_ID_V5: PROFILE_ID_V4,
    PROFILE_ID_V6: PROFILE_ID_V5,
    PROFILE_ID_V7: PROFILE_ID_V6,
    PROFILE_ID_V8: PROFILE_ID_V7,
    PROFILE_ID_V9: PROFILE_ID_V8,
    PROFILE_ID_V10: PROFILE_ID_V9,
    PROFILE_ID_V11: PROFILE_ID_V10,
    PROFILE_ID_V12: PROFILE_ID_V11,
    PROFILE_ID_V13: PROFILE_ID_V12,
    PROFILE_ID_V14: PROFILE_ID_V13,
    PROFILE_ID_V15: PROFILE_ID_V14,
    PROFILE_ID_V16: PROFILE_ID_V15,
    PROFILE_ID_V17: PROFILE_ID_V16,
    PROFILE_ID_V18: PROFILE_ID_V17,
    PROFILE_ID_V19: PROFILE_ID_V18,
    PROFILE_ID_V20: PROFILE_ID_V19,
    PROFILE_ID_V21: PROFILE_ID_V18,
    PROFILE_ID_V22: PROFILE_ID_V21,
    PROFILE_ID_V23: PROFILE_ID_V22,
    PROFILE_ID_V24: PROFILE_ID_V23,
}

COMPATIBILITY_GOAL = "minimal-first-scene-entry"
ROLE_VALUE_WIRE_FORMAT = "p" + "N" * 251
SERVER_TIME_WIRE_FORMAT = "NNNN"
SERVER_TIME_TIME_POLICY = "current_unix_time_at_send"
SERVER_TIME_OPEN_TIME_POLICY = "revival_process_start_unix"
SERVER_TIME_HISTORICAL_ORDERING = "unknown"
MYSTERY_SHOP_TYPE = 1
MYSTERY_SHOP_TIME_POLICY = "reuse_server_time_dtime"
MYSTERY_SHOP_AVAILABILITY = "closed"
MYSTERY_SHOP_GOODS_COUNT = 0
MYSTERY_SHOP_SNAPSHOT_END_SENT = False
MYSTERY_SHOP_HISTORICAL_ORDERING = "unknown"
STORY_START_LIST_POLICY = "empty"
STORY_START_HISTORICAL_ORDERING = "unknown"
# --- Team snapshot (v5) -------------------------------------------------------
# pkgTeamDataMgr.dMaxTeam.  The client's own preset enumeration is 1..dMaxTeam
# (`initTeamCardData`: `for i = 1, dMaxTeam`), and its two guarded accessors
# admit exactly `dGroupID > 0 and dGroupID <= dMaxTeam`.
TEAM_SNAPSHOT_MAX_GROUP = 5
# S2C37 returnTeamInfo slot decomposition, read from the recovered handler
# `s2c.returnTeamInfo(tbSocket, dGroupID, strEquipId1..4, strGodWeaponId,
# strID1..11)`: 4 team equipment ids + 1 god-weapon id + 11 card ids.
TEAM_SNAPSHOT_EQUIP_SLOTS = 4
TEAM_SNAPSHOT_CARD_SLOTS = 11
# The container the client reads as "the current preset", written by
# `setCurGroupID` (S2C35).  There is no recovered producer for the historical
# fresh-role value (D-33 §2, corpus-exhaustive): every member of {1..5} is
# structurally identical while all five presets are empty.  `1` is the minimum
# member of the client's own legal set and the first container its own
# `initTeamCardData` creates -- a labelled compatibility choice, NOT a
# recovered value.
TEAM_SNAPSHOT_CURRENT_GROUP = 1
TEAM_SNAPSHOT_CURRENT_GROUP_POLICY = "lowest_legal_group_of_client_own_enumeration"
TEAM_SNAPSHOT_HISTORICAL_ORDERING = "unknown"
TEAM_SNAPSHOT_EMPTY_SLOT = ""
# --- Recharge-activity login state (v6) ---------------------------------------
# S2C1014 returnACRechargeDataStart 'py' = (dRechargeStart, bReset).  The client's
# own `initRechargeStart` rebuilds every task row from ITS OWN config table, so
# the only value this policy contributes is the activity's recharge total.  The
# archive already publishes that number for this role: S2C10 returnRoleInfo
# carries `recharge_sum = 0` and `one_day_recharge_num = 0`.  The policy repeats
# that shipped value rather than inventing a new one; there is no recovered
# producer for a historical dRechargeStart (D-21), and the client's own
# uninitialised literal (-1, `_createRechargeActivity`) is observationally the
# same because `getRechargeShowValue` clamps every value <= 0 to 0.
RECHARGE_ACTIVITY_RECHARGE_START = 0
RECHARGE_ACTIVITY_RECHARGE_START_POLICY = "zero_recharge_of_shipped_role_snapshot"
RECHARGE_ACTIVITY_RESET_ROWS = True
RECHARGE_ACTIVITY_HISTORICAL_ORDERING = "unknown"
# --- Chapter-map item container (v7) ------------------------------------------
# The client's chapter-map progress container `le_tbChapterItemInfo` is created by
# exactly one recovered function, `pkgChapterInfoMgr.initChapterItemStart()`
# (`le_tbChapterItemInfo = {}`, source lines 48-50), and the module's own default
# is `nil` (line 8) -- so it stays nil until the server runs the client's own
# `initChapterItemStart`/`initChapterItemInfo`/`initChapterItemEnd` sequence (S2C
# 389/390/391).  Every read the world map makes of it is unguarded:
# `isItemFightOver`/`isItemEventOver` (`le_tbChapterItemInfo[dChapterId]`) reached
# from `getChapterProgress`, i.e. from `isChapterPass` -- one call from
# `initChapterBtnNormalStatus(1)` and one from `onChapterClickHandler(1)`.
#
# The two packets therefore carry the client's OWN reset and nothing else: 389 with
# an empty body runs the initializer, 391 is the client's own empty end-of-block
# handler.  Zero `initChapterItemInfo` rows are sent, because a row would assert
# that a specific map item of a specific chapter has been triggered/completed, and
# this role has never entered a chapter (its save has story 1008 finished, level 1,
# and no chapter scene ever entered) -- so the honest row set is the empty one, and
# its display (0 of the chapter's own config items, 0%) is derived by the client
# from its own `guankaChapter`/`chapterMap0` config.
CHAPTER_ITEM_CONTAINER_POLICY = "client_own_init_chapter_item_start"
CHAPTER_ITEM_ROW_COUNT = 0
CHAPTER_ITEM_HISTORICAL_ORDERING = "unknown"
#: The wire order of the two packets is forced by the client's own code: 390
#: indexes the global that only 389 creates, and 391 is the block's end handler.
CHAPTER_ITEM_PACKET_ORDER = ("initChapterItemStart", "initChapterItemEnd")
# --- Observed chapter-fight clear replay (v14) ---------------------------------
# The v7 block above deliberately sends ZERO `initChapterItemInfo` rows, because
# this archive had never observed a chapter fight.  TASK-0174 changes that: the
# same connection that starts a chapter-map battle (`C2S 69 netEnterPveBattle`)
# later settles it (`C2S 67 battlePveGameOver`), and this server answers that
# settlement with exactly one `S2C 96 battlePveGameOverEnd`.  That transaction is
# a fact this build observed, so the row it implies is no longer invented state.
#
# The row format is the client's own, read out of the shipped client:
#   * `pkgScProtocolMgr.lua:504` registers `S2C 390 initChapterItemInfo "ppp"`;
#   * its handler `pkgChapterInfoMgr.initChapterItemInfo(dChapterId, dType, dId)`
#     writes `le_tbChapterItemInfo[dChapterId][dType][dId] = true` (source 52-56);
#   * the chapter-map reader `isItemFightOver(tbInfo, dX, dY, dChapterId)` reads
#     exactly `le_tbChapterItemInfo[dChapterId][tbInfo.type][itemFightId(dX,dY)]`
#     (source 208-221), and `pkgGuankaMapConstMgr.itemFightId(dX, dY)` returns
#     its first argument (bytecode PROTO 0.1, source lines 172-174);
#   * `tbInfo.type` for a fight node is `MAP_TYPE_FIGHT = 10`, a shipped constant
#     (pkgGuankaMapConstMgr source line 28).
# So the only row this policy may ever emit is `(dChapterId, 10, dIndex)`, whose
# values are exactly the client's own C2S 69 fields `p4`, the packaged constant,
# and `p2`.  No progress percentage, reward, drop or timestamp is invented.
CHAPTER_FIGHT_REPLAY_ROW_FORMAT = "ppp"
CHAPTER_FIGHT_REPLAY_CONTAINER_POLICY = (
    "observed_settlement_rows_between_client_own_init_pair"
)
CHAPTER_FIGHT_REPLAY_HISTORICAL_ORDERING = "unknown"
#: `pkgGuankaMapConstMgr.MAP_TYPE_FIGHT` (source line 28, shipped constant).
CHAPTER_FIGHT_REPLAY_MAP_TYPE = 10

# --- Chapter milestone chest replay (v18) -------------------------------------
# `pkgScProtocolMgr.lua:495-498` registers the box family immediately BEFORE the
# chapter-task family:
#
#     registerS2C("",    "initChapterBoxStart")
#     registerS2C("pp",  "initChapterBoxInfo")
#     registerS2C("",    "initChapterBoxEnd")
#     registerS2C("ppp", "chapterAcceptBox", "chapterAcceptBox")
#
# and the client's own handler (`socket/nc/pkgGuankaMapNcMgr` PROTO 0.0..0.2)
# runs `pkgChapterInfoMgr.initChapterBoxStart()` (`le_tbChapterBox = {}`),
# `initChapterBoxInfo(dChapterId, dBoxIndex)` (marks the pair true and creates
# the chapter table) and the empty end handler.  `isChapterBoxAccepted` reads
# only that table, so without the login block an accepted chest looks unopened
# again and the client offers it a second time.
#
# The rows are NOT a compatibility guess: each one is the (chapter, box) pair of
# a `C2S 222` this server itself granted and stored, and the values are the
# stored accept row.  The historical login ordering is unknowable, so the block
# is an explicit synthetic_compatibility ordering placed immediately before the
# chapter-task family (the client's own registration order puts the box family
# there).  With no accepted chest the policy sends ZERO packets -- not an empty
# start/end pair -- so v17 and v18 login streams are byte for byte identical.
CHAPTER_BOX_REPLAY_ROW_FORMAT = "pp"
CHAPTER_BOX_REPLAY_CONTAINER_POLICY = "accepted_box_rows_only"
CHAPTER_BOX_REPLAY_HISTORICAL_ORDERING = "unknown"
CHAPTER_BOX_REPLAY_PACKET_ORDER = (
    "initChapterBoxStart",
    "initChapterBoxInfo",
    "initChapterBoxEnd",
)

# --- Empty mail-list login block (v21) ----------------------------------------
# TASK-0242.  `pkgScProtocolMgr.lua:369-370` registers the two container packets
# with empty formats:
#
#     registerS2C("", "returnMailStart")
#     registerS2C("", "returnMailEnd")
#
# `socket/nc/pkgMailNcMgr.lua` handlers 1-2 forward to
# `pkgMailModelMgr.returnMailStart/End`; `mail/pkgMailModelMgr.lua` lines 15-25
# set `le_tbMailDict = {}` / `le_tbTempItemDict = {}` and then
# `le_tbMailDict = le_tbTempItemDict` + dispatch `MAIL_LIST_UPDATE`.  Module lines
# 3-4 initialise both globals to `nil`, and `hasNewMail` (source line 132) runs
# `pairs(le_tbMailDict)` unguarded -- so a login that never sends this pair
# throws `bad argument #1 to 'pairs' (table expected, got nil)` the first time
# the main city asks `isShowRedPoint` (RUN-0076 logcat six times; RUN-0036 stack).
# Sending only Start+End with zero single-mail rows both creates the empty tables
# and leaves the container empty (no invented mail, attachment, reward or red
# point).  The runtime ids are the D-02 simulation's (S2C 255/256, source lines
# 369/370); a re-derivation test pins them to `analyze_pre_scene.protocol_table`.
MAIL_LOGIN_START_WIRE_ID = 255  # S2C, recovered runtime id (D-02 simulation)
MAIL_LOGIN_END_WIRE_ID = 256  # S2C, recovered runtime id (D-02 simulation)
MAIL_LOGIN_START_NAME = "returnMailStart"
MAIL_LOGIN_END_NAME = "returnMailEnd"
MAIL_LOGIN_WIRE_FORMAT = ""
MAIL_LOGIN_ROW_COUNT = 0
MAIL_LOGIN_POSITION_AFTER_ROLE_INFO = (
    "after_returnRoleInfo_before_returnLoginInfoEnd"
)
MAIL_LOGIN_ALLOWED_POSITIONS = (MAIL_LOGIN_POSITION_AFTER_ROLE_INFO,)
MAIL_LOGIN_HISTORICAL_ORDERING = "unknown"
MAIL_LOGIN_HISTORICAL_VALUES_RECOVERED = False

# --- Team battle-power (战力) login value (v22) -------------------------------
# TASK-0245 / #27.  `pkgChapterEventHandle.handleFight` (recovered source lines
# 250-289) refuses a chapter-map fight with
#
#     if tbCfg.fightLimitFight > 0
#        and pkgTeamDataMgr.getMaxFight() < tbCfg.fightLimitFight then
#         pkgUIAlertMgr.alert2(_T("fight_limit_to", tbCfg.fightLimitFight))
#
# and the shipped `fight_limit_to` text is 战力需要达到{1}.  `le_dMaxFight`
# (`pkgTeamDataMgr.lua` module line 7) starts at 0 and its ONLY writer is
# `setMaxFight`, reached from the S2C 39 `returnTeamMaxFight` handler
# (`socket/nc/pkgTeamNcMgr.lua` PROTO 5).  The stub never sent S2C 39, so every
# fight whose row carries a non-zero `fightLimitFight` (101031=250, 101051=860,
# 102071=1700) was refused regardless of the team -- the Owner's "a stronger
# team is still refused".
#
# The value is the client's own `pkgTeamDataMgr.caclGroupFight` over the preset
# the client is showing (the very number `pkgMainUIMgr.updateFight` prints); the
# formula and every input are recovered, `revival_stub/team_power.py` freezes
# them and `max_decoder/analyze_team_max_fight.py` re-derives them.  Whether the
# original server sent the CURRENT preset's power (rather than the best preset
# a player owns) and exactly when it sent S2C 39 are UNKNOWN, so the value
# choice, its triggers and its login position are labelled
# `synthetic_compatibility`.
TEAM_MAX_FIGHT_WIRE_ID = 39  # S2C, recovered runtime id (D-02 simulation)
TEAM_MAX_FIGHT_NAME = "returnTeamMaxFight"
TEAM_MAX_FIGHT_WIRE_FORMAT = "N"
TEAM_MAX_FIGHT_POSITION_AFTER_GROUP_ID = "after_returnGroupId"
TEAM_MAX_FIGHT_ALLOWED_POSITIONS = (TEAM_MAX_FIGHT_POSITION_AFTER_GROUP_ID,)
TEAM_MAX_FIGHT_HISTORICAL_ORDERING = "unknown"
TEAM_MAX_FIGHT_HISTORICAL_VALUES_RECOVERED = False
TEAM_MAX_FIGHT_VALUE_POLICY = "client_caclGroupFight_of_the_current_preset"
#: Every server-side event after which the current preset's power can change.
TEAM_MAX_FIGHT_TRIGGERS = (
    "login",
    "updateTeam",
    "changeTeamGroup",
    "cardAdvance",
    "cardStrength",
    "battleCardExp",
)

# --- Month-sign state (月签) answer (v23) -------------------------------------
# TASK-0246 / #28.  RUN-0077 showed the client sending C2S 725
# `requestMonthSignData` once a second (server.log 604/685/690/693/778/783,
# 19:47:08.319 .. 19:47:13.472) and the stub answering only the empty S2C 1500
# ack, so `pkgMonthSignModel`'s 1000 ms repeating timer never stopped:
# `setUpMonthSignUpdateTimer` (recovered source lines 130-140) sends while
# `le_nextRefreshTime < pkgServerTimeMgr.getCurrentServerTime()`, and
# `le_nextRefreshTime` is written by NOTHING except `setMonthSignData`, i.e. the
# S2C 1499 `returnMonthSignData` handler (`pkgMonthSignModel.lua` lines 15-22,
# `pkgS2CMonthSignMgr.lua` lines 8-11).
#
# S2C 1499 is `NppaNNN` (D-02 runtime id 1499, source registration line 2032).
# The packet is answered as (1499, 1500) in that order: 1499 writes the model,
# 1500 dispatches MONTH_SIGN_DATA_UPDATED, so the reverse order would have the UI
# read the previous data.  The archive has NO producer of sign rows, extension
# counts or rewards, so the state is the client's own empty container
# (`stringToTable("", 0)` = `{udExtSign = 0, tbSignMap = {}}`, proved safe in
# `max_decoder/analyze_month_sign_family.py`) plus a `nextRefreshTime` on the
# next CN (UTC+8) natural-day boundary of the same server clock S2C 0 uses.  The
# value choice is `synthetic_compatibility`: no historical sign state is claimed.
MONTH_SIGN_STATE_WIRE_ID = 1499  # S2C, recovered runtime id (D-02 simulation)
MONTH_SIGN_STATE_NAME = "returnMonthSignData"
MONTH_SIGN_STATE_WIRE_FORMAT = "NppaNNN"
MONTH_SIGN_STATE_POSITION_BEFORE_ACK = "beforeResponseMonthSignData"
MONTH_SIGN_STATE_ALLOWED_POSITIONS = (MONTH_SIGN_STATE_POSITION_BEFORE_ACK,)
MONTH_SIGN_STATE_TRIGGERS = ("requestMonthSignData",)
MONTH_SIGN_STATE_HISTORICAL_ORDERING = "unknown"
MONTH_SIGN_STATE_HISTORICAL_VALUES_RECOVERED = False
MONTH_SIGN_STATE_VALUE_POLICY = "empty_sign_history_next_cn_natural_day_boundary"
#: The client's own empty mapping text: `pkgStringMgr.split("#", "")` returns
#: `{}` (the `#str ~= 0` guard), so `stringToTable("", 0)` builds
#: `{udExtSign = 0, tbSignMap = {}}` without touching a nil key.
MONTH_SIGN_STATE_EMPTY_SIGN_MAP_TEXT = ""
MONTH_SIGN_STATE_UD_EXT_SIGN = 0
MONTH_SIGN_STATE_LAST_EXT_SIGN_TIME = 0
#: CN has no DST: the natural-day boundary is a fixed +08:00 offset.
MONTH_SIGN_STATE_CN_UTC_OFFSET_SECONDS = 8 * 3600

# --- bitRabbit session state (主城红点链) login push (v24) ---------------------
# TASK-0247 / WP-SYSTEM-001.  RUN-0077 crashed the main-city red-point refresh
# 0.571 s after `doFinishSwitch`:
#
#     temp/luaScript/bitRabbit/pkgBitRabbitDataMgr.lua:102:
#     attempt to compare nil with number
#     ... in function 'checkAddRabbit'
#     temp/luaScript/mainUI/pkgMainUIMgr.lua:167: in function 'addRabbitOnFriend'
#     temp/luaScript/mainUI/pkgMainUIMgr.lua:966: in function 'checkRedPoint'
#
# The recovered `pkgBitRabbitDataMgr` source line 102 is
#
#     if getFightCountLeft() <= 0 then
#
# and `getFightCountLeft` (lines 116-118) returns
# `le_tbBitRabbitData.dFightCountLeft`.  The module initialises the container to
# `{}` (line 7), so the field is nil until a server push writes it.  Its ONLY
# writers are `s2c.syncBitRabbit` (source lines 8-17) and
# `s2c.returnBitRabbitFightResult` (lines 73-78); `pkgScProtocolMgr.lua:1950`
# registers `syncBitRabbit` as `registerS2C("NNNNNN", "syncBitRabbit")`, a
# server push with no lock partner and no C2S request of its own.  The stub never
# sent it (RUN-0077 server.log has no protocol 1435), so the unguarded
# `nil <= 0` threw and the two calls that follow `addRabbitOnFriend` in
# `pkgMainUIMgr.checkRedPoint` (source lines 953-969) never ran:
# `checkChapterRedPoint` and `updateFriendUnreadNum`.
#
# The archive has NO producer of rabbit rows, square entries, fights, ranks or
# rewards, so the only honest state is "activity inactive": six zeros.  With
# `dFightCountLeft` 0 `checkAddRabbit` returns false (the friend rabbit icon
# stays hidden); `dRabbitTime` 0 makes `getMyOwnRabbit` return nil, so the
# square stays empty; nothing is granted, recorded or written.  The packet and
# its format are shipped client code; the values and WHERE in the login segment
# it is pushed are explicit compatibility choices (the original ordering is
# UNKNOWN).
BIT_RABBIT_STATE_WIRE_ID = 1435  # S2C, recovered runtime id (D-02 simulation)
BIT_RABBIT_STATE_NAME = "syncBitRabbit"
BIT_RABBIT_STATE_WIRE_FORMAT = "NNNNNN"
BIT_RABBIT_STATE_POSITION_AFTER_SOUL_SKILL = "after_returnTalentSoulSkill"
BIT_RABBIT_STATE_ALLOWED_POSITIONS = (
    BIT_RABBIT_STATE_POSITION_AFTER_SOUL_SKILL,
)
BIT_RABBIT_STATE_TRIGGERS = ("login",)
BIT_RABBIT_STATE_HISTORICAL_ORDERING = "unknown"
BIT_RABBIT_STATE_HISTORICAL_VALUES_RECOVERED = False
BIT_RABBIT_STATE_VALUE_POLICY = "client_empty_inactive_bit_rabbit_state"
#: `syncBitRabbit`'s six `N` values, all zero: no fight count, no add time, no
#: own rabbit, no live time, no left count, no own bit, no boss config.
BIT_RABBIT_STATE_FIGHT_COUNT_LEFT = 0
BIT_RABBIT_STATE_LAST_ADD_COUNT_TIME = 0
BIT_RABBIT_STATE_RABBIT_TIME = 0
BIT_RABBIT_STATE_RABBIT_LEFT_COUNT = 0
BIT_RABBIT_STATE_RABBIT_OWN_BIT = 0
BIT_RABBIT_STATE_RABBIT_BOSS_CFG_ID = 0


# --- Drawcard (祈灵) main-screen unlock (v15) ----------------------------------
# `pkgSystemFuncMgr.isFunctionOpen('drawcard')` is not a config check: its own
# special case (decompiled `utility/pkgSystemFuncMgr.lua` lines 245-246) is
#
#     if strMacro == "drawcard" then
#         return pkgChapterTaskMgr.isTaskCompleted(1001, 1) or dChapter >= 1
#     end
#
# so the main city's 祈灵 button stays grey and its click listener is the
# "function not open" alert until the client's own chapter-task table holds the
# completed id for task 1001.  `isTaskCompleted` (chapter/pkgChapterTaskMgr.lua
# lines 66-70) looks up `le_tbChapterTaskInfo[dChapterId][dId]` with
# `dId = pkgGuankaMapConstMgr.GetTaskCompletedId(1001)`; that helper (PROTO 0.6,
# source lines 215-217) is `dTaskId + 10000000`, so the key is 10001001 and the
# chapter argument is the literal 1 the gate itself passes.
#
# The row's id/format are recovered, not guessed: `pkgScProtocolMgr.lua:500`
# registers S2C386 `initChapterTaskInfo` with format "pp", and its handler
# (socket/nc/pkgGuankaMapNcMgr PROTO 0.9) calls
# `pkgChapterTaskMgr.initChapterTaskInfo(dChapterId, dId)`, which stores
# `le_tbChapterTaskInfo[dChapterId][dId] = true` (source lines 25-28).
#
# The two VALUES are a labelled compatibility choice: this archive has never
# observed a real server's chapter-task row (D-21), and the permanent QA role's
# own chapter 1 progress is 1/11 with no EXIT, so `dChapter >= 1` is false.  The
# row asserts exactly the one completed id the drawcard gate reads, and nothing
# else -- no chapter progress, no item, no reward, no draw result.
DRAWCARD_UNLOCK_FORMAT = "pp"
DRAWCARD_UNLOCK_CHAPTER_ID = 1
DRAWCARD_UNLOCK_TASK_ID = 1001
DRAWCARD_UNLOCK_COMPLETED_ID = 1001 + 10000000
DRAWCARD_UNLOCK_COMPLETED_ID_POLICY = "client_own_GetTaskCompletedId_plus_10000000"
DRAWCARD_UNLOCK_CONTAINER_POLICY = "single_completed_row_for_the_drawcard_gate"
DRAWCARD_UNLOCK_HISTORICAL_ORDERING = "unknown"
DRAWCARD_UNLOCK_PACKET_COUNT = 1
DRAWCARD_UNLOCK_ROW_COUNT = 1

# --- Experience (历练) task container (v8) ------------------------------------
# The client's experience-task container `le_tbTaskByChapterId` is created by
# exactly one recovered function, the S2C handler `pkgExperienceMgr` registers for
# itself as `s2c.returnTaskStart` (`le_tbTask = {}; le_tbTaskByChapterId = {};
# le_dTaskCount = 0`, source lines 62-66), and the module's own default is `nil`
# (chunk line 17) -- so it stays nil until the server runs the client's own
# `returnTaskStart`/`returnTaskEnd` pair (S2C 1307/1309).
#
# What it changes is not a read of its own but the *survival* of the chapter
# detail panel.  The world map's own init starts a 1,000 ms repeating timer
# (`pkgExperienceMainUIMgr.createTimer`, lines 118-124 -> `onCountdown`), and the
# countdown's "the activity is over" arm (`le_dEndTime` is 0 for this role, i.e.
# never pushed) calls `pkgExperienceMgr.CloseActivityByClient()` at line 73.  That
# dispatches EXPERIENCE_STATUS_CHANGE to `experienceStatusChange`, whose FIRST
# statement is `pkgUtilMgr.setActiveByName(gameObject, "Panel/btnDetail", false)`
# (line 199) -- the chapter detail panel and its 确认/探索 button -- and whose line
# 214 then walks every chapter into `changeChapterBtnExperienceStatus`, which
# reads `pkgExperienceMgr.GetTaskListByChapterId` (pkgExperienceMgr.lua:203,
# `le_tbTaskByChapterId[dChapterId]`, UNGUARDED).  While the container is nil that
# read raises, the exception escapes the dispatch AND `onCountdown` before lines
# 74-76 (`pkgTimerMgr.delete(le_dTimer); le_dTimer = nil`), so the timer is never
# deleted and the panel is switched off again every second (RUN-0031 logcat: 59
# errors in the whole run, 57 of them once per second from 05:21:55.457).
#
# The two packets therefore carry the client's OWN reset and nothing else: 1307
# with an empty body runs `returnTaskStart`, 1309 is the client's own empty
# end-of-block handler.  Zero `returnTaskInfo` rows are sent, because a row would
# assert one experience task of one chapter with its own id/state/progress, and
# this archive has none (D-21).
EXPERIENCE_TASK_CONTAINER_POLICY = "client_own_return_task_start"
EXPERIENCE_TASK_ROW_COUNT = 0
EXPERIENCE_TASK_HISTORICAL_ORDERING = "unknown"
#: The wire order of the two packets is forced by the client's own code: the row
#: reader indexes the global that only `returnTaskStart` creates.
EXPERIENCE_TASK_PACKET_ORDER = ("returnTaskStart", "returnTaskEnd")
# --- Chapter (关卡) task container (v9) ----------------------------------------
# The client's chapter-task container `le_tbChapterTaskInfo` is created by exactly
# one recovered function, the S2C handler `pkgChapterTaskMgr` exposes as
# `initChapterTaskStart` (`le_tbChapterTaskInfo = {}`, source lines 21-23); the
# module's own default is `nil` (chunk line 6); and the name is written by no other
# chunk of the shipped `luaScript` tree at all -- all 1827 chunks are walked for it
# by `analyze_chapter_task_init.assert_whole_tree_writers`.  So it stays nil until
# the server runs the client's own `initChapterTaskStart`/`initChapterTaskEnd` pair
# (S2C 385/387).
#
# What reads it is the client's own scene-switch completion.  `pkgChapterSceneMgr
# .enterChapter` hands `pkgSceneMgr.switchScene` the finish callback that becomes
# `le_finishCallBack`, and `pkgSceneMgr.doFinishSwitch` calls it at line 875 --
# *before* it dispatches ENTER_SCENE (880), calls `pkgSyncSceneMgr.enterScene(881)`,
# prints `-- doFinishSwitch` (882) and tears the loading UI down (884-890).  That
# callback runs `pkgChapterObjectMgr.initIndex2TypeId(chapterId)` at line 112, which
# calls `pkgChapterInfoMgr.getActiveMap` at 385, which calls `isItemOver` at 309,
# which calls `pkgChapterTaskMgr.isTaskReceived` at 262 -- and `isTaskReceived`'s
# condition indexes the global UNGUARDED at `pkgChapterTaskMgr.lua:62`.
#
# So while the container is nil the raise escapes `doFinishSwitch` at 875 and the
# client never finishes entering the chapter: RUN-0032 (TASK-0103) is the first run
# whose logcat carries the switch (`switchScene chapter_qiandengzhen_1 3 nil`,
# 07:17:46.241) and its whole logcat carries `-- doFinishSwitch` exactly twice, both
# for earlier scenes, never for the chapter.  The run ends on the client's own
# loading screen with an unrendered black viewport where the world map was.
#
# The two packets therefore carry the client's OWN reset and nothing else: 385 with
# an empty body runs `initChapterTaskStart`, 387 is the client's own empty
# end-of-block handler.  Zero `initChapterTaskInfo` rows are sent, because a row
# would assert that a specific chapter task is received/completed, and this archive
# has none (D-21); 388 `updateChapterTask` stays unregistered, a push with no
# producer here.
CHAPTER_TASK_CONTAINER_POLICY = "client_own_init_chapter_task_start"
CHAPTER_TASK_ROW_COUNT = 0
CHAPTER_TASK_HISTORICAL_ORDERING = "unknown"
#: The wire order of the two packets is forced by the client's own code: 386 indexes
#: the global that only 385 creates, and 387 is the block's end handler.  The pair is
#: sent immediately BEFORE the v7 chapter-item pair, because the client's own
#: registration table puts the task family (385-388) immediately before the item
#: family (389-392) and both containers are read in the same pass of the same
#: finish callback.
CHAPTER_TASK_PACKET_ORDER = ("initChapterTaskStart", "initChapterTaskEnd")
# --- Chapter (关卡) machine container (v10) ------------------------------------
# The client's chapter-machine container `le_tbChapterMachineInfo` is created by
# exactly one recovered function, and -- unlike the three containers above -- that
# function is not reached through the `socket/nc` wrapper module but registered by
# the owning module ITSELF: `chapter/pkgChapterMachineMgr.lua` writes
# `s2c.initChapterMachineStart = function() le_tbChapterMachineInfo = {} end`
# (source lines 18-20) straight into the shared `s2c` table, and it is the only
# chunk of the shipped `luaScript` tree that mentions the name at all -- all 1827
# are walked for it by `analyze_chapter_machine_init.assert_whole_tree_writers`.
# The module's own default is `nil` (lines 6-7), so the container stays nil until
# the server runs the client's own `initChapterMachineStart`/`initChapterMachineEnd`
# pair (S2C 400/402).
#
# It is read by the SAME finish callback v9 repaired, one statement later.
# `pkgChapterSceneMgr.lua:114 le_finishCallBack` calls
# `pkgChapterObjectMgr.initScene`, whose line 287 calls
# `pkgChapterMachineMgr.initAllMachineObj`; that walks the chapter's own
# `machineList` config and asks `isMachineCompleted(tbMachineInfo.id)` at line 312
# for every machine, and `isMachineCompleted`'s first statement (line 46) indexes
# the global UNGUARDED:
#
#     pkgChapterMachineMgr.lua:45  local dChapterId = pkgChapterInfoMgr.getChapterId()
#     pkgChapterMachineMgr.lua:46  return le_tbChapterMachineInfo[dChapterId] and ...
#
# So while the container is nil the raise escapes `doFinishSwitch` exactly as the
# v8/v9 raises did -- before 880-890 -- and the client never prints
# `-- doFinishSwitch` for the chapter.  That is RUN-0033's break (TASK-0108), and
# it is one statement *after* the one v9 cleared: RUN-0032 raised at
# `pkgChapterSceneMgr.lua:112` (initIndex2TypeId), RUN-0033 at
# `pkgChapterObjectMgr.lua:287` (initScene's own pass), reached through
# `pkgChapterSceneMgr.lua:114`.
#
# The two packets therefore carry the client's OWN reset and nothing else: 400 with
# an empty body runs the initializer, 402 is the client's own empty end-of-block
# handler.  Zero `initChapterMachineInfo` rows are sent, because a row would assert
# that a named machine of a named chapter is completed, and this archive has none
# (D-21); 401 and 403 stay unregistered, pushes with no producer here.
CHAPTER_MACHINE_CONTAINER_POLICY = "client_own_init_chapter_machine_start"
CHAPTER_MACHINE_ROW_COUNT = 0
CHAPTER_MACHINE_HISTORICAL_ORDERING = "unknown"
#: The wire order of the two packets is forced by the client's own code: 401 indexes
#: the global that only 400 creates, and 402 is the block's end handler.  The pair is
#: sent immediately AFTER the v7 chapter-item pair, because the client's own
#: registration table puts the machine family (399-403) after the item family
#: (389-393) and the machine init is the last chapter-map init family this server
#: sends.
CHAPTER_MACHINE_PACKET_ORDER = ("initChapterMachineStart", "initChapterMachineEnd")
# --- Chapter-map battle-start reply (v11) --------------------------------------
# `C2S 69 netEnterPveBattle` is a LOCK-SEND.  The client's own table registers the
# pair `{"NyyNNNNpppppppppaaa", "startPveBattle", "netEnterPveBattle"}`, so
# `pkgLockSendMgr` switches the client's event system off and arms
# `LOCK_TIME = 15000` until S2C 89 `startPveBattle` (or S2C
# `netEnterPveBattleFailed`) arrives.  Until this policy existed the archive
# answered nothing at all, and every run ended with the client shutting its own
# socket down (`dReasonType:9999`, RUN-0041 server.log:777).
#
# S2C 89 is the dispatch switch for the whole battle, and its SECOND value picks
# the branch.  TASK-0139 read both out of the shipped bytecode:
#
#   true  -> pkgBattleMgr.startPveBattle_svrVer starts NO local driver at all;
#            the server must keep feeding S2C 214 netEnterBattle / 215
#            netStartBattle / 213 netGameOver / 216..219 netCmd* -- this archive
#            has no producer for any of them, so that branch cannot finish a
#            battle here;
#   false -> pkgBattleMgr.startPveBattle -> pkgBattleSingleMgr.startSingleDvr ->
#            pkgDvrScene.sdvr_enterBattle -> pkgMainDrive.sdvr_startMainDrive, a
#            wholly local simulation whose "server" is the in-process sdvr2c
#            channel; with `cgDvrMode = CGDVRMODE_SINGLE` not one battle C2S
#            reaches the socket.
#
# This policy therefore takes the FALSE branch: it is the only one by which the
# player can really play the battle through (D-129, Owner choice A).
#
# The values that decide what the player sees carry no recovered producer, and the
# Owner decision allows a labelled compatibility choice for them -- but only a
# labelled one (D-129: never disguised as official_recovered /
# deterministic_recovered).  The rest are the request's OWN values echoed back
# (`request_echo`), which is the strongest source that exists here: the client
# never compares a reply against its request (TASK-0138 section F), and the
# chapter-map start is the client's own C2S 69 payload carrying the node it tapped.
BATTLE_START_REPLY_FORMAT = "NyyNNNNpppppppppaaa"
BATTLE_START_REQUEST_FORMAT = "yNNpppppppppa"
BATTLE_START_REPLY_FIELD_COUNT = 19
BATTLE_START_REQUEST_FIELD_COUNT = 13
#: `pkgBattleConst.GROUP_A`.  The client's own local-battle entry
#: (`pkgBattleSceneEntry.startTest4Adjust`, line 10) and `pkgBattleMgr
#: .enterManorFake` (line 218) both pass `GROUP_A` explicitly, and
#: `startSingleDvr` reads it as "which prepared side is the player's".
BATTLE_START_SELF_GROUP = 1
BATTLE_START_SELF_GROUP_POLICY = "client_own_local_battle_entry_group_a"
#: `pkgBattleConst.PVETYPE_GUANKA_CHAPTER_MAP` (packaged constant, line 219), and
#: the value the client itself sends in C2S 69 (RUN-0040/0041).
BATTLE_START_PVE_OR_PVP_TYPE = 86
#: `pkgBattleConst.PVETYPE_LOCKTOWER` -- the 通天塔 battle type the client's own
#: `pkgBabel.doFight/doFightGoOn` sends in C2S 69 (TASK-0236/D-192).  It is a
#: recovered client constant, not a compatibility choice.
BATTLE_START_LOCKTOWER_PVE_TYPE = 73
#: `pkgBattleConst.PVETYPE_GOD_FIGHT` -- the 神器副本 battle type the client's
#: own `pkgUIArtifactDgnTeamInfo4Conf` confirm sends in C2S 69
#: (`c2sStartPveBattle(PVETYPE_GOD_FIGHT, le_dTower, dLayer, dSn)`,
#: TASK-0257/D-208; its p1..p3 are tower/layer/sn and the client itself sends
#: p4 = 0).  A recovered client constant, not a compatibility choice.
BATTLE_START_GOD_FIGHT_PVE_TYPE = 66
#: `pkgBattleConst.PVETYPE_UNCHARTED` -- the 秘境 battle type the client's own
#: `pkgUIUnchartedEventView` sends in C2S 69
#: (`c2sStartPveBattle(PVETYPE_UNCHARTED, dUnchartedId, dLayer, dArea, dRoom,
#: dIndex, dOption2, dOptionId)`, TASK-0271; p1..p4 are
#: unchartedId/layer/area/room).  A recovered client constant.
BATTLE_START_UNCHARTED_PVE_TYPE = 88
#: TASK-0287: the two dungeon types whose C2S 69 is remembered on the
#: connection, per type, and paired with that connection's own C2S 67.  They
#: keep their own `dungeon_battle_start` key: the chapter map (86) keeps
#: `chapter_battle_start` and the locktower (73) keeps `babel_battle_start`
#: plus its S2C 580 push.  NEITHER type persists, rewards or pushes anything in
#: this slice.
BATTLE_START_DUNGEON_PVE_TYPES = frozenset({
    BATTLE_START_GOD_FIGHT_PVE_TYPE,
    BATTLE_START_UNCHARTED_PVE_TYPE,
})
#: `dRawRandomSeed` reaches the client's own formula
#: `randomSeed = (dRawRandomSeed - 3278) / fmod(pveOrPvpType, 500)` (line 25 of
#: `pkgBattlePveFromSg`), so `3278 + 86 * k` yields the integer `randomSeed = k`.
#: `k = 1` is this archive's fixed choice.  It is NEUTRAL for the scene choice of
#: the chapter this slice plays: `guankaChapter[1].sceneId` is the single-element
#: list `'1'`, so `ass.random(handle, 1, #tbScene)` has only one answer
#: (TASK-0139 section D).  Change the chapter and that neutrality is void.
BATTLE_START_RANDOM_SEED_K = 1
BATTLE_START_RANDOM_SEED_BASE = 3278
BATTLE_START_RAW_RANDOM_SEED = (
    BATTLE_START_RANDOM_SEED_BASE
    + BATTLE_START_PVE_OR_PVP_TYPE * BATTLE_START_RANDOM_SEED_K
)
BATTLE_START_RANDOM_SEED_POLICY = "fixed_3278_plus_86_times_k"
#: TASK-0237 (profile v19): the seed is derived from the type the reply RETURNS,
#: not from a fixed constant.  The client's own formula
#: `randomSeed = (dRawRandomSeed - 3278) / fmod(pveOrPvpType, 500)` then yields
#: the integer `k` for every replied type.  `k = 1` is still a labelled
#: compatibility choice (`synthetic_compatibility`), NOT a recovered value; only
#: the 3278 base and the formula are deterministic_recovered.
BATTLE_START_TYPE_RANDOM_SEED_POLICY = (
    "type_derived_3278_plus_replied_type_times_k"
)
#: `sfStartTime` is only ever compared with the PREVIOUS reply the client saw
#: (`pkgBattlePveFromSg` lines 5-8: the packet is dropped when `dRawRandomSeed`,
#: `sfStartTime` AND `pveOrPvpType` all equal the last one).  The other two are
#: fixed by this policy, so the counter must differ per reply or a second battle
#: in one client process would be discarded as a duplicate.  The counter is
#: per-process and starts at the revival server's own start second, so neither
#: "reply 1" nor any later reply repeats the numbering of a previous process.
BATTLE_START_SF_START_TIME_POLICY = "revival_process_monotonic_counter"
#: `s2`/`s3` have no consumer in the chapter-map branch at all.  The literal is a
#: byte string because that is what the recovery's `a` wire type decodes to, so
#: the assembled 19 values are exactly what the same codec reads back.
BATTLE_START_UNUSED_STRING = b""
BATTLE_START_HISTORICAL_ORDERING = "unknown"
#: The reply is not a login-segment packet: it exists only as the answer to a
#: C2S 69 this connection sent, one reply per request (the whole reason the lock
#: exists is that the client is waiting for exactly one).
BATTLE_START_REPLY_TRIGGER = "one_reply_per_client_request"
#: Value sources, in the four grades TASK-0139 section F established.  These are
#: VALUE origins, not `Provenance.origin` values: a field's provenance says what
#: kind of evidence backs it (and stays `synthetic_compatibility` for almost
#: every position), while its value origin says where the wire value comes from
#: on each reply.  `BattleStartReplyPolicy.field_origins()` returns the
#: provenance origins; `value_origins()` returns these.
BATTLE_START_VALUE_CONSTANT = "compatibility_constant"
BATTLE_START_VALUE_ECHO = "request_echo"
BATTLE_START_VALUE_COUNTER = "reply_counter"
#: TASK-0237: the value is not a constant and not a plain echo -- it is a
#: DETERMINISTIC FUNCTION of one request slot, derived on every reply.  v19 uses
#: it for `dRawRandomSeed` (position 6), which the client reads through
#: `(seed - 3278) / fmod(pveOrPvpType, 500)`; the payload this origin carries is
#: the 0-based index of the request value the seed is derived from.
BATTLE_START_VALUE_REQUEST_SEED = "request_seed"
BATTLE_START_VALUE_ORIGINS = frozenset({
    BATTLE_START_VALUE_CONSTANT,
    BATTLE_START_VALUE_ECHO,
    BATTLE_START_VALUE_COUNTER,
    BATTLE_START_VALUE_REQUEST_SEED,
})
#: The one position whose value is a recovered packaged constant in the v11 /
#: v17 / v18 policies: `pveOrPvpType` (wire position 5).  TASK-0237's v19 policy
#: echoes the request there instead, so the position keeps its recovered
#: dispatch evidence without claiming one fixed value for every type.
BATTLE_START_RECOVERED_CONSTANT_POSITIONS = frozenset({5})
#: TASK-0237: the two positions v19 changes, and the C2S 69 slot both read.
#: The request is `yNNpppppppppa`: index 2 is `pveOrPvpType` (wire position 3).
BATTLE_START_TYPE_POSITION = 5
BATTLE_START_SEED_POSITION = 6
BATTLE_START_REQUEST_TYPE_INDEX = 2
BATTLE_START_TYPE_AWARE_POSITIONS = frozenset({
    BATTLE_START_TYPE_POSITION,
    BATTLE_START_SEED_POSITION,
})
#: TASK-0182: in v17 this position stops echoing C2S 69's own `dSyncCount` and
#: carries the per-reply counter instead.  `pkgBattleMgr.startPveBattle`
#: (`max_decoder/forensics_first_main_city_closure/luaScript__battle__pkgBattleMgr.lua`
#: lines 176-199) compares it with
#: `pkgPlayerInfoMgr.dPveSyncCount` (initially -1, set from the last reply) and
#: discards the packet when the two are equal; the client never compares it with
#: the request.  The shipped client sends `dSyncCount = 0` on every battle, so an
#: echo made the second battle of one client process look like a duplicate.
BATTLE_START_SYNC_COUNT_POSITION = 7

#: The 19 S2C 89 values, one row per wire position:
#: ``(position, symbolic name, wire type, value origin, value payload,
#: field contract, provenance key)``.
#:
#: * ``value payload`` is the constant for ``compatibility_constant``, and the
#:   index into the C2S 69 field tuple for ``request_echo`` (the request is
#:   ``yNNpppppppppa``: 0 isRetry, 1 dSyncCount, 2 pveOrPvpType, 3..11 p1..p9,
#:   12 a1), and ``None`` for ``reply_counter``.
#: * Position 3 carries the client's own field ordering but NO name: TASK-0138
#:   asserted that its handler names exactly positions 1..19 except 3.
#: * The reply's ``p1..p9`` are copied slot for slot without reinterpreting them.
#:   The request-side and reply-side names differ (`dIndex`/`dX`/`dY` vs
#:   `dFightId`/`dX`/`dY`/`dChapterId`/...), and TASK-0138 forbids reading the
#:   request's naming as the reply's contract; equal numbers in equal slots is the
#:   whole claim.
BATTLE_START_FIELD_TABLE: tuple[
    tuple[int, str, str, str, object, str, str], ...
] = (
    (1, "sfStartTime", "N", BATTLE_START_VALUE_COUNTER, None,
     "S2C89 position 1 'N' sfStartTime", "start_time"),
    (2, "isUseSfSvr", "y", BATTLE_START_VALUE_CONSTANT, False,
     "S2C89 position 2 'y' isUseSfSvr", "branch"),
    (3, "(unnamed)", "y", BATTLE_START_VALUE_CONSTANT, False,
     "S2C89 position 3 'y' (the client's own handler leaves it unnamed)", "transfer"),
    (4, "selfGroup", "N", BATTLE_START_VALUE_CONSTANT, BATTLE_START_SELF_GROUP,
     "S2C89 position 4 'N' selfGroup", "side"),
    (5, "pveOrPvpType", "N", BATTLE_START_VALUE_CONSTANT, BATTLE_START_PVE_OR_PVP_TYPE,
     "S2C89 position 5 'N' pveOrPvpType", "type"),
    (6, "dRawRandomSeed", "N", BATTLE_START_VALUE_CONSTANT, BATTLE_START_RAW_RANDOM_SEED,
     "S2C89 position 6 'N' dRawRandomSeed", "seed"),
    (7, "dSyncCount", "N", BATTLE_START_VALUE_ECHO, 1,
     "S2C89 position 7 'N' dSyncCount <- request position 2 'N' dSyncCount",
     "echo_sync_count"),
    (8, "p1", "p", BATTLE_START_VALUE_ECHO, 3,
     "S2C89 position 8 'p' p1 <- request position 4 'p' p1", "echo_positional"),
    (9, "p2", "p", BATTLE_START_VALUE_ECHO, 4,
     "S2C89 position 9 'p' p2 <- request position 5 'p' p2", "echo_positional"),
    (10, "p3", "p", BATTLE_START_VALUE_ECHO, 5,
     "S2C89 position 10 'p' p3 <- request position 6 'p' p3", "echo_positional"),
    (11, "p4", "p", BATTLE_START_VALUE_ECHO, 6,
     "S2C89 position 11 'p' p4 <- request position 7 'p' p4", "echo_positional"),
    (12, "p5", "p", BATTLE_START_VALUE_ECHO, 7,
     "S2C89 position 12 'p' p5 <- request position 8 'p' p5", "echo_positional"),
    (13, "p6", "p", BATTLE_START_VALUE_ECHO, 8,
     "S2C89 position 13 'p' p6 <- request position 9 'p' p6", "echo_positional"),
    (14, "p7", "p", BATTLE_START_VALUE_ECHO, 9,
     "S2C89 position 14 'p' p7 <- request position 10 'p' p7", "echo_positional"),
    (15, "p8", "p", BATTLE_START_VALUE_ECHO, 10,
     "S2C89 position 15 'p' p8 <- request position 11 'p' p8", "echo_positional"),
    (16, "p9", "p", BATTLE_START_VALUE_ECHO, 11,
     "S2C89 position 16 'p' p9 <- request position 12 'p' p9", "echo_positional"),
    (17, "s1", "a", BATTLE_START_VALUE_ECHO, 12,
     "S2C89 position 17 'a' s1 <- request position 13 'a' a1", "echo_blessing"),
    (18, "s2", "a", BATTLE_START_VALUE_CONSTANT, BATTLE_START_UNUSED_STRING,
     "S2C89 position 18 'a' s2", "unused_string"),
    (19, "s3", "a", BATTLE_START_VALUE_CONSTANT, BATTLE_START_UNUSED_STRING,
     "S2C89 position 19 'a' s3", "unused_string"),
)

#: One provenance per KIND of value, keyed the way the table above refers to it.
#: Every position still carries its own `Provenance` object whose `field_contract`
#: names that position, so a single position can be re-audited on its own.
BATTLE_START_PROVENANCE: dict[str, tuple[str, str, str, str, str, bool]] = {
    "start_time": (
        "recovered pkgBattlePveFromSg lines 5-8 (the duplicate-packet comparison "
        "the value feeds) and TASK-0139 section F",
        "synthetic_compatibility",
        "A target-client read contract; D historical value (none exists)",
        "no producer: the value carries no battle fact, it only has to differ from "
        "the reply the client received last, so the revival server numbers its own "
        "replies instead of inventing a battle clock",
        "if the client ever compares sfStartTime with anything but its own previous "
        "reply, replace this policy in a new profile version",
        True,
    ),
    "branch": (
        "recovered dispatch in pkgBattlePveFromSg lines 48-51 (isUseSfSvr) plus "
        "TASK-0139 sections 4A.1/4A.2, which read both target functions statement "
        "by statement",
        "synthetic_compatibility",
        "A target-client branch contract; C compatibility decision",
        "this is a branch SELECTION, not a recovered server value: `true` starts no "
        "local driver and would need S2C 214/215/213/216..219, which this archive "
        "has no producer for, while `false` runs the client's own local simulation "
        "end to end.  Taking `false` is what makes the battle playable (D-129)",
        "the whole battle afterwards runs in the client's local driver, so nothing "
        "this server sends can steer it; if a producer for the server-driven family "
        "is ever recovered, replace this policy in a new profile version",
        True,
    ),
    "transfer": (
        "recovered pkgBattlePveFromSg lines 15-18 (the transfer branch) and "
        "pkgBattleSingleMgr lines 30-34 (the local prepare data it otherwise builds)",
        "synthetic_compatibility",
        "A target-client branch contract; C compatibility decision",
        "`true` would take the player's own lineup from "
        "pkgBattleTransferMgr.getRecvDataAndClear0(S2C_BATTLE), a buffer this "
        "archive has no producer for -- the two prepared sides would be nil where "
        "startSingleDvr expects them not to be.  `false` lets the client build its "
        "own side from its own local save",
        "the value is invisible to the player except through which lineup appears; "
        "if a transfer producer is ever recovered it must be re-decided here",
        True,
    ),
    "side": (
        "recovered pkgBattleSceneEntry.startTest4Adjust line 10 and pkgBattleMgr"
        ".enterManorFake line 218 (both pass pkgBattleConst.GROUP_A), read by "
        "pkgBattleSingleMgr lines 27/29-34/59-61",
        "synthetic_compatibility",
        "A target-client constant contract; C compatibility decision",
        "which side the player is has no producer here, and it is player-visible "
        "(attack or defence, and which side the local game-over credits).  The "
        "client's OWN local-battle entry picks GROUP_A, so this archive picks the "
        "same member of the client's own enum instead of inventing one",
        "if the player's side is ever recovered from the original server, replace "
        "this policy in a new profile version",
        True,
    ),
    "type": (
        "packaged pkgBattleConst.PVETYPE_GUANKA_CHAPTER_MAP = 86 (source line 219) "
        "and the client's own C2S 69 payload (RUN-0040/0041, server.log:708)",
        "deterministic_recovered",
        "A target-client constant and a device-observed request value",
        "the packaged constant selects the chapter-map consumption branch of "
        "pkgPveEnterData, and the client itself asks for exactly this type, so the "
        "reply repeats it rather than choosing it",
        "changing the battle type is a new game-content decision and cannot be made "
        "by editing this field alone",
        False,
    ),
    "seed": (
        "recovered pkgBattlePveFromSg line 25 (the seed formula), "
        "pkgBattleSceneEntry.startTest4Adjust line 14 (the client's own local seed) "
        "and TASK-0139 section D",
        "synthetic_compatibility",
        "A target-client formula contract; D historical value (none exists)",
        "no producer: `dRawRandomSeed` is named by exactly one chunk in the whole "
        "APK and this archive has no battle clock to derive it from.  The value is "
        "chosen so the client's own formula gives the integer randomSeed = k, and "
        "it is neutral for the scene of chapter 1 because that chapter's scene list "
        "has a single element",
        "the neutrality holds only while the chapter's scene list stays a single "
        "element; it steers the battle's internal random sequence, which this "
        "archive has not executed (ass.createRandom is native)",
        True,
    ),
    "echo_sync_count": (
        "recovered pkgBattleMgr lines 177-183 (roleInfo.dPveSyncCount) and "
        "pkgSyncCounterMgr (the client's own counter that produced the request)",
        "request_echo",
        "A target-client duplicate-packet contract; the value is the request's own",
        "the client compares this field with its own last remembered count to drop "
        "duplicates and never with the request, so echoing the request is both the "
        "strongest available source and provably a 'new' packet",
        "none: the field is not shown to the player and cannot invent content",
        True,
    ),
    # TASK-0182 / profile v17: the same position, but minted per reply instead of
    # echoing the request.  The client's own duplicate check is the only consumer.
    "sync_count_counter": (
        "recovered pkgBattleMgr lines 177-183 (playerInfo.dPveSyncCount compared "
        "with a reply's dSyncCount) and pkgSyncCounterMgr (the client's own "
        "sync-counter container, whose original producer is not recovered)",
        "synthetic_compatibility",
        "A target-client duplicate-packet contract; D historical value (none exists)",
        "the client only compares this value with the one it stored from the "
        "previous reply and drops the packet when they are equal, so a value minted "
        "once per reply is what the contract needs.  The original server's "
        "sync-counter producer (S2C returnSyncCounter/updateSyncCounter) is not "
        "recoverable, so this is a per-reply compatibility counter, not a "
        "historical default",
        "if the original sync-counter producer is ever recovered, replace this "
        "policy in a new profile version; the value is never shown to the player",
        True,
    ),
    "echo_positional": (
        "the client's own C2S 69 payload (RUN-0040/0041: 101010, 2, 0, 1, 0, 0, 0, "
        "0, 0) and pkgPveEnterData lines 4-18 (the reply-side readers)",
        "request_echo",
        "A target-client value-domain match; the values are the request's own",
        "the reply side reads these slots as dFightId/dX/dY/dChapterId/"
        "dRewardIndex/dLevel, and the request's own slots are the node the player "
        "tapped.  Equal numbers in equal slots is asserted here; interpreting the "
        "request's names as the reply's contract is NOT (TASK-0138 forbids it).  "
        "The archive invents no node, no coordinate and no chapter",
        "p5/p6 have further readers inside tbExtra that TASK-0139 did not "
        "exhaustively judge; that boundary is recorded, not hidden",
        True,
    ),
    "echo_blessing": (
        "the client's own C2S 69 payload (a1, empty in RUN-0040/0041) and "
        "pkgSmartZhuFuFeature.returnFeatureList (lines 203-229: nil and the empty "
        "string both give an empty list)",
        "request_echo",
        "A target-client contract; the value is the request's own",
        "an empty blessing list is the client's own representation of 'no blessing "
        "entry', and the request that started this battle carried exactly that, so "
        "no blessing is invented",
        "a non-empty request value would be replayed verbatim, which is what the "
        "echo is for; nothing here can add a blessing the request did not carry",
        True,
    ),
    "unused_string": (
        "recovered pkgPveEnterData chapter-map branch (lines 3-23: s2/s3 are not "
        "read at all) and pkgBattlePveFromSg line 29 (the reply's own field order)",
        "synthetic_compatibility",
        "A target-client contract; C compatibility decision",
        "the chapter-map branch has no consumer for these two strings, so the "
        "client's own empty literal is the only value that can be defended",
        "none: an unread field cannot change what the player sees",
        True,
    ),
}

# ---------------------------------------------------------------------------
# S2C 96 `battlePveGameOverEnd` -- the unlock of the chapter-map battle-end
# lock-send (TASK-0155, profile v12).
#
# WHY THIS POLICY MAY EXIST AT ALL.  A lock-send the client issues by itself has
# to be answered one-for-one (D-86), and TASK-0155 read the client's own consumer
# of this particular answer out of the shipped bytecode before writing a single
# value:
#
# * `battlePveGameOverEnd` occurs in exactly TWO of the 3372 shipped `.max`
#   chunks (whole-APK string-constant scan): the registration table
#   `zys_scAll/np/pkgBattleProtoDef.max` and its only handler,
#   `luaScript/battle/net/pkgGameOverFromSg.max`.
# * In that handler the ONLY reads of its own arguments are the two
#   `MOVE R7 R2` instructions of the `PVETYPE_SEVEN_DAY_HAPPY_BOSS` and
#   `PVETYPE_BIT_RABBIT` branches (PROTO 0.3, source lines 65..88, bytecode
#   pc 0078 and 0093).  The `N` argument is never read at all, and the chapter
#   branch (`pveOrPvpType == pkgBattleConst.PVETYPE_GUANKA_CHAPTER_MAP`, pc
#   0009-0011) calls `pkgGameOverDisplay.showChapterPveGameOverView()` with ZERO
#   arguments.
# * `showChapterPveGameOverView` is declared `params=0` (`pkgGameOverDisplay`
#   source lines 303..346): it cannot take a value from any packet.  Its data is
#   `getBInfo()` plus `pkgGainInfoMgr`, and `getBInfo().tbGameOverResult` is
#   written by the client's own local drive --
#   `pkgMainDrive.sdvr_forceDoGameOver` line 233 -> `sdvr2c_net.netGameOver` ->
#   `sdvr2c_data.dataGameOver` -> `pkgSmartMain.protoGameOver` line 115
#   (RUN-0048 logcat:15860 is that dispatch, `netGameOver:  1  0  2`).
# * Even under the other dispatch shape the client could have (the
#   `pkgBattleNet4Cg` wrapper handing the values to the local data drive), the
#   drive indexes `sdvr2c_data[dataFuncName]` and `dataFuncName` is
#   `string.gsub("battlePveGameOverEnd", "net", "data", 1)` -- unchanged, because
#   the name contains no "net" -- so it would look up a key NO chunk defines.
#
# The verdict is therefore a decidable sentence, and it is TRUE: **the chapter
# settlement branch and every consumer derived from it read no field of this
# packet.**  That is why the two values below may be the minimal constants they
# are, and why they cannot change what the player sees.
BATTLE_GAME_OVER_REPLY_FORMAT = "Na"
BATTLE_GAME_OVER_REQUEST_FORMAT = "NNppppppppppppaaaappp"
BATTLE_GAME_OVER_REPLY_FIELD_COUNT = 2
BATTLE_GAME_OVER_REQUEST_FIELD_COUNT = 21
#: The client's own unlock table for this lock-send, read out of row 7 and row 8
#: of `battleProtoSg2Cg` (the two rows both name both lock-sends).
BATTLE_GAME_OVER_UNLOCK_NAMES = ("battlePveGameOver", "battlePveGameOver_svrVer")
#: The one name this archive answers.  `battlePveGameOver_svrVer` stays
#: unregistered -- RUN-0048 proves the client took the single-driver branch, and a
#: `_svrVer` row would assert a server-driven battle with no producer (D-18).
BATTLE_GAME_OVER_REGISTERED_REQUEST = "battlePveGameOver"
BATTLE_GAME_OVER_HISTORICAL_ORDERING = "unknown"
BATTLE_GAME_OVER_REPLY_TRIGGER = "one_reply_per_client_request"
#: The value sources this policy is allowed to use.  `request_echo` is
#: deliberately absent: nothing proves that either reply slot corresponds to any
#: request slot, and an echo would look like a recovered correspondence.  A
#: labelled constant asserts strictly less.
BATTLE_GAME_OVER_VALUE_CONSTANT = "compatibility_constant"
BATTLE_GAME_OVER_VALUE_ORIGINS = frozenset({BATTLE_GAME_OVER_VALUE_CONSTANT})
#: Position 1 'N'.  Nothing reads it (see the verdict above), so the smallest
#: claim available is a fixed zero -- NOT a claim about what the original server
#: sent, and not an echo of the request's own `pveOrPvpType`.
BATTLE_GAME_OVER_RESULT_CODE = 0
#: Position 2 'a'.  Same reasoning; the two branches that DO read this value
#: (sevenDayHappy / bitRabbit) are not the branch this archive plays.
BATTLE_GAME_OVER_UNUSED_STRING = b""

#: The 2 S2C 96 values, one row per wire position:
#: ``(position, symbolic name, wire type, value origin, value payload,
#: field contract, provenance key)``.  The position is what the wire order says;
#: the symbolic name says out loud that the client does not name either slot.
BATTLE_GAME_OVER_FIELD_TABLE: tuple[
    tuple[int, str, str, str, object, str, str], ...
] = (
    (1, "(unnamed) 1", "N", BATTLE_GAME_OVER_VALUE_CONSTANT,
     BATTLE_GAME_OVER_RESULT_CODE,
     "S2C96 position 1 'N' (the client's own handler never names it)",
     "result_code"),
    (2, "(unnamed) 2", "a", BATTLE_GAME_OVER_VALUE_CONSTANT,
     BATTLE_GAME_OVER_UNUSED_STRING,
     "S2C96 position 2 'a' (the client's own handler never names it; read only "
     "by the seven-day-happy and bit-rabbit branches it passes through to "
     "pkgUIBaseViewMgr.showByViewPath)",
     "unused_string"),
)

#: One provenance per KIND of value, keyed the way the table above refers to it.
BATTLE_GAME_OVER_PROVENANCE: dict[str, tuple[str, str, str, str, str, bool]] = {
    "result_code": (
        "recovered consumer analysis: pkgGameOverFromSg PROTO 0.3 (source lines "
        "65..88) never reads its first argument, and the chapter branch it takes "
        "calls pkgGameOverDisplay.showChapterPveGameOverView() with no arguments "
        "(bytecode pc 0009-0011, `CALL R4 1 1`)",
        "synthetic_compatibility",
        "A target-client read contract (exhaustive whole-APK name scan plus the "
        "handler's own bytecode); D historical value (no producer exists)",
        "no producer: the slot has no reader in the chapter branch, so the smallest "
        "defensible value is the identity 0 -- it is not an assertion that the "
        "original server sent 0, and it is not an echo of the request",
        "none for the chapter branch: an unread field cannot change what the player "
        "sees.  If a producer for the original value is ever recovered, replace "
        "this policy in a new profile version",
        True,
    ),
    "unused_string": (
        "recovered consumer analysis: pkgGameOverFromSg PROTO 0.3 reads its second "
        "argument ONLY at pc 0078 and pc 0093, both inside the "
        "PVETYPE_SEVEN_DAY_HAPPY_BOSS / PVETYPE_BIT_RABBIT branches; the chapter "
        "branch passes no packet value anywhere",
        "synthetic_compatibility",
        "A target-client read contract; C compatibility decision",
        "the chapter branch has no consumer for this string, so the client's own "
        "empty literal -- the same choice TASK-0140 made for the two unread "
        "strings of S2C 89 -- is the only value that can be defended",
        "none: an unread field cannot change what the player sees",
        True,
    ),
}
# --- Babel settlement push (v20) ----------------------------------------------
#
# WHY THIS POLICY MAY EXIST AT ALL.  TASK-0236/D-192 proved the whole locktower
# battle edge from the shipped client: `pkgBabel.doFight/doFightGoOn` sends
# C2S 69 with `pveOrPvpType = PVETYPE_LOCKTOWER = 73`; the client settles the
# battle in its own local single driver and then sends C2S 67 with
# `dPackCheckFlag` = the S2C 89 `dSyncCount` while the lock-send timer runs; the
# only unlock is S2C 96 `battlePveGameOverEnd` (which has no 73 branch); and
# `setCurFloor` appears in exactly two shipped chunks, so floor/state/failCount/
# realm can ONLY change through S2C 580 `returnBabelInfo`, which the client never
# requests.  Without the push the tower UI cannot move even though the battle
# finished.
#
# WHAT THIS POLICY CLAIMS, AND NOTHING MORE: **after the paired 73 victory this
# session pushes back the very state it already showed -- the validated dev-play
# overlay's floor/state/failCount/realm.**  It does not advance the floor, change
# the realm or clear a failure count: the original server's post-win values are
# the server state machine and are UNKNOWN (TASK-0236 U-3/U-5).  The push and
# its position after S2C 96 are labelled `synthetic_compatibility`; the pairing
# itself (same connection, C2S 69 p1/p2 -> C2S 67) is `deterministic_recovered`.
# There is no reward and no charge: no packet carries cfgBabelReward or
# cfgBabelFailCost (TASK-0236 U-8).
BABEL_SETTLEMENT_PUSH_WIRE_ID = 580  # S2C, recovered runtime id (D-02 simulation)
BABEL_SETTLEMENT_PUSH_FORMAT = "NNNNN"
BABEL_SETTLEMENT_PUSH_FIELD_COUNT = 5
#: The client's own handler moves R1/R3/R4/R5 into `setCurFloor(f1,f3,f4,f5)`;
#: R2 (position 2) is NEVER read (TASK-0231 U-1), so its name stays the honest
#: placeholder that already logs it as such.
BABEL_SETTLEMENT_PUSH_FIELD_NAMES = (
    "floor", "field2", "state", "failCount", "realm",
)
BABEL_SETTLEMENT_PUSH_SEND_TRIGGER = "babel_settlement"
BABEL_SETTLEMENT_PUSH_TRIGGER_REQUEST = "battlePveGameOver"
#: The values are not constants of this profile: they are read from the session's
#: validated dev-play overlay, so the push can only replay what the session
#: already announced at login.
BABEL_SETTLEMENT_PUSH_VALUE_SOURCE = "dev_play_babel_overlay"
BABEL_SETTLEMENT_PUSH_MAX_PER_SETTLEMENT = 1
BABEL_SETTLEMENT_PUSH_HISTORICAL_ORDERING = "unknown"

# --- Prison-fight boss snapshot (v13) ----------------------------------------
#
# WHY THIS POLICY MAY EXIST AT ALL.  RUN-0054's alert confirm is accepted by the
# client, the alert is destroyed, and then the main UI's PANEL_CLOSE listener
# calls `pkgPrisonFightModel.checkActOn` line 21, which compares the NIL
# `e_tbBossData.startTime` with the server time and raises before the chapter's
# own confirm callback (the return to the main city) can run.  The one and only
# writer of `e_tbBossData` in the whole APK is
# `pkgS2CPrisonFightMgr.s2c.synPrisonFightBossToCg` (source lines 12..30,
# params=12 = socket + 11 values), registered by
# `pkgScProtocolMgr.lua:1850 registerS2C("NappNNppppp", ...)`.  The D-02
# registration simulation derives its wire id as S2C 1370; the two real-device
# anchors S2C 283 `returnSceneInfo` and S2C 1500 `responseMonthSignData` sit on
# either side of that registration and share the same +144 dynamic offset.
#
# The client never requests this state at login (`sendRequestPrisonFightData`,
# C2S 672, is sent only from the panel's own entry), and the model has no
# field-level fallback: `e_tbBossData = e_tbBossData or {}` with
# `checkActOn` reading `startTime`/`endTime` unconditionally.  So a login
# snapshot is the latest position that provably precedes the first
# PANEL_CLOSE, exactly like the story zero snapshot is the latest position
# before the story read.
#
# WHAT THIS POLICY CLAIMS, AND NOTHING MORE: **the prison-fight activity is not
# open in this session.**  `startTime = 0` and `endTime = 0` make both `LE`
# comparisons in `checkActOn` false for any current unix time, which is the
# branch the client's own `getStartEndTime` also treats as "not open"
# (`startTime > 2 and endTime > 2`, source lines 24..33).  No value here is a
# claim about what the original server sent: every one of the 11 positions is
# labelled `synthetic_compatibility`, `historical_values_recovered` is False,
# and the whole policy is replaceable in a new profile version (D-129/D-21).
PRISON_FIGHT_SNAPSHOT_FORMAT = "NappNNppppp"
PRISON_FIGHT_SNAPSHOT_FIELD_COUNT = 11
PRISON_FIGHT_SNAPSHOT_SEND_TRIGGER = "login_snapshot"
PRISON_FIGHT_SNAPSHOT_HISTORICAL_ORDERING = "unknown"
PRISON_FIGHT_SNAPSHOT_PACKET_COUNT = 1
PRISON_FIGHT_SNAPSHOT_CONTAINER = "e_tbBossData"
PRISON_FIGHT_SNAPSHOT_WIRE_ID = 1370
PRISON_FIGHT_VALUE_CONSTANT = "compatibility_constant"
PRISON_FIGHT_VALUE_ORIGINS = frozenset({PRISON_FIGHT_VALUE_CONSTANT})

#: The client's own `pkgPrisonFightConst.s2tPFCommonList` returns `{}` for an
#: empty string (source lines 188..189), and the `a` decoder yields bytes, so
#: `b""` is the client's own empty literal and not a fabricated row set.
PRISON_FIGHT_BUFF_PROGRESS = b""

#: The 11 S2C 1370 values, one row per wire position:
#: ``(position, symbolic name, wire type, value origin, value payload,
#: field contract, provenance key)``.  The symbolic names are the client's own
#: handler parameters; the values are the closed-state constants justified above.
PRISON_FIGHT_FIELD_TABLE: tuple[
    tuple[int, str, str, str, object, str, str], ...
] = (
    (1, "actVersion", "N", PRISON_FIGHT_VALUE_CONSTANT, 0,
     "S2C1370 position 1 'N' (client field actVersion; no reader on the "
     "PANEL_CLOSE path)", "act_version"),
    (2, "tbBuffProgress", "a", PRISON_FIGHT_VALUE_CONSTANT,
     PRISON_FIGHT_BUFF_PROGRESS,
     "S2C1370 position 2 'a' (client field tbBuffProgress; the client's own "
     "parser returns {} for an empty string and the closing path never reads it)",
     "buff_progress"),
    (3, "angryEndTime", "p", PRISON_FIGHT_VALUE_CONSTANT, 0.0,
     "S2C1370 position 3 'p' (client field angryEndTime; checkBossState guards "
     "on > 0 and the closing path does not call it)", "angry_end_time"),
    (4, "weakEndTime", "p", PRISON_FIGHT_VALUE_CONSTANT, 0.0,
     "S2C1370 position 4 'p' (client field weakEndTime; same > 0 guard, same "
     "closing path)", "weak_end_time"),
    (5, "dBossId", "N", PRISON_FIGHT_VALUE_CONSTANT, 0,
     "S2C1370 position 5 'N' (client field dBossId; only the panel path names a "
     "boss and the panel is unreachable while the activity is closed)",
     "boss_id"),
    (6, "dTotalLayerCount", "N", PRISON_FIGHT_VALUE_CONSTANT, 0,
     "S2C1370 position 6 'N' (client field dTotalLayerCount; panel path only)",
     "total_layer_count"),
    (7, "startTime", "p", PRISON_FIGHT_VALUE_CONSTANT, 0.0,
     "S2C1370 position 7 'p' (client field startTime; checkActOn line 21 "
     "requires startTime <= now and this constant is the closed state)",
     "start_time"),
    (8, "endTime", "p", PRISON_FIGHT_VALUE_CONSTANT, 0.0,
     "S2C1370 position 8 'p' (client field endTime; checkActOn line 21 requires "
     "now <= endTime and this constant is the closed state)", "end_time"),
    (9, "createTime", "p", PRISON_FIGHT_VALUE_CONSTANT, 0.0,
     "S2C1370 position 9 'p' (client field createTime; getBossTime reads it "
     "only from the panel path)", "create_time"),
    (10, "resetTime", "p", PRISON_FIGHT_VALUE_CONSTANT, 0.0,
     "S2C1370 position 10 'p' (client field resetTime; same panel-path reader)",
     "reset_time"),
    (11, "angryHp", "p", PRISON_FIGHT_VALUE_CONSTANT, 0.0,
     "S2C1370 position 11 'p' (client field angryHp; checkCanFightAngryBoss "
     "guards on <= 0)", "angry_hp"),
)

#: One provenance record per KIND of value, keyed the way the field table above
#: refers to it.  Every record says the same thing in machine-readable form:
#: the value is a compatibility constant, not a recovered server value.
_PRISON_FIGHT_SOURCE = (
    "recovered producer analysis: the only writer of e_tbBossData in the whole "
    "APK is pkgS2CPrisonFightMgr.s2c.synPrisonFightBossToCg (source lines "
    "12..30, params=12 = socket + 11 values), registered by "
    "pkgScProtocolMgr.lua:1850 registerS2C(\"NappNNppppp\", "
    "\"synPrisonFightBossToCg\"); the D-02 registration simulation derives "
    "S2C 1370 and the device anchors S2C 283 / S2C 1500 bracket it at the same "
    "+144 dynamic offset -- see reports/task_0166_prison_fight_panel_close_blocker.md"
)
_PRISON_FIGHT_EVIDENCE_GRADE = (
    "A target-client wire and consumer contract (id, format, handler and the "
    "sole writer are read from the shipped bytecode, and the two device "
    "anchors bracket the derived id); C historical values (no producer exists "
    "for any of the 11 values)"
)
PRISON_FIGHT_PROVENANCE: dict[str, tuple[str, str, str, str, str, bool]] = {
    key: (
        _PRISON_FIGHT_SOURCE,
        "synthetic_compatibility",
        _PRISON_FIGHT_EVIDENCE_GRADE,
        reason,
        (
            "none on the PANEL_CLOSE path: checkActOn reads only startTime and "
            "endTime, both of which turn a raise into a false result; the packet "
            "dispatches no event and opens no UI.  If a producer for the "
            "historical values is ever recovered, replace the whole policy in a "
            "new profile version"
        ),
        True,
    )
    for key, reason in {
        "act_version": (
            "closed state: the slot has no reader on the PANEL_CLOSE path, so "
            "the identity 0 is the smallest defensible claim -- not an assertion "
            "that the original server sent 0"
        ),
        "buff_progress": (
            "closed state: the client's own pkgPrisonFightConst.s2tPFCommonList "
            "returns {} for an empty string, so the client's own empty literal "
            "is the only value that cannot fabricate a row set"
        ),
        "angry_end_time": (
            "closed state: the client's own checkBossState guards on > 0, and "
            "the closing path never calls it; 0.0 keeps the guard false"
        ),
        "weak_end_time": (
            "closed state: same > 0 guard as angryEndTime; 0.0 keeps the guard "
            "false"
        ),
        "boss_id": (
            "closed state: a boss id would assert a boss, a layer and a reward "
            "this archive has no producer for; the closing path does not read it"
        ),
        "total_layer_count": (
            "closed state: a layer count is a claim about content this archive "
            "cannot produce; the closing path does not read it"
        ),
        "start_time": (
            "closed state: checkActOn line 21 is the ONE consumer on this path, "
            "and startTime <= now is false at 0.0 for every current unix time"
        ),
        "end_time": (
            "closed state: the second comparison in checkActOn line 21, "
            "now <= endTime, is false at 0.0 -- exactly the one semantic this "
            "policy declares"
        ),
        "create_time": (
            "closed state: getBossTime reads it only from the panel path, which "
            "the closed state makes unreachable"
        ),
        "reset_time": (
            "closed state: same panel-path reader as createTime; 0.0 asserts "
            "nothing about a reset schedule"
        ),
        "angry_hp": (
            "closed state: checkCanFightAngryBoss guards on <= 0, so 0.0 keeps "
            "the angry boss unreachable without inventing any HP value"
        ),
    }.items()
}

EXACT_DOUBLE_INTEGER_LIMIT = 2**53
UINT32_MAX = 0xFFFFFFFF

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# TASK-0500: the RoleValue compatibility tables ship inside the package so a
# fresh clone runs without the private analysis tree.
_DATA_DIR = Path(__file__).resolve().with_name("data")
ROLE_VALUE_DESIGN_PATH = _DATA_DIR / "role_value_compatibility_design.csv"
ROLE_VALUE_MAPPING_PATH = _DATA_DIR / "role_value_wire_mapping.csv"

DAILY_CATALOG = (
    "1,7,0,1000;2,7,0,2000;3,7,0,3000;4,7,0,4000;5,7,0,5000;"
    "6,7,0,6000;7,7,0,7000;8,7,0,8000;9,7,0,9000;10,7,0,10000;"
    "11,7,0,11000;12,7,0,12000;13,7,0,13000;14,7,0,14000;"
    "15,7,0,15000;16,7,0,16000;17,7,0,17000;18,7,0,18000;"
    "19,7,0,19000;20,7,0,20000;21,7,0,21000;22,7,0,22000;"
    "23,7,0,23000;24,7,0,24000;25,7,0,25000;26,7,0,26000;"
    "27,7,0,27000;28,7,0,28000;29,7,0,29000;30,7,0,30000;"
    "31,7,0,31000"
)
CUMULATIVE_CATALOG = (
    "3,7,0,1000;5,7,0,2000;7,7,0,3000;10,7,0,3000"
)
SEVEN_DAY_CATALOG = (
    "1,7,0,10000;2,262,0,1;3,6,0,150;4,262,0,2;"
    "5,250,0,2;6,6,0,250;7,3,4413,1"
)

VALID_ORIGINS = frozenset({
    "official_recovered",
    "deterministic_recovered",
    "cross_version",
    "synthetic_compatibility",
    "unknown",
    # The value is the client's OWN request value, sent back in the matching slot
    # of the reply.  It is neither a recovered server value nor a compatibility
    # choice -- it is the strongest source available where no server producer
    # exists -- so it gets its own word rather than being folded into either
    # (TASK-0140; the four grades are the ones TASK-0139 section F established).
    "request_echo",
})


@dataclass(frozen=True, slots=True)
class Provenance:
    source: str
    origin: str
    evidence_grade: str
    reason: str
    compatibility_goal: str
    compatibility_version: str
    field_contract: str
    risk: str
    replaceable: bool

    def __post_init__(self) -> None:
        required = {
            "source": self.source,
            "origin": self.origin,
            "evidence_grade": self.evidence_grade,
            "reason": self.reason,
            "compatibility_goal": self.compatibility_goal,
            "compatibility_version": self.compatibility_version,
            "field_contract": self.field_contract,
            "risk": self.risk,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"missing provenance field(s): {', '.join(missing)}")
        if self.origin not in VALID_ORIGINS:
            raise ValueError(f"invalid provenance origin: {self.origin!r}")
        if self.compatibility_version not in PROFILE_LINEAGE:
            raise ValueError(
                f"unknown provenance compatibility version: "
                f"{self.compatibility_version!r}"
            )


@dataclass(frozen=True, slots=True)
class ProfileMetadata:
    profile_id: str
    target_client: str
    purpose: str
    status: str


@dataclass(frozen=True, slots=True)
class RoleValueField:
    wire_position: int
    symbolic_name: str
    wire_type: str
    initial_value: float | int
    compatibility_class: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if self.wire_position <= 0:
            raise ValueError(f"invalid RoleValue wire position: {self.wire_position}")
        if not self.symbolic_name:
            raise ValueError("RoleValue symbolic name is required")
        if self.wire_type == "p":
            if isinstance(self.initial_value, bool) or not isinstance(self.initial_value, (int, float)):
                raise ValueError(f"{self.symbolic_name}: p value must be numeric")
            if not math.isfinite(float(self.initial_value)):
                raise ValueError(f"{self.symbolic_name}: p value must be finite")
        elif self.wire_type == "N":
            if isinstance(self.initial_value, bool) or not isinstance(self.initial_value, int):
                raise ValueError(f"{self.symbolic_name}: N value must be an integer")
            if not 0 <= self.initial_value <= UINT32_MAX:
                raise ValueError(f"{self.symbolic_name}: N value outside uint32")
        else:
            raise ValueError(f"{self.symbolic_name}: unsupported wire type {self.wire_type!r}")
        if self.provenance.origin != "synthetic_compatibility":
            raise ValueError(
                f"{self.symbolic_name}: compatibility initial amount must be synthetic"
            )
        if self.compatibility_class not in {
            "C_SYNTHETIC_ZERO_SAFE",
            "D_DEFERRED_WIRE_PLACEHOLDER",
        }:
            raise ValueError(
                f"{self.symbolic_name}: invalid compatibility class {self.compatibility_class!r}"
            )


@dataclass(frozen=True, slots=True)
class SignCatalogs:
    daily: str
    cumulative: str
    seven_day: str
    daily_provenance: Provenance
    cumulative_provenance: Provenance
    seven_day_provenance: Provenance


@dataclass(frozen=True, slots=True)
class SignStateTemplate:
    is_new_month: bool
    sign_count: int
    make_up_time: int
    role_make_up_time: int
    is_today_signed: bool
    sum_claimed: str
    seven_claimed: str
    seven_day: int
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class SceneStateTemplate:
    scene_type: int
    pos_x: int
    pos_y: int
    pos_z: int
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class ActiveSoulRule:
    career: int
    soul_id: int
    provenance: Provenance


def lineage_versions(profile_id: str) -> tuple[str, ...]:
    """Return ``profile_id`` followed by its ancestor versions, nearest first."""

    versions: list[str] = []
    current: str | None = profile_id
    while current is not None:
        if current not in PROFILE_LINEAGE:
            raise ValueError(f"unknown profile id in lineage: {current!r}")
        versions.append(current)
        current = PROFILE_LINEAGE[current]
    return tuple(versions)


@dataclass(frozen=True, slots=True)
class ServerTimePolicy:
    """V2-only, server-global S2C 0 compatibility policy.

    Values are resolved at send time from the revival host clock.  This is an
    explicit compatibility policy, not a recovery of historical CN server time
    values and not a claim about the historical login send ordering (both are
    unknown; see the post-283 forensic report).
    """

    time_policy: str
    open_time_policy: str
    clock_value: int
    power_recover_time_value: int
    historical_ordering: str
    time_provenance: Provenance
    clock_provenance: Provenance
    power_recover_provenance: Provenance
    open_time_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.time_policy != SERVER_TIME_TIME_POLICY:
            raise ValueError(f"unexpected dTime policy: {self.time_policy!r}")
        if self.open_time_policy != SERVER_TIME_OPEN_TIME_POLICY:
            raise ValueError(f"unexpected dOpenTime policy: {self.open_time_policy!r}")
        if self.historical_ordering != SERVER_TIME_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay {SERVER_TIME_HISTORICAL_ORDERING!r}"
            )
        for name, value in (
            ("dClock", self.clock_value),
            ("dPowerRecoverTime", self.power_recover_time_value),
        ):
            if not 0 <= value <= UINT32_MAX:
                raise ValueError(f"S2C0 {name} outside uint32: {value}")
        if self.time_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "S2C0 dTime must carry the recovered dynamic-time semantics"
            )
        for provenance in (
            self.clock_provenance,
            self.power_recover_provenance,
            self.open_time_provenance,
            self.ordering_provenance,
        ):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "S2C0 placeholder fields and ordering must be synthetic_compatibility"
                )
        for provenance in (
            self.time_provenance,
            self.clock_provenance,
            self.power_recover_provenance,
            self.open_time_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V2:
                raise ValueError("ServerTime policy provenance must be v2")

    def protocol_fields(
        self, *, now_unix: int, server_started_unix: int
    ) -> tuple[int, int, int, int]:
        fields = (
            int(now_unix),
            self.clock_value,
            self.power_recover_time_value,
            int(server_started_unix),
        )
        for name, value in zip(
            ("dTime", "dClock", "dPowerRecoverTime", "dOpenTime"), fields, strict=True
        ):
            if not 0 <= value <= UINT32_MAX:
                raise ValueError(f"S2C0 {name} outside uint32: {value}")
        return fields


@dataclass(frozen=True, slots=True)
class MysteryShopPolicy:
    """V3-only, server-global mystery-shop (dType=1) compatibility state.

    The client requires ``le_tbShopGoods[1].dTime`` to exist by the first
    main-city entry (``pkgNpcMgr.mysteryShopIsOpen`` reads it synchronously).
    The revival server provides a coherent closed snapshot: the wire ``dTime``
    is the exact server-time value already sent as S2C0, so the client computes
    ``dDiff <= 0`` (closed).  Historical shop state, values and ordering are
    unknown and are not claimed.  Only S2C487 is sent (no S2C488 goods, no
    S2C489 end notification).
    """

    shop_type: int
    time_policy: str
    availability: str
    goods_count: int
    snapshot_end_sent: bool
    historical_ordering: str
    shop_type_provenance: Provenance
    time_provenance: Provenance
    availability_provenance: Provenance
    goods_provenance: Provenance
    snapshot_end_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.shop_type != MYSTERY_SHOP_TYPE:
            raise ValueError(f"unexpected mystery shop type: {self.shop_type!r}")
        if self.time_policy != MYSTERY_SHOP_TIME_POLICY:
            raise ValueError(f"unexpected mystery-shop dTime policy: {self.time_policy!r}")
        if self.availability != MYSTERY_SHOP_AVAILABILITY:
            raise ValueError(
                f"mystery-shop availability must stay {MYSTERY_SHOP_AVAILABILITY!r}"
            )
        if self.goods_count != MYSTERY_SHOP_GOODS_COUNT:
            raise ValueError("mystery-shop goods must stay empty (no invented goods)")
        if self.snapshot_end_sent is not MYSTERY_SHOP_SNAPSHOT_END_SENT:
            raise ValueError("mystery-shop snapshot end must stay unsent (S2C489 omitted)")
        if self.historical_ordering != MYSTERY_SHOP_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay {MYSTERY_SHOP_HISTORICAL_ORDERING!r}"
            )
        if self.shop_type_provenance.origin != "deterministic_recovered":
            raise ValueError("mystery shop_type must be deterministic_recovered")
        for provenance in (
            self.time_provenance,
            self.availability_provenance,
            self.goods_provenance,
            self.snapshot_end_provenance,
            self.ordering_provenance,
        ):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError("mystery-shop state must be synthetic_compatibility")
        for provenance in (
            self.shop_type_provenance,
            self.time_provenance,
            self.availability_provenance,
            self.goods_provenance,
            self.snapshot_end_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V3:
                raise ValueError("MysteryShop policy provenance must be v3")

    def protocol_fields(self, *, server_time_dtime: int) -> tuple[int, int]:
        """Return S2C487 ``Np`` fields, reusing the resolved S2C0 dTime exactly.

        The dTime keeps the ``p`` (IEEE-754 double) contract, not the S2C0
        uint32 contract: only finiteness / non-negativity / exact-integer
        representability are required.
        """

        if isinstance(server_time_dtime, bool) or not isinstance(server_time_dtime, int):
            raise ValueError("mystery-shop dTime must be the integer S2C0 dTime")
        if not 0 <= server_time_dtime < EXACT_DOUBLE_INTEGER_LIMIT:
            raise ValueError(
                f"mystery-shop dTime outside exact IEEE-754 integer range: {server_time_dtime}"
            )
        return (self.shop_type, server_time_dtime)


@dataclass(frozen=True, slots=True)
class StoryStartPolicy:
    """V4-only, server-global S2C 411 ``returnStoryStart`` compatibility state.

    The recovered client reads ``pkgStoryModelMgr.le_story_list`` unguarded from
    the per-NPC model-load callback of the first main city
    (``pkgNpcMgr.create`` -> ``pkgStoryTrigger_1008.triggerStory`` ->
    ``checkStoryFinish`` -> ``getStoryData``), so the container must exist by the
    time the main-city scene switch runs.  The revival server provides the
    client's own representation of "no story is finished": the empty list.

    An empty list carries **no per-story value, no identity and no count**, so
    nothing is invented -- but it is still a claim, not a recovered value: there
    is no producer for the historical fresh-role snapshot (D-05).  Historical
    login ordering is unknown as well, so the send position is an explicit
    synthetic compatibility ordering (see
    ``max_decoder/analyze_story_ordering.py``), never a claim about the
    historical packet stream.  Only S2C411 is sent: 412/413/414/415 stay
    deliberately unregistered.
    """

    story_list: str
    historical_ordering: str
    snapshot_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.story_list != STORY_START_LIST_POLICY:
            raise ValueError(
                f"story list must stay {STORY_START_LIST_POLICY!r} "
                "(an empty container invents no story row)"
            )
        if self.historical_ordering != STORY_START_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay {STORY_START_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.snapshot_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError("StoryStart state must be synthetic_compatibility")
            if provenance.compatibility_version != PROFILE_ID_V4:
                raise ValueError("StoryStart policy provenance must be v4")

    def protocol_fields(self) -> tuple[()]:
        """Return the S2C411 ``''`` fields: the snapshot has an empty body."""

        return ()


@dataclass(frozen=True, slots=True)
class TeamSnapshotPolicy:
    """V5-only, server-global Team + fabao-select login compatibility state.

    The recovered client's main-city UI init reads team state before it binds
    any main-city button: ``pkgMainUIMgr.init`` line 1130 calls ``updateInfo()``
    -> ``updateFight()`` -> ``pkgTeamDataMgr.getCurFight()`` ->
    ``caclGroupFight`` -> ``pkgPlayerInfoMgr.caclRoleFight()`` ->
    ``pkgEquipModelMgr.getTeamEquipFight(getCurGroupID())`` ->
    ``getRoleEquipInfoList`` -> ``getGroupEquips``, whose body is the UNGUARDED
    ``tbTeamCard[dGroupId].tbEquips``.  Nothing in the whole corpus ever filled
    ``tbTeamCard`` on this server, so ``getCurGroupID()`` stays at its ``-1``
    unset sentinel (``dTeamGroupID = dTeamGroupID or nil``) and
    ``tbTeamCard[-1]`` is nil: the error is raised before ``initBtn()`` at line
    1133, which is the *only* binder of the main-city function buttons
    (``btnRisk`` among them).  Measured on-device in RUN-0026 and present in
    every main-city run since RUN-0011 (TASK-0091 section 2.3).

    The compatibility snapshot is the client's own definition of "this role has
    no team": all five presets stay empty, the named preset carries four empty
    equipment slots, eleven empty card slots and an empty god weapon, and the
    fabao-select container is the client's own three empty slots per preset.
    Nothing about ownership
    or progression is fabricated -- but this IS a labelled assertion about a
    role this archive holds no team rows for, NOT a recovered historical
    snapshot (D-33 section 2: no producer exists for the fresh-role team).

    Historical login ordering is unknown as well, so the send position is an
    explicit synthetic compatibility ordering.  Only S2C 36, 37, 48 and 35 are
    sent: 38/39 carry no state the client reads here, and 49/50 would need
    fabao rows this archive does not have.
    """

    current_group: int
    current_group_policy: str
    empty_slot: str
    historical_ordering: str
    container_provenance: Provenance
    current_group_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if not 1 <= self.current_group <= TEAM_SNAPSHOT_MAX_GROUP:
            raise ValueError(
                "team snapshot current group must be inside the client's own "
                f"legal set 1..{TEAM_SNAPSHOT_MAX_GROUP}, got {self.current_group!r}"
            )
        if self.current_group_policy != TEAM_SNAPSHOT_CURRENT_GROUP_POLICY:
            raise ValueError(
                f"unexpected team current-group policy: {self.current_group_policy!r}"
            )
        if self.empty_slot != TEAM_SNAPSHOT_EMPTY_SLOT:
            raise ValueError(
                "team snapshot slots must stay the client's own empty string "
                "(an empty slot invents no card, equipment or god weapon)"
            )
        if self.historical_ordering != TEAM_SNAPSHOT_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay {TEAM_SNAPSHOT_HISTORICAL_ORDERING!r}"
            )
        for provenance in (
            self.container_provenance,
            self.current_group_provenance,
            self.ordering_provenance,
        ):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "team snapshot state must be synthetic_compatibility"
                )
            if provenance.compatibility_version != PROFILE_ID_V5:
                raise ValueError("TeamSnapshot policy provenance must be v5")

    def team_start_fields(self) -> tuple[()]:
        """S2C36 ``returnTeamStart`` ``''``: the client's own preset reset."""

        return ()

    def select_magic_start_fields(self) -> tuple[()]:
        """S2C48 ``returnSelectMagicStart`` ``'': ``initTeamFabaoData()``."""

        return ()

    def team_info_fields(self) -> tuple[object, ...]:
        """S2C37 ``returnTeamInfo`` ``'N' + 16*'a'``: one empty preset record."""

        return (self.current_group,) + (self.empty_slot,) * (
            TEAM_SNAPSHOT_EQUIP_SLOTS + 1 + TEAM_SNAPSHOT_CARD_SLOTS
        )

    def group_id_fields(self) -> tuple[int]:
        """S2C35 ``returnGroupId`` ``'N'``: name the current preset."""

        return (self.current_group,)


@dataclass(frozen=True, slots=True)
class RechargeActivityPolicy:
    """V6-only, server-global recharge-activity login compatibility state.

    The first Lua error of RUN-0027 is raised from the main-city UI init, one
    frame *after* ``initBtn()`` bound every function button:

    ``pkgMainUIMgr.lua:1133 init`` -> ``initBtn`` -> ``:916 initBtnReplace`` ->
    ``:870 checkRedPoint`` -> ``:962 pkgUIMainUIControl.checkRedPointOnMainUI``
    -> ``:204 checkRedPointRechargeActivity`` -> ``:502
    pkgKoiDataMgr.getGiftState`` -> ``pkgKoiDataMgr.lua:88`` ->
    ``pkgUIRechargeACTaskView.getTask2HadGet`` -> ``pkgUIRechargeACTaskView.lua:70
    tbInfo.bHadGet`` (UNGUARDED), where
    ``tbInfo = pkgRechargeActivityModel.e_tbRechargeActivity.tbGetRecharge[
    tbCfgData.id]`` came back nil (RUN-0027 logcat 01:16:30.354, bytecode line
    table in ``max_decoder/analyze_recharge_activity_red_point.py``).

    ``pkgKoiDataMgr.getGiftState``'s guard is ``le_tbSelf and
    le_tbSelf.dChargeTime`` -- and ``dChargeTime`` is initialised to **0** by the
    module's own ``le_tbSelf.dChargeTime = le_tbSelf.dChargeTime or 0``.  Zero is
    truthy in Lua, so the guard is always satisfied on a fresh client and the
    unguarded read always runs.  Nothing in the whole corpus ever filled
    ``tbGetRecharge`` on this server: its only producers are
    ``initRechargeStart`` and ``setACRechargeData``, and both are reached only
    from the ``returnACRechargeData*`` family -- which the client never even asks
    for (C2S500 ``sendGetACData`` has no caller anywhere in the 3,372-chunk
    corpus).

    The compatibility state is the client's own initializer:
    ``s2c.returnACRechargeDataStart(dRechargeStart, bReset=true)`` ->
    ``setRechargeStart`` + ``initRechargeStart()``, whose whole body builds one
    ``{bEnough = false, bHadGet = false}`` row per id of the CLIENT'S OWN config
    table ``pkgRechargeActivityCfgMgr.e_rechargeActivityTask``.  No task row, no
    reward and no purchase is invented; the only scalar this policy supplies is
    the activity's recharge total, and it repeats the ``recharge_sum = 0`` the
    archive already ships for this role in S2C10.

    Historical login ordering is unknown, so the send position is an explicit
    synthetic compatibility ordering.  Only S2C1014 is sent: 1013/1015/1016/1017
    carry per-task "already claimed" flags and 1024 ends a data block, so
    sending them would assert reward history this archive has none of.
    """

    recharge_start: int
    recharge_start_policy: str
    reset_rows: bool
    historical_ordering: str
    container_provenance: Provenance
    recharge_start_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if not 0 <= self.recharge_start <= EXACT_DOUBLE_INTEGER_LIMIT:
            raise ValueError(
                "recharge-activity total must stay the archive's own shipped "
                f"non-negative role value, got {self.recharge_start!r}"
            )
        if self.recharge_start_policy != RECHARGE_ACTIVITY_RECHARGE_START_POLICY:
            raise ValueError(
                f"unexpected recharge-activity total policy: "
                f"{self.recharge_start_policy!r}"
            )
        if self.reset_rows is not RECHARGE_ACTIVITY_RESET_ROWS:
            raise ValueError(
                "bReset must stay true: only the client's own initRechargeStart "
                "creates the rows, and it creates them from the client's own config"
            )
        if self.historical_ordering != RECHARGE_ACTIVITY_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay "
                f"{RECHARGE_ACTIVITY_HISTORICAL_ORDERING!r}"
            )
        for provenance in (
            self.container_provenance,
            self.recharge_start_provenance,
            self.ordering_provenance,
        ):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "recharge-activity state must be synthetic_compatibility"
                )
            if provenance.compatibility_version != PROFILE_ID_V6:
                raise ValueError("RechargeActivity policy provenance must be v6")

    def start_fields(self) -> tuple[int, bool]:
        """S2C1014 ``returnACRechargeDataStart`` (dRechargeStart, bReset)."""

        return (self.recharge_start, self.reset_rows)


@dataclass(frozen=True, slots=True)
class ChapterItemInitPolicy:
    """V7-only, server-global chapter-map item container compatibility state.

    RUN-0029's first Lua error is raised from the world map's own init, on the
    *first* chapter, before any chapter button was bound:

    ``pkgChapterMainUIViewMgr.lua:446 init`` -> ``:245 initChapter`` ->
    ``:63 initChapterBtnNormalStatus`` -> ``pkgChapterInfoMgr.lua:165
    isChapterPass`` -> ``:128 getChapterProgress`` -> the anonymous function
    ``<pkgChapterInfoMgr.lua:208>`` (``isItemFightOver``) -> ``:213``
    ``attempt to index global 'le_tbChapterItemInfo' (a nil value)``
    (RUN-0029 logcat 03:31:25.490).

    That global is created by exactly one recovered function,
    ``pkgChapterInfoMgr.initChapterItemStart()`` (``le_tbChapterItemInfo = {}``,
    source 48-50), whose only caller is the client's own S2C handler
    ``s2c.initChapterItemStart`` -> ``pkgChapterInfoMgr.initChapterItemStart()``.
    The module's own default is ``nil`` (source line 8) and every other writer
    requires the container to exist first, so the world map's unguarded reads can
    only be cleared by the server running the client's own sequence.

    The compatibility state is therefore the client's own empty literal:
    ``S2C 389 initChapterItemStart ''`` runs the initializer, ``S2C 391
    initChapterItemEnd ''`` is the client's own empty end handler.  **No item row
    is sent**: 390 ``initChapterItemInfo`` would assert that a named map item of a
    named chapter has been triggered/completed, and this archive has no such fact
    (the role has never entered a chapter).  The percentage the detail panel then
    shows is derived by the client from its own config tables, not from this
    server.

    Historical login ordering is unknown, so the send position is an explicit
    synthetic compatibility ordering (with the other login-time container pushes,
    before the S2C283 that starts the main-city switch).  392/393 stay
    unregistered: they are per-exploration updates with no producer here.
    """

    container_policy: str
    row_count: int
    historical_ordering: str
    container_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.container_policy != CHAPTER_ITEM_CONTAINER_POLICY:
            raise ValueError(
                f"unexpected chapter-item container policy: {self.container_policy!r}"
            )
        if self.row_count != CHAPTER_ITEM_ROW_COUNT:
            raise ValueError(
                "the chapter-item block must stay empty: only the client's own "
                "initChapterItemStart creates the container, and a row would assert "
                f"map-item completion this archive has no producer for, got "
                f"{self.row_count!r}"
            )
        if self.historical_ordering != CHAPTER_ITEM_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay {CHAPTER_ITEM_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.container_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "chapter-item state must be synthetic_compatibility"
                )
            if provenance.compatibility_version != PROFILE_ID_V7:
                raise ValueError("ChapterItemInit policy provenance must be v7")

    @property
    def packet_count(self) -> int:
        """How many packets this policy puts on the wire (two, always)."""

        return len(CHAPTER_ITEM_PACKET_ORDER)

    def packets(self) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """``(name, fields)`` in the order the client's own code forces."""

        return tuple((name, ()) for name in CHAPTER_ITEM_PACKET_ORDER)


@dataclass(frozen=True, slots=True)
class ExperienceTaskInitPolicy:
    """V8-only, server-global experience-task container compatibility state.

    RUN-0031's confirm tap never reached the client's own ``enterChapter``: the
    chapter detail panel had already been switched off, about a second after the
    node tap opened it, by the client's own countdown tick.  The chain is
    ``pkgExperienceMainUIMgr.lua:62 onCountdown`` (a 1,000 ms repeating timer
    started by ``initExperience`` line 169 -> ``createTimer``, lines 118-124) ->
    ``:73 pkgExperienceMgr.CloseActivityByClient()`` (``le_dEndTime`` is 0 for this
    role, so the "activity is over" arm is always taken) -> ``pkgExperienceMgr.lua:
    178 pkgEventMgr.dispatch(EXPERIENCE_STATUS_CHANGE, ...)`` -> the registered
    listener ``pkgExperienceMainUIMgr.experienceStatusChange`` ->
    ``:199 pkgUtilMgr.setActiveByName(gameObject, "Panel/btnDetail", false)`` ->
    ``:214 changeChapterBtnExperienceStatus`` -> ``:643 pkgExperienceMgr
    .GetTaskListByChapterId(dChapterId)`` -> ``pkgExperienceMgr.lua:203
    `le_tbTaskByChapterId[dChapterId]` (a nil value)`` (RUN-0031 logcat: 59 in
    the whole run, 57 of them once per second from 05:21:55.457 -- the first tick
    of the world map that opened the chapter-1 panel -- to the run's end).

    The nil read is what makes the damage permanent: it raises inside the dispatch,
    so ``onCountdown``'s own cleanup -- ``if le_dTimer then pkgTimerMgr.delete(
    le_dTimer); le_dTimer = nil end`` (lines 74-76) -- never runs, and the tick
    switches ``Panel/btnDetail`` off once per second for the rest of the session.
    With the container present, ``GetTaskListByChapterId(1)`` answers ``nil`` (no
    rows), ``changeChapterBtnExperienceStatus`` short-circuits on its own
    ``isActivityValid()`` test, the dispatch completes, and the timer deletes
    itself: the panel close happens at most once, before any player-opened panel.

    ``le_tbTaskByChapterId``'s only creator is the client's own S2C handler
    ``s2c.returnTaskStart`` (``le_tbTaskByChapterId = {}``, lines 62-66), whose
    module default is ``nil`` (chunk line 17).  The compatibility state is
    therefore that empty literal plus the client's own end handler: **no task row
    is sent**, because a row would assert one experience task of one chapter with
    its own id/state/progress, and this archive has none (D-21).

    Historical login ordering is unknown, so the send position is an explicit
    synthetic compatibility ordering (with the other login-time container pushes,
    before the S2C411 story snapshot and the S2C283 that starts the main-city
    switch).  1310-1312 stay unregistered: they are per-claim reward updates with
    no producer here.
    """

    container_policy: str
    row_count: int
    historical_ordering: str
    container_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.container_policy != EXPERIENCE_TASK_CONTAINER_POLICY:
            raise ValueError(
                f"unexpected experience-task container policy: "
                f"{self.container_policy!r}"
            )
        if self.row_count != EXPERIENCE_TASK_ROW_COUNT:
            raise ValueError(
                "the experience-task block must stay empty: only the client's own "
                "returnTaskStart creates the container, and a row would assert a "
                f"task id/state this archive has no producer for, got "
                f"{self.row_count!r}"
            )
        if self.historical_ordering != EXPERIENCE_TASK_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay {EXPERIENCE_TASK_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.container_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "experience-task state must be synthetic_compatibility"
                )
            if provenance.compatibility_version != PROFILE_ID_V8:
                raise ValueError("ExperienceTaskInit policy provenance must be v8")

    @property
    def packet_count(self) -> int:
        """How many packets this policy puts on the wire (two, always)."""

        return len(EXPERIENCE_TASK_PACKET_ORDER)

    def packets(self) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """``(name, fields)`` in the order the client's own code forces."""

        return tuple((name, ()) for name in EXPERIENCE_TASK_PACKET_ORDER)


@dataclass(frozen=True, slots=True)
class ChapterTaskInitPolicy:
    """V9-only, server-global chapter-task container compatibility state.

    RUN-0032 (TASK-0103) is the first run whose client really started the chapter
    scene: the confirm tap opened it (``Android ACTION_DOWN x=1310 y=645``, logcat
    07:17:46.155, inside the recovered 确认 rectangle) and the client's own
    ``switchScene chapter_qiandengzhen_1 3 nil`` followed at 07:17:46.241.  The AB
    load then *completed* -- ``-- loadEmptyScene``, ``ab_chapter_qiandengzhen_1``,
    ``-- static combile --``, ``ab_chapter_qiandengzhen_1_juqing`` -- and the
    loader called ``pkgSceneMgr.doFinishSwitch``, which raised 1.492 s after the
    switch:

    ``chapter/pkgChapterTaskMgr.lua:62: attempt to index global
    'le_tbChapterTaskInfo' (a nil value)``, reached

    ``pkgSceneMgr.lua:875 doFinishSwitch`` -> ``pkgChapterSceneMgr.lua:112
    le_finishCallBack`` -> ``pkgChapterObjectMgr.lua:385 initIndex2TypeId`` ->
    ``pkgChapterInfoMgr.lua:309 getActiveMap`` -> ``:262 isItemOver`` -> ``:62
    isTaskReceived``.

    ``doFinishSwitch`` (846-892) dispatches ENTER_SCENE at 880, calls
    ``pkgSyncSceneMgr.enterScene`` at 881, prints ``-- doFinishSwitch`` at 882 and
    tears the loading UI down at 884-890 -- every one of them AFTER the callback
    at 875, which is why the run's whole logcat carries ``-- doFinishSwitch``
    exactly twice (main city, world map) and never for the chapter, and why the
    run ends on the client's own loading screen.

    ``le_tbChapterTaskInfo`` is created by exactly one recovered function, the
    S2C handler ``pkgChapterTaskMgr`` exposes as ``initChapterTaskStart``
    (``le_tbChapterTaskInfo = {}``, lines 21-23).  The module's own default is
    ``nil`` (chunk line 6), the chunk writes the global in those two statements
    and no others, and no other chunk of the shipped ``luaScript`` tree writes the
    name at all -- ``analyze_chapter_task_init.assert_whole_tree_writers`` walks
    all 1827 of them.  So the unguarded reads (lines 62, 69, 201, 555) can only be
    cleared by the server running the client's own sequence.

    The compatibility state is therefore the client's own empty literal plus the
    client's own end handler: **no task row is sent**, because a row would assert
    that a specific chapter task is received/completed and this archive has none
    (D-21).  388 ``updateChapterTask`` stays unregistered: a push with no producer
    here.

    Historical login ordering is unknown, so the send position is an explicit
    synthetic compatibility ordering -- immediately before the v7 chapter-item
    pair, because the client's own registration table puts the task family
    (385-388) immediately before the item family (389-392) and both containers are
    read in the same pass of the same finish callback.
    """

    container_policy: str
    row_count: int
    historical_ordering: str
    container_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.container_policy != CHAPTER_TASK_CONTAINER_POLICY:
            raise ValueError(
                f"unexpected chapter-task container policy: {self.container_policy!r}"
            )
        if self.row_count != CHAPTER_TASK_ROW_COUNT:
            raise ValueError(
                "the chapter-task block must stay empty: only the client's own "
                "initChapterTaskStart creates the container, and a row would "
                f"assert chapter-task state this archive has no producer for, got "
                f"{self.row_count!r}"
            )
        if self.historical_ordering != CHAPTER_TASK_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay {CHAPTER_TASK_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.container_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "chapter-task state must be synthetic_compatibility"
                )
            if provenance.compatibility_version != PROFILE_ID_V9:
                raise ValueError("ChapterTaskInit policy provenance must be v9")

    @property
    def packet_count(self) -> int:
        """How many packets this policy puts on the wire (two, always)."""

        return len(CHAPTER_TASK_PACKET_ORDER)

    def packets(self) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """``(name, fields)`` in the order the client's own code forces."""

        return tuple((name, ()) for name in CHAPTER_TASK_PACKET_ORDER)


@dataclass(frozen=True, slots=True)
class DrawCardUnlockPolicy:
    """V15-only, one-row chapter-task state that opens the 祈灵 main screen.

    The gate is the client's own, not an invention: `pkgSystemFuncMgr
    .isFunctionOpen('drawcard')` returns `pkgChapterTaskMgr.isTaskCompleted(1001, 1)
    or dChapter >= 1` (decompiled lines 245-246).  The permanent QA role has never
    passed chapter 1 (1/11 and no EXIT), so `dChapter` is 0 and the only state that
    opens the button is the completed-task key.  `isTaskCompleted` looks up
    `le_tbChapterTaskInfo[dChapterId][GetTaskCompletedId(1001)]`, and
    `GetTaskCompletedId` is `dTaskId + 10000000` -- so the exact row is
    `(1, 10001001)` and its format is the recovered S2C386 `pp`.

    Both values are **synthetic compatibility**, never recovered progress: the
    archive has no observed `initChapterTaskInfo` row, and this policy says so in
    every provenance field.  The row changes no chapter item, no reward, no drop,
    no draw result and no inventory; its only consumer is the gate above (and the
    two `isTaskCompleted(1001, ...)` reads that gate implies).
    """

    chapter_id: int
    completed_task_id: int
    row_format: str
    container_policy: str
    historical_ordering: str
    historical_values_recovered: bool
    replaceable: bool
    row_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.row_format != DRAWCARD_UNLOCK_FORMAT:
            raise ValueError(
                f"drawcard unlock row must stay {DRAWCARD_UNLOCK_FORMAT!r}, "
                f"got {self.row_format!r}"
            )
        if self.chapter_id != DRAWCARD_UNLOCK_CHAPTER_ID:
            raise ValueError(
                "the drawcard gate reads chapter 1 (isTaskCompleted(1001, 1)), "
                f"got {self.chapter_id!r}"
            )
        if self.completed_task_id != DRAWCARD_UNLOCK_COMPLETED_ID:
            raise ValueError(
                "the drawcard gate reads GetTaskCompletedId(1001) = 10001001, "
                f"got {self.completed_task_id!r}"
            )
        if self.container_policy != DRAWCARD_UNLOCK_CONTAINER_POLICY:
            raise ValueError(
                f"unexpected drawcard unlock container policy: "
                f"{self.container_policy!r}"
            )
        if self.historical_ordering != DRAWCARD_UNLOCK_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay "
                f"{DRAWCARD_UNLOCK_HISTORICAL_ORDERING!r}"
            )
        if self.historical_values_recovered:
            raise ValueError(
                "the drawcard unlock row is synthetic; it may not claim "
                "historical_values_recovered"
            )
        if not self.replaceable:
            raise ValueError("the drawcard unlock row must stay replaceable")
        for provenance in (self.row_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "drawcard unlock state must be synthetic_compatibility"
                )
            if provenance.compatibility_version != PROFILE_ID_V15:
                raise ValueError(
                    "DrawCardUnlock policy provenance must be v15"
                )

    @property
    def packet_count(self) -> int:
        """How many packets this policy puts on the wire (one)."""

        return DRAWCARD_UNLOCK_PACKET_COUNT

    @property
    def row_count(self) -> int:
        """How many chapter-task rows it emits (one)."""

        return DRAWCARD_UNLOCK_ROW_COUNT

    def packets(self) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """The single row, in the client's own handler format."""

        return (
            ("initChapterTaskInfo", (self.chapter_id, self.completed_task_id)),
        )


@dataclass(frozen=True, slots=True)
class ChapterMachineInitPolicy:
    """V10-only, server-global chapter-machine container compatibility state.

    RUN-0033 (TASK-0108) is the first run whose client got PAST the statement v9
    repaired.  The confirm tap opened the chapter and the client's own
    ``switchScene chapter_qiandengzhen_1 3 nil`` is in its logcat at 23:50:40.840;
    the AB load then completed -- ``-- loadEmptyScene``, ``ab_chapter_qiandengzhen_1``,
    ``-- static combile --``, ``ab_chapter_qiandengzhen_1_juqing`` -- and the loader
    called ``pkgSceneMgr.doFinishSwitch``, which raised 1.465 s after the switch at
    a *different* line than RUN-0032 did:

    ``chapter/pkgChapterMachineMgr.lua:46: attempt to index global
    'le_tbChapterMachineInfo' (a nil value)``, reached

    ``pkgSceneMgr.lua:875 doFinishSwitch`` -> ``pkgChapterSceneMgr.lua:114
    le_finishCallBack`` -> ``pkgChapterObjectMgr.lua:287 initScene`` -> ``:312
    initAllMachineObj`` -> ``:46 isMachineCompleted``.

    The v9 container did its job: RUN-0032's raise was ``pkgChapterSceneMgr.lua:112
    -> pkgChapterObjectMgr.lua:385 initIndex2TypeId -> ... -> pkgChapterTaskMgr.lua:62``,
    and in RUN-0033 line 112 returns, line 113 (``pkgStoryTrigger.enterChapterScene``)
    returns, and the raise is now one statement later at 114 -- which runs
    ``initScene``, whose own ``pkgChapterTaskMgr.initAllTask`` (line 286) also
    returns before line 287 raises.

    ``le_tbChapterMachineInfo`` is created by exactly one recovered function and the
    module registers it itself: ``chapter/pkgChapterMachineMgr.lua`` writes
    ``s2c.initChapterMachineStart = function() le_tbChapterMachineInfo = {}
    end`` (lines 18-20) into the shared ``s2c`` table.  The module's own default is
    ``nil`` (lines 6-7), and no other chunk of the shipped ``luaScript`` tree
    mentions the name at all -- ``analyze_chapter_machine_init
    .assert_whole_tree_writers`` walks all 1827 of them -- so the unguarded read
    (line 46) can only be cleared by the server running the client's own sequence.

    The compatibility state is therefore the client's own empty literal plus the
    client's own end handler: **no machine row is sent**, because a row would assert
    that a named machine of a named chapter is completed and this archive has none
    (D-21).  401 ``initChapterMachineInfo`` and 403 ``updateChapterMachine`` stay
    unregistered: pushes with no producer here.

    Historical login ordering is unknown, so the send position is an explicit
    synthetic compatibility ordering -- immediately AFTER the v7 chapter-item pair,
    because the client's own registration table puts the machine family (399-403)
    after the item family (389-393) and the machine init is the last chapter-map
    init family this server sends.
    """

    container_policy: str
    row_count: int
    historical_ordering: str
    container_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.container_policy != CHAPTER_MACHINE_CONTAINER_POLICY:
            raise ValueError(
                f"unexpected chapter-machine container policy: "
                f"{self.container_policy!r}"
            )
        if self.row_count != CHAPTER_MACHINE_ROW_COUNT:
            raise ValueError(
                "the chapter-machine block must stay empty: only the client's own "
                "initChapterMachineStart creates the container, and a row would "
                f"assert machine state this archive has no producer for, got "
                f"{self.row_count!r}"
            )
        if self.historical_ordering != CHAPTER_MACHINE_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay "
                f"{CHAPTER_MACHINE_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.container_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "chapter-machine state must be synthetic_compatibility"
                )
            if provenance.compatibility_version != PROFILE_ID_V10:
                raise ValueError("ChapterMachineInit policy provenance must be v10")

    @property
    def packet_count(self) -> int:
        """How many packets this policy puts on the wire (two, always)."""

        return len(CHAPTER_MACHINE_PACKET_ORDER)

    def packets(self) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """``(name, fields)`` in the order the client's own code forces."""

        return tuple((name, ()) for name in CHAPTER_MACHINE_PACKET_ORDER)


@dataclass(frozen=True, slots=True)
class BattleStartField:
    """One of the 19 values of S2C 89, with the source it may take it from.

    ``value_origin`` -- not the wire type -- is what a reviewer has to read: a
    ``compatibility_constant`` is a labelled choice, a ``request_echo`` copies the
    client's own request slot, and a ``reply_counter`` is minted per reply.  The
    constants are validated here; the echoed values cannot be, so they are
    validated when the reply is assembled (``BattleStartReplyPolicy.reply_fields``).
    """

    wire_position: int
    symbolic_name: str
    wire_type: str
    value_origin: str
    constant_value: object | None
    echo_index: int | None
    provenance: Provenance
    #: Only `request_seed` fields use this: the 0-based C2S 69 slot the seed is
    #: derived from (TASK-0237).  It is deliberately separate from
    #: ``echo_index`` because the value is NOT the request's own value -- it is
    #: `3278 + request_value * k` -- so a reviewer can never mistake the two.
    seed_request_index: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.wire_position <= BATTLE_START_REPLY_FIELD_COUNT:
            raise ValueError(
                f"invalid S2C89 wire position: {self.wire_position}"
            )
        if self.wire_type not in {"N", "y", "p", "a"}:
            raise ValueError(
                f"S2C89 position {self.wire_position}: unsupported wire type "
                f"{self.wire_type!r}"
            )
        if self.value_origin not in BATTLE_START_VALUE_ORIGINS:
            raise ValueError(
                f"S2C89 position {self.wire_position}: invalid value origin "
                f"{self.value_origin!r}"
            )
        if self.value_origin == BATTLE_START_VALUE_ECHO:
            if self.constant_value is not None:
                raise ValueError(
                    f"S2C89 position {self.wire_position}: an echoed field carries "
                    "no constant"
                )
            if self.seed_request_index is not None:
                raise ValueError(
                    f"S2C89 position {self.wire_position}: an echoed field is not "
                    "a derived seed"
                )
            if (
                isinstance(self.echo_index, bool)
                or not isinstance(self.echo_index, int)
                or not 0 <= self.echo_index < BATTLE_START_REQUEST_FIELD_COUNT
            ):
                raise ValueError(
                    f"S2C89 position {self.wire_position}: echo index outside the "
                    f"C2S69 request ({self.echo_index!r})"
                )
            if self.provenance.origin != BATTLE_START_VALUE_ECHO:
                raise ValueError(
                    f"S2C89 position {self.wire_position}: an echoed field must say "
                    f"so in its provenance, got {self.provenance.origin!r}"
                )
        else:
            if self.echo_index is not None:
                raise ValueError(
                    f"S2C89 position {self.wire_position}: a non-echoed field "
                    "carries no echo index"
                )
            if self.value_origin == BATTLE_START_VALUE_COUNTER:
                if self.constant_value is not None:
                    raise ValueError(
                        f"S2C89 position {self.wire_position}: the counter value is "
                        "minted at send time and cannot be a constant"
                    )
                if self.seed_request_index is not None:
                    raise ValueError(
                        f"S2C89 position {self.wire_position}: the reply counter is "
                        "not derived from the request"
                    )
            elif self.value_origin == BATTLE_START_VALUE_REQUEST_SEED:
                if self.wire_type != "N":
                    raise ValueError(
                        f"S2C89 position {self.wire_position}: a derived seed is a "
                        f"uint32 (wire 'N'), got {self.wire_type!r}"
                    )
                if self.constant_value is not None:
                    raise ValueError(
                        f"S2C89 position {self.wire_position}: a derived seed is "
                        "computed per reply and cannot be a constant"
                    )
                if (
                    isinstance(self.seed_request_index, bool)
                    or not isinstance(self.seed_request_index, int)
                    or not 0 <= self.seed_request_index
                    < BATTLE_START_REQUEST_FIELD_COUNT
                ):
                    raise ValueError(
                        f"S2C89 position {self.wire_position}: seed request index "
                        f"outside the C2S69 request ({self.seed_request_index!r})"
                    )
                if self.provenance.origin != "synthetic_compatibility":
                    raise ValueError(
                        f"S2C89 position {self.wire_position}: k is a labelled "
                        "compatibility choice and must say so, got "
                        f"{self.provenance.origin!r}"
                    )
            elif self.constant_value is None:
                raise ValueError(
                    f"S2C89 position {self.wire_position}: a compatibility constant "
                    "carries a value"
                )
            else:
                if self.seed_request_index is not None:
                    raise ValueError(
                        f"S2C89 position {self.wire_position}: only a derived seed "
                        "names a request slot"
                    )
        # The one recovered constant, and the reason it is allowed to be one.  An
        # echoed field already asserted its own origin above.
        if self.value_origin == BATTLE_START_VALUE_ECHO:
            pass
        elif self.wire_position in BATTLE_START_RECOVERED_CONSTANT_POSITIONS:
            if self.provenance.origin != "deterministic_recovered":
                raise ValueError(
                    f"S2C89 position {self.wire_position}: the packaged constant "
                    "must stay deterministic_recovered, got "
                    f"{self.provenance.origin!r}"
                )
        elif self.provenance.origin != "synthetic_compatibility":
            raise ValueError(
                f"S2C89 position {self.wire_position}: every value that is not the "
                "packaged constant is a compatibility choice and must say so, got "
                f"{self.provenance.origin!r}"
            )
        if self.wire_type == "N":
            if self.value_origin == BATTLE_START_VALUE_COUNTER:
                # The start-time slot: its value is minted per reply and is
                # range-checked where it is assembled, not here.
                pass
            elif self.value_origin == BATTLE_START_VALUE_ECHO:
                # The echoed value does not exist until a request does; it is
                # validated in `BattleStartReplyPolicy._echo_value`.
                pass
            elif self.value_origin == BATTLE_START_VALUE_REQUEST_SEED:
                # The derived seed does not exist until a request does; it is
                # validated (and range-checked) in
                # `BattleStartReplyPolicy.reply_fields`.
                pass
            elif (
                isinstance(self.constant_value, bool)
                or not isinstance(self.constant_value, int)
                or not 0 <= self.constant_value <= UINT32_MAX
            ):
                raise ValueError(
                    f"S2C89 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not a uint32"
                )
        elif self.value_origin == BATTLE_START_VALUE_COUNTER:
            raise ValueError(
                f"S2C89 position {self.wire_position}: the reply counter is only "
                "defined for the uint32 start-time slot"
            )
        elif self.wire_type == "y" and self.value_origin != BATTLE_START_VALUE_ECHO:
            if not isinstance(self.constant_value, bool):
                raise ValueError(
                    f"S2C89 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not a boolean"
                )
        elif self.wire_type == "a" and self.value_origin != BATTLE_START_VALUE_ECHO:
            # The `a` decoder yields bytes, so a constant written as bytes is
            # exactly what reading the wire back gives.
            if not isinstance(self.constant_value, bytes):
                raise ValueError(
                    f"S2C89 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not a string"
                )


@dataclass(frozen=True, slots=True)
class BattleStartReplyPolicy:
    """S2C 89 ``startPveBattle`` compatibility state (introduced in v11).

    The full justification of every one of the 19 values -- what each position is
    called by the client's own handler, which branch the second value selects, and
    why the remaining fields may only echo the request -- is written above the
    ``BATTLE_START_*`` constants and in ``BATTLE_START_PROVENANCE``.  What this
    class enforces is that the table cannot drift from the recovered format and
    that no position can quietly upgrade its own provenance.

    ``introduced_in`` names which profile version's value contract this object
    implements: v11 echoes C2S 69's own ``dSyncCount`` in position 7, v17 mints a
    per-reply counter there (TASK-0182), and v19 keeps that counter while making
    positions 5/6 type-aware (TASK-0237).  The profile version gate refuses to
    hand a later policy to an earlier profile, so the contracts cannot be mixed
    on the wire.
    """

    reply_format: str
    request_format: str
    fields: tuple[BattleStartField, ...]
    historical_ordering: str
    reply_provenance: Provenance
    ordering_provenance: Provenance
    introduced_in: str

    def __post_init__(self) -> None:
        if self.introduced_in not in (
            PROFILE_ID_V11, PROFILE_ID_V17, PROFILE_ID_V19,
        ):
            raise ValueError(
                f"BattleStartReply policy identity must be v11, v17 or v19, got "
                f"{self.introduced_in!r}"
            )
        if self.reply_format != BATTLE_START_REPLY_FORMAT:
            raise ValueError(
                f"S2C89 format must stay {BATTLE_START_REPLY_FORMAT!r}, got "
                f"{self.reply_format!r}"
            )
        if self.request_format != BATTLE_START_REQUEST_FORMAT:
            raise ValueError(
                f"the request this policy answers must stay "
                f"{BATTLE_START_REQUEST_FORMAT!r}, got {self.request_format!r}"
            )
        if len(self.fields) != BATTLE_START_REPLY_FIELD_COUNT:
            raise ValueError(
                f"S2C89 carries {BATTLE_START_REPLY_FIELD_COUNT} values, got "
                f"{len(self.fields)}"
            )
        positions = [field.wire_position for field in self.fields]
        if positions != list(range(1, BATTLE_START_REPLY_FIELD_COUNT + 1)):
            raise ValueError(
                f"S2C89 positions must cover 1..{BATTLE_START_REPLY_FIELD_COUNT} "
                "in order"
            )
        wire_format = "".join(field.wire_type for field in self.fields)
        if wire_format != self.reply_format:
            raise ValueError(
                f"S2C89 wire types {wire_format!r} do not match the recovered "
                f"format {self.reply_format!r}"
            )
        if self.historical_ordering != BATTLE_START_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay "
                f"{BATTLE_START_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.reply_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the battle-start reply is a compatibility choice and must "
                    "say so"
                )
            if provenance.compatibility_version != PROFILE_ID_V11:
                raise ValueError("BattleStartReply policy provenance must be v11")
        # The one position each contract changes, and only that position.  The
        # v11 policy keeps every field at v11; the v17 policy moves exactly
        # position 7's provenance to v17 and keeps the other 18 at v11; the v19
        # policy keeps that v17 counter and moves exactly positions 5/6 to v19.
        sync_field = self.fields[BATTLE_START_SYNC_COUNT_POSITION - 1]
        expected_value_origin = (
            BATTLE_START_VALUE_COUNTER
            if self.introduced_in in (PROFILE_ID_V17, PROFILE_ID_V19)
            else BATTLE_START_VALUE_ECHO
        )
        if sync_field.value_origin != expected_value_origin:
            raise ValueError(
                f"S2C89 position {BATTLE_START_SYNC_COUNT_POSITION}: "
                f"{self.introduced_in} policy requires value origin "
                f"{expected_value_origin!r}, got {sync_field.value_origin!r}"
            )
        if self.introduced_in == PROFILE_ID_V19:
            type_field = self.fields[BATTLE_START_TYPE_POSITION - 1]
            seed_field = self.fields[BATTLE_START_SEED_POSITION - 1]
            if (
                type_field.value_origin != BATTLE_START_VALUE_ECHO
                or type_field.echo_index != BATTLE_START_REQUEST_TYPE_INDEX
                or type_field.provenance.origin != BATTLE_START_VALUE_ECHO
            ):
                raise ValueError(
                    "the v19 policy must echo the request's pveOrPvpType in "
                    "S2C89 position 5"
                )
            if (
                seed_field.value_origin != BATTLE_START_VALUE_REQUEST_SEED
                or seed_field.seed_request_index
                != BATTLE_START_REQUEST_TYPE_INDEX
            ):
                raise ValueError(
                    "the v19 policy must derive the S2C89 position 6 seed from "
                    "the request's pveOrPvpType"
                )
        for field in self.fields:
            if self.introduced_in == PROFILE_ID_V19:
                if field.wire_position in BATTLE_START_TYPE_AWARE_POSITIONS:
                    expected_version = PROFILE_ID_V19
                elif field.wire_position == BATTLE_START_SYNC_COUNT_POSITION:
                    expected_version = PROFILE_ID_V17
                else:
                    expected_version = PROFILE_ID_V11
            elif (
                self.introduced_in == PROFILE_ID_V17
                and field.wire_position == BATTLE_START_SYNC_COUNT_POSITION
            ):
                expected_version = PROFILE_ID_V17
            else:
                expected_version = PROFILE_ID_V11
            if field.provenance.compatibility_version != expected_version:
                raise ValueError(
                    f"S2C89 position {field.wire_position}: policy provenance must "
                    f"be {expected_version!r} under {self.introduced_in}, got "
                    f"{field.provenance.compatibility_version!r}"
                )

    @property
    def packet_count(self) -> int:
        """How many packets one answered request puts on the wire (one, always)."""

        return 1

    def field_origins(self) -> dict[str, str]:
        """``symbolic name -> provenance origin``, for the wire audit and events."""

        return {field.symbolic_name: field.provenance.origin for field in self.fields}

    def value_origins(self) -> dict[str, str]:
        """``symbolic name -> value origin`` -- where each wire value comes from.

        Deliberately separate from :meth:`field_origins`: the provenance origin
        says what evidence backs a position, the value origin says whether the
        value is a constant, an echo of the request or a per-reply counter.
        """

        return {field.symbolic_name: field.value_origin for field in self.fields}

    def field_names(self) -> tuple[str, ...]:
        return tuple(field.symbolic_name for field in self.fields)

    @property
    def seed_policy(self) -> str:
        """The machine-readable name of this policy's `dRawRandomSeed` rule."""

        if self.introduced_in == PROFILE_ID_V19:
            return BATTLE_START_TYPE_RANDOM_SEED_POLICY
        return BATTLE_START_RANDOM_SEED_POLICY

    def _seed_value(self, field: BattleStartField, value: object) -> int:
        """`3278 + request type * k` for one reply (TASK-0237, v19 only)."""

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= UINT32_MAX
        ):
            raise ValueError(
                f"C2S69 position {(field.seed_request_index or 0) + 1}: "
                f"{value!r} cannot derive S2C89 position "
                f"{field.wire_position} 'N'"
            )
        seed = BATTLE_START_RANDOM_SEED_BASE + value * BATTLE_START_RANDOM_SEED_K
        if not 0 <= seed <= UINT32_MAX:
            raise ValueError(
                f"C2S69 position {(field.seed_request_index or 0) + 1}: derived "
                f"seed {seed} is not a uint32"
            )
        return seed

    def _echo_value(self, field: BattleStartField, value: object) -> object:
        assert field.echo_index is not None
        if field.wire_type == "N":
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= UINT32_MAX
            ):
                raise ValueError(
                    f"C2S69 position {field.echo_index + 1}: {value!r} cannot be "
                    f"echoed as S2C89 position {field.wire_position} 'N'"
                )
            return value
        if field.wire_type == "p":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"C2S69 position {field.echo_index + 1}: {value!r} cannot be "
                    f"echoed as S2C89 position {field.wire_position} 'p'"
                )
            if not math.isfinite(float(value)):
                raise ValueError(
                    f"C2S69 position {field.echo_index + 1}: non-finite value "
                    f"{value!r}"
                )
            return float(value)
        if field.wire_type == "a":
            if not isinstance(value, (bytes, bytearray)):
                raise ValueError(
                    f"C2S69 position {field.echo_index + 1}: {value!r} cannot be "
                    f"echoed as S2C89 position {field.wire_position} 'a'"
                )
            return bytes(value)
        raise AssertionError(f"unhandled echo wire type {field.wire_type!r}")

    def reply_fields(
        self, *, request_fields: tuple[object, ...], sf_start_time: int
    ) -> tuple[object, ...]:
        """Assemble the 19 values for ONE reply to ONE ``C2S 69``.

        ``request_fields`` is the decoded request exactly as the transport
        decoded it (13 values of ``yNNpppppppppa``); ``sf_start_time`` is minted by
        the caller, one distinct value per reply.
        """

        if len(request_fields) != BATTLE_START_REQUEST_FIELD_COUNT:
            raise ValueError(
                f"C2S69 carries {BATTLE_START_REQUEST_FIELD_COUNT} values, got "
                f"{len(request_fields)}"
            )
        if (
            isinstance(sf_start_time, bool)
            or not isinstance(sf_start_time, int)
            or not 0 < sf_start_time <= UINT32_MAX
        ):
            raise ValueError(
                f"sfStartTime must be a non-zero uint32, got {sf_start_time!r}"
            )
        values: list[object] = []
        for field in self.fields:
            if field.value_origin == BATTLE_START_VALUE_COUNTER:
                values.append(sf_start_time)
            elif field.value_origin == BATTLE_START_VALUE_ECHO:
                values.append(
                    self._echo_value(field, request_fields[field.echo_index])
                )
            elif field.value_origin == BATTLE_START_VALUE_REQUEST_SEED:
                index = field.seed_request_index
                assert index is not None
                values.append(self._seed_value(field, request_fields[index]))
            else:
                values.append(field.constant_value)
        return tuple(values)


@dataclass(frozen=True, slots=True)
class BattleGameOverField:
    """One of the 2 values of S2C 96, with the source it may take it from.

    Deliberately narrower than ``BattleStartField``: this edge has no recovered
    constant, no echo and no counter -- every value is a labelled compatibility
    constant -- so the type refuses the other origins instead of carrying fields
    it can never be handed.  A future slice that finds a producer for one of them
    adds the origin here, in a new profile version.
    """

    wire_position: int
    symbolic_name: str
    wire_type: str
    value_origin: str
    constant_value: object | None
    provenance: Provenance

    def __post_init__(self) -> None:
        if not 1 <= self.wire_position <= BATTLE_GAME_OVER_REPLY_FIELD_COUNT:
            raise ValueError(f"invalid S2C96 wire position: {self.wire_position}")
        if self.wire_type not in {"N", "a"}:
            raise ValueError(
                f"S2C96 position {self.wire_position}: unsupported wire type "
                f"{self.wire_type!r}"
            )
        if self.value_origin not in BATTLE_GAME_OVER_VALUE_ORIGINS:
            raise ValueError(
                f"S2C96 position {self.wire_position}: invalid value origin "
                f"{self.value_origin!r}"
            )
        if self.constant_value is None:
            raise ValueError(
                f"S2C96 position {self.wire_position}: a compatibility constant "
                "carries a value"
            )
        if self.provenance.origin != "synthetic_compatibility":
            raise ValueError(
                f"S2C96 position {self.wire_position}: this edge has no recovered "
                "value, so it must be labelled synthetic_compatibility, got "
                f"{self.provenance.origin!r}"
            )
        if self.wire_type == "N":
            if (
                isinstance(self.constant_value, bool)
                or not isinstance(self.constant_value, int)
                or not 0 <= self.constant_value <= UINT32_MAX
            ):
                raise ValueError(
                    f"S2C96 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not a uint32"
                )
        else:
            # The `a` decoder yields bytes, so a constant written as bytes is
            # exactly what reading the wire back gives.
            if not isinstance(self.constant_value, bytes):
                raise ValueError(
                    f"S2C96 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not a string"
                )


@dataclass(frozen=True, slots=True)
class PrisonFightBossField:
    """One of the 11 values of S2C 1370, with the source it may take it from.

    Deliberately narrower than ``BattleStartField``: this edge has no recovered
    constant, no echo and no counter -- every value is a labelled compatibility
    constant -- so the type refuses the other origins instead of carrying fields
    it can never be handed.  A future slice that finds a producer for one of
    them adds the origin here, in a new profile version.
    """

    wire_position: int
    symbolic_name: str
    wire_type: str
    value_origin: str
    constant_value: object | None
    provenance: Provenance

    def __post_init__(self) -> None:
        if not 1 <= self.wire_position <= PRISON_FIGHT_SNAPSHOT_FIELD_COUNT:
            raise ValueError(f"invalid S2C1370 wire position: {self.wire_position}")
        if self.wire_type not in {"N", "a", "p"}:
            raise ValueError(
                f"S2C1370 position {self.wire_position}: unsupported wire type "
                f"{self.wire_type!r}"
            )
        if self.value_origin not in PRISON_FIGHT_VALUE_ORIGINS:
            raise ValueError(
                f"S2C1370 position {self.wire_position}: invalid value origin "
                f"{self.value_origin!r}"
            )
        if self.constant_value is None:
            raise ValueError(
                f"S2C1370 position {self.wire_position}: a compatibility constant "
                "carries a value"
            )
        if self.provenance.origin != "synthetic_compatibility":
            raise ValueError(
                f"S2C1370 position {self.wire_position}: this edge has no recovered "
                "value, so it must be labelled synthetic_compatibility, got "
                f"{self.provenance.origin!r}"
            )
        if self.provenance.compatibility_version != PROFILE_ID_V13:
            raise ValueError(
                f"S2C1370 position {self.wire_position}: policy provenance must "
                "be v13"
            )
        if self.wire_type == "N":
            if (
                isinstance(self.constant_value, bool)
                or not isinstance(self.constant_value, int)
                or not 0 <= self.constant_value <= UINT32_MAX
            ):
                raise ValueError(
                    f"S2C1370 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not a uint32"
                )
        elif self.wire_type == "p":
            if isinstance(self.constant_value, bool) or not isinstance(
                self.constant_value, (int, float)
            ):
                raise ValueError(
                    f"S2C1370 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not numeric"
                )
            if not math.isfinite(float(self.constant_value)):
                raise ValueError(
                    f"S2C1370 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not finite"
                )
        else:
            # The `a` decoder yields bytes, so a constant written as bytes is
            # exactly what reading the wire back gives.
            if not isinstance(self.constant_value, bytes):
                raise ValueError(
                    f"S2C1370 position {self.wire_position}: constant "
                    f"{self.constant_value!r} is not a string"
                )


@dataclass(frozen=True, slots=True)
class BattleGameOverReplyPolicy:
    """V12-only S2C 96 ``battlePveGameOverEnd`` compatibility state.

    The justification of both values -- the exhaustive whole-APK consumer scan,
    the handler's own bytecode and the chapter settlement screen's data source --
    is written above the ``BATTLE_GAME_OVER_*`` constants.  What this class
    enforces is that the reply cannot drift from the recovered row it unlocks and
    that no position can quietly upgrade its own provenance.
    """

    reply_format: str
    request_format: str
    fields: tuple[BattleGameOverField, ...]
    unlock_names: tuple[str, ...]
    answered_request: str
    historical_ordering: str
    reply_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.reply_format != BATTLE_GAME_OVER_REPLY_FORMAT:
            raise ValueError(
                f"S2C96 format must stay {BATTLE_GAME_OVER_REPLY_FORMAT!r}, got "
                f"{self.reply_format!r}"
            )
        if self.request_format != BATTLE_GAME_OVER_REQUEST_FORMAT:
            raise ValueError(
                f"the request this policy answers must stay "
                f"{BATTLE_GAME_OVER_REQUEST_FORMAT!r}, got {self.request_format!r}"
            )
        if len(self.fields) != BATTLE_GAME_OVER_REPLY_FIELD_COUNT:
            raise ValueError(
                f"S2C96 carries {BATTLE_GAME_OVER_REPLY_FIELD_COUNT} values, got "
                f"{len(self.fields)}"
            )
        positions = [field.wire_position for field in self.fields]
        if positions != list(range(1, BATTLE_GAME_OVER_REPLY_FIELD_COUNT + 1)):
            raise ValueError(
                f"S2C96 positions must cover 1.."
                f"{BATTLE_GAME_OVER_REPLY_FIELD_COUNT} in order"
            )
        wire_format = "".join(field.wire_type for field in self.fields)
        if wire_format != self.reply_format:
            raise ValueError(
                f"S2C96 wire types {wire_format!r} do not match the recovered "
                f"format {self.reply_format!r}"
            )
        names = [field.symbolic_name for field in self.fields]
        if len(set(names)) != len(names):
            raise ValueError(
                "S2C96 symbolic names must be unique: the audit and every event "
                "record key their per-field provenance by name"
            )
        if tuple(self.unlock_names) != BATTLE_GAME_OVER_UNLOCK_NAMES:
            raise ValueError(
                "the S2C side unlocks exactly "
                f"{BATTLE_GAME_OVER_UNLOCK_NAMES!r}, got {self.unlock_names!r}"
            )
        if self.answered_request != BATTLE_GAME_OVER_REGISTERED_REQUEST:
            raise ValueError(
                "this policy answers only "
                f"{BATTLE_GAME_OVER_REGISTERED_REQUEST!r}, got "
                f"{self.answered_request!r}"
            )
        if self.historical_ordering != BATTLE_GAME_OVER_HISTORICAL_ORDERING:
            raise ValueError(
                f"historical ordering must stay "
                f"{BATTLE_GAME_OVER_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.reply_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the battle-end reply is a compatibility choice and must say so"
                )
            if provenance.compatibility_version != PROFILE_ID_V12:
                raise ValueError("BattleGameOverReply policy provenance must be v12")
        for field in self.fields:
            if field.provenance.compatibility_version != PROFILE_ID_V12:
                raise ValueError(
                    f"S2C96 position {field.wire_position}: policy provenance must "
                    "be v12"
                )

    @property
    def packet_count(self) -> int:
        """How many packets one answered request puts on the wire (one, always)."""

        return 1

    def field_origins(self) -> dict[str, str]:
        """``symbolic name -> provenance origin``, for the wire audit and events."""

        return {field.symbolic_name: field.provenance.origin for field in self.fields}

    def field_names(self) -> tuple[str, ...]:
        return tuple(field.symbolic_name for field in self.fields)

    def reply_fields(self) -> tuple[object, ...]:
        """Assemble the 2 values for ONE reply to ONE C2S 67.

        There is no request argument and no minted value: the verdict above says
        the chapter branch reads neither slot, so every value is the labelled
        constant the field table declares.  The signature is deliberately
        argument-less, so a later slice cannot start consuming a request here
        without changing this contract on purpose.
        """

        return tuple(field.constant_value for field in self.fields)


@dataclass(frozen=True, slots=True)
class BabelSettlementPushPolicy:
    """V20-only S2C 580 ``returnBabelInfo`` post-settlement push.

    It is not a reply to any client request: the client never asks for S2C 580.
    It is the ONE packet that can change floor/state/failCount/realm (D-187,
    D-192), pushed once after a 73 settlement that this same connection paired
    with its own C2S 69.  The values are not constants of the profile -- they
    are the validated dev-play overlay's values, so this push can only replay
    the state the session already announced at login ("如实回推").

    What this class enforces is that the push cannot drift from the recovered
    S2C 580 layout, that it can only ever be triggered by a babel settlement,
    and that it cannot quietly relabel its own synthetic provenance.
    """

    wire_id: int
    reply_format: str
    field_names: tuple[str, ...]
    send_trigger: str
    trigger_request: str
    value_source: str
    max_per_settlement: int
    historical_ordering: str
    value_provenance: Provenance
    ordering_provenance: Provenance
    introduced_in: str

    def __post_init__(self) -> None:
        if self.wire_id != BABEL_SETTLEMENT_PUSH_WIRE_ID:
            raise ValueError(
                f"the settlement push must stay S2C "
                f"{BABEL_SETTLEMENT_PUSH_WIRE_ID}, got {self.wire_id!r}"
            )
        if self.reply_format != BABEL_SETTLEMENT_PUSH_FORMAT:
            raise ValueError(
                f"S2C580 format must stay {BABEL_SETTLEMENT_PUSH_FORMAT!r}, got "
                f"{self.reply_format!r}"
            )
        if tuple(self.field_names) != BABEL_SETTLEMENT_PUSH_FIELD_NAMES:
            raise ValueError(
                "the S2C580 field names must stay "
                f"{BABEL_SETTLEMENT_PUSH_FIELD_NAMES!r}, got "
                f"{self.field_names!r}"
            )
        if self.send_trigger != BABEL_SETTLEMENT_PUSH_SEND_TRIGGER:
            raise ValueError(
                "S2C580 may only be pushed by "
                f"{BABEL_SETTLEMENT_PUSH_SEND_TRIGGER!r}, got "
                f"{self.send_trigger!r}"
            )
        if self.trigger_request != BABEL_SETTLEMENT_PUSH_TRIGGER_REQUEST:
            raise ValueError(
                "the settlement push is triggered by "
                f"{BABEL_SETTLEMENT_PUSH_TRIGGER_REQUEST!r}, got "
                f"{self.trigger_request!r}"
            )
        if self.value_source != BABEL_SETTLEMENT_PUSH_VALUE_SOURCE:
            raise ValueError(
                "the S2C580 values may only come from "
                f"{BABEL_SETTLEMENT_PUSH_VALUE_SOURCE!r}, got "
                f"{self.value_source!r}"
            )
        if self.max_per_settlement != BABEL_SETTLEMENT_PUSH_MAX_PER_SETTLEMENT:
            raise ValueError(
                "one paired settlement pushes S2C580 at most "
                f"{BABEL_SETTLEMENT_PUSH_MAX_PER_SETTLEMENT} time(s), got "
                f"{self.max_per_settlement!r}"
            )
        if self.historical_ordering != BABEL_SETTLEMENT_PUSH_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{BABEL_SETTLEMENT_PUSH_HISTORICAL_ORDERING!r}"
            )
        for provenance in (self.value_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the post-settlement push is a compatibility choice and must "
                    "say so"
                )
            if provenance.compatibility_version != PROFILE_ID_V20:
                raise ValueError(
                    "BabelSettlementPush policy provenance must be v20"
                )
        if self.introduced_in != PROFILE_ID_V20:
            raise ValueError(
                "BabelSettlementPush policy identity must be v20, got "
                f"{self.introduced_in!r}"
            )

    @property
    def packet_count(self) -> int:
        """How many packets one paired settlement puts on the wire (one, max)."""

        return BABEL_SETTLEMENT_PUSH_MAX_PER_SETTLEMENT

    def push_fields(
        self, overlay_fields: tuple[object, ...] | list[object]
    ) -> tuple[object, ...]:
        """The 5 values of one S2C 580, validated before they reach the wire.

        The caller passes the session's validated overlay values; this method
        refuses anything that is not exactly five uint32 values, so no other
        state (a floor advance, a different realm) can slip into the push.
        """

        values = tuple(overlay_fields)
        if len(values) != BABEL_SETTLEMENT_PUSH_FIELD_COUNT:
            raise ValueError(
                f"S2C580 carries {BABEL_SETTLEMENT_PUSH_FIELD_COUNT} values, got "
                f"{len(values)}"
            )
        for name, value in zip(self.field_names, values, strict=True):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"S2C580 field {name!r}: {value!r} is not a uint32"
                )
            if not 0 <= value <= UINT32_MAX:
                raise ValueError(
                    f"S2C580 field {name!r}: {value!r} is outside uint32"
                )
        return values


@dataclass(frozen=True, slots=True)
class PrisonFightBossSnapshotPolicy:
    """V13-only S2C 1370 ``synPrisonFightBossToCg`` compatibility state.

    The justification of all 11 values -- the only writer of ``e_tbBossData``,
    the D-02 derived id, the closed-state convention and the one consumer on the
    PANEL_CLOSE path -- is written above the ``PRISON_FIGHT_*`` constants.  What
    this class enforces is that the snapshot cannot drift from the recovered
    registration and that no position can quietly upgrade its own provenance.
    """

    snapshot_format: str
    send_trigger: str
    fields: tuple[PrisonFightBossField, ...]
    historical_ordering: str
    historical_values_recovered: bool
    replaceable: bool
    snapshot_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.snapshot_format != PRISON_FIGHT_SNAPSHOT_FORMAT:
            raise ValueError(
                f"S2C1370 format must stay {PRISON_FIGHT_SNAPSHOT_FORMAT!r}, got "
                f"{self.snapshot_format!r}"
            )
        if self.send_trigger != PRISON_FIGHT_SNAPSHOT_SEND_TRIGGER:
            raise ValueError(
                "S2C1370 is a login snapshot and may only be sent by "
                f"{PRISON_FIGHT_SNAPSHOT_SEND_TRIGGER!r}, got {self.send_trigger!r}"
            )
        if len(self.fields) != PRISON_FIGHT_SNAPSHOT_FIELD_COUNT:
            raise ValueError(
                f"S2C1370 carries {PRISON_FIGHT_SNAPSHOT_FIELD_COUNT} values, got "
                f"{len(self.fields)}"
            )
        positions = [field.wire_position for field in self.fields]
        if positions != list(range(1, PRISON_FIGHT_SNAPSHOT_FIELD_COUNT + 1)):
            raise ValueError(
                "S2C1370 positions must cover 1.."
                f"{PRISON_FIGHT_SNAPSHOT_FIELD_COUNT} in order"
            )
        wire_format = "".join(field.wire_type for field in self.fields)
        if wire_format != self.snapshot_format:
            raise ValueError(
                f"S2C1370 wire types {wire_format!r} do not match the recovered "
                f"format {self.snapshot_format!r}"
            )
        names = [field.symbolic_name for field in self.fields]
        if len(set(names)) != len(names):
            raise ValueError(
                "S2C1370 symbolic names must be unique: the audit and every event "
                "record key their per-field provenance by name"
            )
        if self.historical_ordering != PRISON_FIGHT_SNAPSHOT_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{PRISON_FIGHT_SNAPSHOT_HISTORICAL_ORDERING!r}"
            )
        if self.historical_values_recovered is not False:
            raise ValueError(
                "no S2C1370 value has a recovered producer; "
                "historical_values_recovered must be False"
            )
        if self.replaceable is not True:
            raise ValueError("the S2C1370 snapshot must stay replaceable")
        for provenance in (self.snapshot_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the prison-fight snapshot is a compatibility choice and must "
                    "say so"
                )
            if provenance.compatibility_version != PROFILE_ID_V13:
                raise ValueError(
                    "PrisonFightBossSnapshot policy provenance must be v13"
                )
            if provenance.replaceable is not True:
                raise ValueError(
                    "the prison-fight snapshot provenance must stay replaceable"
                )
        for field in self.fields:
            if field.provenance.compatibility_version != PROFILE_ID_V13:
                raise ValueError(
                    f"S2C1370 position {field.wire_position}: policy provenance "
                    "must be v13"
                )
            if field.provenance.replaceable is not True:
                raise ValueError(
                    f"S2C1370 position {field.wire_position}: provenance must stay "
                    "replaceable"
                )

    @property
    def packet_count(self) -> int:
        """How many packets one completed login puts on the wire (one, always)."""

        return PRISON_FIGHT_SNAPSHOT_PACKET_COUNT

    def field_origins(self) -> dict[str, str]:
        """``symbolic name -> provenance origin``, for the wire audit and events."""

        return {field.symbolic_name: field.provenance.origin for field in self.fields}

    def field_names(self) -> tuple[str, ...]:
        return tuple(field.symbolic_name for field in self.fields)

    def snapshot_fields(self) -> tuple[object, ...]:
        """Assemble the 11 values of the one login snapshot.

        There is no request and no minted value: the policy declares the closed
        state and every value is the labelled constant the field table declares.
        The signature is deliberately argument-less, so a later slice cannot
        start deriving a value here without changing this contract on purpose.
        """

        return tuple(field.constant_value for field in self.fields)


@dataclass(frozen=True, slots=True)
class ChapterFightReplayPolicy:
    """V14-only: replay *observed* chapter-map fight settlements as item rows.

    A row is not a compatibility guess.  It is the client's own
    ``initChapterItemInfo(dChapterId, dType, dId)`` triple, derived from one
    battle transaction this server observed on ONE connection:

    * ``C2S 69 netEnterPveBattle`` -- the client's own chapter-map battle start
      (``pkgBattleMgr.c2sStartPveBattle(PVETYPE_GUANKA_CHAPTER_MAP, fightId,
      dIndex, 0, dChapterId, 0)``): ``p1`` fight id, ``p2`` map index, ``p4``
      chapter id, and ``pveOrPvpType == 86``;
    * ``C2S 67 battlePveGameOver`` -- that battle's own settlement; and
    * this server's single ``S2C 96 battlePveGameOverEnd`` answer (the
      lock-send unlock).

    Only then may a row be stored, and only the three values the client itself
    supplied may be replayed: ``dChapterId`` and ``dIndex`` from that request and
    ``MAP_TYPE_FIGHT = 10`` from the shipped constant module.  The row asserts
    exactly what the client's own reader reads -- ``isItemFightOver`` finds
    ``le_tbChapterItemInfo[dChapterId][10][dIndex]`` -- so it marks that one
    fight node completed and nothing else (no progress percentage, no reward,
    no drop, no completion time).

    The win/loss verdict is deliberately NOT decoded: the settlement's
    encrypted result carries it behind the client's own ``native.randvc`` base
    (``pkgSmartGameOverResult.encryptResult`` adds the verdict to a seeded
    random number), so this policy stores only what the wire names in the clear.
    That limitation is recorded in the TASK-0174 report, not hidden behind a
    claimed "victory".

    Historical login ordering is unknown, so the rows ride between the client's
    own ``initChapterItemStart`` and ``initChapterItemEnd`` -- the only two
    packets whose code forces that position (390 indexes the global 389 creates).
    """

    row_format: str
    container_policy: str
    historical_ordering: str
    historical_values_recovered: bool
    replaceable: bool
    row_provenance: Provenance
    value_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.row_format != CHAPTER_FIGHT_REPLAY_ROW_FORMAT:
            raise ValueError(
                f"unexpected chapter-fight row format: {self.row_format!r}"
            )
        if self.container_policy != CHAPTER_FIGHT_REPLAY_CONTAINER_POLICY:
            raise ValueError(
                "unexpected chapter-fight container policy: "
                f"{self.container_policy!r}"
            )
        if self.historical_ordering != CHAPTER_FIGHT_REPLAY_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{CHAPTER_FIGHT_REPLAY_HISTORICAL_ORDERING!r}"
            )
        if self.historical_values_recovered is not False:
            raise ValueError(
                "the settlement's win/loss and every other value are not decoded; "
                "historical_values_recovered must be False"
            )
        if self.replaceable is not True:
            raise ValueError("the chapter-fight replay must stay replaceable")
        if self.row_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "the row format is read from the shipped client and must say "
                "deterministic_recovered"
            )
        if self.value_provenance.origin != "request_echo":
            raise ValueError(
                "chapter / map index are the client's own request values and must "
                "say request_echo"
            )
        if self.ordering_provenance.origin != "synthetic_compatibility":
            raise ValueError(
                "the login position is an explicit compatibility ordering"
            )
        for provenance in (
            self.row_provenance,
            self.value_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V14:
                raise ValueError(
                    "ChapterFightReplay policy provenance must be v14"
                )
            if provenance.replaceable is not True:
                raise ValueError(
                    "the chapter-fight replay provenance must stay replaceable"
                )

    def row_fields(self, *, chapter_id: int, map_index: int) -> tuple[int, int, int]:
        """The one row this policy may emit: ``(dChapterId, dType, dId)``.

        The types are validated rather than coerced silently: the three wire
        fields are ``p`` numbers, and a value the client could not have sent is
        refused instead of being rounded into one.
        """

        for name, value in (("chapter_id", chapter_id), ("map_index", map_index)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"chapter-fight {name} must be an int, got {value!r}")
        if chapter_id <= 0:
            raise ValueError(f"unusable chapter id {chapter_id!r}")
        if map_index <= 0:
            raise ValueError(f"unusable chapter map index {map_index!r}")
        return (chapter_id, CHAPTER_FIGHT_REPLAY_MAP_TYPE, map_index)

    def packets(
        self, rows: tuple[tuple[int, int], ...]
    ) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """``(name, fields)`` for the item block, rows between the client pair.

        ``rows`` is ``(chapter_id, map_index)`` per observed settlement, in the
        caller's order.  With no rows the result is exactly the v7 pair, so a
        v14 server with an archive that has never settled a chapter fight emits
        the same bytes as v13.
        """

        packets: list[tuple[str, tuple[object, ...]]] = [
            ("initChapterItemStart", ())
        ]
        for chapter_id, map_index in rows:
            packets.append(
                (
                    "initChapterItemInfo",
                    self.row_fields(chapter_id=chapter_id, map_index=map_index),
                )
            )
        packets.append(("initChapterItemEnd", ()))
        return tuple(packets)

    @property
    def pair_packet_count(self) -> int:
        """The two packets always sent (the client's own reset pair)."""

        return 2


@dataclass(frozen=True, slots=True)
class ChapterBoxReplayPolicy:
    """V18-only: replay the chapter milestone chests this role already opened.

    The row is the client's own ``initChapterBoxInfo(dChapterId, dBoxIndex)``
    pair, and it is emitted only for a ``(chapter, box)`` row the archive stored
    when it answered a real ``C2S 222``.  No chest the role has not opened is
    invented, and no reward is re-sent: the login block restores the *accepted*
    flag, while the dog food itself is restored by the existing
    ``returnSpecialCardStart/Info/End`` (S2C 504/506/505) snapshot.

    The packet order is forced by the client's own handler: ``InitInfo`` indexes
    the table only ``InitStart`` creates, and ``End`` is the block's empty end
    handler.  With an empty archive the policy sends no packet at all -- an
    empty start/end pair would still reset ``le_tbChapterBox``, which is a
    *different* login stream from v17's.
    """

    row_format: str
    container_policy: str
    historical_ordering: str
    row_provenance: Provenance
    value_provenance: Provenance
    ordering_provenance: Provenance

    def __post_init__(self) -> None:
        if self.row_format != CHAPTER_BOX_REPLAY_ROW_FORMAT:
            raise ValueError(
                f"unexpected chapter-box row format: {self.row_format!r}"
            )
        if self.container_policy != CHAPTER_BOX_REPLAY_CONTAINER_POLICY:
            raise ValueError(
                "unexpected chapter-box container policy: "
                f"{self.container_policy!r}"
            )
        if self.historical_ordering != CHAPTER_BOX_REPLAY_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{CHAPTER_BOX_REPLAY_HISTORICAL_ORDERING!r}"
            )
        for provenance in (
            self.row_provenance,
            self.value_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V18:
                raise ValueError(
                    "ChapterBoxReplay policy provenance must be v18"
                )
            if provenance.replaceable is not True:
                raise ValueError(
                    "the chapter-box replay provenance must stay replaceable"
                )
        if self.row_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "the row format is read from the shipped client and must say "
                "deterministic_recovered"
            )
        if self.value_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "the chapter/box values are this server's own stored accept "
                "rows and must say deterministic_recovered"
            )
        if self.ordering_provenance.origin != "synthetic_compatibility":
            raise ValueError(
                "the login position is an explicit compatibility ordering"
            )

    def row_fields(self, *, chapter_id: int, box_index: int) -> tuple[int, int]:
        """The one row this policy may emit: ``(dChapterId, dBoxIndex)``."""

        for name, value in (("chapter_id", chapter_id), ("box_index", box_index)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"chapter-box {name} must be an int, got {value!r}")
        if chapter_id <= 0:
            raise ValueError(f"unusable chapter id {chapter_id!r}")
        if box_index < 0:
            raise ValueError(f"unusable box index {box_index!r}")
        return (chapter_id, box_index)

    def packets(
        self, rows: tuple[tuple[int, int], ...]
    ) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """``(name, fields)`` for the box block, or **nothing** with no rows."""

        if not rows:
            return ()
        packets: list[tuple[str, tuple[object, ...]]] = [
            ("initChapterBoxStart", ())
        ]
        for chapter_id, box_index in rows:
            packets.append(
                (
                    "initChapterBoxInfo",
                    self.row_fields(chapter_id=chapter_id, box_index=box_index),
                )
            )
        packets.append(("initChapterBoxEnd", ()))
        return tuple(packets)

    @property
    def empty_packet_count(self) -> int:
        """How many packets an archive with no accepted chest puts on the wire."""

        return len(self.packets(()))


@dataclass(frozen=True, slots=True)
class MailLoginPolicy:
    """V21-only empty mail-list login block (S2C 255 + S2C 256).

    The two packets are NOT a reply to any client request: they are the login
    bracket the original server used to create the client's own
    `le_tbMailDict`/`le_tbTempItemDict`.  Without them `hasNewMail` runs
    `pairs(nil)` and the main city's red-point refresh throws (RUN-0076).  The
    policy carries no row, no value and no reward; the only thing it may put on
    the wire is the empty Start/End pair, in that order.

    The block structure (two empty containers bracketing zero rows, both
    registered with format "") is read from the shipped client and is
    `deterministic_recovered`.  *Where* that bracket sits in the login segment
    and the decision to replay it with zero rows are explicit compatibility
    choices (`synthetic_compatibility`); the original ordering is UNKNOWN.
    """

    start_wire_id: int
    end_wire_id: int
    start_name: str
    end_name: str
    wire_format: str
    row_count: int
    allowed_login_positions: tuple[str, ...]
    historical_ordering: str
    historical_values_recovered: bool
    replaceable: bool
    historical: bool
    block_provenance: Provenance
    value_provenance: Provenance
    ordering_provenance: Provenance
    introduced_in: str

    def __post_init__(self) -> None:
        if self.start_wire_id != MAIL_LOGIN_START_WIRE_ID:
            raise ValueError(
                f"the mail start must stay S2C {MAIL_LOGIN_START_WIRE_ID}, got "
                f"{self.start_wire_id!r}"
            )
        if self.end_wire_id != MAIL_LOGIN_END_WIRE_ID:
            raise ValueError(
                f"the mail end must stay S2C {MAIL_LOGIN_END_WIRE_ID}, got "
                f"{self.end_wire_id!r}"
            )
        if (self.start_name, self.end_name) != (
            MAIL_LOGIN_START_NAME,
            MAIL_LOGIN_END_NAME,
        ):
            raise ValueError(
                "the mail packet names must stay "
                f"{MAIL_LOGIN_START_NAME!r}/{MAIL_LOGIN_END_NAME!r}"
            )
        if self.wire_format != MAIL_LOGIN_WIRE_FORMAT:
            raise ValueError(
                f"the mail packets carry no values: {self.wire_format!r}"
            )
        if self.row_count != MAIL_LOGIN_ROW_COUNT:
            raise ValueError(
                f"the mail block must carry zero rows, got {self.row_count!r}"
            )
        if tuple(self.allowed_login_positions) != MAIL_LOGIN_ALLOWED_POSITIONS:
            raise ValueError(
                "the mail login position must stay "
                f"{MAIL_LOGIN_ALLOWED_POSITIONS!r}"
            )
        if self.historical_ordering != MAIL_LOGIN_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{MAIL_LOGIN_HISTORICAL_ORDERING!r}"
            )
        if self.historical_values_recovered is not (
            MAIL_LOGIN_HISTORICAL_VALUES_RECOVERED
        ):
            raise ValueError(
                "no mail value or row is historical; "
                "historical_values_recovered must be False"
            )
        if self.replaceable is not True:
            raise ValueError("the mail login block must stay replaceable")
        if self.historical is not False:
            raise ValueError("the mail login block is not a historical record")
        if self.block_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "the empty Start/End bracket is the shipped client's own "
                "container protocol and must say deterministic_recovered"
            )
        for provenance in (self.value_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the zero-row replay and its login position are explicit "
                    "compatibility choices and must say synthetic_compatibility"
                )
        for provenance in (
            self.block_provenance,
            self.value_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V21:
                raise ValueError("MailLogin policy provenance must be v21")
            if provenance.replaceable is not True:
                raise ValueError("the mail login provenance must stay replaceable")
        if self.introduced_in != PROFILE_ID_V21:
            raise ValueError(
                f"MailLogin policy identity must be v21, got {self.introduced_in!r}"
            )

    @property
    def login_position(self) -> str:
        """The one position this policy allows (the S2C 10..S2C 9 bracket)."""

        return self.allowed_login_positions[0]

    @property
    def packet_count(self) -> int:
        """Exactly two packets: Start then End, zero rows between them."""

        return 2

    def packets(self) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """``(name, fields)`` for the only block this policy may emit."""

        return ((self.start_name, ()), (self.end_name, ()))


@dataclass(frozen=True, slots=True)
class TeamMaxFightPolicy:
    """V22-only login battle-power value (S2C 39 ``returnTeamMaxFight``).

    The packet itself, its format and the client side it feeds are recovered:

    * `pkgScProtocolMgr.lua` line 295 registers ``"N"`` ``returnTeamMaxFight``
      (D-02 runtime id 39, no lock partner);
    * `pkgTeamNcMgr` PROTO 5 forwards the single value to
      `pkgTeamDataMgr.setMaxFight`, which writes ``le_dMaxFight`` and dispatches
      ``UPDATE_TEAM_FIGHT``;
    * the only gate that reads it is `pkgChapterEventHandle.handleFight`'s
      ``fightLimitFight`` check, whose shipped text is 战力需要达到{1}.

    The NUMBER is the client's own `caclGroupFight` for the preset the client is
    currently showing.  The formula (`caclGroupFight` -> `caclRoleFight` +
    `getCardFight` + fabao + shenfu), the shipped `card.fight`/level/advance
    ratios and soul 101's fight are `deterministic_recovered`/
    `official_recovered`; the five terms this archive has no producer for are
    proved zero by `max_decoder/analyze_team_max_fight.py`.  The original
    server's exact value semantics (current preset vs. best preset) and its send
    ordering are UNKNOWN, so the value and its triggers are a labelled
    compatibility choice.
    """

    wire_id: int
    name: str
    wire_format: str
    allowed_login_positions: tuple[str, ...]
    triggers: tuple[str, ...]
    value_policy: str
    historical_ordering: str
    historical_values_recovered: bool
    replaceable: bool
    historical: bool
    block_provenance: Provenance
    value_provenance: Provenance
    ordering_provenance: Provenance
    introduced_in: str

    def __post_init__(self) -> None:
        if self.wire_id != TEAM_MAX_FIGHT_WIRE_ID:
            raise ValueError(
                f"the team max fight must stay S2C {TEAM_MAX_FIGHT_WIRE_ID}, "
                f"got {self.wire_id!r}"
            )
        if self.name != TEAM_MAX_FIGHT_NAME:
            raise ValueError(
                f"the team max fight packet must stay {TEAM_MAX_FIGHT_NAME!r}"
            )
        if self.wire_format != TEAM_MAX_FIGHT_WIRE_FORMAT:
            raise ValueError(
                f"the team max fight carries one N value: {self.wire_format!r}"
            )
        if tuple(self.allowed_login_positions) != TEAM_MAX_FIGHT_ALLOWED_POSITIONS:
            raise ValueError(
                "the team max fight login position must stay "
                f"{TEAM_MAX_FIGHT_ALLOWED_POSITIONS!r}"
            )
        if tuple(self.triggers) != TEAM_MAX_FIGHT_TRIGGERS:
            raise ValueError(
                f"the team max fight triggers must stay {TEAM_MAX_FIGHT_TRIGGERS!r}"
            )
        if self.value_policy != TEAM_MAX_FIGHT_VALUE_POLICY:
            raise ValueError(
                "the team max fight value must stay "
                f"{TEAM_MAX_FIGHT_VALUE_POLICY!r}"
            )
        if self.historical_ordering != TEAM_MAX_FIGHT_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{TEAM_MAX_FIGHT_HISTORICAL_ORDERING!r}"
            )
        if self.historical_values_recovered is not (
            TEAM_MAX_FIGHT_HISTORICAL_VALUES_RECOVERED
        ):
            raise ValueError(
                "the historical value is not recovered; "
                "historical_values_recovered must be False"
            )
        if self.replaceable is not True:
            raise ValueError("the team max fight value must stay replaceable")
        if self.historical is not False:
            raise ValueError("the team max fight value is not a historical record")
        if self.block_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "S2C 39's registration/handler/gate are shipped client code and "
                "must say deterministic_recovered"
            )
        for provenance in (self.value_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the computed value and its send position are compatibility "
                    "choices and must say synthetic_compatibility"
                )
        for provenance in (
            self.block_provenance,
            self.value_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V22:
                raise ValueError("TeamMaxFight policy provenance must be v22")
            if provenance.replaceable is not True:
                raise ValueError("the team max fight provenance must stay replaceable")
        if self.introduced_in != PROFILE_ID_V22:
            raise ValueError(
                f"TeamMaxFight policy identity must be v22, got {self.introduced_in!r}"
            )

    @property
    def login_position(self) -> str:
        """The one login position this policy allows (right after S2C 35)."""

        return self.allowed_login_positions[0]

    def fields(self, team_power: int) -> tuple[int]:
        """The single ``N`` value for one send."""

        value = int(team_power)
        if value < 0 or value > 0xFFFFFFFF:
            raise ValueError(f"team max fight out of the N range: {value}")
        return (value,)


@dataclass(frozen=True, slots=True)
class MonthSignStatePolicy:
    """V23-only synthetic month-sign state (S2C 1499 ``returnMonthSignData``).

    The packet, its format, its only sender and the poll it stops are recovered:

    * `pkgScProtocolMgr.lua:2032` registers ``"NppaNNN"``
      ``returnMonthSignData`` (D-02 runtime id 1499, no lock partner);
    * `pkgMonthSignModel.setUpMonthSignUpdateTimer` (lines 130-140) installs a
      REPEATING 1000 ms `pkgTimerMgr.add` that sends C2S 725 while
      ``le_nextRefreshTime < pkgServerTimeMgr.getCurrentServerTime()``;
    * `le_nextRefreshTime`'s ONLY writer is `setMonthSignData`, i.e. this packet's
      handler, so answering C2S 725 with the empty S2C 1500 alone cannot stop
      the poll (RUN-0077: six requests, ~1.03 s apart).

    The archive has no producer of sign rows, extension counts or rewards, so the
    only honest state is the client's own empty container.  `udExtSign` 0 and the
    empty mapping text make `stringToTable` build ``{udExtSign = 0, tbSignMap =
    {}}``; `nextRefreshTime` is the next CN (UTC+8) natural-day boundary of the
    same server clock S2C 0 sends, which is strictly in the future at every send
    and therefore stops the per-second poll.  Row values, the send position and
    the ordering are labelled compatibility choices: no historical sign state is
    claimed or invented (D-21).
    """

    wire_id: int
    name: str
    wire_format: str
    allowed_positions: tuple[str, ...]
    triggers: tuple[str, ...]
    value_policy: str
    historical_ordering: str
    historical_values_recovered: bool
    replaceable: bool
    historical: bool
    block_provenance: Provenance
    value_provenance: Provenance
    ordering_provenance: Provenance
    introduced_in: str

    def __post_init__(self) -> None:
        if self.wire_id != MONTH_SIGN_STATE_WIRE_ID:
            raise ValueError(
                f"the month-sign state must stay S2C {MONTH_SIGN_STATE_WIRE_ID}, "
                f"got {self.wire_id!r}"
            )
        if self.name != MONTH_SIGN_STATE_NAME:
            raise ValueError(
                f"the month-sign state packet must stay {MONTH_SIGN_STATE_NAME!r}"
            )
        if self.wire_format != MONTH_SIGN_STATE_WIRE_FORMAT:
            raise ValueError(
                "the month-sign state must stay "
                f"{MONTH_SIGN_STATE_WIRE_FORMAT!r}, got {self.wire_format!r}"
            )
        if tuple(self.allowed_positions) != MONTH_SIGN_STATE_ALLOWED_POSITIONS:
            raise ValueError(
                "the month-sign state position must stay "
                f"{MONTH_SIGN_STATE_ALLOWED_POSITIONS!r}"
            )
        if tuple(self.triggers) != MONTH_SIGN_STATE_TRIGGERS:
            raise ValueError(
                f"the month-sign state trigger must stay "
                f"{MONTH_SIGN_STATE_TRIGGERS!r}"
            )
        if self.value_policy != MONTH_SIGN_STATE_VALUE_POLICY:
            raise ValueError(
                "the month-sign state value must stay "
                f"{MONTH_SIGN_STATE_VALUE_POLICY!r}"
            )
        if self.historical_ordering != MONTH_SIGN_STATE_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{MONTH_SIGN_STATE_HISTORICAL_ORDERING!r}"
            )
        if self.historical_values_recovered is not (
            MONTH_SIGN_STATE_HISTORICAL_VALUES_RECOVERED
        ):
            raise ValueError(
                "no sign row, extension count or reward is historical; "
                "historical_values_recovered must be False"
            )
        if self.replaceable is not True:
            raise ValueError("the month-sign state must stay replaceable")
        if self.historical is not False:
            raise ValueError("the month-sign state is not a historical record")
        if self.block_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "S2C 1499's registration/format/handler and the poll it stops are "
                "shipped client code and must say deterministic_recovered"
            )
        for provenance in (self.value_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the empty sign history, its refresh boundary and its send "
                    "position are explicit compatibility choices and must say "
                    "synthetic_compatibility"
                )
        for provenance in (
            self.block_provenance,
            self.value_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V23:
                raise ValueError("MonthSignState policy provenance must be v23")
            if provenance.replaceable is not True:
                raise ValueError("the month-sign state provenance must stay replaceable")
        if self.introduced_in != PROFILE_ID_V23:
            raise ValueError(
                "MonthSignState policy identity must be v23, got "
                f"{self.introduced_in!r}"
            )

    @property
    def position(self) -> str:
        """The one position this policy allows (before the empty S2C 1500)."""

        return self.allowed_positions[0]

    @staticmethod
    def next_refresh_time(now_unix: int) -> int:
        """The next CN (UTC+8) natural-day boundary, strictly after ``now``."""

        now = int(now_unix)
        offset = MONTH_SIGN_STATE_CN_UTC_OFFSET_SECONDS
        local = now + offset
        boundary = local - (local % 86_400) + 86_400 - offset
        if boundary <= now:
            boundary += 86_400
        return boundary

    def fields(
        self, *, now_unix: int
    ) -> tuple[int, float, float, str, int, int, int]:
        """The seven ``NppaNNN`` values for one send on the given server time."""

        now = int(now_unix)
        if now < 0 or now > 0xFFFFFFFF:
            raise ValueError(f"month-sign state time outside uint32: {now}")
        next_refresh = self.next_refresh_time(now)
        if not 0 <= next_refresh <= 0xFFFFFFFF:
            raise ValueError(
                f"month-sign nextRefreshTime outside uint32: {next_refresh}"
            )
        cn_calendar = datetime.fromtimestamp(
            now + MONTH_SIGN_STATE_CN_UTC_OFFSET_SECONDS, timezone.utc
        )
        return (
            MONTH_SIGN_STATE_UD_EXT_SIGN,
            float(MONTH_SIGN_STATE_LAST_EXT_SIGN_TIME),
            float(next_refresh),
            MONTH_SIGN_STATE_EMPTY_SIGN_MAP_TEXT,
            cn_calendar.year,
            cn_calendar.month,
            cn_calendar.day,
        )


@dataclass(frozen=True, slots=True)
class BitRabbitStatePolicy:
    """V24-only synthetic inactive bitRabbit state (S2C 1435 ``syncBitRabbit``).

    The packet, its format, its handler and the crash it prevents are shipped
    client code:

    * `pkgScProtocolMgr.lua:1950` registers ``"NNNNNN"`` ``syncBitRabbit``
      (D-02 runtime id 1435, no lock partner, no C2S request);
    * `pkgBitRabbitDataMgr.s2c.syncBitRabbit` (source lines 8-17) writes
      ``le_tbBitRabbitData.dFightCountLeft`` and the other five fields;
    * `checkAddRabbit` (source lines 100-114) starts with
      ``getFightCountLeft() <= 0`` -- an unguarded comparison against the nil the
      container holds until a server push arrives -- and is called from
      `pkgMainUIMgr.addRabbitOnFriend` (source line 167) inside `checkRedPoint`
      (source line 966), whose next two calls (`checkChapterRedPoint`,
      `updateFriendUnreadNum`) never run after the throw (RUN-0077).

    The archive has no producer of rabbit rows, square entries, fights, ranks or
    rewards, so the only honest state is the client's own inactive one: all six
    zeros.  `checkAddRabbit` then returns false, `getMyOwnRabbit` returns nil and
    nothing is granted.  The zero values and the login position are labelled
    compatibility choices; the original ordering is UNKNOWN (D-21).
    """

    wire_id: int
    name: str
    wire_format: str
    allowed_positions: tuple[str, ...]
    triggers: tuple[str, ...]
    value_policy: str
    historical_ordering: str
    historical_values_recovered: bool
    replaceable: bool
    historical: bool
    block_provenance: Provenance
    value_provenance: Provenance
    ordering_provenance: Provenance
    introduced_in: str

    def __post_init__(self) -> None:
        if self.wire_id != BIT_RABBIT_STATE_WIRE_ID:
            raise ValueError(
                f"the bitRabbit state must stay S2C {BIT_RABBIT_STATE_WIRE_ID}, "
                f"got {self.wire_id!r}"
            )
        if self.name != BIT_RABBIT_STATE_NAME:
            raise ValueError(
                f"the bitRabbit state packet must stay {BIT_RABBIT_STATE_NAME!r}"
            )
        if self.wire_format != BIT_RABBIT_STATE_WIRE_FORMAT:
            raise ValueError(
                "the bitRabbit state must stay "
                f"{BIT_RABBIT_STATE_WIRE_FORMAT!r}, got {self.wire_format!r}"
            )
        if tuple(self.allowed_positions) != BIT_RABBIT_STATE_ALLOWED_POSITIONS:
            raise ValueError(
                "the bitRabbit state position must stay "
                f"{BIT_RABBIT_STATE_ALLOWED_POSITIONS!r}"
            )
        if tuple(self.triggers) != BIT_RABBIT_STATE_TRIGGERS:
            raise ValueError(
                f"the bitRabbit state trigger must stay "
                f"{BIT_RABBIT_STATE_TRIGGERS!r}"
            )
        if self.value_policy != BIT_RABBIT_STATE_VALUE_POLICY:
            raise ValueError(
                "the bitRabbit state value must stay "
                f"{BIT_RABBIT_STATE_VALUE_POLICY!r}"
            )
        if self.historical_ordering != BIT_RABBIT_STATE_HISTORICAL_ORDERING:
            raise ValueError(
                "historical ordering must stay "
                f"{BIT_RABBIT_STATE_HISTORICAL_ORDERING!r}"
            )
        if self.historical_values_recovered is not (
            BIT_RABBIT_STATE_HISTORICAL_VALUES_RECOVERED
        ):
            raise ValueError(
                "no rabbit row, fight, rank or reward is historical; "
                "historical_values_recovered must be False"
            )
        if self.replaceable is not True:
            raise ValueError("the bitRabbit state must stay replaceable")
        if self.historical is not False:
            raise ValueError("the bitRabbit state is not a historical record")
        if self.block_provenance.origin != "deterministic_recovered":
            raise ValueError(
                "S2C 1435's registration/format/handler and the nil comparison "
                "are shipped client code and must say deterministic_recovered"
            )
        for provenance in (self.value_provenance, self.ordering_provenance):
            if provenance.origin != "synthetic_compatibility":
                raise ValueError(
                    "the all-zero inactive state and its send position are "
                    "explicit compatibility choices and must say "
                    "synthetic_compatibility"
                )
        for provenance in (
            self.block_provenance,
            self.value_provenance,
            self.ordering_provenance,
        ):
            if provenance.compatibility_version != PROFILE_ID_V24:
                raise ValueError("BitRabbitState policy provenance must be v24")
            if provenance.replaceable is not True:
                raise ValueError("the bitRabbit state provenance must stay replaceable")
        if self.introduced_in != PROFILE_ID_V24:
            raise ValueError(
                "BitRabbitState policy identity must be v24, got "
                f"{self.introduced_in!r}"
            )

    @property
    def position(self) -> str:
        """The one position this policy allows (after S2C 555)."""

        return self.allowed_positions[0]

    def fields(self) -> tuple[int, int, int, int, int, int]:
        """The six ``N`` values of the client's own inactive state."""

        return (
            BIT_RABBIT_STATE_FIGHT_COUNT_LEFT,
            BIT_RABBIT_STATE_LAST_ADD_COUNT_TIME,
            BIT_RABBIT_STATE_RABBIT_TIME,
            BIT_RABBIT_STATE_RABBIT_LEFT_COUNT,
            BIT_RABBIT_STATE_RABBIT_OWN_BIT,
            BIT_RABBIT_STATE_RABBIT_BOSS_CFG_ID,
        )


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    metadata: ProfileMetadata
    role_values: tuple[RoleValueField, ...]
    sign_catalogs: SignCatalogs
    sign_state_template: SignStateTemplate
    scene_state_template: SceneStateTemplate
    active_soul_rules: tuple[ActiveSoulRule, ...]
    parent_profile_id: str | None = None
    server_time: ServerTimePolicy | None = None
    mystery_shop: MysteryShopPolicy | None = None
    story_start: StoryStartPolicy | None = None
    team_snapshot: TeamSnapshotPolicy | None = None
    recharge_activity: RechargeActivityPolicy | None = None
    chapter_item_init: ChapterItemInitPolicy | None = None
    experience_task_init: ExperienceTaskInitPolicy | None = None
    chapter_task_init: ChapterTaskInitPolicy | None = None
    chapter_machine_init: ChapterMachineInitPolicy | None = None
    battle_start_reply: BattleStartReplyPolicy | None = None
    battle_game_over_reply: BattleGameOverReplyPolicy | None = None
    prison_fight: PrisonFightBossSnapshotPolicy | None = None
    chapter_fight_replay: ChapterFightReplayPolicy | None = None
    drawcard_unlock: DrawCardUnlockPolicy | None = None
    chapter_box_replay: ChapterBoxReplayPolicy | None = None
    babel_settlement_push: BabelSettlementPushPolicy | None = None
    mail_login: MailLoginPolicy | None = None
    team_max_fight: TeamMaxFightPolicy | None = None
    month_sign_state: MonthSignStatePolicy | None = None
    bit_rabbit_state: BitRabbitStatePolicy | None = None

    def __post_init__(self) -> None:
        if self.metadata.profile_id not in PROFILE_LINEAGE:
            raise ValueError(f"unexpected profile id: {self.metadata.profile_id!r}")
        expected_parent = PROFILE_LINEAGE[self.metadata.profile_id]
        if self.parent_profile_id != expected_parent:
            raise ValueError(
                f"profile {self.metadata.profile_id!r} must declare parent "
                f"{expected_parent!r}, got {self.parent_profile_id!r}"
            )
        # Domain-introduced-version expectations (the only real chain
        # v1->v2->v3->v4->v5->v6->v7):
        #   core domains (RoleValue/Sign/Scene/ActiveSoul) -> v1
        #   ServerTime policy -> v2  (validated inside ServerTimePolicy)
        #   MysteryShop policy -> v3 (validated inside MysteryShopPolicy)
        #   StoryStart policy -> v4  (validated inside StoryStartPolicy)
        #   TeamSnapshot policy -> v5 (validated inside TeamSnapshotPolicy)
        #   RechargeActivity policy -> v6 (validated inside RechargeActivityPolicy)
        #   ChapterItemInit policy -> v7 (validated inside ChapterItemInitPolicy)
        #   ExperienceTaskInit policy -> v8 (validated inside its own policy)
        #   ChapterTaskInit policy -> v9 (validated inside its own policy)
        #   ChapterMachineInit policy -> v10 (validated inside its own policy)
        #   BattleStartReply policy -> v11 (validated inside its own policy);
        #     v17 replaces its position 7 value contract only (counter instead
        #     of request echo), enforced above.
        #   BattleGameOverReply policy -> v12 (validated inside its own policy)
        profile_id = self.metadata.profile_id
        if profile_id == PROFILE_ID_V1:
            if self.server_time is not None:
                raise ValueError("v1 profile must not carry a ServerTime policy")
            if self.mystery_shop is not None:
                raise ValueError("v1 profile must not carry a MysteryShop policy")
            if self.story_start is not None:
                raise ValueError("v1 profile must not carry a StoryStart policy")
            if self.team_snapshot is not None:
                raise ValueError("v1 profile must not carry a TeamSnapshot policy")
            if self.recharge_activity is not None:
                raise ValueError("v1 profile must not carry a RechargeActivity policy")
            if self.chapter_item_init is not None:
                raise ValueError("v1 profile must not carry a ChapterItemInit policy")
            if self.chapter_task_init is not None:
                raise ValueError(
                    "v1 profile must not carry a ChapterTaskInit policy"
                )
        elif profile_id == PROFILE_ID_V2:
            if self.server_time is None:
                raise ValueError("v2 profile requires the ServerTime policy")
            if self.mystery_shop is not None:
                raise ValueError("v2 profile must not carry a MysteryShop policy")
            if self.story_start is not None:
                raise ValueError("v2 profile must not carry a StoryStart policy")
            if self.team_snapshot is not None:
                raise ValueError("v2 profile must not carry a TeamSnapshot policy")
            if self.recharge_activity is not None:
                raise ValueError("v2 profile must not carry a RechargeActivity policy")
            if self.chapter_item_init is not None:
                raise ValueError("v2 profile must not carry a ChapterItemInit policy")
            if self.chapter_task_init is not None:
                raise ValueError(
                    "v2 profile must not carry a ChapterTaskInit policy"
                )
        elif profile_id == PROFILE_ID_V3:
            if self.server_time is None:
                raise ValueError("v3 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v3 profile requires the MysteryShop policy")
            if self.story_start is not None:
                raise ValueError("v3 profile must not carry a StoryStart policy")
            if self.team_snapshot is not None:
                raise ValueError("v3 profile must not carry a TeamSnapshot policy")
            if self.recharge_activity is not None:
                raise ValueError("v3 profile must not carry a RechargeActivity policy")
            if self.chapter_item_init is not None:
                raise ValueError("v3 profile must not carry a ChapterItemInit policy")
            if self.chapter_task_init is not None:
                raise ValueError(
                    "v3 profile must not carry a ChapterTaskInit policy"
                )
        elif profile_id == PROFILE_ID_V4:
            if self.server_time is None:
                raise ValueError("v4 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v4 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v4 profile requires the StoryStart policy")
            if self.team_snapshot is not None:
                raise ValueError("v4 profile must not carry a TeamSnapshot policy")
            if self.recharge_activity is not None:
                raise ValueError("v4 profile must not carry a RechargeActivity policy")
            if self.chapter_item_init is not None:
                raise ValueError("v4 profile must not carry a ChapterItemInit policy")
            if self.chapter_task_init is not None:
                raise ValueError(
                    "v4 profile must not carry a ChapterTaskInit policy"
                )
        elif profile_id == PROFILE_ID_V5:
            if self.server_time is None:
                raise ValueError("v5 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v5 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v5 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v5 profile requires the TeamSnapshot policy")
            if self.recharge_activity is not None:
                raise ValueError("v5 profile must not carry a RechargeActivity policy")
            if self.chapter_item_init is not None:
                raise ValueError("v5 profile must not carry a ChapterItemInit policy")
            if self.chapter_task_init is not None:
                raise ValueError(
                    "v5 profile must not carry a ChapterTaskInit policy"
                )
        elif profile_id == PROFILE_ID_V6:
            if self.server_time is None:
                raise ValueError("v6 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v6 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v6 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v6 profile requires the TeamSnapshot policy")
            if self.recharge_activity is None:
                raise ValueError("v6 profile requires the RechargeActivity policy")
            if self.chapter_item_init is not None:
                raise ValueError("v6 profile must not carry a ChapterItemInit policy")
            if self.chapter_task_init is not None:
                raise ValueError(
                    "v6 profile must not carry a ChapterTaskInit policy"
                )
        elif profile_id == PROFILE_ID_V7:
            if self.server_time is None:
                raise ValueError("v7 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v7 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v7 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v7 profile requires the TeamSnapshot policy")
            if self.recharge_activity is None:
                raise ValueError("v7 profile requires the RechargeActivity policy")
            if self.chapter_item_init is None:
                raise ValueError("v7 profile requires the ChapterItemInit policy")
        elif profile_id == PROFILE_ID_V8:
            if self.server_time is None:
                raise ValueError("v8 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v8 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v8 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v8 profile requires the TeamSnapshot policy")
            if self.recharge_activity is None:
                raise ValueError("v8 profile requires the RechargeActivity policy")
            if self.chapter_item_init is None:
                raise ValueError("v8 profile requires the ChapterItemInit policy")
            if self.experience_task_init is None:
                raise ValueError("v8 profile requires the ExperienceTaskInit policy")
        elif profile_id == PROFILE_ID_V9:
            if self.server_time is None:
                raise ValueError("v9 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v9 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v9 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v9 profile requires the TeamSnapshot policy")
            if self.recharge_activity is None:
                raise ValueError("v9 profile requires the RechargeActivity policy")
            if self.chapter_item_init is None:
                raise ValueError("v9 profile requires the ChapterItemInit policy")
            if self.experience_task_init is None:
                raise ValueError("v9 profile requires the ExperienceTaskInit policy")
            if self.chapter_task_init is None:
                raise ValueError("v9 profile requires the ChapterTaskInit policy")
        elif profile_id == PROFILE_ID_V10:
            if self.server_time is None:
                raise ValueError("v10 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v10 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v10 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v10 profile requires the TeamSnapshot policy")
            if self.recharge_activity is None:
                raise ValueError("v10 profile requires the RechargeActivity policy")
            if self.chapter_item_init is None:
                raise ValueError("v10 profile requires the ChapterItemInit policy")
            if self.experience_task_init is None:
                raise ValueError("v10 profile requires the ExperienceTaskInit policy")
            if self.chapter_task_init is None:
                raise ValueError("v10 profile requires the ChapterTaskInit policy")
            if self.chapter_machine_init is None:
                raise ValueError("v10 profile requires the ChapterMachineInit policy")
            if self.battle_start_reply is not None:
                raise ValueError("v10 profile must not carry a BattleStartReply policy")
            if self.battle_game_over_reply is not None:
                raise ValueError(
                    "v10 profile must not carry a BattleGameOverReply policy"
                )
        elif profile_id == PROFILE_ID_V11:
            if self.server_time is None:
                raise ValueError("v11 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v11 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v11 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v11 profile requires the TeamSnapshot policy")
            if self.recharge_activity is None:
                raise ValueError("v11 profile requires the RechargeActivity policy")
            if self.chapter_item_init is None:
                raise ValueError("v11 profile requires the ChapterItemInit policy")
            if self.experience_task_init is None:
                raise ValueError("v11 profile requires the ExperienceTaskInit policy")
            if self.chapter_task_init is None:
                raise ValueError("v11 profile requires the ChapterTaskInit policy")
            if self.chapter_machine_init is None:
                raise ValueError("v11 profile requires the ChapterMachineInit policy")
            if self.battle_start_reply is None:
                raise ValueError("v11 profile requires the BattleStartReply policy")
            if self.battle_game_over_reply is not None:
                raise ValueError(
                    "v11 profile must not carry a BattleGameOverReply policy"
                )
        elif profile_id == PROFILE_ID_V12:
            if self.server_time is None:
                raise ValueError("v12 profile requires the ServerTime policy")
            if self.mystery_shop is None:
                raise ValueError("v12 profile requires the MysteryShop policy")
            if self.story_start is None:
                raise ValueError("v12 profile requires the StoryStart policy")
            if self.team_snapshot is None:
                raise ValueError("v12 profile requires the TeamSnapshot policy")
            if self.recharge_activity is None:
                raise ValueError("v12 profile requires the RechargeActivity policy")
            if self.chapter_item_init is None:
                raise ValueError("v12 profile requires the ChapterItemInit policy")
            if self.experience_task_init is None:
                raise ValueError("v12 profile requires the ExperienceTaskInit policy")
            if self.chapter_task_init is None:
                raise ValueError("v12 profile requires the ChapterTaskInit policy")
            if self.chapter_machine_init is None:
                raise ValueError("v12 profile requires the ChapterMachineInit policy")
            if self.battle_start_reply is None:
                raise ValueError("v12 profile requires the BattleStartReply policy")
            if self.battle_game_over_reply is None:
                raise ValueError(
                    "v12 profile requires the BattleGameOverReply policy"
                )
        else:
            # v13 and every later profile inherit the v13 domain set; the later
            # profiles add their own required policy below.
            if self.server_time is None:
                raise ValueError(
                    f"{profile_id} profile requires the ServerTime policy"
                )
            if self.mystery_shop is None:
                raise ValueError(
                    f"{profile_id} profile requires the MysteryShop policy"
                )
            if self.story_start is None:
                raise ValueError(
                    f"{profile_id} profile requires the StoryStart policy"
                )
            if self.team_snapshot is None:
                raise ValueError(
                    f"{profile_id} profile requires the TeamSnapshot policy"
                )
            if self.recharge_activity is None:
                raise ValueError(
                    f"{profile_id} profile requires the RechargeActivity policy"
                )
            if self.chapter_item_init is None:
                raise ValueError(
                    f"{profile_id} profile requires the ChapterItemInit policy"
                )
            if self.experience_task_init is None:
                raise ValueError(
                    f"{profile_id} profile requires the ExperienceTaskInit policy"
                )
            if self.chapter_task_init is None:
                raise ValueError(
                    f"{profile_id} profile requires the ChapterTaskInit policy"
                )
            if self.chapter_machine_init is None:
                raise ValueError(
                    f"{profile_id} profile requires the ChapterMachineInit policy"
                )
            if self.battle_start_reply is None:
                raise ValueError(
                    f"{profile_id} profile requires the BattleStartReply policy"
                )
            if self.battle_game_over_reply is None:
                raise ValueError(
                    f"{profile_id} profile requires the BattleGameOverReply policy"
                )
            if self.prison_fight is None:
                raise ValueError(
                    f"{profile_id} profile requires the "
                    "PrisonFightBossSnapshot policy"
                )
        # Introduced-in-v8 .. -v14 domains: only the versions that introduced
        # them may carry them, so every earlier server keeps sending
        # byte-for-byte the stream it sent before.
        introduced_after_v8 = (
            PROFILE_ID_V8, PROFILE_ID_V9, PROFILE_ID_V10, PROFILE_ID_V11,
            PROFILE_ID_V12, PROFILE_ID_V13, PROFILE_ID_V14, PROFILE_ID_V15,
            PROFILE_ID_V16, PROFILE_ID_V17, PROFILE_ID_V18, PROFILE_ID_V19,
            PROFILE_ID_V20, PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23,
            PROFILE_ID_V24,
        )
        introduced_after_v9 = (
            PROFILE_ID_V9, PROFILE_ID_V10, PROFILE_ID_V11, PROFILE_ID_V12,
            PROFILE_ID_V13, PROFILE_ID_V14, PROFILE_ID_V15, PROFILE_ID_V16,
            PROFILE_ID_V17, PROFILE_ID_V18, PROFILE_ID_V19, PROFILE_ID_V20,
            PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23, PROFILE_ID_V24,
        )
        introduced_after_v10 = (
            PROFILE_ID_V10, PROFILE_ID_V11, PROFILE_ID_V12, PROFILE_ID_V13,
            PROFILE_ID_V14, PROFILE_ID_V15, PROFILE_ID_V16, PROFILE_ID_V17,
            PROFILE_ID_V18, PROFILE_ID_V19, PROFILE_ID_V20, PROFILE_ID_V21,
            PROFILE_ID_V22, PROFILE_ID_V23, PROFILE_ID_V24,
        )
        if (
            profile_id not in introduced_after_v8
            and self.experience_task_init is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry an ExperienceTaskInit policy"
            )
        if (
            profile_id not in introduced_after_v9
            and self.chapter_task_init is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a ChapterTaskInit policy"
            )
        if (
            profile_id not in introduced_after_v10
            and self.chapter_machine_init is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a ChapterMachineInit policy"
            )
        if (
            profile_id
            not in (PROFILE_ID_V11, PROFILE_ID_V12, PROFILE_ID_V13,
                    PROFILE_ID_V14, PROFILE_ID_V15, PROFILE_ID_V16,
                    PROFILE_ID_V17, PROFILE_ID_V18, PROFILE_ID_V19,
                    PROFILE_ID_V20, PROFILE_ID_V21, PROFILE_ID_V22,
                    PROFILE_ID_V23, PROFILE_ID_V24)
            and self.battle_start_reply is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a BattleStartReply policy"
            )
        if self.battle_start_reply is not None:
            if profile_id in (PROFILE_ID_V19, PROFILE_ID_V20):
                required_policy_version = PROFILE_ID_V19
            elif profile_id in (PROFILE_ID_V17, PROFILE_ID_V18, PROFILE_ID_V21,
                                PROFILE_ID_V22, PROFILE_ID_V23, PROFILE_ID_V24):
                required_policy_version = PROFILE_ID_V17
            else:
                required_policy_version = PROFILE_ID_V11
            if self.battle_start_reply.introduced_in != required_policy_version:
                raise ValueError(
                    f"{profile_id} requires a BattleStartReply policy introduced "
                    f"in {required_policy_version!r}, got "
                    f"{self.battle_start_reply.introduced_in!r}"
                )
        if (
            profile_id not in (PROFILE_ID_V12, PROFILE_ID_V13, PROFILE_ID_V14,
                               PROFILE_ID_V15, PROFILE_ID_V16, PROFILE_ID_V17,
                               PROFILE_ID_V18, PROFILE_ID_V19, PROFILE_ID_V20,
                               PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23,
                               PROFILE_ID_V24)
            and self.battle_game_over_reply is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a BattleGameOverReply policy"
            )
        # Introduced in v13: the login prison-fight snapshot.  Every earlier
        # profile refuses it, so a v1-v12 server replays byte-for-byte the
        # stream it produced before this policy existed.
        if (
            profile_id not in (PROFILE_ID_V13, PROFILE_ID_V14, PROFILE_ID_V15,
                               PROFILE_ID_V16, PROFILE_ID_V17, PROFILE_ID_V18,
                               PROFILE_ID_V19, PROFILE_ID_V20, PROFILE_ID_V21,
                               PROFILE_ID_V22, PROFILE_ID_V23, PROFILE_ID_V24)
            and self.prison_fight is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a PrisonFightBossSnapshot policy"
            )
        # Introduced in v14: replay of the chapter-fight settlements this archive
        # observed.  Every earlier profile refuses it, so a v1-v13 server keeps
        # sending exactly the stream it sent before (in particular the v7
        # chapter-item block still sends zero rows).
        if (
            profile_id not in (PROFILE_ID_V14, PROFILE_ID_V15, PROFILE_ID_V16,
                               PROFILE_ID_V17, PROFILE_ID_V18, PROFILE_ID_V19,
                               PROFILE_ID_V20, PROFILE_ID_V21, PROFILE_ID_V22,
                               PROFILE_ID_V23, PROFILE_ID_V24)
            and self.chapter_fight_replay is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a ChapterFightReplay policy"
            )
        if (
            profile_id in (PROFILE_ID_V14, PROFILE_ID_V15, PROFILE_ID_V16,
                           PROFILE_ID_V17, PROFILE_ID_V18, PROFILE_ID_V19,
                           PROFILE_ID_V20, PROFILE_ID_V21, PROFILE_ID_V22,
                           PROFILE_ID_V23, PROFILE_ID_V24)
            and self.chapter_fight_replay is None
        ):
            raise ValueError(
                f"{profile_id} profile requires the ChapterFightReplay policy"
            )
        # Introduced in v15: the one-row chapter-task state that opens the 祈灵
        # (drawcard) main screen.  Every earlier profile refuses it, so a
        # v1-v14 server keeps sending exactly the stream it sent before.
        if profile_id not in (PROFILE_ID_V15, PROFILE_ID_V16, PROFILE_ID_V17,
                              PROFILE_ID_V18, PROFILE_ID_V19,
                              PROFILE_ID_V20,
                              PROFILE_ID_V21,
                              PROFILE_ID_V22,
                              PROFILE_ID_V23,
                              PROFILE_ID_V24) and self.drawcard_unlock is not None:
            raise ValueError(
                f"{profile_id} must not carry a DrawCardUnlock policy"
            )
        if profile_id in (PROFILE_ID_V15, PROFILE_ID_V16, PROFILE_ID_V17,
                          PROFILE_ID_V18, PROFILE_ID_V19,
                          PROFILE_ID_V20,
                          PROFILE_ID_V21,
                          PROFILE_ID_V22,
                          PROFILE_ID_V23,
                          PROFILE_ID_V24) and self.drawcard_unlock is None:
            raise ValueError(
                "v15 profile requires the DrawCardUnlock policy"
            )
        # Introduced in v18: the login replay of the chapter milestone chests
        # this role already opened.  Every earlier profile refuses it, so a
        # v1-v17 server keeps sending exactly the stream it sent before (zero
        # packets: the v7 chapter-item block still sends zero rows).  v19
        # inherits the v18 object by reference.
        if (
            profile_id not in (PROFILE_ID_V18, PROFILE_ID_V19, PROFILE_ID_V20,
                               PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23,
                               PROFILE_ID_V24)
            and self.chapter_box_replay is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a ChapterBoxReplay policy"
            )
        if (
            profile_id in (PROFILE_ID_V18, PROFILE_ID_V19, PROFILE_ID_V20,
                           PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23,
                           PROFILE_ID_V24)
            and self.chapter_box_replay is None
        ):
            raise ValueError(
                f"{profile_id} profile requires the ChapterBoxReplay policy"
            )
        # Introduced in v20: the post-settlement S2C 580 push of a paired
        # locktower (73) victory.  Only v20 may carry it; v18/v19 keep their
        # exact construction and behaviour (their dataclass default is None).
        if (
            profile_id not in (PROFILE_ID_V20,)
            and self.babel_settlement_push is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a BabelSettlementPush policy"
            )
        if (
            profile_id in (PROFILE_ID_V20,)
            and self.babel_settlement_push is None
        ):
            raise ValueError(
                "v20 profile requires the BabelSettlementPush policy"
            )
        # Introduced in v21: the empty returnMailStart/returnMailEnd login
        # bracket.  Only v21 and its successors may carry it.  v18/v19/v20
        # keep their exact construction and behaviour (dataclass default None),
        # so the pre-v21 login streams stay byte for byte frozen.
        if (
            profile_id not in (PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23,
                               PROFILE_ID_V24)
            and self.mail_login is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a MailLogin policy"
            )
        if (
            profile_id in (PROFILE_ID_V21, PROFILE_ID_V22, PROFILE_ID_V23,
                           PROFILE_ID_V24)
            and self.mail_login is None
        ):
            raise ValueError(
                "v21 profile requires the MailLogin policy"
            )
        # Introduced in v22: the S2C 39 returnTeamMaxFight login value.  Only v22
        # may carry it; v18..v21 keep their exact construction and behaviour
        # (dataclass default None), so their login streams stay byte for byte
        # frozen.
        if (
            profile_id not in (PROFILE_ID_V22, PROFILE_ID_V23, PROFILE_ID_V24)
            and self.team_max_fight is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a TeamMaxFight policy"
            )
        if (
            profile_id in (PROFILE_ID_V22, PROFILE_ID_V23, PROFILE_ID_V24)
            and self.team_max_fight is None
        ):
            raise ValueError(
                "v22 profile requires the TeamMaxFight policy"
            )
        # Introduced in v23: the synthetic month-sign state that answers C2S 725
        # and stops the client's 1000 ms poll.  Only v23 and its v24 successor
        # may carry it, so every earlier server keeps answering the request with
        # the single empty ack it sent before and no login stream moves.
        if (
            profile_id not in (PROFILE_ID_V23, PROFILE_ID_V24)
            and self.month_sign_state is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a MonthSignState policy"
            )
        if (
            profile_id in (PROFILE_ID_V23, PROFILE_ID_V24)
            and self.month_sign_state is None
        ):
            raise ValueError(
                "v23 profile requires the MonthSignState policy"
            )
        # Introduced in v24: the synthetic inactive bitRabbit state (S2C 1435
        # `syncBitRabbit`).  Without it `pkgBitRabbitDataMgr.checkAddRabbit`
        # compares a nil field with 0 at source line 102 and the main-city
        # red-point chain aborts (RUN-0077).  Only v24 may carry it, so every
        # earlier profile keeps its exact login stream.
        if (
            profile_id not in (PROFILE_ID_V24,)
            and self.bit_rabbit_state is not None
        ):
            raise ValueError(
                f"{profile_id} must not carry a BitRabbitState policy"
            )
        if (
            profile_id in (PROFILE_ID_V24,)
            and self.bit_rabbit_state is None
        ):
            raise ValueError(
                "v24 profile requires the BitRabbitState policy"
            )
        for provenance in self._core_provenances():
            if provenance.compatibility_version != PROFILE_ID_V1:
                raise ValueError(
                    f"core-domain provenance must stay {PROFILE_ID_V1!r}, got "
                    f"{provenance.compatibility_version!r}"
                )
        if len(self.role_values) != 252:
            raise ValueError(f"profile requires 252 RoleValue fields, got {len(self.role_values)}")
        positions = [field.wire_position for field in self.role_values]
        if positions != list(range(1, 253)):
            raise ValueError("RoleValue positions must uniquely cover 1..252 in order")
        names = [field.symbolic_name for field in self.role_values]
        if len(set(names)) != 252:
            raise ValueError("RoleValue symbolic names must be unique")
        wire_format = "".join(field.wire_type for field in self.role_values)
        if wire_format != ROLE_VALUE_WIRE_FORMAT:
            raise ValueError("RoleValue wire format must be p + 251*N")
        if self.class_counts() != {
            "C_SYNTHETIC_ZERO_SAFE": 9,
            "D_DEFERRED_WIRE_PLACEHOLDER": 243,
        }:
            raise ValueError(f"unexpected RoleValue class counts: {self.class_counts()}")
        if any(field.initial_value != 0 for field in self.role_values):
            raise ValueError("compatibility RoleValue amounts must be zero")
        _validate_catalog(self.sign_catalogs.daily, expected_indexes=range(1, 32))
        _validate_catalog(self.sign_catalogs.cumulative, expected_indexes=(3, 5, 7, 10))
        _validate_catalog(self.sign_catalogs.seven_day, expected_indexes=range(1, 8))
        _validate_sign_template(self.sign_state_template)
        _validate_scene_template(self.scene_state_template)
        rules = {rule.career: rule for rule in self.active_soul_rules}
        if set(rules) != {1} or rules[1].soul_id != 101:
            raise ValueError("active-soul rule must be career 1 -> 101")

    @property
    def profile_id(self) -> str:
        return self.metadata.profile_id

    def _core_provenances(self) -> tuple[Provenance, ...]:
        """Provenance records of the v1-introduced core domains."""

        return (
            tuple(field.provenance for field in self.role_values)
            + (
                self.sign_catalogs.daily_provenance,
                self.sign_catalogs.cumulative_provenance,
                self.sign_catalogs.seven_day_provenance,
            )
            + (
                self.sign_state_template.provenance,
                self.scene_state_template.provenance,
            )
            + tuple(rule.provenance for rule in self.active_soul_rules)
        )

    def role_value_vector(self) -> tuple[float | int, ...]:
        return tuple(field.initial_value for field in self.role_values)

    def class_counts(self) -> dict[str, int]:
        return dict(Counter(field.compatibility_class for field in self.role_values))

    def vector_hash(self) -> str:
        digest = hashlib.sha256()
        for field in self.role_values:
            digest.update(
                (
                    f"{field.wire_position}|{field.symbolic_name}|{field.wire_type}|"
                    f"{field.initial_value}|{field.compatibility_class}|"
                    f"{field.provenance.origin}\n"
                ).encode("utf-8")
            )
        return digest.hexdigest()

    def active_soul_for_career(self, career: int) -> ActiveSoulRule:
        for rule in self.active_soul_rules:
            if rule.career == career:
                return rule
        raise ValueError(
            f"profile {self.profile_id} has no approved active soul for career {career}"
        )


@dataclass(slots=True)
class MutableRoleValue:
    definition: RoleValueField
    value: float | int


@dataclass(slots=True)
class SignAccountState:
    is_new_month: bool
    sign_count: int
    make_up_time: int
    role_make_up_time: int
    is_today_signed: bool
    sum_claimed: str
    seven_claimed: str
    seven_day: int

    def protocol_fields(self, catalogs: SignCatalogs) -> tuple[object, ...]:
        return (
            self.is_new_month,
            self.sign_count,
            self.make_up_time,
            self.role_make_up_time,
            self.is_today_signed,
            catalogs.daily,
            catalogs.cumulative,
            self.sum_claimed,
            catalogs.seven_day,
            self.seven_claimed,
            self.seven_day,
        )


@dataclass(slots=True)
class SceneState:
    scene_type: int
    pos_x: int
    pos_y: int
    pos_z: int

    def protocol_fields(self) -> tuple[int, int, int, int]:
        return (self.scene_type, self.pos_x, self.pos_y, self.pos_z)


@dataclass(slots=True)
class RoleCompatibilityState:
    profile: CompatibilityProfile
    role_values: list[MutableRoleValue]
    sign_state: SignAccountState
    scene_state: SceneState
    active_soul: int

    def role_value_vector(self) -> tuple[float | int, ...]:
        if len(self.role_values) != 252:
            raise ValueError("role RoleValue state must contain 252 fields")
        values: list[float | int] = []
        for expected_position, field in enumerate(self.role_values, 1):
            if field.definition.wire_position != expected_position:
                raise ValueError("role RoleValue state is out of canonical order")
            if field.definition.wire_type == "p":
                if not math.isfinite(float(field.value)):
                    raise ValueError(f"{field.definition.symbolic_name}: non-finite p")
            elif (
                isinstance(field.value, bool)
                or not isinstance(field.value, int)
                or not 0 <= field.value <= UINT32_MAX
            ):
                raise ValueError(f"{field.definition.symbolic_name}: invalid uint32 N")
            values.append(field.value)
        return tuple(values)

    def vector_hash(self) -> str:
        values = self.role_value_vector()
        digest = hashlib.sha256()
        for field, value in zip(self.role_values, values, strict=True):
            digest.update(
                (
                    f"{field.definition.wire_position}|{field.definition.symbolic_name}|"
                    f"{field.definition.wire_type}|{value}|"
                    f"{field.definition.compatibility_class}|"
                    f"{field.definition.provenance.origin}\n"
                ).encode("utf-8")
            )
        return digest.hexdigest()


def _parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"invalid boolean text: {value!r}")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_role_value_fields(
    design_path: Path = ROLE_VALUE_DESIGN_PATH,
    mapping_path: Path = ROLE_VALUE_MAPPING_PATH,
) -> tuple[RoleValueField, ...]:
    design_rows = _read_csv(design_path)
    mapping_rows = _read_csv(mapping_path)
    if len(design_rows) != len(mapping_rows):
        raise ValueError("RoleValue design/mapping row counts differ")
    fields: list[RoleValueField] = []
    for design, mapping in zip(design_rows, mapping_rows, strict=True):
        position = int(design["wire_position"])
        if position != int(mapping["wire_index_1_based"]):
            raise ValueError(f"RoleValue position mismatch at {position}")
        if design["symbolic_name"] != mapping["value_name"]:
            raise ValueError(f"RoleValue symbolic identity mismatch at {position}")
        if design["type"] != mapping["wire_type"]:
            raise ValueError(f"RoleValue wire type mismatch at {position}")
        wire_type = design["type"]
        initial_value: float | int = (
            float(design["proposed_value"])
            if wire_type == "p"
            else int(design["proposed_value"])
        )
        fields.append(RoleValueField(
            wire_position=position,
            symbolic_name=design["symbolic_name"],
            wire_type=wire_type,
            initial_value=initial_value,
            compatibility_class=design["compatibility_class"],
            provenance=Provenance(
                source=design["source"],
                origin=design["origin"],
                evidence_grade=design["evidence_grade"],
                reason=design["reason"],
                compatibility_goal=COMPATIBILITY_GOAL,
                compatibility_version=design["compatibility_version"],
                field_contract=design["field_contract"],
                risk=design["risk"],
                replaceable=_parse_bool(design["replaceable"]),
            ),
        ))
    return tuple(fields)


def _validate_catalog(catalog: str, *, expected_indexes: Iterable[int]) -> None:
    rows = catalog.split(";") if catalog else []
    indexes: list[int] = []
    for row in rows:
        parts = row.split(",")
        if len(parts) != 4:
            raise ValueError(f"invalid sign catalog row: {row!r}")
        values = [int(part) for part in parts]
        if values[1] < 0 or values[2] < 0 or values[3] < 0:
            raise ValueError(f"negative sign catalog value: {row!r}")
        indexes.append(values[0])
    if indexes != list(expected_indexes):
        raise ValueError(f"unexpected sign catalog indexes: {indexes}")


def _validate_sign_template(state: SignStateTemplate) -> None:
    if state.is_new_month:
        raise ValueError("compatibility-v1 fresh account is not a month rollover")
    if (
        state.sign_count != 0
        or state.make_up_time != 0
        or state.role_make_up_time != 0
        or state.is_today_signed
        or state.sum_claimed
        or state.seven_claimed
        or state.seven_day != 1
    ):
        raise ValueError("invalid Compatibility Sign State V1")
    if state.provenance.origin != "synthetic_compatibility":
        raise ValueError("sign account state must be synthetic_compatibility")


def _validate_scene_template(scene: SceneStateTemplate) -> None:
    if scene.scene_type != 0 or (scene.pos_x, scene.pos_y, scene.pos_z) != (0, 0, 0):
        raise ValueError("invalid Compatibility Scene State V1")
    for value in (scene.pos_x, scene.pos_y, scene.pos_z):
        if not -(2**31) <= value < 2**31:
            raise ValueError("scene coordinate outside signed 32-bit range")
    if scene.provenance.origin != "synthetic_compatibility":
        raise ValueError("scene state must be synthetic_compatibility")


def _provenance(
    *,
    source: str,
    origin: str,
    evidence_grade: str,
    reason: str,
    compatibility_version: str,
    field_contract: str,
    risk: str,
    replaceable: bool,
) -> Provenance:
    return Provenance(
        source=source,
        origin=origin,
        evidence_grade=evidence_grade,
        reason=reason,
        compatibility_goal=COMPATIBILITY_GOAL,
        compatibility_version=compatibility_version,
        field_contract=field_contract,
        risk=risk,
        replaceable=replaceable,
    )


def _build_v1_profile() -> CompatibilityProfile:
    catalog_contract = "semicolon rows of index,itemType,itemId,itemNum"
    sign_catalogs = SignCatalogs(
        daily=DAILY_CATALOG,
        cumulative=CUMULATIVE_CATALOG,
        seven_day=SEVEN_DAY_CATALOG,
        daily_provenance=_provenance(
            source="config/sign/sign20185.max via pkgCfgSignMgr.initDefaultCfg",
            origin="official_recovered",
            evidence_grade="A",
            reason="target CN client directly contains the 31-row default daily catalog",
            compatibility_version=PROFILE_ID_V1,
            field_contract=catalog_contract,
            risk="low",
            replaceable=False,
        ),
        cumulative_provenance=_provenance(
            source="config/sign/signSum20185.max via pkgCfgSignMgr.initDefaultCfg",
            origin="official_recovered",
            evidence_grade="A",
            reason="target CN client directly contains the four cumulative milestones",
            compatibility_version=PROFILE_ID_V1,
            field_contract=catalog_contract,
            risk="low",
            replaceable=False,
        ),
        seven_day_provenance=_provenance(
            source="config/sign/signSeven.max via pkgCfgSignMgr.initSevenCfg",
            origin="official_recovered",
            evidence_grade="A",
            reason="target CN client directly contains the seven-day reward catalog",
            compatibility_version=PROFILE_ID_V1,
            field_contract=catalog_contract,
            risk="low",
            replaceable=False,
        ),
    )
    sign_state = SignStateTemplate(
        is_new_month=False,
        sign_count=0,
        make_up_time=0,
        role_make_up_time=0,
        is_today_signed=False,
        sum_claimed="",
        seven_claimed="",
        seven_day=1,
        provenance=_provenance(
            source="reports/compatibility_reconstruction_design.md#sign-in-compatibility-state",
            origin="synthetic_compatibility",
            evidence_grade="B client-consumer contract; D historical account state",
            reason="coherent unclaimed first-day revival account state with UI-safe catalogs",
            compatibility_version=PROFILE_ID_V1,
            field_contract="S2C85 account fields in yNNNyaaaaaN order",
            risk="claim mutation protocols remain unsupported",
            replaceable=True,
        ),
    )
    scene_state = SceneStateTemplate(
        scene_type=0,
        pos_x=0,
        pos_y=0,
        pos_z=0,
        provenance=_provenance(
            source="reports/compatibility_reconstruction_design.md#scene-selector-decision",
            origin="synthetic_compatibility",
            evidence_grade="B client fallback/unused-field contract; D historical state",
            reason="selector 0 uses explicit default-main-city branch; XYZ are unread placeholders",
            compatibility_version=PROFILE_ID_V1,
            field_contract="S2C283 Nnnn: uint32 selector plus three signed int32 coordinates",
            risk="re-audit if a future reconnect path consumes XYZ",
            replaceable=True,
        ),
    )
    active_soul = ActiveSoulRule(
        career=1,
        soul_id=101,
        provenance=_provenance(
            source="current-client career mapping and config/role/career",
            origin="deterministic_recovered",
            evidence_grade="B",
            reason="target client uniquely maps base career 1 to initial active soul 101",
            compatibility_version=PROFILE_ID_V1,
            field_contract="S2C554 N active soul ID",
            risk="profile acceptance is limited to career 1",
            replaceable=False,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V1,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=_load_role_value_fields(),
        sign_catalogs=sign_catalogs,
        sign_state_template=sign_state,
        scene_state_template=scene_state,
        active_soul_rules=(active_soul,),
    )


def _build_v2_profile() -> CompatibilityProfile:
    """Additive successor: v1 inherited state + the S2C 0 ServerTime policy."""

    v1 = get_profile_v1()
    server_time = ServerTimePolicy(
        time_policy=SERVER_TIME_TIME_POLICY,
        open_time_policy=SERVER_TIME_OPEN_TIME_POLICY,
        clock_value=0,
        power_recover_time_value=0,
        historical_ordering=SERVER_TIME_HISTORICAL_ORDERING,
        time_provenance=_provenance(
            source=(
                "recovered pkgServerTimeMgr init/getCurrentServerTime consumer semantics "
                "(reports/post_283_scene_initialization_forensics.md section 3)"
            ),
            origin="deterministic_recovered",
            evidence_grade="A target-client code; B consumer contract",
            reason=(
                "the client derives current time as le_dLoginServerTime + os.time() - "
                "le_dLoginClientTime, so the wire value is the server Unix wall-clock "
                "at send time; the revival server supplies its own current Unix time, "
                "not the historical CN server time"
            ),
            compatibility_version=PROFILE_ID_V2,
            field_contract="S2C0 dTime: uint32 unix seconds at send time",
            risk=(
                "time-window features follow the revival wall clock; enabling any of "
                "them requires re-auditing this policy"
            ),
            replaceable=True,
        ),
        clock_provenance=_provenance(
            source="reports/post_283_scene_initialization_forensics.md section 3.2",
            origin="synthetic_compatibility",
            evidence_grade="C dead field in this build",
            reason=(
                "dead-field placeholder for target CN 0.1.20.1; le_dLoginServerClock "
                "and getCurrentServerClock have no consumers and the historical wire "
                "value/unit is unknown"
            ),
            compatibility_version=PROFILE_ID_V2,
            field_contract="S2C0 dClock: uint32 placeholder 0",
            risk="re-audit before any consumer of the clock appears",
            replaceable=True,
        ),
        power_recover_provenance=_provenance(
            source="reports/post_283_scene_initialization_forensics.md section 3.2",
            origin="synthetic_compatibility",
            evidence_grade="C dead field in this build",
            reason=(
                "dead-field placeholder for target CN 0.1.20.1; getPowerRecoverTime "
                "has no consumers and the historical semantics/value are unknown; "
                "must be re-audited before any feature begins consuming it"
            ),
            compatibility_version=PROFILE_ID_V2,
            field_contract="S2C0 dPowerRecoverTime: uint32 placeholder 0",
            risk="re-audit before any consumer of power recovery appears",
            replaceable=True,
        ),
        open_time_provenance=_provenance(
            source=(
                "revival_stub process start time; consumer semantics from "
                "pkgCgGuildNcMgr.isSvrStartOver / pkgManorMapMgr.isManorUnlock"
            ),
            origin="synthetic_compatibility",
            evidence_grade="B client-consumer contract; D historical value",
            reason=(
                "uses the actual local revival process start time as a coherent "
                "revival-server open timestamp; historical CN server-open value is "
                "unavailable"
            ),
            compatibility_version=PROFILE_ID_V2,
            field_contract="S2C0 dOpenTime: uint32 unix seconds of revival server start",
            risk="revival-open time is process-local and is not the historical open time",
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source="reports/post_283_scene_initialization_forensics.md section 3.3",
            origin="synthetic_compatibility",
            evidence_grade="C historical order unknown; B client init-order contract",
            reason=(
                "historical login send ordering is unknown; S2C0 is placed after "
                "S2C27 and before the terminal S2C9(40) as an explicit synthetic "
                "compatibility ordering so ServerTime is initialized before the "
                "scene switch"
            ),
            compatibility_version=PROFILE_ID_V2,
            field_contract=(
                "terminal login sync segment order: 554 -> 27 -> 0 -> 9(40) -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V2,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v1.role_values,
        sign_catalogs=v1.sign_catalogs,
        sign_state_template=v1.sign_state_template,
        scene_state_template=v1.scene_state_template,
        active_soul_rules=v1.active_soul_rules,
        parent_profile_id=PROFILE_ID_V1,
        server_time=server_time,
    )


def _build_v3_profile() -> CompatibilityProfile:
    """Additive successor: v2 inherited state + the MysteryShop policy."""

    v2 = get_profile_v2()
    mystery_shop = MysteryShopPolicy(
        shop_type=MYSTERY_SHOP_TYPE,
        time_policy=MYSTERY_SHOP_TIME_POLICY,
        availability=MYSTERY_SHOP_AVAILABILITY,
        goods_count=MYSTERY_SHOP_GOODS_COUNT,
        snapshot_end_sent=MYSTERY_SHOP_SNAPSHOT_END_SENT,
        historical_ordering=MYSTERY_SHOP_HISTORICAL_ORDERING,
        shop_type_provenance=_provenance(
            source=(
                "pkgShopConst.D_MYSTERY_SHOP_TYPE and the recovered call site "
                "pkgNpcMgr.mysteryShopIsOpen"
            ),
            origin="deterministic_recovered",
            evidence_grade="A target-client constant + call site",
            reason="target CN client uses shop type 1 for the mystery shop",
            compatibility_version=PROFILE_ID_V3,
            field_contract="S2C487 dType: uint32 shop type 1",
            risk="low; constant-backed",
            replaceable=False,
        ),
        time_provenance=_provenance(
            source=(
                "recovered mystery-shop consumer semantics "
                "(reports/shop_sync_compatibility_candidate_review.md sections 6-8)"
            ),
            origin="synthetic_compatibility",
            evidence_grade="A field semantics; D historical value",
            reason=(
                "dTime is a close/expiry timestamp in the same Unix-second domain as "
                "getCurrentServerTime(); the compatibility value policy reuses the exact "
                "S2C0 dTime so the state is coherently closed at entry.  Historical "
                "fresh-role values are unknown and not claimed."
            ),
            compatibility_version=PROFILE_ID_V3,
            field_contract="S2C487 dTime: IEEE-754 double = the exact S2C0 dTime value",
            risk=(
                "couples to the v2 ServerTime policy; re-audit together if either changes"
            ),
            replaceable=True,
        ),
        availability_provenance=_provenance(
            source="reports/shop_sync_compatibility_candidate_review.md section 8",
            origin="synthetic_compatibility",
            evidence_grade="B client-comparison contract; D historical state",
            reason=(
                "closed is the client-consistent reading of shop dTime <= "
                "currentServerTime; it is not a recovered historical open/closed state"
            ),
            compatibility_version=PROFILE_ID_V3,
            field_contract="mysteryShopIsOpen(): dDiff > 0 required for open",
            risk=(
                "device clock rollback could flip the derived open state "
                "(nil-safe create path)"
            ),
            replaceable=True,
        ),
        goods_provenance=_provenance(
            source="reports/shop_sync_compatibility_candidate_review.md section 10",
            origin="synthetic_compatibility",
            evidence_grade="C no recovered fresh-role goods",
            reason=(
                "no S2C488 rows are sent: the mystery-shop goods list is intentionally "
                "empty; no goods instance IDs exist in the recovered record"
            ),
            compatibility_version=PROFILE_ID_V3,
            field_contract="le_tbShopGoods[1] has no goods rows",
            risk=(
                "re-audit before enabling any mystery-shop UI interaction (empty rows "
                "with a non-zero dTime would error in pkgUIShopCommonView.show)"
            ),
            replaceable=True,
        ),
        snapshot_end_provenance=_provenance(
            source="reports/shop_sync_compatibility_candidate_review.md section 9",
            origin="synthetic_compatibility",
            evidence_grade="B client handler analysis",
            reason=(
                "S2C487 alone initializes all state required by the first-scene consumer; "
                "S2C489 only dispatches SHOP_REFRESH_1 and is deliberately omitted to "
                "avoid an unnecessary business event before the shop UI exists"
            ),
            compatibility_version=PROFILE_ID_V3,
            field_contract="no S2C489 refreshShopEnd is sent for this snapshot",
            risk="re-audit if a consumer of SHOP_REFRESH_1 is registered before the shop UI",
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source="reports/shop_sync_compatibility_candidate_review.md section 11",
            origin="synthetic_compatibility",
            evidence_grade="C historical order unknown; B client init-order contract",
            reason=(
                "historical login ordering is unknown; S2C487 is placed after S2C0 and "
                "before the terminal S2C9(40) as an explicit synthetic compatibility "
                "ordering so the mystery-shop state exists before the scene switch"
            ),
            compatibility_version=PROFILE_ID_V3,
            field_contract=(
                "terminal login sync segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V3,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v2.role_values,
        sign_catalogs=v2.sign_catalogs,
        sign_state_template=v2.sign_state_template,
        scene_state_template=v2.scene_state_template,
        active_soul_rules=v2.active_soul_rules,
        parent_profile_id=PROFILE_ID_V2,
        server_time=v2.server_time,
        mystery_shop=mystery_shop,
    )


def _build_v4_profile() -> CompatibilityProfile:
    """Additive successor: v3 inherited state + the StoryStart policy."""

    v3 = get_profile_v3()
    story_start = StoryStartPolicy(
        story_list=STORY_START_LIST_POLICY,
        historical_ordering=STORY_START_HISTORICAL_ORDERING,
        snapshot_provenance=_provenance(
            source=(
                "recovered story-state consumer chain: pkgNpcMgr.create -> "
                "loadModelAssetAsync -> loadComplete -> "
                "pkgStoryTrigger_1008.triggerStory -> "
                "pkgStoryModelMgr.checkStoryFinish -> getStoryData -> "
                "le_story_list (reports/task_0027_story_1012_authority_archaeology.md "
                "sections 2.3 and 4.3)"
            ),
            origin="synthetic_compatibility",
            evidence_grade="A target-client read contract; D historical snapshot",
            reason=(
                "the client reads the story container unguarded from the per-NPC "
                "model-load callback of every main city, so the container must "
                "exist; S2C411 returnStoryStart with an EMPTY body makes the "
                "client's own le_story_list = {}, i.e. 'no story is finished'.  An "
                "empty container carries no per-story value, no identity and no "
                "count, so nothing is invented -- but this is a labelled assertion "
                "about a fresh revival session, NOT a recovered historical snapshot "
                "(D-05: no producer for the historical fresh-role state)"
            ),
            compatibility_version=PROFILE_ID_V4,
            field_contract="S2C411 returnStoryStart: empty body, zero wire fields",
            risk=(
                "the empty snapshot is not inert: it arms the gate-free story 1008, "
                "whose first action is C2S233 finishStory -- the companion transport "
                "edge must stay registered or the session ends under D-08"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "max_decoder/analyze_story_ordering.py (the main-city entry chain, "
                "asserted against the recovered sources and corpus)"
            ),
            origin="synthetic_compatibility",
            evidence_grade="C historical order unknown; A target-client call-order contract",
            reason=(
                "historical login ordering is unknown (the story family is not part "
                "of the 25-step login sync index), so S2C411 is sent immediately "
                "BEFORE the terminal S2C283 returnSceneInfo -- the packet whose "
                "dispatch starts the main-city switch "
                "(pkgOnlinePlayersSceneMgr.switchSceneByRecordType -> "
                "pkgSceneMgr.switchScene -> doFinishSwitch -> handlerForScene -> "
                "initInMainCity -> pkgNpcMgr.createByScene).  That is the latest "
                "wire position that provably precedes the le_story_list read, and "
                "the client has initialized the Story container before it enters "
                "the main city.  S2C13 enterGameSuccess is NOT that boundary: its "
                "recovered handler switches no scene and is reached only after the "
                "scene switch already began"
            ),
            compatibility_version=PROFILE_ID_V4,
            field_contract=(
                "terminal login segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> "
                "411 -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V4,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v3.role_values,
        sign_catalogs=v3.sign_catalogs,
        sign_state_template=v3.sign_state_template,
        scene_state_template=v3.scene_state_template,
        active_soul_rules=v3.active_soul_rules,
        parent_profile_id=PROFILE_ID_V3,
        server_time=v3.server_time,
        mystery_shop=v3.mystery_shop,
        story_start=story_start,
    )


def _build_v5_profile() -> CompatibilityProfile:
    """Additive successor: v4 inherited state + the TeamSnapshot policy."""

    v4 = get_profile_v4()
    team_snapshot = TeamSnapshotPolicy(
        current_group=TEAM_SNAPSHOT_CURRENT_GROUP,
        current_group_policy=TEAM_SNAPSHOT_CURRENT_GROUP_POLICY,
        empty_slot=TEAM_SNAPSHOT_EMPTY_SLOT,
        historical_ordering=TEAM_SNAPSHOT_HISTORICAL_ORDERING,
        container_provenance=_provenance(
            source=(
                "recovered consumer chain: pkgMainUIMgr.lua:1130 updateInfo -> "
                "updateFight -> pkgTeamDataMgr.getCurFight -> caclGroupFight -> "
                "pkgPlayerInfoMgr.caclRoleFight -> pkgEquipModelMgr.getTeamEquipFight "
                "-> getRoleEquipInfoList -> pkgTeamDataMgr.getGroupEquips; and "
                "pkgFabaoDataMgr.caclSelectFight -> pkgTeamDataMgr.getTeamFabaoData "
                "(max_decoder/forensics_post283/luaScript__team__pkgTeamDataMgr.lua, "
                "luaScript__equip__pkgEquipModelMgr.lua, "
                "luaScript__role__pkgPlayerInfoMgr.lua, "
                "luaScript__fabao__pkgFabaoDataMgr.lua; writers "
                "luaScript__socket__nc__pkgTeamNcMgr.lua and "
                "luaScript__socket__nc__pkgFabaoNcMgr.lua)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client read contract; D historical snapshot "
                "(D-33 section 2: corpus-exhaustive, no producer)"
            ),
            reason=(
                "the client's main-city UI init reads team state before it binds "
                "any main-city button and the two reads are UNGUARDED "
                "(tbTeamCard[gid].tbEquips) or unguarded-behind-the-range-guard "
                "(tbTeamFabao[gid] -> pairs(nil)); S2C36+S2C37+S2C48 build exactly "
                "the client's own representation of 'this role has no team': five "
                "empty presets, 4 empty equipment slots + 1 empty god weapon + 11 "
                "empty card slots for the named preset, and 3 empty fabao slots per "
                "preset.  Every value sent is the client's own empty-slot literal "
                "'' (getTeamCardData's own 11 x '' fallback, getTeamFabaoData's own "
                "3 x '' fallback), so no card, equipment, god weapon, level or fight "
                "value is invented -- but this is a LABELLED assertion about a role "
                "this archive holds no team rows for, NOT a recovered historical "
                "snapshot"
            ),
            compatibility_version=PROFILE_ID_V5,
            field_contract=(
                "S2C36 returnTeamStart: 0 fields; "
                "S2C37 returnTeamInfo: 1 N group id + 16 a empty slots; "
                "S2C48 returnSelectMagicStart: 0 fields"
            ),
            risk=(
                "the empty snapshot is not inert: it makes getCurGroupID() a legal "
                "group, so the client's own team/fabao readers stop raising and the "
                "main-city UI can bind its function buttons; if a producer for a "
                "role's real team is ever recovered, this policy must be replaced"
            ),
            replaceable=True,
        ),
        current_group_provenance=_provenance(
            source=(
                "pkgTeamDataMgr: `dMaxTeam = 5`, `initTeamCardData` loops "
                "`for i = 1, dMaxTeam`, `getTeamCardData`/`getTeamFabaoData` admit "
                "exactly `dGroupID > 0 and dGroupID <= dMaxTeam`, and "
                "`getCurGroupID` returns the -1 sentinel while `dTeamGroupID` is nil "
                "(luaScript__team__pkgTeamDataMgr.lua); D-33 section 2 (the "
                "fresh-role current group has no recovered producer anywhere in the "
                "3,373-chunk corpus)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client contract for the legal set {1..5}; D historical "
                "value (unknown)"
            ),
            reason=(
                "the group id is written by the server (S2C35 -> setCurGroupID is "
                "its only writer) and read by the client only as a container "
                "selector; the client never asks for it at login (C2S5 "
                "changeTeamGroup is emitted only from the Team UI).  With all five "
                "presets empty the five containers are structurally identical, and 1 "
                "is the minimum member of the client's own legal set and the first "
                "container its own initTeamCardData creates -- chosen as the "
                "documented compatibility minimum, never claimed as recovered"
            ),
            compatibility_version=PROFILE_ID_V5,
            field_contract="S2C35 returnGroupId: 1 uint32 in 1..dMaxTeam",
            risk=(
                "if a producer for the fresh-role current group is ever recovered, "
                "replace this value in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "max_decoder/analyze_team_login_snapshot.py (the recovered "
                "main-city entry chain, asserted against the recovered sources)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C historical order unknown; A target-client call-order contract"
            ),
            reason=(
                "historical login ordering is unknown, so the snapshot is sent "
                "immediately BEFORE the terminal S2C411 story snapshot and the "
                "S2C283 whose dispatch starts the main-city switch "
                "(pkgOnlinePlayersSceneMgr.switchSceneByRecordType -> "
                "pkgSceneMgr.switchScene -> doFinishSwitch -> pkgMainUIMgr "
                "onComplete -> init -> updateInfo at line 1130).  The intra-snapshot "
                "order is forced by the client's own code, not chosen: S2C36 "
                "(initTeamCardData) and S2C37 (setTeamCardData) must precede S2C35, "
                "because setCurGroupID dispatches pkgEventType.CHANGE_TEAM and "
                "pkgMainUIMgr maps CHANGE_TEAM to updateFight, which reads the "
                "unguarded tbTeamCard[getCurGroupID()]"
            ),
            compatibility_version=PROFILE_ID_V5,
            field_contract=(
                "terminal login segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> "
                "36 -> 37 -> 48 -> 35 -> 411 -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V5,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v4.role_values,
        sign_catalogs=v4.sign_catalogs,
        sign_state_template=v4.sign_state_template,
        scene_state_template=v4.scene_state_template,
        active_soul_rules=v4.active_soul_rules,
        parent_profile_id=PROFILE_ID_V4,
        server_time=v4.server_time,
        mystery_shop=v4.mystery_shop,
        story_start=v4.story_start,
        team_snapshot=team_snapshot,
    )


def _build_v6_profile() -> CompatibilityProfile:
    """Additive successor: v5 inherited state + the RechargeActivity policy."""

    v5 = get_profile_v5()
    recharge_activity = RechargeActivityPolicy(
        recharge_start=RECHARGE_ACTIVITY_RECHARGE_START,
        recharge_start_policy=RECHARGE_ACTIVITY_RECHARGE_START_POLICY,
        reset_rows=RECHARGE_ACTIVITY_RESET_ROWS,
        historical_ordering=RECHARGE_ACTIVITY_HISTORICAL_ORDERING,
        container_provenance=_provenance(
            source=(
                "recovered consumer chain: pkgMainUIMgr.lua:1133 initBtn -> :916 "
                "initBtnReplace -> :870 checkRedPoint -> :962 "
                "pkgUIMainUIControl.checkRedPointOnMainUI -> :204 "
                "checkRedPointRechargeActivity -> :502 pkgKoiDataMgr.getGiftState -> "
                "pkgKoiDataMgr.lua:88 pkgUIRechargeACTaskView.getTask2HadGet -> "
                "pkgUIRechargeACTaskView.lua:70 tbInfo.bHadGet; writer "
                "pkgRechargeActivityModel.initRechargeStart (43-49) and "
                "pkgRechargeActivityS2CMgr.s2c.returnACRechargeDataStart (12-19) "
                "(max_decoder/forensics_recharge_ui/luaScript__rechargeActivity__"
                "pkgRechargeActivityModel.lua, ...__pkgRechargeActivityS2CMgr.lua, "
                "...__pkgUIRechargeACTaskView.lua, "
                "max_decoder/forensics_month_sign/luaScript__koi__pkgKoiDataMgr.lua, "
                "...__mainUI__pkgUIMainUIMgr.lua; RUN-0027 logcat 01:16:30.354)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client read contract; D historical snapshot "
                "(no producer anywhere in the 3,372-chunk corpus)"
            ),
            reason=(
                "the client's own main-city red-point pass reads "
                "tbGetRecharge[1003] through an UNGUARDED `tbInfo.bHadGet` while "
                "the container is still the empty table its own "
                "_createRechargeActivity built; the guard that was meant to stop "
                "it (`le_tbSelf and le_tbSelf.dChargeTime`) cannot, because "
                "pkgKoiDataMgr initialises dChargeTime to 0 and 0 is truthy in "
                "Lua.  S2C1014 with bReset = true runs the client's OWN "
                "initRechargeStart(), which creates one {bEnough = false, "
                "bHadGet = false} row per id of the CLIENT'S OWN config table "
                "e_rechargeActivityTask -- the same 'the client's own empty "
                "literal' shape the v5 team snapshot uses.  No task, reward or "
                "purchase is invented, and C2S500 sendGetACData has no caller in "
                "the corpus, so this state can only arrive as a login push"
            ),
            compatibility_version=PROFILE_ID_V6,
            field_contract=(
                "S2C1014 returnACRechargeDataStart: 1 p recharge total + 1 y "
                "bReset; bReset=true selects the client's own initRechargeStart"
            ),
            risk=(
                "the empty snapshot is not inert: it makes every recharge task "
                "row exist with bHadGet=false, so the welfare red-point pass "
                "completes instead of raising.  If a producer for a role's real "
                "recharge activity state is ever recovered, replace this policy "
                "in a new profile version"
            ),
            replaceable=True,
        ),
        recharge_start_provenance=_provenance(
            source=(
                "S2C10 returnRoleInfo's already-shipped role snapshot "
                "(returnRoleInfo fields recharge_sum = 0, one_day_recharge_num = 0; "
                "RUN-0027 server.log line 41) and pkgRechargeActivityModel."
                "_createRechargeActivity's own `dRechargeStart = -1` literal "
                "(source lines 120-132), whose read-back "
                "getRechargeShowValue (31-41) clamps every value <= 0 to 0"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "D historical value (unknown); A target-client clamp contract"
            ),
            reason=(
                "there is no recovered producer for a historical dRechargeStart "
                "(D-21), and no player recharge or reward row is invented: the "
                "archive already publishes this role's recharge total as 0 in "
                "S2C10, and the client clamps every value <= 0 to 0 before it is "
                "shown, so 0 and the client's own uninitialised -1 are "
                "observationally identical; 0 is used because it is the value the "
                "role snapshot this archive already ships states"
            ),
            compatibility_version=PROFILE_ID_V6,
            field_contract="S2C1014 dRechargeStart: 1 double, 0 for this archive",
            risk=(
                "if a producer for the role's real recharge total is recovered, "
                "replace this value in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "max_decoder/analyze_recharge_activity_red_point.py (the recovered "
                "main-city red-point chain, asserted against the recovered sources)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C historical order unknown; A target-client call-order contract"
            ),
            reason=(
                "historical login ordering is unknown, so the packet is sent "
                "immediately after the v5 team snapshot and before the S2C411 "
                "story snapshot and the S2C283 whose dispatch starts the "
                "main-city switch (pkgSceneMgr.switchScene(TYPE_MAIN_CITY) -> "
                "doFinishSwitch -> pkgMainUIMgr.onComplete -> init -> initBtn at "
                "line 1133, whose initBtnReplace at :916 is what reads the "
                "container).  That is the latest wire position that provably "
                "precedes the read"
            ),
            compatibility_version=PROFILE_ID_V6,
            field_contract=(
                "terminal login segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> "
                "36 -> 37 -> 48 -> 35 -> 1014 -> 411 -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V6,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v5.role_values,
        sign_catalogs=v5.sign_catalogs,
        sign_state_template=v5.sign_state_template,
        scene_state_template=v5.scene_state_template,
        active_soul_rules=v5.active_soul_rules,
        parent_profile_id=PROFILE_ID_V5,
        server_time=v5.server_time,
        mystery_shop=v5.mystery_shop,
        story_start=v5.story_start,
        team_snapshot=v5.team_snapshot,
        recharge_activity=recharge_activity,
    )


@lru_cache(maxsize=1)
def get_profile_v1() -> CompatibilityProfile:
    """Return the immutable historical v1 compatibility profile."""

    return _build_v1_profile()


@lru_cache(maxsize=1)
def get_profile_v2() -> CompatibilityProfile:
    """Return the immutable v2 compatibility profile (v1 + ServerTime only)."""

    return _build_v2_profile()


@lru_cache(maxsize=1)
def get_profile_v3() -> CompatibilityProfile:
    """Return the immutable v3 profile (v2 + MysteryShop only)."""

    return _build_v3_profile()


@lru_cache(maxsize=1)
def get_profile_v4() -> CompatibilityProfile:
    """Return the immutable v4 profile (v3 + the StoryStart policy only)."""

    return _build_v4_profile()


@lru_cache(maxsize=1)
def get_profile_v5() -> CompatibilityProfile:
    """Return the immutable v5 profile (v4 + the TeamSnapshot policy only)."""

    return _build_v5_profile()


@lru_cache(maxsize=1)
def get_profile_v6() -> CompatibilityProfile:
    """Return the immutable v6 profile (v5 + the RechargeActivity policy only)."""

    return _build_v6_profile()


def _build_v7_profile() -> CompatibilityProfile:
    """Additive successor: v6 inherited state + the ChapterItemInit policy."""

    v6 = get_profile_v6()
    chapter_item_init = ChapterItemInitPolicy(
        container_policy=CHAPTER_ITEM_CONTAINER_POLICY,
        row_count=CHAPTER_ITEM_ROW_COUNT,
        historical_ordering=CHAPTER_ITEM_HISTORICAL_ORDERING,
        container_provenance=_provenance(
            source=(
                "recovered consumer chain: pkgChapterMainUIViewMgr.lua:446 init -> "
                ":245 initChapter -> :63 initChapterBtnNormalStatus -> "
                "pkgChapterInfoMgr.lua:165 isChapterPass -> :128 getChapterProgress -> "
                "the local function declared at :208 (isItemFightOver) -> :213 "
                "`le_tbChapterItemInfo[dChapterId]`; writer "
                "pkgChapterInfoMgr.initChapterItemStart (48-50) reached only through "
                "socket/nc/pkgGuankaMapNcMgr's s2c.initChapterItemStart "
                "(max_decoder/forensics_adventure_entry/"
                "luaScript__chapter__pkgChapterInfoMgr.lua, "
                "...__pkgChapterMainUIViewMgr.lua, "
                "max_decoder/forensics_chapter_item_init/"
                "luaScript__socket__nc__pkgGuankaMapNcMgr.listing.txt; RUN-0029 "
                "logcat 03:31:25.490)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client read contract; C historical login placement "
                "(no recovered packet-order evidence for this family)"
            ),
            reason=(
                "the world map's own init reads le_tbChapterItemInfo through "
                "UNGUARDED indexes (`isItemFightOver`/`isItemEventOver`, reached from "
                "getChapterProgress -> isChapterPass) while the global is still nil, "
                "because its only creator is the client's own "
                "pkgChapterInfoMgr.initChapterItemStart().  RUN-0029 raised exactly "
                "there on the chapter-1 iteration, before initChapter had bound ANY "
                "chapter button, so the tap the runner did deliver (Android "
                "ACTION_DOWN 703,567) had no listener at all -- and the same read sits "
                "in onChapterClickHandler at line 154, before it activates "
                "Panel/btnDetail.  S2C389 with an empty body runs the client's OWN "
                "`le_tbChapterItemInfo = {}`; NO item row is sent, because a row would "
                "assert that a named map item of a named chapter is "
                "triggered/completed and this archive has never entered a chapter "
                "(D-21).  The client's own sibling reader isChapterArrivedExit already "
                "carries an `if not le_tbChapterItemInfo then return` guard, which is "
                "the client saying nil is a state it expects to be given -- a repair "
                "that guessed values into the container would be inventing exactly the "
                "map state that guard protects"
            ),
            compatibility_version=PROFILE_ID_V7,
            field_contract=(
                "S2C389 initChapterItemStart: empty body, runs the client's own "
                "initChapterItemStart; S2C391 initChapterItemEnd: empty body, the "
                "client's own empty end handler; zero initChapterItemInfo rows"
            ),
            risk=(
                "an empty container is not inert: the detail panel then shows the "
                "client's own derived 0 of N items / 0%, and every per-chapter "
                "progress read answers 'nothing explored yet'.  If a producer for a "
                "role's real chapter-map exploration state is ever recovered, replace "
                "this policy in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "max_decoder/analyze_chapter_item_init.py (the recovered world-map "
                "read chain and the handler pass-throughs, asserted against the "
                "recovered sources)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C historical order unknown; A target-client call-order contract"
            ),
            reason=(
                "historical login placement of the initChapterItem* family is unknown, "
                "so the pair is sent with the other login-time container pushes, after "
                "the v6 recharge-activity packet and before the S2C411 story snapshot "
                "and the S2C283 whose dispatch starts the main-city switch.  The "
                "client's own code fixes the INTRA-block order and only that: 390 "
                "indexes the global that 389 creates, and 391 is the block's end "
                "handler.  S2C389/391 are the only members sent; 390/392/393 stay "
                "unregistered"
            ),
            compatibility_version=PROFILE_ID_V7,
            field_contract=(
                "terminal login segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> "
                "36 -> 37 -> 48 -> 35 -> 1014 -> 389 -> 391 -> 411 -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V7,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v6.role_values,
        sign_catalogs=v6.sign_catalogs,
        sign_state_template=v6.sign_state_template,
        scene_state_template=v6.scene_state_template,
        active_soul_rules=v6.active_soul_rules,
        parent_profile_id=PROFILE_ID_V6,
        server_time=v6.server_time,
        mystery_shop=v6.mystery_shop,
        story_start=v6.story_start,
        team_snapshot=v6.team_snapshot,
        recharge_activity=v6.recharge_activity,
        chapter_item_init=chapter_item_init,
    )


@lru_cache(maxsize=1)
def get_profile_v7() -> CompatibilityProfile:
    """Return the immutable v7 profile (v6 + the ChapterItemInit policy only)."""

    return _build_v7_profile()


def _build_v8_profile() -> CompatibilityProfile:
    """Additive successor: v7 inherited state + the ExperienceTaskInit policy."""

    v7 = get_profile_v7()
    experience_task_init = ExperienceTaskInitPolicy(
        container_policy=EXPERIENCE_TASK_CONTAINER_POLICY,
        row_count=EXPERIENCE_TASK_ROW_COUNT,
        historical_ordering=EXPERIENCE_TASK_HISTORICAL_ORDERING,
        container_provenance=_provenance(
            source=(
                "recovered chain, innermost last: pkgExperienceMainUIMgr.lua:62 "
                "onCountdown (the 1,000 ms timer created by initExperience:169 -> "
                "createTimer 118-124) -> :73 pkgExperienceMgr.CloseActivityByClient "
                "-> pkgExperienceMgr.lua:178 pkgEventMgr.dispatch("
                "EXPERIENCE_STATUS_CHANGE) -> pkgExperienceMainUIMgr.lua:199 "
                "`pkgUtilMgr.setActiveByName(gameObject, \"Panel/btnDetail\", "
                "false)` -> :214 changeChapterBtnExperienceStatus -> :643 "
                "pkgExperienceMgr.GetTaskListByChapterId -> pkgExperienceMgr.lua:203 "
                "`le_tbTaskByChapterId[dChapterId]`; the container's only creator is "
                "the S2C handler pkgExperienceMgr registers for itself, "
                "s2c.returnTaskStart (lines 62-66), with s2c.returnTaskEnd (116-118) "
                "closing the block (max_decoder/analyze_experience_task_init.py, "
                "which re-reads experience/pkgExperienceMgr.max, "
                "experience/pkgExperienceMainUIMgr.max and event/pkgEventMgr.max "
                "with the repo's own decoder; RUN-0031 logcat 05:22:28.427)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client read contract; C historical login placement "
                "(no recovered packet-order evidence for this family)"
            ),
            reason=(
                "the client's own 1 Hz countdown switches the chapter detail panel "
                "off (pkgExperienceMainUIMgr.lua:199) and then raises on the nil "
                "`le_tbTaskByChapterId` (pkgExperienceMgr.lua:203) INSIDE the "
                "dispatch, so onCountdown's own `pkgTimerMgr.delete(le_dTimer)` "
                "(lines 74-76) never runs and the tick repeats forever: RUN-0031's "
                "single confirm tap (Android ACTION_DOWN x=1310 y=645, logcat "
                "05:22:28.225, inside the recovered 确认 rectangle 1172..1448 / "
                "591..699) landed on a panel that had been inactive for ~7 s, which "
                "is why no Lua error, no frame change and no switchScene followed.  "
                "S2C1307 with an empty body runs the client's OWN s2c.returnTaskStart "
                "(`le_tbTaskByChapterId = {}`); NO task row is sent, because a row "
                "would assert one experience task of one chapter with its own "
                "id/state/progress and this archive has none (D-21).  With the "
                "container present the same tick completes and deletes its own "
                "timer, so the panel close happens at most once, before any "
                "player-opened panel"
            ),
            compatibility_version=PROFILE_ID_V8,
            field_contract=(
                "S2C1307 returnTaskStart: empty body, runs the client's own "
                "returnTaskStart; S2C1309 returnTaskEnd: empty body, the client's "
                "own empty end handler; zero returnTaskInfo rows"
            ),
            risk=(
                "an empty container is not inert: `GetTaskListByChapterId` then "
                "answers nil for every chapter, so every 历练 (experience) badge, "
                "red point and task row stays absent.  If a producer for a role's "
                "real experience-task state is ever recovered, replace this policy "
                "in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "max_decoder/analyze_experience_task_init.py (the recovered "
                "countdown/dispatch chain and the handler identities, asserted "
                "against the shipped chunks)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C historical order unknown; A target-client call-order contract"
            ),
            reason=(
                "historical login placement of the returnTask* family is unknown, so "
                "the pair is sent with the other login-time container pushes, after "
                "the v7 chapter-item pair and before the S2C411 story snapshot and "
                "the S2C283 whose dispatch starts the main-city switch -- i.e. long "
                "before the world map's own init starts the countdown timer.  The "
                "client's own code fixes the INTRA-block order and only that: the "
                "row reader indexes the global that only returnTaskStart creates.  "
                "S2C1307/1309 are the only members sent; 1308 and 1310-1312 stay "
                "unregistered"
            ),
            compatibility_version=PROFILE_ID_V8,
            field_contract=(
                "terminal login segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> "
                "36 -> 37 -> 48 -> 35 -> 1014 -> 389 -> 391 -> 1307 -> 1309 -> "
                "411 -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V8,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v7.role_values,
        sign_catalogs=v7.sign_catalogs,
        sign_state_template=v7.sign_state_template,
        scene_state_template=v7.scene_state_template,
        active_soul_rules=v7.active_soul_rules,
        parent_profile_id=PROFILE_ID_V7,
        server_time=v7.server_time,
        mystery_shop=v7.mystery_shop,
        story_start=v7.story_start,
        team_snapshot=v7.team_snapshot,
        recharge_activity=v7.recharge_activity,
        chapter_item_init=v7.chapter_item_init,
        experience_task_init=experience_task_init,
    )


@lru_cache(maxsize=1)
def get_profile_v8() -> CompatibilityProfile:
    """Return the immutable v8 profile (v7 + the ExperienceTaskInit policy only)."""

    return _build_v8_profile()


def _build_v9_profile() -> CompatibilityProfile:
    """Additive successor: v8 inherited state + the ChapterTaskInit policy."""

    v8 = get_profile_v8()
    chapter_task_init = ChapterTaskInitPolicy(
        container_policy=CHAPTER_TASK_CONTAINER_POLICY,
        row_count=CHAPTER_TASK_ROW_COUNT,
        historical_ordering=CHAPTER_TASK_HISTORICAL_ORDERING,
        container_provenance=_provenance(
            source=(
                "recovered chain, innermost last: pkgSceneMgr.lua:875 "
                "doFinishSwitch calls the callback pkgChapterSceneMgr.enterChapter "
                "closed over at 131 and pkgSceneMgr.switchScene stored in the global "
                "`le_finishCallBack` at 771 -> pkgChapterSceneMgr.lua:112 "
                "le_finishCallBack -> pkgChapterObjectMgr.lua:385 initIndex2TypeId -> "
                "pkgChapterInfoMgr.lua:309 getActiveMap -> :262 isItemOver -> "
                "pkgChapterTaskMgr.lua:62 isTaskReceived indexes the global "
                "UNGUARDED; the container's only creator across the whole shipped "
                "luaScript tree is the S2C handler pkgChapterTaskMgr exposes as "
                "initChapterTaskStart (lines 21-23), with initChapterTaskEnd (30-32) "
                "closing the block "
                "(max_decoder/analyze_chapter_task_init.py, which re-reads "
                "chapter/pkgChapterTaskMgr.max, chapter/pkgChapterInfoMgr.max, "
                "chapter/pkgChapterObjectMgr.max, chapter/pkgChapterSceneMgr.max and "
                "scene/pkgSceneMgr.max with the repo's own decoder; RUN-0032 logcat "
                "07:17:47.733)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client read contract; C historical login placement "
                "(no recovered packet-order evidence for this family)"
            ),
            reason=(
                "RUN-0032's client really started the chapter scene -- its own "
                "`switchScene chapter_qiandengzhen_1 3 nil` at 07:17:46.241, the AB "
                "scene and its juqing manifest loaded, the loader then called "
                "doFinishSwitch -- and the finish callback raised 1.492 s later on "
                "the nil `le_tbChapterTaskInfo`.  doFinishSwitch dispatches "
                "ENTER_SCENE at 880, calls pkgSyncSceneMgr.enterScene at 881, prints "
                "`-- doFinishSwitch` at 882 and tears the loading UI down at 884-890, "
                "all AFTER the 875 that raised, so the switch never completed: the "
                "run's whole logcat carries `-- doFinishSwitch` exactly twice (the "
                "main city and the world map) and never for the chapter, and the run "
                "ends on the client's own frozen loading screen (final frame 82.17 "
                "off the frozen chapter-1 node cells, its viewport box 93.70% "
                "near-black against 0.22% on the world map).  S2C385 with an empty "
                "body runs the client's OWN s2c.initChapterTaskStart "
                "(`le_tbChapterTaskInfo = {}`); NO task row is sent, because a row "
                "would assert that a specific chapter task is received/completed and "
                "this archive has none (D-21)"
            ),
            compatibility_version=PROFILE_ID_V9,
            field_contract=(
                "S2C385 initChapterTaskStart: empty body, runs the client's own "
                "initChapterTaskStart; S2C387 initChapterTaskEnd: empty body, the "
                "client's own empty end handler; zero initChapterTaskInfo rows"
            ),
            risk=(
                "an empty container is not inert: `isTaskReceived`/`isTaskCompleted` "
                "then answer false for every task of every chapter, so every "
                "chapter-task NPC flag, task row and submit marker stays absent.  If "
                "a producer for a role's real chapter-task state is ever recovered, "
                "replace this policy in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "max_decoder/analyze_chapter_task_init.py (the recovered finish "
                "chain, the container's single writer and the handler identities, "
                "asserted against the shipped chunks)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C historical order unknown; A target-client registration and "
                "call-order contract"
            ),
            reason=(
                "historical login placement of the initChapterTask* family is "
                "unknown, so the pair is sent with the other login-time container "
                "pushes, immediately BEFORE the v7 chapter-item pair and therefore "
                "long before the S2C411 story snapshot and the S2C283 whose dispatch "
                "starts the main-city switch.  The client's own registration table "
                "fixes the choice of neighbour: the task family (385-388) sits "
                "immediately before the item family (389-392), and both containers "
                "are read in the same pass of the same finish callback.  The client's "
                "own code fixes the INTRA-block order and only that: the row reader "
                "indexes the global that only initChapterTaskStart creates.  "
                "S2C385/387 are the only members sent; 386 and 388 stay unregistered"
            ),
            compatibility_version=PROFILE_ID_V9,
            field_contract=(
                "terminal login segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> "
                "36 -> 37 -> 48 -> 35 -> 1014 -> 385 -> 387 -> 389 -> 391 -> "
                "1307 -> 1309 -> 411 -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V9,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v8.role_values,
        sign_catalogs=v8.sign_catalogs,
        sign_state_template=v8.sign_state_template,
        scene_state_template=v8.scene_state_template,
        active_soul_rules=v8.active_soul_rules,
        parent_profile_id=PROFILE_ID_V8,
        server_time=v8.server_time,
        mystery_shop=v8.mystery_shop,
        story_start=v8.story_start,
        team_snapshot=v8.team_snapshot,
        recharge_activity=v8.recharge_activity,
        chapter_item_init=v8.chapter_item_init,
        experience_task_init=v8.experience_task_init,
        chapter_task_init=chapter_task_init,
    )


@lru_cache(maxsize=1)
def get_profile_v9() -> CompatibilityProfile:
    """Return the immutable v9 profile (v8 + the ChapterTaskInit policy only)."""

    return _build_v9_profile()


def _build_v10_profile() -> CompatibilityProfile:
    """Additive successor: v9 inherited state + the ChapterMachineInit policy."""

    v9 = get_profile_v9()
    chapter_machine_init = ChapterMachineInitPolicy(
        container_policy=CHAPTER_MACHINE_CONTAINER_POLICY,
        row_count=CHAPTER_MACHINE_ROW_COUNT,
        historical_ordering=CHAPTER_MACHINE_HISTORICAL_ORDERING,
        container_provenance=_provenance(
            source=(
                "recovered chain, innermost last: pkgSceneMgr.lua:875 "
                "doFinishSwitch calls the callback pkgChapterSceneMgr.enterChapter "
                "closed over at 131 and pkgSceneMgr.switchScene stored in the global "
                "`le_finishCallBack` at 771 -> pkgChapterSceneMgr.lua:114 "
                "le_finishCallBack -> pkgChapterObjectMgr.lua:287 initScene -> "
                "pkgChapterMachineMgr.lua:312 initAllMachineObj -> :46 "
                "isMachineCompleted indexes the global UNGUARDED at :45-46; the "
                "container's only creator across the whole shipped luaScript tree is "
                "the handler the owning module registers ITSELF, "
                "`s2c.initChapterMachineStart` (chapter/pkgChapterMachineMgr.lua "
                "lines 18-20), with the empty s2c.initChapterMachineEnd (27-29) "
                "closing the block (max_decoder/analyze_chapter_machine_init.py, "
                "which re-reads chapter/pkgChapterMachineMgr.max, "
                "chapter/pkgChapterObjectMgr.max, chapter/pkgChapterSceneMgr.max and "
                "chapter/pkgChapterInfoMgr.max with the repo's own decoder; RUN-0033 "
                "logcat 23:50:42.305)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client read contract; C historical login placement "
                "(no recovered packet-order evidence for this family)"
            ),
            reason=(
                "RUN-0033's client got one statement past the line the v9 "
                "chapter-task container cleared -- RUN-0032 raised at "
                "pkgChapterSceneMgr.lua:112 (initIndex2TypeId) and RUN-0033's line 112 "
                "returns, line 113 returns, and the raise is at :114 -> "
                "pkgChapterObjectMgr.lua:287 initScene -> :312 initAllMachineObj -> "
                "pkgChapterMachineMgr.lua:46 isMachineCompleted on the nil "
                "`le_tbChapterMachineInfo` (also one statement after initScene's own "
                "pkgChapterTaskMgr.initAllTask at 286 returned).  Its own "
                "`switchScene chapter_qiandengzhen_1 3 nil` is at 23:50:40.840, the "
                "raise 1.465 s later, and the whole logcat carries `-- doFinishSwitch` "
                "exactly twice -- main city and world map -- and never for the "
                "chapter, because doFinishSwitch dispatches ENTER_SCENE at 880, calls "
                "pkgSyncSceneMgr.enterScene at 881, prints it at 882 and tears the "
                "loading UI down at 884-890, all AFTER the 875 that raised.  S2C400 "
                "with an empty body runs the client's OWN s2c.initChapterMachineStart "
                "(`le_tbChapterMachineInfo = {}`); NO machine row is sent, because a "
                "row would assert that a named machine of a named chapter is "
                "completed and this archive has none (D-21)"
            ),
            compatibility_version=PROFILE_ID_V10,
            field_contract=(
                "S2C400 initChapterMachineStart: empty body, runs the client's own "
                "initChapterMachineStart; S2C402 initChapterMachineEnd: empty body, "
                "the client's own empty end handler; zero initChapterMachineInfo rows"
            ),
            risk=(
                "an empty container is not inert: `isMachineCompleted` then answers "
                "false for every machine of every chapter, so no completed machine is "
                "swapped to its finished model (`replaceModel`) and every machine "
                "object stays in its interactive form.  If a producer for a role's "
                "real chapter-machine state is ever recovered, replace this policy in "
                "a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "max_decoder/analyze_chapter_machine_init.py (the recovered finish "
                "chain, the container's single writer and the handler identities, "
                "asserted against the shipped chunks)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C historical order unknown; A target-client registration and "
                "call-order contract"
            ),
            reason=(
                "historical login placement of the initChapterMachine* family is "
                "unknown, so the pair is sent with the other login-time container "
                "pushes, immediately AFTER the v7 chapter-item pair and therefore "
                "long before the S2C411 story snapshot and the S2C283 whose dispatch "
                "starts the main-city switch.  The client's own registration table "
                "fixes the choice of neighbour: the machine family (399-403) sits "
                "after the item family (389-393), and both containers are read in the "
                "same pass of the same finish callback.  The client's own code fixes "
                "the INTRA-block order and only that: the row reader indexes the "
                "global that only initChapterMachineStart creates.  S2C400/402 are the "
                "only members sent; 401 and 403 stay unregistered"
            ),
            compatibility_version=PROFILE_ID_V10,
            field_contract=(
                "terminal login segment order: 554 -> 27 -> 0 -> 487 -> 9(40) -> "
                "36 -> 37 -> 48 -> 35 -> 1014 -> 385 -> 387 -> 389 -> 391 -> "
                "400 -> 402 -> 1307 -> 1309 -> 411 -> 283"
            ),
            risk=(
                "if historical ordering evidence appears, replace it in a new "
                "profile version"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V10,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v9.role_values,
        sign_catalogs=v9.sign_catalogs,
        sign_state_template=v9.sign_state_template,
        scene_state_template=v9.scene_state_template,
        active_soul_rules=v9.active_soul_rules,
        parent_profile_id=PROFILE_ID_V9,
        server_time=v9.server_time,
        mystery_shop=v9.mystery_shop,
        story_start=v9.story_start,
        team_snapshot=v9.team_snapshot,
        recharge_activity=v9.recharge_activity,
        chapter_item_init=v9.chapter_item_init,
        experience_task_init=v9.experience_task_init,
        chapter_task_init=v9.chapter_task_init,
        chapter_machine_init=chapter_machine_init,
    )


@lru_cache(maxsize=1)
def get_profile_v10() -> CompatibilityProfile:
    """Return the immutable v10 profile (v9 + the ChapterMachineInit policy only)."""

    return _build_v10_profile()


def _battle_start_fields() -> tuple[BattleStartField, ...]:
    """Build the 19 per-position provenance records of the S2C 89 reply."""

    fields: list[BattleStartField] = []
    for (
        position,
        name,
        wire_type,
        value_origin,
        payload,
        contract,
        provenance_key,
    ) in BATTLE_START_FIELD_TABLE:
        source, origin, evidence_grade, reason, risk, replaceable = (
            BATTLE_START_PROVENANCE[provenance_key]
        )
        fields.append(BattleStartField(
            wire_position=position,
            symbolic_name=name,
            wire_type=wire_type,
            value_origin=value_origin,
            constant_value=(
                None if value_origin != BATTLE_START_VALUE_CONSTANT else payload
            ),
            echo_index=(
                int(payload) if value_origin == BATTLE_START_VALUE_ECHO else None
            ),
            provenance=_provenance(
                source=source,
                origin=origin,
                evidence_grade=evidence_grade,
                reason=reason,
                compatibility_version=PROFILE_ID_V11,
                field_contract=contract,
                risk=risk,
                replaceable=replaceable,
            ),
        ))
    return tuple(fields)


def _build_v11_profile() -> CompatibilityProfile:
    """Additive successor: v10 inherited state + the BattleStartReply policy."""

    v10 = get_profile_v10()
    battle_start_reply = BattleStartReplyPolicy(
        reply_format=BATTLE_START_REPLY_FORMAT,
        request_format=BATTLE_START_REQUEST_FORMAT,
        fields=_battle_start_fields(),
        historical_ordering=BATTLE_START_HISTORICAL_ORDERING,
        reply_provenance=_provenance(
            source=(
                "recovered pkgBattleProtoDef battleProtoSg2Cg row 0 "
                "`{NyyNNNNpppppppppaaa, startPveBattle, netEnterPveBattle}` via "
                "pkgScProtocolMgr.registerS2C -> pkgLockSendMgr.regLockProto, the "
                "handler in battle/net/pkgBattlePveFromSg, and the two branches "
                "TASK-0139 read statement by statement out of "
                "max_decoder/forensics_first_main_city_closure/"
                "luaScript__battle__pkgBattleMgr.lua (reports/"
                "task_0139_battle_drive_ownership.md, reports/"
                "task_0138_pve_battle_start_contract.md)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client wire and branch contract (id, format, handler and "
                "both target functions are read from the shipped bytecode); C "
                "historical values (no producer exists for any of them)"
            ),
            reason=(
                "the answer to a lock-send this archive has been leaving unanswered "
                "(D-129, Owner choice A): TASK-0139 proved that taking "
                "isUseSfSvr = false and the transfer flag = false is the ONLY "
                "branch by which the client plays the battle through on its own "
                "local driver, and the Owner approved a labelled compatibility "
                "choice for the values that have no producer.  Every position is "
                "labelled: the packaged constant is deterministic_recovered, the "
                "request's own values are request_echo, and the rest are "
                "synthetic_compatibility -- none is disguised as recovered "
                "(D-129: no invented fields)"
            ),
            compatibility_version=PROFILE_ID_V11,
            field_contract=(
                "S2C89 startPveBattle 'NyyNNNNpppppppppaaa': one reply per C2S69 "
                "netEnterPveBattle request, on the same connection"
            ),
            risk=(
                "this is the first compatibility policy in this archive that "
                "starts GAME CONTENT rather than repairing a nil container: the "
                "client will load a battle scene, run its own local combat and "
                "settle it.  The three visible choices (isUseSfSvr, position 3, "
                "selfGroup) and the seed are fixed constants, so every battle of "
                "chapter 1 is staged the same way until a producer is recovered.  "
                "The reply has never been observed on a device (S2C 89 is a "
                "derived id, TASK-0138 section A) and the whole policy is "
                "replaceable in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "recovered lock contract: pkgBattleProtoDef row 0 names "
                "`netEnterPveBattle` as the S2C side's lock partner, so the reply is "
                "sent on the connection that sent the request and nowhere else"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client request/answer contract; C historical ordering "
                "(the original server's own send order is unrecoverable)"
            ),
            reason=(
                "the reply is request-triggered, so there is no login-segment "
                "position to decide: exactly one S2C89 leaves for exactly one C2S69 "
                "arriving, on the same connection, which is the contract the "
                "client's own lock-send machinery requires"
            ),
            compatibility_version=PROFILE_ID_V11,
            field_contract=BATTLE_START_REPLY_TRIGGER,
            risk=(
                "none: a reply can only exist where a request did, and a session "
                "with no battle start sends nothing new"
            ),
            replaceable=True,
        ),
        introduced_in=PROFILE_ID_V11,
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V11,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v10.role_values,
        sign_catalogs=v10.sign_catalogs,
        sign_state_template=v10.sign_state_template,
        scene_state_template=v10.scene_state_template,
        active_soul_rules=v10.active_soul_rules,
        parent_profile_id=PROFILE_ID_V10,
        server_time=v10.server_time,
        mystery_shop=v10.mystery_shop,
        story_start=v10.story_start,
        team_snapshot=v10.team_snapshot,
        recharge_activity=v10.recharge_activity,
        chapter_item_init=v10.chapter_item_init,
        experience_task_init=v10.experience_task_init,
        chapter_task_init=v10.chapter_task_init,
        chapter_machine_init=v10.chapter_machine_init,
        battle_start_reply=battle_start_reply,
    )


@lru_cache(maxsize=1)
def get_profile_v11() -> CompatibilityProfile:
    """Return the immutable v11 profile (v10 + the BattleStartReply policy only)."""

    return _build_v11_profile()


def _battle_game_over_fields() -> tuple[BattleGameOverField, ...]:
    """Build the 2 per-position provenance records of the S2C 96 reply."""

    fields: list[BattleGameOverField] = []
    for (
        position,
        name,
        wire_type,
        value_origin,
        payload,
        contract,
        provenance_key,
    ) in BATTLE_GAME_OVER_FIELD_TABLE:
        source, origin, evidence_grade, reason, risk, replaceable = (
            BATTLE_GAME_OVER_PROVENANCE[provenance_key]
        )
        fields.append(BattleGameOverField(
            wire_position=position,
            symbolic_name=name,
            wire_type=wire_type,
            value_origin=value_origin,
            constant_value=payload,
            provenance=_provenance(
                source=source,
                origin=origin,
                evidence_grade=evidence_grade,
                reason=reason,
                compatibility_version=PROFILE_ID_V12,
                field_contract=contract,
                risk=risk,
                replaceable=replaceable,
            ),
        ))
    return tuple(fields)


def _build_v12_profile() -> CompatibilityProfile:
    """Additive successor: v11 inherited state + the BattleGameOverReply policy."""

    v11 = get_profile_v11()
    battle_game_over_reply = BattleGameOverReplyPolicy(
        reply_format=BATTLE_GAME_OVER_REPLY_FORMAT,
        request_format=BATTLE_GAME_OVER_REQUEST_FORMAT,
        fields=_battle_game_over_fields(),
        unlock_names=BATTLE_GAME_OVER_UNLOCK_NAMES,
        answered_request=BATTLE_GAME_OVER_REGISTERED_REQUEST,
        historical_ordering=BATTLE_GAME_OVER_HISTORICAL_ORDERING,
        reply_provenance=_provenance(
            source=(
                "recovered pkgBattleProtoDef battleProtoSg2Cg row 7 "
                "`{Na, battlePveGameOverEnd, battlePveGameOver, "
                "battlePveGameOver_svrVer}` via pkgScProtocolMgr.registerS2C -> "
                "pkgLockSendMgr.regLockProto; the client's only handler "
                "`s2c.battlePveGameOverEnd` in battle/net/pkgGameOverFromSg "
                "(source lines 65..88); the settlement screen it calls "
                "(`pkgGameOverDisplay` lines 303..346); and the device observation "
                "that the client really sends the lock-send (RUN-0048 "
                "server.log:439, decoded in revival_stub/tests/test_codec.py) -- "
                "see reports/task_0155_*.md"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client wire and consumer contract (id, format, lock pair "
                "and the handler's whole body are read from the shipped bytecode, "
                "and an exhaustive whole-APK scan bounds the consumer set); C "
                "historical values (no producer exists for either of them)"
            ),
            reason=(
                "this is the second half of the first battle: the client settles "
                "its own local battle and then asks the server to close it out with "
                "a lock-send (D-86), which this archive has been leaving unanswered "
                "so that the client's own 15 s LOCK_TIME timer killed the "
                "connection instead.  The reply may be minimal because the only "
                "consumer of this packet on the device is "
                "pkgGameOverFromSg's chapter branch, and that branch reads NEITHER "
                "value; every value that does exist is labelled "
                "synthetic_compatibility and none is disguised as recovered (D-129)"
            ),
            compatibility_version=PROFILE_ID_V12,
            field_contract=(
                "S2C96 battlePveGameOverEnd 'Na': one reply per C2S67 "
                "battlePveGameOver request, on the same connection"
            ),
            risk=(
                "low and bounded by the consumer verdict: the two branches that do "
                "read the second value (seven-day-happy, bit-rabbit) are not the "
                "branch this archive plays, and the first value has no reader at "
                "all.  What the answer DOES change is the client's lock state -- "
                "without it the client tears its own socket down after 15 s.  The "
                "reply has never been observed on a device (S2C 96 is a derived "
                "id) and the whole policy is replaceable in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "recovered lock contract: pkgBattleProtoDef rows 7 and 8 both name "
                "`battlePveGameOver` and `battlePveGameOver_svrVer` as the lock "
                "sends this S2C unlocks, so the reply is sent on the connection "
                "that sent the request and nowhere else"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client request/answer contract; C historical ordering "
                "(the original server's own send order is unrecoverable)"
            ),
            reason=(
                "the reply is request-triggered, so there is no login-segment "
                "position to decide: exactly one S2C96 leaves for exactly one C2S67 "
                "arriving, on the same connection, which is the contract the "
                "client's own lock-send machinery requires"
            ),
            compatibility_version=PROFILE_ID_V12,
            field_contract=BATTLE_GAME_OVER_REPLY_TRIGGER,
            risk=(
                "none: a reply can only exist where a request did, and a session "
                "that never settles a battle sends nothing new"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V12,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v11.role_values,
        sign_catalogs=v11.sign_catalogs,
        sign_state_template=v11.sign_state_template,
        scene_state_template=v11.scene_state_template,
        active_soul_rules=v11.active_soul_rules,
        parent_profile_id=PROFILE_ID_V11,
        server_time=v11.server_time,
        mystery_shop=v11.mystery_shop,
        story_start=v11.story_start,
        team_snapshot=v11.team_snapshot,
        recharge_activity=v11.recharge_activity,
        chapter_item_init=v11.chapter_item_init,
        experience_task_init=v11.experience_task_init,
        chapter_task_init=v11.chapter_task_init,
        chapter_machine_init=v11.chapter_machine_init,
        battle_start_reply=v11.battle_start_reply,
        battle_game_over_reply=battle_game_over_reply,
    )


@lru_cache(maxsize=1)
def get_profile_v12() -> CompatibilityProfile:
    """Return the immutable v12 profile (v11 + the BattleGameOverReply policy only)."""

    return _build_v12_profile()


def _prison_fight_fields() -> tuple[PrisonFightBossField, ...]:
    """Build the 11 per-position provenance records of the S2C 1370 snapshot."""

    fields: list[PrisonFightBossField] = []
    for (
        position,
        name,
        wire_type,
        value_origin,
        payload,
        contract,
        provenance_key,
    ) in PRISON_FIGHT_FIELD_TABLE:
        source, origin, evidence_grade, reason, risk, replaceable = (
            PRISON_FIGHT_PROVENANCE[provenance_key]
        )
        fields.append(PrisonFightBossField(
            wire_position=position,
            symbolic_name=name,
            wire_type=wire_type,
            value_origin=value_origin,
            constant_value=payload,
            provenance=_provenance(
                source=source,
                origin=origin,
                evidence_grade=evidence_grade,
                reason=reason,
                compatibility_version=PROFILE_ID_V13,
                field_contract=contract,
                risk=risk,
                replaceable=replaceable,
            ),
        ))
    return tuple(fields)


def _build_v13_profile() -> CompatibilityProfile:
    """Additive successor: v12 inherited state + the PrisonFightBossSnapshot policy."""

    v12 = get_profile_v12()
    prison_fight = PrisonFightBossSnapshotPolicy(
        snapshot_format=PRISON_FIGHT_SNAPSHOT_FORMAT,
        send_trigger=PRISON_FIGHT_SNAPSHOT_SEND_TRIGGER,
        fields=_prison_fight_fields(),
        historical_ordering=PRISON_FIGHT_SNAPSHOT_HISTORICAL_ORDERING,
        historical_values_recovered=False,
        replaceable=True,
        snapshot_provenance=_provenance(
            source=(
                "recovered pkgScProtocolMgr.lua:1850 "
                "registerS2C(\"NappNNppppp\", \"synPrisonFightBossToCg\"); "
                "the handler is the ONLY writer of e_tbBossData in the whole "
                "shipped luaScript tree (source lines 12..30, params=12); the "
                "D-02 registration simulation gives S2C 1370 with the two "
                "real-device anchors S2C 283 / S2C 1500 at the same +144 "
                "offset; RUN-0054 is the device traceback that proves the nil "
                "read blocks the chapter's return to the main city -- see "
                "reports/task_0166_prison_fight_panel_close_blocker.md"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client wire and consumer contract (id, format, handler "
                "and the sole writer are read from the shipped bytecode, and the "
                "two device anchors bracket the derived id); C historical values "
                "(no producer exists for any of the 11 values)"
            ),
            reason=(
                "this is the login-time state the client reads on EVERY "
                "PANEL_CLOSE: pkgMainUIMgr's onPanelClose calls "
                "pkgPrisonFightModel.checkActOn, which compares the unguarded "
                "e_tbBossData.startTime/endTime and raises when the model is the "
                "module's empty table.  The snapshot may be minimal because the "
                "only semantic this policy declares is 'the activity is not "
                "open': startTime=0 and endTime=0 make both LE comparisons "
                "false, which is also the client's own getStartEndTime "
                "closed-window convention.  No value is disguised as recovered "
                "(D-129/D-21) and the whole policy is replaceable"
            ),
            compatibility_version=PROFILE_ID_V13,
            field_contract=(
                "S2C1370 synPrisonFightBossToCg 'NappNNppppp': exactly one "
                "packet per completed login, immediately after the S2C411/412 "
                "story state and immediately before S2C283 returnSceneInfo"
            ),
            risk=(
                "low and bounded by the consumer: the model replaces "
                "e_tbBossData with an 11-field table and dispatches no event, so "
                "the packet cannot open, close or redraw any UI.  What it DOES "
                "change is the PANEL_CLOSE path: checkActOn returns false "
                "instead of raising, which is exactly the edge this policy "
                "restores.  The packet has never been observed on a device "
                "(S2C 1370 is a derived id) and the whole policy is replaceable"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "recovered login order: the client never requests this state "
                "(C2S 672 sendRequestPrisonFightData is sent only from the "
                "panel's own entry), so the snapshot must precede the first "
                "PANEL_CLOSE; the S2C283 below is the packet whose dispatch "
                "starts the main-city switch, making it the latest position "
                "that provably precedes the main UI"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client trigger contract (no write request at login, an "
                "unguarded read on every PANEL_CLOSE); C historical ordering "
                "(the original server's own send order is unrecoverable)"
            ),
            reason=(
                "the packet is a login snapshot, not a reply: it is sent exactly "
                "once per completed login, after the S2C411/412 story state and "
                "before S2C283, so the client model exists before the first "
                "panel can close.  This is an explicit synthetic compatibility "
                "ordering, never a claim about the historical packet stream"
            ),
            compatibility_version=PROFILE_ID_V13,
            field_contract=PRISON_FIGHT_SNAPSHOT_SEND_TRIGGER,
            risk=(
                "none: a session that never completes its login sync sends "
                "nothing new, and a v1-v12 server sends byte-for-byte what it "
                "sent before"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V13,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v12.role_values,
        sign_catalogs=v12.sign_catalogs,
        sign_state_template=v12.sign_state_template,
        scene_state_template=v12.scene_state_template,
        active_soul_rules=v12.active_soul_rules,
        parent_profile_id=PROFILE_ID_V12,
        server_time=v12.server_time,
        mystery_shop=v12.mystery_shop,
        story_start=v12.story_start,
        team_snapshot=v12.team_snapshot,
        recharge_activity=v12.recharge_activity,
        chapter_item_init=v12.chapter_item_init,
        experience_task_init=v12.experience_task_init,
        chapter_task_init=v12.chapter_task_init,
        chapter_machine_init=v12.chapter_machine_init,
        battle_start_reply=v12.battle_start_reply,
        battle_game_over_reply=v12.battle_game_over_reply,
        prison_fight=prison_fight,
    )


@lru_cache(maxsize=1)
def get_profile_v13() -> CompatibilityProfile:
    """Return the immutable v13 profile (v12 + the PrisonFightBossSnapshot policy only)."""

    return _build_v13_profile()


def _build_v14_profile() -> CompatibilityProfile:
    """Additive successor: v13 inherited state + the ChapterFightReplay policy."""

    v13 = get_profile_v13()
    chapter_fight_replay = ChapterFightReplayPolicy(
        row_format=CHAPTER_FIGHT_REPLAY_ROW_FORMAT,
        container_policy=CHAPTER_FIGHT_REPLAY_CONTAINER_POLICY,
        historical_ordering=CHAPTER_FIGHT_REPLAY_HISTORICAL_ORDERING,
        historical_values_recovered=False,
        replaceable=True,
        row_provenance=_provenance(
            source=(
                "recovered pkgScProtocolMgr.lua:504 registerS2C(\"ppp\", "
                "\"initChapterItemInfo\"); its handler "
                "pkgChapterInfoMgr.initChapterItemInfo (source lines 52-56) "
                "writes le_tbChapterItemInfo[dChapterId][dType][dId] = true; "
                "the chapter-map reader isItemFightOver (source lines 208-221) "
                "reads le_tbChapterItemInfo[dChapterId][tbInfo.type]"
                "[itemFightId(dX,dY)], and pkgGuankaMapConstMgr.itemFightId "
                "(bytecode PROTO 0.1, source lines 172-174) returns dX; the "
                "fight node type is the shipped MAP_TYPE_FIGHT = 10 "
                "(pkgGuankaMapConstMgr source line 28) -- see "
                "reports/task_0174_chapter_fight_progress_persistence.md"
            ),
            origin="deterministic_recovered",
            evidence_grade=(
                "A target-client wire and consumer contract (id, format, writer "
                "and reader are read from the shipped bytecode); B shipped "
                "constant (MAP_TYPE_FIGHT = 10)"
            ),
            reason=(
                "the row is the client's own completed-item triple and nothing "
                "else: the same writer that RUN-0029's unguarded reader indexes. "
                "It is emitted only for a battle this server observed start, "
                "settle and answer on ONE connection, so the row asserts a fact "
                "the wire carried rather than a guessed progression"
            ),
            compatibility_version=PROFILE_ID_V14,
            field_contract=(
                "S2C390 initChapterItemInfo 'ppp' = (dChapterId, dType, dId), "
                "one packet per observed settlement, between S2C389 "
                "initChapterItemStart and S2C391 initChapterItemEnd"
            ),
            risk=(
                "bounded by the client's own reader: a row can only make "
                "isItemFightOver true for one (chapter, map index), which "
                "deactivates that one scene object and adds one unit to the "
                "chapter's own progress count.  It cannot unlock chapter 2 "
                "(isChapterPass also needs the EXIT node and >=20%), and it "
                "invents no reward, drop or time.  The settlement's win/loss "
                "verdict is not decoded (see the class docstring)"
            ),
            replaceable=True,
        ),
        value_provenance=_provenance(
            source=(
                "the client's own C2S 69 netEnterPveBattle request "
                "(pkgBattleMgr.c2sStartPveBattle: p1 = tbCfg.fightId, "
                "p2 = dIndex, p4 = dChapterId) -- replayed only as the row key "
                "and the chapter-map index"
            ),
            origin="request_echo",
            evidence_grade=(
                "A target-client request contract (the three fields are the "
                "client's own call arguments, read from pkgChapterEventHandle "
                "line 255 and pkgBattleMgr lines 53-90)"
            ),
            reason=(
                "no server producer exists for these values, and the client "
                "already named them when it started the battle; echoing them "
                "back is the strongest available source and fabricates nothing"
            ),
            compatibility_version=PROFILE_ID_V14,
            field_contract=(
                "S2C390 dChapterId and dId are exactly the C2S69 p4 and p2 of "
                "the same connection"
            ),
            risk=(
                "a client that sent an index it does not itself read back would "
                "produce a row that matches nothing; that is a no-op in the "
                "client's own map, not a fabricated completion"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "the client's own code forces the position: "
                "pkgChapterInfoMgr.initChapterItemInfo's first statement "
                "indexes the global that only S2C389 initChapterItemStart "
                "creates, and initChapterItemEnd is that block's no-op end "
                "handler (source lines 48-59)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client ordering contract (the row reader requires the "
                "container the start packet creates); C historical ordering "
                "(the original server's own row order and send position are "
                "unrecoverable)"
            ),
            reason=(
                "the rows must ride between the client's own start/end packets; "
                "that position is an explicit compatibility ordering, never a "
                "claim about the historical packet stream"
            ),
            compatibility_version=PROFILE_ID_V14,
            field_contract=(
                "S2C389, then N x S2C390, then S2C391, in the login segment "
                "before S2C283"
            ),
            risk=(
                "none on an archive with no observed settlement: zero rows "
                "means the block is byte-for-byte the v13 pair"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V14,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v13.role_values,
        sign_catalogs=v13.sign_catalogs,
        sign_state_template=v13.sign_state_template,
        scene_state_template=v13.scene_state_template,
        active_soul_rules=v13.active_soul_rules,
        parent_profile_id=PROFILE_ID_V13,
        server_time=v13.server_time,
        mystery_shop=v13.mystery_shop,
        story_start=v13.story_start,
        team_snapshot=v13.team_snapshot,
        recharge_activity=v13.recharge_activity,
        chapter_item_init=v13.chapter_item_init,
        experience_task_init=v13.experience_task_init,
        chapter_task_init=v13.chapter_task_init,
        chapter_machine_init=v13.chapter_machine_init,
        battle_start_reply=v13.battle_start_reply,
        battle_game_over_reply=v13.battle_game_over_reply,
        prison_fight=v13.prison_fight,
        chapter_fight_replay=chapter_fight_replay,
    )


@lru_cache(maxsize=1)
def get_profile_v14() -> CompatibilityProfile:
    """Return the immutable v14 profile (v13 + the ChapterFightReplay policy only)."""

    return _build_v14_profile()


def _build_v15_profile() -> CompatibilityProfile:
    """Additive successor: v14 inherited state + the DrawCardUnlock policy."""

    v14 = get_profile_v14()
    drawcard_unlock = DrawCardUnlockPolicy(
        chapter_id=DRAWCARD_UNLOCK_CHAPTER_ID,
        completed_task_id=DRAWCARD_UNLOCK_COMPLETED_ID,
        row_format=DRAWCARD_UNLOCK_FORMAT,
        container_policy=DRAWCARD_UNLOCK_CONTAINER_POLICY,
        historical_ordering=DRAWCARD_UNLOCK_HISTORICAL_ORDERING,
        historical_values_recovered=False,
        replaceable=True,
        row_provenance=_provenance(
            source=(
                "recovered registration pkgScProtocolMgr.lua:500 "
                "registerS2C(\"pp\", \"initChapterTaskInfo\"); its handler "
                "socket/nc/pkgGuankaMapNcMgr PROTO 0.9 calls "
                "pkgChapterTaskMgr.initChapterTaskInfo(dChapterId, dId) "
                "(source lines 25-28), which stores "
                "le_tbChapterTaskInfo[dChapterId][dId] = true; the drawcard "
                "gate is decompiled utility/pkgSystemFuncMgr.lua lines 245-246 "
                "(isTaskCompleted(1001, 1) or dChapter >= 1); the reader "
                "pkgChapterTaskMgr.isTaskCompleted (lines 66-70) asks "
                "GetTaskCompletedId(1001), and pkgGuankaMapConstMgr PROTO 0.6 "
                "(source lines 215-217) is dTaskId + 10000000 -- see "
                "reports/task_0180_drawcard_main_entry.md and "
                "max_decoder/analyze_drawcard_entry.py"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client wire and consumer contract (id, format, "
                "writer, gate and completed-id helper are read from the "
                "shipped bytecode); C the historical row value (no observed "
                "initChapterTaskInfo row exists in this archive)"
            ),
            reason=(
                "the 祈灵 button's own gate reads exactly one completed "
                "chapter-task key, isTaskCompleted(1001, 1) -> "
                "le_tbChapterTaskInfo[1][10001001], and the permanent QA role "
                "has not passed chapter 1 (1/11, no EXIT), so the gate's "
                "other arm (dChapter >= 1) is false.  The row is a labelled "
                "synthetic state, the minimum that makes the client's own gate "
                "true; it does not claim recovered progress and it unlocks "
                "nothing else"
            ),
            compatibility_version=PROFILE_ID_V15,
            field_contract=(
                "S2C386 initChapterTaskInfo 'pp' = (dChapterId=1, "
                "dId=10001001), one packet, sent once per login after the "
                "v9 385/387 pair and before the v7 389/391 pair"
            ),
            risk=(
                "the row makes isTaskCompleted(1001, ...) true for chapter 1, "
                "which is the only consumer of this key in the shipped "
                "luaScript tree; it does not grant chapter progress, items, "
                "rewards, draw results or any other task id.  If a real "
                "chapter-task producer is ever recovered, replace this policy "
                "in a new profile version"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "the client's own handler requires the container only S2C385 "
                "initChapterTaskStart creates (pkgChapterTaskMgr lines 25-28); "
                "S2C387 initChapterTaskEnd is that block's no-op end handler "
                "(lines 30-32), so the row is placed AFTER the v9 pair and "
                "BEFORE the v7 chapter-item pair"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client ordering contract (the row reader requires "
                "the start packet's container); C historical ordering (the "
                "original server's task-row position is unrecoverable)"
            ),
            reason=(
                "the v9 pair keeps its byte-for-byte positions and order; the "
                "new row is additive immediately after it and still long "
                "before S2C283, whose dispatch starts the main-city switch "
                "and therefore before pkgMainUIMgr.updateBtn reads the gate"
            ),
            compatibility_version=PROFILE_ID_V15,
            field_contract=(
                "385 -> 387 -> 386 -> 389 -> 391 ... -> 411 -> 283; no "
                "existing packet id, relative order or bytes change"
            ),
            risk=(
                "an empty archive-independent row would still be sent; the "
                "policy has no observed-data gate because its single value is "
                "the gate itself, not a replay"
            ),
            replaceable=True,
        ),
    )
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V15,
            target_client="cn-0.1.20.1",
            purpose=COMPATIBILITY_GOAL,
            status="compatibility-not-original",
        ),
        role_values=v14.role_values,
        sign_catalogs=v14.sign_catalogs,
        sign_state_template=v14.sign_state_template,
        scene_state_template=v14.scene_state_template,
        active_soul_rules=v14.active_soul_rules,
        parent_profile_id=PROFILE_ID_V14,
        server_time=v14.server_time,
        mystery_shop=v14.mystery_shop,
        story_start=v14.story_start,
        team_snapshot=v14.team_snapshot,
        recharge_activity=v14.recharge_activity,
        chapter_item_init=v14.chapter_item_init,
        experience_task_init=v14.experience_task_init,
        chapter_task_init=v14.chapter_task_init,
        chapter_machine_init=v14.chapter_machine_init,
        battle_start_reply=v14.battle_start_reply,
        battle_game_over_reply=v14.battle_game_over_reply,
        prison_fight=v14.prison_fight,
        chapter_fight_replay=v14.chapter_fight_replay,
        drawcard_unlock=drawcard_unlock,
    )


@lru_cache(maxsize=1)
def get_profile_v15() -> CompatibilityProfile:
    """Return the immutable v15 profile (v14 + the DrawCardUnlock policy only)."""

    return _build_v15_profile()


@lru_cache(maxsize=1)
def _build_v16_profile() -> CompatibilityProfile:
    """V15 inherited byte for byte + one S2C555 soul-skill selection value.

    The team card view reads `pkgTalentMgr.getSoulSkillId() > 0` on its first
    open; without S2C555 that global is nil and the comparison errors before
    `updateTeamData` fills the editor's selection table.  V16 states the empty
    selection (0, the client's own "none" sentinel) immediately after S2C554.
    No policy object is added: the packet has no per-role state to replay.
    """

    v15 = get_profile_v15()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V16,
            target_client=v15.metadata.target_client,
            purpose=v15.metadata.purpose,
            status=v15.metadata.status,
        ),
        role_values=v15.role_values,
        sign_catalogs=v15.sign_catalogs,
        sign_state_template=v15.sign_state_template,
        scene_state_template=v15.scene_state_template,
        active_soul_rules=v15.active_soul_rules,
        parent_profile_id=PROFILE_ID_V15,
        server_time=v15.server_time,
        mystery_shop=v15.mystery_shop,
        story_start=v15.story_start,
        team_snapshot=v15.team_snapshot,
        recharge_activity=v15.recharge_activity,
        chapter_item_init=v15.chapter_item_init,
        experience_task_init=v15.experience_task_init,
        chapter_task_init=v15.chapter_task_init,
        chapter_machine_init=v15.chapter_machine_init,
        battle_start_reply=v15.battle_start_reply,
        battle_game_over_reply=v15.battle_game_over_reply,
        prison_fight=v15.prison_fight,
        chapter_fight_replay=v15.chapter_fight_replay,
        drawcard_unlock=v15.drawcard_unlock,
    )


@lru_cache(maxsize=1)
def get_profile_v16() -> CompatibilityProfile:
    """Return the immutable v16 profile (v15 + the soul-skill selection value)."""

    return _build_v16_profile()


def _build_v17_battle_start_policy() -> BattleStartReplyPolicy:
    """The v11 S2C 89 policy with position 7 minted per reply (TASK-0182).

    Every other position keeps its v11 field object -- same constant, echo or
    counter, same provenance record -- so the 18 unchanged values cannot drift
    while only the duplicate-packet counter moves to v17.
    """

    v16_policy = get_profile_v16().battle_start_reply
    assert v16_policy is not None
    source, origin, evidence_grade, reason, risk, replaceable = (
        BATTLE_START_PROVENANCE["sync_count_counter"]
    )
    fields: list[BattleStartField] = []
    for field in v16_policy.fields:
        if field.wire_position != BATTLE_START_SYNC_COUNT_POSITION:
            fields.append(field)
            continue
        fields.append(BattleStartField(
            wire_position=BATTLE_START_SYNC_COUNT_POSITION,
            symbolic_name=field.symbolic_name,
            wire_type=field.wire_type,
            value_origin=BATTLE_START_VALUE_COUNTER,
            constant_value=None,
            echo_index=None,
            provenance=_provenance(
                source=source,
                origin=origin,
                evidence_grade=evidence_grade,
                reason=reason,
                compatibility_version=PROFILE_ID_V17,
                field_contract=(
                    "S2C89 position 7 'N' dSyncCount: per-reply counter.  The "
                    "client compares it only with the value stored from the "
                    "previous reply (pkgBattleMgr.startPveBattle lines 179-182); "
                    "it never compares it with C2S 69"
                ),
                risk=risk,
                replaceable=replaceable,
            ),
        ))
    return replace(
        v16_policy,
        fields=tuple(fields),
        introduced_in=PROFILE_ID_V17,
    )


@lru_cache(maxsize=1)
def _build_v17_profile() -> CompatibilityProfile:
    """V16 inherited byte for byte + the per-reply S2C 89 sync counter.

    Only `battle_start_reply` is replaced.  No login policy changes, so the v17
    login stream is the v16 stream packet for packet.
    """

    v16 = get_profile_v16()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V17,
            target_client=v16.metadata.target_client,
            purpose=v16.metadata.purpose,
            status=v16.metadata.status,
        ),
        role_values=v16.role_values,
        sign_catalogs=v16.sign_catalogs,
        sign_state_template=v16.sign_state_template,
        scene_state_template=v16.scene_state_template,
        active_soul_rules=v16.active_soul_rules,
        parent_profile_id=PROFILE_ID_V16,
        server_time=v16.server_time,
        mystery_shop=v16.mystery_shop,
        story_start=v16.story_start,
        team_snapshot=v16.team_snapshot,
        recharge_activity=v16.recharge_activity,
        chapter_item_init=v16.chapter_item_init,
        experience_task_init=v16.experience_task_init,
        chapter_task_init=v16.chapter_task_init,
        chapter_machine_init=v16.chapter_machine_init,
        battle_start_reply=_build_v17_battle_start_policy(),
        battle_game_over_reply=v16.battle_game_over_reply,
        prison_fight=v16.prison_fight,
        chapter_fight_replay=v16.chapter_fight_replay,
        drawcard_unlock=v16.drawcard_unlock,
    )


@lru_cache(maxsize=1)
def get_profile_v17() -> CompatibilityProfile:
    """Return the immutable v17 profile (v16 + the per-reply sync counter)."""

    return _build_v17_profile()


def _build_v18_chapter_box_policy() -> ChapterBoxReplayPolicy:
    """The v18 login replay of stored chapter milestone chests."""

    return ChapterBoxReplayPolicy(
        row_format=CHAPTER_BOX_REPLAY_ROW_FORMAT,
        container_policy=CHAPTER_BOX_REPLAY_CONTAINER_POLICY,
        historical_ordering=CHAPTER_BOX_REPLAY_HISTORICAL_ORDERING,
        row_provenance=_provenance(
            source=(
                "recovered pkgScProtocolMgr.lua:495-498: "
                "registerS2C(\"\", \"initChapterBoxStart\"), "
                "registerS2C(\"pp\", \"initChapterBoxInfo\"), "
                "registerS2C(\"\", \"initChapterBoxEnd\"), "
                "registerS2C(\"ppp\", \"chapterAcceptBox\", "
                "\"chapterAcceptBox\"); the client's handler "
                "socket/nc/pkgGuankaMapNcMgr PROTO 0.0..0.2 runs "
                "pkgChapterInfoMgr.initChapterBoxStart() (le_tbChapterBox = {}, "
                "source lines 28-30) and initChapterBoxInfo(dChapterId, "
                "dBoxIndex) (source lines 32-35); isChapterBoxAccepted (source "
                "lines 20-26) reads that table -- see "
                "reports/task_0202_chapter_box_reward.md"
            ),
            origin="deterministic_recovered",
            evidence_grade=(
                "A target-client wire and consumer contract (ids, formats, "
                "handler and reader are read from the shipped bytecode)"
            ),
            reason=(
                "the login block restores exactly the accepted flag the client "
                "itself set when this server answered its C2S 222; without it an "
                "opened chest is drawn unopened and offered a second time"
            ),
            compatibility_version=PROFILE_ID_V18,
            field_contract=(
                "S2C381 initChapterBoxStart '', then one S2C382 "
                "initChapterBoxInfo 'pp' per stored accept, then S2C383 "
                "initChapterBoxEnd ''; zero packets when the archive holds none"
            ),
            risk=(
                "bounded by the client's own reader: a row can only make "
                "isChapterBoxAccepted true for one stored (chapter, box) pair; "
                "it grants nothing and adds no progress"
            ),
            replaceable=True,
        ),
        value_provenance=_provenance(
            source=(
                "this archive's own chapter_box_accepts rows, written by the "
                "one transaction that answered a real C2S 222 after validating "
                "the shipped guankaChapter/guankaReward row and the client's "
                "own progress records"
            ),
            origin="deterministic_recovered",
            evidence_grade=(
                "A server-recorded fact (the row exists only because the client "
                "requested that chest on one connection); B shipped config for "
                "the reward id"
            ),
            reason=(
                "the pair is the client's own (dChapterId, dBoxIndex) request "
                "echoed back from storage; no chest the role has not opened is "
                "ever sent"
            ),
            compatibility_version=PROFILE_ID_V18,
            field_contract=(
                "S2C382 dChapterId/dBoxIndex are exactly one stored "
                "chapter_box_accepts row of the logging-in role"
            ),
            risk=(
                "a corrupted stored pair would mark an unopened chest accepted; "
                "the table is only written by the validated accept transaction "
                "and has a (account, role, chapter, box) primary key"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "the client's own registration order (box family 381-384 "
                "immediately before task family 385-388) and the forced "
                "start/info/end order of pkgGuankaMapNcMgr PROTO 0.0..0.2"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client ordering contract (Info indexes the table "
                "Start creates); C historical ordering (the original server's "
                "send position is unrecoverable)"
            ),
            reason=(
                "the block is sent immediately before the chapter-task family, "
                "an explicit compatibility ordering, never a claim about the "
                "historical packet stream"
            ),
            compatibility_version=PROFILE_ID_V18,
            field_contract=(
                "S2C381, N x S2C382, S2C383, immediately before S2C385/387 and "
                "still before the S2C283 main-city switch"
            ),
            risk=(
                "none on an archive with no accepted chest: zero packets means "
                "the v18 login stream is byte-for-byte the v17 stream"
            ),
            replaceable=True,
        ),
    )


@lru_cache(maxsize=1)
def _build_v18_profile() -> CompatibilityProfile:
    """V17 inherited byte for byte + the conditional chapter-box login block.

    Only `chapter_box_replay` is added.  Every other policy object is the v17
    object, and the policy emits zero packets when the archive has no accepted
    chest, so the default login stream is the v17 stream packet for packet.
    """

    v17 = get_profile_v17()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V18,
            target_client=v17.metadata.target_client,
            purpose=v17.metadata.purpose,
            status=v17.metadata.status,
        ),
        role_values=v17.role_values,
        sign_catalogs=v17.sign_catalogs,
        sign_state_template=v17.sign_state_template,
        scene_state_template=v17.scene_state_template,
        active_soul_rules=v17.active_soul_rules,
        parent_profile_id=PROFILE_ID_V17,
        server_time=v17.server_time,
        mystery_shop=v17.mystery_shop,
        story_start=v17.story_start,
        team_snapshot=v17.team_snapshot,
        recharge_activity=v17.recharge_activity,
        chapter_item_init=v17.chapter_item_init,
        experience_task_init=v17.experience_task_init,
        chapter_task_init=v17.chapter_task_init,
        chapter_machine_init=v17.chapter_machine_init,
        battle_start_reply=v17.battle_start_reply,
        battle_game_over_reply=v17.battle_game_over_reply,
        prison_fight=v17.prison_fight,
        chapter_fight_replay=v17.chapter_fight_replay,
        drawcard_unlock=v17.drawcard_unlock,
        chapter_box_replay=_build_v18_chapter_box_policy(),
    )


@lru_cache(maxsize=1)
def get_profile_v18() -> CompatibilityProfile:
    """Return the immutable v18 profile (v17 + the chapter-box replay only)."""

    return _build_v18_profile()


def _build_v19_battle_start_policy() -> BattleStartReplyPolicy:
    """The v17 S2C 89 policy with positions 5/6 made type-aware (TASK-0237).

    Only those two positions are replaced.  Positions 1/2/3/4/7..19 are the very
    same field objects the v17/v18 policy carries, so the 86 request still
    produces the identical 19-value tuple.

    * position 5 echoes the request's own `pveOrPvpType`, because the client's
      `pkgPveEnterData.initData` dispatches the whole battle initializer on it
      (deterministic_recovered client contract).  A 73 request must therefore be
      answered with 73 or the client runs `initChapterMapData`.
    * position 6 derives `3278 + replied_type * 1` from the same request slot.
      The base and the formula are deterministic_recovered; `k = 1` is a
      labelled compatibility choice (`synthetic_compatibility`), never a
      historical server value.
    """

    v18_policy = get_profile_v18().battle_start_reply
    assert v18_policy is not None
    fields = list(v18_policy.fields)

    fields[BATTLE_START_TYPE_POSITION - 1] = BattleStartField(
        wire_position=BATTLE_START_TYPE_POSITION,
        symbolic_name="pveOrPvpType",
        wire_type="N",
        value_origin=BATTLE_START_VALUE_ECHO,
        constant_value=None,
        echo_index=BATTLE_START_REQUEST_TYPE_INDEX,
        provenance=_provenance(
            source=(
                "recovered pkgBattlePveFromSg PROTO 0.0 (the SETTABLE binding "
                "pveOrPvpType <- R5) and pkgPveEnterData PROTO 0.40 initData "
                "line 866, which dispatches on that value (73 -> "
                "initLockTowerData, 86 -> initChapterMapData); TASK-0236 / "
                "D-192 sections A/B"
            ),
            origin=BATTLE_START_VALUE_ECHO,
            evidence_grade=(
                "A target-client wire and dispatch contract "
                "(deterministic_recovered: the client's own handler and "
                "initializer branch are read out of the shipped bytecode); the "
                "echoed value is the client's own C2S 69 slot"
            ),
            reason=(
                "the reply's position 5 selects the battle initializer, so it "
                "must repeat the type the client asked for.  Echoing the request "
                "is the only value that cannot disagree with the client's own "
                "request; a constant would start the wrong initializer for every "
                "type but 86"
            ),
            compatibility_version=PROFILE_ID_V19,
            field_contract=(
                "S2C89 position 5 'N' pveOrPvpType <- C2S 69 position 3 'N' "
                "pveOrPvpType; pkgPveEnterData.initData dispatches on it"
            ),
            risk=(
                "an unsupported type now reaches its own client branch instead "
                "of the frozen 86 branch; only the dev-babel-main session "
                "(pveOrPvpType 73) selects this policy, and 86 stays byte for "
                "byte"
            ),
            replaceable=True,
        ),
    )

    fields[BATTLE_START_SEED_POSITION - 1] = BattleStartField(
        wire_position=BATTLE_START_SEED_POSITION,
        symbolic_name="dRawRandomSeed",
        wire_type="N",
        value_origin=BATTLE_START_VALUE_REQUEST_SEED,
        constant_value=None,
        echo_index=None,
        seed_request_index=BATTLE_START_REQUEST_TYPE_INDEX,
        provenance=_provenance(
            source=(
                "recovered pkgBattlePveFromSg lines 22-27: randomSeed = "
                "(dRawRandomSeed - 3278) / fmod(pveOrPvpType, 500); TASK-0236 / "
                "D-192 section B"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client formula (deterministic_recovered); the choice "
                "of k = 1 is a labelled compatibility choice "
                "(synthetic_compatibility), not a historical server value"
            ),
            reason=(
                "the documented base 3278 and the k = 1 multiplier only yield an "
                "integer randomSeed when the dividend uses the SAME type the "
                "reply returns; deriving it from the echoed type keeps 86 -> "
                "3364 unchanged and gives 73 -> 3351 instead of the v18 "
                "constant's non-integral 1.178"
            ),
            compatibility_version=PROFILE_ID_V19,
            field_contract=(
                "S2C89 position 6 'N' dRawRandomSeed = 3278 + replied "
                "pveOrPvpType * 1"
            ),
            risk=(
                "whether the client tolerates a non-integral randomSeed is "
                "UNKNOWN (TASK-0236 U-6); this policy removes that question for "
                "73 without claiming the original server's seed value"
            ),
            replaceable=True,
        ),
    )

    return replace(
        v18_policy,
        fields=tuple(fields),
        introduced_in=PROFILE_ID_V19,
    )


@lru_cache(maxsize=1)
def _build_v19_profile() -> CompatibilityProfile:
    """V18 inherited byte for byte + the type-aware S2C 89 reply.

    Only `battle_start_reply` is replaced.  Every login-time policy object is
    the v18 object itself, so a v19 login stream is the v18 stream packet for
    packet.  The profile is NEVER selected by `get_profile()`: the server hands
    it to a session only when the dev-babel-main overlay is applied.
    """

    v18 = get_profile_v18()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V19,
            target_client=v18.metadata.target_client,
            purpose=v18.metadata.purpose,
            status=v18.metadata.status,
        ),
        role_values=v18.role_values,
        sign_catalogs=v18.sign_catalogs,
        sign_state_template=v18.sign_state_template,
        scene_state_template=v18.scene_state_template,
        active_soul_rules=v18.active_soul_rules,
        parent_profile_id=PROFILE_ID_V18,
        server_time=v18.server_time,
        mystery_shop=v18.mystery_shop,
        story_start=v18.story_start,
        team_snapshot=v18.team_snapshot,
        recharge_activity=v18.recharge_activity,
        chapter_item_init=v18.chapter_item_init,
        experience_task_init=v18.experience_task_init,
        chapter_task_init=v18.chapter_task_init,
        chapter_machine_init=v18.chapter_machine_init,
        battle_start_reply=_build_v19_battle_start_policy(),
        battle_game_over_reply=v18.battle_game_over_reply,
        prison_fight=v18.prison_fight,
        chapter_fight_replay=v18.chapter_fight_replay,
        drawcard_unlock=v18.drawcard_unlock,
        chapter_box_replay=v18.chapter_box_replay,
    )


@lru_cache(maxsize=1)
def get_profile_v19() -> CompatibilityProfile:
    """Return the immutable v19 profile (v18 + the type-aware S2C 89 reply).

    Opt-in only: `get_profile()` keeps returning v18, and the server selects
    v19 for a `dev-babel-main` session whose dev-play overlay is applied.
    """

    return _build_v19_profile()


def _build_v20_babel_settlement_push_policy() -> BabelSettlementPushPolicy:
    """The one S2C 580 push that follows a paired 73 victory (TASK-0238).

    The push itself is new compatibility state; every value it carries comes
    from the session's validated dev-play overlay, never from the profile.  Both
    provenances below are `synthetic_compatibility`: the position (after S2C 96)
    and the choice to answer with a push at all are local decisions, while the
    pairing rule and the S2C580-only state channel are `deterministic_recovered`
    (TASK-0236/D-192) and are recorded by the caller, not by this object.
    """

    return BabelSettlementPushPolicy(
        wire_id=BABEL_SETTLEMENT_PUSH_WIRE_ID,
        reply_format=BABEL_SETTLEMENT_PUSH_FORMAT,
        field_names=BABEL_SETTLEMENT_PUSH_FIELD_NAMES,
        send_trigger=BABEL_SETTLEMENT_PUSH_SEND_TRIGGER,
        trigger_request=BABEL_SETTLEMENT_PUSH_TRIGGER_REQUEST,
        value_source=BABEL_SETTLEMENT_PUSH_VALUE_SOURCE,
        max_per_settlement=BABEL_SETTLEMENT_PUSH_MAX_PER_SETTLEMENT,
        historical_ordering=BABEL_SETTLEMENT_PUSH_HISTORICAL_ORDERING,
        value_provenance=_provenance(
            source=(
                "the only writer of floor/state/failCount/realm is the S2C 580 "
                "handler pkgBabelNcMgr (shipped-chunk scan: setCurFloor appears "
                "only in pkgBabelMod and pkgBabelNcMgr); the values themselves "
                "are the validated dev_play_babel overlay of this session "
                "(revival_stub/dev_play_babel.py), TASK-0231/D-187 and "
                "TASK-0236/D-192"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client state channel (deterministic_recovered: S2C 580 "
                "is the only writer); C value source (the dev-play overlay is an "
                "explicit local choice, not a historical server record)"
            ),
            reason=(
                "after a paired 73 victory the tower UI can only advance from "
                "S2C 580.  The original server's post-win floor/state/failCount "
                "values are its own state machine and are UNKNOWN (TASK-0236 "
                "U-3/U-5), so the honest push replays the state this session "
                "already announced at login instead of inventing a floor advance"
            ),
            compatibility_version=PROFILE_ID_V20,
            field_contract=(
                "S2C580 'NNNNN' = (floor, field2 UNKNOWN, state, failCount, "
                "realm), exactly the dev overlay's babel_info; pkgBabelNcMgr "
                "calls setCurFloor(R1,R3,R4,R5)"
            ),
            risk=(
                "the floor never advances on a dev-play victory -- a labelled "
                "dev-play limitation, not a claim about the original server.  "
                "No reward, no charge, no persistence"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "TASK-0236/D-192 section D: the client processes S2C 96 and "
                "S2C 580 in either order; the original server's order is not "
                "recoverable.  This slice sends the push after the existing "
                "S2C 96 reply on the same connection"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "A target-client read contract (both orders are processed); "
                "C ordering decision"
            ),
            reason=(
                "S2C 96 is the lock-send unlock and must not be delayed; the "
                "state push follows it once, on the same connection"
            ),
            compatibility_version=PROFILE_ID_V20,
            field_contract=(
                "C2S 67 (pveOrPvpType=73, GAMEOVERRESULT_WIN) -> S2C 96 -> "
                "S2C 580, at most one S2C 580 per paired victory"
            ),
            risk=(
                "an unsolicited S2C 580 is processed by the client's own "
                "handler; if the original order mattered visually it is "
                "unobservable here and remains UNKNOWN (U-5)"
            ),
            replaceable=True,
        ),
        introduced_in=PROFILE_ID_V20,
    )


@lru_cache(maxsize=1)
def _build_v20_profile() -> CompatibilityProfile:
    """V19 inherited byte for byte + the post-settlement S2C 580 push.

    Only `babel_settlement_push` is added.  `battle_start_reply` is the v19
    object itself, and every login-time policy is the same object v18/v19 carry,
    so a v20 login stream carries exactly the v19 login packets.  The profile is
    NEVER selected by `get_profile()`: the server hands it to a session only
    when the dev-babel-main overlay is applied.
    """

    v19 = get_profile_v19()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V20,
            target_client=v19.metadata.target_client,
            purpose=v19.metadata.purpose,
            status=v19.metadata.status,
        ),
        role_values=v19.role_values,
        sign_catalogs=v19.sign_catalogs,
        sign_state_template=v19.sign_state_template,
        scene_state_template=v19.scene_state_template,
        active_soul_rules=v19.active_soul_rules,
        parent_profile_id=PROFILE_ID_V19,
        server_time=v19.server_time,
        mystery_shop=v19.mystery_shop,
        story_start=v19.story_start,
        team_snapshot=v19.team_snapshot,
        recharge_activity=v19.recharge_activity,
        chapter_item_init=v19.chapter_item_init,
        experience_task_init=v19.experience_task_init,
        chapter_task_init=v19.chapter_task_init,
        chapter_machine_init=v19.chapter_machine_init,
        battle_start_reply=v19.battle_start_reply,
        battle_game_over_reply=v19.battle_game_over_reply,
        prison_fight=v19.prison_fight,
        chapter_fight_replay=v19.chapter_fight_replay,
        drawcard_unlock=v19.drawcard_unlock,
        chapter_box_replay=v19.chapter_box_replay,
        babel_settlement_push=_build_v20_babel_settlement_push_policy(),
    )


@lru_cache(maxsize=1)
def get_profile_v20() -> CompatibilityProfile:
    """Return the immutable v20 profile (v19 + the settlement S2C 580 push).

    Opt-in only: `get_profile()` keeps returning v18, and the server selects
    v20 for a `dev-babel-main` session whose dev-play overlay is applied.
    """

    return _build_v20_profile()


def _build_v21_mail_login_policy() -> MailLoginPolicy:
    """The empty returnMailStart/returnMailEnd login bracket (TASK-0242).

    The client creates ``le_tbMailDict`` only from these two S2C handlers, and
    ``hasNewMail`` iterates it unguarded.  The bracket itself is the shipped
    client's contract; the zero-row replay and its position in the login
    segment are this build's labelled compatibility choices.
    """

    return MailLoginPolicy(
        start_wire_id=MAIL_LOGIN_START_WIRE_ID,
        end_wire_id=MAIL_LOGIN_END_WIRE_ID,
        start_name=MAIL_LOGIN_START_NAME,
        end_name=MAIL_LOGIN_END_NAME,
        wire_format=MAIL_LOGIN_WIRE_FORMAT,
        row_count=MAIL_LOGIN_ROW_COUNT,
        allowed_login_positions=MAIL_LOGIN_ALLOWED_POSITIONS,
        historical_ordering=MAIL_LOGIN_HISTORICAL_ORDERING,
        historical_values_recovered=MAIL_LOGIN_HISTORICAL_VALUES_RECOVERED,
        replaceable=True,
        historical=False,
        block_provenance=_provenance(
            source=(
                "recovered registration pkgScProtocolMgr.lua:369-370 "
                "(registerS2C(\"\", \"returnMailStart\") / (\"\", "
                "\"returnMailEnd\")) and the shipped handlers "
                "socket/nc/pkgMailNcMgr PROTO 1-2 -> "
                "mail/pkgMailModelMgr.returnMailStart/End (source lines 15-25), "
                "which set le_tbMailDict/le_tbTempItemDict and close the "
                "bracket; TASK-0242 forensics"
            ),
            origin="deterministic_recovered",
            evidence_grade=(
                "A target-client wire contract (deterministic_recovered: the "
                "client's own registration formats and both container handlers "
                "are read out of the shipped bytecode)"
            ),
            reason=(
                "pkgMailModelMgr.hasNewMail (source line 132) runs "
                "pairs(le_tbMailDict) unguarded; the global starts nil (module "
                "lines 3-4) and only these two S2C handlers create it.  A login "
                "without the bracket throws the exact RUN-0076 error on every "
                "main-city red-point refresh"
            ),
            compatibility_version=PROFILE_ID_V21,
            field_contract=(
                "S2C 255 returnMailStart \"\" then S2C 256 returnMailEnd \"\", "
                "exactly one of each, zero returnSingleMail rows"
            ),
            risk=(
                "the empty container makes isShowRedPoint evaluate false; no "
                "mail, attachment or reward is invented, so the mailbox is "
                "empty until real rows exist"
            ),
            replaceable=True,
        ),
        value_provenance=_provenance(
            source=(
                "the explicit zero-row choice of this slice; no mail row "
                "producer (guankaMail and the mail write contracts) is "
                "recovered, and none is needed to create the container"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C compatibility choice: the empty bracket is the minimum that "
                "removes pairs(nil) without asserting state the archive does "
                "not hold (D-21)"
            ),
            reason=(
                "a mail row would claim a message, attachment or reward this "
                "archive never received; the original server's row values are "
                "UNKNOWN, so the honest replay is Start + End with zero rows"
            ),
            compatibility_version=PROFILE_ID_V21,
            field_contract=(
                "row_count = 0; every row-carrying packet (S2C 257 "
                "returnSingleMail, 258 returnSingleMailItem, 259 "
                "returnSingleMailEnd) stays unregistered and unsent"
            ),
            risk=(
                "the original server may have delivered real mail at login; "
                "the resulting empty mailbox is a labelled compatibility "
                "limitation, not a historical claim"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "the login position is chosen inside the recovered S2C 8 "
                "returnLoginInfoStart .. S2C 9 returnLoginInfoEnd bracket, "
                "immediately after S2C 10 returnRoleInfo.  The original "
                "ordering is not recoverable"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C ordering decision; the handlers themselves are independent "
                "of pkgCgNetMgr.setSyncComplete and of the main-city UI, so "
                "they are safe anywhere after the modules are required"
            ),
            reason=(
                "placing the pair inside the same sync bracket keeps one login "
                "segment, and the handlers only touch pkgMailModelMgr plus "
                "pkgEventMgr.dispatch(MAIL_LIST_UPDATE), which has no main-city "
                "listener"
            ),
            compatibility_version=PROFILE_ID_V21,
            field_contract=(
                "position = after_returnRoleInfo_before_returnLoginInfoEnd; "
                "packets = (returnMailStart, returnMailEnd)"
            ),
            risk=(
                "if the original order mattered visually it is unobservable "
                "here and remains UNKNOWN (historical_ordering=unknown)"
            ),
            replaceable=True,
        ),
        introduced_in=PROFILE_ID_V21,
    )


@lru_cache(maxsize=1)
def _build_v21_profile() -> CompatibilityProfile:
    """V18 inherited byte for byte + the empty mail login bracket.

    Only `mail_login` is added.  Every other policy object is the v18 object
    itself -- deliberately NOT v19's type-aware `battle_start_reply` nor v20's
    `babel_settlement_push` -- so the default `qa-changan-main` login stream is
    the v18 stream plus exactly the two mail packets.
    """

    v18 = get_profile_v18()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V21,
            target_client=v18.metadata.target_client,
            purpose=v18.metadata.purpose,
            status=v18.metadata.status,
        ),
        role_values=v18.role_values,
        sign_catalogs=v18.sign_catalogs,
        sign_state_template=v18.sign_state_template,
        scene_state_template=v18.scene_state_template,
        active_soul_rules=v18.active_soul_rules,
        parent_profile_id=PROFILE_ID_V18,
        server_time=v18.server_time,
        mystery_shop=v18.mystery_shop,
        story_start=v18.story_start,
        team_snapshot=v18.team_snapshot,
        recharge_activity=v18.recharge_activity,
        chapter_item_init=v18.chapter_item_init,
        experience_task_init=v18.experience_task_init,
        chapter_task_init=v18.chapter_task_init,
        chapter_machine_init=v18.chapter_machine_init,
        battle_start_reply=v18.battle_start_reply,
        battle_game_over_reply=v18.battle_game_over_reply,
        prison_fight=v18.prison_fight,
        chapter_fight_replay=v18.chapter_fight_replay,
        drawcard_unlock=v18.drawcard_unlock,
        chapter_box_replay=v18.chapter_box_replay,
        mail_login=_build_v21_mail_login_policy(),
    )


@lru_cache(maxsize=1)
def get_profile_v21() -> CompatibilityProfile:
    """Return the immutable v21 profile (v18 + the empty mail login bracket).

    Opt-in from v22 onward: v22 inherits this object graph byte for byte and
    adds only the S2C 39 team-power value.
    """

    return _build_v21_profile()


def _build_v22_team_max_fight_policy() -> TeamMaxFightPolicy:
    """The S2C 39 ``returnTeamMaxFight`` login value (TASK-0245 / #27).

    The packet, its format, its handler and the gate that reads it are shipped
    client code.  The NUMBER is the client's own ``caclGroupFight`` over the
    preset it is showing; ``revival_stub/team_power.py`` freezes the shipped
    ``card``/``cardFightRatio``/``soul`` values and
    ``max_decoder/analyze_team_max_fight.py`` re-derives them and proves the
    five zero terms.  What the original server sent (current vs. best preset)
    and its ordering are UNKNOWN.
    """

    return TeamMaxFightPolicy(
        wire_id=TEAM_MAX_FIGHT_WIRE_ID,
        name=TEAM_MAX_FIGHT_NAME,
        wire_format=TEAM_MAX_FIGHT_WIRE_FORMAT,
        allowed_login_positions=TEAM_MAX_FIGHT_ALLOWED_POSITIONS,
        triggers=TEAM_MAX_FIGHT_TRIGGERS,
        value_policy=TEAM_MAX_FIGHT_VALUE_POLICY,
        historical_ordering=TEAM_MAX_FIGHT_HISTORICAL_ORDERING,
        historical_values_recovered=TEAM_MAX_FIGHT_HISTORICAL_VALUES_RECOVERED,
        replaceable=True,
        historical=False,
        block_provenance=_provenance(
            source=(
                "pkgScProtocolMgr.lua:295 registerS2C(\"N\", "
                "\"returnTeamMaxFight\"); socket/nc/pkgTeamNcMgr PROTO 5 "
                "forwards to pkgTeamDataMgr.setMaxFight (team/pkgTeamDataMgr.lua "
                "lines 17-20/23-25); the only gate read is "
                "pkgChapterEventHandle.handleFight lines 250-289 and its shipped "
                "text fight_limit_to = 战力需要达到{1}; TASK-0245 forensics"
            ),
            origin="deterministic_recovered",
            evidence_grade=(
                "A target-client wire contract (deterministic_recovered: the "
                "registration format, the one writer, the one gate and the "
                "shipped alert text are read out of the shipped bytecode)"
            ),
            reason=(
                "le_dMaxFight starts at 0 and the stub never sent S2C 39, so "
                "every fight whose row carries fightLimitFight > 0 refused the "
                "player regardless of the team -- RUN-0077's #27"
            ),
            compatibility_version=PROFILE_ID_V22,
            field_contract=(
                "S2C 39 returnTeamMaxFight \"N\": exactly one uint32 value, no "
                "lock partner"
            ),
            risk=(
                "a wrong value only moves the client's local gate; no reward, "
                "battle result or server-side state is produced by the packet"
            ),
            replaceable=True,
        ),
        value_provenance=_provenance(
            source=(
                "pkgTeamDataMgr.caclGroupFight (lines 226-248) = "
                "pkgPlayerInfoMgr.caclRoleFight (442-451) + sum "
                "pkgCardDataMgr.getCardFight (796-811) + "
                "pkgFabaoDataMgr.caclSelectFight (688-702) + rune shenfu; the "
                "five zero terms are proved in "
                "max_decoder/analyze_team_max_fight.py; card.fight, "
                "cardFightRatio levelN/advN and soul.max[101].fight are frozen "
                "in revival_stub/team_power.py (TEAM_POWER_DIGEST)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C value choice: the formula and every input are recovered, but "
                "the original server's exact value semantics (current preset vs. "
                "best preset) and its triggers are UNKNOWN"
            ),
            reason=(
                "the current preset is the team the Owner is looking at and the "
                "number pkgMainUIMgr.updateFight prints; computing it from this "
                "archive's own rows is the only honest value"
            ),
            compatibility_version=PROFILE_ID_V22,
            field_contract=(
                "value = soul 101 fight + sum(floor(cfg.fight * ratio(level|advN))) "
                "over the preset's client-visible card ids; no synthetic number "
                "is stored"
            ),
            risk=(
                "if the original server used the best preset instead, a player "
                "who saved a weaker current preset sees a lower gate value; the "
                "value is recomputed on every team edit"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "the login position is chosen after the v5 team snapshot's "
                "S2C 35 returnGroupId and inside the recovered terminal login "
                "segment; the original ordering is not recoverable"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C ordering decision: setMaxFight dispatches UPDATE_TEAM_FIGHT -> "
                "pkgMainUIMgr.updateFight -> getCurFight, which needs the v5 "
                "containers and the current group, so the value can only follow "
                "S2C 35"
            ),
            reason=(
                "S2C 35 already dispatches the same updateFight chain at login, so "
                "the new value rides the one position that is provably safe"
            ),
            compatibility_version=PROFILE_ID_V22,
            field_contract=(
                "position = after_returnGroupId; triggers = "
                f"{list(TEAM_MAX_FIGHT_TRIGGERS)}"
            ),
            risk=(
                "the historical server may have sent it elsewhere; the value is "
                "idempotent (same number) so a reordering is replaceable"
            ),
            replaceable=True,
        ),
        introduced_in=PROFILE_ID_V22,
    )


@lru_cache(maxsize=1)
def _build_v22_profile() -> CompatibilityProfile:
    """V21 inherited object for object + the S2C 39 team-power value.

    Only `team_max_fight` is added.  Every other policy object is the v21
    object itself, so the default v22 login stream is the v21 stream plus
    exactly one S2C 39 after S2C 35.
    """

    v21 = get_profile_v21()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V22,
            target_client=v21.metadata.target_client,
            purpose=v21.metadata.purpose,
            status=v21.metadata.status,
        ),
        role_values=v21.role_values,
        sign_catalogs=v21.sign_catalogs,
        sign_state_template=v21.sign_state_template,
        scene_state_template=v21.scene_state_template,
        active_soul_rules=v21.active_soul_rules,
        parent_profile_id=PROFILE_ID_V21,
        server_time=v21.server_time,
        mystery_shop=v21.mystery_shop,
        story_start=v21.story_start,
        team_snapshot=v21.team_snapshot,
        recharge_activity=v21.recharge_activity,
        chapter_item_init=v21.chapter_item_init,
        experience_task_init=v21.experience_task_init,
        chapter_task_init=v21.chapter_task_init,
        chapter_machine_init=v21.chapter_machine_init,
        battle_start_reply=v21.battle_start_reply,
        battle_game_over_reply=v21.battle_game_over_reply,
        prison_fight=v21.prison_fight,
        chapter_fight_replay=v21.chapter_fight_replay,
        drawcard_unlock=v21.drawcard_unlock,
        chapter_box_replay=v21.chapter_box_replay,
        babel_settlement_push=v21.babel_settlement_push,
        mail_login=v21.mail_login,
        team_max_fight=_build_v22_team_max_fight_policy(),
    )


@lru_cache(maxsize=1)
def get_profile_v22() -> CompatibilityProfile:
    """Return the immutable v22 profile (v21 + the S2C 39 team-power value)."""

    return _build_v22_profile()


def _build_v23_month_sign_state_policy() -> MonthSignStatePolicy:
    """The S2C 1499 ``returnMonthSignData`` answer to C2S 725 (TASK-0246 / #28).

    The packet, its format, its handler and the poll it stops are shipped client
    code.  The VALUES are the client's own empty container plus the next CN
    natural-day boundary of the S2C 0 server clock; the archive has no producer
    of sign rows, extension counts or rewards, so nothing is invented (D-21).
    """

    return MonthSignStatePolicy(
        wire_id=MONTH_SIGN_STATE_WIRE_ID,
        name=MONTH_SIGN_STATE_NAME,
        wire_format=MONTH_SIGN_STATE_WIRE_FORMAT,
        allowed_positions=MONTH_SIGN_STATE_ALLOWED_POSITIONS,
        triggers=MONTH_SIGN_STATE_TRIGGERS,
        value_policy=MONTH_SIGN_STATE_VALUE_POLICY,
        historical_ordering=MONTH_SIGN_STATE_HISTORICAL_ORDERING,
        historical_values_recovered=(
            MONTH_SIGN_STATE_HISTORICAL_VALUES_RECOVERED
        ),
        replaceable=True,
        historical=False,
        block_provenance=_provenance(
            source=(
                "pkgScProtocolMgr.lua:2032 registerS2C(\"NppaNNN\", "
                "\"returnMonthSignData\"); pkgS2CMonthSignMgr PROTO 0 "
                "(lines 8-11) forwards all seven fields to "
                "pkgMonthSignModel.setMonthSignData (lines 15-22), the ONLY "
                "writer of le_nextRefreshTime; the repeating 1000 ms "
                "pkgTimerMgr.add in setUpMonthSignUpdateTimer (lines 130-140) "
                "sends only while le_nextRefreshTime < server time -- "
                "TASK-0246 forensics"
            ),
            origin="deterministic_recovered",
            evidence_grade=(
                "A target-client wire contract (deterministic_recovered: the "
                "registration format, the one writer, the poll condition and the "
                "empty-container parser are read out of the shipped bytecode)"
            ),
            reason=(
                "RUN-0077 answered C2S 725 with the empty S2C 1500 ack only, so "
                "le_nextRefreshTime kept its 0 and the client re-sent the "
                "lock-send once a second for the whole RUN (#28)"
            ),
            compatibility_version=PROFILE_ID_V23,
            field_contract=(
                "S2C 1499 returnMonthSignData \"NppaNNN\": udExtSign N, "
                "lastExtSignTime p, nextRefreshTime p, strMonthSign a, "
                "curYear N, curMonth N, curDay N; no lock partner"
            ),
            risk=(
                "a wrong order (1500 before 1499) would let the client dispatch "
                "MONTH_SIGN_DATA_UPDATED before the model holds the new state"
            ),
            replaceable=True,
        ),
        value_provenance=_provenance(
            source=(
                "pkgMonthSignConst.stringToTable: pkgStringMgr.split(\"#\", "
                "strSign) returns {} for the empty string (its `#str ~= 0` "
                "guard), so \"\" with udExtSign 0 builds {udExtSign = 0, "
                "tbSignMap = {}} without writing a nil key; the CN (UTC+8) "
                "natural-day boundary is derived from the S2C 0 dTime source "
                "(ServerTimePolicy.protocol_fields, current_unix_time_at_send)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C value choice: the empty container's shape is recovered, but "
                "this role has no sign history, extension count or reward, so the "
                "values and the refresh boundary are a labelled compatibility "
                "choice, not a historical record"
            ),
            reason=(
                "the client stops polling only when nextRefreshTime is in its "
                "future; the next CN day boundary is the smallest honest value "
                "that keeps the poll at most daily without inventing sign state"
            ),
            compatibility_version=PROFILE_ID_V23,
            field_contract=(
                "udExtSign=0, lastExtSignTime=0, strMonthSign=\"\", "
                "nextRefreshTime=next CN (UTC+8) midnight of the send-time server "
                "clock (strictly > now), curYear/curMonth/curDay = that clock's CN "
                "calendar date"
            ),
            risk=(
                "if the original server refreshed at another boundary (the "
                "client's isPassedDay uses const_ResetHour = 6) the client polls "
                "once at the wrong hour; the packet is replaceable"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "1499 must precede the S2C 1500 ack: the 1500 handler only "
                "dispatches MONTH_SIGN_DATA_UPDATED, and its UI listeners read "
                "the model 1499 writes; the original server's ordering is still "
                "UNKNOWN"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C ordering decision: the two handlers' semantics force "
                "1499-before-1500 locally, but no historical capture exists"
            ),
            reason=(
                "sending the dispatch first would refresh the month-sign UI from "
                "the previous (empty) model"
            ),
            compatibility_version=PROFILE_ID_V23,
            field_contract=(
                "position = beforeResponseMonthSignData; trigger = "
                f"{list(MONTH_SIGN_STATE_TRIGGERS)}"
            ),
            risk=(
                "the historical server may have sent 1499 as a separate push; the "
                "answer is idempotent (same empty state) so a reordering is "
                "replaceable"
            ),
            replaceable=True,
        ),
        introduced_in=PROFILE_ID_V23,
    )


@lru_cache(maxsize=1)
def _build_v23_profile() -> CompatibilityProfile:
    """V22 inherited object for object + the S2C 1499 month-sign state.

    Only `month_sign_state` is added and it is request-triggered, so the default
    v23 login stream is the v22 stream byte for byte.
    """

    v22 = get_profile_v22()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V23,
            target_client=v22.metadata.target_client,
            purpose=v22.metadata.purpose,
            status=v22.metadata.status,
        ),
        role_values=v22.role_values,
        sign_catalogs=v22.sign_catalogs,
        sign_state_template=v22.sign_state_template,
        scene_state_template=v22.scene_state_template,
        active_soul_rules=v22.active_soul_rules,
        parent_profile_id=PROFILE_ID_V22,
        server_time=v22.server_time,
        mystery_shop=v22.mystery_shop,
        story_start=v22.story_start,
        team_snapshot=v22.team_snapshot,
        recharge_activity=v22.recharge_activity,
        chapter_item_init=v22.chapter_item_init,
        experience_task_init=v22.experience_task_init,
        chapter_task_init=v22.chapter_task_init,
        chapter_machine_init=v22.chapter_machine_init,
        battle_start_reply=v22.battle_start_reply,
        battle_game_over_reply=v22.battle_game_over_reply,
        prison_fight=v22.prison_fight,
        chapter_fight_replay=v22.chapter_fight_replay,
        drawcard_unlock=v22.drawcard_unlock,
        chapter_box_replay=v22.chapter_box_replay,
        babel_settlement_push=v22.babel_settlement_push,
        mail_login=v22.mail_login,
        team_max_fight=v22.team_max_fight,
        month_sign_state=_build_v23_month_sign_state_policy(),
    )


@lru_cache(maxsize=1)
def get_profile_v23() -> CompatibilityProfile:
    """Return the immutable v23 profile (v22 + the month-sign state answer)."""

    return _build_v23_profile()


def _build_v24_bit_rabbit_state_policy() -> BitRabbitStatePolicy:
    """The S2C 1435 ``syncBitRabbit`` inactive session state (TASK-0247).

    `pkgBitRabbitDataMgr.checkAddRabbit` (source lines 100-114) compares
    `getFightCountLeft()` with 0 before any guard, and that accessor returns
    `le_tbBitRabbitData.dFightCountLeft` (lines 116-118) -- nil until one of the
    two server pushes writes it.  RUN-0077 proves the crash and the two skipped
    red-point refreshes.  The archive has no rabbit row, square entry, fight,
    rank or reward, so the only honest state is the client's own inactive one:
    six zeros.  The value choice and the login position are labelled
    compatibility choices.
    """

    return BitRabbitStatePolicy(
        wire_id=BIT_RABBIT_STATE_WIRE_ID,
        name=BIT_RABBIT_STATE_NAME,
        wire_format=BIT_RABBIT_STATE_WIRE_FORMAT,
        allowed_positions=BIT_RABBIT_STATE_ALLOWED_POSITIONS,
        triggers=BIT_RABBIT_STATE_TRIGGERS,
        value_policy=BIT_RABBIT_STATE_VALUE_POLICY,
        historical_ordering=BIT_RABBIT_STATE_HISTORICAL_ORDERING,
        historical_values_recovered=BIT_RABBIT_STATE_HISTORICAL_VALUES_RECOVERED,
        replaceable=True,
        historical=False,
        block_provenance=_provenance(
            source=(
                "pkgScProtocolMgr.lua:1950 registerS2C(\"NNNNNN\", "
                "\"syncBitRabbit\"); pkgBitRabbitDataMgr.s2c.syncBitRabbit "
                "(source lines 8-17) is one of the two writers of "
                "le_tbBitRabbitData, the container checkAddRabbit (lines "
                "100-114, line 102 `getFightCountLeft() <= 0`) reads through "
                "getFightCountLeft (lines 116-118); the caller chain is "
                "pkgMainUIMgr.checkRedPoint line 966 -> addRabbitOnFriend line "
                "167 -> checkAddRabbit; TASK-0247 forensics"
            ),
            origin="deterministic_recovered",
            evidence_grade=(
                "A target-client wire contract (deterministic_recovered: the "
                "registration format, both module writers, the unguarded nil "
                "comparison and the caller chain are read out of the shipped "
                "bytecode and confirmed by RUN-0077's Lua stack)"
            ),
            reason=(
                "RUN-0077 logcat:6257/6276 threw "
                "`pkgBitRabbitDataMgr.lua:102: attempt to compare nil with "
                "number` 0.571 s after doFinishSwitch, so checkChapterRedPoint "
                "and updateFriendUnreadNum never ran; no login packet ever "
                "filled dFightCountLeft"
            ),
            compatibility_version=PROFILE_ID_V24,
            field_contract=(
                "S2C 1435 syncBitRabbit \"NNNNNN\": dFightCountLeft, "
                "dLastAddCountTime, dRabbitTime, dRabbitLeftCount, "
                "dRabbitOwnBit, dRabbitBossCfgId; no lock partner"
            ),
            risk=(
                "the packet only writes client memory and dispatches nothing; a "
                "wrong value can at most show a stale rabbit tile count"
            ),
            replaceable=True,
        ),
        value_provenance=_provenance(
            source=(
                "pkgBitRabbitDataMgr module line 7 initialises "
                "le_tbBitRabbitData to {}; with dFightCountLeft/dRabbitTime/"
                "dRabbitLeftCount 0 checkAddRabbit returns false at line 102 and "
                "getMyOwnRabbit (lines 120-135) returns nil.  No rabbit row, "
                "square entry, fight, rank or reward producer exists in the "
                "archive"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C value choice: the container shape and every reader are "
                "recovered, but this role has no bitRabbit state at all, so the "
                "all-zero inactive state is a labelled compatibility choice"
            ),
            reason=(
                "a non-zero fight count would light the friend rabbit icon and "
                "suggest an activity this archive cannot serve; zero is the "
                "minimum state that restores the red-point chain without "
                "inventing rows, fights or rewards"
            ),
            compatibility_version=PROFILE_ID_V24,
            field_contract=(
                "all six N values 0: dFightCountLeft=0, dLastAddCountTime=0, "
                "dRabbitTime=0, dRabbitLeftCount=0, dRabbitOwnBit=0, "
                "dRabbitBossCfgId=0"
            ),
            risk=(
                "if the original server granted three daily calls, the player "
                "cannot call a rabbit until a real producer exists; the state is "
                "replaceable and never claims a historical value"
            ),
            replaceable=True,
        ),
        ordering_provenance=_provenance(
            source=(
                "the login position is the tail of the recovered terminal login "
                "segment, after S2C 555 returnTalentSoulSkill; the original "
                "server's ordering is not recoverable (no capture carries 1435)"
            ),
            origin="synthetic_compatibility",
            evidence_grade=(
                "C ordering decision: the handler only writes "
                "le_tbBitRabbitData and dispatches nothing, so it must only "
                "precede the main-city pkgMainUIMgr.init that reads it"
            ),
            reason=(
                "appending it after the last existing packet keeps every earlier "
                "login packet's index and bytes unchanged and still arrives "
                "before pkgMainUIMgr.checkRedPoint runs"
            ),
            compatibility_version=PROFILE_ID_V24,
            field_contract=(
                "position = after_returnTalentSoulSkill; trigger = login"
            ),
            risk=(
                "the historical server may have pushed it elsewhere; the state "
                "is idempotent (same zeros) so a reordering is replaceable"
            ),
            replaceable=True,
        ),
        introduced_in=PROFILE_ID_V24,
    )


@lru_cache(maxsize=1)
def _build_v24_profile() -> CompatibilityProfile:
    """V23 inherited object for object + the inactive bitRabbit login state.

    Only `bit_rabbit_state` is added, so the default v24 login stream is the v23
    stream plus exactly one S2C 1435 after S2C 555.
    """

    v23 = get_profile_v23()
    return CompatibilityProfile(
        metadata=ProfileMetadata(
            profile_id=PROFILE_ID_V24,
            target_client=v23.metadata.target_client,
            purpose=v23.metadata.purpose,
            status=v23.metadata.status,
        ),
        role_values=v23.role_values,
        sign_catalogs=v23.sign_catalogs,
        sign_state_template=v23.sign_state_template,
        scene_state_template=v23.scene_state_template,
        active_soul_rules=v23.active_soul_rules,
        parent_profile_id=PROFILE_ID_V23,
        server_time=v23.server_time,
        mystery_shop=v23.mystery_shop,
        story_start=v23.story_start,
        team_snapshot=v23.team_snapshot,
        recharge_activity=v23.recharge_activity,
        chapter_item_init=v23.chapter_item_init,
        experience_task_init=v23.experience_task_init,
        chapter_task_init=v23.chapter_task_init,
        chapter_machine_init=v23.chapter_machine_init,
        battle_start_reply=v23.battle_start_reply,
        battle_game_over_reply=v23.battle_game_over_reply,
        prison_fight=v23.prison_fight,
        chapter_fight_replay=v23.chapter_fight_replay,
        drawcard_unlock=v23.drawcard_unlock,
        chapter_box_replay=v23.chapter_box_replay,
        babel_settlement_push=v23.babel_settlement_push,
        mail_login=v23.mail_login,
        team_max_fight=v23.team_max_fight,
        month_sign_state=v23.month_sign_state,
        bit_rabbit_state=_build_v24_bit_rabbit_state_policy(),
    )


@lru_cache(maxsize=1)
def get_profile_v24() -> CompatibilityProfile:
    """Return the immutable v24 profile (v23 + the inactive bitRabbit state)."""

    return _build_v24_profile()


@lru_cache(maxsize=1)
def get_profile() -> CompatibilityProfile:
    """Return the validated immutable active compatibility profile (v24)."""

    return get_profile_v24()


def instantiate_role_compatibility_state(
    career: int,
    *,
    profile: CompatibilityProfile | None = None,
) -> RoleCompatibilityState:
    """Deep-copy mutable profile defaults into one in-memory role instance."""

    selected = profile or get_profile()
    soul_rule = selected.active_soul_for_career(career)
    sign = selected.sign_state_template
    scene = selected.scene_state_template
    return RoleCompatibilityState(
        profile=selected,
        role_values=[
            MutableRoleValue(definition=definition, value=definition.initial_value)
            for definition in selected.role_values
        ],
        sign_state=SignAccountState(
            is_new_month=sign.is_new_month,
            sign_count=sign.sign_count,
            make_up_time=sign.make_up_time,
            role_make_up_time=sign.role_make_up_time,
            is_today_signed=sign.is_today_signed,
            sum_claimed=sign.sum_claimed,
            seven_claimed=sign.seven_claimed,
            seven_day=sign.seven_day,
        ),
        scene_state=SceneState(
            scene_type=scene.scene_type,
            pos_x=scene.pos_x,
            pos_y=scene.pos_y,
            pos_z=scene.pos_z,
        ),
        active_soul=soul_rule.soul_id,
    )


def catalog_hashes(catalogs: SignCatalogs) -> dict[str, str]:
    return {
        name: hashlib.sha256(value.encode("utf-8")).hexdigest()
        for name, value in {
            "daily": catalogs.daily,
            "cumulative": catalogs.cumulative,
            "seven_day": catalogs.seven_day,
        }.items()
    }
