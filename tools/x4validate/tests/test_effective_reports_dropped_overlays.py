"""C10: an overlay that could not be READ must not leave the store saying 'base'.

`_merge.MergeResult.skipped` states its own contract: *"overlays that could not be
parsed and were left out of the tree. Without this channel a malformed overlay is
indistinguishable from an absent one, and the resulting tree looks complete when it
is not."* `_check.py` honours it in three places. `_effective._merge_one` returned
`(res.tree, rec)` and dropped it.

⚠ AND THE EXCEPTION HANDLER COULD NOT COVER FOR IT. `build` wraps each `_merge_one`
in `except etree.LxmlError`, which reads like a skip channel -- but `overlay_root`
catches `XMLSyntaxError` internally, so nothing ever escapes for an OVERLAY file.
That handler can only fire for the base file's own `parse_file`. The tests below
therefore go through a real malformed overlay, not a mocked raise: a mocked
exception would exercise the one path that already worked and prove nothing.
"""
from __future__ import annotations

import types
import sqlite3

import pytest

from lxml import etree

from x4validate import _effective, _merge

REF_WARES = """<?xml version="1.0" encoding="utf-8"?>
<wares>
  <ware id="ore" name="Ore" price_average="100"/>
  <ware id="silicon" name="Silicon" price_average="200"/>
</wares>
"""

GOOD_DIFF = """<?xml version="1.0" encoding="utf-8"?>
<diff>
  <replace sel="//ware[@id='silicon']/@price_average">222</replace>
</diff>
"""

# The SAME op, with the closing tag removed. Byte-for-byte the good overlay
# otherwise, so the only variable between control and case is well-formedness.
BAD_DIFF = GOOD_DIFF.replace("</diff>", "")


def _mod(root, folder, body):
    d = root / "extensions" / folder
    (d / "libraries").mkdir(parents=True)
    (d / "libraries" / "wares.xml").write_text(body, encoding="utf-8")
    (d / "content.xml").write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id="{folder}" name="{folder}" version="100" date="2026-01-01"/>\n',
        encoding="utf-8")
    return d


@pytest.fixture
def tree(tmp_path):
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_text(REF_WARES, encoding="utf-8")
    return tmp_path, ref


def _merged(tmp_path, ref, overlay_body):
    d = _mod(tmp_path, "zzz_over", overlay_body)
    cfg = _merge.Config(reference=ref, overlays=[d])
    return _merge.build_effective("libraries/wares.xml", cfg, extra_overlays=[d])


def test_the_CONTROL_reads_the_overlay_and_reports_NO_loss(tree):
    """Without this the case below could pass because the fixture never worked."""
    tmp_path, ref = tree
    res = _merged(tmp_path, ref, GOOD_DIFF)
    assert res.tree is not None
    assert res.tree.xpath("//ware[@id='silicon']/@price_average") == ["222"], (
        "the overlay did not apply, so this fixture proves nothing about dropping it")
    assert res.skipped == [], res.skipped


def test_a_MALFORMED_overlay_is_reported_through_the_skipped_channel(tree):
    """The merge layer was always right -- it recorded the loss. The defect was
    downstream, in the consumer that threw the record away."""
    tmp_path, ref = tree
    res = _merged(tmp_path, ref, BAD_DIFF)
    assert res.tree is not None, (
        "the base tree must still build -- a malformed overlay is a partial loss, "
        "not a total one, and that is exactly why it is easy to miss")
    assert res.tree.xpath("//ware[@id='silicon']/@price_average") == ["200"], (
        "precondition: the overlay must NOT have applied, or there is no loss to report")
    assert res.skipped, "the merge layer failed to record the dropped overlay"


def test_merge_one_HANDS_BACK_the_dropped_overlays(tree):
    """The unit under repair. It returned `(tree, rec)` and dropped `res.skipped`."""
    tmp_path, ref = tree
    d = _mod(tmp_path, "zzz_over", BAD_DIFF)
    cfg = _merge.Config(reference=ref, overlays=[d])
    out = _effective._merge_one("libraries/wares.xml", cfg, [d])
    assert len(out) == 3, (
        "_merge_one still returns a 2-tuple, so every caller is structurally unable "
        "to see the loss: %r" % (out,))
    _tree, _rec, dropped = out
    assert dropped, "the third element is empty; the channel is still being discarded"


def test_the_EXCEPTION_handler_can_NOT_see_a_malformed_overlay(tree):
    """★ Why the pre-existing `except etree.LxmlError` was not already covering this.

    This is the assumption that made the defect invisible for a year: the handler
    LOOKS like a skip channel. It is one -- for the base file only. `overlay_root`
    swallows `XMLSyntaxError` internally, so a malformed OVERLAY raises nothing and
    the handler never runs. Pinned as a test because it is the thing a reader (and
    a reviewer) gets wrong by inspection.
    """
    tmp_path, ref = tree
    d = _mod(tmp_path, "zzz_over", BAD_DIFF)
    cfg = _merge.Config(reference=ref, overlays=[d])
    # No pytest.raises: the point is that NOTHING is raised.
    tree_, _rec, dropped = _effective._merge_one("libraries/wares.xml", cfg, [d])
    assert tree_ is not None and dropped, (
        "if this ever starts raising, the exception path becomes reachable and the "
        "comment above it must be corrected rather than left as folklore")


def test_the_two_losses_are_COUNTED_SEPARATELY(tree):
    """`absent` and `present but wrong` are different defects and only one of them
    produces a confident wrong answer. Summing them would be its own narrowing."""
    sk = _effective._SkipCount()
    sk.add("a/b.xml", ValueError("boom"))
    sk.add_overlay("c/d.xml", ["ov/x.xml: malformed XML, overlay skipped"])
    sk.add_overlay("c/e.xml", ["ov/y.xml: malformed", "ov/z.xml: malformed"])
    assert sk.n == 1, sk.n
    assert sk.dropped == 3, sk.dropped
    assert len(sk.samples) == 1 and len(sk.dropped_samples) == 3


def test_the_STORE_records_the_dropped_count_so_it_outlives_the_build(tmp_path):
    """Build-time progress scrolls past and is gone; a store carrying silently-wrong
    origins outlives the run that made it. A reader must be able to ask the store."""
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_text(REF_WARES, encoding="utf-8")
    db = tmp_path / "eff.sqlite"
    _effective._write_db(db, _merge.Config(reference=ref), [], [], {}, [], [],
                         dropped_overlays=2,
                         dropped_samples=["a.xml: malformed", "b.xml: malformed"])
    con = sqlite3.connect(str(db))
    try:
        got = dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    assert got["dropped_overlays"] == "2", got.get("dropped_overlays")
    assert "a.xml" in got["dropped_overlay_samples"], got.get("dropped_overlay_samples")


def test_a_CLEAN_build_records_ZERO_rather_than_omitting_the_key(tmp_path):
    """The twin. An ABSENT key is unreadable as 'none were dropped' -- it is
    indistinguishable from a store written before this key existed, which is the
    same absence-vs-non-answer conflation the key exists to end."""
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_text(REF_WARES, encoding="utf-8")
    db = tmp_path / "eff.sqlite"
    _effective._write_db(db, _merge.Config(reference=ref), [], [], {}, [], [])
    con = sqlite3.connect(str(db))
    try:
        got = dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    assert got["dropped_overlays"] == "0", got
    assert got["dropped_overlay_samples"] == "[]", got


# ---------------------------------------------------------------------------------
# THE WIRING, not just the helpers. Release reviewer B1 severed three connections
# inside `build()` and the suite stayed GREEN on all three:
#
#   M2  delete the three `skipped.add_overlay(vpath, dropped)` calls
#   M6  delete `where=vpath` at the three flatten_with_prov call sites
#   M7  delete `truncated_props.clear()`
#
# Every arc change to _effective was pinned at the HELPER level -- `_merge_one`
# returns three things, `_SkipCount` counts separately, `_write_db` stores the key,
# `flatten_with_prov` honours `where=` -- and nothing drove `build()` with a real
# malformed overlay. The two store tests call `_write_db` DIRECTLY with hardcoded
# values, so they cannot notice a build that never hands it a number.
#
# These tests go through `build()` and read the artifact back.


def _build_with(tmp_path, monkeypatch, overlay_body, db_name="eff.sqlite"):
    """A real build over a real reference tree and one real overlay."""
    ref = tmp_path / "reference"
    if not ref.exists():
        (ref / "libraries").mkdir(parents=True)
        (ref / "libraries" / "wares.xml").write_text(REF_WARES, encoding="utf-8")
    _mod(tmp_path, "zzz_over", overlay_body)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml",
                        lambda *a, **k: [])
    db = tmp_path / db_name
    _effective.build(_merge.Config(reference=ref), db,
                     dirs=[tmp_path / "extensions"], kinds=("ware",))
    return db


def _meta(db, key):
    import sqlite3
    con = sqlite3.connect(db)
    try:
        row = con.execute("select value from meta where key = ?", (key,)).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def test_BUILD_records_a_dropped_overlay_in_the_store(tmp_path, monkeypatch):
    """M2. Severing `skipped.add_overlay` left the store saying dropped_overlays=0
    while an overlay really was dropped -- "still vanilla" in the same grammar as a
    verified value, and the suite did not notice."""
    db = _build_with(tmp_path, monkeypatch, BAD_DIFF)
    assert int(_meta(db, "dropped_overlays") or 0) >= 1, (
        "build() dropped an unreadable overlay and the store recorded none")


def test_BUILD_over_a_GOOD_overlay_records_zero(tmp_path, monkeypatch):
    """The twin: the count must be a measurement, not a constant. If this also
    reported a drop, the test above would pass for the wrong reason."""
    db = _build_with(tmp_path, monkeypatch, GOOD_DIFF)
    assert int(_meta(db, "dropped_overlays") or 0) == 0


def test_a_dropped_overlay_is_NAMED_not_merely_counted(tmp_path, monkeypatch):
    """A count says something was lost; a sample says WHICH. The store carries both,
    and `_write_db`'s own signature exists to keep them together."""
    db = _build_with(tmp_path, monkeypatch, BAD_DIFF)
    samples = _meta(db, "dropped_overlay_samples") or ""
    assert "zzz_over" in samples or "wares.xml" in samples, samples


def test_the_COVERAGE_command_reports_the_dropped_count(tmp_path, monkeypatch, capsys):
    """The channel had no READER. `_write_db` has persisted `dropped_overlays` since
    0bb435f -- "a reader can now ask the store itself whether it was built over work
    that did not happen" -- and the release reviewer measured that no shipped reader
    asked. `x4effective coverage`, whose docstring is "answer 'what can this tool NOT
    see?' as a command, not as archaeology", was silent about it.

    On the live store this surfaced 8 real dropped overlays that had been invisible.
    """
    import sqlite3
    from x4validate import _effectivecli
    db = _build_with(tmp_path, monkeypatch, BAD_DIFF, db_name="cov.sqlite")
    con = sqlite3.connect(db)
    try:
        _effectivecli._cmd_coverage(con, None)
    finally:
        con.close()
    out = capsys.readouterr().out
    assert "dropped overlays" in out, out
    assert "1" in out.split("dropped overlays")[1].splitlines()[0]
    assert "WORK THAT DID NOT HAPPEN" in out
    assert "zzz_over" in out or "wares.xml" in out, "the drop must be NAMED"


def test_the_COVERAGE_command_says_so_when_NOTHING_was_dropped(tmp_path, monkeypatch,
                                                               capsys):
    """The twin: a clean build must state the zero rather than omit the line, or the
    reader cannot tell 'none dropped' from 'this build does not report drops'."""
    import sqlite3
    from x4validate import _effectivecli
    db = _build_with(tmp_path, monkeypatch, GOOD_DIFF, db_name="cov0.sqlite")
    con = sqlite3.connect(db)
    try:
        _effectivecli._cmd_coverage(con, None)
    finally:
        con.close()
    out = capsys.readouterr().out
    assert "dropped overlays: 0" in out
    assert "WORK THAT DID NOT HAPPEN" not in out


# --- the DUMP command had no reader for that channel (v3.1.0 review, batch 5) -------
#
# `_cmd_coverage` gained the dropped-overlay reader in-arc (6c7b8ac). Its sibling
# `_cmd_dump` did not, so it printed a tree at rc 0 with a layer silently missing --
# and under --chain the `sources:` line OMITS the dropped overlay, which is worse than
# silence because it reads as an enumeration. MergeResult.skipped says it in its own
# docstring: "the resulting tree looks complete when it is not".


class _Res:
    """The fields _cmd_dump reads, and nothing else."""
    def __init__(self, skipped):
        self.tree = etree.fromstring("<wares/>")
        self.sources = ["base"]
        self.skipped = skipped
        self.unmeasured = []        # issue #17's disclosure channel: none here


def _run_dump(monkeypatch, capsys, skipped):
    from x4validate import _effectivecli as C
    monkeypatch.setattr(C, "active_mods", lambda: [])
    monkeypatch.setattr(C, "ordered_overlays", lambda mods: [])
    monkeypatch.setattr(C, "build_touch_map", lambda ordered: {})
    monkeypatch.setattr(C, "touchers_for", lambda v, t, f: [])
    monkeypatch.setattr(C._merge, "build_effective",
                        lambda *a, **k: _Res(skipped))
    args = types.SimpleNamespace(reference=".", vpath="libraries/wares.xml", chain=False)
    rc = C._cmd_dump(args)
    return rc, capsys.readouterr().out


def test_dump_DISCLOSES_a_dropped_overlay_and_is_DEGRADED(monkeypatch, capsys):
    rc, out = _run_dump(monkeypatch, capsys, ["vro/libraries/wares.xml: malformed XML"])
    assert "INCOMPLETE" in out, out
    assert "vro/libraries/wares.xml" in out, (
        "a count says something was lost; only a NAME says which")
    assert rc == 3, "a tree built over an incomplete population is degraded, not a pass"


def test_dump_over_a_CLEAN_merge_is_rc_0_and_says_nothing(monkeypatch, capsys):
    """The twin. Without it the assertion above is satisfied by a dump that always
    cries INCOMPLETE and never returns 0."""
    rc, out = _run_dump(monkeypatch, capsys, [])
    assert rc == 0
    assert "INCOMPLETE" not in out, out
