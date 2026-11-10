"""TASK-0266 -- C2S 429 goodsUse is a real, persisted effect.

The TASK-0265 contract (`reports/task_0265_bag_goods_use_contract.json`,
sections `request`, `response`, `minimal_slice`) is the authority for every
value in this file:

* C2S 429 goodsUse "NNN" is a LOCK-SEND: the client's own
  `registerS2C("NNN", "goodsUse", "goodsUse")` names S2C 820 as its only
  unlock, and a missing 820 makes the client shut its own socket down after
  LOCK_TIME = 15000 ms.
* S2C 820 goodsUse "NNN" therefore answers EVERY request exactly once:
  success (dCode 1) or refusal (dCode 2, the client's own
  power_max_can_not_use_item toast).
* The minimal faithful item is cfgGoods 376 花呗祈灵符 (use == 1, arg1 == 376,
  autoAddBag == 1), whose cfgGoodsReward 376 row grants (252 LOTTERY_TICKET, 1).
  Its item count is goodsUse6 (S2C 27 index 157 / reward type 376) and its
  effect is lotteryTicket (index 18 / reward type 252).

Success packet order: S2C 24 (item absolute) -> S2C 24 (ticket absolute) ->
S2C 81 (640) -> S2C 83 (252, 0, "", 1) -> S2C 820 (1, 1, remaining).  The
counts are absolute, never deltas, and they survive relogin through the
existing S2C 27 returnAllValue vector.  A fresh archive's S2C 27 vector stays
byte-identical: the two slots are patched only when their persisted key exists.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import get_profile
from revival_stub.eventlog import EventLogger
from revival_stub.protocol_catalog import KNOWN_C2S, LOCK_PARTNERS
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import (
    GOODS_USE_ORIGIN_LOCAL_C2S,
    GOODS_USE_VALUE_NAMES,
    ROLE_VALUE_GOODS_USE,
    ROLE_VALUE_LOTTERY_TICKET,
    RoleStore,
    RoleStoreError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import (
    GOODS_USE_GAIN_TYPE,
    GOODS_USE_ITEM_DTYPE,
    GOODS_USE_ITEM_REWARD_TYPE,
    GOODS_USE_ITEM_VALUE_INDEX,
    GOODS_USE_REFUSAL_DCODE,
    GOODS_USE_REFUSAL_ARG,
    GOODS_USE_REFUSAL_STORE,
    GOODS_USE_REFUSAL_UNSUPPORTED_ITEM,
    GOODS_USE_REWARD_TYPE,
    GOODS_USE_REWARD_VALUE_INDEX,
    GOODS_USE_SUCCESS_DCODE,
    GOODS_USE_UNKNOWN_IDS,
    RevivalTCPServer,
    login_identity_inputs,
)

FIXED_UNIX = 1_790_000_000.0
READ_TIMEOUT_S = 8.0
ROLE_NAME = "goods-use".encode("utf-8")

C2S_GOODS_USE = 429
S2C_GOODS_USE = 820
S2C_ONE_VALUE_N = 24
S2C_GAIN_START = 81
S2C_GAIN_INFO = 83
S2C_ALL_VALUE = 27
S2C_SCENE = 283


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class _LiveServerBase(unittest.TestCase):
    """One in-memory archive plus one live strict server per test."""

    def _store(self) -> RoleStore:
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        self.addCleanup(store.close)
        return store

    def start_server(self, store: RoleStore):
        events: list[dict] = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append),
            time_source=lambda: FIXED_UNIX,
            role_store=store,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)
        return server, events

    def _open(self, server):
        stream = socket.create_connection(
            ("127.0.0.1", server.server_address[1]), timeout=READ_TIMEOUT_S
        )
        stream.settimeout(READ_TIMEOUT_S)
        self.addCleanup(stream.close)
        return stream, WriterState(), ReaderState(), bytearray()

    @staticmethod
    def _send(stream, writer, protocol_id: int, fields) -> None:
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, writer).wire
        )

    def _read_until(self, stream, buffer, reader, protocol_id: int) -> list:
        packets: list = []
        deadline = time.monotonic() + READ_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                chunk = stream.recv(65536)
                if not chunk:
                    self.fail(
                        "server closed the socket before S2C "
                        f"{protocol_id}; ids="
                        f"{[item.spec.protocol_id for item in packets]}"
                    )
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == protocol_id:
                return packets
        self.fail(f"no S2C {protocol_id} answer arrived")

    def _drain(self, stream, buffer, reader, window: float = 0.6) -> list:
        packets: list = []
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                stream.settimeout(max(0.01, deadline - time.monotonic()))
                try:
                    chunk = stream.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
        stream.settimeout(READ_TIMEOUT_S)
        return packets

    def _login_and_create(self, server):
        stream, writer, reader, buffer = self._open(server)
        self._send(stream, writer, 0, DEFAULT_LOGIN_FIELDS)
        self._read_until(stream, buffer, reader, 5)  # noRole
        self._send(stream, writer, 2, (ROLE_NAME, 1))
        self._read_until(stream, buffer, reader, 9)  # base segment end
        self._send(stream, writer, 3, (1, False))
        login_packets = self._read_until(stream, buffer, reader, S2C_SCENE)
        self._drain(stream, buffer, reader)
        return stream, writer, reader, buffer, login_packets

    def _resume(self, server):
        stream, writer, reader, buffer = self._open(server)
        self._send(stream, writer, 0, DEFAULT_LOGIN_FIELDS)
        self._read_until(stream, buffer, reader, 9)  # base segment end
        self._send(stream, writer, 3, (1, False))
        packets = self._read_until(stream, buffer, reader, S2C_SCENE)
        self._drain(stream, buffer, reader)
        return stream, writer, reader, buffer, packets

    def _use(self, stream, writer, reader, buffer, fields=(GOODS_USE_ITEM_DTYPE, 0, 0)):
        self._send(stream, writer, C2S_GOODS_USE, fields)
        return self._read_until(stream, buffer, reader, S2C_GOODS_USE)

    @staticmethod
    def _account_key(store: RoleStore) -> str:
        return store.account_key(**login_identity_inputs(DEFAULT_LOGIN_FIELDS))

    @staticmethod
    def _seed(store: RoleStore, account_key: str, item: int, ticket: int) -> None:
        store.set_meta(
            store.goods_use_meta_key(account_key, ROLE_VALUE_GOODS_USE),
            str(item),
        )
        store.set_meta(
            store.goods_use_meta_key(account_key, ROLE_VALUE_LOTTERY_TICKET),
            str(ticket),
        )

    @staticmethod
    def _wait_events(events: list[dict], name: str, count: int, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [item for item in events if item.get("event") == name]
            if len(matches) >= count:
                return matches
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} #{count} was not emitted")

    @staticmethod
    def _assert_no_errors(events: list[dict]) -> None:
        offenders = [item for item in events if item.get("event") == "tcp_error"]
        assert not offenders, offenders


class GoodsUseStoreTests(unittest.TestCase):
    """The persisted ledger and its one transactional writer."""

    def setUp(self) -> None:
        self.store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        self.addCleanup(self.store.close)
        self.account_key = self.store.account_key(
            server_id=1.0,
            platform="android",
            channel_id="local-channel",
            channel_sub_id="0",
            sdk_identity="local-user",
        )
        self.record, _created = self.store.create_or_get(
            account_key=self.account_key,
            server_id=1.0,
            role_name="goods",
            career=1,
            create_time=int(FIXED_UNIX),
            compatibility_profile_id=get_profile().profile_id,
        )

    def _seed(self, item: int, ticket: int) -> None:
        self.store.set_meta(
            self.store.goods_use_meta_key(self.account_key, ROLE_VALUE_GOODS_USE),
            str(item),
        )
        self.store.set_meta(
            self.store.goods_use_meta_key(
                self.account_key, ROLE_VALUE_LOTTERY_TICKET
            ),
            str(ticket),
        )

    def test_use_goods_moves_both_counts_in_one_transaction(self) -> None:
        self._seed(3, 5)
        result = self.store.use_goods(
            record=self.record,
            item_value_name=ROLE_VALUE_GOODS_USE,
            reward_value_name=ROLE_VALUE_LOTTERY_TICKET,
        )
        self.assertEqual(result.role_id, self.record.role_id)
        self.assertEqual(result.item_before, 3)
        self.assertEqual(result.item_after, 2)
        self.assertEqual(result.used_count, 1)
        self.assertEqual(result.reward_before, 5)
        self.assertEqual(result.reward_after, 6)
        self.assertEqual(result.reward_amount, 1)
        self.assertEqual(result.provenance, GOODS_USE_ORIGIN_LOCAL_C2S)
        self.assertEqual(
            self.store.goods_use_counts(account_key=self.account_key),
            {ROLE_VALUE_GOODS_USE: 2, ROLE_VALUE_LOTTERY_TICKET: 6},
        )
        # The two names are NOT wallet values: the frozen wallet table is
        # untouched, and its writer still refuses them (below).
        self.assertEqual(
            self.store.role_wallet_values(account_key=self.account_key), {}
        )

    def test_use_goods_refuses_a_shortfall_and_writes_nothing(self) -> None:
        with self.assertRaises(RoleStoreError):
            self.store.use_goods(
                record=self.record,
                item_value_name=ROLE_VALUE_GOODS_USE,
                reward_value_name=ROLE_VALUE_LOTTERY_TICKET,
            )
        self.assertEqual(
            self.store.goods_use_counts(account_key=self.account_key), {}
        )
        self._seed(1, 5)
        with self.assertRaises(RoleStoreError):
            self.store.use_goods(
                record=self.record,
                item_value_name=ROLE_VALUE_GOODS_USE,
                reward_value_name=ROLE_VALUE_LOTTERY_TICKET,
                count=2,
            )
        self.assertEqual(
            self.store.goods_use_counts(account_key=self.account_key),
            {ROLE_VALUE_GOODS_USE: 1, ROLE_VALUE_LOTTERY_TICKET: 5},
        )

    def test_use_goods_rejects_zero_bad_names_and_bad_amounts(self) -> None:
        self._seed(2, 5)
        bad_calls = (
            dict(count=0),
            dict(count=-1),
            dict(count=True),
            dict(reward_amount=0),
            dict(reward_amount=-1),
            dict(reward_value_name=ROLE_VALUE_GOODS_USE),
            dict(item_value_name="not-a-role-value"),
            dict(provenance=""),
        )
        for overrides in bad_calls:
            with self.subTest(overrides=overrides):
                arguments = {
                    "record": self.record,
                    "item_value_name": ROLE_VALUE_GOODS_USE,
                    "reward_value_name": ROLE_VALUE_LOTTERY_TICKET,
                }
                arguments.update(overrides)
                with self.assertRaises(RoleStoreError):
                    self.store.use_goods(**arguments)
                self.assertEqual(
                    self.store.goods_use_counts(account_key=self.account_key),
                    {ROLE_VALUE_GOODS_USE: 2, ROLE_VALUE_LOTTERY_TICKET: 5},
                )

    def test_unreadable_or_negative_ledger_values_are_refused(self) -> None:
        key = self.store.goods_use_meta_key(
            self.account_key, ROLE_VALUE_GOODS_USE
        )
        ticket_key = self.store.goods_use_meta_key(
            self.account_key, ROLE_VALUE_LOTTERY_TICKET
        )
        for stored in ("two", "-1"):
            with self.subTest(stored=stored):
                self.store.set_meta(key, stored)
                with self.assertRaises(RoleStoreError):
                    self.store.goods_use_counts(account_key=self.account_key)
                with self.assertRaises(RoleStoreError):
                    self.store.use_goods(
                        record=self.record,
                        item_value_name=ROLE_VALUE_GOODS_USE,
                        reward_value_name=ROLE_VALUE_LOTTERY_TICKET,
                    )
                rows = dict(
                    self.store._conn.execute(
                        "SELECT key, value FROM meta ORDER BY key"
                    ).fetchall()
                )
                self.assertEqual(rows[key], stored)
                self.assertNotIn(
                    ticket_key,
                    rows,
                    "a refused read must not create or change the reward key",
                )

    def test_the_wallet_writers_still_refuse_the_goods_names(self) -> None:
        with self.assertRaises(RoleStoreError):
            self.store.adjust_role_wallet(
                record=self.record,
                value_name=ROLE_VALUE_GOODS_USE,
                delta=1,
                provenance="probe",
            )
        with self.assertRaises(RoleStoreError):
            self.store.adjust_role_wallet(
                record=self.record,
                value_name=ROLE_VALUE_LOTTERY_TICKET,
                delta=1,
                provenance="probe",
            )
        with self.assertRaises(RoleStoreError):
            self.store.provision_role_wallet_once(
                record=self.record,
                values={ROLE_VALUE_GOODS_USE: 1},
                provenance="probe",
            )
        self.assertEqual(
            self.store.role_wallet_values(account_key=self.account_key), {}
        )

    def test_counts_survive_a_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "roles"
            store = RoleStore.at_directory(data_dir, time_source=lambda: FIXED_UNIX)
            try:
                account_key = store.account_key(
                    server_id=1.0,
                    platform="android",
                    channel_id="local-channel",
                    channel_sub_id="0",
                    sdk_identity="local-user",
                )
                record, _ = store.create_or_get(
                    account_key=account_key,
                    server_id=1.0,
                    role_name="goods",
                    career=1,
                    create_time=int(FIXED_UNIX),
                    compatibility_profile_id=get_profile().profile_id,
                )
                store.set_meta(
                    store.goods_use_meta_key(account_key, ROLE_VALUE_GOODS_USE),
                    "3",
                )
                store.set_meta(
                    store.goods_use_meta_key(
                        account_key, ROLE_VALUE_LOTTERY_TICKET
                    ),
                    "5",
                )
                store.use_goods(
                    record=record,
                    item_value_name=ROLE_VALUE_GOODS_USE,
                    reward_value_name=ROLE_VALUE_LOTTERY_TICKET,
                )
            finally:
                store.close()
            reopened = RoleStore.at_directory(
                data_dir, time_source=lambda: FIXED_UNIX
            )
            try:
                self.assertEqual(
                    reopened.goods_use_counts(account_key=account_key),
                    {ROLE_VALUE_GOODS_USE: 2, ROLE_VALUE_LOTTERY_TICKET: 6},
                )
            finally:
                reopened.close()

    def test_meta_key_is_scoped_to_one_value_name(self) -> None:
        item_key = self.store.goods_use_meta_key(
            self.account_key, ROLE_VALUE_GOODS_USE
        )
        ticket_key = self.store.goods_use_meta_key(
            self.account_key, ROLE_VALUE_LOTTERY_TICKET
        )
        self.assertNotEqual(item_key, ticket_key)
        self.assertTrue(item_key.endswith(ROLE_VALUE_GOODS_USE))
        self.assertTrue(ticket_key.endswith(ROLE_VALUE_LOTTERY_TICKET))
        self.assertEqual(set(GOODS_USE_VALUE_NAMES), {
            ROLE_VALUE_GOODS_USE,
            ROLE_VALUE_LOTTERY_TICKET,
        })
        with self.assertRaises(RoleStoreError):
            self.store.goods_use_meta_key(self.account_key, "not-a-value")


class GoodsUseRegistryTests(unittest.TestCase):
    """The two recovered rows, and the lock pair the contract demands."""

    def test_c2s_429_is_the_client_row(self) -> None:
        spec = C2S_REGISTRY.by_id(C2S_GOODS_USE)
        self.assertEqual((spec.name, spec.fmt), ("goodsUse", "NNN"))
        self.assertEqual(LOCK_PARTNERS.get("goodsUse"), ("goodsUse",))

    def test_s2c_820_is_the_only_unlock(self) -> None:
        spec = S2C_REGISTRY.by_id(S2C_GOODS_USE)
        self.assertEqual((spec.name, spec.fmt), ("goodsUse", "NNN"))
        self.assertIs(S2C_REGISTRY.by_name("goodsUse"), spec)
        self.assertEqual(LOCK_PARTNERS.get("goodsUse"), ("goodsUse",))

    def test_the_catalog_also_carries_the_request_row(self) -> None:
        self.assertIn(
            (C2S_GOODS_USE, "goodsUse", "NNN"), set(KNOWN_C2S)
        )


class GoodsUseSuccessTests(_LiveServerBase):
    """The applied path: five packets, absolute counts, persisted state."""

    def test_success_sends_the_five_packets_in_order_with_absolute_counts(self) -> None:
        store = self._store()
        server, events = self.start_server(store)
        stream, writer, reader, buffer, _ = self._login_and_create(server)
        account_key = self._account_key(store)
        self._seed(store, account_key, 3, 5)

        packets = self._use(stream, writer, reader, buffer)
        self.assertEqual(
            [packet.spec.protocol_id for packet in packets],
            [S2C_ONE_VALUE_N, S2C_ONE_VALUE_N, S2C_GAIN_START, S2C_GAIN_INFO, S2C_GOODS_USE],
        )
        self.assertEqual(len([p for p in packets if p.spec.protocol_id == S2C_GOODS_USE]), 1)

        item_count = packets[0]
        self.assertEqual(item_count.spec.fmt, "NN")
        self.assertEqual(
            tuple(item_count.fields),
            (GOODS_USE_ITEM_REWARD_TYPE, 2),
        )
        ticket_count = packets[1]
        self.assertEqual(ticket_count.spec.fmt, "NN")
        self.assertEqual(tuple(ticket_count.fields), (GOODS_USE_REWARD_TYPE, 6))
        gain_start = packets[2]
        self.assertEqual(gain_start.spec.fmt, "N")
        self.assertEqual(tuple(gain_start.fields), (GOODS_USE_GAIN_TYPE,))
        self.assertEqual(GOODS_USE_GAIN_TYPE, 640)
        gain_info = packets[3]
        self.assertEqual(gain_info.spec.fmt, "NNaN")
        self.assertEqual(gain_info.fields[0], GOODS_USE_REWARD_TYPE)
        self.assertEqual(gain_info.fields[1], 0)
        self.assertEqual(_text(gain_info.fields[2]), "")
        self.assertEqual(gain_info.fields[3], 1)
        unlock = packets[4]
        self.assertEqual(unlock.spec.fmt, "NNN")
        self.assertEqual(
            tuple(unlock.fields),
            (GOODS_USE_SUCCESS_DCODE, 1, 2),
        )
        self.assertEqual(GOODS_USE_SUCCESS_DCODE, 1)
        self.assertEqual(
            store.goods_use_counts(account_key=account_key),
            {ROLE_VALUE_GOODS_USE: 2, ROLE_VALUE_LOTTERY_TICKET: 6},
        )
        self.assertEqual(
            (GOODS_USE_ITEM_VALUE_INDEX, GOODS_USE_REWARD_VALUE_INDEX), (157, 18)
        )

        request = self._wait_events(events, "tcp_goods_use_received", 1)[0]
        self.assertEqual(request["d_type"], GOODS_USE_ITEM_DTYPE)
        self.assertEqual(request["item_current_count"], 3)
        self.assertEqual(request["unknown_ids"], list(GOODS_USE_UNKNOWN_IDS))
        self.assertEqual(
            request["goods_use_provenance"], GOODS_USE_ORIGIN_LOCAL_C2S
        )
        self.assertTrue(request["state_change_planned"])
        self.assertFalse(request["state_changed"])
        self.assertEqual(request["lock_timeout_ms"], 15000)

        applied = self._wait_events(events, "tcp_goods_use_applied", 1)[0]
        self.assertEqual(applied["item_before"], 3)
        self.assertEqual(applied["item_after"], 2)
        self.assertEqual(applied["reward_before"], 5)
        self.assertEqual(applied["reward_after"], 6)
        self.assertEqual(
            applied["packet_protocol_ids"],
            [S2C_ONE_VALUE_N, S2C_ONE_VALUE_N, S2C_GAIN_START, S2C_GAIN_INFO, S2C_GOODS_USE],
        )
        self.assertEqual(
            applied["packet_order"],
            [
                "returnOneValue_N",
                "returnOneValue_N",
                "returnGainInfoStart",
                "returnGainInfo",
                "goodsUse",
            ],
        )
        self.assertEqual(applied["unknown_ids"], list(GOODS_USE_UNKNOWN_IDS))
        self.assertTrue(applied["state_changed"])
        self.assertTrue(applied["role_store_written"])
        self.assertTrue(applied["one_reply_per_request"])
        self.assertTrue(applied["absolute_values"])
        self.assertEqual(applied["reply_count"], 1)
        self.assertEqual(
            applied["goods_use_provenance"], GOODS_USE_ORIGIN_LOCAL_C2S
        )

        # One event per packet (the transport already emits tcp_packet_sent),
        # each carrying the same provenance, unknown ids and state change.
        packet_events = [
            event
            for event in events
            if event.get("event") == "tcp_packet_sent"
            and str(event.get("classification", "")).startswith("goods_use")
        ]
        self.assertEqual(
            [event["protocol_id"] for event in packet_events],
            [S2C_ONE_VALUE_N, S2C_ONE_VALUE_N, S2C_GAIN_START, S2C_GAIN_INFO, S2C_GOODS_USE],
        )
        for event in packet_events:
            self.assertEqual(event["unknown_ids"], list(GOODS_USE_UNKNOWN_IDS))
            self.assertEqual(
                event["goods_use_provenance"], GOODS_USE_ORIGIN_LOCAL_C2S
            )
            self.assertTrue(event["state_changed"])
        self._assert_no_errors(events)

    def test_a_second_use_sends_the_new_absolute_counts_not_deltas(self) -> None:
        store = self._store()
        server, events = self.start_server(store)
        stream, writer, reader, buffer, _ = self._login_and_create(server)
        account_key = self._account_key(store)
        self._seed(store, account_key, 3, 5)

        first = self._use(stream, writer, reader, buffer)
        self.assertEqual(tuple(first[0].fields), (GOODS_USE_ITEM_REWARD_TYPE, 2))
        self.assertEqual(tuple(first[1].fields), (GOODS_USE_REWARD_TYPE, 6))
        self.assertEqual(tuple(first[4].fields), (1, 1, 2))

        second = self._use(stream, writer, reader, buffer)
        self.assertEqual(
            tuple(second[0].fields),
            (GOODS_USE_ITEM_REWARD_TYPE, 1),
            "S2C 24 must carry the absolute count, never a -1 delta",
        )
        self.assertEqual(tuple(second[1].fields), (GOODS_USE_REWARD_TYPE, 7))
        self.assertEqual(tuple(second[4].fields), (1, 1, 1))
        self.assertEqual(
            store.goods_use_counts(account_key=account_key),
            {ROLE_VALUE_GOODS_USE: 1, ROLE_VALUE_LOTTERY_TICKET: 7},
        )
        self._assert_no_errors(events)


class GoodsUseRefusalTests(_LiveServerBase):
    """Every refusal is one 820, writes nothing, and keeps the socket usable."""

    def test_refusals_send_one_820_and_write_nothing(self) -> None:
        store = self._store()
        server, events = self.start_server(store)
        stream, writer, reader, buffer, _ = self._login_and_create(server)
        account_key = self._account_key(store)
        self._seed(store, account_key, 2, 5)

        unsupported = self._use(
            stream, writer, reader, buffer, (377, 0, 0)
        )
        self.assertEqual(
            [packet.spec.protocol_id for packet in unsupported], [S2C_GOODS_USE]
        )
        self.assertEqual(
            tuple(unsupported[0].fields),
            (GOODS_USE_REFUSAL_DCODE, 1, 2),
        )
        self.assertEqual(GOODS_USE_REFUSAL_DCODE, 2)

        bad_arg = self._use(stream, writer, reader, buffer, (GOODS_USE_ITEM_DTYPE, 0, 1))
        self.assertEqual(
            [packet.spec.protocol_id for packet in bad_arg], [S2C_GOODS_USE]
        )
        self.assertEqual(tuple(bad_arg[0].fields), (2, 1, 2))

        # Zero count: drop the item key (the ticket row must stay untouched).
        store.delete_meta(
            store.goods_use_meta_key(account_key, ROLE_VALUE_GOODS_USE)
        )
        zero = self._use(stream, writer, reader, buffer)
        self.assertEqual([packet.spec.protocol_id for packet in zero], [S2C_GOODS_USE])
        self.assertEqual(tuple(zero[0].fields), (2, 1, 0))
        self.assertEqual(
            store.goods_use_counts(account_key=account_key),
            {ROLE_VALUE_LOTTERY_TICKET: 5},
        )

        refused = self._wait_events(events, "tcp_goods_use_refused", 3)
        self.assertEqual(
            [item["reason"] for item in refused],
            [
                GOODS_USE_REFUSAL_UNSUPPORTED_ITEM,
                GOODS_USE_REFUSAL_ARG,
                GOODS_USE_REFUSAL_STORE,
            ],
        )
        for item in refused:
            self.assertFalse(item["state_changed"])
            self.assertFalse(item["role_store_written"])
            self.assertTrue(item["connection_kept_alive"])
            self.assertEqual(item["reply_count"], 1)
            self.assertEqual(item["unknown_ids"], list(GOODS_USE_UNKNOWN_IDS))
            self.assertEqual(item["lock_timeout_ms"], 15000)
            self.assertEqual(item["response_protocol_id"], S2C_GOODS_USE)
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event.get("event") == "tcp_packet_sent"
                    and event.get("classification") == "goods_use_refused"
                ]
            ),
            3,
            "one 820 packet event per refusal, never two",
        )

        # The socket is still usable after every refusal.
        self._seed(store, account_key, 1, 5)
        applied = self._use(stream, writer, reader, buffer)
        self.assertEqual(
            [packet.spec.protocol_id for packet in applied],
            [S2C_ONE_VALUE_N, S2C_ONE_VALUE_N, S2C_GAIN_START, S2C_GAIN_INFO, S2C_GOODS_USE],
        )
        self.assertEqual(tuple(applied[0].fields), (GOODS_USE_ITEM_REWARD_TYPE, 0))
        self.assertEqual(tuple(applied[1].fields), (GOODS_USE_REWARD_TYPE, 6))
        self.assertEqual(tuple(applied[4].fields), (1, 1, 0))
        self._assert_no_errors(events)


class GoodsUseReloginTests(_LiveServerBase):
    """Both counts are in S2C 27 after relogin; a fresh archive is unchanged."""

    def test_relogin_replays_both_counts_in_the_s2c_27_vector(self) -> None:
        store = self._store()
        server, events = self.start_server(store)
        stream, writer, reader, buffer, _ = self._login_and_create(server)
        account_key = self._account_key(store)
        self._seed(store, account_key, 3, 5)
        self._use(stream, writer, reader, buffer)
        stream.close()

        _stream, _writer, _reader, _buffer, packets = self._resume(server)
        snapshots = [p for p in packets if p.spec.protocol_id == S2C_ALL_VALUE]
        self.assertEqual(len(snapshots), 1)
        fields = snapshots[0].fields
        expected = list(get_profile().role_value_vector())
        expected[GOODS_USE_ITEM_VALUE_INDEX] = 2
        expected[GOODS_USE_REWARD_VALUE_INDEX] = 6
        self.assertEqual(
            list(fields),
            expected,
            "only the two goods-use slots may differ from the frozen vector",
        )
        self.assertEqual(fields[GOODS_USE_ITEM_VALUE_INDEX], 2)
        self.assertEqual(fields[GOODS_USE_REWARD_VALUE_INDEX], 6)
        self.assertEqual(
            store.goods_use_counts(account_key=account_key),
            {ROLE_VALUE_GOODS_USE: 2, ROLE_VALUE_LOTTERY_TICKET: 6},
        )
        overlay = self._wait_events(
            events, "tcp_goods_use_balance_overlay_applied", 1
        )[0]
        self.assertEqual(
            [item["value_name"] for item in overlay["values"]],
            [ROLE_VALUE_GOODS_USE, ROLE_VALUE_LOTTERY_TICKET],
        )
        self.assertEqual([item["amount"] for item in overlay["values"]], [2, 6])

    def test_a_fresh_archive_login_vector_is_byte_identical(self) -> None:
        store = self._store()
        server, events = self.start_server(store)
        _stream, _writer, _reader, _buffer, packets = self._login_and_create(server)
        snapshots = [p for p in packets if p.spec.protocol_id == S2C_ALL_VALUE]
        self.assertEqual(len(snapshots), 1)
        fields = snapshots[0].fields
        self.assertEqual(
            tuple(fields),
            tuple(get_profile().role_value_vector()),
            "with no persisted goods-use key the frozen all-zero vector is sent",
        )
        self.assertEqual(fields[GOODS_USE_ITEM_VALUE_INDEX], 0)
        self.assertEqual(fields[GOODS_USE_REWARD_VALUE_INDEX], 0)
        account_key = self._account_key(store)
        self.assertEqual(store.goods_use_counts(account_key=account_key), {})
        self.assertEqual(
            [
                event
                for event in events
                if event.get("event") == "tcp_goods_use_balance_overlay_applied"
            ],
            [],
        )
        self._assert_no_errors(events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
