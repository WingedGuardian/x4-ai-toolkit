"""Build-then-swap for the BaseX databases (4.1.1): a failed build never leaves x4raw/x4eff MISSING.

MEASURED 2026-11-05: eight builds in a row were killed by a job-wrapper memory cap. The builders dropped the
database FIRST, so each kill left x4raw and x4eff missing for hours and every `ask.py` query refused. These tests
drive `dbswap` against a FAKE BaseX (an in-memory set of databases with BaseX's measured refusals: ALTER/DROP of a
database another process has open is refused, ALTER onto an existing name is refused) and a real temp directory
for the coverage, journal and receipt files. The real-BaseX check is `test_real_alter_semantics` at the end.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbswap  # noqa: E402

FP = {"content": "c0ffee", "engine": "e1"}


class Killed(BaseException):
    """A kill between two steps: nothing after it runs, no finally-cleanup in our code may 'fix' it."""


class FakeBaseX:
    def __init__(self, dbs=None):
        self.dbs = dict(dbs or {})   # name -> document count
        self.locked: set[str] = set()
        self.calls: list[str] = []
        self.kill_before: str | None = None   # a call text: the process dies before running it

    def _maybe_die(self, call):
        self.calls.append(call)
        if self.kill_before == call:
            raise Killed(call)

    def exists(self, name):
        return name in self.dbs

    def alter(self, src, dst):
        self._maybe_die(f"ALTER DB {src} {dst}")
        if src not in self.dbs:
            return False, f"Database '{src}' was not found."
        if src in self.locked:
            return False, f"Database '{src}' is opened by another process."
        if dst in self.locked:
            return False, f"Database '{src}' could not be renamed."   # MEASURED: and the target is damaged
        # MEASURED (BaseX 12.4): an existing, unopened target is REPLACED, not refused. The fake does the same,
        # so a swap that renamed onto a live name would lose a generation here exactly as it would for real.
        self.dbs[dst] = self.dbs.pop(src)
        return True, ""

    def drop(self, name):
        self._maybe_die(f"DROP DB {name}")
        if name in self.locked:
            return False, f"Database '{name}' is opened by another process."
        self.dbs.pop(name, None)
        return True, ""

    def count(self, name):
        self._maybe_die(f"COUNT {name}")
        if name not in self.dbs:
            raise RuntimeError(f"Database '{name}' was not found.")
        return self.dbs[name]

    def size_bytes(self, name):
        return self.dbs.get(name, 0) * 1000


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "basex"
    h.mkdir()
    return h


def cov(home, name, *, db=None, total=10, fingerprint=FP, status="complete"):
    data = {"db": db or name, "indexed": {"total": total}, "status": status,
            "supports_negative_claim": status == "complete"}
    if fingerprint is not None:
        data["fingerprint"] = fingerprint
    p = home / f"coverage-{name}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def receipt(home, name):
    """The producer's creation receipt, as x4basex_artifacts --declare-db writes it before CREATE DB."""
    p = home / f"disposition-{name}.json"
    p.write_text(json.dumps({"v": 1, "kind": "database", "path": str(home / "data" / name),
                             "producer": "x4-basex",
                             "disposition": "rebuildable cache; permanent disposal"}) + "\n", encoding="utf-8")
    return p


def live_cov(home, db="x4raw"):
    p = home / f"coverage-{db}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def built(home, db="x4raw", live_total=5, next_total=10):
    """A live generation (old world) plus a finished, stamped next generation."""
    bx = FakeBaseX({db: live_total, f"{db}__next": next_total})
    cov(home, db, total=live_total, fingerprint={"content": "old", "engine": "old"})
    cov(home, f"{db}__next", total=next_total)
    receipt(home, f"{db}__next")
    return bx


# ---------------------------------------------------------------- success

def test_success_swaps_in_the_new_generation_and_drops_the_old(home):
    bx = built(home)
    dbswap.swap(bx, home, "x4raw")
    assert bx.dbs == {"x4raw": 10}
    c = live_cov(home)
    assert c["indexed"]["total"] == 10 and c["fingerprint"] == FP
    assert c["db"] == "x4raw", "ask.py ignores a coverage report naming another database"
    assert not (home / "coverage-x4raw__next.json").exists()
    assert not (home / "coverage-x4raw__old.json").exists()
    assert not (home / "swap-x4raw.json").exists()
    assert "DROP DB x4raw__old" in bx.calls


def test_first_build_with_no_live_database_swaps_without_an_old_generation(home):
    bx = FakeBaseX({"x4eff__next": 7})
    cov(home, "x4eff__next", total=7)
    receipt(home, "x4eff__next")
    dbswap.swap(bx, home, "x4eff")
    assert bx.dbs == {"x4eff": 7} and live_cov(home, "x4eff")["db"] == "x4eff"


def test_the_live_database_gets_a_creation_receipt_at_the_swap(home):
    bx = built(home)
    dbswap.swap(bx, home, "x4raw")
    r = json.loads((home / "disposition-x4raw.json").read_text(encoding="utf-8"))
    assert r["kind"] == "database" and Path(r["path"]).name == "x4raw"


# ---------------------------------------------------------------- build failure / kill

def test_build_failure_leaves_the_old_database_and_its_coverage(home):
    """A build that died never reaches swap: the next generation has no stamped coverage."""
    bx = FakeBaseX({"x4raw": 5, "x4raw__next": 3})
    old = cov(home, "x4raw", total=5, fingerprint={"content": "old", "engine": "old"}).read_bytes()
    receipt(home, "x4raw__next")
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.swap(bx, home, "x4raw")
    assert exc.value.kind == "verify"
    assert bx.dbs["x4raw"] == 5 and (home / "coverage-x4raw.json").read_bytes() == old
    assert not any(c.startswith("ALTER") for c in bx.calls)


def test_killed_mid_build_is_reported_and_cleared_by_the_next_prepare(home):
    bx = FakeBaseX({"x4raw": 5, "x4raw__next": 2})   # killed during CREATE/ADD: no coverage, no stamp
    cov(home, "x4raw", total=5)
    receipt(home, "x4raw__next")
    lines = dbswap.describe(bx, home, "x4raw")
    assert any("unfinished build" in ln for ln in lines), lines
    dbswap.prepare(bx, home, "x4raw", free_bytes=10**12, floor_bytes=0)
    assert bx.dbs == {"x4raw": 5}
    assert live_cov(home)["indexed"]["total"] == 5


def test_prepare_keeps_an_unreceipted_next_database(home):
    """A database with a reserved name but no producer receipt is not ours to drop."""
    bx = FakeBaseX({"x4raw": 5, "x4raw__next": 2})
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.prepare(bx, home, "x4raw", free_bytes=10**12, floor_bytes=0)
    assert exc.value.kind == "unknown"
    assert bx.dbs == {"x4raw": 5, "x4raw__next": 2}


# ---------------------------------------------------------------- verification failure

@pytest.mark.parametrize("damage", ["no-coverage", "no-fingerprint", "count-mismatch", "zero-docs",
                                    "wrong-db-field", "no-next-db", "pending-fingerprint"])
def test_verification_failure_swaps_nothing(home, damage):
    bx = built(home)
    nxt = home / "coverage-x4raw__next.json"
    if damage == "no-coverage":
        nxt.unlink()
    elif damage == "no-fingerprint":
        cov(home, "x4raw__next", fingerprint=None)
    elif damage == "count-mismatch":
        bx.dbs["x4raw__next"] = 9
    elif damage == "zero-docs":
        bx.dbs["x4raw__next"] = 0
        cov(home, "x4raw__next", total=0)
    elif damage == "wrong-db-field":
        cov(home, "x4raw__next", db="x4eff__next")
    elif damage == "no-next-db":
        del bx.dbs["x4raw__next"]
    elif damage == "pending-fingerprint":
        # the stamp step consumes the pre-build snapshot; a snapshot still lying there means it never ran
        (home / "pending-fingerprint-x4raw.json").write_text(json.dumps({"db": "x4raw", "fingerprint": FP}))
    before = live_cov(home)
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.swap(bx, home, "x4raw")
    assert exc.value.kind == "verify", exc.value
    assert bx.dbs.get("x4raw") == 5 and live_cov(home) == before
    assert not any(c.startswith("ALTER") for c in bx.calls)


# ---------------------------------------------------------------- lock

def test_a_reader_on_the_live_database_blocks_the_swap_cleanly(home):
    bx = built(home)
    bx.locked.add("x4raw")
    before = live_cov(home)
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.swap(bx, home, "x4raw")
    assert exc.value.kind == "locked"
    assert "opened by another process" in str(exc.value) and "x4raw" in str(exc.value)
    assert "retry" in str(exc.value).lower()
    assert bx.dbs == {"x4raw": 5, "x4raw__next": 10}, "never half-swapped"
    assert live_cov(home) == before and (home / "coverage-x4raw__next.json").is_file()
    assert not (home / "swap-x4raw.json").exists()
    bx.locked.clear()                  # the reader finished: the same call succeeds
    dbswap.swap(bx, home, "x4raw")
    assert bx.dbs == {"x4raw": 10} and live_cov(home)["fingerprint"] == FP


def test_a_reader_on_the_next_generation_puts_the_old_one_back(home):
    """MEASURED shape A8: the live database is already renamed aside when the second rename is refused. The swap
    must restore it, under its own report, and say the refusal is retryable."""
    bx = built(home)
    bx.locked.add("x4raw__next")
    before = live_cov(home)
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.swap(bx, home, "x4raw")
    assert exc.value.kind == "locked" and "restored" in str(exc.value)
    assert bx.dbs == {"x4raw": 5, "x4raw__next": 10}
    assert live_cov(home) == before and not (home / "swap-x4raw.json").exists()


def test_recovery_refuses_when_both_live_and_next_exist_mid_swap(home):
    """Nothing in the swap makes this state; writing the next report over the live database would mislabel it."""
    bx = built(home)
    (home / "swap-x4raw.json").write_text(json.dumps({"db": "x4raw", "phase": "next-moved"}), encoding="utf-8")
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.recover(bx, home, "x4raw")
    assert exc.value.kind == "unknown"
    assert live_cov(home)["indexed"]["total"] == 5


def test_a_reader_on_the_old_generation_leaves_it_for_recovery(home):
    bx = built(home)
    real_alter = bx.alter
    def alter(src, dst):
        ok, msg = real_alter(src, dst)
        if ok and dst == "x4raw__old":
            bx.locked.add("x4raw__old")   # someone opened the old name in between
        return ok, msg
    bx.alter = alter
    lines = dbswap.swap(bx, home, "x4raw")
    assert bx.dbs == {"x4raw": 10, "x4raw__old": 5}
    assert live_cov(home)["fingerprint"] == FP
    assert any("x4raw__old" in ln and "not dropped" in ln for ln in lines), lines
    assert any("x4raw__old" in ln for ln in dbswap.describe(bx, home, "x4raw"))
    bx.locked.clear()
    dbswap.recover(bx, home, "x4raw")
    assert bx.dbs == {"x4raw": 10} and not (home / "swap-x4raw.json").exists()


# ---------------------------------------------------------------- crash between steps

@pytest.mark.parametrize("kill", ["ALTER DB x4raw x4raw__old", "ALTER DB x4raw__next x4raw",
                                  "DROP DB x4raw__old"])
def test_a_kill_at_any_step_recovers_to_one_consistent_generation(home, kill):
    bx = built(home)
    bx.kill_before = kill
    with pytest.raises(Killed):
        dbswap.swap(bx, home, "x4raw")
    bx.kill_before = None
    status = dbswap.describe(bx, home, "x4raw")
    assert any("interrupted" in ln for ln in status), status
    dbswap.recover(bx, home, "x4raw")
    assert not (home / "swap-x4raw.json").exists()
    c = live_cov(home)
    assert "x4raw" in bx.dbs and set(bx.dbs) <= {"x4raw", "x4raw__next"}
    # Whatever generation is live, the coverage file describes THAT one, never the other.
    assert c["indexed"]["total"] == bx.dbs["x4raw"]
    assert c["fingerprint"] == (FP if bx.dbs["x4raw"] == 10 else {"content": "old", "engine": "old"})


def test_crash_between_the_renames_completes_forward(home):
    """x4raw absent, x4raw__old and x4raw__next present: the next generation was VERIFIED before the journal
    was written, so recovery finishes the swap rather than throwing that build away."""
    bx = built(home)
    bx.kill_before = "ALTER DB x4raw__next x4raw"
    with pytest.raises(Killed):
        dbswap.swap(bx, home, "x4raw")
    bx.kill_before = None
    assert "x4raw" not in bx.dbs and live_cov(home) is None, "no report may describe an absent database"
    dbswap.recover(bx, home, "x4raw")
    assert bx.dbs == {"x4raw": 10} and live_cov(home)["fingerprint"] == FP


def test_crash_between_renames_with_next_lost_restores_the_old_generation(home):
    bx = built(home)
    bx.kill_before = "ALTER DB x4raw__next x4raw"
    with pytest.raises(Killed):
        dbswap.swap(bx, home, "x4raw")
    bx.kill_before = None
    del bx.dbs["x4raw__next"]
    dbswap.recover(bx, home, "x4raw")
    assert bx.dbs == {"x4raw": 5}
    assert live_cov(home)["fingerprint"] == {"content": "old", "engine": "old"}


def test_an_old_generation_without_a_journal_is_not_dropped(home):
    bx = FakeBaseX({"x4raw": 5, "x4raw__old": 4})
    dbswap.recover(bx, home, "x4raw")
    assert bx.dbs == {"x4raw": 5, "x4raw__old": 4}
    assert any("x4raw__old" in ln for ln in dbswap.describe(bx, home, "x4raw"))


def test_an_unaccounted_old_generation_blocks_the_swap_and_survives_it(home):
    """MEASURED: BaseX REPLACES an existing target, so renaming the live database onto a stray x4raw__old would
    destroy whatever that is. The swap refuses instead, and touches neither database."""
    bx = built(home)
    bx.dbs["x4raw__old"] = 4
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.swap(bx, home, "x4raw")
    assert exc.value.kind == "unknown" and "x4raw__old" in str(exc.value)
    assert bx.dbs == {"x4raw": 5, "x4raw__next": 10, "x4raw__old": 4}
    assert live_cov(home)["indexed"]["total"] == 5


def test_live_missing_after_interruption_is_reported_with_the_remedy(home):
    bx = FakeBaseX({"x4raw__old": 5, "x4raw__next": 10})
    (home / "swap-x4raw.json").write_text(json.dumps({"db": "x4raw", "phase": "begun"}), encoding="utf-8")
    lines = dbswap.describe(bx, home, "x4raw")
    assert any("interrupted" in ln and "recover" in ln for ln in lines), lines


# ---------------------------------------------------------------- disk

def test_insufficient_disk_refuses_before_anything_is_dropped(home):
    bx = FakeBaseX({"x4raw": 5_000_000, "x4raw__next": 2})   # live is ~5 GB in the fake
    receipt(home, "x4raw__next")
    with pytest.raises(dbswap.SwapRefused) as exc:
        dbswap.prepare(bx, home, "x4raw", free_bytes=6 * 2**30, floor_bytes=2 * 2**30)
    assert exc.value.kind == "disk"
    assert "GiB" in str(exc.value) and "previous" in str(exc.value)
    assert bx.dbs == {"x4raw": 5_000_000, "x4raw__next": 2}, "a refusal touches nothing"
    assert bx.calls == []


def test_enough_disk_passes(home):
    bx = FakeBaseX({"x4raw": 1000})
    dbswap.prepare(bx, home, "x4raw", free_bytes=100 * 2**30, floor_bytes=20 * 2**30)


def test_disk_floor_follows_the_machine_policy(tmp_path):
    gib = 2**30
    assert dbswap.disk_floor_bytes(total=1000 * gib, pagefile=20 * gib, system_drive=True) == 20 * gib
    assert dbswap.disk_floor_bytes(total=1000 * gib, pagefile=80 * gib, system_drive=True) == 50 * gib
    assert dbswap.disk_floor_bytes(total=100 * gib, pagefile=None, system_drive=True) == 5 * gib
    assert dbswap.disk_floor_bytes(total=2000 * gib, pagefile=None, system_drive=False) == 50 * gib


# ---------------------------------------------------------------- names

def test_only_toolkit_databases_are_handled(home):
    with pytest.raises(ValueError):
        dbswap.swap(FakeBaseX(), home, "userdb")


# ---------------------------------------------------------------- real BaseX (skips without Java)

def test_real_alter_semantics(tmp_path):
    """The two BaseX behaviours the swap rests on, on the vendored jar in a private DBPATH."""
    jar = Path(dbswap.__file__).parent / "basex" / "BaseX.jar"
    if not jar.is_file() or not shutil.which("java"):
        pytest.skip("needs the vendored jar and Java")
    home = tmp_path / "basex"
    home.mkdir()
    shutil.copyfile(jar, home / "BaseX.jar")
    (home / ".basexhome").write_bytes(b"")
    (home / ".basex").write_text(f"DBPATH = {(home / 'data').as_posix()}\n", encoding="utf-8")
    bx = dbswap.BaseXRunner(home)
    assert (home / "data").as_posix().lower() in bx.info().replace("\\", "/").lower()
    bx.create_for_test("x4raw", 2)
    bx.create_for_test("x4raw__next", 3)
    receipt(home, "x4raw__next")
    cov(home, "x4raw", total=2)
    cov(home, "x4raw__next", total=3)
    q = home / "hold.xq"
    q.write_text("count(collection('x4raw')), prof:sleep(8000)", encoding="utf-8")
    reader = subprocess.Popen(["java", "-cp", "BaseX.jar", "org.basex.BaseX", q.name], cwd=home,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        import time
        time.sleep(3)
        with pytest.raises(dbswap.SwapRefused) as exc:
            dbswap.swap(bx, home, "x4raw")
        assert exc.value.kind == "locked", exc.value
        assert bx.exists("x4raw") and bx.exists("x4raw__next") and not bx.exists("x4raw__old")
    finally:
        reader.communicate(timeout=60)
    dbswap.swap(bx, home, "x4raw")
    assert bx.exists("x4raw") and not bx.exists("x4raw__next") and not bx.exists("x4raw__old")
    assert bx.count("x4raw") == 3
