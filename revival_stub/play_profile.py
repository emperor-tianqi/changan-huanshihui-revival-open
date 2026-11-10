"""Explicitly named, durable play profiles for a normal game session.

``--data-dir`` is a path, and a path is not a product: nothing in it says which
saved character it holds, whether it is the one the player played yesterday, or
whether it is a throwaway test archive.  A *play profile* is that missing
identity.  An operator names one (``dev-main``), the name resolves to exactly
one fixed directory, and the same name keeps resolving to the same directory --
and therefore to the same salt and the same role row -- across Git commits,
Autopilot tasks, process restarts and reboots.

What this module owns, and what it deliberately does not:

* It owns the **name**: validation, the profile root, the name -> directory
  mapping, the profile manifest, and the refusals that keep a profile out of
  the repository, out of the default per-user archive, and out of the throwaway
  archives the automated runners create per RUN.
* It owns the **instance lease**: one exclusive OS-level lock per profile, so a
  second server can never serve the same save.  The lease is released by the
  operating system if the process dies, so a crash does not brick a profile.
* It owns the **archive guard**: the one file a profile may be served from is
  its own ``revival_roles.sqlite3``, and the guard proves the *file* -- it is
  there, it is a regular file, it is not a link or any other reparse alias, it
  has one name on disk, it is not the default archive's own file, and it is
  still that file once SQLite has it open.  A ``resume`` may never create,
  repair or replace an archive; only an explicit ``init`` creates the first one.
* It does **not** own the role.  The account -> role binding, the role-ID
  serial and the archive structure stay in :mod:`revival_stub.role_store`; a
  profile is opened *through* this module and then served by the same
  :class:`~revival_stub.role_store.RoleStore` every other mode uses.

Failure policy: every unusable condition -- an invalid name, a root inside the
repository or the real archive, a directory that a symlink moved out of the
root, a missing or damaged manifest, a newer structure version, an escaping
path, a missing or aliased archive, a profile already served by another process
-- refuses.  Nothing is adopted, cleared, rebuilt, replaced or migrated, and no
refusable profile directory is created before the refusal.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .role_store import KIND_PLAY_PROFILE, STORE_FILENAME, RoleStore

#: Environment override for the directory that holds every profile.  Named so a
#: device or operator setup can move the whole profile collection without
#: editing command lines, and so tests can prove the resolution.
PROFILE_ROOT_ENV = "REVIVAL_PROFILE_ROOT"

#: Directory name below the platform's per-user data location.  Deliberately a
#: *sibling* of the default ``chang_an_revival`` archive directory and not a
#: child of it: the profile collection may never live inside, contain or
#: replace the archive the automated runs and the earlier tasks used.
PROFILE_ROOT_NAME = "chang_an_revival_profiles"

#: File names inside one profile directory.
PROFILE_MANIFEST_FILENAME = "profile.json"
PROFILE_LOCK_FILENAME = "profile.lock"

#: The manifest's own domain marker and structure version.  A file that does
#: not carry the marker is not a profile manifest, however valid its JSON is.
PROFILE_MANIFEST_FORMAT = "revival-play-profile"
PROFILE_FORMAT_VERSION = 1

#: Recorded provenance of a profile directory.  It says the profile was created
#: by this local simulator, and it is explicitly not an original-server record.
PROFILE_ORIGIN_LOCAL_INIT = "revival_local_profile_init"

#: The two typed modes.  Neither is inferred: `init` refuses a directory that
#: exists, `resume` refuses one that does not, so an uninitialised direct login
#: can never silently become a first-time setup (and a first-time setup can
#: never silently become the destruction of an existing save).
PROFILE_MODE_INIT = "init"
PROFILE_MODE_RESUME = "resume"
PROFILE_MODES = (PROFILE_MODE_INIT, PROFILE_MODE_RESUME)
PROFILE_MODE_DEFAULT = PROFILE_MODE_RESUME

#: A profile name is a lowercase label: it becomes a directory name, an event
#: field and the thing a person types, so it may contain nothing that a path,
#: a shell or a log could reinterpret.
PROFILE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
PROFILE_ID_MAX_LENGTH = 32

#: Windows device names are not usable as directory names.
RESERVED_PROFILE_IDS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)

#: Directory-name prefixes of the archives the automated layers create for one
#: RUN and then delete.  A profile may never *be* one of these, and may never
#: live inside one: a profile is the opposite of per-RUN state.
EPHEMERAL_ARCHIVE_PREFIXES = (
    "revival-fresh-role-test-",   # role_store.RoleStore.fresh_test
    "revival-role-relogin-",      # scripts/device_acceptance.py per-RUN archive
)

#: One locked byte at offset 0 (the lease itself) and a bounded, address-free
#: record from offset 1 so a refused instance can name who holds the profile.
#: The record is fixed-width, so holding the lease never means shrinking it.
PROFILE_LOCK_SENTINEL = b"\n"
PROFILE_LOCK_RECORD_OFFSET = 1
PROFILE_LOCK_RECORD_BYTES = 256

#: Closed refusal vocabulary.  Every refusal carries exactly one of these.
REASON_ID_INVALID = "profile_id_invalid"
REASON_MODE_INVALID = "profile_mode_invalid"
REASON_ROOT_REFUSED = "profile_root_refused"
REASON_DIRECTORY_REFUSED = "profile_directory_refused"
REASON_MISSING = "profile_missing"
REASON_NOT_A_DIRECTORY = "profile_directory_not_a_directory"
REASON_ALREADY_EXISTS = "profile_directory_exists"
REASON_DIRECTORY_UNWRITABLE = "profile_directory_unwritable"
REASON_MANIFEST_MISSING = "profile_manifest_missing"
REASON_MANIFEST_UNREADABLE = "profile_manifest_unreadable"
REASON_MANIFEST_UNRECOGNISED = "profile_manifest_unrecognised"
REASON_MANIFEST_INCOMPLETE = "profile_manifest_incomplete"
REASON_FORMAT_NEWER = "profile_format_version_newer"
REASON_FORMAT_UNMIGRATED = "profile_format_version_unmigrated"
REASON_IDENTITY_MISMATCH = "profile_identity_mismatch"
REASON_INSTANCE_CONFLICT = "profile_instance_conflict"
REASON_LOCK_UNAVAILABLE = "profile_lock_unavailable"
#: The archive *file* itself: `_MISSING` when there is nothing to serve (a
#: half-built profile, or an archive somebody removed), `_REFUSED` when what is
#: there is not provably this profile's own file.
REASON_ARCHIVE_MISSING = "profile_archive_missing"
REASON_ARCHIVE_REFUSED = "profile_archive_refused"

PLAY_PROFILE_REFUSAL_REASONS = (
    REASON_ID_INVALID,
    REASON_MODE_INVALID,
    REASON_ROOT_REFUSED,
    REASON_DIRECTORY_REFUSED,
    REASON_MISSING,
    REASON_NOT_A_DIRECTORY,
    REASON_ALREADY_EXISTS,
    REASON_DIRECTORY_UNWRITABLE,
    REASON_MANIFEST_MISSING,
    REASON_MANIFEST_UNREADABLE,
    REASON_MANIFEST_UNRECOGNISED,
    REASON_MANIFEST_INCOMPLETE,
    REASON_FORMAT_NEWER,
    REASON_FORMAT_UNMIGRATED,
    REASON_IDENTITY_MISMATCH,
    REASON_INSTANCE_CONFLICT,
    REASON_LOCK_UNAVAILABLE,
    REASON_ARCHIVE_MISSING,
    REASON_ARCHIVE_REFUSED,
)


class PlayProfileError(RuntimeError):
    """Base class for every play-profile failure."""


class PlayProfileRefusedError(PlayProfileError):
    """The named profile cannot be served safely.  Fail closed.

    ``reason`` is one of :data:`PLAY_PROFILE_REFUSAL_REASONS`, so a caller (or a
    test) never has to read the message to know which condition refused.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def default_profile_root(
    *, environ: dict[str, str] | None = None, home: Path | None = None
) -> Path:
    """Resolve the directory that holds every play profile, never in the repo.

    * ``REVIVAL_PROFILE_ROOT`` when set (explicit operator/test override);
    * ``%LOCALAPPDATA%\\chang_an_revival_profiles`` on Windows;
    * ``$XDG_DATA_HOME/chang_an_revival_profiles`` (or ``~/.local/share/...``)
      elsewhere.

    The name is the default archive directory's *sibling*, not its child, so a
    profile can never be resolved into the archive the earlier tasks and the
    automated runners used.
    """

    env = os.environ if environ is None else environ
    override = env.get(PROFILE_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / PROFILE_ROOT_NAME
    base = env.get("XDG_DATA_HOME")
    if base:
        return Path(base) / PROFILE_ROOT_NAME
    return (home or Path.home()) / ".local" / "share" / PROFILE_ROOT_NAME


def repository_root() -> Path:
    """The repository this module is running from.

    Derived from this file's own location (``<repo>/revival_stub/revival_stub/
    play_profile.py``), so a profile can never be placed inside the working
    tree where ``git clean``, a checkout or an accidental commit could reach it.
    """

    return Path(__file__).resolve().parents[2]


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def validate_profile_id(profile_id: object) -> str:
    """Return the profile name, or refuse it by name.

    The check is on the *name*, before any path is built from it, so a name
    that could traverse (``..``), nest (``a/b``), be reinterpreted by a shell or
    a filesystem (``DEV-MAIN``, ``con``) or overflow a log line is refused
    rather than sanitised into something else.
    """

    name = _text(profile_id) if profile_id is not None else ""
    if not name:
        raise PlayProfileRefusedError(
            REASON_ID_INVALID, "a play profile needs an explicit name"
        )
    if len(name) > PROFILE_ID_MAX_LENGTH:
        raise PlayProfileRefusedError(
            REASON_ID_INVALID,
            f"play profile name {name!r} is longer than "
            f"{PROFILE_ID_MAX_LENGTH} characters",
        )
    if not PROFILE_ID_PATTERN.match(name):
        raise PlayProfileRefusedError(
            REASON_ID_INVALID,
            f"play profile name {name!r} must be 3-{PROFILE_ID_MAX_LENGTH} "
            f"lowercase letters, digits or hyphens, starting with a letter and "
            f"ending with a letter or digit",
        )
    if name in RESERVED_PROFILE_IDS:
        raise PlayProfileRefusedError(
            REASON_ID_INVALID, f"play profile name {name!r} is a reserved device name"
        )
    return name


def _refuse_ephemeral(path: Path, reason: str, label: str) -> None:
    for part in path.parts:
        lowered = part.lower()
        if lowered.startswith(EPHEMERAL_ARCHIVE_PREFIXES):
            raise PlayProfileRefusedError(
                reason,
                f"{label} {path} is inside the throwaway archive tree "
                f"{part!r}; a play profile is never per-RUN state",
            )


def resolve_profile_root(
    root: str | Path, *, default_archive_dir: str | Path
) -> Path:
    """Resolve the collection directory, refusing the ones a profile may not use.

    ``default_archive_dir`` is required rather than imported: it is the *real*
    per-user archive (``app.default_data_dir()``), the one thing a profile must
    never be resolved into, and the caller is the layer that knows it.
    """

    candidate = _resolved(root)
    archive = _resolved(default_archive_dir)
    forbidden = (
        ("the repository", repository_root()),
        ("the default per-user role archive", archive),
    )
    for label, other in forbidden:
        if candidate == other or other in candidate.parents:
            raise PlayProfileRefusedError(
                REASON_ROOT_REFUSED,
                f"play profile root {candidate} is inside {label} {other}; "
                f"profiles live outside both",
            )
    _refuse_ephemeral(candidate, REASON_ROOT_REFUSED, "play profile root")
    return candidate


def resolve_profile_directory(
    root: Path, profile_id: str, *, default_archive_dir: str | Path
) -> Path:
    """Map one name to exactly one directory, and prove the mapping did not move.

    The name resolves to ``<root>/<name>`` and to nothing else.  Because the
    result is compared *after* resolution, a symlinked or junctioned
    ``<root>/<name>`` that points at the real archive, at a throwaway archive
    or anywhere else outside the root is refused rather than followed.
    """

    expected = root / profile_id
    resolved = _resolved(expected)
    if resolved != expected:
        raise PlayProfileRefusedError(
            REASON_DIRECTORY_REFUSED,
            f"play profile directory {expected} resolves to {resolved}, which "
            f"is not the profile named by {profile_id!r}; refusing to follow it",
        )
    archive = _resolved(default_archive_dir)
    if resolved == archive or archive in resolved.parents:
        raise PlayProfileRefusedError(
            REASON_DIRECTORY_REFUSED,
            f"play profile directory {resolved} is the default per-user role "
            f"archive {archive}; refusing to serve it as a play profile",
        )
    _refuse_ephemeral(resolved, REASON_DIRECTORY_REFUSED, "play profile directory")
    return resolved


# --------------------------------------------------------------------- manifest


def read_profile_manifest(directory: Path) -> dict[str, Any]:
    """Read and validate one manifest, refusing anything this build cannot serve."""

    path = directory / PROFILE_MANIFEST_FILENAME
    if not path.is_file():
        raise PlayProfileRefusedError(
            REASON_MANIFEST_MISSING,
            f"{directory} exists but holds no {PROFILE_MANIFEST_FILENAME}; it is "
            f"not a revival play profile and is never adopted as one",
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlayProfileRefusedError(
            REASON_MANIFEST_UNREADABLE, f"cannot read play profile manifest {path}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise PlayProfileRefusedError(
            REASON_MANIFEST_UNREADABLE,
            f"play profile manifest {path} is not valid JSON: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise PlayProfileRefusedError(
            REASON_MANIFEST_UNREADABLE,
            f"play profile manifest {path} is not a JSON object",
        )
    if payload.get("format") != PROFILE_MANIFEST_FORMAT:
        raise PlayProfileRefusedError(
            REASON_MANIFEST_UNRECOGNISED,
            f"{path} carries format {payload.get('format')!r}, not "
            f"{PROFILE_MANIFEST_FORMAT!r}",
        )
    version = payload.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise PlayProfileRefusedError(
            REASON_MANIFEST_INCOMPLETE,
            f"{path} has no readable format_version",
        )
    if version > PROFILE_FORMAT_VERSION:
        raise PlayProfileRefusedError(
            REASON_FORMAT_NEWER,
            f"play profile {directory} was written at structure {version} > "
            f"{PROFILE_FORMAT_VERSION}; refusing to serve it",
        )
    if version < PROFILE_FORMAT_VERSION:
        raise PlayProfileRefusedError(
            REASON_FORMAT_UNMIGRATED,
            f"play profile {directory} is at structure {version} and no "
            f"recovered migration to {PROFILE_FORMAT_VERSION} exists; refusing "
            f"to rewrite it",
        )
    # `profile_id` is checked for *shape* here and for *identity* by the caller,
    # so a manifest that names nothing is "incomplete" while one that names
    # another profile is a mismatch.
    for key in ("profile_id", "profile_uid", "created_by"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise PlayProfileRefusedError(
                REASON_MANIFEST_INCOMPLETE, f"{path} has no readable {key}"
            )
    if not isinstance(payload.get("created_at"), int) or isinstance(
        payload.get("created_at"), bool
    ):
        raise PlayProfileRefusedError(
            REASON_MANIFEST_INCOMPLETE, f"{path} has no readable created_at"
        )
    return payload


def _write_manifest(directory: Path, payload: dict[str, Any]) -> None:
    """Write the manifest atomically: a reader sees the whole file or nothing."""

    target = directory / PROFILE_MANIFEST_FILENAME
    temporary = directory / f"{PROFILE_MANIFEST_FILENAME}.tmp"
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    try:
        target.chmod(0o600)
    except OSError:  # pragma: no cover - Windows/POSIX policy dependent
        pass


# ------------------------------------------------------------------- the lease


@dataclass(slots=True)
class ProfileInstanceLock:
    """One held profile lease.  Released by :meth:`release` or by the OS."""

    path: Path
    instance_id: str
    pid: int
    started_at: int
    _handle: Any

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:  # pragma: no cover - the unlock path is exercised by close()
            _unlock_handle(handle)
        except OSError:
            pass
        finally:
            handle.close()


def _lock_handle(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":  # pragma: no cover - platform split, both halves used
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - platform split, both halves used
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":  # pragma: no cover - platform split, both halves used
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - platform split, both halves used
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_lock_record(path: str | Path) -> dict[str, Any] | None:
    """The bounded record of whoever holds the lease, or ``None``.

    Read-only and never raising: a refused instance uses this only to name the
    holder in its refusal message.
    """

    try:
        with open(path, "rb") as handle:
            handle.seek(PROFILE_LOCK_RECORD_OFFSET)
            raw = handle.read(PROFILE_LOCK_RECORD_BYTES)
    except OSError:
        return None
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def acquire_instance_lock(
    directory: Path, *, time_source: Callable[[], float] | None = None
) -> ProfileInstanceLock:
    """Take the profile's exclusive lease, or refuse with the holder named.

    The lease is an operating-system file lock, not a marker file: the kernel
    releases it when the holding process exits, so a crash leaves no stale
    lock and a profile never has to be "unlocked" by hand.
    """

    path = directory / PROFILE_LOCK_FILENAME
    try:
        handle = open(path, "a+b")
    except OSError as exc:
        raise PlayProfileRefusedError(
            REASON_LOCK_UNAVAILABLE, f"cannot open play profile lease {path}: {exc}"
        ) from exc
    try:
        _lock_handle(handle)
    except OSError as exc:
        handle.close()
        holder = read_lock_record(path) or {}
        raise PlayProfileRefusedError(
            REASON_INSTANCE_CONFLICT,
            f"play profile {directory} is already served by another process "
            f"(pid={holder.get('pid', 'unknown')}, "
            f"instance={holder.get('instance_id', 'unknown')}); one profile is "
            f"served by one instance",
        ) from exc
    now = int(time_source() if time_source else time.time())
    instance_id = secrets.token_hex(8)
    record = {
        "instance_id": instance_id,
        "pid": os.getpid(),
        "started_at": now,
    }
    encoded = json.dumps(record, sort_keys=True).encode("utf-8")
    if len(encoded) > PROFILE_LOCK_RECORD_BYTES:  # pragma: no cover - fixed fields
        encoded = encoded[:PROFILE_LOCK_RECORD_BYTES]
    raw = PROFILE_LOCK_SENTINEL + encoded.ljust(PROFILE_LOCK_RECORD_BYTES, b" ")
    handle.seek(0)
    handle.write(raw)
    handle.flush()
    os.fsync(handle.fileno())
    return ProfileInstanceLock(
        path=path,
        instance_id=instance_id,
        pid=os.getpid(),
        started_at=now,
        _handle=handle,
    )


# -------------------------------------------------------------- archive guard


@dataclass(frozen=True, slots=True)
class ProfileArchiveIdentity:
    """The physical identity of the one file a profile's archive must be.

    Recorded before anything opens the archive and re-checked once SQLite has
    it open, so "this profile's archive" is one *file* -- with one name, in this
    profile's own directory -- and not merely a path that resolved to something.
    """

    path: Path
    device: int
    inode: int
    link_count: int

    def is_same_file(self, listing: os.stat_result) -> bool:
        return (listing.st_dev, listing.st_ino) == (self.device, self.inode)


class ProfileArchiveGuard:
    """Fail-closed existence and file-identity guard for one profile's archive.

    :mod:`revival_stub.role_store` calls :meth:`verify` before it opens anything
    (before its ``mkdir``, before ``sqlite3.connect``) and :meth:`confirm` once
    SQLite has the database open but **before** the first write (the ``WAL``
    switch, the schema creation), so the file the connection will write to is
    provably the file that was verified.

    Refused with ``profile_archive_missing`` when there is nothing to serve, and
    with ``profile_archive_refused`` for every other file-level condition: a
    symlink, junction or any other reparse point; a path that resolves outside
    the profile's own directory; something that is not a regular file; a file
    with more than one name on disk (a hard link, which ``Path.resolve`` cannot
    see); the protected default per-user archive's own file; and a database that
    is no longer the verified file by the time SQLite has opened it.

    ``init`` is the one mode that may *create* the archive; it still refuses
    every alias, because the first SQLite is created in a directory this call
    itself just made.  Nothing here repairs, rebuilds or replaces an archive,
    and nothing here ever opens, reads or writes the protected default archive
    (it is only ever ``stat``-ed to compare identities).
    """

    def __init__(
        self,
        *,
        directory: Path,
        default_archive_dir: str | Path,
        require_existing: bool,
    ) -> None:
        self.directory = directory
        self.default_archive_dir = _resolved(default_archive_dir)
        self.require_existing = require_existing
        self._identity: ProfileArchiveIdentity | None = None

    @property
    def archive_path(self) -> Path:
        """The one path a profile may be served from."""

        return self.directory / STORE_FILENAME

    def verify(self, path: str | Path) -> ProfileArchiveIdentity | None:
        """Prove the archive is this profile's own regular file, or refuse.

        Returns the identity to re-check later, or ``None`` when an ``init``
        finds no archive yet -- which is the one case in which one may be
        created.  A ``resume`` never returns ``None``.
        """

        expected = self.archive_path
        candidate = Path(path)
        if candidate != expected:
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"play profile {self.directory} may only be served from its own "
                f"archive {expected}, never from {candidate}",
            )
        try:
            listing = os.lstat(expected)
        except FileNotFoundError:
            self._identity = None
            if self.require_existing:
                raise PlayProfileRefusedError(
                    REASON_ARCHIVE_MISSING,
                    f"play profile {self.directory} holds no {STORE_FILENAME}: it "
                    f"was never finished, or its archive was removed. A profile "
                    f"whose save is not there is never re-created, repaired or "
                    f"replaced here; restore the file, or start over with "
                    f"--profile-mode {PROFILE_MODE_INIT} under another name",
                ) from None
            return None
        except OSError as exc:
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"cannot inspect the archive {expected} of play profile "
                f"{self.directory}: {exc}",
            ) from exc
        self._refuse_alias(expected, listing)
        if Path(expected).resolve() != expected:
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {expected} resolves to {Path(expected).resolve()}, "
                f"which is not this profile's own file; refusing to follow it",
            )
        followed = self._follow(expected)
        if followed.st_nlink != 1:
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {expected} has {followed.st_nlink} names on disk; "
                f"a play profile's save is one file with one name, so this is an "
                f"alias of another file and is refused",
            )
        if self._is_protected_archive(expected):
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {expected} is the default per-user role archive "
                f"{self.default_archive_dir / STORE_FILENAME}; a play profile is "
                f"never served from it",
            )
        self._identity = ProfileArchiveIdentity(
            path=expected,
            device=followed.st_dev,
            inode=followed.st_ino,
            link_count=followed.st_nlink,
        )
        return self._identity

    def confirm(self, opened_path: str | Path | None) -> None:
        """Prove the database SQLite just opened is the verified file.

        Called after ``sqlite3.connect`` and before the first write.
        ``opened_path`` is the path SQLite itself reports for its ``main``
        database, so an archive swapped in between :meth:`verify` and the open
        is refused instead of served.  A connection that will not name its
        database fails closed.
        """

        identity = self._identity
        if identity is None:
            # `init`, and there was no archive yet: this call is about to create
            # the first one, which is the whole point of the mode.
            return
        try:
            listing = os.lstat(identity.path)
            current = os.stat(identity.path)
        except OSError as exc:
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {identity.path} of play profile {self.directory} "
                f"changed while it was being opened: {exc}",
            ) from exc
        self._refuse_alias(identity.path, listing)
        if not identity.is_same_file(current):
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {identity.path} of play profile {self.directory} is "
                f"no longer the file that was verified; refusing to serve or write "
                f"it",
            )
        if current.st_nlink != 1:
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {identity.path} gained a second name while it was "
                f"being opened; refusing to serve or write it",
            )
        opened = self._stat_or_none(opened_path)
        if opened is None or not identity.is_same_file(opened):
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the database SQLite opened for play profile {self.directory} is "
                f"not the verified archive {identity.path} "
                f"(opened={opened_path!r}); refusing to serve or write it",
            )

    # ------------------------------------------------------------- internals

    def _refuse_alias(self, path: Path, listing: os.stat_result) -> None:
        if stat.S_ISLNK(listing.st_mode) or getattr(listing, "st_reparse_tag", 0):
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {path} is a link or a reparse point; a play "
                f"profile's save is a plain file inside its own directory, so an "
                f"aliased archive is refused rather than followed",
            )
        if not stat.S_ISREG(listing.st_mode):
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"the archive {path} is not a regular file; refusing to serve it",
            )

    @staticmethod
    def _stat_or_none(path: str | Path | None) -> os.stat_result | None:
        if path is None or str(path) == "":
            return None
        try:
            return os.stat(path)
        except OSError:
            return None

    @staticmethod
    def _follow(path: Path) -> os.stat_result:
        try:
            return os.stat(path)
        except OSError as exc:  # pragma: no cover - lstat just succeeded
            raise PlayProfileRefusedError(
                REASON_ARCHIVE_REFUSED,
                f"cannot read the archive {path}: {exc}",
            ) from exc

    def _is_protected_archive(self, path: Path) -> bool:
        """Is this the default per-user archive's own file?

        ``samefile`` only ever stats the two paths: the real archive is never
        opened, read or written by this module.
        """

        try:
            return os.path.samefile(path, self.default_archive_dir / STORE_FILENAME)
        except OSError:
            return False


# ------------------------------------------------------------------- the profile


@dataclass(frozen=True, slots=True)
class PlayProfile:
    """One named, durable play profile: its identity, its directory, its lease."""

    profile_id: str
    profile_uid: str
    directory: Path
    root: Path
    mode: str
    format_version: int
    created_at: int
    created_by: str
    initialised_now: bool
    #: The per-user archive directory this profile was selected against.  It is
    #: the one file a profile may never be an alias of, so the archive guard
    #: needs it; it is deliberately not part of the bounded event block below.
    default_archive_dir: Path

    def role_store_fields(self) -> dict[str, Any]:
        """The bounded, credential-free profile block every archive event carries."""

        return {
            "play_profile_id": self.profile_id,
            "play_profile_uid": self.profile_uid,
            "play_profile_directory": str(self.directory),
            "play_profile_root": str(self.root),
            "play_profile_mode": self.mode,
            "play_profile_format_version": self.format_version,
            "play_profile_identity_origin": self.created_by,
            "play_profile_created_at": self.created_at,
            "play_profile_initialised_now": self.initialised_now,
            # Guaranteed by the refusals above, and carried so that a reader of
            # the log does not have to re-derive where the profile is NOT.
            "play_profile_is_default_archive": False,
            "play_profile_inside_repository": False,
        }

    def archive_guard(self) -> ProfileArchiveGuard:
        """The archive guard for *this* profile's mode.

        ``resume`` requires the archive to be there; ``init`` is the one call
        that may create the first one.  Neither mode may serve an alias of
        another file, and no mode ever repairs or replaces an archive.
        """

        return ProfileArchiveGuard(
            directory=self.directory,
            default_archive_dir=self.default_archive_dir,
            require_existing=self.mode == PROFILE_MODE_RESUME,
        )

    def open_store(
        self, *, time_source: Callable[[], float] | None = None
    ) -> RoleStore:
        """Take the lease and open the profile's archive, or refuse.

        The archive is verified *before* the lease, so a profile whose save is
        missing, damaged or somebody else's file does not even get a lock file;
        the guard then stays with the store and re-proves the file before the
        first write.  The lease is released by
        :meth:`revival_stub.role_store.RoleStore.close`, so the profile is served
        for exactly as long as the store is open.
        """

        guard = self.archive_guard()
        guard.verify(self.directory / STORE_FILENAME)
        lock = acquire_instance_lock(self.directory, time_source=time_source)
        try:
            return RoleStore.at_directory(
                self.directory,
                kind=KIND_PLAY_PROFILE,
                profile_fields=self.role_store_fields(),
                instance_lock=lock,
                time_source=time_source,
                archive_guard=guard,
            )
        except BaseException:
            lock.release()
            raise


def _initialise_profile(
    *,
    profile_id: str,
    root: Path,
    directory: Path,
    default_archive_dir: str | Path,
    time_source: Callable[[], float],
) -> PlayProfile:
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PlayProfileRefusedError(
            REASON_DIRECTORY_UNWRITABLE, f"cannot create play profile root {root}: {exc}"
        ) from exc
    if directory.exists():
        raise PlayProfileRefusedError(
            REASON_ALREADY_EXISTS,
            f"play profile {directory} already exists; init never adopts, "
            f"clears, replaces or migrates an existing profile",
        )
    try:
        # Not exist_ok: a directory that appeared between the check above and
        # here must be reported, never adopted.
        directory.mkdir()
    except FileExistsError as exc:
        raise PlayProfileRefusedError(
            REASON_ALREADY_EXISTS,
            f"play profile {directory} appeared while init was running; refusing "
            f"to adopt it",
        ) from exc
    except OSError as exc:
        raise PlayProfileRefusedError(
            REASON_DIRECTORY_UNWRITABLE,
            f"cannot create play profile directory {directory}: {exc}",
        ) from exc
    now = int(time_source())
    payload = {
        "format": PROFILE_MANIFEST_FORMAT,
        "format_version": PROFILE_FORMAT_VERSION,
        "profile_id": profile_id,
        "profile_uid": secrets.token_hex(16),
        "created_at": now,
        "created_by": PROFILE_ORIGIN_LOCAL_INIT,
        "purpose": (
            "durable local play profile served by the loopback compatibility "
            "stub; a local simulator record, not an original-server record"
        ),
    }
    try:
        _write_manifest(directory, payload)
    except OSError as exc:
        # The directory was created by this call and now cannot be described;
        # it is left exactly as it is (never removed, never half-adopted) so a
        # later resume reports `profile_manifest_missing` instead of guessing.
        raise PlayProfileRefusedError(
            REASON_DIRECTORY_UNWRITABLE,
            f"cannot write the play profile manifest in {directory}: {exc}",
        ) from exc
    try:
        directory.chmod(0o700)
    except OSError:  # pragma: no cover - Windows/POSIX policy dependent
        pass
    return PlayProfile(
        profile_id=profile_id,
        profile_uid=payload["profile_uid"],
        directory=directory,
        root=root,
        mode=PROFILE_MODE_INIT,
        format_version=PROFILE_FORMAT_VERSION,
        created_at=now,
        created_by=PROFILE_ORIGIN_LOCAL_INIT,
        initialised_now=True,
        default_archive_dir=Path(default_archive_dir),
    )


def _resume_profile(
    *,
    profile_id: str,
    root: Path,
    directory: Path,
    default_archive_dir: str | Path,
) -> PlayProfile:
    if not directory.exists():
        raise PlayProfileRefusedError(
            REASON_MISSING,
            f"play profile {directory} does not exist; an uninitialised profile "
            f"is never created implicitly -- initialise it once with "
            f"--profile-mode {PROFILE_MODE_INIT}",
        )
    if not directory.is_dir():
        raise PlayProfileRefusedError(
            REASON_NOT_A_DIRECTORY, f"play profile path {directory} is not a directory"
        )
    payload = read_profile_manifest(directory)
    stored_id = payload.get("profile_id")
    if stored_id != profile_id:
        raise PlayProfileRefusedError(
            REASON_IDENTITY_MISMATCH,
            f"{directory} holds the profile named {stored_id!r}, not "
            f"{profile_id!r}; refusing to serve it under another name",
        )
    profile = PlayProfile(
        profile_id=profile_id,
        profile_uid=str(payload["profile_uid"]),
        directory=directory,
        root=root,
        mode=PROFILE_MODE_RESUME,
        format_version=int(payload["format_version"]),
        created_at=int(payload["created_at"]),
        created_by=str(payload["created_by"]),
        initialised_now=False,
        default_archive_dir=Path(default_archive_dir),
    )
    # The archive *file*, not only the directory that holds it: a profile whose
    # save is missing, half-built or another file's alias is refused here, while
    # this mode can still refuse without having created anything.
    profile.archive_guard().verify(directory / STORE_FILENAME)
    return profile


def select_play_profile(
    profile_id: str,
    *,
    default_archive_dir: str | Path,
    mode: str = PROFILE_MODE_DEFAULT,
    root: str | Path | None = None,
    time_source: Callable[[], float] | None = None,
) -> PlayProfile:
    """Resolve one named profile for one typed mode, or refuse.

    ``init`` creates the directory and its manifest once and refuses an
    existing directory; ``resume`` requires both *and* the archive the profile
    serves, and refuses everything else -- including a profile whose
    ``revival_roles.sqlite3`` is missing or is another file's alias.  No third
    behaviour exists, and neither mode ever deletes, renames or rewrites
    anything it did not create in this call.
    """

    identifier = validate_profile_id(profile_id)
    if mode not in PROFILE_MODES:
        raise PlayProfileRefusedError(
            REASON_MODE_INVALID,
            f"unknown play profile mode {mode!r}; expected one of {PROFILE_MODES}",
        )
    clock = time_source or time.time
    resolved_root = resolve_profile_root(
        default_profile_root() if root is None else root,
        default_archive_dir=default_archive_dir,
    )
    directory = resolve_profile_directory(
        resolved_root, identifier, default_archive_dir=default_archive_dir
    )
    if mode == PROFILE_MODE_INIT:
        return _initialise_profile(
            profile_id=identifier,
            root=resolved_root,
            directory=directory,
            default_archive_dir=default_archive_dir,
            time_source=clock,
        )
    return _resume_profile(
        profile_id=identifier,
        root=resolved_root,
        directory=directory,
        default_archive_dir=default_archive_dir,
    )
