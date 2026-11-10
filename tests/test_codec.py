from __future__ import annotations

import hashlib
import inspect
import struct
import unittest
from pathlib import Path
import re

from revival_stub.codec import (
    INITIAL_KEY,
    MalformedPacket,
    NeedMoreData,
    ReaderState,
    SequenceMismatch,
    UnknownProtocol,
    WriterState,
    decode_packet,
    encode_packet,
)
from revival_stub.compatibility_profile import (
    get_profile,
    get_profile_v1,
    get_profile_v2,
    get_profile_v3,
    get_profile_v4,
    get_profile_v5,
    get_profile_v6,
    get_profile_v7,
    get_profile_v8,
    get_profile_v9,
    get_profile_v10,
    get_profile_v11,
    get_profile_v12,
    PROFILE_ID_V13,
    instantiate_role_compatibility_state,
)
from revival_stub.registry import C2S_REGISTRY, S2C_REGISTRY, ProtocolRegistry
from revival_stub.role_state import MinimalRoleState


class CodecTests(unittest.TestCase):
    def test_compatibility_wire_audit_regression(self) -> None:
        profile = get_profile()
        compatibility = instantiate_role_compatibility_state(1, profile=profile)
        role = MinimalRoleState(
            username="local-player",
            role_id=900_000_000_001,
            role_name="wire-audit",
            career=1,
            create_time=1_700_000_000,
            server_id=1,
            compatibility=compatibility,
        )
        writer = WriterState()

        def send(protocol_id: int, fields: tuple[object, ...]):
            return encode_packet(S2C_REGISTRY.by_id(protocol_id), fields, writer)

        send(5, ())
        send(14, (True,))
        send(8, (1, False))
        send(10, role.protocol_fields())
        send(9, (1, False))
        send(8, (40, False))
        soul = send(554, (101,))
        role_values = send(27, compatibility.role_value_vector())
        # Compatibility profile v2 inserts S2C 0 serverTime between S2C27 and the
        # terminal S2C9(40); profile v3 then inserts S2C 487 refreshShopStart after
        # S2C0; profile v4 then inserts S2C 411 returnStoryStart between the
        # terminal S2C9(40) and S2C 283 returnSceneInfo.  The 554/27/0 hashes
        # above must stay unchanged; the packets after the 487 insertion are
        # frozen at that insertion (487: sequence 9, key_before 0x992CADF3,
        # key_after 0xFBBECD65) and the ones after the 411 insertion are frozen
        # against the v4 transaction (411: sequence 11, key_before 0xD132D360,
        # key_after 0xCE61EA1B).
        server_time = send(0, (1_700_000_123, 0, 0, 1_700_000_000))
        shop = send(
            487,
            profile.mystery_shop.protocol_fields(server_time_dtime=1_700_000_123),
        )
        send(9, (40, False))
        story = send(411, profile.story_start.protocol_fields())
        scene = send(283, compatibility.scene_state.protocol_fields())
        enter_ack = send(13, ())
        sign = send(
            85,
            compatibility.sign_state.protocol_fields(profile.sign_catalogs),
        )

        expected = {
            "returnTalentSoulId": (7, "00d429111d7b96f4d376f3f23721e89bba047cd9e120f64359d68c3f36423804"),
            "returnAllValue": (1015, "94cb263839c105be620e286898375444da68640f3c99ad87ea507d02dbe55cda"),
            "serverTime": (19, "2dcc839d1e9e637fd5ca3ac0b44461f2153fa03e057f6eb96b66dd2203ac9cbd"),
            "refreshShopStart": (15, "fd6db9c5b431feb60f3ed5ad942b21ec527a0ca353a7ce57f2a274c1d7f50981"),
            "returnStoryStart": (3, "829d9916206f9fa66e208f2c9ad74153d3aa2d6b8950c3628e5dd9c845a1f5a7"),
            "returnSceneInfo": (19, "2ab949876b6e9f9c072b05904c3195bd6c3e7a0645d5509fdf8bbce5fe8735c1"),
            "enterGameSuccess": (3, "f5018e42f7128a8a151ec49c12fb9a1d77ec8dc7a13d3bef9dc2abe3469936a5"),
            "returnSignInReward": (531, "ae4edabd2df89158d7eb6ed063a2752f09d23beeeb5ae6d517c45a8e2b0c56f9"),
        }
        self.assertEqual(server_time.sequence, 8)
        self.assertEqual(server_time.key_before, 0x49E8851B)
        self.assertEqual(server_time.key_after, 0x992CADF3)
        self.assertEqual(shop.sequence, 9)
        self.assertEqual(shop.key_before, server_time.key_after)
        self.assertEqual(shop.key_after, 0xFBBECD65)
        self.assertEqual(story.sequence, 11)
        self.assertEqual(story.key_before, 0xD132D360)
        self.assertEqual(story.key_after, 0xCE61EA1B)
        self.assertEqual(story.fields, ())
        self.assertEqual(scene.key_before, story.key_after)
        for packet in (soul, role_values, server_time, shop, story, scene, enter_ack, sign):
            with self.subTest(protocol=packet.spec.name):
                length, digest = expected[packet.spec.name]
                self.assertEqual(len(packet.wire), length)
                self.assertEqual(hashlib.sha256(packet.wire).hexdigest(), digest)
        self.assertEqual(scene.spec.protocol_id, 283)
        self.assertNotEqual(scene.spec.protocol_id, 138)

    def test_compatibility_protocol_round_trips(self) -> None:
        profile = get_profile()
        role_state = instantiate_role_compatibility_state(1, profile=profile)

        cases = (
            (S2C_REGISTRY.by_id(554), (101,)),
            (S2C_REGISTRY.by_id(27), role_state.role_value_vector()),
            (S2C_REGISTRY.by_id(0), (1_700_000_123, 0, 0, 1_700_000_000)),
            (S2C_REGISTRY.by_id(487), (1, 1_700_000_123)),
            (S2C_REGISTRY.by_id(411), ()),
            (S2C_REGISTRY.by_id(283), (0, 0, 0, 0)),
            (S2C_REGISTRY.by_id(13), ()),
            (S2C_REGISTRY.by_id(1487), ()),
            (
                S2C_REGISTRY.by_id(85),
                role_state.sign_state.protocol_fields(profile.sign_catalogs),
            ),
        )
        # TASK-0167: the v13 prison-fight snapshot is an ordinary S2C; its 11
        # values must survive the same round trip as every other packet.
        prison_fight = profile.prison_fight
        assert prison_fight is not None
        cases = cases + (
            (S2C_REGISTRY.by_id(1370), prison_fight.snapshot_fields()),
        )
        for spec, fields in cases:
            with self.subTest(protocol=spec.name):
                packet = encode_packet(spec, fields, WriterState())
                decoded = decode_packet(packet.wire, S2C_REGISTRY, ReaderState())
                self.assertEqual(decoded.spec.protocol_id, spec.protocol_id)
                if spec.name == "returnAllValue":
                    self.assertEqual(len(decoded.fields), 252)
                    self.assertTrue(all(value == 0 for value in decoded.fields))
                elif spec.name == "returnSignInReward":
                    self.assertEqual(decoded.fields[:5], (False, 0, 0, 0, False))
                    self.assertEqual(decoded.fields[7], b"")
                    self.assertEqual(decoded.fields[9:], (b"", 1))
                else:
                    self.assertEqual(decoded.fields, fields)

    def test_immediate_c2s_contract_round_trips(self) -> None:
        cases = {
            217: (12_345.0, b"apk-md5"),
            4: (False,),
            22: (),
            23: (1_700_000_123.0, 12.5, True),
            37: (),
            39: (),
            579: (True,),
            318: (),
            495: (1,),
            720: (1,),
            233: (1008,),
            232: (GUIDE_TASK_RUN0045_GUIDE_ID,),
        }
        for protocol_id, fields in cases.items():
            with self.subTest(protocol_id=protocol_id):
                spec = C2S_REGISTRY.by_id(protocol_id)
                encoded = encode_packet(spec, fields, WriterState())
                decoded = decode_packet(encoded.wire, C2S_REGISTRY, ReaderState())
                self.assertEqual(decoded.spec.protocol_id, protocol_id)
                self.assertEqual(decoded.fields, fields)

    def test_finish_story_decode_contract(self) -> None:
        # TASK-0035 / D-42: C2S 233 finishStory 'N' -- exactly one uint32
        # storyId, no lock partner, no S2C response.  The first three ids are the
        # deterministic fresh-role 千灯镇 chain (1008 -> 1100 -> 1009); the fourth
        # proves the contract is the FORMAT N, not a fixed id list.
        spec = C2S_REGISTRY.by_id(233)
        self.assertEqual(spec.name, "finishStory")
        self.assertEqual(spec.fmt, "N")
        self.assertNotIn("finishStory", {p.name for p in S2C_REGISTRY})
        for story_id in (1008, 1100, 1009, 0, 1, 9105, 0xFFFFFFFF):
            with self.subTest(story_id=story_id):
                encoded = encode_packet(spec, (story_id,), WriterState())
                # 'N' is a fixed 4-byte field: 3-byte header + 4-byte payload.
                self.assertEqual(len(encoded.wire), 7)
                decoded = decode_packet(encoded.wire, C2S_REGISTRY, ReaderState())
                self.assertEqual(decoded.spec.protocol_id, 233)
                self.assertEqual(decoded.spec.name, "finishStory")
                self.assertEqual(decoded.fields, (story_id,))
                self.assertEqual(decoded.consumed, 7)
        # A truncated payload is a NeedMoreData (an incomplete packet is never
        # guessed), and the wire-level length is the only authority.
        full = encode_packet(spec, (1008,), WriterState())
        for length in (3, 6):
            with self.subTest(truncated_to=length):
                with self.assertRaises(NeedMoreData):
                    decode_packet(full.wire[:length], C2S_REGISTRY, ReaderState())
        # The id is still unknown to a registry that lacks the registration:
        # registering 233 must not have widened the D-08 unknown-id path.
        without_story = ProtocolRegistry("C2S")
        with self.assertRaises(UnknownProtocol):
            decode_packet(full.wire, without_story, ReaderState())

    def test_client_reload_completion_contract_round_trips(self) -> None:
        # TASK-0015: C2S 720 receiveClientReload 'N' is the lock-send request the
        # S2C 13 handler emits unconditionally; the ONLY unlock is the empty
        # S2C 1487 initClientReloadEnd.
        request = encode_packet(C2S_REGISTRY.by_id(720), (1,), WriterState())
        self.assertEqual(len(request.wire), 7)
        decoded_request = decode_packet(request.wire, C2S_REGISTRY, ReaderState())
        self.assertEqual(decoded_request.spec.protocol_id, 720)
        self.assertEqual(decoded_request.spec.name, "receiveClientReload")
        self.assertEqual(decoded_request.fields, (1,))
        self.assertEqual(decoded_request.consumed, 7)

        unlock = encode_packet(S2C_REGISTRY.by_id(1487), (), WriterState())
        # Empty format: the 3-byte header alone, no payload.
        self.assertEqual(unlock.wire, bytes.fromhex("5c 74 93"))
        self.assertEqual(unlock.sequence, 0)
        decoded_unlock = decode_packet(unlock.wire, S2C_REGISTRY, ReaderState())
        self.assertEqual(decoded_unlock.spec.protocol_id, 1487)
        self.assertEqual(decoded_unlock.spec.name, "initClientReloadEnd")
        self.assertEqual(decoded_unlock.fields, ())
        self.assertEqual(decoded_unlock.consumed, 3)

    def test_client_reload_completion_frozen_in_transaction(self) -> None:
        # The empty S2C 1487 as it appears after a deterministic 13-packet login
        # prefix (the same S2C order as the wire audit, minus the two
        # variable-length packets 10/27), i.e. the position where the client's
        # S2C 13 handler provokes C2S 720.  Frozen so a codec/registry change
        # cannot silently redefine the unlock wire.
        writer = WriterState()
        prefix = (
            (5, ()), (14, (True,)), (8, (1, False)), (9, (1, False)),
            (8, (40, False)), (554, (101,)), (0, (1_700_000_123, 0, 0, 1_700_000_000)),
            (487, (1, 1_700_000_123)), (9, (40, False)), (411, ()),
            (283, (0, 0, 0, 0)),
            (13, ()), (85, (False, 0, 0, 0, False, 0, b"", b"", b"", b"", 1)),
        )
        for expected_sequence, (protocol_id, fields) in enumerate(prefix):
            encoded = encode_packet(S2C_REGISTRY.by_id(protocol_id), fields, writer)
            self.assertEqual(encoded.sequence, expected_sequence)
        unlock = encode_packet(S2C_REGISTRY.by_id(1487), (), writer)
        self.assertEqual(unlock.sequence, 13)
        self.assertEqual(unlock.key_before, 0x5D511BAE)
        self.assertEqual(unlock.wire, bytes.fromhex("61 1e a3"))
        self.assertEqual(
            hashlib.sha256(unlock.wire).hexdigest(),
            "c8edc9a929d9ae930949b612942454e3547aa51c11136962eae7026477cef4ed",
        )

    def test_run0011_real_device_reload_request_decodes(self) -> None:
        # Frozen real-device vector (TASK-0014 forensic, reports/run_0011_main_city_forensics.md
        # §9.9-D): RUN-0011 server.log 2026-10-05T10:52:11.523Z on conn-000002
        # recorded the client's first C2S 720 as
        #   {"classification": "malformed_packet", "message": "unknown protocol id 720",
        #    "raw_header_hex": "cb ce 00", "raw_buffer_hex": "cb ce 00 1a cc 12 89"}
        # because the stub had no id 720.  Re-decoding the saved 7 bytes with the
        # stub's own codec at the live reader state (key 0x8912CC1B from the 27
        # preceding C2S packets, sequence 27) must yield receiveClientReload(1).
        raw = bytes.fromhex("cb ce 00 1a cc 12 89")
        reader = ReaderState(key=0x8912CC1B, sequence=27)
        decoded = decode_packet(raw, C2S_REGISTRY, reader)
        self.assertEqual(decoded.spec.protocol_id, 720)
        self.assertEqual(decoded.spec.name, "receiveClientReload")
        self.assertEqual(decoded.fields, (1,))
        self.assertEqual(decoded.sequence, 27)
        self.assertEqual(decoded.consumed, 7)
        self.assertEqual(decoded.key_after, 0xFE0E55C4)
        # And the registry no longer classifies it as unknown (the D-08 cause).
        self.assertEqual(C2S_REGISTRY.by_id(720).fmt, "N")

    def test_protocol_26_matches_recovered_registration_order(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[2]
            / "max_decoder"
            / "decompiled"
            / "zys_scAll__np__pkgScProtocolMgr.lua"
        )
        source = source_path.read_text(encoding="utf-8")
        head = source.split(
            "local function initHeadProtocol", 1
        )[1].split("local function initC2SProtocol", 1)[0]
        body = source.split(
            "local function initC2SProtocol", 1
        )[1].split("pkgBattleNet.registerC2S(registerC2S)", 1)[0]
        registrations = re.findall(
            r'registerC2S\("([^\"]*)", "([^\"]+)"\)', head + body
        )
        self.assertEqual(len(registrations), 61)
        self.assertEqual(registrations[0], ("paapaaaaaap", "loginByToken"))
        self.assertEqual(registrations[25], ("", "onLine"))
        self.assertEqual(registrations[26], ("", "onLine_forNetState"))

    def test_recovered_initial_login_header(self) -> None:
        fields = (
            1.0, b"u", b"m", 2.0, b"0.1.20.1", b"c", b"0",
            b"android", b"d", b"n", 0.0,
        )
        packet = encode_packet(C2S_REGISTRY.by_id(0), fields, WriterState())
        self.assertEqual(packet.wire[:3], bytes.fromhex("93 71 93"))

    def test_recovered_initial_no_role_packet(self) -> None:
        packet = encode_packet(S2C_REGISTRY.by_id(5), (), WriterState())
        self.assertEqual(packet.wire, bytes.fromhex("96 71 93"))

    def test_recovered_post_login_net_state_packet(self) -> None:
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0),
            (
                1.0, b"u", b"m", 2.0, b"0.1.20.1", b"c", b"0",
                b"android", b"d", b"n", 0.0,
            ),
            writer,
        )
        heartbeat = encode_packet(C2S_REGISTRY.by_id(26), (), writer)
        self.assertEqual(login.wire[:3], bytes.fromhex("93 71 93"))
        self.assertEqual(heartbeat.wire, bytes.fromhex("89 71 92"))
        self.assertEqual(heartbeat.sequence, 1)
        self.assertEqual(heartbeat.key_before, INITIAL_KEY)
        self.assertEqual(
            heartbeat.key_after,
            (INITIAL_KEY * 2 + 26) & 0xFFFFFFFF,
        )

        reader = ReaderState()
        decoded_login = decode_packet(login.wire, C2S_REGISTRY, reader)
        decoded_heartbeat = decode_packet(
            heartbeat.wire, C2S_REGISTRY, reader
        )
        self.assertEqual(decoded_login.spec.name, "loginByToken")
        self.assertEqual(decoded_heartbeat.spec.name, "onLine_forNetState")
        self.assertEqual(decoded_heartbeat.fields, ())
        self.assertEqual(decoded_heartbeat.consumed, 3)

    def test_round_trip_every_supported_type(self) -> None:
        registry = ProtocolRegistry("test")
        spec = registry.register(42, "allTypes", "TtWwNnpya")
        fields = (
            255, -12, 65535, -1234, 0xFEDCBA98, -123456789,
            12345.125, True, "幻世",
        )
        encoded = encode_packet(spec, fields, WriterState())
        decoded = decode_packet(encoded.wire, registry, ReaderState())
        self.assertEqual(decoded.fields[:-3], fields[:-3])
        self.assertAlmostEqual(decoded.fields[6], fields[6])
        self.assertIs(decoded.fields[7], True)
        self.assertEqual(decoded.fields[8], "幻世".encode("utf-8"))
        self.assertEqual(decoded.consumed, len(encoded.wire))

    def test_string_length_is_u16_and_encrypted(self) -> None:
        registry = ProtocolRegistry("test")
        spec = registry.register(7, "string", "a")
        encoded = encode_packet(spec, (b"abc",), WriterState())
        wire_length = struct.unpack_from("<H", encoded.wire, 3)[0]
        self.assertEqual(wire_length, 3 ^ (INITIAL_KEY & 0xFFFF))
        decoded = decode_packet(encoded.wire, registry, ReaderState())
        self.assertEqual(decoded.fields, (b"abc",))

    def test_double_only_xors_low_word(self) -> None:
        registry = ProtocolRegistry("test")
        spec = registry.register(8, "number", "p")
        encoded = encode_packet(spec, (12.5,), WriterState())
        plain = struct.pack("<d", 12.5)
        self.assertEqual(encoded.wire[7:11], plain[4:8])
        self.assertNotEqual(encoded.wire[3:7], plain[0:4])
        decoded = decode_packet(encoded.wire, registry, ReaderState())
        self.assertEqual(decoded.fields, (12.5,))

    def test_rolling_key_matches_recovered_formula(self) -> None:
        writer = WriterState()
        reader = ReaderState()
        first = encode_packet(S2C_REGISTRY.by_id(5), (), writer)
        expected_first = (INITIAL_KEY * 1 + 5) & 0xFFFFFFFF
        self.assertEqual(writer.key, expected_first)
        decoded_first = decode_packet(first.wire, S2C_REGISTRY, reader)
        self.assertEqual(reader.key, expected_first)
        second = encode_packet(S2C_REGISTRY.by_id(14), (True,), writer)
        expected_second = (expected_first * 2 + 14) & 0xFFFFFFFF
        self.assertEqual(writer.key, expected_second)
        decoded_second = decode_packet(second.wire, S2C_REGISTRY, reader)
        self.assertEqual(reader.key, expected_second)
        self.assertEqual(decoded_first.spec.protocol_id, 5)
        self.assertEqual(decoded_second.fields, (True,))

    def test_reader_and_writer_states_are_independent(self) -> None:
        reader = ReaderState()
        writer = WriterState()
        encode_packet(S2C_REGISTRY.by_id(5), (), writer)
        self.assertEqual(reader.sequence, 0)
        self.assertEqual(reader.key, INITIAL_KEY)
        self.assertEqual(writer.sequence, 1)

    def test_truncated_header(self) -> None:
        with self.assertRaises(NeedMoreData):
            decode_packet(b"\x93\x71", C2S_REGISTRY, ReaderState())

    def test_truncated_fixed_payload(self) -> None:
        registry = ProtocolRegistry("test")
        spec = registry.register(1, "fixed", "N")
        encoded = encode_packet(spec, (123,), WriterState())
        with self.assertRaises(NeedMoreData):
            decode_packet(encoded.wire[:-1], registry, ReaderState())

    def test_truncated_string_payload(self) -> None:
        registry = ProtocolRegistry("test")
        spec = registry.register(1, "string", "a")
        encoded = encode_packet(spec, (b"abcdef",), WriterState())
        with self.assertRaises(NeedMoreData):
            decode_packet(encoded.wire[:-2], registry, ReaderState())

    def test_wrong_sequence_is_explicit_and_does_not_advance(self) -> None:
        packet = encode_packet(S2C_REGISTRY.by_id(5), (), WriterState())
        corrupt = bytearray(packet.wire)
        corrupt[2] ^= 1
        reader = ReaderState()
        with self.assertRaises(SequenceMismatch):
            decode_packet(corrupt, S2C_REGISTRY, reader)
        self.assertEqual(reader.sequence, 0)
        self.assertEqual(reader.key, INITIAL_KEY)

    def test_unknown_protocol_is_explicit(self) -> None:
        unknown_id = 99
        header = struct.pack("<H", unknown_id ^ (INITIAL_KEY & 0xFFFF))
        header += bytes((INITIAL_KEY & 0xFF,))
        with self.assertRaises(UnknownProtocol) as caught:
            decode_packet(header, C2S_REGISTRY, ReaderState())
        self.assertEqual(caught.exception.protocol_id, unknown_id)
        self.assertEqual(caught.exception.raw_header, header)
        self.assertEqual(caught.exception.raw_buffer, header)

    def test_recovered_online_heartbeat_has_zero_payload(self) -> None:
        writer = WriterState()
        login = encode_packet(
            C2S_REGISTRY.by_id(0),
            (
                1.0, b"u", b"m", 2.0, b"0.1.20.1", b"c", b"0",
                b"android", b"d", b"n", 0.0,
            ),
            writer,
        )
        online = encode_packet(C2S_REGISTRY.by_id(25), (), writer)
        self.assertEqual(online.sequence, 1)
        self.assertEqual(len(online.wire), 3)
        reader = ReaderState()
        decode_packet(login.wire, C2S_REGISTRY, reader)
        decoded = decode_packet(online.wire, C2S_REGISTRY, reader)
        self.assertEqual(decoded.spec.name, "onLine")
        self.assertEqual(decoded.fields, ())

    def test_create_role_contract_is_reflected_in_recovered_source(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = (
            root
            / "max_decoder"
            / "forensics_create_role"
            / "luaScript__createRole__pkgCreateRoleMgr.lua"
        ).read_text(encoding="utf-8-sig")
        self.assertIn(
            "c2s.createRole(e_tbSocket, strRoleName, dCareer)", source
        )
        self.assertIn(
            "function s2c.returnCreateRoleResult(tbSocket, bCreateRole)",
            source,
        )
        self.assertIn("if bCreateRole then", source)
        self.assertIn("pkgSDKPlayerInfoMgr.createRole()", source)

    def test_schema_derived_length_cap(self) -> None:
        registry = ProtocolRegistry("test")
        registry.register(1, "string", "a")
        header = struct.pack("<H", 1 ^ (INITIAL_KEY & 0xFFFF))
        header += bytes((INITIAL_KEY & 0xFF,))
        encoded_length = 5000 ^ (INITIAL_KEY & 0xFFFF)
        packet = header + struct.pack("<H", encoded_length)
        with self.assertRaises(MalformedPacket):
            decode_packet(packet, registry, ReaderState(), max_packet_size=1024)

    def test_field_count_is_checked(self) -> None:
        with self.assertRaises(MalformedPacket):
            encode_packet(S2C_REGISTRY.by_id(14), (), WriterState())


#: TASK-0134.  RUN-0040's own bytes, copied verbatim out of that run's stub
#: record -- `.autopilot/device_runs/RUN-0040/server.log` line 708, the packet
#: the client sent 1.62 s before the challenge tap:
#:
#:     {"classification": "malformed_packet", "connection_id": "conn-000002",
#:      "event": "tcp_error", "message": "unknown protocol id 69",
#:      "protocol_id": 69, "raw_buffer_hex": "<RAW_BUFFER_HEX>",
#:      "raw_buffer_length": 86, "raw_header_hex": "d0 fb 6f", ...}
#:
#: The run directory is gitignored, so the vector is *frozen here* (the same
#: pattern the recovered geometry uses) and re-read from the run when that run
#: is still on this machine.  The reader state is the connection's own: the
#: stub's log gives key_before 0xAEC6FB95 for this packet (the key after the
#: C2S 725 that preceded it, whose own line records key_after = 0xAEC6FB95) and
#: the sequence the connection was expecting (250, one past that packet's 249).
BATTLE_START_BUFFER_HEX = (
    "d0 fb 6f 95 95 fb c6 ae c3 fb c6 ae 95 fb c6 ae 20 a9 f8 40 95 fb c6 ae "
    "00 00 00 40 95 fb c6 ae 00 00 00 00 95 fb c6 ae 00 00 f0 3f 95 fb c6 ae "
    "00 00 00 00 95 fb c6 ae 00 00 00 00 95 fb c6 ae 00 00 00 00 95 fb c6 ae "
    "00 00 00 00 95 fb c6 ae 00 00 00 00 95 fb"
)
BATTLE_START_KEY_BEFORE = 0xAEC6FB95
BATTLE_START_SEQUENCE = 250
BATTLE_START_FIELDS = (False, 0, 86, 101010.0, 2.0, 0.0, 1.0,
                       0.0, 0.0, 0.0, 0.0, 0.0, b"")

#: TASK-0140 / TASK-0182.  The 19 values of the reply to the vector above, in
#: wire order.  Positions 1 (`sfStartTime`) and 7 (`dSyncCount`) are minted per
#: reply by the server (one shared counter), so they are spelled `None` here and
#: filled in by `expected_battle_start_reply`; every other value is fixed by the
#: profile's BattleStartReply policy: three branch/constant choices, the packaged
#: `pveOrPvpType`, the archive's fixed seed, and the request's own values echoed
#: back (`p1..p9`, `s1`).
BATTLE_START_REPLY_FIELDS = (
    None,
    False, False, 1, 86, 3364, None,
    101010.0, 2.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    b"", b"", b"",
)

#: The exact wire length of `NyyNNNNpppppppppaaa` with an empty `s1`/`s2`/`s3`:
#: 3 header + 4 + 1 + 1 + 4 + 4 + 4 + 4 (positions 1-7) + 9*8 (p1..p9) + 3*(2+0).
BATTLE_START_REPLY_WIRE_LENGTH = 103


def expected_battle_start_reply(sf_start_time: int) -> tuple[object, ...]:
    """The 19 reply values for this archive's request vector.

    TASK-0182: position 7 is the same minted counter as position 1, so the
    request's own `dSyncCount` (always 0 on the shipped client) no longer makes a
    second battle look like a duplicate.
    """

    return (
        (sf_start_time,)
        + BATTLE_START_REPLY_FIELDS[1:6]
        + (sf_start_time,)
        + BATTLE_START_REPLY_FIELDS[7:]
    )


class RecoveredBattleStartVectorTests(unittest.TestCase):
    """The one packet RUN-0040's device run really produced, and what it licenses.

    D-13: the wire is frozen in a test, and "the client did not crash" is not an
    oracle.  Here the client *did* crash the connection (the stub refused the id
    and dropped it), so the only honest test is the one that shows the packet
    splits exactly -- boundary, sequence and every field, five orders of
    magnitude apart on the two ends of the payload -- under the registration
    this task added.
    """

    def reader(self) -> ReaderState:
        return ReaderState(key=BATTLE_START_KEY_BEFORE, sequence=BATTLE_START_SEQUENCE)

    def test_the_registered_format_splits_the_run_s_own_packet(self) -> None:
        data = bytes.fromhex(BATTLE_START_BUFFER_HEX)
        self.assertEqual(len(data), 86)
        state = self.reader()
        decoded = decode_packet(data, C2S_REGISTRY, state)
        self.assertEqual((decoded.spec.protocol_id, decoded.spec.name),
                         (69, "netEnterPveBattle"))
        self.assertEqual(decoded.spec.fmt, "yNNpppppppppa")
        self.assertEqual(decoded.fields, BATTLE_START_FIELDS)
        # The frame ends exactly at the buffer: field count, field sizes and the
        # trailing empty string are all consistent, and nothing is left over.
        self.assertEqual(decoded.consumed, len(data))
        # ... and the connection kept advancing as a split stream must.
        self.assertEqual(decoded.sequence, BATTLE_START_SEQUENCE)
        self.assertEqual(decoded.key_before, BATTLE_START_KEY_BEFORE)
        self.assertEqual((state.sequence, state.key),
                         ((BATTLE_START_SEQUENCE + 1) & 0xFF, decoded.key_after))

    def test_the_battle_start_is_recognisable_by_its_own_values(self) -> None:
        """The three fields a reader has to trust: what is being started, where,
        and that this is a first attempt rather than a retry."""

        decoded = decode_packet(bytes.fromhex(BATTLE_START_BUFFER_HEX),
                                C2S_REGISTRY, self.reader())
        is_retry, sync_count, pve_type = decoded.fields[0], decoded.fields[1], decoded.fields[2]
        fight_id, index, _, chapter_id = decoded.fields[3:7]
        self.assertIs(is_retry, False)
        self.assertEqual(sync_count, 0)
        self.assertEqual(pve_type, 86)      # pkgBattleConst.PVETYPE_GUANKA_CHAPTER_MAP
        self.assertEqual((fight_id, index, chapter_id), (101010.0, 2.0, 1.0))

    def test_the_frozen_bytes_are_the_run_s_bytes(self) -> None:
        """Re-read the run when it is still on this machine -- never a dependency."""

        run = Path(__file__).resolve().parents[2] / ".autopilot" / "device_runs" / "RUN-0040"
        log = run / "server.log"
        if not log.is_file():
            self.skipTest("RUN-0040 is not on this machine")
        import json

        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("message") == "unknown protocol id 69":
                self.assertEqual(event["protocol_id"], 69)
                self.assertEqual(event["raw_buffer_length"], 86)
                self.assertEqual(event["raw_header_hex"], "d0 fb 6f")
                self.assertEqual(event["raw_buffer_hex"], BATTLE_START_BUFFER_HEX)
                return
        self.fail("RUN-0040's own record no longer carries the id-69 packet")


#: TASK-0148.  RUN-0045's own bytes for C2S 232, copied verbatim out of that
#: run's stub record -- `.autopilot/device_runs/RUN-0045/server.log:421`, the
#: packet the client sent 6.7 s after the S2C 89 battle start, which the stub
#: refused as an unknown id and answered with a disconnect:
#:
#:     {"classification": "malformed_packet", "connection_id": "conn-000002",
#:      "event": "tcp_error", "message": "unknown protocol id 232",
#:      "protocol_id": 232, "raw_buffer_hex": "<RAW_BUFFER_HEX>",
#:      "raw_buffer_length": 7, "raw_header_hex": "dd dc be", ...}
#:
#: The run directory is gitignored, so the vector is frozen here (the same
#: pattern the C2S 69 vector above uses) and re-read from the run when that run is
#: still on this machine.  The reader state is that connection's own: the header
#: `dd dc be` only names protocol 232 and sequence 139 under the key the previous
#: packet left behind (server.log:419-420, `key_after` 0x243ADC35), and 139 is
#: exactly the sequence the connection was expecting -- so the frame is aligned.
GUIDE_TASK_RUN0045_BUFFER_HEX = "dd dc be 95 d3 3a 24"
GUIDE_TASK_RUN0045_HEADER_HEX = "dd dc be"
GUIDE_TASK_RUN0045_KEY_BEFORE = 0x243ADC35
GUIDE_TASK_RUN0045_SEQUENCE = 139
#: The guide id the 4-byte `N` payload decodes to: `95 d3 3a 24` xor the key's
#: little-endian bytes `35 dc 3a 24` = `a0 0f 00 00`.
GUIDE_TASK_RUN0045_GUIDE_ID = 4000
#: The reader key this packet advances the connection to, frozen because the
#: next packet on that stream is decoded with it.
GUIDE_TASK_RUN0045_KEY_AFTER = 0xD0306DE4


class RecoveredGuideTaskVectorTests(unittest.TestCase):
    """The one C2S 232 packet RUN-0045 really produced, and what it licenses.

    TASK-0026 §3.3 recorded `c2s.finishGuideTask 'N'` as a latent send with no
    lock partner; RUN-0045 is its first device observation.  What the wire has to
    show is that the frame splits exactly -- header, sequence and the one uint32
    field -- under the registration this task added, so the stub can decode and
    record it instead of dropping the connection.
    """

    def reader(self) -> ReaderState:
        return ReaderState(key=GUIDE_TASK_RUN0045_KEY_BEFORE,
                           sequence=GUIDE_TASK_RUN0045_SEQUENCE)

    def test_the_registered_format_splits_the_run_s_own_packet(self) -> None:
        data = bytes.fromhex(GUIDE_TASK_RUN0045_BUFFER_HEX)
        self.assertEqual(len(data), 7)
        state = self.reader()
        decoded = decode_packet(data, C2S_REGISTRY, state)
        self.assertEqual((decoded.spec.protocol_id, decoded.spec.name),
                         (232, "finishGuideTask"))
        self.assertEqual(decoded.spec.fmt, "N")
        self.assertEqual(decoded.fields, (GUIDE_TASK_RUN0045_GUIDE_ID,))
        # The frame ends exactly at the buffer: one 4-byte field after the 3-byte
        # header, nothing left over.
        self.assertEqual(decoded.consumed, len(data))
        self.assertEqual(decoded.sequence, GUIDE_TASK_RUN0045_SEQUENCE)
        self.assertEqual(decoded.key_before, GUIDE_TASK_RUN0045_KEY_BEFORE)
        self.assertEqual(decoded.key_after, GUIDE_TASK_RUN0045_KEY_AFTER)
        self.assertEqual((state.sequence, state.key),
                         ((GUIDE_TASK_RUN0045_SEQUENCE + 1) & 0xFF,
                          GUIDE_TASK_RUN0045_KEY_AFTER))

    def test_the_id_and_sequence_only_decode_under_that_connection_s_key(self) -> None:
        """The header is the licence to call this id 232, so the arithmetic is
        asserted rather than the literal: `dd dc` little-endian xor key low word,
        and `be` xor key low byte."""

        header = bytes.fromhex(GUIDE_TASK_RUN0045_HEADER_HEX)
        key = GUIDE_TASK_RUN0045_KEY_BEFORE
        self.assertEqual(
            int.from_bytes(header[:2], "little") ^ (key & 0xFFFF), 232
        )
        self.assertEqual(header[2] ^ (key & 0xFF), GUIDE_TASK_RUN0045_SEQUENCE)
        # ... and the previous packet of that connection is what left the key
        # behind (server.log:419-420: sequence 138, key_after 0x243ADC35).
        self.assertEqual(GUIDE_TASK_RUN0045_SEQUENCE, 138 + 1)

    def test_the_frozen_bytes_are_the_run_s_bytes(self) -> None:
        """Re-read the run when it is still on this machine -- never a dependency."""

        run = Path(__file__).resolve().parents[2] / ".autopilot" / "device_runs" / "RUN-0045"
        log = run / "server.log"
        if not log.is_file():
            self.skipTest("RUN-0045 is not on this machine")
        import json

        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("message") == "unknown protocol id 232":
                self.assertEqual(event["protocol_id"], 232)
                self.assertEqual(event["raw_buffer_length"], 7)
                self.assertEqual(event["raw_header_hex"], GUIDE_TASK_RUN0045_HEADER_HEX)
                self.assertEqual(event["raw_buffer_hex"], GUIDE_TASK_RUN0045_BUFFER_HEX)
                return
        self.fail("RUN-0045's own record no longer carries the id-232 packet")

    def test_the_format_is_the_recovered_one_not_a_fixed_id_list(self) -> None:
        spec = C2S_REGISTRY.by_id(232)
        # 'N' is a fixed 4-byte field: 3-byte header + 4-byte payload.
        for guide_id in (4000, 0, 1, 0xFFFFFFFF):
            with self.subTest(guide_id=guide_id):
                encoded = encode_packet(spec, (guide_id,), WriterState())
                self.assertEqual(len(encoded.wire), 7)
                decoded = decode_packet(encoded.wire, C2S_REGISTRY, ReaderState())
                self.assertEqual(decoded.fields, (guide_id,))
                self.assertEqual(decoded.consumed, 7)
        # An incomplete packet is never guessed, and the id stays unknown to a
        # registry without the registration -- registering 232 must not have
        # widened the D-08 unknown-id path.
        full = encode_packet(spec, (GUIDE_TASK_RUN0045_GUIDE_ID,), WriterState())
        for length in (3, 6):
            with self.subTest(truncated_to=length):
                with self.assertRaises(NeedMoreData):
                    decode_packet(full.wire[:length], C2S_REGISTRY, ReaderState())
        without_guide = ProtocolRegistry("C2S")
        with self.assertRaises(UnknownProtocol):
            decode_packet(full.wire, without_guide, ReaderState())

    def test_no_s2c_answers_the_guide_task(self) -> None:
        # No registerS2C names finishGuideTask as a lock partner, so nothing may
        # be sent back for it (the C2S 233 finishStory shape).
        self.assertNotIn("finishGuideTask", {spec.name for spec in S2C_REGISTRY})


#: TASK-0155.  The one C2S 67 packet RUN-0048 really produced
#: (`.autopilot/device_runs/RUN-0048/server.log:439`):
#:
#:     {"classification": "malformed_packet", "event": "tcp_error",
#:      "message": "unknown protocol id 67", "protocol_id": 67,
#:      "raw_buffer_hex": "<RAW_BUFFER_HEX>", "raw_buffer_length": 245,
#:      "raw_header_hex": "b0 7f 67", ...}
#:
#: The run directory is gitignored, so the vector is frozen here (the same
#: pattern the C2S 69 / C2S 232 vectors use) and re-read from the run when that run
#: is still on this machine.  The reader state is that connection's own: the
#: header `b0 7f 67` only names protocol 67 and sequence 148 under the key the
#: previous packet left behind (server.log:437's `key_after` 0x56BB7FF3), and 148
#: is exactly the sequence the connection was expecting (the sequence-147
#: speedHeart was the packet before it) -- so the frame is aligned.
BATTLE_GAME_OVER_RUN0048_BUFFER_HEX = (
    "b0 7f 67 a5 7f bb 56 f3 7f bb 56 f3 "
    "7f bb 56 5d 90 57 41 f3 7f bb 16 2b "
    "a0 58 41 f3 7f bb 56 07 9e 4f 41 f3 "
    "7f bb 16 3c fb 5c 41 f3 7f bb d6 d5 "
    "2e 42 41 f3 7f bb 16 48 49 53 41 f3 "
    "7f bb d6 5c b5 52 41 f3 7f bb d6 6c "
    "38 53 41 f3 7f bb d6 d0 60 41 41 f3 "
    "7f bb 56 b0 98 50 41 f3 7f bb 56 50 "
    "38 50 41 f3 7f bb d6 46 99 57 41 f3 "
    "7f f3 7f f3 7f 99 7f c1 53 8a 7a c3 "
    "53 8b 7a c3 03 8a 09 c2 4f 8a 09 c1 "
    "47 8d 09 c2 4b 8b 09 c3 20 8a 09 c2 "
    "20 8a 67 c3 4f 8a 09 ad 21 c7 66 ac "
    "4e 8b 67 ac 4f e4 67 c5 47 e4 67 ac "
    "4f e4 67 ac 4c 8b 62 c4 46 97 65 c3 "
    "4d 8d 67 ac 44 8b 09 c2 4f 8f 67 c4 "
    "20 8a 62 c3 20 8a 67 cb 20 8a 09 c3 "
    "20 8a 09 c0 4f 8a 66 c7 53 88 66 c2 "
    "4b 8f 09 ad 21 f3 7f bb 56 b2 df 49 "
    "41 f3 7f bb 16 0d 31 56 41 f3 7f bb "
    "96 fe d9 52 41"
)
BATTLE_GAME_OVER_RUN0048_HEADER_HEX = "b0 7f 67"
BATTLE_GAME_OVER_RUN0048_KEY_BEFORE = 0x56BB7FF3
BATTLE_GAME_OVER_RUN0048_SEQUENCE = 148
#: The reader key this packet advances the connection to -- the key the NEXT
#: packet on that stream would have been decoded with had the stub not closed it.
BATTLE_GAME_OVER_RUN0048_KEY_AFTER = 0x7B2178B2
#: The 21 values the 245-byte buffer decodes to under `NNppppppppppppaaaappp`.
#: Value 1 is `pkgBattleConst.PVETYPE_GUANKA_CHAPTER_MAP` (86) and value 2 is the
#: client's `tbDataInc.tbExtra.dPackCheckFlag` (0) -- exactly the two leading
#: arguments its own call site passes before `unpack(encryptedResult)`.
BATTLE_GAME_OVER_RUN0048_FIELDS = (
    86, 0,
    6177140.0, 6455469.0, 4144142.0, 7597297.0, 2383275.0, 5055777.0,
    4904306.0, 5038514.0, 2277793.0, 4350656.0, 4251968.0, 6186266.0,
    b"", b"", b"",
    b"2,1,0,0,0|1_101_286_140_0_1_1_11001_^^|0_101_0_168_1_0_1_30479,30261_;"
    b"0_10417_140_118_1_0_1_30104,30144_^^",
    3391332.0, 5817397.0, 4941819.0,
)


class RecoveredBattleGameOverVectorTests(unittest.TestCase):
    """The one C2S 67 packet RUN-0048 really produced, and what it licenses.

    RUN-0048 is the first device observation of this id: the client settled its
    own local battle, sent this packet, and the stub -- which had no registration
    for it -- answered `unknown protocol id 67` and closed the connection
    (server.log:439/440).  What the wire has to show is that the frame splits
    exactly -- header, sequence and all 21 values -- under the registration this
    task added, so the stub can answer the lock-send instead of dropping the
    connection.
    """

    def reader(self) -> ReaderState:
        # `ReaderState.sequence` is the NEXT expected sequence, which is why it is
        # 148 here although the packet itself carries 148 too: the reader compares
        # the decoded byte against this field.
        return ReaderState(key=BATTLE_GAME_OVER_RUN0048_KEY_BEFORE,
                           sequence=BATTLE_GAME_OVER_RUN0048_SEQUENCE)

    def test_the_registered_format_splits_the_run_s_own_packet(self) -> None:
        data = bytes.fromhex(BATTLE_GAME_OVER_RUN0048_BUFFER_HEX)
        self.assertEqual(len(data), 245)
        state = self.reader()
        decoded = decode_packet(data, C2S_REGISTRY, state)
        self.assertEqual((decoded.spec.protocol_id, decoded.spec.name),
                         (67, "battlePveGameOver"))
        self.assertEqual(decoded.spec.fmt, "NNppppppppppppaaaappp")
        self.assertEqual(decoded.fields, BATTLE_GAME_OVER_RUN0048_FIELDS)
        # The frame ends exactly at the buffer: nothing left over and nothing
        # missing -- which is the whole reason the format can be called computed
        # rather than guessed.
        self.assertEqual(decoded.consumed, len(data))
        self.assertEqual(decoded.consumed, 245)
        self.assertEqual(decoded.sequence, BATTLE_GAME_OVER_RUN0048_SEQUENCE)
        self.assertEqual(decoded.key_before, BATTLE_GAME_OVER_RUN0048_KEY_BEFORE)
        self.assertEqual(decoded.key_after, BATTLE_GAME_OVER_RUN0048_KEY_AFTER)
        self.assertEqual((state.sequence, state.key),
                         ((BATTLE_GAME_OVER_RUN0048_SEQUENCE + 1) & 0xFF,
                          BATTLE_GAME_OVER_RUN0048_KEY_AFTER))

    def test_the_id_and_sequence_only_decode_under_that_connection_s_key(self) -> None:
        """The header is the licence to call this id 67, so the arithmetic is
        asserted rather than the literal: `b0 7f` little-endian xor key low word,
        and `67` xor key low byte."""

        header = bytes.fromhex(BATTLE_GAME_OVER_RUN0048_HEADER_HEX)
        key = BATTLE_GAME_OVER_RUN0048_KEY_BEFORE
        self.assertEqual(
            int.from_bytes(header[:2], "little") ^ (key & 0xFFFF), 67
        )
        self.assertEqual(header[2] ^ (key & 0xFF), BATTLE_GAME_OVER_RUN0048_SEQUENCE)
        # ... and the previous packet of that connection is what left the key
        # behind (server.log:437: sequence 147, key_after 0x56BB7FF3), so the
        # frame is aligned rather than a stream desync.
        self.assertEqual(BATTLE_GAME_OVER_RUN0048_SEQUENCE, 147 + 1)

    def test_the_first_value_is_the_chapter_map_type_the_client_itself_sent(self) -> None:
        """A wrong format would still consume bytes; this is what says it is the
        right one.  Value 1 is the packaged `PVETYPE_GUANKA_CHAPTER_MAP` and value
        2 the client's own `dPackCheckFlag`, i.e. the first two arguments of
        `c2s.battlePveGameOver(e_tbSocket, pveOrPvpType, dPackCheckFlag,
        unpack(...))` -- and the `_svrVer` sibling (wire 68, format 'NN') would be
        11 bytes, not 245."""

        data = bytes.fromhex(BATTLE_GAME_OVER_RUN0048_BUFFER_HEX)
        decoded = decode_packet(data, C2S_REGISTRY, self.reader())
        self.assertEqual(decoded.fields[0], 86)
        self.assertEqual(decoded.fields[1], 0)
        # The four `a` slots: three empty, one carrying the client's own packed
        # battle record.  Recorded verbatim, never interpreted by the stub.
        self.assertEqual(decoded.fields[14:17], (b"", b"", b""))
        self.assertIn(b"_101_", decoded.fields[17])
        self.assertEqual(len(decoded.fields), len("NNppppppppppppaaaappp"))

    def test_the_frozen_bytes_are_the_run_s_bytes(self) -> None:
        """Re-read the run when it is still on this machine -- never a dependency."""

        run = Path(__file__).resolve().parents[2] / ".autopilot" / "device_runs" / "RUN-0048"
        log = run / "server.log"
        if not log.is_file():
            self.skipTest("RUN-0048 is not on this machine")
        import json

        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("message") == "unknown protocol id 67":
                self.assertEqual(event["protocol_id"], 67)
                self.assertEqual(event["raw_buffer_length"], 245)
                self.assertEqual(event["raw_header_hex"], BATTLE_GAME_OVER_RUN0048_HEADER_HEX)
                self.assertEqual(event["raw_buffer_hex"], BATTLE_GAME_OVER_RUN0048_BUFFER_HEX)
                return
        self.fail("RUN-0048's own record no longer carries the id-67 packet")

    def test_the_format_is_the_recovered_one_not_a_fixed_id_list(self) -> None:
        spec = C2S_REGISTRY.by_id(67)
        # 2 'N' (4 bytes each) + 12 'p' (8 each) + 4 'a' (2 each, empty) + 3 'p'
        # (8 each) + the 3-byte header = 139 bytes for the all-empty case.
        empty = (86, 0) + (0.0,) * 12 + (b"",) * 4 + (0.0,) * 3
        encoded = encode_packet(spec, empty, WriterState())
        self.assertEqual(len(encoded.wire), 139)
        decoded = decode_packet(encoded.wire, C2S_REGISTRY, ReaderState())
        self.assertEqual(decoded.fields, empty)
        self.assertEqual(decoded.consumed, 139)
        # A truncated buffer is never guessed, and the id stays unknown to a
        # registry without the registration -- registering 67 must not have
        # widened the D-08 unknown-id path.
        full = encode_packet(spec, BATTLE_GAME_OVER_RUN0048_FIELDS, WriterState())
        for length in (3, 100, len(full.wire) - 1):
            with self.subTest(truncated_to=length):
                with self.assertRaises(NeedMoreData):
                    decode_packet(full.wire[:length], C2S_REGISTRY, ReaderState())
        without_game_over = ProtocolRegistry("C2S")
        with self.assertRaises(UnknownProtocol):
            decode_packet(full.wire, without_game_over, ReaderState())

    def test_the_unregistered_neighbours_stay_unknown(self) -> None:
        """D-18: only the row RUN-0048 actually sent is registered.  The
        `_svrVer` sibling would assert a server-driven battle this archive has no
        producer for, and S2C 97 makes the client dispatch FIGHT_ERROR_RETURN
        instead of settling -- a visibly different outcome."""

        with self.assertRaises(KeyError):
            C2S_REGISTRY.by_id(68)
        with self.assertRaises(KeyError):
            S2C_REGISTRY.by_id(97)
        self.assertNotIn("battlePveGameOver_svrVer",
                         {spec.name for spec in C2S_REGISTRY})
        self.assertNotIn("battlePveGameOverError",
                         {spec.name for spec in S2C_REGISTRY})

    def test_s2c_96_is_the_registered_answer_and_round_trips(self) -> None:
        spec = S2C_REGISTRY.by_id(96)
        self.assertEqual(spec.name, "battlePveGameOverEnd")
        self.assertEqual(spec.fmt, "Na")
        encoded = encode_packet(spec, (0, b""), WriterState())
        # 3-byte header + one 4-byte 'N' + the 2-byte length prefix of the empty
        # string (an `a` slot always carries its own length).
        self.assertEqual(len(encoded.wire), 9)
        decoded = decode_packet(encoded.wire, S2C_REGISTRY, ReaderState())
        self.assertEqual(decoded.fields, (0, b""))
        self.assertEqual(decoded.consumed, 9)


class BattleGameOverReplyVectorTests(unittest.TestCase):
    """TASK-0155: the 2-value answer to that packet, encoded and read back.

    The request vector above is a real device capture; the reply has never been on
    a wire (S2C 96 is a derived id), so what can be frozen is the shape the profile
    declares, the arithmetic that keeps it inside the recovered format, and the
    consumer verdict that licenses both constants.
    """

    def policy(self):
        policy = get_profile().battle_game_over_reply
        assert policy is not None
        return policy

    def test_the_profile_assembles_the_2_values_without_the_request(self) -> None:
        policy = self.policy()
        fields = policy.reply_fields()
        self.assertEqual(fields, (0, b""))
        self.assertEqual(len(fields), 2)
        # The signature takes no request argument at all: nothing in the reply is
        # derived from what the client sent, so a later slice cannot start
        # consuming one here without changing this contract on purpose.
        self.assertEqual(
            list(inspect.signature(policy.reply_fields).parameters), []
        )

    def test_the_registered_format_round_trips_and_consumes_the_buffer(self) -> None:
        policy = self.policy()
        spec = S2C_REGISTRY.by_id(96)
        self.assertEqual(spec.fmt, policy.reply_format)
        encoded = encode_packet(spec, policy.reply_fields(), WriterState())
        decoded = decode_packet(encoded.wire, S2C_REGISTRY, ReaderState())
        self.assertEqual(decoded.fields, policy.reply_fields())
        self.assertEqual(decoded.consumed, len(encoded.wire))
        self.assertEqual([field.wire_type for field in policy.fields],
                         list(policy.reply_format))

    def test_the_verdict_is_a_sentence_and_it_holds(self) -> None:
        """The gate this whole slice rests on, re-checked against the frozen
        vector rather than against prose: nothing in the reply may be a function of
        the request, and the profile has to say out loud that the chapter branch
        reads neither slot."""

        policy = self.policy()
        for field in policy.fields:
            self.assertEqual(field.provenance.origin, "synthetic_compatibility")
            self.assertTrue(field.provenance.reason)
            # Every value that is not read cannot be 'recovered', and none of
            # them may claim to be.
            self.assertNotIn(field.provenance.origin,
                             {"deterministic_recovered", "official_recovered",
                              "request_echo"})
            self.assertTrue(field.provenance.replaceable)
            self.assertIn("never names it", field.provenance.field_contract)

    def test_only_v12_carries_the_policy(self) -> None:
        self.assertIsNotNone(get_profile_v12().battle_game_over_reply)
        self.assertIsNone(get_profile_v11().battle_game_over_reply)
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9,
                        get_profile_v10):
            with self.subTest(profile=builder().profile_id):
                self.assertIsNone(builder().battle_game_over_reply)


class PrisonFightSnapshotVectorTests(unittest.TestCase):
    """TASK-0167: the 11-value login snapshot, encoded and read back.

    ``synPrisonFightBossToCg`` has never been on a wire (S2C 1370 is a derived
    id), so what can be frozen is the shape the profile declares, the arithmetic
    that keeps it inside the recovered format, and the one semantic the values
    carry: a closed activity (startTime/endTime = 0).  Every value is labelled
    ``synthetic_compatibility``; none may claim a recovery.
    """

    def policy(self):
        policy = get_profile().prison_fight
        assert policy is not None
        return policy

    def test_the_profile_assembles_the_11_values_without_a_request(self) -> None:
        policy = self.policy()
        self.assertEqual(
            policy.snapshot_fields(),
            (0, b"", 0.0, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0),
        )
        self.assertEqual(len(policy.snapshot_fields()), 11)
        # The signature takes no request argument at all: nothing in the snapshot
        # is derived from what the client sent.
        self.assertEqual(
            list(inspect.signature(policy.snapshot_fields).parameters), []
        )

    def test_the_registered_format_round_trips_and_consumes_the_buffer(self) -> None:
        policy = self.policy()
        spec = S2C_REGISTRY.by_id(1370)
        self.assertEqual((spec.name, spec.fmt),
                         ("synPrisonFightBossToCg", policy.snapshot_format))
        encoded = encode_packet(spec, policy.snapshot_fields(), WriterState())
        # 3 header + N(4) + a(2+0) + p(8) + p(8) + N(4) + N(4) + 5*p(40) = 73.
        self.assertEqual(len(encoded.wire), 73)
        decoded = decode_packet(encoded.wire, S2C_REGISTRY, ReaderState())
        self.assertEqual(decoded.fields, policy.snapshot_fields())
        self.assertEqual(decoded.consumed, len(encoded.wire))
        self.assertEqual([field.wire_type for field in policy.fields],
                         list(policy.snapshot_format))

    def test_every_value_says_it_is_a_compatibility_constant(self) -> None:
        policy = self.policy()
        for field in policy.fields:
            self.assertEqual(field.provenance.origin, "synthetic_compatibility")
            self.assertEqual(
                field.provenance.compatibility_version, PROFILE_ID_V13
            )
            self.assertTrue(field.provenance.reason)
            self.assertTrue(field.provenance.replaceable)
            self.assertNotIn(
                field.provenance.origin,
                {"deterministic_recovered", "official_recovered", "request_echo"},
            )
        self.assertIs(policy.historical_values_recovered, False)
        self.assertEqual(policy.historical_ordering, "unknown")

    def test_only_v13_carries_the_policy(self) -> None:
        self.assertIsNotNone(get_profile().prison_fight)
        self.assertIsNone(get_profile_v12().prison_fight)
        for builder in (get_profile_v1, get_profile_v2, get_profile_v3,
                        get_profile_v4, get_profile_v5, get_profile_v6,
                        get_profile_v7, get_profile_v8, get_profile_v9,
                        get_profile_v10, get_profile_v11):
            with self.subTest(profile=builder().profile_id):
                self.assertIsNone(builder().prison_fight)


class BattleStartReplyVectorTests(unittest.TestCase):
    """TASK-0140: the 19-value answer to that packet, encoded and read back.

    The request vector above is a real device capture; the reply has never been
    on a wire (S2C 89 is a derived id), so what can be frozen is the *shape* the
    profile declares and the arithmetic that keeps it inside the recovered
    format -- a round trip that consumes the buffer exactly, and a truncated
    buffer that is refused rather than half-read.
    """

    def policy(self):
        policy = get_profile().battle_start_reply
        assert policy is not None
        return policy

    def test_the_profile_assembles_the_19_values_for_this_request(self) -> None:
        policy = self.policy()
        fields = policy.reply_fields(
            request_fields=BATTLE_START_FIELDS, sf_start_time=1_700_000_001
        )
        self.assertEqual(len(fields), 19)
        self.assertEqual(fields, expected_battle_start_reply(1_700_000_001))
        # Position 7 is the same minted counter, not the request's `dSyncCount`.
        self.assertEqual(fields[6], 1_700_000_001)
        self.assertNotEqual(fields[6], BATTLE_START_FIELDS[1])
        # The echoed half is the request's own values, slot for slot.
        self.assertEqual(fields[7:16], BATTLE_START_FIELDS[3:12])    # p1..p9
        self.assertEqual(fields[16], BATTLE_START_FIELDS[12])        # s1 <- a1

    def test_the_registered_format_round_trips_and_consumes_the_buffer(self) -> None:
        policy = self.policy()
        spec = S2C_REGISTRY.by_id(89)
        self.assertEqual((spec.name, spec.fmt), ("startPveBattle", policy.reply_format))
        fields = policy.reply_fields(
            request_fields=BATTLE_START_FIELDS, sf_start_time=1_700_000_001
        )
        written = encode_packet(spec, fields, WriterState())
        self.assertEqual(len(written.wire), BATTLE_START_REPLY_WIRE_LENGTH)
        decoded = decode_packet(written.wire, S2C_REGISTRY, ReaderState())
        self.assertEqual(decoded.spec.protocol_id, 89)
        self.assertEqual(decoded.fields, expected_battle_start_reply(1_700_000_001))
        # Same arithmetic the 86-byte C2S 69 vector is checked with: the field
        # count, the field sizes and the trailing empty strings are consistent,
        # and not one byte is left over.
        self.assertEqual(decoded.consumed, len(written.wire))
        self.assertEqual(decoded.key_before, INITIAL_KEY)

    def test_a_truncated_reply_is_refused_not_half_read(self) -> None:
        policy = self.policy()
        fields = policy.reply_fields(
            request_fields=BATTLE_START_FIELDS, sf_start_time=1_700_000_001
        )
        wire = encode_packet(S2C_REGISTRY.by_id(89), fields, WriterState()).wire
        # Cutting anywhere leaves the stream unable to know where the packet
        # ends -- so every truncation must raise instead of returning a short read.
        for cut in (3, 25, 97, len(wire) - 1):
            with self.subTest(cut=cut):
                with self.assertRaises(NeedMoreData):
                    decode_packet(wire[:cut], S2C_REGISTRY, ReaderState())

    def test_the_reply_values_come_only_from_the_profile(self) -> None:
        """Every position names its own source, and the two that must not move do not."""

        policy = self.policy()
        origins = policy.field_origins()
        self.assertEqual(origins["pveOrPvpType"], "deterministic_recovered")
        self.assertEqual(origins["sfStartTime"], "synthetic_compatibility")
        self.assertEqual(origins["isUseSfSvr"], "synthetic_compatibility")
        self.assertEqual(origins["(unnamed)"], "synthetic_compatibility")
        self.assertEqual(origins["selfGroup"], "synthetic_compatibility")
        self.assertEqual(origins["dRawRandomSeed"], "synthetic_compatibility")
        self.assertEqual(origins["s2"], "synthetic_compatibility")
        self.assertEqual(origins["s3"], "synthetic_compatibility")
        # TASK-0182: `dSyncCount` is a labelled compatibility value now (counter),
        # so its PROVENANCE is synthetic_compatibility, not request_echo.
        self.assertEqual(origins["dSyncCount"], "synthetic_compatibility")
        for name in ("p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8",
                     "p9", "s1"):
            self.assertEqual(origins[name], "request_echo", name)
        # The value origins are a separate map: provenance says what backs a
        # position, this says where its wire value comes from.
        value_origins = policy.value_origins()
        self.assertEqual(value_origins["sfStartTime"], "reply_counter")
        self.assertEqual(value_origins["dSyncCount"], "reply_counter")
        self.assertEqual(value_origins["pveOrPvpType"], "compatibility_constant")
        self.assertEqual(value_origins["p1"], "request_echo")
        self.assertEqual(value_origins["s1"], "request_echo")
        # The two branch values TASK-0139 proved must be false for the battle to
        # be playable at all.
        fields = policy.reply_fields(
            request_fields=BATTLE_START_FIELDS, sf_start_time=1
        )
        self.assertIs(fields[1], False)
        self.assertIs(fields[2], False)


if __name__ == "__main__":
    unittest.main()

