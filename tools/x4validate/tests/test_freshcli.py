"""x4fresh: one verdict per derived artifact, and an unattended rebuild that cannot lie about what it did."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from x4validate import _freshcli as fc


@pytest.fixture
def sd(tmp_path, monkeypatch):
    d = tmp_path / "state"
    monkeypatch.setattr(fc, "state_dir", lambda: d)
    monkeypatch.delenv("X4_JOB_WRAPPER", raising=False)
    monkeypatch.delenv("X4_FRESH_AUTO", raising=False)
    return d


# --------------------------------------------------------------------------- status

def test_a_missing_store_is_MISSING_and_its_orphans_are_listed(tmp_path, monkeypatch):
    db = tmp_path / "effective.sqlite"
    (tmp_path / "effective.sqlite.4242.tmp").write_bytes(b"x")
    monkeypatch.setattr(fc._effective, "effective_db", lambda: db)
    s = fc._status_effective()
    assert s.state == "MISSING"
    assert [p.name for p in s.orphans] == ["effective.sqlite.4242.tmp"]


def test_a_store_with_no_fingerprint_is_UNKNOWN_never_fresh(tmp_path, monkeypatch):
    db = tmp_path / "effective.sqlite"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
                      "INSERT INTO meta VALUES ('dropped_overlays','2'),"
                      "('dropped_overlay_samples','[\"a/b.xml: why\", \"c/d.xml: why\"]');")
    con.commit()
    con.close()
    monkeypatch.setattr(fc._effective, "effective_db", lambda: db)
    s = fc._status_effective()
    assert s.state == "UNKNOWN", s
    assert s.notes[0].startswith("2 overlay(s) could not be read")
    assert s.notes[1:] == ["  - a/b.xml", "  - c/d.xml"]


def test_an_unconfigured_store_is_UNKNOWN(monkeypatch):
    monkeypatch.setattr(fc._effective, "effective_db", lambda: None)
    assert fc._status_effective().state == "UNKNOWN"


def test_render_puts_the_directive_first_and_names_every_non_fresh_artifact():
    out = fc.render([fc.Status("effective", "FRESH"), fc.Status("xref", "STALE", ["moved"]),
                     fc.Status("x4eff", "MISSING", ["none"])]).splitlines()
    assert out[0] == "x4fresh: NOT CURRENT: xref STALE, x4eff MISSING -- run `x4fresh rebuild`"
    assert fc.render([fc.Status("effective", "FRESH")]).splitlines()[0] == "x4fresh: all 1 artifact(s) FRESH"


def test_status_exit_code_is_0_only_when_everything_is_fresh(monkeypatch, sd, capsys):
    monkeypatch.setattr(fc, "CHECKS", {n: (lambda n=n: fc.Status(n, "FRESH")) for n in fc.ORDER})
    assert fc.main(["status"]) == 0
    monkeypatch.setitem(fc.CHECKS, "x4raw", lambda: fc.Status("x4raw", "UNKNOWN", ["?"]))
    assert fc.main(["status"]) == 5


# --------------------------------------------------------------------------- the lock

HOLD = ("import sys,time; sys.path.insert(0, sys.argv[2]); from x4validate._freshcli import BuildLock;"
        "from pathlib import Path; l=BuildLock(Path(sys.argv[1])); assert l.acquire(); print('held', flush=True);"
        "time.sleep(60)")


def test_the_lock_refuses_while_held_and_frees_when_the_holder_is_killed(tmp_path):
    lock = tmp_path / "x4fresh.lock"
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(lock), str(fc.PKG_ROOT)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert fc.BuildLock(lock).held_elsewhere() is True        # the control: must refuse
        assert fc.BuildLock(lock).acquire() is False
    finally:
        holder.kill()   # a hard kill: no finally, no unlock in the holder
        holder.wait()
    deadline = time.time() + 2
    while fc.BuildLock(lock).held_elsewhere() and time.time() < deadline:
        time.sleep(0.02)
    assert fc.BuildLock(lock).held_elsewhere() is False


def test_a_second_rebuild_while_locked_starts_nothing(sd, monkeypatch, capsys):
    held = fc.BuildLock(sd / "x4fresh.lock")
    assert held.acquire()
    monkeypatch.setattr(fc, "_run", lambda *a: pytest.fail("a builder ran while the lock was held"))
    try:
        assert fc.rebuild() == 0
    finally:
        held.release()
    assert "already running" in capsys.readouterr().out


# --------------------------------------------------------------------------- rebuild

def _world(monkeypatch, before: dict, after: dict):
    """Fake per-artifact checks: `before` until that artifact is built, `after` from then on."""
    built: set[str] = set()

    def check(name):
        return lambda: (after if name in built else before)[name]()
    monkeypatch.setattr(fc, "CHECKS", {n: check(n) for n in fc.ORDER})
    monkeypatch.setattr(fc, "_deferred", lambda auto: None)
    return built


def _fake_builder(monkeypatch, built: set, code: str):
    def builder(name):
        built.add(name)
        return [sys.executable, "-c", code]
    monkeypatch.setattr(fc, "builder", builder)


FRESH = lambda n: (lambda: fc.Status(n, "FRESH"))                       # noqa: E731
STALE = lambda n: (lambda: fc.Status(n, "STALE", [f"{n} moved [a -> b]"]))  # noqa: E731


def test_a_build_that_dropped_input_is_reported_as_such_never_clean(sd, monkeypatch, capsys):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    after = dict(before, effective=lambda: fc.Status("effective", "FRESH",
                                                    notes=["1 overlay(s) could not be read", "  - x.xml"]))
    built = _world(monkeypatch, before, after)
    _fake_builder(monkeypatch, built, "print('  WARNING: 1 overlay(s) could not be read')")
    assert fc.rebuild(("effective",)) == 0
    out = capsys.readouterr().out
    assert "effective: BUILT WITH 1 DROPS" in out
    assert "WARNING: 1 overlay(s) could not be read" in out
    assert "REBUILT, FRESH" not in out


def test_a_build_that_leaves_the_artifact_stale_is_a_failure(sd, monkeypatch, capsys):
    before = {n: (STALE(n) if n == "xref" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)      # still stale afterwards
    _fake_builder(monkeypatch, built, "pass")        # and exits 0: the claim is checked, not assumed
    assert fc.rebuild(("xref",)) == 1
    assert "xref: FAILED rc 0, now STALE" in capsys.readouterr().out


def test_auto_never_retries_a_failure_against_the_same_inputs(sd, monkeypatch, capsys):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "import sys; sys.exit(1)")
    assert fc.rebuild(auto=True) == 1
    built.clear()
    capsys.readouterr()
    assert fc.rebuild(auto=True) == 0
    assert not built, "the same failure was retried unattended"
    assert "NOT RETRIED" in capsys.readouterr().out
    assert "NOT RETRIED" in (sd / "session-note.txt").read_text(encoding="utf-8")


def test_auto_rebuilds_only_effective_and_xref_and_says_what_it_left(sd, monkeypatch, capsys):
    before = {n: STALE(n) for n in fc.ORDER}
    after = {n: (FRESH(n) if n in fc.AUTO_SET else STALE(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, after)
    _fake_builder(monkeypatch, built, "pass")
    assert fc.rebuild(auto=True) == 0
    assert built == {"effective", "xref"}
    out = capsys.readouterr().out
    assert "not rebuilt unattended" in out
    # The final status runs while THIS process holds the lock; that must not read as another rebuild.
    # (MEASURED 2026-11-01 end to end: the report said "rebuild in progress: x4raw, x4eff".)
    assert "REBUILDING" not in out and "rebuild in progress" not in out
    assert out.splitlines()[0] == "x4fresh: NOT CURRENT: x4raw STALE, x4eff STALE -- run `x4fresh rebuild`"


@pytest.mark.parametrize("why", ["deferring while running: x4.exe", "could not list running processes"])
def test_auto_defers_and_builds_nothing(sd, monkeypatch, capsys, why):
    before = {n: STALE(n) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    monkeypatch.setattr(fc, "_deferred", lambda auto: why)
    _fake_builder(monkeypatch, built, "pass")
    assert fc.rebuild(auto=True) == 0
    assert not built
    assert f"DEFERRED, nothing rebuilt: {why}" in (sd / "session-note.txt").read_text(encoding="utf-8")


def test_deferral_checks_each_clause(monkeypatch):
    """One falsification twin per clause: each guard alone must defer."""
    monkeypatch.setattr(fc._mutation, "active", lambda: None)
    monkeypatch.setattr(fc, "_dirty_engine_sources", lambda: [])
    monkeypatch.setattr(fc, "_running_deferring_processes", lambda: [])
    assert fc._deferred(True) is None
    monkeypatch.setattr(fc, "_running_deferring_processes", lambda: ["x4.exe"])
    assert "x4.exe" in fc._deferred(True)
    assert fc._deferred(False) is None, "a process list must not block a rebuild the user asked for"
    monkeypatch.setattr(fc, "_running_deferring_processes", lambda: None)
    assert "could not list" in fc._deferred(True)
    monkeypatch.setattr(fc, "_running_deferring_processes", lambda: [])
    monkeypatch.setattr(fc, "_dirty_engine_sources", lambda: ["_merge.py"])
    assert "_merge.py" in fc._deferred(False)
    monkeypatch.setattr(fc, "_dirty_engine_sources", lambda: [])
    monkeypatch.setattr(fc._mutation, "active", lambda: Path("marker"))
    assert "mutation" in fc._deferred(False)


def test_auto_off_does_nothing(sd, monkeypatch):
    monkeypatch.setenv("X4_FRESH_AUTO", "off")
    monkeypatch.setattr(fc, "status", lambda *a: pytest.fail("ran with X4_FRESH_AUTO=off"))
    assert fc.rebuild(auto=True) == 0


def test_the_wrapper_prefixes_the_builder_argv_exactly(monkeypatch):
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps(["rb", "run", "--name", "{name}", "--"]))
    assert fc._wrapper("xref") == ["rb", "run", "--name", "x4fresh-xref", "--"]
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps("rb run"))
    with pytest.raises(ValueError):
        fc._wrapper("xref")
    monkeypatch.delenv("X4_JOB_WRAPPER")
    assert fc._wrapper("xref") == []


def test_logs_are_pruned_to_the_newest_five_per_artifact(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(8):
        (logs / f"effective-2026110{i}-000000.log").write_text("x")
    (logs / "xref-20261101-000000.log").write_text("x")
    fc._prune_logs(logs)
    assert sorted(p.name for p in logs.glob("effective-*")) == [f"effective-2026110{i}-000000.log"
                                                               for i in range(3, 8)]
    assert (logs / "xref-20261101-000000.log").exists()
