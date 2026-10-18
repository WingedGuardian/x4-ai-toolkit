r"""`x4debug` — the engine's own verdict, bucketed, attributed, and compared to ours.

The engine log is the only ground truth we have about what actually happens at
load and at galaxy generation. Until now, reading it was a hand-rolled
`grep | sed | sort | uniq -c` pass, repeated from scratch every session, producing
a table nobody could diff against the last one.

Three things follow from that, and they are the whole design:

**1. The rows must sum to the input.** A hand-rolled table has no way to notice it
dropped a shape — which is exactly how `parse_debug` lost 44% of a log for two
weeks. `triage` prints the residue row unconditionally, including when it is zero,
because a row that appears only when non-zero teaches the reader to read its
absence as "nothing there" rather than "not measured".

**2. Two states are compared PER ITEM, never by their totals.** The 2026-08-13 log
holds 342 failing ops in one cpsdo file; `x4validate --tier b` independently
predicted 310 for that mod. Close enough to look like agreement, and not the same
set. `crosscheck` reports three buckets, and the one that matters most is
`observed-not-predicted`: an op the ENGINE skipped that we never predicted is a
blind spot in the validator, and folding it into a total would hide it.

**3. Subsystem errors name an ENTITY, not a mod.** `[JobEngine] ... JobID: 'x'`
says a job could not spawn; it never says whose job it is. Attribution is
therefore a SEARCH against the effective store, and must be reported as a search —
with its denominator — rather than as a lookup that quietly returns nothing.

What this does NOT do, stated so the scope message can say it: it classifies, it
never fixes, and it never edits a mod. Which buckets are *benign* stays a dated
decision in KNOWLEDGEBASE.md that this tool cites — a suppression compiled into
code outlives the reason for it, and one such line here survived the mod it
referred to being uninstalled.
"""

from __future__ import annotations

import collections
import json
import re
import shutil
import sys
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import _debuglog, _freshness, _paths
from . import __version__

#: A new game generates the galaxy; a save load does not. God/job/station errors
#: occur only in the former, so raw counts are NOT comparable across the boundary.
_NEW_GAME_MARKER = "Universe generation begins"


# --------------------------------------------------------------------------- #
# crosscheck                                                                   #
# --------------------------------------------------------------------------- #

@dataclass
class OpComparison:
    """Engine-observed vs validator-predicted, per item.

    `observed_total`/`predicted_total` keep the multiplicities: the engine logs an
    op once per application pass, so the same selector legitimately appears twice.
    Collapsing to sets would silently reconcile 342 with 310 and hide whichever
    gap is real.
    """

    both: list[str] = field(default_factory=list)
    predicted_only: list[str] = field(default_factory=list)
    observed_only: list[str] = field(default_factory=list)
    observed_total: int = 0
    predicted_total: int = 0

    @property
    def clean(self) -> bool:
        return not self.predicted_only and not self.observed_only


def compare_ops(observed: list[str], predicted: list[str]) -> OpComparison:
    o, p = collections.Counter(observed), collections.Counter(predicted)
    return OpComparison(
        both=sorted(o.keys() & p.keys()),
        predicted_only=sorted(p.keys() - o.keys()),
        observed_only=sorted(o.keys() - p.keys()),
        observed_total=sum(o.values()),
        predicted_total=sum(p.values()),
    )


class UnparsedFinding(RuntimeError):
    """A `sel` finding whose selector could not be extracted.

    Raised rather than skipped, on purpose. See `predicted_ops`.
    """


#: Fallback only — `Finding.sel` is the authority (see `predicted_ops`).
#:
#: x4validate reports TWO cardinality failures and RFC 5261 makes both fatal: a
#: `sel` must match exactly one node, so 0 matches and 2 matches are equally
#: skipped by the engine. The "matched nothing" message also carries OPTIONAL
#: suffixes — " (silent)" and " [if= passed: <cond>]" — which are NOT part of the
#: selector and must be stripped, or the comparison can never match.
_SEL_SUFFIX = r"(?:\s*\(silent\))?(?:\s*\[if= passed:[^\]]*\])?"
_SEL_SHAPES = (
    re.compile(r"sel matched nothing:\s*(?P<sel>.+?)" + _SEL_SUFFIX + r"\s*$"),
    re.compile(r"sel matched \d+ nodes \([^)]*\):\s*(?P<sel>.+?)" + _SEL_SUFFIX + r"\s*$"),
)


def op_key(vpath: str, sel: str) -> str:
    """One op's comparison key: the patch FILE and the selector, never the selector alone.

    The same selector legitimately appears in several files of one mod (17 such
    collapses in one real mod, AUDIT-2026-09-24 RT-1), so keying on the bare `sel`
    let an op the engine skipped in `libraries/wares` read as "agreed" with a
    prediction about `libraries/jobs.xml`. The engine drops the `.xml` extension on
    these lines (`_debuglog.xml_candidates`, quirk 1) and the validator keeps it, so
    both sides are normalised the same way: posix separators, lower case, no `.xml`.

    A finding with no file (only a hand-built one; the validator always sets it)
    keys on the bare selector, so it can never be matched to an engine line in
    some other file -- it lands in a one-sided bucket, where it is visible.
    """
    v = vpath.replace(chr(92), "/").strip().removeprefix("./").lstrip("/").lower()
    if v.endswith(".xml"):
        v = v[:-4]
    return f"{v} :: {sel}" if v else sel


def predicted_ops(report) -> list[str]:
    """The ops x4validate says are cardinality failures, keyed by `op_key`.

    Reads `Finding.sel` — the selector verbatim — and falls back to parsing the
    message only for findings that predate that field.

    Two bugs of the same shape were committed here, both caught by running the
    comparison across the whole modlist rather than one mod:

    1. The first version matched only "matched nothing", silently dropped all 116
       "matched 2 nodes" predictions for cpsdo_faction, and reported those very
       ops as "a VALIDATOR BLIND SPOT".
    2. The second swallowed the " (silent)" suffix into the selector, so six
       xenon_backup ops could never match and were reported as predicted-only.

    Both are the register's one shape — a narrowing step reporting its residue as
    a finding — inside the tool built to detect it. Hence: structured field
    first, and **raise** on anything unreadable, because a silent skip here does
    not lose a row, it invents one somewhere else.
    """
    out = []
    for f in getattr(report, "findings", []):
        if f.category != "sel":
            continue
        vpath = getattr(f, "vpath", "") or ""
        structured = getattr(f, "sel", "")
        if structured:
            out.append(op_key(vpath, structured))
            continue
        for rx in _SEL_SHAPES:
            m = rx.search(f.message)
            if m:
                out.append(op_key(vpath, m.group("sel")))
                break
        else:
            # Not every "sel"-category finding IS a cardinality failure — invalid
            # sel=/if= and guarded no-ops share the category and carry no selector
            # to compare. Those are legitimately not predictions.
            if _NON_CARDINALITY.search(f.message):
                continue
            raise UnparsedFinding(
                "x4validate emitted a `sel` finding this comparison cannot read, so "
                "the prediction set would be short by at least one op:\n  "
                f"{f.message}\nAdd its shape to _SEL_SHAPES.")
    return out


#: `sel`-category findings that are not cardinality failures and carry no
#: selector to compare against the engine.
_NON_CARDINALITY = re.compile(
    r"invalid (?:sel|if)=|skipped: if= is false|unparseable XML")


def observed_ops(parsed: _debuglog.ParsedLog, folder: str) -> list[str]:
    """The ops the ENGINE skipped for one mod folder (shapes E/F), keyed by `op_key`."""
    low = folder.lower()
    return [op_key(e.vpath, e.sel) for e in parsed.entries
            if e.cardinality and e.folder.lower() == low]


# --------------------------------------------------------------------------- #
# baseline                                                                     #
# --------------------------------------------------------------------------- #

def _meta_path(archived: Path) -> Path:
    return Path(str(archived) + ".meta.json")


def find_archived(data: bytes, dest_dir: Path) -> Path | None:
    """An archived copy whose BYTES equal *data*, or None (V8: content dedupe).

    By content, never by name or mtime: names carry a random suffix since 5398907, and
    one log can be re-stamped. Same-size candidates only, each hashed in full."""
    if not dest_dir.is_dir():
        return None
    want = hashlib.sha256(data).hexdigest()
    for cand in sorted(dest_dir.glob("debug-*.txt")):
        try:
            if cand.stat().st_size == len(data) and                     hashlib.sha256(cand.read_bytes()).hexdigest() == want:
                return cand
        except OSError:
            continue  # silent-ok: an unreadable candidate is not a match; we archive anew
    return None


def archive(log: Path, dest_dir: Path, config=None) -> Path:
    """Copy a log into the archive with a fingerprint, so two runs are comparable.

    Stamped on the CONTENT axis only. A log records a game launch, not a merge
    product: marking it engine-dependent would make it read STALE every time
    `_merge.py` is touched, and a banner that cries wolf is one the reader learns
    to skip. `engine_dependent: false` is recorded explicitly rather than left
    implied, so the next reader need not infer the intent.
    """
    # ONE snapshot of the bytes: the game may rewrite the file between reads.
    data = log.read_bytes()
    existing = find_archived(data, dest_dir)
    if existing is not None:
        return existing     # identical content already archived: write nothing (V8)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # LOCAL time, matching the `captured` field below. A UTC filename beside a
    # local `captured` reads as two different launches to anyone diffing the
    # archive by name.
    stamp = datetime.fromtimestamp(log.stat().st_mtime).strftime("%Y-%m-%dT%H%M")
    # BYTES FIRST, deliberately. This source is a file the GAME owns: X4 rewrites
    # `debug.txt` (and `{profile}/uidata.xml`) on exit, so the bytes are the one
    # thing that cannot be re-obtained without another play session. Losing them
    # to a metadata failure would be the expensive mistake.
    # Exclusive creation, even if captures share an mtime or another writer wins
    # the proposed name. Retain the previous log AND its metadata.
    while True:
        out = dest_dir / f"debug-{stamp}-{uuid.uuid4().hex}.txt"
        try:
            target = out.open("xb")
            break
        except FileExistsError:
            continue  # silent-ok: exclusive reservation lost; retry without overwriting evidence
    with target:
        target.write(data)
    shutil.copystat(log, out)

    # ...but copy-first left the other half open: if parsing or fingerprinting
    # raised, the archive kept a log with NO meta, and `read_archive_meta` threw on
    # it. A partial artifact that cannot say what it is is neither present nor
    # absent -- the shape this toolkit exists to refuse. So the meta is ALWAYS
    # written, and says so when it is degraded.
    meta = {
        "source": str(log),
        # stat the COPY, not the source: by now the game may have replaced it.
        "captured": datetime.fromtimestamp(out.stat().st_mtime).isoformat(timespec="seconds"),
        "engine_dependent": False,
        "degraded": False,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    try:
        text = out.read_text(encoding="utf-8", errors="replace")
        parsed = _debuglog.parse_log_text(text)
        cfg = config
        if cfg is None:
            from . import _merge
            cfg = _merge.Config()
        meta.update({
            "new_game": _is_new_game(text),
            "total_errors": parsed.total,
            "classified": len(parsed.classified),
            "unclassified": len(parsed.unclassified),
        })
        # `Path("")` is `Path(".")`, so an UNRESOLVED extensions root used to make
        # this fingerprint the CURRENT DIRECTORY as though it were the mod install.
        # REPRODUCED with a control that goes both ways: run from two directories
        # holding 1 and 3 subfolders, an unresolved root produced two DIFFERENT
        # content fingerprints and enumerated those subfolders as installed mods
        # (1 and 3 detail rows); a root that resolves produced the SAME fingerprint
        # from both cwds and 0 rows. The only differing variable is the root.
        #
        # A baseline exists to record WHICH WORLD a capture was taken in, so an
        # unresolved root is precisely the case it cannot answer -- and `degraded`
        # is the field for saying so. Reporting False there is worse than refusing:
        # two captures of one world claim to be from different worlds, while the
        # flag whose whole job is to mark a fingerprint untrustworthy says it is
        # fine. That also contradicts this block's own comment above -- "says so
        # when it is degraded".
        #
        # CLAUDE.md verifier-bug #45 and F46 are the same empty-Path mechanism;
        # what is new here is that it reaches a PERSISTED artifact rather than one
        # ad-hoc measurement.
        ext = _paths.game_extensions()
        if ext is None:
            meta["degraded"] = True
            meta["degraded_reason"] = (
                "the game extensions root is not configured (X4_EXTENSIONS /"
                " X4_GAME), so NO content fingerprint was taken: it would have"
                " described the current directory instead of the mod install")
        else:
            meta["fingerprint"] = _freshness.fingerprint(cfg, ext)
    except Exception as exc:  # noqa: BLE001 - the copy must survive ANY meta failure
        meta["degraded"] = True
        meta["degraded_reason"] = repr(exc)
    # A companion can predate this raw-file reservation or appear meanwhile.
    # Refuse that collision, retaining both the captured bytes and foreign meta.
    with _meta_path(out).open("x", encoding="utf-8") as companion:
        json.dump(meta, companion, indent=2)
    return out


def read_archive_meta(archived: Path) -> dict:
    return json.loads(_meta_path(archived).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# triage                                                                       #
# --------------------------------------------------------------------------- #

def _is_new_game(text: str) -> bool:
    return _NEW_GAME_MARKER in text


def _bucket(e: _debuglog.DebugError) -> tuple[str, str]:
    """(bucket kind, label) for one error. Mods first, then subsystems."""
    if e.folder:
        return "mod", e.folder
    if e.script_name:
        return "script", e.script_name
    if e.subsystem:
        return "subsystem", e.subsystem
    if e.lookup:
        return "index", e.lookup_index or "index"
    return "unclassified", "unclassified"


_SECTIONS = (
    ("mod", "BY MOD (engine named a file the mod owns)"),
    ("script", "BY SCRIPT (resolves to a mod via its name= attribute)"),
    ("subsystem", "BY SUBSYSTEM (engine named an ENTITY, not a mod)"),
    ("index", "INDEX LAYER"),
)


def triage(log: Path, out=None) -> int:
    # Resolved at CALL time, not definition time: a default of `sys.stdout` binds
    # the stream that existed at import, so anything that later replaces it (a test
    # harness, a redirect) is written past rather than into. The bug is invisible in
    # normal use and makes the output untestable, which is worse.
    out = sys.stdout if out is None else out
    text = log.read_text(encoding="utf-8", errors="replace")
    parsed = _debuglog.parse_log_text(text)
    new_game = _is_new_game(text)
    mtime = datetime.fromtimestamp(log.stat().st_mtime)

    def w(s=""):
        print(s, file=out)

    w(f"x4debug triage  {log}")
    w(f"  captured        : {mtime:%Y-%m-%d %H:%M:%S}  (mtime)")
    w(f"  session type    : {'NEW GAME (galaxy generated)' if new_game else 'UNKNOWN (menu or save load)'}")
    if new_game:
        w("                    god / job / station errors occur only at generation, so")
        w("                    raw counts are NOT comparable with a save-load log")
    w(f"  {parsed.coverage_note()}")
    w()

    groups: collections.Counter = collections.Counter()
    for e in parsed.entries:
        groups[_bucket(e)] += 1

    for kind, title in _SECTIONS:
        rows = sorted(((lbl, n) for (k, lbl), n in groups.items() if k == kind),
                      key=lambda r: -r[1])
        if not rows:
            continue
        w(f"  {title}")
        for lbl, n in rows:
            w(f"    {n:6d}  {lbl}")
        w(f"    {sum(n for _, n in rows):6d}  = subtotal")
        w()

    # Printed unconditionally, zero included — see the module docstring.
    resid = groups.get(("unclassified", "unclassified"), 0)
    w(f"  UNCLASSIFIED      {resid:6d}   (a shape this parser does not know)")
    if resid:
        w("    Any count derived from the buckets above is a FLOOR, not a total.")
        for msg, n in collections.Counter(
                u.message[:96] for u in parsed.unclassified).most_common(6):
            w(f"    {n:6d}  {msg}")
    w()

    total_rows = sum(groups.values())
    if total_rows != parsed.total:  # pragma: no cover - the invariant is structural
        w(f"  rows {total_rows} != lines read {parsed.total}: VIOLATED")
        w("  !! the table does not account for the input; treat every number above "
          "as unreliable")
        return 1
    w(f"  rows {total_rows} == lines read {parsed.total}: OK")
    w()
    w('  Which buckets are BENIGN is a dated decision in KNOWLEDGEBASE.md ("Debug '
      'triage"),')
    w("  not a rule compiled in here — a suppression in code outlives its reason.")
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _resolve_log(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg)
        return p if p.is_file() else None
    p = _paths.debug_log()
    return p if p and p.is_file() else None


def _default_archive_dir() -> Path:
    reg = _paths.registry()
    if reg:
        return reg.parent.parent / "_reports" / "debug"
    return Path.cwd() / "debug-archive"


@_paths.refuses_unconfigured
def main(argv: list[str] | None = None) -> int:
    import argparse

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # silent-ok: console encoding shim. Failure changes how output LOOKS,
        # never what was examined.

    p = argparse.ArgumentParser(
        prog="x4debug",
        description="Triage the engine's debug.txt, and compare it to what we predicted.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("triage", help="bucket and attribute every [=ERROR=] line")
    pt.add_argument("log", nargs="?", help="debug.txt (default: $X4_DEBUGLOG / $X4_PROFILE)")

    pc = sub.add_parser(
        "crosscheck",
        help="per-item diff of engine-skipped ops vs x4validate's prediction")
    pc.add_argument("mod", help="mod path (use the DEPLOYED copy — load order matters)")
    pc.add_argument("log", nargs="?", help="debug.txt (default: $X4_DEBUGLOG / $X4_PROFILE)")
    pc.add_argument("--tier", default="b", choices=["a", "b"],
                    help="x4validate tier for the prediction: a = base+DLC only, b = the "
                         "ACTIVE modlist -- installed AND enabled -- in load order (default: %(default)s)")

    pb = sub.add_parser("baseline", help="archive this log with a content fingerprint")
    pb.add_argument("log", nargs="?", help="debug.txt (default: $X4_DEBUGLOG / $X4_PROFILE)")
    pb.add_argument("--dest", help="archive dir (default: dev\\_reports\\debug)")

    args = p.parse_args(argv)

    log = _resolve_log(getattr(args, "log", None))
    if log is None:
        print("x4debug: could not read a debug log. Pass one explicitly, or set "
              "$X4_DEBUGLOG / $X4_PROFILE.", file=sys.stderr)
        print("  This is a NON-ANSWER, not a clean log — a run that examined "
              "nothing must never exit 0.", file=sys.stderr)
        return 2

    if args.cmd == "triage":
        return triage(log)

    if args.cmd == "baseline":
        dest = Path(args.dest) if args.dest else _default_archive_dir()
        try:
            already = find_archived(log.read_bytes(), dest)
        except OSError:
            already = None   # silent-ok: archive() below reads it again and raises if it must
        out = archive(log, dest)
        meta = read_archive_meta(out)
        if already is not None and already == out:
            print(f"  already archived, identical content: {out} -- nothing written")
        else:
            print(f"  archived {out}")
        # The DEGRADED case is checked FIRST, and it is not hypothetical: archive()
        # builds these five keys with a single `meta.update({...})` inside a try, so
        # any raise in it -- parse_log_text, Config(), game_extensions(), fingerprint()
        # -- leaves NONE of them set and records `degraded` instead. This consumer read
        # all five unconditionally, so the degraded path it exists to serve ended in a
        # KeyError traceback. REPRODUCED end-to-end through `x4debug baseline` using the
        # same monkeypatch the shipped test already uses: KeyError: 'total_errors'.
        #
        # rc 1, not 0: a baseline that cannot state its own fingerprint cannot back a
        # later comparison, and reporting it as a clean capture is the failure the
        # whole two-axis freshness contract exists to prevent. The ARCHIVE itself is
        # fine and is kept -- that is why archive() is written to survive any meta
        # failure in the first place.
        if meta.get("degraded"):
            print(f"  !! the archive was written, but its metadata is DEGRADED: "
                  f"{meta.get('degraded_reason', 'no reason recorded')}")
            print("     This capture cannot be compared against a later one, because "
                  "it does not know what world it was taken in.")
            return 1
        print(f"  {meta['total_errors']} errors ({meta['unclassified']} unclassified), "
              f"new_game={meta['new_game']}")
        print(f"  content fingerprint {meta['fingerprint']['content']} "
              "(engine axis N/A — a log is not a merge product)")
        return 0

    return _crosscheck_cmd(args, log)


def _crosscheck_cmd(args, log: Path) -> int:
    from . import _check, _merge

    mod = Path(args.mod)
    if not mod.is_dir():
        print(f"x4debug: not a mod directory: {mod}", file=sys.stderr)
        return 2

    parsed = _debuglog.parse_log(log)
    if parsed.unreadable is not None:
        # ParsedLog's own contract (_debuglog.py): "Consumers must refuse on it."
        # Reading straight into observed_ops made an unreadable log produce ZERO
        # observed ops, which prints exactly like a mod the engine and the
        # validator agree on -- rc 0 on a run that examined nothing.
        print(f"x4debug: {log} is unreadable ({parsed.unreadable}) -- nothing was "
              "examined, so crosscheck has nothing to compare.", file=sys.stderr)
        return 2
    observed = observed_ops(parsed, mod.name)

    config = _merge.Config()
    report = _check.validate(mod, config, tier=args.tier)
    try:
        predicted = predicted_ops(report)
    except UnparsedFinding as exc:
        # `predicted_ops` raises on purpose; the CLI has to land it cleanly. The
        # state here is worse than "no answer": the prediction set is INCOMPLETE,
        # so every bucket below would be wrong in a way that looks authoritative.
        # Same contract the staleness CLI follows — a reason and a distinct code,
        # never a traceback, which says nothing about which state you are in.
        print(f"x4debug: cannot compare — the prediction set is incomplete.\n{exc}",
              file=sys.stderr)
        print("  Reporting buckets from a short prediction set would invent "
              "'observed-only' rows that are really our own blind spot.",
              file=sys.stderr)
        print("  Fix: add the message's shape to _SEL_SHAPES in _debugcli.py, or "
              "populate Finding.sel at the site that emits it.", file=sys.stderr)
        return 4
    r = compare_ops(observed, predicted)

    print(f"x4debug crosscheck  {mod.name}  (tier {args.tier})")
    print(f"  engine skipped   : {r.observed_total} op(s), {len(set(observed))} distinct")
    print(f"  we predicted     : {r.predicted_total} op(s), {len(set(predicted))} distinct")
    print()
    print(f"  agreed           : {len(r.both)}")
    print(f"  predicted only   : {len(r.predicted_only)}  "
          "(we flag it, the engine did not — a stale prediction, or an op that "
          "never ran)")
    for sel in r.predicted_only[:10]:
        print(f"      {sel}")
    if len(r.predicted_only) > 10:
        print(f"      ... and {len(r.predicted_only) - 10} more")
    print(f"  OBSERVED ONLY    : {len(r.observed_only)}  "
          "(the engine skipped it and we never predicted it — a VALIDATOR BLIND SPOT)")
    for sel in r.observed_only[:20]:
        print(f"      {sel}")
    if len(r.observed_only) > 20:
        print(f"      ... and {len(r.observed_only) - 20} more")
    print()
    if report.degraded:
        print("  [!] the validator run was DEGRADED — a whole check did not execute, "
              "so 'predicted' is not a complete prediction:")
        for s in report.degraded:
            print(f"      {s.what}: {s.why}")
    print("  Totals are context only; the three buckets above are the finding.")
    # NOTHING COMPARED IS NOT AGREEMENT. With both sides empty the three buckets
    # are empty by construction, and this printed "agreed: 0 / predicted only: 0 /
    # OBSERVED ONLY: 0" and returned 0 — which reads exactly like a mod the engine
    # and the validator concur on. It is also what a log from a session where this
    # mod never loaded looks like, and what a log recorded before the mod was
    # installed looks like, and the counts cannot tell those apart.
    # `gates/oracle_reverse.py` draws the same line for the same reason; rc 2 is
    # this toolkit's NON-ANSWER.
    if not r.observed_total and not r.predicted_total:
        print("\nREFUSING: this log records no skipped op for " + mod.name
              + " and we predicted\n  none, so nothing was compared. That is not "
              "agreement with the engine — it\n  is also what a log from a session "
              "where " + mod.name + " was never loaded\n  looks like. Confirm the log "
              "covers a launch with this mod enabled\n  (`x4debug triage` names what "
              "it does contain), then re-run.", file=sys.stderr)
        return 2

    if r.clean and report.degraded:
        # The prose above already said "not a complete prediction" and the exit code
        # then said "we agree". A check that did not execute cannot have contributed
        # the predictions that would have populated OBSERVED ONLY — the one bucket
        # this command exists to fill.
        print("  DEGRADED: the buckets above agree, but over an INCOMPLETE prediction "
              "set,\n  so the agreement is not evidence.", file=sys.stderr)
        return 3
    return 0 if r.clean else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
