#!/usr/bin/env python
"""How often do I reach for a KNOWN-BAD instrument shape, and is that improving?

THE QUESTION THIS ANSWERS. `gates/toolkit_usage.py` measures WHETHER the toolkit's
own commands get reached for. This measures whether the shell around them is used
CORRECTLY -- a different failure, and by count the larger one.

WHY A GATE AND NOT MORE PROSE. CLAUDE.md and the memory files are read once, at
session start; the failure happens two hundred tool calls later. Prose cannot fire
at the moment of use. On 2026-08-29 three of one session's mistakes were shapes
already written down in those files, and one had been CITED an hour before being
repeated. So the count has to come from a program: the self-report is produced by
the same faculty that made the mistakes.

⚠ THIS IS A LOWER BOUND AND SAYS SO. Five patterns retain their raw-regex scope;
the three release-failing patterns now use the shared shell grammar to exclude
quoted examples, comments and data heredocs, and to follow pipeline status only
to the next command. Opaque programs and unresolved executable names are not
parsed. A wrong
population, a vacuous comparison, or a number transcribed instead of derived are
all invisible here. A falling count is evidence; a zero is not a clean bill.

★ EVERY PATTERN CARRIES A FIXTURE, AND THE GATE REFUSES TO RUN WITHOUT ONE.

That is the whole design, and it was paid for. Writing ONE regex to match a literal
backslash failed three times in ten minutes, inside this very file:

  1. `r"\\\\[nrt]"` in a raw string is FOUR backslashes -- a regex for TWO literal
     ones. Counted 9 where the answer was 395.
  2. rebuilt as `chr(92) + "[nrt]"`, which is a regex for a literal `[nrt]`. Counted 0.
  3. the verification of (2) was itself wrong: written through a Bash heredoc, its
     `\\b` collapsed to a backspace character, so it agreed with the broken pattern.

None of that is carelessness a rule can fix. A pattern that has never been shown to
match anything is a check that cannot go red -- so each `Shape` names an example it
MUST match and a near-miss it must NOT, `verify_fixtures()` runs before any count is
produced, and `test_instrument_hygiene.py` pins the same contract.

WHAT THE RATE IS FOR. Each shape's share of all commands decides whether it could be
a PreToolUse rule at all. A rule firing on 13% of commands is not a guard, it is a
nuisance that trains you to click through. The tier is DERIVED here and printed,
never asserted in a doc that can rot.

    uv run python gates/instrument_hygiene.py [--record] [--transcripts DIR]

Baseline rates carry a matcher fingerprint. A different matcher must re-measure
the SAME historical population; --record accepts a new period and is not a fix
for old history. Never compare old regex rates against grammar-aware counts.

RE-MEASURING after a matcher change (the gate refuses with rc 2 until you do; there
is no tool for it, only these steps). 1. Copy the old baseline JSON aside: it is
the only record of the old rates and its `commands` count N. 2. Copy the transcript
files that existed when it was recorded into a scratch DIR, and run
`--transcripts DIR` WITHOUT --record: the printed Bash command count must equal N.
Any other count is a different population (a transcript that grew since, or one
deleted), so it cannot stand in for the old one: go to step 4. 3. Count equal: run
`--record --transcripts DIR`, which re-baselines that SAME population under the new
matcher; later runs then compare the calls after it incrementally. 4. Otherwise
`--record` over the whole corpus accepts a NEW period, and the old history is not
compared: say so wherever the change is recorded.

Exit: 0 clean (or recorded) - 1 a shape got worse or appeared - 2 cannot run, or
      nothing got worse but some transcript input was UNREADABLE (a clean verdict over
      a denominator with a hole in it is not clean; AUDIT-2026-09-24 GT-6). A finding
      outranks the refusal.
"""

from __future__ import annotations

import json
import hashlib
import importlib.util
import inspect
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from functools import cache

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402
from toolkit_usage import transcript_dir  # noqa: E402  (ONE implementation of the refusal)

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".instrument-hygiene-baseline.json"
RECORD = "--record" in sys.argv

#: How recently a transcript must have been written for a half-written LAST line to be
#: read as a writer mid-append (release review 2026-09-26). A session appends whole JSON
#: lines, so a live partial line is milliseconds old; one older than this is a transcript
#: a crash truncated, and it counts as unreadable. Ten minutes: generous against a slow
#: disk or a paused writer, and far below the age of any truncated-by-crash leftover.
LIVE_APPEND_WINDOW_S = 600

#: Above this share of all commands a rule cannot be a PreToolUse guard: it would
#: fire so often the prompt becomes noise. MEASURED against the corpus, not chosen.
HOOKABLE_MAX = 0.02

BS = chr(92)

#: A command that CHANGES something. If one appears in a command that then exits
#: non-zero, a side effect may not have happened -- and the rest of the command
#: almost certainly did not run.
SIDE_EFFECT = re.compile(r"\b(cp|mv|mkdir|touch|tee|rm|git add|git commit)\b")
EXIT_CODE = re.compile(r"Exit code (\d+)")


@dataclass(frozen=True)
class Shape:
    key: str
    why: str
    pattern: re.Pattern
    #: A known-bad example this pattern MUST match. Without it the pattern could be
    #: inert and every run would report a comfortable zero.
    hits: str
    #: A near-miss it must NOT match, so the fixture cannot be satisfied by a
    #: pattern that matches everything.
    misses: str


SHAPES: list[Shape] = [
    Shape("dollar-question-after-pipe",
          "$? after a pipeline is the LAST command's status, not the one you mean",
          re.compile(r"\|[^|\n]*(;|&&)\s*(echo\s+)?[^\n]*\$\?"),
          hits="uv run x4live dump | head -1; echo $?",
          misses="uv run x4live dump > out.txt; rc=$?"),
    Shape("bare-python-on-project-code",
          "the system interpreter has no project deps and reports "
          "ModuleNotFoundError as test failures",
          # NOT a lookbehind. The first version used `(?<!uv run )`, which inspects
          # only the seven characters before `python`, so `uv run --frozen python`
          # was flagged as bare: 52 hits where the true count was 2. Caught by
          # SAMPLING the matches rather than trusting the total.
          re.compile(r"^(?:(?!uv run).)*?\bpython3?\s+-m\s+pytest\b", re.S),
          hits="cd /repo/tools/x4validate && python -m pytest -q tests/",
          misses="cd x && uv run --frozen python -m pytest -q"),
    Shape("test-and-commit-in-one-command",
          "a check that runs in the same breath as the irreversible step cannot stop it",
          re.compile(r"(?s)\A(?=.*\b(pytest|run-gates)\b)(?=.*\bgit commit\b)"),
          hits="uv run pytest -q; git commit -m x",
          misses="uv run pytest -q && echo done"),
    Shape("diff-r-as-a-port-proof",
          "reports line-ending differences as findings: 52 differences where 12 were real",
          re.compile(r"\bdiff\s+-[a-z]*r"),
          hits="diff -rq treeA treeB",
          misses="git diff --numstat docs/TRUST.md"),
    Shape("diff-against-a-hand-rolled-baseline",
          "for a TRACKED file git IS the baseline; a scratchpad copy can silently be "
          "last week's",
          re.compile(r"(?s)\A(?=.*(?<!git )\bdiff\b)(?=.*scratchpad)"),
          hits="diff /x/scratchpad/f.bak docs/f.md",
          misses="git diff HEAD --name-only"),
    Shape("writes-source-through-a-bash-heredoc",
          "an escape does not survive the tool boundary; author source with the Write "
          "tool instead",
          re.compile(r"(cat|tee)\s*>+\s*[\"']?[^\s\"'|]*\.(py|sh)\b"),
          hits="cat > gates/x.py <<'EOF'",
          misses="uv run python gates/x.py"),
    Shape("inline-interpreter-heredoc",
          "same escape hazard, and the shape a file-write rule misses entirely: "
          "1,522 commands",
          re.compile(r"\b(python3?|bash|sh)\s+-\s*<<"),
          hits="python - <<'PYEOF'",
          misses="python -c 'print(1)'"),
    Shape("grep-c-or-wc-l-as-a-count",
          "a line count is not an item count; the engine logs most failures twice",
          re.compile(r"\b(grep -c|wc -l)\b"),
          hits="grep -c ERROR debug.txt",
          misses="grep -n ERROR debug.txt"),
]


@cache
def _guard_parser():
    """Use the toolkit's shell grammar; don't invent another quote/heredoc parser."""
    path = ROOT.parents[1] / "agent/guards/claude-hooks/hook_facts.py"
    if not path.is_file():
        _env.skip("no shared shell grammar for the hygiene census",
                  "run this maintainer gate from the toolkit checkout")
    spec = importlib.util.spec_from_file_location("_hygiene_guard_parser", path)
    module = importlib.util.module_from_spec(spec)
    # Read committed source bytes directly: stale .pyc is not a grammar baseline.
    module._hygiene_source_bytes = path.read_bytes()
    exec(compile(module._hygiene_source_bytes, str(path), "exec"), module.__dict__)
    return module


def matches(shape: Shape, command: str, cwd: str = "") -> bool:
    """The three release-failing shapes inspect shell code, not quoted examples.

    Other patterns retain their original raw-text scope. These are lower-bound
    shell checks: opaque programs and unresolved executable names are not parsed.
    """
    if shape.key not in {"dollar-question-after-pipe", "bare-python-on-project-code",
                         "test-and-commit-in-one-command"}:
        return bool(shape.pattern.search(command))
    parser = _guard_parser()
    body = parser.strip_comments(parser.strip_heredocs(parser.join_continuations(command)))
    if shape.key == "dollar-question-after-pipe":
        return parser.dollarq_after_pipe(body, parser.assignments(body))
    if shape.key == "bare-python-on-project-code":
        assigns = parser.assignments(body)
        # Keep this gate's original pytest-only shape. The guard also covers
        # scripts/modules, but expanding that population would change this audit.
        return any(shape.pattern.search(parser.blank_quoted(segment))
            and parser._bare_python_targets_project_code(
            parser.resolve_verb(segment, assigns), directory, assigns)
            for segment, directory in parser.cwd_track(body, cwd))
    return bool(shape.pattern.search(parser.blank_quoted(body)))


def matcher_fingerprint() -> str:
    """Old regex rates cannot be compared with grammar-aware counts."""
    grammar = ROOT.parents[1] / "agent/guards/claude-hooks/hook_facts.py"
    loaded = _guard_parser()._hygiene_source_bytes
    if grammar.read_bytes() != loaded:
        _env.skip("the shared hygiene grammar changed after it was loaded",
                  "rerun against stable source bytes")
    payload = json.dumps([(s.key, s.pattern.pattern, s.pattern.flags) for s in SHAPES])
    blob = (payload.encode("utf-8") + inspect.getsource(matches).encode("utf-8")
            + inspect.getsource(scan).encode("utf-8") + loaded)
    return hashlib.sha256(blob).hexdigest()


def verify_fixtures(shapes: list[Shape] | None = None) -> list[str]:
    """Prove every pattern is LIVE before any count is produced.

    Returns the failures. A pattern that no longer matches its own known-bad
    example has silently stopped working, and every run after that reports a
    comfortable zero -- which is exactly what a broken guard looks like from
    outside.
    """
    bad = []
    for s in (shapes if shapes is not None else SHAPES):
        if not matches(s, s.hits):
            bad.append(f"{s.key}: does NOT match its known-bad example -- pattern is inert")
        if matches(s, s.misses):
            bad.append(f"{s.key}: ALSO matches its near-miss -- pattern is too broad")
    return bad


@dataclass
class Census:
    files: int = 0
    commands: int = 0
    nonzero: int = 0
    unreadable: list[str] = field(default_factory=list)
    #: Files whose LAST line was half-written (unparseable AND not newline-terminated):
    #: the live transcript of a running session, mid-append. Counted apart from
    #: `unreadable` -- it is not a hole in the denominator (review of 87ac461) -- but
    #: only for a file written within LIVE_APPEND_WINDOW_S; an older one is unreadable.
    partial_tail: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    lost_side_effects: list = field(default_factory=list)


def scan(tdir: Path) -> Census:
    """Every Bash command, paired with the exit code its result reported."""
    c = Census()
    files = sorted(tdir.glob("*.jsonl"))
    if not files:
        _env.skip(f"no transcripts in {tdir}",
                  "pass --transcripts DIR or set X4_TRANSCRIPTS")
    for f in files:
        c.files += 1
        uses: dict[str, tuple[str, str]] = {}
        results: dict[str, str] = {}
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            c.unreadable.append(f"{f.name}: {exc}")
            continue
        lines = text.splitlines()
        unterminated = bool(text) and not text.endswith(("\n", "\r"))
        try:
            live = time.time() - f.stat().st_mtime <= LIVE_APPEND_WINDOW_S
        except OSError:
            live = False                    # cannot show it is live: not excused
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                if i == len(lines) - 1 and unterminated and live:
                    # A writer mid-append, not a damaged record: only the FINAL line,
                    # only when no newline has terminated it yet, and only in a file
                    # written within LIVE_APPEND_WINDOW_S -- an old one was truncated.
                    c.partial_tail.append(f.name)
                    continue
                # Recorded, never swallowed: an unparseable transcript is a hole in
                # the denominator, and a hole nobody states is this register's theme.
                c.unreadable.append(f"{f.name}: unparseable line")
                continue
            body = (rec.get("message") or {}).get("content")
            if not isinstance(body, list):
                continue
            for b in body:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    uses[b.get("id", "")] = ((b.get("input") or {}).get("command", ""),
                                              rec.get("cwd") or "")
                elif b.get("type") == "tool_result":
                    results[b.get("tool_use_id", "")] = str(b.get("content", ""))
        for tid, (cmd, cwd) in uses.items():
            if not cmd:
                continue
            c.commands += 1
            for s in SHAPES:
                if matches(s, cmd, cwd):
                    c.counts[s.key] = c.counts.get(s.key, 0) + 1
            m = EXIT_CODE.search(results.get(tid, "")[:400])
            if m and m.group(1) != "0":
                c.nonzero += 1
                # Only when the side effect is NOT in the first segment. A chain like
                # `cp a b && grep x f` exits 1 with the cp having run perfectly well;
                # counting those inflated this from 8 to 39 in the first draft.
                head = cmd.split("&&")[0].split(";")[0]
                if SIDE_EFFECT.search(cmd) and not SIDE_EFFECT.search(head):
                    c.lost_side_effects.append((int(m.group(1)),
                                                cmd.splitlines()[0][:105]))
    return c


def _baseline() -> tuple[dict, dict]:
    """(rates, the whole record). The record carries `commands`, which the verdict needs to
    judge the INCREMENTAL rate rather than a lifetime average that dilutes it."""
    if not BASELINE.exists():
        return {}, {}
    try:
        rec = json.loads(BASELINE.read_text(encoding="utf-8"))
        return rec["rates"], rec
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(f"{BASELINE} exists but cannot be read: {exc}") from exc


def main() -> int:
    fingerprint = matcher_fingerprint()
    bad = verify_fixtures()
    if bad:
        print("REFUSING: a pattern failed its own fixture, so any count would be "
              "meaningless:", file=sys.stderr)
        for b in bad:
            print(f"    {b}", file=sys.stderr)
        return 2

    tdir = transcript_dir()
    c = scan(tdir)
    if matcher_fingerprint() != fingerprint:
        print("REFUSING: the hygiene matcher changed during the census; no stable "
              "comparison or baseline can be produced.", file=sys.stderr)
        return 2
    if c.commands == 0:
        print("REFUSING: no Bash commands found, so a clean result would prove nothing.",
              file=sys.stderr)
        return 2

    print(f"INSTRUMENT HYGIENE — {c.commands} Bash command(s) across {c.files} "
          f"transcript(s)")
    print("  Bash tool calls only: every shape here is bash syntax, so PowerShell calls are "
          "not in this denominator (toolkit_usage counts both)")
    print(f"  {len(SHAPES)} pattern(s), each verified against a known-bad example and "
          f"a near-miss before counting")
    if c.unreadable:
        print(f"  ⚠ {len(c.unreadable)} unreadable input(s) — the denominator is incomplete:")
        for u in c.unreadable[:20]:
            print(f"      {u}")
        if len(c.unreadable) > 20:
            print(f"      ... and {len(c.unreadable) - 20} more")
    if c.partial_tail:
        print(f"  {len(c.partial_tail)} transcript(s) end in a partial last line (a live "
              f"session mid-write; not counted, not a hole): {', '.join(c.partial_tail[:5])}")
    print("  LOWER BOUND: counts shapes a regex can see, never a wrong population,")
    print("  a vacuous comparison, or a number transcribed instead of derived.")
    print("")
    print(f"  {'shape':40} {'count':>6} {'rate':>7}   viable as a PreToolUse rule?")
    rates = {}
    for s in SHAPES:
        n = c.counts.get(s.key, 0)
        rate = n / c.commands
        rates[s.key] = round(rate, 5)
        print(f"  {s.key:40} {n:6} {rate * 100:6.2f}%   "
              f"{'yes' if rate <= HOOKABLE_MAX else 'NO — would flood'}")
    print("")
    print(f"  non-zero Bash exits: {c.nonzero} ({c.nonzero / c.commands * 100:.1f}%)")
    print(f"  ...of which a side effect sat AFTER the failure point and so likely never "
          f"ran: {len(c.lost_side_effects)}")
    for code, cmd in c.lost_side_effects[:5]:
        print(f"      exit {code:3}  {cmd}")

    if RECORD:
        BASELINE.write_text(json.dumps({"rates": rates, "commands": c.commands,
                                       "matcher_fingerprint": fingerprint},
                                       indent=2, sort_keys=True) + chr(10),
                            encoding="utf-8")
        print("")
        print(f"recorded baseline -> {BASELINE.name}")
        return 0

    base, base_meta = _baseline()
    if not base:
        print("")
        print("No baseline yet — run with --record. This run measures but cannot judge.")
        # rc 2, never 0: run-gates.sh buckets 0 as `ok` and discards stdout, so a gate
        # that compared nothing would read as a passing one on every fresh clone
        # (review, 2026-09-14; claude_md_budget and hook_false_positives already refuse).
        return 2

    if base_meta.get("matcher_fingerprint") != fingerprint:
        print("REFUSING: the baseline used a different or unrecorded matcher; raw-regex "
              "rates cannot be compared with shell-grammar counts. Re-measure the "
              "SAME baseline population before comparing, or explicitly accept a new "
              "period with --record (the steps are in this gate's docstring).", file=sys.stderr)
        return 2

    # INCREMENTAL, not cumulative. The corpus is append-only, so a lifetime average's
    # sensitivity to the current period decays as 1/N: MEASURED 2026-09-20 over 22,288
    # commands against a 10,767-command baseline, `$?`-after-a-pipe ran at 2.54% in the new
    # period (baseline 1.74%) and test-and-commit at 0.885% (0.678%) -- both over this rule's
    # own 1.25x threshold, both reported GREEN because the average was diluted by the older
    # half. That is the per-item regression hidden by a total, with sessions as the items.
    # Both operands are already stored, so this needs no new input; an older baseline without
    # `commands` falls back to the cumulative compare and SAYS so.
    base_n = base_meta.get("commands")
    span = (c.commands - base_n) if isinstance(base_n, int) and c.commands > base_n else 0
    # Say WHY the incremental compare is off. This NOTE used to claim "baseline carries
    # no command count" for every span of 0 -- also when it DID carry one and simply no
    # command was added since, or the corpus SHRANK (transcripts deleted), which is a
    # different population, not an old baseline (AUDIT-2026-09-24 GT-6).
    if not isinstance(base_n, int):
        print("  NOTE  baseline carries no command count, so the compare below is the "
              "LIFETIME average, which understates a recent regression.")
    elif c.commands == base_n:
        print(f"  NOTE  no Bash command since the baseline ({base_n}); comparing the "
              "lifetime rates, which are the same population.")
    elif c.commands < base_n:
        print(f"  NOTE  the corpus SHRANK since the baseline ({base_n} -> {c.commands} "
              "commands; transcripts were removed), so this is a different population and "
              "the compare below is lifetime-vs-lifetime, not incremental.")
    worse, appeared = [], []
    for s in SHAPES:
        now, was = rates[s.key], base.get(s.key)
        if span and was is not None:
            # hits added since the baseline, over commands added since the baseline
            now = max(0.0, (now * c.commands - was * base_n) / span)
        if was is None:
            if rates[s.key] > 0:
                appeared.append((s, rates[s.key]))
        elif now > was * 1.25 and now - was > 0.002:
            worse.append((s, was, now))
    if appeared or worse:
        print("")
        for s, now in appeared:
            print(f"NEW SHAPE  {s.key}  {now * 100:.2f}%  — {s.why}")
        for s, was, now in worse:
            print(f"WORSE      {s.key}  {was * 100:.2f}% -> {now * 100:.2f}%  — {s.why}")
        return 1
    print("")
    if c.unreadable:
        print(f"No shape got materially worse IN WHAT COULD BE READ -- but "
              f"{len(c.unreadable)} input(s) were unreadable (listed above), so this is not "
              "a clean verdict. rc 2.", file=sys.stderr)
        return 2
    print("No shape got materially worse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
