"""The archive stub's restore line, what an archive leaves local, and the cold vault's failure paths.

scripts/housekeep_archive.py and housekeep_backups.py are byte-identical with the Skyrim toolkit
(check-housekeep-parity.py), so the restore line cannot spell a per-repo folder. The Recycle Bin is faked.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ha = _load("housekeep_archive_stub_test", "scripts/housekeep_archive.py")
b = _load("housekeep_backups_vault_test", "scripts/housekeep_backups.py")


def write(p, data=b"payload"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def moving_bin(root):
    root.mkdir(exist_ok=True)

    def recycle(paths):
        for i, p in enumerate(paths):
            os.replace(p, root / "{}-{}-{}".format(time.monotonic_ns(), i, Path(p).name))
        return {str(p): None for p in paths}
    return recycle


# ---------------------------------------------------------------- I5: a restore command that exists from anywhere

def _restore_line(stub):
    m = re.search(r'Restore: `python "([^"]+)" restore "([^"]+)"`', stub.read_text(encoding="utf-8"))
    assert m, stub.read_text(encoding="utf-8")
    return m.group(1), m.group(2)


def test_the_restore_line_names_a_script_that_exists_and_it_works_from_another_folder(tmp_path):
    t = tmp_path / "out" / "task"
    write(t / "payload" / "big.bin", b"B" * 500)
    ha.archive_folder(t, tmp_path / "arch", moving_bin(tmp_path / "bin"))
    assert not (t / "payload").exists()
    script, stub = _restore_line(t / ha.STUB)
    assert Path(script).is_absolute() and Path(script).is_file()
    assert Path(script).name == "housekeep_archive.py"
    assert Path(stub) == t / ha.STUB
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = subprocess.run([sys.executable, script, "restore", stub], cwd=elsewhere, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (t / "payload" / "big.bin").read_bytes() == b"B" * 500


def test_the_module_usage_spells_no_per_repo_folder():
    src = (REPO / "scripts" / "housekeep_archive.py").read_text(encoding="utf-8")
    assert "python tools/housekeep_archive.py" not in src
    assert "python scripts/housekeep_archive.py" not in src


def test_the_stub_does_not_claim_a_nas_when_the_archive_is_local(tmp_path):
    t = tmp_path / "out" / "task"
    write(t / "payload" / "big.bin")
    ha.archive_folder(t, tmp_path / "arch", moving_bin(tmp_path / "bin"))
    assert "NAS" not in (t / ha.STUB).read_text(encoding="utf-8").split("<!--")[0]


# ---------------------------------------------------------------- I8: evidence at the top level stays local

@pytest.mark.parametrize("name", ["receipt.json", "events.jsonl", "run.log", "README.md", "notes.md"])
def test_top_level_evidence_stays_local_and_also_in_the_zip(tmp_path, name):
    t = tmp_path / "out" / "task"
    write(t / name, b"evidence")
    write(t / "payload" / "big.bin", b"B" * 500)
    rec = ha.archive_folder(t, tmp_path / "arch", moving_bin(tmp_path / "bin"))
    assert (t / name).read_bytes() == b"evidence"
    assert not (t / "payload").exists()
    import zipfile
    assert name in zipfile.ZipFile(rec["zip"]).namelist()


@pytest.mark.parametrize("rel,data", [("big.json", b"x" * ((1 << 20) + 1)), ("nested/receipt.json", b"{}"),
                                      ("nested/run.log", b"log"), ("data.bin", b"bin"), ("trace.txt", b"text")],
                         ids=["oversized", "nested-json", "nested-log", "bin", "txt"])
def test_oversized_nested_or_other_files_are_retired_after_the_zip_holds_them(tmp_path, rel, data):
    t = tmp_path / "out" / "task"
    write(t / rel, data)
    rec = ha.archive_folder(t, tmp_path / "arch", moving_bin(tmp_path / "bin"))
    assert not (t / rel).exists()
    import zipfile
    assert Path(rel).as_posix() in zipfile.ZipFile(rec["zip"]).namelist()


# ---------------------------------------------------------------- M1: the rolling store's backup exception is narrow

def _guard(globs, why="backup folder"):
    return SimpleNamespace(tracked=set(), links=set(), globs=globs, why_protected=lambda p: why)


def test_a_glob_survives_the_backup_folder_exception():
    assert b.rolling_protection(Path("C:/s/tool-backups/20260101_000000/keepme.dat"),
                                _guard(["*/keepme.dat"])) == "protected_globs"


def test_a_non_matching_glob_keeps_the_exception():
    assert b.rolling_protection(Path("C:/s/tool-backups/20260101_000000/other.dat"), _guard(["*/keepme.dat"])) == ""


def test_another_reason_is_never_cleared():
    assert b.rolling_protection(Path("C:/s/x.dat"), _guard([], why="git-tracked")) == "git-tracked"


# ---------------------------------------------------------------- M6: the cold vault

@pytest.fixture
def vault(tmp_path):
    source = tmp_path / "backups"
    source.mkdir()
    now = time.time()
    for name, days, content in (("oldest", 90, b"one\n"), ("old", 70, b"two\n"), ("new", 1, b"three\n")):
        d = source / name
        d.mkdir()
        (d / "script.py").write_bytes(content)
        stamp = now - days * b.DAY
        os.utime(d / "script.py", (stamp, stamp))
        os.utime(d, (stamp, stamp))
    cfg = dict(root=tmp_path, active_hours=48, backup_archive=dict(
        enabled=True, destination=str(tmp_path / "nas" / "vault"),
        sources=[dict(path=str(source), reason="rollback history")], min_age_days=30, keep_local=1))
    return source, cfg, moving_bin(tmp_path / "bin")


def _archived_and_declared(vault):
    _, cfg, recycle = vault
    doc = b.plan(cfg)
    rows = b.apply(doc, cfg, recycle, b.digest(doc))
    assert len(rows) == 2 and all(r["recycled"] for r in rows)
    records = [json.loads(Path(r["receipt"]).read_text()) for r in rows]
    cfg["backup_archive"]["obsolete"] = {r["id"]: dict(basis="operator-confirmed-obsolete", reason="reviewed",
                                                       evidence="explicit") for r in records}
    return cfg, rows, records, recycle


def test_a_partial_prune_tombstones_what_went_and_keeps_what_did_not(vault):
    cfg, rows, records, recycle = _archived_and_declared(vault)
    doc = b.prune_plan(cfg)
    assert len(doc["objects"]) == 2
    first, second = (Path(o["path"]) for o in doc["objects"])

    def half(paths):
        out = recycle([p for p in paths if Path(p) == first])
        out.update({p: "network recycle unavailable" for p in paths if Path(p) != first})
        return out
    with pytest.raises(b.Refused) as info:
        b.prune(doc, cfg, b.digest(doc), recycler=half)
    assert "1 recycled" in str(info.value) and "1 failed" in str(info.value)
    assert first.name[:-4] and not first.exists() and second.exists()
    retired = Path(cfg["backup_archive"]["destination"]) / "retired"
    tombs = {json.loads(p.read_text())["id"] for p in retired.glob("*.json")}
    by_object = {r["object_id"]: r["id"] for r in records}
    assert tombs == {by_object[first.stem]}
    states = {row["id"]: row["state"] for row in b.review(cfg)}
    assert states[by_object[first.stem]] == "retired"          # no permanent "failed" row for a recycled archive
    assert states[by_object[second.stem]] == "review deletion"
    retry = b.prune_plan(cfg)                                  # the rest can still be finished
    assert [Path(o["path"]) for o in retry["objects"]] == [second]


def test_a_recycle_that_reports_failure_for_a_path_really_gone_still_tombstones_it(vault):
    """x4housekeep.recycle: a failed shell call reports "...; path is gone". With no tombstone that archive read as a failed
    checksum for ever, and its "vault verification incomplete" blocked every later prune plan."""
    cfg, rows, records, recycle = _archived_and_declared(vault)
    doc = b.prune_plan(cfg)

    def reports_gone(paths):
        recycle(paths)
        return {p: "recycle call failed (TimeoutExpired: x); path is gone" for p in paths}
    with pytest.raises(b.Refused, match="backup recycling incomplete"):
        b.prune(doc, cfg, b.digest(doc), recycler=reports_gone)
    assert len(list((Path(cfg["backup_archive"]["destination"]) / "retired").glob("*.json"))) == 2
    assert {row["state"] for row in b.review(cfg)} == {"retired"}, "no permanent failed row for what really went"


def test_a_fully_failed_prune_writes_no_tombstone(vault):
    cfg, rows, records, recycle = _archived_and_declared(vault)
    doc = b.prune_plan(cfg)
    with pytest.raises(b.Refused, match="failed/left in place"):
        b.prune(doc, cfg, b.digest(doc), recycler=lambda paths: {p: "no bin" for p in paths})
    assert not list((Path(cfg["backup_archive"]["destination"]) / "retired").glob("*.json"))


def test_a_fully_successful_prune_tombstones_everything(vault):
    cfg, rows, records, recycle = _archived_and_declared(vault)
    doc = b.prune_plan(cfg)
    assert len(b.prune(doc, cfg, b.digest(doc), recycler=recycle)) == 2
    assert len(list((Path(cfg["backup_archive"]["destination"]) / "retired").glob("*.json"))) == 2


def _record_reads(monkeypatch):
    """A drive that records reads: every file this process opens gets a read time of now, measured or not."""
    real_open = Path.open

    def opening(self, mode="r", *a, **k):
        stream = real_open(self, mode, *a, **k)
        real_close = stream.close

        def close():
            real_close()
            st = os.stat(self)
            os.utime(self, ns=(time.time_ns(), st.st_mtime_ns))
        stream.close = close

        class Wrap:
            def __enter__(self_):
                return stream

            def __exit__(self_, *exc):
                close()
                return False

            def __getattr__(self_, name):
                return getattr(stream, name)
        return Wrap()
    monkeypatch.setattr(Path, "open", opening)


def test_planning_does_not_make_a_generation_look_recently_read(vault, monkeypatch):
    source, cfg, _ = vault
    _record_reads(monkeypatch)
    first = b.plan(cfg)
    assert {Path(a["path"]).name for a in first["actions"]} == {"oldest", "old"}
    again = b.plan(cfg)                                        # the first plan's own reads must not veto the second
    assert {Path(a["path"]).name for a in again["actions"]} == {"oldest", "old"}, again["kept"]


def test_a_real_recent_read_still_vetoes_a_generation(vault):
    source, cfg, _ = vault
    p = source / "old" / "script.py"
    st = p.stat()
    os.utime(p, (time.time(), st.st_mtime))
    assert "generation recently read" in {k["reason"] for k in b.plan(cfg)["kept"]}
