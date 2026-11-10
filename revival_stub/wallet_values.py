"""The persisted wallet values that ride in the client's S2C 27 vector.

`reports/role_value_wire_mapping.csv` (recovered by the v1 compatibility work)
maps every S2C 27 wire slot to its reward-type symbol.  The card-growth economy
writes three of them through ``role_store.role_wallet``:

===========  ==============  =============  ===================
value name   wire index      reward type    client reader
===========  ==============  =============  ===================
money        1 (0-based 0)   7  MONEY       ``pkgPlayerInfoMgr.getMoney``
yinpo        7 (0-based 6)   28 YINPO       ``pkgCgValueMgr.getValue``
jinpo        8 (0-based 7)   29 JINPO       ``pkgCgValueMgr.getValue``
===========  ==============  =============  ===================

The in-place deltas use the client's own ``returnOneValue_p`` (S2C 23, float)
and ``returnOneValue_N`` (S2C 24, uint) packets, both registered in
``pkgScProtocolMgr.lua:278-281``.  Only the three names above are persisted;
every other S2C 27 slot keeps the compatibility profile's frozen zero.
"""

from __future__ import annotations

#: ``value name -> (0-based wire index, reward type id, packet)``.
WALLET_WIRE_VALUES: dict[str, tuple[int, int, str]] = {
    "money": (0, 7, "returnOneValue_p"),
    "yinpo": (6, 28, "returnOneValue_N"),
    "jinpo": (7, 29, "returnOneValue_N"),
}


def coerce_wallet_value(name: str, amount: float):
    """The exact Python type the S2C 27 field validator accepts.

    ``money`` rides ``returnOneValue_p`` (a Lua number) and stays a float; the
    two po values ride ``returnOneValue_N`` (uint32) and must be ints.
    """

    _index, _reward_type, packet = WALLET_WIRE_VALUES[name]
    if packet == "returnOneValue_N":
        return int(amount)
    return float(amount)


def apply_wallet(values: dict[str, float], vector: list) -> list:
    """Overwrite the three persisted slots in one S2C 27 value vector.

    ``vector`` is the compatibility profile's ``role_value_vector`` (a list in
    wire order).  A name absent from ``values`` keeps the frozen zero.
    """

    for name, (index, _reward_type, _packet) in WALLET_WIRE_VALUES.items():
        if name not in values:
            continue
        amount = float(values[name])
        if amount < 0:
            raise ValueError(f"wallet value {name} is negative: {amount}")
        vector[index] = coerce_wallet_value(name, amount)
    return vector
