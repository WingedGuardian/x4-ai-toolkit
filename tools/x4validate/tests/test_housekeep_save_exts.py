"""The shared archiver always protects built-in save names, extensions and profile paths."""
import importlib.util
import json
import os
from pathlib import Path
import zipfile

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


@pytest.mark.parametrize("name", ["fixture.ESS", "fixture.SkSe"])
def test_empty_extensions_still_refuse_skyrim_saves(tmp_path, name):
    task = _task(tmp_path, name)
    with pytest.raises(ha.ArchiveError, match="holds a save"):
        ha.archive_folder(task, tmp_path / "nas", None, save_exts=())
    assert (task / name).read_bytes() == b"fixture"
    assert not (tmp_path / "nas").exists()


def test_TWIN_the_default_still_refuses_a_skyrim_ess(tmp_path):
    task = _task(tmp_path, "fixture.ess")
    with pytest.raises(ha.ArchiveError, match="holds a save"):
        ha.archive_folder(task, tmp_path / "nas", None)


def test_archive_refuses_protected_directory_members(tmp_path):
    task = _task(tmp_path, "ordinary.txt")
    (task / "protected.SKSE").mkdir()
    with pytest.raises(ha.ArchiveError, match="protected save/profile path"):
        ha.archive_folder(task, tmp_path / "nas", None, save_exts=())


@pytest.mark.parametrize("name", ["save_001.xml.gz", "quicksave.xml", "autosave_02.xml.gz"])
def test_x4_save_names_are_refused_whatever_the_extensions(tmp_path, name):
    task = _task(tmp_path, name)
    with pytest.raises(ha.ArchiveError, match="holds a save"):
        ha.archive_folder(task, tmp_path / "nas", None, save_exts=())


def test_is_save_adds_the_callers_extensions_to_the_built_ins():
    assert ha.is_save("c:/x/a.ess")
    assert ha.is_save("c:/x/a.ess", ())
    assert ha.is_save("c:/x/a.skse", (".SAV",))
    assert ha.is_save("c:/x/a.sav", (".SAV",))
    assert ha.is_save("d:/profiles/documents/egosoft/x4/1/content.xml", ())


def test_restore_and_scope_take_the_extensions_too(tmp_path):
    import inspect
    for fn in (ha.restore_archive, ha.archive_scope, ha.review_archives, ha.verify_source):
        assert "save_exts" in inspect.signature(fn).parameters, fn.__name__


@pytest.mark.parametrize(("name", "recorded", "caller"), [
    ("fixture.ESS", [], None),
    ("fixture.SkSe", [".custom"], (".other",)),
    ("fixture.CUSTOM", [".custom"], (".other",)),
    ("fixture.other", [".custom"], (".OTHER",)),
])
def test_receipt_and_caller_extensions_preserve_the_restore_floor(tmp_path, name, recorded, caller):
    archive = tmp_path / "old.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("ordinary.txt", b"ordinary")
        output.writestr(name, b"fixture")
    task = tmp_path / "task"
    task.mkdir()
    stub = task / ha.STUB
    receipt = json.dumps({"zip": str(archive), "save_exts": recorded})
    stub.write_text(f"<!-- {receipt} -->", encoding="utf-8")
    with pytest.raises(ha.ArchiveError, match="protected save"):
        ha.restore_archive(stub, caller)
    assert list(task.iterdir()) == [stub]


def test_a_record_without_save_exts_restores_with_the_default(tmp_path):
    task = _task(tmp_path, "notes.txt")
    rec = ha.archive_folder(task, tmp_path / "nas", None)
    stub = task / ha.STUB
    text = stub.read_text(encoding="utf-8")
    assert '"save_exts": [".ess", ".skse"]' in text
    stub.write_text(text.replace(', "save_exts": [".ess", ".skse"]', ""), encoding="utf-8")   # a pre-4.3.1 stub
    for victim in rec["victims"]:
        victim.unlink()
    assert ha.restore_archive(stub) == task and (task / "notes.txt").read_bytes() == b"fixture"


def test_x4housekeep_passes_x4_extensions_on_every_archiver_call():
    import ast
    src = (REPO / "scripts" / "x4housekeep.py").read_text(encoding="utf-8")
    assert "X4_SAVE_EXTS = ()" in src
    assert '.endswith((".ess",".skse"))' not in src
    seen = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                "archive_scope", "archive_folder", "verify_source", "review_archives", "restore_archive"):
            names = [a.id for a in node.args if isinstance(a, ast.Name)] + [
                k.value.id for k in node.keywords if isinstance(k.value, ast.Name)]
            seen.setdefault(node.func.attr, []).append("X4_SAVE_EXTS" in names)
    # Every call, one by one: reverting any single call site must fail this.
    assert seen.get("archive_scope") == [True, True], seen
    for name in ("archive_folder", "verify_source", "review_archives"):
        assert seen.get(name) == [True], (name, seen)
