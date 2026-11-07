"""4.1.1: a module of real installs must not hold every test's tree until the session ends.

`drop_tmp_path_on_pass` (conftest) removes a PASSING test's own tmp_path; a failing test's
tree is kept, and nothing outside pytest's base temp is ever touched. Everything here works
in tmp_path; nothing real is deleted."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from conftest import drop_tmp_if_passed

_SEEN = {}


@pytest.mark.usefixtures("drop_tmp_path_on_pass")
def test_a_passing_test_writes_a_tree(tmp_path):
    (tmp_path / "toolkit" / "x").mkdir(parents=True)
    locked = tmp_path / "toolkit" / "x" / "x4-paths.env"
    locked.write_text("SENTINEL=1\n", encoding="utf-8")
    os.chmod(locked, stat.S_IREAD)                    # the locked-config fixtures do this
    _SEEN["tree"] = tmp_path
    fake_bin = os.environ.get("X4_RECYCLE_TEST_BIN")
    if fake_bin:                                      # what an installer "recycles" mid-test
        os.makedirs(fake_bin, exist_ok=True)
        name = "%s-.venv" % os.urandom(8).hex()
        os.makedirs(os.path.join(fake_bin, name, "Lib"))
        _SEEN["bin_entry"] = name


def test_its_tree_is_gone_once_it_passed():
    tree = _SEEN.get("tree")
    if tree is None:
        pytest.skip("the writer test did not run first (selected alone)")
    assert not tree.exists(), tree


def test_TWIN_a_failed_test_keeps_its_tree(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    tmp = tmp_path_factory.mktemp("failed-case")
    (tmp / "evidence.txt").write_text("kept", encoding="utf-8")
    assert drop_tmp_if_passed(3, 4, base, tmp) is False
    assert (tmp / "evidence.txt").is_file()
    assert drop_tmp_if_passed(4, 4, base, tmp) is True        # the same tree, once it passed
    assert not tmp.exists()


def test_TWIN_nothing_outside_the_base_temp_is_removed(tmp_path_factory, tmp_path):
    base = tmp_path_factory.mktemp("other-base")
    outside = tmp_path / "not-under-that-base"
    outside.mkdir()
    assert drop_tmp_if_passed(0, 0, base, outside) is False
    assert outside.is_dir()
    assert drop_tmp_if_passed(0, 0, base, base) is False      # the base itself is not "under" it
    assert base.is_dir()


def test_a_link_is_removed_as_a_link_never_followed(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    target = tmp_path_factory.mktemp("link-target")
    (target / "keep.txt").write_text("kept", encoding="utf-8")
    holder = tmp_path_factory.mktemp("link-holder")
    link = holder / "lnk"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        try:
            import _winapi                     # Windows without the symlink privilege: a junction
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError):
            pytest.skip("cannot create a symlink or junction here: %s" % exc)
    assert drop_tmp_if_passed(0, 0, base, link) is True
    assert not os.path.lexists(link)
    assert (target / "keep.txt").is_file()


def test_the_fixture_drops_what_a_passing_test_put_in_the_fake_bin():
    """Runs after the writer above in file order; the bin is the session's (x4pytest)."""
    fake_bin = os.environ.get("X4_RECYCLE_TEST_BIN")
    if not fake_bin or "bin_entry" not in _SEEN:
        pytest.skip("no session fake bin, or the writer did not run first")
    assert not os.path.lexists(os.path.join(fake_bin, _SEEN["bin_entry"]))


def test_a_locked_file_during_the_delete_warns_and_leaves_the_folder(tmp_path_factory, monkeypatch):
    """4.1.1 review: a Windows file lock during rmtree raised in teardown and turned a PASS into an ERROR."""
    import conftest
    base = tmp_path_factory.getbasetemp()
    tmp = tmp_path_factory.mktemp("locked-case")
    (tmp / "held.bin").write_bytes(b"x")

    def locked(_path):
        raise PermissionError(32, "being used by another process", str(tmp / "held.bin"))
    monkeypatch.setattr(conftest, "_rmtree_writable", locked)
    with pytest.warns(UserWarning, match="left in place"):
        assert drop_tmp_if_passed(0, 0, base, tmp) is False
    assert (tmp / "held.bin").is_file()


def test_a_failed_tests_folder_carries_the_kept_failure_marker(tmp_path_factory):
    """x4housekeep recycles (never deletes) a session folder holding one; the names must agree."""
    import importlib.util
    import conftest
    spec = importlib.util.spec_from_file_location("hk_for_marker", Path(conftest.__file__).resolve().parents[3]
                                                  / "scripts" / "x4housekeep.py")
    assert conftest.KEPT_FAILURE == ".x4-kept-failure"
    src = spec.origin and Path(spec.origin).read_text(encoding="utf-8")
    assert 'KEPT_FAILURE = ".x4-kept-failure"' in src
    tmp = tmp_path_factory.mktemp("failed-case-marker")
    conftest.mark_kept_failure(tmp)
    assert (tmp / conftest.KEPT_FAILURE).is_file()


def test_a_real_failing_test_gets_the_marker_and_a_passing_one_is_dropped(tmp_path):
    """End to end: a child pytest with THIS conftest as a plugin; one test fails, one passes. Only tmp_path is written."""
    import subprocess
    import sys
    here = Path(__file__).resolve().parent
    mod = tmp_path / "case_mod"
    mod.mkdir()
    (mod / "test_case.py").write_text(
        "import pytest\n"
        "@pytest.mark.usefixtures('drop_tmp_path_on_pass')\n"
        "def test_fails(tmp_path):\n    (tmp_path / 'evidence.txt').write_text('e')\n    assert False\n"
        "@pytest.mark.usefixtures('drop_tmp_path_on_pass')\n"
        "def test_passes(tmp_path):\n    (tmp_path / 'scratch.txt').write_text('s')\n", encoding="utf-8")
    base = tmp_path / "child-base"
    env = dict(os.environ, PYTHONPATH=str(here) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
                        "--basetemp", str(base), "--rootdir", str(mod), str(mod / "test_case.py")],
                       capture_output=True, text=True, env=env, cwd=str(mod), timeout=300)
    assert "1 failed, 1 passed" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]
    failed = [d for d in base.iterdir() if d.name.startswith("test_fails")]
    passed = [d for d in base.iterdir() if d.name.startswith("test_passes")]
    assert len(failed) == 1 and (failed[0] / ".x4-kept-failure").is_file() and (failed[0] / "evidence.txt").is_file()
    assert passed == []
    # 4.1.1 review B1: the session's base folder is marked too, so evidence outside a tmp_path is kept with it
    assert (base / ".x4-kept-failure").is_file()


def test_a_failing_test_with_only_tmp_path_factory_marks_the_session(tmp_path):
    """4.1.1 review B1: a failing test that wrote its evidence through tmp_path_factory had no tmp_path to mark."""
    import subprocess
    import sys
    here = Path(__file__).resolve().parent
    mod = tmp_path / "case_mod"
    mod.mkdir()
    (mod / "test_case.py").write_text(
        "def test_fails(tmp_path_factory):\n    (tmp_path_factory.mktemp('made') / 'e.txt').write_text('e')\n"
        "    assert False\n", encoding="utf-8")
    base = tmp_path / "child-base"
    env = dict(os.environ, PYTHONPATH=str(here) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
                        "--basetemp", str(base), "--rootdir", str(mod), str(mod / "test_case.py")],
                       capture_output=True, text=True, env=env, cwd=str(mod), timeout=300)
    assert "1 failed" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]
    assert (base / ".x4-kept-failure").is_file()
