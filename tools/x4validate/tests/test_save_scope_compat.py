"""Physical path identity and save-only helper qualification, on owned trees."""
import importlib.util
import json
import os
import shutil
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
])
def test_scope_refusal_has_actionable_static_diagnostic_without_command_or_parser_text(monkeypatch, flags, expected):
    monkeypatch.setitem(sys.modules, "hook_facts", SimpleNamespace(
        facts=lambda *args, **kwargs: flags, PS_PARSE_ERROR="does not parse as PowerShell"))
    with pytest.raises(policy.ScopeError) as caught:
        policy.shell_evidence({"command": "PRIVATE_MARKER"}, {})
    assert expected in str(caught.value)
    assert "PRIVATE_MARKER" not in str(caught.value)


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
