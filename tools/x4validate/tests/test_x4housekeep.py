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
    # Model idle DIRECTORY metadata as well as file metadata. Fresh directories
    # around backdated files are active work and must now be retained.
    for directory, dirs, files in os.walk(root, topdown=False):
        if Path(directory) != root and dirs + files:
            newest = max((Path(directory) / name).stat().st_mtime for name in dirs + files)
            os.utime(directory, (newest, newest))
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


@pytest.fixture(autouse=True)
def _never_the_real_bin(monkeypatch):
    """No test may reach the real Recycle Bin (SHFileOperation, or an OS prompt): the module's recycle() raises here.
    A test that needs a bin injects FakeBin explicitly. The harness opt-out is cleared so session() runs."""
    def refuse(*_a, **_k):
        raise AssertionError("the REAL recycle() was reached from a test")
    monkeypatch.setattr(hk, "recycle", refuse)
    monkeypatch.delenv("X4_HOUSEKEEP_SWEEP", raising=False)


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


@pytest.mark.parametrize("name", ["plain.log", "report final.log", "report(final).log", "report [final] '&.log"])
@pytest.mark.parametrize("form", ["angle", "encoded", "reference", "code", "prose"])
def test_linked_evidence_with_literal_odd_names_is_kept(tree, name, form):
    from urllib.parse import quote
    linked = write(tree / "out/task" / name, blob("catalog"))
    twin = write(tree / "out/unlinked" / name, blob("catalog"))
    rel = linked.relative_to(tree).as_posix()
    body = {"angle": f"[evidence](<{rel}>)", "encoded": f"[evidence]({quote(rel)})",
            "reference": f"[evidence]: <{rel}>", "code": f"`{rel}`", "prose": f"See {rel}."}[form]
    (tree / "CLAUDE.md").write_text(body, encoding="utf-8")
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    assert key(linked) not in planned(doc), (body, doc["actions"])
    assert key(twin) in planned(doc, "recreatable"), "unlinked same-name/same-byte twin must still qualify"


def test_a_link_added_after_planning_protects_the_evidence_at_apply(tree):
    linked = write(tree / "out/task/report(final).log", blob("catalog"))
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    assert key(linked) in planned(doc, "recreatable")
    (tree / "CLAUDE.md").write_text("[evidence](out/task/report(final).log)", encoding="utf-8")
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 1
    assert linked.read_bytes() == blob("catalog")


def test_overlong_evidence_reference_refuses_cleanup_instead_of_truncating(tree):
    write(tree / "out/task/report.log", blob("catalog"))
    (tree / "CLAUDE.md").write_text("[evidence](out/task/" + "x" * 32769 + ")", encoding="utf-8")
    with pytest.raises(OSError, match="discovery bound"):
        hk.plan(cfg_for(tree))


@pytest.mark.parametrize("ending", [".", "..."])
def test_prose_reference_retains_sentence_punctuation_under_posix_path_semantics(tree, monkeypatch, ending):
    import posixpath
    target = write(tree / "out/task/report.log", b"evidence")
    (tree / "CLAUDE.md").write_text("See out/task/report.log" + ending, encoding="utf-8")
    cfg = cfg_for(tree)
    # Windows abspath strips terminal dots; POSIX preserves them. Exercise that
    # lexical difference here as well as on the native Ubuntu CI leg.
    def posix_key(p):
        return posixpath.normpath(str(p).replace(chr(92), "/"))
    monkeypatch.setattr(hk, "_key", posix_key)
    assert posix_key(target) in hk.evidence_links(cfg)


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


@pytest.mark.parametrize("special,ordinary", [
    ("known-good-baseline/item.bin", "ordinary/item.bin"),
    ("known-good-baseline.zip", "ordinary.zip"),
    ("AUDIT_LOG.txt", "ordinary.txt"),
    ("GUARDS-OFF.log", "ordinary.log"),
])
def test_named_forever_baselines_and_audit_records_survive_whole_folder_cleanup(tree, special, ordinary):
    protected = write(tree / "out/protected" / special, b"evidence")
    write(tree / "out/plain" / ordinary, b"evidence")
    cfg = cfg_for(tree, scratch=["out/protected", "out/plain"])
    assert hk.Guard(cfg).why_protected(protected), special
    doc = hk.plan(cfg, only={"scratch"})
    assert planned(doc, "scratch") == {key(tree / "out/plain")}
    fake = FakeBin(tree)
    assert hk.apply(doc, cfg, recycler=fake) == 0
    assert protected.read_bytes() == b"evidence"
    assert not (tree / "out/plain").exists()


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


@pytest.mark.parametrize("side", ["canonical", "duplicate"])
def test_preexisting_outside_hardlink_never_changes_live_permissions(tree, side):
    import stat
    a = write(tree / "out/a/x.log", blob("dup"))
    b = write(tree / "out/b/x.log", blob("dup"))
    outside = tree / "unlisted-live.log"
    os.link(a if side == "canonical" else b, outside)
    if side == "duplicate":
        os.chmod(b, stat.S_IREAD)
    before = outside.stat().st_mode
    cfg = cfg_for(tree, live_roots=[])
    doc = hk.plan(cfg)
    hk.apply(doc, cfg, recycler=FakeBin(tree))
    assert outside.stat().st_mode == before, "deduplication changed an unlisted live inode's permissions"
    assert not doc["actions"], "pre-existing hardlink ownership was not qualified"
    assert outside.read_bytes() == blob("dup")


def test_three_independent_duplicates_can_link_in_one_apply(tree):
    files = [write(tree / "out" / n / "x.log", blob("dup")) for n in ("a", "b", "c")]
    cfg = cfg_for(tree, live_roots=[])
    assert hk.apply(hk.plan(cfg), cfg, recycler=FakeBin(tree)) == 0
    assert len({p.stat().st_ino for p in files}) == 1
    assert files[0].stat().st_nlink == 3


def test_hardlink_added_after_planning_refuses_before_live_chmod(tree):
    a = write(tree / "out/a/x.log", blob("dup"))
    write(tree / "out/b/x.log", blob("dup"))
    cfg = cfg_for(tree, live_roots=[])
    doc = hk.plan(cfg)
    outside = tree / "unlisted-live.log"
    os.link(a, outside)
    before = outside.stat().st_mode
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 1
    assert outside.stat().st_mode == before


def test_rows_sum_to_every_byte_scanned(tree):
    write(tree / "out" / "a" / "01.cat", blob("catalog"))
    write(tree / "out" / "a" / "Egosoft" / "X4" / "1" / "save" / "s.xml.gz", b"save")
    write(tree / "out" / "b" / "small.txt", b"tiny")
    write(tree / "out" / "loose.json", b"{}")
    scanned = sum(p.stat().st_size for p in (tree / "out").rglob("*") if p.is_file())
    assert sum(hk.plan(cfg_for(tree))["rows"].values()) == scanned


# --------------------------------------------------------------------------- whole-folder classes (review 2026-10-20)
# launch / snapshot / scratch recycle a FOLDER: they must consult the Forever list for every file inside it, not only
# the per-file classes. Each guard has a twin with the same layout minus the protected file.

def test_a_scratch_folder_holding_a_save_or_a_backup_is_never_planned(tree):
    write(tree / "scratchA" / "profile-copy" / "save" / "quicksave.xml.gz", b"save")
    write(tree / "scratchB" / "known-good-backups" / "w.xml", b"bk")
    write(tree / "scratchC" / "plain" / "w.xml", b"x")                       # twin: nothing protected inside
    cfg = cfg_for(tree, scratch=["scratchA", "scratchB", "scratchC"])
    doc = hk.plan(cfg)
    assert planned(doc, "scratch") == {key(tree / "scratchC")}
    # apply re-proves it: a plan forged before the save appeared is refused, not executed
    forged = {"class": "scratch", "path": str(tree / "scratchA"), "bytes": 4,
              "fingerprint": hk.fingerprint(tree / "scratchA")}
    assert hk.check(forged, cfg, hk.Guard(cfg), time.time()).startswith("protected")


def test_a_relative_scratch_path_is_the_toolkit_roots_never_the_cwd(tree, monkeypatch):
    write(tree / "scratch" / "f.txt", b"x")
    elsewhere = tree / "cwd"
    write(elsewhere / "scratch" / "keep.txt", b"y")
    monkeypatch.chdir(elsewhere)
    doc = hk.plan(cfg_for(tree, scratch=["scratch"]))
    assert planned(doc, "scratch") == {key(tree / "scratch")}, "resolved against the cwd"


@pytest.mark.parametrize("kind", ["scratch", "launch"])
def test_whole_folder_cleanup_keeps_recently_written_content(tree, kind):
    name = "scratch" if kind == "scratch" else "launches/launch-20200101-000000"
    active = write(tree / name / "active.log", b"current", days=0)
    idle_name = "idle" if kind == "scratch" else "launches/launch-20200102-000000"
    idle = write(tree / idle_name / "old.log", b"old")
    cfg = cfg_for(tree, **({"scratch": [name, idle_name]} if kind == "scratch" else {"launch_logs": ["launches"]}))
    doc = hk.plan(cfg)
    assert key(active.parent) not in planned(doc, kind)
    assert key(idle.parent) in planned(doc, kind)


def test_a_link_plan_cannot_make_a_live_game_file_readonly(tree):
    write(tree / "out/a/log.bin", blob("dup"))
    write(tree / "out/b/log.bin", blob("dup"))
    live = write(tree / "game/other.bin", blob("dup"))
    cfg = cfg_for(tree, live_roots=[])
    action = next(a for a in hk.plan(cfg)["actions"] if a["class"] == "link")
    action["canonical"] = str(live)
    assert hk.check(action, cfg, hk.Guard(cfg), time.time())


def test_a_launch_folder_holding_a_backup_is_never_recycled_or_gzipped(tree):
    old = "launch-20200101-000000"
    write(tree / "launches" / old / "backups" / "x.log", blob("bk", 100_000))
    write(tree / "launches2" / old / "logs" / "x.log", blob("lg", 100_000))  # twin: no backup folder
    cfg = cfg_for(tree, launch_logs=["launches", "launches2"])
    doc = hk.plan(cfg)
    assert planned(doc, "launch") == {key(tree / "launches2" / old)}
    cfg2 = cfg_for(tree, launch_logs=["launches", "launches2"], recycle_after_days=10 ** 6)   # gzip age instead
    doc2 = hk.plan(cfg2)
    assert planned(doc2, "gzip") == {key(tree / "launches2" / old / "logs" / "x.log")}


def test_a_repeating_snapshot_holding_a_save_is_never_planned(tree):
    for root, inner in (("snaps", "save/s.xml.gz"), ("snaps2", "data/s.bin")):    # snaps2 = twin
        for stamp in ("20260101_000000", "20260102_000000", "20260103_000000"):
            write(tree / root / stamp / inner, b"same")
    doc = hk.plan(cfg_for(tree, legacy_snapshots=["snaps", "snaps2"]))
    assert planned(doc, "snapshot") == {key(tree / "snaps2" / "20260102_000000")}


def test_the_example_config_loads():
    ex = REPO / "x4-housekeep.json.example"
    hk.load_config(ex)
    assert set(json.loads(ex.read_text(encoding="utf-8"))) - {"_comment"} <= set(hk.DEFAULTS)


# --------------------------------------------------------------------------- temp sweep (Recycle Bin only, faked here)

def stale(d, days=3):
    t = time.time() - days * DAY
    for p in sorted(d.rglob("*"), key=lambda p: -len(p.parts)) + [d]:
        os.utime(p, (t, t))
    return d


def tdir(temp, name, days=3):
    d = temp / name
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f.txt").write_text("x", encoding="utf-8")
    owner = next(parent.parent for parent in d.parents if parent.name == "temp")
    (d / ".x4-temp-owner.json").write_text(json.dumps({
        "v": 1, "toolkit": os.path.normcase(os.path.realpath(owner)),
        "directory": os.path.normcase(os.path.realpath(d))}), encoding="utf-8")
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


@pytest.mark.parametrize("invalid", ["missing", "toolkit", "directory", "json"])
def test_unmarked_matching_prefix_is_kept_and_other_toolkit_markers_are_kept(tmp_path, invalid):
    temp = tmp_path / "temp"
    unmarked = tdir(temp, "x4qa_unmarked")
    receipt = unmarked / ".x4-temp-owner.json"
    if invalid == "missing":
        receipt.unlink()  # same-run fixture
    elif invalid == "json":
        receipt.write_text("{broken", encoding="utf-8")
    else:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data[invalid] = str(tmp_path / "different")
        receipt.write_text(json.dumps(data), encoding="utf-8")
    stale(unmarked)
    foreign = tdir(temp, "x4qa_foreign")
    receipt = foreign / ".x4-temp-owner.json"
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data["toolkit"] = str(tmp_path / "another-toolkit")
    receipt.write_text(json.dumps(data), encoding="utf-8")
    stale(foreign)
    ours = tdir(temp, "x4qa_owned")
    fake = FakeBin(tmp_path)
    hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=fake)
    assert unmarked.exists() and foreign.exists() and not ours.exists()
    assert [Path(p) for p in fake.got] == [ours]


def test_sweep_keeps_nested_active_files_and_forever_content(tmp_path):
    temp = tmp_path / "temp"
    active = tdir(temp, "x4qa_active")
    (active / "sub" / "f.txt").write_bytes(b"still working")
    protected = tdir(temp, "x4qa_protected")
    write(protected / "backups" / "evidence.bin", b"keep")
    stale(protected)
    ours = tdir(temp, "x4qa_idle")
    hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=FakeBin(tmp_path))
    assert active.exists() and protected.exists() and not ours.exists()


def test_failed_probe_restore_is_reported_and_other_candidates_continue(tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    broken = tdir(temp, "x4qa_restore")
    ours = tdir(temp, "x4qa_other")
    rename = os.rename
    def fail_restore(source, target):
        if str(source).endswith("x4qa_restore.housekeep-probe"):
            raise OSError("fixture restore refused")
        return rename(source, target)
    monkeypatch.setattr(hk.os, "rename", fail_restore)
    res = hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=FakeBin(tmp_path))
    assert res["rows"]["failed"] == 1 and "housekeep-probe" in res["failed"][0]
    assert not ours.exists()
    assert broken.with_name(broken.name + ".housekeep-probe").exists()


def test_gzip_round_trip_uses_a_fake_bin_and_keeps_existing_output(tree):
    import gzip
    source = write(tree / "launches" / "launch-20200101-000000" / "x.log", blob("log", 100_000))
    cfg = cfg_for(tree, launch_logs=["launches"], recycle_after_days=10 ** 6)
    doc = hk.plan(cfg, only={"gzip"})
    assert planned(doc, "gzip") == {key(source)}
    fake = FakeBin(tree)
    assert hk.apply(doc, cfg, recycler=fake) == 0
    assert gzip.decompress(source.with_suffix(".log.gz").read_bytes()) == blob("log", 100_000)
    assert fake.got == [str(source)]
    source = write(source, blob("log", 100_000))
    assert hk.apply(doc, cfg, recycler=fake) == 1
    assert source.exists()


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


def test_the_sweep_never_touches_the_machine_wide_pytest_root(tmp_path):
    """RETENTION.md "Toolkit temp": the toolkit's own name prefixes only, never `pytest-of-<user>`, which every
    project on the machine shares. Even a receipt does not make an old pytest run or a garbage-* dir the toolkit's."""
    temp = tmp_path / "temp"
    base = temp / "pytest-of-someone"
    old, newest, garbage = tdir(base, "pytest-9"), tdir(base, "pytest-10"), tdir(base, "garbage-0d1e")
    ours = tdir(temp, "x4qa_twin001")                          # twin: a toolkit prefix in the same temp is swept
    fake = FakeBin(tmp_path)
    res = hk.sweep_temp(sweep_cfg(tmp_path), temp=temp, recycler=fake)
    assert old.exists() and newest.exists() and garbage.exists()
    assert not ours.exists() and [Path(p) for p in fake.got] == [ours]
    assert sum(res["rows"].values()) == 1                      # the pytest dirs were never even candidates


def _created_prefixes():
    import re
    pat = re.compile(r"""(?:mkdtemp|TemporaryDirectory)\(\s*prefix=["']([^"']+)["']""")
    found = set()
    for root in (REPO / "tools" / "x4validate", REPO / "scripts", REPO / "agent" / "guards" / "claude-hooks"):
        for f in root.rglob("*.py"):
            if ".venv" not in f.parts:
                found.update(pat.findall(f.read_text(encoding="utf-8", errors="replace")))
    return found


def _distinctive(prefix):
    """A name only this toolkit would pick: it says x4. `split_` or `pytest-of-` could be anyone's."""
    return prefix.lower().startswith("x4")


def test_every_swept_temp_prefix_is_distinctive_and_has_a_creator_in_the_repo():
    created = _created_prefixes()
    assert "x4qa_registry_" in created, "the creator scan found nothing: fix it"
    prefixes = hk.DEFAULTS["temp_prefixes"]
    generic = [p for p in prefixes if not _distinctive(p)]
    assert not generic, "generic temp prefixes an unrelated project could also use: {}".format(generic)
    orphan = [p for p in prefixes if not any(c.startswith(p) for c in created)]
    assert not orphan, "swept temp prefixes nothing in the repo creates: {}".format(orphan)
    # twins: the predicates can go red
    assert not _distinctive("split_") and not _distinctive("pytest-of-")
    assert not any(c.startswith("x4-no-such-creator-") for c in created)


def test_apply_resolves_the_recycler_at_call_time_so_the_test_fixture_can_intercept_it():
    """A default bound at def time is the REAL recycle(): monkeypatching hk.recycle would not reach apply()."""
    import inspect
    assert inspect.signature(hk.apply).parameters["recycler"].default is None


def test_the_harness_opt_out_only_stops_the_session_sweep_never_an_explicit_run(tmp_path, monkeypatch, capsys):
    """X4_HOUSEKEEP_SWEEP=off is for test harnesses: the unattended session-start sweep. A sweep-temp or apply the
    user runs by hand still runs."""
    monkeypatch.setattr(hk.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.setattr(hk, "_has_bin", lambda: True)
    monkeypatch.setenv("X4_HOUSEKEEP_SWEEP", "off")
    fake = FakeBin(tmp_path)
    monkeypatch.setattr(hk, "recycle", fake)
    left = tdir(tmp_path / "temp", "x4qa_hand001")
    cfg = sweep_cfg(tmp_path)
    monkeypatch.setattr(hk, "load_config", lambda _p: cfg)
    assert hk.main(["sweep-temp"]) == 0
    assert not left.exists() and len(fake.got) == 1
    state = tmp_path / "note.txt"                              # twin: the session sweep honours the opt-out
    again = tdir(tmp_path / "temp", "x4qa_sess001")
    assert hk.session(cfg, state) == 0
    assert again.exists() and len(fake.got) == 1


def test_apply_by_hand_runs_under_the_harness_opt_out(tree, monkeypatch):
    monkeypatch.setenv("X4_HOUSEKEEP_SWEEP", "off")
    fake = FakeBin(tree)
    monkeypatch.setattr(hk, "recycle", fake)
    source = write(tree / "launches" / "launch-20200101-000000" / "x.log", blob("log", 100_000))
    cfg = cfg_for(tree, launch_logs=["launches"], recycle_after_days=10 ** 6)
    doc = hk.plan(cfg, only={"gzip"})
    assert planned(doc, "gzip") == {key(source)}
    assert hk.apply(doc, cfg) == 0                             # no recycler passed: the module's (faked) bin
    assert fake.got == [str(source)]


def test_session_runs_at_most_once_per_interval_and_writes_a_note(tmp_path, monkeypatch):
    cfg = sweep_cfg(tmp_path)
    cfg.update(codex_sessions_warn_gib=10 ** 6, worktrees_warn=10 ** 6, free_floor_gib=0, sweep_every_hours=6)
    monkeypatch.setattr(hk.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(hk, "recycle", FakeBin(tmp_path))
    monkeypatch.setattr(hk, "_has_bin", lambda: True)           # the FakeBin is the bin, on every OS
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


def _no_real_bin(*_a, **_k):
    raise AssertionError("the REAL recycle() was reached from a test")


def test_off_windows_the_sweep_keeps_its_candidates_and_the_session_still_writes_its_state(tmp_path, monkeypatch):
    cfg = sweep_cfg(tmp_path)
    cfg.update(codex_sessions_warn_gib=10 ** 6, worktrees_warn=10 ** 6, free_floor_gib=0)
    monkeypatch.setattr(hk.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("X4_HOUSEKEEP_SWEEP", raising=False)
    monkeypatch.setattr(hk, "recycle", _no_real_bin)
    monkeypatch.setattr(hk, "_has_bin", lambda: False)
    left = tdir(tmp_path / "temp", "x4qa_lin001")
    state = tmp_path / "note.txt"
    assert hk.session(cfg, state) == 0                          # used to raise: state never written, rerun forever
    assert left.exists() and state.is_file()
    assert "kept: x4housekeep recycles on Windows only" in state.read_text(encoding="utf-8")
    fake = FakeBin(tmp_path)                                    # twin: a platform with a bin recycles it
    monkeypatch.setattr(hk, "recycle", fake)
    monkeypatch.setattr(hk, "_has_bin", lambda: True)
    os.utime(state, (time.time() - 7 * 3600,) * 2)
    hk.session(cfg, state)
    assert not left.exists() and len(fake.got) == 1


def test_session_never_sweeps_when_the_harness_says_off(tmp_path, monkeypatch):
    cfg = sweep_cfg(tmp_path)
    cfg.update(codex_sessions_warn_gib=10 ** 6, worktrees_warn=10 ** 6, free_floor_gib=0)
    monkeypatch.setattr(hk.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    left = tdir(tmp_path / "temp", "x4qa_off001")
    monkeypatch.setenv("X4_HOUSEKEEP_SWEEP", "off")
    monkeypatch.setattr(hk, "recycle", _no_real_bin)
    hk.session(cfg, tmp_path / "note.txt")
    assert left.exists()
    fake = FakeBin(tmp_path)                                    # twin: unset, the same call sweeps
    monkeypatch.setattr(hk, "recycle", fake)
    monkeypatch.setattr(hk, "_has_bin", lambda: True)
    monkeypatch.delenv("X4_HOUSEKEEP_SWEEP")
    hk.session(cfg, tmp_path / "note.txt")
    assert not left.exists()


def test_the_tool_never_deletes_permanently():
    """User ruling 2026-10-18: the Recycle Bin, always -- the tool's own files included."""
    import re
    src = (REPO / "scripts" / "x4housekeep.py").read_text(encoding="utf-8")
    for call in ("os.unlink(", "os.remove(", "shutil.rmtree(", ".unlink(", "os.rmdir(", ".rmdir(",
                 "FileSystem]::Delete"):
        assert call not in src, call
    flags = re.findall(r"fFlags = ([^}]+?) \}", src)
    assert len(flags) == 1 and {"0x4000", "0x40"} <= set(re.findall(r"0x[0-9A-Fa-f]+", flags[0])), flags
