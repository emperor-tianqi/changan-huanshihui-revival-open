"""TASK-0245 / #27 -- the client's own team power over S2C 39.

The Owner's report is "a stronger team is still refused at the battle entry".
The recovered gate is `pkgChapterEventHandle.handleFight`'s
``tbCfg.fightLimitFight > 0 and pkgTeamDataMgr.getMaxFight() < tbCfg.fightLimitFight``
with the shipped text 战力需要达到{1}; ``le_dMaxFight`` starts at 0 and is only
written by the S2C 39 ``returnTeamMaxFight`` handler.  Profile v22 sends it.

These tests drive a real `RevivalTCPServer` over a real socket and assert:

* the frozen formula/digest (`revival_stub.team_power`);
* the login value for the RUN-0077 team (2836) and for an empty preset (100);
* the packet position (right after S2C 35) and the v21 refusal;
* the C2S 6 re-send (new value, repeat is the same value);
* the C2S 5 re-send for the newly current (empty) preset;
* the archive rows are not rewritten by any of it.
"""

from __future__ import annotations

import socket
import threading
import unittest
from pathlib import Path

from revival_stub import team_power
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import (
    PROFILE_ID_V21,
    PROFILE_ID_V22,
    PROFILE_ID_V23,
    PROFILE_ID_V24,
    get_profile,
    get_profile_v21,
    get_profile_v22,
)
from revival_stub.eventlog import EventLogger
from revival_stub.registry import C2S_REGISTRY
from revival_stub.role_store import RoleStore
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer


SERVER_STARTED_UNIX = 1_700_000_000
ACCOUNT_KEY = dict(
    server_id=1.0,
    platform="android",
    channel_id="local-channel",
    channel_sub_id="0",
    sdk_identity="local-user",
)
CARD_ORIGIN = "revival_local_recovered_fight_reward"

#: The three cards of the RUN-0077 team, decoded from the S2C 30 rows.
RUN_0077_CARDS = (
    ("revival-draw-0011-04", 4502, 1, 0),
    ("revival-draw-0012-02", 4526, 7, 0),
    ("revival-draw-0023-03", 4437, 1, 0),
)
RUN_0077_IDS = tuple(row[0] for row in RUN_0077_CARDS)
RUN_0077_POWER = 2836
EMPTY_TEAM_POWER = 100  # soul 101's own fight


def _read_until(stream, server, buffer, state, packets, protocol_id: int) -> None:
    while not any(packet.spec.protocol_id == protocol_id for packet in packets):
        try:
            packet = decode_packet(buffer, server.s2c_registry, state)
        except NeedMoreData:
            chunk = stream.recv(65536)
            if not chunk:
                raise AssertionError(
                    f"server closed before S2C {protocol_id}: "
                    f"{[p.spec.protocol_id for p in packets]}"
                )
            buffer.extend(chunk)
            continue
        del buffer[: packet.consumed]
        packets.append(packet)


class TeamPowerFormulaTests(unittest.TestCase):
    def test_the_frozen_digest_and_inputs(self) -> None:
        # The analyzer prints this digest; the two independent processes agree
        # (max_decoder/analyze_team_max_fight.py --check).
        self.assertEqual(
            team_power.digest(),
            "1bc3165e314c3adb37b6ab7cd3819623dddc7b4882178433b81523825ca3d53c",
        )
        self.assertEqual(team_power.TEAM_POWER_DIGEST, team_power.digest())
        self.assertEqual(len(team_power.CARD_BASE_FIGHT), 354)
        self.assertEqual(team_power.LEVEL_RATIO[0], 1.0)
        self.assertEqual(team_power.LEVEL_RATIO[7], 1.34)
        self.assertEqual(team_power.ADVANCE_RATIO, {1: 1.74, 2: 1.82, 3: 1.9})
        self.assertEqual(team_power.ACTIVE_SOUL_FIGHT, {101: 100})

    def test_the_client_formula_on_the_run_0077_cards(self) -> None:
        # getCardFight = floor(cfg.fight * ratio(level|advN)) -- no equipment.
        self.assertEqual(team_power.card_fight(4502, 1, 0), 918)
        self.assertEqual(team_power.card_fight(4526, 7, 0), 1206)
        self.assertEqual(team_power.card_fight(4437, 1, 0), 612)
        # An advance level wins over the plain level (adv1 = 1.74).
        self.assertEqual(team_power.card_fight(4502, 1, 1), 1566)
        self.assertEqual(
            sum(
                team_power.card_fight(cfg, level, advance)
                for _id, cfg, level, advance in RUN_0077_CARDS
            )
            + team_power.ACTIVE_SOUL_FIGHT[101],
            RUN_0077_POWER,
        )

    def test_unreproducible_inputs_are_refused(self) -> None:
        with self.assertRaises(team_power.TeamPowerError):
            team_power.card_fight(999999, 1, 0)
        with self.assertRaises(team_power.TeamPowerError):
            team_power.card_fight(4502, 11, 0)
        with self.assertRaises(team_power.TeamPowerError):
            team_power.card_fight(4502, 1, 4)
        with self.assertRaises(team_power.TeamPowerError):
            team_power.team_power(card_ids=(), card_rows={}, soul_id=999)

        class Row:
            card_cfg_id = 4502
            level = 1
            advance_level = 0
            equip_id1 = "some-equip"
            equip_id2 = ""
            equip_id3 = ""
            equip_id4 = ""
            god_weapon_id = ""

        with self.assertRaises(team_power.TeamPowerError):
            team_power.team_power(
                card_ids=("x",), card_rows={"x": Row()}, soul_id=101
            )


class _Server:
    """A pre-seeded role + preset, then a real login over a socket."""

    def __init__(self, *, cards=RUN_0077_CARDS, team=True, profile=None) -> None:
        self.events: list[dict] = []
        self.store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=self.store,
            compatibility_profile=(
                profile if profile is not None else get_profile()
            ),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.key = self.store.account_key(**ACCOUNT_KEY)
        self.record, _ = self.store.create_or_get(
            account_key=self.key,
            server_id=1.0,
            role_name="战力道友",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id=self.server.compatibility_profile.profile_id,
        )
        for instance_id, cfg_id, level, advance in cards:
            self.store.record_card_instance(
                record=self.record,
                instance_id=instance_id,
                card_cfg_id=cfg_id,
                level=level,
                advance_level=advance,
                origin=CARD_ORIGIN,
            )
        if team:
            self.store.record_team_preset(
                record=self.record,
                group_id=1,
                equip_ids=("", "", "", ""),
                god_weapon_id="",
                card_ids=RUN_0077_IDS + ("",) * 8,
            )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()

    def login(self):
        stream = socket.create_connection(
            ("127.0.0.1", self.server.server_address[1]), timeout=3
        )
        stream.settimeout(3)
        writer = WriterState()
        reader = ReaderState()
        buffer = bytearray()
        packets: list = []
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer).wire
        )
        _read_until(stream, self.server, buffer, reader, packets, 9)
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
        _read_until(stream, self.server, buffer, reader, packets, 283)
        return stream, writer, reader, buffer, packets

    def values(self, packets) -> list[int]:
        return [
            packet.fields[0]
            for packet in packets
            if packet.spec.protocol_id == 39
        ]

    def wait_event(self, name: str, *, trigger: str | None = None,
                   timeout: float = 2.0) -> dict:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in reversed(self.events):
                if event.get("event") != name:
                    continue
                if trigger is None or event.get("trigger") == trigger:
                    return event
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} trigger={trigger!r} not emitted")


class TeamMaxFightLoginTests(unittest.TestCase):
    def test_the_login_value_is_the_client_own_team_power(self) -> None:
        server = _Server()
        stream = None
        try:
            stream, _writer, _reader, _buffer, packets = server.login()
            ids = [packet.spec.protocol_id for packet in packets]
            self.assertEqual(ids.count(39), 1)
            self.assertEqual(ids[ids.index(35) + 1], 39)
            self.assertEqual(server.values(packets), [RUN_0077_POWER])
            event = server.wait_event(
                "tcp_team_max_fight_sent", trigger="login"
            )
            self.assertEqual(event["trigger"], "login")
            self.assertEqual(event["group_id"], 1)
            self.assertEqual(event["value"], RUN_0077_POWER)
            self.assertEqual(
                event["team_power_digest"], team_power.TEAM_POWER_DIGEST
            )
            self.assertEqual(event["value_provenance"], "official_recovered")
            self.assertEqual(event["rule_provenance"], "deterministic_recovered")
            self.assertEqual(event["ordering_origin"], "synthetic_compatibility")
            self.assertFalse(event["historical_values_recovered"])
            self.assertFalse(event["state_changed"])
        finally:
            if stream is not None:
                stream.close()
            server.close()

    def test_an_empty_preset_sends_the_soul_fight_only(self) -> None:
        server = _Server(team=False)
        stream = None
        try:
            stream, *_rest, packets = server.login()
            self.assertEqual(server.values(packets), [EMPTY_TEAM_POWER])
        finally:
            if stream is not None:
                stream.close()
            server.close()

    def test_a_v21_server_sends_no_s2c39(self) -> None:
        server = _Server(profile=get_profile_v21())
        stream = None
        try:
            stream, *_rest, packets = server.login()
            self.assertEqual(server.values(packets), [])
            self.assertFalse(any(
                event.get("event") == "tcp_team_max_fight_sent"
                for event in server.events
            ))
        finally:
            if stream is not None:
                stream.close()
            server.close()

    def test_only_v22_and_its_later_successors_carry_the_policy(self) -> None:
        self.assertIsNone(get_profile_v21().team_max_fight)
        self.assertEqual(get_profile().profile_id, PROFILE_ID_V24)
        self.assertEqual(get_profile().parent_profile_id, PROFILE_ID_V23)
        self.assertIsNotNone(get_profile().team_max_fight)
        self.assertIs(get_profile().team_max_fight, get_profile_v22().team_max_fight)


class TeamMaxFightUpdateTests(unittest.TestCase):
    def test_update_team_resends_the_new_value_and_repeats_it(self) -> None:
        server = _Server()
        stream = None
        try:
            stream, writer, reader, buffer, packets = server.login()
            one_card = (RUN_0077_IDS[0],) + ("",) * 9
            for _ in range(2):
                packets.clear()
                stream.sendall(
                    encode_packet(
                        C2S_REGISTRY.by_id(6), (1,) + ("",) * 4 + one_card,
                        writer,
                    ).wire
                )
                _read_until(stream, server.server, buffer, reader, packets, 40)
                _read_until(stream, server.server, buffer, reader, packets, 39)
                # The value is the soul plus the one surviving card (4502 L1).
                self.assertEqual(server.values(packets), [100 + 918])
        finally:
            if stream is not None:
                stream.close()
            server.close()

    def test_change_team_group_resends_for_the_new_current_preset(self) -> None:
        server = _Server()
        stream = None
        try:
            stream, writer, reader, buffer, packets = server.login()
            packets.clear()
            stream.sendall(
                encode_packet(C2S_REGISTRY.by_id(5), (2,), writer).wire
            )
            _read_until(stream, server.server, buffer, reader, packets, 35)
            _read_until(stream, server.server, buffer, reader, packets, 39)
            # Preset 2 has no stored row: the client's own empty team.
            self.assertEqual(server.values(packets), [EMPTY_TEAM_POWER])
            event = server.wait_event(
                "tcp_team_max_fight_sent", trigger="changeTeamGroup"
            )
            self.assertEqual(event["trigger"], "changeTeamGroup")
            self.assertEqual(event["group_id"], 2)
        finally:
            if stream is not None:
                stream.close()
            server.close()

    def test_the_archive_rows_are_never_rewritten(self) -> None:
        server = _Server()
        stream = None
        try:
            stream, *_rest, packets = server.login()
            before = [
                (row.instance_id, row.card_cfg_id, row.level, row.advance_level)
                for row in server.store.card_instances(account_key=server.key)
            ]
            self.assertEqual(server.values(packets), [RUN_0077_POWER])
            after = [
                (row.instance_id, row.card_cfg_id, row.level, row.advance_level)
                for row in server.store.card_instances(account_key=server.key)
            ]
            self.assertEqual(before, after)
            self.assertEqual(
                server.store.team_preset(
                    account_key=server.key, group_id=1
                ).card_ids,
                RUN_0077_IDS + ("",) * 8,
            )
        finally:
            if stream is not None:
                stream.close()
            server.close()


if __name__ == "__main__":
    unittest.main()
