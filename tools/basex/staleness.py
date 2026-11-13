r"""Does this BaseX index still describe the world it was built from?

`ask.py` already refuses to render a zero-result as a finding without a coverage
denominator. This adds the missing dimension: the denominator must also be
**current**. A stale index is not an absence and not a non-answer — it is an
answer from a world that no longer exists, and until 2026-08-13 nothing here
detected one.

**Why the fingerprint covers CODE and not just DATA.** `x4eff` was built
2026-08-02. The merge engine was then fixed twice: root-`<replace>` ops were being
dropped while reported applied (2026-08-08 — 858 ops, VRO alone 848), and nested
cross-mod patches were invisible from one of two doors (2026-08-11). **Neither
date changed any content.** A checker watching only inputs would have called the
index fresh for eleven days while it served 858 wrong values. So `x4eff` carries
an ENGINE fingerprint as well as a CONTENT one.

Engine identity is hashed from source BYTES, not a git commit: a dirty working
tree does not move a commit hash, and merge changes here are routinely used
before they are committed.

Usage::

    python staleness.py --snapshot FILE --db x4eff            # before a build's first read of the world
    python staleness.py --write --db x4eff --taken-before FILE  # stamp what that build read
    python staleness.py --write --db x4eff                    # stamp the world NOW (by hand: says nothing about a build)
    python staleness.py --check --db x4eff                    # exit 5 if stale

Why two steps: a fingerprint taken at the END of a build describes the world after it, so a mod changed while it ran
read FRESH for ever over values from before the change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASEX_DIR = HERE / "basex"

# NOTE: there is deliberately NO local copy of the engine-source list here.
# There used to be, and it was DEAD -- defined, never read, because hashing has
# always delegated to `_freshness.hash_engine` via `_core()`. A dead copy is
# worse than no copy: the next person to extend the real list would reasonably
# edit this one and change nothing, or edit the real one and leave this one
# quietly lying about what the fingerprint covers. Same shape as the x4similar
# weights (F8) -- two tables, nothing tying them together.
# The one list lives in `x4validate/_freshness.py::ENGINE_SOURCES`.

#: Databases built from the merged tree, i.e. the ones an engine change invalidates.
ENGINE_DEPENDENT = frozenset({"x4eff"})


class EngineUnavailable(RuntimeError):
    """x4validate could not be imported, so freshness cannot be computed.

    Neither FRESH nor STALE — UNANSWERED. Raised as its own type so the CLI can
    say that plainly instead of dying on a ModuleNotFoundError traceback, which
    tells the reader nothing about which of the three states they are in.
    """


def _core():
    """The ONE implementation, in x4validate. Delegating rather than copying is
    the whole lesson of this register: the DLC-enumeration bug was written five
    times because every caller rolled its own. Falls back to nothing -- if the
    package is unavailable the caller cannot judge freshness and must say so."""
    try:
        from x4validate import _freshness
    except ImportError as exc:
        raise EngineUnavailable(str(exc)) from exc
    return _freshness


def _hash_engine(engine_dir: Path) -> str:
    return _core().hash_engine(engine_dir)


def _hash_content(reference: Path, extensions: Path) -> str:
    return _core().hash_content(reference, extensions)


#: Build code OUTSIDE the package that still shapes an engine-dependent database. The
#: package's `ENGINE_SOURCES` cannot name it (it hashes package files), so before
#: 2026-09-24 an edit to how `x4eff` writes or copies documents left x4eff FRESH
#: (AUDIT-2026-09-24 FR-2). Folded into the engine axis for THAT database only: the
#: sqlite store never runs this script, so its hash must not move when it changes.
ENGINE_EXTRA = {"x4eff": (HERE / "build-effective.py",)}


def _engine_for(engine_dir: Path, db: str) -> str:
    base = _hash_engine(engine_dir)
    extra = ENGINE_EXTRA.get(db, ())
    if not extra:
        return base
    h = hashlib.sha256(base.encode())
    for p in extra:
        h.update(p.name.encode())
        h.update(p.read_bytes() if p.is_file() else b"<ABSENT>")
    return h.hexdigest()[:16]


def fingerprint(reference: Path, extensions: Path, engine_dir: Path, db: str = "") -> dict:
    """The two axes, and DELIBERATELY not the per-folder vector.

    `_freshness.fingerprint` also carries `detail` — the 190 KB vector that lets
    `x4modlist changed` name which mod moved. It is NOT stamped here on purpose:
    `ask.py` re-reads `coverage-<db>.json` on EVERY query to decide whether a
    negative claim is admissible, and inflating that file ~40x would tax the hot
    path to duplicate a baseline the sqlite store and `x4modlist snapshot`
    already provide. The content HASH is delegated (`_hash_content`), so the two
    still agree about *whether* the world moved; only the localisation lives
    elsewhere.
    """
    return {"content": _hash_content(reference, extensions),
            "engine": _engine_for(engine_dir, db)}


@dataclass
class Verdict:
    fresh: bool
    reasons: list[str] = field(default_factory=list)
    db: str = ""
    #: False when freshness could not be EVALUATED at all (the engine is not
    #: importable, or its paths do not resolve). Distinct from `fresh=False`,
    #: which is a positive finding that the world moved. Collapsing the two would
    #: print "no longer describes the current world" — an assertion — when the
    #: honest statement is "nobody could check". Absence, non-answer and a
    #: superseded world are three states, and this register exists to keep them
    #: apart; a banner that says STALE when it means UNKNOWN sends the reader to
    #: rebuild an index that may have been perfectly current.
    determinable: bool = True

    def banner(self) -> str:
        """The text `ask.py` prints on EVERY query until the index is rebuilt."""
        if self.fresh:
            return ""
        if not self.determinable:
            return ("\n" + "!" * 78
                    + f"\n!! FRESHNESS UNKNOWN — {self.db} could not be CHECKED.\n!!   "
                    + "\n!!   ".join(self.reasons)
                    + "\n!! This is not 'stale' and not 'fresh'. Nobody established which."
                    # THE PRESCRIPTION MUST MATCH THE DIAGNOSIS. This said only
                    # "run it through the project environment", which is the fix for
                    # exactly ONE of the four causes — the engine not being importable.
                    # For the other three (no coverage report, an unreadable one, one
                    # with no fingerprint) the action is to BUILD the index, which the
                    # STALE banner below does say and this one had stopped saying.
                    # `reasons` still names the cause, so it was recoverable — but a
                    # reader who acts on the instruction rather than on the reason does
                    # the wrong thing, and the instruction is the louder of the two.
                    + "\n!! If the reason above is that the ENGINE is not importable:"
                    + "\n!!   cd tools/x4validate && uv run python ../basex/ask.py ..."
                    + "\n!! If it is a MISSING, UNREADABLE or UNSTAMPED coverage report,"
                    + "\n!! the index has not been built or not been stamped:"
                    + "\n!!   cd tools/basex && bash build-corpus.sh ; bash build-effective.sh   # ; not && : corpus exits 3 on accounted exclusions"
                    + "\n!! Until then this index cannot back a NEGATIVE claim.\n"
                    + "!" * 78 + "\n")
        return ("\n" + "!" * 78
                + f"\n!! STALE INDEX — {self.db} no longer describes the current world.\n!!   "
                + "\n!!   ".join(self.reasons)
                + "\n!! Rebuild:  cd tools/basex && bash build-corpus.sh ; bash build-effective.sh   # ; not && : corpus exits 3 on accounted exclusions"
                + "\n!! Until then this index cannot back a NEGATIVE claim, and its\n"
                  "!! positive answers describe the world as of the build, not now.\n"
                + "!" * 78 + "\n")


def check(coverage_path: Path, reference: Path, extensions: Path,
          engine_dir: Path, db: str = "") -> Verdict:
    db = db or coverage_path.stem.replace("coverage-", "")
    # determinable=False ON ALL THREE OF THESE. The field, its docstring and the
    # UNKNOWN banner all existed; check() simply never set it, so its only
    # producer was ask.py own except-clause. An absent report, an unreadable one
    # and a report with no fingerprint each mean "nobody established which", and
    # each printed STALE — an ASSERTION that the world has moved, made
    # about a world nothing looked at. It also sends the reader off to rebuild an
    # index that may have been perfectly current.
    if not coverage_path.is_file():
        return Verdict(False, [f"no coverage report at {coverage_path}"], db,
                       determinable=False)
    try:
        data = json.loads(coverage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Verdict(False, [f"coverage report unreadable ({exc})"], db,
                       determinable=False)

    stored = data.get("fingerprint")
    if not stored:
        # Absent is UNKNOWN, never fresh. Every coverage file written before
        # 2026-08-13 lacks this field, and those are precisely the databases
        # whose staleness prompted the check.
        return Verdict(False, ["no fingerprint recorded — built before staleness "
                               "tracking existed, so freshness cannot be established"], db,
                       determinable=False)

    try:
        now = fingerprint(reference, extensions, engine_dir, db)
    except (EngineUnavailable, ImportError):
        # NOT OURS TO CATCH. `main()` owns this one and turns it into rc 6 via
        # `_report_unknown`; swallowing it here returned a determinable=False
        # Verdict that `main` then rendered as rc 5 = STALE, reintroducing the
        # exact confusion this change exists to remove. Caught by
        # test_unimportable_engine_reports_UNKNOWN_not_a_traceback, which was
        # written for that failure and went red on the first draft of this fix.
        raise
    except Exception as exc:
        # The COMPARISON side can fail too, and it fails on a CONFIGURED
        # machine whose roots have moved — not only on a cold checkout,
        # which is the one case ask.py caller-side guard covers. With no `now`,
        # the stored stamp has nothing to be compared against, and that is the
        # definition of UNKNOWN rather than of stale.
        return Verdict(False, [f"the current fingerprint could not be computed "
                               f"({type(exc).__name__}: {exc}), so the stored "
                               f"one has nothing to be compared against"], db,
                       determinable=False)
    reasons = []
    if stored.get("content") != now["content"]:
        reasons.append("content changed: a mod was added, removed, updated, "
                       "toggled, or one of its files was edited "
                       "(run `x4modlist changed` to see which)")
    if db in ENGINE_DEPENDENT and stored.get("engine") != now["engine"]:
        reasons.append("engine changed: the merge code that produced this tree has "
                       "been edited, so the SAME inputs would now merge differently")
    return Verdict(not reasons, reasons, db)


class SnapshotUnusable(ValueError):
    """A stamp was told to use a pre-build fingerprint that is not there, is damaged, or names another database."""


def snapshot(path: Path, reference: Path, extensions: Path, engine_dir: Path, db: str) -> dict:
    """Record the fingerprint BEFORE a build reads anything; `write(taken_before=path)` stamps it afterwards."""
    fp = fingerprint(reference, extensions, engine_dir, db)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"db": db, "fingerprint": fp}), encoding="utf-8")
    os.replace(tmp, path)
    return fp


def _taken_before(path: Path, db: str) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        fp = doc["fingerprint"]
        if doc["db"] != db or not fp.get("engine"):
            raise ValueError(f"it is for {doc['db']!r}, or has no engine axis")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SnapshotUnusable(f"pre-build fingerprint {path} is unusable ({exc})") from exc
    return fp


def write(coverage_path: Path, reference: Path, extensions: Path,
          engine_dir: Path, taken_before: Path | None = None, db: str = "") -> dict:
    """Stamp *coverage_path* in place, preserving every existing key.

    Additive on purpose: `status` and `supports_negative_claim` are what make a
    negative admissible at all, and a stamp that dropped them would trade one
    guarantee for another.

    `taken_before`: stamp the snapshot of the world the build read, and use it up (it describes that build and no
    later one; this process's own hand-off file). Never recomputed if unusable: that is the defect it exists to stop.

    `db`: the database the fingerprint is FOR, when the report is not named after it. A build-then-swap build
    (dbswap.py) reconciles `coverage-x4eff__next.json`, but the world it read is x4eff's: the snapshot was taken
    for x4eff and x4eff's engine axis includes build-effective.py (ENGINE_EXTRA), which `x4eff__next` would miss.
    """
    db = db or coverage_path.stem.replace("coverage-", "")
    fp = _taken_before(taken_before, db) if taken_before else fingerprint(reference, extensions, engine_dir, db)
    data = {}
    if coverage_path.is_file():
        try:
            data = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["fingerprint"] = fp
    coverage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if taken_before:
        Path(taken_before).unlink(missing_ok=True)
    return fp


def _defaults() -> tuple[Path, list[Path], Path]:
    """Resolve reference/extensions/engine, or RAISE.

    These used to fall back to one developer's absolute paths. On anyone else's
    machine that silently resolved to a directory that does not exist, and the
    fingerprint was then computed over nothing -- a guessed default wearing the
    grammar of a measured one, which is the defect this toolkit exists to stop.
    (It also shipped a username.)

    `x4validate._paths` is the ONE resolver (env var -> x4-paths.env ->
    project-relative). Delegating rather than copying is the same lesson as
    `_core()` above: the DLC-enumeration bug was written seven times because
    every caller rolled its own.
    """
    # Read the environment through `_paths`, not around it. Checking os.environ
    # FIRST and only then falling back was redundant — `_paths` already tries the
    # real environment before the config file — and it re-created the two-door
    # shape: the same variable resolved by two rules that could diverge.
    try:
        from x4validate import _paths
    except ImportError as exc:  # noqa: BLE001
        raise EngineUnavailable(
            f"cannot resolve paths: x4validate is not importable ({exc}), and "
            f"$X4_REFERENCE/$X4_EXTENSIONS are not both set") from exc
    reference = _paths.reference()
    extensions = _paths.game_extensions()
    missing = [n for n, v in (("reference", reference), ("extensions", extensions)) if v is None]
    if missing:
        raise EngineUnavailable(
            f"cannot resolve {' and '.join(missing)}. Set $X4_REFERENCE / $X4_EXTENSIONS, "
            f"or configure x4-paths.env. Refusing to guess: a fingerprint taken "
            f"over a path that does not exist reports FRESH forever.")
    # RESOLVING is not EXISTING, and only the second one makes a fingerprint mean
    # anything. `_paths` returns a Path for any non-empty setting, so until now a typo
    # or a moved install was accepted and then hashed over nothing.
    #
    # MEASURED 2026-09-02: two DIFFERENT nonexistent trees both fold to
    # fb2018359186a6be, and so does an EMPTY directory -- `_fold` writes
    # `<NO-EXTENSIONS-DIR>` for an empty vector, which is exactly the collision. An
    # index stamped in that state reads FRESH against any other broken world forever,
    # and `ask.py` gates `NEGATIVE CONFIRMED over N of M documents` on that verdict. The
    # headline safety property -- a negative needs a denominator AND a freshness stamp --
    # was defeated in the one direction that produces a confident wrong answer.
    #
    # Reachable without contrivance: install.sh writes X4_REFERENCE and then tells the
    # user to run bin/unpack-reference.sh to CREATE that directory, so the documented
    # post-install state points at a tree that is not there yet.
    #
    # `libraries/wares.xml` is the probe because it is already `_fold`'s own reference
    # marker -- the same file whose absence silently drops the reference axis from the
    # digest -- so this refuses precisely when the fold would have gone blind.
    if not (Path(reference) / "libraries" / "wares.xml").is_file():
        raise EngineUnavailable(
            f"the reference tree at {reference} has no libraries/wares.xml, so there is "
            f"nothing to fingerprint. Unpack it with bin/unpack-reference.sh, or point "
            f"$X4_REFERENCE at a tree that exists. Refusing to guess: every missing tree "
            f"folds to the same digest, which reads as FRESH forever.")
    # An EMPTY extensions directory is a legitimate state and stays accepted -- it means
    # no mods, and it fingerprints honestly. A MISSING one is refused, because the fold
    # cannot tell it apart from the empty case and would call a broken path fresh.
    if not Path(extensions).is_dir():
        raise EngineUnavailable(
            f"the extensions directory at {extensions} does not exist. Point "
            f"$X4_EXTENSIONS at your game's extensions/ folder. Refusing to guess: a "
            f"missing directory folds to the same digest as an empty one, so a broken "
            f"path would read as FRESH forever. An empty extensions/ is fine.")
    # env-ok: $X4VALIDATE_DIR points at a CHECKOUT of the engine source, for
    # fingerprinting. It is a developer override for where this repo lives, not a
    # user-configured X4 location, and `_paths`' layers deliberately cover only
    # `X4_`-prefixed settings — routing it there would widen that contract.
    engine = Path(os.environ.get(
        "X4VALIDATE_DIR", str(HERE.parent / "x4validate"))) / "x4validate"
    # VALIDATED, like reference and extensions above, for the reason written there:
    # "a fingerprint taken over a path that does not exist reports FRESH forever."
    #
    # That sentence used to be false where it stood. When this check was added, the two
    # axes it claimed to be copying tested only that their paths RESOLVED -- so the
    # comment asserted a validation that did not exist, and a reader (including the next
    # reviewer) had no reason to doubt it. Both are real checks as of 2026-09-02. MEASURED
    # 2026-09-02: `hash_engine` over a missing tree hashes seven fixed names plus seven
    # <ABSENT> markers, so EVERY nonexistent path folds to the SAME constant --
    # 2e797cf6683200c5 -- and `check()` then reports the artifact fresh against any of
    # them, forever. The `<ABSENT>` marker distinguishes ONE missing file, which is what
    # it was written for; it cannot distinguish "I hashed nothing".
    #
    # Reachable by ordinary misconfiguration: X4VALIDATE_DIR is documented as the
    # engine CHECKOUT, this line appends "/x4validate" to it, and pointing it at the
    # PACKAGE directory -- which the variable's name invites -- yields
    # .../x4validate/x4validate/x4validate, which does not exist. `cd` into the package
    # dir succeeds and `uv run` works, so nothing else in the build notices.
    if not (engine / "_merge.py").is_file():
        raise EngineUnavailable(
            f"the engine tree at {engine} has no _merge.py, so there is nothing to "
            f"fingerprint. Set X4VALIDATE_DIR to an x4validate CHECKOUT (the directory "
            f"CONTAINING the x4validate package), not the package itself. Refusing to "
            f"guess: every missing tree hashes to the same constant, which reads as "
            f"FRESH forever.")
    # EVERY INSTALL ROOT, not just the game's (AUDIT-2026-09-24 FR-6). x4raw stages every
    # INSTALLED mod and x4eff every ACTIVE one, both through `_registry`, which walks up to
    # three roots (game-root extensions, the profile's, the Steam workshop dir) -- while
    # this stamp covered only the first, so a mod added or edited in the other two left
    # both databases FRESH. The same fix the sqlite store (`_effective._ext_root`) and
    # x4xref (`_xref.index_roots`) carry. `hash_content` takes one root or many; an absent
    # candidate root is skipped by `content_detail`, so a machine with only the game root
    # fingerprints exactly as before.
    from x4validate import _registry
    roots = [Path(extensions)]
    for extra in _registry.default_installed_dirs():
        if Path(extra) not in roots:
            roots.append(Path(extra))
    return reference, roots, engine


def _report_unknown(db: str, exc: Exception) -> int:
    """Freshness could not be DETERMINED. Distinct from fresh and from stale.

    One implementation, because there are two ways to get here — the engine is not
    importable, or its paths do not resolve — and two copies of this message would
    be two chances to describe the same state differently. The exception text is
    quoted rather than assumed, so the reader is told WHICH of the two it was
    instead of a guess that was right only for the case its author had in mind.
    """
    print(f"cannot determine freshness for {db}: {exc}", file=sys.stderr)
    print("  This is UNKNOWN, not fresh and not stale — do not treat a clean "
          "exit as a clean index.", file=sys.stderr)
    print("  Run it through the project environment:", file=sys.stderr)
    print("    cd tools/x4validate && uv run python ../basex/staleness.py "
          f"--check --db {db}", file=sys.stderr)
    return 6


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="staleness",
        description="Does a BaseX index still describe the current world?")
    p.add_argument("--db", required=True, help="x4raw | x4eff")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="stamp after a build")
    mode.add_argument("--snapshot", metavar="FILE", help="record the fingerprint here, before a build reads anything")
    mode.add_argument("--check", action="store_true", help="exit 5 if stale")
    p.add_argument("--coverage", help="path to coverage-<db>.json")
    p.add_argument("--taken-before", metavar="FILE",
                   help="with --write: stamp this --snapshot, the world the build read, instead of the world now")
    args = p.parse_args(argv)
    if args.taken_before and not args.write:
        p.error("--taken-before goes with --write")

    # `_defaults()` is the EARLIEST thing that can fail, and until 2026-08-24 it sat
    # outside every handler below — so an unconfigured machine got a raw traceback
    # and rc 1 ("your thing has findings") instead of rc 6 ("this is UNKNOWN"). The
    # sibling test monkeypatched `_core`, which fails INSIDE the guarded `check()`
    # call, so it passed honestly while never reaching this line. F44's family.
    try:
        reference, extensions, engine = _defaults()
    except (EngineUnavailable, ImportError) as exc:
        return _report_unknown(args.db, exc)
    cov = Path(args.coverage) if args.coverage else BASEX_DIR / f"coverage-{args.db}.json"

    if args.snapshot:
        try:
            snapshot(Path(args.snapshot), reference, extensions, engine, args.db)
        except (EngineUnavailable, ImportError, OSError) as exc:
            print(f"cannot fingerprint {args.db} before building ({exc}): nothing was built.", file=sys.stderr)
            return 6
        return 0

    if args.write:
        try:
            fp = write(cov, reference, extensions, engine,
                       Path(args.taken_before) if args.taken_before else None, db=args.db)
        except SnapshotUnusable as exc:
            print(f"cannot stamp {args.db}: {exc}", file=sys.stderr)
            return 6
        except (EngineUnavailable, ImportError) as exc:
            print(f"cannot stamp {args.db}: x4validate is not importable here "
                  f"({exc}). Run via `uv run` from tools/x4validate.", file=sys.stderr)
            return 6
        print(f"  fingerprint stamped on {cov.name}: "
              f"content={fp['content']} engine={fp['engine']}")
        return 0

    try:
        verdict = check(cov, reference, extensions, engine, args.db)
    except (EngineUnavailable, ImportError) as exc:
        return _report_unknown(args.db, exc)
    if verdict.fresh:
        print(f"  {args.db}: FRESH — still describes the current world.")
        return 0
    print(verdict.banner(), file=sys.stderr)
    # THE RC HAS TO MATCH THE BANNER. `check()` can now report UNKNOWN -- an
    # absent coverage report, an unreadable one, one with no fingerprint, a
    # current fingerprint that could not be computed -- and printing "FRESHNESS
    # UNKNOWN" while exiting 5 (STALE) would put the two channels in direct
    # contradiction. 6 is already this CLI code for "cannot determine", used by
    # both `_report_unknown` call sites and by --write.
    return 5 if verdict.determinable else 6


if __name__ == "__main__":
    raise SystemExit(main())
