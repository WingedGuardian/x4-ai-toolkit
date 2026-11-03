"""A BaseX disposition receipt identifies a folder, not one spelling of it, and a stale one names its remedy."""
import json
import os
from pathlib import Path

import pytest

import x4basex_artifacts as a


def ready():
    return None


def _stage(root):
    out = root / "_stage"
    a.prepare_output(out, "stage", proof=ready)
    (out / "file.xml").write_text("<generated/>")
    return out


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive paths")
def test_a_case_only_spelling_difference_is_the_same_folder(tmp_path):
    out = _stage(tmp_path)
    marker = out / a.RECEIPT
    declared = json.loads(marker.read_text())
    declared["path"] = declared["path"].upper()
    marker.write_text(json.dumps(declared))
    assert a.dispose([out], proof=ready, artifact_root=tmp_path)[0]["path"] == str(out)


@pytest.mark.skipif(os.name != "nt", reason="backslash and forward slash are one separator only on Windows")
def test_a_slash_only_spelling_difference_is_the_same_folder(tmp_path):
    out = _stage(tmp_path)
    marker = out / a.RECEIPT
    declared = json.loads(marker.read_text())
    declared["path"] = declared["path"].replace("\\", "/")
    marker.write_text(json.dumps(declared))
    assert a.dispose([out], proof=ready, artifact_root=tmp_path)


def test_a_receipt_for_another_folder_is_refused_and_names_the_remedy(tmp_path):
    out = _stage(tmp_path)
    moved = tmp_path / "moved"
    os.rename(out, moved)
    with pytest.raises(OSError) as info:
        a.dispose([moved], proof=ready, artifact_root=tmp_path / "elsewhere")
    message = str(info.value)
    assert str(out) in message and str(moved) in message       # both spellings: what it says, where it is
    assert "moved" in message and "Recycle Bin" in message     # what to do about it
    assert (moved / "file.xml").exists()


@pytest.mark.parametrize("change", [dict(kind="database"), dict(producer="someone-else"), dict(disposition="keep"),
                                    dict(extra=1), dict(v=2)])
def test_every_other_field_still_has_to_match_exactly(tmp_path, change):
    out = _stage(tmp_path)
    marker = out / a.RECEIPT
    declared = json.loads(marker.read_text())
    declared.update(change)
    marker.write_text(json.dumps(declared))
    with pytest.raises(OSError, match="invalid BaseX disposition receipt"):
        a.dispose([out], proof=ready, artifact_root=tmp_path)
    assert (out / "file.xml").exists()


def test_a_non_string_path_is_invalid(tmp_path):
    out = _stage(tmp_path)
    marker = out / a.RECEIPT
    declared = json.loads(marker.read_text())
    declared["path"] = 7
    marker.write_text(json.dumps(declared))
    with pytest.raises(OSError, match="invalid BaseX disposition receipt"):
        a.dispose([out], proof=ready, artifact_root=tmp_path)
