"""scripts/x4housekeep.py: plan, then apply exactly the plan -- and never touch what the policy says is Forever.

Ported with the Skyrim toolkit's tests/test_housekeep.py, which carries the full set; this file pins the X4-specific
save rule and the guards a port could most easily lose. Every guard has a twin: the same bytes minus the one property
the guard keys on ARE planned, so a guard that plans nothing at all cannot pass. The Recycle Bin is faked.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("x4housekeep", REPO / "scripts" / "x4housekeep.py")
hk = importlib.util.module_from_spec(_spec)
sys.modules["x4housekeep"] = hk
_spec.loader.exec_module(hk)

MB = 1024 * 1024
DAY = 86400


def write(path, data, days=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - days * DAY
    os.utime(path, (t, t))
    return path


def blob(seed, size=2 * MB):
    return (seed.encode() * (size // len(seed) + 1))[:size]


def key(p):
    return os.path.normcase(str(p))


def planned(doc, cls=None):
    return {os.path.normcase(a["path"]) for a in doc["actions"] if cls is None or a["class"] == cls}


def cfg_for(root, **over):
    base = {"agent_output": ["out"], "live_roots": ["game"], "evidence_docs": ["CLAUDE.md"], "active_hours": 48}
    base.update(over)
    (root / "hk.json").write_text(json.dumps(base), encoding="utf-8")
    cfg = hk.load_config(root / "hk.json")
    cfg["root"] = root
    return cfg


class FakeBin:
    def __init__(self, root):
        self.bin = root / "bin"
        self.bin.mkdir(exist_ok=True)
        self.got = []

    def __call__(self, paths):
        for p in paths:
            self.got.append(p)
            os.replace(p, self.bin / "{}-{}".format(len(self.got), os.path.basename(p)))
        return {p: None for p in paths}


@pytest.fixture
def tree(tmp_path):
    write(tmp_path / "game" / "01.cat", blob("catalog"))
    (tmp_path / "CLAUDE.md").write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("rel", ["t/Egosoft/X4/PROFILE/save/save_001.xml.gz",
                                 "t/Egosoft/X4/PROFILE/content.xml",
                                 "t/profile-copy/save/quicksave.xml.gz"])
def test_x4_saves_and_the_profile_are_never_planned(tree, rel):
    f = write(tree / "out" / rel, blob("catalog"))
    twin = write(tree / "out" / "t" / "plain" / "01.cat", blob("catalog"))
    doc = hk.plan(cfg_for(tree))
    assert key(f) not in planned(doc)
    assert key(twin) in planned(doc), "twin: the same bytes outside a save/profile path must be planned"


def test_a_copy_of_a_live_game_file_is_recreatable_and_a_variant_is_not(tree):
    same = write(tree / "out" / "t1" / "01.cat", blob("catalog"))
    v = bytearray(blob("catalog"))
    v[0] ^= 1
    off = write(tree / "out" / "t2" / "01.cat", bytes(v))
    doc = hk.plan(cfg_for(tree))
    assert key(same) in planned(doc, "recreatable")
    assert key(off) not in planned(doc, "recreatable")


def test_a_snapshot_of_a_live_state_file_is_not_recreatable(tree):
    write(tree / "game" / "registry.xml", blob("state"))
    snap = write(tree / "out" / "t" / "registry.xml", blob("state"))
    twin = write(tree / "out" / "u" / "01.cat", blob("catalog"))
    doc = hk.plan(cfg_for(tree))
    assert key(snap) not in planned(doc, "recreatable")
    assert key(twin) in planned(doc, "recreatable")


def test_backup_folders_and_git_tracked_files_are_never_planned(tree):
    backup = write(tree / "out" / "t" / "known-good-backups" / "01.cat", blob("catalog"))
    tracked = write(tree / "out" / "u" / "01.cat", blob("catalog"))
    twin = write(tree / "out" / "v" / "01.cat", blob("catalog"))
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    subprocess.run(["git", "-C", str(tree), "add", "-f", "out/u/01.cat"], check=True)
    doc = hk.plan(cfg_for(tree))
    assert not {key(backup), key(tracked)} & planned(doc)
    assert key(twin) in planned(doc)


def test_an_active_task_folder_and_a_loose_root_file_are_never_planned(tree):
    busy = write(tree / "out" / "busy" / "01.cat", blob("catalog"))
    write(tree / "out" / "busy" / "n.md", b"x", days=0)
    loose = write(tree / "out" / "events.jsonl", blob("log"))
    write(tree / "out" / "a" / "events.jsonl", blob("log"))
    twin = write(tree / "out" / "b" / "events.jsonl", blob("log"))
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    assert not {key(busy), key(loose)} & planned(doc)
    assert key(loose) not in {key(a.get("canonical", "")) for a in doc["actions"]}
    assert key(twin) in planned(doc, "link")
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 0
    assert os.access(loose, os.W_OK)


def test_apply_refuses_a_file_changed_since_the_plan(tree):
    f = write(tree / "out" / "t" / "01.cat", blob("catalog"))
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    data = bytearray(f.read_bytes())
    data[1] ^= 1
    write(f, bytes(data))
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 1
    assert f.exists()


def test_duplicates_become_one_read_only_copy(tree):
    a = write(tree / "out" / "a" / "x.log", blob("dup"))
    b = write(tree / "out" / "b" / "x.log", blob("dup"))
    cfg = cfg_for(tree)
    assert hk.apply(hk.plan(cfg), cfg, recycler=FakeBin(tree)) == 0
    assert a.read_bytes() == b.read_bytes() and os.stat(a).st_ino == os.stat(b).st_ino
    assert not os.access(b, os.W_OK)


def test_rows_sum_to_every_byte_scanned(tree):
    write(tree / "out" / "a" / "01.cat", blob("catalog"))
    write(tree / "out" / "a" / "Egosoft" / "X4" / "1" / "save" / "s.xml.gz", b"save")
    write(tree / "out" / "b" / "small.txt", b"tiny")
    write(tree / "out" / "loose.json", b"{}")
    scanned = sum(p.stat().st_size for p in (tree / "out").rglob("*") if p.is_file())
    assert sum(hk.plan(cfg_for(tree))["rows"].values()) == scanned


def test_the_example_config_loads():
    ex = REPO / "x4-housekeep.json.example"
    hk.load_config(ex)
    assert set(json.loads(ex.read_text(encoding="utf-8"))) - {"_comment"} <= set(hk.DEFAULTS)


# --------------------------------------------------------------------------- temp sweep (the one permanent delete)

def stale(d, days=3):
    t = time.time() - days * DAY
    for p in sorted(d.rglob("*"), key=lambda p: -len(p.parts)) + [d]:
        os.utime(p, (t, t))
    return d


def tdir(temp, name, days=3):
    d = temp / name
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f.txt").write_text("x", encoding="utf-8")
    return stale(d, days)


def sweep_cfg(tmp_path):
    cfg = cfg_for(tmp_path)
    cfg["temp_prefixes"] = ["x4qa_", "x4-pytest-batch-"]
    return cfg


def test_the_sweep_deletes_a_stale_toolkit_temp_dir_and_keeps_a_foreign_one(tmp_path):
    temp = tmp_path / "temp"
    ours = tdir(temp, "x4qa_registry_abc123")
    theirs = tdir(temp, "tmpabc123")                          # twin: same age, a name anyone could make
    res = hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=FakeBin(tmp_path))
    assert not ours.exists() and theirs.exists()
    assert res["rows"]["recycled"] == 1


def test_the_sweep_keeps_a_dir_whose_child_changed_recently(tmp_path):
    temp = tmp_path / "temp"
    busy = tdir(temp, "x4qa_out_busy01")
    (busy / "new.txt").write_text("y", encoding="utf-8")
    os.utime(busy, (time.time() - 3 * DAY,) * 2)
    idle = tdir(temp, "x4qa_out_idle01")                      # twin: nothing recent
    res = hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=FakeBin(tmp_path))
    assert busy.exists() and not idle.exists()
    assert res["rows"]["kept: changed recently"] == 1


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to rename a dir with an open file inside")
def test_the_sweep_keeps_a_dir_a_process_has_a_file_open_in(tmp_path):
    temp = tmp_path / "temp"
    held = tdir(temp, "x4-pytest-batch-held01")
    free = tdir(temp, "x4-pytest-batch-free01")                # twin: nothing open
    with open(held / "sub" / "f.txt", encoding="utf-8"):
        res = hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=FakeBin(tmp_path))
    assert held.exists() and not free.exists()
    assert res["rows"]["kept: in use"] == 1


def test_the_sweep_keeps_the_newest_pytest_run_and_removes_older_runs_and_garbage(tmp_path):
    base = tmp_path / "temp" / "pytest-of-someone"
    old, newest, garbage = tdir(base, "pytest-9"), tdir(base, "pytest-10"), tdir(base, "garbage-0d1e")
    hk.sweep_temp(sweep_cfg(tmp_path), temp=tmp_path / "temp", recycler=FakeBin(tmp_path))
    assert newest.exists() and not old.exists() and not garbage.exists()


def test_session_runs_at_most_once_per_interval_and_writes_a_note(tmp_path, monkeypatch):
    cfg = sweep_cfg(tmp_path)
    cfg.update(codex_sessions_warn_gib=10 ** 6, worktrees_warn=10 ** 6, free_floor_gib=0, sweep_every_hours=6)
    monkeypatch.setattr(hk.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(hk, "recycle", FakeBin(tmp_path))
    state = tmp_path / "note.txt"
    state.write_text("", encoding="utf-8")                   # a sweep ran just now
    left = tdir(tmp_path / "temp", "x4qa_thr001")
    hk.session(cfg, state)
    assert left.exists()
    os.utime(state, (time.time() - 7 * 3600,) * 2)           # twin: the last sweep was 7 hours ago
    hk.session(cfg, state)
    assert not left.exists()
    assert state.read_text(encoding="utf-8").startswith("[x4 disk] moved 1 leftover temp dir(s) to the Recycle Bin")


def test_a_dir_the_bin_refuses_is_left_in_place_and_named(tmp_path):
    """Never a permanent delete, not even as a fallback: what the Recycle Bin will not take stays put."""
    temp = tmp_path / "temp"
    refused, taken = tdir(temp, "x4qa_big001"), tdir(temp, "x4qa_ok0001")    # twin: the bin takes this one
    fake = FakeBin(tmp_path)

    def picky(paths):
        out = {}
        for p in paths:
            out.update({str(p): "too big for the Recycle Bin"} if "big" in str(p) else fake([p]))
        return out
    res = hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=picky)
    assert refused.exists() and not taken.exists()
    assert res["rows"]["failed"] == 1 and "x4qa_big001" in res["failed"][0]


def test_the_tool_never_deletes_permanently():
    """User ruling 2026-10-18: the Recycle Bin, always -- the tool's own files included."""
    import re
    src = (REPO / "scripts" / "x4housekeep.py").read_text(encoding="utf-8")
    for call in ("os.unlink(", "os.remove(", "shutil.rmtree(", ".unlink(", "os.rmdir(", ".rmdir(",
                 "FileSystem]::Delete"):
        assert call not in src, call
    flags = re.findall(r"fFlags = ([^}]+?) \}", src)
    assert len(flags) == 1 and {"0x4000", "0x40"} <= set(re.findall(r"0x[0-9A-Fa-f]+", flags[0])), flags
