"""Durable account->role archive: atomicity, isolation and fail-closed policy.

Every test here uses its own temporary directory.  No test may touch, create or
remove the per-user archive that a real run would use.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import PROFILE_ID
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS
from revival_stub.play_profile import (
    PROFILE_MODE_INIT,
    PROFILE_MODE_RESUME,
    select_play_profile,
)
from revival_stub.role_state import REVIVAL_LOCAL_ROLE_SERIAL_BASE
from revival_stub.role_store import (
    ACCOUNT_KEY_DERIVATION,
    BAG_SORT_EVIDENCE_CLIENT_C2S,
    BAG_SORT_ORIGIN_LOCAL_OBSERVATION,
    IDENTITY_ORIGIN_LOCAL_TRANSACTION,
    INSPECTION_MIGRATED,
    INSPECTION_SERVED,
    KIND_FRESH_TEST,
    KIND_MEMORY,
    KIND_PLAY_PROFILE,
    KIND_PERSISTENT,
    SCHEMA_VERSION,
    STORE_FILENAME,
    STORY_EVIDENCE_ORDERED_SUCCESSOR,
    STORY_ORIGIN_LOCAL_OBSERVATION,
    CardInstanceWrite,
    RoleStore,
    RoleStoreError,
    RoleStoreMigrationError,
    RoleStoreUnusableError,
    TicketBalanceShortfallError,
    inspect_archive,
)
# The statement tuples are the *structure* definitions themselves; a fixture built from a
# copy of them would be a second definition, which is the thing this task is about.
from revival_stub.role_store import (  # noqa: E402
    _BAG_SCHEMA_STATEMENTS,
    _BASE_SCHEMA_STATEMENTS,
    _REQUIRED_STRUCTURE,
    _STORY_SCHEMA_STATEMENTS,
)
from revival_stub.role_store import (  # noqa: E402
    CHAPTER_EVENT_REWARD_ORIGIN_LOCAL_OBSERVATION,
    CHAPTER_EVENT_REWARD_ORIGIN_SCHEMA_BACKFILL,
    CHAPTER_EVENT_REWARD_PROVENANCE_MIGRATION,
    CHAPTER_EVENT_REWARD_PROVENANCE_TRANSACTION,
    CRYSTAL_BUCKET_NAMES,
    CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
)
from revival_stub import chapter_map_event as cme  # noqa: E402
from revival_stub.recovered_rewards import (  # noqa: E402
    FIGHT_SPECIAL_CARD_ORIGIN,
)
from revival_stub.tcp_stub import BAG_SORT_SLOT_CARD


# A synthetic SDK identity triple, standing in for one device's loginByToken.
IDENTITY = {
    "server_id": 1.0,
    "platform": "androio",
    "channel_id": "local-channel",
    "channel_sub_id": "0",
    "sdk_identity": "local-player",
}

#: The *play profile* name (not the compatibility profile id below): the one
#: `--play-profile` would be given, and the directory the save lives in.
PLAY_PROFILE_ID = "save-1"


def other_identity(**overrides) -> dict:
    values = dict(IDENTITY)
    values.update(overrides)
    return values


def store_schema_columns(data_dir: Path, table: str) -> list[sqlite3.Row]:
    """The archive's own column list, read from the file the store wrote."""

    connection = sqlite3.connect(str(data_dir / STORE_FILENAME))
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        connection.close()


class RoleStoreArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---------------------------------------------------------------- schema

    def test_new_archive_records_its_structure_and_derivation(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            self.assertEqual(store.kind, KIND_PERSISTENT)
            self.assertEqual(store.count(), 0)
            described = store.describe()
            self.assertEqual(described["role_store_schema_version"], SCHEMA_VERSION)
            self.assertEqual(described["role_store_derivation"], ACCOUNT_KEY_DERIVATION)
            self.assertEqual(described["role_count"], 0)
            # No earlier run's in-memory role may be presented as migrated.
            self.assertEqual(described["historical_role_records_migrated"], 0)
            self.assertEqual(
                store._meta("schema_version"), str(SCHEMA_VERSION)
            )
            self.assertEqual(store._meta("account_key_derivation"), ACCOUNT_KEY_DERIVATION)

    def test_a_second_creator_adopts_the_archive_instead_of_failing(self) -> None:
        """Two processes racing to create the same archive must not collide."""

        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            store.create_or_get(
                account_key=key, server_id=1.0, role_name="kept", career=1,
                create_time=1, compatibility_profile_id=PROFILE_ID,
            )
            # The path the second process takes after losing the race.
            store._create_schema()
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.lookup(key).role_name, "kept")

    def test_archive_is_created_inside_the_chosen_directory_only(self) -> None:
        RoleStore.at_directory(self.data_dir).close()
        self.assertEqual(
            {
                path.name
                for path in self.data_dir.iterdir()
                if path.is_file() and not path.name.endswith(("-wal", "-shm"))
            },
            {STORE_FILENAME},
        )

    def test_memory_archive_starts_empty_and_is_not_a_file(self) -> None:
        store = RoleStore.in_memory()
        try:
            self.assertEqual(store.kind, KIND_MEMORY)
            self.assertEqual(store.count(), 0)
            self.assertEqual(store.describe()["role_store_location"], ":memory:")
        finally:
            store.close()

    def test_fresh_test_archive_removes_its_temporary_directory(self) -> None:
        store = RoleStore.fresh_test()
        self.assertEqual(store.kind, KIND_FRESH_TEST)
        path = Path(store._location)
        self.assertTrue(path.is_file())
        store.close()
        self.assertFalse(path.parent.exists())

    # -------------------------------------------------------------- identity

    def test_account_key_is_stable_across_reopen(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            first = store.account_key(**IDENTITY)
        with RoleStore.at_directory(self.data_dir) as store:
            self.assertEqual(store.account_key(**IDENTITY), first)

    def test_account_key_ignores_unstable_login_fields(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            base = store.account_key(**IDENTITY)
            # The derivation takes exactly server/platform/channel/sdk id.
            self.assertEqual(len(base), 64)
            self.assertEqual(base, store.account_key(**IDENTITY))
            self.assertNotEqual(base, store.account_key(**other_identity(server_id=2.0)))
            self.assertNotEqual(
                base, store.account_key(**other_identity(sdk_identity="someone-else"))
            )
            self.assertNotEqual(
                base, store.account_key(**other_identity(platform="android"))
            )

    def test_account_key_from_another_archive_cannot_be_correlated(self) -> None:
        with RoleStore.at_directory(self.data_dir) as first:
            one = first.account_key(**IDENTITY)
        with RoleStore.at_directory(Path(self._tmp.name) / "other") as second:
            two = second.account_key(**IDENTITY)
        # A per-archive salt: the same SDK identity is not a global identifier.
        self.assertNotEqual(one, two)

    # ------------------------------------------------------------- durability

    def test_created_role_survives_process_restart_and_keeps_its_identity(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            record, created = store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="千灯镇客人",
                career=1,
                create_time=1_700_000_000,
                compatibility_profile_id=PROFILE_ID,
            )
            self.assertTrue(created)
        with RoleStore.at_directory(self.data_dir) as reopened:
            self.assertEqual(reopened.count(), 1)
            found = reopened.lookup(key)
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found.role_id, record.role_id)
            self.assertEqual(found.role_name, "千灯镇客人")
            self.assertEqual(found.career, 1)
            self.assertEqual(found.create_time, 1_700_000_000)
            self.assertEqual(found.identity_origin, IDENTITY_ORIGIN_LOCAL_TRANSACTION)
            self.assertEqual(found.compatibility_profile_id, PROFILE_ID)
            self.assertEqual(found.structure_version, SCHEMA_VERSION)
            state = found.to_role_state(username="local-player")
            self.assertEqual(state.role_id, record.role_id)
            self.assertEqual(state.role_name, "千灯镇客人")
            self.assertEqual(state.career, 1)
            self.assertEqual(state.server_id, 1)

    def test_role_id_serial_is_not_reset_by_a_restart(self) -> None:
        keys = []
        with RoleStore.at_directory(self.data_dir) as store:
            for index in range(3):
                key = store.account_key(**other_identity(sdk_identity=f"player-{index}"))
                keys.append(key)
                store.create_or_get(
                    account_key=key,
                    server_id=1.0,
                    role_name=f"role-{index}",
                    career=1,
                    create_time=1_700_000_000 + index,
                    compatibility_profile_id=PROFILE_ID,
                )
            before = [store.lookup(key).role_id for key in keys]
        with RoleStore.at_directory(self.data_dir) as reopened:
            fresh_key = reopened.account_key(**other_identity(sdk_identity="player-3"))
            record, created = reopened.create_or_get(
                account_key=fresh_key,
                server_id=1.0,
                role_name="role-3",
                career=1,
                create_time=1_700_000_100,
                compatibility_profile_id=PROFILE_ID,
            )
            self.assertTrue(created)
            # The old process-local counter started again at the base serial in
            # every new process; the archive's serial must not.
            self.assertEqual(
                record.role_id,
                (REVIVAL_LOCAL_ROLE_SERIAL_BASE + 3) * 100_000 + 1,
            )
            self.assertNotIn(record.role_id, before)

    def test_role_id_carries_the_selected_server_suffix(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            for server_id in (1, 23, 99_999):
                key = store.account_key(
                    **other_identity(server_id=float(server_id), sdk_identity=f"s{server_id}")
                )
                record, _ = store.create_or_get(
                    account_key=key,
                    server_id=server_id,
                    role_name=f"role-{server_id}",
                    career=1,
                    create_time=1_700_000_000,
                    compatibility_profile_id=PROFILE_ID,
                )
                self.assertEqual(record.role_id % 100_000, server_id)
                self.assertEqual(record.server_id, server_id)

    # -------------------------------------------------------------- isolation

    def test_one_account_gets_one_role_and_a_replay_returns_it(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            first, created = store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="first-name",
                career=1,
                create_time=1_700_000_000,
                compatibility_profile_id=PROFILE_ID,
            )
            second, created_again = store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="second-name",
                career=1,
                create_time=1_700_000_999,
                compatibility_profile_id=PROFILE_ID,
            )
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(second.role_id, first.role_id)
            self.assertEqual(second.role_name, "first-name")
            self.assertEqual(second.create_time, 1_700_000_000)
            self.assertEqual(store.count(), 1)

    def test_different_accounts_are_isolated(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            alpha = store.account_key(**other_identity(sdk_identity="alpha"))
            beta = store.account_key(**other_identity(sdk_identity="beta"))
            self.assertNotEqual(alpha, beta)
            store.create_or_get(
                account_key=alpha, server_id=1.0, role_name="alpha-role", career=1,
                create_time=1, compatibility_profile_id=PROFILE_ID,
            )
            self.assertIsNone(store.lookup(beta))
            store.create_or_get(
                account_key=beta, server_id=1.0, role_name="beta-role", career=1,
                create_time=2, compatibility_profile_id=PROFILE_ID,
            )
            self.assertEqual(store.lookup(alpha).role_name, "alpha-role")
            self.assertEqual(store.lookup(beta).role_name, "beta-role")
            self.assertNotEqual(
                store.lookup(alpha).role_id, store.lookup(beta).role_id
            )

    def test_one_sdk_account_on_two_servers_gets_two_roles(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            one = store.account_key(**other_identity(server_id=1.0))
            two = store.account_key(**other_identity(server_id=2.0))
            self.assertNotEqual(one, two)
            first, _ = store.create_or_get(
                account_key=one, server_id=1.0, role_name="server-one", career=1,
                create_time=1, compatibility_profile_id=PROFILE_ID,
            )
            second, _ = store.create_or_get(
                account_key=two, server_id=2.0, role_name="server-two", career=1,
                create_time=2, compatibility_profile_id=PROFILE_ID,
            )
            self.assertNotEqual(first.role_id, second.role_id)
            self.assertEqual(first.role_id % 100_000, 1)
            self.assertEqual(second.role_id % 100_000, 2)
            self.assertEqual(store.count(), 2)

    # ------------------------------------------------------------ concurrency

    def test_concurrent_create_for_one_account_yields_exactly_one_role(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            results: list[tuple[int, bool]] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(8)

            def worker() -> None:
                try:
                    barrier.wait(timeout=10)
                    record, created = store.create_or_get(
                        account_key=key,
                        server_id=1.0,
                        role_name="contended",
                        career=1,
                        create_time=1_700_000_000,
                        compatibility_profile_id=PROFILE_ID,
                    )
                    results.append((record.role_id, created))
                except BaseException as exc:  # pragma: no cover - failure path
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 8)
            self.assertEqual({role_id for role_id, _ in results}, {results[0][0]})
            self.assertEqual(sum(1 for _, created in results if created), 1)
            self.assertEqual(store.count(), 1)

    def test_two_handles_on_one_archive_never_share_a_role_id(self) -> None:
        first = RoleStore.at_directory(self.data_dir)
        second = RoleStore.at_directory(self.data_dir)
        try:
            results: list[tuple[int, bool]] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(6)

            def worker(index: int) -> None:
                store = first if index % 2 == 0 else second
                try:
                    barrier.wait(timeout=10)
                    key = store.account_key(
                        **other_identity(sdk_identity=f"handle-{index}")
                    )
                    record, created = store.create_or_get(
                        account_key=key,
                        server_id=1.0,
                        role_name=f"handle-{index}",
                        career=1,
                        create_time=1_700_000_000 + index,
                        compatibility_profile_id=PROFILE_ID,
                    )
                    results.append((record.role_id, created))
                except BaseException as exc:  # pragma: no cover - failure path
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 6)
            self.assertEqual(len({role_id for role_id, _ in results}), 6)
            self.assertTrue(all(created for _, created in results))
            self.assertEqual(first.count(), 6)
        finally:
            first.close()
            second.close()

    # ------------------------------------------------------------- rollback

    def test_a_rejected_create_writes_nothing(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            with self.assertRaises(ValueError):
                # server ID outside the exact-Lua-integer namespace the role ID
                # encoder preserves: allocate_revival_role_id refuses, and the
                # transaction must not leave a row, a bumped serial or a lock.
                store.create_or_get(
                    account_key=key,
                    server_id=0,
                    role_name="will-not-exist",
                    career=1,
                    create_time=1,
                    compatibility_profile_id=PROFILE_ID,
                )
            self.assertEqual(store.count(), 0)
            with self.assertRaises(RoleStoreError):
                store.create_or_get(
                    account_key=key,
                    server_id=1.0,
                    role_name="",
                    career=1,
                    create_time=1,
                    compatibility_profile_id=PROFILE_ID,
                )
            self.assertEqual(store.count(), 0)
            # The failed attempt burned no serial either: the next accepted
            # create still starts at the recovered base.
            record, created = store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="accepted",
                career=1,
                create_time=1,
                compatibility_profile_id=PROFILE_ID,
            )
            self.assertTrue(created)
            self.assertEqual(record.role_id, REVIVAL_LOCAL_ROLE_SERIAL_BASE * 100_000 + 1)

    # ------------------------------------------------------------- fail closed

    def test_a_damaged_archive_is_refused_and_never_overwritten(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        archive = self.data_dir / STORE_FILENAME
        archive.write_bytes(b"not a database at all" * 8)
        before = archive.read_bytes()
        with self.assertRaises(RoleStoreUnusableError):
            RoleStore.at_directory(self.data_dir)
        self.assertEqual(archive.read_bytes(), before)

    def test_a_non_file_at_the_archive_path_is_refused(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / STORE_FILENAME).mkdir()
        with self.assertRaises(RoleStoreUnusableError):
            RoleStore.at_directory(self.data_dir)

    def test_an_unknown_structure_version_is_refused(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            store._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", ("999",)
            )
        with self.assertRaises(RoleStoreUnusableError):
            RoleStore.at_directory(self.data_dir)

    def test_an_older_structure_without_a_migration_is_refused(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            store.create_or_get(
                account_key=key, server_id=1.0, role_name="kept", career=1,
                create_time=1, compatibility_profile_id=PROFILE_ID,
            )
            store._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", ("0",)
            )
        with self.assertRaises(RoleStoreMigrationError):
            RoleStore.at_directory(self.data_dir)
        # The refused archive is still there, untouched.
        self.assertTrue((self.data_dir / STORE_FILENAME).is_file())

    def test_an_archive_without_its_salt_is_refused(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            store._conn.execute("DELETE FROM meta WHERE key = 'account_key_salt'")
        with self.assertRaises(RoleStoreUnusableError):
            RoleStore.at_directory(self.data_dir)

    def test_an_archive_without_its_serial_row_is_refused(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            store._conn.execute("DELETE FROM role_serials")
        with self.assertRaises(RoleStoreUnusableError):
            RoleStore.at_directory(self.data_dir)

    def test_a_truncated_archive_is_refused(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            store.create_or_get(
                account_key=store.account_key(**IDENTITY),
                server_id=1.0, role_name="kept", career=1, create_time=1,
                compatibility_profile_id=PROFILE_ID,
            )
        archive = self.data_dir / STORE_FILENAME
        # Drop the write-ahead log first, otherwise it can legitimately replay
        # the committed page back and hide the damage.
        for sidecar in ("-wal", "-shm"):
            side = self.data_dir / (STORE_FILENAME + sidecar)
            if side.exists():
                side.unlink()
        payload = archive.read_bytes()
        archive.write_bytes(payload[: 4096])
        with self.assertRaises(RoleStoreUnusableError):
            RoleStore.at_directory(self.data_dir)

    # -------------------------------------------------- nothing secret at rest

    def test_no_login_credential_is_written_to_the_archive(self) -> None:
        secrets_in = {
            "sdk_identity": "sdk-user-4321",
            "channel_id": "channel-secret",
            "channel_sub_id": "sub-secret",
        }
        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(
                server_id=1.0,
                platform="androio",
                channel_id=secrets_in["channel_id"],
                channel_sub_id=secrets_in["channel_sub_id"],
                sdk_identity=secrets_in["sdk_identity"],
            )
            store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="archived-role-name",
                career=1,
                create_time=1_700_000_000,
                compatibility_profile_id=PROFILE_ID,
            )
        blob = b"".join(
            path.read_bytes()
            for path in self.data_dir.iterdir()
            if path.is_file()
        )
        for name, value in secrets_in.items():
            self.assertNotIn(
                value.encode("utf-8"), blob, f"{name} must not be at rest"
            )
            # Cover the length-prefixed / UTF-8 encoded forms too.
            self.assertNotIn(value.encode("utf-16-le"), blob, f"{name} (utf-16) at rest")
        # Nothing in the schema can hold the fields the derivation deliberately
        # leaves out: there is no column for a verify digest, a token, a device
        # id or an SDK username.
        columns = {
            str(row["name"])
            for row in store_schema_columns(self.data_dir, "roles")
        }
        self.assertEqual(
            columns,
            {
                "account_key", "server_id", "role_id", "role_name", "career",
                "create_time", "identity_origin", "compatibility_profile_id",
                "structure_version", "created_at", "updated_at",
            },
        )
        # The role name is the character name the client itself displays and is
        # the one string that must round-trip.
        self.assertIn("archived-role-name".encode("utf-8"), blob)

    def test_the_derived_key_is_a_salted_digest_not_a_reversible_encoding(self) -> None:
        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            self.assertEqual(len(key), 64)
            self.assertNotIn("local-player", key)
            # A plain unsalted digest of the same identity is a different value,
            # which is what stops a low-entropy SDK id from being brute-forced
            # out of the archive.
            plain = hashlib.sha256(
                "\x1f".join(
                    [ACCOUNT_KEY_DERIVATION, "1", "androio", "local-channel", "0", "local-player"]
                ).encode("utf-8")
            ).hexdigest()
            self.assertNotEqual(key, plain)


class RoleStoreTransactionVisibilityTests(unittest.TestCase):
    """The archive is a private implementation detail; these pin its invariants."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rows_are_only_written_inside_a_committed_transaction(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        try:
            with self.assertRaises(ValueError):
                store.create_or_get(
                    account_key=store.account_key(**IDENTITY),
                    server_id=123_456,
                    role_name="out-of-range-server",
                    career=1,
                    create_time=1,
                    compatibility_profile_id=PROFILE_ID,
                )
            # A second connection to the same file sees no uncommitted row.
            other = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
            try:
                rows = other.execute("SELECT COUNT(*) FROM roles").fetchone()
            finally:
                other.close()
            self.assertEqual(rows[0], 0)
        finally:
            store.close()

    def test_sqlite_rejects_a_role_id_that_disagrees_with_its_server(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                store._conn.execute(
                    "INSERT INTO roles (account_key, server_id, role_id, role_name,"
                    " career, create_time, identity_origin,"
                    " compatibility_profile_id, structure_version, created_at,"
                    " updated_at) VALUES ('x', 1, 900000000002, 'n', 1, 0, 'o',"
                    " 'p', 1, 0, 0)"
                )
        finally:
            store.close()


#: The structure-1 objects verbatim, as the pre-TASK-0073 build wrote them.
#: A test that upgrades an archive must build a *real* old archive, not the new
#: one with a marker rewritten.
V1_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE roles (
        account_key              TEXT    PRIMARY KEY,
        server_id                INTEGER NOT NULL,
        role_id                  INTEGER NOT NULL UNIQUE,
        role_name                TEXT    NOT NULL,
        career                   INTEGER NOT NULL,
        create_time              INTEGER NOT NULL,
        identity_origin          TEXT    NOT NULL,
        compatibility_profile_id TEXT    NOT NULL,
        structure_version        INTEGER NOT NULL,
        created_at               INTEGER NOT NULL,
        updated_at               INTEGER NOT NULL,
        CHECK (server_id > 0 AND server_id < 100000),
        CHECK (role_id > 0),
        CHECK (role_id % 100000 = server_id)
    )
    """,
    """
    CREATE TABLE role_serials (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        next_serial INTEGER NOT NULL
    )
    """,
)

V1_SALT = "a1" * 32
V1_ROLE_ID = 9_000_001 * 100_000 + 1


def v1_account_key(salt_hex: str, identity: dict | None = None) -> str:
    """The archived derivation, recomputed from the archived salt."""

    values = dict(IDENTITY if identity is None else identity)
    parts = (
        ACCOUNT_KEY_DERIVATION,
        str(int(values["server_id"])),
        str(values["platform"]).strip().lower(),
        values["channel_id"],
        values["channel_sub_id"],
        values["sdk_identity"],
    )
    material = bytes.fromhex(salt_hex) + "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def build_v1_archive(
    data_dir: Path, *, keep_wal_open: bool = False, identity: dict | None = None
):
    """A real structure-1 archive: identity, salt, serial and one role row.

    With ``keep_wal_open`` the newest role lives only in a write-ahead log that
    is still open when the archive is served, which is exactly the state a
    snapshot has to survive.  ``identity`` selects the account the one role
    belongs to, so a test that logs in over this archive through the real
    product can archive the identity that login packet actually derives.
    """

    data_dir.mkdir(parents=True, exist_ok=True)
    archive = data_dir / STORE_FILENAME
    key = v1_account_key(V1_SALT, identity)
    connection = sqlite3.connect(str(archive))
    try:
        for statement in V1_SCHEMA_STATEMENTS:
            connection.execute(statement)
        for name, value in (
            ("schema_version", "1"),
            ("account_key_salt", V1_SALT),
            ("account_key_derivation", ACCOUNT_KEY_DERIVATION),
            ("created_at", "1700000000"),
        ):
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)", (name, value)
            )
        connection.execute(
            "INSERT INTO role_serials (id, next_serial) VALUES (1, ?)",
            (str(REVIVAL_LOCAL_ROLE_SERIAL_BASE + 1),),
        )
        connection.execute(
            "INSERT INTO roles (account_key, server_id, role_id, role_name, career,"
            " create_time, identity_origin, compatibility_profile_id,"
            " structure_version, created_at, updated_at)"
            " VALUES (?, 1, ?, ?, 1, 1700000000, ?, ?, 1, 1700000000, 1700000000)",
            (
                key,
                V1_ROLE_ID,
                "旧档角色",
                IDENTITY_ORIGIN_LOCAL_TRANSACTION,
                PROFILE_ID,
            ),
        )
        connection.commit()
        if keep_wal_open:
            # Keep every commit in the -wal file rather than in the main file.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA wal_autocheckpoint = 0")
            connection.execute(
                "UPDATE roles SET updated_at = 1700000001 WHERE account_key = ?",
                (key,),
            )
            connection.commit()
            return connection, key, V1_ROLE_ID
    except BaseException:
        connection.close()
        raise
    connection.close()
    return None, key, V1_ROLE_ID


def archive_rows(data_dir: Path) -> list[tuple]:
    connection = sqlite3.connect(str(data_dir / STORE_FILENAME))
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT account_key, server_id, role_id, role_name, career,"
                " create_time, identity_origin, compatibility_profile_id,"
                " created_at, updated_at FROM roles ORDER BY account_key"
            ).fetchall()
        ]
    finally:
        connection.close()


def snapshot_facts(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "schema_version": connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0],
            "salt": connection.execute(
                "SELECT value FROM meta WHERE key = 'account_key_salt'"
            ).fetchone()[0],
            "tables": sorted(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ),
            "roles": archive_rows_of(connection),
        }
    finally:
        connection.close()


def archive_rows_of(connection: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT account_key, server_id, role_id, role_name, career,"
            " create_time, identity_origin, compatibility_profile_id,"
            " created_at, updated_at FROM roles ORDER BY account_key"
        ).fetchall()
    ]


def snapshot_path(data_dir: Path, version: int = 1) -> Path:
    return data_dir / (STORE_FILENAME + f".pre-migration-v{version}.bak")


class RoleStoreMigrationTests(unittest.TestCase):
    """Structure 1 -> 3: additive, snapshotted first, and never a role change."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_version_1_archive_upgrades_with_its_identity_intact(self) -> None:
        wal, key, role_id = build_v1_archive(self.data_dir, keep_wal_open=True)
        before = archive_rows(self.data_dir)
        try:
            store = RoleStore.at_directory(self.data_dir)
        finally:
            if wal is not None:
                wal.close()
        try:
            # The role is served exactly as it was archived, and the salt still
            # derives the same account key: nothing about identity moved.
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.account_key(**IDENTITY), key)
            record = store.lookup(key)
            self.assertIsNotNone(record)
            self.assertEqual(record.role_id, role_id)
            self.assertEqual(record.role_name, "旧档角色")
            self.assertEqual(record.structure_version, 1)
            described = store.describe()
            self.assertEqual(described["role_store_schema_version"], SCHEMA_VERSION)
            self.assertIs(described["role_store_structure_migrated"], True)
            self.assertEqual(described["role_store_pre_migration_version"], 1)
            self.assertEqual(
                described["role_store_migration_snapshot"],
                str(snapshot_path(self.data_dir)),
            )
            # …and every table of every step it walked is usable straight away.
            self.assertEqual(store.story_completions(account_key=key), ())
            self.assertEqual(store.bag_sort_states(account_key=key), ())
            role = store.lookup(key)
            row, outcome = store.record_bag_sort_state(
                record=role,
                ops=BAG_SORT_SLOT_CARD,
                sort=5,
                observed_at=1_700_000_500,
                packet_sequence=7,
                evidence_kind=BAG_SORT_EVIDENCE_CLIENT_C2S,
            )
            self.assertEqual(outcome, "created")
            self.assertEqual(row.sort, 5)
            self.assertEqual(
                store.bag_sort_state(account_key=key, ops=BAG_SORT_SLOT_CARD).sort, 5
            )
        finally:
            store.close()

        # The snapshot is a real, verified copy of the structure-1 archive,
        # including the row that only ever lived in the open write-ahead log.
        facts = snapshot_facts(snapshot_path(self.data_dir))
        self.assertEqual(facts["integrity"], "ok")
        self.assertEqual(facts["schema_version"], "1")
        self.assertEqual(facts["salt"], V1_SALT)
        self.assertNotIn("story_completions", facts["tables"])
        self.assertEqual(facts["roles"], before)
        # The snapshot is a structure-1 archive: neither table the upgrade adds
        # is in it, which is what makes it a recovery point for this step.
        self.assertNotIn("bag_sort_states", facts["tables"])
        # The upgraded archive keeps the same role rows, and gains the tables.
        self.assertEqual(archive_rows(self.data_dir), before)
        upgraded_tables = snapshot_facts(self.data_dir / STORE_FILENAME)["tables"]
        self.assertIn("story_completions", upgraded_tables)
        self.assertIn("bag_sort_states", upgraded_tables)

    def test_a_second_open_neither_migrates_nor_rewrites_the_snapshot(self) -> None:
        wal, key, _ = build_v1_archive(self.data_dir, keep_wal_open=False)
        first = RoleStore.at_directory(self.data_dir)
        first.close()
        snapshot = snapshot_path(self.data_dir)
        snapshot_before = snapshot.read_bytes()
        second = RoleStore.at_directory(self.data_dir)
        try:
            self.assertIs(second.describe()["role_store_structure_migrated"], False)
            self.assertIsNone(second.describe()["role_store_pre_migration_version"])
            self.assertEqual(second.count(), 1)
            self.assertEqual(second.account_key(**IDENTITY), key)
        finally:
            second.close()
        self.assertEqual(snapshot.read_bytes(), snapshot_before)

    def test_an_unusable_snapshot_path_stops_the_upgrade_untouched(self) -> None:
        build_v1_archive(self.data_dir)
        before = archive_rows(self.data_dir)
        # Somebody already put something that is not a database where the
        # snapshot has to go: the open refuses instead of overwriting it.
        snapshot_path(self.data_dir).write_bytes(b"not a snapshot")
        with self.assertRaises(RoleStoreError):
            RoleStore.at_directory(self.data_dir)
        self.assertEqual(snapshot_path(self.data_dir).read_bytes(), b"not a snapshot")
        self.assertEqual(archive_rows(self.data_dir), before)
        # Nothing was half-upgraded: still structure 1, and the new table was
        # never created.  (The open does switch the journal mode to WAL, which
        # is a pre-existing write of every open and not part of the upgrade, so
        # the file's bytes are not an assertion -- its content is.)
        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                "1",
            )
            self.assertEqual(
                [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                ],
                ["meta", "roles", "role_serials"],
            )
        finally:
            connection.close()

    def test_a_refused_upgrade_leaves_a_version_1_archive_servable_by_hand(self) -> None:
        """A refusal is a refusal: the archive is never left half-upgraded."""

        build_v1_archive(self.data_dir)
        snapshot_path(self.data_dir).mkdir()
        with self.assertRaises(RoleStoreError):
            RoleStore.at_directory(self.data_dir)
        snapshot_path(self.data_dir).rmdir()
        # With the obstruction gone the very same archive upgrades normally.
        store = RoleStore.at_directory(self.data_dir)
        try:
            self.assertEqual(store.count(), 1)
            self.assertIs(store.describe()["role_store_structure_migrated"], True)
        finally:
            store.close()
        self.assertEqual(snapshot_facts(snapshot_path(self.data_dir))["schema_version"], "1")

    def test_a_fresh_archive_is_created_at_the_new_structure(self) -> None:
        store = RoleStore.at_directory(Path(self._tmp.name) / "fresh")
        try:
            self.assertEqual(store._meta("schema_version"), str(SCHEMA_VERSION))
            self.assertIs(store.describe()["role_store_structure_migrated"], False)
            self.assertFalse(snapshot_path(Path(self._tmp.name) / "fresh").exists())
        finally:
            store.close()

    def test_an_existing_play_profile_archive_upgrades_in_place(self) -> None:
        """The TASK-0071/0072 save a player already has: same role, same salt.

        An archive written by the previous build *is* a structure-1 file, so this
        rewrites one as such (keeping its own salt and role row) and then serves
        it through the profile entry the player uses.
        """

        root = Path(self._tmp.name) / "profiles"
        default_archive = Path(self._tmp.name) / "user-data"
        profile = select_play_profile(
            PLAY_PROFILE_ID,
            mode=PROFILE_MODE_INIT,
            root=root,
            default_archive_dir=default_archive,
            time_source=lambda: 1_700_000_000,
        )
        store = profile.open_store(time_source=lambda: 1_700_000_000)
        try:
            key = store.account_key(**IDENTITY)
            record, created = store.create_or_get(
                account_key=key,
                server_id=1.0,
                role_name="旧档角色",
                career=1,
                create_time=1_700_000_000,
                compatibility_profile_id=PROFILE_ID,
            )
            self.assertTrue(created)
            salt = store._meta("account_key_salt")
        finally:
            store.close()

        # Rewrite the profile's save as the structure the previous build wrote:
        # same salt, same account key, same role row, no story table.
        archive = profile.directory / STORE_FILENAME
        v2_rows = archive_rows(profile.directory)
        archive.unlink()
        for sidecar in ("-wal", "-shm"):
            side = profile.directory / (STORE_FILENAME + sidecar)
            if side.exists():
                side.unlink()
        build_v1_archive(profile.directory)
        connection = sqlite3.connect(str(archive))
        try:
            connection.execute(
                "UPDATE meta SET value = ? WHERE key = 'account_key_salt'", (salt,)
            )
            connection.execute("DELETE FROM roles")
            connection.execute(
                "INSERT INTO roles (account_key, server_id, role_id, role_name,"
                " career, create_time, identity_origin, compatibility_profile_id,"
                " structure_version, created_at, updated_at)"
                " VALUES (?, 1, ?, ?, 1, 1700000000, ?, ?, 1, 1700000000, 1700000000)",
                (
                    key,
                    record.role_id,
                    "旧档角色",
                    IDENTITY_ORIGIN_LOCAL_TRANSACTION,
                    PROFILE_ID,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        # It really is the previous build's structure again: same role rows, but
        # structure 1 and no story table.
        downgraded = snapshot_facts(archive)
        self.assertEqual(downgraded["schema_version"], "1")
        self.assertEqual(downgraded["tables"], ["meta", "role_serials", "roles"])
        self.assertEqual(downgraded["roles"], v2_rows)

        resumed = select_play_profile(
            PLAY_PROFILE_ID,
            mode=PROFILE_MODE_RESUME,
            root=root,
            default_archive_dir=default_archive,
            time_source=lambda: 1_700_000_000,
        )
        store = resumed.open_store(time_source=lambda: 1_700_000_000)
        try:
            # Served through the profile entry, with its manifest identity, its
            # one role, its salt and its account key all unchanged.
            self.assertEqual(store.kind, KIND_PLAY_PROFILE)
            self.assertEqual(store.describe()["play_profile_id"], PLAY_PROFILE_ID)
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.account_key(**IDENTITY), key)
            served = store.lookup(key)
            self.assertEqual(served.role_id, record.role_id)
            self.assertEqual(served.role_name, "旧档角色")
            self.assertIs(store.describe()["role_store_structure_migrated"], True)
            self.assertEqual(store.story_completions(account_key=key), ())
        finally:
            store.close()
        facts = snapshot_facts(snapshot_path(profile.directory))
        self.assertEqual(facts["integrity"], "ok")
        self.assertEqual(facts["schema_version"], "1")
        self.assertEqual(facts["salt"], salt)
        # The snapshot holds exactly the role the profile was serving, and the
        # upgraded archive still holds it unchanged.
        self.assertEqual(facts["roles"], archive_rows(profile.directory))
        self.assertEqual(len(facts["roles"]), 1)
        (
            snapshot_key,
            _server,
            snapshot_role_id,
            snapshot_name,
            _career,
            _create_time,
            _origin,
            _profile,
            _created,
            _updated,
        ) = facts["roles"][0]
        self.assertEqual(snapshot_key, key)
        self.assertEqual(snapshot_role_id, record.role_id)
        self.assertEqual(snapshot_name, "旧档角色")


class StructureOneToThreeInvariantTests(unittest.TestCase):
    """TASK-0085: a structure-1 archive walks every step, or is refused whole.

    The regression this class exists for: `_migrate` applied ONE step and then
    wrote the current version marker, so a valid structure-1 archive was served
    as structure 3 while ``bag_sort_states`` was never created.  Everything the
    bag's durability rests on -- the store read, the client's ``C2S 512`` write
    and the login replay -- then failed with a raw SQLite error instead of a
    named refusal.  Two properties are asserted here: the upgrade is
    *step-wise* (structure 1 ends up holding every table of structures 2 and 3),
    and a marker that is not true is *refused*, never quietly made true.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------- the upgrade

    def test_a_version_1_archive_ends_up_holding_the_current_structure(self) -> None:
        wal, key, role_id = build_v1_archive(self.data_dir, keep_wal_open=True)
        try:
            store = RoleStore.at_directory(self.data_dir)
        finally:
            if wal is not None:
                wal.close()
        try:
            self.assertEqual(store._meta("schema_version"), str(SCHEMA_VERSION))
            self.assertEqual(
                store.describe()["role_store_pre_migration_version"], 1
            )
            # Every table the current structure is born with, not just the one
            # the first step happens to add.
            self.assertEqual(
                set(snapshot_facts(self.data_dir / STORE_FILENAME)["tables"]),
                {"meta", "roles", "role_serials", "story_completions",
                 "bag_sort_states", "chapter_fight_clears", "card_instances",
                 "draw_pity", "god_weapon_instances", "hotspot_draw_pity", "team_presets",
                 "team_state", "card_special_inventory", "role_wallet",
                 "role_values", "card_pieces",
                 "chapter_event_clears", "chapter_box_accepts",
                 "chapter_event_reward_grants"},
            )
            # The identity is the archived one, byte for byte.
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.account_key(**IDENTITY), key)
            self.assertEqual(store.lookup(key).role_id, role_id)
            # …and the serial the archive was written with is the serial it still has: an
            # upgrade that reset it would hand a later role the ID of an existing one.
            self.assertEqual(
                store._conn.execute(
                    "SELECT next_serial FROM role_serials"
                ).fetchone()["next_serial"],
                REVIVAL_LOCAL_ROLE_SERIAL_BASE + 1,
            )
            # And both tables of both steps are really usable.
            self.assertEqual(store.story_completions(account_key=key), ())
            self.assertEqual(store.bag_sort_states(account_key=key), ())
        finally:
            store.close()

    def test_the_upgrade_runs_once_and_the_snapshot_is_the_structure_it_replaced(
        self,
    ) -> None:
        build_v1_archive(self.data_dir)
        first = RoleStore.at_directory(self.data_dir)
        first.close()
        snapshot = snapshot_path(self.data_dir)
        facts = snapshot_facts(snapshot)
        self.assertEqual(facts["integrity"], "ok")
        self.assertEqual(facts["schema_version"], "1")
        self.assertEqual(facts["salt"], V1_SALT)
        # The snapshot is the structure the upgrade replaced, so it holds
        # neither of the tables the two steps add.
        self.assertEqual(
            facts["tables"], ["meta", "role_serials", "roles"]
        )
        snapshot_bytes = snapshot.read_bytes()

        second = RoleStore.at_directory(self.data_dir)
        try:
            self.assertIs(second.describe()["role_store_structure_migrated"], False)
            self.assertIsNone(second.describe()["role_store_pre_migration_version"])
            self.assertEqual(second.bag_sort_states(account_key=second.account_key(
                **IDENTITY)), ())
        finally:
            second.close()
        self.assertEqual(snapshot.read_bytes(), snapshot_bytes)

    def test_a_snapshot_of_a_later_structure_cannot_stand_in_for_this_upgrade(
        self,
    ) -> None:
        """The snapshots are per-version, and each one must be the real thing."""

        build_v1_archive(self.data_dir)
        # A file that is a database, but a *newer* structure's: it is not a
        # recovery point for a version-1 archive and may not be adopted as one.
        fresh = RoleStore.at_directory(Path(self._tmp.name) / "other")
        fresh.close()
        shutil.copyfile(
            Path(self._tmp.name) / "other" / STORE_FILENAME, snapshot_path(self.data_dir)
        )
        with self.assertRaises(RoleStoreMigrationError):
            RoleStore.at_directory(self.data_dir)
        # Refused before the archive was touched: still structure 1, still no
        # table of either step.
        self.assertEqual(
            snapshot_facts(self.data_dir / STORE_FILENAME)["tables"],
            ["meta", "role_serials", "roles"],
        )

    def test_a_failing_step_rolls_back_every_step_before_it(self) -> None:
        """One transaction covers the whole walk: a failure is not a partial 2."""

        build_v1_archive(self.data_dir)

        def boom(_connection) -> None:
            raise sqlite3.OperationalError("forced failure inside the upgrade")

        with mock.patch.dict(
            "revival_stub.role_store._MIGRATIONS", {2: boom}, clear=False
        ):
            with self.assertRaises(Exception):
                RoleStore.at_directory(self.data_dir)
        # The step that succeeded left nothing behind: the archive is exactly
        # the structure-1 file it was, marker included.
        facts = snapshot_facts(self.data_dir / STORE_FILENAME)
        self.assertEqual(facts["schema_version"], "1")
        self.assertEqual(facts["tables"], ["meta", "role_serials", "roles"])
        self.assertEqual(facts["salt"], V1_SALT)
        # …and with the failure gone the same file upgrades normally, so the
        # refusal really was about the step and not about the archive.
        store = RoleStore.at_directory(self.data_dir)
        try:
            self.assertIn(
                "bag_sort_states",
                snapshot_facts(self.data_dir / STORE_FILENAME)["tables"],
            )
        finally:
            store.close()

    # ------------------------------------------- a marker that is not true

    def _drop_tables(self, *tables: str) -> None:
        """Remove tables from the archive the way a skipped step never made them.

        A structure-1 archive upgraded by the single-jump build ended up as a
        current archive minus the tables of the steps it skipped -- the same
        meta rows, the same marker, the same roles.  Dropping them again is
        therefore an exact reconstruction of that file, and it re-states no part
        of the schema.
        """

        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            for table in tables:
                connection.execute(f"DROP TABLE {table}")
            connection.commit()
        finally:
            connection.close()

    def test_a_marker_that_promises_a_table_the_file_lacks_is_refused(self) -> None:
        build_v1_archive(self.data_dir)
        with RoleStore.at_directory(self.data_dir):
            pass  # the real upgrade …
        snapshot_facts_path = snapshot_path(self.data_dir)
        self._drop_tables("bag_sort_states")  # … with the last step undone

        with self.assertRaises(RoleStoreUnusableError) as caught:
            RoleStore.at_directory(self.data_dir)
        message = str(caught.exception)
        self.assertIn("records structure 14", message)
        self.assertIn("bag_sort_states (table absent)", message)
        self.assertIn("not repairing it in place", message)
        # A refusal names the recovery point that is really there.
        self.assertIn(str(snapshot_facts_path), message)
        # Nothing was written while refusing: the file is still what it was.
        self.assertEqual(
            snapshot_facts(self.data_dir / STORE_FILENAME)["tables"],
            ["card_instances", "card_pieces", "card_special_inventory",
             "chapter_box_accepts", "chapter_event_clears",
             "chapter_event_reward_grants", "chapter_fight_clears", "draw_pity",
             "god_weapon_instances", "hotspot_draw_pity", "meta", "role_serials",
             "role_values",
             "role_wallet", "roles", "story_completions", "team_presets",
             "team_state"],
        )

    def test_a_marker_that_promises_columns_the_table_lacks_is_refused(self) -> None:
        build_v1_archive(self.data_dir)
        with RoleStore.at_directory(self.data_dir):
            pass
        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            connection.execute("ALTER TABLE bag_sort_states RENAME TO old_bag_sort")
            connection.execute(
                "CREATE TABLE bag_sort_states (account_key TEXT NOT NULL,"
                " ops INTEGER NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RoleStoreUnusableError) as caught:
            RoleStore.at_directory(self.data_dir)
        self.assertIn("bag_sort_states (missing columns:", str(caught.exception))
        self.assertIn("sort", str(caught.exception))

    def test_both_skipped_tables_are_named_when_both_are_missing(self) -> None:
        build_v1_archive(self.data_dir)
        with RoleStore.at_directory(self.data_dir):
            pass
        self._drop_tables("bag_sort_states", "story_completions")
        with self.assertRaises(RoleStoreUnusableError) as caught:
            RoleStore.at_directory(self.data_dir)
        message = str(caught.exception)
        self.assertIn("bag_sort_states (table absent)", message)
        self.assertIn("story_completions (table absent)", message)

    def test_a_structure_three_archive_that_is_whole_is_still_served(self) -> None:
        """The new check refuses a false marker, not the current structure."""

        with RoleStore.at_directory(self.data_dir) as store:
            key = store.account_key(**IDENTITY)
            store.create_or_get(
                account_key=key, server_id=1.0, role_name="现在", career=1,
                create_time=1, compatibility_profile_id=PROFILE_ID,
            )
        with RoleStore.at_directory(self.data_dir) as store:
            self.assertEqual(store.count(), 1)
            self.assertIs(store.describe()["role_store_structure_migrated"], False)
            self.assertEqual(store.bag_sort_states(account_key=store.account_key(
                **IDENTITY)), ())

    def test_a_structure_one_archive_that_already_has_a_later_table_is_refused(
        self,
    ) -> None:
        """A pending step never writes over a table this structure never had."""

        build_v1_archive(self.data_dir)
        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            connection.execute(
                "CREATE TABLE bag_sort_states (account_key TEXT NOT NULL,"
                " ops INTEGER NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RoleStoreMigrationError) as caught:
            RoleStore.at_directory(self.data_dir)
        self.assertIn("already holds bag_sort_states", str(caught.exception))
        # Refused before anything ran: no story table was added either.
        self.assertEqual(
            snapshot_facts(self.data_dir / STORE_FILENAME)["tables"],
            ["bag_sort_states", "meta", "role_serials", "roles"],
        )

    def test_an_archive_that_already_lost_its_identity_is_not_migrated(self) -> None:
        build_v1_archive(self.data_dir)
        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            connection.execute("DELETE FROM meta WHERE key = 'account_key_salt'")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RoleStoreMigrationError) as caught:
            RoleStore.at_directory(self.data_dir)
        self.assertIn("already lost its account_key_salt", str(caught.exception))
        # The upgrade never started: the structure it would have changed is
        # untouched, marker and tables both.  (Read raw: the archive is missing
        # a marker on purpose, so the full-facts helper cannot read it.)
        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                "1",
            )
            self.assertEqual(
                sorted(
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                ),
                ["meta", "role_serials", "roles"],
            )
        finally:
            connection.close()


#: The identity a real login packet derives: the five fields `login_identity_inputs`
#: reads out of `DEFAULT_LOGIN_FIELDS`.  A v1 archive built for a *process* test therefore
#: archives this account, not the module's synthetic one.
CLIENT_IDENTITY = {
    "server_id": 1.0,
    "platform": "android",
    "channel_id": "local-channel",
    "channel_sub_id": "0",
    "sdk_identity": "local-user",
}


class _StubProcess:
    """The real product, in its own process, serving one archive directory."""

    def __init__(self, data_dir: Path, stub_dir: Path):
        self.events: list = []
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "revival_stub", "--data-dir", str(data_dir),
             "--http-port", "0", "--tcp-port", "0"],
            cwd=str(stub_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()
        self.tcp_port = int(self._wait_for("stub_started")["tcp_port"])

    def _drain(self) -> None:
        for line in self.proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                self.events.append(json.loads(line))
            except ValueError:
                continue

    def _wait_for(self, event: str, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for record in list(self.events):
                if record.get("event") == event:
                    return record
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"the stub exited ({self.proc.returncode}) before {event!r}")
            time.sleep(0.02)
        raise AssertionError(f"the stub never reported {event!r}")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=30)
        if self.proc.stdout is not None:
            self.proc.stdout.close()


class _Client:
    """One client connection: the recovered codec, the recovered packet ids."""

    def __init__(self, port: int):
        self.stream = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.stream.settimeout(10)
        self.writer = WriterState()
        self.reader = ReaderState()
        self.buffer = bytearray()
        self.responses: list = []

    def send(self, protocol_id: int, fields: tuple) -> None:
        packet = encode_packet(C2S_REGISTRY.by_id(protocol_id), fields, self.writer)
        self.stream.sendall(packet.wire)

    def read(self, count: int) -> list:
        while len(self.responses) < count:
            try:
                packet = decode_packet(self.buffer, S2C_REGISTRY, self.reader)
            except NeedMoreData:
                chunk = self.stream.recv(65536)
                if not chunk:
                    raise AssertionError(
                        f"server closed after {len(self.responses)} response(s): "
                        f"{[p.spec.protocol_id for p in self.responses]}")
                self.buffer.extend(chunk)
                continue
            del self.buffer[: packet.consumed]
            self.responses.append(packet)
        return self.responses

    def ids(self) -> list:
        return [packet.spec.protocol_id for packet in self.responses]

    def close(self) -> None:
        self.stream.close()


class StructureOneUpgradeProcessTests(unittest.TestCase):
    """TASK-0085 §A: a separate process serves the upgraded archive, bag edge included.

    The unit tests above prove the tables exist after the upgrade; this one proves the
    *product* can be a different process from the one that migrated, log in over the
    socket and use the bag edge -- which is exactly what the single-jump upgrade broke
    (`tcp_stub._send_bag_sort_rows` would meet a missing table on the very next login).
    Nothing here is a simulation stub: the child is `python -m revival_stub` from this
    checkout, and the client speaks the recovered wire format.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"
        self.stub_dir = Path(__file__).resolve().parents[1]
        self.processes: list = []

    def tearDown(self) -> None:
        for process in self.processes:
            process.close()
        self._tmp.cleanup()

    def _boot(self) -> _StubProcess:
        process = _StubProcess(self.data_dir, self.stub_dir)
        self.processes.append(process)
        return process

    def _login_fields(self) -> list:
        fields = list(DEFAULT_LOGIN_FIELDS)
        fields[0] = CLIENT_IDENTITY["server_id"]
        fields[1] = CLIENT_IDENTITY["sdk_identity"].encode("utf-8")
        fields[7] = CLIENT_IDENTITY["platform"].encode("utf-8")
        return fields

    def _resume(self, client: _Client, expected_chain: list) -> list:
        """The recovered existing-role login, exactly as the client drives it."""

        client.send(0, self._login_fields())
        client.read(3)
        client.send(3, (1, False))
        client.read(11)
        client.send(217, (12345.0, b"apk-md5"))
        client.send(4, (False,))
        client.send(22, ())
        client.send(37, ())
        client.send(39, ())
        client.send(579, (True,))
        client.read(len(expected_chain))
        return client.responses

    def test_a_separate_process_logs_in_and_replays_the_bag_slot_it_recorded(self) -> None:
        build_v1_archive(self.data_dir, identity=CLIENT_IDENTITY)
        self.assertEqual(
            snapshot_facts(self.data_dir / STORE_FILENAME)["tables"],
            ["meta", "role_serials", "roles"],
        )

        # Epoch 1: a new process opens the structure-1 archive (migrating it), resumes the
        # role the archive already holds, and records one bag sort.
        first = self._boot()
        client = _Client(first.tcp_port)
        try:
            self._resume(client, [8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, 411, 1370, 283, 13, 85])
            self.assertNotIn(1048, client.ids())
            client.send(512, (1, 234))
            recorded = first._wait_for("tcp_bag_sort_state_recorded")
            self.assertEqual(recorded["bag_sort_ops"], 1)
            self.assertEqual(recorded["bag_sort_stored"], True)
            self.assertEqual(recorded["bag_sort_outcome"], "created")
            self.assertEqual(recorded["bag_sort_value"], 234)
            self.assertEqual(recorded["bag_sort_origin"],
                             BAG_SORT_ORIGIN_LOCAL_OBSERVATION)
        finally:
            client.close()
        first.close()

        # The upgrade really happened, and it happened in that process.
        facts = snapshot_facts(self.data_dir / STORE_FILENAME)
        self.assertEqual(facts["schema_version"], str(SCHEMA_VERSION))
        self.assertEqual(sorted(facts["tables"]),
                         ["bag_sort_states", "card_instances", "card_pieces",
                          "card_special_inventory", "chapter_box_accepts",
                          "chapter_event_clears",
                          "chapter_event_reward_grants",
                          "chapter_fight_clears", "draw_pity",
                          "god_weapon_instances", "hotspot_draw_pity", "meta",
                          "role_serials", "role_values", "role_wallet", "roles",
                          "story_completions", "team_presets", "team_state"])
        self.assertTrue(snapshot_path(self.data_dir).is_file())

        # Epoch 2: a *second* process serves the same archive and replays the slot between
        # the role values and the clock, which is the whole point of the durable row.
        second = self._boot()
        client = _Client(second.tcp_port)
        try:
            chain = self._resume(
                client, [8, 10, 255, 256, 9,8, 554, 27, 1048, 0, 487, 9, 411, 1370, 283, 13, 85])
            replayed = [packet for packet in chain
                        if packet.spec.protocol_id == 1048]
            self.assertEqual(len(replayed), 1)
            self.assertEqual(tuple(replayed[0].fields), (1, 234))
        finally:
            client.close()
        second.close()
        self.assertEqual(self.data_dir.is_dir(), True)


# ------------------------------------------------------------------- inspection
#
# TASK-0089: `inspect_archive` is the answer a caller *outside* the server needs before
# it decides to launch one -- would an open serve this file, and would it have to upgrade
# it first?  It exists because the device layer used to answer that question itself, with
# its own copy of the version number, and refused a save the product migrates.

V2_SALT = "b2" * 32
V2_ROLE_ID = 9_000_002 * 100_000 + 1


def build_v2_archive(data_dir: Path, *, keep_wal_open: bool = False,
                     identity: dict | None = None, story: bool = True):
    """A real structure-2 archive: what the build before TASK-0084 wrote.

    Structure 2 is structure 1 plus `story_completions`, and the tables come from the
    product's own statement tuples, so this file is what the previous build's create path
    would have produced -- not a shape invented by a test.  `keep_wal_open` leaves the
    newest commit in a write-ahead log, which is the state the real save on this machine
    is in (a 4096-byte main file beside a 61 KB `-wal`).
    """

    data_dir.mkdir(parents=True, exist_ok=True)
    archive = data_dir / STORE_FILENAME
    key = v1_account_key(V2_SALT, identity)
    connection = sqlite3.connect(str(archive), isolation_level=None)
    try:
        if keep_wal_open:
            # WAL *before* the first table: every object below is born in the log, so the
            # main file really is the header page the real save's main file is.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA wal_autocheckpoint = 0")
        for statement in (_BASE_SCHEMA_STATEMENTS + _STORY_SCHEMA_STATEMENTS):
            connection.execute(statement)
        for name, value in (
            ("schema_version", "2"),
            ("account_key_salt", V2_SALT),
            ("account_key_derivation", ACCOUNT_KEY_DERIVATION),
            ("created_at", "1700000000"),
        ):
            connection.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (name, value))
        connection.execute(
            "INSERT INTO role_serials (id, next_serial) VALUES (1, ?)",
            (str(REVIVAL_LOCAL_ROLE_SERIAL_BASE + 1),))
        connection.execute(
            "INSERT INTO roles (account_key, server_id, role_id, role_name, career,"
            " create_time, identity_origin, compatibility_profile_id,"
            " structure_version, created_at, updated_at)"
            " VALUES (?, 1, ?, ?, 1, 1700000000, ?, ?, 2, 1700000000, 1700000000)",
            (key, V2_ROLE_ID, "旧档角色", IDENTITY_ORIGIN_LOCAL_TRANSACTION, PROFILE_ID))
        if story:
            connection.execute(
                "INSERT INTO story_completions (account_key, role_id, server_id,"
                " story_id, state, observed_at, evidence_kind, evidence_story_id,"
                " predecessor_sequence, successor_sequence, recorded_at, origin)"
                " VALUES (?, ?, 1, 1008, 2, 1700000100, ?, 1100, 13, 39, 1700000100, ?)",
                (key, V2_ROLE_ID, STORY_EVIDENCE_ORDERED_SUCCESSOR,
                 STORY_ORIGIN_LOCAL_OBSERVATION))
        connection.commit()
        if keep_wal_open:
            return connection, key, V2_ROLE_ID
    except BaseException:
        connection.close()
        raise
    connection.close()
    return None, key, V2_ROLE_ID


def structure_facts(path: Path) -> dict:
    """`{marker, tables}` of one archive, read read-only."""

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            "marker": connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0],
            "tables": sorted(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")),
        }
    finally:
        connection.close()


class ArchiveInspectionTests(unittest.TestCase):
    """Read-only: what an open would do with this file, said before anything opens it."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp(prefix="revival-inspect-test-")
        self.data_dir = Path(self.td)
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.archive = self.data_dir / STORE_FILENAME

    # ------------------------------------------------------------------ the two yeses

    def test_a_current_archive_is_inspected_as_served(self) -> None:
        store = RoleStore.at_directory(self.data_dir)
        try:
            store.create_or_get(account_key="a" * 64, server_id=1, role_name="甲",
                                career=1, create_time=1700000000,
                                compatibility_profile_id=PROFILE_ID)
        finally:
            store.close()
        report = inspect_archive(self.archive)
        self.assertTrue(report.would_be_served, report)
        self.assertFalse(report.would_migrate)
        self.assertEqual(report.verdict, INSPECTION_SERVED)
        self.assertEqual(report.schema_version, SCHEMA_VERSION)
        self.assertEqual(report.migration_steps, ())
        self.assertEqual(report.role_count, 1)
        self.assertEqual(report.quick_check, "ok")
        self.assertEqual(report.error, "")

    def test_a_structure_two_archive_is_inspected_as_a_source_it_would_upgrade(self) -> None:
        build_v2_archive(self.data_dir)
        report = inspect_archive(self.archive)
        self.assertTrue(report.would_be_served, report)
        self.assertTrue(report.would_migrate)
        self.assertEqual(report.verdict, INSPECTION_MIGRATED)
        self.assertEqual(report.schema_version, 2)
        self.assertEqual(report.migration_steps, (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13))
        self.assertEqual(report.missing_structure, ())
        self.assertEqual(report.role_count, 1)

    def test_a_structure_one_archive_names_every_step_it_would_walk(self) -> None:
        build_v1_archive(self.data_dir)
        report = inspect_archive(self.archive)
        self.assertTrue(report.would_migrate)
        self.assertEqual(report.migration_steps, (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13))
        self.assertEqual(report.target_version, SCHEMA_VERSION)

    # ------------------------------------------------------------- the promise is true

    def test_the_verdict_is_what_an_open_then_really_does(self) -> None:
        """The inspection's promise and the open's behaviour are one fact, twice.

        A source structure it calls `migrated` really is served, and its structure really
        is rewritten by the open; a file it calls `refused` really is refused by the open.
        """

        build_v2_archive(self.data_dir)
        self.assertTrue(inspect_archive(self.archive).would_migrate)
        store = RoleStore.at_directory(self.data_dir)
        try:
            self.assertEqual(store.count(), 1)
        finally:
            store.close()
        self.assertEqual(structure_facts(self.archive)["marker"], str(SCHEMA_VERSION))
        self.assertTrue(snapshot_path(self.data_dir, 2).is_file())

        false_marker = Path(tempfile.mkdtemp(prefix="revival-inspect-false-"))
        self.addCleanup(shutil.rmtree, false_marker, ignore_errors=True)
        build_v2_archive(false_marker)
        connection = sqlite3.connect(str(false_marker / STORE_FILENAME))
        try:
            # The TASK-0085 shape exactly: the marker says 3 while the table structure 3
            # is *about* was never created (nothing to drop -- structure 2 never had it).
            connection.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(inspect_archive(false_marker / STORE_FILENAME).error,
                         "structure_not_held")
        with self.assertRaises(RoleStoreUnusableError):
            RoleStore.at_directory(false_marker)

    def test_nothing_is_written_by_an_inspection(self) -> None:
        build_v2_archive(self.data_dir)
        before = self.archive.read_bytes()
        before_stat = self.archive.stat()
        report = inspect_archive(self.archive)
        self.assertTrue(report.would_migrate)
        self.assertEqual(self.archive.read_bytes(), before)
        self.assertEqual(self.archive.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertFalse(snapshot_path(self.data_dir, 2).exists())
        self.assertEqual(structure_facts(self.archive)["marker"], "2")

    # ------------------------------------------------------------------- the refusals

    def test_a_marker_that_promises_a_table_the_file_lacks_is_refused(self) -> None:
        build_v2_archive(self.data_dir)
        connection = sqlite3.connect(str(self.archive))
        try:
            connection.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
            connection.commit()
        finally:
            connection.close()
        report = inspect_archive(self.archive)
        self.assertFalse(report.would_be_served)
        self.assertEqual(report.error, "structure_not_held")
        self.assertEqual(report.schema_version, 3)
        self.assertIn("bag_sort_states", report.missing_structure_text())

    def test_a_table_missing_columns_is_refused(self) -> None:
        build_v2_archive(self.data_dir)
        connection = sqlite3.connect(str(self.archive))
        try:
            connection.execute("ALTER TABLE story_completions DROP COLUMN origin")
            connection.commit()
        finally:
            connection.close()
        report = inspect_archive(self.archive)
        self.assertEqual(report.error, "structure_not_held")
        self.assertIn("story_completions", report.missing_structure_text())
        self.assertIn("origin", report.missing_structure_text())

    def test_an_older_structure_missing_its_own_table_is_refused(self) -> None:
        build_v2_archive(self.data_dir)
        connection = sqlite3.connect(str(self.archive))
        try:
            connection.execute("DROP TABLE story_completions")
            connection.commit()
        finally:
            connection.close()
        report = inspect_archive(self.archive)
        self.assertEqual(report.error, "structure_not_held")
        self.assertEqual(report.schema_version, 2)

    def test_a_newer_structure_is_refused_by_name(self) -> None:
        build_v2_archive(self.data_dir)
        connection = sqlite3.connect(str(self.archive))
        try:
            connection.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                               (str(SCHEMA_VERSION + 1),))
            connection.commit()
        finally:
            connection.close()
        report = inspect_archive(self.archive)
        self.assertFalse(report.would_be_served)
        self.assertEqual(report.error, "newer_structure")
        self.assertEqual(report.schema_version, SCHEMA_VERSION + 1)

    def test_an_archive_that_lost_an_identity_marker_is_refused(self) -> None:
        for key in ("account_key_salt", "account_key_derivation"):
            with self.subTest(key=key):
                td = tempfile.mkdtemp(prefix="revival-inspect-ident-")
                self.addCleanup(shutil.rmtree, td, ignore_errors=True)
                build_v2_archive(Path(td))
                connection = sqlite3.connect(str(Path(td) / STORE_FILENAME))
                try:
                    connection.execute("DELETE FROM meta WHERE key = ?", (key,))
                    connection.commit()
                finally:
                    connection.close()
                report = inspect_archive(Path(td) / STORE_FILENAME)
                self.assertEqual(report.error, "identity_lost")
                self.assertEqual(report.identity_missing, (key,))

    def test_a_lost_serial_row_is_refused(self) -> None:
        build_v2_archive(self.data_dir)
        connection = sqlite3.connect(str(self.archive))
        try:
            connection.execute("DELETE FROM role_serials")
            connection.commit()
        finally:
            connection.close()
        report = inspect_archive(self.archive)
        self.assertEqual(report.error, "identity_lost")
        self.assertEqual(report.identity_missing, ("role_serials.next_serial",))

    def test_a_table_a_pending_step_would_create_is_refused_when_present(self) -> None:
        build_v2_archive(self.data_dir)
        connection = sqlite3.connect(str(self.archive))
        try:
            for statement in _BAG_SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.commit()
        finally:
            connection.close()
        report = inspect_archive(self.archive)
        self.assertEqual(report.error, "pending_table_present")
        self.assertEqual(report.pending_tables_present, ("bag_sort_states",))

    def test_a_missing_file_is_refused_and_never_created(self) -> None:
        report = inspect_archive(self.archive)
        self.assertFalse(report.would_be_served)
        self.assertEqual(report.error, "not_a_file")
        self.assertFalse(self.archive.exists())

    def test_a_file_that_is_not_a_database_is_refused(self) -> None:
        self.archive.write_bytes(b"this is not a database at all, not even close")
        report = inspect_archive(self.archive)
        self.assertEqual(report.error, "header_not_sqlite")
        self.assertEqual(self.archive.read_bytes(),
                         b"this is not a database at all, not even close")

    def test_a_damaged_database_is_refused_rather_than_raised(self) -> None:
        build_v2_archive(self.data_dir)
        data = bytearray(self.archive.read_bytes())
        data[100:4000] = b"\x00" * 3900
        self.archive.write_bytes(bytes(data))
        report = inspect_archive(self.archive)
        self.assertFalse(report.would_be_served)
        self.assertIn(report.error, ("not_sound", "query_failed"))
        self.assertFalse(snapshot_path(self.data_dir, 2).exists())

    # ------------------------------------------------------------------- the WAL shape

    def test_an_archive_whose_commits_are_in_its_wal_is_inspected_through_the_wal(
            self) -> None:
        """The real save's shape: the main file is a header page, the rows are in `-wal`.

        Reading the main file's raw bytes would give a database with no tables at all;
        the inspection goes through SQLite, which reads the log, and it is proved here to
        answer the *true* structure rather than the main file's stale one.
        """

        holding, key, role_id = build_v2_archive(self.data_dir, keep_wal_open=True)
        try:
            report = inspect_archive(self.archive)
            self.assertEqual(report.schema_version, 2)
            self.assertEqual(report.role_count, 1)
            self.assertEqual(report.verdict, INSPECTION_MIGRATED)
            # The main file holds the header page and nothing else: every number above
            # came out of the log, which is the only place the rows are.
            self.assertLess(self.archive.stat().st_size, 8192)
            self.assertTrue((self.data_dir / (STORE_FILENAME + "-wal")).is_file())
            # ... and a copy of the main file alone has no tables at all.
            main_only = self.data_dir.parent / "main-only.sqlite3"
            main_only.write_bytes(self.archive.read_bytes())
            missing = inspect_archive(main_only)
            self.assertFalse(missing.would_be_served)
            self.assertIn(missing.error, ("query_failed", "no_marker", "not_sound"))
        finally:
            holding.close()
        # And the same archive, served for real: the WAL is read, the role row is there
        # and the structure is upgraded behind a snapshot.
        store = RoleStore.at_directory(self.data_dir)
        try:
            record = store.lookup(key)
            self.assertIsNotNone(record)
            self.assertEqual(record.role_id, role_id)
            self.assertEqual(len(store.story_completions(account_key=key)), 1)
        finally:
            store.close()
        self.assertEqual(structure_facts(self.archive)["marker"], str(SCHEMA_VERSION))


LEDGER_FIXED_NOW = 1_700_000_500


class ChapterEventRewardLedgerTests(unittest.TestCase):
    """TASK-0222: the structure-12 event reward ledger is additive and honest.

    The recovered reward (``guankaReward[25101].crystal = 10``) is recorded, but
    the shipped row names no crystal bucket, so the ledger stores SQL NULL with
    ``bucket_provenance='unknown'`` and never touches ``role_wallet``.  These
    tests pin the additive structure, the one-time backfill, the one-transaction
    grant, the closed bucket vocabulary and the rollback of the clear row when
    the ledger write refuses.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"
        self.store = RoleStore.at_directory(
            self.data_dir, time_source=lambda: LEDGER_FIXED_NOW
        )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _role(self):
        key = self.store.account_key(**IDENTITY)
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="账本道友",
            career=1,
            create_time=LEDGER_FIXED_NOW,
            compatibility_profile_id=PROFILE_ID,
        )
        return key, record

    def _rewind_to_v11(self) -> None:
        """Make this archive the structure 11 build would have written.

        Structure 12 differs from structure 11 by exactly one table and the
        marker, so dropping that table and moving the marker back reproduces
        the previous build's archive with every other row untouched.
        """

        self.store.close()
        connection = sqlite3.connect(str(self.data_dir / STORE_FILENAME))
        try:
            connection.execute("DROP TABLE chapter_event_reward_grants")
            connection.execute(
                "UPDATE meta SET value = '11' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        self.store = RoleStore.at_directory(
            self.data_dir, time_source=lambda: LEDGER_FIXED_NOW
        )

    # ------------------------------------------------------------- structure

    def test_structure_12_declares_the_ledger_and_structure_11_does_not(self):
        table = "chapter_event_reward_grants"
        self.assertNotIn(table, _REQUIRED_STRUCTURE[11])
        self.assertEqual(
            _REQUIRED_STRUCTURE[12][table],
            (
                "account_key",
                "role_id",
                "server_id",
                "chapter_id",
                "map_index",
                "event_cfg_id",
                "option_index",
                "reward_id",
                "crystal",
                "crystal_bucket",
                "bucket_provenance",
                "granted_at",
                "request_sequence",
                "reply_sequence",
                "origin",
                "provenance",
            ),
        )
        # The closed bucket vocabulary is exactly the six S2C 10 field names.
        from revival_stub.role_state import ROLE_INFO_FIELD_NAMES

        self.assertEqual(
            tuple(CRYSTAL_BUCKET_NAMES),
            tuple(
                name for name in ROLE_INFO_FIELD_NAMES
                if name.startswith("crystal_")
            ),
        )

    # --------------------------------------------------------------- backfill

    def test_a_version_11_archive_backfills_its_clear_exactly_once(self):
        key, record = self._role()
        clear, created = self.store.record_chapter_event_clear(
            record=record,
            chapter_id=1,
            map_index=36,
            event_cfg_id=812003,
            option_index=1,
            observed_at=LEDGER_FIXED_NOW,
            request_sequence=187,
            reply_sequence=188,
        )
        self.assertTrue(created)
        before = int(time.time())
        self._rewind_to_v11()
        self.assertEqual(self.store._meta("schema_version"), str(SCHEMA_VERSION))
        grant = self.store.chapter_event_reward_grant(
            account_key=key, chapter_id=1, map_index=36
        )
        self.assertIsNotNone(grant)
        assert grant is not None
        self.assertEqual(
            (grant.reward_id, grant.crystal), (cme.REWARD_ID, cme.REWARD_CRYSTAL)
        )
        self.assertIsNone(grant.crystal_bucket)
        self.assertEqual(
            grant.bucket_provenance, CRYSTAL_BUCKET_PROVENANCE_UNKNOWN
        )
        self.assertEqual(grant.origin, CHAPTER_EVENT_REWARD_ORIGIN_SCHEMA_BACKFILL)
        self.assertEqual(grant.provenance, CHAPTER_EVENT_REWARD_PROVENANCE_MIGRATION)
        self.assertEqual(
            (grant.request_sequence, grant.reply_sequence),
            (clear.request_sequence, clear.reply_sequence),
        )
        self.assertGreaterEqual(grant.granted_at, before)
        self.assertLessEqual(grant.granted_at, int(time.time()))
        self.assertEqual(
            self.store.chapter_event_reward_ledger_total(account_key=key), 10
        )
        granted_at = grant.granted_at

        # Reopening a current archive runs no migration and writes no second row.
        self.store.close()
        self.store = RoleStore.at_directory(
            self.data_dir, time_source=lambda: LEDGER_FIXED_NOW
        )
        again = self.store.chapter_event_reward_grant(
            account_key=key, chapter_id=1, map_index=36
        )
        assert again is not None
        self.assertEqual(again, grant)
        self.assertEqual(again.granted_at, granted_at)
        self.assertEqual(
            len(self.store.chapter_event_reward_grants(account_key=key)), 1
        )

    def test_a_version_11_archive_without_the_recovered_clear_backfills_nothing(self):
        key, record = self._role()
        self.store.record_chapter_event_clear(
            record=record,
            chapter_id=2,
            map_index=5,
            event_cfg_id=820000,
            option_index=1,
            observed_at=LEDGER_FIXED_NOW,
            request_sequence=1,
            reply_sequence=2,
        )
        self._rewind_to_v11()
        self.assertEqual(
            self.store.chapter_event_reward_grants(account_key=key), ()
        )
        self.assertEqual(
            self.store.chapter_event_reward_ledger_total(account_key=key), 0
        )

    # -------------------------------------------------------------- the grant

    def test_the_first_grant_is_written_with_the_clear_and_a_replay_is_idempotent(
        self,
    ):
        key, record = self._role()
        outcome = self.store.record_chapter_event_clear_with_grant(
            record=record,
            chapter_id=1,
            map_index=36,
            event_cfg_id=812003,
            option_index=1,
            observed_at=LEDGER_FIXED_NOW,
            request_sequence=187,
            reply_sequence=188,
            reward_id=cme.REWARD_ID,
            reward_crystal=cme.REWARD_CRYSTAL,
            crystal_bucket=None,
            bucket_provenance=CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
        )
        self.assertTrue(outcome.created)
        self.assertTrue(outcome.grant_created)
        self.assertEqual(outcome.ledger_total_after, 10)
        self.assertEqual(outcome.grant.crystal, 10)
        self.assertIsNone(outcome.grant.crystal_bucket)
        self.assertEqual(
            outcome.grant.origin, CHAPTER_EVENT_REWARD_ORIGIN_LOCAL_OBSERVATION
        )
        self.assertEqual(
            outcome.grant.provenance, CHAPTER_EVENT_REWARD_PROVENANCE_TRANSACTION
        )
        self.assertIsNotNone(
            self.store.chapter_event_clear(
                account_key=key, chapter_id=1, map_index=36
            )
        )
        replay = self.store.record_chapter_event_clear_with_grant(
            record=record,
            chapter_id=1,
            map_index=36,
            event_cfg_id=812003,
            option_index=1,
            observed_at=LEDGER_FIXED_NOW + 60,
            request_sequence=300,
            reply_sequence=301,
            reward_id=cme.REWARD_ID,
            reward_crystal=cme.REWARD_CRYSTAL,
            crystal_bucket=None,
            bucket_provenance=CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
        )
        self.assertFalse(replay.created)
        self.assertFalse(replay.grant_created)
        self.assertEqual(replay.ledger_total_after, 10)
        self.assertEqual(replay.grant, outcome.grant)
        self.assertEqual(
            len(self.store.chapter_event_reward_grants(account_key=key)), 1
        )

    def test_a_guessed_or_inconsistent_bucket_is_refused(self):
        _, record = self._role()
        base = dict(
            record=record,
            chapter_id=1,
            map_index=36,
            event_cfg_id=812003,
            option_index=1,
            observed_at=LEDGER_FIXED_NOW,
            request_sequence=1,
            reply_sequence=2,
            reward_id=cme.REWARD_ID,
            reward_crystal=cme.REWARD_CRYSTAL,
        )
        for broken in (
            dict(crystal_bucket="UNKNOWN",
                 bucket_provenance=CRYSTAL_BUCKET_PROVENANCE_UNKNOWN),
            dict(crystal_bucket=None, bucket_provenance="official_recovered"),
            dict(crystal_bucket="crystal_free",
                 bucket_provenance=CRYSTAL_BUCKET_PROVENANCE_UNKNOWN),
            dict(crystal_bucket=None,
                 bucket_provenance="synthetic_compatibility"),
            dict(crystal_bucket=None,
                 bucket_provenance=CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
                 reward_crystal=0),
            dict(crystal_bucket=None,
                 bucket_provenance=CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
                 reward_id=0),
        ):
            with self.subTest(**broken):
                with self.assertRaises(RoleStoreError):
                    self.store.record_chapter_event_clear_with_grant(
                        **{**base, **broken}
                    )
        self.assertEqual(
            self.store.chapter_event_clears(account_key=record.account_key), ()
        )
        self.assertEqual(
            self.store.chapter_event_reward_grants(
                account_key=record.account_key
            ),
            (),
        )

    def test_a_ledger_refusal_rolls_back_the_clear_row(self):
        key, record = self._role()
        with mock.patch.object(
            RoleStore,
            "_write_chapter_event_reward_grant_locked",
            side_effect=RoleStoreError("forced ledger refusal"),
        ):
            with self.assertRaises(RoleStoreError):
                self.store.record_chapter_event_clear_with_grant(
                    record=record,
                    chapter_id=1,
                    map_index=36,
                    event_cfg_id=812003,
                    option_index=1,
                    observed_at=LEDGER_FIXED_NOW,
                    request_sequence=1,
                    reply_sequence=2,
                    reward_id=cme.REWARD_ID,
                    reward_crystal=cme.REWARD_CRYSTAL,
                    crystal_bucket=None,
                    bucket_provenance=CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
                )
        self.assertIsNone(
            self.store.chapter_event_clear(
                account_key=key, chapter_id=1, map_index=36
            )
        )
        self.assertEqual(
            self.store.chapter_event_reward_grants(account_key=key), ()
        )

    def test_the_ledger_rejects_rows_outside_its_closed_vocabulary(self):
        _, record = self._role()
        insert = (
            "INSERT INTO chapter_event_reward_grants"
            " (account_key, role_id, server_id, chapter_id, map_index,"
            " event_cfg_id, option_index, reward_id, crystal, crystal_bucket,"
            " bucket_provenance, granted_at, request_sequence, reply_sequence,"
            " origin, provenance)"
            " VALUES (?, ?, ?, 1, 36, 812003, 1, 25101, ?, ?, ?, 1, 1, 2,"
            " 'test', 'test')"
        )
        for crystal, bucket, provenance in (
            (0, None, CRYSTAL_BUCKET_PROVENANCE_UNKNOWN),
            (10, "crystal_unknown", "official_recovered"),
            (10, None, "guessed"),
        ):
            with self.subTest(crystal=crystal, bucket=bucket):
                try:
                    self.store._conn.execute(
                        insert,
                        (
                            record.account_key,
                            record.role_id,
                            record.server_id,
                            crystal,
                            bucket,
                            provenance,
                        ),
                    )
                except sqlite3.IntegrityError:
                    pass
                else:  # pragma: no cover - the CHECK must refuse this row
                    self.fail(f"the ledger accepted {crystal}/{bucket}/{provenance}")
                finally:
                    self.store._conn.rollback()


class FightSpecialCardTransactionTests(unittest.TestCase):
    """TASK-0223: the fight clear, its money and its dog food are one transaction.

    Fight 102071's recovered reward (``specialCard`` cfg id 1, the 1★ dog food)
    is added to the existing ``card_special_inventory`` table inside the same
    ``BEGIN IMMEDIATE`` that writes ``chapter_fight_clears`` and (when the fight
    pays) ``role_wallet.money``.  A refusal rolls the whole settlement back; a
    replay returns the stored rows and adds nothing.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"
        self.store = RoleStore.at_directory(
            self.data_dir, time_source=lambda: LEDGER_FIXED_NOW
        )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _role(self):
        key = self.store.account_key(**IDENTITY)
        record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="狗粮道友",
            career=1,
            create_time=LEDGER_FIXED_NOW,
            compatibility_profile_id=PROFILE_ID,
        )
        return key, record

    def _record(self, record, **overrides):
        arguments = dict(
            record=record,
            chapter_id=2,
            fight_id=102071,
            map_index=7,
            observed_at=LEDGER_FIXED_NOW,
            request_sequence=1,
            settlement_sequence=2,
            reply_sequence=3,
            special_food={1: 1},
            special_provenance=FIGHT_SPECIAL_CARD_ORIGIN,
        )
        arguments.update(overrides)
        return self.store.record_chapter_fight_clear_with_money(**arguments)

    def test_the_clear_money_and_food_are_one_transaction(self):
        key, record = self._role()
        outcome = self._record(record, wallet_money=40, wallet_provenance="test")
        self.assertTrue(outcome.created)
        self.assertEqual(outcome.money_granted, 40.0)
        self.assertEqual(outcome.special_food_granted, {1: 1})
        self.assertEqual(outcome.special_counts_after, {1: 1})
        self.assertEqual(
            (outcome.money_before, outcome.money_after), (0.0, 40.0)
        )
        self.assertIsNotNone(
            self.store.chapter_fight_clear(
                account_key=key, chapter_id=2, fight_id=102071
            )
        )
        self.assertEqual(self.store.role_wallet_values(account_key=key),
                         {"money": 40.0})
        self.assertEqual(self.store.card_special_counts(account_key=key),
                         {1: 1})
        self.assertEqual(list(self.store.card_instances(account_key=key)), [])

    def test_a_replay_returns_the_rows_and_never_adds_food_or_money(self):
        key, record = self._role()
        self._record(record)
        replay = self._record(
            record,
            observed_at=LEDGER_FIXED_NOW + 10,
            request_sequence=4,
            settlement_sequence=5,
            reply_sequence=6,
        )
        self.assertFalse(replay.created)
        self.assertEqual(replay.special_food_granted, {})
        self.assertEqual(replay.special_counts_after, {1: 1})
        self.assertEqual(replay.money_granted, 0.0)
        self.assertEqual(replay.money_after, 0.0)
        self.assertEqual(self.store.card_special_counts(account_key=key),
                         {1: 1})

    def test_a_food_refusal_rolls_back_the_clear_and_the_money(self):
        key, record = self._role()
        with mock.patch.object(
            RoleStore,
            "_add_card_special_locked",
            side_effect=RoleStoreError("forced dog-food refusal"),
        ):
            with self.assertRaises(RoleStoreError):
                self._record(
                    record, wallet_money=40, wallet_provenance="test"
                )
        self.assertIsNone(
            self.store.chapter_fight_clear(
                account_key=key, chapter_id=2, fight_id=102071
            )
        )
        self.assertEqual(self.store.role_wallet_values(account_key=key), {})
        self.assertEqual(self.store.card_special_counts(account_key=key), {})

    def test_bad_food_requests_are_refused_before_writing(self):
        key, record = self._role()
        for broken in (
            dict(special_food={0: 1}),
            dict(special_food={1: 0}),
            dict(special_food={1: -1}),
            dict(special_food={1: 1}, special_provenance=""),
        ):
            with self.subTest(**broken):
                with self.assertRaises(RoleStoreError):
                    self._record(record, **broken)
        self.assertIsNone(
            self.store.chapter_fight_clear(
                account_key=key, chapter_id=2, fight_id=102071
            )
        )
        self.assertEqual(self.store.card_special_counts(account_key=key), {})

    def test_a_file_archive_reopens_with_the_food_and_does_not_add_it_again(self):
        key, record = self._role()
        self._record(record)
        self.store.close()
        self.store = RoleStore.at_directory(
            self.data_dir, time_source=lambda: LEDGER_FIXED_NOW
        )
        self.assertEqual(
            self.store.card_special_counts(account_key=key), {1: 1}
        )
        replay = self.store.record_chapter_fight_clear_with_money(
            record=record,
            chapter_id=2,
            fight_id=102071,
            map_index=7,
            observed_at=LEDGER_FIXED_NOW,
            request_sequence=7,
            settlement_sequence=8,
            reply_sequence=9,
            special_food={1: 1},
            special_provenance=FIGHT_SPECIAL_CARD_ORIGIN,
        )
        self.assertFalse(replay.created)
        self.assertEqual(replay.special_food_granted, {})
        self.assertEqual(
            self.store.card_special_counts(account_key=key), {1: 1}
        )


class DevPlayTicketLedgerTests(unittest.TestCase):
    """TASK-0235: the ticket ledger is one transaction with the card rows.

    The ``meta`` table carries the two counts (no new table, no schema change).
    The store only enforces the invariants; the payload shape is the caller's
    strict codec, so these tests use a plain two-int JSON codec.
    """

    META_KEY = "dev_play_ticket_balance"
    SEED = {"lottery_ticket": 30, "lottery_ticket_advanced": 30}

    @staticmethod
    def _decode(raw: str) -> dict[str, int]:
        payload = json.loads(raw)
        return {
            "lottery_ticket": payload.get("lottery_ticket"),
            "lottery_ticket_advanced": payload.get("lottery_ticket_advanced"),
        }

    @staticmethod
    def _encode(counts) -> str:
        return json.dumps(dict(counts), sort_keys=True)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "roles"
        self.store = RoleStore.at_directory(
            self.data_dir, time_source=lambda: LEDGER_FIXED_NOW
        )
        key = self.store.account_key(**IDENTITY)
        self.record, _ = self.store.create_or_get(
            account_key=key,
            server_id=1.0,
            role_name="祈灵道友",
            career=1,
            create_time=LEDGER_FIXED_NOW,
            compatibility_profile_id=PROFILE_ID,
        )
        self.key = key

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _read(self):
        return self.store.read_ticket_balance(
            meta_key=self.META_KEY,
            seed_counts=self.SEED,
            decode=self._decode,
            encode=self._encode,
        )

    def _draw(self, **overrides):
        arguments = dict(
            record=self.record,
            meta_key=self.META_KEY,
            balance_field="lottery_ticket",
            seed_counts=self.SEED,
            cost=10,
            cards=(
                CardInstanceWrite(instance_id="ticket-draw-1", card_cfg_id=2209),
                CardInstanceWrite(instance_id="ticket-draw-2", card_cfg_id=3312),
            ),
            decode=self._decode,
            encode=self._encode,
            provenance="test_provenance",
        )
        arguments.update(overrides)
        return self.store.record_ticket_draw(**arguments)

    def test_the_missing_ledger_is_seeded_once_inside_the_transaction(self):
        counts, created = self._read()
        self.assertTrue(created)
        self.assertEqual(counts, self.SEED)
        stored = json.loads(self.store.get_meta(self.META_KEY))
        self.assertEqual(stored, self.SEED)
        again, created_again = self._read()
        self.assertFalse(created_again)
        self.assertEqual(again, self.SEED)

    def test_a_draw_charges_the_balance_and_writes_every_card_row(self):
        # No login ran in this test, so the first draw seeds the ledger and
        # charges it in the same transaction.
        outcome = self._draw()
        self.assertEqual((outcome.balance_before, outcome.balance_after), (30, 20))
        self.assertTrue(outcome.balance_seeded)
        self.assertEqual(
            [row.instance_id for row in outcome.rows],
            ["ticket-draw-1", "ticket-draw-2"],
        )
        self.assertEqual(
            self._read()[0],
            {"lottery_ticket": 20, "lottery_ticket_advanced": 30},
        )
        self.assertIsNotNone(
            self.store.card_instance(
                account_key=self.key, instance_id="ticket-draw-1"
            )
        )
        second = self._draw(
            cost=1, cards=(CardInstanceWrite("ticket-draw-3", 2209),)
        )
        self.assertFalse(second.balance_seeded)
        self.assertEqual(
            (second.balance_before, second.balance_after), (20, 19)
        )

    def test_a_shortfall_writes_neither_the_balance_nor_a_card(self):
        self._draw(cost=30)
        self.assertEqual(self._read()[0]["lottery_ticket"], 0)
        with self.assertRaises(TicketBalanceShortfallError) as caught:
            self._draw(cost=1, cards=(CardInstanceWrite("shortfall", 2209),))
        self.assertEqual(caught.exception.balance, 0)
        self.assertEqual(caught.exception.cost, 1)
        self.assertEqual(caught.exception.balance_field, "lottery_ticket")
        self.assertEqual(self._read()[0]["lottery_ticket"], 0)
        self.assertIsNone(
            self.store.card_instance(
                account_key=self.key, instance_id="shortfall"
            )
        )

    def test_a_card_write_failure_rolls_the_charge_back(self):
        original = self.store._write_card_instance_locked
        calls = {"count": 0}

        def flaky(**kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RoleStoreError("forced card write failure")
            return original(**kwargs)

        with mock.patch.object(
            self.store, "_write_card_instance_locked", side_effect=flaky
        ):
            with self.assertRaises(RoleStoreError):
                self._draw()
        self.assertEqual(calls["count"], 2)
        # The seeded ledger write, the charge and the first card row all rolled
        # back: the marker is absent again and no card exists.
        self.assertIsNone(self.store.get_meta(self.META_KEY))
        self.assertEqual(list(self.store.card_instances(account_key=self.key)), [])
        # The next read seeds it at 30/30, proving nothing leaked.
        counts, created = self._read()
        self.assertTrue(created)
        self.assertEqual(counts, self.SEED)

    def test_an_unusable_codec_or_ledger_rolls_back(self):
        self.store.set_meta(self.META_KEY, "not json")
        with self.assertRaises(ValueError):
            self._draw()
        self.assertIsNone(
            self.store.card_instance(
                account_key=self.key, instance_id="ticket-draw-1"
            )
        )
        self.assertEqual(self.store.get_meta(self.META_KEY), "not json")
        # A codec that names other fields is refused instead of partially read.
        self.store.set_meta(self.META_KEY, json.dumps({"other": 1}))
        with self.assertRaises(RoleStoreError):
            self._draw()

    def test_unusable_requests_are_refused_before_any_write(self):
        for broken in (
            dict(cost=0),
            dict(cards=()),
            dict(provenance=""),
            dict(
                cards=(
                    CardInstanceWrite("a", 2209),
                    CardInstanceWrite("a", 3312),
                )
            ),
            dict(cards=(CardInstanceWrite("", 2209),)),
            dict(cards=(CardInstanceWrite("x", 0),)),
            dict(cards=(CardInstanceWrite("x", 2209, level=-1),)),
            dict(
                cards=(
                    CardInstanceWrite(
                        "x", 2209, fixture_kind="unknown_kind"
                    ),
                )
            ),
        ):
            with self.subTest(**{k: repr(v) for k, v in broken.items()}):
                with self.assertRaises(RoleStoreError):
                    self._draw(**broken)
        self.assertIsNone(self.store.get_meta(self.META_KEY))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
