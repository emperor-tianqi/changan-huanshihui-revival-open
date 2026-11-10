"""Inbound protocol policy for the compatibility stub.

Two modes, chosen explicitly on the command line and never inferred from the
role mode:

``strict``
    The original D-08 discipline.  An id the implemented registry does not know
    is a malformed frame: ``tcp_error`` + ``tcp_disconnected``.  Device
    Acceptance, Golden Journey, regression and protocol forensic runs keep
    this default.

``free_play_tolerant``
    Only the Owner's Dev Server / manual free play.  The read-only client
    catalog (``protocol_catalog``) supplies the id/name/format of protocols the
    client itself sends but the stub has not implemented yet.  Such a packet is
    decoded, recorded and answered only when a safe reply is proven:

    * no response by default -- the socket stays open, no state changes;
    * a safe no-op response only for an id listed in ``FREE_PLAY_SAFE_REPLIES``;
    * a protocol the catalog cannot decode remains strict.  A false reply would
      be worse than a close, and an unknown packet boundary cannot be skipped
      safely.

C2S 10 ``reconnect`` is no longer routed through this policy: it is a real
implemented handler now (S2C 15 ``reconnectSuccess`` + stored-role rehydration),
so the client can resume after backgrounding instead of being refused.

The catalog and this policy are additive data/control only.  They do not
implement a business handler and they never write a role, item, currency or
progress value.
"""

from __future__ import annotations

from dataclasses import dataclass

from .registry import ProtocolSpec


PROTOCOL_POLICY_STRICT = "strict"
PROTOCOL_POLICY_FREE_PLAY_TOLERANT = "free_play_tolerant"
PROTOCOL_POLICIES = (PROTOCOL_POLICY_STRICT, PROTOCOL_POLICY_FREE_PLAY_TOLERANT)

#: Handshakes are never *tolerated* (i.e. never answered by the catalog-only
#: fallback).  C2S 10 reconnect used to live here; it is now an implemented
#: handler (see tcp_stub._handle_reconnect), so this set is empty until another
#: session handshake is found in the client catalog.
FREE_PLAY_STRICT_HANDSHAKES = frozenset()


@dataclass(frozen=True, slots=True)
class FreePlaySafeReply:
    """A response proven safe for one known-but-unimplemented request."""

    response_spec: ProtocolSpec
    response_fields: tuple
    rationale: str


#: C2S 647 `sendToGetLimitShopOpenTime` ('N') is a lock-send: the client's own
#: `registerC2S("N", "sendToGetLimitShopOpenTime",
#: "returnLimitShopOpenTimeUpdated")` makes S2C 1295 the only unlock.  S2C 1295
#: ('N') reaches `pkgLimitShopControl.onGetOpenTimeReturn` (pkgLimitShopNcMgr
#: proto 0.14), which compares the value with `const_ShopType5/6/7`; those
#: constants are 5/6/7 in `zys_scAll/const/pkgLimitShopConst.max`.  A value of 0
#: is not a valid shop type: the handler takes its not-open / no-op path.  The
#: reply is labelled synthetic in the event and changes no server state.
FREE_PLAY_SAFE_REPLIES = {
    647: FreePlaySafeReply(
        response_spec=ProtocolSpec(1295, "returnLimitShopOpenTimeUpdated", "N"),
        response_fields=(0,),
        rationale=(
            "client lock partner S2C 1295; onGetOpenTimeReturn takes its "
            "not-open path for a value outside const_ShopType1..8 (0)"
        ),
    ),
}
