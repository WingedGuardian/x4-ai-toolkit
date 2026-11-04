"""scripts/verify-mod-pack.py refuses an incomplete or unpasteable mod upload folder.

Every twin below breaks ONE thing in an otherwise complete pack and compares the WHOLE problem
list to the one expected problem. That exact comparison is the control: it fails if the rest of
the pack stopped being valid, and it fails if the break cascades into unrelated problems.

Everything happens under tmp_path. Nothing here touches a real mod, a real release folder or the
network.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from _layout import require_repo

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "verify-mod-pack.py"

VERSION = "1.2.0"
ARCHIVE = "sample_mod-1.2.0.zip"
ARCHIVE_BYTES = b"archive bytes, not opened by the checker"
DESCRIPTION = f"NEXUS-DESCRIPTION-v{VERSION}.txt"
SUMMARY = f"NEXUS-SUMMARY-v{VERSION}.txt"
CHANGELOG = f"NEXUS-CHANGELOG-v{VERSION}.txt"
BEFORE = f"description-BEFORE-v{VERSION}.txt"
NOTES = "UPLOAD-NOTES.md"
MANIFEST = "SHIPPED-MANIFEST.json"
RECORD = "REVIEW-RECORD.md"
PASTE_FILES = [DESCRIPTION, SUMMARY, CHANGELOG, BEFORE]
TEXT_FILES = PASTE_FILES + [NOTES, RECORD]


def _mod():
    spec = importlib.util.spec_from_file_location("verify_mod_pack", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(**changes) -> dict:
    data = {
        "mod": "sample_mod",
        "version": VERSION,
        "status": "shipped",
        "archives": [{"name": ARCHIVE, "size": len(ARCHIVE_BYTES),
                      "sha256": hashlib.sha256(ARCHIVE_BYTES).hexdigest()}],
        "files": [{"path": "ext_01.cat", "size": 10, "sha256": "a" * 64}],
    }
    data.update(changes)
    return data


def _write_manifest(pack: Path, data) -> None:
    (pack / MANIFEST).write_bytes(json.dumps(data).encode("utf-8"))


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    folder = tmp_path / "1.2.0"
    folder.mkdir()
    (folder / ARCHIVE).write_bytes(ARCHIVE_BYTES)
    (folder / DESCRIPTION).write_bytes(b"[b]A mod.[/b]\nIt does one thing.\n")
    (folder / SUMMARY).write_bytes(b"A mod that does one thing.\n")
    (folder / CHANGELOG).write_bytes(b"Fixed one thing\nAdded another thing\n")
    (folder / BEFORE).write_bytes(b"[b]A mod.[/b]\nThe old text.\n")
    (folder / NOTES).write_bytes(b"Upload the zip, then paste the description.\n")
    (folder / RECORD).write_bytes(b"Baseline, tested set, findings and answers.\n")
    _write_manifest(folder, _manifest())
    return folder


def found(mod, folder: Path) -> list[tuple[str, str]]:
    return [(p.code, p.file) for p in mod.check_pack(folder).problems]


# ---- the control ---------------------------------------------------------------------------


def test_a_complete_pack_PASSES_and_states_what_it_checked(pack, capsys):
    mod = _mod()
    assert found(mod, pack) == []
    assert mod.main([str(pack)]) == 0
    out = capsys.readouterr().out
    assert "PACK OK" in out, out
    assert "4 paste file(s)" in out, out
    assert "1 archive(s) matched" in out, out
    assert "2 changelog line(s)" in out, out


def test_the_real_exit_code_of_the_real_script(pack, tmp_path):
    ok = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), str(pack)],
                        capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (pack / SUMMARY).write_bytes(b"")
    bad = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), str(pack)],
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert f"[empty] {SUMMARY}" in bad.stdout, bad.stdout


# ---- missing or empty required files -------------------------------------------------------


@pytest.mark.parametrize("name", TEXT_FILES + [MANIFEST])
def test_a_missing_required_file_is_named(pack, name):
    (pack / name).unlink()
    assert found(_mod(), pack) == [("missing", _mod().required_label(name))]


def test_a_missing_archive_is_named(pack):
    (pack / ARCHIVE).unlink()
    mod = _mod()
    assert sorted(found(mod, pack)) == [("archive-missing", ARCHIVE), ("missing", "<archive>")]


@pytest.mark.parametrize("name", TEXT_FILES)
@pytest.mark.parametrize("body", [b"", b"  \r\n\n\t\n", b"\xef\xbb\xbf"], ids=["zero", "blank", "bom"])
def test_an_empty_required_file_is_named(pack, name, body):
    (pack / name).write_bytes(body)
    assert found(_mod(), pack) == [("empty", name)]


def test_an_empty_manifest_is_named(pack):
    (pack / MANIFEST).write_bytes(b" \n")
    assert found(_mod(), pack) == [("empty", MANIFEST)]


def test_a_zero_byte_archive_is_named_even_when_the_manifest_agrees(pack):
    (pack / ARCHIVE).write_bytes(b"")
    _write_manifest(pack, _manifest(archives=[{"name": ARCHIVE, "size": 0,
                                               "sha256": hashlib.sha256(b"").hexdigest()}]))
    assert found(_mod(), pack) == [("empty", ARCHIVE)]


def test_two_candidates_for_one_required_file_are_refused(pack):
    (pack / "NEXUS-SUMMARY-v1.1.0.txt").write_bytes(b"An older summary.\n")
    assert found(_mod(), pack) == [("ambiguous", "NEXUS-SUMMARY-v<version>.txt")]


def test_a_file_named_for_another_version_is_refused(pack):
    (pack / SUMMARY).rename(pack / "NEXUS-SUMMARY-v1.1.0.txt")
    assert found(_mod(), pack) == [("wrong-version", "NEXUS-SUMMARY-v1.1.0.txt")]


# ---- what a paste file may contain ---------------------------------------------------------


@pytest.mark.parametrize("name", PASTE_FILES)
@pytest.mark.parametrize("text", ["line one\n<br />line two\n", "line one <BR>\n"], ids=["xhtml", "upper"])
def test_a_br_tag_in_a_paste_file_is_refused(pack, name, text):
    (pack / name).write_bytes(text.encode("utf-8"))
    assert found(_mod(), pack) == [("br", name)]


@pytest.mark.parametrize("name", PASTE_FILES)
@pytest.mark.parametrize("entity", ["&amp;", "&lt;", "&#92;", "&#x5C;", "&eacute;"])
def test_an_html_entity_in_a_paste_file_is_refused(pack, name, entity):
    (pack / name).write_bytes(f"this {entity} that\n".encode("utf-8"))
    assert found(_mod(), pack) == [("entity", name)]


@pytest.mark.parametrize("text", ["fish & chips\n", "see ?a=1&b=2;c\n", "AT&T\n"])
def test_a_bare_ampersand_is_NOT_an_entity(pack, text):
    (pack / DESCRIPTION).write_bytes(text.encode("utf-8"))
    assert found(_mod(), pack) == []


@pytest.mark.parametrize("name", PASTE_FILES)
def test_crlf_in_a_paste_file_is_refused(pack, name):
    (pack / name).write_bytes(b"line one\r\nline two\r\n")
    problems = _mod().check_pack(pack).problems
    assert [(p.code, p.file) for p in problems] == [("crlf", name)]
    assert "2 " in problems[0].detail


def test_crlf_in_a_file_nobody_pastes_is_allowed(pack):
    (pack / NOTES).write_bytes(b"Upload the zip.\r\n")
    (pack / RECORD).write_bytes(b"Findings.\r\n")
    assert found(_mod(), pack) == []


@pytest.mark.parametrize("name", PASTE_FILES)
@pytest.mark.parametrize("text", [
    "# Paste this into the description box\nThe text.\n",
    "#comment with no space\nThe text.\n",
    "\n\n# after blank lines\nThe text.\n",
    "﻿# after a byte order mark\nThe text.\n",
    "The text.\n## A markdown heading further down\nMore text.\n",
    "Version: 1.2.0\nThe text.\n",
    "Generated by: a script\nThe text.\n",
    "mod name:\nThe text.\n",
], ids=["hash", "hash-nospace", "after-blank", "after-bom", "heading-later", "version", "generated", "no-value"])
def test_a_header_line_in_a_paste_file_is_refused(pack, name, text):
    (pack / name).write_bytes(text.encode("utf-8"))
    assert found(_mod(), pack) == [("header", name)]


@pytest.mark.parametrize("text", [
    "Fixed: a crash on load\nAdded: a new ship\n",   # an ordinary changelog line, not metadata
    "https://example.invalid/page\nThe text.\n",
    "[b]Requirements:[/b] none\n",
    "The text.\n#1 cause of the crash, fixed\n",     # no space after the hash, not on the first line
    "The text.\nVersion: 1.2.0 is the first with this\n",  # a preamble can only open a file
], ids=["changelog-prefix", "url", "bbcode", "numbered-later", "key-later"])
def test_ordinary_text_is_NOT_a_header(pack, text):
    (pack / CHANGELOG).write_bytes(text.encode("utf-8"))
    assert found(_mod(), pack) == []


def test_a_paste_file_that_is_not_utf8_is_refused(pack):
    (pack / DESCRIPTION).write_bytes(b"caf\xe9 in the wrong codepage\n")
    assert found(_mod(), pack) == [("not-utf8", DESCRIPTION)]


# ---- changelog line length -----------------------------------------------------------------


def test_a_changelog_line_at_the_limit_passes(pack):
    (pack / CHANGELOG).write_bytes(("x" * 120 + "\n").encode("utf-8"))
    assert found(_mod(), pack) == []


def test_the_limit_counts_CHARACTERS_not_bytes(pack):
    (pack / CHANGELOG).write_bytes(("é" * 120 + "\n").encode("utf-8"))
    assert found(_mod(), pack) == []


def test_every_changelog_line_over_the_limit_is_named(pack):
    (pack / CHANGELOG).write_bytes(("ok\n" + "x" * 121 + "\nok\n" + "y" * 200 + "\n").encode("utf-8"))
    problems = _mod().check_pack(pack).problems
    assert [(p.code, p.file) for p in problems] == [("line-too-long", CHANGELOG)] * 2
    assert "line 2" in problems[0].detail and "121" in problems[0].detail
    assert "line 4" in problems[1].detail and "200" in problems[1].detail


def test_a_long_line_in_the_description_is_fine(pack):
    (pack / DESCRIPTION).write_bytes(("x" * 500 + "\n").encode("utf-8"))
    assert found(_mod(), pack) == []


# ---- the manifest and the archive hash -----------------------------------------------------


def test_an_archive_differing_from_the_manifest_is_refused(pack):
    (pack / ARCHIVE).write_bytes(b"X" * len(ARCHIVE_BYTES))  # same size, so only the hash can see it
    problems = _mod().check_pack(pack).problems
    assert [(p.code, p.file) for p in problems] == [("archive-hash", ARCHIVE)]
    assert hashlib.sha256(ARCHIVE_BYTES).hexdigest() in problems[0].detail


def test_an_archive_size_differing_from_the_manifest_is_refused(pack):
    row = _manifest()["archives"][0]
    _write_manifest(pack, _manifest(archives=[{**row, "size": row["size"] + 1}]))
    assert found(_mod(), pack) == [("archive-size", ARCHIVE)]


def test_an_uppercase_hash_still_matches(pack):
    row = _manifest()["archives"][0]
    _write_manifest(pack, _manifest(archives=[{**row, "sha256": row["sha256"].upper()}]))
    assert found(_mod(), pack) == []


@pytest.mark.parametrize("name", ["extra.zip", "extra.7z", "EXTRA.RAR"])
def test_an_archive_the_manifest_does_not_list_is_refused(pack, name):
    (pack / name).write_bytes(b"something else")
    assert found(_mod(), pack) == [("archive-unlisted", name)]


def test_every_listed_archive_is_checked(pack):
    second = b"an optional download"
    (pack / "optional.zip").write_bytes(second)
    row = _manifest()["archives"][0]
    _write_manifest(pack, _manifest(archives=[row, {"name": "optional.zip", "size": len(second),
                                                    "sha256": "0" * 64}]))
    assert found(_mod(), pack) == [("archive-hash", "optional.zip")]


@pytest.mark.parametrize("change, needle", [
    ({"status": "draft"}, "status"),
    ({"version": ""}, "version"),
    ({"version": 12}, "version"),
    ({"files": []}, "files"),
    ({"files": "ext_01.cat"}, "files"),
    ({"files": [{"path": "ext_01.cat"}]}, "files[0]"),
    ({"files": [{"path": "ext_01.cat", "size": True, "sha256": "a" * 64}]}, "files[0]"),
    ({"files": [{"path": "ext_01.cat", "size": 1, "sha256": "short"}]}, "files[0]"),
    ({"files": [{"path": "", "size": 1, "sha256": "a" * 64}]}, "files[0]"),
], ids=["status", "version-empty", "version-type", "files-empty", "files-type", "file-fields",
        "file-size-bool", "file-hash-form", "file-path-empty"])
def test_a_malformed_manifest_field_is_named(pack, change, needle):
    _write_manifest(pack, _manifest(**change))
    problems = _mod().check_pack(pack).problems
    assert [(p.code, p.file) for p in problems] == [("manifest", MANIFEST)]
    assert needle in problems[0].detail


def test_a_manifest_with_no_archives_is_refused(pack):
    _write_manifest(pack, _manifest(archives=[]))
    problems = _mod().check_pack(pack).problems
    assert [(p.code, p.file) for p in problems] == [("manifest", MANIFEST)]
    assert "archives" in problems[0].detail


@pytest.mark.parametrize("name", ["../" + ARCHIVE, "..\\" + ARCHIVE, "sub/" + ARCHIVE, "C:" + ARCHIVE, ".."])
def test_an_archive_name_that_leaves_the_folder_is_refused_without_reading_it(pack, name):
    row = _manifest()["archives"][0]
    # Plant the file the traversal would reach, so a checker that followed it would match.
    (pack.parent / ARCHIVE).write_bytes(ARCHIVE_BYTES)
    _write_manifest(pack, _manifest(archives=[{**row, "name": name}]))
    problems = _mod().check_pack(pack).problems
    assert ("manifest", MANIFEST) in [(p.code, p.file) for p in problems]
    assert any("plain file name" in p.detail for p in problems)


def test_an_archive_row_with_a_bad_hash_form_is_refused(pack):
    row = _manifest()["archives"][0]
    _write_manifest(pack, _manifest(archives=[{**row, "sha256": "not-a-hash"}]))
    problems = _mod().check_pack(pack).problems
    assert [(p.code, p.file) for p in problems] == [("manifest", MANIFEST)]
    assert "archives[0]" in problems[0].detail


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b"\xff\xfe"], ids=["syntax", "not-object", "encoding"])
def test_a_manifest_that_cannot_be_used_is_refused_once(pack, body):
    (pack / MANIFEST).write_bytes(body)
    assert found(_mod(), pack) == [("manifest", MANIFEST)]


# ---- a folder or file that cannot be read is a refusal, never an OK -------------------------


def test_an_EMPTY_folder_names_every_required_file(tmp_path, capsys):
    folder = tmp_path / "empty"
    folder.mkdir()
    mod = _mod()
    assert sorted(f for code, f in found(mod, folder) if code == "missing") == sorted(
        [mod.required_label(n) for n in TEXT_FILES + [MANIFEST]] + ["<archive>"])
    assert mod.main([str(folder)]) == 1
    assert "PACK REFUSED: 8 problem(s)" in capsys.readouterr().out


def test_a_folder_that_does_not_exist_is_refused(tmp_path, capsys):
    assert _mod().main([str(tmp_path / "nowhere")]) == 2
    captured = capsys.readouterr()
    assert "PACK REFUSED" in captured.err and "PACK OK" not in captured.out


def test_a_FILE_given_as_the_folder_is_refused(tmp_path, capsys):
    target = tmp_path / "file.txt"
    target.write_bytes(b"not a folder")
    assert _mod().main([str(target)]) == 2
    assert "PACK REFUSED" in capsys.readouterr().err


def test_a_folder_listing_that_raises_is_refused(pack, monkeypatch, capsys):
    mod = _mod()

    def refuse(_path):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(mod.os, "listdir", refuse)
    assert mod.main([str(pack)]) == 2
    captured = capsys.readouterr()
    assert "PACK REFUSED" in captured.err and "Access is denied" in captured.err
    assert "PACK OK" not in captured.out


@pytest.mark.parametrize("name", [DESCRIPTION, NOTES, MANIFEST, ARCHIVE])
def test_a_required_file_that_cannot_be_read_is_refused(pack, name, monkeypatch):
    mod = _mod()

    def refusing_open(path, *args, **kwargs):
        if Path(path).name == name:
            raise PermissionError(13, "Access is denied")
        return open(path, *args, **kwargs)

    monkeypatch.setattr(mod, "open", refusing_open, raising=False)
    problems = mod.check_pack(pack).problems
    assert [(p.code, p.file) for p in problems] == [("unreadable", name)]
    assert "Access is denied" in problems[0].detail


def test_a_directory_in_a_required_files_place_is_refused(pack):
    (pack / RECORD).unlink()
    (pack / RECORD).mkdir()
    assert found(_mod(), pack) == [("unreadable", RECORD)]


def test_a_directory_named_like_an_archive_is_refused(pack):
    (pack / ARCHIVE).unlink()
    (pack / ARCHIVE).mkdir()
    assert found(_mod(), pack) == [("unreadable", ARCHIVE)]


# ---- it reports everything, and it explains itself ------------------------------------------


def test_ALL_problems_are_reported_not_only_the_first(pack, capsys):
    (pack / DESCRIPTION).write_bytes(b"# header\r\n<br />&amp;\r\n")
    (pack / SUMMARY).unlink()
    (pack / CHANGELOG).write_bytes(("z" * 130 + "\n").encode("utf-8"))
    (pack / ARCHIVE).write_bytes(b"changed")
    mod = _mod()
    assert mod.main([str(pack)]) == 1
    out = capsys.readouterr().out
    for piece in (f"[br] {DESCRIPTION}", f"[entity] {DESCRIPTION}", f"[crlf] {DESCRIPTION}",
                  f"[header] {DESCRIPTION}", "[missing] NEXUS-SUMMARY-v<version>.txt",
                  f"[line-too-long] {CHANGELOG}", f"[archive-hash] {ARCHIVE}",
                  f"[archive-size] {ARCHIVE}"):
        assert piece in out, piece + "\n" + out
    assert "PACK REFUSED: 8 problem(s)" in out, out


def test_help_documents_the_manifest_and_the_header_rule(capsys):
    with pytest.raises(SystemExit) as stop:
        _mod().main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for word in ("SHIPPED-MANIFEST.json", '"status"', '"archives"', '"files"', '"sha256"', '"size"',
                 "NEXUS-DESCRIPTION-v", "description-BEFORE-v", "UPLOAD-NOTES.md", "REVIEW-RECORD.md",
                 "header line", "Exit:"):
        assert word in out, word + "\n" + out


def test_the_script_is_stdlib_only_and_cannot_reach_the_network():
    tree = ast.parse(SCRIPT.read_bytes().decode("utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names), roots - set(sys.stdlib_module_names)
    assert not roots & {"urllib", "http", "socket", "ssl", "ftplib", "subprocess"}, roots


def test_the_skill_requires_this_script_and_the_file_names_it_checks():
    require_repo("agent/skills/x4-mod-release-review/SKILL.md")
    text = (REPO / "agent/skills/x4-mod-release-review/SKILL.md").read_bytes().decode("utf-8")
    assert "{{TOOLKIT}}/scripts/verify-mod-pack.py" in text
    for word in ("NEXUS-DESCRIPTION-v", "NEXUS-SUMMARY-v", "NEXUS-CHANGELOG-v", "description-BEFORE-v",
                 "UPLOAD-NOTES.md", "SHIPPED-MANIFEST.json", "REVIEW-RECORD.md", "archives"):
        assert word in text, word
