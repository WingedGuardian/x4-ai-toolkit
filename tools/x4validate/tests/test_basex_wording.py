"""The shipped BaseX-cache wording agrees with the user's rule (2026-10-30, restated 2026-11-26).

x4raw / x4eff go through BaseX's native DROP DB, never backed up or recycled first. Generated
staging/merged trees and stale cache licences go through scripts/x4basex_artifacts.py, which
only touches producer-receipted or known generated slots. Unknown or unreceipted things stay.
"""
from __future__ import annotations

import re

from _layout import ROOT, require_repo


def _text(rel: str) -> str:
    require_repo(rel, why="shipped wording")
    return (ROOT / rel).read_bytes().decode("utf-8")


def test_core_does_not_forbid_the_artifact_script():
    t = " ".join(_text("agent/instructions/core.md").split())
    assert "never run `scripts/x4basex_artifacts.py` by hand" not in t


def test_core_names_the_whole_rule():
    t = " ".join(_text("agent/instructions/core.md").split())
    assert "DROP DB" in t and "x4basex_artifacts.py" in t
    assert re.search(r"licen[cs]es", t), "stale cache licences are part of the rule"
    assert re.search(r"never (backed up|backup)|no backup|without (a )?backup", t, re.I)


def test_f311_is_marked_superseded_without_rewriting_the_old_text():
    t = _text("tools/x4validate/docs/BLIND-SPOTS.md")
    m = re.search(r"^## F311 .*?(?=^## F312 )", t, re.S | re.M)
    assert m, "F311 section not found"
    sec = m.group(0)
    # The old sentence is kept verbatim (a dated record, not rewritten)...
    assert "The user explicitly included regenerable BaseX output in recoverable removal." in sec
    # ...and a dated note follows it, naming the current rule.
    i = sec.index("SUPERSEDED 2026-11-26")
    assert i > sec.index("The user explicitly included regenerable BaseX output")
    note = sec[i:]
    assert "DROP DB" in note and "x4basex_artifacts.py" in note and "RETENTION.md" in note


def test_f320_recycle_before_drop_db_is_marked_superseded():
    """F320's MEASURED paragraph describes recycling the old database before DROP DB; the builders no
    longer do (4.3.0). The old text stays, with a dated note after it."""
    t = _text("tools/x4validate/docs/BLIND-SPOTS.md")
    m = re.search(r"^## F320 .*?(?=^## F321 )", t, re.S | re.M)
    assert m, "F320 section not found"
    sec = m.group(0)
    assert "successful fake-bin recycling precedes DROP DB" in sec
    assert sec.index("SUPERSEDED 2026-11-26") > sec.index("successful fake-bin recycling precedes DROP DB")
    assert "DROP DB" in sec[sec.index("SUPERSEDED 2026-11-26"):]
