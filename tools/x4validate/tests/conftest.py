"""Shared test helpers.

`import_gate` exists because of a real CI failure on the v2.4.0 push: a gate
module imported at test-module scope KILLED THE WHOLE RUN on a machine with no X4
installed.

Every module under `gates/` resolves its paths at IMPORT time (`EXT = _env.extensions()`
and friends — MEASURED: 13 of them do), and `gates/_env.py::skip` reports a missing
install by `raise SystemExit(2)`. That is correct for a gate, which is a script.
But a `SystemExit` during pytest COLLECTION is an INTERNALERROR: pytest aborts the
entire session with exit 3 rather than failing one module. Three test files import
gate modules, so all three were affected; only the first showed up, because
collection stops at the first internal error.

It was invisible locally because unsetting `$X4_*` is NOT the same as having no
game installed — `_paths` still resolved through `x4-paths.env` and the
real install. Only a genuinely clean machine (CI) could surface it.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

#: STOP CREATING STALE BYTECODE. MEASURED 2026-09-12: a mutation run that writes a
#: SAME-LENGTH mutant and restores it within the same mtime SECOND leaves the
#: MUTANT's .pyc in place -- CPython invalidates on (mtime in seconds, size), so the
#: source reads correct, `cmp` says byte-identical, and the next import executes the
#: mutant. Four mutation verdicts were reported before it was caught.
#:
#: This is the PREVENTION half; `tests/test_no_stale_bytecode.py` is the DETECTION
#: half, for caches written by gate runs outside pytest. Both exist because one
#: stops the suite manufacturing the hazard and the other refuses when it already
#: exists. `gates/mutation_probe.py` clears __pycache__ for the same reason and says
#: so in capitals -- the defect was reproduced anyway by hand-rolling a harness, so
#: a fourth written warning was never going to be the fix.
#: ⚠ WHAT THIS DOES NOT COVER, measured rather than assumed. It cannot prevent
#: conftest's OWN .pyc (pytest compiles this file before the line below runs, into
#: tests/__pycache__), nor `tests/`, `scripts/` or `.claude/hooks/` caches written
#: outside this process, nor a gate run via `uv run python gates/...`. The first
#: version of this comment claimed the detection test covered all of those; it did
#: not (review, 2026-09-13). The real boundary is PYTHONDONTWRITEBYTECODE=1 in
#: run-gates.sh, verify-cold.sh and ci.yml; this line is belt-and-braces for the
#: in-process case only.
sys.dont_write_bytecode = True
os.environ["X4_HOUSEKEEP_SWEEP"] = "off"  # every subprocess test inherits safe session-start behavior

GATES = Path(__file__).resolve().parent.parent / "gates"


def pytest_sessionstart(session):
    """Receipt only for this toolkit's newly created pytest base, never its shared parent."""
    toolkit = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(toolkit / "scripts"))
    from x4temp import mark_owned_temp
    mark_owned_temp(session.config._tmp_path_factory.getbasetemp(), toolkit)


def import_gate(name: str, *, module_level: bool = True):
    """Import a module from `gates/`, or SKIP.

    At module scope::

        cross_tool = import_gate("cross_tool")

    Inside a test, where only that test depends on the gate::

        qa_sweep = import_gate("qa_sweep", module_level=False)

    `module_level` must be False inside a function -- `allow_module_level=True` is
    only legal during collection. Importing inside the test body does NOT avoid the
    problem, it just moves it: the SystemExit becomes a test FAILURE instead of a
    collection abort, and a machine with no X4 install is not a broken toolkit.

    A skip, never a silent pass: pytest reports it distinctly, so "this machine has
    no X4 install" can never read as "the invariants hold".
    """
    if str(GATES) not in sys.path:
        sys.path.insert(0, str(GATES))
    try:
        return importlib.import_module(name)
    except (SystemExit, TypeError, OSError) as exc:
        # A gate can fail to import for TWO reasons on an unconfigured machine, and
        # only one of them is a SystemExit:
        #   gates/_env.py::skip          -> SystemExit(2)      (the documented path)
        #   Path(_paths.<thing>())       -> TypeError          (None is not a PathLike)
        # `gates/claims_audit.py` does the latter at module scope, and CI caught it
        # one push after the SystemExit case was fixed.
        #
        # The skip is CONDITIONED on the environment actually being unresolvable, not
        # on the exception type. A blanket catch would silently swallow a real
        # TypeError in a gate on a properly configured machine -- turning a defect
        # into a green skip, which is the exact inversion this suite exists to stop.
        why = _environment_unresolvable_reason()
        if why is None:
            raise
        reason = (f"gates/{name}.py needs a usable X4 install -- {why} "
                  f"({type(exc).__name__} at import: {exc}). "
                  f"Set $X4_GAME / $X4_EXTENSIONS, or see x4-paths.env.")
        if module_level:
            pytest.skip(reason, allow_module_level=True)
        pytest.skip(reason)


# ------------------------------------------------------ gates against a FAKE install
#
# `import_gate` SKIPS on a machine with no X4. That is right for a test that needs the
# real corpus, and WRONG for a test of a gate's own decision logic: that logic runs the
# same on every machine, and the only thing stopping it is the gate resolving its paths
# at IMPORT. MEASURED 2026-09-28 (v3.3.0 windows CI): 16 of 69 skips were such tests,
# every one new in that release, and together they pushed the job over X4_MAX_SKIPS.
# A skip is a test that did not run -- CI never executed that logic once.
#
# `hermetic_gate` / the `gate_install` fixture give the gate a CONFIGURED install that is
# fake: empty tmp roots, handed over through the ENVIRONMENT layer of `_paths`, so the
# gate's REAL `_env` resolution runs (nothing inside `_env` is stubbed). The env layer
# outranks `x4-paths.env`, so a configured machine resolves to the same fake
# roots and the test means the same thing cold and warm. The gate is loaded from
# source under a PRIVATE module name, never the cached `import_gate` copy, which on a
# configured machine was imported against the REAL install.

#: The locations gates/_env.py resolves through `_paths` at CALL time, each given a fake.
#: NOT covered: `_env.registry_file()`, which reads `_registry.DEFAULT_REGISTRY` -- a
#: snapshot taken when `_registry` was first imported, so no environment set now can
#: reach it. A test that needs the registry patches that attribute itself (as
#: test_gates_store_one_door.py's `elsewhere` does).
_FAKE_INSTALL_ENV = ("X4_GAME", "X4_EXTENSIONS", "X4_REFERENCE", "X4_MODS",
                     "X4_REGISTRY", "X4_EFFECTIVE_DB", "X4_ORACLE_LOG")


def make_fake_install(root: Path):
    """Build empty-but-valid roots under *root*: each passes `_env`'s own existence check
    (a dir is a dir, a file is a file) and contains nothing. Returns a namespace of the
    paths plus `env`, the variables that point `_paths` at them."""
    import types
    game = root / "game"
    ext = game / "extensions"
    ref = root / "reference"
    mods = root / "mods"
    for d in (ext, ref, mods):
        d.mkdir(parents=True, exist_ok=True)
    reg = mods / "modlist.yaml"
    reg.write_text("mods: []\n", encoding="utf-8")
    db = root / "effective.sqlite"
    db.write_bytes(b"")
    log = root / "debug.txt"
    log.write_text("", encoding="utf-8")
    env = {"X4_GAME": game, "X4_EXTENSIONS": ext, "X4_REFERENCE": ref, "X4_MODS": mods,
           "X4_REGISTRY": reg, "X4_EFFECTIVE_DB": db, "X4_ORACLE_LOG": log}
    assert set(env) == set(_FAKE_INSTALL_ENV)
    return types.SimpleNamespace(root=root, game=game, ext=ext, ref=ref, mods=mods,
                                 registry=reg, db=db, log=log,
                                 env={k: str(v) for k, v in env.items()})


def _exec_gate(name: str, key: str, register):
    """Execute gates/<name>.py as a NEW module called *key*. `register(key, mod)` puts it
    in sys.modules first (a dataclass needs its module there while it executes)."""
    import importlib.util
    if str(GATES) not in sys.path:
        sys.path.insert(0, str(GATES))     # the gate does `import _env`, as import_gate allows
    spec = importlib.util.spec_from_file_location(key, GATES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    register(key, mod)
    spec.loader.exec_module(mod)
    return mod


def hermetic_gate(name: str):
    """Import a gate at MODULE scope against a fake install, for tests of PURE functions.

    The fake roots exist only while the gate imports and are deleted straight after, so
    its module-level paths (`EXT`, `REF`, ...) point at nothing: a pure-function test
    never reads them, and one that did would fail loudly rather than read a real install.
    A test that runs a gate's `main()` wants the `gate_install` fixture instead.
    """
    import importlib
    import tempfile
    # NO LEAK INTO THE SESSION (v3.3.1 release review, MEASURED): this runs at COLLECTION.
    # A package module first imported inside the fake window snapshots the fake roots at
    # import (`_merge.REFERENCE` stuck at a deleted dir), and 5 unrelated tests then failed
    # when this file was collected first. So the modules that snapshot paths are imported
    # BEFORE the fake env is set, and any x4validate module first imported inside the window
    # is dropped afterwards, so a later import re-reads the real configuration.
    for m in ("x4validate._paths", "x4validate._registry", "x4validate._merge",
              "x4validate._effective", "x4validate._freshness"):
        importlib.import_module(m)
    before = set(sys.modules)
    with tempfile.TemporaryDirectory(prefix="x4-fake-install-") as d, \
            pytest.MonkeyPatch.context() as mp:
        inst = make_fake_install(Path(d))
        for k, v in inst.env.items():
            mp.setenv(k, v)
        mod = _exec_gate(name, f"hermetic_gate_{name}",
                         lambda k, m: sys.modules.__setitem__(k, m))
    for k in set(sys.modules) - before:
        if k.startswith("x4validate"):
            del sys.modules[k]
    return mod


@pytest.fixture
def gate_install(tmp_path, monkeypatch):
    """A fake configured install for the whole test, plus `.load(name)` to import a gate
    against it. The environment stays set until teardown, so a gate function that
    resolves through `_env` at CALL time also sees the fake, never the real install.
    Each `.load` is a fresh module, unregistered at teardown."""
    inst = make_fake_install(tmp_path / "fake-install")
    for k, v in inst.env.items():
        monkeypatch.setenv(k, v)
    inst.load = lambda name: _exec_gate(
        name, f"gate_install_{name}", lambda k, m: monkeypatch.setitem(sys.modules, k, m))
    return inst


@pytest.fixture(scope="session", autouse=True)
def _reference_for_unit_tests(tmp_path_factory):
    """Give `_merge` an empty reference directory when the machine has no X4.

    Since 2.5.0 `_merge.Config()` REFUSES when no reference tree is configured,
    rather than falling back to a CWD-relative guess (F39). That is right for the
    CLIs — an unconfigured toolkit must not invent an answer — but it changed the
    result of the SUITE depending on the machine it ran on: 15 unit tests that
    construct a Config only to exercise overlay merging, TierB shape or archive
    metadata began failing on a fresh clone with no X4 installed. A new user's
    first `pytest` would have shown 15 errors about their own machine.

    So the tests that do not care about a reference tree are given one that is
    empty, and ONLY when the machine cannot resolve a real one. On a configured
    machine this is inert.

    It does not weaken anything. The refusal is pinned directly by
    `tests/test_unconfigured_refusal.py`, which sets `_merge.REFERENCE = None`
    itself and is unaffected by a default; and the real proof lives in
    `scripts/verify-cold.sh`, which runs the actual EXECUTABLES on a cold
    checkout and requires exit 2. What this fixture buys is that a test result
    means the same thing on every machine — which is the property a suite is for.
    """
    from x4validate import _merge, _paths
    if _paths.reference() is not None:
        yield
        return
    previous = _merge.REFERENCE
    _merge.REFERENCE = tmp_path_factory.mktemp("empty-reference")
    try:
        yield
    finally:
        _merge.REFERENCE = previous


@pytest.fixture
def case_insensitive_fs(tmp_path):
    """Skip unless the filesystem treats `Thing.xml` and `thing.xml` as one file.

    X4 is a Windows game and its VFS is case-insensitive: a mod patching
    `md/thing.xml` reaches a file shipped as `md/Thing.xml`, and the toolkit
    models that. Tests that stage one case and patch the other are therefore
    asserting real engine behaviour — but on a case-sensitive filesystem they
    stage two DIFFERENT files, so the scenario they describe cannot be built and
    the failure says nothing about the code.

    DETECTED, not assumed from `sys.platform`. macOS defaults to
    case-insensitive and Linux does not, but either can be configured the other
    way, and a platform guess would then skip a test that would have run (or run
    one that cannot pass). The precondition is a property of the filesystem, so
    it is measured on the filesystem.
    """
    if not fs_is_case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive filesystem: this test stages one "
                    "case and patches another, which is a single file under "
                    "X4's Windows VFS but two files here")


def fs_is_case_insensitive(directory) -> bool:
    """Does *directory*'s filesystem fold case? Probed, never assumed.

    Split out of the fixture so both branches are reachable from a test. A
    precondition check that can only ever return one answer on the machine you
    are sitting at is not verified — it is merely unexercised.
    """
    probe = directory / "CaseProbe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        return (directory / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def _environment_is_unresolvable() -> bool:
    """True when this machine has no usable X4 configuration (see the reason function)."""
    return _environment_unresolvable_reason() is not None


def _environment_unresolvable_reason(root: Path | None = None) -> str | None:
    """Why this machine has no usable X4 configuration, or None when it has one.

    Deliberately checks the THREE things the gates actually need, rather than
    trusting any single one: a machine can have a reference tree but no registry,
    and the failure mode differs per gate.
      * any root NOT CONFIGURED                -> unresolvable, in any layout
      * every root configured, some MISSING    -> unresolvable ONLY in an installed layout
    R2-B1: CONFIGURED is not PRESENT on a fresh install -- it configures every root before
    anything exists there (SETUP_PROMPT runs the suite before the unpack), and a gate then
    exits 2 on the missing folder; 12 ERRORs in an installed toolkit. v4.0.0 delta review:
    that branch applied in a CHECKOUT as well, so on a configured dev machine whose root had
    moved, a gate raising TypeError/OSError at import SKIPPED instead of erroring. In a
    checkout a configured root that is missing is a broken configuration, and it must show.
    """
    try:
        from x4validate import _paths
    except ImportError:
        return "x4validate is not importable"
    ref = _paths.reference()
    if ref is not None and not _paths.value("X4_REFERENCE") and not Path(ref).exists():
        ref = None      # only the derived <X4_TOOLKIT>/reference default, never unpacked
    roots = {"registry": _paths.registry(), "game extensions": _paths.game_extensions(),
             "reference": ref}
    unset = [k for k, v in roots.items() if v is None]
    if unset:
        return "not configured: " + ", ".join(unset)
    missing = [k for k, v in roots.items() if not Path(v).exists()]
    if not missing:
        return None
    import _layout
    if _layout.installed_layout(_layout.ROOT if root is None else root):
        return ("configured but not present yet (an installed toolkit before setup/unpack): "
                + ", ".join(missing)
                + _layout.layout_caveat(_layout.ROOT if root is None else root))
    return None


# --------------------------------------------------------------- skip accounting
#
# A skip is a test that did NOT run, and pytest hides them by default: the summary
# says "1159 passed, 14 skipped" and never says WHICH 14 or WHY. That is how 125 tests
# sat skipped for weeks behind a lookup that could not succeed in the public tree --
# a green tick over untested code, guarding a Lua contract whose violation hangs X4.
#
# Two things here, and neither can produce a false failure:
#   * `-rs` in addopts, so every skip is always NAMED with its reason.
#   * a count printed in the summary, plus an OPT-IN ceiling via X4_MAX_SKIPS for a
#     caller that knows its environment (CI). A fixed ceiling would be wrong: a cold
#     machine legitimately skips more than a configured one, and a check that cries
#     wolf is one people learn to ignore.
import os as _os


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    skipped = terminalreporter.stats.get("skipped", [])
    n = len(skipped)
    terminalreporter.write_line("")
    terminalreporter.write_line(
        "skip accounting: %d test(s) did not run. A skip is not a pass; the reasons are "
        "listed above (-rs)." % n)
    cap = _os.environ.get("X4_MAX_SKIPS")
    if cap is None:
        return
    # AN UNUSABLE CEILING IS A FAILURE, NOT A PASS. This branch used to print
    # "that is a NON-ANSWER, not a pass" and then return, leaving the run GREEN --
    # so the sentence was true and the exit code contradicted it. The caller set
    # the variable, i.e. asked for a ceiling; if we cannot apply the one they gave
    # us, the honest outcome is the one the rc contract already specifies for a
    # could-not-check, not the one that looks like agreement.
    #
    # The realistic accident is an EMPTY value, not garbage: a workflow writing
    # `X4_MAX_SKIPS: ${{ ... }}` from an expression that evaluates to nothing sets
    # the variable to "", which is not None, so this path is exactly where a CI
    # leg silently loses its skip ceiling while every log line still says the
    # suite ran with one.
    try:
        cap_n = int(cap)
    except ValueError:
        terminalreporter.write_line(
            "  FAIL: X4_MAX_SKIPS=%r is not a number, so no ceiling could be applied. "
            "A ceiling that was ASKED FOR and could not be applied is a non-answer, "
            "and a non-answer is not a pass." % cap)
        terminalreporter._session.exitstatus = 1
        return
    if cap_n < 0:
        terminalreporter.write_line(
            "  FAIL: X4_MAX_SKIPS=%d is negative, so no run could ever satisfy it. "
            "A ceiling nothing can meet is a broken instrument, not a strict one."
            % cap_n)
        terminalreporter._session.exitstatus = 1
        return
    if n > cap_n:
        terminalreporter.write_line(
            "  FAIL: %d skips exceeds X4_MAX_SKIPS=%d. A test that stopped running is "
            "indistinguishable from one that passed unless something counts them." % (n, cap_n))
        terminalreporter._session.exitstatus = 1


# --------------------------------------------------------------- conformance dump
@pytest.fixture(scope="session")
def conformance_dump():
    """ONE test-hooks.sh dump per session, shared by the Codex and toy conformance tests. The base is
    <repo>/.test-sandbox (the harness's own default, gitignored), never tmp_path: test-hooks.sh refuses
    a sandbox under /tmp, which is where pytest puts tmp_path on Linux. Lazy: only a test that asks
    for it pays for the dump. Without bash and jq it SKIPS (counted), never passes."""
    import importlib.util
    import shutil
    if not (shutil.which("bash") and shutil.which("jq")):
        pytest.skip("needs Git Bash and jq -- the guard corpus cannot be dumped here")
    repo = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("x4conformance", repo / "scripts" / "x4conformance.py")
    xc = importlib.util.module_from_spec(spec); spec.loader.exec_module(xc)
    rows, sbx = xc.dump_cases(repo)
    try:
        yield rows, sbx
    finally:
        xc.remove_sandbox(sbx, repo / ".test-sandbox")
