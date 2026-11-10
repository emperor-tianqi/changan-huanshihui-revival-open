"""Local login, role creation, and minimal role-sync TCP handler."""

from __future__ import annotations

import json
import itertools
import hashlib
import socket
import socketserver
import threading
import time
from random import SystemRandom
from typing import Any, Callable

from .card_templates import template_id_for
from .battle_rewards import (
    GAMEOVERRESULT_NAMES,
    GAMEOVERRESULT_WIN,
    card_exp_for_fight,
    decode_game_over_result,
)
from .card_growth import (
    ADVANCE_COST_DIGEST,
    ADVANCE_STAR_MAX_EXCLUSIVE,
    ADVANCE_STAR_MIN,
    DOG_FOOD_CARD_IDS,
    MAX_STRENGTH_LEVEL,
    advance_cost,
    advance_max_level,
    calculate_level,
    card_star_rare,
    dog_food_exp_for_cfg,
    max_level,
    split_reward_for_card,
    sum_exp,
)
from .shop_catalog import SHOP_CONFIG, SHOP_GOODS
from .wallet_values import WALLET_WIRE_VALUES, apply_wallet, coerce_wallet_value
from .team_power import (
    TEAM_POWER_DIGEST,
    TEAM_POWER_ORIGIN,
    TEAM_POWER_RULE_PROVENANCE,
    TEAM_POWER_SOURCE,
    TEAM_POWER_VALUE_PROVENANCE,
    TeamPowerError,
    team_power,
)
from .codec import (
    CodecError,
    NeedMoreData,
    ReaderState,
    SequenceMismatch,
    UnknownProtocol,
    WriterState,
    decode_packet,
    encode_packet,
)
from .compatibility_profile import (
    BATTLE_GAME_OVER_REPLY_TRIGGER,
    BATTLE_START_DUNGEON_PVE_TYPES,
    BATTLE_START_GOD_FIGHT_PVE_TYPE,
    BATTLE_START_LOCKTOWER_PVE_TYPE,
    BATTLE_START_PVE_OR_PVP_TYPE,
    BATTLE_START_REPLY_TRIGGER,
    BATTLE_START_REQUEST_TYPE_INDEX,
    BATTLE_START_SELF_GROUP_POLICY,
    BATTLE_START_SF_START_TIME_POLICY,
    BATTLE_START_SYNC_COUNT_POSITION,
    BATTLE_START_TYPE_POSITION,
    BATTLE_START_TYPE_RANDOM_SEED_POLICY,
    # TASK-0337: the client reads its own tbParams.p5/p8/p9 from S2C 89 positions 12/15/16,
    # i.e. tuple indices 11/14/15 (BATTLE_START_FIELD_TABLE puts pveOrPvpType at position 5 and
    # p1..p9 at positions 8..16). Writing them at 4/7/8 overwrote the TYPE instead (RUN38).
    BATTLE_START_UNCHARTED_PVE_TYPE,
    COMPATIBILITY_GOAL,
    CHAPTER_FIGHT_REPLAY_ROW_FORMAT,
    PRISON_FIGHT_SNAPSHOT_CONTAINER,
    PROFILE_ID_V13,
    PROFILE_ID_V14,
    PROFILE_ID_V16,
    PROFILE_ID_V17,
    PROFILE_ID_V18,
    PROFILE_ID_V19,
    PROFILE_ID_V20,
    PROFILE_ID_V21,
    PROFILE_ID_V22,
    PROFILE_ID_V23,
    PROFILE_ID_V24,
    TEAM_SNAPSHOT_CARD_SLOTS,
    TEAM_SNAPSHOT_EQUIP_SLOTS,
    CompatibilityProfile,
    BabelSettlementPushPolicy,
    catalog_hashes,
    get_profile,
    get_profile_v20,
    get_profile_v23,
    instantiate_role_compatibility_state,
)
from .eventlog import EventLogger, mask_secret
from . import dev_play_refine
from . import dev_play_skin
from . import dev_play_fabao
from . import dev_play_rune
from . import dev_play_talent
from . import dev_play_toptower
from .http_contract import CURRENT_VERSION
from .owner_draw_test import (
    DETERMINISTIC_FIXTURE_KIND,
    DETERMINISTIC_FIXTURE_ORIGIN,
    FIXTURE_CARD_LEVEL,
    GROWTH_TEST_ORIGIN,
    LOTTERY_TICKET_ADVANCED_REWARD_TYPE,
    LOTTERY_TICKET_ADVANCED_VALUE_INDEX,
    LOTTERY_TICKET_ADVANCED_WIRE_POSITION,
    LOTTERY_TICKET_REWARD_TYPE,
    LOTTERY_TICKET_VALUE_INDEX,
    LOTTERY_TICKET_WIRE_POSITION,
    OWNER_DRAW_TEST_ORIGIN,
    OWNER_DRAW_TEST_PROFILE_ID,
    OwnerDrawTestGrant,
    OwnerGrowthTestGrant,
)
from .owner_draw_pool import POOL_CARDS, POOL_SOURCE, pool_card_at, pool_index_at
from .draw_odds import (
    ADVANCED_RATES,
    CARDS_BY_ID,
    DRAW_ODDS_DETERMINISTIC_FIXTURE,
    DRAW_ODDS_MODES,
    DRAW_ODDS_RECONSTRUCTED,
    HOT_SPOT_BANNERS,
    HOT_SPOT_CLIENT_TAB_SLOTS,
    HOT_SPOT_LIMITED_BANNERS,
    HOT_SPOT_OPEN_LEVEL,
    HOT_SPOT_TEST_BANNERS,
    LIMITED_RATES,
    NORMAL_RATES,
    LEGEND_PITY_DRAWS,
    RARE_GUARANTEE_DRAWS,
    PROBABILITY_AUTHORITY,
    PROBABILITY_MODEL_SOURCE,
    PROBABILITY_MODEL_VERSION,
    PROBABILITY_ORIGIN,
    DrawOddsState,
    draw_outcomes,
)
from . import chapter_map_event
from . import chapter_map_task
from . import chapter_rewards
from . import market_inspect_order
from . import dev_play_babel
from . import dev_play_uncharted
from .dev_play_babel import (
    BABEL_FIELD_2_PROVENANCE,
    DEV_PLAY_BABEL_LEVEL_OVERLAY_REASON,
    DEV_PLAY_BABEL_META_KEY,
    DEV_PLAY_BABEL_ORIGIN,
    DEV_PLAY_BABEL_PROFILE_ID,
    BabelOverlay,
    DevPlayBabelError,
)
from . import dev_play_artifact
from .dev_play_artifact import (
    ARTIFACT_OPEN_UI_ARGUMENT_SEMANTICS,
    ARTIFACT_OPEN_UI_UNKNOWN_ID,
    ARTIFACT_OPEN_CFG_PROVENANCE,
    ARTIFACT_OPEN_RULE_PROVENANCE,
    ARTIFACT_RACE_TOWER_FIELD_6_PROVENANCE,
    ARTIFACT_RACE_TOWER_FIELD_6_SEMANTICS,
    ARTIFACT_RACE_TOWER_FIELD_6_UNKNOWN_ID,
    ARTIFACT_RACE_TOWER_VALUE_PROVENANCE,
    ARTIFACT_WEEK_VALUE_PROVENANCE,
    DEV_PLAY_ARTIFACT_LEVEL_OVERLAY_REASON,
    DEV_PLAY_ARTIFACT_META_KEY,
    DEV_PLAY_ARTIFACT_ORIGIN,
    DEV_PLAY_ARTIFACT_PROFILE_ID,
    ArtifactOverlay,
    DevPlayArtifactError,
)
from . import dev_play_resources
from .dev_play_resources import (
    DEV_PLAY_RESOURCES_FIXTURE_ID,
    DEV_PLAY_RESOURCES_LEVEL_OVERLAY_REASON,
    DEV_PLAY_RESOURCES_META_KEY,
    DEV_PLAY_RESOURCES_ORIGIN,
    DEV_PLAY_RESOURCES_PROFILE_ID,
    DEV_PLAY_RESOURCES_VALUE_ORIGIN,
    DEV_PLAY_TICKET_BALANCE_META_KEY,
    DEV_PLAY_TICKET_BALANCE_ORIGIN,
    DEV_PLAY_TICKET_BALANCE_PROVENANCE,
    DEV_PLAY_TICKET_BALANCE_SEED,
    TICKET_BALANCE_FIELD_FOR_REWARD_TYPE,
    DevPlayResourcesError,
    ResourcesOverlay,
    decode_ticket_balance,
    encode_ticket_balance,
)
from .protocol_catalog import CatalogRegistry, KNOWN_C2S, LOCK_PARTNERS
from .protocol_policy import (
    FREE_PLAY_SAFE_REPLIES,
    FREE_PLAY_STRICT_HANDSHAKES,
    PROTOCOL_POLICIES,
    PROTOCOL_POLICY_FREE_PLAY_TOLERANT,
    PROTOCOL_POLICY_STRICT,
)
from .registry import C2S_REGISTRY, S2C_REGISTRY, ProtocolRegistry, ProtocolSpec
from .role_state import (
    ROLE_INFO_FIELD_NAMES,
    STORY_STATE_FINISH,
    SYNC_INDEX_END,
    SYNC_INDEX_START,
    MinimalRoleState,
)
from .recovered_rewards import (
    FIGHT_MONEY_DIGEST,
    FIGHT_MONEY_ORIGIN,
    FIGHT_MONEY_RULE_PROVENANCE,
    FIGHT_MONEY_SOURCE,
    FIGHT_MONEY_VALUE_PROVENANCE,
    FIGHT_REWARD_SOURCE,
    FIGHT_SPECIAL_CARD_DIGEST,
    FIGHT_SPECIAL_CARD_ORIGIN,
    FIGHT_SPECIAL_CARD_REWARD_TYPE,
    FIGHT_SPECIAL_CARD_RULE_PROVENANCE,
    FIGHT_SPECIAL_CARD_SOURCE,
    FIGHT_SPECIAL_CARD_VALUE_PROVENANCE,
    money_reward_for_fight,
    reward_for_fight,
    special_card_reward_for_fight,
    unknown_reward_for_fight,
)
from .role_store import (
    BAG_SORT_EVIDENCE_CLIENT_C2S,
    BAG_SORT_ORIGIN_LOCAL_OBSERVATION,
    CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
    CHAPTER_EVENT_EVIDENCE_CLIENT_ACTION,
    CHAPTER_FIGHT_EVIDENCE_SETTLED_TRANSACTION,
    GOODS_USE_META_PREFIX,
    GOODS_USE_ORIGIN_LOCAL_C2S,
    GOODS_USE_VALUE_NAMES,
    ROLE_VALUE_GOODS_USE,
    ROLE_VALUE_LOTTERY_TICKET,
    STORY_EVIDENCE_ORDERED_SUCCESSOR,
    STORY_ORIGIN_LOCAL_OBSERVATION,
    TEAM_CARD_SLOTS,
    TEAM_CURRENT_GROUP_ORIGIN_LOCAL_C2S,
    TEAM_MAX_GROUPS,
    TEAM_RECORD_ORIGIN_LOCAL_C2S_UPDATE,
    WALLET_VALUE_NAMES,
    BagSortRecord,
    CardInstanceWrite,
    ChapterBoxAcceptRecord,
    ChapterEventClearRecord,
    ChapterFightClearRecord,
    RoleRecord,
    RoleStore,
    RoleStoreError,
    StoryRecord,
    TicketBalanceShortfallError,
)


LOGIN_FIELD_NAMES = (
    "server_id",
    "sdk_user_id",
    "sdk_verify_md5",
    "sdk_server_time",
    "client_version",
    "channel_id",
    "channel_sub_id",
    "platform",
    "device_id",
    "sdk_username",
    "client_type",
)
SENSITIVE_LOGIN_FIELDS = {
    "sdk_user_id",
    "sdk_verify_md5",
    "channel_id",
    "channel_sub_id",
    "device_id",
    "sdk_username",
}

#: The two recovered ways one connection can arrive at a role.  A closed
#: vocabulary: every event that names an origin uses exactly these values, so a
#: reader never has to infer "was this a replay?" from the surrounding text.
ROLE_ORIGIN_CREATED = "created_local_transaction"
ROLE_ORIGIN_RESUMED = "resumed_existing_binding"
ROLE_ORIGIN_RECONNECTED = "reconnected_existing_binding"
ROLE_ORIGIN_UNBOUND = "no_binding_yet"

#: How the Team login snapshot (S2C 37) projects a stored preset's card slots
#: onto the card instances this very login actually sent as S2C 30 (D-158).
#: A deterministic draw fixture is hidden whenever the Owner draw-test overlay
#: is off, so a preset that still names one would hand the client an instance
#: id its own `tbCard` does not contain; `pkgCardDataMgr.getCardCfgID` then
#: indexes nil before `pkgMainUIMgr.initBtn` runs.  The projection is
#: synthetic compatibility -- not a recovered historical value, and the stored
#: preset is never rewritten.
TEAM_CARD_PROJECTION_POLICY = "suppressed_deterministic_fixtures_excluded"
TEAM_CARD_PROJECTION_ORIGIN = "synthetic_compatibility"

#: ``C2S 233`` story id -> the story its own close callback chains into.
#:
#: This is the ONE pair this build treats as completion evidence, and it is
#: recovered, not chosen: `pkgStoryTrigger_1008.closeCallback` is the only
#: caller of `pkgStoryTrigger_1100.triggerStory()` in the whole corpus (and the
#: only module that names it at all), and both of that module's own functions
#: that reach 1100 run *from* that close callback -- either directly (1100 is
#: already finished) or through `pkgStoryTrigger.move2Npc(8, moveEndCallback)`.
#: `pkgStoryTrigger_1008.triggerStory(8)` itself is reached only from
#: `pkgNpcMgr.create`'s NPC-8 model-load callback.  So an ordered
#: `C2S233(1008)` -> `C2S233(1100)` pair from one session is the client stating
#: that 1008's dialogue view was created and then destroyed; a lone
#: `C2S233(1008)`, a lone `C2S233(1100)` and every other order are not evidence
#: of anything.  `max_decoder/analyze_story_family.py` pins both halves against
#: the recovered sources.
STORY_COMPLETION_SUCCESSORS: dict[int, int] = {1008: 1100}

#: The stories a stored completion may be replayed for.  A row outside this set
#: is reported and NOT sent: this build can only vouch for the pair above.
STORY_REPLAYABLE_COMPLETIONS = frozenset(STORY_COMPLETION_SUCCESSORS)

#: Why a `C2S 233` did not become a stored completion, or why a stored row was
#: not replayed.  Closed vocabularies, so no reader has to interpret prose.
STORY_COMPLETION_REFUSAL_NO_PREDECESSOR = "no_predecessor_c2s233"
STORY_COMPLETION_REFUSAL_NOT_A_PAIR = "no_recovered_successor_pair"
STORY_COMPLETION_REFUSAL_UNBOUND_ROLE = "no_bound_role"
STORY_COMPLETION_REFUSAL_ROLE_CHANGED = "role_changed_since_arming"
STORY_COMPLETION_REFUSAL_ARCHIVE = "role_archive_refused"
STORY_REPLAY_REFUSAL_NOT_REPLAYABLE = "story_not_replayable"
STORY_REPLAY_REFUSAL_ROLE_MISMATCH = "role_identity_mismatch"
STORY_REPLAY_REFUSAL_STATE = "story_state_not_finish"
STORY_REPLAY_REFUSAL_TIME = "story_time_not_encodable"

#: The largest value the recovered `N` wire field can carry.  A stored
#: observation time outside it cannot be put on the wire, so it is refused
#: rather than truncated.
STORY_MAX_WIRE_TIME = 0xFFFFFFFF

#: The bag sort slots this build accepts a `C2S 512` for.  ``ops`` is the
#: client's own key into ``le_tbBagSort``, and these five are exactly the slots
#: the recovered corpus writes there -- every one of them is a
#: `le_tbBagSort[dOps] = dSort` followed by `sendBagSort(dOps, dSort)`:
#:
#: * 1 ``pkgBagUtil.setBagSortCard``   (the bag's card tab sort selector)
#: * 2 ``pkgBagUtil.setBagSortFabao``  (the bag's fabao tab sort selector)
#: * 3 ``pkgBagUtil.setBagCardSkill``  (card-skill display toggle)
#: * 4 ``pkgBagUtil.setGuildBossAnger``(guild-competition anger value)
#: * 5 ``pkgBagUtil.setBagSortPiece``  (the bag's card-piece tab sort selector)
#:
#: A packet naming any other slot is a value the recovered client cannot
#: produce, so it is refused and nothing is stored.
BAG_SORT_SLOT_CARD = 1
BAG_SORT_SLOT_FABAO = 2
BAG_SORT_SLOT_CARD_SKILL = 3
BAG_SORT_SLOT_GUILD_BOSS_ANGER = 4
BAG_SORT_SLOT_CARD_PIECE = 5

BAG_SORT_RECOVERED_SLOTS = (
    BAG_SORT_SLOT_CARD,
    BAG_SORT_SLOT_FABAO,
    BAG_SORT_SLOT_CARD_SKILL,
    BAG_SORT_SLOT_GUILD_BOSS_ANGER,
    BAG_SORT_SLOT_CARD_PIECE,
)

#: The slots a stored row may be replayed for.  The first three have a recovered
#: *reader* in the bag panel itself -- `pkgBagUtil.getBagSortCard`/
#: `getBagSortFabao`/`getBagSortPiece`, consumed by the card/fabao/piece tab
#: views -- so replaying them restores what the player sees.  Slot 3 is read by
#: `getBagCardSkill` and slot 4 by `getGuildBossAnger`, both outside the bag, so
#: they are stored but not replayed: this build can vouch for the send, not for
#: a bag-visible consumer.
BAG_SORT_REPLAYABLE_SLOTS = frozenset(
    (BAG_SORT_SLOT_CARD, BAG_SORT_SLOT_FABAO, BAG_SORT_SLOT_CARD_PIECE)
)

#: The largest value the recovered `N` sort field can carry.
BAG_SORT_MAX_WIRE_VALUE = 0xFFFFFFFF

#: Why a `C2S 512` did not become a stored bag sort row, or why a stored row was
#: not replayed.  Closed vocabularies, so no reader has to interpret prose.
BAG_SORT_REFUSAL_SLOT_NOT_RECOVERED = "bag_sort_slot_not_recovered"
BAG_SORT_REFUSAL_VALUE_NOT_ENCODABLE = "bag_sort_value_not_encodable"
BAG_SORT_REFUSAL_UNBOUND_ROLE = "no_bound_role"
BAG_SORT_REFUSAL_ARCHIVE = "role_archive_refused"
BAG_SORT_REPLAY_REFUSAL_SLOT_NOT_REPLAYABLE = "bag_sort_slot_not_replayable"
BAG_SORT_REPLAY_REFUSAL_ROLE_MISMATCH = "role_identity_mismatch"
BAG_SORT_REPLAY_REFUSAL_VALUE = "bag_sort_value_not_encodable"
BAG_SORT_REPLAY_REFUSAL_ORIGIN = "bag_sort_origin_not_local_observation"

#: ``pkgCommonConstMgr.TB_SYNC_COUNTER_TYPE.ADVANCE_CARD`` (source line 641).
#: The client sends this value in ``C2S 21 advanceCardEnd`` field 2 and stores
#: whatever the server sends back through S2C 293/294.
ADVANCE_CARD_SYNC_COUNTER_TYPE = 110

#: Closed refusal vocabulary for the advance chain.  Every value is written to
#: the event log and (for the wire reply) only the boolean failure is visible,
#: because S2C 71 carries no reason field.
CARD_ADVANCE_REFUSAL_NO_CHAIN = "no_advance_chain"
CARD_ADVANCE_REFUSAL_NO_TARGET = "no_target"
CARD_ADVANCE_REFUSAL_UNOWNED_TARGET = "unowned_target"
CARD_ADVANCE_REFUSAL_UNMAPPED_CONFIG = "unmapped_target_config"
CARD_ADVANCE_REFUSAL_STAR = "advance_star_out_of_range"
CARD_ADVANCE_REFUSAL_LEVEL_NOT_MAX = "target_not_max_strength_level"
CARD_ADVANCE_REFUSAL_LEVEL_MAX = "advance_level_max"
CARD_ADVANCE_REFUSAL_COST_MISSING = "advance_cost_missing"
CARD_ADVANCE_REFUSAL_PIECE_COST = "unpersistable_advance_piece_cost"
CARD_ADVANCE_REFUSAL_ADV_PIECE = "insufficient_adv_piece"
#: pkgAdvanceCardConst.GOLD_ADV_PIECE / SILVER_ADV_PIECE (S2C 277 cfg ids).
ADV_PIECE_GOLD = 1000
ADV_PIECE_SILVER = 2000

#: City shop protocol constants (TASK-0444).
SHOP_STATUS_SUCCESS = 1
SHOP_STATUS_FAIL = 2
SHOP_STATUS_HANDLED = 4
SHOP_REFRESH_SUCCESS = 5
SHOP_GAIN_TYPE_CITY_SHOP = 700
SHOP_WALLET_CUT_TYPES = {7: "money", 28: "yinpo", 29: "jinpo"}
SHOP_PIECE_REWARD_TYPES = frozenset({9, 73})
SHOP_CARD_REWARD_TYPE = 3

#: cfgDailyTask rows 1..8 (TASK-0448): the shipped daily-task list.
DAILY_TASK_IDS = (1, 2, 3, 4, 5, 6, 7, 8)

#: cfgNpcTask 1001/1002 are the tutorial unlock tasks (悬赏 / 装备铸造).
#: TASK-0453: a dev session completes them on accept so the NPC's functional
#: panel becomes reachable; the completion is synthetic and dev-only.
NPC_TASK_TUTORIAL_IDS = frozenset({1001, 1002})

#: TASK-0441: every storyTalk row whose cfg type > 0 is an event trigger
#: (scene entry / chapter object).  A dev session marks them STATE_FINISH at
#: login so the 长安城 entry does not auto-play the dialogue chain again.
DEV_STORY_SKIP_IDS = (
    100101,
    100201,
    100701,
    101401,
    101501,
    101601,
    200601,
    200701,
    200801,
    201001,
    201101,
    201301,
    201501,
    210501,
    300201,
    300301,
    300401,
    300501,
    300601,
    300701,
    300801,
    300901,
    301001,
    500101,
    500201,
    500301,
    500401,
    500501,
    600101,
    600201,
    600301,
    600401,
    700101,
    700201,
    700301,
    700401,
    700501,
    700601,
    800101,
    800201,
    800301,
    800401,
    800501,
    800607,
    800701,
    800801,
    900101,
    900201,
    900301,
    900401,
    900501,
    900601,
    900701,
    900801,
    900901,
    901001,
    901101,
    901201,
    901301,
    901401,
    910101,
    910201,
    910301,
    910401,
    910501,
    910601,
    1100101,
    1100201,
    1100301,
    1100401,
    1100501,
    1100601,
    1100701,
    1100801,
    1200101,
    1200201,
    1200302,
    1200401,
    1200501,
    1200601,
    1200801,
    1200901,
    1201001,
    1300101,
    1300201,
    1300301,
    1300401,
    1300501,
    1300601,
    1400101,
    1400201,
    1400301,
    1400401,
    1400501,
    1400601,
    1400701,
    1400801,
    1400901,
    1401001,
    1401101,
    9900001,
    9900101,
    9900201,
    9900301,
    9900403,
    9900501,
    9900601,
    9900701,
    9900801,
    9900901,
    9901001,
    9901101,
    9901201,
    9901301,
    9910001,
    9910101,
    9910201,
    9910301,
    9910401,
    9910501,
    9910601,
    9910701,
    9910801,
    9910901,
    9920001,
    9920101,
    9920301,
    9920401,
    9920501,
    9920601,
    9920701,
    9920801,
    9920901,
    9921001,
    9921101,
    9930101,
    9930201,
    9930301,
    9930401,
    9930501,
    9930601,
)

#: TASK-0450: only the chapter-entry talks (storyTalk type 6).  Skipping these
#: suppresses the scene-entry dialogue while leaving the chapter task chain
#: intact (the 141-id variant also removed the main-quest tracker).
DEV_STORY_CHAPTER_IDS = (100101, 600101, 700101, 900101, 1100601, 9900001)
CARD_ADVANCE_REFUSAL_ADV_CARD_SLOT = "unpersisted_advance_card_slot"
CARD_ADVANCE_REFUSAL_SPECIAL_CARD_STAR = "special_card_star_mismatch"
CARD_ADVANCE_REFUSAL_SPECIAL_CARD_UNITS = "insufficient_special_card_units"

#: `specialCard` 100001/100002/100003 are the 妖魂拓片 rows.  The shipped
#: description says "集齐30片后可替代任意一个X星妖灵用于进化", so one selection of
#: the matching-star rubbing stack substitutes one X-star fodder card and costs
#: 30 units.  The number of C2S 20 packets therefore equals the number of
#: substituted cards.
ADVANCE_SPECIAL_CARD_STARS = {100001: 3, 100002: 4, 100003: 5}
ADVANCE_SPECIAL_CARD_UNITS = 30
CARD_ADVANCE_REFUSAL_MATERIAL_COUNT = "material_count_mismatch"
CARD_ADVANCE_REFUSAL_MATERIAL_UNOWNED = "unowned_material"
CARD_ADVANCE_REFUSAL_MATERIAL_SELF = "target_as_material"
CARD_ADVANCE_REFUSAL_MATERIAL_WRONG_CARD = "material_not_same_card"
CARD_ADVANCE_REFUSAL_MATERIAL_PROTECTED = "material_protected"
CARD_ADVANCE_REFUSAL_MONEY = "insufficient_money"
CARD_ADVANCE_REFUSAL_YINPO = "insufficient_yinpo"
CARD_ADVANCE_REFUSAL_JINPO = "insufficient_jinpo"
CARD_ADVANCE_REFUSAL_COUNTER_STALE = "sync_counter_stale"
CARD_ADVANCE_REFUSAL_COUNTER_AHEAD = "sync_counter_ahead"
CARD_ADVANCE_REFUSAL_STORE = "store_refused"

# ------------------------------------------------------ goodsUse (TASK-0266)
#
# C2S 429 goodsUse is the bag panel's 使用 request (the client's own
# registerC2S("NNN", "goodsUse"), pkgScProtocolMgr.lua:1057); S2C 820 is its
# ONLY unlock (line 1058 hands "goodsUse" to pkgLockSendMgr.regLockProto, so the
# client armed LOCK_TIME = 15000 ms).  EVERY request must therefore get EXACTLY
# ONE S2C 820 -- success or refusal -- or the client shuts its own socket down.
#
# The shipped cfgGoods row this slice serves is only the one the TASK-0265
# contract transcribes: item 376 花呗祈灵符, use == 1, arg1 == 376,
# autoAddBag == 1, whose cfgGoodsReward 376 row grants (252 LOTTERY_TICKET, 1).
# The runtime does not open the APK: the row is transcribed here from
# reports/task_0265_bag_goods_use_contract.json minimal_slice.item, and the
# S2C 27 slots come from reports/role_value_wire_mapping.csv:20,159.  Every
# other dType/dCfgId/dArg is refused (U-1/U-4/U-5/U-7 stay unresolved).
GOODS_USE_PROTOCOL_ID = 429
GOODS_USE_REPLY_PROTOCOL_ID = 820
GOODS_USE_ITEM_DTYPE = 376
GOODS_USE_ITEM_CFG_ID = 0
GOODS_USE_ITEM_ARG = 0
GOODS_USE_ITEM_VALUE_NAME = ROLE_VALUE_GOODS_USE
GOODS_USE_ITEM_VALUE_INDEX = 157
GOODS_USE_ITEM_VALUE_WIRE_POSITION = 158
GOODS_USE_ITEM_REWARD_TYPE = 376
GOODS_USE_REWARD_VALUE_NAME = ROLE_VALUE_LOTTERY_TICKET
GOODS_USE_REWARD_VALUE_INDEX = LOTTERY_TICKET_VALUE_INDEX
GOODS_USE_REWARD_VALUE_WIRE_POSITION = LOTTERY_TICKET_WIRE_POSITION
GOODS_USE_REWARD_TYPE = LOTTERY_TICKET_REWARD_TYPE
GOODS_USE_REWARD_AMOUNT = 1
#: TB_GAIN_TYPE.GOODS_USE from zys_scAll/const/pkgCommonConstMgr.
GOODS_USE_GAIN_TYPE = 640
GOODS_USE_SUCCESS_DCODE = 1
GOODS_USE_REFUSAL_DCODE = 2
GOODS_USE_REPLY_DARG = 1
GOODS_USE_LOCK_TIMEOUT_MS = 15000
#: The contract unknowns every goodsUse event names (U-1 grant column, U-2 dArg
#: meaning, U-3 dCurNum meaning).  They are carried, never resolved, here.
GOODS_USE_UNKNOWN_IDS = ("U-1", "U-2", "U-3")

GOODS_USE_REFUSAL_UNBOUND_ROLE = "no_bound_role"
GOODS_USE_REFUSAL_UNSUPPORTED_ITEM = "unsupported_goods_type"
GOODS_USE_REFUSAL_CFG_ID = "unsupported_cfg_id"
GOODS_USE_REFUSAL_ARG = "unsupported_arg"
GOODS_USE_REFUSAL_STORE = "role_store_refused"

#: The S2C 27 slots the login replay fills from the persisted goods-use counts.
#: They are patched only when the corresponding meta key exists, so a fresh
#: archive's frozen all-zero vector is byte-identical.
GOODS_USE_ROLE_VALUE_WIRE_INDICES: dict[str, int] = {
    ROLE_VALUE_GOODS_USE: GOODS_USE_ITEM_VALUE_INDEX,
    ROLE_VALUE_LOTTERY_TICKET: GOODS_USE_REWARD_VALUE_INDEX,
}
GOODS_USE_ROLE_VALUE_WIRE_POSITIONS: dict[str, int] = {
    ROLE_VALUE_GOODS_USE: GOODS_USE_ITEM_VALUE_WIRE_POSITION,
    ROLE_VALUE_LOTTERY_TICKET: GOODS_USE_REWARD_VALUE_WIRE_POSITION,
}


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _logged_fields(value: object) -> object:
    """A tuple/list/dict of decoded wire values, spelled for the event log.

    `eventlog.emit` writes every event as one `json.dumps` line, so a `bytes`
    value in a record is not a cosmetic problem: the `TypeError` it raises
    leaves the handler and closes the connection under it.  RUN-0044 lost the
    first live chapter-map battle start exactly that way -- the S2C 89 reply's
    `s1` echoes the `a`-typed `C2S 69` field, the codec decodes that as `bytes`
    (`b""` there), and `tcp_packet_sent` logged it as it was
    (`.autopilot/device_runs/RUN-0044/server.log:540`).  A byte string is
    therefore spelled as its text -- the same spelling the receive path and
    `tcp_battle_start_replied` already use -- and the container type is kept, so
    no reader sees a shape it did not see before.
    """

    if isinstance(value, dict):
        return {
            name: _text(item) if isinstance(item, bytes) else item
            for name, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(
            _text(item) if isinstance(item, bytes) else item for item in value
        )
    return [_text(item) if isinstance(item, bytes) else item for item in value]


def readable_login_fields(fields: tuple[Any, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in zip(LOGIN_FIELD_NAMES, fields, strict=True):
        if name in SENSITIVE_LOGIN_FIELDS:
            result[name] = mask_secret(value)
        elif isinstance(value, bytes):
            result[name] = _text(value)
        else:
            result[name] = value
    return result


def login_display_username(fields: tuple[Any, ...]) -> str:
    """The S2C10 ``strUserName`` the recovered client is shown.

    Unchanged from the create path: the SDK username, falling back to the SDK
    user id, because the handler only requires a non-empty string.
    """

    username = _text(fields[9])
    return username or _text(fields[1])


def login_identity_inputs(fields: tuple[Any, ...]) -> dict[str, Any]:
    """The ``loginByToken`` inputs the durable account key is derived from.

    Exactly five of the eleven wire fields: the selected server, the
    platform/channel triple that scopes one SDK account, and the SDK user id.
    ``sdk_server_time`` moves with the clock and ``sdk_verify_md5`` is a
    signature over it, so neither is stable enough to name a role;
    ``device_id`` changes on reinstall, so it may not either.
    """

    return {
        "server_id": fields[0],
        "platform": _text(fields[7]),
        "channel_id": _text(fields[5]),
        "channel_sub_id": _text(fields[6]),
        "sdk_identity": _text(fields[1]) or _text(fields[9]),
    }



# TASK-0337: S2C 89 tuple indices the client resolves as its own tbParams.p5/p8/p9.
BATTLE_START_P1_INDEX = 7    # position 8  (tbParams.p1)
BATTLE_START_P2_INDEX = 8    # position 9  (tbParams.p2)
BATTLE_START_P5_INDEX = 11   # position 12
BATTLE_START_P8_INDEX = 14   # position 15
BATTLE_START_P9_INDEX = 15   # position 16

class RevivalTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address,
        event_logger: EventLogger,
        *,
        expected_version: str = CURRENT_VERSION,
        c2s_registry: ProtocolRegistry = C2S_REGISTRY,
        s2c_registry: ProtocolRegistry = S2C_REGISTRY,
        compatibility_profile: CompatibilityProfile | None = None,
        time_source: Callable[[], float] | None = None,
        role_store: RoleStore | None = None,
        protocol_policy: str = PROTOCOL_POLICY_STRICT,
        test_grant: OwnerDrawTestGrant | None = None,
        growth_grant: OwnerGrowthTestGrant | None = None,
        achieve_level_grant: int | None = None,
        draw_odds: str = DRAW_ODDS_DETERMINISTIC_FIXTURE,
        draw_rng=None,
        dev_play_babel: bool = False,
        dev_play_artifact: bool = False,
        dev_play_resources: bool = False,
        dev_play_gacha: bool = False,
    ) -> None:
        if achieve_level_grant is not None:
            if not isinstance(achieve_level_grant, int) or not 0 <= achieve_level_grant <= 9:
                raise ValueError(
                    f"achieve level grant must be an int in 0..9, "
                    f"got {achieve_level_grant!r}"
                )
        if protocol_policy not in PROTOCOL_POLICIES:
            raise ValueError(f"unknown protocol policy {protocol_policy!r}")
        if draw_odds not in DRAW_ODDS_MODES:
            raise ValueError(f"unknown draw odds mode {draw_odds!r}")
        self.draw_odds = draw_odds
        self.draw_rng = draw_rng if draw_rng is not None else SystemRandom()
        self.event_logger = event_logger
        self.expected_version = expected_version
        self.c2s_registry = c2s_registry
        # The implemented registry is what the business handlers below know.
        # `decode_c2s_registry` is the only object the reader uses: in strict
        # mode it IS the implemented registry (D-08 unchanged); in free play it
        # adds the read-only client catalog so a known-but-unimplemented frame
        # has a trustworthy packet boundary instead of killing the socket.
        self.protocol_policy = protocol_policy
        self.implemented_c2s_ids = frozenset(
            spec.protocol_id for spec in c2s_registry
        )
        self.decode_c2s_registry = (
            CatalogRegistry(c2s_registry)
            if protocol_policy == PROTOCOL_POLICY_FREE_PLAY_TOLERANT
            else c2s_registry
        )
        self.s2c_registry = s2c_registry
        self.compatibility_profile = compatibility_profile or get_profile()
        # Narrow injectable clock: production uses real time; tests and the wire
        # audit inject a fixed source so the S2C 0 values stay deterministic.
        self._time_source = time_source or time.time
        self.server_started_unix = int(self._time_source())
        self._connection_ids = itertools.count(1)
        self._id_lock = threading.Lock()
        # Battle-start replies (profile v11).  `sfStartTime` carries no battle
        # fact: the client only compares it with the reply it received LAST, and
        # drops the packet when that, `dRawRandomSeed` and `pveOrPvpType` all
        # match.  The other two are fixed by the policy, so this counter has to
        # differ per reply.  It starts at the process start second, so a
        # restarted server does not repeat a previous session's numbering.
        self._battle_start_replies = 0
        # A caller that supplies a store owns its lifetime; a server that had to
        # build one gets the non-persistent default and closes it again.  The
        # long-lived archive is chosen by the command line (app.py), never by a
        # library call that may be a test, the wire audit or a smoke run.
        self._owns_role_store = role_store is None
        self.role_store = role_store or RoleStore.in_memory(
            time_source=self._time_source
        )
        # The Owner draw-test overlay is opt-in, profile-scoped and explicit.
        # A server started without `--test-grant`, or one serving any profile
        # other than qa-changan-main, keeps the frozen v15 zero vector and never
        # replays fixture cards.  Device Acceptance therefore cannot inherit it.
        try:
            profile_fields = self.role_store.describe() or {}
        except Exception:  # noqa: BLE001 - an unreadable profile is simply not ours
            profile_fields = {}
        self._owner_draw_profile_id = profile_fields.get("play_profile_id")
        # TASK-0232: the dev-play babel login state is opt-in on THREE
        # independent conditions -- the explicit flag, this archive's own
        # profile id, and the overlay meta value (read per login).  The profile
        # check is fixed here; the meta is re-read on every login so a restart
        # and a relogin apply exactly what the archive stores.
        self.dev_play_babel = bool(dev_play_babel)
        self.dev_play_babel_profile_matches = (
            self._owner_draw_profile_id == DEV_PLAY_BABEL_PROFILE_ID
        )
        # TASK-0258: the dev-play artifact overlay uses the same three-condition
        # gate: the explicit flag, this archive's own profile id and the stored
        # meta value (re-read on every login).  The permanent QA archive can
        # never match, so a qa-changan-main session records a C2S 744 and
        # answers nothing.
        self.dev_play_artifact = bool(dev_play_artifact)
        self.dev_play_artifact_profile_matches = (
            self._owner_draw_profile_id == DEV_PLAY_ARTIFACT_PROFILE_ID
        )
        # TASK-0233: the resources overlay uses the same three-condition gate.
        # The profile half is fixed at construction; the meta is re-read on
        # every login, so a restart or relogin applies exactly what the archive
        # stores.  The permanent qa-changan-main archive can never match.
        self.dev_play_resources = bool(dev_play_resources)
        self.dev_play_resources_profile_matches = (
            self._owner_draw_profile_id == DEV_PLAY_RESOURCES_PROFILE_ID
        )
        # TASK-0431: the gacha sandbox.  It reuses the Owner draw-test overlay
        # (tickets + deterministic fixture + hot-spot windows) but is restricted
        # to the three disposable dev profiles; the permanent qa-changan-main
        # archive can never match.  Crystal is announced in S2C 10 only.
        self.dev_play_gacha = bool(dev_play_gacha)
        self.dev_play_gacha_profile_matches = self._owner_draw_profile_id in (
            DEV_PLAY_BABEL_PROFILE_ID,
            DEV_PLAY_ARTIFACT_PROFILE_ID,
            DEV_PLAY_RESOURCES_PROFILE_ID,
        )
        gacha_grant_allowed = (
            self._owner_draw_profile_id == OWNER_DRAW_TEST_PROFILE_ID
            or (self.dev_play_gacha and self.dev_play_gacha_profile_matches)
        )
        self.owner_draw_test_grant = (
            test_grant
            if (test_grant is not None and gacha_grant_allowed)
            else None
        )
        self.owner_growth_test_grant = (
            growth_grant
            if (
                growth_grant is not None
                and self._owner_draw_profile_id == OWNER_DRAW_TEST_PROFILE_ID
            )
            else None
        )
        # The achieve-rank overlay is the same shape: opt-in, profile-scoped and
        # never persisted.  The save has no recovered achieve producer, so a
        # login without this flag sends no achieve packets at all.
        self.achieve_level_test_grant = (
            achieve_level_grant
            if (
                achieve_level_grant is not None
                and self._owner_draw_profile_id == OWNER_DRAW_TEST_PROFILE_ID
            )
            else None
        )
        # Continue after the highest serial already persisted.  The draw ids
        # are ``revival-draw-NNNN-NN``; restarting at 1 made a later session
        # silently overwrite the previous session's card rows (observed
        # 2026-10-18: a 10-pull re-used ``revival-draw-0001-*``).
        self._draw_fixture_serial = self.role_store.max_draw_instance_serial()
        super().__init__(address, RevivalTCPHandler)
        self.event_logger.emit(
            "compatibility_profile_loaded",
            profile_id=self.compatibility_profile.profile_id,
            target_client=self.compatibility_profile.metadata.target_client,
            compatibility_goal=self.compatibility_profile.metadata.purpose,
            status=self.compatibility_profile.metadata.status,
            role_value_field_count=len(self.compatibility_profile.role_values),
            role_value_class_counts=self.compatibility_profile.class_counts(),
            role_value_vector_hash=self.compatibility_profile.vector_hash(),
        )
        if self.protocol_policy != PROTOCOL_POLICY_STRICT:
            # strict is the D-08 default and must keep its exact startup event
            # stream; only the opt-in free-play mode announces itself.
            self.event_logger.emit(
                "protocol_policy_selected",
                protocol_policy=self.protocol_policy,
                implemented_c2s_count=len(self.implemented_c2s_ids),
                catalog_c2s_count=len(KNOWN_C2S),
                strict_handshakes=sorted(FREE_PLAY_STRICT_HANDSHAKES),
            )
        if test_grant is not None:
            active = self.owner_draw_test_grant is not None
            self.event_logger.emit(
                "synthetic_test_grant_enabled" if active
                else "synthetic_test_grant_refused",
                profile_id=self._owner_draw_profile_id,
                provenance=OWNER_DRAW_TEST_ORIGIN,
                lottery_ticket=test_grant.lottery_ticket,
                lottery_ticket_advanced=test_grant.lottery_ticket_advanced,
                wire_positions=[
                    LOTTERY_TICKET_WIRE_POSITION,
                    LOTTERY_TICKET_ADVANCED_WIRE_POSITION,
                ],
                persisted=False,
                device_acceptance_baseline=False,
                fixture_id=test_grant.fixture_id,
                reason=(
                    None if active
                    else "test grant is restricted to qa-changan-main"
                ),
            )
        if achieve_level_grant is not None:
            active = self.achieve_level_test_grant is not None
            self.event_logger.emit(
                "achieve_level_test_grant_enabled" if active
                else "achieve_level_test_grant_refused",
                profile_id=self._owner_draw_profile_id,
                provenance=OWNER_DRAW_TEST_ORIGIN,
                achieve_level=achieve_level_grant,
                packet_ids=[585, 586, 589],
                persisted=False,
                device_acceptance_baseline=False,
                reason=(
                    None
                    if active
                    else "achieve level grant is restricted to qa-changan-main"
                ),
            )
        if self.dev_play_babel:
            # Named startup statement, exactly like the Owner test grants: a
            # flag that cannot take effect is never silently ignored.
            active = self.dev_play_babel_profile_matches
            self.event_logger.emit(
                "dev_play_babel_overlay_enabled" if active
                else "dev_play_babel_overlay_refused",
                profile_id=self._owner_draw_profile_id,
                required_profile=DEV_PLAY_BABEL_PROFILE_ID,
                overlay_meta_key=DEV_PLAY_BABEL_META_KEY,
                provenance=DEV_PLAY_BABEL_ORIGIN,
                replaceable=True,
                historical=False,
                persisted=False,
                device_acceptance_baseline=False,
                reason=(
                    None
                    if active
                    else "dev-play overlay is restricted to dev-babel-main"
                ),
            )
        if self.dev_play_artifact:
            # Same named startup statement as every other explicit overlay: a
            # flag that cannot take effect is never silently ignored.
            active = self.dev_play_artifact_profile_matches
            self.event_logger.emit(
                "dev_play_artifact_overlay_enabled" if active
                else "dev_play_artifact_overlay_refused",
                profile_id=self._owner_draw_profile_id,
                required_profile=DEV_PLAY_ARTIFACT_PROFILE_ID,
                overlay_meta_key=DEV_PLAY_ARTIFACT_META_KEY,
                provenance=DEV_PLAY_ARTIFACT_ORIGIN,
                replaceable=True,
                historical=False,
                persisted=False,
                device_acceptance_baseline=False,
                reason=(
                    None
                    if active
                    else "dev-play overlay is restricted to dev-artifact-main"
                ),
            )
        if self.dev_play_resources:
            # Same named startup statement as every other explicit overlay: a
            # flag that cannot take effect is never silently ignored.
            active = self.dev_play_resources_profile_matches
            self.event_logger.emit(
                "dev_play_resources_overlay_enabled" if active
                else "dev_play_resources_overlay_refused",
                profile_id=self._owner_draw_profile_id,
                required_profile=DEV_PLAY_RESOURCES_PROFILE_ID,
                overlay_meta_key=DEV_PLAY_RESOURCES_META_KEY,
                provenance=DEV_PLAY_RESOURCES_ORIGIN,
                replaceable=True,
                historical=False,
                persisted=False,
                device_acceptance_baseline=False,
                reason=(
                    None
                    if active
                    else "dev-play overlay is restricted to dev-resource-main"
                ),
            )
        if self.dev_play_gacha:
            active = self.dev_play_gacha_profile_matches
            self.event_logger.emit(
                "dev_play_gacha_overlay_enabled" if active
                else "dev_play_gacha_overlay_refused",
                profile_id=self._owner_draw_profile_id,
                allowed_profiles=[
                    DEV_PLAY_BABEL_PROFILE_ID,
                    DEV_PLAY_ARTIFACT_PROFILE_ID,
                    DEV_PLAY_RESOURCES_PROFILE_ID,
                ],
                crystal_announced=999999,
                limited_banners=list(HOT_SPOT_LIMITED_BANNERS),
                shown_tab_slots=HOT_SPOT_CLIENT_TAB_SLOTS,
                provenance="synthetic_compatibility",
                replaceable=True,
                historical=False,
                persisted=False,
                device_acceptance_baseline=False,
                reason=(
                    None
                    if active
                    else "dev-play gacha is restricted to the disposable dev profiles"
                ),
            )
        # One durable-identity statement per process start: what the archive is,
        # where it is, and the honesty boundary that the derived account key is
        # a local simulator identity match and NOT proof of a real player's
        # authenticated SDK identity (loopback only, no token is verified).
        self.event_logger.emit(
            "role_store_ready",
            account_identity_policy="salted_local_derivation",
            account_identity_is_authenticated_player_identity=False,
            sdk_token_verified=False,
            **self.role_store.describe(),
        )

    def next_connection_id(self) -> str:
        with self._id_lock:
            return f"conn-{next(self._connection_ids):06d}"

    def next_draw_fixture_serial(self) -> int:
        """One process-wide serial per deterministic draw request."""

        with self._id_lock:
            self._draw_fixture_serial += 1
            return self._draw_fixture_serial

    def next_battle_start_time(self) -> int:
        """The per-reply counter of THIS battle-start reply: non-zero, unlike the last.

        Strictly increasing within the process and seeded from the process start
        second, so two replies in the same second still differ and a restarted
        server does not hand out the numbering of the process before it.  Since
        TASK-0182 / profile v17 the same value fills S2C 89 position 1
        (`sfStartTime`) and position 7 (`dSyncCount`): the client compares the
        former only with its previous reply (see `pkgBattlePveFromSg` lines 5-8)
        and the latter only with the count it stored from the previous reply
        (`pkgBattleMgr.startPveBattle` lines 179-182).  Neither is a battle clock.
        """

        with self._id_lock:
            self._battle_start_replies += 1
            return self.server_started_unix + self._battle_start_replies

    def now_unix(self) -> int:
        return int(self._time_source())

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            if self._owns_role_store:
                self.role_store.close()


class RevivalTCPHandler(socketserver.BaseRequestHandler):
    server: RevivalTCPServer
    request: socket.socket

    def _send_s2c(
        self,
        connection_id: str,
        writer: WriterState,
        protocol_name: str,
        fields: tuple[object, ...],
        *,
        decoded_fields: object | None = None,
        classification: str = "ok",
        record_fields: dict[str, Any] | None = None,
    ):
        return self._send_s2c_spec(
            connection_id,
            writer,
            self.server.s2c_registry.by_name(protocol_name),
            fields,
            decoded_fields=decoded_fields,
            classification=classification,
            record_fields=record_fields,
        )

    def _send_s2c_spec(
        self,
        connection_id: str,
        writer: WriterState,
        spec: ProtocolSpec,
        fields: tuple[object, ...],
        *,
        decoded_fields: object | None = None,
        classification: str = "ok",
        record_fields: dict[str, Any] | None = None,
    ):
        response = encode_packet(spec, fields, writer)
        self.request.sendall(response.wire)
        self._emit_packet(
            "tcp_packet_sent",
            connection_id,
            response,
            response_protocol=spec.name,
            decoded_fields=_logged_fields(
                fields if decoded_fields is None else decoded_fields
            ),
            classification=classification,
            **(record_fields or {}),
        )
        return response

    def _handle_free_play_unsupported(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record one known-but-unimplemented request and keep the socket.

        The packet was decoded through the read-only client catalog.  This
        method never calls a business handler, never writes the role store and
        never invents a reply.  If ``FREE_PLAY_SAFE_REPLIES`` has an
        evidence-backed safe no-op for this id, it is sent so the client's own
        lock-send timer does not close the connection; otherwise the packet is
        left unanswered and the client's own timer decides.
        """
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None and getattr(role, "compatibility", None) is not None:
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        safe = FREE_PLAY_SAFE_REPLIES.get(packet.spec.protocol_id)
        response_sent = False
        response_name = ""
        response_fields: tuple = ()
        if safe is not None:
            response = self._send_s2c_spec(
                connection_id,
                writer,
                safe.response_spec,
                safe.response_fields,
                decoded_fields=safe.response_fields,
                classification="free_play_safe_noop",
                record_fields={
                    "request_protocol_id": packet.spec.protocol_id,
                    "request_protocol_name": packet.spec.name,
                    "safe_reply_rationale": safe.rationale,
                    "historical_values_recovered": False,
                    "row_origin": "synthetic_compatibility",
                    **extra,
                },
            )
            response_sent = True
            response_name = response.spec.name
            response_fields = response.fields
        self.server.event_logger.emit(
            "tcp_free_play_unsupported_known",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            protocol_fmt=packet.spec.fmt,
            sequence=packet.sequence,
            classification="known_unimplemented",
            fallback_action=(
                "safe_noop_response" if response_sent else "no_response"
            ),
            response_sent=response_sent,
            response_protocol_name=response_name,
            response_fields=list(response_fields),
            state_changed=False,
            connection_kept_alive=True,
            lock_partner=list(LOCK_PARTNERS.get(packet.spec.name, ())),
            request_fields=[
                _text(value) if isinstance(value, bytes) else value
                for value in packet.fields
            ],
            **extra,
        )

    def _owner_draw_role_value_vector(
        self, connection_id, role, compatibility, session_state
    ):
        fields = self._owner_draw_role_value_vector_raw(
            connection_id, role, compatibility, session_state
        )
        if (
            self.server.dev_play_babel
            or self.server.dev_play_artifact
            or self.server.dev_play_resources
            or self.server.dev_play_gacha
        ):
            # TASK-0466: the Owner asked for every growth resource to be
            # plentiful on the dev profiles.  The vector is the client's only
            # source for currencies/materials, so floor every slot; nothing is
            # persisted and a relogin re-applies the same floor.
            fields = tuple(
                99999 if int(value) < 99999 else int(value)
                for value in fields
            )
            self.server.event_logger.emit(
                "dev_play_growth_resource_floor_applied",
                connection_id=connection_id,
                role_id=int(role.role_id),
                field_count=len(fields),
                floor=99999,
            )
        return fields

    def _owner_draw_role_value_vector_raw(
        self, connection_id, role, compatibility, session_state
    ):
        """The S2C 27 vector, with persisted wallet values and the Owner grant.

        The frozen profile stays all-zero.  Two in-memory overlays may change
        it: the wallet rows this archive persisted (money/银魄/金魄, written by
        the client's own card split) and, only while the explicit Owner
        draw-test overlay is active, the two lottery-ticket slots.  Each change
        emits an event naming its wire positions, so neither can be mistaken
        for a recovered historical role value.
        """

        account_key = session_state.get("account_key")
        resources_overlay = session_state.get("dev_play_resources_overlay")
        if resources_overlay is not None:
            # The persisted wallet is provisioned *before* this vector is built,
            # so the client's first S2C 27 already carries the dev-play amounts
            # and every later login reads the rows the client itself spent.
            self._provision_dev_play_resources_wallet(
                connection_id, session_state, resources_overlay
            )
        gacha_overlay = (
            self.server.dev_play_gacha
            and self.server.dev_play_gacha_profile_matches
        )
        if gacha_overlay and account_key is not None:
            record = session_state.get("role_record")
            if record is not None:
                held = self.server.role_store.role_wallet_values(
                    account_key=account_key
                )
                for name, target in (("yinpo", 50000), ("jinpo", 50000)):
                    delta = target - int(held.get(name, 0))
                    if delta > 0:
                        try:
                            self.server.role_store.adjust_role_wallet(
                                record=record,
                                value_name=name,
                                delta=delta,
                                provenance="synthetic_dev_play_gacha_resource",
                            )
                        except Exception:
                            pass
        wallet = (
            self.server.role_store.role_wallet_values(account_key=account_key)
            if account_key is not None
            else {}
        )
        # TASK-0398: the tower's two 神器 tokens are generic role values (structure 13), not
        # wallet rows, so the login vector has to carry them or the shop reads 0 after a relogin.
        # pkgValueDef puts artifactDgnPoint at one-based 250 and artifactDgnExt2 at 252.
        if account_key is not None:
            try:
                stored = {
                    name: int(amount)
                    for name, amount in (
                        self.server.role_store._conn.execute(
                            "SELECT value_name, amount FROM role_values "
                            "WHERE account_key = ? AND value_name IN "
                            "('artifactDgnPoint', 'artifactDgnExt2')",
                            (account_key,),
                        ).fetchall()
                    )
                }
            except Exception:
                stored = {}
            for value_name, index in (("artifactDgnPoint", 249),
                                      ("artifactDgnExt2", 251)):
                if value_name in stored and index < len(compatibility.role_values):
                    compatibility.role_values[index].value = stored[value_name]
        if wallet:
            for name, (index, reward_type, _packet) in WALLET_WIRE_VALUES.items():
                if name not in wallet:
                    continue
                compatibility.role_values[index].value = coerce_wallet_value(
                    name, wallet[name]
                )
            if resources_overlay is not None:
                # TASK-0233: these rows are the explicit dev-play supply, not a
                # recovered historical balance.  The event says so per value.
                self.server.event_logger.emit(
                    "tcp_wallet_overlay_applied",
                    connection_id=connection_id,
                    remote_endpoint=(
                        f"{self.client_address[0]}:{self.client_address[1]}"
                    ),
                    role_id=role.role_id,
                    profile_id=compatibility.profile.profile_id,
                    play_profile_id=DEV_PLAY_RESOURCES_PROFILE_ID,
                    wallet=wallet,
                    values=[
                        {
                            "value_name": name,
                            "index": index + 1,
                            "amount": coerce_wallet_value(name, wallet[name]),
                        }
                        for name, (index, _t, _p) in WALLET_WIRE_VALUES.items()
                        if name in wallet
                    ],
                    wire_positions=[
                        index + 1
                        for name, (index, _t, _p) in WALLET_WIRE_VALUES.items()
                        if name in wallet
                    ],
                    reward_types=[
                        reward_type
                        for name, (_i, reward_type, _p) in WALLET_WIRE_VALUES.items()
                        if name in wallet
                    ],
                    value_origin=DEV_PLAY_RESOURCES_VALUE_ORIGIN,
                    provenance=DEV_PLAY_RESOURCES_ORIGIN,
                    historical_values_recovered=False,
                    replaceable=True,
                    historical=False,
                )
            else:
                self.server.event_logger.emit(
                    "tcp_wallet_overlay_applied",
                    connection_id=connection_id,
                    remote_endpoint=(
                        f"{self.client_address[0]}:{self.client_address[1]}"
                    ),
                    role_id=role.role_id,
                    profile_id=compatibility.profile.profile_id,
                    wallet=wallet,
                    wire_positions=[
                        index + 1
                        for name, (index, _t, _p) in WALLET_WIRE_VALUES.items()
                        if name in wallet
                    ],
                    reward_types=[
                        reward_type
                        for name, (_i, reward_type, _p) in WALLET_WIRE_VALUES.items()
                        if name in wallet
                    ],
                    value_origin="persisted_role_wallet",
                    historical_values_recovered=True,
                )
        # TASK-0321: the babel dev-play archive seeds ZHUXIANLING (role value 1-based index 24,
        # 0-based 23) BEFORE the S2C 27 vector is encoded, so the tower's 跳过战斗 button gets its
        # listener. RUN22 measured 诛仙令 0/2 and a grey, unclickable skip on a clean archive.
        if (self.server.dev_play_babel
                and self.server.dev_play_babel_profile_matches
                and self.server.role_store.get_meta(DEV_PLAY_BABEL_META_KEY) is not None):
            compatibility.role_values[23].value = dev_play_babel.zhuxianling(
                self.server.role_store)
            # TASK-0323: the uncharted entry spends reward 250 (the 秘境钥匙) x1 plus stamina, and
            # RUN24 showed unchartedKey at role-value slot 63 = 0, so 进入 returned silently.
            compatibility.role_values[63].value = 3
            self.server.event_logger.emit(
                "dev_play_babel_zhuxianling_seeded",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                role_id=role.role_id,
                wire_index_0_based=23,
                value=compatibility.role_values[23].value,
                reason="skip button needs ZHUXIANLING >= 2 (RUN22)",
                replaceable=True,
                historical=False,
                persisted=False,
            )
        # TASK-0266: the two counts a C2S 429 goodsUse moved.  They ride the
        # archive's meta table (role_wallet's frozen v9 CHECK admits
        # money/yinpo/jinpo only) and each slot is patched ONLY when its key
        # exists, so a fresh archive's S2C 27 vector -- and every frozen login
        # digest built from it -- stays byte-identical.  The values are
        # absolute; pkgCgValueMgr.setAllValue fills roleInfo.v, the only thing
        # the bag cell reads.  This block deliberately runs BEFORE the two
        # explicit dev-play overlays below, so an owner-draw grant or the
        # dev-resource ticket ledger keeps owning its slots in those sessions;
        # a normal login has neither and therefore shows the persisted counts.
        goods_use_counts = (
            self.server.role_store.goods_use_counts(account_key=account_key)
            if account_key is not None
            else {}
        )
        if goods_use_counts:
            for name, index in GOODS_USE_ROLE_VALUE_WIRE_INDICES.items():
                if name in goods_use_counts:
                    compatibility.role_values[index].value = int(
                        goods_use_counts[name]
                    )
            self.server.event_logger.emit(
                "tcp_goods_use_balance_overlay_applied",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                role_id=role.role_id,
                profile_id=compatibility.profile.profile_id,
                values=[
                    {
                        "value_name": name,
                        "wire_index_0_based": index,
                        "wire_position": GOODS_USE_ROLE_VALUE_WIRE_POSITIONS[name],
                        "amount": int(goods_use_counts[name]),
                    }
                    for name, index in GOODS_USE_ROLE_VALUE_WIRE_INDICES.items()
                    if name in goods_use_counts
                ],
                value_origin="persisted_meta_goods_use_count",
                provenance=GOODS_USE_ORIGIN_LOCAL_C2S,
                meta_key_prefix=GOODS_USE_META_PREFIX,
                historical_values_recovered=True,
                replaceable=False,
            )
        grant = self.server.owner_draw_test_grant
        if grant is None and resources_overlay is None:
            return compatibility.role_value_vector()
        if resources_overlay is not None:
            # TASK-0235: the two ticket slots replay the archive's persisted
            # ledger (seeded 30/30 on the first login, then whatever the
            # client's own C2S 274/33 draws left).  A missing ledger is seeded
            # by `_provision_dev_play_ticket_balance` before this vector is
            # built; reaching here without it is a programming error, not a
            # reason to send the constant.
            balance = session_state.get("dev_play_ticket_balance")
            if balance is None:
                balance = self._provision_dev_play_ticket_balance(
                    connection_id, session_state, resources_overlay
                )
            if balance is None:
                raise RoleStoreError(
                    "dev-play resources ticket ledger is unusable; refusing to "
                    "announce a ticket balance"
                )
            if not session_state.get(
                "dev_play_resources_ticket_overlay_applied"
            ):
                for item in resources_overlay.ticket_values:
                    compatibility.role_values[item["value_index"]].value = int(
                        balance[item["value_name"]]
                    )
                session_state["dev_play_resources_ticket_overlay_applied"] = True
                self.server.event_logger.emit(
                    "tcp_dev_play_resources_ticket_overlay_applied",
                    connection_id=connection_id,
                    remote_endpoint=(
                        f"{self.client_address[0]}:{self.client_address[1]}"
                    ),
                    role_id=role.role_id,
                    profile_id=compatibility.profile.profile_id,
                    play_profile_id=DEV_PLAY_RESOURCES_PROFILE_ID,
                    values=[
                        {
                            **item,
                            "amount": int(balance[item["value_name"]]),
                            "provenance": DEV_PLAY_TICKET_BALANCE_PROVENANCE,
                        }
                        for item in resources_overlay.ticket_values
                    ],
                    value_origin=DEV_PLAY_RESOURCES_VALUE_ORIGIN,
                    provenance=DEV_PLAY_RESOURCES_ORIGIN,
                    balance_origin=DEV_PLAY_TICKET_BALANCE_ORIGIN,
                    ledger_meta_key=DEV_PLAY_TICKET_BALANCE_META_KEY,
                    persisted=True,
                    device_acceptance_baseline=False,
                    replaceable=True,
                    historical=False,
                )
            fields = compatibility.role_value_vector()
            for item in resources_overlay.ticket_values:
                if int(fields[item["value_index"]]) != int(
                    balance[item["value_name"]]
                ):
                    raise RoleStoreError(
                        "dev-play ticket ledger did not land on "
                        f"S2C 27 positions {item['wire_position']}"
                    )
            return fields
        # Reaching here means `resources_overlay` is None, so the first guard
        # above guarantees `grant` is not None.
        if not session_state.get("owner_draw_ticket_grant_applied"):
            compatibility.role_values[LOTTERY_TICKET_VALUE_INDEX].value = int(
                grant.lottery_ticket
            )
            compatibility.role_values[
                LOTTERY_TICKET_ADVANCED_VALUE_INDEX
            ].value = int(grant.lottery_ticket_advanced)
            session_state["owner_draw_ticket_grant_applied"] = True
            self.server.event_logger.emit(
                "synthetic_test_grant_applied",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=role.role_id,
                profile_id=compatibility.profile.profile_id,
                lottery_ticket=int(grant.lottery_ticket),
                lottery_ticket_advanced=int(grant.lottery_ticket_advanced),
                lottery_ticket_reward_type=LOTTERY_TICKET_REWARD_TYPE,
                lottery_ticket_advanced_reward_type=(
                    LOTTERY_TICKET_ADVANCED_REWARD_TYPE
                ),
                wire_positions=[
                    LOTTERY_TICKET_WIRE_POSITION,
                    LOTTERY_TICKET_ADVANCED_WIRE_POSITION,
                ],
                value_indices=[
                    LOTTERY_TICKET_VALUE_INDEX,
                    LOTTERY_TICKET_ADVANCED_VALUE_INDEX,
                ],
                provenance=OWNER_DRAW_TEST_ORIGIN,
                persisted=False,
                device_acceptance_baseline=False,
                fixture_id=grant.fixture_id,
                value_source="role_value_vector",
            )
        if gacha_overlay:
            # TASK-0437: 三世镜 (THREE_MIRROR wire 35 / UN_THREE_MIRROR wire 36)
            # are generic role values, not wallet rows; the refine/觉醒技 panel
            # reads them from this login vector.
            compatibility.role_values[34].value = 5000
            compatibility.role_values[35].value = 5000
        fields = compatibility.role_value_vector()
        if (
            int(fields[LOTTERY_TICKET_VALUE_INDEX])
            != int(grant.lottery_ticket)
            or int(fields[LOTTERY_TICKET_ADVANCED_VALUE_INDEX])
            != int(grant.lottery_ticket_advanced)
        ):
            raise RoleStoreError(
                "owner draw ticket grant did not land on S2C 27 positions "
                f"{LOTTERY_TICKET_WIRE_POSITION}/"
                f"{LOTTERY_TICKET_ADVANCED_WIRE_POSITION}"
            )
        return fields

    def _reconcile_fight_rewards(
        self, connection_id, role, session_state
    ) -> set[int]:
        """Backfill recovered fight rewards for clears already in the archive.

        The permanent QA save already holds the chapter-1 fight clear from an
        earlier RUN.  A reward that was added after that clear must still be
        delivered exactly once; the fixed instance id makes the check and the
        write idempotent.  This is not a manual grant: the fight table's own
        `fightRewardId` is the authority, and only fights the archive recorded
        as settled are considered.
        """

        account_key = session_state.get("account_key")
        role_record = session_state.get("role_record")
        if account_key is None or role_record is None:
            return set()
        existing = {
            row.instance_id
            for row in self.server.role_store.card_instances(
                account_key=account_key
            )
        }
        role_id = role.role_id if role is not None else None
        profile_id = (
            role.compatibility.profile.profile_id
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile.profile_id
        )
        new_templates: set[int] = set()
        for clear in self.server.role_store.chapter_fight_clears(
            account_key=account_key
        ):
            reward = reward_for_fight(clear.fight_id)
            if reward is None or reward.instance_id in existing:
                continue
            try:
                row, outcome = self.server.role_store.record_card_instance(
                    record=role_record,
                    instance_id=reward.instance_id,
                    card_cfg_id=reward.card_id,
                    level=reward.level,
                    sfm_id1=reward.sfm_id1,
                    sfm_id2=reward.sfm_id2,
                    origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
                )
            except RoleStoreError as exc:
                self._emit_error(
                    connection_id,
                    "chapter_fight_reward_backfill_failed",
                    str(exc),
                    fight_id=clear.fight_id,
                    reward_id=reward.reward_id,
                    card_id=reward.card_id,
                )
                continue
            if outcome != "created":
                continue
            existing.add(reward.instance_id)
            try:
                new_templates.add(template_id_for(row.card_cfg_id))
            except KeyError:
                pass
            self.server.event_logger.emit(
                "tcp_chapter_fight_reward_backfilled",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                fight_id=clear.fight_id,
                reward_id=reward.reward_id,
                card_id=reward.card_id,
                card_instance_id=reward.instance_id,
                reward_source=FIGHT_REWARD_SOURCE,
                reward_basis="existing_recorded_fight_clear",
                win_loss_decoded=False,
                win_loss_policy="not_decoded_native_seeded_result",
                historical_values_recovered=True,
                role_id=role_id,
                profile_id=profile_id,
            )
        return new_templates

    def _send_card_snapshot(
        self,
        connection_id,
        writer,
        role,
        session_state,
        *,
        new_template_ids: set[int] | None = None,
    ) -> None:
        """Replay every stored card the client may see at this login.

        `returnCardStart/Info/End` is the client's own card-library writer
        (`pkgCardNcMgr` -> `pkgCardDataMgr.tbCard`).  A deterministic draw
        fixture is replayed only while the explicit Owner draw-test overlay is
        active; a recovered fight reward (or any other non-fixture row) is real
        saved state and replays on every launch, so the card and its team slot
        survive a normal restart.

        The handbook rows are derived from the same visible rows: one S2C 751
        per owned template, sent after the card library is complete.
        """

        grant = self.server.owner_draw_test_grant
        dev_resources_active = (
            session_state.get("dev_play_resources_overlay") is not None
        )
        replay_draw_fixtures = grant is not None or dev_resources_active
        account_key = session_state.get("account_key")
        # The Team login snapshot (S2C 37) may only name card instances this
        # very login has just written into the client's card library (S2C 30).
        # A preset can outlive a card row (TASK-0189: RUN-0061 replayed
        # `revival-fixture-draw-0007-*` in the team while the strict login had
        # suppressed the fixture rows), and the client's `getCardCfgID` then
        # indexes nil before `pkgMainUIMgr.initBtn` runs.  Record both sets
        # BEFORE any early return, so a login that sends no card at all is
        # represented by empty sets rather than by a stale earlier session.
        session_state["login_visible_card_ids"] = set()
        session_state["login_suppressed_fixture_ids"] = set()
        if account_key is None:
            return
        stored = self.server.role_store.card_instances(account_key=account_key)
        rows = [
            row
            for row in stored
            if replay_draw_fixtures
            or row.origin != DETERMINISTIC_FIXTURE_ORIGIN
        ]
        suppressed_fixture_ids = [
            row.instance_id
            for row in stored
            if not replay_draw_fixtures
            and row.origin == DETERMINISTIC_FIXTURE_ORIGIN
        ]
        suppressed = len(suppressed_fixture_ids)
        session_state["login_visible_card_ids"] = {
            row.instance_id for row in rows
        }
        session_state["login_suppressed_fixture_ids"] = set(
            suppressed_fixture_ids
        )
        if not rows:
            return
        profile_id = (
            role.compatibility.profile.profile_id
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile.profile_id
        )
        role_id = role.role_id if role is not None else None
        self._send_s2c(
            connection_id,
            writer,
            "returnCardStart",
            (),
            classification="recovered_card_snapshot",
        )
        dev_cards = (
            self.server.dev_play_babel
            or self.server.dev_play_artifact
            or self.server.dev_play_resources
            or self.server.dev_play_gacha
        )
        for row in rows:
            _fields = list(row.return_card_info_fields())
            if dev_cards and int(_fields[14]) <= 0:
                # TASK-0470: refineLevel has no level-0 row and the 鲁飞 wash
                # panel reads getLevelCfg(dRefineLv) directly, so an unawakened
                # card would raise before C2S 195 is ever sent.
                _fields[14] = 1
            if dev_cards:
                # TASK-0483: a card row missing one of its shipped base skills
                # (for example 汐风's sfmId3 37132 阴阳切断) leaves a whole skill
                # slot empty; fill every zero slot from card/card.max and keep
                # the 6-star awakening seed in the same write.
                _base = dev_play_refine.base_skills(row.card_cfg_id)
                _four = int(_fields[8]) if int(_fields[8]) > 0 else int(
                    dev_play_refine.initial_four(row.card_cfg_id)
                )
                _changed = False
                _star = dev_play_refine.card_star(row.card_cfg_id)
                # TASK-0486: a card has two skills, one of them upgrades at
                # 5-star, the THIRD skill only unlocks at 6-star and the fourth
                # is the 鲁飞 awakening.  Under 6 stars sfmId3/sfmId4 must stay
                # 0 or a 4-star card renders a third skill it never unlocked.
                for _idx, _val in zip((5, 6), _base[:2]):
                    if int(_val) > 0 and (_star >= 5 or int(_fields[_idx]) <= 0):
                        if int(_fields[_idx]) != int(_val):
                            _fields[_idx] = int(_val)
                            _changed = True
                _third = int(_base[2]) if _star >= 6 else 0
                if int(_fields[7]) != _third:
                    _fields[7] = _third
                    _changed = True
                _four = int(_four) if _star >= 6 else 0
                if int(_fields[8]) != _four:
                    _fields[8] = _four
                    _changed = True
                if _changed:
                    try:
                        self.server.role_store.set_card_skills(
                            account_key=account_key,
                            instance_id=row.instance_id,
                            sfm_id1=int(_fields[5]),
                            sfm_id2=int(_fields[6]),
                            sfm_id3=int(_fields[7]),
                            sfm_id4=int(_fields[8]),
                            provenance="synthetic_dev_card_base_skills",
                        )
                    except Exception:
                        pass
                # TASK-0490: dev fixtures carry zero learn stats, so every battle
                # is lost on raw config numbers; give every dev card a large
                # additive hp/atk the client sums into its displayed stats.
                _learn_atk = 200000
                _learn_hp = 1000000
                if (int(_fields[16]) < _learn_atk
                        or int(_fields[17]) < _learn_hp):
                    _fields[16] = _learn_atk
                    _fields[17] = _learn_hp
                    try:
                        self.server.role_store.set_card_learn_stats(
                            account_key=account_key,
                            instance_id=row.instance_id,
                            learn_atk=_learn_atk,
                            learn_hp=_learn_hp,
                        )
                    except Exception:
                        pass
            self._send_s2c(
                connection_id,
                writer,
                "returnCardInfo",
                tuple(_fields),
                decoded_fields={
                    "card_instance_id": row.instance_id,
                    "card_cfg_id": row.card_cfg_id,
                    "card_level": row.level,
                    "card_origin": row.origin,
                    "card_fixture_id": row.fixture_id,
                    "card_fixture_kind": row.fixture_kind,
                },
                classification="recovered_card_snapshot",
                record_fields={
                    "card_instance_id": row.instance_id,
                    "card_cfg_id": row.card_cfg_id,
                    "card_origin": row.origin,
                    "card_fixture_id": row.fixture_id,
                    "card_fixture_kind": row.fixture_kind,
                    "historical_values_recovered": False,
                    "role_id": role_id,
                    "profile_id": profile_id,
                },
            )
        self._send_s2c(
            connection_id,
            writer,
            "returnCardEnd",
            (),
            classification="recovered_card_snapshot",
        )
        self.server.event_logger.emit(
            "tcp_card_snapshot_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            rows_sent=len(rows),
            instance_ids=[row.instance_id for row in rows],
            card_cfg_ids=[row.card_cfg_id for row in rows],
            origins=sorted({row.origin for row in rows}),
            suppressed_fixture_rows=suppressed,
            synthetic_test_grant=grant is not None,
            **(
                {"dev_play_resources_overlay": True}
                if dev_resources_active
                else {}
            ),
            historical_ordering="unknown",
            historical_values_recovered=False,
            replayed=True,
            state_changed=False,
            role_id=role_id,
            profile_id=profile_id,
        )
        self._send_card_dex_rows(
            connection_id,
            writer,
            role,
            rows,
            new_template_ids=new_template_ids or set(),
            snapshot_section="login_replay",
        )
        # The login segment reaches the client before `pkgStoryMgr` has settled
        # the scene switch; `pkgCardDexDataMgr.returnUpdateCardDex` drops rows
        # while a story is waiting.  Keep the visible set and push it again on
        # the first heartbeat after the client has entered the city, so the
        # handbook is populated even when the first copy was dropped.  Repeating
        # a row is safe: the client replaces it and a second NEW_CARD_DEX is
        # suppressed because `tbCardDex[template]` already exists.
        session_state["card_dex_pending"] = (
            list(rows),
            set(new_template_ids or set()),
            1,
        )

    def _apply_growth_grant(self, connection_id, role, session_state) -> None:
        """Top the dog-food inventory up to the Dev-Server test grant.

        This is the same explicit, profile-scoped overlay as the ticket grant,
        but the counts are persisted so consumption survives a relaunch.  Every
        row it writes carries ``synthetic_test_grant`` as its provenance; the
        event names the top-up policy so the numbers can never be read as a
        recovered reward.
        """

        grant = self.server.owner_growth_test_grant
        if grant is None:
            return
        account_key = session_state.get("account_key")
        role_record = session_state.get("role_record")
        if account_key is None or role_record is None:
            return
        current = self.server.role_store.card_special_counts(
            account_key=account_key
        )
        applied: dict[int, int] = {}
        for cfg_id, target in sorted(grant.food.items()):
            before = current.get(cfg_id, 0)
            if before >= target:
                continue
            self.server.role_store.adjust_card_special(
                record=role_record,
                cfg_id=cfg_id,
                delta=target - before,
                provenance=GROWTH_TEST_ORIGIN,
            )
            applied[cfg_id] = target
        self.server.event_logger.emit(
            "synthetic_growth_grant_applied",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id if role is not None else None,
            profile_id=(
                role.compatibility.profile.profile_id
                if role is not None and role.compatibility is not None
                else self.server.compatibility_profile.profile_id
            ),
            food=dict(grant.food),
            applied=applied,
            provenance=GROWTH_TEST_ORIGIN,
            persisted=True,
            top_up_policy="to_target_each_login",
            device_acceptance_baseline=False,
            reward_source="dev_server_test_grant",
        )

    def _send_special_card_snapshot(
        self, connection_id, writer, role, session_state
    ) -> None:
        """Replay the stored dog-food inventory (``pkgSpecialCardDataMgr``).

        The client rebuilds this container from S2C 504/506/505, which
        ``pkgCardNcMgr`` routes to ``empty/insert``.  Nothing is sent when the
        account owns no food, so the default login stream keeps its exact
        frozen bytes; a non-empty inventory is real saved state and replays on
        every launch.
        """

        account_key = session_state.get("account_key")
        if account_key is None:
            return
        # TASK-0433: the gacha sandbox seeds the two 妖魂拓片 stacks (4★/5★,
        # 30 pieces each) so a dev-play session can exercise the evolution UI.
        # The grant is persisted in this disposable profile and labelled.
        if (
            self.server.dev_play_gacha
            and self.server.dev_play_gacha_profile_matches
        ):
            try:
                owned = self.server.role_store.card_special_counts(
                    account_key=account_key
                )
                for cfg_id in (100002, 100003):
                    if int(owned.get(cfg_id, 0)) < 30:
                        self.server.role_store.add_special_card(
                            account_key=account_key,
                            cfg_id=cfg_id,
                            count=30 - int(owned.get(cfg_id, 0)),
                            provenance="synthetic_dev_play_gacha_tuopian",
                        )
            except Exception:
                pass
        if (
            (self.server.dev_play_babel
             and self.server.dev_play_babel_profile_matches)
            or (self.server.dev_play_artifact
                and self.server.dev_play_artifact_profile_matches)
        ):
            # TASK-0467: the strength panel's material grid is the dog-food
            # special-card inventory (妖气团子 …), so seed every food row to
            # 9999 on the dev profiles.  Persisted in the disposable profile.
            try:
                owned = self.server.role_store.card_special_counts(
                    account_key=account_key
                )
                for cfg_id in range(1, 13):
                    held = int(owned.get(cfg_id, 0))
                    if held < 9999:
                        self.server.role_store.add_special_card(
                            account_key=account_key,
                            cfg_id=cfg_id,
                            count=9999 - held,
                            provenance="synthetic_dev_play_dogfood",
                        )
            except Exception:
                pass
        counts = self.server.role_store.card_special_counts(
            account_key=account_key
        )
        if not counts:
            return
        profile_id = (
            role.compatibility.profile.profile_id
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile.profile_id
        )
        self._send_s2c(
            connection_id,
            writer,
            "returnSpecialCardStart",
            (),
            classification="persisted_special_card_snapshot",
        )
        for cfg_id in sorted(counts):
            self._send_s2c(
                connection_id,
                writer,
                "returnSpecialCardInfo",
                (cfg_id, counts[cfg_id]),
                decoded_fields={"cfg_id": cfg_id, "count": counts[cfg_id]},
                classification="persisted_special_card_snapshot",
            )
        self._send_s2c(
            connection_id,
            writer,
            "returnSpecialCardEnd",
            (),
            classification="persisted_special_card_snapshot",
        )
        # TASK-0399: replay owned 神器 so a shop purchase survives a relogin.  The client keys
        # them by instance id in pkgGodWeaponMgr.le_tbGodWeapons and only S2C 1621/1622/1623 fill
        # the list (1625 is the live add).
        weapons = self.server.role_store.god_weapon_rows(account_key=account_key)
        if weapons:
            self._send_s2c(connection_id, writer, "returnGodWeaponStart", (),
                           classification="persisted_god_weapon_snapshot")
            for instance_id, cfg_id, level, exp, star in weapons:
                # TASK-0456: aNNNNyyNNNNN is 12 values:
                # instance, cfg, level, rare, exp, lock, garbage, f1..f5.
                self._send_s2c(
                    connection_id, writer, "returnGodWeaponInfo",
                    (instance_id, int(cfg_id), int(level), 0, int(exp),
                     False, False, 0, 0, 0, 0, 0),
                    classification="persisted_god_weapon_snapshot")
            self._send_s2c(connection_id, writer, "returnGodWeaponEnd", (),
                           classification="persisted_god_weapon_snapshot")
        # TASK-0395: replay the piece (碎片) container too, so a granted piece survives
        # relogin. The gain block is display-only; this list is what the bag reads.
        pieces = self.server.role_store.card_piece_counts(account_key=account_key)
        if pieces:
            self._send_s2c(connection_id, writer, "returnReStart", (),
                           classification="persisted_piece_snapshot")
            for cfg_id, count in sorted(pieces.items()):
                self._send_s2c(
                    connection_id, writer, "returnReInfo", (cfg_id, count),
                    decoded_fields={"cfg_id": cfg_id, "count": count},
                    classification="persisted_piece_snapshot")
            self._send_s2c(connection_id, writer, "returnReEnd", (),
                           classification="persisted_piece_snapshot")
        self.server.event_logger.emit(
            "tcp_special_card_snapshot_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            rows_sent=len(counts),
            counts=counts,
            origin="persisted_special_card_inventory",
            historical_values_recovered=True,
            replayed=True,
            state_changed=False,
            role_id=role.role_id if role is not None else None,
            profile_id=profile_id,
        )

    def _send_advance_sync_counter_snapshot(
        self, connection_id, writer, role, session_state
    ) -> None:
        """Restore the client's own ``ADVANCE_CARD`` sync counter after a login.

        ``pkgSyncCounterMgr`` never increments locally: it stores the value the
        server sent.  Without this packet a fresh client process would start the
        counter at 0 again, and a legitimate next advance would look exactly like
        a retry of the first stored one.  The counter is persisted with the
        committed advance requests, so this is a replay of this archive's own
        state -- not a recovered historical value.  A role that has never
        advanced sends nothing, which keeps the default login stream identical.
        """

        if role is None:
            return
        account_key = session_state.get("account_key")
        if account_key is None:
            return
        next_counter = self.server.role_store.next_advance_sync_counter(
            account_key=account_key, role_id=role.role_id
        )
        # TASK-0435: the client's advanceCardEnd reads
        # pkgSyncCounterMgr.getSyncCounter(ADVANCE_CARD); a counter that was
        # never sent is nil and the 进化 button silently does nothing.  Dev
        # overlays announce 0 so the first advance can be issued; frozen
        # profiles keep the exact old stream (no packet when 0).
        dev_overlay = (
            isinstance(session_state.get("dev_play_babel_overlay"), BabelOverlay)
            or isinstance(session_state.get("dev_play_artifact_overlay"), ArtifactOverlay)
            or (
                self.server.dev_play_gacha
                and self.server.dev_play_gacha_profile_matches
            )
        )
        if next_counter <= 0 and not dev_overlay:
            return
        response = self._send_s2c(
            connection_id,
            writer,
            "returnSyncCounter",
            (ADVANCE_CARD_SYNC_COUNTER_TYPE, next_counter),
            decoded_fields={
                "counter_type": ADVANCE_CARD_SYNC_COUNTER_TYPE,
                "counter_value": next_counter,
            },
            classification="persisted_advance_sync_counter",
        )
        self.server.event_logger.emit(
            "tcp_advance_sync_counter_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            counter_type=ADVANCE_CARD_SYNC_COUNTER_TYPE,
            counter_value=next_counter,
            response_protocol_id=response.spec.protocol_id,
            origin="persisted_card_advance_state",
            historical_values_recovered=False,
            historical_ordering="unknown",
            ordering_origin="synthetic_compatibility",
            state_changed=False,
        )

    def _flush_pending_card_dex(
        self, connection_id, writer, session_state
    ) -> None:
        """Push the login handbook rows on the first heartbeats after entry.

        Three bounded pushes cover the scene switch and any story that is still
        settling; a row the client already holds is simply replaced.  After the
        third push the pending block is dropped, so a long session does not
        repeat login traffic.
        """

        pending = session_state.get("card_dex_pending")
        if pending is None:
            return
        rows, new_template_ids, remaining = pending
        self._send_card_dex_rows(
            connection_id,
            writer,
            session_state.get("role"),
            rows,
            new_template_ids=new_template_ids,
            snapshot_section=f"post_enter_heartbeat_{4 - remaining}",
        )
        if remaining <= 1:
            session_state.pop("card_dex_pending", None)
        else:
            session_state["card_dex_pending"] = (
                rows,
                new_template_ids,
                remaining - 1,
            )

    def _send_card_dex_rows(
        self,
        connection_id,
        writer,
        role,
        rows,
        *,
        new_template_ids: set[int],
        snapshot_section: str,
    ) -> None:
        """Send one S2C 751 per owned handbook template.

        `pkgCardNcMgr.returnUpdateCardDex` stores the row in `tbCardDex` and,
        when `bNewCard` is true, dispatches `NEW_CARD_DEX`; the shipped
        `pkgCardGetShowMgr` turns that into the new-card view (cached while a
        battle scene is current and shown on the next scene).  Only templates
        the account really owns a card of are sent, and an unmapped placeholder
        card id is skipped by name rather than given an invented template.
        """

        templates: dict[int, int] = {}
        unmapped: list[int] = []
        for row in rows:
            try:
                template_id = template_id_for(row.card_cfg_id)
            except KeyError:
                if row.card_cfg_id not in unmapped:
                    unmapped.append(row.card_cfg_id)
                continue
            templates.setdefault(template_id, row.card_cfg_id)
        if not templates:
            return
        profile_id = (
            role.compatibility.profile.profile_id
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile.profile_id
        )
        role_id = role.role_id if role is not None else None
        sent: list[int] = []
        for template_id in sorted(templates):
            card_cfg_id = templates[template_id]
            is_new = template_id in new_template_ids
            self._send_s2c(
                connection_id,
                writer,
                "returnUpdateCardDex",
                (template_id, card_cfg_id, 0, 0, is_new, False),
                decoded_fields={
                    "dTemplateId": template_id,
                    "dCardId": card_cfg_id,
                    "dFavorLevel": 0,
                    "dFavorExp": 0,
                    "bNewCard": is_new,
                    "bActiveAddtionHpAtk": False,
                },
                classification="recovered_card_dex_from_owned_cards",
                record_fields={
                    "template_id": template_id,
                    "card_cfg_id": card_cfg_id,
                    "new_card": is_new,
                    "origin": "derived_from_owned_card_instances",
                    "historical_values_recovered": False,
                    "role_id": role_id,
                    "profile_id": profile_id,
                },
            )
            sent.append(template_id)
        self.server.event_logger.emit(
            "tcp_card_dex_snapshot_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            rows_sent=len(sent),
            template_ids=sent,
            new_template_ids=sorted(new_template_ids & set(templates)),
            unmapped_card_cfg_ids=unmapped,
            snapshot_section=snapshot_section,
            origin="derived_from_owned_card_instances",
            favor_level_sent=0,
            activated_addition_hp_atk=False,
            historical_values_recovered=False,
            role_id=role_id,
            profile_id=profile_id,
        )

    def _gacha_banner_window(self, role):
        """Rotate the six limited banners through the client's three tab slots.

        The client prefab has exactly three slots, so one login shows three of
        the six limited banners and the next login shows the other three.  The
        counter lives in the profile meta (one key per role) so a relogin
        continues the rotation; the first use starts at window 1 because the
        2026-10-28 session already showed 1015-1017.
        """

        store = self.server.role_store
        role_id = int(role.role_id) if role is not None else 0
        key = f"dev_play_gacha_rotation:{role_id}"
        try:
            raw = store.get_meta(key)
        except Exception:
            raw = None
        try:
            count = int(raw) if raw is not None else 1
        except (TypeError, ValueError):
            count = 1
        start = (count % 2) * HOT_SPOT_CLIENT_TAB_SLOTS
        try:
            store.set_meta(key, str(count + 1))
        except Exception:
            pass
        return HOT_SPOT_LIMITED_BANNERS[
            start:start + HOT_SPOT_CLIENT_TAB_SLOTS
        ]

    def _send_hot_spot_snapshot(
        self, connection_id, writer, role, session_state
    ) -> None:
        """Activate a bounded set of Dev Server banners for the draw overlay.

        The client only shows a hot-spot tab when
        ``start < serverTime < end`` (`pkgCardDataMgr.getHotSpot`), and the
        original activity schedule was never recovered.  This sends a clearly
        marked test window for the six later cards' banners; it is gated exactly
        like the ticket grant (qa-changan-main, overlay on, reconstructed odds).
        """

        if self.server.owner_draw_test_grant is None:
            return
        gacha = self.server.dev_play_gacha and self.server.dev_play_gacha_profile_matches
        if self.server.draw_odds != DRAW_ODDS_RECONSTRUCTED and not gacha:
            return
        now = self.server.now_unix()
        window_start = now - 3600
        window_end = now + 30 * 24 * 3600
        banners = (
            self._gacha_banner_window(role) if gacha else HOT_SPOT_TEST_BANNERS
        )
        self._send_s2c(
            connection_id,
            writer,
            "returnHotSpotDrawLibStart",
            (),
            classification="synthetic_hot_spot_activation",
        )
        for banner_id in banners:
            featured_card_id = HOT_SPOT_BANNERS[banner_id]
            self._send_s2c(
                connection_id,
                writer,
                "returnHotSpotDrawLibInfo",
                (banner_id, window_start, window_end, 0, 1),
                classification="synthetic_hot_spot_activation",
                decoded_fields={
                    "hot_spot_id": banner_id,
                    "featured_card_id": featured_card_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "ten_draw_count": 0,
                    "not_target_card": 1,
                },
            )
        self._send_s2c(
            connection_id,
            writer,
            "returnHotSpotDrawLibEnd",
            (),
            classification="synthetic_hot_spot_activation",
        )
        self.server.event_logger.emit(
            "tcp_hot_spot_snapshot_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            banners=list(banners),
            featured_cards=[
                HOT_SPOT_BANNERS[banner] for banner in banners
            ],
            window_start=window_start,
            window_end=window_end,
            activation_source="dev_server_test_window",
            historical_schedule_recovered=False,
            role_id=role.role_id if role is not None else None,
            profile_id=(
                role.compatibility.profile.profile_id
                if role is not None and role.compatibility is not None
                else self.server.compatibility_profile.profile_id
            ),
        )

    def _handle_owner_draw_request(
        self, connection_id, writer, packet, session_state
    ) -> None:
        """Answer one draw request with the selected odds mode.

        ``reconstructed_rate_table`` rolls the documented help-text rates and
        the advanced/limited guarantees; ``deterministic_fixture`` walks the
        frozen 妖灵 test pool with a fixed stride (no RNG).  With the overlay
        disabled it sends the client's own FAILURE result so the 15 s lock is
        released and no connection is killed.
        """

        diamond = packet.spec.name == "startDiamondDrawCard"
        if diamond:
            times, cost_type, hot_spot_id, sync_counter = packet.fields
            start_name = "returnDiamondDrawCardResultStart"
            result_name = "returnDiamondDrawCardResult"
            end_name = "returnDiamondDrawCardResultEnd"
        else:
            times, sync_counter = packet.fields
            cost_type = LOTTERY_TICKET_REWARD_TYPE
            hot_spot_id = 0
            start_name = "returnLotteryTicketDrawCardResultStart"
            result_name = "returnLotteryTicketDrawCardResult"
            end_name = "returnLotteryTicketDrawCardResultEnd"
        numeric_times = int(times)
        role = session_state.get("role")
        role_record = session_state.get("role_record")
        profile_id = (
            role.compatibility.profile.profile_id
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile.profile_id
        )
        request_fields = {
            "request_protocol_id": packet.spec.protocol_id,
            "request_protocol_name": packet.spec.name,
            "request_times": numeric_times,
            "cost_type": int(cost_type),
            "hot_spot_id": int(hot_spot_id),
            "sync_counter": int(sync_counter),
            "role_id": role.role_id if role is not None else None,
            "profile_id": profile_id,
        }
        grant = self.server.owner_draw_test_grant
        dev_resources = session_state.get("dev_play_resources_overlay")
        refusal = None
        if grant is None and dev_resources is None:
            refusal = "overlay_disabled"
        elif role_record is None:
            refusal = "unbound_role"
        elif numeric_times not in (1, 10):
            refusal = "unsupported_times"
        elif dev_resources is not None:
            # TASK-0235: the dev-play draw spends the two ticket ledgers only.
            # A hot-spot pool would need the recovered ten-draw discount and a
            # server-side charge model that no evidence pins down (D-190 U6),
            # so it is refused by name instead of guessing a price.  The same
            # goes for every cost type but the advanced-ticket one; the crystal
            # entry (costType 6) is out of scope and is not implemented here.
            if diamond and int(hot_spot_id) > 0:
                refusal = "unsupported_hot_spot"
            elif (
                diamond
                and int(cost_type) != LOTTERY_TICKET_ADVANCED_REWARD_TYPE
            ):
                refusal = "unsupported_cost_type"
        elif diamond and int(hot_spot_id) > 0 and int(hot_spot_id) not in (
            HOT_SPOT_LIMITED_BANNERS
            if (
                self.server.dev_play_gacha
                and self.server.dev_play_gacha_profile_matches
            )
            else HOT_SPOT_TEST_BANNERS
        ):
            refusal = "inactive_hot_spot"
        if refusal is not None:
            self._send_s2c(
                connection_id,
                writer,
                start_name,
                (),
                classification="owner_draw_test_refused",
            )
            self._send_s2c(
                connection_id,
                writer,
                end_name,
                (2,),
                classification="owner_draw_test_refused",
            )
            self.server.event_logger.emit(
                "tcp_draw_test_refused",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                reason=refusal,
                response_sent=True,
                result_code=2,
                **request_fields,
            )
            return
        serial = self.server.next_draw_fixture_serial()
        reconstructed = (
            dev_resources is None
            and self.server.draw_odds == DRAW_ODDS_RECONSTRUCTED
        )
        limited = reconstructed and diamond and int(hot_spot_id) > 0
        pool_kind = (
            "limited" if limited else ("advanced" if diamond else "normal")
        )
        if reconstructed:
            fixture_id = None
        elif dev_resources is not None:
            fixture_id = f"{DEV_PLAY_RESOURCES_FIXTURE_ID}-{serial:04d}"
        else:
            fixture_id = f"{grant.fixture_id}-{serial:04d}"
        pity_before = None
        pity_after = None
        featured_card_id = None
        selected_cards = []
        if reconstructed:
            state = DrawOddsState()
            account_key = session_state.get("account_key") or role_record.account_key
            if pool_kind in ("advanced", "limited"):
                stored = self.server.role_store.draw_pity(
                    account_key=account_key, pool=pool_kind
                )
                if stored is not None:
                    state = DrawOddsState(
                        since_legend=stored.since_legend,
                        since_rare=stored.since_rare,
                    )
                if limited:
                    featured_card_id = HOT_SPOT_BANNERS[int(hot_spot_id)]
                    stored_banner = self.server.role_store.hotspot_draw_pity(
                        account_key=account_key, hotspot_id=int(hot_spot_id)
                    )
                    if stored_banner is not None:
                        state = DrawOddsState(
                            since_legend=state.since_legend,
                            since_rare=state.since_rare,
                            since_featured_legend=(
                                stored_banner.since_featured_legend
                            ),
                        )
                pity_before = state
            outcomes, new_state = draw_outcomes(
                pool_kind,
                numeric_times,
                state,
                self.server.draw_rng,
                featured_card_id=featured_card_id,
            )
            if pool_kind in ("advanced", "limited"):
                self.server.role_store.record_draw_pity(
                    record=role_record,
                    pool=pool_kind,
                    since_legend=new_state.since_legend,
                    since_rare=new_state.since_rare,
                    provenance=PROBABILITY_AUTHORITY,
                )
                pity_after = new_state
            if limited:
                self.server.role_store.record_hotspot_draw_pity(
                    record=role_record,
                    hotspot_id=int(hot_spot_id),
                    since_featured_legend=new_state.since_featured_legend,
                    provenance=PROBABILITY_AUTHORITY,
                )
            selected_cards = [
                (CARDS_BY_ID[outcome.card_id], outcome.tier, outcome.forced)
                for outcome in outcomes
            ]
            origin = PROBABILITY_ORIGIN
            fixture_kind = None
        else:
            selected_cards = [
                (pool_card_at(serial, index), "", None)
                for index in range(numeric_times)
            ]
            origin = DETERMINISTIC_FIXTURE_ORIGIN
            fixture_kind = DETERMINISTIC_FIXTURE_KIND
        rows = []
        ticket_draw = None
        try:
            if dev_resources is not None:
                # TASK-0235: charge the persisted ledger and write every drawn
                # card row in ONE transaction.  The balance covers
                # `numeric_times` tickets of the pool the request names
                # (252 normal / 262 advanced); a refusal writes nothing.
                ticket_draw = self.server.role_store.record_ticket_draw(
                    record=role_record,
                    meta_key=DEV_PLAY_TICKET_BALANCE_META_KEY,
                    balance_field=TICKET_BALANCE_FIELD_FOR_REWARD_TYPE[
                        int(cost_type)
                    ],
                    seed_counts=DEV_PLAY_TICKET_BALANCE_SEED,
                    cost=numeric_times,
                    cards=tuple(
                        CardInstanceWrite(
                            instance_id=(
                                f"revival-fixture-draw-{serial:04d}-"
                                f"{index + 1:02d}"
                            ),
                            card_cfg_id=selected.card_id,
                            level=FIXTURE_CARD_LEVEL,
                            sfm_id1=selected.sfm_id1,
                            sfm_id2=selected.sfm_id2,
                            origin=origin,
                            fixture_id=fixture_id,
                            fixture_kind=fixture_kind,
                        )
                        for index, (selected, _tier, _forced) in enumerate(
                            selected_cards
                        )
                    ),
                    decode=decode_ticket_balance,
                    encode=encode_ticket_balance,
                    provenance=DEV_PLAY_TICKET_BALANCE_PROVENANCE,
                )
                rows = list(ticket_draw.rows)
            else:
                for index, (selected, _tier, _forced) in enumerate(selected_cards):
                    instance_id = (
                        f"revival-draw-{serial:04d}-{index + 1:02d}"
                        if reconstructed
                        else f"revival-fixture-draw-{serial:04d}-{index + 1:02d}"
                    )
                    row, _outcome = self.server.role_store.record_card_instance(
                        record=role_record,
                        instance_id=instance_id,
                        card_cfg_id=selected.card_id,
                        level=FIXTURE_CARD_LEVEL,
                        sfm_id1=selected.sfm_id1,
                        sfm_id2=selected.sfm_id2,
                        origin=origin,
                        fixture_id=fixture_id,
                        fixture_kind=fixture_kind,
                    )
                    rows.append(row)
        except (TicketBalanceShortfallError, DevPlayResourcesError, RoleStoreError) as exc:
            if isinstance(exc, TicketBalanceShortfallError):
                refusal_reason = "insufficient_tickets"
                refusal_fields = {
                    "reward_type": int(cost_type),
                    "cost": numeric_times,
                    "balance_before": exc.balance,
                }
            elif isinstance(exc, DevPlayResourcesError):
                # A present-but-unusable ledger marker: fail closed, name the
                # reason, and leave the archive untouched.
                refusal_reason = "ticket_balance_unusable"
                refusal_fields = {
                    "reward_type": int(cost_type),
                    "cost": numeric_times,
                    "ticket_balance_reason": exc.reason,
                }
            else:
                refusal_reason = "card_persistence_failed"
                refusal_fields = {}
            self._send_s2c(
                connection_id,
                writer,
                start_name,
                (),
                classification="owner_draw_test_refused",
            )
            self._send_s2c(
                connection_id,
                writer,
                end_name,
                (2,),
                classification="owner_draw_test_refused",
            )
            self.server.event_logger.emit(
                "tcp_draw_test_refused",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                reason=refusal_reason,
                detail=str(exc)[:240],
                response_sent=True,
                result_code=2,
                **refusal_fields,
                **request_fields,
            )
            return
        # The client renders the result by looking each result string up in
        # pkgCardDataMgr.tbCard; send the full card rows before the result.
        result_classification = (
            "reconstructed_probability_result"
            if reconstructed
            else "deterministic_draw_test_fixture"
        )
        for row, (selected, tier, _forced) in zip(rows, selected_cards):
            self._send_s2c(
                connection_id,
                writer,
                "returnCardInfo",
                row.return_card_info_fields(),
                decoded_fields={
                    "card_instance_id": row.instance_id,
                    "card_cfg_id": row.card_cfg_id,
                    "card_name": selected.name,
                    "card_tier": tier or None,
                    "card_level": row.level,
                    "card_origin": row.origin,
                    "card_fixture_id": row.fixture_id,
                    "card_fixture_kind": row.fixture_kind,
                },
                classification=result_classification,
                record_fields={
                    "card_instance_id": row.instance_id,
                    "card_cfg_id": row.card_cfg_id,
                    "card_name": selected.name,
                    "card_origin": row.origin,
                    "card_fixture_id": row.fixture_id,
                    "card_fixture_kind": row.fixture_kind,
                    "historical_values_recovered": False,
                    "role_id": role.role_id if role is not None else None,
                    "profile_id": profile_id,
                },
            )
        new_templates: set[int] = set()
        for row in rows:
            try:
                new_templates.add(template_id_for(row.card_cfg_id))
            except KeyError:
                continue
        self._send_card_dex_rows(
            connection_id,
            writer,
            role,
            rows,
            new_template_ids=new_templates,
            snapshot_section="draw_result",
        )
        self._send_s2c(
            connection_id,
            writer,
            start_name,
            (),
            classification=result_classification,
        )
        for row in rows:
            self._send_s2c(
                connection_id,
                writer,
                result_name,
                (row.instance_id,),
                classification=result_classification,
            )
        if diamond:
            self._send_s2c(
                connection_id,
                writer,
                "syncDiamondDrawInfo",
                (0, 0, 0),
                classification=result_classification,
                record_fields={
                    "counter_origin": (
                        PROBABILITY_ORIGIN
                        if reconstructed
                        else DETERMINISTIC_FIXTURE_ORIGIN
                    ),
                    "historical_values_recovered": False,
                },
            )
        self._send_s2c(
            connection_id,
            writer,
            end_name,
            (1,),
            classification=result_classification,
        )
        if ticket_draw is not None:
            # TASK-0235: the charge already committed with the card rows; the
            # client only redraws a ticket count when a value packet arrives
            # (D-190), so the new balance is published as S2C 24
            # ``returnOneValue_N`` AFTER the result-end packet.  Both the packet
            # choice and that position are a synthetic server choice -- the
            # original server's packet and its ordering against the lock
            # release were never observed (D-190 U2/U4).
            reward_type = int(cost_type)
            value_item = next(
                item
                for item in dev_resources.ticket_values
                if int(item["reward_type"]) == reward_type
            )
            value_packet = self._send_s2c(
                connection_id,
                writer,
                "returnOneValue_N",
                (reward_type, int(ticket_draw.balance_after)),
                classification="synthetic_dev_play_ticket_balance",
            )
            self.server.event_logger.emit(
                "tcp_dev_play_resources_ticket_balance_sent",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                role_id=role.role_id if role is not None else None,
                profile_id=profile_id,
                play_profile_id=DEV_PLAY_RESOURCES_PROFILE_ID,
                packet=value_packet.spec.protocol_id,
                packet_name=value_packet.spec.name,
                reward_type=reward_type,
                value_index=int(value_item["value_index"]),
                wire_position=int(value_item["wire_position"]),
                cost=numeric_times,
                balance_before=int(ticket_draw.balance_before),
                balance_after=int(ticket_draw.balance_after),
                balance_seeded=bool(ticket_draw.balance_seeded),
                ledger_origin=DEV_PLAY_TICKET_BALANCE_ORIGIN,
                ordering="synthetic_after_result_end",
                synthetic_server_choice=True,
                historical_values_recovered=False,
                provenance=DEV_PLAY_TICKET_BALANCE_PROVENANCE,
                replaceable=True,
                historical=False,
                persisted=True,
                state_changed=True,
            )
        if reconstructed:
            self.server.event_logger.emit(
                "tcp_draw_probability_result_sent",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                family=("diamond" if diamond else "lottery"),
                pool=pool_kind,
                result_count=len(rows),
                instance_ids=[row.instance_id for row in rows],
                card_cfg_ids=[row.card_cfg_id for row in rows],
                card_names=[
                    card.name for card, _tier, _forced in selected_cards
                ],
                tiers=[tier for _card, tier, _forced in selected_cards],
                forced=[forced for _card, _tier, forced in selected_cards],
                rates=dict(
                    LIMITED_RATES
                    if limited
                    else (ADVANCED_RATES if diamond else NORMAL_RATES)
                ),
                featured_card_id=featured_card_id,
                legend_pity_draws=LEGEND_PITY_DRAWS,
                rare_guarantee_draws=RARE_GUARANTEE_DRAWS,
                probability_model=PROBABILITY_AUTHORITY,
                model_version=PROBABILITY_MODEL_VERSION,
                model_source=PROBABILITY_MODEL_SOURCE,
                probability_authority=PROBABILITY_AUTHORITY,
                real_probability=True,
                pity_before=(
                    None
                    if pity_before is None
                    else {
                        "since_legend": pity_before.since_legend,
                        "since_rare": pity_before.since_rare,
                        "since_featured_legend": (
                            pity_before.since_featured_legend
                        ),
                    }
                ),
                pity_after=(
                    None
                    if pity_after is None
                    else {
                        "since_legend": pity_after.since_legend,
                        "since_rare": pity_after.since_rare,
                        "since_featured_legend": (
                            pity_after.since_featured_legend
                        ),
                    }
                ),
                historical_values_recovered=False,
                origin=PROBABILITY_ORIGIN,
                persisted=True,
                state_changed=True,
                **request_fields,
            )
        else:
            self.server.event_logger.emit(
                "tcp_draw_test_fixture_sent",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                family=("diamond" if diamond else "lottery"),
                result_count=len(rows),
                instance_ids=[row.instance_id for row in rows],
                card_cfg_ids=[row.card_cfg_id for row in rows],
                card_names=[card.name for card, _tier, _forced in selected_cards],
                pool_source=POOL_SOURCE,
                pool_size=len(POOL_CARDS),
                pool_indexes=[
                    pool_index_at(serial, index) for index in range(numeric_times)
                ],
                fixture_id=fixture_id,
                fixture_kind=DETERMINISTIC_FIXTURE_KIND,
                result_code=1,
                probability_authority="deterministic_test_fixture_not_rng",
                real_probability=False,
                historical_values_recovered=False,
                origin=DETERMINISTIC_FIXTURE_ORIGIN,
                persisted=True,
                state_changed=True,
                **(
                    {
                        "play_profile_id": DEV_PLAY_RESOURCES_PROFILE_ID,
                        "fixture_authority": DEV_PLAY_RESOURCES_VALUE_ORIGIN,
                        "reward_type": int(cost_type),
                        "ticket_cost": numeric_times,
                        "ticket_balance_before": int(ticket_draw.balance_before),
                        "ticket_balance_after": int(ticket_draw.balance_after),
                        "ledger_meta_key": DEV_PLAY_TICKET_BALANCE_META_KEY,
                        "ledger_origin": DEV_PLAY_TICKET_BALANCE_ORIGIN,
                    }
                    if ticket_draw is not None
                    else {}
                ),
                **request_fields,
            )

    def _emit_packet(self, event: str, connection_id: str, packet, **extra) -> None:
        fields = {
            "connection_id": connection_id,
            "remote_endpoint": f"{self.client_address[0]}:{self.client_address[1]}",
            "protocol_id": packet.spec.protocol_id,
            "protocol_name": packet.spec.name,
            "sequence": packet.sequence,
            "key_before": f"0x{packet.key_before:08X}",
            "key_after": f"0x{packet.key_after:08X}",
            **extra,
        }
        if self.server.event_logger.hex_dump:
            fields["wire_hex"] = packet.wire.hex(" ")
        self.server.event_logger.emit(event, **fields)

    def _emit_compatibility_packet(
        self,
        event: str,
        connection_id: str,
        packet,
        role: MinimalRoleState,
        **extra: Any,
    ) -> None:
        assert role.compatibility is not None
        self.server.event_logger.emit(
            event,
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            profile_id=role.compatibility.profile.profile_id,
            compatibility_goal=COMPATIBILITY_GOAL,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            key_before=f"0x{packet.key_before:08X}",
            key_after=f"0x{packet.key_after:08X}",
            wire_length=len(packet.wire),
            wire_sha256=hashlib.sha256(packet.wire).hexdigest(),
            **extra,
        )

    def _emit_error(
        self,
        connection_id: str,
        classification: str,
        message: str,
        **extra: Any,
    ) -> None:
        self.server.event_logger.emit(
            "tcp_error",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            classification=classification,
            message=message,
            **extra,
        )

    def _role_persistence_fields(self, record: RoleRecord) -> dict[str, Any]:
        """The durable-identity block attached to every role event.

        Bounded and credential-free: a short prefix of the salted key, never
        the inputs it was derived from.  ``account_identity_is_authenticated_
        player_identity`` is carried as ``False`` on purpose -- see the module
        note in ``role_store``; no reader may mistake this for SDK auth.
        """

        return {
            "account_key_short": record.display_key(),
            "identity_origin": record.identity_origin,
            "creation_compatibility_profile_id": record.compatibility_profile_id,
            "role_structure_version": record.structure_version,
            "role_store_kind": self.server.role_store.kind,
            "account_identity_policy": "salted_local_derivation",
            "account_identity_is_authenticated_player_identity": False,
            # Empty for every unnamed archive; a named play profile adds its own
            # identity and provenance, so a role in the log always names the
            # profile it belongs to.
            **self.server.role_store.profile_fields(),
        }

    def _refuse_profile_account_mismatch(
        self,
        connection_id: str,
        account_key: str,
        *,
        stage: str,
    ) -> None:
        """Refuse an account a named play profile is not bound to.

        No `S2C 5 noRole` is sent and no `S2C 14`/`S2C 2` path is opened: the
        client's create-role branch is reachable *only* through `noRole`, so
        refusing the login is the one answer that cannot make a second player's
        role appear in somebody else's save.  Nothing is invented for this --
        the same shape the missing-identity refusal already uses.
        """

        self._emit_error(
            connection_id,
            "profile_account_mismatch",
            "the selected durable play profile is bound to another account",
            stage=stage,
            role_store_kind=self.server.role_store.kind,
            role_count=self.server.role_store.count(),
            account_key_short=account_key[:8],
            no_role_sent=False,
            create_role_result_sent=False,
            second_role_created=False,
            **self.server.role_store.profile_fields(),
        )

    def _story_completion_evidence(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        *,
        story_id: int,
        previous_evidence: dict[str, Any] | None,
        successor_sequence: int,
    ) -> dict[str, Any]:
        """Turn an ordered `C2S 233` pair into a durable completion, or say why not.

        Every field of the result is part of the emitted event, so a reader of
        the log sees the whole decision: which story this packet is evidence
        about (if any), whether a row was written, whether one was already
        there, and -- when nothing was stored -- the closed reason.

        Three things must hold at once, and none of them is inferred:

        * the previous `C2S 233` of *this* connection armed a story that the
          recovered close callback chains into this one (`STORY_COMPLETION_SUCCESSORS`);
        * the session is bound to a role this archive really holds (the store
          re-reads the row inside its own transaction, so an unbound session,
          another account's session or a made-up record writes nothing);
        * nothing about the story is read from the client but the id and the
          order -- no reward, no chapter, no downstream story.

        A write failure is reported as an error event and leaves the session
        alive: the client only sent a notification, and our own bookkeeping
        failing is not something it could act on.
        """

        candidate = (
            previous_evidence["story_id"] if previous_evidence else None
        )
        if STORY_COMPLETION_SUCCESSORS.get(candidate) != story_id:
            # Either nothing preceded this packet in this session, or the pair
            # is not the one recovered chain -- so this packet proves nothing
            # about any story and is only recorded.
            return {
                "story_completion_candidate_story_id": None,
                "story_completion_recorded": False,
                "story_completion_already_present": False,
                "story_completion_refusal": (
                    STORY_COMPLETION_REFUSAL_NO_PREDECESSOR
                    if previous_evidence is None
                    else STORY_COMPLETION_REFUSAL_NOT_A_PAIR
                ),
                "server_story_state_written": False,
                "persistence_policy": "record_only_no_persistence",
                "persistence_origin": "synthetic_compatibility",
            }
        fields: dict[str, Any] = {
            "story_completion_candidate_story_id": candidate,
            "story_completion_recorded": False,
            "story_completion_already_present": False,
            "story_completion_refusal": None,
            "server_story_state_written": False,
            "persistence_policy": "ordered_successor_evidence_only",
            "persistence_origin": "synthetic_compatibility",
        }
        role_record = session_state.get("role_record")
        if role_record is None:
            # A session that never bound a role may not write play state, even
            # if it sends a perfectly shaped pair.
            fields["story_completion_refusal"] = (
                STORY_COMPLETION_REFUSAL_UNBOUND_ROLE
            )
            return fields
        if previous_evidence.get("role_id") != role_record.role_id:
            # The arm belongs to whatever role the session was bound to when it
            # arrived; a login that replaced that binding (or an arm from before
            # any role existed) is somebody else's evidence.
            fields["story_completion_refusal"] = (
                STORY_COMPLETION_REFUSAL_ROLE_CHANGED
            )
            return fields
        observed_at = self.server.now_unix()
        try:
            record, created = self.server.role_store.record_story_completion(
                record=role_record,
                story_id=candidate,
                state=STORY_STATE_FINISH,
                observed_at=observed_at,
                evidence_kind=STORY_EVIDENCE_ORDERED_SUCCESSOR,
                evidence_story_id=story_id,
                predecessor_sequence=int(previous_evidence["sequence"]),
                successor_sequence=int(successor_sequence),
            )
        except RoleStoreError as exc:
            # Fail visibly, never silently: the completion is not stored, the
            # event says so, and the session continues because the client's own
            # notification needs no answer.
            self._emit_error(
                connection_id,
                "story_completion_write_failed",
                str(exc),
                story_id=candidate,
                evidence_story_id=story_id,
                **self.server.role_store.profile_fields(),
            )
            fields["story_completion_refusal"] = STORY_COMPLETION_REFUSAL_ARCHIVE
            return fields
        fields.update(record.evidence_fields())
        fields.update(
            {
                "story_completion_story_id": record.story_id,
                "story_completion_recorded": created,
                "story_completion_already_present": not created,
                "story_id_reused": not created,
                "server_story_state_written": created,
                "persistence_origin": STORY_ORIGIN_LOCAL_OBSERVATION,
                "historical_completion_time_recovered": False,
                "completion_time_policy": "observed_evidence_time_not_historical",
                "account_key_short": record.display_key(),
            }
        )
        return fields

    @staticmethod
    def _story_replay_refusal(row: StoryRecord, role: MinimalRoleState) -> str | None:
        """Why a stored row may not be replayed to this session, or ``None``.

        Pure: the row is checked against what this build can vouch for (the one
        recovered pair), against the role actually logging in, and against the
        wire field's own range -- so a row that was written by hand, by another
        tool or by a later version can never make the server assert something it
        cannot prove.
        """

        if row.story_id not in STORY_REPLAYABLE_COMPLETIONS:
            return STORY_REPLAY_REFUSAL_NOT_REPLAYABLE
        if row.role_id != role.role_id:
            return STORY_REPLAY_REFUSAL_ROLE_MISMATCH
        if row.state != STORY_STATE_FINISH:
            return STORY_REPLAY_REFUSAL_STATE
        if not 0 <= row.observed_at <= STORY_MAX_WIRE_TIME:
            return STORY_REPLAY_REFUSAL_TIME
        return None

    def _send_team_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
        session_state: dict[str, Any],
    ) -> None:
        """Send the recovered Team + fabao-select login snapshot (v5 policy).

        Four packets, in the order the client's own code forces:

        * `S2C 36 returnTeamStart ''`  -> `initTeamCardData()`: the client's own
          preset reset, five presets with empty `tbCards`/`tbEquips`;
        * `S2C 37 returnTeamInfo(gid, '', …)` -> `setTeamCardData`: the ONLY
          writer of the full record shape (`tbEquips`/`tbCards`/
          `strGodWeaponId`), and the only way `strGodWeaponId` exists at all;
        * `S2C 48 returnSelectMagicStart ''` -> `initTeamFabaoData()`: the ONLY
          producer of `tbTeamFabao`, without which the very next recovered
          statement in `caclGroupFight` (`pkgFabaoDataMgr.caclSelectFight` ->
          `pairs(tbTeamFabao[gid])`) is a `pairs(nil)`;
        * `S2C 35 returnGroupId(gid)` -> `setCurGroupID`: names the current
          preset LAST, because `setCurGroupID` dispatches
          `pkgEventType.CHANGE_TEAM` and `pkgMainUIMgr` maps `CHANGE_TEAM` to
          `updateFight`, which reads the unguarded
          `tbTeamCard[getCurGroupID()]`.  A group id may therefore only become
          current once its container exists.

        Every slot is the client's own empty-slot literal, so nothing about
        cards, equipment, god weapons, levels or fight power is invented; a
        server whose profile carries no TeamSnapshot policy sends nothing at
        all and its stream is unchanged.
        """

        policy = role.compatibility.profile.team_snapshot
        if policy is None:  # pragma: no cover - v1-v4 profiles
            return
        account_key = session_state.get("account_key")
        group = policy.current_group
        preset = None
        if account_key is not None:
            state = self.server.role_store.team_state(account_key=account_key)
            if state is not None:
                group = state.current_group
            preset = self.server.role_store.team_preset(
                account_key=account_key, group_id=group
            )
        card_ids_sent: list[str] = []
        withheld_card_ids: list[str] = []
        unresolved_card_slots: list[tuple[int, str]] = []
        if preset is not None:
            # Only a card instance this login wrote as S2C 30 exists in the
            # client's `tbCard`.  Empty slots stay empty; a deterministic
            # fixture that the strict login suppressed becomes the client's
            # own empty-slot literal; any other id is neither sent nor
            # knowingly suppressed, so it is kept AND named instead of being
            # silently dropped (the next RUN must be able to classify it).
            visible_ids = session_state.get("login_visible_card_ids") or set()
            suppressed_ids = (
                session_state.get("login_suppressed_fixture_ids") or set()
            )
            for slot, instance_id in enumerate(preset.card_ids):
                if not instance_id or instance_id in visible_ids:
                    card_ids_sent.append(instance_id)
                elif instance_id in suppressed_ids:
                    card_ids_sent.append(policy.empty_slot)
                    withheld_card_ids.append(instance_id)
                else:
                    card_ids_sent.append(instance_id)
                    unresolved_card_slots.append((slot, instance_id))
            info_fields = (
                preset.group_id,
                *preset.equip_ids,
                preset.god_weapon_id,
                *card_ids_sent,
            )
            group_fields = (group,)
            team_state_origin = preset.origin
            team_state_values = preset.evidence_fields()
        else:
            info_fields = (group,) + (policy.empty_slot,) * (
                TEAM_SNAPSHOT_EQUIP_SLOTS + 1 + TEAM_SNAPSHOT_CARD_SLOTS
            )
            card_ids_sent = [policy.empty_slot] * TEAM_SNAPSHOT_CARD_SLOTS
            group_fields = (group,)
            team_state_origin = policy.container_provenance.origin
            team_state_values = {
                "team_group_id": group,
                "team_card_ids": [],
                "team_equip_ids": [],
                "team_god_weapon_id": "",
            }
        for slot, instance_id in unresolved_card_slots:
            self.server.event_logger.emit(
                "tcp_team_snapshot_unresolved_card",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                role_id=role.role_id,
                profile_id=role.compatibility.profile.profile_id,
                instance_id=instance_id,
                group_id=group,
                snapshot_section="team_preset_record",
                card_slot_index=slot,
                card_projection_policy=TEAM_CARD_PROJECTION_POLICY,
                card_projection_origin=TEAM_CARD_PROJECTION_ORIGIN,
                historical_values_recovered=False,
                state_changed=False,
            )
        section = (
            (
                "returnTeamStart",
                policy.team_start_fields(),
                {"snapshot_section": "team_preset_reset"},
            ),
            (
                "returnTeamInfo",
                info_fields,
                {
                    "snapshot_section": "team_preset_record",
                    "dGroupID": group,
                },
            ),
            (
                "returnSelectMagicStart",
                policy.select_magic_start_fields(),
                {"snapshot_section": "fabao_select_container"},
            ),
            (
                "returnGroupId",
                group_fields,
                {
                    "snapshot_section": "current_group",
                    "dGroupID": group,
                },
            ),
        )
        for index, (name, fields, decoded) in enumerate(section):
            response = self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                decoded_fields=decoded,
                classification=(
                    "persisted_team_snapshot"
                    if preset is not None
                    else "compatibility_team_snapshot_v5"
                ),
            )
            # The record itself always carries the STORED values as evidence
            # (`team_card_ids`); the projection fields say what was actually
            # encoded into this login's S2C 37 and why.
            projection_values = (
                {
                    "team_card_ids_sent": list(card_ids_sent),
                    "team_card_ids_withheld": list(withheld_card_ids),
                    "card_projection_policy": TEAM_CARD_PROJECTION_POLICY,
                    "card_projection_origin": TEAM_CARD_PROJECTION_ORIGIN,
                    "replaceable": True,
                }
                if name == "returnTeamInfo"
                else {}
            )
            self._emit_compatibility_packet(
                "tcp_team_snapshot_sent",
                connection_id,
                response,
                role,
                parent_profile_id=(
                    role.compatibility.profile.parent_profile_id
                ),
                policy_compatibility_version=(
                    policy.container_provenance.compatibility_version
                ),
                snapshot_index=index,
                snapshot_packet_count=len(section),
                snapshot_section=decoded["snapshot_section"],
                container_policy_origin=policy.container_provenance.origin,
                current_group=group,
                current_group_policy=policy.current_group_policy,
                current_group_origin=(
                    team_state_origin
                    if preset is not None
                    else policy.current_group_provenance.origin
                ),
                empty_slot=policy.empty_slot,
                empty_slot_count=(
                    len(fields) - 1 if name == "returnTeamInfo" else 0
                ),
                team_state_origin=team_state_origin,
                team_state_persisted=preset is not None,
                historical_ordering=policy.historical_ordering,
                ordering_origin=policy.ordering_provenance.origin,
                historical_values_recovered=False,
                **team_state_values,
                **projection_values,
            )
        # TASK-0245 / profile v22: the battle-power gate value, immediately after
        # the S2C 35 that names the current preset.  The client's
        # `setMaxFight` dispatch runs the same `updateFight` chain S2C 35 itself
        # just ran, so this is the one position that is provably safe.  The
        # value is computed over the card ids this very login put into the
        # client's `tbCard` (`card_ids_sent`); a v18..v21 profile has no policy
        # and sends nothing, so its stream stays frozen.
        session_state.setdefault("team_client_card_ids", {})[group] = tuple(
            card_ids_sent
        )
        self._send_team_max_fight(
            connection_id,
            writer,
            role,
            None,
            session_state,
            trigger="login",
            group_id=group,
            card_ids=tuple(card_ids_sent),
        )

    # ------------------------------------------------------- card growth

    def _client_team_cards(
        self,
        session_state: dict[str, Any],
        role: MinimalRoleState | None,
        role_record,
    ) -> tuple[int, tuple[str, ...]]:
        """The preset the client is currently showing: ``(group, card ids)``.

        The login snapshot records the exact list it sent (withheld fixtures
        become the client's empty slot); later team edits update it.  The stored
        preset is only a fallback for a connection that never saw a snapshot.
        """

        policy = (
            role.compatibility.profile.team_snapshot
            if role is not None and role.compatibility is not None
            else None
        )
        account_key = getattr(role_record, "account_key", None)
        if account_key is None:
            account_key = session_state.get("account_key")
        state = (
            self.server.role_store.team_state(account_key=account_key)
            if account_key is not None
            else None
        )
        if state is not None:
            group = state.current_group
        elif policy is not None:
            group = policy.current_group
        else:  # pragma: no cover - every team policy has a current group
            group = 1
        sent = session_state.get("team_client_card_ids", {}).get(group)
        if sent is not None:
            return group, tuple(sent)
        if account_key is not None:
            preset = self.server.role_store.team_preset(
                account_key=account_key, group_id=group
            )
            if preset is not None:
                return group, tuple(preset.card_ids)
        return group, ()

    def _send_team_max_fight(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState | None,
        role_record,
        session_state: dict[str, Any],
        *,
        trigger: str,
        group_id: int | None = None,
        card_ids: tuple[str, ...] | None = None,
        event_extra: dict[str, Any] | None = None,
    ):
        """Send one S2C 39 ``returnTeamMaxFight`` computed by the client's formula.

        TASK-0245 / #27.  The value is the client's own ``caclGroupFight`` for
        the preset the client is showing (`revival_stub/team_power.py`), never a
        synthetic number and never written to the archive.  The packet is only
        sent by a profile that carries the v22 policy; v18..v21 keep their
        frozen streams.  A value this archive cannot reproduce (unknown card
        config, equipment/god weapon on a team card, a team card with no stored
        row) refuses the packet and records why instead of inventing a number.
        """

        if role is None or role.compatibility is None:
            return None
        profile = role.compatibility.profile
        policy = profile.team_max_fight
        if policy is None:
            return None
        account_key = getattr(role_record, "account_key", None)
        if account_key is None:
            account_key = session_state.get("account_key")
        if account_key is None:
            self.server.event_logger.emit(
                "tcp_team_max_fight_refused",
                connection_id=connection_id,
                message="no account key for this session",
                trigger=trigger,
                reason="unbound_account",
            )
            return None
        snapshot_policy = profile.team_snapshot
        stored_state = self.server.role_store.team_state(account_key=account_key)
        if group_id is None:
            if stored_state is not None:
                group_id = stored_state.current_group
            elif snapshot_policy is not None:
                group_id = snapshot_policy.current_group
            else:  # pragma: no cover - every team policy has a current group
                group_id = 1
        sent_map = session_state.setdefault("team_client_card_ids", {})
        if card_ids is None:
            if group_id in sent_map:
                card_ids = tuple(sent_map[group_id])
            else:
                preset = self.server.role_store.team_preset(
                    account_key=account_key, group_id=group_id
                )
                card_ids = tuple(preset.card_ids) if preset is not None else ()
        rows = {
            row.instance_id: row
            for row in self.server.role_store.card_instances(
                account_key=account_key
            )
        }
        try:
            value = team_power(
                card_ids=card_ids,
                card_rows=rows,
                soul_id=role.compatibility.active_soul,
            )
        except TeamPowerError as exc:
            self.server.event_logger.emit(
                "tcp_team_max_fight_refused",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                role_id=role.role_id,
                profile_id=profile.profile_id,
                trigger=trigger,
                reason=str(exc)[:240],
                group_id=group_id,
                team_card_ids=list(card_ids),
                value_sent=False,
                state_changed=False,
            )
            return None
        response = self._send_s2c(
            connection_id,
            writer,
            "returnTeamMaxFight",
            policy.fields(value),
            decoded_fields={"dTeamMaxFight": value},
            classification="computed_team_max_fight",
        )
        self.server.event_logger.emit(
            "tcp_team_max_fight_sent",
            connection_id=connection_id,
            remote_endpoint=(
                f"{self.client_address[0]}:{self.client_address[1]}"
            ),
            role_id=role.role_id,
            profile_id=profile.profile_id,
            parent_profile_id=profile.parent_profile_id,
            policy_compatibility_version=(
                policy.ordering_provenance.compatibility_version
            ),
            protocol_id=response.spec.protocol_id,
            protocol_name=response.spec.name,
            response_sequence=response.sequence,
            trigger=trigger,
            group_id=group_id,
            team_card_ids=list(card_ids),
            active_soul=role.compatibility.active_soul,
            value=value,
            value_policy=policy.value_policy,
            value_origin=TEAM_POWER_ORIGIN,
            value_provenance=TEAM_POWER_VALUE_PROVENANCE,
            rule_provenance=TEAM_POWER_RULE_PROVENANCE,
            formula_source=TEAM_POWER_SOURCE,
            team_power_digest=TEAM_POWER_DIGEST,
            ordering_origin=policy.ordering_provenance.origin,
            historical_ordering=policy.historical_ordering,
            historical_values_recovered=False,
            replaceable=True,
            state_changed=False,
            **(event_extra or {}),
        )
        return response

    def _growth_account(self, session_state):
        """``(role, role_record, account_key)`` or ``(None, None, None)``."""

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        account_key = session_state.get("account_key")
        if role is None or role_record is None or account_key is None:
            return None, None, None
        return role, role_record, account_key

    def _team_card_ids(self, account_key: str) -> set[str]:
        """Every card instance deployed in any stored team preset."""

        deployed: set[str] = set()
        for preset in self.server.role_store.team_presets(account_key=account_key):
            deployed.update(value for value in preset.card_ids if value)
        return deployed

    def _split_refusal(self, connection_id, writer, reason: str) -> None:
        self._send_s2c(
            connection_id,
            writer,
            "returnCardSplitResult",
            (2,),
            decoded_fields={"result": 2, "refusal": reason},
            classification="card_split_refused",
        )

    def _strength_refusal(self, connection_id, writer, target: str, reason: str) -> None:
        self._send_s2c(
            connection_id,
            writer,
            "returnStrengthCardResult",
            (False, target),
            decoded_fields={"result": False, "refusal": reason},
            classification="card_strength_refused",
        )

    def _handle_card_split_packet(
        self, connection_id, writer, packet, session_state
    ) -> None:
        """C2S 218/219/220: the client's own 分解 chain.

        The three packets are one bounded transaction.  Material validation
        (owned, unlocked, not deployed, config row present) happens at
        ``splitCardEnd`` so the client's 15 s lock is released exactly once,
        with the client's own ``RESULT_TYPE`` (1 success / 2 failure).  The
        reward popup reads the gain-info family, so those rows are sent before
        the result packet.
        """

        name = packet.spec.name
        if name == "splitCardStart":
            session_state["card_split"] = {"instances": []}
            return
        if name == "splitCard":
            session = session_state.setdefault(
                "card_split", {"instances": []}
            )
            session["instances"].append(packet.fields[0].decode("utf-8"))
            return

        session = session_state.pop("card_split", None)
        role, role_record, account_key = self._growth_account(session_state)
        if session is None or not session["instances"] or role_record is None:
            self._split_refusal(connection_id, writer, "no_selection")
            return
        instance_ids = tuple(dict.fromkeys(session["instances"]))
        deployed = self._team_card_ids(account_key)
        total_yinpo = 0
        total_jinpo = 0
        try:
            for instance_id in instance_ids:
                row = self.server.role_store.card_instance(
                    account_key=account_key, instance_id=instance_id
                )
                if row is None:
                    self._split_refusal(connection_id, writer, "unowned_card")
                    return
                if row.locked:
                    self._split_refusal(connection_id, writer, "locked_card")
                    return
                if instance_id in deployed:
                    self._split_refusal(connection_id, writer, "card_in_team")
                    return
                yinpo, jinpo = split_reward_for_card(
                    row.card_cfg_id, row.advance_level
                )
                total_yinpo += yinpo
                total_jinpo += jinpo
        except (KeyError, ValueError):
            self._split_refusal(connection_id, writer, "unmapped_split_config")
            return
        try:
            wallet = self.server.role_store.apply_card_split(
                record=role_record,
                instance_ids=instance_ids,
                yinpo=total_yinpo,
                jinpo=total_jinpo,
            )
        except RoleStoreError:
            self._split_refusal(connection_id, writer, "store_refused")
            return

        self._send_s2c(
            connection_id, writer, "returnGainInfoStart", (280,),
            classification="card_split_reward",
        )
        if total_yinpo:
            self._send_s2c(
                connection_id, writer, "returnGainInfo",
                (28, 0, "", total_yinpo),
                classification="card_split_reward",
            )
        if total_jinpo:
            self._send_s2c(
                connection_id, writer, "returnGainInfo",
                (29, 0, "", total_jinpo),
                classification="card_split_reward",
            )
        self._send_s2c(
            connection_id, writer, "returnGainInfoEnd", (),
            classification="card_split_reward",
        )
        self._send_s2c(
            connection_id, writer, "returnOneValue_N", (28, int(wallet["yinpo"])),
            classification="card_split_wallet",
        )
        self._send_s2c(
            connection_id, writer, "returnOneValue_N", (29, int(wallet["jinpo"])),
            classification="card_split_wallet",
        )
        for instance_id in instance_ids:
            self._send_s2c(
                connection_id, writer, "delCard", (instance_id,),
                decoded_fields={"card_instance_id": instance_id},
                classification="card_split_consumed",
            )
        self._send_s2c(
            connection_id, writer, "returnCardSplitResult", (1,),
            decoded_fields={
                "result": 1,
                "instances": list(instance_ids),
                "yinpo": total_yinpo,
                "jinpo": total_jinpo,
                "yinpo_after": wallet["yinpo"],
                "jinpo_after": wallet["jinpo"],
            },
            classification="card_split_applied",
        )
        self.server.event_logger.emit(
            "tcp_card_split_applied",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            account_key_prefix=account_key[:8],
            instance_ids=list(instance_ids),
            yinpo=total_yinpo,
            jinpo=total_jinpo,
            yinpo_after=wallet["yinpo"],
            jinpo_after=wallet["jinpo"],
            reward_source="shipped_cardSplit_max",
            provenance="revival_local_card_split_reward",
            historical_values_recovered=True,
            state_changed=True,
        )

    def _handle_card_strength_packet(
        self, connection_id, writer, packet, session_state
    ) -> None:
        """C2S 14/15/16/17: the client's own 强化 chain.

        ``strengthCardStart`` names the target; the client then sends optional
        material card ids and dog-food counts; ``strengthCardEnd`` (a lock-send,
        partner S2C 70) is when the server re-validates everything, applies the
        client's own ``calculateLevel`` math, persists atomically and sends the
        new 21-value card row before the result.  A refusal still sends S2C 70
        with ``false`` so the client's 15 s lock never kills the connection.
        """

        name = packet.spec.name
        if name == "strengthCardStart":
            session_state["card_strength"] = {
                "target": packet.fields[0].decode("utf-8"),
                "materials": [],
                "food": {},
            }
            return
        if name == "strengthCard":
            session = session_state.setdefault(
                "card_strength", {"target": "", "materials": [], "food": {}}
            )
            session["materials"].append(packet.fields[0].decode("utf-8"))
            return
        if name == "strengthDogFoodCard":
            session = session_state.setdefault(
                "card_strength", {"target": "", "materials": [], "food": {}}
            )
            cfg_id, count = int(packet.fields[0]), int(packet.fields[1])
            if count > 0:
                session["food"][cfg_id] = session["food"].get(cfg_id, 0) + count
            return

        session = session_state.pop("card_strength", None)
        role, role_record, account_key = self._growth_account(session_state)
        target = session["target"] if session is not None else ""
        if session is None or not target or role_record is None:
            self._strength_refusal(connection_id, writer, target, "no_target")
            return
        target_row = self.server.role_store.card_instance(
            account_key=account_key, instance_id=target
        )
        if target_row is None:
            self._strength_refusal(connection_id, writer, target, "unowned_target")
            return
        try:
            star, _rare = card_star_rare(target_row.card_cfg_id)
        except KeyError:
            self._strength_refusal(
                connection_id, writer, target, "unmapped_target_config"
            )
            return
        if target_row.level >= max_level(star):
            self._strength_refusal(connection_id, writer, target, "level_max")
            return

        deployed = self._team_card_ids(account_key)
        material_ids = tuple(dict.fromkeys(session["materials"]))
        added_exp = 0
        for instance_id in material_ids:
            if instance_id == target:
                self._strength_refusal(
                    connection_id, writer, target, "target_as_material"
                )
                return
            row = self.server.role_store.card_instance(
                account_key=account_key, instance_id=instance_id
            )
            if row is None:
                self._strength_refusal(
                    connection_id, writer, target, "unowned_material"
                )
                return
            if row.locked or instance_id in deployed:
                self._strength_refusal(
                    connection_id, writer, target, "material_protected"
                )
                return
            material_star, _ = card_star_rare(row.card_cfg_id)
            added_exp += sum_exp(material_star, row.level, row.exp)

        food = {cfg: amount for cfg, amount in session["food"].items() if amount > 0}
        for cfg_id, amount in food.items():
            if cfg_id not in DOG_FOOD_CARD_IDS:
                self._strength_refusal(
                    connection_id, writer, target, "unknown_dog_food"
                )
                return
            if self.server.role_store.card_special_counts(
                account_key=account_key
            ).get(cfg_id, 0) < amount:
                self._strength_refusal(
                    connection_id, writer, target, "dog_food_missing"
                )
                return
            added_exp += dog_food_exp_for_cfg(cfg_id) * amount
        if added_exp <= 0:
            self._strength_refusal(connection_id, writer, target, "no_material")
            return

        total = sum_exp(star, target_row.level, target_row.exp) + added_exp
        new_level, new_exp = calculate_level(star, total)
        try:
            updated = self.server.role_store.apply_card_strength(
                record=role_record,
                target_instance_id=target,
                new_level=new_level,
                new_exp=new_exp,
                material_ids=material_ids,
                dog_food_spent=food,
                provenance="revival_local_card_strength",
            )
        except RoleStoreError:
            self._strength_refusal(connection_id, writer, target, "store_refused")
            return

        self._send_s2c(
            connection_id,
            writer,
            "updateCard",
            updated.return_card_info_fields(),
            decoded_fields={
                "card_instance_id": updated.instance_id,
                "card_level": updated.level,
                "card_exp": updated.exp,
                "added_exp": added_exp,
            },
            classification="card_strength_applied",
        )
        for instance_id in material_ids:
            self._send_s2c(
                connection_id, writer, "delCard", (instance_id,),
                decoded_fields={"card_instance_id": instance_id},
                classification="card_strength_consumed",
            )
        remaining = self.server.role_store.card_special_counts(
            account_key=account_key
        )
        for cfg_id in food:
            self._send_s2c(
                connection_id,
                writer,
                "returnSingleSpecialCard",
                (cfg_id, int(remaining.get(cfg_id, 0))),
                classification="card_strength_food_spent",
            )
        self._send_s2c(
            connection_id,
            writer,
            "returnStrengthCardResult",
            (True, target),
            decoded_fields={
                "result": True,
                "card_instance_id": target,
                "level_before": target_row.level,
                "level_after": updated.level,
                "exp_after": updated.exp,
                "added_exp": added_exp,
                "materials": list(material_ids),
                "dog_food_spent": food,
            },
            classification="card_strength_applied",
        )
        self.server.event_logger.emit(
            "tcp_card_strength_applied",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            account_key_prefix=account_key[:8],
            card_instance_id=target,
            card_cfg_id=updated.card_cfg_id,
            level_before=target_row.level,
            level_after=updated.level,
            exp_after=updated.exp,
            added_exp=added_exp,
            materials=list(material_ids),
            dog_food_spent=food,
            level_math="client_calculateLevel",
            provenance="revival_local_card_strength",
            state_changed=True,
        )
        # TASK-0245 / #27: a strength changes this card's own fight when the
        # card is in the preset the client is showing.
        group, team_ids = self._client_team_cards(
            session_state, role, role_record
        )
        if target in team_ids:
            self._send_team_max_fight(
                connection_id,
                writer,
                role,
                role_record,
                session_state,
                trigger="cardStrength",
                group_id=group,
                card_ids=team_ids,
                event_extra={
                    "card_instance_id": target,
                    "level_after": updated.level,
                },
            )

    def _advance_refusal(
        self,
        connection_id,
        writer,
        session_state,
        target: str,
        reason: str,
        **extra: Any,
    ) -> None:
        """Answer one advance chain with the client's own failure result.

        S2C 71's second field is the only failure signal the recovered client
        reads; it carries no reason, so the closed code lives in the event log.
        """

        role = session_state.get("role")
        self._send_s2c(
            connection_id,
            writer,
            "returnAdvanceCardResult",
            (target, False),
            decoded_fields={"card_instance_id": target, "result": False,
                            "refusal": reason},
            classification="card_advance_refused",
        )
        self.server.event_logger.emit(
            "tcp_card_advance_refused",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=None if role is None else role.role_id,
            card_instance_id=target,
            refusal=reason,
            state_changed=False,
            **extra,
        )

    def _send_guide_done_snapshot(self, connection_id, writer) -> None:
        """Mark the chapter-8 guide dialogues finished (TASK-0468).

        The strength/city guide runs from pkgGuideModelMgr.le_tb_guide, which is
        filled only by S2C 408/409/410.  Without it checkGuideFinish() is false
        on every launch and the intro talk plays again; the 141 story ids do not
        cover these guide ids (10001..).
        """

        self._send_s2c(connection_id, writer, "returnGuideTaskStart", (),
                       classification="synthetic_dev_guide_done")
        for guide_id in range(10001, 10101):
            self._send_s2c(
                connection_id, writer, "returnGuideTask",
                (guide_id, 2, 0),
                classification="synthetic_dev_guide_done",
            )
        self._send_s2c(connection_id, writer, "returnGuideTaskEnd", (),
                       classification="synthetic_dev_guide_done")

    def _send_story_skip(self, connection_id, writer, role, session_state) -> None:
        """Mark every storyTalk event trigger STATE_FINISH after the scene loads.

        TASK-0443: the client's chapter story trigger runs on scene entry and
        checks checkStoryFinish(storyId); sending S2C 412 for the cfg rows with
        type > 0 during the login segment made the client close the connection,
        so they are sent on the first heartbeat instead (the story model accepts
        412 at any time).
        """

        for story_id in DEV_STORY_SKIP_IDS:
            self._send_s2c(
                connection_id, writer, "returnStory", (int(story_id), 2, 0),
                decoded_fields={"story_id": int(story_id), "state": 2},
                classification="story_skip_after_entry",
            )

    def _replay_story_state(
        self, connection_id, writer, role, session_state, *, send_start: bool = True
    ) -> None:
        """Replay finished story ids so the city cutscenes stop repeating.

        TASK-0438: the client's pkgStoryModelMgr only knows a story's state
        from S2C 411/412/413 (or 414); C2S 233 finishStory writes its local
        state but a fresh client process starts empty, which is why every entry
        to 长安城 replayed the same dialogues.  The finished ids are persisted
        from C2S 233 and replayed here (STATE_FINISH = 2).
        """

        if role is None:
            return
        account_key = session_state.get("account_key")
        if account_key is None:
            return
        key = f"story_done:{int(role.role_id)}"
        try:
            raw = self.server.role_store.get_meta(key)
        except Exception:
            raw = None
        stored: set[int] = set()
        if raw:
            try:
                stored = {int(v) for v in json.loads(raw)}
            except (TypeError, ValueError):
                stored = set()
        # The 141-id "skip every story trigger" experiment broke the client's
        # login (it dropped the connection right after S2C 555), so only the
        # ids the client itself reported via C2S 233 are replayed.
        story_ids = sorted(stored)
        if not story_ids:
            return
        if send_start:
            self._send_s2c(
                connection_id, writer, "returnStoryStart", (),
                classification="persisted_story_state",
            )
        for story_id in story_ids:
            self._send_s2c(
                connection_id, writer, "returnStory", (story_id, 2, 0),
                decoded_fields={"story_id": story_id, "state": 2},
                classification="persisted_story_state",
            )
        # S2C 413 returnStoryEnd is deliberately unregistered (empty handler,
        # no lock partner); sending it is unnecessary and raised KeyError.

    def _replay_adv_pieces(self, connection_id, writer, role, session_state) -> None:
        """Replay the 进阶石 inventory (pkgAdvanceMaterialInfo.tbAdvPiece).

        TASK-0440: the client's checkMaterial reads silver/gold advance pieces
        from tbAdvPiece, filled only by S2C 276/277/278.  A dev session is
        seeded with 100/100 once and the counts are persisted in the profile
        meta so consumption survives a relogin.
        """

        if role is None:
            return
        account_key = session_state.get("account_key")
        if account_key is None:
            return
        key = f"adv_piece:{int(role.role_id)}"
        store = self.server.role_store
        try:
            raw = store.get_meta(key)
        except Exception:
            raw = None
        counts: dict[int, int] = {}
        if raw:
            try:
                counts = {int(k): int(v) for k, v in json.loads(raw).items()}
            except (TypeError, ValueError):
                counts = {}
        if not counts:
            counts = {ADV_PIECE_GOLD: 100, ADV_PIECE_SILVER: 100}
            try:
                store.set_meta(key, json.dumps({str(k): v for k, v in counts.items()}))
            except Exception:
                pass
        self._send_s2c(
            connection_id, writer, "returnAdvPieceStart", (),
            classification="persisted_adv_piece_state",
        )
        for cfg_id in sorted(counts):
            self._send_s2c(
                connection_id, writer, "returnAdvPieceInfo",
                (cfg_id, counts[cfg_id]),
                decoded_fields={"cfg_id": cfg_id, "count": counts[cfg_id]},
                classification="persisted_adv_piece_state",
            )
        self._send_s2c(
            connection_id, writer, "returnAdvPieceEnd", (),
            classification="persisted_adv_piece_state",
        )

    def _shop_meta_key(self, role) -> str:
        return f"shop:{int(role.role_id)}"

    def _load_shop_state(self, role) -> dict:
        try:
            raw = self.server.role_store.get_meta(self._shop_meta_key(role))
        except Exception:
            raw = None
        if not raw:
            return {}
        try:
            state = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _save_shop_state(self, role, state: dict) -> None:
        try:
            self.server.role_store.set_meta(
                self._shop_meta_key(role), json.dumps(state, sort_keys=True)
            )
        except Exception:
            pass

    def _roll_shop_slots(self, d_type: int, role_level: int) -> list[dict]:
        cfg = SHOP_CONFIG.get(int(d_type))
        if cfg is None:
            return []
        rng = SystemRandom()
        slots: list[dict] = []
        for grid in cfg["grids"]:
            rows = SHOP_GOODS.get(int(grid), {})
            eligible = []
            for goods_id, row in rows.items():
                role_lv, end_lv = int(row[6]), int(row[7])
                if role_lv and role_level < role_lv:
                    continue
                if end_lv and role_level > end_lv:
                    continue
                eligible.append((int(goods_id), row))
            if not eligible:
                slots.append({"grid": int(grid), "goods": 0, "sold": False})
                continue
            total = sum(max(1, int(row[5])) for _, row in eligible)
            pick = rng.randrange(total)
            chosen_id = eligible[-1][0]
            for goods_id, row in eligible:
                weight = max(1, int(row[5]))
                if pick < weight:
                    chosen_id = goods_id
                    break
                pick -= weight
            slots.append({"grid": int(grid), "goods": chosen_id, "sold": False})
        return slots

    def _send_shop_slots(self, connection_id, writer, d_type, entry) -> None:
        self._send_s2c(
            connection_id,
            writer,
            "refreshShopStart",
            (int(d_type), int(entry.get("time") or int(time.time()))),
            classification="shop_snapshot",
        )
        for slot in entry.get("slots") or []:
            self._send_s2c(
                connection_id,
                writer,
                "refreshGoods",
                (
                    int(d_type),
                    int(slot.get("goods") or 0),
                    1 if slot.get("sold") else 0,
                ),
                classification="shop_snapshot",
            )
        self._send_s2c(
            connection_id, writer, "refreshShopEnd", (int(d_type),),
            classification="shop_snapshot",
        )

    def _shop_entry(self, role, d_type: int):
        state = self._load_shop_state(role)
        entry = state.get(str(int(d_type)))
        if not isinstance(entry, dict):
            entry = None
        return state, entry

    def _handle_shop_open(self, connection_id, writer, role, session_state, d_type: int) -> None:
        if role is None:
            return
        cfg = SHOP_CONFIG.get(int(d_type))
        if cfg is None:
            return
        state, entry = self._shop_entry(role, d_type)
        if entry is None or not entry.get("slots"):
            role_level = max(int(getattr(role, "level", 60) or 60), 60)
            entry = {
                "time": int(time.time()),
                "slots": self._roll_shop_slots(d_type, role_level),
            }
            state[str(int(d_type))] = entry
            self._save_shop_state(role, state)
        self._send_shop_slots(connection_id, writer, d_type, entry)
        self.server.event_logger.emit(
            "shop_opened",
            connection_id=connection_id,
            role_id=int(role.role_id),
            shop_type=int(d_type),
            shop_name=cfg.get("name"),
            slot_count=len(entry.get("slots") or []),
            provenance="shipped_shopCfg_shopGoods_roll",
        )

    def _handle_shop_refresh(self, connection_id, writer, role, session_state, d_type: int) -> None:
        if role is None:
            return
        cfg = SHOP_CONFIG.get(int(d_type))
        if cfg is None:
            return
        refresh_cut = int(cfg.get("refresh_cut") or 0)
        if refresh_cut:
            funds = self._shop_funds(session_state, 7)
            if funds is None or funds < refresh_cut:
                self._send_s2c(
                    connection_id, writer, "refreshShopResult",
                    (int(d_type), SHOP_STATUS_FAIL),
                    classification="shop_refresh_no_funds",
                )
                return
            if not self._shop_spend(session_state, 7, refresh_cut):
                self._send_s2c(
                    connection_id, writer, "refreshShopResult",
                    (int(d_type), SHOP_STATUS_FAIL),
                    classification="shop_refresh_spend_failed",
                )
                return
        role_level = max(int(getattr(role, "level", 60) or 60), 60)
        state, _ = self._shop_entry(role, d_type)
        entry = {
            "time": int(time.time()),
            "slots": self._roll_shop_slots(d_type, role_level),
        }
        state[str(int(d_type))] = entry
        self._save_shop_state(role, state)
        self._send_s2c(
            connection_id, writer, "refreshShopResult",
            (int(d_type), SHOP_REFRESH_SUCCESS),
            classification="shop_refresh",
        )
        self._send_shop_slots(connection_id, writer, d_type, entry)

    def _shop_can_grant(self, reward_type: int) -> bool:
        return (
            reward_type == SHOP_CARD_REWARD_TYPE
            or reward_type in SHOP_PIECE_REWARD_TYPES
            or reward_type in SHOP_WALLET_CUT_TYPES
        )

    def _shop_funds(self, session_state, cut_type: int) -> float | None:
        name = SHOP_WALLET_CUT_TYPES.get(int(cut_type))
        account_key = session_state.get("account_key")
        if name is None or account_key is None:
            return None
        wallet = self.server.role_store.role_wallet_values(account_key=account_key)
        return float(wallet.get(name, 0))

    def _shop_grant(self, role, session_state, reward_type: int, cfg_id: int, count: int) -> bool:
        store = self.server.role_store
        record = session_state.get("role_record")
        account_key = session_state.get("account_key")
        try:
            if reward_type == SHOP_CARD_REWARD_TYPE:
                instance_id = (
                    f"shop-{int(role.role_id)}-{int(time.time() * 1000)}-{int(cfg_id)}"
                )
                store.record_card_instance(
                    record=record,
                    instance_id=instance_id,
                    card_cfg_id=int(cfg_id),
                    level=1,
                    advance_level=0,
                    origin="shop_purchase",
                )
                return True
            if reward_type in SHOP_PIECE_REWARD_TYPES:
                store.add_card_piece(
                    account_key=account_key,
                    card_cfg_id=int(cfg_id),
                    count=int(count),
                    provenance="shop_purchase",
                )
                return True
            name = SHOP_WALLET_CUT_TYPES.get(int(reward_type))
            if name is not None:
                store.adjust_role_wallet(
                    record=record,
                    value_name=name,
                    delta=float(count),
                    provenance="shop_purchase",
                )
                return True
        except Exception:
            return False
        return False

    def _shop_spend(self, session_state, cut_type: int, amount: int) -> bool:
        name = SHOP_WALLET_CUT_TYPES.get(int(cut_type))
        record = session_state.get("role_record")
        if name is None or record is None:
            return False
        try:
            self.server.role_store.adjust_role_wallet(
                record=record,
                value_name=name,
                delta=-float(amount),
                provenance="shop_purchase",
            )
        except Exception:
            return False
        return True

    def _send_shop_gain(self, connection_id, writer, reward_type: int, cfg_id: int, count: int) -> None:
        self._send_s2c(
            connection_id, writer, "returnGainInfoStart",
            (SHOP_GAIN_TYPE_CITY_SHOP,), classification="shop_gain",
        )
        self._send_s2c(
            connection_id, writer, "returnGainInfo",
            (int(reward_type), int(cfg_id), "", int(count)),
            classification="shop_gain",
        )
        self._send_s2c(
            connection_id, writer, "returnGainInfoEnd", (), classification="shop_gain",
        )

    def _handle_shop_buy(self, connection_id, writer, role, session_state, fields) -> None:
        d_type = int(fields[0])
        d_pos = int(fields[1])
        if role is None:
            return
        state, entry = self._shop_entry(role, d_type)
        slots = (entry or {}).get("slots") or []
        if d_pos < 1 or d_pos > len(slots):
            self._send_s2c(
                connection_id, writer, "buyGoodsResult",
                (d_type, d_pos, SHOP_STATUS_FAIL), classification="shop_buy_refused",
            )
            return
        slot = slots[d_pos - 1]
        if slot.get("sold"):
            self._send_s2c(
                connection_id, writer, "buyGoodsResult",
                (d_type, d_pos, SHOP_STATUS_FAIL), classification="shop_buy_sold_out",
            )
            return
        goods = SHOP_GOODS.get(int(slot.get("grid") or 0), {}).get(
            int(slot.get("goods") or 0)
        )
        if goods is None:
            self._send_s2c(
                connection_id, writer, "buyGoodsResult",
                (d_type, d_pos, SHOP_STATUS_FAIL), classification="shop_buy_unmapped",
            )
            return
        reward_type, cfg_id, count, cut_type, cut_num = (
            int(goods[0]), int(goods[1]), int(goods[2]), int(goods[3]), int(goods[4])
        )
        if not self._shop_can_grant(reward_type):
            self._send_s2c(
                connection_id, writer, "buyGoodsResult",
                (d_type, d_pos, SHOP_STATUS_FAIL), classification="shop_buy_unsupported_reward",
            )
            self.server.event_logger.emit(
                "shop_buy_unsupported",
                connection_id=connection_id,
                role_id=int(role.role_id),
                shop_type=d_type,
                position=d_pos,
                reward_type=reward_type,
                cfg_id=cfg_id,
                reason="no recovered inventory for this reward type",
            )
            return
        funds = self._shop_funds(session_state, cut_type)
        if funds is None or funds < cut_num:
            self._send_s2c(
                connection_id, writer, "buyGoodsResult",
                (d_type, d_pos, SHOP_STATUS_FAIL), classification="shop_buy_no_funds",
            )
            return
        if not self._shop_grant(role, session_state, reward_type, cfg_id, count):
            self._send_s2c(
                connection_id, writer, "buyGoodsResult",
                (d_type, d_pos, SHOP_STATUS_FAIL), classification="shop_buy_grant_failed",
            )
            return
        if not self._shop_spend(session_state, cut_type, cut_num):
            self._send_s2c(
                connection_id, writer, "buyGoodsResult",
                (d_type, d_pos, SHOP_STATUS_FAIL), classification="shop_buy_spend_failed",
            )
            return
        slot["sold"] = True
        state[str(int(d_type))] = entry
        self._save_shop_state(role, state)
        self._send_shop_gain(connection_id, writer, reward_type, cfg_id, count)
        self._send_s2c(
            connection_id, writer, "buyGoodsResult",
            (d_type, d_pos, SHOP_STATUS_SUCCESS), classification="shop_buy_ok",
        )
        self.server.event_logger.emit(
            "shop_goods_bought",
            connection_id=connection_id,
            role_id=int(role.role_id),
            shop_type=d_type,
            position=d_pos,
            reward_type=reward_type,
            cfg_id=cfg_id,
            count=count,
            cut_type=cut_type,
            cut_num=cut_num,
        )

    def _handle_shop_packet(self, connection_id, writer, session_state, packet) -> None:
        role = session_state.get("role")
        if packet.spec.name == "openShop":
            self._handle_shop_open(
                connection_id, writer, role, session_state, int(packet.fields[0])
            )
        elif packet.spec.name == "refreshShop":
            self._handle_shop_refresh(
                connection_id, writer, role, session_state, int(packet.fields[0])
            )
        elif packet.spec.name == "buyGoods":
            self._handle_shop_buy(
                connection_id, writer, role, session_state, packet.fields
            )

    def _grant_gacha_fodder(self, connection_id, role, session_state) -> None:
        """Give the dev session five 妖猴大圣·林 (cfg 4522) fodder copies.

        TASK-0437: the client refuses a fodder card that is in the team, and the
        Owner's 妖猴大圣·林 attempt used its team card as material, so the
        evolve button failed client-side without a C2S.  These copies are level
        10, advance 0 and never enter the team; each row is labelled synthetic.
        """

        if role is None:
            return
        if not (
            self.server.dev_play_gacha
            and self.server.dev_play_gacha_profile_matches
        ):
            return
        account_key = session_state.get("account_key")
        record = session_state.get("role_record")
        if account_key is None or record is None:
            return
        store = self.server.role_store
        for index in range(1, 6):
            instance_id = f"dev-fodder-4522-{index:02d}"
            try:
                if store.card_instance(
                    account_key=account_key, instance_id=instance_id
                ) is not None:
                    continue
                store.record_card_instance(
                    record=record,
                    instance_id=instance_id,
                    card_cfg_id=4522,
                    level=10,
                    advance_level=0,
                    sfm_id1=0,
                    origin="synthetic_dev_play_gacha_fodder",
                )
            except Exception:
                pass
        probe_id = "dev-awaken-6516"
        try:
            if store.card_instance(
                account_key=account_key, instance_id=probe_id
            ) is None:
                store.record_card_instance(
                    record=record,
                    instance_id=probe_id,
                    card_cfg_id=6516,
                    level=10,
                    advance_level=0,
                    sfm_id1=0,
                    origin="synthetic_dev_awaken_probe",
                )
        except Exception:
            pass

    def _send_dev_mail_snapshot(self, connection_id, writer) -> None:
        """Send one real welcome mail so the mail panel is not empty (TASK-0464)."""

        mail_id = 1
        self._send_s2c(connection_id, writer, "returnMailStart", (),
                       classification="synthetic_dev_mail")
        self._send_s2c(
            connection_id, writer, "returnSingleMail",
            (mail_id, int(time.time()), 0,
             "长安幻世绘", "欢迎回来",
             "捉妖师，长安城已经为你准备好了。"),
            classification="synthetic_dev_mail")
        # S2C 256 swaps le_tbMailDict = le_tbTempItemDict, so the row must
        # still be in the TEMP dict here; sending 259 (which moves it into the
        # old mail dict) first makes 256 discard it again -- that is why the
        # panel stayed empty after TASK-0464.
        self._send_s2c(connection_id, writer, "returnMailEnd", (),
                       classification="synthetic_dev_mail")

    def _replay_npc_tasks(self, connection_id, writer, role, session_state) -> None:
        """Replay the persisted NPC task states at login (TASK-0436)."""

        if role is None:
            return
        account_key = session_state.get("account_key")
        if account_key is None:
            return
        key = f"npc_task:{int(role.role_id)}"
        try:
            raw = self.server.role_store.get_meta(key)
        except Exception:
            raw = None
        if not raw:
            return
        try:
            tasks = {int(k): int(v) for k, v in json.loads(raw).items()}
        except (TypeError, ValueError):
            return
        if not tasks:
            return
        self._send_s2c(
            connection_id, writer, "returnNpcTaskStart", (),
            classification="persisted_npc_task_state",
        )
        for task_id in sorted(tasks):
            self._send_s2c(
                connection_id, writer, "returnNpcTaskData",
                (task_id, tasks[task_id]),
                decoded_fields={"task_id": task_id, "state": tasks[task_id]},
                classification="persisted_npc_task_state",
            )
        self._send_s2c(
            connection_id, writer, "returnNpcTaskEnd", (),
            classification="persisted_npc_task_state",
        )

    def _handle_npc_task_receive(
        self, connection_id, writer, packet, session_state
    ) -> None:
        """Persist one accepted NPC task and acknowledge it (TASK-0436).

        The client only fills le_taskDict from S2C 567/568/569, and C2S 294
        receiveNpcTask is the accept path.  Without a stored state every login
        restarted the city NPC dialogues; this writes state 20 (RECEIVED_TASK)
        under the profile meta and answers S2C 566 updateSingleNpcTask.
        """

        role = session_state.get("role")
        account_key = session_state.get("account_key")
        task_id = int(packet.fields[0])
        tutorial_unlock = (
            task_id in NPC_TASK_TUTORIAL_IDS
            and self.server.dev_play_gacha
            and self.server.dev_play_gacha_profile_matches
        )
        state = 21 if tutorial_unlock else 20
        if role is not None and account_key is not None and task_id > 0:
            key = f"npc_task:{int(role.role_id)}"
            store = self.server.role_store
            try:
                raw = store.get_meta(key)
            except Exception:
                raw = None
            tasks = {}
            if raw:
                try:
                    tasks = {
                        int(k): int(v) for k, v in json.loads(raw).items()
                    }
                except (TypeError, ValueError):
                    tasks = {}
            tasks[task_id] = state
            try:
                store.set_meta(key, json.dumps({str(k): v for k, v in tasks.items()},
                                               sort_keys=True))
            except Exception:
                pass
            self.server.event_logger.emit(
                "tcp_npc_task_received",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                role_id=role.role_id,
                task_id=task_id,
                state=state,
                tutorial_unlock=tutorial_unlock,
                persisted=True,
            )
        self._send_s2c(
            connection_id, writer, "updateSingleNpcTask", (task_id, state),
            decoded_fields={"task_id": task_id, "state": state},
            classification="persisted_npc_task_state",
        )

    def _handle_soul_talent_packet(
        self, connection_id, writer, packet, session_state
    ) -> None:
        """Answer the 元魂珠/天赋 lock-sends (TASK-0434).

        activeSoulTalent(talentId, counter) activates the personal 元魂 node
        (talentNode id 5, soulId1 2202); its lock partner is S2C 554
        returnTalentSoulId, which is also what sets the client's le_dSoulId.
        S2C 553 returnTalentSoulNode carries the node id back.  selectSoulSkill
        answers S2C 555 with the soul skill cfg (11001 for soul 101), and the
        show-mode request answers S2C 557.  The values are the shipped cfg
        rows; the learned state itself is not persisted (no recovered store).
        """

        role = session_state.get("role")
        compatibility = role.compatibility if role is not None else None
        soul_id = 0
        if compatibility is not None:
            soul_id = int(getattr(compatibility, "active_soul", 0) or 0)
        if soul_id <= 0:
            soul_id = 101
        name = packet.spec.name
        if name == "activeSoulTalent":
            talent_id = int(packet.fields[0])
            self._send_s2c(
                connection_id, writer, "returnTalentSoulNode", (talent_id,),
                decoded_fields={"talent_id": talent_id},
                classification="synthetic_dev_play_soul_talent",
            )
            self._send_s2c(
                connection_id, writer, "returnTalentSoulId", (soul_id,),
                decoded_fields={"soul_id": soul_id},
                classification="synthetic_dev_play_soul_talent",
            )
            self.server.event_logger.emit(
                "tcp_soul_talent_activated",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                talent_id=talent_id,
                soul_id=soul_id,
                persisted=False,
            )
            return
        if name == "selectSoulSkill":
            index = int(packet.fields[0])
            skill_id = 11001
            self._send_s2c(
                connection_id, writer, "returnTalentSoulSkill", (skill_id,),
                decoded_fields={"skill_index": index, "skill_id": skill_id},
                classification="synthetic_dev_play_soul_skill",
            )
            return
        mode = int(packet.fields[0])
        self._send_s2c(
            connection_id, writer, "returnTalentSoulShowMode", (mode,),
            decoded_fields={"show_mode": mode},
            classification="synthetic_dev_play_soul_show_mode",
        )

    def _handle_card_advance_packet(
        self, connection_id, writer, packet, session_state
    ) -> None:
        """C2S 18/19/20/21: the client's own card-advance (进阶) chain.

        ``advanceCardStart`` opens the selection; ``advanceCard`` names one
        fodder card instance per packet; ``advanceAdvCard`` names one advance-card
        special-card cfg id per packet; ``advanceCardEnd`` (a lock-send whose
        partner is S2C 71) carries the target instance id and the client's own
        ``ADVANCE_CARD`` sync counter.  The server re-validates everything the
        client's ``checkMaterial`` checked, charges the shipped
        ``cardAdvance.max`` row and writes the new ``advance_level`` in one
        transaction, then sends the full 21-value card row before the result so
        the bag/team views read the new level as they do after a strength.

        The shipped table's ``silverPiece``/``goldPiece`` columns name advance
        pieces this archive has no inventory for (the client reads them from
        ``pkgAdvanceMaterialInfo``, fed only by material protocols this stub does
        not implement), and the recovered client contract does not state how
        many special-card units one ``advanceAdvCard`` selection consumes.  Both
        are refused by name and the whole request is left untouched -- never a
        half-charge.
        """

        name = packet.spec.name
        if name == "advanceCardStart":
            session_state["card_advance"] = {"cards": [], "adv_cards": []}
            return
        if name == "advanceCard":
            session = session_state.setdefault(
                "card_advance", {"cards": [], "adv_cards": []}
            )
            session["cards"].append(_text(packet.fields[0]))
            return
        if name == "advanceAdvCard":
            session = session_state.setdefault(
                "card_advance", {"cards": [], "adv_cards": []}
            )
            session["adv_cards"].append(int(packet.fields[0]))
            return

        session = session_state.pop("card_advance", None)
        role, role_record, account_key = self._growth_account(session_state)
        target = _text(packet.fields[0]).strip()
        counter = int(packet.fields[1])
        if role is None or role_record is None:
            self._advance_refusal(
                connection_id, writer, session_state, target, "no_bound_role"
            )
            return
        if not target:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_NO_TARGET,
            )
            return
        store = self.server.role_store
        try:
            next_counter = store.next_advance_sync_counter(
                account_key=account_key, role_id=role.role_id
            )
        except RoleStoreError as exc:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_STORE,
                detail=str(exc)[:240],
            )
            return
        if counter < next_counter:
            try:
                prior = store.card_advance_request(
                    account_key=account_key,
                    role_id=role.role_id,
                    sync_counter=counter,
                )
            except RoleStoreError as exc:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_STORE,
                    detail=str(exc)[:240],
                )
                return
            current = store.card_instance(
                account_key=account_key, instance_id=target
            )
            if prior is not None and prior.instance_id == target and current is not None:
                # A transport retry of an already-committed advance.  Replay its
                # success without touching the card or the wallet.
                self._send_s2c(
                    connection_id,
                    writer,
                    "updateCard",
                    current.return_card_info_fields(),
                    decoded_fields={
                        "card_instance_id": current.instance_id,
                        "card_advance_level": current.advance_level,
                    },
                    classification="card_advance_replayed",
                )
                self._send_s2c(
                    connection_id,
                    writer,
                    "returnAdvanceCardResult",
                    (target, True),
                    decoded_fields={"card_instance_id": target, "result": True},
                    classification="card_advance_replayed",
                )
                self._send_s2c(
                    connection_id,
                    writer,
                    "updateSyncCounter",
                    (ADVANCE_CARD_SYNC_COUNTER_TYPE, next_counter),
                    decoded_fields={
                        "counter_type": ADVANCE_CARD_SYNC_COUNTER_TYPE,
                        "counter_value": next_counter,
                    },
                    classification="card_advance_sync_counter",
                )
                self.server.event_logger.emit(
                    "tcp_card_advance_replayed",
                    connection_id=connection_id,
                    remote_endpoint=(
                        f"{self.client_address[0]}:{self.client_address[1]}"
                    ),
                    role_id=role.role_id,
                    account_key_prefix=account_key[:8],
                    card_instance_id=target,
                    sync_counter=counter,
                    next_sync_counter=next_counter,
                    previous_request=prior.evidence_fields(),
                    action="idempotent_replay",
                    state_changed=False,
                )
                return
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_COUNTER_STALE,
                sync_counter=counter,
                expected_sync_counter=next_counter,
            )
            return
        if counter > next_counter:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_COUNTER_AHEAD,
                sync_counter=counter,
                expected_sync_counter=next_counter,
            )
            self._send_s2c(
                connection_id,
                writer,
                "updateSyncCounter",
                (ADVANCE_CARD_SYNC_COUNTER_TYPE, next_counter),
                decoded_fields={
                    "counter_type": ADVANCE_CARD_SYNC_COUNTER_TYPE,
                    "counter_value": next_counter,
                },
                classification="card_advance_sync_counter",
            )
            return
        if session is None:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_NO_CHAIN,
                sync_counter=counter,
            )
            return

        target_row = store.card_instance(account_key=account_key, instance_id=target)
        if target_row is None:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_UNOWNED_TARGET,
                sync_counter=counter,
            )
            return
        try:
            star, rare = card_star_rare(target_row.card_cfg_id)
        except KeyError:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_UNMAPPED_CONFIG,
                sync_counter=counter,
                card_cfg_id=target_row.card_cfg_id,
            )
            return
        if not ADVANCE_STAR_MIN <= star < ADVANCE_STAR_MAX_EXCLUSIVE:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_STAR,
                sync_counter=counter,
                card_star=star,
            )
            return
        if target_row.level != MAX_STRENGTH_LEVEL:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_LEVEL_NOT_MAX,
                sync_counter=counter,
                card_level=target_row.level,
                required_level=MAX_STRENGTH_LEVEL,
            )
            return
        maximum = advance_max_level(star)
        star_up = False
        if target_row.advance_level >= maximum:
            # TASK-0485: a card already at its max advance is the 进化
            # (star-up) request: move it to cfg+1000 and reset advance_level
            # instead of refusing.  Only 6-star cards have nowhere to go.
            if star >= ADVANCE_STAR_MAX_EXCLUSIVE:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_LEVEL_MAX,
                    sync_counter=counter,
                    advance_level=target_row.advance_level,
                    max_advance_level=maximum,
                )
                return
            star_up = True
        target_level = target_row.advance_level + 1
        if star_up:
            money = yinpo = jinpo = silver = gold = card_num = 0
        try:
            if not star_up:
                money, yinpo, jinpo, silver, gold, card_num = advance_cost(
                    star, rare, target_level
                )
        except KeyError:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_COST_MISSING,
                sync_counter=counter,
                card_star=star,
                card_rare=rare,
                target_advance_level=target_level,
            )
            return
        adv_pieces: tuple[tuple[int, int], ...] = ()
        if (silver or gold) and not star_up:
            # TASK-0440: the shipped row charges 进阶石; the client reads those
            # counts from pkgAdvanceMaterialInfo (S2C 276/277/278) and refuses to
            # send C2S 21 when they are 0, so the server must own the inventory.
            piece_key = f"adv_piece:{int(role.role_id)}"
            try:
                raw_pieces = store.get_meta(piece_key)
            except Exception:
                raw_pieces = None
            piece_counts: dict[int, int] = {}
            if raw_pieces:
                try:
                    piece_counts = {
                        int(k): int(v) for k, v in json.loads(raw_pieces).items()
                    }
                except (TypeError, ValueError):
                    piece_counts = {}
            need: dict[int, int] = {}
            if silver:
                need[ADV_PIECE_SILVER] = int(silver)
            if gold:
                need[ADV_PIECE_GOLD] = int(gold)
            for piece_cfg, units in need.items():
                if int(piece_counts.get(piece_cfg, 0)) < units:
                    self._advance_refusal(
                        connection_id, writer, session_state, target,
                        CARD_ADVANCE_REFUSAL_ADV_PIECE,
                        sync_counter=counter,
                        target_advance_level=target_level,
                        piece_cfg_id=piece_cfg,
                        required_units=units,
                        available_units=int(piece_counts.get(piece_cfg, 0)),
                    )
                    return
            adv_pieces = tuple(need.items())
        fodder = () if star_up else tuple(dict.fromkeys(session["cards"]))
        special_cards = []
        for cfg_id in session["adv_cards"]:
            special_star = ADVANCE_SPECIAL_CARD_STARS.get(int(cfg_id))
            if special_star is None:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_ADV_CARD_SLOT,
                    sync_counter=counter,
                    advance_card_cfg_id=int(cfg_id),
                )
                return
            if special_star != star:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_SPECIAL_CARD_STAR,
                    sync_counter=counter,
                    advance_card_cfg_id=int(cfg_id),
                    advance_card_star=special_star,
                    target_star=star,
                )
                return
            special_cards.append(int(cfg_id))
        if len(fodder) + len(special_cards) != card_num:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_MATERIAL_COUNT,
                sync_counter=counter,
                material_ids=list(fodder),
                special_card_cfg_ids=special_cards,
                required_card_num=card_num,
            )
            return
        if special_cards:
            special_counts = store.card_special_counts(account_key=account_key)
            needed: dict[int, int] = {}
            for cfg_id in special_cards:
                needed[cfg_id] = needed.get(cfg_id, 0) + ADVANCE_SPECIAL_CARD_UNITS
            for cfg_id, units in needed.items():
                if int(special_counts.get(cfg_id, 0)) < units:
                    self._advance_refusal(
                        connection_id, writer, session_state, target,
                        CARD_ADVANCE_REFUSAL_SPECIAL_CARD_UNITS,
                        sync_counter=counter,
                        advance_card_cfg_id=cfg_id,
                        required_units=units,
                        available_units=int(special_counts.get(cfg_id, 0)),
                    )
                    return
        deployed = self._team_card_ids(account_key)
        for instance_id in fodder:
            if instance_id == target:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_MATERIAL_SELF,
                    sync_counter=counter,
                )
                return
            row = store.card_instance(
                account_key=account_key, instance_id=instance_id
            )
            if row is None:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_MATERIAL_UNOWNED,
                    sync_counter=counter,
                    material_id=instance_id,
                )
                return
            if row.card_cfg_id != target_row.card_cfg_id:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_MATERIAL_WRONG_CARD,
                    sync_counter=counter,
                    material_id=instance_id,
                    material_cfg_id=row.card_cfg_id,
                    target_cfg_id=target_row.card_cfg_id,
                )
                return
            if row.locked or instance_id in deployed:
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    CARD_ADVANCE_REFUSAL_MATERIAL_PROTECTED,
                    sync_counter=counter,
                    material_id=instance_id,
                )
                return

        wallet = store.role_wallet_values(account_key=account_key)
        for name, amount, refusal in (
            ("money", money, CARD_ADVANCE_REFUSAL_MONEY),
            ("yinpo", yinpo, CARD_ADVANCE_REFUSAL_YINPO),
            ("jinpo", jinpo, CARD_ADVANCE_REFUSAL_JINPO),
        ):
            if amount > wallet.get(name, 0.0):
                self._advance_refusal(
                    connection_id, writer, session_state, target,
                    refusal,
                    sync_counter=counter,
                    required=amount,
                    available=wallet.get(name, 0.0),
                )
                return

        # TASK-0439: the max advance level is the star-up (进化): move the card
        # to cfg+1000 and reset advance_level, which is what the client's
        # 5★+0 preview means.  Anything else writes an invalid 4★+4 row that
        # the client cannot render (bag/team stop opening).
        new_card_cfg_id = None
        if star_up or target_level >= advance_max_level(star):
            new_card_cfg_id = int(target_row.card_cfg_id) + 1000
        try:
            updated, wallet_after, request_record = store.apply_card_advance(
                record=role_record,
                target_instance_id=target,
                expected_advance_level=target_row.advance_level,
                new_advance_level=target_level,
                fodder_ids=fodder,
                money=money,
                yinpo=yinpo,
                jinpo=jinpo,
                sync_counter=counter,
                provenance="revival_local_c2s18_21_advance",
                special_fodder=tuple(
                    (cfg_id, ADVANCE_SPECIAL_CARD_UNITS)
                    for cfg_id in special_cards
                ),
                new_card_cfg_id=new_card_cfg_id,
                adv_pieces=adv_pieces,
            )
        except RoleStoreError as exc:
            self._advance_refusal(
                connection_id, writer, session_state, target,
                CARD_ADVANCE_REFUSAL_STORE,
                sync_counter=counter,
                detail=str(exc)[:240],
            )
            return

        if star_up:
            # TASK-0485: the evolved cfg carries its own sfmId1..3 and, at
            # 6-star, the awakening slot the client's fourth skill needs.
            try:
                _base = dev_play_refine.base_skills(updated.card_cfg_id)
                _four = dev_play_refine.initial_four(updated.card_cfg_id)
                if any(int(v) for v in _base) or _four:
                    updated = store.set_card_skills(
                        account_key=account_key,
                        instance_id=target,
                        sfm_id1=int(_base[0]),
                        sfm_id2=int(_base[1]),
                        sfm_id3=int(_base[2]),
                        sfm_id4=int(_four or updated.sfm_id4),
                        provenance="revival_local_star_up_skills",
                    )
            except Exception:
                pass

        self._send_s2c(
            connection_id,
            writer,
            "updateCard",
            updated.return_card_info_fields(),
            decoded_fields={
                "card_instance_id": updated.instance_id,
                "card_level": updated.level,
                "card_advance_level": updated.advance_level,
            },
            classification="card_advance_applied",
        )
        self._send_s2c(
            connection_id,
            writer,
            "returnOneValue_p",
            (7, float(wallet_after["money"])),
            decoded_fields={
                "reward_type": 7,
                "amount": float(wallet_after["money"]),
            },
            classification="card_advance_wallet",
        )
        self._send_s2c(
            connection_id,
            writer,
            "returnOneValue_N",
            (28, int(wallet_after["yinpo"])),
            classification="card_advance_wallet",
        )
        self._send_s2c(
            connection_id,
            writer,
            "returnOneValue_N",
            (29, int(wallet_after["jinpo"])),
            classification="card_advance_wallet",
        )
        self._send_s2c(
            connection_id,
            writer,
            "returnAdvanceCardResult",
            (target, True),
            decoded_fields={
                "card_instance_id": target,
                "result": True,
                "advance_level_before": request_record.advance_level_from,
                "advance_level_after": request_record.advance_level_to,
            },
            classification="card_advance_applied",
        )
        self._send_s2c(
            connection_id,
            writer,
            "updateSyncCounter",
            (ADVANCE_CARD_SYNC_COUNTER_TYPE, next_counter + 1),
            decoded_fields={
                "counter_type": ADVANCE_CARD_SYNC_COUNTER_TYPE,
                "counter_value": next_counter + 1,
            },
            classification="card_advance_sync_counter",
        )
        self.server.event_logger.emit(
            "tcp_card_advance_applied",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            account_key_prefix=account_key[:8],
            card_instance_id=target,
            card_cfg_id=updated.card_cfg_id,
            card_star=star,
            card_rare=rare,
            advance_level_before=request_record.advance_level_from,
            advance_level_after=request_record.advance_level_to,
            money_spent=money,
            yinpo_spent=yinpo,
            jinpo_spent=jinpo,
            silver_piece_spent=0,
            gold_piece_spent=0,
            fodder_ids=list(fodder),
            sync_counter=counter,
            next_sync_counter=next_counter + 1,
            wallet_after=wallet_after,
            cost_source="shipped_cardAdvance_max",
            advance_cost_digest=ADVANCE_COST_DIGEST,
            sync_counter_policy="server_advances_recovered_client_counter",
            provenance="revival_local_c2s18_21_advance",
            state_changed=True,
        )
        # TASK-0245 / #27: the advance changed this card's fight ratio only when
        # the card is in the preset the client is showing.
        group, team_ids = self._client_team_cards(
            session_state, role, role_record
        )
        if target in team_ids:
            self._send_team_max_fight(
                connection_id,
                writer,
                role,
                role_record,
                session_state,
                trigger="cardAdvance",
                group_id=group,
                card_ids=team_ids,
                event_extra={
                    "card_instance_id": target,
                    "advance_level_after": request_record.advance_level_to,
                },
            )

    def _handle_team_update(
        self,
        connection_id: str,
        writer: WriterState,
        remote: str,
        session_state: dict[str, Any],
        packet,
    ) -> None:
        """C2S 6 `updateTeam`: persist one complete preset and echo S2C 40.

        The registered format is `N` + 14 `a`: one group id, four equipment
        ids and ten card ids.  The eleventh card slot is the client's own
        level-999 slot and is preserved from the stored row instead of being
        invented.  The god weapon is likewise preserved: the C2S format does
        not carry it.  A request that names a card this account does not own is
        refused with the client's own S2C 41 error partner, which also releases
        its 15 s lock.
        """

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }

        def refuse(reason: str, detail: str = "") -> None:
            response = self._send_s2c(
                connection_id,
                writer,
                "updateTeamError",
                (),
                classification="team_update_refused",
            )
            self.server.event_logger.emit(
                "tcp_team_update_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                response_protocol_id=response.spec.protocol_id,
                response_sequence=response.sequence,
                reason=reason,
                detail=detail[:240],
                recorded=False,
                **extra,
            )

        if role is None or role_record is None:
            refuse("unbound_role")
            return
        values = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        if len(values) != 1 + TEAM_SNAPSHOT_EQUIP_SLOTS + 10:
            refuse("wire_field_count", f"got {len(values)} values")
            return
        try:
            group = int(values[0])
        except (TypeError, ValueError):
            refuse("unusable_group_id", repr(values[0]))
            return
        if not 1 <= group <= TEAM_MAX_GROUPS:
            refuse("group_out_of_range", str(group))
            return
        equips = tuple(str(value or "") for value in values[1:5])
        wire_cards = tuple(str(value or "").strip() for value in values[5:])
        stored = self.server.role_store.team_preset(
            account_key=role_record.account_key, group_id=group
        )
        # Slot 11 is never editable at any role level (TB_CARD_OPEN_LEVEL[11]
        # = 999); preserve whatever the stored record carried instead of
        # truncating the row.  The god weapon is not in the C2S format.
        god_weapon_id = stored.god_weapon_id if stored is not None else ""
        slot11 = stored.card_ids[TEAM_CARD_SLOTS - 1] if stored is not None else ""
        card_ids = wire_cards + (slot11,)
        try:
            record = self.server.role_store.record_team_preset(
                record=role_record,
                group_id=group,
                equip_ids=equips,
                god_weapon_id=god_weapon_id,
                card_ids=card_ids,
                origin=TEAM_RECORD_ORIGIN_LOCAL_C2S_UPDATE,
            )
        except RoleStoreError as exc:
            refuse("store_refused", str(exc))
            return
        response = self._send_s2c(
            connection_id,
            writer,
            "updateTeam",
            record.team_info_fields(),
            decoded_fields={"dGroupID": record.group_id},
            classification="persisted_team_update",
        )
        self.server.event_logger.emit(
            "tcp_team_update_recorded",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            response_protocol_id=response.spec.protocol_id,
            response_sequence=response.sequence,
            recorded=True,
            wire_card_ids_sent=[value for value in wire_cards if value],
            preserved_slot11=card_ids[TEAM_CARD_SLOTS - 1],
            lock_partner="updateTeam",
            **record.evidence_fields(),
            **extra,
        )
        # TASK-0245 / #27: the edited preset is now what the client shows, so
        # re-send the battle-power gate value after the S2C 40 echo.
        session_state.setdefault("team_client_card_ids", {})[
            record.group_id
        ] = tuple(record.card_ids)
        state = self.server.role_store.team_state(
            account_key=role_record.account_key
        )
        snapshot_policy = (
            role.compatibility.profile.team_snapshot
            if role is not None and role.compatibility is not None
            else None
        )
        current_group = (
            state.current_group
            if state is not None
            else (
                snapshot_policy.current_group if snapshot_policy is not None else 1
            )
        )
        if record.group_id == current_group:
            self._send_team_max_fight(
                connection_id,
                writer,
                role,
                role_record,
                session_state,
                trigger="updateTeam",
                group_id=record.group_id,
                card_ids=tuple(record.card_ids),
                event_extra={"request_sequence": packet.sequence},
            )

    def _handle_change_team_group(
        self,
        connection_id: str,
        writer: WriterState,
        remote: str,
        session_state: dict[str, Any],
        packet,
    ) -> None:
        """C2S 5 `changeTeamGroup`: persist the choice and answer S2C 35.

        If the chosen preset has a stored record, it is sent as S2C 40 right
        after the group id so the client's `tbTeamCard[gid]` holds the saved
        team (login only names the preset that was current at the previous
        save).  The reply is always sent once, which releases the client's
        lock-send timer.
        """

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        policy = (
            role.compatibility.profile.team_snapshot
            if role is not None and role.compatibility is not None
            else None
        )
        fallback_group = policy.current_group if policy is not None else 1
        values = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        try:
            group = int(values[0]) if values else fallback_group
        except (TypeError, ValueError):
            group = fallback_group
        reason = None
        record = None
        if role is None or role_record is None:
            reason = "unbound_role"
            group = fallback_group
        elif not 1 <= group <= TEAM_MAX_GROUPS:
            reason = "group_out_of_range"
            group = fallback_group
        else:
            try:
                state = self.server.role_store.set_team_current_group(
                    record=role_record,
                    group_id=group,
                    origin=TEAM_CURRENT_GROUP_ORIGIN_LOCAL_C2S,
                )
                record = self.server.role_store.team_preset(
                    account_key=role_record.account_key, group_id=group
                )
                group = state.current_group
            except RoleStoreError as exc:
                reason = "store_refused"
                group = fallback_group
        response = self._send_s2c(
            connection_id,
            writer,
            "returnGroupId",
            (group,),
            decoded_fields={"dGroupID": group},
            classification=(
                "persisted_team_group"
                if reason is None
                else "team_group_refused_fallback"
            ),
        )
        update_response = None
        if record is not None:
            update_response = self._send_s2c(
                connection_id,
                writer,
                "updateTeam",
                record.team_info_fields(),
                decoded_fields={"dGroupID": record.group_id},
                classification="persisted_team_preset_replay",
            )
        self.server.event_logger.emit(
            "tcp_team_group_changed",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            response_protocol_id=response.spec.protocol_id,
            response_sequence=response.sequence,
            recorded=reason is None,
            reason=reason,
            requested_group=(values[0] if values else None),
            current_group=group,
            preset_replayed=record is not None,
            preset_replay_protocol_id=(
                update_response.spec.protocol_id
                if update_response is not None
                else None
            ),
            lock_partner="changeTeamGroup",
            **extra,
        )
        # TASK-0245 / #27: the current preset changed, so its power is the value
        # the battle-entry gate must read.  A preset with no stored row is the
        # client's own empty team (`S2C 36` gave every preset an empty
        # container), so the value is the soul/role part alone.
        client_cards = tuple(record.card_ids) if record is not None else ()
        session_state.setdefault("team_client_card_ids", {})[group] = client_cards
        self._send_team_max_fight(
            connection_id,
            writer,
            role,
            role_record,
            session_state,
            trigger="changeTeamGroup",
            group_id=group,
            card_ids=client_cards,
            event_extra={"request_sequence": packet.sequence},
        )

    def _send_recharge_activity_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
    ) -> None:
        """Send the recharge-activity login state (v6 policy): one packet.

        `S2C 1014 returnACRechargeDataStart(dRechargeStart, bReset=true)` runs
        the client's OWN `pkgRechargeActivityModel.initRechargeStart()`, whose
        whole body creates one `{bEnough = false, bHadGet = false}` row per id
        of the client's own config table
        `pkgRechargeActivityCfgMgr.e_rechargeActivityTask`.  Without it
        `tbGetRecharge` stays the empty table `_createRechargeActivity` built,
        and `pkgUIRechargeACTaskView.getTask2HadGet`'s UNGUARDED
        `tbInfo.bHadGet` (source line 70) raises -- which is RUN-0027's first
        Lua error, one frame after `initBtn()` bound the function buttons.

        No task row, reward, purchase or claim flag is invented: `bReset = true`
        is the client's own reset, and the only scalar this sends is the
        activity's recharge total, which repeats the `recharge_sum = 0` the
        archive already ships for this role in S2C10.  A server whose profile
        carries no RechargeActivity policy sends nothing at all.
        """

        policy = role.compatibility.profile.recharge_activity
        if policy is None:  # pragma: no cover - v1-v5 profiles
            return
        response = self._send_s2c(
            connection_id,
            writer,
            "returnACRechargeDataStart",
            policy.start_fields(),
            decoded_fields={
                "dRechargeStart": policy.recharge_start,
                "bReset": policy.reset_rows,
            },
            classification="compatibility_recharge_activity_v6",
        )
        self._emit_compatibility_packet(
            "tcp_recharge_activity_state_sent",
            connection_id,
            response,
            role,
            parent_profile_id=role.compatibility.profile.parent_profile_id,
            policy_compatibility_version=(
                policy.container_provenance.compatibility_version
            ),
            snapshot_packet_count=1,
            snapshot_packet_ids=[response.spec.protocol_id],
            recharge_start=policy.recharge_start,
            recharge_start_policy=policy.recharge_start_policy,
            recharge_start_origin=policy.recharge_start_provenance.origin,
            reset_rows=policy.reset_rows,
            container_policy_origin=policy.container_provenance.origin,
            rows_created_by_client_config=policy.reset_rows,
            # The client reads the container during the main-city switch the
            # S2C283 below starts, so this position provably precedes the read.
            precedes_scene_switch_packet=True,
            scene_switch_boundary_protocol_id=283,
            historical_ordering=policy.historical_ordering,
            ordering_origin=policy.ordering_provenance.origin,
            historical_values_recovered=False,
        )

    def _chapter_box_replay_records(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        role: MinimalRoleState,
    ) -> tuple[ChapterBoxAcceptRecord, ...]:
        """The chapter chests of the role logging in, or none.

        Same discipline as the fight/event replay rows: the rows belong to the
        account the session is bound to and are re-read through the role id as
        well.  A read failure is reported and yields no rows; an unreadable row
        must never become a packet.
        """

        account_key = session_state.get("account_key")
        if account_key is None:  # pragma: no cover - the login path sets it
            return ()
        try:
            rows = self.server.role_store.chapter_box_accepts(
                account_key=account_key
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "chapter_box_accept_read_failed",
                str(exc),
            )
            return ()
        return tuple(row for row in rows if row.role_id == role.role_id)

    def _send_chapter_box_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
        session_state: dict[str, Any],
    ) -> None:
        """Replay the chapter milestone chests this role already opened (v18).

        `S2C 381 initChapterBoxStart` runs the client's own
        `pkgChapterInfoMgr.initChapterBoxStart()` (`le_tbChapterBox = {}`), each
        `S2C 382 initChapterBoxInfo(chapterId, boxIndex)` marks one stored accept
        true, and `S2C 383` is the client's own empty end handler.  The client's
        `isChapterBoxAccepted` reads only that table, so an opened chest would
        otherwise be drawn unopened and offered again.

        A profile before v18 carries no policy and sends nothing.  A v18 archive
        with no accepted chest also sends **zero packets** (not an empty
        start/end pair, which would still reset the client's table), so the v17
        and v18 login streams are byte-for-byte identical until a chest is
        actually opened.
        """

        policy = role.compatibility.profile.chapter_box_replay
        if policy is None:  # pragma: no cover - v1-v17 profiles
            return
        records = self._chapter_box_replay_records(
            connection_id, session_state, role
        )
        rows = tuple(record.replay_row() for record in records)
        packets = policy.packets(rows)
        if not packets:
            return
        responses = []
        for name, fields in packets:
            responses.append(
                self._send_s2c(
                    connection_id,
                    writer,
                    name,
                    fields,
                    decoded_fields=(
                        {"dChapterId": fields[0], "dBoxIndex": fields[1]}
                        if name == "initChapterBoxInfo"
                        else {"payload_fields": len(fields)}
                    ),
                    classification="persisted_chapter_box_snapshot",
                )
            )
        self._emit_compatibility_packet(
            "tcp_chapter_box_snapshot_sent",
            connection_id,
            responses[0],
            role,
            parent_profile_id=role.compatibility.profile.parent_profile_id,
            policy_compatibility_version=(
                policy.ordering_provenance.compatibility_version
            ),
            container_policy=policy.container_policy,
            container_policy_origin=policy.row_provenance.origin,
            snapshot_packet_count=len(responses),
            snapshot_packet_ids=[packet.spec.protocol_id for packet in responses],
            rows_sent=len(records),
            replay_rows=[list(row) for row in rows],
            row_format=policy.row_format,
            value_origin=policy.value_provenance.origin,
            ordering_origin=policy.ordering_provenance.origin,
            historical_ordering=policy.historical_ordering,
            historical_values_recovered=False,
            initialises_client_container="le_tbChapterBox",
            replayed=True,
            state_changed=False,
            # The client reads the table when the chapter UI is built, after
            # the main-city switch S2C283 starts; this position precedes it.
            precedes_scene_switch_packet=True,
            scene_switch_boundary_protocol_id=283,
        )

    def _send_chapter_item_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
        session_state: dict[str, Any],
    ) -> None:
        """Send the chapter-map item container init (v7 policy + v14 replay).

        `S2C 389 initChapterItemStart ''` runs the client's OWN
        `pkgChapterInfoMgr.initChapterItemStart()`, whose whole body is
        `le_tbChapterItemInfo = {}`; `S2C 391 initChapterItemEnd ''` is the client's
        own empty end-of-block handler.  Without the first one the global stays
        `nil` (the module's own default), and the world map's init reads it through
        an UNGUARDED index -- `isItemFightOver`'s `le_tbChapterItemInfo[dChapterId]`
        at source line 213, reached from `isChapterPass(1)` inside
        `initChapterBtnNormalStatus`, i.e. from `pkgChapterMainUIViewMgr.initChapter`
        before it has bound a single chapter button.  That is RUN-0029's first Lua
        error (logcat 03:31:25.490): the node rendered, the tap was delivered, and
        no listener existed to receive it.

        BETWEEN those two packets, a v14 profile replays ONE
        `S2C 390 initChapterItemInfo 'ppp'` per chapter-fight settlement this
        archive observed on one connection (TASK-0174): `(dChapterId, 10,
        dIndex)`, the client's own completed-item triple.  The client's own
        `isItemFightOver` reader then answers true for exactly that fight node,
        and `getActiveMap` deactivates it (`bDeactive`), so `createAllEventObjs`
        does not create its scene object at all.  With no stored settlement the
        packet list is exactly the v7 pair, so an archive that never settled a
        chapter fight produces the v13 stream byte-for-byte.

        A row is never derived from a guess: the caller only stores one when the
        client's own `C2S 69`, its `C2S 67` and this server's single `S2C 96`
        answer all completed on one connection, and the policy only emits the
        values that request carried plus the shipped `MAP_TYPE_FIGHT = 10`.
        """

        profile = role.compatibility.profile
        policy = profile.chapter_item_init
        replay = profile.chapter_fight_replay
        if policy is None:  # pragma: no cover - v1-v6 profiles
            return
        records = self._chapter_fight_replay_records(
            connection_id, session_state, role
        )
        # TASK-0184: a chapter-map event the client itself completed is replayed
        # as its own item row.  Only a v14+ profile carries an item-replay policy
        # at all (`chapter_fight_replay`), so a v1-v13 login keeps exactly the
        # packet list it had: no policy, no event rows, byte-for-byte unchanged.
        event_records = (
            self._chapter_event_replay_records(connection_id, session_state, role)
            if replay is not None
            else ()
        )
        if replay is not None:
            packets = list(
                replay.packets(
                    tuple((row.chapter_id, row.map_index) for row in records)
                )
            )
            if event_records:
                # The client's own 389/391 pair is the only forced position: 390
                # indexes the global 389 creates, and 391 closes the block.  The
                # event rows go between the fight rows and the end packet.
                if packets[-1][0] != "initChapterItemEnd":  # pragma: no cover
                    raise AssertionError(
                        "the chapter-item replay no longer ends with "
                        "initChapterItemEnd"
                    )
                packets = (
                    packets[:-1]
                    + [
                        ("initChapterItemInfo", row.replay_row())
                        for row in event_records
                    ]
                    + [packets[-1]]
                )
            else:
                packets = tuple(packets)
            row_policy_compatibility_version = PROFILE_ID_V14
            row_policy = replay.container_policy
            row_origin = replay.row_provenance.origin
            row_value_origin = replay.value_provenance.origin
            row_ordering_origin = replay.ordering_provenance.origin
        else:
            packets = policy.packets()
            row_policy_compatibility_version = None
            row_policy = ""
            row_origin = ""
            row_value_origin = ""
            row_ordering_origin = ""
        # TASK-0232: the dev-play archive needs the client's own EXIT marker for
        # chapters 1..8 so `getCurrentPassedChapter()` reaches 8 (D-187).  They
        # are one contiguous synthetic block immediately after the client's
        # `initChapterItemStart` reset and never interleaved with the observed
        # fight rows below; without the overlay this branch does nothing and the
        # packet list stays byte-for-byte the frozen v18 stream.
        dev_play_overlay = session_state.get("dev_play_babel_overlay")
        dev_rows = (
            dev_play_overlay.exit_rows
            if isinstance(dev_play_overlay, BabelOverlay)
            else ()
        )
        if dev_rows:
            if packets[0][0] != "initChapterItemStart":  # pragma: no cover
                raise AssertionError(
                    "the chapter-item block no longer starts with "
                    "initChapterItemStart"
                )
            if packets[-1][0] != "initChapterItemEnd":  # pragma: no cover
                raise AssertionError(
                    "the chapter-item block no longer ends with "
                    "initChapterItemEnd"
                )
            packets = (
                packets[0],
                *[
                    ("initChapterItemInfo", row)
                    for row in dev_rows
                ],
                *packets[1:],
            )
        item_rows_sent = len(records) + len(event_records) + len(dev_rows)
        dev_row_set = frozenset(dev_rows)
        responses = [
            self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                decoded_fields=(
                    {
                        "dChapterId": fields[0],
                        "dType": fields[1],
                        "dId": fields[2],
                    }
                    if name == "initChapterItemInfo"
                    else {"payload_fields": len(fields)}
                ),
                classification=(
                    "dev_play_synthetic_exit_row"
                    if name == "initChapterItemInfo"
                    and tuple(fields) in dev_row_set
                    else "observed_chapter_fight_replay_v14"
                    if name == "initChapterItemInfo"
                    else "compatibility_chapter_item_init_v7"
                ),
            )
            for name, fields in packets
        ]
        sent_ids = [response.spec.protocol_id for response in responses]
        for index, response in enumerate(responses):
            self._emit_compatibility_packet(
                "tcp_chapter_item_state_sent",
                connection_id,
                response,
                role,
                parent_profile_id=role.compatibility.profile.parent_profile_id,
                policy_compatibility_version=(
                    policy.container_provenance.compatibility_version
                ),
                snapshot_index=index,
                snapshot_packet_count=len(packets),
                snapshot_packet_ids=sent_ids,
                snapshot_section=response.spec.name,
                container_policy=policy.container_policy,
                container_policy_origin=policy.container_provenance.origin,
                item_rows_sent=item_rows_sent,
                chapter_fight_rows_sent=len(records),
                chapter_event_rows_sent=len(event_records),
                chapter_fight_replay=replay is not None,
                chapter_fight_row_policy=row_policy,
                chapter_fight_row_origin=row_origin,
                chapter_fight_value_origin=row_value_origin,
                chapter_fight_ordering_origin=row_ordering_origin,
                chapter_fight_policy_compatibility_version=(
                    row_policy_compatibility_version
                ),
                # The client reads the container when the world map is built, i.e.
                # long after this login segment; the S2C283 below is the packet
                # whose dispatch starts the main-city switch.
                precedes_scene_switch_packet=True,
                scene_switch_boundary_protocol_id=283,
                historical_ordering=(
                    replay.historical_ordering
                    if replay is not None
                    else policy.historical_ordering
                ),
                ordering_origin=(
                    replay.ordering_provenance.origin
                    if replay is not None
                    else policy.ordering_provenance.origin
                ),
                historical_values_recovered=False,
                **(
                    {"dev_play_exit_rows_sent": len(dev_rows)}
                    if dev_rows
                    else {}
                ),
            )
        for chapter_id, map_type, exit_index in dev_rows:
            row = (chapter_id, map_type, exit_index)
            response = next(
                (
                    response
                    for response in responses
                    if response.spec.name == "initChapterItemInfo"
                    and tuple(response.fields) == row
                ),
                None,
            )
            self.server.event_logger.emit(
                "tcp_dev_play_babel_exit_row_sent",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                profile=DEV_PLAY_BABEL_PROFILE_ID,
                role_id=role.role_id,
                protocol_id=(
                    None if response is None else response.spec.protocol_id
                ),
                protocol_name="initChapterItemInfo",
                sequence=(None if response is None else response.sequence),
                row_values=row,
                chapter_id=chapter_id,
                map_type=map_type,
                map_type_name="MAP_TYPE_EXIT",
                exit_index=exit_index,
                row_origin=DEV_PLAY_BABEL_ORIGIN,
                value_origin="deterministic_recovered",
                selection_origin="synthetic_compatibility",
                replaceable=True,
                historical=False,
                login_position="between_initChapterItemStart_and_End",
                synthetic_ordering=True,
            )
        for record in records:
            response = next(
                (
                    response
                    for response in responses
                    if response.spec.name == "initChapterItemInfo"
                    and response.fields
                    == (record.chapter_id, 10, record.map_index)
                ),
                None,
            )
            self.server.event_logger.emit(
                "tcp_chapter_fight_clear_replayed",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=role.role_id,
                account_key_short=record.display_key(),
                protocol_id=(
                    None if response is None else response.spec.protocol_id
                ),
                protocol_name="initChapterItemInfo",
                sequence=(None if response is None else response.sequence),
                replayed=True,
                row_values=(
                    record.chapter_id,
                    10,
                    record.map_index,
                ),
                row_format=CHAPTER_FIGHT_REPLAY_ROW_FORMAT,
                row_origin=row_origin,
                value_origin=row_value_origin,
                item_row_sender="observed_settlement",
                login_position="between_initChapterItemStart_and_End",
                historical_row_order_recovered=False,
                **record.evidence_fields(),
            )
        for record in event_records:
            response = next(
                (
                    response
                    for response in responses
                    if response.spec.name == "initChapterItemInfo"
                    and tuple(response.fields) == record.replay_row()
                ),
                None,
            )
            self.server.event_logger.emit(
                "tcp_chapter_event_clear_replayed",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=role.role_id,
                account_key_short=record.display_key(),
                protocol_id=(
                    None if response is None else response.spec.protocol_id
                ),
                protocol_name="initChapterItemInfo",
                sequence=(None if response is None else response.sequence),
                replayed=True,
                row_values=record.replay_row(),
                row_format=CHAPTER_FIGHT_REPLAY_ROW_FORMAT,
                row_origin="deterministic_recovered",
                value_origin="request_echo",
                item_row_sender="observed_client_action",
                login_position="between_initChapterItemStart_and_End",
                historical_row_order_recovered=False,
                row_provenance={
                    "chapter_id": "request_echo",
                    "d_type": "deterministic_recovered",
                    "map_index": "request_echo",
                },
                map_type_policy="shipped_pkgGuankaMapConstMgr_MAP_TYPE_EVENT",
                **record.evidence_fields(),
            )

    def _send_babel_info_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState,
    ) -> None:
        """Push the dev-play ``S2C 580 returnBabelInfo`` after the chapter block.

        The client never requests this packet (D-187): its ``pkgBabelNcMgr``
        handler only stores what the server sends, so a login that wants the
        babel screen to show a real floor/state must have it pushed.  The
        position -- after the client's own chapter-item ``389/390/391`` block --
        is an explicit synthetic ordering, not a recovered historical one.

        With no overlay (the normal archive, ``qa-changan-main`` included) this
        method sends nothing at all, so the login stream stays byte-for-byte the
        frozen v18 stream.
        """

        overlay = session_state.get("dev_play_babel_overlay")
        if not isinstance(overlay, BabelOverlay):
            return
        # TASK-0274: a persisted dev-play floor wins over the overlay default, so progress
        # made by skipping survives a relogin (the client only learns the floor from S2C 580).
        persisted_floor = dev_play_babel.read_floor(self.server.role_store)
        fields = (dev_play_babel.babel_info_fields_with_floor(
            overlay.babel_info_fields(), persisted_floor)
            if persisted_floor is not None else overlay.babel_info_fields())
        response = self._send_s2c(
            connection_id,
            writer,
            "returnBabelInfo",
            fields,
            decoded_fields={
                "floor": fields[0],
                "field2": fields[1],
                "state": fields[2],
                "failCount": fields[3],
                "realm": fields[4],
            },
            classification="dev_play_synthetic_babel_state",
        )
        self.server.event_logger.emit(
            "tcp_babel_info_pushed",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            profile=DEV_PLAY_BABEL_PROFILE_ID,
            role_id=role.role_id,
            protocol_id=response.spec.protocol_id,
            protocol_name="returnBabelInfo",
            sequence=response.sequence,
            fields=list(fields),
            floor=overlay.floor,
            state=overlay.state,
            state_name=overlay.state_name,
            failCount=overlay.fail_count,
            realm=overlay.realm,
            field2=overlay.field2,
            field2_provenance=BABEL_FIELD_2_PROVENANCE,
            field2_semantics=dev_play_babel.BABEL_FIELD_2_SEMANTICS,
            origin=DEV_PLAY_BABEL_ORIGIN,
            replaceable=True,
            historical=False,
            persisted=False,
            login_position="after_chapter_item_block",
            synthetic_ordering=True,
            client_request="none",
        )

    def _chapter_fight_replay_records(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        role: MinimalRoleState,
    ) -> tuple[ChapterFightClearRecord, ...]:
        """The stored settlements of the role logging in, or none.

        The rows belong to the account the session is bound to and are re-read
        through the role id as well: a row written for another role (or by a
        later tool) is never replayed into this session.  A read failure is
        reported and yields no rows -- a login must not fail because our own
        bookkeeping cannot be read, and an unreadable row must never become a
        packet.
        """

        account_key = session_state.get("account_key")
        if account_key is None:  # pragma: no cover - the login path sets it
            return ()
        try:
            rows = self.server.role_store.chapter_fight_clears(
                account_key=account_key
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "chapter_fight_clear_read_failed",
                str(exc),
            )
            return ()
        return tuple(row for row in rows if row.role_id == role.role_id)

    def _chapter_event_replay_records(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        role: MinimalRoleState,
    ) -> tuple[ChapterEventClearRecord, ...]:
        """The stored chapter-event clears of the role logging in, or none.

        Same discipline as the fight rows above: the rows belong to the account
        the session is bound to and are re-read through the role id as well.  A
        read failure is reported and yields no rows; an unreadable row must
        never become a packet.
        """

        account_key = session_state.get("account_key")
        if account_key is None:  # pragma: no cover - the login path sets it
            return ()
        try:
            rows = self.server.role_store.chapter_event_clears(
                account_key=account_key
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "chapter_event_clear_read_failed",
                str(exc),
            )
            return ()
        return tuple(row for row in rows if row.role_id == role.role_id)

    def _battle_team_snapshot(self, session_state) -> dict[str, Any]:
        """The stored team the client's C2S 69 is fighting with.

        `pkgBattleSingleMgr` builds the local player group from
        `pkgTeamDataMgr.getTeamCardData(groupId)`; this server's copy of that
        team is the preset the client itself saved with C2S 6.  The group is
        the last made-current one (C2S 5), or the profile's default when the
        client never named one.  An absent preset is an empty team, not an
        error: the battle still settles, it simply has no card instance to
        award.
        """

        account_key = session_state.get("account_key")
        role = session_state.get("role")
        profile = (
            role.compatibility.profile
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile
        )
        policy = profile.team_snapshot
        group = policy.current_group if policy is not None else 1
        preset = None
        if account_key is not None:
            state = self.server.role_store.team_state(account_key=account_key)
            if state is not None:
                group = state.current_group
            preset = self.server.role_store.team_preset(
                account_key=account_key, group_id=group
            )
        if preset is None:
            return {
                "team_group_id": group,
                "team_card_ids": [],
                "team_origin": "no_stored_team_preset",
            }
        return {
            "team_group_id": group,
            "team_card_ids": [value for value in preset.card_ids if value],
            "team_origin": preset.origin,
        }

    @staticmethod
    def _chapter_battle_start_fields(packet) -> dict[str, int] | None:
        """The chapter-map identity of one C2S 69, or ``None`` if it is not one.

        `pkgBattleMgr.c2sStartPveBattle(PVETYPE_GUANKA_CHAPTER_MAP, tbCfg.
        fightId, dIndex, 0, dChapterId, 0)` sends
        `netEnterPveBattle(socket, isRetry, dSyncCount, pveOrPvpType, p1..p9,
        a1)`, so the wire positions are fixed by the client's own call:
        index 2 is `pveOrPvpType`, index 3 is `p1` (fight id), index 4 is `p2`
        (the chapter-map key the client calls `dIndex`), index 6 is `p4` (the
        chapter id).  A packet that is not a chapter-map start, or whose values
        are not positive integers, returns ``None`` -- nothing is rounded or
        guessed into a candidate.
        """

        fields = packet.fields
        if packet.spec.protocol_id != 69 or len(fields) != 13:
            return None
        try:
            pve_type = int(fields[2])
            fight_id = int(fields[3])
            map_index = int(fields[4])
            chapter_id = int(fields[6])
        except (TypeError, ValueError):
            return None
        for value in (fields[2], fields[3], fields[4], fields[6]):
            if isinstance(value, float) and not value.is_integer():
                return None
            if isinstance(value, bool):
                return None
        if pve_type != BATTLE_START_PVE_OR_PVP_TYPE:
            return None
        if fight_id <= 0 or map_index <= 0 or chapter_id <= 0:
            return None
        return {
            "pve_or_pvp_type": pve_type,
            "fight_id": fight_id,
            "map_index": map_index,
            "chapter_id": chapter_id,
            "request_sequence": int(packet.sequence),
        }

    @staticmethod
    def _babel_battle_start_fields(
        packet, reply_fields: tuple[object, ...], reply_sequence: int
    ) -> dict[str, int] | None:
        """The locktower identity of one C2S 69 answered with type 73, or ``None``.

        TASK-0238.  The client's own ``pkgBabel.doFight/doFightGoOn`` calls
        ``c2sStartPveBattle(PVETYPE_LOCKTOWER, floor, 0/1)``, whose wire slots
        are fixed by ``c2s.netEnterPveBattle``: index 3 is ``p1`` (floor) and
        index 4 is ``p2`` (the client's ``dFightType``) -- TASK-0236/D-192.
        The ``dSyncCount`` is the reply's own position 7, which the client
        asserts non-zero and sends back in C2S 67 as ``dPackCheckFlag``.

        A non-73 reply, a malformed/non-integral request value or a zero sync
        count returns ``None``: nothing is remembered and nothing is guessed.
        """

        if packet.spec.protocol_id != 69 or len(packet.fields) != 13:
            return None
        if len(reply_fields) < BATTLE_START_SYNC_COUNT_POSITION:
            return None
        try:
            replied_type = int(reply_fields[BATTLE_START_TYPE_POSITION - 1])
            sync_count = int(reply_fields[BATTLE_START_SYNC_COUNT_POSITION - 1])
        except (TypeError, ValueError):
            return None
        if (
            isinstance(reply_fields[BATTLE_START_TYPE_POSITION - 1], bool)
            or replied_type != BATTLE_START_LOCKTOWER_PVE_TYPE
            or sync_count <= 0
        ):
            return None
        values: list[int] = []
        for value in (packet.fields[3], packet.fields[4]):
            if isinstance(value, bool):
                return None
            if isinstance(value, float) and not value.is_integer():
                return None
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                return None
        floor, fight_type = values
        if floor <= 0:
            return None
        return {
            "floor": floor,
            "fight_type": fight_type,
            "pve_or_pvp_type": replied_type,
            "sync_count": sync_count,
            "request_sequence": int(packet.sequence),
            "reply_sequence": int(reply_sequence),
        }

    @staticmethod
    def _dungeon_battle_start_fields(
        packet, reply_fields: tuple[object, ...], reply_sequence: int
    ) -> dict[str, int] | None:
        """The artifact/uncharted identity of one C2S 69 answered with 66 or 88.

        TASK-0287.  Both types travel through the one recovered writer
        `pkgBattleMgr.c2sStartPveBattle` (`c2s.netEnterPveBattle`):
        `pkgUIArtifactDgnTeamInfo4Conf` sends
        `(PVETYPE_GOD_FIGHT=66, le_dTower, dLayer, dSn)` and
        `pkgUIUnchartedEventView` sends `(PVETYPE_UNCHARTED=88, dUnchartedId,
        dLayer, dArea, dRoom, dIndex, dOption2, dOptionId)` (TASK-0257/D-208,
        TASK-0271), so the wire slots are fixed: index 2 is `pveOrPvpType` and
        indices 3..6 are `p1..p4`.  For 66 those are tower/layer/sn and the
        client's own p4 = 0; for 88 they are unchartedId/layer/area/room.

        The reply's own type must be the type the client asked for.  The fixed
        v17/v18 reply answers 86 to every request and the client then really
        enters the chapter branch, so remembering a 66/88 fight in that case
        would be a lie.  The reply's position 7 `dSyncCount` is the value the
        client sends back in C2S 67 as `dPackCheckFlag`.

        A non-66/88 request or reply, a reply whose type differs from the
        request's, a malformed/non-integral value or a zero sync count returns
        `None`: nothing is remembered and nothing is guessed.
        """

        if packet.spec.protocol_id != 69 or len(packet.fields) != 13:
            return None
        if len(reply_fields) < BATTLE_START_SYNC_COUNT_POSITION:
            return None
        type_cell = reply_fields[BATTLE_START_TYPE_POSITION - 1]
        sync_cell = reply_fields[BATTLE_START_SYNC_COUNT_POSITION - 1]
        if isinstance(type_cell, bool) or isinstance(sync_cell, bool):
            return None
        try:
            replied_type = int(type_cell)
            sync_count = int(sync_cell)
        except (TypeError, ValueError):
            return None
        if replied_type not in BATTLE_START_DUNGEON_PVE_TYPES or sync_count <= 0:
            return None
        request_type_cell = packet.fields[BATTLE_START_REQUEST_TYPE_INDEX]
        if isinstance(request_type_cell, bool):
            return None
        try:
            requested_type = int(request_type_cell)
        except (TypeError, ValueError):
            return None
        if requested_type != replied_type:
            return None
        slots: list[int] = []
        for value in packet.fields[3:7]:
            if isinstance(value, bool):
                return None
            if isinstance(value, float) and not value.is_integer():
                return None
            try:
                slots.append(int(value))
            except (TypeError, ValueError):
                return None
        p1, p2, p3, p4 = slots
        captured: dict[str, int] = {
            "pve_or_pvp_type": replied_type,
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "p4": p4,
            "sync_count": sync_count,
            "request_sequence": int(packet.sequence),
            "reply_sequence": int(reply_sequence),
        }
        if replied_type == BATTLE_START_GOD_FIGHT_PVE_TYPE:
            captured.update({"tower": p1, "layer": p2, "sn": p3})
        elif replied_type == BATTLE_START_UNCHARTED_PVE_TYPE:
            captured.update({
                "uncharted_id": p1,
                "layer": p2,
                "area": p3,
                "room": p4,
            })
        else:  # pragma: no cover - the membership check above already refused
            return None
        return captured

    def _record_chapter_fight_clear(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState | None,
        packet,
        response,
    ) -> None:
        """Store the one settlement this connection had already started.

        Called only AFTER the S2C 96 reply has been written, so the three parts
        the task names -- the client's request, its settlement and our answer --
        all exist on this connection before anything is persisted.  A settlement
        that cannot be paired with a request on the same connection, that comes
        from another role, or that is not a chapter-map settlement is NOT
        stored; the session stays alive because the client's notification needs
        no further answer, and the refusal is a named event, never a silent
        drop.  A storage failure is a `tcp_error` (visible) and also stores
        nothing.
        """

        profile = (
            role.compatibility.profile
            if role is not None
            else self.server.compatibility_profile
        )
        if profile.chapter_fight_replay is None:
            # A v1-v13 server has no policy that could turn a settlement into a
            # row; there is nothing to record and nothing to report.
            return
        pending = session_state.pop("chapter_battle_start", None)
        role_record = session_state.get("role_record")
        pve_type: int | None = None
        try:
            if packet.fields:
                pve_type = int(packet.fields[0])
        except (TypeError, ValueError):  # pragma: no cover - decoded as 'N'
            pve_type = None
        reason: str | None = None
        if pending is None:
            reason = "no_pending_battle_start_on_this_connection"
        elif role_record is None or role is None:
            reason = "unbound_role"
        elif pending["role_id"] != role_record.role_id:
            reason = "role_changed"
        elif (
            pending["pve_or_pvp_type"] != BATTLE_START_PVE_OR_PVP_TYPE
            or pve_type != BATTLE_START_PVE_OR_PVP_TYPE
        ):
            reason = "not_a_chapter_map_battle"
        if reason is not None:
            self.server.event_logger.emit(
                "tcp_chapter_fight_clear_not_recorded",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=(None if role is None else role.role_id),
                reason=reason,
                settlement_protocol_id=packet.spec.protocol_id,
                settlement_sequence=packet.sequence,
                settlement_pve_or_pvp_type=pve_type,
                reply_protocol_id=response.spec.protocol_id,
                reply_sequence=response.sequence,
                recorded=False,
                evidence_kind=CHAPTER_FIGHT_EVIDENCE_SETTLED_TRANSACTION,
                persistence_origin="synthetic_compatibility",
            )
            return
        assert pending is not None and role_record is not None
        # C2S 67 slot 12 is `base[12] + result * n` (pkgSmartGameOverResult's
        # `encryptResult`).  `battle_rewards` recovers the base/n from two
        # device victories and one recorded loss, so the settlement's own code
        # is now decoded rather than guessed.  A value outside the six packed
        # codes stays `None` and is reported as undecoded; nothing is awarded
        # from an undecoded value.
        result_code = (
            decode_game_over_result(packet.fields[13])
            if len(packet.fields) > 13
            else None
        )
        # The money producer: the shipped `guankaMonster` row for this fight.
        # A loss / undecoded result / unfrozen fight / random range is a named
        # refusal that grants 0; only a decoded victory whose shipped row has a
        # single money value writes the wallet, and it does so inside the same
        # transaction as the clear row (created-only, so a replay cannot pay
        # twice).
        money_reward = money_reward_for_fight(pending["fight_id"])
        money_amount = 0
        money_reason: str | None = None
        if result_code is None:
            money_reason = "result_code_undecoded"
        elif result_code != GAMEOVERRESULT_WIN:
            money_reason = "battle_not_a_victory"
        elif money_reward is None:
            money_reason = "fight_not_in_recovered_money_table"
        elif not money_reward.deterministic:
            money_reason = "money_range_unrecovered"
        elif money_reward.amount == 0:
            money_reason = "fight_money_is_zero"
        else:
            money_amount = int(money_reward.amount)
        # The special-card producer (D-167's recovered 8886 semantics): fight
        # 102071's reward row 501 slot 1 names `specialCard` cfg id 1 (the 1★
        # dog food) once.  It is paid on a decoded victory inside the same
        # transaction as the clear row (and the money, when the fight pays
        # any).  There is no field-proven battle-scene display packet for it,
        # so nothing is sent now; the login `returnSpecialCard*` snapshot is
        # the client-visible road.
        special_reward = special_card_reward_for_fight(pending["fight_id"])
        special_food: dict[int, int] = {}
        special_reason: str | None = None
        if special_reward is None:
            special_reason = "fight_has_no_recovered_special_card_reward"
        elif result_code is None:
            special_reason = "result_code_undecoded"
        elif result_code != GAMEOVERRESULT_WIN:
            special_reason = "battle_not_a_victory"
        else:
            special_food = {special_reward.cfg_id: special_reward.count}
        try:
            outcome = (
                self.server.role_store.record_chapter_fight_clear_with_money(
                    record=role_record,
                    chapter_id=pending["chapter_id"],
                    fight_id=pending["fight_id"],
                    map_index=pending["map_index"],
                    observed_at=self.server.now_unix(),
                    request_sequence=pending["request_sequence"],
                    settlement_sequence=packet.sequence,
                    reply_sequence=response.sequence,
                    wallet_money=money_amount,
                    wallet_provenance=(
                        FIGHT_MONEY_ORIGIN if money_amount else ""
                    ),
                    special_food=special_food,
                    special_provenance=(
                        FIGHT_SPECIAL_CARD_ORIGIN if special_food else ""
                    ),
                )
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "chapter_fight_clear_write_failed",
                str(exc),
                chapter_id=pending["chapter_id"],
                fight_id=pending["fight_id"],
                map_index=pending["map_index"],
                money_amount=float(money_amount),
                money_source=FIGHT_MONEY_SOURCE,
                money_digest=FIGHT_MONEY_DIGEST,
                money_state="not_written",
                special_card_cfg_id=(
                    None if special_reward is None else special_reward.cfg_id
                ),
                special_card_count=(
                    None if special_reward is None else special_reward.count
                ),
                special_card_state="not_written",
                **self.server.role_store.profile_fields(),
            )
            return
        record, created = outcome.record, outcome.created
        self.server.event_logger.emit(
            "tcp_chapter_fight_clear_recorded",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            account_key_short=record.display_key(),
            recorded=created,
            already_present=not created,
            evidence_kind=CHAPTER_FIGHT_EVIDENCE_SETTLED_TRANSACTION,
            settlement_protocol_id=packet.spec.protocol_id,
            settlement_sequence=packet.sequence,
            reply_protocol_id=response.spec.protocol_id,
            reply_sequence=response.sequence,
            # What will be replayed: the client's own request values plus the
            # packaged MAP_TYPE_FIGHT constant, assembled by the v14 policy.
            replay_row=(
                record.chapter_id,
                10,
                record.map_index,
            ),
            replay_row_format=CHAPTER_FIGHT_REPLAY_ROW_FORMAT,
            replay_value_origin="request_echo",
            replay_row_origin="deterministic_recovered",
            win_loss_decoded=result_code is not None,
            win_loss_policy="c2s67_slot12_randvc_decoded",
            battle_result_code=result_code,
            battle_result_name=GAMEOVERRESULT_NAMES.get(result_code),
            battle_result_evidence="c2s67_slot_12_base_6179048_step_7218",
            historical_completion_time_recovered=False,
            **record.evidence_fields(),
        )
        if created:
            self._grant_chapter_fight_reward(
                connection_id, writer, role_record, record.fight_id, result_code
            )
        self._emit_chapter_fight_special_card(
            connection_id,
            record,
            outcome,
            created,
            special_reward,
            special_reason,
            result_code,
            profile,
        )
        self._grant_chapter_battle_card_exp(
            connection_id, writer, session_state, role, packet, pending, result_code
        )
        money_event_base: dict[str, Any] = {
            "connection_id": connection_id,
            "remote_endpoint": f"{self.client_address[0]}:{self.client_address[1]}",
            "role_id": role.role_id,
            "account_key_short": record.display_key(),
            "chapter_id": record.chapter_id,
            "map_index": record.map_index,
            "fight_id": record.fight_id,
            # `guankaMonster` has no `guankaReward` row id; naming one would be
            # an invented identity.  The row itself is named instead.
            "reward_id": None,
            "reward_id_reason": "guankaMonster_money_row_has_no_guankaReward_id",
            "reward_row": f"guankaMonster[{record.fight_id}]",
            "reward_source": FIGHT_MONEY_SOURCE,
            "reward_digest": FIGHT_MONEY_DIGEST,
            "money_lower": (
                None if money_reward is None else money_reward.money_lower
            ),
            "money_upper": (
                None if money_reward is None else money_reward.money_upper
            ),
            "money_amount": float(money_amount),
            "money_before": float(outcome.money_before),
            "money_after": float(outcome.money_after),
            "idempotency_key": (
                f"chapter_fight_clear:{record.role_id}:"
                f"{record.chapter_id}:{record.fight_id}"
            ),
            "idempotency_basis": "chapter_fight_clears(chapter_id, fight_id)",
            "money_value_provenance": FIGHT_MONEY_VALUE_PROVENANCE,
            "money_rule_provenance": FIGHT_MONEY_RULE_PROVENANCE,
            "win_loss_decoded": result_code is not None,
            "win_loss_policy": "c2s67_slot12_randvc_decoded",
            "battle_result_code": result_code,
            "battle_result_name": GAMEOVERRESULT_NAMES.get(result_code),
            "profile_id": profile.profile_id,
        }
        if created and money_amount:
            self.server.event_logger.emit(
                "tcp_chapter_fight_money_granted",
                granted=True,
                persisted=True,
                state_changed=True,
                money_granted=float(money_amount),
                **money_event_base,
            )
            self._send_s2c(
                connection_id,
                writer,
                "returnOneValue_p",
                (7, float(outcome.money_after)),
                decoded_fields={
                    "reward_type": 7,
                    "reward_type_name": "MONEY",
                    "amount": float(outcome.money_after),
                    "money_granted": float(money_amount),
                    "fight_id": record.fight_id,
                },
                classification="chapter_fight_money_granted",
                record_fields={
                    "reward_source": FIGHT_MONEY_SOURCE,
                    "reward_digest": FIGHT_MONEY_DIGEST,
                    "idempotency_key": money_event_base["idempotency_key"],
                    "money_before": float(outcome.money_before),
                    "money_after": float(outcome.money_after),
                    "state_changed": True,
                },
            )
        elif created:
            self.server.event_logger.emit(
                "tcp_chapter_fight_money_not_granted",
                granted=False,
                persisted=False,
                state_changed=False,
                money_granted=0.0,
                reason=money_reason,
                **money_event_base,
            )
        elif money_amount:
            self.server.event_logger.emit(
                "tcp_chapter_fight_money_already_present",
                granted=False,
                persisted=True,
                state_changed=False,
                money_granted=0.0,
                reason="chapter_fight_clear_already_recorded",
                **money_event_base,
            )

    def _record_babel_settlement(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState | None,
        packet,
        response,
    ) -> None:
        """Pair one locktower settlement with this connection's own C2S 69.

        TASK-0238.  This method is a no-op for every session that is not an
        applied dev-babel-main overlay, so the chapter path (86) and every
        non-dev session keep their exact events and bytes.

        A pair needs, on THIS connection: the C2S 69 that was answered with
        type 73 (remembered by the netEnterPveBattle branch), the same role, a
        C2S 67 whose own ``pveOrPvpType`` is 73, and a decoded
        ``GAMEOVERRESULT_WIN``.  ``dPackCheckFlag`` is recorded and compared
        with the S2C 89 ``dSyncCount`` -- the recovery says they are the same
        value (``pkgPveEnterData`` line 945 asserts it non-zero and
        ``pkgGameOverDisplay`` sends it back), and that comparison is evidence
        for the first device RUN rather than a gate that could hide a packet.

        A paired victory pushes exactly one S2C 580 with the overlay's own
        floor/state/failCount/realm (`如实回推`).  Nothing is persisted: no
        chapter row, no wallet, no card, no reward.
        """

        overlay = session_state.get("dev_play_babel_overlay")
        if not isinstance(overlay, BabelOverlay):
            return
        profile = (
            role.compatibility.profile
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile
        )
        push_policy = profile.babel_settlement_push
        pending = session_state.pop("babel_battle_start", None)
        role_record = session_state.get("role_record")
        pve_type: int | None = None
        if packet.fields:
            try:
                pve_type = int(packet.fields[0])
            except (TypeError, ValueError):
                pve_type = None
        d_pack_check_flag: int | None = None
        if len(packet.fields) > 1:
            try:
                d_pack_check_flag = int(packet.fields[1])
            except (TypeError, ValueError):
                d_pack_check_flag = None
        result_code = (
            decode_game_over_result(packet.fields[13])
            if len(packet.fields) > 13
            else None
        )
        reason: str | None = None
        if pending is None and pve_type != BATTLE_START_LOCKTOWER_PVE_TYPE:
            # An ordinary chapter settlement on a dev session is not this edge:
            # there is no babel start to pair and nothing to say about it.
            return
        if pending is not None and pve_type != BATTLE_START_LOCKTOWER_PVE_TYPE:
            reason = "not_a_babel_settlement"
        elif push_policy is None:
            reason = "babel_settlement_push_policy_missing"
        elif pending is None:
            # A repeated C2S 67 (or a 73 settlement whose start was never
            # captured on this connection).  The pending key was already popped,
            # so a second push is impossible by construction.
            reason = "no_pending_babel_battle_start_on_this_connection"
        elif role_record is None or role is None:
            reason = "unbound_role"
        elif pending["role_id"] != role.role_id:
            reason = "role_changed"
        elif result_code != GAMEOVERRESULT_WIN:
            reason = "battle_not_a_victory"
        if reason is not None:
            self.server.event_logger.emit(
                "tcp_babel_settlement_not_paired",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=(None if role is None else role.role_id),
                play_profile_id=DEV_PLAY_BABEL_PROFILE_ID,
                profile_id=profile.profile_id,
                reason=reason,
                settlement_protocol_id=packet.spec.protocol_id,
                settlement_sequence=packet.sequence,
                settlement_pve_or_pvp_type=pve_type,
                battle_result_code=result_code,
                battle_result_name=GAMEOVERRESULT_NAMES.get(result_code),
                pending_floor=(None if pending is None else pending["floor"]),
                pending_dFightType=(
                    None if pending is None else pending["fight_type"]
                ),
                dPackCheckFlag=d_pack_check_flag,
                s2c89_dSyncCount=(
                    None if pending is None else pending["sync_count"]
                ),
                paired=False,
                pushed=False,
                recorded=False,
                pairing_provenance="deterministic_recovered",
            )
            return
        assert pending is not None and role is not None
        self.server.event_logger.emit(
            "tcp_babel_settlement_paired",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            play_profile_id=DEV_PLAY_BABEL_PROFILE_ID,
            profile_id=profile.profile_id,
            parent_profile_id=profile.parent_profile_id,
            # The client's own C2S 69 slots (p1 = floor, p2 = dFightType).
            floor=pending["floor"],
            dFightType=pending["fight_type"],
            pve_or_pvp_type=pending["pve_or_pvp_type"],
            request_sequence=pending["request_sequence"],
            s2c89_reply_sequence=pending["reply_sequence"],
            s2c89_dSyncCount=pending["sync_count"],
            settlement_protocol_id=packet.spec.protocol_id,
            settlement_sequence=packet.sequence,
            settlement_pve_or_pvp_type=pve_type,
            battle_result_code=result_code,
            battle_result_name=GAMEOVERRESULT_NAMES.get(result_code),
            reply_protocol_id=response.spec.protocol_id,
            reply_sequence=response.sequence,
            # C2S 67's second value is documented as the S2C 89 dSyncCount
            # (pkgPveEnterData line 945 / pkgGameOverDisplay line 20).  Both
            # values are recorded and compared; the pairing itself does not
            # gate on the comparison, so a device mismatch is visible evidence
            # rather than a silent dropped packet.
            dPackCheckFlag=d_pack_check_flag,
            pack_check_matches_s2c89=(
                d_pack_check_flag is not None
                and d_pack_check_flag == pending["sync_count"]
            ),
            floor_value_origin="request_echo",
            fight_type_value_origin="request_echo",
            pairing_provenance="deterministic_recovered",
            pairing_source=(
                "pkgBattleMgr.lua lines 53-73 (c2sStartPveBattle -> "
                "netEnterPveBattle), pkgPveEnterData line 945 "
                "(dPackCheckFlag = assert(dSyncCount)), pkgGameOverDisplay "
                "PROTO 0.0 lines 6-25 (syncRecord2Server); TASK-0236/D-192"
            ),
            recorded=False,
            persisted=False,
            chapter_fight_recorded=False,
            reward_granted=False,
            wallet_written=False,
            card_written=False,
            historical_values_recovered=False,
        )
        self._push_babel_settlement_info(
            connection_id,
            writer,
            session_state,
            role,
            profile,
            push_policy,
            overlay,
            pending,
            packet,
            response,
        )

    def _grant_artifact_dgn_reward(self, connection_id: str, writer: WriterState,
                                   session_state: dict[str, Any],
                                   role: MinimalRoleState, pending: dict[str, Any]) -> None:
        """Send the artifact-dungeon win reward once per (tower, layer, sn) (TASK-0299)."""

        tower = int(pending["tower"])
        layer = int(pending["layer"])
        sn = int(pending["sn"])
        key = dev_play_artifact.artifact_grant_key(role.role_id, tower, layer, sn)
        already = self.server.role_store.get_meta(key)
        if already is not None:
            self.server.event_logger.emit(
                "tcp_artifact_dgn_reward_already_granted",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=role.role_id,
                tower=tower, layer=layer, sn=sn,
                grant_key=key,
                previous=already,
                duplicate_suppressed=True,
                **session_state.get("_artifact_extra", {}),
            )
            return
        # TASK-0353: keep the cleared node in the archive so a relogin walks the same path.
        dev_play_artifact.record_clear(
            self.server.role_store, role.role_id, tower, layer, sn)
        self.server.event_logger.emit(
            "tcp_artifact_dgn_clear_recorded",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id, tower=tower, layer=layer, sn=sn,
            clear_key=dev_play_artifact.artifact_clear_key(role.role_id, tower, layer, sn),
            persisted=True, role_store_written=True,
        )
        gain_type = dev_play_artifact.ARTIFACT_GAIN_TYPE
        start = self._send_s2c(
            connection_id, writer, "returnGainInfoStart", (gain_type,),
            decoded_fields={"dGainType": gain_type},
            classification="synthetic_artifact_dgn_reward",
        )
        # TASK-0387: pay THIS node's cfg row instead of one fixed set (contract task_0384).
        node_rows = dev_play_artifact.artifact_reward_rows_for(layer, sn)
        if not node_rows:
            node_rows = dev_play_artifact.artifact_reward_rows()
        rows_sent = 0
        for reward_type, cfg_id, instance_id, num in node_rows:
            row = (int(reward_type), int(cfg_id), str(instance_id), int(num))
            self._send_s2c(
                connection_id, writer, "returnGainInfo", row,
                decoded_fields={"dRewardType": row[0], "dCfgId": row[1],
                                "strInstanceId": row[2], "dNum": row[3]},
                classification="synthetic_artifact_dgn_reward",
            )
            rows_sent += 1
        end = self._send_s2c(
            connection_id, writer, "returnGainInfoEnd", (),
            decoded_fields={},
            classification="synthetic_artifact_dgn_reward",
        )
        # TASK-0376: the panel is display-only; write the rows where the archive can hold them
        # and push the per-container updates so the player sees them without a relogin.
        role_record = session_state.get("role_record")
        if role_record is not None:
            self._write_artifact_dgn_reward(
                connection_id, writer, role, role_record,
                node_rows)
        self.server.role_store.set_meta(key, str(dev_play_artifact.ARTIFACT_REWARD_ID))
        self.server.event_logger.emit(
            "tcp_artifact_dgn_reward_granted",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            tower=tower, layer=layer, sn=sn,
            reward_id=dev_play_artifact.ARTIFACT_REWARD_ID,
            gain_type=gain_type,
            reward_rows=rows_sent,
            grant_key=key,
            packet_order=[start.spec.name, "returnGainInfo", "returnGainInfoEnd"],
            sequence_start=start.sequence,
            sequence_end=end.sequence,
            value_provenance=dev_play_artifact.ARTIFACT_REWARD_PROVENANCE,
            unknown_ids=["U-1", "U-3"],
            replaceable=True,
            historical=False,
            role_store_written=True,
            **session_state.get("_artifact_extra", {}),
        )

    def _handle_artifact_dgn_sel_card(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """TASK-0364: C2S 750 artifactDgnSelCard -- the 马车 node opens a card offer.

        The client never sends 747 for this node type: tapping it asks the server for the
        offer (C2S 750), the server streams S2C 1541 rows (code 1 start / 2 row / 4 end), and
        the player's pick comes back as C2S 751. Recording 750 without answering is what made
        the tile look dead (live 21:0x, twelve taps, no panel)."""

        role = session_state.get("role")
        if role is None:
            return
        try:
            sn = 1
            layer = 1
            try:
                layer = int(packet.fields[2])
                sn = int(packet.fields[3])
            except (IndexError, TypeError, ValueError):
                layer, sn = 1, 1
            self._send_artifact_card_offer(connection_id, writer, role, sn, session_state, layer)
        except Exception as exc:  # never let the offer take the connection down
            self.server.event_logger.emit(
                "tcp_artifact_dgn_card_offer_failed",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                error=type(exc).__name__,
                message=str(exc)[:200],
                connection_kept_alive=True,
            )
        return

    def _handle_artifact_dgn_do_sel_card(self, connection_id, writer, packet, session_state):
        """C2S 751 artifactDgnDoSelCard: grant the picked card and finish the node."""

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        # TASK-0386: ALWAYS answer. A silent return left the client retrying the pick in a
        # tight loop (live 22:29: 404 x C2S 751 in seven seconds), because only S2C 1542
        # closes the panel.
        if role is None or role_record is None:
            self._send_s2c(
                connection_id, writer, "artifactDgnDoSelCard", (0, 0),
                decoded_fields={"dCode": 0},
                classification="synthetic_artifact_dgn_card_pick_no_role",
            )
            return
        fields = [int(v) for v in packet.fields[:3]]
        sn = fields[0] if fields else 1
        raw_index = fields[1] if len(fields) > 1 else 1
        offered = session_state.get("artifact_card_offer") or []
        # TASK-0371: the client sends the GLOBAL key it was given (base+slot); the live log
        # carried [3, 8, 0] for the middle card of sn 3, where base = (3-1)*3 = 6.
        base = (max(1, int(sn)) - 1) * 3
        slot = None
        for candidate in (raw_index - base, raw_index):
            if 1 <= candidate <= len(offered):
                slot = candidate
                break
        chosen = offered[slot - 1] if slot else None
        layer, node_sn = session_state.get("artifact_card_node", (1, sn))
        instance_id = None
        if chosen is not None:
            instance_id = self.server.role_store.add_card_instance(
                role.role_id, role_record.account_key, int(chosen[1]),
                level=int(chosen[2]), exp=int(chosen[3]))
            # S2C 32 addCard "aNNNNNNNNaaaaa": the client only shows what it is told here.
            self._send_s2c(
                connection_id, writer, "addCard",
                (instance_id, int(chosen[1]), int(chosen[2]), int(chosen[3]), 0,
                 0, 0, 0, 0, "", "", "", "", ""),
                classification="synthetic_artifact_dgn_card_grant",
            )
        self._send_s2c(
            connection_id, writer, "artifactDgnDoSelCard", (0, 0),
            decoded_fields={"dCode": 0},
            classification="synthetic_artifact_dgn_card_pick",
        )
        session_state.pop("artifact_card_offer", None)
        tower = dev_play_artifact.ARTIFACT_RACE_TOWER_DEV_PLAY_STATE[1]
        path_fields = dev_play_artifact.path_after_clear(
            self.server.role_store, role.role_id, tower, int(layer), int(node_sn))
        dev_play_artifact.record_clear(
            self.server.role_store, role.role_id, tower, int(layer), int(node_sn))
        # TASK-0390: the picked card is a FINISHED node. 1542 alone closes the panel but leaves
        # the tile active, so the player had to open the team view before the carriage went
        # away (live 22:3x). 1531 + 1532(dIsFight=0) is the same answer every other non-battle
        # node gets, and it is what marks the node done on the map.
        self._send_s2c(
            connection_id, writer, "artifactDgnSendPath", path_fields,
            classification="synthetic_artifact_dgn_card_pick",
        )
        self._send_s2c(
            connection_id, writer, "artifactDgnDoEvt",
            dev_play_artifact.do_evt_fields(tower, int(layer), int(node_sn), is_fight=0),
            classification="synthetic_artifact_dgn_card_pick",
        )
        self._resend_tower_grid(connection_id, writer, session_state)
        self.server.event_logger.emit(
            "tcp_artifact_dgn_card_granted",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id, sn=int(node_sn), pick=raw_index,
            instance_id=instance_id,
            cfg_id=(None if chosen is None else int(chosen[1])),
            reward_granted=instance_id is not None,
            persisted=instance_id is not None,
            role_store_written=instance_id is not None,
            connection_kept_alive=True,
        )

    def _send_artifact_card_offer(self, connection_id, writer, role, sn=1, session_state=None, layer=1) -> None:
        """The S2C 1541 stream for one carriage node (code 1 / 2 xN / 4)."""

        try:
            rows = self.server.role_store.card_offer_rows(role.role_id, limit=3)
        except Exception:
            rows = []
        # TASK-0368: keys are GLOBAL indices grouped by sn -- getSelCardBySn(dSn) reads
        # (dSn-1)*MAX_REWARD_CARD_NUM+1 .. dSn*MAX and the code-4 row calls showSelCard(key)
        # with key == dSn. Writing key 0 for code 4 made the panel render three empty slots.
        base = (max(1, int(sn)) - 1) * 3
        if session_state is not None:
            session_state["artifact_card_offer"] = list(rows)
            session_state["artifact_card_node"] = (int(layer), int(sn))
        start = self._send_s2c(
            connection_id, writer, "artifactDgnSelCard",
            (1, 0, "", 0, 0, 0, ""),
            decoded_fields={"dCode": 1},
            classification="synthetic_artifact_dgn_card_offer",
        )
        sent = [start.spec.name]
        for index, row in enumerate(rows):
            fields = (2, base + index + 1, str(row[0]), int(row[1]), int(row[2]), int(row[3]), str(row[1]))
            self._send_s2c(
                connection_id, writer, "artifactDgnSelCard", fields,
                decoded_fields={"dCode": 2, "key": fields[1], "strInstanceId": fields[2],
                                "dCfgId": fields[3], "dLevel": fields[4],
                                "dExp": fields[5], "strTmpCard": fields[6]},
                classification="synthetic_artifact_dgn_card_offer",
            )
            sent.append("artifactDgnSelCard")
        end = self._send_s2c(
            connection_id, writer, "artifactDgnSelCard",
            (4, sn, "", 0, 0, 0, ""),
            decoded_fields={"dCode": 4},
            classification="synthetic_artifact_dgn_card_offer",
        )
        sent.append(end.spec.name)
        self.server.event_logger.emit(
            "tcp_artifact_dgn_card_offer_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            sn=int(sn),
            offered=len(rows),
            packet_order=sent,
            value_provenance="archive_card_rows",
            connection_kept_alive=True,
        )

    def _resend_tower_grid(self, connection_id, writer, session_state):
        """TASK-0359: re-send the whole node grid after an interaction.

        The client rebuilds its tower view from the LAST batch of S2C 1530 it received, so
        answering a node with only that layer's rows made every other layer's tiles vanish
        (live 01:0x: "the old tiles disappeared, and it jumped"). The full grid plus the
        archive path keeps the map complete and in place."""

        role = session_state.get("role")
        store = self.server.role_store
        if role is None:
            return
        path_values = dev_play_artifact.cleared_path(
            store, role.role_id,
            dev_play_artifact.ARTIFACT_RACE_TOWER_DEV_PLAY_STATE[1])
        # TASK-0362: fill the tower's member rows on this path too. le_tbCards (S2C 1537) is the
        # only table the 队伍 panel reads, so opening the tower from 继续探索 showed an empty team
        # even though the team commit path has always sent those rows.
        try:
            self._send_artifact_card_rows(
                connection_id, writer, session_state,
                dev_play_artifact.tower_team_csv(
                    dev_play_artifact.tower_card_instance_ids()),
                dict(extra),
            )
        except Exception as exc:  # never let the team preview break the entry
            self.server.event_logger.emit(
                "tcp_artifact_dgn_card_rows_failed",
                connection_id=connection_id,
                error=type(exc).__name__,
                message=str(exc)[:200],
            )
        for name, fields in dev_play_artifact.tower_data_packets(path_values):
            self._send_s2c(connection_id, writer, name, fields,
                           classification="synthetic_artifact_dgn_grid_resend")

    def _handle_artifact_dgn_go_next(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """TASK-0352: C2S 749 artifactDgnGoNext -- the "next layer" button.

        The client sends it with two ints (dLayer, dSn in the recovered reading) and expects the
        path row plus that layer's node rows so the new layer is walkable. It must never start a
        fight: the player asked to move, not to battle.
        """

        # TASK-0385: the button is a TOWER-COMPLETE action, not a per-layer step. The client
        # only sends it when getNextLayer() >= 13 (pkgArtifactDgnMgr.goNextTower), i.e. every
        # layer is done. Its two ints are the tower id and the hard flag -- NOT layer/sn -- so
        # reading fields[0] as a layer reset the whole tower to layer 1 (live 22:2x: "no more
        # monsters, cannot go down").  A new run means a new emptiness, so the clears of this
        # tower are dropped and the fresh path is sent.
        role = session_state.get("role")
        tower_id = dev_play_artifact.ARTIFACT_RACE_TOWER_DEV_PLAY_STATE[1]
        before = list(dev_play_artifact.cleared_path(
            self.server.role_store, role.role_id, tower_id)) if role is not None else [0]
        complete = max((i for i, v in enumerate(before, 1) if v > 0), default=0) >= 12
        if complete and role is not None:
            # A finished tower restarts: the run's clears are archived into the event first.
            self.server.role_store.delete_meta_prefix(
                f"artifact_dgn_clear:{role.role_id}:{tower_id}:")
            self.server.role_store.delete_meta_prefix(
                f"artifact_dgn_grant:{role.role_id}:{tower_id}:")
        fields = [int(v) for v in packet.fields[:2]]
        layer = 1
        sn = 1
        if not complete:
            layer = fields[0] if fields else 1
            inner = max((i for i, v in enumerate(before, 1) if v > 0), default=0) + 1
            if layer < 1 or layer < inner:
                layer = max(1, inner)
        # TASK-0358: rebuild from the archive instead of overwriting a static row. The old code
        # called path_fields_for_layer(layer, sn) with the CLIENT's sn (0), which zeroed every
        # slot and threw the player back to layer 1 (live 01:0x: "clicked next layer, told to
        # clear first").
        path_values = list(dev_play_artifact.cleared_path(
            self.server.role_store, session_state["role"].role_id,
            dev_play_artifact.ARTIFACT_RACE_TOWER_DEV_PLAY_STATE[1]))
        index = int(layer) - 1
        if 0 <= index < len(path_values):
            path_values[index] = max(path_values[index], 1)
        path_response = self._send_s2c(
            connection_id, writer, "artifactDgnSendPath", tuple(path_values),
            classification="synthetic_artifact_dgn_go_next",
        )
        sent = [path_response.spec.name]
        for row in dev_play_artifact.tower_node_rows_for_layer(layer):
            node = self._send_s2c(
                connection_id, writer, "artifactDgnSendNode", row,
                classification="synthetic_artifact_dgn_go_next",
            )
            sent.append(node.spec.name)
        self._resend_tower_grid(connection_id, writer, session_state)
        self.server.event_logger.emit(
            "tcp_artifact_dgn_go_next",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            request_fields=fields,
            layer=layer, sn=sn,
            packet_order=sent,
            node_rows=len(sent) - 1,
            starts_battle=False,
            value_provenance="dev_play_plan",
            connection_kept_alive=True,
        )

    def _advance_artifact_layer(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState | None,
        pending: dict[str, Any],
    ) -> None:
        """TASK-0348: after a tower WIN, make the NEXT layer walkable.

        Until now a win paid its reward but the tower view never learned about layer+1, so the
        player could clear a layer and then had nothing new to tap. The tower view reads its
        nodes from S2C 1530 and its progress from S2C 1531, so pushing both for layer+1 is the
        smallest honest answer: the same known-battle event (evt 56) is reused, because a node
        row only carries (dCode, dLayer, dSn, evtId) and the client resolves the event by id.
        """

        if role is None:
            return
        layer = int(pending["layer"])
        sn = int(pending["sn"])
        tower = int(pending["tower"])
        # TASK-0354: the path after a clear comes from the archive plus THIS clear. Clearing
        # sn 1 of a two-node layer must leave the player choosing sn 2 in the same layer, not
        # jump them to the next one (live session 00:4x).
        path_values = dev_play_artifact.path_after_clear(
            self.server.role_store, role.role_id, tower, layer, sn)
        next_layer = layer if int(sn) < dev_play_artifact.max_sn_for_layer(layer) else layer + 1
        path_response = self._send_s2c(
            connection_id, writer, "artifactDgnSendPath", path_values,
            classification="synthetic_artifact_dgn_layer_advance",
        )
        # TASK-0380: re-send the NEXT layer's REAL rows. This used to push two nodes carrying
        # the repeat-battle evt 56, which turned every tile of that layer into a plain fight
        # (live 21:5x: "the next node became an ordinary purple one"). The cfg rows are what
        # make a carriage stay a carriage.
        node_rows = []
        for fields in dev_play_artifact.tower_node_rows_for_layer(next_layer):
            self._send_s2c(
                connection_id, writer, "artifactDgnSendNode", fields,
                classification="synthetic_artifact_dgn_layer_advance",
            )
            node_rows.append(list(fields))
        self.server.event_logger.emit(
            "tcp_artifact_dgn_layer_advanced",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            tower=tower, cleared_layer=layer, cleared_sn=sn,
            next_layer=next_layer,
            packet_order=[path_response.spec.name] + ["artifactDgnSendNode"] * len(node_rows),
            node_rows=node_rows,
            evt_ids=[row[3] for row in node_rows],
            value_provenance="dev_play_plan_extension",
            unknown_ids=["U-advance"],
            replaceable=True,
            historical=False,
            state_changed=False,
            connection_kept_alive=True,
        )

    def _resolve_relic_pick(self, session_state, packet) -> int:
        """Map C2S 748's 1-based offer index to the relic id and persist it (TASK-0409)."""

        role = session_state.get("role")
        if role is None or not packet.fields:
            return 0
        try:
            index = int(packet.fields[0])
        except (TypeError, ValueError):
            return 0
        node = session_state.get("artifact_relic_node") or (1, 1, 1)
        layer, sn = int(node[1]), int(node[2])
        offer_key = f"artifact_dgn_yiwu_offer:{role.role_id}:1:1:{layer}:{sn}"
        raw = self.server.role_store.get_meta(offer_key)
        offer = [int(part) for part in str(raw or "").split(",") if part.strip().isdigit()]
        if not (1 <= index <= len(offer)):
            return 0
        relic_id = offer[index - 1]
        self.server.role_store.set_meta(
            f"artifact_dgn_yiwu:{role.role_id}:1:1:{relic_id}", "1")
        session_state.pop("artifact_relic_node", None)
        return relic_id

    def _offer_artifact_relic(self, connection_id, writer, session_state, layer, sn) -> None:
        """Send the S2C 1534 offer after a relic node is won (TASK-0409)."""

        role = session_state.get("role")
        if role is None:
            return
        if (int(layer), int(sn)) not in dev_play_artifact.ARTIFACT_RELIC_NODES:
            return
        offer = dev_play_artifact.relic_offer(int(layer) * 10 + int(sn))
        self.server.role_store.set_meta(
            f"artifact_dgn_yiwu_offer:{role.role_id}:1:1:{int(layer)}:{int(sn)}",
            ",".join(str(i) for i in offer))
        session_state["artifact_relic_node"] = (1, int(layer), int(sn))
        self._send_s2c(connection_id, writer, "artifactDgnSelYiwu", (1, 0),
                       classification="synthetic_artifact_dgn_relic_offer")
        for relic_id in offer:
            self._send_s2c(connection_id, writer, "artifactDgnSelYiwu", (2, int(relic_id)),
                           classification="synthetic_artifact_dgn_relic_offer")

    # ---------------------------------------------------------------- 挑战BOSS (TASK-0412)
    #: contract task_0406: act 1001 is the act the client's own test() enters (stage 101).
    CHALLENGE_BOSS_ACT_ID = 1001
    CHALLENGE_BOSS_STAGES = (101, 102, 103, 201, 202, 203)
    CHALLENGE_BOSS_DURATION_MINUTES = 420
    #: cfgChallengeBossReward[1102] for act 1001: money 6000 + 252x1 + 257x8 + 203x30.
    CHALLENGE_BOSS_REWARD_ROWS = ((7, 0, 6000), (252, 0, 1), (257, 0, 8), (203, 0, 30))

    def _challenge_boss_act_info(self, connection_id, writer) -> None:
        now = int(self.server.now_unix())
        self._send_s2c(
            connection_id, writer, "returnChallengeBossActInfo",
            (now, now + self.CHALLENGE_BOSS_DURATION_MINUTES * 60, self.CHALLENGE_BOSS_ACT_ID),
            classification="synthetic_challenge_boss_act")

    def _challenge_boss_data(self, connection_id, writer, session_state) -> None:
        role = session_state.get("role")
        kills = {}
        if role is not None:
            try:
                raw = self.server.role_store.get_meta(
                    f"challenge_boss_kill:{role.role_id}:{self.CHALLENGE_BOSS_ACT_ID}")
                kills = json.loads(raw) if raw else {}
            except Exception:
                kills = {}
        boss_rows = "&".join(
            "%d,0,%d" % (stage, int(kills.get(str(stage), 0)))
            for stage in self.CHALLENGE_BOSS_STAGES)
        claimed = 0
        if role is not None:
            try:
                raw = self.server.role_store.get_meta(
                    f"challenge_boss_claimed:{role.role_id}:{self.CHALLENGE_BOSS_ACT_ID}")
                claimed = int(raw or 0)
            except Exception:
                claimed = 0
        total_kills = sum(int(kills.get(str(stage), 0)) for stage in self.CHALLENGE_BOSS_STAGES)
        reward_rows = "&".join(
            "%d,%d" % (index + 1, 2 if index < claimed else (1 if index < total_kills else 0))
            for index in range(3))
        self._send_s2c(
            connection_id, writer, "returnChallengeBossData",
            # TASK-0426: pkgChallengeBossInfoViewMgr:79 enables the challenge button only when
            # dIndexId == dStageId or dIndexId == 0.  Sending 1 lit NOTHING (device screenshot:
            # every boss read 不可挑战), so the "any boss" sentinel is the correct value.
            (0, 0, 0, 0, boss_rows, reward_rows, 0),
            decoded_fields={"dReset": 0, "dIndexBoss": 0, "dMaxHurt": 0, "dRank": 0,
                            "strBoss": boss_rows, "strReward": reward_rows,
                            "dLastMaxHurt": 0},
            classification="synthetic_challenge_boss_data")

    def _challenge_boss_claim(self, connection_id, writer, packet, session_state) -> None:
        role = session_state.get("role")
        role_record = session_state.get("role_record")
        index = 0
        if packet.fields:
            try:
                index = int(packet.fields[0])
            except (TypeError, ValueError):
                index = 0
        granted = False
        if role is not None and role_record is not None and 1 <= index <= 3:
            key = f"challenge_boss_claimed:{role.role_id}:{self.CHALLENGE_BOSS_ACT_ID}"
            claimed = int(self.server.role_store.get_meta(key) or 0)
            if index > claimed:
                self._send_s2c(connection_id, writer, "returnGainInfoStart", (724,),
                               classification="synthetic_challenge_boss_reward")
                for reward_type, cfg_id, num in self.CHALLENGE_BOSS_REWARD_ROWS:
                    self._send_s2c(connection_id, writer, "returnGainInfo",
                                   (int(reward_type), int(cfg_id), "", int(num)),
                                   classification="synthetic_challenge_boss_reward")
                self._send_s2c(connection_id, writer, "returnGainInfoEnd", (),
                               classification="synthetic_challenge_boss_reward")
                try:
                    self._write_artifact_dgn_reward(
                        connection_id, writer, role, role_record,
                        tuple((t, c, "", n) for t, c, n in self.CHALLENGE_BOSS_REWARD_ROWS))
                except Exception:
                    pass
                self.server.role_store.set_meta(key, str(index))
                granted = True
        self._send_s2c(connection_id, writer, "returnChallengeBossGetReward",
                       (1 if granted else 0,),
                       classification="synthetic_challenge_boss_claim")

    # ------------------------------------------------------- 房间副本 (TASK-0425)
    #: contract task_0423: roomDungeonIndex has 7 rooms of 4 seals + 1 hide; the chain is
    #: S2C-FIRST (1362 before 1359, 1363/1366 before their Process packets).
    ROOM_DUNGEON_ROWS = 7
    ROOM_DUNGEON_SEALS = (11001, 11002, 11003, 11004)

    def _room_dungeon_enter(self, connection_id, writer, packet, session_state) -> None:
        role = session_state.get("role")
        instance = "room-%s-%d" % (0 if role is None else role.role_id, self.ROOM_DUNGEON_ROWS)
        self._send_s2c(connection_id, writer, "returnRoomDungeonCfg",
                       (1, 0, instance, 1, ""),
                       decoded_fields={"dCfgId": 1, "strInstanceId": instance},
                       classification="synthetic_room_dungeon_enter")
        self._send_s2c(connection_id, writer, "returnRoomDungeonDataStart", (),
                       classification="synthetic_room_dungeon_enter")
        for event_id in self.ROOM_DUNGEON_SEALS:
            self._send_s2c(connection_id, writer, "returnRoomDungeonDataProcess", (5, event_id),
                           classification="synthetic_room_dungeon_enter")
        self._send_s2c(connection_id, writer, "returnRoomDungeonDataEnd", (),
                       classification="synthetic_room_dungeon_enter")
        self._send_s2c(connection_id, writer, "returnRoomDungeonCompletedStart", (),
                       classification="synthetic_room_dungeon_enter")
        self._send_s2c(connection_id, writer, "returnRoomDungeonCompletedEnd", (),
                       classification="synthetic_room_dungeon_enter")
        # 1359 last: it reads le_tbCfg, which 1362 has just filled.
        self._send_s2c(connection_id, writer, "roomDungeonEnter", (instance, 1),
                       classification="synthetic_room_dungeon_enter")

    def _room_dungeon_event_done(self, connection_id, writer, packet, session_state) -> None:
        fields = [int(v) for v in packet.fields[:4]]
        event_type = fields[0] if fields else 5
        event_id = fields[1] if len(fields) > 1 else 0
        # EVENT_STATUS_SUCCESS = 1 on the first field of 1360; 1369 marks the row done.
        self._send_s2c(connection_id, writer, "roomDungeonEventDone", (1, 0, 0, 0, 0),
                       classification="synthetic_room_dungeon_event")
        self._send_s2c(connection_id, writer, "updateRoomDungeonCompleted",
                       (event_type, event_id),
                       classification="synthetic_room_dungeon_event")

    def _room_dungeon_finish(self, connection_id, writer, session_state) -> None:
        self._send_s2c(connection_id, writer, "roomDungeonFinish", (0,),
                       classification="synthetic_room_dungeon_finish")

    # --------------------------------------------------------------- 仙途 (TASK-0422/0428)
    #: contract task_0420: NO grid -- three layers.  S2C 239 has no lock partner, so it must be
    #: PUSHED before openFairyWay compares dLayerId on an empty table; 246/244 back the panel too.
    #: RUN-0079: clicking 活动 -> 封妖 -> 进入 removed the red point (so isFunctionOpen had passed
    #: and onEnterFairyway ran) and sent NOTHING, because pkgFairyWayMgr.openFairyWay compares
    #: le_tbFairyWayInfo.dLayerId (nil on a fresh client: the table starts as {}) with 0/number.
    #: The client never requests 239, so the server has to prime those globals before the first
    #: click -- the client's own S2C 720 reload boundary is the first point after the main city
    #: is up (see _fairy_way_prime and the receiveClientReload branch).
    FAIRY_WAY_MAX_LAYER = 3
    FAIRY_WAY_START_RESETS = 3
    FAIRY_WAY_START_MAGIC = 5.0
    #: Contract U-2: the original opponent snapshot is not recovered.  The match
    #: view only renders these fields (name/level/fight/career), so the stand-in
    #: is an explicit compatibility value, not a claim about the original server.
    FAIRY_WAY_OPPONENT_NAME = "封妖幻影"
    FAIRY_WAY_OPPONENT_LEVEL = 60
    FAIRY_WAY_OPPONENT_FIGHT = 14134
    #: TASK-0430: battle teams.  The opponent comes from the shipped
    #: fairyWayFight pool (159 rows, 15 card columns + soulId/magicId/rune);
    #: rows 1..3 are used for layers 1..3 (row 1: soul 2202, cards 2202/2208/
    #: 2209/2211/2224 at Lv5, runes 2012/2002; row 2 and row 3 carry their own
    #: five cards).  The player side uses the archive's own team preset, or the
    #: six highest-level owned cards when no preset exists.  Soul 101 is the
    #: shipped protagonist soul (cfg soul 101, sfmId1 11001).
    FAIRY_WAY_PLAYER_SOUL_CFG = 101
    FAIRY_WAY_PLAYER_SOUL_LEVEL = 60
    FAIRY_WAY_PREPARE_LEVEL = 60
    FAIRY_WAY_PREPARE_FIGHT = 4660
    FAIRY_WAY_FALLBACK_CARD_CFG = 1101
    FAIRY_WAY_OPPONENT_ROWS = {
        1: {"level": 25, "soul": 2202,
            "cards": ((2202, 5), (2208, 5), (2209, 5), (2211, 5), (2224, 5)),
            "runes": (2012, 2002)},
        2: {"level": 25, "soul": 2202,
            "cards": ((2211, 5), (2224, 5), (2218, 5), (2224, 5), (2225, 5)),
            "runes": (2012, 2002)},
        3: {"level": 21, "soul": 2202,
            "cards": ((2208, 5), (2209, 5), (2211, 5), (2224, 5), (2218, 5)),
            "runes": (2012, 2002)},
    }
    #: contract task_0420 §2: layer completion rewards come from
    #: fairyWayFixReward 1007/1008/1009 -- layer 1/2 = MONEY(7) 3000, layer 3 =
    #: ADV_PIECE(112) 2000 x3 + MONEY(7) 6000.  The client drains the 110 gain
    #: bucket when 243 status 2 arrives, so the block is sent first.
    FAIRY_WAY_GAIN_TYPE = 110
    FAIRY_WAY_LAYER_REWARDS = {
        1: ((7, 0, 3000),),
        2: ((7, 0, 3000),),
        3: ((112, 2000, 3), (7, 0, 6000)),
    }

    def _fairy_way_read(self, session_state) -> dict:
        """The five S2C-239 scalars, defaulted to a fresh account's state."""

        state: dict = {}
        role = session_state.get("role")
        if role is not None:
            try:
                raw = self.server.role_store.get_meta(f"fairy_way:{role.role_id}")
                loaded = json.loads(raw) if raw else {}
                if isinstance(loaded, dict):
                    state = loaded
            except (TypeError, ValueError):
                state = {}
        def _as_int(key, default):
            try:
                return int(state.get(key, default))
            except (TypeError, ValueError):
                return default
        try:
            magic = float(state.get("magic", 0.0))
        except (TypeError, ValueError):
            magic = 0.0
        return {
            "layer": _as_int("layer", 0),
            "reward": _as_int("reward", 0),
            "reset": _as_int("reset", self.FAIRY_WAY_START_RESETS),
            "change": _as_int("change", 0),
            "max": _as_int("max", 0),
            "buy": _as_int("buy", 0),
            "magic": magic,
        }

    def _fairy_way_write(self, session_state, state: dict) -> None:
        role = session_state.get("role")
        if role is None:
            return
        try:
            self.server.role_store.set_meta(
                f"fairy_way:{role.role_id}", json.dumps(state, sort_keys=True))
        except Exception:
            pass

    def _push_fairy_way(self, connection_id, writer, session_state) -> None:
        state = self._fairy_way_read(session_state)
        self._send_s2c(
            connection_id, writer, "returnFairyWayInfo",
            (state["layer"], state["reward"], state["reset"], state["change"],
             state["magic"]),
            decoded_fields={"dLayerId": state["layer"],
                            "dRewardLayer": state["reward"],
                            "dReset": state["reset"],
                            "dChangeTimes": state["change"],
                            "dMagicEnergy": state["magic"]},
            classification="synthetic_fairy_way_info")
        self._send_s2c(connection_id, writer, "returnFairyWayBuyResetTimes",
                       (state["buy"],),
                       classification="synthetic_fairy_way_info")
        self._send_s2c(connection_id, writer, "returnFairyWayMaxLayer",
                       (state["max"],),
                       classification="synthetic_fairy_way_info")

    def _fairy_way_prime(self, connection_id, writer, session_state) -> None:
        """Push 239/246/244 before the player can press 封妖 -> 进入.

        The client's openFairyWay (pkgFairyWayMgr.lua:122-135) reads these globals
        before any C2S exists, so the prime is a synthetic ordering choice anchored
        at the client's own reload completion (C2S 720 -> S2C 1487), not a reply.
        """

        self._push_fairy_way(connection_id, writer, session_state)

    def _fairy_way_layer(self, session_state) -> int:
        layer = session_state.get("fairy_way_layer")
        if layer is None:
            layer = self._fairy_way_read(session_state)["layer"]
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            layer = 1
        return min(max(layer, 1), self.FAIRY_WAY_MAX_LAYER)

    def _fairy_way_player_cards(self, session_state):
        account_key = session_state.get("account_key")
        if account_key is None:
            return ()
        store = self.server.role_store
        group = 1
        try:
            state = store.team_state(account_key=account_key)
            if state is not None:
                group = int(state.current_group)
        except Exception:
            group = 1
        cards = []
        for group_id in (group, 1):
            try:
                preset = store.team_preset(account_key=account_key, group_id=group_id)
            except Exception:
                preset = None
            if preset is None:
                continue
            for instance_id in preset.card_ids:
                if not instance_id:
                    continue
                try:
                    record = store.card_instance(
                        account_key=account_key, instance_id=str(instance_id))
                except Exception:
                    record = None
                if record is not None:
                    cards.append(record)
            if cards:
                break
        if not cards:
            try:
                owned = store.card_instances(account_key=account_key)
            except Exception:
                owned = ()
            cards = sorted(owned, key=lambda r: (int(r.level), int(r.advance_level)),
                           reverse=True)[:6]
        return tuple(cards)

    def _send_fairy_way_side(self, connection_id, writer, transfer_type, index,
                             *, role_id, level, fight, career, name, soul,
                             cards, runes, classification) -> None:
        self._send_s2c(connection_id, writer, "battleTransferBegin",
                       (transfer_type, 0.0, index),
                       decoded_fields={"transferType": transfer_type, "index": index},
                       classification=classification)
        self._send_s2c(
            connection_id, writer, "battleTransferBase",
            (transfer_type, 0.0, float(role_id), int(level), int(fight),
             int(career), name, "", False, 0),
            decoded_fields={"dRoleId": float(role_id), "dLevel": int(level),
                            "dFight": int(fight), "dCareer": int(career)},
            classification=classification)
        if soul is not None and int(soul[0]) > 0:
            self._send_s2c(
                connection_id, writer, "battleTransferSoul",
                (transfer_type, 0.0, int(soul[0]), int(soul[1]), 0, 0, False,
                 10000, 0, 0, 0),
                decoded_fields={"dCfgId": int(soul[0]), "dLevel": int(soul[1])},
                classification=classification)
        for cfg_id, card_level, exp, advance, sfm4, learn_hp, learn_atk in cards:
            self._send_s2c(
                connection_id, writer, "battleTransferCard",
                (transfer_type, 0.0, int(cfg_id), int(card_level), int(exp),
                 int(advance), int(sfm4), 0, False, 10000, 0,
                 int(learn_hp), int(learn_atk)),
                decoded_fields={"dCfgId": int(cfg_id), "dLevel": int(card_level)},
                classification=classification)
        for slot, rune_cfg in enumerate(runes or (), 1):
            if int(rune_cfg) <= 0:
                continue
            self._send_s2c(
                connection_id, writer, "battleTransferShenFu",
                (transfer_type, 0.0, slot, int(rune_cfg), 0, 0),
                decoded_fields={"dIndex": slot, "dCfgId": int(rune_cfg)},
                classification=classification)

    def _fairy_way_opponent_side(self, layer, role_id=0.0, career=1):
        row = self.FAIRY_WAY_OPPONENT_ROWS.get(
            int(layer), self.FAIRY_WAY_OPPONENT_ROWS[1])
        cards = tuple((cfg, lvl, 0, 0, 0, 0, 0) for cfg, lvl in row["cards"])
        return {
            "role_id": float(role_id), "level": int(row["level"]),
            "fight": self.FAIRY_WAY_PREPARE_FIGHT, "career": int(career),
            "name": self.FAIRY_WAY_OPPONENT_NAME,
            "soul": (int(row["soul"]), int(row["level"])),
            "cards": cards, "runes": tuple(int(r) for r in row["runes"]),
        }

    def _fairy_way_player_side(self, session_state):
        role = session_state.get("role")
        role_id = float(role.role_id) if role is not None else 0.0
        career = int(getattr(role, "career", 1) or 1) if role is not None else 1
        name = ((getattr(role, "role_name", "") or "道友")
                if role is not None else "道友")
        records = self._fairy_way_player_cards(session_state)
        cards = tuple((r.card_cfg_id, r.level, r.exp, r.advance_level,
                       r.sfm_id4, r.learn_hp, r.learn_atk) for r in records)
        if not cards:
            cards = tuple((self.FAIRY_WAY_FALLBACK_CARD_CFG, 10, 0, 0, 0, 0, 0)
                          for _ in range(5))
        return {
            "role_id": role_id, "level": self.FAIRY_WAY_PREPARE_LEVEL,
            "fight": self.FAIRY_WAY_PREPARE_FIGHT, "career": career,
            "name": name,
            "soul": (self.FAIRY_WAY_PLAYER_SOUL_CFG,
                     self.FAIRY_WAY_PLAYER_SOUL_LEVEL),
            "cards": cards, "runes": (),
        }

    def _fairy_way_push_match(self, connection_id, writer, session_state) -> None:
        """Push the layer's real opponent team into the 详情 view.

        The client's own C2S 47 has no caller, so the server fills le_tbMatchInfo
        under transferType 3: the opponent row from fairyWayFight (soul + five
        cards + two runes) at index 1, then S2C 242.  The row is what the match
        view renders in 法宝/神符/首发/援军 (TASK-0430).
        """

        layer = self._fairy_way_layer(session_state)
        side = self._fairy_way_opponent_side(layer)
        self._send_fairy_way_side(
            connection_id, writer, 3, 1, role_id=side["role_id"],
            level=side["level"], fight=side["fight"], career=side["career"],
            name=side["name"], soul=side["soul"], cards=side["cards"],
            runes=side["runes"], classification="synthetic_fairy_way_match")
        self._send_s2c(connection_id, writer, "returnfairyWayMatchEnd", (),
                       classification="synthetic_fairy_way_match")

    def _fairy_way_battle_prepare(self, connection_id, writer, session_state) -> None:
        """Fill BOTH PvP prepare slots from the player's team and the shipped pool.

        S2C 89 hasPrepareData makes the client read transferType 1, lstData[1] as
        team A and lstData[2] as team B.  A is the archive's own preset cards plus
        the protagonist soul; B is the fairyWayFight row for the current layer, so
        the two sides are no longer the same template (TASK-0430).  The values are
        shipped-config teams, not a claim about the original server's rank pick
        (contract U-2 still records that the rank -> row selection is unknown).
        """

        layer = self._fairy_way_layer(session_state)
        player = self._fairy_way_player_side(session_state)
        opponent = self._fairy_way_opponent_side(layer)
        self._send_fairy_way_side(
            connection_id, writer, 1, 1, role_id=player["role_id"],
            level=player["level"], fight=player["fight"], career=player["career"],
            name=player["name"], soul=player["soul"], cards=player["cards"],
            runes=player["runes"],
            classification="synthetic_fairy_way_battle_prepare")
        self._send_fairy_way_side(
            connection_id, writer, 1, 2, role_id=opponent["role_id"],
            level=opponent["level"], fight=opponent["fight"],
            career=opponent["career"], name=opponent["name"],
            soul=opponent["soul"], cards=opponent["cards"],
            runes=opponent["runes"],
            classification="synthetic_fairy_way_battle_prepare")

    def _fairy_way_start(self, connection_id, writer, session_state) -> None:
        state = self._fairy_way_read(session_state)
        if state["reset"] <= 0:
            # 240 is the lock partner: answer even on refusal.  The client only
            # opens the panel for status 1; 2 is the label it shows elsewhere.
            self._send_s2c(connection_id, writer, "returnFairyWayStartResult", (2,),
                           classification="synthetic_fairy_way_start_refused")
            return
        state["reset"] -= 1
        state["layer"] = 1
        state["reward"] = 0
        state["change"] = 0
        state["magic"] = self.FAIRY_WAY_START_MAGIC
        if state["max"] < 1:
            state["max"] = 1
        self._fairy_way_write(session_state, state)
        # 240 releases the 15 s lock; the panel then reads the refreshed 239.
        self._send_s2c(connection_id, writer, "returnFairyWayStartResult", (1,),
                       classification="synthetic_fairy_way_start")
        self._push_fairy_way(connection_id, writer, session_state)
        self._fairy_way_push_match(connection_id, writer, session_state)

    def _fairy_way_open_main(self, connection_id, writer, session_state) -> None:
        self._send_s2c(connection_id, writer, "fairyWayOpenMainUI", (),
                       classification="synthetic_fairy_way_open")
        self._push_fairy_way(connection_id, writer, session_state)
        # The layer buttons only become challenge buttons when match info exists.
        self._fairy_way_push_match(connection_id, writer, session_state)

    def _fairy_way_settlement(self, session_state, packet) -> bool:
        """A won type-53 fight leaves the layer's reward waiting (dRewardLayer).

        The client settles 封妖 through the ordinary C2S 67 / S2C 96 pair and
        then reopens the panel; the reward button only exists when S2C 239 names
        a non-zero dRewardLayer, so the win has to be persisted here.
        """

        fields = getattr(packet, "fields", ())
        if not fields:
            return False
        try:
            pve_type = int(fields[0])
        except (TypeError, ValueError):
            return False
        if pve_type != 53:
            return False
        layer = session_state.get("fairy_way_layer")
        if layer is None:
            return False
        try:
            result = decode_game_over_result(fields[13])
        except (IndexError, TypeError, ValueError):
            result = None
        if result != GAMEOVERRESULT_WIN:
            return False
        state = self._fairy_way_read(session_state)
        state["layer"] = int(layer)
        state["reward"] = int(layer)
        state["max"] = max(state["max"], int(layer))
        self._fairy_way_write(session_state, state)
        return True

    def _fairy_way_reward(self, connection_id, writer, packet, session_state) -> None:
        """C2S 49: only layer 3's race box asks for a race; 2 ends the reward round.

        Contract task_0420: status 3 = race select (layer 3 raceBox 20001),
        status 2 = grant.  Layers 1/2 use their fixed cfg reward directly.  The
        client's status-2 handler drains the FAIRYWAY_REWARD=110 bucket, so the
        81/83/82 block must already be on the wire.  Money is persisted and
        echoed with returnOneValue_p; the ADV_PIECE row is display-only because
        this archive has no persisted adv-piece container (noted as a gap).
        """

        role = session_state.get("role")
        pick = -1
        if packet.fields:
            try:
                pick = int(packet.fields[0])
            except (TypeError, ValueError):
                pick = -1
        state = self._fairy_way_read(session_state)
        reward_layer = int(state["reward"])
        if reward_layer <= 0:
            # Nothing pending; answer the lock partner and keep the panel sane.
            self._send_s2c(connection_id, writer, "returnFairyWayRewardStatus", (2,),
                           classification="synthetic_fairy_way_reward_empty")
            return
        if reward_layer == self.FAIRY_WAY_MAX_LAYER and pick <= 0:
            self._send_s2c(connection_id, writer, "returnFairyWayRewardStatus", (3,),
                           classification="synthetic_fairy_way_race_select")
            return
        rows = self.FAIRY_WAY_LAYER_REWARDS.get(reward_layer, ())
        self._send_s2c(connection_id, writer, "returnGainInfoStart",
                       (self.FAIRY_WAY_GAIN_TYPE,),
                       decoded_fields={"dGainType": self.FAIRY_WAY_GAIN_TYPE},
                       classification="synthetic_fairy_way_reward_gain")
        for reward_type, cfg_id, num in rows:
            self._send_s2c(
                connection_id, writer, "returnGainInfo",
                (int(reward_type), int(cfg_id), "", int(num)),
                decoded_fields={"dRewardType": int(reward_type),
                                "dCfgId": int(cfg_id), "strInstanceId": "",
                                "dNum": int(num)},
                classification="synthetic_fairy_way_reward_gain")
        self._send_s2c(connection_id, writer, "returnGainInfoEnd", (),
                       classification="synthetic_fairy_way_reward_gain")
        money = sum(int(num) for reward_type, _cfg, num in rows
                    if int(reward_type) == 7)
        role_record = session_state.get("role_record")
        if money and role_record is not None:
            try:
                total = self.server.role_store.adjust_role_wallet(
                    record=role_record, value_name="money", delta=float(money),
                    provenance="fairy_way_reward")
                self._send_s2c(
                    connection_id, writer, "returnOneValue_p",
                    (7, float(total)),
                    decoded_fields={"reward_type": 7, "amount": float(total)},
                    classification="synthetic_fairy_way_reward_money")
            except Exception as exc:
                self.server.event_logger.emit(
                    "tcp_fairy_way_reward_write_failed",
                    connection_id=connection_id,
                    error=type(exc).__name__,
                    message=str(exc)[:200],
                )
        next_layer = (reward_layer + 1
                      if reward_layer < self.FAIRY_WAY_MAX_LAYER else 0)
        state["layer"] = next_layer
        state["max"] = max(int(state["max"]), reward_layer)
        state["reward"] = 0
        self._fairy_way_write(session_state, state)
        self._send_s2c(connection_id, writer, "returnFairyWayRewardStatus", (2,),
                       classification="synthetic_fairy_way_reward")
        self._push_fairy_way(connection_id, writer, session_state)

    # --------------------------------------------------------------- 地牢 (TASK-0421)
    #: contract task_0419: the panel never shows without S2C 1377, so C2S 672 is the entry and
    #: C2S 681 is the once-per-second refresh whose three answers close with S2C 1387.
    def _prison_fight_enter(self, connection_id, writer, session_state) -> None:
        self._send_s2c(connection_id, writer, "returnPrisonFightRequestDataEnd", (),
                       classification="synthetic_prison_fight_enter")

    def _prison_fight_refresh(self, connection_id, writer, packet, session_state) -> None:
        """Answer one C2S 681 selector.  1370/1375 are the boss and point snapshots."""

        fields = [int(v) for v in packet.fields[:4]]
        # (1,0,0,0) asks for the boss record; (0,1,0,0) for the point record; (0,0,0,1) for all.
        want_boss = bool(fields and fields[0]) or (len(fields) > 3 and fields[3])
        want_point = bool(len(fields) > 1 and fields[1]) or (len(fields) > 3 and fields[3])
        if want_boss:
            self._send_s2c(
                connection_id, writer, "synPrisonFightBossToCg",
                (0, 0, 0, 1, 1, 480, 0, 0, 0, 0, 0),
                decoded_fields={"dTotalLayerCount": 480},
                classification="synthetic_prison_fight_refresh")
        if want_point:
            self._send_s2c(
                connection_id, writer, "synPrisonFightPointToCg",
                (1, 0, 0, 0, 0.0, 0, 0, 0.0, 0, 0),
                classification="synthetic_prison_fight_refresh")
        self._send_s2c(connection_id, writer, "returnPrisonFightGetDataEnd", (),
                       classification="synthetic_prison_fight_refresh")

    # ------------------------------------------------------- 材料副本 (TASK-0418)
    #: contract task_0417: cfgDgnFood has 17 NPC rows; the act allows 3 fights per cycle.
    DGN_FOOD_NPCS = tuple(range(1, 18))
    DGN_FOOD_ACT = 1
    DGN_FOOD_ATTEMPTS = 3
    DGN_FOOD_STATE_OPEN = 1

    def _dgn_food_attempts(self, session_state) -> int:
        role = session_state.get("role")
        if role is None:
            return self.DGN_FOOD_ATTEMPTS
        try:
            used = int(self.server.role_store.get_meta(f"dgn_food_used:{role.role_id}") or 0)
        except Exception:
            used = 0
        return max(0, self.DGN_FOOD_ATTEMPTS - used)

    def _dgn_food_enter(self, connection_id, writer, packet, session_state) -> None:
        act = self.DGN_FOOD_ACT
        if packet.fields:
            try:
                act = int(packet.fields[0])
            except (TypeError, ValueError):
                act = self.DGN_FOOD_ACT
        self._send_s2c(connection_id, writer, "dgnEnter", (act, 1),
                       classification="synthetic_dgn_food_enter")
        self._send_s2c(connection_id, writer, "dgnState",
                       (act, self._dgn_food_attempts(session_state),
                        self.DGN_FOOD_STATE_OPEN),
                       classification="synthetic_dgn_food_enter")
        for npc in self.DGN_FOOD_NPCS:
            self._send_s2c(connection_id, writer, "dgnNpcInfo", (act, npc, 1),
                           classification="synthetic_dgn_food_enter")

    def _dgn_food_start_fight(self, connection_id, writer, packet, session_state) -> None:
        fields = [int(v) for v in packet.fields[:3]]
        act = fields[0] if fields else self.DGN_FOOD_ACT
        npc = fields[1] if len(fields) > 1 else 0
        remaining = self._dgn_food_attempts(session_state)
        # seed 0 tells the client "no fight" (it only fights when count > 0), so a spent act
        # answers the same packet with 0 instead of leaving the button dead.
        seed = (act * 1000 + npc * 7 + 13) if remaining > 0 else 0
        self._send_s2c(connection_id, writer, "dgnFoodSynSeed",
                       (act, npc, seed, remaining),
                       decoded_fields={"dActId": act, "dNpcId": npc, "dSeed": seed,
                                       "dCount": remaining},
                       classification="synthetic_dgn_food_fight")

    def _dgn_food_record_fight(self, session_state) -> None:
        role = session_state.get("role")
        if role is None:
            return
        key = f"dgn_food_used:{role.role_id}"
        try:
            used = int(self.server.role_store.get_meta(key) or 0)
        except Exception:
            used = 0
        self.server.role_store.set_meta(key, str(min(self.DGN_FOOD_ATTEMPTS, used + 1)))

    # ------------------------------------------------------------- 锁妖塔 (TASK-0416)
    #: contract task_0414: floors 1..15 are the low tower and each floor owns a span of five
    #: stages starting at 6.  The server names the stage inside the span; the client only sends
    #: the floor (p1 of C2S 69).  U-1 records that the original pick inside the span is unknown.
    LOCK_TOWER_MAX_FLOOR = 15
    LOCK_TOWER_LIFE = 5

    def _lock_tower_pass(self, session_state) -> int:
        role = session_state.get("role")
        if role is None:
            return 0
        try:
            return int(self.server.role_store.get_meta(f"lock_tower_pass:{role.role_id}") or 0)
        except Exception:
            return 0

    def _lock_tower_life(self, session_state) -> int:
        """Remaining tower life (TASK-0428, contract U-3).

        The client's own gate is isLockTowerLifeOff() = (dTowerLife == 0) in
        pkgLockTowerInfo.lua:158-160; dTowerLife starts at 0 and is only written
        by S2C 672 returnLockTowerLevelAndTime or S2C 678 returnLockTowerBaseData.
        Before TASK-0428 this server sent neither, so RUN-0079 showed the panel
        opening with 2/5 life and the enter button answering 剩余生命不足 locally,
        without ever sending C2S 69.  The stock 5 is pkgLockTowerConst's full
        life; the decrement rule is U-3 (unknown), so a settlement does not
        spend it yet.
        """

        role = session_state.get("role")
        if role is None:
            return self.LOCK_TOWER_LIFE
        try:
            return int(self.server.role_store.get_meta(f"lock_tower_life:{role.role_id}")
                       or self.LOCK_TOWER_LIFE)
        except Exception:
            return self.LOCK_TOWER_LIFE

    def _lock_tower_stage(self, floor: int) -> int:
        return 6 + (max(1, int(floor)) - 1) * 5

    def _lock_tower_enter(self, connection_id, writer, session_state) -> None:
        self._send_s2c(connection_id, writer, "enterLockTower", (0, True),
                       classification="synthetic_lock_tower_enter")

    def _push_lock_tower_info(self, connection_id, writer, session_state) -> None:
        passed = self._lock_tower_pass(session_state)
        self._send_s2c(connection_id, writer, "returnTowerInfoStart", (),
                       classification="synthetic_lock_tower_info")
        # One gq row per floor: the client's getAllQq4Show walks the tower's
        # startLevel..endLevel range and attaches dShowLevel to each row it gets.
        for floor in range(1, self.LOCK_TOWER_MAX_FLOOR + 1):
            self._send_s2c(connection_id, writer, "returnTowerGqInfo",
                           (self._lock_tower_stage(floor), floor),
                           decoded_fields={"dEliteGqId": self._lock_tower_stage(floor),
                                           "dStep": floor},
                           classification="synthetic_lock_tower_info")
        # S2C 678 returnLockTowerBaseData 'NNyN' -- dLife, dPassLv, bHightTower,
        # dFirstPassHight.  It is what makes the enter button send C2S 69: the
        # client's life gate reads this packet's dLife, and its next floor is
        # dPassLv + 1.  Sent BEFORE 660 so the view opens with the state set.
        life = self._lock_tower_life(session_state)
        self._send_s2c(connection_id, writer, "returnLockTowerBaseData",
                       (life, passed, False, 0),
                       decoded_fields={"dLife": life, "dPassLv": passed,
                                       "bHightTower": False, "dFirstPassHight": 0},
                       classification="synthetic_lock_tower_info")
        self._send_s2c(connection_id, writer, "returnTowerInfoEnd", (),
                       classification="synthetic_lock_tower_info")

    def _lock_tower_default_cards(self, connection_id, writer) -> None:
        self._send_s2c(connection_id, writer, "returnLockTowerDefaultCardStart", (),
                       classification="synthetic_lock_tower_cards")
        self._send_s2c(connection_id, writer, "returnLockTowerDefaultCardEnd",
                       (False, 0, 0, 0, 0, 0, 0),
                       classification="synthetic_lock_tower_cards")

    def _lock_tower_record_win(self, session_state, floor: int) -> None:
        role = session_state.get("role")
        if role is None:
            return
        passed = self._lock_tower_pass(session_state)
        if int(floor) > passed:
            self.server.role_store.set_meta(
                f"lock_tower_pass:{role.role_id}", str(int(floor)))

    def _lock_tower_settlement(self, session_state, packet) -> None:
        """A settled type-93 fight advances the pass floor on a decoded victory.

        The client settles a 锁妖塔 fight through the ordinary C2S 67 / S2C 96
        pair; packet field 0 is pveOrPvpType and field 13 is the packed result
        slot that battle_rewards.decode_game_over_result understands
        (base 6179048, step 7218, RUN-0048's own loss sample 6193484).
        The floor comes from the C2S 69 this connection started; without it the
        reply must not guess.
        """

        fields = getattr(packet, "fields", ())
        if not fields:
            return
        try:
            pve_type = int(fields[0])
        except (TypeError, ValueError):
            return
        if pve_type != 93:
            return
        floor = session_state.get("lock_tower_floor")
        if floor is None:
            return
        try:
            result = decode_game_over_result(fields[13])
        except (IndexError, TypeError, ValueError):
            result = None
        if result == GAMEOVERRESULT_WIN:
            self._lock_tower_record_win(session_state, int(floor))

    # ------------------------------------------------------------ 城市挑战 (TASK-0415)
    #: contract task_0413: city 1 has five indexes, city 2 has ten; each index has a stage and an
    #: index reward.  City 3 has no config rows at all, so it stays closed.
    CITY_CHALLENGE_INDEXES = {1: 5, 2: 10}
    #: cfg.cityChallengeStage for (city 1, index 1..5); the server names the stage, not the client.
    CITY_CHALLENGE_STAGES = {
        (1, 1): 101, (1, 2): 102, (1, 3): 103, (1, 4): 104, (1, 5): 105,
    }
    CITY_CHALLENGE_INDEX_REWARD = {
        (1, 1): 20100, (1, 2): 20200, (1, 3): 20300, (1, 4): 20400, (1, 5): 20500,
    }

    def _city_challenge_state(self, session_state) -> dict:
        role = session_state.get("role")
        if role is None:
            return {}
        try:
            raw = self.server.role_store.get_meta(f"city_challenge:{role.role_id}")
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _push_city_challenge(self, connection_id, writer, session_state) -> None:
        """S2C 562 -- the ONLY thing that fills the view (C2S 290 is never sent)."""

        state = self._city_challenge_state(session_state)
        rows = []
        for city, count in sorted(self.CITY_CHALLENGE_INDEXES.items()):
            reached = int(state.get(f"city{city}", 0))
            index = min(count, reached + 1)
            rows.append("%d,%d,1" % (city, index))
        reward_rows = [str(value) for value in state.get("claimed", [])]
        self._send_s2c(
            connection_id, writer, "returnCityChallengeData",
            (";".join(rows), ";".join(reward_rows), 0, 0, 0, 0, 0, 0),
            decoded_fields={"strCityChallenge": ";".join(rows),
                            "strReward": ";".join(reward_rows)},
            classification="synthetic_city_challenge_data")

    def _city_challenge_claim(self, connection_id, writer, packet, session_state) -> None:
        role = session_state.get("role")
        role_record = session_state.get("role_record")
        fields = [int(v) for v in packet.fields[:2]]
        city = fields[0] if fields else 0
        index = fields[1] if len(fields) > 1 else 0
        granted = False
        if role is not None and role_record is not None:
            state = self._city_challenge_state(session_state)
            claimed = list(state.get("claimed", []))
            key = "%d,%d" % (city, index)
            reached = int(state.get("city%d" % city, 0))
            if index <= reached and key not in claimed:
                reward_id = self.CITY_CHALLENGE_INDEX_REWARD.get((city, index))
                if reward_id is not None:
                    try:
                        self.server.role_store.adjust_role_value(
                            account_key=role_record.account_key,
                            value_name="cityChallengeReward%d" % reward_id,
                            delta=1, provenance="city_challenge")
                    except Exception:
                        pass
                claimed.append(key)
                state["claimed"] = claimed
                self.server.role_store.set_meta(
                    f"city_challenge:{role.role_id}", json.dumps(state))
                granted = True
        self._send_s2c(connection_id, writer, "returnIndexReward",
                       (city, index, granted),
                       classification="synthetic_city_challenge_claim")
        self._push_city_challenge(connection_id, writer, session_state)

    def _owned_relic_ids(self, session_state) -> tuple:
        """The relic ids this run owns, from the archive meta (TASK-0408)."""

        role = session_state.get("role")
        if role is None:
            return ()
        prefix = f"artifact_dgn_yiwu:{role.role_id}:"
        try:
            rows = self.server.role_store.meta_with_prefix(prefix)
        except Exception:
            return ()
        ids = []
        for key, _value in rows:
            try:
                ids.append(int(key.rsplit(":", 1)[1]))
            except (IndexError, ValueError):
                continue
        return tuple(ids)

    def _send_artifact_points(self, connection_id, writer, account_key: str) -> None:
        """Push the two 神器积分 values the shop renders (171/173)."""

        for name, wire in (("artifactDgnPoint", 171), ("artifactDgnExt2", 173)):
            try:
                row = self.server.role_store._conn.execute(
                    "SELECT amount FROM role_values"
                    " WHERE account_key = ? AND value_name = ?",
                    (account_key, name),
                ).fetchone()
                if row is not None:
                    self._send_s2c(
                        connection_id,
                        writer,
                        "returnOneValue_N",
                        (wire, int(row[0])),
                        classification="artifact_shop_points",
                    )
            except Exception:
                pass

    def _push_artifact_shop(self, connection_id, writer, session_state) -> None:
        """Push one S2C 1547 row per shop shelf (TASK-0396, contract task_0392).

        The view opens without any request and indexes item.goods on its second line, so a shelf
        without a row raises and the render loop dies -- that is why the shop was empty."""

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        # TASK-0456 dev seed: enough 神器积分 (171) / ext2 (173) to exercise a
        # real purchase on the device.  Labelled synthetic and capped at 5000.
        if role_record is not None and self.server.dev_play_artifact_profile_matches:
            for value_name in ("artifactDgnPoint", "artifactDgnExt2"):
                try:
                    row = self.server.role_store._conn.execute(
                        "SELECT amount FROM role_values"
                        " WHERE account_key = ? AND value_name = ?",
                        (role_record.account_key, value_name),
                    ).fetchone()
                    have = int(row[0]) if row is not None else 0
                    if have < 20000:
                        self.server.role_store.adjust_role_value(
                            account_key=role_record.account_key,
                            value_name=value_name,
                            delta=20000 - have,
                            provenance="synthetic_dev_artifact_shop_points",
                        )
                except Exception:
                    pass
            self._send_artifact_points(connection_id, writer, role_record.account_key)
        counts = {}
        if role is not None:
            try:
                raw = self.server.role_store.get_meta(
                    f"artifact_shop_buy_count:{role.role_id}")
                counts = json.loads(raw) if raw else {}
            except Exception:
                counts = {}
        # TASK-0456: JSON keys are strings; shop_rows indexes by int shelf.
        counts = {int(k): int(v) for k, v in counts.items()}
        slots = self._artifact_shop_slots(role) if role is not None else {}
        for row in dev_play_artifact.shop_rows(
                self.server.now_unix(), buy_counts=counts,
                goods_by_shelf=slots):
            self._send_s2c(connection_id, writer, "artifactDgnShopShop", row,
                           decoded_fields={"dCode": row[0], "shelf": row[1],
                                           "goods": row[2], "buyCount": row[3]})
        # TASK-0460: dCode 3 dispatches GUILD_LIST_REFRESH, which is the view's
        # own refresh() call; without it the cells can keep stale/missing click
        # handlers after the first render.
        self._send_s2c(
            connection_id, writer, "artifactDgnShopShop", (3, 0, 0, 0, 0),
            classification="synthetic_artifact_shop_refresh_signal")

    def _artifact_shop_slots(self, role) -> dict:
        try:
            raw = self.server.role_store.get_meta(
                f"artifact_shop_slots:{int(role.role_id)}")
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _handle_artifact_shop_refresh(self, connection_id, writer, session_state) -> None:
        """C2S 757: reroll every shelf for this role, reset the buy limits.

        The client's 1549 handler is empty (unlock only), so the new rows are
        pushed right after it; dCode=3 makes the view rebuild its cells.
        """
        role = session_state.get("role")
        if role is not None:
            rng = SystemRandom()
            slots = {}
            for shelf, rows in dev_play_artifact.ARTIFACT_SHOP_GOODS.items():
                slots[str(int(shelf))] = int(rows[rng.randrange(len(rows))][0])
            try:
                self.server.role_store.set_meta(
                    f"artifact_shop_slots:{int(role.role_id)}",
                    json.dumps(slots, sort_keys=True))
                self.server.role_store.set_meta(
                    f"artifact_shop_buy_count:{int(role.role_id)}",
                    json.dumps({str(s): 1 for s in slots}, sort_keys=True))
            except Exception:
                pass
            self.server.event_logger.emit(
                "tcp_artifact_dgn_shop_refreshed",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=int(role.role_id),
                shelves=len(slots),
            )
        self._send_s2c(connection_id, writer, "artifactDgnShopUpdate", (0, 0),
                       classification="synthetic_artifact_shop_refresh")
        try:
            self._push_artifact_shop(connection_id, writer, session_state)
        except Exception:
            pass

    def _dev_card_row(self, row):
        """One S2C 30 tuple with the dev-only fields normalised (TASK-0477).

        A card written by refineSave/replace has refine_lv >= 1 in memory only
        after the store update; the login snapshot already normalises 0 -> 1,
        so the update rows must do the same or the client's next wash aborts at
        getLevelCfg(0) and stops responding.
        """

        fields = list(row.return_card_info_fields())
        dev = (
            self.server.dev_play_babel
            or self.server.dev_play_artifact
            or self.server.dev_play_resources
            or self.server.dev_play_gacha
        )
        if dev:
            if int(fields[14]) <= 0:
                fields[14] = 1
            if int(fields[8]) <= 0:
                four = dev_play_refine.initial_four(row.card_cfg_id)
                if four > 0:
                    fields[8] = four
        return tuple(fields)

    def _team_selection_meta(self, role, kind: str) -> dict:
        key = f"team_{kind}:{int(role.role_id)}"
        try:
            raw = self.server.role_store.get_meta(key)
        except Exception:
            raw = None
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _store_team_selection(self, role, kind: str, group: int,
                              index: int, value) -> None:
        data = self._team_selection_meta(role, kind)
        slots = data.setdefault(str(int(group)), {})
        slots[str(int(index))] = value
        try:
            self.server.role_store.set_meta(
                f"team_{kind}:{int(role.role_id)}",
                json.dumps(data, ensure_ascii=False, sort_keys=True))
        except Exception:
            pass

    def _replay_dev_team_selection(self, connection_id, writer,
                                   session_state) -> None:
        role = session_state.get("role")
        if role is None:
            return
        fabao = self._team_selection_meta(role, "fabao")
        self._send_s2c(connection_id, writer, "returnSelectMagicStart", (),
                       classification="synthetic_dev_team_fabao_start")
        for group, slots in sorted(fabao.items()):
            for index, value in sorted(slots.items()):
                if value:
                    self._send_s2c(
                        connection_id, writer, "returnSelectMagic",
                        (int(group), str(value), int(index)),
                        classification="synthetic_dev_team_fabao_row")
        self._send_s2c(connection_id, writer, "returnSelectMagicEnd", (),
                       classification="synthetic_dev_team_fabao_end")
        rune = self._team_selection_meta(role, "rune")
        self._send_s2c(connection_id, writer, "returnRuneSelectStart", (),
                       classification="synthetic_dev_team_rune_start")
        for group, slots in sorted(rune.items()):
            for index, value in sorted(slots.items()):
                if value:
                    self._send_s2c(
                        connection_id, writer, "returnRuneSelect",
                        (int(group), int(index), int(value)),
                        classification="synthetic_dev_team_rune_row")
        self._send_s2c(connection_id, writer, "returnRuneSelectEnd", (),
                       classification="synthetic_dev_team_rune_end")

    def _send_dev_talent_snapshot(self, connection_id, writer) -> None:
        """TASK-0493: grant every talent node at max level plus 999 points.

        TASK-0493 probe: REVIVAL_TALENT_NODES=0 sends no node rows and
        REVIVAL_TALENT_EXTRAS=0 skips the skill/soul side tables, so the device
        can A/B which group the talent view fails on.
        """

        import os as _os

        send_nodes = _os.environ.get("REVIVAL_TALENT_NODES", "1") != "0"
        send_extras = _os.environ.get("REVIVAL_TALENT_EXTRAS", "1") != "0"
        self._send_s2c(connection_id, writer, "returnTalentStart", (),
                       classification="synthetic_dev_talent_start")
        if send_nodes:
            for node_id, max_level, _type in dev_play_talent.TALENT_NODES:
                self._send_s2c(connection_id, writer, "returnTalent",
                               (int(node_id), int(max_level)),
                               classification="synthetic_dev_talent_node")
        if not send_extras:
            self._send_s2c(connection_id, writer, "returnTalentEnd", (),
                           classification="synthetic_dev_talent_end")
            self._send_s2c(connection_id, writer, "returnTalentPointStart", (),
                           classification="synthetic_dev_talent_point_start")
            for talent_type in range(1, 6):
                self._send_s2c(connection_id, writer, "returnTalentPoint",
                               (int(talent_type), 999),
                               classification="synthetic_dev_talent_point")
            self._send_s2c(connection_id, writer, "returnTalentPointEnd", (),
                           classification="synthetic_dev_talent_point_end")
            return
        # TASK-0493: talent-skill slots, "id:1,2,3@id:1,2,3".
        skill_table = "@".join(
            f"{int(node_id)}:1,2,3"
            for node_id, _lv, _t in dev_play_talent.TALENT_NODES
        )
        self._send_s2c(connection_id, writer, "returnTalentSkill",
                       (skill_table,),
                       classification="synthetic_dev_talent_skills")
        self._send_s2c(connection_id, writer, "returnTalentCardCut", ("",),
                       classification="synthetic_dev_talent_cut")
        self._send_s2c(connection_id, writer, "returnNodeSkill", ("",),
                       classification="synthetic_dev_talent_nodeskill")
        self._send_s2c(connection_id, writer, "returnTalentSoulNode", (0,),
                       classification="synthetic_dev_talent_soulnode")
        self._send_s2c(connection_id, writer, "returnTalentSoulId", (100,),
                       classification="synthetic_dev_talent_soulid")
        self._send_s2c(connection_id, writer, "returnTalentSoulSkill", (0,),
                       classification="synthetic_dev_talent_soulskill")
        self._send_s2c(connection_id, writer, "returnTalentResetTimes", (0,),
                       classification="synthetic_dev_talent_reset")
        self._send_s2c(connection_id, writer, "returnTalentSoulShowMode", (0,),
                       classification="synthetic_dev_talent_soulmode")
        self._send_s2c(connection_id, writer, "returnTalentEnd", (),
                       classification="synthetic_dev_talent_end")
        self._send_s2c(connection_id, writer, "returnTalentPointStart", (),
                       classification="synthetic_dev_talent_point_start")
        for talent_type in range(6):
            self._send_s2c(connection_id, writer, "returnTalentPoint",
                           (int(talent_type), 999),
                           classification="synthetic_dev_talent_point")
        self._send_s2c(connection_id, writer, "returnTalentPointEnd", (),
                       classification="synthetic_dev_talent_point_end")

    def _send_dev_fabao_snapshot(self, connection_id, writer) -> None:
        """TASK-0491/0492: grant every 法宝 at its max level (10).

        battle/superSkillLv.max carries exp0..exp10 (ten level-ups), so 10 is
        the shipped cap; the client reads dLevel straight from this row.
        """

        self._send_s2c(connection_id, writer, "returnMagicStart", (),
                       classification="synthetic_dev_fabao_start")
        zero_attrs = (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        for cfg_id in dev_play_fabao.FABAO_IDS:
            self._send_s2c(
                connection_id, writer, "returnMagicInfo",
                (f"dev-fabao-{cfg_id}", int(cfg_id), 4, 10, 0) + zero_attrs,
                classification="synthetic_dev_fabao_row",
            )
        self._send_s2c(connection_id, writer, "returnMagicEnd", (),
                       classification="synthetic_dev_fabao_end")

    def _send_dev_rune_snapshot(self, connection_id, writer) -> None:
        """TASK-0492: grant every shipped 神符 (battle/shenFuDef.max)."""

        self._send_s2c(connection_id, writer, "returnRuneInfoStart", (),
                       classification="synthetic_dev_rune_start")
        for cfg_id in dev_play_rune.RUNE_IDS:
            self._send_s2c(connection_id, writer, "returnRuneInfo",
                           (int(cfg_id),),
                           classification="synthetic_dev_rune_row")
        self._send_s2c(connection_id, writer, "returnRuneInfoEnd", (),
                       classification="synthetic_dev_rune_end")

    def _handle_skin_request(self, connection_id, writer, session_state) -> None:
        """C2S 627 -> S2C 1255 with every shipped skin unlocked (TASK-0487)."""

        self._send_s2c(
            connection_id, writer, "updateAllSkinsData",
            (
                ";".join(str(int(v)) for v in dev_play_skin.CAREER_SKINS),
                ";".join(str(int(v)) for v in dev_play_skin.CARD_SKINS),
                "",
            ),
            decoded_fields={
                "career_skins": len(dev_play_skin.CAREER_SKINS),
                "card_skins": len(dev_play_skin.CARD_SKINS),
            },
            classification="synthetic_dev_skin_unlock",
        )

    def _handle_skin_change(self, connection_id, writer, packet, session_state) -> None:
        raw = packet.fields[0]
        skin_id = (
            int(raw.decode("utf-8", "replace"))
            if isinstance(raw, (bytes, bytearray))
            else int(raw)
        )
        self._send_s2c(connection_id, writer, "changeCareerSkinFinish", (skin_id,),
                       classification="synthetic_dev_skin_change")

    def _handle_refine_card(self, connection_id, writer, packet, session_state) -> None:
        """C2S 195 refineCard(aNN) -> S2C 404 (TASK-0470).

        The wash itself is a server roll (refineLevel.weight); the three
        candidate ids are real battle/feature rows so the client can render
        them, and the chosen one is persisted by _handle_refine_save.
        """

        raw_instance = packet.fields[0]
        instance_id = (
            raw_instance.decode("utf-8", "replace")
            if isinstance(raw_instance, (bytes, bytearray))
            else str(raw_instance)
        )
        ops = int(packet.fields[2]) if len(packet.fields) > 2 else 0
        account_key = session_state.get("account_key")
        record = None
        if account_key is not None:
            record = self.server.role_store.card_instance(
                account_key=account_key, instance_id=instance_id)
        if record is None:
            self._send_s2c(
                connection_id, writer, "returnRefineCardFeatureList",
                (instance_id, 1, 0, 0, 0, 0, False, False, False, ops),
                classification="refine_card_missing",
            )
            return
        level = int(record.refine_lv) or 1
        result = dev_play_refine.roll_refine(record.card_cfg_id, level)
        if ops == 1:
            # TASK-0482: 高级洗练 costs one 三世镜 (reward type 160) and is a
            # guaranteed tier-up at 7/8/9; the client already validated the
            # level window and the local value.
            result = dict(result)
            result["code"] = 0
            result["lost_level"] = 0
        cfg = dev_play_refine.REFINE_LEVELS.get(level) or dev_play_refine.REFINE_LEVELS[1]
        max_fu = 0 if int(cfg[1]) else int(cfg[2])
        try:
            if int(result["code"]) == 0:
                # TASK-0480: success raises the tier directly, no blessing.
                updated_card = self.server.role_store.set_card_refine(
                    account_key=account_key,
                    instance_id=instance_id,
                    refine_lv=min(
                        dev_play_refine.MAX_REFINE_LEVEL, level + 1
                    ),
                    refine_skill=str(record.refine_skill or ""),
                    provenance="c2s195_refine_success",
                    refine_lv_times=0,
                )
            elif int(result["code"]) == 2:
                updated_card = self.server.role_store.set_card_refine(
                    account_key=account_key,
                    instance_id=instance_id,
                    refine_lv=int(result["lost_level"]),
                    refine_skill=str(record.refine_skill or ""),
                    provenance="c2s195_refine_level_down",
                    refine_lv_times=0,
                )
            elif max_fu > 0:
                # failure at tiers 1..5 adds one blessing; a full bar is a
                # guaranteed tier-up.
                updated_card = self.server.role_store.apply_refine_wash(
                    account_key=account_key,
                    instance_id=instance_id,
                    max_fu=max_fu,
                )
            else:
                updated_card = record
            self._send_s2c(
                connection_id, writer, "returnCardInfo",
                self._dev_card_row(updated_card),
                decoded_fields={
                    "card_instance_id": instance_id,
                    "refine_lv": int(updated_card.refine_lv),
                    "refine_lv_times": int(updated_card.refine_lv_times),
                },
                classification="refine_card_update",
            )
        except Exception:
            pass
        session_state["refine_pending"] = {
            "instance_id": instance_id,
            "candidates": tuple(int(v) for v in result["candidates"]),
            "new_level": int(result["new_level"]),
            "ops": ops,
        }
        f1, f2, f3 = result["candidates"]
        self._send_s2c(
            connection_id, writer, "returnRefineCardFeatureList",
            (instance_id, int(result["code"]), int(result["lost_level"]),
             int(f1), int(f2), int(f3),
             bool(result["cores"][0]), bool(result["cores"][1]),
             bool(result["cores"][2]), ops),
            decoded_fields={
                "card_instance_id": instance_id,
                "result": int(result["code"]),
                "lost_level": int(result["lost_level"]),
                "features": [int(f1), int(f2), int(f3)],
                "refine_level": level,
                "next_level": int(result["new_level"]),
            },
            classification="synthetic_refine_roll",
        )
        self.server.event_logger.emit(
            "tcp_refine_roll",
            connection_id=connection_id,
            role_id=None if session_state.get("role") is None
            else int(session_state["role"].role_id),
            card_instance_id=instance_id,
            refine_level=level,
            result=int(result["code"]),
            features=[int(f1), int(f2), int(f3)],
            next_level=int(result["new_level"]),
        )

    def _handle_refine_save(self, connection_id, writer, packet, session_state) -> None:
        """C2S 310 refineSave(aNN): persist the chosen feature, echo the card."""

        raw_instance = packet.fields[0]
        instance_id = (
            raw_instance.decode("utf-8", "replace")
            if isinstance(raw_instance, (bytes, bytearray))
            else str(raw_instance)
        )
        feat_id = int(packet.fields[1])
        account_key = session_state.get("account_key")
        role = session_state.get("role")
        record = None
        if account_key is not None:
            record = self.server.role_store.card_instance(
                account_key=account_key, instance_id=instance_id)
        if record is None or role is None:
            self._send_s2c(connection_id, writer, "refineSave", (2,),
                           classification="refine_save_missing")
            return
        skills = [s for s in str(record.refine_skill or "").split("?") if s]
        if str(feat_id) in skills:
            self._send_s2c(connection_id, writer, "refineSave", (1,),
                           classification="refine_save_duplicate")
            return
        new_level = int(record.refine_lv) or 1
        skills.append(str(feat_id))
        try:
            updated = self.server.role_store.set_card_refine(
                account_key=account_key,
                instance_id=instance_id,
                refine_lv=new_level,
                refine_skill="?".join(skills),
                provenance="c2s310_refine_save",
                refine_lv_times=int(record.refine_lv_times),
            )
        except Exception:
            self._send_s2c(connection_id, writer, "refineSave", (2,),
                           classification="refine_save_failed")
            return
        session_state.pop("refine_pending", None)
        self._send_s2c(connection_id, writer, "refineSave", (0,),
                       classification="synthetic_refine_save")
        self._send_s2c(
            connection_id, writer, "returnCardInfo",
            self._dev_card_row(updated),
            decoded_fields={
                "card_instance_id": instance_id,
                "refine_lv": int(updated.refine_lv),
                "refine_skill": str(updated.refine_skill),
            },
            classification="refine_card_update",
        )
        self._send_s2c(connection_id, writer, "refineRefineStone", (99999,),
                       classification="synthetic_refine_stone")
        self.server.event_logger.emit(
            "tcp_refine_save",
            connection_id=connection_id,
            role_id=int(role.role_id),
            card_instance_id=instance_id,
            feat_id=feat_id,
            refine_lv=int(updated.refine_lv),
            skills=str(updated.refine_skill),
        )

    def _handle_refine_replace(self, connection_id, writer, packet, session_state) -> None:
        """C2S 196 replaceCardSkill(aNN): swap the 4th skill for a candidate.

        The wash panel's 替换 button sends the 1-based candidate index; the
        client then closes the view on S2C 405 and reads the new sfmId4 from the
        S2C 30 row this method sends.
        """

        raw_instance = packet.fields[0]
        instance_id = (
            raw_instance.decode("utf-8", "replace")
            if isinstance(raw_instance, (bytes, bytearray))
            else str(raw_instance)
        )
        index = int(packet.fields[1])
        account_key = session_state.get("account_key")
        pending = session_state.get("refine_pending") or {}
        candidates = tuple(pending.get("candidates") or ())
        if account_key is None or not (1 <= index <= len(candidates)):
            self._send_s2c(connection_id, writer,
                           "returnRefineCardReplaceResult", (1,),
                           classification="refine_replace_refused")
            return
        feat_id = int(candidates[index - 1])
        try:
            updated = self.server.role_store.set_card_sfm4(
                account_key=account_key,
                instance_id=instance_id,
                sfm_id4=feat_id,
                provenance="c2s196_replace_skill",
            )
        except Exception:
            self._send_s2c(connection_id, writer,
                           "returnRefineCardReplaceResult", (2,),
                           classification="refine_replace_failed")
            return
        session_state.pop("refine_pending", None)
        self._send_s2c(connection_id, writer,
                       "returnRefineCardReplaceResult", (0,),
                       classification="synthetic_refine_replace")
        self._send_s2c(
            connection_id, writer, "returnCardInfo",
            self._dev_card_row(updated),
            decoded_fields={
                "card_instance_id": instance_id,
                "sfm_id4": int(updated.sfm_id4),
            },
            classification="refine_card_update",
        )
        self.server.event_logger.emit(
            "tcp_refine_replace",
            connection_id=connection_id,
            card_instance_id=instance_id,
            sfm_id4=feat_id,
        )

    def _handle_artifact_shop_buy(self, connection_id, writer, packet, session_state) -> None:
        """C2S 756 artifactDgnShopBuy (LOCK-SEND): charge, grant what we can hold, always answer.

        A weapon is an instance in pkgGodWeaponMgr and needs S2C 1625 with an instance id the
        archive cannot mint yet, so a 164 shelf is answered WITHOUT charging and recorded as an
        unsupported buy.  A special-card shelf (8886) is real: it charges and grants."""

        fields = [int(v) for v in packet.fields[:4]]
        shelf = fields[0] if fields else 0
        buy_num = fields[2] if len(fields) > 2 and fields[2] > 0 else 1
        role = session_state.get("role")
        role_record = session_state.get("role_record")
        goods_id = fields[1] if len(fields) > 1 else 0
        # TASK-0401: resolve (shelf, goods) -- shelf 106 offers four special cards at four
        # prices, so the goods id the client names decides the cost and the grant.
        entry = dev_play_artifact.shop_entry(shelf, goods_id)
        if entry is None and shelf in dev_play_artifact.ARTIFACT_SHOP_GOODS:
            entry = dev_play_artifact.ARTIFACT_SHOP_GOODS[shelf][0]
        granted = None
        charged = None
        reason = None
        if entry is None:
            reason = "unknown_shelf"
        elif role is None or role_record is None:
            reason = "no_role"
        else:
            _goods, reward_type, cfg_id, _gwd, cost_type, cost, _name = entry
            value_name = ("artifactDgnPoint" if int(cost_type) == 171
                          else "artifactDgnExt2")
            balance = 0
            try:
                row = self.server.role_store._conn.execute(
                    "SELECT amount FROM role_values WHERE account_key = ? AND value_name = ?",
                    (role_record.account_key, value_name)).fetchone()
                balance = int(row[0]) if row is not None else 0
            except Exception:
                balance = 0
            try:
                raw_counts = self.server.role_store.get_meta(
                    f"artifact_shop_buy_count:{role.role_id}")
                meta_counts = json.loads(raw_counts) if raw_counts else {}
            except Exception:
                meta_counts = {}
            sold_out = int(meta_counts.get(str(int(shelf)), 1)) <= 0
            if int(reward_type) == 164:
                # TASK-0397: a 164 shelf names a concrete 6-star weapon through gdId, so the
                # buy can grant it for real (S2C 1625) instead of reporting itself unsupported.
                if sold_out:
                    reason = "sold_out"
                elif balance < int(cost) * buy_num:
                    reason = "goods_not_enough"
                else:
                    try:
                        instance_id = self.server.role_store.add_god_weapon(
                            account_key=role_record.account_key,
                            role_id=role.role_id, cfg_id=int(_gwd),
                            star=6, provenance="artifact_dgn_shop")
                        self.server.role_store.adjust_role_value(
                            account_key=role_record.account_key, value_name=value_name,
                            delta=-int(cost) * buy_num, provenance="artifact_dgn_shop")
                        granted = instance_id
                        charged = int(cost) * buy_num
                        self._send_s2c(
                            connection_id, writer, "returnGainInfoStart",
                            (2,), classification="synthetic_artifact_shop_gain")
                        self._send_s2c(
                            connection_id, writer, "returnGainInfo",
                            (162, int(_gwd), instance_id, 1),
                            classification="synthetic_artifact_shop_gain")
                        self._send_s2c(
                            connection_id, writer, "returnGainInfoEnd",
                            (), classification="synthetic_artifact_shop_gain")
                        # TASK-0456: the registration is aNNNNyyNNNNN (12
                        # values): instance, cfg, level, rare, exp, lock,
                        # garbage, feature1..5.  An extra value here raised
                        # MalformedPacket AFTER the grant and the deduction.
                        self._send_s2c(
                            connection_id, writer, "returnAddSingleGodWeapon",
                            (instance_id, int(_gwd), 6, 0, 0, False, False,
                             0, 0, 0, 0, 0),
                            classification="synthetic_artifact_shop_weapon")
                    except Exception as exc:
                        reason = type(exc).__name__
            elif sold_out:
                reason = "sold_out"
            elif balance < int(cost) * buy_num:
                reason = "goods_not_enough"
            else:
                try:
                    total = self.server.role_store.add_special_card(
                        account_key=role_record.account_key, cfg_id=int(cfg_id),
                        count=int(buy_num), provenance="artifact_dgn_shop")
                    self.server.role_store.adjust_role_value(
                        account_key=role_record.account_key, value_name=value_name,
                        delta=-int(cost) * buy_num, provenance="artifact_dgn_shop")
                    granted = total
                    charged = int(cost) * buy_num
                    self._send_s2c(
                        connection_id, writer, "returnGainInfoStart",
                        (2,), classification="synthetic_artifact_shop_gain")
                    self._send_s2c(
                        connection_id, writer, "returnGainInfo",
                        (8886, int(cfg_id), "", int(buy_num)),
                        classification="synthetic_artifact_shop_gain")
                    self._send_s2c(
                        connection_id, writer, "returnGainInfoEnd",
                        (), classification="synthetic_artifact_shop_gain")
                    self._send_s2c(connection_id, writer, "returnSingleSpecialCard",
                                   (int(cfg_id), int(total)),
                                   classification="synthetic_artifact_shop_grant")
                except Exception as exc:
                    reason = type(exc).__name__
        if charged is not None and role is not None:
            account_key = session_state.get("account_key")
            if account_key:
                self._send_artifact_points(connection_id, writer, account_key)
            try:
                key = f"artifact_shop_buy_count:{role.role_id}"
                raw = self.server.role_store.get_meta(key)
                counts = json.loads(raw) if raw else {}
                counts[str(int(shelf))] = max(0, int(counts.get(str(int(shelf)), 1)) - 1)
                self.server.role_store.set_meta(key, json.dumps(counts))
                self._send_s2c(
                    connection_id, writer, "artifactDgnShopShop",
                    (2, int(shelf), int(goods_id), counts[str(int(shelf))],
                     int(self.server.now_unix()) + dev_play_artifact.ARTIFACT_SHOP_REFRESH_SECONDS),
                    classification="synthetic_artifact_shop_restock")
            except Exception:
                pass
        self._send_s2c(connection_id, writer, "artifactDgnShopBuy", (0, 0),
                       decoded_fields={"dCode": 0},
                       classification="synthetic_artifact_shop_buy")
        self.server.event_logger.emit(
            "tcp_artifact_dgn_shop_buy",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            shelf=shelf, buy_num=buy_num, charged=charged, granted=granted, reason=reason,
            connection_kept_alive=True,
        )

    def _grant_artifact_non_battle_reward(
        self, connection_id, writer, session_state, tower_id, layer, sn, rows
    ) -> None:
        """Send the gain trio for one non-battle node, once per (tower, layer, sn).

        The chest that pays the 神器 tokens is a non-battle node, so this is the only place the
        player can receive reward type 171/173 in the tower (contract task_0384)."""

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        if role is None or role_record is None:
            return
        key = f"artifact_dgn_node_grant:{role.role_id}:{tower_id}:{layer}:{sn}"
        if self.server.role_store.get_meta(key):
            return
        self._send_s2c(connection_id, writer, "returnGainInfoStart", (596,),
                       classification="synthetic_artifact_dgn_node_reward")
        for row in rows:
            self._send_s2c(connection_id, writer, "returnGainInfo", row,
                           classification="synthetic_artifact_dgn_node_reward")
        self._send_s2c(connection_id, writer, "returnGainInfoEnd", (),
                       classification="synthetic_artifact_dgn_node_reward")
        self._write_artifact_dgn_reward(
            connection_id, writer, role, role_record, rows)
        self.server.role_store.set_meta(key, "1")

    def _write_artifact_dgn_reward(self, connection_id, writer, role, role_record, rows):
        """Write the artifact tower's reward rows where the archive can hold them (TASK-0376).

        Contract task_0375: S2C 81/83/82 is display-only, so the panel is not a grant. The rows
        map to  7 MONEY -> role_wallet,  8886 SPECIAL_CARD -> card_special_inventory, and
        171 ARTIFACT_DGN_POINT -> the archive meta scalar (role_wallet is frozen to
        money/yinpo/jinpo, so the point has no typed row yet)."""

        money, special, point = dev_play_artifact.artifact_reward_writes(rows)
        written = {}
        try:
            if money:
                total = self.server.role_store.adjust_role_wallet(
                    record=role_record, value_name="money", delta=float(money),
                    provenance="artifact_dgn_reward")
                written["money"] = total
                self._send_s2c(connection_id, writer, "returnOneValue_p",
                               (7, float(total)),
                               classification="synthetic_artifact_dgn_reward_value")
            if special:
                total = self.server.role_store.add_special_card(
                    account_key=role_record.account_key,
                    cfg_id=dev_play_artifact.ARTIFACT_SPECIAL_CARD_CFG_ID,
                    count=int(special), provenance="artifact_dgn_reward")
                written["special_card"] = total
                self._send_s2c(connection_id, writer, "returnSingleSpecialCard",
                               (dev_play_artifact.ARTIFACT_SPECIAL_CARD_CFG_ID, int(total)),
                               classification="synthetic_artifact_dgn_reward_special_card")
            if point:
                # TASK-0377: structure 13's role_values is the typed home for this point --
                # role_wallet is still frozen to money/yinpo/jinpo.
                total = self.server.role_store.adjust_role_value(
                    account_key=role_record.account_key,
                    value_name="artifactDgnPoint", delta=int(point),
                    provenance="artifact_dgn_reward")
                written["artifact_dgn_point"] = total
                self._send_s2c(connection_id, writer, "returnOneValue_N",
                               (171, int(total)),
                               classification="synthetic_artifact_dgn_reward_point")
        except Exception as exc:
            self.server.event_logger.emit(
                "tcp_artifact_dgn_reward_write_failed",
                connection_id=connection_id,
                error=type(exc).__name__,
                message=str(exc)[:200],
                connection_kept_alive=True,
            )
            return
        self.server.event_logger.emit(
            "tcp_artifact_dgn_reward_written",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            rows=[list(row) for row in rows],
            written=written,
            typed_homes={"money": "role_wallet", "special_card": "card_special_inventory",
                         "artifact_dgn_point": "role_values"},
            role_store_written=bool(written),
            persisted=bool(written),
            connection_kept_alive=True,
        )

    def _grant_uncharted_win_reward(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState | None,
        packet,
    ) -> None:
        """TASK-0344: push the 秘境 WIN gain block BEFORE S2C 96.

        pkgGameOverDisplay.showUnchartedPveGameOverView drains TB_GAIN_TYPE.FIGHT (=1) in the
        same turn it renders the panel, so the 81/83/82 block must already be on the wire. The
        rows come straight from cfg unchartedReward[unchartedBoss[bossId].rewardId] -- for boss 1
        that is 2001 = (type 9 PIECE, itemId 4438, num 5) -- never from a guess.

        The pending start is PEEKED, not popped: _record_dungeon_settlement consumes it later.
        """

        if not packet.fields:
            return
        try:
            pve_type = int(packet.fields[0])
        except (TypeError, ValueError):
            return
        if pve_type != BATTLE_START_UNCHARTED_PVE_TYPE:
            return
        starts = session_state.get("dungeon_battle_start")
        if not isinstance(starts, dict):
            return
        pending = starts.get(pve_type)
        if not isinstance(pending, dict):
            return
        if role is None or session_state.get("role_record") is None:
            return
        if pending.get("role_id") != role.role_id:
            return
        result_code = (
            decode_game_over_result(packet.fields[13])
            if len(packet.fields) > 13
            else None
        )
        if result_code != GAMEOVERRESULT_WIN:
            return
        uncharted_id = int(pending.get("uncharted_id") or 1)
        layer = int(pending.get("layer") or 1)
        area = int(pending.get("area") or 1)
        room = int(pending.get("room") or 1)
        reward_row = dev_play_uncharted.UNCHARTED_BOSS_REWARD_ROWS.get(1)
        if reward_row is None:
            return
        key = dev_play_uncharted.uncharted_grant_key(
            role.role_id, uncharted_id, layer, area, room, 1)
        store = self.server.role_store
        already = store.get_meta(key)
        if already is not None:
            self.server.event_logger.emit(
                "tcp_uncharted_reward_already_granted",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=role.role_id,
                grant_key=key,
                reward_id=already,
                reward_granted=False,
                pushed=False,
                persisted=False,
                role_store_written=False,
                connection_kept_alive=True,
            )
            return
        # TASK-0394: write the piece into the archive and push its own container update. The
        # gain block is display-only (contract task_0375), so without this the 碎片 was shown
        # once and never stored.
        try:
            role_record = session_state.get("role_record")
            for reward_type, cfg_id, num in reward_row:
                if int(reward_type) == dev_play_artifact.UNCHARTED_PIECE_REWARD_TYPE:
                    total = self.server.role_store.add_card_piece(
                        account_key=role_record.account_key,
                        card_cfg_id=int(cfg_id), count=int(num),
                        provenance="uncharted_reward")
                    self._send_s2c(
                        connection_id, writer, "returnSingleRe",
                        (int(cfg_id), int(total)),
                        classification="synthetic_uncharted_piece")
        except Exception as exc:
            self.server.event_logger.emit(
                "tcp_uncharted_piece_write_failed",
                connection_id=connection_id,
                error=type(exc).__name__,
                message=str(exc)[:200],
            )
        sent: list[str] = []
        start = self._send_s2c(
            connection_id, writer, "returnGainInfoStart",
            (dev_play_uncharted.UNCHARTED_WIN_GAIN_TYPE,),
            decoded_fields={"dGainType": dev_play_uncharted.UNCHARTED_WIN_GAIN_TYPE},
            classification="synthetic_uncharted_win_gain",
        )
        sent.append(start.spec.name)
        for reward_type, cfg_id, num in reward_row:
            row = self._send_s2c(
                connection_id, writer, "returnGainInfo",
                (int(reward_type), int(cfg_id), "", int(num)),
                decoded_fields={"dRewardType": int(reward_type),
                                "dCfgId": int(cfg_id),
                                "strInstanceId": "",
                                "dNum": int(num)},
                classification="synthetic_uncharted_win_gain",
            )
            sent.append(row.spec.name)
        end = self._send_s2c(
            connection_id, writer, "returnGainInfoEnd", (),
            classification="synthetic_uncharted_win_gain",
        )
        sent.append(end.spec.name)
        store.set_meta(key, "2001")
        self.server.event_logger.emit(
            "tcp_uncharted_reward_granted",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            grant_key=key,
            uncharted_id=uncharted_id, layer=layer, area=area, room=room,
            boss_id=1, reward_id=2001,
            gain_type=dev_play_uncharted.UNCHARTED_WIN_GAIN_TYPE,
            packet_order=sent,
            rows=[list(row) for row in reward_row],
            value_provenance="cfg_unchartedReward_2001",
            reward_granted=True,
            pushed=True,
            persisted=False,
            role_store_written=True,
            state_changed=False,
            connection_kept_alive=True,
        )

    def _record_dungeon_settlement(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState | None,
        packet,
        response,
    ) -> None:
        """Pair one artifact (66) / uncharted (88) settlement with its own C2S 69.

        TASK-0287.  A pair needs, on THIS connection: the C2S 69 that was
        answered with the settlement's own `pveOrPvpType` (remembered by the
        netEnterPveBattle branch under that int), the same role, and a decoded
        `dPackCheckFlag` (recorded and compared with the S2C 89
        `dSyncCount` -- evidence, never a gate).

        NOTHING is persisted and NOTHING is rewarded: no chapter row, no
        wallet, no card and no S2C 580 (that push is the locktower's own).  The
        paired event IS this slice's outcome.  A settlement with no remembered
        fight for its type -- a repeat, or a start this connection never saw --
        is recorded and ignored; the socket stays alive.
        """

        pve_type: int | None = None
        if packet.fields:
            try:
                pve_type = int(packet.fields[0])
            except (TypeError, ValueError):
                pve_type = None
        if pve_type not in BATTLE_START_DUNGEON_PVE_TYPES:
            return
        profile = (
            role.compatibility.profile
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile
        )
        role_record = session_state.get("role_record")
        starts = session_state.get("dungeon_battle_start")
        pending: dict[str, Any] | None = None
        if isinstance(starts, dict):
            pending = starts.pop(pve_type, None)
            if not starts:
                session_state.pop("dungeon_battle_start", None)
        d_pack_check_flag: int | None = None
        if len(packet.fields) > 1:
            try:
                d_pack_check_flag = int(packet.fields[1])
            except (TypeError, ValueError):
                d_pack_check_flag = None
        result_code = (
            decode_game_over_result(packet.fields[13])
            if len(packet.fields) > 13
            else None
        )
        identity = (
            {}
            if pending is None
            else {
                key: pending[key]
                for key in (
                    "tower", "layer", "sn", "uncharted_id", "area", "room",
                )
                if key in pending
            }
        )
        reason: str | None = None
        if pending is None:
            reason = "no_pending_dungeon_battle_start_on_this_connection"
        elif role_record is None or role is None:
            reason = "unbound_role"
        elif pending["role_id"] != role.role_id:
            reason = "role_changed"
        if reason is not None:
            self.server.event_logger.emit(
                "tcp_dungeon_settlement_not_paired",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=(None if role is None else role.role_id),
                profile_id=profile.profile_id,
                reason=reason,
                settlement_protocol_id=packet.spec.protocol_id,
                settlement_sequence=packet.sequence,
                settlement_pve_or_pvp_type=pve_type,
                battle_result_code=result_code,
                battle_result_name=GAMEOVERRESULT_NAMES.get(result_code),
                dPackCheckFlag=d_pack_check_flag,
                pending_p1=(None if pending is None else pending["p1"]),
                pending_p2=(None if pending is None else pending["p2"]),
                pending_p3=(None if pending is None else pending["p3"]),
                pending_p4=(None if pending is None else pending["p4"]),
                s2c89_dSyncCount=(
                    None if pending is None else pending["sync_count"]
                ),
                paired=False,
                pushed=False,
                recorded=False,
                persisted=False,
                role_store_written=False,
                state_changed=False,
                connection_kept_alive=True,
                persistence_policy="no_persistence_in_this_slice",
                pairing_provenance="deterministic_recovered",
            )
            return
        assert pending is not None and role is not None
        # TASK-0299: a WON type-66 fight pays cfgArtifactDgnEvent[56].rewardId 60001 through the
        # generic gain trio (81/83/82 with dGainType 596).  The client awards nothing itself on
        # this path (pkgGameOverDisplay type-66 branch passes tbReward=nil), so this is the only
        # source -- and one (tower, layer, sn) must pay at most once, hence the meta ledger key.
        if (pve_type == BATTLE_START_GOD_FIGHT_PVE_TYPE
                and result_code == GAMEOVERRESULT_WIN
                and pending.get("tower") is not None):
            try:
                self._grant_artifact_dgn_reward(
                    connection_id, writer, session_state, role, pending)
                # TASK-0348: a cleared layer must leave the NEXT one walkable; without this the
                # tower dead-ends after one win and the player has nothing new to tap.
                self._advance_artifact_layer(
                    connection_id, writer, session_state, role, pending)
            except Exception as exc:  # never let the reward break the settlement record
                self.server.event_logger.emit(
                    "tcp_artifact_dgn_reward_failed",
                    connection_id=connection_id,
                    remote_endpoint=(
                        f"{self.client_address[0]}:{self.client_address[1]}"),
                    role_id=role.role_id,
                    error=repr(exc)[:300],
                    paired_still_recorded=True,
                )
        self.server.event_logger.emit(
            "tcp_dungeon_settlement_paired",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            profile_id=profile.profile_id,
            parent_profile_id=profile.parent_profile_id,
            pve_or_pvp_type=pending["pve_or_pvp_type"],
            settlement_pve_or_pvp_type=pve_type,
            p1=pending["p1"],
            p2=pending["p2"],
            p3=pending["p3"],
            p4=pending["p4"],
            **identity,
            request_sequence=pending["request_sequence"],
            s2c89_reply_sequence=pending["reply_sequence"],
            s2c89_dSyncCount=pending["sync_count"],
            settlement_protocol_id=packet.spec.protocol_id,
            settlement_sequence=packet.sequence,
            reply_protocol_id=response.spec.protocol_id,
            reply_sequence=response.sequence,
            # C2S 67 slot 2 carries the S2C 89 dSyncCount back
            # (pkgPveEnterData line 945 / pkgGameOverDisplay line 20).  Both
            # values are recorded and compared; the pairing itself does not
            # gate on the comparison, so a device mismatch is visible evidence
            # rather than a silent dropped packet.
            dPackCheckFlag=d_pack_check_flag,
            pack_check_matches_s2c89=(
                d_pack_check_flag is not None
                and d_pack_check_flag == pending["sync_count"]
            ),
            win_loss_decoded=result_code is not None,
            battle_result_code=result_code,
            battle_result_name=GAMEOVERRESULT_NAMES.get(result_code),
            victory=result_code == GAMEOVERRESULT_WIN,
            outcome_recorded=True,
            paired=True,
            # A 66/88 victory has no state push in this slice: S2C 580 belongs
            # to the locktower only.  The pairing is the whole outcome.
            pushed=False,
            victory_push_sent=False,
            recorded=False,
            persisted=False,
            role_store_written=False,
            state_changed=False,
            chapter_fight_recorded=False,
            reward_granted=False,
            wallet_written=False,
            card_written=False,
            historical_values_recovered=False,
            persistence_policy="no_persistence_in_this_slice",
            pairing_provenance="deterministic_recovered",
            pairing_source=(
                "pkgBattleMgr.c2sStartPveBattle -> c2s.netEnterPveBattle "
                "(pveOrPvpType at index 2, p1..p4 at indices 3..6), "
                "pkgUIArtifactDgnTeamInfo4Conf (66: tower/layer/sn), "
                "pkgUIUnchartedEventView (88: unchartedId/layer/area/room), "
                "pkgGameOverDisplay.syncRecord2Server -> C2S 67; "
                "TASK-0257/D-208, TASK-0271, TASK-0287"
            ),
        )

    def _push_babel_settlement_info(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState,
        profile: CompatibilityProfile,
        push_policy: BabelSettlementPushPolicy,
        overlay: BabelOverlay,
        pending: dict[str, Any],
        packet,
        response,
    ) -> None:
        """Push the one post-settlement S2C 580 with the overlay's own state.

        The values are the session's validated overlay values -- the same ones
        the login push sent -- so this is an honest echo of the state the
        session already shows: no floor advance, no realm change, no fail-count
        change (all three are UNKNOWN for the original server, U-3/U-5).  The
        event carries its own trigger label; it never reuses the login event's
        ``tcp_babel_info_pushed`` / ``login_position``.
        """

        # TASK-0428 / RUN-0080: a real 73 battle settles through C2S 67, and the
        # client had already been announced the PERSISTED floor by the login
        # S2C 580.  Echoing the overlay's default floor here reset the tower UI
        # to floor 1 after a device victory even though dev_play_babel_floor was
        # still 10.  The policy says this push replays the state the session
        # announced, so take the persisted floor when one exists (still no
        # advance: the original post-win rule is U-3/U-5).
        persisted_floor = dev_play_babel.read_floor(self.server.role_store)
        session_floor = (persisted_floor if persisted_floor is not None
                         else overlay.floor)
        try:
            fields = push_policy.push_fields(
                dev_play_babel.babel_info_fields_with_floor(
                    overlay.babel_info_fields(), session_floor))
        except ValueError as exc:  # pragma: no cover - validated overlay
            self._emit_error(
                connection_id,
                "babel_settlement_push_fields_invalid",
                str(exc),
                floor=overlay.floor,
                state=overlay.state,
                failCount=overlay.fail_count,
                realm=overlay.realm,
                pushed=False,
            )
            return
        push_count = int(session_state.get("babel_settlement_push_count", 0)) + 1
        response_580 = self._send_s2c(
            connection_id,
            writer,
            "returnBabelInfo",
            fields,
            decoded_fields={
                "floor": fields[0],
                "field2": fields[1],
                "state": fields[2],
                "failCount": fields[3],
                "realm": fields[4],
            },
            classification="dev_play_synthetic_babel_settlement_state",
        )
        session_state["babel_settlement_push_count"] = push_count
        self.server.event_logger.emit(
            "tcp_babel_settlement_info_pushed",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            play_profile_id=DEV_PLAY_BABEL_PROFILE_ID,
            profile_id=profile.profile_id,
            protocol_id=response_580.spec.protocol_id,
            protocol_name="returnBabelInfo",
            sequence=response_580.sequence,
            fields=list(fields),
            floor=session_floor,
            overlay_floor=overlay.floor,
            session_floor_origin=(
                "persisted_dev_play_floor" if persisted_floor is not None
                else "overlay_default"),
            state=overlay.state,
            state_name=overlay.state_name,
            failCount=overlay.fail_count,
            realm=overlay.realm,
            field2=overlay.field2,
            field2_provenance=BABEL_FIELD_2_PROVENANCE,
            field2_semantics=dev_play_babel.BABEL_FIELD_2_SEMANTICS,
            origin=DEV_PLAY_BABEL_ORIGIN,
            value_origin="dev_play_babel_overlay",
            value_source=push_policy.value_source,
            send_trigger=push_policy.send_trigger,
            login_s2c580_reused=False,
            login_position=None,
            trigger_request=push_policy.trigger_request,
            trigger_settlement_sequence=packet.sequence,
            trigger_reply_sequence=response.sequence,
            floor_before=pending["floor"],
            dFightType=pending["fight_type"],
            max_per_settlement=push_policy.max_per_settlement,
            push_count=push_count,
            floor_advanced=False,
            realm_changed=False,
            fail_count_changed=False,
            value_provenance="synthetic_compatibility",
            ordering_provenance="synthetic_compatibility",
            pairing_provenance="deterministic_recovered",
            synthetic_ordering=True,
            replaceable=True,
            historical=False,
            historical_values_recovered=False,
            persisted=False,
            reward_granted=False,
            wallet_written=False,
        )

    def _handle_chapter_map_event(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Answer one `C2S 224` and record the client's own event action.

        The six request values are `(dChapterId, dIndex, 0, optionIndex, 0,
        dSyncCount)`; `chapter_map_event.classify_request` accepts exactly the
        one recovered node and refuses everything else by name.  The reply's
        first five values are the client's own request values (positions 2 and
        3 are what `checkEventObjValid` reads as the node coordinates; 1, 4 and
        5 have no recovered consumer) and position 6 is the shipped
        `EVENT_STATUS_SUCCESS = 1`.  One request, one reply.

        A *refused* request is answered too (TASK-0243 / D-186): C2S 224 is a
        LOCK-SEND whose only unlock is S2C 398, so staying silent made the real
        client lock its UI and shut the socket down after 15 s.  The refusal
        reply echoes the request and carries a non-SUCCESS
        `EVENT_STATUS_ILLEGAL`; it stores nothing, grants nothing and never
        reports success.
        """

        role = session_state.get("role")
        profile = self.server.compatibility_profile
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            profile = role.compatibility.profile
            extra = {
                "role_id": role.role_id,
                "profile_id": profile.profile_id,
            }
        decision = chapter_map_event.classify_request(packet.fields)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        if not decision.accepted:
            refusal_fields, padded = chapter_map_event.refusal_reply_fields(
                packet.fields
            )
            # D-186: the client's own lock must be released even when the
            # request is refused.  The answer is a non-SUCCESS EVENT_STATUS_*
            # and changes no server state.
            response = self._send_s2c(
                connection_id,
                writer,
                chapter_map_event.S2C_PROTOCOL_NAME,
                refusal_fields,
                decoded_fields={
                    "request_echo_1": refusal_fields[0],
                    "map_index": refusal_fields[1],
                    "map_y": refusal_fields[2],
                    "option_index": refusal_fields[3],
                    "request_echo_5": refusal_fields[4],
                    "event_status": refusal_fields[5],
                },
                classification="deterministic_recovered_chapter_event_refusal",
                record_fields={
                    "lock_partner": packet.spec.name,
                    "lock_pair_origin": "deterministic_recovered",
                    "unlock_only_for": packet.spec.name,
                    "reply_trigger": "one_reply_per_client_request",
                    "refusal": decision.refusal,
                    **extra,
                },
            )
            self.server.event_logger.emit(
                "tcp_chapter_event_refused",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                request_fields=request_fields,
                refusal=decision.refusal,
                refusal_vocabulary=list(chapter_map_event.REFUSAL_CODES),
                response_sent=True,
                response_protocol_id=response.spec.protocol_id,
                response_protocol_name=response.spec.name,
                response_sequence=response.sequence,
                response_key_before=f"0x{response.key_before:08X}",
                response_key_after=f"0x{response.key_after:08X}",
                response_wire_length=len(response.wire),
                response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
                response_fields=list(refusal_fields),
                echo_positions=[
                    position
                    for position in range(1, 6)
                    if position not in padded
                ],
                padded_positions=padded,
                echo_provenance=(
                    "request_echo"
                    if not padded
                    else chapter_map_event.REFUSAL_REPLY_PADDING_ORIGIN
                ),
                event_status=refusal_fields[5],
                event_status_name=chapter_map_event.REFUSAL_REPLY_STATUS_NAME,
                event_status_provenance=(
                    chapter_map_event.REFUSAL_REPLY_STATUS_PROVENANCE
                ),
                event_status_origin=chapter_map_event.REFUSAL_REPLY_STATUS_ORIGIN,
                success_claimed=False,
                state_changed=False,
                reward_granted=False,
                row_recorded=False,
                connection_kept_alive=True,
                reply_count=1,
                lock_partner=list(chapter_map_event.S2C_UNLOCK_NAMES),
                reply_trigger="one_reply_per_client_request",
                historical_values_recovered=False,
                historical_ordering="unknown",
                **extra,
            )
            return
        request = decision.request
        assert request is not None
        reply_fields = chapter_map_event.success_reply_fields(request)
        response = self._send_s2c(
            connection_id,
            writer,
            chapter_map_event.S2C_PROTOCOL_NAME,
            reply_fields,
            decoded_fields={
                "request_echo_1": reply_fields[0],
                "map_index": reply_fields[1],
                "map_y": reply_fields[2],
                "option_index": reply_fields[3],
                "request_echo_5": reply_fields[4],
                "event_status": reply_fields[5],
            },
            classification="deterministic_recovered_chapter_event_reply",
            record_fields={
                "lock_partner": packet.spec.name,
                "lock_pair_origin": "deterministic_recovered",
                "unlock_only_for": packet.spec.name,
                "reply_trigger": "one_reply_per_client_request",
                **extra,
            },
        )
        self.server.event_logger.emit(
            "tcp_chapter_event_replied",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            request_protocol_id=packet.spec.protocol_id,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            request_fields=request_fields,
            response_protocol_id=response.spec.protocol_id,
            response_protocol_name=response.spec.name,
            response_sequence=response.sequence,
            response_key_before=f"0x{response.key_before:08X}",
            response_key_after=f"0x{response.key_after:08X}",
            response_wire_length=len(response.wire),
            response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
            reply_fields=list(reply_fields),
            reply_field_names=[
                "request_echo_1",
                "map_index",
                "map_y",
                "option_index",
                "request_echo_5",
                "event_status",
            ],
            field_value_origins=[
                "request_echo",
                "request_echo",
                "request_echo",
                "request_echo",
                "request_echo",
                "deterministic_recovered",
            ],
            client_consumed_positions={
                "2": "checkEventObjValid dX",
                "3": "checkEventObjValid dY",
                "6": "compared with EVENT_STATUS_SUCCESS",
            },
            client_unread_positions=[1, 4, 5],
            event_status=reply_fields[5],
            event_status_name=chapter_map_event.EVENT_STATUS_NAMES.get(
                chapter_map_event.EVENT_STATUS_SUCCESS
            ),
            event_node=chapter_map_event.CHAPTER_ONE_EVENT_NODE.canonical(),
            lock_partner=packet.spec.name,
            lock_pair_origin="deterministic_recovered",
            unlock_only_for=packet.spec.name,
            reply_trigger="one_reply_per_client_request",
            reply_count=1,
            requests_answered=(
                session_state.get("chapter_event_request_count", 0) + 1
            ),
            repeated_request=(
                session_state.get("chapter_event_request_count", 0) > 0
            ),
            historical_ordering="unknown",
            historical_values_recovered=False,
            **extra,
        )
        session_state["chapter_event_request_count"] = (
            session_state.get("chapter_event_request_count", 0) + 1
        )
        self._record_chapter_event_clear(
            connection_id,
            session_state,
            role,
            packet,
            response,
            request,
        )

    def _record_chapter_event_clear(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        role: MinimalRoleState | None,
        packet,
        response,
        request,
    ) -> None:
        """Store the one action this connection just completed, after its reply.

        Called only AFTER the S2C 398 success answer was written, so the
        request and the answer both exist on this connection before anything is
        persisted.  A profile without an item-replay policy (v1-v13) has
        nowhere to put the row, so nothing is stored and the refusal is named.
        A storage failure is a `tcp_error` and also stores nothing.
        """

        profile = (
            role.compatibility.profile
            if role is not None
            else self.server.compatibility_profile
        )
        role_record = session_state.get("role_record")
        reason: str | None = None
        if profile.chapter_item_init is None or profile.chapter_fight_replay is None:
            reason = "no_chapter_item_replay_policy"
        elif role_record is None or role is None:
            reason = "unbound_role"
        elif role_record.role_id != role.role_id:  # pragma: no cover - defensive
            reason = "role_changed"
        if reason is not None:
            self.server.event_logger.emit(
                "tcp_chapter_event_clear_not_recorded",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=(None if role is None else role.role_id),
                reason=reason,
                request_protocol_id=packet.spec.protocol_id,
                request_sequence=packet.sequence,
                reply_protocol_id=response.spec.protocol_id,
                reply_sequence=response.sequence,
                recorded=False,
                evidence_kind=CHAPTER_EVENT_EVIDENCE_CLIENT_ACTION,
                persistence_origin="synthetic_compatibility",
            )
            return
        assert role_record is not None
        try:
            outcome = self.server.role_store.record_chapter_event_clear_with_grant(
                record=role_record,
                chapter_id=request.chapter_id,
                map_index=request.map_index,
                event_cfg_id=chapter_map_event.EVENT_CFG_ID,
                option_index=request.option_index,
                observed_at=self.server.now_unix(),
                request_sequence=packet.sequence,
                reply_sequence=response.sequence,
                reward_id=chapter_map_event.REWARD_ID,
                reward_crystal=chapter_map_event.REWARD_CRYSTAL,
                crystal_bucket=chapter_map_event.REWARD_LEDGER_BUCKET,
                bucket_provenance=(
                    chapter_map_event.REWARD_LEDGER_BUCKET_PROVENANCE
                ),
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "chapter_event_clear_write_failed",
                str(exc),
                chapter_id=request.chapter_id,
                map_index=request.map_index,
                **self.server.role_store.profile_fields(),
            )
            return
        record = outcome.record
        created = outcome.created
        grant = outcome.grant
        self.server.event_logger.emit(
            "tcp_chapter_event_clear_recorded",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            account_key_short=record.display_key(),
            recorded=created,
            already_present=not created,
            evidence_kind=CHAPTER_EVENT_EVIDENCE_CLIENT_ACTION,
            request_protocol_id=packet.spec.protocol_id,
            request_sequence=packet.sequence,
            reply_protocol_id=response.spec.protocol_id,
            reply_sequence=response.sequence,
            replay_row=record.replay_row(),
            replay_row_format=CHAPTER_FIGHT_REPLAY_ROW_FORMAT,
            replay_value_origin="request_echo",
            replay_type_origin="deterministic_recovered",
            replay_row_origin="deterministic_recovered",
            reward_authority={
                "reward_id": grant.reward_id,
                "crystal": grant.crystal,
                "crystal_bucket": grant.crystal_bucket,
                "bucket_provenance": grant.bucket_provenance,
                "granted": False,
                "ledger_recorded": True,
                "ledger_created": outcome.grant_created,
                "ledger_total_after": outcome.ledger_total_after,
                "applied_to_client_balance": False,
                "client_replay": "not_implemented",
                "provenance": grant.provenance,
                "reason": (
                    "the shipped reward row carries one scalar crystal and no "
                    "bucket column, so the reward's bucket is unknown; this row "
                    "records the server-side reward authority only. No S2C 10 / "
                    "updateCrystal / gain-info is sent and the client's visible "
                    "balance is unchanged"
                ),
            },
            historical_completion_time_recovered=False,
            **record.evidence_fields(),
        )

    def _handle_market_inspect_order(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Answer one `C2S 411 inspectOrderValid` with exactly one S2C 745.

        The client asks the server to validate a shop order before it hands the
        order to the payment SDK.  C2S 411 is a LOCK-SEND whose only unlock is
        S2C 745 (`pkgScProtocolMgr.lua:966` names `inspectOrderValid` as the
        lock partner), so an unanswered request left the shop on its 「购买中」
        overlay until `LOCK_TIME = 15000` ms closed the socket (D-186, Issue
        #26).

        This stub creates no order: every request is answered with the client's
        own `e_tbDealResult.NUM_RESTRICT`, which `pkgRechargeCenterMgr
        .inspectOrderResult` handles with `pkgUIAlertMgr.alert2(
        'market_deal_result_num_restrict')` and an immediate return -- never
        reaching `pkgPayMgr.payOrder`.  No order row, no charge, no reward and
        no `role_store` write happens here, and the wait overlay destroys itself
        after its own 6000 ms delay.
        """

        role = session_state.get("role")
        profile = self.server.compatibility_profile
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            profile = role.compatibility.profile
            extra = {
                "role_id": role.role_id,
                "profile_id": profile.profile_id,
            }
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        decision = market_inspect_order.classify_request(packet.fields)
        reply_fields, padded = market_inspect_order.refusal_reply_fields(
            packet.fields
        )
        response = self._send_s2c(
            connection_id,
            writer,
            market_inspect_order.S2C_PROTOCOL_NAME,
            reply_fields,
            decoded_fields={
                "deal_result": reply_fields[0],
                "market_type": reply_fields[1],
                "recharge_id": reply_fields[2],
                "cost": reply_fields[3],
                "custom_order_id": _text(reply_fields[4]),
            },
            classification="synthetic_recovered_market_inspect_refusal",
            record_fields={
                "lock_partner": packet.spec.name,
                "lock_pair_origin": "deterministic_recovered",
                "unlock_only_for": packet.spec.name,
                "reply_trigger": "one_reply_per_client_request",
                **extra,
            },
        )
        session_state["market_inspect_order_count"] = (
            session_state.get("market_inspect_order_count", 0) + 1
        )
        self.server.event_logger.emit(
            "tcp_market_inspect_order_refused",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            request_fields=request_fields,
            request_field_names=list(market_inspect_order.C2S_FIELD_NAMES),
            refusal=decision.refusal or market_inspect_order.REFUSAL_NO_ORDER_FLOW,
            refusal_vocabulary=list(market_inspect_order.REFUSAL_CODES),
            response_sent=True,
            response_protocol_id=response.spec.protocol_id,
            response_protocol_name=response.spec.name,
            response_sequence=response.sequence,
            response_key_before=f"0x{response.key_before:08X}",
            response_key_after=f"0x{response.key_after:08X}",
            response_wire_length=len(response.wire),
            response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
            response_fields=list(reply_fields),
            response_field_names=list(market_inspect_order.S2C_FIELD_NAMES),
            echo_positions=[
                position
                for position in (2, 3, 4)
                if position not in padded
            ],
            padded_positions=padded,
            echo_provenance=(
                "request_echo"
                if not padded
                else market_inspect_order.PADDING_ORIGIN
            ),
            deal_result=reply_fields[0],
            deal_result_name=market_inspect_order.REFUSAL_DEAL_RESULT_NAME,
            deal_result_provenance=(
                market_inspect_order.REFUSAL_DEAL_RESULT_PROVENANCE
            ),
            deal_result_origin=market_inspect_order.REFUSAL_DEAL_RESULT_ORIGIN,
            client_alert_key=market_inspect_order.REFUSAL_ALERT_KEY,
            client_alert_provenance="deterministic_recovered",
            client_pay_order_reached=False,
            order_created=False,
            charged=False,
            reward_granted=False,
            state_changed=False,
            role_store_written=False,
            schema_changed=False,
            sync_counter_checked=False,
            sync_counter_updated=False,
            connection_kept_alive=True,
            lock_partner=list(market_inspect_order.S2C_UNLOCK_NAMES),
            historical_values_recovered=False,
            historical_ordering="unknown",
            **extra,
        )
        self.server.event_logger.emit(
            "tcp_market_inspect_order_replied",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            request_protocol_id=packet.spec.protocol_id,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            response_protocol_id=response.spec.protocol_id,
            response_protocol_name=response.spec.name,
            response_sequence=response.sequence,
            reply_fields=list(reply_fields),
            deal_result=reply_fields[0],
            lock_partner=packet.spec.name,
            lock_pair_origin="deterministic_recovered",
            unlock_only_for=packet.spec.name,
            reply_trigger="one_reply_per_client_request",
            reply_count=1,
            requests_answered=session_state["market_inspect_order_count"],
            repeated_request=session_state["market_inspect_order_count"] > 1,
            historical_values_recovered=False,
            **extra,
        )

    def _handle_goods_use(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Answer one C2S 429 goodsUse with EXACTLY ONE S2C 820 (TASK-0266).

        C2S 429 is a LOCK-SEND, not a notification: the client's own
        registerS2C("NNN", "goodsUse", "goodsUse") (pkgScProtocolMgr.lua:1058)
        hands the third argument to pkgLockSendMgr.regLockProto, so the client
        disabled its event system and armed LOCK_TIME = 15000 ms when it sent
        the request.  S2C 820 is the ONLY unlock, and a missing reply makes the
        client shut its own socket down (pkgLockSendMgr_unluac.lua lines
        40-46/48-53).  Therefore EVERY path below sends exactly one 820:
        success (dCode 1) or refusal (dCode 2, the client's own
        power_max_can_not_use_item branch, which toasts and refreshes nothing).

        The slice is the one cfgGoods row the TASK-0265 contract transcribes:
        item 376 花呗祈灵符, use == 1, arg1 == 376, autoAddBag == 1, whose
        cfgGoodsReward 376 row grants (252 LOTTERY_TICKET, 1).  The item count
        is goodsUse6 (S2C 27 index 157 / reward type 376) and the effect is
        lotteryTicket (index 18 / reward type 252); both are written in ONE
        role_store transaction.  Any other dType/dCfgId/dArg is refused and
        writes nothing -- the unresolved grant/arg/curNum questions U-1/U-2/U-3
        are carried on every event, never invented here.

        Success packet order, recovered from the client's own readers:
          1. S2C 24 returnOneValue_N (376, absolute item count)   -- the bag
          2. S2C 24 returnOneValue_N (252, absolute ticket count) -- the effect
          3. S2C 81 returnGainInfoStart (640 = TB_GAIN_TYPE.GOODS_USE)
          4. S2C 83 returnGainInfo (252, 0, "", 1)                -- popup row
          5. S2C 820 goodsUse (1, 1, remaining item count)        -- unlock
        The two S2C 24 rows MUST precede 5: the bag table rebuilds inside the
        GOODS_USE dispatch and reads the RoleValues, never dCurNum.
        """

        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        d_type, d_cfg_id, d_arg = (int(value) for value in packet.fields)
        role = session_state.get("role")
        role_record = session_state.get("role_record")
        account_key = session_state.get("account_key")
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        current_counts = (
            self.server.role_store.goods_use_counts(account_key=account_key)
            if account_key is not None
            else {}
        )
        current_item_count = int(
            current_counts.get(GOODS_USE_ITEM_VALUE_NAME, 0)
        )

        self.server.event_logger.emit(
            "tcp_goods_use_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            request_fields=request_fields,
            request_field_names=["dType", "dCfgId", "dArg"],
            d_type=d_type,
            d_cfg_id=d_cfg_id,
            d_arg=d_arg,
            supported_item_dtype=GOODS_USE_ITEM_DTYPE,
            item_value_name=GOODS_USE_ITEM_VALUE_NAME,
            item_value_index=GOODS_USE_ITEM_VALUE_INDEX,
            item_wire_position=GOODS_USE_ITEM_VALUE_WIRE_POSITION,
            item_current_count=current_item_count,
            reward_value_name=GOODS_USE_REWARD_VALUE_NAME,
            reward_value_index=GOODS_USE_REWARD_VALUE_INDEX,
            reward_wire_position=GOODS_USE_REWARD_VALUE_WIRE_POSITION,
            reward_type=GOODS_USE_REWARD_TYPE,
            reward_amount=GOODS_USE_REWARD_AMOUNT,
            unknown_ids=list(GOODS_USE_UNKNOWN_IDS),
            goods_use_provenance=GOODS_USE_ORIGIN_LOCAL_C2S,
            lock_partner=[packet.spec.name],
            lock_timeout_ms=GOODS_USE_LOCK_TIMEOUT_MS,
            reply_count=1,
            state_changed=False,
            state_change_planned=True,
            **extra,
        )

        refusal: str | None = None
        detail = ""
        if role is None or role_record is None or account_key is None:
            refusal = GOODS_USE_REFUSAL_UNBOUND_ROLE
            detail = "goodsUse requires a completed login on this connection"
        elif d_type != GOODS_USE_ITEM_DTYPE:
            refusal = GOODS_USE_REFUSAL_UNSUPPORTED_ITEM
            detail = (
                f"dType {d_type} is not the one cfgGoods row this slice serves "
                f"({GOODS_USE_ITEM_DTYPE})"
            )
        elif d_cfg_id != GOODS_USE_ITEM_CFG_ID:
            refusal = GOODS_USE_REFUSAL_CFG_ID
            detail = (
                f"dCfgId {d_cfg_id} is not {GOODS_USE_ITEM_CFG_ID}; a use == 1 "
                "row IS the item, so the client sends 0"
            )
        elif d_arg != GOODS_USE_ITEM_ARG:
            refusal = GOODS_USE_REFUSAL_ARG
            detail = (
                f"dArg {d_arg} is not {GOODS_USE_ITEM_ARG}; only the use == 2 "
                "path names a cfgGoods.argN slot"
            )

        result = None
        if refusal is None:
            assert role_record is not None
            try:
                result = self.server.role_store.use_goods(
                    record=role_record,
                    item_value_name=GOODS_USE_ITEM_VALUE_NAME,
                    reward_value_name=GOODS_USE_REWARD_VALUE_NAME,
                    count=1,
                    reward_amount=GOODS_USE_REWARD_AMOUNT,
                    provenance=GOODS_USE_ORIGIN_LOCAL_C2S,
                )
            except RoleStoreError as exc:
                refusal = GOODS_USE_REFUSAL_STORE
                detail = str(exc)[:240]

        if refusal is not None:
            # The refusal 820 is the lock unlock: store nothing, keep the
            # socket, and let the client's own power_max_can_not_use_item toast
            # explain it.  dCurNum is the item count the bag already renders.
            response = self._send_s2c(
                connection_id,
                writer,
                "goodsUse",
                (
                    GOODS_USE_REFUSAL_DCODE,
                    GOODS_USE_REPLY_DARG,
                    current_item_count,
                ),
                decoded_fields={
                    "dCode": GOODS_USE_REFUSAL_DCODE,
                    "dArg": GOODS_USE_REPLY_DARG,
                    "dCurNum": current_item_count,
                },
                classification="goods_use_refused",
                record_fields={
                    "goods_use_provenance": GOODS_USE_ORIGIN_LOCAL_C2S,
                    "unknown_ids": list(GOODS_USE_UNKNOWN_IDS),
                    "state_changed": False,
                    "role_store_written": False,
                    **extra,
                },
            )
            self.server.event_logger.emit(
                "tcp_goods_use_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                request_fields=request_fields,
                request_field_names=["dType", "dCfgId", "dArg"],
                d_type=d_type,
                d_cfg_id=d_cfg_id,
                d_arg=d_arg,
                reason=refusal,
                detail=detail,
                item_value_name=GOODS_USE_ITEM_VALUE_NAME,
                item_current_count=current_item_count,
                response_sent=True,
                response_protocol_id=response.spec.protocol_id,
                response_protocol_name=response.spec.name,
                response_sequence=response.sequence,
                response_wire_length=len(response.wire),
                response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
                response_fields=[
                    GOODS_USE_REFUSAL_DCODE,
                    GOODS_USE_REPLY_DARG,
                    current_item_count,
                ],
                unknown_ids=list(GOODS_USE_UNKNOWN_IDS),
                goods_use_provenance=GOODS_USE_ORIGIN_LOCAL_C2S,
                lock_partner=[packet.spec.name],
                lock_timeout_ms=GOODS_USE_LOCK_TIMEOUT_MS,
                reply_count=1,
                state_changed=False,
                role_store_written=False,
                connection_kept_alive=True,
                **extra,
            )
            return

        assert result is not None
        packet_extra = {
            "goods_use_provenance": result.provenance,
            "unknown_ids": list(GOODS_USE_UNKNOWN_IDS),
            "state_changed": True,
            **extra,
        }
        item_response = self._send_s2c(
            connection_id,
            writer,
            "returnOneValue_N",
            (GOODS_USE_ITEM_REWARD_TYPE, result.item_after),
            decoded_fields={
                "reward_type": GOODS_USE_ITEM_REWARD_TYPE,
                "amount": result.item_after,
                "value_name": GOODS_USE_ITEM_VALUE_NAME,
                "wire_index_0_based": GOODS_USE_ITEM_VALUE_INDEX,
                "absolute": True,
            },
            classification="goods_use_item_count",
            record_fields=packet_extra,
        )
        reward_response = self._send_s2c(
            connection_id,
            writer,
            "returnOneValue_N",
            (GOODS_USE_REWARD_TYPE, result.reward_after),
            decoded_fields={
                "reward_type": GOODS_USE_REWARD_TYPE,
                "amount": result.reward_after,
                "value_name": GOODS_USE_REWARD_VALUE_NAME,
                "wire_index_0_based": GOODS_USE_REWARD_VALUE_INDEX,
                "absolute": True,
            },
            classification="goods_use_reward_count",
            record_fields=packet_extra,
        )
        gain_start_response = self._send_s2c(
            connection_id,
            writer,
            "returnGainInfoStart",
            (GOODS_USE_GAIN_TYPE,),
            decoded_fields={
                "dGainType": GOODS_USE_GAIN_TYPE,
                "gain_type_name": "GOODS_USE",
            },
            classification="goods_use_gain_start",
            record_fields=packet_extra,
        )
        gain_info_response = self._send_s2c(
            connection_id,
            writer,
            "returnGainInfo",
            (
                GOODS_USE_REWARD_TYPE,
                0,
                "",
                GOODS_USE_REWARD_AMOUNT,
            ),
            decoded_fields={
                "dRewardType": GOODS_USE_REWARD_TYPE,
                "dCfgId": 0,
                "strInstanceId": "",
                "dNum": GOODS_USE_REWARD_AMOUNT,
            },
            classification="goods_use_gain_info",
            record_fields=packet_extra,
        )
        unlock_response = self._send_s2c(
            connection_id,
            writer,
            "goodsUse",
            (
                GOODS_USE_SUCCESS_DCODE,
                GOODS_USE_REPLY_DARG,
                result.item_after,
            ),
            decoded_fields={
                "dCode": GOODS_USE_SUCCESS_DCODE,
                "dArg": GOODS_USE_REPLY_DARG,
                "dCurNum": result.item_after,
                "unlock_only_for": packet.spec.name,
            },
            classification="goods_use_applied",
            record_fields=packet_extra,
        )
        self.server.event_logger.emit(
            "tcp_goods_use_applied",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            request_fields=request_fields,
            request_field_names=["dType", "dCfgId", "dArg"],
            d_type=d_type,
            d_cfg_id=d_cfg_id,
            d_arg=d_arg,
            item_value_name=result.item_value_name,
            item_value_index=GOODS_USE_ITEM_VALUE_INDEX,
            item_wire_position=GOODS_USE_ITEM_VALUE_WIRE_POSITION,
            item_reward_type=GOODS_USE_ITEM_REWARD_TYPE,
            item_before=result.item_before,
            item_after=result.item_after,
            used_count=result.used_count,
            reward_value_name=result.reward_value_name,
            reward_value_index=GOODS_USE_REWARD_VALUE_INDEX,
            reward_wire_position=GOODS_USE_REWARD_VALUE_WIRE_POSITION,
            reward_type=GOODS_USE_REWARD_TYPE,
            reward_before=result.reward_before,
            reward_after=result.reward_after,
            reward_amount=result.reward_amount,
            gain_type=GOODS_USE_GAIN_TYPE,
            packet_order=[
                "returnOneValue_N",
                "returnOneValue_N",
                "returnGainInfoStart",
                "returnGainInfo",
                "goodsUse",
            ],
            packet_protocol_ids=[
                item_response.spec.protocol_id,
                reward_response.spec.protocol_id,
                gain_start_response.spec.protocol_id,
                gain_info_response.spec.protocol_id,
                unlock_response.spec.protocol_id,
            ],
            response_sequences=[
                item_response.sequence,
                reward_response.sequence,
                gain_start_response.sequence,
                gain_info_response.sequence,
                unlock_response.sequence,
            ],
            reply_final_fields=[
                GOODS_USE_SUCCESS_DCODE,
                GOODS_USE_REPLY_DARG,
                result.item_after,
            ],
            unknown_ids=list(GOODS_USE_UNKNOWN_IDS),
            goods_use_provenance=result.provenance,
            value_origin="persisted_meta_goods_use_count",
            effect_config_source=(
                "apk_unpacked/assets/config/goods/cfgGoodsReward.max row 376"
            ),
            grant_column_unknown_id="U-1",
            reply_arg_unknown_id="U-2",
            current_count_unknown_id="U-3",
            lock_partner=[packet.spec.name],
            lock_timeout_ms=GOODS_USE_LOCK_TIMEOUT_MS,
            reply_count=1,
            one_reply_per_request=True,
            absolute_values=True,
            state_changed=True,
            role_store_written=True,
            connection_kept_alive=True,
            historical_values_recovered=False,
            **extra,
        )

    def _handle_equip_fight_data_update(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record one C2S 653 and, on an applied dev-play overlay, answer 1320 -> 1321.

        `equip_fight/pkgEquipFightPanel.show()` sends this zero-field request and then
        `c2s.artifactDgnOpenUI` on the same connection.  Its own registration has no lock
        partner, so a missing reply never shuts the socket down -- it only leaves the
        panel unusable:

        * the five fb cells are unlocked exclusively by S2C 1320's `strOpenFbIds`
          (`pkgEquipFightModel.lua:118` `e_tbOpenFbIds[fbId] ~= nil`);
        * the 1 Hz re-send of this very request stops only when S2C 1320's
          `dDataResetTime`/`dDataFbResetTime` are both greater than the client's own
          server time (`pkgEquipFightPanel._onUpdateTimer`, source lines 84-97);
        * a stage detail cannot render before S2C 1321, because `getScoreBuffIds` feeds
          `getStageRuleIds` straight into `ipairs` (`pkgEquipFightModel.lua:221-224`).

        Every value is a dev-play CHOICE (provenance `synthetic_compatibility`,
        replaceable, not historical): a session WITHOUT the explicit overlay records
        the request and sends nothing, exactly as TASK-0259 shipped it.  Nothing is
        written to `role_store`.
        """

        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        overlay = session_state.get("dev_play_artifact_overlay")
        overlay_applied = isinstance(overlay, ArtifactOverlay)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        request_count = session_state.get("equip_fight_data_update_count", 0) + 1
        session_state["equip_fight_data_update_count"] = request_count
        self.server.event_logger.emit(
            "tcp_equip_fight_data_update_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            key_before=f"0x{packet.key_before:08X}",
            key_after=f"0x{packet.key_after:08X}",
            wire_format=packet.spec.fmt,
            request_fields=request_fields,
            request_field_count=len(packet.fields),
            overlay_applied=overlay_applied,
            dev_play_artifact_flag=bool(self.server.dev_play_artifact),
            play_profile_id=self.server._owner_draw_profile_id,
            requests_answered=request_count,
            repeated_request=request_count > 1,
            reply_available_under_overlay=[
                "syncEquipFightStageToCg",
                "syncEquipFightRandomDataToCg",
            ],
            reply_ordering_origin="synthetic_compatibility",
            reply_ordering_unknown_id="U-B",
            **extra,
        )
        dev_main = bool(
            (self.server.dev_play_babel
             and self.server.dev_play_babel_profile_matches)
            or (self.server.dev_play_gacha
                and self.server.dev_play_gacha_profile_matches)
        )
        if not overlay_applied and not dev_main:
            # Record-only: the reply values are a dev-play choice, so a session without
            # the explicit overlay gets no packet at all and keeps its socket (never the
            # D-08/D-127 unsupported break).
            self.server.event_logger.emit(
                "tcp_equip_fight_data_update_recorded",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                sequence=packet.sequence,
                overlay_applied=False,
                response_sent=False,
                response_protocol_ids=[],
                lock_partner=[],
                reason="dev_play_artifact_overlay_not_applied",
                next_probe=(
                    "on the dev-artifact-main device session, confirm the equip-fight "
                    "panel keeps opening while this unanswered request repeats about "
                    "once per second (the model refresh times stay 0.0), capture one "
                    "raw C2S 653 frame, and check the client log for any Lua error"
                ),
                state_changed=False,
                role_store_written=False,
                connection_kept_alive=True,
                **extra,
            )
            return
        if overlay_applied:
            assert isinstance(overlay, ArtifactOverlay)
        now_unix = self.server.now_unix()
        try:
            stage_fields, random_fields = dev_play_artifact.equip_fight_panel_fields(
                now_unix
            )
        except DevPlayArtifactError as exc:
            self.server.event_logger.emit(
                "tcp_equip_fight_data_update_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                sequence=packet.sequence,
                reason=exc.reason,
                detail=str(exc),
                response_sent=False,
                server_time_unix=now_unix,
                connection_kept_alive=True,
                **extra,
            )
            return
        stage_response = self._send_s2c(
            connection_id,
            writer,
            "syncEquipFightStageToCg",
            stage_fields,
            decoded_fields={
                "refreshTime": stage_fields[0],
                "strEquipFightStage": stage_fields[1],
                "strScoreNoClear": stage_fields[2],
                "isDebugAllOpen": stage_fields[3],
                "strOpenFbIds": stage_fields[4],
                "dDataResetTime": stage_fields[5],
                "dDataFbResetTime": stage_fields[6],
            },
            classification="synthetic_equip_fight_panel_stage",
        )
        self.server.event_logger.emit(
            "tcp_equip_fight_stage_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=stage_response.spec.protocol_id,
            protocol_name=stage_response.spec.name,
            sequence=stage_response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=stage_response.spec.fmt,
            fields=list(stage_fields),
            strOpenFbIds=stage_fields[4],
            open_fb_ids=list(dev_play_artifact.EQUIP_FIGHT_PANEL_OPEN_FB_IDS),
            open_fb_id_separator="#",
            fb_cell_unlock_site="pkgEquipFightModel.lua:118",
            stage_table=str(stage_fields[1]),
            score_table=str(stage_fields[2]),
            debug_all_open=bool(stage_fields[3]),
            next_fresh_time=stage_fields[5],
            fb_refresh_time=stage_fields[6],
            refresh_window_seconds=(
                dev_play_artifact.EQUIP_FIGHT_PANEL_REFRESH_WINDOW_S
            ),
            refresh_semantics=(
                "pkgEquipFightPanel._onUpdateTimer re-sends C2S 653 while "
                "e_dNextFreshTime < pkgServerTimeMgr.getCurrentServerTime()"
            ),
            server_time_unix=now_unix,
            value_provenance=dev_play_artifact.EQUIP_FIGHT_PANEL_VALUE_PROVENANCE,
            value_source=dev_play_artifact.EQUIP_FIGHT_PANEL_SOURCE,
            unknown_ids=list(dev_play_artifact.EQUIP_FIGHT_PANEL_UNKNOWN_IDS),
            replaceable=True,
            historical=False,
            historical_values_recovered=False,
            request_count=request_count,
            repeated_request=request_count > 1,
            state_changed=False,
            role_store_written=False,
            connection_kept_alive=True,
            **extra,
        )
        random_response = self._send_s2c(
            connection_id,
            writer,
            "syncEquipFightRandomDataToCg",
            random_fields,
            decoded_fields={
                "refreshTime": random_fields[0],
                "strBuffIds": random_fields[1],
                "strScoreRules": random_fields[2],
            },
            classification="synthetic_equip_fight_panel_random",
        )
        self.server.event_logger.emit(
            "tcp_equip_fight_random_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=random_response.spec.protocol_id,
            protocol_name=random_response.spec.name,
            sequence=random_response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=random_response.spec.fmt,
            fields=list(random_fields),
            buff_table=str(random_fields[1]),
            score_rule_table=str(random_fields[2]),
            empty_tables_safe_because=(
                "getScoreBuffIds feeds getStageRuleIds into ipairs "
                "(pkgEquipFightModel.lua:221-224); with no open stage no stage id is "
                "rendered, and _s2tEFCommonList skips every entry without dStageId > 0"
            ),
            packet_order=["syncEquipFightStageToCg", "syncEquipFightRandomDataToCg"],
            ordering_origin="synthetic_compatibility",
            ordering_unknown_id="U-B",
            server_time_unix=now_unix,
            value_provenance=dev_play_artifact.EQUIP_FIGHT_PANEL_VALUE_PROVENANCE,
            replaceable=True,
            historical=False,
            historical_values_recovered=False,
            request_count=request_count,
            repeated_request=request_count > 1,
            state_changed=False,
            role_store_written=False,
            connection_kept_alive=True,
            **extra,
        )

    def _handle_artifact_dgn_open_ui(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record one C2S 744 and, on an applied overlay, answer 1526 -> 1527.

        The client's 神器副本 entry page (`artifactDgn/pkgUIArtifactDgnEnter`)
        draws from exactly two server packets:

        * S2C 1526 `artifactDgnSendWeekAndOpen` `NNyyyyy` -- the client formula
          week, the shop week, and `cfgArtifactDgnWeek[week].open1..5` as
          booleans.  Both week numbers are computed from the SAME server clock
          the login's S2C 0 uses (`RevivalTCPServer.now_unix`), never pinned to
          a literal week.
        * S2C 1527 `artifactDgnSendRaceAndTower` `NNNNNN` -- the zero
          first-enter state (synthetic_compatibility, replaceable, not
          historical) plus the registration-only 6th N (U-3), sent as 0 with no
          meaning.

        1526 goes first because the 1527 handler dispatches
        `pkgUIArtifactDgnEnter_update`, whose listeners read what 1526 has just
        written; the ordering itself is a synthetic choice (no server-side
        observation).

        Nothing else in this family is sent: 1528/1529/1530/1531/1537/1538
        belong to later edges, and 745/746 are unimplemented.  A session without
        the applied overlay records the request and keeps the connection.
        """

        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        overlay = session_state.get("dev_play_artifact_overlay")
        overlay_applied = isinstance(overlay, ArtifactOverlay)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        request_count = session_state.get("artifact_dgn_open_ui_count", 0) + 1
        session_state["artifact_dgn_open_ui_count"] = request_count
        # Record FIRST, before any reply: the request is evidence even when the
        # overlay is absent.  The argument itself is U-1 (`unknown_placeholder`)
        # and is never interpreted.
        self.server.event_logger.emit(
            "tcp_artifact_dgn_open_ui_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            key_before=f"0x{packet.key_before:08X}",
            key_after=f"0x{packet.key_after:08X}",
            wire_format=packet.spec.fmt,
            request_fields=request_fields,
            request_field_count=len(packet.fields),
            open_ui_argument_semantics=ARTIFACT_OPEN_UI_ARGUMENT_SEMANTICS,
            open_ui_unknown_id=ARTIFACT_OPEN_UI_UNKNOWN_ID,
            open_ui_argument_next_probe=(
                "capture one C2S 744 raw frame on the dev-artifact-main device "
                "session to learn whether the native writer pads the registered N"
            ),
            overlay_applied=overlay_applied,
            dev_play_artifact_flag=bool(self.server.dev_play_artifact),
            play_profile_id=self.server._owner_draw_profile_id,
            requests_answered=request_count,
            repeated_request=request_count > 1,
            connection_kept_alive=True,
            **extra,
        )
        if not overlay_applied:
            # TASK-0488: even without the artifact overlay, a dev main save may
            # browse 昆仑; announce every tower as open so the weekday gate does
            # not hide the equipment dungeons.
            try:
                if (
                    (self.server.dev_play_babel
                     and self.server.dev_play_babel_profile_matches)
                    or (self.server.dev_play_gacha
                        and self.server.dev_play_gacha_profile_matches)
                ):
                    self._send_s2c(
                        connection_id, writer,
                        "artifactDgnSendWeekAndOpen",
                        dev_play_artifact.week_and_open_fields(
                            self.server.now_unix()),
                        classification="synthetic_dev_artifact_week_open",
                    )
            except Exception:
                pass
            # Record-only: no artifactDgn packet leaves the server and the
            # socket stays open (never the D-08/D-127 unsupported break).
            self.server.event_logger.emit(
                "tcp_artifact_dgn_open_ui_recorded",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                sequence=packet.sequence,
                overlay_applied=False,
                response_sent=False,
                response_protocol_ids=[],
                reason="dev_play_artifact_overlay_not_applied",
                connection_kept_alive=True,
                state_changed=False,
                role_store_written=False,
                **extra,
            )
            return
        assert isinstance(overlay, ArtifactOverlay)
        now_unix = self.server.now_unix()
        try:
            week_fields = overlay.week_and_open_fields(now_unix)
        except DevPlayArtifactError as exc:
            # A server clock before the artifact epoch would produce a week
            # outside cfgArtifactDgnWeek; sending it would index a nil row in
            # the client.  Refuse by name, send nothing, keep the socket.
            self.server.event_logger.emit(
                "tcp_artifact_dgn_open_ui_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                sequence=packet.sequence,
                reason=exc.reason,
                detail=str(exc),
                response_sent=False,
                server_time_unix=now_unix,
                connection_kept_alive=True,
                **extra,
            )
            return
        week, shop_week, *open_flags = week_fields
        week_response = self._send_s2c(
            connection_id,
            writer,
            "artifactDgnSendWeekAndOpen",
            week_fields,
            decoded_fields={
                "dWeek": week,
                "dWeek4Shop": shop_week,
                "tbOpen1": open_flags[0],
                "tbOpen2": open_flags[1],
                "tbOpen3": open_flags[2],
                "tbOpen4": open_flags[3],
                "tbOpen5": open_flags[4],
            },
            classification="synthetic_artifact_dgn_week_and_open",
        )
        _week, _integral, use_sec, rest_sec = dev_play_artifact.get_cur_week(
            now_unix
        )
        _shop_week, shop_integral = dev_play_artifact.get_cur_week4_shop(
            now_unix
        )
        self.server.event_logger.emit(
            "tcp_artifact_dgn_week_and_open_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=week_response.spec.protocol_id,
            protocol_name=week_response.spec.name,
            sequence=week_response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=week_response.spec.fmt,
            fields=list(week_fields),
            dWeek=week,
            dWeek4Shop=shop_week,
            open_flags=list(open_flags),
            open_flag_width="y",
            open_flags_are_booleans=True,
            open_matrix_row=[
                int(flag) for flag in dev_play_artifact.ARTIFACT_WEEK_OPEN[week]
            ],
            server_time_unix=now_unix,
            week_formula="pkgCfgArtifactDgn.getCurWeek",
            week_loop_seconds=dev_play_artifact.ARTIFACT_WEEK_LOOP_S,
            week_epoch_offset_seconds=(
                dev_play_artifact.ARTIFACT_WEEK_EPOCH_OFFSET_S
            ),
            week_use_seconds=use_sec,
            week_rest_seconds=rest_sec,
            shop_week_formula="pkgCfgArtifactDgn.getCurWeek4Shop",
            shop_loop_seconds=dev_play_artifact.ARTIFACT_SHOP_LOOP_S,
            shop_integral_days=shop_integral,
            week_value_provenance=ARTIFACT_WEEK_VALUE_PROVENANCE,
            open_cfg_provenance=ARTIFACT_OPEN_CFG_PROVENANCE,
            open_rule_provenance=ARTIFACT_OPEN_RULE_PROVENANCE,
            week_value_source=dev_play_artifact.ARTIFACT_WEEK_OPEN_SOURCE,
            replaceable=True,
            historical=False,
            historical_values_recovered=False,
            **extra,
        )
        race_fields = overlay.race_tower_fields
        race_response = self._send_s2c(
            connection_id,
            writer,
            "artifactDgnSendRaceAndTower",
            race_fields,
            decoded_fields={
                "dCurRace": race_fields[0],
                "dCurTower": race_fields[1],
                "dTowerCount": race_fields[2],
                "dCardCount": race_fields[3],
                "dIsHard": race_fields[4],
                "field_6": race_fields[5],
            },
            classification="synthetic_artifact_dgn_initial_state",
        )
        self.server.event_logger.emit(
            "tcp_artifact_dgn_race_and_tower_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=race_response.spec.protocol_id,
            protocol_name=race_response.spec.name,
            sequence=race_response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=race_response.spec.fmt,
            fields=list(race_fields),
            race_tower_fields=list(race_fields[:5]),
            field_6=race_fields[5],
            field_6_semantics=ARTIFACT_RACE_TOWER_FIELD_6_SEMANTICS,
            field_6_provenance=ARTIFACT_RACE_TOWER_FIELD_6_PROVENANCE,
            field_6_unknown_id=ARTIFACT_RACE_TOWER_FIELD_6_UNKNOWN_ID,
            value_provenance=ARTIFACT_RACE_TOWER_VALUE_PROVENANCE,
            packet_order=["artifactDgnSendWeekAndOpen",
                          "artifactDgnSendRaceAndTower",
                          "artifactDgnAchInfo"],
            ordering_origin="synthetic_compatibility",
            dispatch_note=(
                "the S2C 1527 handler dispatches pkgUIArtifactDgnEnter_update; "
                "no server-side ordering observation exists"
            ),
            replaceable=True,
            historical=False,
            historical_values_recovered=False,
            send_trigger="artifactDgnOpenUI",
            request_count=request_count,
            repeated_request=request_count > 1,
            connection_kept_alive=True,
            state_changed=False,
            role_store_written=False,
            **extra,
        )
        # TASK-0270: the tower entry plan. 1526/1527 are already on the wire; what follows is
        # everything the tower view reads synchronously inside its own show(): the fabao
        # inventory (45/46/47), the per-layer path (1531), three node rows (1530), the
        # 18-value team (1538), and finally 1529 which opens the view. Values are
        # synthetic_compatibility and the only store write is the dev-play card grant.
        # TASK-0281: the tower entry plan is NOT sent here any more. The device run
        # (reports/run_first_artifact_device_2026-10-26.md) showed that pushing 1529 inside the
        # 744 reply made the client jump from the equip-fight panel into the tower 312 ms after
        # the panel opened, so the panel could not even be observed. The plan is now sent by
        # the team commit (C2S 745/746), which is the client's own gate before a tower opens.
        #
        # TASK-0282: S2C 1551 artifactDgnAchInfo is the ONLY writer of the entry page score
        # (pkgArtifactDgnAchievement.le_dScore) and no C2S requests it, so the server must push
        # it. Without it pkgUIArtifactDgnEnter:170 concatenates nil, which is exactly the second
        # Lua error the first device run logged.
        ach_fields = dev_play_artifact.ach_info_fields()
        ach_response = self._send_s2c(
            connection_id,
            writer,
            "artifactDgnAchInfo",
            ach_fields,
            decoded_fields={"dStartWeek": ach_fields[0], "dScore": ach_fields[1],
                            "strGetReward": ach_fields[2]},
            classification="synthetic_artifact_dgn_ach_info",
        )
        self.server.event_logger.emit(
            "tcp_artifact_dgn_ach_info_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=ach_response.spec.protocol_id,
            protocol_name=ach_response.spec.name,
            sequence=ach_response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=ach_response.spec.fmt,
            fields=list(ach_fields),
            value_provenance=dev_play_artifact.ARTIFACT_RACE_TOWER_VALUE_PROVENANCE,
            unknown_ids=["U-5"],
            replaceable=True,
            historical=False,
            state_changed=False,
            role_store_written=False,
            connection_kept_alive=True,
            **extra,
        )
        # TASK-0456: the 商店 button sits on this entry page.  Its view renders
        # every cfg shelf and indexes getShopByShelf(cfg.id) immediately, so a
        # missing S2C 1547 row aborts the loop after the first cell -- which is
        # exactly the single placeholder cell the device showed.  Push all eight
        # shelves here, before the page can be used.
        try:
            self._push_artifact_shop(connection_id, writer, session_state)
        except Exception as exc:
            self.server.event_logger.emit(
                "tcp_artifact_dgn_shop_push_failed",
                connection_id=connection_id,
                remote_endpoint=remote,
                error=type(exc).__name__,
                message=str(exc)[:200],
            )
        # TASK-0283: the path/node rows belong to the tower view's data, not to a v    iew
        # transition. The second device run proved the player can open the tower from the
        # entry page ("继续探索") BEFORE any team commit, and that window died in
        # pkgUIArtifactDgnTower.lua:219 setArrow because NEXT_NODE/le_tbPath were empty.
        # None of these packets opens a view, so sending them here does not restore the
        # 312 ms auto-jump that 1529 caused (TASK-0281).
        data_sent: list[str] = []
        # TASK-0319: remember which evtId sits on each (layer, sn) so a later C2S 747 can be
        # answered by event type instead of always starting a fight.
        node_events = session_state.setdefault("artifact_node_evt", {})
        for name, fields in dev_play_artifact.tower_data_packets(            dev_play_artifact.cleared_path(self.server.role_store,                                         session_state['role'].role_id,                                         dev_play_artifact.ARTIFACT_RACE_TOWER_DEV_PLAY_STATE[1])):
            if name == "artifactDgnSendNode" and len(fields) >= 4 and int(fields[0]) == 2:
                node_events[(int(fields[1]), int(fields[2]))] = int(fields[3])
            data_response = self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                classification="synthetic_artifact_dgn_tower_data",
            )
            data_sent.append(name)
            self.server.event_logger.emit(
                "tcp_artifact_dgn_tower_data_sent",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=data_response.spec.protocol_id,
                protocol_name=data_response.spec.name,
                sequence=data_response.sequence,
                request_protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                wire_format=data_response.spec.fmt,
                fields=list(fields),
                packet_order=list(data_sent),
                opens_a_view=False,
                value_provenance=dev_play_artifact.TOWER_TEAM_ORIGIN,
                unknown_ids=["U-F"],
                replaceable=True,
                historical=False,
                state_changed=False,
                role_store_written=False,
                connection_kept_alive=True,
                **extra,
            )
        # TASK-0285: the same frame then runs showLimit -> isFabaoOK -> getFabaoCfgID(slot 12),
        # so the fabao push (45/46/47) must be on the wire before the team preview or the
        # client asserts "err strID" in pkgFabaoDataMgr and show() dies before showMemberCount.
        for name, fields in dev_play_artifact.tower_magic_packets():
            magic_response = self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                classification="synthetic_artifact_dgn_tower_magic_preview",
            )
            self.server.event_logger.emit(
                "tcp_artifact_dgn_tower_magic_preview_sent",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=magic_response.spec.protocol_id,
                protocol_name=magic_response.spec.name,
                sequence=magic_response.sequence,
                request_protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                wire_format=magic_response.spec.fmt,
                fields=list(fields),
                opens_a_view=False,
                read_only_preview=True,
                value_provenance=dev_play_artifact.TOWER_TEAM_ORIGIN,
                unknown_ids=["U-11"],
                replaceable=True,
                historical=False,
                state_changed=False,
                role_store_written=False,
                connection_kept_alive=True,
                **extra,
            )
        # TASK-0284: showMemberCount runs right after setArrow in the same show() frame and
        # indexes the team table (pkgArtifactDgnMod.getTeamInfo -> pairs), so the pre-commit
        # "continue exploring" path throws pkgArtifactDgnMod.lua:121 unless the team row is
        # already on the wire. 1538 is read-only member data here: it fills le_tbCurTeam and
        # opens nothing, and the commit still re-sends it.
        team_csv = dev_play_artifact.tower_team_csv(
            self._artifact_team_card_ids(session_state))
        team_response = self._send_s2c(
            connection_id,
            writer,
            "artifactDgnSendTeam",
            (team_csv,),
            decoded_fields={"strTeam": team_csv},
            classification="synthetic_artifact_dgn_tower_member_preview",
        )
        self.server.event_logger.emit(
            "tcp_artifact_dgn_tower_team_preview_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=team_response.spec.protocol_id,
            protocol_name=team_response.spec.name,
            sequence=team_response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=team_response.spec.fmt,
            fields=[team_csv],
            opens_a_view=False,
            read_only_preview=True,
            value_provenance=dev_play_artifact.TOWER_TEAM_ORIGIN,
            member_count_note=("the preview echoes synthetic wind card ids, so the client's "
                               "own member count still resolves to 0/0 (U-10)"),
            unknown_ids=["U-10"],
            replaceable=True,
            historical=False,
            state_changed=False,
            role_store_written=False,
            connection_kept_alive=True,
            **extra,
        )
        return

    def _send_artifact_card_rows(self, connection_id: str, writer: WriterState,
                                 session_state: dict[str, Any], team_csv: str,
                                 extra: dict[str, Any]) -> int:
        """Send S2C 1537 artifactDgnSendCard rows for one team CSV (TASK-0295).

        le_tbCards is the ONLY table the tower's member counter reads, and 1537 is its only
        writer, so without these rows the tower shows 0/0 forever (TASK-0291 contract).
        """

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        if role is None or role_record is None:
            return 0
        rows = dev_play_artifact.card_rows_for_team(
            self.server.role_store, role_record.account_key, role.role_id,
            dev_play_artifact.tower_card_instance_ids(), team_csv)
        sent = 0
        for row in rows:
            self._send_s2c(
                connection_id, writer, "artifactDgnSendCard", row,
                decoded_fields={"dCode": row[0], "a_type": row[1], "a_strIns": row[2],
                                "a_maxHp": row[3], "a_hpRate": row[4], "strTmpCard": row[5]},
                classification="synthetic_artifact_dgn_card_row",
            )
            sent += 1
        self.server.event_logger.emit(
            "tcp_artifact_dgn_card_rows_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            protocol_name="artifactDgnSendCard",
            row_count=sent,
            team_csv=team_csv,
            value_provenance=dev_play_artifact.ARTIFACT_ENEMY_TEAM_PROVENANCE,
            unknown_ids=["U-KEY", "U-SOUL"],
            replaceable=True,
            historical=False,
            state_changed=False,
            role_store_written=False,
            **extra,
        )
        return sent

    def _artifact_team_card_ids(self, session_state: dict[str, Any]) -> tuple:
        """The card instance ids the artifact tower team echoes (TASK-0286).

        The device runs showed the tower's member count as 0/0 because the synthetic
        "wind1".."wind6" instances are not owned by the role. The dev-play archive is a copy of
        the Owner's own account, so its real instances are used when present and the synthetic
        set is only the fallback for a bare test store.
        """

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        # TASK-0388: a team the player committed (C2S 745) outranks the synthetic pick. Without
        # this the entry echo reset the roster to our own choice and the next commit echoed THAT
        # back, so the fight never used the player's team (live 22:2x).
        committed = session_state.get("artifact_team_csv")
        if not committed and role is not None:
            committed = self.server.role_store.get_meta(f"artifact_team_csv:{role.role_id}")
        if committed:
            slots = [slot for slot in str(committed).split(",")[1:11] if slot and slot != "0"]
            if slots:
                return tuple(slots[:10])
        if role is not None and role_record is not None:
            # TASK-0292: isCanSelectFabao needs getRaceCardNum(tower race) >= raceNumLimit(6),
            # so race-3 (阴) instances go first and the rest of the ten slots are filled with
            # the role's best remaining cards.
            merged: list[str] = list(dev_play_artifact.race_card_instance_ids(
                self.server.role_store, role_record.account_key, role.role_id, limit=10))
            for instance_id in dev_play_artifact.top_card_instance_ids(
                    self.server.role_store, role_record.account_key, role.role_id, limit=10):
                if instance_id not in merged:
                    merged.append(instance_id)
                if len(merged) >= 10:
                    break
            if merged:
                return tuple(merged[:10])
        return dev_play_artifact.tower_card_instance_ids()

    def _send_artifact_tower_entry(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
        *,
        extra: dict[str, Any],
        now_unix: int | float,
        sent_by: str = "team_commit",
    ) -> tuple:
        """Send the artifact tower entry plan for one applied dev-play overlay.

        Returns the packet names actually sent (empty when the overlay or the role record is
        missing). The card grant is idempotent: the instance ids are deterministic, so a
        second panel open updates the same rows instead of minting new ones.
        """
        overlay = session_state.get("dev_play_artifact_overlay")
        role_record = session_state.get("role_record")
        if not isinstance(overlay, ArtifactOverlay) or role_record is None:
            return ()
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        try:
            commissioned = dev_play_artifact.commission_tower_cards(
                self.server.role_store, role_record, now=now_unix
            )
        except RoleStoreError as exc:
            # A refused grant is not a reason to leave the tower half-open: nothing else is
            # sent and the connection is kept, with the refusal named in the event.
            self.server.event_logger.emit(
                "tcp_artifact_tower_entry_card_grant_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                request_protocol_name=packet.spec.name,
                detail=str(exc),
                response_sent=False,
                connection_kept_alive=True,
                **extra,
            )
            return ()
        # TASK-0286: real owned instances win; the synthetic wind set is only a fallback.
        card_ids = self._artifact_team_card_ids(session_state)
        # TASK-0381: the commit answer must carry the archive's progress and the team the
        # player just committed. Using the defaults here is what restarted the tower at layer 1
        # and re-seated the preset team after a roster change (live 22:0x).
        role = session_state.get("role")
        tower_id = dev_play_artifact.ARTIFACT_RACE_TOWER_DEV_PLAY_STATE[1]
        path_values = dev_play_artifact.cleared_path(
            self.server.role_store, role.role_id, tower_id) if role is not None else None
        team_csv = session_state.get("artifact_team_csv")
        if not team_csv and role is not None:
            team_csv = self.server.role_store.get_meta(f"artifact_team_csv:{role.role_id}")
        plan = dev_play_artifact.tower_entry_packets(card_ids, path_values, team_csv)
        sent: list[str] = []
        protocol_ids: list[int] = []
        # TASK-0411: replay the owned relics so the top-bar 祝福 button lists them after a
        # relogin (S2C 1533 code 1 resets, code 2 appends; pkgArtifactDgnTower reads le_tbYiwu).
        try:
            owned_relics = self._owned_relic_ids(session_state)
            if owned_relics:
                self._send_s2c(connection_id, writer, "artifactDgnYiwu", (1, 0),
                               classification="persisted_artifact_relic_snapshot")
                for relic_id in owned_relics:
                    self._send_s2c(connection_id, writer, "artifactDgnYiwu",
                                   (2, int(relic_id)),
                                   classification="persisted_artifact_relic_snapshot")
        except Exception:
            pass
        # TASK-0396: the shop shelves ride with the tower entry -- its view opens without
        # any request, so the rows must already be on the wire when 商店 is tapped.
        try:
            self._push_artifact_shop(connection_id, writer, session_state)
        except Exception as exc:
            self.server.event_logger.emit(
                "tcp_artifact_dgn_shop_push_failed",
                connection_id=connection_id,
                error=type(exc).__name__,
                message=str(exc)[:200],
            )
        for name, fields in plan:
            # TASK-0297: fill le_tbCards right before the view opens.  It is the only table the
            # tower's member counter reads (TASK-0291 contract), 1537 is its only writer, and the
            # rows must be keyed by this very plan's team CSV.  Cosmetic only: the fight gate does
            # not read it.
            if name == "artifactDgnSetRace":
                self._send_artifact_card_rows(
                    connection_id, writer, session_state,
                    (team_csv or dev_play_artifact.tower_team_csv(card_ids)), extra)
            response = self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                classification="synthetic_artifact_dgn_tower_entry",
            )
            sent.append(name)
            protocol_ids.append(response.spec.protocol_id)
        self.server.event_logger.emit(
            "tcp_artifact_tower_entry_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            sent_by=sent_by,
            packet_order=sent,
            response_protocol_ids=protocol_ids,
            card_instance_ids=list(card_ids),
            card_cfg_id=dev_play_artifact.TOWER_WIND_CARD_CFG_ID,
            card_grant_outcomes=[outcome for _iid, outcome in commissioned],
            fabao_instance_id=dev_play_artifact.TOWER_FABAO_INSTANCE_ID,
            fabao_cfg_id=dev_play_artifact.TOWER_FABAO_CFG_ID,
            team_csv=dev_play_artifact.tower_team_csv(card_ids),
            path_slot_1=dev_play_artifact.TOWER_PATH_VALUES[0],
            path_registered_width=len(dev_play_artifact.TOWER_PATH_VALUES),
            path_bound_width=19,
            node_events=[list(row) for row in dev_play_artifact.TOWER_NODE_EVENTS],
            opens_the_view_last="artifactDgnSetRace",
            value_provenance=dev_play_artifact.EQUIP_FIGHT_PANEL_VALUE_PROVENANCE,
            unknown_ids=["U-F", "U1", "U2", "U3"],
            replaceable=True,
            historical=False,
            state_changed=True,
            role_store_written=bool(commissioned),
            connection_kept_alive=True,
            **extra,
        )
        return tuple(sent)

    def _remember_artifact_team(self, connection_id, writer, packet, session_state: dict[str, Any]) -> None:
        """Persist the tower team the client just committed (TASK-0378).

        C2S 745 artifactDgnSetTeam "a" carries one CSV: a race token followed by up to ten
        card instance ids. The tower's member rows and the battle both have to follow it,"""

        if packet.spec.name != "artifactDgnSetTeam" or not packet.fields:
            return
        csv = packet.fields[0]
        if isinstance(csv, bytes):
            csv = csv.decode("utf-8", "replace")
        csv = str(csv)
        if "," not in csv:
            return
        session_state["artifact_team_csv"] = csv
        role = session_state.get("role")
        if role is not None:
            self.server.role_store.set_meta(
                f"artifact_team_csv:{role.role_id}", csv)
        # The BATTLE roster is the game team (S2C 37 reads the archive's team_presets row),
        # not the tower echo, so the same ids are written there too (TASK-0303 contract).
        role_record = session_state.get("role_record")
        ids = [slot for slot in csv.split(",")[1:11] if slot and slot != "0"]
        if role_record is not None and ids:
            try:
                dev_play_artifact.boost_team(
                    self.server.role_store, role_record.account_key, ids)
                # TASK-0383: S2C 37 returnTeamInfo is the ONLY writer of the client's
                # tbTeamCard, and a battle reads it there. Writing the archive without
                # re-sending this snapshot is why the fight used the PREVIOUS save
                # (live 22:0x: "it fights with the team I saved last time").
                _role = session_state.get("role")
                if _role is not None:
                    self._send_team_snapshot(
                        connection_id, writer, _role, session_state)
            except Exception as exc:
                self.server.event_logger.emit(
                    "tcp_artifact_dgn_team_preset_failed",
                    error=type(exc).__name__,
                    message=str(exc)[:200],
                )
        self.server.event_logger.emit(
            "tcp_artifact_dgn_team_remembered",
            connection_id=None,
            role_id=(None if role is None else role.role_id),
            team_csv=csv,
            slots=[slot for slot in csv.split(",")[1:11] if slot and slot != "0"],
            connection_kept_alive=True,
        )

    def _handle_artifact_dgn_team_race(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record C2S 745/746 and, under the dev-play overlay, answer team (1538) + 1529(0).

        TASK-0264: 745 always precedes 746, and only S2C 1529 with dCode 0 turns the team
        panel into the tower view -- any other code only raises a client alert. The team is
        therefore echoed first and the open packet goes last, so the view reads a team it has
        already received.
        """
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        overlay = session_state.get("dev_play_artifact_overlay")
        overlay_applied = isinstance(overlay, ArtifactOverlay)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        count_key = f"artifact_dgn_{packet.spec.name}_count"
        request_count = session_state.get(count_key, 0) + 1
        session_state[count_key] = request_count
        self.server.event_logger.emit(
            "tcp_artifact_dgn_team_race_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            wire_format=packet.spec.fmt,
            request_fields=request_fields,
            request_field_count=len(packet.fields),
            overlay_applied=overlay_applied,
            requests_answered=request_count,
            repeated_request=request_count > 1,
            connection_kept_alive=True,
            **extra,
        )
        if not overlay_applied:
            return
        # TASK-0281: the commit request is what opens the tower, so the full entry plan
        # (fabao push 45/46/47, path 1531, nodes 1530x3, team 1538, and 1529 LAST) is sent
        # here -- one sequence, one place, and the equip-fight panel stays on screen until the
        # player commits a team.
        sent = self._send_artifact_tower_entry(
            connection_id, writer, packet, session_state, extra=extra,
            now_unix=self.server.now_unix(),
        )

        self.server.event_logger.emit(
            "tcp_artifact_dgn_team_race_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            packet_order=list(sent),
            sent_by="team_commit",
            value_provenance=dev_play_artifact.TOWER_TEAM_ORIGIN,
            replaceable=True,
            historical=False,
            **extra,
        )

    def _handle_uncharted_enter(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record C2S 244 and answer the 秘境 entry, or refuse it and keep the socket.

        C2S 244 unchartedEnter "Ny" is a LOCK-SEND (TASK-0271): its registration pairs it with
        the S2C 458 of the same name, so an unanswered request arms LOCK_TIME=15000 and the
        client shuts its own socket. A session WITHOUT the dev-play babel overlay is therefore
        still answered -- with unchartedEnter(status=0) -- but no run identity, matrix or state
        is created, so the refusal cannot be mistaken for a cleared dungeon.
        """
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        overlay = session_state.get("dev_play_babel_overlay")
        overlay_applied = isinstance(overlay, BabelOverlay)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        uncharted_id = int(packet.fields[0]) if packet.fields else 0
        sweep = bool(packet.fields[1]) if len(packet.fields) > 1 else False
        self.server.event_logger.emit(
            "tcp_uncharted_enter_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            wire_format=packet.spec.fmt,
            request_fields=request_fields,
            uncharted_id=uncharted_id,
            sweep=sweep,
            overlay_applied=overlay_applied,
            lock_partner=["unchartedEnter"],
            connection_kept_alive=True,
            **extra,
        )
        if not overlay_applied:
            refused = self._send_s2c(
                connection_id,
                writer,
                "unchartedEnter",
                dev_play_uncharted.uncharted_enter_fields(
                    dev_play_uncharted.UNCHARTED_STATUS_REFUSED, 0),
                decoded_fields={"dStatus": dev_play_uncharted.UNCHARTED_STATUS_REFUSED,
                                "dLayer": 0},
                classification="synthetic_uncharted_entry_refused",
            )
            self.server.event_logger.emit(
                "tcp_uncharted_enter_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                protocol_id=refused.spec.protocol_id,
                protocol_name=refused.spec.name,
                sequence=refused.sequence,
                reason="dev_play_babel_overlay_not_applied",
                response_sent=True,
                state_changed=False,
                role_store_written=False,
                connection_kept_alive=True,
                **extra,
            )
            return
        sent: list[str] = []
        rows = dev_play_uncharted.uncharted_room_rows()
        for name, fields in dev_play_uncharted.uncharted_entry_packets(uncharted_id):
            self._send_s2c(connection_id, writer, name, fields,
                           classification="synthetic_uncharted_entry")
            sent.append(name)
        self.server.event_logger.emit(
            "tcp_uncharted_entry_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            packet_order=sent,
            uncharted_id=uncharted_id,
            room_rows=[list(row) for row in rows],
            room_count=len(rows),
            pve_type=dev_play_uncharted.UNCHARTED_PVE_TYPE,
            first_fight_room=dev_play_uncharted.UNCHARTED_FIRST_FIGHT_ROOM,
            value_provenance=dev_play_uncharted.PROVENANCE_SYNTHETIC,
            unknown_ids=["U-1", "U-2", "U-8"],
            replaceable=True,
            historical=False,
            state_changed=False,
            role_store_written=False,
            connection_kept_alive=True,
            **extra,
        )

    def _handle_uncharted_building_sync(
        self,
        connection_id: str,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record C2S 260 syncUnchartedBuildingInfo -- it has no S2C partner (TASK-0271)."""
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        self.server.event_logger.emit(
            "tcp_uncharted_building_sync_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            wire_format=packet.spec.fmt,
            request_field_count=len(packet.fields),
            response_sent=False,
            reason="no_s2c_partner_in_the_client_registration",
            connection_kept_alive=True,
            **extra,
        )

    def _handle_babel_fight(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Answer C2S 319 babelFight with S2C 579 and, under the overlay, advance the floor.

        C2S 319 is the skip button's request from pkgUIBabelLost (TASK-0231 babel_fight): the
        client sends (curFloor, SKIP==3, unsupplied third). The server's S2C 579 is the
        authoritative result the client switches on:

        * SKIP_SUCC (40) is the only code that also destroys pkgUIBabelLost and calls
          warEndByClient(true), so it is the correct answer to a skip under the dev-play
          overlay; the floor is then re-announced in a session-scoped S2C 580.
        * Without the overlay the answer is NOT_ENOUGH_QUAN (10) and nothing changes: the
          enum has no generic "refused" code, which is recorded as UNKNOWN U-10 instead of
          being invented. Nothing is written to role_store either way.
        """
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        overlay = session_state.get("dev_play_babel_overlay")
        overlay_applied = isinstance(overlay, BabelOverlay)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        floor = int(packet.fields[0]) if packet.fields else 0
        skip_flag = int(packet.fields[1]) if len(packet.fields) > 1 else 0
        self.server.event_logger.emit(
            "tcp_babel_fight_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            wire_format=packet.spec.fmt,
            request_fields=request_fields,
            floor=floor,
            skip_requested=skip_flag == dev_play_babel.BABEL_FIGHT_SKIP_FLAG,
            overlay_applied=overlay_applied,
            lock_partner=[],
            connection_kept_alive=True,
            **extra,
        )
        result = (dev_play_babel.BABEL_RESULT_SKIP_SUCC if overlay_applied
                  else dev_play_babel.BABEL_RESULT_NOT_ENOUGH_QUAN)
        response = self._send_s2c(
            connection_id,
            writer,
            "babelResult",
            (result,),
            decoded_fields={"dResult": result},
            classification="synthetic_babel_fight_result",
        )
        # TASK-0322: a SKIP_SUCC actually costs the currency. The count lives in the archive meta
        # table and is re-seeded into the login value vector, so the debit survives a relogin.
        zhuxianling_before = zhuxianling_after = None
        if result == dev_play_babel.BABEL_RESULT_SKIP_SUCC:
            zhuxianling_before, zhuxianling_after = dev_play_babel.spend_zhuxianling(
                self.server.role_store)
        self.server.event_logger.emit(
            "tcp_babel_result_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=response.spec.protocol_id,
            protocol_name=response.spec.name,
            sequence=response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=response.spec.fmt,
            result=result,
            result_meaning={0: "SUCC", 10: "NOT_ENOUGH_QUAN", 20: "FIGHT_FAIL",
                            30: "FIGHT_SUCC", 40: "SKIP_SUCC",
                            50: "REALM_SUCC"}.get(result, "UNKNOWN"),
            overlay_applied=overlay_applied,
            value_provenance=dev_play_babel.BABEL_RESULT_PROVENANCE,
            unknown_ids=["U-10"],
            replaceable=True,
            historical=False,
            state_changed=zhuxianling_after is not None,
            role_store_written=zhuxianling_after is not None,
            zhuxianling_before=zhuxianling_before,
            zhuxianling_after=zhuxianling_after,
            zhuxianling_cost=dev_play_babel.DEV_PLAY_BABEL_SKIP_COST,
            connection_kept_alive=True,
            **extra,
        )
        if not overlay_applied or not isinstance(overlay, BabelOverlay):
            return
        # TASK-0274: the advance is persisted in the archive's role-scoped meta table, so a
        # relogin replays THIS floor through the login S2C 580 instead of falling back to 1.
        stored_floor = dev_play_babel.read_floor(self.server.role_store)
        base_floor = max(floor, stored_floor or 0, overlay.floor)
        next_floor = dev_play_babel.write_floor(self.server.role_store, base_floor + 1)
        fields = dev_play_babel.babel_info_fields_with_floor(
            overlay.babel_info_fields(), next_floor)
        advanced = self._send_s2c(
            connection_id,
            writer,
            "returnBabelInfo",
            fields,
            decoded_fields={"floor": fields[0], "field2": fields[1], "state": fields[2],
                            "failCount": fields[3], "realm": fields[4]},
            classification="synthetic_babel_floor_advance",
        )
        self.server.event_logger.emit(
            "tcp_babel_floor_advanced",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=advanced.spec.protocol_id,
            protocol_name=advanced.spec.name,
            sequence=advanced.sequence,
            fields=list(fields),
            previous_floor=floor,
            next_floor=fields[0],
            persisted=True,
            session_scoped=False,
            persisted_meta_key=dev_play_babel.DEV_PLAY_BABEL_FLOOR_META_KEY,
            value_provenance=dev_play_babel.BABEL_RESULT_PROVENANCE,
            replaceable=True,
            historical=False,
            state_changed=False,
            role_store_written=False,
            **extra,
        )

    def _handle_artifact_dgn_enemy_team_info(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record C2S 752 and, under the dev-play overlay, answer S2C 1543 with team="default".

        C2S 752 artifactDgnEnemyTeamInfo (race, tower, layer, sn) registers with two arguments,
        so an unanswered request costs nothing but the tap -- it is NOT a lock-send. The answer's
        strTeam decides the client branch: "default" opens pkgUIArtifactDgnTeamInfo4Conf, whose
        confirm starts the battle with C2S 69 (PVETYPE_GOD_FIGHT=66) directly, skipping 747/1539.
        That branch needs cfgArtifactDgnEvent[evtId].defaultStageId > 0, which holds for the
        layer-2 nodes of towers 1/2 (TASK-0264), so it is the shortest route to a first artifact
        fight. Without the overlay nothing is sent and nothing is created.
        """
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        overlay = session_state.get("dev_play_artifact_overlay")
        overlay_applied = isinstance(overlay, ArtifactOverlay)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        race = int(packet.fields[0]) if len(packet.fields) > 0 else 0
        tower = int(packet.fields[1]) if len(packet.fields) > 1 else 0
        layer = int(packet.fields[2]) if len(packet.fields) > 2 else 0
        sn = int(packet.fields[3]) if len(packet.fields) > 3 else 0
        self.server.event_logger.emit(
            "tcp_artifact_dgn_enemy_team_info_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            wire_format=packet.spec.fmt,
            request_fields=request_fields,
            race=race,
            tower=tower,
            layer=layer,
            sn=sn,
            overlay_applied=overlay_applied,
            lock_partner=[],
            connection_kept_alive=True,
            **extra,
        )
        if not overlay_applied:
            return
        fields = dev_play_artifact.artifact_enemy_team_info_fields(layer, sn)
        response = self._send_s2c(
            connection_id,
            writer,
            "artifactDgnEnemyTeamInfo",
            fields,
            decoded_fields={"dCode": fields[0], "dLayer": fields[1], "dSn": fields[2],
                            "dCurSn": fields[3], "strTeam": fields[4], "strOppHp": fields[5]},
            classification="synthetic_artifact_dgn_enemy_team_info",
        )
        self.server.event_logger.emit(
            "tcp_artifact_dgn_enemy_team_info_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=response.spec.protocol_id,
            protocol_name=response.spec.name,
            sequence=response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=response.spec.fmt,
            fields=list(fields),
            branch="default",
            branch_consequence=("pkgUIArtifactDgnTeamInfo4Conf confirm sends C2S 69 with "
                                "PVETYPE_GOD_FIGHT=66 directly; 747/1539 are skipped"),
            value_provenance=dev_play_artifact.ARTIFACT_ENEMY_TEAM_PROVENANCE,
            unknown_ids=["U-11"],
            replaceable=True,
            historical=False,
            state_changed=False,
            role_store_written=False,
            connection_kept_alive=True,
            **extra,
        )

    def _handle_artifact_dgn_do_evt(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Record C2S 747 artifactDgnDoEvt and, under the overlay, answer S2C 1539.

        RUN9 device observation (2026-10-26): after S2C 1543 the client's team-info
        confirm button calls ``pkgArtifactDgnMgr.doFight`` -> ``c2s.artifactDgnDoEvt``
        (race, tower, layer, sn, dSync), NOT C2S 69.  The client's own
        ``s2c.artifactDgnFight(dLayer, dSn)`` handler (committed decompile
        mgr:434-437) is the only thing that starts the fight: it calls
        ``c2sStartPveBattle(PVETYPE_GOD_FIGHT=66, le_dTower, dLayer, dSn)``.
        S2C 1539 is registered ``NN``, so the reply is the client's own layer/sn
        echoed back; nothing else is invented.  C2S 747 has no lock partner
        (two-argument registration), so an unanswered one costs nothing.
        """
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        role = session_state.get("role")
        extra: dict[str, Any] = {}
        if role is not None:
            extra = {
                "role_id": role.role_id,
                "profile_id": role.compatibility.profile.profile_id,
            }
        overlay = session_state.get("dev_play_artifact_overlay")
        overlay_applied = isinstance(overlay, ArtifactOverlay)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        layer = int(packet.fields[2]) if len(packet.fields) > 2 else 0
        sn = int(packet.fields[3]) if len(packet.fields) > 3 else 0
        self.server.event_logger.emit(
            "tcp_artifact_dgn_do_evt_received",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=packet.spec.protocol_id,
            protocol_name=packet.spec.name,
            sequence=packet.sequence,
            wire_format=packet.spec.fmt,
            request_fields=request_fields,
            layer=layer,
            sn=sn,
            overlay_applied=overlay_applied,
            lock_partner=[],
            connection_kept_alive=True,
            **extra,
        )
        if not overlay_applied:
            return
        # TASK-0319: answer by EVENT TYPE. A non-battle cell (entrance, maze, finger-guess,
        # hide-and-seek, hot spring, shaman, chest, carriage) must get S2C 1531 + 1532 with
        # dIsFight=0; only a battle cell (6/10/12) may get S2C 1539, whose client handler starts
        # C2S 69. Before this branch every 747 started a fight, so tapping a chest fought a boss.
        tower_id = int(packet.fields[1]) if len(packet.fields) > 1 else 1
        evt_id = session_state.get("artifact_node_evt", {}).get((int(layer), int(sn)))
        event_type = dev_play_artifact.event_type_for(evt_id) if evt_id is not None else None
        # TASK-0352: an id we do not know is NOT a battle. Treating "unknown" as battle is what
        # turned every non-battle tile into a fight in the live session; a non-battle answer costs
        # nothing while a wrong fight breaks the player's expectation of the map.
        is_battle = event_type in dev_play_artifact.ARTIFACT_BATTLE_EVT_TYPES
        if not is_battle:
            # TASK-0361: a finished non-battle node IS a cleared node. Without recording it the
            # client's doEvtButton refuses the next tile with "无法操作" (live 02:0x, right after
            # the hiding-spot minigame on layer 4).
            cleared_role_id = session_state["role"].role_id
            path_fields = dev_play_artifact.path_after_clear(
                self.server.role_store, cleared_role_id, tower_id, layer, sn)
            dev_play_artifact.record_clear(
                self.server.role_store, cleared_role_id, tower_id, layer, sn)
            # TASK-0387b: this node's own reward must be shown BEFORE 1532, or the client
            # defers the popup to the next node tap (contract task_0384 chest_flow).
            gain_rows = dev_play_artifact.artifact_reward_rows_for(
                layer, sn, fallback=True)
            if gain_rows:
                self._grant_artifact_non_battle_reward(
                    connection_id, writer, session_state, tower_id, layer, sn, gain_rows)
            path_response = self._send_s2c(
                connection_id, writer, "artifactDgnSendPath", path_fields,
                classification="synthetic_artifact_dgn_path_advance",
            )
            doevt_fields = dev_play_artifact.do_evt_fields(tower_id, layer, sn, is_fight=0)
            doevt_response = self._send_s2c(
                connection_id, writer, "artifactDgnDoEvt", doevt_fields,
                decoded_fields={"dCode": doevt_fields[0], "dTower": doevt_fields[1],
                                "dLayer": doevt_fields[2], "dSn": doevt_fields[3],
                                "dIsFight": doevt_fields[6]},
                classification="synthetic_artifact_dgn_non_battle",
            )
            self._resend_tower_grid(connection_id, writer, session_state)
            self.server.event_logger.emit(
                "tcp_artifact_dgn_non_battle_answered",
                connection_id=connection_id,
                remote_endpoint=remote,
                request_protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                tower=tower_id, layer=layer, sn=sn,
                evt_id=evt_id, event_type=event_type,
                packet_order=[path_response.spec.name, doevt_response.spec.name],
                is_fight=False,
                value_provenance=dev_play_artifact.ARTIFACT_ENEMY_TEAM_PROVENANCE,
                unknown_ids=["U-maze", "U-order"],
                replaceable=True,
                historical=False,
                role_store_written=False,
                connection_kept_alive=True,
                **extra,
            )
            return
        response = self._send_s2c(
            connection_id,
            writer,
            "artifactDgnFight",
            (layer, sn),
            decoded_fields={"dLayer": layer, "dSn": sn},
            classification="synthetic_artifact_dgn_fight",
        )
        self.server.event_logger.emit(
            "tcp_artifact_dgn_fight_sent",
            connection_id=connection_id,
            remote_endpoint=remote,
            protocol_id=response.spec.protocol_id,
            protocol_name=response.spec.name,
            sequence=response.sequence,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            wire_format=response.spec.fmt,
            fields=[layer, sn],
            branch_consequence=(
                "s2c.artifactDgnFight -> c2sStartPveBattle(PVETYPE_GOD_FIGHT=66, "
                "le_dTower, dLayer, dSn) = C2S 69"
            ),
            value_provenance="request_echo",
            replaceable=True,
            historical=False,
            state_changed=False,
            role_store_written=False,
            connection_kept_alive=True,
            **extra,
        )

    def _handle_chapter_map_task(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Answer one `C2S 227 chapterMapTask` and record the client's action.

        The five request values are `(dChapterId, dHostIndex, dTaskId, dAction,
        dSyncCount)`; `chapter_map_task.classify_request` accepts exactly
        `TASK_RECEIVE(1)` and `TASK_COMPLETE(2)` and refuses everything else by
        name.  The reply's first four values are the client's own (its handler
        reads only positions 1, 3 and 5) and position 5 is this server's status:

        * receive on a task not yet received -> the row is written, status 2
          (`TASK_STATUS_REV_SUCCESS` -> `netTaskReceived`);
        * complete on a received, not completed task -> the row is written,
          status 1 (`TASK_STATUS_SUCCESS` -> `netTaskCompleted`);
        * a repeat of either -> nothing is written, status 6
          (`TASK_STATUS_HANDLED`);
        * complete on a task this account never received -> nothing is written,
          status 7 (`TASK_STATUS_NOT_REV`), because the client's own
          `isTaskReceived` gate would have refused it;
        * a store or role-binding failure -> nothing is written, status 3
          (complete) or 4 (receive), never a success.

        The state is written BEFORE the reply is encoded, so the status the
        client sees always describes a durable row.  The sequence the reply will
        occupy is `writer.sequence` at call time, which is exactly the number
        `encode_packet` is about to stamp on it.

        Nothing here sends `S2C 388 updateChapterTask`, validates or generates a
        sync counter (U-239-3), or grants the task's `rewardId` (U-239-1).
        """

        role = session_state.get("role")
        profile = self.server.compatibility_profile
        extra: dict[str, Any] = {}
        if role is not None:
            assert role.compatibility is not None
            profile = role.compatibility.profile
            extra = {
                "role_id": role.role_id,
                "profile_id": profile.profile_id,
            }
        request_fields = [
            _text(value) if isinstance(value, bytes) else value
            for value in packet.fields
        ]
        decision = chapter_map_task.classify_request(packet.fields)
        if not decision.answered:
            # Not even the action is recoverable: refuse by name, send nothing,
            # change nothing -- the same fail-closed path `C2S 224` takes.
            self.server.event_logger.emit(
                "tcp_chapter_task_refused",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                request_fields=request_fields,
                refusal=decision.refusal,
                refusal_vocabulary=list(chapter_map_task.REFUSAL_CODES),
                response_sent=False,
                recorded=False,
                state_changed=False,
                connection_kept_alive=True,
                lock_partner=list(chapter_map_task.S2C_UNLOCK_NAMES),
                historical_values_recovered=False,
                **extra,
            )
            return

        request = decision.request
        role_record = session_state.get("role_record")
        outcome = None
        failure_reason = ""
        failure_message = ""
        if request is not None:
            if role is None or role_record is None:
                failure_reason = "unbound_role"
            elif role_record.role_id != role.role_id:  # pragma: no cover - defensive
                failure_reason = "role_changed"
            else:
                try:
                    outcome = self.server.role_store.record_chapter_task_event(
                        record=role_record,
                        chapter_id=request.chapter_id,
                        task_id=request.task_id,
                        action=request.action,
                        sync_count=int(request.sync_count),
                        request_sequence=packet.sequence,
                        reply_sequence=writer.sequence,
                        observed_at=self.server.now_unix(),
                    )
                except (
                    RoleStoreError,
                    chapter_map_task.ChapterMapTaskError,
                ) as exc:
                    failure_reason = "state_write_failed"
                    failure_message = str(exc)
        if request is not None and not failure_reason:
            assert outcome is not None
            status = chapter_map_task.STATE_REPLY_STATUS[outcome.state]
            reply_fields = chapter_map_task.success_reply_fields(request, status)
        else:
            # The action is known but the request (or the store) is not usable:
            # answer that action's own failure status rather than stay silent
            # (D-186: a lock-send must never be dropped without an unlock).
            if request is not None:
                status = chapter_map_task.ACTION_FAILURE_STATUS[request.action]
                echo = request.echo_fields()
            else:
                assert decision.failure_status is not None
                assert decision.echo_fields is not None
                status = decision.failure_status
                echo = decision.echo_fields
            reply_fields = chapter_map_task.failure_reply_fields(echo, status)
        if failure_reason:
            self.server.event_logger.emit(
                "tcp_chapter_task_state_write_failed",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                reason=failure_reason,
                message=failure_message,
                action=(None if request is None else request.action),
                request_protocol_id=packet.spec.protocol_id,
                request_sequence=packet.sequence,
                reply_status=int(status),
                recorded=False,
                state_changed=False,
                connection_kept_alive=True,
                **extra,
            )
        elif request is None:
            self.server.event_logger.emit(
                "tcp_chapter_task_refused",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                protocol_id=packet.spec.protocol_id,
                protocol_name=packet.spec.name,
                request_sequence=packet.sequence,
                request_fields=request_fields,
                refusal=decision.refusal,
                refusal_vocabulary=list(chapter_map_task.REFUSAL_CODES),
                response_sent=True,
                reply_status=int(status),
                recorded=False,
                state_changed=False,
                connection_kept_alive=True,
                lock_partner=list(chapter_map_task.S2C_UNLOCK_NAMES),
                historical_values_recovered=False,
                **extra,
            )
        response = self._send_s2c(
            connection_id,
            writer,
            chapter_map_task.S2C_PROTOCOL_NAME,
            reply_fields,
            decoded_fields={
                "chapter_id": reply_fields[0],
                "host_index": reply_fields[1],
                "task_id": reply_fields[2],
                "action": reply_fields[3],
                "task_status": reply_fields[4],
            },
            classification="deterministic_recovered_chapter_task_reply",
            record_fields={
                "lock_partner": packet.spec.name,
                "lock_pair_origin": "deterministic_recovered",
                "unlock_only_for": packet.spec.name,
                "reply_trigger": "one_reply_per_answerable_client_request",
                **extra,
            },
        )
        if outcome is not None:
            self.server.event_logger.emit(
                "tcp_chapter_task_recorded",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                recorded=outcome.state_changed,
                state_changed=outcome.state_changed,
                row_origin=chapter_map_task.CHAPTER_TASK_ORIGIN,
                value_origin=chapter_map_task.CHAPTER_TASK_VALUE_ORIGIN,
                completed_id_rule=chapter_map_task.CHAPTER_TASK_COMPLETED_ID_RULE,
                completed_id_offset=chapter_map_task.COMPLETED_ID_OFFSET,
                row=None if outcome.row is None else outcome.row.to_payload(),
                rows_after=[row.to_payload() for row in outcome.rows_after],
                reply_status=int(status),
                reply_status_name=chapter_map_task.TASK_STATUS_NAMES.get(
                    int(status)
                ),
                reward_granted=False,
                reward_authority="UNKNOWN (U-239-1)",
                sync_counter_checked=False,
                update_chapter_task_sent=False,
                historical_values_recovered=False,
                historical_rule_recovered=False,
                **outcome.evidence_fields(),
                **extra,
            )
        self.server.event_logger.emit(
            "tcp_chapter_task_replied",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            request_protocol_id=packet.spec.protocol_id,
            request_protocol_name=packet.spec.name,
            request_sequence=packet.sequence,
            request_fields=request_fields,
            response_protocol_id=response.spec.protocol_id,
            response_protocol_name=response.spec.name,
            response_sequence=response.sequence,
            response_key_before=f"0x{response.key_before:08X}",
            response_key_after=f"0x{response.key_after:08X}",
            response_wire_length=len(response.wire),
            response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
            reply_fields=list(reply_fields),
            reply_field_names=list(chapter_map_task.FIELD_NAMES),
            field_value_origins=list(chapter_map_task.REPLY_VALUE_ORIGINS),
            client_consumed_positions=dict(
                chapter_map_task.CLIENT_CONSUMED_POSITIONS
            ),
            client_unread_positions=list(chapter_map_task.CLIENT_UNREAD_POSITIONS),
            task_status=int(status),
            task_status_name=chapter_map_task.TASK_STATUS_NAMES.get(int(status)),
            state=(
                chapter_map_task.STATE_REFUSED
                if outcome is None
                else outcome.state
            ),
            recorded=bool(outcome is not None and outcome.state_changed),
            state_changed=bool(outcome is not None and outcome.state_changed),
            refusal=decision.refusal,
            failure_reason=failure_reason,
            policy_origin="revival_local_c2s227_state_machine",
            reply_count=1,
            requests_answered=(
                session_state.get("chapter_task_request_count", 0) + 1
            ),
            repeated_request=(
                session_state.get("chapter_task_request_count", 0) > 0
            ),
            lock_partner=packet.spec.name,
            lock_pair_origin="deterministic_recovered",
            unlock_only_for=packet.spec.name,
            reply_trigger="one_reply_per_answerable_client_request",
            historical_ordering="unknown",
            historical_values_recovered=False,
            historical_rule_recovered=False,
            **extra,
        )
        session_state["chapter_task_request_count"] = (
            session_state.get("chapter_task_request_count", 0) + 1
        )

    @staticmethod
    def _wire_int(value) -> int | None:
        """An exact non-negative integer from a decoded ``p`` field, or ``None``.

        The wire type is a 64-bit IEEE-754 double, so ``1.0`` is the integer 1
        and ``1.5`` is not an id.  Booleans are refused rather than coerced.
        """

        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    def _handle_chapter_accept_box(
        self,
        connection_id: str,
        writer: WriterState,
        packet,
        session_state: dict[str, Any],
    ) -> None:
        """Answer one `C2S 222 chapterAcceptBox` with exactly one `S2C 384`.

        The three request values are `(dChapterId, dBoxIndex, dSyncCount)` from
        `pkgUIChapterMainUI.onAcceptBox`; the third is the client's own guanka
        map sync counter and is not validated here (this archive keeps no such
        counter, and inventing one would fabricate a SYNC_ERR).  The reply's
        third value is the shipped `ACCEPT_BOX_STATUS_*` the client's handler
        compares.

        The chest is answered only when all of these hold, each from its own
        authority:

        * the chest is in `chapter_rewards` (the shipped `guankaChapter` row);
        * its reward row is fully recovered and persistable (the shipped
          `guankaReward` row + `specialCard` dog-food mapping);
        * the server's own `chapter_fight_clears`/`chapter_event_clears`
          recompute the client's `getChapterProgress` at or above the chest's
          threshold (never a client-claimed percentage);
        * the `(role, chapter, box)` accept row does not exist yet.

        Success is ONE transaction (accept row + dog food), then the client's
        own gain-info block (S2C81/83/82 with gain type 503), then the S2C384
        success that unlocks the request.  A repeated request is answered
        ACCEPTED without a second grant; every refusal is a named FAIL.
        """

        role = session_state.get("role")
        role_record = session_state.get("role_record")
        profile = (
            role.compatibility.profile
            if role is not None and role.compatibility is not None
            else self.server.compatibility_profile
        )
        extra: dict[str, Any] = {"profile_id": profile.profile_id}
        if role is not None:
            extra["role_id"] = role.role_id
        raw = list(packet.fields)
        chapter_raw = raw[0] if len(raw) > 0 else None
        box_raw = raw[1] if len(raw) > 1 else None
        chapter_id = self._wire_int(chapter_raw)
        box_index = self._wire_int(box_raw)
        request_fields = [
            _text(value) if isinstance(value, bytes) else value for value in raw
        ]

        def reply(status: int, event: str, **fields: Any):
            echo_chapter = chapter_raw if chapter_id is None else chapter_id
            echo_box = box_raw if box_index is None else box_index
            response = self._send_s2c(
                connection_id,
                writer,
                "chapterAcceptBox",
                (echo_chapter, echo_box, status),
                decoded_fields={
                    "dChapterId": echo_chapter,
                    "dBoxIndex": echo_box,
                    "status": status,
                    "status_name": chapter_rewards.ACCEPT_BOX_STATUS_NAMES.get(
                        status
                    ),
                },
                classification="chapter_box_reply",
                record_fields={
                    "lock_partner": packet.spec.name,
                    "lock_pair_origin": "deterministic_recovered",
                    "unlock_only_for": packet.spec.name,
                    "reply_trigger": "one_reply_per_client_request",
                    **extra,
                },
            )
            payload: dict[str, Any] = {
                "connection_id": connection_id,
                "remote_endpoint": (
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                "request_protocol_id": packet.spec.protocol_id,
                "request_protocol_name": packet.spec.name,
                "request_sequence": packet.sequence,
                "request_fields": request_fields,
                "request_field_names": [
                    "dChapterId",
                    "dBoxIndex",
                    "dSyncCount",
                ],
                "sync_count": raw[2] if len(raw) > 2 else None,
                "response_protocol_id": response.spec.protocol_id,
                "response_protocol_name": response.spec.name,
                "response_sequence": response.sequence,
                "response_key_before": f"0x{response.key_before:08X}",
                "response_key_after": f"0x{response.key_after:08X}",
                "response_wire_length": len(response.wire),
                "response_wire_sha256": hashlib.sha256(response.wire).hexdigest(),
                "reply_fields": [echo_chapter, echo_box, status],
                "status": status,
                "status_name": chapter_rewards.ACCEPT_BOX_STATUS_NAMES.get(
                    status
                ),
                "chapter_id": chapter_id,
                "box_index": box_index,
                "reply_count": 1,
                "lock_partner": packet.spec.name,
                "lock_pair_origin": "deterministic_recovered",
                "unlock_only_for": packet.spec.name,
                "reply_trigger": "one_reply_per_client_request",
                "historical_ordering": "unknown",
                **extra,
            }
            payload.update(fields)
            self.server.event_logger.emit(event, **payload)
            return response

        def refuse(reason: str, **fields: Any):
            if reason not in chapter_rewards.BOX_REFUSAL_REASONS:
                raise AssertionError(
                    f"chapter-box refusal {reason!r} is outside the closed "
                    "vocabulary"
                )
            return reply(
                chapter_rewards.ACCEPT_BOX_STATUS_FAIL,
                "tcp_chapter_box_refused",
                refusal=reason,
                refusal_vocabulary=list(chapter_rewards.BOX_REFUSAL_REASONS),
                granted=False,
                persisted=False,
                provenance="deterministic_recovered",
                **fields,
            )

        if role is None or role_record is None:
            return refuse("unbound_role")
        if profile.chapter_box_replay is None:
            return refuse("no_chapter_box_policy")
        if chapter_id is None or box_index is None or chapter_id <= 0 or box_index < 0:
            return refuse("unusable_request_value")
        frozen = chapter_rewards.reward_for_box(chapter_id, box_index)
        if frozen is None:
            return refuse(
                chapter_rewards.box_refusal(chapter_id, box_index),
                reward_id=0,
            )
        if not frozen.grantable:
            return refuse(
                frozen.refusal,
                reward_id=frozen.reward_id,
                box_status=frozen.status,
            )
        try:
            existing = self.server.role_store.chapter_box_accept(
                account_key=role_record.account_key,
                role_id=role_record.role_id,
                chapter_id=chapter_id,
                box_index=box_index,
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id, "chapter_box_read_failed", str(exc)
            )
            return refuse("store_unavailable", reward_id=frozen.reward_id)
        if existing is not None:
            return reply(
                chapter_rewards.ACCEPT_BOX_STATUS_ACCEPTED,
                "tcp_chapter_box_already_accepted",
                refusal="",
                reward_id=existing.reward_id,
                granted=False,
                persisted=True,
                already_accepted=True,
                provenance="deterministic_recovered",
                accepted_at=existing.accepted_at,
                accepted_reply_sequence=existing.reply_sequence,
            )
        try:
            fight_clears = self.server.role_store.chapter_fight_clears(
                account_key=role_record.account_key
            )
            event_clears = self.server.role_store.chapter_event_clears(
                account_key=role_record.account_key
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id, "chapter_box_progress_read_failed", str(exc)
            )
            return refuse("store_unavailable", reward_id=frozen.reward_id)
        progress = chapter_rewards.chapter_progress(
            chapter_id, fight_clears=fight_clears, event_clears=event_clears
        )
        if progress is None:
            return refuse("progress_unavailable", reward_id=frozen.reward_id)
        threshold_fields = {
            "reward_id": frozen.reward_id,
            "box_status": frozen.status,
            "threshold_numerator": frozen.threshold_numerator,
            "threshold_denominator": frozen.threshold_denominator,
            "threshold_source": frozen.threshold_source,
            **progress.evidence(),
        }
        if not frozen.threshold_met(
            triggered=progress.triggered, total=progress.total
        ):
            return refuse("progress_insufficient", **threshold_fields)

        food = frozen.food_grant()
        gain_rows = frozen.gain_rows()
        # The S2C 384 is the LAST packet of the success block: one
        # returnGainInfoStart, one returnGainInfo per dog-food row, one
        # returnGainInfoEnd, then the unlock.  Its sequence is therefore
        # deterministic and is stored with the accept row.
        planned_reply_sequence = writer.sequence + len(gain_rows) + 2
        try:
            record, created = self.server.role_store.accept_chapter_box(
                record=role_record,
                chapter_id=chapter_id,
                box_index=box_index,
                reward_id=frozen.reward_id,
                food=food,
                reply_sequence=planned_reply_sequence,
                origin=chapter_rewards.CHAPTER_BOX_ORIGIN_CLIENT_REQUEST,
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "chapter_box_accept_write_failed",
                str(exc),
                chapter_id=chapter_id,
                box_index=box_index,
                reward_id=frozen.reward_id,
                **self.server.role_store.profile_fields(),
            )
            return refuse("store_refused", reward_id=frozen.reward_id)
        if not created:
            return reply(
                chapter_rewards.ACCEPT_BOX_STATUS_ACCEPTED,
                "tcp_chapter_box_already_accepted",
                refusal="",
                reward_id=record.reward_id,
                granted=False,
                persisted=True,
                already_accepted=True,
                provenance="deterministic_recovered",
                accepted_at=record.accepted_at,
                accepted_reply_sequence=record.reply_sequence,
            )

        self._send_s2c(
            connection_id,
            writer,
            "returnGainInfoStart",
            (chapter_rewards.GUANKA_MAP_ACCEPT_BOX_GAIN_TYPE,),
            classification="chapter_box_reward",
        )
        for gain in gain_rows:
            self._send_s2c(
                connection_id,
                writer,
                "returnGainInfo",
                gain,
                decoded_fields={
                    "reward_type": gain[0],
                    "cfg_id": gain[1],
                    "instance_id": gain[2],
                    "count": gain[3],
                },
                classification="chapter_box_reward",
            )
        self._send_s2c(
            connection_id,
            writer,
            "returnGainInfoEnd",
            (),
            classification="chapter_box_reward",
        )
        # The unlock is sent no matter what an auxiliary read does: a
        # failed inventory read must not leave the client's LOCK-SEND hanging.
        try:
            food_after = self.server.role_store.card_special_counts(
                account_key=role_record.account_key
            )
        except RoleStoreError:
            food_after = {}
        response = reply(
            chapter_rewards.ACCEPT_BOX_STATUS_SUCCESS,
            "tcp_chapter_box_accepted",
            refusal="",
            granted=True,
            persisted=True,
            already_accepted=False,
            provenance="deterministic_recovered",
            reward_source="shipped_guankaReward_max",
            reward_row=frozen.reward_row().canonical() if frozen.reward_row() else "",
            **threshold_fields,
            gain_info_rows=[list(row) for row in gain_rows],
            gain_info_type=chapter_rewards.GUANKA_MAP_ACCEPT_BOX_GAIN_TYPE,
            food_granted=food,
            food_after=food_after,
            accept_origin=chapter_rewards.CHAPTER_BOX_ORIGIN_CLIENT_REQUEST,
            accepted_at=record.accepted_at,
            accepted_reply_sequence=record.reply_sequence,
            planned_reply_sequence=planned_reply_sequence,
            historical_values_recovered=True,
            historical_completion_time_recovered=False,
            **record.evidence_fields(),
        )
        if response.sequence != planned_reply_sequence:  # pragma: no cover
            self._emit_error(
                connection_id,
                "chapter_box_reply_sequence_mismatch",
                f"planned {planned_reply_sequence}, sent {response.sequence}",
                chapter_id=chapter_id,
                box_index=box_index,
            )
        return response

    def _grant_chapter_fight_reward(
        self,
        connection_id: str,
        writer: WriterState,
        role_record,
        fight_id: int,
        result_code: int | None,
    ) -> None:
        """Grant the recovered `guankaMapFight.fightRewardId` card, once.

        The fight table's own reward row is the authority
        (`recovered_rewards.py`); this method never invents a card.  The
        instance id is fixed, so a repeated settlement updates the same row and
        never produces a second card or a second new-card animation.

        The client's own `returnCardInfo` writer is used (not `addCard`: its
        reduced record leaves the four equipment fields nil), and the handbook
        row carries `bNewCard=true`, which `pkgCardGetShowMgr` caches while the
        battle scene is current and shows when the player returns to the
        chapter/city scene.  The result code is now decoded for the card-EXP
        producer (``battle_rewards``), but this older item reward keeps its
        historical first-settlement rule; the event states that basis.
        """

        reward = reward_for_fight(fight_id)
        if reward is None:
            unknown = unknown_reward_for_fight(fight_id)
            if unknown is not None:
                # Record the shipped row verbatim and grant nothing.  A named UNKNOWN is
                # the only honest outcome until the reward type's dispatcher semantics are
                # recovered; it is visible in the event log instead of a silent drop.
                self.server.event_logger.emit(
                    "tcp_chapter_fight_reward_unknown_type",
                    connection_id=connection_id,
                    remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                    role_id=(None if role_record is None else role_record.role_id),
                    fight_id=fight_id,
                    reward_id=unknown.reward_id,
                    reward_slot=unknown.slot,
                    reward_type=unknown.reward_type,
                    item_id=unknown.item_id,
                    count=unknown.count,
                    semantics=unknown.semantics,
                    granted=False,
                    persisted=False,
                    reward_source=unknown.source,
                    historical_values_recovered=False,
                    reason="reward_type_semantics_unrecovered",
                    profile_id=self.server.compatibility_profile.profile_id,
                )
            return
        role = None
        profile_id = self.server.compatibility_profile.profile_id
        try:
            row, outcome = self.server.role_store.record_card_instance(
                record=role_record,
                instance_id=reward.instance_id,
                card_cfg_id=reward.card_id,
                level=reward.level,
                sfm_id1=reward.sfm_id1,
                sfm_id2=reward.sfm_id2,
                origin=CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD,
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "chapter_fight_reward_write_failed",
                str(exc),
                fight_id=fight_id,
                reward_id=reward.reward_id,
                card_id=reward.card_id,
                **self.server.role_store.profile_fields(),
            )
            return
        base_event = {
            "connection_id": connection_id,
            "remote_endpoint": f"{self.client_address[0]}:{self.client_address[1]}",
            "fight_id": fight_id,
            "reward_id": reward.reward_id,
            "card_id": reward.card_id,
            "card_instance_id": reward.instance_id,
            "reward_source": FIGHT_REWARD_SOURCE,
            "win_loss_decoded": result_code is not None,
            "win_loss_policy": "c2s67_slot12_randvc_decoded",
            "battle_result_code": result_code,
            "battle_result_name": GAMEOVERRESULT_NAMES.get(result_code),
            "reward_basis": "first_observed_settlement",
            "historical_values_recovered": True,
            "profile_id": profile_id,
        }
        if outcome != "created":
            self.server.event_logger.emit(
                "tcp_chapter_fight_reward_already_present", **base_event
            )
            return
        self._send_s2c(
            connection_id,
            writer,
            "returnCardInfo",
            row.return_card_info_fields(),
            decoded_fields={
                "card_instance_id": row.instance_id,
                "card_cfg_id": row.card_cfg_id,
                "card_level": row.level,
                "card_origin": row.origin,
            },
            classification="recovered_fight_reward_card",
            record_fields={
                "card_instance_id": row.instance_id,
                "card_cfg_id": row.card_cfg_id,
                "card_origin": row.origin,
                "fight_id": fight_id,
                "reward_id": reward.reward_id,
                "historical_values_recovered": True,
            },
        )
        self._send_card_dex_rows(
            connection_id,
            writer,
            None,
            (row,),
            new_template_ids={template_id_for(row.card_cfg_id)},
            snapshot_section="fight_reward",
        )
        self.server.event_logger.emit(
            "tcp_chapter_fight_reward_granted",
            card_origin=row.origin,
            card_level=row.level,
            **base_event,
        )

    def _emit_chapter_fight_special_card(
        self,
        connection_id: str,
        record: ChapterFightClearRecord,
        outcome,
        created: bool,
        special_reward,
        special_reason: str | None,
        result_code: int | None,
        profile,
    ) -> None:
        """Report the dog-food grant the settlement transaction did or did not make.

        The grant itself already happened (or was refused) inside
        ``record_chapter_fight_clear_with_money``; this method only records the
        outcome.  Nothing is sent to the client here: no battle-scene
        gain-info/update packet for ``SPECIAL_CARD`` has been field-proven, so
        ``display`` says ``not_sent_until_proven`` and the inventory replays
        through the login S2C 504/506/505 snapshot.
        """

        if special_reward is None:
            return
        cfg_id = special_reward.cfg_id
        base: dict[str, Any] = {
            "connection_id": connection_id,
            "remote_endpoint": f"{self.client_address[0]}:{self.client_address[1]}",
            "role_id": record.role_id,
            "account_key_short": record.display_key(),
            "chapter_id": record.chapter_id,
            "map_index": record.map_index,
            "fight_id": record.fight_id,
            "reward_id": special_reward.reward_id,
            "reward_slot": special_reward.slot,
            "reward_type": FIGHT_SPECIAL_CARD_REWARD_TYPE,
            "special_card_cfg_id": cfg_id,
            "count": special_reward.count,
            "star": special_reward.star,
            "reward_source": FIGHT_SPECIAL_CARD_SOURCE,
            "reward_digest": FIGHT_SPECIAL_CARD_DIGEST,
            "value_provenance": FIGHT_SPECIAL_CARD_VALUE_PROVENANCE,
            "rule_provenance": FIGHT_SPECIAL_CARD_RULE_PROVENANCE,
            "provenance": (
                f"value={FIGHT_SPECIAL_CARD_VALUE_PROVENANCE};"
                f"rule={FIGHT_SPECIAL_CARD_RULE_PROVENANCE}"
            ),
            "grant_origin": FIGHT_SPECIAL_CARD_ORIGIN,
            "idempotency_key": (
                f"chapter_fight_clear:{record.role_id}:"
                f"{record.chapter_id}:{record.fight_id}"
            ),
            "idempotency_basis": "chapter_fight_clears(chapter_id, fight_id)",
            "inventory_count_after": outcome.special_counts_after.get(cfg_id, 0),
            "display": "not_sent_until_proven",
            "display_reason": (
                "no_field_proven_special_card_gain_info_on_settlement"
            ),
            "client_replay": "login_returnSpecialCard_snapshot",
            "win_loss_decoded": result_code is not None,
            "win_loss_policy": "c2s67_slot12_randvc_decoded",
            "battle_result_code": result_code,
            "battle_result_name": GAMEOVERRESULT_NAMES.get(result_code),
            "profile_id": profile.profile_id,
        }
        if created and cfg_id in outcome.special_food_granted:
            self.server.event_logger.emit(
                "tcp_chapter_fight_special_card_granted",
                granted=True,
                persisted=True,
                state_changed=True,
                inventory_granted=dict(outcome.special_food_granted),
                **base,
            )
        elif created:
            self.server.event_logger.emit(
                "tcp_chapter_fight_special_card_not_granted",
                granted=False,
                persisted=False,
                state_changed=False,
                reason=special_reason,
                **base,
            )
        else:
            self.server.event_logger.emit(
                "tcp_chapter_fight_special_card_already_present",
                granted=False,
                persisted=True,
                state_changed=False,
                reason="chapter_fight_clear_already_recorded",
                **base,
            )

    def _grant_chapter_battle_card_exp(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState,
        packet,
        pending: dict[str, Any],
        result_code: int | None,
    ) -> None:
        """Award the shipped ``guankaMonster.cardExp`` to the battle's team.

        The client's victory view only *displays* this value; it never writes a
        card row.  This method is the producer: on a decoded chapter-map
        victory it applies the fight's flat ``cardExp`` to every non-protagonist
        card captured when C2S 69 started, persists level/EXP through the
        client's own ``calculateLevel`` math, and replays each changed row with
        S2C 34 ``updateCard``.  A loss, an undecoded slot, an unknown fight, a
        zero EXP row or an empty team is a named refusal -- never a guess and
        never a silent skip.
        """

        fight_id = int(pending["fight_id"])
        team_ids = tuple(pending.get("team_card_ids") or ())
        slot_value = packet.fields[13] if len(packet.fields) > 13 else None
        card_exp = card_exp_for_fight(fight_id)
        base_event = {
            "connection_id": connection_id,
            "remote_endpoint": f"{self.client_address[0]}:{self.client_address[1]}",
            "role_id": role.role_id,
            "fight_id": fight_id,
            "team_group_id": pending.get("team_group_id"),
            "team_card_ids": list(team_ids),
            "team_origin": pending.get("team_origin"),
            "card_exp": card_exp,
            "card_exp_source": "config/guankaMap/guankaMonster.max cardExp",
            "result_slot_value": slot_value,
            "battle_result_code": result_code,
            "battle_result_name": GAMEOVERRESULT_NAMES.get(result_code),
            "win_loss_decoded": result_code is not None,
            "win_loss_policy": "c2s67_slot12_randvc_decoded",
        }
        reason: str | None = None
        if result_code is None:
            reason = "result_code_undecoded"
        elif result_code != GAMEOVERRESULT_WIN:
            reason = "battle_not_a_victory"
        elif card_exp is None:
            reason = "fight_not_in_recovered_card_exp_table"
        elif card_exp == 0:
            reason = "fight_card_exp_is_zero"
        elif not team_ids:
            reason = "no_stored_team_cards"
        role_record = session_state.get("role_record")
        if reason is None and role_record is None:
            reason = "unbound_role"
        if reason is not None:
            self.server.event_logger.emit(
                "tcp_battle_card_exp_not_awarded",
                reason=reason,
                awarded=False,
                persisted=False,
                **base_event,
            )
            return
        assert role_record is not None and card_exp is not None
        account_key = session_state.get("account_key")
        before = {
            row.instance_id: row
            for row in self.server.role_store.card_instances(
                account_key=account_key
            )
            if row.instance_id in team_ids
        }
        try:
            updated = self.server.role_store.apply_card_battle_exp(
                record=role_record,
                instance_ids=team_ids,
                card_exp=card_exp,
                provenance="revival_local_c2s67_victory_card_exp",
            )
        except RoleStoreError as exc:
            self._emit_error(
                connection_id,
                "battle_card_exp_write_failed",
                str(exc),
                fight_id=fight_id,
                card_exp=card_exp,
                team_card_ids=list(team_ids),
                **self.server.role_store.profile_fields(),
            )
            return
        awards: list[dict[str, Any]] = []
        for row in updated:
            old = before.get(row.instance_id)
            awards.append(
                {
                    "card_instance_id": row.instance_id,
                    "card_cfg_id": row.card_cfg_id,
                    "level_before": None if old is None else old.level,
                    "level_after": row.level,
                    "exp_before": None if old is None else old.exp,
                    "exp_after": row.exp,
                    "added_exp": card_exp,
                }
            )
            self._send_s2c(
                connection_id,
                writer,
                "updateCard",
                row.return_card_info_fields(),
                decoded_fields={
                    "card_instance_id": row.instance_id,
                    "card_cfg_id": row.card_cfg_id,
                    "card_level": row.level,
                    "card_exp": row.exp,
                    "added_exp": card_exp,
                    "fight_id": fight_id,
                },
                classification="battle_card_exp_applied",
            )
        self.server.event_logger.emit(
            "tcp_battle_card_exp_applied",
            awarded=True,
            persisted=True,
            awards=awards,
            card_level_math="client_calculateLevel",
            provenance="revival_local_c2s67_victory_card_exp",
            **base_event,
        )
        # TASK-0245 / #27: a level-up changes its card's fight ratio, so the
        # battle-entry gate value is recomputed for the preset the client shows.
        role_record = session_state.get("role_record")
        group, client_ids = self._client_team_cards(
            session_state, role, role_record
        )
        levelled = {
            award["card_instance_id"]
            for award in awards
            if award["level_before"] != award["level_after"]
        }
        if levelled & set(client_ids):
            self._send_team_max_fight(
                connection_id,
                writer,
                role,
                role_record,
                session_state,
                trigger="battleCardExp",
                group_id=group,
                card_ids=client_ids,
                event_extra={
                    "fight_id": fight_id,
                    "levelled_card_ids": sorted(levelled & set(client_ids)),
                },
            )

    def _send_chapter_task_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
    ) -> None:
        """Send the chapter-task container init (v9 policy): two packets.

        `S2C 385 initChapterTaskStart ''` runs the client's OWN
        `pkgChapterTaskMgr` S2C handler of the same name, whose whole body is
        `le_tbChapterTaskInfo = {}` (lines 21-23);
        `S2C 387 initChapterTaskEnd ''` is the client's own empty end-of-block
        handler.  Without the first one the global stays `nil` (the module's own
        default, chunk line 6, and no other chunk of the shipped luaScript tree
        writes the name at all -- all 1827 are walked for it by
        `analyze_chapter_task_init.assert_whole_tree_writers`) and the client's
        scene-switch completion raises:

        `pkgSceneMgr.lua:875 doFinishSwitch` -> the callback
        `pkgChapterSceneMgr.enterChapter` closed over at 131 -> `:112
        le_finishCallBack` -> `pkgChapterObjectMgr.lua:385 initIndex2TypeId` ->
        `pkgChapterInfoMgr.lua:309 getActiveMap` -> `:262 isItemOver` ->
        `pkgChapterTaskMgr.lua:62 isTaskReceived`, indexing the nil global.

        That is RUN-0032's break, and it is *terminal* for the switch:
        `doFinishSwitch` dispatches ENTER_SCENE at 880, calls
        `pkgSyncSceneMgr.enterScene` at 881, prints `-- doFinishSwitch` at 882 and
        tears the loading UI down at 884-890 -- all after the 875 that raised.  So
        the client never finishes entering `chapter_qiandengzhen_1`, its whole
        logcat carries `-- doFinishSwitch` exactly twice (main city, world map), and
        it sits on its own loading screen for the rest of the run.

        NO `initChapterTaskInfo` row is sent.  A row asserts that a specific chapter
        task of a specific chapter is received/completed, and this archive has none
        (D-21).  A server whose profile carries no ChapterTaskInit policy sends
        nothing at all.
        """

        policy = role.compatibility.profile.chapter_task_init
        if policy is None:  # pragma: no cover - v1-v8 profiles
            return
        responses = [
            self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                decoded_fields={"payload_fields": len(fields)},
                classification="compatibility_chapter_task_init_v9",
            )
            for name, fields in policy.packets()
        ]
        sent_ids = [response.spec.protocol_id for response in responses]
        for index, response in enumerate(responses):
            self._emit_compatibility_packet(
                "tcp_chapter_task_state_sent",
                connection_id,
                response,
                role,
                parent_profile_id=role.compatibility.profile.parent_profile_id,
                policy_compatibility_version=(
                    policy.container_provenance.compatibility_version
                ),
                snapshot_index=index,
                snapshot_packet_count=policy.packet_count,
                snapshot_packet_ids=sent_ids,
                snapshot_section=response.spec.name,
                container_policy=policy.container_policy,
                container_policy_origin=policy.container_provenance.origin,
                item_rows_sent=policy.row_count,
                # The client reads the container when it finishes entering the
                # chapter, i.e. long after this login segment; the S2C283 below is
                # the packet whose dispatch starts the main-city switch.
                precedes_scene_switch_packet=True,
                scene_switch_boundary_protocol_id=283,
                historical_ordering=policy.historical_ordering,
                ordering_origin=policy.ordering_provenance.origin,
                historical_values_recovered=False,
            )

    def _send_drawcard_unlock(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
    ) -> None:
        """Send the v15 one-row chapter-task state that opens 祈灵.

        The client's own gate is `pkgSystemFuncMgr.isFunctionOpen('drawcard')`:
        `pkgChapterTaskMgr.isTaskCompleted(1001, 1) or dChapter >= 1`.  The
        permanent QA role has never passed chapter 1 (1/11 and no EXIT), so the
        only arm this server can satisfy is the completed-task key.  The row is
        `(1, 10001001)` in the recovered S2C386 `pp` format; its values are
        synthetic compatibility, not recovered progress, and the policy says so.

        It is sent immediately AFTER the v9 385/387 pair, which keeps that pair
        byte-for-byte and in order, and still long before S2C283 -- the packet
        whose dispatch starts the main-city switch, i.e. before
        `pkgMainUIMgr.updateBtn` reads the gate.  A profile built from v1-v14 has
        no such policy and therefore sends nothing here.
        """

        policy = role.compatibility.profile.drawcard_unlock
        if policy is None:  # pragma: no cover - v1-v14 profiles
            return
        for name, fields in policy.packets():
            response = self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                decoded_fields={"payload_fields": len(fields)},
                classification="compatibility_drawcard_unlock_v15",
            )
            self._emit_compatibility_packet(
                "tcp_drawcard_unlock_sent",
                connection_id,
                response,
                role,
                parent_profile_id=role.compatibility.profile.parent_profile_id,
                policy_compatibility_version=(
                    policy.row_provenance.compatibility_version
                ),
                drawcard_chapter_id=policy.chapter_id,
                drawcard_completed_task_id=policy.completed_task_id,
                drawcard_row_format=policy.row_format,
                drawcard_gate="pkgSystemFuncMgr.isFunctionOpen('drawcard')",
                container_policy=policy.container_policy,
                historical_ordering=policy.historical_ordering,
                historical_values_recovered=policy.historical_values_recovered,
                row_origin=policy.row_provenance.origin,
                replaceable=policy.replaceable,
            )

    def _send_chapter_task_rows(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
        session_state: dict[str, Any],
    ) -> None:
        """Replay the chapter tasks this role's client itself acted on.

        This is the second half of the `C2S 227` edge (TASK-0240).  The v9 pair
        sent earlier on this login (`S2C 385 initChapterTaskStart` -> `S2C 387
        initChapterTaskEnd`) has already reset `le_tbChapterTaskInfo`; each
        `S2C 386 initChapterTaskInfo(chapterId, taskId)` marks one entry true.
        The client's own `netTaskReceived`/`netTaskCompleted` never write that
        table (U-240-1), and both submit senders and the shared
        `pkgStoryTrigger.checkTaskState` gate read only the table -- so a task
        the client received in one session can gate a submit or a story only
        after its row is replayed here.

        Discipline, identical to `_chapter_box_replay_records`:

        * the rows are read by the session's own `account_key` and filtered by
          `role_id`, so another role's rows can never become packets;
        * a profile without the v9 `ChapterTaskInit` policy sends nothing;
        * an archive with no row sends **zero** packets (not an empty
          start/end pair), so the v9 385/387 pair and the v15 `(1, 10001001)`
          row keep their exact bytes;
        * a damaged payload, a missing account key or a read failure is one
          named `tcp_chapter_task_replay_refused` and no half block.

        One received row is one packet; a completed row is two (its own id,
        then `GetTaskCompletedId(taskId)`), received first.  This function sends
        no `S2C 385`/`S2C 387` and never resets the client's container.
        """

        policy = role.compatibility.profile.chapter_task_init
        if policy is None:  # pragma: no cover - v1-v8 profiles
            return
        account_key = session_state.get("account_key")
        if account_key is None:  # pragma: no cover - the login path sets it
            self.server.event_logger.emit(
                "tcp_chapter_task_replay_refused",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=role.role_id,
                reason="account_key_absent",
                rows_sent=0,
                state_changed=False,
                replaceable=chapter_map_task.CHAPTER_TASK_REPLACEABLE,
                historical_values_recovered=False,
            )
            return
        try:
            rows = self.server.role_store.chapter_task_rows(
                account_key=account_key
            )
        except RoleStoreError as exc:
            self.server.event_logger.emit(
                "tcp_chapter_task_replay_refused",
                connection_id=connection_id,
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                role_id=role.role_id,
                reason="rows_unreadable",
                message=str(exc),
                rows_sent=0,
                state_changed=False,
                replaceable=chapter_map_task.CHAPTER_TASK_REPLACEABLE,
                historical_values_recovered=False,
            )
            return
        mine = tuple(row for row in rows if row.role_id == role.role_id)
        if not mine:
            return
        packets: list[tuple[int, int]] = []
        for row in sorted(mine, key=chapter_map_task.ChapterTaskRow.sort_key):
            packets.extend(row.replay_rows())
        responses = [
            self._send_s2c(
                connection_id,
                writer,
                "initChapterTaskInfo",
                (chapter_id, task_id),
                decoded_fields={
                    "dChapterId": chapter_id,
                    "dTaskId": task_id,
                },
                classification="persisted_chapter_task_rows",
            )
            for chapter_id, task_id in packets
        ]
        self._emit_compatibility_packet(
            "tcp_chapter_task_rows_sent",
            connection_id,
            responses[0],
            role,
            parent_profile_id=role.compatibility.profile.parent_profile_id,
            policy_compatibility_version=(
                policy.container_provenance.compatibility_version
            ),
            container_policy=policy.container_policy,
            snapshot_packet_count=len(responses),
            snapshot_packet_ids=[packet.spec.protocol_id for packet in responses],
            rows_sent=len(mine),
            replay_rows=[list(pair) for pair in packets],
            row_format="pp",
            row_origin=chapter_map_task.CHAPTER_TASK_ORIGIN,
            value_origin=chapter_map_task.CHAPTER_TASK_VALUE_ORIGIN,
            completed_id_rule="GetTaskCompletedId(+10000000)",
            completed_id_rule_origin=(
                chapter_map_task.CHAPTER_TASK_COMPLETED_ID_RULE
            ),
            login_position="after_drawcard_unlock_before_chapter_item",
            container_initialised_by="initChapterTaskStart(385)@v9",
            resets_client_container=False,
            historical_ordering="unknown",
            ordering_origin="synthetic_compatibility",
            replaceable=chapter_map_task.CHAPTER_TASK_REPLACEABLE,
            historical_values_recovered=False,
            state_changed=False,
        )

    def _send_chapter_machine_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
    ) -> None:
        """Send the chapter-machine container init (v10 policy): two packets.

        `S2C 400 initChapterMachineStart ''` runs the handler
        `chapter/pkgChapterMachineMgr` registers for ITSELF under that name, whose
        whole body is `le_tbChapterMachineInfo = {}` (lines 18-20);
        `S2C 402 initChapterMachineEnd ''` is the client's own empty end-of-block
        handler.  Without the first one the global stays `nil` (the module's own
        default, chunk lines 6-7, and no other chunk of the shipped luaScript tree
        mentions the name at all -- all 1827 are walked for it by
        `analyze_chapter_machine_init.assert_whole_tree_writers`) and the client's
        scene-switch completion raises ONE STATEMENT LATER than the line v9 repaired:

        `pkgSceneMgr.lua:875 doFinishSwitch` -> the callback
        `pkgChapterSceneMgr.enterChapter` closed over at 131 -> `:114
        le_finishCallBack` (RUN-0032 raised at `:112`) -> `pkgChapterObjectMgr.lua:287
        initScene` -> `pkgChapterMachineMgr.lua:312 initAllMachineObj` -> `:46
        isMachineCompleted`, indexing the nil global.

        That is RUN-0033's break, and it is *terminal* for the switch exactly as the
        v8 and v9 breaks were: `doFinishSwitch` dispatches ENTER_SCENE at 880, calls
        `pkgSyncSceneMgr.enterScene` at 881, prints `-- doFinishSwitch` at 882 and
        tears the loading UI down at 884-890 -- all after the 875 that raised.  So
        the client never finishes entering `chapter_qiandengzhen_1`, its whole logcat
        carries `-- doFinishSwitch` exactly twice (main city, world map), and it sits
        on its own loading screen for the rest of the run.

        NO `initChapterMachineInfo` row is sent.  A row asserts that a named machine
        of a named chapter is completed, and this archive has none (D-21).  A server
        whose profile carries no ChapterMachineInit policy sends nothing at all.
        """

        policy = role.compatibility.profile.chapter_machine_init
        if policy is None:  # pragma: no cover - v1-v9 profiles
            return
        responses = [
            self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                decoded_fields={"payload_fields": len(fields)},
                classification="compatibility_chapter_machine_init_v10",
            )
            for name, fields in policy.packets()
        ]
        sent_ids = [response.spec.protocol_id for response in responses]
        for index, response in enumerate(responses):
            self._emit_compatibility_packet(
                "tcp_chapter_machine_state_sent",
                connection_id,
                response,
                role,
                parent_profile_id=role.compatibility.profile.parent_profile_id,
                policy_compatibility_version=(
                    policy.container_provenance.compatibility_version
                ),
                snapshot_index=index,
                snapshot_packet_count=policy.packet_count,
                snapshot_packet_ids=sent_ids,
                snapshot_section=response.spec.name,
                container_policy=policy.container_policy,
                container_policy_origin=policy.container_provenance.origin,
                machine_rows_sent=policy.row_count,
                # The client reads the container when it finishes entering the
                # chapter, i.e. long after this login segment; the S2C283 below is
                # the packet whose dispatch starts the main-city switch.
                precedes_scene_switch_packet=True,
                scene_switch_boundary_protocol_id=283,
                historical_ordering=policy.historical_ordering,
                ordering_origin=policy.ordering_provenance.origin,
                historical_values_recovered=False,
            )

    def _send_experience_task_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
    ) -> None:
        """Send the experience-task container init (v8 policy): two packets.

        `S2C 1307 returnTaskStart ''` runs the client's OWN
        `pkgExperienceMgr` S2C handler of the same name, whose whole body is
        `le_tbTask = {}; le_tbTaskByChapterId = {}; le_dTaskCount = 0`;
        `S2C 1309 returnTaskEnd ''` is the client's own empty end-of-block handler.
        Without the first one the global stays `nil` (the module's own default,
        chunk line 17) and the world map's countdown tick raises on it:

        `pkgExperienceMainUIMgr.lua:62 onCountdown` (1,000 ms repeating timer) ->
        `:73 pkgExperienceMgr.CloseActivityByClient()` -> `pkgExperienceMgr.lua:178
        pkgEventMgr.dispatch(EXPERIENCE_STATUS_CHANGE)` -> the registered listener
        `experienceStatusChange`, whose FIRST statement (`:199`) switches
        `Panel/btnDetail` off and whose `:214` then walks every chapter into
        `changeChapterBtnExperienceStatus` -> `:643 GetTaskListByChapterId` ->
        `pkgExperienceMgr.lua:203 le_tbTaskByChapterId[dChapterId]` (a nil value).

        The raise happens inside the dispatch, so `onCountdown`'s own cleanup --
        `if le_dTimer then pkgTimerMgr.delete(le_dTimer); le_dTimer = nil end`
        (lines 74-76) -- never runs and the chapter detail panel is switched off
        once per second for the rest of the session.  That is RUN-0031's break: the
        runner's confirm tap was delivered at the right pixel (logcat 05:22:28.225,
        `ACTION_DOWN x[0]=1310.0 y[0]=645.0`, inside the recovered 确认 rectangle)
        ~8 s after the panel opened, by which time the client itself had already
        hidden it.

        NO `returnTaskInfo` row is sent.  A row asserts one experience task of one
        chapter with its own id/state/progress, and this archive has none; with the
        container empty the tick's `isActivityValid()` test short-circuits and the
        timer deletes itself.  A server whose profile carries no ExperienceTaskInit
        policy sends nothing at all.
        """

        policy = role.compatibility.profile.experience_task_init
        if policy is None:  # pragma: no cover - v1-v7 profiles
            return
        responses = [
            self._send_s2c(
                connection_id,
                writer,
                name,
                fields,
                decoded_fields={"payload_fields": len(fields)},
                classification="compatibility_experience_task_init_v8",
            )
            for name, fields in policy.packets()
        ]
        sent_ids = [response.spec.protocol_id for response in responses]
        for index, response in enumerate(responses):
            self._emit_compatibility_packet(
                "tcp_experience_task_state_sent",
                connection_id,
                response,
                role,
                parent_profile_id=role.compatibility.profile.parent_profile_id,
                policy_compatibility_version=(
                    policy.container_provenance.compatibility_version
                ),
                snapshot_index=index,
                snapshot_packet_count=policy.packet_count,
                snapshot_packet_ids=sent_ids,
                snapshot_section=response.spec.name,
                container_policy=policy.container_policy,
                container_policy_origin=policy.container_provenance.origin,
                task_rows_sent=policy.row_count,
                # The container's first read is the world map's own countdown tick,
                # which cannot run before the chapter UI exists -- long after this
                # login segment; the S2C283 below is the packet whose dispatch
                # starts the main-city switch.
                precedes_scene_switch_packet=True,
                scene_switch_boundary_protocol_id=283,
                historical_ordering=policy.historical_ordering,
                ordering_origin=policy.ordering_provenance.origin,
                historical_values_recovered=False,
            )

    def _send_story_completion_rows(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
        session_state: dict[str, Any],
    ) -> None:
        """Replay the archive's observed completions, after S2C411 and before S2C283.

        Only rows this build can vouch for are sent, one `S2C 412 returnStory`
        each, carrying the story id, the client's own ``STATE_FINISH`` and the
        time the completion was *observed* -- never a reconstructed historical
        completion time.  A row that is refused is reported as its own event
        instead of being sent or silently dropped.  With no stored row nothing
        is sent at all, so an archive that has never completed a story produces
        exactly the login stream it produced before.
        """

        account_key = session_state.get("account_key")
        if account_key is None:  # pragma: no cover - the login path always sets it
            return
        for row in self.server.role_store.story_completions(
            account_key=account_key
        ):
            refusal = self._story_replay_refusal(row, role)
            if refusal is not None:
                self.server.event_logger.emit(
                    "tcp_story_completion_not_replayed",
                    connection_id=connection_id,
                    remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                    role_id=role.role_id,
                    account_key_short=row.display_key(),
                    reason=refusal,
                    replayed=False,
                    story_id=row.story_id,
                    **row.evidence_fields(),
                )
                continue
            response = self._send_s2c(
                connection_id,
                writer,
                "returnStory",
                (row.story_id, row.state, row.observed_at),
                decoded_fields={
                    "dStoryId": row.story_id,
                    "dState": row.state,
                    "dTime": row.observed_at,
                },
                classification="observed_story_completion_row",
            )
            self._emit_compatibility_packet(
                "tcp_story_completion_replayed",
                connection_id,
                response,
                role,
                completion_time_policy="observed_evidence_time_not_historical",
                historical_completion_time_recovered=False,
                replayed=True,
                story_id=row.story_id,
                **row.evidence_fields(),
            )
        if self.server.dev_play_gacha and self.server.dev_play_gacha_profile_matches:
            # TASK-0443: dev sessions skip the chapter story triggers; the rows
            # go in the same pre-S2C283 window as the observed completions.
            self._send_story_skip(
                connection_id, writer, role, session_state
            )
        # TASK-0469: replay the ids the client itself reported (story_done),
        # after the observed rows and without a second S2C411 (which would wipe
        # them).  Without this the city-guide story 10001 was never marked.
        self._replay_story_state(
            connection_id, writer, role, session_state, send_start=False
        )
        if (
            self.server.dev_play_babel
            or self.server.dev_play_artifact
            or self.server.dev_play_resources
            or self.server.dev_play_gacha
        ):
            self._send_guide_done_snapshot(connection_id, writer)

    def _send_prison_fight_snapshot(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
    ) -> None:
        """Send the v13 login snapshot of the prison-fight boss state (S2C 1370).

        `pkgS2CPrisonFightMgr.s2c.synPrisonFightBossToCg` (source lines 12..30,
        params=12 = socket + 11 values) is the ONLY writer of `e_tbBossData` in
        the whole shipped luaScript tree, and the client's model defaults to the
        module's empty table with no field-level fallback.  `checkActOn` line 21
        reads `startTime`/`endTime` unconditionally on EVERY PANEL_CLOSE, so a
        session that has never received this packet raises there; RUN-0054's
        alert confirm is the device traceback that proves the error unwinds
        before the chapter's own return-to-main-city callback
        (`pkgUIAlertMgr` destroys the alert first, then calls the callback).

        The packet is sent once per completed login, after the S2C411/412 story
        state and immediately before S2C283 returnSceneInfo -- the latest wire
        position that provably precedes the main-city switch and therefore the
        first panel close.  The 11 values are the closed-state constants the
        profile's `PrisonFightBossSnapshot` policy declares; this handler only
        assembles them.  A server whose profile carries no such policy (v1-v12)
        sends nothing at all and its stream is byte-for-byte what it was.
        """

        policy = role.compatibility.profile.prison_fight
        if policy is None:  # pragma: no cover - v1-v12 profiles
            return
        fields = policy.snapshot_fields()
        response = self._send_s2c(
            connection_id,
            writer,
            "synPrisonFightBossToCg",
            fields,
            decoded_fields={
                name: _text(value) if isinstance(value, bytes) else value
                for name, value in zip(policy.field_names(), fields)
            },
            classification="compatibility_prison_fight_snapshot_v13",
        )
        self._emit_compatibility_packet(
            "tcp_prison_fight_snapshot_sent",
            connection_id,
            response,
            role,
            parent_profile_id=role.compatibility.profile.parent_profile_id,
            policy_compatibility_version=PROFILE_ID_V13,
            packet_count=policy.packet_count,
            snapshot_format=policy.snapshot_format,
            send_trigger=policy.send_trigger,
            initialises_client_container=PRISON_FIGHT_SNAPSHOT_CONTAINER,
            field_names=list(policy.field_names()),
            field_origins=policy.field_origins(),
            # The client reads the container from `pkgMainUIMgr.onPanelClose`,
            # i.e. on the first panel close after the main-city switch the
            # S2C283 below starts; this position provably precedes it.
            precedes_scene_switch_packet=True,
            scene_switch_boundary_protocol_id=283,
            historical_ordering=policy.historical_ordering,
            historical_values_recovered=policy.historical_values_recovered,
            ordering_origin=policy.ordering_provenance.origin,
        )

    def _send_achieve_level_grant(
        self,
        connection_id: str,
        writer: WriterState,
        level: int,
    ) -> None:
        """Send the Dev-Server achieve-rank overlay (S2C 585/586/589).

        ``pkgAchieveMod.s_achieveLevel(tbSocket, dLevel, ...)`` sets
        ``le_dLevel`` from the first wire value; ``pkgUIChapterEventView``
        compares that level with a chapter option's ``dLevelLimit`` and shows
        ``achieve_level_limit`` ("请达到二钱天师") when it is short.  The save has
        no recovered achieve producer, so this packet is emitted ONLY under the
        explicit ``--achieve-level`` Dev-Server flag: session-scoped, labelled
        synthetic, never persisted, and absent from every normal login stream.
        The empty start/end frames are the client's own container init.
        """

        start = self._send_s2c(
            connection_id,
            writer,
            "achieveAllStart",
            (),
            classification="synthetic_test_grant",
        )
        end = self._send_s2c(
            connection_id,
            writer,
            "achieveAllEnd",
            (),
            classification="synthetic_test_grant",
        )
        level_response = self._send_s2c(
            connection_id,
            writer,
            "achieveLevel",
            (level, 0, 0, 0, 0, 0),
            decoded_fields={
                "level": level,
                "clg1": 0,
                "clg2": 0,
                "clg3": 0,
                "clg4": 0,
                "clg5": 0,
            },
            classification="synthetic_test_grant",
        )
        self.server.event_logger.emit(
            "tcp_achieve_level_grant_sent",
            connection_id=connection_id,
            provenance=OWNER_DRAW_TEST_ORIGIN,
            achieve_level=level,
            packet_ids=[
                start.spec.protocol_id,
                end.spec.protocol_id,
                level_response.spec.protocol_id,
            ],
            claimed_level_tasks=0,
            persisted=False,
            device_acceptance_baseline=False,
            historical_values_recovered=False,
        )

    def _bag_sort_evidence(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        *,
        ops: int,
        sort: int,
        packet_sequence: int,
    ) -> dict[str, Any]:
        """Turn one `C2S 512` into a durable bag sort row, or say why not.

        Every field of the result is part of the emitted event, so a reader of
        the log sees the whole decision: which slot the packet names, whether a
        row was written, whether one was already there, whether the stored value
        changed, and -- when nothing was stored -- the closed reason.

        Two things must hold, and neither is inferred:

        * the packet names one of the five slots the recovered client can write
          (`BAG_SORT_RECOVERED_SLOTS`) and carries a value the `N` wire field
          can hold;
        * the session is bound to a role this archive really holds -- the store
          re-reads the role row inside its own transaction, so an unbound
          session, another account's session or a made-up record writes nothing.

        Nothing about the sort is interpreted: the unpacked race/rank/icon
        meaning lives in the client's own reader, which re-validates every field
        against its own recovered enum bounds before it applies anything.

        A write failure is reported as an error event and leaves the session
        alive: the client wrote its local value before sending and needs no
        answer, so our own bookkeeping failing is not something it could act on.
        """

        fields: dict[str, Any] = {
            # One key name for the slot everywhere: the packet's `dOps` here, and
            # the stored row's own `ops` when a row was written (same value).
            "bag_sort_ops": ops,
            "bag_sort_stored": False,
            "bag_sort_outcome": None,
            "bag_sort_refusal": None,
            "server_bag_state_written": False,
            "persistence_policy": "observed_c2s512_only",
            "persistence_origin": "synthetic_compatibility",
        }
        if ops not in BAG_SORT_RECOVERED_SLOTS:
            fields["bag_sort_refusal"] = BAG_SORT_REFUSAL_SLOT_NOT_RECOVERED
            return fields
        if not 0 <= sort <= BAG_SORT_MAX_WIRE_VALUE:
            fields["bag_sort_refusal"] = BAG_SORT_REFUSAL_VALUE_NOT_ENCODABLE
            return fields
        role_record = session_state.get("role_record")
        if role_record is None:
            fields["bag_sort_refusal"] = BAG_SORT_REFUSAL_UNBOUND_ROLE
            return fields
        observed_at = self.server.now_unix()
        try:
            record, outcome = self.server.role_store.record_bag_sort_state(
                record=role_record,
                ops=ops,
                sort=sort,
                observed_at=observed_at,
                packet_sequence=int(packet_sequence),
                evidence_kind=BAG_SORT_EVIDENCE_CLIENT_C2S,
            )
        except RoleStoreError as exc:
            # Fail visibly, never silently: the setting is not stored, the event
            # says so, and the session continues because the client's own
            # notification needs no answer.
            self._emit_error(
                connection_id,
                "bag_sort_write_failed",
                str(exc),
                bag_sort_ops=ops,
                **self.server.role_store.profile_fields(),
            )
            fields["bag_sort_refusal"] = BAG_SORT_REFUSAL_ARCHIVE
            return fields
        fields.update(record.evidence_fields())
        fields.update(
            {
                "bag_sort_stored": True,
                "bag_sort_outcome": outcome,
                "server_bag_state_written": outcome != "unchanged",
                "persistence_origin": BAG_SORT_ORIGIN_LOCAL_OBSERVATION,
                "account_key_short": record.display_key(),
            }
        )
        return fields

    @staticmethod
    def _bag_sort_replay_refusal(
        row: BagSortRecord, role: MinimalRoleState
    ) -> str | None:
        """Why a stored bag sort row may not be replayed to this session.

        Pure: the row is checked against the slots that have a recovered reader
        in the bag panel, against the role actually logging in, against the
        wire field's own range, and against the provenance this build writes --
        so a row written by hand, by another tool or by a later version can
        never make the server assert a setting it cannot prove.
        """

        if row.ops not in BAG_SORT_REPLAYABLE_SLOTS:
            return BAG_SORT_REPLAY_REFUSAL_SLOT_NOT_REPLAYABLE
        if row.role_id != role.role_id:
            return BAG_SORT_REPLAY_REFUSAL_ROLE_MISMATCH
        if not 0 <= row.sort <= BAG_SORT_MAX_WIRE_VALUE:
            return BAG_SORT_REPLAY_REFUSAL_VALUE
        if row.origin != BAG_SORT_ORIGIN_LOCAL_OBSERVATION:
            return BAG_SORT_REPLAY_REFUSAL_ORIGIN
        return None

    def _send_bag_sort_rows(
        self,
        connection_id: str,
        writer: WriterState,
        role: MinimalRoleState,
        session_state: dict[str, Any],
    ) -> None:
        """Replay the archive's observed bag sort slots, right after `S2C 27`.

        Only rows this build can vouch for are sent, one `S2C 1048 bagSortState`
        each, carrying the slot and the packed value the client itself sent.
        The position (after the role-value snapshot the bag's item panel reads,
        before the scene switch) is compatibility ordering: the historical send
        position was never recovered.  A row that is refused is reported as its
        own event instead of being sent or silently dropped.  With no stored row
        nothing is sent at all, so an archive that has never had a sort change
        produces exactly the login stream it produced before.
        """

        account_key = session_state.get("account_key")
        if account_key is None:  # pragma: no cover - the login path always sets it
            return
        for row in self.server.role_store.bag_sort_states(account_key=account_key):
            refusal = self._bag_sort_replay_refusal(row, role)
            if refusal is not None:
                self.server.event_logger.emit(
                    "tcp_bag_sort_not_replayed",
                    connection_id=connection_id,
                    remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                    role_id=role.role_id,
                    account_key_short=row.display_key(),
                    reason=refusal,
                    replayed=False,
                    **row.evidence_fields(),
                )
                continue
            response = self._send_s2c(
                connection_id,
                writer,
                "bagSortState",
                (row.ops, row.sort),
                decoded_fields={"dOps": row.ops, "dSort": row.sort},
                classification="observed_bag_sort_row",
            )
            self._emit_compatibility_packet(
                "tcp_bag_sort_replayed",
                connection_id,
                response,
                role,
                replayed=True,
                bag_sort_ordering="compatibility_ordering_after_role_values",
                historical_ordering_unknown=True,
                **row.evidence_fields(),
            )

    def _emit_compat_role_initialized(
        self,
        connection_id: str,
        role: MinimalRoleState,
        *,
        role_origin: str,
        persistence: dict[str, Any],
    ) -> None:
        compatibility = role.compatibility
        assert compatibility is not None
        self.server.event_logger.emit(
            "tcp_compat_role_initialized",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            role_id=role.role_id,
            role_name=role.role_name,
            career=role.career,
            server_id=role.server_id,
            profile_id=compatibility.profile.profile_id,
            compatibility_goal=COMPATIBILITY_GOAL,
            role_value_field_count=len(compatibility.role_values),
            role_value_class_counts=compatibility.profile.class_counts(),
            role_value_vector_hash=compatibility.vector_hash(),
            sign_state_origin=(
                compatibility.profile.sign_state_template.provenance.origin
            ),
            scene_state_origin=(
                compatibility.profile.scene_state_template.provenance.origin
            ),
            active_soul=compatibility.active_soul,
            active_soul_origin=(
                compatibility.profile.active_soul_for_career(
                    role.career
                ).provenance.origin
            ),
            role_origin=role_origin,
            **persistence,
        )

    def _dev_play_babel_overlay(
        self, connection_id: str
    ) -> BabelOverlay | None:
        """The validated dev-play overlay for this login, or ``None``.

        All three conditions are checked here, on every login: the explicit
        server flag, the archive's own ``play_profile_id`` and the stored
        ``meta`` value.  A present-but-unusable meta is reported with its named
        reason and then ignored (the login continues with the frozen v18
        packets) -- it is never treated as absence without a log line.
        """

        if not self.server.dev_play_babel:
            return None
        if not self.server.dev_play_babel_profile_matches:
            return None
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        try:
            overlay = dev_play_babel.load_overlay(self.server.role_store)
        except DevPlayBabelError as exc:
            self.server.event_logger.emit(
                "tcp_dev_play_babel_overlay_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile_id=self.server._owner_draw_profile_id,
                overlay_meta_key=DEV_PLAY_BABEL_META_KEY,
                reason=exc.reason,
                detail=str(exc),
                applied=False,
                persisted=False,
            )
            return None
        if overlay is None:
            self.server.event_logger.emit(
                "tcp_dev_play_babel_overlay_absent",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile_id=self.server._owner_draw_profile_id,
                overlay_meta_key=DEV_PLAY_BABEL_META_KEY,
                applied=False,
                reason="no_overlay_meta",
            )
            return None
        self.server.event_logger.emit(
            "tcp_dev_play_babel_overlay_applied",
            connection_id=connection_id,
            remote_endpoint=remote,
            profile=DEV_PLAY_BABEL_PROFILE_ID,
            role_level=overlay.role_level,
            floor=overlay.floor,
            state=overlay.state,
            state_name=overlay.state_name,
            failCount=overlay.fail_count,
            realm=overlay.realm,
            field2=overlay.field2,
            field2_provenance=BABEL_FIELD_2_PROVENANCE,
            origin=DEV_PLAY_BABEL_ORIGIN,
            replaceable=True,
            historical=False,
            persisted=False,
        )
        return overlay

    def _dev_play_artifact_overlay(
        self, connection_id: str
    ) -> ArtifactOverlay | None:
        """The validated dev-play artifact overlay for this login, or ``None``.

        All three conditions are checked here, on every login: the explicit
        server flag, the archive's own ``play_profile_id`` and the stored
        ``meta`` value.  A present-but-unusable meta is reported with its named
        reason and then ignored (the login continues with the frozen stream and
        a later C2S 744 is recorded without an answer) -- it is never treated as
        absence without a log line.
        """

        if not self.server.dev_play_artifact:
            return None
        if not self.server.dev_play_artifact_profile_matches:
            return None
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        try:
            overlay = dev_play_artifact.load_overlay(self.server.role_store)
        except DevPlayArtifactError as exc:
            self.server.event_logger.emit(
                "tcp_dev_play_artifact_overlay_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile_id=self.server._owner_draw_profile_id,
                overlay_meta_key=DEV_PLAY_ARTIFACT_META_KEY,
                reason=exc.reason,
                detail=str(exc),
                applied=False,
                persisted=False,
            )
            return None
        if overlay is None:
            self.server.event_logger.emit(
                "tcp_dev_play_artifact_overlay_absent",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile_id=self.server._owner_draw_profile_id,
                overlay_meta_key=DEV_PLAY_ARTIFACT_META_KEY,
                applied=False,
                reason="no_overlay_meta",
            )
            return None
        self.server.event_logger.emit(
            "tcp_dev_play_artifact_overlay_applied",
            connection_id=connection_id,
            remote_endpoint=remote,
            profile=DEV_PLAY_ARTIFACT_PROFILE_ID,
            role_level=overlay.role_level,
            entry=overlay.entry,
            origin=DEV_PLAY_ARTIFACT_ORIGIN,
            replaceable=True,
            historical=False,
            persisted=False,
        )
        return overlay

    def _dev_play_resources_overlay(
        self, connection_id: str
    ) -> ResourcesOverlay | None:
        """The validated dev-play resources overlay for this login, or ``None``.

        All three conditions are re-checked here: the explicit server flag, the
        archive's own ``play_profile_id`` and the stored ``meta`` value.  A
        present-but-unusable meta is reported with its named reason and then
        ignored (the login continues with the frozen v18 packets) -- it is
        never treated as absence without a log line.
        """

        if not self.server.dev_play_resources:
            return None
        if not self.server.dev_play_resources_profile_matches:
            return None
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        try:
            overlay = dev_play_resources.load_overlay(self.server.role_store)
        except DevPlayResourcesError as exc:
            self.server.event_logger.emit(
                "tcp_dev_play_resources_overlay_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile=self.server._owner_draw_profile_id,
                overlay_meta_key=DEV_PLAY_RESOURCES_META_KEY,
                reason=exc.reason,
                detail=str(exc),
                applied=False,
                persisted=False,
            )
            return None
        if overlay is None:
            self.server.event_logger.emit(
                "tcp_dev_play_resources_overlay_absent",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile=self.server._owner_draw_profile_id,
                overlay_meta_key=DEV_PLAY_RESOURCES_META_KEY,
                applied=False,
                reason="no_overlay_meta",
            )
            return None
        payload = overlay.to_payload()
        self.server.event_logger.emit(
            "tcp_dev_play_resources_overlay_applied",
            connection_id=connection_id,
            remote_endpoint=remote,
            profile=DEV_PLAY_RESOURCES_PROFILE_ID,
            role_level=overlay.role_level,
            lottery_ticket=overlay.lottery_ticket,
            lottery_ticket_advanced=overlay.lottery_ticket_advanced,
            wallet=overlay.wallet_values,
            provenance=payload["provenance"],
            origin=DEV_PLAY_RESOURCES_ORIGIN,
            value_origin=DEV_PLAY_RESOURCES_VALUE_ORIGIN,
            replaceable=True,
            historical=False,
            persisted=False,
        )
        return overlay

    def _provision_dev_play_resources_wallet(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        overlay: ResourcesOverlay,
    ) -> dict[str, float]:
        """Create this archive's missing wallet rows once, before any S2C 27.

        Only missing ``value_name`` rows are inserted; an existing row is never
        topped up.  That is what makes the client's own 强化 spend survive a
        re-login: the next login sees the already-spent balance and this helper
        finds every row present, so it writes nothing.
        """

        account_key = session_state.get("account_key")
        role_record = session_state.get("role_record")
        if account_key is None or role_record is None:
            return {}
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        try:
            created = self.server.role_store.provision_role_wallet_once(
                record=role_record,
                values=overlay.wallet_values,
                provenance=DEV_PLAY_RESOURCES_ORIGIN,
            )
        except RoleStoreError as exc:
            # A failed provision is not a partial write (one transaction), so
            # the login can continue with whatever rows already exist.  Name it
            # instead of hiding the fact.
            self.server.event_logger.emit(
                "tcp_dev_play_resources_wallet_provision_failed",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile=DEV_PLAY_RESOURCES_PROFILE_ID,
                detail=str(exc)[:240],
                persisted=False,
                state_changed=False,
            )
            return {}
        if created:
            self.server.event_logger.emit(
                "tcp_dev_play_resources_wallet_provisioned",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile=DEV_PLAY_RESOURCES_PROFILE_ID,
                values=[
                    {
                        "value_name": name,
                        "amount": amount,
                        "provenance": DEV_PLAY_RESOURCES_ORIGIN,
                    }
                    for name, amount in created.items()
                ],
                provisioned=sorted(created),
                provenance=DEV_PLAY_RESOURCES_ORIGIN,
                value_origin=DEV_PLAY_RESOURCES_VALUE_ORIGIN,
                top_up_policy="provision_missing_rows_once",
                existing_rows_untouched=True,
                replaceable=True,
                historical=False,
                persisted=True,
            )
        return created

    def _provision_dev_play_ticket_balance(
        self,
        connection_id: str,
        session_state: dict[str, Any],
        overlay: ResourcesOverlay,
    ) -> dict[str, int] | None:
        """Read this archive's ticket ledger, creating the 30/30 seed once.

        One ``BEGIN IMMEDIATE`` transaction in ``role_store`` reads the marker
        and inserts the seed only when it is missing, so the first login and a
        first draw can never disagree about the initial balance.  A marker that
        exists but does not validate is *not* replaced: the caller withholds the
        whole resources overlay, so no ticket value is sent and the draw gate
        stays closed.
        """

        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        try:
            counts, created = self.server.role_store.read_ticket_balance(
                meta_key=DEV_PLAY_TICKET_BALANCE_META_KEY,
                seed_counts=DEV_PLAY_TICKET_BALANCE_SEED,
                decode=decode_ticket_balance,
                encode=encode_ticket_balance,
            )
        except (RoleStoreError, DevPlayResourcesError) as exc:
            reason = getattr(exc, "reason", "ticket_balance_unreadable")
            self.server.event_logger.emit(
                "tcp_dev_play_resources_ticket_balance_refused",
                connection_id=connection_id,
                remote_endpoint=remote,
                profile=DEV_PLAY_RESOURCES_PROFILE_ID,
                overlay_meta_key=DEV_PLAY_TICKET_BALANCE_META_KEY,
                reason=reason,
                detail=str(exc)[:240],
                applied=False,
                persisted=False,
            )
            return None
        session_state["dev_play_ticket_balance"] = counts
        self.server.event_logger.emit(
            "tcp_dev_play_resources_ticket_balance_ready",
            connection_id=connection_id,
            remote_endpoint=remote,
            profile=DEV_PLAY_RESOURCES_PROFILE_ID,
            values=[
                {
                    "value_name": item["value_name"],
                    "value_index": int(item["value_index"]),
                    "wire_position": int(item["wire_position"]),
                    "reward_type": int(item["reward_type"]),
                    "amount": int(counts[item["value_name"]]),
                }
                for item in overlay.ticket_values
            ],
            created=created,
            balance_origin=DEV_PLAY_TICKET_BALANCE_ORIGIN,
            provenance=DEV_PLAY_TICKET_BALANCE_PROVENANCE,
            replaceable=True,
            historical=False,
            persisted=True,
        )
        return counts

    def _send_base_role_segment(
        self,
        connection_id: str,
        writer: WriterState,
        session_state: dict[str, Any],
        role: MinimalRoleState,
        *,
        role_origin: str,
        include_create_result: bool,
        persistence: dict[str, Any],
    ) -> None:
        """Send the recovered base role segment (sync index 1) and arm the ACK.

        This is the one recovered closure both entry paths share, and the only
        thing that differs between them is the optional leading S2C14:

        * a *new* role answers the client's own C2S2 with
          ``returnCreateRoleResult(true)`` before the segment;
        * an *existing* role sends no S2C14 at all.  S2C14 is the direct
          response to ``createRole`` (``registerS2C("y",
          "returnCreateRoleResult")`` names no lock partner and its handler only
          calls ``pkgSDKPlayerInfoMgr.createRole()``), so emitting it without a
          C2S2 would be an invented packet in a session that never asked to
          create anything.

        Both then send exactly what the recovered client needs to reach
        ``pkgPlayerInfoMgr.initSuperManInfo`` and to answer with a real
        ``C2S3 receivePackage(1, false)``:
        ``S2C8(1, false)`` → ``S2C10(role)`` → ``S2C9(1, false)``.
        """

        if include_create_result:
            self._send_s2c(
                connection_id,
                writer,
                "returnCreateRoleResult",
                (True,),
            )
        self._send_s2c(
            connection_id,
            writer,
            "returnLoginInfoStart",
            (SYNC_INDEX_START, False),
        )
        role_fields = role.protocol_fields()
        # TASK-0232: the dev-play overlay is re-read from the archive's own meta
        # on every login.  A login without it (flag off, other profile, no meta,
        # or an unusable meta) keeps the frozen v18 packet stream exactly.
        dev_play_overlay = self._dev_play_babel_overlay(connection_id)
        session_state["dev_play_babel_overlay"] = dev_play_overlay
        if dev_play_overlay is not None:
            # TASK-0237/0238: a dev-babel-main session whose overlay is really
            # applied re-instantiates the role's compatibility state from the
            # opt-in v20 profile.  v20 inherits every v19 policy by reference --
            # including v19's type-aware `battle_start_reply` -- and adds ONLY
            # `babel_settlement_push`, the single S2C 580 push that follows a
            # paired 73 victory (D-192/D-194).  No other session can reach this
            # line: the overlay itself is gated on flag + profile + stored meta,
            # and every login-time policy object is v18's, so the login stream
            # is unchanged.
            role.compatibility = instantiate_role_compatibility_state(
                role.career, profile=get_profile_v20()
            )
            self.server.event_logger.emit(
                "tcp_dev_play_babel_profile_selected",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                play_profile_id=DEV_PLAY_BABEL_PROFILE_ID,
                profile_id=PROFILE_ID_V20,
                parent_profile_id=PROFILE_ID_V19,
                selected_by="dev_play_babel_overlay_applied",
                battle_start_reply_policy="type_aware_echo",
                dispatch_provenance="deterministic_recovered",
                seed_policy=BATTLE_START_TYPE_RANDOM_SEED_POLICY,
                seed_provenance="synthetic_compatibility",
                babel_settlement_push_policy="synthetic_overlay_echo",
                login_stream_changed=False,
                replaceable=True,
                historical=False,
            )
        # TASK-0258: the dev-play artifact overlay.  It carries exactly one
        # wire value into the login stream -- the session-scoped level 60 the
        # client's own `functionOpen[43]` gate reads.  Its two answer packets
        # are held for the C2S 744 this session may later send, so a login that
        # never opens the panel sends nothing extra.
        artifact_overlay = self._dev_play_artifact_overlay(connection_id)
        session_state["dev_play_artifact_overlay"] = artifact_overlay
        if (
            isinstance(artifact_overlay, ArtifactOverlay)
            and self.server.dev_play_artifact_profile_matches
        ):
            # TASK-0456: the entry page sends no request, so the shelves are
            # pushed on the heartbeat until the client has loaded the artifact
            # shop module (every 10th tick keeps the traffic negligible).
            session_state["artifact_shop_push_tick"] = 0
        if (
            self.server.dev_play_babel
            and self.server.dev_play_babel_profile_matches
        ):
            session_state["dev_mail_tick"] = 0

        # TASK-0233: a second, profile-scoped overlay.  Its level is also
        # session-scoped (the roles row has no level column and is not written).
        resources_overlay = self._dev_play_resources_overlay(connection_id)
        if resources_overlay is not None and (
            self._provision_dev_play_ticket_balance(
                connection_id, session_state, resources_overlay
            )
            is None
        ):
            # A damaged ledger closes the whole overlay: the login then keeps
            # the frozen v18 stream (level 1, zero wallet, no draw gate)
            # instead of announcing a ticket balance nobody can trust.
            resources_overlay = None
        session_state["dev_play_resources_overlay"] = resources_overlay
        level_overlay = None
        level_overlay_reason = None
        if dev_play_overlay is not None:
            # The client's babel entry gate reads this same S2C 10 level value
            # (functionOpen[15].openLevel = 29).  Session-scoped: the roles row
            # has no level column and is not written (role_level_overlay_persisted
            # = False below).
            level_overlay = dev_play_overlay.role_level
            level_overlay_reason = DEV_PLAY_BABEL_LEVEL_OVERLAY_REASON
        elif artifact_overlay is not None:
            # The client's artifact entry gate reads this same S2C 10 level
            # value (functionOpen[43].openLevel = 60; functionOpen[35] = 38 for
            # the equip-fight panel that owns the tab).  Session-scoped: the
            # roles row has no level column and is not written.
            level_overlay = artifact_overlay.role_level
            level_overlay_reason = DEV_PLAY_ARTIFACT_LEVEL_OVERLAY_REASON
        elif resources_overlay is not None:
            # The resources overlay's level is a dev-play constant whose only
            # recovered anchor is the babel threshold (functionOpen[15]);
            # it is announced for this session only.
            level_overlay = resources_overlay.role_level
            level_overlay_reason = DEV_PLAY_RESOURCES_LEVEL_OVERLAY_REASON
        elif (
            self.server.owner_draw_test_grant is not None
            and self.server.draw_odds == DRAW_ODDS_RECONSTRUCTED
        ):
            # The client's hot-spot tab gate is its own functionOpen row
            # (macro "hot_spot", openLevel 15).  The QA profile is level 1, so
            # the Dev Server session announces level 15 in the login role info;
            # this is session-scoped and never written to the role row.
            level_overlay = HOT_SPOT_OPEN_LEVEL
            level_overlay_reason = "hot_spot_open_level"
        if level_overlay is not None:
            role_fields = tuple(
                level_overlay if name == "level" else value
                for name, value in zip(
                    ROLE_INFO_FIELD_NAMES, role_fields, strict=True
                )
            )
        gacha_active = (
            self.server.dev_play_gacha
            and self.server.dev_play_gacha_profile_matches
        )
        dev_crystal_floor = (
            gacha_active
            or (self.server.dev_play_artifact
                and self.server.dev_play_artifact_profile_matches)
            or (self.server.dev_play_resources
                and self.server.dev_play_resources_profile_matches)
            or (self.server.dev_play_babel
                and self.server.dev_play_babel_profile_matches)
        )
        if dev_crystal_floor:
            # TASK-0461: the Owner asked for a constant 元宝 floor on the dev
            # profiles.  The roles row has no crystal column and nothing is
            # written; every login re-announces 99999.  Draws still come from
            # the deterministic fixture.
            role_fields = tuple(
                99999 if name == "crystal"
                else (60 if name == "level" and gacha_active
                      else (99999 if name == "refine_stone"
                            else value))
                for name, value in zip(
                    ROLE_INFO_FIELD_NAMES, role_fields, strict=True
                )
            )
        readable_role_fields = dict(zip(ROLE_INFO_FIELD_NAMES, role_fields, strict=True))
        readable_role_fields["username"] = mask_secret(readable_role_fields["username"])
        self._send_s2c(
            connection_id,
            writer,
            "returnRoleInfo",
            role_fields,
            decoded_fields=readable_role_fields,
            classification="minimal_role_snapshot",
        )
        # TASK-0242 / Issue #30: the empty mail bracket.  The shipped client
        # creates `le_tbMailDict` only from S2C 255/256, and `hasNewMail`
        # (pkgMailModelMgr source line 132) calls pairs() on it unguarded, so a
        # login without this pair throws the RUN-0076 LUA ERROR on the first
        # main-city red-point refresh.  Emitted only when the profile this
        # connection actually uses carries the policy: the default v21 does, the
        # dev-babel-main overlay session ('v20') and every frozen v18/v19
        # session do not, so their streams keep their exact bytes.  Zero rows,
        # zero values, no mail/attachment/reward/red-point packet.
        compatibility = role.compatibility
        mail_policy = (
            compatibility.profile.mail_login
            if compatibility is not None
            else None
        )
        if mail_policy is not None:
            mail_packets = mail_policy.packets()
            for name, fields in mail_packets:
                self._send_s2c(
                    connection_id,
                    writer,
                    name,
                    fields,
                    classification="empty_mail_list",
                )
            self.server.event_logger.emit(
                "tcp_mail_login_block_sent",
                connection_id=connection_id,
                remote_endpoint=(
                    f"{self.client_address[0]}:{self.client_address[1]}"
                ),
                role_id=role.role_id,
                profile_id=compatibility.profile.profile_id,
                parent_profile_id=compatibility.profile.parent_profile_id,
                login_position=mail_policy.login_position,
                ordering_origin="synthetic_compatibility",
                historical_ordering=mail_policy.historical_ordering,
                rows=mail_policy.row_count,
                replaceable=mail_policy.replaceable,
                historical=mail_policy.historical,
                historical_values_recovered=(
                    mail_policy.historical_values_recovered
                ),
                block_structure_origin=mail_policy.block_provenance.origin,
                packet_count=mail_policy.packet_count,
                protocol_ids=[
                    mail_policy.start_wire_id,
                    mail_policy.end_wire_id,
                ],
                protocol_names=[
                    mail_policy.start_name,
                    mail_policy.end_name,
                ],
                packet_order="returnMailStart_then_returnMailEnd",
                single_mail_rows_registered=False,
            )
        self._send_s2c(
            connection_id,
            writer,
            "returnLoginInfoEnd",
            (SYNC_INDEX_START, False),
        )
        session_state["role"] = role
        session_state["role_origin"] = role_origin
        session_state["sync_phase"] = "awaiting_base_ack"
        self.server.event_logger.emit(
            "tcp_login_sync_segment_sent",
            connection_id=connection_id,
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            sync_index=SYNC_INDEX_START,
            classification="minimal_base_role_segment",
            role_origin=role_origin,
            role_id=role.role_id,
            create_role_result_sent=include_create_result,
            role_level_overlay=level_overlay,
            role_level_overlay_reason=level_overlay_reason,
            role_level_overlay_persisted=False,
            **(
                {
                    "role_level_overlay_origin": DEV_PLAY_BABEL_ORIGIN,
                    "role_level_overlay_replaceable": True,
                    "role_level_overlay_historical": False,
                }
                if dev_play_overlay is not None
                else (
                    {
                        "role_level_overlay_origin": DEV_PLAY_ARTIFACT_ORIGIN,
                        "role_level_overlay_replaceable": True,
                        "role_level_overlay_historical": False,
                    }
                    if artifact_overlay is not None
                    else (
                        {
                            "role_level_overlay_origin": (
                                DEV_PLAY_RESOURCES_ORIGIN
                            ),
                            "role_level_overlay_replaceable": True,
                            "role_level_overlay_historical": False,
                        }
                        if resources_overlay is not None
                        else {}
                    )
                )
            ),
            **persistence,
        )

    def _handle_reconnect(
        self, connection_id, writer, packet, session_state
    ) -> bool:
        """Resume a stored role on a new connection (C2S 10 -> S2C 15).

        The client sends this only after it has completed a login in this
        process (`pkgReconnectMgr.reconnectLogin` checks `getGameLogined()`),
        and its S2C 15 handler only tears down the reconnect view and replays
        the last unacked request -- it never asks for the login segment again.
        The server therefore re-binds the same stored role and answers with the
        empty ``reconnectSuccess``; the client keeps its role and card state.
        """

        fields = packet.fields
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        client_version = _text(fields[4])
        if client_version != self.server.expected_version:
            self._emit_error(
                connection_id,
                "reconnect_version_mismatch",
                f"expected {self.server.expected_version}, got {client_version}",
            )
            return False
        identity = {
            "server_id": fields[0],
            "platform": _text(fields[7]),
            "channel_id": _text(fields[5]),
            "channel_sub_id": _text(fields[6]),
            "sdk_identity": _text(fields[1]),
        }
        if not identity["sdk_identity"]:
            self._emit_error(
                connection_id,
                "reconnect_identity_missing",
                "reconnect carried no sdk user id",
            )
            return False
        try:
            account_key = self.server.role_store.account_key(**identity)
        except RoleStoreError as exc:
            self._emit_error(
                connection_id, "reconnect_identity_unavailable", str(exc)
            )
            return False
        existing = self.server.role_store.lookup(account_key)
        if existing is None:
            self._emit_error(
                connection_id,
                "reconnect_unknown_role",
                "no stored role for the reconnect identity",
            )
            return False
        try:
            role = existing.to_role_state(
                username=existing.role_name,
                profile=self.server.compatibility_profile,
            )
        except ValueError as exc:
            self._emit_error(
                connection_id,
                "reconnect_unusable_stored_role",
                str(exc),
                stored_career=existing.career,
                stored_role_id=existing.role_id,
            )
            return False
        session_state["account_key"] = account_key
        session_state["role"] = role
        session_state["role_record"] = existing
        session_state["role_origin"] = ROLE_ORIGIN_RECONNECTED
        session_state["sync_phase"] = "complete"
        # TASK-0233: a reconnect does not resend S2C 27, but the client can
        # still send C2S 274/33 on this socket.  Re-read the same three-condition
        # overlay (and, harmlessly, the already-idempotent wallet provision) so
        # the draw gate is not lost just because the transport reconnected.
        resources_overlay = self._dev_play_resources_overlay(connection_id)
        if resources_overlay is not None and (
            self._provision_dev_play_ticket_balance(
                connection_id, session_state, resources_overlay
            )
            is None
        ):
            resources_overlay = None
        session_state["dev_play_resources_overlay"] = resources_overlay
        if resources_overlay is not None:
            self._provision_dev_play_resources_wallet(
                connection_id, session_state, resources_overlay
            )
        session_state["login"] = {
            "server_id": fields[0],
            "username": existing.role_name,
        }
        session_state["reconnect_needs_scene"] = True
        response = self._send_s2c(
            connection_id,
            writer,
            "reconnectSuccess",
            (),
            classification="recovered_reconnect_ack",
        )
        self.server.event_logger.emit(
            "tcp_reconnect_accepted",
            connection_id=connection_id,
            remote_endpoint=remote,
            role_id=existing.role_id,
            profile_id=self.server.compatibility_profile.profile_id,
            role_origin=ROLE_ORIGIN_RECONNECTED,
            server_id=fields[0],
            client_version=client_version,
            identity=mask_secret(identity["sdk_identity"]),
            response_protocol_id=response.spec.protocol_id,
            response_protocol_name=response.spec.name,
            response_wire_length=len(response.wire),
            response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
            historical_values_recovered=True,
        )
        return True

    def handle(self) -> None:
        connection_id = self.server.next_connection_id()
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        self.server.event_logger.emit(
            "tcp_connected", connection_id=connection_id, remote_endpoint=remote
        )
        self.request.settimeout(5.0)
        reader = ReaderState()
        writer = WriterState()
        buffer = bytearray()
        first_packet = True
        session_state: dict[str, Any] = {}
        try:
            while True:
                try:
                    packet = decode_packet(buffer, self.server.decode_c2s_registry, reader)
                except NeedMoreData:
                    try:
                        chunk = self.request.recv(65536)
                    except socket.timeout:
                        self._emit_error(connection_id, "timeout", "timed out waiting for packet bytes")
                        break
                    if not chunk:
                        if buffer:
                            self._emit_error(
                                connection_id,
                                "truncated_packet",
                                f"peer closed with {len(buffer)} buffered byte(s)",
                            )
                        break
                    buffer.extend(chunk)
                    continue
                except SequenceMismatch as exc:
                    self._emit_error(connection_id, "wrong_sequence", str(exc))
                    break
                except UnknownProtocol as exc:
                    extra: dict[str, Any] = {
                        "protocol_id": exc.protocol_id,
                        "raw_header_hex": exc.raw_header.hex(" "),
                    }
                    if self.server.event_logger.hex_dump:
                        # Unknown formats have no trustworthy packet boundary;
                        # preserve the currently buffered bytes for replay.
                        extra["raw_buffer_hex"] = exc.raw_buffer.hex(" ")
                        extra["raw_buffer_length"] = len(exc.raw_buffer)
                    self._emit_error(
                        connection_id,
                        "malformed_packet",
                        str(exc),
                        **extra,
                    )
                    break
                except CodecError as exc:
                    self._emit_error(connection_id, "malformed_packet", str(exc))
                    break

                del buffer[: packet.consumed]
                decoded_fields = (
                    readable_login_fields(packet.fields)
                    if packet.spec.name == "loginByToken"
                    else [
                        _text(value) if isinstance(value, bytes) else value
                        for value in packet.fields
                    ]
                )
                catalog_only = (
                    self.server.protocol_policy
                    == PROTOCOL_POLICY_FREE_PLAY_TOLERANT
                    and packet.spec.protocol_id not in self.server.implemented_c2s_ids
                )
                self._emit_packet(
                    "tcp_packet_received",
                    connection_id,
                    packet,
                    decoded_fields=decoded_fields,
                    classification=(
                        "free_play_unsupported_known" if catalog_only else "ok"
                    ),
                )

                if first_packet and packet.spec.name not in {
                    "loginByToken",
                    "reconnect",
                }:
                    self._emit_error(
                        connection_id,
                        "unexpected_first_protocol",
                        f"expected loginByToken(0), got {packet.spec.name}({packet.spec.protocol_id})",
                    )
                    break
                if packet.spec.name == "reconnect":
                    # A new socket after a background/disconnect resumes the
                    # stored role; the client's own reconnectSuccess handler
                    # keeps the loaded role/card state and replays its last
                    # unacked request.  No login segment is re-sent.
                    if not self._handle_reconnect(
                        connection_id, writer, packet, session_state
                    ):
                        break
                    first_packet = False
                    continue
                if catalog_only:
                    # Free play only: the frame had a trustworthy boundary from
                    # the client catalog, but the stub has no business handler
                    # for it.  State, rewards and role data are never touched.
                    if packet.spec.name in FREE_PLAY_STRICT_HANDSHAKES:
                        self._emit_error(
                            connection_id,
                            "free_play_handshake_refused",
                            (
                                f"{packet.spec.name}({packet.spec.protocol_id}) "
                                "is a session handshake, not a tolerant UI feature"
                            ),
                        )
                        break
                    self._handle_free_play_unsupported(
                        connection_id, writer, packet, session_state
                    )
                    continue
                if packet.spec.name != "loginByToken":
                    if packet.spec.name in {"onLine", "onLine_forNetState"}:
                        self._flush_pending_card_dex(
                            connection_id, writer, session_state
                        )
                        if session_state.pop("adv_piece_pending", False):
                            self._replay_adv_pieces(
                                connection_id,
                                writer,
                                session_state.get("role"),
                                session_state,
                            )
                        mail_tick = session_state.get("dev_mail_tick")
                        if mail_tick is not None:
                            mail_tick += 1
                            session_state["dev_mail_tick"] = mail_tick
                            if mail_tick == 2:
                                try:
                                    self._send_dev_mail_snapshot(connection_id, writer)
                                except Exception:
                                    pass
                        tick = session_state.get("artifact_shop_push_tick")
                        if tick is not None:
                            tick += 1
                            session_state["artifact_shop_push_tick"] = tick
                            # TASK-0457: the shelves only need to be on the wire
                            # once after the client loads the artifact shop
                            # module; pushing them every 10 heartbeats kept a
                            # recurring 8-packet burst.  Keep two early pushes
                            # and a slow fallback (about once a minute).
                            if tick in (2, 12):
                                try:
                                    self._push_artifact_shop(
                                        connection_id, writer, session_state
                                    )
                                except Exception:
                                    pass

                        _team_tick = session_state.get("team_replay_tick")
                        if _team_tick is not None and packet.spec.name == "onLine":
                            _team_tick += 1
                            session_state["team_replay_tick"] = _team_tick
                            if _team_tick == 2:
                                try:
                                    self._replay_dev_team_selection(
                                        connection_id, writer, session_state)
                                except Exception:
                                    pass
                        self._emit_packet(
                            "tcp_packet_ignored",
                            connection_id,
                            packet,
                            classification="ok_no_response",
                            reason=(
                                "client online heartbeat"
                                if packet.spec.name == "onLine"
                                else "client net-state heartbeat"
                            ),
                        )
                        continue
                    if packet.spec.name in {
                        "startLotteryTicketDraw",
                        "startDiamondDrawCard",
                    }:
                        # Owner draw-test overlay: a labelled deterministic
                        # fixture, never an RNG/weight/pity result (D-147).
                        self._handle_owner_draw_request(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    # `finishGuideTask` joined this set in TASK-0148: RUN-0045
                    # put C2S 232 on the wire after the S2C 89 battle start and
                    # the stub answered a registered-nowhere id with
                    # `malformed_packet` + `tcp_disconnected`
                    # (`.autopilot/device_runs/RUN-0045/server.log:421-422`),
                    # which is what the player saw as 网络断开.  It is a recovered
                    # contract with no lock partner and no S2C response, i.e. the
                    # same decode + record + no response shape as its neighbour
                    # C2S 233 finishStory -- not a relaxation of the D-08
                    # unknown-id path, which is unchanged below.
                    #
                    # `getOfflineChatData` joined this set in TASK-0249: RUN-0078
                    # (`.autopilot/device_runs/RUN-0078/server.log:605-609`) put
                    # the client's own main-city offline-chat request on the
                    # wire right after C2S 720/1487 completed, and the strict
                    # D-08 path answered `malformed_packet` + `tcp_disconnected`
                    # on the connection that had just reached the main city.
                    # `pkgFriendChatFileMgr.getOfflineChatData` (PROTO 0.5,
                    # source 61-63) is one `c2s.getOfflineChatData(e_tbSocket)`
                    # call with no lock timer and no S2C wait; the client's own
                    # registration contains no response for it.
                    if packet.spec.name in {
                        "updateApkSize",
                        "oneMinute",
                        "speedHeart",
                        "refreshSign7Time",
                        "requestExitRoom",
                        "systemGMList",
                        "cgStatusSave",
                        "finishGuideTask",
                        "getOfflineChatData",
                        "setDrawCardAutoSplit",
                        "startStamp",
                        "startStampScene",
                        "updateSceneType",
                        "syncOnlinePlayers",
                    }:
                        reasons = {
                            "updateApkSize": "recovered Android APK metadata notification",
                            "oneMinute": "recovered periodic client timer notification",
                            "speedHeart": (
                                "recovered periodic client speed-heart notification; "
                                "the registration contains no S2C response"
                            ),
                            "refreshSign7Time": "recovered non-locking sign timing refresh",
                            "requestExitRoom": "recovered reconnect room-cleanup notification",
                            "systemGMList": "recovered optional debug request; no response is reconstructed",
                            "cgStatusSave": "recovered optional CG progress notification; persistence is out of scope",
                            "finishGuideTask": (
                                "recovered guidance-chain completion notification; "
                                "no S2C response and no lock partner are registered"
                            ),
                            "getOfflineChatData": (
                                "recovered main-city offline-chat request; the "
                                "client own registration has no S2C response"
                            ),
                            "setDrawCardAutoSplit": (
                                "recovered draw-panel auto-split preference; the "
                                "client's own registration has no S2C response"
                            ),
                            "startStamp": (
                                "recovered empty-format stamp notification; the "
                                "client's own registration has no S2C response"
                            ),
                            "startStampScene": (
                                "same empty-format stamp notification on its "
                                "scene-switch wire id 43; no S2C response"
                            ),
                            "updateSceneType": (
                                "recovered scene-switch notification from "
                                "pkgOnlinePlayersDisplayMgr; no S2C response"
                            ),
                            "syncOnlinePlayers": (
                                "recovered scene-switch online-player sync; a "
                                "local session has no other players to send, so "
                                "it is recorded without a player list"
                            ),
                        }
                        role = session_state.get("role")
                        extra: dict[str, Any] = {}
                        if role is not None:
                            assert role.compatibility is not None
                            extra = {
                                "role_id": role.role_id,
                                "profile_id": role.compatibility.profile.profile_id,
                            }
                        self._emit_packet(
                            "tcp_packet_ignored",
                            connection_id,
                            packet,
                            classification="ok_no_response",
                            reason=reasons[packet.spec.name],
                            **extra,
                        )
                        if (
                            packet.spec.name == "startStampScene"
                            and session_state.pop("reconnect_needs_scene", False)
                            and role is not None
                            and role.compatibility is not None
                        ):
                            # TASK-0479: a reconnect never re-sends the login
                            # segment, yet the client that re-enters a scene
                            # waits for the scene record; without it the main
                            # city loading bar stalls.
                            self._send_s2c(
                                connection_id,
                                writer,
                                "returnSceneInfo",
                                role.compatibility.scene_state.protocol_fields(),
                                classification="reconnect_scene_state",
                            )
                        continue
                    if packet.spec.name == "enterGame":
                        # TASK-0318: the equip/god-weapon instance block is sent OUTSIDE the login
                        # sync segment (the 276 abort happened at sync end), and only when the
                        # explicitly enabled dev-play switch is on. Its ids are inferred, so one
                        # device run is the acceptance test.
                        if (dev_play_artifact.DEV_PLAY_PUSH_INSTANCES
                                and isinstance(
                                    session_state.get("dev_play_artifact_overlay"),
                                    ArtifactOverlay)):
                            sent_names = []
                            for _name, _fields in dev_play_artifact.instance_packets():
                                self._send_s2c(
                                    connection_id, writer, _name, _fields,
                                    classification="synthetic_dev_play_instance",
                                )
                                sent_names.append(_name)
                            self.server.event_logger.emit(
                                "tcp_dev_play_instances_sent",
                                connection_id=connection_id,
                                remote_endpoint=(
                                    f"{self.client_address[0]}:{self.client_address[1]}"),
                                packet_order=sent_names,
                                equip_ids=list(dev_play_artifact.EQUIP_INSTANCE_IDS),
                                god_weapon_id=dev_play_artifact.GOD_WEAPON_INSTANCE_ID,
                                place="after_login_sync",
                                replaceable=True,
                                historical=False,
                                role_store_written=False,
                            )
                    if packet.spec.name == "netEnterPveBattle":
                        # C2S 69 'yNNpppppppppa' -- the chapter-map battle start
                        # the client sends when the player taps 挑战.  TASK-0134
                        # recovered the id, the format, the sole sender and the
                        # lock partner; TASK-0135 wired the id into this dispatch
                        # chain (a registered-but-undispatched id fell through to
                        # the `unsupported_protocol` fallback below, which `break`s
                        # the read loop, so the client's own battle start closed
                        # the connection 1.62 s before the tap under test --
                        # RUN-0040 `.autopilot/device_runs/RUN-0040/server.log:708`).
                        #
                        # TASK-0140 answers it.  This is a LOCK-SEND: the client
                        # switched its own event system off and armed its 15 s
                        # timer, and S2C 89 `startPveBattle` is the only unlock.
                        # TASK-0139 proved which reply branch can be played
                        # through (`isUseSfSvr = false`: the client's own local
                        # single-driver simulation, no battle C2S on the socket at
                        # all), and D-129 records the Owner's decision to take it
                        # with every value that has no producer labelled
                        # `synthetic_compatibility` or `request_echo`.
                        #
                        # One request, one reply, on the same connection: the 19
                        # values are assembled by the profile's BattleStartReply
                        # policy (compatibility_profile.BATTLE_START_FIELD_TABLE),
                        # never here.  Nothing else is answered and the client's
                        # own battle start is never echoed as a C2S.
                        role = session_state.get("role")
                        profile = self.server.compatibility_profile
                        extra: dict[str, Any] = {}
                        if role is not None:
                            assert role.compatibility is not None
                            profile = role.compatibility.profile
                            extra = {
                                "role_id": role.role_id,
                                "profile_id": profile.profile_id,
                            }
                        battle_start_policy = profile.battle_start_reply
                        if battle_start_policy is None:
                            # A server built on a v1-v10 profile has no
                            # battle-start reply, and inventing one here would put
                            # values on the wire that no policy declares.  Fail
                            # closed exactly as an unknown protocol does; the
                            # client's own lock timer then disconnects it.
                            self._emit_error(
                                connection_id,
                                "battle_start_reply_unsupported",
                                f"compatibility profile {profile.profile_id} defines "
                                "no chapter-map battle-start reply",
                            )
                            break
                        sf_start_time = self.server.next_battle_start_time()
                        reply_fields = battle_start_policy.reply_fields(
                            request_fields=packet.fields,
                            sf_start_time=sf_start_time,
                        )
                        # TASK-0288: the artifact (66) and uncharted (88) dev-play overlays make
                        # this reply echo the REQUESTED type. The stock reply is the fixed
                        # chapter-map 86/3364 and the client settles with the type it was
                        # answered with, so without this echo an artifact fight settles as a
                        # chapter fight and TASK-0287's 66/88 pairing can never fire (TASK-0287
                        # finding). Only the dev-play overlays are affected; every other
                        # session keeps the frozen byte stream.
                        artifact_overlay_on = isinstance(
                            session_state.get("dev_play_artifact_overlay"), ArtifactOverlay)
                        babel_overlay_on = isinstance(
                            session_state.get("dev_play_babel_overlay"), BabelOverlay)
                        request_pve_type: int | None = None
                        if len(packet.fields) > BATTLE_START_REQUEST_TYPE_INDEX:
                            try:
                                request_pve_type = int(
                                    packet.fields[BATTLE_START_REQUEST_TYPE_INDEX])
                            except (TypeError, ValueError):
                                request_pve_type = None
                        # TASK-0418/0427: 53 (封妖), 71 (三生路), 89 (材料副本), 91 (城市挑战),
                        # 93 (锁妖塔), 97 (房间副本) and 99 (地牢) are
                        # ordinary PVE dungeons whose type must survive the reply -- settling them
                        # as the default chapter type would book the win to the wrong system.
                        echoes_dungeon_type = (
                            request_pve_type is not None
                            and (request_pve_type in (53, 71, 89, 91, 93, 97, 99)
                                 or (request_pve_type == BATTLE_START_GOD_FIGHT_PVE_TYPE
                                     and artifact_overlay_on)
                                 or (request_pve_type == BATTLE_START_UNCHARTED_PVE_TYPE
                                     and babel_overlay_on)))
                        if echoes_dungeon_type:
                            echoed = list(reply_fields)
                            echoed[BATTLE_START_TYPE_POSITION - 1] = request_pve_type
                            # TASK-0336: a type-88 fight must carry the SERVER's identity of
                            # the fight. The client's own C2S sends p8=0 (roomId) and p9=0
                            # (bossId), and echoing them made pkgPveEnterData:85 index a nil
                            # tbEventOptionCfg 735 times (RUN37) because
                            # getCfgBossById(0) is nil. Positions 8..10 of the reply are the
                            # cell index, roomId and boss id the client resolves against its
                            # own uncharted1 / unchartedBoss tables.
                            if request_pve_type == BATTLE_START_GOD_FIGHT_PVE_TYPE:
                                # TASK-0408: field 17 (index 16) is tbParams.s1, the colon-
                                # separated 祝福 feature ids.  It is where the tower relics take
                                # effect: pkgPveEnterData parses it into tbZhufuFeatureSets.
                                relics = self._owned_relic_ids(session_state)
                                if relics:
                                    echoed[16] = dev_play_artifact.relic_zhufu_csv(relics)
                            if request_pve_type == BATTLE_START_UNCHARTED_PVE_TYPE:
                                # TASK-0337: the SLOTS matter. compatibility_profile's
                                # BATTLE_START_FIELD_TABLE puts pveOrPvpType at reply position 5
                                # (index 4) and p1..p9 at positions 8..16 (index 7..15), so the
                                # client's p5/p8/p9 are tuple indices 11/14/15. TASK-0336 wrote
                                # 4/7/8 instead, which overwrote the TYPE with 2 and pushed the
                                # room into p1 -- the client then printed ">>>>> 2" and hit
                                # pkgPveEnterData:941 assert(false) 810 times (RUN38).
                                uncharted_cell, uncharted_room, uncharted_boss = (
                                    dev_play_uncharted.BATTLE_IDENTITY)
                                echoed[BATTLE_START_P5_INDEX] = uncharted_cell
                                echoed[BATTLE_START_P8_INDEX] = uncharted_room
                                echoed[BATTLE_START_P9_INDEX] = uncharted_boss
                            if request_pve_type == 93:
                                # TASK-0428: 锁妖塔. pkgPveEnterData.initLockTowerNewData
                                # reads tbParams.p1 (index 7) as the lockTowerStage id and
                                # p2 (index 8) as the floor, then resolves the row through
                                # pkgCfgLockTowerMgr.getStage(p1). The client only sends the
                                # FLOOR in p1 of its own C2S 69, so the server names a stage
                                # inside that floor's five-id span (contract U-1: the
                                # original pick inside the span is not recovered).
                                try:
                                    tower_floor = int(packet.fields[3])
                                except (IndexError, TypeError, ValueError):
                                    tower_floor = 1
                                echoed[BATTLE_START_P1_INDEX] = self._lock_tower_stage(tower_floor)
                                echoed[BATTLE_START_P2_INDEX] = tower_floor
                                session_state["lock_tower_floor"] = tower_floor
                            if request_pve_type == 53:
                                # TASK-0428: 封妖's fight is layer-based; p1 of the client's
                                # C2S 69 is the layer (pkgFairyWayMgr.fairyWayFight passes
                                # getFairyWayLayerId()). Remember it so a type-53 settlement
                                # can set that layer's pending-reward flag.
                                try:
                                    fairy_layer = int(packet.fields[3])
                                except (IndexError, TypeError, ValueError):
                                    fairy_layer = 1
                                session_state["fairy_way_layer"] = fairy_layer
                                # TASK-0429: materialise lstData[1]/[2] under
                                # S2C_BATTLE (transferType 1) BEFORE the 89 reply,
                                # and tell the client it should read them.
                                self._fairy_way_battle_prepare(
                                    connection_id, writer, session_state)
                                echoed[2] = True
                            # TASK-0337 guard: whatever an overlay writes, the reply must still
                            # carry the type the client asked for. TASK-0336 silently turned 88
                            # into 2 here and the client asserted 810x while the server log
                            # looked perfectly healthy.
                            if echoes_dungeon_type:
                                surviving_type = echoed[BATTLE_START_TYPE_POSITION - 1]
                                if surviving_type != request_pve_type:
                                    self.server.event_logger.emit(
                                        "battle_start_echo_invariant_violated",
                                        connection_id=connection_id,
                                        remote_endpoint=(
                                            f"{self.client_address[0]}:"
                                            f"{self.client_address[1]}"),
                                        request_pve_type=request_pve_type,
                                        reply_type=surviving_type,
                                        action="restored_request_type",
                                        connection_kept_alive=True,
                                    )
                                    echoed[BATTLE_START_TYPE_POSITION - 1] = request_pve_type
                            # TASK-0296: for a type-66 fight position 8 is the STAGE id, not an
                            # echo of the request. pkgPveEnterData:699 does
                            # getStage(p1) -> cfgArtifactDgnStage[dStageId] with no guard, so the
                            # echoed tower id (1) cloned nil and the battle never loaded
                            # (table_ext:236 x422 in run9). Position 18 is the enemy HP table:
                            # an empty string zeroes every slot and pkgBattleDataInc then
                            # instances no monster at all.
                            if request_pve_type == BATTLE_START_GOD_FIGHT_PVE_TYPE:
                                try:
                                    tower = int(packet.fields[3])
                                    layer = int(packet.fields[4])
                                    sn = int(packet.fields[5])
                                except (IndexError, TypeError, ValueError):
                                    tower, layer, sn = 1, 2, 1
                                stage_id = dev_play_artifact.node_stage_id(layer, sn, dev_play_artifact.god_fight_stage_id(tower, layer, sn))
                                echoed[7] = stage_id
                                echoed[8] = 0
                                echoed[9] = 0
                                echoed[17] = dev_play_artifact.god_fight_hp_csv()
                            # TASK-0341: the uncharted branch takes the ENCODED table, not the
                            # type-66 comma CSV. TASK-0340 recovered the encoding:
                            # encoded = (hp*10000 + 25486 + index) * 7, joined as "index_encoded".
                            # 25% (the same winnable setting RUN14 used for the artifact fight):
                            if request_pve_type == BATTLE_START_UNCHARTED_PVE_TYPE:
                                # TASK-0342: a 0% decode means "not instanced" in the client
                                # (x_pkgBattleDataInc:413-419 skips the monster), so this is the
                                # unambiguous probe for whether the table binds to this fight.
                                echoed[17] = dev_play_uncharted.hp_percent_table(
                                    dev_play_uncharted.UNCHARTED_HP_PERCENT)
                            # TASK-0339: REVERTED TASK-0338. The uncharted branch DOES read this
                            # slot, but with a different grammar: pkgBattleStatistics.getHpPercent
                            # Table splits each row on "_" as (index, encoded) where
                            # encoded = (hpPercent + e_dRandomVal4HpPercent + index) * 7. The
                            # type-66 comma form ("1,2500;...") gives no second number there and
                            # aborts the battle with 1287 x pkgBattleStatistics:461 (RUN40). An
                            # empty string restores RUN39's playable loss path until the encoder's
                            # seed-derived offset is recovered.
                            reply_fields = tuple(echoed)
                            self.server.event_logger.emit(
                                "tcp_battle_start_type_echoed",
                                connection_id=connection_id,
                                remote_endpoint=(
                                    f"{self.client_address[0]}:{self.client_address[1]}"),
                                protocol_name="startPveBattle",
                                request_sequence=packet.sequence,
                                request_pve_type=request_pve_type,
                                replied_type_position=BATTLE_START_TYPE_POSITION,
                                stock_reply_type=86,
                                echo_provenance="dev_play_overlay",
                                **extra,
                            )
                        response = self._send_s2c(
                            connection_id,
                            writer,
                            "startPveBattle",
                            reply_fields,
                            decoded_fields={
                                field.symbolic_name: value
                                for field, value in zip(
                                    battle_start_policy.fields,
                                    reply_fields,
                                    strict=True,
                                )
                            },
                            classification="profile_backed_battle_start_reply",
                            record_fields={
                                "lock_partner": packet.spec.name,
                                "lock_pair_origin": "deterministic_recovered",
                                "unlock_only_for": packet.spec.name,
                                "reply_trigger": BATTLE_START_REPLY_TRIGGER,
                                **extra,
                            },
                        )
                        self.server.event_logger.emit(
                            "tcp_battle_start_replied",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            request_protocol_id=packet.spec.protocol_id,
                            request_protocol_name=packet.spec.name,
                            request_sequence=packet.sequence,
                            request_fields=[
                                _text(value) if isinstance(value, bytes) else value
                                for value in packet.fields
                            ],
                            response_protocol_id=response.spec.protocol_id,
                            response_protocol_name=response.spec.name,
                            response_sequence=response.sequence,
                            response_key_before=f"0x{response.key_before:08X}",
                            response_key_after=f"0x{response.key_after:08X}",
                            response_wire_length=len(response.wire),
                            response_wire_sha256=hashlib.sha256(
                                response.wire
                            ).hexdigest(),
                            reply_fields=[
                                _text(value) if isinstance(value, bytes) else value
                                for value in reply_fields
                            ],
                            reply_field_names=list(
                                battle_start_policy.field_names()
                            ),
                            field_origins=battle_start_policy.field_origins(),
                            field_value_origins=battle_start_policy.value_origins(),
                            sf_start_time=sf_start_time,
                            sync_count_reply=reply_fields[
                                BATTLE_START_SYNC_COUNT_POSITION - 1
                            ],
                            sf_start_time_policy=BATTLE_START_SF_START_TIME_POLICY,
                            seed_policy=battle_start_policy.seed_policy,
                            battle_start_policy_version=(
                                battle_start_policy.introduced_in
                            ),
                            self_group_policy=BATTLE_START_SELF_GROUP_POLICY,
                            branch_is_use_sf_svr=reply_fields[1],
                            branch_transfer_prepare_data=reply_fields[2],
                            battle_drive="client_local_single_dvr",
                            battle_c2s_reaches_socket=False,
                            lock_partner=packet.spec.name,
                            lock_pair_origin="deterministic_recovered",
                            unlock_only_for=packet.spec.name,
                            reply_trigger=BATTLE_START_REPLY_TRIGGER,
                            reply_count=1,
                            requests_answered=(
                                session_state.get("battle_start_request_count", 0) + 1
                            ),
                            repeated_request=(
                                session_state.get("battle_start_request_count", 0) > 0
                            ),
                            historical_ordering=(
                                battle_start_policy.historical_ordering
                            ),
                            ordering_origin=(
                                battle_start_policy.ordering_provenance.origin
                            ),
                            parent_profile_id=profile.parent_profile_id,
                            policy_compatibility_version=(
                                battle_start_policy.reply_provenance
                                .compatibility_version
                            ),
                            historical_values_recovered=False,
                            **extra,
                        )
                        session_state["battle_start_request_count"] = (
                            session_state.get("battle_start_request_count", 0) + 1
                        )
                        # TASK-0174: remember the transaction this reply opened.
                        # C2S 67 alone names no chapter or fight; the client's own
                        # request does (`p1` fight, `p2` map index, `p4` chapter),
                        # and a settlement may only be paired with it when both
                        # arrived on THIS connection.  Only a chapter-map battle
                        # (`pveOrPvpType == 86`, the packaged constant) is a
                        # candidate: every other pve type settles in its own
                        # system and has no chapter item to replay.
                        chapter_replay = profile.chapter_fight_replay
                        if chapter_replay is not None and role is not None:
                            captured = self._chapter_battle_start_fields(packet)
                            if captured is not None:
                                captured.update(self._battle_team_snapshot(session_state))
                                session_state["chapter_battle_start"] = {
                                    "role_id": role.role_id,
                                    **captured,
                                }
                            else:
                                session_state.pop("chapter_battle_start", None)
                        # TASK-0238: remember this connection's own locktower
                        # start in an independent key (never the chapter one).
                        # Only a dev-babel-main session whose overlay is applied
                        # and whose profile carries the settlement-push policy
                        # reaches this block, and only when the reply the client
                        # actually received was type 73.  Nothing is persisted:
                        # the key lives in this connection's session state.
                        babel_overlay = session_state.get("dev_play_babel_overlay")
                        if (
                            isinstance(babel_overlay, BabelOverlay)
                            and profile.babel_settlement_push is not None
                        ):
                            babel_captured = self._babel_battle_start_fields(
                                packet, reply_fields, response.sequence
                            )
                            if babel_captured is None:
                                session_state.pop("babel_battle_start", None)
                            else:
                                session_state["babel_battle_start"] = {
                                    "role_id": (
                                        None if role is None else role.role_id
                                    ),
                                    **babel_captured,
                                }
                                self.server.event_logger.emit(
                                    "tcp_babel_battle_start_captured",
                                    connection_id=connection_id,
                                    remote_endpoint=remote,
                                    role_id=(
                                        None if role is None else role.role_id
                                    ),
                                    play_profile_id=DEV_PLAY_BABEL_PROFILE_ID,
                                    profile_id=profile.profile_id,
                                    pve_or_pvp_type=babel_captured[
                                        "pve_or_pvp_type"
                                    ],
                                    floor=babel_captured["floor"],
                                    dFightType=babel_captured["fight_type"],
                                    request_sequence=babel_captured[
                                        "request_sequence"
                                    ],
                                    s2c89_dSyncCount=babel_captured["sync_count"],
                                    s2c89_reply_sequence=babel_captured[
                                        "reply_sequence"
                                    ],
                                    capture_origin="request_echo",
                                    pairing_provenance="deterministic_recovered",
                                    persisted=False,
                                )
                        # TASK-0287: the same "remember it on this connection"
                        # rule for the two dungeon types that settle through
                        # this same C2S 67 but have no state push of their own.
                        # The key is per type, so an artifact start and an
                        # uncharted start on one connection cannot consume each
                        # other; nothing is sent, persisted or rewarded here.
                        dungeon_captured = self._dungeon_battle_start_fields(
                            packet, reply_fields, response.sequence
                        )
                        if dungeon_captured is not None:
                            dungeon_starts = session_state.get(
                                "dungeon_battle_start"
                            )
                            if not isinstance(dungeon_starts, dict):
                                dungeon_starts = {}
                            dungeon_starts[
                                dungeon_captured["pve_or_pvp_type"]
                            ] = {
                                "role_id": (
                                    None if role is None else role.role_id
                                ),
                                **dungeon_captured,
                            }
                            session_state["dungeon_battle_start"] = (
                                dungeon_starts
                            )
                            dungeon_identity = {
                                key: dungeon_captured[key]
                                for key in (
                                    "tower", "layer", "sn", "uncharted_id",
                                    "area", "room",
                                )
                                if key in dungeon_captured
                            }
                            self.server.event_logger.emit(
                                "tcp_dungeon_battle_start_captured",
                                connection_id=connection_id,
                                remote_endpoint=remote,
                                role_id=(
                                    None if role is None else role.role_id
                                ),
                                profile_id=profile.profile_id,
                                pve_or_pvp_type=dungeon_captured[
                                    "pve_or_pvp_type"
                                ],
                                p1=dungeon_captured["p1"],
                                p2=dungeon_captured["p2"],
                                p3=dungeon_captured["p3"],
                                p4=dungeon_captured["p4"],
                                **dungeon_identity,
                                request_sequence=dungeon_captured[
                                    "request_sequence"
                                ],
                                s2c89_dSyncCount=dungeon_captured[
                                    "sync_count"
                                ],
                                s2c89_reply_sequence=dungeon_captured[
                                    "reply_sequence"
                                ],
                                capture_origin="request_echo",
                                pairing_provenance="deterministic_recovered",
                                persisted=False,
                                role_store_written=False,
                                state_changed=False,
                            )
                        continue
                    if packet.spec.name == "battlePveGameOver":
                        # C2S 67 'NNppppppppppppaaaappp' -- the chapter-map battle
                        # END the client sends once it has settled the battle it
                        # played itself.  RUN-0048 is the device observation
                        # (`.autopilot/device_runs/RUN-0048/server.log:439`): 245
                        # bytes whose header decodes to id 67 / sequence 148
                        # (frame-aligned behind the sequence-147 speedHeart) and
                        # whose payload is pveOrPvpType 86 plus the client's own
                        # packed result.  Before TASK-0155 the stub answered this
                        # registered-nowhere id with `malformed_packet` +
                        # `tcp_disconnected` (`:440`), i.e. the player's own battle
                        # end killed the connection.
                        #
                        # This is a LOCK-SEND of the same family as the C2S 69
                        # above: pkgBattleProtoDef row 7 is
                        # `{ "Na", "battlePveGameOverEnd", "battlePveGameOver",
                        # "battlePveGameOver_svrVer" }` and pkgScProtocolMgr's own
                        # registerS2C hands those trailing names to
                        # pkgLockSendMgr.regLockProto, so the client switched its
                        # event system off and armed LOCK_TIME = 15000 ms; S2C 96
                        # `battlePveGameOverEnd` is the only unlock and S2C 97
                        # `battlePveGameOverError` is the (unregistered) error
                        # variant.
                        #
                        # TASK-0155 read the client's ONLY consumer of that answer
                        # out of the shipped bytecode before writing a value: an
                        # exhaustive whole-APK name scan finds
                        # `battlePveGameOverEnd` in exactly two chunks (the
                        # registration table and battle/net/pkgGameOverFromSg), and
                        # PROTO 0.3 of that chunk -- its only handler -- never reads
                        # the first value at all and calls
                        # `pkgGameOverDisplay.showChapterPveGameOverView()` with no
                        # arguments on the chapter branch.  That verdict is why the
                        # two values may be the labelled constants the profile
                        # declares, and why nothing is echoed from the request.
                        #
                        # One request, one reply, on the same connection.  The
                        # values come from the profile's BattleGameOverReply policy
                        # (compatibility_profile.BATTLE_GAME_OVER_FIELD_TABLE),
                        # never from here.
                        role = session_state.get("role")
                        profile = self.server.compatibility_profile
                        extra: dict[str, Any] = {}
                        if role is not None:
                            assert role.compatibility is not None
                            profile = role.compatibility.profile
                            extra = {
                                "role_id": role.role_id,
                                "profile_id": profile.profile_id,
                            }
                        game_over_policy = profile.battle_game_over_reply
                        if game_over_policy is None:
                            # A server built on a v1-v11 profile has no battle-end
                            # reply, and inventing one here would put values on the
                            # wire that no policy declares.  Fail closed exactly as
                            # an unknown protocol does; the client's own lock timer
                            # then disconnects it (D-08, unchanged).
                            self._emit_error(
                                connection_id,
                                "battle_game_over_reply_unsupported",
                                f"compatibility profile {profile.profile_id} defines "
                                "no chapter-map battle-end reply",
                            )
                            break
                        reply_fields = game_over_policy.reply_fields()
                        # TASK-0344: a 秘境 WIN must carry its gain block BEFORE S2C 96.
                        # pkgGameOverDisplay.showUnchartedPveGameOverView drains
                        # TB_GAIN_TYPE.FIGHT (=1) in the same turn it shows the panel, so a block
                        # sent after 96 leaves the panel empty. The artifact tower is not affected
                        # (its 596 block is drained later, on return to the tower).
                        self._grant_uncharted_win_reward(
                            connection_id, writer, session_state, role, packet
                        )
                        # TASK-0428: a 锁妖塔 victory advances the persisted pass floor
                        # before S2C 96, so the panel's next C2S 378 already sees it.
                        self._lock_tower_settlement(session_state, packet)
                        fairy_way_won = self._fairy_way_settlement(session_state, packet)
                        response = self._send_s2c(
                            connection_id,
                            writer,
                            "battlePveGameOverEnd",
                            reply_fields,
                            decoded_fields={
                                field.symbolic_name: value
                                for field, value in zip(
                                    game_over_policy.fields,
                                    reply_fields,
                                    strict=True,
                                )
                            },
                            classification="profile_backed_battle_game_over_reply",
                            record_fields={
                                "lock_partner": packet.spec.name,
                                "lock_pair_origin": "deterministic_recovered",
                                "unlock_only_for": packet.spec.name,
                                "reply_trigger": BATTLE_GAME_OVER_REPLY_TRIGGER,
                                **extra,
                            },
                        )
                        # TASK-0429: the 封妖 panel is reopened by the client's own
                        # warEndByClient and never re-requests 239; without this push its
                        # reward model keeps the pre-fight dRewardLayer=0.
                        if fairy_way_won:
                            self._push_fairy_way(connection_id, writer, session_state)
                        self.server.event_logger.emit(
                            "tcp_battle_game_over_replied",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            request_protocol_id=packet.spec.protocol_id,
                            request_protocol_name=packet.spec.name,
                            request_sequence=packet.sequence,
                            # The request carries the client's own packed battle
                            # result: recorded verbatim, never interpreted and never
                            # answered from (nothing in the reply is derived from
                            # it).  `_text` keeps the event JSON-serialisable
                            # (1a4d715: no raw bytes may reach an event record).
                            request_fields=[
                                _text(value) if isinstance(value, bytes) else value
                                for value in packet.fields
                            ],
                            response_protocol_id=response.spec.protocol_id,
                            response_protocol_name=response.spec.name,
                            response_sequence=response.sequence,
                            response_key_before=f"0x{response.key_before:08X}",
                            response_key_after=f"0x{response.key_after:08X}",
                            response_wire_length=len(response.wire),
                            response_wire_sha256=hashlib.sha256(
                                response.wire
                            ).hexdigest(),
                            reply_fields=[
                                _text(value) if isinstance(value, bytes) else value
                                for value in reply_fields
                            ],
                            reply_field_names=list(
                                game_over_policy.field_names()
                            ),
                            field_origins=game_over_policy.field_origins(),
                            unlock_names=list(game_over_policy.unlock_names),
                            answered_request=game_over_policy.answered_request,
                            lock_partner=packet.spec.name,
                            lock_pair_origin="deterministic_recovered",
                            unlock_only_for=packet.spec.name,
                            reply_trigger=BATTLE_GAME_OVER_REPLY_TRIGGER,
                            reply_count=1,
                            requests_answered=(
                                session_state.get("battle_game_over_request_count", 0)
                                + 1
                            ),
                            repeated_request=(
                                session_state.get("battle_game_over_request_count", 0)
                                > 0
                            ),
                            historical_ordering=(
                                game_over_policy.historical_ordering
                            ),
                            ordering_origin=(
                                game_over_policy.ordering_provenance.origin
                            ),
                            parent_profile_id=profile.parent_profile_id,
                            policy_compatibility_version=(
                                game_over_policy.reply_provenance
                                .compatibility_version
                            ),
                            historical_values_recovered=False,
                            **extra,
                        )
                        self._record_chapter_fight_clear(
                            connection_id,
                            writer,
                            session_state,
                            role,
                            packet,
                            response,
                        )
                        # TASK-0238: the locktower (73) settlement edge.  On a
                        # dev-babel-main session this pairs the C2S 67 with the
                        # C2S 69 that this same connection was answered with
                        # type 73, and pushes the one post-settlement S2C 580.
                        # Every other session returns before touching anything,
                        # so its events and bytes are exactly what they were.
                        self._record_babel_settlement(
                            connection_id,
                            writer,
                            session_state,
                            role,
                            packet,
                            response,
                        )
                        # TASK-0287: pair this connection's artifact (66) /
                        # uncharted (88) settlement with its own C2S 69 start.
                        # No reward, no row, no S2C 580: the event is the whole
                        # outcome.  An unmatched type is recorded and ignored.
                        self._record_dungeon_settlement(
                            connection_id,
                            writer,
                            session_state,
                            role,
                            packet,
                            response,
                        )
                        session_state["battle_game_over_request_count"] = (
                            session_state.get("battle_game_over_request_count", 0) + 1
                        )
                        continue
                    if packet.spec.name == "chapterMapEvent":
                        # C2S 224 `chapterMapEvent` 'NNNNNp' -- the chapter-map
                        # event action the client sends when the player presses
                        # a reward option of a chapter event (here chapter 1's
                        # 「散乱的货物」, option 1 「翻找」).  The client's own
                        # registration makes it a LOCK-SEND whose only unlock is
                        # S2C 398; without that reply the client showed
                        # 「正在努力载入 请稍候...」 and closed its socket after
                        # LOCK_TIME = 15 s (manual session 20261018-move,
                        # server.log:11716/11717/11785).
                        #
                        # The handler validates the one recovered node, answers
                        # once, and records the client's own action as the item
                        # row `isItemEventOver` reads at the next login.  An
                        # unrecognised node/option is refused by name and is
                        # STILL answered with one S2C 398 carrying a non-SUCCESS
                        # EVENT_STATUS_ILLEGAL (D-186): refusing silently left
                        # the client's 15 s lock-send timer to close the socket.
                        self._handle_chapter_map_event(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "inspectOrderValid":
                        # C2S 411 `inspectOrderValid` 'pppp' -- the shop's order
                        # validation before a payment (Issue #26).  A LOCK-SEND
                        # whose only unlock is S2C 745 `inspectOrderResult`; the
                        # handler answers exactly once with the client's own
                        # NUM_RESTRICT failure (a visible alert2, never
                        # pkgPayMgr.payOrder) and creates no order.
                        self._handle_market_inspect_order(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name in ("equipMagic", "replaceMagic"):
                        try:
                            _f = packet.fields
                            _inst = (_f[0].decode("utf-8", "replace")
                                     if isinstance(_f[0], (bytes, bytearray))
                                     else str(_f[0]))
                            _gid = int(_f[1])
                            _equip = bool(_f[2]) if len(_f) > 2 else True
                            _idx = int(_f[3]) if len(_f) > 3 else 1
                        except Exception:
                            _inst, _gid, _equip, _idx = "", 0, True, 1
                        _role = session_state.get("role")
                        if _role is not None and _inst:
                            self._store_team_selection(
                                _role, "fabao", _gid, _idx, str(_inst))
                        self._send_s2c(
                            connection_id, writer, "updateSelectMagic",
                            (int(_gid), str(_inst), int(_idx), bool(_equip)),
                            classification="synthetic_dev_magic_equip")
                        continue
                    if packet.spec.name == "topTowerEnter":
                        try:
                            _map = int(packet.fields[0])
                        except Exception:
                            _map = 1
                        # TASK-0499: pkgTopTowerConst STATE_OPEN=2,
                        # NODE_STATE_INIT=1, ENERGY_SWITCH=1000 (the client
                        # subtracts it from dEnergy), ENERGY_INIT=100.
                        self._send_s2c(connection_id, writer,
                                       "topTowerState", (int(_map), 2),
                                       classification="synthetic_dev_top_state")
                        self._send_s2c(connection_id, writer,
                                       "topTowerMapState",
                                       (int(_map), 2, 0),
                                       classification="synthetic_dev_top_map")
                        self._send_s2c(connection_id, writer,
                                       "topTowerCur", (1000, 1100),
                                       classification="synthetic_dev_top_cur")
                        for _node in dev_play_toptower.MAP1_NODES:
                            self._send_s2c(
                                connection_id, writer, "topTowerEvt",
                                (int(_node), 1, 0, 0, 0),
                                classification="synthetic_dev_top_evt")
                        self._send_s2c(connection_id, writer,
                                       "topTowerPass", (1, 0),
                                       classification="synthetic_dev_top_pass")
                        continue
                    if packet.spec.name == "strengthMagicStart":
                        _raw = packet.fields[0] if packet.fields else ""
                        _target = (_raw.decode("utf-8", "replace")
                                   if isinstance(_raw, (bytes, bytearray))
                                   else str(_raw))
                        session_state["strength_magic_target"] = _target
                        continue
                    if packet.spec.name in ("strengthMagic",
                                            "strengthDogMagic"):
                        continue
                    if packet.spec.name == "strengthMagicEnd":
                        _target = session_state.pop(
                            "strength_magic_target", None)
                        if _target:
                            self._send_s2c(
                                connection_id, writer,
                                "returnStrengthMagicResult",
                                (str(_target), True),
                                classification=(
                                    "synthetic_dev_fabao_strength"),
                            )
                        continue
                    if packet.spec.name in ("equipRune", "replaceRune"):
                        try:
                            _f = packet.fields
                            _gid = int(_f[0])
                            _idx = int(_f[1])
                            _cfg = int(_f[2])
                        except Exception:
                            _gid, _idx, _cfg = 0, 0, 0
                        _role = session_state.get("role")
                        if _role is not None and _cfg:
                            self._store_team_selection(
                                _role, "rune", _gid, _idx, int(_cfg))
                        self._send_s2c(
                            connection_id, writer, "updateSelectRune",
                            (int(_gid), int(_idx), int(_cfg), True),
                            classification="synthetic_dev_rune_equip")
                        continue
                    if packet.spec.name == "addRune":
                        try:
                            _cfg = int(packet.fields[0])
                        except Exception:
                            _cfg = 0
                        self._send_s2c(connection_id, writer, "addRune",
                                       (int(_cfg),),
                                       classification="synthetic_dev_rune_add")
                        continue
                    if packet.spec.name == "dgnEnter":
                        try:
                            _did = int(packet.fields[0])
                        except Exception:
                            _did = 1
                        self._send_s2c(connection_id, writer, "dgnEnter",
                                       (int(_did), 0),
                                       classification="synthetic_dev_dgn_enter")
                        self._send_s2c(connection_id, writer, "dgnState",
                                       (int(_did), 1, 0),
                                       classification="synthetic_dev_dgn_state")
                        continue
                    if packet.spec.name in ("learnTalent", "levelupTalent"):
                        try:
                            _raw = packet.fields[0]
                            _tid = (int(_raw.decode("utf-8", "replace"))
                                    if isinstance(_raw, (bytes, bytearray))
                                    else int(_raw))
                        except Exception:
                            _tid = 0
                        _result = ("returnTalentLearnResult"
                                   if packet.spec.name == "learnTalent"
                                   else "returnTalentLevelupResult")
                        self._send_s2c(connection_id, writer, _result,
                                       (int(_tid), 1),
                                       classification="synthetic_dev_talent_result")
                        continue
                    if packet.spec.name == "unlockTalentSkill":
                        try:
                            _raw = packet.fields[0]
                            _tid = (int(_raw.decode("utf-8", "replace"))
                                    if isinstance(_raw, (bytes, bytearray))
                                    else int(_raw))
                            _raw2 = (packet.fields[1]
                                     if len(packet.fields) > 1 else 0)
                            _idx = (int(_raw2.decode("utf-8", "replace"))
                                    if isinstance(_raw2, (bytes, bytearray))
                                    else int(_raw2))
                        except Exception:
                            _tid, _idx = 0, 0
                        self._send_s2c(connection_id, writer,
                                       "returnOneTalentSkill",
                                       (int(_tid), int(_idx)),
                                       classification="synthetic_dev_talent_skill")
                        continue
                    if packet.spec.name in ("switchTalentIndex",
                                            "switchTalentIndex2"):
                        self._send_s2c(connection_id, writer,
                                       "returnTalentSwitchEnd", (),
                                       classification="synthetic_dev_talent_switch")
                        continue
                    if packet.spec.name == "sendToEquipFightDataUpate":
                        # C2S 653 `sendToEquipFightDataUpate` '' -- the
                        # equip-fight panel's data request (TASK-0259).
                        # `pkgEquipFightPanel.show()` sends it BEFORE
                        # `c2s.artifactDgnOpenUI`, so leaving it unregistered
                        # killed the strict connection one frame before the
                        # 神器副本 entry request (TASK-0258 §F.2).  The
                        # client's own registration has no lock partner and no
                        # recovered producer exists for S2C 1322
                        # `returnEquipFightDataUpdate`: record and keep the
                        # connection, never the `unsupported_protocol` break
                        # below (D-127).
                        self._handle_equip_fight_data_update(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "artifactDgnOpenUI":
                        # C2S 744 `artifactDgnOpenUI` 'N' -- the 神器副本 entry
                        # request (TASK-0258).  `equip_fight/pkgEquipFightPanel
                        # .show()` sends it with no business argument; the
                        # registration says one N and stays the wire authority
                        # (TASK-0257 U-1, never captured on a device).
                        #
                        # ONE EDGE: the handler records every request, and only
                        # a session whose explicit dev-play artifact overlay was
                        # applied at login is answered with S2C 1526 then
                        # S2C 1527 (the entry page's two data packets).  Without
                        # the overlay it records and keeps the connection --
                        # never the `unsupported_protocol` break below (D-127).
                        self._handle_artifact_dgn_open_ui(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name in ("artifactDgnSetTeam",
                                            "artifactDgnSetRace"):
                        # C2S 745/746 -- the team/race commit requests of the tower entry
                        # (TASK-0270). Under the dev-play overlay they are answered with the
                        # team echo (1538) and then 1529(code 0), which is the only packet
                        # that opens the tower view (TASK-0264).
                        # TASK-0378: C2S 745 carries the team the player actually
                        # configured, as a CSV of instance ids. Discarding it is why a new
                        # team never reached the fight (live 21:45: 101,artifact-...4525-001,...).
                        self._remember_artifact_team(connection_id, writer, packet, session_state)
                        self._handle_artifact_dgn_team_race(
                            connection_id, writer, packet, session_state
                        )
                        try:
                            self._push_artifact_shop(
                                connection_id, writer, session_state
                            )
                        except Exception:
                            pass
                        continue
                    if packet.spec.name == "unchartedEnter":
                        # C2S 244 -- the 秘境 first-clear request (TASK-0272). LOCK-SEND: it
                        # is always answered, with 434/439/440../441/458 under the dev-play
                        # babel overlay and with 458(status=0) otherwise.
                        self._handle_uncharted_enter(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "unchartedBossSelected":
                        # TASK-0333: C2S 255 unchartedBossSelected "NNNNNNp" -- RUN35 caught it
                        # the moment 选择 was tapped, and an unregistered id closes the socket.
                        # Store the chosen index and confirm it with 470 + the 471 echo.
                        selected_fields = [int(v) for v in packet.fields[:6]]
                        # TASK-0335: the C2S carries TWO indices. fields[4] is the event-grid
                        # cell, fields[5] is the boss card index (1..3) -- the exit panel compares
                        # its i2 against dSelected, so the answer must carry the CARD index.
                        # TASK-0335 also fixes the echo status: the client's 471 handler only
                        # revalidates when dStatus == 1 (RUN36 saw 0 and did nothing).
                        cell_index = selected_fields[4] if len(selected_fields) > 4 else 0
                        card_index = selected_fields[5] if len(selected_fields) > 5 else cell_index
                        selected_index = card_index
                        session_state["uncharted_boss_selected"] = card_index
                        session_state["uncharted_boss_cell"] = cell_index
                        selected_reply = self._send_s2c(
                            connection_id, writer, "returnUnchartedBossSelected",
                            (selected_index,),
                            decoded_fields={"dSelected": selected_index},
                            classification="synthetic_uncharted_boss_selected",
                        )
                        echo_fields = (tuple(selected_fields[:5])
                                       + (1, card_index))  # dStatus=1, dIndex=card
                        echo_reply = self._send_s2c(
                            connection_id, writer, "unchartedBossSelected", echo_fields,
                            classification="synthetic_uncharted_boss_selected_echo",
                        )
                        self.server.event_logger.emit(
                            "tcp_uncharted_boss_selected",
                            connection_id=connection_id,
                            remote_endpoint=(
                                f"{self.client_address[0]}:{self.client_address[1]}"),
                            request_fields=[int(v) for v in packet.fields[:6]],
                            selected_index=selected_index,
                            packet_order=[selected_reply.spec.name, echo_reply.spec.name],
                            echo_fields=list(echo_fields),
                            value_provenance="request_echo",
                            unknown_ids=["U-255"],
                            replaceable=True,
                            historical=False,
                            state_changed=False,
                            connection_kept_alive=True,
                        )
                        continue
                    if packet.spec.name == "artifactDgnDoSelYiwu":
                        # C2S 748: the player picked a relic.  S2C 1535(0, id) opens the
                        # "got relic" view and S2C 1533 code 2 records it in the list.
                        # TASK-0409: C2S 748 carries the 1-based INDEX of the offer row the
                        # player tapped, not the relic id (contract task_0403).  The server has
                        # to remember the 1534 order it sent and resolve the index against it.
                        yiwu = self._resolve_relic_pick(session_state, packet)
                        self._send_s2c(connection_id, writer, "artifactDgnDoSelYiwu",
                                       (0, int(yiwu)),
                                       classification="synthetic_artifact_dgn_yiwu")
                        if int(yiwu) > 0:
                            self._send_s2c(connection_id, writer, "artifactDgnYiwu",
                                           (2, int(yiwu)),
                                           classification="synthetic_artifact_dgn_yiwu")
                        continue
                    if packet.spec.name == "artifactDgnUseGoods":
                        # C2S 753 is a LOCK-SEND: answer 1544 so the client never waits.
                        self._send_s2c(connection_id, writer, "artifactDgnUseGoods",
                                       (0, 0, 0, 0),
                                       classification="synthetic_artifact_dgn_use_goods")
                        continue
                    if packet.spec.name == "roomDungeonEnter":
                        self._room_dungeon_enter(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "roomDungeonEventDone":
                        self._room_dungeon_event_done(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "roomDungeonFinish":
                        self._room_dungeon_finish(
                            connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "startFairyWay":
                        self._fairy_way_start(connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "fairyWayOpenMainUI":
                        self._fairy_way_open_main(connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "dispatchReward":
                        self._fairy_way_reward(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "sendRequestPrisonFightData":
                        self._prison_fight_enter(
                            connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "sendGetPrisonFightData":
                        self._prison_fight_refresh(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "sendGetPrisonFightShareReward":
                        self._send_s2c(connection_id, writer,
                                       "returnPrisonFightFriendShareStart", (),
                                       classification="synthetic_prison_fight_share")
                        continue
                    if packet.spec.name == "dgnEnter":
                        self._dgn_food_enter(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "dgnFoodStartFight":
                        self._dgn_food_start_fight(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "enterLockTower":
                        self._lock_tower_enter(connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "getTowerInfo":
                        self._push_lock_tower_info(connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "getLockTowerDefaultCard":
                        self._lock_tower_default_cards(connection_id, writer)
                        continue
                    if packet.spec.name == "getIndexReward":
                        self._city_challenge_claim(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "resetCityChallengeFailTime":
                        # The main-city button sends this and then opens the view, which
                        # fills ONLY from a server push -- so push it here.
                        self._push_city_challenge(connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "challengeBossActInfo":
                        self._challenge_boss_act_info(connection_id, writer)
                        continue
                    if packet.spec.name == "challengeBossGetInfo":
                        self._challenge_boss_data(connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "challengeBossGetReward":
                        self._challenge_boss_claim(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "challengeBossReset":
                        self._send_s2c(connection_id, writer, "returnChallengeBossReset",
                                       (0,),
                                       classification="synthetic_challenge_boss_reset")
                        continue
                    if packet.spec.name == "challengeBossGetRank":
                        self._send_s2c(connection_id, writer,
                                       "returnChallengeBossRankStart", ("",),
                                       classification="synthetic_challenge_boss_rank")
                        self._send_s2c(connection_id, writer,
                                       "returnChallengeBossRankEnd", (),
                                       classification="synthetic_challenge_boss_rank")
                        continue
                    if packet.spec.name == "sendToEquipFightDataUpate":
                        # TASK-0489: the 昆仑 装备副本 panel requests its data
                        # with an empty C2S 653 and keeps a spinner until the
                        # empty S2C 1322 ack arrives.
                        self._send_s2c(
                            connection_id, writer,
                            "returnEquipFightDataUpdate", (),
                            classification="synthetic_equip_fight_update",
                        )
                        continue
                    if packet.spec.name == "sendToEquipFightOneKey":
                        self._send_s2c(
                            connection_id, writer,
                            "returnEquipFightOneKey", (0,),
                            classification="synthetic_equip_fight_one_key",
                        )
                        continue
                    if packet.spec.name == "requestAllSkinsDataFromCg":
                        self._handle_skin_request(
                            connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "changeCareerSkinFromCg":
                        self._handle_skin_change(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "refineCard":
                        self._handle_refine_card(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "refineSave":
                        self._handle_refine_save(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "refineQuit":
                        session_state.pop("refine_pending", None)
                        continue
                    if packet.spec.name == "replaceCardSkill":
                        self._handle_refine_replace(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "artifactDgnShopBuy":
                        self._handle_artifact_shop_buy(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "artifactDgnShopRefresh":
                        # 757 is also a LOCK-SEND: answer 1549 to release it,
                        # then reroll the shelves for this role.
                        self._handle_artifact_shop_refresh(
                            connection_id, writer, session_state)
                        continue
                    if packet.spec.name == "artifactDgnGoNext":
                        # TASK-0352: the "next layer" button; answering it is what keeps the
                        # session alive where an unregistered 749 used to close the socket.
                        self._handle_artifact_dgn_go_next(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "artifactDgnSelCard":
                        self._handle_artifact_dgn_sel_card(
                            connection_id, writer, packet, session_state)
                        continue
                    if packet.spec.name == "artifactDgnDoSelCard":
                        # TASK-0370: the pick now really lands -- the chosen card is written to
                        # the archive, pushed as S2C 32 addCard, and the node is marked cleared
                        # so the tower moves on (live 21:2x: 获取妖灵 did nothing at all).
                        self._handle_artifact_dgn_do_sel_card(
                            connection_id, writer, packet, session_state)
                        continue
                    if False:
                        # TASK-0364: the pick result. Code 0 closes the panel with 您获得了妖灵.
                        self._send_s2c(
                            connection_id, writer, "artifactDgnDoSelCard", (0, 0),
                            decoded_fields={"dCode": 0},
                            classification="synthetic_artifact_dgn_card_pick",
                        )
                        session_state["artifact_card_picked"] = True
                        continue
                    if packet.spec.name == "syncUnchartedBuildingInfo":
                        # C2S 260 -- no S2C partner; record and keep the connection.
                        self._handle_uncharted_building_sync(
                            connection_id, packet, session_state
                        )
                        continue
                    if packet.spec.name == "babelFight":
                        # C2S 319 -- the tower skip/fight request (TASK-0273); answered with
                        # S2C 579 and, under the dev-play overlay, a session-scoped 580.
                        self._handle_babel_fight(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "artifactDgnDoEvt":
                        # C2S 747 -- the team-info confirm (RUN9: C2S 69 does NOT come
                        # straight from the button; doFight sends 747 first). Answered
                        # with S2C 1539 (layer, sn), whose client handler sends C2S 69.
                        self._handle_artifact_dgn_do_evt(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "artifactDgnEnemyTeamInfo":
                        # C2S 752 -- the tower node tap (TASK-0278). Not a lock-send; under
                        # the overlay it is answered with S2C 1543 team="default".
                        self._handle_artifact_dgn_enemy_team_info(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "chapterMapTask":
                        # C2S 227 `chapterMapTask` 'NNNNp' -- the chapter task
                        # family (TASK-0240): a task NPC's 「领取」 (TASK_RECEIVE)
                        # and every manual/automatic submission (TASK_COMPLETE).
                        # The client's own registration makes it a LOCK-SEND
                        # whose only unlock is S2C 397, and the reply's status
                        # is what tells the client whether the task is now
                        # received (2) or completed (1).  The handler records the
                        # client's own action and never grants its reward.
                        self._handle_chapter_map_task(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "chapterAcceptBox":
                        # C2S 222 `chapterAcceptBox` 'ppp' -- the client's
                        # chapter milestone chest (TASK-0202).  It is a
                        # LOCK-SEND whose only unlock is S2C 384; the handler
                        # validates the chest against the shipped
                        # guankaChapter/guankaReward rows and the server's own
                        # progress records, grants the dog food in ONE
                        # transaction, and answers exactly once.
                        self._handle_chapter_accept_box(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "dailyTaskOpen":
                        # TASK-0448: open the daily-task panel with the shipped
                        # tasks at DOO/0 progress; claiming stays unimplemented.
                        self._send_s2c(
                            connection_id, writer, "dailyTaskOpen", (),
                            classification="daily_task_snapshot",
                        )
                        self._send_s2c(
                            connection_id, writer, "dailyTaskClean", (),
                            classification="daily_task_snapshot",
                        )
                        for task_id in DAILY_TASK_IDS:
                            self._send_s2c(
                                connection_id, writer, "dailyTaskInfo",
                                (int(task_id), 1, 0),
                                classification="daily_task_snapshot",
                            )
                        self._send_s2c(
                            connection_id, writer, "dailyTaskRed", (0,),
                            classification="daily_task_snapshot",
                        )
                        continue
                    if packet.spec.name in {"openBounty", "refreshBountyTask"}:
                        # TASK-0447: no recovered bounty task generator, so the
                        # board opens with an explicit empty index (real empty
                        # state, not invented tasks).
                        now = int(time.time())
                        self._send_s2c(
                            connection_id, writer, "returnBountyTaskIndexStart",
                            (now, 0), classification="bounty_empty_index",
                        )
                        self._send_s2c(
                            connection_id, writer, "returnBountyTaskIndexEnd", (),
                            classification="bounty_empty_index",
                        )
                        continue
                    if packet.spec.name in {"openShop", "refreshShop", "buyGoods"}:
                        self._handle_shop_packet(
                            connection_id, writer, session_state, packet
                        )
                        continue
                    if packet.spec.name == "finishStory":
                        # C2S 233 finishStory 'N' (TASK-0035, extended by
                        # TASK-0073).  Deterministic support of an
                        # already-recovered transport contract (D-21 part 6),
                        # not new synthetic state: the id, the single uint32
                        # storyId field, the sole send site
                        # (pkgStoryMgr.finishStory) and the absence of a lock
                        # partner are all asserted by analyze_story_family.py /
                        # analyze_post283_scene_init.py.  The client writes its
                        # own local STATE_FINISH before it sends, so nothing is
                        # answered -- no S2C413 "End", no S2C414 status row, no
                        # invented timestamp.
                        #
                        # TASK-0073: a lone `C2S233(X)` is still NOT a
                        # completion -- it is the client *opening* X's dialogue
                        # (D-53 §1).  What this session now also remembers is the
                        # ordered pair: when the story a proven predecessor
                        # chains into arrives next, the predecessor's completion
                        # is a fact the client itself asserted, and it is stored
                        # for the role this session is bound to.  Every other
                        # order, every repeat and every unbound session stores
                        # nothing, and the event says which.
                        story_id = int(packet.fields[0])
                        previous_evidence = session_state.get("story_evidence")
                        role = session_state.get("role")
                        # TASK-0438: persist the finished story id so the login
                        # replay can mark it STATE_FINISH and the city cutscene
                        # does not restart on every entry.
                        if (
                            role is not None
                            and session_state.get("account_key") is not None
                            and story_id > 0
                        ):
                            _story_key = f"story_done:{int(role.role_id)}"
                            try:
                                _raw = self.server.role_store.get_meta(_story_key)
                                _ids = {
                                    int(v) for v in json.loads(_raw)
                                } if _raw else set()
                            except Exception:
                                _ids = set()
                            _ids.add(story_id)
                            try:
                                self.server.role_store.set_meta(
                                    _story_key,
                                    json.dumps(sorted(_ids)),
                                )
                            except Exception:
                                pass
                        session_state["story_evidence"] = {
                            "story_id": story_id,
                            "sequence": packet.sequence,
                            # The arm belongs to the role this session is bound to
                            # now; `_story_completion_evidence` refuses a pair that
                            # straddles a later binding change.
                            "role_id": (
                                None if role is None else role.role_id
                            ),
                        }
                        extra = {}
                        if role is not None:
                            assert role.compatibility is not None
                            extra = {
                                "role_id": role.role_id,
                                "profile_id": role.compatibility.profile.profile_id,
                            }
                        completion = self._story_completion_evidence(
                            connection_id,
                            session_state,
                            story_id=story_id,
                            previous_evidence=previous_evidence,
                            successor_sequence=packet.sequence,
                        )
                        self.server.event_logger.emit(
                            "tcp_story_finish_recorded",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            protocol_id=packet.spec.protocol_id,
                            protocol_name=packet.spec.name,
                            sequence=packet.sequence,
                            key_before=f"0x{packet.key_before:08X}",
                            key_after=f"0x{packet.key_after:08X}",
                            story_id=story_id,
                            classification="ok_no_response",
                            no_response_reason=(
                                "recovered story-completion notification; the client "
                                "writes its own local STATE_FINISH before sending and "
                                "the registration contains no S2C response"
                            ),
                            response_sent=False,
                            lock_partner=None,
                            historical_persistence_recovered=False,
                            **completion,
                            **extra,
                        )
                        continue
                    if packet.spec.name == "receiveClientReload":
                        # Compatibility policy (TASK-0015): the client's S2C 13
                        # handler unconditionally sends this lock-send request
                        # (registerS2C("", "initClientReloadEnd",
                        # "receiveClientReload") -- S2C 1487 is its only unlock,
                        # and an unanswered send makes the client disable its
                        # event system and shut its own socket down after
                        # LOCK_TIME = 15000 ms).  The historical reload script
                        # payloads are unknown, so the answer is the minimal
                        # empty completion: exactly one S2C 1487 and nothing
                        # else -- no 1485/1486/1488, no Lua code.  This is a
                        # replaceable synthetic_compatibility policy, not
                        # recovered historical server behaviour.
                        requested_reload_index = int(packet.fields[0])
                        response = self._send_s2c(
                            connection_id,
                            writer,
                            "initClientReloadEnd",
                            (),
                            classification="compatibility_empty_reload_completion",
                        )
                        role = session_state.get("role")
                        extra = {}
                        if role is not None:
                            assert role.compatibility is not None
                            extra = {
                                "role_id": role.role_id,
                                "profile_id": role.compatibility.profile.profile_id,
                            }
                        self.server.event_logger.emit(
                            "tcp_client_reload_completion_sent",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            request_protocol_id=packet.spec.protocol_id,
                            request_protocol_name=packet.spec.name,
                            request_sequence=packet.sequence,
                            requested_reload_index=requested_reload_index,
                            response_protocol_id=response.spec.protocol_id,
                            response_protocol_name=response.spec.name,
                            response_sequence=response.sequence,
                            response_key_before=f"0x{response.key_before:08X}",
                            response_key_after=f"0x{response.key_after:08X}",
                            response_wire_length=len(response.wire),
                            response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
                            reload_policy="minimal_empty_reload_completion",
                            reload_policy_origin="synthetic_compatibility",
                            historical_reload_script_recovered=False,
                            reload_start_sent=False,
                            reload_info_sent=False,
                            add_reload_sent=False,
                            **extra,
                        )
                        # TASK-0428: prime 封妖's panel state.  pkgFairyWayMgr.openFairyWay
                        # reads le_tbFairyWayInfo.dLayerId before any C2S exists, and the
                        # table starts as {} on a fresh client; without S2C 239 the click
                        # dies on "attempt to compare nil with number" and sends nothing.
                        self._fairy_way_prime(connection_id, writer, session_state)
                        continue
                    if packet.spec.name in {
                        "splitCardStart",
                        "splitCard",
                        "splitCardEnd",
                    }:
                        # C2S 218/219/220: the bag's own 分解 chain.  The lock
                        # partner is S2C 376; one result is sent on every path.
                        self._handle_card_split_packet(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name in {
                        "strengthCardStart",
                        "strengthCard",
                        "strengthDogFoodCard",
                        "strengthCardEnd",
                    }:
                        # C2S 14-17: the bag's own 强化 chain.  C2S 17 is a
                        # lock-send whose partner is S2C 70; one result is sent
                        # on every path, so the client's 15 s lock is released.
                        self._handle_card_strength_packet(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name in {
                        "advanceCardStart",
                        "advanceCard",
                        "advanceAdvCard",
                        "advanceCardEnd",
                    }:
                        # C2S 18-21: the bag's own card-advance chain.  C2S 21 is
                        # a lock-send whose partner is S2C 71; exactly one result
                        # is sent on every path, so the client's lock is released.
                        self._handle_card_advance_packet(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "receiveNpcTask":
                        # TASK-0436: persist the accepted NPC task so the city
                        # dialogs do not restart on every login.
                        self._handle_npc_task_receive(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name in {
                        "activeSoulTalent",
                        "selectSoulSkill",
                        "changeTalentSoulShowMode",
                    }:
                        # TASK-0434: the 元魂珠/天赋 lock-sends.  Exactly one
                        # partner packet is sent on every path so the client's
                        # lock is released.
                        self._handle_soul_talent_packet(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    if packet.spec.name == "updateTeam":
                        # C2S 6: the client's own team editor save.  Its lock
                        # partner is S2C 40/41, so exactly one of them is sent
                        # on every path (see _handle_team_update).
                        self._handle_team_update(
                            connection_id,
                            writer,
                            remote,
                            session_state,
                            packet,
                        )
                        continue
                    if packet.spec.name == "changeTeamGroup":
                        # C2S 5: the client's own preset switch.  S2C 35 is the
                        # lock-partner answer; a stored preset is replayed as
                        # S2C 40 immediately after it.
                        self._handle_change_team_group(
                            connection_id,
                            writer,
                            remote,
                            session_state,
                            packet,
                        )
                        continue
                    if packet.spec.name == "bagSortState":
                        # C2S 512 bagSortState 'NN' (TASK-0084).  Deterministic
                        # support of an already-recovered transport contract
                        # (D-21 part 6), not new synthetic state: the id, the two
                        # uint32 fields, the five writer slots and the absence of
                        # a lock partner are all asserted by
                        # analyze_bag_family.py / analyze_post283_scene_init.py.
                        # The only sender, pkgBagUtil.sendBagSort, is called from
                        # setBagSortCard/Piece/Fabao/setBagCardSkill/
                        # setGuildBossAnger -- and each of them writes the
                        # client-local `le_tbBagSort[dOps]` *before* it sends, so
                        # nothing is answered: no echo, no S2C 1048, no invented
                        # historical setting time.  What the packet does leave
                        # behind is the player's own setting, stored for the role
                        # this session is bound to and replayed at the next login
                        # by `_send_bag_sort_rows`.
                        sort_ops = int(packet.fields[0])
                        sort_value = int(packet.fields[1])
                        role = session_state.get("role")
                        extra = {}
                        if role is not None:
                            assert role.compatibility is not None
                            extra = {
                                "role_id": role.role_id,
                                "profile_id": role.compatibility.profile.profile_id,
                            }
                        sort_evidence = self._bag_sort_evidence(
                            connection_id,
                            session_state,
                            ops=sort_ops,
                            sort=sort_value,
                            packet_sequence=packet.sequence,
                        )
                        self.server.event_logger.emit(
                            "tcp_bag_sort_state_recorded",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            protocol_id=packet.spec.protocol_id,
                            protocol_name=packet.spec.name,
                            sequence=packet.sequence,
                            key_before=f"0x{packet.key_before:08X}",
                            key_after=f"0x{packet.key_after:08X}",
                            classification="ok_no_response",
                            no_response_reason=(
                                "recovered bag sort notification; the client writes "
                                "its own local le_tbBagSort value before sending and "
                                "the registration names no lock partner"
                            ),
                            response_sent=False,
                            lock_partner=None,
                            historical_setting_time_recovered=False,
                            **sort_evidence,
                            **extra,
                        )
                        continue
                    if packet.spec.name == "requestMonthSignData":
                        # C2S 725 requestMonthSignData '' (TASK-0094).  Recovered
                        # contract, not reconstructed behaviour: the id and the
                        # empty format, the sole sender
                        # (pkgMonthSignModel.sendToGetMonthSignData), the
                        # main-city timer that reaches it and the lock partner
                        # that makes it a LOCK-SEND are all asserted by
                        # max_decoder/analyze_month_sign_family.py from the
                        # recovered sources.
                        #
                        # One request, one answer, on the same connection: the
                        # recovered S2C1500 handler takes no field and its whole
                        # body is a single pkgEventMgr.dispatch, so the empty
                        # body is the client's own contract and not a placeholder
                        # for data.
                        #
                        # TASK-0246 (profile v23): the answer is now the pair
                        # S2C 1499 `returnMonthSignData` -> S2C 1500.  S2C 1499 is
                        # the ONLY writer of `pkgMonthSignModel.le_nextRefreshTime`
                        # (its handler calls setMonthSignData), and the 1000 ms
                        # repeating timer sends C2S 725 only while that value is
                        # behind the server clock -- so the ack alone left the
                        # client re-sending the lock-send every second (RUN-0077).
                        # The packet carries the client's own EMPTY container plus
                        # a nextRefreshTime on the next CN natural-day boundary:
                        # no sign row, extension count or reward is invented
                        # (D-21).  1499 goes first because the 1500 handler only
                        # dispatches MONTH_SIGN_DATA_UPDATED, whose listeners read
                        # what 1499 has just written.  Profiles without the policy
                        # (v1..v22, e.g. the opt-in v20 dev-babel session) keep
                        # sending exactly the single empty ack they sent before.
                        role = session_state.get("role")
                        profile = self.server.compatibility_profile
                        extra = {}
                        if role is not None:
                            assert role.compatibility is not None
                            profile = role.compatibility.profile
                            extra = {"role_id": role.role_id}
                        extra["profile_id"] = profile.profile_id
                        state_policy = profile.month_sign_state
                        if state_policy is None:
                            # TASK-0463: the v20 dev-babel session has no policy,
                            # so the client re-sent C2S 725 every second (a
                            # lock-send that disables the UI while armed).  The
                            # v23 policy is the same client contract; use it.
                            state_policy = get_profile_v23().month_sign_state
                        state_response = None
                        if state_policy is not None:
                            now_unix = self.server.now_unix()
                            state_fields = state_policy.fields(now_unix=now_unix)
                            state_response = self._send_s2c(
                                connection_id,
                                writer,
                                state_policy.name,
                                state_fields,
                                classification="synthetic_month_sign_state",
                            )
                            (
                                ud_ext_sign,
                                last_ext_sign_time,
                                next_refresh_time,
                                str_month_sign,
                                cur_year,
                                cur_month,
                                cur_day,
                            ) = state_fields
                            self.server.event_logger.emit(
                                "tcp_month_sign_state_sent",
                                connection_id=connection_id,
                                remote_endpoint=remote,
                                protocol_id=state_response.spec.protocol_id,
                                protocol_name=state_response.spec.name,
                                wire_format=state_policy.wire_format,
                                position=state_policy.position,
                                trigger=packet.spec.name,
                                request_sequence=packet.sequence,
                                response_sequence=state_response.sequence,
                                response_key_before=(
                                    f"0x{state_response.key_before:08X}"
                                ),
                                response_key_after=(
                                    f"0x{state_response.key_after:08X}"
                                ),
                                response_wire_length=len(state_response.wire),
                                response_wire_sha256=hashlib.sha256(
                                    state_response.wire
                                ).hexdigest(),
                                server_time_unix=now_unix,
                                ud_ext_sign=ud_ext_sign,
                                last_ext_sign_time=last_ext_sign_time,
                                next_refresh_time=next_refresh_time,
                                str_month_sign=str_month_sign,
                                cur_year=cur_year,
                                cur_month=cur_month,
                                cur_day=cur_day,
                                value_policy=state_policy.value_policy,
                                value_provenance=state_policy.value_provenance.origin,
                                ordering_origin=(
                                    state_policy.ordering_provenance.origin
                                ),
                                historical_ordering=(
                                    state_policy.historical_ordering
                                ),
                                historical_values_recovered=(
                                    state_policy.historical_values_recovered
                                ),
                                replaceable=state_policy.replaceable,
                                historical=state_policy.historical,
                                state_changed=False,
                                policy_compatibility_version=(
                                    state_policy.introduced_in
                                ),
                                **extra,
                            )
                        response = self._send_s2c(
                            connection_id,
                            writer,
                            "responseMonthSignData",
                            (),
                            classification="recovered_lock_pair_empty_ack",
                        )
                        self.server.event_logger.emit(
                            "tcp_month_sign_data_ack_sent",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            request_protocol_id=packet.spec.protocol_id,
                            request_protocol_name=packet.spec.name,
                            request_sequence=packet.sequence,
                            repeated_request=(
                                session_state.get("month_sign_request_count", 0) > 0
                            ),
                            response_protocol_id=response.spec.protocol_id,
                            response_protocol_name=response.spec.name,
                            response_sequence=response.sequence,
                            response_key_before=f"0x{response.key_before:08X}",
                            response_key_after=f"0x{response.key_after:08X}",
                            response_wire_length=len(response.wire),
                            response_wire_sha256=hashlib.sha256(response.wire).hexdigest(),
                            response_count=1,
                            state_packet_sent=state_response is not None,
                            state_protocol_id=(
                                state_response.spec.protocol_id
                                if state_response is not None
                                else None
                            ),
                            state_sequence=(
                                state_response.sequence
                                if state_response is not None
                                else None
                            ),
                            requests_answered=(
                                session_state.get("month_sign_request_count", 0) + 1
                            ),
                            lock_partner=packet.spec.name,
                            lock_pair_origin="deterministic_recovered",
                            unlock_only_for="requestMonthSignData",
                            month_sign_rows_sent=0,
                            month_sign_history_recovered=False,
                            **extra,
                        )
                        session_state["month_sign_request_count"] = (
                            session_state.get("month_sign_request_count", 0) + 1
                        )
                        continue
                    if packet.spec.name == "enterGame":
                        role = session_state.get("role")
                        if role is None or session_state.get("sync_phase") != "complete":
                            self._emit_error(
                                connection_id,
                                "unexpected_enter_game",
                                "enterGame requires a completed compatibility login sync",
                            )
                            break
                        is_support_face_tracking = bool(packet.fields[0])
                        response = self._send_s2c(
                            connection_id,
                            writer,
                            "enterGameSuccess",
                            (),
                            classification="request_triggered_lock_ack",
                        )
                        self._emit_compatibility_packet(
                            "tcp_enter_game_ack_sent",
                            connection_id,
                            response,
                            role,
                            request_protocol_id=packet.spec.protocol_id,
                            is_support_face_tracking=is_support_face_tracking,
                            origin="deterministic_recovered",
                            evidence_class="exact recovered request/ACK contract",
                        )
                        continue
                    if packet.spec.name == "getSignReward":
                        role = session_state.get("role")
                        if role is None or session_state.get("sync_phase") != "complete":
                            self._emit_error(
                                connection_id,
                                "unexpected_sign_query",
                                "getSignReward requires a completed compatibility login sync",
                            )
                            break
                        assert role.compatibility is not None
                        compatibility = role.compatibility
                        catalogs = compatibility.profile.sign_catalogs
                        sign_fields = compatibility.sign_state.protocol_fields(catalogs)
                        response = self._send_s2c(
                            connection_id,
                            writer,
                            "returnSignInReward",
                            sign_fields,
                            decoded_fields={
                                "is_new_month": compatibility.sign_state.is_new_month,
                                "sign_count": compatibility.sign_state.sign_count,
                                "make_up_time": compatibility.sign_state.make_up_time,
                                "role_make_up_time": compatibility.sign_state.role_make_up_time,
                                "is_today_signed": compatibility.sign_state.is_today_signed,
                                "sum_claimed": compatibility.sign_state.sum_claimed,
                                "seven_claimed": compatibility.sign_state.seven_claimed,
                                "seven_day": compatibility.sign_state.seven_day,
                                "catalog_hashes": catalog_hashes(catalogs),
                            },
                            classification="profile_backed_sign_query_response",
                        )
                        self._emit_compatibility_packet(
                            "tcp_sign_state_sent",
                            connection_id,
                            response,
                            role,
                            request_protocol_id=packet.spec.protocol_id,
                            catalog_origin={
                                "daily": catalogs.daily_provenance.origin,
                                "cumulative": catalogs.cumulative_provenance.origin,
                                "seven_day": catalogs.seven_day_provenance.origin,
                            },
                            catalog_serialization="deterministic_recovered",
                            account_state_origin=(
                                compatibility.profile.sign_state_template.provenance.origin
                            ),
                            catalog_hashes=catalog_hashes(catalogs),
                            account_state={
                                "is_new_month": compatibility.sign_state.is_new_month,
                                "sign_count": compatibility.sign_state.sign_count,
                                "make_up_time": compatibility.sign_state.make_up_time,
                                "role_make_up_time": compatibility.sign_state.role_make_up_time,
                                "is_today_signed": compatibility.sign_state.is_today_signed,
                                "sum_claimed": compatibility.sign_state.sum_claimed,
                                "seven_claimed": compatibility.sign_state.seven_claimed,
                                "seven_day": compatibility.sign_state.seven_day,
                            },
                        )
                        continue
                    if packet.spec.name == "createRole":
                        # C2S2 owns the role name and career.  The remaining
                        # fields are the source-backed minimal S2C10 state.
                        login = session_state["login"]
                        if not self.server.role_store.accepts_account(
                            session_state["account_key"]
                        ):
                            # The write-side gate for a named play profile: even
                            # if a createRole somehow arrives on a session that
                            # was not refused at login, it may never add a
                            # second player's role to an already-bound profile.
                            self._refuse_profile_account_mismatch(
                                connection_id,
                                session_state["account_key"],
                                stage="create_role",
                            )
                            break
                        career = int(packet.fields[1])
                        try:
                            instantiate_role_compatibility_state(
                                career,
                                profile=self.server.compatibility_profile,
                            )
                        except ValueError as exc:
                            self._emit_error(
                                connection_id,
                                "unsupported_compatibility_career",
                                str(exc),
                                career=career,
                                profile_id=self.server.compatibility_profile.profile_id,
                            )
                            break
                        # The binding, the role ID and the serial now live in one
                        # durable transaction, so a replay of this request
                        # returns the role that already exists instead of
                        # allocating a second one, and a restart can no longer
                        # hand the same role ID to a different role.
                        record, created = self.server.role_store.create_or_get(
                            account_key=session_state["account_key"],
                            server_id=login["server_id"],
                            role_name=_text(packet.fields[0]),
                            career=career,
                            create_time=self.server.now_unix(),
                            compatibility_profile_id=(
                                self.server.compatibility_profile.profile_id
                            ),
                        )
                        role = record.to_role_state(
                            username=login["username"],
                            profile=self.server.compatibility_profile,
                        )
                        # The durable row itself, so every later fact this
                        # session observes (a story completion) is stored against
                        # a role the archive really holds rather than against
                        # whatever the wire claimed.
                        session_state["role_record"] = record
                        compatibility = role.compatibility
                        assert compatibility is not None
                        persistence = self._role_persistence_fields(record)
                        self.server.event_logger.emit(
                            "tcp_role_created" if created else "tcp_create_role_replay_absorbed",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            role=role.event_role(),
                            classification=(
                                "accepted_local_transaction"
                                if created
                                else "existing_binding_returned"
                            ),
                            role_origin=(
                                ROLE_ORIGIN_CREATED if created else ROLE_ORIGIN_RESUMED
                            ),
                            role_id_reused=not created,
                            second_role_created=False,
                            **persistence,
                        )
                        self._emit_compat_role_initialized(
                            connection_id,
                            role,
                            role_origin=(
                                ROLE_ORIGIN_CREATED if created else ROLE_ORIGIN_RESUMED
                            ),
                            persistence=persistence,
                        )
                        self._send_base_role_segment(
                            connection_id,
                            writer,
                            session_state,
                            role,
                            role_origin=(
                                ROLE_ORIGIN_CREATED if created else ROLE_ORIGIN_RESUMED
                            ),
                            include_create_result=True,
                            persistence=persistence,
                        )
                        continue
                    if packet.spec.name == "receivePackage":
                        sync_index = int(packet.fields[0])
                        reconnect = bool(packet.fields[1])
                        if (
                            session_state.get("sync_phase") != "awaiting_base_ack"
                            or sync_index != SYNC_INDEX_START
                            or reconnect
                        ):
                            self._emit_error(
                                connection_id,
                                "unexpected_sync_ack",
                                "expected receivePackage(1, false) after the base role segment",
                                decoded_fields=(sync_index, reconnect),
                            )
                            break
                        self._send_s2c(
                            connection_id,
                            writer,
                            "returnLoginInfoStart",
                            (SYNC_INDEX_END, False),
                        )
                        role = session_state.get("role")
                        if role is None or role.compatibility is None:
                            self._emit_error(
                                connection_id,
                                "missing_compatibility_role",
                                "terminal login sync requires an initialized compatibility role",
                            )
                            break
                        compatibility = role.compatibility
                        # TASK-0434: the 元魂珠 panel reads le_dSoulTalent (S2C
                        # 553) as well as le_dSoulId (S2C 554).  The talent node
                        # 5 is the personal 元魂 row (soulId1 2202); only dev
                        # overlays get it, so frozen profile streams keep bytes.
                        soul_node = 5 if (
                            isinstance(session_state.get("dev_play_babel_overlay"), BabelOverlay)
                            or isinstance(session_state.get("dev_play_artifact_overlay"), ArtifactOverlay)
                        ) else 0
                        if soul_node:
                            self._send_s2c(
                                connection_id, writer, "returnTalentSoulNode",
                                (soul_node,),
                                classification="synthetic_dev_play_soul_node",
                            )
                            self._send_s2c(
                                connection_id, writer, "returnTalentPoint",
                                (1, 80),
                                decoded_fields={"talent_type": 1,
                                                "left_point": 80},
                                classification="synthetic_dev_play_talent_point",
                            )
                            # TASK-0435: activeSoulTalent/levelupTalent read
                            # getSyncCounter(TALENT=189); nil makes the 学习
                            # button a silent no-op.
                            self._send_s2c(
                                connection_id, writer, "returnSyncCounter",
                                (189, 0),
                                decoded_fields={"counter_type": 189,
                                                "counter_value": 0},
                                classification="synthetic_dev_play_talent_counter",
                            )
                            self._replay_npc_tasks(
                                connection_id, writer, role, session_state
                            )
                            self._grant_gacha_fodder(
                                connection_id, role, session_state
                            )
                            # TASK-0442: replaying S2C 412 before the scene
                            # segment makes the client close the connection,
                            # so the story replay is disabled until a valid
                            # placement is proven.
                            # TASK-0442: sending S2C 276/277/278 inside the
                            # login segment makes this client close the
                            # connection before the terminal packets; delayed
                            # to the first heartbeat instead.
                            session_state["adv_piece_pending"] = True
                        soul_response = self._send_s2c(
                            connection_id,
                            writer,
                            "returnTalentSoulId",
                            (compatibility.active_soul,),
                            classification="deterministic_active_soul",
                        )
                        soul_rule = compatibility.profile.active_soul_for_career(role.career)
                        self._emit_compatibility_packet(
                            "tcp_active_soul_sent",
                            connection_id,
                            soul_response,
                            role,
                            origin=soul_rule.provenance.origin,
                            evidence_class=soul_rule.provenance.evidence_grade,
                            career=role.career,
                            active_soul=compatibility.active_soul,
                        )
                        role_value_fields = self._owner_draw_role_value_vector(
                            connection_id, role, compatibility, session_state
                        )
                        role_value_response = self._send_s2c(
                            connection_id,
                            writer,
                            "returnAllValue",
                            role_value_fields,
                            decoded_fields={
                                "field_count": len(role_value_fields),
                                "vector_hash": compatibility.vector_hash(),
                                "class_counts": compatibility.profile.class_counts(),
                                "synthetic_test_grant": (
                                    self.server.owner_draw_test_grant is not None
                                ),
                            },
                            classification="synthetic_compatibility_role_values",
                        )
                        self._emit_compatibility_packet(
                            "tcp_role_values_sent",
                            connection_id,
                            role_value_response,
                            role,
                            origin="synthetic_compatibility",
                            evidence_class="field-level C/D compatibility classification",
                            field_count=len(role_value_fields),
                            vector_hash=compatibility.vector_hash(),
                            class_counts=compatibility.profile.class_counts(),
                            synthetic_test_grant=(
                                self.server.owner_draw_test_grant is not None
                            ),
                        )
                        # TASK-0084: the bag sort slots this archive actually
                        # observed the client change, immediately after the
                        # role-value snapshot whose goods slots the bag's item
                        # panel reads, so the client holds the player's own
                        # setting before the bag can be opened.  The position is
                        # compatibility ordering -- the historical send position
                        # was never recovered -- and with no stored slot this
                        # sends nothing at all.
                        self._send_bag_sort_rows(
                            connection_id, writer, role, session_state
                        )
                        # Stored cards replay here: recovered fight rewards
                        # always, deterministic draw fixtures only while the
                        # Owner draw-test overlay is active.  The handbook
                        # rows are derived from the same visible set.  A role
                        # with no visible card sends nothing, so the default
                        # Device Acceptance stream is unchanged.
                        new_reward_templates = self._reconcile_fight_rewards(
                            connection_id, role, session_state
                        )
                        self._send_card_snapshot(
                            connection_id,
                            writer,
                            role,
                            session_state,
                            new_template_ids=new_reward_templates,
                        )
                        self._apply_growth_grant(
                            connection_id, role, session_state
                        )
                        self._send_special_card_snapshot(
                            connection_id, writer, role, session_state
                        )
                        # TASK-0206: the client's own ADVANCE_CARD sync counter.
                        # Compatibility ordering next to the material inventory;
                        # the historical position was never recovered.  Sends
                        # nothing for a role that has never advanced, so the
                        # default login stream is unchanged.
                        self._send_advance_sync_counter_snapshot(
                            connection_id, writer, role, session_state
                        )
                        self._send_hot_spot_snapshot(
                            connection_id, writer, role, session_state
                        )
                        server_time_policy = compatibility.profile.server_time
                        if server_time_policy is not None:
                            # Compatibility ordering: after S2C27, before the
                            # terminal S2C9(40).  The historical order is unknown;
                            # this position is synthetic compatibility ordering.
                            now_unix = self.server.now_unix()
                            server_time_fields = server_time_policy.protocol_fields(
                                now_unix=now_unix,
                                server_started_unix=self.server.server_started_unix,
                            )
                            server_time_response = self._send_s2c(
                                connection_id,
                                writer,
                                "serverTime",
                                server_time_fields,
                                decoded_fields={
                                    "dTime": server_time_fields[0],
                                    "dClock": server_time_fields[1],
                                    "dPowerRecoverTime": server_time_fields[2],
                                    "dOpenTime": server_time_fields[3],
                                },
                                classification="compatibility_server_time_v2",
                            )
                            self._emit_compatibility_packet(
                                "tcp_server_time_sent",
                                connection_id,
                                server_time_response,
                                role,
                                parent_profile_id=compatibility.profile.parent_profile_id,
                                policy_compatibility_version=(
                                    server_time_policy.time_provenance.compatibility_version
                                ),
                                field_origins={
                                    "dTime": server_time_policy.time_provenance.origin,
                                    "dClock": server_time_policy.clock_provenance.origin,
                                    "dPowerRecoverTime": (
                                        server_time_policy.power_recover_provenance.origin
                                    ),
                                    "dOpenTime": (
                                        server_time_policy.open_time_provenance.origin
                                    ),
                                },
                                field_policies={
                                    "dTime": server_time_policy.time_policy,
                                    "dOpenTime": server_time_policy.open_time_policy,
                                },
                                server_time_values={
                                    "dTime": server_time_fields[0],
                                    "dClock": server_time_fields[1],
                                    "dPowerRecoverTime": server_time_fields[2],
                                    "dOpenTime": server_time_fields[3],
                                },
                                historical_ordering=server_time_policy.historical_ordering,
                                ordering_origin=(
                                    server_time_policy.ordering_provenance.origin
                                ),
                                ordering_reason=(
                                    server_time_policy.ordering_provenance.reason
                                ),
                                historical_values_recovered=False,
                            )
                        mystery_shop_policy = compatibility.profile.mystery_shop
                        if (
                            server_time_policy is not None
                            and mystery_shop_policy is not None
                        ):
                            # Compatibility ordering: after S2C0, before the terminal
                            # S2C9(40).  Historical ordering is unknown; this position
                            # is synthetic compatibility ordering.  The shop dTime is
                            # the EXACT resolved S2C0 dTime value -- the clock is not
                            # read a second time.
                            shop_response = self._send_s2c(
                                connection_id,
                                writer,
                                "refreshShopStart",
                                mystery_shop_policy.protocol_fields(
                                    server_time_dtime=server_time_fields[0]
                                ),
                                decoded_fields={
                                    "dType": mystery_shop_policy.shop_type,
                                    "dTime": server_time_fields[0],
                                },
                                classification="compatibility_mystery_shop_state_v3",
                            )
                            self._emit_compatibility_packet(
                                "tcp_mystery_shop_state_sent",
                                connection_id,
                                shop_response,
                                role,
                                parent_profile_id=compatibility.profile.parent_profile_id,
                                policy_compatibility_version=(
                                    mystery_shop_policy.time_provenance.compatibility_version
                                ),
                                shop_type=mystery_shop_policy.shop_type,
                                shop_type_origin=(
                                    mystery_shop_policy.shop_type_provenance.origin
                                ),
                                shop_time_values={
                                    "dType": mystery_shop_policy.shop_type,
                                    "dTime": server_time_fields[0],
                                },
                                d_time_policy=mystery_shop_policy.time_policy,
                                d_time_policy_origin=(
                                    mystery_shop_policy.time_provenance.origin
                                ),
                                availability=mystery_shop_policy.availability,
                                availability_origin=(
                                    mystery_shop_policy.availability_provenance.origin
                                ),
                                goods_count=mystery_shop_policy.goods_count,
                                goods_origin=(
                                    mystery_shop_policy.goods_provenance.origin
                                ),
                                snapshot_end_sent=(
                                    mystery_shop_policy.snapshot_end_sent
                                ),
                                historical_ordering=(
                                    mystery_shop_policy.historical_ordering
                                ),
                                ordering_origin=(
                                    mystery_shop_policy.ordering_provenance.origin
                                ),
                                server_time_dtime_reused=True,
                                historical_values_recovered=False,
                            )
                        self._send_s2c(
                            connection_id,
                            writer,
                            "returnLoginInfoEnd",
                            (SYNC_INDEX_END, False),
                        )
                        session_state["sync_phase"] = "complete"
                        self.server.event_logger.emit(
                            "tcp_login_sync_complete_sent",
                            connection_id=connection_id,
                            remote_endpoint=remote,
                            sync_index=SYNC_INDEX_END,
                            classification="compatibility_terminal_segment",
                        )
                        # TASK-0092: the recovered Team + fabao-select login
                        # snapshot, immediately after the terminal sync segment
                        # and immediately BEFORE the S2C411 story snapshot and
                        # the S2C283 whose dispatch starts the main-city switch
                        # (pkgSceneMgr.switchScene(TYPE_MAIN_CITY) ->
                        # doFinishSwitch -> pkgMainUIMgr.onComplete -> init ->
                        # updateInfo at line 1130).  That is the latest wire
                        # position that provably precedes the client's unguarded
                        # team read; the historical login ordering is unknown, so
                        # this position is an explicit synthetic compatibility
                        # ordering, never a claim about the historical packet
                        # stream.  A server built from v1-v4 has no such policy
                        # and therefore sends byte-for-byte what it sent before.
                        self._send_team_snapshot(
                            connection_id, writer, role, session_state
                        )
                        # TASK-0094: the recharge-activity state, immediately
                        # after the team snapshot and immediately before the
                        # S2C411 story snapshot and the S2C283 whose dispatch
                        # starts the main-city switch (pkgMainUIMgr.onComplete ->
                        # init -> initBtn at line 1133, whose initBtnReplace at
                        # :916 is the red-point pass that reads tbGetRecharge).
                        # Historical login ordering is unknown, so this position
                        # is an explicit synthetic compatibility ordering.  A
                        # server built from v1-v5 has no such policy and sends
                        # byte-for-byte what it sent before.
                        self._send_recharge_activity_snapshot(
                            connection_id, writer, role
                        )
                        # TASK-0202: the chapter milestone chests this role
                        # already opened, replayed as the client's own
                        # S2C381/382/383 block.  The client's registration order
                        # puts the box family (381-384) immediately BEFORE the
                        # chapter-task family (385-388), so the block rides
                        # here.  A v1-v17 profile has no policy and sends
                        # nothing; a v18 profile with no accepted chest sends
                        # zero packets, so the default login stream stays
                        # byte-for-byte what it was.
                        self._send_chapter_box_snapshot(
                            connection_id, writer, role, session_state
                        )
                        # TASK-0104: the chapter-task container init, with the
                        # other login-time container pushes and immediately BEFORE
                        # the v7 chapter-item pair -- the client's own registration
                        # table puts the task family (385-388) immediately before
                        # the item family (389-392), and both containers are read in
                        # the same pass of the same finish callback.  Its module
                        # default is nil, so a server built from v1-v8 sends nothing
                        # here and its stream stays byte-for-byte what it was.
                        self._send_chapter_task_snapshot(
                            connection_id, writer, role
                        )
                        # TASK-0180: the v15 one-row chapter-task state that
                        # opens the client's own 祈灵 gate.  It follows the v9
                        # 385/387 pair immediately -- keeping that pair's bytes
                        # and order unchanged -- and still precedes S2C283,
                        # whose dispatch starts the main-city switch, so
                        # pkgMainUIMgr.updateBtn sees the completed id.  A
                        # server built from v1-v14 sends nothing here and its
                        # stream stays byte-for-byte what it was.
                        self._send_drawcard_unlock(
                            connection_id, writer, role
                        )
                        # TASK-0240: the chapter tasks this role's client
                        # itself received or completed, replayed through the
                        # client's own S2C386 container.  The v9 385/387 pair
                        # above already reset the table and the v15 row above
                        # is the drawcard gate; this block adds only the stored
                        # rows and sends zero packets when there are none, so
                        # the default login stream is byte-for-byte what it
                        # was.
                        self._send_chapter_task_rows(
                            connection_id, writer, role, session_state
                        )
                        # TASK-0098: the chapter-map item container init, with
                        # the other login-time container pushes and immediately
                        # before the S2C411 story snapshot and the S2C283 whose
                        # dispatch starts the main-city switch.  The client reads
                        # it when the world map is built (pkgChapterMainUIViewMgr
                        # .initChapter), and its own module default is nil, so a
                        # server built from v1-v6 sends nothing here and its
                        # stream stays byte-for-byte what it was.
                        self._send_chapter_item_snapshot(
                            connection_id, writer, role, session_state
                        )
                        # TASK-0232: the dev-play babel info, immediately AFTER
                        # the client's own chapter-item block (the synthetic
                        # ordering is recorded in the event) and only when the
                        # dev-babel-main overlay is active.  A normal login --
                        # including every qa-changan-main login -- sends zero
                        # packets here.
                        self._send_babel_info_snapshot(
                            connection_id, writer, session_state, role
                        )
                        # TASK-0324: the 秘境 open dialog calls pkgUnchartedData.IsCanSweep
                        # BEFORE binding its buttons, and IsCanSweep indexes le_tbUnchartedPass,
                        # which only S2C 434 returnUnchartedCfg writes -- a bootstrap cycle (434
                        # comes after C2S 244, 244 only from that dialog). RUN25 died exactly there.
                        # The dev-play login therefore announces the first uncharted row up front.
                        if isinstance(
                                session_state.get("dev_play_babel_overlay"),
                                BabelOverlay):
                            # TASK-0325: dUnchartedId must be 0, not 1. The select view
                            # treats GetUnchartedId()==rowId as "this island is already in
                            # progress", relabels the row to 继续 and jumps straight into the
                            # scene -- which skips the 开启 dialog and therefore C2S 244
                            # entirely (RUN26). A 0 keeps the pass table alive for IsCanSweep
                            # while leaving row 1 in its 进入 -> dialog -> 244 path.
                            cfg_fields = (0, 0, "", 0, False, False, False)
                            cfg_response = self._send_s2c(
                                connection_id, writer, "returnUnchartedCfg", cfg_fields,
                                decoded_fields={
                                    "dUnchartedId": cfg_fields[0],
                                    "dRandomSeed": cfg_fields[1],
                                    "strInstanceId": cfg_fields[2],
                                    "dDeadCount": cfg_fields[3],
                                    "bPass1": cfg_fields[4],
                                    "bPass2": cfg_fields[5],
                                    "bPass3": cfg_fields[6],
                                },
                                classification="synthetic_dev_play_uncharted_cfg",
                            )
                            self.server.event_logger.emit(
                                "tcp_dev_play_uncharted_cfg_pushed",
                                connection_id=connection_id,
                                remote_endpoint=(
                                    f"{self.client_address[0]}:{self.client_address[1]}"),
                                protocol_id=cfg_response.spec.protocol_id,
                                sequence=cfg_response.sequence,
                                fields=list(cfg_fields),
                                reason=("the open dialog needs le_tbUnchartedPass before C2S 244"),
                                unknown_ids=["U-434-timing"],
                                replaceable=True,
                                historical=False,
                            )
                        # TASK-0375: give the protagonist a 元魂珠. pkgBattlePrepareDataCreator
                        # passes le_dSoulId/le_dSoulSkillId/le_dSoulShowMode into getSoulHpAndAtk,
                        # which is how the soul body gets 630 hp / 210 atk x factors; without
                        # these five packets the protagonist never exists on the field.
                        if (isinstance(
                                session_state.get("dev_play_artifact_overlay"),
                                ArtifactOverlay)
                                or isinstance(
                                    session_state.get("dev_play_babel_overlay"),
                                    BabelOverlay)):
                            # 554 is already sent by the compatibility profile (career 1 -> soul
                            # 101), so this block only fills what that path leaves empty: the
                            # skill (11001 = cfg soul 101 sfmId1), the show mode, and a bracket
                            # so the panel reads a complete record.
                            soul_packets = (
                                ("returnTalentStart", ()),
                                ("returnTalentSoulSkill", (11001,)),
                                ("returnTalentSoulShowMode", (1,)),
                                ("returnTalentEnd", ()),
                            )
                            for _name, _fields in soul_packets:
                                self._send_s2c(
                                    connection_id, writer, _name, _fields,
                                    classification="synthetic_dev_play_soul",
                                )
                        # TASK-0323: the uncharted select view aborts without the building
                        # container (pkgUnchartedData:702 pairs(nil), RUN24), so dev-play logins
                        # send the empty 484/486 bracket after the babel/artifact pushes.
                        if (isinstance(
                                session_state.get("dev_play_babel_overlay"),
                                BabelOverlay)
                                or isinstance(
                                    session_state.get("dev_play_artifact_overlay"),
                                    ArtifactOverlay)):
                            for _name, _fields in dev_play_uncharted.building_sync_packets():
                                self._send_s2c(
                                    connection_id, writer, _name, _fields,
                                    classification="synthetic_dev_play_uncharted_building",
                                )
                        # TASK-0320: every dev-play session sends the empty mail brackets. The v24
                        # login stream has none, so pkgMailModelMgr.lua:132 pairs(nil) fires once
                        # per login on the dev archives (RUN21 measured exactly that).
                        if (isinstance(
                                session_state.get("dev_play_artifact_overlay"),
                                ArtifactOverlay)
                                or isinstance(
                                    session_state.get("dev_play_babel_overlay"),
                                    BabelOverlay)):
                            self._send_dev_mail_snapshot(connection_id, writer)
                            # TASK-0487: unlock every shipped skin before the
                            # 时装 panel opens (it reads this container).
                            try:
                                self._handle_skin_request(
                                    connection_id, writer, session_state)
                            except Exception:
                                pass
                            # TASK-0488: the 昆仑 tower list locks itself on
                            # the first build; announce every tower open at
                            # login, not only on C2S 744.
                            try:
                                self._send_s2c(
                                    connection_id, writer,
                                    "artifactDgnSendWeekAndOpen",
                                    dev_play_artifact.week_and_open_fields(
                                        self.server.now_unix()),
                                    classification=(
                                        "synthetic_dev_artifact_week_open"),
                                )
                            except Exception:
                                pass
                            try:
                                self._send_dev_fabao_snapshot(
                                    connection_id, writer)
                            except Exception:
                                pass
                            try:
                                self._send_dev_rune_snapshot(
                                    connection_id, writer)
                            except Exception:
                                pass
                            try:
                                self._send_dev_talent_snapshot(
                                    connection_id, writer)
                            except Exception:
                                pass
                            # TASK-0498: replay the stored team 法宝/神符
                            # selections on the 2nd heartbeat, after the login
                            # bracket has settled (TASK-0497 proved replaying
                            # inside the bracket breaks the client's team init).
                            session_state["team_replay_tick"] = 0
                        # TASK-0312: the artifact dev-play overlay also fills the advance-piece
                        # namespace the bag's 其他 page reads (S2C 276/277/278). Without it the
                        # page raises on a nil tbAdvPiece and no row can be tapped (RUN16).
                        # TASK-0315: OFF by default. RUN18 proved the client accepts the login
                        # stream and then aborts ~250 ms after "同步数据完成" with
                        # "客户端主动断开连接 dReasonType:101", never sending enterGameSuccess --
                        # and the only login-stream additions since the known-good RUN17 are this
                        # block and the 999999 card write. TASK-0310 reconstructed the 276/277/278
                        # ids by anchoring rather than from a device capture, so they are the
                        # prime suspect: switch them off until a device run proves them.
                        if (dev_play_artifact.PUSH_ADV_PIECE_ON_LOGIN
                                and isinstance(
                                    session_state.get("dev_play_artifact_overlay"),
                                    ArtifactOverlay)):
                            for _name, _fields in dev_play_artifact.adv_piece_packets():
                                self._send_s2c(
                                    connection_id, writer, _name, _fields,
                                    classification="synthetic_dev_play_adv_piece",
                                )
                            self.server.event_logger.emit(
                                "tcp_dev_play_adv_piece_sent",
                                connection_id=connection_id,
                                remote_endpoint=(
                                    f"{self.client_address[0]}:{self.client_address[1]}"),
                                role_id=role.role_id,
                                rows=list(dev_play_artifact.ADV_PIECE_ROWS),
                                value_provenance=(
                                    dev_play_artifact.ADV_PIECE_PROVENANCE),
                                replaceable=True,
                                historical=False,
                                state_changed=False,
                                role_store_written=False,
                            )
                        # TASK-0109: the chapter-machine container init, with the
                        # other login-time container pushes and immediately AFTER
                        # the v7 chapter-item pair -- the client's own registration
                        # table puts the machine family (399-403) after the item
                        # family (389-393), and both containers are read in the same
                        # pass of the same finish callback (the machine one one
                        # statement later).  Its module default is nil, so a server
                        # built from v1-v9 sends nothing here and its stream stays
                        # byte-for-byte what it was.
                        self._send_chapter_machine_snapshot(
                            connection_id, writer, role
                        )
                        # TASK-0102: the experience-task container init, with the
                        # other login-time container pushes and immediately before
                        # the S2C411 story snapshot and the S2C283 whose dispatch
                        # starts the main-city switch.  The client reads it from
                        # the world map's own countdown tick (a 1,000 ms timer
                        # started when the chapter UI is built), and its module
                        # default is nil, so a server built from v1-v7 sends
                        # nothing here and its stream stays byte-for-byte what it
                        # was.
                        self._send_experience_task_snapshot(
                            connection_id, writer, role
                        )
                        story_start_policy = compatibility.profile.story_start
                        if story_start_policy is not None:
                            # Compatibility ordering (v4/TASK-0035): immediately
                            # BEFORE S2C283 returnSceneInfo -- the packet whose
                            # dispatch starts the main-city switch
                            # (pkgOnlinePlayersSceneMgr.switchSceneByRecordType ->
                            # pkgSceneMgr.switchScene(TYPE_MAIN_CITY) ->
                            # doFinishSwitch -> handlerForScene -> initInMainCity
                            # -> pkgNpcMgr.createByScene -> create ->
                            # loadModelAssetAsync -> loadComplete ->
                            # pkgStoryTrigger_1008.triggerStory -> checkStoryFinish
                            # -> pkgStoryModelMgr.le_story_list).  This is the
                            # latest wire position that provably precedes that
                            # read, so the client has initialized the Story
                            # container before it enters the main city.  The
                            # historical login ordering is unknown; this position
                            # is an explicit synthetic compatibility ordering,
                            # never a claim about the historical packet stream.
                            # (S2C13 enterGameSuccess is NOT that boundary: its
                            # recovered handler switches no scene and is reached
                            # only after the switch already began -- see
                            # max_decoder/analyze_story_ordering.py.)
                            story_response = self._send_s2c(
                                connection_id,
                                writer,
                                "returnStoryStart",
                                story_start_policy.protocol_fields(),
                                classification="compatibility_story_zero_snapshot_v4",
                            )
                            self._emit_compatibility_packet(
                                "tcp_story_start_sent",
                                connection_id,
                                story_response,
                                role,
                                parent_profile_id=(
                                    compatibility.profile.parent_profile_id
                                ),
                                policy_compatibility_version=(
                                    story_start_policy.snapshot_provenance
                                    .compatibility_version
                                ),
                                story_list_policy=story_start_policy.story_list,
                                story_list_policy_origin=(
                                    story_start_policy.snapshot_provenance.origin
                                ),
                                initialises_client_container="le_story_list",
                                story_rows_sent=0,
                                historical_ordering=(
                                    story_start_policy.historical_ordering
                                ),
                                ordering_origin=(
                                    story_start_policy.ordering_provenance.origin
                                ),
                                ordering_reason=(
                                    story_start_policy.ordering_provenance.reason
                                ),
                                historical_values_recovered=False,
                            )
                            # TASK-0073: the story rows this archive actually
                            # observed, immediately after the snapshot that
                            # initialises (and therefore clears) the client's
                            # container and before S2C283 starts the main-city
                            # switch -- the same boundary the empty snapshot
                            # already uses, so the client has its story state
                            # before pkgNpcMgr.create reads it.  With no stored
                            # completion this sends nothing at all.
                            self._send_story_completion_rows(
                                connection_id, writer, role, session_state
                            )
                        # TASK-0167: the prison-fight boss state, once per
                        # completed login.  It follows the S2C411/412 story
                        # state and immediately precedes S2C283 returnSceneInfo
                        # -- the packet whose dispatch starts the main-city
                        # switch, and therefore the last position that
                        # provably precedes the client's first PANEL_CLOSE
                        # (pkgMainUIMgr.onPanelClose -> checkShowPrisonFight ->
                        # pkgPrisonFightModel.checkActOn, the nil compare that
                        # stops the chapter's return to the main city).  A
                        # server built from v1-v12 has no such policy, so this
                        # block sends nothing and its stream is byte-for-byte
                        # what it was.
                        prison_fight_policy = compatibility.profile.prison_fight
                        if prison_fight_policy is not None:
                            self._send_prison_fight_snapshot(
                                connection_id, writer, role
                            )
                        scene_fields = compatibility.scene_state.protocol_fields()
                        scene_response = self._send_s2c(
                            connection_id,
                            writer,
                            "returnSceneInfo",
                            scene_fields,
                            decoded_fields={
                                "scene_type": compatibility.scene_state.scene_type,
                                "pos_x": compatibility.scene_state.pos_x,
                                "pos_y": compatibility.scene_state.pos_y,
                                "pos_z": compatibility.scene_state.pos_z,
                            },
                            classification="synthetic_compatibility_scene_state",
                        )
                        scene_provenance = compatibility.profile.scene_state_template.provenance
                        self._emit_compatibility_packet(
                            "tcp_scene_state_sent",
                            connection_id,
                            scene_response,
                            role,
                            origin=scene_provenance.origin,
                            evidence_class=scene_provenance.evidence_grade,
                            reason=scene_provenance.reason,
                            scene_state={
                                "scene_type": compatibility.scene_state.scene_type,
                                "pos_x": compatibility.scene_state.pos_x,
                                "pos_y": compatibility.scene_state.pos_y,
                                "pos_z": compatibility.scene_state.pos_z,
                            },
                        )
                        # Sent AFTER the scene switch so every existing login
                        # packet keeps its index and bytes.  The team view reads
                        # `pkgTalentMgr.getSoulSkillId() > 0` on its first open;
                        # that global is nil until S2C 555 arrives.  0 is the
                        # client's own "no soul skill selected" sentinel.
                        # V15 and earlier streams are frozen and omit it.
                        if (
                            role.compatibility.profile.profile_id
                            in (PROFILE_ID_V16, PROFILE_ID_V17, PROFILE_ID_V18,
                                PROFILE_ID_V19, PROFILE_ID_V20, PROFILE_ID_V21,
                                PROFILE_ID_V22, PROFILE_ID_V23, PROFILE_ID_V24)
                        ):
                            # TASK-0375c: the soul skill is real under a dev-play overlay and stays
                            # the 0 sentinel elsewhere. cfg soul 101 carries sfmId1 = 11001 (contract
                            # task_0374) and the client adds those features to the soul body -- but
                            # the frozen v16..v24 login streams are contracts of their own, so only
                            # the overlay run moves them.
                            _soul_skill = (
                                dev_play_artifact.PROTAGONIST_SOUL_SKILL_ID
                                if (isinstance(session_state.get("dev_play_artifact_overlay"),
                                               ArtifactOverlay)
                                    or isinstance(session_state.get("dev_play_babel_overlay"),
                                                  BabelOverlay))
                                else 0
                            )
                            soul_skill_response = self._send_s2c(
                                connection_id,
                                writer,
                                "returnTalentSoulSkill",
                                (_soul_skill,),
                                classification=("synthetic_dev_play_soul_skill"
                                                if _soul_skill else
                                                "synthetic_no_soul_skill_selected"),
                            )
                            self._emit_compatibility_packet(
                                "tcp_talent_soul_skill_sent",
                                connection_id,
                                soul_skill_response,
                                role,
                                soul_skill_id=_soul_skill,
                                origin="synthetic_compatibility",
                                value_policy="client_none_sentinel",
                                selection_recovered=False,
                                historical_values_recovered=False,
                            )
                        # TASK-0247 / profile v24: the inactive bitRabbit state.
                        # The client's `pkgBitRabbitDataMgr.checkAddRabbit`
                        # (source line 102) compares `getFightCountLeft()` with 0
                        # before any guard, and that accessor returns
                        # `le_tbBitRabbitData.dFightCountLeft`, nil until this
                        # server push writes it.  Without it the main-city
                        # red-point pass throws the exact RUN-0077 error and the
                        # two calls after `addRabbitOnFriend` never run.  The
                        # six zeros are the client's own inactive state: no
                        # activity, no square row, no fight, no reward.  Sent
                        # AFTER S2C 555 so every existing login packet keeps its
                        # index and bytes; v1..v23 sessions (the dev-babel-main
                        # v20 overlay included) carry no policy and are unchanged.
                        compatibility = role.compatibility
                        bit_rabbit_policy = (
                            compatibility.profile.bit_rabbit_state
                            if compatibility is not None
                            else None
                        )
                        if bit_rabbit_policy is not None:
                            bit_rabbit_response = self._send_s2c(
                                connection_id,
                                writer,
                                "syncBitRabbit",
                                bit_rabbit_policy.fields(),
                                classification="synthetic_inactive_bit_rabbit_state",
                            )
                            self._emit_compatibility_packet(
                                "tcp_bit_rabbit_state_sent",
                                connection_id,
                                bit_rabbit_response,
                                role,
                                position=bit_rabbit_policy.position,
                                trigger=bit_rabbit_policy.triggers[0],
                                fight_count_left=0,
                                last_add_count_time=0,
                                rabbit_time=0,
                                rabbit_left_count=0,
                                rabbit_own_bit=0,
                                rabbit_boss_cfg_id=0,
                                value_policy=bit_rabbit_policy.value_policy,
                                value_provenance=(
                                    bit_rabbit_policy.value_provenance.origin
                                ),
                                ordering_origin=(
                                    bit_rabbit_policy.ordering_provenance.origin
                                ),
                                historical_ordering=(
                                    bit_rabbit_policy.historical_ordering
                                ),
                                historical_values_recovered=(
                                    bit_rabbit_policy.historical_values_recovered
                                ),
                                replaceable=bit_rabbit_policy.replaceable,
                                historical=bit_rabbit_policy.historical,
                                bit_rabbit_rows_sent=0,
                                state_changed=False,
                            )
                        # Dev-Server-only achieve-rank overlay.  Absent from
                        # every normal login, so the frozen v16 stream and all
                        # wire-audit hashes are unchanged unless the explicit
                        # --achieve-level flag was given.
                        if self.server.achieve_level_test_grant is not None:
                            self._send_achieve_level_grant(
                                connection_id,
                                writer,
                                self.server.achieve_level_test_grant,
                            )
                        continue
                    if packet.spec.name == "goodsUse":
                        # TASK-0266: C2S 429 is a LOCK-SEND (S2C 820 is its only
                        # unlock), so this branch always answers exactly one 820
                        # -- success or refusal -- and never reaches the
                        # unsupported_protocol break (D-127).  The client would
                        # otherwise shut its own socket down after LOCK_TIME.
                        self._handle_goods_use(
                            connection_id, writer, packet, session_state
                        )
                        continue
                    # TASK-0346: a zero-field notification with no handler is a RECORD, not a
                    # fatal error. Closing the socket here is what made every 活动 press cost a
                    # cold relaunch (RUN45 caught C2S 525 doing exactly that twice). Such a
                    # packet has no fields to misparse and no lock partner, so keeping the
                    # connection is strictly safer than dropping it.
                    # TASK-0360: a REGISTERED id whose format decoded cleanly has a known packet
                    # boundary, so a missing handler is a semantic gap, not a protocol hazard.
                    # Closing the socket for it costs the player the whole session (live 01:50:
                    # artifactDgnCheckSoul when they saved a new team). Record it and stay alive;
                    # the unsupported_protocol event still tells us which id needs a handler.
                    self.server.event_logger.emit(
                        "tcp_record_only_notification",
                        connection_id=connection_id,
                        remote_endpoint=(
                            f"{self.client_address[0]}:{self.client_address[1]}"),
                        protocol_id=packet.spec.protocol_id,
                        protocol_name=packet.spec.name,
                        wire_format=packet.spec.fmt,
                        sequence=packet.sequence,
                        request_fields=[
                            _text(value) if isinstance(value, bytes) else value
                            for value in packet.fields
                        ],
                        reply_sent=False,
                        reason="registered C2S with no handler",
                        connection_kept_alive=True,
                    )
                    self._emit_error(
                        connection_id,
                        "unsupported_protocol_recorded",
                        f"{packet.spec.name} is registered but not handled; recorded, connection kept",
                    )
                    continue

                first_packet = False
                client_version = _text(packet.fields[4])
                if client_version != self.server.expected_version:
                    self._emit_error(
                        connection_id,
                        "version_mismatch",
                        f"expected {self.server.expected_version}, got {client_version}",
                    )
                    break

                identity = login_identity_inputs(packet.fields)
                if not identity["sdk_identity"]:
                    # With neither an SDK user id nor an SDK username there is
                    # nothing to recognise an account by, and every anonymous
                    # client would collide on one key.  Refuse the login
                    # instead of answering `noRole` for a shared identity.
                    self._emit_error(
                        connection_id,
                        "account_identity_missing",
                        "loginByToken carried neither sdk_user_id nor sdk_username",
                    )
                    break
                # A login re-establishes who this session is.  Whatever a previous
                # login on this same connection established -- a role, and any
                # story the client armed under it -- belongs to that account and
                # may not survive into this one, or a pair straddling the change
                # could credit one account's story to another.
                for stale in (
                    "role",
                    "role_record",
                    "role_origin",
                    "story_evidence",
                    "chapter_battle_start",
                ):
                    session_state.pop(stale, None)
                session_state["login"] = {
                    "server_id": packet.fields[0],
                    "username": login_display_username(packet.fields),
                }
                try:
                    account_key = self.server.role_store.account_key(**identity)
                except RoleStoreError as exc:
                    self._emit_error(
                        connection_id,
                        "account_identity_unavailable",
                        str(exc),
                    )
                    break
                session_state["account_key"] = account_key

                existing = self.server.role_store.lookup(account_key)
                if existing is not None:
                    # The recovered existing-role branch.  The client asks for
                    # nothing between loginByToken and the role stream: the only
                    # C2S role request is createRole, and the only C2S that names
                    # an existing role is `reconnect`, which the client refuses
                    # to send on a cold start (`pkgReconnectMgr.reconnectLogin`
                    # calls `pkgCgNetMgr.startLogin` instead whenever
                    # `getGameLogined()` is false -- and that flag only becomes
                    # true from the terminal S2C9(40, false) of a completed
                    # segment sync, so it is false in every new process).  The
                    # server therefore pushes the same index-driven segment
                    # stream the create path proved, with no S2C5 and no S2C14.
                    #
                    # S2C5 `noRole` is the one and only branch that switches the
                    # client to the role-creation scene
                    # (`pkgCgLoginNcMgr.s2c.noRole` ->
                    # `pkgSceneMgr.switchScene(TYPE_CREATE_ROLE, ...)`), so
                    # sending it for an account that already owns a role is
                    # exactly what forced a second creation before this change.
                    try:
                        role = existing.to_role_state(
                            username=session_state["login"]["username"],
                            profile=self.server.compatibility_profile,
                        )
                    except ValueError as exc:
                        # Only reachable from an archive this build did not
                        # write (the create path validates the career before it
                        # stores anything).  Refuse the session rather than let
                        # the handler thread raise with a client connected.
                        self._emit_error(
                            connection_id,
                            "unusable_stored_role",
                            str(exc),
                            stored_career=existing.career,
                            stored_role_id=existing.role_id,
                            profile_id=self.server.compatibility_profile.profile_id,
                        )
                        break
                    persistence = self._role_persistence_fields(existing)
                    self.server.event_logger.emit(
                        "tcp_role_resumed",
                        connection_id=connection_id,
                        remote_endpoint=remote,
                        role=role.event_role(),
                        classification="persisted_binding_restored",
                        role_origin=ROLE_ORIGIN_RESUMED,
                        no_role_sent=False,
                        create_role_result_sent=False,
                        second_role_created=False,
                        stale_profile=(
                            existing.compatibility_profile_id
                            != self.server.compatibility_profile.profile_id
                        ),
                        **persistence,
                    )
                    self._emit_compat_role_initialized(
                        connection_id,
                        role,
                        role_origin=ROLE_ORIGIN_RESUMED,
                        persistence=persistence,
                    )
                    session_state["role_record"] = existing
                    self._send_base_role_segment(
                        connection_id,
                        writer,
                        session_state,
                        role,
                        role_origin=ROLE_ORIGIN_RESUMED,
                        include_create_result=False,
                        persistence=persistence,
                    )
                    continue

                if not self.server.role_store.accepts_account(account_key):
                    # TASK-0071: this archive is one player's named play profile
                    # and this is not that player.  `noRole` is the client's
                    # create-role switch, so answering it here would create a
                    # second character inside an existing save; the login is
                    # refused instead, and the connection closed.
                    self._refuse_profile_account_mismatch(
                        connection_id, account_key, stage="login"
                    )
                    break

                session_state["role_origin"] = ROLE_ORIGIN_UNBOUND
                self.server.event_logger.emit(
                    "tcp_account_role_unbound",
                    connection_id=connection_id,
                    remote_endpoint=remote,
                    classification="no_persisted_binding",
                    account_key_short=account_key[:8],
                    role_store_kind=self.server.role_store.kind,
                    account_identity_policy="salted_local_derivation",
                    account_identity_is_authenticated_player_identity=False,
                    role_origin=ROLE_ORIGIN_UNBOUND,
                )
                self._send_s2c(
                    connection_id,
                    writer,
                    "noRole",
                    (),
                )
                # Keep the same connection alive for heartbeats, role creation,
                # and the source-backed minimal post-create synchronization.
                if not buffer:
                    try:
                        chunk = self.request.recv(65536)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buffer.extend(chunk)
        except (ConnectionError, OSError) as exc:
            self._emit_error(connection_id, "socket_error", str(exc))
        finally:
            self.server.event_logger.emit(
                "tcp_disconnected", connection_id=connection_id, remote_endpoint=remote
            )

