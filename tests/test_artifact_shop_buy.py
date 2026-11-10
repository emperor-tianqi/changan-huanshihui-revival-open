"""TASK-0400 -- the shop buy charges, grants and always answers.

C2S 756 is a LOCK-SEND: an unanswered buy freezes the UI for 15 s and then shuts the socket, so
the handler must reply even when it refuses.  This test drives the real handler with a fake store.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import tcp_stub  # noqa: E402


class _Store:
    def __init__(self, balance):
        self.balance = balance
        self.weapons = []
        self.specials = []
        self._conn = self

    def execute(self, sql, params):
        class _Row:
            def __init__(self, amount):
                self._amount = amount

            def fetchone(self):
                return (self._amount,)

        return _Row(self.balance)

    def add_god_weapon(self, **kwargs):
        self.weapons.append(kwargs)
        return f"gw-{kwargs['cfg_id']}-001"

    def add_special_card(self, **kwargs):
        self.specials.append(kwargs)
        return 3

    def adjust_role_value(self, **kwargs):
        self.balance += kwargs["delta"]
        return self.balance


class _Logger:
    def __init__(self):
        self.records = []

    def emit(self, event, **fields):
        self.records.append((event, fields))


class _Handler:
    def __init__(self, balance):
        self.server = types.SimpleNamespace(role_store=_Store(balance), event_logger=_Logger())
        self.client_address = ("127.0.0.1", 1)
        self.sent = []

    def _send_s2c(self, connection_id, writer, name, fields, **kwargs):
        self.sent.append((name, tuple(fields)))
        return types.SimpleNamespace(spec=types.SimpleNamespace(name=name))


def _buy(handler, shelf, goods):
    session = {
        "role": types.SimpleNamespace(role_id=900000000001),
        "role_record": types.SimpleNamespace(account_key="acct"),
    }
    packet = types.SimpleNamespace(
        spec=types.SimpleNamespace(name="artifactDgnShopBuy"),
        fields=(shelf, goods, 1, 278), sequence=9)
    tcp_stub.RevivalTCPHandler._handle_artifact_shop_buy(handler, "c1", None, packet, session)
    return handler


class ShopBuyTests(unittest.TestCase):
    def test_a_weapon_shelf_charges_and_grants(self):
        h = _buy(_Handler(balance=5000), 101, 1)
        self.assertEqual(len(h.server.role_store.weapons), 1)
        self.assertEqual(h.server.role_store.weapons[0]["cfg_id"], 1101)
        self.assertEqual(h.server.role_store.balance, 5000 - 1800)
        names = [name for name, _ in h.sent]
        self.assertIn("returnAddSingleGodWeapon", names)
        self.assertEqual(names[-1], "artifactDgnShopBuy", "the lock is released last")

    def test_a_poor_player_is_refused_but_still_answered(self):
        h = _buy(_Handler(balance=10), 101, 1)
        self.assertEqual(h.server.role_store.weapons, [])
        self.assertEqual(h.server.role_store.balance, 10)
        self.assertEqual([name for name, _ in h.sent], ["artifactDgnShopBuy"])

    def test_a_special_card_shelf_grants_into_its_own_container(self):
        h = _buy(_Handler(balance=500), 106, 24)
        self.assertEqual(len(h.server.role_store.specials), 1)
        self.assertEqual(h.server.role_store.balance, 500 - 80)
        self.assertIn("returnSingleSpecialCard", [name for name, _ in h.sent])

    def test_an_unknown_shelf_still_releases_the_lock(self):
        h = _buy(_Handler(balance=5000), 999, 1)
        self.assertEqual([name for name, _ in h.sent], ["artifactDgnShopBuy"])
        self.assertEqual(h.server.role_store.weapons, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
