"""The 妖灵 loop's server side: stored cards, handbook rows, team presets.

Three contracts are pinned here, each against the client's own handlers:

* a recovered chapter-fight reward (``guankaMapFight.fightRewardId`` 201 ->
  KAPAI 1103) is granted exactly once, pushed to a live client as the full
  21-value ``returnCardInfo`` row, and announced through the handbook's
  ``returnUpdateCardDex`` new-card event;
* the same card replays on an ordinary login (no draw-test overlay) as
  S2C 29/30/31, and its template lights exactly one handbook row;
* a team edit from the client's own C2S 6 is validated against owned cards,
  persisted, answered with the lock-partner S2C 40, and replayed as S2C 37 on
  the next login.  An unowned card is refused with S2C 41 and writes nothing.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest

from revival_stub.card_templates import (
    CARD_TEMPLATE_IDS,
    TEMPLATE_DIGEST,
    template_id_for,
)
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.eventlog import EventLogger
from revival_stub.owner_draw_test import (
    DETERMINISTIC_FIXTURE_KIND,
    DETERMINISTIC_FIXTURE_ORIGIN,
    FIXTURE_CARD_CFG_ID,
    FIXTURE_CARD_SFM_ID1,
    FIXTURE_CARD_SFM_ID2,
    OWNER_DRAW_TEST_PROFILE_ID,
    OwnerDrawTestGrant,
)
from revival_stub.recovered_rewards import (
    FIGHT_REWARD_101010,
    FIGHT_REWARD_DIGEST,
    reward_for_fight,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import (
    CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
    RoleStore,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.tcp_stub import RevivalTCPServer
from tests.test_codec import BATTLE_GAME_OVER_RUN0048_FIELDS, BATTLE_START_FIELDS

SERVER_STARTED_UNIX = 1_700_000_000

#: The login identity `DEFAULT_LOGIN_FIELDS` represents.
ACCOUNT_KEY = dict(
    server_id=1.0,
    platform="android",
    channel_id="local-channel",
    channel_sub_id="0",
    sdk_identity="local-user",
)


class FrozenRewardTests(unittest.TestCase):
    def test_card_template_digest_is_frozen(self) -> None:
        self.assertEqual(len(CARD_TEMPLATE_IDS), 350)
        self.assertEqual(
            TEMPLATE_DIGEST,
            "4483c2be3644375fc2626fd119135a75b745341170087030c7d892ec163797ed",
        )
        self.assertEqual(template_id_for(1103), 1103)
        self.assertEqual(template_id_for(4301), 3301)
        self.assertEqual(template_id_for(5531), 4531)
        with self.assertRaises(KeyError):
            template_id_for(33333)

    def test_fight_reward_is_frozen_client_config(self) -> None:
        self.assertEqual(
            FIGHT_REWARD_DIGEST,
            "7b660d8177fae1605707af32efbc6d6954ad3b86d9b94b309aaacbbe20282616",
        )
        reward = reward_for_fight(101010)
        self.assertIs(reward, FIGHT_REWARD_101010)
        self.assertEqual(reward.card_id, 1103)
        self.assertEqual(reward.sfm_id1, 30104)
        self.assertEqual(reward.sfm_id2, 30144)
        self.assertEqual(reward.instance_id, "revival-reward-101010-01")
        self.assertIsNone(reward_for_fight(999999))


class _Server:
    def __init__(
        self, *, grant=None, draw_odds="deterministic_fixture", profile_id=None
    ) -> None:
        self.events: list[dict] = []
        self.store = RoleStore.in_memory(time_source=lambda: SERVER_STARTED_UNIX)
        if profile_id is not None:
            # The Owner draw-test overlay is only active for its own named
            # play profile (the same gate `tcp_stub.RevivalTCPServer` applies).
            self.store._profile_fields = {"play_profile_id": profile_id}
        self.server = RevivalTCPServer(
            ("127.0.0.1", 0),
            EventLogger(stream=None, sink=self.events.append),
            time_source=lambda: SERVER_STARTED_UNIX,
            role_store=self.store,
            test_grant=grant,
            draw_odds=draw_odds,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()

    def wait_event(self, name: str, timeout: float = 2.0) -> dict:
        return self.wait_events(name, 1, timeout)[0]

    def wait_events(self, name: str, count: int, timeout: float = 2.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [
                item for item in self.events if item.get("event") == name
            ]
            if len(matches) >= count:
                return matches
            time.sleep(0.01)
        raise AssertionError(f"event {name!r} #{count} was not emitted")

    def seed_role_and_clear(self):
        key = self.store.account_key(**ACCOUNT_KEY)
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="奖励回填",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id="cn-0.1.20.1-revival-v15",
        )
        self.store.record_chapter_fight_clear(
            record=record,
            chapter_id=1,
            fight_id=101010,
            map_index=2,
            observed_at=SERVER_STARTED_UNIX,
            request_sequence=1,
            settlement_sequence=2,
            reply_sequence=3,
        )
        return key, record

    def seed_role_and_card(self):
        key = self.store.account_key(**ACCOUNT_KEY)
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="妖灵闭环",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id="cn-0.1.20.1-revival-v15",
        )
        row, _ = self.store.record_card_instance(
            record=record,
            instance_id=FIGHT_REWARD_101010.instance_id,
            card_cfg_id=FIGHT_REWARD_101010.card_id,
            level=1,
            sfm_id1=FIGHT_REWARD_101010.sfm_id1,
            sfm_id2=FIGHT_REWARD_101010.sfm_id2,
            origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
        )
        return key, record, row

    def connect(self, *, create: bool):
        writer = WriterState()
        reader = ReaderState()
        payload = bytearray(
            encode_packet(
                C2S_REGISTRY.by_id(0), DEFAULT_LOGIN_FIELDS, writer
            ).wire
        )
        if create:
            payload.extend(
                encode_packet(
                    C2S_REGISTRY.by_id(2), (b"LoopRole", 1), writer
                ).wire
            )
        payload.extend(
            encode_packet(C2S_REGISTRY.by_id(3), (1, False), writer).wire
        )
        stream = socket.create_connection(
            ("127.0.0.1", self.server.server_address[1]), timeout=2
        )
        stream.settimeout(2)
        stream.sendall(bytes(payload))
        return stream, writer, reader, bytearray()

    @staticmethod
    def send(stream, writer, protocol_id, fields):
        stream.sendall(
            encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, writer).wire
        )

    @staticmethod
    def read_until(stream, buffer, reader, protocol_id, timeout=3.0):
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


class FightRewardLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server(grant=None)
        self.addCleanup(self.server.close)

    def _settle_first_fight(self) -> list:
        stream, writer, reader, buffer = self.server.connect(create=True)
        try:
            responses = self.server.read_until(stream, buffer, reader, 27)
            self.server.send(stream, writer, 69, BATTLE_START_FIELDS)
            responses += self.server.read_until(stream, buffer, reader, 89)
            self.server.send(
                stream, writer, 67, BATTLE_GAME_OVER_RUN0048_FIELDS
            )
            responses += self.server.read_until(stream, buffer, reader, 751)
        finally:
            stream.close()
        return responses

    def test_first_settlement_grants_the_config_card_once_and_replays_it(self) -> None:
        responses = self._settle_first_fight()
        card_infos = [p for p in responses if p.spec.protocol_id == 30]
        self.assertTrue(
            any(p.fields[0] == b"revival-reward-101010-01" for p in card_infos),
            [p.fields for p in card_infos],
        )
        reward_info = next(
            p for p in card_infos if p.fields[0] == b"revival-reward-101010-01"
        )
        self.assertEqual(reward_info.fields[1], 1103)
        dex_rows = [p for p in responses if p.spec.protocol_id == 751]
        self.assertTrue(
            any(row.fields[0] == 1103 and row.fields[1] == 1103 for row in dex_rows),
            [row.fields for row in dex_rows],
        )
        self.assertTrue(
            any(row.fields[4] is True for row in dex_rows),
            "the freshly granted card must carry bNewCard=true",
        )
        event = self.server.wait_event("tcp_chapter_fight_reward_granted")
        self.assertEqual(event["fight_id"], 101010)
        self.assertEqual(event["reward_id"], 201)
        self.assertEqual(event["card_id"], 1103)
        self.assertTrue(event["win_loss_decoded"])
        self.assertTrue(event["historical_values_recovered"])

        key = self.server.store.account_key(**ACCOUNT_KEY)
        rows = self.server.store.card_instances(account_key=key)
        self.assertEqual(
            [row.instance_id for row in rows], ["revival-reward-101010-01"]
        )
        self.assertEqual(
            rows[0].origin, CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD
        )

        # A second settlement on a new connection cannot create a second card.
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            self.server.send(stream, writer, 69, BATTLE_START_FIELDS)
            self.server.read_until(stream, buffer, reader, 89)
            self.server.send(
                stream, writer, 67, BATTLE_GAME_OVER_RUN0048_FIELDS
            )
            self.server.read_until(stream, buffer, reader, 96)
            after_settlement = self.server.read_until(
                stream, buffer, reader, 751, timeout=0.5
            )
        finally:
            stream.close()
        self.assertFalse(
            [p for p in after_settlement if p.spec.protocol_id in (30, 751)]
        )
        self.assertEqual(
            [row.instance_id for row in self.server.store.card_instances(account_key=key)],
            ["revival-reward-101010-01"],
        )

        # An ordinary login (no overlay) replays the card and one handbook row.
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        replayed = [
            p
            for p in packets
            if p.spec.protocol_id == 30
            and p.fields[0] == b"revival-reward-101010-01"
        ]
        self.assertEqual(len(replayed), 1)
        dex = [
            p
            for p in packets
            if p.spec.protocol_id == 751 and p.fields[0] == 1103
        ]
        self.assertEqual(len(dex), 1)
        self.assertFalse(dex[0].fields[4])
        snapshot = self.server.wait_event("tcp_card_snapshot_sent")
        self.assertEqual(snapshot["rows_sent"], 1)
        self.assertEqual(snapshot["suppressed_fixture_rows"], 0)
        self.assertEqual(
            snapshot["origins"], [CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD]
        )


class FightRewardBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server(grant=None)
        self.addCleanup(self.server.close)
        self.key, _ = self.server.seed_role_and_clear()

    def test_existing_clear_backfills_once_at_login(self) -> None:
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        replayed = [
            p
            for p in packets
            if p.spec.protocol_id == 30
            and p.fields[0] == b"revival-reward-101010-01"
        ]
        self.assertEqual(len(replayed), 1)
        dex = [p for p in packets if p.spec.protocol_id == 751]
        self.assertTrue(any(row.fields[0] == 1103 and row.fields[4] for row in dex))
        event = self.server.wait_event("tcp_chapter_fight_reward_backfilled")
        self.assertEqual(event["fight_id"], 101010)
        self.assertEqual(event["reward_basis"], "existing_recorded_fight_clear")
        self.assertFalse(event["win_loss_decoded"])

        # A second login neither rewrites the row nor announces it again.
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        dex = [p for p in packets if p.spec.protocol_id == 751]
        self.assertTrue(any(row.fields[0] == 1103 for row in dex))
        self.assertFalse(any(row.fields[4] for row in dex))
        self.server.wait_events("tcp_card_snapshot_sent", 2)
        backfills = [
            item
            for item in self.server.events
            if item.get("event") == "tcp_chapter_fight_reward_backfilled"
        ]
        self.assertEqual(len(backfills), 1)
        rows = self.server.store.card_instances(account_key=self.key)
        self.assertEqual([row.instance_id for row in rows], ["revival-reward-101010-01"])


class TeamEditLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server(grant=None)
        self.addCleanup(self.server.close)
        self.key, self.record, self.card = self.server.seed_role_and_card()

    def _login(self) -> list:
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            packets = self.server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        return packets

    def test_team_edit_persists_replies_and_replays(self) -> None:
        packets = self._login()
        baseline = [p for p in packets if p.spec.protocol_id == 37]
        self.assertEqual(len(baseline), 1)
        self.assertEqual(baseline[0].fields[1:], (b"",) * 16)

        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            self.server.read_until(stream, buffer, reader, 283)
            card_id = self.card.instance_id.encode()
            self.server.send(
                stream,
                writer,
                6,
                (1, b"", b"", b"", b"", card_id,
                 b"", b"", b"", b"", b"", b"", b"", b"", b""),
            )
            responses = self.server.read_until(stream, buffer, reader, 40)
        finally:
            stream.close()
        update = next(p for p in responses if p.spec.protocol_id == 40)
        self.assertEqual(update.fields[0], 1)
        self.assertEqual(update.fields[6], card_id)
        event = self.server.wait_event("tcp_team_update_recorded")
        self.assertTrue(event["recorded"])
        self.assertEqual(event["team_card_ids"][0], self.card.instance_id)

        stored = self.server.store.team_preset(account_key=self.key, group_id=1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.card_ids[0], self.card.instance_id)

        # A fresh login replays the saved preset through S2C 37.
        packets = self._login()
        team_info = [p for p in packets if p.spec.protocol_id == 37]
        self.assertEqual(len(team_info), 1)
        self.assertEqual(team_info[0].fields[0], 1)
        self.assertEqual(team_info[0].fields[6], card_id)

    def test_unowned_card_is_refused_with_the_client_error_partner(self) -> None:
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            self.server.read_until(stream, buffer, reader, 283)
            self.server.send(
                stream,
                writer,
                6,
                (1, b"", b"", b"", b"", b"not-owned",
                 b"", b"", b"", b"", b"", b"", b"", b"", b""),
            )
            responses = self.server.read_until(stream, buffer, reader, 41)
        finally:
            stream.close()
        self.assertTrue(any(p.spec.protocol_id == 41 for p in responses))
        event = self.server.wait_event("tcp_team_update_refused")
        self.assertEqual(event["reason"], "store_refused")
        self.assertIsNone(
            self.server.store.team_preset(account_key=self.key, group_id=1)
        )

    def test_change_group_persists_and_answers_the_lock_partner(self) -> None:
        stream, writer, reader, buffer = self.server.connect(create=False)
        try:
            self.server.read_until(stream, buffer, reader, 283)
            self.server.send(stream, writer, 5, (2,))
            responses = self.server.read_until(stream, buffer, reader, 35)
        finally:
            stream.close()
        group = next(p for p in responses if p.spec.protocol_id == 35)
        self.assertEqual(group.fields, (2,))
        # The persist runs on the connection thread, so a loaded machine can observe the reply
        # before the row lands; poll briefly instead of racing it.
        deadline = time.monotonic() + 3.0
        state = self.server.store.team_state(account_key=self.key)
        while state is None and time.monotonic() < deadline:
            time.sleep(0.05)
            state = self.server.store.team_state(account_key=self.key)
        self.assertIsNotNone(state, "the group change never reached the archive")
        self.assertEqual(state.current_group, 2)
        self.server.wait_event("tcp_team_group_changed")


class TeamLoginFixtureProjectionTests(unittest.TestCase):
    """TASK-0190: S2C 37 may only name card instances this login sent.

    RUN-0061 stopped at ``pkgMainUIMgr.lua:1130 init`` because the strict
    login suppressed the deterministic fixture rows from S2C 30 while the
    stored preset still named them in S2C 37; the client's ``getCardCfgID``
    then indexed nil before ``initBtn``.  These tests pin the projection rule
    and the stored-preset evidence that must survive it.
    """

    #: S2C 37: one group id, four equipment slots, one god-weapon slot, then
    #: the eleven card slots.
    CARD_SLOT_START = 6
    FIXTURE_INSTANCE_ID = "revival-fixture-draw-0007-10"

    def _make_server(self, *, grant) -> _Server:
        server = _Server(
            grant=grant,
            profile_id=(
                OWNER_DRAW_TEST_PROFILE_ID if grant is not None else None
            ),
        )
        self.addCleanup(server.close)
        return server

    def _seed_two_cards_and_preset(self, server: _Server):
        key = server.store.account_key(**ACCOUNT_KEY)
        record, _ = server.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="登录投影",
            career=1,
            create_time=SERVER_STARTED_UNIX,
            compatibility_profile_id="cn-0.1.20.1-revival-v15",
        )
        real, _ = server.store.record_card_instance(
            record=record,
            instance_id=FIGHT_REWARD_101010.instance_id,
            card_cfg_id=FIGHT_REWARD_101010.card_id,
            level=1,
            sfm_id1=FIGHT_REWARD_101010.sfm_id1,
            sfm_id2=FIGHT_REWARD_101010.sfm_id2,
            origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
        )
        fixture, _ = server.store.record_card_instance(
            record=record,
            instance_id=self.FIXTURE_INSTANCE_ID,
            card_cfg_id=FIXTURE_CARD_CFG_ID,
            level=1,
            sfm_id1=FIXTURE_CARD_SFM_ID1,
            sfm_id2=FIXTURE_CARD_SFM_ID2,
            origin=DETERMINISTIC_FIXTURE_ORIGIN,
            fixture_id="drawcard-result-0007",
            fixture_kind=DETERMINISTIC_FIXTURE_KIND,
        )
        server.store.record_team_preset(
            record=record,
            group_id=1,
            equip_ids=("", "", "", ""),
            god_weapon_id="",
            card_ids=(real.instance_id, fixture.instance_id) + ("",) * 9,
        )
        return key, record, real, fixture

    def _login(self, server: _Server) -> list:
        stream, writer, reader, buffer = server.connect(create=False)
        try:
            packets = server.read_until(stream, buffer, reader, 283)
        finally:
            stream.close()
        return packets

    @staticmethod
    def _team_info(packets) -> object:
        rows = [p for p in packets if p.spec.protocol_id == 37]
        assert len(rows) == 1, [p.fields for p in rows]
        return rows[0]

    @staticmethod
    def _team_record_event(server: _Server) -> dict:
        events = server.wait_events("tcp_team_snapshot_sent", 4)
        return next(
            event
            for event in events
            if event["snapshot_section"] == "team_preset_record"
        )

    def test_strict_login_withholds_a_suppressed_fixture_slot(self) -> None:
        server = self._make_server(grant=None)
        key, _, real, fixture = self._seed_two_cards_and_preset(server)

        packets = self._login(server)

        # S2C 30 carries only the recovered reward card.
        card_ids = {
            p.fields[0] for p in packets if p.spec.protocol_id == 30
        }
        self.assertEqual(card_ids, {real.instance_id.encode()})

        # S2C 37 keeps the real slot and uses the client's empty literal in
        # place of the fixture slot; the packet shape is unchanged.
        team_info = self._team_info(packets)
        self.assertEqual(len(team_info.fields), 17)
        self.assertEqual(
            team_info.fields[self.CARD_SLOT_START], real.instance_id.encode()
        )
        self.assertEqual(
            team_info.fields[self.CARD_SLOT_START + 1], b""
        )

        event = self._team_record_event(server)
        self.assertEqual(len(event["team_card_ids_sent"]), 11)
        self.assertEqual(event["team_card_ids_sent"][0], real.instance_id)
        self.assertEqual(event["team_card_ids_sent"][1], "")
        self.assertEqual(event["team_card_ids_withheld"], [fixture.instance_id])
        self.assertEqual(
            event["card_projection_policy"],
            "suppressed_deterministic_fixtures_excluded",
        )
        self.assertEqual(
            event["card_projection_origin"], "synthetic_compatibility"
        )
        self.assertIs(event["replaceable"], True)
        # The event still carries the STORED preset as evidence.
        self.assertEqual(
            event["team_card_ids"][:2],
            [real.instance_id, fixture.instance_id],
        )
        self.assertIs(event["historical_values_recovered"], False)

        # The stored preset is untouched by the projection.
        stored = server.store.team_preset(account_key=key, group_id=1)
        self.assertEqual(
            stored.card_ids[:2], (real.instance_id, fixture.instance_id)
        )

        snapshot = server.wait_event("tcp_card_snapshot_sent")
        self.assertEqual(snapshot["suppressed_fixture_rows"], 1)
        self.assertEqual(snapshot["instance_ids"], [real.instance_id])
        self.assertFalse(
            [
                item
                for item in server.events
                if item.get("event") == "tcp_team_snapshot_unresolved_card"
            ]
        )

    def test_grant_login_replays_both_slots_unchanged(self) -> None:
        server = self._make_server(grant=OwnerDrawTestGrant())
        key, _, real, fixture = self._seed_two_cards_and_preset(server)

        packets = self._login(server)

        card_ids = {
            p.fields[0] for p in packets if p.spec.protocol_id == 30
        }
        self.assertEqual(
            card_ids,
            {real.instance_id.encode(), fixture.instance_id.encode()},
        )
        team_info = self._team_info(packets)
        self.assertEqual(
            team_info.fields[self.CARD_SLOT_START], real.instance_id.encode()
        )
        self.assertEqual(
            team_info.fields[self.CARD_SLOT_START + 1],
            fixture.instance_id.encode(),
        )

        event = self._team_record_event(server)
        self.assertEqual(
            event["team_card_ids_sent"][:2],
            [real.instance_id, fixture.instance_id],
        )
        self.assertEqual(event["team_card_ids_withheld"], [])
        self.assertEqual(
            event["card_projection_policy"],
            "suppressed_deterministic_fixtures_excluded",
        )
        stored = server.store.team_preset(account_key=key, group_id=1)
        self.assertEqual(
            stored.card_ids[:2], (real.instance_id, fixture.instance_id)
        )

    def test_an_unresolved_slot_is_kept_and_named(self) -> None:
        server = self._make_server(grant=None)
        key, record, real, fixture = self._seed_two_cards_and_preset(server)
        # The preset outlives the card row: neither visible nor suppressed.
        deleted = server.store.delete_card_instances(
            record=record, instance_ids=(real.instance_id,)
        )
        self.assertEqual(deleted, 1)

        packets = self._login(server)

        # The only remaining stored card is the suppressed fixture, so the
        # card snapshot sends nothing at all (an empty set is recorded first).
        self.assertFalse(
            [p for p in packets if p.spec.protocol_id == 30]
        )
        team_info = self._team_info(packets)
        # The unknown slot keeps its stored value -- never silently cleared.
        self.assertEqual(
            team_info.fields[self.CARD_SLOT_START], real.instance_id.encode()
        )
        self.assertEqual(
            team_info.fields[self.CARD_SLOT_START + 1], b""
        )

        event = server.wait_event("tcp_team_snapshot_unresolved_card")
        self.assertEqual(event["instance_id"], real.instance_id)
        self.assertEqual(event["group_id"], 1)
        self.assertEqual(event["snapshot_section"], "team_preset_record")
        self.assertEqual(event["card_slot_index"], 0)
        self.assertIs(event["historical_values_recovered"], False)

        team_event = self._team_record_event(server)
        self.assertEqual(
            team_event["team_card_ids_sent"][0], real.instance_id
        )
        self.assertEqual(
            team_event["team_card_ids_withheld"], [fixture.instance_id]
        )
        self.assertEqual(
            team_event["team_card_ids"][:2],
            [real.instance_id, fixture.instance_id],
        )
        stored = server.store.team_preset(account_key=key, group_id=1)
        self.assertEqual(
            stored.card_ids[:2], (real.instance_id, fixture.instance_id)
        )


if __name__ == "__main__":
    unittest.main()
