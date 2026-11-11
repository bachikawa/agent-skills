"""Tests that the repository-level copy of LICENSE-UNICODE stays the same.

The skill ships its own LICENSE-UNICODE, because tools such as npx skills
copy only the skill directory. The copy at the repository root is there so
that GitHub shows the license on the top page.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
SKILL = REPOSITORY / "plugins" / "kundoku-bungo" / "skills" / "kundoku-bungo"


class LicenseCopyTest(unittest.TestCase):
    def test_root_copy_of_the_unicode_license_matches_the_skill(self) -> None:
        root = (REPOSITORY / "LICENSE-UNICODE").read_bytes()
        skill = (SKILL / "LICENSE-UNICODE").read_bytes()

        self.assertEqual(root, skill)


if __name__ == "__main__":
    unittest.main()
