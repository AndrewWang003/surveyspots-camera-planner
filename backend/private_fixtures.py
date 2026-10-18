"""Access to private survey files that are not published with this repository.

The historical survey corpus comes from real field projects, so it is kept out
of version control. Tests that need it skip cleanly when it is absent.
"""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTDATA = ROOT / "testdata"
TEST_FIXTURES = ROOT / "testfixtures"


def read_private(path: Path) -> bytes:
    """Return a private survey file, or skip the calling test when it is absent."""
    if not path.is_file():
        raise unittest.SkipTest(f"private survey file not published: {path.name}")
    return path.read_bytes()


def private_corpus(directory: Path = TESTDATA) -> list[Path]:
    """Return every private historical KML, or skip when the corpus is absent."""
    files = sorted(directory.glob("*.kml"))
    if not files:
        raise unittest.SkipTest("private historical survey corpus not published")
    return files
