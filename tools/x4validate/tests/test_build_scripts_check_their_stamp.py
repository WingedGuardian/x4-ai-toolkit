"""The build scripts must REFUSE when the freshness stamp fails (F126).

WHY THIS TEST EXISTS. F126: `build-effective.sh` built the x4eff database correctly, ran
`staleness.py --write` in a subshell whose rc was never captured, and exited 0 on the
coverage verdict alone. The stamp had failed, so a CURRENT database read STALE forever and
refused to back any negative claim. Success reported for work the script did not do.

The fix captures the rc and exits 1. This test is what stops it regressing -- without it
the guard is decoration: nothing could prove it goes red (CLAUDE.md #26).

TWINS, ONE PER SCRIPT. The identical unchecked call sat in BOTH build scripts, and fixing
one would have left the other. So both are asserted here, by name, rather than looping over
a glob that could silently match zero files.

STRUCTURAL, NOT SUBSTRING-ONLY (#37). Prose satisfies substrings -- the comment block
explaining F126 contains the words "stamp" and "exit" and would satisfy a naive search. So
each assertion is anchored to the ACTUAL call: the `staleness.py --write` line must be the
one that captures a return code, and a non-zero one must reach an `exit`.

⚠ WHAT THIS DOES NOT COVER, stated so the coverage is not overread: it asserts the SHAPE of
the shell source, not the running behaviour. It cannot catch a stamp that succeeds while
writing the wrong fingerprint. The stronger check named in the F126 entry -- compare the
stamped fingerprint against the one the build just computed -- is still unwritten.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BASEX = Path(__file__).resolve().parent.parent.parent / "basex"
SCRIPTS = ("build-corpus.sh", "build-effective.sh")


def _source(name: str) -> str:
    p = BASEX / name
    if not p.is_file():
        pytest.skip(f"{p} is absent -- a skip is not a pass; the script moved or the "
                    f"checkout is partial")
    return p.read_text(encoding="utf-8", errors="replace")


def _strip_comments(text: str) -> str:
    """Comments are prose. The F126 explanation itself mentions stamps and exits."""
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


def _is_invocation(line: str) -> bool:
    """Is this line a CALL, or merely text that quotes one?

    The fix's own error message echoes the re-stamp command back to the user, so a naive
    "line mentions staleness.py" match finds two hits in one script and neither the count
    nor the rc assertion means what it looks like. Echoed help text is data, not a call --
    the same distinction the hooks make between a command and a quoted string.
    """
    s = line.strip()
    return not (s.startswith("echo") or s.startswith("printf") or ">&2" in s)


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_stamp_call_CAPTURES_its_return_code(script):
    """The exact defect: the call ran in a subshell and its rc was discarded."""
    body = _strip_comments(_source(script))
    stamp_lines = [ln for ln in body.splitlines()
                   if "staleness.py" in ln and "--write" in ln and _is_invocation(ln)]
    assert stamp_lines, f"{script}: no `staleness.py --write` call found at all"
    assert len(stamp_lines) == 1, (
        f"{script}: expected exactly ONE stamp call, found {len(stamp_lines)} -- a second "
        f"one would need its own rc check and this test would not see it")
    line = stamp_lines[0]
    assert re.search(r"\|\|\s*\w*RC=\$\?", line), (
        f"{script}: the stamp call does not capture its exit code. This is F126 verbatim:\n"
        f"    {line.strip()}\n"
        f"A failed stamp leaves a BUILT database UNSTAMPED, reading STALE forever, while "
        f"the script exits 0 on the coverage verdict alone.")


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_FAILED_stamp_reaches_an_exit(script):
    """Capturing the rc is worthless if nothing acts on it.

    The falsification twin for the clause above: `|| STAMP_RC=$?` with no branch would
    satisfy the first test completely while still exiting 0.
    """
    body = _strip_comments(_source(script))
    m = re.search(r"\|\|\s*(\w*RC)=\$\?", body[body.index("--write --db"):]) \
        if "--write --db" in body else None
    assert m, f"{script}: no captured rc to act on (see the twin test)"
    var = m.group(1)
    # the variable must be TESTED, and a failing branch must exit non-zero
    tested = re.search(rf'\[\s*"?\$\{{?{var}}}?"?\s*-ne\s*0|\[\s*"\$\{{?{var}}}?"\s*!=\s*"?0',
                       body)
    assert tested, (
        f"{script}: ${var} is captured but never tested -- the rc is collected and dropped, "
        f"which exits 0 exactly as F126 did")
    tail = body[tested.end():tested.end() + 600]
    assert re.search(r"\bexit\s+[1-9]", tail), (
        f"{script}: the failing-stamp branch does not exit non-zero within its block")


@pytest.mark.parametrize("script,first_read", [("build-corpus.sh", "stage.py"), ("build-effective.sh", "build-effective.py")])
def test_the_fingerprint_is_taken_before_the_first_read_of_the_world(script, first_read):
    """T1/R2-7: stamped at the END, a mod edited during the build read FRESH for ever over the old values. x4fresh could
    revoke that afterwards; running the script directly, as CLAUDE.md says to, nothing did. STRUCTURAL: the running shell
    needs Java and a corpus; the two halves (snapshot, stamp from it) are behaviour-tested in tools/basex/test_staleness.py."""
    calls = [ln for ln in _strip_comments(_source(script)).splitlines() if _is_invocation(ln)]
    snapshot = [i for i, ln in enumerate(calls) if "staleness.py" in ln and "--snapshot" in ln]
    reads = [i for i, ln in enumerate(calls) if f'"$HERE/{first_read}"' in ln]
    stamps = [i for i, ln in enumerate(calls) if "staleness.py" in ln and "--write" in ln]
    assert len(snapshot) == 1 and len(reads) == 1 and len(stamps) == 1, (snapshot, reads, stamps)
    assert snapshot[0] < reads[0] < stamps[0]
    assert "--taken-before" in calls[stamps[0]], "a stamp that recomputes at the end is the original defect"


def test_the_two_builds_are_NOT_chained_with_and_in_any_guidance():
    """F126's second half: `build-corpus.sh && build-effective.sh` runs only the first.

    `build-corpus.sh` legitimately exits 3 on SKIPCORRUPT exclusions that are fully
    accounted, so `&&` silently skips the effective build -- and that was the form the
    freshness banner itself printed, in two places, plus CLAUDE.md. Anyone copying the
    documented command got half a rebuild and no warning.

    ⚠ THE POPULATION IS THE WHOLE TEST. The first version of this globbed only `*.sh` and
    `*.py` and reported a clean zero -- while the guidance also lives in `README.md`,
    `QUERIES.md` and the shipped repo-root `CLAUDE.md`, none of which it looked at. A
    negative scoped to the wrong population is worth nothing (CLAUDE.md #22, #34), so the
    search set is derived from where the string ACTUALLY appears, not from a habit about
    file extensions, and the count of files searched is asserted so it cannot pass vacuously.

    Two exclusions, both narrow and named: `BLIND-SPOTS.md` documents this very defect and
    must be free to quote the broken form, and `tests/fixtures/` holds a frozen copy of an
    old CLAUDE.md kept deliberately as history.
    """
    repo = BASEX.parent.parent
    roots = [BASEX, repo / "tools" / "x4validate" / "x4validate"]
    candidates = []
    for root in roots:
        if root.is_dir():
            for ext in ("*.sh", "*.py", "*.md"):
                candidates.extend(root.rglob(ext))
    for shipped in ("CLAUDE.md", "AGENTS.md"):
        if (repo / shipped).is_file():
            candidates.append(repo / shipped)
    # Plan 2 lane A: the rebuild line moved with its section into the x4-toolkit-dev skill.
    candidates.extend(sorted((repo / "agent" / "skills").glob("*/SKILL.md")))

    checked, offenders = 0, []
    for p in candidates:
        parts = p.parts
        if p.name == "BLIND-SPOTS.md" or "fixtures" in parts or p.name == Path(__file__).name:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        checked += 1
        if re.search(r"build-corpus\.sh\s*&&", text):
            offenders.append(str(p.relative_to(repo)))

    assert checked >= 5, (
        f"only {checked} file(s) were searched -- too few to support this negative. An "
        f"empty or tiny population makes a clean result meaningless (CLAUDE.md #9)")
    # the guidance must actually BE somewhere in the searched set, or the test proves nothing
    mentions = sum(1 for p in candidates
                   if p.name != "BLIND-SPOTS.md" and "fixtures" not in p.parts
                   and p.name != Path(__file__).name
                   and "build-corpus.sh" in p.read_text(encoding="utf-8", errors="replace"))
    assert mentions > 0, (
        "no searched file mentions build-corpus.sh at all -- the population does not "
        "contain the guidance this test is about, so a pass would be vacuous")
    assert not offenders, (
        "these still chain the two builds with `&&`, which runs only the first whenever "
        f"build-corpus exits 3: {offenders}")
