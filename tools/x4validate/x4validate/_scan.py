r"""One packed-aware, skip-recording walk over a mod's XML.

Why this module exists
----------------------
Six modules each hand-rolled the same loop — rglob the loose tree, parse, then
walk the `.cat` members — and every copy ended the same way::

    except etree.XMLSyntaxError:
        continue

That `continue` is the bug this package has now fixed at three layers
(`Report.skipped`, `coverage.json`, `_input.require_mod_dir`) and it kept
reappearing because the loop kept being rewritten. Patching the six copies
individually guarantees a seventh. So there is one copy, and it takes a channel
for what it could not read.

Measured before the fix: `_check.iter_mod_xml_roots` dropped **12 files across 3
installed mods, 9 of them `<diff>` files** — precisely what `check_sel_resolution`
exists to examine. Neither existing guard caught it: `MergeResult.skipped` is a
different code path (overlay-tree parses), and the `checked == 0` warning only
fires at *zero*, so five good diffs plus one malformed printed nothing at all.

Ordering contract
-----------------
Loose files win over packed catalog members, matching the engine and
`_merge.overlay_root`. A loose file that will not parse still shadows its packed
twin — it is recorded as unreadable rather than silently falling through to the
packed copy, because the engine would not fall through either.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from lxml import etree

from . import _cat, _merge


#: The marker an unscanned FOLDER carries in its rendering, so a reader of a stored
#: list (x4xref's `.notindexed` sidecar) can tell it from a file that would not parse.
FOLDER_MARKER = "(FOLDER NOT SCANNED: could not be listed)"


@dataclass
class Unreadable:
    """A file the scanner found but could not parse -- or, with *folder*, a folder it
    could not LIST, so nothing inside it was scanned. Never discard these."""
    vpath: str
    why: str
    packed: bool = False
    folder: bool = False
    mod: str = ""

    def __str__(self) -> str:
        if self.folder:
            where = (f"mod folder '{self.mod}'" if not self.vpath
                     else f"folder '{self.vpath}' of mod '{self.mod}'")
            return f"{where} {FOLDER_MARKER}: {self.why}"
        return f"{self.vpath} ({'packed' if self.packed else 'loose'}): {self.why}"


def _list_dir(path: Path) -> list[Path]:
    """One directory's entries, sorted. A seam so a listing failure can be tested."""
    return sorted(path.iterdir())


def _walk_xml(root: Path, mod_dir: Path,
              unreadable: list[Unreadable] | None) -> Iterator[Path]:
    """Every `*.xml` under *root*, like `root.rglob("*.xml")` (same platform case rule,
    symlinked folders not followed) -- except that a subfolder which cannot be LISTED is
    appended to *unreadable* as an unscanned folder, where rglob skipped it silently and
    its files read as absent."""
    def onerror(exc: OSError) -> None:
        if unreadable is None:
            return  # silent-ok: the caller passed no channel (see iter_mod_xml's contract)
        where = Path(exc.filename) if exc.filename else root
        try:
            rel = where.relative_to(mod_dir).as_posix() + "/"
        except ValueError:
            rel = str(where)
        unreadable.append(Unreadable(rel, f"could not list this folder, so its files were "
                                          f"NOT scanned: {exc}", folder=True, mod=mod_dir.name))

    for dirpath, _dirs, files in os.walk(root, onerror=onerror):
        for name in files:
            if fnmatch.fnmatch(name, "*.xml"):
                yield Path(dirpath) / name


#: A predicate takes the mod-relative virtual path (forward slashes, original
#: case) and returns whether to parse the file at all. Filtering here rather
#: than after parsing is what keeps the big scans (x4xref over 100 mods) cheap.
Predicate = Callable[[str], bool]


class under:
    """Match files beneath any of *prefixes* (case-insensitive, e.g. "md/").

    Carries `.prefixes` so `iter_mod_xml` can narrow its `rglob` to just those
    subtrees instead of walking everything and filtering after. That matters:
    x4xref scans the reference root, where a full walk is ~13k files to reach
    the ~2k under md/ and aiscripts/.
    """

    def __init__(self, *prefixes: str):
        self.prefixes = tuple(p.lower() for p in prefixes)

    def __call__(self, vpath: str) -> bool:
        return vpath.lower().startswith(self.prefixes)


class under_nested:
    """Match files beneath *prefixes* directly OR behind repeated ``extensions/<owner>/``.

    A mod patches another mod's file at ``extensions/<owner>/<path>`` (CLAUDE.md gotcha #6),
    and the prefix repeats for a patch of a patch. `under` matches path STARTS only, so
    x4xref built on it never read one nested md/aiscripts patch: MEASURED on a real
    install, 20 of 24 named overlay script documents were omitted with 0 unreadable.

    For MOD and DLC roots only. Never point it at the reference ROOT: its own
    ``extensions/`` subtree is the DLC, which callers enumerate separately, so the
    nested match would count every DLC file a second time.

    `.prefixes` adds ``extensions/`` so `_scan_roots` narrows the rglob to the script
    dirs plus the extensions subtree, never the whole mod.
    """

    def __init__(self, *prefixes: str):
        import re
        dirs = tuple(p.lower().strip("/") for p in prefixes)
        self.prefixes = tuple(d + "/" for d in dirs) + ("extensions/",)
        self._rx = re.compile(r"(?:extensions/[^/]+/)*(?:"
                              + "|".join(re.escape(d) for d in dirs) + r")/",
                              re.IGNORECASE)

    def __call__(self, vpath: str) -> bool:
        return self._rx.match(vpath) is not None


def ending(*suffixes: str) -> Predicate:
    lowered = tuple(s.lower() for s in suffixes)
    return lambda vpath: vpath.lower().endswith(lowered)


class all_of:
    """Conjunction that keeps the `.prefixes` narrowing of any `under` inside it."""

    def __init__(self, *predicates: Predicate):
        self.predicates = predicates
        for p in predicates:
            if getattr(p, "prefixes", None):
                self.prefixes = p.prefixes
                break

    def __call__(self, vpath: str) -> bool:
        return all(p(vpath) for p in self.predicates)


def _scan_roots(mod_dir: Path, predicate: Predicate | None,
                unreadable: list[Unreadable] | None = None) -> list[Path] | None:
    """Directories to rglob. Narrowed by a predicate's `.prefixes` when it has any.

    None when a mod folder that EXISTS cannot be listed: that is appended to
    *unreadable* as an unscanned FOLDER naming the mod, and the caller skips the LOOSE
    walk only -- its catalogs are still read (`_cat` raises if they cannot be either)."""
    prefixes = getattr(predicate, "prefixes", None)
    if not prefixes:
        return [mod_dir]
    # Narrow to each prefix's leading directory segment. A head that is not a
    # real directory simply contributes nothing — there can be no loose file
    # under it. Deduped so overlapping prefixes ("assets", "assets/units") do
    # not yield the same file twice.
    #
    # Matched against the folders ON DISK, case-folded, never joined literally: the
    # predicate is case-insensitive (the engine reads virtual paths that way), and a
    # literal `mod_dir / "extensions"` misses a loose `Extensions/` on a case-sensitive
    # filesystem and, on Windows, spells the vpath's first segment from the literal
    # instead of the disk. Only the top level is listed, so the walk stays narrowed
    # (`_merge._folded_loose`, the existing case-fold index, rglobs the whole overlay,
    # which is exactly the cost this narrowing exists to avoid). Two spellings of one
    # head on a case-sensitive disk are both walked and both yielded -- the ambiguity
    # `_merge.overlay_root` refuses; this enumeration does not model which one loads.
    heads = {p.strip("/").split("/")[0].lower() for p in prefixes}
    try:
        children = _list_dir(mod_dir)
    except FileNotFoundError:
        return []  # silent-ok: an ABSENT folder has no loose files to walk -- an absence,
        # not a read failure (the literal `is_dir()` test gave the same answer).
    except OSError as exc:
        if unreadable is not None:
            unreadable.append(Unreadable(
                "", f"could not list the mod folder, so its loose files were NOT scanned "
                    f"(its catalogs were still read): {exc}", folder=True, mod=mod_dir.name))
        return None
    return [c for c in children if c.name.lower() in heads and c.is_dir()]


def iter_mod_xml(
    mod_dir: Path,
    predicate: Predicate | None = None,
    unreadable: list[Unreadable] | None = None,
) -> Iterator[tuple[str, etree._Element]]:
    """Yield ``(vpath, root)`` for every XML a mod owns, loose then packed.

    A file that will not parse is APPENDED TO *unreadable*, never dropped
    silently. Pass ``unreadable=None`` only where the caller genuinely has no way
    to report — and if you find yourself doing that, that is the bug.
    """
    yielded: set[str] = set()

    # None: the mod folder could not be listed (recorded by _scan_roots). Only the LOOSE
    # walk is skipped; the catalogs below are still read.
    roots = _scan_roots(mod_dir, predicate, unreadable) or []
    loose = sorted(p for root in roots for p in _walk_xml(root, mod_dir, unreadable))
    for path in loose:
        if not path.is_file():
            continue
        vpath = path.relative_to(mod_dir).as_posix()
        if predicate is not None and not predicate(vpath):
            continue
        # Claim the vpath BEFORE parsing: a malformed loose file shadows its
        # packed twin in the engine too, so silently using the packed copy would
        # model the wrong file.
        yielded.add(vpath.lower())
        try:
            root = _merge.parse_file(path)
        except (etree.XMLSyntaxError, OSError) as exc:
            if unreadable is not None:
                unreadable.append(Unreadable(vpath, str(exc)))
            continue
        yield vpath, root

    for vpath, member in sorted(_cat.mod_vfs(mod_dir, packed_only=True).items()):
        if not vpath.lower().endswith(".xml") or vpath.lower() in yielded:
            continue
        if predicate is not None and not predicate(vpath):
            continue
        try:
            root = _merge.parse_bytes(_cat.read_member(member))
        except (etree.XMLSyntaxError, OSError, ValueError) as exc:
            if unreadable is not None:
                unreadable.append(Unreadable(vpath, str(exc), packed=True))
            continue
        yield vpath, root


def iter_mod_xml_bytes(
    mod_dir: Path,
    predicate: Predicate | None = None,
    unreadable: list[Unreadable] | None = None,
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(vpath, raw_bytes)`` for every XML a mod owns, loose then packed.

    Same enumeration and same loose-shadows-packed rule as `iter_mod_xml`, minus
    the parse. For callers that want to CHEAPLY REJECT most files and parse only
    the survivors — a byte test is roughly 3.5x cheaper than building the tree
    (MEASURED over the reference tree: 8.9s parsing everything vs 2.5s reading
    everything). Anything that needs a tree must still parse; this exists so a
    caller can avoid parsing files that provably cannot match, never so it can
    answer a structural question by looking at raw text.

    Unreadable files are skipped, and `check_readability` owns the parse-failure
    report over the same enumeration. A caller that keeps its OWN unreadable ledger
    (``gates/noop_audit.py``) may pass *unreadable* to receive read failures here
    too — added so that gate could stop hand-rolling a packed-only walk without
    losing the channel it already reported.
    """
    yielded: set[str] = set()
    # None: the mod folder could not be listed (recorded by _scan_roots). Only the LOOSE
    # walk is skipped; the catalogs below are still read.
    roots = _scan_roots(mod_dir, predicate, unreadable) or []
    loose = sorted(p for root in roots for p in _walk_xml(root, mod_dir, unreadable))
    for path in loose:
        if not path.is_file():
            continue
        vpath = path.relative_to(mod_dir).as_posix()
        if predicate is not None and not predicate(vpath):
            continue
        yielded.add(vpath.lower())
        try:
            yield vpath, path.read_bytes()
        except OSError as exc:
            if unreadable is not None:
                unreadable.append(Unreadable(vpath, str(exc)))
            continue  # silent-ok: see docstring — check_readability reports it.

    for vpath, member in sorted(_cat.mod_vfs(mod_dir, packed_only=True).items()):
        if not vpath.lower().endswith(".xml") or vpath.lower() in yielded:
            continue
        if predicate is not None and not predicate(vpath):
            continue
        try:
            yield vpath, _cat.read_member(member)
        except (OSError, ValueError) as exc:
            if unreadable is not None:
                unreadable.append(Unreadable(vpath, str(exc), packed=True))
            continue  # silent-ok: as above.


def iter_mod_text(
    mod_dir: Path,
    suffixes: tuple[str, ...],
    unreadable: list[Unreadable] | None = None,
) -> Iterator[tuple[str, str, bool]]:
    """Yield ``(vpath, text, packed)`` for a mod's files ending in *suffixes*.

    The text-level twin of :func:`iter_mod_xml`, for scanners that match on raw
    source lines rather than parsed structure — and the only option for ``.lua``,
    which is not XML and so can never arrive through `iter_mod_xml`.

    Same ordering contract: loose wins over packed, matching the engine.

    Measured reason this exists: `_migration.scan_mod` walked `mod_dir.rglob`
    alone, so on a PACKED mod it scanned nothing and reported a clean 9.0 port.
    `sn_mod_support_apis` — the very mod whose `Lua_Loader` the check is about —
    ships one loose file (`content.xml`) and **4 `Lua_Loader.Load` hits inside
    `ext_01.cat`**, and `--update` reported zero.
    """
    lowered = tuple(s.lower() for s in suffixes)
    yielded: set[str] = set()

    for path in sorted(mod_dir.rglob("*")):
        if not path.is_file() or not path.name.lower().endswith(lowered):
            continue
        vpath = path.relative_to(mod_dir).as_posix()
        # Claim before reading, for the same reason iter_mod_xml does: a loose
        # file shadows its packed twin even when it cannot be read.
        yielded.add(vpath.lower())
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            if unreadable is not None:
                unreadable.append(Unreadable(vpath, str(exc)))
            continue
        yield vpath, text, False

    # xml_only=False: .lua members are exactly what the default index drops.
    for vpath, member in sorted(_cat.mod_vfs(mod_dir, xml_only=False, packed_only=True).items()):
        if not vpath.lower().endswith(lowered) or vpath.lower() in yielded:
            continue
        try:
            text = _cat.read_member(member).decode("utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            if unreadable is not None:
                unreadable.append(Unreadable(vpath, str(exc), packed=True))
            continue
        yield vpath, text, True


def count_line(shown: int, total: int, noun: str, flag: str = "--limit") -> str:
    """Render a count that discloses its own bound.

    A bounded result printed as a bare count makes the *number* wrong, not just
    the list: `x4effective` defaulted to `--limit 200` and printed "200 ware(s)"
    against a store of 2,431. Any `[:n]` slice must go through this.
    """
    if total > shown:
        return (f"{shown} of {total} {noun} shown — TRUNCATED by {flag}; "
                f"use {flag} {total} (or 0) for all")
    return f"{shown} {noun}"


@dataclass(frozen=True)
class Coverage:
    """How much of a mod's XML a scan saw, split loose vs packed.

    Exists so an ad-hoc measurement gets its denominator for FREE instead of
    remembering to compute one. MEASURED 2026-08-13: a corpus scan built on
    `_cat.mod_vfs` (catalogs only) read **2,681** XML files across 115 mods and
    reported a name "NOT FOUND"; the correct enumeration read **4,401** and found
    it in a loose file. A single total could not have shown which half was
    missing — only the split can, which is why `loose` and `packed` are separate
    fields rather than one number.

    ⚠ Parse failures are NOT counted here: this classifies the files the walk
    YIELDED. For unreadable files use `iter_mod_xml(unreadable=[...])`, which is
    the channel that exists for exactly that.
    """
    mod: str
    loose: int
    packed: int

    @property
    def total(self) -> int:
        return self.loose + self.packed

    def __str__(self) -> str:
        return (f"{self.mod}: {self.total} XML "
                f"({self.loose} loose + {self.packed} packed)")


def mod_xml_inventory(mod_dir: Path, predicate: Predicate | None = None) -> Coverage:
    """Count a mod's XML, loose vs packed, over the SAME walk everything else uses.

    Deliberately consumes `iter_mod_xml_bytes` rather than re-enumerating: a second
    enumeration here would be the very defect this module was created to end (see
    the module docstring — six hand-rolled copies, and the seventh appeared anyway
    in a throwaway script). The loose/packed split is DERIVED from the yielded
    vpaths, because loose-shadows-packed means a vpath that exists on disk is the
    one the walk yielded.
    """
    loose = packed = 0
    for vpath, _raw in iter_mod_xml_bytes(mod_dir, predicate):
        if (mod_dir / vpath).is_file():
            loose += 1
        else:
            packed += 1
    return Coverage(mod_dir.name, loose, packed)


@dataclass
class CorpusScan:
    """The denominator for a scan across the WHOLE installed set.

    `Coverage` answers "how much of ONE mod did I see". This answers the question
    that keeps being got wrong: *did my corpus scan look at anything at all?*

    MEASURED, the recurring failure (7 instances, `docs/BLIND-SPOTS.md`): an ad-hoc
    sweep called `etree.fromstring()` on roots `iter_mod_xml` had ALREADY parsed,
    raised `TypeError` on every one of 4,391 files, swallowed it in a bare
    ``except Exception: continue``, and reported **0 dangling across 115 mods** --
    a clean corpus over a population of zero. The truth was 3, in 1 mod. It was
    caught only because one mod was known to have them.

    So a zero cannot be rendered as a finding here: `verdict()` RAISES when nothing
    parsed. That is the same contract `tools/basex/ask.py` enforces for BaseX
    queries, which the Python side had no equivalent of -- which is why the bug
    kept coming back.
    """
    mods_scanned: int = 0
    files_parsed: int = 0
    unreadable: list[Unreadable] | None = None
    skipped_mods: list[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.unreadable is None:
            self.unreadable = []
        if self.skipped_mods is None:
            self.skipped_mods = []

    def denominator(self) -> str:
        """The scanned SOURCE SET, not just the failures.

        Names what was looked at, because a blind spot is by definition absent from
        the list of things that failed -- the lesson `x4xref` learned the hard way.
        """
        s = (f"SCANNED SOURCE SET: {self.mods_scanned} mod(s), "
             f"{self.files_parsed} XML file(s) parsed")
        if self.unreadable:
            s += f", {len(self.unreadable)} unreadable"
        if self.skipped_mods:
            s += f", {len(self.skipped_mods)} mod(s) SKIPPED ENTIRELY"
        return s

    def verdict(self, hits: int, noun: str) -> str:
        """Render a finding WITH its denominator, or refuse.

        Raises when nothing parsed: `hits == 0` over `files_parsed == 0` is a
        NON-ANSWER, not an absence, and the whole point of this class is that the
        two can never again be printed in the same grammar.
        """
        if self.files_parsed == 0:
            raise ValueError(
                f"refusing to report '{hits} {noun}': NOTHING WAS PARSED "
                f"({self.denominator()}). That is a non-answer, not an absence — "
                f"fix the scan before quoting a result from it.")
        return f"{hits} {noun} — {self.denominator()}"


def iter_corpus_xml(
    ext_dir: Path,
    report: CorpusScan,
    predicate: Predicate | None = None,
) -> Iterator[tuple[str, str, etree._Element]]:
    """Yield ``(mod_folder, vpath, root)`` for every installed MOD, filling *report*.

    Mods come from `_registry.scan_installed`, which is the single implementation of
    "what is installed": it reads each mod's own manifest and already excludes
    `ego_dlc_*`. Hand-rolling that walk here was caught by
    `tests/test_dlc_enumeration.py` -- fittingly, since re-implementing something the
    package already owns is the very bug class this helper exists to end.

    DLC is deliberately NOT reachable from here. The reference tree IS the unpacked
    base+DLC, so a sweep over both double-counts every DLC -- it reported 92 md
    collisions where the answer was 1, and 200 module groups where it was 146, twice
    in one session (CLAUDE.md #20). For DLC content use `Config.dlc_dirs()`.

    ``content.xml`` IS yielded — it is XML the mod owns, and every corpus denominator
    already recorded (4,391 files over 115 mods) counts it. Filter it in the caller if
    your question is about content; do not change it here, or previously recorded
    numbers stop reproducing.

    Nothing is dropped in silence: a mod whose manifest will not parse lands in
    `report.skipped_mods`, and a file that will not parse lands in
    `report.unreadable`. Both shrink the denominator that `verdict()` prints.
    """
    from . import _registry

    dropped: list[str] = []
    # INSTALLED: a corpus sweep asks "does this appear anywhere I have", which
    # is broader than "what loads" on purpose -- narrowing it would make a
    # negative weaker, not stronger.
    mods = _registry.mods("installed", [ext_dir], dropped=dropped)
    for reason in dropped:
        report.skipped_mods.append((reason.split(":", 1)[0], reason))
    for mod in mods:
        report.mods_scanned += 1
        mod_dir = Path(mod["path"])
        try:
            items = list(iter_mod_xml(mod_dir, predicate, unreadable=report.unreadable))
        except (OSError, ValueError) as exc:
            # Recorded, never swallowed: a mod we could not walk is a hole in the
            # denominator, and mods_scanned above would otherwise overstate coverage.
            report.skipped_mods.append((mod["folder"], str(exc)))
            continue
        for vpath, root in items:
            report.files_parsed += 1
            yield mod["folder"], vpath, root
