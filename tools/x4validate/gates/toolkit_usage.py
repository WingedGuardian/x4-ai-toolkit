r"""Behavioural audit: which of our own instruments do we actually REACH FOR.

WHY THIS EXISTS.
================
Every toolkit invocation depends on the assistant choosing to make it. Nothing
measured whether that happens, so the routing table in CLAUDE.md was an
instruction enforced by attention alone. The costed precedent is BLIND-SPOTS
**F58**: `x4effective dump --chain` existed, was correct, and was in `--help` the
whole time -- nobody ran it, and 65 of 241 vpaths were labelled "renamed by
Egosoft" when Egosoft had renamed nothing.

MEASURED 2026-08-28 by hand over 21 transcripts / 15,041 tool calls: 38 of 41
capabilities had been invoked at least once, 3 never. The interesting figure was
not coverage but the NAMED-to-INVOKED ratio -- `x4stats` 81 named / 14 invoked,
`x4similar` 82 / 14 and last actually run 15 days earlier, both of them named in
commands that same day. A capability we discuss and stop running is the F58 shape
before it has cost anything.

WHY THE SURFACE IS ENUMERATED BY ASKING THE PROGRAMS.
=====================================================
That hand measurement first enumerated subcommands by grepping for
`add_parser("...")`, and MISSED `x4debug crosscheck` (the name sits on the line
after the call) and `x4xref who-calls`/`who-listens` (built in a loop, so the name
is a variable, never a literal). An inventory of capabilities that is itself
missing capabilities is F58's own mechanism. So the surface comes from `--help`
plus argparse's invalid-choice listing, and the CLI roster from pyproject's
`[project.scripts]` rather than a hand-kept tuple. A CLI whose surface cannot be
enumerated is a REFUSAL, never a silently empty row.

WHAT IS A FINDING AND WHAT IS ONLY A NUMBER.
============================================
Dormancy thresholds are not calibrated -- nobody knows yet what normal looks like
for a tool legitimately used a few times a month -- so dormancy is reported as
INFO. Failing on an uncalibrated threshold is the flood that trains you to ignore
the output ("sub-90% buys a MEASUREMENT, not a gate"). Two things ARE findings,
and BOTH are baseline-relative:

  * a capability newly absent from `gates/qa_sweep.py`'s roster. There are 13 real
    gaps today (this said 17 until the argv-shape defect below was fixed);
    failing on all of them every run would make the gate permanently
    red, which is the same flood by another route. The known set is ACCEPTED and
    still printed on every run, so it is visible rather than silenced -- a NEW gap
    is the finding.
  * usage drift: a capability that HAD been invoked and no longer is, one that has
    vanished from the surface, or a newly added capability never invoked

WHY THE BASELINE IS LOCAL.
==========================
It describes one person's session history on one machine. A committed baseline
would be wrong for every other clone and would fail their first run --
`perf_guard`'s reasoning, applied to behaviour rather than wall-clock. `--record`
writes a gitignored file. The transcripts themselves are never read into any
artifact: only per-capability counts and dates leave this gate.

  uv run python gates/toolkit_usage.py [--record] [--transcripts DIR]

Exit: 0 clean (or recorded) - 1 findings - 2 cannot run (never a guess): the
      roster could not be read, or qa_sweep's coverage could not be evaluated and
      nothing else was found. A finding outranks a refusal. (AUDIT-2026-09-24 GT-4:
      the qa_sweep refusal used to be appended to the findings, so "could not look"
      exited 1 as though coverage had regressed.)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import _env

from x4validate import _paths, _surface

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".toolkit-usage-baseline.json"
RECORD = "--record" in sys.argv

#: A capability named recently but not INVOKED for at least this long is the
#: "we discuss it and never run it" shape. INFO only -- see the docstring.
DORMANT_DAYS = 14


# ---------------------------------------------------------------------------
# the SURFACE -- asked of the programs, never parsed out of their source
# ---------------------------------------------------------------------------
def cli_roster() -> list[str]:
    """Every console script the package installs -- asked of `x4validate._surface`.

    While this gate lived in the separate dev repository it kept its own copy of the
    enumeration, because the public tree could not import from it. With one repository
    that copy is a second implementation of one question, and it had already fallen
    behind: the shared one refuses when the subprocess never ran (uv absent, timeout),
    where the copy read that failure as "single-command CLI". A refusal to read the
    ROSTER becomes this gate's rc 2, as the copy's own `_env.skip` did; a single CLI
    whose subcommands cannot be enumerated is reported below as a finding (rc 1).
    """
    try:
        return _surface.cli_roster(ROOT)
    except _surface.SurfaceUnavailable as exc:
        _env.skip(exc.what, exc.how)


def subcommands(cli: str) -> tuple[list[str] | None, str]:
    """(subcommands, note); None is a REFUSAL. See `x4validate._surface.subcommands`."""
    return _surface.subcommands(cli, cwd=ROOT)


#: The patterns live in `_surface`; re-exported for the tests that pin them.
_CHOOSE = _surface._CHOOSE
_SUBS_IN_HELP = _surface._SUBS_IN_HELP


# ---------------------------------------------------------------------------
# the TRANSCRIPTS
# ---------------------------------------------------------------------------
def transcript_dir() -> Path:
    """Resolved, never hardcoded -- these paths carry a username (see _env).

    ⚠ This DERIVES the project directory from the configured game root and
    requires an exact match. It deliberately does NOT fall back to "the biggest
    transcript directory", which is what the first version did: on this machine
    that picked an unrelated game's project, scanned 39 of its transcripts, and
    reported a perfectly well-formed **"0 invoked, 41 never invoked"**. The
    instrument answered an adjacent question and nothing about the output looked
    wrong. A guess that can be silently wrong is worse than a refusal.
    """
    for i, a in enumerate(sys.argv):
        if a == "--transcripts" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1])
    # Through `_paths`, not `os.environ` -- so `x4-paths.env` is honoured
    # like every other setting. Reading the environment directly is the two-doors
    # shape that produced F30.
    env = _paths.path_value("X4_TRANSCRIPTS")
    if env is not None:
        return env
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        _env.skip(f"no transcript directory ({projects} does not exist)",
                  "pass --transcripts DIR or set X4_TRANSCRIPTS")
    root = _paths.game_root()
    if root is None:
        _env.skip("the game root is not configured, so the project directory "
                  "cannot be derived",
                  "pass --transcripts DIR or set X4_TRANSCRIPTS")
    # Claude Code names a project directory after its path with every
    # non-alphanumeric character replaced. Verified against the real directory;
    # if that convention ever changes this REFUSES rather than picking a
    # neighbour.
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(root))
    d = projects / slug
    if not d.is_dir() or not any(d.glob("*.jsonl")):
        _env.skip(f"no transcripts for this workspace at {d}",
                  "the project directory is derived from the game root; pass "
                  "--transcripts DIR or set X4_TRANSCRIPTS if it lives elsewhere")
    return d


class Scan:
    """Commands pulled from transcripts, with the denominator that qualifies them."""

    def __init__(self) -> None:
        self.files = 0
        self.lines = 0
        self.unreadable: list[str] = []
        self.commands: list[tuple[str, str]] = []       # (YYYY-MM-DD, command)

    @property
    def span(self) -> tuple[str, str]:
        ds = sorted(d for d, _ in self.commands if d)
        return (ds[0], ds[-1]) if ds else ("", "")

    def banner(self) -> str:
        lo, hi = self.span
        return (f"scanned {self.files} transcript(s), {self.lines} line(s), "
                f"{len(self.unreadable)} unreadable -> {len(self.commands)} shell "
                f"command(s), {lo or '?'} .. {hi or '?'}")


def scan(tdir: Path) -> Scan:
    s = Scan()
    for fn in sorted(tdir.glob("*.jsonl")):
        s.files += 1
        try:
            text = fn.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            s.unreadable.append(f"{fn.name}: {exc}")
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            s.lines += 1
            try:
                rec = json.loads(line)
            except ValueError:
                s.unreadable.append(f"{fn.name}: unparseable line")
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            day = (rec.get("timestamp") or "")[:10]
            for c in content:
                if not (isinstance(c, dict) and c.get("type") == "tool_use"):
                    continue
                if c.get("name") not in ("Bash", "PowerShell"):
                    continue
                cmd = str((c.get("input") or {}).get("command") or "")
                if cmd:
                    s.commands.append((day, cmd))
    if s.files == 0:
        _env.skip(f"no *.jsonl transcripts in {tdir}",
                  "refusing rather than reporting every capability as unused")
    if not s.commands:
        _env.skip(f"{s.files} transcript(s) in {tdir} yielded no shell commands",
                  "a zero here would misreport the whole surface as never invoked")
    return s


def invocation_re(cli: str, sub: str | None) -> re.Pattern[str]:
    """An invocation POSITION, not a mention.

    `cd tools/x4validate` is a directory, not a call -- counting it inflated the
    hand measurement 4.8x (6,106 apparent invocations against a real 1,263).
    """
    head = r"(?:^|[\n;|(]|&&|\|\||uv run |uvx |python -m )\s*" + re.escape(cli)
    tail = (r"\s+" + re.escape(sub) + r"\b") if sub else r"\s"
    return re.compile(head + tail)


# ---------------------------------------------------------------------------
_USAGE_FLAG = re.compile(r"\[(-{1,2}[A-Za-z0-9][\w-]*)( [A-Z][A-Z0-9_]*| \{[^}]*\})?\]")


def usage_flags(help_text: str) -> dict[str, bool] | None:
    """{flag: takes a value} for the GLOBAL flags in a CLI's own `usage:` paragraph, or None when there
    is no usage line (unknown, never "no flags"). argparse prints a value flag as `[--file FILE]` and a
    valueless one as `[--version]`, so the arity is read from the parser, not guessed."""
    m = re.search(r"^usage:.*?(?:\n\s*\n|\Z)", help_text or "", re.S | re.M)
    if not m:
        return None
    return {f: bool(v) for f, v in _USAGE_FLAG.findall(m.group(0))}


def _subcommand_in(argv: list[str], subs: list[str],
                   flags: dict[str, bool] | None = None) -> str | None:
    """The subcommand in *argv*'s subcommand position, or None when that cannot be told (UNDECIDABLE).

    The first word that is not a flag or a flag's value decides: a subcommand, or None. `--flag=value` is
    one word. *flags* is `usage_flags` for the CLI ({flag: takes a value}); a flag it declares consumes
    exactly its value. A flag of UNKNOWN arity consumes the next word only when that word is not a
    subcommand; when it IS one, the word may be the flag's value or the subcommand, so the answer is None.
    Until 2026-11-14 every flag was assumed to take a value, and the review MEASURED
    `["--quiet", "query", "pause"]` -> `pause` and `["--db=x", "query", "pause"]` -> `pause`: confident and
    wrong. A loud "cannot tell" beats a quiet mis-attribution, which is why this function exists.
    """
    known = flags or {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("-"):
            if "=" in a:
                i += 1
                continue
            if a in known:
                i += 2 if known[a] else 1
                continue
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and nxt in subs:
                return None
            i += 2
            continue
        return a if a in subs else None
    return None


class Coverage:
    """What `qa_sweep` exercises, keyed the way each CLI actually works.

    ⚠ THREE argv shapes, and enumerating two of them was wrong TWICE.

      `["dev/some_mod"]`                      single-command CLI -> a mod PATH
      `["triage"]`                            subcommand first
      `["--registry", <path>, "dashboard"]`   a GLOBAL FLAG, then the subcommand

    Keying on `argv[0]` broke on the first shape (x4validate/x4diff/x4similar
    reported untested while qa_sweep exercises them 9, 3 and 2 times), and then
    again on the third: **7 of 54 cells lead with a global flag**, which made
    `x4modlist dashboard/needs-review/verify/source` read as gaps when they are
    exercised against a sandbox registry. F75 was published saying 17 when the
    answer is 13.

    So the subcommand is now SEARCHED FOR anywhere in argv, against the live
    surface, and a cell where none can be found is recorded in `undecidable`
    rather than silently contributing nothing. A fourth shape then shows up as a
    finding instead of quietly shrinking coverage.
    """

    def __init__(self, cells, subs_by_cli: dict[str, list[str]],
                 flags_by_cli: dict[str, dict[str, bool]] | None = None) -> None:
        self.pairs: set[tuple[str, str]] = set()
        self.tools: set[str] = set()
        #: cells on a subcommand-bearing CLI where no subcommand could be found.
        #: Coverage for these is UNKNOWABLE, which is not the same as absent.
        self.undecidable: list[tuple[str, str]] = []
        for c in cells:
            self.tools.add(c.tool)
            subs = subs_by_cli.get(c.tool)
            if not subs:                       # single-command CLI, or unknown
                self.pairs.add((c.tool, ""))
                continue
            found = _subcommand_in(c.argv or [], subs, (flags_by_cli or {}).get(c.tool))
            if found is None:
                self.undecidable.append((c.tool, getattr(c, "label", "")))
            else:
                self.pairs.add((c.tool, found))

    def covers(self, cli: str, sub: str) -> bool:
        return (cli, sub) in self.pairs if sub else cli in self.tools


def _baseline_missing() -> list[str]:
    """The qa_sweep gaps already accepted. Absent baseline = nothing accepted,
    so on a fresh machine every gap reads as NEW -- which is correct: nobody has
    signed off on that machine's backlog yet."""
    if not BASELINE.exists():
        return []
    try:
        return list(json.loads(BASELINE.read_text(encoding="utf-8"))
                    .get("missing") or [])
    except (ValueError, OSError) as exc:
        # Not swallowed: an unreadable baseline must not silently mean "nothing
        # was ever accepted", which would flood the run with false NEW rows.
        raise RuntimeError(f"baseline {BASELINE.name} is unreadable: {exc}") from exc


def surface_was_exercised(caps: dict) -> bool:
    """Is this a credible reading of a workspace whose own tools these are?

    An empty surface is not credible either -- it would mean the roster produced
    nothing, and `all()` over an empty mapping is vacuously True, which is the
    "green that could not go red" shape.
    """
    return bool(caps) and any(v["invoked"] for v in caps.values())


def qa_sweep_coverage(subs_by_cli: dict[str, list[str]]) -> tuple[Coverage | None, str]:
    """(Coverage, note). None is a REFUSAL, never "covers nothing".

    Importing qa_sweep runs its module-level mod picking, which calls SystemExit
    on an unconfigured machine -- so a bare `except Exception` would miss it and
    the gate would report every capability untested over a roster it never read.
    """
    try:
        sys.path.insert(0, str(ROOT / "gates"))
        import qa_sweep  # noqa: PLC0415
    except BaseException as exc:                        # SystemExit included
        return None, f"could not import qa_sweep ({type(exc).__name__}: {exc})"
    cells = getattr(qa_sweep, "CELLS", None)
    if not cells:
        return None, "qa_sweep.CELLS is absent or empty"
    return Coverage(cells, subs_by_cli), ""


# ---------------------------------------------------------------------------
def audit() -> dict:
    tdir = transcript_dir()
    s = scan(tdir)
    print(f"transcripts: {tdir}")
    print(f"  {s.banner()}")
    for u in s.unreadable[:5]:
        print(f"    unreadable: {u}")
    if len(s.unreadable) > 5:
        print(f"    ... and {len(s.unreadable) - 5} more")

    caps: dict[str, dict] = {}
    unenumerable: list[str] = []
    subs_by_cli: dict[str, list[str]] = {}
    for cli in cli_roster():
        subs, note = subcommands(cli)
        if subs is None:
            unenumerable.append(f"{cli}: {note}")
            continue
        subs_by_cli[cli] = list(subs)
        for sub in (subs or [""]):
            key = f"{cli} {sub}".strip()
            rx = invocation_re(cli, sub or None)
            inv = [d for d, c in s.commands if rx.search(c)]
            # NAMED is the capability appearing ANYWHERE in a command -- in a
            # heredoc writing docs, a gate roster, a --help. It must be keyed at
            # the same granularity as INVOKED: keying it on the CLI alone gave
            # every one of x4modlist's 12 subcommands `named 151`, which makes
            # "named recently but never run" true of subcommands nobody has
            # mentioned at all, and the ratio meaningless.
            needle = f"{cli} {sub}" if sub else cli
            named = [d for d, c in s.commands if needle in c]
            caps[key] = {
                "cli": cli, "sub": sub,
                "invoked": len(inv),
                "named": len(named),
                "first": min(inv) if inv else "",
                "last": max(inv) if inv else "",
                "last_named": max(named) if named else "",
            }
    # SECOND, INDEPENDENT GUARD on the same failure. The derived-slug rule above
    # is a claim about a convention this repo does not own; if it ever stops
    # holding, the symptom is transcripts belonging to some OTHER project, and
    # that reads as "every capability unused" -- a well-formed, confident, wholly
    # wrong answer. Zero invocations across the ENTIRE surface is not a credible
    # measurement of a workspace whose own tools these are, so refuse. Two guards
    # because one of them rests on someone else's naming scheme.
    if not surface_was_exercised(caps):
        _env.skip(
            f"none of the {len(caps)} capabilities was invoked once in "
            f"{len(s.commands)} commands from {tdir}",
            "that is not a credible reading of this workspace -- these are almost "
            "certainly another project's transcripts. Pass --transcripts DIR.")
    return {"caps": caps, "unenumerable": unenumerable,
            "subs_by_cli": subs_by_cli,
            "scanned": {"transcripts": s.files, "lines": s.lines,
                        "commands": len(s.commands),
                        "unreadable": len(s.unreadable),
                        "span": list(s.span)}}


def _days_since(iso: str) -> int | None:
    """`None` means NO date -- the capability was never invoked.

    A MALFORMED date is not that, and must not collapse into it: returning None
    there would drop the capability out of the dormancy list with nothing said,
    which is absence and non-answer becoming the same value. These dates come
    from our own scanner, so a malformed one is a bug in this file, not a
    property of the data -- raise it.
    """
    if not iso:
        return None
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return (date.today() - date(y, m, d)).days
    except ValueError as exc:
        raise ValueError(
            f"malformed date {iso!r} came out of the transcript scan") from exc


def main() -> int:
    result = audit()
    caps: dict[str, dict] = result["caps"]
    unenumerable: list[str] = result["unenumerable"]

    findings: list[str] = []
    refusals: list[str] = []           # could-not-look, kept apart from findings

    print()
    print("=" * 78)
    print(f"SURFACE: {len(caps)} capability(ies) enumerated"
          + (f", {len(unenumerable)} CLI(s) NOT enumerable" if unenumerable else ""))
    for u in unenumerable:
        print(f"  REFUSED  {u}")
        findings.append(f"surface not enumerable -- {u}")

    used = {k: v for k, v in caps.items() if v["invoked"]}
    never = sorted(k for k, v in caps.items() if not v["invoked"])
    print(f"  invoked at least once: {len(used)}"
          f"   never invoked: {len(never)}"
          f"   (buckets sum to {len(used) + len(never)} of {len(caps)})")
    assert len(used) + len(never) == len(caps), "buckets must sum to the surface"

    # --- INFO: dormancy, the shape this gate exists to surface -------------
    print()
    print("DORMANT -- named recently, not invoked in "
          f"{DORMANT_DAYS}+ days  (INFO, threshold not calibrated)")
    dormant = []
    for k, v in sorted(caps.items()):
        d_inv, d_named = _days_since(v["last"]), _days_since(v["last_named"])
        if d_inv is not None and d_inv >= DORMANT_DAYS and d_named is not None \
                and d_named < DORMANT_DAYS:
            dormant.append((d_inv, k, v))
    for d_inv, k, v in sorted(dormant, reverse=True):
        print(f"  {d_inv:4d}d  {k:<24} invoked {v['invoked']:4d}  "
              f"named {v['named']:5d}  last run {v['last']}")
    if not dormant:
        print("  none")

    print()
    print("NEVER INVOKED  (INFO)")
    for k in never:
        print(f"        {k:<24} named {caps[k]['named']}")
    if not never:
        print("  none")

    # --- FINDING: functionally untested ------------------------------------
    print()
    covered, note = qa_sweep_coverage(result.get("subs_by_cli") or {})
    if covered is None:
        print(f"  REFUSED  qa_sweep coverage: {note}")
        refusals.append(f"qa_sweep coverage not evaluable -- {note}")
        # keep whatever was already accepted; a refusal must not wipe the baseline
        result["missing"] = _baseline_missing()
    else:
        # A cell whose subcommand cannot be located is UNKNOWABLE coverage, not
        # absent coverage. This is the guard on the CLASS: argv had three shapes
        # and two were enumerated, twice. A fourth shape now fails loudly here
        # instead of quietly shrinking the covered set.
        for tool, label in covered.undecidable:
            print(f"  UNDECIDABLE  qa_sweep cell {tool} \"{label}\" -- no known "
                  "subcommand found in its argv")
            findings.append(
                f"qa_sweep cell has an unrecognised argv shape -- {tool} \"{label}\"")
        missing = sorted(k for k, v in caps.items()
                         if not covered.covers(v["cli"], v["sub"]))
        print(f"QA-SWEEP COVERAGE: {len(caps) - len(missing)} of {len(caps)} "
              f"capabilities exercised, {len(missing)} not")
        # Baseline-relative, NOT absolute. There are 13 real gaps today, and a
        # gate that fails on every run over a known backlog is the flood this
        # file's docstring argues against -- it trains you to ignore the runner,
        # which is worse than not having one. So the KNOWN set is accepted and a
        # NEW uncovered capability is the finding. The backlog stays visible in
        # the listing below and in gates/README.md; it is not silenced, it is
        # just not re-litigated on every run.
        known = set(_baseline_missing())
        for k in missing:
            new = k not in known
            print(f"  {'NEW      ' if new else 'known    '}{k}")
            if new:
                findings.append(f"not exercised by qa_sweep (NEW) -- {k}")
        for k in sorted(known - set(missing)):
            print(f"  fixed    {k}  <- now exercised; re-record the baseline")
        result["missing"] = missing

    # --- FINDING: drift against the recorded baseline -----------------------
    print()
    if RECORD:
        BASELINE.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"recorded baseline -> {BASELINE.name} ({len(caps)} capabilities)")
        # Deliberately NOT an early `return 0`. Recording accepts the CURRENT
        # usage picture as the drift baseline; it says nothing about the absolute
        # findings above, which are not baseline-relative. Returning 0 here would
        # hand back an all-clear over 13 real coverage gaps -- a green that could
        # not have gone red.
    elif not BASELINE.exists():
        print(f"no baseline at {BASELINE.name} -- run with --record to create one. "
              "Drift is NOT being checked.")
        # A REFUSAL, not a pass (AUDIT-2026-09-24 GT-8): half of this gate did not run,
        # and its siblings return 2 in exactly this state.
        refusals.append(f"drift: no baseline at {BASELINE.name} (run with --record)")
    else:
        old = json.loads(BASELINE.read_text(encoding="utf-8"))["caps"]
        drift: list[str] = []                 # counted, never grepped back out of
        for k, v in sorted(caps.items()):     # `findings` -- a substring filter is
            was = old.get(k)                  # not a way to count a structured set
            if was is None:
                if not v["invoked"]:
                    drift.append(f"new capability never invoked -- {k}")
                continue
            if was["invoked"] and not v["invoked"]:
                drift.append(f"regressed to never-invoked -- {k} "
                             f"(baseline had {was['invoked']})")
        for k in sorted(set(old) - set(caps)):
            drift.append(f"capability disappeared from the surface -- {k}")
        findings.extend(drift)
        print(f"drift vs {BASELINE.name}: {len(drift)} baseline finding(s)")

    print()
    print("=" * 78)
    for r in refusals:
        print(f"  NOT EVALUATED: {r}")
    if findings:
        print(f"FINDINGS: {len(findings)}")
        for f in findings:
            print(f"  - {f}")
        return 1
    if refusals:
        print(f"REFUSING: {len(refusals)} part(s) of this gate could not be evaluated, so "
              "'no findings' would be a guess. rc 2, not a finding and not a pass.",
              file=sys.stderr)
        return 2
    print("No findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
