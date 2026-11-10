"""Recovered `C2S 411 inspectOrderValid` / `S2C 745 inspectOrderResult` contract.

The player-visible break this module serves (Issue #26, D-186): on the manual
2026-10-24 acceptance run the shop's buy button sent `C2S 411
inspectOrderValid`, the stub had no handler for it, the free-play path recorded
it as `known_unimplemented` and sent nothing -- and because the client's own
registration makes this a LOCK-SEND, the shop sat on its waiting overlay and the
socket died with `dReasonType:9999` after `LOCK_TIME = 15000` ms.

Everything below is read out of the shipped client, never guessed:

* The wire ids and formats are the client's own
  `registerC2S("pppp", "inspectOrderValid")` (``pkgScProtocolMgr.lua:953``) and
  `registerS2C("ppppa", "inspectOrderResult", "inspectOrderValid")`
  (``pkgScProtocolMgr.lua:966``).  The third S2C argument is the lock partner, so
  S2C 745 is the only thing that releases C2S 411.  Runtime ids 411 / 745 are
  the D-02 registration simulation's answer (C2S source ordinal 321 + 90 battle
  rows; S2C source ordinal 601 + 144 battle/transfer rows), re-derived by
  ``max_decoder/analyze_pre_scene.protocol_table()`` and by
  ``max_decoder/analyze_market_inspect_order.py``.
* The format char ``p`` is this archive's codec decoding of a 64-bit IEEE-754
  double (``codec._decode_scalar``); ``a`` is a length-prefixed string.
* The S2C handler is ``s2c.inspectOrderResult`` in
  ``socket/nc/pkgMarketNcMgr`` (PROTO 0.15, source lines 86-88).  It forwards its
  five decoded values straight to ``pkgRechargeCenterMgr.inspectOrderResult``
  as ``(dResult, dMarketType, dId, dCost, strCustomOrderId)``.
* ``pkgRechargeCenterMgr.inspectOrderResult`` (PROTO 0.1, source lines 21-50) is
  the whole client-visible behaviour.  It prints the five values, then:
    - ``dResult == e_tbDealResult.NUM_RESTRICT (1)`` -> ``pkgUIAlertMgr.alert2
      (_T('market_deal_result_num_restrict'))`` and ``RETURN``;
    - ``FEE_NOT_ENOUGH (2)``  -> ``alert2`` + return;
    - ``RECONNECT (4)``       -> ``alert2`` + return;
    - ``ORDER_CREATE_INVAILD (8)`` -> ``alert2`` + return;
    - ``DO_NOTHING (9)``      -> silent ``RETURN``;
    - ``SW_DISCONNECT (11)``  -> ``alert2`` + return;
    - anything else (including ``BUY_SUCCESS (3)``, ``DATA_INVALIDITY (5)``,
      ``PRICE_TYPE_ERROR (6)``, ``ERROR (7)``, ``TIME_EXPIRED (10)``) falls
      through to ``pkgPayMgr.payOrder(dMarketType, dId, dCost,
      strCustomOrderId, le_tbCfgData)`` -- the payment SDK path.
  So a refusal MUST be one of {1, 2, 4, 8, 9, 11}; the other values would be
  read as "go and pay".
* The wait overlay is ``market/pkgMarketWaitTipsViewMgr``.  Its ``show``
  (PROTO 0.1, source lines 13-17) passes ``dDelayTime = 6000`` to
  ``pkgCommmonWaitTipsMgr.show``, whose timer callback (PROTO 0.1.1, lines
  44-71) calls ``pkgUISystemViewMgr.destroyUI`` once that delay has elapsed --
  so the overlay dismisses itself and a refusal cannot leave it up forever.
* The two recovered senders both pass the same four values
  ``(dMarketType, dId, dCost, dSyncCount)``:
    - ``market/pkgMarketMgr`` PROTO 0.1.1 (source lines 108-122), the recharge
      tab's buy button: ``e_tbMarketType.RECHARGE``, the cell's ``id`` and
      ``price``, and ``pkgSyncCounterMgr.getSyncCounter(
      TB_SYNC_COUNTER_TYPE.MARKET_INSPECT_ORDER)``;
    - ``market/pkgUIMarketGiftBuyPanel`` PROTO 0.2.2 (source lines 200-237), the
      limited-gift panel, same shape with the gift's ``dKey``/``price``.
  Both branches are gated on ``pkgMarketMgr.le_test_pay`` / ``pkgPlatformMgr
  .isAndroid()``/``isIphone()``; otherwise they send ``C2S 412 simulatePay``,
  which this slice does NOT implement.

What this module does: it always answers one request with exactly one
`S2C 745` carrying a client-recognisable **refusal** result.  It creates no
order, charges nothing, grants nothing and stores nothing (no `role_store`
write, no schema change): the shipment order flow itself is out of scope.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Sequence


# ------------------------------------------------------------------ the contract

C2S_PROTOCOL_ID = 411
C2S_PROTOCOL_NAME = "inspectOrderValid"
C2S_FORMAT = "pppp"

S2C_PROTOCOL_ID = 745
S2C_PROTOCOL_NAME = "inspectOrderResult"
S2C_FORMAT = "ppppa"

#: `pkgScProtocolMgr.lua:966` -- the third `registerS2C` argument.
S2C_UNLOCK_NAMES: tuple[str, ...] = ("inspectOrderValid",)

#: Wire order of the request, from both recovered senders.
C2S_FIELD_NAMES: tuple[str, ...] = (
    "market_type",
    "recharge_id",
    "cost",
    "sync_count",
)
#: Wire order of the reply, from `pkgRechargeCenterMgr.inspectOrderResult`.
S2C_FIELD_NAMES: tuple[str, ...] = (
    "deal_result",
    "market_type",
    "recharge_id",
    "cost",
    "custom_order_id",
)

#: `zys_scAll/const/pkgMarketConst.max` -- `e_tbMarketType` (source lines 1-8).
MARKET_TYPE_NAMES: dict[int, str] = {
    0: "RECHARGE",
    1: "CARD",
    2: "FIRST_RECHARGE",
    3: "RED_PAPER",
    4: "LIMITED_GIFT",
    5: "MONTH_FREE_GIFT",
}

#: `zys_scAll/const/pkgMarketConst.max` -- `e_tbDealResult` (source lines 63-75).
DEAL_RESULT_NAMES: dict[int, str] = {
    1: "NUM_RESTRICT",
    2: "FEE_NOT_ENOUGH",
    3: "BUY_SUCCESS",
    4: "RECONNECT",
    5: "DATA_INVALIDITY",
    6: "PRICE_TYPE_ERROR",
    7: "ERROR",
    8: "ORDER_CREATE_INVAILD",
    9: "DO_NOTHING",
    10: "TIME_EXPIRED",
    11: "SW_DISCONNECT",
}

DEAL_RESULT_SUCCESS = 3

#: The `e_tbDealResult` values whose client branch shows `pkgUIAlertMgr.alert2`
#: and returns before `pkgPayMgr.payOrder` (source lines 23-41).
DEAL_RESULT_ALERT_CODES: tuple[int, ...] = (1, 2, 4, 8, 11)
#: The value whose client branch returns silently (source lines 36-38).
DEAL_RESULT_SILENT_CODE = 9
#: Every `e_tbDealResult` the client handles before `pkgPayMgr.payOrder`.
DEAL_RESULT_REFUSAL_CODES: tuple[int, ...] = (
    DEAL_RESULT_ALERT_CODES + (DEAL_RESULT_SILENT_CODE,)
)

#: Alert localisation keys, in the client's own comparison order.
DEAL_RESULT_ALERT_KEYS: dict[int, str] = {
    1: "market_deal_result_num_restrict",
    2: "market_deal_result_fee_not_enough",
    4: "market_reconnect",
    8: "market_order_create_invaild",
    11: "market_sw_disconnect",
}

#: The result one request is refused with.
#:
#: `NUM_RESTRICT (1)` is the smallest `e_tbDealResult` the client handles before
#: `pkgPayMgr.payOrder` **and** the one that raises a visible alert, so the
#: player gets an explicit failure instead of a silent unlock.  The choice is
#: `synthetic_compatibility`: the shipped client never records which result the
#: original server sent for an order it would not create, and this stub creates
#: no order at all.  Never send `BUY_SUCCESS (3)` or any of 5/6/7/10 -- the
#: client would call `pkgPayMgr.payOrder`.
REFUSAL_DEAL_RESULT = 1
REFUSAL_DEAL_RESULT_NAME = "NUM_RESTRICT"
REFUSAL_DEAL_RESULT_PROVENANCE = "synthetic_compatibility"
REFUSAL_DEAL_RESULT_ORIGIN = "revival_local_c2s411_refusal_result"
#: The alert the client shows for :data:`REFUSAL_DEAL_RESULT`.
REFUSAL_ALERT_KEY = DEAL_RESULT_ALERT_KEYS[REFUSAL_DEAL_RESULT]

#: `strCustomOrderId` when no order exists.  The refusal branch returns before
#: reading it, but the client formats it with `%s` first (source line 22), so
#: the value must be a string -- `nil` would raise inside `string.format`.
EMPTY_CUSTOM_ORDER_ID = ""

#: Positions filled with 0 because the request could not be echoed.
PADDING_ORIGIN = "synthetic_compatibility"

#: Closed refusal vocabulary, reported verbatim in the event log.
REFUSAL_FIELD_COUNT = "market_inspect_field_count"
REFUSAL_NON_NUMERIC = "market_inspect_non_numeric"
#: The reason every well-formed request is still refused: this stub has no
#: order flow, so no `C2S 411` can ever be answered with `BUY_SUCCESS`.
REFUSAL_NO_ORDER_FLOW = "market_inspect_no_order_flow"
REFUSAL_CODES = (
    REFUSAL_FIELD_COUNT,
    REFUSAL_NON_NUMERIC,
    REFUSAL_NO_ORDER_FLOW,
)


@dataclass(frozen=True, slots=True)
class MarketInspectOrderRequest:
    """One decoded `C2S 411`, reduced to the values the reply needs."""

    market_type: float
    recharge_id: float
    cost: float
    sync_count: float

    def echo_fields(self) -> tuple[object, ...]:
        """The three request values the reply carries back."""

        return (self.market_type, self.recharge_id, self.cost)


@dataclass(frozen=True, slots=True)
class MarketInspectOrderDecision:
    """One classification; ``request`` is ``None`` for a refused shape."""

    request: MarketInspectOrderRequest | None
    refusal: str

    @property
    def accepted(self) -> bool:
        return self.request is not None


def _as_number(value: object) -> float | None:
    """A finite number as float, or ``None`` (booleans are never numbers here).

    The registered format is ``pppp``, so every value the codec produces is
    already a float.  The stricter check only matters for direct callers.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def classify_request(fields: Sequence[object]) -> MarketInspectOrderDecision:
    """Decode one request's four values; refuse a shape that cannot be echoed.

    Unlike `C2S 224` there is no accepted-vs-refused *content*: this stub never
    creates an order, so every request is answered with a refusal result.  The
    classification only decides whether the reply can honestly echo the
    request's own market type / id / cost.
    """

    values = tuple(fields)
    if len(values) != len(C2S_FORMAT):
        return MarketInspectOrderDecision(None, REFUSAL_FIELD_COUNT)
    numbers = [_as_number(value) for value in values]
    if any(number is None for number in numbers):
        return MarketInspectOrderDecision(None, REFUSAL_NON_NUMERIC)
    market_type, recharge_id, cost, sync_count = numbers
    return MarketInspectOrderDecision(
        MarketInspectOrderRequest(
            market_type=market_type,
            recharge_id=recharge_id,
            cost=cost,
            sync_count=sync_count,
        ),
        "",
    )


def refusal_reply_fields(
    fields: Sequence[object],
) -> tuple[tuple[object, ...], list[int]]:
    """The five `S2C 745` values answering one request.

    Positions 2-4 echo the client's own market type / id / cost so the client's
    `%s` formatting and cfg lookup would line up if it ever read them (it does
    not on the refusal branch); position 1 is the refusal result and position 5
    the empty custom order id -- no order exists, and a fabricated one would be
    a lie.  Positions that cannot be echoed are filled with 0 and returned as
    padded indices so the caller can label the reply honestly.
    """

    decision = classify_request(fields)
    padded: list[int] = []
    if decision.accepted and decision.request is not None:
        echo: list[object] = list(decision.request.echo_fields())
    else:
        echo = [0, 0, 0]
        padded = [2, 3, 4]
    return (
        float(REFUSAL_DEAL_RESULT),
        echo[0],
        echo[1],
        echo[2],
        EMPTY_CUSTOM_ORDER_ID,
    ), padded


# --------------------------------------------------------------- re-derivation


class MarketInspectOrderDrift(AssertionError):
    """The shipped client no longer matches the frozen contract."""


def contract_canonical() -> str:
    market_types = ",".join(
        f"{name}:{value}" for value, name in sorted(MARKET_TYPE_NAMES.items())
    )
    results = ",".join(
        f"{name}:{value}" for value, name in sorted(DEAL_RESULT_NAMES.items())
    )
    alerts = ",".join(
        f"{code}:{DEAL_RESULT_ALERT_KEYS[code]}" for code in DEAL_RESULT_ALERT_CODES
    )
    return (
        f"c2s={C2S_PROTOCOL_ID}:{C2S_PROTOCOL_NAME}:{C2S_FORMAT}"
        f"|s2c={S2C_PROTOCOL_ID}:{S2C_PROTOCOL_NAME}:{S2C_FORMAT}"
        f"|unlock={','.join(S2C_UNLOCK_NAMES)}"
        f"|request={','.join(C2S_FIELD_NAMES)}"
        f"|reply={','.join(S2C_FIELD_NAMES)}"
        f"|marketTypes={market_types}"
        f"|dealResults={results}"
        f"|alertResults={','.join(str(code) for code in DEAL_RESULT_ALERT_CODES)}"
        f"|silentResult={DEAL_RESULT_SILENT_CODE}"
        f"|alerts={alerts}"
        f"|success={DEAL_RESULT_SUCCESS}"
        f"|refusal={REFUSAL_DEAL_RESULT}:{REFUSAL_DEAL_RESULT_NAME}"
        f"|refusalAlert={REFUSAL_ALERT_KEY}"
    )


#: sha256 of :func:`contract_canonical`; the analyzer prints the same digest and
#: a shipped-config drift changes it.
CONTRACT_DIGEST = (
    "9af4c6d2c334d29a04979ca7868a6399b13d59d8bf79fed4a881ed57f1187600"
)


def contract_digest() -> str:
    return hashlib.sha256(contract_canonical().encode("utf-8")).hexdigest()


def verify_contract_digest() -> None:
    actual = contract_digest()
    if actual != CONTRACT_DIGEST:  # pragma: no cover - drift guard
        raise MarketInspectOrderDrift(
            f"contract digest {actual} != frozen {CONTRACT_DIGEST}"
        )


def assert_contract() -> None:
    """Fail closed if any frozen value contradicts the client's behaviour."""

    if REFUSAL_DEAL_RESULT not in DEAL_RESULT_REFUSAL_CODES:
        raise MarketInspectOrderDrift(
            f"refusal result {REFUSAL_DEAL_RESULT} is not handled before payOrder"
        )
    if REFUSAL_DEAL_RESULT == DEAL_RESULT_SUCCESS:
        raise MarketInspectOrderDrift("a refusal must never be BUY_SUCCESS")
    if DEAL_RESULT_SUCCESS in DEAL_RESULT_REFUSAL_CODES:
        raise MarketInspectOrderDrift(
            "BUY_SUCCESS may never be listed as a refusal result"
        )
    if set(DEAL_RESULT_ALERT_KEYS) != set(DEAL_RESULT_ALERT_CODES):
        raise MarketInspectOrderDrift("alert keys and alert codes disagree")
    if len(C2S_FORMAT) != len(C2S_FIELD_NAMES):
        raise MarketInspectOrderDrift("request format/field names disagree")
    if len(S2C_FORMAT) != len(S2C_FIELD_NAMES):
        raise MarketInspectOrderDrift("reply format/field names disagree")
