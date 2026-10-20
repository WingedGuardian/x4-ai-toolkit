"""Project pytest startup must not prune another project's older numbered runs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


def child(tmp_path, bootstrap, explicit=None):
    shared = tmp_path / "shared-user-temp"
    shared.mkdir()
    import getpass
    old = shared / ("pytest-of-" + getpass.getuser()) / "pytest-0"
    old.mkdir(parents=True)
    (old / "unmarked-other-project.bin").write_bytes(b"KEEP OTHER PROJECT")
    case = tmp_path / "cases"
    case.mkdir()
    result_file = case / "result.json"
    test = case / "test_child.py"
    test.write_text("import json\nfrom pathlib import Path\n"
        "def test_fixture(tmp_path):\n"
        "    Path(" + repr(str(result_file)) + ").write_text(json.dumps({'base': str(tmp_path.parent)}))\n")
    code = ("import importlib.util,sys,pytest\n"
            "s=importlib.util.spec_from_file_location('x4_child_bootstrap',sys.argv[1])\n"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            "raise SystemExit(pytest.main(sys.argv[2:],plugins=[m]))\n")
    args = [sys.executable, "-B", "-c", code, str(bootstrap), "-q",
            "-o", "tmp_path_retention_count=0", "-o", "tmp_path_retention_policy=all", str(test)]
    if explicit is not None:
        args += ["--basetemp", str(explicit)]
    run = subprocess.run(args, cwd=case, capture_output=True, text=True, timeout=60,
                         env=dict(os.environ, PYTEST_DEBUG_TEMPROOT=str(shared)))
    return run, old / "unmarked-other-project.bin", result_file


@pytest.mark.parametrize("bootstrap", ["tools/x4validate/tests/conftest.py", "tools/basex/conftest.py"])
def test_default_pytest_keeps_legacy_shared_runs_and_cleans_only_its_own_scratch(tmp_path, bootstrap):
    run, witness, output = child(tmp_path, ROOT / bootstrap)
    assert run.returncode == 0, run.stdout + run.stderr
    assert witness.read_bytes() == b"KEEP OTHER PROJECT"
    base = Path(json.loads(output.read_text())["base"])
    assert base.parent.name.startswith("x4-pytest-session-")
    assert not base.parent.exists(), "this invocation's owned scratch was left behind"


def test_existing_explicit_basetemp_is_refused_without_erasing_it(tmp_path):
    explicit = tmp_path / "prior-run"
    explicit.mkdir()
    (explicit / "previous.bin").write_bytes(b"KEEP PRIOR RUN")
    run, witness, _ = child(tmp_path, ROOT / "tools/x4validate/tests/conftest.py", explicit)
    assert run.returncode != 0
    assert "new directory" in run.stdout + run.stderr
    assert (explicit / "previous.bin").read_bytes() == b"KEEP PRIOR RUN"
    assert witness.read_bytes() == b"KEEP OTHER PROJECT"


def test_new_explicit_basetemp_is_respected(tmp_path):
    explicit = tmp_path / "new-run"
    run, witness, output = child(tmp_path, ROOT / "tools/x4validate/tests/conftest.py", explicit)
    assert run.returncode == 0, run.stdout + run.stderr
    assert Path(json.loads(output.read_text())["base"]) == explicit
    assert witness.read_bytes() == b"KEEP OTHER PROJECT"
    assert (explicit / ".x4-temp-owner.json").is_file()


def test_late_global_initialization_cancels_deferred_pruning(tmp_path):
    from contextlib import ExitStack
    from types import SimpleNamespace
    import x4pytest
    witness = tmp_path / "other-project.bin"
    witness.write_bytes(b"KEEP")
    stack = ExitStack()
    stack.callback(witness.unlink)
    config = SimpleNamespace(_tmp_path_factory=SimpleNamespace(_basetemp=tmp_path, _exit_stack=stack))
    with pytest.raises(pytest.UsageError, match="before toolkit isolation"):
        x4pytest.configure(config, ROOT)
    stack.close()
    assert witness.read_bytes() == b"KEEP"
