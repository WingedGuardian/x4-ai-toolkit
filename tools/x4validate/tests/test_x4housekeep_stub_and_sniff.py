"""Release review 4.1.0 round 2: what an archive stub says about its folder, the save sniff, and a report that writes nothing.

The Recycle Bin is faked throughout; nothing here reaches the real one.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
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
    assert len(notes) == 1 and "record cannot be read" in notes[0] and "1 other item(s)" in notes[0], notes


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


# -------------------------------------------------------------------- R3-M2: the sniff runs last, and never blocks

def _cheap_reason_world(tmp_path):
    """One file per cheap reason, each with and without save content, under one configured root."""
    root = tmp_path / "root"
    cfg = mkcfg(root, protected_globs=["*/glob/*"])
    rows = {}
    for label, rel in (("known-good baseline", "known-good-1/f"), ("audit record", "t/audit_log.txt"),
                       ("backup folder", "my-backups/f"), ("protected_globs", "glob/f"),
                       ("git-tracked", "t/tracked"), ("linked evidence", "t/linked"), ("", "t/plain")):
        for content, body in (("save", BODY), ("other", b"not a save")):
            f = root / "agent" / ("c_" + content) / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)
            rows[(label, content)] = f
    guard = hk.Guard(cfg)
    guard.tracked.update(hk._key(rows[("git-tracked", c)]) for c in ("save", "other"))
    guard.links.update(hk._key(rows[("linked evidence", c)]) for c in ("save", "other"))
    return guard, rows


def test_save_content_outranks_every_cheap_reason_as_it_always_did(tmp_path):
    """"save" must win: archive_refusal, archive_protection, the cold-backup protect and housekeep_backups each overrule
    some of the cheap reasons, so returning "backup folder" for a save copy there would let it go."""
    guard, rows = _cheap_reason_world(tmp_path)
    for (label, content), f in rows.items():
        assert guard.why_protected(f) == ("save" if content == "save" else label), (label, content)


def test_a_save_copy_in_a_backup_folder_is_never_retired_by_the_callers_that_overrule_the_backup_reason(tmp_path):
    guard, rows = _cheap_reason_world(tmp_path)
    assert hk.archive_protection(guard, rows[("backup folder", "save")]) == "save"
    assert hk.archive_protection(guard, rows[("backup folder", "other")]) == "", "the control: the backup reason alone falls"


@pytest.mark.parametrize("label", ["known-good baseline", "audit record", "backup folder", "protected_globs",
                                   "git-tracked", "linked evidence"])
def test_a_caller_asking_only_whether_stops_at_the_cheap_reason(tmp_path, monkeypatch, label):
    guard, rows = _cheap_reason_world(tmp_path)
    monkeypatch.setattr(hk, "_holds_savegame", lambda _p: pytest.fail("sniffed although a cheap reason already holds it"))
    assert guard.why_protected(rows[(label, "save")], any_reason=True) == label


def test_TWIN_any_reason_still_sniffs_when_nothing_cheap_holds_the_file(tmp_path):
    guard, rows = _cheap_reason_world(tmp_path)
    assert guard.why_protected(rows[("", "save")], any_reason=True) == "save"
    assert guard.why_protected(rows[("", "other")], any_reason=True) == ""


def test_any_reason_gives_the_same_verdict_on_every_file(tmp_path):
    guard, rows = _cheap_reason_world(tmp_path)
    for key, f in rows.items():
        assert bool(guard.why_protected(f, any_reason=True)) == bool(guard.why_protected(f)), key


def test_a_non_regular_file_is_never_opened_by_the_sniff(tmp_path, monkeypatch):
    """The Windows half: a FIFO cannot be made here, so the mode is what is faked; the real one is below."""
    f = tmp_path / "f"
    f.write_bytes(BODY)
    assert hk._holds_savegame(f), "the control: the same file, regular, is sniffed"
    st = os.stat(f)
    fake = os.stat_result((0o010644,) + tuple(st)[1:])
    monkeypatch.setattr(hk.os, "stat", lambda *_a, **_k: fake)
    monkeypatch.setattr(hk._archive_module(), "open_unrecorded", lambda _p: pytest.fail("opened a FIFO"))
    assert hk._holds_savegame(f) is False


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX only: Windows has no named pipes on the file system")
def test_a_real_fifo_does_not_block_the_sniff(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    code = "import sys; sys.path.insert(0, sys.argv[1]); import x4housekeep as hk; print(hk._holds_savegame(sys.argv[2]))"
    done = subprocess.run([sys.executable, "-c", code, str(REPO / "scripts"), str(fifo)], capture_output=True, text=True,
                          timeout=30)
    assert done.stdout.strip() == "False", done.stderr


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


# -------------------------------------------------------------------- 4.1.1: a malformed stub record is a clean refusal

_GOOD_HASH = "0" * 64

MALFORMED_RECORDS = {
    "top level is a list": "[1, 2]",
    "top level is null": "null",
    "top level is a string": '"source_hashes"',
    "source_hashes missing": "{}",
    "source_hashes is a list": '{"source_hashes": ["notes.md"]}',
    "source_hashes is a string": '{"source_hashes": "notes.md"}',
    "source_hashes is null": '{"source_hashes": null}',
    "source_hashes is a number": '{"source_hashes": 7}',
    "a hash is a number": '{"source_hashes": {"notes.md": 5}}',
    "a hash is nested garbage": '{"source_hashes": {"notes.md": {"x": [1, {"y": null}]}}}',
    "a hash is not hex": '{"source_hashes": {"notes.md": "not-a-sha256"}}',
    "a name is empty": '{"source_hashes": {"": "%s"}}' % _GOOD_HASH,
    "nesting too deep to parse": '{"source_hashes": ' + "[" * 200000 + "]" * 200000 + "}",
}


@pytest.mark.parametrize("shape", sorted(MALFORMED_RECORDS))
def test_a_malformed_stub_record_is_a_clean_refusal_and_the_item_stays(tmp_path, shape):
    """MEASURED 2026-11-06 before the fix: a list or a string in `source_hashes` passed `set(hashes)` and then crashed
    `_kept_changes` on `.get` (AttributeError), a too-deep record crashed the parse (RecursionError) -- each escaping
    stub_state and stopping the whole plan -- and a number or nested value was silently compared as a hash. Each shape is
    now "record cannot be read", the folder is kept, and nothing in it is planned."""
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old\n"})
    (t / "ARCHIVED.md").write_text("# Archived\n\n<!-- " + MALFORMED_RECORDS[shape] + " -->\n", encoding="utf-8")
    doc = hk.plan(cfg, now=now + LATER)
    notes = doc["coverage"]["archive_inventory"]["stub_notes"]
    assert len(notes) == 1 and "record cannot be read" in notes[0] and "1 other item(s)" in notes[0], notes
    assert not [a for a in doc["actions"] if str(t) in json.dumps(a)], "nothing in a folder with an unreadable record"
    assert (t / "notes.md").read_bytes() == b"old\n" and (t / "ARCHIVED.md").exists()


def test_TWIN_a_well_formed_record_still_compares(tmp_path):
    """The control for every shape above: the same folder with a readable record is compared, not refused."""
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old\n"})
    (t / "ARCHIVED.md").write_text(
        "# Archived\n\n<!-- " + json.dumps({"source_hashes": {"notes.md": _GOOD_HASH}}) + " -->\n", encoding="utf-8")
    notes = _notes(cfg, now)
    assert len(notes) == 1 and "edited since" in notes[0] and "cannot be read" not in notes[0], notes


def test_a_huge_stub_is_refused_unread(tmp_path, monkeypatch):
    """A stub past the record cap is not read into memory to look for its record."""
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old\n"})
    monkeypatch.setattr(hk, "STUB_RECORD_MAX_BYTES", 1024)
    (t / "ARCHIVED.md").write_text("# Archived\n\n<!-- {\"source_hashes\": {}} -->\n" + "x" * 4096, encoding="utf-8")
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: pytest.fail("read " + str(self))
                        if self.name == "ARCHIVED.md" else real(self, *a, **k))
    notes = _notes(cfg, now)
    assert len(notes) == 1 and "record cannot be read" in notes[0] and "too large" in notes[0], notes


def test_the_refusal_names_why_the_record_cannot_be_read(tmp_path):
    cfg, t, now = _archived(tmp_path, **{"notes.md": b"old\n"})
    (t / "ARCHIVED.md").write_text('# A\n\n<!-- {"source_hashes": ["notes.md"]} -->\n', encoding="utf-8")
    notes = _notes(cfg, now)
    assert "source_hashes is list, not an object" in notes[0], notes
