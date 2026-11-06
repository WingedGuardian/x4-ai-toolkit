"""4.1.1: a module of real installs must not hold every test's tree until the session ends.

`drop_tmp_path_on_pass` (conftest) removes a PASSING test's own tmp_path; a failing test's
tree is kept, and nothing outside pytest's base temp is ever touched. Everything here works
in tmp_path; nothing real is deleted."""
from __future__ import annotations

import os
import stat

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
