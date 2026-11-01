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

from x4validate import __version__, _effective, _freshness, _mutation, _registry, _xref

PKG_ROOT = Path(__file__).resolve().parent.parent          # tools/x4validate
TOOLKIT_ROOT = PKG_ROOT.parent.parent                       # repo root
BASEX_ROOT = PKG_ROOT.parent / "basex"                      # tools/basex
ORDER = ("effective", "xref", "x4raw", "x4eff")
AUTO_SET = ("effective", "xref")
STATES = ("FRESH", "STALE", "UNKNOWN", "MISSING", "REBUILDING")
WARN_RE = re.compile(r"\bWARNING\b|\bNOT INDEXED\b|\bINCOMPLETE\b|\bERROR\b")


@dataclass
class Status:
    name: str
    state: str
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # what the artifact does NOT include
    orphans: list[Path] = field(default_factory=list)   # temp files a killed build left behind


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
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            verdict = _effective.store_freshness(con)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()
    except sqlite3.Error as exc:
        return Status("effective", "UNKNOWN", [f"store unreadable: {exc}"], orphans=orphans)
    notes = []
    dropped = int(meta.get("dropped_overlays") or 0)
    if dropped:
        try:
            samples = json.loads(meta.get("dropped_overlay_samples") or "[]")
        except ValueError:
            samples = []
        notes.append(f"{dropped} overlay(s) could not be read and are NOT in the store; values they "
                     "would set read as base:")
        notes.extend("  - " + s.split(":", 1)[0] for s in samples)
    return Status("effective", _classify(verdict), verdict.reasons, notes, orphans)


def _status_xref() -> Status:
    try:
        tsv = _xref._default_tsv()
    except (SystemExit, ValueError, RuntimeError) as exc:
        return Status("xref", "UNKNOWN", [f"the xref index location is not configured ({exc})"])
    orphans = _orphans(tsv, tsv.name, _xref._sidecar(tsv).name, tsv.name + ".freshness.json")
    if not tsv.is_file():
        return Status("xref", "MISSING", [f"no index at {tsv}"], orphans=orphans)
    try:
        _rows, signed, excl, verdict = _xref.check_index(tsv)
    except _xref.INDEX_UNREADABLE as exc:
        return Status("xref", "UNKNOWN", [f"index unreadable: {exc}"], orphans=orphans)
    reasons = list(verdict.reasons)
    if not (signed and excl):
        reasons.append("index integrity unavailable (unsigned): absence cannot be certified")
    state = _classify(verdict) if verdict.fresh is False else ("FRESH" if signed and excl else "UNKNOWN")
    return Status("xref", state, reasons, orphans=orphans)


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


def status(only=ORDER) -> list[Status]:
    sd = state_dir()
    locked = bool(sd) and BuildLock(sd / "x4fresh.lock").held_elsewhere()
    out = []
    for name in only:
        s = CHECKS[name]()
        if locked and s.state != "FRESH":
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
    else:
        lines.append(f"x4fresh: all {len(results)} artifact(s) FRESH")
    for s in results:
        lines.append(f"  {s.name:<10} {s.state}")
        lines.extend(f"      {r}" for r in s.reasons)
        lines.extend(f"      {n}" for n in s.notes)
        if s.orphans:
            lines.append(f"      {len(s.orphans)} orphan temp file(s) from a build that did not finish "
                         "(safe to delete by hand once no build is running):")
            lines.extend(f"        {p}" for p in s.orphans)
    return "\n".join(lines)


# --------------------------------------------------------------------------- rebuild

def _wrapper(name: str) -> list[str]:
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
    bash = os.environ.get("X4_BASH") or shutil.which("bash")
    if bash and "system32" in bash.lower():   # WSL's bash: a different filesystem view; never the builder's
        bash = None
    git_bash = r"C:\Program Files\Git\bin\bash.exe"
    if not bash and os.name == "nt" and os.path.isfile(git_bash):
        bash = git_bash
    if not bash:
        raise FileNotFoundError("no bash for the BaseX builders (set X4_BASH)")
    script = {"x4raw": "build-corpus.sh", "x4eff": "build-effective.sh"}[name]
    return [bash, str(BASEX_ROOT / script)]


def _run(name: str, log: Path) -> tuple[int, list[str]]:
    argv = _wrapper(name) + builder(name)
    warnings: list[str] = []
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("$ " + " ".join(argv) + "\n")
        fh.flush()
        proc = subprocess.Popen(argv, cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
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
    try:
        out = subprocess.run(["git", "-C", str(TOOLKIT_ROOT), "status", "--porcelain", "--", *rels],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []  # not a git checkout (an installed toolkit): nothing to be dirty against
    if out.returncode != 0:
        return []
    return [ln[3:].strip() for ln in out.stdout.splitlines() if ln.strip()]


def _running_deferring_processes() -> list[str] | None:
    want = [p.strip().lower() for p in os.environ.get("X4_FRESH_DEFER_WHILE", "X4.exe").split(";") if p.strip()]
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20)
            names = {ln.split('","')[0].strip('"').lower() for ln in out.stdout.splitlines() if ln.strip()}
        else:
            out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=20)
            names = {Path(ln.strip()).name.lower() for ln in out.stdout.splitlines() if ln.strip()}
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not names:
        return None
    return [w for w in want if w in names]


def _memo_path(sd: Path) -> Path:
    return sd / "last-failures.json"


def _load_memo(sd: Path) -> dict:
    try:
        return json.loads(_memo_path(sd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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
    if auto and os.environ.get("X4_FRESH_AUTO", "").lower() == "off":
        return 0
    lock = BuildLock(sd / "x4fresh.lock")
    if not lock.acquire():
        print("x4fresh: a rebuild is already running (lock held); nothing started")
        return 0
    try:
        return _rebuild_locked(sd, only, auto, note or sd / "session-note.txt")
    finally:
        lock.release()


def _rebuild_locked(sd: Path, only, auto: bool, note: Path) -> int:
    targets = [n for n in only if not auto or n in AUTO_SET]
    before = {s.name: s for s in status(ORDER)}
    lines, failed = [], False
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
        try:
            rc, warnings = _run(name, log)
        except (OSError, ValueError) as exc:
            rc, warnings = -1, [f"could not start the builder: {exc}"]
        after = CHECKS[name]()
        dropped = sum(1 for n in after.notes if n.startswith("  - "))
        if rc not in (0, 3) or after.state != "FRESH":
            failed = True
            memo[name] = {"reasons": key, "rc": rc, "when": time.strftime("%Y-%m-%d %H:%M")}
            lines.append(f"{name}: FAILED rc {rc}, now {after.state} (log: {log})")
        else:
            memo.pop(name, None)
            verdict = "BUILT WITH {} DROPS".format(dropped) if dropped else (
                "BUILT WITH WARNINGS" if warnings or rc == 3 else "REBUILT, FRESH")
            lines.append(f"{name}: {verdict} (log: {log})")
        lines.extend(f"    {w}" for w in warnings[:8])
        if len(warnings) > 8:
            lines.append(f"    ... {len(warnings) - 8} more warning line(s) in the log")
    _prune_logs(sd / "logs")
    sd.mkdir(parents=True, exist_ok=True)
    memo_tmp = _memo_path(sd).with_suffix(f".{os.getpid()}.tmp")
    memo_tmp.write_text(json.dumps(memo, indent=1), encoding="utf-8")
    os.replace(memo_tmp, _memo_path(sd))
    final = status(ORDER)
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
        _write_note(note, report + "\n" if left or failed or any("DEFERRED" in ln for ln in lines) else "")
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

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="x4fresh", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("status", help="one verdict per artifact; exit 0 only if all are FRESH")
    ps.add_argument("--only", nargs="+", choices=ORDER, default=list(ORDER))
    pr = sub.add_parser("rebuild", help="rebuild what is not FRESH, one at a time, under a lock")
    pr.add_argument("--only", nargs="+", choices=ORDER, default=list(ORDER))
    pr.add_argument("--auto", action="store_true",
                    help="unattended mode (session start): effective + xref only, defers while a "
                         "listed process runs, never retries the same failure")
    pr.add_argument("--note", type=Path,
                    help="with --auto: write the outcome here for the next session start to print "
                         "(empty when everything is fresh); default <registry>/x4fresh/session-note.txt")
    args = p.parse_args(argv)
    if args.cmd == "status":
        results = status(tuple(args.only))
        print(render(results))
        return 0 if all(s.state == "FRESH" for s in results) else 5
    return rebuild(tuple(args.only), auto=args.auto, note=args.note)


if __name__ == "__main__":
    sys.exit(main())
