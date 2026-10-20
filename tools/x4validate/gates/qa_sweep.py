#!/usr/bin/env python
"""QA sweep — exercise EVERY tool x EVERY subcommand against the real install.

Written 2026-08-08 after a defect (root-<replace> silently dropped, reported as
applied) survived three sessions because nothing ever exercised that path. The
point is to find bugs here, not while using the tools.

Each cell records: exit code, stdout size, and whether it crashed. A cell is
RED on an unhandled traceback or an unexpected exit code; YELLOW when it runs
but returns nothing useful (a possible unfinished feature); GREEN otherwise.

Run:  uv run python gates/qa_sweep.py [--verbose]
Exit: 0 all green, 1 any red, 3 only yellows.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

EXT = _env.extensions()
REF = _env.reference()
try:
    DEV = _env.mods_dir()
except SystemExit:      # mod-source dir is optional here; extensions are enough
    DEV = EXT

VERBOSE = "--verbose" in sys.argv


@dataclass
class Cell:
    tool: str
    label: str
    argv: list[str]
    expect: tuple[int, ...] = (0,)
    #: substring that must appear in the cell's output -- stdout AND stderr, combined, as
    #: `run()` matches it -- for the cell to count as doing work; absent, the cell is YELLOW
    wants: str | None = None
    #: True when a non-zero exit is a legitimate finding, not a tool failure
    findings_ok: bool = False
    #: Minutes, not seconds. Excluded unless --all, so the default sweep stays a
    #: thing you actually run. A slow cell that makes the sweep unbearable is a
    #: cell that gets skipped by a human instead of by a flag.
    slow: bool = False
    #: Extra environment for THIS cell only -- how a build is redirected at a
    #: throwaway output instead of the artifact the workspace depends on.
    env: dict = field(default_factory=dict)
    status: str = ""
    detail: str = ""
    out: str = field(default="", repr=False)


def _pick_mod(*, patching: bool = False) -> str:
    """An installed mod to exercise the CLIs against.

    Chosen from whatever is installed rather than named, so this runs on anyone's
    setup. `patching=True` prefers a mod that actually ships diff patches, which
    is what makes the sel-resolution paths do real work.
    """
    mods = [d for d in sorted(EXT.iterdir())
            if d.is_dir() and not d.name.lower().startswith("ego_dlc_")]
    if patching:
        for d in mods:
            for f in list(d.rglob("*.xml"))[:60]:
                try:
                    if b"<diff" in f.read_bytes()[:2000]:
                        return str(d)
                except OSError:
                    continue  # silent-ok: only picking a sample mod; try the next file
    return str(mods[0]) if mods else str(EXT)


def _owning_mod() -> str:
    """A mod that wins at least one value in the effective store, or a fallback.

    Read from the store so the cell exercises a real `diff-mod` result on any
    install, instead of naming one machine's overhaul.
    """
    import sqlite3
    try:
        db = _env.effective_db()
    except SystemExit:
        return Path(_pick_mod()).name
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute(
            "SELECT origin, COUNT(*) c FROM entities WHERE origin != 'base' "
            "GROUP BY origin ORDER BY c DESC LIMIT 1").fetchone()
        con.close()
        return row[0] if row else Path(_pick_mod()).name
    except sqlite3.Error:
        return Path(_pick_mod()).name


_MOD = _pick_mod(patching=True)
_MOD2 = _pick_mod()

_SANDBOX: str | None = None
_SANDBOX_DIR: Path | None = None


def _any_registry_id() -> str:
    """Some id that exists in the sandbox registry — DISCOVERED, never named.

    Naming a mod here would bake one machine's modlist into a gate; it also has to
    come from the SANDBOX, because the command under test writes to whatever
    registry it is pointed at.
    """
    try:
        from ruamel.yaml import YAML
        data = YAML().load(Path(_sandbox_registry()).read_text(encoding="utf-8"))
        for m in (data or {}).get("mods") or []:
            if m.get("id"):
                return str(m["id"])
    except (OSError, AttributeError, TypeError, ValueError):
        # silent-ok: no usable registry -> a sentinel id. The cell then exercises
        # the not-in-registry path (exit 2), which is in its expected set, so the
        # cell still asserts a real behaviour rather than silently not running.
        pass
    return "__no_such_mod__"


def _sandbox_dir() -> Path:
    """One throwaway directory per sweep, for cells that WRITE something.

    Same reasoning as `_sandbox_registry`: a gate must not mutate the state it
    inspects. MEASURED 2026-08-29 while adding these cells -- `x4modlist
    --registry <throwaway> snapshot` wrote a real snapshot into the DEFAULT
    registry's folder, because `snapshots_dir()` resolved DEFAULT_REGISTRY and
    ignored the override every other command honoured.
    """
    global _SANDBOX_DIR
    if _SANDBOX_DIR is None:
        import atexit
        import shutil
        import tempfile
        _SANDBOX_DIR = Path(tempfile.mkdtemp(prefix="x4qa_out_"))
        # Removed at exit: nothing reads it after the sweep. Without this every sweep left one behind
        # (767 of them, and 1,184 registry copies, on one machine by 2026-10-18).
        atexit.register(shutil.rmtree, _SANDBOX_DIR, True)
    return _SANDBOX_DIR


def _keyless_config() -> Path:
    """An EMPTY config file, for cells that must run with no `X4_NEXUS_KEY` anywhere.

    `X4_CONFIG` naming a file replaces the toolkit's own `x4-paths.env`, so a key kept there
    is not read either. Written once per sweep, into the throwaway directory.
    """
    path = _sandbox_dir() / "keyless-x4-paths.env"
    path.write_bytes(b"")
    return path


def _sandbox_registry() -> str:
    """A throwaway copy of the real registry, made once per sweep.

    Copied rather than synthesized so the cells still exercise real data with
    real shape; written to a temp dir so the WRITING commands (`dashboard`)
    cannot touch the user's triage state.
    """
    global _SANDBOX
    if _SANDBOX is None:
        import atexit
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="x4qa_registry_"))
        atexit.register(shutil.rmtree, tmp, True)
        dest = tmp / "modlist.yaml"
        try:
            src = _env.registry_file()
        except (SystemExit, AttributeError):
            src = None
        if src and Path(src).is_file():
            _env.sandbox_copy(src, dest)   # writable: it is a throwaway, not the original
        else:
            dest.write_text("meta: {}\nmods: []\n", encoding="utf-8")
        _SANDBOX = str(dest)
    return _SANDBOX



def _newest_groundtruth() -> str:
    """The most recent `x4live groundtruth` fixture, or a path that does not exist.

    NOT a silent skip. With no fixture present the cell runs against a missing path and
    the tool answers rc 2 ("no such groundtruth file") -- a real answer, inside expect.
    Dropping the cell instead would shrink what the sweep covers without saying so,
    which is the defect check_coverage() exists to refuse.
    """
    try:
        # ONLY what `x4live groundtruth` writes: groundtruth-YYYYMMDD-HHMMSS.tsv. The bare glob
        # also matched a macro LIST saved beside the fixtures (`groundtruth-macros-...tsv`),
        # which sorted after every real fixture and turned the mappings cell RED (MEASURED
        # 2026-09-14; tests/test_qa_sweep_newest_groundtruth.py).
        hits = sorted(p for p in (_env.mods_dir() / "_reports").glob("groundtruth-*.tsv")
                      if re.fullmatch(r"groundtruth-\d{8}-\d{6}\.tsv", p.name))
        if hits:
            return str(hits[-1])
    except (Exception, SystemExit):   # _env.* raises SystemExit, a BaseException
        # silent-ok: absence IS the answer here, and the cell reports it as rc 2.
        pass
    return str(ROOT / "__no_groundtruth_fixture__.tsv")


def _shipped_clis() -> set[str]:
    """Which console scripts THIS checkout actually declares.

    The dev tree and the public bundle do not ship the same set -- work lands in
    dev first and is released deliberately. Until 2026-08-29 the port handled that
    by HAND-DELETING the cells for unshipped CLIs on the way across, which is the
    same class as F73 and F76: a step a human performs on every port, invisible to
    every guard, and wrong the first time somebody forgets. (It was forgotten
    immediately: copying this file across turned `test_no_qa_cell_names_a_cli_that
    _does_not_exist` red, which is the only reason it was noticed.)

    So the roster adapts instead. One file, both trees, and the difference is
    DERIVED from pyproject rather than maintained by memory.
    """
    import tomllib     # requires-python is >=3.13, so this is always available
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = set(data.get("project", {}).get("scripts", {}))
    if not scripts:
        # REFUSE rather than guess. An empty set would mean "filter nothing", so a
        # broken pyproject would silently restore the very cells this is meant to
        # drop -- a non-answer wearing the shape of an answer.
        raise RuntimeError(
            f"{ROOT / 'pyproject.toml'} declares no [project.scripts]; the cell "
            f"roster cannot be decided, and filtering nothing would silently "
            f"restore cells for CLIs that may not exist.")
    return scripts


_ALL_CELLS: list[Cell] = [
    # ---- x4validate -----------------------------------------------------
    Cell("x4validate", "--paths", ["--paths"], wants="reference"),
    Cell("x4validate", "tier a (overlay mod)", [_MOD, "--tier", "a"],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4validate", "tier b (overlay mod)", [_MOD, "--tier", "b"],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4validate", "--json", [_MOD, "--json"],
         expect=(0, 1, 3), wants="{", findings_ok=True),
    Cell("x4validate", "--sel-only", [_MOD, "--sel-only"],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4validate", "--entity/--like", [_MOD,
                                           "--entity", "ware:ore", "--like", "ware:silicon"],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4validate", "--update", [_MOD, "--update"],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4validate", "--debug", [_MOD, "--debug"],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4validate", "nonexistent mod dir", [str(DEV / "__no_such_mod__")],
         expect=(1, 2, 3), findings_ok=True),

    # ---- x4effective ----------------------------------------------------
    Cell("x4effective", "ls macro", ["ls", "macro", "--limit", "20"], expect=(0,), wants="_macro"),
    Cell("x4effective", "ls ware", ["ls", "ware", "--limit", "20"], expect=(0,), wants="tech"),
    Cell("x4effective", "ls --modified-only", ["ls", "macro", "--modified-only", "--limit", "10"],
         expect=(0,)),
    Cell("x4effective", "ls --filter", ["ls", "macro", "--filter", "shield", "--limit", "5"],
         expect=(0,), wants="shield"),
    Cell("x4effective", "ls unknown kind", ["ls", "__no_such_kind__"], expect=(0, 1, 2),
         findings_ok=True),
    Cell("x4effective", "show", ["show", "macro", "shield_arg_l_standard_01_mk1_macro"],
         expect=(0,), wants="recharge"),
    Cell("x4effective", "attr", ["attr", "macro", "recharge.delay",
                                 "--class", "shieldgenerator"], expect=(0,)),
    Cell("x4effective", "who-sets", ["who-sets", "macro", "shield_arg_l_standard_01_mk1_macro",
                                     "recharge.delay"], expect=(0,), wants="recharge.delay"),
    # diff-mod needs a mod that actually WINS something, discovered from the
    # store rather than named — otherwise this cell only passes on one machine.
    Cell("x4effective", "diff-mod", ["diff-mod", _owning_mod()], expect=(0,)),
    Cell("x4effective", "dump", ["dump", "libraries/wares.xml"], expect=(0,), wants="<"),
    Cell("x4effective", "dump md/ path", ["dump", "md/encounters.xml"], expect=(0, 1),
         findings_ok=True),
    Cell("x4effective", "dump unknown vpath", ["dump", "no/such/file.xml"], expect=(0, 1, 2),
         findings_ok=True),
    Cell("x4effective", "sql", ["sql", "SELECT COUNT(*) FROM entities"], expect=(0,)),
    Cell("x4effective", "sql rejects write", ["sql", "DELETE FROM entities"],
         expect=(1, 2), findings_ok=True),
    Cell("x4effective", "show unknown entity", ["show", "macro", "__no_such_entity__"],
         expect=(0, 1), findings_ok=True),

    # ---- x4compat -------------------------------------------------------
    Cell("x4compat", "check", ["check"], expect=(0, 1, 3), findings_ok=True),

    # ---- x4xref ---------------------------------------------------------
    Cell("x4xref", "who-calls", ["who-calls", "find_station"], expect=(0, 1), findings_ok=True),
    Cell("x4xref", "who-listens", ["who-listens", "event_player_ejected"],
         expect=(0, 1), findings_ok=True),
    Cell("x4xref", "cue", ["cue", "Manager"], expect=(0, 1), findings_ok=True),
    Cell("x4xref", "who-calls unknown", ["who-calls", "__no_such_action__"],
         expect=(0, 1), findings_ok=True),

    # ---- x4stats --------------------------------------------------------
    Cell("x4stats", "wares", ["wares", _MOD],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4stats", "macro", ["macro", str(REF / "assets/props/SurfaceElements/macros/"
                                           "shield_arg_l_standard_01_mk1_macro.xml")],
         expect=(0, 1), findings_ok=True),

    # ---- x4similar ------------------------------------------------------
    Cell("x4similar", "default sweep", ["--threshold", "0.95"], expect=(0, 1, 3),
         findings_ok=True),
    Cell("x4similar", "--cross-mod-only", ["--threshold", "0.95", "--cross-mod-only"],
         expect=(0, 1, 3), findings_ok=True),

    # ---- x4modlist ------------------------------------------------------
    # --registry points at a THROWAWAY COPY: `dashboard` WRITES WORKLIST.md, and
    # this cell was regenerating the user's real one on every sweep. It is a pure
    # function of modlist.yaml so nothing was corrupted, but a gate must not
    # mutate the state it is inspecting — that is how a sweep turns into an edit.
    Cell("x4modlist", "dashboard", ["--registry", _sandbox_registry(), "dashboard"],
         expect=(0, 1), findings_ok=True),
    Cell("x4modlist", "needs-review", ["--registry", _sandbox_registry(), "needs-review"],
         expect=(0, 1), findings_ok=True),
    # `verify` exits 1 while any identity is unconfirmed — that is a FINDING, not a
    # failure, so it is usable as a gate without making a normal run look broken.
    Cell("x4modlist", "verify", ["--registry", _sandbox_registry(), "verify"],
         expect=(0, 1), findings_ok=True),
    Cell("x4modlist", "verify --rescore", ["--registry", _sandbox_registry(),
                                           "verify", "--rescore"],
         expect=(0, 1), findings_ok=True),
    # A pin is offline and must not need the network: `source` records a non-Nexus
    # origin, which is exactly the case where no API call can help.
    Cell("x4modlist", "source (off-nexus)", ["--registry", _sandbox_registry(),
                                             "source", _any_registry_id(), "local"],
         expect=(0, 2), findings_ok=True),

    # ---- x4diff ---------------------------------------------------------
    Cell("x4diff", "two mod versions",
         [_MOD, _MOD2],
         expect=(0, 1), findings_ok=True),
    Cell("x4diff", "--detail",
         [_MOD, _MOD2, "--detail"],
         expect=(0, 1), findings_ok=True),
    Cell("x4diff", "same dir (no-op diff)",
         [_MOD, _MOD],
         expect=(0, 1), findings_ok=True),

    # ---- x4debug --------------------------------------------------------
    # The log is a real artifact of a real launch, so these cells depend on one
    # existing. Both "no log configured" and "a clean log" are legitimate, hence
    # the widened expect: what is NOT legitimate is a crash or a silent 0 over
    # nothing, which `wants` pins down.
    Cell("x4debug", "triage", ["triage"], expect=(0, 2), findings_ok=True,
         wants=None),
    Cell("x4debug", "triage missing log", ["triage", "__no_such_log__.txt"],
         expect=(2,), findings_ok=True),
    Cell("x4debug", "crosscheck", ["crosscheck", _MOD], expect=(0, 1, 2),
         findings_ok=True),
    Cell("x4debug", "crosscheck bad mod", ["crosscheck", "__no_such_mod__"],
         expect=(2,), findings_ok=True),
    # ---- x4save ---------------------------------------------------------
    # info reads only the header, so it is fast and must always succeed.
    Cell("x4save", "info (newest save)", ["info"], wants="SAVE-BAKED"),
    # check builds the full definition set (~20s) and legitimately exits 1 when a
    # save references content the live tree no longer defines.
    Cell("x4save", "check (newest save)", ["check", "--limit", "5"],
         expect=(0, 1, 3), findings_ok=True, wants="examined"),
    # a non-save must be rc 2 -- a NON-ANSWER, never a clean result.
    Cell("x4save", "refuses a non-save", ["info", "pyproject.toml"], expect=(2,)),
    # ---- x4live ---------------------------------------------------------
    # These read a dump written by an in-game probe, so "no dump present" (rc 2)
    # is a legitimate state on a machine that has never run one -- what is NOT
    # legitimate is rc 0 over nothing, which is the whole point of the tool.
    Cell("x4live", "dump", ["dump"], expect=(0, 2, 3), findings_ok=True),
    Cell("x4live", "extensions", ["extensions"], expect=(0, 1, 2, 3),
         findings_ok=True),
    Cell("x4live", "errors", ["errors", "--limit", "5"], expect=(0, 1, 2, 3),
         findings_ok=True),
    Cell("x4live", "oracle", ["oracle"], expect=(0, 1, 2, 3), findings_ok=True),
    # a file that is not a uidata.xml must be a NON-ANSWER, never a clean zero.
    Cell("x4live", "refuses a non-uidata file", ["--file", "pyproject.toml", "dump"],
         expect=(2, 3), findings_ok=True),
    Cell("x4live", "refuses a missing file",
         ["--file", "__no_such_uidata__.xml", "dump"], expect=(2,), findings_ok=True),
    # `mappings` PROPOSES field mappings from a dump; with no dump it must be the
    # same rc 2 non-answer as its siblings, never a clean zero over nothing.
    # rc 1 belongs here too: `cmd_mappings` ends `return 1 if (fresh_props or
    # ambiguous) else 0`, so a dump that HAS candidates to propose is rc 1 -- the
    # normal, useful outcome. The tuple omitted it and the cell went RED the first
    # time the store was rebuilt with enough content to produce an ambiguity, which
    # is why every sibling x4live cell above already says (0, 1, 2, 3). Accepting 1
    # cannot mask a non-answer: the no-dump and no-store paths return 3 and 2 from
    # early guards, long before anything could be proposed.
    Cell("x4live", "mappings", ["mappings"], expect=(0, 1, 2, 3), findings_ok=True),
    # `archive` copies the game's uidata.xml out before X4 overwrites it; offline it either
    # archives an existing dump (0) or refuses because there is none (2). Written into the
    # sweep's throwaway dir, never the user's $X4_MODS/_reports. (`pause`/`unpause` need a
    # RUNNING game and stay accepted gaps in toolkit_usage's baseline.)
    Cell("x4live", "archive", ["archive", "--out", str(_sandbox_dir() / "archive")],
         expect=(0, 2), findings_ok=True),

    # ---- the F75 backlog: capabilities the sweep did not exercise --------
    # Every WRITING x4modlist cell points at the throwaway registry, so a sweep
    # cannot edit the user's triage state. `snapshot` needed a tool fix before it
    # could honour that: it wrote to the DEFAULT registry regardless.
    Cell("x4modlist", "ingest", ["--registry", _sandbox_registry(), "ingest"],
         expect=(0, 1), findings_ok=True),
    Cell("x4modlist", "snapshot", ["--registry", _sandbox_registry(),
                                   "snapshot", "--label", "qa-sweep"], expect=(0,)),
    Cell("x4modlist", "tracked", ["--registry", _sandbox_registry(),
                                  "tracked", "--limit", "3"], expect=(0, 1),
         findings_ok=True),
    Cell("x4modlist", "mark", ["--registry", _sandbox_registry(),
                               "mark", _any_registry_id()], expect=(0, 2),
         findings_ok=True),
    Cell("x4modlist", "ignore", ["--registry", _sandbox_registry(), "ignore",
                                 _any_registry_id(), "--reason", "qa-sweep"],
         expect=(0, 2), findings_ok=True),
    Cell("x4modlist", "resolve", ["--registry", _sandbox_registry(), "resolve",
                                  _any_registry_id(), "1"], expect=(0, 2),
         findings_ok=True),
    # `refresh` is the only cell that COULD reach the network. A bogus id resolves
    # to nothing, so it exercises the command's plumbing without an API call and
    # without spending the rate budget. A gate must not depend on a remote host.
    Cell("x4modlist", "refresh (no network)", ["--registry", _sandbox_registry(),
                                               "refresh", "--ids", "__no_such_mod__"],
         expect=(0, 1), findings_ok=True),
    # `search` is the other cell that COULD reach the network, so it is swept only through its
    # REFUSAL: with no key it must exit 2 naming X4_NEXUS_KEY (in stdout or stderr, which
    # `wants` matches combined) before any request is made. The child gets an empty config
    # file and a blank key (a blank env value is dropped by `_paths`, so the ambient key
    # cannot leak in). If a key resolved anyway, what the cell shows depends on Nexus's
    # answer: ACCEPTED -> rc 0 -> RED; any other Nexus failure -> rc 2 without the key's
    # name -> YELLOW, not RED. A key Nexus REFUSES prints the same static "missing or was
    # refused" text with rc 2, so it reads GREEN although a request was made: this cell
    # proves the keyless refusal, not the absence of a key.
    Cell("x4modlist", "search (no key, no network)",
         ["--registry", _sandbox_registry(), "search", "qa-sweep"], expect=(2,),
         wants="X4_NEXUS_KEY",
         env={"X4_NEXUS_KEY": "", "X4_CONFIG": str(_keyless_config())}),
    # `changed` exits 1 when it FINDS something, which is the normal state.
    Cell("x4modlist", "changed", ["--registry", _sandbox_registry(), "changed"],
         expect=(0, 1, 3), findings_ok=True),
    Cell("x4debug", "baseline", ["baseline", "--dest", str(_sandbox_dir() / "baseline")],
         expect=(0, 2), findings_ok=True),
    Cell("x4effective", "coverage", ["coverage"], expect=(0, 1, 3), findings_ok=True),

    # ---- SLOW: real builds, redirected at throwaway outputs --------------
    # These exercise the real code path end to end. They are excluded by default
    # because they take minutes; they must NEVER write the shared artifacts, so
    # each is pointed at a temp output and the fingerprints are asserted unchanged
    # afterwards by whoever runs --all.
    Cell("x4effective", "build (sandboxed)", ["build"], expect=(0,), slow=True,
         env={"X4_EFFECTIVE_DB": str(_sandbox_dir() / "effective.sqlite")}),
    Cell("x4xref", "build (sandboxed)",
         ["build", "--out", str(_sandbox_dir() / "md_xref.tsv")],
         expect=(0,), slow=True),
    # The LIVE half needs the game RUNNING with the live-query mod deployed, which
    # a sweep cannot arrange. So the contract asserted here is the REFUSAL: with no
    # game (or no pywin32) these must be rc 2 -- a NON-ANSWER naming what is missing
    # -- and never rc 0. A short timeout keeps the sweep fast; the point is the exit
    # code, not the wait. rc 0 is allowed only because a sweep run WHILE the game is
    # up should not be reported as a gate failure.
    Cell("x4live", "query ping (rc 2 unless the game is up)",
         ["query", "ping", "--timeout", "1"], expect=(0, 2), findings_ok=True),
    # The READ half of the write verbs. 1 is the engine saying the state is unreadable.
    Cell("x4live", "pausestate (rc 2 unless the game is up)",
         ["pausestate", "--timeout", "1"], expect=(0, 1, 2), findings_ok=True),
    Cell("x4live", "ramp (rc 2 unless the game is up)",
         ["ramp", "--timeout", "1"], expect=(0, 2), findings_ok=True),
    # An unknown verb must still be a real ANSWER from the engine (rc 1), never a
    # crash -- but with no game up it cannot get that far, so 2 is the honest state.
    Cell("x4live", "groundtruth (rc 2 unless the game is up)",
         ["groundtruth", "--timeout", "1"], expect=(0, 2), findings_ok=True),
    Cell("x4live", "harvest (rc 2 unless the game is up)",
         ["harvest", "--timeout", "1"], expect=(0, 2), findings_ok=True),
    # The source parse runs first (reference/ ui lua); then the pipe. 3 is a batch the game
    # did not answer -- a non-answer the tool refuses to call a finding.
    Cell("x4live", "ffi-census (rc 2 unless the game is up)",
         ["ffi-census", "--timeout", "1"], expect=(0, 2, 3), findings_ok=True),
    # ⚠ The two cells above run the DEFAULT uidata path, which exits 2 whenever the
    # engine-probe mod is not deployed -- so they were GREEN while never exercising the
    # oracle at all. These run the groundtruth path, the one that actually compares.
    Cell("x4live", "oracle --from-groundtruth",
         ["oracle", "--from-groundtruth", _newest_groundtruth()],
         expect=(0, 1, 2), findings_ok=True),
    Cell("x4live", "mappings --from-groundtruth",
         ["mappings", "--from-groundtruth", _newest_groundtruth()],
         expect=(0, 1, 2), findings_ok=True),
    Cell("x4live", "query rejects an unknown verb",
         ["query", "__no_such_verb__", "--timeout", "1"], expect=(1, 2),
         findings_ok=True),
]

#: CLIs named by a cell that this checkout does not ship. NAMED, never silently
#: dropped -- a roster that shrinks without saying so is the defect this gate exists
#: to catch, and it would be embarrassing for the gate to commit it.
_SHIPPED = _shipped_clis()
UNSHIPPED: list[str] = sorted({c.tool for c in _ALL_CELLS} - _SHIPPED)
CELLS: list[Cell] = [c for c in _ALL_CELLS if c.tool in _SHIPPED]


def run(cell: Cell) -> Cell:
    # encoding= is load-bearing: the tools print U+2190 ("<-") in provenance
    # columns, and Windows' default cp1252 raises UnicodeDecodeError mid-read,
    # silently yielding an empty capture that looks like "the tool printed
    # nothing". Anyone piping these tools on Windows hits the same thing.
    # env-ok: this INHERITS the ambient environment to hand to a child process, it
    # does not RESOLVE configuration. Going through `_paths` here would be wrong:
    # the child must see the same environment a human running the command would,
    # plus this cell's overrides. Reading a setting still goes through `_env`.
    env = {**os.environ, **cell.env} if cell.env else None
    proc = subprocess.run(["uv", "run", cell.tool, *cell.argv], cwd=ROOT,
                          capture_output=True, text=True, timeout=1800,
                          encoding="utf-8", errors="replace", env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    cell.out = out
    tb = "Traceback (most recent call last)" in out
    if tb:
        cell.status, cell.detail = "RED", "unhandled traceback"
    elif proc.returncode not in cell.expect:
        cell.status = "RED"
        cell.detail = f"exit {proc.returncode}, expected {cell.expect}"
    elif cell.wants and cell.wants not in out:
        cell.status, cell.detail = "YELLOW", f"no {cell.wants!r} in output ({len(out)}B)"
    elif len(out.strip()) == 0:
        cell.status, cell.detail = "YELLOW", "no output at all"
    else:
        cell.status, cell.detail = "GREEN", f"exit {proc.returncode}, {len(out)}B"
    return cell



# --------------------------------------------------------------------------- #
# COVERAGE: does this sweep actually exercise EVERY subcommand it claims to?
# --------------------------------------------------------------------------- #
#
# THE DEFECT THIS CLOSES. The docstring at the top of this file says "EVERY tool x
# EVERY subcommand". CELLS is a hand-maintained literal and nothing compared the two,
# so the claim was aspiration, not fact. MEASURED 2026-08-30: 29 of 43 subcommands had
# a cell. It was found by accident -- a new `x4live harvest` subcommand was added and
# the sweep reported GREEN 58 / RED 0 having never run it.
#
# The exclusions below are not the defect. Some subcommands genuinely should not run in
# a sweep: they rebuild a store for minutes, call the Nexus API, or mutate the registry
# the sweep is inspecting. The defect was that NOTHING RECORDED WHICH, so a genuinely
# missed subcommand was indistinguishable from a considered omission -- exactly the
# shape `scripts/verify-port.py` already refuses with its ALLOW literal.

#: Subcommands deliberately NOT swept, each with the reason it is excluded. Anything
#: else uncovered is a FINDING, not a default. Keep the reason attached: an entry nobody
#: can justify is how an allow-list stops being a decision and starts being an excuse.
UNSWEPT: dict[tuple[str, str], str] = {
    # Subcommands deliberately NOT swept, each with a reason that must still be true.
    #
    # ⚠ THIS LIST HELD NINE DEAD ENTRIES UNTIL 2026-09-05, AND A DEAD ENTRY IS A
    # SECOND GATE. `check_coverage` counts `s in covered` OR `(cli, s) in UNSWEPT`,
    # so an entry naming a subcommand that HAS a cell silently absorbs the loss of
    # that cell -- including for both artifact builders.
    #
    # The nine were excluded because this branch lacked Cell.slow, Cell.env and
    # _sandbox_dir, and the note said in as many words: "This entry DIES AT MERGE".
    # The merge happened, the infrastructure is here, every one of the nine has a
    # working cell -- and the entries stayed. MEASURED 2026-09-05 with the gate's own
    # rule (`covered[c.tool].update(c.argv)`): 9 of 10 dead, only `x4live archive`
    # genuinely unswept.
    #
    # A note that describes a FUTURE action is a note nobody re-reads. If an entry
    # here is ever conditional on something changing, the condition belongs in a
    # test, not in prose.
    # The two GAME-STATE writes. Their read half, `pausestate`, IS swept.
    ("x4live", "pause"):
        "a WRITE to the running game: with the game up a cell would pause the user's "
        "session, and without it the cell exits 2 and proves nothing. Every exit code "
        "and refusal is covered offline by tests/test_livecli.py, "
        "tests/test_livepipe_e2e.py and the lupa suite in tests/test_modlua_rearm.py.",
    ("x4live", "unpause"):
        "a WRITE to the running game: with the game up a cell would unpause a session "
        "the user may have paused, and without it the cell exits 2 and proves nothing. "
        "Covered offline by tests/test_livecli.py, tests/test_livepipe_e2e.py and "
        "tests/test_modlua_rearm.py.",
}


def _subcommands(cli: str) -> list[str] | None:
    """The subcommands argparse lists for *cli*, or None if we could not ask.

    None is a NON-ANSWER and is reported as one -- never folded into "covered". A
    coverage check that cannot tell "this CLI has no subcommands" from "we failed to
    ask" is the defect it exists to catch.

    ⚠ Takes the choices block on its OWN LINE, not the first `{a,b}` in --help. An
    earlier attempt at this measurement read x4validate's `--tier {a,b}` as two
    subcommands and reported 0/2 coverage for a CLI that has no subcommands at all.
    """
    try:
        r = subprocess.run(["uv", "run", cli, "--help"], capture_output=True,
                           text=True, timeout=120, cwd=ROOT)
    except (subprocess.TimeoutExpired, OSError):
        # silent-ok: NOT swallowed -- None is the channel. check_coverage()
        # renders it as 'coverage is UNKNOWN (not assumed complete)', which is
        # the whole point: a CLI we could not ask must never be folded into
        # 'nothing missing'. Pinned by
        # test_a_CLI_whose_help_cannot_be_READ_is_UNKNOWN_not_COMPLETE.
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"^\s*\{([a-z0-9_,-]+)\}\s*$", r.stdout or "", re.M)
    return m.group(1).split(",") if m else []


def check_coverage() -> tuple[list[str], list[str]]:
    """Compare the subcommands each CLI advertises against the cells that run them.

    Returns (findings, notes). A finding is an uncovered subcommand with no UNSWEPT
    entry, and it fails the gate.
    """
    findings: list[str] = []
    notes: list[str] = []
    covered: dict[str, set[str]] = {}
    for c in CELLS:
        # ANY token in the argv counts, wherever it sits. Several cells pass a flag
        # first (`--registry <sandbox> dashboard`), so assuming argv[0] is the
        # subcommand under-counts -- it reported x4modlist as 0/12 when it is 4/12.
        covered.setdefault(c.tool, set()).update(c.argv)

    total = swept = 0
    for cli in sorted(covered):
        subs = _subcommands(cli)
        if subs is None:
            notes.append(f"{cli}: could not read --help, so coverage is UNKNOWN "
                         f"(not assumed complete)")
            continue
        if not subs:
            continue                      # a single-command CLI; nothing to enumerate
        for s in subs:
            total += 1
            if s in covered[cli]:
                swept += 1
            elif (cli, s) in UNSWEPT:
                swept += 1
            else:
                findings.append(f"{cli} {s}: no cell and no UNSWEPT reason")
    notes.insert(0, f"subcommand coverage: {swept}/{total} "
                    f"({len(UNSWEPT)} deliberately unswept, each with a reason)")
    return findings, notes

def main() -> int:
    # Make the OUTPUT stream able to carry what the cells produce, before printing
    # anything. The cells already capture with encoding="utf-8", errors="replace", so
    # `cell.out` legitimately holds characters like U+2190; printing one to Windows'
    # default cp1252 stdout raises UnicodeEncodeError. Reported by a peer session and
    # confirmed by differential run: the gate died MID-REPORT, after the verdict lines,
    # leaving a partial report plus a traceback instead of a summary -- and it fires
    # exactly when a cell has already failed, which is the worst possible moment.
    #
    # Fixed here rather than at the one line that happened to crash: the same shape sits
    # on four print paths (the per-cell line, the VERBOSE tail, the RED detail and its
    # captured output), so patching the crash site would have left three live.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # silent-ok: console encoding shim; a failure here means the
                  # stream keeps its default codec, which is the pre-fix behaviour
    run_slow = "--all" in sys.argv
    cells = [c for c in CELLS if run_slow or not c.slow]
    skipped = [c for c in CELLS if c.slow and not run_slow]
    print(f"QA sweep - {len(cells)} cells across "
          f"{len({c.tool for c in cells})} tools" + chr(10) + "=" * 78)
    # NAME what was left out. A sweep that silently runs a subset and prints a
    # clean total is this repository's founding defect, and a gate is not exempt
    # from it because the exclusion happened to be deliberate.
    if UNSHIPPED:
        print(f"  NOT IN THIS BUILD: {len(_ALL_CELLS) - len(CELLS)} cell(s) name "
              f"CLI(s) this checkout does not ship: {', '.join(UNSHIPPED)}")
        print("=" * 78)
    if skipped:
        print(f"  SKIPPED {len(skipped)} slow cell(s) - pass --all to include them:")
        for c in skipped:
            print(f"    {c.tool} {c.label}")
        print("=" * 78)
    reds, yellows = [], []
    for cell in cells:
        try:
            run(cell)
        except subprocess.TimeoutExpired:
            cell.status, cell.detail = "RED", "TIMEOUT (>30min)"
        except Exception as exc:                       # harness must never mask a tool bug
            cell.status, cell.detail = "RED", f"harness error: {exc!r}"
        mark = {"GREEN": "  ok ", "YELLOW": " WARN", "RED": " FAIL"}[cell.status]
        print(f"{mark}  {cell.tool:<13} {cell.label:<26} {cell.detail}")
        if cell.status == "RED":
            reds.append(cell)
        elif cell.status == "YELLOW":
            yellows.append(cell)
        if VERBOSE and cell.status != "GREEN":
            for line in cell.out.splitlines()[-12:]:
                print(f"          | {line}")

    cov_findings, cov_notes = check_coverage()
    print("=" * 78)
    for n in cov_notes:
        print(f"  {n}")
    for f in cov_findings:
        print(f"  UNCOVERED  {f}")
    print(f"GREEN {len(cells) - len(reds) - len(yellows)}   "
          f"YELLOW {len(yellows)}   RED {len(reds)}   "
          f"UNCOVERED {len(cov_findings)}"
          + (f"   SKIPPED {len(skipped)} (slow)" if skipped else ""))
    for c in reds:
        print(f"\nRED  {c.tool} {c.label}: {c.detail}")
        for line in c.out.splitlines()[-14:]:
            print(f"     | {line}")
    for c in yellows:
        print(f"\nWARN {c.tool} {c.label}: {c.detail}")
    return 1 if (reds or cov_findings) else (3 if yellows else 0)


if __name__ == "__main__":
    raise SystemExit(main())
