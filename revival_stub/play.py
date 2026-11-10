"""The normal-play entry point: one named profile, one command.

    python -m revival_stub.play dev-main                    # resume and play
    python -m revival_stub.play dev-main --profile-mode init # one-time setup

This is a thin wrapper over :func:`revival_stub.app.run`: it names the profile
mode and the profile, so a player never has to know a path, and it exposes only
the flags a play session has a reason to change.  The service it starts is the
same loopback HTTP/TCP/entry contract every other mode serves, and the archive
it serves is the one the name selects -- nothing else is inferred.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import app
from .owner_draw_test import parse_test_grant
from .draw_odds import DRAW_ODDS_MODES, DRAW_ODDS_RECONSTRUCTED
from .play_profile import PROFILE_MODE_DEFAULT, PROFILE_MODES, default_profile_root
from .protocol_policy import PROTOCOL_POLICIES, PROTOCOL_POLICY_FREE_PLAY_TOLERANT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m revival_stub.play",
        description=(
            "Serve one named durable play profile over the loopback "
            "compatibility stub: the same role, in the same directory, every "
            "time this command runs."
        ),
    )
    parser.add_argument(
        "play_profile",
        metavar="PROFILE-ID",
        help="the profile to play, e.g. dev-main (3-32 lowercase letters, digits or hyphens)",
    )
    parser.add_argument(
        "--profile-mode",
        choices=PROFILE_MODES,
        default=PROFILE_MODE_DEFAULT,
        help=(
            f"{PROFILE_MODE_DEFAULT} (default): play the profile that exists; "
            f"{PROFILE_MODES[0]}: create the profile once (refuses a name that "
            f"already exists)"
        ),
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        help=f"directory holding every profile; default {default_profile_root()}",
    )
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument(
        "--advertise-host",
        default="127.0.0.1",
        help="host written into enter.txt and server-list.txt (e.g. 10.0.2.2 for an emulator)",
    )
    parser.add_argument(
        "--http-port", type=int, default=0, help="0 chooses a free loopback port"
    )
    parser.add_argument(
        "--tcp-port", type=int, default=0, help="0 chooses a free loopback port"
    )
    parser.add_argument(
        "--protocol-policy",
        choices=PROTOCOL_POLICIES,
        default=PROTOCOL_POLICY_FREE_PLAY_TOLERANT,
        help=(
            "free_play_tolerant (default for the normal-play entry): a "
            "known-but-unimplemented client feature is recorded without "
            "killing the socket; strict restores the D-08 fail-closed path"
        ),
    )
    parser.add_argument(
        "--test-grant",
        type=parse_test_grant,
        default=None,
        metavar="OWNER-DRAW[:N/M]",
        help=(
            "explicit Owner draw-test overlay (qa-changan-main only): grant "
            "N normal / M advanced prayer tickets and answer draw requests "
            "with the mode selected by --draw-odds"
        ),
    )
    parser.add_argument(
        "--draw-odds",
        choices=DRAW_ODDS_MODES,
        default=DRAW_ODDS_RECONSTRUCTED,
        help=(
            "reconstructed_rate_table (default for the normal-play entry): the "
            "documented help-text rates plus the advanced 10-pull rare "
            "guarantee and 89->90 legend pity; deterministic_fixture keeps the "
            "frozen test pool and no RNG"
        ),
    )
    parser.add_argument("--hex-dump", action="store_true")
    # TASK-0277: the normal-play entry is the ONE launcher a human uses to serve a dev-play
    # archive, so it must be able to forward the three explicit overlays. Without these the
    # wrapper silently dropped them (the app then applied no overlay at all) and a dev-play
    # session started from here looked exactly like a client that ignored the server.
    parser.add_argument(
        "--dev-play-artifact",
        action="store_true",
        help="explicit dev-play overlay for the dev-artifact-main archive (level 60 + C2S 744 chain)",
    )
    parser.add_argument(
        "--dev-play-babel",
        action="store_true",
        help="explicit dev-play overlay for the dev-babel-main archive (level 29 + S2C 580 + chapters)",
    )
    parser.add_argument(
        "--dev-play-resources",
        action="store_true",
        help="explicit dev-play overlay for the dev-resource-main archive (level 29 + ticket ledger)",
    )
    return parser


def to_app_namespace(args: argparse.Namespace) -> argparse.Namespace:
    """The one archive-selecting answer, in the shape :func:`app.run` reads."""

    return argparse.Namespace(
        bind_host=args.bind_host,
        advertise_host=args.advertise_host,
        http_port=args.http_port,
        tcp_port=args.tcp_port,
        client_patch_dir=None,
        role_mode=app.ROLE_MODE_PLAY_PROFILE,
        play_profile=args.play_profile,
        profile_mode=args.profile_mode,
        profile_root=args.profile_root,
        data_dir=None,
        hex_dump=args.hex_dump,
        protocol_policy=args.protocol_policy,
        test_grant=args.test_grant,
        draw_odds=args.draw_odds,
        dev_play_artifact=args.dev_play_artifact,
        dev_play_babel=args.dev_play_babel,
        dev_play_resources=args.dev_play_resources,
    )


def main(argv: list[str] | None = None) -> None:
    app.run(to_app_namespace(build_parser().parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    main()
