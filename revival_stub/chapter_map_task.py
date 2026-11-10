"""Recovered `C2S 227 chapterMapTask` contract and the chapter-task state machine.

The player-visible break this module serves is the largest remaining story gap
in the mechanism-family matrix (TASK-0239 / D-195): the chapter task family is
the one shared mechanism whose server side does not exist at all.  A task NPC's
「领取」 and every manual/automatic task submission send `C2S 227
chapterMapTask`, and the client's own registration makes it a LOCK-SEND whose
only unlock is `S2C 397`.  Without a handler the player's first tap turns into a
15 s 「正在努力载入 请稍候...」 and then a closed socket, exactly like the
recovered `C2S 224` break (D-186).  Worse, `pkgStoryMgr.showStoryView` calls
`pkgStoryTrigger.checkTaskState` for every story, and that returns false while
the client's task container is empty -- so 33 recovered stories never open
their dialogue view and never send their own `C2S 233`.

Everything below is read out of the shipped client, never guessed:

* The wire id and format are the client's own ``registerC2S("NNNNp",
  "chapterMapTask")`` (``pkgScProtocolMgr.lua:192``).  The runtime id 227 is the
  D-02 registration simulation's answer (source ordinal 137 + the 90 dynamic
  battle rows inserted before it), re-derived by
  ``max_decoder/analyze_pre_scene.protocol_table()``.
* The reply id and format are the client's own ``registerS2C("NNNNN",
  "chapterMapTask", "chapterMapTask")`` (``pkgScProtocolMgr.lua:511``); the
  third argument is the lock partner, which is what makes C2S 227 a LOCK-SEND
  and S2C 397 its only unlock.  Runtime id 397 = source ordinal 253 + 144.
* The five request values are ``(dChapterId, dHostIndex, dTaskId, dAction,
  dSyncCount)``.  Both recovered senders pass exactly that shape:
  ``pkgChapterTaskMgr.IsSubmitTask`` PROTO 0.13.0 (source lines 154-158) and
  ``pkgChapterTaskMgr.CheckAllTaskAchieve`` PROTO 0.18 (source lines 389-420)
  send ``TASK_COMPLETE(2)``; ``pkgUIChapterTaskNpc`` PROTO 0.1.0 (source lines
  98-109) sends ``TASK_RECEIVE(1)`` after ``assert(le_dTaskId == optId)``.
* ``TASK_RECEIVE = 1`` and ``TASK_COMPLETE = 2`` come from
  ``pkgGuankaMapConstMgr`` lines 146-147 of its decoded listing; the ten
  ``TASK_STATUS_*`` constants are lines 109-118.  ``GetTaskCompletedId(id) =
  id + 10000000`` is the function the client's own ``isTaskCompleted`` indexes
  the container with (``pkgChapterTaskMgr`` PROTO 0.9, source lines 66-70).
* The S2C handler is ``s2c.chapterMapTask`` in ``socket/nc/pkgGuankaMapNcMgr``
  PROTO 0.16 (source lines 126-145, 6 parameters = socket + 5 values).  It
  prints all five, compares the fifth with ``TASK_STATUS_SUCCESS``, and only
  positions 1, 3 and 5 are ever read: status 1 calls
  ``pkgChapterTaskMgr.netTaskCompleted(R1, R3)``, status 2 calls
  ``netTaskReceived(R1, R3)``, status 3/4 print a failure line, 5/6 jump to the
  end and 7-10 have no branch at all.  Positions 2 and 4 therefore carry no
  meaning to the recovered client (U-239-2) and the reply echoes them.
* ``pkgChapterTaskMgr.netTaskReceived`` / ``netTaskCompleted`` (PROTO 0.7 /
  PROTO 0.6) do **not** write ``le_tbChapterTaskInfo``; only the login
  container (``S2C 385`` reset -> ``S2C 386`` row -> ``S2C 387`` end) and
  ``S2C 388 updateChapterTask`` write it (U-240-1).  ``isTaskReceived``
  (PROTO 0.8) reads that table alone, and both submit senders gate on it, so a
  task received in this session cannot be submitted or gate a story until the
  next login replays the row.

What this slice does and deliberately does not do:

* It records the client's own receive/complete actions as durable rows and
  answers exactly one `S2C 397` per answerable request, so the lock releases and
  the client stops hanging.
* It replays those rows at the next login through the client's own `S2C 386`
  container, which is what makes ``isTaskReceived`` true again.
* It never sends `S2C 388 updateChapterTask` (its producer pairing is UNKNOWN),
  never validates or invents a sync counter (U-239-3), and never grants a task
  reward: ``guankaMapTask.rewardId`` is a client config column whose server
  grant path is not recovered (U-239-1).  The stub also does not verify the
  task's achievement conditions -- the client gates completion itself
  (U-240-2), and whether the original server validated independently is
  UNKNOWN (``historical_rule_recovered = false``).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


# ------------------------------------------------------------------ the contract

C2S_PROTOCOL_ID = 227
C2S_PROTOCOL_NAME = "chapterMapTask"
C2S_FORMAT = "NNNNp"

S2C_PROTOCOL_ID = 397
S2C_PROTOCOL_NAME = "chapterMapTask"
S2C_FORMAT = "NNNNN"

#: `pkgScProtocolMgr.lua:511` S2C registration block: the third argument of the
#: `chapterMapTask` row is the C2S name, i.e. S2C 397 unlocks C2S 227.
S2C_UNLOCK_NAMES: tuple[str, ...] = ("chapterMapTask",)

#: `pkgGuankaMapConstMgr` source lines 109-118, re-read from the shipped const
#: listing (`zys_scAll__const__pkgGuankaMapConstMgr.listing.txt`).
TASK_STATUS_SUCCESS = 1
TASK_STATUS_REV_SUCCESS = 2
TASK_STATUS_FAIL = 3
TASK_STATUS_REV_FAIL = 4
TASK_STATUS_SYNC_ERR = 5
TASK_STATUS_HANDLED = 6
TASK_STATUS_NOT_REV = 7
TASK_STATUS_NOT_ACHIEVE = 8
TASK_STATUS_NOT_EXIST = 9
TASK_STATUS_ILLEGAL = 10

TASK_STATUS_NAMES: dict[int, str] = {
    TASK_STATUS_SUCCESS: "TASK_STATUS_SUCCESS",
    TASK_STATUS_REV_SUCCESS: "TASK_STATUS_REV_SUCCESS",
    TASK_STATUS_FAIL: "TASK_STATUS_FAIL",
    TASK_STATUS_REV_FAIL: "TASK_STATUS_REV_FAIL",
    TASK_STATUS_SYNC_ERR: "TASK_STATUS_SYNC_ERR",
    TASK_STATUS_HANDLED: "TASK_STATUS_HANDLED",
    TASK_STATUS_NOT_REV: "TASK_STATUS_NOT_REV",
    TASK_STATUS_NOT_ACHIEVE: "TASK_STATUS_NOT_ACHIEVE",
    TASK_STATUS_NOT_EXIST: "TASK_STATUS_NOT_EXIST",
    TASK_STATUS_ILLEGAL: "TASK_STATUS_ILLEGAL",
}

#: `pkgGuankaMapConstMgr` source lines 146-147.
TASK_RECEIVE = 1
TASK_COMPLETE = 2
TASK_ACTIONS: tuple[int, int] = (TASK_RECEIVE, TASK_COMPLETE)
TASK_ACTION_NAMES: dict[int, str] = {
    TASK_RECEIVE: "TASK_RECEIVE",
    TASK_COMPLETE: "TASK_COMPLETE",
}

#: `pkgGuankaMapConstMgr.GetTaskCompletedId(dTaskId)` = ``dTaskId + 10000000``.
#: `pkgChapterTaskMgr.isTaskCompleted` (PROTO 0.9, source lines 66-70) indexes
#: `le_tbChapterTaskInfo[chapterId][completedId]`, so the login row for a
#: completed task uses the offset id.
COMPLETED_ID_OFFSET = 10_000_000

#: Wire position -> the client's own name, in order, for both directions.
FIELD_NAMES: tuple[str, ...] = (
    "dChapterId",
    "dHostIndex",
    "dTaskId",
    "dAction",
    "dSyncCount",
)

#: `s2c.chapterMapTask` (PROTO 0.16) reads only these reply positions.  The
#: ones it prints but never compares carry no semantics (U-239-2).
CLIENT_CONSUMED_POSITIONS: dict[int, str] = {
    1: "chapterId -> netTaskCompleted/netTaskReceived",
    3: "taskId -> netTaskCompleted/netTaskReceived",
    5: "status compared with TASK_STATUS_*",
}
CLIENT_UNREAD_POSITIONS: tuple[int, ...] = (2, 4)

#: Reply positions 1-4 are the client's own request values; only the status is a
#: server decision.
REPLY_VALUE_ORIGINS: tuple[str, ...] = (
    "request_echo",
    "request_echo",
    "request_echo",
    "request_echo",
    "deterministic_recovered",
)

#: The words this module's state machine can report.  A caller maps them onto
#: reply statuses; none of them is a reward decision.
STATE_RECEIVED = "received"
STATE_COMPLETED = "completed"
STATE_ALREADY_HANDLED = "already_handled"
STATE_NOT_RECEIVED = "not_received"
STATE_REFUSED = "refused"

#: The status a caller answers for each state-machine result.
STATE_REPLY_STATUS: dict[str, int] = {
    STATE_RECEIVED: TASK_STATUS_REV_SUCCESS,
    STATE_COMPLETED: TASK_STATUS_SUCCESS,
    STATE_ALREADY_HANDLED: TASK_STATUS_HANDLED,
    STATE_NOT_RECEIVED: TASK_STATUS_NOT_REV,
}

#: The named failure a caller answers when it could not consult or write the
#: state at all, by the action it was asked to perform.
ACTION_FAILURE_STATUS: dict[int, int] = {
    TASK_RECEIVE: TASK_STATUS_REV_FAIL,
    TASK_COMPLETE: TASK_STATUS_FAIL,
}


def completed_task_id(task_id: int) -> int:
    """`pkgGuankaMapConstMgr.GetTaskCompletedId`."""

    return int(task_id) + COMPLETED_ID_OFFSET


# ----------------------------------------------------------------- the request

#: Closed refusal vocabulary, reported verbatim in `tcp_chapter_task_refused`.
REFUSAL_FIELD_COUNT = "chapter_map_task_field_count"
REFUSAL_ACTION = "chapter_map_task_action"
REFUSAL_NON_INTEGRAL = "chapter_map_task_non_integral"
REFUSAL_SYNC_COUNT_TYPE = "chapter_map_task_sync_count_type"

REFUSAL_CODES: tuple[str, ...] = (
    REFUSAL_FIELD_COUNT,
    REFUSAL_ACTION,
    REFUSAL_NON_INTEGRAL,
    REFUSAL_SYNC_COUNT_TYPE,
)


@dataclass(frozen=True, slots=True)
class ChapterMapTaskRequest:
    """One accepted `C2S 227`, reduced to the values this slice acts on."""

    chapter_id: int
    host_index: int
    task_id: int
    action: int
    sync_count: float

    @property
    def action_name(self) -> str:
        return TASK_ACTION_NAMES[self.action]

    def echo_fields(self) -> tuple[int, int, int, int]:
        """The client's own first four values (reply positions 1-4)."""

        return (self.chapter_id, self.host_index, self.task_id, self.action)


@dataclass(frozen=True, slots=True)
class ChapterMapTaskDecision:
    """One classification of a `C2S 227` frame.

    Exactly one of three shapes:

    * ``request`` set -- a well-formed request the state machine may act on;
    * ``failure_status`` and ``echo_fields`` set -- the action is recoverable
      (1 or 2) but another value is not, so the caller answers the action's own
      failure status rather than staying silent (D-186: a lock-send must never
      be silently dropped);
    * neither -- the frame is not even answerable (too few fields, or the action
      itself is not 1/2); the caller sends nothing and changes nothing, the same
      fail-closed path `C2S 224` takes.
    """

    request: ChapterMapTaskRequest | None
    refusal: str
    failure_status: int | None = None
    echo_fields: tuple[int, int, int, int] | None = None

    @property
    def accepted(self) -> bool:
        return self.request is not None

    @property
    def answered(self) -> bool:
        """Is this decision answered with an `S2C 397` at all?"""

        return self.request is not None or self.echo_fields is not None


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


def _as_wire_int(value: object) -> int | None:
    """An integral, non-negative, 32-bit value -- the wire type of ``N``."""

    number = _as_int(value)
    if number is None or number < 0 or number > 0xFFFFFFFF:
        return None
    return number


def classify_request(fields: Sequence[object]) -> ChapterMapTaskDecision:
    """Classify one `C2S 227` frame without inventing anything.

    The recovered senders all pass five values with ``action`` in {1, 2}.  The
    action is read first: without it nothing can be answered safely.
    """

    values = tuple(fields)
    if len(values) < 4:
        return ChapterMapTaskDecision(None, REFUSAL_FIELD_COUNT)
    action = _as_int(values[3])
    if action is None or action not in TASK_ACTIONS:
        return ChapterMapTaskDecision(None, REFUSAL_ACTION)
    numbers = [_as_wire_int(value) for value in values[:4]]
    if any(number is None for number in numbers):
        # The action is usable but the echoed ids are not wire integers, so no
        # honest reply can be built: stay silent, exactly like a refused event.
        return ChapterMapTaskDecision(None, REFUSAL_NON_INTEGRAL)
    echo: tuple[int, int, int, int] = (
        numbers[0],
        numbers[1],
        numbers[2],
        numbers[3],
    )
    failure = ACTION_FAILURE_STATUS[action]
    if len(values) != len(C2S_FORMAT):
        return ChapterMapTaskDecision(
            None, REFUSAL_FIELD_COUNT, failure, echo
        )
    last = values[4]
    if isinstance(last, bool) or not isinstance(last, (int, float)):
        return ChapterMapTaskDecision(
            None, REFUSAL_SYNC_COUNT_TYPE, failure, echo
        )
    sync_count = float(last)
    if not math.isfinite(sync_count):
        return ChapterMapTaskDecision(
            None, REFUSAL_SYNC_COUNT_TYPE, failure, echo
        )
    return ChapterMapTaskDecision(
        ChapterMapTaskRequest(
            chapter_id=echo[0],
            host_index=echo[1],
            task_id=echo[2],
            action=echo[3],
            sync_count=sync_count,
        ),
        "",
    )


def success_reply_fields(
    request: ChapterMapTaskRequest,
    status: int = TASK_STATUS_SUCCESS,
) -> tuple[object, ...]:
    """The five `S2C 397` values for one answered request.

    Positions 1-4 are the client's own values (the handler reads only 1 and 3)
    and position 5 is the status this server decided on.
    """

    if int(status) not in TASK_STATUS_NAMES:
        raise ValueError(f"unknown chapter-task status {status!r}")
    return (
        request.chapter_id,
        request.host_index,
        request.task_id,
        request.action,
        int(status),
    )


def failure_reply_fields(
    echo_fields: Sequence[int],
    status: int,
) -> tuple[object, ...]:
    """The five `S2C 397` values for a request whose action is known but unusable."""

    if int(status) not in TASK_STATUS_NAMES:
        raise ValueError(f"unknown chapter-task status {status!r}")
    if len(echo_fields) != len(C2S_FORMAT) - 1:
        raise ValueError(
            f"a failure reply echoes four values, got {len(echo_fields)}"
        )
    return tuple(int(value) for value in echo_fields) + (int(status),)


# ------------------------------------------------------------ stored task rows


#: `meta` key prefix.  The rows ride the archive's existing `meta` table
#: (structure 1), so every older archive opens unchanged, no new table and no
#: ``SCHEMA_VERSION`` change is needed (the TASK-0206/TASK-0232 pattern).
CHAPTER_TASK_META_PREFIX = "chapter_task_rows:"

#: The stored payload's own domain marker and structure version.
CHAPTER_TASK_META_FORMAT = "revival-chapter-task-rows"
CHAPTER_TASK_META_FORMAT_VERSION = 1

#: Provenance of the rows.  They are the client's own observed actions, never an
#: original-server record: ``revival_local_c2s227_record``.
CHAPTER_TASK_ORIGIN = "revival_local_c2s227_record"
CHAPTER_TASK_VALUE_ORIGIN = "request_echo"
CHAPTER_TASK_COMPLETED_ID_RULE = "deterministic_recovered"
CHAPTER_TASK_REPLACEABLE = True

#: One payload key set, kept as data so validation and the report agree.
CHAPTER_TASK_PAYLOAD_KEYS = frozenset(
    {
        "format",
        "format_version",
        "account_key",
        "origin",
        "provenance",
        "replaceable",
        "rows",
    }
)

#: One row key set, in the order the report describes them.
CHAPTER_TASK_ROW_KEYS: tuple[str, ...] = (
    "account_key",
    "role_id",
    "chapter_id",
    "task_id",
    "received",
    "completed",
    "received_at",
    "completed_at",
)


def chapter_task_meta_key(account_key: str) -> str:
    """The one `meta` key that carries one account's chapter-task rows."""

    return f"{CHAPTER_TASK_META_PREFIX}{account_key}"


def chapter_task_provenance() -> dict[str, Any]:
    """The provenance block stored with every payload.

    The value origin and the completed-id rule are deliberately separate: the
    ids are the client's own request values, while the ``+10000000`` mapping is
    a recovered client rule.
    """

    return {
        "value_origin": CHAPTER_TASK_VALUE_ORIGIN,
        "completed_id_rule": CHAPTER_TASK_COMPLETED_ID_RULE,
        "completed_id_offset": COMPLETED_ID_OFFSET,
        "historical_values_recovered": False,
    }


def _require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChapterMapTaskError(
            REFUSAL_ROW_INVALID, f"{field} must be an int, not {value!r}"
        )
    return value


@dataclass(frozen=True, slots=True)
class ChapterTaskRow:
    """One stored chapter task of one role, as the client asserted it."""

    account_key: str
    role_id: int
    chapter_id: int
    task_id: int
    received: bool
    completed: bool
    received_at: int
    completed_at: int

    def sort_key(self) -> tuple[int, int]:
        return (self.chapter_id, self.task_id)

    def to_payload(self) -> dict[str, Any]:
        return {
            "account_key": self.account_key,
            "role_id": self.role_id,
            "chapter_id": self.chapter_id,
            "task_id": self.task_id,
            "received": self.received,
            "completed": self.completed,
            "received_at": self.received_at,
            "completed_at": self.completed_at,
        }

    def replay_rows(self) -> tuple[tuple[int, int], ...]:
        """The client's own `S2C 386` rows, received before completed.

        ``initChapterTaskInfo(chapterId, taskId)`` marks one received task; a
        completed task is the same row under ``GetTaskCompletedId(taskId)`` and
        needs its own packet, because the client's container is a plain map.
        """

        rows: list[tuple[int, int]] = []
        if self.received:
            rows.append((self.chapter_id, self.task_id))
        if self.completed:
            rows.append((self.chapter_id, completed_task_id(self.task_id)))
        return tuple(rows)


def _row_from_payload(row: object) -> ChapterTaskRow:
    if not isinstance(row, dict) or set(row) != set(CHAPTER_TASK_ROW_KEYS):
        raise ChapterMapTaskError(
            REFUSAL_ROW_INVALID,
            f"a chapter-task row must carry exactly "
            f"{list(CHAPTER_TASK_ROW_KEYS)}, got {row!r}",
        )
    account_key = row["account_key"]
    if not isinstance(account_key, str) or not account_key:
        raise ChapterMapTaskError(
            REFUSAL_ROW_INVALID, f"a row account_key must be a string: {account_key!r}"
        )
    received = row["received"]
    completed = row["completed"]
    if not isinstance(received, bool) or not isinstance(completed, bool):
        raise ChapterMapTaskError(
            REFUSAL_ROW_INVALID, "a row's received/completed must be booleans"
        )
    if not received:
        # Only the receive/complete transitions ever write a row, and complete
        # requires a received row, so an unreceived row is not a state this
        # server can store -- refuse it instead of replaying it as progress.
        raise ChapterMapTaskError(
            REFUSAL_ROW_INVALID,
            "a stored chapter-task row must be received; refusing a row that "
            "asserts neither a receive nor a completed task",
        )
    return ChapterTaskRow(
        account_key=account_key,
        role_id=_require_int(row["role_id"], "role_id"),
        chapter_id=_require_int(row["chapter_id"], "chapter_id"),
        task_id=_require_int(row["task_id"], "task_id"),
        received=received,
        completed=completed,
        received_at=_require_int(row["received_at"], "received_at"),
        completed_at=_require_int(row["completed_at"], "completed_at"),
    )


def rows_payload(
    account_key: str, rows: Sequence[ChapterTaskRow]
) -> dict[str, Any]:
    """The one canonical meta payload for one account's rows."""

    ordered = sorted(rows, key=ChapterTaskRow.sort_key)
    return {
        "format": CHAPTER_TASK_META_FORMAT,
        "format_version": CHAPTER_TASK_META_FORMAT_VERSION,
        "account_key": account_key,
        "origin": CHAPTER_TASK_ORIGIN,
        "provenance": chapter_task_provenance(),
        "replaceable": CHAPTER_TASK_REPLACEABLE,
        "rows": [row.to_payload() for row in ordered],
    }


def encode_rows(account_key: str, rows: Sequence[ChapterTaskRow]) -> str:
    """The `role_store` codec: rows -> one canonical payload string."""

    return json.dumps(
        rows_payload(account_key, rows), ensure_ascii=False, sort_keys=True
    )


def parse_rows(raw: str, *, account_key: str | None = None) -> tuple[ChapterTaskRow, ...]:
    """Validate one stored payload, or refuse with a named reason.

    A damaged payload is never partially applied and never guessed: the caller
    turns the refusal into its own event.
    """

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_INVALID_JSON,
            f"chapter-task rows are not valid JSON: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_NOT_AN_OBJECT,
            f"chapter-task rows must be a JSON object, not "
            f"{type(payload).__name__}",
        )
    if payload.get("format") != CHAPTER_TASK_META_FORMAT:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_FORMAT_UNKNOWN,
            f"chapter-task rows carry format {payload.get('format')!r}, not "
            f"{CHAPTER_TASK_META_FORMAT!r}",
        )
    if payload.get("format_version") != CHAPTER_TASK_META_FORMAT_VERSION:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_FORMAT_VERSION,
            f"chapter-task rows format_version "
            f"{payload.get('format_version')!r} is not "
            f"{CHAPTER_TASK_META_FORMAT_VERSION}",
        )
    stored_account = payload.get("account_key")
    if not isinstance(stored_account, str) or not stored_account:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_ACCOUNT_MISMATCH,
            f"chapter-task rows carry no account_key: {stored_account!r}",
        )
    if account_key is not None and stored_account != account_key:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_ACCOUNT_MISMATCH,
            f"chapter-task rows name account {stored_account!r}, not "
            f"{account_key!r}",
        )
    if payload.get("origin") != CHAPTER_TASK_ORIGIN:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_ORIGIN_MISMATCH,
            f"chapter-task rows origin {payload.get('origin')!r} is not "
            f"{CHAPTER_TASK_ORIGIN!r}",
        )
    if payload.get("replaceable") is not CHAPTER_TASK_REPLACEABLE:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_FLAGS_MISMATCH,
            "chapter-task rows must stay replaceable=true",
        )
    if payload.get("provenance") != chapter_task_provenance():
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_PROVENANCE_MISMATCH,
            "chapter-task rows provenance must stay "
            f"{chapter_task_provenance()!r}; got {payload.get('provenance')!r}",
        )
    if set(payload) != CHAPTER_TASK_PAYLOAD_KEYS:
        raise ChapterMapTaskError(
            REFUSAL_PAYLOAD_KEYS_CHANGED,
            "chapter-task payload keys changed: "
            f"missing={sorted(CHAPTER_TASK_PAYLOAD_KEYS - set(payload))} "
            f"extra={sorted(set(payload) - CHAPTER_TASK_PAYLOAD_KEYS)}",
        )
    stored = payload.get("rows")
    if not isinstance(stored, list):
        raise ChapterMapTaskError(
            REFUSAL_ROWS_NOT_A_LIST,
            f"chapter-task rows must be a list, not {type(stored).__name__}",
        )
    rows = tuple(_row_from_payload(row) for row in stored)
    for row in rows:
        if row.account_key != stored_account:
            raise ChapterMapTaskError(
                REFUSAL_PAYLOAD_ACCOUNT_MISMATCH,
                "a chapter-task row names a different account than its payload",
            )
    keys = [row.sort_key() for row in rows]
    if len(set(keys)) != len(keys):
        raise ChapterMapTaskError(
            REFUSAL_ROW_INVALID,
            "chapter-task rows repeat one (chapter, task) pair",
        )
    return tuple(sorted(rows, key=ChapterTaskRow.sort_key))


# ------------------------------------------------------------------- refusals


class ChapterMapTaskError(ValueError):
    """A stored chapter-task payload is unusable; ``reason`` says which fact.

    The wire-refusal words and the payload-refusal words are one closed
    vocabulary so a report can name whichever layer refused.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


#: Payload-level refusal words (distinct from the wire-shape ones above).
REFUSAL_PAYLOAD_INVALID_JSON = "chapter_task_payload_invalid_json"
REFUSAL_PAYLOAD_NOT_AN_OBJECT = "chapter_task_payload_not_an_object"
REFUSAL_PAYLOAD_FORMAT_UNKNOWN = "chapter_task_payload_format_unknown"
REFUSAL_PAYLOAD_FORMAT_VERSION = "chapter_task_payload_format_version_unsupported"
REFUSAL_PAYLOAD_ACCOUNT_MISMATCH = "chapter_task_payload_account_mismatch"
REFUSAL_PAYLOAD_ORIGIN_MISMATCH = "chapter_task_payload_origin_mismatch"
REFUSAL_PAYLOAD_FLAGS_MISMATCH = "chapter_task_payload_flags_mismatch"
REFUSAL_PAYLOAD_PROVENANCE_MISMATCH = "chapter_task_payload_provenance_mismatch"
REFUSAL_PAYLOAD_KEYS_CHANGED = "chapter_task_payload_keys_changed"
REFUSAL_ROWS_NOT_A_LIST = "chapter_task_rows_not_a_list"
REFUSAL_ROW_INVALID = "chapter_task_row_invalid"

PAYLOAD_REFUSAL_CODES: tuple[str, ...] = (
    REFUSAL_PAYLOAD_INVALID_JSON,
    REFUSAL_PAYLOAD_NOT_AN_OBJECT,
    REFUSAL_PAYLOAD_FORMAT_UNKNOWN,
    REFUSAL_PAYLOAD_FORMAT_VERSION,
    REFUSAL_PAYLOAD_ACCOUNT_MISMATCH,
    REFUSAL_PAYLOAD_ORIGIN_MISMATCH,
    REFUSAL_PAYLOAD_FLAGS_MISMATCH,
    REFUSAL_PAYLOAD_PROVENANCE_MISMATCH,
    REFUSAL_PAYLOAD_KEYS_CHANGED,
    REFUSAL_ROWS_NOT_A_LIST,
    REFUSAL_ROW_INVALID,
)


# ------------------------------------------------------- the transition itself


def task_transition(
    current: ChapterTaskRow | None, action: int
) -> str:
    """The state-machine answer for one action against the stored row.

    Pure so the transition can be tested without a database, and so the store
    and the report cannot disagree:

    * receive, nothing stored or not yet received -> ``received``;
    * receive, already received -> ``already_handled`` (no downgrade, no write);
    * complete, received and not completed -> ``completed``;
    * complete, already completed -> ``already_handled``;
    * complete, never received -> ``not_received`` (the client's own
      ``isTaskReceived`` gate would have refused this, so it is not recorded).
    """

    if action not in TASK_ACTIONS:
        raise ChapterMapTaskError(
            REFUSAL_ACTION, f"unknown chapter-task action {action!r}"
        )
    received = bool(current is not None and current.received)
    completed = bool(current is not None and current.completed)
    if action == TASK_RECEIVE:
        return STATE_ALREADY_HANDLED if received else STATE_RECEIVED
    return STATE_ALREADY_HANDLED if completed else (
        STATE_COMPLETED if received else STATE_NOT_RECEIVED
    )


# --------------------------------------------------------------- re-derivation


def contract_canonical() -> str:
    statuses = ",".join(
        f"{name}:{value}" for value, name in sorted(TASK_STATUS_NAMES.items())
    )
    actions = ",".join(
        f"{name}:{value}" for value, name in sorted(TASK_ACTION_NAMES.items())
    )
    return (
        f"c2s={C2S_PROTOCOL_ID}:{C2S_PROTOCOL_NAME}:{C2S_FORMAT}"
        f"|s2c={S2C_PROTOCOL_ID}:{S2C_PROTOCOL_NAME}:{S2C_FORMAT}"
        f"|unlock={','.join(S2C_UNLOCK_NAMES)}"
        f"|actions={actions}"
        f"|completedIdOffset={COMPLETED_ID_OFFSET}"
        f"|statuses={statuses}"
    )


#: sha256 of :func:`contract_canonical`.  The analyzer prints the same digest; a
#: registration or constant drift changes it.
CONTRACT_DIGEST = "598b793ec4a06a6934a8b70a3a4194ac76ca73f93816cd91f9867e0192104876"


def contract_digest() -> str:
    return hashlib.sha256(contract_canonical().encode("utf-8")).hexdigest()


class ChapterMapTaskDrift(AssertionError):
    """The shipped client no longer matches the frozen contract."""


def verify_contract_digest() -> None:
    actual = contract_digest()
    if actual != CONTRACT_DIGEST:  # pragma: no cover - drift guard
        raise ChapterMapTaskDrift(
            f"contract digest {actual} != frozen {CONTRACT_DIGEST}"
        )


def evidence_fields() -> Mapping[str, Any]:
    """The bounded, reusable description every event and report quotes."""

    return {
        "c2s_protocol_id": C2S_PROTOCOL_ID,
        "c2s_protocol_name": C2S_PROTOCOL_NAME,
        "c2s_format": C2S_FORMAT,
        "s2c_protocol_id": S2C_PROTOCOL_ID,
        "s2c_protocol_name": S2C_PROTOCOL_NAME,
        "s2c_format": S2C_FORMAT,
        "lock_partner": S2C_PROTOCOL_NAME,
        "task_receive": TASK_RECEIVE,
        "task_complete": TASK_COMPLETE,
        "completed_id_offset": COMPLETED_ID_OFFSET,
        "contract_digest": CONTRACT_DIGEST,
    }
