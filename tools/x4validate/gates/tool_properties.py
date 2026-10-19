#!/usr/bin/env python
r"""Correctness PROPERTIES for the three tools with the thinnest coverage.

Gate coverage was uneven. x4validate had six gates, x4effective three, x4compat
determinism, x4modlist two. `x4diff`, `x4xref` and `x4stats` had only `qa_sweep`
cells — which assert a tool RUNS and prints something, not that what it prints
is TRUE. "Exits 0 with output" is precisely the bar a confidently-wrong tool
clears.

So this gate checks properties that must hold regardless of input, each one
falsifiable and checked against ground truth derived INDEPENDENTLY of the tool
being tested (a fresh lxml parse of the same file, not the tool's own index):

  x4diff   identity      diff(A,A) is empty for every A.
           antisymmetry  diff(A,B).added == diff(B,A).removed, and vice versa.
                         A tool can look right on one direction and still have
                         old/new swapped; only the pair catches that.
  x4xref   citations     every "defined at file:line" it prints must really
                         have that cue at that line. A wrong line silently
                         sends the user to the wrong place.
           completeness  cues found by an independent parse must be in the index
                         — reported WITH a denominator, never as a bare count.
  x4stats  fidelity      every numeric it reports for a macro must equal the
                         value in the file, re-parsed from scratch.
  x4compat soundness     every mod named in a collision must really ship that
                         vpath, and the winner must be one of them. A conflict
                         detector that invents a conflict is as bad as one that
                         misses it.
           completeness  a shared FULL-FILE path is either reported or a known
                         non-override class (per-mod manifest, union-merged
                         registry). NOT "any shared vpath must collide" -- two
                         diffs on disjoint nodes legitimately do not.
           name-clash    the ENTITY-level kind gets its own invariants: every
                         named mod must really DEFINE that macro, and no
                         load-order winner may be claimed (index/macros.xml
                         decides). Its composite vpath makes the two file-level
                         properties above inapplicable, so it is checked
                         separately rather than waved through.
  x4similar monotonicity results at a LOWER threshold must be a superset of
                         those at a higher one. A scoring bug breaks this even
                         when every individual run looks plausible.

Targets are discovered from the local install; nothing is named.

Run:  uv run python gates/tool_properties.py [--samples=N]
Exit: 0 all properties hold, 1 any violation.
"""
from __future__ import annotations

import random
import re
import subprocess
import sys
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402
from x4validate import _cat, _compat, _merge, _paths, _scan, _stats, _xref  # noqa: E402

SAMPLES = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--samples=")), 12)
#: --exhaustive: verify EVERY output row, not a sample. Slower (~10 min) but it is
#: the release bar — "strictly and totally verify all of the output". First full
#: run 2026-08-09: xref 23,646/23,646 exact, stats 10,150 values 0 mismatched,
#: similar 808/808 pairs, diff 30/30 planted mutations. Three apparent violations
#: along the way were all bugs in the VERIFIER (wrong ship copy compared; `id`
#: mutations that legitimately read as structural) — check the checker first.
EXHAUSTIVE = "--exhaustive" in sys.argv
EXT = _env.extensions()
REF = _env.reference()
failures: list[str] = []
skips: list[str] = []
#: Files `_is_full_file` could not read. Reported, never silently dropped.
_UNREADABLE: list[str] = []


def run(tool: str, *argv: str) -> str:
    p = subprocess.run(["uv", "run", tool, *argv], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=1800)
    return (p.stdout or "") + (p.stderr or "")


def note(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'  ok ' if ok else ' FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(f"{label}: {detail}")


def skip(label: str, reason: str) -> None:
    """A check that COULD NOT RUN. Printed as SKIP, recorded, and it makes the gate
    return 3 -- never 0.

    Three sites called `note(True, ..., "SKIPPED: ...")`, which prints an `ok` line and
    never touches `failures`, so the gate printed "All properties hold." and returned 0
    over checks that never executed. This file already states the rule at the
    store-vs-x4eff manifest branch -- "A SKIP IS NOT A PASS ... the same rule this file
    applies to everything else it measures" -- and three places did not apply it.
    Prose was true; the code was not.

    rc 3 rather than rc 1 because these are ENVIRONMENTAL (no store on a CI runner),
    not defects. `scripts/run-gates.sh` already has the could-not-run bucket and will
    report NOT A CLEAN SWEEP, which is the honest answer."""
    print(f"  SKIP  {label}  {reason}")
    skips.append(f"{label}: {reason}")


def mods(n: int) -> list[Path]:
    """Loose mods with XML, discovered from the install."""
    out = []
    for d in sorted(EXT.iterdir()):
        if not d.is_dir() or d.name.lower().startswith("ego_dlc_"):
            continue
        if any(d.rglob("*.xml")):
            out.append(d)
        if len(out) >= n:
            break
    return out


_COUNTS = re.compile(r"changed files:\s*(\d+)\s+added:\s*(\d+)\s+removed:\s*(\d+)")


def diff_counts(a: Path, b: Path) -> tuple[int, int, int] | None:
    m = _COUNTS.search(run("x4diff", str(a), str(b)))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def check_x4diff() -> None:
    print("\nx4diff")
    targets = mods(SAMPLES)
    for d in targets[:SAMPLES]:
        got = diff_counts(d, d)
        note(got == (0, 0, 0), f"identity  diff({d.name}, itself)", f"counts={got}")
    # antisymmetry over consecutive pairs
    for a, b in zip(targets, targets[1:]):
        fwd, rev = diff_counts(a, b), diff_counts(b, a)
        if fwd is None or rev is None:
            note(False, f"antisymmetry  {a.name} <-> {b.name}", "unparseable output")
            continue
        ok = fwd[1] == rev[2] and fwd[2] == rev[1] and fwd[0] == rev[0]
        note(ok, f"antisymmetry  {a.name} <-> {b.name}", f"{fwd} vs {rev}")


def check_x4xref() -> None:
    print("\nx4xref")
    rows = _xref.read_tsv(_xref._default_tsv())
    # kind is "cuedef" and the path field is `file` — checked against the real
    # vocabulary, not guessed. The first cut guessed both and reported an empty
    # sample; it FAILED rather than passing vacuously, which is the point.
    defined = [r for r in rows if r.kind == "cuedef" and r.file and r.line]
    if not defined:
        note(False, "index has cue definitions", "none found — build the index first")
        return
    print(f"  (index holds {len(rows)} rows, {len(defined)} cue definitions)")

    # -- citations: the file:line it reports must really hold that cue --------
    random.seed(20260809)
    checked = bad = unresolved = 0
    for r in random.sample(defined, min(60, len(defined))):
        base = REF if r.source == "base" else EXT / r.source
        path = base / r.file
        if not path.is_file():
            unresolved += 1          # packed or DLC-owned; not a citation error
            continue
        try:
            tree = etree.parse(str(path))
        except (etree.XMLSyntaxError, OSError):
            unresolved += 1
            continue
        hit = any(el.get("name") == r.name and el.sourceline == r.line
                  for el in tree.iter("cue"))
        checked += 1
        if not hit:
            bad += 1
            if bad <= 3:
                print(f"          mis-cited: {r.name} claimed at {r.file}:{r.line}")
    note(bad == 0, "citations resolve to the exact line",
         f"{checked - bad}/{checked} exact ({unresolved} not loose-resolvable)")

    # -- completeness, WITH a denominator ------------------------------------
    indexed = {(r.name, r.file) for r in defined}
    truth, missing = 0, []
    for vpath in ("md/encounters.xml", "md/npc_missions.xml", "md/signal_leaks.xml"):
        path = REF / vpath
        if not path.is_file():
            continue
        for el in etree.parse(str(path)).iter("cue"):
            name = el.get("name")
            if not name:
                continue
            truth += 1
            if (name, vpath) not in indexed:
                missing.append(f"{vpath}:{name}")
    note(not missing, "independently-parsed cues are all indexed",
         f"{truth - len(missing)}/{truth} found"
         + (f"; missing e.g. {missing[:2]}" if missing else ""))


def check_x4stats() -> None:
    print("\nx4stats")
    macros = []
    for sub in ("assets/props/SurfaceElements/macros", "assets/props/engines/macros"):
        d = REF / sub
        if d.is_dir():
            macros += sorted(d.glob("*_macro.xml"))[:SAMPLES]
    if not macros:
        note(False, "found macros to sample", "none under reference/")
        return

    total = mismatched = 0
    for path in macros[:SAMPLES]:
        reported = _stats.macro_stats(path)
        if not reported:
            continue
        # Ground truth: re-parse from scratch, walking the properties tree
        # ourselves rather than reusing the tool's own flattener.
        root = etree.parse(str(path)).getroot()
        macro = next(iter(root.iter("macro")), None)
        if macro is None:
            continue
        truth: dict[str, str] = {}
        for el in macro.iter():
            if el is macro or not isinstance(el.tag, str):
                continue
            for k, v in el.attrib.items():
                truth.setdefault(f"{el.tag}.{k}", v)
        for prop, value in reported.items():
            if prop == "class" or prop not in truth:
                continue
            total += 1
            want = truth[prop]
            try:
                same = abs(float(value) - float(want)) < 1e-9
            except (TypeError, ValueError):
                same = str(value) == want
            if not same:
                mismatched += 1
                if mismatched <= 3:
                    print(f"          {path.name} {prop}: reported {value!r}, file says {want!r}")
    note(mismatched == 0, "reported numbers match a fresh parse of the file",
         f"{total - mismatched}/{total} values agree")


def _ships_vpath(mod: Path) -> set[str]:
    """Every XML vpath a mod ships, loose or packed — computed WITHOUT using
    x4compat's own path helper, so it is genuinely independent evidence."""
    out = {p.relative_to(mod).as_posix().lower()
           for p in mod.rglob("*.xml") if p.is_file()}
    try:
        out |= {v.lower() for v in _cat.mod_vfs(mod, packed_only=True)}  # packed-ok: unioned with rglob above
    except OSError:
        pass  # silent-ok: an unreadable archive shrinks this mod's evidence set;
              # the soundness check below reports any vpath it then cannot back up
    return out


def _is_full_file(mod: Path, vpath: str) -> bool:
    """True when the mod ships *vpath* as a whole-file override, not a <diff>.

    Read independently of x4compat (fresh parse / raw catalog read).
    """
    p = mod / vpath
    try:
        if p.is_file():
            return etree.parse(str(p)).getroot().tag != "diff"
        data = _cat.read_path(mod, vpath)
        if data is None:
            return False
        return etree.fromstring(data).tag != "diff"
    except (etree.XMLSyntaxError, OSError, ValueError) as exc:
        # Counted, not swallowed. Returning False quietly would shrink the
        # completeness denominator with nothing said — the shape that turns a
        # missed override into "all clear". The count is printed with the result.
        _UNREADABLE.append(f"{mod.name}/{vpath}: {exc}")
        return False


def check_x4compat() -> None:
    print("\nx4compat")
    report = _compat.analyze(EXT, config=_merge.Config())
    if not report.collisions:
        note(False, "produced collisions to check", "none found on this install")
        return
    print(f"  ({report.mods_scanned} mods scanned, {len(report.collisions)} collisions)")

    # NAME-CLASH is an ENTITY-level kind: its `vpath` is a composite
    # "mod:path | mod:path" and its winner is deliberately EMPTY (index/macros.xml
    # decides, not load order). The two file-level properties below do not apply
    # to it, so it gets its own below rather than being waved through.
    clashes = [c for c in report.collisions if c.kind == "NAME-CLASH"]
    file_level = [c for c in report.collisions if c.kind != "NAME-CLASH"]

    owns: dict[str, set[str]] = {}
    unsound = winnerless = 0
    for c in file_level:
        for folder in c.mods:
            d = EXT / folder
            if folder not in owns:
                owns[folder] = _ships_vpath(d) if d.is_dir() else set()
            v = c.vpath.lower()
            # A cross-mod patch's vpath is the TARGET's path, `extensions/<target>/<rel>`.
            # The target itself ships plain `<rel>`; only the patching mod ships the
            # nested form. Comparing literally called that unsound -- the property was
            # wrong, not the tool. Accept either spelling for this mod.
            nested = f"extensions/{folder.lower()}/"
            candidates = {v, v[len(nested):]} if v.startswith(nested) else {v}
            if not (candidates & owns[folder]):
                unsound += 1
                if unsound <= 3:
                    print(f"          unsound: {folder} named for {c.vpath}, but does not ship it")
        # MIGRATED 2026-08-13, not dropped. SUBTREE's `winner` is now deliberately
        # empty (the load-order winner there is the WIPER, not the owner of the
        # final value), so the claim moves to `wiped_by` rather than SUBTREE being
        # exempted. Exempting would have silently shrunk this invariant from 445
        # rows to 297 — the narrowing shape this whole gate exists to catch.
        claimed = c.wiped_by if c.kind == "SUBTREE" else c.winner
        if claimed not in c.mods:
            winnerless += 1
            if winnerless <= 3:
                label = "wiped_by" if c.kind == "SUBTREE" else "winner"
                print(f"          {label} {claimed!r} not among {c.mods}")
    subtree_n = sum(1 for c in file_level if c.kind == "SUBTREE")
    note(unsound == 0, "every named mod really ships the colliding vpath",
         f"{len(file_level)} file-level collisions checked")
    note(winnerless == 0, "the mod a collision NAMES is always one of its mods",
         f"{len(file_level)} checked ({subtree_n} of them via wiped_by)")

    # NAME-CLASH invariants: every named mod must really DEFINE that macro name,
    # and no winner may be claimed (index/macros.xml decides, not load order).
    bad_def = bad_win = 0
    for c in clashes:
        if c.winner:
            bad_win += 1
        for part in c.vpath.split(" | "):
            folder, _, vp = part.partition(":")
            d = EXT / folder
            if not d.is_dir():
                continue
            defs = _compat._macro_defs(d)
            if c.target.lower() not in defs:
                bad_def += 1
                if bad_def <= 3:
                    print(f"          {folder} named for {c.target}, but does not define it")
    note(bad_def == 0, "NAME-CLASH: every named mod defines that macro",
         f"{len(clashes)} clash(es) checked")
    note(bad_win == 0, "NAME-CLASH: claims no load-order winner",
         f"{len(clashes)} checked")

    # Completeness -- stated as something actually TRUE, after three attempts at
    # it were each too strong and each blamed the tool for being RIGHT:
    #
    #   1. "two mods ship the same vpath => must collide" is false: two <diff>s
    #      touching disjoint nodes do not conflict (107 of 392 shared paths here).
    #   2. "...as a full file => must collide" is false for MANIFESTS: content.xml
    #      and ui.xml are per-mod declarations, not game content, and neither
    #      exists in reference/.
    #   3. ...and false for UNION-MERGED registries: shipping a whole
    #      libraries/loadouts.xml is a CONTRIBUTION, not an override. Only a
    #      same-id clash is a conflict, which is what x4compat's UNION-KEY kind
    #      already models.
    #
    # So the check is: a shared full-file path must be either REPORTED or a
    # known non-override class. A path in neither bucket fails, which keeps this
    # falsifiable -- a genuinely missed override still trips it.
    manifests = {"content.xml", "ui.xml"}
    union_merged = {"libraries/camerasettings.xml", "libraries/loadouts.xml",
                    "libraries/region_definitions.xml", "libraries/wares.xml",
                    "libraries/jobs.xml"}
    full: dict[str, list[str]] = {}
    for d in sorted(EXT.iterdir()):
        if not d.is_dir() or d.name.lower().startswith("ego_dlc_"):
            continue
        for v in _ships_vpath(d):
            if v.endswith(".xml") and not v.startswith("extensions/") and _is_full_file(d, v):
                full.setdefault(v, []).append(d.name)
    shared_full = {v for v, m in full.items() if len(m) > 1}
    reported = {c.vpath.lower() for c in report.collisions}
    # 4. ...and false for whole union DIRECTORIES: t/ and index/ files are merged
    #    entry by entry (KNOWLEDGEBASE "t-files are UNIONED"), and x4compat
    #    already compares their keys (_compat.UNION_DIRS). libraries/ is NOT
    #    wholly union-merged, so it keeps the explicit list above. MEASURED
    #    2026-10-18: two mods' complete t/0001-l359.xml, 0 shared (page, t) ids.
    union_dirs = tuple(p for p in _compat.UNION_DIRS if p != "libraries/")
    union_merged |= {v for v in shared_full if v.lower().startswith(union_dirs)}
    unexplained = sorted(shared_full - reported - manifests - union_merged)
    note(not unexplained, "shared full-file paths are reported or a known non-override class",
         f"{len(shared_full)} shared; {len(shared_full & reported)} reported, "
         f"{len(shared_full & (manifests | union_merged))} manifest/union-merged, "
         f"{len(unexplained)} unexplained"
         + (f"; e.g. {unexplained[:2]}" if unexplained else ""))
    if _UNREADABLE:
        print(f"          note: {len(_UNREADABLE)} file(s) unreadable, excluded from the "
              f"denominator — e.g. {_UNREADABLE[0][:90]}")


_SIM_PAIR = re.compile(r"^\s*([\w./-]+)\s+.*?([\w./-]+)\s*$")


def check_x4similar() -> None:
    print("\nx4similar")
    loose = run("x4similar", "--threshold", "0.80")
    tight = run("x4similar", "--threshold", "0.95")

    def pairs(text: str) -> set[str]:
        # Identity-bearing lines only; the exact render is not the property.
        return {ln.strip() for ln in text.splitlines()
                if "_macro" in ln and "<->" in ln or " vs " in ln}

    lo, hi = pairs(loose), pairs(tight)
    note(hi <= lo, "0.95 results are a subset of 0.80 results",
         f"loose={len(lo)} tight={len(hi)} extra_in_tight={len(hi - lo)}")
    note("Traceback" not in loose and "Traceback" not in tight,
         "no crash at either threshold")


def _xref_source_root(source: str) -> Path:
    """Resolve an index row's source to its root. `dlc:` rows live unpacked
    under reference/extensions/ — missing that made 10,473 rows (44%) read as
    unreadable on the first exhaustive run."""
    if source == "base":
        return REF
    if source.startswith("dlc:"):
        return REF / "extensions" / source[4:]
    return EXT / source


def check_x4xref_exhaustive() -> None:
    """EVERY cuedef row, packed and DLC included — not a sample."""
    print("\nx4xref (exhaustive)")
    from collections import defaultdict
    rows = [r for r in _xref.read_tsv(_xref._default_tsv()) if r.kind == "cuedef"]
    by_file = defaultdict(list)
    for r in rows:
        by_file[(r.source, r.file)].append(r)
    ok = bad = unreadable = 0
    for (source, vfile), items in sorted(by_file.items()):
        base = _xref_source_root(source)
        data = None
        pth = base / vfile
        if pth.is_file():
            try:
                data = pth.read_bytes()
            except OSError:
                pass  # silent-ok: falls through to the packed read; if that also
                # fails the rows count as UNREADABLE, which FAILS the check
        if data is None and source != "base":
            data = _cat.read_path(base, vfile)
        if data is None:
            unreadable += len(items)
            continue
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError:
            unreadable += len(items)
            continue
        present = {(el.get("name"), el.sourceline) for el in root.iter("cue")}
        for r in items:
            if (r.name, r.line) in present:
                ok += 1
            else:
                bad += 1
    note(bad == 0 and unreadable == 0,
         "EVERY cuedef citation resolves to the exact line",
         f"{ok}/{ok + bad + unreadable} exact, {bad} mis-cited, {unreadable} unreadable")


def check_x4stats_exhaustive() -> None:
    """Every *_macro.xml under reference assets/, every reported value."""
    print("\nx4stats (exhaustive)")
    macros = []
    for sub in ("assets/props", "assets/units"):
        d = REF / sub
        if d.is_dir():
            macros += sorted(d.rglob("*_macro.xml"))
    values = mismatched = 0
    for path in macros:
        reported = _stats.macro_stats(path)
        if not reported:
            continue
        try:
            root = etree.parse(str(path)).getroot()
        except (etree.XMLSyntaxError, OSError):
            continue  # silent-ok: macro_stats returned for it, so an unreadable
            # re-parse here only shrinks the verified count, never fakes a pass
        macro = next(iter(root.iter("macro")), None)
        if macro is None:
            continue
        truth: dict[str, str] = {}
        for el in macro.iter():
            if el is macro or not isinstance(el.tag, str):
                continue
            for k, v in el.attrib.items():
                truth.setdefault(f"{el.tag}.{k}", v)
        for prop, value in reported.items():
            if prop == "class" or prop not in truth:
                continue
            values += 1
            want = truth[prop]
            try:
                same = abs(float(value) - float(want)) < 1e-9
            except (TypeError, ValueError):
                same = str(value) == want
            if not same:
                mismatched += 1
    note(mismatched == 0, "EVERY reported macro value matches a fresh parse",
         f"{len(macros)} files, {values} values, {mismatched} mismatched")


def check_script_registry_scope() -> None:
    """TRIPWIRE for the ONE thing F27's experiment did not test.

    `_merge._SCRIPT_REGISTRY_DIRS` is `md/` only, because that is where the
    filename-registry rule is engine-proven. `aiscripts/` is *believed* to behave the
    same way, but MEASURED 2026-08-22 over the installed corpus there are ZERO
    complete-file-at-a-vanilla-vpath instances under aiscripts/ -- nothing to verify
    it against, and no observable difference either way.

    So rather than widen the rule on an inference, this fails the moment a real
    instance appears. An untested generalisation must either be measured or made to
    speak up; what it must never do is sit there being quietly assumed.
    """
    print("")
    print("script registry (F27) -- scope tripwire")
    rep = _scan.CorpusScan()
    md_hits: list[str] = []
    ai_hits: list[str] = []
    dlc_dirs = [d for d in (REF / "extensions").iterdir() if d.is_dir()]
    for mod, vpath, root in _scan.iter_corpus_xml(EXT, rep):
        rel = vpath.replace("\\", "/")
        low = rel.lower()
        if root.tag == "diff":
            continue
        if not (low.startswith("md/") or low.startswith("aiscripts/")):
            continue
        if not ((REF / rel).is_file() or any((d / rel).is_file() for d in dlc_dirs)):
            continue
        (md_hits if low.startswith("md/") else ai_hits).append(mod + "::" + rel)

    print("    " + rep.denominator())
    note(True, "md/ complete files at a vanilla vpath (modelled INERT)",
         str(len(md_hits)) + ": " + (", ".join(md_hits) or "none"))
    hint = "" if not ai_hits else (
        "  <-- the generalisation now HAS an instance. Decide deliberately: verify "
        "in-game whether it is inert, then either add 'aiscripts/' to "
        "_merge._SCRIPT_REGISTRY_DIRS or record why it differs. Do NOT widen it on "
        "the strength of the md/ result alone.")
    note(not ai_hits,
         "aiscripts/ complete files at a vanilla vpath (NOT modelled -- untested)",
         str(len(ai_hits)) + ": " + (", ".join(ai_hits) or "none") + hint)


#: PINNED 2026-08-22 on the post-F27 store (22,966 entities / 582,107 attr rows).
#: F33 has TWO axes and they are now in DIFFERENT states. Saying "F33 fixed" would be
#: wrong; so would leaving the attr pin at its pre-fix size.
#:
#:   attr axis  — FIXED 2026-08-22. The flattener now disambiguates a repeated bracket
#:                collision-only (`licence[x]`, then `licence[x#1]`), so the first
#:                claimant keeps its original key and 99.8% of rows are byte-identical.
#:                627 groups -> 0. MEASURED per item on a full rebuild: 582,107 attr rows
#:                and 22,966 entities UNCHANGED, entity+value multiset identical (0
#:                differences), 1,153 rows changed prop string and nothing else —
#:                1,065 that were duplicated props, 82 sibling-bracket clashes whose
#:                attributes differed, 6 pre-existing `connection[...#n]`.
#:                It is pinned at 0 so a regression is a FAILURE, not a silent return.
#:
#:   entity axis — STILL OPEN, deliberately. `(kind, name)` is duplicated across 63
#:                groups (90 extra rows), 23 of which DISAGREE. This is a different
#:                problem: the same macro name defined in base plus five DLC, where
#:                `index/macros.xml` decides which one the engine uses, NOT load order
#:                (gotcha #18). A positional suffix would be an answer to a question
#:                nobody asked.
#: RE-PINNED 2026-10-13 at 65 (was 63, MEASURED 2026-08-22): the group SET is identical under
#: v3.3.1 and master code on the same 159-mod list, so the +2 is modlist drift (+5 groups from
#: mods added since, -3 from mods removed); itemised in BLIND-SPOTS F33. Not vanilla-only:
#: 35 of the 65 involve a mod, so this count moves with the modlist.
#: RE-PINNED 2026-10-19 at 62: b88e425 code and v4.0 code, each building a store from the
#: same 170-mod list, give the IDENTICAL 62-group set (same groups, same origins per group,
#: 25,739 entities each), so the -3 is modlist drift, not the merge fixes; 32 of 62 involve
#: a mod (was 35 of 65).
_F33_ENTITY_GROUPS = 62    # duplicate (kind, name) -- 23 DISAGREED as of 2026-08-24 (63-group set; not re-measured). OPEN.
_F33_ATTR_GROUPS = 0        # duplicate (entity_id, prop) -- FIXED, pinned at zero.


def check_store_key_uniqueness() -> None:
    """F33 tripwire: the store's keys are not unique, and the size of that is pinned.

    MEASURED 2026-08-22: `(kind, name)` is duplicated across 63 groups (90 extra rows),
    23 of which hold copies that DISAGREE on at least one attribute -- e.g.
    `macro/cluster_sm3_background_macro`, defined in base plus five DLC. `(entity_id,
    prop)` is duplicated across 627 groups (1,071 extra rows), **201** of which disagree,
    because the flattener's bracket discriminator is not unique among siblings:
    `faction/player licences.licence[generaluseequipment].factions` holds EIGHT distinct
    values under one key.

    The ATTR axis was fixed 2026-08-22 and is now pinned at ZERO, so a regression fails
    here rather than returning quietly. The measurement that licensed the fix: 1,698 rows
    (0.29%) would move under a collision-only index versus 358,415 (61.6%) if every key
    were indexed positionally, and 0 of the 56 lines in `dev/_registry/CLAIMS.tsv` use a
    bracket prop, so no recorded claim could be invalidated.

    The ENTITY axis is still open on purpose -- see the note on the constants above. A
    gate that reported "F33: ok" would hide that.

    A stale or absent store SKIPS with a reason. It must never read as a pass: "we did not
    look" rendered as "nothing was wrong" is the founding defect of this whole register.
    """
    import sqlite3

    from x4validate import _effective

    print("")
    print("store key uniqueness (F33) -- attr axis FIXED (pinned 0); entity axis OPEN")
    # The ONE door (`$X4_EFFECTIVE_DB`, else beside the registry) -- not a re-derived
    # `DEFAULT_REGISTRY.parent`, which ignored the setting (release review 2026-09-26).
    store = _effective.effective_db()
    if store is None or not Path(store).is_file():
        skip("store key uniqueness",
             f"no effective store (resolved to {store or 'nothing'}) — not checked")
        return
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        if not _effective.store_freshness(con).fresh:
            skip("store key uniqueness",
                 "the store is STALE, so its counts describe a world that has "
                 "moved on — rebuild with `uv run x4effective build`, then re-run")
            return
        ent = con.execute(
            "select count(*) from (select kind,name from entities "
            "group by kind,name having count(*)>1)").fetchone()[0]
        att = con.execute(
            "select count(*) from (select entity_id,prop from attrs "
            "group by entity_id,prop having count(*)>1)").fetchone()[0]
    finally:
        con.close()

    for label, got, want in (("(kind,name) entity", ent, _F33_ENTITY_GROUPS),
                             ("(entity_id,prop) attr", att, _F33_ATTR_GROUPS)):
        hint = "" if got == want else (
            "  <-- CHANGED. If a mod was added this may be expected — attribute it per "
            "item, then re-pin. If it grew with no content change, something started "
            "emitting duplicate keys.")
        note(got == want, f"duplicate {label} key groups",
             f"{got} (pinned {want}){hint}")


def _manifest_candidates() -> list[Path]:
    """Where the BaseX x4eff effective-manifest.json may live, in preference order: the
    tree holding this file, then $X4_TOOLKIT's. A function so a test can point the gate
    at a stub without depending on whether THIS tree has built its databases."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "basex" / "_eff"
        / "effective-manifest.json",
    ]
    # `_paths.path_value`, NOT os.environ -- the config file is a LAYER, and
    # tests/test_env_resolution_is_delegated.py enforces that every module goes
    # through the same door. My first version read the environment directly and that
    # test caught it, which is the point of having it.
    tk = _paths.path_value("X4_TOOLKIT")
    if tk:
        candidates.append(Path(tk) / "tools" / "basex" / "_eff"
                          / "effective-manifest.json")
    return candidates


def check_mod_scope_agreement() -> None:
    """The store and x4eff must model the SAME world, and say which world it is.

    Both answer "what does the engine see", from two independently-built mod
    lists. Until 2026-08-22 they disagreed and nothing compared them: the store
    used the active set (114) while `build-effective.py` used the on-disk set
    (115), so x4eff carried a DISABLED mod's 19 files as live content. Surplus
    content corrupts POSITIVE answers, which -- unlike negatives -- no coverage
    denominator guards.

    Agreeing today is not the property worth checking; agreeing BY CONSTRUCTION
    is. So this compares the two sets element-wise, and a stale or absent artifact
    SKIPS with a reason rather than passing.
    """
    import json
    import sqlite3

    from x4validate import _effective, _registry

    print("")
    print("mod-scope agreement — the store vs BaseX x4eff")

    store_path = _effective.effective_db()     # the ONE door, resolved now (not DB_PATH,
    #                                              an import-time snapshot)

    # TWO BASEX TREES EXIST, and this looked in the wrong one. The self-relative path
    # resolves to <repo>/tools/basex/_eff/, which is the SHIPPING copy -- it never
    # builds anything. The databases are built by the LIVE install under $X4_TOOLKIT,
    # and that is where the manifest lands. MEASURED 2026-09-06: the manifest existed,
    # 40,014 bytes with 125 overlays, while this cell reported SKIPPED.
    #
    # PRE-ARC (fe09e99, v2.4.0). Registered as BLIND-SPOTS F105's shape: when two
    # copies of one tool exist, a path derived from THIS file's location picks the
    # copy that happens to hold this file, which is not the copy that does the work.
    candidates = _manifest_candidates()
    manifest = next((c for c in candidates if c.is_file()), candidates[0])

    if store_path is None or not Path(store_path).is_file():
        # Genuinely nothing to compare: no store means no build ever ran here. This
        # one stays a skip, and it is what CI hits.
        skip("store vs x4eff mod set", "no effective store on this machine")
        return
    if not manifest.is_file():
        # A SKIP IS NOT A PASS. The store exists, so this machine DOES build these
        # artifacts, and the one cell that proves the two agree could not run. That is
        # a could-not-check, and it now FAILS rather than printing ok -- the same rule
        # this file applies to everything else it measures.
        note(False, "store vs x4eff mod set",
             "COULD NOT CHECK — an effective store exists but no effective-manifest.json "
             "was found, so store-vs-x4eff agreement is UNVERIFIED. Looked in: "
             + "; ".join(str(c) for c in candidates)
             + ". Run tools/basex/build-effective.sh in the tree that owns the databases.")
        return

    db = sqlite3.connect(str(store_path))
    try:
        store_mods = {r[0].lower() for r in db.execute("select folder from mods")}
    finally:
        db.close()
    eff_mods = {m.lower() for m in
                json.loads(manifest.read_text(encoding="utf-8")).get("overlays_in_load_order", [])}
    active_list = _registry.mods("active")
    active = {m["folder"].lower() for m in active_list}
    left_out = _registry.dropped_note(active_list)
    if left_out:
        # Absent from the ACTIVE side of the comparison below, correctly -- NAMED so an
        # artifact that still carries it points at its cause (F139).
        print(f"        NOT in the active set (the engine does not load it): {left_out}")

    only_store, only_eff = sorted(store_mods - eff_mods), sorted(eff_mods - store_mods)
    note(not only_store and not only_eff,
         "store vs x4eff mod set",
         f"{len(store_mods)} vs {len(eff_mods)}"
         + ("" if not (only_store or only_eff) else
            f"  <-- DIVERGED. store-only={only_store} x4eff-only={only_eff}"))
    # And both must be the ACTIVE set, not merely equal to each other -- two
    # artifacts can agree perfectly while both modelling the wrong world.
    # BOTH directions. Reporting only `eff_mods - active` meant a merely STALE
    # index -- the common case, a mod deployed since the last build -- printed an
    # empty list beside a failure, naming a direction that was not the problem.
    # Same shape as the 2026-08-25 finding: a two-channel comparison diffed on one.
    _extra = sorted(eff_mods - active)
    _missing = sorted(active - eff_mods)
    _why = []
    if _extra:
        _why.append(f"x4eff carries {_extra} the engine will NOT load")
    if _missing:
        _why.append(f"x4eff is MISSING {_missing}, active but never indexed "
                    f"(usually just stale -- rebuild)")
    note(eff_mods == active, "x4eff models the ACTIVE set",
         f"{len(eff_mods)} vs {len(active)} active"
         + ("" if eff_mods == active else "  <-- " + "; ".join(_why)))


def main() -> int:
    print("=" * 88)
    print("TOOL PROPERTIES — x4diff / x4xref / x4stats, checked against independent truth")
    print("=" * 88)
    check_x4diff()
    check_x4xref()
    check_x4stats()
    if EXHAUSTIVE:
        check_x4xref_exhaustive()
        check_x4stats_exhaustive()
    check_x4compat()
    check_x4similar()
    check_script_registry_scope()
    check_store_key_uniqueness()
    check_mod_scope_agreement()
    print("\n" + "=" * 88)
    if failures:
        print(f"PROPERTY VIOLATIONS: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    if skips:
        # DEGRADED, never a clean pass: some properties were not measured at all.
        print(f"NOT A CLEAN RUN: {len(skips)} propert(ies) COULD NOT BE CHECKED")
        for k in skips:
            print(f"  - {k}")
        print("Every property that RAN held. That is not the same as all of them "
              "holding.")
        return 3
    print("All properties hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
