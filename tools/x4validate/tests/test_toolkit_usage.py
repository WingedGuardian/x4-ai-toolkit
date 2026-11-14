"""`gates/toolkit_usage.py` -- the parts that were wrong on its first run.

Both defects it shipped with were the same shape as the thing it audits: an
instrument answering an ADJACENT question in a well-formed way.

  1. Its transcript fallback picked "the largest project directory", which on the
     reference machine was an unrelated game. It scanned 39 of that project's
     transcripts and reported a confident **"0 invoked, 41 never invoked"**.
  2. `qa_sweep`'s `Cell.argv[0]` is a SUBCOMMAND only when the CLI has
     subparsers; for a single-command CLI it is a mod-path ARGUMENT. Keying
     everything on `(tool, argv[0])` made `x4validate`, `x4diff` and `x4similar`
     unmatchable, so it reported three tools as untested that qa_sweep exercises
     9, 3 and 2 times.

One twin per clause, because each guard shadows the ones behind it.
"""

from __future__ import annotations

import json

import pytest

from conftest import import_gate

tu = import_gate("toolkit_usage")


class _Cell:
    """The fields Coverage reads off `qa_sweep.Cell`."""

    def __init__(self, tool: str, argv: list[str], label: str = "c") -> None:
        self.tool, self.argv, self.label = tool, argv, label


#: the live surface Coverage searches argv against
SUBS = {"x4debug": ["triage", "crosscheck", "baseline"],
        "x4modlist": ["dashboard", "verify", "ingest"],
        "x4effective": ["build", "dump"]}


# --------------------------------------------------------------------------
# an INVOCATION is not a MENTION
# --------------------------------------------------------------------------
def test_a_cd_into_the_tool_directory_is_not_an_invocation():
    """The defect that inflated the hand measurement 4.8x (6,106 vs 1,263)."""
    rx = tu.invocation_re("x4validate", None)
    assert not rx.search("cd tools/x4validate && uv run pytest -q")
    assert not rx.search('cd "C:/x/tools/x4validate-tooling" && ls')


def test_a_real_invocation_is_recognised_in_each_position():
    rx = tu.invocation_re("x4validate", None)
    for cmd in ("uv run x4validate dev/mod",
                "cd /x && uv run x4validate dev/mod",
                "x4validate dev/mod",
                "foo; x4validate dev/mod"):
        assert rx.search(cmd), cmd


def test_a_subcommand_invocation_does_not_match_a_sibling():
    build = tu.invocation_re("x4effective", "build")
    assert build.search("uv run x4effective build")
    assert not build.search("uv run x4effective dump md/setup.xml")
    assert not build.search("uv run x4effective builder")   # \b, not a prefix


# --------------------------------------------------------------------------
# qa_sweep coverage -- one clause per CLI shape
# --------------------------------------------------------------------------
def test_single_command_cli_is_covered_by_any_cell_for_that_tool():
    """argv[0] there is a mod path; keying on the pair can never match."""
    cov = tu.Coverage([_Cell("x4validate", ["dev/some_mod", "--tier", "b"])], SUBS)
    assert cov.covers("x4validate", "")


def test_single_command_coverage_does_not_leak_to_another_tool():
    cov = tu.Coverage([_Cell("x4validate", ["dev/some_mod"])], SUBS)
    assert not cov.covers("x4diff", "")


def test_subcommand_cli_is_covered_only_for_the_subcommand_exercised():
    cov = tu.Coverage([_Cell("x4debug", ["triage"])], SUBS)
    assert cov.covers("x4debug", "triage")
    assert not cov.covers("x4debug", "baseline")


def test_a_cell_with_no_argv_does_not_claim_a_subcommand():
    cov = tu.Coverage([_Cell("x4debug", [])], SUBS)
    assert not cov.covers("x4debug", "triage")


# --------------------------------------------------------------------------
# the second guard: zero invocations across the WHOLE surface is not credible
# --------------------------------------------------------------------------
def test_a_surface_with_one_invocation_is_credible():
    assert tu.surface_was_exercised({"a": {"invoked": 0}, "b": {"invoked": 1}})


def test_a_surface_with_no_invocation_anywhere_is_not_credible():
    """The symptom of scanning some other project's transcripts."""
    assert not tu.surface_was_exercised({"a": {"invoked": 0}, "b": {"invoked": 0}})


def test_an_empty_surface_is_not_credible_either():
    """`any()` over nothing is False, but `all()` would be vacuously True --
    pinned so a future rewrite cannot reintroduce the vacuous-pass shape."""
    assert not tu.surface_was_exercised({})


# --------------------------------------------------------------------------
# scanning refuses rather than reporting a confident zero
# --------------------------------------------------------------------------
def test_a_directory_with_no_transcripts_refuses(tmp_path):
    with pytest.raises(SystemExit) as e:
        tu.scan(tmp_path)
    assert e.value.code == 2


def test_transcripts_that_yield_no_commands_refuse(tmp_path):
    """A transcript full of non-shell tool calls must not read as 'unused'."""
    rec = {"timestamp": "2026-08-28T10:00:00Z",
           "message": {"role": "assistant",
                       "content": [{"type": "tool_use", "name": "Read",
                                    "input": {"file_path": "x"}}]}}
    (tmp_path / "s.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        tu.scan(tmp_path)
    assert e.value.code == 2


def test_a_shell_command_is_collected_with_its_date(tmp_path):
    """The positive twin: proves the refusals above are not the only branch."""
    rec = {"timestamp": "2026-08-28T10:00:00Z",
           "message": {"role": "assistant",
                       "content": [{"type": "tool_use", "name": "Bash",
                                    "input": {"command": "uv run x4debug triage"}}]}}
    (tmp_path / "s.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    s = tu.scan(tmp_path)
    assert s.commands == [("2026-08-28", "uv run x4debug triage")]
    assert s.files == 1 and s.unreadable == []


def test_an_unparseable_line_is_recorded_not_swallowed(tmp_path):
    good = {"timestamp": "2026-08-28T10:00:00Z",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": "Bash",
                                     "input": {"command": "uv run x4save info"}}]}}
    (tmp_path / "s.jsonl").write_text("{not json\n" + json.dumps(good) + "\n",
                                      encoding="utf-8")
    s = tu.scan(tmp_path)
    assert len(s.unreadable) == 1
    assert len(s.commands) == 1


# --------------------------------------------------------------------------
# the surface is asked of the programs -- an empty list and a refusal differ
# --------------------------------------------------------------------------
def test_help_with_a_subparser_block_is_detected():
    assert tu._SUBS_IN_HELP.search(
        "usage: x4debug\n\npositional arguments:\n  {triage,crosscheck,baseline}\n")


def test_help_without_a_subparser_block_is_not_a_false_positive():
    assert not tu._SUBS_IN_HELP.search(
        "usage: x4validate [-h] mod\n\npositional arguments:\n  mod   the mod\n")


def test_the_authoritative_choice_list_is_parsed_from_argparse():
    m = tu._CHOOSE.search(
        "error: argument cmd: invalid choice: 'x' "
        "(choose from 'triage', 'crosscheck', 'baseline')")
    assert m
    subs = [s.strip().strip("'\"") for s in m.group(1).split(",")]
    assert subs == ["triage", "crosscheck", "baseline"]


# --------------------------------------------------------------------------
# a malformed date is a NON-ANSWER, and must not look like "never invoked"
# --------------------------------------------------------------------------
def test_no_date_means_never_invoked():
    assert tu._days_since("") is None


def test_a_wellformed_date_is_measured():
    from datetime import date, timedelta
    y = (date.today() - timedelta(days=3)).isoformat()
    assert tu._days_since(y) == 3


def test_a_malformed_date_raises_rather_than_collapsing_into_none():
    """Returning None here would silently drop the capability out of the
    dormancy list -- absence and non-answer becoming the same value."""
    with pytest.raises(ValueError):
        tu._days_since("not-a-date")


# --------------------------------------------------------------------------
# the accepted-gap baseline: absent must mean "nothing accepted", and an
# unreadable one must NOT quietly mean the same thing
# --------------------------------------------------------------------------
def test_absent_baseline_accepts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "BASELINE", tmp_path / "nope.json")
    assert tu._baseline_missing() == []


def test_baseline_gaps_are_read_back(tmp_path, monkeypatch):
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"missing": ["x4modlist mark"]}), encoding="utf-8")
    monkeypatch.setattr(tu, "BASELINE", b)
    assert tu._baseline_missing() == ["x4modlist mark"]


def test_an_unreadable_baseline_raises_rather_than_reading_as_empty(
        tmp_path, monkeypatch):
    """Returning [] there would flood the run with false NEW rows while looking
    like a clean first run -- absence and non-answer collapsing again."""
    b = tmp_path / "b.json"
    b.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(tu, "BASELINE", b)
    with pytest.raises(RuntimeError):
        tu._baseline_missing()


# --------------------------------------------------------------------------
# argv has THREE shapes. Enumerating two of them was wrong TWICE -- once for
# the single-command CLI, then again for a leading global flag, which published
# F75 as 17 when the answer is 13. One twin per shape, plus a guard on the CLASS.
# --------------------------------------------------------------------------
def test_a_global_flag_before_the_subcommand_is_still_found():
    """`["--registry", <path>, "dashboard"]` -- 7 of 54 real cells look like this."""
    cov = tu.Coverage([_Cell("x4modlist", ["--registry", "/tmp/r.yaml", "dashboard"])],
                      SUBS)
    assert cov.covers("x4modlist", "dashboard")
    assert not cov.covers("x4modlist", "verify")
    assert cov.undecidable == []


def test_a_flag_after_the_subcommand_is_still_found():
    cov = tu.Coverage([_Cell("x4modlist", ["--registry", "/r", "verify", "--rescore"])],
                      SUBS)
    assert cov.covers("x4modlist", "verify")


def test_an_unrecognisable_argv_shape_is_UNDECIDABLE_not_silently_absent():
    """The guard on the class. A fourth shape must fail loudly rather than
    quietly shrink the covered set, which is how the same bug landed twice."""
    cov = tu.Coverage([_Cell("x4debug", ["--only", "something"], label="odd")], SUBS)
    assert cov.undecidable == [("x4debug", "odd")]
    assert not cov.covers("x4debug", "triage")


def test_undecidable_is_empty_when_every_cell_resolves():
    """The positive twin -- otherwise the assertion above could pass vacuously."""
    cov = tu.Coverage([_Cell("x4debug", ["triage"]),
                       _Cell("x4modlist", ["--registry", "/r", "ingest"]),
                       _Cell("x4validate", ["dev/mod"])], SUBS)
    assert cov.undecidable == []
    assert cov.covers("x4debug", "triage") and cov.covers("x4modlist", "ingest")


def test_a_flag_VALUE_cannot_beat_the_real_subcommand():
    """`["--registry", "build", "dump"]` must resolve to `dump`, not `build` -- when the CLI's own usage says
    `--registry` takes a value. (Until 2026-11-14 this resolved to `dump` by ASSUMING every flag takes one,
    which is the same guess that turned `["--quiet", "query", "pause"]` into `pause`.)"""
    flags = {"x4effective": {"--registry": True}}
    cov = tu.Coverage([_Cell("x4effective", ["--registry", "build", "dump"])], SUBS, flags)
    assert cov.covers("x4effective", "dump")
    assert not cov.covers("x4effective", "build")


def test_without_the_flags_arity_a_sub_NAMED_value_is_UNDECIDABLE():
    """Twin: with no usage to read, `build` may be the value or the subcommand. Say so."""
    cov = tu.Coverage([_Cell("x4effective", ["--registry", "build", "dump"], label="amb")], SUBS)
    assert cov.undecidable == [("x4effective", "amb")]


QP = ["query", "pause"]


def test_a_flag_EQUALS_value_is_one_word():
    """MEASURED by the reviewer 2026-11-14: `["--db=x", "query", "pause"]` returned `pause`."""
    assert tu._subcommand_in(["--db=x", "query", "pause"], QP) == "query"


def test_a_VALUELESS_flag_of_unknown_arity_before_a_subcommand_is_UNDECIDABLE():
    """MEASURED by the reviewer: `["--quiet", "query", "pause"]` returned `pause`, a confident wrong answer."""
    assert tu._subcommand_in(["--quiet", "query", "pause"], QP) is None


def test_a_VALUELESS_flag_the_usage_declares_is_not_given_a_value():
    assert tu._subcommand_in(["--quiet", "query", "pause"], QP, {"--quiet": False}) == "query"


def test_a_VALUE_flag_the_usage_declares_consumes_exactly_one_word():
    assert tu._subcommand_in(["--db", "pause", "query"], QP, {"--db": True}) == "query"


def test_the_first_non_flag_word_decides_and_a_non_subcommand_is_UNDECIDABLE():
    """With the arity known, a stray positional before any subcommand is not skipped past."""
    assert tu._subcommand_in(["stray", "query"], QP, {}) is None


def test_usage_flags_are_read_from_the_clis_own_help():
    text = ("usage: x4live [-h] [--version] [--file FILE]\n"
            "              {automation,dump,query} ...\n\nRead what ...\n")
    assert tu.usage_flags(text) == {"-h": False, "--version": False, "--file": True}


def test_usage_with_no_global_flags_still_answers():
    assert tu.usage_flags("usage: x4save [-h] [--version] {info,check} ...\n") == {"-h": False,
                                                                                  "--version": False}


def test_no_usage_line_is_unknown_not_empty():
    assert tu.usage_flags("") is None


def test_the_heuristic_errs_toward_UNDECIDABLE_not_misattribution():
    """A VALUELESS flag before the subcommand makes it skip one too many. That is
    a known limit, and it fails in the safe direction: a loud 'cannot tell'
    rather than a quiet wrong answer. Pinned so the limit stays visible."""
    cov = tu.Coverage([_Cell("x4debug", ["--verbose", "triage"], label="v")], SUBS)
    assert cov.undecidable == [("x4debug", "v")]


def test_a_surface_REFUSAL_is_this_gates_rc_2_never_an_empty_roster(monkeypatch):
    """The roster is asked of `x4validate._surface` since the gate moved into this
    repository (2026-09-13). A refusal there must stay a refusal here: an empty roster would
    make every coverage figure below it vacuously complete."""
    def refuse(root=None):
        raise tu._surface.SurfaceUnavailable("no roster", "restore pyproject.toml")

    monkeypatch.setattr(tu._surface, "cli_roster", refuse)
    with pytest.raises(SystemExit) as exc:
        tu.cli_roster()
    assert exc.value.code == 2
