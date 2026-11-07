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

DISK. The swap holds two generations at once. `prepare()` refuses a build that would leave less than the floor
free (system drive: min(pagefile, 50 GiB); other drives: min(5%, 50 GiB)), with the numbers in the message. There
is deliberately NO drop-first fallback: that fallback is the defect this module exists to remove, and a silent one
would bring it back exactly when the disk is short. Free space or move DBPATH, then rebuild.

CLI (run from tools/x4validate with `uv run python ../basex/dbswap.py ...`):
    prepare --db x4raw   recover, check disk, drop a leftover receipted <db>__next     (rc 0 ok, 2 refused)
    swap    --db x4raw   verify <db>__next and swap it in                             (rc 0, 2 verify, 7 locked)
    recover --db x4raw   finish or roll back an interrupted swap                       (rc 0, 7 locked)
    status  --db x4raw   read-only: describe leftovers and interruptions
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASEX_DIR = HERE / "basex"
DBS = ("x4raw", "x4eff")
NEXT, OLD = "__next", "__old"
GIB = 2 ** 30
#: Staging/serialized-tree working set beside the new generation; preflight's measured figure.
STAGING_BYTES = 3 * GIB
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
    _write_json(f.journal, {"db": f.db, "phase": phase, "pid": os.getpid()})


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


def _locked(name: str, msg: str, what: str) -> SwapRefused:
    return SwapRefused("locked", (
        f"{what}: BaseX refused ({msg.strip() or 'no message'}). Another process holds '{name}' open -- an ask.py or "
        f"coverage query, a BaseX GUI or server. Nothing was swapped; the previous database and its coverage are in "
        f"place. Retry once that reader finishes: uv run python ../basex/dbswap.py swap --db <db>"))


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
                raise _locked(db, msg, f"cannot move {db} aside")
            raise SwapRefused("verify", f"cannot move {db} aside: BaseX said {msg.strip()!r}; nothing was swapped")
    _set_phase(f, "live-moved")
    lines += _commit(bx, f, report)
    return lines


def _commit(bx, f: Files, report: dict | None = None) -> list[str]:
    """live-moved -> next-moved -> committed -> done. Each step is idempotent, so recovery re-enters here."""
    if not bx.exists(f.db):
        ok, msg = bx.alter(f.next, f.db)
        if not ok or not bx.exists(f.db):
            return _rollback(bx, f, f"cannot rename {f.next} to {f.db}: BaseX said {msg.strip()!r}")
    _set_phase(f, "next-moved")
    report = report if report is not None else _read_json(f.cov_next)
    if report is not None:
        report = dict(report, db=f.db)          # ask.py ignores a report naming another database
        _write_json(f.cov, report)
        f.cov_next.unlink(missing_ok=True)
    _write_receipt(f, bx, f.db)
    _set_phase(f, "committed")
    return _drop_old(bx, f)


def _drop_old(bx, f: Files) -> list[str]:
    lines = []
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


def _rollback(bx, f: Files, why: str) -> list[str]:
    """The next generation could not take the name: put the previous one back, or say plainly it could not."""
    if bx.exists(f.old) and not bx.exists(f.db):
        ok, msg = bx.alter(f.old, f.db)
        if ok and bx.exists(f.db):
            _move(f.cov_old, f.cov)
            f.receipt(f.old).unlink(missing_ok=True)
            f.journal.unlink(missing_ok=True)
            raise SwapRefused("verify", f"{why}; the previous {f.db} was restored")
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


def _floor_for(where: Path) -> int:
    usage = shutil.disk_usage(where)
    anchor = Path(os.path.abspath(where)).anchor
    sysdrive = os.environ.get("SystemDrive", "")  # env-ok: Windows' own system-drive name, not a configured location
    system = bool(sysdrive) and anchor.lower().startswith(sysdrive.lower())
    pagefile = None
    if system:
        try:
            pagefile = os.stat(Path(anchor) / "pagefile.sys").st_size
        except OSError:
            pagefile = None   # silent-ok: unknown pagefile falls back to the 5% rule, said in the refusal
    return disk_floor_bytes(total=usage.total, pagefile=pagefile, system_drive=system)


def prepare(bx, home: Path, db: str, *, free_bytes: int | None = None, floor_bytes: int | None = None) -> list[str]:
    """Run before a build reads anything: recover, check disk, clear a leftover receipted <db>__next."""
    f = Files(home, db)
    lines = recover(bx, home, db)
    if bx.exists(f.next) and not _receipted(f, f.next):
        raise SwapRefused("unknown", f"a database named {f.next} exists without this tool's receipt "
                                     f"({f.receipt(f.next).name}); it is kept and nothing was built. Drop it by "
                                     "hand once you know what it is")
    need = (bx.size_bytes(db) or 0) + STAGING_BYTES
    where = bx.data_dir() if hasattr(bx, "data_dir") else f.home
    if free_bytes is None:
        free_bytes = shutil.disk_usage(where).free
    if floor_bytes is None:
        floor_bytes = _floor_for(where)
    # the leftover next generation is about to be dropped, so its space counts as free
    reclaim = bx.size_bytes(f.next) if bx.exists(f.next) else 0
    if free_bytes + reclaim - need < floor_bytes:
        raise SwapRefused("disk", (
            f"not enough disk to build {db} beside the previous one: {free_bytes / GIB:.1f} GiB free, the new "
            f"generation needs about {need / GIB:.1f} GiB (the current {db} plus {STAGING_BYTES / GIB:.0f} GiB of "
            f"staging), and {floor_bytes / GIB:.1f} GiB must stay free. Nothing was dropped or built; the previous "
            f"{db} stays in place. Free space (or move BaseX's DBPATH) and rebuild. There is no drop-first fallback "
            f"on purpose: it is what left the databases MISSING"))
    if bx.exists(f.next):
        ok, msg = bx.drop(f.next)
        if not ok or bx.exists(f.next):
            if _LOCKED_RE.search(msg):
                raise _locked(f.next, msg, f"cannot clear the leftover {f.next}")
            raise SwapRefused("unknown", f"cannot drop the leftover {f.next}: BaseX said {msg.strip()!r}")
        lines.append(f"{db}: dropped the leftover {f.next} from an unfinished build")
    f.cov_next.unlink(missing_ok=True)
    return lines


# ---------------------------------------------------------------- read-only status

def describe(bx, home: Path, db: str) -> list[str]:
    """What status should say about generations and interruptions. Reads only."""
    f = Files(home, db)
    out = []
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

    def _cmd(self, *args: str) -> tuple[int, str]:
        p = subprocess.run(["java", "-cp", "BaseX.jar", "org.basex.BaseX", *args], cwd=self.home,
                           capture_output=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (p.stdout + p.stderr).decode("utf-8", errors="replace").replace("\r\n", "\n")
        return p.returncode, text

    def info(self) -> str:
        return self._cmd("-c", "INFO")[1]

    def alter(self, src: str, dst: str) -> tuple[bool, str]:
        if self.exists(dst):   # MEASURED: BaseX REPLACES an existing unopened target instead of refusing
            return False, f"Database '{dst}' already exists (refused here: BaseX would replace it)."
        rc, out = self._cmd("-c", f"ALTER DB {src} {dst}")
        return rc == 0, out

    def drop(self, name: str) -> tuple[bool, str]:
        rc, out = self._cmd("-c", f"DROP DB {name}")
        return rc == 0, out

    def count(self, name: str) -> int:
        spec = importlib.util.spec_from_file_location("dbswap_basex_run", HERE / "basex_run.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rc, out, err = mod.run_query(f"count(collection('{name}'))", self.home)
        if rc != 0:
            raise RuntimeError((err or out).strip().splitlines()[-1] if (err or out).strip() else f"rc {rc}")
        return int(out.strip())

    def create_for_test(self, name: str, docs: int) -> None:
        body = ", ".join(f"<d n='{i}'/>" for i in range(docs))
        paths = ", ".join(f"'d{i}.xml'" for i in range(docs))
        rc, out = self._cmd(f"db:create('{name}', ({body}), ({paths}))")
        if rc != 0:
            raise RuntimeError(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dbswap", description="Build-then-swap for x4raw/x4eff.")
    p.add_argument("action", choices=("prepare", "swap", "recover", "status"))
    p.add_argument("--db", required=True, choices=DBS)
    p.add_argument("--home", default=str(BASEX_DIR), help="the BaseX home (default: tools/basex/basex)")
    args = p.parse_args(argv)
    bx = BaseXRunner(Path(args.home))
    try:
        if args.action == "status":
            lines = describe(bx, Path(args.home), args.db) or [f"{args.db}: no swap leftovers"]
        else:
            lines = {"prepare": prepare, "swap": swap, "recover": recover}[args.action](bx, Path(args.home), args.db)
            if args.action == "swap":
                lines.append(f"{args.db}: SWAPPED IN the new generation")
    except SwapRefused as exc:
        print(f"REFUSED ({exc.kind}): {exc}", file=sys.stderr)
        return RC_LOCKED if exc.kind == "locked" else 2
    for ln in lines:
        print(f"  {ln}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
