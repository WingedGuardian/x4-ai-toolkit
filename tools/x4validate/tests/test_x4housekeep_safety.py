"""x4housekeep: where a path really lands, the Forever class, and failures that must not read as success.

The rule these pin: permanent delete only for proven-rebuildable items with a producer receipt; the Recycle Bin for
anything that could be the only copy; unknown value is kept; a failed recycle never falls back to deletion.
MEASURED 2026-10-31: Windows' shell delete destroys a file on a network share silently (rc 0, no dialog), so the
refusal is the only protection there. Every guard has a twin: the same fixture minus the one property it keys on goes
through, so a guard that refuses everything cannot pass. The Recycle Bin is faked throughout.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("x4housekeep_safety", REPO / "scripts" / "x4housekeep.py")
hk = importlib.util.module_from_spec(_spec)
sys.modules["x4housekeep_safety"] = hk
_spec.loader.exec_module(hk)

_REAL_RECYCLE = hk.recycle
_REAL_CAPACITY = hk.bin_capacity
MB = 1024 * 1024
DAY = 86400
GIB = 1024 ** 3
win = pytest.mark.skipif(os.name != "nt", reason="Windows volumes, junctions and the shell's Recycle Bin")


@pytest.fixture(autouse=True)
def _never_the_real_bin(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the REAL recycle() was reached from a test")
    monkeypatch.setattr(hk, "recycle", refuse)
    monkeypatch.delenv("X4_HOUSEKEEP_SWEEP", raising=False)


def write(path, data=b"x", days=30):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - days * DAY
    os.utime(path, (t, t))
    return path


def age(root, days=30):
    t = time.time() - days * DAY
    for dp, ds, fs in os.walk(root, topdown=False):
        for n in fs:
            os.utime(Path(dp) / n, (t, t))
        os.utime(dp, (t, t))


def cfg_for(root, **over):
    data = {"root": str(root), "agent_output": [], "evidence_docs": []}
    data.update(over)
    p = root / "hk.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    cfg = hk.load_config(p)
    return cfg


def obsolete(cfg, *paths):
    for p in paths:
        cfg["obsolete_items"] = dict(cfg.get("obsolete_items") or {})
        cfg["obsolete_items"][str(p)] = dict(basis="operator-confirmed-obsolete", reason="fixture retired it",
                                             evidence="explicit test disposition",
                                             content_hash=hk.certainty.content_hash(p))
    return cfg


class FakeBin:
    def __init__(self, root):
        self.dir = root / "fake-bin"
        self.dir.mkdir(exist_ok=True)
        self.got = []

    def __call__(self, paths):
        for p in paths:
            self.got.append(str(p))
            os.replace(p, self.dir / "{}-{}".format(len(self.got), os.path.basename(p)))
        return {str(p): None for p in paths}


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    return link


@pytest.fixture
def bin_ok(monkeypatch):
    """A fixed local volume whose bin has room, so only the landing rules can refuse."""
    monkeypatch.setattr(hk, "_drive_type", lambda s: 3)
    monkeypatch.setattr(hk, "bin_capacity", lambda s: 10 * GIB)


# ---------------------------------------------------------------- I1: judge where the path really lands

@win
def test_a_path_under_a_junction_is_refused_and_its_real_spelling_is_not(tmp_path, bin_ok):
    real = write(tmp_path / "real" / "only-copy.bin")
    link = junction(tmp_path / "via", tmp_path / "real")
    assert "link" in hk.bin_refusal(link / "only-copy.bin")
    assert hk.bin_refusal(real) == ""                     # twin: the same bytes by their own path


@win
def test_the_junction_itself_is_refused(tmp_path, bin_ok):
    write(tmp_path / "real" / "f.bin")
    link = junction(tmp_path / "via", tmp_path / "real")
    assert "link" in hk.bin_refusal(link)


@win
@pytest.mark.parametrize("real,word", [("\\\\fileserver\\share\\f.bin", "network"), ("Q:\\elsewhere\\f.bin", "volume"),
                                       ("\\\\?\\Volume{1234}\\f.bin", "network")])
def test_a_path_that_really_lands_elsewhere_is_refused_with_no_link_on_the_way(tmp_path, bin_ok, monkeypatch, real, word):
    # A SUBST drive or a volume with no letter: no reparse point to see, only realpath tells.
    f = write(tmp_path / "f.bin")
    monkeypatch.setattr(hk.os.path, "realpath", lambda p, **k: real)
    assert word in hk.bin_refusal(f)


@win
def test_a_real_path_on_a_remote_drive_letter_is_refused(tmp_path, monkeypatch):
    f = write(tmp_path / "f.bin")
    here = os.path.splitdrive(str(f))[0].upper()
    remote = "Z:" if here != "Z:" else "Y:"
    monkeypatch.setattr(hk.os.path, "realpath", lambda p, **k: remote + "\\f.bin")
    monkeypatch.setattr(hk, "_drive_type", lambda s: 4 if s.upper().startswith(remote) else 3)
    monkeypatch.setattr(hk, "bin_capacity", lambda s: 10 * GIB)
    assert hk.bin_refusal(f)


@win
def test_a_mapped_network_drive_is_refused_by_its_own_letter(tmp_path, monkeypatch):
    f = write(tmp_path / "f.bin")
    monkeypatch.setattr(hk, "_drive_type", lambda s: 4)
    monkeypatch.setattr(hk, "bin_capacity", lambda s: 10 * GIB)
    assert "network" in hk.bin_refusal(f)


def test_a_bare_network_path_is_refused_before_any_stat(monkeypatch):
    def no_look(*_a, **_k):
        raise AssertionError("looked at the share")
    monkeypatch.setattr(hk, "_is_link", no_look)
    monkeypatch.setattr(hk, "_drive_type", no_look)
    if os.name == "nt":
        assert "network" in hk.landing_refusal("\\\\fileserver\\share\\f.bin")
        assert "network" in hk.landing_refusal("//fileserver/share/f.bin")


@win
def test_only_a_case_or_short_name_difference_is_not_a_landing_change(tmp_path, bin_ok, monkeypatch):
    # The twin that stops "any difference refuses": realpath expands 8.3 names (GitHub's RUNNER~1) and fixes case.
    f = write(tmp_path / "f.bin")
    monkeypatch.setattr(hk.os.path, "realpath", lambda p, **k: str(f).upper())
    assert hk.bin_refusal(f) == ""


@win
def test_a_landing_that_cannot_be_established_is_kept(tmp_path, bin_ok, monkeypatch):
    f = write(tmp_path / "f.bin")

    def broken(p, **k):
        raise OSError("no answer")
    monkeypatch.setattr(hk.os.path, "realpath", broken)
    assert "could not be established" in hk.bin_refusal(f)


@win
def test_a_link_inside_a_folder_is_refused(tmp_path, bin_ok):
    write(tmp_path / "elsewhere" / "keep.bin")
    write(tmp_path / "plain" / "a.bin")
    folder = write(tmp_path / "held" / "a.bin").parent
    junction(folder / "inner", tmp_path / "elsewhere")
    assert "link" in hk.bin_refusal(folder)
    assert hk.bin_refusal(tmp_path / "plain") == ""       # twin


@win
def test_an_unmeasurable_size_is_kept(tmp_path, bin_ok, monkeypatch):
    # Refusing is what keeps Windows' "too big, delete permanently?" prompt off an unattended desktop.
    folder = write(tmp_path / "d" / "a.bin").parent
    real_stat = os.stat

    def stat(p, *a, **k):
        if str(p).endswith("a.bin"):
            raise PermissionError(13, "denied", str(p))
        return real_stat(p, *a, **k)
    monkeypatch.setattr(hk.os, "stat", stat)
    assert "measured" in hk.bin_refusal(folder)


@win
def test_a_missing_item_is_not_a_size_failure(tmp_path, bin_ok):
    assert hk.bin_refusal(tmp_path / "gone.bin") == ""     # the shell then reports it; nothing can be lost


@win
def test_recycle_never_hands_a_linked_path_to_the_shell(tmp_path, bin_ok, monkeypatch):
    write(tmp_path / "real" / "f.bin")
    link = junction(tmp_path / "via", tmp_path / "real")
    target = str(link / "f.bin")

    def shell(*_a, **_k):
        raise AssertionError("the shell was called")
    monkeypatch.setattr(hk.subprocess, "run", shell)
    result = _REAL_RECYCLE([target])
    assert "link" in result[target]
    assert Path(target).read_bytes() == b"x"              # no fallback: the bytes stay


def _class_fixture(cls, real):
    """A real plan action of class `cls` under `real`, valid when reached by its own path."""
    (real / "out" / "task").mkdir(parents=True)
    write(real / "CLAUDE.md", b"")
    over = {"agent_output": ["out"], "live_roots": ["game"], "min_bytes": 1, "launch_logs": ["logs"],
            "scratch": ["scratch-bed"]}
    if cls == "recreatable":
        write(real / "game" / "01.cat", b"C" * MB)
        write(real / "out" / "task" / "copy.cat", b"C" * MB)
    elif cls == "build":
        write(real / "out" / "task" / "build" / "a.obj", b"O" * 100)
    elif cls == "link":
        write(real / "out" / "task" / "a.bin", b"D" * MB)
        write(real / "out" / "task2" / "a.bin", b"D" * MB)
    elif cls == "gzip":
        write(real / "logs" / "launch-{}".format(time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - 30 * DAY)))
              / "big.log", b"L" * 70000)
    elif cls == "launch":
        write(real / "logs" / "launch-{}".format(time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - 200 * DAY)))
              / "a.log", b"L" * 10)
    else:
        write(real / "scratch-bed" / "a.bin", b"S" * 10)
    age(real)
    cfg = cfg_for(real, **over)
    if cls in ("build", "launch", "scratch"):
        target = {"build": real / "out" / "task" / "build" / "a.obj"}.get(cls)
        if cls == "launch":
            target = next((real / "logs").iterdir())
        elif cls == "scratch":
            target = real / "scratch-bed"
        obsolete(cfg, target)
        age(real)
    return cfg


@win
@pytest.mark.parametrize("cls", ["recreatable", "build", "link", "gzip", "launch", "scratch"])
@pytest.mark.parametrize("via_link", [True, False])
def test_every_recycling_class_is_judged_by_where_it_lands(tmp_path, cls, via_link, capsys):
    real = tmp_path / "real"
    real.mkdir()
    cfg = _class_fixture(cls, real)
    doc = hk.plan(cfg, only={cls})
    actions = [a for a in doc["actions"] if a["class"] == cls]
    assert actions, doc["rows"]
    fake = FakeBin(tmp_path)
    if via_link:
        link = junction(tmp_path / "via", real)
        swap = lambda v: v.replace(str(real), str(link)) if isinstance(v, str) else v
        doc = dict(doc, actions=[{k: swap(v) for k, v in a.items()} for a in actions])
        cfg = dict(cfg, root=link, obsolete_items={swap(k): v for k, v in cfg["obsolete_items"].items()},
                   agent_output=[str(link / "out")], live_roots=[str(link / "game")],
                   launch_logs=[str(link / "logs")], scratch=[str(link / "scratch-bed")])
    before = sorted(str(p) for p in real.rglob("*") if p.is_file())
    rc = hk.apply(doc, cfg, only={cls}, recycler=fake, now=time.time())
    out = capsys.readouterr().out
    if via_link:
        assert rc == 1 and "link" in out, out
        assert fake.got == []
        assert sorted(str(p) for p in real.rglob("*") if p.is_file()) == before
    else:
        assert rc == 0, out
        assert fake.got or cls == "link"


@win
def test_an_agent_output_root_behind_a_junction_is_held_by_the_plan(tmp_path):
    write(tmp_path / "real" / "out" / "task" / "a.bin", b"D" * MB)
    write(tmp_path / "real" / "out" / "task2" / "a.bin", b"D" * MB)
    age(tmp_path / "real")
    link = junction(tmp_path / "via", tmp_path / "real")
    doc = hk.plan(cfg_for(tmp_path, agent_output=[str(link / "out")], min_bytes=1))
    assert doc["actions"] == []
    assert doc["coverage"]["held"] and "link" in doc["coverage"]["held"][0]["reason"]
    twin = hk.plan(cfg_for(tmp_path, agent_output=[str(tmp_path / "real" / "out")], min_bytes=1))
    assert [a["class"] for a in twin["actions"]] == ["link"]


# ---------------------------------------------------------------- M9: the bin's own settings

class FakeRegistry:
    """Replaces winreg's read path only: this suite never reads the machine's Recycle Bin settings."""
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"

    def __init__(self, values):
        self.values = values                                # {(hive, key-substring): {name: value}}

    def OpenKey(self, hive, key):
        for (h, part), vals in self.values.items():
            if h == hive and part in key:
                return _Key(vals)
        raise FileNotFoundError(key)

    @staticmethod
    def QueryValueEx(k, name):
        if name not in k.vals:
            raise FileNotFoundError(name)
        return k.vals[name], 4


class _Key:
    def __init__(self, vals):
        self.vals = vals

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


BIT = "BitBucket"
POL = "Policies\\Explorer"


def _registry(monkeypatch, values, drive=3):
    monkeypatch.setitem(sys.modules, "winreg", FakeRegistry(values))
    monkeypatch.setattr(hk, "_drive_type", lambda s: drive)
    monkeypatch.setattr(hk, "bin_capacity", _REAL_CAPACITY)


@win
@pytest.mark.parametrize("hive", ["HKCU", "HKLM"])
def test_the_no_recycle_policy_means_no_bin(tmp_path, monkeypatch, hive):
    _registry(monkeypatch, {("HKCU", BIT): {"MaxCapacity": 10000}, (hive, POL): {"NoRecycleFiles": 1}})
    assert hk.bin_capacity(str(tmp_path)) == 0


@win
def test_the_no_recycle_policy_set_to_zero_leaves_the_bin_alone(tmp_path, monkeypatch):
    _registry(monkeypatch, {("HKCU", BIT): {"MaxCapacity": 10000}, ("HKCU", POL): {"NoRecycleFiles": 0}})
    assert hk.bin_capacity(str(tmp_path)) == 10000 * MB


@win
def test_a_stale_key_on_a_removable_volume_proves_nothing(tmp_path, monkeypatch):
    # Windows keeps BitBucket\Volume\{guid} for a USB stick it has seen; a removable volume has no bin to go to.
    _registry(monkeypatch, {("HKCU", BIT): {"MaxCapacity": 10000}}, drive=2)
    assert hk.bin_capacity(str(tmp_path)) is None


@win
def test_a_fixed_volume_with_the_same_key_is_trusted(tmp_path, monkeypatch):
    _registry(monkeypatch, {("HKCU", BIT): {"MaxCapacity": 10000}}, drive=3)
    assert hk.bin_capacity(str(tmp_path)) == 10000 * MB


# ---------------------------------------------------------------- I4: the Forever class holds for task folders

def _archive_cfg(root):
    return cfg_for(root, agent_output=["out"], archive_enabled=True, archive_root=str(root / "arch"))


def test_a_task_holding_a_backup_folder_or_named_for_one_is_not_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(hk, "reads_recorded", lambda folder: True)
    for name, member in (("task-a", "backups/only-copy.txt"), ("my-backup-store", "only-copy.txt"),
                         ("task-plain", "plain/notes.txt")):
        write(tmp_path / "out" / name / member, b"only copy")
        write(tmp_path / "out" / name / ".housekeep-closed", b"")
    write(tmp_path / "out" / "task-empty-backup" / "plain.txt", b"x")
    (tmp_path / "out" / "task-empty-backup" / "old-backup").mkdir()
    write(tmp_path / "out" / "task-empty-backup" / ".housekeep-closed", b"")
    age(tmp_path)
    doc = hk.plan(_archive_cfg(tmp_path), only={"archive"})
    assert [Path(a["path"]).name for a in doc["actions"]] == ["task-plain"], doc["rows"]
    guard = hk.Guard(_archive_cfg(tmp_path))
    why = lambda name: hk.is_done(tmp_path / "out" / name, 30, set(), [], guard)
    assert why("task-a") == why("my-backup-store") == why("task-empty-backup") == "holds backup folder"
    assert why("task-plain") == ""


def test_the_apply_recheck_refuses_a_backup_folder_that_appeared_after_the_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(hk, "reads_recorded", lambda folder: True)
    t = tmp_path / "out" / "task"
    write(t / "a.txt", b"x")
    write(t / ".housekeep-closed", b"")
    age(tmp_path)
    cfg = _archive_cfg(tmp_path)
    action = hk.plan(cfg, only={"archive"})["actions"][0]
    (t / "backups").mkdir()
    write(t / "backups" / "b.txt", b"x")
    age(tmp_path)
    assert "backup folder" in hk.check(dict(action, fingerprint=hk.fingerprint(t)), cfg, hk.Guard(cfg), time.time())


def test_a_configured_source_root_named_backups_is_the_operators_choice(tmp_path, monkeypatch):
    # The twin: `archive_roots` naming a store is explicit. Only what sits inside a task is the Forever class.
    monkeypatch.setattr(hk, "reads_recorded", lambda folder: True)
    write(tmp_path / "backups" / "old-gen" / "x.bin", b"x")
    age(tmp_path)
    cfg = _archive_cfg(tmp_path)
    cfg["archive_roots"] = {str(tmp_path / "backups"): 14}
    assert [Path(a["path"]).name for a in hk.plan(cfg, only={"archive"})["actions"]] == ["old-gen"]


# ---------------------------------------------------------------- M1: a glob under a backup-named path

def _snapshots(root, store, globs):
    for stamp in ("20260101_000000", "20260102_000000", "20260103_000000"):
        write(store / stamp / "keepme.dat", b"same")
    age(root)
    return cfg_for(root, legacy_snapshots=[str(store)], protected_globs=globs)


def test_protected_globs_hold_under_a_backup_named_store(tmp_path):
    cfg = _snapshots(tmp_path, tmp_path / "tool-backups" / "snaps", ["*/keepme.dat"])
    assert hk.plan(cfg, only={"snapshot"})["actions"] == []


def test_a_glob_that_does_not_match_leaves_the_rolling_store_rule_alone(tmp_path):
    cfg = _snapshots(tmp_path, tmp_path / "tool-backups" / "snaps", ["*/other.dat"])
    assert len(hk.plan(cfg, only={"snapshot"})["actions"]) == 1


# ---------------------------------------------------------------- M3: its own recycle is not someone else's write

def _two_classes(root):
    write(root / "game" / "01.cat", b"C" * MB)
    write(root / "out" / "task" / "copy.cat", b"C" * MB)
    obj = write(root / "out" / "task" / "build" / "x.obj", b"O" * 100)
    write(root / "CLAUDE.md", b"")
    age(root)
    cfg = cfg_for(root, agent_output=["out"], live_roots=["game"], min_bytes=1)
    return obsolete(cfg, obj), obj


def test_the_final_recheck_does_not_refuse_after_its_own_recycle(tmp_path, capsys):
    cfg, obj = _two_classes(tmp_path)
    age(tmp_path)
    doc = hk.plan(cfg)
    assert {a["class"] for a in doc["actions"]} == {"recreatable", "build"}
    fake = FakeBin(tmp_path)
    assert hk.apply(doc, cfg, recycler=fake) == 0, capsys.readouterr().out
    assert not obj.exists()


def test_the_final_recheck_still_sees_a_real_write_in_that_folder(tmp_path, capsys):
    cfg, obj = _two_classes(tmp_path)
    age(tmp_path)
    doc = hk.plan(cfg)
    fake = FakeBin(tmp_path)

    def and_another_writer(paths):
        result = fake(paths)
        if any("copy.cat" in str(p) for p in paths):
            (tmp_path / "out" / "task" / "new-work.txt").write_text("another session")
        return result
    assert hk.apply(doc, cfg, recycler=and_another_writer) == 1
    assert "active" in capsys.readouterr().out
    assert obj.exists()


def test_the_final_recheck_ignores_only_folders_it_changed(tmp_path):
    # The clause: ignoring a folder's own mtime must not hide a NEW FILE inside it, nor a folder it never touched.
    f = write(tmp_path / "t" / "sub" / "a.txt")
    age(tmp_path)
    old = hk.newest_mtime(tmp_path / "t")
    os.utime(tmp_path / "t" / "sub", None)
    fresh = hk.newest_mtime(tmp_path / "t")
    assert fresh > old
    assert hk.newest_mtime(tmp_path / "t", own=[hk._key(tmp_path / "t" / "sub")]) == old
    assert hk.newest_mtime(tmp_path / "t", own=[hk._key(tmp_path / "t")]) == fresh
    f.write_text("newer")
    assert hk.newest_mtime(tmp_path / "t", own=[hk._key(tmp_path / "t" / "sub")]) > old


# ---------------------------------------------------------------- M4: ArchiveError is a refusal, never a traceback

def _archive_error():
    return hk._archive_module().ArchiveError("archive changed during proof")


def test_final_recheck_archive_error_is_a_refusal(tmp_path, monkeypatch, capsys):
    cfg, _ = _two_classes(tmp_path)
    age(tmp_path)
    doc = hk.plan(cfg, only={"build"})
    fake = FakeBin(tmp_path)
    real_check, calls = hk.check, []

    def second_call_raises(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise _archive_error()
        return real_check(*a, **k)
    monkeypatch.setattr(hk, "check", second_call_raises)
    assert hk.apply(doc, cfg, only={"build"}, recycler=fake) == 1
    assert "changed during proof" in capsys.readouterr().out
    assert fake.got == []


@pytest.mark.parametrize("cmd", ["plan", "report"])
def test_a_file_changing_during_the_plan_is_a_clean_refusal(tmp_path, monkeypatch, capsys, cmd):
    write(tmp_path / "out" / "task" / "a.bin", b"D" * MB)
    write(tmp_path / "out" / "task2" / "a.bin", b"D" * MB)
    age(tmp_path)
    cfg_for(tmp_path, agent_output=["out"], min_bytes=1)

    def changed(*_a, **_k):
        raise _archive_error()
    monkeypatch.setattr(hk, "blake", changed)
    argv = ["--config", str(tmp_path / "hk.json"), "plan", "-o", str(tmp_path / "p.json")] if cmd == "plan" \
        else ["--config", str(tmp_path / "hk.json"), "report"]
    assert hk.main(argv) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err and "changed during proof" in err


def test_an_unexpected_programming_error_still_raises(tmp_path, monkeypatch):
    # The twin: the clean-refusal path must not swallow a real bug into an exit code.
    cfg_for(tmp_path, agent_output=[])
    monkeypatch.setattr(hk, "plan", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        hk.main(["--config", str(tmp_path / "hk.json"), "report"])


def test_a_stub_folder_is_not_archived_again_because_its_kept_evidence_is_large(tmp_path, monkeypatch):
    # Three kept top-level receipts can pass 1 MiB together. Planning them again only fails at apply, every run.
    monkeypatch.setattr(hk, "reads_recorded", lambda folder: True)
    t = tmp_path / "out" / "archived"
    write(t / "ARCHIVED.md", b"stub")
    write(t / ".housekeep-closed", b"")
    for i in range(3):
        write(t / "receipt{}.json".format(i), b"x" * (900 * 1024))
    other = tmp_path / "out" / "grew-after"                   # the twin: real content beside a stub is still surfaced,
    #                                                           as a note: planned, it could only fail at apply, every run
    write(other / "ARCHIVED.md", b"stub")
    write(other / ".housekeep-closed", b"")
    write(other / "payload" / "new.bin", b"x" * (2 * MB))
    age(tmp_path)
    doc = hk.plan(_archive_cfg(tmp_path), only={"archive"})
    assert doc["actions"] == []
    notes = doc["coverage"]["archive_inventory"]["stub_notes"]
    assert len(notes) == 1 and "grew-after" in notes[0], notes


# ---------------------------------------------------------------- docs-review M16: the optional job ledger

def _plan_with_ledger(tmp_path, monkeypatch, ledger_text, name="ledger.json"):
    monkeypatch.setattr(hk, "reads_recorded", lambda folder: True)
    write(tmp_path / "out" / "task" / "a.txt")
    write(tmp_path / "out" / "task" / ".housekeep-closed", b"")
    age(tmp_path)
    cfg = _archive_cfg(tmp_path)
    if ledger_text is not None:
        ledger = write(tmp_path / name, ledger_text.encode("utf-8"))
        cfg["job_ledger"] = str(ledger)
    return cfg


@pytest.mark.parametrize("bad", ["{not json", "[]", '{"entries": 5}', '{"entries": [7]}', '{"entries": [{"cwd": 3}]}'])
def test_a_malformed_job_ledger_does_not_hold_archiving_and_says_so(tmp_path, monkeypatch, capsys, bad):
    cfg = _plan_with_ledger(tmp_path, monkeypatch, bad)
    doc = hk.plan(cfg, only={"archive"})
    assert [a["class"] for a in doc["actions"]] == ["archive"]
    hk.print_coverage(doc)
    assert "job ledger" in capsys.readouterr().out                     # a narrowed check announces itself


def test_a_readable_job_ledger_still_holds_a_running_jobs_folder(tmp_path, monkeypatch):
    cfg = _plan_with_ledger(tmp_path, monkeypatch, "{}")
    (tmp_path / "ledger.json").write_text(json.dumps({"entries": [{"cwd": str(tmp_path / "out" / "task")}]}))
    age(tmp_path)
    assert hk.plan(cfg, only={"archive"})["actions"] == []


def test_with_no_ledger_configured_none_is_read(tmp_path, monkeypatch):
    home = tmp_path / "home"
    write(home / ".claude" / "tools" / "resource-budget" / "ledger.json",
          json.dumps({"entries": [{"cwd": str(tmp_path / "out" / "task")}]}).encode())
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    cfg = _plan_with_ledger(tmp_path, monkeypatch, None)
    assert [a["class"] for a in hk.plan(cfg, only={"archive"})["actions"]] == ["archive"]


def test_the_shipped_code_names_no_private_tool_path():
    src = (REPO / "scripts" / "x4housekeep.py").read_text(encoding="utf-8")
    assert "resource-budget" not in src and '".claude" / "tools"' not in src


def test_the_unknown_key_check_accepts_job_ledger(tmp_path):
    assert cfg_for(tmp_path, job_ledger="x.json")["job_ledger"] == "x.json"


# ---------------------------------------------------------------- saves: "every copy"

@pytest.mark.parametrize("name", ["save_001.xml.gz", "quicksave.xml.gz", "autosave_01.xml.gz", "save_012.xml",
                                  "QUICKSAVE.XML.GZ"])
def test_a_save_copy_outside_save_is_recognised_by_its_name(name):
    low = "c:/out/task/" + name.lower()
    assert hk.is_save(low)
    assert hk._archive_module().is_save("C:/out/task/" + name)


@pytest.mark.parametrize("name", ["library.xml.gz", "wares.xml", "save_notes.xml", "save_001.xml.gz.txt",
                                  "my_quicksave.xml.gz", "autosave.xml", "save.json"])
def test_ordinary_names_are_not_saves(name):
    low = "c:/out/task/" + name
    assert not hk.is_save(low)
    assert not hk._archive_module().is_save("C:/out/task/" + name)


def test_the_existing_save_rules_still_hold():
    assert hk.is_save("c:/users/x/documents/egosoft/x4/123/content.xml")
    assert hk.is_save("c:/out/save/renamed.xml.gz")


def _gz(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as f:
        f.write(text.encode("utf-8"))
    return path


def test_a_renamed_save_is_recognised_by_its_content_and_its_read_time_is_kept(tmp_path):
    cfg = cfg_for(tmp_path, agent_output=["out"])
    guard = hk.Guard(cfg)
    copy = _gz(tmp_path / "out" / "task" / "pre-mod-test.xml.gz", '<?xml version="1.0"?>\n<savegame>\n<info/>')
    t = time.time() - 30 * DAY
    os.utime(copy, (t, t))
    assert guard.why_protected(copy) == "save"
    assert abs(copy.stat().st_atime - t) < 5              # a plan's own read must not trip the 48-hour read veto


@pytest.mark.parametrize("name,text", [("wares.xml.gz", "<wares><savegame-like/></wares>"), ("big.xml.gz", "<a/>")])
def test_other_gzipped_xml_is_not_a_save(tmp_path, name, text):
    cfg = cfg_for(tmp_path, agent_output=["out"])
    assert hk.Guard(cfg).why_protected(_gz(tmp_path / "out" / "task" / name, text)) == ""


def test_plain_xml_save_and_plain_xml_other(tmp_path):
    cfg = cfg_for(tmp_path, agent_output=["out"])
    guard = hk.Guard(cfg)
    assert guard.why_protected(write(tmp_path / "out" / "t" / "a.xml", b'<?xml version="1.0"?><savegame>')) == "save"
    assert guard.why_protected(write(tmp_path / "out" / "t" / "b.xml", b'<?xml version="1.0"?><wares>')) == ""


def test_a_corrupt_gz_is_treated_as_ordinary_content_not_a_crash(tmp_path):
    cfg = cfg_for(tmp_path, agent_output=["out"])
    assert hk.Guard(cfg).why_protected(write(tmp_path / "out" / "t" / "a.xml.gz", b"not gzip")) == ""


# ---------------------------------------------------------------- I8: the doc's promises

def test_a_large_pin_is_named_even_when_another_rule_holds_the_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hk, "reads_recorded", lambda folder: False)   # no read signal: closure-only drive
    write(tmp_path / "out" / "pinned-big" / "payload.bin", b"P" * 2000)
    write(tmp_path / "out" / "pinned-big" / ".housekeep-keep", b"")
    write(tmp_path / "out" / "unpinned-big" / "payload.bin", b"P" * 2000)
    age(tmp_path)
    cfg = _archive_cfg(tmp_path)
    cfg["pin_warn_gib"] = 1000 / GIB
    doc = hk.plan(cfg, only={"archive"})
    assert [Path(p).name for p, _ in doc["large_pins"]] == ["pinned-big"]
    hk.print_coverage(doc)
    out = capsys.readouterr().out
    assert "pinned-big" in out and "unpinned-big" not in out


def test_a_small_pin_is_not_named(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hk, "reads_recorded", lambda folder: True)
    write(tmp_path / "out" / "pinned-small" / "a.bin", b"P")
    write(tmp_path / "out" / "pinned-small" / ".housekeep-keep", b"")
    age(tmp_path)
    doc = hk.plan(_archive_cfg(tmp_path), only={"archive"})
    assert doc["large_pins"] == []
    hk.print_coverage(doc)
    assert "pinned-small" not in capsys.readouterr().out


def _retention():
    return (REPO / "RETENTION.md").read_text(encoding="utf-8")


def test_the_doc_states_the_idle_rule_the_code_applies():
    assert hk.DEFAULTS["archive_after_days"] == 14 and hk.DEFAULTS["min_idle_days"] == 7
    doc = _retention()
    assert "no write for 7 days" not in doc
    assert "`archive_after_days`" in doc and "`min_idle_days`" in doc and "`read_veto_hours`" in doc


def test_the_doc_names_the_session_sweep_as_the_exception_to_rules_6_and_7():
    doc = _retention()
    rule7 = doc[doc.index("7. **Never unattended"):doc.index("### Where agent output goes")]
    assert "temp sweep" in rule7.lower() and "detached" in rule7.lower()
    assert "Session start sweeps" in doc


def test_the_doc_says_what_stays_local_after_archiving():
    doc = _retention()
    row = next(line for line in doc.splitlines() if line.startswith("| **Finished work**"))
    for word in ("`.json`", "`.jsonl`", "`.log`", "1 MiB"):
        assert word in row, word


@pytest.mark.parametrize("cls", hk.CLASSES)
def test_every_class_is_in_the_shipped_lists(cls):
    assert cls in hk.__doc__.split("--only CLASS[,CLASS]]")[2].splitlines()[0], cls
    line = next(l for l in _retention().splitlines() if "runs the classes in a fixed order" in l)
    assert cls in line + _retention().split(line)[1].splitlines()[0], cls


# ---------------------------------------------------------------- the report's cost

def test_the_report_size_comes_from_the_directory_listing_not_a_stat_per_file(tmp_path, monkeypatch):
    # MEASURED 2026-11-03: a warm `report` over 200,847 objects under the agents' scratchpads took 32.9 s with two stats
    # per entry and 18.4 s without. The listing already holds the answer.
    write(tmp_path / "d" / "sub" / "a.bin", b"12345")
    write(tmp_path / "d" / "b.bin", b"123")

    def no_stat(self, *a, **k):
        raise AssertionError("a per-file stat")
    root = tmp_path / "d"
    monkeypatch.setattr(Path, "lstat", no_stat)
    monkeypatch.setattr(Path, "is_symlink", no_stat)
    assert hk.dir_size_counted(root) == (8, [], [])


# ---------------------------------------------------------------- I6: runtime state never enters a commit

@pytest.mark.parametrize("rel,ignored", [(".claude/diag/housekeep/task-archive-review.json", True),
                                         (".claude/diag/housekeep/backup-review.json", True),
                                         (".claude/archive/agent/task.zip", True),
                                         (".claude/x4fresh-session.txt", True),
                                         (".claude/hooks/session-canary.sh", False),
                                         (".claude/settings.json", False)])
def test_runtime_state_is_gitignored(rel, ignored):
    from _layout import require_repo
    require_repo(".git", why="asks git which paths it ignores")
    r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "--no-index", rel], capture_output=True)
    assert r.returncode == (0 if ignored else 1), rel
