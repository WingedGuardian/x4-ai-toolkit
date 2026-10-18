"""Every tagged release's CLAUDE.md / AGENTS.md is KNOWN to the installers' 4.0 migration.

A tag missing from scripts/shipped-instruction-hashes.txt makes an upgrade from that release
keep the user's UNEDITED file as X4-NOTES.pre-4.0.md: safe, but noisy and wrongly worded.
A STRAY hash is worse: it would let an installer replace a user's file that happened to
match it. So the list is pinned by set EQUALITY with what the tags hold, not by coverage.

Scope (user decision H-Q3, 2026-10-02): release TAGS only, `v[0-9]*`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
GEN = ROOT / "tools" / "x4validate" / "scripts" / "gen-shipped-hashes.py"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_shipped_hashes", GEN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_canonical_form_one_twin_per_clause():
    c = _gen().canonical_sha256
    assert c(b"\xef\xbb\xbfa\r\nb\r\n\r\n") == hashlib.sha256(b"a\nb").hexdigest()
    assert c(b"a\r\nb") == c(b"a\nb")                 # clause: CR removed
    assert c(b"\xef\xbb\xbfa") == c(b"a")             # clause: leading BOM removed
    assert c(b"a\n\n\n") == c(b"a")                   # clause: trailing LF stripped
    assert c(b"a\nb") != c(b"a\nc")                   # content still counts
    assert c(b"\na") != c(b"a")                       # a LEADING newline is content
    assert c(b"x\xef\xbb\xbfa") != c(b"xa")           # a BOM is stripped only at the start


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t.invalid", "-c", "user.name=t",
                    "-c", "core.autocrlf=false", *a], check=True, capture_output=True)


def test_check_REPORTS_a_tag_missing_from_the_list(tmp_path):
    """The falsification twin for the coverage test: on a repo where it MUST go red, it does."""
    g = _gen()
    _git(tmp_path, "init", "-q")
    (tmp_path / "CLAUDE.md").write_bytes(b"one\n")
    _git(tmp_path, "add", "CLAUDE.md")
    _git(tmp_path, "commit", "-qm", "1")
    _git(tmp_path, "tag", "v1.0")
    (tmp_path / "CLAUDE.md").write_bytes(b"two\r\n")
    _git(tmp_path, "commit", "-qam", "2")
    _git(tmp_path, "tag", "v2.0")
    _git(tmp_path, "tag", "not-a-release")             # outside v[0-9]*: never counted
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "shipped-instruction-hashes.txt").write_text(
        "%s  CLAUDE.md  v1.0\n" % g.canonical_sha256(b"one\n"), encoding="utf-8")
    missing = g.check(tmp_path)
    assert len(missing) == 1 and "v2.0" in missing[0], missing
    g.write(tmp_path)
    assert g.check(tmp_path) == []
    rows = g.read_rows(tmp_path)
    assert {(n, t) for _, n, t in rows} == {("CLAUDE.md", "v1.0"), ("CLAUDE.md", "v2.0")}, rows


def test_check_REPORTS_a_STRAY_row(tmp_path):
    """Twin of the equality clause: a hash no tag shipped is reported, not tolerated."""
    g = _gen()
    _git(tmp_path, "init", "-q")
    (tmp_path / "CLAUDE.md").write_bytes(b"one\n")
    _git(tmp_path, "add", "CLAUDE.md")
    _git(tmp_path, "commit", "-qm", "1")
    _git(tmp_path, "tag", "v1.0")
    g.write(tmp_path)
    f = tmp_path / "scripts" / "shipped-instruction-hashes.txt"
    f.write_text(f.read_text(encoding="utf-8") + "%s  CLAUDE.md  vNEVER\n" % ("0" * 64),
                 encoding="utf-8")
    bad = g.check(tmp_path)
    assert len(bad) == 1 and "0" * 64 in bad[0], bad


def _tags():
    r = subprocess.run(["git", "-C", str(ROOT), "tag", "--list", "v[0-9]*"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("not a git checkout -- the shipped tags are unknown here")
    if not r.stdout.split():
        if os.environ.get("CI"):
            pytest.fail("CI checkout has NO tags: the tests job needs fetch-depth: 0 (ci.yml)")
        pytest.skip("no tags in this checkout (shallow clone)")
    return r.stdout.split()


def test_the_hash_list_covers_EVERY_tag_and_holds_nothing_else():
    _tags()
    assert _gen().check(ROOT) == []


def test_the_list_holds_NOTHING_that_was_never_shipped():
    """Set equality on (hash, name), not only coverage: a stray hash would let an installer
    overwrite a user's file that happened to match it."""
    _tags()
    g = _gen()
    have = g.read_rows(ROOT)
    assert have, "the shipped list is empty"
    assert {(h, n) for h, n, _ in have} == {(h, n) for h, n, _ in g.expected(ROOT)}


def test_FXB2_the_count_line_names_EVERY_name():
    """The summary counted CLAUDE.md and AGENTS.md only; .claude/x4-paths.env.example rows
    were checked and never counted. One twin per NAME: each count is its own rows'."""
    g = _gen()
    rows = [("a" * 64, n, "v1.0.0") for n in g.NAMES] + [("b" * 64, g.NAMES[2], "v1.1.0")]
    line = g.count_line(rows, 2)
    assert line.startswith("%d row(s) over 2 tags" % (len(g.NAMES) + 1)), line
    assert "1 CLAUDE.md" in line and "1 AGENTS.md" in line, line
    assert "2 .claude/x4-paths.env.example" in line, line
    assert "1 .claude/settings.json" in line, line
    assert "1 KNOWLEDGEBASE.md" in line and "1 agent/targets/codex/hooks.json.tmpl" in line, line
    assert all(n in line for n in g.NAMES), line
