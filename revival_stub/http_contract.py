"""HTTP bootstrap documents matching the recovered Lua parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CURRENT_VERSION = "0.1.20.1"
CURRENT_VERSION_MD5_PLACEHOLDER = "d41d8cd98f00b204e9800998ecf8427e"
PROJECT_ID = "P10441"


@dataclass(frozen=True, slots=True)
class HttpContract:
    advertised_host: str = "127.0.0.1"
    http_port: int = 18080
    tcp_host: str = "127.0.0.1"
    tcp_port: int = 16001
    server_name: str = "幻世单机"

    @property
    def http_authority(self) -> str:
        return f"{self.advertised_host}:{self.http_port}"

    def enter_entries(self) -> list[str]:
        base = self.http_authority
        # Lua asserts positions 1..13. Empty Lua strings are truthy, so the
        # switch entry can safely be "" and is explicitly skipped by ~= "".
        return [
            f"{base}/patch",                 # 1 patch base
            f"{base}/server-list.txt",       # 2 server list
            f"{base}/entire-version.txt",    # 3 full-package manifest
            f"{base}/noop/account-verify",   # 4 account verification
            f"{base}/noop/pay-notify",       # 5 payment callback
            f"{base}/noop/extra-package",    # 6 extra package
            f"{base}/noop/announcement",     # 7 announcement
            f"{base}/noop/bw-content",       # 8 banned-word content
            f"{base}/noop/bw-name",          # 9 banned-word name
            f"{base}/noop/error-upload",     # 10 error upload
            "",                              # 11 no redirect/switch
            f"{base}/noop/login-code",       # 12 login-code URL list
            PROJECT_ID,                       # 13 project ID
        ]

    def render_enter(self) -> str:
        return "\n".join(f"{{{value}}}" for value in self.enter_entries()) + "\n"

    def render_version(self) -> str:
        # The current version is the last valid record. Its md5/size are never
        # selected as a patch candidate by the recovered parser.
        return (
            f"{{{CURRENT_VERSION}#{CURRENT_VERSION_MD5_PLACEHOLDER}#0}}\n"
        )

    def render_entire_version(self) -> str:
        # Not fetched when render_version() produces an empty later-version list.
        return ""

    def render_server_list(self) -> str:
        return (
            "#1;\n"
            f"$1,{self.tcp_host};\n"
            f"1,{self.server_name},{self.tcp_host},{self.tcp_port},@1,$1,0;\n"
        )


def parse_enter_like_client(content: str) -> list[str]:
    """Mirror pkgUrlConfigMgr.parseUrlFile's line/braces behavior."""
    result: list[str] = []
    for line in content.splitlines():
        if not line:
            continue
        start = line.find("{")
        end = line.find("}")
        if start >= 0 and end >= 0:
            result.append(line[start + 1 : end])
    return result


def versions_after_current_like_client(
    content: str, current: str = CURRENT_VERSION
) -> list[dict[str, Any]]:
    """Mirror pkgVersionMgr.parseVersionFile's relevant behavior."""
    result: list[dict[str, Any]] = []
    found_current = False
    for line in content.splitlines():
        if not line:
            continue
        start = line.find("{")
        end = line.find("}")
        if start < 0 or end < 0:
            continue
        fields = [field for field in line[start + 1 : end].split("#") if field]
        if len(fields) < 2 or not fields[0] or not fields[1]:
            continue
        version = fields[0]
        size = int(fields[2]) if len(fields) >= 3 else None
        if found_current:
            result.append({"version": version, "md5": fields[1], "size": size})
        elif version == current:
            found_current = True
    return result


def parse_server_list_like_client(content: str) -> dict[str, Any]:
    """Parse the subset used by pkgServerDataMgr for the one-server stub."""
    recommended: list[int] = []
    voice: dict[str, str] = {}
    servers: list[dict[str, Any]] = []
    maintenance = 0
    for source_line in content.splitlines():
        if not source_line or source_line.startswith("--"):
            continue
        semicolon = source_line.find(";")
        if semicolon < 0:
            continue
        line = source_line[:semicolon]
        if line.startswith("#"):
            recommended = [int(item) for item in line[1:].split(",")]
        elif line.startswith("$"):
            alias, host = line.split(",", 1)
            voice[alias] = host
        elif line.startswith("&"):
            maintenance = int(line[1:])
        else:
            fields = line.split(",")
            if len(fields) < 7:
                raise ValueError(f"server row has {len(fields)} fields, expected >=7")
            status = int(fields[4][1:])
            servers.append(
                {
                    "id": int(fields[0]),
                    "name": fields[1],
                    "host": fields[2],
                    "port": int(fields[3]),
                    "status": status if maintenance <= 0 else 4,
                    "voice_host": voice.get(fields[5]),
                    "maintenance_end": fields[6] if maintenance <= 0 else maintenance,
                }
            )
    return {"recommended": recommended, "voice": voice, "servers": servers}


def inspect_patch_residue(client_patch_dir: Path | None) -> dict[str, Any]:
    """Read-only check for the client-side zip that can force update UI."""
    if client_patch_dir is None:
        return {
            "status": "not_configured",
            "path": None,
            "exists": None,
            "note": "pass --client-patch-dir when the emulator patch path is known",
        }
    candidate = client_patch_dir / f"{CURRENT_VERSION}.zip"
    return {
        "status": "checked",
        "path": str(candidate.resolve()),
        "exists": candidate.is_file(),
    }


PRODUCTION_SHAPED_ENTRY_RE = re.compile(r"^/cahsh-ob/config/[^/]+/enter\.txt$")

