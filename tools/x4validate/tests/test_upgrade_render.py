"""X4 upgrade reviews bind generated hook definitions and per-host shell tokens."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]


def renderer():
    path = REPO / "tools/x4_harness/x4_upgrade_render.py"
    spec = importlib.util.spec_from_file_location("x4_render_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(base):
    root, source, bundle = base / "destination [fixture]", base / "source", base / "review"
    root.mkdir()
    source.mkdir()
    files = {".agents/skills/example/SKILL.md": b"Run {{TOOLKIT}}/tools/example\n",
             ".opencode/skills/example/SKILL.md": b"Run {{TOOLKIT}}/tools/example\n",
             "tools/x4_harness/codex-hooks.json.tmpl": (REPO / "agent/targets/codex/hooks.json.tmpl").read_bytes(),
             ".codex/hooks/codex-entry.sh": (REPO / ".codex/hooks/codex-entry.sh").read_bytes()}
    for name, data in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root, source, bundle, {"v": 1, "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_rendered_review_generates_codex_and_keeps_per_host_shells_independent(tmp_path, monkeypatch, shell):
    monkeypatch.syspath_prepend(str(REPO / "tools/x4_harness"))
    import save_policy
    upgrade, installation = (save_policy.core(REPO, name) for name in ("upgrade", "installation"))
    module = renderer()
    root, source, bundle, manifest = fixture(tmp_path)
    other = "powershell" if shell == "bash" else "bash"
    config = {"v": 1, "agent_shell": shell, "opencode_shell": other}
    review = upgrade.stage_review_bundle(root, source, bundle, manifest, reserve=0,
        candidate_transform=lambda installed, staged, files: module.prepare(installed, staged, files, config),
        generated_paths=(".codex/hooks.json",))
    assert not list(root.iterdir())
    assert review["generated_files"] == [".codex/hooks.json"]
    expected = "$env:X4_TOOLKIT" if shell == "powershell" else "$X4_TOOLKIT"
    assert (bundle / "candidate/.agents/skills/example/SKILL.md").read_text() == "Run " + expected + "/tools/example\n"
    expected_other = "$env:X4_TOOLKIT" if other == "powershell" else "$X4_TOOLKIT"
    assert expected_other in (bundle / "candidate/.opencode/skills/example/SKILL.md").read_text()
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == value for name, value in manifest["files"].items())
    assert json.loads((bundle / "candidate/.codex/hooks.json").read_bytes()) == json.loads(module.render_codex(
        (source / module.TEMPLATE).read_text(), root))
    result = installation.apply(root, bundle, hashlib.sha256((bundle / "complete.json").read_bytes()).hexdigest(),
        quiescent=True, reserve=0, candidate_validator=module.validate)
    assert (root / ".codex/hooks.json").exists()
    rolled = installation.rollback(root, result["operation"], quiescent=True)
    assert ".codex/hooks.json" in rolled["retained_new_files"]
    assert not (root / ".codex/hooks.json").exists()


@pytest.mark.parametrize("bad", ['"', '\n', '\r', '%', '!', '$', '`'])
def test_codex_root_refuses_shell_expansion_and_broken_quoting(bad):
    module = renderer()
    with pytest.raises(ValueError, match="refusing a Codex hook root"):
        module.render_codex((REPO / "agent/targets/codex/hooks.json.tmpl").read_text(), "C:/fixture/" + bad + "toolkit")


def test_unrendered_skill_refuses_before_any_installed_write(tmp_path):
    module = renderer()
    file = tmp_path / "SKILL.md"
    file.write_bytes(b"Run {{TOOLKIT}}/tools/example")
    with pytest.raises(ValueError, match="destination rendering"):
        module.validate(tmp_path, ({"path": ".agents/skills/example/SKILL.md", "source": file},))


def test_missing_bound_template_does_not_generate_an_unreviewed_definition(tmp_path):
    root, source, bundle, manifest = fixture(tmp_path)
    manifest["files"].pop(renderer().TEMPLATE)
    with pytest.raises(ValueError, match="omits the canonical Codex template"):
        renderer().prepare(root, source, manifest["files"], {"v": 1, "agent_shell": "bash", "opencode_shell": "bash"})


def test_cli_stages_rendered_definitions_without_touching_the_destination(tmp_path):
    root, source, bundle, manifest = fixture(tmp_path)
    inventory, config = tmp_path / "inventory.json", tmp_path / "config.json"
    inventory.write_text(json.dumps(manifest))
    config.write_text('{"v":1,"agent_shell":"powershell","opencode_shell":"bash"}')
    result = subprocess.run([sys.executable, "-B", str(REPO / "tools/agent-harness.py"), "upgrade-stage",
        "--source", str(source), "--installed-root", str(root), "--candidate", str(inventory),
        "--destination", str(bundle), "--render-config", str(config)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["generated_files"] == [".codex/hooks.json"]
    assert not list(root.iterdir())
