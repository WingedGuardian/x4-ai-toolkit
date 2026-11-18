"""The qa_sweep cells for `x4live automation`, `x4live equipment` and `x4save census`.

`test_qa_coverage` found the three with no cell (2026-11-14). Each new cell is an OFFLINE refusal: the tool
refuses on its arguments before it opens the live pipe, a journal, or any save, so the sweep needs no game
and touches nothing. Each names its stream (`wants_on="stderr"`), so a refusal printed where a stdout
reader would take it as data does not count.

The real cells are run here, not re-typed, so a reworded refusal or a refusal that moved behind the pipe
turns this red. The stream clause gets its own twin with a stubbed process.
"""
from __future__ import annotations

import subprocess

import pytest

LABELS = {
    ("x4save", "census refuses an unknown --by key"),
    ("x4live", "equipment refuses a malformed ship id"),
    ("x4live", "automation refuses a write verb through query"),
    # 4.3: `x4live save` refuses a wildcard slot before the save folder or the pipe.
    ("x4live", "save refuses a wildcard slot"),
}


def _qa():
    from conftest import import_gate
    return import_gate("qa_sweep", module_level=False)


def _cells(qa):
    return [c for c in qa.CELLS if (c.tool, c.label) in LABELS]


def test_the_offline_refusal_cells_exist_and_each_names_stderr():
    cells = _cells(_qa())
    assert {(c.tool, c.label) for c in cells} == LABELS
    for c in cells:
        assert c.expect == (2,) and c.wants and c.wants.startswith("error: ") and c.wants_on == "stderr"


@pytest.mark.parametrize("label", sorted(l for _t, l in LABELS))
def test_each_cell_is_GREEN_on_its_offline_refusal(label):
    qa = _qa()
    cell = next(c for c in _cells(qa) if c.label == label)
    qa.run(cell)
    assert cell.status == "GREEN", (cell.status, cell.detail, cell.out[-600:])


def _stub(monkeypatch, qa, stdout, stderr, rc=2):
    monkeypatch.setattr(qa.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0] if a else [], rc, stdout=stdout, stderr=stderr))


def test_the_text_on_the_WRONG_stream_is_YELLOW(monkeypatch):
    """Twin of the stream clause: the same text on stdout must not satisfy wants_on='stderr'."""
    qa = _qa()
    _stub(monkeypatch, qa, stdout="error: refused\n", stderr="")
    cell = qa.Cell("x4live", "t", ["x"], expect=(2,), findings_ok=True, wants="error: refused",
                   wants_on="stderr")
    qa.run(cell)
    assert cell.status == "YELLOW" and "stderr" in cell.detail


def test_the_text_on_the_RIGHT_stream_is_GREEN(monkeypatch):
    qa = _qa()
    _stub(monkeypatch, qa, stdout="", stderr="error: refused\n")
    cell = qa.Cell("x4live", "t", ["x"], expect=(2,), findings_ok=True, wants="error: refused",
                   wants_on="stderr")
    qa.run(cell)
    assert cell.status == "GREEN"


def test_the_default_still_matches_both_streams(monkeypatch):
    """Every existing cell keeps its combined-output behaviour."""
    qa = _qa()
    _stub(monkeypatch, qa, stdout="error: refused\n", stderr="", rc=0)
    cell = qa.Cell("x4live", "t", ["x"], wants="error: refused")
    qa.run(cell)
    assert cell.status == "GREEN"
