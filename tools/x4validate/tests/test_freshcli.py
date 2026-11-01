"""x4fresh: one verdict per derived artifact, and an unattended rebuild that cannot lie about what it did."""
from __future__ import annotations

import json
import os
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
    after = dict(before, effective=lambda: fc.Status("effective", "FRESH", incomplete=1,
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


# --------------------------------------------------------------------------- review 2026-11-01 findings

def _store(tmp_path, **meta):
    db = tmp_path / "effective.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    con.commit()
    con.close()
    return db


def test_whole_files_the_build_skipped_count_as_missing(tmp_path, monkeypatch):
    """Finding 1: a build that could not merge whole files said nothing a reader could see later."""
    db = _store(tmp_path, skipped_files=2, skipped_file_samples='["t/0001-l044.xml: bad", "x.xml: bad"]')
    monkeypatch.setattr(fc._effective, "effective_db", lambda: db)
    s = fc._status_effective()
    assert s.incomplete == 2
    assert s.notes[0].startswith("2 file(s) could not be merged at all")
    assert "  - t/0001-l044.xml" in s.notes
    assert fc.WARN_RE.search("  skipped 2 unparseable file(s); e.g. t/0001-l044.xml")


def test_the_drop_count_is_the_stores_own_not_the_sample_lines(tmp_path, monkeypatch):
    """Finding 3: 37 drops with 10 stored samples read 'BUILT WITH 10 DROPS'."""
    db = _store(tmp_path, dropped_overlays=37,
                dropped_overlay_samples=json_list([f"o{i}.xml: why" for i in range(10)]))
    monkeypatch.setattr(fc._effective, "effective_db", lambda: db)
    s = fc._status_effective()
    assert s.incomplete == 37
    assert sum(1 for n in s.notes if n.startswith("  - ")) == fc.MAX_LISTED


def json_list(items):
    return json.dumps(items)


def test_fresh_but_incomplete_is_never_reported_clean(monkeypatch, sd, capsys):
    monkeypatch.setattr(fc, "CHECKS", {n: (lambda n=n: fc.Status(n, "FRESH", incomplete=(8 if n == "effective" else 0)))
                                       for n in fc.ORDER})
    assert fc.main(["status"]) == 3
    head = capsys.readouterr().out.splitlines()[0]
    assert head == "x4fresh: all 4 artifact(s) FRESH, but INCOMPLETE: effective (8 missing)"


def test_auto_puts_a_drop_in_the_session_note(sd, monkeypatch):
    """Finding 2: 'BUILT WITH N DROPS' went only to stdout, which the session launcher discards."""
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    after = dict(before, effective=lambda: fc.Status("effective", "FRESH", incomplete=3))
    built = _world(monkeypatch, before, after)
    _fake_builder(monkeypatch, built, "pass")
    assert fc.rebuild(auto=True) == 0
    text = (sd / "session-note.txt").read_text(encoding="utf-8")
    assert "effective: BUILT WITH 3 DROPS" in text


def test_a_check_that_raises_is_UNKNOWN_not_a_traceback(monkeypatch, sd, capsys):
    """Finding 5."""
    def boom():
        raise OSError("disk went away")
    monkeypatch.setattr(fc, "CHECKS", dict({n: (lambda n=n: fc.Status(n, "FRESH")) for n in fc.ORDER}, xref=boom))
    assert fc.main(["status"]) == 5
    assert "xref       UNKNOWN" in capsys.readouterr().out


def test_a_checkout_whose_git_fails_defers(monkeypatch, tmp_path):
    """Finding 4: every git failure used to read as 'no uncommitted engine changes'."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(fc, "TOOLKIT_ROOT", tmp_path)

    def fails(*a, **k):
        return subprocess.CompletedProcess(a, 128, "", "fatal: detected dubious ownership")
    monkeypatch.setattr(fc.subprocess, "run", fails)
    assert fc._dirty_engine_sources() == ["<git could not check: rc 128>"]
    (tmp_path / ".git").rmdir()                         # twin: an installed toolkit is legitimately clean
    assert fc._dirty_engine_sources() == []


def test_while_a_rebuild_runs_only_its_targets_read_REBUILDING(sd, monkeypatch):
    """Finding 7: an --auto rebuild never touches x4eff, so a stale x4eff must still say STALE."""
    monkeypatch.setattr(fc, "CHECKS", {n: STALE(n) for n in fc.ORDER})
    held = fc.BuildLock(sd / "x4fresh.lock")
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(sd / "x4fresh.lock"), str(fc.PKG_ROOT)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        (sd / "rebuilding.json").write_text(json.dumps({"targets": ["effective", "xref"]}), encoding="utf-8")
        states = {s.name: s.state for s in fc.status()}
        assert states == {"effective": "REBUILDING", "xref": "REBUILDING", "x4raw": "STALE", "x4eff": "STALE"}
        (sd / "rebuilding.json").unlink()                # twin: unknown targets -> all non-fresh in play
        assert {s.state for s in fc.status()} == {"REBUILDING"}
    finally:
        holder.kill()
        holder.wait()
    del held


CHILD_TREE = ("import sys,subprocess,time; sys.path.insert(0, sys.argv[1]); from x4validate._freshcli import _ChildTree;"
              "t=_ChildTree(); p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], **t.popen_kwargs());"
              "assert t.adopt(p); print(p.pid, flush=True); time.sleep(60)")


@pytest.mark.skipif(not (os.name == "nt" or sys.platform.startswith("linux")), reason="no child-tie on this OS")
def test_the_builder_dies_with_x4fresh(tmp_path):
    """Finding 6: a killed x4fresh freed the lock while its builder kept running."""
    parent = subprocess.Popen([sys.executable, "-c", CHILD_TREE, str(fc.PKG_ROOT)], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    assert _alive(child), "the control: the builder must be running before the kill"
    parent.kill()
    parent.wait()
    deadline = time.time() + 5
    while _alive(child) and time.time() < deadline:
        time.sleep(0.1)
    assert not _alive(child), "the builder outlived x4fresh"


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
        return f'"{pid}"' in out
    return Path(f"/proc/{pid}").exists() and "Z" not in Path(f"/proc/{pid}/stat").read_text().split()[2]


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
