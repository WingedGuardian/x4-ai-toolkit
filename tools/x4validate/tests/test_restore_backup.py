"""Restore retains the newer destination, and failed preservation cannot overwrite it."""
import importlib.util
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/restore-from-backup.py"
spec = importlib.util.spec_from_file_location("restore_backup", SCRIPT)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


@pytest.mark.parametrize("existing", [True, False])
def test_restore_retains_current_bytes_and_verifies_old_bytes(tmp_path, existing):
    backups = tmp_path / "backups (old) [1] ' &"
    backups.mkdir()
    name = "old [snapshot] '&.txt"
    (backups / name).write_bytes(b"old")
    dest = tmp_path / "current [file] '&.txt"
    if existing:
        dest.write_bytes(b"new")
    retained = restore.restore(backups, name, dest)
    assert dest.read_bytes() == b"old"
    assert (backups / name).read_bytes() == b"old"
    if existing:
        assert retained.read_bytes() == b"new"
    else:
        assert retained is None
    assert not list(tmp_path.glob(".x4-restore-*"))


def test_failed_preservation_never_replaces_current_bytes(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "old").write_bytes(b"old")
    dest = tmp_path / "current"
    dest.write_bytes(b"new")
    monkeypatch.setattr(restore.shutil, "copyfileobj", lambda src, dst: dst.write(b"corrupt"))
    with pytest.raises(OSError, match="preservation failed"):
        restore.restore(backups, "old", dest)
    assert dest.read_bytes() == b"new"


def test_backup_lookup_is_literal_and_cannot_escape(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "snap1").write_bytes(b"old")
    dest = tmp_path / "current"
    dest.write_bytes(b"new")
    for name in ("snap*", "../outside"):
        with pytest.raises((OSError, ValueError)):
            restore.restore(backups, name, dest)
    assert dest.read_bytes() == b"new"


def test_documented_shell_entry_restores_literal_odd_names(tmp_path):
    import os, subprocess, sys
    from test_install_over_existing import _bash
    bash = _bash()
    if not bash:
        pytest.skip("Git Bash unavailable: shell entry not exercised")
    backups = tmp_path / ".claude/backups"
    backups.mkdir(parents=True)
    name = "old [snapshot] '&.txt"
    (backups / name).write_bytes(b"old")
    dest = tmp_path / "current [file] '&.txt"
    dest.write_bytes(b"new")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp_path.as_posix(), RESTORE="1", X4_PYTHON=sys.executable)
    result = subprocess.run([bash, str(SCRIPT.with_suffix('.sh')), name, dest.as_posix()],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preserved and verified" in result.stdout
    assert dest.read_bytes() == b"old"
    assert next(backups.glob("before-restore__*")).read_bytes() == b"new"
