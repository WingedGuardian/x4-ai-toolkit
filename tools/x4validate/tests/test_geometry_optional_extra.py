"""x4similar without the optional `geometry` extra (4.0.1 review).

test_geometry*.py skip whole when the extra is missing, so nothing pinned what a user without
it sees. Plain x4similar must not need numpy, and --geometry must refuse with an install hint
(rc 2), never a traceback. The four libraries are hidden from import here, so this runs the
same with or without the extra installed.
"""
import sys

import pytest

from x4validate import _similarity

EXTRA = ("numpy", "scipy", "trimesh", "rtree")


@pytest.fixture
def without_extra(monkeypatch):
    for name in EXTRA:
        monkeypatch.setitem(sys.modules, name, None)          # `import numpy` -> ImportError
    for name in list(sys.modules):
        if name.startswith("x4validate._geometry_"):          # not _geometrycli: stdlib only
            monkeypatch.delitem(sys.modules, name)


def test_geometry_without_the_extra_refuses_with_an_install_hint(without_extra, tmp_path, capsys):
    rc = _similarity.main(["--reference", str(tmp_path), "--ext-dir", str(tmp_path),
                           "--geometry", "assets/ship_macro.xml"])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "geometry unavailable" in err and "uv sync --extra geometry" in err, err


def test_TWIN_help_needs_no_extra(without_extra, capsys):
    with pytest.raises(SystemExit) as exit_:
        _similarity.main(["--help"])
    assert exit_.value.code == 0
    assert "--geometry" in capsys.readouterr().out
