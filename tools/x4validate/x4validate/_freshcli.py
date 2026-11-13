"""x4fresh -- is every derived artifact current, and rebuild the ones that are not.

Four artifacts answer questions about the live modlist, and each one describes the world AS OF ITS BUILD:

    effective   the effective store (x4effective)          rebuild: x4effective build
    xref        the md/aiscript cross-index (x4xref)       rebuild: x4xref build
    x4raw       BaseX, every file as written               rebuild: tools/basex/build-corpus.sh
    x4eff       BaseX, the merged effective tree           rebuild: tools/basex/build-effective.sh

`status` asks each artifact's OWN freshness check (the same code its readers use) and prints one verdict
per artifact: FRESH, STALE (with the reason), UNKNOWN (freshness cannot be established -- never read as
fresh), MISSING, REBUILDING (another x4fresh holds the build lock), or NOT-BUILT (a BaseX database that was
never built, on a machine with no BaseX or Java to build it: optional, so it is not counted as not current).
It also lists what the last build could NOT include (the store's dropped overlays, skipped files, truncated
property subtrees) and orphan temp files a killed build left behind. Exit: 0 all FRESH and complete, 3 all
FRESH but some known INCOMPLETE, 5 otherwise; a NOT-BUILT artifact counts only when named with --only.

`rebuild` runs the builders ONE AT A TIME (two concurrent builds once cost x4eff 17% of its documents),
under an OS lock that the operating system releases if the holder dies, so a crashed build never leaves a
stale lock (MEASURED 2026-11-01: 3 of 3 hard kills released it at once). Every builder's WARNING lines are
carried to the final summary: a build that dropped input ends "BUILT WITH N DROPS", never "fresh". Lines the job
wrapper prints itself (they start "<its program>: ") stay in the log and are counted, but they are not the build's
losses. Any
rebuild defers while a mutation gate is editing the engine or engine sources are uncommitted. Exit: 0 every
requested artifact is FRESH; 1 a builder failed; 5 nothing failed but one is still not FRESH (deferred, lock
held, a gate started meanwhile, the world moved during its build, or NOT-BUILT and named with --only).

`rebuild --auto` is what a session start launches in the background. It rebuilds the effective store and the
xref index itself. It defers while a listed process runs (X4_FRESH_DEFER_WHILE, default X4.exe;X4), and it does
not retry a build that already failed against the same inputs (reasons and the world's digest). It exits 0
unless a builder failed: the note carries the rest, not the exit code. The note reads "did not complete" from
the start of the run until its outcome replaces it, and it is empty only when everything this machine can build
is FRESH and complete (a NOT-BUILT artifact is named in one note).

The BaseX databases (x4raw, then x4eff) take about half an hour and hold a second copy until the swap
(tools/basex/dbswap.py), so --auto never builds them itself: it starts ONE detached background builder at
below-normal priority and returns. It does so only when all of these hold, else the note says which did not:
the game is not running; the installed world (the freshness digest) has been unchanged for
X4_FRESH_BASEX_QUIET_MIN minutes (default 30; measured from the first time an --auto run saw this digest, and a
builder started earlier waits out the rest); the disk fits the second copy (dbswap's own check, read-only); no
build of that database is running; BaseX and Java are here; the database was built here before (a database
never built is never started automatically: build it once by hand); the same inputs did not already fail; and,
for x4eff, the effective store is FRESH. The builder takes the build lock, so `status` reads REBUILDING, and its
outcome replaces the session note of the workspace that started it; `status` also prints the last automatic BaseX
rebuild's state. A game that starts DURING a database's build does not stop it: the builder repeats the game check
every 30 s and, from the first sighting, keeps that build's whole process tree (bash, java, anything started later)
at IDLE priority, and once the game is seen closed returns each process it lowered to its earlier priority (never
above below-normal; a process started during the game inherits idle and is restored with its parent; on Linux every
thread). `status` and the note show what the processes took, read back: "idle since" only when every one did,
otherwise how many did not and why (a job wrapper's job object can refuse it), and "restore ... incomplete" when one
stays at idle. On Linux an ordinary user cannot raise a nice value back (RLIMIT_NICE 0), so there the build stays at
idle until it ends and the restore reads "incomplete ... permission denied". Without a process start time (macOS and
other non-Linux POSIX) no process can be confirmed as the build's, so nothing is lowered: "idle requested ..., not
taken ... identity unconfirmed". x4eff still waits for a closed game. It holds the build lock for BOTH databases (about half an hour): builders never run two at a time,
and x4eff must read an effective store nobody is replacing. Meanwhile a session start rebuilds nothing (its note
says a rebuild is running) and `rebuild` by hand exits 5, "already running".

Optional environment:
    X4_JOB_WRAPPER        JSON list prefixed to every builder command; "{name}" becomes x4fresh-<artifact>
                          (e.g. a resource governor). Unset: builders run directly. Its own lines are told apart
                          by "<program>: " at the start of a line, for its first word and any script it names.
    X4_FRESH_DEFER_WHILE  ';'-separated process names that defer --auto (default: X4.exe)
    X4_FRESH_AUTO=off     --auto does nothing
    X4_FRESH_BASEX_AUTO=off   --auto starts no new background BaseX builder, and a builder re-reads it once, when it
                          takes its own lock; one already waiting out its quiet period keeps waiting and is stopped
                          by the pid `x4fresh status` shows
    X4_FRESH_BASEX_QUIET_MIN  minutes the mods must be unchanged before it does (default 30)
    X4_BASH              bash used for the BaseX builders (default: bash on PATH)
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from x4validate import __version__, _effective, _freshness, _merge, _mutation, _paths, _registry, _xref

PKG_ROOT = Path(__file__).resolve().parent.parent          # tools/x4validate
TOOLKIT_ROOT = PKG_ROOT.parent.parent                       # repo root
BASEX_ROOT = PKG_ROOT.parent / "basex"                      # tools/basex
ORDER = ("effective", "xref", "x4raw", "x4eff")
AUTO_SET = ("effective", "xref")
BASEX = ("x4raw", "x4eff")                 # order matters: x4eff is built after x4raw, and after the effective store
BASEX_AUTO_LOCK = "basex-auto.lock"        # held for the whole life of the ONE background BaseX builder
BASEX_QUIET_MIN = 30
BASEX_MAX_WAIT = 6 * 3600                  # a builder whose world never goes quiet gives up after this
BASEX_LOCK_WAIT = 15 * 60                  # how long the builder waits for x4fresh.lock (another session's build)
BASEX_ALOCK_TRIES = 10                     # x 0.5 s: outlasts a probe of basex-auto.lock, never a real holder
NOT_BUILT = "NOT-BUILT"
STATES = ("FRESH", "STALE", "UNKNOWN", "MISSING", "REBUILDING", NOT_BUILT)
# Holds the session note from the start of an --auto run until its outcome replaces it, so a run that dies
# cannot leave the previous run's empty note to read as "everything is fine". scripts/x4fresh_session.py
# spells the same prefix (it is stdlib-only and cannot import this).
NOTE_PENDING = ("x4fresh: NOT CHECKED: the session-start check started but has not reported: it is still "
                "running, or it died. Run `x4fresh status` from tools/x4validate.\n")
# `skipped N unparseable file(s)` is the effective build's whole-file loss line and carries none of the
# other words (review 2026-11-01, finding 1): without it a build that lost files read "REBUILT, FRESH".
WARN_RE = re.compile(r"\bWARNING\b|\bNOT INDEXED\b|\bINCOMPLETE\b|\bERROR\b|\bskipped \d+ unparseable\b")
#: A job wrapper's own line that explains a NON-ZERO exit: a kill, a refusal, a watchdog, a cap that was hit, or the
#: exit code it reports. Generic words, no wrapper's codes; `exit 0` and a bare `(cap 25.0)` budget remark do not match.
WRAPPER_EXIT_RE = re.compile(r"\bKILLED\b|\bkilled\b|\brefused\b|\bREFUSED\b|\bwatchdog\b|\btimed out\b"
                             r"|\bcap\b[^\n]*\bhit\b|\bexit(?:ed)?(?: with)?(?: code)? [1-9]\d*\b", re.I)
MAX_LISTED = 5   # per list in a report; the hook output that carries it is bounded
# A line that only SUMMARISES the losses the store lists by identity. `accepted` compares those identities,
# so these say nothing new once the sets match; any OTHER warning is outside what the user accepted.
LOSS_SUMMARY_RE = re.compile(r"\bWARNING: \d+ overlay\(s\) could not be read\b"
                             r"|\bskipped \d+ unparseable\b"
                             r"|\bWARNING: \d+ property subtree\(s\) hit\b"
                             r"|\bNOT INDEXED\b.*\d+ file\(s\) would not parse\b")
MOVED_STAMP = "moved-during-build"   # never equals a digest, so a reader compares it and reads STALE


@dataclass
class Status:
    name: str
    state: str
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # what the artifact does NOT include
    orphans: list[Path] = field(default_factory=list)   # temp files a killed build left behind
    incomplete: int = 0                                 # items the artifact is known to be missing
    loss_keys: list[str] | None = None                  # WHICH items, by identity (None: cannot say)
    accepted: str | None = None                         # when the user accepted exactly this loss set
    # Reasons that describe a passing moment, not the artifact's inputs (dbswap leftovers with their pids, a running
    # rebuild). Shown like any reason, but never part of the failure memo's key: a failed build LEAVES some of them,
    # and they made the next key differ, so the same failure ran once more (4.1.1 review).
    volatile: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- where things live

def state_dir() -> Path | None:
    """Lock, logs, the failure memo and the session note live beside the registry they describe."""
    reg = _registry.DEFAULT_REGISTRY
    return Path(reg).parent / "x4fresh" if reg else None


def _orphans(target: Path, *names: str) -> list[Path]:
    """`<name>.<pid>.tmp` files beside `target`: what a build killed before its os.replace leaves."""
    out: list[Path] = []
    for n in names or (target.name,):
        out.extend(sorted(target.parent.glob(f"{n}.*.tmp")))
    return out


# --------------------------------------------------------------------------- the build lock

class BuildLock:
    """An OS byte-range lock. The OS releases it when the holding process dies, however it dies, so
    there is no stale-lock cleanup and no guessing whether a pid is alive (unsafe on Windows)."""

    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, 0)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None

    def held_elsewhere(self) -> bool:
        if self.acquire():
            self.release()
            return False
        return True


# --------------------------------------------------------------------------- per-artifact status

def _status_effective() -> Status:
    db = _effective.effective_db()
    if db is None:
        return Status("effective", "UNKNOWN", ["the effective-store location is not configured "
                                               "(set X4_EFFECTIVE_DB or X4_MODS)"])
    orphans = _orphans(db)
    if not db.is_file():
        return Status("effective", "MISSING", [f"no store at {db}"], orphans=orphans)
    try:
        con = sqlite3.connect(_effective._sqlite_ro_uri(db.resolve()), uri=True)
        try:
            verdict = _effective.store_freshness(con)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()
    except sqlite3.Error as exc:
        return Status("effective", "UNKNOWN", [f"store unreadable: {exc}"], orphans=orphans)
    notes: list[str] = []
    lost = 0
    for count_key, sample_key, what in (
            ("dropped_overlays", "dropped_overlay_samples",
             "overlay(s) could not be read and are NOT in the store; values they would set read as base"),
            ("skipped_files", "skipped_file_samples",
             "file(s) could not be merged at all and contribute NO rows"),
            ("truncated_props", "truncated_prop_samples",
             "property subtree(s) were deeper than the build's depth guard and are NOT indexed")):
        raw = meta.get(count_key)
        try:
            n = int(raw or 0)
        except ValueError:
            notes.append(f"unreadable {count_key} in the store's metadata ({raw!r}): completeness unknown")
            lost += 1
            continue
        if not n:
            continue
        lost += n
        try:
            samples = json.loads(meta.get(sample_key) or "[]")
        except ValueError:
            samples = []
        notes.append(f"{n} {what}" + (f" (first {min(len(samples), MAX_LISTED)}):" if samples else ""))
        notes.extend("  - " + str(s).split(":", 1)[0] for s in samples[:MAX_LISTED])
    try:
        keys = json.loads(meta["loss_keys"]) if "loss_keys" in meta else None
    except ValueError:
        keys = None
    return Status("effective", _classify(verdict), verdict.reasons, notes, orphans, incomplete=lost,
                  loss_keys=keys if lost else [])


def _status_xref() -> Status:
    try:
        tsv = _xref._default_tsv()
    except (SystemExit, ValueError, RuntimeError) as exc:
        return Status("xref", "UNKNOWN", [f"the xref index location is not configured ({exc})"])
    orphans = _orphans(tsv, tsv.name, _xref._sidecar(tsv).name, tsv.name + ".freshness.json")
    if not tsv.is_file():
        return Status("xref", "MISSING", [f"no index at {tsv}"], orphans=orphans)
    try:
        rows, signed, excl, verdict = _xref.check_index(tsv)
    except _xref.INDEX_UNREADABLE as exc:
        return Status("xref", "UNKNOWN", [f"index unreadable: {exc}"], orphans=orphans)
    reasons = list(verdict.reasons)
    if not (signed and excl):
        reasons.append("index integrity unavailable (unsigned): absence cannot be certified")
    state = _classify(verdict) if verdict.fresh is False else ("FRESH" if signed and excl else "UNKNOWN")
    # What the index does not hold -- the same two facts its own queries print (review finding 2).
    notes: list[str] = []
    lost = 0
    try:
        excluded = [ln for ln in _xref._sidecar(tsv).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        excluded = []
    if excluded:
        lost += len(excluded)
        notes.append(f"{len(excluded)} file(s) would not parse and are NOT INDEXED (first "
                     f"{min(len(excluded), MAX_LISTED)}):")
        notes.extend("  - " + e for e in excluded[:MAX_LISTED])
    gap = _xref.coverage_incomplete(rows)
    if gap:
        lost += 1
        notes.append("the index holds fewer DLC than are installed: its queries answer DEGRADED (exit 3)")
    keys = sorted(f"not-indexed {e}" for e in excluded) + (["dlc-coverage-gap"] if gap else [])
    return Status("xref", state, reasons, notes, orphans, incomplete=lost, loss_keys=keys)


def _load_staleness():
    spec = importlib.util.spec_from_file_location("staleness", BASEX_ROOT / "staleness.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["staleness"] = mod  # its dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def _basex_unbuildable() -> str | None:
    """Why this machine cannot build a BaseX database, in one line, or None (it can, or we cannot tell: a
    preflight that fails to run is a non-answer, and "cannot be built" must not be a guess). Uses
    tools/basex/preflight.py, the check the builders themselves refuse on."""
    try:
        spec = importlib.util.spec_from_file_location("x4fresh_basex_preflight", BASEX_ROOT / "preflight.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod    # its dataclasses and annotations resolve through sys.modules
        spec.loader.exec_module(mod)
        problems = mod.check(["java", "jar"], BASEX_ROOT / "basex")
    except Exception:  # noqa: BLE001  # silent-ok: None keeps the MISSING verdict, whose advice names the builder
        return None
    return problems[0].splitlines()[0] if problems else None


def _database_on_disk(name: str) -> bool:
    """A BaseX database exists as files under basex/data/<db>. Whatever the coverage report says about it."""
    try:
        return any((BASEX_ROOT / "basex" / "data" / name).iterdir())
    except OSError:
        return False  # silent-ok: nothing there to find; the caller's "never built" needs no database


def _not_built(name: str, why: str) -> Status:
    return Status(name, NOT_BUILT, [f"never built here, and this machine cannot build it: {why} "
                                    "Optional: only the BaseX queries need it (setup: tools/basex/README.md)."])


def _swap_notes(name: str) -> list[str]:
    """What tools/basex/dbswap.py says about generations beside `name` (build-then-swap, 4.1.1): an interrupted
    swap, an unfinished or unswapped <db>__next, a stray <db>__old. Read-only; never runs BaseX. A check that
    cannot run says so in one line rather than hiding the rest of the verdict."""
    path = BASEX_ROOT / "dbswap.py"
    if not path.is_file():
        return []
    try:
        spec = importlib.util.spec_from_file_location("x4fresh_dbswap", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        home = BASEX_ROOT / "basex"
        return mod.describe(mod.BaseXRunner(home), home, name)
    except Exception as exc:  # noqa: BLE001 -- said, not swallowed: the verdict above it still stands
        return [f"the build-then-swap state could not be read ({type(exc).__name__}: {exc})"]


def _status_basex(name: str) -> Status:
    s = _status_basex_alone(name)
    # Every state (4.1.1 review B3): a FRESH database beside an unjournaled <db>__old or a finished but unswapped
    # <db>__next (~1 GB) was never mentioned. Beside a verdict that is not about them they are notes, so the
    # FRESH or NOT BUILT verdict and its reasons (the rebuild memo's key) stay as they are.
    notes = _swap_notes(name)
    (s.notes if s.state in ("FRESH", NOT_BUILT) else s.reasons).extend(notes)
    s.volatile.extend(notes)
    return s


def _status_basex_alone(name: str) -> Status:
    cov = BASEX_ROOT / "basex" / f"coverage-{name}.json"
    if not (BASEX_ROOT / "staleness.py").is_file():
        return (Status(name, "UNKNOWN", [f"no BaseX freshness check at {BASEX_ROOT}, but a coverage report exists"])
                if cov.is_file() else _not_built(name, f"no BaseX tooling at {BASEX_ROOT}."))
    if not cov.is_file():
        why = _basex_unbuildable()
        if why and _database_on_disk(name):
            return Status(name, "MISSING", [f"a database is on disk ({BASEX_ROOT / 'basex' / 'data' / name}) with no coverage "
                                            f"report, so it cannot be judged, and this machine cannot rebuild it: {why}"])
        if why:
            return _not_built(name, why)
        return Status(name, "MISSING", [f"no coverage report at {cov}: never built, or the last build "
                                        "did not finish"])
    try:
        st = _load_staleness()
        reference, extensions, engine = st._defaults()
        verdict = st.check(cov, reference, extensions, engine, name)
    except Exception as exc:  # noqa: BLE001 -- ask.py treats every failure here as UNKNOWN, so do we
        return Status(name, "UNKNOWN", [f"freshness could not be checked ({type(exc).__name__}: {exc})"])
    if verdict.fresh:
        return Status(name, "FRESH")
    return Status(name, "STALE" if verdict.determinable else "UNKNOWN", list(verdict.reasons))


def _classify(verdict) -> str:
    """_freshness.Verdict has no 'determinable' flag; its UNKNOWN reasons say so in words."""
    if verdict.fresh:
        return "FRESH"
    text = " ".join(verdict.reasons)
    return "UNKNOWN" if ("cannot be established" in text or "UNKNOWN" in text) else "STALE"


CHECKS = {"effective": _status_effective, "xref": _status_xref,
          "x4raw": lambda: _status_basex("x4raw"), "x4eff": lambda: _status_basex("x4eff")}


def check(name: str) -> Status:
    """One artifact's verdict. ANY failure of the check itself is UNKNOWN, never a traceback that skips
    the verdict, the failure memo and the session note (review 2026-11-01, finding 5)."""
    try:
        return CHECKS[name]()
    except Exception as exc:  # noqa: BLE001 -- a check that cannot run is a non-answer, said as one
        return Status(name, "UNKNOWN", [f"freshness check failed ({type(exc).__name__}: {exc})"])


def _accepted_path(sd: Path) -> Path:
    return sd / "accepted-losses.json"


def _load_accepted(sd: Path | None) -> dict:
    if sd is None:
        return {}
    try:
        data = json.loads(_accepted_path(sd).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}  # silent-ok: no/unreadable accept list = nothing accepted, so every loss is reported (fails loud)


def _mark_accepted(s: Status, accepted: dict) -> Status:
    """A loss set IDENTICAL to the accepted one is known and accepted; any difference -- a file added OR
    removed -- is news. An artifact that cannot list its losses (an older store) is never accepted."""
    entry = accepted.get(s.name)
    entry = entry if isinstance(entry, dict) else {}
    keys = entry.get("keys")
    if s.incomplete and s.loss_keys is not None and sorted(s.loss_keys) == keys:
        s.accepted = entry.get("when", "?")
    elif s.incomplete and s.loss_keys is not None and isinstance(keys, list):
        # Said in the artifact's own lines, or "BUILT WITH N DROPS" after an accept reads as if the accept was lost.
        new = sorted(set(s.loss_keys) - set(keys))
        s.notes.append(f"differs from the {len(keys)} loss(es) accepted {entry.get('when', '?')}: {len(new)} new, "
                       f"{len(set(keys) - set(s.loss_keys))} no longer lost"
                       + (f"; first new: {new[0]}" if new else "")
                       + ("; property subtrees are listed by name only since 4.1.0, so a list accepted before that "
                          "cannot hold them" if new and all(k.startswith("truncated ") for k in new) else "")
                       + "; review, then `x4fresh accept`")
    return s


def _unaccepted(results: list[Status]) -> list[Status]:
    return [s for s in results if s.incomplete and not s.accepted]


def _rebuilding_targets(sd: Path) -> set[str] | None:
    """What the lock holder said it would rebuild, or None (unknown: treat every artifact as in play)."""
    try:
        data = json.loads((sd / "rebuilding.json").read_text(encoding="utf-8"))
        return {n for n in data.get("targets", []) if n in ORDER}
    except (OSError, ValueError, AttributeError, TypeError):
        return None  # silent-ok: None = unknown targets; the caller then reports EVERY artifact as REBUILDING


def status(only=ORDER, holding_lock: bool = False) -> list[Status]:
    """`holding_lock`: the caller IS the rebuild. The lock then reads as held from here too (an OS lock
    refuses a second handle even in the same process), and must not be reported as someone else's.

    While another process rebuilds, only the artifacts IT is rebuilding read REBUILDING; the rest keep
    their real state (an --auto rebuild never touches BaseX, so a stale x4eff must still say STALE).
    Probing the lock takes it for an instant: a rebuild starting in that instant sees "already running"
    and starts nothing (exit 5 by hand; --auto exits 0 and the next session start retries it)."""
    sd = state_dir()
    locked = not holding_lock and bool(sd) and BuildLock(sd / "x4fresh.lock").held_elsewhere()
    in_play = _rebuilding_targets(sd) if locked else set()
    accepted = _load_accepted(sd)
    out = []
    for name in only:
        s = _mark_accepted(check(name), accepted)
        if locked and s.state != "FRESH" and (in_play is None or name in in_play):
            s.reasons.insert(0, f"a rebuild is running (lock held: {sd / 'x4fresh.lock'})")
            s.volatile.append(s.reasons[0])
            s.state = "REBUILDING"
        out.append(s)
    return out


def render(results: list[Status]) -> str:
    """Directive first (a long report is truncated from the tail), inventory after."""
    bad = [s for s in results if s.state not in ("FRESH", NOT_BUILT)]
    unbuilt = [s.name for s in results if s.state == NOT_BUILT]
    built = len(results) - len(unbuilt)
    also = f"; not built on this machine: {', '.join(unbuilt)}" if unbuilt else ""
    lines = []
    if bad:
        lines.append("x4fresh: NOT CURRENT: " + ", ".join(f"{s.name} {s.state}" for s in bad)
                     + " -- run `x4fresh rebuild`" if any(s.state != "REBUILDING" for s in bad)
                     else "x4fresh: rebuild in progress: " + ", ".join(s.name for s in bad))
    elif _unaccepted(results):
        # Fresh is about WHEN, not HOW MUCH: a current store that dropped input is not "clean".
        lines.append(f"x4fresh: all {built} artifact(s) FRESH, but INCOMPLETE: "
                     + ", ".join(f"{s.name} ({s.incomplete} missing)" for s in _unaccepted(results))
                     + " -- not a build failure: input the build could not include, and nobody has accepted that loss "
                       "yet (a first rebuild after an upgrade lands here). Review, then `x4fresh accept` if it is known "
                       "and acceptable; `status` is exit 3 until then" + also)
    elif not built and unbuilt:
        lines.append(f"x4fresh: nothing to report on: not built on this machine: {', '.join(unbuilt)}")
    else:
        known = sum(s.incomplete for s in results if s.accepted)
        lines.append(f"x4fresh: all {built} artifact(s) FRESH"
                     + (f" ({known} known loss(es), accepted)" if known else "") + also)
    for s in results:
        lines.append(f"  {s.name:<10} {s.state}"
                     + (f"  ({s.incomplete} known loss(es) accepted {s.accepted}; listed below)" if s.accepted else ""))
        lines.extend(f"      {r}" for r in s.reasons)
        lines.extend(f"      {n}" for n in s.notes)
        if s.orphans:
            lines.append(f"      {len(s.orphans)} orphan temp file(s) from a build that did not finish "
                         "(safe to delete by hand once no build is running):")
            lines.extend(f"        {p}" for p in s.orphans[:MAX_LISTED])
            if len(s.orphans) > MAX_LISTED:
                lines.append(f"        ... and {len(s.orphans) - MAX_LISTED} more beside it")
    return "\n".join(lines)


# --------------------------------------------------------------------------- rebuild

def _wrapper_prefixes() -> tuple[str, ...]:
    """How the job wrapper's OWN lines start: "<program>: ", for its first word and any script or binary it names. Narrow
    on purpose (a builder's indented "WARNING" never starts this way), since what matches is not read as a loss."""
    try:
        argv = _wrapper_argv()
    except ValueError:
        return ()  # silent-ok: the builder's start reports this same error; here it means nothing is set aside
    out: list[str] = []
    for i, arg in enumerate(argv):
        path = Path(arg)
        if i == 0 or path.suffix.lower() in (".py", ".exe", ".cmd", ".bat", ".sh"):
            out.append(path.stem + ": ")
    return tuple(out)


def _wrapper_argv() -> list[str]:
    # env-ok: a per-machine runtime switch (job wrapper argv), not a configured location
    raw = os.environ.get("X4_JOB_WRAPPER", "").strip()
    if not raw:
        return []
    argv = json.loads(raw)
    if not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
        raise ValueError("X4_JOB_WRAPPER must be a JSON list of strings")
    return argv


def _wrapper(name: str) -> list[str]:
    return [a.replace("{name}", f"x4fresh-{name}") for a in _wrapper_argv()]


def builder(name: str) -> list[str]:
    if name in ("effective", "xref"):
        mod, fn = {"effective": ("x4validate._effectivecli", "main"), "xref": ("x4validate._xref", "main")}[name]
        return [sys.executable, "-c", f"import sys; from {mod} import {fn}; sys.exit({fn}(['build']))"]
    # env-ok: an interpreter override for the builders, not a configured data location
    bash = os.environ.get("X4_BASH") or shutil.which("bash")
    if bash and "system32" in bash.lower():   # WSL's bash: a different filesystem view; never the builder's
        bash = None
    git = shutil.which("git")
    if not bash and os.name == "nt" and git:
        # Git for Windows: <Git>/cmd/git.exe (or <Git>/bin/git.exe) beside <Git>/bin/bash.exe. Derived from
        # the git on PATH, never a hardcoded install location.
        cand = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if cand.is_file():
            bash = str(cand)
    if not bash:
        raise FileNotFoundError("no bash for the BaseX builders (set X4_BASH)")
    script = {"x4raw": "build-corpus.sh", "x4eff": "build-effective.sh"}[name]
    return [bash, str(BASEX_ROOT / script)]


class _ChildTree:
    """Tie the builder's process tree to THIS process's life (review 2026-11-01, finding 6).

    The build lock is released by the OS when x4fresh dies -- but a builder it started would keep
    running, and the next --auto would start a second concurrent build (the 17% x4eff loss the lock
    exists to prevent). Windows: a job object with KILL_ON_JOB_CLOSE; the OS closes our handle when we
    die, which kills the tree. Linux: PR_SET_PDEATHSIG. Elsewhere: unavailable, said in the log."""

    def __init__(self):
        self.job = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)

            class _Basic(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                            ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

            class _Ext(ctypes.Structure):
                _fields_ = [("Basic", _Basic), ("Io", ctypes.c_uint64 * 6), ("p", ctypes.c_size_t),
                            ("j", ctypes.c_size_t), ("pp", ctypes.c_size_t), ("pj", ctypes.c_size_t)]
            info = _Ext()
            info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if job and k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info),
                                                   ctypes.sizeof(info)):
                self.job, self.k32, self.wintypes = job, k32, wintypes

    def popen_kwargs(self) -> dict:
        if os.name == "nt" or not sys.platform.startswith("linux"):
            return {}

        def _die_with_parent():
            import ctypes
            import signal
            ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
        return {"preexec_fn": _die_with_parent}

    def adopt(self, proc) -> bool:
        if not self.job:
            return sys.platform.startswith("linux")
        return bool(self.k32.AssignProcessToJobObject(self.wintypes.HANDLE(self.job),
                                                      self.wintypes.HANDLE(int(proc._handle))))


def _run(name: str, log: Path, argv: list[str] | None = None,
         watch: _GameWatch | None = None) -> tuple[int, list[str], list[str]]:
    """(exit code, the builder's warning lines, the job wrapper's own warning lines). Every line is in the log.
    `argv`: run this instead of the artifact's builder (the swap-only retry), under the same wrapper.
    `watch`: watch for a game while it runs, and keep the builder's process tree at idle priority while one runs."""
    argv = _wrapper(name) + (argv if argv is not None else builder(name))
    warnings: list[str] = []
    wrappers: list[str] = []
    own = _wrapper_prefixes()
    log.parent.mkdir(parents=True, exist_ok=True)
    tree = _ChildTree()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("$ " + " ".join(argv) + "\n")
        fh.flush()
        proc = subprocess.Popen(argv, cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                **tree.popen_kwargs())
        adopted = tree.adopt(proc)
        if not adopted:
            fh.write("x4fresh: NOTE: the builder is not tied to x4fresh's life on this platform; if x4fresh "
                     "is killed, the build lock frees while the builder may still run\n")
        try:
            if watch is not None:
                # In the try: whatever happens, the build is still read to its end, waited for, and the watch stopped.
                watch.start(proc, job=tree.job if adopted else None)
            for line in proc.stdout:
                fh.write(line)
                mine = bool(own) and line.startswith(own)
                # A wrapper's KILL line carries no WARNING word (rb: "KILLED by rb: memory cap 3.0 GB hit (allocation
                # refused) -> exit 86"), and it is the line that says why the builder stopped (4.1.1).
                if WARN_RE.search(line) or (mine and WRAPPER_EXIT_RE.search(line)):
                    (wrappers if mine else warnings).append(line.rstrip())
            rc = proc.wait()
        finally:
            if watch is not None:
                watch.stop()
    return rc, warnings, wrappers


WHY_MAX = 300   # characters of a wrapper line quoted on the FAILED line


def _exit_reason(wrapper_lines: list[str], rc: int) -> str | None:
    """The wrapper line that explains exit `rc`: the LAST one naming `exit <rc>`, else the last explaining line. An
    earlier "refused" can be an admission the wrapper then waited out; the kill comes last (4.1.1 review)."""
    explains = [w for w in wrapper_lines if WRAPPER_EXIT_RE.search(w)]
    names_rc = [w for w in explains if re.search(r"\bexit(?:ed)?(?: with)?(?: code)? {}\b".format(rc), w, re.I)]
    return (names_rc or explains or [None])[-1]


def _explaining_first(wrapper_lines: list[str], rc: int = 0) -> list[str]:
    """The job wrapper's lines: the one that explains exit `rc` first, then the others in printed order."""
    why = _exit_reason(wrapper_lines, rc)
    return ([why] if why else []) + [w for w in wrapper_lines if w is not why]


def _deferred(auto: bool) -> str | None:
    """Why an unattended rebuild must not start now, or None. Every check fails SAFE (defers)."""
    if _mutation.active() is not None:
        return "a mutation gate is editing the engine sources (marker present)"
    dirty = _dirty_engine_sources()
    if dirty:
        return "engine sources have uncommitted changes: " + ", ".join(dirty)
    if not auto:
        return None
    busy = _running_deferring_processes()
    if busy is None:
        return "could not list running processes, so a game may be running"
    if busy:
        return "deferring while running: " + ", ".join(busy)
    return None


def _dirty_engine_sources() -> list[str]:
    rels = [str(Path("tools/x4validate/x4validate") / Path(p).name) for p in _freshness.ENGINE_SOURCES]
    if not (TOOLKIT_ROOT / ".git").exists():
        return []  # an installed toolkit, not a checkout: nothing to be dirty against
    # A CHECKOUT whose git cannot answer (not on PATH, "dubious ownership", a timeout) is NOT clean:
    # it defers (review 2026-11-01, finding 4 -- this returned "clean" on every git failure).
    try:
        out = subprocess.run(["git", "-C", str(TOOLKIT_ROOT), "status", "--porcelain", "--", *rels],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"<git could not check: {type(exc).__name__}>"]
    if out.returncode != 0:
        return [f"<git could not check: rc {out.returncode}>"]
    return [ln[3:].strip() for ln in out.stdout.splitlines() if ln.strip()]


def _running_deferring_processes() -> list[str] | None:
    # X4.exe on Windows, X4 for the native Linux build (review finding 9).
    # env-ok: a per-machine runtime switch (process names to defer for), not a configured location
    want = [p.strip().lower() for p in os.environ.get("X4_FRESH_DEFER_WHILE", "X4.exe;X4").split(";") if p.strip()]
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20)
            names = {ln.split('","')[0].strip('"').lower() for ln in out.stdout.splitlines() if ln.strip()}
        else:
            out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=20)
            names = {Path(ln.strip()).name.lower() for ln in out.stdout.splitlines() if ln.strip()}
    except (OSError, subprocess.SubprocessError):
        return None  # silent-ok: None = could not list processes; the caller DEFERS and says so in the note
    if out.returncode != 0 or not names:
        return None
    return [w for w in want if w in names]


# --------------------------------------------------------------------------- a game that starts mid-build (4.1.1)
# The background BaseX builder checks the game before each database, but x4raw alone takes about a quarter of an
# hour. While it builds, _GameWatch asks the same question every GAME_POLL_SECONDS (one process listing: cheap) and,
# from the first time a game is seen, keeps the build's whole process tree (the job wrapper, bash, java, and anything
# they start later) at IDLE priority. It never stops the build. When the game is seen closed, every process it
# lowered goes back to what it had before, never above below-normal. A listing that cannot answer counts as a game
# (it fails toward idle, never toward faster). What `status` shows is what the processes took, read back, never
# what was asked: a priority-limited job object makes the call succeed and change nothing.
#
# A pid can be reused between the listing and the call, so every call confirms it holds the process it listed:
# Windows, on the opened handle, by membership of the build's own job object or else by the start time recorded at
# listing; POSIX, by the start time in /proc (checked just before the call: best-effort, there is no handle). A
# process it cannot confirm is never touched.

IDLE, BELOW_NORMAL = "idle", "below-normal"
GAME_POLL_SECONDS = 30.0
EVENTS_MAX = 10    # priority changes kept for the note; the rest are counted
_WIN_CLASS = {IDLE: 0x00000040, BELOW_NORMAL: 0x00004000}      # IDLE_PRIORITY_CLASS, BELOW_NORMAL_PRIORITY_CLASS
# Windows priority classes from lowest to highest: idle, below-normal, normal, above-normal, high, realtime.
_WIN_RANK = {0x40: 0, 0x4000: 1, 0x20: 2, 0x8000: 3, 0x80: 4, 0x100: 5}
_NICE = {IDLE: 19, BELOW_NORMAL: 10}
_GONE = "gone"            # _set_priority: the process exited meanwhile, which is no problem
_NOT_OURS = "not ours"    # _set_priority: the pid now names another process (reused), which is never touched


def _raw(level) -> int:
    """A level's raw value on this platform (a Windows priority class, a POSIX nice); a raw int passes through."""
    if isinstance(level, int):
        return level
    return _WIN_CLASS[level] if os.name == "nt" else _NICE[level]


def _rank(raw: int) -> int:
    """Lower means lower priority. Windows: the class order (an unknown class ranks as normal); POSIX: -nice."""
    return _WIN_RANK.get(raw, 2) if os.name == "nt" else -raw


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, wintypes, k32


def _process_table() -> dict[int, int] | None:
    """pid -> parent pid for every process, or None when the listing cannot be had."""
    if os.name == "nt":
        ctypes, wintypes, k32 = _kernel32()

        class _Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD),
                        ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                        ("ppid", wintypes.DWORD), ("base", ctypes.c_long), ("flags", wintypes.DWORD),
                        ("exe", ctypes.c_wchar * 260)]
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)      # TH32CS_SNAPPROCESS
        if not snap or snap == wintypes.HANDLE(-1).value:
            return None
        try:
            entry = _Entry()
            entry.dwSize = ctypes.sizeof(_Entry)
            table: dict[int, int] = {}
            ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(entry))
            while ok:
                table[int(entry.pid)] = int(entry.ppid)
                ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(entry))
            return table or None
        finally:
            k32.CloseHandle(snap)
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None  # silent-ok: None = could not list; the caller says so in the note
    table = {}
    for ln in out.stdout.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = int(parts[1])
    return table if out.returncode == 0 and table else None


def _handle_birth(k32, wintypes, ctypes, h) -> int | None:
    """Windows: the creation time (FILETIME ticks) of the process an open handle holds, None when unreadable."""
    times = [ctypes.c_uint64() for _ in range(4)]
    if not k32.GetProcessTimes(wintypes.HANDLE(h), *[ctypes.byref(t) for t in times]):
        return None
    return times[0].value


def _handle_in_job(k32, wintypes, ctypes, h, job) -> bool | None:
    """Windows: whether the process an open handle holds is in `job`; None when that cannot be asked."""
    inside = wintypes.BOOL()
    if not k32.IsProcessInJob(wintypes.HANDLE(h), wintypes.HANDLE(job), ctypes.byref(inside)):
        return None
    return bool(inside.value)


_PROC = Path("/proc")


def _linux() -> bool:
    return sys.platform.startswith("linux")


def _tids(pid: int) -> list[int]:
    """Linux: every thread of the process, since a nice value there belongs to ONE thread (setpriority on a pid sets
    only the thread whose id it is, so java's running threads would keep theirs). [pid] elsewhere, or when the
    threads cannot be listed."""
    if not _linux():
        return [pid]
    try:
        tids = sorted(int(e.name) for e in (_PROC / str(pid) / "task").iterdir() if e.name.isdigit())
    except OSError:
        return [pid]  # silent-ok: unlistable threads: the per-pid call is the fallback, as off Linux
    return tids or [pid]


def _posix_set(pid: int, want: int, threads: dict[int, int | None], own_originals: bool = True) -> str | None:
    """Set nice `want` on every thread of `pid` (the pid itself off Linux). `threads`: tid -> its nice before the
    first touch, kept by the caller across calls; a thread first seen after that was started meanwhile (inheriting a
    lowered nice) and has no original of its own (None: it takes the process's, `want` on a restore). With
    `own_originals` False (a process that itself inherited idle from a lowered parent) no thread has one. Each thread
    gets the LOWER priority of `want` and its own original, so a restore never raises a thread above what it had. A
    thread that vanished is ignored."""
    prio = getattr(os, "PRIO_PROCESS", 0)
    first = not threads and own_originals
    done, err = 0, None
    for tid in _tids(pid):
        if tid not in threads:
            try:
                threads[tid] = os.getpriority(prio, tid) if first else None
            except OSError:
                threads[tid] = None  # silent-ok: unknown original: it gets `want`, the documented fallback
        orig = threads[tid]
        target = want if orig is None or orig <= want else orig      # a higher nice is a lower priority
        try:
            os.setpriority(prio, tid, target)
        except ProcessLookupError:
            continue  # silent-ok: the thread (or the process) ended meanwhile; none left = _GONE below
        except PermissionError:
            # Raising a nice value back down (the restore after a game) needs privilege on Linux: an ordinary user's
            # RLIMIT_NICE is 0. Lowering never does, so a refusal there is something else.
            err = err or ("permission denied" if want >= _NICE[IDLE]
                          else "permission denied (raising a priority back needs privilege here)")
            continue
        except OSError as exc:
            err = err or f"{type(exc).__name__}: {exc}"
            continue
        done += 1
    if err:
        return err
    return None if done else _GONE


def _posix_birth(pid: int) -> int | None:
    """Linux: the process's start time (field 22 of /proc/<pid>/stat, clock ticks after boot); None elsewhere or
    when it cannot be read (the process exited, or there is no /proc)."""
    try:
        stat = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None  # silent-ok: None = unknown; a caller never touches a process it cannot confirm
    fields = stat.rpartition(")")[2].split()     # the command name in parentheses may hold spaces
    return int(fields[19]) if len(fields) > 19 and fields[19].isdigit() else None


def _created(pid: int) -> int | None:
    """The process's start time (Windows FILETIME ticks; Linux /proc ticks), None when it cannot be read. A Windows
    parent pid is never updated when the parent exits, so a pid reused by our build could look like the parent of an
    older, unrelated process; a child is never older than its parent, and _descendants drops one that is. The same
    value, recorded at listing, is how a later call confirms the pid still names that process."""
    if os.name != "nt":
        return _posix_birth(pid)
    ctypes, wintypes, k32 = _kernel32()
    h = k32.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        return _handle_birth(k32, wintypes, ctypes, h)
    finally:
        k32.CloseHandle(h)


def _descendants(root: int) -> list[tuple[int, int | None, int | None]] | None:
    """(pid, start time, parent pid) for `root` and every process below it, root first (its parent None); None when
    they cannot be listed."""
    table = _process_table()
    if table is None:
        return None
    children: dict[int, list[int]] = {}
    for pid, ppid in table.items():
        if pid != ppid:
            children.setdefault(ppid, []).append(pid)
    born: dict[int, int | None] = {root: _created(root)}
    tree, todo, seen = [(root, born[root], None)], [root], {root}
    while todo:
        parent = todo.pop()
        for child in children.get(parent, []):
            if child in seen:
                continue
            born[child] = _created(child)
            if born[parent] is not None and born[child] is not None and born[child] < born[parent]:
                continue        # an older process naming a reused pid as its parent: not ours
            seen.add(child)
            tree.append((child, born[child], parent))
            todo.append(child)
    return tree


def _win_open_error(err: int) -> str:
    return _GONE if err == 87 else ("access denied" if err == 5 else f"Windows error {err}")


def _win_open_ours(pid: int, born: int | None, job, access: int):
    """Windows: (k32 bits, handle, None) for the process `pid` names IF it is the one listed, else (.., None, why)."""
    ctypes, wintypes, k32 = _kernel32()
    h = k32.OpenProcess(access, False, pid)
    if not h:
        return (ctypes, wintypes, k32), None, _win_open_error(ctypes.get_last_error())
    code = wintypes.DWORD()
    why = None
    if k32.GetExitCodeProcess(wintypes.HANDLE(h), ctypes.byref(code)) and code.value != 259:
        why = _GONE                                  # exited; our handle only keeps its record alive
    elif not (job and _handle_in_job(k32, wintypes, ctypes, h, job)):
        # Not provably in the build's job (no job, or a process started before the job took the tree): the start
        # time recorded at listing must match the process this handle holds.
        now = _handle_birth(k32, wintypes, ctypes, h)
        if born is None or now is None:
            why = "identity unconfirmed (its start time could not be read), so it was not touched"
        elif now != born:
            why = _NOT_OURS
    if why:
        k32.CloseHandle(h)
        return (ctypes, wintypes, k32), None, why
    return (ctypes, wintypes, k32), h, None


def _posix_ours(pid: int, born: int | None) -> str | None:
    if born is None:
        return "identity unconfirmed (no process start time on this platform), so it was not touched"
    now = _posix_birth(pid)
    if now is None:
        return _GONE
    return None if now == born else _NOT_OURS


def _set_priority(pid: int, level, born: int | None = None, job=None,
                  threads: dict[int, int | None] | None = None, own_originals: bool = True) -> str | None:
    """Set one process's priority (IDLE, BELOW_NORMAL, or a raw value) if `pid` still names the process listed with
    start time `born` (or, on Windows, one in the build's `job`). None: done, read back; _GONE: it exited meanwhile;
    _NOT_OURS: the pid was reused, nothing touched; else what went wrong, for the note. `threads`: POSIX, the
    per-thread originals _posix_set keeps for this process (Linux sets every thread); `own_originals`: see there."""
    want = _raw(level)
    if os.name == "nt":
        (ctypes, wintypes, k32), h, why = _win_open_ours(pid, born, job, 0x0200 | 0x1000)
        if h is None:                                 # PROCESS_SET_INFORMATION | QUERY_LIMITED_INFORMATION
            return why
        try:
            if not k32.SetPriorityClass(wintypes.HANDLE(h), wintypes.DWORD(want)):
                err = ctypes.get_last_error()
                return "access denied" if err == 5 else f"Windows error {err}"
            # A job object with a priority-class limit (a job wrapper's, for one) makes SetPriorityClass succeed
            # and change nothing (MEASURED 2026-11-09 under rb: the call returned success, the class stayed
            # below-normal). Read it back: "done" is the process's state, not the call's return.
            got = k32.GetPriorityClass(wintypes.HANDLE(h))
            if got != want:
                now = next((lvl for lvl, cls in _WIN_CLASS.items() if cls == got), hex(got))
                return f"not taken, still {now} (a job object, such as a job wrapper's, limits its priority)"
            return None
        finally:
            k32.CloseHandle(h)
    why = _posix_ours(pid, born)        # identity per PROCESS (its start time); the threads follow it
    if why:
        return why
    return _posix_set(pid, want, threads if threads is not None else {}, own_originals)


def _priority_raw(pid: int, born: int | None = None, job=None) -> int | None:
    """The raw priority (Windows class, POSIX nice) of the process listed with start time `born`; None when it cannot
    be read or the pid no longer names that process."""
    if os.name == "nt":
        (_c, wintypes, k32), h, _why = _win_open_ours(pid, born, job, 0x1000)
        if h is None:
            return None
        try:
            return k32.GetPriorityClass(wintypes.HANDLE(h)) or None
        finally:
            k32.CloseHandle(h)
    if _posix_ours(pid, born):
        return None
    try:
        return os.getpriority(os.PRIO_PROCESS, pid)
    except OSError:
        return None  # silent-ok: None = cannot be read, this function's documented non-answer


def _get_priority(pid: int) -> str | None:
    """IDLE, BELOW_NORMAL, another value as text, or None when it cannot be read (no identity check: a test aid)."""
    if os.name == "nt":
        ctypes, wintypes, k32 = _kernel32()
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        try:
            got = k32.GetPriorityClass(wintypes.HANDLE(h))
        finally:
            k32.CloseHandle(h)
        return next((lvl for lvl, cls in _WIN_CLASS.items() if cls == got), hex(got) if got else None)
    try:
        got = os.getpriority(os.PRIO_PROCESS, pid)
    except OSError:
        return None  # silent-ok: None = cannot be read, this function's documented non-answer
    return next((lvl for lvl, n in _NICE.items() if n == got), str(got))


def _short(errs: dict[str, list[int]]) -> str:
    return "; ".join(errs)[:200]


class _GameWatch:
    """Watches for a game while one database builds; see the section comment above. `on_change(watch)` runs when what
    `x4fresh status` should show changes (the background builder records it); `lines(db)` is for the note."""

    def __init__(self, on_change=None):
        self.level: str | None = None       # None: never touched; IDLE; BELOW_NORMAL: restored after a game
        self.priority = ""                  # what `x4fresh status` shows while the build runs: the MEASURED state
        self.priority_at = ""               # when that last changed
        self.events: list[str] = []
        self._more = 0                      # priority changes past EVENTS_MAX, counted
        self._last_event = ""
        self.problems: dict[tuple, str] = {}
        self._refused: dict[tuple, list[int]] = {}
        self._original: dict[tuple[int, int | None], int | None] = {}   # (pid, start) -> priority at first touch
        self._lowered: set[tuple[int, int | None]] = set()               # what this watch dropped to idle
        self._threads: dict[tuple[int, int | None], dict] = {}           # POSIX: per-thread originals per process
        self._since = ""
        self._touched = 0                   # successful priority changes, so a failure can say some happened
        self._unwritten = False             # on_change failed: write the state again on the next poll
        self.job = None
        self.on_change = on_change
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _event(self, text: str) -> None:
        if text == self._last_event:      # the same state again, back to back: said once
            return
        self._last_event = text
        if len(self.events) >= EVENTS_MAX:
            self._more += 1
        else:
            self.events.append(text)

    def _refuse(self, kind: str, pid: int, err: str) -> None:
        pids = self._refused.setdefault((kind, err), [])
        if pid not in pids:
            pids.append(pid)
        shown = ", ".join(str(p) for p in pids[:MAX_LISTED]) + (", ..." if len(pids) > MAX_LISTED else "")
        self.problems[("refused", kind, err)] = f"could not {kind} {len(pids)} process(es) (pid {shown}): {err}"

    def poll(self, root: int) -> None:
        busy = _running_deferring_processes()
        when = time.strftime("%H:%M", time.localtime(_now()))
        before = self.priority
        if busy is None or busy:
            why = ("could not list running processes, so a game may be running" if busy is None
                   else f"a game started ({', '.join(busy)})")
            if self.level != IDLE:
                self.level, self._since = IDLE, when
            # every poll: a process the build started since is dropped too, and the state shown is re-measured
            self.priority, event = self._lower(root, why)
            if self.priority != before:
                self._event(event)
        elif self.level == IDLE:
            self.level = BELOW_NORMAL
            self.priority, event = self._restore(root, when)
            self._event(event)
        if self.priority != before:
            self.priority_at = _stamp(_now())
        if (self.priority != before or self._unwritten) and self.on_change is not None:
            self._unwritten = True
            self.on_change(self)
            self._unwritten = False

    def _lower(self, root: int, why: str) -> tuple[str, str]:
        """(what status shows, the note's event) for one drop to idle, from what the processes took."""
        tree = _descendants(root)
        since = self._since
        if tree is None:
            self.problems[("tree",)] = "could not list the build's processes to set idle priority"
            return (f"idle requested {since}, not taken: could not list the build's processes: {why}",
                    f"idle priority requested at {since}, not taken: could not list the build's processes: {why}")
        took, refused, errs = 0, 0, {}
        keys = {pid: (pid, born) for pid, born, _parent in tree}
        parents = {pid: parent for pid, _born, parent in tree}
        earlier = set(self._lowered)     # lowered by an EARLIER poll: a process seen in this pass existed before
        for pid, born, _parent in tree:
            key = (pid, born)
            inherited = False
            if key not in self._original:
                # A process the build started after the drop (a new java) inherited idle from its lowered parent:
                # its own reading is not what it had before the game. It takes its nearest lowered ancestor's.
                ancestor = self._lowered_ancestor(pid, keys, parents, earlier)
                if ancestor is not None:
                    self._original[key], inherited = self._original[ancestor], True
                    self._lowered.add(key)
                    earlier.add(key)         # its own children, below it in this same pass, inherited from it
                else:
                    self._original[key] = _priority_raw(pid, born, self.job)
            orig = self._original[key]
            if key not in self._lowered and orig is not None and _rank(orig) <= _rank(_raw(IDLE)):
                took += 1                              # idle already, before any game: never touched
                continue
            err = _set_priority(pid, IDLE, born, self.job, threads=self._threads.setdefault(key, {}),
                                own_originals=not inherited)
            if err in (_GONE, _NOT_OURS):
                continue
            if err:
                refused += 1
                errs.setdefault(err, []).append(pid)
                self._refuse("set idle priority on", pid, err)
            else:
                took += 1
                self._touched += 1
                self._lowered.add(key)
        total = took + refused
        if not refused:
            return (f"idle since {since}: {why}", f"lowered to idle priority at {since}: {why}")
        if not took:
            return (f"idle requested {since}, not taken: {refused} of {total} processes ({_short(errs)}): {why}",
                    f"idle priority requested at {since}, not taken on {refused} of {total} processes: {why}")
        return (f"idle since {since} for {took} of {total} processes; not taken: {refused} ({_short(errs)}): {why}",
                f"lowered to idle priority at {since} on {took} of {total} processes, not taken on {refused}: {why}")

    def _lowered_ancestor(self, pid: int, keys: dict, parents: dict, lowered: set) -> tuple | None:
        """The key of `pid`'s nearest RECORDED ancestor in this tree if that one is in `lowered`, else None: a parent
        that was idle before the game passed on a real idle, not one this watch caused."""
        seen, up = {pid}, parents.get(pid)
        while up is not None and up not in seen:
            if keys.get(up) in self._original:
                return keys[up] if keys[up] in lowered else None
            seen.add(up)
            up = parents.get(up)
        return None

    def _restore(self, root: int, when: str) -> tuple[str, str]:
        """(what status shows, the note's event) for the restore after a game: only what this watch lowered, back to
        what it had, never above below-normal."""
        tree = _descendants(root)
        if tree is None:
            self.problems[("tree", "restore")] = "could not list the build's processes to restore their priority"
            return (f"restore at {when} not done: could not list the build's processes, so they stay at idle",
                    f"restore to below-normal at {when} not done: could not list the build's processes")
        done, lower, left, errs = 0, 0, 0, {}
        for pid, born, _parent in tree:
            key = (pid, born)
            if key not in self._lowered:
                continue
            self._lowered.discard(key)
            orig = self._original.get(key)
            if orig is None:
                left += 1
                errs.setdefault("original priority unknown", []).append(pid)
                self._refuse("restore the priority of", pid,
                             "its priority before the game could not be read; left at idle")
                continue
            target = BELOW_NORMAL if _rank(orig) >= _rank(_raw(BELOW_NORMAL)) else orig
            err = _set_priority(pid, target, born, self.job, threads=self._threads.setdefault(key, {}))
            if err in (_GONE, _NOT_OURS):
                continue
            if not err:
                self._touched += 1
                # The state, not the call's return: read it back (POSIX setpriority, or a thread left behind).
                got = _priority_raw(pid, born, self.job)
                if got is not None and got != _raw(target):
                    err = f"the call succeeded but its priority read back is {got}"
            if err:
                left += 1
                errs.setdefault(err, []).append(pid)
                self._refuse("restore the priority of", pid, f"{err}; left at idle")
            elif target == BELOW_NORMAL:
                done += 1
            else:
                lower += 1                             # back to an original lower than below-normal
        total = done + lower + left
        if not left:
            if not lower:
                return (f"below-normal again since {when}, after a game ran",
                        f"restored to below-normal priority at {when}: the game closed")
            # A zero count is left out: "(0 to below-normal, ...)" named a priority no process was given (CI,
            # MEASURED at cba3a1ce on ubuntu, where a nice value lower than below-normal exists).
            parts = ([f"{done} to below-normal"] if done else []) + [f"{lower} to their lower earlier priority"]
            return (f"priority restored since {when} after a game ran ({', '.join(parts)})",
                    f"restored to the earlier priority at {when}: the game closed")
        return (f"restore at {when} incomplete: {left} of {total} processes left at idle ({_short(errs)})",
                f"priority restore at {when} incomplete: {left} of {total} processes left at idle")

    def safe_poll(self, root: int) -> None:
        shown, touched = self.priority, self._touched
        try:
            self.poll(root)
        except Exception as exc:  # noqa: BLE001 -- the watch must never stop the build it watches
            what = f"{type(exc).__name__}: {exc}"
            if self.priority != shown or self._unwritten:
                said = f"priority changed (now: {self.priority}); status write failed: {what}"
            elif self._touched != touched:
                said = (f"could not finish watching for a game ({what}); {self._touched - touched} process(es) had "
                        "their priority changed before it stopped")
            else:
                said = f"could not watch for a game ({what}); the build kept its priority"
            self.problems[("raised", type(exc).__name__)] = said

    def lines(self, name: str) -> list[str]:
        more = [f"{name}: ... {self._more} more priority change(s)"] if self._more else []
        return [f"{name}: {e}" for e in self.events] + more + [f"{name}: {p}" for p in self.problems.values()]

    def start(self, proc, job=None) -> None:
        """Never raises: a watch that cannot start is said in the note, and the build runs on unwatched.
        `job`: the job object the build's tree is in, which confirms a process is ours (Windows)."""
        root = proc.pid
        self.job = job
        if self.level == IDLE:
            self.safe_poll(root)             # a second process for the same database (the swap, then the build)
        self._stop.clear()

        def loop():
            while not self._stop.wait(GAME_POLL_SECONDS):
                self.safe_poll(root)
        try:
            self._thread = threading.Thread(target=loop, name="x4fresh-game-watch", daemon=True)
            self._thread.start()
        except Exception as exc:  # noqa: BLE001 -- the watch must never stop the build it watches
            self._thread = None
            self.problems[("raised", "start", type(exc).__name__)] = (
                f"could not watch for a game ({type(exc).__name__}: {exc}); the build ran unwatched")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None


def _memo_path(sd: Path) -> Path:
    return sd / "last-failures.json"


def _load_memo(sd: Path) -> dict:
    try:
        data = json.loads(_memo_path(sd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # silent-ok: no/unreadable memo = no remembered failure, so the build is attempted (one retry at most)
    # The same for a memo that parses to the wrong shape: it remembers nothing, it must not crash the run.
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _world_digest() -> str | None:
    """Both freshness axes of the installed world right now, or None when that cannot be established."""
    try:
        cfg = _merge.Config()
        fp = _freshness.fingerprint(cfg, _effective._ext_root(cfg))
    except Exception:  # noqa: BLE001  # silent-ok: None = a non-answer; the caller says "could not be compared"
        return None
    return f"{fp['content']}/{fp['engine']}" if fp.get("content") else None


def _revoke_basex_stamp(name: str) -> str | None:
    """BaseX stamps at the END of its build, so a world that moved during it reads FRESH under the new digest.
    ask.py reads the coverage file, never x4fresh, so the correction goes there: every other key survives,
    and the content axis can no longer match. Returns why it could not, or None."""
    cov = BASEX_ROOT / "basex" / f"coverage-{name}.json"
    try:
        data = json.loads(cov.read_text(encoding="utf-8"))
        data["fingerprint"]["content"] = MOVED_STAMP
        _write_note(cov, json.dumps(data, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


NOTE_REPLACE_TRIES = 5      # os.replace attempts; Windows refuses (PermissionError) while a reader has the target open
NOTE_REPLACE_PAUSE = 0.25   # seconds between them: about a second in all, then the failure is the caller's as before


def _write_note(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    try:
        for attempt in range(NOTE_REPLACE_TRIES):
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                # A concurrent `x4fresh status` reading the file; the waiting builder rewrites its record every minute.
                if attempt == NOTE_REPLACE_TRIES - 1:
                    raise
                _sleep(NOTE_REPLACE_PAUSE)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def rebuild(only=ORDER, auto: bool = False, note: Path | None = None, named: bool = False) -> int:
    """`named`: the artifacts were asked for with --only, so a NOT-BUILT one among them counts."""
    sd = state_dir()
    if sd is None:
        print("x4fresh: REFUSING: the registry location is not configured (set X4_MODS)", file=sys.stderr)
        return 2
    # env-ok: the session-start opt-out switch, not a configured location
    if auto and os.environ.get("X4_FRESH_AUTO", "").lower() == "off":
        return 0
    note = (note or sd / "session-note.txt").resolve()
    lock = BuildLock(sd / "x4fresh.lock")
    if not lock.acquire():
        print("x4fresh: a rebuild is already running (lock held); nothing started")
        if auto:
            # The background BaseX builder holds the lock for half an hour: without this the launcher's "did not
            # complete ... or it died" stood in the note for every session start meanwhile.
            _write_note(note, _running_note(sd))
        return 0 if auto else 5
    try:
        targets = [n for n in only if not auto or n in AUTO_SET]
        # Tells a concurrent `status` which artifacts are actually in play (review finding 7).
        _write_note(sd / "rebuilding.json", json.dumps({"targets": targets, "auto": auto, "pid": os.getpid()}))
        if auto:
            _write_note(note, NOTE_PENDING)
        return _rebuild_locked(sd, targets, auto, note, named=named)
    finally:
        with contextlib.suppress(OSError):
            (sd / "rebuilding.json").unlink()
        lock.release()


def _not_built_path(sd: Path) -> Path:
    return sd / "not-built-noted.json"


def _note_not_built_once(sd: Path, final: list[Status]) -> list[str]:
    """Which NOT-BUILT artifacts the session note has already named. Records the current set, so one that
    gets built and is later lost again is news again. Unreadable: named again, the loud direction."""
    try:
        told = json.loads(_not_built_path(sd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        told = []  # silent-ok: nothing remembered means it is named again, once more
    now = sorted(s.name for s in final if s.state == NOT_BUILT)
    _write_note(_not_built_path(sd), json.dumps(now))
    return [n for n in now if isinstance(told, list) and n in told]


def _memo_key(s: Status, world: str | None) -> str:
    # The reasons alone are not "the same inputs": MISSING and UNKNOWN carry no digest, so a mod could
    # change and the same words would suppress the retry forever (review T10). Volatile reasons are left out: they
    # describe what a build left behind, not its inputs (4.1.1 review).
    return " | ".join(r for r in s.reasons if r not in s.volatile) + f" @ {world}"


def _rebuild_locked(sd: Path, targets, auto: bool, note: Path, named: bool = False,
                    basex_child: bool = False) -> int:
    """`basex_child`: this is the detached background BaseX builder; every database is checked again just before
    its build (the game, the quiet period, the disk, locks, the effective store), and its outcome is recorded."""
    before = {s.name: s for s in status(ORDER, holding_lock=True)}
    lines, failed, unclean = [], False, False
    running_since = ""
    swap_waiting = False      # a finished build's swap is still refused by a reader: pending, not a failure
    skips: dict[str, str] = {}
    ran: list[str] = []
    why = _deferred(auto)
    if why:
        lines.append(f"DEFERRED, nothing rebuilt: {why}")
    memo = _load_memo(sd)
    for name in targets if not why else []:
        s = before[name]
        if s.state == "FRESH":
            memo.pop(name, None)     # fixed by hand since: an older failure says nothing about the next one
            continue
        if s.state == NOT_BUILT:
            if named:
                lines.append(f"{name}: NOT BUILT: nothing started; {s.reasons[0] if s.reasons else ''}")
            continue
        if basex_child:
            not_now = _basex_ready(sd, name, memo)
            if not_now:
                skips[name] = not_now
                continue
            running_since = _stamp(_now())
            _basex_record(sd, "running", building=name, when=running_since)
            ran.append(name)
        # A finished build an earlier run could not swap in (rc 7: a reader held the database) is swapped in alone,
        # never dropped and rebuilt; the memo of that rc 7 does not stop it, since nothing about it needs fixing.
        swap_first = name in BASEX and _basex_swap_pending(name)
        world_before = _world_digest()
        key = _memo_key(s, world_before)
        if auto and not swap_first and memo.get(name, {}).get("reasons") == key:
            what = ("against these same inputs" if world_before is not None
                    else "and its inputs could not be compared since")
            lines.append(f"{name}: NOT RETRIED: it did not end FRESH {what} at "
                         f"{memo[name].get('when')} (rc {memo[name].get('rc')}); run `x4fresh rebuild` "
                         "by hand after fixing the cause")
            continue
        if _mutation.active() is not None:
            lines.append(f"{name}: SKIPPED: a mutation gate started meanwhile")
            continue
        swap_held = False
        # Only the background builder: it is the one that builds unattended, maybe while the user starts a game.
        # A priority change keeps `when` (the build's start, "running since") and says its own time apart.
        watch = (_GameWatch(on_change=lambda w, db=name, since=running_since: _basex_record(
            sd, "running", building=db, when=since, priority=w.priority, priority_at=w.priority_at))
                 if basex_child else None)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        log = sd / "logs" / f"{name}-{stamp}.log"
        try:
            if swap_first:
                swap_log = sd / "logs" / f"{name}-{stamp}-swap.log"
                rc, warnings, wrapper_lines = _run(name, swap_log, _swap_argv(name), watch=watch)
                now = check(name).state if rc == 0 else None
                if now == "FRESH":
                    lines.append(f"{name}: swapped in the finished build an earlier run could not swap; no rebuild "
                                 f"needed (log: {swap_log})")
                    log = swap_log
                elif rc == SWAP_LOCKED_RC:
                    lines.append(f"{name}: the finished build an earlier run left still cannot be swapped in: a "
                                 "reader holds the database; the next run retries the swap alone")
                    log = swap_log
                    swap_held = swap_waiting = True
                else:
                    lines.append(f"{name}: the finished build an earlier run left was "
                                 + (f"swapped in, but it is {now}" if rc == 0 else f"not swapped in (rc {rc})")
                                 + f"; rebuilding (log: {swap_log})")
                    swap_first = False
            if not swap_first:
                rc, warnings, wrapper_lines = _run(name, log, watch=watch)
        except (OSError, ValueError) as exc:
            rc, warnings, wrapper_lines = -1, [f"could not start the builder: {exc}"], []
        world_after = _world_digest()
        moved = world_before is not None and world_after is not None and world_before != world_after
        if moved and name not in AUTO_SET:
            why_not = _revoke_basex_stamp(name)
            if why_not:
                lines.append(f"{name}: its stamp could not be revoked ({why_not}): it may read FRESH over a moved world")
                unclean = True
        after = _mark_accepted(check(name), _load_accepted(sd))
        if swap_held:
            # Exit 5's meaning (nothing failed, the artifact is not current), not a builder failure; no failure memo,
            # which would only get in the way of the swap-alone retry the next run makes anyway.
            unclean = True
        elif rc not in (0, 3):
            failed = True
            memo[name] = {"reasons": key, "rc": rc, "when": time.strftime("%Y-%m-%d %H:%M")}
            why = _exit_reason(wrapper_lines, rc)                  # a wrapper's refusal or kill may be WHY it failed
            wrapper_lines = _explaining_first(wrapper_lines, rc)
            if why and len(why) > WHY_MAX:
                why = why[:WHY_MAX - 3] + "..."
            lines.append(f"{name}: FAILED rc {rc}, now {after.state} (log: {log})"
                         + (f" -- job wrapper: {why}" if why else ""))
            warnings = wrapper_lines + warnings
        elif after.state != "FRESH":
            # The builder did its work and the artifact still is not current (typically: the world moved during it).
            # Not "a builder failed", exit 1's documented meaning: this is exit 5, and the note carries it.
            unclean = True
            memo[name] = {"reasons": key, "rc": rc, "when": time.strftime("%Y-%m-%d %H:%M")}
            lines.append(f"{name}: BUILT (rc {rc}), but now {after.state} (log: {log})")
        else:
            memo.pop(name, None)
            # The count is the artifact's OWN (meta), never the number of sample lines (capped at 10).
            outside = [w for w in warnings if not LOSS_SUMMARY_RE.search(w)]
            if after.accepted and not outside:
                # The SAME losses the user accepted: still said, never called clean, but not news.
                verdict = f"REBUILT, FRESH ({after.incomplete} known loss(es), accepted {after.accepted})"
            else:
                verdict = f"BUILT WITH {after.incomplete} DROPS" if after.incomplete else (
                    "BUILT WITH WARNINGS" if warnings or rc == 3 else "REBUILT, FRESH")
                unclean = unclean or verdict != "REBUILT, FRESH"
            lines.append(f"{name}: {verdict} (log: {log})")
        if world_before is None or world_after is None:
            unclean = True
            lines.append(f"{name}: the installed world's digest could not be compared around this build, "
                         "so a change during it cannot be ruled out")
        elif moved:
            unclean = True
            lines.append(f"{name}: the installed world MOVED during this build [{world_before} -> {world_after}]: "
                         "a mod, the reference or the engine changed while it ran, so rebuild once that settles")
        if wrapper_lines and rc in (0, 3):
            lines.append(f"{name}: {len(wrapper_lines)} line(s) from the job wrapper itself are in the log, not counted "
                         f"as losses; first: {wrapper_lines[0]}")
        if watch is not None:
            lines.extend(watch.lines(name))      # a game seen mid-build: when it went idle, and any problem
        lines.extend(f"    {w}" for w in warnings[:8])
        if len(warnings) > 8:
            lines.append(f"    ... {len(warnings) - 8} more warning line(s) in the log")
    _prune_logs(sd / "logs")
    sd.mkdir(parents=True, exist_ok=True)
    memo_tmp = _memo_path(sd).with_suffix(f".{os.getpid()}.tmp")
    memo_tmp.write_text(json.dumps(memo, indent=1), encoding="utf-8")
    os.replace(memo_tmp, _memo_path(sd))
    final = status(ORDER, holding_lock=True)
    left = [s for s in final if s.state not in ("FRESH", NOT_BUILT)]
    if basex_child:
        lines.extend(_skip_lines(skips))
        outcome = "failed" if failed else ("swap pending" if swap_waiting else ("finished" if ran else "skipped"))
        detail = [ln for ln in lines if ln.startswith(BASEX) or ln.startswith("DEFERRED")]
        _basex_record(sd, outcome, detail="; ".join(detail)[:600] or "nothing to rebuild")
    elif auto and not why:
        lines.extend(_basex_session_plan(sd, {s.name: s for s in final}, memo, note))
    head = render(final).splitlines()[0]
    report = "\n".join([head] + lines + render(final).splitlines()[1:])
    print(report)
    if auto:
        # ANY outcome other than a clean rebuild reaches the next session start -- a drop or a warning
        # included (review finding 2: those went only to stdout, which the launcher discards).
        # The background builder overwrites a note a session start wrote earlier, maybe naming a NOT-BUILT database for
        # the first time and before anyone printed it: it names every NOT-BUILT row and leaves the record alone.
        told = [] if basex_child else _note_not_built_once(sd, final)
        shown = [s for s in final if s.name not in told]
        noteworthy = left or failed or unclean or _unaccepted(final) or \
            any("DEFERRED" in ln or "NOT RETRIED" in ln for ln in lines) or \
            any(s.state == NOT_BUILT for s in shown)
        rendered = render(shown).splitlines()
        _write_note(note, "\n".join(rendered[:1] + lines + rendered[1:]) + "\n" if noteworthy else "")
        return 1 if failed else 0
    if failed:
        return 1
    still = [s for s in final if s.name in targets and s.state != "FRESH" and (named or s.state != NOT_BUILT)]
    return 5 if still else 0


# --------------------------------------------------------------------------- the automatic BaseX rebuild (4.1.1)

def _now() -> float:
    return time.time()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _stamp(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _switch_off(var: str) -> bool:
    # env-ok: a per-machine runtime switch, not a configured location
    return os.environ.get(var, "").strip().lower() == "off"


def _basex_quiet_seconds() -> tuple[float | None, str | None]:
    """(seconds, None), or (None, why): an unreadable setting is a non-answer, never a guessed default."""
    # env-ok: a per-machine runtime switch (minutes), not a configured location
    raw = os.environ.get("X4_FRESH_BASEX_QUIET_MIN", "").strip()
    if not raw:
        return BASEX_QUIET_MIN * 60.0, None
    try:
        minutes = float(raw)
        if not minutes >= 0 or minutes == float("inf"):
            raise ValueError(raw)
    except ValueError:
        return None, f"X4_FRESH_BASEX_QUIET_MIN={raw!r} is not a number of minutes, so the quiet period is unknown"
    return minutes * 60.0, None


def _first_seen(sd: Path, digest: str, now: float) -> float:
    """When an x4fresh run first saw the installed world with this digest. Measured on the digest itself (both freshness
    axes), not on file times: a removed mod or an engine change moves it too, and nothing outside what freshness
    covers can. It errs late, never early: a change is first seen at the next session start, not when it was made."""
    p = sd / "world-seen.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None  # silent-ok: nothing remembered starts the quiet period now, the waiting direction
    if isinstance(data, dict) and data.get("digest") == digest and isinstance(data.get("first_seen"), (int, float)) \
            and data["first_seen"] <= now:
        return float(data["first_seen"])
    _write_note(p, json.dumps({"digest": digest, "first_seen": now}))
    return now


_DBSWAP = None


def _load_dbswap():
    """tools/basex/dbswap.py, loaded by file (it is not in this package)."""
    global _DBSWAP
    if _DBSWAP is None or Path(_DBSWAP.__file__).resolve() != (BASEX_ROOT / "dbswap.py").resolve():
        spec = importlib.util.spec_from_file_location("x4fresh_dbswap", BASEX_ROOT / "dbswap.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _DBSWAP = mod
    return _DBSWAP


def _basex_previously_built(db: str) -> bool:
    """Built here before: a coverage report, or the database (or the previous generation of an interrupted swap)."""
    home = BASEX_ROOT / "basex"
    if (home / f"coverage-{db}.json").is_file():
        return True
    bx = _load_dbswap().BaseXRunner(home)
    return bx.exists(db) or bx.exists(db + "__old")


def _basex_running_build(db: str) -> int | None:
    return _load_dbswap().running_build(BASEX_ROOT / "basex", db)


SWAP_LOCKED_RC = 7    # dbswap.RC_LOCKED: refused because a reader (or another build) holds the database


def _basex_swap_pending(db: str) -> bool:
    """A finished, stamped <db>__next waits to be swapped in, and no build of `db` is running (dbswap's own reading)."""
    try:
        mod = _load_dbswap()
        home = BASEX_ROOT / "basex"
        return bool(mod.unswapped_next(mod.BaseXRunner(home), home, db)) and mod.running_build(home, db) is None
    except Exception:  # noqa: BLE001  # silent-ok: False = the full build runs, whose `prepare` reports what it finds
        return False


def _swap_argv(db: str) -> list[str]:
    return [sys.executable, str(BASEX_ROOT / "dbswap.py"), "swap", "--db", db]


def _basex_disk_shortfall(db: str) -> str | None:
    mod = _load_dbswap()
    home = BASEX_ROOT / "basex"
    return mod.disk_shortfall(mod.BaseXRunner(home), home, db)


def _basex_blocker(db: str, statuses: dict, memo: dict, world: str | None) -> str | None:
    """Why `db` must not be rebuilt automatically now, or None. Cheap checks first. An exception from any check blocks
    (said in the note); but `_basex_unbuildable` itself returns None when its BaseX/Java preflight cannot run, so that
    one never blocks here: the build's own preflight refuses if BaseX or Java is really absent."""
    try:
        if not _basex_previously_built(db):
            return (f"never built here; an automatic rebuild only refreshes a database built once before "
                    f"(`x4fresh rebuild --only {db}`)")
        if db == "x4eff" and statuses["effective"].state != "FRESH":
            return (f"the effective store is {statuses['effective'].state}, and x4eff is built from the same merge, "
                    "so it waits for a FRESH store")
        # Only the swap of a finished build is waiting: it needs no new disk, and the memo of its rc 7 does not stop it.
        swap_only = _basex_swap_pending(db)
        m = memo.get(db, {})
        if not swap_only and m.get("reasons") == _memo_key(statuses[db], world):
            return (f"not retried: it did not end FRESH against these same inputs at {m.get('when')} (rc {m.get('rc')}); "
                    f"run `x4fresh rebuild --only {db}` by hand after fixing the cause")
        pid = _basex_running_build(db)
        if pid:
            return f"a build of {db} is already running (pid {pid})"
        why = _basex_unbuildable()
        if why:
            return f"BaseX cannot build here now: {why}"
        return None if swap_only else _basex_disk_shortfall(db)
    except Exception as exc:  # noqa: BLE001 -- said in the note; an unchecked condition never starts a build
        return f"its conditions could not be checked ({type(exc).__name__}: {exc})"


def _quiet_left(sd: Path) -> tuple[float | None, str | None, float]:
    """(seconds still to wait, why not measurable, quiet seconds)."""
    quiet, err = _basex_quiet_seconds()
    if err:
        return None, err, 0.0
    world = _world_digest()
    if world is None:
        return None, "the installed world's digest cannot be established, so the quiet period cannot be measured", quiet
    now = _now()
    return max(0.0, quiet - (now - _first_seen(sd, world, now))), None, quiet


def _skip_lines(skips: dict[str, str]) -> list[str]:
    by_reason: dict[str, list[str]] = {}
    for db, why in skips.items():
        by_reason.setdefault(why, []).append(db)
    return [f"{', '.join(dbs)}: not rebuilt automatically: {why}" for why, dbs in by_reason.items()]


def _basex_record(sd: Path, state: str, **fields) -> None:
    """basex-auto.json: what the automatic BaseX rebuild is doing or last did. `status` and the note read it."""
    _write_note(sd / "basex-auto.json", json.dumps(dict({"state": state, "pid": os.getpid(), "when": _stamp(_now())},
                                                        **fields)))


def _basex_auto_line(sd: Path | None) -> str | None:
    if sd is None:
        return None
    try:
        rec = json.loads((sd / "basex-auto.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # silent-ok: no automatic BaseX rebuild has been recorded here
    if not isinstance(rec, dict):
        return None
    state, when, pid = rec.get("state"), rec.get("when", "?"), rec.get("pid", "?")
    if state in ("waiting", "running"):
        if BuildLock(sd / BASEX_AUTO_LOCK).held_elsewhere():
            if state == "waiting":
                return (f"automatic BaseX rebuild: waiting since {when} for the mods to stay unchanged "
                        f"(until about {rec.get('until', '?')}; pid {pid})")
            return (f"automatic BaseX rebuild: running since {when} (pid {pid}"
                    + (f", building {rec['building']}" if rec.get("building") else "")
                    + (f"; {rec['priority']}" if rec.get("priority") else "") + ")")
        return (f"automatic BaseX rebuild: started {when} (pid {pid}) and ended without reporting (killed, or the "
                "machine stopped); the next session start schedules it again")
    return f"last automatic BaseX rebuild: {state} {when}: {rec.get('detail', '')}"


def _running_note(sd: Path) -> str:
    targets = _rebuilding_targets(sd)
    what = f" ({', '.join(n for n in ORDER if n in targets)})" if targets else ""
    line = _basex_auto_line(sd)
    return (f"x4fresh: a rebuild is running{what}, so this session start rebuilt nothing; its outcome replaces this "
            "note. `x4fresh status` says where it is.\n" + (line + "\n" if line else ""))


def _basex_session_plan(sd: Path, final: dict, memo: dict, note: Path) -> list[str]:
    """At the end of a session-start --auto run: start the background BaseX builder, or say in one line per reason why
    not. Never builds here (the session-start run must stay short)."""
    cands = [db for db in BASEX if final[db].state not in ("FRESH", NOT_BUILT)]
    if not cands:
        return []
    skips: dict[str, str] = {}
    left, err, quiet = None, None, 0.0
    # The live builder first, the off switch second: a builder started before the switch was set owns its record,
    # and "skipped" written over it read as if nothing was running (4.1.1 review).
    if BuildLock(sd / BASEX_AUTO_LOCK).held_elsewhere():
        return [f"{', '.join(cands)}: not rebuilt automatically: an automatic BaseX rebuild is already scheduled or "
                "running (`x4fresh status`)"]
    if _switch_off("X4_FRESH_BASEX_AUTO"):
        err = "X4_FRESH_BASEX_AUTO=off"
    else:
        left, err, quiet = _quiet_left(sd)
    if err:
        skips = {db: err for db in cands}
    else:
        world = _world_digest()
        for db in cands:
            why = _basex_blocker(db, final, memo, world)
            if why:
                skips[db] = why
    lines = _skip_lines(skips)
    build = [db for db in cands if db not in skips]
    if not build:
        _basex_record(sd, "skipped", detail="; ".join(lines))
        return lines
    try:
        pid = _spawn_basex(note)
    except (OSError, ValueError) as exc:
        msg = f"{', '.join(build)}: the automatic rebuild could not start ({type(exc).__name__}: {exc})"
        _basex_record(sd, "failed", detail=msg)
        return lines + [msg]
    wait = (f"; it waits until the mods have been unchanged for {quiet / 60:g} min (about "
            f"{time.strftime('%H:%M', time.localtime(_now() + left))})" if left else "")
    return lines + [f"{', '.join(build)}: automatic rebuild started in the background (pid {pid}){wait}; "
                    "its outcome replaces this note"]


def _basex_ready(sd: Path, db: str, memo: dict) -> str | None:
    """In the background builder, just before `db`: everything again, since half an hour may have passed."""
    why = _deferred(True)
    if why:
        return why
    left, err, _quiet = _quiet_left(sd)
    if err:
        return err
    if left:
        return "the mods changed again after the quiet period started; the next session start schedules it again"
    statuses = {"effective": check("effective"), db: check(db)}
    return _basex_blocker(db, statuses, memo, _world_digest())


def _basex_child_argv(note: Path) -> list[str]:
    return [sys.executable, "-m", "x4validate._freshcli", "rebuild", "--auto-basex", "--note", str(note)]


def _on_windows() -> bool:
    return os.name == "nt"


def _spawn_basex(note: Path) -> int:
    """Start the background BaseX builder and return its pid at once: its own process group, a HIDDEN console,
    below-normal priority (inherited by the builders it starts), no handle on our pipes, so it outlives the
    session-start run. MEASURED 2026-11-07 (n=1 each): such a child, with and without breakaway, outlived `uv run`'s
    exit (that measurement used DETACHED_PROCESS; process lifetime does not depend on the console flag).

    CREATE_NO_WINDOW, never DETACHED_PROCESS (4.1.1 review): a DETACHED process has no console at all, so every
    console program it starts -- bash for the whole build, git, tasklist, java -- is given a NEW, VISIBLE console
    window, and closing that window killed the build. A hidden console is inherited by all of them."""
    argv = _basex_child_argv(note)
    common = {"cwd": str(PKG_ROOT), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if _on_windows():
        base = 0x08000000 | 0x00000200 | 0x00004000   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL
        try:
            proc = subprocess.Popen(argv, creationflags=base | 0x01000000, **common)   # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            proc = subprocess.Popen(argv, creationflags=base, **common)    # the job forbids breakaway
    else:
        proc = subprocess.Popen(argv, start_new_session=True, preexec_fn=lambda: os.nice(10), **common)
    proc.returncode = 0   # never waited for, on purpose: keeps Popen.__del__ from warning about a running child
    return proc.pid


def _basex_child(note: Path) -> int:
    """The background builder: one at a time (basex-auto.lock, held for its whole life), waits out the quiet period,
    takes the build lock, then x4raw and x4eff with every condition checked again; the outcome goes to the note and
    to basex-auto.json."""
    sd = state_dir()
    if sd is None:
        return 2
    note = Path(note).resolve()
    alock = BuildLock(sd / BASEX_AUTO_LOCK)
    # `status` and a session start PROBE this lock by taking it for an instant (held_elsewhere): a builder starting in
    # that instant gave up having built and recorded nothing (4.1.1 review). A probe lets go at once; retry briefly.
    for _attempt in range(BASEX_ALOCK_TRIES):
        if alock.acquire():
            break
        _sleep(0.5)
    else:
        return 0      # another background builder really holds it: it owns the outcome and the record
    try:
        # After the lock, so a record is only ever written by its holder.
        off = [v for v in ("X4_FRESH_AUTO", "X4_FRESH_BASEX_AUTO") if _switch_off(v)]
        if off:
            _basex_record(sd, "skipped", detail=f"{off[0]}=off: the background builder started and built nothing")
            return 0
        try:
            return _basex_child_locked(sd, note)
        except Exception as exc:  # noqa: BLE001 -- nobody reads this process's output: the record is the report
            _basex_record(sd, "failed", detail=f"the background builder crashed ({type(exc).__name__}: {exc})")
            return 1
    finally:
        alock.release()


def _basex_child_locked(sd: Path, note: Path) -> int:
    start = _now()
    while True:
        left, err, _quiet = _quiet_left(sd)
        if err or not left:
            break     # an unmeasurable quiet period is said per database by _basex_ready
        if _now() - start > BASEX_MAX_WAIT:
            _basex_record(sd, "skipped", detail=f"the mods kept changing for {BASEX_MAX_WAIT // 3600} hours; the "
                                                "next session start schedules it again")
            return 0
        _basex_record(sd, "waiting", until=time.strftime("%H:%M", time.localtime(_now() + left)))
        _sleep(min(60.0, left))
    lock = BuildLock(sd / "x4fresh.lock")
    asked = _now()
    while not lock.acquire():
        if _now() - asked > BASEX_LOCK_WAIT:
            _basex_record(sd, "skipped", detail=f"another rebuild held the x4fresh lock for {BASEX_LOCK_WAIT // 60} "
                                                "min; the next session start schedules it again")
            return 0
        _sleep(5.0)
    try:
        _write_note(sd / "rebuilding.json", json.dumps({"targets": list(BASEX), "auto": True, "pid": os.getpid()}))
        _basex_record(sd, "running")
        return _rebuild_locked(sd, list(BASEX), True, note, basex_child=True)
    finally:
        with contextlib.suppress(OSError):
            (sd / "rebuilding.json").unlink()
        lock.release()


def _prune_logs(logs: Path, keep: int = 5) -> None:
    """Keep the newest `keep` logs per artifact. Our own output, created by this tool."""
    if not logs.is_dir():
        return
    for name in ORDER:
        mine = sorted(logs.glob(f"{name}-*.log"))
        for old in mine[:-keep]:
            with contextlib.suppress(OSError):
                old.unlink()


# --------------------------------------------------------------------------- CLI

ONLY_HELP = f"limit to these artifacts (default: all of {', '.join(ORDER)})"


@_paths.refuses_unconfigured
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="x4fresh", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("status", help="one verdict per artifact; exit 0 all FRESH and complete, 3 all FRESH "
                                       "but some known INCOMPLETE (dropped/unparsed input), 5 otherwise")
    ps.add_argument("--only", nargs="+", choices=ORDER, default=None, help=ONLY_HELP)
    pr = sub.add_parser("rebuild", help="rebuild what is not FRESH, one at a time, under a lock; exit 0 every "
                                        "requested artifact is FRESH, 1 a builder failed, 5 still not FRESH "
                                        "(deferred, lock held, gate started, world moved); --auto: 0 unless a "
                                        "builder failed")
    pr.add_argument("--only", nargs="+", choices=ORDER, default=None, help=ONLY_HELP)
    pr.add_argument("--auto", action="store_true",
                    help="unattended mode (session start): rebuilds effective + xref, then starts ONE background "
                         "BaseX builder once the game is closed and the mods have been unchanged for "
                         "X4_FRESH_BASEX_QUIET_MIN minutes (default 30; X4_FRESH_BASEX_AUTO=off starts no new one, and one "
                         "already waiting is stopped by its pid in `x4fresh status`); that "
                         "builder holds the build lock while it builds, so other rebuilds exit 5 meanwhile; defers "
                         "while a listed process runs, never retries the same failure")
    pr.add_argument("--auto-basex", action="store_true", help=argparse.SUPPRESS)   # the background builder itself
    pr.add_argument("--note", type=Path,
                    help="with --auto: write the outcome here for the next session start to print "
                         "(reads 'did not complete' until the run reports; empty only when everything this "
                         "machine can build is fresh); default <registry>/x4fresh/session-note.txt")
    pa = sub.add_parser("accept", help="record each artifact's CURRENT losses (dropped/unparsed input) as "
                                       "known; the session note then stays quiet until that set changes")
    pa.add_argument("--only", nargs="+", choices=ORDER, default=None, help=ONLY_HELP)
    args = p.parse_args(argv)
    named = args.only is not None
    only = tuple(args.only) if named else ORDER
    if state_dir() is None:
        # Cold machine: every subcommand refuses with rc 2 ("not set up"), the toolkit-wide contract.
        # `status` used to answer rc 5 with four UNKNOWNs here -- a verdict about artifacts that cannot exist.
        print("x4fresh: REFUSING: the registry location is not configured (set X4_MODS)", file=sys.stderr)
        return 2
    if args.cmd == "status":
        results = status(only)
        print(render(results))
        line = _basex_auto_line(state_dir())
        if line and any(n in BASEX for n in only):
            print(line)
        if not all(s.state == "FRESH" or (s.state == NOT_BUILT and not named) for s in results):
            return 5
        return 3 if _unaccepted(results) else 0
    if args.cmd == "accept":
        return accept(only)
    if args.auto_basex:
        return _basex_child(args.note or state_dir() / "session-note.txt")
    return rebuild(only, auto=args.auto, note=args.note, named=named)


def accept(only=ORDER) -> int:
    """Record the losses of each FRESH artifact as known. A user decision, never automatic: it is what
    lets a permanent, understood loss stop repeating at every session start without hiding a NEW one."""
    sd = state_dir()
    if sd is None:
        print("x4fresh: REFUSING: the registry location is not configured (set X4_MODS)", file=sys.stderr)
        return 2
    data = _load_accepted(sd)
    rc = 0
    for s in status(only):
        if s.state == NOT_BUILT:
            print(f"{s.name}: nothing to accept: it is not built on this machine")
            continue
        if s.state != "FRESH":
            print(f"{s.name}: NOT accepted: it is {s.state}; rebuild first, then accept what the fresh build lost")
            rc = 2
            continue
        if s.incomplete and s.loss_keys is None:
            print(f"{s.name}: NOT accepted: this build does not list its losses by name; rebuild first")
            rc = 2
            continue
        keys = sorted(s.loss_keys or [])
        data[s.name] = {"keys": keys, "when": time.strftime("%Y-%m-%d")}
        print(f"{s.name}: accepted {len(keys)} known loss(es)" + (":" if keys else ""))
        for k in keys[:MAX_LISTED]:
            print(f"    {k}")
        if len(keys) > MAX_LISTED:
            print(f"    ... and {len(keys) - MAX_LISTED} more (all recorded in {_accepted_path(sd)})")
    sd.mkdir(parents=True, exist_ok=True)
    _write_note(_accepted_path(sd), json.dumps(data, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
