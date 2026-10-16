"""ADAPTING.md is executable documentation: every x4guard command it shows parses, every repo path
it cites exists, no number it states can drift from the code, and installers ship it."""
from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "ADAPTING.md"
TEXT = DOC.read_text(encoding="utf-8") if DOC.is_file() else ""
spec = importlib.util.spec_from_file_location("x4guard_src", REPO / "agent/guards/claude-hooks/x4guard.py")


@pytest.fixture
def _guard_src():
    from _layout import require_repo
    require_repo("agent/guards/claude-hooks/x4guard.py", why="the guard SOURCE")


def test_it_exists_and_names_the_product():
    assert "X4 AI Assistant Toolkit" in TEXT


def test_action_v1_examples_parse_as_complete_batched_requests(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    from modding_harness.generic import parse
    blocks = re.findall(r"```json\n(.*?)\n```", TEXT, re.S)
    actions = [json.loads(block) for block in blocks if '"api_version":1' in block]
    assert len(actions) == 2, "the new-host file and shell examples must both be checked"
    for example in actions:
        example["cwd"] = str(tmp_path)
        action = parse(json.dumps(example))
        assert action.host == "future-coder" and action.session_id and action.tool_call_id
        if action.surface == "files":
            assert action.operations == ("write",) and len(action.paths) == 1
            assert Path(action.paths[0]).is_relative_to(tmp_path)
        else:
            assert action.surface == "shell" and action.shell == "powershell" and action.command


@pytest.mark.parametrize("heading", [
    "Self-assessment", "fail open or closed", "The guard contract", "Worked example: Claude Code",
    "Worked example: Codex", "Where your adapter goes", "Required proof", "Rules", "Upstream submission"])
def test_every_spec_11_section_is_present(heading):
    assert heading.lower() in TEXT.lower()


def _x4guard_lines(text=TEXT):
    blocks = re.findall(r"```(?:[A-Za-z]+)?\n(.*?)\n```", text, re.S)
    return [ln.strip().split("x4guard.py", 1)[1] for block in blocks for ln in block.splitlines()
            if "x4guard.py check" in ln and re.match(r"\s*python(?:3)?\s", ln)]


def test_command_extraction_keeps_fenced_commands_without_parsing_prose():
    text = "Legacy `x4guard.py check` is a preview.\n```\npython .claude/hooks/x4guard.py check --wrong x\n```"
    assert _x4guard_lines(text) == [" check --wrong x"]


def test_every_check_command_it_shows_parses_with_the_real_cli(monkeypatch, _guard_src):
    lines = _x4guard_lines()
    assert len(lines) >= 3                                   # shell, write, delete at least
    x4g = importlib.util.module_from_spec(spec); spec.loader.exec_module(x4g)
    monkeypatch.setattr(x4g, "verdict_for", lambda *a, **k: {"v": 1})
    for ln in lines:
        argv = [t for t in shlex.split(ln.replace("\\", "/")) if t]
        argv = [("x" if "<" in t else t) for t in argv]      # placeholders like <cmd>
        assert x4g.main(argv) == 0, ln


def test_TWIN_a_wrong_flag_would_have_failed(monkeypatch, _guard_src):
    x4g = importlib.util.module_from_spec(spec); spec.loader.exec_module(x4g)
    with pytest.raises(SystemExit) as e:
        x4g.main(["check", "--agent", "toy", "--kind", "write", "--path", "x"])   # the spec's --agent: not real
    assert e.value.code == 2


def test_every_repo_path_it_cites_exists():
    from _layout import installed_layout, require_repo
    cited = set(re.findall(r"`((?:agent|scripts|tools|docs)/[^`\s*]+)`", TEXT))
    assert cited
    # R2-B1: an INSTALLED toolkit carries no agent/ or root docs/. Those citations are checked
    # in the repository; here the rest are checked, and the skip below NAMES what was not.
    repo_only = {p for p in cited if p.startswith(("agent/", "docs/"))} if installed_layout() else set()
    missing = [p for p in sorted(cited - repo_only)
               if not (REPO / p.rstrip("/")).exists() and "<" not in p]
    assert not missing, missing
    if repo_only:
        require_repo(*sorted({p.split("/", 1)[0] for p in repo_only}),
                     why="%d citation(s) under agent/ or docs/ NOT checked here" % len(repo_only))


def test_the_timeout_default_is_never_restated_wrongly(monkeypatch, _guard_src):
    monkeypatch.delenv("X4_GUARD_TIMEOUT_S", raising=False)
    x4g = importlib.util.module_from_spec(spec); spec.loader.exec_module(x4g)
    default = x4g._timeout_setting()[0]
    for m in re.finditer(r"X4_GUARD_TIMEOUT_S[^.\n]{0,60}?default\D{0,10}(\d+(?:\.\d+)?)", TEXT):
        assert float(m.group(1)) == default, m.group(0)


# --- FX-AD (2026-10-05): the time-budget guidance states numbers, so each is pinned ---------- #
# A cold adapter took a 10 s budget with no guidance and lost 1 of 151 conformance cases to a
# timeout. The doc now states x4guard's defaults, the worst-case sum and the budget formula;
# every one of those numbers is derived here from the module, never retyped.

def _budget_problems(text: str, timeout_s: float, kill_wait_s: float, drain_s: float,
                     toy: dict | None = None) -> list[str]:
    """Every place the 'Choosing the time budget' section disagrees with the given constants."""
    probs = []
    table = {}
    for name in ("TIMEOUT_S", "KILL_WAIT_S", "DRAIN_GRACE_S"):
        m = re.search(r"^\| `" + name + r"` \| (\d+(?:\.\d+)?) s \|", text, re.M)
        if not m:
            probs.append(f"no table row for {name}")
        else:
            table[name] = float(m.group(1))
    want = {"TIMEOUT_S": timeout_s, "KILL_WAIT_S": kill_wait_s, "DRAIN_GRACE_S": drain_s}
    for k, v in table.items():
        if v != want[k]:
            probs.append(f"table says {k} = {v:g}, x4guard says {want[k]:g}")
    worst = timeout_s + kill_wait_s + drain_s
    m = re.search(r"(\d+) \+ (\d+) \+ (\d+)\s*\n?\s*= (\d+) s with the defaults", text)
    if not m:
        probs.append("no 'A + B + C = W s with the defaults' sentence")
    elif [float(x) for x in m.groups()] != [timeout_s, kill_wait_s, drain_s, worst]:
        probs.append(f"worst-case sum {m.group(0)!r} != {timeout_s:g} + {kill_wait_s:g} + {drain_s:g} = {worst:g}")
    m = re.search(r"`D >= (\d+) \+ 3`", text)
    if not m or float(m.group(1)) != worst:
        probs.append(f"the 'leave it unset' threshold is not D >= {worst:g} + 3")
    m = re.search(r"`B = D - (\d+)`", text)
    if not m or float(m.group(1)) != kill_wait_s + drain_s + 3:
        probs.append(f"the budget formula is not B = D - {kill_wait_s + drain_s + 3:g}")
    m = re.search(r"D = (\d+), B = (\d+), x4guard's worst case (\d+) s", text)
    if not m:
        probs.append("no worked example 'D = .., B = .., x4guard's worst case .. s'")
    else:
        d, b, w = (float(x) for x in m.groups())
        if b != d - (kill_wait_s + drain_s + 3) or w != b + kill_wait_s + drain_s:
            probs.append(f"worked example {m.group(0)!r} does not follow the formula")
        if toy is not None and (toy.get("GUARD_BUDGET_S"), toy.get("DEADLINE_S")) != (b, d):
            probs.append(f"worked example says B={b:g}, D={d:g}; the committed toy adapter has {toy}")
    return probs


def _toy_adapter_numbers() -> dict | None:
    f = REPO / "tools/x4validate/tests/fixtures/toy_agent/toy_adapter.py"
    if not f.is_file():
        return None
    src = f.read_text(encoding="utf-8")
    return {k: float(re.search(r"^" + k + r"\s*=\s*(\d+)", src, re.M).group(1))
            for k in ("GUARD_BUDGET_S", "DEADLINE_S")}


def _guard_constants(monkeypatch):
    monkeypatch.delenv("X4_GUARD_TIMEOUT_S", raising=False)
    x4g = importlib.util.module_from_spec(spec); spec.loader.exec_module(x4g)
    return x4g.TIMEOUT_S, x4g.KILL_WAIT_S, x4g.DRAIN_GRACE_S


TOY_ADAPTER = "tools/x4validate/tests/fixtures/toy_agent/toy_adapter.py"


def _toy_numbers_or_refuse() -> dict:
    """The committed toy adapter's numbers. v4.0.0 delta review: a None here used to drop the
    worked-example comparison SILENTLY (`toy=None` skips that clause). Now: an installed layout
    without the fixture SKIPS, counted and named; anywhere else the missing fixture FAILS."""
    toy = _toy_adapter_numbers()
    if toy is None:
        from _layout import require_repo
        require_repo(TOY_ADAPTER, why="the worked example's B/D are compared against it")
        pytest.fail(f"{TOY_ADAPTER} is missing, so the worked example cannot be compared "
                    "against the committed toy adapter")
    return toy


def test_the_time_budget_section_states_x4guards_real_defaults(monkeypatch, _guard_src):
    consts = _guard_constants(monkeypatch)
    assert _budget_problems(TEXT, *consts, toy=_toy_numbers_or_refuse()) == []


def test_TWIN_a_missing_toy_adapter_FAILS_in_a_checkout(monkeypatch, tmp_path):
    import _layout
    monkeypatch.setattr(sys.modules[__name__], "_toy_adapter_numbers", lambda: None)
    real = _layout.require_repo
    monkeypatch.setattr(_layout, "require_repo",
                        lambda *rel, **k: real(*rel, **{**k, "root": tmp_path}))
    # A REAL checkout: since FX-H2 an `agent/` directory alone is an in-place install, and
    # this twin then skipped instead of testing anything (CI run 38287706366, both legs).
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "README.md").write_text("x", encoding="utf-8")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
           "-c", f"safe.directory={tmp_path.as_posix()}"]
    for argv in (["init", "-q"], ["add", "agent"], ["commit", "-q", "-m", "agent"]):
        subprocess.run(git + argv, check=True, capture_output=True, env=_layout._TL.clean_git_env())
    assert not _layout.installed_layout(tmp_path), "control: the fixture must read as a checkout"
    with pytest.raises(pytest.fail.Exception, match="is missing"):
        _toy_numbers_or_refuse()


def test_TWIN_a_missing_toy_adapter_is_a_COUNTED_skip_in_an_install(monkeypatch, tmp_path):
    import _layout
    monkeypatch.setattr(sys.modules[__name__], "_toy_adapter_numbers", lambda: None)
    real = _layout.require_repo
    monkeypatch.setattr(_layout, "require_repo",
                        lambda *rel, **k: real(*rel, **{**k, "root": tmp_path}))
    (tmp_path / "tools").mkdir()                                   # an install: no agent/, no .git
    with pytest.raises(pytest.skip.Exception) as e:
        _toy_numbers_or_refuse()
    assert _layout.REASON in str(e.value) and "toy_adapter.py" in str(e.value)


@pytest.mark.parametrize("mutate", [
    lambda t, c: t.replace("| `KILL_WAIT_S` | 5 s |", "| `KILL_WAIT_S` | 6 s |"),     # a table row
    lambda t, c: t.replace("= 33 s with the defaults", "= 34 s with the defaults"),   # the sum
    lambda t, c: t.replace("`B = D - 11`", "`B = D - 10`"),                           # the formula
    lambda t, c: t.replace("D = 26, B = 15", "D = 26, B = 16"),                       # the example
])
def test_TWIN_each_stated_budget_number_can_go_red(monkeypatch, _guard_src, mutate):
    consts = _guard_constants(monkeypatch)
    mutated = mutate(TEXT, consts)
    assert mutated != TEXT, "the mutation did not apply: the doc no longer says what this twin edits"
    assert _budget_problems(mutated, *consts) != []


def test_TWIN_a_moved_x4guard_default_is_caught(monkeypatch, _guard_src):
    """The other direction: x4guard's constant changes and the doc does not follow."""
    t, k, d = _guard_constants(monkeypatch)
    assert _budget_problems(TEXT, t, k + 1, d) != []
    assert _budget_problems(TEXT, t + 5, k, d) != []


def test_capability_report_fields_match_the_upstream_template():
    def fields(section):
        body = TEXT.split(section, 1)[1].split("\n## ", 1)[0]
        return re.findall(r"^\s*([A-Z][A-Z /-]{2,}):", body, re.M)
    assert fields("## 1.") and fields("## 1.") == fields("## 7.")


@pytest.mark.parametrize("installer,pattern", [("install.sh", r"X4_COPY_ITEMS=\"[^\"]*\bADAPTING\.md\b"),
                                               ("install.ps1", r"\$X4CopyItems\s*=\s*@\([^)]*'ADAPTING\.md'")])
def test_installers_ship_it(installer, pattern):
    assert re.search(pattern, (REPO / installer).read_text(encoding="utf-8"), re.S)


def test_the_instruction_core_points_an_unsupported_agent_at_it():
    from _layout import require_repo
    require_repo("AGENTS.md", why="AGENTS.md is installed only with the codex/generic targets")
    assert "ADAPTING.md" in (REPO / "AGENTS.md").read_text(encoding="utf-8")


# --- T7: the once-per-release cold exercise ---------------------------------------------- #
COLD = REPO / "docs" / "ADAPTING-COLD-TEST.md"


@pytest.fixture
def _docs_tree():
    from _layout import require_repo
    require_repo("docs/ADAPTING-COLD-TEST.md", "docs/REVIEW-SCOPE.md",
                 why="the maintainers' docs/ is not installed")


def test_the_cold_exercise_gives_the_subagent_only_adapting_and_the_toy_docs(_docs_tree):
    t = COLD.read_text(encoding="utf-8")
    prompt = t.split("<!-- PROMPT -->", 2)[1]
    assert "ADAPTING.md" in prompt and "TOY-AGENT.md" in prompt
    for leak in ("codex.py", "x4conformance.py", "toy_adapter.py", "profile.json\"", "MUTANTS"):
        assert leak not in prompt, leak


def test_the_cold_exercise_has_a_pass_criterion_and_a_record_location(_docs_tree):
    t = COLD.read_text(encoding="utf-8")
    assert "exits 0" in t and "docs/superpowers/measurements/" in t


def test_review_scope_lists_the_cold_exercise(_docs_tree):
    assert "ADAPTING-COLD-TEST.md" in (REPO / "docs" / "REVIEW-SCOPE.md").read_text(encoding="utf-8")


# --- T8: the universal setup prompt ---------------------------------------------------------- #
PROMPT = (REPO / "SETUP_PROMPT.txt").read_text(encoding="utf-8")


def test_setup_prompt_is_agent_neutral_and_routes_unknown_agents():
    assert "X4 AI Assistant Toolkit" in PROMPT and "ADAPTING.md" in PROMPT
    assert "x4doctor" in PROMPT and "--help" in PROMPT          # supported agents come from the installer, not a list here
    assert "Claude runs" not in PROMPT and "Claude Code Modding Toolkit" not in PROMPT


def test_setup_prompt_never_tells_the_agent_to_trust_or_approve_hooks_itself():
    low = PROMPT.lower()
    assert "never approve" in low or "do not approve" in low


# --- release review v4.0.0: R6-01 and R6-04 ------------------------------------------------- #
_USER_DOCS = ("SETUP_PROMPT.txt", "ADAPTING.md", "README.md")


def _bare_ps1_invocations(text: str) -> list[str]:
    r"""Every `install.ps1` the doc tells someone to RUN without the `-ExecutionPolicy Bypass
    -File` prefix a stock Windows needs (README: a bare `install.ps1` run directly is refused
    by the default execution policy before it runs anything). Three invocation shapes:
      * `install.ps1 -<Switch>` anywhere;
      * a line that STARTS with the script (`.\install.ps1`, `& ./install.ps1`), switch or not
        -- v4.0.0 delta review: a bare run with no switch slipped past the first shape;
      * `powershell`/`pwsh ... install.ps1` on one line.
    A prose MENTION ("install.ps1 already ran", a backticked name mid-sentence) is none of these."""
    ok_prefix = r"-ExecutionPolicy\s+Bypass\s+-File\s+$"
    bad = []
    for m in re.finditer(r"(\S*install\.ps1) -[A-Z]\w*", text):
        before = text[max(0, m.start() - 60):m.start()]
        if not re.search(ok_prefix, before):
            bad.append(m.group(0))
    for m in re.finditer(r"^[ \t]*(?:&[ \t]*)?[\"']?(?:\.[\\/])?install\.ps1\b[^\n]*", text, re.M):
        if m.group(0) not in bad and not any(m.group(0).strip().startswith(b) for b in bad):
            bad.append(m.group(0).strip())
    for m in re.finditer(r"\b(?:powershell|pwsh)(?:\.exe)?[ \t]+[^\n]*?(\S*install\.ps1)", text):
        before = text[m.start():m.start(1)]
        if not re.search(ok_prefix, before) and m.group(0) not in bad:
            bad.append(m.group(0))
    return bad


@pytest.mark.parametrize("snippet,flagged", [
    ("```\n.\\install.ps1\n```", True),                                  # bare run, no switch
    ("```\n& ./install.ps1\n```", True),
    ("run `powershell .\\install.ps1` now", True),
    ("run `powershell -File install.ps1` now", True),                     # -File alone: refused
    ("```\n.\\install.ps1 -Method global\n```", True),                    # the original shape
    ("powershell -ExecutionPolicy Bypass -File install.ps1    # Windows", False),
    ("powershell -ExecutionPolicy Bypass -File .\\install.ps1 -Help", False),
    ("if I installed with install.ps1 from PowerShell, install.ps1 already ran", False),
    ("a bare `.\\install.ps1` would be refused before it ran anything", False),
])
def test_TWIN_bare_ps1_detector_per_shape(snippet, flagged):
    assert bool(_bare_ps1_invocations(snippet)) is flagged, (snippet, _bare_ps1_invocations(snippet))


@pytest.mark.parametrize("name", _USER_DOCS)
def test_every_powershell_invocation_runs_on_a_stock_windows(name):
    assert _bare_ps1_invocations((REPO / name).read_text(encoding="utf-8")) == []


def _claims_reference_protection_unconditionally(text: str) -> list[str]:
    """The OS lock on reference/ exists only once `x4refguard.py apply` ran (the installers never
    apply it). A doc for an UNPROTECTED agent that calls it simply present is the R6-04 defect."""
    hits = [p for p in ("tree is read-only on disk", "read-only protection of the reference folder")
            if p in text]
    if "x4refguard.py apply" not in text:
        hits.append("names no `x4refguard.py apply`")
    return hits


@pytest.mark.parametrize("name", ("SETUP_PROMPT.txt", "ADAPTING.md"))
def test_the_os_lock_is_described_as_applied_not_assumed(name):
    assert _claims_reference_protection_unconditionally((REPO / name).read_text(encoding="utf-8")) == []


def test_readme_agent_table_conditions_the_os_lock_on_being_applied():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith(("| Deletes in `reference", "| Overwrites in `reference"))]
    assert len(rows) == 2, rows
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")][1:]
        assert all("once applied" in c for c in cells), row


# --- install red-team 2026-10-04 (lane FX-R) ----------------------------------------------- #

def test_B1_setup_prompt_does_not_send_a_PowerShell_agent_to_bash_setup_sh():
    """A stock Windows PATH resolves `bash` to the WSL stub, so `bash setup.sh` typed in
    PowerShell FAILS -- and install.ps1 already ran setup.sh through Git Bash."""
    assert 'Then run "bash setup.sh"' not in PROMPT
    assert "install.ps1 already ran" in PROMPT and "bash setup.sh" in PROMPT


def test_B3_setup_prompt_names_the_confirmation_of_the_reference_apply():
    assert "x4refguard.py apply" in PROMPT and "--yes" in PROMPT


# --- second install red-team 2026-10-04 (lane FX-R2) ---------------------------------------- #

def test_R2B1_setup_prompt_gives_the_suite_command_PER_SHELL_and_says_what_pass_means():
    """"its test suite should pass" named no command, and the obvious one (`&&`) is rejected by
    Windows PowerShell 5.1. An installed toolkit SKIPS the repo-only tests, so 'pass' must say
    that skips are fine."""
    assert "cd tools/x4validate && uv run pytest -q" in PROMPT
    assert "cd tools/x4validate; uv run pytest -q" in PROMPT
    assert "0 failed and 0 errors" in PROMPT and "skipped tests are expected" in PROMPT
    assert "should pass" not in PROMPT


def test_R2h_setup_prompt_gives_the_WINDOWS_unpack_command():
    assert "bash.exe\" bin/unpack-reference.sh" in PROMPT and "bash bin/unpack-reference.sh" in PROMPT


def test_R2_readme_gives_the_suite_command_per_shell_and_the_sentinel_rule():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "`cd tools/x4validate; uv run pytest -q`" in text and "REPO-ONLY" in text
    assert "carrying the `.unpacked-and-locked` sentinel" in text
