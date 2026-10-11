"""Tests for ask.py's counting and negative-claim contract.

Run:  cd tools\\basex && python -m pytest test_ask.py -q

No BaseX and no JVM required — `run_xq` is monkeypatched, because what is under
test is the counting and the guard, not XQuery evaluation.

Background (audit 2026-08-01, finding F9): "hits" was `len(output_lines)`, which
is unrelated to the number of matches — 847 occurrences across 4 documents
printed as "32 hit(s)". Worse, the zero-result guard keyed off that same empty
line list, so `count(...)` returning 0 emitted the single line "0", the guard
never ran, and a zero result rendered as "1 hit(s)" with exit 0 — the exact false
positive this tool exists to prevent, reached by its most natural phrasing.
"""
from __future__ import annotations

import pytest

import ask


@pytest.mark.parametrize('attr', ['name[false()]', 'name | //*', 'a/b', 'a::b', 'a:b:c', ' x', 'Q{urn:x}a'])
def test_attribute_syntax_is_refused_before_query(monkeypatch, capsys, attr):
    # A malformed attribute used to become executable XQuery and certify a zero.
    _complete_zero(monkeypatch)
    assert ask.main(['attr', attr]) == 2
    assert 'attribute' in capsys.readouterr().err


@pytest.mark.parametrize('attr', ['name', 'xml:lang', 'énergie', 'foo-bar_2'])
def test_valid_attribute_names_still_execute(monkeypatch, attr):
    _positive(monkeypatch)
    assert ask.main(['attr', attr]) == 0


@pytest.mark.parametrize('predicate', [
    'if (true()) then 1 else 2', 'switch (1) case 1 return 1 default return 2',
    '1 + 0', '(1)', 'count(.//ware)', 'xs:integer(1)',
])
def test_numeric_predicate_zero_never_certified(monkeypatch, capsys, predicate):
    _complete_zero(monkeypatch)
    q = f"collection('x4raw')[{predicate}]//cue[@name='AuditCue']"
    assert ask.main(['xq', q]) == 4
    assert 'NEGATIVE CONFIRMED' not in capsys.readouterr().out


@pytest.mark.parametrize('q', [
    "collection('x4raw')//ware[@id='it''s']",
    "(db:get('x4raw'))[exists(.//ware) and not(empty(.//cue))]//ware[@n >= -1]",
    "(: outer (: nested :) :) fn:collection('x4raw')//ware[@x = '(: string :)']",
    "collection('x4raw')//ware[(@x='a' or @x='b') and .//price]",
])
def test_restricted_content_grammar_positive_controls(monkeypatch, capsys, q):
    _complete_zero(monkeypatch)
    assert ask.main(['xq', q]) == 0
    assert 'NEGATIVE CONFIRMED' in capsys.readouterr().out


@pytest.fixture(autouse=True)
def _preflight_is_satisfied(monkeypatch):
    """Neutralise the environment preflight for every test in this module.

    `ask.main()` checks the jar and the database BEFORE running a query (F47), so
    without this, every test that goes through `main()` exits 2 on a machine with
    no index built -- and passes on a machine that has one.

    MEASURED 2026-08-25, and the reason this exists: 9 of these tests were green
    on the development machine and red on a fresh checkout of the same commit.
    Their verdict was coming from whether a multi-GB database happened to exist,
    not from anything in the fixture. A test whose answer depends on mutable state
    outside itself is not testing what its name says.

    Neutralising the preflight here is only safe because the wiring is pinned
    separately by `test_the_preflight_is_actually_wired_into_main` below. Without
    that companion this fixture would be a way to silently delete a guard.
    """
    monkeypatch.setattr(ask.preflight, "check", lambda *a, **k: [])
    # Git Bash exports MSYSTEM to every native child, pytest included, and ask.py now reads
    # it. Unset for every test so a verdict never depends on which shell ran the suite;
    # the Git Bash tests below set it explicitly.
    monkeypatch.delenv("MSYSTEM", raising=False)
    monkeypatch.delenv("MSYS_NO_PATHCONV", raising=False)
    monkeypatch.delenv("MSYS2_ARG_CONV_EXCL", raising=False)


def test_the_preflight_is_actually_wired_into_main(monkeypatch, capsys):
    """The falsification twin for the fixture above.

    If `main()` ever stopped consulting the preflight, every other test here would
    still pass -- they stub it out. This one fails instead.
    """
    monkeypatch.setattr(ask.preflight, "check",
                        lambda *a, **k: ["synthetic problem: the environment is not ready"])
    rc = ask.main(["xq", "1+1", "--db", "x4raw"])
    assert rc == 2, "an unusable environment must refuse with 2 (not configured), never 0 or 1"
    assert "synthetic problem" in capsys.readouterr().err


def _fake_basex(monkeypatch, payload: str, *, wrap_fails: bool = False):
    """Stand in for BaseX. Honours the count wrapper the way BaseX would."""
    def fake(q: str) -> str:
        if ask._SEP in q:
            if wrap_fails:
                raise RuntimeError("[XPST0003] Static error: simulated prolog clash")
            items = [] if not payload else payload.split("\n")
            return f"{len(items)}\n{ask._SEP}\n{payload}"
        return payload
    monkeypatch.setattr(ask, "run_xq", fake)


# --- counting -----------------------------------------------------------------

def test_item_count_is_items_not_lines(monkeypatch):
    _fake_basex(monkeypatch, "a\nb\nc")
    out, n = ask.run_counted("//whatever")
    assert n == 3 and out == "a\nb\nc"


def test_empty_result_counts_zero(monkeypatch):
    _fake_basex(monkeypatch, "")
    _, n = ask.run_counted("//nothing")
    assert n == 0


def test_uncompilable_wrapper_reports_unknown_not_a_wrong_number(monkeypatch):
    """A count we could not take must be None so the caller says so, rather than
    quoting the line count as though it meant something."""
    _fake_basex(monkeypatch, "a\nb", wrap_fails=True)
    out, n = ask.run_counted("declare namespace x = 'urn:x'; //a")
    assert n is None and out == "a\nb"


def test_separator_is_a_legal_xml_character():
    """U+0001 is not a legal XML character: using it made the wrapper fail to
    compile, which silently cost the count on every single query."""
    assert ask._SEP.isprintable() and ask._SEP not in ("", " ")


# --- the negative-claim contract ---------------------------------------------

def test_zero_count_query_refuses_to_render_a_negative(monkeypatch, capsys):
    """The headline: count() of nothing must NOT read as a hit, and must not
    quietly bypass the coverage guard."""
    _fake_basex(monkeypatch, "0")
    rc = ask.main(["xq", "count(//nothing)"])
    text = capsys.readouterr().out
    assert rc == 4, "must refuse, not return success"
    assert "NOT A NEGATIVE FINDING" in text
    assert "one atomic value, not one" in text


def test_empty_sequence_still_reaches_the_coverage_guard(monkeypatch, capsys):
    """The other side: the guard must still fire for a genuinely empty result."""
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
    })
    # Staleness is a SEPARATE gate (added 2026-08-13) and would otherwise refuse
    # here on the real on-disk fingerprint. Pin it so this test keeps testing the
    # coverage guard rather than whatever the local databases happen to be.
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["xq", "collection('x4raw')//nothing"])
    text = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED over 100 of 100" in text


def test_a_real_nonzero_result_is_not_second_guessed(monkeypatch, capsys):
    """A count() returning a REAL number is a positive answer; only zero is the
    trap. Pins that the refusal is not fired indiscriminately."""
    _fake_basex(monkeypatch, "42")
    rc = ask.main(["xq", "count(//something)"])
    text = capsys.readouterr().out
    assert rc == 0 and "NOT A NEGATIVE FINDING" not in text


def test_missing_coverage_still_refuses(monkeypatch, capsys):
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {})
    rc = ask.main(["xq", "//nothing"])
    assert rc == 4 and "No coverage report" in capsys.readouterr().out


# --- staleness: an index that no longer describes the world -------------------

def _stale(monkeypatch, fresh: bool):
    """Force ask.py's staleness verdict without touching the real DBs."""
    import staleness
    monkeypatch.setattr(
        ask, "staleness_verdict",
        lambda db: staleness.Verdict(fresh, [] if fresh else ["engine changed: test"], db))


def test_a_stale_index_warns_even_on_a_POSITIVE_result(monkeypatch, capsys):
    """The user's requirement: warn every time it is run, until rebuilt. A
    positive answer from a stale index is still an answer about a world that has
    moved on -- silence here is what let x4eff serve 858 wrong values for 11 days."""
    _fake_basex(monkeypatch, "hit-one\nhit-two")
    _stale(monkeypatch, fresh=False)
    rc = ask.main(["xq", "//x"])
    cap = capsys.readouterr()          # capture ONCE: readouterr() resets the buffers,
    out = cap.out + cap.err            # so calling it twice discards stderr
    assert "STALE INDEX" in out, "a stale index answered without saying so"
    assert rc == 0, "a positive result still stands; it is warned about, not withheld"


def test_a_stale_index_REFUSES_to_render_a_negative(monkeypatch, capsys):
    """Same contract as missing coverage: a zero-result needs a denominator, and
    a denominator from a superseded world is not one."""
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
    })
    _stale(monkeypatch, fresh=False)
    rc = ask.main(["xq", "//x"])
    out = capsys.readouterr().out
    assert rc == 4, "a negative from a stale index must not be admissible"
    assert "NOT A NEGATIVE FINDING" in out


def test_a_fresh_index_says_nothing_extra(monkeypatch, capsys):
    """The banner must not become background noise, or it stops being read."""
    _fake_basex(monkeypatch, "hit-one")
    _stale(monkeypatch, fresh=True)
    ask.main(["xq", "//x"])
    assert "STALE INDEX" not in capsys.readouterr().out


def test_a_query_naming_a_DIFFERENT_collection_than_db_is_refused(monkeypatch, capsys):
    """`--db` picks the coverage AND freshness denominator; the query text picks
    what is actually searched. When they disagree the answer is scored against
    the wrong world -- `ask.py`'s own docstring says the two databases "must never
    share" a denominator, and this is how they silently did.

    Found end-to-end 2026-08-13: `ask.py xq "count(collection('x4eff')//macro)"`
    ran against x4eff while reporting "in x4raw", and a stale x4eff produced no
    warning because x4raw was fresh."""
    _fake_basex(monkeypatch, "5")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4raw", "xq", "count(collection('x4eff')//macro)"])
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert rc == 2, "a denominator/query mismatch must not answer"
    assert "x4eff" in text and "--db" in text


def test_a_query_naming_the_SAME_collection_is_fine(monkeypatch, capsys):
    _fake_basex(monkeypatch, "5")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4eff", "xq", "count(collection('x4eff')//macro)"])
    assert rc == 0


def _basex_raises(monkeypatch, message: str):
    """BaseX itself failing, the way the real jar reports it."""
    def boom(_xquery):
        raise RuntimeError(message)
    monkeypatch.setattr(ask, "run_xq", boom)


_CONTEXT_UNDEFINED = ("Stopped at /basex/, 1/8:" + chr(10)
                      + "[XPDY0002] .: Context value is undefined.")


def test_a_CONTEXT_UNDEFINED_error_names_the_cause_and_the_cure(monkeypatch, capsys):
    """MEASURED 2026-09-19: `xq --file` with `count(//ware)` printed exactly this and
    exit 2 -- true, and naming neither the cause (an xq query addresses its own
    database; there is no implicit context node) nor the cure. A cold, docs-only agent
    reached for `//ware` because CLAUDE.md sends you to ask.py without saying a query
    names its own collection. Same shape as the unbuilt-DB translation already here:
    BaseX's words are kept as evidence, ours add what to do."""
    _basex_raises(monkeypatch, _CONTEXT_UNDEFINED)
    rc = ask.main(["--db", "x4eff", "xq", "count(//ware)"])
    text = "".join(capsys.readouterr())
    assert rc == 2
    assert "[XPDY0002]" in text, "BaseX's own words must survive as evidence"
    assert "names no database" in text, text
    assert "collection('x4eff')" in text, "the cure must name the --db collection"


def test_TWIN_an_UNRELATED_BaseX_error_is_not_dressed_up_as_this_one(monkeypatch, capsys):
    """The guard reads BaseX's verdict, so it must not answer for a different one."""
    _basex_raises(monkeypatch, "Stopped at /basex/, 1/3:" + chr(10)
                  + "[XPST0003] Unexpected end of query.")
    rc = ask.main(["--db", "x4eff", "xq", "count(collection('x4eff')//ware"])
    text = "".join(capsys.readouterr())
    assert rc == 2
    assert "XPST0003" in text
    assert "names no database" not in text, text


def test_TWIN_a_query_that_needs_NO_database_still_answers(monkeypatch, capsys):
    """`1+1` is legal and names no database, and an earlier version of this fix refused
    it by reading literals instead of BaseX's verdict -- 22 tests went red, which is how
    the over-firing was caught. It must still answer."""
    _fake_basex(monkeypatch, "2")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4eff", "xq", "1+1"])
    text = "".join(capsys.readouterr())
    assert rc == 0, text
    assert "names no database" not in text


# --- freshness that cannot be DETERMINED (distinct from stale) ----------------

def _undeterminable(monkeypatch):
    """Make the freshness check itself fail, the way an unconfigured machine does.

    Patches the REAL seam (`staleness._defaults`) rather than `ask.staleness_verdict`,
    because the latter is the function under test -- patching it would prove only
    that the test can patch things.
    """
    import staleness

    def boom():
        raise staleness.EngineUnavailable(
            "cannot resolve reference and extensions")
    monkeypatch.setattr(staleness, "_defaults", boom)


def test_undeterminable_freshness_is_UNKNOWN_not_a_traceback(monkeypatch, capsys):
    """MEASURED 2026-08-24 on a proven-cold checkout: raw traceback, rc **1**.

    rc 1 is the damaging part. In this toolkit it means "the thing you asked about
    has findings"; the truth was "this toolkit is not set up", and those demand
    opposite responses. Same defect F39 removed from the CLIs, still alive here.

    A positive result still STANDS -- the query ran and BaseX answered. What is
    unknown is whether the index still describes the world, and the honest move is
    to say so rather than to withhold a real answer or to imply a freshness nobody
    established.
    """
    _undeterminable(monkeypatch)
    _fake_basex(monkeypatch, "hit-one\nhit-two")
    rc = ask.main(["xq", "//x"])
    cap = capsys.readouterr()
    both = cap.out + cap.err
    assert "Traceback" not in both, "an unconfigured machine got a raw traceback"
    assert rc == 0, "a positive result still stands; freshness is a caveat, not a veto"
    assert "UNKNOWN" in both.upper(), "silence would imply a freshness nobody established"


def test_undeterminable_freshness_REFUSES_to_render_a_negative(monkeypatch, capsys):
    """Same contract as stale and as missing coverage: a zero needs a denominator,
    and a denominator whose currency cannot be established is not one."""
    _undeterminable(monkeypatch)
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
    })
    rc = ask.main(["xq", "//x"])
    cap = capsys.readouterr()
    assert "Traceback" not in cap.out + cap.err
    assert rc == 4, "a negative from an index of unknown currency must not be admissible"


# --- the denominator FLOOR (Track 2 audit, 2026-09-06) ------------------------
#
# The three guards above this point ask "is there a coverage report", "does it
# claim to support a negative", and "is it fresh". None of them asks whether the
# denominator is a NUMBER GREATER THAN ZERO -- so a report that answered yes to
# all three published the bare zero this tool exists to refuse, wearing the one
# sentence that means it was checked.
#
# Both shapes are reachable, not hypothetical:
#   * None -- `cov.get("indexed", {}).get("total")` yields None whenever the key
#     is absent, and nothing downstream re-checks it.
#   * 0    -- coverage.py validates --reference/--extensions for NON-EMPTINESS
#     and never for EXISTENCE, and count_disk_xml returns 0 for a root that is
#     not a directory. A typo'd path therefore publishes
#     {"expected": {"total": 0}, "indexed": {"total": 0}, "status": "complete",
#      "supports_negative_claim": true}, which is precisely this input.

def test_a_ZERO_denominator_is_refused_not_confirmed(monkeypatch, capsys):
    """`NEGATIVE CONFIRMED over 0 of 0 documents (complete).` with rc 0.

    Nothing was indexed, so "zero hits" cannot be distinguished from "we never
    looked" -- the founding distinction of this tool, inverted by its own verdict
    line.
    """
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 0}, "expected": {"total": 0}, "unparseable": [],
    })
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["xq", "//nothing"])
    text = capsys.readouterr().out
    assert "NEGATIVE CONFIRMED" not in text, (
        "a denominator of zero was rendered as a confirmed negative")
    assert rc == 4 and "NOT A NEGATIVE FINDING" in text


def test_an_ABSENT_denominator_is_refused_not_confirmed(monkeypatch, capsys):
    """The other shape: `NEGATIVE CONFIRMED over None of None documents`.

    A coverage report missing its `indexed`/`expected` keys reaches the verdict
    line with None on both sides, and `(expected or 0) - (indexed or 0)` quietly
    turns that into 0, so the sentence reads `(complete)`.
    """
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "unparseable": [],
    })
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["xq", "//nothing"])
    text = capsys.readouterr().out
    assert "NEGATIVE CONFIRMED" not in text, (
        "an absent denominator was rendered as a confirmed negative")
    assert "None of None" not in text
    assert rc == 4 and "NOT A NEGATIVE FINDING" in text


def test_the_floor_does_not_over_fire_on_a_REAL_denominator(monkeypatch, capsys):
    """The falsification twin for the two above.

    A floor that refused everything would make them pass while deleting the
    feature. One real document is enough to support a negative, and must still
    read as one.
    """
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 1}, "expected": {"total": 1}, "unparseable": [],
    })
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["xq", "collection('x4raw')//nothing"])
    text = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED over 1 of 1" in text


# --- the cross-DB guard knows only ONE spelling (Track 2 audit, 2026-09-06) ---

def test_a_foreign_db_named_via_db_get_is_refused(monkeypatch, capsys):
    """`collection('x4eff')` is not the only way to name a database.

    BaseX addresses one directly with `db:get('<name>')` (`db:open` before BaseX
    10; the vendored jar is 12.4). The guard's regex matched `collection(...)`
    only, so a `db:get('x4eff')` query ran against x4eff while being scored
    against x4raw's coverage AND x4raw's freshness -- the exact failure the guard
    was written for in the first place, reached through a different spelling.
    """
    _fake_basex(monkeypatch, "5")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4raw", "xq", "db:get('x4eff')//macro"])
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert rc == 2, "a db:get() naming another database was answered, not refused"
    assert "x4eff" in text and "--db" in text


def test_the_legacy_db_open_spelling_is_refused_too(monkeypatch, capsys):
    """BaseX 10 renamed db:open to db:get. Habit and older notes still use the
    old name, and the guard should not depend on which one the caller reached for."""
    _fake_basex(monkeypatch, "5")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4raw", "xq", "db:open('x4eff')//macro"])
    assert rc == 2


def test_db_get_naming_the_SAME_database_is_fine(monkeypatch, capsys):
    """The falsification twin: a guard that refused every db:get() would make the
    two above pass while breaking the ordinary case."""
    _fake_basex(monkeypatch, "5")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4raw", "xq", "db:get('x4raw')//macro"])
    assert rc == 0, "a db:get() naming the queried database must not be refused"


# --- an unavailable item count cannot confirm anything ------------------------

def test_an_UNAVAILABLE_item_count_cannot_confirm_a_negative(monkeypatch, capsys):
    """`run_counted` returns (output, None) when its count wrapper will not
    compile -- the documented case being a query carrying its own prolog, i.e.
    any query needing a namespace declaration or a user-defined function.

    The positive branch honestly labels the number `output line(s), item count
    unavailable`. The zero branch dropped that caveat entirely -- `unit` is
    computed and never used there -- and issued the guarantee anyway. MEASURED
    against real BaseX: three genuine matches, each serializing to a zero-length
    string, printed `NEGATIVE CONFIRMED over 2 of 2 documents (complete).` with
    rc 0, while the identical query without the prolog reported `3 item(s)`.

    An empty SERIALIZATION is not an empty SEQUENCE, and this is exactly what
    `run_counted`'s own docstring already required of its caller: "The caller must
    then say the count is unavailable rather than quote the line count as though
    it were meaningful."
    """
    _fake_basex(monkeypatch, "", wrap_fails=True)
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
    })
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["xq", "//nothing"])
    text = capsys.readouterr().out
    assert "NEGATIVE CONFIRMED" not in text, (
        "a negative was confirmed while the item count was unavailable")
    assert rc == 4 and "NOT A NEGATIVE FINDING" in text
    # This names a phrase the output does NOT line-wrap. The first draft
    # asserted a two-word phrase the message splits across two lines, so it
    # failed against a correct fix -- the checker, not the subject.
    assert "serialization is not an empty sequence" in text


def test_a_negative_over_a_tree_with_SKIPPED_OVERLAYS_is_not_called_complete(monkeypatch, capsys):
    """`missing` is a DOCUMENT-COUNT deficit, and a malformed overlay does not reduce
    the document count -- the document still exists, merely without that overlay
    applied. So `missing` was 0, the exclusions never printed, and the sentence ended
    "(complete)" over a tree that had dropped overlays.

    coverage.py publishes `negative_claim_excludes` precisely so a caller can "RENDER
    the caveat instead of reading a bare boolean", and no shipped caller asked. A
    disclosure with no reader is decoration.
    """
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100},
        "unparseable": ["mod_a/libraries/wares.xml: malformed XML"],
        "negative_claim_excludes": {
            "vpaths_without_effective_tree": 2, "unparseable_overlays": 1},
    })
    _stale(monkeypatch, fresh=True)
    ask.main(["xq", "collection('x4raw')//nothing"])
    text = capsys.readouterr().out
    assert "(complete)" not in text, (
        "a tree excluding 1 unparseable overlay and 2 unbuilt vpaths was reported as "
        "complete, so a zero reads as a negative over the whole corpus:\n%s" % text)
    assert "NOT COMPLETE" in text and "1 overlay" in text and "2 vpath" in text, (
        "the exclusions coverage.json already carries were not rendered:\n%s" % text)


def test_a_genuinely_complete_tree_still_says_complete(monkeypatch, capsys):
    """The twin. Without it the fix is satisfied by never saying complete at all,
    which would make the tool useless for the claim it exists to support."""
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
        "negative_claim_excludes": {
            "vpaths_without_effective_tree": 0, "unparseable_overlays": 0},
    })
    _stale(monkeypatch, fresh=True)
    ask.main(["xq", "collection('x4raw')//nothing"])
    text = capsys.readouterr().out
    assert "(complete)" in text, (
        "a tree with nothing excluded must still support the negative claim:\n%s" % text)
    assert "NOT COMPLETE" not in text, text


# --- what a result MEANS (cold E2E agent, 2026-09-14) ---------------------------
# A docs-only agent read "3541 item(s) in x4eff." without knowing what an item is, and noted
# that leaving off --db silently searches the files AS WRITTEN rather than the live tree.

def _positive(monkeypatch):
    _fake_basex(monkeypatch, "libraries/wares.xml  <ware>")
    _stale(monkeypatch, fresh=True)


def test_a_positive_result_says_what_an_ITEM_is(monkeypatch, capsys):
    _positive(monkeypatch)
    assert ask.main(["refs", "energycells", "--db", "x4eff"]) == 0
    out = capsys.readouterr().out
    assert "1 item(s) in x4eff." in out, "the count line itself must not change"
    assert "not a count of files or entities" in out, out


def test_an_OMITTED_db_is_named_as_the_as_written_default(monkeypatch, capsys):
    _positive(monkeypatch)
    assert ask.main(["refs", "energycells"]) == 0
    out = capsys.readouterr().out
    assert "1 item(s) in x4raw." in out
    assert "--db x4eff" in out and "AS WRITTEN" in out, out


def test_TWIN_an_EXPLICIT_db_gets_no_default_hint(monkeypatch, capsys):
    """The twin, per database: naming --db, even `--db x4raw` itself, is a choice, and
    re-explaining it on every run is noise that teaches the reader to skip the output."""
    for db in ("x4raw", "x4eff"):
        _positive(monkeypatch)
        assert ask.main(["refs", "energycells", "--db", db]) == 0
        assert "AS WRITTEN" not in capsys.readouterr().out, db


def test_help_states_the_db_default_and_what_each_db_is(capsys):
    import pytest
    with pytest.raises(SystemExit) as exc:
        ask.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # "effective" alone is already in the module docstring --help prints (review, 2026-09-14).
    assert "default: x4raw" in out and "effective merged tree" in out, out


def test_TWIN_an_xq_query_gets_no_default_hint(monkeypatch, capsys):
    """An `xq` query names its own collection, and the foreign-collection guard refuses a
    --db that disagrees -- so "add --db x4eff" is advice that fails if followed (review,
    2026-09-14). The hint belongs to `refs` and `attr`, where --db alone picks the database."""
    _positive(monkeypatch)
    assert ask.main(["xq", "collection('x4raw')//ware"]) == 0
    assert "AS WRITTEN" not in capsys.readouterr().out


# --- Git Bash rewrites // in arguments (cold E2E agent, 2026-09-14) -----------------------
# MEASURED: from Git Bash, `ask.py xq 'count(collection("x4raw")//ware)'` reached Python as
# `count(collection("x4raw")/ware)`, and `'//ware'` as `/ware` -- MSYS path conversion, before
# any of this code runs. The coverage guard then certified "NEGATIVE CONFIRMED" rc 0 over a query
# nobody typed. The same query from PowerShell, or with MSYS_NO_PATHCONV=1, counted 14,068.

def _complete_zero(monkeypatch):
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
    })
    _stale(monkeypatch, fresh=True)


def test_a_ZERO_from_an_argv_xq_under_Git_Bash_is_NOT_a_negative(monkeypatch, capsys):
    _complete_zero(monkeypatch)
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    rc = ask.main(["xq", "/ware"])
    out = capsys.readouterr().out
    assert rc == 4, out
    assert "NEGATIVE CONFIRMED" not in out and "--file" in out, out


def test_TWIN_the_same_zero_read_from_a_query_FILE_is_confirmed(monkeypatch, capsys, tmp_path):
    _complete_zero(monkeypatch)
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    q = tmp_path / "q.xq"
    q.write_text("collection('x4raw')//nothing", encoding="utf-8")
    rc = ask.main(["xq", "--file", str(q)])
    out = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED over 100 of 100" in out, out


def test_TWIN_the_same_argv_zero_outside_Git_Bash_is_confirmed(monkeypatch, capsys):
    _complete_zero(monkeypatch)
    rc = ask.main(["xq", "collection('x4raw')//nothing"])
    out = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED over 100 of 100" in out, out


def test_TWIN_refs_under_Git_Bash_is_unaffected(monkeypatch, capsys):
    """refs/attr build their query in Python from an id or attribute name; no // crosses argv."""
    _complete_zero(monkeypatch)
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    rc = ask.main(["refs", "nothing_by_this_id", "--db", "x4raw"])
    out = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED" in out, out


def test_a_POSITIVE_argv_xq_under_Git_Bash_shows_the_query_AS_RECEIVED(monkeypatch, capsys):
    """A rewritten query can also return a wrong NON-zero answer, so every argv xq result under
    Git Bash shows the query exactly as Python received it."""
    _positive(monkeypatch)
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    assert ask.main(["xq", "collection('x4raw')/ware"]) == 0
    out = capsys.readouterr().out
    assert "as received" in out and "collection('x4raw')/ware" in out, out


def test_a_query_file_and_an_argv_query_together_is_a_usage_error(tmp_path):
    import pytest
    q = tmp_path / "q.xq"
    q.write_text("//x", encoding="utf-8")
    for argv in (["xq", "//x", "--file", str(q)], ["xq"], ["refs", "x", "--file", str(q)], ["refs"]):
        with pytest.raises(SystemExit) as exc:
            ask.main(argv)
        assert exc.value.code == 2, argv


def test_an_UNREADABLE_query_file_refuses_rc2(tmp_path, capsys):
    rc = ask.main(["xq", "--file", str(tmp_path / "absent.xq")])
    assert rc == 2 and "absent.xq" in capsys.readouterr().err


# --- second review of the Git Bash guard (2026-09-14) -------------------------------------

def test_a_BLANK_or_COMMENT_ONLY_query_is_refused_not_confirmed(monkeypatch, capsys, tmp_path):
    """An empty query returns an empty sequence, and the zero guard certified it: a truncated or
    unsaved query file gave "NEGATIVE CONFIRMED" rc 0 (measured with the real jar by the review)."""
    import pytest
    for n, body in enumerate(["", "  \n\t\n", "(: nothing :)", "(: outer (: nested :) outer :)"]):
        _complete_zero(monkeypatch)
        q = tmp_path / ("q%d.xq" % n)
        q.write_text(body, encoding="utf-8")
        rc = ask.main(["xq", "--file", str(q)])
        cap = capsys.readouterr()
        assert rc == 2 and "NEGATIVE CONFIRMED" not in cap.out and "empty" in cap.err, (body, cap)
    _complete_zero(monkeypatch)
    assert ask.main(["xq", "(: just a comment :)"]) == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        ask.main(["refs", "  "])
    assert exc.value.code == 2


def test_TWIN_a_comment_beside_a_real_expression_still_runs(monkeypatch, capsys, tmp_path):
    _complete_zero(monkeypatch)
    q = tmp_path / "q.xq"
    q.write_text("(: why this query exists :) collection('x4raw')//nothing", encoding="utf-8")
    rc = ask.main(["xq", "--file", str(q)])
    assert rc == 0 and "NEGATIVE CONFIRMED" in capsys.readouterr().out


def test_a_UTF8_BOM_in_a_query_file_is_not_sent_to_BaseX(monkeypatch, capsys, tmp_path):
    """Notepad and Windows PowerShell 5.1 write a BOM; BaseX rejects U+FEFF as a context error,
    so the user saw a BaseX failure naming the wrong cause."""
    _complete_zero(monkeypatch)
    seen = []
    inner = ask.run_xq
    monkeypatch.setattr(ask, "run_xq", lambda q: (seen.append(q), inner(q))[1])
    q = tmp_path / "bom.xq"
    q.write_bytes(b"\xef\xbb\xbfcollection('x4raw')//nothing")
    assert ask.main(["xq", "--file", str(q)]) == 0
    assert seen and all("\ufeff" not in s for s in seen), seen


def test_an_EMPTY_but_DEFINED_MSYSTEM_still_refuses(monkeypatch, capsys):
    """MEASURED 2026-09-20 from Git Bash: with `MSYSTEM=` (defined, empty) the MSYS runtime STILL
    rewrites argv -- `'//ware'` arrived as `/ware`. The guard read `env.get("MSYSTEM") or None`,
    so an empty value folded to None and the refusal switched OFF while the rewrite continued:
    a certified NEGATIVE CONFIRMED over a query nobody typed, which is F122 reopened. PRESENCE
    is the signal, not truthiness."""
    _fake_basex(monkeypatch, "0")
    _stale(monkeypatch, fresh=True)
    monkeypatch.setenv("MSYSTEM", "")
    rc = ask.main(["--db", "x4raw", "xq", "count(collection('x4raw')//nothing)"])
    text = "".join(capsys.readouterr())
    assert rc == 4, text
    assert "NEGATIVE CONFIRMED" not in text


def test_TWIN_an_EMPTY_but_DEFINED_conversion_switch_turns_the_refusal_OFF(monkeypatch, capsys):
    """The other direction, same semantics: MSYS treats MSYS_NO_PATHCONV as SET when merely
    defined, so an empty value means conversion is OFF and a zero is a real negative. Reading it
    with bool() called that "on" and refused a legal zero."""
    _fake_basex(monkeypatch, "")
    _stale(monkeypatch, fresh=True)
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
    })
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("MSYS_NO_PATHCONV", "")
    # NOT a count() query: a count()-shaped zero is refused on its own axis, whatever the shell.
    rc = ask.main(["--db", "x4raw", "xq", "collection('x4raw')//nothing"])
    text = "".join(capsys.readouterr())
    assert rc == 0, text
    assert "NEGATIVE CONFIRMED" in text


def test_a_FAILED_argv_query_under_Git_Bash_still_shows_what_arrived(monkeypatch, capsys):
    """The CHANGELOG promises "every argument-query result shows the query as received", but the
    BaseX-ERROR path had no such line -- and a leading `/` rewritten to `C:/Program Files/Git/...`
    is the MSYS outcome most likely to produce an error rather than a zero."""
    def boom(_xquery):
        raise RuntimeError("Stopped at /basex/, 1/3:" + chr(10)
                           + "[XPST0003] Unexpected end of query: '<a rewritten path>'.")
    monkeypatch.setattr(ask, "run_xq", boom)
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    rc = ask.main(["--db", "x4raw", "xq", "/ware"])
    text = "".join(capsys.readouterr())
    assert rc == 2
    assert "as received" in text, text
    assert "--file" in text, text


def test_conversion_switched_OFF_turns_the_Git_Bash_refusal_off(monkeypatch, capsys):
    """MSYS_NO_PATHCONV, or MSYS2_ARG_CONV_EXCL=*, leaves `//` intact (measured), so an argument
    query is what was typed and its zero is a real negative."""
    for var, value in (("MSYS_NO_PATHCONV", "1"), ("MSYS2_ARG_CONV_EXCL", "*")):
        _complete_zero(monkeypatch)
        monkeypatch.setenv("MSYSTEM", "MINGW64")
        monkeypatch.delenv("MSYS_NO_PATHCONV", raising=False)
        monkeypatch.delenv("MSYS2_ARG_CONV_EXCL", raising=False)
        monkeypatch.setenv(var, value)
        rc = ask.main(["xq", "collection('x4raw')//nothing"])
        out = capsys.readouterr().out
        assert rc == 0 and "NEGATIVE CONFIRMED" in out, (var, out)


def test_TWIN_a_PARTIAL_conversion_exclusion_still_refuses(monkeypatch, capsys):
    """MSYS2_ARG_CONV_EXCL names PREFIXES to leave alone; only `*` excludes every argument."""
    _complete_zero(monkeypatch)
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("MSYS2_ARG_CONV_EXCL", "--file")
    assert ask.main(["xq", "//nothing"]) == 4


def test_TWIN_no_as_received_notice_for_a_query_FILE_under_Git_Bash(monkeypatch, capsys, tmp_path):
    _positive(monkeypatch)
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    q = tmp_path / "q.xq"
    q.write_text("collection('x4raw')/ware", encoding="utf-8")
    assert ask.main(["xq", "--file", str(q)]) == 0
    assert "as received" not in capsys.readouterr().out


def test_TWIN_no_as_received_notice_for_an_argv_query_outside_Git_Bash(monkeypatch, capsys):
    _positive(monkeypatch)
    assert ask.main(["xq", "collection('x4raw')/ware"]) == 0
    assert "as received" not in capsys.readouterr().out


# --- a zero is certified only when NOTHING in the query can narrow the documents -------------
#
# History. fu-ask (2026-09-26) refused a query that tested a document-identity function in a
# predicate, a `where` clause or a comparison -- a list of narrowing SHAPES. The v3.3.0 release
# review then walked 12 rewrites past it (a `let` then a `where` on the variable, an arrow into
# contains(), tokenize(...)[3], string-length(...) < 30, filter() with a callback, switch,
# db:node-pre, a positional predicate, subsequence(), a `collection#1` function reference, an
# EQName spelling); 11 printed "NEGATIVE CONFIRMED over the whole database", rc 0. A list of
# shapes cannot converge. The rule is now about TOKENS: if the query (comments removed, string
# literal contents blanked) contains anything that can select documents by WHERE they are --
# identity, position, node order, a function reference, an EQName, query text evaluated from a
# string -- the query still RUNS and its hits are reported, but a zero is not certified (rc 4).
# Selecting by CONTENT (`collection('x4eff')[.//ware]`) is not narrowing by address: every
# document was examined, so that zero stays certified.

#: The release reviewer's 12 bypasses, verbatim (scratchpad relreview/r6-live-basex/probe_ask.py).
REVIEWER_BYPASSES = [
    "for $d in collection('x4eff') let $u := document-uri($d) where contains($u,'libraries/wares') return $d//ware[@id='zz']",
    "for $d in collection('x4eff') return if (document-uri($d) => contains('libraries/wares')) then $d//ware[@id='zz'] else ()",
    "for $d in collection('x4eff') return if (tokenize(document-uri($d),'/')[3] = 'libraries') then $d//ware[@id='zz'] else ()",
    "filter(collection('x4eff'), function($d){ substring(base-uri($d),8,9) eq 'libraries' })//ware[@id='zz']",
    "collection('x4eff') ! (let $u := base-uri(.) return .[$u = '/x4eff/libraries/wares.xml'])//ware[@id='zz']",
    "for $d in collection('x4eff') return switch (document-uri($d)) case '/x4eff/libraries/wares.xml' return $d//ware[@id='zz'] default return ()",
    "collection('x4eff')[position() le 9]//ware[@id='zz']",
    "subsequence(collection('x4eff'), 1, 9)//ware[@id='zz']",
    "for $d in collection('x4eff') where db:node-pre($d) lt 100 return $d//ware[@id='zz']",
    "let $c := collection('x4eff') return collection#1('x4eff/libraries')//ware[@id='zz']",
    "for $d in collection('x4eff') return if (string-length(document-uri($d)) < 30) then $d//ware else ()",
    "collection('x4eff')[Q{http://www.w3.org/2005/xpath-functions}document-uri(.) = '/x4eff/libraries/wares.xml']//ware",
]

#: The shapes fu-ask already refused (pre-run, rc 2). Under the token rule they RUN and their
#: zero is not certified -- still never a NEGATIVE CONFIRMED.
FU_ASK_SHAPES = [
    "collection('x4eff')[matches(document-uri(.),'libraries/wares')]//*[@id='zzqqxx_nohit']",
    "for $d in collection('x4eff') where contains(base-uri($d),'libraries/wares') return $d//*[@id='zzqqxx_nohit']",
    "db:get('x4eff')[matches(document-uri(.),'libraries/wares')]//*[@id='zzqqxx_nohit']",
    "collection('x4eff')[starts-with(document-uri(.),'/x4eff/libraries/')]//*[@id='zzqqxx_nohit']",
    "let $w := collection('x4eff') return $w[contains(base-uri(.), 'x')]",
    "collection('x4eff')//ware[range[document-uri(.) = 'foo']]",
    "collection('x4eff')['/x4eff/libraries/wares.xml' = document-uri(.)]//ware",
]

#: Further shapes the token classes cover, one per class the reviewer did not reach, so each
#: class is pinned by at least one query of its own.
OTHER_CLASSES = [
    ("collection('x4eff')[last()]//ware", "last("),
    ("head(collection('x4eff'))//ware", "head("),
    ("tail(collection('x4eff'))//ware", "tail("),
    ("remove(collection('x4eff'), 1)//ware", "remove("),
    ("for-each(collection('x4eff'), function($d) { $d })//ware", "for-each("),
    ("fold-left(collection('x4eff'), (), function($a, $d) { ($a, $d) })//ware", "fold-left("),
    ("for $d at $i in collection('x4eff') where $i lt 10 return $d//ware", "at $"),
    ("for $d in collection('x4eff') count $c where $c lt 10 return $d//ware", "count $"),
    ("for tumbling window $w in collection('x4eff') start when true() return $w//ware", "window"),
    ("collection('x4eff')[1]//ware", "[1]"),
    ("let $n := 3 return collection('x4eff')[$n]//ware", "[$n]"),
    ("collection('x4eff')[count(.//ware)]//ware", "[count("),
    ("for $d in collection('x4eff') where db:node-id($d) lt 9 return $d//ware", "node-id("),
    ("for $d in collection('x4eff') where db:path($d) = 'x' return $d//ware", "path("),
    ("collection('x4eff')[generate-id(.) = 'x']//ware", "generate-id("),
    ("collection('x4eff')[. << collection('x4eff')[9]]//ware", "<<"),
    ("let $f := collection('x4eff')[9] return collection('x4eff')[. is $f]//ware", " is "),
    ("util:item(collection('x4eff'), 2)//ware", "util:"),
    ("array { collection('x4eff') }?3//ware", "array {"),
    ("(collection('x4eff'), xquery:eval('()'))//ware", "xquery:"),
    ("function-lookup(xs:QName('fn:head'), 1)(collection('x4eff'))//ware", "function-lookup("),
    ("collection('x4eff')[random:double() lt 0.5]//ware", "random:"),
    ("map:merge(collection('x4eff') ! map { 'k': . })?k//ware", "map:"),
    # an attribute value template is an EXPRESSION inside quotes -- blanking string contents
    # must not hide it
    ("for $d in collection('x4eff') let $e := <e a='{document-uri($d)}'/> where contains($e/@a, 'l') return $d//ware", "document-uri("),
    # a comment opener INSIDE a string literal is not a comment -- stripping it as one used to
    # swallow everything up to the next ':)', token and all
    ("collection('x4eff')[('(:', document-uri(.), ':)') = '/x4eff/a.xml']//ware", "document-uri("),
    # a `}` inside a nested literal within an enclosed expression does not close it
    ("for $d in collection('x4eff') let $e := <e a='{concat(\"}\", document-uri($d))}'/> "
     "where contains($e/@a, 'l') return $d//ware", "document-uri("),
    # an apostrophe in a constructor's TEXT content does not open a string literal
    ("(<r>it's</r>, collection('x4eff')[position() lt 9])//ware", "position("),
]

ALL_UNCERTIFIABLE = REVIEWER_BYPASSES + FU_ASK_SHAPES + [q for q, _ in OTHER_CLASSES]

#: Must still certify: whole-database, content-only queries -- including ones that SPELL a
#: token inside a string literal or a comment, or use a word that merely contains one.
MUST_CERTIFY = [
    "collection('x4eff')//ware[@id='x']",
    "collection('x4eff')[.//ware]//x",
    "collection('x4eff')[@note = 'calls document-uri(.) = x']//ware",
    "collection('x4eff')//ware[@note = 'position() last() head( subsequence( #1 Q{x}']",
    "(: document-uri(.) = 'x', position() le 9, collection#1 :) collection('x4eff')//ware",
    "collection('x4eff')//lastname/position",           # names that CONTAIN a token word
    "collection('x4eff')//ware[@position = 'head']",
    "collection('x4eff')//ware[@id = '1']",             # a STRING '1' is not a position
    "db:get('x4eff')//ware[@id='x']",
]

# Deliberate compatibility boundary: FLWOR is still executable, but the
# restricted recognizer does not prove its enumeration or predicate types.
@pytest.mark.parametrize('q', [
    "for $head in collection('x4eff')//ware[@id='x'] return $head",
    "for $w in collection('x4eff')//ware where count($w/price) = 0 return $w",
])
def test_flwor_zero_is_now_uncertified(monkeypatch, capsys, q):
    _complete_zero(monkeypatch)
    assert ask.main(['xq', q, '--db', 'x4eff']) == 4
    assert 'restricted' in capsys.readouterr().out


@pytest.mark.parametrize("q", ALL_UNCERTIFIABLE)
def test_a_ZERO_from_a_query_that_can_narrow_by_address_is_NOT_certified(monkeypatch, capsys, q):
    _complete_zero(monkeypatch)
    rc = ask.main(["--db", "x4eff", "xq", q])
    cap = capsys.readouterr()
    assert rc == 4, (rc, cap)
    assert "NEGATIVE CONFIRMED" not in cap.out, cap.out
    assert "NOT A NEGATIVE FINDING" in cap.out and "can narrow" in cap.out, cap.out


@pytest.mark.parametrize("q", ALL_UNCERTIFIABLE)
def test_TWIN_the_same_queries_RUN_and_their_hits_are_reported(monkeypatch, capsys, q):
    """The token rule withholds CERTIFICATION, never the answer: a hit is a hit."""
    _fake_basex(monkeypatch, "<ware id='zz'/>")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4eff", "xq", q])
    cap = capsys.readouterr()
    assert rc == 0, (rc, cap)
    assert "1 item(s) in x4eff" in cap.out and "addresses PART" not in cap.err, cap


@pytest.mark.parametrize("q,token", OTHER_CLASSES)
def test_the_uncertified_zero_NAMES_the_token_it_saw(monkeypatch, capsys, q, token):
    _complete_zero(monkeypatch)
    assert ask.main(["--db", "x4eff", "xq", q]) == 4
    out = capsys.readouterr().out
    named = ask._scope_tokens(q)
    assert any(token.strip() in n for n in named), (token, named)
    assert all(n in out for n in named), (named, out)


@pytest.mark.parametrize("q", MUST_CERTIFY)
def test_TWIN_a_whole_database_content_query_is_still_certified(monkeypatch, capsys, q):
    _complete_zero(monkeypatch)
    rc = ask.main(["--db", "x4eff", "xq", q])
    out = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED over 100 of 100" in out, (ask._scope_tokens(q), out)


def test_refs_is_certified_though_its_own_query_RETURNS_document_uri(monkeypatch, capsys):
    """`q_refs` returns `document-uri(root($n))` to NAME the file of each hit. ask.py wrote that
    query itself and it addresses every document, so the token scan must not apply to it --
    otherwise every `refs` negative, the tool's most-used claim, would stop being certified."""
    assert ask._scope_tokens(ask.q_refs("x4eff", "zz")), "premise: the refs query spells a token"
    _complete_zero(monkeypatch)
    rc = ask.main(["refs", "zz_no_such_id", "--db", "x4eff"])
    out = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED over 100 of 100" in out, out


# Finding 2 of the v3.3.0 review: `$d => document-uri()` was refused rc 2 as "compared" --
# the `>` of the arrow `=>` read as a comparison. A query that merely RETURNS a document's URI
# must run and report its hits.
@pytest.mark.parametrize("q", [
    "for $d in collection('x4eff') return $d => document-uri()",
    "for $d in collection('x4eff') return $d => base-uri()",
    "for $d in collection('x4eff') return document-uri($d)",
    "collection('x4eff')//ware ! document-uri(root(.))",
    "collection('x4eff')//ware ! [document-uri(root(.)), string(@id)]",
])
def test_a_query_RETURNING_a_document_uri_runs_and_reports_its_hits(monkeypatch, capsys, q):
    _fake_basex(monkeypatch, "/x4eff/libraries/wares.xml")
    _stale(monkeypatch, fresh=True)
    rc = ask.main(["--db", "x4eff", "xq", q])
    cap = capsys.readouterr()
    assert rc == 0 and "/x4eff/libraries/wares.xml" in cap.out, cap


def test_the_scope_note_comes_AFTER_the_database_checks(monkeypatch, capsys):
    """A query that names no database at all keeps ITS reason: the token note is not the
    first thing a reader needs when there was no search in the first place."""
    _complete_zero(monkeypatch)
    rc = ask.main(["--db", "x4eff", "xq", "(1, 2, 3)[position() > 5]"])
    out = capsys.readouterr().out
    assert rc == 4 and "names no database" in out, out


def test_the_pre_run_address_refusal_is_UNCHANGED(monkeypatch, capsys):
    """`_db_reaches` still refuses scope narrowed inside a reach call's own arguments BEFORE
    the run (rc 2) -- the token rule is additive, it replaced only the shape list."""
    _complete_zero(monkeypatch)
    rc = ask.main(["--db", "x4eff", "xq", "collection('x4eff/libraries')//ware"])
    assert rc == 2 and "addresses PART of a database" in capsys.readouterr().err


# --- an unreadable CATALOG is named, not counted as one vpath (v3.3.0 review, finding 3) ----

def _unexplained_with_catalog(monkeypatch, catalogs):
    _fake_basex(monkeypatch, "")
    monkeypatch.setattr(ask, "load_coverage", lambda db: {
        "db": db, "status": "unexplained", "supports_negative_claim": False,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": [],
        "unreadable_catalogs": catalogs})
    _stale(monkeypatch, fresh=True)


def test_an_unreadable_CATALOG_is_named_in_the_refusal(monkeypatch, capsys):
    """100 of 100 indexed reads as nothing missing; the refusal must say WHAT is missing --
    a catalog whose packed members were never enumerated, so no count of them exists."""
    fail = "somemod: catalog unreadable (ValueError: bad cat) -- only its LOOSE files were enumerated"
    _unexplained_with_catalog(monkeypatch, [fail])
    rc = ask.main(["--db", "x4eff", "xq", "collection('x4eff')//ware[@id='zz']"])
    out = capsys.readouterr().out
    assert rc == 4 and "NEGATIVE CONFIRMED" not in out, out
    assert fail in out and "never enumerated" in out, out


def test_TWIN_no_catalog_line_when_none_is_unreadable(monkeypatch, capsys):
    _unexplained_with_catalog(monkeypatch, [])
    assert ask.main(["--db", "x4eff", "xq", "collection('x4eff')//ware[@id='zz']"]) == 4
    assert "never enumerated" not in capsys.readouterr().out



# --- R5-1 (v4.0.0 review): ask.py must start WITHOUT lxml --------------------------------

def _no_lxml_dir(tmp_path):
    """A PYTHONPATH entry whose `lxml` raises on import: the interpreter of a user who never
    installed it (ask.py is run with a bare `python`, not the x4validate venv)."""
    shadow = tmp_path / "shadow" / "lxml"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("raise ImportError('lxml is not installed (test shadow)')\n",
                                        encoding="utf-8")
    return shadow.parent


def test_ask_help_RUNS_without_lxml(tmp_path):
    """MEASURED by the reviewer (review/scratch-R5-tools/ask_bare.txt): `python ask.py --help`
    crashed with ModuleNotFoundError, rc 1 -- content_query imported lxml at load only to
    validate a QName."""
    import os, subprocess, sys
    from pathlib import Path as P
    here = P(ask.__file__).resolve().parent
    env = dict(os.environ, PYTHONPATH=str(_no_lxml_dir(tmp_path)))  # env-ok: a child process's environment, not configuration
    r = subprocess.run([sys.executable, str(here / "ask.py"), "--help"], capture_output=True,
                       text=True, env=env, cwd=str(tmp_path), timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert "usage" in r.stdout.lower()


def test_TWIN_the_shadow_really_hides_lxml(tmp_path):
    import os, subprocess, sys
    env = dict(os.environ, PYTHONPATH=str(_no_lxml_dir(tmp_path)))  # env-ok: a child process's environment, not configuration
    r = subprocess.run([sys.executable, "-c", "import lxml"], capture_output=True, text=True,
                       env=env, cwd=str(tmp_path), timeout=60)
    assert r.returncode != 0 and "test shadow" in r.stderr


@pytest.mark.parametrize("name", ["name", "xml:lang", "\u00e9nergie", "foo-bar_2", "_x", "a.b",
                                  "x\u00b7y", "\u4e2d\u6587", "", "1a", "-a", ".a", "a b", "a:b:c",
                                  ":a", "a:", "a\u00b2", "\u00d7a", "a/b", "a=b"])
def test_the_stdlib_QName_check_AGREES_with_lxml(name):
    """is_qname no longer needs lxml; where lxml IS present the two must agree name for name,
    so dropping the import did not widen what may certify a zero."""
    from content_query import is_qname
    try:
        from lxml import etree
    except ImportError:
        pytest.skip("lxml absent: nothing to compare the stdlib check against")
    parts = name.split(":")
    try:
        want = len(parts) <= 2 and all(p and etree.QName(p).namespace is None
                                       and etree.QName(p).localname == p for p in parts)
    except ValueError:
        want = False
    assert is_qname(name) is want, (name, want)


# --- #7: basex_run is the one BaseX runner (no JVM: subprocess is faked) --------------
class _Proc:
    def __init__(self, rc, out, err=b""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_7_undecodable_output_is_refused_not_mangled(monkeypatch, tmp_path):
    import basex_run
    monkeypatch.setattr(ask, "BASEX_DIR", tmp_path)
    monkeypatch.setattr(basex_run.subprocess, "run", lambda *a, **k: _Proc(0, b"ok \xff"))
    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        ask.run_xq("1")


def test_7_the_query_travels_as_a_utf8_file_never_as_an_argument(monkeypatch, tmp_path):
    import basex_run
    seen = {}

    def fake(argv, **kw):
        path = argv[-1]
        seen["argv"] = argv
        seen["bytes"] = open(path, "rb").read()
        return _Proc(0, b"r\r\n")
    monkeypatch.setattr(basex_run.subprocess, "run", fake)
    monkeypatch.setattr(ask, "BASEX_DIR", tmp_path)
    q = 'string-to-codepoints("' + chr(0x706B) + '")'
    assert ask.run_xq(q) == "r"
    assert "-q" not in seen["argv"] and q not in seen["argv"]
    assert seen["bytes"] == q.encode("utf-8")
    assert "-Dstderr.encoding=UTF-8" in seen["argv"] and "-Dstdout.encoding=UTF-8" in seen["argv"]
    assert list(tmp_path.iterdir()) == [], "the temporary query file was left behind"


def test_7_no_hand_rolled_basex_runner_outside_basex_run():
    """Every Python caller of the jar goes through basex_run; a new `-q` argv call would
    reintroduce the cp1252 argument channel. Matched on an exact AST string constant, so
    prose in docstrings (preflight names the class in sentences) does not count."""
    import ast
    from pathlib import Path
    here = Path(ask.__file__).resolve().parent
    offenders = []
    for py in sorted(here.glob("*.py")):
        if py.name.startswith("test_") or py.name == "basex_run.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Constant) and n.value == "org.basex.BaseX" for n in ast.walk(tree)):
            offenders.append(py.name)
    assert offenders == []
    assert any(isinstance(n, ast.Constant) and n.value == "org.basex.BaseX"
               for n in ast.walk(ast.parse((here / "basex_run.py").read_text(encoding="utf-8"))))
