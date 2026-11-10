"""TASK-0277 -- the normal-play launcher forwards the three dev-play overlays.

python -m revival_stub.play is the entry a human uses to serve a dev-play archive. It used to
build an app namespace without the dev-play flags, so a session started from here applied no
overlay at all and looked like a client that ignored the server.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from revival_stub import play  # noqa: E402


class PlayEntryFlagTests(unittest.TestCase):
    def _namespace(self, *extra):
        args = play.build_parser().parse_args(["dev-artifact-main", "--profile-mode", "resume", *extra])
        return play.to_app_namespace(args)

    def test_flags_default_to_off(self):
        namespace = self._namespace()
        self.assertFalse(namespace.dev_play_artifact)
        self.assertFalse(namespace.dev_play_babel)
        self.assertFalse(namespace.dev_play_resources)
        self.assertEqual(namespace.role_mode, "play_profile")

    def test_each_flag_is_forwarded(self):
        for flag, attribute in (("--dev-play-artifact", "dev_play_artifact"),
                                ("--dev-play-babel", "dev_play_babel"),
                                ("--dev-play-resources", "dev_play_resources")):
            with self.subTest(flag=flag):
                namespace = self._namespace(flag)
                self.assertTrue(getattr(namespace, attribute))

    def test_profile_selection_survives(self):
        namespace = self._namespace("--dev-play-artifact")
        self.assertEqual(namespace.play_profile, "dev-artifact-main")
        self.assertEqual(namespace.profile_mode, "resume")
        self.assertIsNone(namespace.data_dir)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
