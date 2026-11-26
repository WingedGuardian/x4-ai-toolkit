"""X4-NOTES.md stays the user's file through the shared upgrader (core `notes_files`, 4.3.1).

Since core a84098c, the core names no game's notes file: a toolkit passes its own as
`notes_files`. Without it, `X4-NOTES.md` would be an ordinary file to the upgrader, and an
upgrade candidate shipping that name could overwrite the user's notes.
"""
import hashlib
import json
import shutil
import subprocess
import sys

from codex_testlib import REPO


def _toolkit(tmp_path):
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    return root


def _harness(root, *args):
    return subprocess.run([sys.executable, "-B", str(root / "tools/agent-harness.py"), *args],
                          capture_output=True, timeout=45)


def _stage(tmp_path, root, shipped):
    installed, source, bundle = tmp_path / "installed", tmp_path / "source", tmp_path / "review"
    installed.mkdir()
    source.mkdir()
    (installed / "X4-NOTES.md").write_bytes(b"my private notes")
    (installed / "README.md").write_bytes(b"old readme")
    files = {}
    for name, data in shipped.items():
        (source / name).write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    inventory = tmp_path / "candidate.json"
    inventory.write_text(json.dumps({"v": 1, "files": files}))
    staged = _harness(root, "upgrade-stage", "--installed-root", str(installed), "--source", str(source),
                      "--candidate", str(inventory), "--destination", str(bundle))
    assert staged.returncode == 0, staged.stderr
    return installed, bundle


def _apply(tmp_path, root, installed, bundle, choices):
    path = tmp_path / "choices.json"
    path.write_text(json.dumps(choices))
    review = hashlib.sha256((bundle / "complete.json").read_bytes()).hexdigest()
    return _harness(root, "upgrade-apply", "--installed-root", str(installed), "--bundle", str(bundle),
                    "--review-sha256", review, "--choices", str(path), "--quiescent")


def test_a_candidate_shipping_x4_notes_never_overwrites_the_users_file(tmp_path):
    root = _toolkit(tmp_path)
    installed, bundle = _stage(tmp_path, root, {"X4-NOTES.md": b"shipped notes", "README.md": b"new readme"})
    # Every OTHER collision is resolved, so the notes file is the only thing that can refuse.
    applied = _apply(tmp_path, root, installed, bundle, {"X4-NOTES.md": "candidate", "README.md": "candidate"})
    assert (installed / "X4-NOTES.md").read_bytes() == b"my private notes", applied.stdout
    assert applied.returncode != 0, "choosing the candidate for the notes file must be refused"
    assert b"X4-NOTES.md" in applied.stdout, applied.stdout      # refused for the notes, not something else


def test_TWIN_the_same_apply_without_the_notes_choice_goes_through(tmp_path):
    root = _toolkit(tmp_path)
    installed, bundle = _stage(tmp_path, root, {"X4-NOTES.md": b"shipped notes", "README.md": b"new readme"})
    applied = _apply(tmp_path, root, installed, bundle, {"X4-NOTES.md": "keep", "README.md": "candidate"})
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (installed / "README.md").read_bytes() == b"new readme"
    assert (installed / "X4-NOTES.md").read_bytes() == b"my private notes"


def test_editing_x4_notes_during_a_review_is_not_a_refusal(tmp_path):
    root = _toolkit(tmp_path)
    installed, bundle = _stage(tmp_path, root, {"README.md": b"new readme"})
    (installed / "X4-NOTES.md").write_bytes(b"my private notes, edited while the review was open")
    applied = _apply(tmp_path, root, installed, bundle, {"README.md": "candidate"})
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (installed / "README.md").read_bytes() == b"new readme"
    assert (installed / "X4-NOTES.md").read_bytes() == b"my private notes, edited while the review was open"
