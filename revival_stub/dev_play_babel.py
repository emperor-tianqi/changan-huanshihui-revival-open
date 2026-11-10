"""Explicit dev-play login state for the 通天塔 (babel) UI (TASK-0232).

The break this module serves is the one *before* any tower fight: on a fresh
permanent archive the client's own entry gate can never open, because

* ``functionOpen[15]`` (``openLevel = 29``, ``showChapter = 7``,
  ``chapter = 8``) makes the button visible only from the 7th passed chapter and
  openable only from level 29 and the 8th passed chapter (D-187);
* the client's level is the 5th ``S2C 10 returnRoleInfo`` value, and its passed
  chapter is counted from the ``S2C 390 initChapterItemInfo`` rows of type
  ``MAP_TYPE_EXIT = 3`` (``pkgChapterInfoMgr.getCurrentPassedChapter``);
* the babel screen itself reads ``S2C 580 returnBabelInfo``, which the client
  never requests.

A real archive cannot be edited to satisfy this, and the permanent QA profile
must not be written.  This module therefore owns one **explicit, profile-scoped
synthetic overlay** for a separate dev-play profile (``dev-babel-main``):

* the archive holds one ``meta`` value (no new table, no migration, no
  ``SCHEMA_VERSION`` change), written only by the ``seed`` command below;
* the server applies it only when three independent conditions hold: the archive
  *is* ``dev-babel-main``, the server was started with the explicit
  ``--dev-play-babel`` flag, and the meta value is present and valid.  Missing
  any one of them sends exactly the frozen v18 login stream;
* every value is labelled ``synthetic_compatibility`` (chosen for dev play, not
  recovered history), ``replaceable = true`` and ``historical = false``.  The
  EXIT row *indexes* are not chosen: they are the shipped ``type = 3`` node of
  each chapter map, re-derived in ``max_decoder`` by the test.

Nothing here writes a role level, a wallet, an inventory row or the permanent
``qa-changan-main`` archive.

Usage::

    python -m revival_stub.dev_play_babel seed   --profile dev-babel-main
    python -m revival_stub.dev_play_babel status --profile dev-babel-main
    python -m revival_stub.dev_play_babel remove --profile dev-babel-main
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .play_profile import (
    PROFILE_MODE_RESUME,
    PlayProfileError,
    select_play_profile,
)
from .role_store import RoleStore, RoleStoreError


# --------------------------------------------------------------- the profile

#: The only play profile this overlay may serve.  The permanent QA archive is
#: never written by this module, and no other name is accepted.
DEV_PLAY_BABEL_PROFILE_ID = "dev-babel-main"

#: The one ``meta`` key (structure 1) that carries the overlay.  It is
#: namespaced so it cannot collide with the archive's own bookkeeping keys.
DEV_PLAY_BABEL_META_KEY = "dev_play_babel_overlay"

#: The stored payload's own domain marker and structure version.
DEV_PLAY_BABEL_META_FORMAT = "revival-dev-play-babel-overlay"
DEV_PLAY_BABEL_META_FORMAT_VERSION = 1

#: Provenance of the overlay.  It says this is a local simulator dev play state,
#: never an original-server record.
DEV_PLAY_BABEL_ORIGIN = "revival_local_dev_play_babel_overlay"

#: The reason written into the login event's ``role_level_overlay_reason``.
DEV_PLAY_BABEL_LEVEL_OVERLAY_REASON = "dev_play_babel_overlay"

#: ``VALUE``/``SELECTION`` provenance words already used by the project.
PROVENANCE_DETERMINISTIC_RECOVERED = "deterministic_recovered"
PROVENANCE_SYNTHETIC_COMPATIBILITY = "synthetic_compatibility"


# ------------------------------------------------------------- the frozen values

#: ``functionOpen[15]`` (D-187): the client's own open/show thresholds.
BABEL_OPEN_LEVEL = 29
BABEL_OPEN_CHAPTER = 8
BABEL_SHOW_CHAPTER = 7

#: ``pkgGuankaMapConstMgr``: the client's own ``MAP_TYPE_EXIT``.
MAP_TYPE_EXIT = 3

#: One ``(chapter_id, MAP_TYPE_EXIT, exit_index)`` row per chapter 1..8.
#:
#: The *index* is the shipped chapter map's own ``type = 3`` node.  Chapters
#: 1..8 use ``guankaChapter[id].mapConf = "0".."7"`` and each
#: ``chapterMap<mapConf>.max`` holds exactly one EXIT node; its key/``index`` is
#: the third value below.  Provenance: ``deterministic_recovered`` (index) +
#: ``synthetic_compatibility`` (the choice to send these rows for dev play).
#: Re-derived by ``max_decoder/analyze_first_chapter.recover_chapter_map`` in
#: ``revival_stub/tests/test_dev_play_babel.py`` when the APK extraction is
#: present; the test skips (never invents) when it is not.
DEV_PLAY_BABEL_EXIT_ROWS: tuple[tuple[int, int, int], ...] = (
    (1, MAP_TYPE_EXIT, 15),
    (2, MAP_TYPE_EXIT, 39),
    (3, MAP_TYPE_EXIT, 45),
    (4, MAP_TYPE_EXIT, 2),
    (5, MAP_TYPE_EXIT, 2),
    (6, MAP_TYPE_EXIT, 2),
    (7, MAP_TYPE_EXIT, 10),
    (8, MAP_TYPE_EXIT, 13),
)

#: Where each frozen index was read from.  Kept as data so the report and a
#: reviewer can point at the exact shipped chunk.
DEV_PLAY_BABEL_EXIT_SOURCES: dict[int, tuple[str, str, int]] = {
    1: ("chapterMap0.max", "guankaChapter[1].mapConf='0'", 15),
    2: ("chapterMap1.max", "guankaChapter[2].mapConf='1'", 39),
    3: ("chapterMap2.max", "guankaChapter[3].mapConf='2'", 45),
    4: ("chapterMap3.max", "guankaChapter[4].mapConf='3'", 2),
    5: ("chapterMap4.max", "guankaChapter[5].mapConf='4'", 2),
    6: ("chapterMap5.max", "guankaChapter[6].mapConf='5'", 2),
    7: ("chapterMap6.max", "guankaChapter[7].mapConf='6'", 10),
    8: ("chapterMap7.max", "guankaChapter[8].mapConf='7'", 13),
}

#: ``S2C 580 returnBabelInfo`` is ``NNNNN`` and its handler calls
#: ``setCurFloor(f1, f3, f4, f5)`` -- field 2 is never read (D-187, U-1).
#: The dev-play starting state is the client's own UNLOCK enum with a fresh
#: floor/realm and no failures.
DEV_PLAY_BABEL_FLOOR = 1
DEV_PLAY_BABEL_FIELD_2_PLACEHOLDER = 0
DEV_PLAY_BABEL_STATE_UNLOCK = 20
DEV_PLAY_BABEL_FAIL_COUNT = 0
DEV_PLAY_BABEL_REALM = 1
DEV_PLAY_BABEL_BABEL_INFO: tuple[int, int, int, int, int] = (
    DEV_PLAY_BABEL_FLOOR,
    DEV_PLAY_BABEL_FIELD_2_PLACEHOLDER,
    DEV_PLAY_BABEL_STATE_UNLOCK,
    DEV_PLAY_BABEL_FAIL_COUNT,
    DEV_PLAY_BABEL_REALM,
)

#: The client's own ``pkgBabelConst.FLOOR_STATE`` names, for logs only.
BABEL_STATE_NAMES: dict[int, str] = {0: "LOCK", 20: "UNLOCK", 30: "GO_ON"}

#: Field 2 is a *placeholder N*, not a value with a meaning: the client's
#: ``returnBabelInfo`` handler moves R1/R3/R4/R5 into ``setCurFloor`` and never
#: reads R2.  If a later build starts reading it, this is the label that must
#: fail closed rather than guess.
BABEL_FIELD_2_SEMANTICS = "unknown_placeholder"
BABEL_FIELD_2_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY


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
REASON_SEEDED_AT_UNREADABLE = "overlay_seeded_at_unreadable"

DEV_PLAY_BABEL_REFUSAL_REASONS = (
    REASON_PROFILE_NOT_ALLOWED,
    REASON_META_INVALID_JSON,
    REASON_META_NOT_AN_OBJECT,
    REASON_FORMAT_UNKNOWN,
    REASON_FORMAT_VERSION_UNSUPPORTED,
    REASON_PROFILE_MISMATCH,
    REASON_ORIGIN_MISMATCH,
    REASON_VALUES_MISMATCH,
    REASON_FLAGS_MISMATCH,
    REASON_SEEDED_AT_UNREADABLE,
)


class DevPlayBabelError(RuntimeError):
    """The overlay cannot be applied.  ``reason`` is one closed-vocabulary word."""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in DEV_PLAY_BABEL_REFUSAL_REASONS:  # pragma: no cover
            raise ValueError(f"unknown dev-play babel refusal reason {reason!r}")
        super().__init__(message)
        self.reason = reason


def require_profile(profile_id: object) -> str:
    """Return ``dev-babel-main``, or refuse every other name (incl. the QA one)."""

    if profile_id != DEV_PLAY_BABEL_PROFILE_ID:
        raise DevPlayBabelError(
            REASON_PROFILE_NOT_ALLOWED,
            f"the dev-play babel overlay is restricted to "
            f"{DEV_PLAY_BABEL_PROFILE_ID!r}; refusing {profile_id!r} "
            f"(the permanent qa-changan-main archive is never written)",
        )
    return DEV_PLAY_BABEL_PROFILE_ID


# -------------------------------------------------------------------- overlay


@dataclass(frozen=True, slots=True)
class BabelOverlay:
    """One validated overlay.  Its values are canonical, never caller-chosen."""

    profile_id: str
    seeded_at: int

    @property
    def role_level(self) -> int:
        return BABEL_OPEN_LEVEL

    @property
    def exit_rows(self) -> tuple[tuple[int, int, int], ...]:
        return DEV_PLAY_BABEL_EXIT_ROWS

    def babel_info_fields(self) -> tuple[int, int, int, int, int]:
        return DEV_PLAY_BABEL_BABEL_INFO

    @property
    def floor(self) -> int:
        return DEV_PLAY_BABEL_FLOOR

    @property
    def field2(self) -> int:
        return DEV_PLAY_BABEL_FIELD_2_PLACEHOLDER

    @property
    def state(self) -> int:
        return DEV_PLAY_BABEL_STATE_UNLOCK

    @property
    def fail_count(self) -> int:
        return DEV_PLAY_BABEL_FAIL_COUNT

    @property
    def realm(self) -> int:
        return DEV_PLAY_BABEL_REALM

    @property
    def state_name(self) -> str:
        return BABEL_STATE_NAMES[self.state]

    def to_payload(self) -> dict[str, Any]:
        return overlay_payload(self.profile_id, self.seeded_at)


def overlay_payload(profile_id: str, seeded_at: int) -> dict[str, Any]:
    """The one canonical meta payload.  Provenance travels with every value."""

    return {
        "format": DEV_PLAY_BABEL_META_FORMAT,
        "format_version": DEV_PLAY_BABEL_META_FORMAT_VERSION,
        "profile_id": profile_id,
        "origin": DEV_PLAY_BABEL_ORIGIN,
        "seeded_at": int(seeded_at),
        "role_level": BABEL_OPEN_LEVEL,
        "exit_rows": [list(row) for row in DEV_PLAY_BABEL_EXIT_ROWS],
        "babel_info": {
            "floor": DEV_PLAY_BABEL_FLOOR,
            "field2": DEV_PLAY_BABEL_FIELD_2_PLACEHOLDER,
            "state": DEV_PLAY_BABEL_STATE_UNLOCK,
            "failCount": DEV_PLAY_BABEL_FAIL_COUNT,
            "realm": DEV_PLAY_BABEL_REALM,
        },
        "provenance": {
            "role_level": PROVENANCE_SYNTHETIC_COMPATIBILITY,
            "exit_rows": (
                f"{PROVENANCE_DETERMINISTIC_RECOVERED}"
                f"+{PROVENANCE_SYNTHETIC_COMPATIBILITY}"
            ),
            "babel_info": PROVENANCE_SYNTHETIC_COMPATIBILITY,
            "field2": BABEL_FIELD_2_PROVENANCE,
        },
        "field2_semantics": BABEL_FIELD_2_SEMANTICS,
        "replaceable": True,
        "historical": False,
        "never_write": "qa-changan-main",
    }


def _int_value(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DevPlayBabelError(
            REASON_VALUES_MISMATCH,
            f"{field} must be an int, not {value!r}",
        )
    return value


def parse_overlay_payload(raw: str, *, profile_id: str) -> BabelOverlay:
    """Validate one stored payload, or refuse with a named reason.

    The stored value is a *marker for the canonical dev-play state*, never a
    source of arbitrary wire values: a level other than 29, a different EXIT
    row, a different babel state or a missing/reworded provenance is refused
    rather than sent.
    """

    require_profile(profile_id)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise DevPlayBabelError(
            REASON_META_INVALID_JSON, f"overlay meta is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise DevPlayBabelError(
            REASON_META_NOT_AN_OBJECT,
            f"overlay meta must be a JSON object, not {type(payload).__name__}",
        )
    if payload.get("format") != DEV_PLAY_BABEL_META_FORMAT:
        raise DevPlayBabelError(
            REASON_FORMAT_UNKNOWN,
            f"overlay meta carries format {payload.get('format')!r}, not "
            f"{DEV_PLAY_BABEL_META_FORMAT!r}",
        )
    if payload.get("format_version") != DEV_PLAY_BABEL_META_FORMAT_VERSION:
        raise DevPlayBabelError(
            REASON_FORMAT_VERSION_UNSUPPORTED,
            f"overlay meta format_version {payload.get('format_version')!r} is "
            f"not {DEV_PLAY_BABEL_META_FORMAT_VERSION}",
        )
    if payload.get("profile_id") != profile_id:
        raise DevPlayBabelError(
            REASON_PROFILE_MISMATCH,
            f"overlay meta names profile {payload.get('profile_id')!r}, not "
            f"{profile_id!r}",
        )
    if payload.get("origin") != DEV_PLAY_BABEL_ORIGIN:
        raise DevPlayBabelError(
            REASON_ORIGIN_MISMATCH,
            f"overlay meta origin {payload.get('origin')!r} is not "
            f"{DEV_PLAY_BABEL_ORIGIN!r}",
        )
    if payload.get("replaceable") is not True or payload.get("historical") is not False:
        raise DevPlayBabelError(
            REASON_FLAGS_MISMATCH,
            "overlay meta must be replaceable=true and historical=false",
        )
    seeded_at = _int_value(payload.get("seeded_at"), "seeded_at")
    if seeded_at < 0:
        raise DevPlayBabelError(
            REASON_SEEDED_AT_UNREADABLE,
            f"overlay meta has a negative seeded_at {seeded_at}",
        )
    role_level = _int_value(payload.get("role_level"), "role_level")
    if role_level != BABEL_OPEN_LEVEL:
        raise DevPlayBabelError(
            REASON_VALUES_MISMATCH,
            f"overlay role_level {role_level} is not the frozen "
            f"{BABEL_OPEN_LEVEL}",
        )
    rows = payload.get("exit_rows")
    if not isinstance(rows, list) or len(rows) != len(DEV_PLAY_BABEL_EXIT_ROWS):
        raise DevPlayBabelError(
            REASON_VALUES_MISMATCH,
            f"overlay exit_rows must be the {len(DEV_PLAY_BABEL_EXIT_ROWS)} "
            f"frozen rows, got {rows!r}",
        )
    for expected, row in zip(DEV_PLAY_BABEL_EXIT_ROWS, rows, strict=True):
        if (
            not isinstance(row, list)
            or len(row) != 3
            or tuple(_int_value(item, "exit_rows[]") for item in row) != expected
        ):
            raise DevPlayBabelError(
                REASON_VALUES_MISMATCH,
                f"overlay exit row {row!r} is not the frozen {expected!r}",
            )
    info = payload.get("babel_info")
    if not isinstance(info, dict):
        raise DevPlayBabelError(
            REASON_VALUES_MISMATCH, "overlay babel_info must be a JSON object"
        )
    expected_info = {
        "floor": DEV_PLAY_BABEL_FLOOR,
        "field2": DEV_PLAY_BABEL_FIELD_2_PLACEHOLDER,
        "state": DEV_PLAY_BABEL_STATE_UNLOCK,
        "failCount": DEV_PLAY_BABEL_FAIL_COUNT,
        "realm": DEV_PLAY_BABEL_REALM,
    }
    if info != expected_info:
        raise DevPlayBabelError(
            REASON_VALUES_MISMATCH,
            f"overlay babel_info {info!r} is not the frozen {expected_info!r}",
        )
    if payload.get("field2_semantics") != BABEL_FIELD_2_SEMANTICS:
        raise DevPlayBabelError(
            REASON_VALUES_MISMATCH,
            "overlay field2 must stay labelled "
            f"{BABEL_FIELD_2_SEMANTICS!r}; the client does not define it",
        )
    return BabelOverlay(profile_id=profile_id, seeded_at=seeded_at)


def load_overlay(store: RoleStore) -> BabelOverlay | None:
    """The stored overlay, ``None`` when absent, or a refusal if unusable.

    Absence is the normal "not enabled" case (the caller sends the frozen v18
    login stream).  A *present* value that does not validate is not absence:
    it raises so the caller can record the named refusal instead of hiding it.
    """

    raw = store.get_meta(DEV_PLAY_BABEL_META_KEY)
    if raw is None:
        return None
    profile_id = DEV_PLAY_BABEL_PROFILE_ID
    return parse_overlay_payload(raw, profile_id=profile_id)


def seed_overlay(
    store: RoleStore,
    *,
    profile_id: str,
    time_source: Callable[[], float] | None = None,
) -> tuple[BabelOverlay, bool]:
    """Write the canonical overlay once (idempotent); return ``(overlay, created)``."""

    require_profile(profile_id)
    clock = time_source or time.time
    previous = load_overlay(store)
    if previous is not None:
        return previous, False
    overlay = BabelOverlay(profile_id=profile_id, seeded_at=int(clock()))
    store.set_meta(
        DEV_PLAY_BABEL_META_KEY,
        json.dumps(overlay.to_payload(), ensure_ascii=False, sort_keys=True),
    )
    return overlay, True


def remove_overlay(store: RoleStore) -> bool:
    """Delete only this module's key; every other meta row is untouched."""

    return store.delete_meta(DEV_PLAY_BABEL_META_KEY)


def describe_overlay(store: RoleStore) -> dict[str, Any] | None:
    """The validated overlay as a bounded dict, or ``None`` when absent."""

    overlay = load_overlay(store)
    if overlay is None:
        return None
    return overlay.to_payload()


# ------------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m revival_stub.dev_play_babel",
        description=(
            "Seed, inspect or remove the explicit synthetic 通天塔 login state "
            "of the separate dev-play archive dev-babel-main.  The permanent "
            "qa-changan-main archive is never opened or written."
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
        help=f"the named durable play profile; only {DEV_PLAY_BABEL_PROFILE_ID}",
    )
    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_profile(args.profile)
    except DevPlayBabelError as exc:
        print(f"dev_play_babel: refusing: {exc}", file=sys.stderr)
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
        print(f"dev_play_babel: refusing: {exc}", file=sys.stderr)
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
        except DevPlayBabelError as exc:
            # A stored value that does not validate is never overwritten by
            # `seed` and never guessed by `status`: name the reason, refuse.
            print(
                f"dev_play_babel: refusing: {exc.reason}: {exc}",
                file=sys.stderr,
            )
            return 2
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover - the CLI entry itself
    raise SystemExit(main())


# ------------------------------------------------ babel fight / skip (TASK-0273)
#
# pkgUIBabelLost's skip button is the only sender of C2S 319 babelFight "NNN" (floor,
# SKIP==3, unsupplied third). The server answers S2C 579 babelResult "N" and the client's
# pkgBabel.c_result switches on it (TASK-0231 babel_fight.reply):
#
#   0 SUCC / 10 NOT_ENOUGH_QUAN / 20 FIGHT_FAIL / 30 FIGHT_SUCC / 40 SKIP_SUCC / 50 REALM_SUCC
#
# SKIP_SUCC is the only code that also destroys pkgUIBabelLost and calls warEndByClient(true),
# so it is the correct answer to a skip request.  On a session without the dev-play overlay the
# server refuses with NOT_ENOUGH_QUAN and changes nothing: the enum has no generic "refused"
# code, which is recorded as an UNKNOWN rather than invented.
BABEL_RESULT_SUCC = 0
BABEL_RESULT_NOT_ENOUGH_QUAN = 10
BABEL_RESULT_FIGHT_FAIL = 20
BABEL_RESULT_FIGHT_SUCC = 30
BABEL_RESULT_SKIP_SUCC = 40
BABEL_RESULT_REALM_SUCC = 50
BABEL_FIGHT_SKIP_FLAG = 3
BABEL_RESULT_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY


# TASK-0322: the skip costs a currency the client shows as 诛仙令 (role value 1-based 24). RUN23
# proved the skip worked but never debited it (free skipping). The count is kept in the archive meta
# table (role_wallet's CHECK admits only money/yinpo/jinpo) and re-seeded into the login value
# vector, so a skip now costs and the cost survives a relogin.
DEV_PLAY_BABEL_ZHUXIANLING_META_KEY = "dev_play_babel_zhuxianling"
DEV_PLAY_BABEL_ZHUXIANLING_DEFAULT = 5
DEV_PLAY_BABEL_SKIP_COST = 2


def zhuxianling(store):
    """The persisted 诛仙令 count, or the dev-play default when unset."""

    raw = store.get_meta(DEV_PLAY_BABEL_ZHUXIANLING_META_KEY)
    if raw is None:
        return DEV_PLAY_BABEL_ZHUXIANLING_DEFAULT
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEV_PLAY_BABEL_ZHUXIANLING_DEFAULT
    return max(0, value)


def spend_zhuxianling(store, cost=DEV_PLAY_BABEL_SKIP_COST):
    """Debit one skip from the archive and return (before, after)."""

    before = zhuxianling(store)
    after = max(0, before - max(0, int(cost)))
    store.set_meta(DEV_PLAY_BABEL_ZHUXIANLING_META_KEY, str(after))
    return before, after

def babel_info_fields_with_floor(fields, floor):
    """Copy one S2C 580 field tuple with the floor replaced (session-scoped advance)."""
    values = list(fields)
    values[0] = int(floor)
    return tuple(int(value) for value in values)


# ------------------------------------------------ persistent dev-play floor (TASK-0274)
#
# The floor the client shows comes only from S2C 580 (pkgBabelMod.setCurFloor), and a skip
# advances it client-side. Without persistence a relogin would replay the overlay's default and
# the tower would fall back to floor 1, so the dev-play floor is kept in the archive's existing
# role-scoped meta table (same table the overlay itself uses; no schema change).
DEV_PLAY_BABEL_FLOOR_META_KEY = "dev_play_babel_floor"
DEV_PLAY_BABEL_FLOOR_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY


def read_floor(store):
    """The persisted dev-play floor, or None when unset/unreadable (never raises)."""
    raw = store.get_meta(DEV_PLAY_BABEL_FLOOR_META_KEY)
    if raw is None:
        return None
    try:
        floor = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return floor if floor >= 1 else None


def write_floor(store, floor):
    """Persist one dev-play floor (>= 1) and return it."""
    value = max(1, int(floor))
    store.set_meta(DEV_PLAY_BABEL_FLOOR_META_KEY, str(value))
    return value


def clear_floor(store):
    """Remove the persisted floor. Returns True when a value was present."""
    return bool(store.delete_meta(DEV_PLAY_BABEL_FLOOR_META_KEY))
