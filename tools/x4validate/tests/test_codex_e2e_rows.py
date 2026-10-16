"""The live Codex e2e rows must not be satisfiable by the model ECHOING the prompt.

v4.0.0 review R4-8: the `validator-context` row passed when the transcript contained
"X4 VALIDATION" (case-folded) -- and its own prompt asked for "any message about X4
validation", which `codex exec` echoes into the very transcript the check reads. The row could
not go red. The check now looks only for strings the VALIDATOR emits, after removing the
prompt from the transcript, and the prompt no longer contains any of them.

No Codex runs here: the rows' check functions are pure over a transcript string.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "codex-e2e.py"


@pytest.fixture(scope="module")
def e2e():
    return _load()


@pytest.fixture
def codex_payload():
    from _layout import installed_layout
    hooks, rules = REPO / ".codex/hooks", REPO / ".codex/rules"
    if not hooks.exists() and not rules.exists() and installed_layout():
        pytest.skip("Codex runtime was not selected; live Codex decoy construction requires its hooks and rules")
    assert hooks.is_dir() and rules.is_dir(), "selected Codex runtime is incomplete"


def test_a_MISSING_codex_e2e_script_FAILS_rather_than_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.modules[__name__], "SCRIPT", tmp_path / "codex-e2e.py")
    with pytest.raises(AssertionError, match="ships in every layout"):
        _load()


def _load():
    # v4.0.0 delta review: this was an unconditional skip. scripts/codex-e2e.py is tracked,
    # not export-ignored, and copied by both installers (`scripts` is in the copy set), so
    # every layout carries it: a missing one is a broken tree, never a reason to skip.
    assert SCRIPT.is_file(), f"{SCRIPT} is missing -- it ships in every layout"
    spec = importlib.util.spec_from_file_location("codex_e2e_rows", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["codex_e2e_rows"] = mod
    spec.loader.exec_module(mod)
    return mod


def _check(e2e, text):
    prompt, check, _blocks = e2e.ROWS["validator-context"]
    return check(None, text)


def test_the_ECHOED_prompt_alone_does_NOT_satisfy_the_validator_row(e2e):
    prompt = e2e.ROWS["validator-context"][0]
    transcript = "user\n" + prompt + "\ncodex\nI received no such message.\n"
    assert not _check(e2e, transcript)


@pytest.mark.parametrize("said", ["x4validate (advisory) flagged this edit:",
                                  "VALIDATION NOT COMPLETED: one or more requested checks could not run"])
def test_TWIN_a_message_the_VALIDATOR_emits_does(e2e, said):
    prompt = e2e.ROWS["validator-context"][0]
    assert _check(e2e, "user\n" + prompt + "\ncodex\nThe message was: " + said + "\n")


@pytest.mark.parametrize("said", [
    "X4 VALIDATION DID NOT RUN for dev/mymod/libraries/wares.xml: x",
    "X4 VALIDATION DID NOT RUN: the toolkit hook failed (boom). x4validate (advisory) earlier"])
def test_FXB2_a_DID_NOT_RUN_context_FAILS_the_row(e2e, said):
    """FX-B2 (delta review): "X4 VALIDATION DID NOT RUN" is the ADAPTER saying the hook FAILED
    -- no validator ran -- and it passed the row whose point is that the validator's context
    reached the model. It now fails it, even beside a validator marker."""
    prompt = e2e.ROWS["validator-context"][0]
    assert not _check(e2e, "user\n" + prompt + "\ncodex\nThe message was: " + said + "\n")


def test_FXB2_the_prompt_does_not_name_the_failure_marker_either(e2e):
    assert e2e.VALIDATOR_FAILED.lower() not in e2e.ROWS["validator-context"][0].lower()


def test_the_prompt_names_none_of_the_markers(e2e):
    prompt = e2e.ROWS["validator-context"][0]
    for marker in e2e.VALIDATOR_MARKERS:
        assert marker.lower() not in prompt.lower(), marker


def test_decoy_carries_the_exact_shared_runtime_and_lock(e2e, tmp_path, codex_payload):
    root, env = e2e.build_decoy(tmp_path)
    expected = REPO / "tools/modding_harness"
    for source in expected.rglob("*.py"):
        assert (root / "tools/modding_harness" / source.relative_to(expected)).read_bytes() == source.read_bytes()
    assert (root / "tools/modding-harness.lock.json").read_bytes() == (REPO / "tools/modding-harness.lock.json").read_bytes()
    for name in ("X4_TOOLKIT", "X4_REFERENCE", "X4_GAME", "X4_PROFILE", "X4_SAVES",
                 "X4_EXTENSIONS", "X4_MODS", "X4_DOCUMENTS", "X4_BACKUPS", "X4_CONFIG"):
        assert Path(env[name]).resolve().is_relative_to(tmp_path.resolve()), (name, env[name])
    assert Path(env["X4_CONFIG"]).read_bytes() == b""
    assert not Path(env["X4_GAME"]).resolve().is_relative_to((root / "dev").resolve())


def test_existing_decoy_is_refused_without_changing_its_bytes(e2e, tmp_path):
    root = tmp_path / "X4 E2E root"
    root.mkdir()
    sentinel = root / "user-data.txt"
    sentinel.write_bytes(b"retain this")
    with pytest.raises(FileExistsError):
        e2e.build_decoy(tmp_path)
    assert sentinel.read_bytes() == b"retain this"
    assert sorted(p.name for p in root.iterdir()) == [sentinel.name]


def test_constructor_uses_only_shipped_runtime_rendering(e2e, tmp_path, monkeypatch, codex_payload):
    runtime = tmp_path / "runtime-only"
    for name in (".codex/hooks", ".codex/rules", "tools/modding_harness"):
        shutil.copytree(REPO / name, runtime / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("tools/modding-harness.lock.json", "tools/x4_harness/x4_upgrade_render.py",
                 "tools/x4_harness/codex-hooks.json.tmpl"):
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    monkeypatch.setattr(e2e, "REPO", runtime)
    monkeypatch.setattr(e2e, "GEN", runtime / "absent-generator.py", raising=False)
    root, _env = e2e.build_decoy(tmp_path / "out")
    text = (root / ".codex/hooks.json").read_text(encoding="utf-8")
    assert isinstance(json.loads(text), dict) and "{{" not in text
    assert root.as_posix() in text


@pytest.mark.parametrize("target,decision", [("reference/libraries/wares.xml", "deny"),
                                               ("dev/mymod/libraries/wares.xml", "allow")])
def test_constructed_decoy_runs_evaluated_policy_without_a_host(e2e, tmp_path, target, decision, codex_payload):
    root, env = e2e.build_decoy(tmp_path)
    result = subprocess.run([sys.executable, str(root / ".codex/hooks/x4guard.py"), "check",
                             "--kind", "write", "--path", str(root / target)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    verdict = json.loads(result.stdout)
    assert verdict["decision"] == decision, verdict
    assert not verdict.get("inert", False), verdict
    assert e2e.ref_intact(root)


def test_phase_a_retains_independent_hook_control_and_fault_roots(e2e, tmp_path, monkeypatch, codex_payload):
    roots = []
    def fake_host(root, env, prompt, log, bypass=True):
        roots.append(root)
        if not (root / ".codex/hooks.json").is_file():
            (root / e2e.REF).write_text("control changed", encoding="utf-8")
        return "INERT"
    monkeypatch.setattr(e2e, "codex_exec", fake_host)
    results = e2e.phase_a(tmp_path, ["shell-write"])
    assert all(row["ok"] for row in results), results
    assert len(roots) == len(set(roots)) == 3
    assert [e2e.ref_intact(root) for root in roots] == [True, False, True]
