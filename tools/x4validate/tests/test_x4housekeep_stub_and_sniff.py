"""Release review 4.1.0 round 2: what an archive stub says about its folder, the save sniff, and a report that writes nothing.

The Recycle Bin is faked throughout; nothing here reaches the real one.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
import x4housekeep as hk  # noqa: E402

DAY = 86400
BODY = b'<?xml version="1.0" encoding="UTF-8"?>\n<savegame>\n<info/></savegame>\n'


@pytest.fixture(autouse=True)
def _never_the_real_bin(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the REAL recycle() was reached from a test")
    monkeypatch.setattr(hk, "recycle", refuse)


def age(root, days=30):
    t = time.time() - days * DAY
    for dp, ds, fs in os.walk(root, topdown=False):
        for n in fs:
            os.utime(Path(dp) / n, (t, t))
        os.utime(dp, (t, t))


def mkcfg(root, **over):
    root.mkdir(parents=True, exist_ok=True)
    (root / "agent").mkdir(exist_ok=True)
    data = dict(root=str(root), agent_output=["agent"], archive_enabled=True, archive_root=str(root / "vault"),
                evidence_docs=["DOC.md"], approved_sha256=hk.cleanup_code_sha256())
    data.update(over)
    p = root / "cfg.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return hk.load_config(p)


def moving_bin(root):
    trash = root / "trash"
    trash.mkdir(exist_ok=True)
    n = [0]

    def recycle(paths):
        out = {}
        for p in paths:
            n[0] += 1
            shutil.move(str(p), str(trash / str(n[0])))
            out[str(p)] = None
        return out
    return recycle


def refusing_bin(paths):
    return {str(p): "left in place: test" for p in paths}


def task(root, name="task1", **files):
    t = root / "agent" / name
    t.mkdir(parents=True)
    for rel, data in files.items():
        f = t / rel.replace("__", "/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    (t / ".housekeep-closed").write_text("", encoding="utf-8")
    age(t)
    return t


LATER = 30 * DAY


def _archive_row(doc):
    return {k: v for k, v in doc["rows"].items() if "archive" in k}


# -------------------------------------------------------------------- R2-3: a stub is never re-planned

def test_deep_linked_evidence_does_not_replan_an_archived_folder(tmp_path):
    """_only_kept_evidence called keep_local without `extra`: archiving kept the linked file, the next plan did not
    count it, and the folder was planned again for ever, each apply failing rc 1."""
    root = tmp_path / "r"
    cfg = mkcfg(root)
    t = task(root, deep__evidence=b"e" * (2 << 20), payload=b"p" * (3 << 20))
    (root / "DOC.md").write_text("see agent/task1/deep/evidence for it\n", encoding="utf-8")
    now = time.time()
    doc = hk.plan(cfg, now=now)
    assert [a["class"] for a in doc["actions"] if a["class"] == "archive"] == ["archive"], "the control: it was due"
    assert hk.apply(doc, cfg, recycler=moving_bin(root), now=now) == 0
    assert (t / "deep" / "evidence").exists() and not (t / "payload").exists()
    again = hk.plan(cfg, now=now + LATER)
    assert not [a for a in again["actions"] if a["class"] == "archive"]
    assert again["coverage"]["archive_inventory"]["stub_notes"] == [], "kept evidence is what a stub is supposed to hold"


def test_a_recycler_leftover_is_said_once_and_does_not_fail_every_run(tmp_path, capsys):
    root = tmp_path / "r"
    cfg = mkcfg(root)
    task(root, payload=b"p" * (3 << 20))
    now = time.time()
    doc = hk.plan(cfg, now=now)
    assert hk.apply(doc, cfg, recycler=refusing_bin, now=now) == 1, "the run that failed says so"
    assert "PARTIAL" in capsys.readouterr().out
    again = hk.plan(cfg, now=now + LATER)
    assert not [a for a in again["actions"] if a["class"] == "archive"], "a second archive can only fail"
    assert hk.apply(again, cfg, recycler=refusing_bin, now=now + LATER) == 0
    notes = again["coverage"]["archive_inventory"]["stub_notes"]
    assert len(notes) == 1 and "still here" in notes[0] and "1 named in its zip" in notes[0], notes
    hk.print_coverage(again)
    assert "NOTE " in capsys.readouterr().out


def test_new_work_beside_a_stub_is_named_as_possibly_the_only_copy(tmp_path):
    """Under 1 MiB too: the old size shortcut called anything small "stub only" unseen."""
    root = tmp_path / "r"
    cfg = mkcfg(root)
    t = task(root, payload=b"p" * (3 << 20))
    now = time.time()
    assert hk.apply(hk.plan(cfg, now=now), cfg, recycler=moving_bin(root), now=now) == 0
    (t / "later.bin").write_bytes(b"new")
    age(t)
    notes = hk.plan(cfg, now=now + LATER)["coverage"]["archive_inventory"]["stub_notes"]
    assert len(notes) == 1 and "1 NOT in it" in notes[0] and "later.bin" in notes[0], notes


def _archived(tmp_path, **files):
    root = tmp_path / "r"
    cfg = mkcfg(root)
    t = task(root, payload=b"p" * (3 << 20), **files)
    now = time.time()
    assert hk.apply(hk.plan(cfg, now=now), cfg, recycler=moving_bin(root), now=now) == 0
    return cfg, t, now


def _notes(cfg, now):
    return hk.plan(cfg, now=now + LATER)["coverage"]["archive_inventory"]["stub_notes"]


@pytest.mark.parametrize("name", ["findings.md", "results.json", "run.log", "README", "README.txt", "rows.jsonl"])
def test_a_new_small_top_level_file_beside_a_stub_gets_a_note(tmp_path, name):
    """The kept patterns hid these: keep_local filtered them out before the record was looked at."""
    cfg, t, now = _archived(tmp_path)
    (t / name).write_bytes(b"NEW WORK\n")
    notes = _notes(cfg, now)
    assert len(notes) == 1 and "1 NOT in it" in notes[0] and name in notes[0], notes


def test_TWIN_kept_and_unchanged_stays_quiet(tmp_path):
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old notes\n", "results.json": b"{}", "README": b"r"})
    assert (t / "notes.md").exists() and (t / "results.json").exists(), "the control: archiving kept them"
    assert _notes(cfg, now) == []


def test_a_kept_file_edited_after_the_archive_gets_a_note(tmp_path):
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old notes\n", "results.json": b"{}"})
    (t / "notes.md").write_bytes(b"EDITED after the archive\n")
    notes = _notes(cfg, now)
    assert len(notes) == 1 and "1 kept but edited since" in notes[0] and "notes.md" in notes[0], notes
    assert "NOT in it" in notes[0] and "0 NOT in it" in notes[0], notes
    assert (t / "notes.md").read_bytes() == b"EDITED after the archive\n", "nothing is deleted or changed"


def test_TWIN_a_tracked_top_level_file_stays_quiet_whatever_its_bytes(tmp_path):
    """git holds it; only the pattern-kept files are compared."""
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old\n"})
    (t / "notes.md").write_bytes(b"edited, but tracked\n")
    guard = hk.Guard(cfg)
    assert "edited since" in hk.stub_state(t, guard), "the control: untracked, it is noted"
    guard.tracked.add(hk._key(t / "notes.md"))
    assert hk.stub_state(t, guard) == ""


def test_the_comparison_reads_a_bounded_amount_and_says_what_it_skipped(tmp_path, monkeypatch):
    cfg, t, now = _archived(tmp_path, **{"a.md": b"a" * 2000, "b.md": b"b" * 2000, "c.md": b"c" * 2000})
    monkeypatch.setattr(hk, "STUB_COMPARE_BYTES", 3000)
    read = []
    real = hk.certainty._local_sha256
    monkeypatch.setattr(hk.certainty, "_local_sha256", lambda p: read.append(Path(p).name) or real(p))
    state = hk.stub_state(t, hk.Guard(cfg))
    assert read == ["a.md"], "stops at the budget"
    assert "2 kept but not compared" in state, state


def test_TWIN_the_comparison_read_is_not_recorded(tmp_path, monkeypatch):
    """A plan's own look must not trip the read veto: the same unrecorded stream the sniff uses."""
    cfg, t, now = _archived(tmp_path, **{"a.md": b"a"})
    calls = []
    reads = hk.certainty._reads_helper()
    real = reads.open_unrecorded
    monkeypatch.setattr(reads, "open_unrecorded", lambda p: calls.append(Path(p).name) or real(p))
    assert hk.stub_state(t, hk.Guard(cfg)) == ""
    assert calls == ["a.md"]


def test_a_stub_with_no_kept_file_reads_neither_its_record_nor_a_hash(tmp_path, monkeypatch):
    """The common case costs what it did."""
    cfg, t, now = _archived(tmp_path)
    monkeypatch.setattr(hk.certainty, "_local_sha256", lambda p: pytest.fail("hashed " + str(p)))
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: pytest.fail("read " + str(self))
                        if self.name == "ARCHIVED.md" else real(self, *a, **k))
    assert hk.stub_state(t, hk.Guard(cfg)) == ""


def test_kept_files_beside_a_stub_whose_record_cannot_be_read_are_not_quiet(tmp_path):
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old\n"})
    (t / "ARCHIVED.md").write_text("# Archived, record gone\n", encoding="utf-8")
    notes = _notes(cfg, now)
    assert len(notes) == 1 and "record cannot be read" in notes[0], notes


def test_a_stub_whose_record_cannot_be_read_says_so_and_stays(tmp_path):
    root = tmp_path / "r"
    cfg = mkcfg(root)
    t = task(root, payload=b"p" * (2 << 20), ARCHIVED_md=b"")
    (t / "ARCHIVED_md").rename(t / "ARCHIVED.md")
    age(t)
    doc = hk.plan(cfg, now=time.time())
    assert not [a for a in doc["actions"] if a["class"] == "archive"]
    notes = doc["coverage"]["archive_inventory"]["stub_notes"]
    assert len(notes) == 1 and "cannot be read" in notes[0]


def test_a_tracked_file_deep_in_a_stubbed_folder_counts_as_kept(tmp_path):
    """The `tracked` half of the set archiving uses; the first test covers `links`."""
    root = tmp_path / "r"
    cfg = mkcfg(root)
    t = task(root, deep__tracked=b"t", ARCHIVED_md=b"<!-- {\"source_hashes\": {}} -->")
    (t / "ARCHIVED_md").rename(t / "ARCHIVED.md")
    guard = hk.Guard(cfg)
    assert "NOT in it" in hk.stub_state(t, guard), "the control: untracked, it is a stray"
    guard.tracked.add(hk._key(t / "deep" / "tracked"))
    assert hk.stub_state(t, guard) == ""


def test_a_stub_grown_past_the_kept_size_is_still_the_stub(tmp_path):
    """Its list of leftovers can pass 1 MiB, which takes it out of keep_local; it is not an item "still here"."""
    root = tmp_path / "r"
    cfg = mkcfg(root)
    t = task(root, ARCHIVED_md=b"<!-- {\"source_hashes\": {}} -->\n" + b"x" * (2 << 20))
    (t / "ARCHIVED_md").rename(t / "ARCHIVED.md")
    assert hk.stub_state(t, hk.Guard(cfg)) == ""


def test_a_folder_with_no_stub_is_still_archived(tmp_path):
    """The twin: only a stub stops the plan."""
    root = tmp_path / "r"
    cfg = mkcfg(root)
    task(root, payload=b"p" * (2 << 20))
    assert [a["class"] for a in hk.plan(cfg, now=time.time())["actions"]] == ["archive"]


# -------------------------------------------------------------------- R2-8: the ledger note reaches apply

def _ledger_cfg(root, text):
    (root).mkdir(parents=True, exist_ok=True)
    (root / "ledger.json").write_text(text, encoding="utf-8")
    return mkcfg(root, job_ledger=str(root / "ledger.json"))


def test_an_unreadable_ledger_is_noted_when_apply_acts_on_it(tmp_path, capsys):
    root = tmp_path / "r"
    cfg = _ledger_cfg(root, "{not json")
    task(root, payload=b"p" * (2 << 20))
    now = time.time()
    doc = hk.plan(cfg, now=now)
    assert "could not be read" in doc["coverage"]["archive_inventory"]["job_ledger_note"]
    assert hk.apply(doc, cfg, recycler=moving_bin(root), now=now) == 0
    assert "running jobs were not checked" in capsys.readouterr().out


def test_a_readable_ledger_adds_no_note_to_apply(tmp_path, capsys):
    root = tmp_path / "r"
    cfg = _ledger_cfg(root, json.dumps({"entries": []}))
    task(root, payload=b"p" * (2 << 20))
    now = time.time()
    assert hk.apply(hk.plan(cfg, now=now), cfg, recycler=moving_bin(root), now=now) == 0
    assert "NOTE" not in capsys.readouterr().out


def test_an_apply_with_no_archive_action_says_nothing_about_the_ledger(tmp_path, capsys):
    """Twin: the ledger is only a hold on archiving."""
    root = tmp_path / "r"
    cfg = _ledger_cfg(root, "{not json")
    doc = {"actions": [{"class": "scratch", "path": str(root / "gone"), "bytes": 1}]}
    assert hk.apply(doc, cfg, recycler=moving_bin(root), now=time.time()) == 1, "the control: that class really ran"
    assert "NOTE" not in capsys.readouterr().out


# -------------------------------------------------------------------- R2-5: a copy by content, whatever it is called

@pytest.mark.parametrize("name,data", [
    ("before-crash.xml.gz", gzip.compress(BODY)),
    ("before-crash.xml", BODY),
    ("save_001.xml.gz.bak", gzip.compress(BODY)),
    ("keep-me.gz", gzip.compress(BODY)),
    ("save_001.bak", gzip.compress(BODY)),
    ("mysave.xml.bak", BODY),
    ("no-extension", gzip.compress(BODY)),
    ("plain.txt", BODY),
    ("truncated.bin", gzip.compress(BODY + os.urandom(9000))[:200]),
], ids=lambda v: v if isinstance(v, str) else "")
def test_a_renamed_save_copy_is_a_save_by_its_content(tmp_path, name, data):
    (tmp_path / "root").mkdir()
    guard = hk.Guard(mkcfg(tmp_path / "root"))
    p = tmp_path / "elsewhere" / name
    p.parent.mkdir()
    p.write_bytes(data)
    assert guard.why_protected(p) == "save"


@pytest.mark.parametrize("name,data", [
    ("notes.xml", b"<?xml version='1.0'?><notes>savegame</notes>"),
    ("like.xml", b"<savegame-like/>"),
    ("macros.xml.gz", gzip.compress(b"<macros>" + b"x" * 9000 + b"</macros>")),
    ("broken.gz", b"\x1f\x8b" + b"junk" * 50),
    ("short.bin", b"\x1f"),
    ("empty.xml", b""),
    ("blob.bin", os.urandom(20000)),
], ids=lambda v: v if isinstance(v, str) else "")
def test_other_content_is_not_a_save_whatever_its_name(tmp_path, name, data):
    (tmp_path / "root").mkdir()
    guard = hk.Guard(mkcfg(tmp_path / "root"))
    p = tmp_path / "elsewhere" / name
    p.parent.mkdir()
    p.write_bytes(data)
    assert guard.why_protected(p) == ""


def test_the_sniff_is_bounded_on_a_large_file(tmp_path, monkeypatch):
    """A file far bigger than the sniff: the read stops at the bound, and a gzip bomb decompresses no further."""
    bomb = tmp_path / "bomb.gz"
    bomb.write_bytes(gzip.compress(b"\0" * (64 << 20)))
    assert bomb.stat().st_size > 2 * hk.SAVE_SNIFF_BYTES, "the control: more on disk than the sniff reads"
    seen = []
    real = hk._archive_module().open_unrecorded

    class Spy:
        def __init__(self, stream):
            self.stream = stream

        def read(self, n=-1):
            assert 0 < n <= 2 * hk.SAVE_SNIFF_BYTES, n
            data = self.stream.read(n)
            seen.append(len(data))
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()
    monkeypatch.setattr(hk._archive_module(), "open_unrecorded", lambda p: Spy(real(p)))
    assert hk._holds_savegame(bomb) is False
    assert seen == [2 * hk.SAVE_SNIFF_BYTES]


def test_a_missing_path_or_a_folder_is_not_a_save_and_does_not_raise(tmp_path):
    assert hk._holds_savegame(tmp_path) is False
    assert hk._holds_savegame(tmp_path / "gone") is False


# -------------------------------------------------------------------- report writes nothing

def _records_reads(folder: Path) -> bool:
    probe = folder / "records-reads-probe"
    probe.write_bytes(b".")
    old = time.time() - 3 * DAY
    os.utime(probe, (old, old))
    with open(probe, "rb") as f:
        f.read()
    moved = probe.stat().st_atime > old + 3600
    probe.unlink()
    return moved


def test_a_look_is_not_recorded_as_a_read_on_a_volume_that_records_reads(tmp_path):
    if not _records_reads(tmp_path):
        pytest.skip("this volume does not record reads, so there is nothing to prove here")
    f = tmp_path / "file.bin"
    f.write_bytes(b"x" * 10000)
    old = time.time() - 3 * DAY
    for look in (hk._holds_savegame, hk.blake, hk.certainty._local_sha256):
        os.utime(f, (old, old))
        look(f)
        assert abs(f.stat().st_atime - old) < 3600, look


def test_report_never_calls_utime(tmp_path, monkeypatch, capsys):
    """`report` says it changes nothing. A put-back read time is a write: it moved ctime, and a look taken while another
    process read the file erased that read."""
    root = tmp_path / "r"
    cfg = mkcfg(root, min_bytes=1)
    t = task(root, a=b"same" * 1000, b=b"same" * 1000, c=BODY, d=gzip.compress(b"<macros/>"))
    cfg["obsolete_items"] = {}
    calls = []
    monkeypatch.setattr(os, "utime", lambda *a, **k: calls.append(a))
    assert hk.report(cfg) == 0
    capsys.readouterr()
    assert calls == []
    assert (t / "a").exists()


def test_open_unrecorded_reads_the_same_bytes_as_open(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(os.urandom(9000))
    with hk._archive_module().open_unrecorded(f) as stream:
        assert stream.read() == f.read_bytes()
    os.chmod(f, 0o400)
    try:
        with hk._archive_module().open_unrecorded(f) as stream:
            assert stream.read(10) == f.read_bytes()[:10], "a read-only file still opens"
    finally:
        os.chmod(f, 0o600)
    with pytest.raises(OSError):
        hk._archive_module().open_unrecorded(tmp_path / "gone")


# -------------------------------------------------------------------- minor: a refusal says the true reason

def test_the_hard_link_class_is_not_refused_for_want_of_a_bin(monkeypatch):
    monkeypatch.setattr(hk, "_drive_type", lambda _p: 4)
    monkeypatch.setattr(hk, "_is_link", lambda _p: False)
    assert "no Recycle Bin" in hk.landing_refusal("C:/x/y")
    assert "no Recycle Bin" not in hk.landing_refusal("C:/x/y", "hard links stay on local volumes")
