"""TASK-0071: the named, durable play profile and the normal-play entry.

The edge this module exists for is the one a *player* hits, not a test runner: a
named profile is initialised once, a real client creates its character with the
name it typed, the serving process ends, and the same name serves the same
character again -- twice over -- with no `noRole`, no `S2C 14`, no second
`C2S 2` and no per-RUN reset.

TASK-0072 adds the other half of that promise, in
`PlayProfileArchiveGuardTests`: the save the profile serves is a *file*.  A
`resume` whose archive is missing, half-built, a link or hard link of another
file, or replaced between the check and the open is refused -- before the lease,
before `sqlite3.connect`, and before a byte is written -- and only an explicit
`init` ever creates a profile's first archive.

Every directory used here is inside one temporary test root.  Nothing in this
module may read, write or resolve into the real per-user archive: the resolved
default archive path is redirected at its own documented override in `setUp`
and is only ever *compared* against.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from revival_stub import app as app_module
from revival_stub import play as play_module
from revival_stub import role_store as role_store_module
from revival_stub.app import (
    DATA_DIR_ENV,
    ROLE_MODE_FRESH_TEST,
    ROLE_MODE_PLAY_PROFILE,
    ROLE_MODE_RESUME,
    build_parser,
    build_role_store,
    run,
)
from revival_stub.codec import (
    NeedMoreData,
    ReaderState,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import PROFILE_ID as COMPATIBILITY_PROFILE_ID
from revival_stub.eventlog import EventLogger
from revival_stub.play_profile import (
    PROFILE_FORMAT_VERSION,
    PROFILE_LOCK_FILENAME,
    PROFILE_MANIFEST_FILENAME,
    PROFILE_LOCK_RECORD_BYTES,
    PROFILE_LOCK_RECORD_OFFSET,
    PROFILE_MODE_INIT,
    PROFILE_MODE_RESUME,
    PROFILE_ORIGIN_LOCAL_INIT,
    REASON_ALREADY_EXISTS,
    REASON_ARCHIVE_MISSING,
    REASON_ARCHIVE_REFUSED,
    REASON_DIRECTORY_REFUSED,
    REASON_FORMAT_NEWER,
    REASON_FORMAT_UNMIGRATED,
    REASON_ID_INVALID,
    REASON_IDENTITY_MISMATCH,
    REASON_INSTANCE_CONFLICT,
    REASON_MANIFEST_INCOMPLETE,
    REASON_MANIFEST_MISSING,
    REASON_MANIFEST_UNREADABLE,
    REASON_MANIFEST_UNRECOGNISED,
    REASON_MISSING,
    REASON_MODE_INVALID,
    REASON_ROOT_REFUSED,
    PlayProfileRefusedError,
    ProfileArchiveGuard,
    acquire_instance_lock,
    default_profile_root,
    read_lock_record,
    select_play_profile,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_store import (
    KIND_PERSISTENT,
    KIND_PLAY_PROFILE,
    STORE_FILENAME,
    RoleStore,
    RoleStoreUnusableError,
)
from revival_stub.sim_client import DEFAULT_LOGIN_FIELDS

FIXED_NOW = 1_700_000_000
PROFILE_ID = "dev-main"

#: The recovered S2C ids of one complete create session and of the resumed one
#: that must follow it.  Byte-for-byte the same chains `test_role_relogin.py`
#: pins for the path-addressed archive, asserted here for the *named* profile.
#: TASK-0092 / profile v5: the Team + fabao-select login snapshot, sent
#: immediately before S2C411 by the terminal login segment.
#: TASK-0094 / profile v6: ONE recharge-activity packet, sent after the
#: team block and before S2C411.
TEAM_SNAPSHOT_CHAIN = [36, 37, 48, 35]
#: TASK-0245 / profile v22: the client's own team power, right after S2C 35.
TEAM_MAX_FIGHT_CHAIN = [39]
RECHARGE_ACTIVITY_CHAIN = [1014]
CHAPTER_TASK_CHAIN = [385, 387, 386]  # v9 pair + TASK-0180 v15 row  # TASK-0104 / profile v9
CHAPTER_ITEM_CHAIN = [389, 391]  # TASK-0098 / profile v7
CHAPTER_MACHINE_CHAIN = [400, 402]  # TASK-0109 / profile v10 (after the item pair)
EXPERIENCE_TASK_CHAIN = [1307, 1309]  # TASK-0102 / profile v8
TERMINAL_CHAIN = [
    *TEAM_SNAPSHOT_CHAIN, *TEAM_MAX_FIGHT_CHAIN, *RECHARGE_ACTIVITY_CHAIN,
    *CHAPTER_TASK_CHAIN, *CHAPTER_ITEM_CHAIN, *CHAPTER_MACHINE_CHAIN,
    *EXPERIENCE_TASK_CHAIN,
]
#: TASK-0167 / profile v13: ONE prison-fight boss snapshot (S2C 1370), sent
#: after the S2C411/412 story state and immediately before S2C283.
PRISON_FIGHT_CHAIN = [1370]
#: TASK-0247 / profile v24: ONE inactive bitRabbit state (S2C 1435
#: syncBitRabbit), appended after S2C 555 returnTalentSoulSkill.
BIT_RABBIT_CHAIN = [1435]
NEW_ROLE_CHAIN = [5, 14, 8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, *TERMINAL_CHAIN, 411, *PRISON_FIGHT_CHAIN, 283, 555, *BIT_RABBIT_CHAIN, 13, 85]
RESUMED_ROLE_CHAIN = [8, 10, 255, 256, 9,8, 554, 27, 0, 487, 9, *TERMINAL_CHAIN, 411, *PRISON_FIGHT_CHAIN, 283, 555, *BIT_RABBIT_CHAIN, 13, 85]

#: How far the session has got after each phase.
#: TASK-0242 / profile v21 inserts the two empty mail-container packets after
#: S2C 10, so the base segment is two packets longer.
NEW_AFTER_CREATE = 7
NEW_AFTER_TERMINAL = 15 + len(TERMINAL_CHAIN) + len(PRISON_FIGHT_CHAIN)
NEW_COMPLETE = len(NEW_ROLE_CHAIN)
RESUMED_AFTER_LOGIN = 5
RESUMED_AFTER_TERMINAL = 13 + len(TERMINAL_CHAIN) + len(PRISON_FIGHT_CHAIN)
RESUMED_COMPLETE = len(RESUMED_ROLE_CHAIN)
NEW_SNAPSHOT_INDEX = NEW_ROLE_CHAIN.index(10)
RESUMED_SNAPSHOT_INDEX = RESUMED_ROLE_CHAIN.index(10)

#: The name the *client* types.  Deliberately not `AutoTest01`: the client's own
#: `C2S 2` is the only name evidence there is, and no test may pre-fill one.
CLIENT_ROLE_NAME = "灯下客"


def login_fields(
    *,
    server_id: float = 1.0,
    sdk_user_id: bytes = b"local-user",
    device_id: bytes = b"local-device",
) -> list:
    fields = list(DEFAULT_LOGIN_FIELDS)
    fields[0] = server_id
    fields[1] = sdk_user_id
    fields[8] = device_id
    return fields


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under ``root``, by relative name."""

    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def protected_bytes(root: Path) -> dict[str, bytes]:
    """The same, minus the instance lease: a lease is a lock, not saved state."""

    return {
        name: blob
        for name, blob in tree_bytes(root).items()
        if Path(name).name != PROFILE_LOCK_FILENAME
    }


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Session:
    """One client connection with its own rolling codec state."""

    def __init__(self, port: int) -> None:
        self.stream = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.stream.settimeout(5)
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
                        f"server closed after {len(self.responses)} response(s); "
                        f"ids={self.ids()}"
                    )
                self.buffer.extend(chunk)
                continue
            del self.buffer[: packet.consumed]
            self.responses.append(packet)
        return self.responses

    def ids(self) -> list:
        return [packet.spec.protocol_id for packet in self.responses]

    def quiet_state(self) -> str:
        self.stream.settimeout(0.2)
        try:
            chunk = self.stream.recv(1)
        except socket.timeout:
            return "quiet"
        finally:
            self.stream.settimeout(5)
        return "closed" if not chunk else "unexpected_data"

    def close(self) -> None:
        self.stream.close()


class _ProfileTestCase(unittest.TestCase):
    """One temporary test root: a profile collection and a redirected default."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.profile_root = self.root / "profiles"
        self.default_archive = self.root / "user-data"
        self._env = dict(os.environ)
        # The real per-user archive must never be opened, created or compared
        # against by accident: redirect its own documented override.
        os.environ[DATA_DIR_ENV] = str(self.default_archive)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def select(
        self,
        mode: str = PROFILE_MODE_RESUME,
        *,
        profile_id: str = PROFILE_ID,
        root: Path | str | None = None,
        default_archive_dir: Path | None = None,
    ):
        return select_play_profile(
            profile_id,
            mode=mode,
            root=self.profile_root if root is None else root,
            default_archive_dir=default_archive_dir or self.default_archive,
            time_source=lambda: FIXED_NOW,
        )

    def profile_dir(self, profile_id: str = PROFILE_ID) -> Path:
        return self.profile_root / profile_id


class PlayProfileNameAndLocationTests(_ProfileTestCase):
    """The name, the root and the mapping: refused before anything is created."""

    def test_a_valid_name_is_the_only_thing_that_becomes_a_directory(self) -> None:
        for good in ("dev-main", "player1", "a-b-c", "main"):
            with self.subTest(name=good):
                directory = self.root / "roots" / good
                profile = self.select(
                    PROFILE_MODE_INIT, profile_id=good, root=directory.parent
                )
                self.assertEqual(profile.directory, directory)
                self.assertEqual(profile.profile_id, good)
                self.assertEqual(profile.directory.name, good)

    def test_an_invalid_name_is_refused_before_any_directory_exists(self) -> None:
        invalid = (
            "", "..", "../escape", "dev/main", "dev\\main", "..\\escape",
            "Dev-Main", "dev_main", "dev main", "-dev", "dev-", "a", "ab",
            "con", "nul", "com1", "d" * 33, ".hidden", "dev.main", "dev\u00e9",
        )
        for bad in invalid:
            with self.subTest(name=bad):
                with self.assertRaises(PlayProfileRefusedError) as caught:
                    self.select(PROFILE_MODE_INIT, profile_id=bad)
                self.assertEqual(caught.exception.reason, REASON_ID_INVALID)
        self.assertFalse(self.profile_root.exists(), "nothing may be created")
        self.assertFalse((self.root / "escape").exists())

    def test_the_root_may_not_be_inside_the_repository(self) -> None:
        repository = Path(app_module.__file__).resolve().parents[2]
        for candidate in (repository, repository / "revival_stub"):
            with self.subTest(root=candidate):
                with self.assertRaises(PlayProfileRefusedError) as caught:
                    self.select(PROFILE_MODE_INIT, root=candidate)
                self.assertEqual(caught.exception.reason, REASON_ROOT_REFUSED)
                self.assertFalse((candidate / PROFILE_ID).exists())

    def test_the_root_may_not_be_the_default_archive_directory(self) -> None:
        for candidate in (self.default_archive, self.default_archive / "profiles"):
            with self.subTest(root=candidate):
                with self.assertRaises(PlayProfileRefusedError) as caught:
                    self.select(PROFILE_MODE_INIT, root=candidate)
                self.assertEqual(caught.exception.reason, REASON_ROOT_REFUSED)
        self.assertFalse(self.default_archive.exists())

    def test_a_profile_may_not_live_in_a_throwaway_run_directory(self) -> None:
        for name in ("revival-role-relogin-run0021", "revival-fresh-role-test-abc"):
            ephemeral = self.root / name
            ephemeral.mkdir(parents=True, exist_ok=True)
            with self.subTest(root=name):
                with self.assertRaises(PlayProfileRefusedError) as caught:
                    self.select(PROFILE_MODE_INIT, root=ephemeral)
                self.assertEqual(caught.exception.reason, REASON_ROOT_REFUSED)
            # …and the name may not name the profile directory itself either.
            with self.subTest(directory=name):
                with self.assertRaises(PlayProfileRefusedError) as caught:
                    self.select(PROFILE_MODE_INIT, profile_id=name)
                self.assertEqual(caught.exception.reason, REASON_DIRECTORY_REFUSED)
        self.assertFalse(self.profile_root.exists())

    def test_resume_refuses_a_profile_that_was_never_initialised(self) -> None:
        with self.assertRaises(PlayProfileRefusedError) as caught:
            self.select(PROFILE_MODE_RESUME)
        self.assertEqual(caught.exception.reason, REASON_MISSING)
        # An uninitialised resume creates nothing at all, not even the root.
        self.assertFalse(self.profile_root.exists())

    def test_init_refuses_an_existing_directory_and_never_adopts_it(self) -> None:
        directory = self.profile_dir()
        directory.mkdir(parents=True)
        sentinel = directory / "someone-elses-save"
        sentinel.write_bytes(b"not a revival archive")
        before = protected_bytes(self.profile_root)
        with self.assertRaises(PlayProfileRefusedError) as caught:
            self.select(PROFILE_MODE_INIT)
        self.assertEqual(caught.exception.reason, REASON_ALREADY_EXISTS)
        self.assertEqual(protected_bytes(self.profile_root), before)
        self.assertFalse((directory / PROFILE_MANIFEST_FILENAME).exists())

    def test_a_profile_cannot_be_served_under_another_name(self) -> None:
        self.select(PROFILE_MODE_INIT)
        self.profile_dir().rename(self.profile_root / "main-dev")
        before = protected_bytes(self.profile_root)
        with self.assertRaises(PlayProfileRefusedError) as caught:
            self.select(PROFILE_MODE_RESUME, profile_id="main-dev")
        self.assertEqual(caught.exception.reason, REASON_IDENTITY_MISMATCH)
        self.assertEqual(protected_bytes(self.profile_root), before)

    def test_a_manifest_this_build_cannot_serve_is_refused_untouched(self) -> None:
        good = {
            "format": "revival-play-profile",
            "format_version": PROFILE_FORMAT_VERSION,
            "profile_id": PROFILE_ID,
            "profile_uid": "0" * 32,
            "created_at": FIXED_NOW,
            "created_by": PROFILE_ORIGIN_LOCAL_INIT,
        }
        cases = (
            ("missing", None, REASON_MANIFEST_MISSING),
            ("empty", b"", REASON_MANIFEST_UNREADABLE),
            ("not-json", b"{not json", REASON_MANIFEST_UNREADABLE),
            ("not-an-object", b"[1, 2, 3]", REASON_MANIFEST_UNREADABLE),
            ("foreign-format",
             json.dumps({**good, "format": "something-else"}).encode("utf-8"),
             REASON_MANIFEST_UNRECOGNISED),
            ("newer",
             json.dumps({**good, "format_version": PROFILE_FORMAT_VERSION + 1}).encode("utf-8"),
             REASON_FORMAT_NEWER),
            ("older", json.dumps({**good, "format_version": 0}).encode("utf-8"),
             REASON_FORMAT_UNMIGRATED),
            ("no-version",
             json.dumps({k: v for k, v in good.items() if k != "format_version"}).encode("utf-8"),
             REASON_MANIFEST_INCOMPLETE),
            ("version-as-text", json.dumps({**good, "format_version": "1"}).encode("utf-8"),
             REASON_MANIFEST_INCOMPLETE),
            ("no-id",
             json.dumps({k: v for k, v in good.items() if k != "profile_id"}).encode("utf-8"),
             REASON_MANIFEST_INCOMPLETE),
            ("no-uid",
             json.dumps({k: v for k, v in good.items() if k != "profile_uid"}).encode("utf-8"),
             REASON_MANIFEST_INCOMPLETE),
            ("no-created-at",
             json.dumps({k: v for k, v in good.items() if k != "created_at"}).encode("utf-8"),
             REASON_MANIFEST_INCOMPLETE),
            ("no-origin",
             json.dumps({k: v for k, v in good.items() if k != "created_by"}).encode("utf-8"),
             REASON_MANIFEST_INCOMPLETE),
        )
        for label, payload, reason in cases:
            with self.subTest(case=label):
                directory = self.profile_dir()
                if directory.exists():
                    shutil.rmtree(directory)
                directory.mkdir(parents=True)
                if payload is not None:
                    (directory / PROFILE_MANIFEST_FILENAME).write_bytes(payload)
                before = protected_bytes(self.profile_root)
                with self.assertRaises(PlayProfileRefusedError) as caught:
                    self.select(PROFILE_MODE_RESUME)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(protected_bytes(self.profile_root), before)

    def test_a_symlinked_profile_directory_cannot_escape_the_root(self) -> None:
        other_root = self.root / "elsewhere"
        real = self.select(PROFILE_MODE_INIT, root=other_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        link = self.profile_dir()
        try:
            link.symlink_to(real.directory, target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover - privilege dependent
            self.skipTest("this host cannot create a directory symlink")
        before = protected_bytes(other_root)
        with self.assertRaises(PlayProfileRefusedError) as caught:
            self.select(PROFILE_MODE_RESUME)
        self.assertEqual(caught.exception.reason, REASON_DIRECTORY_REFUSED)
        self.assertEqual(protected_bytes(other_root), before)

    def test_a_damaged_archive_inside_a_valid_profile_is_refused_untouched(self) -> None:
        profile = self.select(PROFILE_MODE_INIT)
        archive = profile.directory / STORE_FILENAME
        archive.write_bytes(b"\x00" * 64)
        before = protected_bytes(self.profile_root)
        with self.assertRaises(RoleStoreUnusableError):
            self.select(PROFILE_MODE_RESUME).open_store()
        self.assertEqual(protected_bytes(self.profile_root), before)
        # …and the launcher reports it instead of starting a socket.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                run(
                    build_parser().parse_args(
                        [
                            "--role-mode", ROLE_MODE_PLAY_PROFILE,
                            "--play-profile", PROFILE_ID,
                            "--profile-root", str(self.profile_root),
                        ]
                    )
                )
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("refusing to start", stderr.getvalue())
        self.assertEqual(protected_bytes(self.profile_root), before)

    def test_an_unknown_profile_mode_is_refused(self) -> None:
        with self.assertRaises(PlayProfileRefusedError) as caught:
            self.select("first_time_guess")
        self.assertEqual(caught.exception.reason, REASON_MODE_INVALID)
        self.assertFalse(self.profile_root.exists())

    def test_the_default_profile_root_is_a_sibling_of_the_default_archive(self) -> None:
        windows = {
            "LOCALAPPDATA": r"C:\Users\someone\AppData\Local",
            "REVIVAL_DATA_DIR": "",
            "REVIVAL_PROFILE_ROOT": "",
        }
        resolved = default_profile_root(environ=windows)
        self.assertEqual(
            resolved,
            Path(r"C:\Users\someone\AppData\Local") / "chang_an_revival_profiles",
        )
        self.assertNotEqual(resolved, app_module.default_data_dir(environ=windows))
        posix = {
            "XDG_DATA_HOME": "/home/someone/.local/share",
            "REVIVAL_DATA_DIR": "",
            "REVIVAL_PROFILE_ROOT": "",
        }
        self.assertEqual(
            default_profile_root(environ=posix),
            Path("/home/someone/.local/share") / "chang_an_revival_profiles",
        )
        override = dict(posix)
        override["REVIVAL_PROFILE_ROOT"] = "/mnt/elsewhere/profiles"
        self.assertEqual(
            default_profile_root(environ=override), Path("/mnt/elsewhere/profiles")
        )

    # ------------------------------------------------------------------- CLI

    def test_the_cli_refuses_the_profile_flags_on_a_path_based_mode(self) -> None:
        for argv in (
            ["--play-profile", PROFILE_ID],
            ["--profile-root", str(self.profile_root)],
            ["--profile-mode", PROFILE_MODE_INIT],
            ["--role-mode", ROLE_MODE_FRESH_TEST, "--play-profile", PROFILE_ID],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit):
                    build_role_store(build_parser().parse_args(argv))

    def test_the_cli_refuses_a_data_dir_next_to_a_named_profile(self) -> None:
        args = build_parser().parse_args(
            [
                "--role-mode", ROLE_MODE_PLAY_PROFILE,
                "--play-profile", PROFILE_ID,
                "--profile-root", str(self.profile_root),
                "--data-dir", str(self.root / "somewhere"),
            ]
        )
        with self.assertRaises(SystemExit):
            build_role_store(args)
        self.assertFalse(self.profile_root.exists())

    def test_the_cli_requires_a_profile_name_for_the_play_mode(self) -> None:
        args = build_parser().parse_args(
            [
                "--role-mode", ROLE_MODE_PLAY_PROFILE,
                "--profile-root", str(self.profile_root),
            ]
        )
        with self.assertRaises(SystemExit):
            build_role_store(args)
        self.assertFalse(self.profile_root.exists())

    def test_the_cli_default_mode_is_unchanged(self) -> None:
        """The path-based modes keep their meanings; none of them is a profile."""

        args = build_parser().parse_args([])
        self.assertEqual(args.role_mode, ROLE_MODE_RESUME)
        self.assertIsNone(args.play_profile)
        self.assertEqual(args.profile_mode, PROFILE_MODE_RESUME)
        self.assertIsNone(args.profile_root)


class PlayProfileLeaseTests(_ProfileTestCase):
    """One profile is served by one instance, and the lease outlives nothing."""

    def test_a_second_store_on_the_same_profile_is_refused(self) -> None:
        profile = self.select(PROFILE_MODE_INIT)
        first = profile.open_store(time_source=lambda: FIXED_NOW)
        try:
            self.assertEqual(first.kind, KIND_PLAY_PROFILE)
            lease = profile.directory / PROFILE_LOCK_FILENAME
            record = read_lock_record(lease)
            self.assertIsNotNone(record)
            self.assertEqual(record["pid"], os.getpid())
            self.assertEqual(record["started_at"], FIXED_NOW)
            # The locked byte is the lease itself, so a reader (including the
            # refusing instance's own diagnostic) reads the record past it.
            self.assertEqual(
                lease.stat().st_size,
                PROFILE_LOCK_RECORD_OFFSET + PROFILE_LOCK_RECORD_BYTES,
            )
            with self.assertRaises(PlayProfileRefusedError) as caught:
                self.select(PROFILE_MODE_RESUME).open_store()
            self.assertEqual(caught.exception.reason, REASON_INSTANCE_CONFLICT)
            self.assertIn(f"pid={os.getpid()}", str(caught.exception))
            self.assertIn(record["instance_id"], str(caught.exception))
            # A refused instance writes nothing: the holder's lease is intact.
            self.assertEqual(read_lock_record(lease), record)
        finally:
            first.close()

    def test_the_lease_is_released_when_the_store_closes(self) -> None:
        profile = self.select(PROFILE_MODE_INIT)
        first = profile.open_store()
        first.close()
        second = self.select(PROFILE_MODE_RESUME).open_store(
            time_source=lambda: FIXED_NOW
        )
        try:
            self.assertEqual(second.kind, KIND_PLAY_PROFILE)
            self.assertEqual(second.profile_fields()["play_profile_id"], PROFILE_ID)
        finally:
            second.close()

    def test_the_path_addressed_archive_keeps_no_lease(self) -> None:
        """Only a *named* profile is exclusive; `--data-dir` stays as it was."""

        directory = self.root / "roles"
        first = RoleStore.at_directory(directory)
        try:
            second = RoleStore.at_directory(directory)
            try:
                self.assertEqual(first.kind, KIND_PERSISTENT)
                self.assertEqual(second.kind, KIND_PERSISTENT)
                self.assertEqual(second.profile_fields(), {})
            finally:
                second.close()
        finally:
            first.close()

    def test_two_processes_cannot_serve_one_profile_and_death_frees_it(self) -> None:
        """The lease is an OS lock, so a killed server leaves no stale lock."""

        stub_dir = Path(app_module.__file__).resolve().parents[1]
        argv = [
            sys.executable, "-m", "revival_stub.play", PROFILE_ID,
            "--profile-root", str(self.profile_root),
        ]
        first = subprocess.Popen(
            [*argv, "--profile-mode", PROFILE_MODE_INIT],
            cwd=str(stub_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        third = None
        try:
            self.assertTrue(
                _wait_for_stub_started(first), "the first play entry never started"
            )
            second = subprocess.run(
                argv, cwd=str(stub_dir), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            self.assertEqual(second.returncode, 2)
            self.assertIn("refusing to start", second.stderr)
            self.assertIn("already served by another process", second.stderr)
            # The killed process's lease is released by the operating system,
            # so a crash never leaves a profile nobody can unlock.
            first.kill()
            first.wait(timeout=30)
            third = subprocess.Popen(
                argv, cwd=str(stub_dir), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace",
            )
            self.assertTrue(
                _wait_for_stub_started(third),
                "a profile must be servable again after its holder died",
            )
        finally:
            for proc in (first, third):
                if proc is None:
                    continue
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=30)
                if proc.stdout is not None:
                    proc.stdout.close()


def _wait_for_stub_started(proc: subprocess.Popen, *, timeout: float = 90.0) -> bool:
    """Read the child's event stream until it says it is serving."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            return False
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("event") == "stub_started":
            return True
    return False


class PlayProfileArchiveGuardTests(_ProfileTestCase):
    """TASK-0072: the archive a profile serves is a *file*, verified or refused.

    An initialised profile is a save that exists: `resume` opens exactly that
    file, and an archive that is missing, half-built, aliased or substituted is
    refused *before* SQLite connects, before the instance lease, and before
    anything is written.  Every case runs against real temporary files inside
    the test root; the real per-user archive is never used as material (its
    resolved path is redirected and only ever compared against).
    """

    # ------------------------------------------------------------- fixtures

    @staticmethod
    def _account_key(store: RoleStore, sdk_identity: str = "local-user") -> str:
        return store.account_key(
            server_id=1.0,
            platform="android",
            channel_id="local-channel",
            channel_sub_id="0",
            sdk_identity=sdk_identity,
        )

    def _profile_with_archive(
        self, profile_id: str = PROFILE_ID, *, with_role: bool = True
    ) -> Path:
        """A real named profile, initialised and opened once: a real archive."""

        store = self.select(PROFILE_MODE_INIT, profile_id=profile_id).open_store(
            time_source=lambda: FIXED_NOW
        )
        try:
            if with_role:
                store.create_or_get(
                    account_key=self._account_key(store),
                    server_id=1.0,
                    role_name=CLIENT_ROLE_NAME,
                    career=1,
                    create_time=FIXED_NOW,
                    compatibility_profile_id=COMPATIBILITY_PROFILE_ID,
                )
        finally:
            store.close()
        return self.profile_dir(profile_id)

    def _default_archive_database(self) -> Path:
        """The *simulated* default per-user archive, holding one role."""

        store = RoleStore.at_directory(
            self.default_archive, time_source=lambda: FIXED_NOW
        )
        try:
            store.create_or_get(
                account_key=self._account_key(store),
                server_id=1.0,
                role_name=CLIENT_ROLE_NAME,
                career=1,
                create_time=FIXED_NOW,
                compatibility_profile_id=COMPATIBILITY_PROFILE_ID,
            )
        finally:
            store.close()
        return self.default_archive / STORE_FILENAME

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int]:
        listing = os.stat(path)
        return (listing.st_dev, listing.st_ino)

    def _replace_with_alias(self, archive: Path, target: Path, *, how: str) -> None:
        archive.unlink()
        try:
            if how == "symlink":
                archive.symlink_to(target)
            else:
                os.link(target, archive)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover - host policy
            self.skipTest(f"this host cannot create a file {how}: {exc}")

    def _refusals(self, reason: str, *, profile_id: str = PROFILE_ID) -> None:
        """Both entry points refuse: selecting the profile, and opening it."""

        for step, call in (
            ("select", lambda: self.select(PROFILE_MODE_RESUME, profile_id=profile_id)),
            (
                "open",
                lambda: self.select(
                    PROFILE_MODE_RESUME, profile_id=profile_id
                ).open_store(),
            ),
        ):
            with self.subTest(step=step):
                with self.assertRaises(PlayProfileRefusedError) as caught:
                    call()
                self.assertEqual(caught.exception.reason, reason)

    # ------------------------------------------------------------------ (a)

    def test_a_deleted_archive_is_refused_and_never_recreated(self) -> None:
        """Only the main database goes away; the profile is not rebuilt."""

        directory = self._profile_with_archive()
        archive = directory / STORE_FILENAME
        manifest = directory / PROFILE_MANIFEST_FILENAME
        manifest_before = manifest.read_bytes()
        archive.unlink()
        lease = directory / PROFILE_LOCK_FILENAME
        lease_before = lease.read_bytes()
        before = tree_bytes(self.root)
        self.assertEqual(manifest.read_bytes(), manifest_before)

        self._refusals(REASON_ARCHIVE_MISSING)

        self.assertFalse(archive.exists(), "an empty database is never created")
        self.assertEqual(
            lease.read_bytes(),
            lease_before,
            "the refusal happens before the profile is leased",
        )
        self.assertEqual(tree_bytes(self.root), before)

        # The product entry refuses before a socket opens: there is no server
        # that could answer a client, so nothing can be answered `noRole`.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit_code:
                run(
                    build_parser().parse_args(
                        [
                            "--role-mode", ROLE_MODE_PLAY_PROFILE,
                            "--play-profile", PROFILE_ID,
                            "--profile-root", str(self.profile_root),
                        ]
                    )
                )
        self.assertEqual(exit_code.exception.code, 2)
        self.assertIn("refusing to start", stderr.getvalue())
        self.assertIn(STORE_FILENAME, stderr.getvalue())
        self.assertEqual(manifest.read_bytes(), manifest_before)
        self.assertEqual(tree_bytes(self.root), before)
        self.assertFalse(archive.exists())

    # ------------------------------------------------------------------ (c)

    def test_a_half_built_profile_is_refused_not_repaired(self) -> None:
        """`init` wrote the manifest, and no archive ever followed it."""

        self.select(PROFILE_MODE_INIT)
        directory = self.profile_dir()
        self.assertEqual(
            sorted(path.name for path in directory.iterdir()),
            [PROFILE_MANIFEST_FILENAME],
        )
        before = tree_bytes(self.root)

        self._refusals(REASON_ARCHIVE_MISSING)

        self.assertEqual(tree_bytes(self.root), before)
        self.assertEqual(
            sorted(path.name for path in directory.iterdir()),
            [PROFILE_MANIFEST_FILENAME],
            "a half-built profile is never completed, migrated or removed",
        )

    # ------------------------------------------------------------------ (d)

    def test_an_archive_that_is_a_symlink_is_refused(self) -> None:
        other = self._profile_with_archive("other-save")
        default_db = self._default_archive_database()
        for profile_id, alias_of, target in (
            (PROFILE_ID, "another profile's save", other / STORE_FILENAME),
            ("dev-alt", "the default per-user archive", default_db),
        ):
            with self.subTest(profile=profile_id, alias_of=alias_of):
                directory = self._profile_with_archive(profile_id)
                archive = directory / STORE_FILENAME
                self._replace_with_alias(archive, target, how="symlink")
                self.assertTrue(archive.is_symlink())
                self.assertEqual(
                    self._file_identity(archive),
                    self._file_identity(target),
                    "the alias really is the other file",
                )
                before = tree_bytes(self.root)

                self._refusals(REASON_ARCHIVE_REFUSED, profile_id=profile_id)

                self.assertEqual(tree_bytes(self.root), before)
                self.assertTrue(
                    archive.is_symlink(), "the alias is left exactly as it was"
                )

    # ------------------------------------------------------------------ (e)

    def test_an_archive_that_is_a_hard_link_is_refused(self) -> None:
        """`Path.resolve` cannot see this one: the two names are one file."""

        other = self._profile_with_archive("other-save")
        default_db = self._default_archive_database()
        for profile_id, alias_of, target in (
            (PROFILE_ID, "another profile's save", other / STORE_FILENAME),
            ("dev-alt", "the default per-user archive", default_db),
        ):
            with self.subTest(profile=profile_id, alias_of=alias_of):
                directory = self._profile_with_archive(profile_id)
                archive = directory / STORE_FILENAME
                self._replace_with_alias(archive, target, how="hard link")
                self.assertTrue(os.path.samefile(archive, target))
                self.assertEqual(
                    self._file_identity(archive), self._file_identity(target)
                )
                self.assertGreaterEqual(os.stat(archive).st_nlink, 2)
                before = tree_bytes(self.root)

                self._refusals(REASON_ARCHIVE_REFUSED, profile_id=profile_id)

                self.assertEqual(tree_bytes(self.root), before)

    def test_the_default_archives_own_file_is_never_a_profiles_archive(self) -> None:
        """The same-file rule itself, exercised on the guard.

        The launcher cannot reach this state -- a profile directory may not be
        the default archive directory -- so the rule is proven where it lives:
        the archive *is* the protected file, by identity, and the guard refuses
        it.  The file is only ever `stat`-ed: nothing is opened or written.
        """

        directory = self._profile_with_archive()
        guard = ProfileArchiveGuard(
            directory=directory, default_archive_dir=directory, require_existing=True
        )
        before = tree_bytes(self.root)
        with self.assertRaises(PlayProfileRefusedError) as caught:
            guard.verify(directory / STORE_FILENAME)
        self.assertEqual(caught.exception.reason, REASON_ARCHIVE_REFUSED)
        self.assertEqual(tree_bytes(self.root), before)

    # ----------------------------------------------------------- the race

    def test_an_archive_substituted_before_the_open_is_refused(self) -> None:
        """A file exchanged between the guard and SQLite is refused, not served.

        `sqlite3.connect` is wrapped so that the substitution happens in exactly
        the window the guard closes: the file is verified, and replaced before
        SQLite opens it.
        """

        directory = self._profile_with_archive()
        archive = directory / STORE_FILENAME
        donor = self._profile_with_archive("other-save") / STORE_FILENAME
        replacement = self.root / "substituted.sqlite3"
        shutil.copyfile(donor, replacement)
        substituted = replacement.read_bytes()

        real_connect = role_store_module.sqlite3.connect

        def connect_after_substitution(*args, **kwargs):
            os.replace(replacement, archive)
            return real_connect(*args, **kwargs)

        with mock.patch.object(
            role_store_module.sqlite3, "connect", connect_after_substitution
        ):
            with self.assertRaises(PlayProfileRefusedError) as caught:
                self.select(PROFILE_MODE_RESUME).open_store()
        self.assertEqual(caught.exception.reason, REASON_ARCHIVE_REFUSED)
        # The substituted file was neither configured nor written: a refused
        # open may not have touched it.
        self.assertEqual(archive.read_bytes(), substituted)
        # …and the refusal released the lease, so the profile is not bricked.
        released = acquire_instance_lock(directory)
        released.release()

    # ------------------------------------------------------------------ (f)

    def test_a_legitimate_resume_opens_the_very_file_it_verified(self) -> None:
        directory = self._profile_with_archive()
        archive = directory / STORE_FILENAME
        identity = self._file_identity(archive)
        for _ in range(2):
            store = self.select(PROFILE_MODE_RESUME).open_store(
                time_source=lambda: FIXED_NOW
            )
            try:
                self.assertEqual(store.kind, KIND_PLAY_PROFILE)
                self.assertEqual(archive.read_bytes()[:16], b"SQLite format 3\x00")
                self.assertEqual(
                    store.count(), 1, "the durable role is still the one role"
                )
                self.assertEqual(
                    self._file_identity(archive),
                    identity,
                    "a resume opens the existing archive, never a new one",
                )
            finally:
                store.close()
        # The lease is unaffected by the guard: one profile, one instance.
        held = self.select(PROFILE_MODE_RESUME).open_store()
        try:
            with self.assertRaises(PlayProfileRefusedError) as caught:
                self.select(PROFILE_MODE_RESUME).open_store()
            self.assertEqual(caught.exception.reason, REASON_INSTANCE_CONFLICT)
            self.assertEqual(self._file_identity(archive), identity)
        finally:
            held.close()

    # ------------------------------------------------------------------ (g)

    def test_the_guard_is_scoped_to_named_profiles(self) -> None:
        """The path-addressed and throwaway archives keep their behaviour."""

        # A path-addressed archive is still served as it always was, including
        # one whose database is a second name of another file: that mode's
        # ownership rules were not changed by this task.
        owned = self.root / "path-addressed"
        first = RoleStore.at_directory(owned)
        first.close()
        alias_dir = self.root / "elsewhere"
        alias_dir.mkdir(parents=True)
        try:
            os.link(owned / STORE_FILENAME, alias_dir / STORE_FILENAME)
        except OSError as exc:  # pragma: no cover - host policy
            self.skipTest(f"this host cannot create a hard link: {exc}")
        second = RoleStore.at_directory(alias_dir)
        try:
            self.assertEqual(second.kind, KIND_PERSISTENT)
            self.assertEqual(second.profile_fields(), {})
        finally:
            second.close()
        # …and a `fresh_role_test` archive is still its own throwaway.
        fresh = RoleStore.fresh_test()
        try:
            self.assertEqual(fresh.kind, "ephemeral_fresh_role_test")
            self.assertEqual(fresh.profile_fields(), {})
        finally:
            fresh.close()

    def test_a_play_profile_store_cannot_be_opened_without_its_guard(self) -> None:
        loose = self.root / "loose"
        with self.assertRaises(ValueError):
            RoleStore(loose / STORE_FILENAME, kind=KIND_PLAY_PROFILE)
        self.assertFalse(loose.exists())


class PlayProfilePlayEntryTests(_ProfileTestCase):
    """The whole product edge, over the real entry: HTTP, TCP, restart, resume."""

    def setUp(self) -> None:
        super().setUp()
        self.events: list[dict] = []
        self._running: list[tuple[app_module.StubServers, list[threading.Thread]]] = []
        self._args = build_parser().parse_args(
            [
                "--role-mode", ROLE_MODE_PLAY_PROFILE,
                "--play-profile", PROFILE_ID,
                "--profile-root", str(self.profile_root),
                "--bind-host", "127.0.0.1",
                "--advertise-host", "127.0.0.1",
                "--http-port", "0",
                "--tcp-port", "0",
            ]
        )

    def tearDown(self) -> None:
        self._stop_all()
        super().tearDown()

    # ------------------------------------------------------------- lifecycle

    def _boot(
        self, *, mode: str = PROFILE_MODE_RESUME
    ) -> tuple[RoleStore, app_module.StubServers]:
        """Open the named profile through the product CLI, then serve it."""

        store = build_role_store(
            build_parser().parse_args(
                [
                    "--role-mode", ROLE_MODE_PLAY_PROFILE,
                    "--play-profile", PROFILE_ID,
                    "--profile-mode", mode,
                    "--profile-root", str(self.profile_root),
                ]
            )
        )
        logger = EventLogger(stream=None, sink=self.events.append)
        servers = app_module.build_servers(self._args, logger, store)
        threads = [
            threading.Thread(target=servers.tcp.serve_forever, daemon=True),
            threading.Thread(target=servers.http.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        self._running.append((servers, threads))
        return store, servers

    def _stop_all(self) -> None:
        for servers, threads in self._running:
            servers.tcp.shutdown()
            servers.http.shutdown()
            servers.close()
            for thread in threads:
                thread.join(timeout=2)
        self._running.clear()

    # ---------------------------------------------------------------- flows

    def _drive_immediate_block(self, session: _Session) -> None:
        session.send(217, (12345.0, b"apk-md5"))
        session.send(4, (False,))
        session.send(22, ())
        session.send(37, ())
        session.send(39, ())
        session.send(579, (True,))

    def _create_role(self, session: _Session) -> list:
        session.send(0, login_fields())
        session.read(1)
        session.send(2, (CLIENT_ROLE_NAME.encode("utf-8"), 1))
        session.read(NEW_AFTER_CREATE)
        session.send(3, (1, False))
        session.read(NEW_AFTER_TERMINAL)
        self._drive_immediate_block(session)
        session.read(NEW_COMPLETE)
        return session.responses

    def _resume_role(self, session: _Session) -> list:
        session.send(0, login_fields())
        session.read(RESUMED_AFTER_LOGIN)
        session.send(3, (1, False))
        session.read(RESUMED_AFTER_TERMINAL)
        self._drive_immediate_block(session)
        session.read(RESUMED_COMPLETE)
        return session.responses

    # ----------------------------------------------------------- the product

    def test_the_play_entry_serves_the_recovered_http_contract(self) -> None:
        store, servers = self._boot(mode=PROFILE_MODE_INIT)
        try:
            authority = f"127.0.0.1:{servers.http_port}"
            with urllib.request.urlopen(
                f"http://{authority}/enter.txt", timeout=5
            ) as response:
                self.assertEqual(response.status, 200)
                body = response.read().decode("utf-8")
            self.assertIn(f"{{{authority}/patch}}", body)
            self.assertIn(f"{{{authority}/noop/account-verify}}", body)
            self.assertIn("{P10441}", body)
        finally:
            store.close()

    def test_a_named_profile_survives_two_real_server_restarts(self) -> None:
        store, servers = self._boot(mode=PROFILE_MODE_INIT)
        created = _Session(servers.tcp_port)
        try:
            responses = self._create_role(created)
            self.assertEqual(created.ids(), NEW_ROLE_CHAIN)
            self.assertEqual(
                responses[NEW_SNAPSHOT_INDEX].spec.name, "returnRoleInfo"
            )
            snapshot = responses[NEW_SNAPSHOT_INDEX].fields
            role_identity = (snapshot[1], snapshot[2], snapshot[3], snapshot[14])
            # The stored name is the one the client itself sent in C2S 2.
            self.assertEqual(snapshot[2], CLIENT_ROLE_NAME.encode("utf-8"))
            self.assertNotEqual(snapshot[2], b"AutoTest01")
            self.assertEqual(created.quiet_state(), "quiet")
            self.assertEqual(store.kind, KIND_PLAY_PROFILE)
            self.assertEqual(store.count(), 1)
        finally:
            created.close()
            store.close()

        for epoch in (1, 2):
            with self.subTest(restart=epoch):
                self.events.clear()
                store, servers = self._boot()
                try:
                    # The archive is a durable WAL database with FULL sync, so a
                    # committed create survives the process that made it.
                    journal = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
                    self.assertEqual(str(journal).lower(), "wal")
                    self.assertEqual(
                        int(store._conn.execute("PRAGMA synchronous").fetchone()[0]), 2
                    )
                    resumed = _Session(servers.tcp_port)
                    try:
                        responses = self._resume_role(resumed)
                        self.assertEqual(resumed.ids(), RESUMED_ROLE_CHAIN)
                        self.assertNotIn(5, resumed.ids())
                        self.assertNotIn(14, resumed.ids())
                        snapshot = responses[RESUMED_SNAPSHOT_INDEX].fields
                        self.assertEqual(
                            (snapshot[1], snapshot[2], snapshot[3], snapshot[14]),
                            role_identity,
                        )
                        self.assertEqual(
                            responses[RESUMED_SNAPSHOT_INDEX].spec.name,
                            "returnRoleInfo",
                        )
                        # The terminal segment really is the index-40 one.
                        # TASK-0242: the v21 mail pair shifts it by two.
                        self.assertEqual(responses[5].fields, (40, False))
                        self.assertEqual(responses[10].fields, (40, False))
                        # …and the client's own heartbeat still rides the wire
                        # on the resumed session, with no answer.
                        resumed.send(23, (FIXED_NOW + 3, 12.5, True))
                        self.assertEqual(resumed.quiet_state(), "quiet")
                        resumed.send(25, ())
                        resumed.send(26, ())
                        self.assertEqual(resumed.quiet_state(), "quiet")
                    finally:
                        resumed.close()
                    self.assertEqual(store.count(), 1)
                    resumed_events = [
                        event
                        for event in self.events
                        if event.get("event") == "tcp_role_resumed"
                    ]
                    self.assertEqual(len(resumed_events), 1)
                    self.assertEqual(resumed_events[0]["play_profile_id"], PROFILE_ID)
                    self.assertEqual(
                        resumed_events[0]["role_origin"], "resumed_existing_binding"
                    )
                    # No create path was touched anywhere in the resumed epoch:
                    # no noRole, no S2C 14, no C2S 2 and no new role row.
                    self.assertEqual(
                        [
                            event
                            for event in self.events
                            if event.get("protocol_name")
                            in {"noRole", "returnCreateRoleResult"}
                        ],
                        [],
                    )
                    self.assertEqual(
                        [
                            event
                            for event in self.events
                            if event.get("event")
                            in {"tcp_role_created", "tcp_create_role_replay_absorbed"}
                        ],
                        [],
                    )
                    self.assertEqual(
                        [
                            event
                            for event in self.events
                            if event.get("event") == "tcp_packet_received"
                            and event.get("protocol_name") == "createRole"
                        ],
                        [],
                    )
                    self.assertFalse(
                        [
                            event
                            for event in self.events
                            if event.get("event") == "tcp_error"
                        ]
                    )
                finally:
                    store.close()

    def test_an_archive_with_no_role_yet_still_onboards_after_a_restart(self) -> None:
        """(TASK-0072) An empty `roles` table is a real archive, not damage."""

        store, _ = self._boot(mode=PROFILE_MODE_INIT)
        archive = self.profile_dir() / STORE_FILENAME
        try:
            self.assertEqual(store.count(), 0)
            self.assertTrue(archive.is_file())
            identity = (os.stat(archive).st_dev, os.stat(archive).st_ino)
        finally:
            store.close()
            self._stop_all()

        # The profile was initialised and never played: its database exists and
        # is served as it is, so the one-time onboarding still happens -- once.
        store, servers = self._boot()
        try:
            self.assertEqual(
                (os.stat(archive).st_dev, os.stat(archive).st_ino), identity
            )
            self.assertEqual(store.count(), 0)
            created = _Session(servers.tcp_port)
            try:
                responses = self._create_role(created)
                self.assertEqual(created.ids(), NEW_ROLE_CHAIN)
                self.assertEqual(
                    responses[NEW_SNAPSHOT_INDEX].fields[2],
                    CLIENT_ROLE_NAME.encode("utf-8"),
                )
            finally:
                created.close()
            self.assertEqual(store.count(), 1)
        finally:
            store.close()

    def test_a_bound_profile_refuses_another_account_without_answering(self) -> None:
        store, servers = self._boot(mode=PROFILE_MODE_INIT)
        created = _Session(servers.tcp_port)
        try:
            self._create_role(created)
        finally:
            created.close()
            store.close()

        store, servers = self._boot()
        held = store.lookup(
            store.account_key(
                server_id=1.0, platform="android", channel_id="local-channel",
                channel_sub_id="0", sdk_identity="local-user",
            )
        )
        self.assertIsNotNone(held)
        self.events.clear()
        try:
            intruder = _Session(servers.tcp_port)
            try:
                intruder.send(0, login_fields(sdk_user_id=b"someone-else"))
                # Nothing is answered at all -- not even `noRole`, which is the
                # client's create-role switch.
                self.assertEqual(intruder.quiet_state(), "closed")
                self.assertEqual(intruder.responses, [])
            finally:
                intruder.close()
            refusals = [
                event
                for event in self.events
                if event.get("event") == "tcp_error"
                and event.get("classification") == "profile_account_mismatch"
            ]
            self.assertEqual(len(refusals), 1)
            self.assertEqual(refusals[0]["stage"], "login")
            self.assertEqual(refusals[0]["play_profile_id"], PROFILE_ID)
            self.assertFalse(refusals[0]["no_role_sent"])
            self.assertFalse(refusals[0]["second_role_created"])
            self.assertEqual(
                [e for e in self.events if e.get("event") == "tcp_packet_sent"], []
            )
            # The legitimate player's role is exactly as it was.
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.lookup(held.account_key), held)
        finally:
            store.close()

    def test_the_profile_identity_is_auditable_from_the_manifest_and_the_log(self) -> None:
        store, servers = self._boot(mode=PROFILE_MODE_INIT)
        try:
            manifest = json.loads(
                (self.profile_dir() / PROFILE_MANIFEST_FILENAME).read_text("utf-8")
            )
            self.assertEqual(manifest["format"], "revival-play-profile")
            self.assertEqual(manifest["format_version"], PROFILE_FORMAT_VERSION)
            self.assertEqual(manifest["profile_id"], PROFILE_ID)
            self.assertEqual(manifest["created_by"], PROFILE_ORIGIN_LOCAL_INIT)
            self.assertIsInstance(manifest["created_at"], int)
            self.assertEqual(len(manifest["profile_uid"]), 32)
            described = store.describe()
            self.assertEqual(described["play_profile_id"], PROFILE_ID)
            self.assertEqual(described["play_profile_uid"], manifest["profile_uid"])
            self.assertEqual(described["play_profile_mode"], PROFILE_MODE_INIT)
            self.assertTrue(described["play_profile_initialised_now"])
            self.assertFalse(described["play_profile_is_default_archive"])
            self.assertFalse(described["play_profile_inside_repository"])
            self.assertEqual(described["role_store_kind"], KIND_PLAY_PROFILE)
            self.assertEqual(
                Path(described["play_profile_directory"]), self.profile_dir()
            )
            # The profile lives in its own directory, outside every other
            # archive, and never inside the repository or the default archive.
            self.assertNotIn(self.default_archive, self.profile_dir().parents)
            self.assertNotEqual(self.profile_dir(), app_module.default_data_dir())
        finally:
            store.close()

    def test_the_launcher_entry_selects_exactly_the_cli_entry(self) -> None:
        # Create the profile once -- including the archive it serves, which a
        # `resume` requires -- then reach it through both entries.
        self.select(PROFILE_MODE_INIT).open_store().close()
        launcher = play_module.build_parser().parse_args(
            [PROFILE_ID, "--profile-root", str(self.profile_root)]
        )
        namespace = play_module.to_app_namespace(launcher)
        self.assertEqual(namespace.role_mode, ROLE_MODE_PLAY_PROFILE)
        self.assertEqual(namespace.play_profile, PROFILE_ID)
        self.assertEqual(namespace.profile_mode, PROFILE_MODE_RESUME)
        self.assertIsNone(namespace.data_dir)
        store = build_role_store(namespace)
        try:
            self.assertEqual(store.kind, KIND_PLAY_PROFILE)
            self.assertEqual(Path(store._location), self.profile_dir() / STORE_FILENAME)
            self.assertEqual(
                store.profile_fields()["play_profile_uid"],
                json.loads(
                    (self.profile_dir() / PROFILE_MANIFEST_FILENAME).read_text("utf-8")
                )["profile_uid"],
            )
        finally:
            store.close()
        via_cli = build_role_store(
            build_parser().parse_args(
                [
                    "--role-mode", ROLE_MODE_PLAY_PROFILE,
                    "--play-profile", PROFILE_ID,
                    "--profile-root", str(self.profile_root),
                ]
            )
        )
        try:
            self.assertEqual(
                Path(via_cli._location), self.profile_dir() / STORE_FILENAME
            )
        finally:
            via_cli.close()

    def test_the_play_entry_refuses_a_port_another_process_owns(self) -> None:
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        # A backlog deeper than the two probes this test makes: the holder never
        # accepts, so a full queue would answer the second probe differently.
        holder.listen(64)
        occupied = holder.getsockname()[1]
        self.addCleanup(holder.close)
        args = build_parser().parse_args(
            [
                "--role-mode", ROLE_MODE_PLAY_PROFILE,
                "--play-profile", PROFILE_ID,
                "--profile-mode", PROFILE_MODE_INIT,
                "--profile-root", str(self.profile_root),
                "--tcp-port", str(occupied),
            ]
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                run(args)
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("already served by another process", stderr.getvalue())
        # The refusal happens before any profile state exists.
        self.assertFalse(self.profile_root.exists())
        self.assertTrue(app_module.port_is_owned("127.0.0.1", occupied))
        self.assertFalse(app_module.port_is_owned("127.0.0.1", _free_port()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
