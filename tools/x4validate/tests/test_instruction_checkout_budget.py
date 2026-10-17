"""Codex consumes actual instruction bytes, including Windows checkout newlines."""
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[3]


def generator():
    script = REPO / "tools/x4validate/scripts/gen-agent-trees.py"
    spec = importlib.util.spec_from_file_location("checkout_budget_generator", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("text,stale", [
    ("x" * 32765 + "\n", False),  # 32767 actual CRLF bytes
    ("x" * 32766 + "\n", False),  # exactly32768 actual bytes
    ("x" * 32767 + "\n", True),   # rendered32768, actual32769
    ("é" * 16383 + "\n", False),  # UTF8, exactly32768 actual bytes
    ("é" * 16383 + "x\n", True),  # character count fits; UTF8 bytes do not
], ids=["below", "exact", "above", "utf8-exact", "utf8-above"])
def test_raw_agents_budget_boundary(tmp_path, text, stale):
    g = generator()
    assert g.AGENTS_MD_MAX_BYTES == 32768
    raw = text.encode("utf-8").replace(b"\n", b"\r\n")
    (tmp_path / "AGENTS.md").write_bytes(raw)
    assert g.problems({"AGENTS.md": text}, tmp_path) == (["STALE    AGENTS.md"] if stale else [])
    assert (tmp_path / "AGENTS.md").read_bytes() == raw


def test_check_refuses_an_oversized_equivalent_raw_file(tmp_path, monkeypatch):
    g = generator()
    text = "x" * 32767 + "\n"
    raw = text.encode().replace(b"\n", b"\r\n")
    (tmp_path / "AGENTS.md").write_bytes(raw)
    monkeypatch.setattr(g, "REPO", tmp_path)
    monkeypatch.setattr(g, "generate", lambda root: {"AGENTS.md": text})
    assert g.main(["--check"]) == 1
    assert (tmp_path / "AGENTS.md").read_bytes() == raw


def test_write_repairs_raw_budget_without_truncating_or_rewriting_other_crlf(tmp_path, monkeypatch, capsys):
    g = generator()
    text = "x" * 32767 + "\n"
    (tmp_path / "AGENTS.md").write_bytes(text.encode().replace(b"\n", b"\r\n"))
    other = b"# Keep this CRLF file\r\n"
    (tmp_path / "CLAUDE.md").write_bytes(other)
    monkeypatch.setattr(g, "REPO", tmp_path)
    monkeypatch.setattr(g, "generate", lambda root: {"AGENTS.md": text, "CLAUDE.md": other.decode().replace("\r\n", "\n")})
    assert g.main([]) == 0
    assert (tmp_path / "AGENTS.md").read_bytes() == text.encode()
    assert len((tmp_path / "AGENTS.md").read_bytes()) == 32768
    assert (tmp_path / "CLAUDE.md").read_bytes() == other
    assert "STALE    AGENTS.md" in capsys.readouterr().out
    assert g.main(["--check"]) == 0


def test_windows_style_git_clone_keeps_actual_agents_bytes_within_budget(tmp_path):
    g = generator()
    expected = g.render_agents_md(REPO / "agent").encode("utf-8")
    assert len(expected) <= 32768
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    hooks = tmp_path / "empty-hooks"
    source.mkdir()
    hooks.mkdir()
    (source / ".gitattributes").write_bytes((REPO / ".gitattributes").read_bytes())
    (source / "AGENTS.md").write_bytes(expected)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    base = ["git", "-c", "core.autocrlf=true", "-c", "core.hooksPath=" + str(hooks),
            "-c", "user.name=Owned checkout fixture", "-c", "user.email=owned@example.invalid"]
    for args in (["init", "-q", str(source)],
                 ["-C", str(source), "add", "--", ".gitattributes", "AGENTS.md"],
                 ["-C", str(source), "commit", "-q", "-m", "Owned instruction checkout"],
                 ["clone", "-q", "--no-hardlinks", str(source), str(checkout)]):
        result = subprocess.run([*base, *args], env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
    actual = (checkout / "AGENTS.md").read_bytes()
    assert len(actual) <= 32768, f"Windows-style checkout expands AGENTS.md to {len(actual)} bytes"
    assert actual == expected
