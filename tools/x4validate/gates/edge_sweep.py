#!/usr/bin/env python
"""Edge sweep — hostile and degenerate inputs against every CLI.

Smoke tests prove the happy path. This proves the tools FAIL WELL: a clear
message and a sane exit code, never a traceback and never a confident wrong
answer. Covers empty mods, malformed manifests, missing args, absent paths, and
an unconfigured environment.

WHAT "UNCONFIGURED" MEANS HERE (reviewer M-4): every path variable blanked AND an
explicitly selected, EMPTY `X4_CONFIG` file (F254: without it, code in this toolkit
discovered its own private config from any CWD). That is an empty configuration,
not the new user's NO configuration: the discovery path that finds no x4-paths.env
anywhere is not exercised by this sweep on a configured machine. That state is
covered by `scripts/verify-cold.sh`, which refuses to run until all three roots
resolve to None and then runs every CLI against it.

Every cell carries the exit codes that count as FAILING WELL for it
(AUDIT-2026-09-24 GT-4). Judging only tracebacks scored `x4validate <missing dir>`
exiting 0 with "OK" as handled -- the confident wrong answer this sweep exists to
catch. The sets were MEASURED 2026-09-25 (39 cells) and then judged: a cell whose
measured rc was itself a wrong answer is pinned to the RIGHT code, not the
observed one (see `x4xref who-calls ''`).

Run:  uv run python gates/edge_sweep.py [--verbose]
Exit: 0 every cell exited within its expected set, 1 any traceback, hang, or
      exit code outside the cell's expected set.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERBOSE = "--verbose" in sys.argv
# Every input this sweep uses is synthesized in a temp dir, so no game paths are
# needed — deliberately, since hostile-input coverage should run anywhere.

def _declared_clis() -> list[str]:
    """The CLI set, DERIVED from `[project.scripts]` rather than restated here.

    This was a hand-maintained literal until 2026-08-26, and it listed 9 of 10 --
    `x4debug` was missing, so the sweep covered a subset AND PRINTED SUCCESS. That
    is this register's founding shape, sitting inside a gate. A literal cannot
    notice a new CLI; the source of truth can.

    Pinned by tests/test_cli_enumerations_agree.py, which checks every
    hand-maintained enumeration of "the CLIs" against pyproject at once.
    """
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return sorted(data["project"]["scripts"])


TOOLS = _declared_clis()


#: Every variable `_paths` consults. Blanking a subset is worse than blanking
#: none, because the run then LOOKS unconfigured while quietly resolving through
#: whichever alias was missed — the first version of this sweep blanked
#: `X4_GAME_ROOT` and never `X4_GAME`, so its headline claim was false.
_PATH_VARS = ("X4_TOOLKIT", "X4_GAME", "X4_GAME_ROOT", "X4_GAME_EXTENSIONS",
              "X4_EXTENSIONS", "X4_REFERENCE", "X4_PROFILE", "X4_PROFILE_CONTENT",
              "X4_PROFILE_EXTENSIONS", "X4_WORKSHOP_CONTENT", "X4_REGISTRY",
              "X4_MODS", "X4_DEBUGLOG", "X4_SAVES", "X4_EFFECTIVE_DB",
              "X4_ORACLE_LOG",
              # Which FILE `_paths` reads (v4.0.0 review R5-9): left set, the
              # "unconfigured" sweep read whatever config it named.
              "X4_CONFIG")


def run(argv: list[str], env: dict | None = None, timeout: int = 900,
        cwd: Path | None = None):
    # env-ok: inheriting the whole environment for a subprocess, not resolving a
    # setting. `_paths` answers "where is X configured"; this is "hand the child
    # what I was given", and filtering it through a resolver would be wrong.
    e = dict(os.environ)
    if env is not None:
        e.update(env)
    p = subprocess.run(["uv", "run", "--project", str(ROOT), *argv],
                       cwd=str(cwd or ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


#: Exit-code sets. 0 = answered clean, 1 = findings / a reported error, 2 = refused
#: (usage, missing input), 3 = DEGRADED (a requested check could not run).
REFUSE = frozenset({2})
HELP = frozenset({0})


def unconfigured_environment(away: Path) -> dict[str, str]:
    """Empty variables alone still discover the acting toolkit's real config.

    Select a real, empty fixture config explicitly; keep production resolution
    semantics intact and avoid reading private paths or artifact locations.
    """
    config = away / "x4-paths.env"
    config.write_text("", encoding="utf-8")
    return {**{k: "" for k in _PATH_VARS}, "X4_CONFIG": str(config)}


def check(label: str, argv: list[str], env: dict | None = None,
          cwd: Path | None = None, expect: frozenset = REFUSE) -> tuple[str, str]:
    """A cell passes when it neither crashes nor hangs AND exits within `expect`."""
    try:
        rc, out = run(argv, env, cwd=cwd)
    except subprocess.TimeoutExpired:
        return "FAIL", "HANG (>15min)"
    if "Traceback (most recent call last)" in out:
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:]
        return "FAIL", f"traceback: {tail[0][:90] if tail else '?'}"
    if rc not in expect:
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:]
        return "FAIL", (f"exit {rc}, expected one of {sorted(expect)}: "
                        f"{tail[0][:70] if tail else '<no output>'}")
    if rc == 0 and not out.strip():
        return "WARN", "exit 0 with no output"
    return "ok", f"exit {rc}, {len(out)}B"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="x4qa_"))
    fails, warns, total = [], [], 0
    try:
        empty = tmp / "empty_mod"
        empty.mkdir()

        manifest_only = tmp / "manifest_only"
        manifest_only.mkdir()
        (manifest_only / "content.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<content id="qa_manifest_only" name="QA" version="1" />\n', encoding="utf-8")

        broken = tmp / "broken_manifest"
        broken.mkdir()
        (broken / "content.xml").write_text("<content id='unclosed'", encoding="utf-8")

        badxml = tmp / "broken_patch"
        (badxml / "libraries").mkdir(parents=True)
        (badxml / "content.xml").write_text(
            '<content id="qa_broken_patch" name="QA" version="1" />', encoding="utf-8")
        (badxml / "libraries" / "wares.xml").write_text(
            "<diff><replace sel='//wares'><wares>", encoding="utf-8")

        emptypatch = tmp / "empty_patch"
        (emptypatch / "libraries").mkdir(parents=True)
        (emptypatch / "content.xml").write_text(
            '<content id="qa_empty_patch" name="QA" version="1" />', encoding="utf-8")
        (emptypatch / "libraries" / "wares.xml").write_text("<diff/>", encoding="utf-8")

        missing = tmp / "does_not_exist"

        f = frozenset
        # (label, argv, env, cwd, expected exit codes). A NOTHING-TO-CHECK mod may
        # refuse (2) or say DEGRADED (3), never 0 -- 0 is "checked and clean".
        cases: list[tuple] = [
            ("empty mod dir",            ["x4validate", str(empty)], None, None, f({2, 3})),
            ("manifest only",            ["x4validate", str(manifest_only)], None, None, f({2, 3})),
            ("malformed content.xml",    ["x4validate", str(broken)], None, None, f({1, 2, 3})),
            ("malformed diff patch",     ["x4validate", str(badxml)], None, None, f({1, 3})),
            # An empty <diff/> is a real, parseable, no-op patch: clean is a true answer.
            ("empty <diff/>",            ["x4validate", str(emptypatch)], None, None, f({0, 3})),
            ("missing dir",              ["x4validate", str(missing)], None, None, REFUSE),
            ("empty mod, tier b",        ["x4validate", str(empty), "--tier", "b"], None, None,
                                         f({2, 3})),
            ("bad tier value",           ["x4validate", str(empty), "--tier", "z"], None, None,
                                         REFUSE),
            ("--entity without --like",  ["x4validate", str(manifest_only),
                                          "--entity", "ware:ore"], None, None, f({2, 3})),
            ("--file that does not exist", ["x4validate", str(manifest_only),
                                            "--file", "no/such.xml"], None, None, REFUSE),
            ("x4diff missing operand",   ["x4diff", str(empty)], None, None, REFUSE),
            ("x4diff both missing",      ["x4diff", str(missing), str(missing)], None, None, REFUSE),
            ("x4stats macro on non-xml", ["x4stats", "macro", str(broken / "content.xml")], None,
                                         None, f({1, 2})),
            # "introduces/changes no wares" is a TRUE answer about an empty mod.
            ("x4stats wares empty mod",  ["x4stats", "wares", str(empty)], None, None, f({0, 3})),
            ("x4effective sql injection", ["x4effective", "sql",
                                           "SELECT 1; DROP TABLE entities"], None, None, f({1, 2})),
            ("x4effective sql garbage",  ["x4effective", "sql", "NOT SQL AT ALL"], None, None,
                                         f({1, 2})),
            ("x4effective dump traversal", ["x4effective", "dump", "../../etc/passwd"], None, None,
                                            f({1, 2})),
            # An empty name is a question nobody asked: refuse it. (Before the audit
            # fix that makes x4xref refuse empty names, it exited 0 and called '' "a
            # real negative over 150683 indexed rows" -- MEASURED 2026-09-25.)
            ("x4xref who-calls empty",   ["x4xref", "who-calls", ""], None, None, REFUSE),
            ("x4similar bad threshold",  ["x4similar", "--threshold", "9"], None, None, REFUSE),
            ("x4similar neg threshold",  ["x4similar", "--threshold", "-1"], None, None, REFUSE),
            ("x4compat bad subcommand",  ["x4compat", "nosuchcmd"], None, None, REFUSE),
            ("x4modlist bad subcommand", ["x4modlist", "nosuchcmd"], None, None, REFUSE),
        ]
        # Every tool must survive a fully unconfigured environment — the state a
        # NEW USER is in. These barriers are required:
        #   * blank every alias in _PATH_VARS, not a subset;
        #   * run from a directory OUTSIDE any toolkit, because
        #     a resolver outside the toolkit layout can discover a CWD config;
        #   * explicitly select an empty fixture config: code in this toolkit
        #     discovers its own config even from an unrelated CWD (F254).
        away = tmp / "elsewhere"
        away.mkdir()
        blank = unconfigured_environment(away)
        for t in TOOLS:
            cases.append((f"{t} unconfigured --help", [t, "--help"], blank, away, HELP))
        # --help barely touches resolution; these actually exercise the chain.
        cases.append(("x4validate unconfigured run",
                      ["x4validate", str(manifest_only)], blank, away, REFUSE))
        # --paths is a REPORT of what resolved; saying "nothing did" is an answer.
        cases.append(("x4validate --paths unconfigured", ["x4validate", "--paths"], blank, away,
                      f({0, 2})))
        cases.append(("x4effective unconfigured", ["x4effective", "ls", "macro"], blank, away,
                      REFUSE))
        cases.append(("x4compat unconfigured run", ["x4compat", "check"], blank, away, REFUSE))
        cases.append(("x4similar unconfigured run", ["x4similar"], blank, away, REFUSE))
        cases.append(("x4xref unconfigured query",
                      ["x4xref", "who-calls", "find_station"], blank, away, REFUSE))

        print(f"EDGE SWEEP — {len(cases)} hostile-input cells\n" + "=" * 78)
        for label, argv, env, cwd, expect in cases:
            total += 1
            status, detail = check(label, argv, env, cwd, expect)
            print(f"  {status:<5} {label:<30} {detail}")
            if status == "FAIL":
                fails.append((label, argv, detail))
            elif status == "WARN":
                warns.append((label, detail))
        print("=" * 78)
        print(f"ok {total - len(fails) - len(warns)}   WARN {len(warns)}   FAIL {len(fails)}")
        for label, argv, detail in fails:
            print(f"\nFAIL {label}\n     argv={argv}\n     {detail}")
        for label, detail in warns:
            print(f"WARN {label}: {detail}")
        return 1 if fails else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
