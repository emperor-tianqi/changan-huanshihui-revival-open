from __future__ import annotations

import unittest
from pathlib import Path
import re

from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY
from revival_stub.role_state import (
    MAX_EXACT_LUA_INTEGER,
    ROLE_INFO_FIELD_NAMES,
    MinimalRoleState,
    allocate_revival_role_id,
)


class MinimalRoleStateTests(unittest.TestCase):
    def test_protocol_contracts_match_recovered_registration(self) -> None:
        self.assertEqual(
            (S2C_REGISTRY.by_id(8).name, S2C_REGISTRY.by_id(8).fmt),
            ("returnLoginInfoStart", "Ny"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(9).name, S2C_REGISTRY.by_id(9).fmt),
            ("returnLoginInfoEnd", "Ny"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(10).name, S2C_REGISTRY.by_id(10).fmt),
            ("returnRoleInfo", "apapppppppppppppppppp"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(27).name, len(S2C_REGISTRY.by_id(27).fmt)),
            ("returnAllValue", 252),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(85).name, S2C_REGISTRY.by_id(85).fmt),
            ("returnSignInReward", "yNNNyaaaaaN"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(283).name, S2C_REGISTRY.by_id(283).fmt),
            ("returnSceneInfo", "Nnnn"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(554).name, S2C_REGISTRY.by_id(554).fmt),
            ("returnTalentSoulId", "N"),
        )
        self.assertEqual(
            (C2S_REGISTRY.by_id(4).name, C2S_REGISTRY.by_id(4).fmt),
            ("enterGame", "y"),
        )
        self.assertEqual(
            (S2C_REGISTRY.by_id(13).name, S2C_REGISTRY.by_id(13).fmt),
            ("enterGameSuccess", ""),
        )

        source = (
            Path(__file__).resolve().parents[2]
            / "max_decoder"
            / "decompiled"
            / "zys_scAll__np__pkgScProtocolMgr.lua"
        ).read_text(encoding="utf-8")
        self.assertIn('registerS2C("Ny", "returnLoginInfoStart")', source)
        self.assertIn('registerS2C("Ny", "returnLoginInfoEnd")', source)
        self.assertIn(
            'registerS2C("apapppppppppppppppppp", "returnRoleInfo")',
            source,
        )

        # This list deliberately sees only calls whose format is a string
        # literal.  Its index is a source ordinal, not necessarily the native
        # runtime wire ID: pkgScProtocolMgr also registers dynamic value,
        # battle, and transfer protocols into the same append-only S2C table.
        literal_registrations = re.findall(
            r'registerS2C\("([^\"]*)",\s*"([^\"]+)"', source
        )
        self.assertEqual(
            literal_registrations[138], ("Nnnn", "returnSceneInfo")
        )

        # Before returnSceneInfo, runtime registration additionally appends
        # returnAllValue (1), battleProtoSg2Cg (11),
        # battleProtoSf2Cg (120), and BattleTransfer (13).  Native regProto
        # assigns the previous vector length, so the real wire ID is 283.
        return_scene_runtime_id = 138 + 1 + 11 + 120 + 13
        self.assertEqual(return_scene_runtime_id, 283)
        self.assertNotEqual(return_scene_runtime_id, 138)
        self.assertEqual(
            S2C_REGISTRY.by_id(return_scene_runtime_id).name,
            "returnSceneInfo",
        )
        with self.assertRaises(KeyError):
            S2C_REGISTRY.by_id(138)

        login_handlers = (
            Path(__file__).resolve().parents[2]
            / "max_decoder"
            / "decompiled"
            / "luaScript__socket__nc__pkgCgLoginNcMgr.lua"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "pkgOnlinePlayersSceneMgr.switchSceneByRecordType(dSceneType)",
            login_handlers,
        )

    def test_role_info_field_order_and_minimal_values(self) -> None:
        role = MinimalRoleState(
            username="local-player",
            role_id=900_000_000_001,
            role_name="单机道友",
            career=1,
            create_time=1_700_000_000,
        )
        fields = role.protocol_fields()
        self.assertEqual(len(fields), len(ROLE_INFO_FIELD_NAMES))
        named = dict(zip(ROLE_INFO_FIELD_NAMES, fields, strict=True))
        self.assertEqual(named["username"], "local-player")
        self.assertEqual(named["role_name"], "单机道友")
        self.assertEqual(named["career"], 1)
        self.assertEqual(named["level"], 1)
        self.assertEqual(named["exp"], 0)
        self.assertEqual(named["power"], 180)
        self.assertEqual(named["create_time"], 1_700_000_000)
        self.assertEqual(named["crystal"], 0)
        self.assertEqual(
            named["crystal"],
            named["crystal_rmb"]
            + named["crystal_rmb_bind"]
            + named["crystal_ex"]
            + named["crystal_ex_bind"]
            + named["crystal_free"]
            + named["crystal_free_bind"],
        )
        self.assertEqual(role.server_id, 1)
        self.assertIsNotNone(role.compatibility)
        self.assertEqual(role.compatibility.active_soul, 101)

    def test_revival_role_id_is_exact_and_preserves_server_id(self) -> None:
        role_id = allocate_revival_role_id(9_000_000, 23.0)
        self.assertEqual(role_id, 900_000_000_023)
        self.assertEqual(role_id % 100_000, 23)
        self.assertLess(role_id, MAX_EXACT_LUA_INTEGER)

    def test_revival_role_id_rejects_invalid_server_id(self) -> None:
        for server_id in (0, 100_000, -1, 1.5, float("inf")):
            with self.subTest(server_id=server_id):
                with self.assertRaises(ValueError):
                    allocate_revival_role_id(9_000_000, server_id)


if __name__ == "__main__":
    unittest.main()
