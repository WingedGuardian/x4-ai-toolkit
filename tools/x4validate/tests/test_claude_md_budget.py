"""gates/claude_md_budget.py -- CLAUDE.md may not grow silently.

WHY A RECORDED PER-FILE SIZE AND NOT ONE CEILING. It is lowered only by `--record`
(user decision 2026-09-25) -- NOT a self-tightening ratchet. "Under 40,000 chars" is a
threshold that cannot go
red in the way that matters: it reports PASS on a file full of content that has a
better home. MEASURED 2026-09-12, the two CLAUDE.md files on this machine moved in
OPPOSITE directions -- the game-root one 154,425 -> 38,056 (-75%), and the SHIPPED
one 18,420 -> 70,118 in a single commit (`8289d5a`, +3.8x) which nothing announced.
A recorded size would not have blocked that commit; it would have made it name what it
displaced.
"""

from __future__ import annotations

import json

import pytest

from conftest import import_gate

b_ = import_gate("claude_md_budget")

CR, LF = chr(13), chr(10)


def _write(p, text, crlf=False):
    data = text.replace(LF, CR + LF) if crlf else text
    p.write_bytes(data.encode("utf-8"))
    return p


def test_char_count_is_the_same_for_an_LF_and_a_CRLF_checkout(tmp_path):
    """A checkout's line endings are not content (F120). MEASURED 2026-09-13: counting the
    raw CR made a fast-forward that rewrote the shipped CLAUDE.md as CRLF read as GREW +872,
    on a file 52 characters SMALLER than its baseline."""
    lf = _write(tmp_path / "lf.md", "a" + LF + "b" + LF)
    crlf = _write(tmp_path / "crlf.md", "a" + LF + "b" + LF, crlf=True)
    assert b_.char_count(lf) == b_.char_count(crlf) == 4


def test_TWIN_each_newline_still_counts_once_and_real_growth_shows_in_either_ending(tmp_path):
    """The twin: normalising must not swallow newlines -- the `read_text()`-style loss this
    gate was first built to avoid -- and one added character is one in both endings."""
    for crlf in (False, True):
        base = _write(tmp_path / ("base%s.md" % crlf), "a" + LF + "b" + LF, crlf=crlf)
        grown = _write(tmp_path / ("grown%s.md" % crlf), "a" + LF + "bc" + LF, crlf=crlf)
        assert b_.char_count(base) == 4, "a newline must count as a character"
        assert b_.char_count(grown) - b_.char_count(base) == 1


def test_char_count_of_a_multibyte_file_is_chars_not_bytes(tmp_path):
    p = _write(tmp_path / "m.md", "\u2605\u26a0" + LF)
    assert b_.char_count(p) == 3
    assert p.stat().st_size > 3


def test_growth_beyond_baseline_is_a_violation_naming_the_overage():
    v = b_.violations({"a": 100}, {"a": 90})
    assert len(v) == 1 and v[0].name == "a"
    assert v[0].measured == 100 and v[0].baseline == 90 and v[0].over == 10


def test_shrinking_or_equal_is_no_violation():
    assert b_.violations({"a": 90, "b": 50}, {"a": 90, "b": 60}) == []


def test_a_file_absent_from_the_baseline_is_not_a_violation():
    """First sight of a file is not growth. It is recorded on the next --record."""
    assert b_.violations({"new": 999}, {}) == []


def test_an_unreadable_file_is_a_REFUSAL_not_a_pass(tmp_path):
    with pytest.raises(b_.Unmeasurable):
        b_.char_count(tmp_path / "does-not-exist.md")


def test_resolving_no_files_at_all_exits_2(monkeypatch):
    """A budget that passes when it measured nothing is the defect it exists to
    prevent."""
    monkeypatch.setattr(b_, "budget_files", lambda: [])
    assert b_.main() == 2


def test_a_missing_baseline_says_drift_is_not_checked_and_never_passes_silently(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(b_, "BASELINE", tmp_path / "absent.json")
    rc = b_.main()
    # ONE readouterr(): the first call drains BOTH streams, so a second `.err` is
    # always empty. The refusal line goes to stderr and this test read only stdout
    # while believing it read both (checker bug, 2026-09-13).
    cap = capsys.readouterr()
    out = cap.out + cap.err
    assert "NOT being checked" in out, out
    assert rc == 2, "an unrecorded budget must REFUSE, not pass: rc %r" % rc


def test_record_then_check_is_green_and_the_baseline_round_trips(monkeypatch, tmp_path):
    bl = tmp_path / "b.json"
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main(record=True) == 0
    data = json.loads(bl.read_text(encoding="utf-8"))
    assert data, "recorded an empty baseline"
    assert data.pop("_counting") == b_.COUNTING, data
    assert all(isinstance(v, int) for v in data.values()), data
    assert b_.main() == 0


def test_the_real_files_are_found_and_named():
    """END TO END: at least the game-root CLAUDE.md must resolve on this machine."""
    files = b_.budget_files()
    assert files, "no CLAUDE.md resolved at all"
    names = {n for n, _ in files}
    # The shipped copy is in every clone; the game-root copy only where X4 is configured.
    assert any("shipped" in n for n in names), names
    for _, p in files:
        assert b_.char_count(p) > 1000


def test_a_baseline_for_DIFFERENT_files_is_a_REFUSAL(monkeypatch, tmp_path, capsys):
    """Disjoint keys compared nothing -- that is not 'within the floor'."""
    bl = tmp_path / "b.json"
    bl.write_text(json.dumps({"_counting": b_.COUNTING, "some other file": 10}), encoding="utf-8")
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main() == 2
    cap = capsys.readouterr()
    assert "NOT CHECKED" in cap.out and "REFUSING" in cap.err


def test_a_PARTIAL_baseline_names_the_file_it_does_not_check(monkeypatch, tmp_path, capsys):
    files = b_.budget_files()
    if len(files) < 2:
        pytest.skip("needs two resolvable CLAUDE.md files -- NOT CHECKED")
    first = files[0][0]
    bl = tmp_path / "b.json"
    bl.write_text(json.dumps({"_counting": b_.COUNTING, first: 10_000_000}), encoding="utf-8")
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main() == 0
    assert "NOT CHECKED  " + files[1][0] in capsys.readouterr().out


def test_a_baseline_from_the_OLD_counting_method_is_a_REFUSAL(monkeypatch, tmp_path, capsys):
    """A pre-F120 baseline counted raw CRs, so on a CRLF checkout it is too HIGH by the line
    count and would pass that much growth in silence. It must be re-recorded, not read."""
    bl = tmp_path / "b.json"
    bl.write_text(json.dumps({"shipped (repo root)": 10_000_000}), encoding="utf-8")
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main() == 2
    assert "counting method" in capsys.readouterr().err


def test_RECORD_replaces_an_OLD_METHOD_baseline_instead_of_refusing(monkeypatch, tmp_path):
    """The refusal for an old-method baseline says "re-record with --record"; that command
    must then work, not refuse on the same unreadable baseline."""
    bl = tmp_path / "b.json"
    bl.write_text(json.dumps({"shipped (repo root)": 1}), encoding="utf-8")
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main() == 2
    assert b_.main(record=True) == 0
    assert json.loads(bl.read_text(encoding="utf-8"))["_counting"] == b_.COUNTING
    assert b_.main() == 0


# ------------------------------------------------------------- issue #21: a COMMITTED floor for the shipped file
@pytest.fixture
def shipped(monkeypatch, tmp_path):
    """A stand-in shipped CLAUDE.md (CRLF, so the count is the content count) and a floor file path, and no game
    root lookup at all: --shipped must never need one."""
    md = _write(tmp_path / "CLAUDE.md", "line one" + LF + "line two" + LF, crlf=True)
    floor = tmp_path / "claude-md-floor.json"
    monkeypatch.setattr(b_, "SHIPPED", md)
    monkeypatch.setattr(b_, "FLOOR", floor)

    def no_game_root():
        raise AssertionError("--shipped looked up the game root")
    monkeypatch.setattr(b_._paths, "game_root", no_game_root)
    return md, floor, b_.char_count(md)


def _floor(path, n, counting=None):
    path.write_text(json.dumps({"_counting": counting or b_.COUNTING, b_.SHIPPED_LABEL: n}), encoding="utf-8")
    return path.read_bytes()


def test_RED_shipped_growth_past_the_committed_floor_is_rc_1(shipped, capsys):
    md, floor, n = shipped
    _floor(floor, n - 1)
    assert b_.main(argv=["--shipped"]) == 1
    assert "GREW: " + b_.SHIPPED_LABEL + " " + str(n - 1) + " -> " + str(n) + " (+1 chars)" in capsys.readouterr().err


def test_TWIN_shipped_at_or_under_the_floor_is_rc_0_and_never_lowers_it(shipped, capsys):
    md, floor, n = shipped
    before = _floor(floor, n)
    assert b_.main(argv=["--shipped"]) == 0
    before = _floor(floor, n + 50)
    assert b_.main(argv=["--shipped"]) == 0
    assert floor.read_bytes() == before, "a shrink must not lower the floor by itself (user decision 2026-09-25)"
    assert "50 under" in capsys.readouterr().out


@pytest.mark.parametrize("state", ["missing", "not json", "old counting", "no shipped entry"])
def test_TWIN_a_floor_that_cannot_be_read_is_rc_2(shipped, state, capsys):
    md, floor, n = shipped
    if state == "not json":
        floor.write_text("{", encoding="utf-8")
    elif state == "old counting":
        _floor(floor, n * 10, counting="raw")
    elif state == "no shipped entry":
        floor.write_text(json.dumps({"_counting": b_.COUNTING, "game root": 1}), encoding="utf-8")
    assert b_.main(argv=["--shipped"]) == 2
    assert "REFUSING" in capsys.readouterr().err


def test_TWIN_a_missing_shipped_file_is_rc_2(shipped):
    md, floor, n = shipped
    _floor(floor, n)
    md.unlink()
    assert b_.main(argv=["--shipped"]) == 2


def test_shipped_record_writes_the_committed_floor_and_names_the_change(shipped, capsys):
    md, floor, n = shipped
    _floor(floor, n + 7)
    assert b_.main(argv=["--shipped", "--record"]) == 0
    assert json.loads(floor.read_text(encoding="utf-8")) == {"_counting": b_.COUNTING, b_.SHIPPED_LABEL: n}
    assert "floor {} -> {} (-7)".format(n + 7, n) in capsys.readouterr().out
    assert b_.main(argv=["--shipped"]) == 0


def test_TWIN_shipped_record_of_another_label_is_refused(shipped):
    md, floor, n = shipped
    before = _floor(floor, n + 7)
    assert b_.main(argv=["--shipped", "--record", "game root"]) == 2
    assert floor.read_bytes() == before


def test_RED_record_LABEL_re_records_that_one_file_only(monkeypatch, tmp_path):
    """Issue #21, secondary: a bare --record after trimming one file re-recorded EVERY file at its current size, which
    accepts any unreviewed growth in the others."""
    a = _write(tmp_path / "a.md", "aaaa" + LF)
    g = _write(tmp_path / "g.md", "gggggggg" + LF)
    monkeypatch.setattr(b_, "budget_files", lambda: [("shipped (repo root)", a), ("game root", g)])
    bl = tmp_path / "b.json"
    bl.write_text(json.dumps({"_counting": b_.COUNTING, "shipped (repo root)": 1, "game root": 1}), encoding="utf-8")
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main(argv=["--record", "game root"]) == 0
    data = json.loads(bl.read_text(encoding="utf-8"))
    assert data["game root"] == 9 and data["shipped (repo root)"] == 1, data
    assert b_.main(argv=[]) == 1, "the shipped file's growth is still reported"


def test_TWIN_record_of_an_unknown_label_is_refused_and_writes_nothing(monkeypatch, tmp_path):
    a = _write(tmp_path / "a.md", "aaaa" + LF)
    monkeypatch.setattr(b_, "budget_files", lambda: [("shipped (repo root)", a)])
    bl = tmp_path / "b.json"
    bl.write_text(json.dumps({"_counting": b_.COUNTING, "shipped (repo root)": 1}), encoding="utf-8")
    before = bl.read_bytes()
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main(argv=["--record", "no such file"]) == 2
    assert bl.read_bytes() == before


def test_TWIN_record_LABEL_over_an_unreadable_baseline_is_refused(monkeypatch, tmp_path):
    """One label cannot be recorded into a baseline whose other entries cannot be read: that would drop them."""
    a = _write(tmp_path / "a.md", "aaaa" + LF)
    monkeypatch.setattr(b_, "budget_files", lambda: [("shipped (repo root)", a)])
    bl = tmp_path / "b.json"
    bl.write_text("{", encoding="utf-8")
    monkeypatch.setattr(b_, "BASELINE", bl)
    assert b_.main(argv=["--record", "shipped (repo root)"]) == 2
    assert bl.read_text(encoding="utf-8") == "{"


def test_the_committed_floor_is_readable_and_keyed_for_the_shipped_file():
    """The real file: committed beside this gate, counted the current way, one entry. (Its VERDICT is the gate's job:
    `--shipped` runs in the pre-push gate, in CI's Fast checks and as a release stage.)"""
    data = json.loads(b_.FLOOR.read_bytes().decode("utf-8"))
    assert data == {"_counting": b_.COUNTING, b_.SHIPPED_LABEL: data[b_.SHIPPED_LABEL]}
    assert isinstance(data[b_.SHIPPED_LABEL], int) and 1000 < data[b_.SHIPPED_LABEL] <= b_.HARD_CEILING


def test_RED_ci_fast_checks_run_the_shipped_floor():
    """Issue #21: CI cold-skipped the budget (it needed a game root and a local baseline). The Fast checks job runs
    `--shipped`, which needs neither."""
    from _layout import require_repo
    require_repo(".github/workflows/ci.yml", why="workflow source is not copied into installed runtimes")
    from ci_workflow import load_ci
    steps = load_ci(b_.REPO_ROOT)["jobs"]["quick"]["steps"]
    runs = [s for s in steps if "claude_md_budget.py" in str(s.get("run", ""))]
    assert len(runs) == 1, runs
    assert runs[0]["run"].split() == ["python", "-B", "gates/claude_md_budget.py", "--shipped"]
    assert runs[0].get("working-directory") == "tools/x4validate"
    assert "if" not in runs[0] and "continue-on-error" not in runs[0], "a step that cannot fail the job is decoration"
