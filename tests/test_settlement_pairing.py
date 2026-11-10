"""TASK-0287 -- the settlement pairing for the three dungeon battle types.

C2S 67 `battlePveGameOver` names no fight: it only carries the client's own
`pveOrPvpType` and the packed result.  The identity comes from the C2S 69 that
this SAME connection was answered with, so the pairing is per connection and per
type:

* 86 chapter map  -> `chapter_battle_start`, persisted (TASK-0174);
* 73 locktower    -> `babel_battle_start`, one S2C 580 push, no persistence
  (TASK-0238);
* 66 artifact     -> `dungeon_battle_start[66]`, event only (this task);
* 88 uncharted    -> `dungeon_battle_start[88]`, event only (this task).

The 66/88 tests drive the wire through the opt-in v20 profile (the only profile
whose S2C 89 echoes the requested type); the 73 test pins the unchanged babel
expectations.  Nothing here touches a device, an adb server or a RUN.
"""

from __future__ import annotations

import unittest

from revival_stub import dev_play_babel as dpb
from revival_stub import dev_play_uncharted as dpu
from revival_stub.compatibility_profile import (
    BATTLE_START_DUNGEON_PVE_TYPES,
    BATTLE_START_GOD_FIGHT_PVE_TYPE,
    BATTLE_START_LOCKTOWER_PVE_TYPE,
    BATTLE_START_PVE_OR_PVP_TYPE,
    BATTLE_START_RAW_RANDOM_SEED,
    BATTLE_START_UNCHARTED_PVE_TYPE,
)
from revival_stub.role_store import RoleStore
from tests.test_codec import BATTLE_GAME_OVER_RUN0048_FIELDS
from tests.test_dev_play_babel import _EnvRoots


BATTLE_START_REPLY_ID = 89
BATTLE_GAME_OVER_REPLY_ID = 96
BABEL_INFO_ID = 580


class SettlementPairingTests(_EnvRoots):
    """One C2S 69 answered with 66/88, then that connection's own C2S 67."""

    def _dev_server(self):
        self.create_profile(dpb.DEV_PLAY_BABEL_PROFILE_ID)
        code, _ = self.run_cli(
            ["seed", "--profile", dpb.DEV_PLAY_BABEL_PROFILE_ID]
        )
        self.assertEqual(code, 0)
        store = self.open_profile(dpb.DEV_PLAY_BABEL_PROFILE_ID)
        self.addCleanup(store.close)
        server, events = self.start_server(store, dev_play_babel=True)
        return store, server, events

    def _start(
        self, stream, buffer, reader, writer, pve_type, *,
        p1, p2=0, p3=0, p4=0,
    ):
        """Send one C2S 69 and return the S2C 89 the client actually got.

        p1..p4 are the identity slots of the recovered client call: for 66 the
        artifact call passes tower/layer/sn (p4 stays 0), for 88 the uncharted
        call passes unchartedId/layer/area/room.
        """
        fields = (
            False, 0, pve_type,
            float(p1), float(p2), float(p3), float(p4),
            0.0, 0.0, 0.0, 0.0, 0.0, b"",
        )
        self._send(stream, writer, 69, fields)
        return self._read_through(
            stream, buffer, reader, BATTLE_START_REPLY_ID
        )[-1]

    def _settle(self, stream, buffer, reader, writer, *, pve_type, sync_count):
        """Send the RUN-0048 C2S 67 vector under this type and read its unlock."""
        fields = list(BATTLE_GAME_OVER_RUN0048_FIELDS)
        fields[0] = pve_type
        fields[1] = sync_count
        self._send(stream, writer, 67, tuple(fields))
        return self._read_through(
            stream, buffer, reader, BATTLE_GAME_OVER_REPLY_ID
        )

    def _assert_no_persistence(self, store, *, reward_tables: bool = False) -> None:
        tables = ["chapter_fight_clears", "card_instances"]
        if reward_tables:
            tables += ["role_wallet", "card_special_inventory"]
        for table in tables:
            count = store._conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            self.assertEqual(count, 0, table)

    def test_the_artifact_type_66_pairs_with_its_own_start(self) -> None:
        store, server, events = self._dev_server()
        stream, buffer, reader, writer = self._login_keep_open(server)
        # The login segment can trail post-283 pushes (e.g. S2C 555); drain
        # them so every assertion below is about the battle edge alone.
        self._drain(stream, buffer, reader)
        battle_start = self._start(
            stream, buffer, reader, writer,
            BATTLE_START_GOD_FIGHT_PVE_TYPE,
            p1=1, p2=2, p3=1,
        )
        # The v20 reply echoes the requested type, so this is a real 66 battle.
        self.assertEqual(
            battle_start.fields[4], BATTLE_START_GOD_FIGHT_PVE_TYPE
        )
        sync_count = battle_start.fields[6]
        self.assertNotEqual(sync_count, 0)

        captured = self._wait_events(
            events, "tcp_dungeon_battle_start_captured", 1
        )[0]
        self.assertEqual(
            captured["pve_or_pvp_type"], BATTLE_START_GOD_FIGHT_PVE_TYPE
        )
        self.assertEqual((captured["tower"], captured["layer"], captured["sn"]),
                         (1, 2, 1))
        self.assertEqual(captured["s2c89_reply_sequence"],
                         battle_start.sequence)
        self.assertEqual(captured["s2c89_dSyncCount"], sync_count)
        self.assertFalse(captured["persisted"])
        self.assertFalse(captured["role_store_written"])
        self.assertFalse(captured["state_changed"])

        after = self._settle(
            stream, buffer, reader, writer,
            pve_type=BATTLE_START_GOD_FIGHT_PVE_TYPE,
            sync_count=sync_count,
        )
        self.assertEqual(
            [packet.spec.protocol_id for packet in after],
            [BATTLE_GAME_OVER_REPLY_ID],
        )
        # 66 has no state push (the S2C 580 is the locktower's own), but a WON settlement now
        # also pays the artifact reward, so the gain block 81 / 83 x3 / 82 follows 96, and
        # TASK-0348 then makes the next layer walkable with 1531 + two 1530 node rows.
        leftover = [packet.spec.protocol_id
                    for packet in self._drain(stream, buffer, reader)]
        self.assertTrue(all(pid in (81, 83, 82, 23, 503, 24, 1531, 1530) for pid in leftover),
                        leftover)
        self.assertEqual(leftover[:1], [81], "the gain block starts with 81")
        self.assertEqual(leftover[4:5], [82], "and ends with 82")
        # TASK-0376: the rows are now written where the archive can hold them, and each write
        # pushes its own container update: 23 money, 503 special card, 24 tower point.
        self.assertEqual(leftover[5:8], [23, 503, 24],
                         "the three typed pushes follow the display block")
        self.assertEqual(leftover[8:], [1531, 1530, 1530],
                         "the layer advance follows the reward")

        paired = self._wait_events(
            events, "tcp_dungeon_settlement_paired", 1
        )[0]
        self.assertEqual(
            paired["pve_or_pvp_type"], BATTLE_START_GOD_FIGHT_PVE_TYPE
        )
        self.assertEqual(
            paired["settlement_pve_or_pvp_type"],
            BATTLE_START_GOD_FIGHT_PVE_TYPE,
        )
        self.assertEqual((paired["tower"], paired["layer"], paired["sn"]),
                         (1, 2, 1))
        self.assertEqual(paired["request_sequence"],
                         captured["request_sequence"])
        self.assertEqual(paired["s2c89_dSyncCount"], sync_count)
        self.assertEqual(paired["dPackCheckFlag"], sync_count)
        self.assertTrue(paired["pack_check_matches_s2c89"])
        self.assertTrue(paired["win_loss_decoded"])
        self.assertTrue(paired["victory"])
        self.assertTrue(paired["outcome_recorded"])
        self.assertTrue(paired["paired"])
        self.assertEqual(paired["pairing_provenance"],
                         "deterministic_recovered")
        self.assertEqual(paired["persistence_policy"],
                         "no_persistence_in_this_slice")
        # The whole "no persistence in this slice" set the 73 path emits.
        for key in (
            "pushed", "victory_push_sent", "recorded", "persisted",
            "role_store_written", "state_changed",
            "chapter_fight_recorded", "reward_granted", "wallet_written",
            "card_written", "historical_values_recovered",
        ):
            self.assertFalse(paired[key], key)
        self.assertFalse(any(
            event.get("event") == "tcp_babel_settlement_paired"
            for event in events
        ))
        self._assert_no_persistence(store)
        # TASK-0376: the win pays the tower reward into the archive, so the wallet and the
        # special-card inventory must now hold the granted rows (7 x210 money, 8886 x1 cfg 300).
        money = store._conn.execute(
            "SELECT amount FROM role_wallet WHERE value_name = 'money'").fetchall()
        self.assertEqual([float(row[0]) for row in money], [210.0])
        special = store._conn.execute(
            "SELECT cfg_id, count FROM card_special_inventory").fetchall()
        self.assertEqual([tuple(row) for row in special], [(300, 1)])

    def test_the_uncharted_type_88_pairs_with_its_own_start(self) -> None:
        store, server, events = self._dev_server()
        stream, buffer, reader, writer = self._login_keep_open(server)
        # The login segment can trail post-283 pushes (e.g. S2C 555); drain
        # them so every assertion below is about the battle edge alone.
        self._drain(stream, buffer, reader)
        battle_start = self._start(
            stream, buffer, reader, writer,
            BATTLE_START_UNCHARTED_PVE_TYPE,
            p1=1, p2=1, p3=1, p4=1,
        )
        self.assertEqual(
            battle_start.fields[4], BATTLE_START_UNCHARTED_PVE_TYPE
        )
        sync_count = battle_start.fields[6]
        self.assertNotEqual(sync_count, 0)

        captured = self._wait_events(
            events, "tcp_dungeon_battle_start_captured", 1
        )[0]
        self.assertEqual(
            captured["pve_or_pvp_type"], BATTLE_START_UNCHARTED_PVE_TYPE
        )
        self.assertEqual(
            (captured["uncharted_id"], captured["layer"], captured["area"],
             captured["room"]),
            (1, 1, 1, 1),
        )

        after = self._settle(
            stream, buffer, reader, writer,
            pve_type=BATTLE_START_UNCHARTED_PVE_TYPE,
            sync_count=sync_count,
        )
        # TASK-0344: a 秘境 WIN now carries its gain block BEFORE the game-over reply, because
        # pkgGameOverDisplay drains TB_GAIN_TYPE.FIGHT in the same turn it shows the panel.
        self.assertEqual(
            [packet.spec.protocol_id for packet in after],
            # TASK-0394: the piece write pushes S2C 75 before the gain block.
            [75, 81, 83, 82, BATTLE_GAME_OVER_REPLY_ID],
        )
        # TASK-0394: S2C 75 (the piece row) now precedes the gain block.
        self.assertEqual(after[0].fields, (4438, 5), "the piece row: cfg 4438 x5")
        self.assertEqual(after[1].fields, (1,), "dGainType = TB_GAIN_TYPE.FIGHT")
        self.assertEqual(
            after[2].fields, (9, 4438, b"", 5),  # the wire form of an empty instance id
            "the row is cfg unchartedReward[2001] slot 1, never a guess",
        )
        self.assertEqual(self._drain(stream, buffer, reader), [])

        paired = self._wait_events(
            events, "tcp_dungeon_settlement_paired", 1
        )[0]
        self.assertEqual(
            paired["pve_or_pvp_type"], BATTLE_START_UNCHARTED_PVE_TYPE
        )
        self.assertEqual(
            (paired["uncharted_id"], paired["layer"], paired["area"],
             paired["room"]),
            (1, 1, 1, 1),
        )
        self.assertTrue(paired["victory"])
        self.assertTrue(paired["paired"])
        self.assertEqual(paired["dPackCheckFlag"], sync_count)
        self.assertTrue(paired["pack_check_matches_s2c89"])
        for key in (
            "pushed", "victory_push_sent", "recorded", "persisted",
            "role_store_written", "state_changed",
            "chapter_fight_recorded", "reward_granted", "wallet_written",
            "card_written", "historical_values_recovered",
        ):
            self.assertFalse(paired[key], key)
        self.assertFalse(any(
            event.get("event") == "tcp_babel_settlement_paired"
            for event in events
        ))
        self._assert_no_persistence(store)

    def test_the_locktower_type_73_keeps_its_push_and_its_events(self) -> None:
        _store, server, events = self._dev_server()
        stream, buffer, reader, writer = self._login_keep_open(server)
        # The login segment can trail post-283 pushes (e.g. S2C 555); drain
        # them so every assertion below is about the battle edge alone.
        self._drain(stream, buffer, reader)
        battle_start = self._start(
            stream, buffer, reader, writer,
            BATTLE_START_LOCKTOWER_PVE_TYPE,
            p1=5, p2=1,
        )
        self.assertEqual(
            battle_start.fields[4], BATTLE_START_LOCKTOWER_PVE_TYPE
        )
        sync_count = battle_start.fields[6]

        after = self._settle(
            stream, buffer, reader, writer,
            pve_type=BATTLE_START_LOCKTOWER_PVE_TYPE,
            sync_count=sync_count,
        )
        self.assertEqual(
            [packet.spec.protocol_id for packet in after],
            [BATTLE_GAME_OVER_REPLY_ID],
        )
        # The settlement gets its unlock first, then exactly one S2C 580.
        self.assertEqual(
            [packet.spec.protocol_id
             for packet in self._drain(stream, buffer, reader)],
            [BABEL_INFO_ID],
        )
        paired = self._wait_events(
            events, "tcp_babel_settlement_paired", 1
        )[0]
        self.assertEqual(paired["floor"], 5)
        self.assertEqual(paired["dFightType"], 1)
        self.assertEqual(paired["pve_or_pvp_type"],
                         BATTLE_START_LOCKTOWER_PVE_TYPE)
        self.assertEqual(paired["s2c89_dSyncCount"], sync_count)
        self.assertTrue(paired["pack_check_matches_s2c89"])
        self.assertFalse(paired["recorded"])
        self.assertFalse(paired["persisted"])
        self._wait_events(events, "tcp_babel_settlement_info_pushed", 1)
        # The generalized 66/88 edge never substitutes for the babel one.
        self.assertFalse(any(
            event.get("event") in {
                "tcp_dungeon_battle_start_captured",
                "tcp_dungeon_settlement_paired",
            }
            for event in events
        ))

    def test_a_settlement_without_its_start_is_ignored_and_keeps_the_socket(
        self,
    ) -> None:
        _store, server, events = self._dev_server()
        stream, buffer, reader, writer = self._login_keep_open(server)
        # The login segment can trail post-283 pushes (e.g. S2C 555); drain
        # them so every assertion below is about the battle edge alone.
        self._drain(stream, buffer, reader)
        after = self._settle(
            stream, buffer, reader, writer,
            pve_type=BATTLE_START_UNCHARTED_PVE_TYPE,
            sync_count=1,
        )
        self.assertEqual(
            [packet.spec.protocol_id for packet in after],
            [BATTLE_GAME_OVER_REPLY_ID],
        )
        self.assertEqual(self._drain(stream, buffer, reader), [])
        ignored = self._wait_events(
            events, "tcp_dungeon_settlement_not_paired", 1
        )[0]
        self.assertEqual(
            ignored["reason"],
            "no_pending_dungeon_battle_start_on_this_connection",
        )
        self.assertFalse(ignored["paired"])
        self.assertFalse(ignored["pushed"])
        self.assertFalse(ignored["recorded"])
        self.assertFalse(ignored["persisted"])
        self.assertFalse(ignored["role_store_written"])
        self.assertFalse(ignored["state_changed"])
        self.assertTrue(ignored["connection_kept_alive"])
        self.assertFalse(any(
            event.get("event") == "tcp_dungeon_settlement_paired"
            for event in events
        ))
        # Still alive: the next C2S 69 is answered with its S2C 89.
        battle_start = self._start(
            stream, buffer, reader, writer,
            BATTLE_START_UNCHARTED_PVE_TYPE,
            p1=1, p2=1, p3=1, p4=1,
        )
        self.assertEqual(
            battle_start.spec.protocol_id, BATTLE_START_REPLY_ID
        )

    def test_a_fixed_86_reply_does_not_remember_the_requested_type(self) -> None:
        # The default (v24) profile still carries the v17 fixed reply: it
        # answers 86/3364 to every request, and the client then really enters
        # the chapter branch.  A 66 request is therefore NOT a remembered 66
        # fight -- remembering it would claim a battle the client never had.
        store = RoleStore.in_memory(time_source=self._clock)
        self.addCleanup(store.close)
        server, events = self.start_server(store, dev_play_babel=False)
        stream, buffer, reader, writer = self._login_keep_open(server)
        self._drain(stream, buffer, reader)
        battle_start = self._start(
            stream, buffer, reader, writer,
            BATTLE_START_GOD_FIGHT_PVE_TYPE,
            p1=1, p2=2, p3=1,
        )
        self.assertEqual(battle_start.fields[4], BATTLE_START_PVE_OR_PVP_TYPE)
        self.assertEqual(battle_start.fields[5], BATTLE_START_RAW_RANDOM_SEED)
        self.assertFalse(any(
            event.get("event") == "tcp_dungeon_battle_start_captured"
            for event in events
        ))
        after = self._settle(
            stream, buffer, reader, writer,
            pve_type=BATTLE_START_GOD_FIGHT_PVE_TYPE,
            sync_count=battle_start.fields[6],
        )
        self.assertEqual(
            [packet.spec.protocol_id for packet in after],
            [BATTLE_GAME_OVER_REPLY_ID],
        )
        ignored = self._wait_events(
            events, "tcp_dungeon_settlement_not_paired", 1
        )[0]
        self.assertEqual(
            ignored["reason"],
            "no_pending_dungeon_battle_start_on_this_connection",
        )


class DungeonBattleTypeConstantTests(unittest.TestCase):
    """The recovered client constants, and the two copies of 88 staying equal."""

    def test_the_recovered_types(self) -> None:
        self.assertEqual(BATTLE_START_GOD_FIGHT_PVE_TYPE, 66)
        self.assertEqual(BATTLE_START_UNCHARTED_PVE_TYPE, 88)
        self.assertEqual(BATTLE_START_LOCKTOWER_PVE_TYPE, 73)
        self.assertEqual(
            BATTLE_START_DUNGEON_PVE_TYPES,
            frozenset({
                BATTLE_START_GOD_FIGHT_PVE_TYPE,
                BATTLE_START_UNCHARTED_PVE_TYPE,
            }),
        )
        self.assertEqual(dpu.UNCHARTED_PVE_TYPE,
                         BATTLE_START_UNCHARTED_PVE_TYPE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
