"""gates/routing_coverage.py -- is every CLI named in the routing table?

WHAT THIS GATE CLAIMS, AND WHAT IT MUST NOT. It enforces RESIDENT ROUTING: a cold
session should be able to route to any tool without searching. It does NOT claim a
missing row makes a tool undiscoverable -- MEASURED 2026-09-12, a cold agent found
an unrouted CLI and used it correctly in 193 s via KNOWLEDGEBASE.md and the
toolkit README, before its row existed. The cost of a missing row is SEARCH
(39 tool calls, ~74k tokens), not unreachability. Stating the stronger claim would
re-introduce a population error this gate was born from.
"""

from __future__ import annotations

import pathlib

import pytest

from conftest import import_gate

rc_ = import_gate("routing_coverage")

TABLE = """\
| Question shape | Use | Not |
|---|---|---|
| do selectors resolve? | **x4validate** | eyeballing |
| what did the ENGINE say? | **x4debug triage** | `grep \\| sort \\| uniq -c` |
"""


def test_use_column_is_extracted_from_a_table():
    assert rc_.routed_clis(TABLE) == {"x4validate", "x4debug"}


def test_an_escaped_pipe_is_data_not_a_delimiter():
    """The row using the CORRECT markdown escape must not read as malformed, and
    its third cell must not leak into the Use column. A naive split on every pipe
    reads 5 cells where there are 3 -- MEASURED on the real file 2026-09-12."""
    rows = rc_.table_rows(TABLE)
    assert len(rows) == 2, rows
    assert [len(r) for r in rows] == [3, 3], rows
    assert rows[1][1].strip() == "**x4debug triage**"


def test_a_CRLF_table_leaves_no_carriage_return_in_any_cell():
    """The real file is CRLF, and bytes-then-decode preserves that.

    The first version of this test compared only routed_clis() between LF and CRLF
    input, and a mutant that REMOVED the normalisation survived it -- a stray CR
    lands in the last cell, where routed_clis never looks. Asserting on the cells
    is what makes this able to go red.
    """
    crlf = TABLE.replace(chr(10), chr(13) + chr(10))
    rows = rc_.table_rows(crlf)
    assert rows == rc_.table_rows(TABLE), rows
    stray = [c for r in rows for c in r if chr(13) in c]
    assert not stray, stray


def test_a_cli_only_in_the_Not_column_does_not_count_as_routed():
    t = TABLE + "| find a file? | **Glob** | **x4diff** is not for this |\n"
    assert "x4diff" not in rc_.routed_clis(t)


def test_missing_returns_the_unrouted_clis_named():
    missing = rc_.missing(["x4validate", "x4debug", "x4live"], TABLE)
    assert missing == ["x4live"]


def test_an_unparseable_table_is_a_REFUSAL_not_an_empty_answer():
    """Zero rows would make every CLI read as unrouted, which is a different
    finding from 'I could not read the table'."""
    with pytest.raises(rc_.TableUnreadable):
        rc_.routed_clis("no table here at all\njust prose\n")


def test_the_live_file_routes_every_cli():
    """END TO END against the real CLAUDE.md files and the real roster."""
    if rc_.claude_md_paths() == []:
        pytest.skip("no CLAUDE.md resolves here (no repo-root copy and no game root)")
    assert rc_.main() == 0


FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "claude_md_pre_routing_rows.md"


def test_it_goes_RED_against_the_pre_landing_file():
    """FALSIFIABILITY FROM HISTORY, on every clone. The first version pinned a commit
    in the author's PRIVATE game-root repo, so it silently skipped on every other
    machine forever -- "reproducible" on exactly one. The pre-landing file is now a
    committed fixture: no git, no skip."""
    before = FIXTURE.read_bytes().decode("utf-8")
    assert len(before) > 30_000, "fixture truncated"
    missing = rc_.missing(rc_.roster(), before)
    assert sorted(missing) == ["x4diff", "x4live", "x4modlist", "x4save", "x4stats"], missing


def test_a_CLI_name_inside_a_PATH_is_not_a_route():
    """`x4validate/_*.py` names a directory, not the tool. The review's case exactly."""
    t = TABLE.replace("| do selectors resolve? | **x4validate** | eyeballing |\n", "")
    t += "| mutation-test code? | **`gates/mutation_probe.py`**, but ONLY `x4validate/_*.py` | x |\n"
    assert "x4validate" not in rc_.routed_clis(t)


def test_a_bold_or_backticked_tool_name_still_routes():
    t = TABLE + "| a? | **`x4save info`** | x |\n| b? | `x4diff` and x4stats | y |\n"
    assert {"x4save", "x4diff", "x4stats"} <= rc_.routed_clis(t)


# --- Plan 2 lane A, Task 6: AGENTS.md carries the same shared core, so it is in the population.

def test_the_shipped_AGENTS_md_is_in_the_population():
    labels = {label for label, _ in rc_.claude_md_paths()}
    if not (rc_.REPO_ROOT / "AGENTS.md").is_file():
        pytest.skip("no AGENTS.md at the repo root (not the shipped layout)")
    assert "shipped AGENTS.md" in labels, labels


def test_TWIN_an_AGENTS_md_missing_a_row_turns_the_gate_red(tmp_path, monkeypatch, capsys):
    """Twin of the live run: CLAUDE.md fully routed, AGENTS.md lacking x4live -> rc 1 naming it."""
    from _layout import runtime_instructions
    full = runtime_instructions(rc_.REPO_ROOT)[0].read_bytes()
    good = tmp_path / "CLAUDE.md"
    good.write_bytes(full)
    bad = tmp_path / "AGENTS.md"
    bad.write_bytes(b"\n".join(l for l in full.split(b"\n") if b"**x4live**" not in l))
    monkeypatch.setattr(rc_, "claude_md_paths",
                        lambda: [("shipped (repo root)", good), ("shipped AGENTS.md", bad)])
    assert rc_.main() == 1
    err = capsys.readouterr().err
    assert "UNROUTED in shipped AGENTS.md" in err and "x4live" in err, err
