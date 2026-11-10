"""TASK-0340/0341 -- the uncharted battle reply's enemy HP table.

The client decodes S2C 89 position 18 as tbSet[i] = clamp(v/7 - 25486 - i, 0, 10000), so the wire
string is "index_encoded;..." with encoded = (hp*10000 + 25486 + index) * 7. The type-66 comma
form is fatal there (pkgBattleStatistics:461), which aborted RUN40.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import dev_play_uncharted as dpu  # noqa: E402

#: The exact strings the TASK-0340 contract recovered from the shipped client chunk.
CONTRACT = {
    0.25: "1_195909;2_195916;3_195923;4_195930;5_195937",
    0.50: "1_213409;2_213416;3_213423;4_213430;5_213437",
    1.00: "1_248409;2_248416;3_248423;4_248430;5_248437",
}


class HpPercentEncodingTests(unittest.TestCase):
    def test_the_salt_is_the_shipped_constant(self):
        self.assertEqual(dpu.HP_PERCENT_SALT, 25486)
        self.assertEqual(dpu.HP_PERCENT_SCALE, 7)

    def test_the_three_contract_strings_are_reproduced_exactly(self):
        for percent, expected in CONTRACT.items():
            with self.subTest(percent=percent):
                self.assertEqual(dpu.hp_percent_table(percent), expected)

    def test_the_grammar_is_underscore_pairs(self):
        value = dpu.hp_percent_table(0.25)
        self.assertNotIn(",", value, "a comma row aborts the uncharted parser with :461")
        pairs = value.split(";")
        self.assertEqual(len(pairs), 5)
        for index, pair in enumerate(pairs, start=1):
            left, right = pair.split("_")
            self.assertEqual(int(left), index)
            self.assertGreater(int(right), dpu.HP_PERCENT_SALT * dpu.HP_PERCENT_SCALE)

    def test_the_stub_sends_it_for_type_88(self):
        # TASK-0345: the stub sends the module constant, so the setting that produced the two
        # device wins (0.0 = "not instanced") is one named value, not a literal at the call site.
        source = (TESTS.parent / "revival_stub" / "tcp_stub.py").read_text(encoding="utf-8")
        self.assertIn("dev_play_uncharted.hp_percent_table(", source)
        self.assertIn("dev_play_uncharted.UNCHARTED_HP_PERCENT", source)
        self.assertEqual(dpu.UNCHARTED_HP_PERCENT, 0.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
