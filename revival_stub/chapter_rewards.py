"""The chapter-map milestone boxes, recovered from the shipped config.

The player-visible edge this module serves: the first chapter's own top bar draws
three chests and the client's ``pkgUIChapterMainUI.onAcceptBox`` sends
``C2S 222 chapterAcceptBox(dChapterId, dBoxIndex, dSyncCount)`` when the player
presses one.  Nothing on the server answered it, so the client's LOCK-SEND timer
ran out after 15 s and closed the socket -- the box could be seen but never
opened.  This module is the recovered *values* half of the answer; the wire and
the storage live in ``tcp_stub`` / ``role_store``.

Everything below is read out of the shipped client, never guessed:

* ``guankaChapter.max`` row 1: ``passRewardId = 5001`` (box 0),
  ``rewardPercent1 = 0`` / ``rewardId1 = 0`` (box 1, absent in the shipped
  table, therefore DISABLED), ``rewardPercent2 = 50`` / ``rewardId2 = 5000``
  (box 2, the 50 % milestone this slice is named after).  Re-derived by
  ``max_decoder/analyze_chapter_rewards.py`` from
  ``apk_unpacked/assets/config/guankaMap/guankaChapter.max``.
* ``guankaReward.max`` rows 5000 and 5001, slot by slot (crystal/money plus
  ``type1..10``/``itemId1..10``/``num1..10``).
* ``type = 8886`` is ``pkgRewardConstMgr.TB_REWARD_TYPE.SPECIAL_CARD`` (shipped
  const module, source line 84), and its ``itemId`` is the ``specialCard.max``
  row: ``pkgGuideConst.lua`` GUIDE_REWARD uses ``{SPECIAL_CARD, 3, PATH_TEAM}``
  for the 3-star dog food, and ``pkgBagOtherView.lua`` line 108 builds every
  special-card icon as ``getIconInfo(SPECIAL_CARD, dCfgId, v.dNum)``.  Rows
  1..5 of ``specialCard.max`` are the five dog foods (妖气团子 .. 灵山佛跳墙)
  and their ``star`` is 1..5, so a ``SPECIAL_CARD`` slot is granted as
  ``DOG_FOOD_<star>`` (301..305) with ``cfgId = itemId``.
* The progress gate is the client's own rule: ``getChapterProgress`` /
  ``statistExploration`` count FIGHT nodes with ``explore ~= 0`` and the
  NORMAL/CHEST EVENT nodes with ``explore ~= 0``; JINGYAOSI/CANJU nodes are
  counted in their own columns only (bytecode PROTO 0.0 of
  ``pkgChapterExplorationMgr``: the JINGYAOSI/CANJU branches never assign the
  ``isOver`` register the shared tail tests), and EXIT only sets
  ``le_isArrivedExit``.  Chapter 1 therefore has 11 counted nodes: fights 2, 4,
  7, 16 and events 24, 32, 36, 39, 40, 41, 42.

Nothing here is a device observation: the real-device step is DEVICE_PENDING.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

# --------------------------------------------------------------- the constants

#: ``pkgRewardConstMgr.TB_REWARD_TYPE`` values this slice reads (shipped const
#: chunk, decompiled source lines 8, 32-33 and 84).
KAPAI = 3
CRYSTAL = 6
MONEY = 7
SPECIAL_CARD = 8886

REWARD_TYPE_NAMES: dict[int, str] = {
    KAPAI: "KAPAI",
    CRYSTAL: "CRYSTAL",
    MONEY: "MONEY",
    SPECIAL_CARD: "SPECIAL_CARD",
}

#: ``specialCard.max`` rows 1..5 and their shipped ``star`` (each row's own
#: ``star`` column; the five dog foods are 妖气团子/忘川咸鱼汤/昆仑叫花鸡/
#: 东海龙须酥/灵山佛跳墙).
SPECIAL_CARD_STARS: dict[int, int] = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}

#: ``pkgRewardConstMgr`` DOG_FOOD_1..5 = 301..305 (source lines 32-33).
DOG_FOOD_GAIN_TYPES: dict[int, int] = {1: 301, 2: 302, 3: 303, 4: 304, 5: 305}

#: The client's literal box-0 threshold:
#: ``pkgUIChapterMainUI.checkBoxAccept``/``onAcceptBox`` lines 196-310 compare
#: ``dRewardPercent = 0.799`` for ``dBoxIndex == 0``.  It is not a config value.
CLIENT_BOX0_THRESHOLD = (799, 1000)

#: ``pkgCommonConstMgr.TB_GAIN_TYPE.GUANKA_MAP_ACCEPT_BOX`` (decompiled source
#: line 32, shipped value 503): the ``returnGainInfoStart`` type the client's
#: ``onChapterAcceptBox`` drains with ``getInfoAndClear``.
GUANKA_MAP_ACCEPT_BOX_GAIN_TYPE = 503

#: The status vocabulary a frozen box carries.
BOX_ENABLED = "enabled"
BOX_DISABLED = "disabled"
BOX_UNPERSISTABLE = "unpersistable_reward_slot"

#: ``pkgGuankaMapConstMgr.ACCEPT_BOX_STATUS_*`` (shipped const chunk, source
#: lines 58-63): the third S2C 384 value the client's own handler compares.
ACCEPT_BOX_STATUS_SUCCESS = 1
ACCEPT_BOX_STATUS_SYNC_ERR = 2
ACCEPT_BOX_STATUS_ACCEPTED = 3
ACCEPT_BOX_STATUS_FAIL = 4
ACCEPT_BOX_STATUS_ILLEGAL = 5
ACCEPT_BOX_STATUS_NAMES: dict[int, str] = {
    ACCEPT_BOX_STATUS_SUCCESS: "SUCCESS",
    ACCEPT_BOX_STATUS_SYNC_ERR: "SYNC_ERR",
    ACCEPT_BOX_STATUS_ACCEPTED: "ACCEPTED",
    ACCEPT_BOX_STATUS_FAIL: "FAIL",
    ACCEPT_BOX_STATUS_ILLEGAL: "ILLEGAL",
}

#: The closed refusal vocabulary a ``tcp_chapter_box_refused`` event carries.
BOX_REFUSAL_REASONS: tuple[str, ...] = (
    "unbound_role",
    "no_chapter_box_policy",
    "unusable_request_value",
    "unsupported_chapter",
    "unsupported_box",
    "disabled_box",
    "unpersistable_reward_slot",
    "progress_unavailable",
    "progress_insufficient",
    "store_unavailable",
    "store_refused",
)

#: Named refusal codes the C2S 222 handler attaches to a refused box.
BOX_REFUSAL_DISABLED = "disabled_box"
BOX_REFUSAL_UNPERSISTABLE = "unpersistable_reward_slot"
BOX_REFUSAL_UNKNOWN_CHAPTER = "unsupported_chapter"
BOX_REFUSAL_UNKNOWN_BOX = "unsupported_box"

#: One `chapterAcceptBox` accept row's origin: the client's own C2S 222.
CHAPTER_BOX_ORIGIN_CLIENT_REQUEST = "revival_local_c2s222_chapter_box_accept"

#: Login-replay provenance origins (the values are the stored accept rows).
CHAPTER_BOX_ROW_PROVENANCE = "deterministic_recovered"
CHAPTER_BOX_VALUE_PROVENANCE = "deterministic_recovered"
CHAPTER_BOX_ORDERING_PROVENANCE = "synthetic_compatibility"


# ------------------------------------------------------------------- the rows


@dataclass(frozen=True, slots=True)
class RewardSlot:
    """One non-empty ``typeN/itemIdN/numN`` slot of a ``guankaReward`` row."""

    wire_type: int
    item_id: int
    count: int

    @property
    def type_name(self) -> str:
        return REWARD_TYPE_NAMES.get(self.wire_type, "UNKNOWN")

    @property
    def is_dog_food(self) -> bool:
        return (
            self.wire_type == SPECIAL_CARD
            and self.item_id in SPECIAL_CARD_STARS
        )

    @property
    def dog_food_star(self) -> int | None:
        return SPECIAL_CARD_STARS.get(self.item_id) if self.is_dog_food else None

    @property
    def dog_food_gain_type(self) -> int | None:
        star = self.dog_food_star
        return None if star is None else DOG_FOOD_GAIN_TYPES[star]

    def gain_info(self) -> tuple[int, int, str, int]:
        """The client's own ``returnGainInfo`` fields for this slot.

        ``NNaN`` = (reward type, cfg id, instance id, count).  Dog food has no
        instance, and its display cfg id is the ``specialCard`` row.
        """

        gain_type = self.dog_food_gain_type
        if gain_type is None:
            raise ValueError(
                f"reward slot type {self.wire_type} item {self.item_id} is not "
                "a persisted dog-food special card"
            )
        return (gain_type, self.item_id, "", self.count)

    def canonical(self) -> str:
        return (
            f"slot:{self.wire_type}:{self.item_id}:{self.count}:"
            f"{self.type_name}"
        )


@dataclass(frozen=True, slots=True)
class RewardRow:
    """One ``guankaReward`` row: its crystal/money columns and non-empty slots."""

    reward_id: int
    crystal: int
    money: int
    slots: tuple[RewardSlot, ...]

    @property
    def unsupported_reason(self) -> str:
        """Why a row cannot be granted through the persisted inventory, if so.

        The archive persists dog food (``card_special_inventory``) and the three
        wallet values money/银魄/金魄.  A recovered row that also carries
        crystal/money has no persisted sink here, so it is refused whole rather
        than half-granted (D-21: never a partial, false reward).
        """

        if self.crystal:
            return "crystal"
        if self.money:
            return "money"
        if any(not slot.is_dog_food for slot in self.slots):
            return "reward_slot_type"
        return ""

    def canonical(self) -> str:
        parts = [
            f"reward:{self.reward_id}",
            f"crystal={self.crystal}",
            f"money={self.money}",
        ]
        parts.extend(slot.canonical() for slot in self.slots)
        return ":".join(parts)


@dataclass(frozen=True, slots=True)
class BoxReward:
    """One chapter-map chest: its threshold, reward row and support status."""

    chapter_id: int
    box_index: int
    reward_id: int
    status: str
    refusal: str
    threshold_numerator: int
    threshold_denominator: int
    threshold_source: str

    @property
    def grantable(self) -> bool:
        return self.status == BOX_ENABLED

    def threshold_met(self, *, triggered: int, total: int) -> bool:
        """The client's own comparison, in exact integer arithmetic.

        ``checkBoxAccept``/``onAcceptBox`` compare ``dRewardPercent <=
        pkgChapterExplorationMgr.getExplorationProgress()``, and the latter is
        ``le_dTriggeredProgress / le_dTotalProgress`` (PROTO 0.4 of
        ``pkgChapterExplorationMgr``).  ``total`` is never zero for a chapter
        this module knows (the client itself returns 1.0 in that case).
        """

        if total <= 0:
            return False
        return triggered * self.threshold_denominator >= (
            self.threshold_numerator * total
        )

    def reward_row(self) -> RewardRow | None:
        return REWARD_ROWS.get(self.reward_id)

    def gain_rows(self) -> tuple[tuple[int, int, str, int], ...]:
        """The gain-info rows this box would display, in slot order."""

        row = self.reward_row()
        if row is None:
            return ()
        return tuple(slot.gain_info() for slot in row.slots)

    def food_grant(self) -> dict[int, int]:
        """``{specialCard cfg id: count}`` for the persisted inventory."""

        row = self.reward_row()
        if row is None:
            return {}
        totals: dict[int, int] = {}
        for slot in row.slots:
            totals[slot.item_id] = totals.get(slot.item_id, 0) + slot.count
        return totals

    def canonical(self) -> str:
        return (
            f"box:{self.chapter_id}:{self.box_index}:status={self.status}:"
            f"refusal={self.refusal}:reward={self.reward_id}:"
            f"threshold={self.threshold_numerator}/{self.threshold_denominator}"
        )


@dataclass(frozen=True, slots=True)
class ChapterRewardRow:
    """The ``guankaChapter`` columns the chest button reads."""

    chapter_id: int
    map_conf: str
    reward_percent1: int
    reward_id1: int
    reward_percent2: int
    reward_id2: int
    pass_condition: int
    pass_reward_id: int

    def canonical(self) -> str:
        return (
            f"chapter:{self.chapter_id}:mapConf={self.map_conf}:"
            f"rewardPercent1={self.reward_percent1}:rewardId1={self.reward_id1}:"
            f"rewardPercent2={self.reward_percent2}:rewardId2={self.reward_id2}:"
            f"passCondition={self.pass_condition}:passRewardId={self.pass_reward_id}"
        )


#: ``guankaChapter.max`` rows this slice reads.  Chapter 1 is the only chapter
#: this archive has ever entered; every other chapter has no box the server can
#: answer and is refused by name.
CHAPTER_REWARDS: dict[int, ChapterRewardRow] = {
    1: ChapterRewardRow(
        chapter_id=1,
        map_conf="0",
        reward_percent1=0,
        reward_id1=0,
        reward_percent2=50,
        reward_id2=5000,
        pass_condition=20,
        pass_reward_id=5001,
    ),
}

#: The two reward rows the chapter-1 chests name.  Frozen from
#: ``guankaReward.max`` by ``max_decoder/analyze_chapter_rewards.py``.
REWARD_ROWS: dict[int, RewardRow] = {
    5000: RewardRow(
        reward_id=5000,
        crystal=0,
        money=0,
        slots=(
            RewardSlot(wire_type=SPECIAL_CARD, item_id=3, count=1),
            RewardSlot(wire_type=SPECIAL_CARD, item_id=2, count=1),
        ),
    ),
    5001: RewardRow(
        reward_id=5001,
        crystal=20,
        money=0,
        slots=(
            RewardSlot(wire_type=SPECIAL_CARD, item_id=3, count=2),
            RewardSlot(wire_type=SPECIAL_CARD, item_id=2, count=1),
        ),
    ),
}

#: Chapter 1's three chests, exactly as ``pkgUIChapterMainUI`` indexes them.
#: Box 1 is DISABLED because the shipped chapter row names neither a percentage
#: nor a reward for it (``rewardPercent1 = 0`` / ``rewardId1 = 0``).
BOX_REWARDS: dict[int, dict[int, BoxReward]] = {
    1: {
        0: BoxReward(
            chapter_id=1,
            box_index=0,
            reward_id=5001,
            # The row itself is fully recovered, but it carries 20 crystal and
            # this archive has no crystal column (same boundary the chapter-map
            # event's crystal reward records).  Refuse the whole box rather than
            # display a reward that cannot be persisted.
            status=BOX_UNPERSISTABLE,
            refusal=BOX_REFUSAL_UNPERSISTABLE,
            threshold_numerator=CLIENT_BOX0_THRESHOLD[0],
            threshold_denominator=CLIENT_BOX0_THRESHOLD[1],
            threshold_source="pkgUIChapterMainUI.checkBoxAccept literal 0.799",
        ),
        1: BoxReward(
            chapter_id=1,
            box_index=1,
            reward_id=0,
            status=BOX_DISABLED,
            refusal=BOX_REFUSAL_DISABLED,
            threshold_numerator=0,
            threshold_denominator=100,
            threshold_source="guankaChapter[1].rewardPercent1 / 100",
        ),
        2: BoxReward(
            chapter_id=1,
            box_index=2,
            reward_id=5000,
            status=BOX_ENABLED,
            refusal="",
            threshold_numerator=50,
            threshold_denominator=100,
            threshold_source="guankaChapter[1].rewardPercent2 / 100",
        ),
    },
}


def reward_row_for_box(box: BoxReward) -> RewardRow | None:
    return REWARD_ROWS.get(box.reward_id)


def reward_for_box(chapter_id: int, box_index: int) -> BoxReward | None:
    """The frozen chest, or ``None`` for a chapter/box this slice does not know.

    A known-but-disabled or known-but-unpersistable chest is returned as its
    frozen record; the caller must check ``grantable`` and report the record's
    named ``refusal``.  Only a genuinely unfrozen chapter/box returns ``None``.
    """

    return BOX_REWARDS.get(int(chapter_id), {}).get(int(box_index))


def box_refusal(chapter_id: int, box_index: int) -> str:
    """The named refusal for an unknown or disabled chest."""

    box = reward_for_box(chapter_id, box_index)
    if box is not None:
        return box.refusal or BOX_REFUSAL_DISABLED
    if int(chapter_id) not in CHAPTER_REWARDS:
        return BOX_REFUSAL_UNKNOWN_CHAPTER
    return BOX_REFUSAL_UNKNOWN_BOX


# --------------------------------------------------------- the progress nodes


@dataclass(frozen=True, slots=True)
class ProgressNode:
    """One chapter-map node the client's own ``getChapterProgress`` counts.

    ``kind`` is ``"fight"`` or ``"event"``; ``cfg_id`` is the map row's ``id``
    (the fight id or event config id), and ``subtype`` names the event subtype
    for the evidence block (``"normal"`` / ``"chest"``; empty for fights).
    """

    index: int
    kind: str
    cfg_id: int
    subtype: str = ""

    def canonical(self) -> str:
        return (
            f"progress:1:node:{self.index}:{self.kind}:{self.cfg_id}:{self.subtype}"
        )


#: ``chapterMap0.max`` / ``guankaMapEvent.max``, frozen by
#: ``max_decoder/analyze_chapter_rewards.py``.  The client visits a map row when
#: ``explore ~= 0`` or ``type == MAP_TYPE_EVENT``; a FIGHT row is counted, an
#: EVENT row is counted only when its shipped event subtype is NORMAL (0) or
#: CHEST (1) and ``explore ~= 0``.  Chapter 1's event rows 6 and 37 have
#: ``explore = 0`` and are not counted; row 37 is an OBSTACLE (subtype 3).
CHAPTER_PROGRESS_NODES: dict[int, tuple[ProgressNode, ...]] = {
    1: (
        ProgressNode(index=2, kind="fight", cfg_id=101010),
        ProgressNode(index=4, kind="fight", cfg_id=101020),
        ProgressNode(index=7, kind="fight", cfg_id=101031),
        ProgressNode(index=16, kind="fight", cfg_id=101051),
        ProgressNode(index=24, kind="event", cfg_id=60016, subtype="chest"),
        ProgressNode(index=32, kind="event", cfg_id=20011, subtype="chest"),
        ProgressNode(index=36, kind="event", cfg_id=812003, subtype="normal"),
        ProgressNode(index=39, kind="event", cfg_id=40011, subtype="chest"),
        ProgressNode(index=40, kind="event", cfg_id=20012, subtype="chest"),
        ProgressNode(index=41, kind="event", cfg_id=820017, subtype="normal"),
        ProgressNode(index=42, kind="event", cfg_id=820015, subtype="normal"),
    ),
}


@dataclass(frozen=True, slots=True)
class ChapterProgress:
    """The server-recorded counterpart of ``pkgChapterExplorationMgr``.

    ``triggered``/``total`` are exactly what ``getExplorationProgress`` divides:
    a FIGHT node is triggered when this archive recorded its settlement for the
    same map index and fight id, an EVENT node when it recorded the same map
    index and event config id.  The records come from
    ``chapter_fight_clears`` and ``chapter_event_clears`` only -- never from a
    client-claimed percentage.
    """

    chapter_id: int
    triggered: int
    total: int
    fights_completed: tuple[int, ...]
    fights_missing: tuple[int, ...]
    events_completed: tuple[int, ...]
    events_missing: tuple[int, ...]
    unknown_nodes: tuple[int, ...] = ()

    @property
    def ratio(self) -> float:
        return 0.0 if self.total <= 0 else self.triggered / self.total

    def evidence(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "progress_triggered": self.triggered,
            "progress_total": self.total,
            "progress_ratio": round(self.ratio, 6),
            "progress_fights_completed": list(self.fights_completed),
            "progress_fights_missing": list(self.fights_missing),
            "progress_events_completed": list(self.events_completed),
            "progress_events_missing": list(self.events_missing),
            "progress_unknown_nodes": list(self.unknown_nodes),
            "progress_source": "server_records:chapter_fight_clears+chapter_event_clears",
            "progress_policy": "client_getChapterProgress_weights",
        }


def progress_nodes_for(chapter_id: int) -> tuple[ProgressNode, ...] | None:
    return CHAPTER_PROGRESS_NODES.get(int(chapter_id))


def chapter_progress(
    chapter_id: int,
    *,
    fight_clears: Iterable[Any],
    event_clears: Iterable[Any],
) -> ChapterProgress | None:
    """Recompute the client's exploration percentage from server records.

    ``fight_clears`` carry ``chapter_id``, ``map_index`` and ``fight_id``;
    ``event_clears`` carry ``chapter_id``, ``map_index`` and ``event_cfg_id``.
    Anything that does not match the frozen node's own config id neither counts
    nor is invented as a different node.
    """

    nodes = progress_nodes_for(chapter_id)
    if nodes is None:
        return None
    numeric_chapter = int(chapter_id)
    fights = {
        int(row.map_index): int(row.fight_id)
        for row in fight_clears
        if int(row.chapter_id) == numeric_chapter
    }
    events = {
        int(row.map_index): int(row.event_cfg_id)
        for row in event_clears
        if int(row.chapter_id) == numeric_chapter
    }
    triggered = 0
    fights_done: list[int] = []
    fights_missing: list[int] = []
    events_done: list[int] = []
    events_missing: list[int] = []
    for node in nodes:
        if node.kind == "fight":
            matched = fights.get(node.index) == node.cfg_id
            (fights_done if matched else fights_missing).append(node.index)
        else:
            matched = events.get(node.index) == node.cfg_id
            (events_done if matched else events_missing).append(node.index)
        if matched:
            triggered += 1
    return ChapterProgress(
        chapter_id=numeric_chapter,
        triggered=triggered,
        total=len(nodes),
        fights_completed=tuple(fights_done),
        fights_missing=tuple(fights_missing),
        events_completed=tuple(events_done),
        events_missing=tuple(events_missing),
    )


# ------------------------------------------------------------------- the digest


def canonical_lines() -> tuple[str, ...]:
    """The frozen table as deterministic text; the digest is over exactly this."""

    lines: list[str] = []
    for chapter_id in sorted(CHAPTER_REWARDS):
        lines.append(CHAPTER_REWARDS[chapter_id].canonical())
    for reward_id in sorted(REWARD_ROWS):
        lines.append(REWARD_ROWS[reward_id].canonical())
    for chapter_id in sorted(BOX_REWARDS):
        for box_index in sorted(BOX_REWARDS[chapter_id]):
            lines.append(BOX_REWARDS[chapter_id][box_index].canonical())
    for cfg_id in sorted(SPECIAL_CARD_STARS):
        lines.append(f"special_card:{cfg_id}:{SPECIAL_CARD_STARS[cfg_id]}")
    for chapter_id in sorted(CHAPTER_PROGRESS_NODES):
        for node in CHAPTER_PROGRESS_NODES[chapter_id]:
            lines.append(node.canonical())
    return tuple(lines)


def digest() -> str:
    return hashlib.sha256("\n".join(canonical_lines()).encode("utf-8")).hexdigest()


#: Frozen digest of the tables above.  Re-derived by
#: ``max_decoder/analyze_chapter_rewards.py`` (two consecutive stdout runs must
#: be byte-identical); tests assert this literal and never read ``apk_unpacked``.
BOX_REWARD_DIGEST = "c8477ef8a0658f76ecc9580115d0b5b44a389515d8ca80260756afc609b251fd"

#: The digest the analyzer is expected to print for this slice.  Kept as the one
#: obvious constant so a frozen-table edit fails in one place.
FROZEN_DIGEST = BOX_REWARD_DIGEST


def verify_against_chunks(repo_root=None) -> tuple[dict | None, str]:
    """``(derived, reason)``: re-read the shipped chunks when they are present.

    ``reason`` is ``""`` only when every chunk was read and its values matched
    the frozen table.  ``(None, "chunk:missing")`` is not a mismatch: the
    unpacked tree is gitignored, so a checkout without it can still state the
    frozen table.  This is a convenience for the forensic pass; the analyzer
    (``max_decoder/analyze_chapter_rewards.py``) is the independent re-derivation.
    """

    import sys
    from pathlib import Path

    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    required = (
        "apk_unpacked/assets/config/guankaMap/guankaChapter.max",
        "apk_unpacked/assets/config/guankaMap/guankaReward.max",
        "apk_unpacked/assets/config/guankaMap/chapterMap/chapterMap0.max",
        "apk_unpacked/assets/config/guankaMap/guankaMapEvent.max",
        "apk_unpacked/assets/config/card/specialCard.max",
    )
    if any(not (root / path).is_file() for path in required):
        return None, "chunk:missing"
    analyzer = root / "max_decoder" / "analyze_chapter_rewards.py"
    if not analyzer.is_file():
        return None, "chunk:analyzer_missing"
    if str(root / "max_decoder") not in sys.path:
        sys.path.insert(0, str(root / "max_decoder"))
    from analyze_chapter_rewards import derive, canonical_lines as derive_lines

    derived = derive(root)
    lines = derive_lines(derived)
    if tuple(lines) != canonical_lines():
        differing = [
            line for line in lines if line not in set(canonical_lines())
        ][:3]
        return derived, "chunk:field_drift:" + ",".join(differing)
    return derived, ""


__all__ = [
    "BOX_DISABLED",
    "BOX_ENABLED",
    "BOX_REFUSAL_DISABLED",
    "BOX_REFUSAL_UNKNOWN_BOX",
    "BOX_REFUSAL_UNKNOWN_CHAPTER",
    "BOX_REFUSAL_UNPERSISTABLE",
    "BOX_REWARD_DIGEST",
    "BOX_REWARDS",
    "BOX_UNPERSISTABLE",
    "CHAPTER_BOX_ORIGIN_CLIENT_REQUEST",
    "CHAPTER_BOX_ORDERING_PROVENANCE",
    "CHAPTER_BOX_ROW_PROVENANCE",
    "CHAPTER_BOX_VALUE_PROVENANCE",
    "CHAPTER_PROGRESS_NODES",
    "CHAPTER_REWARDS",
    "CLIENT_BOX0_THRESHOLD",
    "CRYSTAL",
    "ChapterProgress",
    "ChapterRewardRow",
    "DOG_FOOD_GAIN_TYPES",
    "FROZEN_DIGEST",
    "KAPAI",
    "MONEY",
    "ProgressNode",
    "REWARD_ROWS",
    "REWARD_TYPE_NAMES",
    "RewardRow",
    "RewardSlot",
    "SPECIAL_CARD",
    "SPECIAL_CARD_STARS",
    "BoxReward",
    "box_refusal",
    "canonical_lines",
    "chapter_progress",
    "digest",
    "progress_nodes_for",
    "reward_for_box",
    "reward_row_for_box",
    "verify_against_chunks",
]
