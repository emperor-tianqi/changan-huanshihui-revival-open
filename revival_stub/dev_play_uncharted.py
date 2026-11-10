"""Dev-play 秘境 (uncharted) entry data plane (TASK-0272).

The contract is TASK-0271 (reports/task_0271_uncharted_entry_contract.json). The client's
first-clear flow is exactly:

    C2S 244 unchartedEnter  "Ny"   (unchartedId, bSweep=false)   <-- LOCK-SEND
    C2S 260 syncUnchartedBuildingInfo ""                          (no reply partner)
    S2C 434 returnUnchartedCfg      "NNaNyyy"                    (id, seed, instance, dead, 3 bools)
    S2C 439 returnUnchrtedDataStart ""                            (opens the room matrix)
    S2C 440 returnUnchrtedDataProcess "NNNN"  xN                  (layer, area, room, roomId)
    S2C 441 returnUnchrtedDataEnd   ""
    S2C 458 unchartedEnter          "NN"                          (status, layer)  <-- UNLOCK

The client draws every room from its own config (unchartedIndex.mapConf -> uncharted1[roomId]),
so the server only has to deliver the matrix. Every value here is a dev-play CHOICE:
provenance synthetic_compatibility, replaceable, never historical.

The matrix is deliberately the smallest legal one (one layer-1 room, roomId 101, the first
row of unchartedIndex[1].firstRoomWeight). Whatever the real generator produced is UNKNOWN
(TASK-0271 U-1/U-2/U-8), so the slice proves the chain, not the original dungeon.
"""

from __future__ import annotations

#: The overlay that opens this content: the babel (通天塔) dev-play profile, because the
#: uncharted rows are gated on pkgBabelMod.getRealm() > 1 or babelLimit(10) <= getCurFloor(),
#: and only that overlay pushes S2C 580.
UNCHARTED_ENTRY_PROFILE_ID = "dev-babel-main"

PROVENANCE_SYNTHETIC = "synthetic_compatibility"

#: (layer, area, room, roomId) rows of the minimal matrix. 101 is unchartedIndex[1]'s first
#: firstRoomWeight entry and a real uncharted1 row.
# TASK-0326: the client asserts (room count - 1) > 1 for the first area, so the matrix needs at
# least THREE rows; one room froze the scene preload (RUN27 pkgUnchartedTerrainMgr:279). The same
# shipped room design is reused for all three slots rather than inventing room ids.
# TASK-0330: room 2 is the EXIT room (uncharted1[106], whose type-6 cell holds the exit NPC), because
# RUN33 only ever saw the entrance NPC: every row was roomId 101. Room 101 stays for the entrance.
# TASK-0335: room 1 IS the exit room now. RUN33-RUN36 could never walk across the bridge, and the
# exit NPC (unchartedBossNpc#2, type 1, the only 斩 control) exists only in 106/306/506 -- so the
# room the player spawns in has to be 106. Room 2 keeps the entrance room for its boss-select NPC.
UNCHARTED_MINIMAL_ROOMS = ((1, 1, 1, 106), (1, 1, 2, 101), (1, 1, 3, 101))

#: uncharted1[101].cells carries a type-4 event whose option chain leads to the first fight
#: (TASK-0271 Q4), so one room is enough to reach a battle via the shared C2S 69 path.
UNCHARTED_FIRST_FIGHT_ROOM = 101
#: TASK-0330: the exit room (uncharted1[106]); its type-6 cell hosts the exit NPC that offers the fight.
UNCHARTED_EXIT_ROOM = 106
UNCHARTED_PVE_TYPE = 88

#: Instance id is an opaque client-side key; "uncharted1" is a local choice.
UNCHARTED_INSTANCE_ID = "uncharted1"
UNCHARTED_STATUS_ENTER = 1
UNCHARTED_STATUS_REFUSED = 0
UNCHARTED_ENTRY_LAYER = 1
UNCHARTED_SEED = 20261026


def return_uncharted_cfg_fields(uncharted_id: int = 1, *, seed: int = UNCHARTED_SEED,
                                instance_id: str = UNCHARTED_INSTANCE_ID,
                                dead_count: int = 0):
    """S2C 434 returnUnchartedCfg "NNaNyyy": the only writer of the run identity."""
    return (int(uncharted_id), int(seed), str(instance_id), int(dead_count),
            False, False, False)


def uncharted_enter_fields(status: int = UNCHARTED_STATUS_ENTER,
                           layer: int = UNCHARTED_ENTRY_LAYER):
    """S2C 458 unchartedEnter "NN": dStatus == 1 enters, anything else keeps the player out."""
    return (int(status), int(layer))


def uncharted_room_rows(rooms=UNCHARTED_MINIMAL_ROOMS):
    """The S2C 440 "NNNN" rows, in the order the client expects them."""
    return tuple((int(layer), int(area), int(room), int(room_id))
                 for layer, area, room, room_id in rooms)


def uncharted_entry_packets(uncharted_id: int = 1):
    """(name, fields) pairs for one full entry, in send order (458 last)."""
    # TASK-0326: order matters. RUN27 proved returnUnchrtedDataStart must precede
    # returnUnchartedCfg, because the cfg handler runs findHideRoomEnter as soon as the run id is
    # non-zero and that reads the hide data 439 creates. The completed bracket (442/444) follows
    # the room rows so le_tbUnchartedCompleted exists before the scene preload reads it.
    packets = [("returnUnchrtedDataStart", ()),
               ("returnUnchartedCfg", return_uncharted_cfg_fields(uncharted_id))]
    for row in uncharted_room_rows():
        packets.append(("returnUnchrtedDataProcess", row))
    packets.append(("returnUnchrtedDataEnd", ()))
    # TASK-0328: the small map (the only in-scene room overview) needs the unlock-area table.
    # TASK-0330: three real boss ids from cfg unchartedBoss (1 天罡神将, 2 火女, 3 人偶师), none
    # unlocked yet, nothing selected.
    packets.append(("returnUnchartedBossList", (1, 2, 3)))
    packets.append(("returnUnchartedBossUnlocked", (False, False, False)))
    # TASK-0335: boss 1 already chosen, so the exit NPC has something to send the player after.
    packets.append(("returnUnchartedBossSelected", (1,)))
    packets.append(("returnUnchartedUnlockAreaStart", ()))
    packets.append(("returnUnchartedUnlockAreaProcess", (1, 1)))
    packets.append(("returnUnchartedUnlockAreaEnd", ()))
    packets.append(("returnUnchrtedCompletedStart", ()))
    packets.append(("returnUnchrtedCompletedEnd", ()))
    # TASK-0327: three more empty containers. createPlayerObj pairs le_tbUnchartedBuff before any
    # nil guard (RUN28 froze there), and the task / npc-hp globals are read the same way.
    for start, end in (("returnUnchrtedBuffStart", "returnUnchrtedBuffEnd"),
                       ("returnUnchrtedTaskStart", "returnUnchrtedTaskEnd"),
                       ("returnUnchrtedNpcHpRecordStart", "returnUnchrtedNpcHpRecordEnd")):
        packets.append((start, ()))
        packets.append((end, ()))
    packets.append(("unchartedEnter", uncharted_enter_fields()))
    return tuple(packets)

# TASK-0323: the select view needs the building container before it can render, and RUN24 watched
# pkgUnchartedData:702 raise pairs(nil) because S2C 484 was never sent. An EMPTY bracket (start +
# end, no rows) is the smallest honest answer: the shipped table then exists and the view draws
# its own empty strip instead of aborting.


def building_sync_packets(rows=()):
    """(name, fields) for the uncharted building container: start, optional rows, end."""

    packets = [("syncUnchartedBuildingInfoStart", ())]
    for row in rows:
        packets.append(("syncUnchartedBuildingInfo", tuple(row)))
    packets.append(("syncUnchartedBuildingInfoEnd", ()))
    return tuple(packets)

# TASK-0336: the reply's positions 5/8/9 (cell index, roomId, boss id). pkgPveEnterData
# resolves them against uncharted1[roomId].cells[index] and unchartedBoss[bossId]; the
# client's own C2S sends 0 for both ids, so they must come from the server.
UNCHARTED_BOSS_CELL_INDEX = 2   # the type-6 cell of the exit room
UNCHARTED_BATTLE_ROOM_ID = UNCHARTED_EXIT_ROOM
UNCHARTED_BATTLE_BOSS_ID = 1    # the boss pre-selected by S2C 470
BATTLE_IDENTITY = (UNCHARTED_BOSS_CELL_INDEX, UNCHARTED_BATTLE_ROOM_ID,
                   UNCHARTED_BATTLE_BOSS_ID)

# TASK-0340: the uncharted battle reply's position 18 is the enemy HP percent table, and its
# grammar is "index_encoded;..." -- NOT the type-66 "index,percent" form. The client decodes
# with tbSet[i] = clamp(v/7 - SALT - i, 0, 10000) where SALT is the constant 25486 baked into
# pkgBattleStatistics.max (bytecode LOADK 25486.0 + SETGLOBAL, no other writer in 3373 assets).
# A comma row here gives split2Number("_", "1,2500") -> nil and aborts the battle (:461).
HP_PERCENT_SALT = 25486
HP_PERCENT_SCALE = 7


def hp_percent_table(percent=0.25, count=5):
    """The position-18 string for one uncharted fight: "1_195909;2_195916;..."."""

    scaled = int(round(float(percent) * 10000))
    rows = []
    for index in range(1, int(count) + 1):
        encoded = (scaled + HP_PERCENT_SALT + index) * HP_PERCENT_SCALE
        rows.append(f"{index}_{encoded}")
    return ";".join(rows)

# TASK-0343/0344: the 秘境 WIN gain block. unchartedBoss[1].rewardId = 2001, and
# unchartedReward[2001] declares exactly one slot: type 9 (TB_REWARD_TYPE.PIECE), itemId 4438,
# num 5 (TASK-0343 contract, decoded from the shipped unchartedReward.max). The panel drains
# TB_GAIN_TYPE.FIGHT = 1 when 'battlePveGameOverEnd' arrives.
UNCHARTED_WIN_GAIN_TYPE = 1
UNCHARTED_BOSS_REWARD_ROWS = {
    1: ((9, 4438, 5),),
}


def uncharted_grant_key(role_id, uncharted_id, layer, area, room, boss_id):
    """The archive-meta key that makes a win reward exact-once."""

    return (f"uncharted_grant:{int(role_id)}:{int(uncharted_id)}:{int(layer)}:"
            f"{int(area)}:{int(room)}:{int(boss_id)}")

# TASK-0345: the percentage the reply actually sends. RUN43/44 used 0.0 (the slots are
# 'not instanced'), which is the setting that produced the first two device wins.
UNCHARTED_HP_PERCENT = 0.0

