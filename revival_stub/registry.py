"""Extensible protocol registries recovered from pkgScProtocolMgr.max."""

from __future__ import annotations

from dataclasses import dataclass

from .compatibility_profile import ROLE_VALUE_WIRE_FORMAT


SUPPORTED_FORMAT_CHARS = frozenset("TtWwNnpya")


@dataclass(frozen=True, slots=True)
class ProtocolSpec:
    protocol_id: int
    name: str
    fmt: str

    def __post_init__(self) -> None:
        if not 0 <= self.protocol_id <= 0xFFFF:
            raise ValueError(f"protocol id out of range: {self.protocol_id}")
        unknown = set(self.fmt) - SUPPORTED_FORMAT_CHARS
        if unknown:
            raise ValueError(f"unsupported format character(s): {sorted(unknown)}")


class ProtocolRegistry:
    """A direction-specific, append-friendly protocol registry."""

    def __init__(self, direction: str) -> None:
        self.direction = direction
        self._by_id: dict[int, ProtocolSpec] = {}
        self._by_name: dict[str, ProtocolSpec] = {}

    def register(self, protocol_id: int, name: str, fmt: str) -> ProtocolSpec:
        if protocol_id in self._by_id:
            raise ValueError(f"duplicate {self.direction} protocol id {protocol_id}")
        if name in self._by_name:
            raise ValueError(f"duplicate {self.direction} protocol name {name}")
        spec = ProtocolSpec(protocol_id, name, fmt)
        self._by_id[protocol_id] = spec
        self._by_name[name] = spec
        return spec

    def by_id(self, protocol_id: int) -> ProtocolSpec:
        try:
            return self._by_id[protocol_id]
        except KeyError as exc:
            raise KeyError(
                f"unknown {self.direction} protocol id {protocol_id}"
            ) from exc

    def by_name(self, name: str) -> ProtocolSpec:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"unknown {self.direction} protocol {name!r}") from exc

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self):
        return iter(self._by_id.values())


C2S_REGISTRY = ProtocolRegistry("C2S")
C2S_REGISTRY.register(0, "loginByToken", "paapaaaaaap")
C2S_REGISTRY.register(2, "createRole", "aN")
C2S_REGISTRY.register(3, "receivePackage", "Ny")
C2S_REGISTRY.register(4, "enterGame", "y")
# C2S 10 `reconnect` ('paNaaaaa').  The client sends reconnect as the first
# packet of a new socket after the process stayed alive through a
# background/disconnect (`pkgReconnectMgr.reconnectLogin` only calls it when
# `getGameLogined()` is true).  Its S2C 15 `reconnectSuccess` reply is
# registered with the other header S2C ids below.
C2S_REGISTRY.register(10, "reconnect", "paNaaaaa")
C2S_REGISTRY.register(22, "oneMinute", "")
# Chapter-map battle result (TASK-0155).  Same DYNAMIC battle registration as its
# neighbours below: pkgScProtocolMgr.initC2SProtocol source line 110 hands the
# registration callback to pkgBattleNet.registerC2S, which walks
# pkgBattleProtoDef.battleProtoCg2Sg (12 entries, wire ids 61..72) and then
# battleProtoCg2Sf (78), so this row is index 6 of the first block and its wire id
# is 61 + 6 = 67.  The name and the format are the client's own table, read out of
# the shipped bytecode listing (never counted from source text, D-02):
# max_decoder/forensics_protocol_ids/zys_scAll__np__pkgBattleProtoDef.listing.txt,
# entries 6/7/8 of the table that ends with
# `SETLIST R4 12 1` / `SETGLOBAL R4 K181='battleProtoCg2Sg'`:
#     index 6 -> 'NNppppppppppppaaaappp' 'battlePveGameOver'          (wire 67)
#     index 7 -> 'NN'                   'battlePveGameOver_svrVer'   (wire 68)
#     index 8 -> 'yNNpppppppppa'        'netEnterPveBattle'          (wire 69)
# max_decoder/analyze_pre_scene.py::battle_c2s_entries() re-derives all three from
# that same listing, and tests/test_protocol_derivation.py asserts this
# registration against it, so the two cannot drift apart.
#
# RUN-0048 is the device observation (`.autopilot/device_runs/RUN-0048/
# server.log:439`): 245 bytes whose header `b0 7f 67` XORs against that
# connection's reader key `0x56BB7FF3` (server.log:437's key_after) to
# protocol_id 0x7FB0 ^ 0x7FF3 = 67 and sequence byte 0x67 ^ 0xF3 = 148, exactly
# the next expected sequence (147 + 1).  Decoding the buffer with the format above
# consumes exactly 245 bytes and yields pveOrPvpType 86 -- which is the client's
# own first argument.
#
# LOCK.  The S2C side of this row is `{ "Na", "battlePveGameOverEnd",
# "battlePveGameOver", "battlePveGameOver_svrVer" }` -- index 7 of
# battleProtoSg2Cg -- and pkgScProtocolMgr's own registerS2C hands those trailing
# fields to pkgLockSendMgr.regLockProto (`pkgScProtocolMgr.lua:23`), so C2S 67 and
# its `_svrVer` sibling are lock-sends unlocked by S2C 96 (and, on the error path,
# by S2C 97).  Without either, the client's own LOCK_TIME = 15000 ms timer fires
# and the client shuts its own socket down
# (`reconnect/lockSend/pkgLockSendMgr.max` source lines 43..58).
#
# ONLY the observed row is registered.  `battlePveGameOver_svrVer` (wire 68) is
# NOT registered: RUN-0048 proves the client took the single-driver branch, which
# sends 67, and a `_svrVer` row would assert a server-driven battle this archive
# has no producer for (D-18/D-21: one edge at a time, nothing pre-filled).  While
# unregistered it keeps the D-08 fail-closed path -- `unknown protocol id` plus the
# closed connection -- exactly as C2S 67 itself did before this slice.
C2S_REGISTRY.register(67, "battlePveGameOver", "NNppppppppppppaaaappp")
# Chapter-map battle start (TASK-0134).  This is a DYNAMIC battle registration,
# not a pkgScProtocolMgr literal: pkgScProtocolMgr.initC2SProtocol line 110 hands
# the registration callback to pkgBattleNet.registerC2S, which walks
# pkgBattleProtoDef.battleProtoCg2Sg (12 entries) and then battleProtoCg2Sf (78),
# i.e. 90 C2S ids inserted after source ordinal 60 (pointBuyGoods) and before
# source ordinal 61 (changeMailState) -- the same insertion the recovered table
# already uses for C2S 233 finishStory (143+90) and C2S 725 (635+90).
#
# The name and the format are the client's own table, read out of the shipped
# bytecode listing (not counted from source text, D-02):
# max_decoder/forensics_protocol_ids/zys_scAll__np__pkgBattleProtoDef.listing.txt,
# the 9th (0-based 8) entry of the table that ends with
# `SETLIST R4 12 1` / `SETGLOBAL R4 K181='battleProtoCg2Sg'`:
#     K190='yNNpppppppppa' K26='netEnterPveBattle'
# so wire id 61+8 = 69.  max_decoder/analyze_pre_scene.py re-derives both the
# entry and the wire id from that same listing, and
# revival_stub/tests/test_protocol_derivation.py asserts this registration
# against it, so the two cannot drift apart.
#
# The ONLY sender in the whole corpus is pkgBattleMgr.c2sStartPveBattle's inner
# closure (luaScript__battle__pkgBattleMgr.lua lines 53-87), which every chapter
# caller reaches through
#   pkgBattleMgr.c2sStartPveBattle(pkgBattleConst.PVETYPE_GUANKA_CHAPTER_MAP,
#                                  tbCfg.fightId, dIndex, 0, dChapterId, 0)
# -- that call is `c2s.netEnterPveBattle(e_tbSocket, isRetry, dSyncCount,
# pveOrPvpType, p1..p9, a1)`, i.e. 'y' isRetry, 'N' dSyncCount, 'N'
# pveOrPvpType, nine 'p' parameters and one 'a'.  A whole-corpus scan for
# `netEnterPveBattle` finds only pkgBattleMgr itself and pkgConnectMgr's
# le_notClearProto table, so no other module can put this id on the wire.
#
# The client DOES await an answer: pkgBattleProtoDef registers the S2C side as
# `{ "NyyNNNNpppppppppaaa", "startPveBattle", "netEnterPveBattle" }`, and
# registerS2C hands that third field to pkgLockSendMgr.regLockProto, which makes
# C2S 69 a LOCK-SEND (D-86 sense): the client switches its own event system off,
# shows the lock-send overlay and arms LOCK_TIME = 15000 ms, and the S2C
# startPveBattle is the only unlock (on timeout the client shuts its OWN socket
# down).
#
# TASK-0134/0135 stopped at "decode + record + NO response" because every field of
# the reply is battle state this archive had no producer for.  TASK-0139 then read
# both dispatch branches of the reply out of the client's own bytecode and found
# one that needs no server at all (`isUseSfSvr = false` -> the client's own local
# single-driver simulation), and D-129 records the Owner's decision to take it with
# every non-recoverable value labelled `synthetic_compatibility`.  Since TASK-0140
# the reply IS sent, once per request: see `S2C 89` below and the
# BattleStartReply policy in compatibility_profile (profile v11).
C2S_REGISTRY.register(69, "netEnterPveBattle", "yNNpppppppppa")
# Recovered from pkgScProtocolMgr.initC2SProtocol (source line 71) and the only
# sender, pkgHeartMgr.onSpeedHeartHandler (luaScript__utility__pkgHeartMgr.lua:
# 160): c2s.speedHeart(e_tbSocket, os.time(), le_cur_net_delta, bClearOld), i.e.
# 'p' unix seconds, 'p' measured net delta, 'y' first-send-after-scene-change.
# No dynamic C2S registration precedes it, so source ordinal 23 is also the
# native runtime ID (asserted through max_decoder/analyze_pre_scene.py).
# Recovered client registration contains no S2C response for it; the transport
# contract is decode + record + no response (like 22/37/579, D-08).
C2S_REGISTRY.register(23, "speedHeart", "ppy")
# Recovered from pkgScProtocolMgr.initC2SProtocol and pkgHeartMgr: this is the
# periodic online heartbeat. It has no payload. The active S2C registration
# table contains returnHeartTime_forNetState for protocol 26, but no active
# returnHeartTime registration for protocol 25; do not invent a response.
C2S_REGISTRY.register(25, "onLine", "")
C2S_REGISTRY.register(26, "onLine_forNetState", "")
C2S_REGISTRY.register(37, "refreshSign7Time", "")
C2S_REGISTRY.register(39, "getSignReward", "")
# IDs after battle-protocol insertion are the recovered native runtime IDs,
# not source-literal ordinals.
C2S_REGISTRY.register(217, "updateApkSize", "pa")
# Main-city offline-chat request (TASK-0249, WP-SYSTEM-001 S1).  The client's own
# `pkgScProtocolMgr.lua:122` is `registerC2S("", "getOfflineChatData")`, the 72nd
# C2S registration; with the recovered 90 dynamic battle rows inserted before it
# the runtime id is 162 (derived by `analyze_pre_scene.protocol_table()`, never
# counted from source text -- D-02; asserted by
# `tests/test_protocol_derivation.py`).  Its whole matching function
# `pkgFriendChatFileMgr.getOfflineChatData` (PROTO 0.5, source lines 61-63) is one
# `c2s.getOfflineChatData(e_tbSocket)` call -- no payload, no callback, no lock
# timer -- and its only caller is the main-city UI loading-complete callback
# `pkgMainUIMgr.lua:1234`.  No `registerS2C` in the corpus names it: the only
# recovered S2C containing "Offline" is `returnKickOfflineResult` (source line
# 477), a different handler.  The transport contract is therefore decode +
# record + no response (the C2S 22/23/232 pattern), never a synthetic reply.
#
# RUN-0078 is the first device observation
# (`.autopilot/device_runs/RUN-0078/server.log:605-609`): C2S 720 leaves the
# reader at key 0x20997D02 / sequence 12, then the 3-byte frame `a0 7d 0f`
# decodes to 0x7DA0 ^ 0x7D02 = 162 with sequence byte 0x0F ^ 0x02 = 13 -- an
# aligned frame with an empty payload, not a stream desync -- and the strict
# D-08 path killed the connection that had just reached the main city.
C2S_REGISTRY.register(162, "getOfflineChatData", "")
# Guidance-chain completion (TASK-0148).  Recovered from
# pkgScProtocolMgr.initC2SProtocol source line 197, the literal immediately
# BEFORE `registerC2S("N", "finishStory")` (line 198), so it carries the same
# +90 dynamic battle insert as its neighbour: source ordinal 142 + 90 = 232.
# That shift is re-derived by max_decoder/analyze_pre_scene.py::
# protocol_table(), never counted from source text (D-02), and
# revival_stub/tests/test_protocol_derivation.py asserts the frozen row
# (name / fmt / ordinal / +90) together with both neighbours.
#
# RUN-0045 is the first device observation of this id
# (`.autopilot/device_runs/RUN-0045/server.log:421`): after the S2C 89 battle
# start the client played its own local battle and then put this packet on the
# wire at 19:19:32.972Z.  Its header `dd dc be` decodes against that
# connection's reader key `0x243ADC35` (the key_after of the previous packet,
# server.log:419-420) to protocol_id 0xDCDD ^ 0xDC35 = 232 with sequence byte
# 0xBE ^ 0x35 = 139, i.e. exactly the next expected sequence -- the frame is
# aligned, so 232 is the client's own id and not a stream desync.  Its 4-byte
# payload decodes to guideId 4000.
#
# The ONLY sender in the corpus is pkgGuideMgr.finishGuideTask
# (luaScript__guide__pkgGuideMgr.lua:14) `c2s.finishGuideTask(e_tbSocket,
# dGuideId)`, reached from that guide's completion callbacks; the client's own
# local guide-state writes follow the send inside the same function.  No
# registerS2C in the corpus names finishGuideTask as its lock partner (TASK-0026
# §3.3 already recorded the same for the then-unregistered send), so this is NOT
# a lock-send.  The transport contract is therefore decode + record + no response
# (the C2S 22/23/233 pattern); registering the recovered id/format adds no
# synthetic state.
C2S_REGISTRY.register(232, "finishGuideTask", "N")
# Recovered from pkgScProtocolMgr.initC2SProtocol (source line 198; runtime id =
# source ordinal 143 + 90 dynamic battle inserts = 233, asserted through
# max_decoder/analyze_pre_scene.py and analyze_story_family.py).  The ONLY send
# site in the whole corpus is pkgStoryMgr.finishStory(dStoryId), which writes the
# client-local STATE_FINISH *before* it sends, so the client needs no reply to
# stay consistent.  None of the five story registerS2C calls names a lock
# partner, so this is NOT a lock-send (unlike C2S 720).  The transport contract
# is therefore decode + record + no response (the C2S 22/23/37/579 pattern);
# registering the recovered id/format adds no synthetic state.
C2S_REGISTRY.register(233, "finishStory", "N")
# Chapter-map event action (TASK-0184).  The client's own
# `pkgScProtocolMgr.lua:189` registers `registerC2S("NNNNNp", "chapterMapEvent")`;
# it is the 134th C2S registration, and 90 dynamic battle rows are inserted
# before it (`initC2SProtocol` line 110 hands the callback to
# `pkgBattleNet.registerC2S`), so the runtime id is 224 -- asserted by
# `analyze_pre_scene.protocol_table()` and by
# `tests/test_protocol_derivation.py`, never counted from source text (D-02).
#
# WHAT SENDS IT.  A whole-corpus string scan (`max_decoder/scan_strings.py`
# over 1,827 chunks) finds `chapterMapEvent` as a C2S call constant in exactly
# two chunks, both read out of the shipped bytecode:
#   * `chapter/pkgChapterEventHandle` PROTO 0.11 (source 291-322): the
#     `tbCfg.type == EVENT_SUBTYPE_CHEST` branch plays an animation and its
#     nested callback (PROTO 0.11.0, 311-319) sends
#     `chapterMapEvent(socket, dChapterId, dIndex, 0, 0, 0, dSyncCount)`;
#   * `chapter/UI/pkgUIChapterEventView` PROTO 0.1.1.2 (source 166-169), the
#     EVENT_TYPE_REWARD option button: it reads the option's `dIndex` and sends
#     `chapterMapEvent(socket, dChapterId, dIndex, 0, <option dIndex>, 0,
#     dSyncCount)`.
# Both senders put the literal 0 at wire positions 3 and 5.
#
# LOCK.  `pkgScProtocolMgr.lua:512` is
# `registerS2C("pppppp", "chapterMapEvent", "chapterMapEvent")`; the third
# argument is handed to `pkgLockSendMgr.regLockProto`, so C2S 224 is a
# LOCK-SEND and S2C 398 is its only unlock.  That is exactly the lock the real
# client showed as 「正在努力载入 请稍候...」 for 15 s before it closed its own
# socket in the 2026-10-18 session.
#
# The transport handler decodes, validates the one recovered chapter-1 event
# node, answers once with S2C 398, and records the client's own action as an
# item row for the next login replay.  Values are assembled by
# `revival_stub.chapter_map_event`, never here.
C2S_REGISTRY.register(224, "chapterMapEvent", "NNNNNp")
# Chapter task receive/submit (TASK-0240).  `pkgScProtocolMgr.lua:192` is
# `registerC2S("NNNNp", "chapterMapTask")`, the 137th C2S registration; with the
# 90 dynamic battle rows inserted before it the runtime id is 227 (derived by
# `analyze_pre_scene.protocol_table()` and asserted by
# `tests/test_protocol_derivation.py`, never counted from source text -- D-02).
#
# WHAT SENDS IT.  Three recovered senders, all with the same five values
# `(dChapterId, dHostIndex, dTaskId, dAction, dSyncCount)`:
#   * `pkgChapterTaskMgr.IsSubmitTask` PROTO 0.13.0 (source lines 154-158),
#     `TASK_COMPLETE`;
#   * `pkgChapterTaskMgr.CheckAllTaskAchieve` PROTO 0.18 (source lines
#     389-420), `TASK_COMPLETE`, gated on received && not completed && achieved;
#   * `pkgUIChapterTaskNpc` PROTO 0.1.0 (source lines 98-109), `TASK_RECEIVE`.
#
# LOCK.  `pkgScProtocolMgr.lua:511` is
# `registerS2C("NNNNN", "chapterMapTask", "chapterMapTask")`; the third argument
# is handed to `pkgLockSendMgr.regLockProto`, so C2S 227 is a LOCK-SEND and
# S2C 397 is its only unlock (the same class as C2S 224, LOCK_TIME = 15000 ms).
#
# The handler records the client's own action and answers exactly once; the
# values are assembled by `revival_stub.chapter_map_task`, never here.  It never
# sends S2C 388 updateChapterTask, never invents a sync counter and never grants
# a task reward (guankaMapTask.rewardId's server path is UNKNOWN).
C2S_REGISTRY.register(227, "chapterMapTask", "NNNNp")
# Chapter milestone chest (TASK-0202).  `pkgScProtocolMgr.lua:187` registers
# `registerC2S("ppp", "chapterAcceptBox")`, and its S2C row (line 498) is
# `registerS2C("ppp", "chapterAcceptBox", "chapterAcceptBox")` -- the third
# argument is the lock partner, so this is a LOCK-SEND unlocked only by S2C 384.
#
# The runtime id is the D-02 registration simulation's answer, NOT the source
# ordinal and NOT the S2C id: C2S ordinal 132 + the 90 dynamic battle rows =
# 222 (asserted by analyze_pre_scene.protocol_table() and
# tests/test_protocol_derivation.py).  C2S 384 in the catalog is a different
# protocol (verifyActiveCode) and must never be registered here.
#
# The three request values are `(dChapterId, dBoxIndex, dSyncCount)` from
# `pkgUIChapterMainUI.onAcceptBox` (source lines 284-310); the reply's status is
# compared with `pkgGuankaMapConstMgr.ACCEPT_BOX_STATUS_*` by the handler
# `socket/nc/pkgGuankaMapNcMgr` PROTO 0.12 (source lines 49-62).  Values are
# assembled by `revival_stub.chapter_rewards`, never here.
C2S_REGISTRY.register(222, "chapterAcceptBox", "ppp")
C2S_REGISTRY.register(318, "systemGMList", "")
C2S_REGISTRY.register(495, "cgStatusSave", "N")
C2S_REGISTRY.register(579, "requestExitRoom", "y")
# Recovered from pkgScProtocolMgr (source line 2013).  The only sender is
# pkgClientReloadMgr.startReceiveClientReload (luaScript__system__pkgClientReloadMgr.lua:9),
# called unconditionally as the last statement of the S2C 13 enterGameSuccess
# handler (pkgCgLoginNcMgr.s2c.enterGameSuccess).  Runtime ID = source ordinal
# 630 + 90 battle C2S inserts (asserted through max_decoder/analyze_pre_scene.py
# and analyze_post283_scene_init.py).  registerS2C("", "initClientReloadEnd",
# "receiveClientReload") (line 2016) makes this a LOCK-SEND: the client disables
# its event system and shuts its own socket down after LOCK_TIME = 15000 ms
# unless S2C 1487 arrives.  The answer is the compatibility policy implemented
# in tcp_stub (minimal empty completion), never recovered historical script data.
C2S_REGISTRY.register(720, "receiveClientReload", "N")
# Recovered from pkgScProtocolMgr (source line 1369; runtime id = source ordinal
# 422 + 90 battle C2S inserts = 512, asserted through
# max_decoder/analyze_pre_scene.py and analyze_bag_family.py).  The ONLY send
# site in the corpus is pkgBagUtil.sendBagSort, reached from the five recovered
# writer functions setBagSortCard/Piece/Fabao/setBagCardSkill/setGuildBossAnger;
# every one of them writes the client-local `le_tbBagSort[dOps]` *before* it
# sends, so the client needs no reply to stay consistent.  Neither registration
# of this name carries a lock partner -- `registerS2C("NN", "bagSortState")` has
# no third argument -- so unlike C2S 429 goodsUse (whose unlock is
# registerS2C("NNN", "goodsUse", "goodsUse")) this is NOT a lock-send.  The
# transport contract is therefore decode + record + no response (the
# C2S 22/23/233 pattern); registering the recovered id/format adds no synthetic
# state.
C2S_REGISTRY.register(512, "bagSortState", "NN")
# Month-sign data request (pkgScProtocolMgr source line 2033; runtime id =
# source ordinal 635 + 90 battle C2S inserts = 725, asserted through
# max_decoder/analyze_pre_scene.py).  The ONLY send site in the whole corpus is
# pkgMonthSignModel.sendToGetMonthSignData
# (luaScript__monthSign__pkgMonthSignModel.lua:124-127), which is reached from
# the recovered 1000 ms REPEATING timer pkgMainUIMgr installs during the
# main-city UI init:
#   pkgMainUIMgr.lua:1132 `updateInfo()` -> statement 15 `updateMonthSignTimer()`
#   at source line 661 -> `pkgMonthSignModel.setUpMonthSignUpdateTimer()`
#   (pkgMainUIMgr.lua source lines 642-644) ->
#   `pkgTimerMgr.add(1000, ...)` (= LuaTimer.Add(1000, 1000, ...), so the first
#   fire is 1000 ms later and every 1000 ms after that) -> the callback's only
#   statement compares `le_nextRefreshTime < pkgServerTimeMgr.getCurrentServerTime()`
#   and sends when true.  `le_nextRefreshTime` starts at its 0 unset value and
#   is written by NOTHING except `pkgMonthSignModel.setMonthSignData`, i.e. the
#   S2C1499 handler -- so the client asks once a second and keeps asking.
# That is why RUN-0027 saw the request 1.29 s after the main city's init
# (client 21:16:28.05 -> 21:16:29.34) as a bare 3-byte header `d7 7f 0f` with
# no payload: the recovered format is `''`, zero fields.
#
# This is a LOCK-SEND, not a fire-and-forget notification: see the S2C1500
# registration below, which names it as the lock partner.
C2S_REGISTRY.register(725, "requestMonthSignData", "")

# Order validation before a shop payment (TASK-0243, Issue #26).  The client's
# own `pkgScProtocolMgr.lua:953` is `registerC2S("pppp", "inspectOrderValid")`,
# the 321st C2S registration; with the 90 dynamic battle rows inserted before it
# the runtime id is 411 (derived by `analyze_pre_scene.protocol_table()` and by
# `max_decoder/analyze_market_inspect_order.py`, never counted from source text
# -- D-02).
#
# WHAT SENDS IT.  Two recovered senders, both with the same four values
# `(dMarketType, dId, dCost, dSyncCount)`:
#   * `market/pkgMarketMgr` PROTO 0.1.1 (source lines 108-122), the recharge
#     tab's buy button -- `e_tbMarketType.RECHARGE`, the cell's `id`/`price`,
#     and `pkgSyncCounterMgr.getSyncCounter(MARKET_INSPECT_ORDER)`;
#   * `market/pkgUIMarketGiftBuyPanel` PROTO 0.2.2 (source lines 200-237), the
#     limited-gift panel, same shape with the gift's `dKey`/`price`.
# Both open `market/pkgMarketWaitTipsViewMgr` with `pkgUIMgr.LOCK_SEND_ORDER`
# first -- that constant is the UI sorting order 1000
# (`uiManager/pkgUIMgr` source line 30), not a lock declaration.
#
# LOCK.  `pkgScProtocolMgr.lua:966` is
# `registerS2C("ppppa", "inspectOrderResult", "inspectOrderValid")`; the third
# argument is handed to `pkgLockSendMgr.regLockProto`, so C2S 411 is a LOCK-SEND
# and S2C 745 is its only unlock.  Without it the shop keeps its waiting overlay
# (`_T('market_buying')`) and the client closes its socket after
# LOCK_TIME = 15000 ms (D-186, Issue #26).
#
# The handler answers exactly once with a client-recognisable refusal result and
# creates no order; values are assembled by `revival_stub.market_inspect_order`,
# never here.  C2S 412 `simulatePay` stays unimplemented (D-21).
C2S_REGISTRY.register(411, "inspectOrderValid", "pppp")
# C2S 653 `sendToEquipFightDataUpate` '' -- the equip-fight panel's data
# request (TASK-0259), and the FIRST frame `equip_fight/pkgEquipFightPanel.show()`
# puts on the wire.
#
# The id and the zero-field format are the client's own registration
# (`zys_scAll__np__pkgScProtocolMgr.lua:1790`,
# `registerC2S("", "sendToEquipFightDataUpate")`), re-derived by
# `max_decoder/analyze_pre_scene.protocol_table()`: it is source ordinal 563 and
# the 90 dynamic battle rows are inserted before it, so 563 + 90 = 653 -- never
# counted from source text (D-02).  That registration row has NO third
# argument; the very next C2S row (line 1791, `registerC2S("NN",
# "sendToEquipFightOneKey", "returnEquipFightOneKey", "returnEquipFightError")`)
# is what a lock-send looks like, so C2S 653 is a fire-and-forget request and
# arms no 15 s client lock.
#
# WHAT SENDS IT.  The only send site in the whole corpus is
# `pkgEquipFightControl.sendToFetchEquipFightData` (source lines 19-21,
# `c2s.sendToEquipFightDataUpate(e_tbSocket)`), reached from
# `pkgEquipFightPanel._sendToUpdateData()` (panel source lines 75-77) inside
# `show()` (lines 62-73) and from that panel's 1000 ms `_onUpdateTimer`
# refresh.  `show()` calls it BEFORE `c2s.artifactDgnOpenUI`, which is why a
# strict D-08 break on this id never lets the client reach C2S 744 (TASK-0258
# report §F.2).
#
# REPLY.  The client registers S2C 1322 `returnEquipFightDataUpdate` ('') at
# line 1783, but this archive has no recovered producer for its values and an
# empty "data updated" packet would still assert a server state never observed
# on a device.  The transport contract is therefore decode + record + no
# response (the C2S 22/23/233 pattern); the handler must never fall through to
# the `unsupported_protocol` break (D-127).
C2S_REGISTRY.register(213, "strengthMagicStart", "a")
C2S_REGISTRY.register(214, "strengthMagic", "a")
C2S_REGISTRY.register(215, "strengthDogMagic", "NN")
C2S_REGISTRY.register(216, "strengthMagicEnd", "N")
C2S_REGISTRY.register(11, "equipMagic", "aNyNp")
C2S_REGISTRY.register(12, "replaceMagic", "NNNp")
C2S_REGISTRY.register(385, "topTowerEnter", "NN")
C2S_REGISTRY.register(406, "equipRune", "NNNyN")
C2S_REGISTRY.register(407, "replaceRune", "NNNp")
C2S_REGISTRY.register(408, "addRune", "N")
C2S_REGISTRY.register(653, "sendToEquipFightDataUpate", "")
# C2S 744 `artifactDgnOpenUI` 'N' -- the 神器副本 entry request (TASK-0258).
#
# The id and the format are the client's own registration
# (`zys_scAll__np__pkgScProtocolMgr.lua:2078-2121`, the artifactDgn family
# 744-759), re-derived by `max_decoder/analyze_pre_scene.protocol_table()` and
# frozen in `reports/task_0257_artifact_dgn_contract.json` (`protocols`).  In
# the whole corpus the packet has six send sites, all `c2s.artifactDgnOpenUI(
# e_tbSocket)` with no business argument:
#   * `equip_fight/pkgEquipFightPanel.show()` -- the 装备战斗面板 opens, i.e.
#     the same action that reveals `tab/artifact`;
#   * two `artifactDgn/pkgUIArtifactDgnEnter` sites and three in the dead
#     `pkgUIArtifactDgnEnter2` view (活动更新 retry paths, TASK-0257).
# The registration declares one N, so this is the wire authority; the argument
# itself has never been captured on a device (TASK-0257 U-1) and is recorded,
# never interpreted.
#
# LOCK.  The family's only lock partners are C2S 756/757 `artifactDgnShopBuy`
# / `artifactDgnShopUpdate` -> S2C 1548/1549 (TASK-0257); 744 names none, so an
# unanswered request arms no 15 s lock timer -- but the handle() branch still
# records every request and, on an applied dev-play overlay, answers S2C 1526
# then S2C 1527.  Without the overlay it records and keeps the connection: it
# must never fall through to the `unsupported_protocol` break (D-127).
#
# ONE EDGE ONLY (D-18/D-21): 745/746 and the rest of the family stay
# unregistered; their packets cannot be sent by this stub.
C2S_REGISTRY.register(744, "artifactDgnOpenUI", "N")

S2C_REGISTRY = ProtocolRegistry("S2C")
# First recovered S2C registration (pkgScProtocolMgr).  Sent by the
# compatibility-profile v2 ServerTime policy: dTime = revival send-time unix
# seconds, dClock / dPowerRecoverTime are dead-field placeholders, dOpenTime =
# revival process start.  Historical values and send ordering are unknown.
S2C_REGISTRY.register(0, "serverTime", "NNNN")
S2C_REGISTRY.register(5, "noRole", "")
S2C_REGISTRY.register(8, "returnLoginInfoStart", "Ny")
S2C_REGISTRY.register(9, "returnLoginInfoEnd", "Ny")
S2C_REGISTRY.register(10, "returnRoleInfo", "apapppppppppppppppppp")
S2C_REGISTRY.register(13, "enterGameSuccess", "")
S2C_REGISTRY.register(14, "returnCreateRoleResult", "y")
# S2C 15 `reconnectSuccess` (''): the sole lock partner of C2S 10 `reconnect`.
# The client's `pkgReconnectMgr.reconnectSuccess` tears down the reconnect view,
# re-sends enterGame when it was in game, and replays the last unacked request.
S2C_REGISTRY.register(15, "reconnectSuccess", "")
S2C_REGISTRY.register(27, "returnAllValue", ROLE_VALUE_WIRE_FORMAT)
S2C_REGISTRY.register(85, "returnSignInReward", "yNNNyaaaaaN")
S2C_REGISTRY.register(283, "returnSceneInfo", "Nnnn")
# The answer to C2S 69 `netEnterPveBattle` (TASK-0138 derived the contract,
# TASK-0139 derived the branch that has to be taken, TASK-0140 wired it up).
#
# IDENTITY.  This is a DYNAMIC battle registration, not a pkgScProtocolMgr
# literal: `pkgScProtocolMgr.initS2CProtocol` (decompiled source line 345) hands
# the registration callback to `pkgBattleNet.registerS2C`, which walks
# battleProtoSg2Cg (11 entries) and then battleProtoSf2Cg (120).  Row 0 of the
# first table is this entry, so its wire id is 89 = the number of S2C
# registrations that happened before it.  The direction counts are independent
# (`le_lastS2CProtoId` / `le_lastC2SProtoId`), so 89 is an S2C number only.  The
# simulation is anchored by six device-observed ids -- C2S 22/26/69/725 and
# S2C 283/1500 -- and it is `max_decoder/analyze_pve_battle_start.py` /
# `analyze_pre_scene.protocol_table()` that re-derive it, never a count of source
# text (D-02).  S2C 89 itself has NEVER been observed on a device: it is a
# derived id (NOT_VERIFIED), and the next device run is what may confirm it.
#
# FORMAT.  The client's own table gives `NyyNNNNpppppppppaaa` (row 0 of
# battleProtoSg2Cg, `max_decoder/forensics_protocol_ids/
# zys_scAll__np__pkgBattleProtoDef.listing.txt:349-354`), 19 values matching the
# 20 parameters of the client's own handler in `battle/net/pkgBattlePveFromSg`
# (socket + 19).  Position by position: sfStartTime, isUseSfSvr, (unnamed),
# selfGroup, pveOrPvpType, dRawRandomSeed, dSyncCount, p1..p9, s1..s3.
#
# LOCK.  The third field of that row is `netEnterPveBattle`, i.e. this packet is
# the unlock of the C2S 69 lock-send, and the reason exactly one reply is sent
# per request.  `netEnterPveBattleFailed` is the other unlock; it is NOT
# registered, because it would tell the player the battle failed while it did not.
#
# VALUES.  Every one of the 19 values is defined and provenance-labelled by the
# compatibility profile's BattleStartReply policy (v11):
# `BATTLE_START_FIELD_TABLE` / `BATTLE_START_PROVENANCE` in
# compatibility_profile.py.  The registry only fixes the id and the format; the
# values are never assembled here.
S2C_REGISTRY.register(89, "startPveBattle", "NyyNNNNpppppppppaaa")
# Chapter-map battle end (TASK-0155) -- the unlock of the C2S 67 lock-send above.
# Same dynamic battle block as S2C 89: pkgBattleNet.registerS2C
# (`pkgBattleNet.lua:22-32`) walks battleProtoSg2Cg (11 entries) FIRST and then
# battleProtoSf2Cg (120), and pkgScProtocolMgr.initS2CProtocol calls it at source
# line 345.  `battlePveGameOverEnd` is index 7 of battleProtoSg2Cg -- the row
# `{ "Na", "battlePveGameOverEnd", "battlePveGameOver", "battlePveGameOver_svrVer" }`
# -- so its wire id is 89 + 7 = 96.  The id is derived exactly the way S2C 89 was
# (registration-order simulation anchored on six device-observed ids), by
# max_decoder/analyze_pre_scene.py::protocol_table(); tests/
# test_protocol_derivation.py asserts the frozen row.
#
# FORMAT.  `Na`, two values; the client's own handler `s2c.battlePveGameOverEnd`
# in `luaScript/battle/net/pkgGameOverFromSg.max` takes 3 parameters (socket + 2),
# and its body never names either value.
#
# LOCK.  This is the S2C side of the pair named by the C2S rows 67/68 above; it is
# the ONLY unlock, and `battlePveGameOverError` (wire 97, format '') is the error
# variant.  97 is deliberately NOT registered: it makes the client dispatch
# FIGHT_ERROR_RETURN (`pkgGameOverFromSg` PROTO 0.4, source lines 90..92) instead
# of settling the battle, i.e. a visibly different outcome this archive has no
# evidence for (D-129 already excluded the equivalent fallback for S2C 89).
#
# VALUES.  Both values are defined and provenance-labelled by the compatibility
# profile's BattleGameOverReply policy (v12):
# `BATTLE_GAME_OVER_FIELD_TABLE` / `BATTLE_GAME_OVER_PROVENANCE` in
# compatibility_profile.py.  The registry only fixes the id and the format; the
# values are never assembled here.
S2C_REGISTRY.register(96, "battlePveGameOverEnd", "Na")
# Story-state family (pkgScProtocolMgr lines 528-532).  S2C411 returnStoryStart
# is the compatibility profile v4 StoryStart policy's EMPTY body, which is the
# client's own `le_story_list = {}` ("no story is finished"), and it is the
# first packet of every login's story state.  S2C412 returnStory 'NNN' then
# carries the rows: TASK-0073 replays it ONLY for a story whose completion this
# archive actually observed (`story_completions`), and never for a story that
# has no such row -- so the empty-snapshot login of an archive with no
# completion stays byte-for-byte what it was.
# 413 returnStoryEnd '', 414 updateStoryStatus 'NNN' and 415 resetStory 'N' stay
# deliberately unregistered: `s2c.returnStoryEnd` is an empty handler with no
# lock partner (so it closes nothing and is provably not required), and 414/415
# would assert a per-story id/state/time that has no recovered producer
# (D-21: no pre-filling, no invented story rows).
S2C_REGISTRY.register(411, "returnStoryStart", "")
S2C_REGISTRY.register(412, "returnStory", "NNN")
# Team login family (pkgScProtocolMgr lines 291-295).  Runtime IDs equal the
# source ordinals: no dynamic battle insert precedes line 295
# (max_decoder/analyze_team_family.py and analyze_post283_scene_init.py assert
# this through the single registration simulation, D-02).
# The client's own main-city UI init aborts without this family:
# pkgMainUIMgr.init line 1130 `updateInfo()` -> updateFight -> getCurFight ->
# caclGroupFight -> pkgPlayerInfoMgr.caclRoleFight -> getTeamEquipFight ->
# getRoleEquipInfoList -> `pkgTeamDataMgr.getGroupEquips(dGroupId)` =
# `tbTeamCard[dGroupId].tbEquips` (UNGUARDED) with dGroupId = getCurGroupID()
# = -1, and `tbTeamCard[-1]` is nil (RUN-0026 logcat, TASK-0091 §2.2).
#   * 35 returnGroupId 'N' -> setCurGroupID.  registerS2C("N",
#     "returnGroupId", "changeTeamGroup") makes it the lock-partner answer of
#     C2S 5, which the client sends ONLY from the Team UI -- never at login.
#   * 36 returnTeamStart '' -> initTeamCardData: the client's own preset reset,
#     `for i = 1, dMaxTeam` (dMaxTeam = 5) with empty tbCards/tbEquips.
#   * 37 returnTeamInfo 'N'+16*'a' -> setTeamCardData(dGroupID, 4 equips,
#     11 cards, god weapon, true); the ONLY writer of the full record shape
#     tbEquips/tbCards/strGodWeaponId.
# 38 returnTeamEnd '' (empty handler, no lock partner, writes nothing) stays
# deliberately unregistered: it would add no state the client reads (D-21).
# 39 returnTeamMaxFight 'N' IS registered (TASK-0245 / profile v22): its handler
# writes `le_dMaxFight`, the value `pkgChapterEventHandle.handleFight` compares
# with `tbCfg.fightLimitFight` (the shipped `fight_limit_to` text is
# 战力需要达到{1}) and the value `pkgMainUIMgr.getCurFight` cannot replace.
S2C_REGISTRY.register(39, "returnTeamMaxFight", "N")
S2C_REGISTRY.register(35, "returnGroupId", "N")
S2C_REGISTRY.register(36, "returnTeamStart", "")
S2C_REGISTRY.register(37, "returnTeamInfo", "N" + "a" * 16)
# Team editing (2026-10-18).  The client's own `registerC2S` at
# `pkgScProtocolMgr.lua:34-35` names
#   C2S 5 changeTeamGroup "N" and C2S 6 updateTeam "N" + 14*"a".
# `changeTeamGroup` is a lock-send whose partner is S2C 35 (`returnGroupId`);
# `updateTeam` is a lock-send whose partner is S2C 40 (`updateTeam`, the same
# 17-value shape as S2C 37) or S2C 41 (`updateTeamError`, empty).  The client
# call site (`pkgUITeamSelectView.saveTeamCardData`) passes four equipment ids
# and eleven card ids; the registered format carries fourteen strings, i.e.
# four equipment ids + ten card slots -- slot 11 is the client's own
# level-999 slot (`TB_CARD_OPEN_LEVEL[11] = 999`) and is never editable.
# The reply S2C 40 still carries all eleven card slots so the client's record
# shape is complete.
C2S_REGISTRY.register(5, "changeTeamGroup", "N")
C2S_REGISTRY.register(6, "updateTeam", "N" + "a" * 14)
S2C_REGISTRY.register(40, "updateTeam", "N" + "a" * 16)
S2C_REGISTRY.register(41, "updateTeamError", "")
# Fabao-select container family (pkgScProtocolMgr line 304).  Only
# returnSelectMagicStart is registered: `s2c.returnSelectMagicStart` ->
# `pkgTeamDataMgr.initTeamFabaoData()` is the ONLY producer of `tbTeamFabao`
# in the whole corpus, and `pkgFabaoDataMgr.caclSelectFight()` -- the statement
# right after caclRoleFight in the same `caclGroupFight` -- does
# `pairs(pkgTeamDataMgr.getTeamFabaoData(gid))`, which is `pairs(nil)` while
# `tbTeamFabao[gid]` is nil.  Its empty body builds the client's own
# "no fabao selected in any preset": `tbTeamFabao[i] = {"","",""}`.
# 49 returnSelectMagic 'NaN' (per-(group, slot) rows) and 50
# returnSelectMagicEnd '' (dispatches MAGIC_SELECT_CHANGED and writes nothing
# else) stay deliberately unregistered: this archive has no fabao rows to
# replay, so sending them would invent parties (D-21).
S2C_REGISTRY.register(48, "returnSelectMagicStart", "")
S2C_REGISTRY.register(49, "returnSelectMagic", "NaN")
S2C_REGISTRY.register(50, "returnSelectMagicEnd", "")
S2C_REGISTRY.register(728, "returnRuneSelectStart", "")
S2C_REGISTRY.register(729, "returnRuneSelect", "NNN")
S2C_REGISTRY.register(730, "returnRuneSelectEnd", "")
S2C_REGISTRY.register(554, "returnTalentSoulId", "N")
# `returnTalentSoulSkill` `N` (pkgScProtocolMgr line 672; D-02 id 555).
# `pkgTalentNcMgr.returnTalentSoulSkill` -> `pkgTalentMgr.setSoulSkill`; the
# team card view compares the value with `> 0` on its first open
# (`pkgUITeamCardView.updateSoulSkill`).  The global starts nil, so a login
# that never sends this packet makes the comparison raise and aborts
# `pkgUITeamCardView.show` before `updateTeamData` fills `tbSelCardData` --
# which is exactly what the team-editor path then indexes.  The value 0 is
# the client's own "no soul skill selected" sentinel; this archive has no
# selection to replay, so the server sends the empty selection explicitly.
S2C_REGISTRY.register(555, "returnTalentSoulSkill", "N")
# Achieve (天师 rank) login snapshot.  IDs and shapes are the client's own
# `initS2CProtocol` registration order (D-02 authority
# `analyze_pre_scene.protocol_table()`, source lines 718-726):
#   achieveAllStart '' -> achieveAll 'NNNNN' rows -> achieveAllEnd ''
#   achieveLevel 'NNNNNN'
# `pkgAchieveMod.s_achieveLevel(tbSocket, dLevel, ...)` sets the level from the
# first wire value; `pkgUIChapterEventView` gates a chapter option on it
# (`achieve_level_limit`).  Nothing is sent on a normal login: the level has no
# recovered producer in this save, so only the explicit Dev-Server grant below
# emits it.
S2C_REGISTRY.register(585, "achieveAllStart", "")
S2C_REGISTRY.register(586, "achieveAllEnd", "")
S2C_REGISTRY.register(587, "achieveAll", "NNNNN")
S2C_REGISTRY.register(589, "achieveLevel", "NNNNNN")
S2C_REGISTRY.register(590, "achieveLevelTaskStatus", "N")
# Mystery-shop state (dType=1, closed snapshot) sent by the compatibility
# profile v3 MysteryShop policy.  Only S2C487 is sent: no S2C488 goods rows and
# no S2C489 end notification (see reports/shop_sync_compatibility_candidate_review.md).
S2C_REGISTRY.register(487, "refreshShopStart", "Np")
# TASK-0444: city shop open/buy flow (pkgShopCommonMgr).
S2C_REGISTRY.register(488, "refreshGoods", "NNy")
S2C_REGISTRY.register(489, "refreshShopEnd", "N")
S2C_REGISTRY.register(490, "buyGoodsResult", "NNN")
S2C_REGISTRY.register(491, "refreshShopResult", "NN")
C2S_REGISTRY.register(57, "openShop", "N")
C2S_REGISTRY.register(58, "refreshShop", "NN")
C2S_REGISTRY.register(59, "buyGoods", "NNp")
# Client Lua hot-reload family (pkgScProtocolMgr lines 2014-2017).  Only
# initClientReloadEnd is registered: it is the SOLE unlock of C2S 720 and its
# payload is empty.  1485 initClientReloadStart '', 1486 initClientReloadInfo
# 'papaN' and 1488 addClientReload 'papa' stay deliberately unregistered -- the
# historical script payloads have no recovered producer (unknown), and 1486/1488
# would make the client loadstring() unverified code.
S2C_REGISTRY.register(1487, "initClientReloadEnd", "")
# Bag sort state (pkgScProtocolMgr line 1368; runtime id = source ordinal 904 +
# 144 battle S2C inserts = 1048).  `s2c.bagSortState(tbSocket, dOps, dSort)`
# is the ONLY writer of the client's `le_tbBagSort` table, and the bag reads it
# back through pkgBagUtil.getBagSortCard/Fabao/Piece when the panel opens, so
# this packet is the client's restore path for the player's own setting.  It is
# sent ONLY for a slot this archive actually observed the client send (see
# tcp_stub._send_bag_sort_rows): an archive with no such row logs in
# byte-for-byte as before.
S2C_REGISTRY.register(1048, "bagSortState", "NN")
# Month-sign request/response pair (pkgScProtocolMgr source line 2034; runtime
# id = source ordinal 1356 + 144 battle S2C inserts = 1500).
#
# `registerS2C("", "responseMonthSignData", "requestMonthSignData")` passes its
# third argument to `pkgLockSendMgr.regLockProto(strUnlockFunc, ...)`, which
# builds `LOCK_PROTO_LIST["requestMonthSignData"]["responseMonthSignData"]`.
# `pkgConnectMgr.regMobileProto` wraps EVERY c2s sender with
# `pkgLockSendMgr.send(funcString)` after the wire write
# (luaScript__socket__pkgConnectMgr.lua:53), and `pkgLockSendMgr.send` marks
# C2S725 as a LOCK-SEND:
#   * `pkgUIMgr.enableEventSystem(false)` -- the client's own UI event system
#     is switched off;
#   * `showLockSendView()` -- the `reconnect/lockSend/pkgLockSendViewMgr`
#     overlay is shown (unless a scene switch is in progress);
#   * `startTimer()` -- a LOCK_TIME = 15000 ms timer is armed.
# S2C1500 is the ONLY unlock: `nativeCb.netCallhook` -> `pkgLockSendMgr.receiver`
# -> `getLockFuncList(strReceiveFunc)` clears `le_send_list` and, once it is
# empty, `clear()` -> `stopTimer()` + `pkgUIMgr.enableEventSystem(true)`.  If
# nothing arrives, `lockOverTimeHandler` runs `pkgConnectMgr.shutDown()` and
# `e_tbSocket:onShutdown(...)`, i.e. the CLIENT kills its own socket.
#
# The body is empty on both sides, and the recovered handler proves an empty
# body is harmless: `s2c.responseMonthSignData(tbSocket)` takes no field at all
# and its whole body is one `pkgEventMgr.dispatch(MONTH_SIGN_DATA_UPDATED)`
# (luaScript__monthSign__pkgS2CMonthSignMgr.lua:13-15).  It reads no table, so
# it cannot raise a nil reference anywhere downstream.
#
# S2C 1499 `returnMonthSignData` 'NppaNNN' IS registered since TASK-0246 and is
# sent as this request's own answer, immediately before the S2C 1500 ack.  The
# per-role sign history stays UNKNOWN -- there is no producer of sign rows,
# extension counts or rewards -- so the packet carries the client's own empty
# container (`stringToTable("", 0)`) plus a `nextRefreshTime` on the next CN
# natural-day boundary.  That is the only value that stops the poll above
# without inventing sign state (policy v23 `month_sign_state`); 1497/1498 and
# the rest of the family remain absent from this registry.
S2C_REGISTRY.register(1499, "returnMonthSignData", "NppaNNN")
S2C_REGISTRY.register(1500, "responseMonthSignData", "")
# S2C 1435 `syncBitRabbit` 'NNNNNN' (pkgScProtocolMgr source line 1950; runtime
# id 1435 by the D-02 registration simulation).  TASK-0247: the client's
# `pkgBitRabbitDataMgr.checkAddRabbit` compares `getFightCountLeft()` with 0
# before any guard, and `getFightCountLeft` returns
# `le_tbBitRabbitData.dFightCountLeft`, which the module initialises to nil and
# ONLY this push (source lines 8-17; the fight-result packet is the other
# writer) fills.  A login without it throws
# `pkgBitRabbitDataMgr.lua:102: attempt to compare nil with number` during the
# main-city red-point pass (RUN-0077) and skips `checkChapterRedPoint` /
# `updateFriendUnreadNum`.  The stub carries the client's own inactive state
# (six zeros) at the tail of the login segment (policy v24 `bit_rabbit_state`);
# the square list, fight results and the rest of the family stay unregistered
# and unsent.
S2C_REGISTRY.register(1435, "syncBitRabbit", "NNNNNN")
# The 神器副本 entry page's two data packets (TASK-0258).  Both ids are the
# client's own registration rows (`pkgScProtocolMgr.lua:2078-2121`), re-derived
# by `analyze_pre_scene.protocol_table()` and frozen in
# `reports/task_0257_artifact_dgn_contract.json` (`protocols`); neither has a
# lock partner.
#
# S2C 1526 `artifactDgnSendWeekAndOpen` 'NNyyyyy'.  The handler
# (`artifactDgn/pkgArtifactDgnMgr` source lines 214-227) stores
# `le_dWeek`/`le_tbOpen = {f3..f7}` and calls `pkgArtifactDgnShop.setWeek(f2)`.
# The five open flags are `y` on the wire because the entry view tests them as
# Lua booleans (`getDgnsByWeek` maps `openN == 1`); an integer 0 is still true
# in Lua, so the closed weeks must be sent as false.
#
# S2C 1527 `artifactDgnSendRaceAndTower` 'NNNNNN'.  The handler (source lines
# 229-238) declares FIVE business values and writes
# `le_dRace/le_dTower/le_dTowerCount/le_dCardCount/le_dIsHard`, then dispatches
# `pkgUIArtifactDgnEnter_update`.  The 6th N exists only in the registration and
# is TASK-0257 U-3; it is sent as 0 and never given a meaning.
#
# Both are sent by exactly one path: a C2S 744 on a session whose explicit
# dev-play artifact overlay is applied (dev-artifact-main + --dev-play-artifact
# + valid archive meta).  Every other session records the request and sends
# nothing.
S2C_REGISTRY.register(1526, "artifactDgnSendWeekAndOpen", "NNyyyyy")
S2C_REGISTRY.register(1527, "artifactDgnSendRaceAndTower", "NNNNNN")
# The 装备战斗面板's own data plane (TASK-0263).  Both ids are the client's own
# S2C registration rows, re-derived and frozen in
# `reports/task_0261_equip_fight_contract.json`; neither has a lock partner, so a
# missing reply can never shut the socket down -- it only leaves the panel unusable.
#
# S2C 1320 `syncEquipFightStageToCg` 'paayapp' -- reader `pkgS2CEquipFightMgr`
# source lines 7-19.  Field 5 `strOpenFbIds` is a `#`-joined id list consumed by
# `table_ext.values2key(pkgStringMgr.split2Number("#", ...))`; it is the ONLY input
# that unlocks an fb cell (`pkgEquipFightModel.lua:118`).  Fields 6/7 are the refresh
# instants compared with `pkgServerTimeMgr.getCurrentServerTime()` by
# `pkgEquipFightPanel._onUpdateTimer` -- that comparison is what stops the 1 Hz
# re-send of C2S 653.  Field 4 is `y` because the handler stores a Lua boolean.
#
# S2C 1321 `syncEquipFightRandomDataToCg` 'paa' -- reader source lines 20-26.
# `strScoreRules` must cover every stage the panel can open: `getScoreBuffIds` feeds
# `getStageRuleIds` into `ipairs` (`pkgEquipFightModel.lua:221-224`), so a nil rule
# list is a Lua error rather than an empty list.
#
# Both are sent by exactly one path: a C2S 653 on a session whose explicit dev-play
# artifact overlay is applied (dev-artifact-main + --dev-play-artifact + valid
# archive meta).  Every other session records the request and sends nothing.
S2C_REGISTRY.register(1320, "syncEquipFightStageToCg", "paayapp")
S2C_REGISTRY.register(1321, "syncEquipFightRandomDataToCg", "paa")
# Recharge-activity login state (pkgScProtocolMgr source line 1327; runtime id =
# source ordinal 870 + 144 battle S2C inserts = 1014).
#
# The recovered consumer is the main-city UI init's own red-point pass:
# pkgMainUIMgr.lua:1133 initBtn -> :916 initBtnReplace -> :870 checkRedPoint ->
# :962 pkgUIMainUIControl.checkRedPointOnMainUI -> :204 checkRedPointRechargeActivity
# -> :502 pkgKoiDataMgr.getGiftState -> pkgKoiDataMgr.lua:88
# pkgUIRechargeACTaskView.getTask2HadGet -> pkgUIRechargeACTaskView.lua:70
# `tbInfo.bHadGet`, where tbInfo comes from the UNGUARDED
# `pkgRechargeActivityModel.e_tbRechargeActivity.tbGetRecharge[tbCfgData.id]`.
# RUN-0027 raised exactly there (logcat 01:16:30.354) and aborted everything
# after initBtn line 916: the second updateBtn(), initBtnTest(), the btnARFace
# block and the tail of pkgMainUIMgr.init.
#
# `s2c.returnACRechargeDataStart(tbSocket, dRechargeStart, bReset)` is the only
# sender-side initializer: with bReset truthy it calls the client's OWN
# `initRechargeStart()`, which creates one `{bEnough = false, bHadGet = false}`
# row per id of the client's own config table
# `pkgRechargeActivityCfgMgr.e_rechargeActivityTask`.  The client never asks for
# it -- C2S500 sendGetACData has no caller in the 3,372-chunk corpus -- so the
# packet can only arrive as a login push (the v6 RechargeActivity policy).
#
# 1013/1015 (per-task state), 1016/1017 (one-day recharge rows) and 1024
# (returnACGetDataEnd) stay deliberately unregistered: they would assert
# claimed-reward or recharge history this archive has none of (D-21).
S2C_REGISTRY.register(1014, "returnACRechargeDataStart", "py")
# Chapter-map item container (pkgScProtocolMgr lines 503-505; runtime ids 389/390/
# 391, derived by analyze_pre_scene.protocol_table() -- the S2C side is past the
# 144 dynamic battle insertions, so these are NOT the source ordinals).
#
# The recovered consumer is the world map's own init of the FIRST chapter:
# pkgChapterMainUIViewMgr.lua:446 init -> :245 initChapter -> :63
# initChapterBtnNormalStatus -> pkgChapterInfoMgr.lua:165 isChapterPass -> :128
# getChapterProgress -> the local function declared at :208 (isItemFightOver) ->
# :213 `le_tbChapterItemInfo[dChapterId]` (UNGUARDED).  RUN-0029 raised exactly
# there (logcat 03:31:25.490) on the chapter-1 iteration, i.e. BEFORE
# `pkgUIButtonMgr.addBtnListener(btnChapter, onChapterClickHandler, dChapterId)`
# ran for ANY chapter -- which is why the runner's delivered tap (Android
# ACTION_DOWN x=703.0 y=567.0, logcat 03:31:33.730) hit the right rectangle and
# did nothing at all.  The same read is in onChapterClickHandler at line 154,
# before it activates Panel/btnDetail at 195, so binding alone would not have
# been enough.
#
# `pkgChapterInfoMgr` declares `le_tbChapterItemInfo = le_tbChapterItemInfo or nil`
# (line 8), its ONLY creator is `initChapterItemStart()` (`= {}`, lines 48-50),
# and the only caller of that is the client's own S2C handler
# `socket/nc/pkgGuankaMapNcMgr`'s `s2c.initChapterItemStart(tbSocket)`.
#
# 390 initChapterItemInfo 'ppp' was deliberately UNREGISTERED while this archive
# had never observed a chapter fight: a row would assert that a named map item of
# a named chapter is triggered/completed (D-21).  TASK-0174 changes that premise:
# the same connection that starts a chapter-map battle (C2S 69), settles it
# (C2S 67) and receives its one S2C 96 answer has produced a fact this build
# observed, and the client's OWN row writer is `pkgChapterInfoMgr.
# initChapterItemInfo(dChapterId, dType, dId)` (source 52-56).  The id and the
# format below are the client's own registration (pkgScProtocolMgr.lua:504,
# re-derived by analyze_pre_scene.protocol_table() and analyzed by
# analyze_chapter_item_init.assert_protocol_table): registering them adds no
# value -- values are assembled only by the v14 ChapterFightReplay policy and are
# limited to the client's own C2S 69 fields.  v1..v13 profiles still send zero
# rows, so their streams are byte-for-byte what they were.  392
# updateChapterItemInfo 'ppppp' and 393 deleteChapterRecrod 'N' are
# per-exploration updates with no producer here, and stay unregistered.
S2C_REGISTRY.register(389, "initChapterItemStart", "")
S2C_REGISTRY.register(390, "initChapterItemInfo", "ppp")
S2C_REGISTRY.register(391, "initChapterItemEnd", "")
# Chapter milestone chest family (TASK-0202).  `pkgScProtocolMgr.lua:495-498`
# registers the block immediately before the chapter-task family:
#     registerS2C("",    "initChapterBoxStart")
#     registerS2C("pp",  "initChapterBoxInfo")
#     registerS2C("",    "initChapterBoxEnd")
#     registerS2C("ppp", "chapterAcceptBox", "chapterAcceptBox")
# Runtime ids 381..384 = source ordinals 237..240 + the 144 dynamic
# battle/transfer S2C rows, derived by `analyze_pre_scene.protocol_table()`.
#
# 381/382/383 are the client's own container reset/info/end
# (`pkgChapterInfoMgr.initChapterBoxStart/Info/End`, source lines 20-42); the
# v18 login policy sends them only for chests this archive actually accepted.
# 384 is the single unlock of C2S 222, and its third value is compared with the
# shipped ACCEPT_BOX_STATUS_* constants (1 SUCCESS / 2 SYNC_ERR / 3 ACCEPTED /
# 4 FAIL / 5 ILLEGAL).
S2C_REGISTRY.register(381, "initChapterBoxStart", "")
S2C_REGISTRY.register(382, "initChapterBoxInfo", "pp")
S2C_REGISTRY.register(383, "initChapterBoxEnd", "")
S2C_REGISTRY.register(384, "chapterAcceptBox", "ppp")
# Chapter task reply (TASK-0240).  The client's own registration is
# `pkgScProtocolMgr.lua:511`:
#     registerS2C("NNNNN", "chapterMapTask", "chapterMapTask")
# five values, runtime id = the 253rd S2C registration + the 144 dynamic
# battle/transfer rows inserted before it = 397 (derived by
# `analyze_pre_scene.protocol_table()`; the neighbouring box family is 381-384
# and the chapterMapEvent reply is 398 by the same shift).
#
# CONSUMER.  `socket/nc/pkgGuankaMapNcMgr` PROTO 0.16 (source lines 126-145, 6
# parameters = socket + 5 values) prints all five and reads only 1, 3 and 5:
# status 1 -> `pkgChapterTaskMgr.netTaskCompleted(p1, p3)`, status 2 ->
# `netTaskReceived(p1, p3)`, 3/4 print a failure line, 5/6 jump to the end and
# 7-10 have no branch.  Positions 2 and 4 are therefore meaningless to the
# recovered client (U-239-2) and the reply simply echoes them.
#
# NOTE.  This is the S2C side only.  `updateChapterTask` (388) stays
# unregistered: its producer pairing is UNKNOWN, and the client's own
# netTaskReceived/netTaskCompleted never write the container it feeds.
S2C_REGISTRY.register(397, "chapterMapTask", "NNNNN")
# Chapter-map event reply (TASK-0184).  The client's own registration is
# `pkgScProtocolMgr.lua:512`:
#     registerS2C("pppppp", "chapterMapEvent", "chapterMapEvent")
# six values, runtime id = the 254th S2C registration + the 144 dynamic
# battle/transfer rows inserted before it = 398 (derived by
# `analyze_pre_scene.protocol_table()`; the chapter-item family immediately
# before it is already frozen at 389/390/391 by the same shift).
#
# FORMAT.  `p` is the archive codec's 64-bit IEEE-754 double, so the six
# values are numbers, not strings.
#
# CONSUMER.  The handler is `socket/nc/pkgGuankaMapNcMgr` PROTO 0.14 (source
# lines 86-107, 7 parameters = socket + 6 values).  It prints all six, compares
# the sixth with `pkgGuankaMapConstMgr.EVENT_STATUS_SUCCESS` (1), and on
# success calls `pkgChapterEventHandle.checkChapterReward`, whose callback runs
# `pkgChapterObjectMgr.checkEventObjValid(p2, p3)`.  Positions 2 and 3 are the
# node coordinates; positions 1, 4 and 5 are never read by the recovered
# client.  The reply's five non-status values are therefore the client's own
# request values (`request_echo`) and the sixth is the recovered status
# constant.  Nothing else is answered.
S2C_REGISTRY.register(398, "chapterMapEvent", "pppppp")
# Mail login bracket (TASK-0242 / Issue #30).  The client's own registration is
# `pkgScProtocolMgr.lua:369-370`:
#     registerS2C("", "returnMailStart")
#     registerS2C("", "returnMailEnd")
# Two empty formats, runtime ids 255/256 = the D-02 registration simulation's
# answer (`max_decoder/analyze_pre_scene.protocol_table()`; re-derived by
# `revival_stub/tests/test_mail_login_block.py`, never copied from a report).
#
# CONSUMER.  `socket/nc/pkgMailNcMgr` PROTO 1-2 (source lines 1-6) take no values
# and forward to `pkgMailModelMgr.returnMailStart()` / `.returnMailEnd()`
# (source lines 15-25): the first creates `le_tbMailDict = {}` and
# `le_tbTempItemDict = {}`, the second swaps the temp table in and dispatches
# `MAIL_LIST_UPDATE`.  `hasNewMail` (source line 132) iterates `le_tbMailDict`
# with `pairs` and the module initialises it to nil, which is the exact
# RUN-0076 `pkgMailModelMgr.lua:132` error.
#
# ONLY these two ids are registered.  The row-carrying S2C 257/258/259
# (`returnSingleMail` / `returnSingleMailItem` / `returnSingleMailEnd`) and every
# mail write request stay unregistered and unsent: no mail row producer is
# recovered (D-21, TASK-0242).
S2C_REGISTRY.register(42, "refineRefineStone", "p")
S2C_REGISTRY.register(404, "returnRefineCardFeatureList", "aNNNNNyyyN")
S2C_REGISTRY.register(405, "returnRefineCardReplaceResult", "N")
S2C_REGISTRY.register(406, "refineSave", "N")
S2C_REGISTRY.register(407, "refineReplaceSave", "N")
S2C_REGISTRY.register(725, "returnRuneInfoStart", "")
S2C_REGISTRY.register(726, "returnRuneInfo", "N")
S2C_REGISTRY.register(727, "returnRuneInfoEnd", "")
S2C_REGISTRY.register(731, "updateSelectRune", "NNNy")
S2C_REGISTRY.register(732, "updateReplaceRune", "NNyNNy")
S2C_REGISTRY.register(733, "addRune", "N")
S2C_REGISTRY.register(366, "returnStrengthMagicResult", "ay")
S2C_REGISTRY.register(51, "updateSelectMagic", "NaNy")
S2C_REGISTRY.register(52, "updateReplaceMagic", "NNaNa")
S2C_REGISTRY.register(683, "topTowerEvt", "NNNNN")
S2C_REGISTRY.register(684, "topTowerCur", "NN")
S2C_REGISTRY.register(685, "topTowerMapState", "NNN")
S2C_REGISTRY.register(686, "topTowerState", "NN")
S2C_REGISTRY.register(687, "topTowerPass", "NN")
S2C_REGISTRY.register(1322, "returnEquipFightDataUpdate", "")
S2C_REGISTRY.register(1328, "returnEquipFightOneKey", "N")
S2C_REGISTRY.register(1255, "updateAllSkinsData", "aaa")
S2C_REGISTRY.register(1258, "returnSkinBuyNum", "p")
S2C_REGISTRY.register(1259, "changeCareerSkinFinish", "N")
S2C_REGISTRY.register(1260, "changeAllCardSkinFinish", "a")
S2C_REGISTRY.register(1261, "changeOneCardSkinFinish", "aN")
S2C_REGISTRY.register(1262, "addCareerSkinsFromSg", "a")
S2C_REGISTRY.register(1263, "addCardSkinsFromSg", "a")
S2C_REGISTRY.register(408, "returnGuideTaskStart", "")
S2C_REGISTRY.register(409, "returnGuideTask", "NNN")
S2C_REGISTRY.register(410, "returnGuideTaskEnd", "")
S2C_REGISTRY.register(255, "returnMailStart", "")
S2C_REGISTRY.register(256, "returnMailEnd", "")
# TASK-0464: one real welcome mail row so the mail panel has data to open.
S2C_REGISTRY.register(257, "returnSingleMail", "pNTaaa")
S2C_REGISTRY.register(258, "returnSingleMailItem", "pNNNT")
S2C_REGISTRY.register(259, "returnSingleMailEnd", "p")
# Babel (通天塔) info push (TASK-0231/D-187; TASK-0232 server push).  The client
# registers `registerS2C("NNNNN", "returnBabelInfo")` at
# `pkgScProtocolMgr.lua:710`; runtime 580 is the D-02 registration simulation's
# answer, re-derived by `max_decoder/analyze_babel_entry.py` (two runs
# byte-identical, frozen contract `reports/task_0231_babel_contract.json`).
#
# DIRECTION.  `protocol_catalog.py` already lists C2S 579 requestExitRoom and
# C2S 580 searchRoom.  Those are CLIENT requests in a different direction; this
# row registers only the server->client id and must never be added to the C2S
# registry or the C2S catalog.
#
# CONSUMER.  `socket/nc/pkgBabelNcMgr` source lines 8-18: the handler calls
# `pkgBabelMod.setCurFloor(f1, f3, f4, f5)` -- field 2 is never read by the
# recovered client, so its value carries no meaning (D-187 U-1).  There is no
# C2S request for this packet: it is a server push, which is why TASK-0232 sends
# it from the dev-play overlay only.  A normal archive sends nothing here.
S2C_REGISTRY.register(580, "returnBabelInfo", "NNNNN")
# Experience (历练) task container (pkgScProtocolMgr lines 1315-1320; runtime ids
# 1307/1308/1309, derived by analyze_pre_scene.protocol_table() -- the S2C side is
# past the dynamic battle insertions, so these are NOT the source ordinals).
#
# RUN-0031's confirm tap is the consumer of this container, one step away from the
# tap itself.  The chapter detail panel's 确认/探索 button (`Panel/btnDetail/panel/
# btn`, bound to `onEnter` at pkgChapterMainUIViewMgr.lua:182) had ALREADY been
# switched off by the client's own 1 Hz countdown tick when the runner's single tap
# reached it (~8 s after it opened): pkgExperienceMainUIMgr.lua:62 onCountdown ->
# :73 `pkgExperienceMgr.CloseActivityByClient()` -> pkgExperienceMgr.lua:178
# `pkgEventMgr.dispatch(EXPERIENCE_STATUS_CHANGE)` -> pkgExperienceMainUIMgr.lua:
# 199 `pkgUtilMgr.setActiveByName(gameObject, "Panel/btnDetail", false)` -> :214
# `changeChapterBtnExperienceStatus` -> :643 `pkgExperienceMgr
# .GetTaskListByChapterId(dChapterId)` -> pkgExperienceMgr.lua:203
# `le_tbTaskByChapterId[dChapterId]` (UNGUARDED, and the global is nil).
#
# `pkgExperienceMgr` declares `le_tbTaskByChapterId = le_tbTaskByChapterId or nil`
# (chunk line 17); its ONLY creator is the S2C handler the module registers for
# itself, `s2c.returnTaskStart` (`le_tbTask = {}; le_tbTaskByChapterId = {};
# le_dTaskCount = 0`, lines 62-66), and `s2c.returnTaskEnd` (116-118) closes the
# block.  The nil read raises INSIDE the dispatch, so onCountdown's next three
# statements never run -- `if le_dTimer then pkgTimerMgr.delete(le_dTimer);
# le_dTimer = nil end` (74-76) -- which is why the whole sequence repeats every
# 1,000 ms (RUN-0031 logcat: 59 errors in the whole run, 57 of them once per
# second from 05:21:55.457 -- the first tick of the world map that opened the
# chapter-1 panel -- to the run's end).  With the container created, it completes, deletes
# its own timer, and never touches the panel again.
#
# 1308 returnTaskInfo 'pNNNn' stays deliberately UNREGISTERED: a row asserts one
# experience task of one chapter with its own id/state/progress, and this archive
# has no such fact (D-21).  1310-1312 (the reward block) are per-claim updates with
# no producer here and stay unregistered too.
S2C_REGISTRY.register(1307, "returnTaskStart", "")
S2C_REGISTRY.register(1309, "returnTaskEnd", "")
# Chapter (关卡) task container (pkgScProtocolMgr lines 495-500; runtime ids 385/386/
# 387/388, derived by analyze_pre_scene.protocol_table() -- the S2C side is past the
# dynamic battle insertions, so these are NOT the source ordinals).
#
# This is the client's own scene-switch completion reading a second, sibling
# container.  RUN-0032's client really started the chapter scene (`switchScene
# chapter_qiandengzhen_1 3 nil`, logcat 07:17:46.241) and the AB load completed, so
# the loader called `pkgSceneMgr.doFinishSwitch`, which raised 1.492 s later at
# `pkgChapterTaskMgr.lua:62` on the nil `le_tbChapterTaskInfo`.
#
# The chain is `pkgSceneMgr.lua:875 doFinishSwitch` -> the callback
# `pkgChapterSceneMgr.enterChapter` closed over at 131 and `switchScene` stored in
# the global `le_finishCallBack` at 771 -> `pkgChapterSceneMgr.lua:112` ->
# `pkgChapterObjectMgr.lua:385 initIndex2TypeId` -> `pkgChapterInfoMgr.lua:309
# getActiveMap` -> `:262 isItemOver` -> `:62 isTaskReceived`, whose condition indexes
# the global UNGUARDED.
#
# `pkgChapterTaskMgr` declares `le_tbChapterTaskInfo = le_tbChapterTaskInfo or nil`
# (line 6) and writes it in exactly two statements -- that default and
# `initChapterTaskStart()` (`= {}`, lines 21-23), whose only caller is the client's
# own S2C handler `socket/nc/pkgGuankaMapNcMgr`'s
# `s2c.initChapterTaskStart(tbSocket)`.  And no OTHER chunk of the shipped
# luaScript tree writes the name at all: all 1827 chunks are walked for it
# (`analyze_chapter_task_init.assert_whole_tree_writers`), so the four unguarded
# reads (62, 69, 201, 555) can only be cleared by the server running the client's
# own sequence.
#
# `doFinishSwitch` dispatches ENTER_SCENE at 880, calls `pkgSyncSceneMgr.enterScene`
# at 881, prints `-- doFinishSwitch` at 882 and tears the loading UI down at 884-890,
# all AFTER the 875 that raised -- which is why the run's whole logcat carries
# `-- doFinishSwitch` exactly twice and never for the chapter, and why the run ends
# on the client's own frozen loading screen.
#
# 386 initChapterTaskInfo 'pp' is registered by TASK-0180 for the v15
# DrawCardUnlock policy only.  The v9 policy itself still sends ZERO rows, because
# a row asserts that a specific chapter task of a specific chapter is
# received/completed and this archive has no observed row (D-21).  The one v15 row
# is a labelled synthetic_compatibility state whose only consumer is the client's
# own 祈灵 gate (pkgSystemFuncMgr.isFunctionOpen('drawcard') ->
# pkgChapterTaskMgr.isTaskCompleted(1001, 1)); see the policy's docstring and
# reports/task_0180_drawcard_main_entry.md.  It is also why the pair below is
# ordered -- 386's first statement indexes the very global 385 creates.  388
# updateChapterTask 'Np' is a per-task push with no producer here and stays
# unregistered.
#
# The family sits immediately BEFORE the chapter-item family above in the client's
# own registration order (385-388 then 389-392), which is why the v9 pair is sent
# immediately before the v7 pair.
S2C_REGISTRY.register(385, "initChapterTaskStart", "")
S2C_REGISTRY.register(386, "initChapterTaskInfo", "pp")
S2C_REGISTRY.register(387, "initChapterTaskEnd", "")
# Chapter (关卡) machine container (pkgScProtocolMgr lines 513-517; runtime ids
# 399/400/401/402/403, derived by analyze_pre_scene.protocol_table() -- the S2C side
# is past the dynamic battle insertions, so these are NOT the source ordinals).
#
# The THIRD container of the same finish callback, and the one RUN-0033 (TASK-0108)
# broke on -- one statement after the statement v9 cleared.  The chain is
# `pkgSceneMgr.lua:875 doFinishSwitch` -> `pkgChapterSceneMgr.lua:114
# le_finishCallBack` (RUN-0032 raised one statement earlier, at :112) ->
# `pkgChapterObjectMgr.lua:287 initScene` -> `pkgChapterMachineMgr.lua:312
# initAllMachineObj` -> `:46 isMachineCompleted`, whose own line 46 indexes the
# global UNGUARDED (`:45 local dChapterId = pkgChapterInfoMgr.getChapterId()`).
#
# `pkgChapterMachineMgr` declares `le_tbChapterMachineInfo = le_tbChapterMachineInfo
# or nil` (lines 6-7) and creates it in exactly ONE statement, but -- unlike the
# task and item families -- it does not go through `socket/nc/pkgGuankaMapNcMgr`:
# the module registers the handler ITSELF as `s2c.initChapterMachineStart`
# (`= {}`, lines 18-20), with the empty `s2c.initChapterMachineEnd` (27-29) closing
# the block.  No OTHER chunk of the shipped luaScript tree mentions the name at all:
# all 1827 chunks are walked for it
# (`analyze_chapter_machine_init.assert_whole_tree_writers`), so the unguarded read
# can only be cleared by the server running the client's own sequence.
#
# `doFinishSwitch` dispatches ENTER_SCENE at 880, calls `pkgSyncSceneMgr.enterScene`
# at 881, prints `-- doFinishSwitch` at 882 and tears the loading UI down at 884-890,
# all AFTER the 875 that raised -- which is why RUN-0033's whole logcat carries
# `-- doFinishSwitch` exactly twice and never for the chapter.
#
# 401 initChapterMachineInfo 'pp' stays deliberately UNREGISTERED: a row would
# assert that a named machine of a named chapter is completed, and this archive has
# no such fact (D-21).  It is also why the pair below is ordered -- 401's first
# statement indexes the very global 400 creates.  403 updateChapterMachine 'pp' is a
# per-machine push with no producer here and stays unregistered too.
#
# The family sits immediately AFTER the chapter-item family in the client's own
# registration order (389-393 then ... then 399-403), which is why the v10 pair is
# sent immediately after the v7 pair.
S2C_REGISTRY.register(400, "initChapterMachineStart", "")
S2C_REGISTRY.register(402, "initChapterMachineEnd", "")
# Prison-fight boss state (pkgScProtocolMgr.lua:1850,
# `registerS2C("NappNNppppp", "synPrisonFightBossToCg")`).  Runtime ID 1370 is
# derived by `analyze_pre_scene.protocol_table()` from the client's own
# registration order (ordinal 1226 + 144 dynamic battle/transfer insertions);
# the two real-device anchors S2C 283 `returnSceneInfo` (ordinal 139) and
# S2C 1500 `responseMonthSignData` (ordinal 1356) bracket it at the same +144.
#
# WHY IT IS REGISTERED.  Its handler in
# `luaScript/prison_fight/pkgS2CPrisonFightMgr.max` (source lines 12..30,
# params=12 = socket + 11 values) is the ONLY writer of `e_tbBossData` in the
# whole shipped luaScript tree.  The client model defaults to `or {}` with no
# field fallback, and `pkgPrisonFightModel.checkActOn` line 21 reads
# `startTime`/`endTime` unconditionally on EVERY PANEL_CLOSE: RUN-0054's alert
# confirm is destroyed first and the error then unwinds through
# `pkgEventMgr.dispatch`, so the chapter's own return-to-main-city callback
# never runs.  A login snapshot is the latest position that provably precedes
# the first panel close.
#
# VALUES.  All 11 values are fixed and provenance-labelled by the compatibility
# profile's PrisonFightBossSnapshot policy (v13,
# `PRISON_FIGHT_FIELD_TABLE` / `PRISON_FIGHT_PROVENANCE` in
# compatibility_profile.py).  The registry only fixes the id and the format; no
# value is assembled here (D-132).
#
# NEIGHBOURS.  The rest of the family -- S2C 1371 `synPrisonFightRoleToCg`,
# 1372 `synPrisonFightFriendShareStart`, 1373 `synPrisonFightFriendShareEnd`,
# 1374 `synPrisonFightFriendShareToCg` and 1375 `synPrisonFightPointToCg` --
# stays deliberately unregistered: none of them is read on the PANEL_CLOSE
# path, and registering one would assert prison-fight content this archive has
# no producer for (D-21).  C2S 672 / 681 stay unregistered too: the client only
# sends them from the prison-fight panel, which the closed state keeps
# unreachable.
S2C_REGISTRY.register(1370, "synPrisonFightBossToCg", "NappNNppppp")

# Card library. The client's own `pkgScProtocolMgr.lua:285-290` registers this
# family; the D-02 ids are derived by `analyze_pre_scene.protocol_table()`:
#   29 returnCardStart "" / 30 returnCardInfo "aNNNNNNNNaaaayNNNNNaa" /
#   31 returnCardEnd "".
# `pkgCardNcMgr` writes `pkgCardDataMgr.tbCard`; S2C 30 is the safe writer
# because it carries all 21 fields (including the four equip strings), unlike
# S2C 32 addCard whose reduced record can reach `insertEquipIndex2Card(nil)`.
# The login replay carries every stored card whose provenance is not a
# deterministic draw fixture; fixture rows stay behind the Owner draw-test
# overlay.  Recovered fight rewards (recovered_rewards.py) therefore persist
# across a normal launch.
S2C_REGISTRY.register(29, "returnCardStart", "")
S2C_REGISTRY.register(30, "returnCardInfo", "aNNNNNNNNaaaayNNNNNaa")
S2C_REGISTRY.register(31, "returnCardEnd", "")

# 图鉴 / 妖典 (cardDex).  `pkgScProtocolMgr.lua:972` registers
# `registerS2C("NNNNyy", "returnUpdateCardDex")`; D-02 places it at S2C 751.
# `pkgCardDexNcMgr` -> `pkgCardDexDataMgr.returnUpdateCardDex` writes
# `tbCardDex[toRawTempleteId(dTemplateId)]`; `bNewCard` dispatches NEW_CARD_DEX,
# which `pkgCardGetShowMgr.onNewCardGet` turns into the shipped new-card view.
# A row is emitted only for a template the account actually owns a card of.
S2C_REGISTRY.register(751, "returnUpdateCardDex", "NNNNyy")

# Card growth: split (C2S 218-220) and strength (C2S 14-17), with the replies the
# client's own lock partners expect.  All ids come from the same D-02 derivation
# (`analyze_pre_scene.protocol_table()`); the client handlers are
# `pkgCardSplitNcMgr`, `pkgCardNcMgr` and `pkgStrengthCardInfoMgr`.
#   23 returnOneValue_p "Np" / 24 returnOneValue_N "NN"  (wallet deltas)
#   33 delCard "a" / 34 updateCard "aNNNNNNNNaaaayNNNNNaa"
#   70 returnStrengthCardResult "ya"  (lock partner of strengthCardEnd)
#   81/82/83 returnGainInfoStart/End/Info  (split reward popup rows)
#   376 returnCardSplitResult "N"  (lock partner of splitCardEnd)
#   503 returnSingleSpecialCard "NN"  (dog-food count after strength)
#   504/505/506 special-card snapshot (start/end/info)
# `returnCardSplitResult` only carries `RESULT_TYPE`; the amounts the popup
# displays come from the gain-info family, so both are sent.  The snapshot is
# emitted only when the account owns a non-zero count, which keeps the default
# (empty) login stream byte-identical.
C2S_REGISTRY.register(14, "strengthCardStart", "a")
C2S_REGISTRY.register(15, "strengthCard", "a")
C2S_REGISTRY.register(16, "strengthDogFoodCard", "NN")
C2S_REGISTRY.register(17, "strengthCardEnd", "")
C2S_REGISTRY.register(218, "splitCardStart", "")
C2S_REGISTRY.register(219, "splitCard", "a")
C2S_REGISTRY.register(220, "splitCardEnd", "N")
S2C_REGISTRY.register(23, "returnOneValue_p", "Np")
S2C_REGISTRY.register(24, "returnOneValue_N", "NN")
S2C_REGISTRY.register(33, "delCard", "a")
S2C_REGISTRY.register(34, "updateCard", "aNNNNNNNNaaaayNNNNNaa")
S2C_REGISTRY.register(70, "returnStrengthCardResult", "ya")
S2C_REGISTRY.register(81, "returnGainInfoStart", "N")
S2C_REGISTRY.register(82, "returnGainInfoEnd", "")
S2C_REGISTRY.register(83, "returnGainInfo", "NNaN")
S2C_REGISTRY.register(376, "returnCardSplitResult", "N")
S2C_REGISTRY.register(503, "returnSingleSpecialCard", "NN")
S2C_REGISTRY.register(504, "returnSpecialCardStart", "")
S2C_REGISTRY.register(505, "returnSpecialCardEnd", "")
S2C_REGISTRY.register(506, "returnSpecialCardInfo", "NN")

# Card advance (进阶), C2S 18-21.  `pkgScProtocolMgr.lua:66-69` registers
#   advanceCardStart "" / advanceCard "a" / advanceAdvCard "N" /
#   advanceCardEnd "aN"
# and `:327` registers `returnAdvanceCardResult "ay"` with `advanceCardEnd` as
# its lock partner.  `pkgAdvanceCardMgr` (PROTO 0.2.0, source lines 155-171)
# sends one `advanceCard` per selected fodder instance, one `advanceAdvCard` per
# selected advance-card cfg id, and finally `advanceCardEnd(selectId,
# pkgSyncCounterMgr.getSyncCounter(TB_SYNC_COUNTER_TYPE.ADVANCE_CARD))`.
# `TB_SYNC_COUNTER_TYPE.ADVANCE_CARD = 110` is read from the shipped
# `pkgCommonConstMgr` (PROTO 0, source line 641).
# D-02 ids: C2S 18-21 are top-level source ordinals 18-21; S2C
# `returnAdvanceCardResult` is wire 71 (source ordinal 327, literal 70).
# The sync-counter pair is registered for the same reason the client handler
# exists: the server is what advances the counter the next request carries.
C2S_REGISTRY.register(18, "advanceCardStart", "")
C2S_REGISTRY.register(19, "advanceCard", "a")
C2S_REGISTRY.register(20, "advanceAdvCard", "N")
C2S_REGISTRY.register(21, "advanceCardEnd", "aN")
S2C_REGISTRY.register(71, "returnAdvanceCardResult", "ay")
S2C_REGISTRY.register(293, "returnSyncCounter", "NN")
S2C_REGISTRY.register(294, "updateSyncCounter", "NN")

# 祈灵 draw requests/results (same Owner overlay).  `pkgScProtocolMgr.lua`
# registers C2S 274 `startLotteryTicketDraw` ("NN") and C2S 33
# `startDiamondDrawCard` ("NNNN") with the S2C result families below; each
# `...ResultEnd` is the lock partner (15 s client lock).  The `a` result is one
# opaque card-instance id per packet, one packet per card; the End `N` is
# `pkgDiamondDrawConst.RESULT` (1 = SUCCESS).  The reconstructed probability
# model (D-148) produces the results; the deterministic fixture stays selectable.
C2S_REGISTRY.register(274, "startLotteryTicketDraw", "NN")
C2S_REGISTRY.register(33, "startDiamondDrawCard", "NNNN")
S2C_REGISTRY.register(537, "returnLotteryTicketDrawCardResultStart", "")
S2C_REGISTRY.register(538, "returnLotteryTicketDrawCardResult", "a")
S2C_REGISTRY.register(539, "returnLotteryTicketDrawCardResultEnd", "N")
S2C_REGISTRY.register(497, "returnDiamondDrawCardResultStart", "")
S2C_REGISTRY.register(498, "returnDiamondDrawCardResult", "a")
S2C_REGISTRY.register(499, "returnDiamondDrawCardResultEnd", "N")
S2C_REGISTRY.register(500, "syncDiamondDrawInfo", "NNN")
# Hot-spot/limited draw library.  Ids derive from `pkgScProtocolMgr`'s S2C
# registration order with the same +145 offset that reconciles the observed
# wire ids around this block (naive 869 -> id 1014; naive 903 -> id 1048;
# naive 1162 -> id 1307), so naive 1022..1026 -> 1167..1171.  The client's
# `pkgCardDataMgr.returnHotSpotDrawLibInfo` stores
# `(dCfgId, dStartTime, dEndTime, dTenDrawCount, dNotTargetCard)` and only
# activates a banner whose window contains the current server time.
S2C_REGISTRY.register(1167, "returnHotSpotDrawLibStart", "")
S2C_REGISTRY.register(1168, "returnHotSpotDrawLibInfo", "NNNNN")
S2C_REGISTRY.register(1169, "updateHotSpotDrawLibInfo", "NNNNN")
S2C_REGISTRY.register(1170, "returnHotSpotDrawLibEnd", "")
S2C_REGISTRY.register(1171, "updateHotSpotDrawLib", "NNN")
# Shop order validation answer (TASK-0243, Issue #26).  `pkgScProtocolMgr.lua:966`
# is `registerS2C("ppppa", "inspectOrderResult", "inspectOrderValid")` -- the
# third argument makes it the ONLY unlock of the C2S 411 lock-send.  The runtime
# id 745 = source ordinal 601 + the 144 dynamic battle/transfer S2C rows
# (`analyze_pre_scene.protocol_table()`, D-02); the neighbouring ids are
# 744 updateMarketDataList and 746 updateOneDayRechargeNum.
#
# The five values `(dResult, dMarketType, dId, dCost, strCustomOrderId)` go
# through `socket/nc/pkgMarketNcMgr` PROTO 0.15 (source 86-88) straight into
# `pkgRechargeCenterMgr.inspectOrderResult` (source 21-50), whose non-success
# branches raise `pkgUIAlertMgr.alert2` and return before `pkgPayMgr.payOrder`.
# This stub always sends such a refusal result; it never creates an order.
S2C_REGISTRY.register(745, "inspectOrderResult", "ppppa")

# ------------------------------------------------ bag goodsUse (TASK-0266)
#
# C2S 429 goodsUse "NNN" -- the bag panel's 使用 request.  The client's own
# registration is `zys_scAll__np__pkgScProtocolMgr.lua:1057`,
# `registerC2S("NNN", "goodsUse")`: source ordinal 339, and the 90 dynamic
# battle rows are inserted before it, so the wire id is 339 + 90 = 429 -- never
# counted from source text (D-02; `max_decoder/analyze_pre_scene.protocol_table()`
# and `reports/task_0265_bag_goods_use_contract.json` agree).
#
# WHAT SENDS IT.  Six recovered call sites (whole-corpus scan, payload count =
# CALL.B - 2), all carrying `(dType, dCfgId, dArg)`:
#   * `bag/pkgBagOtherView` source line 247 (bag 其他 tab, cfgGoods.use == 1);
#   * the same view line 254 (烟花, use == 1, guild-build scene);
#   * `bag/pkgUIBagOtherUseSel` line 92 (自选 sub-view, use == 2; dArg = argN);
#   * `shop/pkgUIBuyPowerMgr` line 128 (power page, dCfgId hard 0);
#   * `manor/manorFort/pkgUIManorFortBagItemTipView` lines 53/77 (杏黄旗, use == 5).
# Field 1 is a `pkgRewardConstMgr.TB_REWARD_TYPE` id (a cfgGoods key), not a row
# index; field 2 is 0 whenever the goods row *is* the item, which is every
# use == 1/2 row; field 3 is the chosen cfgGoods.argN slot only on use == 2.
# The client has no count field: one use = one item.  The registration is
# `registerC2S("NNN", "goodsUse")` -- no third argument -- so the send itself
# carries no lock, but the very next line (1058) is
# `registerS2C("NNN", "goodsUse", "goodsUse")`: the third argument is handed to
# `pkgLockSendMgr.regLockProto`, so C2S 429 IS a LOCK-SEND and S2C 820 is its
# ONLY unlock.  A missing 820 is not a no-op: the client armed
# `LOCK_TIME = 15000` ms and then runs `pkgConnectMgr.shutDown()`
# (`reconnect/lockSend/pkgLockSendMgr_unluac.lua` lines 7/40-46/48-53).
#
# TASK-0266 is the minimal faithful slice of the shipped cfgGoods table: item
# 376 花呗祈灵符 (`apk_unpacked/assets/config/goods/cfgGoods.max`: use = 1,
# arg1 = 376, autoAddBag = 1) whose cfgGoodsReward 376 row grants
# `(252 LOTTERY_TICKET, 1)`.  Both sides are RoleValue fields, so the effect is
# renderable at all: the item count `goodsUse6` (S2C 27 wire index 157, reward
# type 376) and the effect `lotteryTicket` (wire index 18, reward type 252)
# (`reports/role_value_wire_mapping.csv`:20,159).  EXP(98)/POWER(99) rows are
# refused: neither id is a RoleValue field (contract U-4).
#
# EVERY request gets exactly ONE S2C 820 -- success (dCode 1) or refusal
# (dCode 2) -- because two 820s would unlock twice and zero would shut the
# client's own socket down.  A refusal writes nothing and keeps the connection.
# The contract's unknowns travel on every event: U-1 (is the cfgGoods.argN
# grant a shipped client fact or only a config convention?), U-2 (what do dArg
# 1 and 2 mean? only the union is proved) and U-3 (is dCurNum the remaining or
# the used count?).  This build takes the conservative reading of each: grant
# arg1 for use == 1, send dArg = 1, and send the remaining count.
C2S_REGISTRY.register(429, "goodsUse", "NNN")
# S2C 820 goodsUse "NNN" -- the sole unlock of C2S 429 (see the row above).
# The client's own registration is `zys_scAll__np__pkgScProtocolMgr.lua:1058`,
# `registerS2C("NNN", "goodsUse", "goodsUse")`: source ordinal 676 + the 144
# dynamic battle/transfer S2C rows = 820 (contract `protocols.s2c_goods_use`).
# The handler `bag/pkgBagUtil.s2c.goodsUse(tbSocket, dCode, dArg, dCurNum)`
# (committed decompile `forensics_bag_authority/luaScript__bag__pkgBagUtil.lua`
# lines 103-118) is the whole consumer: dCode 1 toasts `bag_goods_use_succ`,
# and when dArg is 1 or 2 it also shows `pkgGainInfoMgr.getInfoAndClear(
# GOODS_USE)` and dispatches `GOODS_USE` with dCurNum (the bag table's refresh
# trigger); dCode 2 toasts `power_max_can_not_use_item` and refreshes nothing;
# dCode 502 is silent (U-6).  S2C 24 `returnOneValue_N` is what actually moves
# the two RoleValues into `roleInfo.v` -- dCurNum is only handed to the bag
# event -- so the success path sends 24(goodsUse6 item) and 24(lotteryTicket
# effect) BEFORE this row, then the popup pair 81(640)/83(252, 0, "", 1);
# 640 is `TB_GAIN_TYPE.GOODS_USE` from `zys_scAll/const/pkgCommonConstMgr`.
# A refusal sends this row alone with dCode 2, dArg 1 and the current count.
S2C_REGISTRY.register(820, "goodsUse", "NNN")


# --- 神器副本塔内入口 (TASK-0270, 2026-10-26) -------------------------------------
# Every id below is the client's own registration row, re-derived by
# max_decoder/analyze_artifact_tower.py (TASK-0264) and cross-checked in
# reports/task_0268_fabao_gate_verification.md (the S2C counter is ZERO-based; the derivation
# was calibrated against 1526/1527/1529/1531/1538/1543 and C2S 746/752/747).
#
# 45/46/47 are the fabao (法宝) push triple: 45 clears the inventory, 46 adds one instance
# (strInstanceId + cfgId + feature/level/exp + 8 attribute pairs = 21 payload values), 47 is
# a print-only terminator. All three register with two arguments => no lock partner and no
# C2S pair => server push only.
S2C_REGISTRY.register(45, "returnMagicStart", "")
S2C_REGISTRY.register(46, "returnMagicInfo", "a" + "N" * 20)
S2C_REGISTRY.register(47, "returnMagicEnd", "")
# 1529 opens the tower view; 1530 wipes/inserts one node; 1531 carries the per-layer path
# (26 registered N, the client binds 19 -> TASK-0264 U-F); 1538 carries the 18-value team.
S2C_REGISTRY.register(1529, "artifactDgnSetRace", "N")
S2C_REGISTRY.register(1530, "artifactDgnSendNode", "NNNN")
S2C_REGISTRY.register(1531, "artifactDgnSendPath", "N" * 26)
S2C_REGISTRY.register(1538, "artifactDgnSendTeam", "a")
# C2S 745 (the 18-value team CSV) and 746 (the chosen race) are the client's two commit
# requests from pkgArtifactDgnMod.sendTeamDataChange; 745 first, 746 only on first commit.
C2S_REGISTRY.register(745, "artifactDgnSetTeam", "a")
C2S_REGISTRY.register(746, "artifactDgnSetRace", "N")


# --- 秘境 uncharted 首通入口 (TASK-0272, 2026-10-26) -------------------------------
# ids/formats are the client's own registration rows, re-derived by
# max_decoder/analyze_uncharted_entry.py (TASK-0271, --check reproducible).
# C2S 244 is a LOCK-SEND: its third registration argument pairs it with the S2C 458 of the
# same name, so a missing 458 arms LOCK_TIME=15000 and the client shuts its own socket.
# C2S 260 has no S2C partner at all; it is recorded only.
C2S_REGISTRY.register(244, "unchartedEnter", "Ny")
C2S_REGISTRY.register(260, "syncUnchartedBuildingInfo", "")
# 434 carries the run identity (id, seed, instance, dead count, 3 booleans); 439/440/441 are
# the (layer, area, room) -> roomId matrix; 458 is the unlock and dStatus == 1 enters.
S2C_REGISTRY.register(434, "returnUnchartedCfg", "NNaNyyy")
S2C_REGISTRY.register(439, "returnUnchrtedDataStart", "")
S2C_REGISTRY.register(440, "returnUnchrtedDataProcess", "NNNN")
S2C_REGISTRY.register(441, "returnUnchrtedDataEnd", "")
S2C_REGISTRY.register(458, "unchartedEnter", "NN")


# --- 通天塔 战斗/跳层 (TASK-0273, 2026-10-26) --------------------------------------
# C2S 319 babelFight "NNN" is the skip button's request (pkgUIBabelLost); it registers with
# two arguments, so it has no lock partner. S2C 579 babelResult "N" is the authoritative
# result code (pkgBabel.c_result): 0 SUCC / 10 NOT_ENOUGH_QUAN / 20 FIGHT_FAIL / 30 FIGHT_SUCC
# / 40 SKIP_SUCC / 50 REALM_SUCC.
C2S_REGISTRY.register(319, "babelFight", "NNN")
S2C_REGISTRY.register(579, "babelResult", "N")


# --- 神器副本塔内首战入口 (TASK-0278, 2026-10-26) ----------------------------------
# C2S 752 artifactDgnEnemyTeamInfo "NNNN" (race, tower, layer, sn): the node tap. Two-argument
# registration => no lock partner. S2C 1543 artifactDgnEnemyTeamInfo "NNNNaa" answers it; the
# strTeam "default" branch makes the client start the battle with C2S 69 (PVETYPE_GOD_FIGHT=66).
C2S_REGISTRY.register(752, "artifactDgnEnemyTeamInfo", "NNNN")
S2C_REGISTRY.register(1543, "artifactDgnEnemyTeamInfo", "NNNNaa")
# C2S 747 artifactDgnDoEvt "NNNNNNN": the team-info confirm button's request
# (pkgArtifactDgnMgr.doFight -> c2s.artifactDgnDoEvt(race, tower, layer, sn, sync)).
# Two-argument registration => no lock partner. RUN9 proved the client sends it on the
# 1543 reply's team branch; S2C 1539 artifactDgnFight "NN" (layer, sn) is the client's
# own s2c.artifactDgnFight handler, which starts the fight with C2S 69.
C2S_REGISTRY.register(747, "artifactDgnDoEvt", "NNNNNNN")
S2C_REGISTRY.register(1539, "artifactDgnFight", "NN")


# --- 神器副本：入口种族/塔与成就积分 (TASK-0282, 2026-10-26) -------------------------
# S2C 1551 artifactDgnAchInfo "NNa" is the ONLY writer of the entry page's score
# (pkgArtifactDgnAchievement.le_dScore); the client has no request for it, so the server must
# push it. Without it pkgUIArtifactDgnEnter:170 concatenates nil.
S2C_REGISTRY.register(1551, "artifactDgnAchInfo", "NNa")
S2C_REGISTRY.register(1552, "artifactDgnAchUpdateScore", "N")

# S2C 1537 artifactDgnSendCard "NNaNNa": the ONLY writer of le_tbCards, which is what the tower's
# member counter reads (TASK-0291 contract). dCode=1 clears, dCode=2 inserts one row keyed by the
# same slot token the 1538 team CSV carries. Without it the tower shows 队伍 0/0.
S2C_REGISTRY.register(1537, "artifactDgnSendCard", "NNaNNa")

# --- 背包「其他」页 / 进阶碎片 (TASK-0312, 2026-10-26) ---------------------------------
# tbAdvPiece lives in the pkgAdvanceMaterialInfo NAMESPACE (nameSpace.max:11 setfenv), and the
# cfg manager's own table can never reach it, so the bag's 其他 page raises ipairs/pairs on nil
# until a packet fills it. The only writer is S2C 287 returnAdvPieceStart (no
# arguments).  TASK-0442: the client's own registration resolves these names to
# 287/288/289/291, not the earlier 276/277/278 ordinal+144 guess; sending the
# guessed ids made the client drop the connection.
S2C_REGISTRY.register(287, "returnAdvPieceStart", "")
S2C_REGISTRY.register(288, "returnAdvPieceInfo", "NN")
S2C_REGISTRY.register(289, "returnAdvPieceEnd", "")
S2C_REGISTRY.register(291, "returnSingleAdvPiece", "NN")
# 754 is artifactDgnCheckSoul, NOT talent: pkgScProtocolMgr.max:2790 registerC2S("NNNN", ...),
# sent with no arguments by pkgArtifactDgnMgr.max:982. Registering it stops strict mode from
# dropping the connection on it (RUN16 hit exactly that).
C2S_REGISTRY.register(754, "artifactDgnCheckSoul", "NNNN")

# --- 装备/神兵实例 (TASK-0318, 2026-10-26) ---------------------------------------------
# The only remaining legal combat levers: getCardFight = cfg.fight x ratio + equip + godWeapon.
# These ids are the same ordinal+144 inference that produced the wrong 276/277/278, so the block
# is OFF by default and one device run is its acceptance test.
S2C_REGISTRY.register(1264, "returnEquipStart", "")
S2C_REGISTRY.register(1265, "returnEquipInfo", "aNNNNyNNy" + "N" * 35)
S2C_REGISTRY.register(1266, "returnEquipEnd", "")
S2C_REGISTRY.register(1621, "returnGodWeaponStart", "")
S2C_REGISTRY.register(1622, "returnGodWeaponInfo", "aNNNNyyNNNNN")
S2C_REGISTRY.register(1623, "returnGodWeaponEnd", "")
S2C_REGISTRY.register(1625, "returnAddSingleGodWeapon", "aNNNNyyNNNNN")

# S2C 1532 artifactDgnDoEvt "NNNNNNN": the answer to a NON-battle node tap (dIsFight=0). Without
# it every 747 was answered with 1539 artifactDgnFight, whose client handler starts a type-66
# battle -- so tapping a chest, hot spring or hiding spot started a fight (TASK-0317 finding).
S2C_REGISTRY.register(1532, "artifactDgnDoEvt", "NNNNNNN")

# --- 秘境 building sync (TASK-0323, 2026-10-26) -------------------------------------
# pkgUnchartedData:702 does pairs(le_tbUnchartedBuilding), and that table is only created by
# S2C 484 syncUnchartedBuildingInfoStart -- without it the select view's init aborts (RUN24)
# and every 进入 button stays dead.
S2C_REGISTRY.register(484, "syncUnchartedBuildingInfoStart", "")
S2C_REGISTRY.register(485, "syncUnchartedBuildingInfo", "NNppNN")
S2C_REGISTRY.register(486, "syncUnchartedBuildingInfoEnd", "")

# --- 秘境 completed bracket (TASK-0326) ------------------------------------------------
# pkgUnchartedData:644 IsEventOver indexes le_tbUnchartedCompleted, which ONLY
# returnUnchrtedCompletedStart creates; without it the scene preload dies (RUN27).
S2C_REGISTRY.register(442, "returnUnchrtedCompletedStart", "")
S2C_REGISTRY.register(443, "returnUnchrtedCompletedProcess", "NNNN")
S2C_REGISTRY.register(444, "returnUnchrtedCompletedEnd", "")

# --- 秘境 scene brackets (TASK-0327) ---------------------------------------------------
# createPlayerObj reads GetSpeedBuffValue -> pairs(le_tbUnchartedBuff), which only
# returnUnchrtedBuffStart creates; the scene froze without it (RUN28). The task and npc-hp
# brackets are the same shape and their own globals are read before a nil guard.
S2C_REGISTRY.register(446, "returnUnchrtedBuffStart", "")
S2C_REGISTRY.register(447, "returnUnchrtedBuffProcess", "NN")
S2C_REGISTRY.register(448, "returnUnchrtedBuffEnd", "")
S2C_REGISTRY.register(450, "returnUnchrtedTaskStart", "")
S2C_REGISTRY.register(451, "returnUnchrtedTaskProcess", "NNNNN")
S2C_REGISTRY.register(452, "returnUnchrtedTaskEnd", "")
S2C_REGISTRY.register(454, "returnUnchrtedNpcHpRecordStart", "")
S2C_REGISTRY.register(455, "returnUnchrtedNpcHpRecordProcess", "NNNNNa")
S2C_REGISTRY.register(456, "returnUnchrtedNpcHpRecordEnd", "")

# --- 秘境 unlock-area bracket (TASK-0328) ----------------------------------------------
# pkgUnchartedData:652 IsAreaUnlocked indexes le_tbUnchartedUnlockArea, which only
# returnUnchartedUnlockAreaStart creates; without it the in-UI small map builds no room nodes
# (RUN31). One row (1,1) marks the first area unlocked under either field reading.
S2C_REGISTRY.register(435, "returnUnchartedUnlockAreaStart", "")
S2C_REGISTRY.register(436, "returnUnchartedUnlockAreaProcess", "NN")
S2C_REGISTRY.register(437, "returnUnchartedUnlockAreaEnd", "")

# --- 秘境 boss select (TASK-0330) ------------------------------------------------------
# returnUnchartedBossList is the ONLY writer of le_tbUnchartedBossList (tbBoss); without it the
# boss-select view raises at :161 and never binds its buttons (RUN33).
S2C_REGISTRY.register(468, "returnUnchartedBossList", "NNN")
S2C_REGISTRY.register(469, "returnUnchartedBossUnlocked", "yyy")
S2C_REGISTRY.register(470, "returnUnchartedBossSelected", "N")
S2C_REGISTRY.register(471, "unchartedBossSelected", "NNNNNNN")

# --- zero-field C2S seen on device but unregistered (TASK-0331) -------------------------
# A strict session CLOSES the socket on an unknown id (codec.UnknownProtocol: without a format
# there is no trustworthy packet boundary), which is what killed RUN34. Both ids are empty-format
# records from the client's own table (ordinal+89 calibration: 155 -> C2S 244 unchartedEnter).
C2S_REGISTRY.register(525, "requestConstructionGamePublicCardLib", "")
C2S_REGISTRY.register(698, "getRechargeDailyJson", "")

C2S_REGISTRY.register(123, "signIn", "")
C2S_REGISTRY.register(124, "makeUpSignIn", "")
C2S_REGISTRY.register(127, "refreshSign7TimeOpenOrLeft", "")
C2S_REGISTRY.register(130, "requestPowerGiftTime", "")
C2S_REGISTRY.register(131, "acceptPowerGift", "")
C2S_REGISTRY.register(152, "extractMailStart", "")
C2S_REGISTRY.register(155, "deleteMailStart", "")
C2S_REGISTRY.register(126, "refreshSign7Time_v126", "")  # TASK-0332: client-table id (name already taken at 37)
C2S_REGISTRY.register(128, "getSignReward_v128", "")  # TASK-0332: client-table id (name already taken at 39)
C2S_REGISTRY.register(255, "unchartedBossSelected", "NNNNNNp")  # TASK-0333: RUN35 caught it on the wire; unregistered -> strict socket close

# --- zero-field C2S inventory (TASK-0334) -----------------------------------------------
# The client ships 718 C2S ids; 176 take no fields. A strict session CLOSES the socket on an
# unregistered id, which cost RUN34 (525/698) and RUN35 (255). These are RECORDS only: no reply,
# because none of them has a lock partner here. Deliberately excluded: ids the test suite pins as
# unregistered (24/394/395/656/672/681/725) and any id or name already present.
C2S_REGISTRY.register(97, "requestCmd", "")
C2S_REGISTRY.register(132, "startStamp", "")
C2S_REGISTRY.register(134, "endStamp", "")
C2S_REGISTRY.register(136, "battleC2S75", "")
C2S_REGISTRY.register(142, "legacyFairyWayOpenShop", "")
C2S_REGISTRY.register(145, "legacyFairyWayOpenMainUI", "")
C2S_REGISTRY.register(173, "requestLadderInfo", "")
C2S_REGISTRY.register(174, "cancelLadderMatch", "")
C2S_REGISTRY.register(175, "removeLadderOnLine", "")
C2S_REGISTRY.register(176, "requestLadderRankList", "")
C2S_REGISTRY.register(177, "requestLadderLocalRankList", "")
C2S_REGISTRY.register(178, "requestLadderSelfRankList", "")
C2S_REGISTRY.register(181, "requestBetList", "")
C2S_REGISTRY.register(182, "requestMonsterData", "")
C2S_REGISTRY.register(184, "requestMonsterRaceRankList", "")
C2S_REGISTRY.register(186, "requestMonsterRaceReadyTime", "")
C2S_REGISTRY.register(188, "signupCampBattle", "")
C2S_REGISTRY.register(189, "enterCampBattle", "")
C2S_REGISTRY.register(190, "cancelSignupCampBattle", "")
C2S_REGISTRY.register(191, "requestCampBattleRankData", "")
C2S_REGISTRY.register(192, "requestCampBattleData", "")
C2S_REGISTRY.register(193, "requestCampBattleReadyTime", "")
C2S_REGISTRY.register(194, "requestCampBattleGuildData", "")
C2S_REGISTRY.register(240, "refreshFriendInfo", "")
C2S_REGISTRY.register(243, "getFriendBattleInfo", "")
C2S_REGISTRY.register(267, "openBounty", "")
# TASK-0447: 悬赏榜空索引（pkgBountyNcMgr.returnBountyTaskIndex*），让 NPC 打开
# 而不是等一个永远不会来的回包。
S2C_REGISTRY.register(507, "returnBountyTaskIndexStart", "pp")
S2C_REGISTRY.register(508, "returnBountyTaskIndex", "Ny")
S2C_REGISTRY.register(509, "returnBountyTaskIndexEnd", "")
C2S_REGISTRY.register(268, "addAdvanceTask", "")
C2S_REGISTRY.register(276, "learnCardCutTalentStart", "")
C2S_REGISTRY.register(281, "levelUpCardCutTalentStart", "")
C2S_REGISTRY.register(290, "getCityChallengeData", "")
C2S_REGISTRY.register(293, "resetCityChallengeFailTime", "")
C2S_REGISTRY.register(305, "getGemChallengeNum", "")
C2S_REGISTRY.register(307, "getAllGemChallengeScoreData", "")
C2S_REGISTRY.register(314, "getOpenActivityDay", "")
C2S_REGISTRY.register(316, "getRecommendFriend", "")
C2S_REGISTRY.register(324, "achieveAll", "")
C2S_REGISTRY.register(326, "achieveLevelTaskAccept", "")
C2S_REGISTRY.register(327, "achieveLevelTaskGiveup", "")
C2S_REGISTRY.register(335, "guildGardenEnter", "")
C2S_REGISTRY.register(336, "guildGardenLeave", "")
C2S_REGISTRY.register(360, "guildBuyRename", "")
C2S_REGISTRY.register(364, "guildBossPanel", "")
C2S_REGISTRY.register(376, "getLockTowerDefaultCard", "")
C2S_REGISTRY.register(377, "enterLockTower", "")
C2S_REGISTRY.register(378, "getTowerInfo", "")
C2S_REGISTRY.register(397, "dailyTaskOpen", "")
# TASK-0448: 日常任务（洛修/夏昭）打开时的 S2C 快照族。
S2C_REGISTRY.register(699, "dailyTaskInfo", "NNN")
S2C_REGISTRY.register(700, "dailyTaskGet", "N")
S2C_REGISTRY.register(701, "dailyTaskClean", "")
S2C_REGISTRY.register(702, "dailyTaskOpen", "")
S2C_REGISTRY.register(703, "dailyTaskRed", "N")
C2S_REGISTRY.register(404, "monopolyHelp", "")
C2S_REGISTRY.register(195, "refineCard", "aNN")
C2S_REGISTRY.register(196, "replaceCardSkill", "aNN")
C2S_REGISTRY.register(197, "refineComposite", "NNN")
C2S_REGISTRY.register(309, "refineQuit", "a")
C2S_REGISTRY.register(310, "refineSave", "aNN")
C2S_REGISTRY.register(311, "refineReplaceSave", "aN")
C2S_REGISTRY.register(624, "changeCareerSkinFromCg", "N")
C2S_REGISTRY.register(625, "changeAllCardSkinFromCg", "NN")
C2S_REGISTRY.register(626, "changeOneCardSkinFromCg", "aN")
C2S_REGISTRY.register(410, "receiveFirstRechargeReward", "")
C2S_REGISTRY.register(423, "requestGuildCompetitionInfo", "")
C2S_REGISTRY.register(424, "enterGuildCompetition", "")
C2S_REGISTRY.register(426, "requestGuildCompetitionRankList", "")
C2S_REGISTRY.register(437, "switchManorScene", "")
C2S_REGISTRY.register(439, "leaveManorScene", "")
C2S_REGISTRY.register(449, "sendManorTeamReliveOneMinute", "")
C2S_REGISTRY.register(468, "reqManorGuildMembers", "")
C2S_REGISTRY.register(474, "manorFakeBattleGuide", "")
C2S_REGISTRY.register(478, "requestManorFieldMonsterTeam", "")
C2S_REGISTRY.register(481, "requestManorCatchedFieldMonster", "")
C2S_REGISTRY.register(483, "requestFieldMonsterBattleTimes", "")
C2S_REGISTRY.register(487, "switchSyncScene", "")
C2S_REGISTRY.register(489, "leaveSyncScene", "")
C2S_REGISTRY.register(491, "qgHandLeaveChapter", "")
C2S_REGISTRY.register(500, "sendGetACData", "")
C2S_REGISTRY.register(518, "cancelPreMathch", "")
C2S_REGISTRY.register(520, "startPreMatch", "")
C2S_REGISTRY.register(521, "requestConstructionGamePreInfo", "")
C2S_REGISTRY.register(522, "requestConstructionGamePlayerTeam", "")
C2S_REGISTRY.register(524, "requestConstructionGameBattleInfo", "")
C2S_REGISTRY.register(526, "requestConstructionGameActivityStatus", "")
C2S_REGISTRY.register(527, "removeConstructionGameOnline", "")
C2S_REGISTRY.register(529, "sendPreMatchPlayerList", "")
C2S_REGISTRY.register(531, "getConstructionGamePreMatchRank", "")
C2S_REGISTRY.register(533, "requestPreMatchIntegralInfo", "")
C2S_REGISTRY.register(534, "goInConstructionGameActivity", "")
C2S_REGISTRY.register(535, "goOutConstructionGameActivity", "")
C2S_REGISTRY.register(536, "requsetConstructionGamePreWaitOnLine", "")
C2S_REGISTRY.register(537, "buyConstructionGamePreLife", "")
C2S_REGISTRY.register(540, "requestConstructionGameFinalsInfo", "")
C2S_REGISTRY.register(542, "GMStartFinalsMatch", "")
C2S_REGISTRY.register(551, "manorFortTradeCancel", "")
C2S_REGISTRY.register(552, "requestManorFortTradeInfo", "")
C2S_REGISTRY.register(553, "manorFortTradeReceive", "")
C2S_REGISTRY.register(554, "castleCoreSynInfo", "")
C2S_REGISTRY.register(570, "manorRoleExploitRankGet", "")
C2S_REGISTRY.register(572, "requestRoomInfo", "")
C2S_REGISTRY.register(575, "requestRecommandRoomList", "")
C2S_REGISTRY.register(578, "cancelRoomChallenge", "")
C2S_REGISTRY.register(581, "requestSelfRoomInfo", "")
C2S_REGISTRY.register(586, "requestRoomTeamDataChange", "")
C2S_REGISTRY.register(590, "koiActivityExpGet", "")
C2S_REGISTRY.register(591, "koiActivityLevelRewardOneKeyGet", "")
C2S_REGISTRY.register(594, "getMonthFreeGift", "")
C2S_REGISTRY.register(596, "getRechargeReturn", "")
C2S_REGISTRY.register(597, "getLeaderboardMount", "")
C2S_REGISTRY.register(598, "get3rdTest", "")
C2S_REGISTRY.register(599, "getPreRegCard", "")
C2S_REGISTRY.register(601, "sevenDayHappyRefreshBoss", "")
C2S_REGISTRY.register(603, "requestSevenDayHappyHelpList", "")
C2S_REGISTRY.register(605, "inviteWorld2FightBoss", "")
C2S_REGISTRY.register(612, "changeSevenDayHappyHelpList", "")
C2S_REGISTRY.register(617, "cardLearnTeam", "")
C2S_REGISTRY.register(627, "requestAllSkinsDataFromCg", "")
C2S_REGISTRY.register(634, "equipSplitStart", "")
C2S_REGISTRY.register(663, "challengeBossActInfo", "")
C2S_REGISTRY.register(664, "challengeBossGetInfo", "")
C2S_REGISTRY.register(666, "challengeBossReset", "")
C2S_REGISTRY.register(667, "challengeBossGetRank", "")
C2S_REGISTRY.register(690, "requestTradeRecords", "")
C2S_REGISTRY.register(693, "requestTradeOnSaleItem", "")
C2S_REGISTRY.register(695, "requestRechargeDaily", "")
C2S_REGISTRY.register(700, "requestMergeServerActivityTime", "")
C2S_REGISTRY.register(701, "requestMergeServerActivityRewardDay", "")
C2S_REGISTRY.register(705, "callRabit", "")
C2S_REGISTRY.register(706, "getBitRabbitSquare", "")
C2S_REGISTRY.register(708, "requestFeedRankList", "")
C2S_REGISTRY.register(709, "guildBeastLogRequest", "")
C2S_REGISTRY.register(712, "requestGuildBeastInfo", "")
C2S_REGISTRY.register(713, "requestGuildBeastSkill", "")
C2S_REGISTRY.register(714, "requestWakeUpGuildBeast", "")
C2S_REGISTRY.register(715, "requestGuildBeastPlayStage", "")
C2S_REGISTRY.register(716, "requestGuildPigInfo", "")
C2S_REGISTRY.register(717, "requestGuildSmallPig", "")
C2S_REGISTRY.register(721, "checkSgSvrConnect", "")
C2S_REGISTRY.register(729, "registerConstructionGameEquipStart", "")
# TASK-0431: the draw panel sends its auto-split preference (C2S 728) after a
# draw result; the client's own registration carries no S2C response, and the
# D-08 strict decoder killed the connection with "unknown protocol id 728"
# until this row existed.  The handler only records it (no response).
C2S_REGISTRY.register(728, "setDrawCardAutoSplit", "N")
# TASK-0434: the 天赋/元魂珠 UI sends these three lock-sends.  Their partners
# are S2C 554/555/557; without a handler the client stays locked (or the strict
# decoder drops the connection).
C2S_REGISTRY.register(275, "learnTalent", "NN")
C2S_REGISTRY.register(280, "levelupTalent", "NN")
C2S_REGISTRY.register(284, "unlockTalentSkill", "NN")
C2S_REGISTRY.register(286, "resetTalent", "NN")
C2S_REGISTRY.register(288, "addTalentPoint", "N")
C2S_REGISTRY.register(301, "switchTalentIndex", "N")
C2S_REGISTRY.register(302, "switchTalentIndex2", "N")
C2S_REGISTRY.register(285, "activeSoulTalent", "NN")
# TASK-0436: NPC 对话/接任务。客户端 pkgNpcTaskMgr 的 le_taskDict 只由
# S2C 567/568/569 填充；服务端原来不存也不回放，所以每次登录 NPC 都重新对话。
C2S_REGISTRY.register(294, "receiveNpcTask", "N")
S2C_REGISTRY.register(566, "updateSingleNpcTask", "NN")
S2C_REGISTRY.register(567, "returnNpcTaskStart", "")
S2C_REGISTRY.register(568, "returnNpcTaskData", "NN")
S2C_REGISTRY.register(569, "returnNpcTaskEnd", "")
C2S_REGISTRY.register(287, "selectSoulSkill", "NN")
C2S_REGISTRY.register(289, "changeTalentSoulShowMode", "NN")
# TASK-0432: entering 长安城 sends C2S 264 updateSceneType(n) and C2S 43
# startStamp(""); the client registers neither an S2C response nor a lock for
# them.  Unregistered, the strict decoder answered "unknown protocol id
# 264/43" and dropped the connection, which the player saw as the city crash.
C2S_REGISTRY.register(43, "startStampScene", "")
C2S_REGISTRY.register(264, "updateSceneType", "n")
# C2S 262 syncOnlinePlayers is the second half of the same scene switch (the
# client sends updateSceneType then syncOnlinePlayers).  Its S2C partner
# syncOnlinePlayersEnd has an empty body and starts the 40 s refresh timer;
# a local single-player session answers it later, but the packet must decode
# first or the strict D-08 path drops the city connection.
C2S_REGISTRY.register(262, "syncOnlinePlayers", "nn")
C2S_REGISTRY.register(731, "registerConstructionGameEquipEnd", "")
C2S_REGISTRY.register(732, "registerConstructionGameGodWeaponStart", "")
C2S_REGISTRY.register(734, "registerConstructionGameGodWeaponEnd", "")
C2S_REGISTRY.register(737, "requestApply", "")
C2S_REGISTRY.register(760, "requestChristmasActivityInfo", "")
C2S_REGISTRY.register(766, "cancelChristmasActivityTeamMatch", "")
C2S_REGISTRY.register(767, "exitChristmasActivityTeam", "")
C2S_REGISTRY.register(769, "getChristmasActivityInviteList", "")
C2S_REGISTRY.register(770, "getChristmasActivityPlayerCanInviteList", "")
C2S_REGISTRY.register(771, "exitChristmasActivity", "")
C2S_REGISTRY.register(772, "requestChristmasActivityRewardInfo", "")
C2S_REGISTRY.register(773, "requestChristmasActivityRankData", "")
C2S_REGISTRY.register(776, "getMemberRank", "")
C2S_REGISTRY.register(777, "getGuildRank", "")
C2S_REGISTRY.register(778, "getGuildRedEnvelope", "")
C2S_REGISTRY.register(779, "newYearDraw", "")
C2S_REGISTRY.register(780, "newYearDrawCheckOpen", "")
C2S_REGISTRY.register(781, "newYearDrawBuyTicket", "")
C2S_REGISTRY.register(783, "newYearDrawDoRewardEvent", "")
C2S_REGISTRY.register(790, "godWeaponLevelupStart", "")
C2S_REGISTRY.register(795, "gwSplitStart", "")
C2S_REGISTRY.register(801, "requestWorldBossInfo", "")
C2S_REGISTRY.register(802, "requestWorldBossPlayerInfo", "")
C2S_REGISTRY.register(803, "requestWorldBossActivityIndex", "")
C2S_REGISTRY.register(804, "requestWorldBossRank", "")
C2S_REGISTRY.register(805, "requestWorldBossSelfRank", "")

# --- tower node interactions (TASK-0352) ------------------------------------------------
# 749 artifactDgnGoNext is the button the player pressed to open the next layer; an unregistered
# id closes a strict session (live 2026-10-27 00:28). 748/750/751/753 are the other node
# interactions (relic pick, carriage card pick, goods use) and are recorded for the same reason.
C2S_REGISTRY.register(748, "artifactDgnDoSelYiwu", "NN")
C2S_REGISTRY.register(749, "artifactDgnGoNext", "NN")
C2S_REGISTRY.register(750, "artifactDgnSelCard", "NNNNN")
C2S_REGISTRY.register(751, "artifactDgnDoSelCard", "NNN")
C2S_REGISTRY.register(753, "artifactDgnUseGoods", "NNNN")

# --- tower carriage card offer (TASK-0365) ----------------------------------------------
# C2S 750 asks for the offer; the answer streams S2C 1541 rows (code 1 start / 2 row /
# 4 end) and the pick result is S2C 1542. Without these the handler raised KeyError and
# took the connection with it.
S2C_REGISTRY.register(1541, "artifactDgnSelCard", "NNaNNNa")
S2C_REGISTRY.register(1542, "artifactDgnDoSelCard", "NN")
# TASK-0370: S2C 32 addCard pushes one owned card to the client (aNNNNNNNNaaaaa).
S2C_REGISTRY.register(32, "addCard", "aNNNNNNNNaaaaa")
S2C_REGISTRY.register(540, "returnTalentStart", "")
S2C_REGISTRY.register(541, "returnTalent", "NN")
S2C_REGISTRY.register(542, "returnTalentEnd", "")
S2C_REGISTRY.register(543, "returnTalentLearnResult", "NN")
S2C_REGISTRY.register(544, "returnTalentLevelupResult", "NN")
S2C_REGISTRY.register(545, "returnTalentPointStart", "")
S2C_REGISTRY.register(547, "returnTalentPointEnd", "")
S2C_REGISTRY.register(548, "returnTalentCardCut", "a")
S2C_REGISTRY.register(549, "returnNodeSkill", "a")
S2C_REGISTRY.register(550, "returnOneNodeSkill", "NN")
S2C_REGISTRY.register(558, "returnTalentPointReward", "N")
S2C_REGISTRY.register(560, "talentSet", "NNNNNN")
S2C_REGISTRY.register(551, "returnTalentSkill", "a")
S2C_REGISTRY.register(552, "returnOneTalentSkill", "aN")
S2C_REGISTRY.register(556, "returnTalentResetTimes", "N")
S2C_REGISTRY.register(559, "returnUnlockTalentSkillFail", "N")
S2C_REGISTRY.register(561, "returnTalentName", "aaaaaN")
S2C_REGISTRY.register(573, "returnTalentSwitchEnd", "")
S2C_REGISTRY.register(546, "returnTalentPoint", "NN")
S2C_REGISTRY.register(553, "returnTalentSoulNode", "N")
S2C_REGISTRY.register(557, "returnTalentSoulShowMode", "N")

# TASK-0394: the card-piece (碎片) container -- S2C 75 one entry, 72/73/74 the full list.
S2C_REGISTRY.register(72, "returnReStart", "")
S2C_REGISTRY.register(73, "returnReInfo", "NN")
S2C_REGISTRY.register(74, "returnReEnd", "")
S2C_REGISTRY.register(75, "returnSingleRe", "NN")

# TASK-0396: artifact-dungeon shop (contract task_0392).  756/757 are LOCK-SENDs, so an
# unanswered buy freezes the UI for 15 s and then shuts the socket -- they must be answered.
C2S_REGISTRY.register(755, "artifactDgnShopSelect", "NNNNNN")
C2S_REGISTRY.register(756, "artifactDgnShopBuy", "NNNN")
C2S_REGISTRY.register(757, "artifactDgnShopRefresh", "NNN")
C2S_REGISTRY.register(758, "artifactDgnShopReset", "NNNNN")
S2C_REGISTRY.register(1546, "artifactDgnShopShopOpen", "NNNNNNNNNNNNNN")
S2C_REGISTRY.register(1547, "artifactDgnShopShop", "NNNNN")
S2C_REGISTRY.register(1548, "artifactDgnShopBuy", "NN")
S2C_REGISTRY.register(1549, "artifactDgnShopUpdate", "NN")
S2C_REGISTRY.register(1550, "artifactDgnShopRedPoint", "NNNNN")

# TASK-0402: the relic (遗物) container and the reagent-use reply.  Ids come from the repo
# analyzer (analyze_pre_scene.protocol_table), not a line-count guess.
S2C_REGISTRY.register(1533, "artifactDgnYiwu", "NN")
S2C_REGISTRY.register(1534, "artifactDgnSelYiwu", "NN")
S2C_REGISTRY.register(1535, "artifactDgnDoSelYiwu", "NN")
S2C_REGISTRY.register(1544, "artifactDgnUseGoods", "NNNN")

# TASK-0405: the remaining dungeon entries.  An UNREGISTERED C2S closes the socket, so every
# entry button the player can tap needs at least a registered boundary (formats from
# analyze_pre_scene.protocol_table).
C2S_REGISTRY.register(46, "startFairyWay", "N")
C2S_REGISTRY.register(330, "dgnEnter", "N")
C2S_REGISTRY.register(386, "topTowerOpen", "NNNNN")
C2S_REGISTRY.register(669, "roomDungeonEnter", "ap")
C2S_REGISTRY.register(681, "sendGetPrisonFightData", "NNNN")

# TASK-0412: the 挑战BOSS dungeon (contract task_0406).  663/664 open the view, 665 claims a
# chest, 666 resets; the fight itself is the ordinary PVE pair (69 / 89) with pve type 71.
C2S_REGISTRY.register(665, "challengeBossGetReward", "N")
C2S_REGISTRY.register(668, "requestChallengeBossRoleTeam", "p")
S2C_REGISTRY.register(1350, "returnChallengeBossData", "NNNNaaN")
S2C_REGISTRY.register(1351, "returnChallengeBossActInfo", "NNN")
S2C_REGISTRY.register(1352, "returnChallengeBossGetReward", "N")
S2C_REGISTRY.register(1353, "returnChallengeBossReset", "N")
S2C_REGISTRY.register(1354, "returnChallengeBossRankStart", "a")
S2C_REGISTRY.register(1355, "returnChallengeBossRank", "pNNNNNNa")
S2C_REGISTRY.register(1356, "returnChallengeBossRankEnd", "")
S2C_REGISTRY.register(1357, "requestChallengeBossRoleTeam", "pa")
S2C_REGISTRY.register(1358, "challengeBossGmChallengeState", "N")

# TASK-0415: 城市挑战 (contract task_0413).  The view never asks for data -- C2S 290 has no
# sender at all -- so the server pushes S2C 562 after the reset request and after a win.
C2S_REGISTRY.register(291, "getCardReward", "NNN")
C2S_REGISTRY.register(292, "getIndexReward", "NN")
S2C_REGISTRY.register(562, "returnCityChallengeData", "aaNNNNNN")
S2C_REGISTRY.register(563, "returnCardReward", "NNNy")
S2C_REGISTRY.register(564, "returnIndexReward", "NNy")

# TASK-0416: 锁妖塔 (contract task_0414).  The fight is the ordinary PVE pair with
# pveOrPvpType 93; the floor travels in p1 of C2S 69 and S2C 89 carries the stage id.
C2S_REGISTRY.register(379, "getLockTowerRanking", "N")
C2S_REGISTRY.register(380, "changeCardOrMagic", "N")
C2S_REGISTRY.register(381, "selectTowerReward", "NNNN")
S2C_REGISTRY.register(652, "returnLockTowerDefaultCardStart", "")
S2C_REGISTRY.register(653, "returnLockTowerDefaultCard", "aNNN")
S2C_REGISTRY.register(654, "returnLockTowerDefaultCardEnd", "yNNNNNN")
S2C_REGISTRY.register(655, "enterLockTower", "Ny")
S2C_REGISTRY.register(656, "returnTowerInfoStart", "")
S2C_REGISTRY.register(657, "returnTowerCardInfo", "NaNNNN")
S2C_REGISTRY.register(658, "returnTowerMagicInfo", "NaNNNNNNNNNNNNNNNNNNNNN")
S2C_REGISTRY.register(659, "returnTowerGqInfo", "NN")
S2C_REGISTRY.register(660, "returnTowerInfoEnd", "")
S2C_REGISTRY.register(667, "returnTowerReardEnd", "y")
S2C_REGISTRY.register(672, "returnLockTowerLevelAndTime", "NpNy")
S2C_REGISTRY.register(678, "returnLockTowerBaseData", "NNyN")
S2C_REGISTRY.register(679, "returnLockTowerFightStartFail", "")

# TASK-0418: 材料/资源副本 (contract task_0417).  330 enters the act, 329 names the NPC whose
# fight to start; the reply 592 carries the seed and the remaining count.  The fight itself
# is the ordinary PVE pair with pveOrPvpType 89 (PVETYPE_DOG_FOOD).
C2S_REGISTRY.register(329, "dgnFoodStartFight", "NNN")
S2C_REGISTRY.register(592, "dgnFoodSynSeed", "NNNN")
S2C_REGISTRY.register(593, "dgnFoodResult", "NNNNNN")
S2C_REGISTRY.register(594, "dgnNpcInfo", "NNN")
S2C_REGISTRY.register(595, "dgnEnter", "NN")
S2C_REGISTRY.register(596, "dgnState", "NNN")

# TASK-0421: 地牢 (contract task_0419).  Module is luaScript/prison_fight/ (there is no
# luaScript/prison/).  C2S 672 opens the panel (its answer 1377 does the showByViewPath),
# C2S 681 is the 1 s refresh whose answers are 1370/1371/1375 closed by 1387, and the fight
# is the ordinary pair with pveOrPvpType 99 (PVETYPE_PRISON_FIGHT).
C2S_REGISTRY.register(672, "sendRequestPrisonFightData", "")
C2S_REGISTRY.register(676, "sendOpenNextLayer", "")
C2S_REGISTRY.register(679, "sendGetLayerOnekeyReward", "NNNp")
C2S_REGISTRY.register(680, "sendGetFightBossOneKeyReward", "Np")
C2S_REGISTRY.register(682, "sendGetPrisonFightShareReward", "")
S2C_REGISTRY.register(1371, "synPrisonFightRoleToCg", "NpppNaaaapppy")
S2C_REGISTRY.register(1372, "returnPrisonFightFriendShareStart", "")
S2C_REGISTRY.register(1374, "returnPrisonFightFriendShareToCg", "NaaaNp")
S2C_REGISTRY.register(1375, "synPrisonFightPointToCg", "NNNNpNNpNN")
S2C_REGISTRY.register(1376, "returnPrisonFightError", "Na")
S2C_REGISTRY.register(1377, "returnPrisonFightRequestDataEnd", "")
S2C_REGISTRY.register(1384, "returnPrisonFightOver", "nyp")
S2C_REGISTRY.register(1387, "returnPrisonFightGetDataEnd", "")
S2C_REGISTRY.register(1390, "returnPrisonFightCloseEnter", "y")

# TASK-0422: 仙途 (contract task_0420).  There is NO grid -- three layers drawn as
# buttons.  S2C 239 has no lock partner, so the server must PUSH it before openFairyWay
# compares dLayerId on an empty table; C2S 46 is the only writer with a 15 s lock (240).
C2S_REGISTRY.register(47, "startFairyWayFight", "")
C2S_REGISTRY.register(48, "fairyWaySweep", "NN")
C2S_REGISTRY.register(49, "dispatchReward", "N")
C2S_REGISTRY.register(50, "buyFairyWayResetTimes", "N")
C2S_REGISTRY.register(51, "buyFairyWayAndReset", "N")
C2S_REGISTRY.register(52, "changeEnermy", "N")
C2S_REGISTRY.register(53, "fairyWayOpenShop", "")
C2S_REGISTRY.register(54, "fairyWayRefreshShop", "N")
C2S_REGISTRY.register(55, "fairyWayBuyGoods", "Np")
C2S_REGISTRY.register(56, "fairyWayOpenMainUI", "")
# TASK-0428: the battle-preparation transfer family.  pkgScProtocolMgr calls
# pkgBattleTransferMgr.registerProto(s2c, registerS2C, appCg), whose e_tbProto table is
# the authority for both the ids (220 + row index) and the formats.  S2C 220/221 are
# the begin/base pair that materialises le_tbMatchInfo: pkgFairyWayMgr.initMatchData
# (S2C 242) unguardedly reads tbRecvData.lstData[1], and the 封妖 panel only enables a
# layer's challenge button when next(tbMatchInfo) is true (pkgUIFairyWayMainMgr:66-81).
S2C_REGISTRY.register(220, "battleTransferBegin", "NpN")
S2C_REGISTRY.register(221, "battleTransferBase", "NppNNNaayN")
#: Rows 3/4/9 of pkgBattleTransferMgr.e_tbProto: one soul, one team card, one
#: shenfu.  TASK-0430 uses them to fill both PvP prepare slots (lstData[1]/[2])
#: for a 封妖 (type 53) battle and the match preview.
S2C_REGISTRY.register(222, "battleTransferSoul", "NpNNNNTNNNN")
S2C_REGISTRY.register(223, "battleTransferCard", "NpNNNNNNTNNNN")
S2C_REGISTRY.register(228, "battleTransferShenFu", "NpNNNN")
S2C_REGISTRY.register(239, "returnFairyWayInfo", "NNNNp")
S2C_REGISTRY.register(240, "returnFairyWayStartResult", "N")
S2C_REGISTRY.register(241, "returnfairyWayMatchStart", "")
S2C_REGISTRY.register(242, "returnfairyWayMatchEnd", "")
S2C_REGISTRY.register(243, "returnFairyWayRewardStatus", "N")
S2C_REGISTRY.register(244, "returnFairyWayMaxLayer", "N")
S2C_REGISTRY.register(245, "returnBuyFairyWayResetTimesResult", "N")
S2C_REGISTRY.register(246, "returnFairyWayBuyResetTimes", "N")
S2C_REGISTRY.register(247, "returnfairyWayChangeEnermyResult", "N")
S2C_REGISTRY.register(253, "fairyWayOpenMainUI", "")

# TASK-0425: 房间副本 (contract task_0423).  The chain is S2C-FIRST: 1362 must arrive
# before 1359 (it reads le_tbCfg.strInstanceId), and 1363/1366 must precede their
# Process packets.  The fight is pveOrPvpType 97 with the ordinary C2S 67 / S2C 96 pair.
C2S_REGISTRY.register(670, "roomDungeonEventDone", "NNNN")
C2S_REGISTRY.register(671, "roomDungeonFinish", "p")
S2C_REGISTRY.register(1359, "roomDungeonEnter", "aN")
S2C_REGISTRY.register(1360, "roomDungeonEventDone", "NNNNN")
S2C_REGISTRY.register(1361, "roomDungeonFinish", "N")
S2C_REGISTRY.register(1362, "returnRoomDungeonCfg", "NNaNa")
S2C_REGISTRY.register(1363, "returnRoomDungeonDataStart", "")
S2C_REGISTRY.register(1364, "returnRoomDungeonDataProcess", "NN")
S2C_REGISTRY.register(1365, "returnRoomDungeonDataEnd", "")
S2C_REGISTRY.register(1366, "returnRoomDungeonCompletedStart", "")
S2C_REGISTRY.register(1367, "returnRoomDungeonCompletedProcess", "NN")
S2C_REGISTRY.register(1368, "returnRoomDungeonCompletedEnd", "")
S2C_REGISTRY.register(1369, "updateRoomDungeonCompleted", "NN")