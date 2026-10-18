"""gen-agent-trees.py, Codex target (lane B Task 7): .codex/hooks carries every guard byte for
byte plus the adapter, and hooks.json comes from ONE frozen template.

Kept apart from test_gen_agent_trees.py so lanes A, B and E can change their own tests freely.
"""
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
SRC = PKG / "scripts" / "gen-agent-trees.py"
TEMPLATE = REPO / "agent" / "targets" / "codex" / "hooks.json.tmpl"

#: Changing a Codex hook DEFINITION silently switches every user's guards off until they
#: re-review it (spike R5; Codex keys trust on the definition's hash). Changing this pin needs a
#: CHANGELOG entry headed "Codex users must re-review hooks" carrying the new value
#: (test_codex_disclosure.py checks that).
TEMPLATE_SHA256 = "9e50f5517b2e895cc412ca1727d00e85a8e54ba910286b1c662870f321f00212"


def load():
    spec = importlib.util.spec_from_file_location("gen_agent_trees_codex_under_test", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


#: Tests that need no agent/ source: they ask the SHIPPED git rules (FX-H2 / I-1).
_NO_AGENT_SOURCE = {"test_hooks_json_is_not_a_generated_file"}


@pytest.fixture(autouse=True)
def _source_checkout(request):
    if request.function.__name__ in _NO_AGENT_SOURCE:
        return
    if not (REPO / "agent").is_dir():
        pytest.skip("not a source checkout (no agent/) -- Codex generation NOT checked here")


def test_codex_hooks_template_is_frozen():
    b = TEMPLATE.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(b).hexdigest() == TEMPLATE_SHA256


def test_rendered_hooks_json_is_valid_and_uses_wrappers(tmp_path):
    root = tmp_path / "X4 Foundations"
    d = json.loads(load().render_codex_hooks_json(root))
    assert set(d["hooks"]) == {"SessionStart", "PreToolUse", "PostToolUse"}
    pre = d["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == ".*" and pre["hooks"][0]["timeout"] == 60
    matcher = d["hooks"]["PostToolUse"][0]["matcher"]
    assert all(re.fullmatch(matcher, name) for name in ("Bash", "apply_patch"))
    assert not any(re.fullmatch(matcher, name) for name in ("Read", "write_stdin", "mcp__other__Bash"))
    for groups in d["hooks"].values():
        for h in groups[0]["hooks"]:
            win, posix = h["commandWindows"], h["command"]
            assert str(root).replace("/", "\\") + "\\.codex\\hooks\\codex-entry.ps1" in win
            assert str(root).replace("\\", "/") + "/.codex/hooks/codex-entry.sh" in posix
            # MEASURED: a hook command that STARTS with a double quote never runs (0/3) -- fails open
            assert not win.startswith('"') and not posix.startswith('"')
            # MEASURED: pwsh first, Windows PowerShell as the fallback when pwsh is absent
            assert win.startswith("pwsh ") and " || powershell " in win
            assert "-File" in win and "ExecutionPolicy Bypass" in win


@pytest.mark.parametrize("bad", ['C:/a"b', "C:/a\nb", "C:/a%USERNAME%b", "C:/a\rb"])
def test_render_refuses_unsafe_root(bad):
    g = load()
    with pytest.raises(g.GenerationError):
        g.render_codex_hooks_json(Path(bad))


def test_TWIN_a_spaced_parenthesised_root_is_fine():
    d = json.loads(load().render_codex_hooks_json(Path("C:/Program Files (x86)/Steam/steamapps/common/X4 Foundations")))
    assert "Program Files (x86)" in d["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"]


def test_codex_hooks_carry_the_guards_byte_identical():
    exp = load().generate(REPO)
    claude = {rel[len(".claude/hooks/"):]: t for rel, t in exp.items() if rel.startswith(".claude/hooks/")}
    codex = {rel[len(".codex/hooks/"):]: t for rel, t in exp.items() if rel.startswith(".codex/hooks/")}
    assert claude and all(codex.get(n) == t for n, t in claude.items())
    adapters = set(codex) - set(claude)
    assert adapters == {"codex_adapter.py", "patch_paths.py", "codex_trust.py", "codex-entry.ps1", "codex-entry.sh"}


def test_codex_tree_is_fresh_in_the_checkout():
    g = load()
    stale = [p for p in g.problems(g.generate(REPO), REPO) if ".codex/" in p]
    assert stale == [], "run: uv run python scripts/gen-agent-trees.py"


def test_codex_hook_modes_match_the_claude_copy():
    """Byte identity includes the executable bit (git's index is the only record on Windows)."""
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-s", "--", ".claude/hooks", ".codex/hooks"],
                       capture_output=True, text=True)
    if r.returncode != 0 or ".codex/hooks/" not in r.stdout:
        pytest.skip("the Codex hooks are not tracked here -- modes NOT checked")
    modes = {}
    for line in r.stdout.splitlines():
        meta, path = line.split("\t", 1)
        modes[path] = meta.split()[0]
    pairs = {p[len(".codex/hooks/"):]: m for p, m in modes.items() if p.startswith(".codex/hooks/")}
    diff = {n: (modes.get(".claude/hooks/" + n), m) for n, m in pairs.items()
            if ".claude/hooks/" + n in modes and modes[".claude/hooks/" + n] != m}
    assert diff == {}
    assert pairs.get("codex-entry.sh") == "100755"


def test_TWIN_ghost_in_codex_hooks_is_reported(tmp_path):
    g = load()
    exp = g.generate(REPO)
    for rel, text in exp.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    (tmp_path / ".codex/hooks/stray.sh").write_bytes(b"x\n")
    assert g.problems(exp, tmp_path) == ["GHOST    .codex/hooks/stray.sh"]


def test_hooks_json_is_not_a_generated_file(tmp_path):
    """It holds an absolute path (per install): rendered by deploy/installers, never committed.

    FX-H2 / I-1: three halves, each where it can be answered. Generation needs agent/; the
    SHIPPED rule is asked git-free in a scratch repo, so a release zip (no .git) still checks
    it; this tree's own repository is asked only when it IS this toolkit's checkout."""
    from _layout import own_checkout, shipped_rules_repo
    if (REPO / "agent").is_dir():
        assert ".codex/hooks.json" not in load().generate(REPO)
    rules = shipped_rules_repo(tmp_path, REPO)
    r = subprocess.run(["git", "-C", str(rules), "check-ignore", "-q", "--no-index", ".codex/hooks.json"])
    assert r.returncode == 0, "the shipped .gitignore must ignore .codex/hooks.json (rc %d)" % r.returncode
    if own_checkout(REPO):
        r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", ".codex/hooks.json"])
        assert r.returncode == 0, ".codex/hooks.json must be git-ignored"


def test_M7_render_codex_hooks_json_takes_its_RENDERER_from_the_given_source(tmp_path):
    """The template came from `src`, the renderer from this repo's agent/ whatever `src` was:
    a different source tree was rendered by the wrong code. Planted: a renderer in `src`
    that returns a sentinel; the repo's real renderer cannot produce it."""
    src = tmp_path / "agent"
    (src / "targets" / "codex").mkdir(parents=True)
    (src / "targets" / "codex" / "hooks.json.tmpl").write_text('{"t": "{{ROOT}}"}', encoding="utf-8")
    hooks = src / "guards" / "claude-hooks"
    hooks.mkdir(parents=True)
    (hooks / "x4_upgrade_render.py").write_text(
        "def render_codex(tmpl, root):\n    return 'SENTINEL-FROM-SRC'\n", encoding="utf-8")
    assert load().render_codex_hooks_json(tmp_path / "X4", src) == "SENTINEL-FROM-SRC"


def test_M7_TWIN_without_src_the_repo_renderer_still_renders(tmp_path):
    out = load().render_codex_hooks_json(tmp_path / "X4")
    assert "SENTINEL" not in out and json.loads(out)["hooks"]


def test_M7_generate_reads_the_guard_tree_ONCE(monkeypatch):
    """generate() rendered the guards four times (directly twice, and once inside each of the
    Codex and OpenCode trees) -- four reads of one tree that could differ mid-run."""
    g = load()
    calls = []
    real = g.render_hooks

    def counting(src):
        calls.append(src)
        return real(src)
    monkeypatch.setattr(g, "render_hooks", counting)
    out = g.generate(REPO)
    assert len(calls) == 1, len(calls)
    assert any(k.startswith(".codex/hooks/") for k in out)
    assert any(k.startswith(".opencode/hooks/") for k in out)
