"""The shared archiver takes each toolkit's own save extensions (`save_exts`, user ruling 2026-11-26).

X4 passes none of Skyrim's: a `.ess`/`.skse` met in an X4 task folder is an ordinary file there. The archiver's X4
clauses (save names, the profile subtree) hold for every caller, so no toolkit archives an X4 save it meets.
The default stays both games' extensions, so a caller that passes nothing behaves as before.
"""
import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HELPER = Path(os.environ.get("HOUSEKEEP_ARCHIVE_UNDER_TEST", REPO / "scripts" / "housekeep_archive.py"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ha = _load("housekeep_archive_save_exts", HELPER)


def _task(tmp_path, name):
    task = tmp_path / "tasks" / "t1"
    task.mkdir(parents=True)
    (task / "notes.md").write_text("evidence")
    (task / name).write_bytes(b"fixture")
    return task


def test_x4_extensions_archive_a_skyrim_ess_as_an_ordinary_file(tmp_path):
    task = _task(tmp_path, "fixture.ess")
    rec = ha.archive_folder(task, tmp_path / "nas", None, save_exts=())
    assert "fixture.ess" in rec["source_hashes"]


def test_TWIN_the_default_still_refuses_a_skyrim_ess(tmp_path):
    task = _task(tmp_path, "fixture.ess")
    with pytest.raises(ha.ArchiveError, match="holds a save"):
        ha.archive_folder(task, tmp_path / "nas", None)


@pytest.mark.parametrize("name", ["save_001.xml.gz", "quicksave.xml", "autosave_02.xml.gz"])
def test_x4_save_names_are_refused_whatever_the_extensions(tmp_path, name):
    task = _task(tmp_path, name)
    with pytest.raises(ha.ArchiveError, match="holds a save"):
        ha.archive_folder(task, tmp_path / "nas", None, save_exts=())


def test_is_save_takes_the_callers_extensions():
    assert ha.is_save("c:/x/a.ess")
    assert not ha.is_save("c:/x/a.ess", ())
    assert ha.is_save("c:/x/a.sav", (".sav",))
    assert ha.is_save("d:/profiles/documents/egosoft/x4/1/content.xml", ())


def test_restore_and_scope_take_the_extensions_too(tmp_path):
    import inspect
    for fn in (ha.restore_archive, ha.archive_scope, ha.review_archives, ha.verify_source):
        assert "save_exts" in inspect.signature(fn).parameters, fn.__name__


def test_x4housekeep_passes_x4_extensions_on_every_archiver_call():
    src = (REPO / "scripts" / "x4housekeep.py").read_text(encoding="utf-8")
    assert "X4_SAVE_EXTS = ()" in src
    for call in ("archive_scope(action, cfg, X4_SAVE_EXTS)", "save_exts=X4_SAVE_EXTS)",
                 "verify_source(Path(a['path']), rec, X4_SAVE_EXTS)"):
        assert call in src, call
    assert '.endswith((".ess",".skse"))' not in src
