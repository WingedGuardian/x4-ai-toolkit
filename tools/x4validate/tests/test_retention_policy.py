"""RETENTION.md's shared section is the same text in the X4 and Skyrim toolkits.

The local pin validates this copy. check-housekeep-parity.py compares actual peer bytes. Instead each pins the hash of the
section between the shared-policy markers. Change the text in one repo and this goes red until the new hash is
pinned -- in BOTH repos (the sibling Skyrim toolkit: tests/test_retention_policy.py, docs/RETENTION.md), with the same
text.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PINNED = "f82b311e309d02f299a0b7f2087d417697d303bb69e020bd946174cd090d9f90"
DOC = REPO / "RETENTION.md"


def shared_section(text: bytes) -> bytes:
    m = re.search(rb"<!-- shared-policy:begin -->\n(.*?)<!-- shared-policy:end -->",
                  text.replace(b"\r\n", b"\n"), re.S)
    assert m, "RETENTION.md lost its shared-policy markers"
    return m.group(1)


def test_the_shared_policy_matches_the_pinned_text_shared_with_the_skyrim_toolkit():
    got = hashlib.sha256(shared_section(DOC.read_bytes())).hexdigest()
    assert got == PINNED, ("the shared retention policy changed ({}). Make the same edit in the sibling Skyrim toolkit "
                           "docs/RETENTION.md, then pin the new hash in both repos.".format(got))


def test_a_one_character_edit_changes_the_hash():
    edited = DOC.read_bytes().replace(b"Forever", b"Forevr", 1)
    assert hashlib.sha256(shared_section(edited)).hexdigest() != PINNED
