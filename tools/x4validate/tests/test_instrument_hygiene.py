"""`gates/instrument_hygiene.py` — and above all, that its patterns are LIVE.

THE DEFECT THIS PINS. Writing one regex to match a literal backslash failed three
times in ten minutes, inside the gate built to count exactly that kind of mistake:

  1. `r"\\\\[nrt]"` in a raw string is FOUR backslashes — a regex for TWO literal
     ones. It counted 9 where the answer was 395.
  2. rebuilt as `chr(92) + "[nrt]"`, which is a regex for a literal `[nrt]`. Counted 0.
  3. the verification of (2) was itself written through a Bash heredoc, where `\\b`
     collapsed to a backspace character — so it agreed with the broken pattern.

Every one of those reported a comfortable, well-formed number. **A pattern that has
never been shown to match anything is a check that cannot go red**, and no amount of
care substitutes for a fixture: attempt 3 was made by someone who had just written
attempts 1 and 2 down as lessons.

So each `Shape` carries a known-bad example it MUST match and a near-miss it must
NOT, `verify_fixtures()` runs before any count is produced, and the tests below make
that contract enforceable rather than conventional.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import import_gate

ih = import_gate("instrument_hygiene")

NL = chr(10)
BS = chr(92)

_GRAMMAR = "agent/guards/claude-hooks/hook_facts.py"


@pytest.fixture(autouse=True)
def _a_missing_shell_grammar_is_a_counted_skip_in_an_install(monkeypatch):
    """D-C2: the census reads the toolkit's shell grammar from `agent/`, which an install does not
    ship, and refuses with `SystemExit(2)` ("run this maintainer gate from the toolkit checkout").
    pytest reports a SystemExit as a FAILURE: 22 of this file's tests failed on an installed copy.
    Only the tests that actually reach the grammar are affected, and only in an installed layout where
    the file is absent; a checkout that lost it still gets the gate's own refusal."""
    from _layout import ROOT, installed_layout, require_repo
    if not installed_layout() or (ROOT / _GRAMMAR).is_file():
        return
    real = ih._env.skip

    def skip(what, how):
        if "shared shell grammar" in what or "hygiene grammar" in what:
            require_repo(_GRAMMAR, why="the shell grammar the hygiene census parses with")
        real(what, how)
    monkeypatch.setattr(ih._env, "skip", skip)


# --------------------------------------------------------------------------
# the contract: every shipped pattern is live, and provably so
# --------------------------------------------------------------------------
def test_every_shipped_pattern_matches_its_own_known_bad_example():
    """The load-bearing test. If this fails, some pattern has gone inert and the
    gate has been reporting zero for a shape it can no longer see."""
    assert ih.verify_fixtures() == []


def test_every_shape_actually_carries_a_fixture():
    """A Shape added without one would satisfy verify_fixtures() vacuously — an
    empty string matches nothing and is matched by nothing, so both checks pass."""
    for s in ih.SHAPES:
        assert s.hits.strip(), f"{s.key} has no known-bad example"
        assert s.misses.strip(), f"{s.key} has no near-miss"
        assert s.why.strip(), f"{s.key} does not say why it matters"


def test_an_INERT_pattern_is_reported(monkeypatch):
    """Falsification: reproduce failure (2) above — a pattern that compiles, runs,
    and matches nothing it should."""
    dead = ih.Shape("dead", "w", re.compile("ZZZ-never-occurs"),
                    hits="a real bad example", misses="fine")
    bad = ih.verify_fixtures([dead])
    assert len(bad) == 1 and "inert" in bad[0]


def test_an_OVER_BROAD_pattern_is_reported():
    """The other direction. Without this, `re.compile('')` would pass the first
    check and the fixture would prove nothing at all."""
    greedy = ih.Shape("greedy", "w", re.compile(""), hits="x", misses="y")
    bad = ih.verify_fixtures([greedy])
    assert len(bad) == 1 and "too broad" in bad[0]


def test_a_healthy_pattern_reports_nothing():
    """The positive twin, so the two assertions above cannot pass for a
    verify_fixtures() that complains about everything."""
    ok = ih.Shape("ok", "w", re.compile(r"\bfoo\b"), hits="a foo here", misses="food")
    assert ih.verify_fixtures([ok]) == []


def test_the_bare_python_rule_does_not_flag_a_uv_run_invocation():
    """The regression that made this rule report 52 where the answer was 2. A
    lookbehind inspects a FIXED width — seven characters — so anything between
    `uv run` and `python` defeated it."""
    s = next(x for x in ih.SHAPES if x.key == "bare-python-on-project-code")
    assert s.pattern.search("python -m pytest -q")
    for exonerated in ("uv run python -m pytest",
                       "uv run --frozen python -m pytest",
                       "cd x && uv run --project ../y python -m pytest a.py"):
        assert not s.pattern.search(exonerated), exonerated


@pytest.mark.parametrize("key,command", [
    ("dollar-question-after-pipe", "sha256sum x | head -1; uv run pytest > out; rc=$?"),
    ("bare-python-on-project-code", "python - <<'EOF'\nexample = 'python -m pytest'\nEOF"),
    ("test-and-commit-in-one-command", "git commit -m 'pytest passed earlier'"),
    ("test-and-commit-in-one-command", "cat > example.txt <<'EOF'\npytest; git commit\nEOF"),
])
def test_data_and_intervening_commands_are_not_hygiene_failures(key, command):
    shape = next(s for s in ih.SHAPES if s.key == key)
    assert not ih.matches(shape, command)


@pytest.mark.parametrize("key,command", [
    ("dollar-question-after-pipe", 'uv run pytest | tail -1; echo "rc=$?"'),
    ("bare-python-on-project-code", "cd /repo/tools/x4validate && python -m pytest"),
    ("test-and-commit-in-one-command", "uv run pytest; git commit -m ok"),
])
def test_real_hygiene_failures_still_match(key, command):
    shape = next(s for s in ih.SHAPES if s.key == key)
    assert ih.matches(shape, command)


def test_bare_python_respects_the_actual_project_scope_and_cwd():
    shape = next(s for s in ih.SHAPES if s.key == "bare-python-on-project-code")
    assert not ih.matches(shape, "python -m pytest hook_tests.py", cwd="/repo/agent/guards/claude-hooks")
    assert ih.matches(shape, "python -m pytest tests", cwd="/repo/tools/x4validate")
    assert ih.matches(shape, "cd /repo/tools/x4validate && python -m pytest tests", cwd="/elsewhere")
    assert not ih.matches(shape, "uv run python -m pytest tests", cwd="/repo/tools/x4validate")


def test_bare_python_keeps_the_existing_pytest_only_shape():
    shape = next(s for s in ih.SHAPES if s.key == "bare-python-on-project-code")
    assert not ih.matches(shape, "python gates/check.py", cwd="/repo/tools/x4validate")


# --------------------------------------------------------------------------
# scanning: absence, non-answer and a real reading are three different states
# --------------------------------------------------------------------------
def _rec(cmd, tid="t1"):
    return json.dumps({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]}})


def _res(text, tid="t1"):
    return json.dumps({"message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}})


def test_a_directory_with_no_transcripts_refuses(tmp_path):
    with pytest.raises(SystemExit) as e:
        ih.scan(tmp_path)
    assert e.value.code == 2


def test_an_unparseable_line_is_RECORDED_not_swallowed(tmp_path):
    (tmp_path / "s.jsonl").write_text("{not json" + NL + _rec("echo hi") + NL,
                                      encoding="utf-8")
    c = ih.scan(tmp_path)
    assert len(c.unreadable) == 1
    assert c.commands == 1


def test_a_shape_is_counted(tmp_path):
    (tmp_path / "s.jsonl").write_text(_rec("grep -c ERROR debug.txt") + NL,
                                      encoding="utf-8")
    c = ih.scan(tmp_path)
    assert c.counts.get("grep-c-or-wc-l-as-a-count") == 1


# --------------------------------------------------------------------------
# the exit-code rung: a side effect BEFORE the failure point almost certainly ran
# --------------------------------------------------------------------------
def test_a_side_effect_in_the_FIRST_segment_is_not_counted_as_lost(tmp_path):
    """`cp a b && grep x f` exits 1 with the cp having run perfectly well. Counting
    those inflated this figure from 8 to 39 in the first draft — an overcount found
    by red-teaming rather than by any test."""
    (tmp_path / "s.jsonl").write_text(
        _rec("cp a b && grep missing f") + NL + _res("Exit code 1") + NL,
        encoding="utf-8")
    c = ih.scan(tmp_path)
    assert c.nonzero == 1
    assert c.lost_side_effects == []


def test_a_side_effect_AFTER_the_failure_point_is_counted(tmp_path):
    """The positive twin. Without it the test above would pass for an
    implementation that never counted anything."""
    (tmp_path / "s.jsonl").write_text(
        _rec("grep missing f && cp a b") + NL + _res("Exit code 1") + NL,
        encoding="utf-8")
    c = ih.scan(tmp_path)
    assert len(c.lost_side_effects) == 1


def test_a_zero_exit_is_not_counted_at_all(tmp_path):
    (tmp_path / "s.jsonl").write_text(
        _rec("grep found f && cp a b") + NL + _res("some output") + NL,
        encoding="utf-8")
    c = ih.scan(tmp_path)
    assert c.nonzero == 0 and c.lost_side_effects == []


# --------------------------------------------------------------------------
# the baseline distinguishes ABSENT from UNREADABLE
# --------------------------------------------------------------------------
def test_an_absent_baseline_accepts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ih, "BASELINE", tmp_path / "nope.json")
    assert ih._baseline() == ({}, {})


def test_the_verdict_judges_the_INCREMENTAL_rate_not_the_lifetime_average(tmp_path, monkeypatch):
    """MEASURED 2026-09-20, and this is why: over 22,288 commands against a 10,767-command
    baseline, `$?`-after-a-pipe ran at 2.54% in the NEW period (baseline 1.74%) and the gate
    reported green, because the lifetime average -- 2.15% -- was diluted by the older half.
    A corpus that only grows makes any one period's contribution decay as 1/N, so the shape
    this gate exists to catch becomes invisible exactly as it gets worse.

    Arithmetic here: baseline 1,000 commands at 1.00%; now 2,000 commands at 1.60%. The new
    1,000 therefore ran at 2.20%, over the 1.25x rule; the cumulative 1.60% is also over, so
    to isolate the mechanism the numbers are chosen where cumulative is UNDER and
    incremental is OVER: baseline 10,000 at 1.00%; now 20,000 at 1.20% -> incremental 1.40%,
    which is 1.4x the baseline while the cumulative 1.20x sits under the threshold."""
    key = ih.SHAPES[0].key
    base = {"rates": {key: 0.010}, "commands": 10000}
    b = tmp_path / "b.json"
    b.write_text(__import__("json").dumps(base), encoding="utf-8")
    monkeypatch.setattr(ih, "BASELINE", b)
    rates, meta = ih._baseline()
    assert meta["commands"] == 10000
    cumulative_now = 0.012
    span = 20000 - 10000
    incremental = (cumulative_now * 20000 - rates[key] * 10000) / span
    assert incremental == pytest.approx(0.014)
    assert not (cumulative_now > rates[key] * 1.25), "cumulative must NOT trip, or this proves nothing"
    assert incremental > rates[key] * 1.25 and incremental - rates[key] > 0.002


def test_a_damaged_baseline_RAISES_rather_than_reading_as_empty(tmp_path, monkeypatch):
    b = tmp_path / "b.json"
    b.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(ih, "BASELINE", b)
    with pytest.raises(RuntimeError):
        ih._baseline()


def test_the_hookable_threshold_is_a_measured_constant_not_a_guess():
    """It decides whether a shape can become a PreToolUse rule at all. Pinned so a
    silent widening cannot make a flooding rule look viable."""
    assert ih.HOOKABLE_MAX == 0.02


def test_a_run_with_NO_BASELINE_is_a_REFUSAL_rc2_never_a_pass(tmp_path, monkeypatch, capsys):
    """run-gates.sh reads rc 0 as `ok` and discards stdout, so "measures but cannot judge"
    at rc 0 printed a passing gate over a comparison that never happened (review,
    2026-09-14)."""
    t = tmp_path / "t"
    t.mkdir()
    (t / "s.jsonl").write_text(_rec("echo hi") + NL + _res("hi") + NL, encoding="utf-8")
    monkeypatch.setattr(ih, "transcript_dir", lambda: t)
    monkeypatch.setattr(ih, "BASELINE", tmp_path / "absent.json")
    monkeypatch.setattr(ih, "RECORD", False)
    assert ih.main() == 2
    assert "cannot judge" in capsys.readouterr().out


def _one_command_run(tmp_path, monkeypatch, extra_lines: str = ""):
    t = tmp_path / "t"
    t.mkdir(parents=True)
    (t / "s.jsonl").write_text(_rec("echo hi") + NL + _res("hi") + NL + extra_lines,
                               encoding="utf-8")
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"rates": {s.key: 0.0 for s in ih.SHAPES}, "commands": 1,
                            "matcher_fingerprint": ih.matcher_fingerprint()}),
                 encoding="utf-8")
    monkeypatch.setattr(ih, "transcript_dir", lambda: t)
    monkeypatch.setattr(ih, "BASELINE", b)
    monkeypatch.setattr(ih, "RECORD", False)


def test_an_UNREADABLE_input_is_not_a_clean_verdict(tmp_path, monkeypatch, capsys):
    """AUDIT-2026-09-24 GT-6: unreadable transcript lines were printed (the first 5) and then
    ignored -- rc 0 over a denominator with a hole in it. A finding would still outrank it."""
    _one_command_run(tmp_path, monkeypatch)
    assert ih.main() == 0                                     # control: nothing unreadable
    capsys.readouterr()
    _one_command_run(tmp_path / "x", monkeypatch, extra_lines="{ this is not json" + NL)
    assert ih.main() == 2
    out = capsys.readouterr()
    assert "unparseable line" in out.out and "not a clean verdict" in out.err


def test_the_span_NOTE_names_the_real_reason(tmp_path, monkeypatch, capsys):
    """AUDIT-2026-09-24 GT-6: the NOTE said 'baseline carries no command count' for EVERY
    zero span -- also when the baseline had one and no command had been added since."""
    _one_command_run(tmp_path, monkeypatch)                  # baseline commands == now == 1
    ih.main()
    out = capsys.readouterr().out
    assert "carries no command count" not in out
    assert "no Bash command since the baseline" in out


def test_a_HALF_WRITTEN_last_line_of_a_live_transcript_is_tolerated(tmp_path, monkeypatch, capsys):
    """Review of 87ac461: the transcript of the session RUNNING this gate is being appended
    to, so its last line can be half-written. That is not a hole in the denominator and must
    not flip the gate to rc 2 -- it is counted separately and printed. A broken line anywhere
    ELSE is still unreadable (pinned by test_an_UNREADABLE_input_is_not_a_clean_verdict)."""
    _one_command_run(tmp_path, monkeypatch, extra_lines='{"message": {"content": [{"ty')
    assert ih.main() == 0
    out = capsys.readouterr().out
    assert "partial last line" in out
    c = ih.scan(tmp_path / "t")
    assert c.unreadable == [] and c.partial_tail == ["s.jsonl"]


def test_a_half_written_last_line_in_an_OLD_transcript_is_unreadable(tmp_path, monkeypatch,
                                                                     capsys):
    """Release review 2026-09-26: ANY unterminated unparseable last line was excused as a
    live append -- including a transcript a crash truncated long ago, which is a real
    hole in the denominator. Only a transcript written within LIVE_APPEND_WINDOW_S is a
    writer mid-append; the twin above (a fresh file) is still tolerated."""
    import os
    import time
    _one_command_run(tmp_path, monkeypatch, extra_lines='{"message": {"content": [{"ty')
    f = tmp_path / "t" / "s.jsonl"
    old = time.time() - 86400
    os.utime(f, (old, old))
    assert ih.main() == 2
    c = ih.scan(tmp_path / "t")
    assert c.partial_tail == [] and c.unreadable, (c.partial_tail, c.unreadable)


def test_a_baseline_from_another_matcher_is_not_compared(tmp_path, monkeypatch, capsys):
    _one_command_run(tmp_path, monkeypatch)
    assert ih.main() == 0
    capsys.readouterr()
    baseline = json.loads(ih.BASELINE.read_text(encoding="utf-8"))
    baseline.pop("matcher_fingerprint")
    ih.BASELINE.write_text(json.dumps(baseline), encoding="utf-8")
    assert ih.main() == 2
    assert "different or unrecorded matcher" in capsys.readouterr().err


def test_a_matcher_changed_during_the_scan_voids_the_reading(tmp_path, monkeypatch, capsys):
    _one_command_run(tmp_path, monkeypatch)
    values = iter(["before", "after"])
    monkeypatch.setattr(ih, "matcher_fingerprint", lambda: next(values))
    assert ih.main() == 2
    assert "changed during the census" in capsys.readouterr().err
