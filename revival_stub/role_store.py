"""Durable account -> role identity for the loopback compatibility stub.

Standard library ``sqlite3`` only.  This module owns the two facts the handler
previously kept in memory and therefore lost on every restart:

* the binding between one local account identity and the role it created on one
  server, and
* the role-ID serial, which started again at
  :data:`revival_stub.role_state.REVIVAL_LOCAL_ROLE_SERIAL_BASE` in every new
  process and so handed the same role ID to different roles.

What is stored, and what is deliberately not:

* Stored: a stable, salted, *derived* ``account_key`` (never the input), the
  server id, the role id, the role name, the career, the acceptance time, the
  identity's provenance and the compatibility-profile version the role's
  synthetic snapshot was first built from, plus the structural schema version.
* Stored since structure version 2: the *observed* completion of one story
  (``story_completions``), together with the raw evidence that produced it --
  the ordered ``C2S 233`` pair, its sequences and the time it was seen.  This is
  play state the client itself asserted, not a recovered historical record; the
  row says so in its ``origin`` column.  It is not part of a compatibility
  profile: the v4 ``S2C 411`` empty snapshot stays the frozen synthetic template
  and a replayed ``S2C 412`` row is durable per-role data, exactly like the role
  row itself.
* Never stored: ``sdk_user_id``, ``sdk_verify_md5``, any SDK token,
  ``device_id``, the SDK username or any other part of the ``loginByToken``
  payload.  The database holds a one-way digest, not a credential.

Honesty boundary (repeated in the emitted events, not only here): this stub
listens on loopback and verifies no official SDK token.  The derived key is a
*local simulator identity match* -- it is not proof of a real player's
authentication, and it must never be described as one.  A value a client
simply types in is likewise not a strong credential; the key only has to be
stable enough to recognise the same local account again.

Failure policy: every unusable-database condition (corrupt file, unknown or
newer schema, failed migration) raises.  The store never silently replaces an
unreadable archive with an empty one, because that would overwrite the only
copy of a role.  A named play profile goes one step further and is opened
through its own guard (``archive_guard``): the file is verified before SQLite
touches it, and the database SQLite opened is proven to be that file before the
first write, so an archive that is missing or is another file's alias cannot be
created, served or written by accident.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from . import card_growth, chapter_map_event, chapter_map_task
from .compatibility_profile import (
    CompatibilityProfile,
    get_profile,
    instantiate_role_compatibility_state,
)
from .role_state import (
    REVIVAL_LOCAL_ROLE_SERIAL_BASE,
    ROLE_ID_SERVER_FACTOR,
    MinimalRoleState,
    allocate_revival_role_id,
)


#: Structural version of the tables below.  A change to any column, type or
#: constraint is a new number *and* a migration step in ``_MIGRATIONS``; the
#: number is stored in the database and compared on every open.
#:
#: 1 -> 2 adds ``story_completions`` (TASK-0073).  2 -> 3 adds
#: ``bag_sort_states`` (TASK-0084).  3 -> 4 adds ``chapter_fight_clears``
#: (TASK-0174).  4 -> 5 adds ``card_instances`` (the Owner draw-test overlay).
#: 5 -> 6 adds ``draw_pity`` (the reconstructed advanced-pool pity counters).
#: 6 -> 7 rebuilds ``draw_pity`` to admit the shared limited pool and adds
#: ``hotspot_draw_pity`` (the per-banner featured-legend counter).  This is the
#: one step that rewrites a table in place (copy -> drop -> rename), because
#: SQLite cannot alter a CHECK constraint; every existing row is copied.
#: 7 -> 8 adds ``team_presets``/``team_state`` (the persisted team edit).
#: 8 -> 9 adds ``card_special_inventory`` and ``role_wallet`` (the card-growth
#: economy: dog-food counts and the 银魄/金魄/money the split writes).
#: 9 -> 10 adds ``chapter_event_clears`` (the chapter-map event a client action
#: completed, replayed as its own ``S2C 390`` row).
#: 10 -> 11 adds ``chapter_box_accepts`` (the chapter milestone chest the client
#: opened with ``C2S 222``, replayed as its own ``S2C 382`` row).  The same
#: transaction raises ``card_special_inventory`` by the reward row's dog food.
#: 11 -> 12 adds ``chapter_event_reward_grants`` (the server-side reward
#: authority behind one observed ``C2S 224`` event: the shipped
#: ``guankaReward[row].crystal`` amount, the reward id and, when recoverable,
#: its crystal bucket).  The reward's *bucket* is not recoverable from the
#: shipped reward row, so the row stores SQL NULL plus
#: ``bucket_provenance='unknown'`` and claims nothing about the client's visible
#: balance.  The migration backfills one row for every already-stored
#: ``chapter_event_clears`` row of the recovered node, once.  This table is a
#: ledger, not a wallet: ``role_wallet`` (money/yinpo/jinpo) is untouched.
#: The card-advance chain (TASK-0206) needs no structural change: its one
#: role-scoped idempotency value lives in the archive's existing ``meta``
#: key-value table (see ``CARD_ADVANCE_META_PREFIX``), so every older archive
#: opens unchanged and no migration is added.
#: Every other step is additive: no existing table, column or row is touched, so
#: an older archive keeps its role, its account key, its salt, its serial, its
#: story rows, its bag rows, its chapter-fight rows and its identity
#: byte-for-byte and only gains the new table.  A migration always runs behind a
#: verified pre-migration snapshot (see ``_write_migration_snapshot``).
SCHEMA_VERSION = 14

#: File name inside the chosen data directory.
STORE_FILENAME = "revival_roles.sqlite3"

#: Domain separator + scheme name of the account-key derivation.  Changing the
#: derivation changes every key, so it is versioned in the digest itself.
ACCOUNT_KEY_DERIVATION = "revival-local-account/v1"

#: Provenance recorded for a role row this stub created itself out of a C2S2
#: ``createRole`` plus the local ID allocator.  It is explicitly *not* an
#: original-server record: the historical CN server's own role row was never
#: recovered, so no row may claim to be one.
IDENTITY_ORIGIN_LOCAL_TRANSACTION = "revival_local_create_role_transaction"

#: Recorded for a row that could ever be imported from an original server
#: export.  Unused today; named so that a later importer cannot silently reuse
#: the local-transaction label for adopted history.
IDENTITY_ORIGIN_IMPORTED = "imported_original_record"

#: ``KIND_*`` values are the closed vocabulary reported in every event.
KIND_MEMORY = "ephemeral_memory"
KIND_PERSISTENT = "persistent"
KIND_FRESH_TEST = "ephemeral_fresh_role_test"
#: TASK-0071: a *named* durable play profile (`play_profile`), opened through
#: `revival_stub.play_profile`.  It differs from KIND_PERSISTENT in exactly one
#: behaviour: a profile is **one player's save**, so an account other than the
#: one that created its role is refused rather than answered `noRole` (the
#: default archive stays multi-account by design and is unchanged).
KIND_PLAY_PROFILE = "durable_play_profile"

ROLE_STORE_KINDS = (KIND_MEMORY, KIND_PERSISTENT, KIND_FRESH_TEST, KIND_PLAY_PROFILE)

#: The provenance of a story-completion row.  It is explicitly a *local
#: simulator* record of what this client did on this archive -- never a claim
#: about what the historical CN server persisted, which was never recovered.
STORY_ORIGIN_LOCAL_OBSERVATION = "revival_local_c2s233_successor_evidence"

#: The one evidence shape this build accepts, named so a reader never has to
#: infer it: the client sent ``C2S 233`` for a story and then, in the same
#: session, the ``C2S 233`` of the story its own close callback chains into.
STORY_EVIDENCE_ORDERED_SUCCESSOR = "c2s233_ordered_successor"

#: The provenance of a bag-sort row (TASK-0084).  Like the story rows it is a
#: *local simulator* record of what this client sent to this archive -- never a
#: claim about what the historical CN server persisted, which was never
#: recovered.
BAG_SORT_ORIGIN_LOCAL_OBSERVATION = "revival_local_c2s512_bag_sort_state"

#: The one evidence shape this build accepts for a bag-sort row: the client's
#: own ``C2S 512`` for one sort slot, carrying the packed sort value it had
#: already written into its local ``le_tbBagSort`` table before sending.
BAG_SORT_EVIDENCE_CLIENT_C2S = "c2s512_bag_sort_state"

#: The provenance of a chapter-fight clear row (TASK-0174).  Like the story and
#: bag rows it is a *local simulator* record of what this client did on this
#: archive -- never a claim about what the historical CN server persisted.
#:
#: The record is one **settlement transaction observed on one connection**: the
#: client's own ``C2S 69 netEnterPveBattle`` (which names the chapter, the fight
#: and the chapter-map index), the ``C2S 67 battlePveGameOver`` that closes that
#: very battle, and the server's one ``S2C 96 battlePveGameOverEnd`` reply.  No
#: reward, drop, time-of-day or progress number is stored, and the row says
#: nothing the wire did not carry.
CHAPTER_FIGHT_ORIGIN_LOCAL_OBSERVATION = (
    "revival_local_c2s69_c2s67_s2c96_settlement"
)

#: The one accepted evidence shape, named so no reader has to infer it.
CHAPTER_FIGHT_EVIDENCE_SETTLED_TRANSACTION = "c2s69_then_c2s67_with_s2c96_reply"

#: The provenance of a chapter-map event clear row (TASK-0184).  Like the story,
#: bag and chapter-fight rows it is a *local simulator* record of what this
#: client did on this archive -- never a claim about what the historical CN
#: server persisted.  The record is one client action observed on one
#: connection: the client's own ``C2S 224 chapterMapEvent`` for a recovered
#: chapter-map event node and this server's single ``S2C 398 chapterMapEvent``
#: success answer.  No reward, drop or completion time is stored.
CHAPTER_EVENT_ORIGIN_LOCAL_OBSERVATION = "revival_local_c2s224_s2c398_event"

#: The one accepted evidence shape, named so no reader has to infer it.
CHAPTER_EVENT_EVIDENCE_CLIENT_ACTION = "c2s224_then_s2c398_success_reply"

#: `pkgGuankaMapConstMgr.MAP_TYPE_EVENT` (source line 30 of the shipped const
#: chunk).  It is the ``dType`` of the replayed ``S2C 390`` row; the focused
#: tests assert it equals ``chapter_map_event.MAP_TYPE_EVENT``.
CHAPTER_EVENT_REPLAY_MAP_TYPE = 13

#: The six crystal buckets the client's own S2C 10 handler writes
#: (`pkgPlayerInfoMgr.updateCrystalDetail`, source lines 111-118).  A reward's
#: bucket may only be one of these or SQL NULL; the shipped `guankaReward` row
#: carries a single scalar `crystal` and says nothing about which bucket an
#: event reward enters, so this archive stores NULL and says so.
CRYSTAL_BUCKET_NAMES = (
    "crystal_rmb",
    "crystal_rmb_bind",
    "crystal_ex",
    "crystal_ex_bind",
    "crystal_free",
    "crystal_free_bind",
)

#: The only bucket provenance this slice can honestly write: the reward row is
#: recovered, but the bucket is not.
CRYSTAL_BUCKET_PROVENANCE_UNKNOWN = "unknown"
CRYSTAL_BUCKET_PROVENANCES = (
    CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
    "official_recovered",
    "deterministic_recovered",
)

#: Provenance of one reward-ledger row.  The value comes from the shipped
#: `guankaReward[25101].crystal = 10` row (`official_recovered`); the rule that
#: one observed event action records one reward (`deterministic_recovered`); the
#: time is this archive's own transaction/migration clock, never a
#: reconstructed historical completion time.
CHAPTER_EVENT_REWARD_ORIGIN_LOCAL_OBSERVATION = (
    "revival_local_c2s224_s2c398_event_reward"
)
CHAPTER_EVENT_REWARD_ORIGIN_SCHEMA_BACKFILL = "revival_local_schema_v12_backfill"
CHAPTER_EVENT_REWARD_PROVENANCE_TRANSACTION = (
    "value=official_recovered;rule=deterministic_recovered;"
    "time=revival_local_transaction_time"
)
CHAPTER_EVENT_REWARD_PROVENANCE_MIGRATION = (
    "value=official_recovered;rule=deterministic_recovered;"
    "time=revival_local_migration_time"
)

#: Structure version 5: one row per card instance this archive serves at login.
#: The row mirrors the client's own 21-field ``returnCardInfo`` payload.  A row
#: labelled with this origin is a **deterministic test fixture** used only by the
#: explicit Owner draw-test overlay; it is not an original-server card, not a
#: probability result, and it is never replayed without the overlay flag.
CARD_INSTANCE_ORIGIN_DETERMINISTIC_TEST_FIXTURE = (
    "revival_local_deterministic_test_fixture"
)
CARD_INSTANCE_FIXTURE_KIND_DETERMINISTIC_DRAW = "deterministic_draw_result"

#: A card granted by a recovered `guankaMapFight.fightRewardId` row (see
#: `recovered_rewards.py`).  Unlike a fixture row it replays on every login,
#: without the Owner draw-test overlay.
CARD_INSTANCE_ORIGIN_RECOVERED_FIGHT_REWARD = (
    "revival_local_recovered_fight_reward"
)

#: The persisted wallet names this build knows.  They are the reward-type
#: symbols in `reports/role_value_wire_mapping.csv` whose wire slots the
#: client's S2C 27 vector carries: money (index 0), yinPo (index 6), jinPo
#: (index 7).  A name outside this closed set is refused rather than stored.
WALLET_MONEY = "money"
WALLET_YINPO = "yinpo"
WALLET_JINPO = "jinpo"
WALLET_VALUE_NAMES = (WALLET_MONEY, WALLET_YINPO, WALLET_JINPO)

#: The provenance written by the client's own card split: every amount comes
#: from the shipped `cardSplit.max` row of the card that was consumed.
WALLET_ORIGIN_CARD_SPLIT = "revival_local_card_split_reward"

#: TASK-0266.  The two role-value names the recovered `cfgGoods` 376 row
#: (花呗祈灵符) reads and writes: the item count the bag renders (`goodsUse6`,
#: S2C 27 wire index 157 / reward type 376) and the effect it grants
#: (`lotteryTicket`, wire index 18 / reward type 252).  Both names come from
#: `reports/task_0265_bag_goods_use_contract.json` (`minimal_slice`) and
#: `reports/role_value_wire_mapping.csv:20,159`; neither is part of the frozen
#: money/银魄/金魄 wallet set, so the wallet writers keep refusing them.
ROLE_VALUE_GOODS_USE = "goodsUse6"
ROLE_VALUE_LOTTERY_TICKET = "lotteryTicket"
GOODS_USE_VALUE_NAMES = (ROLE_VALUE_GOODS_USE, ROLE_VALUE_LOTTERY_TICKET)

#: TASK-0266.  The role-scoped `meta` prefix holding the two goods-use counts.
#: The frozen structure-9 `role_wallet` table declares
#: `CHECK (value_name IN ('money', 'yinpo', 'jinpo'))` (`role_store.py` schema
#: v9), so a name outside that closed set cannot be stored there without a
#: structure change, and `SCHEMA_VERSION = 12` is frozen by existing tests and
#: archives.  A generic RoleValue table (or a rebuilt `role_wallet` CHECK) is a
#: later, separate migration; this slice rides the archive's existing
#: key-value `meta` table exactly like `CARD_ADVANCE_META_PREFIX` does, so
#: every older archive opens unchanged and consumes no new structure version.
#: Every key is `<prefix><account_key>:<value_name>` and every value is one
#: non-negative integer written as decimal text (the S2C 27 N fields are
#: uint32).  Provenance is not stored (the table has no column for it); the
#: writer returns it and the transport event carries it at write time.
GOODS_USE_META_PREFIX = "goods_use_count:"

#: The provenance written by the local C2S 429 handler when it persists one use.
GOODS_USE_ORIGIN_LOCAL_C2S = "revival_local_goods_use_c2s"

#: The archive's existing ``meta`` key holding the *latest committed* card
#: advance of one account.  The value is one bounded JSON object; the key is
#: suffixed with the account's salted key, so it is role-scoped without a new
#: table.  Its purpose is the client's own ``ADVANCE_CARD`` sync counter: a
#: transport resend of the latest ``C2S 21`` must replay success without charging
#: twice.  Older counters are refused rather than replayed, which also charges
#: nothing.  Because it rides an existing table, every older archive opens
#: unchanged and no schema version is consumed.
CARD_ADVANCE_META_PREFIX = "card_advance_last:"

#: The archive's own bookkeeping keys.  The generic ``get_meta``/``set_meta``/
#: ``delete_meta`` helpers (added for the TASK-0232 dev-play overlay) refuse
#: these: a caller may store its own namespaced value, but may never rewrite the
#: structure marker, the account-key salt/derivation or the archive birth time.
RESERVED_META_KEYS = frozenset(
    {
        "schema_version",
        "account_key_salt",
        "account_key_derivation",
        "created_at",
    }
)

CARD_ADVANCE_KEYS = (
    "role_id",
    "server_id",
    "sync_counter",
    "instance_id",
    "advance_level_from",
    "advance_level_to",
    "money_spent",
    "yinpo_spent",
    "jinpo_spent",
    "fodder_ids",
    "recorded_at",
    "origin",
)

#: The client's own team shape: five presets (`pkgTeamDataMgr.dMaxTeam = 5`),
#: eleven card slots per preset (S2C 37/40 carry `strID1..strID11`), and an
#: editable C2S 6 format of four equipment ids plus ten card ids.  Slot 11 is
#: the client's level-999 slot (`TB_CARD_OPEN_LEVEL[11] = 999`) and is never
#: sent by the editor.
TEAM_MAX_GROUPS = 5
TEAM_CARD_SLOTS = 11
TEAM_EDITABLE_CARD_SLOTS = 10
TEAM_EQUIP_SLOTS = 4

#: Provenance of a team row written by the client's own C2S 6, and of the
#: current preset written by C2S 5.  These are player actions, not recovered
#: historical server state.
TEAM_RECORD_ORIGIN_LOCAL_C2S_UPDATE = "revival_local_c2s_update_team"
TEAM_CURRENT_GROUP_ORIGIN_LOCAL_C2S = "revival_local_c2s_change_team_group"

#: Suffix of the pre-migration snapshot a structural upgrade writes first.
#: Named after the structure being copied so a directory can hold the snapshot
#: of more than one upgrade step without ambiguity.
MIGRATION_SNAPSHOT_SUFFIX = ".pre-migration-v{version}.bak"

_SALT_BYTES = 32

#: Structure version 1: identity only.
_BASE_SCHEMA_STATEMENTS = (
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
        CHECK (server_id > 0 AND server_id < %d),
        CHECK (role_id > 0),
        -- Same invariant MinimalRoleState enforces in memory and the client
        -- re-derives with math.fmod(roleId, 100000): the role ID's decimal
        -- suffix is the server ID.
        CHECK (role_id %% %d = server_id)
    )
    """ % (ROLE_ID_SERVER_FACTOR, ROLE_ID_SERVER_FACTOR),
    """
    CREATE TABLE role_serials (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        next_serial INTEGER NOT NULL
    )
    """,
)

#: Structure version 2: one row per *observed* story completion.  Exactly one
#: statement, so the create path and the version-1 migration cannot drift: the
#: migration adds precisely this table and nothing else.
#:
#: The primary key is ``(account_key, story_id)``: one account has one role per
#: archive (``roles.account_key`` is the primary key), so the pair names both
#: the player and the story.  ``role_id``/``server_id`` are stored as well so a
#: row can never be read without the role it belongs to, and the same
#: role-ID-suffix invariant the ``roles`` table enforces is enforced here too.
#: The evidence columns are the *raw* observation: which pair of ids proved it,
#: on which packet sequences, and when it was seen -- never a reconstructed
#: historical completion time.
_STORY_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE story_completions (
        account_key          TEXT    NOT NULL,
        role_id              INTEGER NOT NULL,
        server_id            INTEGER NOT NULL,
        story_id             INTEGER NOT NULL,
        state                INTEGER NOT NULL,
        observed_at          INTEGER NOT NULL,
        evidence_kind        TEXT    NOT NULL,
        evidence_story_id    INTEGER NOT NULL,
        predecessor_sequence INTEGER NOT NULL,
        successor_sequence   INTEGER NOT NULL,
        recorded_at          INTEGER NOT NULL,
        origin               TEXT    NOT NULL,
        PRIMARY KEY (account_key, story_id),
        CHECK (story_id > 0),
        CHECK (role_id > 0),
        CHECK (server_id > 0 AND server_id < %d),
        CHECK (role_id %% %d = server_id)
    )
    """ % (ROLE_ID_SERVER_FACTOR, ROLE_ID_SERVER_FACTOR),
)

#: Structure version 3: one row per *observed* bag sort slot (TASK-0084).
#: Exactly one statement, so the create path and the version-2 migration cannot
#: drift: the migration adds precisely this table and nothing else.
#:
#: The primary key is ``(account_key, ops)``: one account has one role per
#: archive, and ``ops`` is the client's own sort-slot index (``le_tbBagSort``
#: key).  A sort is the player's *current* setting, not an immutable historical
#: fact, so a later observation of the same slot replaces the value -- unlike
#: ``story_completions``, where the first observation is the fact.
#: ``observed_at`` is when this server saw the client's packet and
#: ``packet_sequence`` is the sequence of that packet, so the row can never be
#: read without the observation it came from.
_BAG_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE bag_sort_states (
        account_key     TEXT    NOT NULL,
        role_id         INTEGER NOT NULL,
        server_id       INTEGER NOT NULL,
        ops             INTEGER NOT NULL,
        sort            INTEGER NOT NULL,
        observed_at     INTEGER NOT NULL,
        packet_sequence INTEGER NOT NULL,
        recorded_at     INTEGER NOT NULL,
        origin          TEXT    NOT NULL,
        PRIMARY KEY (account_key, ops),
        CHECK (ops > 0),
        CHECK (sort >= 0),
        CHECK (role_id > 0),
        CHECK (server_id > 0 AND server_id < %d),
        CHECK (role_id %% %d = server_id)
    )
    """ % (ROLE_ID_SERVER_FACTOR, ROLE_ID_SERVER_FACTOR),
)

#: Structure version 4: one row per *observed* chapter-map fight clear
#: (TASK-0174).  Exactly one statement, so the create path and the
#: version-3 migration cannot drift.
#:
#: ``(account_key, chapter_id, fight_id)`` is the key the task names: one
#: account has one role per archive, and the client's own ``C2S 69`` carries the
#: chapter (``p4``) and the fight (``p1``) of the battle it starts.  ``map_index``
#: is the chapter-map key (``p2``, the client's own ``dIndex``) that the replayed
#: ``initChapterItemInfo`` row must carry -- the client's reader
#: ``pkgChapterInfoMgr.isItemFightOver`` looks its entry up by exactly
#: ``itemFightId(dX, dY)``, and the shipped constant module defines that as the
#: first argument.  ``request_sequence``/``settlement_sequence``/
#: ``reply_sequence`` are the three wire packets of ONE connection, in order;
#: ``observed_at`` is when this server saw the settlement (never a
#: reconstructed historical completion time); ``origin`` says the row is a local
#: observation of that transaction.
_CHAPTER_FIGHT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE chapter_fight_clears (
        account_key         TEXT    NOT NULL,
        role_id             INTEGER NOT NULL,
        server_id           INTEGER NOT NULL,
        chapter_id          INTEGER NOT NULL,
        fight_id            INTEGER NOT NULL,
        map_index           INTEGER NOT NULL,
        observed_at         INTEGER NOT NULL,
        request_sequence    INTEGER NOT NULL,
        settlement_sequence INTEGER NOT NULL,
        reply_sequence      INTEGER NOT NULL,
        recorded_at         INTEGER NOT NULL,
        origin              TEXT    NOT NULL,
        PRIMARY KEY (account_key, chapter_id, fight_id),
        CHECK (chapter_id > 0),
        CHECK (fight_id > 0),
        CHECK (map_index > 0),
        CHECK (role_id > 0),
        CHECK (server_id > 0 AND server_id < %d),
        CHECK (role_id %% %d = server_id)
    )
    """ % (ROLE_ID_SERVER_FACTOR, ROLE_ID_SERVER_FACTOR),
)

#: Structure version 5: one row per card instance.  Exactly one statement, so
#: the create path and the version-4 migration cannot drift.  The columns are
#: the client's own 21-value ``returnCardInfo`` row (``aNNNNNNNNaaaayNNNNNaa``),
#: plus the role identity, a local provenance and an optional fixture label.
#: ``dSumExp``/``bNew`` are client-derived and are deliberately not stored.
_CARD_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE card_instances (
        account_key     TEXT    NOT NULL,
        role_id         INTEGER NOT NULL,
        server_id       INTEGER NOT NULL,
        instance_id     TEXT    NOT NULL,
        card_cfg_id     INTEGER NOT NULL,
        level           INTEGER NOT NULL,
        exp             INTEGER NOT NULL,
        advance_level   INTEGER NOT NULL,
        sfm_id1         INTEGER NOT NULL,
        sfm_id2         INTEGER NOT NULL,
        sfm_id3         INTEGER NOT NULL,
        sfm_id4         INTEGER NOT NULL,
        equip_id1       TEXT    NOT NULL,
        equip_id2       TEXT    NOT NULL,
        equip_id3       TEXT    NOT NULL,
        equip_id4       TEXT    NOT NULL,
        locked          INTEGER NOT NULL CHECK (locked IN (0, 1)),
        refine_lv       INTEGER NOT NULL,
        refine_lv_times INTEGER NOT NULL,
        learn_atk       INTEGER NOT NULL,
        learn_hp        INTEGER NOT NULL,
        learn_state     INTEGER NOT NULL,
        refine_skill    TEXT    NOT NULL,
        god_weapon_id   TEXT    NOT NULL,
        origin          TEXT    NOT NULL,
        fixture_id      TEXT,
        fixture_kind    TEXT,
        recorded_at     INTEGER NOT NULL,
        PRIMARY KEY (account_key, instance_id),
        CHECK (instance_id <> ''),
        CHECK (card_cfg_id > 0),
        CHECK (role_id > 0),
        CHECK (server_id > 0 AND server_id < %d),
        CHECK (role_id %% %d = server_id),
        CHECK (fixture_kind IS NULL OR fixture_kind = 'deterministic_draw_result')
    )
    """ % (ROLE_ID_SERVER_FACTOR, ROLE_ID_SERVER_FACTOR),
)

#: Structure version 6: the advanced-pool pity counters.  One row per role and
#: pool; ``normal`` is stored too even though the documented normal pool has no
#: pity, so the schema does not encode that assumption.
_DRAW_PITY_V6_STATEMENTS = (
    """
    CREATE TABLE draw_pity (
        account_key  TEXT    NOT NULL,
        pool         TEXT    NOT NULL,
        since_legend INTEGER NOT NULL,
        since_rare   INTEGER NOT NULL,
        updated_at   INTEGER NOT NULL,
        provenance   TEXT    NOT NULL,
        PRIMARY KEY (account_key, pool),
        CHECK (pool IN ('normal', 'advanced')),
        CHECK (since_legend >= 0),
        CHECK (since_rare >= 0)
    )
    """,
)

#: Structure version 7: the limited pool shares one legend pity across banners,
#: and each banner keeps its own consecutive-non-featured-legend counter.
_DRAW_PITY_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE draw_pity (
        account_key  TEXT    NOT NULL,
        pool         TEXT    NOT NULL,
        since_legend INTEGER NOT NULL,
        since_rare   INTEGER NOT NULL,
        updated_at   INTEGER NOT NULL,
        provenance   TEXT    NOT NULL,
        PRIMARY KEY (account_key, pool),
        CHECK (pool IN ('normal', 'advanced', 'limited')),
        CHECK (since_legend >= 0),
        CHECK (since_rare >= 0)
    )
    """,
)

_HOTSPOT_PITY_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE hotspot_draw_pity (
        account_key           TEXT    NOT NULL,
        hotspot_id            INTEGER NOT NULL,
        since_featured_legend INTEGER NOT NULL,
        updated_at            INTEGER NOT NULL,
        provenance            TEXT    NOT NULL,
        PRIMARY KEY (account_key, hotspot_id),
        CHECK (hotspot_id > 0),
        CHECK (since_featured_legend >= 0)
    )
    """,
)

#: Structure version 8: the player's own team presets, exactly in the client's
#: S2C 37/40 record shape (four equipment ids, one god-weapon id, eleven card
#: slots), plus which preset is current (S2C 35).  Card ids are instance ids
#: from `card_instances`; the store validates ownership before writing.
_TEAM_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE team_presets (
        account_key   TEXT    NOT NULL,
        group_id      INTEGER NOT NULL,
        equip_id1     TEXT    NOT NULL,
        equip_id2     TEXT    NOT NULL,
        equip_id3     TEXT    NOT NULL,
        equip_id4     TEXT    NOT NULL,
        god_weapon_id TEXT    NOT NULL,
        card_id1      TEXT    NOT NULL,
        card_id2      TEXT    NOT NULL,
        card_id3      TEXT    NOT NULL,
        card_id4      TEXT    NOT NULL,
        card_id5      TEXT    NOT NULL,
        card_id6      TEXT    NOT NULL,
        card_id7      TEXT    NOT NULL,
        card_id8      TEXT    NOT NULL,
        card_id9      TEXT    NOT NULL,
        card_id10     TEXT    NOT NULL,
        card_id11     TEXT    NOT NULL,
        origin        TEXT    NOT NULL,
        updated_at    INTEGER NOT NULL,
        PRIMARY KEY (account_key, group_id),
        CHECK (group_id BETWEEN 1 AND 5)
    )
    """,
    """
    CREATE TABLE team_state (
        account_key   TEXT    PRIMARY KEY,
        current_group INTEGER NOT NULL,
        origin        TEXT    NOT NULL,
        updated_at    INTEGER NOT NULL,
        CHECK (current_group BETWEEN 1 AND 5)
    )
    """,
)

_SCHEMA_V7_STATEMENTS = (
    _BASE_SCHEMA_STATEMENTS
    + _STORY_SCHEMA_STATEMENTS
    + _BAG_SCHEMA_STATEMENTS
    + _CHAPTER_FIGHT_SCHEMA_STATEMENTS
    + _CARD_SCHEMA_STATEMENTS
    + _DRAW_PITY_SCHEMA_STATEMENTS
    + _HOTSPOT_PITY_SCHEMA_STATEMENTS
)

#: Structure version 9: the card-growth economy.  ``card_special_inventory`` is
#: the client's ``pkgSpecialCardDataMgr`` (dog-food special cards, cfg ids 1-5);
#: ``role_wallet`` holds the persisted reward-type amounts the client mirrors in
#: its S2C 27 value vector (money / 银魄 / 金魄).  Both are additive: an older
#: archive keeps every row and only gains the two tables.
_GROWTH_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE card_special_inventory (
        account_key TEXT    NOT NULL,
        cfg_id      INTEGER NOT NULL,
        count       INTEGER NOT NULL,
        provenance  TEXT    NOT NULL,
        updated_at  INTEGER NOT NULL,
        PRIMARY KEY (account_key, cfg_id),
        CHECK (cfg_id > 0),
        CHECK (count >= 0)
    )
    """,
    """
    CREATE TABLE role_wallet (
        account_key TEXT    NOT NULL,
        value_name  TEXT    NOT NULL,
        amount      REAL    NOT NULL,
        provenance  TEXT    NOT NULL,
        updated_at  INTEGER NOT NULL,
        PRIMARY KEY (account_key, value_name),
        CHECK (value_name IN ('money', 'yinpo', 'jinpo')),
        CHECK (amount >= 0)
    )
    """,
)

#: Structure version 10: one row per chapter-map event node a client action
#: completed.  The row is the client's own ``S2C 390 initChapterItemInfo``
#: triple: ``dChapterId`` and ``dIndex`` come from the client's ``C2S 224``
#: request, and ``dType`` is the shipped ``MAP_TYPE_EVENT = 13``.  The extra
#: columns hold the event config id (from the shipped chapter-map row) and the
#: option slot the player pressed, so the record says exactly which action was
#: observed.  ``observed_at`` is when this server saw the request; no reward,
#: drop or historical completion time is stored.  Purely additive: an older
#: archive keeps every row and only gains the table.
_CHAPTER_EVENT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE chapter_event_clears (
        account_key      TEXT    NOT NULL,
        role_id          INTEGER NOT NULL,
        server_id        INTEGER NOT NULL,
        chapter_id       INTEGER NOT NULL,
        map_index        INTEGER NOT NULL,
        event_cfg_id     INTEGER NOT NULL,
        option_index     INTEGER NOT NULL,
        observed_at      INTEGER NOT NULL,
        request_sequence INTEGER NOT NULL,
        reply_sequence   INTEGER NOT NULL,
        recorded_at      INTEGER NOT NULL,
        origin           TEXT    NOT NULL,
        PRIMARY KEY (account_key, chapter_id, map_index),
        CHECK (chapter_id > 0),
        CHECK (map_index > 0),
        CHECK (event_cfg_id > 0),
        CHECK (option_index >= 0),
        CHECK (role_id > 0),
        CHECK (server_id > 0 AND server_id < %d),
        CHECK (role_id %% %d = server_id)
    )
    """ % (ROLE_ID_SERVER_FACTOR, ROLE_ID_SERVER_FACTOR),
)

#: Structure version 11: one row per chapter milestone chest the client opened
#: with ``C2S 222 chapterAcceptBox``.  The row is the client's own
#: ``S2C 382 initChapterBoxInfo(dChapterId, dBoxIndex)`` pair plus the shipped
#: ``guankaReward`` row it granted; ``accepted_at``/``reply_sequence`` are the
#: transaction's own facts, never a reconstructed historical time.  The primary
#: key makes "the same chest is granted once per role" a database fact, not a
#: caller's promise.  Purely additive: an older archive keeps every row and
#: only gains the table.
_CHAPTER_BOX_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE chapter_box_accepts (
        account_key    TEXT    NOT NULL,
        role_id        INTEGER NOT NULL,
        chapter_id     INTEGER NOT NULL,
        box_index      INTEGER NOT NULL,
        reward_id      INTEGER NOT NULL,
        accepted_at    INTEGER NOT NULL,
        reply_sequence INTEGER NOT NULL,
        origin         TEXT    NOT NULL,
        PRIMARY KEY (account_key, role_id, chapter_id, box_index),
        CHECK (chapter_id > 0),
        CHECK (box_index >= 0),
        CHECK (reward_id > 0),
        CHECK (role_id > 0)
    )
    """,
)

#: Structure version 12: one row per event reward whose authority this archive
#: has recovered, written in the same transaction as the event clear it belongs
#: to.  The row records the shipped ``guankaReward[row].crystal`` value, the
#: reward id, and the crystal bucket only when a bucket is recoverable: the
#: shipped reward row has a single scalar ``crystal`` and no bucket column, so
#: this slice stores SQL NULL with ``bucket_provenance='unknown'`` and never
#: claims the client's visible balance moved.  ``granted_at`` is this archive's
#: own clock (transaction time, or migration time for a backfill), never a
#: reconstructed historical time.  Purely additive: an older archive keeps every
#: row and only gains the table; ``role_wallet`` is not touched.
_CRYSTAL_BUCKET_SQL = ", ".join(f"'{name}'" for name in CRYSTAL_BUCKET_NAMES)
_CRYSTAL_BUCKET_PROVENANCE_SQL = ", ".join(
    f"'{name}'" for name in CRYSTAL_BUCKET_PROVENANCES
)

_CHAPTER_EVENT_REWARD_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE chapter_event_reward_grants (
        account_key       TEXT    NOT NULL,
        role_id           INTEGER NOT NULL,
        server_id         INTEGER NOT NULL,
        chapter_id        INTEGER NOT NULL,
        map_index         INTEGER NOT NULL,
        event_cfg_id      INTEGER NOT NULL,
        option_index      INTEGER NOT NULL,
        reward_id         INTEGER NOT NULL,
        crystal           INTEGER NOT NULL,
        crystal_bucket    TEXT,
        bucket_provenance TEXT    NOT NULL,
        granted_at        INTEGER NOT NULL,
        request_sequence  INTEGER NOT NULL,
        reply_sequence    INTEGER NOT NULL,
        origin            TEXT    NOT NULL,
        provenance        TEXT    NOT NULL,
        PRIMARY KEY (account_key, chapter_id, map_index),
        CHECK (chapter_id > 0),
        CHECK (map_index > 0),
        CHECK (event_cfg_id > 0),
        CHECK (option_index >= 0),
        CHECK (role_id > 0),
        CHECK (server_id > 0 AND server_id < %d),
        CHECK (role_id %% %d = server_id),
        CHECK (reward_id > 0),
        CHECK (crystal > 0),
        CHECK (granted_at > 0),
        CHECK (crystal_bucket IS NULL OR crystal_bucket IN (%s)),
        CHECK (bucket_provenance IN (%s))
    )
    """
    % (
        ROLE_ID_SERVER_FACTOR,
        ROLE_ID_SERVER_FACTOR,
        _CRYSTAL_BUCKET_SQL,
        _CRYSTAL_BUCKET_PROVENANCE_SQL,
    ),
)

_SCHEMA_STATEMENTS_V11 = (
    _SCHEMA_V7_STATEMENTS
    + _TEAM_SCHEMA_STATEMENTS
    + _GROWTH_SCHEMA_STATEMENTS
    + _CHAPTER_EVENT_SCHEMA_STATEMENTS
    + _CHAPTER_BOX_SCHEMA_STATEMENTS
)

_SCHEMA_STATEMENTS = (
    _SCHEMA_STATEMENTS_V11 + _CHAPTER_EVENT_REWARD_SCHEMA_STATEMENTS
)

#: What every structure version must physically hold, expressed in the *same*
#: statement tuples the create path builds from.  One source of truth, so "what
#: a new archive is born with", "what a migration adds" and "what an integrity
#: check demands of a marker" cannot drift apart.  A version with no entry here
#: is refused rather than assumed, so a future bump fails closed until it is
#: named here.
_REWARD_STORAGE_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS role_values ("
    " account_key TEXT NOT NULL,"
    " value_name TEXT NOT NULL,"
    " amount INTEGER NOT NULL,"
    " provenance TEXT NOT NULL,"
    " updated_at INTEGER NOT NULL,"
    " PRIMARY KEY (account_key, value_name),"
    " CHECK (amount >= 0)"
    ")",
    "CREATE TABLE IF NOT EXISTS card_pieces ("
    " account_key TEXT NOT NULL,"
    " card_cfg_id INTEGER NOT NULL,"
    " count INTEGER NOT NULL,"
    " provenance TEXT NOT NULL,"
    " updated_at INTEGER NOT NULL,"
    " PRIMARY KEY (account_key, card_cfg_id),"
    " CHECK (card_cfg_id > 0),"
    " CHECK (count >= 0)"
    ")",
)


#: TASK-0397: god-weapon instances.  The artifact shop sells 6-star 神器 by gdId and a
#: random-artifact box must resolve into one; a weapon is instance-keyed in the client
#: (pkgGodWeaponMgr.le_tbGodWeapons), so the archive needs the same identity.
_GOD_WEAPON_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS god_weapon_instances ("
    " account_key TEXT NOT NULL,"
    " role_id INTEGER NOT NULL,"
    " instance_id TEXT NOT NULL,"
    " cfg_id INTEGER NOT NULL,"
    " level INTEGER NOT NULL,"
    " exp INTEGER NOT NULL,"
    " star INTEGER NOT NULL,"
    " provenance TEXT NOT NULL,"
    " created_at INTEGER NOT NULL,"
    " PRIMARY KEY (account_key, instance_id),"
    " CHECK (cfg_id > 0)"
    ")",
)


_STRUCTURE_STATEMENTS: Mapping[int, tuple[str, ...]] = {
    1: _BASE_SCHEMA_STATEMENTS,
    2: _BASE_SCHEMA_STATEMENTS + _STORY_SCHEMA_STATEMENTS,
    3: _BASE_SCHEMA_STATEMENTS + _STORY_SCHEMA_STATEMENTS + _BAG_SCHEMA_STATEMENTS,
    4: (
        _BASE_SCHEMA_STATEMENTS
        + _STORY_SCHEMA_STATEMENTS
        + _BAG_SCHEMA_STATEMENTS
        + _CHAPTER_FIGHT_SCHEMA_STATEMENTS
    ),
    5: (
        _BASE_SCHEMA_STATEMENTS
        + _STORY_SCHEMA_STATEMENTS
        + _BAG_SCHEMA_STATEMENTS
        + _CHAPTER_FIGHT_SCHEMA_STATEMENTS
        + _CARD_SCHEMA_STATEMENTS
    ),
    6: (
        _BASE_SCHEMA_STATEMENTS
        + _STORY_SCHEMA_STATEMENTS
        + _BAG_SCHEMA_STATEMENTS
        + _CHAPTER_FIGHT_SCHEMA_STATEMENTS
        + _CARD_SCHEMA_STATEMENTS
        + _DRAW_PITY_V6_STATEMENTS
    ),
    7: _SCHEMA_V7_STATEMENTS,
    8: _SCHEMA_V7_STATEMENTS + _TEAM_SCHEMA_STATEMENTS,
    9: _SCHEMA_V7_STATEMENTS
    + _TEAM_SCHEMA_STATEMENTS
    + _GROWTH_SCHEMA_STATEMENTS,
    10: (
        _SCHEMA_V7_STATEMENTS
        + _TEAM_SCHEMA_STATEMENTS
        + _GROWTH_SCHEMA_STATEMENTS
        + _CHAPTER_EVENT_SCHEMA_STATEMENTS
    ),
    11: _SCHEMA_STATEMENTS_V11,
    12: _SCHEMA_STATEMENTS,
    13: _SCHEMA_STATEMENTS + _REWARD_STORAGE_STATEMENTS,
    14: _SCHEMA_STATEMENTS + _REWARD_STORAGE_STATEMENTS + _GOD_WEAPON_STATEMENTS,
}

_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.IGNORECASE
)
_COLUMN_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(TEXT|INTEGER|REAL|BLOB|NUMERIC)\b",
    re.IGNORECASE,
)
#: Line openers of a table constraint, not of a column definition.
_COLUMN_CONSTRAINT_KEYWORDS = frozenset(
    {"PRIMARY", "CHECK", "UNIQUE", "FOREIGN", "CONSTRAINT"}
)


def _declared_structure(
    statements: tuple[str, ...],
) -> Mapping[str, tuple[str, ...]]:
    """``{table: (column, ...)}`` as declared by a tuple of CREATE statements."""

    structure: dict[str, tuple[str, ...]] = {}
    for statement in statements:
        head = _CREATE_TABLE_RE.match(statement)
        if head is None:  # pragma: no cover - every statement is a CREATE TABLE
            continue
        columns = []
        for line in statement.splitlines():
            match = _COLUMN_RE.match(line)
            if match is None:
                continue
            if match.group(1).upper() in _COLUMN_CONSTRAINT_KEYWORDS:
                continue
            columns.append(match.group(1))
        structure[head.group(1)] = tuple(columns)
    return structure


#: ``version -> {table: (column, ...)}``, derived from the statements above.
_REQUIRED_STRUCTURE: Mapping[int, Mapping[str, tuple[str, ...]]] = {
    version: _declared_structure(statements)
    for version, statements in _STRUCTURE_STATEMENTS.items()
}


def _identity_facts_of(connection: sqlite3.Connection) -> dict[str, Any]:
    """``(salt, derivation, serial)`` as one open reads them -- one definition, used by
    the open itself and by the read-only inspection, so "what an archive must still be
    able to say about its accounts" cannot be two different questions.

    A ``None`` value means the fact is *absent*; the values are never compared here.
    The caller must have established that ``role_serials`` exists, because a missing
    table is a different refusal from a missing row.
    """

    serial = connection.execute("SELECT next_serial FROM role_serials").fetchone()
    return {
        "account_key_salt": _meta_of(connection, "account_key_salt"),
        "account_key_derivation": _meta_of(connection, "account_key_derivation"),
        "role_serials.next_serial": (
            None if serial is None else int(serial["next_serial"])
        ),
    }


def _meta_of(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM meta WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else str(row["value"])


def _table_names_of(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _table_columns_of(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _missing_structure(
    connection: sqlite3.Connection, version: int
) -> dict[str, tuple[str, ...]]:
    """What the given structure version demands and this database does not hold.

    ``{table: ()}`` means the table itself is absent; a non-empty tuple names the
    columns an existing table lacks.  The caller must have established that the
    version has a recovered definition (see :data:`_REQUIRED_STRUCTURE`).
    """

    present = _table_names_of(connection)
    missing: dict[str, tuple[str, ...]] = {}
    for table, columns in _REQUIRED_STRUCTURE[version].items():
        if table not in present:
            missing[table] = ()
            continue
        held = _table_columns_of(connection, table)
        lacking = tuple(column for column in columns if column not in held)
        if lacking:
            missing[table] = lacking
    return missing


def _describe_missing_structure(missing: Mapping[str, tuple[str, ...]]) -> str:
    """One boundable clause naming exactly what a version demands and is absent."""

    parts = []
    for table in sorted(missing):
        columns = missing[table]
        if not columns:
            parts.append(f"{table} (table absent)")
        else:
            parts.append(f"{table} (missing columns: {', '.join(columns)})")
    return "; ".join(parts)


class RoleStoreError(RuntimeError):
    """Base class for every role-store failure."""


class RoleStoreUnusableError(RoleStoreError):
    """The chosen archive exists but cannot be served safely.  Fail closed."""


class RoleStoreMigrationError(RoleStoreUnusableError):
    """The archive's structural version has no recovered upgrade path."""


def _read_write_uri(path: Path) -> str:
    """A ``file:`` URI that opens one *existing* database and never creates one.

    ``mode=rw`` makes SQLite refuse a database that is not there instead of
    starting an empty one, which is what a named play profile's ``resume``
    requires: the only way a profile's first archive may appear is the explicit
    ``init``.  The path is percent-encoded because a per-user directory may hold
    spaces or non-ASCII characters.
    """

    return "file:" + quote(str(path).replace("\\", "/"), safe="/:") + "?mode=rw"


def _read_only_uri(path: Path) -> str:
    """A ``file:`` URI that can only read: a verification never repairs."""

    return "file:" + quote(str(path).replace("\\", "/"), safe="/:") + "?mode=ro"


#: Every column of a role row, in a fixed order, for whole-archive comparisons.
_ROLE_ROW_COLUMNS = (
    "account_key",
    "server_id",
    "role_id",
    "role_name",
    "career",
    "create_time",
    "identity_origin",
    "compatibility_profile_id",
    "structure_version",
    "created_at",
    "updated_at",
)


def _role_row_tuples(connection: sqlite3.Connection) -> list[tuple]:
    """Every role row, ordered, so two databases can be compared as data."""

    rows = connection.execute(
        "SELECT " + ", ".join(_ROLE_ROW_COLUMNS) + " FROM roles ORDER BY account_key"
    ).fetchall()
    return [tuple(row) for row in rows]


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


@dataclass(frozen=True, slots=True)
class RoleRecord:
    """One durable account -> role binding, exactly as stored."""

    account_key: str
    server_id: int
    role_id: int
    role_name: str
    career: int
    create_time: int
    identity_origin: str
    compatibility_profile_id: str
    structure_version: int
    created_at: int
    updated_at: int

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def to_role_state(
        self,
        *,
        username: str,
        profile: CompatibilityProfile | None = None,
    ) -> MinimalRoleState:
        """Rebuild the in-memory role snapshot this record describes.

        ``username`` is supplied by the *current* login packet rather than read
        from the archive: the SDK username is deliberately not at rest here.
        The compatibility state is re-instantiated from ``(career, profile)``,
        the same deterministic constructor the create path used, so a running
        profile bump produces new compatibility values for the same durable
        role identity instead of editing stored ones.
        """

        selected = profile or get_profile()
        compatibility = instantiate_role_compatibility_state(
            self.career, profile=selected
        )
        return MinimalRoleState(
            username=username,
            role_id=self.role_id,
            role_name=self.role_name,
            career=self.career,
            create_time=self.create_time,
            server_id=self.server_id,
            compatibility=compatibility,
        )


@dataclass(frozen=True, slots=True)
class StoryRecord:
    """One observed story completion, exactly as stored.

    ``state`` is the client's own ``pkgGuideConst.STATE_FINISH`` and
    ``observed_at`` is when this server saw the evidence -- not a reconstructed
    historical completion time.  ``evidence_kind``, ``evidence_story_id`` and
    the two sequences are the raw observation the row was derived from.
    """

    account_key: str
    role_id: int
    server_id: int
    story_id: int
    state: int
    observed_at: int
    evidence_kind: str
    evidence_story_id: int
    predecessor_sequence: int
    successor_sequence: int
    recorded_at: int
    origin: str

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free block every story event carries."""

        return {
            "story_state": self.state,
            "story_observed_at": self.observed_at,
            "story_recorded_at": self.recorded_at,
            "story_evidence_kind": self.evidence_kind,
            "story_evidence_id": self.evidence_story_id,
            "story_evidence_sequences": (
                self.predecessor_sequence,
                self.successor_sequence,
            ),
            "story_completion_origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class BagSortRecord:
    """One observed bag sort slot, exactly as stored.

    ``ops`` is the client's own slot index (its recovered ``le_tbBagSort`` key)
    and ``sort`` is the *packed* value the client sent -- this build never
    unpacks it into race/rank/icon fields, because the client's reader does that
    itself with its own per-slot enum bounds.  ``observed_at`` is when this
    server saw the packet, not a historical setting time.
    """

    account_key: str
    role_id: int
    server_id: int
    ops: int
    sort: int
    observed_at: int
    packet_sequence: int
    recorded_at: int
    origin: str

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free block every bag event carries."""

        return {
            "bag_sort_ops": self.ops,
            "bag_sort_value": self.sort,
            "bag_sort_observed_at": self.observed_at,
            "bag_sort_packet_sequence": self.packet_sequence,
            "bag_sort_recorded_at": self.recorded_at,
            "bag_sort_origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class ChapterFightClearRecord:
    """One observed chapter-map fight settlement, exactly as stored.

    ``chapter_id``/``fight_id`` are the client's own ``C2S 69`` fields ``p4``
    and ``p1``; ``map_index`` is its ``p2`` (the chapter-map key).  The three
    sequences are the wire packets of the ONE connection the transaction was
    observed on: the battle-start request, the battle's own game-over report and
    this server's single answer.  ``observed_at`` is the settlement's arrival
    time here -- never a reconstructed completion time.
    """

    account_key: str
    role_id: int
    server_id: int
    chapter_id: int
    fight_id: int
    map_index: int
    observed_at: int
    request_sequence: int
    settlement_sequence: int
    reply_sequence: int
    recorded_at: int
    origin: str

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free block every chapter-fight event carries."""

        return {
            "chapter_fight_chapter_id": self.chapter_id,
            "chapter_fight_fight_id": self.fight_id,
            "chapter_fight_map_index": self.map_index,
            "chapter_fight_observed_at": self.observed_at,
            "chapter_fight_sequences": (
                self.request_sequence,
                self.settlement_sequence,
                self.reply_sequence,
            ),
            "chapter_fight_recorded_at": self.recorded_at,
            "chapter_fight_origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class ChapterFightClearOutcome:
    """The clear row plus the wallet values the same transaction observed.

    ``created`` is False for a replay; ``money_before``/``money_after`` are
    read inside the transaction either way, so an event can report the exact
    before/after pair instead of racing a second connection.  ``money_granted``
    is 0 on a replay or when the caller asked for no money.

    ``special_food_granted``/``special_counts_after`` are the same facts for
    the optional dog-food grant (``specialCard`` cfg id -> count): empty on a
    replay, and the inventory value read inside the transaction either way.
    """

    record: ChapterFightClearRecord
    created: bool
    money_before: float
    money_after: float
    money_granted: float
    special_food_granted: dict[int, int] = field(default_factory=dict)
    special_counts_after: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChapterEventClearRecord:
    """One observed chapter-map event action, exactly as stored.

    ``chapter_id``/``map_index`` are the client's own ``C2S 224`` fields 1 and
    2; ``event_cfg_id`` is the shipped chapter-map row's event config id and
    ``option_index`` is the option slot the client said the player pressed
    (field 4).  The two sequences are the wire packets of the ONE connection
    the action was observed on: the request and this server's single answer.
    ``observed_at`` is the request's arrival time here -- never a reconstructed
    completion time.
    """

    account_key: str
    role_id: int
    server_id: int
    chapter_id: int
    map_index: int
    event_cfg_id: int
    option_index: int
    observed_at: int
    request_sequence: int
    reply_sequence: int
    recorded_at: int
    origin: str

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def replay_row(self) -> tuple[int, int, int]:
        """The client's own `initChapterItemInfo` triple to replay at login."""

        return (self.chapter_id, CHAPTER_EVENT_REPLAY_MAP_TYPE, self.map_index)

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free block every chapter-event event carries."""

        return {
            "chapter_event_chapter_id": self.chapter_id,
            "chapter_event_map_index": self.map_index,
            "chapter_event_cfg_id": self.event_cfg_id,
            "chapter_event_option_index": self.option_index,
            "chapter_event_observed_at": self.observed_at,
            "chapter_event_sequences": (
                self.request_sequence,
                self.reply_sequence,
            ),
            "chapter_event_recorded_at": self.recorded_at,
            "chapter_event_origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class ChapterEventRewardGrantRecord:
    """One event reward's server-side authority, exactly as stored.

    ``reward_id``/``crystal``/``event_cfg_id`` are the shipped
    ``guankaReward[row]`` row the recovered event names; ``crystal_bucket`` is
    ``None`` unless a bucket was recoverable, and ``bucket_provenance`` says so
    instead of naming a guessed bucket.  ``granted_at`` is this archive's own
    clock (transaction time or, for a schema-12 backfill, migration time); it is
    never a reconstructed historical completion time.  The row is a *ledger*
    record: it says the server accepted the reward authority, not that the
    client's visible wardrobe of crystal buckets moved.
    """

    account_key: str
    role_id: int
    server_id: int
    chapter_id: int
    map_index: int
    event_cfg_id: int
    option_index: int
    reward_id: int
    crystal: int
    crystal_bucket: str | None
    bucket_provenance: str
    granted_at: int
    request_sequence: int
    reply_sequence: int
    origin: str
    provenance: str

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free ledger block every event carries."""

        return {
            "ledger_reward_id": self.reward_id,
            "ledger_crystal": self.crystal,
            "ledger_crystal_bucket": self.crystal_bucket,
            "ledger_bucket_provenance": self.bucket_provenance,
            "ledger_granted_at": self.granted_at,
            "ledger_origin": self.origin,
            "ledger_provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class ChapterEventClearOutcome:
    """The clear row and the reward-ledger row one transaction produced.

    ``created`` is true only for the first observation of the action;
    ``grant_created`` is true when this transaction wrote the ledger row (first
    observation, or a single backfill of a clear that had none).  A replay
    returns the stored rows with both flags false, so no caller can mistake a
    replay for a second grant.  ``ledger_total_after`` is the sum of this
    account's ledger ``crystal`` values -- never a wallet balance.
    """

    record: ChapterEventClearRecord
    created: bool
    grant: ChapterEventRewardGrantRecord
    grant_created: bool
    ledger_total_after: int


@dataclass(frozen=True, slots=True)
class ChapterBoxAcceptRecord:
    """One chapter milestone chest the client opened, exactly as stored.

    ``chapter_id``/``box_index`` are the client's own ``C2S 222`` fields 1 and
    2; ``reward_id`` is the shipped ``guankaChapter`` column the box names.
    ``accepted_at`` is when this server answered the request and
    ``reply_sequence`` is the one ``S2C 384`` that unlocked it.  Nothing here
    reconstructs a historical opening time.
    """

    account_key: str
    role_id: int
    chapter_id: int
    box_index: int
    reward_id: int
    accepted_at: int
    reply_sequence: int
    origin: str

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def replay_row(self) -> tuple[int, int]:
        """The client's own ``initChapterBoxInfo`` pair to replay at login."""

        return (self.chapter_id, self.box_index)

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free block every chapter-box event carries."""

        return {
            "chapter_box_chapter_id": self.chapter_id,
            "chapter_box_index": self.box_index,
            "chapter_box_reward_id": self.reward_id,
            "chapter_box_accepted_at": self.accepted_at,
            "chapter_box_reply_sequence": self.reply_sequence,
            "chapter_box_origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class ChapterTaskOutcome:
    """The result of one observed `C2S 227 chapterMapTask` action.

    ``state`` is one of :mod:`revival_stub.chapter_map_task`'s words
    (``received`` / ``completed`` / ``already_handled`` / ``not_received``).
    ``state_changed`` is true only when this transaction wrote the row, so a
    repeat receive or a repeat complete can never look like a new action.
    ``rows_after`` is the whole account's ordered row set after the call.
    """

    action: int
    state: str
    row: "chapter_map_task.ChapterTaskRow | None"
    rows_after: tuple["chapter_map_task.ChapterTaskRow", ...]
    created: bool
    state_changed: bool
    sync_count: int
    request_sequence: int
    reply_sequence: int
    observed_at: int

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded block the handler's own events carry."""

        return {
            "chapter_task_action": self.action,
            "chapter_task_action_name": chapter_map_task.TASK_ACTION_NAMES.get(
                self.action
            ),
            "chapter_task_state": self.state,
            "chapter_task_state_changed": self.state_changed,
            "chapter_task_row_created": self.created,
            "chapter_task_sync_count": self.sync_count,
            "chapter_task_request_sequence": self.request_sequence,
            "chapter_task_reply_sequence": self.reply_sequence,
            "chapter_task_observed_at": self.observed_at,
            "chapter_task_rows_after": len(self.rows_after),
        }


@dataclass(frozen=True, slots=True)
class CardInstanceRecord:
    """One stored card instance, exactly as the client's own card row.

    The fields are the client's 21-value ``returnCardInfo`` payload
    (``aNNNNNNNNaaaayNNNNNaa``).  ``dSumExp`` and ``bNew`` are derived by the
    client and are deliberately absent.  ``origin`` is a local provenance; a
    deterministic test fixture additionally carries ``fixture_id`` and
    ``fixture_kind`` so a reader can never mistake it for a recovered card.
    """

    account_key: str
    role_id: int
    server_id: int
    instance_id: str
    card_cfg_id: int
    level: int
    exp: int
    advance_level: int
    sfm_id1: int
    sfm_id2: int
    sfm_id3: int
    sfm_id4: int
    equip_id1: str
    equip_id2: str
    equip_id3: str
    equip_id4: str
    locked: bool
    refine_lv: int
    refine_lv_times: int
    learn_atk: int
    learn_hp: int
    learn_state: int
    refine_skill: str
    god_weapon_id: str
    origin: str
    fixture_id: str | None
    fixture_kind: str | None
    recorded_at: int

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def return_card_info_fields(self) -> tuple:
        """The 21 values of S2C 30 ``returnCardInfo`` in recovered wire order."""

        return (
            self.instance_id,
            self.card_cfg_id,
            self.level,
            self.exp,
            self.advance_level,
            self.sfm_id1,
            self.sfm_id2,
            self.sfm_id3,
            self.sfm_id4,
            self.equip_id1,
            self.equip_id2,
            self.equip_id3,
            self.equip_id4,
            self.locked,
            self.refine_lv,
            self.refine_lv_times,
            self.learn_atk,
            self.learn_hp,
            self.learn_state,
            self.refine_skill,
            self.god_weapon_id,
        )

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free block every card event carries."""

        return {
            "card_instance_id": self.instance_id,
            "card_cfg_id": self.card_cfg_id,
            "card_level": self.level,
            "card_origin": self.origin,
            "card_fixture_id": self.fixture_id,
            "card_fixture_kind": self.fixture_kind,
            "card_recorded_at": self.recorded_at,
        }


@dataclass(frozen=True, slots=True)
class CardAdvanceRequestRecord:
    """One committed ``advanceCardEnd`` (C2S 21), as replayed for a duplicate.

    The client sends its own ``pkgSyncCounterMgr`` value for
    ``TB_SYNC_COUNTER_TYPE.ADVANCE_CARD`` (110) in field 2.  That counter is the
    only thing in the recovered request that distinguishes a *retry* of an
    already-applied advance from the player's next legitimate advance of the
    same card, so the accepted request is remembered under it.  A resend with
    the same counter replays success without touching the card or the wallet.

    It is persisted as one bounded JSON value in the archive's existing ``meta``
    key-value table (key ``card_advance_last:<account_key>``), written inside
    the same transaction as the card and wallet change.  No structural change is
    needed, so every existing archive opens unchanged.
    """

    account_key: str
    role_id: int
    server_id: int
    sync_counter: int
    instance_id: str
    advance_level_from: int
    advance_level_to: int
    money_spent: float
    yinpo_spent: int
    jinpo_spent: int
    fodder_ids: tuple[str, ...]
    recorded_at: int
    origin: str

    def display_key(self, visible: int = 8) -> str:
        """Short, non-reversible handle used in event logs."""

        return self.account_key[:visible]

    def evidence_fields(self) -> dict[str, Any]:
        """The bounded, credential-free block the replay event carries."""

        return {
            "card_advance_sync_counter": self.sync_counter,
            "card_advance_instance_id": self.instance_id,
            "card_advance_level_from": self.advance_level_from,
            "card_advance_level_to": self.advance_level_to,
            "card_advance_money_spent": self.money_spent,
            "card_advance_yinpo_spent": self.yinpo_spent,
            "card_advance_jinpo_spent": self.jinpo_spent,
            "card_advance_fodder_ids": list(self.fodder_ids),
            "card_advance_origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class CardInstanceWrite:
    """One ``card_instances`` row for a draw's single transaction (TASK-0235).

    The draw handler only ever needs these columns for a deterministic fixture
    row; every other column keeps ``record_card_instance``'s default.
    """

    instance_id: str
    card_cfg_id: int
    level: int = 1
    sfm_id1: int = 0
    sfm_id2: int = 0
    origin: str = CARD_INSTANCE_ORIGIN_DETERMINISTIC_TEST_FIXTURE
    fixture_id: str | None = None
    fixture_kind: str | None = None


@dataclass(frozen=True, slots=True)
class TicketDrawOutcome:
    """The committed result of one dev-play ticket draw.

    ``balance_seeded`` says the ledger marker was missing and this same
    transaction created it at the seed balance before charging.
    """

    rows: tuple[CardInstanceRecord, ...]
    balance_before: int
    balance_after: int
    balance_seeded: bool


class TicketBalanceShortfallError(RoleStoreError):
    """The ledger holds fewer tickets than the draw costs; nothing was written."""

    def __init__(self, *, balance_field: str, balance: int, cost: int) -> None:
        super().__init__(
            f"ticket balance {balance_field} has {balance}, the draw costs {cost}"
        )
        self.balance_field = str(balance_field)
        self.balance = int(balance)
        self.cost = int(cost)


@dataclass(frozen=True, slots=True)
class GoodsUseRecord:
    """The committed result of one C2S 429 goodsUse (TASK-0266).

    Both counts are the archive's own decimal-integer ledger values (see
    GOODS_USE_META_PREFIX), not S2C 27 wire values: the handler turns
    them into absolute returnOneValue_N packets itself.  The before values are
    read inside the same transaction that wrote the after values, so a caller
    can never observe a half-applied pair.
    """

    account_key: str
    role_id: int
    item_value_name: str
    item_before: int
    item_after: int
    used_count: int
    reward_value_name: str
    reward_before: int
    reward_after: int
    reward_amount: int
    provenance: str

    def event_fields(self) -> dict[str, Any]:
        return {
            "item_value_name": self.item_value_name,
            "item_before": self.item_before,
            "item_after": self.item_after,
            "used_count": self.used_count,
            "reward_value_name": self.reward_value_name,
            "reward_before": self.reward_before,
            "reward_after": self.reward_after,
            "reward_amount": self.reward_amount,
            "goods_use_provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class HotSpotPityRecord:
    """The per-banner consecutive-non-featured-legend counter."""

    account_key: str
    hotspot_id: int
    since_featured_legend: int
    updated_at: int
    provenance: str

    def evidence_fields(self) -> dict[str, Any]:
        return {
            "hotspot_id": self.hotspot_id,
            "hotspot_since_featured_legend": self.since_featured_legend,
            "hotspot_updated_at": self.updated_at,
            "hotspot_provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class DrawPityRecord:
    """The persisted pity counters for one role and one draw pool."""

    account_key: str
    pool: str
    since_legend: int
    since_rare: int
    updated_at: int
    provenance: str

    def evidence_fields(self) -> dict[str, Any]:
        return {
            "draw_pool": self.pool,
            "pity_since_legend": self.since_legend,
            "pity_since_rare": self.since_rare,
            "pity_updated_at": self.updated_at,
            "pity_provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class TeamPresetRecord:
    """One stored team preset in the client's own S2C 37/40 record shape."""

    account_key: str
    group_id: int
    equip_ids: tuple[str, ...]
    god_weapon_id: str
    card_ids: tuple[str, ...]
    origin: str
    updated_at: int

    def team_info_fields(self) -> tuple[object, ...]:
        """The 17 values of S2C 37/40 ``returnTeamInfo``/``updateTeam``."""

        return (
            self.group_id,
            *self.equip_ids,
            self.god_weapon_id,
            *self.card_ids,
        )

    def evidence_fields(self) -> dict[str, Any]:
        return {
            "team_group_id": self.group_id,
            "team_card_ids": list(self.card_ids),
            "team_equip_ids": list(self.equip_ids),
            "team_god_weapon_id": self.god_weapon_id,
            "team_origin": self.origin,
            "team_updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class TeamStateRecord:
    """Which preset the client's C2S 5 made current."""

    account_key: str
    current_group: int
    origin: str
    updated_at: int

    def evidence_fields(self) -> dict[str, Any]:
        return {
            "team_current_group": self.current_group,
            "team_current_group_origin": self.origin,
            "team_current_group_updated_at": self.updated_at,
        }


class RoleStore:
    """A single SQLite connection guarded by one lock, plus its live metadata."""

    def __init__(
        self,
        location: str | Path,
        *,
        kind: str,
        remove_root_on_close: Path | None = None,
        time_source: Callable[[], float] | None = None,
        profile_fields: Mapping[str, Any] | None = None,
        instance_lock: object | None = None,
        archive_guard: object | None = None,
    ) -> None:
        if kind not in ROLE_STORE_KINDS:
            raise ValueError(f"unknown role store kind: {kind!r}")
        if kind == KIND_PLAY_PROFILE and archive_guard is None:
            # A named play profile is one player's save, and the guard is what
            # proves the *file* is that save.  Opening the kind without it would
            # let a missing, damaged or aliased archive be served, so it is
            # refused here rather than silently trusted; the opener is
            # `revival_stub.play_profile.PlayProfile.open_store`.
            raise ValueError(
                "a durable play profile is opened through its archive guard; "
                "use PlayProfile.open_store()"
            )
        self._kind = kind
        self._remove_root_on_close = remove_root_on_close
        # Set only when *this* open performed a structural upgrade; carried into
        # `describe()` so the startup event states what happened to the file
        # instead of leaving a reader to diff it.
        self._migration_record: dict[str, Any] | None = None
        # A named play profile's bounded identity/provenance block, and the OS
        # lease that proves this process is the one serving it.  Both travel
        # with the store's lifetime: whoever closes the store releases them.
        self._profile_fields = dict(profile_fields or {})
        self._instance_lock = instance_lock
        self._lock = threading.RLock()
        self._closed = False
        self._time_source = time_source
        self._is_memory = str(location) == ":memory:"
        self._path = None if self._is_memory else Path(location).resolve()
        # Before anything opens the archive: the guard proves the *file* this
        # store may serve (it exists, it is a regular file, it is not a link or
        # a hard link, it is not the protected default archive), because the
        # checks below only follow a path.
        guarded = None
        if archive_guard is not None:
            guarded = archive_guard.verify(Path(location))  # type: ignore[attr-defined]
        if self._path is not None and self._path.exists():
            self._verify_existing_archive(self._path)
        if guarded is not None:
            # The guard found the archive, so SQLite must open *that* file and
            # may never create one: `mode=rw` refuses a database that is not
            # there instead of silently starting an empty one.
            target = _read_write_uri(self._path)  # type: ignore[arg-type]
            is_uri = True
        else:
            target = str(self._path)
            is_uri = False
        try:
            self._conn = sqlite3.connect(
                ":memory:" if self._is_memory else target,
                isolation_level=None,
                check_same_thread=False,
                uri=is_uri,
            )
        except sqlite3.Error as exc:  # pragma: no cover - OS-level refusal
            raise RoleStoreUnusableError(
                f"cannot open role archive {self._path}: {exc}"
            ) from exc
        self._conn.row_factory = sqlite3.Row
        try:
            if archive_guard is not None:
                # Still before the first write (the WAL switch and the schema
                # creation below): the database this connection will write to
                # must be the file that was just verified.
                archive_guard.confirm(  # type: ignore[attr-defined]
                    self._main_database_file()
                )
            self._configure()
            self._ensure_schema()
            self._verify_integrity()
        except RoleStoreError:
            self._conn.close()
            raise
        except sqlite3.Error as exc:
            # Any structural SQLite refusal (a damaged image, a file that is
            # not actually a database, a lock the archive never releases) is
            # one condition from the caller's point of view: the archive cannot
            # be served, so nothing may be written into it.
            self._conn.close()
            raise RoleStoreUnusableError(
                f"role archive {self._location} could not be opened safely: {exc}"
            ) from exc
        except BaseException:
            self._conn.close()
            raise
        if self._path is not None:
            # Credentials are not stored, but the binding still names an
            # account; keep it owner-only where the platform supports it.
            try:
                self._path.chmod(0o600)
            except OSError:  # pragma: no cover - Windows/POSIX policy dependent
                pass

    # ------------------------------------------------------------------ opening

    @classmethod
    def in_memory(
        cls, *, time_source: Callable[[], float] | None = None
    ) -> "RoleStore":
        """A fresh empty archive that cannot outlive the process."""

        return cls(":memory:", kind=KIND_MEMORY, time_source=time_source)

    @classmethod
    def at_directory(
        cls,
        data_dir: Path | str,
        *,
        time_source: Callable[[], float] | None = None,
        kind: str = KIND_PERSISTENT,
        profile_fields: Mapping[str, Any] | None = None,
        instance_lock: object | None = None,
        archive_guard: object | None = None,
    ) -> "RoleStore":
        """The long-lived archive: ``<data_dir>/revival_roles.sqlite3``.

        ``kind``/``profile_fields``/``instance_lock``/``archive_guard`` are
        supplied by the named play-profile opener; every other caller keeps the
        persistent default and no guard, so the path-addressed archive and the
        throwaway test archive keep their established behaviour exactly.
        """

        directory = Path(data_dir)
        if archive_guard is not None:
            # Before `mkdir`: an archive that cannot be served is not even
            # given a directory to live in.
            archive_guard.verify(directory / STORE_FILENAME)  # type: ignore[attr-defined]
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RoleStoreUnusableError(
                f"cannot create role data directory {directory}: {exc}"
            ) from exc
        return cls(
            directory / STORE_FILENAME,
            kind=kind,
            time_source=time_source,
            profile_fields=profile_fields,
            instance_lock=instance_lock,
            archive_guard=archive_guard,
        )

    @classmethod
    def fresh_test(
        cls, *, time_source: Callable[[], float] | None = None
    ) -> "RoleStore":
        """A new-role test archive in its own temporary directory.

        Used by ``--role-mode fresh_role_test`` so that a regression or device
        new-role run can never touch, and can never be confused by, a real
        player's long-lived archive.
        """

        root = Path(tempfile.mkdtemp(prefix="revival-fresh-role-test-"))
        return cls(
            root / STORE_FILENAME,
            kind=KIND_FRESH_TEST,
            remove_root_on_close=root,
            time_source=time_source,
        )

    @staticmethod
    def _verify_existing_archive(path: Path) -> None:
        """Refuse anything that is not already a readable SQLite database.

        Checked before ``sqlite3.connect`` can create or reinterpret a file, so
        a corrupted archive is reported instead of being overwritten.
        """

        if not path.is_file():
            raise RoleStoreUnusableError(
                f"role archive path {path} exists but is not a regular file"
            )
        header = b""
        try:
            with path.open("rb") as handle:
                header = handle.read(16)
        except OSError as exc:
            raise RoleStoreUnusableError(
                f"cannot read role archive {path}: {exc}"
            ) from exc
        if header != b"SQLite format 3\x00":
            raise RoleStoreUnusableError(
                f"role archive {path} is not a SQLite database "
                f"(header={header!r}); refusing to overwrite it"
            )

    def _main_database_file(self) -> str | None:
        """The path SQLite itself reports for its ``main`` database, or ``None``.

        Read once, before the first write, so a named play profile's guard can
        prove that the database this connection will write to is the file it
        verified -- a path is not evidence, and neither is a successful
        ``connect``.  A connection that will not name its database returns
        ``None``, which the guard fails closed on.
        """

        try:
            rows = self._conn.execute("PRAGMA database_list").fetchall()
        except sqlite3.Error:
            return None
        for row in rows:
            if str(row["name"]) == "main":
                return str(row["file"])
        return None  # pragma: no cover - SQLite always lists `main`

    def _configure(self) -> None:
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if not self._is_memory:
            # WAL + FULL: the role must survive a process kill immediately
            # after the create transaction commits.
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = FULL")

    def _ensure_schema(self) -> None:
        stored = self._stored_schema_version()
        if stored is None:
            if self._table_names():
                raise RoleStoreUnusableError(
                    f"role archive {self._location} has tables but no "
                    f"schema_version marker; refusing to guess its structure"
                )
            self._create_schema()
            return
        if stored == SCHEMA_VERSION:
            return
        if stored > SCHEMA_VERSION:
            raise RoleStoreUnusableError(
                f"role archive {self._location} was written by a newer "
                f"revival_stub (structure {stored} > {SCHEMA_VERSION}); "
                f"refusing to serve it"
            )
        # The upgrade is destructive only in principle, so it happens behind a
        # snapshot that has already been written *and* verified as a readable
        # copy of this archive.  A snapshot that cannot be produced stops the
        # open here, with the archive untouched.
        snapshot = self._write_migration_snapshot(stored)
        self._migrate(stored)
        self._migration_record = {
            "pre_migration_structure_version": stored,
            "migration_snapshot": str(snapshot),
        }

    def _stored_schema_version(self) -> int | None:
        if "meta" not in self._table_names():
            return None
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError) as exc:
            raise RoleStoreUnusableError(
                f"role archive {self._location} has an unreadable "
                f"schema_version marker {row['value']!r}"
            ) from exc

    def _table_names(self) -> set[str]:
        return {
            str(row["name"])
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    def _create_schema(self) -> None:
        def body() -> None:
            # A second process may have created the archive between the check
            # in _ensure_schema and this transaction.  Re-read inside the lock
            # and adopt what is there instead of failing on `table exists`.
            if "meta" in self._table_names():
                return
            for statement in _REWARD_STORAGE_STATEMENTS + _GOD_WEAPON_STATEMENTS:
                self._conn.execute(statement)
            for statement in _SCHEMA_STATEMENTS:
                self._conn.execute(statement)
            for key, value in (
                ("schema_version", str(SCHEMA_VERSION)),
                ("account_key_salt", secrets.token_bytes(_SALT_BYTES).hex()),
                ("account_key_derivation", ACCOUNT_KEY_DERIVATION),
                ("created_at", str(self._now())),
            ):
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?)", (key, value)
                )
            self._conn.execute(
                "INSERT INTO role_serials (id, next_serial) VALUES (1, ?)",
                (str(REVIVAL_LOCAL_ROLE_SERIAL_BASE),),
            )

        self._transaction(body)

    def _migrate(self, stored: int) -> None:
        """Upgrade an older archive **step by step**, never in one jump.

        The walk is ``stored -> stored + 1 -> … -> SCHEMA_VERSION`` and every
        step is one entry of ``_MIGRATIONS``, so an archive at structure 1 gains
        the version-2 table *and* the version-3 table before its marker is
        allowed to say 3.  One jump was the TASK-0085 regression: a valid
        structure-1 archive was marked current after a single step while
        ``bag_sort_states`` was never created, and every later read of the bag
        failed with a raw SQLite error.

        Everything refused below is refused *before* the archive is touched: a
        step that was never recovered, a claimed structure the file does not
        really hold, a table a pending step would create that is already present
        (so no step ever writes over a table this structure never had), and an
        identity marker that is already lost.  One transaction then covers every
        step, the proof that the target structure now physically exists, and the
        version marker, so a failure (or a crash) can never leave a
        half-upgraded archive whose marker invites the next reader to skip a
        step.
        """

        steps = list(range(stored, SCHEMA_VERSION))
        for step in steps:
            if step not in _MIGRATIONS:
                raise RoleStoreMigrationError(
                    f"role archive {self._location} is at structure {stored} and "
                    f"no recovered migration from structure {step} to "
                    f"{step + 1} exists; refusing to rewrite it"
                )
        if stored not in _REQUIRED_STRUCTURE:
            # No recovered definition of the structure being left, so "does the file
            # really hold it?" cannot be asked -- and a step may not run unwatched.
            raise RoleStoreMigrationError(
                f"role archive {self._location} is at structure {stored}, which has "
                f"no recovered definition of what it must hold; refusing to "
                f"rewrite it"
            )
        gaps = _missing_structure(self._conn, stored)
        if gaps:
            raise RoleStoreMigrationError(
                f"role archive {self._location} claims structure {stored} but "
                f"does not hold it ({_describe_missing_structure(gaps)}); "
                f"refusing to migrate from a structure that is not really there"
            )
        pending: dict[str, tuple[str, ...]] = {}
        for step in steps:
            for table, columns in _REQUIRED_STRUCTURE[step + 1].items():
                if table not in _REQUIRED_STRUCTURE[step]:
                    pending[table] = columns
        already = sorted(table for table in pending if table in self._table_names())
        if already:
            raise RoleStoreMigrationError(
                f"role archive {self._location} already holds "
                f"{', '.join(already)} while its marker says structure {stored}; "
                f"refusing to migrate over a table this structure never had"
            )
        identity_before = self._identity_facts()
        lost = sorted(key for key, value in identity_before.items() if value is None)
        if lost:
            raise RoleStoreMigrationError(
                f"role archive {self._location} has already lost its "
                f"{', '.join(lost)}; refusing to migrate an archive that can no "
                f"longer identify its accounts"
            )
        roles_before = _role_row_tuples(self._conn)

        def body() -> None:
            for step in steps:
                _MIGRATIONS[step](self._conn)
            gaps_after = _missing_structure(self._conn, SCHEMA_VERSION)
            if gaps_after:
                raise RoleStoreMigrationError(
                    f"the upgrade of {self._location} to structure "
                    f"{SCHEMA_VERSION} did not build "
                    f"{_describe_missing_structure(gaps_after)}; rolling back"
                )
            if self._identity_facts() != identity_before:
                raise RoleStoreMigrationError(
                    f"the upgrade of {self._location} moved an identity fact; "
                    f"rolling back"
                )
            if _role_row_tuples(self._conn) != roles_before:
                raise RoleStoreMigrationError(
                    f"the upgrade of {self._location} changed a role row; "
                    f"rolling back"
                )
            updated = self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
            if updated.rowcount != 1:
                raise RoleStoreMigrationError(
                    f"role archive {self._location} has no schema_version marker "
                    f"row to update; rolling back the upgrade"
                )

        self._transaction(body)

    # ------------------------------------------------------- migration snapshot

    def _migration_snapshot_path(self, stored: int) -> Path:
        if self._path is None:  # pragma: no cover - a memory archive is born current
            raise RoleStoreMigrationError(
                "an in-memory archive has no file to snapshot before an upgrade"
            )
        return self._path.with_name(
            self._path.name + MIGRATION_SNAPSHOT_SUFFIX.format(version=stored)
        )

    def _write_migration_snapshot(self, stored: int) -> Path:
        """Write, then verify, the pre-migration copy of this archive.

        ``Connection.backup`` is SQLite's own online backup: it reads through
        the connection, so a database whose newest commits still live in a
        ``-wal`` file is copied as one consistent file rather than as a main
        file whose tail is missing.  The copy is then reopened read-only and
        checked against the live archive (integrity, structure marker, every
        role row) before the migration is allowed to touch anything, because a
        snapshot nobody verified is not a recovery point.

        A snapshot that already exists is never overwritten -- the first one of
        a given structure version is the recovery point -- but it must still
        verify, or the open fails closed.
        """

        target = self._migration_snapshot_path(stored)
        created_here = False
        if target.exists():
            self._verify_migration_snapshot(target, stored)
            return target
        destination = sqlite3.connect(str(target))
        created_here = True
        try:
            self._conn.backup(destination)
        except sqlite3.Error as exc:
            destination.close()
            self._discard_failed_snapshot(target, created_here)
            raise RoleStoreMigrationError(
                f"role archive {self._location} could not be snapshotted before "
                f"its structure upgrade: {exc}"
            ) from exc
        destination.close()
        try:
            self._verify_migration_snapshot(target, stored)
        except RoleStoreError:
            self._discard_failed_snapshot(target, created_here)
            raise
        return target

    @staticmethod
    def _discard_failed_snapshot(target: Path, created_here: bool) -> None:
        """Remove only a snapshot this call created and could not verify."""

        if not created_here:  # pragma: no cover - guarded by the caller
            return
        try:
            target.unlink()
        except OSError:  # pragma: no cover - a leftover is refused, not trusted
            pass

    def _verify_migration_snapshot(self, target: Path, stored: int) -> None:
        """Prove the snapshot is a readable copy of *this* archive.

        Read-only, and every failure is a refusal: a snapshot that is not a
        database, is damaged, carries another structure version, or holds a
        different set of roles than the archive about to be upgraded is not a
        recovery point for this migration.
        """

        self._verify_existing_archive(target)
        try:
            copy = sqlite3.connect(
                _read_only_uri(target), uri=True, isolation_level=None
            )
        except sqlite3.Error as exc:  # pragma: no cover - OS-level refusal
            raise RoleStoreMigrationError(
                f"pre-migration snapshot {target} cannot be opened: {exc}"
            ) from exc
        copy.row_factory = sqlite3.Row
        try:
            check = copy.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]) != "ok":
                raise RoleStoreMigrationError(
                    f"pre-migration snapshot {target} is not a sound database "
                    f"({check[0] if check is not None else 'no result'})"
                )
            marker = copy.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if marker is None or str(marker["value"]) != str(stored):
                raise RoleStoreMigrationError(
                    f"pre-migration snapshot {target} does not carry structure "
                    f"{stored}; refusing to migrate behind it"
                )
            if _role_row_tuples(copy) != _role_row_tuples(self._conn):
                raise RoleStoreMigrationError(
                    f"pre-migration snapshot {target} does not hold the role "
                    f"rows of the archive it was taken from"
                )
        except sqlite3.Error as exc:
            raise RoleStoreMigrationError(
                f"pre-migration snapshot {target} could not be read: {exc}"
            ) from exc
        finally:
            copy.close()

    def _verify_integrity(self) -> None:
        try:
            row = self._conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise RoleStoreUnusableError(
                f"role archive {self._location} failed its integrity check: {exc}"
            ) from exc
        result = str(row[0]) if row is not None else "no result"
        if result != "ok":
            raise RoleStoreUnusableError(
                f"role archive {self._location} is damaged: {result}"
            )
        # The marker is a claim about the file, and the file has to hold it.
        # An archive whose marker says 3 while `bag_sort_states` was never
        # created is the TASK-0085 regression, and the one thing this check may
        # never do is repair it in place: growing the tables a marker promises
        # would serve a file that two builds disagree about, and would hide the
        # migration that never ran.  The refusal names the version, the objects
        # and the recovery point instead, so the operator can restore the
        # verified pre-migration snapshot by hand.
        version = self._stored_schema_version()
        if version is None or version not in _REQUIRED_STRUCTURE:
            raise RoleStoreUnusableError(
                f"role archive {self._location} carries no recovered structure "
                f"definition (marker={version!r}); refusing to serve it"
            )
        gaps = _missing_structure(self._conn, version)
        if gaps:
            raise RoleStoreUnusableError(
                f"role archive {self._location} records structure {version} but "
                f"does not hold it ({_describe_missing_structure(gaps)}); "
                f"refusing to serve an archive whose marker is not true, and "
                f"not repairing it in place"
                f"{self._recovery_point_hint()}"
            )
        # An archive that cannot yield its salt, its derivation or its serial is
        # not repairable here, and replacing it with an empty one would destroy
        # the binding.
        for key, value in self._identity_facts().items():
            if value is None:
                raise RoleStoreUnusableError(
                    f"role archive {self._location} is missing its {key!r}; "
                    f"refusing to serve or replace it"
                )

    def _identity_facts(self) -> dict[str, Any]:
        """The three facts an open must read and a migration may never move.

        Their *values* are compared across an upgrade (a step that rewrote the
        salt would silently re-key every account); their presence is what
        ``_verify_integrity`` demands.  The caller must have established that the
        tables exist, because a missing ``role_serials`` is a different refusal.
        """

        return _identity_facts_of(self._conn)

    def _recovery_point_hint(self) -> str:
        """Name the pre-migration snapshots sitting next to this archive, if any.

        A refusal that says what is wrong and where the recovery point is can be
        acted on; one that only says "unusable" cannot.
        """

        if self._path is None:  # pragma: no cover - a memory archive is born current
            return ""
        snapshots = sorted(
            self._path.parent.glob(self._path.name + ".pre-migration-v*.bak")
        )
        if not snapshots:
            return ""
        return "; pre-migration snapshot(s) present: " + ", ".join(
            str(path) for path in snapshots
        )

    # ------------------------------------------------------------------ helpers

    def _transaction(self, body: Callable[[], Any]) -> Any:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = body()
            self._conn.execute("COMMIT")
        except BaseException:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                # SQLite already discarded the transaction (a body error can
                # abort it outright).  The original exception is the fact that
                # matters, and nothing was committed either way.
                pass
            raise
        return result

    def _meta(self, key: str) -> str | None:
        return _meta_of(self._conn, key)

    # ------------------------------------------------------- the generic meta API
    #
    # The ``meta`` table is structure 1 and every archive already has it, so a
    # local simulator value that must survive a restart does not need a new
    # table, a migration or a new SCHEMA_VERSION (TASK-0206 proved the pattern
    # with the card-advance counter; TASK-0232 uses the same table for the
    # dev-play babel overlay).  These helpers are deliberately tiny: one read,
    # one UPSERT, one delete, all scoped to *this* archive/profile because they
    # run on this store's own connection.  The archive's own bookkeeping keys
    # are reserved and refused rather than silently overwritten.

    @staticmethod
    def _validate_meta_key(key: object) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("a meta key must be a non-empty string")
        if key in RESERVED_META_KEYS:
            raise ValueError(
                f"meta key {key!r} belongs to the archive itself and is reserved"
            )
        return key

    def get_meta(self, key: str) -> str | None:
        """Read one caller-owned key, or ``None`` when it was never written."""

        checked = self._validate_meta_key(key)
        with self._lock:
            return self._meta(checked)

    def add_card_instance(self, role_id: int, account_key: str, cfg_id: int, *, level: int = 1,
                         exp: int = 0, advance_level: int = 0, origin: str = "artifact_dgn_clear") -> str:
        """Insert one owned card and return its new instance id (TASK-0370)."""

        base = f"artifact-{int(role_id)}-{int(cfg_id)}"
        index = 1
        while True:
            instance_id = f"{base}-{index:03d}"
            exists = self._conn.execute(
                "SELECT 1 FROM card_instances WHERE instance_id = ?", (instance_id,)
            ).fetchone()
            if exists is None:
                break
            index += 1
        self._conn.execute(
            "INSERT INTO card_instances (account_key, role_id, server_id, instance_id, "
            "card_cfg_id, level, exp, advance_level, sfm_id1, sfm_id2, sfm_id3, sfm_id4, "
            "equip_id1, equip_id2, equip_id3, equip_id4, locked, refine_lv, refine_lv_times, "
            "learn_atk, learn_hp, learn_state, refine_skill, god_weapon_id, origin, "
            "fixture_id, fixture_kind, recorded_at) VALUES (" + "?," * 27 + "?) ",
            (account_key, int(role_id), 1, instance_id, int(cfg_id), int(level), int(exp),
             int(advance_level), 0, 0, 0, 0, "", "", "", "", 0, 0, 0, 0, 0, 0, "", "",
             origin, "", "deterministic_draw_result", int(__import__("time").time())),
        )
        self._conn.commit()
        return instance_id

    def card_offer_rows(self, role_id: int, limit: int = 3) -> list[tuple]:
        """(instance_id, cfg_id, level, exp) for a few owned cards (TASK-0364).

        The artifact tower's 马车 node offers cards; the offer must name cards the role really
        owns, because the client resolves dCfgId against its own card table."""

        rows = self._conn.execute(
            "SELECT instance_id, card_cfg_id, level, exp FROM card_instances "
            "WHERE role_id = ? ORDER BY card_cfg_id DESC, instance_id LIMIT ?",
            (int(role_id), int(limit)),
        ).fetchall()
        return [(str(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in rows]

    def adjust_role_value(self, *, account_key: str, value_name: str, delta: int,
                        provenance: str = "artifact_dgn_reward") -> int:
        """Add ``delta`` to a generic role value and return the new amount (TASK-0377).

        Structure 13's ``role_values`` is the home for every role value the frozen
        ``role_wallet`` CHECK refuses -- the artifact tower's point, for one."""

        with self._lock:
            def body() -> int:
                now = self._now()
                row = self._conn.execute(
                    "SELECT amount FROM role_values "
                    "WHERE account_key = ? AND value_name = ?",
                    (account_key, value_name),
                ).fetchone()
                total = int(row[0]) + int(delta) if row is not None else int(delta)
                if total < 0:
                    raise RoleStoreError(
                        f"refusing to drive role value {value_name!r} below zero "
                        f"({row[0] if row else 0} + {delta})"
                    )
                self._conn.execute(
                    "INSERT INTO role_values "
                    "(account_key, value_name, amount, provenance, updated_at) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(account_key, value_name) DO UPDATE SET "
                    "amount = excluded.amount, provenance = excluded.provenance, "
                    "updated_at = excluded.updated_at",
                    (account_key, value_name, total, provenance, now),
                )
                return total
            return self._transaction(body)

    def add_god_weapon(self, *, account_key: str, role_id: int, cfg_id: int,
                       star: int = 6, level: int = 1, exp: int = 0,
                       provenance: str = "artifact_dgn_shop") -> str:
        """Insert one 神器 instance and return its id (TASK-0397)."""

        base = f"gw-{int(role_id)}-{int(cfg_id)}"
        index = 1
        while True:
            instance_id = f"{base}-{index:03d}"
            if self._conn.execute(
                "SELECT 1 FROM god_weapon_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone() is None:
                break
            index += 1
        self._conn.execute(
            "INSERT INTO god_weapon_instances (account_key, role_id, instance_id, cfg_id,"
            " level, exp, star, provenance, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (account_key, int(role_id), instance_id, int(cfg_id), int(level), int(exp),
             int(star), provenance, int(self._now())),
        )
        self._conn.commit()
        return instance_id

    def god_weapon_rows(self, *, account_key: str) -> list[tuple]:
        """(instance_id, cfg_id, level, exp, star) for every owned 神器."""

        rows = self._conn.execute(
            "SELECT instance_id, cfg_id, level, exp, star FROM god_weapon_instances "
            "WHERE account_key = ? ORDER BY cfg_id, instance_id",
            (account_key,),
        ).fetchall()
        return tuple(tuple(row) for row in rows)

    def card_piece_counts(self, *, account_key: str) -> dict[int, int]:
        """{card_cfg_id: count} for every stored 碎片 (TASK-0395)."""

        rows = self._conn.execute(
            "SELECT card_cfg_id, count FROM card_pieces WHERE account_key = ? "
            "ORDER BY card_cfg_id",
            (account_key,),
        ).fetchall()
        return {int(row[0]): int(row[1]) for row in rows}

    def add_card_piece(self, *, account_key: str, card_cfg_id: int, count: int = 1,
                      provenance: str = "uncharted_reward") -> int:
        """Add card 碎片 (reward type 9) and return the new count (TASK-0377)."""

        with self._lock:
            def body() -> int:
                now = self._now()
                row = self._conn.execute(
                    "SELECT count FROM card_pieces "
                    "WHERE account_key = ? AND card_cfg_id = ?",
                    (account_key, int(card_cfg_id)),
                ).fetchone()
                total = int(row[0]) + int(count) if row is not None else int(count)
                self._conn.execute(
                    "INSERT INTO card_pieces "
                    "(account_key, card_cfg_id, count, provenance, updated_at) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(account_key, card_cfg_id) DO UPDATE SET "
                    "count = excluded.count, provenance = excluded.provenance, "
                    "updated_at = excluded.updated_at",
                    (account_key, int(card_cfg_id), total, provenance, now),
                )
                return total
            return self._transaction(body)

    def add_special_card(self, *, account_key: str, cfg_id: int, count: int = 1,
                        provenance: str = "artifact_dgn_reward") -> int:
        """Add special cards (reward type 8886) and return the new count (TASK-0376)."""

        with self._lock:
            def body() -> int:
                now = self._now()
                row = self._conn.execute(
                    "SELECT count FROM card_special_inventory "
                    "WHERE account_key = ? AND cfg_id = ?",
                    (account_key, int(cfg_id)),
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO card_special_inventory "
                        "(account_key, cfg_id, count, provenance, updated_at) "
                        "VALUES (?,?,?,?,?)",
                        (account_key, int(cfg_id), int(count), provenance, now),
                    )
                    return int(count)
                total = int(row[0]) + int(count)
                self._conn.execute(
                    "UPDATE card_special_inventory SET count = ?, updated_at = ? "
                    "WHERE account_key = ? AND cfg_id = ?",
                    (total, now, account_key, int(cfg_id)),
                )
                return total
            return self._transaction(body)

    def delete_meta_prefix(self, prefix: str) -> int:
        """Delete every meta row whose key starts with ``prefix`` (TASK-0385)."""

        with self._lock:
            def body() -> int:
                cursor = self._conn.execute(
                    "DELETE FROM meta WHERE key LIKE ?", (prefix + "%",))
                return int(cursor.rowcount or 0)
            return self._transaction(body)

    def meta_with_prefix(self, prefix: str) -> list[tuple[str, str]]:
        """Every (key, value) whose key starts with prefix (TASK-0353)."""

        rows = self._conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (prefix.replace("%", "") + "%",),
        ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def set_meta(self, key: str, value: str) -> None:
        """Write one caller-owned key atomically (single-statement UPSERT).

        The value is stored verbatim; a caller that needs structure encodes it
        itself (the dev-play overlay stores one bounded JSON object).  Nothing
        outside the ``meta`` row is touched, so no role, wallet, inventory or
        schema marker can change through this call.
        """

        checked = self._validate_meta_key(key)
        if not isinstance(value, str):
            raise ValueError("a meta value must be a string")
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (checked, value),
            )

    def delete_meta(self, key: str) -> bool:
        """Delete one caller-owned key; ``True`` when a row was removed."""

        checked = self._validate_meta_key(key)
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM meta WHERE key = ?", (checked,)
            )
            return cursor.rowcount > 0

    def _now(self) -> int:
        return int(self._time_source() if self._time_source else time.time())

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def _location(self) -> str:
        return ":memory:" if self._is_memory else str(self._path)

    def profile_fields(self) -> dict[str, Any]:
        """The named play profile this store serves, bounded; empty otherwise."""

        return dict(self._profile_fields)

    def describe(self) -> dict[str, object]:
        """The bounded, credential-free description used in startup events."""

        with self._lock:
            migration = self._migration_record or {}
            return {
                "role_store_kind": self._kind,
                "role_store_location": self._location,
                "role_store_schema_version": SCHEMA_VERSION,
                "role_store_derivation": ACCOUNT_KEY_DERIVATION,
                "role_count": self.count(),
                # Named explicitly so no reader can infer that the in-memory
                # role of the earlier runs was adopted into the archive.
                "historical_role_records_migrated": 0,
                # A structural upgrade of the file itself, stated rather than
                # left for a reader to discover: which structure was replaced,
                # and where the verified pre-migration copy is.  Both are
                # ``None``/``False`` for an open that changed no structure.
                "role_store_structure_migrated": bool(self._migration_record),
                "role_store_pre_migration_version": migration.get(
                    "pre_migration_structure_version"
                ),
                "role_store_migration_snapshot": migration.get(
                    "migration_snapshot"
                ),
                **self._profile_fields,
            }

    # ------------------------------------------------------------ the player one

    def accepts_account(self, account_key: str) -> bool:
        """May this archive serve this account at all?

        A named play profile is one player's save, so its binding is exclusive:
        the account that created its role is served, any other account is
        refused *before* the handler can answer `noRole` -- which is the
        client's create-role switch, and would let a second player add a role to
        somebody else's profile.  An unbound profile (no role yet) still accepts
        the first account, which is the one-time onboarding.

        Every other kind is multi-account by design and accepts everyone.
        """

        if self._kind != KIND_PLAY_PROFILE:
            return True
        with self._lock:
            row = self._conn.execute(
                "SELECT account_key FROM roles WHERE account_key = ?", (account_key,)
            ).fetchone()
            if row is not None:
                return True
            return self.count() == 0

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM roles").fetchone()
            return int(row["n"])

    # ------------------------------------------------------------------ identity

    def account_key(
        self,
        *,
        server_id: float | int,
        platform: str,
        channel_id: str,
        channel_sub_id: str,
        sdk_identity: str,
    ) -> str:
        """Derive the durable, non-sensitive key for one account on one server.

        The inputs are the ``loginByToken`` fields the client actually sends:
        the selected server, the platform/channel triple and the SDK user id.
        ``sdk_server_time`` and the verify digest are signatures over time and
        ``device_id`` changes on reinstall, so neither may identify a role.
        A random per-archive salt keeps a low-entropy SDK user id from being
        recoverable by brute force from the stored digest.
        """

        try:
            numeric_server_id = int(server_id)
        except (TypeError, ValueError) as exc:
            raise RoleStoreError(f"unusable login server id {server_id!r}") from exc
        parts = (
            ACCOUNT_KEY_DERIVATION,
            str(numeric_server_id),
            platform.strip().lower(),
            channel_id,
            channel_sub_id,
            sdk_identity,
        )
        material = self._salt() + "\x1f".join(parts).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _salt(self) -> bytes:
        with self._lock:
            value = self._meta("account_key_salt")
        if value is None:  # pragma: no cover - guarded by _verify_integrity
            raise RoleStoreUnusableError(
                f"role archive {self._location} lost its salt"
            )
        return bytes.fromhex(value)

    # ------------------------------------------------------------------ reads

    def lookup(self, account_key: str) -> RoleRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM roles WHERE account_key = ?", (account_key,)
            ).fetchone()
        return None if row is None else _record_from_row(row)

    # ------------------------------------------------------------------ writes

    def create_or_get(
        self,
        *,
        account_key: str,
        server_id: float | int,
        role_name: str,
        career: int,
        create_time: int,
        compatibility_profile_id: str,
    ) -> tuple[RoleRecord, bool]:
        """Bind one role to one account, or return the binding that exists.

        Everything happens inside one ``BEGIN IMMEDIATE`` transaction: the
        existing-binding check, the serial read, the serial bump and the insert.
        A replay of ``createRole`` for an account that already has a role
        therefore returns that role and reports ``created=False``; it can never
        produce a second role for the same account, a duplicate role ID, or a
        partial write that survives a failure.
        """

        numeric_server_id = int(server_id)
        name = _text(role_name)
        if not name:
            raise RoleStoreError("a role name must not be empty")
        now = self._now()
        with self._lock:

            def body() -> tuple[RoleRecord, bool]:
                existing = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ?", (account_key,)
                ).fetchone()
                if existing is not None:
                    return _record_from_row(existing), False
                if self._kind == KIND_PLAY_PROFILE:
                    # The write-side half of `accepts_account`: inside the same
                    # transaction that would insert, so no code path (a replay,
                    # a second connection, a direct caller) can add a second
                    # player's role to a named play profile.
                    bound = self._conn.execute(
                        "SELECT account_key FROM roles LIMIT 1"
                    ).fetchone()
                    if bound is not None:
                        raise RoleStoreError(
                            f"role archive {self._location} is a durable play "
                            f"profile bound to another account; refusing to add "
                            f"a second role to it"
                        )
                serial_row = self._conn.execute(
                    "SELECT next_serial FROM role_serials WHERE id = 1"
                ).fetchone()
                if serial_row is None:  # pragma: no cover - guarded at open
                    raise RoleStoreUnusableError(
                        f"role archive {self._location} lost its role-ID serial"
                    )
                serial = int(serial_row["next_serial"])
                role_id = allocate_revival_role_id(serial, numeric_server_id)
                self._conn.execute(
                    "UPDATE role_serials SET next_serial = ? WHERE id = 1",
                    (serial + 1,),
                )
                self._conn.execute(
                    "INSERT INTO roles (account_key, server_id, role_id, role_name,"
                    " career, create_time, identity_origin,"
                    " compatibility_profile_id, structure_version, created_at,"
                    " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        account_key,
                        numeric_server_id,
                        role_id,
                        name,
                        int(career),
                        int(create_time),
                        IDENTITY_ORIGIN_LOCAL_TRANSACTION,
                        compatibility_profile_id,
                        SCHEMA_VERSION,
                        now,
                        now,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ?", (account_key,)
                ).fetchone()
                if row is None:  # pragma: no cover - INSERT just succeeded
                    raise RoleStoreError("role row vanished inside its own transaction")
                return _record_from_row(row), True

            return self._transaction(body)

    # ------------------------------------------------------------ story facts

    def story_completions(self, *, account_key: str) -> tuple[StoryRecord, ...]:
        """Every stored story completion of one account, ordered by story."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM story_completions WHERE account_key = ? "
                "ORDER BY story_id",
                (account_key,),
            ).fetchall()
        return tuple(_story_from_row(row) for row in rows)

    def story_completion(
        self, *, account_key: str, story_id: int
    ) -> StoryRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM story_completions WHERE account_key = ? "
                "AND story_id = ?",
                (account_key, int(story_id)),
            ).fetchone()
        return None if row is None else _story_from_row(row)

    def record_story_completion(
        self,
        *,
        record: RoleRecord,
        story_id: int,
        state: int,
        observed_at: int,
        evidence_kind: str,
        evidence_story_id: int,
        predecessor_sequence: int,
        successor_sequence: int,
    ) -> tuple[StoryRecord, bool]:
        """Persist one *observed* story completion, or return the one stored.

        The row is keyed by the account and the story, and it is written inside
        one transaction that first re-reads the role itself: a completion can
        only ever be attached to a role this archive really holds, so a caller
        that invents a ``RoleRecord`` (or replays one from a session whose role
        was never bound) writes nothing.  A second observation of the same story
        returns the stored row and reports ``created=False``: the first
        observation is the fact, and a later sighting is not allowed to rewrite
        its time or its evidence.

        Nothing here reads a story state from the client, a reward, a chapter or
        a downstream story.  Only the three values the caller observed are
        stored, and ``origin`` records that they are a local observation.
        """

        numeric_story_id = int(story_id)
        if numeric_story_id <= 0:
            raise RoleStoreError(f"unusable story id {story_id!r}")
        numeric_state = int(state)
        now = self._now()
        with self._lock:

            def body() -> tuple[StoryRecord, bool]:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    # The archive does not hold this role, so there is nothing
                    # this completion could belong to.  Refuse instead of
                    # writing a fact about a role that does not exist.
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record a story "
                        f"completion for it"
                    )
                existing = self._conn.execute(
                    "SELECT * FROM story_completions WHERE account_key = ? "
                    "AND story_id = ?",
                    (record.account_key, numeric_story_id),
                ).fetchone()
                if existing is not None:
                    return _story_from_row(existing), False
                self._conn.execute(
                    "INSERT INTO story_completions (account_key, role_id,"
                    " server_id, story_id, state, observed_at, evidence_kind,"
                    " evidence_story_id, predecessor_sequence,"
                    " successor_sequence, recorded_at, origin)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.account_key,
                        record.role_id,
                        record.server_id,
                        numeric_story_id,
                        numeric_state,
                        int(observed_at),
                        evidence_kind,
                        int(evidence_story_id),
                        int(predecessor_sequence),
                        int(successor_sequence),
                        now,
                        STORY_ORIGIN_LOCAL_OBSERVATION,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM story_completions WHERE account_key = ? "
                    "AND story_id = ?",
                    (record.account_key, numeric_story_id),
                ).fetchone()
                if row is None:  # pragma: no cover - INSERT just succeeded
                    raise RoleStoreError(
                        "story completion row vanished inside its own transaction"
                    )
                return _story_from_row(row), True

            return self._transaction(body)

    # ------------------------------------------------------------- bag facts

    def bag_sort_states(self, *, account_key: str) -> tuple[BagSortRecord, ...]:
        """Every stored bag sort slot of one account, ordered by slot."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bag_sort_states WHERE account_key = ? ORDER BY ops",
                (account_key,),
            ).fetchall()
        return tuple(_bag_sort_from_row(row) for row in rows)

    def bag_sort_state(
        self, *, account_key: str, ops: int
    ) -> BagSortRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM bag_sort_states WHERE account_key = ? AND ops = ?",
                (account_key, int(ops)),
            ).fetchone()
        return None if row is None else _bag_sort_from_row(row)

    def record_bag_sort_state(
        self,
        *,
        record: RoleRecord,
        ops: int,
        sort: int,
        observed_at: int,
        packet_sequence: int,
        evidence_kind: str,
    ) -> tuple[BagSortRecord, str]:
        """Persist the client's current sort for one bag slot.

        Returns the stored row and one of three closed outcomes:

        ``created``
            no row existed for this slot and one was written;
        ``unchanged``
            the stored value is byte-for-byte the one just observed, so nothing
            was written -- a repeated packet is idempotent and cannot rewrite
            the time of a setting that did not change;
        ``updated``
            the slot held a different value and now holds this one, with this
            observation's time and sequence.

        The write happens inside one transaction that first re-reads the role
        itself, exactly like the story rows: a caller that invents a
        ``RoleRecord`` (or replays one from a session whose role was never
        bound) writes nothing.  ``origin`` records that the row is a local
        observation.
        """

        numeric_ops = int(ops)
        if numeric_ops <= 0:
            raise RoleStoreError(f"unusable bag sort slot {ops!r}")
        numeric_sort = int(sort)
        now = self._now()
        with self._lock:

            def body() -> tuple[BagSortRecord, str]:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record a bag sort for it"
                    )
                existing = self._conn.execute(
                    "SELECT * FROM bag_sort_states WHERE account_key = ? AND ops = ?",
                    (record.account_key, numeric_ops),
                ).fetchone()
                if existing is not None:
                    if int(existing["sort"]) == numeric_sort:
                        return _bag_sort_from_row(existing), "unchanged"
                    self._conn.execute(
                        "UPDATE bag_sort_states SET role_id = ?, server_id = ?,"
                        " sort = ?, observed_at = ?, packet_sequence = ?,"
                        " recorded_at = ?, origin = ?"
                        " WHERE account_key = ? AND ops = ?",
                        (
                            record.role_id,
                            record.server_id,
                            numeric_sort,
                            int(observed_at),
                            int(packet_sequence),
                            now,
                            BAG_SORT_ORIGIN_LOCAL_OBSERVATION,
                            record.account_key,
                            numeric_ops,
                        ),
                    )
                    outcome = "updated"
                else:
                    self._conn.execute(
                        "INSERT INTO bag_sort_states (account_key, role_id,"
                        " server_id, ops, sort, observed_at, packet_sequence,"
                        " recorded_at, origin)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.account_key,
                            record.role_id,
                            record.server_id,
                            numeric_ops,
                            numeric_sort,
                            int(observed_at),
                            int(packet_sequence),
                            now,
                            BAG_SORT_ORIGIN_LOCAL_OBSERVATION,
                        ),
                    )
                    outcome = "created"
                row = self._conn.execute(
                    "SELECT * FROM bag_sort_states WHERE account_key = ? AND ops = ?",
                    (record.account_key, numeric_ops),
                ).fetchone()
                if row is None:  # pragma: no cover - the write just succeeded
                    raise RoleStoreError(
                        "bag sort row vanished inside its own transaction"
                    )
                return _bag_sort_from_row(row), outcome

            return self._transaction(body)

    # --------------------------------------------------- chapter fight facts

    def chapter_fight_clears(
        self, *, account_key: str
    ) -> tuple[ChapterFightClearRecord, ...]:
        """Every stored chapter-fight clear of one account, ordered by key."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chapter_fight_clears WHERE account_key = ? "
                "ORDER BY chapter_id, fight_id",
                (account_key,),
            ).fetchall()
        return tuple(_chapter_fight_clear_from_row(row) for row in rows)

    def chapter_fight_clear(
        self, *, account_key: str, chapter_id: int, fight_id: int
    ) -> ChapterFightClearRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chapter_fight_clears WHERE account_key = ? "
                "AND chapter_id = ? AND fight_id = ?",
                (account_key, int(chapter_id), int(fight_id)),
            ).fetchone()
        return None if row is None else _chapter_fight_clear_from_row(row)

    def record_chapter_fight_clear(
        self,
        *,
        record: RoleRecord,
        chapter_id: int,
        fight_id: int,
        map_index: int,
        observed_at: int,
        request_sequence: int,
        settlement_sequence: int,
        reply_sequence: int,
    ) -> tuple[ChapterFightClearRecord, bool]:
        """Persist one *observed* chapter-fight settlement, or return the stored one.

        The caller must only reach this after the three packets of ONE connection
        are complete -- the client's ``C2S 69`` (request, which names chapter /
        fight / map index), its ``C2S 67`` (settlement) and this server's single
        ``S2C 96`` answer.  The write happens inside one transaction that first
        re-reads the role itself, exactly like the story and bag rows: a caller
        that invents a ``RoleRecord`` (or replays one from a session whose role
        was never bound) writes nothing.

        A second settlement of the same ``(chapter, fight)`` returns the stored
        row and reports ``created=False``: the first observation is the fact and
        a later sighting may not rewrite its time or its sequences.  Nothing
        here invents a reward, a drop, a completion time or any other progress.

        This is the no-money wrapper around
        :meth:`record_chapter_fight_clear_with_money`, kept for every caller
        (and every test) that only records the clear fact.
        """

        outcome = self.record_chapter_fight_clear_with_money(
            record=record,
            chapter_id=chapter_id,
            fight_id=fight_id,
            map_index=map_index,
            observed_at=observed_at,
            request_sequence=request_sequence,
            settlement_sequence=settlement_sequence,
            reply_sequence=reply_sequence,
        )
        return outcome.record, outcome.created

    def record_chapter_fight_clear_with_money(
        self,
        *,
        record: RoleRecord,
        chapter_id: int,
        fight_id: int,
        map_index: int,
        observed_at: int,
        request_sequence: int,
        settlement_sequence: int,
        reply_sequence: int,
        wallet_money: float = 0.0,
        wallet_provenance: str = "",
        special_food: dict[int, int] | None = None,
        special_provenance: str = "",
    ) -> ChapterFightClearOutcome:
        """Record the clear row and, when given, the fight's rewards in one transaction.

        ``wallet_money`` is the recovered ``guankaMonster.moneyLower/Upper``
        amount for a decoded victory (``recovered_rewards.FightMoneyReward``).
        ``special_food`` is the recovered ``specialCard`` dog-food grant
        (``recovered_rewards.FightSpecialCardReward``, cfg id -> count).  Both
        are written **inside the same transaction** that inserts the clear row:
        a wallet or inventory refusal rolls the clear row back too, so a
        settlement never ends half-recorded or half-paid.  A replay
        (``created=False``) returns the stored row and the values it observed
        without writing any of them.
        """

        numeric_chapter = int(chapter_id)
        numeric_fight = int(fight_id)
        numeric_index = int(map_index)
        numeric_money = float(wallet_money)
        if numeric_chapter <= 0:
            raise RoleStoreError(f"unusable chapter id {chapter_id!r}")
        if numeric_fight <= 0:
            raise RoleStoreError(f"unusable chapter fight id {fight_id!r}")
        if numeric_index <= 0:
            raise RoleStoreError(f"unusable chapter map index {map_index!r}")
        if numeric_money < 0:
            raise RoleStoreError(f"chapter-fight money cannot be negative: {wallet_money!r}")
        food: dict[int, int] = {}
        for cfg_id, count in dict(special_food or {}).items():
            numeric_cfg = int(cfg_id)
            numeric_count = int(count)
            if numeric_cfg <= 0:
                raise RoleStoreError(f"unusable dog-food cfg id {cfg_id!r}")
            if numeric_count <= 0:
                raise RoleStoreError(
                    f"dog-food count for cfg {numeric_cfg} must be positive, "
                    f"got {count!r}"
                )
            food[numeric_cfg] = numeric_count
        if food and not str(special_provenance):
            raise RoleStoreError(
                "a chapter-fight dog-food grant must carry a provenance"
            )
        now = self._now()
        with self._lock:

            def body() -> ChapterFightClearOutcome:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record a chapter-fight "
                        f"clear for it"
                    )
                existing = self._conn.execute(
                    "SELECT * FROM chapter_fight_clears WHERE account_key = ? "
                    "AND chapter_id = ? AND fight_id = ?",
                    (record.account_key, numeric_chapter, numeric_fight),
                ).fetchone()
                if existing is not None:
                    current = self._role_wallet_amount_locked(
                        record.account_key, WALLET_MONEY
                    )
                    special_current = {
                        cfg_id: self._card_special_count_locked(
                            record.account_key, cfg_id
                        )
                        for cfg_id in sorted(food)
                    }
                    return ChapterFightClearOutcome(
                        record=_chapter_fight_clear_from_row(existing),
                        created=False,
                        money_before=current,
                        money_after=current,
                        money_granted=0.0,
                        special_food_granted={},
                        special_counts_after=special_current,
                    )
                self._conn.execute(
                    "INSERT INTO chapter_fight_clears (account_key, role_id,"
                    " server_id, chapter_id, fight_id, map_index, observed_at,"
                    " request_sequence, settlement_sequence, reply_sequence,"
                    " recorded_at, origin)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.account_key,
                        record.role_id,
                        record.server_id,
                        numeric_chapter,
                        numeric_fight,
                        numeric_index,
                        int(observed_at),
                        int(request_sequence),
                        int(settlement_sequence),
                        int(reply_sequence),
                        now,
                        CHAPTER_FIGHT_ORIGIN_LOCAL_OBSERVATION,
                    ),
                )
                money_before = self._role_wallet_amount_locked(
                    record.account_key, WALLET_MONEY
                )
                money_after = money_before
                if numeric_money:
                    # A refusal here (empty provenance, a bogus value name, a
                    # negative result) raises out of the transaction and rolls
                    # the clear INSERT back with it.
                    money_after = self._write_role_wallet_locked(
                        account_key=record.account_key,
                        value_name=WALLET_MONEY,
                        delta=numeric_money,
                        provenance=wallet_provenance,
                        now=now,
                    )
                special_after: dict[int, int] = {}
                for cfg_id, count in sorted(food.items()):
                    # A refusal here (or a SQLite error) rolls the clear row and
                    # the money back with it: the reward can never exist
                    # without the settlement it was paid for, or vice versa.
                    special_after[cfg_id] = self._add_card_special_locked(
                        account_key=record.account_key,
                        cfg_id=cfg_id,
                        count=count,
                        provenance=str(special_provenance),
                        now=now,
                    )
                row = self._conn.execute(
                    "SELECT * FROM chapter_fight_clears WHERE account_key = ? "
                    "AND chapter_id = ? AND fight_id = ?",
                    (record.account_key, numeric_chapter, numeric_fight),
                ).fetchone()
                if row is None:  # pragma: no cover - INSERT just succeeded
                    raise RoleStoreError(
                        "chapter-fight clear row vanished inside its own transaction"
                    )
                return ChapterFightClearOutcome(
                    record=_chapter_fight_clear_from_row(row),
                    created=True,
                    money_before=money_before,
                    money_after=money_after,
                    money_granted=numeric_money,
                    special_food_granted=dict(food),
                    special_counts_after=special_after,
                )

            return self._transaction(body)

    # --------------------------------------------------- chapter event facts

    def chapter_event_clears(
        self, *, account_key: str
    ) -> tuple[ChapterEventClearRecord, ...]:
        """Every stored chapter-event clear of one account, ordered by key."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chapter_event_clears WHERE account_key = ? "
                "ORDER BY chapter_id, map_index",
                (account_key,),
            ).fetchall()
        return tuple(_chapter_event_clear_from_row(row) for row in rows)

    def chapter_event_clear(
        self, *, account_key: str, chapter_id: int, map_index: int
    ) -> ChapterEventClearRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chapter_event_clears WHERE account_key = ? "
                "AND chapter_id = ? AND map_index = ?",
                (account_key, int(chapter_id), int(map_index)),
            ).fetchone()
        return None if row is None else _chapter_event_clear_from_row(row)

    def record_chapter_event_clear(
        self,
        *,
        record: RoleRecord,
        chapter_id: int,
        map_index: int,
        event_cfg_id: int,
        option_index: int,
        observed_at: int,
        request_sequence: int,
        reply_sequence: int,
    ) -> tuple[ChapterEventClearRecord, bool]:
        """Persist one *observed* chapter-map event action, or return the stored one.

        The caller must only reach this after a validated ``C2S 224`` and the
        single ``S2C 398`` success answer were both written on THIS connection.
        The write happens inside one transaction that first re-reads the role
        itself, exactly like the story, bag and fight rows: a caller that
        invents a ``RoleRecord`` writes nothing.

        A second action on the same ``(chapter, map_index)`` returns the stored
        row and reports ``created=False``: the first observation is the fact and
        a later sighting may not rewrite its time or its sequences.  Nothing
        here invents a reward, a drop or a completion time.
        """

        numeric_chapter = int(chapter_id)
        numeric_index = int(map_index)
        numeric_cfg = int(event_cfg_id)
        numeric_option = int(option_index)
        if numeric_chapter <= 0:
            raise RoleStoreError(f"unusable chapter id {chapter_id!r}")
        if numeric_index <= 0:
            raise RoleStoreError(f"unusable chapter map index {map_index!r}")
        if numeric_cfg <= 0:
            raise RoleStoreError(f"unusable chapter event cfg id {event_cfg_id!r}")
        if numeric_option < 0:
            raise RoleStoreError(f"unusable option index {option_index!r}")
        now = self._now()
        with self._lock:

            def body() -> tuple[ChapterEventClearRecord, bool]:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record a chapter-event "
                        f"clear for it"
                    )
                existing = self._conn.execute(
                    "SELECT * FROM chapter_event_clears WHERE account_key = ? "
                    "AND chapter_id = ? AND map_index = ?",
                    (record.account_key, numeric_chapter, numeric_index),
                ).fetchone()
                if existing is not None:
                    return _chapter_event_clear_from_row(existing), False
                self._conn.execute(
                    "INSERT INTO chapter_event_clears (account_key, role_id,"
                    " server_id, chapter_id, map_index, event_cfg_id,"
                    " option_index, observed_at, request_sequence,"
                    " reply_sequence, recorded_at, origin)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.account_key,
                        record.role_id,
                        record.server_id,
                        numeric_chapter,
                        numeric_index,
                        numeric_cfg,
                        numeric_option,
                        int(observed_at),
                        int(request_sequence),
                        int(reply_sequence),
                        now,
                        CHAPTER_EVENT_ORIGIN_LOCAL_OBSERVATION,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM chapter_event_clears WHERE account_key = ? "
                    "AND chapter_id = ? AND map_index = ?",
                    (record.account_key, numeric_chapter, numeric_index),
                ).fetchone()
                if row is None:  # pragma: no cover - INSERT just succeeded
                    raise RoleStoreError(
                        "chapter-event clear row vanished inside its own transaction"
                    )
                return _chapter_event_clear_from_row(row), True

            return self._transaction(body)

    # ------------------------------------------ chapter event reward ledger

    def chapter_event_reward_grants(
        self, *, account_key: str
    ) -> tuple[ChapterEventRewardGrantRecord, ...]:
        """Every event-reward ledger row of one account, ordered by key."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chapter_event_reward_grants"
                " WHERE account_key = ? ORDER BY chapter_id, map_index",
                (account_key,),
            ).fetchall()
        return tuple(_chapter_event_reward_grant_from_row(row) for row in rows)

    def chapter_event_reward_grant(
        self, *, account_key: str, chapter_id: int, map_index: int
    ) -> ChapterEventRewardGrantRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chapter_event_reward_grants"
                " WHERE account_key = ? AND chapter_id = ? AND map_index = ?",
                (account_key, int(chapter_id), int(map_index)),
            ).fetchone()
        return None if row is None else _chapter_event_reward_grant_from_row(row)

    def chapter_event_reward_ledger_total(self, *, account_key: str) -> int:
        """The sum of this account's ledger ``crystal`` values (not a wallet)."""

        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(crystal), 0) AS total"
                " FROM chapter_event_reward_grants WHERE account_key = ?",
                (account_key,),
            ).fetchone()
        return int(row["total"])

    def record_chapter_event_clear_with_grant(
        self,
        *,
        record: RoleRecord,
        chapter_id: int,
        map_index: int,
        event_cfg_id: int,
        option_index: int,
        observed_at: int,
        request_sequence: int,
        reply_sequence: int,
        reward_id: int,
        reward_crystal: int,
        crystal_bucket: str | None = None,
        bucket_provenance: str = CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
    ) -> ChapterEventClearOutcome:
        """Persist one observed event action and its reward authority atomically.

        The clear row and the ``chapter_event_reward_grants`` row are written
        inside ONE ``BEGIN IMMEDIATE`` transaction (the same pattern as
        ``record_chapter_fight_clear_with_money``), so a ledger refusal takes the
        clear row back with it.  A second action on the same
        ``(chapter, map_index)`` returns the stored rows and reports
        ``created=False``; a clear that has no ledger row yet (written by the
        pre-ledger wrapper, or by a build before structure 12) is backfilled
        exactly once -- the primary key makes a second grant impossible.

        The reward value is the shipped ``guankaReward[row].crystal``; the
        bucket is ``None`` unless it was recovered, and this method refuses a
        combination that pretends an unknown bucket is a known one.  Nothing
        here touches ``role_wallet`` or the client-visible balance.
        """

        numeric_chapter = int(chapter_id)
        numeric_index = int(map_index)
        numeric_cfg = int(event_cfg_id)
        numeric_option = int(option_index)
        numeric_reward = int(reward_id)
        numeric_crystal = int(reward_crystal)
        if numeric_chapter <= 0:
            raise RoleStoreError(f"unusable chapter id {chapter_id!r}")
        if numeric_index <= 0:
            raise RoleStoreError(f"unusable chapter map index {map_index!r}")
        if numeric_cfg <= 0:
            raise RoleStoreError(f"unusable chapter event cfg id {event_cfg_id!r}")
        if numeric_option < 0:
            raise RoleStoreError(f"unusable option index {option_index!r}")
        if numeric_reward <= 0:
            raise RoleStoreError(f"unusable reward id {reward_id!r}")
        if numeric_crystal <= 0:
            raise RoleStoreError(f"unusable reward crystal {reward_crystal!r}")
        bucket = None if crystal_bucket is None else str(crystal_bucket)
        if bucket is not None and bucket not in CRYSTAL_BUCKET_NAMES:
            raise RoleStoreError(f"unknown crystal bucket {crystal_bucket!r}")
        bucket_source = str(bucket_provenance)
        if bucket_source not in CRYSTAL_BUCKET_PROVENANCES:
            raise RoleStoreError(
                f"unknown bucket provenance {bucket_provenance!r}"
            )
        if bucket is None and bucket_source != CRYSTAL_BUCKET_PROVENANCE_UNKNOWN:
            raise RoleStoreError(
                "a missing crystal bucket must be recorded as unknown provenance"
            )
        if bucket is not None and bucket_source == CRYSTAL_BUCKET_PROVENANCE_UNKNOWN:
            raise RoleStoreError(
                f"crystal bucket {bucket!r} cannot carry unknown provenance"
            )
        now = self._now()
        with self._lock:

            def body() -> ChapterEventClearOutcome:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record a chapter-event "
                        f"clear for it"
                    )
                existing = self._conn.execute(
                    "SELECT * FROM chapter_event_clears WHERE account_key = ? "
                    "AND chapter_id = ? AND map_index = ?",
                    (record.account_key, numeric_chapter, numeric_index),
                ).fetchone()
                created = False
                if existing is None:
                    self._conn.execute(
                        "INSERT INTO chapter_event_clears (account_key, role_id,"
                        " server_id, chapter_id, map_index, event_cfg_id,"
                        " option_index, observed_at, request_sequence,"
                        " reply_sequence, recorded_at, origin)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.account_key,
                            record.role_id,
                            record.server_id,
                            numeric_chapter,
                            numeric_index,
                            numeric_cfg,
                            numeric_option,
                            int(observed_at),
                            int(request_sequence),
                            int(reply_sequence),
                            now,
                            CHAPTER_EVENT_ORIGIN_LOCAL_OBSERVATION,
                        ),
                    )
                    created = True
                    existing = self._conn.execute(
                        "SELECT * FROM chapter_event_clears WHERE account_key = ? "
                        "AND chapter_id = ? AND map_index = ?",
                        (record.account_key, numeric_chapter, numeric_index),
                    ).fetchone()
                    if existing is None:  # pragma: no cover - INSERT just succeeded
                        raise RoleStoreError(
                            "chapter-event clear row vanished inside its own "
                            "transaction"
                        )
                grant_row = self._conn.execute(
                    "SELECT * FROM chapter_event_reward_grants WHERE account_key = ? "
                    "AND chapter_id = ? AND map_index = ?",
                    (record.account_key, numeric_chapter, numeric_index),
                ).fetchone()
                grant_created = False
                if grant_row is None:
                    self._write_chapter_event_reward_grant_locked(
                        role=record,
                        chapter_id=numeric_chapter,
                        map_index=numeric_index,
                        event_cfg_id=numeric_cfg,
                        option_index=numeric_option,
                        reward_id=numeric_reward,
                        reward_crystal=numeric_crystal,
                        crystal_bucket=bucket,
                        bucket_provenance=bucket_source,
                        request_sequence=int(request_sequence),
                        reply_sequence=int(reply_sequence),
                        now=now,
                    )
                    grant_created = True
                    grant_row = self._conn.execute(
                        "SELECT * FROM chapter_event_reward_grants"
                        " WHERE account_key = ? AND chapter_id = ? AND map_index = ?",
                        (record.account_key, numeric_chapter, numeric_index),
                    ).fetchone()
                    if grant_row is None:  # pragma: no cover - INSERT just succeeded
                        raise RoleStoreError(
                            "chapter-event reward ledger row vanished inside its "
                            "own transaction"
                        )
                total_row = self._conn.execute(
                    "SELECT COALESCE(SUM(crystal), 0) AS total"
                    " FROM chapter_event_reward_grants WHERE account_key = ?",
                    (record.account_key,),
                ).fetchone()
                assert existing is not None and grant_row is not None
                return ChapterEventClearOutcome(
                    record=_chapter_event_clear_from_row(existing),
                    created=created,
                    grant=_chapter_event_reward_grant_from_row(grant_row),
                    grant_created=grant_created,
                    ledger_total_after=int(total_row["total"]),
                )

            return self._transaction(body)

    def _write_chapter_event_reward_grant_locked(
        self,
        *,
        role: RoleRecord,
        chapter_id: int,
        map_index: int,
        event_cfg_id: int,
        option_index: int,
        reward_id: int,
        reward_crystal: int,
        crystal_bucket: str | None,
        bucket_provenance: str,
        request_sequence: int,
        reply_sequence: int,
        now: int,
    ) -> None:
        """Insert one ledger row on the caller's open transaction.

        Kept separate so a test can force a failure after the clear INSERT and
        prove the whole transaction rolls back; the public method is the only
        production caller.
        """

        self._conn.execute(
            "INSERT INTO chapter_event_reward_grants"
            " (account_key, role_id, server_id, chapter_id, map_index,"
            " event_cfg_id, option_index, reward_id, crystal, crystal_bucket,"
            " bucket_provenance, granted_at, request_sequence, reply_sequence,"
            " origin, provenance)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                role.account_key,
                role.role_id,
                role.server_id,
                chapter_id,
                map_index,
                event_cfg_id,
                option_index,
                reward_id,
                reward_crystal,
                crystal_bucket,
                bucket_provenance,
                now,
                request_sequence,
                reply_sequence,
                CHAPTER_EVENT_REWARD_ORIGIN_LOCAL_OBSERVATION,
                CHAPTER_EVENT_REWARD_PROVENANCE_TRANSACTION,
            ),
        )

    # --------------------------------------------------- chapter box accepts

    def chapter_box_accepts(
        self, *, account_key: str
    ) -> tuple[ChapterBoxAcceptRecord, ...]:
        """Every stored chapter-box accept of one account, ordered by key."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chapter_box_accepts WHERE account_key = ? "
                "ORDER BY chapter_id, box_index",
                (account_key,),
            ).fetchall()
        return tuple(_chapter_box_accept_from_row(row) for row in rows)

    def chapter_box_accept(
        self, *, account_key: str, role_id: int, chapter_id: int, box_index: int
    ) -> ChapterBoxAcceptRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chapter_box_accepts WHERE account_key = ? "
                "AND role_id = ? AND chapter_id = ? AND box_index = ?",
                (
                    account_key,
                    int(role_id),
                    int(chapter_id),
                    int(box_index),
                ),
            ).fetchone()
        return None if row is None else _chapter_box_accept_from_row(row)

    def accept_chapter_box(
        self,
        *,
        record: RoleRecord,
        chapter_id: int,
        box_index: int,
        reward_id: int,
        food: dict[int, int],
        reply_sequence: int,
        origin: str,
    ) -> tuple[ChapterBoxAcceptRecord, bool]:
        """Open one chapter chest once: the accept row and its dog food together.

        The caller reaches this only after it has validated the chest against
        the shipped ``guankaChapter``/``guankaReward`` rows and the client's own
        progress records.  The write is ONE transaction: re-read the role, refuse
        a second accept of the same ``(role, chapter, box)`` (returning the
        stored row with ``created=False`` and granting nothing), insert the
        accept row, then add each dog-food count to ``card_special_inventory``
        on the same connection.  Any failure rolls the whole box back -- a
        displayed reward can never exist without its stored row, and vice versa.
        """

        numeric_chapter = int(chapter_id)
        numeric_box = int(box_index)
        numeric_reward = int(reward_id)
        numeric_reply = int(reply_sequence)
        if numeric_chapter <= 0:
            raise RoleStoreError(f"unusable chapter id {chapter_id!r}")
        if numeric_box < 0:
            raise RoleStoreError(f"unusable box index {box_index!r}")
        if numeric_reward <= 0:
            raise RoleStoreError(f"unusable box reward id {reward_id!r}")
        if numeric_reply < 0:
            raise RoleStoreError(f"unusable reply sequence {reply_sequence!r}")
        if not str(origin):
            raise RoleStoreError("chapter-box origin must be non-empty")
        counts: dict[int, int] = {}
        for cfg_id, count in dict(food).items():
            numeric_cfg = int(cfg_id)
            numeric_count = int(count)
            if numeric_cfg <= 0:
                raise RoleStoreError(f"unusable dog-food cfg id {cfg_id!r}")
            if numeric_count <= 0:
                raise RoleStoreError(
                    f"dog-food count for cfg {numeric_cfg} must be positive, "
                    f"got {count!r}"
                )
            counts[numeric_cfg] = numeric_count
        if not counts:
            raise RoleStoreError(
                "a chapter-box accept must grant at least one persisted dog food"
            )
        now = self._now()
        with self._lock:

            def body() -> tuple[ChapterBoxAcceptRecord, bool]:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to accept a chapter box "
                        f"for it"
                    )
                existing = self._conn.execute(
                    "SELECT * FROM chapter_box_accepts WHERE account_key = ? "
                    "AND role_id = ? AND chapter_id = ? AND box_index = ?",
                    (
                        record.account_key,
                        record.role_id,
                        numeric_chapter,
                        numeric_box,
                    ),
                ).fetchone()
                if existing is not None:
                    return _chapter_box_accept_from_row(existing), False
                self._conn.execute(
                    "INSERT INTO chapter_box_accepts (account_key, role_id,"
                    " chapter_id, box_index, reward_id, accepted_at,"
                    " reply_sequence, origin)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.account_key,
                        record.role_id,
                        numeric_chapter,
                        numeric_box,
                        numeric_reward,
                        now,
                        numeric_reply,
                        str(origin),
                    ),
                )
                for cfg_id, count in sorted(counts.items()):
                    self._add_card_special_locked(
                        account_key=record.account_key,
                        cfg_id=cfg_id,
                        count=count,
                        provenance=str(origin),
                        now=now,
                    )
                row = self._conn.execute(
                    "SELECT * FROM chapter_box_accepts WHERE account_key = ? "
                    "AND role_id = ? AND chapter_id = ? AND box_index = ?",
                    (
                        record.account_key,
                        record.role_id,
                        numeric_chapter,
                        numeric_box,
                    ),
                ).fetchone()
                if row is None:  # pragma: no cover - INSERT just succeeded
                    raise RoleStoreError(
                        "chapter-box accept row vanished inside its own transaction"
                    )
                return _chapter_box_accept_from_row(row), True

            return self._transaction(body)

    # ------------------------------------------------------ chapter task rows
    #
    # TASK-0240: the client's own `C2S 227 chapterMapTask` receive/complete
    # actions.  The rows ride the archive's existing ``meta`` table (structure
    # 1) under one key per account, so every older archive opens unchanged, no
    # new table is created and ``SCHEMA_VERSION`` stays 12.  The read is strict:
    # a payload that does not validate raises instead of yielding a half-read
    # row set, and the caller names the refusal in its own event.

    def chapter_task_rows(
        self, *, account_key: str
    ) -> tuple[chapter_map_task.ChapterTaskRow, ...]:
        """Every stored chapter-task row of one account, ordered by key.

        Raises :class:`RoleStoreError` when the stored payload exists but is
        unusable; an archive that never stored one yields ``()``.
        """

        key = str(account_key)
        with self._lock:
            return self._chapter_task_rows_locked(key)

    def _chapter_task_rows_locked(
        self, account_key: str
    ) -> tuple[chapter_map_task.ChapterTaskRow, ...]:
        raw = self._meta(chapter_map_task.chapter_task_meta_key(account_key))
        if raw is None:
            return ()
        try:
            return chapter_map_task.parse_rows(raw, account_key=account_key)
        except chapter_map_task.ChapterMapTaskError as exc:
            raise RoleStoreError(
                f"chapter-task rows of this archive are unreadable "
                f"({exc.reason}): {exc}"
            ) from exc

    def record_chapter_task_event(
        self,
        *,
        record: RoleRecord,
        chapter_id: int,
        task_id: int,
        action: int,
        sync_count: int,
        request_sequence: int,
        reply_sequence: int,
        observed_at: int,
    ) -> ChapterTaskOutcome:
        """Apply one observed receive/complete and return the transition.

        One ``BEGIN IMMEDIATE`` transaction re-reads the bound role, reads this
        account's rows, and applies :func:`chapter_map_task.task_transition`:

        * a receive on a task that is not received writes ``received``;
        * a complete on a received, not completed task writes ``completed`` and
          keeps ``received``;
        * a repeat receive / repeat complete changes nothing and reports
          ``already_handled`` (a completed task is never downgraded);
        * a complete on a task this account never received changes nothing and
          reports ``not_received`` -- the client's own ``isTaskReceived`` gate
          would have refused it, so recording it would invent progress.

        The sync counter is stored with the observation but is never compared or
        generated (U-239-3).  Nothing here touches a wallet, an inventory, a
        card row or a reward (U-239-1).
        """

        numeric_chapter = int(chapter_id)
        numeric_task = int(task_id)
        numeric_action = int(action)
        numeric_sync = int(sync_count)
        numeric_request = int(request_sequence)
        numeric_reply = int(reply_sequence)
        numeric_observed = int(observed_at)
        if numeric_chapter <= 0:
            raise RoleStoreError(f"unusable chapter id {chapter_id!r}")
        if numeric_task <= 0:
            raise RoleStoreError(f"unusable chapter task id {task_id!r}")
        if numeric_action not in chapter_map_task.TASK_ACTIONS:
            raise RoleStoreError(f"unusable chapter task action {action!r}")
        if numeric_sync < 0:
            raise RoleStoreError(f"unusable task sync count {sync_count!r}")
        if numeric_request < 0:
            raise RoleStoreError(f"unusable request sequence {request_sequence!r}")
        if numeric_reply < 0:
            raise RoleStoreError(f"unusable reply sequence {reply_sequence!r}")
        if numeric_observed < 0:
            raise RoleStoreError(f"unusable observation time {observed_at!r}")
        with self._lock:

            def body() -> ChapterTaskOutcome:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record a chapter-task "
                        f"action for it"
                    )
                rows = self._chapter_task_rows_locked(record.account_key)
                current = next(
                    (
                        row
                        for row in rows
                        if row.chapter_id == numeric_chapter
                        and row.task_id == numeric_task
                    ),
                    None,
                )
                if current is not None and current.role_id != record.role_id:
                    # One account holds one role; a row of another role under
                    # the same (chapter, task) can only be archive corruption.
                    raise RoleStoreError(
                        "a chapter-task row of this account belongs to another "
                        f"role ({current.role_id}); refusing to overwrite it"
                    )
                state = chapter_map_task.task_transition(current, numeric_action)
                if state in (
                    chapter_map_task.STATE_ALREADY_HANDLED,
                    chapter_map_task.STATE_NOT_RECEIVED,
                ):
                    return ChapterTaskOutcome(
                        action=numeric_action,
                        state=state,
                        row=current,
                        rows_after=rows,
                        created=False,
                        state_changed=False,
                        sync_count=numeric_sync,
                        request_sequence=numeric_request,
                        reply_sequence=numeric_reply,
                        observed_at=numeric_observed,
                    )
                created = current is None
                if numeric_action == chapter_map_task.TASK_RECEIVE:
                    updated = chapter_map_task.ChapterTaskRow(
                        account_key=record.account_key,
                        role_id=record.role_id,
                        chapter_id=numeric_chapter,
                        task_id=numeric_task,
                        received=True,
                        completed=bool(current is not None and current.completed),
                        received_at=numeric_observed,
                        completed_at=(
                            0 if current is None else current.completed_at
                        ),
                    )
                else:
                    assert current is not None  # task_transition guarantees it
                    updated = chapter_map_task.ChapterTaskRow(
                        account_key=record.account_key,
                        role_id=record.role_id,
                        chapter_id=numeric_chapter,
                        task_id=numeric_task,
                        received=True,
                        completed=True,
                        received_at=current.received_at,
                        completed_at=numeric_observed,
                    )
                replaced = tuple(
                    row
                    for row in rows
                    if not (
                        row.chapter_id == numeric_chapter
                        and row.task_id == numeric_task
                    )
                ) + (updated,)
                ordered = tuple(
                    sorted(replaced, key=chapter_map_task.ChapterTaskRow.sort_key)
                )
                payload = chapter_map_task.encode_rows(
                    record.account_key, ordered
                )
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (
                        self._validate_meta_key(
                            chapter_map_task.chapter_task_meta_key(
                                record.account_key
                            )
                        ),
                        payload,
                    ),
                )
                return ChapterTaskOutcome(
                    action=numeric_action,
                    state=state,
                    row=updated,
                    rows_after=ordered,
                    created=created,
                    state_changed=True,
                    sync_count=numeric_sync,
                    request_sequence=numeric_request,
                    reply_sequence=numeric_reply,
                    observed_at=numeric_observed,
                )

            return self._transaction(body)

    # ------------------------------------------------------------ card instances

    def card_instances(self, *, account_key: str) -> tuple[CardInstanceRecord, ...]:
        """Every stored card instance of one account, ordered by instance id."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM card_instances WHERE account_key = ? "
                "ORDER BY instance_id",
                (account_key,),
            ).fetchall()
        return tuple(_card_instance_from_row(row) for row in rows)

    def max_draw_instance_serial(self) -> int:
        """Highest ``revival-draw-NNNN-*`` / ``revival-fixture-draw-NNNN-*`` serial.

        Draw instance ids carry a process-wide four-digit serial.  A fresh
        server process must continue after the highest serial already stored:
        otherwise its first draw reuses ids such as ``revival-draw-0001-01``
        and silently overwrites the previous session's card rows.
        """

        with self._lock:
            rows = self._conn.execute(
                "SELECT instance_id FROM card_instances WHERE "
                "instance_id LIKE 'revival-draw-%' OR "
                "instance_id LIKE 'revival-fixture-draw-%'"
            ).fetchall()
        maximum = 0
        for row in rows:
            parts = str(row["instance_id"]).split("-")
            try:
                maximum = max(maximum, int(parts[parts.index("draw") + 1]))
            except (ValueError, IndexError):
                continue
        return maximum

    def record_card_instance(
        self,
        *,
        record: RoleRecord,
        instance_id: str,
        card_cfg_id: int,
        level: int = 1,
        exp: int = 0,
        advance_level: int = 0,
        sfm_id1: int = 0,
        sfm_id2: int = 0,
        sfm_id3: int = 0,
        sfm_id4: int = 0,
        equip_id1: str = "",
        equip_id2: str = "",
        equip_id3: str = "",
        equip_id4: str = "",
        locked: bool = False,
        refine_lv: int = 0,
        refine_lv_times: int = 0,
        learn_atk: int = 0,
        learn_hp: int = 0,
        learn_state: int = 0,
        refine_skill: str = "",
        god_weapon_id: str = "",
        origin: str = CARD_INSTANCE_ORIGIN_DETERMINISTIC_TEST_FIXTURE,
        fixture_id: str | None = None,
        fixture_kind: str | None = None,
    ) -> tuple[CardInstanceRecord, str]:
        """Persist one card instance and return ``(row, created|updated)``.

        The columns mirror the client's own ``returnCardInfo`` row.  The write
        re-reads the bound role inside the transaction, exactly like the story,
        bag and chapter-fight rows, so a caller that invents a ``RoleRecord``
        writes nothing.  A deterministic fixture must name its ``fixture_id`` and
        ``fixture_kind``; the row is never replayed without the Owner draw-test
        overlay.
        """

        text = str(instance_id).strip()
        if not text or len(text) > 255:
            raise RoleStoreError(f"unusable card instance id {instance_id!r}")
        numeric_cfg = int(card_cfg_id)
        if numeric_cfg <= 0:
            raise RoleStoreError(f"unusable card config id {card_cfg_id!r}")
        numeric_fields = {
            "level": int(level),
            "exp": int(exp),
            "advance_level": int(advance_level),
            "sfm_id1": int(sfm_id1),
            "sfm_id2": int(sfm_id2),
            "sfm_id3": int(sfm_id3),
            "sfm_id4": int(sfm_id4),
            "refine_lv": int(refine_lv),
            "refine_lv_times": int(refine_lv_times),
            "learn_atk": int(learn_atk),
            "learn_hp": int(learn_hp),
            "learn_state": int(learn_state),
        }
        for name, value in numeric_fields.items():
            if value < 0:
                raise RoleStoreError(f"unusable card {name} {value!r}")
        if fixture_kind not in (
            None,
            CARD_INSTANCE_FIXTURE_KIND_DETERMINISTIC_DRAW,
        ):
            raise RoleStoreError(f"unknown card fixture kind {fixture_kind!r}")
        now = self._now()
        with self._lock:
            return self._transaction(
                lambda: self._write_card_instance_locked(
                    record=record,
                    now=now,
                    instance_id=text,
                    card_cfg_id=numeric_cfg,
                    level=numeric_fields["level"],
                    exp=numeric_fields["exp"],
                    advance_level=numeric_fields["advance_level"],
                    sfm_id1=numeric_fields["sfm_id1"],
                    sfm_id2=numeric_fields["sfm_id2"],
                    sfm_id3=numeric_fields["sfm_id3"],
                    sfm_id4=numeric_fields["sfm_id4"],
                    equip_id1=str(equip_id1),
                    equip_id2=str(equip_id2),
                    equip_id3=str(equip_id3),
                    equip_id4=str(equip_id4),
                    locked=bool(locked),
                    refine_lv=numeric_fields["refine_lv"],
                    refine_lv_times=numeric_fields["refine_lv_times"],
                    learn_atk=numeric_fields["learn_atk"],
                    learn_hp=numeric_fields["learn_hp"],
                    learn_state=numeric_fields["learn_state"],
                    refine_skill=str(refine_skill),
                    god_weapon_id=str(god_weapon_id),
                    origin=str(origin),
                    fixture_id=fixture_id,
                    fixture_kind=fixture_kind,
                )
            )

    def _write_card_instance_locked(
        self,
        *,
        record: RoleRecord,
        now: int,
        instance_id: str,
        card_cfg_id: int,
        level: int,
        exp: int,
        advance_level: int,
        sfm_id1: int,
        sfm_id2: int,
        sfm_id3: int,
        sfm_id4: int,
        equip_id1: str,
        equip_id2: str,
        equip_id3: str,
        equip_id4: str,
        locked: bool,
        refine_lv: int,
        refine_lv_times: int,
        learn_atk: int,
        learn_hp: int,
        learn_state: int,
        refine_skill: str,
        god_weapon_id: str,
        origin: str,
        fixture_id: str | None,
        fixture_kind: str | None,
    ) -> tuple[CardInstanceRecord, str]:
        """Insert or update one ``card_instances`` row on the caller's transaction.

        Extracted verbatim from :meth:`record_card_instance` (TASK-0235) so the
        ticket draw can write every drawn row inside the same transaction that
        charges the balance, without a second ``BEGIN IMMEDIATE``.
        ``record_card_instance`` keeps its own validation and passes the same
        values, so every existing caller's behaviour is unchanged.
        """

        role_row = self._conn.execute(
            "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
            (record.account_key, record.role_id),
        ).fetchone()
        if role_row is None:
            raise RoleStoreError(
                f"role archive {self._location} does not hold role "
                f"{record.role_id}; refusing to record a card instance "
                f"for it"
            )
        values = (
            record.role_id,
            record.server_id,
            card_cfg_id,
            level,
            exp,
            advance_level,
            sfm_id1,
            sfm_id2,
            sfm_id3,
            sfm_id4,
            equip_id1,
            equip_id2,
            equip_id3,
            equip_id4,
            1 if locked else 0,
            refine_lv,
            refine_lv_times,
            learn_atk,
            learn_hp,
            learn_state,
            refine_skill,
            god_weapon_id,
            origin,
            fixture_id,
            fixture_kind,
            now,
        )
        existing = self._conn.execute(
            "SELECT * FROM card_instances WHERE account_key = ? "
            "AND instance_id = ?",
            (record.account_key, instance_id),
        ).fetchone()
        columns = (
            "role_id, server_id, card_cfg_id, level, exp, advance_level,"
            " sfm_id1, sfm_id2, sfm_id3, sfm_id4, equip_id1, equip_id2,"
            " equip_id3, equip_id4, locked, refine_lv, refine_lv_times,"
            " learn_atk, learn_hp, learn_state, refine_skill,"
            " god_weapon_id, origin, fixture_id, fixture_kind, recorded_at"
        )
        if existing is None:
            self._conn.execute(
                f"INSERT INTO card_instances (account_key, instance_id,"
                f" {columns}) VALUES (?, ?, "
                + ", ".join("?" for _ in range(26))
                + ")",
                (record.account_key, instance_id, *values),
            )
            outcome = "created"
        else:
            assignments = ", ".join(
                f"{name.strip()} = ?"
                for name in columns.split(",")
            )
            self._conn.execute(
                f"UPDATE card_instances SET {assignments}"
                " WHERE account_key = ? AND instance_id = ?",
                (*values, record.account_key, instance_id),
            )
            outcome = "updated"
        row = self._conn.execute(
            "SELECT * FROM card_instances WHERE account_key = ? "
            "AND instance_id = ?",
            (record.account_key, instance_id),
        ).fetchone()
        if row is None:  # pragma: no cover - the write just succeeded
            raise RoleStoreError(
                "card instance row vanished inside its own transaction"
            )
        return _card_instance_from_row(row), outcome

    def card_instance(
        self, *, account_key: str, instance_id: str
    ) -> "CardInstanceRecord | None":
        """One stored card instance of one account, or ``None``."""

        row = self._conn.execute(
            "SELECT * FROM card_instances WHERE account_key = ? "
            "AND instance_id = ?",
            (account_key, str(instance_id)),
        ).fetchone()
        return _card_instance_from_row(row) if row is not None else None

    def set_card_refine(
        self,
        *,
        account_key: str,
        instance_id: str,
        refine_lv: int,
        refine_skill: str,
        provenance: str = "c2s310_refine_save",
        refine_lv_times: int | None = None,
    ) -> "CardInstanceRecord":
        """Update a card's refine tier/skills and bump the wash counter.

        TASK-0470: the client's 洗练 result is persisted on the card row so the
        next S2C 30 carries it back (dRefineLv / strRefineSkill).
        """

        with self._lock:

            def body() -> "CardInstanceRecord":
                now = self._now()
                row = self._conn.execute(
                    "SELECT instance_id, refine_lv_times FROM card_instances "
                    "WHERE account_key = ? AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                if row is None:
                    raise RoleStoreError(
                        f"account has no card instance {instance_id!r}"
                    )
                times = (
                    int(row[1]) if refine_lv_times is None
                    else int(refine_lv_times)
                )
                self._conn.execute(
                    "UPDATE card_instances SET refine_lv = ?, "
                    "refine_lv_times = ?, refine_skill = ? "
                    "WHERE account_key = ? AND instance_id = ?",
                    (int(refine_lv), times, str(refine_skill),
                     account_key, str(instance_id)),
                )
                updated = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                assert updated is not None
                return _card_instance_from_row(updated)

            return self._transaction(body)

    def apply_refine_wash(
        self,
        *,
        account_key: str,
        instance_id: str,
        max_fu: int,
        max_level: int = 10,
        provenance: str = "c2s195_refine_wash",
    ) -> "CardInstanceRecord":
        """Add one 洗练祝福; a full bar raises the wash tier by one."""

        with self._lock:

            def body() -> "CardInstanceRecord":
                row = self._conn.execute(
                    "SELECT refine_lv, refine_lv_times FROM card_instances "
                    "WHERE account_key = ? AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                if row is None:
                    raise RoleStoreError(
                        f"account has no card instance {instance_id!r}"
                    )
                level = max(1, int(row[0]))
                times = int(row[1])
                if int(max_fu) > 0:
                    times += 1
                    if times >= int(max_fu):
                        level = min(int(max_level), level + 1)
                        times = 0
                self._conn.execute(
                    "UPDATE card_instances SET refine_lv = ?, "
                    "refine_lv_times = ? WHERE account_key = ? AND instance_id = ?",
                    (level, times, account_key, str(instance_id)),
                )
                updated = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                assert updated is not None
                return _card_instance_from_row(updated)

            return self._transaction(body)

    def set_card_learn_stats(
        self,
        *,
        account_key: str,
        instance_id: str,
        learn_atk: int,
        learn_hp: int,
        provenance: str = "synthetic_dev_card_learn_stats",
    ) -> "CardInstanceRecord":
        """Set the additive learn stats the client adds to card hp/atk."""

        with self._lock:

            def body() -> "CardInstanceRecord":
                row = self._conn.execute(
                    "SELECT 1 FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                if row is None:
                    raise RoleStoreError(
                        f"account has no card instance {instance_id!r}"
                    )
                self._conn.execute(
                    "UPDATE card_instances SET learn_atk = ?, learn_hp = ? "
                    "WHERE account_key = ? AND instance_id = ?",
                    (int(learn_atk), int(learn_hp),
                     account_key, str(instance_id)),
                )
                updated = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                assert updated is not None
                return _card_instance_from_row(updated)

            return self._transaction(body)

    def set_card_skills(
        self,
        *,
        account_key: str,
        instance_id: str,
        sfm_id1: int,
        sfm_id2: int,
        sfm_id3: int,
        sfm_id4: int,
        provenance: str = "synthetic_dev_card_skills",
    ) -> "CardInstanceRecord":
        """Persist the card's three base skills and the awakening slot."""

        with self._lock:

            def body() -> "CardInstanceRecord":
                row = self._conn.execute(
                    "SELECT 1 FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                if row is None:
                    raise RoleStoreError(
                        f"account has no card instance {instance_id!r}"
                    )
                self._conn.execute(
                    "UPDATE card_instances SET sfm_id1 = ?, sfm_id2 = ?, "
                    "sfm_id3 = ?, sfm_id4 = ? "
                    "WHERE account_key = ? AND instance_id = ?",
                    (int(sfm_id1), int(sfm_id2), int(sfm_id3), int(sfm_id4),
                     account_key, str(instance_id)),
                )
                updated = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                assert updated is not None
                return _card_instance_from_row(updated)

            return self._transaction(body)

    def set_card_sfm4(
        self,
        *,
        account_key: str,
        instance_id: str,
        sfm_id4: int,
        provenance: str = "c2s196_replace_skill",
    ) -> "CardInstanceRecord":
        """Replace the card's fourth skill (the 觉醒技 slot) with a washed one."""

        with self._lock:

            def body() -> "CardInstanceRecord":
                row = self._conn.execute(
                    "SELECT 1 FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                if row is None:
                    raise RoleStoreError(
                        f"account has no card instance {instance_id!r}"
                    )
                self._conn.execute(
                    "UPDATE card_instances SET sfm_id4 = ? "
                    "WHERE account_key = ? AND instance_id = ?",
                    (int(sfm_id4), account_key, str(instance_id)),
                )
                updated = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (account_key, str(instance_id)),
                ).fetchone()
                assert updated is not None
                return _card_instance_from_row(updated)

            return self._transaction(body)

    def delete_card_instances(
        self, *, record: RoleRecord, instance_ids: tuple[str, ...]
    ) -> int:
        """Delete card instances owned by ``record``; return how many vanished.

        The role is re-read inside the transaction, exactly like the write
        methods.  An id owned by another account (or absent) is not an error by
        itself -- the caller decides -- but the returned count lets it refuse a
        partial delete *before* it answers a success to the client.
        """

        fields = tuple(dict.fromkeys(str(value) for value in instance_ids))
        if not fields:
            return 0
        with self._lock:

            def body() -> int:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to delete card instances"
                    )
                deleted = 0
                for instance_id in fields:
                    cursor = self._conn.execute(
                        "DELETE FROM card_instances WHERE account_key = ? "
                        "AND instance_id = ?",
                        (record.account_key, instance_id),
                    )
                    deleted += int(cursor.rowcount)
                return deleted

            return self._transaction(body)

    def apply_card_split(
        self,
        *,
        record: RoleRecord,
        instance_ids: tuple[str, ...],
        yinpo: int,
        jinpo: int,
        provenance: str = WALLET_ORIGIN_CARD_SPLIT,
    ) -> dict[str, float]:
        """Consume the selected cards and credit 银魄/金魄 in one transaction.

        Every id must still exist for this role; a missing one rolls the whole
        split back, so the client can never be told "success" after losing only
        part of its selection.  The amounts are the shipped ``cardSplit.max``
        rows the caller already summed.
        """

        fields = tuple(dict.fromkeys(str(value) for value in instance_ids))
        if not fields:
            raise RoleStoreError("card split needs at least one instance")
        amounts = {WALLET_YINPO: int(yinpo), WALLET_JINPO: int(jinpo)}
        for name, value in amounts.items():
            if value < 0:
                raise RoleStoreError(f"negative split amount {name}={value}")
        now = self._now()
        with self._lock:

            def body() -> dict[str, float]:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to split cards"
                    )
                existing = {
                    str(row["instance_id"])
                    for row in self._conn.execute(
                        "SELECT instance_id FROM card_instances WHERE "
                        "account_key = ? AND instance_id IN ("
                        + ", ".join("?" for _ in fields)
                        + ")",
                        (record.account_key, *fields),
                    ).fetchall()
                }
                missing = [value for value in fields if value not in existing]
                if missing:
                    raise RoleStoreError(
                        "card split selection is no longer owned: "
                        + ", ".join(sorted(missing))
                    )
                for instance_id in fields:
                    self._conn.execute(
                        "DELETE FROM card_instances WHERE account_key = ? "
                        "AND instance_id = ?",
                        (record.account_key, instance_id),
                    )
                updated: dict[str, float] = {}
                for name, value in amounts.items():
                    row = self._conn.execute(
                        "SELECT amount FROM role_wallet WHERE account_key = ? "
                        "AND value_name = ?",
                        (record.account_key, name),
                    ).fetchone()
                    current = float(row["amount"]) if row is not None else 0.0
                    amount = current + value
                    self._conn.execute(
                        "INSERT INTO role_wallet"
                        " (account_key, value_name, amount, provenance, updated_at)"
                        " VALUES (?, ?, ?, ?, ?)"
                        " ON CONFLICT(account_key, value_name) DO UPDATE SET"
                        " amount = excluded.amount,"
                        " provenance = excluded.provenance,"
                        " updated_at = excluded.updated_at",
                        (record.account_key, name, amount, str(provenance), now),
                    )
                    updated[name] = amount
                return updated

            return self._transaction(body)

    def apply_card_strength(
        self,
        *,
        record: RoleRecord,
        target_instance_id: str,
        new_level: int,
        new_exp: int,
        material_ids: tuple[str, ...],
        dog_food_spent: dict[int, int],
        provenance: str,
    ) -> "CardInstanceRecord":
        """Update the target, delete materials and spend dog food atomically.

        The target row is re-read and must still exist; every material id must
        still belong to the role; every dog-food count must remain non-negative.
        One transaction means a failure at any step leaves both the card rows and
        the special-card inventory exactly as they were.
        """

        target = str(target_instance_id)
        materials = tuple(dict.fromkeys(str(value) for value in material_ids))
        food = {int(key): int(value) for key, value in dog_food_spent.items()}
        if target in materials:
            raise RoleStoreError("a strength target cannot feed itself")
        if int(new_level) < 0 or int(new_exp) < 0:
            raise RoleStoreError("strength result has a negative level/exp")
        for cfg_id, amount in food.items():
            if cfg_id <= 0 or amount <= 0:
                raise RoleStoreError(
                    f"unusable dog-food spend {cfg_id!r}={amount!r}"
                )
        now = self._now()
        with self._lock:

            def body() -> CardInstanceRecord:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to strengthen a card"
                    )
                target_row = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (record.account_key, target),
                ).fetchone()
                if target_row is None:
                    raise RoleStoreError(
                        f"strength target {target!r} is not owned by this role"
                    )
                if materials:
                    existing = {
                        str(row["instance_id"])
                        for row in self._conn.execute(
                            "SELECT instance_id FROM card_instances WHERE "
                            "account_key = ? AND instance_id IN ("
                            + ", ".join("?" for _ in materials)
                            + ")",
                            (record.account_key, *materials),
                        ).fetchall()
                    }
                    missing = [
                        value for value in materials if value not in existing
                    ]
                    if missing:
                        raise RoleStoreError(
                            "strength materials are no longer owned: "
                            + ", ".join(sorted(missing))
                        )
                for cfg_id, amount in food.items():
                    row = self._conn.execute(
                        "SELECT count FROM card_special_inventory WHERE "
                        "account_key = ? AND cfg_id = ?",
                        (record.account_key, cfg_id),
                    ).fetchone()
                    current = int(row["count"]) if row is not None else 0
                    if current < amount:
                        raise RoleStoreError(
                            f"dog food {cfg_id} has {current}, need {amount}"
                        )
                self._conn.execute(
                    "UPDATE card_instances SET level = ?, exp = ?, recorded_at = ?"
                    " WHERE account_key = ? AND instance_id = ?",
                    (int(new_level), int(new_exp), now, record.account_key, target),
                )
                for instance_id in materials:
                    self._conn.execute(
                        "DELETE FROM card_instances WHERE account_key = ? "
                        "AND instance_id = ?",
                        (record.account_key, instance_id),
                    )
                for cfg_id, amount in food.items():
                    row = self._conn.execute(
                        "SELECT count FROM card_special_inventory WHERE "
                        "account_key = ? AND cfg_id = ?",
                        (record.account_key, cfg_id),
                    ).fetchone()
                    current = int(row["count"]) if row is not None else 0
                    self._conn.execute(
                        "UPDATE card_special_inventory SET count = ?,"
                        " provenance = ?, updated_at = ?"
                        " WHERE account_key = ? AND cfg_id = ?",
                        (
                            current - amount,
                            str(provenance),
                            now,
                            record.account_key,
                            cfg_id,
                        ),
                    )
                row = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (record.account_key, target),
                ).fetchone()
                if row is None:  # pragma: no cover - the update just succeeded
                    raise RoleStoreError(
                        "strength target vanished inside its own transaction"
                    )
                return _card_instance_from_row(row)

            return self._transaction(body)

    def card_advance_request(
        self, *, account_key: str, role_id: int, sync_counter: int
    ) -> "CardAdvanceRequestRecord | None":
        """The committed advance remembered under one client sync counter.

        Only the latest committed request is kept (one bounded JSON value per
        account).  A resend of anything older than it is not replayed; the
        handler refuses it as a stale counter, which still charges nothing.
        """

        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?",
                (_card_advance_meta_key(account_key),),
            ).fetchone()
        state = _card_advance_state(row)
        if state is None:
            return None
        if int(state.get("role_id", -1)) != int(role_id):
            return None
        if int(state.get("sync_counter", -1)) != int(sync_counter):
            return None
        return _card_advance_request_from_state(
            state, account_key=account_key
        )

    def next_advance_sync_counter(self, *, account_key: str, role_id: int) -> int:
        """The counter the client's next ``advanceCardEnd`` must carry.

        The client never increments ``pkgSyncCounterMgr`` locally: it stores the
        value the server sends.  Every committed advance therefore consumes one
        counter, and the next expected value is ``last + 1`` (0 before the first
        advance).
        """

        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?",
                (_card_advance_meta_key(account_key),),
            ).fetchone()
        state = _card_advance_state(row)
        if state is None or int(state.get("role_id", -1)) != int(role_id):
            return 0
        return int(state.get("sync_counter", -1)) + 1

    def apply_card_advance(
        self,
        *,
        record: RoleRecord,
        target_instance_id: str,
        expected_advance_level: int,
        new_advance_level: int,
        fodder_ids: tuple[str, ...],
        money: int,
        yinpo: int,
        jinpo: int,
        sync_counter: int,
        provenance: str,
        special_fodder: tuple[tuple[int, int], ...] = (),
        new_card_cfg_id: int | None = None,
        adv_pieces: tuple[tuple[int, int], ...] = (),
    ) -> tuple["CardInstanceRecord", dict[str, float], "CardAdvanceRequestRecord"]:
        """Charge one advance and write ``advance_level`` in one transaction.

        Everything the client's own ``checkMaterial`` verified is re-read here:
        the target row must still exist, still carry ``expected_advance_level``
        and still sit at the max strength level; every fodder id must still
        belong to the role and must not be the target; every charged wallet
        amount must still be affordable.  Any failure rolls the whole thing
        back, so the wallet can never go negative and the card can never gain a
        level without paying for it.  The accepted request is written in the
        same transaction under the client's ``ADVANCE_CARD`` sync counter, which
        is what makes a resent ``C2S 21`` idempotent.
        """

        target = str(target_instance_id)
        fodder = tuple(dict.fromkeys(str(value) for value in fodder_ids))
        special = tuple(
            (int(cfg_id), int(units)) for cfg_id, units in special_fodder
        )
        for cfg_id, units in special:
            if cfg_id <= 0 or units <= 0:
                raise RoleStoreError(
                    f"unusable special fodder {cfg_id!r}x{units!r}"
                )
        expected = int(expected_advance_level)
        new_level = int(new_advance_level)
        counter = int(sync_counter)
        costs = {"money": int(money), "yinpo": int(yinpo), "jinpo": int(jinpo)}
        if target in fodder:
            raise RoleStoreError("an advance target cannot be its own fodder")
        if expected < 0 or new_level <= expected:
            raise RoleStoreError(
                f"unusable advance level {expected!r} -> {new_level!r}"
            )
        if counter < 0:
            raise RoleStoreError(f"unusable advance sync counter {counter!r}")
        for name, amount in costs.items():
            if amount < 0:
                raise RoleStoreError(f"unusable advance cost {name}={amount!r}")
        if not str(provenance).strip():
            raise RoleStoreError("advance needs a provenance")
        now = self._now()
        with self._lock:

            def body():
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to advance a card"
                    )
                prior_row = self._conn.execute(
                    "SELECT value FROM meta WHERE key = ?",
                    (_card_advance_meta_key(record.account_key),),
                ).fetchone()
                prior_state = _card_advance_state(prior_row)
                if (
                    prior_state is not None
                    and int(prior_state.get("role_id", -1)) == int(record.role_id)
                    and counter <= int(prior_state.get("sync_counter", -1))
                ):
                    raise RoleStoreError(
                        f"advance sync counter {counter} is already committed "
                        f"for this role"
                    )
                target_row = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (record.account_key, target),
                ).fetchone()
                if target_row is None:
                    raise RoleStoreError(
                        f"advance target {target!r} is not owned by this role"
                    )
                if int(target_row["advance_level"]) != expected:
                    raise RoleStoreError(
                        f"advance target moved: expected advance level "
                        f"{expected}, stored {int(target_row['advance_level'])}"
                    )
                if fodder:
                    existing = {
                        str(row["instance_id"])
                        for row in self._conn.execute(
                            "SELECT instance_id FROM card_instances WHERE "
                            "account_key = ? AND instance_id IN ("
                            + ", ".join("?" for _ in fodder)
                            + ")",
                            (record.account_key, *fodder),
                        ).fetchall()
                    }
                    missing = [value for value in fodder if value not in existing]
                    if missing:
                        raise RoleStoreError(
                            "advance fodder is no longer owned: "
                            + ", ".join(sorted(missing))
                        )
                wallet_after: dict[str, float] = {}
                for name, amount in costs.items():
                    row = self._conn.execute(
                        "SELECT amount FROM role_wallet WHERE account_key = ? "
                        "AND value_name = ?",
                        (record.account_key, name),
                    ).fetchone()
                    current = float(row["amount"]) if row is not None else 0.0
                    if amount > current:
                        raise RoleStoreError(
                            f"wallet value {name} has {current}, need {amount}"
                        )
                    if amount:
                        updated = current - amount
                        self._conn.execute(
                            "INSERT INTO role_wallet"
                            " (account_key, value_name, amount, provenance,"
                            " updated_at) VALUES (?, ?, ?, ?, ?)"
                            " ON CONFLICT(account_key, value_name) DO UPDATE SET"
                            " amount = excluded.amount,"
                            " provenance = excluded.provenance,"
                            " updated_at = excluded.updated_at",
                            (
                                record.account_key,
                                name,
                                updated,
                                str(provenance),
                                now,
                            ),
                        )
                        wallet_after[name] = updated
                    else:
                        wallet_after[name] = current
                if new_card_cfg_id is not None:
                    # TASK-0439: the max advance level is the star-up: the
                    # client's 进化 turns a 4★+3 into 5★+0, i.e. the same card
                    # row moves to the next-star cfg (id + 1000) and resets
                    # advance_level.  Writing advance_level=4 on a 4★ row left
                    # the client with an invalid card and broke the bag/team.
                    if int(new_card_cfg_id) <= 0:
                        raise RoleStoreError(
                            f"unusable evolved cfg {new_card_cfg_id!r}"
                        )
                    self._conn.execute(
                        "UPDATE card_instances SET card_cfg_id = ?,"
                        " advance_level = 0, recorded_at = ?"
                        " WHERE account_key = ? AND instance_id = ?",
                        (int(new_card_cfg_id), now, record.account_key, target),
                    )
                else:
                    self._conn.execute(
                        "UPDATE card_instances SET advance_level = ?,"
                        " recorded_at = ? WHERE account_key = ? AND instance_id = ?",
                        (new_level, now, record.account_key, target),
                    )
                for instance_id in fodder:
                    self._conn.execute(
                        "DELETE FROM card_instances WHERE account_key = ? "
                        "AND instance_id = ?",
                        (record.account_key, instance_id),
                    )
                if adv_pieces:
                    # TASK-0440: consume 进阶石 in the same transaction.
                    piece_key = f"adv_piece:{int(record.role_id)}"
                    piece_row = self._conn.execute(
                        "SELECT value FROM meta WHERE key = ?", (piece_key,)
                    ).fetchone()
                    piece_counts: dict[str, int] = {}
                    if piece_row is not None and piece_row["value"]:
                        try:
                            piece_counts = {
                                str(k): int(v)
                                for k, v in json.loads(piece_row["value"]).items()
                            }
                        except (TypeError, ValueError):
                            piece_counts = {}
                    for cfg_id, units in adv_pieces:
                        have = int(piece_counts.get(str(cfg_id), 0))
                        if have < int(units):
                            raise RoleStoreError(
                                f"advance piece {cfg_id} has {have}, need {units}"
                            )
                        piece_counts[str(cfg_id)] = have - int(units)
                    self._conn.execute(
                        "INSERT INTO meta (key, value) VALUES (?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (piece_key, json.dumps(piece_counts, sort_keys=True)),
                    )
                for cfg_id, units in special:
                    row = self._conn.execute(
                        "SELECT count FROM card_special_inventory WHERE "
                        "account_key = ? AND cfg_id = ?",
                        (record.account_key, cfg_id),
                    ).fetchone()
                    if row is None or int(row["count"]) < units:
                        raise RoleStoreError(
                            f"special fodder {cfg_id} has "
                            f"{0 if row is None else int(row['count'])}, "
                            f"need {units}"
                        )
                    left = int(row["count"]) - units
                    if left:
                        self._conn.execute(
                            "UPDATE card_special_inventory SET count = ?, "
                            "updated_at = ? WHERE account_key = ? AND cfg_id = ?",
                            (left, now, record.account_key, cfg_id),
                        )
                    else:
                        self._conn.execute(
                            "DELETE FROM card_special_inventory WHERE "
                            "account_key = ? AND cfg_id = ?",
                            (record.account_key, cfg_id),
                        )
                state = {
                    "role_id": int(record.role_id),
                    "server_id": int(record.server_id),
                    "sync_counter": counter,
                    "instance_id": target,
                    "advance_level_from": expected,
                    "advance_level_to": new_level,
                    "money_spent": float(costs["money"]),
                    "yinpo_spent": costs["yinpo"],
                    "jinpo_spent": costs["jinpo"],
                    "fodder_ids": list(fodder),
                    "recorded_at": now,
                    "origin": str(provenance),
                }
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (
                        _card_advance_meta_key(record.account_key),
                        json.dumps(state, ensure_ascii=False, sort_keys=True),
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM card_instances WHERE account_key = ? "
                    "AND instance_id = ?",
                    (record.account_key, target),
                ).fetchone()
                if row is None:  # pragma: no cover - the update just succeeded
                    raise RoleStoreError(
                        "advance target vanished inside its own transaction"
                    )
                return (
                    _card_instance_from_row(row),
                    wallet_after,
                    _card_advance_request_from_state(
                        state, account_key=record.account_key
                    ),
                )

            return self._transaction(body)

    def apply_card_battle_exp(
        self,
        *,
        record: RoleRecord,
        instance_ids: tuple[str, ...],
        card_exp: int,
        provenance: str,
    ) -> tuple["CardInstanceRecord", ...]:
        """Add one fight's flat ``guankaMonster.cardExp`` to the given cards.

        The client's own ``calculateLevel`` math runs **inside** the
        transaction: the current row is re-read, its starred ``cardLv`` table is
        consumed and the resulting level/leftover EXP are written.  Callers
        pass the battle's team instance ids captured when C2S 69 started; an id
        that is no longer owned is a refusal, never a silent skip.  The
        provenance is stored on the event, not on the card row (the row keeps
        the origin it was acquired with).
        """

        ids = tuple(dict.fromkeys(str(value) for value in instance_ids if value))
        amount = int(card_exp)
        if amount < 0:
            raise RoleStoreError(f"battle card exp {card_exp!r} is negative")
        if not str(provenance).strip():
            raise RoleStoreError("battle card exp needs a provenance")
        if not ids:
            return ()
        now = self._now()
        with self._lock:

            def body() -> tuple["CardInstanceRecord", ...]:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to grant battle card exp"
                    )
                updated: list[CardInstanceRecord] = []
                for instance_id in ids:
                    row = self._conn.execute(
                        "SELECT * FROM card_instances WHERE account_key = ? "
                        "AND instance_id = ?",
                        (record.account_key, instance_id),
                    ).fetchone()
                    if row is None:
                        raise RoleStoreError(
                            f"battle team card {instance_id!r} is not owned by "
                            "this role"
                        )
                    try:
                        star, _rare = card_growth.card_star_rare(
                            row["card_cfg_id"]
                        )
                    except KeyError as exc:
                        raise RoleStoreError(
                            f"battle team card {instance_id!r} has no cardLv row"
                        ) from exc
                    total = (
                        card_growth.sum_exp(star, row["level"], row["exp"]) + amount
                    )
                    new_level, new_exp = card_growth.calculate_level(star, total)
                    self._conn.execute(
                        "UPDATE card_instances SET level = ?, exp = ?, recorded_at = ?"
                        " WHERE account_key = ? AND instance_id = ?",
                        (new_level, new_exp, now, record.account_key, instance_id),
                    )
                    after = self._conn.execute(
                        "SELECT * FROM card_instances WHERE account_key = ? "
                        "AND instance_id = ?",
                        (record.account_key, instance_id),
                    ).fetchone()
                    if after is None:  # pragma: no cover - the update just ran
                        raise RoleStoreError(
                            f"battle team card {instance_id!r} vanished inside "
                            "its own transaction"
                        )
                    updated.append(_card_instance_from_row(after))
                return tuple(updated)

            return self._transaction(body)

    # ------------------------------------------------- card growth economy

    def card_special_counts(self, *, account_key: str) -> dict[int, int]:
        """Dog-food special-card counts of one account (``pkgSpecialCardDataMgr``)."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT cfg_id, count FROM card_special_inventory "
                "WHERE account_key = ? ORDER BY cfg_id",
                (account_key,),
            ).fetchall()
        return {int(row["cfg_id"]): int(row["count"]) for row in rows}

    def adjust_card_special(
        self,
        *,
        record: RoleRecord,
        cfg_id: int,
        delta: int,
        provenance: str,
    ) -> int:
        """Add ``delta`` to one special-card count and return the new value.

        The decrement path is the important one: a negative result raises and
        the transaction rolls back, so two concurrent strength calls can never
        spend the same food twice.
        """

        numeric_cfg = int(cfg_id)
        numeric_delta = int(delta)
        if numeric_cfg <= 0:
            raise RoleStoreError(f"unusable special card cfg id {cfg_id!r}")
        if not str(provenance):
            raise RoleStoreError("special card provenance must be non-empty")
        now = self._now()
        with self._lock:

            def body() -> int:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to adjust special cards"
                    )
                row = self._conn.execute(
                    "SELECT count FROM card_special_inventory WHERE "
                    "account_key = ? AND cfg_id = ?",
                    (record.account_key, numeric_cfg),
                ).fetchone()
                current = int(row["count"]) if row is not None else 0
                updated = current + numeric_delta
                if updated < 0:
                    raise RoleStoreError(
                        f"special card {numeric_cfg} would go negative "
                        f"({current} {numeric_delta:+d})"
                    )
                self._conn.execute(
                    "INSERT INTO card_special_inventory"
                    " (account_key, cfg_id, count, provenance, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(account_key, cfg_id) DO UPDATE SET"
                    " count = excluded.count,"
                    " provenance = excluded.provenance,"
                    " updated_at = excluded.updated_at",
                    (record.account_key, numeric_cfg, updated, str(provenance), now),
                )
                return updated

            return self._transaction(body)

    def role_wallet_values(self, *, account_key: str) -> dict[str, float]:
        """Persisted wallet amounts of one account, keyed by value name."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT value_name, amount FROM role_wallet "
                "WHERE account_key = ? ORDER BY value_name",
                (account_key,),
            ).fetchall()
        return {str(row["value_name"]): float(row["amount"]) for row in rows}

    def _role_wallet_amount_locked(self, account_key: str, value_name: str) -> float:
        """Read one wallet amount on the caller's open transaction.

        The caller already holds ``self._lock`` (or is inside a
        ``_transaction`` body); this helper never opens its own transaction.
        """

        row = self._conn.execute(
            "SELECT amount FROM role_wallet WHERE account_key = ? "
            "AND value_name = ?",
            (account_key, str(value_name)),
        ).fetchone()
        return float(row["amount"]) if row is not None else 0.0

    def _write_role_wallet_locked(
        self,
        *,
        account_key: str,
        value_name: str,
        delta: float,
        provenance: str,
        now: int,
    ) -> float:
        """Add ``delta`` to one wallet value on the caller's open transaction.

        Shared by :meth:`adjust_role_wallet` and
        :meth:`record_chapter_fight_clear_with_money` so the card-advance sink
        and the fight money producer cannot drift apart in validation or SQL.
        A refusal raises out of the caller's transaction.
        """

        name = str(value_name)
        if name not in WALLET_VALUE_NAMES:
            raise RoleStoreError(f"unknown wallet value name {value_name!r}")
        numeric_delta = float(delta)
        if not str(provenance):
            raise RoleStoreError("wallet provenance must be non-empty")
        current = self._role_wallet_amount_locked(account_key, name)
        updated = current + numeric_delta
        if updated < 0:
            raise RoleStoreError(
                f"wallet value {name} would go negative "
                f"({current} {numeric_delta:+f})"
            )
        self._conn.execute(
            "INSERT INTO role_wallet"
            " (account_key, value_name, amount, provenance, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(account_key, value_name) DO UPDATE SET"
            " amount = excluded.amount,"
            " provenance = excluded.provenance,"
            " updated_at = excluded.updated_at",
            (account_key, name, updated, str(provenance), now),
        )
        return updated

    def _card_special_count_locked(self, account_key: str, cfg_id: int) -> int:
        """Read one dog-food count on the caller's open transaction."""

        row = self._conn.execute(
            "SELECT count FROM card_special_inventory WHERE "
            "account_key = ? AND cfg_id = ?",
            (account_key, int(cfg_id)),
        ).fetchone()
        return int(row["count"]) if row is not None else 0

    def _add_card_special_locked(
        self,
        *,
        account_key: str,
        cfg_id: int,
        count: int,
        provenance: str,
        now: int,
    ) -> int:
        """Add ``count`` dog food to one ``specialCard`` cfg id.

        Shared by :meth:`accept_chapter_box` and
        :meth:`record_chapter_fight_clear_with_money` so the chest and the
        fight-reward producers cannot drift apart in their addition or their
        ``provenance``/``updated_at`` conventions.  The caller already holds
        ``self._lock`` (or is inside a ``_transaction`` body); a refusal raises
        out of the caller's transaction.
        """

        numeric_cfg = int(cfg_id)
        numeric_count = int(count)
        if numeric_cfg <= 0:
            raise RoleStoreError(f"unusable dog-food cfg id {cfg_id!r}")
        if numeric_count <= 0:
            raise RoleStoreError(
                f"dog-food count for cfg {numeric_cfg} must be positive, "
                f"got {count!r}"
            )
        if not str(provenance):
            raise RoleStoreError("special card provenance must be non-empty")
        updated = (
            self._card_special_count_locked(account_key, numeric_cfg)
            + numeric_count
        )
        self._conn.execute(
            "INSERT INTO card_special_inventory"
            " (account_key, cfg_id, count, provenance, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(account_key, cfg_id) DO UPDATE SET"
            " count = excluded.count,"
            " provenance = excluded.provenance,"
            " updated_at = excluded.updated_at",
            (account_key, numeric_cfg, updated, str(provenance), now),
        )
        return updated

    def adjust_role_wallet(
        self,
        *,
        record: RoleRecord,
        value_name: str,
        delta: float,
        provenance: str,
    ) -> float:
        """Add ``delta`` to one wallet value and return the new amount."""

        now = self._now()
        with self._lock:

            def body() -> float:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to adjust the wallet"
                    )
                return self._write_role_wallet_locked(
                    account_key=record.account_key,
                    value_name=value_name,
                    delta=delta,
                    provenance=provenance,
                    now=now,
                )

            return self._transaction(body)

    def provision_role_wallet_once(
        self,
        *,
        record: RoleRecord,
        values: Mapping[str, float],
        provenance: str,
    ) -> dict[str, float]:
        """Insert only the wallet rows this account does *not* have yet.

        One ``BEGIN IMMEDIATE`` transaction checks the role row, checks each
        name and amount, then inserts only missing ``role_wallet`` rows.  An
        existing row is never changed, never topped up and never raised, so a
        dev-play provisioning pass can run on every login while a real card
        advance that already spent part of the amount keeps its new balance
        across a re-login.  Returns the rows this call actually created; an
        empty dict is the normal "everything was already there" case.

        The helper deliberately does not go through
        :meth:`adjust_role_wallet`: that method adds a delta to a row and would
        restore spent currency.  A refusal raises out of the transaction and
        leaves every wallet row untouched.
        """

        if not str(provenance):
            raise RoleStoreError("wallet provenance must be non-empty")
        cleaned: dict[str, float] = {}
        for raw_name, raw_amount in values.items():
            name = str(raw_name)
            if name not in WALLET_VALUE_NAMES:
                raise RoleStoreError(f"unknown wallet value name {raw_name!r}")
            if name in cleaned:
                raise RoleStoreError(f"wallet value {name} is named twice")
            amount = float(raw_amount)
            if amount < 0:
                raise RoleStoreError(f"wallet value {name} is negative: {amount}")
            cleaned[name] = amount
        now = self._now()
        with self._lock:

            def body() -> dict[str, float]:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to provision its wallet"
                    )
                created: dict[str, float] = {}
                for name, amount in cleaned.items():
                    exists = self._conn.execute(
                        "SELECT 1 FROM role_wallet WHERE account_key = ? "
                        "AND value_name = ?",
                        (record.account_key, name),
                    ).fetchone()
                    if exists is not None:
                        continue
                    self._conn.execute(
                        "INSERT INTO role_wallet"
                        " (account_key, value_name, amount, provenance, updated_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (record.account_key, name, amount, str(provenance), now),
                    )
                    created[name] = amount
                return created

            return self._transaction(body)

    # ------------------------------------------------------ goods-use counts
    #
    # TASK-0266.  The two role values a C2S 429 goodsUse moves -- the bag item
    # count (goodsUse6, S2C 27 wire index 157 / reward type 376) and the effect
    # it grants (lotteryTicket, wire index 18 / reward type 252) -- live in the
    # archive's existing meta key-value table, NOT in role_wallet.  That is a
    # deliberate consequence of two frozen facts:
    #
    #   * role_wallet's structure-9 CHECK admits exactly
    #     ('money', 'yinpo', 'jinpo'); storing any other name there would need
    #     the table rebuilt, and
    #   * SCHEMA_VERSION = 12 is frozen by existing archives and tests, so this
    #     slice adds no v13 migration.
    #
    # A generic RoleValue table (or a widened role_wallet CHECK) is a later,
    # separate migration.  The meta pattern is the one this module already uses
    # for CARD_ADVANCE_META_PREFIX: every older archive opens unchanged and no
    # structure version is consumed.  A key only exists after the first use, so
    # a fresh archive reads no value and its S2C 27 vector stays byte-identical.

    @staticmethod
    def goods_use_meta_key(account_key: str, value_name: str) -> str:
        """The role-scoped meta key of one goods-use count (TASK-0266)."""

        if not isinstance(account_key, str) or not account_key:
            raise RoleStoreError("goods-use count needs a non-empty account key")
        if value_name not in GOODS_USE_VALUE_NAMES:
            raise RoleStoreError(
                f"unknown goods-use value name {value_name!r}; this slice knows "
                f"{GOODS_USE_VALUE_NAMES}"
            )
        return f"{GOODS_USE_META_PREFIX}{account_key}:{value_name}"

    def goods_use_counts(self, *, account_key: str) -> dict[str, int]:
        """The persisted goods-use counts of one account, keyed by value name.

        Only a name this archive actually wrote is returned, exactly like
        role_wallet_values; an absent name means "never used / zero".
        """

        with self._lock:
            counts: dict[str, int] = {}
            for name in GOODS_USE_VALUE_NAMES:
                value = self._goods_use_count_locked(account_key, name)
                if value is not None:
                    counts[name] = value
        return counts

    def _goods_use_count_locked(
        self, account_key: str, value_name: str
    ) -> int | None:
        """Read one goods-use count on the caller's open transaction.

        The caller already holds self._lock (or is inside a _transaction
        body); this helper never opens its own transaction.  A missing key is
        None (never written); an unreadable value raises rather than being
        silently treated as zero.
        """

        key = self.goods_use_meta_key(account_key, value_name)
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        raw = str(row["value"])
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise RoleStoreError(
                f"goods-use count {value_name!r} is not an integer: {raw!r}"
            ) from exc
        if value < 0:
            raise RoleStoreError(
                f"goods-use count {value_name!r} is negative: {value}"
            )
        return value

    def _write_goods_use_count_locked(
        self,
        *,
        account_key: str,
        value_name: str,
        value: int,
    ) -> int:
        """Write one non-negative goods-use count on the caller's transaction.

        The meta table has no provenance/updated_at column, so this writer
        stores the count only; the caller's provenance is returned to it in
        the GoodsUseRecord and named in the transport event at write time.  A
        refusal raises out of the caller's transaction and writes nothing.
        """

        if isinstance(value, bool) or not isinstance(value, int):
            raise RoleStoreError(
                f"goods-use count {value_name!r} must be an int, got {value!r}"
            )
        if value < 0:
            raise RoleStoreError(
                f"goods-use count {value_name!r} is negative: {value}"
            )
        key = self.goods_use_meta_key(account_key, value_name)
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        return value

    def use_goods(
        self,
        *,
        record: RoleRecord,
        item_value_name: str,
        reward_value_name: str,
        count: int = 1,
        reward_amount: int = 1,
        provenance: str = GOODS_USE_ORIGIN_LOCAL_C2S,
    ) -> GoodsUseRecord:
        """Apply one goodsUse in ONE transaction and return both before/after pairs.

        The item count must already cover count, the two names must be the two
        distinct role values this slice persists, and every refusal raises
        RoleStoreError out of the transaction: no row, no meta key and no
        partial write survives.  The write is exact -- item - count and
        reward + reward_amount -- and the S2C 24 values the handler sends are
        the absolute after values this method returns.
        """

        if item_value_name not in GOODS_USE_VALUE_NAMES:
            raise RoleStoreError(
                f"unknown goods-use item value name {item_value_name!r}"
            )
        if reward_value_name not in GOODS_USE_VALUE_NAMES:
            raise RoleStoreError(
                f"unknown goods-use reward value name {reward_value_name!r}"
            )
        if item_value_name == reward_value_name:
            raise RoleStoreError(
                "goods use cannot spend and grant the same value "
                f"{item_value_name!r}"
            )
        for label, value in (("count", count), ("reward amount", reward_amount)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RoleStoreError(
                    f"goods-use {label} must be an int, got {value!r}"
                )
            if value <= 0:
                raise RoleStoreError(
                    f"goods-use {label} must be positive, got {value!r}"
                )
        if not isinstance(provenance, str) or not provenance:
            raise RoleStoreError("goods-use provenance must be non-empty")

        with self._lock:

            def body() -> GoodsUseRecord:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing the goods use"
                    )
                item_before = (
                    self._goods_use_count_locked(
                        record.account_key, item_value_name
                    )
                    or 0
                )
                if item_before < count:
                    raise RoleStoreError(
                        f"goods-use item {item_value_name!r} has {item_before}, "
                        f"the use needs {count}"
                    )
                reward_before = (
                    self._goods_use_count_locked(
                        record.account_key, reward_value_name
                    )
                    or 0
                )
                item_after = self._write_goods_use_count_locked(
                    account_key=record.account_key,
                    value_name=item_value_name,
                    value=item_before - count,
                )
                reward_after = self._write_goods_use_count_locked(
                    account_key=record.account_key,
                    value_name=reward_value_name,
                    value=reward_before + reward_amount,
                )
                return GoodsUseRecord(
                    account_key=record.account_key,
                    role_id=int(record.role_id),
                    item_value_name=item_value_name,
                    item_before=item_before,
                    item_after=item_after,
                    used_count=count,
                    reward_value_name=reward_value_name,
                    reward_before=reward_before,
                    reward_after=reward_after,
                    reward_amount=reward_amount,
                    provenance=provenance,
                )

            return self._transaction(body)

    # --------------------------------------------------- dev-play ticket ledger
    #
    # TASK-0235.  The two 祈灵 ticket balances of the separate dev-play archive
    # live in the existing ``meta`` table (structure 1) under one caller-owned
    # key, so no table, migration or ``SCHEMA_VERSION`` change is needed.  The
    # payload's shape is *not* known here: the caller passes its own strict
    # ``decode``/``encode`` codecs (``revival_stub.dev_play_resources``), and the
    # transaction only enforces the invariants every caller needs -- the key set
    # is exactly the seed's, every count is a non-negative int, the balance
    # covers the cost, and the ledger write plus every card row either commit
    # together or not at all.

    @staticmethod
    def _validate_ticket_counts(
        values: object, *, expected: Mapping[str, int]
    ) -> dict[str, int]:
        if not isinstance(values, Mapping):
            raise RoleStoreError(
                "ticket balance codec must return a mapping, got "
                f"{type(values).__name__}"
            )
        keys = set(values)
        if keys != set(expected):
            raise RoleStoreError(
                "ticket balance fields changed: expected "
                f"{sorted(expected)}, got {sorted(keys)}"
            )
        counts: dict[str, int] = {}
        for name in expected:
            value = values[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise RoleStoreError(
                    f"ticket balance {name} must be an int, got {value!r}"
                )
            if value < 0:
                raise RoleStoreError(
                    f"ticket balance {name} is negative: {value}"
                )
            counts[name] = value
        return counts

    def _read_or_seed_ticket_balance_locked(
        self,
        *,
        meta_key: str,
        seed_counts: Mapping[str, int],
        decode: Callable[[str], Mapping[str, int]],
        encode: Callable[[Mapping[str, int]], str],
    ) -> tuple[dict[str, int], bool]:
        """Read the ledger on the caller's transaction, seeding it if absent."""

        checked_key = self._validate_meta_key(meta_key)
        seed = {str(name): int(amount) for name, amount in seed_counts.items()}
        if not seed:  # pragma: no cover - every caller passes the two tickets
            raise RoleStoreError("a ticket balance seed must name its fields")
        for name, amount in seed.items():
            if amount < 0:
                raise RoleStoreError(f"ticket balance seed {name} is negative")
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (checked_key,)
        ).fetchone()
        if row is None:
            counts = self._validate_ticket_counts(seed, expected=seed)
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                (checked_key, encode(counts)),
            )
            return counts, True
        counts = self._validate_ticket_counts(
            decode(str(row["value"])), expected=seed
        )
        return counts, False

    def read_ticket_balance(
        self,
        *,
        meta_key: str,
        seed_counts: Mapping[str, int],
        decode: Callable[[str], Mapping[str, int]],
        encode: Callable[[Mapping[str, int]], str],
    ) -> tuple[dict[str, int], bool]:
        """The ledger's two counts, created once at the seed when missing.

        One ``BEGIN IMMEDIATE`` transaction reads the marker and, when this
        archive has none, writes the seed before returning it.  That is what
        makes a dev-play login's first ``S2C 27`` and the first draw agree on
        the same initial 30/30 without a second write path.  Returns
        ``(counts, created)``.
        """

        with self._lock:
            return self._transaction(
                lambda: self._read_or_seed_ticket_balance_locked(
                    meta_key=meta_key,
                    seed_counts=seed_counts,
                    decode=decode,
                    encode=encode,
                )
            )

    def record_ticket_draw(
        self,
        *,
        record: RoleRecord,
        meta_key: str,
        balance_field: str,
        seed_counts: Mapping[str, int],
        cost: int,
        cards: tuple[CardInstanceWrite, ...],
        decode: Callable[[str], Mapping[str, int]],
        encode: Callable[[Mapping[str, int]], str],
        provenance: str,
    ) -> TicketDrawOutcome:
        """Charge one draw and write every drawn card row in one transaction.

        The transaction first re-reads the bound role, then the ledger (seeding
        a missing marker at the seed balance), then refuses with
        :class:`TicketBalanceShortfallError` when the balance cannot cover
        ``cost``.  Only after the balance is written does it insert the card
        rows.  Any refusal or error rolls the whole transaction back, so a
        balance can never be charged without its cards and cards can never be
        granted without paying.
        """

        field = str(balance_field)
        numeric_cost = int(cost)
        if numeric_cost <= 0:
            raise RoleStoreError(f"unusable ticket draw cost {cost!r}")
        if not str(provenance):  # pragma: no cover - every caller names it
            raise RoleStoreError("a ticket draw needs a provenance")
        writes = tuple(cards)
        if not writes:
            raise RoleStoreError("a ticket draw must write at least one card")
        written_ids = [write.instance_id for write in writes]
        if len(set(written_ids)) != len(written_ids):
            raise RoleStoreError("a ticket draw names one card instance twice")
        for write in writes:
            text = str(write.instance_id).strip()
            if not text or len(text) > 255:
                raise RoleStoreError(
                    f"unusable card instance id {write.instance_id!r}"
                )
            if int(write.card_cfg_id) <= 0:
                raise RoleStoreError(
                    f"unusable card config id {write.card_cfg_id!r}"
                )
            if int(write.level) < 0:
                raise RoleStoreError(f"unusable card level {write.level!r}")
            if write.fixture_kind not in (
                None,
                CARD_INSTANCE_FIXTURE_KIND_DETERMINISTIC_DRAW,
            ):
                raise RoleStoreError(
                    f"unknown card fixture kind {write.fixture_kind!r}"
                )
        now = self._now()
        with self._lock:

            def body() -> TicketDrawOutcome:
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to charge a ticket draw"
                    )
                counts, seeded = self._read_or_seed_ticket_balance_locked(
                    meta_key=meta_key,
                    seed_counts=seed_counts,
                    decode=decode,
                    encode=encode,
                )
                if field not in counts:
                    raise RoleStoreError(
                        f"ticket balance field {field!r} is not in the ledger"
                    )
                before = int(counts[field])
                if before < numeric_cost:
                    raise TicketBalanceShortfallError(
                        balance_field=field, balance=before, cost=numeric_cost
                    )
                counts[field] = before - numeric_cost
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (self._validate_meta_key(meta_key), encode(counts)),
                )
                rows: list[CardInstanceRecord] = []
                for write in writes:
                    row, _outcome = self._write_card_instance_locked(
                        record=record,
                        now=now,
                        instance_id=str(write.instance_id).strip(),
                        card_cfg_id=int(write.card_cfg_id),
                        level=int(write.level),
                        exp=0,
                        advance_level=0,
                        sfm_id1=int(write.sfm_id1),
                        sfm_id2=int(write.sfm_id2),
                        sfm_id3=0,
                        sfm_id4=0,
                        equip_id1="",
                        equip_id2="",
                        equip_id3="",
                        equip_id4="",
                        locked=False,
                        refine_lv=0,
                        refine_lv_times=0,
                        learn_atk=0,
                        learn_hp=0,
                        learn_state=0,
                        refine_skill="",
                        god_weapon_id="",
                        origin=str(write.origin),
                        fixture_id=write.fixture_id,
                        fixture_kind=write.fixture_kind,
                    )
                    rows.append(row)
                return TicketDrawOutcome(
                    rows=tuple(rows),
                    balance_before=before,
                    balance_after=before - numeric_cost,
                    balance_seeded=seeded,
                )

            return self._transaction(body)

    # ------------------------------------------------------------------ draw pity

    def draw_pity(self, *, account_key: str, pool: str) -> "DrawPityRecord | None":
        """The persisted pity counters for one role and pool, if any."""

        row = self._conn.execute(
            "SELECT * FROM draw_pity WHERE account_key = ? AND pool = ?",
            (account_key, pool),
        ).fetchone()
        return _draw_pity_from_row(row) if row is not None else None

    def record_draw_pity(
        self,
        *,
        record: RoleRecord,
        pool: str,
        since_legend: int,
        since_rare: int,
        provenance: str,
    ) -> "DrawPityRecord":
        """Upsert the advanced-pool pity counters after one draw request."""

        if pool not in ("normal", "advanced", "limited"):
            raise RoleStoreError(f"unknown draw pool {pool!r}")
        legend_value = int(since_legend)
        rare_value = int(since_rare)
        if legend_value < 0 or rare_value < 0:
            raise RoleStoreError(
                f"unusable draw pity counters {legend_value}/{rare_value}"
            )
        now = self._now()
        with self._lock:

            def body() -> "DrawPityRecord":
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record draw pity for it"
                    )
                self._conn.execute(
                    "INSERT INTO draw_pity (account_key, pool, since_legend,"
                    " since_rare, updated_at, provenance)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(account_key, pool) DO UPDATE SET"
                    " since_legend = excluded.since_legend,"
                    " since_rare = excluded.since_rare,"
                    " updated_at = excluded.updated_at,"
                    " provenance = excluded.provenance",
                    (
                        record.account_key,
                        pool,
                        legend_value,
                        rare_value,
                        now,
                        str(provenance),
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM draw_pity WHERE account_key = ? AND pool = ?",
                    (record.account_key, pool),
                ).fetchone()
                if row is None:  # pragma: no cover - the write just succeeded
                    raise RoleStoreError(
                        "draw pity row vanished inside its own transaction"
                    )
                return _draw_pity_from_row(row)

            return self._transaction(body)

    def hotspot_draw_pity(
        self, *, account_key: str, hotspot_id: int
    ) -> "HotSpotPityRecord | None":
        """The per-banner featured-legend counter, if any."""

        row = self._conn.execute(
            "SELECT * FROM hotspot_draw_pity WHERE account_key = ?"
            " AND hotspot_id = ?",
            (account_key, int(hotspot_id)),
        ).fetchone()
        return _hotspot_pity_from_row(row) if row is not None else None

    def record_hotspot_draw_pity(
        self,
        *,
        record: RoleRecord,
        hotspot_id: int,
        since_featured_legend: int,
        provenance: str,
    ) -> "HotSpotPityRecord":
        """Upsert one banner's consecutive-non-featured-legend counter."""

        banner = int(hotspot_id)
        value = int(since_featured_legend)
        if banner <= 0:
            raise RoleStoreError(f"unusable hotspot id {hotspot_id!r}")
        if value < 0:
            raise RoleStoreError(
                f"unusable hotspot featured counter {since_featured_legend!r}"
            )
        now = self._now()
        with self._lock:

            def body() -> "HotSpotPityRecord":
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record hotspot pity"
                    )
                self._conn.execute(
                    "INSERT INTO hotspot_draw_pity (account_key, hotspot_id,"
                    " since_featured_legend, updated_at, provenance)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(account_key, hotspot_id) DO UPDATE SET"
                    " since_featured_legend = excluded.since_featured_legend,"
                    " updated_at = excluded.updated_at,"
                    " provenance = excluded.provenance",
                    (record.account_key, banner, value, now, str(provenance)),
                )
                row = self._conn.execute(
                    "SELECT * FROM hotspot_draw_pity WHERE account_key = ?"
                    " AND hotspot_id = ?",
                    (record.account_key, banner),
                ).fetchone()
                if row is None:  # pragma: no cover - the write just succeeded
                    raise RoleStoreError(
                        "hotspot pity row vanished inside its own transaction"
                    )
                return _hotspot_pity_from_row(row)

            return self._transaction(body)

    # ------------------------------------------------------------- team presets

    def team_preset(
        self, *, account_key: str, group_id: int
    ) -> "TeamPresetRecord | None":
        """One stored preset, if this account has one."""

        row = self._conn.execute(
            "SELECT * FROM team_presets WHERE account_key = ? AND group_id = ?",
            (account_key, int(group_id)),
        ).fetchone()
        return _team_preset_from_row(row) if row is not None else None

    def team_presets(self, *, account_key: str) -> tuple["TeamPresetRecord", ...]:
        """Every stored preset of one account, ordered by group id."""

        rows = self._conn.execute(
            "SELECT * FROM team_presets WHERE account_key = ? ORDER BY group_id",
            (account_key,),
        ).fetchall()
        return tuple(_team_preset_from_row(row) for row in rows)

    def team_state(self, *, account_key: str) -> "TeamStateRecord | None":
        """The preset the client last made current, if any."""

        row = self._conn.execute(
            "SELECT * FROM team_state WHERE account_key = ?",
            (account_key,),
        ).fetchone()
        return _team_state_from_row(row) if row is not None else None

    def record_team_preset(
        self,
        *,
        record: RoleRecord,
        group_id: int,
        equip_ids: tuple[str, ...],
        god_weapon_id: str,
        card_ids: tuple[str, ...],
        origin: str = TEAM_RECORD_ORIGIN_LOCAL_C2S_UPDATE,
    ) -> "TeamPresetRecord":
        """Store one complete preset from the client's own C2S 6.

        Every non-empty card id must already be a card instance of this very
        account; the method refuses an unowned id instead of writing a team the
        player could not have built.  The row is replaced wholesale, exactly as
        the client's `setTeamCardData` replaces `tbTeamCard[gid]`.
        """

        group = int(group_id)
        if not 1 <= group <= TEAM_MAX_GROUPS:
            raise RoleStoreError(
                f"unusable team group {group_id!r}; the client has "
                f"1..{TEAM_MAX_GROUPS}"
            )
        equips = tuple(str(value or "") for value in equip_ids)
        if len(equips) != TEAM_EQUIP_SLOTS:
            raise RoleStoreError(
                f"team preset needs {TEAM_EQUIP_SLOTS} equipment slots, "
                f"got {len(equips)}"
            )
        cards = tuple(str(value or "").strip() for value in card_ids)
        if len(cards) != TEAM_CARD_SLOTS:
            raise RoleStoreError(
                f"team preset needs {TEAM_CARD_SLOTS} card slots, got {len(cards)}"
            )
        god = str(god_weapon_id or "")
        now = self._now()
        with self._lock:

            def body() -> "TeamPresetRecord":
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record a team preset"
                    )
                owned = {
                    str(row["instance_id"])
                    for row in self._conn.execute(
                        "SELECT instance_id FROM card_instances WHERE account_key = ?",
                        (record.account_key,),
                    ).fetchall()
                }
                seen: set[str] = set()
                for card_id in cards:
                    if not card_id:
                        continue
                    if card_id in seen:
                        raise RoleStoreError(
                            f"team preset repeats card {card_id!r}"
                        )
                    if card_id not in owned:
                        raise RoleStoreError(
                            f"team preset names card {card_id!r}, which this "
                            "account does not own"
                        )
                    seen.add(card_id)
                columns = (
                    "equip_id1",
                    "equip_id2",
                    "equip_id3",
                    "equip_id4",
                    "god_weapon_id",
                    *(f"card_id{index}" for index in range(1, TEAM_CARD_SLOTS + 1)),
                )
                values = (*equips, god, *cards, str(origin), now)
                placeholders = ", ".join("?" for _ in range(len(values) + 2))
                assignments = ", ".join(
                    f"{column} = excluded.{column}" for column in columns
                )
                self._conn.execute(
                    f"INSERT INTO team_presets (account_key, group_id, "
                    f"{', '.join(columns)}, origin, updated_at) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT(account_key, group_id) DO UPDATE SET "
                    f"{assignments}, origin = excluded.origin, "
                    f"updated_at = excluded.updated_at",
                    (record.account_key, group, *values),
                )
                row = self._conn.execute(
                    "SELECT * FROM team_presets WHERE account_key = ?"
                    " AND group_id = ?",
                    (record.account_key, group),
                ).fetchone()
                if row is None:  # pragma: no cover - the write just succeeded
                    raise RoleStoreError(
                        "team preset row vanished inside its own transaction"
                    )
                return _team_preset_from_row(row)

            return self._transaction(body)

    def set_team_current_group(
        self,
        *,
        record: RoleRecord,
        group_id: int,
        origin: str = TEAM_CURRENT_GROUP_ORIGIN_LOCAL_C2S,
    ) -> "TeamStateRecord":
        """Store the preset the client's C2S 5 made current."""

        group = int(group_id)
        if not 1 <= group <= TEAM_MAX_GROUPS:
            raise RoleStoreError(
                f"unusable team group {group_id!r}; the client has "
                f"1..{TEAM_MAX_GROUPS}"
            )
        now = self._now()
        with self._lock:

            def body() -> "TeamStateRecord":
                role_row = self._conn.execute(
                    "SELECT * FROM roles WHERE account_key = ? AND role_id = ?",
                    (record.account_key, record.role_id),
                ).fetchone()
                if role_row is None:
                    raise RoleStoreError(
                        f"role archive {self._location} does not hold role "
                        f"{record.role_id}; refusing to record the current group"
                    )
                self._conn.execute(
                    "INSERT INTO team_state (account_key, current_group, origin,"
                    " updated_at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(account_key) DO UPDATE SET"
                    " current_group = excluded.current_group,"
                    " origin = excluded.origin,"
                    " updated_at = excluded.updated_at",
                    (record.account_key, group, str(origin), now),
                )
                row = self._conn.execute(
                    "SELECT * FROM team_state WHERE account_key = ?",
                    (record.account_key,),
                ).fetchone()
                if row is None:  # pragma: no cover - the write just succeeded
                    raise RoleStoreError(
                        "team state row vanished inside its own transaction"
                    )
                return _team_state_from_row(row)

            return self._transaction(body)

    # ------------------------------------------------------------------ lifetime

    def __enter__(self) -> "RoleStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()
            root = self._remove_root_on_close
            # The play-profile lease is taken by whoever opened the store and is
            # released by whoever closes it, so exactly one process serves one
            # profile for exactly as long as it holds the archive open.
            lock = self._instance_lock
            self._instance_lock = None
        if lock is not None:
            lock.release()  # type: ignore[attr-defined]
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Structure 1 -> 2: add ``story_completions``, and nothing else.

    Purely additive by construction: the statements come from the same tuple the
    create path uses, so "what a new archive has" and "what an upgraded archive
    gains" cannot drift apart.  No role row, no salt, no serial and no other
    table is read, rewritten or dropped.
    """

    for statement in _STORY_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Structure 2 -> 3: add ``bag_sort_states``, and nothing else.

    Purely additive by construction, from the same tuple the create path uses.
    A version-2 archive keeps its roles, its story rows, its salt and its serial
    byte-for-byte.
    """

    for statement in _BAG_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Structure 3 -> 4: add ``chapter_fight_clears``, and nothing else.

    Purely additive by construction, from the same tuple the create path uses.
    A version-3 archive keeps its roles, its story rows, its bag rows, its salt
    and its serial byte-for-byte.
    """

    for statement in _CHAPTER_FIGHT_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
    """Structure 4 -> 5: add ``card_instances``, and nothing else.

    Purely additive by construction, from the same tuple the create path uses.
    A version-4 archive keeps its roles, its story rows, its bag rows, its
    chapter-fight rows, its salt and its serial byte-for-byte.
    """

    for statement in _CARD_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
    """Structure 5 -> 6: add ``draw_pity``, and nothing else.

    Purely additive by construction, from the same tuple the create path uses.
    A version-5 archive keeps its roles, its story/bag/chapter/card rows, its
    salt and its serial byte-for-byte.
    """

    for statement in _DRAW_PITY_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v6_to_v7(connection: sqlite3.Connection) -> None:
    """Structure 6 -> 7: admit the limited pool and add the banner counters.

    SQLite cannot alter the ``draw_pity`` CHECK, so the table is rebuilt with
    the same columns and every row copied before the old table is dropped.
    ``hotspot_draw_pity`` is purely additive.  The rebuild runs in the same
    transaction as the version marker and behind the pre-migration snapshot.
    """

    connection.execute(
        """
        CREATE TABLE draw_pity_v7 (
            account_key  TEXT    NOT NULL,
            pool         TEXT    NOT NULL,
            since_legend INTEGER NOT NULL,
            since_rare   INTEGER NOT NULL,
            updated_at   INTEGER NOT NULL,
            provenance   TEXT    NOT NULL,
            PRIMARY KEY (account_key, pool),
            CHECK (pool IN ('normal', 'advanced', 'limited')),
            CHECK (since_legend >= 0),
            CHECK (since_rare >= 0)
        )
        """
    )
    connection.execute(
        "INSERT INTO draw_pity_v7 (account_key, pool, since_legend,"
        " since_rare, updated_at, provenance)"
        " SELECT account_key, pool, since_legend, since_rare, updated_at,"
        " provenance FROM draw_pity"
    )
    connection.execute("DROP TABLE draw_pity")
    connection.execute("ALTER TABLE draw_pity_v7 RENAME TO draw_pity")
    for statement in _HOTSPOT_PITY_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v7_to_v8(connection: sqlite3.Connection) -> None:
    """Structure 7 -> 8: add ``team_presets`` and ``team_state``, and nothing else.

    Purely additive by construction, from the same tuple the create path uses.
    A version-7 archive keeps its roles, story/bag/chapter/card/draw rows, its
    salt and its serial byte-for-byte.
    """

    for statement in _TEAM_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v8_to_v9(connection: sqlite3.Connection) -> None:
    """Structure 8 -> 9: add the card-growth inventory and wallet, nothing else.

    Purely additive: a version-8 archive keeps its roles, story/bag/chapter/
    card/draw/team rows, its salt and its serial byte-for-byte.
    """

    for statement in _GROWTH_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v9_to_v10(connection: sqlite3.Connection) -> None:
    """Structure 9 -> 10: add ``chapter_event_clears``, and nothing else.

    Purely additive: a version-9 archive keeps its roles, story/bag/chapter/
    card/draw/team/growth rows, its salt and its serial byte-for-byte.
    """

    for statement in _CHAPTER_EVENT_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v10_to_v11(connection: sqlite3.Connection) -> None:
    """Structure 10 -> 11: add ``chapter_box_accepts``, and nothing else.

    Purely additive: a version-10 archive keeps its roles, story/bag/chapter/
    card/draw/team/growth/event rows, its salt and its serial byte-for-byte.
    The dog food a box grants is written into the existing
    ``card_special_inventory`` table by ``accept_chapter_box``'s transaction.
    """

    for statement in _CHAPTER_BOX_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v11_to_v12(connection: sqlite3.Connection) -> None:
    """Structure 11 -> 12: add the event reward ledger and backfill it once.

    Purely additive: a version-11 archive keeps its roles, story/bag/chapter/
    card/draw/team/growth/event/box rows, its salt and its serial byte-for-byte.
    Every already-stored clear of the one recovered reward-bearing event
    (chapter 1 / index 36 / cfg 812003 / option 1) gains exactly one ledger row
    with the shipped ``guankaReward[25101].crystal = 10`` value.  The bucket is
    not recoverable from that row, so the backfill writes SQL NULL and
    ``bucket_provenance='unknown'``; ``granted_at`` is the migration's own
    clock, ``request_sequence``/``reply_sequence`` are the original clear's
    wire facts, and ``origin`` names the backfill.  The primary key plus the
    single migration step make a second backfill impossible.
    """

    for statement in _CHAPTER_EVENT_REWARD_SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO chapter_event_reward_grants"
        " (account_key, role_id, server_id, chapter_id, map_index,"
        " event_cfg_id, option_index, reward_id, crystal, crystal_bucket,"
        " bucket_provenance, granted_at, request_sequence, reply_sequence,"
        " origin, provenance)"
        " SELECT account_key, role_id, server_id, chapter_id, map_index,"
        " event_cfg_id, option_index, ?, ?, NULL, ?, ?, request_sequence,"
        " reply_sequence, ?, ?"
        " FROM chapter_event_clears"
        " WHERE chapter_id = ? AND map_index = ? AND event_cfg_id = ?"
        " AND option_index = ?",
        (
            chapter_map_event.REWARD_ID,
            chapter_map_event.REWARD_CRYSTAL,
            CRYSTAL_BUCKET_PROVENANCE_UNKNOWN,
            max(1, int(time.time())),
            CHAPTER_EVENT_REWARD_ORIGIN_SCHEMA_BACKFILL,
            CHAPTER_EVENT_REWARD_PROVENANCE_MIGRATION,
            chapter_map_event.CHAPTER_ID,
            chapter_map_event.MAP_INDEX,
            chapter_map_event.EVENT_CFG_ID,
            chapter_map_event.OPTION_INDEX,
        ),
    )


#: ``stored version -> upgrade callable``.  A step only ever *adds*: it may
#: create the objects a later structure needs, and it may not alter or delete a
#: row, a column or a constraint that an earlier structure wrote.  It runs
#: inside one transaction together with the version-marker update, and only ever
#: after a verified pre-migration snapshot exists.  Deliberately not a no-op: an
#: unknown stored version must never be rewritten in place.
#: TASK-0377: reward rows with no typed home in structures 1..12 (contract task_0375).
#: `role_wallet` is frozen to money/yinpo/jinpo, so every other role value (the artifact
#: tower's point, for one) had nowhere to go; and a card 碎片 is neither a card instance
#: nor a special card.  Both are additive tables, so the step is purely additive.



def _migrate_v12_to_v13(connection: sqlite3.Connection) -> None:
    """Structure 12 -> 13: add role_values and card_pieces (TASK-0377).

    Purely additive: no existing table, row or marker changes.  A version-12 archive
    keeps every role, wallet, card, special card, chapter and event row byte for byte.
    """

    for statement in _REWARD_STORAGE_STATEMENTS:
        connection.execute(statement)

def _migrate_v13_to_v14(connection: sqlite3.Connection) -> None:
    """Structure 13 -> 14: add god_weapon_instances (TASK-0397)."""

    for statement in _GOD_WEAPON_STATEMENTS:
        connection.execute(statement)


_MIGRATIONS: Mapping[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
    6: _migrate_v6_to_v7,
    7: _migrate_v7_to_v8,
    8: _migrate_v8_to_v9,
    9: _migrate_v9_to_v10,
    10: _migrate_v10_to_v11,
    11: _migrate_v11_to_v12,
    12: _migrate_v12_to_v13,
    13: _migrate_v13_to_v14,
}


# ------------------------------------------------------- read-only inspection
#
# One question a caller *outside* the server has to answer before it launches
# anything: would opening this file serve it -- and would the open have to rewrite
# the file's structure to do it?  TASK-0089 is why it is answered here rather than by
# the caller: the device layer's resume preflight compared the marker against a copy
# of `SCHEMA_VERSION` it kept for itself, so a save one structural version behind --
# exactly what every save written before TASK-0084 is -- was refused before the
# product that serves those saves had a chance to run its own upgrade.  The answer
# belongs to the code that will open the file, and nothing below is a second
# definition of anything: the marker is read, then the *same*
# `_REQUIRED_STRUCTURE` / `_MIGRATIONS` / `_identity_facts_of` the open and the
# upgrade consult decide the verdict, in loops over `range(marker, SCHEMA_VERSION)`
# that no future structural version has to edit.

#: The closed words an inspection's ``verdict`` can be.  Each names what an *open*
#: would do, never how the file happens to look: ``served`` -- the archive already
#: holds this build's structure and the open only reads it; ``migrated`` -- the
#: archive holds an older structure this build has a recovered, snapshot-protected
#: upgrade path for, so the open rewrites it; ``refused`` -- the open would fail, and
#: `error` names which of the refusals it would be.
INSPECTION_SERVED = "served"
INSPECTION_MIGRATED = "migrated"
INSPECTION_REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    """What a read-only look at one archive says about serving it.

    Every field is a fact read out of the file, or a closed word.  ``verdict`` is the
    conclusion; ``error``/``detail`` say which refusal an open would meet when it is
    ``refused``, and are empty otherwise.
    """

    location: str
    readable: bool
    error: str
    detail: str
    verdict: str
    schema_version: int | None
    target_version: int
    definition_recovered: bool
    missing_structure: tuple[tuple[str, tuple[str, ...]], ...]
    identity_missing: tuple[str, ...]
    migration_steps: tuple[int, ...]
    unrecovered_step: int | None
    pending_tables_present: tuple[str, ...]
    quick_check: str
    role_count: int | None

    @property
    def would_be_served(self) -> bool:
        """Would an open serve this archive (as it stands, or after its upgrade)?"""

        return self.verdict in (INSPECTION_SERVED, INSPECTION_MIGRATED)

    @property
    def would_migrate(self) -> bool:
        """Would serving it *rewrite* it -- i.e. is this an older, reachable source?"""

        return self.verdict == INSPECTION_MIGRATED

    def missing_structure_text(self) -> str:
        """The missing objects as one boundable clause (``""`` when there are none)."""

        return _describe_missing_structure(
            {table: columns for table, columns in self.missing_structure}
        )


def inspect_archive(location: str | Path) -> ArchiveInspection:
    """Would an open of this archive serve it, and would it have to upgrade it?

    Read-only and side-effect free, in the strong sense: ``mode=ro`` makes SQLite
    refuse every write, a file that is not there is never created, a damaged image is
    never repaired, and no migration is ever run -- the snapshot and the rewrite are
    the opening store's, not this function's.  It never raises either: every condition
    becomes ``refused`` with a bounded ``error`` word, so a caller can treat "no
    answer" and "the answer is no" the same way (and must).

    The verdict follows the open's own order of refusals: a marker that exists and has
    a recovered definition, a file that really holds the structure it claims, the three
    identity facts present, every upgrade step between the marker and
    :data:`SCHEMA_VERSION` recovered, and no table a pending step would create already
    present.  A *newer* marker is refused by name rather than by arithmetic.
    """

    path = Path(location)
    facts: dict[str, Any] = {
        "location": str(path),
        "readable": False,
        "error": "",
        "detail": "",
        "verdict": INSPECTION_REFUSED,
        "schema_version": None,
        "target_version": SCHEMA_VERSION,
        "definition_recovered": False,
        "missing_structure": (),
        "identity_missing": (),
        "migration_steps": (),
        "unrecovered_step": None,
        "pending_tables_present": (),
        "quick_check": "",
        "role_count": None,
    }

    def answer(**over: Any) -> ArchiveInspection:
        facts.update(over)
        return ArchiveInspection(**facts)

    try:
        if not path.is_file():
            return answer(error="not_a_file",
                          detail="the archive file is not there")
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return answer(error="unreadable",
                      detail="the archive file could not be read")
    if header != b"SQLite format 3\x00":
        return answer(error="header_not_sqlite",
                      detail="the file is not a SQLite database")
    try:
        connection = sqlite3.connect(
            _read_only_uri(path), uri=True, isolation_level=None
        )
    except sqlite3.Error:
        return answer(error="open_failed",
                      detail="SQLite would not open the file read-only")
    connection.row_factory = sqlite3.Row
    try:
        check = connection.execute("PRAGMA quick_check").fetchone()
        quick = "no result" if check is None else str(check[0])
        if quick != "ok":
            return answer(error="not_sound", quick_check=quick,
                          detail=f"the database is damaged ({quick})")
        tables = _table_names_of(connection)
        if "meta" not in tables:
            return answer(error="no_marker", quick_check=quick,
                          detail="the archive has no meta table, so it carries no "
                                 "structure marker")
        marker_row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if marker_row is None:
            return answer(error="no_marker", quick_check=quick,
                          detail="the archive carries no schema_version marker")
        try:
            marker = int(str(marker_row["value"]))
        except (TypeError, ValueError):
            return answer(error="unreadable_marker", quick_check=quick,
                          detail=f"the structure marker {marker_row['value']!r} is not "
                                 f"a number")
        if marker > SCHEMA_VERSION:
            return answer(error="newer_structure", quick_check=quick,
                          schema_version=marker,
                          detail=f"the archive was written by a newer revival_stub "
                                 f"(structure {marker} > {SCHEMA_VERSION})")
        if marker not in _REQUIRED_STRUCTURE:
            return answer(error="no_recovered_definition", quick_check=quick,
                          schema_version=marker,
                          detail=f"structure {marker} has no recovered definition of "
                                 f"what it must hold")
        missing = _missing_structure(connection, marker)
        if missing:
            return answer(
                error="structure_not_held", quick_check=quick, schema_version=marker,
                definition_recovered=True,
                missing_structure=tuple(
                    (table, tuple(columns)) for table, columns in sorted(missing.items())
                ),
                detail=f"it claims structure {marker} but does not hold it "
                       f"({_describe_missing_structure(missing)})")
        identity = _identity_facts_of(connection)
        lost = tuple(sorted(key for key, value in identity.items() if value is None))
        if lost:
            return answer(error="identity_lost", quick_check=quick,
                          schema_version=marker, definition_recovered=True,
                          identity_missing=lost,
                          detail=f"it has already lost its {', '.join(lost)}")
        steps = tuple(range(marker, SCHEMA_VERSION))
        unrecovered = next(
            (step for step in steps if step not in _MIGRATIONS), None)
        if unrecovered is not None:
            return answer(error="step_not_recovered", quick_check=quick,
                          schema_version=marker, definition_recovered=True,
                          migration_steps=steps, unrecovered_step=unrecovered,
                          detail=f"no recovered migration from structure "
                                 f"{unrecovered} to {unrecovered + 1} exists")
        pending: dict[str, tuple[str, ...]] = {}
        for step in steps:
            for table, columns in _REQUIRED_STRUCTURE[step + 1].items():
                if table not in _REQUIRED_STRUCTURE[step]:
                    pending[table] = columns
        already = tuple(sorted(table for table in pending if table in tables))
        if already:
            return answer(error="pending_table_present", quick_check=quick,
                          schema_version=marker, definition_recovered=True,
                          migration_steps=steps, pending_tables_present=already,
                          detail=f"it already holds {', '.join(already)} while its "
                                 f"marker says structure {marker}")
        count_row = connection.execute("SELECT COUNT(*) AS n FROM roles").fetchone()
        role_count = None if count_row is None else int(count_row["n"])
        verdict = (INSPECTION_SERVED if marker == SCHEMA_VERSION
                   else INSPECTION_MIGRATED)
        return answer(
            readable=True, quick_check=quick, schema_version=marker,
            definition_recovered=True, migration_steps=steps, role_count=role_count,
            verdict=verdict,
            detail=("the archive already holds this build's structure" if verdict
                    == INSPECTION_SERVED else
                    f"the archive is at structure {marker} and this build upgrades it "
                    f"step by step to {SCHEMA_VERSION}"))
    except sqlite3.Error as exc:
        return answer(error="query_failed",
                      detail=f"the archive could not be read: {exc}")
    finally:
        connection.close()


def _record_from_row(row: sqlite3.Row) -> RoleRecord:
    return RoleRecord(
        account_key=str(row["account_key"]),
        server_id=int(row["server_id"]),
        role_id=int(row["role_id"]),
        role_name=str(row["role_name"]),
        career=int(row["career"]),
        create_time=int(row["create_time"]),
        identity_origin=str(row["identity_origin"]),
        compatibility_profile_id=str(row["compatibility_profile_id"]),
        structure_version=int(row["structure_version"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _bag_sort_from_row(row: sqlite3.Row) -> BagSortRecord:
    return BagSortRecord(
        account_key=str(row["account_key"]),
        role_id=int(row["role_id"]),
        server_id=int(row["server_id"]),
        ops=int(row["ops"]),
        sort=int(row["sort"]),
        observed_at=int(row["observed_at"]),
        packet_sequence=int(row["packet_sequence"]),
        recorded_at=int(row["recorded_at"]),
        origin=str(row["origin"]),
    )


def _chapter_fight_clear_from_row(row: sqlite3.Row) -> ChapterFightClearRecord:
    return ChapterFightClearRecord(
        account_key=str(row["account_key"]),
        role_id=int(row["role_id"]),
        server_id=int(row["server_id"]),
        chapter_id=int(row["chapter_id"]),
        fight_id=int(row["fight_id"]),
        map_index=int(row["map_index"]),
        observed_at=int(row["observed_at"]),
        request_sequence=int(row["request_sequence"]),
        settlement_sequence=int(row["settlement_sequence"]),
        reply_sequence=int(row["reply_sequence"]),
        recorded_at=int(row["recorded_at"]),
        origin=str(row["origin"]),
    )


def _chapter_event_clear_from_row(row: sqlite3.Row) -> ChapterEventClearRecord:
    return ChapterEventClearRecord(
        account_key=str(row["account_key"]),
        role_id=int(row["role_id"]),
        server_id=int(row["server_id"]),
        chapter_id=int(row["chapter_id"]),
        map_index=int(row["map_index"]),
        event_cfg_id=int(row["event_cfg_id"]),
        option_index=int(row["option_index"]),
        observed_at=int(row["observed_at"]),
        request_sequence=int(row["request_sequence"]),
        reply_sequence=int(row["reply_sequence"]),
        recorded_at=int(row["recorded_at"]),
        origin=str(row["origin"]),
    )


def _chapter_event_reward_grant_from_row(
    row: sqlite3.Row,
) -> ChapterEventRewardGrantRecord:
    bucket = row["crystal_bucket"]
    return ChapterEventRewardGrantRecord(
        account_key=str(row["account_key"]),
        role_id=int(row["role_id"]),
        server_id=int(row["server_id"]),
        chapter_id=int(row["chapter_id"]),
        map_index=int(row["map_index"]),
        event_cfg_id=int(row["event_cfg_id"]),
        option_index=int(row["option_index"]),
        reward_id=int(row["reward_id"]),
        crystal=int(row["crystal"]),
        crystal_bucket=None if bucket is None else str(bucket),
        bucket_provenance=str(row["bucket_provenance"]),
        granted_at=int(row["granted_at"]),
        request_sequence=int(row["request_sequence"]),
        reply_sequence=int(row["reply_sequence"]),
        origin=str(row["origin"]),
        provenance=str(row["provenance"]),
    )


def _chapter_box_accept_from_row(row: sqlite3.Row) -> ChapterBoxAcceptRecord:
    return ChapterBoxAcceptRecord(
        account_key=str(row["account_key"]),
        role_id=int(row["role_id"]),
        chapter_id=int(row["chapter_id"]),
        box_index=int(row["box_index"]),
        reward_id=int(row["reward_id"]),
        accepted_at=int(row["accepted_at"]),
        reply_sequence=int(row["reply_sequence"]),
        origin=str(row["origin"]),
    )


def _card_advance_meta_key(account_key: str) -> str:
    return f"{CARD_ADVANCE_META_PREFIX}{account_key}"


def _card_advance_state(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Decode the one stored advance state, refusing anything unreadable."""

    if row is None:
        return None
    try:
        state = json.loads(str(row["value"]))
    except (TypeError, ValueError) as exc:
        raise RoleStoreError(
            f"card advance state is unreadable: {row['value']!r}"
        ) from exc
    if not isinstance(state, dict):
        raise RoleStoreError(f"card advance state is not an object: {state!r}")
    try:
        _card_advance_request_from_state(state)
    except (TypeError, ValueError) as exc:
        raise RoleStoreError(
            f"card advance state has an unusable value: {state!r}"
        ) from exc
    return state


def _card_advance_request_from_state(
    state: Mapping[str, Any], *, account_key: str = ""
) -> CardAdvanceRequestRecord:
    missing = [key for key in CARD_ADVANCE_KEYS if key not in state]
    if missing:
        raise RoleStoreError(
            f"card advance state lacks {', '.join(missing)}"
        )
    fodder = state["fodder_ids"]
    if not isinstance(fodder, list):
        raise RoleStoreError(
            f"card advance state fodder ids are not a list: {fodder!r}"
        )
    return CardAdvanceRequestRecord(
        account_key=str(account_key),
        role_id=int(state["role_id"]),
        server_id=int(state["server_id"]),
        sync_counter=int(state["sync_counter"]),
        instance_id=str(state["instance_id"]),
        advance_level_from=int(state["advance_level_from"]),
        advance_level_to=int(state["advance_level_to"]),
        money_spent=float(state["money_spent"]),
        yinpo_spent=int(state["yinpo_spent"]),
        jinpo_spent=int(state["jinpo_spent"]),
        fodder_ids=tuple(str(value) for value in fodder),
        recorded_at=int(state["recorded_at"]),
        origin=str(state["origin"]),
    )


def _card_instance_from_row(row: sqlite3.Row) -> CardInstanceRecord:
    return CardInstanceRecord(
        account_key=str(row["account_key"]),
        role_id=int(row["role_id"]),
        server_id=int(row["server_id"]),
        instance_id=str(row["instance_id"]),
        card_cfg_id=int(row["card_cfg_id"]),
        level=int(row["level"]),
        exp=int(row["exp"]),
        advance_level=int(row["advance_level"]),
        sfm_id1=int(row["sfm_id1"]),
        sfm_id2=int(row["sfm_id2"]),
        sfm_id3=int(row["sfm_id3"]),
        sfm_id4=int(row["sfm_id4"]),
        equip_id1=str(row["equip_id1"]),
        equip_id2=str(row["equip_id2"]),
        equip_id3=str(row["equip_id3"]),
        equip_id4=str(row["equip_id4"]),
        locked=bool(int(row["locked"])),
        refine_lv=int(row["refine_lv"]),
        refine_lv_times=int(row["refine_lv_times"]),
        learn_atk=int(row["learn_atk"]),
        learn_hp=int(row["learn_hp"]),
        learn_state=int(row["learn_state"]),
        refine_skill=str(row["refine_skill"]),
        god_weapon_id=str(row["god_weapon_id"]),
        origin=str(row["origin"]),
        fixture_id=None if row["fixture_id"] is None else str(row["fixture_id"]),
        fixture_kind=(
            None if row["fixture_kind"] is None else str(row["fixture_kind"])
        ),
        recorded_at=int(row["recorded_at"]),
    )


def _hotspot_pity_from_row(row: sqlite3.Row) -> HotSpotPityRecord:
    return HotSpotPityRecord(
        account_key=str(row["account_key"]),
        hotspot_id=int(row["hotspot_id"]),
        since_featured_legend=int(row["since_featured_legend"]),
        updated_at=int(row["updated_at"]),
        provenance=str(row["provenance"]),
    )


def _team_preset_from_row(row: sqlite3.Row) -> TeamPresetRecord:
    return TeamPresetRecord(
        account_key=str(row["account_key"]),
        group_id=int(row["group_id"]),
        equip_ids=tuple(str(row[f"equip_id{index}"]) for index in range(1, 5)),
        god_weapon_id=str(row["god_weapon_id"]),
        card_ids=tuple(
            str(row[f"card_id{index}"]) for index in range(1, TEAM_CARD_SLOTS + 1)
        ),
        origin=str(row["origin"]),
        updated_at=int(row["updated_at"]),
    )


def _team_state_from_row(row: sqlite3.Row) -> TeamStateRecord:
    return TeamStateRecord(
        account_key=str(row["account_key"]),
        current_group=int(row["current_group"]),
        origin=str(row["origin"]),
        updated_at=int(row["updated_at"]),
    )


def _draw_pity_from_row(row: sqlite3.Row) -> DrawPityRecord:
    return DrawPityRecord(
        account_key=str(row["account_key"]),
        pool=str(row["pool"]),
        since_legend=int(row["since_legend"]),
        since_rare=int(row["since_rare"]),
        updated_at=int(row["updated_at"]),
        provenance=str(row["provenance"]),
    )


def _story_from_row(row: sqlite3.Row) -> StoryRecord:
    return StoryRecord(
        account_key=str(row["account_key"]),
        role_id=int(row["role_id"]),
        server_id=int(row["server_id"]),
        story_id=int(row["story_id"]),
        state=int(row["state"]),        observed_at=int(row["observed_at"]),
        evidence_kind=str(row["evidence_kind"]),
        evidence_story_id=int(row["evidence_story_id"]),
        predecessor_sequence=int(row["predecessor_sequence"]),
        successor_sequence=int(row["successor_sequence"]),
        recorded_at=int(row["recorded_at"]),
        origin=str(row["origin"]),
    )
