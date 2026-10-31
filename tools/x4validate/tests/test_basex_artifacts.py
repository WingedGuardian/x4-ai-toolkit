"""Generated BaseX caches are disposed directly; source/evidence never qualify."""
import json
import os
from pathlib import Path

import pytest

import x4basex_artifacts as a


def ready():
    return None


def test_new_workspace_has_creation_receipt_and_rebuild_does_not_recycle(tmp_path):
    out = tmp_path / "custom"
    a.prepare_output(out, "effective", proof=ready)
    marker = json.loads((out / a.RECEIPT).read_text())
    assert marker == a.receipt(out, "effective")
    (out / "library.xml").write_text("<library/>")
    done = a.dispose([out], proof=ready)
    assert done[0]["path"] == str(out) and not out.exists()


def test_refused_input_proof_preserves_all_old_contents(tmp_path):
    out = tmp_path / "old"
    a.prepare_output(out, "stage", proof=ready)
    p = out / "one.xml"
    p.write_text("<old/>")
    def absent():
        raise OSError("reference unavailable")
    with pytest.raises(OSError, match="reference unavailable"):
        a.dispose([out], proof=absent)
    assert p.read_text() == "<old/>"


def test_unknown_custom_workspace_is_kept_without_backfilling_receipt(tmp_path):
    out = tmp_path / "unknown"
    out.mkdir()
    p = out / "one.xml"
    p.write_text("<only-copy/>")
    with pytest.raises(OSError, match="unreceipted custom"):
        a.prepare_output(out, "stage", proof=ready)
    assert p.exists() and not (out / a.RECEIPT).exists()


@pytest.mark.parametrize("name", ["README.md", "debug.log", "save.xml.gz", ".housekeep-keep"])
def test_foreign_evidence_refuses_entire_batch_before_any_deletion(tmp_path, name):
    one, two = tmp_path / "one", tmp_path / "two"
    for p in (one, two):
        a.prepare_output(p, "stage", proof=ready)
        (p / "file.xml").write_text("<generated/>")
    (two / name).write_text("keep")
    with pytest.raises(OSError, match="unexpected file"):
        a.dispose([one, two], proof=ready)
    assert (one / "file.xml").exists() and (two / name).exists()


def test_canonical_legacy_slot_uses_explicit_cache_rule_without_faking_receipt(tmp_path):
    out = tmp_path / "_eff/tree"
    out.mkdir(parents=True)
    (out / "file.xml").write_text("<generated/>")
    assert not (out / a.RECEIPT).exists()
    a.dispose([out], proof=ready, artifact_root=tmp_path)
    assert not out.exists()


def test_database_directory_must_be_dropped_by_basex_not_filesystem_helper(tmp_path):
    db = tmp_path / "basex/data/x4eff"
    db.mkdir(parents=True)
    (db / "tbl.basex").write_bytes(b"database")
    with pytest.raises(OSError, match="unreceipted custom"):
        a.dispose([db], proof=ready, artifact_root=tmp_path)
    assert (db / "tbl.basex").read_bytes() == b"database"


def test_database_disposition_is_written_before_native_creation(tmp_path):
    marker = a.declare_database("x4eff", proof=ready, artifact_root=tmp_path)
    db = tmp_path / "basex/data/x4eff"
    assert not db.exists() and marker.is_file()
    assert json.loads(marker.read_text()) == a.receipt(db, "database")
    db.mkdir(parents=True)
    (db / "tbl.basex").write_bytes(b"live")
    before = marker.read_bytes()
    with pytest.raises(OSError, match="still exists after DROP DB"):
        a.declare_database("x4eff", proof=ready, artifact_root=tmp_path)
    assert marker.read_bytes() == before and (db / "tbl.basex").read_bytes() == b"live"


def test_database_declaration_does_not_create_receipts_for_unknown_names(tmp_path):
    with pytest.raises(ValueError, match="not a toolkit-owned"):
        a.declare_database("personal", proof=ready, artifact_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_database_receipts_cannot_be_created_inside_reference(tmp_path):
    with pytest.raises(OSError, match="overlaps an input"):
        a.declare_database("x4eff", proof=ready, artifact_root=tmp_path, sources=(tmp_path,))
    assert list(tmp_path.iterdir()) == []


def test_only_exact_generated_metadata_slots_qualify(tmp_path):
    directory = tmp_path / "basex"
    directory.mkdir()
    known = directory / "coverage-x4eff.json"
    known.write_text('{"coverage": "generated"}')
    other = directory / "notes.json"
    other.write_text('{"notes": "handwritten"}')
    with pytest.raises(OSError, match="not a recognized"):
        a.dispose([known, other], proof=ready, artifact_root=tmp_path)
    assert known.exists() and other.exists()
    a.dispose([known], proof=ready, artifact_root=tmp_path)
    assert not known.exists() and other.exists()


def test_invalid_receipt_and_wrong_path_are_not_authority(tmp_path):
    out = tmp_path / "one"
    a.prepare_output(out, "stage", proof=ready)
    marker = out / a.RECEIPT
    d = json.loads(marker.read_text())
    d["path"] = str(tmp_path / "other")
    marker.write_text(json.dumps(d))
    with pytest.raises(OSError, match="invalid BaseX disposition"):
        a.dispose([out], proof=ready)
    assert marker.exists()


@pytest.mark.parametrize("content", ["invalid json", "[]"])
def test_malformed_receipt_is_a_refusal_and_preserves_workspace(tmp_path, content):
    out = tmp_path / "one"
    a.prepare_output(out, "stage", proof=ready)
    (out / a.RECEIPT).write_text(content)
    with pytest.raises(OSError, match="invalid BaseX disposition"):
        a.dispose([out], proof=ready)
    assert out.is_dir()


def test_source_overlap_is_refused_even_for_new_output(tmp_path):
    source = tmp_path / "reference"
    source.mkdir()
    with pytest.raises(OSError, match="overlaps an input"):
        a.prepare_output(source / "tree", "effective", proof=ready, sources=(source,))
    assert not (source / "tree").exists()


def test_failure_is_reported_and_never_falls_back(tmp_path, monkeypatch, capsys):
    out = tmp_path / "one"
    a.prepare_output(out, "stage", proof=ready)
    def locked(*args, **kwargs):
        raise OSError("locked")
    monkeypatch.setattr(a.shutil, "rmtree", locked)
    with pytest.raises(OSError, match="locked"):
        a.dispose([out], proof=ready)
    d = json.loads(capsys.readouterr().err)
    assert d["permanently_deleted"] == [] and d["recycled"] == []
    assert d["failed"][0]["path"] == str(out) and out.exists()


def test_native_database_rebuild_keeps_only_the_current_generation(tmp_path):
    import shutil
    import subprocess
    java = shutil.which("java")
    jar = a.ARTIFACT_ROOT / "basex/BaseX.jar"
    if not java or not jar.is_file():
        pytest.skip("native lifecycle requires the installed Java/BaseX runtime")
    if getattr(jar.stat(), "st_file_attributes", 0) & 1:
        pytest.skip("read-only input jar cannot be safely hard-linked into disposable scratch")
    root = tmp_path / "fixture"
    home = root / "basex"
    home.mkdir(parents=True)
    os.link(jar, home / "BaseX.jar")
    (home / ".basexhome").write_bytes(b"")
    env = {k: v for k, v in os.environ.items()
           if k not in {"JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"}}
    command = [java, "-Xmx128m", "-XX:ActiveProcessorCount=1", "-cp", "BaseX.jar", "org.basex.BaseX"]
    def run(*args):
        p = subprocess.run([*command, *args], cwd=home, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert p.returncode == 0, p.stdout + p.stderr
        return p.stdout
    # Read-only precondition: never execute CREATE/DROP until the real runtime
    # confirms that its database path is in this isolated scratch home.
    info = run("-c", "INFO")
    assert str(home).replace(chr(92), "/").lower() in info.replace(chr(92), "/").lower(), info
    for _ in range(2):
        marker = a.declare_database("x4raw", proof=ready, artifact_root=root)
        assert marker.is_file() and not (home / "data/x4raw").exists()
        run("-c", "CREATE DB x4raw <fixture/>")
        assert run("-q", "count(collection('x4raw'))").strip() == "1"
        assert {p.name for p in (home / "data").iterdir() if p.is_dir()} == {"x4raw"}
        run("-c", "DROP DB x4raw")
        assert not (home / "data/x4raw").exists()
    assert not list(home.rglob("*.zip")), "native rebuild created a backup"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_junction_inside_cache_preserves_both_cache_and_target(tmp_path):
    import subprocess
    out = tmp_path / "one"
    a.prepare_output(out, "stage", proof=ready)
    external = tmp_path / "external"
    external.mkdir()
    p = external / "unique.xml"
    p.write_text("<unique/>")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(out / "linked"), str(external)],
                   check=True, capture_output=True)
    with pytest.raises(OSError, match="link/reparse"):
        a.dispose([out], proof=ready)
    assert p.read_text() == "<unique/>" and out.exists()
