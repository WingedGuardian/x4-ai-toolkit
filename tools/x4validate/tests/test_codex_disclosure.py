"""Codex hook-trust disclosure (spec 5.8): an unreviewed, changed or disabled Codex hook is
SILENT, so three detectors exist -- the SessionStart banner the model is told to look for,
codex_trust.py outside the session, and a release convention for the frozen template."""
import ast
import importlib.util
import re
import time
from pathlib import Path

import pytest
from _layout import require_repo  # noqa: E402  (R2-B1: repo-only content skips, counted)

from test_gen_codex_tree import TEMPLATE_SHA256

REPO = Path(__file__).resolve().parents[3]
ADAPTER_SRC = REPO / "agent" / "guards" / "adapters" / "codex.py"
require_repo("agent/guards/adapters/codex.py", "agent/instructions/codex.md", module_level=True,
             why="the adapter SOURCE and the Codex instruction source")


def _banner() -> str:
    """The ONE module-level `BANNER = "<str>"` assignment, read from the AST (release review
    R7-7: a regex over the source, or a substring of it, is satisfied by a comment or prose)."""
    tree = ast.parse(ADAPTER_SRC.read_text(encoding="utf-8"))
    hits = [n.value.value for n in tree.body
            if isinstance(n, ast.Assign) and [getattr(t, "id", None) for t in n.targets] == ["BANNER"]
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)]
    assert len(hits) == 1, f"expected exactly one module-level BANNER string, found {len(hits)}"
    return hits[0]


def _load_adapter():
    """The RENDERED adapter (.codex/hooks/codex_adapter.py): there its sibling modules sit beside
    it, as Codex runs it. test_gen_codex_tree pins it to the source the AST is read from."""
    spec = importlib.util.spec_from_file_location("codex_adapter_disclosure",
                                                  REPO / ".codex" / "hooks" / "codex_adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_template_change_is_announced():
    log = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    pinned = re.findall(r"codex-hooks-template: ([0-9a-f]{64})", log)
    assert pinned and pinned[0] == TEMPLATE_SHA256, (
        "the frozen Codex hook template changed: add a CHANGELOG entry headed "
        "'Codex users must re-review hooks' carrying 'codex-hooks-template: <new sha>'")
    head = log[: log.index("codex-hooks-template: " + pinned[0])]
    assert "Codex users must re-review hooks" in head[-2000:]


def test_banner_is_one_constant_and_the_adapter_prints_it(monkeypatch):
    banner = _banner()
    assert banner.startswith("X4 GUARDS LIVE (codex hooks v1)")
    # Behaviour, not text: call session_start with the two SessionStart guards stubbed out, and
    # require the banner as the FIRST line of the context Codex receives.
    mod = _load_adapter()
    assert mod.BANNER == banner
    monkeypatch.setattr(mod, "_run_hook", lambda *a, **k: ("", None), raising=True)
    out = mod.session_start(time.monotonic() + 30)
    assert out["context"].split("\n")[0] == banner, out


def test_banner_text_is_in_the_codex_addendum():
    addendum = (REPO / "agent" / "instructions" / "codex.md").read_text(encoding="utf-8")
    banner = _banner()
    assert banner.split(" —")[0] in addendum      # the model is told to look for EXACTLY what is printed


def test_J_Q2_the_banner_does_not_claim_EVERY_shell_command_is_checked():
    """Plan 3 decision J-Q2: input typed into a running shell (`write_stdin`) and a shell
    `workdir` reach no guard (MEASURED, codex-0160.md), so 'every shell command ... is checked'
    over-claims. The pinned prefix stays; the tail names what is NOT checked."""
    banner = _banner()
    # The grammar, not two substrings (release review R7-7): "<checked> are checked (<unchecked>
    # is not)." -- the running shell must sit in the NOT-checked clause, and nothing in the
    # checked clause may claim "every".
    m = re.fullmatch(r"X4 GUARDS LIVE \(codex hooks v1\) — (?P<checked>[^()]+?) (?:is|are) checked "
                     r"\((?P<unchecked>[^()]+?) (?:is|are) not\)\.", banner)
    assert m, f"banner does not read '<checked> are checked (<unchecked> is not).': {banner!r}"
    assert "running shell" in m.group("unchecked"), m.groupdict()
    assert "running shell" not in m.group("checked") and "every" not in m.group("checked").lower(), m.groupdict()



def test_the_charter_gets_the_real_session_and_the_other_hooks_keep_their_fixed_payload(monkeypatch):
    """Session charter: Codex's real SessionStart event names the charter file (session_id) and says
    when it matters (source `compact`); the reference and canary hooks keep their fixed payload."""
    mod = _load_adapter()
    seen = {}

    def fake(script, payload, timeout):
        seen[script] = payload
        return ("SESSION CHARTER (stub)" if script == "charter.sh" else ""), None

    monkeypatch.setattr(mod, "_run_hook", fake, raising=True)
    out = mod.session_start(time.monotonic() + 30, {"hook_event_name": "SessionStart", "source": "compact",
                                                    "session_id": "0b7c6733-5bc4-4b6f-af5e-8c64ed2ce4fe"})
    fixed = {"hook_event_name": "SessionStart", "source": "startup"}
    assert seen["check-reference-version.sh"] == fixed and seen["session-canary.sh"] == fixed
    assert seen["charter.sh"] == {"hook_event_name": "SessionStart", "source": "compact",
                                  "session_id": "0b7c6733-5bc4-4b6f-af5e-8c64ed2ce4fe"}
    assert "SESSION CHARTER (stub)" in out["context"]
    mod.session_start(time.monotonic() + 30)            # the one-argument call still works
    assert seen["charter.sh"]["source"] == "startup" and seen["charter.sh"]["session_id"] is None


def test_the_charter_hook_round_trips_through_the_rendered_tree(tmp_path):
    """The rendered .codex/hooks/charter.py loads the pinned core and prints a charter written for
    a session, from the toolkit root it sits under (.agent-sessions/ is gitignored there)."""
    import json
    import subprocess
    import sys
    sid = "x4-test-" + str(time.time_ns())
    charter_file = REPO / ".agent-sessions" / (sid + ".md")
    charter_file.parent.mkdir(exist_ok=True)
    try:
        hook = REPO / ".codex" / "hooks" / "charter.py"
        r = subprocess.run([sys.executable, "-B", str(hook), "prompt"], timeout=60, capture_output=True,
                           input=json.dumps({"session_id": sid, "prompt": "port the patch"}).encode())
        assert (r.returncode, r.stdout) == (0, b""), r.stderr
        assert "> port the patch" in charter_file.read_text(encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(hook), "session-start"], timeout=60, capture_output=True,
                           input=json.dumps({"session_id": sid, "source": "compact"}).encode())
        assert r.returncode == 0, r.stderr
        assert "> port the patch" in r.stdout.decode("utf-8")
    finally:
        charter_file.unlink(missing_ok=True)
        try:
            charter_file.parent.rmdir()          # only if this test created it and left it empty
        except OSError:
            pass


def test_a_charter_that_does_not_fit_becomes_a_pointer_never_a_cut(monkeypatch):
    """The Codex context is cut from its end to fit max_chars(), and the charter comes last: when
    the other hooks leave too little room it is replaced by a pointer naming the file, so the agent
    never reads a silently truncated charter."""
    mod = _load_adapter()
    charter_text = "SESSION CHARTER (from .agent-sessions/s1.md):\n" + "c" * 4000 + "\n----- end of charter text -----"

    def fake(script, payload, timeout):
        if script == "charter.sh":
            return charter_text, None
        return ("K" * 8000 if script == "session-canary.sh" else ""), None

    monkeypatch.setattr(mod, "_run_hook", fake, raising=True)
    out = mod.session_start(time.monotonic() + 30, {"hook_event_name": "SessionStart", "source": "compact",
                                                    "session_id": "s1"})["context"]
    assert "not shown (session-start output budget)" in out and ".agent-sessions/s1.md" in out
    assert "--session=s1 list" in out
    assert "c" * 50 not in out
    # The pointer sits right after the banner, so a tight budget that cuts the canary (8,000 characters)
    # still keeps it whole, as long as the banner and pointer themselves fit.
    monkeypatch.setenv("X4_HOOK_MAX_CHARS", "1500")
    out = mod.session_start(time.monotonic() + 30, {"hook_event_name": "SessionStart", "source": "compact",
                                                    "session_id": "s1"})["context"]
    assert "not shown (session-start output budget)" in out and "--session=s1 list" in out

    monkeypatch.delenv("X4_HOOK_MAX_CHARS", raising=False)

    def roomy(script, payload, timeout):
        return (charter_text if script == "charter.sh" else ""), None

    monkeypatch.setattr(mod, "_run_hook", roomy, raising=True)
    out = mod.session_start(time.monotonic() + 30, {"hook_event_name": "SessionStart", "source": "compact",
                                                    "session_id": "s1"})["context"]
    assert out.endswith("----- end of charter text -----")


def _hook_only_tree(tmp_path):
    """A hook-only game-root deployment: .codex/hooks copied into a project with no tools/ of its own."""
    import shutil
    project = tmp_path / "game"
    shutil.copytree(REPO / ".codex" / "hooks", project / ".codex" / "hooks")
    return project


def test_a_hook_only_game_root_loads_the_core_from_the_toolkit(tmp_path, monkeypatch):
    import json
    import os
    import subprocess
    import sys
    project = _hook_only_tree(tmp_path)
    env = dict(os.environ, X4_TOOLKIT=str(REPO))
    env.pop("PYTHONPATH", None)
    hook = project / ".codex" / "hooks" / "charter.py"
    r = subprocess.run([sys.executable, "-B", str(hook), "prompt"], timeout=60, capture_output=True, env=env,
                       input=json.dumps({"session_id": "gr1", "prompt": "from the game root"}).encode())
    assert r.returncode == 0, r.stderr
    assert "> from the game root" in (project / ".agent-sessions" / "gr1.md").read_text(encoding="utf-8")
    assert not (REPO / ".agent-sessions" / "gr1.md").exists()          # the charter stays in the hook's project


def test_the_prompt_fast_path_still_shows_an_existing_ledger(tmp_path):
    import json
    import os
    import subprocess
    import sys
    project = _hook_only_tree(tmp_path)
    env = dict(os.environ, X4_TOOLKIT=str(REPO))
    env.pop("PYTHONPATH", None)
    hook = project / ".codex" / "hooks" / "charter.py"
    event = json.dumps({"session_id": "gr2", "prompt": "first"}).encode()
    r = subprocess.run([sys.executable, "-B", str(hook), "prompt"], timeout=60, capture_output=True, env=env,
                       input=event)
    assert (r.returncode, r.stdout) == (0, b""), r.stderr               # charter created, no ledger: silent
    ledger = REPO / "tools" / "modding_harness" / "ledger.py"
    r = subprocess.run([sys.executable, "-B", str(ledger), "--workspace", str(project), "--session=gr2", "add", "--",
                        "keep this visible"], timeout=60, capture_output=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, "-B", str(hook), "prompt"], timeout=60, capture_output=True, env=env,
                       input=event)
    assert "keep this visible" in r.stdout.decode("utf-8"), r.stderr
