"""Recovered `C2S 224 chapterMapEvent` contract and chapter 1's chest node.

The player-visible break this module serves: on 2026-10-18 the real client sent
`C2S 224 chapterMapEvent` while the player opened the first chapter's
「散乱的货物」 node.  The stub had no registration for the id, so the frame fell
through the free-play path as `known_unimplemented`, the client's own LOCK-SEND
timer ran out after 15 s and the client closed its socket (it had put
「正在努力载入 请稍候...」 on screen and never took it down).

Everything below is read out of the shipped client, never guessed:

* The wire id and format are the client's own ``registerC2S("NNNNNp",
  "chapterMapEvent")`` (``pkgScProtocolMgr.lua:189``).  The runtime id 224 is
  the D-02 registration simulation's answer (source ordinal 134 + the 90
  dynamic battle rows inserted before it), re-derived by
  ``max_decoder/analyze_pre_scene.protocol_table()``.
* The reply id and format are the client's own ``registerS2C("pppppp",
  "chapterMapEvent", "chapterMapEvent")`` (``pkgScProtocolMgr.lua:512``); the
  third argument is the lock partner, which is what makes C2S 224 a LOCK-SEND
  and S2C 398 its only unlock.  Runtime id 398 = source ordinal 254 + 144.
* The S2C handler is ``s2c.chapterMapEvent`` in ``socket/nc/pkgGuankaMapNcMgr``
  (source lines 86-107, 7 parameters = socket + 6 values).  It prints all six,
  compares ``p6`` with ``pkgGuankaMapConstMgr.EVENT_STATUS_SUCCESS``, and on
  success calls ``pkgChapterEventHandle.checkChapterReward`` with a callback
  that runs ``pkgChapterObjectMgr.checkEventObjValid(p2, p3)``.  Only positions
  2, 3 and 6 are read by the recovered client; positions 1, 4 and 5 have no
  consumer.
* The format char is ``p``, which this archive's codec decodes/encodes as a
  64-bit IEEE-754 double (``codec._decode_scalar`` / ``_encode_scalar``) -- the
  six values are numbers, not strings.
* There are exactly two senders in the whole shipped corpus
  (``max_decoder/scan_strings.py`` over 1,827 chunks):
  ``pkgChapterEventHandle.handleEvent``'s chest branch (source lines 266-291)
  and ``pkgUIChapterEventView``'s option callback (source lines 166-169).  Both
  pass ``(dChapterId, dIndex, 0, optionIndex, 0, dSyncCount)``; the chest
  branch's ``optionIndex`` is the literal 0, and the event view's is the
  option's 1-based ``dIndex`` from ``pkgCfgGuankaMapMgr.getEventOptions``.
* Chapter 1's 「散乱的货物」 is ``chapterMap0[36]``: ``type = MAP_TYPE_EVENT
  (13)``, ``id = 812003``.  ``guankaMapEvent[812003]`` is
  ``type = EVENT_SUBTYPE_NORMAL (0)`` with exactly one option
  (``optType1 = EVENT_TYPE_REWARD (2), optDesc1 = 翻找``); its ``rewardId1`` is
  ``25101`` and ``guankaReward[25101]`` carries ``crystal = 10``.
  ``pkgCfgGuankaMapMgr.getEventOptions`` (PROTO 0.13, source lines 192-242) is
  what proves the mapping: it loops ``i = 1, 3`` over the parallel
  ``optType<i>/optDesc<i>/rewardId<i>`` columns, keeps only ``optType > 0``,
  and stores ``dIndex = i``.  So option 1 is the one observed on the wire and
  its reward row is 25101.

The **reward value is recovered, but the client-visible grant is not made**.
The shipped ``guankaReward[25101]`` row carries one scalar ``crystal = 10`` and
no bucket column; the client's own S2C 10 handler keeps ``dCrystal`` equal to
the six bucket fields (`pkgPlayerInfoMgr.initSuperManInfo` /
`updateCrystalDetail`), and nothing in the shipped client says which bucket an
event reward enters.  TASK-0222 therefore records the reward in the server-side
ledger only (``role_store.chapter_event_reward_grants``) with
``crystal_bucket = NULL`` and ``bucket_provenance = 'unknown'``, and sends no
S2C 10, no ``updateCrystal`` and no gain-info packet; the client's visible
balance stays unchanged.  The client's own handler accepts a nil reward (its
``else`` branch), so the event still completes.  See
``reports/task_0222_event_reward_ledger.md``.

**A refused request is answered, never dropped** (TASK-0243 / D-186).  C2S 224
is a LOCK-SEND: the client stops taking input, shows its waiting overlay and
calls `pkgConnectMgr.shutDown()` after `LOCK_TIME = 15000` ms unless the S2C
that names it as lock partner arrives.  So every refused request gets exactly
one `S2C 398` whose status is a non-SUCCESS `EVENT_STATUS_*`
(:data:`REFUSAL_REPLY_STATUS`); nothing is stored, no reward is granted and no
success is faked.  See ``reports/task_0243_lock_send_refusal_replies.md``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Sequence


# ------------------------------------------------------------------ the contract

C2S_PROTOCOL_ID = 224
C2S_PROTOCOL_NAME = "chapterMapEvent"
C2S_FORMAT = "NNNNNp"

S2C_PROTOCOL_ID = 398
S2C_PROTOCOL_NAME = "chapterMapEvent"
S2C_FORMAT = "pppppp"

#: `pkgScProtocolMgr.lua:508-513` S2C registration block: the third argument of
#: the `chapterMapEvent` row is the C2S name, i.e. S2C 398 unlocks C2S 224.
S2C_UNLOCK_NAMES: tuple[str, ...] = ("chapterMapEvent",)

#: `pkgGuankaMapConstMgr` (source lines 28-30, 67-73, 150-157), re-read from the
#: shipped const chunk by `max_decoder/analyze_chapter_map_event.py`.
MAP_TYPE_EVENT = 13
EVENT_SUBTYPE_NORMAL = 0
EVENT_STATUS_SUCCESS = 1
EVENT_STATUS_ILLEGAL = 7
EVENT_STATUS_NAMES: dict[int, str] = {
    1: "SUCCESS",
    2: "SYNC_ERR",
    3: "MAP_ERR",
    4: "POS_ERR",
    5: "HANDLED",
    6: "LOSE",
    7: "ILLEGAL",
}

#: `pkgGuankaMapConstMgr.EVENT_TYPE_*` (source lines 120-133).
EVENT_TYPE_FIGHT = 1
EVENT_TYPE_REWARD = 2

#: The two positions both recovered senders pass as the literal 0.
C2S_ZERO_POSITIONS = (2, 4)  # 0-based: wire fields 3 and 5


# ------------------------------------------------------- chapter 1's chest node

#: `chapterMap0[36]` + `guankaMapEvent[812003]` + `guankaReward[25101]`.
CHAPTER_ID = 1
MAP_INDEX = 36
EVENT_CFG_ID = 812003
OPTION_INDEX = 1
OPTION_TYPE = EVENT_TYPE_REWARD
EVENT_NAME = "散乱的货物"
OPTION_DESCRIPTION = "翻找"
REWARD_ID = 25101
REWARD_CRYSTAL = 10
#: The reward's crystal bucket.  The shipped row has no bucket column and the
#: client only mirrors the six buckets from S2C 10 / updateCrystal, so no bucket
#: can be named from source.  ``None`` is the honest value; a string such as
#: ``"UNKNOWN"`` would impersonate one of the client's bucket names.
REWARD_LEDGER_BUCKET: str | None = None
REWARD_LEDGER_BUCKET_PROVENANCE = "unknown"
EVENT_SUBTYPE = EVENT_SUBTYPE_NORMAL
IS_DELETE = 0


@dataclass(frozen=True, slots=True)
class ChapterEventNode:
    """The one chapter-map event node this slice answers."""

    chapter_id: int
    map_index: int
    event_cfg_id: int
    option_index: int
    option_type: int
    event_name: str
    option_description: str
    reward_id: int
    reward_crystal: int
    event_subtype: int
    is_delete: int

    def canonical(self) -> str:
        return (
            f"chapter={self.chapter_id}|index={self.map_index}"
            f"|cfg={self.event_cfg_id}|option={self.option_index}"
            f"|optType={self.option_type}|name={self.event_name}"
            f"|optDesc={self.option_description}|rewardId={self.reward_id}"
            f"|crystal={self.reward_crystal}|subtype={self.event_subtype}"
            f"|isDelete={self.is_delete}"
        )


CHAPTER_ONE_EVENT_NODE = ChapterEventNode(
    chapter_id=CHAPTER_ID,
    map_index=MAP_INDEX,
    event_cfg_id=EVENT_CFG_ID,
    option_index=OPTION_INDEX,
    option_type=OPTION_TYPE,
    event_name=EVENT_NAME,
    option_description=OPTION_DESCRIPTION,
    reward_id=REWARD_ID,
    reward_crystal=REWARD_CRYSTAL,
    event_subtype=EVENT_SUBTYPE,
    is_delete=IS_DELETE,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def contract_canonical() -> str:
    statuses = ",".join(
        f"{name}:{value}" for value, name in sorted(EVENT_STATUS_NAMES.items())
    )
    return (
        f"c2s={C2S_PROTOCOL_ID}:{C2S_PROTOCOL_NAME}:{C2S_FORMAT}"
        f"|s2c={S2C_PROTOCOL_ID}:{S2C_PROTOCOL_NAME}:{S2C_FORMAT}"
        f"|unlock={','.join(S2C_UNLOCK_NAMES)}"
        f"|mapTypeEvent={MAP_TYPE_EVENT}"
        f"|eventSubtypeNormal={EVENT_SUBTYPE_NORMAL}"
        f"|eventTypes=fight:{EVENT_TYPE_FIGHT},reward:{EVENT_TYPE_REWARD}"
        f"|statuses={statuses}"
    )


#: sha256 of :func:`contract_canonical` / :meth:`ChapterEventNode.canonical`.
#: The analyzer prints the same digests; a config or bytecode drift changes them.
CONTRACT_DIGEST = "7643c47b659aa0c64d12067247aed82c2cbcdc09b3836126b997e9680370727a"
NODE_DIGEST = "adcdec125c1b60906d350526cd0fe697f76589608f3bb1db2fad48f0be103296"


def contract_digest() -> str:
    return _sha256(contract_canonical())


def node_digest(node: ChapterEventNode | None = None) -> str:
    return _sha256((node or CHAPTER_ONE_EVENT_NODE).canonical())


# ------------------------------------------------------------ request handling


REFUSAL_FIELD_COUNT = "chapter_map_event_field_count"
REFUSAL_NON_INTEGRAL = "chapter_map_event_non_integral"
REFUSAL_SYNC_COUNT_TYPE = "chapter_map_event_sync_count_type"
REFUSAL_CONSTANT_POSITION = "chapter_map_event_constant_position"
REFUSAL_UNKNOWN_NODE = "chapter_map_event_unknown_node"
REFUSAL_UNKNOWN_OPTION = "chapter_map_event_unknown_option"

#: Closed vocabulary, reported verbatim in `tcp_chapter_event_refused`.
REFUSAL_CODES = (
    REFUSAL_FIELD_COUNT,
    REFUSAL_NON_INTEGRAL,
    REFUSAL_SYNC_COUNT_TYPE,
    REFUSAL_CONSTANT_POSITION,
    REFUSAL_UNKNOWN_NODE,
    REFUSAL_UNKNOWN_OPTION,
)


@dataclass(frozen=True, slots=True)
class ChapterMapEventRequest:
    """One accepted `C2S 224`, reduced to the values this slice acts on."""

    chapter_id: int
    map_index: int
    option_index: int
    sync_count: float

    def replay_row(self) -> tuple[int, int, int]:
        """The client's own `initChapterItemInfo` row: (chapter, type, id)."""

        return (self.chapter_id, MAP_TYPE_EVENT, self.map_index)

    def replay_values(self) -> tuple[int, int, int]:
        return self.replay_row()


@dataclass(frozen=True, slots=True)
class ChapterMapEventDecision:
    """One classification: exactly one of ``request`` / ``refusal`` is set."""

    request: ChapterMapEventRequest | None
    refusal: str

    @property
    def accepted(self) -> bool:
        return self.request is not None


def _as_int(value: object) -> int | None:
    """An integral number as int, or ``None`` (booleans are never numbers here)."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)
    return None


def classify_request(fields: Sequence[object]) -> ChapterMapEventDecision:
    """Accept only the one event request this archive has recovered.

    The two recovered senders pass ``(dChapterId, dIndex, 0, option, 0,
    dSyncCount)``.  Anything else is refused by name: no row is stored and no
    reward is granted.  A refused request is still *answered* with one
    `S2C 398` carrying :data:`REFUSAL_REPLY_STATUS` (:func:`refusal_reply_fields`)
    -- refusing silently would leave the client's own 15 s lock-send timer to
    close the socket (D-186).
    """

    values = tuple(fields)
    if len(values) != len(C2S_FORMAT):
        return ChapterMapEventDecision(None, REFUSAL_FIELD_COUNT)
    numbers = [_as_int(value) for value in values[:5]]
    if any(number is None for number in numbers):
        return ChapterMapEventDecision(None, REFUSAL_NON_INTEGRAL)
    last = values[5]
    if isinstance(last, bool) or not isinstance(last, (int, float)):
        return ChapterMapEventDecision(None, REFUSAL_SYNC_COUNT_TYPE)
    sync_count = float(last)
    if not math.isfinite(sync_count):
        return ChapterMapEventDecision(None, REFUSAL_SYNC_COUNT_TYPE)
    chapter_id, map_index, third, option_index, fifth = numbers
    if third != 0 or fifth != 0:
        return ChapterMapEventDecision(None, REFUSAL_CONSTANT_POSITION)
    node = CHAPTER_ONE_EVENT_NODE
    if (chapter_id, map_index) != (node.chapter_id, node.map_index):
        return ChapterMapEventDecision(None, REFUSAL_UNKNOWN_NODE)
    if option_index != node.option_index:
        return ChapterMapEventDecision(None, REFUSAL_UNKNOWN_OPTION)
    return ChapterMapEventDecision(
        ChapterMapEventRequest(
            chapter_id=chapter_id,
            map_index=map_index,
            option_index=option_index,
            sync_count=sync_count,
        ),
        "",
    )


def success_reply_fields(
    request: ChapterMapEventRequest,
) -> tuple[object, ...]:
    """The six `S2C 398` values for one accepted request.

    Positions 1-5 are the same values the client itself sent (the client's
    handler reads only positions 2, 3 and 6); position 6 is the recovered
    ``EVENT_STATUS_SUCCESS`` constant.  Nothing is invented: the coordinates
    ``checkEventObjValid`` receives are the client's own ``dIndex`` and its
    literal 0.
    """

    return (
        request.chapter_id,
        request.map_index,
        0,
        request.option_index,
        0,
        float(EVENT_STATUS_SUCCESS),
    )


# ----------------------------------------------------------- refusal answers

#: The status one refused `C2S 224` is answered with.
#:
#: The client's own handler (`socket/nc/pkgGuankaMapNcMgr` PROTO 0.14, source
#: lines 86-107) prints all six values, then compares position 6 with
#: `pkgGuankaMapConstMgr.EVENT_STATUS_SUCCESS`.  Only SUCCESS enters
#: `pkgChapterEventHandle.checkChapterReward`; the five other recovered statuses
#: (LOSE 6, SYNC_ERR 2, MAP_ERR 3, POS_ERR 4, HANDLED 5) and ILLEGAL 7 all fall
#: to the same empty tail and simply `RETURN` -- identical, proven no-op
#: branches.  What matters for D-186 is that S2C 398 *is* the lock partner of
#: C2S 224 (`pkgScProtocolMgr.lua:512`), so any one of them releases the
#: client's 15 s lock and it stops waiting.
#:
#: ILLEGAL is chosen because the refusals this module names are malformed or
#: unknown requests (wrong field count, non-integral values, non-zero constant
#: positions, an unknown node/option), i.e. "the server will not act on this",
#: not a claim that the *map* or the *position* is wrong (MAP_ERR/POS_ERR would
#: assert facts about the world that the refuser does not know).  The choice is
#: `synthetic_compatibility`: the shipped client never records which status the
#: original server used for a refusal.  Do NOT send SUCCESS here -- the client
#: would run `checkChapterReward` and treat the refusal as a completed action.
REFUSAL_REPLY_STATUS = EVENT_STATUS_ILLEGAL
REFUSAL_REPLY_STATUS_NAME = "ILLEGAL"
REFUSAL_REPLY_STATUS_PROVENANCE = "synthetic_compatibility"
REFUSAL_REPLY_STATUS_ORIGIN = "revival_local_c2s224_refusal_status"

#: Positions whose value could not be taken from the refused request and had to
#: be filled with 0 (only reachable when the request cannot be echoed at all).
REFUSAL_REPLY_PADDING_ORIGIN = "synthetic_compatibility"


def refusal_reply_fields(
    fields: Sequence[object],
) -> tuple[tuple[object, ...], list[int]]:
    """The six `S2C 398` values answering one refused request.

    D-186: a lock-send that the server refuses must still be unlocked, so every
    refused `C2S 224` gets exactly one S2C 398 carrying a non-SUCCESS
    `EVENT_STATUS_*`.  Positions 1-5 echo the client's own request values by
    the same rule the success reply follows; positions 3 and 5 are the client's
    own literals 0 in every reachable request, so echoing them is identical to
    the accepted path.  The client reads none of them on a non-SUCCESS status.

    When the request cannot be echoed at all (fewer than five numeric values --
    impossible over the wire, because the registered format is ``NNNNNp``), the
    five positions are filled with 0 and returned as padded indices, so the
    caller can label the reply honestly instead of guessing.
    """

    values = tuple(fields)
    echo: list[object] = []
    padded: list[int] = []
    for position, value in enumerate(values[:5]):
        usable = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and (not isinstance(value, float) or math.isfinite(value))
        )
        if usable:
            echo.append(value)
        else:
            echo.append(0)
            padded.append(position + 1)
    while len(echo) < 5:
        padded.append(len(echo) + 1)
        echo.append(0)
    return (*(echo), float(REFUSAL_REPLY_STATUS)), padded


# --------------------------------------------------------------- re-derivation


class ChapterMapEventDrift(AssertionError):
    """The shipped client no longer matches the frozen contract."""


def verify_contract_digest() -> None:
    actual = contract_digest()
    if actual != CONTRACT_DIGEST:  # pragma: no cover - drift guard
        raise ChapterMapEventDrift(
            f"contract digest {actual} != frozen {CONTRACT_DIGEST}"
        )


def verify_node_digest(node: ChapterEventNode | None = None) -> None:
    actual = node_digest(node)
    frozen = NODE_DIGEST
    if actual != frozen:  # pragma: no cover - drift guard
        raise ChapterMapEventDrift(f"node digest {actual} != frozen {frozen}")
