"""RETENTION.md's shared section is the same text in the X4 and Skyrim toolkits.

The two repos have disjoint histories, so nothing can import one from the other. Instead each pins the hash of the
section between the shared-policy markers. Change the text in one repo and this goes red until the new hash is
pinned -- in BOTH repos (skyrimvr-claude-toolkit: tests/test_retention_policy.py, docs/RETENTION.md), with the same
text.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PINNED = "e89302148024fd266cbd0a5075fea84c6d1f705c49d835c7f7c91e7198cdf3ef"
DOC = REPO / "RETENTION.md"


def shared_section(text: bytes) -> bytes:
    m = re.search(rb"<!-- shared-policy:begin -->\n(.*?)<!-- shared-policy:end -->",
                  text.replace(b"\r\n", b"\n"), re.S)
    assert m, "RETENTION.md lost its shared-policy markers"
    return m.group(1)


def test_the_shared_policy_matches_the_pinned_text_shared_with_the_skyrim_toolkit():
    got = hashlib.sha256(shared_section(DOC.read_bytes())).hexdigest()
    assert got == PINNED, ("the shared retention policy changed ({}). Make the same edit in skyrimvr-claude-toolkit "
                           "docs/RETENTION.md, then pin the new hash in both repos.".format(got))


def test_a_one_character_edit_changes_the_hash():
    edited = DOC.read_bytes().replace(b"Forever", b"Forevr", 1)
    assert hashlib.sha256(shared_section(edited)).hexdigest() != PINNED
