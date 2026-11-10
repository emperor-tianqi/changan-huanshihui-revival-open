from __future__ import annotations

import csv
import dataclasses
import math
import unittest
from pathlib import Path

from revival_stub.compatibility_profile import (
    BATTLE_START_FIELD_TABLE,
    BATTLE_START_HISTORICAL_ORDERING,
    BATTLE_START_PVE_OR_PVP_TYPE,
    BATTLE_START_RANDOM_SEED_BASE,
    BATTLE_START_RANDOM_SEED_POLICY,
    BATTLE_START_RAW_RANDOM_SEED,
    BATTLE_START_RECOVERED_CONSTANT_POSITIONS,
    BATTLE_START_REPLY_FIELD_COUNT,
    BATTLE_START_REPLY_FORMAT,
    BATTLE_START_REQUEST_FORMAT,
    BATTLE_START_REQUEST_TYPE_INDEX,
    BATTLE_START_SEED_POSITION,
    BATTLE_START_SELF_GROUP,
    BATTLE_START_SYNC_COUNT_POSITION,
    BATTLE_START_TYPE_POSITION,
    BATTLE_START_TYPE_RANDOM_SEED_POLICY,
    BATTLE_START_VALUE_CONSTANT,
    BATTLE_START_VALUE_COUNTER,
    BATTLE_START_VALUE_ECHO,
    BATTLE_START_VALUE_ORIGINS,
    BATTLE_START_VALUE_REQUEST_SEED,
    BATTLE_GAME_OVER_FIELD_TABLE,
    BATTLE_GAME_OVER_HISTORICAL_ORDERING,
    BATTLE_GAME_OVER_REGISTERED_REQUEST,
    BATTLE_GAME_OVER_REPLY_FIELD_COUNT,
    BATTLE_GAME_OVER_REPLY_FORMAT,
    BATTLE_GAME_OVER_REQUEST_FORMAT,
    BATTLE_GAME_OVER_UNLOCK_NAMES,
    BABEL_SETTLEMENT_PUSH_FIELD_NAMES,
    BABEL_SETTLEMENT_PUSH_FORMAT,
    BABEL_SETTLEMENT_PUSH_MAX_PER_SETTLEMENT,
    TEAM_MAX_FIGHT_ALLOWED_POSITIONS,
    TEAM_MAX_FIGHT_NAME,
    TEAM_MAX_FIGHT_TRIGGERS,
    TEAM_MAX_FIGHT_VALUE_POLICY,
    TEAM_MAX_FIGHT_WIRE_FORMAT,
    TEAM_MAX_FIGHT_WIRE_ID,
    BABEL_SETTLEMENT_PUSH_SEND_TRIGGER,
    BABEL_SETTLEMENT_PUSH_TRIGGER_REQUEST,
    BABEL_SETTLEMENT_PUSH_VALUE_SOURCE,
    BABEL_SETTLEMENT_PUSH_WIRE_ID,
    BATTLE_START_LOCKTOWER_PVE_TYPE,
    CHAPTER_ITEM_CONTAINER_POLICY,
    CHAPTER_ITEM_HISTORICAL_ORDERING,
    CHAPTER_ITEM_ROW_COUNT,
    CUMULATIVE_CATALOG,
    DAILY_CATALOG,
    PROFILE_ID,
    PROFILE_ID_V1,
    PROFILE_ID_V2,
    PROFILE_ID_V3,
    PROFILE_ID_V4,
    PROFILE_ID_V5,
    PROFILE_ID_V6,
    PROFILE_ID_V7,
    PROFILE_ID_V8,
    PROFILE_ID_V9,
    PROFILE_ID_V10,
    PROFILE_ID_V11,
    PROFILE_ID_V12,
    PROFILE_ID_V13,
    PROFILE_ID_V17,
    PROFILE_ID_V18,
    PROFILE_ID_V19,
    PROFILE_ID_V20,
    PROFILE_ID_V21,
    PROFILE_ID_V22,
    PROFILE_ID_V23,
    PROFILE_ID_V24,
    RECHARGE_ACTIVITY_HISTORICAL_ORDERING,
    CHAPTER_MACHINE_CONTAINER_POLICY,
    CHAPTER_MACHINE_HISTORICAL_ORDERING,
    CHAPTER_MACHINE_ROW_COUNT,
    CHAPTER_TASK_CONTAINER_POLICY,
    CHAPTER_TASK_HISTORICAL_ORDERING,
    CHAPTER_TASK_ROW_COUNT,
    EXPERIENCE_TASK_CONTAINER_POLICY,
    EXPERIENCE_TASK_HISTORICAL_ORDERING,
    EXPERIENCE_TASK_ROW_COUNT,
    RECHARGE_ACTIVITY_RECHARGE_START,
    RECHARGE_ACTIVITY_RECHARGE_START_POLICY,
    ROLE_VALUE_WIRE_FORMAT,
    SEVEN_DAY_CATALOG,
    STORY_START_HISTORICAL_ORDERING,
    STORY_START_LIST_POLICY,
    TEAM_SNAPSHOT_CARD_SLOTS,
    TEAM_SNAPSHOT_CURRENT_GROUP,
    TEAM_SNAPSHOT_CURRENT_GROUP_POLICY,
    TEAM_SNAPSHOT_EQUIP_SLOTS,
    TEAM_SNAPSHOT_HISTORICAL_ORDERING,
    TEAM_SNAPSHOT_MAX_GROUP,
    VALID_ORIGINS,
    CompatibilityProfile,
    StoryStartPolicy,
    TeamSnapshotPolicy,
    get_profile,
    get_profile_v1,
    get_profile_v2,
    get_profile_v3,
    get_profile_v4,
    get_profile_v5,
    get_profile_v6,
    get_profile_v7,
    get_profile_v8,
    get_profile_v9,
    get_profile_v10,
    get_profile_v11,
    get_profile_v12,
    get_profile_v13,
    get_profile_v14,
    get_profile_v15,
    get_profile_v16,
    get_profile_v17,
    get_profile_v18,
    get_profile_v19,
    get_profile_v20,
    get_profile_v21,
    get_profile_v22,
    instantiate_role_compatibility_state,
)


ROOT = Path(__file__).resolve().parents[2]

# Frozen at v1 creation (2026-10-04).  Regenerating this value would mean the
# v1 audit history was rewritten; the v2 successor must never change it.
V1_ROLE_VALUE_VECTOR_HASH = (
    "7d3d0560a65586dd4d99524b573c492020dfcb957a000df1e47ddcfc14690901"
)


class CompatibilityProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = get_profile()

    def test_exact_id_and_immutable_profile(self) -> None:
        self.assertEqual(self.profile.profile_id, PROFILE_ID)
        self.assertEqual(self.profile.metadata.target_client, "cn-0.1.20.1")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.profile.metadata.profile_id = "mutated"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            self.profile.role_values[0] = self.profile.role_values[0]  # type: ignore[index]

    def test_role_value_records_match_canonical_mapping(self) -> None:
        mapping_path = ROOT / "reports" / "role_value_wire_mapping.csv"
        with mapping_path.open(encoding="utf-8-sig", newline="") as handle:
            mapping = list(csv.DictReader(handle))
        self.assertEqual(len(self.profile.role_values), 252)
        self.assertEqual(
            "".join(field.wire_type for field in self.profile.role_values),
            ROLE_VALUE_WIRE_FORMAT,
        )
        self.assertEqual(
            [field.symbolic_name for field in self.profile.role_values],
            [row["value_name"] for row in mapping],
        )
        self.assertEqual(
            [field.wire_position for field in self.profile.role_values],
            list(range(1, 253)),
        )
        self.assertEqual(
            self.profile.class_counts(),
            {
                "C_SYNTHETIC_ZERO_SAFE": 9,
                "D_DEFERRED_WIRE_PLACEHOLDER": 243,
            },
        )
        self.assertTrue(all(field.provenance.source for field in self.profile.role_values))
        self.assertTrue(all(field.provenance.reason for field in self.profile.role_values))
        self.assertTrue(all(field.provenance.replaceable for field in self.profile.role_values))
        self.assertTrue(all(field.initial_value == 0 for field in self.profile.role_values))

    def test_invalid_profiles_and_fields_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.profile,
                role_values=self.profile.role_values[:-1],
            )
        first = self.profile.role_values[0]
        with self.assertRaises(ValueError):
            dataclasses.replace(first, initial_value=math.inf)
        second = self.profile.role_values[1]
        with self.assertRaises(ValueError):
            dataclasses.replace(second, initial_value=-1)
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.profile,
                role_values=(
                    dataclasses.replace(first, symbolic_name=second.symbolic_name),
                    *self.profile.role_values[1:],
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                first,
                provenance=dataclasses.replace(first.provenance, source=""),
            )
        # The assertion is about an UNKNOWN version, not about a later one: a
        # provenance may not claim a compatibility version that was never
        # allocated.  It used to be spelled with the literal `...-v9`, which
        # TASK-0104 then allocated -- so the literal is now derived from the
        # active version with a suffix no profile id may ever carry, and the
        # stronger "a v1 domain may not move to a LATER version" invariant is
        # asserted separately below (against `PROFILE_ID_V3`, and the same shape
        # is why the v1 role values are not allowed to carry the active version).
        with self.assertRaises(ValueError):
            dataclasses.replace(
                first.provenance,
                compatibility_version=PROFILE_ID + "-draft",
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.profile,
                role_values=(
                    dataclasses.replace(
                        first,
                        provenance=dataclasses.replace(
                            first.provenance, compatibility_version=PROFILE_ID
                        ),
                    ),
                    *self.profile.role_values[1:],
                ),
            )
        # Lineage invariants (v1 -> v2 -> v3 -> v4): each domain must keep the
        # version at which it was introduced; policies may not move between
        # versions.
        with self.assertRaises(ValueError):
            dataclasses.replace(self.profile, server_time=None)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.profile, mystery_shop=None)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.profile, story_start=None)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.profile, parent_profile_id=None)
        with self.assertRaises(ValueError):
            dataclasses.replace(
                get_profile_v1(),
                server_time=self.profile.server_time,
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                get_profile_v1(),
                mystery_shop=self.profile.mystery_shop,
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                get_profile_v1(),
                story_start=self.profile.story_start,
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                get_profile_v2(),
                mystery_shop=self.profile.mystery_shop,
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                get_profile_v2(),
                story_start=self.profile.story_start,
            )
        # v3 is frozen: it may not retroactively acquire the v4 policy.
        with self.assertRaises(ValueError):
            dataclasses.replace(
                get_profile_v3(),
                story_start=self.profile.story_start,
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.profile,
                role_values=(
                    dataclasses.replace(
                        first,
                        provenance=dataclasses.replace(
                            first.provenance, compatibility_version=PROFILE_ID_V3
                        ),
                    ),
                    *self.profile.role_values[1:],
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.profile,
                server_time=dataclasses.replace(
                    self.profile.server_time,
                    time_provenance=dataclasses.replace(
                        self.profile.server_time.time_provenance,
                        compatibility_version=PROFILE_ID_V3,
                    ),
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.profile,
                mystery_shop=dataclasses.replace(
                    self.profile.mystery_shop,
                    time_provenance=dataclasses.replace(
                        self.profile.mystery_shop.time_provenance,
                        compatibility_version=PROFILE_ID_V2,
                    ),
                ),
            )

    def test_role_instances_are_deep_copy_isolated(self) -> None:
        first = instantiate_role_compatibility_state(1, profile=self.profile)
        second = instantiate_role_compatibility_state(1, profile=self.profile)
        self.assertIsNot(first.role_values, second.role_values)
        self.assertIsNot(first.role_values[0], second.role_values[0])
        self.assertIsNot(first.sign_state, second.sign_state)
        self.assertIsNot(first.scene_state, second.scene_state)
        first.role_values[0].value = 77.0
        first.sign_state.seven_day = 2
        first.scene_state.pos_x = 99
        self.assertEqual(second.role_values[0].value, 0.0)
        self.assertEqual(second.sign_state.seven_day, 1)
        self.assertEqual(second.scene_state.pos_x, 0)
        self.assertEqual(self.profile.role_values[0].initial_value, 0.0)
        vector = first.role_value_vector()
        self.assertEqual(vector[0], 77.0)
        self.assertEqual(vector[1:], (0,) * 251)

    def test_sign_catalogs_and_fresh_account_state(self) -> None:
        def rows(value: str) -> list[list[int]]:
            return [[int(part) for part in row.split(",")] for row in value.split(";")]

        daily = rows(DAILY_CATALOG)
        cumulative = rows(CUMULATIVE_CATALOG)
        seven_day = rows(SEVEN_DAY_CATALOG)
        self.assertEqual(
            daily,
            [[day, 7, 0, day * 1000] for day in range(1, 32)],
        )
        self.assertEqual(
            cumulative,
            [[3, 7, 0, 1000], [5, 7, 0, 2000], [7, 7, 0, 3000], [10, 7, 0, 3000]],
        )
        self.assertEqual(
            seven_day,
            [
                [1, 7, 0, 10000],
                [2, 262, 0, 1],
                [3, 6, 0, 150],
                [4, 262, 0, 2],
                [5, 250, 0, 2],
                [6, 6, 0, 250],
                [7, 3, 4413, 1],
            ],
        )
        state = instantiate_role_compatibility_state(1, profile=self.profile)
        self.assertFalse(state.sign_state.is_new_month)
        self.assertEqual(state.sign_state.sign_count, 0)
        self.assertFalse(state.sign_state.is_today_signed)
        self.assertEqual(state.sign_state.sum_claimed, "")
        self.assertEqual(state.sign_state.seven_claimed, "")
        self.assertEqual(state.sign_state.seven_day, 1)
        fields = state.sign_state.protocol_fields(self.profile.sign_catalogs)
        self.assertEqual(fields[5], DAILY_CATALOG)
        self.assertEqual(fields[6], CUMULATIVE_CATALOG)
        self.assertEqual(fields[8], SEVEN_DAY_CATALOG)

    def test_scene_and_active_soul_contract(self) -> None:
        state = instantiate_role_compatibility_state(1, profile=self.profile)
        self.assertEqual(state.scene_state.protocol_fields(), (0, 0, 0, 0))
        self.assertEqual(
            self.profile.scene_state_template.provenance.origin,
            "synthetic_compatibility",
        )
        self.assertEqual(state.active_soul, 101)
        self.assertEqual(
            self.profile.active_soul_for_career(1).provenance.origin,
            "deterministic_recovered",
        )
        with self.assertRaises(ValueError):
            instantiate_role_compatibility_state(2, profile=self.profile)

    def test_v1_profile_remains_constructible_and_immutable(self) -> None:
        v1 = get_profile_v1()
        self.assertEqual(v1.profile_id, PROFILE_ID_V1)
        self.assertIsNone(v1.parent_profile_id)
        self.assertIsNone(v1.server_time)
        self.assertEqual(v1.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        self.assertEqual(v1.role_value_vector(), (0.0,) + (0,) * 251)
        self.assertEqual(v1.class_counts(), {
            "C_SYNTHETIC_ZERO_SAFE": 9,
            "D_DEFERRED_WIRE_PLACEHOLDER": 243,
        })
        for provenance in v1._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)

    def test_v2_lineage_inherits_v1_unchanged(self) -> None:
        v1 = get_profile_v1()
        v2 = get_profile_v2()
        self.assertEqual(v2.profile_id, PROFILE_ID_V2)
        self.assertNotEqual(v2.profile_id, PROFILE_ID)
        self.assertEqual(v2.parent_profile_id, PROFILE_ID_V1)
        self.assertIs(v2.role_values, v1.role_values)
        self.assertIs(v2.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v2.sign_state_template, v1.sign_state_template)
        self.assertIs(v2.scene_state_template, v1.scene_state_template)
        self.assertIs(v2.active_soul_rules, v1.active_soul_rules)
        self.assertEqual(v2.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v2._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        self.assertIsNotNone(v2.server_time)
        self.assertIsNone(v2.mystery_shop)

    def test_v3_lineage_inherits_v2_unchanged(self) -> None:
        v1 = get_profile_v1()
        v2 = get_profile_v2()
        v3 = get_profile_v3()
        self.assertEqual(v3.profile_id, PROFILE_ID_V3)
        self.assertNotEqual(v3.profile_id, PROFILE_ID)
        self.assertEqual(v3.parent_profile_id, PROFILE_ID_V2)
        self.assertIs(v3.role_values, v1.role_values)
        self.assertIs(v3.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v3.sign_state_template, v1.sign_state_template)
        self.assertIs(v3.scene_state_template, v1.scene_state_template)
        self.assertIs(v3.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v3.server_time, v2.server_time)
        self.assertIsNotNone(v3.mystery_shop)
        self.assertIsNone(v3.story_start)
        self.assertEqual(v3.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v3._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        for provenance in (
            v3.server_time.time_provenance,
            v3.server_time.clock_provenance,
            v3.server_time.power_recover_provenance,
            v3.server_time.open_time_provenance,
            v3.server_time.ordering_provenance,
        ):
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V2)

    def test_v4_lineage_inherits_v3_unchanged(self) -> None:
        # TASK-0035: v4 is the additive successor of v3 and adds ONLY the
        # StoryStart policy; every inherited v3 domain object must be the very
        # same object, so a v3 server still replays the frozen v3 wire.
        v1 = get_profile_v1()
        v3 = get_profile_v3()
        v4 = get_profile_v4()
        self.assertEqual(v4.profile_id, PROFILE_ID_V4)
        self.assertNotEqual(v4.profile_id, PROFILE_ID_V3)
        self.assertNotEqual(v4.profile_id, PROFILE_ID)
        self.assertEqual(v4.parent_profile_id, PROFILE_ID_V3)
        self.assertIs(v4.role_values, v1.role_values)
        self.assertIs(v4.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v4.sign_state_template, v1.sign_state_template)
        self.assertIs(v4.scene_state_template, v1.scene_state_template)
        self.assertIs(v4.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v4.server_time, v3.server_time)
        self.assertIs(v4.mystery_shop, v3.mystery_shop)
        self.assertIsNotNone(v4.story_start)
        # TASK-0092: the Team snapshot is a v5-only policy.  Without it a v4
        # server inserts nothing and replays the frozen v4 wire byte for byte.
        self.assertIsNone(v4.team_snapshot)
        self.assertEqual(v4.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v4._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)

    def test_v5_lineage_inherits_v4_unchanged(self) -> None:
        # TASK-0092: v5 is the additive successor of v4 and adds ONLY the
        # TeamSnapshot policy; every inherited domain object must be the very
        # same object, so a v1-v4 server still replays the frozen v1-v4 wire.
        v1 = get_profile_v1()
        v3 = get_profile_v3()
        v4 = get_profile_v4()
        v5 = get_profile_v5()
        self.assertEqual(v5.profile_id, PROFILE_ID_V5)
        self.assertNotEqual(v5.profile_id, PROFILE_ID)
        self.assertNotEqual(v5.profile_id, PROFILE_ID_V4)
        self.assertEqual(v5.parent_profile_id, PROFILE_ID_V4)
        self.assertIs(v5.role_values, v1.role_values)
        self.assertIs(v5.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v5.sign_state_template, v1.sign_state_template)
        self.assertIs(v5.scene_state_template, v1.scene_state_template)
        self.assertIs(v5.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v5.server_time, v3.server_time)
        self.assertIs(v5.mystery_shop, v3.mystery_shop)
        self.assertIs(v5.story_start, v4.story_start)
        self.assertIsNotNone(v5.team_snapshot)
        self.assertEqual(v5.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v5._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)

    def test_v6_lineage_inherits_v5_unchanged(self) -> None:
        # TASK-0094: v6 is the additive successor of v5 and adds ONLY the
        # RechargeActivity policy.  Every inherited object must be the very same
        # object, so the v5 team snapshot and everything before it stay frozen.
        v1 = get_profile_v1()
        v3 = get_profile_v3()
        v4 = get_profile_v4()
        v5 = get_profile_v5()
        v6 = get_profile_v6()
        self.assertEqual(v6.profile_id, PROFILE_ID_V6)
        self.assertEqual(v6.parent_profile_id, PROFILE_ID_V5)
        self.assertIs(v6.role_values, v1.role_values)
        self.assertIs(v6.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v6.sign_state_template, v1.sign_state_template)
        self.assertIs(v6.scene_state_template, v1.scene_state_template)
        self.assertIs(v6.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v6.server_time, v3.server_time)
        self.assertIs(v6.mystery_shop, v3.mystery_shop)
        self.assertIs(v6.story_start, v4.story_start)
        self.assertIs(v6.team_snapshot, v5.team_snapshot)
        self.assertIsNotNone(v6.recharge_activity)
        self.assertEqual(v6.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v6._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # v1..v5 carry no recharge-activity state at all.
        for profile in (v1, get_profile_v2(), v3, v4, v5):
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.recharge_activity)

    def test_v7_lineage_inherits_v6_unchanged(self) -> None:
        # TASK-0098: v7 is the additive successor of v6 and adds ONLY the
        # ChapterItemInit policy.  Every inherited object must be the very same
        # object, so everything the login segment sent before stays frozen.
        v1 = get_profile_v1()
        v3 = get_profile_v3()
        v4 = get_profile_v4()
        v5 = get_profile_v5()
        v6 = get_profile_v6()
        v7 = get_profile_v7()
        self.assertEqual(v7.profile_id, PROFILE_ID_V7)
        self.assertEqual(v7.parent_profile_id, PROFILE_ID_V6)
        self.assertIs(v7.role_values, v1.role_values)
        self.assertIs(v7.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v7.sign_state_template, v1.sign_state_template)
        self.assertIs(v7.scene_state_template, v1.scene_state_template)
        self.assertIs(v7.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v7.server_time, v3.server_time)
        self.assertIs(v7.mystery_shop, v3.mystery_shop)
        self.assertIs(v7.story_start, v4.story_start)
        self.assertIs(v7.team_snapshot, v5.team_snapshot)
        self.assertIs(v7.recharge_activity, v6.recharge_activity)
        self.assertIsNotNone(v7.chapter_item_init)
        self.assertEqual(v7.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v7._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # v1..v6 carry no chapter-item state at all: a v6 server's wire is
        # byte-for-byte what it was before this task.
        for profile in (v1, get_profile_v2(), v3, v4, v5, v6):
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.chapter_item_init)
        # ... and v7 itself carries no experience-task state (TASK-0102).
        self.assertIsNone(v7.experience_task_init)

    def test_v8_lineage_inherits_v7_unchanged(self) -> None:
        # TASK-0102: v8 is the additive successor of v7 and adds ONLY the
        # ExperienceTaskInit policy.  Every inherited object must be the very same
        # object, so everything the login segment sent before stays frozen.
        # TASK-0104 moved the ACTIVE profile to v9; v8 stays constructible and
        # byte-for-byte what RUN-0031 and RUN-0032 ran.
        v1 = get_profile_v1()
        v7 = get_profile_v7()
        v8 = get_profile_v8()
        self.assertEqual(v8.profile_id, PROFILE_ID_V8)
        self.assertEqual(v8.parent_profile_id, PROFILE_ID_V7)
        self.assertIs(v8.role_values, v1.role_values)
        self.assertIs(v8.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v8.sign_state_template, v1.sign_state_template)
        self.assertIs(v8.scene_state_template, v1.scene_state_template)
        self.assertIs(v8.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v8.server_time, v7.server_time)
        self.assertIs(v8.mystery_shop, v7.mystery_shop)
        self.assertIs(v8.story_start, v7.story_start)
        self.assertIs(v8.team_snapshot, v7.team_snapshot)
        self.assertIs(v8.recharge_activity, v7.recharge_activity)
        self.assertIs(v8.chapter_item_init, v7.chapter_item_init)
        self.assertIsNotNone(v8.experience_task_init)
        self.assertIsNone(v8.chapter_task_init)
        self.assertEqual(v8.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v8._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # v1..v7 carry no experience-task state at all: a v7 server's wire is
        # byte-for-byte what it was before this task (the profile RUN-0031 ran).
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.experience_task_init)

    def test_v9_lineage_inherits_v8_unchanged(self) -> None:
        # TASK-0104: v9 is the additive successor of v8 and adds ONLY the
        # ChapterTaskInit policy.  Every inherited object must be the very same
        # object, so everything the login segment sent before stays frozen --
        # including the whole v8 experience-task pair.  TASK-0109 made v10 active,
        # so this asks for the frozen v9 profile explicitly.
        v1 = get_profile_v1()
        v8 = get_profile_v8()
        v9 = get_profile_v9()
        self.assertEqual(v9.profile_id, PROFILE_ID_V9)
        self.assertEqual(v9.parent_profile_id, PROFILE_ID_V8)
        self.assertIs(v9.role_values, v1.role_values)
        self.assertIs(v9.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v9.sign_state_template, v1.sign_state_template)
        self.assertIs(v9.scene_state_template, v1.scene_state_template)
        self.assertIs(v9.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v9.server_time, v8.server_time)
        self.assertIs(v9.mystery_shop, v8.mystery_shop)
        self.assertIs(v9.story_start, v8.story_start)
        self.assertIs(v9.team_snapshot, v8.team_snapshot)
        self.assertIs(v9.recharge_activity, v8.recharge_activity)
        self.assertIs(v9.chapter_item_init, v8.chapter_item_init)
        self.assertIs(v9.experience_task_init, v8.experience_task_init)
        self.assertIsNotNone(v9.chapter_task_init)
        self.assertEqual(v9.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v9._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # v1..v8 carry no chapter-task state at all: a v8 server's wire is
        # byte-for-byte what it was before this task (the profile RUN-0032 ran).
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.chapter_task_init)
        # ... and the profiles that carry it are exactly the ones from v9 onward.
        self.assertEqual(
            [builder().profile_id
             for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                             get_profile_v4, get_profile_v5, get_profile_v6,
                             get_profile_v7, get_profile_v8, get_profile_v9)
             if builder().chapter_task_init is not None],
            [PROFILE_ID_V9],
        )

    def test_v10_lineage_inherits_v9_unchanged(self) -> None:
        # TASK-0109: v10 is the additive successor of v9 and adds ONLY the
        # ChapterMachineInit policy.  Every inherited object must be the very same
        # object, so everything the login segment sent before stays frozen --
        # including the whole v9 chapter-task pair and the v7/v8 pairs under it.
        # TASK-0140 made v11 active, so this test asks for the frozen v10 profile
        # by name; that IS the point of the lineage (D-110: a later version may
        # never retroactively change an earlier one).
        v1 = get_profile_v1()
        v9 = get_profile_v9()
        v10 = get_profile_v10()
        self.assertEqual(v10.profile_id, PROFILE_ID_V10)
        self.assertEqual(v10.parent_profile_id, PROFILE_ID_V9)
        self.assertIs(v10.role_values, v1.role_values)
        self.assertIs(v10.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v10.sign_state_template, v1.sign_state_template)
        self.assertIs(v10.scene_state_template, v1.scene_state_template)
        self.assertIs(v10.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v10.server_time, v9.server_time)
        self.assertIs(v10.mystery_shop, v9.mystery_shop)
        self.assertIs(v10.story_start, v9.story_start)
        self.assertIs(v10.team_snapshot, v9.team_snapshot)
        self.assertIs(v10.recharge_activity, v9.recharge_activity)
        self.assertIs(v10.chapter_item_init, v9.chapter_item_init)
        self.assertIs(v10.experience_task_init, v9.experience_task_init)
        self.assertIs(v10.chapter_task_init, v9.chapter_task_init)
        self.assertIsNotNone(v10.chapter_machine_init)
        self.assertIsNone(v9.chapter_machine_init)
        self.assertEqual(v10.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v10._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # v1..v9 carry no chapter-machine state at all: a v9 server's wire is
        # byte-for-byte what it was before this task (the profile RUN-0033 ran).
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.chapter_machine_init)
        # ... and exactly one profile carries it.  (v11 inherits the SAME object;
        # "carries it" is asserted below, so this list names the version that
        # INTRODUCED the policy.)
        self.assertEqual(
            [builder().profile_id
             for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                             get_profile_v4, get_profile_v5, get_profile_v6,
                             get_profile_v7, get_profile_v8, get_profile_v9,
                             get_profile_v10)
             if builder().chapter_machine_init is not None],
            [PROFILE_ID_V10],
        )

    def test_chapter_machine_init_policy_contract(self) -> None:
        policy = get_profile().chapter_machine_init
        assert policy is not None
        # S2C400 initChapterMachineStart + S2C402 initChapterMachineEnd: two empty
        # bodies, in the order the client's own code forces.
        self.assertEqual(
            policy.packets(),
            (("initChapterMachineStart", ()), ("initChapterMachineEnd", ())),
        )
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(policy.container_policy, CHAPTER_MACHINE_CONTAINER_POLICY)
        self.assertEqual(policy.row_count, CHAPTER_MACHINE_ROW_COUNT)
        self.assertEqual(policy.row_count, 0)
        self.assertEqual(
            policy.historical_ordering, CHAPTER_MACHINE_HISTORICAL_ORDERING
        )
        for provenance in (
            policy.container_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V10)
            self.assertTrue(provenance.replaceable)
            self.assertTrue(provenance.reason)
        # The container is the client's own initialiser, and the report must keep
        # saying so -- including that no machine row is sent.
        self.assertIn("initChapterMachineStart", policy.container_provenance.reason)
        self.assertIn(
            "pkgChapterMachineMgr.lua:312", policy.container_provenance.source
        )
        self.assertIn(":46 isMachineCompleted", policy.container_provenance.source)
        self.assertIn("doFinishSwitch", policy.container_provenance.source)
        self.assertIn("D-21", policy.container_provenance.reason)
        # The RISK the empty container carries is stated, not hidden: the client
        # answers "no" to every machine until a real producer is recovered.
        self.assertIn("isMachineCompleted", policy.container_provenance.risk)
        self.assertIs(policy.container_provenance.replaceable, True)

    def test_chapter_machine_init_policy_is_refused_by_every_other_version(self) -> None:
        policy = get_profile().chapter_machine_init
        assert policy is not None
        # A row would assert that a named machine of a named chapter is completed;
        # this archive has none (D-21), so the policy's own constructor refuses one.
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, row_count=1)
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, container_policy="invented_rows")
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, historical_ordering="recovered")
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance, origin="recovered_archive"
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance,
                    compatibility_version=PROFILE_ID_V9,
                ),
            )
        # No earlier version may be handed the policy either: the profile's own
        # lineage invariants refuse it, so a v1-v9 server can never grow the pair.
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(profile, chapter_machine_init=policy)
        # And the active profile may not be handed a foreign shape: it requires
        # the policy, so dropping it is refused too.
        with self.assertRaises(ValueError):
            dataclasses.replace(get_profile(), chapter_machine_init=None)

    def test_v11_lineage_inherits_v10_unchanged(self) -> None:
        # TASK-0140: v11 is the additive successor of v10 and adds ONLY the
        # BattleStartReply policy.  Every inherited object must be the very same
        # object, so the whole login segment -- every container pair from v5 to
        # v10 and everything under them -- stays byte-for-byte what it was, and a
        # v10 server still replays the frozen v10 wire.
        v1 = get_profile_v1()
        v10 = get_profile_v10()
        # TASK-0155 moved the ACTIVE profile to v12, so this test asks for v11 by
        # name: what it asserts is a property of v11 itself, and reading it
        # through `get_profile()` made the assertion expire on the next version.
        v11 = get_profile_v11()
        self.assertEqual(v11.profile_id, PROFILE_ID_V11)
        self.assertEqual(v11.parent_profile_id, PROFILE_ID_V10)
        self.assertIs(v11.role_values, v1.role_values)
        self.assertIs(v11.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v11.sign_state_template, v1.sign_state_template)
        self.assertIs(v11.scene_state_template, v1.scene_state_template)
        self.assertIs(v11.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v11.server_time, v10.server_time)
        self.assertIs(v11.mystery_shop, v10.mystery_shop)
        self.assertIs(v11.story_start, v10.story_start)
        self.assertIs(v11.team_snapshot, v10.team_snapshot)
        self.assertIs(v11.recharge_activity, v10.recharge_activity)
        self.assertIs(v11.chapter_item_init, v10.chapter_item_init)
        self.assertIs(v11.experience_task_init, v10.experience_task_init)
        self.assertIs(v11.chapter_task_init, v10.chapter_task_init)
        self.assertIs(v11.chapter_machine_init, v10.chapter_machine_init)
        self.assertIsNotNone(v11.battle_start_reply)
        self.assertIsNone(v10.battle_start_reply)
        self.assertEqual(v11.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v11._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # v1..v10 carry no battle-start reply at all: every one of them answers a
        # C2S 69 exactly the way it did before this task -- with nothing.
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9,
                        get_profile_v10):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.battle_start_reply)
        # ... and exactly one profile introduced it.
        self.assertEqual(
            [builder().profile_id
             for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                             get_profile_v4, get_profile_v5, get_profile_v6,
                             get_profile_v7, get_profile_v8, get_profile_v9,
                             get_profile_v10, get_profile_v11)
             if builder().battle_start_reply is not None],
            [PROFILE_ID_V11],
        )

    def test_v12_lineage_inherits_v11_unchanged(self) -> None:
        # TASK-0155: v12 is the additive successor of v11 and adds ONLY the
        # BattleGameOverReply policy.  Every inherited object must be the very same
        # object, so the whole login segment AND the battle-start reply stay
        # byte-for-byte what they were, and a v11 server still replays the frozen
        # v11 wire.
        v1 = get_profile_v1()
        v11 = get_profile_v11()
        # TASK-0167 moved the ACTIVE profile to v13, so this test asks for v12 by
        # name: what it asserts is a property of v12 itself, and reading it
        # through `get_profile()` made the assertion expire on the next version.
        v12 = get_profile_v12()
        self.assertEqual(v12.profile_id, PROFILE_ID_V12)
        self.assertEqual(v12.parent_profile_id, PROFILE_ID_V11)
        self.assertIs(v12.role_values, v1.role_values)
        self.assertIs(v12.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v12.sign_state_template, v1.sign_state_template)
        self.assertIs(v12.scene_state_template, v1.scene_state_template)
        self.assertIs(v12.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v12.server_time, v11.server_time)
        self.assertIs(v12.mystery_shop, v11.mystery_shop)
        self.assertIs(v12.story_start, v11.story_start)
        self.assertIs(v12.team_snapshot, v11.team_snapshot)
        self.assertIs(v12.recharge_activity, v11.recharge_activity)
        self.assertIs(v12.chapter_item_init, v11.chapter_item_init)
        self.assertIs(v12.experience_task_init, v11.experience_task_init)
        self.assertIs(v12.chapter_task_init, v11.chapter_task_init)
        self.assertIs(v12.chapter_machine_init, v11.chapter_machine_init)
        # The battle-start policy is the SAME object, not a copy: answering C2S 69
        # must stay byte-for-byte what v11 did.
        self.assertIs(v12.battle_start_reply, v11.battle_start_reply)
        self.assertIsNotNone(v12.battle_game_over_reply)
        self.assertIsNone(v11.battle_game_over_reply)
        self.assertEqual(v12.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v12._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # Every version before v12 answers a C2S 67 exactly the way it did before
        # this task: with nothing at all (fail closed).
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9,
                        get_profile_v10, get_profile_v11):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.battle_game_over_reply)
        # ... and exactly one profile introduced it.
        self.assertEqual(
            [builder().profile_id
             for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                             get_profile_v4, get_profile_v5, get_profile_v6,
                             get_profile_v7, get_profile_v8, get_profile_v9,
                             get_profile_v10, get_profile_v11, get_profile_v12)
             if builder().battle_game_over_reply is not None],
            [PROFILE_ID_V12],
        )

    def test_v13_lineage_inherits_v12_unchanged(self) -> None:
        # TASK-0167: v13 is the additive successor of v12 and adds ONLY the
        # PrisonFightBossSnapshot policy.  Every inherited object must be the very
        # same object, so the whole login segment -- every container pair, the
        # story snapshot, S2C283 and both battle replies -- stays byte-for-byte
        # what v12 produced, and a v12 server still replays the frozen v12 wire.
        # `get_profile_v13()` names v13 itself; the active profile moved to v14
        # in TASK-0174, which inherits every one of these objects unchanged.
        v1 = get_profile_v1()
        v12 = get_profile_v12()
        v13 = get_profile_v13()
        self.assertEqual(v13.profile_id, PROFILE_ID_V13)
        self.assertEqual(v13.parent_profile_id, PROFILE_ID_V12)
        self.assertIs(v13.role_values, v1.role_values)
        self.assertIs(v13.sign_catalogs, v1.sign_catalogs)
        self.assertIs(v13.sign_state_template, v1.sign_state_template)
        self.assertIs(v13.scene_state_template, v1.scene_state_template)
        self.assertIs(v13.active_soul_rules, v1.active_soul_rules)
        self.assertIs(v13.server_time, v12.server_time)
        self.assertIs(v13.mystery_shop, v12.mystery_shop)
        self.assertIs(v13.story_start, v12.story_start)
        self.assertIs(v13.team_snapshot, v12.team_snapshot)
        self.assertIs(v13.recharge_activity, v12.recharge_activity)
        self.assertIs(v13.chapter_item_init, v12.chapter_item_init)
        self.assertIs(v13.experience_task_init, v12.experience_task_init)
        self.assertIs(v13.chapter_task_init, v12.chapter_task_init)
        self.assertIs(v13.chapter_machine_init, v12.chapter_machine_init)
        # Both battle policies are the SAME objects, not copies: answering C2S 69
        # and C2S 67 must stay byte-for-byte what v12 did.
        self.assertIs(v13.battle_start_reply, v12.battle_start_reply)
        self.assertIs(v13.battle_game_over_reply, v12.battle_game_over_reply)
        self.assertIsNotNone(v13.prison_fight)
        self.assertIsNone(v12.prison_fight)
        self.assertEqual(v13.vector_hash(), V1_ROLE_VALUE_VECTOR_HASH)
        for provenance in v13._core_provenances():
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V1)
        # Every version before v13 sends exactly the login stream it sent before
        # this task: with no snapshot at all.
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9,
                        get_profile_v10, get_profile_v11, get_profile_v12):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                self.assertIsNone(profile.prison_fight)
        # ... and exactly one profile introduced it.
        self.assertEqual(
            [builder().profile_id
             for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                             get_profile_v4, get_profile_v5, get_profile_v6,
                             get_profile_v7, get_profile_v8, get_profile_v9,
                             get_profile_v10, get_profile_v11, get_profile_v12,
                             get_profile_v13)
             if builder().prison_fight is not None],
            [PROFILE_ID_V13],
        )

    def test_battle_game_over_reply_policy_contract(self) -> None:
        policy = get_profile().battle_game_over_reply
        assert policy is not None
        self.assertEqual(policy.reply_format, BATTLE_GAME_OVER_REPLY_FORMAT)
        self.assertEqual(policy.request_format, BATTLE_GAME_OVER_REQUEST_FORMAT)
        self.assertEqual(len(policy.fields), BATTLE_GAME_OVER_REPLY_FIELD_COUNT)
        self.assertEqual(
            "".join(field.wire_type for field in policy.fields),
            BATTLE_GAME_OVER_REPLY_FORMAT,
        )
        self.assertEqual(
            tuple(field.wire_position for field in policy.fields),
            tuple(range(1, BATTLE_GAME_OVER_REPLY_FIELD_COUNT + 1)),
        )
        self.assertEqual(policy.packet_count, 1)
        self.assertEqual(
            policy.historical_ordering, BATTLE_GAME_OVER_HISTORICAL_ORDERING
        )
        self.assertEqual(
            tuple(row[0] for row in BATTLE_GAME_OVER_FIELD_TABLE),
            tuple(range(1, BATTLE_GAME_OVER_REPLY_FIELD_COUNT + 1)),
        )
        self.assertEqual(
            policy.unlock_names, BATTLE_GAME_OVER_UNLOCK_NAMES
        )
        self.assertEqual(
            policy.answered_request, BATTLE_GAME_OVER_REGISTERED_REQUEST
        )
        # Every position carries its own provenance record, at v12, and none of
        # them may be blank.
        for field in policy.fields:
            self.assertTrue(field.provenance.reason)
            self.assertTrue(field.provenance.source)
            self.assertTrue(field.provenance.field_contract)
            self.assertIn(field.provenance.origin, VALID_ORIGINS)
            self.assertEqual(
                field.provenance.compatibility_version, PROFILE_ID_V12
            )
            # Nothing may be dressed up as recovered or as an echo: this edge has
            # no recovered value and no proven request/reply correspondence.
            self.assertEqual(field.provenance.origin, "synthetic_compatibility")
            self.assertTrue(field.provenance.replaceable)
        for provenance in (policy.reply_provenance, policy.ordering_provenance):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V12)
            self.assertTrue(provenance.replaceable)
        # The values themselves, and that they cannot be edited into something
        # else without the wire types noticing.
        self.assertEqual(policy.reply_fields(), (0, b""))
        # No position may silently upgrade its own provenance: the type refuses
        # every origin except the labelled constant.
        first = policy.fields[0]
        with self.assertRaises(ValueError):
            dataclasses.replace(first, provenance=dataclasses.replace(
                first.provenance, origin="deterministic_recovered"))
        with self.assertRaises(ValueError):
            dataclasses.replace(first, value_origin="request_echo")
        with self.assertRaises(ValueError):
            dataclasses.replace(first, wire_type="p")
        # And exactly one profile may carry it: every other version refuses it,
        # so a v1-v11 server can never grow the reply.
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9,
                        get_profile_v10, get_profile_v11):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(profile, battle_game_over_reply=policy)
        # The active profile requires the policy, so dropping it is refused too.
        with self.assertRaises(ValueError):
            dataclasses.replace(get_profile(), battle_game_over_reply=None)

    def test_battle_start_reply_policy_contract(self) -> None:
        policy = get_profile().battle_start_reply
        assert policy is not None
        v11_policy = get_profile_v11().battle_start_reply
        assert v11_policy is not None
        self.assertEqual(policy.introduced_in, PROFILE_ID_V17)
        self.assertEqual(v11_policy.introduced_in, PROFILE_ID_V11)
        self.assertEqual(policy.reply_format, BATTLE_START_REPLY_FORMAT)
        self.assertEqual(policy.request_format, BATTLE_START_REQUEST_FORMAT)
        self.assertEqual(len(policy.fields), BATTLE_START_REPLY_FIELD_COUNT)
        self.assertEqual(policy.fields[0].wire_type + "".join(
            field.wire_type for field in policy.fields[1:]
        ), BATTLE_START_REPLY_FORMAT)
        self.assertEqual(policy.packet_count, 1)
        self.assertEqual(
            policy.historical_ordering, BATTLE_START_HISTORICAL_ORDERING
        )
        # The table the policy is built from is the same 19 rows: a position may
        # not exist in one and not the other.
        self.assertEqual(
            tuple(row[0] for row in BATTLE_START_FIELD_TABLE),
            tuple(range(1, BATTLE_START_REPLY_FIELD_COUNT + 1)),
        )
        # Every position carries its own provenance record and none of them may
        # be blank.  In v17 only position 7 moves to v17; the other 18 stay v11.
        for field in policy.fields:
            self.assertTrue(field.provenance.reason)
            self.assertTrue(field.provenance.source)
            self.assertTrue(field.provenance.field_contract)
            self.assertIn(field.provenance.origin, VALID_ORIGINS)
            expected_version = (
                PROFILE_ID_V17
                if field.wire_position == BATTLE_START_SYNC_COUNT_POSITION
                else PROFILE_ID_V11
            )
            self.assertEqual(
                field.provenance.compatibility_version, expected_version
            )
        # The v11 object itself keeps every field at v11 (v1..v16 freeze).
        for field in v11_policy.fields:
            self.assertEqual(
                field.provenance.compatibility_version, PROFILE_ID_V11
            )
        # The four grades, and which positions may use which.  Nothing may be
        # dressed up as recovered: exactly one position is.
        recovered = [
            field.wire_position
            for field in policy.fields
            if field.provenance.origin == "deterministic_recovered"
        ]
        self.assertEqual(recovered, [5])
        self.assertEqual(
            frozenset(recovered), BATTLE_START_RECOVERED_CONSTANT_POSITIONS
        )
        self.assertEqual(
            policy.fields[4].constant_value, BATTLE_START_PVE_OR_PVP_TYPE
        )
        echo = [
            field.wire_position
            for field in policy.fields
            if field.value_origin == BATTLE_START_VALUE_ECHO
        ]
        self.assertEqual(echo, list(range(8, 18)))
        counter = [
            field.wire_position
            for field in policy.fields
            if field.value_origin == BATTLE_START_VALUE_COUNTER
        ]
        self.assertEqual(counter, [1, BATTLE_START_SYNC_COUNT_POSITION])
        constant = [
            field.wire_position
            for field in policy.fields
            if field.value_origin == BATTLE_START_VALUE_CONSTANT
        ]
        self.assertEqual(constant, [2, 3, 4, 5, 6, 18, 19])
        # TASK-0237 adds `request_seed` for v19's derived position 6.  The v11 /
        # v17 / v18 policies above carry none of it: their own position lists are
        # asserted unchanged right above (and below).
        self.assertEqual(BATTLE_START_VALUE_ORIGINS, frozenset(
            {BATTLE_START_VALUE_CONSTANT, BATTLE_START_VALUE_ECHO,
             BATTLE_START_VALUE_COUNTER, BATTLE_START_VALUE_REQUEST_SEED}
        ))
        # The v11 policy is the frozen predecessor: position 7 still echoes.
        v11_echo = [
            field.wire_position
            for field in v11_policy.fields
            if field.value_origin == BATTLE_START_VALUE_ECHO
        ]
        self.assertEqual(v11_echo, list(range(7, 18)))
        v11_counter = [
            field.wire_position
            for field in v11_policy.fields
            if field.value_origin == BATTLE_START_VALUE_COUNTER
        ]
        self.assertEqual(v11_counter, [1])
        # The three visible choices TASK-0139 said must be taken for the battle to
        # be playable, and the seed that is neutral for this chapter's scene.
        self.assertIs(policy.fields[1].constant_value, False)
        self.assertIs(policy.fields[2].constant_value, False)
        self.assertEqual(policy.fields[3].constant_value, BATTLE_START_SELF_GROUP)
        self.assertEqual(policy.fields[5].constant_value, BATTLE_START_RAW_RANDOM_SEED)
        self.assertEqual(BATTLE_START_RAW_RANDOM_SEED, 3278 + 86 * 1)
        # The rule that keeps the archive honest: a value with no producer says so.
        self.assertEqual(policy.fields[1].provenance.origin, "synthetic_compatibility")
        self.assertEqual(policy.fields[5].provenance.origin, "synthetic_compatibility")
        # TASK-0182: position 7 is a per-reply counter whose provenance is a
        # labelled compatibility choice, not a `request_echo`.
        self.assertEqual(
            policy.fields[BATTLE_START_SYNC_COUNT_POSITION - 1].value_origin,
            BATTLE_START_VALUE_COUNTER,
        )
        self.assertEqual(
            policy.fields[BATTLE_START_SYNC_COUNT_POSITION - 1].provenance.origin,
            "synthetic_compatibility",
        )
        self.assertEqual(
            policy.field_origins()["dSyncCount"], "synthetic_compatibility"
        )
        self.assertEqual(
            policy.value_origins()["dSyncCount"], BATTLE_START_VALUE_COUNTER
        )
        self.assertEqual(
            v11_policy.field_origins()["dSyncCount"], "request_echo"
        )
        self.assertEqual(
            v11_policy.value_origins()["dSyncCount"], BATTLE_START_VALUE_ECHO
        )
        self.assertIn("D-129", policy.reply_provenance.reason)
        self.assertIn("isUseSfSvr", policy.reply_provenance.reason)
        self.assertIs(policy.reply_provenance.replaceable, True)

    def test_battle_start_reply_policy_is_refused_by_every_other_version(self) -> None:
        policy = get_profile().battle_start_reply
        assert policy is not None
        # The format is the client's own table, so a reply that could not be read
        # back is refused rather than sent.
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, reply_format="N" * BATTLE_START_REPLY_FIELD_COUNT)
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, request_format="NNNN")
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, historical_ordering="recovered")
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, fields=policy.fields[:-1])
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                reply_provenance=dataclasses.replace(
                    policy.reply_provenance, origin="official_recovered"
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                reply_provenance=dataclasses.replace(
                    policy.reply_provenance,
                    compatibility_version=PROFILE_ID_V10,
                ),
            )
        # No position may quietly upgrade its own provenance: the seed is not a
        # recovered value and the counter is not a constant.
        seed = policy.fields[5]
        self.assertEqual(seed.value_origin, BATTLE_START_VALUE_CONSTANT)
        with self.assertRaises(ValueError):
            dataclasses.replace(
                seed,
                provenance=dataclasses.replace(
                    seed.provenance, origin="deterministic_recovered"
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy.fields[0], constant_value=1_700_000_000
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy.fields[1],
                provenance=dataclasses.replace(
                    policy.fields[1].provenance, origin="request_echo"
                ),
            )
        # ... and neither may a request-echo field claim to be a constant.
        # (Position 8 is still an echo; position 7 is the v17 counter now.)
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy.fields[7],
                provenance=dataclasses.replace(
                    policy.fields[7].provenance, origin="synthetic_compatibility"
                ),
            )
        # The policy's own version identity is enforced: a v17 policy cannot be
        # relabelled as v11 (its position 7 would then carry a v17 provenance
        # under a v11 identity), and a v11 policy cannot be relabelled as v17.
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, introduced_in=PROFILE_ID_V11)
        v11_policy = get_profile_v11().battle_start_reply
        assert v11_policy is not None
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                fields=v11_policy.fields,
            )
        # No earlier version may be handed the policy either: the profile's own
        # lineage invariants refuse it, so a v1-v10 server can never grow a reply.
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9,
                        get_profile_v10):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(profile, battle_start_reply=policy)
        # TASK-0182: v11..v16 must refuse the v17 policy (their login stream and
        # their battle-start echo are frozen), and v17 must refuse the v11 one.
        for builder in (get_profile_v11, get_profile_v12, get_profile_v13,
                        get_profile_v14, get_profile_v15, get_profile_v16):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(profile, battle_start_reply=policy)
        with self.assertRaises(ValueError):
            dataclasses.replace(get_profile_v17(), battle_start_reply=v11_policy)
        # And the active profile may not be handed a foreign shape: it requires
        # the policy, so dropping it is refused too.
        with self.assertRaises(ValueError):
            dataclasses.replace(get_profile(), battle_start_reply=None)

    def test_battle_start_reply_refuses_a_request_it_cannot_answer(self) -> None:
        policy = get_profile().battle_start_reply
        assert policy is not None
        request = (False, 0, 86, 101010.0, 2.0, 0.0, 1.0,
                   0.0, 0.0, 0.0, 0.0, 0.0, b"")
        # A reply is one request's answer, so a request of the wrong shape or a
        # non-zero-able start time is refused instead of half-echoed.
        with self.assertRaises(ValueError):
            policy.reply_fields(request_fields=request[:-1], sf_start_time=1)
        with self.assertRaises(ValueError):
            policy.reply_fields(request_fields=request, sf_start_time=0)
        with self.assertRaises(ValueError):
            policy.reply_fields(request_fields=request, sf_start_time=True)
        with self.assertRaises(ValueError):
            policy.reply_fields(
                request_fields=request, sf_start_time=2 ** 32
            )
        # A request slot the reply must echo is validated against the reply's own
        # wire type, not against the request's spelling.  `dSyncCount` is no
        # longer echoed in v17, so the bad value goes in `p1` (position 8).
        bad_p1 = (False, 0, 86, "101010", *request[4:])
        with self.assertRaises(ValueError):
            policy.reply_fields(request_fields=bad_p1, sf_start_time=1)
        # TASK-0182: the request's own `dSyncCount` is ignored -- a second battle
        # gets a fresh counter even when the client repeats its constant 0.
        ignored_sync_count = (request[0], 99, request[2], *request[3:])
        ignored_fields = policy.reply_fields(
            request_fields=ignored_sync_count, sf_start_time=7
        )
        self.assertEqual(ignored_fields[6], 7)
        # The assembled reply is exactly what the recovered format reads back:
        # every position, in order, with the echoed count fields unchanged.
        fields = policy.reply_fields(request_fields=request, sf_start_time=7)
        self.assertEqual(len(fields), BATTLE_START_REPLY_FIELD_COUNT)
        self.assertEqual(fields[0], 7)
        self.assertEqual(fields[6], 7)
        self.assertEqual(fields[7:16], request[3:12])
        self.assertEqual(fields[16], request[12])
        self.assertEqual(fields[17], b"")
        self.assertEqual(fields[18], b"")

    def test_chapter_task_init_policy_contract(self) -> None:
        policy = get_profile().chapter_task_init
        assert policy is not None
        # S2C385 initChapterTaskStart + S2C387 initChapterTaskEnd: two empty
        # bodies, in the order the client's own code forces.
        self.assertEqual(
            policy.packets(),
            (("initChapterTaskStart", ()), ("initChapterTaskEnd", ())),
        )
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(policy.container_policy, CHAPTER_TASK_CONTAINER_POLICY)
        self.assertEqual(policy.row_count, CHAPTER_TASK_ROW_COUNT)
        self.assertEqual(policy.row_count, 0)
        self.assertEqual(
            policy.historical_ordering, CHAPTER_TASK_HISTORICAL_ORDERING
        )
        for provenance in (
            policy.container_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V9)
            self.assertTrue(provenance.replaceable)
            self.assertTrue(provenance.reason)
        # The container is the client's own initialiser, and the report must keep
        # saying so -- including that no task row is sent.
        self.assertIn("initChapterTaskStart", policy.container_provenance.reason)
        self.assertIn(
            "pkgChapterTaskMgr.lua:62", policy.container_provenance.source
        )
        self.assertIn("isTaskReceived", policy.container_provenance.source)
        self.assertIn("doFinishSwitch", policy.container_provenance.source)
        self.assertIn("D-21", policy.container_provenance.reason)
        # The RISK the empty container carries is stated, not hidden: the client
        # answers "no" to every chapter task until a real producer is recovered.
        self.assertIn("isTaskReceived", policy.container_provenance.risk)
        self.assertIs(policy.container_provenance.replaceable, True)

    def test_chapter_task_init_policy_is_refused_by_every_other_version(self) -> None:
        policy = get_profile().chapter_task_init
        assert policy is not None
        # A row would assert a specific chapter task of a specific chapter is
        # received/completed; this archive has none (D-21), so the policy's own
        # constructor refuses one.
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, row_count=1)
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, container_policy="invented_rows")
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, historical_ordering="recovered")
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance, origin="recovered_archive"
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance,
                    compatibility_version=PROFILE_ID_V8,
                ),
            )
        # No earlier version may be handed the policy either: the profile's own
        # lineage invariants refuse it, so a v1-v8 server can never grow the pair.
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8):
            profile = builder()
            with self.subTest(profile=profile.profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(profile, chapter_task_init=policy)
        # And the active profile may not be handed a foreign shape: it requires
        # the policy, so dropping it is refused too.
        with self.assertRaises(ValueError):
            dataclasses.replace(get_profile(), chapter_task_init=None)

    def test_experience_task_init_policy_contract(self) -> None:
        policy = get_profile().experience_task_init
        assert policy is not None
        # S2C1307 returnTaskStart + S2C1309 returnTaskEnd: two empty bodies, in the
        # order the client's own code forces.
        self.assertEqual(
            policy.packets(),
            (("returnTaskStart", ()), ("returnTaskEnd", ())),
        )
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(policy.container_policy, EXPERIENCE_TASK_CONTAINER_POLICY)
        self.assertEqual(policy.row_count, EXPERIENCE_TASK_ROW_COUNT)
        self.assertEqual(policy.row_count, 0)
        self.assertEqual(
            policy.historical_ordering, EXPERIENCE_TASK_HISTORICAL_ORDERING
        )
        for provenance in (
            policy.container_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V8)
            self.assertTrue(provenance.replaceable)
            self.assertTrue(provenance.reason)
        # The container is the client's own initialiser, and the report must keep
        # saying so -- including that no task row is sent.
        self.assertIn("returnTaskStart", policy.container_provenance.reason)
        self.assertIn(
            "le_tbTaskByChapterId[dChapterId]", policy.container_provenance.source
        )
        self.assertIn("Panel/btnDetail", policy.container_provenance.source)
        self.assertIn("D-21", policy.container_provenance.reason)
        self.assertIn("onCountdown", policy.container_provenance.source)
        # The send position is synthetic ordering, not a historical claim.
        self.assertIn("S2C283", policy.ordering_provenance.reason)
        self.assertIn("1308", policy.ordering_provenance.reason)

    def test_experience_task_init_policy_refuses_a_foreign_shape(self) -> None:
        policy = get_profile().experience_task_init
        assert policy is not None
        for replace, message in (
            ({"row_count": 1}, "empty"),
            ({"container_policy": "guessed"}, "container policy"),
            ({"historical_ordering": "known"}, "ordering"),
        ):
            with self.subTest(**replace):
                with self.assertRaisesRegex(ValueError, message):
                    dataclasses.replace(policy, **replace)
        with self.assertRaisesRegex(ValueError, "v8"):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance,
                    compatibility_version=PROFILE_ID_V7,
                ),
            )

    def test_recharge_activity_policy_contract(self) -> None:
        policy = get_profile().recharge_activity
        assert policy is not None
        # S2C1014 returnACRechargeDataStart(dRechargeStart, bReset): the only
        # payload is the activity total plus the client's own reset flag.
        self.assertEqual(
            policy.start_fields(), (RECHARGE_ACTIVITY_RECHARGE_START, True)
        )
        self.assertEqual(
            policy.recharge_start_policy, RECHARGE_ACTIVITY_RECHARGE_START_POLICY
        )
        self.assertTrue(policy.reset_rows)
        self.assertEqual(
            policy.historical_ordering, RECHARGE_ACTIVITY_HISTORICAL_ORDERING
        )
        for provenance in (
            policy.container_provenance,
            policy.recharge_start_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V6)
            self.assertTrue(provenance.replaceable)
            self.assertTrue(provenance.reason)
        # The container is the client's own initialiser, and the report must keep
        # saying so.
        self.assertIn(
            "initRechargeStart", policy.container_provenance.reason
        )
        self.assertIn(
            "tbInfo.bHadGet", policy.container_provenance.source
        )
        self.assertIn("D-21", policy.recharge_start_provenance.reason)
        # The send position is synthetic ordering, not a historical claim.
        self.assertIn(
            "S2C283", policy.ordering_provenance.reason
        )

    def test_chapter_item_init_policy_contract(self) -> None:
        policy = get_profile_v7().chapter_item_init
        assert policy is not None
        # S2C389 initChapterItemStart + S2C391 initChapterItemEnd: two empty
        # bodies, in the order the client's own code forces.
        self.assertEqual(
            policy.packets(),
            (("initChapterItemStart", ()), ("initChapterItemEnd", ())),
        )
        self.assertEqual(policy.packet_count, 2)
        self.assertEqual(policy.container_policy, CHAPTER_ITEM_CONTAINER_POLICY)
        self.assertEqual(policy.row_count, CHAPTER_ITEM_ROW_COUNT)
        self.assertEqual(
            policy.historical_ordering, CHAPTER_ITEM_HISTORICAL_ORDERING
        )
        for provenance in (
            policy.container_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V7)
            self.assertTrue(provenance.replaceable)
            self.assertTrue(provenance.reason)
        # The container is the client's own initialiser, and the report must keep
        # saying so -- including that no row is sent.
        self.assertIn(
            "initChapterItemStart", policy.container_provenance.reason
        )
        self.assertIn(
            "le_tbChapterItemInfo[dChapterId]", policy.container_provenance.source
        )
        self.assertIn("D-21", policy.container_provenance.reason)
        self.assertIn("isItemFightOver", policy.container_provenance.source)
        # The send position is synthetic ordering, not a historical claim.
        self.assertIn("S2C283", policy.ordering_provenance.reason)
        self.assertIn("390", policy.ordering_provenance.reason)

    def test_chapter_item_init_policy_refuses_a_foreign_shape(self) -> None:
        policy = get_profile_v7().chapter_item_init
        assert policy is not None
        for replace, message in (
            ({"row_count": 1}, "empty"),
            ({"container_policy": "guessed"}, "container policy"),
            ({"historical_ordering": "known"}, "ordering"),
        ):
            with self.subTest(**replace):
                with self.assertRaisesRegex(ValueError, message):
                    dataclasses.replace(policy, **replace)
        with self.assertRaisesRegex(ValueError, "v7"):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance,
                    compatibility_version=PROFILE_ID_V6,
                ),
            )

    def test_team_snapshot_policy_contract(self) -> None:
        policy = get_profile_v5().team_snapshot
        assert policy is not None
        # The current preset is a labelled compatibility minimum inside the
        # client's own legal set -- never a recovered value.
        self.assertEqual(policy.current_group, TEAM_SNAPSHOT_CURRENT_GROUP)
        self.assertEqual(policy.current_group, 1)
        self.assertEqual(
            policy.current_group_policy, TEAM_SNAPSHOT_CURRENT_GROUP_POLICY
        )
        self.assertEqual(policy.empty_slot, "")
        self.assertEqual(policy.historical_ordering, TEAM_SNAPSHOT_HISTORICAL_ORDERING)
        # S2C36 returnTeamStart and S2C48 returnSelectMagicStart are empty bodies.
        self.assertEqual(policy.team_start_fields(), ())
        self.assertEqual(policy.select_magic_start_fields(), ())
        # S2C37 returnTeamInfo: 1 group id + 4 equips + 1 god weapon + 11 cards,
        # every slot the client's own empty-slot literal.
        info = policy.team_info_fields()
        self.assertEqual(len(info), 17)
        self.assertEqual(
            len(info) - 1, TEAM_SNAPSHOT_EQUIP_SLOTS + 1 + TEAM_SNAPSHOT_CARD_SLOTS
        )
        self.assertEqual(info, (1,) + ("",) * 16)
        # S2C35 returnGroupId: exactly one uint32.
        self.assertEqual(policy.group_id_fields(), (1,))
        for provenance in (
            policy.container_provenance,
            policy.current_group_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V5)
            self.assertTrue(provenance.replaceable)
            self.assertTrue(provenance.reason)
        # The snapshot is a labelled assertion, never a recovered value.
        self.assertIn(
            "NOT a recovered historical snapshot", policy.container_provenance.reason
        )
        self.assertIn("D-33", policy.container_provenance.evidence_grade)
        self.assertIn("D-33", policy.current_group_provenance.source)
        self.assertIn("recovered", policy.current_group_provenance.reason)
        # The intra-block order is forced by the client's own CHANGE_TEAM
        # dispatch, not chosen.
        self.assertIn("CHANGE_TEAM", policy.ordering_provenance.reason)
        self.assertIn("unknown", policy.ordering_provenance.reason.lower())
        # No card, equipment, god-weapon or group value outside the client's own
        # legal set may be introduced by this policy.
        for empty in ("rows", None, "0"):
            with self.assertRaises(ValueError):
                dataclasses.replace(policy, empty_slot=empty)
        for bad_group in (0, 6, -1):
            with self.assertRaises(ValueError):
                dataclasses.replace(policy, current_group=bad_group)
        with self.assertRaises(ValueError):
            dataclasses.replace(policy, historical_ordering="recovered")
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance, origin="official_recovered"
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                container_provenance=dataclasses.replace(
                    policy.container_provenance,
                    compatibility_version=PROFILE_ID_V4,
                ),
            )
        self.assertEqual(TEAM_SNAPSHOT_MAX_GROUP, 5)
        self.assertEqual(
            policy.current_group,
            min(range(1, TEAM_SNAPSHOT_MAX_GROUP + 1)),
        )

    def test_story_start_policy_contract(self) -> None:
        policy = get_profile().story_start
        assert policy is not None
        self.assertEqual(policy.story_list, "empty")
        self.assertEqual(policy.story_list, STORY_START_LIST_POLICY)
        self.assertEqual(policy.historical_ordering, "unknown")
        self.assertEqual(policy.historical_ordering, STORY_START_HISTORICAL_ORDERING)
        # S2C411 has an empty body: zero wire fields, nothing per story id.
        self.assertEqual(policy.protocol_fields(), ())
        for provenance in (policy.snapshot_provenance, policy.ordering_provenance):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V4)
            self.assertTrue(provenance.replaceable)
            self.assertTrue(provenance.reason)
        # The snapshot is a labelled assertion, never a recovered value.
        self.assertIn("NOT a recovered historical snapshot", policy.snapshot_provenance.reason)
        self.assertIn("D-05", policy.snapshot_provenance.reason)
        # ... and the ordering claim names the historical gap explicitly.
        self.assertIn("unknown", policy.ordering_provenance.reason.lower())
        # No row-carrying family member may be introduced by this policy.
        with self.assertRaises(ValueError):
            StoryStartPolicy(
                story_list="rows",
                historical_ordering=STORY_START_HISTORICAL_ORDERING,
                snapshot_provenance=policy.snapshot_provenance,
                ordering_provenance=policy.ordering_provenance,
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy, historical_ordering="recovered",
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                snapshot_provenance=dataclasses.replace(
                    policy.snapshot_provenance, origin="official_recovered"
                ),
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                policy,
                snapshot_provenance=dataclasses.replace(
                    policy.snapshot_provenance,
                    compatibility_version=PROFILE_ID_V3,
                ),
            )
        # The story family is not part of the login sync index (D-42 section 5).
        self.assertEqual(policy.ordering_provenance.compatibility_version, PROFILE_ID_V4)

    def test_mystery_shop_policy_contract(self) -> None:
        policy = get_profile().mystery_shop
        assert policy is not None
        self.assertEqual(policy.shop_type, 1)
        self.assertEqual(policy.time_policy, "reuse_server_time_dtime")
        self.assertEqual(policy.availability, "closed")
        self.assertEqual(policy.goods_count, 0)
        self.assertFalse(policy.snapshot_end_sent)
        self.assertEqual(policy.historical_ordering, "unknown")
        self.assertEqual(
            policy.protocol_fields(server_time_dtime=1_700_000_123),
            (1, 1_700_000_123),
        )
        # Exact-reuse invariant: the shop dTime is the resolved S2C0 dTime value.
        server_time_policy = get_profile().server_time
        assert server_time_policy is not None
        server_fields = server_time_policy.protocol_fields(
            now_unix=1_700_000_123, server_started_unix=1_700_000_000
        )
        self.assertEqual(
            policy.protocol_fields(server_time_dtime=server_fields[0])[1],
            server_fields[0],
        )
        self.assertEqual(
            policy.shop_type_provenance.origin, "deterministic_recovered"
        )
        self.assertFalse(policy.shop_type_provenance.replaceable)
        for provenance in (
            policy.time_provenance,
            policy.availability_provenance,
            policy.goods_provenance,
            policy.snapshot_end_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertTrue(provenance.replaceable)
        for provenance in (
            policy.shop_type_provenance,
            policy.time_provenance,
            policy.availability_provenance,
            policy.goods_provenance,
            policy.snapshot_end_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V3)
        # The shop dTime keeps the p (double) contract, not the uint32 contract.
        with self.assertRaises(ValueError):
            policy.protocol_fields(server_time_dtime=-1)
        with self.assertRaises(ValueError):
            policy.protocol_fields(server_time_dtime=2**53)
        with self.assertRaises(ValueError):
            policy.protocol_fields(server_time_dtime=1_700_000_123.5)
        with self.assertRaises(ValueError):
            policy.protocol_fields(server_time_dtime=True)

    def test_v18_lineage_inherits_v17_and_only_adds_the_box_replay(self) -> None:
        # TASK-0202: v18 adds one login-time policy object and nothing else.
        # Every earlier profile refuses it, so v1..v17 keep the stream they had.
        v17 = get_profile_v17()
        v18 = get_profile_v18()
        self.assertEqual(v18.profile_id, PROFILE_ID_V18)
        self.assertEqual(v18.parent_profile_id, PROFILE_ID_V17)
        self.assertIsNone(v17.chapter_box_replay)
        policy = v18.chapter_box_replay
        self.assertIsNotNone(policy)
        for attribute in (
            "role_values",
            "sign_catalogs",
            "sign_state_template",
            "scene_state_template",
            "active_soul_rules",
            "server_time",
            "mystery_shop",
            "story_start",
            "team_snapshot",
            "recharge_activity",
            "chapter_item_init",
            "experience_task_init",
            "chapter_task_init",
            "chapter_machine_init",
            "battle_start_reply",
            "battle_game_over_reply",
            "prison_fight",
            "chapter_fight_replay",
            "drawcard_unlock",
        ):
            self.assertIs(
                getattr(v18, attribute),
                getattr(v17, attribute),
                msg=f"{attribute} must be the very same v17 object",
            )
        # pkgGuankaMapNcMgr PROTO 0.0..0.2: an empty archive sends nothing at
        # all, otherwise the block is Start, Info per stored chest, End.
        assert policy is not None
        self.assertEqual(policy.packets(()), ())
        self.assertEqual(
            policy.packets(((1, 2),)),
            (
                ("initChapterBoxStart", ()),
                ("initChapterBoxInfo", (1, 2)),
                ("initChapterBoxEnd", ()),
            ),
        )
        for builder in (
            get_profile_v1,
            get_profile_v2,
            get_profile_v3,
            get_profile_v4,
            get_profile_v5,
            get_profile_v6,
            get_profile_v7,
            get_profile_v8,
            get_profile_v9,
            get_profile_v10,
            get_profile_v11,
            get_profile_v12,
            get_profile_v13,
            get_profile_v14,
            get_profile_v15,
            get_profile_v16,
            get_profile_v17,
        ):
            with self.subTest(profile=builder().profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(
                        builder(), chapter_box_replay=policy
                    )

    def test_server_time_policy_values_and_uint32_contract(self) -> None:
        policy = get_profile().server_time
        assert policy is not None
        fields = policy.protocol_fields(
            now_unix=1_700_000_123, server_started_unix=1_700_000_000
        )
        self.assertEqual(fields, (1_700_000_123, 0, 0, 1_700_000_000))
        self.assertEqual(
            {
                "dTime": policy.time_provenance.origin,
                "dClock": policy.clock_provenance.origin,
                "dPowerRecoverTime": policy.power_recover_provenance.origin,
                "dOpenTime": policy.open_time_provenance.origin,
            },
            {
                "dTime": "deterministic_recovered",
                "dClock": "synthetic_compatibility",
                "dPowerRecoverTime": "synthetic_compatibility",
                "dOpenTime": "synthetic_compatibility",
            },
        )
        self.assertEqual(policy.time_policy, "current_unix_time_at_send")
        self.assertEqual(policy.open_time_policy, "revival_process_start_unix")
        self.assertEqual(policy.historical_ordering, "unknown")
        self.assertEqual(
            policy.ordering_provenance.origin, "synthetic_compatibility"
        )
        self.assertTrue(policy.time_provenance.replaceable)
        for provenance in (
            policy.time_provenance,
            policy.clock_provenance,
            policy.power_recover_provenance,
            policy.open_time_provenance,
            policy.ordering_provenance,
        ):
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V2)
        with self.assertRaises(ValueError):
            policy.protocol_fields(now_unix=2**32, server_started_unix=0)
        with self.assertRaises(ValueError):
            policy.protocol_fields(now_unix=0, server_started_unix=-1)


class ProfileV19TypeAwareBattleStartTests(unittest.TestCase):
    """TASK-0237: v19 is an opt-in successor whose S2C 89 reply is type-aware.

    The 86 reply must stay byte-identical to v18.  The 73 reply must echo the
    request type in position 5 and derive 3278 + type * 1 in position 6.  Every
    other position -- and every other policy object -- is v18's.
    """

    #: The RUN-0040 chapter-map request (86 / fight 101010) frozen in
    #: test_codec.py, repeated here so this module stands alone.
    REQUEST_86 = (
        False, 0, 86, 101010.0, 2.0, 0.0, 1.0,
        0.0, 0.0, 0.0, 0.0, 0.0, b"",
    )
    #: A locktower request: type 73, floor 5, dFightType 0, everything else 0.
    REQUEST_73 = (
        False, 0, 73, 5.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, b"",
    )

    def setUp(self) -> None:
        self.v18 = get_profile_v18()
        self.v19 = get_profile_v19()
        self.v18_policy = self.v18.battle_start_reply
        self.v19_policy = self.v19.battle_start_reply
        assert self.v18_policy is not None
        assert self.v19_policy is not None

    def test_v19_is_the_opt_in_successor_of_v18(self) -> None:
        # v19 stays opt-in; TASK-0247 moved the DEFAULT to v24, whose v23 parent
        # is a v21 successor (so this v19 contract object itself is unchanged).
        self.assertEqual(get_profile().profile_id, PROFILE_ID_V24)
        self.assertEqual(self.v19.profile_id, PROFILE_ID_V19)
        self.assertEqual(self.v19.parent_profile_id, PROFILE_ID_V18)
        self.assertEqual(self.v18.parent_profile_id, PROFILE_ID_V17)
        # Every login-time policy object is the v18 object by identity, so a
        # v19 login stream is the v18 stream packet for packet.
        for name in (
            "role_values", "sign_catalogs", "sign_state_template",
            "scene_state_template", "active_soul_rules", "server_time",
            "mystery_shop", "story_start", "team_snapshot",
            "recharge_activity", "chapter_item_init", "experience_task_init",
            "chapter_task_init", "chapter_machine_init",
            "battle_game_over_reply", "prison_fight", "chapter_fight_replay",
            "drawcard_unlock", "chapter_box_replay",
        ):
            self.assertIs(getattr(self.v19, name), getattr(self.v18, name), name)
        self.assertIsNot(self.v19_policy, self.v18_policy)
        self.assertEqual(self.v19_policy.introduced_in, PROFILE_ID_V19)
        # Positions 1/2/3/4/7..19 are the very same field objects.
        for index, field in enumerate(self.v19_policy.fields):
            if field.wire_position in (BATTLE_START_TYPE_POSITION,
                                       BATTLE_START_SEED_POSITION):
                continue
            self.assertIs(field, self.v18_policy.fields[index])

    def test_the_86_reply_is_byte_identical_to_v18(self) -> None:
        for start_time in (1_700_000_001, 1_700_000_002):
            self.assertEqual(
                self.v19_policy.reply_fields(
                    request_fields=self.REQUEST_86, sf_start_time=start_time
                ),
                self.v18_policy.reply_fields(
                    request_fields=self.REQUEST_86, sf_start_time=start_time
                ),
            )
        # The frozen v18 19-value tuple, spelled out.
        self.assertEqual(
            self.v19_policy.reply_fields(
                request_fields=self.REQUEST_86, sf_start_time=100
            ),
            (100, False, False, 1, 86, 3364, 100,
             101010.0, 2.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0,
             b"", b"", b""),
        )

    def test_the_73_reply_echoes_the_type_and_derives_the_seed(self) -> None:
        reply = self.v19_policy.reply_fields(
            request_fields=self.REQUEST_73, sf_start_time=100
        )
        self.assertEqual(
            reply,
            (100, False, False, 1, 73, 3351, 100,
             5.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
             b"", b"", b""),
        )
        # Only positions 5 and 6 differ from the v18 answer to the same request.
        v18_reply = self.v18_policy.reply_fields(
            request_fields=self.REQUEST_73, sf_start_time=100
        )
        differing = [
            position
            for position, (left, right) in enumerate(
                zip(reply, v18_reply, strict=True), start=1
            )
            if left != right
        ]
        self.assertEqual(
            differing, [BATTLE_START_TYPE_POSITION, BATTLE_START_SEED_POSITION]
        )
        self.assertEqual(v18_reply[4], BATTLE_START_PVE_OR_PVP_TYPE)
        self.assertEqual(v18_reply[5], BATTLE_START_RAW_RANDOM_SEED)

    def test_the_reply_counter_stays_nonzero_and_changes_per_reply(self) -> None:
        first = self.v19_policy.reply_fields(
            request_fields=self.REQUEST_73, sf_start_time=100
        )
        second = self.v19_policy.reply_fields(
            request_fields=self.REQUEST_73, sf_start_time=101
        )
        for reply in (first, second):
            self.assertNotEqual(reply[0], 0)
            self.assertEqual(reply[6], reply[0])
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[6], 100)
        self.assertEqual(second[6], 101)

    def test_the_type_echo_and_the_seed_keep_their_provenance(self) -> None:
        type_field = self.v19_policy.fields[BATTLE_START_TYPE_POSITION - 1]
        seed_field = self.v19_policy.fields[BATTLE_START_SEED_POSITION - 1]
        # The client dispatch that makes position 5 decisive is recovered; the
        # field's own value origin is the request echo.
        self.assertEqual(type_field.value_origin, BATTLE_START_VALUE_ECHO)
        self.assertEqual(type_field.echo_index, BATTLE_START_REQUEST_TYPE_INDEX)
        self.assertEqual(type_field.provenance.origin, "request_echo")
        self.assertIn("deterministic_recovered",
                      type_field.provenance.evidence_grade)
        # The seed formula is recovered; k = 1 is a labelled choice.
        self.assertEqual(seed_field.value_origin,
                         BATTLE_START_VALUE_REQUEST_SEED)
        self.assertEqual(seed_field.seed_request_index,
                         BATTLE_START_REQUEST_TYPE_INDEX)
        self.assertEqual(seed_field.provenance.origin,
                         "synthetic_compatibility")
        self.assertEqual(
            seed_field.provenance.compatibility_version, PROFILE_ID_V19
        )
        self.assertEqual(
            self.v19_policy.value_origins()["pveOrPvpType"],
            BATTLE_START_VALUE_ECHO,
        )
        self.assertEqual(
            self.v19_policy.value_origins()["dRawRandomSeed"],
            BATTLE_START_VALUE_REQUEST_SEED,
        )
        self.assertEqual(
            self.v19_policy.seed_policy, BATTLE_START_TYPE_RANDOM_SEED_POLICY
        )
        self.assertEqual(
            self.v18_policy.seed_policy, BATTLE_START_RANDOM_SEED_POLICY
        )
        self.assertEqual(
            BATTLE_START_RANDOM_SEED_BASE + 73, 3351
        )
        for provenance in (type_field.provenance, seed_field.provenance):
            self.assertTrue(provenance.reason)
            self.assertTrue(provenance.source)
            self.assertTrue(provenance.field_contract)
            self.assertIn(provenance.origin, VALID_ORIGINS)
            self.assertTrue(provenance.replaceable)

    def test_the_two_contracts_cannot_be_mixed_or_relabelled(self) -> None:
        # A v18 profile may not be handed the v19 policy, and vice versa.
        with self.assertRaises(ValueError):
            dataclasses.replace(self.v18, battle_start_reply=self.v19_policy)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.v19, battle_start_reply=self.v18_policy)
        # The identity cannot be relabelled without the two changed fields
        # noticing (they carry v19 provenance and a v19-only origin).
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.v19_policy, introduced_in=PROFILE_ID_V17
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.v18_policy, introduced_in=PROFILE_ID_V19
            )
        # Position 6 may not be dressed up as recovered.
        seed = self.v19_policy.fields[BATTLE_START_SEED_POSITION - 1]
        with self.assertRaises(ValueError):
            dataclasses.replace(
                seed,
                provenance=dataclasses.replace(
                    seed.provenance, origin="deterministic_recovered"
                ),
            )
        # A derived seed needs a request slot and may not carry a constant.
        with self.assertRaises(ValueError):
            dataclasses.replace(seed, constant_value=BATTLE_START_RAW_RANDOM_SEED)
        with self.assertRaises(ValueError):
            dataclasses.replace(seed, seed_request_index=None)
        with self.assertRaises(ValueError):
            dataclasses.replace(seed, wire_type="p")


class ProfileV20BabelSettlementPushTests(unittest.TestCase):
    """TASK-0238: v20 adds exactly one policy -- the post-settlement S2C 580.

    It inherits every v19 object by identity (including the type-aware S2C 89
    policy), so a v20 login stream carries the v19 packets; `get_profile()`
    still returns v18 and no earlier profile may carry the new policy.
    """

    def setUp(self) -> None:
        self.v18 = get_profile_v18()
        self.v19 = get_profile_v19()
        self.v20 = get_profile_v20()
        self.push = self.v20.babel_settlement_push
        assert self.push is not None

    def test_v20_is_the_opt_in_successor_of_v19(self) -> None:
        self.assertEqual(get_profile().profile_id, PROFILE_ID_V24)
        self.assertEqual(self.v20.profile_id, PROFILE_ID_V20)
        self.assertEqual(self.v20.parent_profile_id, PROFILE_ID_V19)
        # Every other policy object -- login-time and request-triggered -- is
        # the v19 object by identity, so v20 adds no login packet.
        for name in (
            "role_values", "sign_catalogs", "sign_state_template",
            "scene_state_template", "active_soul_rules", "server_time",
            "mystery_shop", "story_start", "team_snapshot",
            "recharge_activity", "chapter_item_init", "experience_task_init",
            "chapter_task_init", "chapter_machine_init", "battle_start_reply",
            "battle_game_over_reply", "prison_fight", "chapter_fight_replay",
            "drawcard_unlock", "chapter_box_replay",
        ):
            self.assertIs(getattr(self.v20, name), getattr(self.v19, name), name)
        # ... and v19's objects are v18's where v19 changed nothing.
        self.assertIs(self.v20.battle_start_reply, self.v19.battle_start_reply)
        self.assertIs(self.v20.chapter_box_replay, self.v18.chapter_box_replay)
        # The active default and every earlier profile stay constructible with
        # no push policy at all.
        self.assertIsNone(self.v18.babel_settlement_push)
        self.assertIsNone(self.v19.babel_settlement_push)
        self.assertEqual(self.push.introduced_in, PROFILE_ID_V20)

    def test_the_push_policy_declares_the_recovered_s2c580_layout(self) -> None:
        self.assertEqual(self.push.wire_id, BABEL_SETTLEMENT_PUSH_WIRE_ID)
        self.assertEqual(self.push.reply_format, BABEL_SETTLEMENT_PUSH_FORMAT)
        self.assertEqual(
            self.push.field_names, BABEL_SETTLEMENT_PUSH_FIELD_NAMES
        )
        self.assertEqual(
            self.push.send_trigger, BABEL_SETTLEMENT_PUSH_SEND_TRIGGER
        )
        self.assertEqual(
            self.push.trigger_request, BABEL_SETTLEMENT_PUSH_TRIGGER_REQUEST
        )
        self.assertEqual(
            self.push.value_source, BABEL_SETTLEMENT_PUSH_VALUE_SOURCE
        )
        self.assertEqual(
            self.push.max_per_settlement,
            BABEL_SETTLEMENT_PUSH_MAX_PER_SETTLEMENT,
        )
        self.assertEqual(self.push.packet_count, 1)
        self.assertEqual(
            self.push.value_provenance.origin, "synthetic_compatibility"
        )
        self.assertEqual(
            self.push.ordering_provenance.origin, "synthetic_compatibility"
        )
        for provenance in (
            self.push.value_provenance, self.push.ordering_provenance
        ):
            self.assertEqual(
                provenance.compatibility_version, PROFILE_ID_V20
            )
            self.assertTrue(provenance.replaceable)
            self.assertIn(provenance.origin, VALID_ORIGINS)
            self.assertTrue(provenance.source)
            self.assertTrue(provenance.reason)

    def test_the_push_replays_only_the_overlay_values(self) -> None:
        self.assertEqual(self.push.push_fields((1, 0, 20, 0, 1)),
                         (1, 0, 20, 0, 1))
        for bad in (
            (1, 0, 20, 0),            # too few
            (1, 0, 20, 0, 1, 2),      # too many
            (1.5, 0, 20, 0, 1),       # not an int
            (True, 0, 20, 0, 1),      # bool is not a wire value
            (-1, 0, 20, 0, 1),        # outside uint32
        ):
            with self.subTest(values=bad):
                with self.assertRaises(ValueError):
                    self.push.push_fields(bad)

    def test_the_contracts_cannot_be_mixed_or_relabelled(self) -> None:
        # v18/v19 must not carry the v20 policy.
        for earlier in (self.v18, self.v19):
            with self.assertRaises(ValueError):
                dataclasses.replace(
                    earlier, babel_settlement_push=self.push
                )
        # v20 must carry it.
        with self.assertRaises(ValueError):
            dataclasses.replace(self.v20, babel_settlement_push=None)
        # The policy's identity cannot be relabelled.
        with self.assertRaises(ValueError):
            dataclasses.replace(self.push, introduced_in=PROFILE_ID_V19)
        # The values may not be dressed up as recovered.
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.push,
                value_provenance=dataclasses.replace(
                    self.push.value_provenance,
                    origin="deterministic_recovered",
                ),
            )
        # The recovered S2C 580 layout may not drift.
        with self.assertRaises(ValueError):
            dataclasses.replace(self.push, reply_format="NNNN")
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.push, trigger_request="netEnterPveBattle"
            )
        with self.assertRaises(ValueError):
            dataclasses.replace(self.push, max_per_settlement=2)

    def test_the_locktower_type_is_the_recovered_client_constant(self) -> None:
        self.assertEqual(BATTLE_START_LOCKTOWER_PVE_TYPE, 73)
        # The v19 type-aware reply is unchanged in v20: a 73 request still
        # answers 73/3351 and a 86 request still answers 86/3364.
        policy = self.v20.battle_start_reply
        assert policy is not None
        reply_73 = policy.reply_fields(
            request_fields=(
                False, 0, 73, 5.0, 0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 0.0, 0.0, b"",
            ),
            sf_start_time=100,
        )
        self.assertEqual(reply_73[4], 73)
        self.assertEqual(reply_73[5], 3351)
        reply_86 = policy.reply_fields(
            request_fields=(
                False, 0, 86, 101010.0, 2.0, 0.0, 1.0,
                0.0, 0.0, 0.0, 0.0, 0.0, b"",
            ),
            sf_start_time=100,
        )
        self.assertEqual(reply_86[4], 86)
        self.assertEqual(reply_86[5], 3364)


class ProfileV22TeamMaxFightTests(unittest.TestCase):
    """TASK-0245 / #27: v22 adds exactly the S2C 39 battle-power value."""

    def setUp(self) -> None:
        self.v21 = get_profile_v21()
        self.v22 = get_profile_v22()
        self.policy = self.v22.team_max_fight
        assert self.policy is not None

    def test_v22_is_the_default_and_only_adds_the_team_max_fight(self) -> None:
        # TASK-0247 moved the DEFAULT to v24, which inherits this very policy
        # object; the v22 object's own contract is unchanged.
        self.assertEqual(get_profile().profile_id, PROFILE_ID_V24)
        self.assertEqual(self.v22.profile_id, PROFILE_ID_V22)
        self.assertIs(get_profile().team_max_fight, self.policy)
        self.assertEqual(self.v22.parent_profile_id, PROFILE_ID_V21)
        self.assertEqual(self.v21.parent_profile_id, PROFILE_ID_V18)
        # Every other policy object -- login-time and request-triggered -- is
        # the v21 object by identity, so v22 adds exactly one login packet.
        for name in (
            "role_values", "sign_catalogs", "sign_state_template",
            "scene_state_template", "active_soul_rules", "server_time",
            "mystery_shop", "story_start", "team_snapshot",
            "recharge_activity", "chapter_item_init", "experience_task_init",
            "chapter_task_init", "chapter_machine_init", "battle_start_reply",
            "battle_game_over_reply", "prison_fight", "chapter_fight_replay",
            "drawcard_unlock", "chapter_box_replay",
            "babel_settlement_push", "mail_login",
        ):
            self.assertIs(getattr(self.v22, name), getattr(self.v21, name), name)
        self.assertIsNone(self.v21.team_max_fight)
        self.assertIsNotNone(self.v22.mail_login)

    def test_the_policy_declares_the_recovered_s2c39_contract(self) -> None:
        self.assertEqual(self.policy.wire_id, TEAM_MAX_FIGHT_WIRE_ID)
        self.assertEqual(self.policy.name, TEAM_MAX_FIGHT_NAME)
        self.assertEqual(self.policy.wire_format, TEAM_MAX_FIGHT_WIRE_FORMAT)
        self.assertEqual(
            self.policy.allowed_login_positions,
            TEAM_MAX_FIGHT_ALLOWED_POSITIONS,
        )
        self.assertEqual(self.policy.triggers, TEAM_MAX_FIGHT_TRIGGERS)
        self.assertEqual(self.policy.value_policy, TEAM_MAX_FIGHT_VALUE_POLICY)
        self.assertEqual(self.policy.fields(2836), (2836,))
        with self.assertRaises(ValueError):
            self.policy.fields(-1)
        with self.assertRaises(ValueError):
            self.policy.fields(2**32)
        self.assertEqual(
            self.policy.block_provenance.origin, "deterministic_recovered"
        )
        for provenance in (
            self.policy.value_provenance, self.policy.ordering_provenance
        ):
            self.assertEqual(provenance.origin, "synthetic_compatibility")
            self.assertEqual(provenance.compatibility_version, PROFILE_ID_V22)
            self.assertTrue(provenance.replaceable)
        self.assertFalse(self.policy.historical_values_recovered)
        self.assertFalse(self.policy.historical)

    def test_v18_to_v21_refuse_the_policy_and_v22_requires_it(self) -> None:
        for earlier in (
            get_profile_v18(), get_profile_v19(), get_profile_v20(), self.v21
        ):
            with self.subTest(profile=earlier.profile_id):
                with self.assertRaises(ValueError):
                    dataclasses.replace(
                        earlier, team_max_fight=self.policy
                    )
        with self.assertRaises(ValueError):
            dataclasses.replace(self.v22, team_max_fight=None)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.policy, introduced_in=PROFILE_ID_V21)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.policy, wire_id=38)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.policy, wire_format="NN")
        with self.assertRaises(ValueError):
            dataclasses.replace(
                self.policy,
                value_provenance=dataclasses.replace(
                    self.policy.value_provenance, origin="deterministic_recovered"
                ),
            )


if __name__ == "__main__":
    unittest.main()
