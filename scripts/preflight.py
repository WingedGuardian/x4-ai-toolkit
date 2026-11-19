"""X4 toolkit push preflight: the checks CI rejects in its first minutes, run locally in about a minute.

Maintainer-only (export-ignored, never in an install). The pre-push gate runs it as its FIRST step and stops on
anything but a pass; it can also be run by hand:

    python scripts/preflight.py <toolkit checkout or worktree>

Why (2026-11-17/18, the 4.2.0 round): two pushes each spent a ~2 h pre-push gate and then failed on
something that takes seconds to see -- an unmarked except handler (test_no_silent_swallow) and a
trailing comment on a `timeout-minutes:` line that the SHARED ci-policy contract reads literally
(Fast checks red in 6 s). Neither is something the toolkit's own pre-push gate ran in its fast part.

Checks (each one's exit code is captured bare and reported; any failure -> exit 1; a check that
cannot RUN -> exit 2, never a pass):
  1. Python syntax of every tracked .py (what CI's "Python syntax" step does)
  2. `uv lock --check` in tools/x4validate (CI's "Dependency lock freshness")
  3. the shared ci-policy contract at the EXACT commit the workflows pin (CI's ci-policy step)
  4. generated trees and CLI reference are current
  5. the repo-wide meta tests that enumerate every file/rule (they fail on code no changed-file
     mapping points at)

THE SHARED CONTRACT (3) is `ci/contracts.py` of the modding-agent-harness repository at the pinned commit. Sources,
in order: a cache in the git directory (`<git-common-dir>/prepush-gate/contracts/<sha>.py`, shared by every worktree,
never tracked); a local clone of that repository named by the X4_HARNESS_CLONE environment variable (the offline
source); the raw file at the pinned commit on GitHub. The first copy found is cached. None of them -> the check could
not run (exit 2). The file runs here as CI runs it: it is the owner's own repository at a commit the workflows pin.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CONTRACT_REPO = "WingedGuardian/modding-agent-harness"
CONTRACT_PATH = "ci/contracts.py"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{sha}/{path}"
FETCH_TIMEOUT_S = 20
META_TESTS = ["tests/test_no_silent_swallow.py", "tests/test_workflow_ownership.py",
              "tests/test_claude_md_budget.py", "tests/test_gen_agent_trees.py",
              "tests/test_cli_reference.py"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, cwd):
    t = time.monotonic()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=NO_WINDOW)
    return p.returncode, (p.stdout + p.stderr), time.monotonic() - t


#: The interpreter this preflight runs under. The pre-push hook starts it with whatever `python` is first on PATH
#: (3.10 on the maintainer's machine, 2026-11-18), while the toolkit and CI are 3.13: parsing 3.12+ f-strings in-process
#: there reported 3 valid files as syntax errors. Patchable by the tests.
RUNNING_PYTHON = tuple(sys.version_info[:2])

_SYNTAX_SNIPPET = (
    "import ast, sys\n"
    "bad = []\n"
    "for f in sys.stdin.read().split('\\0'):\n"
    "    if not f: continue\n"
    "    try: ast.parse(open(f, 'rb').read(), filename=f)\n"
    "    except SyntaxError as e: bad.append(f'{f}:{e.lineno}: {e.msg}')\n"
    "    except OSError as e: bad.append(f'{f}: unreadable ({e})')\n"
    "print('\\n'.join(bad[:20]))\n"
    "sys.exit(1 if bad else 0)\n")


def _required_python(root: Path):
    """(major, minor) from tools/x4validate/pyproject.toml `requires-python = ">=X.Y"`; None when unreadable."""
    try:
        text = (root / "tools" / "x4validate" / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^requires-python\s*=\s*"\s*>=\s*(\d+)\.(\d+)', text, re.M)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _syntax_under_project_python(root: Path, files: list[str], need):
    """Parse with the project's own interpreter (`uv run`), for a preflight started by an older Python."""
    t = time.monotonic()
    try:
        p = subprocess.run(["uv", "run", "--frozen", "--project", str(root / "tools" / "x4validate"), "python", "-c",
                            _SYNTAX_SNIPPET], cwd=root, input="\0".join(files), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    except OSError as e:
        return 2, f"this preflight runs under Python {RUNNING_PYTHON}, the toolkit needs >= {need}, and uv could not run: {e}"
    if p.returncode not in (0, 1):
        return 2, (f"this preflight runs under Python {RUNNING_PYTHON}, the toolkit needs >= {need}; parsing under the "
                   f"project interpreter could not run (rc {p.returncode}): {(p.stdout + p.stderr).strip()[-300:]}")
    note = f"{len(files)} files parsed under the project interpreter ({time.monotonic() - t:.0f}s)"
    return p.returncode, (note if p.returncode == 0 else p.stdout.strip())


def check_syntax(root: Path):
    rc, out, _ = run(["git", "ls-files", "-z", "*.py"], root)
    if rc:
        return 2, "git ls-files failed: " + out[-300:]
    files = [f for f in out.split("\0") if f]
    need = _required_python(root)
    if files and need and RUNNING_PYTHON < need:
        return _syntax_under_project_python(root, files, need)
    bad = []
    for f in files:
        try:
            ast.parse((root / f).read_bytes(), filename=f)
        except SyntaxError as e:
            bad.append(f"{f}:{e.lineno}: {e.msg}")
        except OSError as e:
            bad.append(f"{f}: unreadable ({e})")
    if not files:
        return 2, "no tracked .py files found -- wrong root?"
    return (1 if bad else 0), (f"{len(files)} files parsed" if not bad else "\n".join(bad[:20]))


def check_lock(root: Path):
    rc, out, _ = run(["uv", "lock", "--check"], root / "tools" / "x4validate")
    return (0 if rc == 0 else 1), out.strip()[-400:]


def fetch_url(url: str, timeout: float = FETCH_TIMEOUT_S) -> bytes:
    """The raw file. Any failure raises (OSError family); the caller treats it as 'no copy from here'."""
    with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310 -- a fixed https URL
        if getattr(r, "status", 200) != 200:
            raise OSError(f"HTTP {r.status}")
        return r.read()


def _is_contract(src: bytes) -> bool:
    """A body that is not Python defining check_contract (a 404 page, a truncated read) is not the contract."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return False
    return any(isinstance(n, ast.FunctionDef) and n.name == "check_contract" for n in tree.body)


def _cache_path(root: Path, sha: str) -> Path | None:
    rc, out, _ = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], root)
    if rc or not out.strip():
        return None
    return Path(out.strip().splitlines()[-1]) / "prepush-gate" / "contracts" / (sha + ".py")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def contract_source(root: Path, sha: str) -> tuple[bytes | None, str]:
    """(the contract's bytes at `sha`, where they came from), or (None, every source tried and why it gave nothing)."""
    tried = []
    cache = _cache_path(root, sha)
    if cache is None:
        tried.append("cache: git could not name the git directory")
    elif cache.is_file():
        data = cache.read_bytes()
        if _is_contract(data):
            return data, "cache"
        tried.append(f"cache: {cache.name} is not the contract (ignored)")
    else:
        tried.append("cache: none for this commit")
    found, origin = None, ""
    clone = os.environ.get("X4_HARNESS_CLONE", "").strip()
    if clone:
        rc, out, _ = run(["git", "-C", clone, "show", f"{sha}:{CONTRACT_PATH}"], root)
        if rc == 0 and _is_contract(out.encode("utf-8")):
            found, origin = out.encode("utf-8"), "X4_HARNESS_CLONE"
        else:
            tried.append(f"X4_HARNESS_CLONE: no {CONTRACT_PATH} at {sha[:12]} there")
    else:
        tried.append("X4_HARNESS_CLONE: not set")
    if found is None:
        url = RAW_URL.format(repo=CONTRACT_REPO, sha=sha, path=CONTRACT_PATH)
        try:
            data = fetch_url(url, timeout=FETCH_TIMEOUT_S)
        except (OSError, ValueError) as e:            # URLError and timeouts are OSErrors
            tried.append(f"{url}: {type(e).__name__}: {e}")
        else:
            if _is_contract(data):
                found, origin = data, url
            else:
                tried.append(f"{url}: the body is not the contract ({len(data)} bytes)")
    if found is None:
        return None, "; ".join(tried)
    if cache is not None:
        try:
            _write_atomic(cache, found)
        except OSError as e:
            origin += f" (NOT cached: {e})"
    return found, origin


def check_contract(root: Path):
    refs = set()
    for wf in (root / ".github" / "workflows").glob("*.yml"):
        refs |= set(re.findall(r"modding-agent-harness/\.github/actions/ci-policy@([0-9a-f]{40})",
                               wf.read_text(encoding="utf-8")))
    if len(refs) != 1:
        return 2, f"expected ONE pinned ci-policy ref across the workflows, found {sorted(refs)}"
    ref = refs.pop()
    src, origin = contract_source(root, ref)
    if src is None:
        return 2, f"ci/contracts.py at {ref[:12]} could not be had from any source: {origin}"
    with tempfile.TemporaryDirectory() as d:
        mod_path = Path(d) / "contracts_pinned.py"
        mod_path.write_bytes(src)
        spec = importlib.util.spec_from_file_location("contracts_pinned", mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        try:
            mod.check_contract(str(root), "x4")
        except Exception as e:  # the contract's own refusal IS the finding
            return 1, f"contract {ref[:12]} refuses: {e}"
    return 0, f"contract {ref[:12]} holds (from {origin})"


def check_generated(root: Path):
    pkg = root / "tools" / "x4validate"
    msgs, worst = [], 0
    for script in ("scripts/gen-agent-trees.py", "scripts/gen-cli-reference.py"):
        rc, out, _ = run(["uv", "run", "--frozen", "python", script, "--check"], pkg)
        msgs.append(f"{script} --check rc={rc}" + ("" if rc == 0 else ": " + out.strip()[-300:]))
        worst = max(worst, 1 if rc else 0)
    return worst, "\n".join(msgs)


def check_meta(root: Path):
    pkg = root / "tools" / "x4validate"
    present = [t for t in META_TESTS if (pkg / t).is_file()]
    missing = sorted(set(META_TESTS) - set(present))
    if not present:
        return 2, "none of the meta tests exist -- wrong root?"
    rc, out, _ = run(["uv", "run", "--frozen", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                      *present], pkg)
    tail = "\n".join(out.strip().splitlines()[-6:])
    note = f" (missing, not run: {missing})" if missing else ""
    if rc not in (0, 1):
        return 2, f"pytest could not run (rc {rc}){note}\n{tail}"
    return rc, tail + note


CHECKS = [("python syntax", check_syntax), ("uv lock --check", check_lock),
          ("shared ci-policy contract", check_contract), ("generated files current", check_generated),
          ("repo-wide meta tests", check_meta)]


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1]).resolve()
    if not (root / ".github" / "workflows" / "ci.yml").is_file():
        print(f"preflight: {root} is not an X4 toolkit checkout (no .github/workflows/ci.yml)")
        return 2
    results = []
    for name, fn in CHECKS:
        t = time.monotonic()
        try:
            rc, msg = fn(root)
        except Exception as e:  # an instrument failure is a non-answer, never a pass
            rc, msg = 2, f"check crashed: {type(e).__name__}: {e}"
        results.append((name, rc))
        word = {0: "ok", 1: "FAIL", 2: "COULD NOT RUN"}[rc]
        print(f"== {name}: {word} ({time.monotonic() - t:.0f}s)")
        if msg:
            for line in msg.splitlines():
                print("   " + line)
    failed = [n for n, rc in results if rc == 1]
    unrun = [n for n, rc in results if rc == 2]
    print(f"preflight: {len(results) - len(failed) - len(unrun)}/{len(results)} ok"
          + (f"; FAILED: {failed}" if failed else "") + (f"; COULD NOT RUN: {unrun}" if unrun else ""))
    return 1 if failed else (2 if unrun else 0)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # silent-ok: a stream without reconfigure keeps its codec
    sys.exit(main(sys.argv))
