"""tools/modding_harness/ is VENDORED and hash-pinned by tools/modding-harness.lock.json.

Nothing checked the pin: an edit to a vendored file, or a new file dropped beside them, would
ship as if it were the upstream revision the lock names. Every listed file must exist with its
sha256, and nothing may sit in the directory that the lock does not list -- on disk (what an
install ships) and, in a checkout, in git (what a release archives).
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from _layout import require_repo

REPO = Path(__file__).resolve().parents[3]
LOCK = "tools/modding-harness.lock.json"
VENDORED = "tools/modding_harness"


def _ignored(rel: str) -> bool:
    return "__pycache__" in rel.split("/") or rel.endswith((".pyc", ".pyo"))


def lock_problems(root: Path) -> list[str]:
    lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    pinned = lock["files"]
    problems = [f"lock entry outside {VENDORED}/: {rel}" for rel in pinned
                if not rel.startswith(VENDORED + "/")]
    for rel, digest in sorted(pinned.items()):
        path = root / rel
        if not path.is_file():
            problems.append(f"MISSING  {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append(f"CHANGED  {rel}")
    on_disk = {p.relative_to(root).as_posix() for p in (root / VENDORED).rglob("*") if p.is_file()}
    problems += [f"UNLISTED {rel}" for rel in sorted(on_disk - set(pinned)) if not _ignored(rel)]
    return problems


def test_every_vendored_file_matches_its_pin_and_nothing_is_unlisted():
    pinned = json.loads((REPO / LOCK).read_text(encoding="utf-8"))["files"]
    assert pinned, "the lock lists no files: nothing would be checked"
    assert lock_problems(REPO) == []


def test_git_tracks_exactly_the_pinned_files():
    require_repo(".git", why="the tracked set is a git question")
    pinned = set(json.loads((REPO / LOCK).read_text(encoding="utf-8"))["files"])
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", VENDORED],
                       capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    tracked = {p for p in r.stdout.decode("utf-8").split("\0") if p}
    assert tracked, "git listed nothing under the vendored directory"
    assert tracked == pinned, {"unlisted": sorted(tracked - pinned), "untracked": sorted(pinned - tracked)}


def test_TWIN_each_clause_can_fail(tmp_path):
    """A changed byte, a missing file and an unlisted file are each reported."""
    (tmp_path / "tools").mkdir()
    shutil.copytree(REPO / VENDORED, tmp_path / VENDORED, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / LOCK, tmp_path / LOCK)
    assert lock_problems(tmp_path) == []
    pinned = sorted(json.loads((REPO / LOCK).read_text(encoding="utf-8"))["files"])
    changed, missing = tmp_path / pinned[0], tmp_path / pinned[1]
    changed.write_bytes(changed.read_bytes() + b"\n# local edit\n")
    missing.unlink()
    (tmp_path / VENDORED / "planted.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / VENDORED / "__pycache__").mkdir(exist_ok=True)
    (tmp_path / VENDORED / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"ignored")
    assert lock_problems(tmp_path) == [f"CHANGED  {pinned[0]}", f"MISSING  {pinned[1]}",
                                       f"UNLISTED {VENDORED}/planted.py"]
