"""x4fresh -- is every derived artifact current, and rebuild the ones that are not.

Four artifacts answer questions about the live modlist, and each one describes the world AS OF ITS BUILD:

    effective   the effective store (x4effective)          rebuild: x4effective build
    xref        the md/aiscript cross-index (x4xref)       rebuild: x4xref build
    x4raw       BaseX, every file as written               rebuild: tools/basex/build-corpus.sh
    x4eff       BaseX, the merged effective tree           rebuild: tools/basex/build-effective.sh

`status` asks each artifact's OWN freshness check (the same code its readers use) and prints one verdict
per artifact: FRESH, STALE (with the reason), UNKNOWN (freshness cannot be established -- never read as
fresh), MISSING, or REBUILDING (another x4fresh holds the build lock). It also lists what the last build
could NOT include (the store's dropped overlays) and orphan temp files a killed build left behind.

`rebuild` runs the builders ONE AT A TIME (two concurrent builds once cost x4eff 17% of its documents),
under an OS lock that the operating system releases if the holder dies, so a crashed build never leaves a
stale lock (MEASURED 2026-11-01: 3 of 3 hard kills released it at once). Every builder's WARNING lines are
carried to the final summary: a build that dropped input ends "BUILT WITH N DROPS", never "fresh".

`rebuild --auto` is what a session start launches in the background. It only rebuilds the effective store
and the xref index: the BaseX builders drop their database for the length of the build, so they are never
started unattended (reported instead). It defers while a listed process runs (X4_FRESH_DEFER_WHILE,
default X4.exe), while a mutation gate is editing the engine, or while engine sources are uncommitted, and
it does not retry a build that already failed against the same inputs.

Optional environment:
    X4_JOB_WRAPPER        JSON list prefixed to every builder command; "{name}" becomes x4fresh-<artifact>
                          (e.g. a resource governor). Unset: builders run directly.
    X4_FRESH_DEFER_WHILE  ';'-separated process names that defer --auto (default: X4.exe)
    X4_FRESH_AUTO=off     --auto does nothing
    X4_BASH               bash used for the BaseX builders (default: bash on PATH)
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
import time
from dataclasses import dataclass, field
from pathlib import Path

from x4validate import __version__, _effective, _freshness, _merge, _mutation, _paths, _registry, _xref

PKG_ROOT = Path(__file__).resolve().parent.parent          # tools/x4validate
TOOLKIT_ROOT = PKG_ROOT.parent.parent                       # repo root
BASEX_ROOT = PKG_ROOT.parent / "basex"                      # tools/basex
ORDER = ("effective", "xref", "x4raw", "x4eff")
AUTO_SET = ("effective", "xref")
STATES = ("FRESH", "STALE", "UNKNOWN", "MISSING", "REBUILDING")
# `skipped N unparseable file(s)` is the effective build's whole-file loss line and carries none of the
# other words (review 2026-11-01, finding 1): without it a build that lost files read "REBUILT, FRESH".
WARN_RE = re.compile(r"\bWARNING\b|\bNOT INDEXED\b|\bINCOMPLETE\b|\bERROR\b|\bskipped \d+ unparseable\b")
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


def _status_basex(name: str) -> Status:
    cov = BASEX_ROOT / "basex" / f"coverage-{name}.json"
    if not (BASEX_ROOT / "staleness.py").is_file():
        return Status(name, "UNKNOWN", [f"no BaseX tooling at {BASEX_ROOT}"])
    if not cov.is_file():
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
    entry = accepted.get(s.name) or {}
    if s.incomplete and s.loss_keys is not None and sorted(s.loss_keys) == entry.get("keys"):
        s.accepted = entry.get("when", "?")
    return s


def _unaccepted(results: list[Status]) -> list[Status]:
    return [s for s in results if s.incomplete and not s.accepted]


def _rebuilding_targets(sd: Path) -> set[str] | None:
    """What the lock holder said it would rebuild, or None (unknown: treat every artifact as in play)."""
    try:
        data = json.loads((sd / "rebuilding.json").read_text(encoding="utf-8"))
        return {n for n in data.get("targets", []) if n in ORDER}
    except (OSError, ValueError, AttributeError):
        return None  # silent-ok: None = unknown targets; the caller then reports EVERY artifact as REBUILDING


def status(only=ORDER, holding_lock: bool = False) -> list[Status]:
    """`holding_lock`: the caller IS the rebuild. The lock then reads as held from here too (an OS lock
    refuses a second handle even in the same process), and must not be reported as someone else's.

    While another process rebuilds, only the artifacts IT is rebuilding read REBUILDING; the rest keep
    their real state (an --auto rebuild never touches BaseX, so a stale x4eff must still say STALE).
    Probing the lock takes it for an instant: a rebuild starting in that instant sees "already running"
    and exits 0 -- the next session start retries it."""
    sd = state_dir()
    locked = not holding_lock and bool(sd) and BuildLock(sd / "x4fresh.lock").held_elsewhere()
    in_play = _rebuilding_targets(sd) if locked else set()
    accepted = _load_accepted(sd)
    out = []
    for name in only:
        s = _mark_accepted(check(name), accepted)
        if locked and s.state != "FRESH" and (in_play is None or name in in_play):
            s.reasons.insert(0, f"a rebuild is running (lock held: {sd / 'x4fresh.lock'})")
            s.state = "REBUILDING"
        out.append(s)
    return out


def render(results: list[Status]) -> str:
    """Directive first (a long report is truncated from the tail), inventory after."""
    bad = [s for s in results if s.state != "FRESH"]
    lines = []
    if bad:
        lines.append("x4fresh: NOT CURRENT: " + ", ".join(f"{s.name} {s.state}" for s in bad)
                     + " -- run `x4fresh rebuild`" if any(s.state != "REBUILDING" for s in bad)
                     else "x4fresh: rebuild in progress: " + ", ".join(s.name for s in bad))
    elif _unaccepted(results):
        # Fresh is about WHEN, not HOW MUCH: a current store that dropped input is not "clean".
        lines.append(f"x4fresh: all {len(results)} artifact(s) FRESH, but INCOMPLETE: "
                     + ", ".join(f"{s.name} ({s.incomplete} missing)" for s in _unaccepted(results))
                     + " -- review, then `x4fresh accept` if this loss is known and acceptable")
    else:
        known = sum(s.incomplete for s in results if s.accepted)
        lines.append(f"x4fresh: all {len(results)} artifact(s) FRESH"
                     + (f" ({known} known loss(es), accepted)" if known else ""))
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

def _wrapper(name: str) -> list[str]:
    # env-ok: a per-machine runtime switch (job wrapper argv), not a configured location
    raw = os.environ.get("X4_JOB_WRAPPER", "").strip()
    if not raw:
        return []
    argv = json.loads(raw)
    if not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
        raise ValueError("X4_JOB_WRAPPER must be a JSON list of strings")
    return [a.replace("{name}", f"x4fresh-{name}") for a in argv]


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


def _run(name: str, log: Path) -> tuple[int, list[str]]:
    argv = _wrapper(name) + builder(name)
    warnings: list[str] = []
    log.parent.mkdir(parents=True, exist_ok=True)
    tree = _ChildTree()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("$ " + " ".join(argv) + "\n")
        fh.flush()
        proc = subprocess.Popen(argv, cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                **tree.popen_kwargs())
        if not tree.adopt(proc):
            fh.write("x4fresh: NOTE: the builder is not tied to x4fresh's life on this platform; if x4fresh "
                     "is killed, the build lock frees while the builder may still run\n")
        for line in proc.stdout:
            fh.write(line)
            if WARN_RE.search(line):
                warnings.append(line.rstrip())
        rc = proc.wait()
    return rc, warnings


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


def _memo_path(sd: Path) -> Path:
    return sd / "last-failures.json"


def _load_memo(sd: Path) -> dict:
    try:
        return json.loads(_memo_path(sd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # silent-ok: no/unreadable memo = no remembered failure, so the build is attempted (one retry at most)


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


def _write_note(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, p)


def rebuild(only=ORDER, auto: bool = False, note: Path | None = None) -> int:
    sd = state_dir()
    if sd is None:
        print("x4fresh: REFUSING: the registry location is not configured (set X4_MODS)", file=sys.stderr)
        return 2
    # env-ok: the session-start opt-out switch, not a configured location
    if auto and os.environ.get("X4_FRESH_AUTO", "").lower() == "off":
        return 0
    lock = BuildLock(sd / "x4fresh.lock")
    if not lock.acquire():
        print("x4fresh: a rebuild is already running (lock held); nothing started")
        return 0
    try:
        targets = [n for n in only if not auto or n in AUTO_SET]
        # Tells a concurrent `status` which artifacts are actually in play (review finding 7).
        _write_note(sd / "rebuilding.json", json.dumps({"targets": targets, "auto": auto, "pid": os.getpid()}))
        return _rebuild_locked(sd, targets, auto, (note or sd / "session-note.txt").resolve())
    finally:
        with contextlib.suppress(OSError):
            (sd / "rebuilding.json").unlink()
        lock.release()


def _rebuild_locked(sd: Path, targets, auto: bool, note: Path) -> int:
    before = {s.name: s for s in status(ORDER, holding_lock=True)}
    lines, failed, unclean = [], False, False
    why = _deferred(auto)
    if why:
        lines.append(f"DEFERRED, nothing rebuilt: {why}")
    memo = _load_memo(sd)
    for name in targets if not why else []:
        s = before[name]
        if s.state == "FRESH":
            continue
        key = " | ".join(s.reasons)
        if auto and memo.get(name, {}).get("reasons") == key:
            lines.append(f"{name}: NOT RETRIED: it failed against these same inputs at "
                         f"{memo[name].get('when')} (rc {memo[name].get('rc')}); run `x4fresh rebuild` "
                         "by hand after fixing the cause")
            continue
        if _mutation.active() is not None:
            lines.append(f"{name}: SKIPPED: a mutation gate started meanwhile")
            continue
        log = sd / "logs" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        world_before = _world_digest()
        try:
            rc, warnings = _run(name, log)
        except (OSError, ValueError) as exc:
            rc, warnings = -1, [f"could not start the builder: {exc}"]
        world_after = _world_digest()
        moved = world_before is not None and world_after is not None and world_before != world_after
        if moved and name not in AUTO_SET:
            why_not = _revoke_basex_stamp(name)
            if why_not:
                lines.append(f"{name}: its stamp could not be revoked ({why_not}): it may read FRESH over a moved world")
                unclean = True
        after = _mark_accepted(check(name), _load_accepted(sd))
        if rc not in (0, 3) or after.state != "FRESH":
            failed = True
            memo[name] = {"reasons": key, "rc": rc, "when": time.strftime("%Y-%m-%d %H:%M")}
            lines.append(f"{name}: FAILED rc {rc}, now {after.state} (log: {log})")
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
        lines.extend(f"    {w}" for w in warnings[:8])
        if len(warnings) > 8:
            lines.append(f"    ... {len(warnings) - 8} more warning line(s) in the log")
    _prune_logs(sd / "logs")
    sd.mkdir(parents=True, exist_ok=True)
    memo_tmp = _memo_path(sd).with_suffix(f".{os.getpid()}.tmp")
    memo_tmp.write_text(json.dumps(memo, indent=1), encoding="utf-8")
    os.replace(memo_tmp, _memo_path(sd))
    final = status(ORDER, holding_lock=True)
    left = [s for s in final if s.state != "FRESH"]
    if auto:
        skipped = [s.name for s in left if s.name not in AUTO_SET]
        if skipped:
            lines.append(f"not rebuilt unattended (their build drops the database while it runs): "
                         f"{', '.join(skipped)} -- run `x4fresh rebuild --only {' '.join(skipped)}`")
    head = render(final).splitlines()[0]
    report = "\n".join([head] + lines + render(final).splitlines()[1:])
    print(report)
    if auto:
        # ANY outcome other than a clean rebuild reaches the next session start -- a drop or a warning
        # included (review finding 2: those went only to stdout, which the launcher discards).
        noteworthy = left or failed or unclean or _unaccepted(final) or \
            any("DEFERRED" in ln or "NOT RETRIED" in ln for ln in lines)
        _write_note(note, report + "\n" if noteworthy else "")
    return 1 if failed else 0


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
    ps.add_argument("--only", nargs="+", choices=ORDER, default=list(ORDER), help=ONLY_HELP)
    pr = sub.add_parser("rebuild", help="rebuild what is not FRESH, one at a time, under a lock")
    pr.add_argument("--only", nargs="+", choices=ORDER, default=list(ORDER), help=ONLY_HELP)
    pr.add_argument("--auto", action="store_true",
                    help="unattended mode (session start): effective + xref only, defers while a "
                         "listed process runs, never retries the same failure")
    pr.add_argument("--note", type=Path,
                    help="with --auto: write the outcome here for the next session start to print "
                         "(empty when everything is fresh); default <registry>/x4fresh/session-note.txt")
    pa = sub.add_parser("accept", help="record each artifact's CURRENT losses (dropped/unparsed input) as "
                                       "known; the session note then stays quiet until that set changes")
    pa.add_argument("--only", nargs="+", choices=ORDER, default=list(ORDER), help=ONLY_HELP)
    args = p.parse_args(argv)
    if state_dir() is None:
        # Cold machine: every subcommand refuses with rc 2 ("not set up"), the toolkit-wide contract.
        # `status` used to answer rc 5 with four UNKNOWNs here -- a verdict about artifacts that cannot exist.
        print("x4fresh: REFUSING: the registry location is not configured (set X4_MODS)", file=sys.stderr)
        return 2
    if args.cmd == "status":
        results = status(tuple(args.only))
        print(render(results))
        if not all(s.state == "FRESH" for s in results):
            return 5
        return 3 if _unaccepted(results) else 0
    if args.cmd == "accept":
        return accept(tuple(args.only))
    return rebuild(tuple(args.only), auto=args.auto, note=args.note)


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
