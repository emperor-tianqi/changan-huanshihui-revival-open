"""TASK-0366 -- every S2C name the stub sends must exist in the registry.

The carriage offer (C2S 750) wrote a reasonable reply stream for S2C 1541/1542 that were never
registered, so the send raised KeyError inside the packet path and closed the connection (live
21:1x). This test reads tcp_stub.py, extracts every literal protocol name passed to _send_s2c
and asserts the S2C registry knows it.
"""

import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

sys.path.insert(0, str(TESTS))

from revival_stub.registry import S2C_REGISTRY  # noqa: E402

STUB = ROOT / "revival_stub" / "tcp_stub.py"
SEND_CALL = re.compile(
    r'_send_s2c\(\s*[^,]+,\s*[^,]+,\s*"([A-Za-z][A-Za-z0-9_]*)"', re.S)


class SentNamesAreRegisteredTests(unittest.TestCase):
    def test_the_pattern_finds_the_known_names(self):
        names = set(SEND_CALL.findall(STUB.read_text(encoding="utf-8")))
        self.assertGreater(len(names), 10)
        self.assertIn("returnGainInfoStart", names)
        self.assertIn("artifactDgnSendPath", names)

    def test_every_sent_name_is_registered(self):
        names = sorted(set(SEND_CALL.findall(STUB.read_text(encoding="utf-8"))))
        unknown = []
        for name in names:
            try:
                S2C_REGISTRY.by_name(name)
            except KeyError:
                unknown.append(name)
        self.assertEqual(unknown, [],
                         "sent but never registered: " + ", ".join(unknown))

    def test_the_carriage_replies_are_registered(self):
        self.assertEqual(S2C_REGISTRY.by_id(1541).name, "artifactDgnSelCard")
        self.assertEqual(S2C_REGISTRY.by_id(1541).fmt, "NNaNNNa")
        self.assertEqual(S2C_REGISTRY.by_id(1542).name, "artifactDgnDoSelCard")
        self.assertEqual(S2C_REGISTRY.by_id(1542).fmt, "NN")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()