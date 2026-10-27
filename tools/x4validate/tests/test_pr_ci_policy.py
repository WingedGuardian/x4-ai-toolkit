"""X4 consumes the common policy without dropping its existing platform gates."""
import re
from pathlib import Path
from ruamel.yaml import YAML
from _layout import require_repo

ROOT = Path(__file__).resolve().parents[3]


def test_draft_readiness_and_full_queue_use_shared_policy():
    require_repo(".github/workflows/ci.yml", why="CI source is not installed", root=ROOT)
    data = YAML(typ="safe").load((ROOT/".github/workflows/ci.yml").read_text())
    assert set(data["on"]["pull_request"]["types"]) >= {"opened","synchronize","reopened","ready_for_review"}
    assert data["on"]["push"]["branches"] == ["master","main"]
    jobs = data["jobs"]
    assert set(jobs["qualification"]["needs"]) == {"policy","quick","scrub"}
    assert jobs["qualification"]["if"] == "${{ needs.policy.outputs.full_required == 'true' }}"
    assert jobs["qualification"]["uses"] == "./.github/workflows/full-qualification.yml"
    assert jobs["qualification"]["concurrency"] == {"group":"full-qualification-${{ github.repository }}","queue":"max","cancel-in-progress":False}
    assert jobs["required"]["if"] == "${{ always() }}"
    assert set(jobs["required"]["needs"]) == {"policy","quick","scrub","qualification"}
    refs = [step["uses"] for job in jobs.values() for step in job.get("steps",[]) if "/ci-policy@" in step.get("uses","")]
    assert refs and len(set(refs)) == 1
    assert re.fullmatch(r"WingedGuardian/modding-agent-harness/\.github/actions/ci-policy@[0-9a-f]{40}",refs[0])


def test_full_platform_gates_and_original_check_order_are_retained():
    require_repo(".github/workflows/full-qualification.yml",why="CI source is not installed",root=ROOT)
    data = YAML(typ="safe").load((ROOT/".github/workflows/full-qualification.yml").read_text())
    jobs = data["jobs"]
    matrix = jobs["tests"]["strategy"]
    assert matrix["max-parallel"] == 2 and matrix["fail-fast"] is False
    assert {(r["os"],r["experimental"]) for r in matrix["matrix"]["include"]} == {("windows-latest",False),("ubuntu-latest",False)}
    assert jobs["full-required"]["needs"] == ["tests"]
    assert jobs["full-required"]["if"] == "${{ always() }}"
    names = [step.get("name","") for step in jobs["tests"]["steps"]]
    assert names.index("Setup complete") < names.index("Hook regression suite") < names.index("Run the suite with NO game installed")
    assert "Cold-clone verification (the check that has lied most)" in names
