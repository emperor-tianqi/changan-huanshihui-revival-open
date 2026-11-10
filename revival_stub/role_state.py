"""Source-backed minimal role state for the post-create login snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .compatibility_profile import (
    RoleCompatibilityState,
    instantiate_role_compatibility_state,
)


# Recovered from pkgSyncConstMgr.  Index 1 is the base snapshot segment and
# index 40 is the terminal synchronization marker.
SYNC_INDEX_START = 1
SYNC_INDEX_END = 40

# Recovered from zys_scAll/const/pkgGuideConst.lua (`STATE_RECEIVE = 1`,
# `STATE_FINISH = 2`).  It is the value `pkgStoryModelMgr.updateStoryStatusByClient`
# writes into `le_story_list[id].dState` and the value
# `pkgStoryModelMgr.checkStoryFinish` compares against, so it is the one state a
# replayed story row may carry.  The client's own constant, not a synthetic one.
STORY_STATE_FINISH = 2

# Role IDs use Lua's number / protocol ``p`` (IEEE-754 double).  The client
# extracts the server ID with role_id % 100000, so keep a decimal namespace
# whose values are exact integers below 2**53.
ROLE_ID_SERVER_FACTOR = 100_000
REVIVAL_LOCAL_ROLE_SERIAL_BASE = 9_000_000
MAX_EXACT_LUA_INTEGER = 2**53

# Source-backed new-role values.  Level 1 exists in config/role/level and
# POWER_INIT is 180 in pkgCommonConstMgr.  The remaining counters/currencies
# are additive quantities whose zero value is accepted by initSuperManInfo.
INITIAL_LEVEL = 1
INITIAL_EXP = 0
INITIAL_CRYSTAL = 0
INITIAL_POWER = 180
INITIAL_REFINE_STONE = 0


ROLE_INFO_FIELD_NAMES = (
    "username",
    "role_id",
    "role_name",
    "career",
    "level",
    "exp",
    "crystal",
    "power",
    "refine_stone",
    "one_day_recharge_num",
    "one_day_recharge_time",
    "recharge_sum",
    "change_name_num",
    "trumpet",
    "create_time",
    "crystal_rmb",
    "crystal_rmb_bind",
    "crystal_ex",
    "crystal_ex_bind",
    "crystal_free",
    "crystal_free_bind",
)


def allocate_revival_role_id(serial: int, server_id: float | int) -> int:
    """Allocate a positive exact local ID preserving the selected server ID."""

    numeric_server_id = float(server_id)
    if not math.isfinite(numeric_server_id) or not numeric_server_id.is_integer():
        raise ValueError(f"server ID must be a finite integer, got {server_id!r}")
    integer_server_id = int(numeric_server_id)
    if not 0 < integer_server_id < ROLE_ID_SERVER_FACTOR:
        raise ValueError(
            f"server ID must be in 1..{ROLE_ID_SERVER_FACTOR - 1}, "
            f"got {integer_server_id}"
        )
    role_id = int(serial) * ROLE_ID_SERVER_FACTOR + integer_server_id
    if not 0 < role_id < MAX_EXACT_LUA_INTEGER:
        raise ValueError(f"role ID is outside the exact Lua-number range: {role_id}")
    return role_id


@dataclass(slots=True)
class MinimalRoleState:
    username: str
    role_id: int
    role_name: str
    career: int
    create_time: int
    server_id: int | None = None
    compatibility: RoleCompatibilityState | None = None

    def __post_init__(self) -> None:
        if not 0 < self.role_id < MAX_EXACT_LUA_INTEGER:
            raise ValueError(f"role ID is outside the exact Lua-number range: {self.role_id}")
        derived_server_id = self.role_id % ROLE_ID_SERVER_FACTOR
        if self.server_id is None:
            self.server_id = derived_server_id
        if self.server_id != derived_server_id:
            raise ValueError(
                f"role ID server suffix {derived_server_id} != server ID {self.server_id}"
            )
        if self.compatibility is None:
            self.compatibility = instantiate_role_compatibility_state(self.career)
        elif self.compatibility.active_soul != 101 or self.career != 1:
            raise ValueError(
                "compatibility profile requires career 1 and active soul 101"
            )

    def protocol_fields(self) -> tuple[object, ...]:
        """Return S2C10 fields in the recovered handler's exact order."""

        return (
            self.username,
            self.role_id,
            self.role_name,
            self.career,
            INITIAL_LEVEL,
            INITIAL_EXP,
            INITIAL_CRYSTAL,
            INITIAL_POWER,
            INITIAL_REFINE_STONE,
            0,  # one-day recharge amount, stored client-side as value / 100
            0,  # one-day recharge timestamp
            0,  # total recharge
            0,  # change-name count
            0,  # trumpet count
            self.create_time,
            0,  # crystal RMB
            0,  # crystal RMB bound
            0,  # crystal exchange
            0,  # crystal exchange bound
            0,  # crystal free
            0,  # crystal free bound
        )

    def event_role(self) -> dict[str, object]:
        assert self.compatibility is not None
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "career": self.career,
            "server_id": self.server_id,
            "level": INITIAL_LEVEL,
            "power": INITIAL_POWER,
            "create_time": self.create_time,
            "compatibility_profile": self.compatibility.profile.profile_id,
        }
