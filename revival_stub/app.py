"""Command-line launcher for the loopback-only milestone-1 services."""

from __future__ import annotations

import argparse
import dataclasses
import os
import socket
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .eventlog import EventLogger
from .http_contract import HttpContract, inspect_patch_residue
from .http_stub import RevivalHTTPServer
from .owner_draw_test import parse_growth_grant, parse_test_grant
from .draw_odds import DRAW_ODDS_DETERMINISTIC_FIXTURE, DRAW_ODDS_MODES
from .play_profile import (
    PROFILE_MODE_DEFAULT,
    PROFILE_MODE_INIT,
    PROFILE_MODES,
    PlayProfileError,
    default_profile_root,
    select_play_profile,
)
from .protocol_policy import PROTOCOL_POLICIES, PROTOCOL_POLICY_STRICT
from .role_store import STORE_FILENAME, RoleStore, RoleStoreError
from .tcp_stub import RevivalTCPServer


#: The three typed role modes.  None is inferred from anything: the mode is an
#: argument, and an unknown value is refused by argparse before a socket opens.
ROLE_MODE_RESUME = "resume_existing_role"
ROLE_MODE_FRESH_TEST = "fresh_role_test"
#: A named, durable play profile: the normal-play entry.  It serves the same
#: recovered role stream as `resume_existing_role`, from a directory that a
#: name -- not a path -- selects, and it is the only mode whose archive is a
#: single player's (see `role_store.KIND_PLAY_PROFILE`).
ROLE_MODE_PLAY_PROFILE = "play_profile"
ROLE_MODES = (ROLE_MODE_RESUME, ROLE_MODE_FRESH_TEST, ROLE_MODE_PLAY_PROFILE)
ROLE_MODE_DEFAULT = ROLE_MODE_RESUME

#: Overrides the resolved data directory.  An environment override is named so
#: that a device or operator setup can point at one archive without editing
#: command lines, and so tests can prove the resolution without a real profile.
DATA_DIR_ENV = "REVIVAL_DATA_DIR"

#: Directory name below the platform's per-user data location.
DATA_DIR_NAME = "chang_an_revival"


def default_data_dir(
    *, environ: dict[str, str] | None = None, home: Path | None = None
) -> Path:
    """Resolve the per-user archive directory, never inside the repository.

    The role archive must outlive `git clean -fdx`, an Autopilot test run and a
    device run's temporary directory, so it is placed in the platform's
    per-user application-data location:

    * ``REVIVAL_DATA_DIR`` when set (explicit operator/test override);
    * ``%LOCALAPPDATA%\\chang_an_revival`` on Windows;
    * ``$XDG_DATA_HOME/chang_an_revival`` (or ``~/.local/share/...``) elsewhere.
    """

    env = os.environ if environ is None else environ
    override = env.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / DATA_DIR_NAME
    base = env.get("XDG_DATA_HOME")
    if base:
        return Path(base) / DATA_DIR_NAME
    return (home or Path.home()) / ".local" / "share" / DATA_DIR_NAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Chang'an Huanshihui 0.1.20.1 milestone-1 compatibility stub"
    )
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument(
        "--http-port", type=int, default=0,
        help="HTTP listen port; 0 chooses a free loopback port",
    )
    parser.add_argument(
        "--tcp-port", type=int, default=0,
        help="TCP listen port; 0 chooses a free loopback port",
    )
    parser.add_argument(
        "--advertise-host",
        default="127.0.0.1",
        help="host written into enter.txt and server-list.txt",
    )
    parser.add_argument(
        "--client-patch-dir",
        type=Path,
        help="optional read-only check for an existing 0.1.20.1.zip",
    )
    parser.add_argument(
        "--role-mode",
        choices=ROLE_MODES,
        default=ROLE_MODE_DEFAULT,
        help=(
            f"{ROLE_MODE_RESUME} (default): serve the durable per-user role "
            f"archive, resuming a bound account and creating a role for an "
            f"unbound one; {ROLE_MODE_FRESH_TEST}: a throwaway archive in its "
            f"own temporary directory, so a new-role test can neither read nor "
            f"pollute the real one; {ROLE_MODE_PLAY_PROFILE}: one *named* "
            f"durable play profile (--play-profile), the normal-play entry"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help=(
            f"directory holding {STORE_FILENAME}; default {default_data_dir()} "
            f"(only for --role-mode {ROLE_MODE_RESUME})"
        ),
    )
    parser.add_argument(
        "--play-profile",
        metavar="PROFILE-ID",
        help=(
            f"the named durable play profile to serve "
            f"(required by, and only meaningful for, --role-mode {ROLE_MODE_PLAY_PROFILE})"
        ),
    )
    parser.add_argument(
        "--profile-mode",
        choices=PROFILE_MODES,
        default=PROFILE_MODE_DEFAULT,
        help=(
            f"{PROFILE_MODE_DEFAULT} (default): the named profile must already "
            f"exist and is served as it is; {PROFILE_MODE_INIT}: create the "
            f"named profile once, refusing a directory that already exists "
            f"(only for --role-mode {ROLE_MODE_PLAY_PROFILE})"
        ),
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        help=(
            "directory holding every named play profile; "
            f"default {default_profile_root()} (only for "
            f"--role-mode {ROLE_MODE_PLAY_PROFILE})"
        ),
    )
    parser.add_argument(
        "--protocol-policy",
        choices=PROTOCOL_POLICIES,
        default=PROTOCOL_POLICY_STRICT,
        help=(
            f"{PROTOCOL_POLICY_STRICT} (default): D-08 fail-closed on an "
            "unimplemented or unknown id -- required for Device Acceptance and "
            "protocol forensic runs; free_play_tolerant: only for the Owner's "
            "Dev Server / manual free play -- a known-but-unimplemented client "
            "request is recorded and the socket stays open, with no state or "
            "business effect"
        ),
    )
    parser.add_argument(
        "--test-grant",
        type=parse_test_grant,
        default=None,
        metavar="OWNER-DRAW[:N/M]",
        help=(
            "explicit Owner draw-test overlay, only for the qa-changan-main "
            "Dev Server: grant N normal / M advanced prayer tickets in the "
            "S2C 27 vector and answer draw requests (see --draw-odds).  "
            "Requires --play-profile qa-changan-main; never used by Device "
            "Acceptance"
        ),
    )
    parser.add_argument(
        "--growth-grant",
        type=parse_growth_grant,
        default=None,
        metavar="OWNER-GROWTH[:ID=N,...]",
        help=(
            "explicit Owner card-growth test overlay, only for the "
            "qa-changan-main Dev Server: seed the dog-food special-card "
            "inventory (cfg ids 1-5) so the client's own 强化 chain can run.  "
            "Every write is labelled synthetic_test_grant; never used by "
            "Device Acceptance"
        ),
    )
    parser.add_argument(
        "--draw-odds",
        choices=DRAW_ODDS_MODES,
        default=DRAW_ODDS_DETERMINISTIC_FIXTURE,
        help=(
            "deterministic_fixture (default): the frozen 130-card test pool, no "
            "RNG; reconstructed_rate_table: the documented help-text rates and "
            "the advanced 10-pull rare guarantee / 89->90 legend pity.  Both "
            "modes require --test-grant and only run on the Owner Dev Server"
        ),
    )
    parser.add_argument(
        "--achieve-level",
        type=int,
        default=None,
        metavar="N",
        help=(
            "explicit Owner achieve-rank test overlay, only for the "
            "qa-changan-main Dev Server: send S2C 589 achieveLevel with rank N "
            "(0-9) so the client's own `pkgAchieveMod.getLevel()` gate "
            "(`achieve_level_limit`, e.g. the tavern 挑战老板娘 at 二钱=2) can "
            "be exercised.  Session-scoped, never persisted; the save has no "
            "recovered achieve producer, so a normal login sends nothing"
        ),
    )
    parser.add_argument(
        "--dev-play-babel",
        action="store_true",
        help=(
            "explicit dev-play overlay for the separate dev-babel-main archive: "
            "announce level 29 in S2C 10, replay the eight MAP_TYPE_EXIT rows of "
            "chapters 1..8 in S2C 390 and push S2C 580 returnBabelInfo.  It takes "
            "effect only when the served profile is dev-babel-main AND that "
            "archive holds the overlay meta seeded by "
            "`python -m revival_stub.dev_play_babel seed --profile dev-babel-main`; "
            "every other profile (qa-changan-main included) keeps the frozen v18 "
            "login stream byte-for-byte"
        ),
    )
    parser.add_argument(
        "--dev-play-artifact",
        action="store_true",
        help=(
            "explicit dev-play overlay for the separate dev-artifact-main "
            "archive: announce level 60 in S2C 10 and answer C2S 744 "
            "artifactDgnOpenUI with S2C 1526 (week + cfgArtifactDgnWeek open "
            "flags) and S2C 1527 (zero first-enter state).  It takes effect "
            "only when the served profile is dev-artifact-main AND that "
            "archive holds the overlay meta seeded by "
            "`python -m revival_stub.dev_play_artifact seed --profile "
            "dev-artifact-main`; every other profile (qa-changan-main "
            "included) keeps the frozen login stream byte-for-byte and "
            "answers no artifactDgn packet"
        ),
    )
    parser.add_argument(
        "--dev-play-resources",
        action="store_true",
        help=(
            "explicit dev-play overlay for the separate dev-resource-main "
            "archive: announce level 29 in S2C 10, set the two S2C 27 ticket "
            "slots to 30, provision the missing money/yinpo/jinpo wallet rows "
            "once (100000/50000/50000) and answer C2S 274/33 with the existing "
            "deterministic draw fixture.  It takes effect only when the served "
            "profile is dev-resource-main AND that archive holds the overlay "
            "meta seeded by `python -m revival_stub.dev_play_resources seed "
            "--profile dev-resource-main`; every other profile (qa-changan-main "
            "included) keeps the frozen v18 login stream byte-for-byte"
        ),
    )
    parser.add_argument(
        "--dev-play-gacha",
        action="store_true",
        help=(
            "explicit dev-play gacha sandbox: on a dev-babel-main / "
            "dev-artifact-main / dev-resource-main profile it also lets "
            "the Owner draw-test overlay run (requires --test-grant), "
            "announces 999999 crystal in S2C 10 and activates all 20 "
            "limited hot-spot banners (1001-1020).  The permanent "
            "qa-changan-main archive is never served by this flag"
        ),
    )
    parser.add_argument("--hex-dump", action="store_true")
    return parser


def build_role_store(args: argparse.Namespace) -> RoleStore:
    """Build the archive the chosen typed mode names, and nothing else."""

    if args.role_mode == ROLE_MODE_PLAY_PROFILE:
        return _build_play_profile_store(args)
    if (
        args.play_profile is not None
        or args.profile_root is not None
        or args.profile_mode != PROFILE_MODE_DEFAULT
    ):
        # The profile flags name a *named* profile.  Accepting them next to a
        # path-based mode would leave two answers to "which archive", so the
        # combination is refused rather than resolved by precedence.
        raise SystemExit(
            f"--play-profile/--profile-mode/--profile-root require "
            f"--role-mode {ROLE_MODE_PLAY_PROFILE}"
        )
    if args.role_mode == ROLE_MODE_FRESH_TEST:
        if args.data_dir is not None:
            raise SystemExit(
                f"--data-dir cannot be combined with --role-mode {ROLE_MODE_FRESH_TEST}: "
                f"that mode always uses its own throwaway archive"
            )
        return RoleStore.fresh_test()
    return RoleStore.at_directory(args.data_dir or default_data_dir())


def _build_play_profile_store(args: argparse.Namespace) -> RoleStore:
    """Resolve the named profile, take its lease, open its archive.

    The name -- never a path -- selects the directory: a caller that wants a
    different profile names a different profile.  Every step below refuses
    before anything is created, so a bad name, a refused location, an
    uninitialised profile, a damaged manifest or a profile another process is
    already serving all stop the launcher with nothing written.
    """

    if args.data_dir is not None:
        raise SystemExit(
            f"--data-dir cannot be combined with --role-mode {ROLE_MODE_PLAY_PROFILE}: "
            f"a play profile is selected by name, so that its directory is fixed "
            f"and auditable; use --profile-root to move the whole collection"
        )
    if not args.play_profile:
        raise SystemExit(
            f"--role-mode {ROLE_MODE_PLAY_PROFILE} needs --play-profile PROFILE-ID "
            f"(a validated name such as dev-main)"
        )
    profile = select_play_profile(
        args.play_profile,
        mode=args.profile_mode,
        root=args.profile_root,
        default_archive_dir=default_data_dir(),
    )
    return profile.open_store()


@dataclass(slots=True)
class StubServers:
    """The two loopback services of one entry, already bound to their ports."""

    contract: HttpContract
    http: RevivalHTTPServer
    tcp: RevivalTCPServer
    http_port: int
    tcp_port: int

    def close(self) -> None:
        self.http.server_close()
        self.tcp.server_close()


def build_servers(
    args: argparse.Namespace, logger: EventLogger, role_store: RoleStore
) -> StubServers:
    """Bind the HTTP and TCP services of one entry.

    Split out of :func:`run` so the *entry* -- not a hand-built server -- is
    what a test drives: the same loopback HTTP/TCP/entry contract, the same
    advertised host, the same port arithmetic, whichever role mode selected the
    archive.  A failure while binding the second service closes the first, so a
    half-started entry never leaves a port owned by nothing.
    """

    tcp_server = RevivalTCPServer(
        (args.bind_host, args.tcp_port),
        logger,
        role_store=role_store,
        protocol_policy=getattr(args, "protocol_policy", PROTOCOL_POLICY_STRICT),
        test_grant=getattr(args, "test_grant", None),
        growth_grant=getattr(args, "growth_grant", None),
        achieve_level_grant=getattr(args, "achieve_level", None),
        draw_odds=getattr(
            args, "draw_odds", DRAW_ODDS_DETERMINISTIC_FIXTURE
        ),
        dev_play_babel=getattr(args, "dev_play_babel", False),
        dev_play_artifact=getattr(args, "dev_play_artifact", False),
        dev_play_resources=getattr(args, "dev_play_resources", False),
        dev_play_gacha=getattr(args, "dev_play_gacha", False),
    )
    actual_tcp_port = int(tcp_server.server_address[1])
    contract = HttpContract(
        advertised_host=args.advertise_host,
        http_port=args.http_port,
        tcp_host=args.advertise_host,
        tcp_port=actual_tcp_port,
    )
    try:
        http_server = RevivalHTTPServer(
            (args.bind_host, args.http_port), contract, logger
        )
    except BaseException:
        tcp_server.server_close()
        raise
    actual_http_port = int(http_server.server_address[1])
    if actual_http_port != contract.http_port:
        contract = dataclasses.replace(contract, http_port=actual_http_port)
        http_server.contract = contract
    return StubServers(
        contract=contract,
        http=http_server,
        tcp=tcp_server,
        http_port=actual_http_port,
        tcp_port=actual_tcp_port,
    )


def port_is_owned(host: str, port: int, *, timeout: float = 0.3) -> bool:
    """True when something already answers connections on ``host:port``.

    Windows lets two sockets bind the same port when ``SO_REUSEADDR`` is set
    (the hazard already recorded for the dashboard port), so "the bind
    succeeded" is not evidence that this process owns the port.  A connect
    attempt is.
    """

    probe_host = host.strip() or "127.0.0.1"
    if probe_host in {"0.0.0.0", "::", "[::]"}:
        probe_host = "127.0.0.1"
    try:
        with socket.create_connection((probe_host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def refuse_owned_ports(args: argparse.Namespace) -> None:
    """Refuse a named play profile's entry on a port another process owns.

    Only the normal-play entry is checked here.  The automated modes have their
    own ownership rules in the device layer (a foreign port holder is already
    refused by its preflight), and this runs before the profile is created,
    opened or locked, so a refusal leaves nothing behind.
    """

    if args.role_mode != ROLE_MODE_PLAY_PROFILE:
        return
    for label, port in (("HTTP", args.http_port), ("TCP", args.tcp_port)):
        if not port:
            continue
        if port_is_owned(args.bind_host, port):
            print(
                f"revival_stub: refusing to start: {label} port {port} on "
                f"{args.bind_host} is already served by another process; a play "
                f"profile is served by one instance on a port it provably owns "
                f"(nothing was created)",
                file=sys.stderr,
            )
            raise SystemExit(2)


def run(args: argparse.Namespace) -> None:
    # Before any state exists: a port this process does not own is not a port it
    # may serve a player's save on.
    refuse_owned_ports(args)
    logger = EventLogger(hex_dump=args.hex_dump)
    try:
        role_store = build_role_store(args)
    except (RoleStoreError, PlayProfileError) as exc:
        # Fail closed before any socket opens: a damaged or unreadable archive
        # is never replaced by an empty one, and a refused profile is never
        # created, adopted or migrated.
        print(f"revival_stub: refusing to start: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    logger.emit(
        "role_mode_selected",
        role_mode=args.role_mode,
        data_dir=str(args.data_dir) if args.data_dir else None,
        play_profile=args.play_profile,
        profile_mode=args.profile_mode if args.play_profile else None,
        **role_store.describe(),
    )
    servers = build_servers(args, logger, role_store)

    residue = inspect_patch_residue(args.client_patch_dir)
    logger.emit("client_patch_residue", **residue)
    logger.emit(
        "stub_started",
        bind_host=args.bind_host,
        http_port=servers.http_port,
        tcp_port=servers.tcp_port,
        entry_override=f"{args.advertise_host}:{servers.http_port}/enter.txt",
        exposed_publicly=args.bind_host not in {"127.0.0.1", "::1", "localhost"},
    )

    tcp_thread = threading.Thread(
        target=servers.tcp.serve_forever,
        name="revival-tcp",
        daemon=True,
    )
    tcp_thread.start()
    try:
        servers.http.serve_forever()
    except KeyboardInterrupt:
        logger.emit("stub_stopping", reason="keyboard_interrupt")
    finally:
        # Both loops stop before either socket closes: closing a listening
        # socket under a running serve_forever would surface as an OSError
        # inside that thread rather than as an orderly stop.
        servers.http.shutdown()
        servers.tcp.shutdown()
        servers.close()
        tcp_thread.join(timeout=2.0)
        # This launcher built the archive, so this launcher closes it: a
        # committed create transaction is already durable, a fresh_role_test
        # archive removes its own temporary directory here, and a named play
        # profile releases its instance lease here.
        role_store.close()
        logger.emit("stub_stopped", role_store_closed=True)


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()

