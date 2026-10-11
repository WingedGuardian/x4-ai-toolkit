"""Exercise cold-checkout setup independently of the long pytest suite.

The real Git checkout and shell run; the uv seam audits its inputs and avoids
recursively running pytest. CI still runs the complete cold suite separately.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "tools/x4validate/scripts/verify-cold.sh"
spec = importlib.util.spec_from_file_location("cold_gitbash", REPO / "scripts/gitbash.py")
gitbash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gitbash)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class ColdCheckoutProbe:
    def __init__(self, root, script, bash, env):
        self.root, self.script, self.bash, self.env = root, script, bash, env

    def __repr__(self):
        # A failing pytest must never render the inherited environment values.
        return "ColdCheckoutProbe(environment hidden)"


@pytest.fixture
def checkout_probe():
    base = REPO / ".test-sandbox"
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cold-probe-", dir=base) as work:
        root = Path(work) / "source"
        script = root / "tools/x4validate/scripts/verify-cold.sh"
        script.parent.mkdir(parents=True)
        script.write_bytes(SCRIPT.read_bytes())
        for name in (".gitignore", ".gitattributes"):
            (root / name).write_bytes((REPO / name).read_bytes())
        (root / "tools/x4validate/pyproject.toml").write_bytes(
            (REPO / "tools/x4validate/pyproject.toml").read_bytes())
        git(root, "init", "-q")
        git(root, "add", "--", ".gitignore", ".gitattributes", "tools/x4validate/scripts/verify-cold.sh", "tools/x4validate/pyproject.toml")
        git(root, "-c", "user.name=Toolkit Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        sha = git(root, "rev-parse", "HEAD")
        git(root, "tag", "cold-fixture")
        (root / "later.txt").write_text("newer commit\n", encoding="utf-8")
        git(root, "add", "--", "later.txt")
        git(root, "-c", "user.name=Toolkit Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "later fixture")
        (root / "x4-paths.env").write_text("X4_GAME=private-fixture\n", encoding="utf-8")
        bins = Path(work) / "bin"
        bins.mkdir()
        uv = bins / "uv"
        uv.write_text('''#!/bin/bash
set -eu
case "$*" in
  *"_paths"*)
    if env | grep -q '^X4_'; then echo 'unexpected inherited config' >&2; exit 9; fi
    if [ "${COLD_WARM_TEST:-}" = 1 ]; then echo 'warm|None|None'; else echo 'None|None|None'; fi
    ;;
  *"pytest"*)
    if [ "${COLD_ARCHIVE:-}" = 1 ]; then
      # The release-zip population: NO repository may be visible, not even the parent one.
      if git rev-parse --git-dir >/dev/null 2>&1; then echo 'archive leg sees a repository' >&2; exit 6; fi
      root=$(cd ../.. && pwd)
      test -f "$root/tools/x4validate/scripts/verify-cold.sh" || { echo 'extract is empty' >&2; exit 6; }
      test ! -e "$root/later.txt" || { echo 'wrong archived revision' >&2; exit 6; }
      test ! -e "$root/x4-paths.env" || { echo 'private config travelled' >&2; exit 6; }
      case "$root" in /tmp/*|/var/tmp/*) exit 8;; esac
      printf '%s
' "$root" > "$COLD_TEST_TRACE"
      exit 0
    fi
    root=$(git rev-parse --show-toplevel)
    test "$(git rev-parse HEAD)" = "$COLD_EXPECT_SHA" || { echo 'wrong cloned commit' >&2; exit 6; }
    test ! -e "$root/x4-paths.env" || { echo 'private config travelled' >&2; exit 6; }
    git -C "$root" check-ignore -q .codex/hooks.json || { echo 'Codex ignore query failed' >&2; exit 6; }
    git -C "$root" check-ignore -q .opencode/opencode.jsonc || { echo 'OpenCode ignore query failed' >&2; exit 6; }
    test "$(git -C "$root" check-attr export-ignore -- tools/x4validate/audit/paths.py)" = 'tools/x4validate/audit/paths.py: export-ignore: set' || { echo 'export attribute query failed' >&2; exit 6; }
    case "$root" in /tmp/*|/var/tmp/*) exit 8;; esac
    test "$PYTHONDONTWRITEBYTECODE" = 1
    printf '%s\n' "$root" > "$COLD_TEST_TRACE"
    ;;
  *) exit 2;;
esac
''', encoding="utf-8", newline="\n")
        uv.chmod(0o755)
        bash = gitbash.find_bash()
        if not bash:
            pytest.skip("no real non-WSL bash available")
        env = os.environ.copy()
        env.update(PATH=str(bins) + os.pathsep + env["PATH"],
                   COLD_EXPECT_SHA=sha, COLD_TEST_TRACE=str(Path(work) / "trace"),
                   X4_TOOLKIT=str(root), X4_CONFIG=str(root / "x4-paths.env"),
                   X4_REFERENCE=str(root / "reference"))
        yield ColdCheckoutProbe(root, script, str(bash), env)


def run_probe(probe, ref="cold-fixture"):
    return subprocess.run([probe.bash, str(probe.script), ref], cwd=probe.root, env=probe.env,
                          capture_output=True, text=True, timeout=90)


def test_cold_checkout_has_git_metadata_and_a_neutral_location(checkout_probe):
    r = run_probe(checkout_probe)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COLD RUN CLEAN" in r.stdout
    assert Path(checkout_probe.env["COLD_TEST_TRACE"]).is_file()


def test_a_warm_clone_stops_before_pytest(checkout_probe):
    checkout_probe.env["COLD_WARM_TEST"] = "1"
    r = run_probe(checkout_probe)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "REFUSING TO RUN" in r.stderr
    assert not Path(checkout_probe.env["COLD_TEST_TRACE"]).exists()


def test_an_unknown_revision_stops_before_pytest(checkout_probe):
    r = run_probe(checkout_probe, "missing-cold-fixture")
    assert r.returncode == 2, r.stdout + r.stderr
    assert not Path(checkout_probe.env["COLD_TEST_TRACE"]).exists()


def run_archive_probe(probe, ref="cold-fixture"):
    probe.env["COLD_ARCHIVE"] = "1"
    return subprocess.run([probe.bash, str(probe.script), "--archive", ref], cwd=probe.root,
                          env=probe.env, capture_output=True, text=True, timeout=90)


def test_the_archive_leg_sees_no_repository_and_the_right_revision(checkout_probe):
    """FX-H2 / I-1: the release zip has no .git. The extract sits inside the probe's own repo,
    so this passes only if GIT_CEILING_DIRECTORIES really hides it (the stub exits 6 if not)."""
    r = run_archive_probe(checkout_probe)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COLD RUN CLEAN (archive)" in r.stdout
    assert "no repository visible" in r.stdout
    assert Path(checkout_probe.env["COLD_TEST_TRACE"]).is_file()


def test_the_archive_leg_stops_on_an_unknown_revision(checkout_probe):
    r = run_archive_probe(checkout_probe, "missing-cold-fixture")
    assert r.returncode == 2, r.stdout + r.stderr
    assert not Path(checkout_probe.env["COLD_TEST_TRACE"]).exists()


def test_the_archive_leg_keeps_the_cold_precondition(checkout_probe):
    checkout_probe.env["COLD_WARM_TEST"] = "1"
    r = run_archive_probe(checkout_probe)
    assert r.returncode == 3, r.stdout + r.stderr
    assert not Path(checkout_probe.env["COLD_TEST_TRACE"]).exists()
