"""Explicit dev-play resources for the separate ``dev-resource-main`` archive.

The Owner's 2026-10-24 direction (#2) is to let the game's own 祈灵 draw and
基础养成 run while the original producers of prayer tickets and currency are
still unrecovered.  The permanent ``qa-changan-main`` save must not be edited
for that, so this module owns one **explicit, profile-scoped synthetic overlay**
for a separate dev-play profile:

* the archive holds one ``meta`` value (no new table, no migration, no
  ``SCHEMA_VERSION`` change), written only by the ``seed`` command below;
* the server applies it only when three independent conditions hold: the archive
  *is* ``dev-resource-main``, the server was started with the explicit
  ``--dev-play-resources`` flag, and the meta value is present and valid;
* every value is labelled ``synthetic_compatibility`` (chosen for dev play, not
  recovered history), ``replaceable = true`` and ``historical = false``.

The canonical values are:

=======================  =======  =============================================
value                    amount   provenance
=======================  =======  =============================================
``role_level``           29       ``synthetic_compatibility`` (threshold
                                  ``functionOpen[15].openLevel`` is
                                  ``deterministic_recovered``)
``lottery_ticket``       30       ``synthetic_compatibility``
``lottery_ticket_advanced`` 30    ``synthetic_compatibility``
``money``                100000.0 ``synthetic_compatibility``
``yinpo``                50000    ``synthetic_compatibility``
``jinpo``                50000    ``synthetic_compatibility``
=======================  =======  =============================================

These are dev-play constants, never a recovered economy.  The wallet is
provisioned **once** into this archive's ``role_wallet`` (only missing rows), so
the client's own 强化 sink spends real persisted rows and a later re-login keeps
the spent balance.  Since TASK-0235 the two tickets have their own persisted
ledger (``dev_play_ticket_balance``, structure 1, no new table): the server
charges the client's own ``C2S 274``/``C2S 33`` draws in the same transaction
that writes the drawn ``card_instances`` rows, and the resulting balance is
replayed at the next login's ``S2C 27``.  The client never subtracts locally
(D-190), so without this ledger the 30/30 would refill on every login.  The
post-draw value packet (``S2C 24 returnOneValue_N``) and its position after the
result-end packet are an explicit **synthetic server choice**; which packet the
original server used, and its ordering against the lock release, stay UNKNOWN
(U2/U4).  Draws reuse the existing deterministic card fixture; they are not
probability results and the RNG/weight/pity authority stays UNKNOWN (D-147).

Nothing here writes a role level, a permanent QA archive, a profile version or a
wire-audit file.

Usage::

    python -m revival_stub.dev_play_resources seed   --profile dev-resource-main
    python -m revival_stub.dev_play_resources status --profile dev-resource-main
    python -m revival_stub.dev_play_resources remove --profile dev-resource-main
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .owner_draw_test import (
    DETERMINISTIC_FIXTURE_KIND,
    DETERMINISTIC_FIXTURE_ORIGIN,
    LOTTERY_TICKET_ADVANCED_REWARD_TYPE,
    LOTTERY_TICKET_ADVANCED_VALUE_INDEX,
    LOTTERY_TICKET_ADVANCED_WIRE_POSITION,
    LOTTERY_TICKET_REWARD_TYPE,
    LOTTERY_TICKET_VALUE_INDEX,
    LOTTERY_TICKET_WIRE_POSITION,
)
from .play_profile import (
    PROFILE_MODE_RESUME,
    PlayProfileError,
    select_play_profile,
)
from .role_store import RoleStore, RoleStoreError


# --------------------------------------------------------------- the profile

#: The only play profile this overlay may serve.  The permanent QA archive is
#: never written by this module, and no other name is accepted.
DEV_PLAY_RESOURCES_PROFILE_ID = "dev-resource-main"

#: The one ``meta`` key (structure 1) that carries the overlay.
DEV_PLAY_RESOURCES_META_KEY = "dev_play_resources_overlay"

#: The stored payload's own domain marker and structure version.
DEV_PLAY_RESOURCES_META_FORMAT = "revival-dev-play-resources-overlay"
DEV_PLAY_RESOURCES_META_FORMAT_VERSION = 1

#: Provenance of the overlay.  It says this is a local simulator dev play state,
#: never an original-server record.  It is also the ``provenance`` column value
#: of every wallet row this module provisions.
DEV_PLAY_RESOURCES_ORIGIN = "revival_local_dev_play_resources_overlay"

#: The event/caller word for "this value came from the dev-play overlay".
DEV_PLAY_RESOURCES_VALUE_ORIGIN = "dev_play_resources_overlay"

#: The reason written into the login event's ``role_level_overlay_reason``.
DEV_PLAY_RESOURCES_LEVEL_OVERLAY_REASON = "dev_play_resource_overlay"

#: ``VALUE``/``SELECTION`` provenance words already used by the project.
PROVENANCE_DETERMINISTIC_RECOVERED = "deterministic_recovered"
PROVENANCE_SYNTHETIC_COMPATIBILITY = "synthetic_compatibility"

#: The canonical overlay marker.  A stored value with any other state is not a
#: different kind of overlay; it is a damaged marker and is refused.
DEV_PLAY_RESOURCES_STATE = "seeded"

#: The one payload key set, kept as data so validation and the report agree.
DEV_PLAY_RESOURCES_PAYLOAD_KEYS = frozenset(
    {
        "format",
        "format_version",
        "profile_id",
        "origin",
        "seeded_at",
        "state",
        "role_level",
        "lottery_ticket",
        "lottery_ticket_advanced",
        "wallet",
        "provenance",
        "replaceable",
        "historical",
        "never_write",
    }
)


# ------------------------------------------------------------- the frozen values

#: ``functionOpen[15].openLevel`` (D-187): the client's own babel gate.  The
#: number is recovered; using it to announce a dev-play level is synthetic.
DEV_PLAY_RESOURCES_ROLE_LEVEL = 29

#: The client's own S2C 27 lottery-ticket slots (pinned by owner_draw_test).
DEV_PLAY_RESOURCES_LOTTERY_TICKET = 30
DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED = 30

#: ``(value_name, amount)`` in the wallet's own wire order: money (index 0),
#: yinpo (index 6), jinpo (index 7).  These are dev-play constants, not an
#: original economy.
DEV_PLAY_RESOURCES_WALLET: tuple[tuple[str, float], ...] = (
    ("money", 100000.0),
    ("yinpo", 50000.0),
    ("jinpo", 50000.0),
)

#: The canonical deterministic fixture base id for dev draws.  The suffix is
#: the same process-wide serial the Owner overlay uses; the result is still a
#: deterministic fixture, not a probability result.
DEV_PLAY_RESOURCES_FIXTURE_ID = "dev-resource-draw"
DEV_PLAY_RESOURCES_TICKET_COUNTS: tuple[int, int] = (
    DEV_PLAY_RESOURCES_LOTTERY_TICKET,
    DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED,
)

#: Wire facts re-used from the Owner overlay so the two overlays cannot drift.
DEV_PLAY_RESOURCES_TICKET_VALUES: tuple[dict[str, int], ...] = (
    {
        "value_name": "lottery_ticket",
        "value_index": LOTTERY_TICKET_VALUE_INDEX,
        "wire_position": LOTTERY_TICKET_WIRE_POSITION,
        "reward_type": LOTTERY_TICKET_REWARD_TYPE,
        "amount": DEV_PLAY_RESOURCES_LOTTERY_TICKET,
    },
    {
        "value_name": "lottery_ticket_advanced",
        "value_index": LOTTERY_TICKET_ADVANCED_VALUE_INDEX,
        "wire_position": LOTTERY_TICKET_ADVANCED_WIRE_POSITION,
        "reward_type": LOTTERY_TICKET_ADVANCED_REWARD_TYPE,
        "amount": DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED,
    },
)

#: The fixture origin/kind the draw handler already writes for the Owner
#: overlay.  ``card_instances`` rows are replayed only while this overlay is
#: active, exactly like the Owner overlay's fixture rows.
DEV_PLAY_RESOURCES_FIXTURE_ORIGIN = DETERMINISTIC_FIXTURE_ORIGIN
DEV_PLAY_RESOURCES_FIXTURE_KIND = DETERMINISTIC_FIXTURE_KIND


# ------------------------------------------------- the persisted ticket ledger
#
# TASK-0234/D-190 proved the draw chain never subtracts locally: the client only
# redraws a ticket count after a server value packet, and the matching single
# value packet is ``S2C 24 returnOneValue_N``.  The two balances therefore need
# a server-side home.  They live in the archive's existing ``meta`` table
# (structure 1) under a second key -- no new table, no migration, no
# ``SCHEMA_VERSION`` change -- and every read is a strict canonical-payload
# check, so a tampered or half-written marker is refused with a named reason
# instead of being sent as a balance.

#: The one ``meta`` key that carries the two current ticket balances.
DEV_PLAY_TICKET_BALANCE_META_KEY = "dev_play_ticket_balance"

#: The ledger payload's own domain marker and structure version.
DEV_PLAY_TICKET_BALANCE_META_FORMAT = "revival-dev-play-ticket-balance"
DEV_PLAY_TICKET_BALANCE_META_FORMAT_VERSION = 1

#: Provenance of the ledger itself.  It is a local simulator balance, never an
#: original-server record; each value it replays is labelled
#: ``synthetic_compatibility``.
DEV_PLAY_TICKET_BALANCE_ORIGIN = "revival_local_dev_play_ticket_balance"
DEV_PLAY_TICKET_BALANCE_PROVENANCE = PROVENANCE_SYNTHETIC_COMPATIBILITY

#: The two balance fields, in the client's own S2C 27 wire order (index 18
#: before index 36).  They are also the ``value_name`` keys of the ledger.
DEV_PLAY_TICKET_BALANCE_FIELDS: tuple[str, str] = (
    "lottery_ticket",
    "lottery_ticket_advanced",
)

#: The one payload key set, kept as data so validation and the report agree.
DEV_PLAY_TICKET_BALANCE_PAYLOAD_KEYS = frozenset(
    {
        "format",
        "format_version",
        "profile_id",
        "origin",
        *DEV_PLAY_TICKET_BALANCE_FIELDS,
        "provenance",
        "replaceable",
        "historical",
        "never_write",
    }
)

#: The seed balance: the same 30/30 the TASK-0233 overlay announces.  It is a
#: dev-play constant, not a recovered economy.
DEV_PLAY_TICKET_BALANCE_SEED: dict[str, int] = {
    "lottery_ticket": DEV_PLAY_RESOURCES_LOTTERY_TICKET,
    "lottery_ticket_advanced": DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED,
}

#: reward type -> ledger field.  The two pairs come from the same pinned
#: ``owner_draw_test`` constants the S2C 27 vector uses.
TICKET_BALANCE_FIELD_FOR_REWARD_TYPE: dict[int, str] = {
    LOTTERY_TICKET_REWARD_TYPE: "lottery_ticket",
    LOTTERY_TICKET_ADVANCED_REWARD_TYPE: "lottery_ticket_advanced",
}


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

#: The ticket ledger's own closed vocabulary.  A damaged balance marker is not
#: the overlay marker's failure, so it names its own fact.
REASON_TICKET_BALANCE_INVALID_JSON = "ticket_balance_invalid_json"
REASON_TICKET_BALANCE_NOT_AN_OBJECT = "ticket_balance_not_an_object"
REASON_TICKET_BALANCE_FORMAT_UNKNOWN = "ticket_balance_format_unknown"
REASON_TICKET_BALANCE_FORMAT_VERSION = "ticket_balance_format_version_unsupported"
REASON_TICKET_BALANCE_PROFILE_MISMATCH = "ticket_balance_profile_mismatch"
REASON_TICKET_BALANCE_ORIGIN_MISMATCH = "ticket_balance_origin_mismatch"
REASON_TICKET_BALANCE_VALUES_MISMATCH = "ticket_balance_values_mismatch"
REASON_TICKET_BALANCE_FLAGS_MISMATCH = "ticket_balance_flags_mismatch"
REASON_TICKET_BALANCE_NEVER_WRITE_MISMATCH = "ticket_balance_never_write_mismatch"

DEV_PLAY_RESOURCES_REFUSAL_REASONS = (
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
    REASON_TICKET_BALANCE_INVALID_JSON,
    REASON_TICKET_BALANCE_NOT_AN_OBJECT,
    REASON_TICKET_BALANCE_FORMAT_UNKNOWN,
    REASON_TICKET_BALANCE_FORMAT_VERSION,
    REASON_TICKET_BALANCE_PROFILE_MISMATCH,
    REASON_TICKET_BALANCE_ORIGIN_MISMATCH,
    REASON_TICKET_BALANCE_VALUES_MISMATCH,
    REASON_TICKET_BALANCE_FLAGS_MISMATCH,
    REASON_TICKET_BALANCE_NEVER_WRITE_MISMATCH,
)


class DevPlayResourcesError(RuntimeError):
    """The overlay cannot be applied.  ``reason`` is one closed-vocabulary word."""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in DEV_PLAY_RESOURCES_REFUSAL_REASONS:  # pragma: no cover
            raise ValueError(f"unknown dev-play resources refusal reason {reason!r}")
        super().__init__(message)
        self.reason = reason


def require_profile(profile_id: object) -> str:
    """Return ``dev-resource-main``, or refuse every other name (incl. the QA one)."""

    if profile_id != DEV_PLAY_RESOURCES_PROFILE_ID:
        raise DevPlayResourcesError(
            REASON_PROFILE_NOT_ALLOWED,
            f"the dev-play resources overlay is restricted to "
            f"{DEV_PLAY_RESOURCES_PROFILE_ID!r}; refusing {profile_id!r} "
            f"(the permanent qa-changan-main archive is never written)",
        )
    return DEV_PLAY_RESOURCES_PROFILE_ID


# -------------------------------------------------------------------- overlay


@dataclass(frozen=True, slots=True)
class ResourcesOverlay:
    """One validated overlay.  Its values are canonical, never caller-chosen."""

    profile_id: str
    seeded_at: int

    @property
    def role_level(self) -> int:
        return DEV_PLAY_RESOURCES_ROLE_LEVEL

    @property
    def lottery_ticket(self) -> int:
        return DEV_PLAY_RESOURCES_LOTTERY_TICKET

    @property
    def lottery_ticket_advanced(self) -> int:
        return DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED

    @property
    def wallet_values(self) -> dict[str, float]:
        return {name: float(amount) for name, amount in DEV_PLAY_RESOURCES_WALLET}

    @property
    def ticket_values(self) -> tuple[dict[str, int], ...]:
        return tuple(dict(item) for item in DEV_PLAY_RESOURCES_TICKET_VALUES)

    @property
    def fixture_id_base(self) -> str:
        return DEV_PLAY_RESOURCES_FIXTURE_ID

    def to_payload(self) -> dict[str, Any]:
        return overlay_payload(self.profile_id, self.seeded_at)


def overlay_payload(profile_id: str, seeded_at: int) -> dict[str, Any]:
    """The one canonical meta payload.  Provenance travels with every value."""

    return {
        "format": DEV_PLAY_RESOURCES_META_FORMAT,
        "format_version": DEV_PLAY_RESOURCES_META_FORMAT_VERSION,
        "profile_id": profile_id,
        "origin": DEV_PLAY_RESOURCES_ORIGIN,
        "seeded_at": int(seeded_at),
        "state": DEV_PLAY_RESOURCES_STATE,
        "role_level": DEV_PLAY_RESOURCES_ROLE_LEVEL,
        "lottery_ticket": DEV_PLAY_RESOURCES_LOTTERY_TICKET,
        "lottery_ticket_advanced": DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED,
        "wallet": {
            name: float(amount) for name, amount in DEV_PLAY_RESOURCES_WALLET
        },
        "provenance": {
            "role_level": PROVENANCE_SYNTHETIC_COMPATIBILITY,
            "role_level_threshold": PROVENANCE_DETERMINISTIC_RECOVERED,
            "lottery_ticket": PROVENANCE_SYNTHETIC_COMPATIBILITY,
            "lottery_ticket_advanced": PROVENANCE_SYNTHETIC_COMPATIBILITY,
            "wallet": PROVENANCE_SYNTHETIC_COMPATIBILITY,
        },
        "replaceable": True,
        "historical": False,
        "never_write": "qa-changan-main",
    }


def _int_value(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DevPlayResourcesError(
            REASON_VALUES_MISMATCH,
            f"{field} must be an int, not {value!r}",
        )
    return value


def parse_overlay_payload(raw: str, *, profile_id: str) -> ResourcesOverlay:
    """Validate one stored payload, or refuse with a named reason.

    The stored value is a *marker for the canonical dev-play state*, never a
    source of arbitrary wire values: a different level, different ticket or
    wallet amounts, an extra/missing key or a reworded provenance is refused
    rather than sent or partially applied.
    """

    require_profile(profile_id)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise DevPlayResourcesError(
            REASON_META_INVALID_JSON, f"overlay meta is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise DevPlayResourcesError(
            REASON_META_NOT_AN_OBJECT,
            f"overlay meta must be a JSON object, not {type(payload).__name__}",
        )
    if payload.get("format") != DEV_PLAY_RESOURCES_META_FORMAT:
        raise DevPlayResourcesError(
            REASON_FORMAT_UNKNOWN,
            f"overlay meta carries format {payload.get('format')!r}, not "
            f"{DEV_PLAY_RESOURCES_META_FORMAT!r}",
        )
    if payload.get("format_version") != DEV_PLAY_RESOURCES_META_FORMAT_VERSION:
        raise DevPlayResourcesError(
            REASON_FORMAT_VERSION_UNSUPPORTED,
            f"overlay meta format_version {payload.get('format_version')!r} is "
            f"not {DEV_PLAY_RESOURCES_META_FORMAT_VERSION}",
        )
    if payload.get("profile_id") != profile_id:
        raise DevPlayResourcesError(
            REASON_PROFILE_MISMATCH,
            f"overlay meta names profile {payload.get('profile_id')!r}, not "
            f"{profile_id!r}",
        )
    if payload.get("origin") != DEV_PLAY_RESOURCES_ORIGIN:
        raise DevPlayResourcesError(
            REASON_ORIGIN_MISMATCH,
            f"overlay meta origin {payload.get('origin')!r} is not "
            f"{DEV_PLAY_RESOURCES_ORIGIN!r}",
        )
    if payload.get("never_write") != "qa-changan-main":
        raise DevPlayResourcesError(
            REASON_NEVER_WRITE_MISMATCH,
            "overlay meta must keep never_write='qa-changan-main', got "
            f"{payload.get('never_write')!r}",
        )
    if payload.get("replaceable") is not True or payload.get("historical") is not False:
        raise DevPlayResourcesError(
            REASON_FLAGS_MISMATCH,
            "overlay meta must be replaceable=true and historical=false",
        )
    seeded_at = _int_value(payload.get("seeded_at"), "seeded_at")
    if seeded_at < 0:
        raise DevPlayResourcesError(
            REASON_SEEDED_AT_UNREADABLE,
            f"overlay meta has a negative seeded_at {seeded_at}",
        )
    if payload.get("state") != DEV_PLAY_RESOURCES_STATE:
        raise DevPlayResourcesError(
            REASON_VALUES_MISMATCH,
            f"overlay state {payload.get('state')!r} is not "
            f"{DEV_PLAY_RESOURCES_STATE!r}",
        )
    role_level = _int_value(payload.get("role_level"), "role_level")
    if role_level != DEV_PLAY_RESOURCES_ROLE_LEVEL:
        raise DevPlayResourcesError(
            REASON_VALUES_MISMATCH,
            f"overlay role_level {role_level} is not the frozen "
            f"{DEV_PLAY_RESOURCES_ROLE_LEVEL}",
        )
    for name, expected in (
        ("lottery_ticket", DEV_PLAY_RESOURCES_LOTTERY_TICKET),
        ("lottery_ticket_advanced", DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED),
    ):
        value = _int_value(payload.get(name), name)
        if value != expected:
            raise DevPlayResourcesError(
                REASON_VALUES_MISMATCH,
                f"overlay {name} {value} is not the frozen {expected}",
            )
    wallet = payload.get("wallet")
    expected_wallet = {
        name: float(amount) for name, amount in DEV_PLAY_RESOURCES_WALLET
    }
    if not isinstance(wallet, dict) or wallet != expected_wallet:
        raise DevPlayResourcesError(
            REASON_VALUES_MISMATCH,
            f"overlay wallet {wallet!r} is not the frozen {expected_wallet!r}",
        )
    expected_provenance = overlay_payload(profile_id, seeded_at)["provenance"]
    if payload.get("provenance") != expected_provenance:
        raise DevPlayResourcesError(
            REASON_VALUES_MISMATCH,
            "overlay provenance must stay "
            f"{expected_provenance!r}; got {payload.get('provenance')!r}",
        )
    if set(payload) != DEV_PLAY_RESOURCES_PAYLOAD_KEYS:
        raise DevPlayResourcesError(
            REASON_VALUES_MISMATCH,
            "overlay payload keys changed: "
            f"missing={sorted(DEV_PLAY_RESOURCES_PAYLOAD_KEYS - set(payload))} "
            f"extra={sorted(set(payload) - DEV_PLAY_RESOURCES_PAYLOAD_KEYS)}",
        )
    return ResourcesOverlay(profile_id=profile_id, seeded_at=seeded_at)


def load_overlay(store: RoleStore) -> ResourcesOverlay | None:
    """The stored overlay, ``None`` when absent, or a refusal if unusable."""

    raw = store.get_meta(DEV_PLAY_RESOURCES_META_KEY)
    if raw is None:
        return None
    return parse_overlay_payload(raw, profile_id=DEV_PLAY_RESOURCES_PROFILE_ID)


def seed_overlay(
    store: RoleStore,
    *,
    profile_id: str,
    time_source: Callable[[], float] | None = None,
) -> tuple[ResourcesOverlay, bool]:
    """Write the canonical overlay once (idempotent); return ``(overlay, created)``."""

    require_profile(profile_id)
    clock = time_source or time.time
    previous = load_overlay(store)
    if previous is not None:
        return previous, False
    overlay = ResourcesOverlay(profile_id=profile_id, seeded_at=int(clock()))
    store.set_meta(
        DEV_PLAY_RESOURCES_META_KEY,
        json.dumps(overlay.to_payload(), ensure_ascii=False, sort_keys=True),
    )
    return overlay, True


def remove_overlay(store: RoleStore) -> bool:
    """Delete only this module's key; every other meta row is untouched."""

    return store.delete_meta(DEV_PLAY_RESOURCES_META_KEY)


def describe_overlay(store: RoleStore) -> dict[str, Any] | None:
    """The validated overlay as a bounded dict, or ``None`` when absent."""

    overlay = load_overlay(store)
    if overlay is None:
        return None
    return overlay.to_payload()


# ------------------------------------------------------- the ticket ledger API


@dataclass(frozen=True, slots=True)
class TicketBalance:
    """The two persisted ticket counts of the dev-play archive."""

    lottery_ticket: int
    lottery_ticket_advanced: int

    @property
    def counts(self) -> dict[str, int]:
        return {
            "lottery_ticket": int(self.lottery_ticket),
            "lottery_ticket_advanced": int(self.lottery_ticket_advanced),
        }

    def amount(self, reward_type: int) -> int:
        """The balance of one reward type (252 / 262), or a named refusal."""

        field = TICKET_BALANCE_FIELD_FOR_REWARD_TYPE.get(int(reward_type))
        if field is None:
            raise DevPlayResourcesError(
                REASON_TICKET_BALANCE_VALUES_MISMATCH,
                f"reward type {reward_type!r} is not a ticket balance field",
            )
        return int(self.counts[field])

    def to_payload(self, profile_id: str) -> dict[str, Any]:
        return ticket_balance_payload(
            profile_id,
            lottery_ticket=self.lottery_ticket,
            lottery_ticket_advanced=self.lottery_ticket_advanced,
        )


def _balance_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_VALUES_MISMATCH,
            f"ticket balance {field} must be an int, not {value!r}",
        )
    if value < 0:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_VALUES_MISMATCH,
            f"ticket balance {field} is negative: {value}",
        )
    return value


def ticket_balance_payload(
    profile_id: str, *, lottery_ticket: int, lottery_ticket_advanced: int
) -> dict[str, Any]:
    """The one canonical ledger payload.  Every field is a fixed word/value."""

    return {
        "format": DEV_PLAY_TICKET_BALANCE_META_FORMAT,
        "format_version": DEV_PLAY_TICKET_BALANCE_META_FORMAT_VERSION,
        "profile_id": profile_id,
        "origin": DEV_PLAY_TICKET_BALANCE_ORIGIN,
        "lottery_ticket": _balance_int(lottery_ticket, "lottery_ticket"),
        "lottery_ticket_advanced": _balance_int(
            lottery_ticket_advanced, "lottery_ticket_advanced"
        ),
        "provenance": DEV_PLAY_TICKET_BALANCE_PROVENANCE,
        "replaceable": True,
        "historical": False,
        "never_write": "qa-changan-main",
    }


def parse_ticket_balance(raw: str, *, profile_id: str) -> TicketBalance:
    """Validate one stored ledger payload, or refuse with a named reason.

    A stored balance is a *ledger row*, so the two counts are the one part that
    may differ from the seed; every other field must still be the canonical
    marker.  A different profile, origin, provenance, flags, ``never_write`` or
    key set is refused rather than partially applied.
    """

    require_profile(profile_id)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_INVALID_JSON,
            f"ticket balance meta is not valid JSON: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_NOT_AN_OBJECT,
            f"ticket balance meta must be a JSON object, not "
            f"{type(payload).__name__}",
        )
    if payload.get("format") != DEV_PLAY_TICKET_BALANCE_META_FORMAT:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_FORMAT_UNKNOWN,
            f"ticket balance meta carries format {payload.get('format')!r}, not "
            f"{DEV_PLAY_TICKET_BALANCE_META_FORMAT!r}",
        )
    if (
        payload.get("format_version")
        != DEV_PLAY_TICKET_BALANCE_META_FORMAT_VERSION
    ):
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_FORMAT_VERSION,
            "ticket balance meta format_version "
            f"{payload.get('format_version')!r} is not "
            f"{DEV_PLAY_TICKET_BALANCE_META_FORMAT_VERSION}",
        )
    if payload.get("profile_id") != profile_id:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_PROFILE_MISMATCH,
            f"ticket balance meta names profile {payload.get('profile_id')!r}, "
            f"not {profile_id!r}",
        )
    if payload.get("origin") != DEV_PLAY_TICKET_BALANCE_ORIGIN:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_ORIGIN_MISMATCH,
            f"ticket balance meta origin {payload.get('origin')!r} is not "
            f"{DEV_PLAY_TICKET_BALANCE_ORIGIN!r}",
        )
    if payload.get("never_write") != "qa-changan-main":
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_NEVER_WRITE_MISMATCH,
            "ticket balance meta must keep never_write='qa-changan-main', got "
            f"{payload.get('never_write')!r}",
        )
    if payload.get("replaceable") is not True or payload.get("historical") is not False:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_FLAGS_MISMATCH,
            "ticket balance meta must be replaceable=true and historical=false",
        )
    if payload.get("provenance") != DEV_PLAY_TICKET_BALANCE_PROVENANCE:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_VALUES_MISMATCH,
            "ticket balance meta provenance must stay "
            f"{DEV_PLAY_TICKET_BALANCE_PROVENANCE!r}, got "
            f"{payload.get('provenance')!r}",
        )
    if set(payload) != DEV_PLAY_TICKET_BALANCE_PAYLOAD_KEYS:
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_VALUES_MISMATCH,
            "ticket balance payload keys changed: "
            f"missing={sorted(DEV_PLAY_TICKET_BALANCE_PAYLOAD_KEYS - set(payload))} "
            f"extra={sorted(set(payload) - DEV_PLAY_TICKET_BALANCE_PAYLOAD_KEYS)}",
        )
    return TicketBalance(
        lottery_ticket=_balance_int(payload.get("lottery_ticket"), "lottery_ticket"),
        lottery_ticket_advanced=_balance_int(
            payload.get("lottery_ticket_advanced"), "lottery_ticket_advanced"
        ),
    )


def decode_ticket_balance(
    raw: str, *, profile_id: str = DEV_PLAY_RESOURCES_PROFILE_ID
) -> dict[str, int]:
    """The ``role_store`` codec: one stored payload -> the two counts."""

    return parse_ticket_balance(raw, profile_id=profile_id).counts


def encode_ticket_balance(
    counts: Mapping[str, int],
    *,
    profile_id: str = DEV_PLAY_RESOURCES_PROFILE_ID,
) -> str:
    """The ``role_store`` codec: the two counts -> the canonical payload."""

    if set(counts) != set(DEV_PLAY_TICKET_BALANCE_FIELDS):
        raise DevPlayResourcesError(
            REASON_TICKET_BALANCE_VALUES_MISMATCH,
            "ticket balance counts must name exactly "
            f"{sorted(DEV_PLAY_TICKET_BALANCE_FIELDS)}, got {sorted(counts)}",
        )
    payload = ticket_balance_payload(
        profile_id,
        lottery_ticket=counts["lottery_ticket"],
        lottery_ticket_advanced=counts["lottery_ticket_advanced"],
    )
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def load_ticket_balance(store: RoleStore) -> TicketBalance | None:
    """The stored ledger, ``None`` when absent, or a refusal if unusable."""

    raw = store.get_meta(DEV_PLAY_TICKET_BALANCE_META_KEY)
    if raw is None:
        return None
    return parse_ticket_balance(raw, profile_id=DEV_PLAY_RESOURCES_PROFILE_ID)


def seed_ticket_balance(store: RoleStore, *, profile_id: str) -> TicketBalance:
    """Reset the ledger to the canonical 30/30 seed (the ``seed`` command)."""

    require_profile(profile_id)
    balance = TicketBalance(
        lottery_ticket=DEV_PLAY_RESOURCES_LOTTERY_TICKET,
        lottery_ticket_advanced=DEV_PLAY_RESOURCES_LOTTERY_TICKET_ADVANCED,
    )
    store.set_meta(
        DEV_PLAY_TICKET_BALANCE_META_KEY,
        encode_ticket_balance(balance.counts, profile_id=profile_id),
    )
    return balance


def remove_ticket_balance(store: RoleStore) -> bool:
    """Delete only the ledger key; the overlay marker is a separate key."""

    return store.delete_meta(DEV_PLAY_TICKET_BALANCE_META_KEY)


def describe_ticket_balance(store: RoleStore) -> dict[str, Any] | None:
    """The validated ledger as a bounded dict, or ``None`` when absent."""

    balance = load_ticket_balance(store)
    if balance is None:
        return None
    return balance.to_payload(DEV_PLAY_RESOURCES_PROFILE_ID)


# ------------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m revival_stub.dev_play_resources",
        description=(
            "Seed, inspect or remove the explicit synthetic 祈灵券/货币/等级 overlay "
            "of the separate dev-play archive dev-resource-main.  The permanent "
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
        help=f"the named durable play profile; only {DEV_PLAY_RESOURCES_PROFILE_ID}",
    )
    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_profile(args.profile)
    except DevPlayResourcesError as exc:
        print(f"dev_play_resources: refusing: {exc}", file=sys.stderr)
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
        print(f"dev_play_resources: refusing: {exc}", file=sys.stderr)
        return 2
    try:
        try:
            if args.command == "seed":
                overlay, created = seed_overlay(store, profile_id=args.profile)
                # TASK-0235: `seed` is the reset affordance for the ledger, so
                # it always writes 30/30 again (the overlay marker above stays
                # idempotent and keeps its original `seeded_at`).
                balance = seed_ticket_balance(store, profile_id=args.profile)
                _print_json(
                    {
                        "command": "seed",
                        "profile_id": args.profile,
                        "created": created,
                        "overlay": overlay.to_payload(),
                        "ticket_balance": balance.to_payload(args.profile),
                    }
                )
                return 0
            if args.command == "status":
                _print_json(
                    {
                        "command": "status",
                        "profile_id": args.profile,
                        "overlay": describe_overlay(store),
                        "ticket_balance": describe_ticket_balance(store),
                    }
                )
                return 0
            removed = remove_overlay(store)
            balance_removed = remove_ticket_balance(store)
            _print_json(
                {
                    "command": "remove",
                    "profile_id": args.profile,
                    "removed": removed,
                    "ticket_balance_removed": balance_removed,
                }
            )
            return 0
        except DevPlayResourcesError as exc:
            # A stored value that does not validate is never overwritten by
            # `seed` and never guessed by `status`: name the reason, refuse.
            print(
                f"dev_play_resources: refusing: {exc.reason}: {exc}",
                file=sys.stderr,
            )
            return 2
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover - the CLI entry itself
    raise SystemExit(main())
