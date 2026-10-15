"""Preservation receives physical spellings; rule comparisons retain their old form."""
import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[3] / "agent/guards/claude-hooks/hook_facts.py"
spec = importlib.util.spec_from_file_location("save_provenance_hook_facts", SOURCE)
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


def evidence(command, cwd="/Fixture/Workspace", roots=None):
    records = []
    payload = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}
    ordinary = facts.facts(payload, roots or {})
    actual = facts.facts(payload, roots or {}, evidence=records)
    assert actual == ordinary
    return records


def test_relative_save_keeps_physical_case_after_relative_cd_and_popd():
    records = evidence('pushd "Saves"; rm "Progress.xml.gz"; popd; echo x > "Other.xml.gz"')
    assert any(r["path"] == "/Fixture/Workspace/Saves/Progress.xml.gz" and r["operation"] == "delete" for r in records)
    assert any(r["path"] == "/Fixture/Workspace/Other.xml.gz" and r["operation"] == "write" for r in records)


def test_root_variable_absolute_paths_and_copy_direction_are_preserved():
    records = evidence('cd "$X4_SAVES"; cp "A.xml.gz" "/Out/Copy.xml.gz"; mv "B.xml.gz" "C.xml.gz"',
                       roots={"saves": "/Fixture/Saves"})
    assert not any(r["path"] == "/Fixture/Saves/A.xml.gz" for r in records)
    assert any(r["path"] == "/Out/Copy.xml.gz" and r["operation"] == "write" for r in records)
    assert any(r["path"] == "/Fixture/Saves/B.xml.gz" and r["operation"] == "move" for r in records)
    assert any(r["path"] == "/Fixture/Saves/C.xml.gz" and r["operation"] == "write" for r in records)


def test_alternative_cd_lanes_keep_original_spelling():
    records = evidence('cd "${CHOICE:+Saves}/Fixture"; rm "A.xml.gz"')
    paths = {r["path"] for r in records if r["operation"] == "delete"}
    assert {"/Fixture/A.xml.gz", "/Fixture/Workspace/Saves/Fixture/A.xml.gz"} <= paths


def test_unknown_directory_and_filtered_tree_are_not_exact_file_evidence():
    records = evidence('cd "$UNKNOWN"; rm A.xml.gz')
    assert any(r["unresolved"] for r in records)
    records = evidence('for d in Saves saves; do cd "/Fixture/$d"; rm "A.xml.gz"; done')
    assert all(r["unresolved"] for r in records if r["operation"] == "delete")
    records = evidence('find "/Fixture/Saves" -name "*.xml.gz" -delete')
    assert any(r["scope"] == "filtered-tree" for r in records)


def test_directory_copy_source_spellings_follow_the_same_cwd_and_assignment_lane():
    records = evidence('SRC="Input/Case.xml.gz"; cd "Work"; cp -- "$SRC" "$X4_SAVES/"',
                       roots={"saves": "/Fixture/Saves"})
    row = next(r for r in records if r["scope"] == "copy-destination")
    assert row["copy_sources"] == [{"path": "/Fixture/Workspace/Work/Input/Case.xml.gz", "unresolved": False}]
    assert row["path"] == "/Fixture/Saves/"
