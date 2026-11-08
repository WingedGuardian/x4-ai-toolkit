r"""Build-then-swap for the toolkit's BaseX databases (x4raw, x4eff).

WHY. Until 4.1.1 both builders ran `DROP DB <db>` and then built. MEASURED 2026-11-05: eight builds in a row were
killed by a job-wrapper memory cap, and each kill left x4raw and x4eff MISSING for hours; every `ask.py` corpus
query refused meanwhile. Now a build writes `<db>__next`, this module verifies it, swaps it in, and only then drops
the previous generation. A build that fails, is killed or is refused leaves the previous database and its coverage
report untouched, so it reads STALE (an answer about an older world, labelled as one), never MISSING.

THE NAMES. `<db>__next` is created only by a builder, after `x4basex_artifacts.py --declare-db <db>__next` wrote its
producer receipt. `<db>__old` is created only by `swap()`'s own ALTER, which writes its receipt first. Nothing here
drops a reserved name without its receipt, and nothing here touches any other database.

THE FILES (all beside the jar, in the BaseX home):
    coverage-<db>.json          describes <db>, the database in place. ask.py reads ONLY this one.
    coverage-<db>__next.json    the build's report on <db>__next, stamped with the pre-build fingerprint
    coverage-<db>__old.json     the previous report, parked while <db> is renamed to <db>__old
    swap-<db>.json              the journal: a swap is in progress (phase), so a crash is recovered, never guessed
    build-<db>.lock             the build lock: ONE builder or dbswap call per database at a time (below)
At every moment coverage-<db>.json either describes the database named <db> or is absent. It is parked BEFORE the
live database is renamed and written AFTER the next one takes its name, so an absent report (UNKNOWN: ask.py
certifies no negative) covers the gap and never a report about the other generation.

THE BASEX FACTS this rests on (MEASURED on BaseX 12.4, private DBPATH; see BLIND-SPOTS F336 and the 4.1.1 row):
  * ALTER DB and DROP DB of a database another process has open are refused, rc 1, "Database 'x' is opened by
    another process.", and change nothing; the reader finishes with its own result.
  * ALTER DB onto a name that already exists is NOT refused when nobody holds the target: the target is replaced.
    So every ALTER here first checks that its target is absent, or it could destroy a generation.
  * ALTER/DROP of an absent database: ALTER rc 1 "not found"; DROP rc 0 (so DROP's rc proves nothing: existence
    is re-checked after it).

THE LOCK. Two builders of one database would race: a second `prepare` -> `recover` could undo the first's swap
mid-flight, a rollback could race a commit (both pass the target-absent check, and BaseX's ALTER then REPLACES one
generation with the other), `prepare` could drop the first's `<db>__next` between CREATE and coverage, and both
share `pending-fingerprint-<db>.json`. So `build-<db>.lock` is created with O_EXCL and names its owner's pid and
creation time. A builder takes it before `prepare` (`dbswap.py lock --owner <its pid>`) and releases it after the
swap; every `prepare`/`swap`/`recover` call checks it, and a bare CLI call takes it for itself. A lock whose owner
is not running is STALE (a killed build): it is taken over, and the takeover is reported. "Not running" is decided
by the process AND its start time, so a reused pid does not keep a dead build's lock alive.

DISK. The swap holds two generations at once. `prepare()` refuses a build that would leave less than the floor
free (system drive: min(pagefile, 50 GiB); other drives: min(5%, 50 GiB)), with the numbers in the message. There
is deliberately NO drop-first fallback: that fallback is the defect this module exists to remove, and a silent one
would bring it back exactly when the disk is short. Free space or move DBPATH, then rebuild.

CLI (run from tools/x4validate with `uv run python ../basex/dbswap.py ...`):
    lock    --db x4raw --owner PID   take the build lock for a builder process      (rc 0, 7 held by a live build)
    unlock  --db x4raw --owner PID   release it, only if PID holds it
    prepare --db x4raw   recover, check disk, drop a leftover receipted <db>__next     (rc 0 ok, 2 refused, 7 busy)
    swap    --db x4raw   verify <db>__next and swap it in                             (rc 0, 2 verify, 7 locked)
    recover --db x4raw   finish or roll back an interrupted swap                       (rc 0, 7 locked)
    status  --db x4raw   read-only: describe leftovers, interruptions and a running build
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASEX_DIR = HERE / "basex"
DBS = ("x4raw", "x4eff")
NEXT, OLD = "__next", "__old"
GIB = 2 ** 30
#: Staging/serialized-tree working set beside the new generation; preflight's measured figure.
STAGING_BYTES = 3 * GIB
#: A FIRST build has no previous generation to measure, so its size is ESTIMATED: tools/basex/.gitignore records
#: basex/data at 2.8 GB for a built corpus (both databases; README: 942 MB x4raw + 949 MB x4eff, 2026-08-24).
#: One database is budgeted at that whole figure, rounded up, so the estimate errs towards refusing.
FIRST_BUILD_BYTES = 3 * GIB
#: rc for a refusal that is retryable as is (a reader holds a database): distinct from 2 (fix something first).
RC_LOCKED = 7
_LOCKED_RE = re.compile(r"opened by another process|is being updated|locked", re.I)


class SwapRefused(RuntimeError):
    """Nothing was changed (or everything changed was put back). `kind`: verify | locked | disk | unknown."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _check_db(db: str) -> None:
    if db not in DBS:
        raise ValueError(f"not a toolkit-owned BaseX database: {db!r} (only {', '.join(DBS)})")


class Files:
    def __init__(self, home: Path, db: str):
        _check_db(db)
        self.home, self.db = Path(home), db
        self.next, self.old = db + NEXT, db + OLD
        self.cov = self.home / f"coverage-{db}.json"
        self.cov_next = self.home / f"coverage-{self.next}.json"
        self.cov_old = self.home / f"coverage-{self.old}.json"
        self.journal = self.home / f"swap-{db}.json"
        self.lock = self.home / f"build-{db}.lock"
        self.pending = self.home / f"pending-fingerprint-{db}.json"

    def receipt(self, name: str) -> Path:
        return self.home / f"disposition-{name}.json"


def _write_json(path: Path, data: dict) -> None:
    """Encode first, then write a sibling and replace: a failed write never truncates the report in place."""
    raw = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _phase(f: Files) -> str | None:
    j = _read_json(f.journal)
    return j.get("phase") if j else ("unreadable" if f.journal.exists() else None)


def _set_phase(f: Files, phase: str) -> None:
    _write_json(f.journal, {"db": f.db, "phase": phase, "pid": os.getpid(), "at": time.time()})


def _receipt_value(path: Path) -> dict:
    # The same shape x4basex_artifacts.receipt() writes, so either tool reads the other's receipts.
    return {"v": 1, "kind": "database", "path": str(path), "producer": "x4-basex",
            "disposition": "rebuildable cache; permanent disposal"}


def _receipted(f: Files, name: str) -> bool:
    r = _read_json(f.receipt(name))
    return bool(r) and r.get("kind") == "database" and r.get("producer") == "x4-basex" \
        and isinstance(r.get("path"), str) and Path(r["path"]).name == name


def _write_receipt(f: Files, bx, name: str) -> None:
    _write_json(f.receipt(name), _receipt_value(bx.path_of(name) if hasattr(bx, "path_of")
                                                else f.home / "data" / name))


def _move(src: Path, dst: Path) -> None:
    if src.exists():
        os.replace(src, dst)


def _locked(name: str, msg: str, what: str, retry: str) -> SwapRefused:
    return SwapRefused("locked", (
        f"{what}: BaseX refused ({msg.strip() or 'no message'}). Another process holds '{name}' open -- an ask.py or "
        f"coverage query, a BaseX GUI or server. Nothing was swapped; the previous database and its coverage are in "
        f"place. Retry once that reader finishes: {retry}"))


# ---------------------------------------------------------------- the build lock

def _process_started(pid: int) -> float | None:
    """The process's start time (epoch seconds) if it is running, `0.0` if it is running but its start time cannot
    be read, None if it is not running. Never signals the process (os.kill on Windows TERMINATES it)."""
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            # 5 = access denied: the process EXISTS (another user's); anything else (87) = no such process
            return 0.0 if ctypes.get_last_error() == 5 else None
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *[ctypes.byref(t) for t in times]):
                return 0.0
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return ticks / 1e7 - 11644473600.0              # FILETIME (1601, 100 ns) -> Unix epoch
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return 0.0
    try:   # Linux: start time in clock ticks since boot -> epoch
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        boot = next(float(ln.split()[1]) for ln in Path("/proc/stat").read_text().splitlines()
                    if ln.startswith("btime "))
        return boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return 0.0


def owner_running(pid, since) -> bool:
    """Is the process that wrote a lock or journal at `since` still running? A pid whose current process started
    AFTER `since` (2 s slack) is a reused pid: the writer is gone."""
    try:
        pid, since = int(pid), float(since)
    except (TypeError, ValueError):
        return False
    started = _process_started(pid)
    if started is None:
        return False
    return started == 0.0 or started <= since + 2.0


def _lock_holder(f: Files) -> dict | None:
    return _read_json(f.lock)


def take_lock(home: Path, db: str, owner: int) -> list[str]:
    """Hold `build-<db>.lock` for `owner`, or refuse. Returns notes (a stale takeover is one).

    Already held by `owner`: fine (a builder's later steps). Held by a running process: SwapRefused("busy").
    Held by a process that is not running, or unreadable for over a minute: taken over, and said."""
    f = Files(home, db)
    notes: list[str] = []
    for _attempt in range(3):
        try:
            fd = os.open(f.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = _lock_holder(f)
            if held is not None and held.get("pid") == owner:
                return notes
            if held is not None and owner_running(held.get("pid"), held.get("at")):
                raise SwapRefused("busy", (
                    f"another build of {db} is running (pid {held.get('pid')}, lock {f.lock.name}); nothing was "
                    f"touched. Retry once it finishes"))
            if held is None:
                try:
                    age = time.time() - f.lock.stat().st_mtime
                except OSError:
                    continue
                if age < 60:   # being written right now by another taker
                    raise SwapRefused("busy", f"{f.lock.name} is being written by another process; retry")
            # STALE: move it aside under a name only we use, then confirm we moved the one we judged.
            aside = f.lock.with_name(f"{f.lock.name}.stale-{os.getpid()}")
            try:
                os.replace(f.lock, aside)
            except OSError:
                continue
            moved = _read_json(aside)
            if moved != held:   # someone replaced it between our read and our move: put theirs back
                try:
                    os.rename(aside, f.lock)
                except OSError:
                    pass   # silent-ok: theirs is gone only if a third process already took a newer lock
                raise SwapRefused("busy", f"{f.lock.name} changed hands while being taken over; retry")
            aside.unlink(missing_ok=True)
            notes.append(f"{db}: took over a stale build lock left by pid {(held or {}).get('pid', '?')} "
                         f"(not running: a killed or crashed build)")
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"db": db, "pid": owner, "at": time.time()}, fh)
        return notes
    raise SwapRefused("busy", f"could not take {f.lock.name}; retry")


def running_build(home: Path, db: str) -> int | None:
    """The pid of a RUNNING process holding `build-<db>.lock`, or None (no lock, or a stale one the next build takes
    over). Read-only."""
    held = _lock_holder(Files(home, db))
    if held is not None and owner_running(held.get("pid"), held.get("at")):
        return held.get("pid")
    return None


def release_lock(home: Path, db: str, owner: int) -> bool:
    """Remove the lock only if `owner` holds it. Returns whether it did."""
    f = Files(home, db)
    held = _lock_holder(f)
    if held is not None and held.get("pid") == owner:
        f.lock.unlink(missing_ok=True)
        return True
    return False


# ---------------------------------------------------------------- verification

def verify(bx, home: Path, db: str) -> dict:
    """Refuse unless <db>__next is a finished, stamped build whose report describes it. Returns its report."""
    f = Files(home, db)

    def no(why):
        raise SwapRefused("verify", f"{f.next} is not swapped in: {why}. The previous {db} is untouched.")

    if not bx.exists(f.next):
        no(f"there is no {f.next} database (the build did not create it)")
    if not _receipted(f, f.next):
        no(f"{f.receipt(f.next).name} is missing or invalid, so this tool did not create {f.next}")
    if f.pending.exists():
        no(f"{f.pending.name} still exists: the fingerprint stamp never ran, so the report has no 'as of'")
    report = _read_json(f.cov_next)
    if report is None:
        no(f"no readable {f.cov_next.name} (coverage never reconciled: the build died or was refused)")
    if report.get("db") != f.next:
        no(f"{f.cov_next.name} describes {report.get('db')!r}, not {f.next}")
    fp = report.get("fingerprint")
    if not isinstance(fp, dict) or not fp.get("content") or not fp.get("engine"):
        no(f"{f.cov_next.name} carries no complete fingerprint, so its world is unknown")
    if "status" not in report:
        no(f"{f.cov_next.name} has no coverage status")
    try:
        expected = int(report["indexed"]["total"])
    except (KeyError, TypeError, ValueError):
        no(f"{f.cov_next.name} has no indexed document total")
    try:
        counted = bx.count(f.next)
    except (RuntimeError, OSError) as exc:
        no(f"could not count its documents ({exc})")
    if counted <= 0:
        no("it holds no documents")
    if counted != expected:
        no(f"it holds {counted} documents but its coverage report says {expected}")
    return report


# ---------------------------------------------------------------- the swap

def swap(bx, home: Path, db: str) -> list[str]:
    """Verify <db>__next, then rename it into place and drop the previous generation."""
    f = Files(home, db)
    lines = recover(bx, home, db)
    report = verify(bx, home, db)
    if bx.exists(f.old):
        raise SwapRefused("unknown", f"{f.old} exists and no swap journal accounts for it; it is kept. "
                                     "Drop it by hand once you know what it is, then retry the swap")
    _set_phase(f, "begun")              # from here a crash is recovered from the journal, never guessed
    had_live = bx.exists(db)
    if had_live:
        _write_receipt(f, bx, f.old)    # creation receipt for the generation this rename makes
        _move(f.cov, f.cov_old)         # park the live report BEFORE the rename: absent, never mismatched
        ok, msg = bx.alter(db, f.old)
        if not ok or bx.exists(db):
            _move(f.cov_old, f.cov)
            f.receipt(f.old).unlink(missing_ok=True)
            f.journal.unlink(missing_ok=True)
            if _LOCKED_RE.search(msg):
                raise _locked(db, msg, f"cannot move {db} aside",
                              f"(cd tools/x4validate && uv run python ../basex/dbswap.py swap --db {db})")
            raise SwapRefused("verify", f"cannot move {db} aside: BaseX said {msg.strip()!r}; nothing was swapped")
    _set_phase(f, "live-moved")
    lines += _commit(bx, f, report)
    return lines


def _commit(bx, f: Files, report: dict | None = None) -> list[str]:
    """live-moved -> next-moved -> committed -> done. Each step is idempotent, so recovery re-enters here."""
    if not bx.exists(f.db):
        ok, msg = bx.alter(f.next, f.db)
        if not ok or not bx.exists(f.db):
            return _rollback(bx, f, f"cannot rename {f.next} to {f.db}: BaseX said {msg.strip()!r}",
                             "locked" if _LOCKED_RE.search(msg) else "verify")
    elif bx.exists(f.next) and f.cov_next.exists():
        # The live name is taken while the next generation is still here: its report would be written over a
        # database it does not describe. Nothing in the swap produces this, so it is not decided here.
        raise SwapRefused("unknown", f"{f.db} and {f.next} both exist during an interrupted swap; refusing to "
                                     f"guess which one {f.cov_next.name} describes. Inspect them, then remove "
                                     f"{f.journal.name} by hand")
    _set_phase(f, "next-moved")
    report = report if report is not None else _read_json(f.cov_next)
    if report is not None:
        report = dict(report, db=f.db)          # ask.py ignores a report naming another database
        _write_json(f.cov, report)
        f.cov_next.unlink(missing_ok=True)
    _write_receipt(f, bx, f.db)
    # <db>__next no longer exists: its receipt must not outlive it, or a later database of that name that this
    # tool did not create would pass _receipted() and be dropped.
    f.receipt(f.next).unlink(missing_ok=True)
    _set_phase(f, "committed")
    return _drop_old(bx, f)


def _drop_old(bx, f: Files) -> list[str]:
    lines = []
    if bx.exists(f.old) and not _receipted(f, f.old):
        return [f"{f.db}: swapped in; {f.old} was NOT dropped: {f.receipt(f.old).name} is missing or invalid, so "
                f"this tool cannot show it made it. Drop it by hand once you know what it is"]
    if bx.exists(f.old):
        ok, msg = bx.drop(f.old)
        if not ok or bx.exists(f.old):
            return [f"{f.db}: swapped in; the previous generation {f.old} was not dropped (BaseX said "
                    f"{msg.strip()!r}). It is dropped by the next build or by `dbswap.py recover --db {f.db}`"]
        lines.append(f"{f.db}: previous generation dropped")
    f.cov_old.unlink(missing_ok=True)
    f.receipt(f.old).unlink(missing_ok=True)
    f.journal.unlink(missing_ok=True)
    return lines


def _rollback(bx, f: Files, why: str, kind: str = "verify") -> list[str]:
    """The next generation could not take the name: put the previous one back, or say plainly it could not.
    `kind` is what the caller is told once the previous generation is back: `locked` (a reader held the next
    generation; the same swap can simply be retried) or `verify`."""
    if bx.exists(f.old) and not bx.exists(f.db):
        ok, msg = bx.alter(f.old, f.db)
        if ok and bx.exists(f.db):
            _move(f.cov_old, f.cov)
            f.receipt(f.old).unlink(missing_ok=True)
            f.journal.unlink(missing_ok=True)
            raise SwapRefused(kind, f"{why}; the previous {f.db} was restored, nothing is swapped"
                              + ("; retry the swap once that reader finishes" if kind == "locked" else ""))
        raise SwapRefused("locked" if _LOCKED_RE.search(msg) else "unknown",
                          f"{why}; restoring the previous {f.db} from {f.old} ALSO failed ({msg.strip()!r}). "
                          f"{f.db} is MISSING until `dbswap.py recover --db {f.db}` succeeds")
    f.journal.unlink(missing_ok=True)
    raise SwapRefused("unknown", f"{why}; there is no previous generation to restore")


# ---------------------------------------------------------------- recovery

def recover(bx, home: Path, db: str) -> list[str]:
    """Finish or undo an interrupted swap, decided by the journal plus which databases exist.

    `begun`       the live database may or may not have been renamed: if it still has its name, un-park its report
                  and stop (nothing was swapped); if it is gone, continue as `live-moved`.
    `live-moved`  <db>__next was verified before the journal existed, so go FORWARD: rename it into place. If it is
                  gone too, restore <db>__old.
    `next-moved` / `committed`  write the report for the new generation (if still parked) and drop the old one.
    No journal: a <db>__old is not ours to judge (kept, reported); a <db>__next is left for the next build.
    """
    f = Files(home, db)
    phase = _phase(f)
    if phase is None:
        return []
    if phase == "unreadable":
        raise SwapRefused("unknown", f"{f.journal} is unreadable; a swap was interrupted and its state cannot be "
                                     "decided. Inspect the databases and remove the journal by hand")
    j = _read_json(f.journal) or {}
    if j.get("pid") != os.getpid() and owner_running(j.get("pid"), j.get("at")):
        raise SwapRefused("busy", f"a swap of {db} is in progress (pid {j.get('pid')}, journal {f.journal.name}); "
                                  "nothing was touched. Retry once it finishes")
    if phase == "begun":
        if bx.exists(db):
            _move(f.cov_old, f.cov)
            f.receipt(f.old).unlink(missing_ok=True)
            f.journal.unlink(missing_ok=True)
            return [f"{db}: an interrupted swap had not moved {db}; nothing changed"]
        phase = "live-moved"
    if phase == "live-moved":
        if not bx.exists(db) and not bx.exists(f.next):
            return _rollback_or_raise(bx, f)
        return [f"{db}: completing an interrupted swap"] + _commit(bx, f)
    if phase in ("next-moved", "committed"):
        return [f"{db}: completing an interrupted swap"] + _commit(bx, f)
    raise SwapRefused("unknown", f"{f.journal} names an unknown phase {phase!r}")


def _rollback_or_raise(bx, f: Files) -> list[str]:
    try:
        _rollback(bx, f, f"an interrupted swap lost {f.next}")
    except SwapRefused as exc:
        if exc.kind == "verify":            # restored: the normal outcome here
            return [str(exc)]
        raise
    return []


# ---------------------------------------------------------------- before a build

def disk_floor_bytes(*, total: int, pagefile: int | None, system_drive: bool) -> int:
    """The free space a job must leave: system drive min(pagefile, 50 GiB), else min(5% of the drive, 50 GiB)."""
    cap = 50 * GIB
    if system_drive and pagefile:
        return min(pagefile, cap)
    return min(total * 5 // 100, cap)


def _floor_for(where: Path) -> tuple[int, str]:
    """(floor bytes, how it was derived). `where` is resolved first: a junctioned DBPATH lives on its TARGET's drive,
    which is the drive whose free space and floor rule apply."""
    real = Path(os.path.realpath(where))
    usage = shutil.disk_usage(real)
    anchor = real.anchor
    sysdrive = os.environ.get("SystemDrive", "")  # env-ok: Windows' own system-drive name, not a configured location
    system = bool(sysdrive) and anchor.lower().startswith(sysdrive.lower())
    pagefile = None
    if system:
        try:
            pagefile = os.stat(Path(anchor) / "pagefile.sys").st_size
        except OSError:
            pagefile = None   # silent-ok: said in the returned rule, which the refusal prints
    floor = disk_floor_bytes(total=usage.total, pagefile=pagefile, system_drive=system)
    if system and pagefile:
        rule = f"system drive {anchor}: min(pagefile, 50 GiB)"
    elif system:
        rule = f"system drive {anchor}, pagefile size UNKNOWN: min(5% of the drive, 50 GiB) used instead"
    else:
        rule = f"drive {anchor}: min(5% of the drive, 50 GiB)"
    return floor, rule


def disk_shortfall(bx, home: Path, db: str, *, free_bytes: int | None = None,
                   floor_bytes: int | None = None) -> str | None:
    """Why a build of `db` would not fit beside the previous generation, or None. READ-ONLY: no recovery, no drop, no
    BaseX command (sizes are read from the files), so an unattended caller can ask before starting a build. `prepare`
    refuses on exactly this answer."""
    f = Files(home, db)
    if bx.exists(db):
        size, basis = bx.size_bytes(db), f"the size of the current {db}"
    else:
        size, basis = FIRST_BUILD_BYTES, (f"an ESTIMATE of {FIRST_BUILD_BYTES / GIB:.0f} GiB: there is no previous "
                                          f"{db} to measure")
    need = size + STAGING_BYTES
    where = bx.data_dir() if hasattr(bx, "data_dir") else f.home
    where = Path(os.path.realpath(where if Path(where).exists() else Path(where).parent))
    rule = "floor given by the caller"
    if free_bytes is None:
        free_bytes = shutil.disk_usage(where).free
    if floor_bytes is None:
        floor_bytes, rule = _floor_for(where)
    # the leftover next generation is about to be dropped, so its space counts as free
    reclaim = bx.size_bytes(f.next) if bx.exists(f.next) else 0
    if free_bytes + reclaim - need < floor_bytes:
        return (f"not enough disk to build {db} beside the previous one: {free_bytes / GIB:.1f} GiB free on {where}, "
                f"the new generation needs about {need / GIB:.1f} GiB ({basis}, plus {STAGING_BYTES / GIB:.0f} GiB of "
                f"staging), and {floor_bytes / GIB:.1f} GiB must stay free ({rule}). Nothing was dropped or built; the "
                f"previous {db} stays in place. Free space (or move BaseX's DBPATH) and rebuild. There is no drop-first "
                f"fallback on purpose: it is what left the databases MISSING")
    return None


def prepare(bx, home: Path, db: str, *, free_bytes: int | None = None, floor_bytes: int | None = None) -> list[str]:
    """Run before a build reads anything: recover, check disk, clear a leftover receipted <db>__next."""
    f = Files(home, db)
    lines = recover(bx, home, db)
    if bx.exists(f.next) and not _receipted(f, f.next):
        raise SwapRefused("unknown", f"a database named {f.next} exists without this tool's receipt "
                                     f"({f.receipt(f.next).name}); it is kept and nothing was built. Drop it by "
                                     "hand once you know what it is")
    short = disk_shortfall(bx, home, db, free_bytes=free_bytes, floor_bytes=floor_bytes)
    if short:
        raise SwapRefused("disk", short)
    if bx.exists(f.next):
        ok, msg = bx.drop(f.next)
        if not ok or bx.exists(f.next):
            if _LOCKED_RE.search(msg):
                raise _locked(f.next, msg, f"cannot clear the leftover {f.next}", "the same build")
            raise SwapRefused("unknown", f"cannot drop the leftover {f.next}: BaseX said {msg.strip()!r}")
        lines.append(f"{db}: dropped the leftover {f.next} from an unfinished build")
    f.cov_next.unlink(missing_ok=True)
    return lines


# ---------------------------------------------------------------- read-only status

def describe(bx, home: Path, db: str) -> list[str]:
    """What status should say about generations and interruptions. Reads only."""
    f = Files(home, db)
    out = []
    held = _lock_holder(f)
    if held is not None:
        out.append(f"a build of {db} is running (pid {held.get('pid')})" if owner_running(held.get("pid"), held.get("at"))
                   else f"{f.lock.name} is left by pid {held.get('pid')}, which is not running; the next build takes it over")
    phase = _phase(f)
    if phase:
        out.append(f"a swap of {db} was interrupted (journal phase {phase!r}); the next build recovers it, or run "
                   f"`uv run python ../basex/dbswap.py recover --db {db}` from tools/x4validate")
    if bx.exists(f.old) and phase is None:
        out.append(f"{f.old} exists with no swap journal: kept, not judged; drop it by hand once you know what it is")
    elif bx.exists(f.old):
        out.append(f"the previous generation {f.old} is still on disk and is dropped by recovery")
    if bx.exists(f.next) and phase is None:
        stamped = (_read_json(f.cov_next) or {}).get("fingerprint")
        out.append(f"{f.next} is a finished build that was not swapped in (retry: dbswap.py swap --db {db})"
                   if stamped else
                   f"{f.next} is left from an unfinished build; the next build drops it")
    return out


# ---------------------------------------------------------------- real BaseX

def _preflight():
    spec = importlib.util.spec_from_file_location("dbswap_preflight", HERE / "preflight.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class BaseXRunner:
    """BaseX in its home directory, one JVM per command, the database path read the way the builders read it."""

    def __init__(self, home: Path = BASEX_DIR):
        self.home = Path(home)

    def data_dir(self) -> Path:
        d = _preflight()._dbpath(self.home)
        return d if d.is_absolute() else self.home / d

    def path_of(self, name: str) -> Path:
        return self.data_dir() / name

    def exists(self, name: str) -> bool:
        try:
            return any(self.path_of(name).iterdir())
        except OSError:
            return False   # silent-ok: an absent directory is an absent database

    def size_bytes(self, name: str) -> int:
        total = 0
        for dp, _dirs, files in os.walk(self.path_of(name)):
            for n in files:
                try:
                    total += os.lstat(os.path.join(dp, n)).st_size
                except OSError:
                    pass   # silent-ok: an estimate for the disk check, which also keeps a margin
        return total

    @staticmethod
    def _run():
        """basex_run, by FILE: the ONE way this toolkit starts the jar (test_7 bans a hand-rolled one)."""
        spec = importlib.util.spec_from_file_location("dbswap_basex_run", HERE / "basex_run.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def info(self) -> str:
        return self._run().run_command("INFO", self.home)[1]

    def alter(self, src: str, dst: str) -> tuple[bool, str]:
        if self.exists(dst):   # MEASURED: BaseX REPLACES an existing unopened target instead of refusing
            return False, f"Database '{dst}' already exists (refused here: BaseX would replace it)."
        rc, out = self._run().run_command(f"ALTER DB {src} {dst}", self.home)
        return rc == 0, out

    def drop(self, name: str) -> tuple[bool, str]:
        rc, out = self._run().run_command(f"DROP DB {name}", self.home)
        return rc == 0, out

    def _query(self, xquery: str) -> str:
        rc, out, err = self._run().run_query(xquery, self.home)
        if rc != 0:
            text = (err or out).strip()
            raise RuntimeError(text.splitlines()[-1] if text else f"BaseX rc {rc}")
        return out

    def count(self, name: str) -> int:
        return int(self._query(f"count(collection('{name}'))").strip())

    def create_for_test(self, name: str, docs: int) -> None:
        body = ", ".join(f"<d n='{i}'/>" for i in range(docs))
        paths = ", ".join(f"'d{i}.xml'" for i in range(docs))
        self._query(f"db:create('{name}', ({body}), ({paths}))")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dbswap", description="Build-then-swap for x4raw/x4eff.")
    p.add_argument("action", choices=("lock", "unlock", "prepare", "swap", "recover", "status"))
    p.add_argument("--db", required=True, choices=DBS)
    p.add_argument("--home", default=str(BASEX_DIR), help="the BaseX home (default: tools/basex/basex)")
    p.add_argument("--owner", type=int, default=None,
                   help="the pid of the builder holding build-<db>.lock (the builders pass their own); without it "
                        "a prepare/swap/recover call takes the lock for itself and releases it when done")
    args = p.parse_args(argv)
    home = Path(args.home)
    bx = BaseXRunner(home)
    if args.action in ("lock", "unlock") and args.owner is None:
        p.error(f"{args.action} needs --owner")
    owner = args.owner if args.owner is not None else os.getpid()
    own_call = args.owner is None and args.action in ("prepare", "swap", "recover")
    lines: list[str] = []
    try:
        if args.action == "status":
            lines = describe(bx, home, args.db) or [f"{args.db}: no swap leftovers"]
        elif args.action == "unlock":
            lines = [f"{args.db}: build lock released" if release_lock(home, args.db, owner)
                     else f"{args.db}: build lock not held by pid {owner}; left as it is"]
        else:
            lines = take_lock(home, args.db, owner)
            if args.action == "lock":
                lines.append(f"{args.db}: build lock held by pid {owner}")
            else:
                try:
                    lines += {"prepare": prepare, "swap": swap, "recover": recover}[args.action](bx, home, args.db)
                finally:
                    if own_call:
                        release_lock(home, args.db, owner)
                if args.action == "swap":
                    lines.append(f"{args.db}: SWAPPED IN the new generation")
    except SwapRefused as exc:
        for ln in lines:
            print(f"  {ln}")
        print(f"REFUSED ({exc.kind}): {exc}", file=sys.stderr)
        return RC_LOCKED if exc.kind in ("locked", "busy") else 2
    for ln in lines:
        print(f"  {ln}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
