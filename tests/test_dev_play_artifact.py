"""TASK-0258 -- the dev-play artifact_dgn entry data plane (offline).

The overlay exists so the client's own 神器副本 entry page can open on a
separate dev-play archive without touching the permanent ``qa-changan-main``
save:

* ``S2C 10 returnRoleInfo`` announces level 60 (``functionOpen[43].openLevel``);
* ``C2S 744 artifactDgnOpenUI`` is answered with ``S2C 1526`` (the client
  formula week + ``cfgArtifactDgnWeek`` open flags) and ``S2C 1527`` (the zero
  first-enter state), in that order.

Everything is profile-scoped and opt-in.  The tests below prove the positive
path, every "one condition missing" negative path **byte for byte** against the
frozen login stream, the shipped-config derivation of the week matrix, every
week/day the formulas can produce, and the boundary that nothing else in the
artifactDgn family is emitted.
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

from revival_stub import dev_play_artifact as dpa
from revival_stub.app import default_data_dir
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.eventlog import EventLogger
from revival_stub.play_profile import (
    PROFILE_MODE_INIT,
    PROFILE_MODE_RESUME,
    PROFILE_ROOT_ENV,
    PlayProfileRefusedError,
    select_play_profile,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import SCHEMA_VERSION, RoleStore
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_CONTRACT = PROJECT_ROOT / "reports/task_0257_artifact_dgn_contract.json"
CFG_ARTIFACT_DGN_CHUNK = (
    PROJECT_ROOT
    / "max_decoder/forensics_artifact_dgn"
    / "zys_scAll__cfg__artifactDgn__pkgCfgArtifactDgn.lua"
)

#: One fixed clock for both the store and the server, so two fresh archives
#: produce the same role row (and therefore the same wire bytes).  It yields
#: week 5 / shop day 2, i.e. a non-default week: a hardcoded week 1 cannot pass.
FIXED_UNIX = 1_790_000_000.0

#: A second clock exactly three week loops later: week 3, shop day 2+3.
SECOND_UNIX = FIXED_UNIX + 3 * 259200

#: How long one login / one answer may take in this suite.
READ_TIMEOUT_S = 8.0

ROLE_INFO_ID = 10
SCENE_INFO_ID = 283
WEEK_AND_OPEN_ID = 1526
RACE_AND_TOWER_ID = 1527
ACH_INFO_ID = 1551
PATH_ID = 1531
NODE_ID = 1530

#: TASK-0351: the plan sends one 1530 per grid row; deriving the count
#: from the builder keeps these assertions honest when the grid grows.
NODE_ROWS = len([1 for _n, _f in dpa.tower_entry_packets(dpa.tower_card_instance_ids()) if _n == "artifactDgnSendNode"])
TEAM_ID = 1538
MAGIC_START_ID = 45
MAGIC_INFO_ID = 46
MAGIC_END_ID = 47
MONTH_SIGN_ACK_ID = 1500

#: The artifactDgn packets this slice must never emit.
#: TASK-0270 implemented 1529/1530/1531/1538 (the tower entry plan), so the boundary now
#: covers the later edges the 744 reply still must not touch.
#: TASK-0295 registered 1537 artifactDgnSendCard; 1528 stays absent.
UNIMPLEMENTED_ARTIFACT_S2C = (1528,)


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
            code = dpa.main(argv)
        return code, out.getvalue()

    # -------------------------------------------------------------- the server

    def start_server(
        self,
        store: RoleStore,
        *,
        dev_play_artifact: bool,
        clock=None,
    ):
        events: list[dict] = []
        source = clock or self._clock
        server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=events.append),
            time_source=source,
            role_store=store,
            dev_play_artifact=dev_play_artifact,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)
        return server, events

    def login(self, server: RevivalTCPServer, *, create: bool):
        """Drive the recovered login until the terminal S2C 283."""

        writer = WriterState()
        reader = ReaderState()
        payload = bytearray(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        if create:
            payload.extend(
                encode_packet(
                    C2S_REGISTRY.by_id(2), (b"ArtifactDev", 1), writer
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
        packets = []
        deadline = time.monotonic() + READ_TIMEOUT_S
        try:
            while time.monotonic() < deadline:
                try:
                    packet = decode_packet(buffer, S2C_REGISTRY, reader)
                except NeedMoreData:
                    chunk = stream.recv(65536)
                    if not chunk:
                        self.fail(
                            "server closed the login stream after "
                            f"{len(packets)} packets"
                        )
                    buffer.extend(chunk)
                    continue
                del buffer[: packet.consumed]
                packets.append(packet)
                if packet.spec.protocol_id == SCENE_INFO_ID:
                    return stream, packets
        finally:
            stream.close()
        self.fail(
            f"login never reached S2C {SCENE_INFO_ID} "
            f"(got {[p.spec.protocol_id for p in packets]})"
        )

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

    def _login_keep_open(self, server: RevivalTCPServer):
        """Drive the login to S2C 283 without closing the socket."""

        writer = WriterState()
        reader = ReaderState()
        payload = bytearray(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(2), (b"ArtifactDev", 1), writer).wire
        )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
        stream = socket.create_connection(
            ("127.0.0.1", server.server_address[1]), timeout=READ_TIMEOUT_S
        )
        stream.settimeout(READ_TIMEOUT_S)
        self.addCleanup(stream.close)
        stream.sendall(bytes(payload))
        buffer = bytearray()
        packets = []
        deadline = time.monotonic() + READ_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                chunk = stream.recv(65536)
                if not chunk:
                    self.fail(
                        "server closed the login stream after "
                        f"{len(packets)} packets"
                    )
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == SCENE_INFO_ID:
                return stream, buffer, reader, writer
        self.fail("login never reached S2C 283")

    @staticmethod
    def _send(stream, writer, protocol_id: int, fields) -> None:
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, writer).wire
        )

    def _read_through(self, stream, buffer, reader, protocol_id: int):
        """Read packets until (and including) the first ``protocol_id``."""

        packets = []
        deadline = time.monotonic() + READ_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                packet = decode_packet(buffer, S2C_REGISTRY, reader)
            except NeedMoreData:
                chunk = stream.recv(65536)
                if not chunk:
                    self.fail(
                        f"server closed the socket before S2C {protocol_id}"
                    )
                buffer.extend(chunk)
                continue
            del buffer[: packet.consumed]
            packets.append(packet)
            if packet.spec.protocol_id == protocol_id:
                return packets
        self.fail(f"no S2C {protocol_id} answer arrived")

    def _drain(self, stream, buffer, reader, window: float = 0.5):
        """Every packet that follows within ``window`` seconds (may be none)."""

        packets = []
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


class DevPlayArtifactModuleTests(unittest.TestCase):
    """The module's own contract (no server, no device)."""

    def test_the_canonical_overlay_round_trips_and_is_marked_synthetic(self) -> None:
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        self.addCleanup(store.close)
        overlay, created = dpa.seed_overlay(
            store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID
        )
        self.assertTrue(created)
        self.assertEqual(overlay.role_level, dpa.ARTIFACT_DGN_OPEN_LEVEL)
        self.assertEqual(overlay.entry, dpa.DEV_PLAY_ARTIFACT_ENTRY)
        payload = dpa.describe_overlay(store)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["role_level"], 60)
        self.assertEqual(payload["entry"]["open_level"], 60)
        self.assertEqual(payload["entry"]["macro"], "artifact_dgn")
        self.assertTrue(payload["replaceable"])
        self.assertFalse(payload["historical"])
        self.assertEqual(payload["never_write"], "qa-changan-main")
        self.assertEqual(
            payload["provenance"]["role_level"], "synthetic_compatibility"
        )
        self.assertEqual(
            payload["provenance"]["week"], "deterministic_recovered"
        )
        # Idempotent: a second seed keeps the first row and reports no creation.
        again, created_again = dpa.seed_overlay(
            store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID
        )
        self.assertFalse(created_again)
        self.assertEqual(again.seeded_at, overlay.seeded_at)
        # remove deletes only this module's key.
        self.assertTrue(dpa.remove_overlay(store))
        self.assertFalse(dpa.remove_overlay(store))
        self.assertIsNone(dpa.describe_overlay(store))

    def test_every_other_profile_is_refused(self) -> None:
        for name in ("qa-changan-main", "", None, "dev-main", "dev-artifact"):
            with self.subTest(name=name):
                with self.assertRaises(dpa.DevPlayArtifactError) as caught:
                    dpa.require_profile(name)
                self.assertEqual(caught.exception.reason, "profile_not_allowed")

    def test_a_tampered_payload_is_refused_with_a_named_reason(self) -> None:
        store = RoleStore.in_memory(time_source=lambda: FIXED_UNIX)
        self.addCleanup(store.close)
        raw = json.dumps(
            dpa.overlay_payload(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID, 1),
            ensure_ascii=False,
        )
        cases = {
            "not json": ("{", dpa.REASON_META_INVALID_JSON),
            "not an object": ("[]", dpa.REASON_META_NOT_AN_OBJECT),
        }
        for label, (value, reason) in cases.items():
            with self.subTest(case=label):
                store.set_meta(dpa.DEV_PLAY_ARTIFACT_META_KEY, value)
                with self.assertRaises(dpa.DevPlayArtifactError) as caught:
                    dpa.load_overlay(store)
                self.assertEqual(caught.exception.reason, reason)
        variants = []
        for mutate in (
            lambda payload: payload.update(role_level=61),
            lambda payload: payload["entry"].update(open_level=59),
            lambda payload: payload["entry"].update(macro="equip_fight"),
            lambda payload: payload.update(origin="somewhere_else"),
            lambda payload: payload.update(never_write="dev-artifact-main"),
            lambda payload: payload.update(replaceable=False),
            lambda payload: payload["provenance"].update(
                week="synthetic_compatibility"
            ),
            lambda payload: payload.pop("provenance"),
            lambda payload: payload.update(extra="unexpected"),
        ):
            payload = json.loads(raw)
            mutate(payload)
            variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload):
                store.set_meta(
                    dpa.DEV_PLAY_ARTIFACT_META_KEY,
                    json.dumps(payload, ensure_ascii=False),
                )
                with self.assertRaises(dpa.DevPlayArtifactError) as caught:
                    dpa.load_overlay(store)
                self.assertIn(
                    caught.exception.reason,
                    {
                        dpa.REASON_VALUES_MISMATCH,
                        dpa.REASON_ORIGIN_MISMATCH,
                        dpa.REASON_NEVER_WRITE_MISMATCH,
                        dpa.REASON_FLAGS_MISMATCH,
                    },
                )

    def test_the_week_formula_mirrors_the_client_for_every_week_and_shop_day(self) -> None:
        """The Lua body is ``modf``/``fmod`` arithmetic; mirror it independently."""

        seen_weeks = set()
        seen_shop_days = set()
        for step in range(20):
            now = 316800 + step * 86400 + 3600
            # Independent arithmetic for a positive shifted clock.
            shifted = now - 316800
            expected_week = (shifted // 259200) % 5 + 1
            expected_shop = (shifted // 86400) % 4 + 1
            week, integral, use_sec, rest_sec = dpa.get_cur_week(now)
            shop_week, shop_integral = dpa.get_cur_week4_shop(now)
            self.assertEqual(week, expected_week)
            self.assertEqual(shop_week, expected_shop)
            self.assertEqual(integral, shifted // 259200)
            self.assertEqual(shop_integral, shifted // 86400)
            self.assertEqual(use_sec, float(shifted % 259200))
            self.assertEqual(rest_sec, float(259200 - shifted % 259200))
            seen_weeks.add(week)
            seen_shop_days.add(shop_week)
        self.assertEqual(seen_weeks, {1, 2, 3, 4, 5})
        self.assertEqual(seen_shop_days, {1, 2, 3, 4})
        # The two module constants are the client's own loop widths.
        self.assertEqual(dpa.ARTIFACT_WEEK_LOOP_S, 3 * 24 * 60 * 60)
        self.assertEqual(dpa.ARTIFACT_SHOP_LOOP_S, 1 * 24 * 60 * 60)
        self.assertEqual(dpa.ARTIFACT_WEEK_EPOCH_OFFSET_S, 316800)

    def test_the_week_formula_refuses_a_week_outside_the_shipped_table(self) -> None:
        # Far enough before the 316800 s epoch offset the shifted clock is a
        # full negative loop and Lua's fmod yields a non-positive week; there is
        # no such cfg row.  (The last second before the offset truncates to -0,
        # which the client itself maps to week 1 -- mirrored, not special-cased.)
        for now in (0, 1000, 316800 - 259200):
            with self.subTest(now=now):
                with self.assertRaises(dpa.DevPlayArtifactError) as caught:
                    dpa.week_and_open_fields(now)
                self.assertEqual(caught.exception.reason, "artifact_week_out_of_range")

    def test_the_open_matrix_is_the_frozen_contract_matrix(self) -> None:
        contract = json.loads(ARTIFACT_CONTRACT.read_text(encoding="utf-8"))
        matrix = contract["config"]["relations"]["week"]["open_matrix"]
        self.assertEqual(
            sorted(int(key) for key in matrix), sorted(dpa.ARTIFACT_WEEK_OPEN)
        )
        for week, row in sorted(matrix.items()):
            with self.subTest(week=week):
                self.assertEqual(
                    [1 if flag else 0 for flag in dpa.week_open_flags(int(week))],
                    [int(value) for value in row],
                )
        for week, row in dpa.ARTIFACT_WEEK_OPEN.items():
            self.assertEqual(len(row), 5)
            self.assertEqual(sum(row), 2)  # every shipped week opens two races
            self.assertEqual(
                tuple(bool(value) for value in row), dpa.week_open_flags(week)
            )

    def test_the_two_answer_packets_use_the_registered_formats(self) -> None:
        self.assertEqual(
            (C2S_REGISTRY.by_id(744).name, C2S_REGISTRY.by_id(744).fmt),
            ("artifactDgnOpenUI", "N"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(WEEK_AND_OPEN_ID).name,
             S2C_REGISTRY.by_id(WEEK_AND_OPEN_ID).fmt),
            ("artifactDgnSendWeekAndOpen", "NNyyyyy"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(RACE_AND_TOWER_ID).name,
             S2C_REGISTRY.by_id(RACE_AND_TOWER_ID).fmt),
            ("artifactDgnSendRaceAndTower", "NNNNNN"),
        )
        # Nothing else in the family is implemented by this slice.
        for unregistered_id in UNIMPLEMENTED_ARTIFACT_S2C:
            with self.assertRaises(KeyError):
                S2C_REGISTRY.by_id(unregistered_id)
        # TASK-0270 registered the two commit requests (745 team, 746 race); the rest of the
        # family stays absent.
        for registered_c2s, name in ((745, "artifactDgnSetTeam"),
                                     (746, "artifactDgnSetRace"),
                                     (752, "artifactDgnEnemyTeamInfo"),
                                     (747, "artifactDgnDoEvt")):
            self.assertEqual(C2S_REGISTRY.by_id(registered_c2s).name, name)
        # TASK-0278 registered 752 (the node tap); RUN9/TASK-0293 registered 747 (the team-info
        # confirm, which is what the client actually sends before C2S 69). The rest stays absent.
        # TASK-0352 registered the node-interaction family: 749 (artifactDgnGoNext) is the
        # "next layer" button the live session pressed (an unregistered id closed the socket),
        # and 748/750/751/753 are the relic / carriage / goods interactions behind the same taps.
        for registered_c2s, name in ((748, "artifactDgnDoSelYiwu"),
                                     (749, "artifactDgnGoNext"),
                                     (750, "artifactDgnSelCard"),
                                     (751, "artifactDgnDoSelCard"),
                                     (753, "artifactDgnUseGoods")):
            self.assertEqual(C2S_REGISTRY.by_id(registered_c2s).name, name)

    def test_the_race_and_tower_state_is_legal_and_open(self) -> None:
        # TASK-0282: the device run proved the zero state is unplayable -- getDgnDescCfg(0) is
        # nil and tbTmp[0] is nil, so show() aborts and the tower view stays prefab default.
        # The dev-play overlay therefore announces a pair that is legal (race 1..5, tower 1..9)
        # AND open in the current week.
        fields = dpa.race_and_tower_fields()
        self.assertEqual(fields, (3, 1, 0, 0, 0, 0))
        self.assertIn(fields[0], range(1, 6))
        self.assertIn(fields[1], range(1, 10))
        self.assertEqual(
            dpa.ARTIFACT_RACE_TOWER_DEV_PLAY_TITLE, "阴殿-第一层"
        )
        open_races = [index + 1 for index, flag in enumerate(
            dpa.week_open_flags(2)) if flag]
        self.assertIn(fields[0], open_races)
        self.assertEqual(
            dpa.ARTIFACT_RACE_TOWER_VALUE_PROVENANCE,
            "synthetic_compatibility",
        )
        self.assertEqual(
            dpa.ARTIFACT_RACE_TOWER_FIELD_6_SEMANTICS, "unknown_placeholder"
        )
        self.assertEqual(dpa.ARTIFACT_RACE_TOWER_FIELD_6_PROVENANCE, "unknown")
        self.assertEqual(dpa.ARTIFACT_RACE_TOWER_FIELD_6_UNKNOWN_ID, "U-3")
        self.assertEqual(dpa.ARTIFACT_OPEN_UI_UNKNOWN_ID, "U-1")

    def test_the_client_formula_source_still_carries_the_frozen_constants(self) -> None:
        if not CFG_ARTIFACT_DGN_CHUNK.is_file():  # pragma: no cover - APK absent
            self.skipTest("the recovered cfgArtifactDgn chunk is not present")
        text = CFG_ARTIFACT_DGN_CHUNK.read_text(encoding="utf-8")
        # getCurWeek / getCurWeek4Shop bodies (lines 111-126 / 144-152).
        self.assertIn("dNow = dNow - 316800", text)
        self.assertIn("local loopIntervalDay = 3", text)
        self.assertIn("local loopIntervalDay = 1", text)
        self.assertIn("math.fmod(dInt, 5)", text)
        self.assertIn("math.fmod(dInt, 4)", text)
        self.assertIn("local r8_5 = math.fmod(dInt, 5)", text)
        self.assertIn("local r11_3 = math.fmod(dInt, 4)", text)
        # The open-flag rule the booleans must mirror.
        self.assertIn('cfg["open" .. i] == 1', text)


class DevPlayArtifactCliTests(_EnvRoots):
    """The CLI is the only writer, and it only ever serves dev-artifact-main."""

    def test_seed_status_remove_on_a_temporary_profile(self) -> None:
        self.create_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        code, out = self.run_cli(
            ["seed", "--profile", dpa.DEV_PLAY_ARTIFACT_PROFILE_ID]
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["created"])
        code, out = self.run_cli(
            ["status", "--profile", dpa.DEV_PLAY_ARTIFACT_PROFILE_ID]
        )
        self.assertEqual(code, 0)
        status = json.loads(out)["overlay"]
        self.assertEqual(status["role_level"], 60)
        self.assertEqual(status["entry"], dpa.DEV_PLAY_ARTIFACT_ENTRY)
        code, out = self.run_cli(
            ["remove", "--profile", dpa.DEV_PLAY_ARTIFACT_PROFILE_ID]
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["removed"])
        code, out = self.run_cli(
            ["status", "--profile", dpa.DEV_PLAY_ARTIFACT_PROFILE_ID]
        )
        self.assertIsNone(json.loads(out)["overlay"])

    def test_the_cli_refuses_the_qa_profile_and_every_other_name(self) -> None:
        for name in ("qa-changan-main", "dev-main"):
            for command in ("seed", "status", "remove"):
                with self.subTest(profile=name, command=command):
                    code, _ = self.run_cli([command, "--profile", name])
                    self.assertEqual(code, 2)
        # A refused name never creates a profile directory.
        self.assertEqual(
            [
                path.name
                for path in Path(os.environ[PROFILE_ROOT_ENV]).glob("*")
            ],
            [],
        )

    def test_the_cli_refuses_to_seed_a_profile_that_does_not_exist(self) -> None:
        code, _ = self.run_cli(
            ["seed", "--profile", dpa.DEV_PLAY_ARTIFACT_PROFILE_ID]
        )
        self.assertEqual(code, 2)
        with self.assertRaises(PlayProfileRefusedError) as caught:
            select_play_profile(
                dpa.DEV_PLAY_ARTIFACT_PROFILE_ID,
                mode=PROFILE_MODE_RESUME,
                default_archive_dir=default_data_dir(),
            )
        self.assertEqual(caught.exception.reason, "profile_missing")


class DevPlayArtifactLoginTests(_EnvRoots):
    """The login-side wire contract, positive and negative."""

    def _dev_profile_with_overlay(self) -> RoleStore:
        self.create_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        code, _ = self.run_cli(
            ["seed", "--profile", dpa.DEV_PLAY_ARTIFACT_PROFILE_ID]
        )
        self.assertEqual(code, 0)
        store = self.open_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        self.addCleanup(store.close)
        return store

    def test_the_overlay_announces_level_60_without_extra_packets(self) -> None:
        store = self._dev_profile_with_overlay()
        server, events = self.start_server(store, dev_play_artifact=True)
        _stream, packets = self.login(server, create=True)

        ids = [packet.spec.protocol_id for packet in packets]
        role_info = next(p for p in packets if p.spec.protocol_id == ROLE_INFO_ID)
        self.assertEqual(int(role_info.fields[4]), dpa.ARTIFACT_DGN_OPEN_LEVEL)
        # The overlay is a login *level* only: no artifact packet is pushed.
        self.assertNotIn(WEEK_AND_OPEN_ID, ids)
        self.assertNotIn(RACE_AND_TOWER_ID, ids)
        for unimplemented in UNIMPLEMENTED_ARTIFACT_S2C:
            self.assertNotIn(unimplemented, ids)

        applied = self._wait_events(
            events, "tcp_dev_play_artifact_overlay_applied", 1
        )[0]
        self.assertEqual(applied["role_level"], 60)
        self.assertEqual(applied["entry"], dpa.DEV_PLAY_ARTIFACT_ENTRY)
        self.assertFalse(applied["persisted"])

        segment = self._wait_events(events, "tcp_login_sync_segment_sent", 1)[0]
        self.assertEqual(segment["role_level_overlay"], 60)
        self.assertEqual(
            segment["role_level_overlay_reason"], "dev_play_artifact_overlay"
        )
        self.assertFalse(segment["role_level_overlay_persisted"])
        self.assertEqual(
            segment["role_level_overlay_origin"], dpa.DEV_PLAY_ARTIFACT_ORIGIN
        )

        # No schema or role-row change: the overlay is session-scoped only.
        self.assertEqual(SCHEMA_VERSION, 14)
        columns = [
            row[1] for row in store._conn.execute("PRAGMA table_info(roles)")
        ]
        self.assertNotIn("level", columns)
        self.assertEqual(store.count(), 1)

        # A relogin applies the same overlay from the archive's meta again.
        _stream, second = self.login(server, create=False)
        second_role = next(
            p for p in second if p.spec.protocol_id == ROLE_INFO_ID
        )
        self.assertEqual(int(second_role.fields[4]), 60)
        self.assertNotIn(14, [p.spec.protocol_id for p in second])
        self._wait_events(events, "tcp_dev_play_artifact_overlay_applied", 2)
        self.assertEqual(store.count(), 1)

    def test_without_the_flag_or_meta_or_profile_the_login_is_the_frozen_stream(
        self,
    ) -> None:
        def capture(*, profile_id: str, seeded: bool, flag: bool):
            store = RoleStore.in_memory(time_source=self._clock)
            store._profile_fields = {"play_profile_id": profile_id}
            self.addCleanup(store.close)
            if seeded:
                dpa.seed_overlay(
                    store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID
                )
            server, _events = self.start_server(
                store, dev_play_artifact=flag
            )
            _stream, packets = self.login(server, create=True)
            return packets

        baseline = capture(
            profile_id="qa-changan-main", seeded=False, flag=False
        )
        self.assertNotIn(
            WEEK_AND_OPEN_ID, [p.spec.protocol_id for p in baseline]
        )
        role_info = next(
            p for p in baseline if p.spec.protocol_id == ROLE_INFO_ID
        )
        self.assertEqual(int(role_info.fields[4]), 1)

        cases = {
            "flag_off": dict(
                profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID,
                seeded=True,
                flag=False,
            ),
            "meta_absent": dict(
                profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID,
                seeded=False,
                flag=True,
            ),
            "profile_mismatch": dict(
                profile_id="qa-changan-main", seeded=True, flag=True
            ),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                packets = capture(**kwargs)
                self.assertEqual(
                    self._sequence(packets), self._sequence(baseline)
                )
                self.assertEqual(self._wires(packets), self._wires(baseline))
                self.assertNotIn(
                    WEEK_AND_OPEN_ID,
                    [p.spec.protocol_id for p in packets],
                )
                self.assertNotIn(
                    RACE_AND_TOWER_ID,
                    [p.spec.protocol_id for p in packets],
                )

    def test_a_broken_overlay_meta_is_reported_and_not_applied(self) -> None:
        self.create_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        store = self.open_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        self.addCleanup(store.close)
        store.set_meta(dpa.DEV_PLAY_ARTIFACT_META_KEY, "{not json")
        server, events = self.start_server(store, dev_play_artifact=True)
        _stream, packets = self.login(server, create=True)
        role_info = next(
            p for p in packets if p.spec.protocol_id == ROLE_INFO_ID
        )
        self.assertEqual(int(role_info.fields[4]), 1)
        self.assertNotIn(
            WEEK_AND_OPEN_ID, [p.spec.protocol_id for p in packets]
        )
        refused = self._wait_events(
            events, "tcp_dev_play_artifact_overlay_refused", 1
        )[0]
        self.assertEqual(refused["reason"], dpa.REASON_META_INVALID_JSON)
        self.assertFalse(refused["applied"])


class DevPlayArtifactOpenUiTests(_EnvRoots):
    """C2S 744 and its two answers, positive and negative."""

    def _dev_profile_with_overlay(self) -> RoleStore:
        self.create_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        code, _ = self.run_cli(
            ["seed", "--profile", dpa.DEV_PLAY_ARTIFACT_PROFILE_ID]
        )
        self.assertEqual(code, 0)
        store = self.open_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        self.addCleanup(store.close)
        return store

    def _open_ui(self, stream, buffer, reader, writer, fields=(0,)):
        # Consume any tail of the login segment (S2C 555/1435 arrive after the
        # terminal S2C 283) so the returned list is exactly the answer.
        #
        # TASK-0270: the reply now continues past 1527 with the tower entry plan and ends
        # with S2C 1529, so the helper reads through 1529 and the two entry packets stay
        # packets[0] and packets[1].
        self._drain(stream, buffer, reader)
        self._send(stream, writer, 744, fields)
        # TASK-0281: 744 answers 1526/1527 only; the tower entry plan moved to the team commit.
        # TASK-0282: 1551 artifactDgnAchInfo follows 1527 (it is the only writer of the entry
        # page score), so one reply is 1526, 1527, 1551.
        packets = self._read_through(stream, buffer, reader, ACH_INFO_ID)
        # TASK-0350: five node rows follow the path row (layers 2 and 3).
        for _ in range(4):
            packets += self._read_through(stream, buffer, reader, NODE_ID)
        packets += self._read_through(stream, buffer, reader, NODE_ID)
        # TASK-0285: the fabao push (45/46/47) then the team row (1538) ride with the reply as
        # read-only previews.
        packets += self._read_through(stream, buffer, reader, MAGIC_START_ID)
        packets += self._read_through(stream, buffer, reader, MAGIC_INFO_ID)
        packets += self._read_through(stream, buffer, reader, MAGIC_END_ID)
        packets += self._read_through(stream, buffer, reader, TEAM_ID)
        return packets

    def test_the_entry_request_is_answered_1526_then_1527(self) -> None:
        store = self._dev_profile_with_overlay()
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)

        packets = self._open_ui(stream, buffer, reader, writer)
        # TASK-0270 extended this same reply with the tower entry plan: the two entry
        # packets are still FIRST and in order, then the fabao push (45/46/47), the path,
        # three node rows, the team, and 1529 (which opens the tower) LAST.
        self.assertEqual(
            [packet.spec.protocol_id for packet in packets],
            [WEEK_AND_OPEN_ID, RACE_AND_TOWER_ID, ACH_INFO_ID, PATH_ID]
            + [NODE_ID] * NODE_ROWS
            + [MAGIC_START_ID, MAGIC_INFO_ID, MAGIC_END_ID, TEAM_ID],
        )
        # TASK-0281: the tower entry plan (45/46/47/1531/1530x3/1538/1529) is no longer part
        # of this reply -- it is sent by the team commit (C2S 745/746) so the panel stays
        # observable. Nothing else follows here, in particular none of the later edges.
        self.assertEqual(self._drain(stream, buffer, reader), [])

        received = self._wait_events(
            events, "tcp_artifact_dgn_open_ui_received", 1
        )[0]
        self.assertTrue(received["overlay_applied"])
        self.assertEqual(received["wire_format"], "N")
        self.assertEqual(received["request_fields"], [0])
        self.assertEqual(
            received["open_ui_argument_semantics"], "unknown_placeholder"
        )
        self.assertEqual(received["open_ui_unknown_id"], "U-1")
        self.assertFalse(received["repeated_request"])
        self.assertTrue(received["connection_kept_alive"])

        week_event = self._wait_events(
            events, "tcp_artifact_dgn_week_and_open_sent", 1
        )[0]
        self.assertEqual(week_event["wire_format"], "NNyyyyy")
        self.assertEqual(week_event["open_flag_width"], "y")
        self.assertTrue(week_event["open_flags_are_booleans"])
        self.assertEqual(
            week_event["week_formula"], "pkgCfgArtifactDgn.getCurWeek"
        )
        self.assertEqual(
            week_event["shop_week_formula"],
            "pkgCfgArtifactDgn.getCurWeek4Shop",
        )
        self.assertEqual(week_event["server_time_unix"], int(FIXED_UNIX))
        self.assertEqual(
            week_event["week_value_provenance"], "deterministic_recovered"
        )
        self.assertFalse(week_event["historical"])
        self.assertTrue(week_event["replaceable"])

        race_event = self._wait_events(
            events, "tcp_artifact_dgn_race_and_tower_sent", 1
        )[0]
        self.assertEqual(race_event["wire_format"], "NNNNNN")
        # TASK-0282: the dev-play pair is legal AND open this week (race 3 yin dian, tower 1).
        self.assertEqual(race_event["fields"], [3, 1, 0, 0, 0, 0])
        self.assertEqual(race_event["field_6"], 0)
        self.assertEqual(
            race_event["field_6_semantics"], "unknown_placeholder"
        )
        self.assertEqual(race_event["field_6_provenance"], "unknown")
        self.assertEqual(race_event["field_6_unknown_id"], "U-3")
        self.assertEqual(
            race_event["value_provenance"], "synthetic_compatibility"
        )
        self.assertEqual(
            race_event["packet_order"],
            ["artifactDgnSendWeekAndOpen", "artifactDgnSendRaceAndTower",
             "artifactDgnAchInfo"],
        )
        self.assertEqual(
            race_event["ordering_origin"], "synthetic_compatibility"
        )
        self.assertFalse(race_event["historical"])
        self.assertFalse(race_event["role_store_written"])

        # A second request is answered again, and the count is recorded.
        second = self._open_ui(stream, buffer, reader, writer)
        # TASK-0281: a repeated 744 answers the same two entry packets; the tower plan belongs
        # to the team commit and is not replayed here.
        self.assertEqual(
            [packet.spec.protocol_id for packet in second],
            [WEEK_AND_OPEN_ID, RACE_AND_TOWER_ID, ACH_INFO_ID, PATH_ID]
            + [NODE_ID] * NODE_ROWS
            + [MAGIC_START_ID, MAGIC_INFO_ID, MAGIC_END_ID, TEAM_ID],
        )
        self.assertEqual(self._drain(stream, buffer, reader), [])
        second_received = self._wait_events(
            events, "tcp_artifact_dgn_open_ui_received", 2
        )[1]
        self.assertEqual(second_received["requests_answered"], 2)
        self.assertTrue(second_received["repeated_request"])

    def test_the_week_and_open_values_follow_the_client_formula(self) -> None:
        contract = json.loads(ARTIFACT_CONTRACT.read_text(encoding="utf-8"))
        matrix = contract["config"]["relations"]["week"]["open_matrix"]
        for clock in (FIXED_UNIX, SECOND_UNIX):
            with self.subTest(clock=clock):
                store = RoleStore.in_memory(time_source=lambda: clock)
                store._profile_fields = {
                    "play_profile_id": dpa.DEV_PLAY_ARTIFACT_PROFILE_ID
                }
                self.addCleanup(store.close)
                dpa.seed_overlay(
                    store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID
                )
                server, _events = self.start_server(
                    store,
                    dev_play_artifact=True,
                    clock=lambda: clock,
                )
                stream, buffer, reader, writer = self._login_keep_open(server)
                packets = self._open_ui(stream, buffer, reader, writer)
                # TASK-0270 appended the tower entry plan after the two entry packets; this
                # test only pins the week/open derivation, which is still packets[0].
                week_packet, race_packet = packets[0], packets[1]

                week, shop_week = dpa.get_cur_week(clock)[0], dpa.get_cur_week4_shop(clock)[0]
                self.assertEqual(week_packet.fields[0], week)
                self.assertEqual(week_packet.fields[1], shop_week)
                self.assertNotEqual(week, 1)  # a hardcoded week 1 cannot pass
                open_flags = week_packet.fields[2:]
                self.assertEqual(len(open_flags), 5)
                for flag in open_flags:
                    self.assertIsInstance(flag, bool)
                self.assertEqual(
                    open_flags, dpa.week_open_flags(week)
                )
                self.assertEqual(
                    [1 if flag else 0 for flag in open_flags],
                    [int(value) for value in matrix[str(week)]],
                )
                self.assertEqual(
                    race_packet.fields, (3, 1, 0, 0, 0, 0)
                )
                # TASK-0283: the path/node rows ride with this reply, so the wire is already
                # exhausted here -- nothing else may follow.
                self.assertEqual(self._drain(stream, buffer, reader), [])

    def test_a_session_without_the_overlay_records_and_keeps_the_connection(
        self,
    ) -> None:
        cases = {
            "flag_off": dict(
                profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID,
                seeded=True,
                flag=False,
            ),
            "meta_absent": dict(
                profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID,
                seeded=False,
                flag=True,
            ),
            "profile_mismatch": dict(
                profile_id="qa-changan-main", seeded=True, flag=True
            ),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                store = RoleStore.in_memory(time_source=self._clock)
                store._profile_fields = {
                    "play_profile_id": kwargs["profile_id"]
                }
                self.addCleanup(store.close)
                if kwargs["seeded"]:
                    dpa.seed_overlay(
                        store, profile_id=dpa.DEV_PLAY_ARTIFACT_PROFILE_ID
                    )
                server, events = self.start_server(
                    store, dev_play_artifact=kwargs["flag"]
                )
                stream, buffer, reader, writer = self._login_keep_open(server)
                self._drain(stream, buffer, reader)
                before_roles = store.count()
                before_meta = store.get_meta(dpa.DEV_PLAY_ARTIFACT_META_KEY)
                self.assertEqual(before_roles, 1)
                self._send(stream, writer, 744, (0,))
                # The client's own month-sign request proves the socket is
                # still being read and answered after the record-only path.
                self._send(stream, writer, 725, ())
                packets = self._read_through(
                    stream, buffer, reader, MONTH_SIGN_ACK_ID
                )
                artifact_ids = [
                    packet.spec.protocol_id
                    for packet in packets
                    if packet.spec.protocol_id in
                    (WEEK_AND_OPEN_ID, RACE_AND_TOWER_ID)
                ]
                self.assertEqual(artifact_ids, [])
                for unimplemented in UNIMPLEMENTED_ARTIFACT_S2C:
                    self.assertNotIn(
                        unimplemented,
                        [packet.spec.protocol_id for packet in packets],
                    )
                recorded = self._wait_events(
                    events, "tcp_artifact_dgn_open_ui_recorded", 1
                )[0]
                self.assertFalse(recorded["response_sent"])
                self.assertEqual(
                    recorded["reason"],
                    "dev_play_artifact_overlay_not_applied",
                )
                self.assertTrue(recorded["connection_kept_alive"])
                self.assertFalse(any(
                    event.get("event") == "tcp_error" for event in events
                ))
                self.assertFalse(any(
                    event.get("event")
                    == "tcp_artifact_dgn_week_and_open_sent"
                    for event in events
                ))
                # No archive write of any kind: the role count and the overlay
                # meta row are exactly what they were before the request.
                self.assertEqual(store.count(), before_roles)
                self.assertEqual(
                    store.get_meta(dpa.DEV_PLAY_ARTIFACT_META_KEY),
                    before_meta,
                )

    def test_a_broken_overlay_meta_records_without_answer(self) -> None:
        self.create_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        store = self.open_profile(dpa.DEV_PLAY_ARTIFACT_PROFILE_ID)
        self.addCleanup(store.close)
        store.set_meta(dpa.DEV_PLAY_ARTIFACT_META_KEY, '{"format": "wrong"}')
        server, events = self.start_server(store, dev_play_artifact=True)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._send(stream, writer, 744, (0,))
        self._send(stream, writer, 725, ())
        packets = self._read_through(stream, buffer, reader, MONTH_SIGN_ACK_ID)
        self.assertNotIn(
            WEEK_AND_OPEN_ID,
            [packet.spec.protocol_id for packet in packets],
        )
        self.assertNotIn(
            RACE_AND_TOWER_ID,
            [packet.spec.protocol_id for packet in packets],
        )
        self._wait_events(
            events, "tcp_dev_play_artifact_overlay_refused", 1
        )
        recorded = self._wait_events(
            events, "tcp_artifact_dgn_open_ui_recorded", 1
        )[0]
        self.assertFalse(recorded["overlay_applied"])


class DevPlayArtifactSourceTests(unittest.TestCase):
    """The entry thresholds the overlay announces are the shipped rows."""

    def test_the_entry_levels_are_the_shipped_function_open_rows(self) -> None:
        contract = json.loads(ARTIFACT_CONTRACT.read_text(encoding="utf-8"))
        entry = contract["entry"]["function_open"]
        self.assertEqual(
            entry["artifact_dgn"]["openLevel"], dpa.ARTIFACT_DGN_OPEN_LEVEL
        )
        self.assertEqual(
            entry["equip_fight"]["openLevel"], dpa.EQUIP_FIGHT_OPEN_LEVEL
        )
        self.assertEqual(entry["artifact_dgn"]["id"], 43)
        self.assertEqual(entry["equip_fight"]["id"], 35)
        self.assertEqual(
            entry["artifact_dgn"]["macro"], dpa.ARTIFACT_DGN_MACRO
        )
        self.assertEqual(entry["artifact_dgn"]["mainUIPath"], "")
        self.assertEqual(contract["entry"]["reachable"], True)
        self.assertEqual(contract["dev_play"]["recommended_archive_id"],
                         "dev-artifact-main")

    def test_the_three_registrations_are_the_contract_rows(self) -> None:
        contract = json.loads(ARTIFACT_CONTRACT.read_text(encoding="utf-8"))
        protocols = contract["protocols"]
        for wire_id, direction in (
            ("744", "c2s"),
            ("1526", "s2c"),
            ("1527", "s2c"),
        ):
            row = protocols[direction][wire_id]
            with self.subTest(wire_id=wire_id, direction=direction):
                registry = (
                    C2S_REGISTRY if direction == "c2s" else S2C_REGISTRY
                )
                spec = registry.by_id(int(wire_id))
                self.assertEqual((spec.name, spec.fmt), (row["name"], row["format"]))
                # No member of this slice is a lock-send.
                self.assertFalse(row["lock_partner"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
