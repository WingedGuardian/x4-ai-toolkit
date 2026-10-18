r"""Every shipped CLI must be exercised by the QA sweep.

`gates/qa_sweep.py` is the "every CLI x subcommand" gate, and its cell list is
hand-written. A hand-written list of things to check has exactly one failure
mode, and it is this register's only failure mode: a new entry point is added,
nobody remembers the list, and the gate keeps reporting success over a smaller
population than it claims to cover.

That is not hypothetical here — `x4debug` shipped as the 9th console script on
2026-08-13 and qa_sweep could not see it, which is the third occurrence of this
shape in one day (the first two were inside the tool `x4debug` itself).

So the list is checked against `pyproject.toml`'s `[project.scripts]` rather than
against memory. Adding a CLI without a cell now fails here instead of silently
shrinking what the gate measures.
"""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _declared_clis() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["scripts"])


def test_every_console_script_has_at_least_one_qa_cell():
    from conftest import import_gate
    qa_sweep = import_gate("qa_sweep", module_level=False)

    covered = {c.tool for c in qa_sweep.CELLS}
    missing = _declared_clis() - covered
    assert not missing, (
        f"these CLIs ship but qa_sweep never runs them: {sorted(missing)}. "
        "The gate would report success over a population smaller than it claims.")


def test_no_qa_cell_names_a_cli_that_does_not_exist():
    """The mirror: a renamed CLI must not leave a cell silently testing nothing."""
    from conftest import import_gate
    qa_sweep = import_gate("qa_sweep", module_level=False)

    stale = {c.tool for c in qa_sweep.CELLS} - _declared_clis()
    assert not stale, f"qa_sweep has cells for CLIs that are not shipped: {sorted(stale)}"


def test_no_cli_module_declares_its_own_version():
    """One version, one source of truth.

    Every CLI reports `--version`. If a module defines its own `__version__`
    instead of importing the package's, the toolkit ships two answers to "what
    version is this?" — and the one the user sees depends on which command they
    happened to run. `x4debug` shipped saying 2.3.0 while the other eight said
    2.2.1, from the same working tree.

    Same shape as every other finding in the register: a second implementation of
    a fact that already had one.
    """
    import re

    pkg = ROOT / "x4validate"
    offenders = []
    for mod in sorted(pkg.glob("*.py")):
        if mod.name == "__init__.py":
            continue
        src = mod.read_text(encoding="utf-8")
        if re.search(r"^__version__\s*=\s*[\"']", src, re.M):
            offenders.append(mod.name)
    assert not offenders, (
        f"these modules declare their own __version__ instead of importing the "
        f"package's: {offenders}")


# --------------------------------------------------------------------------- #
# SUBCOMMAND-level coverage -- the same guard, one granularity down
# --------------------------------------------------------------------------- #
#
# ★ THE LESSON, and it is the whole reason these tests exist. The test above guards
# coverage at the CLI level: "every console script has AT LEAST ONE cell". It passed
# continuously while `x4live harvest` shipped uncovered, because `x4live` had ten other
# cells. The guard was real, it was correct, and it was at the WRONG GRANULARITY -- so
# the defect its own docstring describes recurred one level below it.
#
# MEASURED 2026-08-30 when the gap was found: 29 of 43 subcommands had a cell, while
# `qa_sweep`'s module docstring claimed "EVERY tool x EVERY subcommand".
#
# Do not read this as "now it is closed". Read it as: a completeness guard is only
# complete at the level it counts, and the next one down is where the next one hides.


def _qa():
    from conftest import import_gate
    return import_gate("qa_sweep", module_level=False)


def test_every_subcommand_is_either_SWEPT_or_JUSTIFIED():
    """The gate's own claim, enforced. An uncovered subcommand with no UNSWEPT reason
    is a finding, because otherwise a genuine omission and a considered exclusion are
    the same silence."""
    qa = _qa()
    findings, notes = qa.check_coverage()
    assert not findings, (
        "these subcommands are neither swept nor justified:\n  "
        + "\n  ".join(findings) + "\n" + "\n".join(notes))


def test_the_coverage_check_GOES_RED_when_a_cell_is_removed(monkeypatch):
    """A green that could not have gone red is decoration (CLAUDE.md #26). Removing the
    cell for a real subcommand MUST be reported."""
    qa = _qa()
    kept = [c for c in qa.CELLS if "harvest" not in c.argv]
    monkeypatch.setattr(qa, "CELLS", kept)
    findings, _ = qa.check_coverage()
    assert any("harvest" in f for f in findings), (
        "removing the harvest cell did not produce a finding, so the guard cannot fail")


def test_an_UNSWEPT_reason_counts_as_covered(monkeypatch):
    """The second clause, tested separately -- a guard that fires first shadows the one
    behind it. Without this, the check would demand cells for the minutes-long rebuilds
    and the honest answer would be to switch it off."""
    qa = _qa()
    kept = [c for c in qa.CELLS if "harvest" not in c.argv]
    monkeypatch.setattr(qa, "CELLS", kept)
    monkeypatch.setitem(qa.UNSWEPT, ("x4live", "harvest"), "test: justified not covered")
    findings, _ = qa.check_coverage()
    assert not any("harvest" in f for f in findings)


def test_a_CLI_whose_help_cannot_be_READ_is_UNKNOWN_not_COMPLETE(monkeypatch):
    """The third clause. A coverage check that folds "we could not ask" into "nothing
    missing" is the exact narrowing-without-announcement this register exists for."""
    qa = _qa()
    monkeypatch.setattr(qa, "_subcommands", lambda cli: None)
    findings, notes = qa.check_coverage()
    assert any("UNKNOWN" in n for n in notes), notes
    assert not findings, "an unreadable --help must not be reported as missing cells"


def test_every_UNSWEPT_entry_carries_a_REAL_reason():
    """An allow-list entry nobody can justify is how it stops being a decision and
    starts being an excuse -- the rule verify-port.py already applies to its ALLOW."""
    qa = _qa()
    assert qa.UNSWEPT, "the exclusion list is empty; that is suspicious, not clean"
    for (cli, sub), reason in qa.UNSWEPT.items():
        assert isinstance(reason, str) and len(reason) >= 30, (cli, sub, reason)
        assert not reason.lower().startswith(("todo", "tbd", "later", "n/a")), (
            f"{cli} {sub}: '{reason}' is a placeholder, not a reason")


def test_UNSWEPT_does_not_name_a_subcommand_that_no_longer_exists():
    """The mirror of the CLI-level test above: a renamed or removed subcommand must not
    leave a stale exclusion quietly excusing nothing."""
    qa = _qa()
    stale = []
    for (cli, sub) in qa.UNSWEPT:
        subs = qa._subcommands(cli)
        if subs is None:
            continue                      # could not ask; not evidence either way
        if sub not in subs:
            stale.append(f"{cli} {sub}")
    assert not stale, f"UNSWEPT excuses subcommands that do not exist: {stale}"


def test_UNSWEPT_never_names_a_subcommand_THAT_A_CELL_ALREADY_COVERS():
    """The direction the staleness test does not check, and the one that rotted.

    `check_coverage` counts `s in covered[cli]` OR `(cli, s) in UNSWEPT`, so an entry
    naming a subcommand that HAS a cell is a dead second gate: it silently absorbs the
    loss of that cell for ever after.

    MEASURED 2026-09-05: 9 of the 10 entries were in exactly that state. They had been
    added while this branch lacked Cell.slow / Cell.env / _sandbox_dir, with a note
    reading "This entry DIES AT MERGE" -- the merge happened, every one of the nine
    gained a working cell, and the entries stayed. A/B with three real cells deleted:
    without the dead entries 3 findings; with them, 0.

    Its sibling `test_the_coverage_check_GOES_RED_when_a_cell_is_removed` could not
    catch this either: it removes the `harvest` cell, the one subcommand NOT in
    UNSWEPT -- the only choice that could still go red.
    """
    qa = _qa()
    covered: dict[str, set[str]] = {}
    for c in qa.CELLS:                      # the gate's OWN rule, not a re-derivation
        covered.setdefault(c.tool, set()).update(c.argv)
    dead = sorted((cli, sub) for (cli, sub) in qa.UNSWEPT
                  if sub in covered.get(cli, set()))
    assert not dead, (
        "these UNSWEPT entries name subcommands a cell already runs, so each is a "
        "second gate that would absorb the loss of that cell:\n  "
        + "\n  ".join("%s %s" % p for p in dead)
        + "\n\nDelete the entry: a real check always beats a justified skip.")


def test_x4modlist_search_is_swept_only_through_its_offline_refusal():
    """`search` can reach the network, so its cell must be the keyless refusal and it must be
    able to go red: with the cell removed the coverage check reports `search`."""
    qa = _qa()
    cells = [c for c in qa.CELLS if c.tool == "x4modlist" and "search" in c.argv]
    assert len(cells) == 1, cells
    cell = cells[0]
    assert cell.expect == (2,) and cell.wants == "X4_NEXUS_KEY"
    assert cell.env["X4_NEXUS_KEY"] == ""
    assert Path(cell.env["X4_CONFIG"]).read_bytes() == b""    # no key can come from a file
    assert "--registry" in cell.argv                         # the sandbox, never the real one


def test_the_search_cell_runs_green_offline_and_goes_red_if_a_key_resolves(monkeypatch):
    qa = _qa()
    cell = next(c for c in qa.CELLS if c.tool == "x4modlist" and "search" in c.argv)
    assert qa.run(cell).status == "GREEN", (cell.status, cell.detail, cell.out[-400:])
    # The twin: the same cell with a key that resolves would get past the refusal. Prove the
    # cell is judged on the exit code by pointing it at a different expectation.
    monkeypatch.setattr(cell, "status", "")
    monkeypatch.setattr(cell, "expect", (0,))
    assert qa.run(cell).status == "RED"


def test_removing_the_search_cell_is_reported_as_a_finding(monkeypatch):
    qa = _qa()
    kept = [c for c in qa.CELLS if not (c.tool == "x4modlist" and "search" in c.argv)]
    monkeypatch.setattr(qa, "CELLS", kept)
    findings, _ = qa.check_coverage()
    assert any("x4modlist search" in f for f in findings), findings
