"""Cross-agent installer fixtures retain exact public native runtime inputs."""
import hashlib
import json
from pathlib import Path

from _layout import require_repo
from test_install_over_existing import _agent_source

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures/opencode-runtime"


def test_fixture_tracks_the_actual_generated_native_runtime():
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    require_repo("agent/guards/adapters/opencode.py", why="fixture freshness needs native generator source")
    assert manifest["v"] == 1 and len(manifest["files"]) == 5
    for name, row in manifest["files"].items():
        fixture = (FIXTURE / name).read_bytes().replace(b"\r\n", b"\n")
        assert fixture == (ROOT / row["source"]).read_bytes().replace(b"\r\n", b"\n"), name
        assert hashlib.sha256(fixture).hexdigest() == row["sha256"]


def test_constructor_has_its_native_dependencies_without_selected_opencode(tmp_path):
    source = _agent_source(tmp_path)
    for name in ("opencode_config.py", "opencode_adapter.py", "codex_adapter.py", "patch_paths.py", "save_policy.py",
                 "profile_approval.py"):   # codex_adapter imports it (dc3aac1)
        assert (source / ".opencode/hooks" / name).is_file(), name
    assert (source / ".opencode/plugins/x4guard.js").is_file()
