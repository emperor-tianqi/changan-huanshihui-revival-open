"""TASK-0233 -- the dev-play resources overlay on ``dev-resource-main``.

The overlay exists so the client's own 祈灵 draw and 强化 wallet can run without
editing the permanent ``qa-changan-main`` save:

* ``S2C 10 returnRoleInfo`` announces level 29 (``functionOpen[15].openLevel``);
* ``S2C 27 returnAllValue`` carries 30/30 tickets in memory and the persisted
  money/银魄/金魄 rows (100000/50000/50000) from this archive's ``role_wallet``;
* the wallet rows are provisioned **once** (missing rows only), so the client's
  own C2S 18-21 sink spends them and a re-login keeps the spent balance;
* C2S 274/33 reuse the existing deterministic card fixture and write
  ``card_instances`` only in this archive.

Everything is profile-scoped and opt-in.  The tests prove the positive path,
every "one condition missing" negative path **byte for byte** against the
frozen v18 login stream, the CLI's profile gate, the one-time wallet policy, the
advance/relogin loop, the deterministic (never probability) draw, and the fact
that the qa-only grants stay refused on this profile.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from revival_stub import dev_play_resources as dpr
from revival_stub.app import default_data_dir
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.eventlog import EventLogger
from revival_stub.owner_draw_test import (
    OwnerDrawTestGrant,
    OwnerGrowthTestGrant,
)
from revival_stub.play_profile import (
    PROFILE_MODE_INIT,
    PROFILE_MODE_RESUME,
    PROFILE_ROOT_ENV,
    PlayProfileRefusedError,
    select_play_profile,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import (
    CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
    SCHEMA_VERSION,
    CardInstanceWrite,
    RoleStore,
    RoleStoreError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


#: One fixed clock for both the store and the server, so two fresh archives
#: produce the same role row (and therefore the same wire bytes).
FIXED_UNIX = 1_700_000_000.0

#: How long a login or one request may take in this suite.
READ_TIMEOUT_S = 8.0

ROLE_INFO_ID = 10
ROLE_VALUE_ID = 27
CARD_INFO_ID = 30
CARD_START_ID = 29
CARD_END_ID = 31
SCENE_INFO_ID = 283

#: A 3-star normal card with the shipped four advance rows; target level 1
#: costs 200 money + 50 银魄 (``card_growth.advance_cost(3, 3.0, 1)``).
ADVANCE_CARD_CFG_ID = 3312
ADVANCE_COST_MONEY = 200.0
ADVANCE_COST_YINPO = 50

#: Client request ids used by the draw and advance handlers.
DRAW_TICKET_ID = 274
DRAW_DIAMOND_ID = 33
ADVANCE_START_ID = 18
ADVANCE_END_ID = 21
ADVANCE_COUNTER_ID = 294


class _EnvRoots(unittest.TestCase):
    """Base: throw-away profile + default-archive roots for this test only."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved_env = {
            key: os.environ.get(key)
            for key in (PROFILE_ROOT_ENV, "REVIVAL_DATA_DIR")
        }
        os.environ[PROFILE_ROOT_ENV] = str(Path(self._tmp.name) / "profiles")
        os.environ["REVIVAL_DATA_DIR"] = str(Path(self._tmp.name) / "archive")
        self.addCleanup(self._restore_env)
        self._clock = lambda: FIXED_UNIX

    def _restore_env(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ------------------------------------------------------------- profile ops

    def create_profile(self, name: str) -> None:
        profile = select_play_profile(
            name,
            mode=PROFILE_MODE_INIT,
            default_archive_dir=default_data_dir(),
        )
        store = profile.open_store(time_source=self._clock)
        store.close()

    def open_profile(self, name: str):
        profile = select_play_profile(
            name,
            mode=PROFILE_MODE_RESUME,
            default_archive_dir=default_data_dir(),
        )
        return profile.open_store(time_source=self._clock)

    @staticmethod
    def run_cli(argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dpr.main(argv)
        return code, out.getvalue()

    # -------------------------------------------------------------- the server

    def start_server(
        self,
        store: RoleStore,
        *,
        dev_play_resources: bool,
        test_grant=None,
        growth_grant=None,
        achieve_level_grant=None,
        draw_odds="deterministic_fixture",
    ):
        events: list[dict] = []
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append),
            time_source=self._clock,
            role_store=store,
            test_grant=test_grant,
            growth_grant=growth_grant,
            achieve_level_grant=achieve_level_grant,
            draw_odds=draw_odds,
            dev_play_resources=dev_play_resources,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)
        return server, events

    # -------------------------------------------------------------- transport

    def connect_login(self, server: RevivalTCPServer, *, create: bool):
        """Drive the recovered login until the terminal S2C 283."""

        writer = WriterState()
        reader = ReaderState()
        payload = bytearray(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        if create:
            payload.extend(
                encode_packet(
                    C2S_REGISTRY.by_id(2), (b"ResourceDev", 1), writer
                ).wire
            )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
        stream = socket.create_connection(
            ("127.0.0.1", server.server_address[1]), timeout=READ_TIMEOUT_S
        )
        stream.settimeout(READ_TIMEOUT_S)
        stream.sendall(bytes(payload))
        buffer = bytearray()
        packets = self.read_until(stream, buffer, reader, SCENE_INFO_ID)
        return stream, writer, reader, buffer, packets

    def read_until(self, stream, buffer, reader, protocol_id, timeout=3.0):
        deadline = time.monotonic() + timeout
        packets = []
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                try:
                    chunk = stream.recv(65536)
                except socket.timeout:
                    return packets
                if not chunk:
                    return packets
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == protocol_id:
                return packets
        return packets

    @staticmethod
    def send(stream, writer, protocol_id, fields):
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, writer).wire
        )

    @staticmethod
    def drain(stream, buffer, reader, seconds: float = 0.3):
        """Every packet that arrives within ``seconds``; used to prove absence."""

        packets = []
        stream.settimeout(seconds)
        try:
            while True:
                try:
                    packet = decode_packet(buffer, S2C_REGISTRY, reader)
                except NeedMoreData:
                    chunk = stream.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    continue
                del buffer[: packet.consumed]
                packets.append(packet)
        except socket.timeout:
            pass
        finally:
            stream.settimeout(READ_TIMEOUT_S)
        return packets

    @staticmethod
    def _wait_events(
        events: list[dict], name: str, count: int, timeout: float = 3.0
    ) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [item for item in events if item.get("event") == name]
            if len(matches) >= count:
                return matches
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} #{count} was not emitted")

    @staticmethod
    def _sequence(packets) -> list[tuple[int, tuple]]:
        return [(packet.spec.protocol_id, packet.fields) for packet in packets]

    @staticmethod
    def _wires(packets) -> list[bytes]:
        return [packet.wire for packet in packets]

    @staticmethod
    def _role_value(packets):
        values = [p for p in packets if p.spec.protocol_id == ROLE_VALUE_ID]
        return values[-1]


class DevPlayResourcesModuleTests(unittest.TestCase):
    """The module's own contract (no server, no device)."""

    def test_the_canonical_overlay_round_trips_and_is_marked_synthetic(self) -> None:
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        self.addCleanup(store.close)
        overlay, created = dpr.seed_overlay(
            store, profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        )
        self.assertTrue(created)
        self.assertEqual(overlay.role_level, 29)
        self.assertEqual(
            (overlay.lottery_ticket, overlay.lottery_ticket_advanced),
            dpr.DEV_PLAY_RESOURCES_TICKET_COUNTS,
        )
        self.assertEqual(
            overlay.wallet_values, {"money": 100000.0, "yinpo": 50000.0, "jinpo": 50000.0}
        )
        payload = dpr.describe_overlay(store)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["role_level"], 29)
        self.assertEqual(payload["lottery_ticket"], 30)
        self.assertEqual(payload["lottery_ticket_advanced"], 30)
        self.assertEqual(payload["wallet"], overlay.wallet_values)
        self.assertEqual(payload["state"], "seeded")
        self.assertTrue(payload["replaceable"])
        self.assertFalse(payload["historical"])
        self.assertEqual(payload["never_write"], "qa-changan-main")
        self.assertEqual(
            payload["provenance"]["role_level"], "synthetic_compatibility"
        )
        self.assertEqual(
            payload["provenance"]["role_level_threshold"],
            "deterministic_recovered",
        )
        # Idempotent: a second seed keeps the first row and reports no creation.
        again, created_again = dpr.seed_overlay(
            store, profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        )
        self.assertFalse(created_again)
        self.assertEqual(again.seeded_at, overlay.seeded_at)
        # remove deletes only this module's key.
        self.assertTrue(dpr.remove_overlay(store))
        self.assertFalse(dpr.remove_overlay(store))
        self.assertIsNone(dpr.describe_overlay(store))

    def test_every_other_profile_is_refused(self) -> None:
        for name in ("qa-changan-main", "", None, "dev-main", "dev-resource"):
            with self.subTest(name=name):
                with self.assertRaises(dpr.DevPlayResourcesError) as caught:
                    dpr.require_profile(name)
                self.assertEqual(caught.exception.reason, "profile_not_allowed")

    def test_a_tampered_payload_is_refused_with_a_named_reason(self) -> None:
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        self.addCleanup(store.close)
        raw = json.dumps(
            dpr.overlay_payload(dpr.DEV_PLAY_RESOURCES_PROFILE_ID, 1),
            ensure_ascii=False,
        )
        cases = {
            "not json": ("{", dpr.REASON_META_INVALID_JSON),
            "not an object": ("[]", dpr.REASON_META_NOT_AN_OBJECT),
        }
        for label, (value, reason) in cases.items():
            with self.subTest(case=label):
                store.set_meta(dpr.DEV_PLAY_RESOURCES_META_KEY, value)
                with self.assertRaises(dpr.DevPlayResourcesError) as caught:
                    dpr.load_overlay(store)
                self.assertEqual(caught.exception.reason, reason)
        mutations = (
            ("level", lambda p: p.update(role_level=30), dpr.REASON_VALUES_MISMATCH),
            ("tickets", lambda p: p.update(lottery_ticket=31), dpr.REASON_VALUES_MISMATCH),
            (
                "advanced tickets",
                lambda p: p.update(lottery_ticket_advanced=29),
                dpr.REASON_VALUES_MISMATCH,
            ),
            (
                "wallet",
                lambda p: p["wallet"].update(money=1.0),
                dpr.REASON_VALUES_MISMATCH,
            ),
            ("state", lambda p: p.update(state="other"), dpr.REASON_VALUES_MISMATCH),
            (
                "provenance",
                lambda p: p["provenance"].update(wallet="official_recovered"),
                dpr.REASON_VALUES_MISMATCH,
            ),
            (
                "provenance missing",
                lambda p: p.pop("provenance"),
                dpr.REASON_VALUES_MISMATCH,
            ),
            ("origin", lambda p: p.update(origin="elsewhere"), dpr.REASON_ORIGIN_MISMATCH),
            ("profile", lambda p: p.update(profile_id="dev-babel-main"), dpr.REASON_PROFILE_MISMATCH),
            ("never_write", lambda p: p.update(never_write="qa"), dpr.REASON_NEVER_WRITE_MISMATCH),
            ("replaceable", lambda p: p.update(replaceable=False), dpr.REASON_FLAGS_MISMATCH),
            ("historical", lambda p: p.update(historical=True), dpr.REASON_FLAGS_MISMATCH),
            ("extra key", lambda p: p.update(extra=1), dpr.REASON_VALUES_MISMATCH),
            ("missing key", lambda p: p.pop("state"), dpr.REASON_VALUES_MISMATCH),
            ("format", lambda p: p.update(format="other"), dpr.REASON_FORMAT_UNKNOWN),
        )
        for label, mutate, reason in mutations:
            with self.subTest(case=label):
                payload = json.loads(raw)
                mutate(payload)
                store.set_meta(
                    dpr.DEV_PLAY_RESOURCES_META_KEY,
                    json.dumps(payload, ensure_ascii=False),
                )
                with self.assertRaises(dpr.DevPlayResourcesError) as caught:
                    dpr.load_overlay(store)
                self.assertEqual(caught.exception.reason, reason)


class DevPlayTicketLedgerPayloadTests(unittest.TestCase):
    """The persisted ledger payload: canonical, strict, never a guess."""

    def test_the_canonical_payload_round_trips(self) -> None:
        raw = dpr.encode_ticket_balance(
            {"lottery_ticket": 30, "lottery_ticket_advanced": 30}
        )
        self.assertEqual(
            dpr.decode_ticket_balance(raw),
            {"lottery_ticket": 30, "lottery_ticket_advanced": 30},
        )
        payload = json.loads(raw)
        self.assertEqual(payload["format"], dpr.DEV_PLAY_TICKET_BALANCE_META_FORMAT)
        self.assertEqual(
            payload["format_version"],
            dpr.DEV_PLAY_TICKET_BALANCE_META_FORMAT_VERSION,
        )
        self.assertEqual(payload["profile_id"], dpr.DEV_PLAY_RESOURCES_PROFILE_ID)
        self.assertEqual(payload["origin"], dpr.DEV_PLAY_TICKET_BALANCE_ORIGIN)
        self.assertEqual(
            payload["provenance"], dpr.DEV_PLAY_TICKET_BALANCE_PROVENANCE
        )
        self.assertTrue(payload["replaceable"])
        self.assertFalse(payload["historical"])
        self.assertEqual(payload["never_write"], "qa-changan-main")
        self.assertEqual(
            set(payload), dpr.DEV_PLAY_TICKET_BALANCE_PAYLOAD_KEYS
        )

    def test_a_tampered_ledger_is_refused_with_a_named_reason(self) -> None:
        raw = dpr.encode_ticket_balance(
            {"lottery_ticket": 12, "lottery_ticket_advanced": 7}
        )
        cases = {
            "not json": ("{", dpr.REASON_TICKET_BALANCE_INVALID_JSON),
            "not an object": ("[]", dpr.REASON_TICKET_BALANCE_NOT_AN_OBJECT),
            "format": (
                lambda p: p.update(format="other"),
                dpr.REASON_TICKET_BALANCE_FORMAT_UNKNOWN,
            ),
            "version": (
                lambda p: p.update(format_version=2),
                dpr.REASON_TICKET_BALANCE_FORMAT_VERSION,
            ),
            "profile": (
                lambda p: p.update(profile_id="qa-changan-main"),
                dpr.REASON_TICKET_BALANCE_PROFILE_MISMATCH,
            ),
            "origin": (
                lambda p: p.update(origin="official_recovered"),
                dpr.REASON_TICKET_BALANCE_ORIGIN_MISMATCH,
            ),
            "provenance": (
                lambda p: p.update(provenance="official_recovered"),
                dpr.REASON_TICKET_BALANCE_VALUES_MISMATCH,
            ),
            "negative": (
                lambda p: p.update(lottery_ticket=-1),
                dpr.REASON_TICKET_BALANCE_VALUES_MISMATCH,
            ),
            "float": (
                lambda p: p.update(lottery_ticket=1.5),
                dpr.REASON_TICKET_BALANCE_VALUES_MISMATCH,
            ),
            "extra key": (
                lambda p: p.update(extra=1),
                dpr.REASON_TICKET_BALANCE_VALUES_MISMATCH,
            ),
            "missing key": (
                lambda p: p.pop("lottery_ticket_advanced"),
                dpr.REASON_TICKET_BALANCE_VALUES_MISMATCH,
            ),
            "replaceable": (
                lambda p: p.update(replaceable=False),
                dpr.REASON_TICKET_BALANCE_FLAGS_MISMATCH,
            ),
            "historical": (
                lambda p: p.update(historical=True),
                dpr.REASON_TICKET_BALANCE_FLAGS_MISMATCH,
            ),
            "never_write": (
                lambda p: p.update(never_write="qa"),
                dpr.REASON_TICKET_BALANCE_NEVER_WRITE_MISMATCH,
            ),
        }
        for label, (mutate, reason) in cases.items():
            with self.subTest(case=label):
                if isinstance(mutate, str):
                    text = mutate
                else:
                    payload = json.loads(raw)
                    mutate(payload)
                    text = json.dumps(payload)
                with self.assertRaises(dpr.DevPlayResourcesError) as caught:
                    dpr.decode_ticket_balance(text)
                self.assertEqual(caught.exception.reason, reason)
        # The QA profile name is refused by the ledger exactly like the overlay.
        with self.assertRaises(dpr.DevPlayResourcesError) as caught:
            dpr.parse_ticket_balance(raw, profile_id="qa-changan-main")
        self.assertEqual(caught.exception.reason, dpr.REASON_PROFILE_NOT_ALLOWED)


class DevPlayResourcesCliTests(_EnvRoots):
    """The CLI is the only writer, and it only ever serves dev-resource-main."""

    def test_seed_status_remove_on_a_temporary_profile(self) -> None:
        name = dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        self.create_profile(name)
        # Give the archive a role and a provisioned wallet before the CLI runs,
        # so `remove` can be checked not to touch either.
        store = self.open_profile(name)
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
            role_name="资源档",
            career=1,
            create_time=int(FIXED_UNIX),
            compatibility_profile_id="cn-0.1.20.1-revival-v18",
        )
        store.provision_role_wallet_once(
            record=record,
            values={"money": 100000.0, "yinpo": 50000.0, "jinpo": 50000.0},
            provenance=dpr.DEV_PLAY_RESOURCES_ORIGIN,
        )
        store.close()

        code, out = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["created"])
        code, out = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["created"])
        # `seed` is the ledger's reset affordance: it always writes 30/30.
        self.assertEqual(
            json.loads(out)["ticket_balance"]["lottery_ticket"], 30
        )
        code, out = self.run_cli(["status", "--profile", name])
        self.assertEqual(code, 0)
        status = json.loads(out)["overlay"]
        self.assertEqual(status["role_level"], 29)
        self.assertEqual(status["wallet"], {"money": 100000.0, "yinpo": 50000.0, "jinpo": 50000.0})
        balance = json.loads(out)["ticket_balance"]
        self.assertEqual(
            (balance["lottery_ticket"], balance["lottery_ticket_advanced"]),
            (30, 30),
        )
        self.assertEqual(balance["origin"], dpr.DEV_PLAY_TICKET_BALANCE_ORIGIN)
        self.assertEqual(
            balance["provenance"], dpr.DEV_PLAY_TICKET_BALANCE_PROVENANCE
        )
        self.assertTrue(balance["replaceable"])
        self.assertFalse(balance["historical"])
        self.assertEqual(balance["never_write"], "qa-changan-main")

        # Spend part of the ledger, then seed again: the reset is explicit.
        store = self.open_profile(name)
        try:
            store.set_meta(
                dpr.DEV_PLAY_TICKET_BALANCE_META_KEY,
                dpr.encode_ticket_balance(
                    {"lottery_ticket": 17, "lottery_ticket_advanced": 5}
                ),
            )
        finally:
            store.close()
        code, out = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        self.assertEqual(
            (
                json.loads(out)["ticket_balance"]["lottery_ticket"],
                json.loads(out)["ticket_balance"]["lottery_ticket_advanced"],
            ),
            (30, 30),
        )

        code, out = self.run_cli(["remove", "--profile", name])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["removed"])
        self.assertTrue(json.loads(out)["ticket_balance_removed"])
        code, out = self.run_cli(["status", "--profile", name])
        self.assertIsNone(json.loads(out)["overlay"])
        self.assertIsNone(json.loads(out)["ticket_balance"])

        store = self.open_profile(name)
        try:
            self.assertIsNone(store.get_meta(dpr.DEV_PLAY_RESOURCES_META_KEY))
            self.assertIsNone(
                store.get_meta(dpr.DEV_PLAY_TICKET_BALANCE_META_KEY)
            )
            self.assertEqual(store.count(), 1)
            self.assertEqual(
                store.role_wallet_values(account_key=account_key),
                {"money": 100000.0, "yinpo": 50000.0, "jinpo": 50000.0},
            )
        finally:
            store.close()

    def test_the_cli_refuses_the_qa_profile_and_every_other_name(self) -> None:
        for name in ("qa-changan-main", "dev-main"):
            for command in ("seed", "status", "remove"):
                with self.subTest(profile=name, command=command):
                    code, _ = self.run_cli([command, "--profile", name])
                    self.assertEqual(code, 2)
        # A refused name never creates a profile directory.
        self.assertEqual(
            [path.name for path in Path(os.environ[PROFILE_ROOT_ENV]).glob("*")],
            [],
        )

    def test_the_cli_refuses_to_seed_a_profile_that_does_not_exist(self) -> None:
        code, _ = self.run_cli(
            ["seed", "--profile", dpr.DEV_PLAY_RESOURCES_PROFILE_ID]
        )
        self.assertEqual(code, 2)
        with self.assertRaises(PlayProfileRefusedError) as caught:
            select_play_profile(
                dpr.DEV_PLAY_RESOURCES_PROFILE_ID,
                mode=PROFILE_MODE_RESUME,
                default_archive_dir=default_data_dir(),
            )
        self.assertEqual(caught.exception.reason, "profile_missing")


class DevPlayResourcesLoginTests(_EnvRoots):
    """The login-side wire contract, positive and negative."""

    def _dev_profile_with_overlay(self) -> RoleStore:
        name = dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        self.create_profile(name)
        code, _ = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        store = self.open_profile(name)
        self.addCleanup(store.close)
        return store

    def test_the_overlay_reaches_the_client_in_the_recovered_order(self) -> None:
        store = self._dev_profile_with_overlay()
        server, events = self.start_server(store, dev_play_resources=True)
        stream, _writer, _reader, _buffer, packets = self.connect_login(
            server, create=True
        )
        stream.close()

        role_info = next(p for p in packets if p.spec.protocol_id == ROLE_INFO_ID)
        self.assertEqual(int(role_info.fields[4]), 29)
        role_value = self._role_value(packets)
        self.assertEqual(len([p for p in packets if p.spec.protocol_id == ROLE_VALUE_ID]), 1)
        self.assertEqual(role_value.fields[18], 30)
        self.assertEqual(role_value.fields[36], 30)
        self.assertIsInstance(role_value.fields[0], float)
        self.assertEqual(role_value.fields[0], 100000.0)
        self.assertEqual(role_value.fields[6], 50000)
        self.assertEqual(role_value.fields[7], 50000)
        self.assertIsInstance(role_value.fields[6], int)
        self.assertIsInstance(role_value.fields[7], int)

        applied = self._wait_events(
            events, "tcp_dev_play_resources_overlay_applied", 1
        )[0]
        self.assertEqual(applied["role_level"], 29)
        self.assertEqual(applied["wallet"]["money"], 100000.0)
        self.assertFalse(applied["persisted"])
        provisioned = self._wait_events(
            events, "tcp_dev_play_resources_wallet_provisioned", 1
        )[0]
        self.assertEqual(
            {item["value_name"]: item["amount"] for item in provisioned["values"]},
            {"money": 100000.0, "yinpo": 50000.0, "jinpo": 50000.0},
        )
        self.assertTrue(provisioned["persisted"])
        self.assertTrue(provisioned["replaceable"])
        self.assertFalse(provisioned["historical"])
        wallet_event = self._wait_events(events, "tcp_wallet_overlay_applied", 1)[0]
        self.assertEqual(
            wallet_event["play_profile_id"], dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        )
        self.assertEqual(
            wallet_event["value_origin"], dpr.DEV_PLAY_RESOURCES_VALUE_ORIGIN
        )
        self.assertEqual(wallet_event["provenance"], dpr.DEV_PLAY_RESOURCES_ORIGIN)
        self.assertFalse(wallet_event["historical_values_recovered"])
        self.assertTrue(wallet_event["replaceable"])
        self.assertFalse(wallet_event["historical"])
        self.assertEqual(
            {item["value_name"]: (item["index"], item["amount"]) for item in wallet_event["values"]},
            {"money": (1, 100000.0), "yinpo": (7, 50000), "jinpo": (8, 50000)},
        )
        tickets = self._wait_events(
            events, "tcp_dev_play_resources_ticket_overlay_applied", 1
        )[0]
        # TASK-0235: the tickets now come from the persisted ledger, not from a
        # session-only constant, so the event says persisted=true.
        self.assertTrue(tickets["persisted"])
        self.assertEqual(
            tickets["balance_origin"], dpr.DEV_PLAY_TICKET_BALANCE_ORIGIN
        )
        self.assertEqual(
            tickets["ledger_meta_key"], dpr.DEV_PLAY_TICKET_BALANCE_META_KEY
        )
        self.assertEqual(
            [(item["value_index"], item["amount"]) for item in tickets["values"]],
            [(18, 30), (36, 30)],
        )
        self.assertEqual(
            {item["provenance"] for item in tickets["values"]},
            {dpr.DEV_PLAY_TICKET_BALANCE_PROVENANCE},
        )
        ready = self._wait_events(
            events, "tcp_dev_play_resources_ticket_balance_ready", 1
        )[0]
        # The CLI `seed` already wrote the 30/30 ledger, so this login finds it.
        self.assertFalse(ready["created"])
        self.assertTrue(ready["persisted"])
        self.assertEqual(
            {item["value_name"]: item["amount"] for item in ready["values"]},
            {"lottery_ticket": 30, "lottery_ticket_advanced": 30},
        )
        self.assertEqual(
            store.get_meta(dpr.DEV_PLAY_TICKET_BALANCE_META_KEY),
            dpr.encode_ticket_balance(
                {"lottery_ticket": 30, "lottery_ticket_advanced": 30}
            ),
        )
        segment = self._wait_events(events, "tcp_login_sync_segment_sent", 1)[0]
        self.assertEqual(segment["role_level_overlay"], 29)
        self.assertEqual(
            segment["role_level_overlay_reason"], "dev_play_resource_overlay"
        )
        self.assertFalse(segment["role_level_overlay_persisted"])
        self.assertEqual(
            store.role_wallet_values(account_key=self._account_key(store)),
            {"money": 100000.0, "yinpo": 50000.0, "jinpo": 50000.0},
        )

        # A relogin applies the same overlay from the archive again, but the
        # wallet is not topped up a second time.
        stream, _w, _r, _b, second = self.connect_login(server, create=False)
        stream.close()
        second_value = self._role_value(second)
        self.assertEqual(second_value.fields[18], 30)
        self.assertEqual(second_value.fields[0], 100000.0)
        self.assertEqual(second_value.fields[6], 50000)
        self.assertEqual(second_value.fields[7], 50000)
        self.assertEqual(
            len(
                [
                    item
                    for item in events
                    if item.get("event")
                    == "tcp_dev_play_resources_wallet_provisioned"
                ]
            ),
            1,
        )
        self.assertEqual(store.count(), 1)
        self.assertEqual(
            store.role_wallet_values(account_key=self._account_key(store)),
            {"money": 100000.0, "yinpo": 50000.0, "jinpo": 50000.0},
        )

    @staticmethod
    def _account_key(store: RoleStore) -> str:
        return store.account_key(
            server_id=1.0,
            platform="android",
            channel_id="local-channel",
            channel_sub_id="0",
            sdk_identity="local-user",
        )

    def test_without_the_flag_meta_or_profile_the_login_is_the_frozen_v18_stream(
        self,
    ) -> None:
        def capture(*, profile_id: str, seeded: bool, flag: bool):
            store = RoleStore.in_memory(time_source=self._clock)
            store._profile_fields = {"play_profile_id": profile_id}
            self.addCleanup(store.close)
            if seeded:
                dpr.seed_overlay(
                    store, profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID
                )
            server, _events = self.start_server(
                store, dev_play_resources=flag
            )
            stream, _w, _r, _b, packets = self.connect_login(server, create=True)
            stream.close()
            return packets, store

        baseline, baseline_store = capture(
            profile_id="qa-changan-main", seeded=False, flag=False
        )
        role_info = next(
            p for p in baseline if p.spec.protocol_id == ROLE_INFO_ID
        )
        self.assertEqual(int(role_info.fields[4]), 1)
        baseline_value = self._role_value(baseline)
        self.assertEqual(baseline_value.fields[18], 0)
        self.assertEqual(baseline_value.fields[36], 0)
        self.assertEqual(baseline_value.fields[0], 0.0)
        self.assertEqual(baseline_store.role_wallet_values(
            account_key=self._account_key(baseline_store)
        ), {})

        cases = {
            "flag_off": dict(
                profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID,
                seeded=True,
                flag=False,
            ),
            "meta_absent": dict(
                profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID,
                seeded=False,
                flag=True,
            ),
            "profile_mismatch": dict(
                profile_id="qa-changan-main", seeded=True, flag=True
            ),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                packets, store = capture(**kwargs)
                self.assertEqual(self._sequence(packets), self._sequence(baseline))
                self.assertEqual(self._wires(packets), self._wires(baseline))
                self.assertEqual(
                    store.role_wallet_values(
                        account_key=self._account_key(store)
                    ),
                    {},
                )

    def test_a_broken_overlay_meta_is_reported_and_not_sent(self) -> None:
        name = dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        self.create_profile(name)
        store = self.open_profile(name)
        self.addCleanup(store.close)
        store.set_meta(dpr.DEV_PLAY_RESOURCES_META_KEY, "{not json")
        server, events = self.start_server(store, dev_play_resources=True)
        stream, writer, reader, buffer, packets = self.connect_login(
            server, create=True
        )
        try:
            role_info = next(
                p for p in packets if p.spec.protocol_id == ROLE_INFO_ID
            )
            self.assertEqual(int(role_info.fields[4]), 1)
            value = self._role_value(packets)
            self.assertEqual(value.fields[18], 0)
            self.assertEqual(value.fields[36], 0)
            self.assertEqual(value.fields[0], 0.0)
            # The draw gate is closed too: the client's own failure result.
            self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
            draw = self.read_until(stream, buffer, reader, 539)
        finally:
            stream.close()
        self.assertTrue(any(p.spec.protocol_id == 537 for p in draw))
        self.assertEqual(
            [p for p in draw if p.spec.protocol_id == 539][-1].fields, (2,)
        )
        self.assertFalse(any(p.spec.protocol_id == CARD_INFO_ID for p in draw))
        refused = self._wait_events(
            events, "tcp_dev_play_resources_overlay_refused", 1
        )[0]
        self.assertEqual(refused["reason"], dpr.REASON_META_INVALID_JSON)
        self.assertFalse(refused["applied"])
        self.assertEqual(
            store.role_wallet_values(account_key=self._account_key(store)), {}
        )
        self.assertEqual(store.card_instances(account_key=self._account_key(store)), ())


class DevPlayResourcesGrowthTests(_EnvRoots):
    """The persisted wallet, the client's own advance sink and the relogin."""

    def test_advance_spends_the_provisioned_wallet_and_relogin_keeps_it(self) -> None:
        name = dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        self.create_profile(name)
        code, _ = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        store = self.open_profile(name)
        self.addCleanup(store.close)
        account_key = DevPlayResourcesLoginTests._account_key(store)
        record, _ = store.create_or_get(
            account_key=account_key,
            server_id=1.0,
            role_name="资源档",
            career=1,
            create_time=int(FIXED_UNIX),
            compatibility_profile_id="cn-0.1.20.1-revival-v18",
        )
        store.record_card_instance(
            record=record,
            instance_id="advance-target",
            card_cfg_id=ADVANCE_CARD_CFG_ID,
            level=10,
            advance_level=0,
            sfm_id1=0,
            origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
        )
        server, events = self.start_server(store, dev_play_resources=True)

        stream, writer, reader, buffer, packets = self.connect_login(
            server, create=False
        )
        try:
            value = self._role_value(packets)
            self.assertEqual(value.fields[0], 100000.0)
            self.assertEqual(value.fields[6], 50000)
            self.send(stream, writer, ADVANCE_START_ID, ())
            self.send(stream, writer, ADVANCE_END_ID, (b"advance-target", 0))
            advanced = self.read_until(stream, buffer, reader, ADVANCE_COUNTER_ID)
        finally:
            stream.close()
        result = next(p for p in advanced if p.spec.protocol_id == 71)
        self.assertEqual(result.fields, (b"advance-target", True))
        values = {
            (p.fields[0], p.fields[1])
            for p in advanced
            if p.spec.protocol_id in (23, 24)
        }
        self.assertIn((7, 100000.0 - ADVANCE_COST_MONEY), values)
        self.assertIn((28, 50000 - ADVANCE_COST_YINPO), values)
        self.assertEqual(
            store.role_wallet_values(account_key=account_key),
            {
                "money": 100000.0 - ADVANCE_COST_MONEY,
                "yinpo": float(50000 - ADVANCE_COST_YINPO),
                "jinpo": 50000.0,
            },
        )
        self.assertEqual(
            store.card_instance(
                account_key=account_key, instance_id="advance-target"
            ).advance_level,
            1,
        )

        # Re-login: the spent balance stays spent (no top-up) and the card row
        # replays its advance level.
        stream, _w, _r, _b, second = self.connect_login(server, create=False)
        try:
            second_value = self._role_value(second)
            self.assertEqual(second_value.fields[0], 100000.0 - ADVANCE_COST_MONEY)
            self.assertEqual(second_value.fields[6], 50000 - ADVANCE_COST_YINPO)
            self.assertEqual(second_value.fields[7], 50000)
            cards = [p for p in second if p.spec.protocol_id == CARD_INFO_ID]
            target = next(
                p for p in cards if p.fields[0] == b"advance-target"
            )
            self.assertEqual(target.fields[4], 1)
        finally:
            stream.close()
        self.assertEqual(
            len(
                [
                    item
                    for item in events
                    if item.get("event")
                    == "tcp_dev_play_resources_wallet_provisioned"
                ]
            ),
            1,
        )
        self.assertEqual(store.count(), 1)


class DevPlayResourcesIsolationTests(_EnvRoots):
    """Schema, qa-only grants and the deterministic draw boundary."""

    def test_schema_is_unchanged_and_the_qa_grants_stay_refused(self) -> None:
        store = RoleStore.in_memory(time_source=self._clock)
        store._profile_fields = {
            "play_profile_id": dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        }
        self.addCleanup(store.close)
        dpr.seed_overlay(store, profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID)
        server, events = self.start_server(
            store,
            dev_play_resources=True,
            test_grant=OwnerDrawTestGrant(),
            growth_grant=OwnerGrowthTestGrant({3: 9}),
            achieve_level_grant=5,
            draw_odds="reconstructed_rate_table",
        )
        stream, _w, _r, _b, packets = self.connect_login(server, create=True)
        stream.close()

        # The qa-scoped grants were refused at construction; the resources
        # overlay is the only thing that reached the wire.
        self.assertIsNone(server.owner_draw_test_grant)
        self.assertIsNone(server.owner_growth_test_grant)
        self.assertIsNone(server.achieve_level_test_grant)
        refused = {
            item["event"]
            for item in events
            if item.get("event", "").endswith("_refused")
        }
        self.assertIn("synthetic_test_grant_refused", refused)
        self.assertIn("achieve_level_test_grant_refused", refused)
        self.assertFalse(
            any(
                item.get("event") == "synthetic_growth_grant_applied"
                for item in events
            )
        )
        ids = {p.spec.protocol_id for p in packets}
        self.assertNotIn(1167, ids)  # hot-spot draw lib start
        self.assertNotIn(1168, ids)  # hot-spot draw lib info
        self.assertNotIn(1170, ids)  # hot-spot draw lib end
        self.assertNotIn(89, ids)  # battle start
        self.assertNotIn(585, ids)  # achieve level
        self.assertNotIn(586, ids)
        self.assertNotIn(589, ids)
        self.assertNotIn(504, ids)  # special-card start
        self.assertNotIn(506, ids)
        self.assertNotIn(505, ids)
        value = DevPlayResourcesLoginTests._role_value(packets)
        self.assertEqual(value.fields[18], 30)
        self.assertEqual(value.fields[0], 100000.0)
        self.assertFalse(any(item.get("event") == "tcp_hot_spot_snapshot_sent" for item in events))

        self.assertEqual(SCHEMA_VERSION, 14)
        columns = [row[1] for row in store._conn.execute("PRAGMA table_info(roles)")]
        self.assertNotIn("level", columns)
        tables = {
            row[0]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("role_wallet", tables)
        self.assertIn("meta", tables)
        self.assertFalse([name for name in tables if "dev_play" in name])
        self.assertEqual(store.count(), 1)


class DevPlayResourcesDrawTests(_EnvRoots):
    """C2S 274/33 on the dev profile, and the closed gate everywhere else."""

    def _ready(self):
        name = dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        self.create_profile(name)
        code, _ = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        store = self.open_profile(name)
        self.addCleanup(store.close)
        server, events = self.start_server(store, dev_play_resources=True)
        return store, server, events

    def test_both_draw_families_are_deterministic_fixtures_in_this_archive(self) -> None:
        store, server, events = self._ready()
        stream, writer, reader, buffer, _packets = self.connect_login(
            server, create=True
        )
        try:
            self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
            first = self.read_until(stream, buffer, reader, 24)
            self.assertTrue(any(p.spec.protocol_id == 537 for p in first))
            self.assertEqual(
                [p for p in first if p.spec.protocol_id == 539][-1].fields, (1,)
            )
            first_results = [p for p in first if p.spec.protocol_id == 538]
            self.assertEqual(len(first_results), 1)
            self.assertTrue(
                first_results[0].fields[0].startswith(b"revival-fixture-draw-")
            )
            self.assertEqual(len([p for p in first if p.spec.protocol_id == CARD_INFO_ID]), 1)
            # TASK-0235: the new balance follows the result-end packet as S2C 24.
            self.assertEqual(
                [p.fields for p in first if p.spec.protocol_id == 24], [(252, 29)]
            )

            # A second draw in the same login still walks the fixture, with a
            # fresh serial, and charges the ledger again.
            self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
            second = self.read_until(stream, buffer, reader, 24)
            second_results = [p for p in second if p.spec.protocol_id == 538]
            self.assertEqual(len(second_results), 1)
            self.assertNotEqual(
                first_results[0].fields[0], second_results[0].fields[0]
            )
            self.assertEqual(
                [p.fields for p in second if p.spec.protocol_id == 24],
                [(252, 28)],
            )

            self.send(stream, writer, DRAW_DIAMOND_ID, (1, 262, 0, 0))
            advanced = self.read_until(stream, buffer, reader, 24)
            self.assertTrue(any(p.spec.protocol_id == 497 for p in advanced))
            self.assertTrue(any(p.spec.protocol_id == 500 for p in advanced))
            self.assertEqual(
                [p for p in advanced if p.spec.protocol_id == 499][-1].fields,
                (1,),
            )
            advanced_results = [
                p for p in advanced if p.spec.protocol_id == 498
            ]
            self.assertEqual(len(advanced_results), 1)
            self.assertTrue(
                advanced_results[0].fields[0].startswith(b"revival-fixture-draw-")
            )
            self.assertEqual(
                [p.fields for p in advanced if p.spec.protocol_id == 24],
                [(262, 29)],
            )
        finally:
            stream.close()

        account_key = DevPlayResourcesLoginTests._account_key(store)
        rows = store.card_instances(account_key=account_key)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {row.origin for row in rows},
            {dpr.DEV_PLAY_RESOURCES_FIXTURE_ORIGIN},
        )
        self.assertEqual(
            {row.fixture_kind for row in rows},
            {dpr.DEV_PLAY_RESOURCES_FIXTURE_KIND},
        )
        self.assertTrue(
            all(row.fixture_id.startswith("dev-resource-draw-") for row in rows)
        )
        fixture_events = self._wait_events(
            events, "tcp_draw_test_fixture_sent", 3
        )
        for event in fixture_events:
            self.assertFalse(event["real_probability"])
            self.assertEqual(
                event["origin"], dpr.DEV_PLAY_RESOURCES_FIXTURE_ORIGIN
            )
            self.assertEqual(
                event["play_profile_id"], dpr.DEV_PLAY_RESOURCES_PROFILE_ID
            )
            self.assertEqual(
                event["fixture_authority"], dpr.DEV_PLAY_RESOURCES_VALUE_ORIGIN
            )
            self.assertTrue(event["fixture_id"].startswith("dev-resource-draw-"))
        # No probability model was used even though the server was started
        # with the reconstructed odds mode in another test: the dev overlay
        # always walks the deterministic fixture.
        self.assertFalse(
            any(
                item.get("event") == "tcp_draw_probability_result_sent"
                for item in events
            )
        )

        # The fixture rows replay on the next login while the overlay is active,
        # and the S2C 27 ticket slots carry the charged balance.
        stream, _w, _r, _b, relogin = self.connect_login(server, create=False)
        try:
            cards = [p for p in relogin if p.spec.protocol_id == CARD_INFO_ID]
            self.assertEqual(len(cards), 3)
            relogin_value = self._role_value(relogin)
            self.assertEqual(relogin_value.fields[18], 28)
            self.assertEqual(relogin_value.fields[36], 29)
        finally:
            stream.close()

    def test_without_the_three_conditions_the_draw_gate_is_closed(self) -> None:
        def attempt(*, profile_id: str, seeded: bool, flag: bool, broken: bool, grant=None):
            store = RoleStore.in_memory(time_source=self._clock)
            store._profile_fields = {"play_profile_id": profile_id}
            self.addCleanup(store.close)
            if seeded:
                dpr.seed_overlay(
                    store, profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID
                )
            if broken:
                store.set_meta(dpr.DEV_PLAY_RESOURCES_META_KEY, "{not json")
            server, events = self.start_server(
                store, dev_play_resources=flag, test_grant=grant
            )
            stream, writer, reader, buffer, _packets = self.connect_login(
                server, create=True
            )
            try:
                self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
                packets = self.read_until(stream, buffer, reader, 539)
            finally:
                stream.close()
            return store, events, packets

        cases = {
            "flag_off": dict(
                profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID,
                seeded=True,
                flag=False,
                broken=False,
            ),
            "meta_absent": dict(
                profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID,
                seeded=False,
                flag=True,
                broken=False,
            ),
            "meta_broken": dict(
                profile_id=dpr.DEV_PLAY_RESOURCES_PROFILE_ID,
                seeded=True,
                flag=True,
                broken=True,
            ),
            "profile_mismatch": dict(
                profile_id="qa-changan-main", seeded=True, flag=True, broken=False
            ),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                store, events, packets = attempt(**kwargs)
                self.assertTrue(any(p.spec.protocol_id == 537 for p in packets))
                self.assertEqual(
                    [p for p in packets if p.spec.protocol_id == 539][-1].fields,
                    (2,),
                )
                self.assertFalse(
                    any(p.spec.protocol_id == CARD_INFO_ID for p in packets)
                )
                self.assertEqual(
                    store.card_instances(
                        account_key=DevPlayResourcesLoginTests._account_key(store)
                    ),
                    (),
                )
                refused = self._wait_events(
                    events, "tcp_draw_test_refused", 1
                )[-1]
                self.assertEqual(refused["reason"], "overlay_disabled")

        # The qa-only Owner gate is unchanged: without --test-grant it refuses,
        # with the grant it still answers the existing deterministic fixture.
        store, events, packets = attempt(
            profile_id="qa-changan-main",
            seeded=False,
            flag=False,
            broken=False,
        )
        self.assertEqual(
            self._wait_events(events, "tcp_draw_test_refused", 1)[-1]["reason"],
            "overlay_disabled",
        )
        store.close()
        store = RoleStore.in_memory(time_source=self._clock)
        store._profile_fields = {"play_profile_id": "qa-changan-main"}
        self.addCleanup(store.close)
        server, events = self.start_server(
            store, dev_play_resources=True, test_grant=OwnerDrawTestGrant()
        )
        stream, writer, reader, buffer, _packets = self.connect_login(
            server, create=True
        )
        try:
            self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
            packets = self.read_until(stream, buffer, reader, 539)
            trailing = self.drain(stream, buffer, reader)
        finally:
            stream.close()
        self.assertEqual(
            [p for p in packets if p.spec.protocol_id == 539][-1].fields, (1,)
        )
        self.assertEqual(
            len([p for p in packets if p.spec.protocol_id == CARD_INFO_ID]), 1
        )
        fixture = self._wait_events(
            events, "tcp_draw_test_fixture_sent", 1
        )[-1]
        self.assertIsNone(fixture.get("play_profile_id"))
        # TASK-0235: the qa --test-grant path stays exactly as it was -- no
        # ticket ledger, no S2C 24, no charge fields on the fixture event.
        self.assertNotIn("reward_type", fixture)
        self.assertNotIn("ticket_balance_after", fixture)
        self.assertIsNone(
            store.get_meta(dpr.DEV_PLAY_TICKET_BALANCE_META_KEY)
        )
        self.assertEqual(trailing, [])
        self.assertFalse(
            any(
                item.get("event")
                == "tcp_dev_play_resources_ticket_balance_sent"
                for item in events
            )
        )


class DevPlayResourcesTicketLedgerTests(_EnvRoots):
    """TASK-0235: the persisted 祈灵券 ledger and the C2S 274/33 charge."""

    def _ready(self):
        name = dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        self.create_profile(name)
        code, _ = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        store = self.open_profile(name)
        self.addCleanup(store.close)
        server, events = self.start_server(store, dev_play_resources=True)
        return store, server, events

    @staticmethod
    def _counts(store: RoleStore) -> dict[str, int]:
        balance = dpr.load_ticket_balance(store)
        assert balance is not None
        return balance.counts

    def test_a_ten_pull_charges_ten_and_the_value_packet_follows_the_end(self) -> None:
        store, server, events = self._ready()
        stream, writer, reader, buffer, _packets = self.connect_login(
            server, create=True
        )
        try:
            self.send(stream, writer, DRAW_TICKET_ID, (10, 0))
            packets = self.read_until(stream, buffer, reader, 24)
        finally:
            stream.close()
        ids = [p.spec.protocol_id for p in packets]
        self.assertEqual(ids[-1], 24)
        self.assertEqual(ids[-2], 539)
        self.assertEqual(
            [p.fields for p in packets if p.spec.protocol_id == 24], [(252, 20)]
        )
        self.assertEqual(len([p for p in packets if p.spec.protocol_id == 538]), 10)
        self.assertEqual(self._counts(store)["lottery_ticket"], 20)
        self.assertEqual(self._counts(store)["lottery_ticket_advanced"], 30)
        charged = self._wait_events(
            events, "tcp_dev_play_resources_ticket_balance_sent", 1
        )[0]
        self.assertEqual(charged["packet"], 24)
        self.assertEqual(charged["packet_name"], "returnOneValue_N")
        self.assertEqual(charged["reward_type"], 252)
        self.assertEqual(charged["value_index"], 18)
        self.assertEqual(charged["wire_position"], 19)
        self.assertEqual(charged["cost"], 10)
        self.assertEqual(charged["balance_before"], 30)
        self.assertEqual(charged["balance_after"], 20)
        self.assertFalse(charged["balance_seeded"])
        self.assertEqual(charged["ordering"], "synthetic_after_result_end")
        self.assertTrue(charged["synthetic_server_choice"])
        self.assertFalse(charged["historical_values_recovered"])
        self.assertEqual(charged["provenance"], "synthetic_compatibility")
        self.assertTrue(charged["replaceable"])
        self.assertFalse(charged["historical"])
        self.assertTrue(charged["persisted"])
        self.assertEqual(
            charged["ledger_origin"], dpr.DEV_PLAY_TICKET_BALANCE_ORIGIN
        )
        fixture = self._wait_events(events, "tcp_draw_test_fixture_sent", 1)[0]
        self.assertEqual(fixture["reward_type"], 252)
        self.assertEqual(fixture["ticket_cost"], 10)
        self.assertEqual(fixture["ticket_balance_before"], 30)
        self.assertEqual(fixture["ticket_balance_after"], 20)

    def test_the_advanced_pool_charges_the_262_ledger(self) -> None:
        store, server, events = self._ready()
        stream, writer, reader, buffer, _packets = self.connect_login(
            server, create=True
        )
        try:
            self.send(stream, writer, DRAW_DIAMOND_ID, (10, 262, 0, 0))
            packets = self.read_until(stream, buffer, reader, 24)
        finally:
            stream.close()
        self.assertEqual(
            [p.fields for p in packets if p.spec.protocol_id == 24],
            [(262, 20)],
        )
        self.assertEqual(self._counts(store)["lottery_ticket_advanced"], 20)
        self.assertEqual(self._counts(store)["lottery_ticket"], 30)
        charged = self._wait_events(
            events, "tcp_dev_play_resources_ticket_balance_sent", 1
        )[0]
        self.assertEqual((charged["value_index"], charged["wire_position"]), (36, 37))
        self.assertEqual((charged["balance_before"], charged["balance_after"]), (30, 20))

    def test_an_insufficient_balance_writes_nothing(self) -> None:
        store, server, events = self._ready()
        stream, writer, reader, buffer, _packets = self.connect_login(
            server, create=True
        )
        try:
            for _ in range(3):
                self.send(stream, writer, DRAW_TICKET_ID, (10, 0))
                self.read_until(stream, buffer, reader, 24)
            self.assertEqual(self._counts(store)["lottery_ticket"], 0)
            cards_before = len(
                store.card_instances(
                    account_key=DevPlayResourcesLoginTests._account_key(store)
                )
            )
            self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
            refused = self.read_until(stream, buffer, reader, 539)
        finally:
            stream.close()
        self.assertEqual(
            [p for p in refused if p.spec.protocol_id == 539][-1].fields, (2,)
        )
        self.assertFalse(any(p.spec.protocol_id == 538 for p in refused))
        self.assertFalse(any(p.spec.protocol_id == CARD_INFO_ID for p in refused))
        self.assertFalse(any(p.spec.protocol_id == 24 for p in refused))
        self.assertEqual(self._counts(store)["lottery_ticket"], 0)
        self.assertEqual(
            len(
                store.card_instances(
                    account_key=DevPlayResourcesLoginTests._account_key(store)
                )
            ),
            cards_before,
        )
        event = self._wait_events(events, "tcp_draw_test_refused", 1)[-1]
        self.assertEqual(event["reason"], "insufficient_tickets")
        self.assertEqual(event["reward_type"], 252)
        self.assertEqual(event["cost"], 1)
        self.assertEqual(event["balance_before"], 0)
        self.assertEqual(event["result_code"], 2)

    def test_unsupported_cost_type_and_hot_spot_are_named_refusals(self) -> None:
        store, server, events = self._ready()
        stream, writer, reader, buffer, _packets = self.connect_login(
            server, create=True
        )
        cases = (
            ((1, 6, 0, 0), "unsupported_cost_type", "crystal"),
            ((10, 252, 0, 0), "unsupported_cost_type", "normal-as-33"),
            ((10, 262, 1, 0), "unsupported_hot_spot", "hot-spot"),
        )
        try:
            for index, (fields, reason, label) in enumerate(cases, start=1):
                with self.subTest(case=label):
                    self.send(stream, writer, DRAW_DIAMOND_ID, fields)
                    packets = self.read_until(stream, buffer, reader, 499)
                    self.assertEqual(
                        [p for p in packets if p.spec.protocol_id == 499][-1].fields,
                        (2,),
                    )
                    self.assertFalse(
                        any(p.spec.protocol_id == 498 for p in packets)
                    )
                    self.assertFalse(
                        any(p.spec.protocol_id == CARD_INFO_ID for p in packets)
                    )
                    self.assertFalse(any(p.spec.protocol_id == 24 for p in packets))
                    refused = self._wait_events(
                        events, "tcp_draw_test_refused", index
                    )
                    self.assertEqual(refused[-1]["reason"], reason)
                    self.assertEqual(refused[-1]["result_code"], 2)
                    self.assertEqual(
                        self._counts(store),
                        {"lottery_ticket": 30, "lottery_ticket_advanced": 30},
                    )
        finally:
            stream.close()
        self.assertEqual(
            store.card_instances(
                account_key=DevPlayResourcesLoginTests._account_key(store)
            ),
            (),
        )

    def test_a_missing_ledger_is_seeded_inside_the_draw_transaction(self) -> None:
        store, server, events = self._ready()
        stream, writer, reader, buffer, _packets = self.connect_login(
            server, create=True
        )
        try:
            # The login seeded it; remove it to exercise the draw-side seed.
            self.assertTrue(
                store.delete_meta(dpr.DEV_PLAY_TICKET_BALANCE_META_KEY)
            )
            self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
            packets = self.read_until(stream, buffer, reader, 24)
        finally:
            stream.close()
        self.assertEqual(
            [p.fields for p in packets if p.spec.protocol_id == 24], [(252, 29)]
        )
        self.assertEqual(self._counts(store)["lottery_ticket"], 29)
        charged = self._wait_events(
            events, "tcp_dev_play_resources_ticket_balance_sent", 1
        )[0]
        self.assertTrue(charged["balance_seeded"])

    def test_a_failed_card_row_rolls_the_balance_back(self) -> None:
        store, server, _events = self._ready()
        stream, _w, _r, _b, _packets = self.connect_login(server, create=True)
        stream.close()
        account_key = DevPlayResourcesLoginTests._account_key(store)
        record = store.lookup(account_key)
        assert record is not None
        original = store._write_card_instance_locked
        calls = {"count": 0}

        def flaky(**kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RoleStoreError("simulated second card write failure")
            return original(**kwargs)

        store._write_card_instance_locked = flaky
        try:
            with self.assertRaises(RoleStoreError):
                store.record_ticket_draw(
                    record=record,
                    meta_key=dpr.DEV_PLAY_TICKET_BALANCE_META_KEY,
                    balance_field="lottery_ticket",
                    seed_counts=dpr.DEV_PLAY_TICKET_BALANCE_SEED,
                    cost=10,
                    cards=(
                        CardInstanceWrite(
                            instance_id="rollback-1",
                            card_cfg_id=2209,
                            fixture_id="rollback-fixture",
                            fixture_kind=dpr.DEV_PLAY_RESOURCES_FIXTURE_KIND,
                        ),
                        CardInstanceWrite(
                            instance_id="rollback-2",
                            card_cfg_id=2209,
                            fixture_id="rollback-fixture",
                            fixture_kind=dpr.DEV_PLAY_RESOURCES_FIXTURE_KIND,
                        ),
                    ),
                    decode=dpr.decode_ticket_balance,
                    encode=dpr.encode_ticket_balance,
                    provenance=dpr.DEV_PLAY_TICKET_BALANCE_PROVENANCE,
                )
        finally:
            store._write_card_instance_locked = original
        self.assertEqual(calls["count"], 2)
        # The first card row was written inside the transaction and rolled back
        # with the ledger write: neither exists.
        self.assertEqual(self._counts(store)["lottery_ticket"], 30)
        self.assertIsNone(
            store.card_instance(account_key=account_key, instance_id="rollback-1")
        )
        self.assertIsNone(
            store.card_instance(account_key=account_key, instance_id="rollback-2")
        )

    def test_a_damaged_ledger_closes_the_overlay_instead_of_announcing_a_balance(
        self,
    ) -> None:
        name = dpr.DEV_PLAY_RESOURCES_PROFILE_ID
        self.create_profile(name)
        code, _ = self.run_cli(["seed", "--profile", name])
        self.assertEqual(code, 0)
        store = self.open_profile(name)
        self.addCleanup(store.close)
        store.set_meta(dpr.DEV_PLAY_TICKET_BALANCE_META_KEY, "{not json")
        server, events = self.start_server(store, dev_play_resources=True)
        stream, writer, reader, buffer, packets = self.connect_login(
            server, create=True
        )
        try:
            role_info = next(
                p for p in packets if p.spec.protocol_id == ROLE_INFO_ID
            )
            self.assertEqual(int(role_info.fields[4]), 1)
            value = self._role_value(packets)
            self.assertEqual(value.fields[18], 0)
            self.assertEqual(value.fields[36], 0)
            self.send(stream, writer, DRAW_TICKET_ID, (1, 0))
            draw = self.read_until(stream, buffer, reader, 539)
        finally:
            stream.close()
        self.assertEqual(
            [p for p in draw if p.spec.protocol_id == 539][-1].fields, (2,)
        )
        self.assertFalse(any(p.spec.protocol_id == CARD_INFO_ID for p in draw))
        refused = self._wait_events(
            events, "tcp_dev_play_resources_ticket_balance_refused", 1
        )[0]
        self.assertEqual(
            refused["reason"], dpr.REASON_TICKET_BALANCE_INVALID_JSON
        )
        self.assertFalse(refused["applied"])
        # The marker is not overwritten by the refusal.
        self.assertEqual(
            store.get_meta(dpr.DEV_PLAY_TICKET_BALANCE_META_KEY), "{not json"
        )
        self.assertEqual(store.card_instances(account_key=self._key(store)), ())

    def _key(self, store: RoleStore) -> str:
        return DevPlayResourcesLoginTests._account_key(store)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
