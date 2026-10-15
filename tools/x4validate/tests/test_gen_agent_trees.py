"""gen-agent-trees.py: every agent-facing file is generated from agent/ and must stay fresh."""
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
SRC = PKG / "scripts" / "gen-agent-trees.py"


def load():
    spec = importlib.util.spec_from_file_location("gen_agent_trees_under_test", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(autouse=True)
def _source_checkout(request):
    # An installed copy has no agent/ (installers do not ship it): the freshness tests have
    # nothing to compare there. The tmp-tree tests opt out here; their fixture skips on its own.
    if "fresh_copy" in request.fixturenames or request.node.name.startswith("test_missing_source"):
        return
    from _layout import require_repo
    require_repo("agent", why="generated-file freshness NOT checked here")


def test_generation_has_a_denominator():
    out = load().generate(REPO)
    assert "CLAUDE.md" in out
    assert sorted(p for p in out if p.startswith(".claude/agents/")) == [
        ".claude/agents/cross-file-impact.md", ".claude/agents/mod-research.md"]


def test_committed_trees_are_fresh():
    g = load()
    assert g.problems(g.generate(REPO), REPO) == [], \
        "stale generated files -- run: uv run python scripts/gen-agent-trees.py"


def test_generation_is_deterministic():
    g = load()
    assert g.generate(REPO) == g.generate(REPO)


def test_no_personal_path_reaches_generated_output():
    # A home directory in either slash dialect, or an 8-digit X4 profile id. A bare drive letter
    # is NOT personal (CLAUDE.md explains Wine's `Z:\`); scan-identifiers.py stays the
    # authoritative check on contributor identifiers.
    pat = re.compile(r"[\\/]Users[\\/]|/home/|\b\d{8}\b")
    # Rendered prose only. The guards under .claude/hooks/ are copied verbatim and legitimately
    # carry generic path fixtures (/Users/tester, /home/user) and Steam build ids; they are
    # covered by scan-identifiers.py, which knows placeholders from real identifiers.
    hits = [rel for rel, text in load().generate(REPO).items()
            if not rel.startswith((".claude/hooks/", ".codex/hooks/", ".opencode/hooks/", "tools/x4_harness/"))
                and pat.search(text)]
    assert hits == []


def test_agent_frontmatter_matches_the_claude_contract():
    out = load().generate(REPO)
    head = out[".claude/agents/cross-file-impact.md"].split("\n")
    assert head[:6] == ["---", "name: cross-file-impact", head[2], "tools: Glob, Grep, Read, Bash",
                        "model: sonnet", "---"]
    assert head[2].startswith("description: Use BEFORE implementing a multi-file X4 change.")


def test_every_skill_and_settings_is_generated():
    out = load().generate(REPO)
    skills = sorted(p for p in out if p.startswith(".claude/skills/") and p.endswith("/SKILL.md"))
    assert len(skills) == 12, skills
    assert ".claude/skills/x4-toolkit-dev/SKILL.md" in skills
    assert ".claude/skills/x4-mod-release-review/SKILL.md" in skills
    assert ".claude/settings.json" in out
    assert sum(p.startswith(".claude/skills/x4-cli-reference/reference/") for p in out) == 11


def test_tokens_are_rendered_everywhere():
    # Runtime carriers: `{{TOOLKIT}}` in `.agents/skills/` (and OpenCode's
    # `.opencode/skills/`, which render identically) is left for the
    # INSTALLER to render per OS. Anything else with `{{` -- any other token, or that token
    # anywhere else -- is a leak. The frozen runtime Codex template deliberately
    # retains exactly its two root slots; the next test binds it to the source.
    def residue(rel, text):
        if rel.startswith((".agents/skills/", ".opencode/skills/")):
            return text.replace("{{TOOLKIT}}", "")
        if rel == "tools/x4_harness/codex-hooks.json.tmpl":
            return text.replace("{{ROOT}}", "").replace("{{ROOT_WIN}}", "")
        return text
    leaks = [rel for rel, text in load().generate(REPO).items() if "{{" in residue(rel, text)]
    assert leaks == []


def test_runtime_codex_template_is_the_complete_frozen_source():
    template = (REPO / "agent/targets/codex/hooks.json.tmpl").read_text(encoding="utf-8")
    actual = load().generate(REPO)["tools/x4_harness/codex-hooks.json.tmpl"]
    assert actual == template and "{{ROOT}}" in actual and "{{ROOT_WIN}}" in actual


def test_no_double_banner_on_the_cli_reference():
    text = load().generate(REPO)[".claude/skills/x4-cli-reference/SKILL.md"]
    assert text.count("<!-- GENERATED") == 1


def test_hooks_are_generated_byte_identical():
    out = load().generate(REPO)
    src = REPO / "agent" / "guards" / "claude-hooks"
    srcs = sorted(f.relative_to(src).as_posix() for f in src.rglob("*")
                  if f.is_file() and "__pycache__" not in f.parts and ".pytest_cache" not in f.parts)
    gen = sorted(p[len(".claude/hooks/"):] for p in out if p.startswith(".claude/hooks/"))
    assert gen == srcs and len(gen) >= 13      # MEASURED: 13 tracked hook files at 2f8e913
    for name in srcs:
        assert out[".claude/hooks/" + name] == (src / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_hook_modes_match_their_source():
    """Byte identity includes the executable bit. On Windows (core.filemode=false) a regenerated
    file re-added to the index defaults to 100644 -- MEASURED when the hooks moved: 5 scripts
    lost 100755. Compared in git's index, the only place Windows records the bit."""
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-s", "--", ".claude/hooks", "agent/guards/claude-hooks"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip("not a git checkout with tracked hooks -- modes NOT checked here")
    modes = {}
    for line in r.stdout.splitlines():
        meta, path = line.split("\t", 1)
        modes[path] = meta.split()[0]
    gen = {p[len(".claude/hooks/"):]: m for p, m in modes.items() if p.startswith(".claude/hooks/")}
    src = {p[len("agent/guards/claude-hooks/"):]: m for p, m in modes.items() if p.startswith("agent/guards/")}
    assert gen and src
    assert {n: (src.get(n), m) for n, m in gen.items() if src.get(n) != m} == {}


def test_hooks_get_no_banner_and_no_token_rewrite():
    out = load().generate(REPO)
    assert not any("<!-- GENERATED" in t for p, t in out.items() if p.startswith(".claude/hooks/"))
    assert "CLAUDE_PROJECT_DIR" in out[".claude/hooks/_x4-env.sh"]   # its fallback root stays literal


def test_agents_md_is_core_plus_codex_addendum_within_the_byte_limit():
    """Codex silently drops AGENTS.md text past 32,768 BYTES (MEASURED 2026-09-30, and the
    exact cut re-measured 2026-10-02 on Codex 0.160.0: plan-2 measure-A M-A2)."""
    out = load().generate(REPO)
    a, c = out["AGENTS.md"], out["CLAUDE.md"]
    assert a.startswith("# AGENTS.md") and "<!-- GENERATED from agent/ -->" in a
    assert len(a.encode("utf-8")) <= 32768
    # the shared core reaches both: the routing table and the evidence rules are in each
    for must in ("Route BEFORE you search", "Label the evidence tier", "x4-toolkit-dev", "X4-NOTES.md"):
        assert must in a and must in c, must


def test_agents_md_carries_what_codex_cannot_get_from_hooks():
    text = load().generate(REPO)["AGENTS.md"]
    for must in ("fail OPEN", "x4guard.py check", "--shell powershell", "inert: true", "reference",
                 ".agents/skills", "git add -A", "$env:X4_TOOLKIT", "Codex only"):
        assert must in text, must
    for banned in ("NotebookEdit", "CLAUDE_PROJECT_DIR", "timed-out hook (30 s)"):
        assert banned not in text, banned            # Claude facts must not leak into AGENTS.md


def test_TWIN_an_oversized_agents_md_refuses(tmp_path):
    g = load()
    src = _agent_src_copy(tmp_path)
    over = 32768 - len(g.render_entry(src, "codex").encode("utf-8")) + 200   # relative to today
    p = src / "instructions/core.md"
    p.write_bytes(p.read_bytes() + b"filler line for the size limit\n" * (over // 31 + 1))
    with pytest.raises(g.GenerationError, match="silently drops"):
        g.generate(tmp_path)


def test_agents_md_limit_is_BYTES_not_chars(tmp_path):
    # twin: under 32,768 chars but over 32,768 bytes must refuse
    g = load()
    src = _agent_src_copy(tmp_path)
    room_chars = 32768 - len(g.render_entry(src, "codex")) - 10
    p = src / "instructions/codex.md"
    p.write_bytes(p.read_bytes() + ("★" * room_chars + "\n").encode())
    assert len(g.render_entry(src, "codex")) < 32768                 # chars: under
    with pytest.raises(g.GenerationError, match="silently drops"):
        g.generate(tmp_path)


def test_every_x4guard_line_in_agents_md_parses_and_answers():
    """The addendum quotes command lines; if the guard CLI changes its flags, this goes red."""
    import json
    import shlex
    import sys
    text = load().generate(REPO)["AGENTS.md"]
    lines = [l.strip() for l in text.splitlines() if "x4guard.py check" in l and l.strip().startswith("python ")]
    assert len(lines) >= 3, lines
    for l in lines:
        argv = shlex.split(l.replace('"<cmd>"', '"echo hi"').replace('"<file>"', '"dev/probe/x.xml"'))[1:]
        r = subprocess.run([sys.executable, str(REPO / argv[0]), *argv[1:]], capture_output=True,
                           text=True, timeout=120, cwd=str(REPO))
        assert json.loads(r.stdout)["decision"] in {"allow", "advise", "ask", "deny"}, (l, r.stdout, r.stderr)


def test_the_repo_ships_no_second_agents_md():
    """MEASURED 2026-10-02 (Codex 0.160.0, measure-A M-A2): a root AGENTS.md and a nested one
    SHARE one 32,768-byte budget. A second AGENTS.md anywhere in the tree would silently cut
    the generated one. Tracked files only (git ls-files), the population that ships."""
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True)
    if r.returncode != 0:
        pytest.skip("not a git checkout -- the shipped-file population is unknown here")
    files = [p for p in r.stdout.decode("utf-8").split("\0") if p]
    assert len(files) > 100, len(files)                 # a real population, not an empty listing
    agents = [p for p in files if p.rsplit("/", 1)[-1].lower() == "agents.md"]
    assert agents == ["AGENTS.md"], agents


def _generator_owned_paths() -> list[str]:
    """Every path a generator writes: gen-agent-trees' output, the CLI-reference SOURCE it
    renders from (gen-cli-reference writes agent/skills/x4-cli-reference/), and the
    shipped-hashes data file. Derived from the generators, never typed out per tree."""
    out = set(load().generate(REPO))
    cli = ".claude/skills/x4-cli-reference/"
    out |= {"agent/skills/x4-cli-reference/" + p[len(cli):] for p in out if p.startswith(cli)}
    out.add("scripts/shipped-instruction-hashes.txt")
    return sorted(out)


def test_every_generator_owned_path_is_TRACKED_and_SHIPS():
    """Present on disk is not committed. MEASURED 2026-10-04 (CI run 37172347642): the bare
    `reference/` rule in .gitignore swallowed all 11 `.opencode/skills/x4-cli-reference/
    reference/*.md`; locally they existed untracked, so every freshness test was green on the
    machine that generated them and red on every clone. The earlier per-tree check-ignore pin
    listed the trees by hand and so could not see a fourth one. This asks git: each generated
    path must be TRACKED (`ls-files`), and must not be `export-ignore`d out of the release
    archive that build-release.sh makes with `git archive`."""
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True)
    if r.returncode != 0:
        pytest.skip("not a git checkout -- tracked-ness NOT checked here")
    tracked = {p for p in r.stdout.decode("utf-8").split("\0") if p}
    assert len(tracked) > 100, len(tracked)              # a real population, not an empty listing
    owned = _generator_owned_paths()
    assert len(owned) > 100, len(owned)                  # the generators enumerated something
    untracked = [p for p in owned if p not in tracked]
    assert untracked == [], f"{len(untracked)} generated file(s) not tracked by git: {untracked}"
    a = subprocess.run(["git", "-C", str(REPO), "check-attr", "-z", "export-ignore", "--stdin"],
                       input="\0".join(owned).encode("utf-8") + b"\0", capture_output=True)
    assert a.returncode == 0, a.stderr
    f = a.stdout.decode("utf-8").split("\0")
    rows = list(zip(f[0::3], f[1::3], f[2::3]))
    assert len(rows) == len(owned), (len(rows), len(owned))   # one answer per path, or refuse
    dropped = [p for p, _, v in rows if v not in ("unspecified", "unset")]
    assert dropped == [], f"export-ignore drops generated file(s) from the release: {dropped}"


def test_missing_source_refuses_rather_than_skipping(tmp_path):
    g = load()
    with pytest.raises(g.GenerationError):
        g.generate(tmp_path)          # no agent/ at all: refuse, never a partial result


@pytest.fixture
def fresh_copy(tmp_path):
    # R2-B1: it GENERATES from agent/ (the comment above said these tests need no agent/; MEASURED
    # in an installed toolkit: 9 setup errors here).
    from _layout import require_repo
    require_repo("agent", why="the tmp tree is generated from the agent/ source")
    g = load()
    exp = g.generate(REPO)
    for rel, text in exp.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return g, exp, tmp_path


def test_TWIN_a_hand_edit_is_STALE(fresh_copy):
    g, exp, root = fresh_copy
    (root / "CLAUDE.md").write_bytes(b"hand edit\n")
    assert g.problems(exp, root) == ["STALE    CLAUDE.md"]


def test_TWIN_a_stray_agent_is_a_GHOST(fresh_copy):
    g, exp, root = fresh_copy
    (root / ".claude/agents/stray.md").write_bytes(b"x\n")
    assert g.problems(exp, root) == ["GHOST    .claude/agents/stray.md"]


def test_TWIN_a_deleted_file_is_MISSING(fresh_copy):
    g, exp, root = fresh_copy
    (root / ".claude/agents/mod-research.md").unlink()
    assert g.problems(exp, root) == ["MISSING  .claude/agents/mod-research.md"]


def test_TWIN_a_hand_edited_skill_is_STALE(fresh_copy):
    g, exp, root = fresh_copy
    p = root / ".claude/skills/x4-debug/SKILL.md"
    p.write_bytes(p.read_bytes() + b"\nextra\n")
    assert g.problems(exp, root) == ["STALE    .claude/skills/x4-debug/SKILL.md"]


def test_gitignored_files_are_never_ghosts(fresh_copy):
    g, exp, root = fresh_copy
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_bytes(b".pytest_cache/\n")
    cache = root / ".claude/agents/.pytest_cache/x"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"x\n")
    assert g.problems(exp, root) == []          # ignored by git -> not a ghost


def test_crlf_checkout_is_not_stale(fresh_copy):
    g, exp, root = fresh_copy
    p = root / "CLAUDE.md"
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert g.problems(exp, root) == []


def test_I6_write_mode_rewrites_only_stale_files_and_names_them(tmp_path, monkeypatch, capsys):
    """A hand edit to a generated file (e.g. a tool writing .claude/settings.json) must not be
    lost UNSEEN: write mode names each STALE file it overwrites and leaves fresh files untouched."""
    import os
    import shutil
    g = load()
    shutil.copytree(REPO / "agent", tmp_path / "agent")
    for rel, text in g.generate(REPO).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    fresh = tmp_path / ".claude/agents/mod-research.md"
    os.utime(fresh, (1_000_000_000, 1_000_000_000))
    edited = tmp_path / ".claude/settings.json"
    edited.write_bytes(b'{"hand": "edit"}\n')
    monkeypatch.setattr(g, "REPO", tmp_path)
    assert g.main([]) == 0
    out = capsys.readouterr()
    assert "STALE    .claude/settings.json" in out.out + out.err
    assert fresh.stat().st_mtime == 1_000_000_000          # untouched
    assert b"hand" not in edited.read_bytes()               # restored from agent/


# ---------------------------------------------------------------- lane E (Plan 2): E6 agent.yaml

CFI = "agent/agents/cross-file-impact/agent.yaml"
MR = "agent/agents/mod-research/agent.yaml"


def _agent_copy(tmp_path):
    shutil.copytree(REPO / "agent", tmp_path / "agent", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def _edit(root, rel, old, new):
    p = root / rel
    t = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert old in t, f"fixture drifted: {old!r} is not in {rel}"
    p.write_bytes(t.replace(old, new, 1).encode("utf-8"))


def _frontmatter(text):
    from ruamel.yaml import YAML
    assert text.startswith("---\n")
    return YAML(typ="safe").load(text[4:text.index("\n---\n", 4)])


@pytest.mark.parametrize("tools", ["tools: Glob, Grep, Read, Bash",      # MEASURED: became G, l, o, b, ...
                                   "tools: Read",            # a string with no ',' or ' ': only the list clause sees it
                                   'tools: [Glob, "Read, Grep"]',
                                   "tools: [Glob, 7]",
                                   'tools: [Glob, ""]'])
def test_E6_tools_that_are_not_a_list_of_names_refuse(tmp_path, tools):
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, "tools: [Glob, Grep, Read, Bash]", tools)
    with pytest.raises(g.GenerationError, match="tools"):
        g.generate(root)


@pytest.mark.parametrize("desc", ["Note: use BEFORE editing", "Line one\\nLine two", "a #hash", "[bracketed]"])
def test_E6_any_description_survives_into_parseable_frontmatter(tmp_path, desc):
    """MEASURED: ': ' and a newline produced frontmatter that does not parse."""
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, 'description: "Use BEFORE', f'description: "{desc} -- Use BEFORE')
    fm = _frontmatter(g.generate(root)[".claude/agents/cross-file-impact.md"])
    assert fm["description"].startswith(desc.replace("\\n", "\n") + " -- Use BEFORE"), fm
    assert fm["name"] == "cross-file-impact" and fm["model"] == "sonnet"


@pytest.mark.parametrize("desc", ["Note: x",      # the render does not parse at all
                                  "a #hash"])     # it parses, but reads back as 'a'
def test_E6_TWIN_the_readback_refuses_a_render_that_does_not_parse_back(tmp_path, monkeypatch, desc):
    """The belt-and-braces clause on its own: with the quoting step broken, the re-read of the
    rendered frontmatter must refuse rather than emit a frontmatter Claude Code would misread.
    One param per clause of the re-read (parse error / value mismatch)."""
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, 'description: "Use BEFORE', f'description: "{desc} -- Use BEFORE')
    monkeypatch.setattr(g, "_yaml_scalar", lambda s: s)
    with pytest.raises(g.GenerationError, match="rendered frontmatter"):
        g.generate(root)


def test_E6_generated_frontmatter_parses_back_to_its_source():
    """Regression pin over the real agents: name/description/tools/model round-trip."""
    from ruamel.yaml import YAML
    out = load().generate(REPO)
    for name in ("cross-file-impact", "mod-research"):
        src = YAML(typ="safe").load((REPO / "agent" / "agents" / name / "agent.yaml").read_bytes())
        fm = _frontmatter(out[f".claude/agents/{name}.md"])
        assert fm["name"] == src["name"] and fm["description"] == src["description"]
        assert fm["tools"] == ", ".join(src["claude"]["tools"])


def test_E6_duplicate_agent_names_refuse(tmp_path):
    """MEASURED: one of the two agents was silently dropped."""
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, MR, "name: mod-research", "name: cross-file-impact")
    with pytest.raises(g.GenerationError, match="cross-file-impact"):
        g.generate(root)


# --- v4.0.0 release review R3-5: no agent.yaml key is silently dropped; read_only is enforced.
@pytest.mark.parametrize("old,new,why", [
    # MEASURED before: a typo `tool:` was dropped, so the subagent was rendered with NO tools
    # line -- Claude Code then gives it EVERY tool.
    ("claude:\n  tools:", "claude:\n  tool:", "claude.tool"),
    ("tier: balanced", "tier: balanced\nmodle: haiku", "modle"),
])
def test_R3_5_an_unknown_agent_yaml_key_refuses(tmp_path, old, new, why):
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, old, new)
    with pytest.raises(g.GenerationError, match=why):
        g.generate(root)


@pytest.mark.parametrize("old,new,why", [
    ("tools: [Glob, Grep, Read, Bash]", "tools: [Glob, Grep, Read, Edit]", "Edit"),      # a write tool
    ("tools: [Glob, Grep, Read, Bash]", "tools: [Glob, Write]", "Write"),
    ("tools: [Glob, Grep, Read, Bash]", "tools: []", "every tool"),                     # empty = all
    ("read_only: true", "read_only: yes please", "read_only"),                         # not a bool
])
def test_R3_5_read_only_is_enforced_one_twin_per_clause(tmp_path, old, new, why):
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, old, new)
    with pytest.raises(g.GenerationError, match=why):
        g.generate(root)


def test_R3_5_TWIN_without_read_only_a_write_tool_is_accepted(tmp_path):
    """The control: the same Edit tool is fine on an agent that does not declare read_only, so
    the refusal above is the read_only clause and not the tool name."""
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, "read_only: true\n", "")
    _edit(root, CFI, "tools: [Glob, Grep, Read, Bash]", "tools: [Glob, Grep, Read, Edit]")
    assert "Edit" in _frontmatter(g.generate(root)[".claude/agents/cross-file-impact.md"])["tools"]


@pytest.mark.parametrize("raw", ['"../../escaped"', '"sub/dir"', r"'sub\dir'", '"Upper-Case"',
                                 '"has space"', "123", '"-leading"', '""'])
def test_E6_a_name_that_is_not_a_plain_agent_name_refuses(tmp_path, raw):
    """MEASURED: '../../escaped' became .claude/agents/../../escaped.md -- outside .claude/."""
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, "name: cross-file-impact", f"name: {raw}")
    with pytest.raises(g.GenerationError, match="name"):
        g.generate(root)


def test_E6_TWIN_a_plain_new_name_still_generates(tmp_path):
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, "name: cross-file-impact", "name: impact-2")
    assert ".claude/agents/impact-2.md" in g.generate(root)


def test_E6_a_claude_block_that_is_not_a_mapping_refuses(tmp_path):
    g = load()
    root = _agent_copy(tmp_path)
    _edit(root, CFI, "claude:\n  tools: [Glob, Grep, Read, Bash]", "claude: [Glob]")
    with pytest.raises(g.GenerationError, match="claude"):
        g.generate(root)


# ---------------------------------------------------------------- lane E (Plan 2): E7 refusals

SK = "agent/skills/x4-debug/SKILL.md"


@pytest.mark.parametrize("label", ["never-closed frontmatter", "non-UTF-8 skill", "non-UTF-8 agent.yaml",
                                   "YAML syntax error", "duplicate YAML key"])
def test_E7_a_malformed_source_refuses_with_rc2(tmp_path, monkeypatch, capsys, label):
    """MEASURED: each of these escaped as a traceback (rc 1), not a refusal (rc 2)."""
    g = load()
    root = _agent_copy(tmp_path)
    if label == "never-closed frontmatter":
        (root / SK).write_bytes(b"---\nname: x4-debug\ndescription: d\nno closing fence\n")
    elif label == "non-UTF-8 skill":
        (root / SK).write_bytes((root / SK).read_bytes() + b"\xff\n")
    elif label == "non-UTF-8 agent.yaml":
        (root / CFI).write_bytes((root / CFI).read_bytes() + b"# \xff\n")
    elif label == "YAML syntax error":
        _edit(root, CFI, "tier: balanced", "tier: [balanced")
    else:
        _edit(root, CFI, "tier: balanced", "tier: balanced\ntier: deep")
    monkeypatch.setattr(g, "REPO", root)
    assert g.main(["--check"]) == 2, label
    err = capsys.readouterr().err
    assert err.startswith("REFUSING:") and ("SKILL.md" in err or "agent.yaml" in err), err


def test_E7_TWIN_an_unmodified_copy_is_not_refused(tmp_path, monkeypatch, capsys):
    g = load()
    root = _agent_copy(tmp_path)
    monkeypatch.setattr(g, "REPO", root)
    assert g.main(["--check"]) == 1          # a bare copy has no generated files: MISSING, not REFUSING
    assert "REFUSING" not in capsys.readouterr().err


def test_E7_a_non_utf8_generated_file_is_STALE_not_a_crash(fresh_copy):
    g, exp, root = fresh_copy
    (root / "CLAUDE.md").write_bytes(b"\xff\xfe broken\n")
    assert g.problems(exp, root) == ["STALE    CLAUDE.md"]
# --- Plan 2 lane A, Task 3: entry files = addendum title + banner + core with the addendum body
# --- at ONE marker; CLAUDE.md refused above 40,000 CHARACTERS; leftover tokens refused.

def _agent_src_copy(tmp_path):
    import shutil
    shutil.copytree(REPO / "agent", tmp_path / "agent")
    return tmp_path / "agent"


def test_claude_md_is_title_banner_core_with_addendum_at_the_marker(tmp_path):
    g = load()
    src = _agent_src_copy(tmp_path)
    (src / "instructions/core.md").write_bytes(b"intro\n\n{{AGENT_ADDENDUM}}\n\noutro\n")
    (src / "instructions/claude.md").write_bytes(b"# T\n\nADD\n")
    out = g.render_entry(src, "claude")
    assert out == "# T\n\n<!-- GENERATED from agent/ -->\nintro\n\nADD\n\noutro\n"


@pytest.mark.parametrize("core", [b"no marker\n", b"{{AGENT_ADDENDUM}}\n{{AGENT_ADDENDUM}}\n"],
                         ids=["zero", "two"])
def test_TWIN_marker_count_other_than_one_refuses(tmp_path, core):
    g = load()
    src = _agent_src_copy(tmp_path)
    (src / "instructions/core.md").write_bytes(core)
    with pytest.raises(g.GenerationError, match="AGENT_ADDENDUM"):
        g.render_entry(src, "claude")


@pytest.mark.parametrize("addendum", [b"", b"no title\nbody\n"], ids=["empty", "no-h1"])
def test_TWIN_addendum_without_an_h1_title_refuses(tmp_path, addendum):
    g = load()
    src = _agent_src_copy(tmp_path)
    (src / "instructions/claude.md").write_bytes(addendum)
    with pytest.raises(g.GenerationError, match="title"):
        g.render_entry(src, "claude")


def test_TWIN_an_oversized_claude_md_refuses(tmp_path):
    g = load()
    src = _agent_src_copy(tmp_path)
    over = g.CLAUDE_MD_MAX_CHARS - len(g.render_entry(src, "claude")) + 100   # relative to today's size
    p = src / "instructions/core.md"
    p.write_bytes(p.read_bytes() + ("\u2605" * over + "\n").encode())
    with pytest.raises(g.GenerationError, match="40000 characters|40,000 characters"):
        g.generate(tmp_path)


def test_claude_md_limit_is_counted_in_CHARACTERS_not_bytes(tmp_path):
    # twin of the above: multi-byte text under 40,000 chars but over 40,000 BYTES must PASS
    g = load()
    src = _agent_src_copy(tmp_path)
    room = g.CLAUDE_MD_MAX_CHARS - len(g.render_entry(src, "claude")) - 10
    p = src / "instructions/claude.md"
    p.write_bytes(p.read_bytes() + ("\u2605" * room + "\n").encode())       # 3 bytes each
    out = g.generate(tmp_path)["CLAUDE.md"]                                # must not raise
    assert len(out) <= g.CLAUDE_MD_MAX_CHARS < len(out.encode("utf-8"))


def test_claude_md_ceiling_is_the_budget_gates_ceiling():
    from conftest import import_gate
    assert load().CLAUDE_MD_MAX_CHARS == import_gate("claude_md_budget", module_level=False).HARD_CEILING


def test_TWIN_an_unknown_token_left_after_rendering_refuses(tmp_path):
    g = load()
    src = _agent_src_copy(tmp_path)
    p = src / "instructions/core.md"
    p.write_bytes(p.read_bytes() + b"\n{{NOT_A_TOKEN}}\n")
    with pytest.raises(g.GenerationError, match="NOT_A_TOKEN"):
        g.generate(tmp_path)


def test_size_report_names_both_entry_files_with_their_units():
    g = load()
    lines = g.size_report(g.generate(REPO))
    assert any(l.startswith("CLAUDE.md ") and "/40,000 chars" in l for l in lines), lines
    assert any(l.startswith("AGENTS.md ") and "/32,768 bytes" in l for l in lines), lines


# --- Plan 2 lane A, Task 4: maintainer guidance lives in the x4-toolkit-dev skill, verbatim.

MOVED_HEADINGS = ("A Step That Narrows Data MUST Announce It", "A Derived Artifact Must Declare WHEN",
                  "Tools Must Be Trustworthy BEFORE the Modlist", "Bug Handling Is a FUNNEL",
                  "Concurrent Sessions: Isolate the TREE", "Memory and loaded context are LEADS")
#: Python-internal / maintainer-gate routing rows (option B, DECISIONS #1). The CLI row
#: `x4effective dump --chain` is NOT among them: it stays in the entry files.
MOVED_ROWS = ("`_scan.iter_mod_xml`", "`_registry.scan_installed()`", "**MANIFEST ID**",
              "`_scan.iter_corpus_xml(ext, report)`", "`_effective.base_vpaths`",
              "`gates/mutation_probe.py`")


@pytest.mark.parametrize("heading", MOVED_HEADINGS)
def test_maintainer_section_lives_in_exactly_one_place(heading):
    out = load().generate(REPO)
    skill = out[".claude/skills/x4-toolkit-dev/SKILL.md"]
    assert heading in skill, f"{heading!r} missing from the x4-toolkit-dev skill (relocated, never deleted)"
    assert heading not in out["CLAUDE.md"] and heading not in out["AGENTS.md"], f"{heading!r} still in an entry file"


@pytest.mark.parametrize("row", MOVED_ROWS)
def test_maintainer_routing_row_lives_in_exactly_one_place(row):
    out = load().generate(REPO)
    assert row in out[".claude/skills/x4-toolkit-dev/SKILL.md"], row
    assert row not in out["CLAUDE.md"] and row not in out["AGENTS.md"], row


def test_the_cli_chain_row_stays_in_the_entry_file():
    assert "`x4effective dump --chain <vpath>`" in load().generate(REPO)["CLAUDE.md"]


def test_entry_files_point_to_the_dev_skill_and_x4_notes():
    out = load().generate(REPO)
    for f in ("CLAUDE.md", "AGENTS.md"):
        assert "x4-toolkit-dev" in out[f] and "X4-NOTES.md" in out[f]


def test_the_generator_never_owns_x4_notes():
    g = load()
    assert not any("X4-NOTES" in p for p in g.OWNED) and not any("X4-NOTES" in p for p in g.generate(REPO))


# --- Plan 2 lane A, Task 5: the core is agent-neutral; Claude facts live in the claude addendum.
#: One sample per NEUTRALITY_BANNED clause (#26: a falsification twin per clause).
BANNED_SAMPLES = ["$CLAUDE_PROJECT_DIR", "Claude Code", "see CLAUDE.md", "MEMORY.md", "NotebookEdit",
                  "settings.json", ".claude/hooks/x", ".claude\\settings", "use **Glob**", "the **Grep** tool",
                  # release review R3-2: each of these passed the list before
                  "use the Grep/Glob tool", "the Edit tool", "run it in claude code", "the .claude folder",
                  "$CLAUDE_PLUGIN_ROOT/hooks"]


@pytest.mark.parametrize("sample", BANNED_SAMPLES)
def test_TWIN_core_naming_a_claude_only_mechanism_refuses(tmp_path, sample):
    g = load()
    src = _agent_src_copy(tmp_path)
    p = src / "instructions/core.md"
    p.write_bytes(p.read_bytes() + f"\nleak: {sample}\n".encode())
    with pytest.raises(g.GenerationError, match="neutral"):
        g.generate(tmp_path)


@pytest.mark.parametrize("allowed", [".claude\\backups\\known-good-x\\", ".claude/backups/x"])
def test_allowlisted_toolkit_paths_do_not_refuse(tmp_path, allowed):
    g = load()
    src = _agent_src_copy(tmp_path)
    p = src / "instructions/core.md"
    p.write_bytes(p.read_bytes() + f"\nok: {allowed}\n".encode())
    g.generate(tmp_path)              # must not raise


def test_TWIN_an_allowlisted_path_does_not_hide_a_banned_one_on_the_same_line(tmp_path):
    g = load()
    src = _agent_src_copy(tmp_path)
    p = src / "instructions/core.md"
    p.write_bytes(p.read_bytes() + b"\nok .claude/backups/x but .claude/settings.json\n")
    with pytest.raises(g.GenerationError, match="neutral"):
        g.generate(tmp_path)


def test_core_naming_the_3x_config_location_is_now_REFUSED(tmp_path):
    """Plan 3 lane I moved the path config to the toolkit root, so the allowance that let the
    core name `.claude/x4-paths.env` is gone: naming it again is a Claude-tree path."""
    g = load()
    src = _agent_src_copy(tmp_path)
    p = src / "instructions/core.md"
    p.write_bytes(p.read_bytes() + b"\nconfig: .claude/x4-paths.env\n")
    with pytest.raises(g.GenerationError, match="neutral"):
        g.generate(tmp_path)


def test_neutrality_refusal_names_the_line_number(tmp_path):
    g = load()
    with pytest.raises(g.GenerationError, match=r"line 3\b"):
        g.check_neutral("one\ntwo\nthree NotebookEdit\n")


def test_the_committed_core_is_neutral():
    load().check_neutral((REPO / "agent/instructions/core.md").read_bytes().decode("utf-8"))


# --- release review R3-2: skills reach Codex and OpenCode verbatim, so they are checked too.
def test_R3_2_the_committed_skills_are_neutral():
    load().check_skills_neutral(REPO / "agent")


def test_R3_2_a_skill_naming_a_claude_only_mechanism_refuses(tmp_path):
    g = load()
    src = _agent_src_copy(tmp_path)
    p = src / "skills/x4-balance/SKILL.md"
    p.write_bytes(p.read_bytes() + b"\nRead CLAUDE.md first.\n")
    with pytest.raises(g.GenerationError, match=r"x4-balance/SKILL\.md is not agent-neutral"):
        g.generate(tmp_path)


def test_R3_2_TWIN_a_skill_allowance_is_scoped_to_its_own_file(tmp_path):
    """The allowance for one file's exact text must not excuse the same text in another skill."""
    g = load()
    (rel, texts), = list(g.SKILL_NEUTRALITY_ALLOWED.items())[:1]
    src = _agent_src_copy(tmp_path)
    p = src / "skills/x4-balance/SKILL.md"
    p.write_bytes(p.read_bytes() + ("\n" + texts[0] + "\n").encode("utf-8"))
    with pytest.raises(g.GenerationError, match="x4-balance"):
        g.generate(tmp_path)


def test_R3_2_TWIN_a_stale_skill_allowance_refuses(tmp_path, monkeypatch):
    g = load()
    monkeypatch.setattr(g, "SKILL_NEUTRALITY_ALLOWED",
                        {**g.SKILL_NEUTRALITY_ALLOWED, "x4-balance/SKILL.md": ("text that is not there",)})
    with pytest.raises(g.GenerationError, match="stale"):
        g.check_skills_neutral(REPO / "agent")


def test_R3_2_skills_render_the_generated_file_list_from_OWNED():
    """x4-toolkit-dev lists the generated paths through the token, never by hand (R3-6)."""
    g = load()
    out = g.generate(REPO)
    phrase = g._generated_files_phrase()
    for prefix in (".claude/skills/", ".agents/skills/", ".opencode/skills/"):
        text = out[prefix + "x4-toolkit-dev/SKILL.md"]
        assert phrase in text and "{{GENERATED_FILES}}" not in text, prefix


def test_TWIN_an_empty_addendum_body_refuses(tmp_path):
    g = load()
    src = _agent_src_copy(tmp_path)
    (src / "instructions/claude.md").write_bytes(b"# Title only\n")
    with pytest.raises(g.GenerationError, match="addendum"):
        g.render_entry(src, "claude")


def test_claude_md_still_carries_its_hook_facts():
    text = load().generate(REPO)["CLAUDE.md"]
    for must in ("NotebookEdit", "timed-out hook", "CLAUDE_PROJECT_DIR", "env var > `x4-paths.env` > default",
                 "**Glob**", "**Grep**"):
        assert must in text, must


# --- Plan 2 lane A, Task 7: the same skill sources reach Codex and generic agents (.agents/skills/).

def _token_skills():
    """Skills whose SOURCE carries the toolkit token: derived, never retyped (MEASURED 7 on
    2026-10-02), so the per-target counts below cannot pass on an empty population."""
    n = sum("{{TOOLKIT}}" in p.read_bytes().decode("utf-8")
            for p in (REPO / "agent" / "skills").glob("*/SKILL.md"))
    assert n >= 7, n
    return n


def test_codex_skills_mirror_the_claude_skills_one_to_one():
    out = load().generate(REPO)
    cl = sorted(p[len(".claude/skills/"):] for p in out if p.startswith(".claude/skills/"))
    cx = sorted(p[len(".agents/skills/"):] for p in out if p.startswith(".agents/skills/"))
    assert cl == cx and len(cx) >= 23          # 12 SKILL.md + 11 cli reference files


def test_codex_skills_KEEP_the_toolkit_token_for_the_installer():
    # Plan 2 user decision #2: the generated Codex/generic skills keep `{{TOOLKIT}}` and the
    # INSTALLER renders it per OS (`$env:X4_TOOLKIT` on Windows, `$X4_TOOLKIT` elsewhere). The
    # in-repo copy cannot know the OS. Lane J (Plan 3): the generator used to render it to
    # `$X4_TOOLKIT` itself, so both installers' rewrite found no token and never fired.
    out = load().generate(REPO)
    cx = {p: t for p, t in out.items() if p.startswith(".agents/skills/")}
    assert not any("CLAUDE_PROJECT_DIR" in t for t in cx.values())
    assert not any("$X4_TOOLKIT" in t for t in cx.values()), \
        [p for p, t in cx.items() if "$X4_TOOLKIT" in t]
    # Any toolkit path, not only tools/x4validate: x4-mod-release-review cites {{TOOLKIT}}/docs/.
    assert sum("{{TOOLKIT}}/" in t for t in cx.values()) == _token_skills()
    # the only placeholder a Codex skill may carry is the one the installers render
    assert {m for t in cx.values() for m in re.findall(r"\{\{[A-Z_]+\}\}", t)} == {"{{TOOLKIT}}"}


def test_claude_skills_are_unchanged_by_the_codex_target():
    # the claude rendering keeps $CLAUDE_PROJECT_DIR until phase 7 (spec section 4)
    out = load().generate(REPO)
    assert sum("$CLAUDE_PROJECT_DIR/" in t for p, t in out.items()
               if p.startswith(".claude/skills/")) == _token_skills()
    assert not any("$X4_TOOLKIT/tools" in t for p, t in out.items() if p.startswith(".claude/skills/"))


def test_TWIN_a_stray_codex_skill_is_a_GHOST(fresh_copy):
    g, exp, root = fresh_copy
    (root / ".agents/skills/stray").mkdir(parents=True)
    (root / ".agents/skills/stray/SKILL.md").write_bytes(b"x\n")
    assert g.problems(exp, root) == ["GHOST    .agents/skills/stray/SKILL.md"]


def test_TWIN_a_hand_edited_codex_skill_is_STALE(fresh_copy):
    g, exp, root = fresh_copy
    p = root / ".agents/skills/x4-debug/SKILL.md"
    p.write_bytes(p.read_bytes() + b"\nextra\n")
    assert g.problems(exp, root) == ["STALE    .agents/skills/x4-debug/SKILL.md"]


def test_J4_every_x4guard_path_agents_md_names_ships_with_the_codex_target():
    """--agent codex installs AGENTS.md .codex .agents -- not .claude (test_F8 in
    test_install_over_existing.py pins `.claude/hooks` ABSENT for --agent codex). A guard path
    AGENTS.md tells Codex to run must be in that set."""
    text = load().generate(REPO)["AGENTS.md"]
    cmds = [l.strip() for l in text.splitlines() if l.strip().startswith("python ") and "x4guard.py" in l]
    assert len(cmds) >= 3, cmds
    for l in cmds:
        assert l.split()[1].startswith(".codex/hooks/"), l
