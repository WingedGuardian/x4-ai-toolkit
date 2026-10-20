"""Prior-run removal is recoverable; a failed recycler cannot trigger a delete."""
from pathlib import Path
import os

import pytest

from x4recycle import recycle_or_raise


def test_recycles_odd_names_and_keeps_the_bytes(tmp_path):
    victim = tmp_path / "old (tree) [1] ' &"
    victim.mkdir()
    (victim / "precious").write_bytes(b"original bytes")
    recycle_or_raise([victim])
    assert not victim.exists()
    # Literal names matter: brackets in a path are not a wildcard contract.
    saved = [p for p in Path(os.environ["X4_RECYCLE_TEST_BIN"]).iterdir()
             if p.name.endswith("-" + victim.name)]
    assert len(saved) == 1
    assert (saved[0] / "precious").read_bytes() == b"original bytes"


@pytest.mark.parametrize("response", ["declined", None, "missing"])
def test_refused_or_false_success_retains_original(tmp_path, response):
    victim = tmp_path / "old.bin"
    victim.write_bytes(b"original")
    def fake(paths):
        return {} if response == "missing" else {paths[0]: response}
    with pytest.raises(OSError, match="refusal"):
        recycle_or_raise([victim], recycler=fake)
    assert victim.read_bytes() == b"original"


def test_absent_path_does_not_call_a_backend(tmp_path):
    def unexpected(paths):
        raise AssertionError("nothing to recycle")
    recycle_or_raise([tmp_path / "absent"], recycler=unexpected)


def test_unconfigured_test_never_calls_native_recycler(tmp_path, monkeypatch):
    victim = tmp_path / "keep"
    victim.write_bytes(b"keep")
    monkeypatch.delenv("X4_RECYCLE_TEST_ROOT")
    with pytest.raises(AssertionError, match="inject a fake"):
        recycle_or_raise([victim])
    assert victim.read_bytes() == b"keep"


def test_a_redirected_root_is_refused_without_touching_its_target(tmp_path):
    from test_deploy_mod import _symlink_or_skip
    target = tmp_path / "outside"
    target.mkdir()
    (target / "precious").write_bytes(b"keep")
    link = tmp_path / "linked"
    _symlink_or_skip(link, target, is_dir=True)
    with pytest.raises(OSError, match="link"):
        recycle_or_raise([link])
    assert (target / "precious").read_bytes() == b"keep"


def test_home_alias_is_refused_before_calling_any_backend(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "sub").mkdir(parents=True)
    (home / "precious").write_bytes(b"keep")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    def unexpected(paths):
        raise AssertionError("the home alias reached the recycler")
    with pytest.raises(OSError, match="root or home"):
        recycle_or_raise([home / "sub/.."], recycler=unexpected)
    assert (home / "precious").read_bytes() == b"keep"
