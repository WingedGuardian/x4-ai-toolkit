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
    monkeypatch.delenv("X4_FRESH_BASEX_QUIET_MIN", raising=False)
    # Off unless a test turns it on: no test may start a real background BaseX build.
    monkeypatch.setenv("X4_FRESH_BASEX_AUTO", "off")
    monkeypatch.setattr(fc, "_spawn_basex", lambda note: pytest.fail("a real background BaseX build was started"))
    monkeypatch.setattr(fc, "_world_digest", lambda: "world")
    # No test reads the real BaseX home for a waiting swap; the tests of that read point it at a private one.
    monkeypatch.setattr(fc, "_basex_swap_pending", lambda db: False)
    return d


# --------------------------------------------------------------------------- status

@pytest.fixture(autouse=True)
def _no_stub_basex_module_left_behind():
    """These tests point x4fresh at stub BaseX scripts, and importing one leaves it in sys.modules. A later test in
    the same process then got the stub: `module 'staleness' has no attribute 'Verdict'`, 14 failures in the pre-push
    gate, whose order differs from CI's alphabetical one."""
    names = ("staleness", "preflight")
    modules = {n: sys.modules.get(n) for n in names}
    path = list(sys.path)
    yield
    sys.path[:] = path
    for name, module in modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


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


@pytest.mark.parametrize("cmd", [["status"], ["rebuild"], ["rebuild", "--auto"], ["accept"]])
def test_an_unconfigured_machine_refuses_every_subcommand_with_rc_2(monkeypatch, capsys, cmd):
    """rc 2 = "not set up", the contract every toolkit CLI keeps. `status` once answered rc 5 with four
    UNKNOWNs here instead; a CHECK that is called would have been the wrong thing to stub."""
    monkeypatch.setattr(fc, "state_dir", lambda: None)
    called = []
    monkeypatch.setattr(fc, "CHECKS", {n: (lambda n=n: called.append(n) or fc.Status(n, "FRESH")) for n in fc.ORDER})
    assert fc.main(cmd) == 2
    assert called == []
    assert "not configured" in capsys.readouterr().err


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
        assert fc.rebuild() == 5, "nothing was rebuilt, so a by-hand rebuild must not read as success"
        assert fc.rebuild(auto=True) == 0, "--auto: the running build owns the outcome and the note"
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


def test_a_build_that_leaves_the_artifact_stale_is_not_called_clean_and_not_called_failed(sd, monkeypatch, capsys):
    """The builder said rc 0 and the artifact still reads STALE: the claim is checked, not assumed. The docs say exit 1
    is "a builder failed" and 5 "nothing failed but one is still not FRESH", and "FAILED rc 0" was neither."""
    before = {n: (STALE(n) if n == "xref" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)      # still stale afterwards
    _fake_builder(monkeypatch, built, "pass")        # and exits 0
    assert fc.rebuild(("xref",)) == 5
    out = capsys.readouterr().out
    assert "xref: BUILT (rc 0), but now STALE" in out
    assert "FAILED" not in out


def test_a_builder_that_really_failed_is_still_exit_1(sd, monkeypatch, capsys):
    """The twin."""
    before = {n: (STALE(n) if n == "xref" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "import sys; sys.exit(7)")
    assert fc.rebuild(("xref",)) == 1
    assert "xref: FAILED rc 7, now STALE" in capsys.readouterr().out


def test_auto_says_the_same_for_a_clean_build_that_is_not_fresh_and_exits_0(sd, monkeypatch):
    before = {n: (STALE(n) if n == "xref" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "pass")
    assert fc.rebuild(("xref",), auto=True) == 0
    assert "xref: BUILT (rc 0), but now STALE" in (sd / "session-note.txt").read_text(encoding="utf-8")


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
    assert "x4raw, x4eff: not rebuilt automatically: X4_FRESH_BASEX_AUTO=off" in out
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
    assert head.startswith("x4fresh: all 4 artifact(s) FRESH, but INCOMPLETE: effective (8 missing) -- ")
    assert "Review, then `x4fresh accept` if it is known and acceptable" in head


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


# --------------------------------------------------------------------------- accepted losses (user decision 2026-11-01)

EIGHT = [f"dropped assets/structures/defence/macros/tmp/d{i}.xml <- samplemod/assets/.../d{i}.xml" for i in range(8)]


def _lossy(keys):
    return lambda: fc.Status("effective", "FRESH", incomplete=len(keys), loss_keys=list(keys))


def test_accepting_known_losses_quiets_status_but_still_lists_them(sd, monkeypatch, capsys):
    monkeypatch.setattr(fc, "CHECKS", dict({n: FRESH(n) for n in fc.ORDER}, effective=_lossy(EIGHT)))
    assert fc.main(["status"]) == 3                                     # before: incomplete, said so
    assert fc.main(["accept"]) == 0
    capsys.readouterr()
    assert fc.main(["status"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "x4fresh: all 4 artifact(s) FRESH (8 known loss(es), accepted)"
    assert "8 known loss(es) accepted" in out[1]


@pytest.mark.parametrize("change", ["one more", "one fixed"])
def test_any_change_to_an_accepted_loss_set_is_news_again(sd, monkeypatch, capsys, change):
    monkeypatch.setattr(fc, "CHECKS", dict({n: FRESH(n) for n in fc.ORDER}, effective=_lossy(EIGHT)))
    assert fc.main(["accept"]) == 0
    now = EIGHT + ["dropped new.xml <- mod/new.xml"] if change == "one more" else EIGHT[1:]
    monkeypatch.setitem(fc.CHECKS, "effective", _lossy(now))
    capsys.readouterr()
    assert fc.main(["status"]) == 3
    assert "but INCOMPLETE: effective" in capsys.readouterr().out.splitlines()[0]


def test_auto_note_is_silent_for_accepted_losses_and_speaks_for_new_ones(sd, monkeypatch):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=_lossy(EIGHT)))
    _fake_builder(monkeypatch, built, "pass")
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "accepted-losses.json").write_text(json.dumps({"effective": {"keys": sorted(EIGHT), "when": "d"}}))
    assert fc.rebuild(auto=True) == 0
    assert (sd / "session-note.txt").read_text(encoding="utf-8") == ""
    built.clear()
    built2 = _world(monkeypatch, before, dict(before, effective=_lossy(EIGHT + ["dropped x <- y"])))
    _fake_builder(monkeypatch, built2, "pass")
    assert fc.rebuild(auto=True) == 0
    assert "BUILT WITH 9 DROPS" in (sd / "session-note.txt").read_text(encoding="utf-8")


def test_accept_refuses_a_stale_artifact_and_one_that_cannot_list_its_losses(sd, monkeypatch, capsys):
    monkeypatch.setattr(fc, "CHECKS", dict({n: FRESH(n) for n in fc.ORDER}, xref=STALE("xref"),
                                           effective=lambda: fc.Status("effective", "FRESH", incomplete=8)))
    assert fc.main(["accept"]) == 2
    out = capsys.readouterr().out
    assert "xref: NOT accepted: it is STALE" in out
    assert "effective: NOT accepted: this build does not list its losses" in out


def test_the_store_records_every_loss_by_identity_uncapped():
    sc = fc._effective._SkipCount()
    for i in range(25):
        sc.add_overlay(f"a/{i}.xml", [f"samplemod/a/{i}.xml: malformed XML"])
    sc.add("t/0001-l044.xml", ValueError("bad"))
    assert len(sc.dropped_samples) == 10 and len(sc.keys) == 26
    assert "dropped a/3.xml <- samplemod/a/3.xml" in sc.keys and "unmerged t/0001-l044.xml" in sc.keys


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


# --------------------------------------------------------------------------- the world moved during a build

def _moving_world(monkeypatch, *digests):
    """_world_digest answers each of `digests` in turn, then repeats the last."""
    seq = list(digests)
    monkeypatch.setattr(fc, "_world_digest", lambda: seq.pop(0) if len(seq) > 1 else seq[0])


def test_a_build_the_world_moved_under_is_reported_and_is_a_failure(sd, monkeypatch, capsys):
    """The artifact's own stamp reads STALE after the fix; x4fresh must also SAY why, and not read as clean."""
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "pass")
    _moving_world(monkeypatch, "a", "b")
    assert fc.rebuild(("effective",)) == 5, "nothing failed; the artifact is simply not FRESH"
    out = capsys.readouterr().out
    assert "effective: the installed world MOVED during this build [a -> b]" in out
    assert "FAILED" not in out


def test_no_move_no_line_the_control(sd, monkeypatch, capsys):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=FRESH("effective")))
    _fake_builder(monkeypatch, built, "pass")
    assert fc.rebuild(("effective",)) == 0
    assert "MOVED" not in capsys.readouterr().out


def test_a_digest_that_cannot_be_compared_is_said_never_read_as_no_move(sd, monkeypatch, capsys):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=FRESH("effective")))
    _fake_builder(monkeypatch, built, "pass")
    monkeypatch.setattr(fc, "_world_digest", lambda: None)
    assert fc.rebuild(("effective",), auto=True) == 0
    assert "could not be compared" in (sd / "session-note.txt").read_text(encoding="utf-8")


def test_a_basex_stamp_taken_after_a_moved_build_is_revoked(sd, monkeypatch, tmp_path, capsys):
    """BaseX stamps at the END of its build, so it reads FRESH over a moved world. Its readers (ask.py) look
    at the coverage file, not at x4fresh, so the correction has to land THERE."""
    (tmp_path / "bx" / "basex").mkdir(parents=True)
    cov = tmp_path / "bx" / "basex" / "coverage-x4eff.json"
    cov.write_text(json.dumps({"status": "complete", "fingerprint": {"content": "new", "engine": "e"}}), encoding="utf-8")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path / "bx")

    def reads_its_stamp():
        data = json.loads(cov.read_text(encoding="utf-8"))
        ok = data["fingerprint"]["content"] == "new"
        return fc.Status("x4eff", "FRESH") if ok else fc.Status("x4eff", "STALE", ["content changed"])
    built = _world(monkeypatch, {n: FRESH(n) for n in fc.ORDER}, {n: FRESH(n) for n in fc.ORDER})
    monkeypatch.setitem(fc.CHECKS, "x4eff", lambda: fc.Status("x4eff", "STALE", ["old"]) if "x4eff" not in built
                        else reads_its_stamp())
    _fake_builder(monkeypatch, built, "pass")
    _moving_world(monkeypatch, "a", "b")
    assert fc.rebuild(("x4eff",)) == 5
    out = capsys.readouterr().out
    assert "x4eff: BUILT (rc 0), but now STALE" in out and "MOVED" in out
    data = json.loads(cov.read_text(encoding="utf-8"))
    assert data["status"] == "complete", "every other key of the coverage report must survive"
    assert data["fingerprint"]["content"] == fc.MOVED_STAMP


def test_world_digest_is_a_non_answer_when_the_roots_are_unknown(monkeypatch):
    monkeypatch.setattr(fc._merge, "Config", lambda *a, **k: object())
    monkeypatch.setattr(fc._effective, "_ext_root", lambda cfg: None)
    monkeypatch.setattr(fc._freshness, "fingerprint", lambda *a, **k: {"content": None, "engine": "e"})
    assert fc._world_digest() is None
    monkeypatch.setattr(fc._freshness, "fingerprint", lambda *a, **k: {"content": "c", "engine": "e"})
    assert fc._world_digest() == "c/e"

    def boom(*a, **k):
        raise OSError("gone")
    monkeypatch.setattr(fc._freshness, "fingerprint", boom)
    assert fc._world_digest() is None


# --------------------------------------------------------------------------- accepted means EVERY warning is accepted

def _accepted_rebuild(sd, monkeypatch, code):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=_lossy(EIGHT)))
    _fake_builder(monkeypatch, built, code)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "accepted-losses.json").write_text(json.dumps({"effective": {"keys": sorted(EIGHT), "when": "d"}}))
    assert fc.rebuild(auto=True) == 0
    return (sd / "session-note.txt").read_text(encoding="utf-8")


def test_a_new_warning_after_an_accept_reaches_the_note(sd, monkeypatch):
    note = _accepted_rebuild(sd, monkeypatch, "print('  WARNING: a brand new kind of loss')")
    assert "WARNING: a brand new kind of loss" in note
    assert "BUILT WITH 8 DROPS" in note and "accepted d" not in note.splitlines()[1]


@pytest.mark.parametrize("line", ["  WARNING: 8 overlay(s) could not be read and were left OUT of it",
                                  "  skipped 8 unparseable file(s); e.g. t/0001-l044.xml",
                                  "  WARNING: 8 property subtree(s) hit the depth-64 guard and were NOT indexed",
                                  "  NOT INDEXED - 8 file(s) would not parse; any answer excludes them"])
def test_a_warning_that_only_summarises_the_accepted_loss_stays_quiet(sd, monkeypatch, line):
    """Each twin keeps one summary line in the allowed set: without it, an accept could never quiet a build."""
    assert _accepted_rebuild(sd, monkeypatch, f"print({line!r})") == ""


def test_a_summary_line_is_only_quiet_while_the_loss_set_still_matches(sd, monkeypatch):
    """The line alone never quiets anything: a changed key set is news even with the same wording."""
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=_lossy(EIGHT + ["dropped new <- m"])))
    _fake_builder(monkeypatch, built, "print('  WARNING: 9 overlay(s) could not be read')")
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "accepted-losses.json").write_text(json.dumps({"effective": {"keys": sorted(EIGHT), "when": "d"}}))
    assert fc.rebuild(auto=True) == 0
    assert "BUILT WITH 9 DROPS" in (sd / "session-note.txt").read_text(encoding="utf-8")


def test_truncated_property_subtrees_count_as_a_loss_of_the_store(tmp_path, monkeypatch):
    db = _store(tmp_path, truncated_props=12, truncated_prop_samples=json_list(["a.xml: x.y", "b.xml: z"]),
                loss_keys=json_list(["truncated a.xml: x.y", "truncated b.xml: z"]))
    monkeypatch.setattr(fc._effective, "effective_db", lambda: db)
    s = fc._status_effective()
    assert s.incomplete == 12
    assert s.notes[0].startswith("12 property subtree(s)")
    assert "  - a.xml" in s.notes
    assert s.loss_keys == ["truncated a.xml: x.y", "truncated b.xml: z"]


def test_a_store_with_no_truncation_key_has_no_truncation_loss(tmp_path, monkeypatch):
    """An older store: this key absent reads 0, as the two older loss keys do (its engine axis is stale anyway)."""
    monkeypatch.setattr(fc._effective, "effective_db", lambda: _store(tmp_path))
    assert fc._status_effective().incomplete == 0


# --------------------------------------------------------------------------- rebuild's exit code (review T3)

def test_a_deferred_rebuild_by_hand_is_not_a_success(sd, monkeypatch, capsys):
    before = {n: STALE(n) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    monkeypatch.setattr(fc, "_deferred", lambda auto: "engine sources have uncommitted changes: x")
    _fake_builder(monkeypatch, built, "pass")
    assert fc.rebuild(("effective",)) == 5
    assert not built
    assert fc.rebuild(("effective",), auto=True) == 0, "twin: --auto keeps 0, the note carries it"
    assert "DEFERRED" in capsys.readouterr().out


def test_a_deferral_with_everything_requested_fresh_is_still_a_success(sd, monkeypatch):
    before = {n: FRESH(n) for n in fc.ORDER}
    _world(monkeypatch, before, before)
    monkeypatch.setattr(fc, "_deferred", lambda auto: "a game is running")
    assert fc.rebuild(("effective",)) == 0


def test_a_mutation_gate_skip_by_hand_is_not_a_success(sd, monkeypatch, capsys):
    before = {n: (STALE(n) if n == "xref" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "pass")
    monkeypatch.setattr(fc._mutation, "active", lambda: Path("marker"))
    assert fc.rebuild(("xref",)) == 5
    assert not built and "SKIPPED" in capsys.readouterr().out
    assert fc.rebuild(("xref",), auto=True) == 0


def test_only_the_requested_artifacts_decide_the_exit_code(sd, monkeypatch):
    before = {n: (STALE(n) if n == "x4eff" else FRESH(n)) for n in fc.ORDER}
    _world(monkeypatch, before, before)
    assert fc.rebuild(("effective",)) == 0
    assert fc.rebuild(("x4eff",)) != 0


def test_a_builder_failure_stays_rc_1_by_hand_and_under_auto(sd, monkeypatch):
    before = {n: (STALE(n) if n == "xref" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "import sys; sys.exit(1)")
    assert fc.rebuild(("xref",)) == 1
    (sd / "last-failures.json").write_text("{}", encoding="utf-8")   # else --auto rightly does not retry it
    assert fc.rebuild(("xref",), auto=True) == 1


# --------------------------------------------------------------------------- the note cannot read "fine" by accident (T4)

def test_a_run_that_dies_leaves_did_not_complete_never_an_empty_note(sd, monkeypatch):
    def dies(*a, **k):
        raise RuntimeError("killed in the middle")
    monkeypatch.setattr(fc, "_rebuild_locked", dies)
    note = sd / "session-note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("", encoding="utf-8")                 # the previous run was clean
    with pytest.raises(RuntimeError):
        fc.rebuild(auto=True)
    assert note.read_text(encoding="utf-8").startswith(fc.NOTE_PENDING)


def test_a_run_that_finishes_overwrites_the_pending_note(sd, monkeypatch):
    before = {n: FRESH(n) for n in fc.ORDER}
    _world(monkeypatch, before, before)
    assert fc.rebuild(auto=True) == 0
    assert (sd / "session-note.txt").read_text(encoding="utf-8") == ""


def test_the_pending_note_is_on_disk_while_the_build_runs(sd, monkeypatch):
    seen = []
    real = fc._rebuild_locked

    def spy(sd_, targets, auto, note, **k):
        seen.append(note.read_text(encoding="utf-8"))
        return real(sd_, targets, auto, note, **k)
    monkeypatch.setattr(fc, "_rebuild_locked", spy)
    before = {n: FRESH(n) for n in fc.ORDER}
    _world(monkeypatch, before, before)
    fc.rebuild(auto=True)
    assert seen and seen[0].startswith(fc.NOTE_PENDING)


def test_a_rebuild_by_hand_never_touches_the_note(sd, monkeypatch):
    before = {n: FRESH(n) for n in fc.ORDER}
    _world(monkeypatch, before, before)
    note = sd / "session-note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("OLD", encoding="utf-8")
    assert fc.rebuild() == 0
    assert note.read_text(encoding="utf-8") == "OLD"


@pytest.mark.parametrize("memo", ['["not a dict"]', '"text"', '7', 'null', '{"effective": "not a dict"}',
                                  '{"effective": ["x"]}', '{broken'])
def test_a_malformed_memo_means_nothing_remembered_never_a_crash(sd, monkeypatch, memo):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=FRESH("effective")))
    _fake_builder(monkeypatch, built, "pass")
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "last-failures.json").write_text(memo, encoding="utf-8")
    assert fc.rebuild(auto=True) == 0
    assert "effective" in built, "the build was not attempted"


@pytest.mark.parametrize("data", ['["x"]', '{"effective": "not a dict"}', '{"effective": 7}'])
def test_a_malformed_accept_list_accepts_nothing_never_a_crash(sd, monkeypatch, data):
    monkeypatch.setattr(fc, "CHECKS", dict({n: FRESH(n) for n in fc.ORDER}, effective=_lossy(EIGHT)))
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "accepted-losses.json").write_text(data, encoding="utf-8")
    assert fc.main(["status"]) == 3


# --------------------------------------------------------------------------- NOT-BUILT (T5)

def _no_basex(reason="java is not on PATH."):
    return lambda n: (lambda: fc.Status(n, fc.NOT_BUILT, [f"never built here, and it cannot be: {reason}"]))


def _public_machine(monkeypatch):
    nb = _no_basex()
    return {n: (nb(n) if n in ("x4raw", "x4eff") else FRESH(n)) for n in fc.ORDER}


def test_status_basex_never_built_without_java_is_not_built(tmp_path, monkeypatch):
    (tmp_path / "staleness.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: "java is not on PATH.")
    s = fc._status_basex("x4raw")
    assert s.state == fc.NOT_BUILT and "java is not on PATH" in " ".join(s.reasons)


def test_status_basex_never_built_with_a_toolchain_is_still_missing(tmp_path, monkeypatch):
    """Twin: this machine CAN build it, so `x4fresh rebuild` is the right advice and it is not current."""
    (tmp_path / "staleness.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: None)
    assert fc._status_basex("x4raw").state == "MISSING"


def test_status_basex_once_built_is_never_not_built(tmp_path, monkeypatch):
    """Twin: the toolchain going away does not make an existing index stop being judged."""
    (tmp_path / "staleness.py").write_text("raise RuntimeError('judged')", encoding="utf-8")
    (tmp_path / "basex").mkdir()
    (tmp_path / "basex" / "coverage-x4raw.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: "java is not on PATH.")
    assert fc._status_basex("x4raw").state == "UNKNOWN"


def test_status_basex_with_no_tooling_at_all_is_not_built(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path / "nothing-here")
    s = fc._status_basex("x4eff")
    assert s.state == fc.NOT_BUILT and "no BaseX tooling" in " ".join(s.reasons)


def test_unbuildable_comes_from_the_real_preflight(tmp_path, monkeypatch):
    """The real preflight.py, not a stub: an empty BaseX folder has no jar on any machine."""
    src = fc.BASEX_ROOT / "preflight.py"
    if not src.is_file():
        pytest.skip("no tools/basex/preflight.py in this layout")
    (tmp_path / "preflight.py").write_bytes(src.read_bytes())
    (tmp_path / "basex").mkdir()
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    why = fc._basex_unbuildable()
    assert why and "\n" not in why and ("java" in why or "BaseX.jar" in why)


def test_a_preflight_that_cannot_run_is_not_read_as_unbuildable(tmp_path, monkeypatch):
    """A non-answer about the toolchain keeps MISSING: claiming "cannot be built" would be a guess."""
    (tmp_path / "preflight.py").write_text("raise RuntimeError('broken')", encoding="utf-8")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    assert fc._basex_unbuildable() is None


def test_render_does_not_call_not_built_not_current_or_suggest_a_rebuild(monkeypatch):
    out = fc.render([fc.Status("effective", "FRESH"), fc.Status("xref", "FRESH"),
                     fc.Status("x4raw", fc.NOT_BUILT, ["r"]), fc.Status("x4eff", fc.NOT_BUILT, ["r"])])
    assert out.splitlines()[0].startswith("x4fresh: all 2 artifact(s) FRESH; not built on this machine: x4raw, x4eff")
    assert "NOT CURRENT" not in out and "x4fresh rebuild" not in out
    twin = fc.render([fc.Status("effective", "STALE", ["moved"]), fc.Status("x4raw", fc.NOT_BUILT, ["r"])])
    assert twin.splitlines()[0] == "x4fresh: NOT CURRENT: effective STALE -- run `x4fresh rebuild`"


def test_status_exit_0_on_a_public_machine_5_when_asked_for_the_unbuilt_one(sd, monkeypatch, capsys):
    monkeypatch.setattr(fc, "CHECKS", _public_machine(monkeypatch))
    assert fc.main(["status"]) == 0
    assert fc.main(["status", "--only", "x4raw"]) == 5
    assert fc.main(["status", "--only", "effective"]) == 0


def test_rebuild_never_runs_a_builder_that_cannot_succeed(sd, monkeypatch, capsys):
    checks = _public_machine(monkeypatch)
    built = _world(monkeypatch, checks, checks)
    _fake_builder(monkeypatch, built, "pass")
    assert fc.main(["rebuild"]) == 0
    assert not built
    capsys.readouterr()
    assert fc.main(["rebuild", "--only", "x4raw"]) == 5, "it was asked for by name and is not there"
    assert not built
    assert "x4raw: NOT BUILT" in capsys.readouterr().out


def test_the_session_note_names_not_built_once(sd, monkeypatch):
    checks = _public_machine(monkeypatch)
    _world(monkeypatch, checks, checks)
    assert fc.rebuild(auto=True) == 0
    first = (sd / "session-note.txt").read_text(encoding="utf-8")
    assert "x4raw" in first and "x4eff" in first and "NOT CURRENT" not in first
    assert "not rebuilt unattended" not in first
    assert fc.rebuild(auto=True) == 0
    assert (sd / "session-note.txt").read_text(encoding="utf-8") == "", "reported twice"


def test_a_later_problem_note_does_not_repeat_the_not_built_rows(sd, monkeypatch):
    checks = _public_machine(monkeypatch)
    _world(monkeypatch, checks, checks)
    assert fc.rebuild(auto=True) == 0
    _world(monkeypatch, dict(checks, xref=STALE("xref")), dict(checks, xref=STALE("xref")))
    monkeypatch.setattr(fc, "_deferred", lambda auto: "a game is running")
    assert fc.rebuild(auto=True) == 0
    note = (sd / "session-note.txt").read_text(encoding="utf-8")
    assert "xref STALE" in note and "x4raw" not in note


def test_not_built_is_reported_again_after_it_was_built_and_lost(sd, monkeypatch):
    checks = _public_machine(monkeypatch)
    _world(monkeypatch, checks, checks)
    fc.rebuild(auto=True)
    fresh = {n: FRESH(n) for n in fc.ORDER}
    _world(monkeypatch, fresh, fresh)
    fc.rebuild(auto=True)
    _world(monkeypatch, checks, checks)
    fc.rebuild(auto=True)
    assert "x4raw" in (sd / "session-note.txt").read_text(encoding="utf-8")


def test_accept_is_not_refused_by_an_artifact_that_cannot_exist_here(sd, monkeypatch, capsys):
    monkeypatch.setattr(fc, "CHECKS", _public_machine(monkeypatch))
    assert fc.main(["accept"]) == 0
    assert "x4raw: nothing to accept" in capsys.readouterr().out


# --------------------------------------------------------------------------- the failure memo names its inputs (T10)

CONSTANT = lambda n: (lambda: fc.Status(n, "MISSING", [f"no store at /x/{n}"]))   # noqa: E731


def test_a_constant_reason_with_moved_inputs_is_retried(sd, monkeypatch):
    """`MISSING` carries no digest, so the reason alone stayed identical and it was never retried."""
    before = {n: (CONSTANT(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "import sys; sys.exit(1)")
    assert fc.rebuild(auto=True) == 1
    built.clear()
    monkeypatch.setattr(fc, "_world_digest", lambda: "moved")
    assert fc.rebuild(auto=True) == 1
    assert built == {"effective"}, "a mod changed and the build was still not retried"


def test_a_constant_reason_with_the_same_inputs_is_not_retried_the_twin(sd, monkeypatch):
    before = {n: (CONSTANT(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "import sys; sys.exit(1)")
    assert fc.rebuild(auto=True) == 1
    built.clear()
    assert fc.rebuild(auto=True) == 0
    assert not built


def test_inputs_that_cannot_be_compared_are_not_called_the_same_inputs(sd, monkeypatch):
    monkeypatch.setattr(fc, "_world_digest", lambda: None)
    before = {n: (CONSTANT(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "import sys; sys.exit(1)")
    fc.rebuild(auto=True)
    fc.rebuild(auto=True)
    note = (sd / "session-note.txt").read_text(encoding="utf-8")
    assert "NOT RETRIED" in note and "same inputs" not in note and "could not be compared" in note


def test_an_artifact_seen_fresh_clears_its_memo(sd, monkeypatch):
    before = {n: (CONSTANT(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    _fake_builder(monkeypatch, built, "import sys; sys.exit(1)")
    fc.rebuild(auto=True)
    fresh = {n: FRESH(n) for n in fc.ORDER}
    _world(monkeypatch, fresh, fresh)
    fc.rebuild(auto=True)
    assert "effective" not in json.loads((sd / "last-failures.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- release review 4.1.0 round 2

WRAPPER_REMARK = "rb: WARNING: disk:C whole-drive decline exceeds estimate 0.0 GiB; unattributed"


def _wrapped(monkeypatch, *argv):
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps(list(argv)))


def _echoing_builder(monkeypatch, built, *lines):
    """A builder printing `lines`, and a wrapper that adds nothing to the command: only the lines are under test."""
    monkeypatch.setattr(fc, "_wrapper", lambda _n: [])
    _fake_builder(monkeypatch, built, "; ".join(f"print({ln!r})" for ln in lines) or "pass")


def _rebuilt_with_accepted_losses(sd, monkeypatch, *lines, wrapper=("py", "-3.12", "C:/tools/rb.py", "run", "--")):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=_lossy(EIGHT)))
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps(list(wrapper)))
    _echoing_builder(monkeypatch, built, *lines)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "accepted-losses.json").write_text(json.dumps({"effective": {"keys": sorted(EIGHT), "when": "d"}}))
    assert fc.rebuild(auto=True) == 0
    return (sd / "session-note.txt").read_text(encoding="utf-8")


def test_a_wrappers_disk_remark_is_not_a_loss_so_accept_can_quiet_a_rebuild(sd, monkeypatch):
    """R2-4: rb prints this on any drive decline, so `BUILT WITH 8 DROPS` and a note came back after every accept."""
    assert _rebuilt_with_accepted_losses(sd, monkeypatch, WRAPPER_REMARK) == ""


def test_a_real_warning_beside_a_wrapper_remark_still_reaches_the_note(sd, monkeypatch):
    note = _rebuilt_with_accepted_losses(sd, monkeypatch, WRAPPER_REMARK, "  WARNING: a brand new kind of loss")
    assert "WARNING: a brand new kind of loss" in note and "BUILT WITH 8 DROPS" in note


@pytest.mark.parametrize("line", ["WARNING: rb: not at the start", "rbx: WARNING: another program", " rb: WARNING: indented"])
def test_only_a_line_that_starts_with_the_wrappers_own_name_is_the_wrappers(sd, monkeypatch, line):
    assert line in _rebuilt_with_accepted_losses(sd, monkeypatch, line)


def test_with_no_wrapper_configured_nothing_is_set_aside(sd, monkeypatch):
    """The twin: `rb: ` means nothing without a wrapper called rb."""
    note = _rebuilt_with_accepted_losses(sd, monkeypatch, WRAPPER_REMARK, wrapper=("other-tool", "--"))
    assert WRAPPER_REMARK in note


def test_the_wrappers_prefixes_come_from_its_own_command(monkeypatch):
    _wrapped(monkeypatch, "py", "-3.12", "C:/tools/rb.py", "run", "--name", "{name}", "--")
    assert fc._wrapper_prefixes() == ("py: ", "rb: ")
    _wrapped(monkeypatch, "C:/bin/throttle.exe", "--cap", "50")
    assert fc._wrapper_prefixes() == ("throttle: ",)
    monkeypatch.delenv("X4_JOB_WRAPPER")
    assert fc._wrapper_prefixes() == ()


def test_a_real_wrapper_process_its_remark_in_the_log_and_counted_in_the_report(sd, monkeypatch, capsys, tmp_path):
    """A REAL wrapper around a REAL builder, the merged stream as x4fresh reads it. A step that narrows must announce it:
    set aside, never erased."""
    wrapper = tmp_path / "throttle.py"
    wrapper.write_text("import subprocess, sys\n"
                       "print('throttle: WARNING: drive declined', flush=True)\n"
                       "sys.exit(subprocess.run(sys.argv[2:]).returncode)\n", encoding="utf-8")
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, dict(before, effective=FRESH("effective")))
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps([sys.executable, str(wrapper), "--"]))
    _fake_builder(monkeypatch, built, "pass")
    assert fc.rebuild(("effective",)) == 0
    out = capsys.readouterr().out
    assert "effective: REBUILT, FRESH" in out and "1 line(s) from the job wrapper itself" in out
    log = next((sd / "logs").glob("effective-*.log"))
    assert "throttle: WARNING: drive declined" in log.read_text(encoding="utf-8")


def test_a_failed_build_shows_the_wrappers_lines_because_they_may_be_why(sd, monkeypatch, capsys):
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    built = _world(monkeypatch, before, before)
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps(["rb", "run", "--"]))
    monkeypatch.setattr(fc, "_wrapper", lambda _n: [])
    _fake_builder(monkeypatch, built, "import sys; print('rb: ERROR: stopped at the memory cap'); sys.exit(1)")
    assert fc.rebuild(("effective",)) == 1
    assert "rb: ERROR: stopped at the memory cap" in capsys.readouterr().out


def _killing_wrapper(tmp_path, *said):
    """A REAL wrapper that prints `said` (its own lines, in order) and exits 86, as rb does on a kill."""
    wrapper = tmp_path / "throttle.py"
    wrapper.write_text("import subprocess, sys\n"
                       "subprocess.run(sys.argv[2:])\n"
                       + "".join(f"print({line!r}, flush=True)\n" for line in said)
                       + "sys.exit(86)\n", encoding="utf-8")
    return wrapper


KILL = "throttle: KILLED by throttle: memory cap 3.0 GB hit (allocation refused) -> exit 86"


def test_a_failed_build_names_the_wrappers_kill_reason_first(sd, monkeypatch, capsys, tmp_path):
    """4.1.1: the FAILED line showed an unrelated disk remark while the kill line, which carries no WARNING or ERROR
    word, was not collected at all. The line that explains the exit comes first, whatever order it was printed in."""
    wrapper = _killing_wrapper(tmp_path, "throttle: WARNING: disk:C whole-drive decline exceeds estimate", KILL,
                               "throttle: measured: cpu peak 4% (cap 25.0)")
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    _fake_builder(monkeypatch, _world(monkeypatch, before, before), "pass")
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps([sys.executable, str(wrapper), "--"]))
    assert fc.rebuild(("effective",)) == 1
    out = capsys.readouterr().out
    failed = next(ln for ln in out.splitlines() if "effective: FAILED rc 86" in ln)
    assert KILL in failed, out
    shown = [ln.strip() for ln in out.splitlines() if ln.startswith("    ")]
    assert shown[0] == KILL, out
    assert "throttle: WARNING: disk:C whole-drive decline exceeds estimate" in shown, out


def test_TWIN_a_successful_build_does_not_report_the_wrappers_exit_remarks(sd, monkeypatch, capsys, tmp_path):
    """A clean exit's `child exit 0` or `(cap 25.0)` explains nothing, and is not counted as a wrapper line."""
    wrapper = tmp_path / "throttle.py"
    wrapper.write_text("import subprocess, sys\n"
                       "r = subprocess.run(sys.argv[2:]).returncode\n"
                       "print('throttle: measured: cpu peak 4% (cap 25.0)', flush=True)\n"
                       "print('throttle: child exit 0', flush=True)\n"
                       "sys.exit(r)\n", encoding="utf-8")
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    _fake_builder(monkeypatch, _world(monkeypatch, before, dict(before, effective=FRESH("effective"))), "pass")
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps([sys.executable, str(wrapper), "--"]))
    assert fc.rebuild(("effective",)) == 0
    out = capsys.readouterr().out
    assert "effective: REBUILT, FRESH" in out and "from the job wrapper" not in out, out


def test_TWIN_a_failure_with_no_explaining_line_keeps_the_printed_order(sd, monkeypatch, capsys, tmp_path):
    wrapper = _killing_wrapper(tmp_path, "throttle: WARNING: first", "throttle: ERROR: second")
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    _fake_builder(monkeypatch, _world(monkeypatch, before, before), "pass")
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps([sys.executable, str(wrapper), "--"]))
    assert fc.rebuild(("effective",)) == 1
    out = capsys.readouterr().out
    shown = [ln.strip() for ln in out.splitlines() if ln.startswith("    ")]
    assert shown[:2] == ["throttle: WARNING: first", "throttle: ERROR: second"], out
    assert "job wrapper:" not in next(ln for ln in out.splitlines() if "FAILED rc 86" in ln), out


def test_a_loss_list_accepted_before_truncated_keys_existed_explains_the_mismatch(sd, monkeypatch):
    """An old accept holds the 8 overlays and cannot hold the property subtrees, listed by name only since 4.1.0."""
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "accepted-losses.json").write_text(json.dumps({"effective": {"keys": sorted(EIGHT), "when": "2026-11-01"}}))
    keys = sorted(EIGHT) + ["truncated a.xml: x.y", "truncated b.xml: z"]
    s = fc._mark_accepted(fc.Status("effective", "FRESH", incomplete=10, loss_keys=keys),
                          json.loads((sd / "accepted-losses.json").read_text()))
    assert s.accepted is None
    said = " ".join(s.notes)
    assert "differs from the 8 loss(es) accepted 2026-11-01: 2 new, 0 no longer lost" in said
    assert "listed by name only since 4.1.0" in said


def test_a_mismatch_with_new_overlays_does_not_blame_the_upgrade(sd):
    keys = sorted(EIGHT) + ["dropped another.xml"]
    s = fc._mark_accepted(fc.Status("effective", "FRESH", incomplete=9, loss_keys=keys),
                          {"effective": {"keys": sorted(EIGHT), "when": "d"}})
    said = " ".join(s.notes)
    assert "1 new, 0 no longer lost" in said and "first new: dropped another.xml" in said and "4.1.0" not in said


def test_a_matching_list_and_no_list_at_all_explain_nothing(sd):
    match = fc._mark_accepted(fc.Status("effective", "FRESH", incomplete=8, loss_keys=sorted(EIGHT)),
                              {"effective": {"keys": sorted(EIGHT), "when": "d"}})
    assert match.accepted == "d" and match.notes == []
    fresh = fc._mark_accepted(fc.Status("effective", "FRESH", incomplete=8, loss_keys=sorted(EIGHT)), {})
    assert fresh.notes == []


def test_the_first_run_after_an_upgrade_says_plainly_what_exit_3_means(sd, capsys):
    head = fc.render([fc.Status("effective", "FRESH", incomplete=18, loss_keys=sorted(EIGHT))]).splitlines()[0]
    assert "not a build failure" in head and "`x4fresh accept`" in head and "exit 3 until then" in head


def test_a_database_on_disk_with_no_coverage_is_not_never_built(tmp_path, monkeypatch):
    """R2-6: no jar and no coverage used to read "never built here", exit 0, over a database on disk."""
    (tmp_path / "staleness.py").write_text("", encoding="utf-8")
    db = tmp_path / "basex" / "data" / "x4raw"
    db.mkdir(parents=True)
    (db / "tbl.basex").write_bytes(b"db")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: "java is not on PATH.")
    s = fc._status_basex("x4raw")
    assert s.state == "MISSING" and "database is on disk" in " ".join(s.reasons)
    assert "java is not on PATH" in " ".join(s.reasons)


def _swap_world(tmp_path, monkeypatch, *dbs):
    """A BaseX home holding the named database folders, with the real dbswap/preflight beside it."""
    import shutil
    real = Path(fc.__file__).resolve().parents[2] / "basex"
    for name in ("dbswap.py", "preflight.py"):
        shutil.copyfile(real / name, tmp_path / name)
    (tmp_path / "staleness.py").write_text("raise RuntimeError('judged')", encoding="utf-8")
    for d in dbs:
        (tmp_path / "basex" / "data" / d).mkdir(parents=True)
        (tmp_path / "basex" / "data" / d / "tbl.basex").write_bytes(b"db")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: None)
    return tmp_path / "basex"


def test_an_interrupted_swap_is_reported_with_its_remedy(tmp_path, monkeypatch):
    """4.1.1 build-then-swap: a crash between the renames leaves x4raw absent and x4raw__old/__next on disk.
    MISSING is true, but on its own it sends the reader to a full rebuild; the swap journal says recovery."""
    home = _swap_world(tmp_path, monkeypatch, "x4raw__old", "x4raw__next")
    (home / "swap-x4raw.json").write_text('{"db": "x4raw", "phase": "live-moved"}', encoding="utf-8")
    s = fc._status_basex("x4raw")
    text = " ".join(s.reasons)
    assert s.state == "MISSING" and "interrupted" in text and "dbswap.py recover --db x4raw" in text, s


def test_a_failed_build_leaves_the_previous_database_judged_not_missing(tmp_path, monkeypatch):
    """Twin: the live database and its report are in place, an unfinished x4raw__next beside them. The
    verdict is the live one's (here UNKNOWN from the stub), never MISSING, and the leftover is named."""
    home = _swap_world(tmp_path, monkeypatch, "x4raw", "x4raw__next")
    (home / "coverage-x4raw.json").write_text("{}", encoding="utf-8")
    s = fc._status_basex("x4raw")
    assert s.state == "UNKNOWN", s
    assert "x4raw__next is left from an unfinished build" in " ".join(s.reasons), s


_FRESH_STALENESS = chr(10).join(("from types import SimpleNamespace",
                                  "def _defaults():",
                                  "    return None, None, None",
                                  "def check(*a, **k):",
                                  "    return SimpleNamespace(fresh=True, determinable=True, reasons=[])", ""))


@pytest.mark.parametrize("left, said", [("x4raw__old", "x4raw__old exists with no swap journal"),
                                        ("x4raw__next", "x4raw__next is left from an unfinished build")])
def test_a_fresh_database_still_names_a_generation_beside_it(tmp_path, monkeypatch, left, said):
    """4.1.1 review B3: the swap notes were added only when the database was not FRESH, so a FRESH x4raw beside an
    unjournaled x4raw__old, or a ~1 GB x4raw__next, was never mentioned. The verdict stays FRESH; the note is said."""
    home = _swap_world(tmp_path, monkeypatch, "x4raw", left)
    (tmp_path / "staleness.py").write_text(_FRESH_STALENESS, encoding="utf-8")
    (home / "coverage-x4raw.json").write_text("{}", encoding="utf-8")
    s = fc._status_basex("x4raw")
    assert s.state == "FRESH", s
    assert said in " ".join(s.reasons + s.notes), s
    assert said in fc.render([s]), fc.render([s])


def test_TWIN_a_fresh_database_alone_says_nothing_more(tmp_path, monkeypatch):
    home = _swap_world(tmp_path, monkeypatch, "x4raw")
    (tmp_path / "staleness.py").write_text(_FRESH_STALENESS, encoding="utf-8")
    (home / "coverage-x4raw.json").write_text("{}", encoding="utf-8")
    s = fc._status_basex("x4raw")
    assert s.state == "FRESH" and s.reasons == [] and s.notes == [], s


def test_an_empty_database_folder_is_still_never_built(tmp_path, monkeypatch):
    (tmp_path / "staleness.py").write_text("", encoding="utf-8")
    (tmp_path / "basex" / "data" / "x4raw").mkdir(parents=True)
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: "java is not on PATH.")
    assert fc._status_basex("x4raw").state == fc.NOT_BUILT


def test_another_databases_folder_does_not_count(tmp_path, monkeypatch):
    (tmp_path / "staleness.py").write_text("", encoding="utf-8")
    db = tmp_path / "basex" / "data" / "x4eff"
    db.mkdir(parents=True)
    (db / "tbl.basex").write_bytes(b"db")
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: "java is not on PATH.")
    assert fc._status_basex("x4raw").state == fc.NOT_BUILT


def _failed_line(sd, monkeypatch, capsys, tmp_path, *said):
    wrapper = _killing_wrapper(tmp_path, *said)
    before = {n: (STALE(n) if n == "effective" else FRESH(n)) for n in fc.ORDER}
    _fake_builder(monkeypatch, _world(monkeypatch, before, before), "pass")
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps([sys.executable, str(wrapper), "--"]))
    assert fc.rebuild(("effective",)) == 1
    out = capsys.readouterr().out
    return next(ln for ln in out.splitlines() if "effective: FAILED rc 86" in ln), out


def test_an_earlier_admission_refusal_that_was_waited_out_is_not_blamed(sd, monkeypatch, capsys, tmp_path):
    """4.1.1 review: `refused` at admission, then a wait, then the real kill -> the kill (it names this rc) is the why."""
    early = "throttle: admission refused: cpu busy, waiting 30s"
    failed, _out = _failed_line(sd, monkeypatch, capsys, tmp_path, early, KILL)
    assert failed.endswith("job wrapper: " + KILL), failed


def test_the_last_explaining_line_wins_when_none_names_the_exit_code(sd, monkeypatch, capsys, tmp_path):
    failed, _out = _failed_line(sd, monkeypatch, capsys, tmp_path,
                                "throttle: admission refused once", "throttle: watchdog killed the child")
    assert failed.endswith("job wrapper: throttle: watchdog killed the child"), failed


def test_TWIN_a_line_naming_another_exit_code_loses_to_one_naming_this_one(sd, monkeypatch, capsys, tmp_path):
    other = "throttle: KILLED an earlier child -> exit 3"
    failed, _out = _failed_line(sd, monkeypatch, capsys, tmp_path, KILL, other)
    assert failed.endswith("job wrapper: " + KILL), failed


def test_the_quoted_wrapper_line_is_capped(sd, monkeypatch, capsys, tmp_path):
    long = "throttle: KILLED " + "x" * 2000 + " -> exit 86"
    failed, _out = _failed_line(sd, monkeypatch, capsys, tmp_path, long)
    quoted = failed.split("job wrapper: ", 1)[1]
    assert len(quoted) <= 300 and quoted.startswith("throttle: KILLED"), len(quoted)



# --------------------------------------------------------------------------- automatic BaseX rebuild (4.1.1)
# `rebuild --auto` hands x4raw/x4eff to ONE detached background process, only when the game is closed, the world has
# been quiet for X4_FRESH_BASEX_QUIET_MIN (default 30) minutes, the disk fits a second copy, no build holds a lock,
# BaseX can build here, the database was built once before, and the same inputs did not already fail.

T0 = 1_800_000_000.0


@pytest.fixture
def bxa(sd, monkeypatch):
    """BaseX auto ON, every machine fact faked to 'fine'; tests flip one at a time."""
    monkeypatch.delenv("X4_FRESH_BASEX_AUTO", raising=False)
    clock = {"t": T0}
    monkeypatch.setattr(fc, "_now", lambda: clock["t"])

    def sleep(s):
        clock["t"] += s
    monkeypatch.setattr(fc, "_sleep", sleep)
    monkeypatch.setattr(fc, "_basex_previously_built", lambda db: True)
    monkeypatch.setattr(fc, "_basex_unbuildable", lambda: None)
    monkeypatch.setattr(fc, "_basex_disk_shortfall", lambda db: None)
    monkeypatch.setattr(fc, "_basex_running_build", lambda db: None)
    spawned = []
    monkeypatch.setattr(fc, "_spawn_basex", lambda note: spawned.append(note) or 4242)
    return {"clock": clock, "spawned": spawned}


def _seen(sd, minutes_ago, digest="world"):
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "world-seen.json").write_text(json.dumps({"digest": digest, "first_seen": T0 - minutes_ago * 60}),
                                        encoding="utf-8")


def _basex_stale(monkeypatch, effective="FRESH"):
    before = {n: (STALE(n) if n in fc.BASEX else FRESH(n)) for n in fc.ORDER}
    if effective != "FRESH":
        before["effective"] = STALE("effective")
    return _world(monkeypatch, before, before)


def _note(sd):
    return (sd / "session-note.txt").read_text(encoding="utf-8")


def test_auto_starts_basex_in_the_background_once_the_world_is_quiet(sd, bxa, monkeypatch):
    built = _basex_stale(monkeypatch)
    _fake_builder(monkeypatch, built, "pass")
    _seen(sd, 31)
    assert fc.rebuild(auto=True) == 0
    assert not built, "the session-start run must never build BaseX itself (it is detached)"
    assert len(bxa["spawned"]) == 1
    assert "x4raw, x4eff: automatic rebuild started in the background (pid 4242)" in _note(sd)


def test_the_quiet_period_not_yet_over_starts_a_waiting_builder_and_says_until_when(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    _seen(sd, 10)
    assert fc.rebuild(auto=True) == 0
    assert len(bxa["spawned"]) == 1
    assert "waits until the mods have been unchanged for 30 min" in _note(sd)


def test_the_quiet_period_is_configurable(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    _seen(sd, 10)
    monkeypatch.setenv("X4_FRESH_BASEX_QUIET_MIN", "5")
    fc.rebuild(auto=True)
    assert "waits" not in _note(sd) and bxa["spawned"]


def test_an_unreadable_quiet_setting_skips_never_guesses(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    monkeypatch.setenv("X4_FRESH_BASEX_QUIET_MIN", "half an hour")
    fc.rebuild(auto=True)
    assert not bxa["spawned"]
    assert "not rebuilt automatically: X4_FRESH_BASEX_QUIET_MIN" in _note(sd)


def test_an_unmeasurable_world_skips(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    monkeypatch.setattr(fc, "_world_digest", lambda: None)
    fc.rebuild(auto=True)
    assert not bxa["spawned"]
    assert "quiet period cannot be measured" in _note(sd)


def test_first_seen_is_kept_for_the_same_world_and_reset_by_a_new_one(sd):
    assert fc._first_seen(sd, "a", 100.0) == 100.0
    assert fc._first_seen(sd, "a", 500.0) == 100.0
    assert fc._first_seen(sd, "b", 900.0) == 900.0, "a changed world starts the quiet period again"


@pytest.mark.parametrize("flip, expect", [
    ("_basex_disk_shortfall", "not enough disk"),
    ("_basex_running_build", "a build of"),
    ("_basex_unbuildable", "BaseX cannot build here"),
    ("_basex_previously_built", "never built here"),
])
def test_each_machine_condition_alone_skips_with_one_line(sd, bxa, monkeypatch, flip, expect):
    _basex_stale(monkeypatch)
    _seen(sd, 60)
    value = {"_basex_disk_shortfall": lambda db: "not enough disk to build " + db,
             "_basex_running_build": lambda db: 777,
             "_basex_unbuildable": lambda: "java is not on PATH.",
             "_basex_previously_built": lambda db: False}[flip]
    monkeypatch.setattr(fc, flip, value)
    assert fc.rebuild(auto=True) == 0
    assert not bxa["spawned"]
    note = _note(sd)
    assert expect in note and "not rebuilt automatically" in note
    assert "Traceback" not in note


def test_a_game_running_defers_basex_too(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    _seen(sd, 60)
    monkeypatch.setattr(fc, "_deferred", lambda auto: "deferring while running: x4.exe")
    fc.rebuild(auto=True)
    assert not bxa["spawned"]
    assert "DEFERRED" in _note(sd)


def test_one_background_builder_at_a_time(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    _seen(sd, 60)
    held = fc.BuildLock(sd / fc.BASEX_AUTO_LOCK)
    assert held.acquire()
    try:
        fc.rebuild(auto=True)
    finally:
        held.release()
    assert not bxa["spawned"]
    assert "already scheduled or running" in _note(sd)


def test_never_built_or_not_built_is_never_started(sd, bxa, monkeypatch):
    checks = _public_machine(monkeypatch)
    _world(monkeypatch, checks, checks)
    fc.rebuild(auto=True)
    assert not bxa["spawned"]
    assert "not rebuilt automatically" not in _note(sd), "a NOT-BUILT database is named once, not every session"


def test_a_stale_effective_store_holds_back_x4eff_only(sd, bxa, monkeypatch):
    built = _basex_stale(monkeypatch, effective="STALE")
    _fake_builder(monkeypatch, built, "import sys; sys.exit(1)")    # the effective build fails: still STALE
    _seen(sd, 60)
    fc.rebuild(auto=True)
    assert bxa["spawned"]
    note = _note(sd)
    assert "x4eff: not rebuilt automatically: the effective store is STALE" in note
    assert "x4raw: automatic rebuild started" in note


def test_a_remembered_failure_is_not_started_again(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    _seen(sd, 60)
    sd.mkdir(parents=True, exist_ok=True)
    key = "x4raw moved [a -> b] @ world"
    (sd / "last-failures.json").write_text(json.dumps({"x4raw": {"reasons": key, "rc": 1, "when": "then"}}),
                                           encoding="utf-8")
    fc.rebuild(auto=True)
    note = _note(sd)
    assert "x4raw: not rebuilt automatically: not retried" in note
    assert "x4eff: automatic rebuild started" in note


def test_the_off_switch(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    _seen(sd, 60)
    monkeypatch.setenv("X4_FRESH_BASEX_AUTO", "off")
    fc.rebuild(auto=True)
    assert not bxa["spawned"]
    assert "not rebuilt automatically: X4_FRESH_BASEX_AUTO=off" in _note(sd)


# ---- the background builder itself

def _child_world(monkeypatch, effective_after_x4raw="FRESH"):
    order: list[str] = []
    state = {n: (STALE(n) if n in fc.BASEX else FRESH(n)) for n in fc.ORDER}

    def check(name):
        def f():
            if name == "effective" and "x4raw" in order and effective_after_x4raw != "FRESH":
                return STALE("effective")()
            return (FRESH(name) if name in order else state[name])()
        return f
    monkeypatch.setattr(fc, "CHECKS", {n: check(n) for n in fc.ORDER})
    monkeypatch.setattr(fc, "_deferred", lambda auto: None)

    def builder(name):
        order.append(name)
        return [sys.executable, "-c", "pass"]
    monkeypatch.setattr(fc, "builder", builder)
    return order


def test_the_background_builder_waits_out_the_quiet_period_then_builds_x4raw_then_x4eff(sd, bxa, monkeypatch):
    order = _child_world(monkeypatch)
    _seen(sd, 10)
    assert fc._basex_child(sd / "session-note.txt") == 0
    assert order == ["x4raw", "x4eff"]
    assert bxa["clock"]["t"] >= T0 + 20 * 60, "it started before the quiet period was over"
    rec = json.loads((sd / "basex-auto.json").read_text(encoding="utf-8"))
    assert rec["state"] == "finished"
    assert _note(sd) == "", "a clean outcome leaves the note empty, as every --auto run does"
    assert "x4raw: REBUILT, FRESH" in rec["detail"] and "x4eff: REBUILT, FRESH" in rec["detail"]
    assert fc._basex_auto_line(sd).startswith("last automatic BaseX rebuild: finished")


def test_a_world_that_moves_during_the_wait_restarts_it(sd, bxa, monkeypatch):
    order = _child_world(monkeypatch)
    _seen(sd, 25)
    digests = iter(["world", "moved"])
    monkeypatch.setattr(fc, "_world_digest", lambda: next(digests, "moved"))
    fc._basex_child(sd / "session-note.txt")
    assert order == ["x4raw", "x4eff"]
    assert bxa["clock"]["t"] >= T0 + 30 * 60, "a change during the wait must restart the 30 minutes"


def test_the_effective_store_going_stale_during_x4raw_holds_back_x4eff(sd, bxa, monkeypatch):
    order = _child_world(monkeypatch, effective_after_x4raw="STALE")
    _seen(sd, 60)
    fc._basex_child(sd / "session-note.txt")
    assert order == ["x4raw"]
    assert "x4eff: not rebuilt automatically: the effective store is STALE" in _note(sd)


def test_the_background_builder_rechecks_the_game_before_building(sd, bxa, monkeypatch):
    order = _child_world(monkeypatch)
    monkeypatch.setattr(fc, "_deferred", lambda auto: "deferring while running: x4.exe" if auto else None)
    _seen(sd, 60)
    fc._basex_child(sd / "session-note.txt")
    assert order == []
    assert json.loads((sd / "basex-auto.json").read_text(encoding="utf-8"))["state"] == "skipped"


def test_a_second_background_builder_does_nothing(sd, bxa, monkeypatch):
    order = _child_world(monkeypatch)
    _seen(sd, 60)
    held = fc.BuildLock(sd / fc.BASEX_AUTO_LOCK)
    assert held.acquire()
    try:
        assert fc._basex_child(sd / "session-note.txt") == 0
    finally:
        held.release()
    assert order == []


def test_a_failed_background_build_is_recorded_and_remembered(sd, bxa, monkeypatch):
    order = _child_world(monkeypatch)
    monkeypatch.setattr(fc, "builder",
                        lambda name: order.append(name) or [sys.executable, "-c", "import sys; sys.exit(2)"])
    _seen(sd, 60)
    fc._basex_child(sd / "session-note.txt")
    rec = json.loads((sd / "basex-auto.json").read_text(encoding="utf-8"))
    assert rec["state"] == "failed"
    assert "x4raw" in json.loads((sd / "last-failures.json").read_text(encoding="utf-8"))
    assert "FAILED rc 2" in _note(sd)


def test_a_background_builder_that_crashes_records_it(sd, bxa, monkeypatch):
    _child_world(monkeypatch)
    _seen(sd, 60)

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(fc, "_rebuild_locked", boom)
    assert fc._basex_child(sd / "session-note.txt") == 1
    rec = json.loads((sd / "basex-auto.json").read_text(encoding="utf-8"))
    assert rec["state"] == "failed" and "kaboom" in rec["detail"]


def test_a_record_left_running_by_a_dead_builder_is_said_so(sd):
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "basex-auto.json").write_text(json.dumps({"state": "running", "pid": 1, "when": "2026-11-07 10:00"}),
                                        encoding="utf-8")
    assert "ended without reporting" in fc._basex_auto_line(sd)


def test_a_record_running_with_its_lock_held_is_running(sd):
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "basex-auto.json").write_text(json.dumps({"state": "running", "pid": 1, "when": "2026-11-07 10:00",
                                                    "building": "x4raw"}), encoding="utf-8")
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(sd / fc.BASEX_AUTO_LOCK), str(fc.PKG_ROOT)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        line = fc._basex_auto_line(sd)
    finally:
        holder.kill()
        holder.wait()
    assert line.startswith("automatic BaseX rebuild: running since 2026-11-07 10:00") and "x4raw" in line


def test_status_prints_the_automatic_build_line(sd, monkeypatch, capsys):
    monkeypatch.setattr(fc, "CHECKS", {n: FRESH(n) for n in fc.ORDER})
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "basex-auto.json").write_text(json.dumps({"state": "skipped", "when": "2026-11-07 10:00",
                                                    "detail": "x4raw: not enough disk"}), encoding="utf-8")
    assert fc.main(["status"]) == 0
    assert "last automatic BaseX rebuild: skipped 2026-11-07 10:00: x4raw: not enough disk" in capsys.readouterr().out


def test_a_session_start_while_a_build_runs_says_so_not_that_it_died(sd, monkeypatch):
    held = fc.BuildLock(sd / "x4fresh.lock")
    assert held.acquire()
    try:
        assert fc.rebuild(auto=True) == 0
    finally:
        held.release()
    note = _note(sd)
    assert note.startswith("x4fresh: a rebuild is running") and not note.startswith(fc.NOTE_PENDING)


def test_the_detached_launch_returns_at_once_and_outlives_the_call(tmp_path, monkeypatch):
    """The real spawn, with a stand-in child: Popen must return while the child is still running."""
    flag = tmp_path / "alive"
    code = f"import time,pathlib; pathlib.Path({str(flag)!r}).write_text('up'); time.sleep(30)"
    monkeypatch.setattr(fc, "_basex_child_argv", lambda note: [sys.executable, "-c", code])
    t = time.time()
    pid = fc._spawn_basex(tmp_path / "note.txt")
    assert time.time() - t < 5, "the launch waited for the build"
    deadline = time.time() + 15
    while not flag.exists() and time.time() < deadline:
        time.sleep(0.1)
    try:
        assert flag.exists(), "the detached child never ran"
        assert _alive(pid)
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)


# ---- 4.1.1 review of the automatic BaseX rebuild

NO_WINDOW, DETACHED, NEW_GROUP, BELOW_NORMAL, BREAKAWAY = 0x08000000, 0x00000008, 0x00000200, 0x00004000, 0x01000000


class _PopenSpy:
    """Records each Popen call's kwargs; the first `fail` calls raise OSError (a job that forbids breakaway)."""

    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    def __call__(self, argv, **kw):
        self.calls.append(kw)
        if len(self.calls) <= self.fail:
            raise OSError("breakaway refused")
        from types import SimpleNamespace
        return SimpleNamespace(pid=999, returncode=None)


@pytest.mark.parametrize("fail", [0, 1])
def test_the_background_builder_gets_a_hidden_console_never_a_detached_one(monkeypatch, tmp_path, fail):
    """DETACHED_PROCESS gives the builder NO console, so every console child it starts (bash for the whole build,
    git, tasklist, java) gets a NEW, VISIBLE one; closing that window killed the build. CREATE_NO_WINDOW gives it a
    hidden console its children inherit. Both attempts (with and without breakaway) carry it."""
    spy = _PopenSpy(fail)
    monkeypatch.setattr(fc.subprocess, "Popen", spy)
    monkeypatch.setattr(fc, "_on_windows", lambda: True, raising=False)
    assert fc._spawn_basex(tmp_path / "note.txt") == 999
    flags = spy.calls[-1]["creationflags"]
    assert flags & NO_WINDOW and not flags & DETACHED, hex(flags)
    assert flags & NEW_GROUP and flags & BELOW_NORMAL, hex(flags)
    assert bool(flags & BREAKAWAY) is (fail == 0), hex(flags)
    assert "start_new_session" not in spy.calls[-1]


def _stale_staleness(reason):
    return chr(10).join(("from types import SimpleNamespace",
                         "def _defaults():",
                         "    return None, None, None",
                         "def check(*a, **k):",
                         f"    return SimpleNamespace(fresh=False, determinable=True, reasons=[{reason!r}])", ""))


def _what_a_failed_build_leaves(home, dead_pid=2_000_000_000):
    """The leftovers a failed or killed build really leaves beside a live x4raw: an unfinished x4raw__next and a
    build lock whose owner is not running. dbswap's describe() turns each into a line with a pid in it."""
    nxt = home / "data" / "x4raw__next"
    nxt.mkdir(parents=True)
    (nxt / "tbl.basex").write_bytes(b"db")
    (home / "build-x4raw.lock").write_text(json.dumps({"db": "x4raw", "pid": dead_pid, "at": 1.0}), encoding="utf-8")


def test_the_failure_memo_key_ignores_what_the_failed_build_left_behind(tmp_path, monkeypatch):
    """4.1.1 review: the key was built from reasons that included dbswap's describe() lines. A failure was recorded
    with the key from BEFORE the build; the leftovers it then left changed the next key, so the same failed build ran
    once more at the next session start. Driven through the real _status_basex and the real dbswap.describe()."""
    home = _swap_world(tmp_path, monkeypatch, "x4raw")
    (tmp_path / "staleness.py").write_text(_stale_staleness("x4raw: a mod changed"), encoding="utf-8")
    (home / "coverage-x4raw.json").write_text("{}", encoding="utf-8")
    recorded = fc._memo_key(fc._status_basex("x4raw"), "world")
    _what_a_failed_build_leaves(home)
    after = fc._status_basex("x4raw")
    said = " ".join(after.reasons)
    assert "x4raw__next is left from an unfinished build" in said and "2000000000" in said, after
    assert fc._memo_key(after, "world") == recorded
    memo = {"x4raw": {"reasons": recorded, "rc": 1, "when": "then"}}
    monkeypatch.setattr(fc, "_basex_previously_built", lambda db: True)
    assert fc._basex_blocker("x4raw", {"x4raw": after}, memo, "world").startswith("not retried")


def test_TWIN_a_changed_freshness_reason_still_changes_the_key(tmp_path, monkeypatch):
    home = _swap_world(tmp_path, monkeypatch, "x4raw")
    (tmp_path / "staleness.py").write_text(_stale_staleness("x4raw: a mod changed"), encoding="utf-8")
    (home / "coverage-x4raw.json").write_text("{}", encoding="utf-8")
    recorded = fc._memo_key(fc._status_basex("x4raw"), "world")
    (tmp_path / "staleness.py").write_text(_stale_staleness("x4raw: another mod changed"), encoding="utf-8")
    sys.modules.pop("staleness", None)
    assert fc._memo_key(fc._status_basex("x4raw"), "world") != recorded
    assert fc._memo_key(fc._status_basex("x4raw"), "moved") != fc._memo_key(fc._status_basex("x4raw"), "world")


def _swap_retry_world(monkeypatch, swap_code="pass", after_swap="FRESH", pending_once=True):
    """The background builder over x4raw and x4eff STALE, where x4raw has a finished, stamped x4raw__next that an
    earlier run could not swap in (a reader held x4raw: rc 7). `ran` records each swap and each full build."""
    ran: list[str] = []
    moved = lambda n: (lambda: fc.Status(n, "STALE", [f"{n}: the swapped-in build is older than the mods"]))  # noqa: E731

    def check(name):
        def f():
            if name in ran:
                return FRESH(name)()
            if "swap:" + name in ran and after_swap != "UNCHANGED":    # UNCHANGED: the swap was refused
                return (FRESH(name) if after_swap == "FRESH" else moved(name))()
            return (STALE(name) if name in fc.BASEX else FRESH(name))()
        return f
    monkeypatch.setattr(fc, "CHECKS", {n: check(n) for n in fc.ORDER})
    monkeypatch.setattr(fc, "_deferred", lambda auto: None)
    monkeypatch.setattr(fc, "builder", lambda name: ran.append(name) or [sys.executable, "-c", "pass"])
    monkeypatch.setattr(fc, "_swap_argv", lambda name: ran.append("swap:" + name) or [sys.executable, "-c", swap_code],
                        raising=False)
    monkeypatch.setattr(fc, "_basex_swap_pending",
                        lambda db: db == "x4raw" and not (pending_once and "swap:x4raw" in ran), raising=False)
    return ran


def _remember_failed(sd, db="x4raw"):
    """The memo a real failure writes: the key the real key function gives the status before that build."""
    sd.mkdir(parents=True, exist_ok=True)
    key = fc._memo_key(STALE(db)(), "world")
    (sd / "last-failures.json").write_text(json.dumps({db: {"reasons": key, "rc": 7, "when": "then"}}),
                                           encoding="utf-8")


def test_a_build_refused_only_at_the_swap_is_retried_as_the_swap_alone(sd, bxa, monkeypatch):
    """4.1.1 review: a build that ended rc 7 (a reader held x4raw at swap time) left a verified, stamped x4raw__next.
    The next attempt must swap that in, not drop it and rebuild for half an hour -- and the failure memo, whose key
    no longer moves (previous fix), must not stop the swap either."""
    ran = _swap_retry_world(monkeypatch)
    _remember_failed(sd)
    _seen(sd, 60)
    assert fc._basex_child(sd / "session-note.txt") == 0
    assert ran == ["swap:x4raw", "x4eff"], ran
    rec = json.loads((sd / "basex-auto.json").read_text(encoding="utf-8"))
    assert rec["state"] == "finished" and "x4raw: swapped in the finished build" in rec["detail"], rec
    assert "x4raw" not in json.loads((sd / "last-failures.json").read_text(encoding="utf-8"))


def test_a_swap_refused_again_is_a_failure_and_is_retried_as_the_swap_next_time(sd, bxa, monkeypatch):
    ran = _swap_retry_world(monkeypatch, swap_code="import sys; sys.exit(7)", after_swap="UNCHANGED",
                            pending_once=False)
    _seen(sd, 60)
    fc._basex_child(sd / "session-note.txt")
    assert "FAILED rc 7" in _note(sd)
    fc._basex_child(sd / "session-note.txt")
    assert ran == ["swap:x4raw", "x4eff", "swap:x4raw"], "never a full build while a finished one waits to be swapped"


def test_TWIN_a_swapped_in_build_that_is_still_not_current_is_rebuilt_in_the_same_run(sd, bxa, monkeypatch):
    ran = _swap_retry_world(monkeypatch, after_swap="STALE")
    _seen(sd, 60)
    fc._basex_child(sd / "session-note.txt")
    assert ran == ["swap:x4raw", "x4raw", "x4eff"], ran


def test_TWIN_a_swap_whose_verification_refuses_falls_back_to_the_full_build(sd, bxa, monkeypatch):
    ran = _swap_retry_world(monkeypatch, swap_code="import sys; sys.exit(2)", after_swap="UNCHANGED")
    _seen(sd, 60)
    fc._basex_child(sd / "session-note.txt")
    assert ran == ["swap:x4raw", "x4raw", "x4eff"], ran


def test_TWIN_no_finished_build_waiting_and_a_remembered_failure_is_not_retried(sd, bxa, monkeypatch):
    ran = _swap_retry_world(monkeypatch)
    monkeypatch.setattr(fc, "_basex_swap_pending", lambda db: False)
    _remember_failed(sd)
    _seen(sd, 60)
    fc._basex_child(sd / "session-note.txt")
    assert ran == ["x4eff"], ran


def test_the_session_start_schedules_a_pending_swap_despite_the_memo(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    monkeypatch.setattr(fc, "_basex_swap_pending", lambda db: db == "x4raw", raising=False)
    monkeypatch.setattr(fc, "_basex_disk_shortfall", lambda db: "not enough disk to build " + db)
    _remember_failed(sd)
    _seen(sd, 60)
    fc.rebuild(auto=True)
    note = _note(sd)
    assert "x4raw: automatic rebuild started" in note, "a swap needs neither the memo's leave nor new disk"
    assert "x4eff: not rebuilt automatically: not enough disk" in note


@pytest.mark.parametrize("change, pending", [(None, True), ("unstamped", False), ("journal", False),
                                             ("live-lock", False), ("no-next", False)])
def test_a_pending_swap_is_read_from_the_real_dbswap_state(tmp_path, monkeypatch, change, pending):
    home = _swap_world(tmp_path, monkeypatch, "x4raw", *(() if change == "no-next" else ("x4raw__next",)))
    stamped = {"db": "x4raw__next", "fingerprint": {"content": "c", "engine": "e"}}
    (home / "coverage-x4raw__next.json").write_text(json.dumps({} if change == "unstamped" else stamped),
                                                    encoding="utf-8")
    if change == "journal":
        (home / "swap-x4raw.json").write_text('{"db": "x4raw", "phase": "begun"}', encoding="utf-8")
    if change == "live-lock":
        (home / "build-x4raw.lock").write_text(json.dumps({"db": "x4raw", "pid": os.getpid(), "at": time.time()}),
                                               encoding="utf-8")
    assert fc._basex_swap_pending("x4raw") is pending


def _record(sd):
    return json.loads((sd / "basex-auto.json").read_text(encoding="utf-8"))


def test_the_off_switch_never_overwrites_a_running_builders_record(sd, bxa, monkeypatch):
    """4.1.1 review: with X4_FRESH_BASEX_AUTO=off the plan wrote "skipped" before looking at basex-auto.lock, so
    `status` said skipped while a builder (started before the switch) was still running."""
    _basex_stale(monkeypatch)
    _seen(sd, 60)
    monkeypatch.setenv("X4_FRESH_BASEX_AUTO", "off")
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "basex-auto.json").write_text(json.dumps({"state": "running", "pid": 1, "when": "2026-11-07 10:00"}),
                                        encoding="utf-8")
    held = fc.BuildLock(sd / fc.BASEX_AUTO_LOCK)
    assert held.acquire()
    try:
        fc.rebuild(auto=True)
    finally:
        held.release()
    assert _record(sd)["state"] == "running"
    assert "already scheduled or running" in _note(sd) and not bxa["spawned"]


def test_TWIN_the_off_switch_with_no_builder_running_records_skipped(sd, bxa, monkeypatch):
    _basex_stale(monkeypatch)
    _seen(sd, 60)
    monkeypatch.setenv("X4_FRESH_BASEX_AUTO", "off")
    fc.rebuild(auto=True)
    assert _record(sd)["state"] == "skipped" and "X4_FRESH_BASEX_AUTO=off" in _record(sd)["detail"]


@pytest.mark.parametrize("var", ["X4_FRESH_AUTO", "X4_FRESH_BASEX_AUTO"])
def test_each_off_switch_inside_the_background_builder_builds_nothing_and_says_so(sd, bxa, monkeypatch, var):
    """4.1.1 review: the builder returned 0 with no record at all, so `status` kept whatever an earlier run said."""
    order = _child_world(monkeypatch)
    _seen(sd, 60)
    monkeypatch.setenv(var, "off")
    assert fc._basex_child(sd / "session-note.txt") == 0
    assert order == []
    rec = _record(sd)
    assert rec["state"] == "skipped" and f"{var}=off" in rec["detail"], rec


def test_a_lock_probe_racing_the_builders_start_does_not_stop_it(sd, bxa, monkeypatch):
    """`status` and a session start PROBE basex-auto.lock by taking it for an instant. A builder starting in that
    instant returned 0 having built nothing and recorded nothing; it retries briefly instead."""
    order = _child_world(monkeypatch)
    _seen(sd, 60)
    refusals = {"left": 2}
    real = fc.BuildLock

    class Probed(real):
        def acquire(self):
            if self.path.name == fc.BASEX_AUTO_LOCK and refusals["left"]:
                refusals["left"] -= 1
                return False
            return super().acquire()
    monkeypatch.setattr(fc, "BuildLock", Probed)
    assert fc._basex_child(sd / "session-note.txt") == 0
    assert order == ["x4raw", "x4eff"] and _record(sd)["state"] == "finished"


def test_TWIN_a_builder_that_really_holds_the_lock_keeps_its_record(sd, bxa, monkeypatch):
    order = _child_world(monkeypatch)
    _seen(sd, 60)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "basex-auto.json").write_text(json.dumps({"state": "running", "pid": 1, "when": "then"}), encoding="utf-8")
    held = fc.BuildLock(sd / fc.BASEX_AUTO_LOCK)
    assert held.acquire()
    try:
        assert fc._basex_child(sd / "session-note.txt") == 0
    finally:
        held.release()
    assert order == [] and _record(sd)["state"] == "running"


def test_the_background_builder_never_hides_a_not_built_notice_it_overwrites(sd, bxa, monkeypatch):
    """The background builder writes the same note a session start wrote, half an hour later. That session start may
    have named a NOT-BUILT database for the first time (and recorded it as told); the builder's note replaced it, read
    "told", and left it out, so no session start ever printed it. The builder names NOT-BUILT rows and records
    nothing about them: that record belongs to the session-start runs."""
    order = _child_world(monkeypatch)
    monkeypatch.setitem(fc.CHECKS, "x4eff", lambda: fc._not_built("x4eff", "java is not on PATH."))
    _seen(sd, 60)
    sd.mkdir(parents=True, exist_ok=True)
    fc._write_note(fc._not_built_path(sd), json.dumps(["x4eff"]))
    fc._basex_child(sd / "session-note.txt")
    assert order == ["x4raw"]
    assert "x4eff" in _note(sd) and fc.NOT_BUILT in _note(sd), _note(sd)
    assert json.loads(fc._not_built_path(sd).read_text(encoding="utf-8")) == ["x4eff"]
    # Twin: a session-start run still names it only once -- test_the_session_note_names_not_built_once.


def test_TWIN_posix_starts_a_new_session_and_no_windows_flags(monkeypatch, tmp_path):
    spy = _PopenSpy()
    monkeypatch.setattr(fc.subprocess, "Popen", spy)
    monkeypatch.setattr(fc, "_on_windows", lambda: False, raising=False)
    fc._spawn_basex(tmp_path / "note.txt")
    assert spy.calls[-1]["start_new_session"] is True and "creationflags" not in spy.calls[-1]
