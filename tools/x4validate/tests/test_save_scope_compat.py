"""Physical path identity and save-only helper qualification, on owned trees."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from _layout import ROOT, common_hook_dir


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, common_hook_dir(ROOT) / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


facts = _load("scope_compat_facts", "hook_facts.py")
policy = _load("scope_compat_policy", "save_policy.py")


@pytest.mark.parametrize("prefix", ["FileSystem::", "filesystem::", "Microsoft.PowerShell.Core\\FileSystem::"])
@pytest.mark.parametrize("suffix", ["C:/MiXeD/Save.xml.gz", "C:/MiXeD/../Save.xml.gz", "//?/C:/MiXeD/Save.xml.gz"])
def test_physical_filesystem_provider_decode_retains_case_and_path(prefix, suffix):
    assert facts.physical_join("", prefix + suffix) == suffix


def test_relative_operand_uses_decoded_provider_cwd_without_case_folding():
    assert facts.physical_join("FileSystem::C:/MiXeD", "Save.xml.gz") == "C:/MiXeD/Save.xml.gz"


@pytest.mark.parametrize("path", ["Registry::C:/MiXeD", "Unknown::C:/MiXeD", "file:///C:/MiXeD"])
def test_other_provider_and_uri_spellings_are_not_decoded_as_filesystem(path):
    # With no filesystem cwd these spellings identify no physical file.
    assert facts.physical_join("", path) == ""


@pytest.mark.parametrize("flags,expected", [
    ({"powershell_error": "does not parse as PowerShell: PRIVATE_MARKER"}, "does not parse"),
    ({"powershell_error": "PRIVATE_MARKER"}, "scope could not be completely evaluated"),
    ({"carrier_untranslated": True}, "scope could not be completely evaluated"),
    # Past the expansion bound the Bash backend's only answer is the same deny; running
    # it re-expands the whole command, which doubled the cost under the 20 s deadline and
    # replaced the actionable reason with "policy engine failed: TimeoutExpired" on CI.
    ({"carriers_truncated": True}, "stopped expanding"),
])
def test_scope_refusal_has_actionable_static_diagnostic_without_command_or_parser_text(monkeypatch, flags, expected):
    monkeypatch.setitem(sys.modules, "hook_facts", SimpleNamespace(
        facts=lambda *args, **kwargs: flags, PS_PARSE_ERROR="does not parse as PowerShell"))
    with pytest.raises(policy.ScopeError) as caught:
        policy.shell_evidence({"command": "PRIVATE_MARKER"}, {})
    assert expected in str(caught.value)
    assert "PRIVATE_MARKER" not in str(caught.value)


@pytest.mark.parametrize("flags", [{"carriers_truncated": False}, {}])
def test_TWIN_a_command_within_the_expansion_bound_still_yields_evidence(monkeypatch, flags):
    def collect(*args, evidence=None, **kwargs):
        evidence.append({"path": "C:/x", "operation": "delete"})
        return flags
    monkeypatch.setitem(sys.modules, "hook_facts", SimpleNamespace(
        facts=collect, PS_PARSE_ERROR="does not parse as PowerShell"))
    assert policy.shell_evidence({"command": "x"}, {}) == [{"path": "C:/x", "operation": "delete"}]


@pytest.fixture
def translator(monkeypatch):
    """The REAL translation path with subprocess.run replaced: nothing starts a PowerShell."""
    monkeypatch.setitem(sys.modules, "hook_facts", facts)
    monkeypatch.setattr(facts, "_PS_CACHE", {})
    monkeypatch.setattr(facts, "_ps_spent", [0.0])
    monkeypatch.setattr(facts, "_pwsh_exe", lambda: "fixture-powershell")
    monkeypatch.setattr(policy, "folders", lambda roots: [], raising=False)

    def install(result=None, raises=None):
        def run(*args, **kwargs):
            if raises is not None:
                raise raises
            return SimpleNamespace(stdout=json.dumps(result).encode() if result is not None else b"not json",
                                   returncode=1)
        monkeypatch.setattr(facts.subprocess, "run", run)
    return install


def _refusal(tool, command):
    with pytest.raises(policy.ScopeError) as caught:
        policy.shell_evidence({"tool_name": tool, "tool_input": {"command": command}, "cwd": "."}, {})
    assert not isinstance(caught.value, policy.ShellSyntaxError)
    return str(caught.value)


TIMED_OUT = subprocess.TimeoutExpired("fixture-powershell", 10)
WRAPPED = "powershell -c 'Get-Item PRIVATE_MARKER'"


@pytest.mark.parametrize("tool,command", [("PowerShell", "Get-Item PRIVATE_MARKER"), ("Bash", WRAPPED)])
def test_timed_out_translation_says_so_not_available_parser(translator, tool, command):
    translator(raises=TIMED_OUT)
    reason = _refusal(tool, command)
    assert "scope could not be completely evaluated" in reason and "timed out" in reason
    assert "available parser" not in reason and "PRIVATE_MARKER" not in reason


def test_spent_budget_says_so_not_available_parser(translator, monkeypatch):
    translator(raises=AssertionError("the budget refusal must not start PowerShell"))
    monkeypatch.setattr(facts, "_PS_BUDGET_S", 0.1)    # facts() resets what was spent, not this
    reason = _refusal("PowerShell", "Get-Item PRIVATE_MARKER")
    assert "budget" in reason and "available parser" not in reason and "PRIVATE_MARKER" not in reason


@pytest.mark.parametrize("tool,command", [("PowerShell", "Get-Item PRIVATE_MARKER"), ("Bash", WRAPPED)])
@pytest.mark.parametrize("failure,expected", [
    ({"raises": OSError("PRIVATE_MARKER")}, "could not be started"),
    ({}, "no verdict"),
    ({"result": {"ok": False, "reason": "PRIVATE_MARKER"}}, "could not be translated"),
    # Translator text is never a category: naming a timeout in it must not read as one.
    ({"result": {"ok": False, "reason": "PowerShell could not be run (TimeoutExpired) PRIVATE_MARKER"}},
     "could not be translated"),
])
def test_TWIN_other_failures_keep_the_parser_remedy_and_name_a_static_category(translator, tool, command, failure, expected):
    translator(**failure)
    reason = _refusal(tool, command)
    assert expected in reason and "use literal targets and an available parser" in reason
    assert "timed out" not in reason and "PRIVATE_MARKER" not in reason


def test_no_powershell_is_named_without_the_configured_value(translator, monkeypatch):
    translator(raises=AssertionError("no PowerShell means nothing starts"))
    monkeypatch.setattr(facts, "_pwsh_exe", lambda: None)
    monkeypatch.setenv("X4_PWSH", "PRIVATE_MARKER")
    reason = _refusal("PowerShell", "Get-Item x")
    assert "no PowerShell" in reason and "available parser" in reason and "PRIVATE_MARKER" not in reason


@pytest.mark.parametrize("failure", [{"raises": TIMED_OUT}, {"raises": OSError("x")}, {},
                                     {"result": {"ok": False, "reason": "x"}}])
@pytest.mark.parametrize("tool,command", [("PowerShell", "Get-Item x"), ("Bash", WRAPPED)])
def test_the_category_changes_no_verdict(translator, monkeypatch, failure, tool, command):
    translator(**failure)
    monkeypatch.setattr(policy, "read_roots", lambda deadline: {"toolkit": str(ROOT)})
    call = ("shell", tool.lower(), command, None, "fixture")
    result = policy.batch({"tool_name": tool, "tool_input": {"command": command}, "cwd": "."}, [call],
                          lambda qualified: None, None, "codex", preview=True, execution_cwd_known=True)
    assert result["decision"] == "deny" and result["inert"] is True
    assert result["result"]["evaluation"] == "failed" and not result["transactions"]


@pytest.mark.parametrize("failure", [{"raises": TIMED_OUT}, {}])
def test_the_bash_backend_stream_is_unchanged_by_the_category(translator, failure):
    """The backend ASKS on rc 4 with the old reason; for a carrier it reads the same one bool."""
    translator(**failure)
    rc, raw, got = facts.ipc({"tool_name": "PowerShell", "tool_input": {"command": "Get-Item x"}}, {})
    assert rc == 4 and raw == str(got["powershell_error"]).encode() and b"category" not in raw
    rc, raw, got = facts.ipc({"tool_name": "Bash", "tool_input": {"command": WRAPPED}}, {})
    assert rc == 0 and b"carrier_untranslated\t1" in raw and b"category" not in raw
    assert got["untranslated_category"]


def test_batch_syntax_refusal_is_not_an_inert_guard_failure(monkeypatch):
    monkeypatch.setattr(policy, "read_roots", lambda deadline: {"toolkit": str(ROOT)})
    def syntax(*args, **kwargs):
        raise policy.ShellSyntaxError("PowerShell command does not parse; correct its syntax.")
    monkeypatch.setattr(policy, "shell_evidence", syntax)
    result = policy.batch({"tool_name": "fixture", "tool_input": {}},
        [("shell", "powershell", "bad syntax", None, "fixture")], lambda qualified: None,
        None, "codex", preview=True, execution_cwd_known=True)
    assert result["decision"] == "deny" and not result["inert"]
    assert result["result"]["evaluation"] == "evaluated"
    assert "does not parse" in result["reason"] and not result["transactions"]


@pytest.mark.skipif(os.name != "nt", reason="Windows drive namespace parsing")
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
@pytest.mark.parametrize("suffix,pattern", [("MiXeD.xml.gz", False), ("*.xml.gz", True), ("Save[12].xml.gz", True)])
def test_drive_namespace_is_not_a_pattern_but_filename_magic_still_is(prefix, suffix, pattern):
    assert policy._has_magic(prefix + "C:\\MiXeD\\" + suffix) is pattern


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "toolkit"
    shutil.copytree(ROOT / "tools/modding_harness", root / "tools/modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "tools/modding-harness.lock.json", root / "tools/modding-harness.lock.json")
    state = root / ".modding-agent-harness"
    state.mkdir()
    store = tmp_path / "external-store"
    store.mkdir()
    (state / "config.json").write_text(json.dumps({"v": 1, "save_store": str(store), "save_reserve_bytes": 0}))
    saves = tmp_path / "profile/save"
    saves.mkdir(parents=True)
    return root, {"profile": str(saves.parent), "saves": str(saves)}, tmp_path


@pytest.mark.parametrize("relative", ["game/extensions/amod/*", "toolkit/dev/**", "profile/save-other/*"])
def test_disjoint_recursive_helpers_do_not_become_unqualified_save_operations(world, relative):
    root, roots, temp = world
    row = {"path": str(temp / relative), "operation": "delete", "scope": "tree", "unresolved": False}
    _profile, plan, _affected, _save_only = policy.resolve(root, roots, [row])
    assert not plan.unresolved, plan.unresolved
    assert not plan.mutations


@pytest.mark.parametrize("relative", ["profile/save/*", "profile/save/**/future.xml.gz", "profile/*"])
def test_recursive_helpers_intersecting_save_roots_refuse_even_without_members(world, relative):
    root, roots, temp = world
    _profile, plan, _affected, _save_only = policy.resolve(root, roots, [{
        "path": str(temp / relative), "operation": "write", "scope": "tree", "unresolved": False}])
    assert "membership is not qualified" in plan.unresolved, plan.unresolved


@pytest.mark.parametrize("relative,scope,reason", [
    ("toolkit/*", "tree", "neutral harness state"),
    ("toolkit/.*", "file", "neutral harness state"),
    ("toolkit/**", "tree", "neutral harness state"),
    ("external-store/*", "tree", "permanent save store"),
])
def test_narrow_save_scope_still_protects_state_and_independent_store(world, relative, scope, reason):
    root, roots, temp = world
    _profile, plan, _affected, _save_only = policy.resolve(root, roots, [{
        "path": str(temp / relative), "operation": "delete", "scope": scope, "unresolved": False}])
    assert reason in plan.unresolved, plan.unresolved
