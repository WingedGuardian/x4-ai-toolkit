r"""`x4debug` — turn a debug.txt into a triage, and compare it to what we predicted.

Two things this must never do, both learned expensively:

**Never present a bucket table whose rows do not sum to the input.** Every triage
before this one was hand-rolled `grep | sort | uniq -c`, and a hand-rolled table
has no way to notice that it dropped a shape. The residue row is therefore
mandatory output, printed even when it is zero — a table that only shows residue
when residue exists teaches the reader that its absence means nothing happened.

**Never compare two states by their totals.** The 2026-08-13 log has 342 failing
ops in one cpsdo file; x4validate independently predicted 310 for that mod. Those
two numbers are close enough to look like agreement and are not the same set. The
crosscheck reports per item, in three buckets, and `observed-not-predicted` — the
validator's blind spot — is the one that has to be impossible to overlook.
"""

from pathlib import Path

import pytest

from x4validate import _debugcli, _freshness

SAMPLE = "\n".join([
    r"Logfile started, time Thu Aug 13 14:06:37 2026",
    r"[General] 0.00 Universe generation begins",
    r"[=ERROR=] 0.00 No matching node for path '//wares/ware[@id='x']/owner/@faction' in patch file 'extensions\modA\libraries\wares'. Skipping node.",
    r"[=ERROR=] 0.00 Multiple matching nodes for path '//wares/ware[@id='y']' in patch file 'extensions\modA\libraries\wares'. Skipping node.",
    r"[=ERROR=] 0.00 [JobEngine] No ship generated for JobID: 'argon_heavyfrigate_patrol_l_sector'. Probably invalid ship macro/group/ref definition.",
    r"[=ERROR=] 0.00 TotallyNovelSubsystem::Boom(): nobody has ever parsed this",
])


def _write(tmp_path, text=SAMPLE, name="debug.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_triage_rows_sum_to_the_input(tmp_path, capsys):
    """The invariant, as output. If the buckets do not add up to the lines read,
    the table is lying and must say so rather than print a plausible total."""
    rc = _debugcli.main(["triage", str(_write(tmp_path))])
    out = capsys.readouterr().out
    assert rc == 0
    assert "4 [=ERROR=] line(s) read" in out
    assert "1 UNCLASSIFIED" in out, out


def test_triage_prints_the_residue_row_even_when_it_is_ZERO(tmp_path, capsys):
    """A row that appears only when non-zero trains the reader to read its absence
    as 'not measured'. It has to be present either way."""
    text = "\n".join(SAMPLE.splitlines()[:-1])  # drop the novel shape
    rc = _debugcli.main(["triage", str(_write(tmp_path, text))])
    out = capsys.readouterr().out
    # AUDIT TS-2 (release review 2026-09-26): this asserted only `"0" in out` -- which
    # the capture DATE satisfies -- and never looked at the rc. The row is asserted
    # exactly: its label, a count of ZERO in its column, and its explanation; the FLOOR
    # warning that belongs to a non-zero residue must be absent; the rows still sum.
    assert rc == 0, out
    import re
    rows = re.findall(r"(?m)^  UNCLASSIFIED +(\d+)   \(a shape this parser does not know\)$",
                      out)
    assert rows == ["0"], out
    assert "FLOOR, not a total" not in out, out
    assert "rows 3 == lines read 3: OK" in out, out


def test_triage_states_the_logs_age_and_whether_it_is_a_new_game(tmp_path, capsys):
    """A stale log looks exactly like a fresh one. And error COUNTS are not
    comparable across the new-game/save-load boundary, so the boundary has to be
    stated before any number is."""
    _debugcli.main(["triage", str(_write(tmp_path))])
    out = capsys.readouterr().out
    assert "new game" in out.lower(), out
    assert "captured" in out.lower() or "mtime" in out.lower(), out


def test_triage_does_not_infer_a_save_load_from_an_absent_generation_marker(tmp_path, capsys):
    """Menu-only startup also lacks the marker; absence cannot prove a save load."""
    text = SAMPLE.replace("[General] 0.00 Universe generation begins", "")
    assert _debugcli.main(["triage", str(_write(tmp_path, text))]) == 0
    out = capsys.readouterr().out
    session = next(line for line in out.splitlines() if "session type" in line)
    assert "UNKNOWN" in session and "menu" in session and "save load" in session
    assert "rows 4 == lines read 4: OK" in out


def test_triage_attributes_diff_op_failures_to_the_owning_mod(tmp_path, capsys):
    _debugcli.main(["triage", str(_write(tmp_path))])
    out = capsys.readouterr().out
    assert "modA" in out
    assert "jobengine" in out.lower()


def test_a_missing_log_is_a_NON_ANSWER_not_a_clean_one(tmp_path, capsys):
    """The single most dangerous outcome: exit 0 on a log that was never read,
    which renders 'nothing examined' as 'nothing wrong'."""
    rc = _debugcli.main(["triage", str(tmp_path / "nope.txt")])
    err = capsys.readouterr().err
    assert rc != 0, "a log that could not be read must never exit 0"
    assert "could not" in err.lower() or "not found" in err.lower()


def test_crosscheck_splits_into_three_buckets_not_two_counts(tmp_path):
    """Predicted-and-observed is the boring one. The two asymmetric buckets are
    the findings: one is a stale prediction, the other is a validator blind spot."""
    observed = ["//a[@id='1']", "//b[@id='2']"]
    predicted = ["//a[@id='1']", "//c[@id='3']"]
    r = _debugcli.compare_ops(observed=observed, predicted=predicted)
    assert r.both == ["//a[@id='1']"]
    assert r.predicted_only == ["//c[@id='3']"]
    assert r.observed_only == ["//b[@id='2']"], (
        "an op the engine skipped but we never predicted is a blind spot in the "
        "validator — it must surface as its own bucket, never fold into a total")


def test_crosscheck_counts_duplicates_rather_than_collapsing_them(tmp_path):
    """The engine logs an op once per application pass, so the same sel legitimately
    appears twice. Collapsing to a set would silently reconcile 342 with 310 and
    hide whichever gap is real."""
    r = _debugcli.compare_ops(observed=["//a", "//a"], predicted=["//a"])
    assert r.observed_total == 2 and r.predicted_total == 1
    assert r.both == ["//a"]


@pytest.fixture
def resolved_extensions(tmp_path, monkeypatch):
    """Give the archive a REAL extensions root, named by the test.

    Without this, the two tests below passed on a machine with NO X4 install for
    entirely the wrong reason: `archive` fell back to `Path("")` -- which is
    `Path(".")` -- fingerprinted the CURRENT DIRECTORY as though it were the mod
    install, and reported degraded: false. They were exercising the defect and
    calling it the happy path, and CI (which has no install) is exactly where
    that happened.

    A test whose answer depends on mutable state outside itself is not testing
    what its name says -- the same reason test_ask.py pins its preflight.
    """
    root = tmp_path / "game-extensions"
    root.mkdir()
    monkeypatch.setattr(_debugcli._paths, "game_extensions", lambda: root)
    return root


def test_baseline_stamps_a_content_fingerprint_but_NOT_an_engine_one(
        tmp_path, resolved_extensions):
    """A log is a record of a game launch, not a merge product. Flagging it as
    engine-dependent would make it read STALE every time we touch _merge.py, and a
    banner that cries wolf is one the reader learns to skip."""
    log = _write(tmp_path)
    out = _debugcli.archive(log, tmp_path / "archive")
    assert out.is_file(), "the log itself must be copied, not just described"
    meta = _debugcli.read_archive_meta(out)
    assert meta["engine_dependent"] is False
    assert meta["fingerprint"]["content"]
    assert meta["total_errors"] == 4


def test_archive_keeps_distinct_captures_with_the_same_source_mtime(tmp_path, resolved_extensions):
    import os
    log = _write(tmp_path)
    stamp = log.stat().st_mtime
    first = _debugcli.archive(log, tmp_path / "archive")
    first_bytes = first.read_bytes()
    first_meta = _debugcli._meta_path(first).read_bytes()
    log.write_text("[=ERROR=] 1.0 another capture\n", encoding="utf-8")
    os.utime(log, (stamp, stamp))
    second = _debugcli.archive(log, tmp_path / "archive")
    assert second != first
    assert first.read_bytes() == first_bytes
    assert _debugcli._meta_path(first).read_bytes() == first_meta
    assert second.read_bytes() == log.read_bytes()
    assert _debugcli.read_archive_meta(second)["total_errors"] == 1


@pytest.mark.parametrize("late", [False, True], ids=["orphan", "late-companion"])
def test_archive_never_replaces_independent_metadata(tmp_path, resolved_extensions, monkeypatch, late):
    from types import SimpleNamespace
    log = _write(tmp_path)
    dest = tmp_path / "archive"
    dest.mkdir()
    token = "a" * 32
    stamp = _debugcli.datetime.fromtimestamp(log.stat().st_mtime).strftime("%Y-%m-%dT%H%M")
    raw = dest / f"debug-{stamp}-{token}.txt"
    companion = _debugcli._meta_path(raw)
    foreign = b"independent metadata evidence"
    monkeypatch.setattr(_debugcli.uuid, "uuid4", lambda: SimpleNamespace(hex=token))
    real_parse = _debugcli._debuglog.parse_log_text
    def parse(text):
        companion.write_bytes(foreign)
        return real_parse(text)
    if late:
        monkeypatch.setattr(_debugcli._debuglog, "parse_log_text", parse)
    else:
        companion.write_bytes(foreign)
    with pytest.raises(FileExistsError):
        _debugcli.archive(log, dest)
    assert companion.read_bytes() == foreign
    assert raw.read_bytes() == log.read_bytes()


class _F:
    def __init__(self, category, message, vpath=""):
        self.category, self.message, self.vpath = category, message, vpath


class _R:
    def __init__(self, findings):
        self.findings = findings
        self.degraded = []


def test_predicted_ops_reads_BOTH_cardinality_shapes():
    r"""The bug this tool committed against itself, on its first real run.

    x4validate reports two distinct cardinality failures, and RFC 5261 makes both
    fatal — a `sel` must match exactly one node, so 0 and 2 are equally skipped by
    the engine:

        <replace> sel matched nothing: <sel>
        <replace> sel matched 2 nodes (must match exactly 1 ...): <sel>

    The first extractor matched only "matched nothing", silently dropped all 116
    multi-match predictions on cpsdo_faction, and then reported those same 116 as
    "the engine skipped it and we never predicted it — a VALIDATOR BLIND SPOT".
    A narrowing step that reports its own residue as someone else's defect: the
    register's shape, written into the tool built to find it.
    """
    r = _R([
        _F("sel", "<replace> sel matched nothing: //a[@id='1']"),
        _F("sel", "<replace> sel matched 2 nodes (must match exactly 1 — the engine "
                  "SKIPS ambiguous ops, so this silently does nothing): //b[@id='2']"),
        _F("sel", "<add> sel matched nothing: //c[@id='3']"),
        _F("path", "some other category entirely"),
    ])
    assert _debugcli.predicted_ops(r) == ["//a[@id='1']", "//b[@id='2']", "//c[@id='3']"]


def test_an_unrecognised_sel_finding_RAISES_rather_than_being_skipped():
    """The generalisation. Any future reword of a `sel` message must fail loudly
    here, not shrink the prediction set in silence — silence is what turned a
    working validator into a fabricated blind spot."""
    import pytest
    r = _R([_F("sel", "<replace> sel did something we have never phrased before")])
    with pytest.raises(_debugcli.UnparsedFinding) as exc:
        _debugcli.predicted_ops(r)
    assert "never phrased before" in str(exc.value)


def test_predicted_ops_prefers_the_STRUCTURED_sel_over_the_message():
    r"""Second extraction bug of the same class, found by the same sweep.

    x4validate's message is `sel matched nothing: <sel>` plus OPTIONAL suffixes
    — ` (silent)` and ` [if= passed: <cond>]`. A tail-anchored regex swallows
    them, so `/wares/ware[@id='resourceprobe_02_sm'] (silent)` was compared
    against the engine's `/wares/ware[@id='resourceprobe_02_sm']` and could never
    match: 6 fabricated "predicted-only" rows on xenon_backup alone.

    The fix is to stop scraping prose that has structure behind it. `Finding.sel`
    carries the selector verbatim; the regex survives only as a fallback for
    findings that predate the field.
    """
    class F:
        category, message, sel = "sel", "<add> sel matched nothing: /a/b (silent)", "/a/b"

    assert _debugcli.predicted_ops(_R([F()])) == ["/a/b"], (
        "the structured field must win — the message carries suffixes that are "
        "not part of the selector")


def test_the_message_fallback_also_strips_the_known_suffixes():
    """Belt and braces: even without the structured field, the suffixes must go."""
    r = _R([
        _F("sel", "<add> sel matched nothing: /a/b (silent)"),
        _F("sel", "<replace> sel matched nothing: /c/d [if= passed: //x]"),
        _F("sel", "<add> sel matched nothing: /e/f (silent) [if= passed: //y]"),
    ])
    assert _debugcli.predicted_ops(r) == ["/a/b", "/c/d", "/e/f"]


def test_an_unreadable_sel_finding_ends_the_CLI_cleanly_not_in_a_traceback(monkeypatch, capsys, tmp_path):
    """`predicted_ops` RAISES on purpose, so the CLI has to catch it.

    Same rule the staleness CLI already follows: a tool that cannot answer says
    so, with a reason and a distinct exit code. A traceback tells the reader
    nothing about WHICH of the three states they are in — and here the state is
    "the prediction set is incomplete, so every bucket below would be wrong",
    which is far worse than no answer at all.
    """
    log = tmp_path / "debug.txt"
    log.write_text(SAMPLE, encoding="utf-8")
    mod = tmp_path / "modA"
    mod.mkdir()
    (mod / "content.xml").write_text('<content id="modA" version="100"/>', encoding="utf-8")

    def boom(_report):
        raise _debugcli.UnparsedFinding("<replace> sel did something new: //x")

    from x4validate import _check
    monkeypatch.setattr(_debugcli, "predicted_ops", boom)
    monkeypatch.setattr(_check, "validate", lambda *a, **k: _R([]))

    rc = _debugcli.main(["crosscheck", str(mod), str(log)])
    err = capsys.readouterr().err
    assert rc == 4, f"expected the distinct 'cannot compare' code, got {rc}"
    assert "Traceback" not in err
    assert "incomplete" in err.lower() or "cannot compare" in err.lower()
    assert "_SEL_SHAPES" in err, "the message must name the fix"


def test_archive_never_leaves_a_log_whose_meta_cannot_be_read(tmp_path, monkeypatch):
    """A VOLATILE source must not produce a half-archived entry.

    `archive()` copies the log FIRST -- deliberately, and that is right for a file
    the GAME owns: `debug.txt` and `{profile}/uidata.xml` are rewritten by X4 on
    exit, so the bytes are the one thing that cannot be re-obtained without another
    play session. Losing them to a metadata failure would be the expensive mistake.

    But copy-first left the other half open: if parsing or fingerprinting raised,
    the archive kept a log with NO `.meta.json`, and `read_archive_meta` on it threw.
    A partial artifact that reports nothing is the shape this toolkit exists to
    refuse -- an entry that cannot say what it is is neither present nor absent.

    So: keep the bytes, ALWAYS write a meta, and make the meta say it is degraded.
    Prompted by a peer's `x4live archive` design note, not by a failure in the wild.
    """
    log = tmp_path / "debug.txt"
    log.write_text("[=ERROR=] something\n", encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("fingerprint unavailable")
    monkeypatch.setattr(_freshness, "fingerprint", boom)

    out = _debugcli.archive(log, tmp_path / "archive")
    assert out.is_file(), "the bytes must survive -- the source may be gone by now"
    assert out.read_text(encoding="utf-8") == log.read_text(encoding="utf-8")

    meta = _debugcli.read_archive_meta(out)          # must NOT raise
    assert meta.get("degraded") is True
    assert "fingerprint" in str(meta.get("degraded_reason", "")).lower()


def test_the_CONSUMER_survives_a_degraded_meta(tmp_path, monkeypatch, capsys,
                                              resolved_extensions):
    """The test above proved the ARCHIVE survives. Nothing proved the READER did.

    `archive()` sets its five keys with one `meta.update({...})` inside a try, so any
    raise leaves NONE of them and records `degraded` instead -- and `main()` read all
    five unconditionally. REPRODUCED through the real CLI before the fix:
    `KeyError: 'total_errors'`, an uncaught traceback, immediately after successfully
    writing the archive. The degraded flag existed precisely so a reader could act on
    it, and the only reader never looked.

    `resolved_extensions` is REQUIRED here: with no root, archive() now
    degrades before it ever reaches the fingerprint call, and this test is
    about what happens when that call RAISES.
    """
    log = tmp_path / "debug.txt"
    log.write_text("[=ERROR=] something\n", encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("fingerprint unavailable")
    monkeypatch.setattr(_freshness, "fingerprint", boom)

    rc = _debugcli.main(["baseline", str(log), "--dest", str(tmp_path / "arch")])
    out = capsys.readouterr().out
    assert rc == 1, "a baseline that cannot state its fingerprint must not report success"
    assert "DEGRADED" in out, out
    assert "fingerprint unavailable" in out, out
    assert "archived" in out, "the archive was still written and must still be named"


def test_a_HEALTHY_baseline_still_reports_rc0(tmp_path, capsys, resolved_extensions):
    """The twin: a guard that returned 1 for everything would pass the test above."""
    log = tmp_path / "debug.txt"
    log.write_text("[=ERROR=] something\n", encoding="utf-8")
    rc = _debugcli.main(["baseline", str(log), "--dest", str(tmp_path / "arch")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "DEGRADED" not in out
    assert "content fingerprint" in out, out


def test_an_UNRESOLVED_extensions_root_is_degraded_not_fingerprinted(
        tmp_path, monkeypatch, capsys):
    r"""`Path("")` is `Path(".")`, so an unresolved extensions root made the
    content fingerprint describe the CURRENT DIRECTORY as if it were the mod
    install -- and reported degraded: false while doing it.

    MEASURED, control red both ways: run from two directories holding 1 and 3
    subfolders, an unresolved root produced two DIFFERENT content fingerprints
    and enumerated those subfolders as installed mods (1 and 3 detail rows);
    with the root resolved, both cwds produced the SAME fingerprint and 0 rows.

    A baseline exists to say WHICH WORLD a capture was taken in. An unresolved
    root is exactly the case where it cannot, and `degraded` is the field for
    saying so -- it said the opposite, which is worse than refusing.
    """
    monkeypatch.setattr(_debugcli._paths, "game_extensions", lambda: None)
    log = _write(tmp_path)
    out = _debugcli.archive(log, tmp_path / "archive")
    assert out.is_file(), "the log must still be copied -- the archive survives"
    meta = _debugcli.read_archive_meta(out)
    assert meta["degraded"] is True, (
        "a fingerprint taken over the current directory was reported as sound")
    assert "fingerprint" not in meta, (
        "a fingerprint was recorded for a world this capture never looked at")
    assert "extensions root" in meta["degraded_reason"]
    # the parse results do NOT depend on the root, so they must survive
    assert meta["total_errors"] == 4


def test_a_capture_NEVER_records_a_fingerprint_that_depends_on_the_CWD(
        tmp_path, monkeypatch):
    r"""The invariant, stated so that it could actually have failed.

    An earlier draft of this test supplied a resolved extensions root and asserted
    the two fingerprints matched -- which passed before the fix as well as after,
    because with a root resolved the cwd never mattered. It was testing the control
    condition, so it was decoration.

    The condition that was actually broken is the UNRESOLVED root: `Path("")` is
    `Path(".")`, so the walk enumerated whatever directory the command was run from.
    The honest invariant covers both outcomes -- a capture may decline to fingerprint,
    or it may fingerprint the world, but it may never fingerprint the CWD:

        pre-fix : two fingerprints, DIFFERENT (bd308509... vs bb04955c...), plus 1
                  and 3 detail rows naming the cwd's subfolders as installed mods
        post-fix: no fingerprint at all, degraded: true, both times

    so the assertion below goes red on the first and green on the second.
    """
    monkeypatch.setattr(_debugcli._paths, "game_extensions", lambda: None)
    log = _write(tmp_path)
    seen = []
    for name, subdirs in (("a", 1), ("b", 3)):
        here = tmp_path / ("cwd_" + name)
        here.mkdir()
        for i in range(subdirs):
            (here / ("looks_like_a_mod_%d" % i)).mkdir()
        monkeypatch.chdir(here)
        meta = _debugcli.read_archive_meta(
            _debugcli.archive(log, tmp_path / ("arch_" + name)))
        seen.append(meta.get("fingerprint", {}).get("content"))
    assert seen[0] == seen[1], (
        "the current directory leaked into the content fingerprint: two captures of "
        "one world, taken from different directories, disagree about which world "
        "they are from: %s" % (seen,))


# --- crosscheck's own empty comparison (v3.1.0 release review, B2, PRE-ARC) --------


#: The file SAMPLE's two E/F lines name. The validator always sets `Finding.vpath`;
#: crosscheck keys on file + selector (AUDIT-2026-09-24 RT-1), so the fixture must too.
_WARES = "libraries/wares.xml"


def _cc(monkeypatch, tmp_path, log_text, findings, degraded=()):
    """Drive `crosscheck` over a synthetic log and a synthetic validator report."""
    log = tmp_path / "debug.txt"
    log.write_text(log_text, encoding="utf-8")
    mod = tmp_path / "modA"
    mod.mkdir(exist_ok=True)
    (mod / "content.xml").write_text('<content id="modA" version="100"/>', encoding="utf-8")
    from x4validate import _check
    rep = _R(list(findings))
    rep.degraded = list(degraded)
    monkeypatch.setattr(_check, "validate", lambda *a, **k: rep)
    return _debugcli.main(["crosscheck", str(mod), str(log)])


class _Skip:
    def __init__(self, what, why):
        self.what, self.why = what, why


def test_NOTHING_COMPARED_is_a_non_answer_not_agreement(monkeypatch, tmp_path, capsys):
    """Pre-fix: "agreed: 0 / predicted only: 0 / OBSERVED ONLY: 0" and rc 0, which
    reads exactly like a mod the engine and the validator concur on. It is equally
    what a log from a session where the mod never loaded looks like."""
    rc = _cc(monkeypatch, tmp_path, "nothing about modA in here\n", [])
    assert rc == 2
    assert "REFUSING" in capsys.readouterr().err


def test_a_DEGRADED_prediction_set_cannot_produce_AGREEMENT(monkeypatch, tmp_path, capsys):
    """The command printed "'predicted' is not a complete prediction" and then
    returned the code that means "we agree". A check that did not execute cannot
    have contributed the predictions that would fill OBSERVED ONLY -- the one
    bucket crosscheck exists for."""
    rc = _cc(monkeypatch, tmp_path, SAMPLE,
             [_F("sel", "<replace> sel matched nothing: //wares/ware[@id='x']/owner/@faction", vpath=_WARES),
              _F("sel", "<add> sel matched nothing: //wares/ware[@id='y']", vpath=_WARES)],
             degraded=[_Skip("ware-reference checks", "an overlay would not parse")])
    assert rc == 3
    assert "DEGRADED" in capsys.readouterr().err


def test_a_REAL_agreement_over_a_COMPLETE_prediction_set_is_still_rc0(monkeypatch,
                                                                      tmp_path):
    """The twin. Without it both tests above pass on a command that never returns 0."""
    rc = _cc(monkeypatch, tmp_path, SAMPLE,
             [_F("sel", "<replace> sel matched nothing: //wares/ware[@id='x']/owner/@faction", vpath=_WARES),
              _F("sel", "<add> sel matched nothing: //wares/ware[@id='y']", vpath=_WARES)])
    assert rc == 0


def test_a_REAL_disagreement_still_fails(monkeypatch, tmp_path):
    """The other twin: the command must still be able to find something."""
    rc = _cc(monkeypatch, tmp_path, SAMPLE,
             [_F("sel", "<add> sel matched nothing: /nowhere/at/all")])
    assert rc == 1


def test_op_key_matches_the_engines_extensionless_path_to_the_validators_file():
    """RT-1's twin: keying on the FILE must not break the agreement it exists to find.
    The engine logs `libraries<backslash>wares` (no extension, any case); the
    validator reports `libraries/wares.xml`. Both must produce one key -- and a
    different file must not."""
    sel = "//ware[@id='a']"
    assert _debugcli.op_key("Libraries\\Wares", sel) == _debugcli.op_key("libraries/wares.xml", sel)
    assert _debugcli.op_key("libraries/jobs.xml", sel) != _debugcli.op_key("libraries/wares", sel)


# --- finding 5: crosscheck must refuse on an UNREADABLE log, not read it as clean ------
#
# ParsedLog's own contract (_debuglog.py): "Consumers must refuse on it." triage()
# already does (test_a_missing_log_is_a_NON_ANSWER_not_a_clean_one, above); crosscheck
# read `parsed` straight into `observed_ops` without ever checking `.unreadable`, so an
# unreadable log produced ZERO observed ops -- which prints exactly like a mod the
# engine and the validator agree on, rc 0.

def test_crosscheck_refuses_on_an_UNREADABLE_log_rather_than_reporting_agreement(
        monkeypatch, tmp_path, capsys):
    mod = tmp_path / "modA"
    mod.mkdir()
    (mod / "content.xml").write_text('<content id="modA" version="100"/>', encoding="utf-8")
    from x4validate import _check, _debuglog
    monkeypatch.setattr(_check, "validate", lambda *a, **k: _R([]))
    # A genuinely readable FILE (so the earlier "does this look like a log?" check
    # in _resolve_log passes and this reaches _crosscheck_cmd), but parse_log itself
    # reports the read as having failed -- e.g. a decode error _resolve_log cannot
    # see from is_file() alone.
    log = tmp_path / "debug.txt"
    log.write_text("irrelevant\n", encoding="utf-8")
    monkeypatch.setattr(_debuglog, "parse_log", lambda path: _debuglog.ParsedLog(
        total=0, entries=[], unclassified=[], unreadable="OSError: simulated"))
    rc = _debugcli.main(["crosscheck", str(mod), str(log)])
    err = capsys.readouterr().err
    assert rc == 2, (rc, err)
    assert "unreadable" in err.lower() or "could not" in err.lower(), err
    assert str(log) in err, err


def test_crosscheck_still_compares_normally_when_the_log_IS_readable(monkeypatch, tmp_path,
                                                                      capsys):
    """Twin: the new refusal is keyed on .unreadable specifically -- a readable log
    with zero observed ops still hits the PRE-EXISTING 'NOTHING COMPARED' refusal
    (both rc 2, different reasons), never the new unreadable-log message."""
    rc = _cc(monkeypatch, tmp_path, "nothing about modA in here\n", [])
    err = capsys.readouterr().err
    assert rc == 2 and "REFUSING" in err and "unreadable" not in err.lower(), err


def test_archiving_the_SAME_log_twice_writes_ONE_copy_and_says_so(tmp_path, resolved_extensions,
                                                                 capsys):
    """V8: since 5398907 every run drew a random-suffixed name, so the same debug.txt was
    archived again on every `x4debug baseline` -- an archive of near-duplicates nobody can
    quote from. Deduplicated by CONTENT (sha256 of the bytes), not by name or mtime."""
    log = _write(tmp_path)
    dest = tmp_path / "archive"
    first = _debugcli.archive(log, dest)
    again = _debugcli.archive(log, dest)
    assert again == first
    assert len(list(dest.glob("debug-*.txt"))) == 1
    assert _debugcli.read_archive_meta(first)["sha256"]
    rc = _debugcli.main(["baseline", str(log), "--dest", str(dest)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "already archived" in out and "nothing written" in out.lower(), out
    assert len(list(dest.glob("debug-*.txt"))) == 1


def test_TWIN_a_log_with_DIFFERENT_bytes_is_archived_alongside(tmp_path, resolved_extensions):
    log = _write(tmp_path)
    dest = tmp_path / "archive"
    first = _debugcli.archive(log, dest)
    log.write_text(log.read_text(encoding="utf-8") + "[=ERROR=] 9.0 one more\n", encoding="utf-8")
    second = _debugcli.archive(log, dest)
    assert second != first and len(list(dest.glob("debug-*.txt"))) == 2
