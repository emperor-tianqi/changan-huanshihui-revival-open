"""City shop (C2S 57/58/59 -> S2C 487/488/489/490) end-to-end tests (TASK-0444)."""

from __future__ import annotations

import unittest

from revival_stub.shop_catalog import SHOP_CONFIG, SHOP_GOODS

from tests.test_card_advance import _Server


class CityShopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server()
        self.addCleanup(self.server.close)
        self.key, self.record = self.server.seed_role()

    def open_shop(self, stream, writer, reader, buffer, shop_type=2):
        self.server.send(stream, writer, 57, (shop_type,))
        return self.server.read_until(stream, buffer, reader, 489)

    def test_open_shop_sends_start_goods_end(self) -> None:
        stream, writer, reader, buffer = self.server.connect()
        try:
            packets = self.open_shop(stream, writer, reader, buffer, 2)
        finally:
            stream.close()
        ids = [packet.spec.protocol_id for packet in packets]
        self.assertIn(487, ids)
        self.assertIn(488, ids)
        self.assertIn(489, ids)
        goods = [
            packet
            for packet in packets
            if packet.spec.protocol_id == 488 and packet.fields[0] == 2
        ]
        self.assertEqual(len(goods), 9)
        ends = [
            packet
            for packet in packets
            if packet.spec.protocol_id == 489 and packet.fields[0] == 2
        ]
        self.assertEqual(len(ends), 1)

    def test_buy_piece_slot_deducts_money_and_adds_piece(self) -> None:
        self.server.seed_wallet(self.record, money=100000)
        before_pieces = sum(
            self.server.store.card_piece_counts(account_key=self.key).values()
        )
        stream, writer, reader, buffer = self.server.connect()
        try:
            packets = self.open_shop(stream, writer, reader, buffer, 2)
            goods_rows = {
                index + 1: packet.fields[1]
                for index, packet in enumerate(
                    [
                        p
                        for p in packets
                        if p.spec.protocol_id == 488 and p.fields[0] == 2
                    ]
                )
            }
            self.server.send(stream, writer, 59, (2, 3, 0))
            result = self.server.read_until(stream, buffer, reader, 490)
        finally:
            stream.close()
        self.assertEqual(result[-1].fields, (2, 3, 1))
        after_pieces = sum(
            self.server.store.card_piece_counts(account_key=self.key).values()
        )
        self.assertGreater(after_pieces, before_pieces)
        self.assertLess(self.server.wallet(self.key)["money"], 100000)

    def test_refresh_charges_the_shipped_money_cost(self) -> None:
        self.server.seed_wallet(self.record, money=1000)
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.open_shop(stream, writer, reader, buffer, 2)
            self.server.send(stream, writer, 58, (2, 0))
            packets = self.server.read_until(stream, buffer, reader, 491)
        finally:
            stream.close()
        self.assertEqual(packets[-1].fields, (2, 5))
        self.assertEqual(self.server.wallet(self.key)["money"], 700)

    def test_refresh_without_money_is_refused(self) -> None:
        self.server.seed_wallet(self.record, money=100)
        stream, writer, reader, buffer = self.server.connect()
        try:
            self.open_shop(stream, writer, reader, buffer, 2)
            self.server.send(stream, writer, 58, (2, 0))
            packets = self.server.read_until(stream, buffer, reader, 491)
        finally:
            stream.close()
        self.assertEqual(packets[-1].fields, (2, 2))
        self.assertEqual(self.server.wallet(self.key)["money"], 100)

    def test_buy_card_slot_creates_card_instance(self) -> None:
        self.server.seed_wallet(self.record, money=100000)
        stream, writer, reader, buffer = self.server.connect()
        try:
            for _ in range(20):
                packets = self.open_shop(stream, writer, reader, buffer, 1)
                rows = [
                    packet
                    for packet in packets
                    if packet.spec.protocol_id == 488 and packet.fields[0] == 1
                ]
                if rows:
                    goods_id = rows[0].fields[1]
                    row = SHOP_GOODS[SHOP_CONFIG[1]["grids"][0]].get(goods_id)
                    if row is not None and row[0] == 3:
                        break
                self.server.send(stream, writer, 58, (1, 0))
                self.server.read_until(stream, buffer, reader, 491)
            self.server.send(stream, writer, 59, (1, 1, 0))
            result = self.server.read_until(stream, buffer, reader, 490)
        finally:
            stream.close()
        self.assertEqual(result[-1].fields, (1, 1, 1))
        cards = self.server.store.card_instances(account_key=self.key)
        self.assertTrue(any(card.origin == "shop_purchase" for card in cards))


if __name__ == "__main__":
    unittest.main()
