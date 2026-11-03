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
    monkeypatch.setattr(fc, "_world_digest", lambda: "world")
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
    assert head == ("x4fresh: all 4 artifact(s) FRESH, but INCOMPLETE: effective (8 missing) -- review, "
                    "then `x4fresh accept` if this loss is known and acceptable")


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

EIGHT = [f"dropped assets/structures/defence/macros/tmp/d{i}.xml <- vro/assets/.../d{i}.xml" for i in range(8)]


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
        sc.add_overlay(f"a/{i}.xml", [f"vro/a/{i}.xml: malformed XML"])
    sc.add("t/0001-l044.xml", ValueError("bad"))
    assert len(sc.dropped_samples) == 10 and len(sc.keys) == 26
    assert "dropped a/3.xml <- vro/a/3.xml" in sc.keys and "unmerged t/0001-l044.xml" in sc.keys


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
    assert fc.rebuild(("effective",)) == 1
    out = capsys.readouterr().out
    assert "effective: the installed world MOVED during this build [a -> b]" in out


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
    assert fc.rebuild(("x4eff",)) == 1
    out = capsys.readouterr().out
    assert "x4eff: FAILED rc 0, now STALE" in out and "MOVED" in out
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
