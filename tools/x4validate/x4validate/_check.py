"""Orchestration: run the three checks against a mod folder and collect findings."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from lxml import etree

from . import (_cat, _compat, _debuglog, _effective, _exprlint, _loadorder, _merge,
               _migration, _refs, _registry, _resolve, _scan, _xref, _xsd, _modfiles)

# A ship variant macro file: <base>_<a|b|c|...>_macro.xml
VARIANT_RE = re.compile(r"^(?P<base>.+)_(?P<v>[a-z0-9])_macro\.xml$")

# Localisation: X4 UNIONS pages across every t-file (base + DLC + mods), and
# strings may be defined either in the language-neutral 0001.xml or the
# English 0001-l044.xml. So text defs must be unioned across all sources, not
# read from one overridable path.
TEXT_FILES = ("t/0001.xml", "t/0001-l044.xml")
#: English (l044) or language-neutral t-files, at ANY directory depth.
#: A fixed two-entry TEXT_FILES list was a narrowing step: MEASURED, one mod
#: (`code_vgr_battlecruiser`) ships 90 strings under `sfx/weapons/t/`, 56 of
#: which were absent from the definition set — so its OWN {page,t} references
#: read as dangling. The mod is fine; the oracle was short.
#: Other languages stay OUT deliberately: folding l007/l033/… into one English
#: set would hide a genuinely missing English string behind a German one.
T_FILE_RE = re.compile(r"(?:^|/)t/\d+(?:-l044)?\.xml$", re.IGNORECASE)
#: Bounded-depth globs, not `rglob`. MEASURED on the 60 GB reference tree:
#: 0.06s total for all four, versus a full walk.
_T_GLOBS = ("t/*.xml", "*/t/*.xml", "*/*/t/*.xml", "*/*/*/t/*.xml")


def t_file_rels(src: Path) -> list[str]:
    """Relative paths of every English/neutral t-file in *src*, loose and packed."""
    rels: set[str] = set()
    for pattern in _T_GLOBS:
        try:
            candidates = list(src.glob(pattern))
        except OSError:
            continue  # silent-ok: an unreadable directory yields no candidates here,
            # and the caller's own read of a known path still reports failures.
        for f in candidates:
            rel = f.relative_to(src).as_posix()
            if T_FILE_RE.search("/" + rel):
                rels.add(rel)
    try:
        packed = _cat.mod_vfs(src, packed_only=True)  # packed-ok: loose half handled above
    except OSError:
        packed = {}
    for v in packed:
        rel = v.replace("\\", "/")
        if T_FILE_RE.search("/" + rel):
            rels.add(rel)
    # The historical pair stays in the set even if globbing found nothing, so a
    # source whose directory cannot be listed still gets its standard files read.
    return sorted(rels | set(TEXT_FILES))
WARES_FILE = "libraries/wares.xml"
# index/macros.xml is additively UNIONED across extensions (like t-files), not
# overridden — each extension registers its own macro->file mappings.
MACRO_INDEX = "index/macros.xml"
RACES_FILE = "libraries/races.xml"
FACTIONS_FILE = "libraries/factions.xml"
#: A mod's manifest, not payload. It is XML that `iter_mod_xml_roots` yields, so any
#: "does this mod ship content?" count must exclude it — otherwise a folder holding
#: nothing but content.xml looks like it has one payload file.
MANIFEST = "content.xml"


def collect_text_defs(config: _merge.Config, extra_overlays=None,
                      report: Report | None = None) -> set[tuple[str, str]]:
    """Union (page,t) definitions from every t-file across base + DLC + overlays.

    Works for full <language> files and <diff> files alike (text_defs scans
    //page[@id]//t[@id] regardless of root). t-files are purely additive, so a
    plain union is faithful here.

    **PACKED t-files count** (fixed 2026-07-27). This used to test `f.is_file()`
    only, so a mod shipping its strings inside ext_01.cat contributed nothing —
    the same blind spot that made iter_diff_files report packed mods as clean.
    Measured on the live modlist: **977 strings exist only in packed t-files**
    (vro 425, ship_variation_expansion 108, xenon_backup 102, ...). Every one was
    a potential false "introduced text reference does not resolve", and Tier B —
    where you patch another mod and quote its strings — is exactly where it bites.

    A t-file that fails to parse is REPORTED, not swallowed: a shrunken def set
    turns into false unresolved-reference errors downstream, and silently
    dropping it is indistinguishable from the string genuinely not existing.
    """
    defs: set[tuple[str, str]] = set()
    sources = ([config.reference] + config.dlc_dirs()
               + list(config.overlays) + list(extra_overlays or []))
    for src in sources:
        defs |= _source_text_defs(src, report)
    return defs


def _source_text_defs(src: Path, report: Report | None = None) -> set[tuple[str, str]]:
    """(page,t) definitions from every t-file under ONE source root, loose or packed."""
    defs: set[tuple[str, str]] = set()
    for rel in t_file_rels(src):
        f = src / rel
        try:
            if f.is_file():
                defs |= _refs.text_defs(_merge.parse_file(f))
                continue
            data = _cat.read_path(src, rel)  # packed: ext_01.cat/.dat
            if data is not None:
                defs |= _refs.text_defs(_merge.parse_bytes(data))
        except (etree.XMLSyntaxError, OSError) as exc:
            if report is not None:
                report.skip("text-reference checks",
                            f"{src.name}/{rel}: unreadable, its strings are missing "
                            f"from the definition set ({exc})")
    return defs


def collect_macro_defs(config: _merge.Config, extra_overlays=None,
                       report: Report | None = None) -> set[str] | None:
    """Registered macro names from the EFFECTIVE index/macros.xml, or None.

    Built through build_effective rather than by unioning each directory's file,
    because an extension may ship index/macros.xml as a <diff> containing
    <remove> ops — and may ship it packed inside a .cat. A naive per-directory
    union sees neither, so it reports a macro as defined when the effective index
    no longer contains it. (Real case: xspvro removes
    turret_xen_m_beam_02_mk1_macro without re-adding it, orphaning a vanilla
    macro that six other mods reference.)

    Returns **None** when the index could not be built — never an empty set.
    Both `_refs` consumers gate on this value, and they used to do so by
    truthiness: an empty set silently disabled *every* macro-reference check and
    made ware completeness's `component` field pass unconditionally. "Could not
    read the index" and "the index defines no macros" must not be the same value.
    """
    def _fail(why: str) -> None:
        if report is not None:
            report.skip("macro-reference checks", f"{MACRO_INDEX}: {why}", degraded=True)

    try:
        merged = _merge.build_effective(MACRO_INDEX, config, extra_overlays=extra_overlays)
    except (etree.LxmlError, OSError) as exc:
        _fail(f"could not build the effective macro index ({exc})")
        return None
    if merged.tree is None:
        _fail("no effective macro index (missing from base+DLC and every overlay)")
        return None
    # DEGRADED, through the one shared helper (AUDIT-2026-09-24 VA-10). This loop
    # recorded a dropped overlay as a plain skip, so a macro index built without
    # a mod's registrations exited 0/1 instead of 3.
    note_dropped_overlays(merged, "macro-reference checks", report)
    return _refs.macro_names(merged.tree)


#: Library files that DEFINE macros/components without registering them in
#: `index/`. MEASURED: 726 macros + 30 components live here and appear in
#: NEITHER index/macros.xml NOR the effective store. Cheap to read, so they join
#: the eager half rather than forcing a corpus scan (gotcha #11).
LIBRARY_DEFS = ("libraries/character_macros.xml", "libraries/character_components.xml")


class EntityDefs:
    """Effective macro/component definitions, unioning both namespaces.

    Corpus candidates are selected from source bytes, but existence is always
    checked on the final merged document. A source hit is not a live definition.
    Caches are analysis-instance-local; no persisted un-fingerprinted set is reused.

    **Namespace-agnostic on purpose.** `<component ref>` names a MACRO inside
    `libraries/wares.xml` but a COMPONENT inside a macro file (gotcha #11), and
    in a `<diff>` carrying `<replace sel="//component">` at an asset path,
    element ancestry cannot classify it at all — only the selector target can.
    Registrations locate candidate documents; a surviving index entry cannot
    resurrect a definition removed from its file. Targeted lookups merge only
    that document's actual touchers and cache its verified names. Unindexed
    definitions use byte-prefiltered corpus discovery, followed by the same merge.
    `all_names()` scans candidates in bulk, never one corpus pass per reference.

    Namespace confusion (a macro used where a component is required) remains
    outside this union oracle's scope. `len` counts only verified names in the
    documents examined so far; it is not a full-corpus coverage denominator.
    """

    def __init__(self, config: _merge.Config, extra_overlays=None,
                 report: Report | None = None) -> None:
        self._config = config
        self._extra = list(extra_overlays or [])
        self._report = report
        self._own: set[str] | None = None
        #: bulk definition set, built once by all_names() (F65)
        self._all: set[str] | None = None
        #: name -> defined anywhere in the corpus. Cached both ways, so a mod
        #: with several unresolvable refs pays for one search each, not per use.
        self._corpus_answers: dict[str, bool] = {}
        #: How many files the corpus tier actually PARSED. The byte pre-filter's
        #: whole claim is that this stays near zero; a test asserts it.
        self.corpus_parses = 0
        #: all_names(): documents built by a MERGE vs parsed straight from their one
        #: source's bytes (V2: only a document something patches needs the merge).
        self.merged_documents = 0
        self.direct_documents = 0
        self._index: set[str] = set()
        #: The same names, case-FOLDED: the engine resolves macro/component names
        #: ignoring case (V9 -- MEASURED live, 40 of 1,500 sampled registrations are
        #: defined only under another capitalisation, e.g. XU_EP2_universe_macro).
        self._index_folded: set[str] | None = None
        self._registered_folded: dict[str, list[str]] = {}
        self._registered: dict[str, list[str]] = {}
        self._document_names: dict[str, set[str]] = {}
        self._touch: dict[str, set[Path]] | None = None
        self._unreadable_touchers: set[Path] = set()
        owners = {p.name.lower() for p in list(config.overlays) + self._extra}
        for rel in (MACRO_INDEX, _resolve.COMPONENT_INDEX):
            recorder = _merge.Recorder()
            merged = _merge.build_effective(rel, config, extra_overlays=self._extra, recorder=recorder)
            note_dropped_overlays(merged, f"entity definitions ({rel})", self._report)
            if merged.tree is not None:
                for entry in merged.tree.xpath("//entry[@name][@value]"):
                    value = entry.get("value").replace("\\", "/").lstrip("/")
                    if not value.lower().endswith(".xml"):
                        value += ".xml"
                    source = recorder.winner(entry, "value").source
                    if not value.lower().startswith("extensions/") and (source.lower() in owners or
                            source.lower().startswith("ego_dlc_")):
                        value = "extensions/" + source + "/" + value
                    self._registered.setdefault(entry.get("name"), []).append(value)
        for rel in LIBRARY_DEFS:
            merged = _merge.build_effective(rel, config, extra_overlays=self._extra)
            note_dropped_overlays(merged, f"entity definitions ({rel})", self._report)
            if merged.tree is not None:
                self._index |= set(merged.tree.xpath("//macro/@name"))
                self._index |= set(merged.tree.xpath("//component/@name"))

    def __len__(self) -> int:
        return len(set(self._index).union(*self._document_names.values()))

    def __contains__(self, name: object) -> bool:
        if name in self._index:
            return True
        if not isinstance(name, str):
            return False
        folded = name.lower()
        if self._index_folded is None:
            self._index_folded = {n.lower() for n in self._index}
            for reg, vpaths in self._registered.items():
                self._registered_folded.setdefault(reg.lower(), []).extend(vpaths)
        if folded in self._index_folded:
            return True
        for vpath in self._registered_folded.get(folded, ()):
            if folded in {n.lower() for n in self._names_for_document(vpath)}:
                return True
        # Cheap middle tier: the mod under test is ONE small directory, and its
        # own unindexed content is the common miss (MEASURED: 753 macro names
        # corpus-wide are defined in asset files but never registered). Trying it
        # before the 13s corpus parse keeps the usual case fast without making
        # the answer any weaker — a name found here is genuinely defined.
        if self._own is None:
            self._own = self._scan(self._extra, "mod")
        if folded in {n.lower() for n in self._own}:
            return True
        if folded in self._corpus_answers:
            return self._corpus_answers[folded]
        found = self._search_corpus(name)
        self._corpus_answers[folded] = found
        return found

    @property
    def corpus_scanned(self) -> bool:
        return bool(self._corpus_answers)

    def _search_corpus(self, name: str) -> bool:
        """Is *name* defined as a macro/component anywhere in base + DLC + overlays?

        Byte pre-filter, then parse to CONFIRM. Building the whole definition set
        instead parses every file — MEASURED 8.9s for the reference tree alone,
        which `gates/perf_guard.py` flagged as three regressions (ebi 5.8x,
        cpsdo_vro 4.9x, code_vgr 4.9x). Searching for the one name wanted is
        **3.5x** faster (8.9s -> 2.5s) and loses no exactness: a file whose raw
        bytes contain the name is still parsed and XPath-checked, so the speedup
        comes only from skipping files that provably cannot match.

        Per-name cost is affordable because the tier is rarely reached at all:
        MEASURED, 7 distinct references across 114 mods miss the eager tiers —
        at most 2 for any single mod.
        """
        # Case-insensitive, as the engine is (V9): the byte pre-filter folds too, and
        # the parsed candidate is compared folded. Folding the bytes is a SUPERSET
        # filter -- it can only keep more files for the exact check.
        needle = name.lower().encode("utf-8", "replace")
        folded = name.lower()
        sources = ([self._config.reference] + self._config.dlc_dirs()
                   + list(self._config.overlays) + self._extra)
        candidates = self._candidate_documents(sources, needle)
        for vpath in candidates:
            root = self._effective_definition_tree(vpath)
            if root is not None and any(
                    n.lower() == folded
                    for n in root.xpath("//macro/@name | //component/@name")):
                return True
        return False

    def _candidate_documents(self, sources, needle=None):
        """Read loose AND packed input, then deduplicate logical merge targets."""
        owners = {p.name.lower() for p in list(self._config.overlays) + self._extra}
        dlc = {p.resolve() for p in self._config.dlc_dirs()}
        candidates = {}
        for src in sources:
            unreadable = []
            try:
                for vpath, data in _scan.iter_mod_xml_bytes(src, unreadable=unreadable):
                    # UTF-16 XML and numeric character references cannot be
                    # rejected by a UTF-8 byte needle; parse those candidates.
                    if (needle is not None and needle not in data.lower()
                            and b"&" not in data and b"\0" not in data):
                        continue
                    if needle is None and not re.search(rb"<(?:macro|component)\b", data) and b"\0" not in data:
                        continue
                    if src.resolve() in dlc:
                        vpath = "extensions/" + src.name + "/" + vpath
                    vpath = _modfiles.logical_vpath(vpath, owners)
                    candidates.setdefault(vpath.lower(), vpath)
            except (OSError, ValueError) as exc:
                unreadable.append(_scan.Unreadable(str(src), str(exc)))
            for failure in unreadable:
                if self._report is not None:
                    self._report.skip("effective entity-definition inputs", str(failure), degraded=True)
        return list(candidates.values())

    def _effective_definition_tree(self, vpath):
        self.corpus_parses += 1
        try:
            config, extras = self._document_config(vpath)
            merged = _merge.build_effective(vpath, config, extra_overlays=extras)
            note_dropped_overlays(merged, "effective entity definitions ({})".format(vpath), self._report)
            return merged.tree
        except (OSError, ValueError, etree.XMLSyntaxError) as exc:
            if self._report is not None:
                self._report.skip("effective entity definitions ({})".format(vpath), str(exc), degraded=True)
            return None

    def all_names(self) -> set[str]:
        """Every macro/component name defined anywhere — built ONCE, not per name.

        **This is not an optimisation of `__contains__`; it is the accessor a
        MANY-NAME caller must use instead.**

        `__contains__` answers a per-name question at per-name cost, and its own
        docstring states the population that makes that affordable: *"7 distinct
        references across 114 mods miss the eager tiers — at most 2 for any single
        mod."* True for a MOD, which is the caller it was built for. It is not a
        property of the tool, it is a property of that caller.

        MEASURED 2026-08-26 with a different caller: one savegame carries **5,022**
        distinct macro references, ~2,500 of which miss the eager index. The lazy
        tier re-enumerates base + DLC + every overlay for each of them (~2.5s a
        call) and blew a 600s cap with no result. The same question answered in
        bulk took **19.4s** then. Registered as BLIND-SPOTS **F65**.

        Since every candidate became a MERGED document (d6cc98a/ded0a21), only a document
        something patches is merged; one source's full file is parsed from the bytes
        already read (V2). MEASURED 2026-10-18 on the live install (19,763 documents,
        22,860 names, identical per document): 116 s merging all -> 31-44 s, 3,381 merged.

        Cached, so repeated calls are free. Returns the union of the eager index
        and the corpus scan — the same set `__contains__` would answer from,
        never a narrower one.
        """
        if self._all is None:
            sources = ([self._config.reference] + self._config.dlc_dirs()
                       + list(self._config.overlays) + self._extra)
            self._all = set(self._index) | self._scan(sources, "corpus")
        return self._all

    def _names_for_document(self, vpath):
        key = vpath.lower()
        if key not in self._document_names:
            root = self._effective_definition_tree(vpath)
            self._document_names[key] = (set(root.xpath("//macro/@name | //component/@name"))
                                         if root is not None else set())
        return self._document_names[key]

    def _document_config(self, vpath):
        """Merge only actual touchers, retaining the same order and nested owners."""
        overlays = list(self._config.overlays) + self._extra
        owners = {p.name.lower() for p in overlays}
        if self._touch is None:
            self._touch = {}
            for source in overlays:
                try:
                    for path in _modfiles.mod_xml_paths(source).values():
                        key = _modfiles.logical_vpath(path, owners).lower()
                        self._touch.setdefault(key, set()).add(source)
                except (OSError, ValueError) as exc:
                    self._unreadable_touchers.add(source)
                    if self._report is not None:
                        self._report.skip("definition overlay enumeration", str(exc), degraded=True)
        key = _modfiles.logical_vpath(vpath, owners).lower()
        wanted = self._touch.get(key, set()) | self._unreadable_touchers
        return (replace(self._config, overlays=tuple(p for p in self._config.overlays if p in wanted)),
                [p for p in self._extra if p in wanted])

    def _scan(self, sources, tier: str) -> set[str]:
        """Every `<macro name>` / `<component name>` defined in *sources*.

        The index is NOT the definition set (gotcha #11) — MEASURED, 753 macro
        names are defined in asset files without ever being registered, and 6 of
        7 sampled "missing" references were real entities living in a library.
        """
        names: set[str] = set()
        docs, sources_of, unreadable = self._bulk_documents(sources)
        for key, (vpath, data) in docs.items():
            if (key not in self._document_names and data is not None and not unreadable
                    and len(sources_of.get(key, ())) == 1):
                root = self._direct_root(data)
                if root is not None:
                    self.direct_documents += 1
                    self._document_names[key] = set(root.xpath(
                        "//macro/@name | //component/@name"))
            if key not in self._document_names:
                self.merged_documents += 1
            names |= self._names_for_document(vpath)
        return names

    @staticmethod
    def _direct_root(data):
        """*data* parsed, when it IS its document's effective tree; else None (merge).

        Only a full, non-diff file qualifies: a `<diff>` with nothing under it defines
        nothing, and the merge is what says so."""
        try:
            root = _merge.parse_bytes(data)
        except etree.XMLSyntaxError:
            return None  # silent-ok: falls back to the merge path, which reports it as before
        return None if root.tag == "diff" else root

    def _bulk_documents(self, sources):
        """Candidate definition documents for a bulk scan, with WHO ships each one.

        Returns ({key: (vpath, bytes)}, {key: {physical sources}}, unreadable?). A key's
        source set counts every file the merge for that document could apply -- every
        XML, not only definition candidates: a DLC file at `extensions/<dlc>/<rel>` also
        counts for base `<rel>` (build_effective applies DLC diffs there), and a
        mod-on-mod patch counts for the owner's logical document. Physical identity (the
        file's own path) keeps the reference tree's unpacked `extensions/<dlc>/` and the
        same DLC root from counting one file twice. A key whose bytes come from two
        sources keeps no bytes: it is merged."""
        owners = {p.name.lower() for p in list(self._config.overlays) + self._extra}
        dlc = {p.resolve() for p in self._config.dlc_dirs()}
        docs: dict[str, tuple[str, bytes | None]] = {}
        sources_of: dict[str, set[str]] = {}
        any_unreadable = bool(self._unreadable_touchers)
        for src in sources:
            unreadable = []
            in_dlc = src.resolve() in dlc
            try:
                for vpath, data in _scan.iter_mod_xml_bytes(src, unreadable=unreadable):
                    ident = os.path.normcase(str(src / vpath))
                    if in_dlc:
                        logical = "extensions/" + src.name + "/" + vpath
                        keys = (logical, vpath)
                    else:
                        logical = _modfiles.logical_vpath(vpath, owners)
                        keys = (logical,)
                    for k in keys:
                        sources_of.setdefault(k.lower(), set()).add(ident)
                    if not re.search(rb"<(?:macro|component)\b", data) and b"\0" not in data:
                        continue
                    key = logical.lower()
                    # A mod-on-mod file (extensions/<owner>/<rel>) is never ITS document's
                    # tree as read: the merge decides what it lands on. MEASURED live: 19
                    # full files under extensions/cpsdo_zb_modpack/assets/prop/ that the
                    # owner does not ship (it has props/) merge to NO tree.
                    direct = data if (key not in docs and logical == vpath) else None
                    docs[key] = (docs[key][0], None) if key in docs else (logical, direct)
            except (OSError, ValueError) as exc:
                unreadable.append(_scan.Unreadable(str(src), str(exc)))
            for failure in unreadable:
                any_unreadable = True
                if self._report is not None:
                    self._report.skip("effective entity-definition inputs", str(failure),
                                      degraded=True)
        return docs, sources_of, any_unreadable


def _mod_identity(mod_dir: Path, report: Report | None = None) -> tuple[str, str]:
    """(folder name, content.xml id) for the mod under test; id may be ''.

    An empty id is not free: Tier B excludes the mod's own installed copy by
    folder name AND by id, so losing the id means a mod deployed under a
    different folder name gets merged in as if it were a third-party overlay —
    the mod validated against itself.
    """
    folder = mod_dir.resolve().name
    mod_id = ""
    cx = mod_dir / "content.xml"
    if cx.is_file():
        try:
            mod_id = _merge.parse_file(cx).get("id") or ""
        except etree.XMLSyntaxError as exc:
            if report is not None:
                report.skip("mod identity",
                            f"content.xml will not parse ({exc}) — this mod's own installed "
                            "copy can only be excluded by folder name, not by id")
    return folder, mod_id


@dataclass
class TierB:
    """The two trees a cross-mod check needs. They are NOT interchangeable.

    `patch_time` is truncated at the mod's own load-order position — the tree that
    exists at the moment the engine applies this mod's diffs. `final` is every
    installed extension — the tree that exists once loading is done, which is what
    a *runtime* lookup (does macro X resolve? does ware Y exist?) actually sees.

    Using one for the other's job is wrong in both directions, and `X4CapturableXenonXL`
    demonstrates both at once (measured 2026-07-28 — see `tier_b_overlays`).
    """
    patch_time: tuple[Path, ...] = ()
    final: tuple[Path, ...] = ()
    notes: list[str] = field(default_factory=list)


def tier_b_overlays(mod_dir: Path,
                    report: Report | None = None) -> tuple[tuple[Path, ...], list[str]]:
    """The PATCH-TIME tree only, as `(overlay_dirs, notes)`.

    Thin wrapper over `tier_b_trees` for callers that genuinely only care about the
    tree a `sel=` sees (the diff oracle, ad-hoc scripts). Anything validating a mod
    should use `tier_b_trees` and honour BOTH trees.
    """
    t = tier_b_trees(mod_dir, report)
    return t.patch_time, t.notes


def tier_b_trees(mod_dir: Path, report: Report | None = None) -> TierB:
    """Both installed-extension trees for the mod under test — see `TierB`.

    `patch_time` holds the roots that load BEFORE the mod, which is the tree its
    `sel=` selectors actually see. This is what makes a cross-mod patch
    checkable: Tier A (base+DLC) cannot see a node another mod adds, nor notice
    one another mod removed, so both directions produce false results —
    a false 'sel matched nothing' for the former, a false 'OK' for the latter.

    **Truncated at the mod's own load-order position (fixed 2026-07-26).** Merging
    *every* other mod builds a tree that never exists at the moment this mod is
    patched: a node some LATER mod adds is already present, so an op that the
    engine skips looks fine to us. Measured against the engine's own debug.txt for
    `cpsdo_faction` (192 rejected ops):

        overlays = all others (old)  -> 165 agree, 27 FALSE OK, 3 false alarm
        overlays = those before (new)-> 192 agree,  0 FALSE OK, 3 false alarm

    All 27 were `//wares/ware[@id='ishield_cpsdo_*']/owner/@faction`: `cpsdo_vro`
    *adds* those wares and loads AFTER `cpsdo_faction`, which patches them — neither
    declares a dependency on the other, so the engine runs the patch first and skips
    it. A real upstream bug our optimistic tree was hiding.

    **The two trees ARE now split (2026-07-28).** The limitation this docstring used
    to describe as theoretical was confirmed against the engine, so reference /
    file-existence / connection checks run on `TierB.final` while `sel=` resolution
    keeps this patch-time tree. `X4CapturableXenonXL` (load position 79) proves both
    directions at once, which is why one tree cannot serve both:

      - patch-time is RIGHT for `sel=`: its three `<remove sel="…connection_cockpit">`
        ops match the vanilla node at position 79. Against the final tree they read as
        'sel matched nothing' — 3 false alarms — because `xspvro` (position 92)
        replaces that macro file wholesale.
      - final is RIGHT for references: `xspvro` re-points `ship_xen_xl_carrier_01_a_macro`
        at component `ship_pla_xl_battleship`, which has 243 connections and lacks
        `connection_shieldgen_external03` (vanilla `ship_xen_xl_carrier_01` HAS it).
        The mod's `libraries/loadouts.xml:44` still asks for it, so at runtime the
        loadout is broken — a REAL defect the patch-time tree cannot see, and which
        the deployed copy therefore reported as 0 errors.

    Not engine-logged: a loadout mismatch only surfaces when the ship is instantiated,
    and the reference log is a load-time capture. The chain is verified statically
    instead (load order 79 < 92; both mods ship the macro file, so the later wins).

    The mod under test is excluded by BOTH folder name and content.xml id, since
    a dev folder is usually also deployed under extensions\\ and would otherwise
    be merged in twice (its own ops pre-applied, masking real misses).

    Load order here is the engine's MEASURED rule (`_loadorder.compute_load_order`:
    case-insensitive folder order, dependencies in repeated passes).
    """
    notes: list[str] = []

    def _fallback(why: str) -> TierB:
        """Tier B was asked for and could not be built.

        The fallback tree is base+DLC — i.e. Tier A — which cannot see anything
        another mod adds or removes. A cross-mod patch validated against it is
        not validated at all. This used to emit a note and exit 0, so a gate or
        CI step read the run as a pass; it is now a degraded skip, which the CLI
        turns into a non-zero exit.
        """
        if report is not None:
            report.skip("Tier B cross-mod validation",
                        f"{why}; fell back to base+DLC (Tier A) — cross-mod selectors "
                        "cannot resolve against that tree", degraded=True)
        return TierB((), (), [f"Tier B: {why}; fell back to base+DLC only"])

    try:
        # ACTIVE: Tier B models the tree the ENGINE builds, so a mod that is
        # installed-but-disabled must not supply a selector target. Using the
        # on-disk set here made a selector that only matches inside a disabled
        # mod report OK -- a FALSE PASS, in the mode whose whole purpose is
        # catching silent no-ops.
        not_loaded: list[str] = []
        mods = _registry.mods("active", dropped=not_loaded)
    except OSError as exc:
        return _fallback(f"could not scan installed mods ({exc})")
    # DISCLOSED, never silent (AUDIT-2026-09-24 final review): a mod the engine will
    # not load is correctly absent from both trees, but a selector that only resolves
    # inside it would otherwise read "sel matched nothing" with no hint why. Not
    # degraded -- the tree is the engine's tree -- but NAMED, mod and reason.
    for msg in not_loaded:
        notes.append(f"Tier B: left out of both trees -- {msg}")
    note_not_loaded(mods, "Tier B world model: a mod the engine does not load", report)
    if not mods:
        return _fallback("no installed extensions found")

    folder, mod_id = _mod_identity(mod_dir, report)
    # ONE placement rule with x4compat and x4stats (`_loadorder.place_candidate`):
    # key = the installed copy's folder when it matches by folder or id (either case),
    # else the dev copy's own folder; dependencies from THIS copy's manifest -- the
    # version under test. It used to match case-SENSITIVELY (so a case-different dev
    # copy merged its own installed copy as a third party) and to order by the
    # INSTALLED manifest (so a dependency new in the dev copy was ignored).
    placement = _loadorder.place_candidate(mods, mod_dir, cand_id=mod_id)
    installed = bool(placement.excluded)
    mods = placement.mods
    by_folder = {m["folder"]: m for m in mods}
    order = _compat.compute_load_order(mods)
    # What the order and the set ASSUME (an unmeasured root, a case-only dependency
    # match, a duplicate id, ...) rides on the placed list; rendered AFTER ordering so
    # the order's own records are in it (release review, finding 3).
    model = _registry.model_note(mods)
    if model:
        notes.append(f"Tier B: load-order model -- {model}")

    dirs: list[Path] = []       # patch-time: up to the mod's own position
    final_dirs: list[Path] = []  # runtime: every other installed extension
    skipped = ", ".join(m["folder"] for m in placement.excluded)
    placed = False
    for name in order:
        m = by_folder.get(name)
        if m is None:
            continue
        if m is placement.entry:
            # The mod under test is excluded from BOTH trees — merging its own copy
            # would pre-apply its ops and mask exactly the misses we look for.
            placed = True
            continue  # keep walking: later mods are invisible to selectors, but REAL at runtime
        p = Path(m["path"])
        if p.is_dir():
            final_dirs.append(p)
            if not placed:  # everything after the mod loads LATER — not visible to our selectors
                dirs.append(p)

    # `placed` is ALWAYS True: `place_candidate` puts the candidate's entry in the set
    # and `compute_load_order` returns every folder it is given. The old `else` branch
    # ("assumed to load LAST") was unreachable since LO-6 placed dev-only mods; it is
    # gone, and tests/test_tierb_nested_later_target.py pins the invariant.
    if not placed:  # pragma: no cover - structural invariant, refuse rather than guess
        return _fallback("the mod under test is missing from the computed load order")
    # ACTIVE, not installed (release review, finding 7): `order` is the active set with
    # the candidate in it, so the other mods number len(order) - 1.
    notes.append(
        f"Tier B: merged {len(dirs)} extension(s) that load BEFORE this mod "
        f"(of {len(order) - 1} other active) — the tree its selectors actually see")
    if installed:
        notes.append(f"Tier B: excluded the mod under test's installed copy '{skipped}' "
                     "and everything loading after it")
    else:
        notes.append("Tier B: this mod is NOT installed; it was placed where the engine "
                     "would load it (folder order + its manifest's dependencies) and "
                     "everything loading after that position was excluded")
    if len(final_dirs) > len(dirs):
        notes.append(
            f"Tier B: reference/connection checks additionally see the {len(final_dirs) - len(dirs)} "
            "extension(s) loading AFTER it — those are invisible to selectors but real at runtime")
    notes.append("Tier B: load order follows the engine's MEASURED rule (case-insensitive "
                 "folder order, dependencies in repeated passes; see gates/load_order_oracle.py)")
    return TierB(tuple(dirs), tuple(final_dirs), notes)


@dataclass
class Finding:
    severity: str   # "error" | "warn" | "info"
    category: str   # "sel" | "ref" | "completeness" | "path" | "diff" | ...
    message: str
    vpath: str = ""
    line: int = 0
    #: For category "sel": the selector VERBATIM. The message renders it with
    #: optional suffixes ("(silent)", "[if= passed: ...]"), so anything comparing
    #: selectors op-for-op against the engine log must read this field, not scrape
    #: the prose. `x4debug crosscheck` fabricated 6 findings by doing the latter.
    sel: str = ""


@dataclass
class Skipped:
    """A piece of work the validator could NOT do.

    Every false-pass this tool has ever shipped had the same shape: a helper hit
    an unreadable file, returned a falsy sentinel (`set()` / `[]` / `None`), and
    the caller could not tell "found nothing" from "could not look". With no
    channel to say *"I did not check this"*, silence rendered as OK.

    *degraded* = the skip disabled an ENTIRE check, so a clean run is not
    evidence of correctness and the CLI exits non-zero. A partial skip (one
    unreadable file among many) is reported but does not gate.
    """
    what: str       # which check / input was affected
    why: str        # the concrete reason, including the exception text
    degraded: bool = False


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Work that was NOT done. See the Skipped docstring — this is the channel
    #: whose absence caused every "nothing examined rendered as OK" defect.
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def degraded(self) -> list[Skipped]:
        """Skips that disabled a whole check — the run cannot be trusted as a pass."""
        return [s for s in self.skipped if s.degraded]

    def add(self, *args, **kwargs) -> None:
        self.findings.append(Finding(*args, **kwargs))

    def skip(self, what: str, why: str, degraded: bool = False) -> None:
        self.skipped.append(Skipped(what, why, degraded))


def note_not_loaded(mod_list, what: str, report: "Report | None") -> None:
    """Wire a `_registry.mods()` result's exclusions into the report (BLIND-SPOTS F139).

    One NOT CHECKED skip per mod the read left out -- a mod the engine will not load,
    or one whose manifest will not parse -- naming it and why. NOT degraded: the tree
    is the engine's own tree, so nothing the check premised on is missing; but a
    selector or sibling that exists only inside that mod would otherwise read as
    absent with no thread to pull.

    Deduplicated on the record, across checks: Tier B, the variant check and the
    nested-script scope all read the same active set in one run, and one cause
    should read as one line. A `None` report drops it, as for `note_dropped_overlays`.
    """
    if report is None:
        return
    for folder, why in _registry.left_out(mod_list).items():
        msg = f"{folder}: {why}"
        if any(s.why == msg for s in report.skipped):
            continue
        report.skip(what, msg)


def note_dropped_overlays(merged, what: str, report: "Report | None") -> None:
    """Wire `MergeResult.skipped` into the report. ONE implementation, on purpose.

    `MergeResult.skipped`'s own field comment states the contract: *"Work NOT done:
    overlays that could not be parsed and were left out of the tree. Without this
    channel a malformed overlay is indistinguishable from an absent one, and the
    resulting tree looks complete when it is not."*

    It was honoured at 3 of the 14 `build_effective` call sites in this file. The
    other 11 threw the list away, so an overlay belonging to a THIRD-PARTY mod that
    would not parse produced gating findings against an UNRELATED mod, with the
    reason erased. REPRODUCED with two overlays differing only by a missing
    `</diff>`, the mod under test unchanged between runs:

        overlay well-formed : errors=[]                              NOT CHECKED=[]  exit 0
        overlay MALFORMED   : ['ware reference does not resolve: ..'] NOT CHECKED=[]  exit 1

    and `_merge` had recorded it all along, as
    `skipped=['ov_broken/libraries/wares.xml: malformed XML, overlay skipped ..']`.

    Under `--tier b` -- the mode CLAUDE.md tells you to use for anything cross-mod
    -- every installed extension is an overlay, so ONE malformed file anywhere in a
    125-mod install can fabricate gating errors in a mod that has nothing to do
    with it, and leave the reader no thread to pull.

    DEGRADED, matching the three sites that already did this: the verdict is
    computed against an incomplete tree, so the check's PREMISE is disabled rather
    than one file. `README.md` names this exact case as a code 3 -- "an overlay that
    would not parse so the comparison tree is incomplete". It was producing code 1.

    MEASURED before landing: of the 111 files the installed set supplies at the
    vpaths these call sites build, ZERO are malformed, so this changes no verdict
    today. The 11 malformed documents in the corpus all sit under `tmp/`, `backup/`,
    `md_debug/` or a non-active-language t-file -- paths the engine never opens and
    the merge never loads (CLAUDE.md #29).

    Deduplicated because `Report.skip` does not: the same dropped overlay reaches
    several of these builds through different vpaths, and one cause should read as
    one line.

    A `None` report is the `EntityDefs` path, whose caller genuinely may not have
    one. That is the only case allowed to drop the message, and it is the reason
    that parameter is optional.
    """
    if report is None:
        return
    for msg in merged.skipped:
        why = ("an overlay could not be parsed, so the comparison tree is "
               "incomplete: %s" % msg)
        if any(s.what == what and s.why == why for s in report.skipped):
            continue
        report.skip(what, why, degraded=True)


def iter_diff_files(mod_dir: Path):
    """Yield (virtual_path, diff_root) for every <diff> file in the mod.

    Delegates to iter_mod_xml_roots so PACKED mods are covered too. Until
    2026-07-26 this walked mod_dir.rglob("*.xml") directly, which finds nothing in
    a mod shipped as ext_01.cat/.dat — so sel-resolution, the tool's core check,
    silently examined ZERO ops and the run printed "OK: no issues found". That is a
    whole-mod false pass: `moreroomsforships` reported clean while the engine was
    rejecting 6 of its ops. 9 of the 10 mods with cardinality failures in the
    reference log are packed, so this blind spot covered most of the real damage.
    """
    for vpath, root in iter_mod_xml_roots(mod_dir):
        if root.tag == "diff":
            yield vpath, root



def iter_mod_xml_roots(mod_dir: Path, predicate=None):
    """Yield (virtual_path, root) for every XML file owned by a mod.

    Thin wrapper over `_scan.iter_mod_xml` — loose wins over packed, matching
    the engine. Files that will not parse are skipped here and reported ONCE by
    `check_readability`, rather than at each of this function's eight call sites.
    """
    yield from _scan.iter_mod_xml(mod_dir, predicate)


#: Directory segments whose contents the engine never loads — a mod's own
#: scratch space. A malformed file in one of these is untidy, not broken.
#: Measured over 100 installed mods: 10 of the 12 unparseable files live here
#: (all in VRO's tmp/, backup/ and md_debug/), so without this split the check
#: would be 83% noise.
SCRATCH_DIRS = frozenset({"tmp", "backup", "backups", "old", "unused", "md_debug", ".git"})


def check_readability(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Report every XML file in the mod that will not parse.

    Until 2026-07-28 these vanished: `iter_mod_xml_roots` ended both its branches
    in a bare `except XMLSyntaxError: continue`. Neither nearby guard covered it —
    `MergeResult.skipped` is a different code path (overlay-tree parses), and the
    "checked == 0" warning only fires at *zero*, so five good diffs plus one
    malformed printed nothing at all.

    A file at a real content path is also a `warn`: the engine's parser is no more
    forgiving than lxml, so it cannot load that file either. Confirmed live on two
    installed mods — `cpsdo_faction/t/0001-l088.xml` (stray `</t>`, drops a whole
    Traditional-Chinese page) and `xspvro/assets/units/size_xl/xen_shell.xml`
    (`<offset>`/`<lights>` mis-nested, drops a component).
    """
    # No manifest = not an extension. X4 discovers mods by content.xml, so a folder
    # without one is never loaded and every verdict below it is about a file the
    # engine will not read. Reporting that clean is a false pass about the whole run,
    # so it gates. Packed mods keep content.xml LOOSE (it is read before the catalog),
    # so this is safe for them too.
    if not (mod_dir / MANIFEST).is_file():
        report.skip("this folder as an extension",
                    f"no {MANIFEST} — X4 discovers extensions by their manifest, so it "
                    "would never load this folder at all", degraded=True)

    unreadable: list[_scan.Unreadable] = []
    for _ in _scan.iter_mod_xml(mod_dir, unreadable=unreadable):
        pass
    for u in unreadable:
        segments = {s.lower() for s in u.vpath.split("/")[:-1]}
        scratch = bool(segments & SCRATCH_DIRS)
        report.skip("this file was not examined at all",
                    f"{u.vpath}: will not parse ({u.why})")
        if not scratch:
            report.add("warn", "parse",
                       f"file will not parse, so the engine cannot load it either: {u.why}",
                       u.vpath)


def _check_ops(diff_root, tree, vpath: str, report: Report) -> None:
    """Report every diff op the ENGINE would not apply, deriving from `apply_diff`.

    **One implementation.** This used to re-evaluate each op's `sel=` itself, in
    parallel with `_merge.apply_diff` — two independent code paths answering the
    same question, which is the shape `docs/BLIND-SPOTS.md` was built around. They
    disagreed in both directions, because this one evaluated every op against the
    tree as it stood BEFORE the mod's own ops, while the engine applies them in
    document order to a mutating tree:

    * `<remove sel="X"/>` then `<replace sel="X/y/@z">` — the engine skips the
      replace; we reported nothing. MEASURED: 2 ops, 1 mod (`moreroomsforships`),
      confirmed against the engine's own log via `x4debug crosscheck`.
    * `<add sel="X">` then `<replace sel="X/new/@z">` — the node exists only after
      the diff's own add, so we reported a FALSE error. MEASURED: 458 ops select
      into an earlier `<add>` across 18 mods — 229x the first case, and the more
      expensive direction, since a fabricated error on a gating check is worse
      than a missed one.

    Deriving from `apply_diff` also picks up two verdicts this function used to
    drop on the floor via a bare `continue`: an op tag `diff.xsd` does not admit
    (`<relace>`), and an `add type=` we do not model.

    The tree is DEEP-COPIED first. `apply_diff` mutates, and the merged tree
    belongs to the caller and is reused by later checks — corrupting it would be a
    worse defect than the one being fixed.
    """
    # apply_diff appends exactly one AppliedOp per element with a string tag and
    # skips comments/PIs without appending, so pairing must use the same filter.
    # Asserted rather than assumed: a silent mis-pairing would attribute findings
    # to the wrong op and line, which is worse than not checking at all.
    ops = [o for o in diff_root if isinstance(o.tag, str)]
    applied = _merge.apply_diff(copy.deepcopy(tree), diff_root)
    if len(applied) != len(ops):  # pragma: no cover - structural invariant
        report.skip("diff op checking",
                    f"{vpath}: apply_diff returned {len(applied)} verdicts for "
                    f"{len(ops)} ops — cannot pair them, so no op was checked",
                    degraded=True)
        return

    for op, a in zip(ops, applied):
        if a.ok and not a.skipped_if:
            continue
        cond = op.get("if")
        if a.skipped_if:
            # A guarded op whose guard is false is a DESIGNED no-op — the idiom for
            # targeting content from a mod that may not be installed. Never an error.
            report.add("info", "sel",
                       f"<{op.tag}> skipped: if= is false ({cond}) — "
                       "guarded no-op, sel= not evaluated", vpath, a.line)
        elif a.detail.startswith("invalid if=:"):
            report.add("error", "sel",
                       f"<{op.tag}> invalid if= ({a.detail.split(': ', 1)[1]})",
                       vpath, a.line)
        elif a.detail.startswith("invalid sel=:"):
            report.add("error", "sel",
                       f"<{op.tag}> invalid sel= ({a.detail.split(': ', 1)[1]})",
                       vpath, a.line)
        elif a.detail == "sel matched nothing":
            # A guard that IS open but whose sel still misses is a real problem:
            # the author asserted the target should exist.
            report.add("warn" if a.silent else "error", "sel",
                       f"<{op.tag}> sel matched nothing: {a.sel}"
                       + (" (silent)" if a.silent else "")
                       + (f" [if= passed: {cond}]" if cond else ""), vpath, a.line,
                       sel=a.sel)
        elif a.ambiguous:
            # RFC 5261: sel must select exactly ONE node. X4 logs "Multiple matching
            # nodes ... Skipping node" and applies NOTHING — the patch looks fine
            # and does nothing. Disambiguate with a predicate.
            report.add("error", "sel",
                       f"<{op.tag}> sel matched {a.detail.split()[2]} nodes "
                       "(must match exactly 1 — the engine SKIPS ambiguous ops, so "
                       f"this silently does nothing): {a.sel}",
                       vpath, a.line, sel=a.sel)
        elif a.detail.startswith("unknown op "):
            # `diff.xsd` admits exactly add/replace/remove, so <relace sel="..."> is a
            # schema violation the engine ignores outright. The old code skipped these
            # with a bare `continue`, so a typo'd op tag vanished and the file still
            # reported OK. detail already names the tag — do not print it twice.
            report.add("error", "sel", a.detail, vpath, a.line, sel=a.sel)
        else:
            # Everything else apply_diff refuses: an unmodelled
            # add type=, or a mutation helper that returned a reason. All of these
            # were invisible here before — the first two behind a bare `continue`.
            report.add("error", "sel", f"<{op.tag}> {a.detail}", vpath, a.line,
                       sel=a.sel)


def _installed_folders() -> set[str] | None:
    """Lowercased folder names of every installed extension, or None if unscannable.

    None, not `set()`: an empty set means "no mods are installed", which reads as
    a definite fact about the world. The caller must be able to tell that apart
    from "the extensions directory could not be listed".
    """
    try:
        # INSTALLED, deliberately: this asks whether `extensions/<folder>/` names
        # a real extension directory or is a typo (gotcha #6). That is a question
        # about the DISK, and it stays true whether or not the target is switched
        # on -- a patch aimed at a disabled mod is inert, not misspelled.
        return {m["folder"].lower() for m in _registry.mods("installed")}
    except OSError:
        # silent-ok: None is this function's whole contract (see docstring) —
        # distinct from set(). _path_verdict branches on it and reports
        # "unverifiable" rather than silently excusing the patch.
        return None


#: `_inactive_folders`' reason for a mod that is switched OFF (manifest or profile).
DISABLED = "disabled"


def _inactive_folders() -> dict[str, str] | None:
    """{lowercased folder: why} for every extension that is INSTALLED but NOT
    active, or None if unscannable.

    Two different reasons, kept apart (BLIND-SPOTS F139): :data:`DISABLED` for a mod
    switched off in its manifest or the profile, and the engine's own record
    ("NOT LOADED by the engine -- required dependency ...") for an ENABLED mod the
    engine leaves out. This used to be `installed - active` under the name
    `_disabled_folders`, so the second kind was reported as "installed but
    disabled" -- a claim about a switch the user never touched.

    Derived from ONE registry read of both scopes, so "inactive" is positive
    knowledge about a real folder -- never "absent from the active set", which is
    also what an unknown folder looks like."""
    try:
        installed = {m["folder"].lower() for m in _registry.mods("installed")}
        active_list = _registry.mods("active")
    except OSError:
        # silent-ok: None is the contract, as for `_installed_folders`; the one
        # caller then declines to call the target inactive.
        return None
    active = {m["folder"].lower() for m in active_list}
    excluded = {f.lower(): why for f, why in _registry.left_out(active_list).items()}
    return {f: excluded.get(f, DISABLED) for f in installed - active}


def _in_tree(target: str, config: _merge.Config) -> bool:
    """Is extension *target* one of the roots this run actually merges?"""
    t = target.lower()
    return any(p.name.lower() == t
               for p in (*config.overlays, *config.final_overlays))


def _target_inactive_why(target: str, config: _merge.Config) -> str | None:
    """Why *target* is INSTALLED but NOT loaded (see `_inactive_folders`), or None
    when it is loaded, merged here, not installed, or unknown."""
    if _in_tree(target, config):
        return None
    inactive = _inactive_folders()
    return None if inactive is None else inactive.get(target.lower())


def _target_disabled(target: str, config: _merge.Config) -> bool:
    """True only when *target* is INSTALLED, NOT active, and not merged here --
    switched off OR left out by the engine; `_target_inactive_why` says which.

    Every unknown answers False, so this can only ever excuse a patch whose
    target is positively known not to load."""
    return _target_inactive_why(target, config) is not None


def _inactive_phrase(target: str, config: _merge.Config) -> str:
    """'DISABLED' or the engine's reason, for a target `_target_disabled` excused."""
    why = _target_inactive_why(target, config)
    return "DISABLED" if why in (None, DISABLED) else why


def _inactive_target_reason(vpath: str, config: _merge.Config) -> str | None:
    """Why *vpath*'s nested target is not loaded ("not installed" / "installed
    but disabled"), or None if it is loaded, not nested, a DLC, or unknown."""
    owned = config.packed_dlc_names()
    nested = _merge._nested_target(vpath, owned)
    if nested is None or nested[0].lower() in owned:
        return None
    if _in_tree(nested[0], config):
        return None
    installed = _installed_folders()
    if installed is None:
        return None
    if nested[0].lower() not in installed:
        return f"'{nested[0]}' is not installed"
    if _target_disabled(nested[0], config):
        phrase = _inactive_phrase(nested[0], config)
        return (f"'{nested[0]}' is installed but "
                f"{'disabled' if phrase == 'DISABLED' else phrase}")
    return None


def _later_loading_owner(nested: tuple[str, str], config: _merge.Config) -> Path | None:
    """The root of nested target T when T is in the RUNTIME tree but not the
    PATCH-TIME one (active, loading after the mod under test) AND ships the file;
    else None. Tier A has no runtime tree, so this is None there.

    Both clauses are required: a T that loads earlier is in `config.overlays` and its
    miss is a genuine path error, and so is a later T that does not ship the file."""
    target = nested[0].lower()
    if any(p.name.lower() == target for p in config.overlays):
        return None
    owner = next((p for p in config.final_overlays if p.name.lower() == target), None)
    if owner is None:
        return None
    unreadable: list[str] = []
    if _merge.overlay_root(owner, nested[1], unreadable) is None and not unreadable:
        return None             # T does not ship it: a real path mismatch
    return owner


def _no_base_finding(vpath: str, config: _merge.Config) -> tuple[str, str, str]:
    """(severity, category, message) for a file whose base tree could not be built.

    A NESTED cross-mod patch — `extensions/<target>/<rel>` — whose <target> is not
    installed is a DESIGNED no-op, not an error: compatibility-patch mods ship
    patches for dozens of optional targets and you only have some. The engine simply
    never loads that file. Treating it as an error is the same mistake as flagging an
    `if=`-guarded op whose guard is false, and it is loud: enabling packed-mod input
    made `moreroomsforships` report 76 errors, of which 72 were absent targets.

    `extensions/ego_dlc_*/...` is a SEPARATE case, because `_nested_target` treats
    `ego_dlc_*` as ordinary base content living in `reference/extensions/` — which is
    only true for DLC that were actually unpacked there. Real case: `ego_dlc_mini_01`
    (Hyperion Pack) and `ego_dlc_mini_02` (Envoy Pack) are genuinely INSTALLED and
    PACKED in the live game (`extensions/ego_dlc_mini_01/ext_01.cat`), but `reference/`
    was only ever unpacked for the 6 DLC named in CLAUDE.md — these two are simply
    missing from it. So this is not "target not installed" (it is), and not "path
    mismatch" (the file may genuinely exist) — it is "cannot verify with the reference
    we have". Reporting it as ERROR asserts non-existence we cannot actually back up.

    Target installed but no base found is still a real error — that is a genuine
    path mismatch or a file the target does not actually ship.
    """
    nested = _merge._nested_target(vpath, config.packed_dlc_names())
    if nested is not None:
        installed = _installed_folders()
        if installed is None:
            # Could not list the extensions dir. Downgrading to "not installed"
            # would silently excuse a genuine path mismatch; saying so is the
            # only honest option.
            return ("info", "unverifiable",
                    f"patch targets extension '{nested[0]}', but the installed extension "
                    "set could not be listed — cannot tell an inactive patch from a broken path")
        # A packed-only DLC is owned like a mod (so it lands here) but is NOT in
        # scan_installed, which filters ego_dlc_* out. Calling it "not installed"
        # would be flatly false.
        if nested[0].lower() not in installed | config.packed_dlc_names():
            return ("info", "inactive",
                    f"patch targets extension '{nested[0]}', which is not installed — "
                    "the engine never loads this file (designed no-op, not an error)")
        # Installed is a fact about the DISK; whether the engine LOADS the target
        # is a fact about the active set. A target that is on disk but switched
        # off is neither misspelled nor present in any tree, so it fell through to
        # "path mismatch" ERROR under Tier B (AUDIT-2026-09-24 VA-6).
        if _target_disabled(nested[0], config):
            phrase = _inactive_phrase(nested[0], config)
            state = ("DISABLED — the engine never loads this file while it is off"
                     if phrase == "DISABLED" else
                     f"{phrase} — so the engine never loads this file")
            return ("info", "inactive",
                    f"patch targets extension '{nested[0]}', which is installed but "
                    f"{state} (designed no-op, not an error)")
        owner = _later_loading_owner(nested, config)
        if owner is not None:
            # ACTIVE, SHIPS the file, loads AFTER this mod -- and `_sel_check_file`
            # already retried with the owner in the tree, so reaching here means even
            # that built nothing (the owner's copy is unreadable, or is itself a
            # <diff>). Not a path mismatch: the path names a real file.
            return ("warn", "unverifiable",
                    f"patch targets extension '{owner.name}', which is ACTIVE, loads "
                    f"AFTER this mod and ships '{nested[1]}', but no base tree could be "
                    "built from that file (unreadable, or itself a <diff>), so this "
                    "patch's ops were not evaluated")
        owner = next((p for p in config.overlays if p.name.lower() == nested[0].lower()), None)
        if owner is not None and nested[0].lower() not in config.packed_dlc_names():
            oroot = _merge.overlay_root(owner, nested[1], [])
            if oroot is not None and oroot.tag == "diff":
                # The path names a real file; it is the owner's PATCH of a file owned
                # elsewhere, not a document of its own. MEASURED 2026-10-19 (engine log,
                # 1 of 1 live, a doubly-nested `extensions/<mod>/extensions/<DLC>/...`):
                # the engine never opened a nested patch of such a file, while it opened
                # all 10 whose owner ships the file FULL (F305). Depth 1 is INFERRED.
                return ("error", "path",
                        f"patch targets '{owner.name}'s copy of '{nested[1]}', but "
                        f"'{owner.name}' ships that file as a <diff> (a patch of a file it "
                        "does not own), and the engine never opens a nested patch of a "
                        f"<diff> (MEASURED 2026-10-19) -- patch '{nested[1]}' at its own "
                        "path instead; this patch can never apply")
    parts = vpath.split("/")
    if len(parts) > 1 and parts[0] == "extensions" and parts[1].lower().startswith("ego_dlc_"):
        dlc = parts[1]
        known = {d.name.lower() for d in config.dlc_dirs()}
        if dlc.lower() not in known:
            # Whether the DLC is INSTALLED is a fact about the game root, and it
            # has to be looked up — not assumed. This branch used to answer every
            # unknown `ego_dlc_*` with "which is installed but was never unpacked",
            # asserting an install it never checked. Both clauses can be false:
            # `dlc_dirs()` now covers all 8 real DLC (mini-DLC included, read
            # packed), so in a normally-configured run this is reachable ONLY when
            # the DLC is absent. Measured on an X Rebirth mod misfiled among X4
            # ones: 11 findings swearing `ego_dlc_2` and `ego_dlc_teladi_outpost`
            # were installed, when neither is even an X4 DLC.
            game_ext = _merge.GAME_ROOT / "extensions"
            if not game_ext.is_dir():
                return ("info", "unverifiable",
                        f"targets DLC '{dlc}', which is not in the reference tree, and the "
                        "game root is not configured — cannot tell an uninstalled DLC from "
                        "an un-unpacked one")
            if (game_ext / dlc).is_dir():
                return ("info", "unverifiable",
                        f"targets DLC '{dlc}', which is installed but is not readable from "
                        "reference/ — cannot verify this path exists (not a confirmed error)")
            return ("info", "inactive",
                    f"targets DLC '{dlc}', which is not installed — "
                    "the engine never loads this file (designed no-op, not an error)")
    return ("error", "path",
            f"no base game file for '{vpath}' (path mismatch? this patch can never apply)")


def _inert_bare_path_finding(vpath: str, merged: _merge.MergeResult) -> tuple[str, str, str]:
    """(severity, category, message) for a <diff> the engine never loads at all.

    A patch aimed at ANOTHER MOD's file must live at `extensions/<owner>/<rel>` inside
    your mod. At the BARE mirrored path it is inert: the engine only consults
    reference/ + DLC for that path, finds the file is not base-game content, and never
    opens the file. Nothing is logged, no op is rejected, and the mod loads clean — the
    quietest failure in X4 modding.

    Proven 2026-08-01 from a two-run debug.txt, on ONE identical rel path under
    `assets/units/size_s/macros/`, with three mods involved: the file's OWNER is
    logged; a second mod's NESTED patch (`extensions/<owner>/<rel>`) of it is logged
    AND has its op evaluated (`[=ERROR=] No matching node ... @makerrace`); a third
    mod's BARE-path patch of the same path is absent from both runs — even though
    that mod is loaded, its own base-game-path files being logged three times.
    Same path, same load, three mods, two logged and one not.

    Why this needs its own finding rather than falling out of the existing
    `_no_base_finding`: under Tier B an installed mod's full file satisfies `base_found`
    (`_merge.build_effective`'s `base_found or mode in {union, full}`), so `merged.tree`
    is non-None and the Tier A error is silently CURED by installing more mods. Measured
    on the live 101-mod install: Tier A reports these 7 files, Tier B reports 0 errors and
    exits 0 — a false OK, in the tier the convention tells you to use for cross-mod work.
    """
    # Only union/full contributors SUPPLY the file; a `diff` contributor is another
    # mod patching it, exactly as this mod is. Listing patchers as suppliers would
    # send the reader to move their file under a mod that does not own it — the
    # wrong-reason failure that stops the next person checking.
    suppliers = [s.rsplit(":", 1)[0] for s in merged.sources
                 if s.rsplit(":", 1)[-1] in {"union", "full", "owner"}]
    owner = suppliers[0] if suppliers else "<owner>"
    who = ", ".join(suppliers) if suppliers else "another mod, not the base game"
    return ("error", "path",
            f"'{vpath}' is not a base game file — it is supplied by {who}. "
            f"A patch over another MOD's file must live at "
            f"'extensions/{owner}/{vpath}' inside your mod; at this bare path "
            f"the engine never loads it (silent no-op, no log line, no rejected op)")


#: Ops in ONE file beyond which validation gets visibly slow. Applying a diff is
#: O(n^2) in ops-per-file — every op re-evaluates its selector against a tree the
#: previous ops just grew — so cost rises ~3.5x each time the count doubles
#: (measured 2026-08-08). That is inherent to "N ops x a full-tree evaluation",
#: not a defect to optimize away: an op cannot reuse a match the op before it
#: invalidated. The threshold is ~3x the largest file in a real ~120-mod install
#: (1,443 ops, about 0.03s), so normal content never trips it and nobody is left
#: staring at an apparent hang with no explanation.
_LARGE_OP_COUNT = 5000


def _warn_if_pathologically_large(diff_root, vpath: str, report: Report) -> None:
    n = sum(1 for op in diff_root if isinstance(op.tag, str))
    if n >= _LARGE_OP_COUNT:
        report.add("info", "path",
                   f"{n} ops in one file — validation is O(n^2) in ops-per-file, so "
                   f"this file alone may take a while (typical mods ship under 1,500)",
                   vpath)


def _sel_check_file(vpath: str, diff_root, config: _merge.Config, report: Report,
                    seen_skips: set[str]) -> bool:
    """Sel-resolution for ONE diff file. Returns True iff its ops were EVALUATED.

    The ONE implementation behind both the full run and `--file`. They used to be
    two, and the `--file` copy lacked the inactive-target and inert-bare-path
    verdicts: the per-edit hook called a nested patch for an uninstalled target an
    ERROR (the full run: INFO "inactive"), and let a bare-path patch over another
    MOD's file through clean (the full run: ERROR) -- AUDIT-2026-09-24 VA-2.
    """
    _warn_if_pathologically_large(diff_root, vpath, report)
    merged = _merge.build_effective(vpath, config)
    # An overlay we could not parse was left out of this tree, so every verdict
    # below is computed against an incomplete tree. That disables the check's
    # premise, not one file — the Skipped docstring's definition of degraded.
    for msg in merged.skipped:
        if msg not in seen_skips:
            seen_skips.add(msg)
            report.skip(f"sel-resolution against a complete tree ({vpath})",
                        f"an overlay could not be parsed, so the comparison tree is "
                        f"incomplete: {msg}", degraded=True)
    later_owner = None
    if merged.tree is None:
        nested = _merge._nested_target(vpath, config.packed_dlc_names())
        later_owner = _later_loading_owner(nested, config) if nested is not None else None
        if later_owner is not None:
            # The target is ACTIVE, ships the file, and loads AFTER this mod. MEASURED
            # 2026-10-12 (engine log + a save, kuertee_additional_agent_actions ->
            # kuertee_emergent_missions and two more): the engine APPLIES such a patch,
            # right after the owner's file and before later mods. So the tree this
            # patch meets is the owner's file plus the nested patches of mods loading
            # before THIS one -- exactly `_build_owned` over the patch-time overlays
            # with the owner added (as `_compat._owner_aware_config` does).
            merged = _merge.build_effective(vpath, _merge.replace(
                config, overlays=tuple(config.overlays) + (later_owner,)))
            for msg in merged.skipped:
                if msg not in seen_skips:
                    seen_skips.add(msg)
                    report.skip(f"sel-resolution against a complete tree ({vpath})",
                                f"an overlay could not be parsed, so the comparison tree "
                                f"is incomplete: {msg}", degraded=True)
    if merged.tree is None:
        report.add(*_no_base_finding(vpath, config), vpath)
        return False
    if later_owner is not None:
        report.add("info", "load-order",
                   f"patch targets extension '{later_owner.name}', which loads AFTER this "
                   "mod; the engine applies a nested patch to a later-loading mod right "
                   "after that mod's file (MEASURED 2026-10-12), so the ops were evaluated "
                   f"against '{later_owner.name}'s file plus earlier patchers", vpath)
    if (not merged.base_from_game
            and _merge._nested_target(vpath, config.packed_dlc_names()) is None):
        # Some mod supplies this file, but the GAME does not — so a bare-path diff
        # over it is inert. Deliberately no _check_ops: its verdict on a file the
        # engine never opens is noise, and "sel resolves fine" on a dead patch is
        # exactly the false reassurance this finding exists to kill.
        report.add(*_inert_bare_path_finding(vpath, merged), vpath)
        return False
    _check_ops(diff_root, merged.tree, vpath, report)
    return True


def _sel_note(checked: int, found: int, payload: int | str) -> str:
    """The denominator line. `checked` counts files whose ops were EVALUATED; a
    diff with no base, an inactive target or an inert bare path is FOUND but not
    checked (AUDIT-2026-09-24 VA-11a -- it used to be counted as checked)."""
    note = (f"sel-resolution: {checked} diff file(s) checked "
            f"across {payload} payload XML file(s)")
    if found != checked:
        note += (f"; {found - checked} more diff file(s) found whose ops were NOT "
                 "evaluated (no base / inactive target / inert path -- see "
                 "the findings)")
    return note


def check_nested_diff(vpath: str, root: etree._Element, report: Report) -> None:
    """ERROR for every `<diff>` element BELOW the root of a complete (non-diff) file.

    The engine treats a file as a patch only when `<diff>` IS the document root (the X4
    diff format; the audit row states the engine applies no nested one); one
    nested inside a complete file (`<wares>...<diff>...</diff></wares>` -- a patch
    pasted into the wrong kind of file) is never applied, and the run printed "OK: no
    issues found" (AUDIT-2026-09-24 VA-15). ERROR, not WARN: it is the same class as
    every other never-applied patch here (an op whose sel matches nothing) -- the
    author wrote edits that do not happen, with no engine message. MEASURED
    2026-09-26 before gating: 0 of 7,516 parsed XML files across 156 installed
    extension folders carry one, so this cannot flood.

    A `<diff>` ROOT is excluded: the name is the patch format itself there, and a
    `<diff>` nested inside a patch's payload is a different question.
    """
    if root.tag == "diff":
        return
    for el in root.iter("diff"):
        if el is root:
            continue
        report.add("error", "diff",
                   f"<diff> nested inside a complete <{root.tag}> file is never applied: "
                   "the engine reads a patch only when <diff> is the document root -- "
                   "move these ops into their own <diff> file at this path",
                   vpath, el.sourceline or 0)


def check_sel_resolution(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Flag any non-silent op whose sel= matches nothing in the merged base+DLC tree.

    Also flags a `<diff>` nested inside a complete file (`check_nested_diff`): it is
    a patch that never applies, so it belongs with the selector verdicts and runs in
    every mode that runs them (full, `--sel-only`, `--file`)."""
    checked = found = 0
    seen_skips: set[str] = set()
    payload = []  # one parse pass serves both the nested-diff check and the denominator
    for vpath, root in iter_mod_xml_roots(mod_dir):
        check_nested_diff(vpath, root, report)
        if vpath.lower() != MANIFEST:
            payload.append(vpath)
    for vpath, diff_root in iter_diff_files(mod_dir):
        found += 1
        checked += _sel_check_file(vpath, diff_root, config, report, seen_skips)

    # Always state the denominator. "OK: no issues found" over 14 files and over 1
    # file printed identically until 2026-08-01, for the tool's PRIMARY check.
    report.notes.append(_sel_note(checked, found, len(payload)))

    if found:
        return
    # No diff file at all. That has three causes with three different verdicts,
    # and until 2026-08-01 all three produced one warning asserting the third —
    # via report.add("warn", "skipped", ...), which LOOKS like the skip channel but
    # creates a Finding, so report.degraded stayed empty and the CLI exited 0.
    #
    # Measured over the 102 installed mods before this was written: 16 additive-only,
    # 1 asset-only (XTex: 0 XML but 4695 catalog members), 0 unreadable. Marking all
    # of them degraded would have been a second half-fix — 17 false exit-3s.
    if payload:
        report.notes.append(
            "sel-resolution: this mod is additive-only — it ships payload XML but no "
            "<diff>, so there are no selectors to resolve (not a problem)")
    elif _cat.is_packed(mod_dir):
        # Packed with no XML is normal for a texture/shader mod, but only if the
        # catalog actually OPENED. XTex is the live example: 0 XML members, 4695
        # total. An empty vfs means the .cat/.dat could not be read.
        if _cat.mod_vfs(mod_dir, xml_only=False, packed_only=True):  # packed-ok: is-this-mod-packed
            report.notes.append(
                "sel-resolution: this mod is packed and ships no XML (assets only) — "
                "there is nothing for the selector check to examine")
        else:
            report.skip("sel-resolution", "this mod is packed but not one catalog member "
                        "could be read — the .cat/.dat could not be opened", degraded=True)
    elif any(p.is_file() and p.name.lower() != MANIFEST for p in mod_dir.rglob("*")):
        report.notes.append(
            "sel-resolution: this mod ships loose assets but no XML — there is nothing "
            "for the selector check to examine")
    else:
        # Nothing but a manifest. Not an extension the engine can do anything with,
        # and reporting it clean is the same false pass this branch exists to end.
        report.skip("sel-resolution", "this folder contains nothing but content.xml — "
                    "no payload of any kind was found to check", degraded=True)


def _file_vpath(file_path: Path, mod_dir: Path) -> str | None:
    """*file_path*'s vpath inside *mod_dir*, or None if it is not inside it.

    Both sides are RESOLVED first: a relative mod_dir with an absolute --file (or
    the reverse) made `relative_to` raise, and the old fallback used the bare
    FILENAME as the vpath -- `wares.xml` instead of `libraries/wares.xml` -- so a
    clean patch read as "no base game file" (AUDIT-2026-09-24 VA-4)."""
    try:
        return file_path.resolve().relative_to(mod_dir.resolve()).as_posix()
    except ValueError:
        # silent-ok: None IS the channel -- the one caller records a DEGRADED skip
        # naming the file, rather than guessing a vpath.
        return None


def check_sel_resolution_one(file_path: Path, mod_dir: Path,
                             config: _merge.Config, report: Report) -> None:
    """Fast path: sel-resolution for ONE edited file (the auto-validate hook).

    Shares `_sel_check_file` with the full run, so both give one verdict for one
    file, and states its own denominator note (AUDIT-2026-09-24 VA-14: it printed
    "OK: no issues found" with no notes at all)."""
    try:
        root = _merge.parse_file(file_path)
    except etree.XMLSyntaxError as exc:
        report.add("error", "sel", f"unparseable XML: {exc}", str(file_path))
        return
    except OSError as exc:
        # Missing/unreadable file. lxml raises OSError here, not XMLSyntaxError,
        # so the narrower catch above let a plain typo escape as a traceback.
        report.add("error", "path", f"cannot read file: {exc}", str(file_path))
        return
    vpath = _file_vpath(file_path, mod_dir)
    if vpath is None:
        # Guessing a vpath (the bare filename) produced false verdicts; refusing to
        # guess is the honest answer. Degraded: the one check asked for did not run.
        report.skip(f"sel-resolution ({file_path})",
                    f"the file is not inside the mod folder '{mod_dir}', so its game "
                    "path (vpath) is unknown and its selectors were not resolved",
                    degraded=True)
        return
    check_nested_diff(vpath, root, report)
    if root.tag != "diff":
        # NOT a silent return. This function only knows how to check a <diff>, and
        # `validate()` RETURNS immediately after calling it in --file mode -- so for
        # a complete file nothing whatsoever was examined, and the run printed
        # "OK: no issues found" with rc 0, no notes and no NOT CHECKED. That is the
        # sentence the skipped channel exists to prevent, reached by the hook that
        # auto-validates whatever the user just edited.
        #
        # NOT degraded, on the precedent set for the default-mode script disclosure
        # (tests/test_script_files_disclose_unvalidated.py): a complete file will
        # never contain selectors, so an exit 3 here would be permanent and
        # unclearable, and a gate that can never go green trains you to ignore it.
        report.skip(f"sel-resolution ({vpath})",
                    f"<{root.tag}> is a complete file, not a <diff>, so it has no "
                    f"selectors to resolve -- and --file runs no other check")
        report.notes.append(_sel_note(0, 0, f"1 (--file: {vpath})"))
        return
    checked = _sel_check_file(vpath, root, config, report, set())
    report.notes.append(_sel_note(int(checked), 1, f"1 (--file: {vpath})"))


def _added_subtrees(diff_root: etree._Element):
    """Wrap each <add>'s children in a throwaway root so refs can be scanned."""
    for op in diff_root:
        if isinstance(op.tag, str) and op.tag == "add":
            holder = etree.Element("_added")
            for child in op:
                if isinstance(child.tag, str):
                    holder.append(child)  # move into holder; fine, diff_root is discarded
            yield holder


def _ref_severity(vpath: str, config: _merge.Config) -> tuple[str, str]:
    """(severity, suffix) for a dangling reference found in *vpath*.

    `error`, except for a CROSS-MOD reference under Tier A, which is structurally
    unanswerable there rather than wrong. `Config.overlays` empty is Tier A --
    "base+DLC only, which cannot see content that another mod adds, removes or
    overrides", in that field's own words -- and a file at
    `extensions/<target>/...` is a nested patch whose payload names `<target>`'s
    content. CLAUDE.md already records that class as EXPECTED under Tier A for
    SELECTORS; this is the same rule for REFERENCES.

    MEASURED 2026-09-06, Tier A, 125 installed extensions: of 16 full-file ref
    errors, `cpsdo_vro`'s `bullet_cpsdo_turret_m_ion_01_mk4` and
    `bullet_cpsdo_l_ion_01_mk1` ARE defined -- in `cpsdo_zb_modpack`, the mod
    cpsdo_vro patches. Both findings sit at `extensions/cpsdo_zb_modpack/...`.

    Under Tier B this returns `error` -- those overlays ARE merged, so a reference
    that still dangles there really does dangle -- EXCEPT inside an INACTIVE
    nested patch (target not installed, or installed but disabled). The same run
    already calls that file a designed no-op the engine never loads, so a
    reference inside it cannot be a gating defect (AUDIT-2026-09-24 VA-5). It is
    still reported, as INFO naming why.

    A patch on a PACKED-only DLC gates as `error` in both tiers: `dlc_dirs()`
    merges packed DLC too, so Tier A CAN answer it (AUDIT-2026-09-24 VA-7 -- it
    was demoted as "cross-mod").
    """
    if config.overlays:
        inactive = _inactive_target_reason(vpath, config)
        if inactive is not None:
            return "info", (f" [inactive patch: {inactive}, so the engine never "
                            "loads this file -- not a defect of this mod]")
        return "error", ""
    # `_merge._nested_target` is the ONE implementation of this question, and it
    # already draws the line in the right place: a DLC target returns None, because
    # Tier A merges base AND DLC and so CAN resolve those. A first draft hand-rolled
    # `parts[0] == "extensions" and not parts[1].startswith("ego_dlc_")` and wrongly
    # demoted `ebi_timelines_faction_use_ship`'s `ship_spl_xl_ark_01_c` -- which is
    # absent from Tier A's 4,721-name definition set, whole variant family included.
    # `_no_base_finding` asks the same question through the same helper.
    owned = config.packed_dlc_names()
    target = _merge._nested_target(vpath, owned)
    if target is None or target[0].lower() in owned:
        return "error", ""
    return "info", (" [cross-mod: this file patches another MOD, which Tier A does "
                    "not merge -- re-check with `--tier b`]")


def check_references(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Flag references the mod *introduces* that resolve to no definition.

    Both scopes gate as errors. ``<add>`` ops in ``<diff>`` files have done so
    since v1; **full files** were checked not at all until 2026-08-13 -- MEASURED,
    1,625 files across 56 mods, 38% of their XML -- shipped as INFO first so a
    newly-visible check could not break a build before its hit list had ever been
    read, and PROMOTED once that run earned it: 114 mods, 14 findings, every one
    confirmed genuine, zero false positives. `test_full_file_findings_gate_as_ERROR`
    and `test_diff_add_findings_stay_ERROR` in tests/test_reference_scope.py pin
    both halves.

    !! This paragraph said "reported as INFO for now ... the plan is to promote
    after one clean corpus run" until 2026-09-06, while the code gated from the
    same commit that wrote it (`fe09e99`) -- so a reader was told the promotion had
    not happened when it had, and that the flood risk was still contained. The
    docstring was the stale half, not the code; the promotion above is what
    actually occurred.

    CROSS-MOD REFERENCES ARE THE ONE EXCEPTION, and they are why the promotion's
    "zero false positives" no longer holds by itself. Under Tier A `Config.overlays`
    is empty -- "base+DLC only, which cannot see content that another mod adds,
    removes or overrides", in that field's own words -- so a reference living in a
    nested patch at `extensions/<target>/...` names content Tier A structurally
    cannot resolve. CLAUDE.md already records that class as EXPECTED under Tier A
    for SELECTORS ("no base game file" / "sel matched nothing"); this is the same
    rule for REFERENCES.

    MEASURED 2026-09-06 over 125 installed extensions, Tier A: 16 full-file ref
    errors across six mods, of which `cpsdo_vro`'s two --
    `bullet_cpsdo_turret_m_ion_01_mk4` and `bullet_cpsdo_l_ion_01_mk1` -- ARE
    defined, in `cpsdo_zb_modpack`, the very mod cpsdo_vro patches. The 2026-08-13
    verification could not have seen them; the modlist grew that pair afterwards.
    So the premise decayed rather than the decision being wrong, and the fix is to
    scope the gate rather than to lower it.

    Still REPORTED, never hidden: severity decides the exit code, not visibility,
    and the demoted message names `--tier b` as the mode that CAN answer.
    """
    mod_overlay = [mod_dir]
    wares_merged = _merge.build_effective(WARES_FILE, config, extra_overlays=mod_overlay)
    note_dropped_overlays(wares_merged, "ware-reference checks", report)
    ware_def_set = _refs.ware_defs(wares_merged.tree)
    # Free ride on the tree above: obtainability is a JOIN, not a scan. Only the
    # deprecated half ships -- MEASURED corpus-wide, 'no ware' is 71% of indexed
    # macros and is the NORMAL state (bullet 170/170, scenery 89.5%), so checking
    # it would fire on 3,945 correctly-by-design macros. See the _refs docstring.
    dead_macros = _refs.deprecated_only_macros(wares_merged.tree)
    text_def_set = collect_text_defs(config, mod_overlay, report)
    macro_def_set = collect_macro_defs(config, mod_overlay, report)
    entity_defs = EntityDefs(config, mod_overlay, report)
    expressions: list[str] = []

    # ONE pass, not two. `iter_diff_files` is exactly `root.tag == "diff"`, so
    # this branch is the same partition without parsing every file a second time.
    # NB the 12.3s -> 26.3s measured on VRO is NOT this — it is the lazy corpus
    # scan below firing, which collapsing the passes did not move at all. Recorded
    # because the double-parse was the obvious suspect and measuring cleared it.
    diff_files = full_files = 0
    unobtainable = 0
    # Once per FILE, not per dangling ref: the inactive-target question scans the
    # extension set and reads the profile.
    _sev_cache: dict[str, tuple[str, str]] = {}

    def _sev_for(vpath: str) -> tuple[str, str]:
        if vpath not in _sev_cache:
            _sev_cache[vpath] = _ref_severity(vpath, config)
        return _sev_cache[vpath]

    for vpath, root in iter_mod_xml_roots(mod_dir):
        if vpath.lower() == MANIFEST:
            continue
        # INFO, never a gate: an author may reference deprecated content on purpose,
        # and this DOES resolve -- it is reachable in the tree and simply not
        # purchasable. Reported so "resolves" stops being read as "usable".
        for u in _refs.unobtainable_refs(root, dead_macros):
            unobtainable += 1
            report.add("info", "unobtainable",
                       f"{u.ref} resolves, but no LIVE ware supplies it "
                       f"(only deprecated: {', '.join(u.wares)}) - it cannot be "
                       f"bought or equipped in game", vpath, u.line)
        if root.tag == "diff":
            diff_files += 1
            for holder in _added_subtrees(root):
                # entity_defs, NOT macro_def_set: `<component ref>` is namespace-dependent
                # (gotcha #11) and checking it against the macro index alone cost 23 FALSE
                # gating errors on the live modlist — `standardzone` and `standardregion`
                # are components, defined in libraries/component.xml and registered in
                # index/components.xml. One question, one oracle, both scopes.
                for d in _refs.find_dangling(holder, ware_def_set, text_def_set, entity_defs,
                                             where=vpath, expressions=expressions):
                    sev, why = _sev_for(vpath)
                    report.add(sev, "ref",
                               f"introduced {d.kind} reference does not resolve: {d.ref}{why}",
                               vpath, d.line)
        else:
            full_files += 1
            for d in _refs.find_dangling(root, ware_def_set, text_def_set, entity_defs,
                                         where=vpath, expressions=expressions):
                sev, why = _sev_for(vpath)
                report.add(sev, "ref",
                           f"{d.kind} reference does not resolve: {d.ref}{why}",
                           vpath, d.line)

    report.notes.append(
        f"reference defs: {len(ware_def_set)} wares, {len(text_def_set)} text strings, "
        + (f"{len(macro_def_set)} indexed macros" if macro_def_set is not None
           else "macros NOT CHECKED (index unreadable)")
        + f", {len(entity_defs)} verified macro/component names in examined documents"
        + (" (+ corpus scan)" if entity_defs.corpus_scanned else "")
        + f"; scanned {diff_files} diff + {full_files} full file(s)"
        + (f"; skipped {len(expressions)} script expression(s) in @ware" if expressions else "")
        + f"; {len(dead_macros)} macro(s) have only deprecated suppliers"
        + f", {unobtainable} reference(s) to them here")


def check_file_existence(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """For each <component ref="X_macro"> the mod introduces, verify the whole
    chain resolves to real files: macro registered -> macro file -> its component
    -> component file."""
    mod_overlay = [mod_dir]
    macro_index = _resolve.build_index(config, mod_overlay, _resolve.MACRO_INDEX, report)
    component_index = _resolve.build_index(config, mod_overlay, _resolve.COMPONENT_INDEX, report)
    seen: set[str] = set()
    for vpath, diff_root in iter_diff_files(mod_dir):
        for holder in _added_subtrees(diff_root):
            for comp in holder.xpath("//component[@ref]"):
                macro = comp.get("ref")
                if macro in seen:
                    continue
                seen.add(macro)
                for msg in _resolve.macro_component_links(macro, macro_index, component_index):
                    report.add("error", "file", msg, vpath, comp.sourceline or 0)


def check_page_collisions(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Warn when the mod's {page,t} pairs already exist earlier in the tree (silent clobber).

    ⚠ THE OP TREE, NOT `_added_subtrees`. That helper moves an `<add>`'s CHILDREN
    into a throwaway root, which DISCARDS the selector -- and for the into-page form
    the selector is the only place the page id appears at all
    (`<add sel="/language[@id='44']/page[@id='20005']"><t id="1">`). It also matched
    `op.tag == "add"` and nothing else. So the two forms that can actually clobber a
    base string -- an into-page `<add>`, and any `<replace>` -- were the two forms
    this warning could not see.

    `_refs.text_defs` learned both in 4a76af3, which fixed the REFERENCE half; that
    commit's own docstring recorded that "the collision half failed the other way: a
    mod clobbering base string {1001,1} through the into-page form printed 'OK: no
    issues found'", and it stayed true for another day. Handing it the whole diff
    root is the fix: the `//page[@id]` walk still finds every page-element form, and
    the selector branch now runs because the selectors still exist.

    FULL t-files count too (AUDIT-2026-09-24 VA-9). t-files are merged additively,
    so a complete `<language>` file redefining a base `{page,t}` clobbers it exactly
    as the `<diff>` form does -- but only `<diff>` files were walked, so the same
    string was warned in one form and silent in the other. A complete t-file is
    reported ONCE per file with a count: INFO when it overrides base/DLC strings
    (the normal rename idiom), WARN when it redefines another MOD's strings.

    The warning NAMES the definer (AUDIT-2026-09-24 VA-11f): under Tier B the
    earlier tree includes other mods, and "already defined in base/DLC" was printed
    for a string only another MOD defines.

    The other two `_added_subtrees` callers are deliberately left alone -- they ask
    "what content does this mod INTRODUCE", where an `<add>` payload is the right
    population and a `<replace>` payload is a separate question with its own corpus
    cost. This call site asks "what does it OVERWRITE", which is the opposite one.
    """
    base_cfg = replace(config, overlays=(), final_overlays=())
    base_defs = collect_text_defs(base_cfg, report=report)  # base + DLC only (NOT the mod)
    mod_defs = [(ov.name, _source_text_defs(ov, report)) for ov in config.overlays]

    def _definer(key: tuple[str, str]) -> str | None:
        if key in base_defs:
            return "base/DLC"
        names = [name for name, defs in mod_defs if key in defs]
        return f"mod {', '.join(repr(n) for n in names)}" if names else None

    for vpath, root in iter_mod_xml_roots(mod_dir):
        if root.tag != "diff" and not T_FILE_RE.search(vpath):
            continue  # a complete NON-t-file defines no strings
        added: set[tuple[str, str]] = _refs.text_defs(root)
        if root.tag == "diff":
            for page, t in sorted(added):
                who = _definer((page, t))
                if who is None:
                    continue
                report.add("warn", "text",
                           f"text {{{page},{t}}} already defined in {who} — your add "
                           "clobbers it", vpath)
            continue
        # A COMPLETE t-file overriding strings is the normal rename idiom, not an
        # accident: VRO's ships 304 {page,t} per language file, so one WARN per
        # string took a real run from 3 WARNs to 307. ONE finding per file, with
        # the count: INFO for base/DLC strings (the idiom), WARN only for strings
        # another MOD defines -- two mods fighting over one string is the case
        # someone has to decide.
        over_base = sorted(k for k in added if k in base_defs)
        over_mods: dict[str, list[tuple[str, str]]] = {}
        for k in sorted(added - set(over_base)):
            who = _definer(k)
            if who is not None:
                over_mods.setdefault(who, []).append(k)

        def _ids(keys: list[tuple[str, str]]) -> str:
            head = ", ".join(f"{{{p},{t}}}" for p, t in keys[:5])
            return head + (f", ... (+{len(keys) - 5} more)" if len(keys) > 5 else "")

        if over_base:
            report.add("info", "text",
                       f"complete t-file overrides {len(over_base)} base/DLC string(s) "
                       f"({_ids(over_base)}) — the usual rename idiom; check it is "
                       "intended", vpath)
        for who, keys in over_mods.items():
            report.add("warn", "text",
                       f"complete t-file redefines {len(keys)} string(s) already defined "
                       f"in {who} ({_ids(keys)}) — whichever loads later wins", vpath)


def check_text_sanity(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Warn on literal newlines inside text DB entries.

    X4 imports multiline <t> values but logs TextDB converter errors for them.
    Treat this as cleanup-worthy log noise rather than a gating loader failure.
    """
    for vpath, root in iter_mod_xml_roots(mod_dir):
        if not vpath.lower().startswith("t/"):
            continue
        for page in root.xpath("//page[@id]"):
            pid = page.get("id")
            for t in page.xpath(".//t[@id]"):
                text = "".join(t.itertext())
                if "\n" in text or "\r" in text:
                    report.add("warn", "text",
                               f"text {{{pid},{t.get('id')}}} contains a literal newline; "
                               "X4 logs this during TextDB import", vpath, t.sourceline or 0)


def _race_defs(config: _merge.Config, extra_overlays=None, report=None) -> set[str]:
    races = _merge.build_effective(RACES_FILE, config, extra_overlays=list(extra_overlays or []))
    note_dropped_overlays(races, "race definitions", report)
    if races.tree is None:
        return set()
    return set(races.tree.xpath("//race/@id"))


def check_identity_values(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Gate on identification makerrace values that are not real races.

    Factions such as 'loanshark' are valid in owner/job contexts but invalid
    as makerrace values; the engine reports these as race-list import errors.
    """
    races = _race_defs(config, [mod_dir], report)
    if not races:
        # A check that could not run is a SKIP, not a finding (AUDIT-2026-09-24
        # VA-11e: it was a WARN, which reads as a verdict about the mod). Degraded
        # only when the mod has a makerrace to check -- otherwise nothing was lost,
        # and a permanent exit 3 on every mod without one would train you to
        # ignore exit 3.
        subjects = sum(len(root.xpath("//identification[@makerrace]"))
                       for _, root in iter_mod_xml_roots(mod_dir))
        report.skip("race (makerrace) identity checks",
                    f"could not resolve race definitions from {RACES_FILE}; "
                    f"{subjects} makerrace value(s) in this mod were not verified",
                    degraded=bool(subjects))
        return
    for vpath, root in iter_mod_xml_roots(mod_dir):
        for ident in root.xpath("//identification[@makerrace]"):
            race = ident.get("makerrace")
            if race and race not in races:
                report.add("error", "identity",
                           f"identification makerrace='{race}' is not a valid race id",
                           vpath, ident.sourceline or 0)


def _tag_set(value: str | None) -> frozenset[str]:
    return frozenset((value or "").split())


def check_engine_connection_tags(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Gate on non-identical engine compatibility tags within one component."""
    for vpath, root in iter_mod_xml_roots(mod_dir):
        if not vpath.lower().startswith("assets/"):
            continue
        for comp in root.xpath("//component[@name]"):
            engines: list[tuple[str, frozenset[str], int]] = []
            for conn in comp.xpath(".//connection[@name][@tags]"):
                tags = _tag_set(conn.get("tags"))
                if "engine" in tags:
                    engines.append((conn.get("name"), tags, conn.sourceline or 0))
            if len(engines) < 2:
                continue
            by_tags: dict[frozenset[str], list[str]] = {}
            for name, tags, _line in engines:
                by_tags.setdefault(tags, []).append(name)
            if len(by_tags) <= 1:
                continue
            detail = "; ".join(
                f"{','.join(names)}=[{' '.join(sorted(tags))}]"
                for tags, names in sorted(by_tags.items(), key=lambda item: sorted(item[1])[0])
            )
            report.add("error", "connection",
                       f"component '{comp.get('name')}' has non-identical engine connection tags: {detail}",
                       vpath, engines[0][2])


def check_connections(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Verify every <loadout> `path` resolves to a <connection> on the ship's component.

    Catches the engine/weapon/shield re-attach failure mode (loadout points at a
    connection the mesh doesn't have). Skips silently when the component can't be
    resolved (avoids false positives)."""
    mod_overlay = [mod_dir]
    macro_index = _resolve.build_index(config, mod_overlay, _resolve.MACRO_INDEX, report)
    component_index = _resolve.build_index(config, mod_overlay, _resolve.COMPONENT_INDEX, report)
    conn_cache: dict[str, set[str] | None] = {}

    def conns_for_component(comp_ref: str | None) -> set[str] | None:
        if not comp_ref:
            return None
        if comp_ref not in conn_cache:
            cf = _resolve.read_indexed(component_index, comp_ref)
            conns = _resolve.connections_of(cf.data) if cf else None
            if cf and conns is None:
                report.skip("connection checks",
                            f"component '{comp_ref}' ({cf.display.name}): unreadable, its loadout "
                            "targets were not verified")
            conn_cache[comp_ref] = conns
        return conn_cache[comp_ref]

    def component_of_macro(macro_name: str) -> str | None:
        mf = _resolve.read_indexed(macro_index, macro_name)
        if mf is None:
            return None
        try:
            mr = _merge.parse_bytes(mf.data)
        except etree.XMLSyntaxError as exc:
            report.skip("connection checks",
                        f"macro '{macro_name}' ({mf.display.name}): will not parse ({exc}), "
                        "its loadout targets were not verified")
            return None
        refs = mr.xpath(f"//macro[@name={_resolve._xq(macro_name)}]/component/@ref")
        return refs[0] if refs else None

    def check_loadout(lo, comp_ref, vpath, label):
        conns = conns_for_component(comp_ref)
        if conns is None:
            return
        for conn_name, line in _resolve.loadout_targets(lo):
            if conn_name not in conns:
                report.add("error", "connection",
                           f"loadout {label} references connection '{conn_name}' "
                           f"not on component '{comp_ref}'", vpath, line)

    for vpath, root in iter_mod_xml_roots(mod_dir):
        # Inline loadouts: <macro ...><loadouts>... (component ref in the same macro)
        for macro in root.xpath("//macro[.//loadout]"):
            crefs = macro.xpath("./component/@ref")
            comp_ref = crefs[0] if crefs else None
            for lo in macro.xpath(".//loadout"):
                check_loadout(lo, comp_ref, vpath, f"in {macro.get('name')}")
        # Centralized loadouts.xml: <loadout macro="M">
        for lo in root.xpath("//loadout[@macro]"):
            check_loadout(lo, component_of_macro(lo.get("macro")), vpath, f"for {lo.get('macro')}")


def check_module_groups(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Verify every `<module group="X">` names a group defined in libraries/modulegroups.xml.

    The engine rejects a dangling one at station-generation time with
    ``FactoryGenerator::GetAllPossibleMacros(): Station group reference 'X' not found or
    does not contain any macros`` — the module contributes no macros and the station is
    built without it. x4validate was structurally blind to this until 2026-08-21 because
    `modulegroups` was not an indexed registry; the engine found it and we did not.

    MEASURED 2026-08-21 over the effective tree (base + 8 DLC incl. both mini-DLC + every
    installed mod): 192 `<module>` entries, 146 groups, every entry carries `@group`, and
    0 dangle once the one real offender is repaired. So this gates as an error on day one
    without flooding — unlike `check_references`' full-file half, whose hit list had never
    been reviewed in the wild.
    """
    merged = _merge.build_effective("libraries/modulegroups.xml", config,
                                    extra_overlays=[mod_dir])
    # A dropped overlay is DEGRADED through the one shared helper (AUDIT-2026-09-24
    # VA-10; it was a plain skip, so the incomplete tree exited 0/1, not 3).
    note_dropped_overlays(merged, "module group checks", report)
    subjects = [(vpath, el) for vpath, root in iter_mod_xml_roots(mod_dir)
                if vpath.lower() != MANIFEST
                for el in root.xpath("//module[@group]")]
    # The whole check OFF is a DEGRADED skip when the mod has a `<module group=>`
    # to verify (AUDIT-2026-09-24 VA-13, technical default). Without one nothing
    # was lost, so it stays a disclosure rather than a permanent exit 3.
    why_off = None
    if merged.tree is None:
        why_off = "libraries/modulegroups.xml did not merge"
    else:
        defined = set(merged.tree.xpath("//group/@name"))
        if not defined:
            # An empty definition set would mark every reference dangling. That is
            # a non-answer, not a finding — say so instead of a wall of errors.
            why_off = "libraries/modulegroups.xml merged but defines no <group name=>"
    if why_off is not None:
        report.skip("module group checks",
                    f"{why_off}; {len(subjects)} <module group=> target(s) in this mod "
                    "were not verified", degraded=bool(subjects))
        return

    checked = 0
    for vpath, el in subjects:
        checked += 1
        group = el.get("group")
        if group not in defined:
            report.add("error", "ref",
                       f"module '{el.get('id')}' references station module group "
                       f"'{group}', which no libraries/modulegroups.xml defines",
                       vpath, el.sourceline)
    if checked:
        report.notes.append(
            f"module groups: {checked} <module group=> reference(s) checked against "
            f"{len(defined)} defined group(s)")


def _sibling_pool(vdir: str, config: _merge.Config, base_dirs: dict[str, set[str]],
                  installed: dict[str, Path]) -> tuple[set[str], str] | None:
    """Filenames the OWNER of *vdir* ships there, and a label for it.

    "Sibling" means "another variant the SAME owner ships", so the owner has to be
    resolved before the directory is read:

      assets/units/...                      -> base + DLC
      extensions/ego_dlc_x/assets/...       -> that DLC (packed ones included)
      extensions/<other mod>/assets/...     -> that MOD's own copy of the tree

    ``None`` means the owner could not be resolved -- reported through
    ``Report.skip``, never as a silent ``continue``.
    """
    parts = vdir.split("/")
    if len(parts) >= 3 and parts[0] == "extensions":
        owner = parts[1].lower()
        root = installed.get(owner)
        if root is not None and owner not in config.packed_dlc_names():
            rel = "/".join(parts[2:])
            names = {low.rpartition("/")[2]
                     for low in _compat._mod_xml_paths(root)
                     if low.rpartition("/")[0] == rel}
            return names, f"mod '{parts[1]}'"
        # Not an installed mod -> an `extensions/ego_dlc_*/` path. `base_dirs`
        # already holds those under exactly this prefix (both the six unpacked DLC
        # and, since F34, the two packed mini-DLC), so FALL THROUGH rather than
        # bailing. Bailing here is not hypothetical: the first cut of this
        # function did, and it took VRO's 3 real findings -- ship_ter_l_flagship_01,
        # ship_ter_m_corvette_02, ship_ter_s_fighter_04, all under
        # extensions/ego_dlc_timelines/ -- straight back to zero.
    pool = base_dirs.get(vdir)
    return (pool, "base+DLC") if pool is not None else None


def check_variant_consistency(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Warn when the mod patches one ship variant macro but not its base siblings
    (per-variant props like hull/cargo/loadout won't change for the untouched ones).

    THREE enumerations, and until 2026-08-22 all three were wrong in the same
    direction -- each looked only at loose files on disk.

    1. THE MOD UNDER TEST was walked with ``mod_dir.rglob("*.xml")``, so a PACKED
       mod contributed nothing. MEASURED across 115 installed mods: of 378 variant
       macro files, **14 were reachable and 364 (96.3%) were invisible** -- VRO,
       both ship_variation_expansion mods, all four lc4hunter packs. This is the
       identical defect `iter_diff_files` was repaired for on 2026-07-26, in the
       function next door, never carried across. `_scan`/`_compat` have provided
       the packed+loose enumeration ever since.
    2. THE BASE TREE was read as `config.reference / vdir`, which cannot see the
       two mini-DLC (never unpacked; F34). 2 files.
    3. A CROSS-MOD path (`extensions/<other mod>/...`) has its siblings inside
       THAT mod, not in `reference/` at all, so the check silently did nothing. 2
       files, both `cpsdo_faction`.

    Fixing only (2) and (3) would have changed nothing observable, because those 4
    files sit inside the 364 that were never reached. Measured before landing:
    378 files now examined, **3 warnings, all in one mod** -- so this gates rather
    than merely informing.
    """
    own = _compat._mod_xml_paths(mod_dir)
    variants = sorted((low, real) for low, real in own.items()
                      if VARIANT_RE.match(low.rpartition("/")[2]))
    if not variants:
        return

    base_dirs: dict[str, set[str]] = {}
    for low in _effective.base_vpaths(config, "*_macro.xml"):
        d, _, n = low.rpartition("/")
        base_dirs.setdefault(d, set()).add(n)

    # Resolved ONCE: `scan_installed()` inside the per-file loop made this O(n*m)
    # and cost 33 s across the installed set for a lookup that never changes.
    # ACTIVE: a "sibling variant" is one the engine will actually load alongside.
    active = _registry.mods("active")
    # A sibling that only a mod the engine leaves out ships is not in the pool --
    # correct, and NAMED (F139), or "no siblings" would read as a fact about the ship.
    note_not_loaded(active, "variant sibling check: a mod the engine does not load",
                    report)
    installed = {m["folder"].lower(): Path(m["path"])
                 for m in active if Path(m["path"]).is_dir()}

    unresolved: list[str] = []
    for low, real in variants:
        name = low.rpartition("/")[2]
        m = VARIANT_RE.match(name)
        vdir = low.rpartition("/")[0]
        resolved = _sibling_pool(vdir, config, base_dirs, installed)
        if resolved is None:
            unresolved.append(real)
            continue
        pool, _origin = resolved
        pat = re.compile(re.escape(m.group("base")) + r"_[a-z0-9]_macro\.xml$")
        siblings = sorted(s for s in pool if pat.match(s))
        if len(siblings) < 2:
            continue
        untouched = [s for s in siblings
                     if s != name and (f"{vdir}/{s}" if vdir else s) not in own]
        if untouched:
            report.add("warn", "variant",
                       f"patched '{real.rpartition('/')[2]}' but not sibling variant(s) "
                       f"{', '.join(untouched)} — per-variant props "
                       "(hull/cargo/loadout) won't change for them", real)

    if unresolved:
        # MEASURED 2026-08-22: 46 of 378 across the installed set. Not silence --
        # "we could not look" must never render as "we looked and it was fine".
        report.skip("variant sibling check",
                    f"{len(unresolved)} variant macro(s) sit in a directory whose owner "
                    f"supplies no macro files (e.g. {unresolved[0]}) — no sibling set to "
                    f"compare against, so those were NOT checked")


def check_completeness(
    mod_dir: Path, config: _merge.Config, report: Report, entity: str, like: str
) -> None:
    """entity/like are 'ware:<id>' specs (v1 supports the ware type)."""
    etype, _, eid = entity.partition(":")
    ltype, _, lid = like.partition(":")
    # ware / ship / module are all <ware> entries; the analogue defines the footprint.
    entity_types = {"ware", "ship", "module"}
    if etype not in entity_types or ltype not in entity_types:
        report.add("info", "completeness",
                   f"completeness supports {sorted(entity_types)}; got '{etype}'/'{ltype}'")
        return
    mod_overlay = [mod_dir]
    wares = _merge.build_effective(WARES_FILE, config, extra_overlays=mod_overlay)
    note_dropped_overlays(wares, "completeness", report)
    text_def_set = collect_text_defs(config, mod_overlay, report)
    macro_def_set = collect_macro_defs(config, mod_overlay, report)
    # EntityDefs, NOT macro_def_set -- the same correction check_references already
    # carries, and for the same reason. `_refs` answers the `<component ref>` question
    # with whatever set it is handed, and the macro INDEX alone is the wrong oracle:
    # a macro defined in an asset file and never registered in index/macros.xml is
    # legal and common (EntityDefs records 753 such names corpus-wide). This function
    # exists for `--entity/--like`, i.e. for a mod UNDER DEVELOPMENT, which is exactly
    # where an unregistered macro lives.
    #
    # REPRODUCED 2026-09-02 on a synthetic mod: check_references reported the ref as
    # resolving while check_completeness gated with "missing 'component'" -- the two
    # checks contradicting each other about the same attribute. Adding the one index
    # entry, the only variable, turned exit 1 into exit 0. Over shipped content the
    # incidence is 0 of 1,614 component refs (Tier B, 125 active mods), so this is a
    # live bug for mod authors rather than a wrong answer about the base game.
    #
    # None is PRESERVED, not replaced by an empty set: `collect_macro_defs` returns
    # None only when the index could not be built, and _refs treats that as "check
    # presence alone". EntityDefs has no such state, so the degraded case must keep
    # answering None or an unreadable index would start reporting real refs as missing.
    completeness_defs = (None if macro_def_set is None
                         else EntityDefs(config, mod_overlay, report))
    rep = _refs.ware_completeness(eid, lid, wares.tree, text_def_set, completeness_defs)
    report.notes.append(f"completeness checked kinds: {', '.join(rep.checked)}")

    # The catalog compares <ware>-WRAPPER fields only (see _refs._entity_kinds).
    # For a ship or module that is a fraction of the real footprint: nothing here
    # opens the macro, so a scaffold with no <physics>, no engine/shield/turret
    # slots and no connections reports "0 missing" -- a false clean, which is the
    # exact defect class the skip channel exists to make visible. Declared, not
    # degraded: the check DID answer its 8 questions, and the interior was never
    # in scope. Marking it degraded would exit 3 on every ship scaffold and train
    # the reader to ignore the signal.
    if etype in {"ship", "module"}:
        report.skip(
            f"completeness: {etype} macro interior",
            "compares <ware>-wrapper fields only (definition, name/description "
            "strings, price, production, component ref, owner, restriction). NOT "
            "checked: physics, connections/hardpoints, engine/shield/turret slots, "
            "storage, hull, software, steering curves. A 'complete' result here "
            "does NOT mean the macro is complete",
        )

    # A nonexistent --like analogue makes every comparison vacuous: its footprint
    # is all-False, so `missing` is empty and the old code cheerfully reported
    # "matches the footprint". Bail before that, and say which id was not found.
    if rep.analogue_missing:
        report.add("error", "completeness",
                   f"analogue '{lid}' does not exist in the effective wares tree — "
                   "nothing to compare against, so this check did not run "
                   "(check the --like id, or whether the mod defining it is installed)")
        report.skip("completeness", f"--like analogue '{lid}' not found", degraded=True)
        return
    if rep.entity_missing:
        report.add("error", "completeness",
                   f"entity '{eid}' does not exist in the effective wares tree — "
                   "the mod does not define it (check the --entity id)")
        return
    if not rep.missing:
        scope = ("the <ware>-wrapper footprint" if etype in {"ship", "module"}
                 else "the footprint")
        tail = (" (macro interior NOT checked — see skipped)"
                if etype in {"ship", "module"} else "")
        report.add("info", "completeness",
                   f"'{eid}' matches {scope} of '{lid}'{tail}")
    for kind in rep.missing:
        report.add("error", "completeness",
                   f"'{eid}' is missing '{kind}' that analogue '{lid}' has")


def _relpath(abs_file: str, mod_dir: Path) -> str:
    try:
        return Path(abs_file).relative_to(mod_dir).as_posix()
    except ValueError:
        return abs_file


def check_migration(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Runtime-only 9.0 breakages (dead APIs / deprecated Lua) the XSD can't catch."""
    unreadable: list[str] = []
    for m in _migration.scan_mod(mod_dir, unreadable):
        # Say when the hit is inside the catalog: the user cannot open that file
        # in an editor, and the fix is a repack, not a text edit.
        where = "  (inside the mod's catalog)" if m.packed else ""
        report.add("warn", "migration", f"{m.note}  [{m.snippet}]{where}",
                   _relpath(m.file, mod_dir), m.line)
    for u in unreadable:
        report.skip("9.0 migration scan", f"{u} — not scanned for dead APIs")


def check_exprlint(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Heuristic lint of the script-expression grammar inside attribute VALUES —
    the layer XSD is blind to (it validates structure, treats values as opaque
    strings). ADVISORY only (warn/info, never gating): a regex heuristic flags for
    review; the authoritative gate is the --debug debug.txt correlation."""
    for f in _exprlint.scan_mod(mod_dir):
        report.add(f.severity, "exprlint", f"{f.note}  [{f.snippet}]", f.vpath, f.line)


def _script_name_map(mod_dir: Path) -> dict[str, str]:
    """name -> vpath for the mod's own mdscript/aiscript files (their root
    `name=` attribute). Resolves debug.txt runtime errors (which name a SCRIPT,
    not a file) back to a file."""
    out: dict[str, str] = {}
    for vpath, root in _xref._iter_mod_files(mod_dir):
        if isinstance(root.tag, str) and root.tag in ("mdscript", "aiscript"):
            name = root.get("name")
            if name:
                out[name] = vpath
    return out


def _mod_ids(mod_dir: Path) -> set[str]:
    """Identity tokens this mod owns in an extensions\\<folder> path: its dev
    folder name plus its content.xml id (they can differ)."""
    # `.resolve()`: `Path(".").name` is "" (AUDIT-2026-09-24 VA-11c), so a mod
    # validated from inside its own folder lost its folder identity and no engine
    # line under `extensions\<folder>\` ever matched. `_mod_identity` already
    # resolves; this is the same rule.
    ids = {mod_dir.resolve().name.lower()}
    cx = mod_dir / "content.xml"
    if cx.is_file():
        try:
            mid = _merge.parse_file(cx).get("id")
            if mid:
                ids.add(mid.lower())
        except (etree.XMLSyntaxError, OSError):
            # silent-ok: the folder name (already in `ids`) is the identity that
            # matters for an extensions/<folder> path; the content.xml id is a
            # bonus alias. _mod_identity reports the same unreadable file.
            pass
    return ids


def check_debug_correlation(mod_dir: Path, config: _merge.Config, report: Report,
                            debug_path: str | Path) -> None:
    """Fold the ENGINE's own [=ERROR=] lines for THIS mod into the report — the
    authoritative layer static checks can't provide. Engine errors GATE (exit 1);
    engine warnings advise. Staleness caveat: pass a debug.txt captured AFTER the
    latest edits — a gate on a stale log is a false failure."""
    parsed = _debuglog.parse_log(debug_path)
    if parsed.unreadable is not None:
        # The authoritative layer the caller ASKED for examined nothing. A missing
        # or unreadable log used to parse as an empty one, so `--debug <typo>`
        # printed "OK: no issues found" rc 0 (AUDIT-2026-09-24 VA-1). Degraded:
        # the whole engine-correlation check is off, so a clean run proves nothing.
        report.notes.append(parsed.coverage_note())
        report.skip("engine debug.txt correlation",
                    f"'{debug_path}' could not be read ({parsed.unreadable}) -- no "
                    "engine error was examined", degraded=True)
        return
    entries = [e for e in parsed.entries if e.ident_kind in ("path", "script", "lookup")]
    # The residue line goes in FIRST and unconditionally. Until 2026-08-13 this
    # function reported the count it had CLASSIFIED as the log's total, so a log
    # with 2,430 errors was described as having 1,363 — a denominator it never
    # measured. Now the log's own total is stated, and anything this parser could
    # not classify is named rather than absorbed.
    report.notes.append(parsed.coverage_note())
    if parsed.total and not entries:
        report.notes.append(
            f"debug: none of the {parsed.total} engine error(s) identify a mod file or "
            "script — see `x4debug triage` for the subsystem-level errors, which name a "
            "game ENTITY instead and need the effective store to attribute")
        return
    if not entries:
        report.notes.append(f"debug: no [=ERROR=] lines parsed from '{debug_path}' "
                            "(clean log, or wrong path / pre-load capture)")
        return
    ids = _mod_ids(mod_dir)
    names = _script_name_map(mod_dir)
    matched = 0
    diffops = 0
    # Shape G (index-lookup miss) names an ENTITY, not a file or script, so this
    # function cannot attribute it: which mod references a missing macro is a
    # search over the effective store (`x4debug triage`), not a lookup here. They
    # used to be counted as "mod-identifying" and then fall through the script
    # branch (`names.get("")`) and vanish (AUDIT-2026-09-24 VA-11b). Technical
    # default: DISCLOSED as not attributed, never guessed at.
    lookups = [e for e in entries if e.ident_kind == "lookup"]
    entries = [e for e in entries if e.ident_kind != "lookup"]
    if lookups:
        report.notes.append(
            f"debug: {len(lookups)} engine index-lookup error(s) (e.g. "
            f"'{lookups[0].lookup}' in '{lookups[0].lookup_index}') name an entity, "
            "not a file -- NOT attributed to this or any mod here. Check whether this "
            "mod references them with `x4debug triage`")
    for e in entries:
        if e.ident_kind == "path":
            if e.folder.lower() not in ids:
                continue
            # Shapes E/F name the patch file WITHOUT its extension — resolve it back
            # to the real file so the finding points somewhere openable.
            vpath = e.vpath
            for cand in _debuglog.xml_candidates(e.vpath):
                if (mod_dir / cand).is_file():
                    vpath = cand
                    break
        else:  # "script" — resolve name -> file via the mod's own scripts
            vpath = names.get(e.script_name)
            if vpath is None:
                continue
        matched += 1
        if e.cardinality:
            diffops += 1
        report.add(e.severity, "debug", f"(engine) {e.message}", vpath, e.line)
    report.notes.append(
        f"debug: {matched} engine finding(s) matched this mod from '{debug_path}' "
        f"(of {len(entries)} file/script-identifying, {parsed.total} total in the log)"
        + (f"; {diffops} are diff-op cardinality failures — the engine SKIPPED those "
           "ops, so those patches silently did nothing" if diffops else "")
        + ("" if matched else " — clean load for this mod, or a stale/other-mod log"))



def check_script_validation_scope(mod_dir: Path, config: _merge.Config,
                                  report: Report, remedy: str = "`--update`") -> None:
    """Disclose that md/ and aiscripts/ files went unvalidated in the default run.

    Reported from real use 2026-08-27: an additive-only <mdscript> with three
    md.xsd violations returned "OK: no issues found", exit 0.

    The tool CAN catch those -- MEASURED on the same file, `--update` reports the
    element-ordering ones as ERROR rc 1 and the surplus-attribute one as an
    advisory. What it does not do is run any script check by default, and that is
    deliberate: compiling md.xsd costs ~102s, so both `check_xsd` and the fast
    `check_required_attrs` pass sit behind --update.

    So the defect was never a missing check. It was a missing SENTENCE: for a mod
    whose payload is script files, nothing applicable ran, and the run still said
    "OK: no issues found" -- exactly what `Skipped` exists to prevent, and what
    this same tool already refuses to do for a content.xml-only folder.

    NOT degraded. MEASURED with the corrected predicate: 79 of 125 installed mods
    ship script XML and 18 are script-only. An exit 3 firing on 18 mods
    permanently, clearable only by paying the ~102s compile every run, converts it
    from "investigate" into "ignore" -- a check that floods is worse than no check.

    TWO populations, reported separately, because the ADVICE differs. Direct
    children of `md/` and `aiscripts/` are fixable by `--update`. A nested
    cross-mod patch is not: both halves of `_xsd.validate_mod` filter
    `count("/") != 1`, so nothing validates those at all. Counting them into a
    "run --update" message would have made the number complete and the advice
    FALSE -- worse than the under-count it replaced.
    """
    prefixes = tuple(f"{s}/" for s in _xsd.SCRIPT_DIRS)
    unreadable: list[_scan.Unreadable] = []
    top = nested = other = 0
    for vpath, _root in _scan.iter_mod_xml(mod_dir, lambda v: True, unreadable):
        low = vpath.lower()
        if not low.endswith(".xml") or low == "content.xml":
            continue
        if low.startswith(prefixes):
            top += 1
        elif _xsd.strip_nesting(vpath).startswith(prefixes):
            nested += 1          # a cross-mod script patch (CLAUDE.md #6)
        else:
            other += 1
    if top:
        # The required-attribute class now runs in every mode, so "nothing was
        # examined" is no longer true here -- name what IS still missing instead.
        # `--xsd-fast`'s own note uses this same split, deliberately.
        report.skip(
            "script-schema",
            f"{top} md/aiscripts file(s): the required-attribute class was checked "
            f"and is COMPLETE, but the 'element not expected' class - where element "
            f"ORDERING errors live - and the schema-strict advisories were not. "
            f"Those need the ~102s schema compile: {remedy}")
    if nested:
        # A SEPARATE statement, because the advice differs. Both halves of
        # `_xsd.validate_mod` filter `count("/") != 1` -- direct children only, and
        # deliberately, so the loose and packed halves cover the same population.
        # A nested cross-mod script patch is therefore validated by NOTHING, and
        # telling the caller to run `--update` would be false advice. MEASURED
        # 2026-08-27: 15 such files across 7 mods, every one a `<diff>`.
        # "only payload" belongs on THIS branch too. A mod whose sole payload is a
        # nested patch never reaches the branch above, so without this it is never
        # told that nothing was examined -- and it is the stronger case, since not
        # even `--update` would examine it. Caught by a prediction that disagreed:
        # 17 reported where 18 were expected.
        n_only = ("; they are this mod's only payload, so nothing that can fail "
                  "was examined" if not (top or other) else "")
        report.skip(
            "script-schema-nested",
            f"{nested} nested cross-mod script patch(es) at `extensions/<target>/"
            f"md|aiscripts/` are NOT VALIDATED BY ANYTHING - not by this run, and "
            f"not by `--update` either, which checks direct children only{n_only}")

    # The same gap one surface along, found by sweeping for the SHAPE rather than
    # by tripping over it: `check_effective_schema` validates merged data files
    # against the schema they declare and is gated behind --update identically.
    # Eligibility and the declaration lookup come from _xsd, never re-derived here
    # — a second implementation of the same normalisation is F66's whole point.
    lib = config.reference / "libraries"
    with_schema: list[str] = []
    seen: set[str] = set()
    for vpath, _root in iter_mod_xml_roots(mod_dir):
        v = vpath.replace("\\", "/")
        if v.lower() in seen or not _xsd.eligible(v):
            continue
        seen.add(v.lower())
        base = _merge.build_effective(v, config)
        note_dropped_overlays(base, "script validation scope", report)
        if base.tree is None:
            continue                      # brand-new file: no base to validate against
        schema_path, why_not = _xsd.schema_of(base.tree, lib, v)
        if why_not or schema_path is None:
            continue
        with_schema.append(v)
    if with_schema:
        report.skip(
            "effective-schema",
            f"{len(with_schema)} patched data file(s) declare a schema and the merged "
            f"result was NOT validated against it — runs only under {remedy} "
            f"(e.g. {with_schema[0]})")


def check_required_attrs(mod_dir: Path, config: _merge.Config,
                         report: Report) -> set[tuple[str, int, str]]:
    """Gating pass for `attribute X is required but missing` — no schema compile.

    FAST (~0.05s vs 98-122s). The KB calls this class the only reliable 9.0
    migration signal, and it is a flat fact per element, so it needs none of the
    content-model automaton that makes compiling `md.xsd`/`aiscripts.xsd` slow.

    Runs ALWAYS, ahead of `check_xsd`, so the gating answer is available
    immediately even when the full compile follows. Returns the keys it reported
    so `check_xsd` can avoid double-reporting the same finding.

    Equivalence with libxml2 on this class is proven corpus-wide by
    `gates/xsd_fast_parity.py` — it is not assumed.
    """
    lib = config.reference / "libraries"
    prefixes = tuple(f"{s}/" for s in _xsd.SCRIPT_DIRS)
    unreadable: list[_scan.Unreadable] = []
    reported: set[tuple[str, int, str]] = set()
    files = 0
    for vpath, root in _scan.iter_mod_xml(
            mod_dir,
            lambda v: v.lower().startswith(prefixes) and v.lower().count("/") == 1,
            unreadable):
        files += 1
        for f in _xsd.required_attr_findings(root, vpath, lib):
            key = (vpath, f.line, f.message)
            if key in reported:
                continue
            reported.add(key)
            report.add("error", "xsd", f.message, vpath, f.line)
    for u in unreadable:
        report.skip("required-attribute scan", str(u))
    # Only speak when there was something to check. This pass now runs in EVERY
    # mode, and a "0 script file(s) checked" line on the 46 of 125 installed mods
    # that ship no scripts is noise -- and noise is what trains people to stop
    # reading the notes. A mod with no script files gets no script commentary,
    # here or from check_script_validation_scope.
    if not files:
        return reported
    report.notes.append(
        f"required-attrs: {files} script file(s) checked without compiling a schema "
        f"({len(reported)} gating breakage(s)); "
        f"{_xsd.ambiguous_element_names(str(lib / 'md.xsd'))} element name(s) are declared with "
        f"conflicting requirements and use the INTERSECTION, so they can only "
        f"under-report, never raise a false error")
    return reported


def check_xsd(mod_dir: Path, config: _merge.Config, report: Report,
              already: set[tuple[str, int, str]] | None = None) -> None:
    """Validate MD/aiscript files against the bundled schemas (the 9.0 migration
    backbone). SLOW (~100s schema warmup) — only via --update, never the default.

    CATEGORIZE, don't gate: `md.xsd` is STRICTER than the actual engine (it rejects
    lowercase script/cue names and extra-but-tolerated attributes that working mods
    use). Evidence-based gating:
      GATE (error)    = 'required but missing' (loader enforces required attrs) AND
                        'element not expected' (an action not in the engine's schema
                        -> likely removed/renamed; safer to flag for review than miss).
      ADVISORY (info) = 'attribute not allowed' + name-pattern facets + key cascades
                        (the *evidenced* engine-tolerance noise from real mods).
    NOTE: nothing is hidden — advisories are still reported; categorization only sets
    severity + exit code. Authority on what truly breaks = the Migration Map + in-game test."""
    findings, checked, skipped = _xsd.validate_mod(mod_dir, config)

    # THE SKIP COUNT WAS DISCARDED. `skipped` was bound here and never read
    # again, so the files the schema pass could NOT check left no trace at all --
    # while the NESTED half five lines below already reports its own skips and
    # says "not checkable" in its note. Same function, two halves, one channel.
    #
    # `ROOT_TO_SCHEMA` holds only <mdscript> and <aiscript>, so every md/- or
    # aiscripts/-rooted document that is not one of those two is skipped -- and
    # the <diff> form is what CLAUDE.md #21 measures as 263 of 264 script-path
    # collisions. MEASURED over all 125 installed extensions: checked=181,
    # SKIPPED=206, i.e. 53.2% of the files --update reaches, across 44 of 125
    # mods. Two of them have checked == 0 and printed "XSD: 0 validated - 0
    # gating breakage(s)" -- output indistinguishable from a mod shipping no
    # scripts at all.
    #
    # NOT degraded, on the same evidence the default-mode disclosure was decided
    # on (tests/test_script_files_disclose_unvalidated.py): there is no bundled
    # schema for these roots, so an exit 3 would be permanent and unclearable,
    # and a gate that can never go green trains you to ignore it.
    if skipped:
        report.skip("XSD schema validation",
                    "%d of %d script file(s) have no bundled schema for their root "
                    "element -- only <mdscript> and <aiscript> have one, so a <diff>-"
                    "rooted script cannot be schema-checked at all"
                    % (skipped, checked + skipped))

    # F70: a nested cross-mod script patch is validated by NOTHING otherwise --
    # both halves of `validate_mod` filter `count("/") != 1`, so a patch at
    # `<mymod>/extensions/<target>/md/foo.xml` is never examined. Validate it
    # through the document the ENGINE builds, and ATTRIBUTE the result: MEASURED
    # over all 16 nested patches installed here, a merged-only check yields 182
    # findings of which 167 (91.8%) are the TARGET's own -- a flood, and a check
    # that floods is worse than no check.
    #
    # Scope "active" is a LITERAL, per CLAUDE.md #24: a target the engine will not
    # load cannot contribute to the document the engine builds, and `installed`
    # here would resolve a patch against a mod that is switched off.
    active = _registry.mods("active")
    # A nested patch whose target the engine leaves out resolves against nothing --
    # correct, and NAMED (F139).
    note_not_loaded(active, "nested script scope: a mod the engine does not load", report)
    nested = _xsd.validate_nested_scripts(
        mod_dir, config,
        {m["folder"].lower(): Path(m["path"]) for m in active})
    findings = findings + nested.introduced
    checked += nested.checked
    for nvpath, why in nested.skips:
        report.skip(f"nested script patch {nvpath}", why)
    if nested.checked or nested.skips:
        report.notes.append(
            f"XSD(nested): {nested.checked} cross-mod script patch(es) validated via "
            f"their merged result — {len(nested.introduced)} introduced, "
            f"{len(nested.fixed)} fixed, {len(nested.skips)} not checkable")

    def _gates(msg: str) -> bool:
        if _xsd.schema_element_gap(msg):
            return False  # engine-verified md.xsd gap — advisory, never a gate
        return "is required but missing" in msg or "is not expected" in msg

    high = [f for f in findings if _gates(f.message)]
    low = [f for f in findings if not _gates(f.message)]
    # Anything the fast pass already reported must not be reported twice.
    seen = already or set()
    dupes = sum(1 for f in high
                if (_relpath(f.file, mod_dir), f.line, f.message) in seen)
    report.notes.append(
        f"XSD: {checked} validated"
        f"{f', {skipped} NOT CHECKED (no bundled schema for their root)' if skipped else ''}"
        f" — {len(high)} gating breakage(s) "
        f"(required-attr / removed-element{f'; {dupes} already reported by the fast pass'
                                           if dupes else ''}), {len(low)} schema-strict "
        f"advisor{'y' if len(low) == 1 else 'ies'} (md.xsd stricter than the engine)")
    for f in high:
        if (_relpath(f.file, mod_dir), f.line, f.message) in seen:
            continue
        report.add("error", "xsd", f.message, _relpath(f.file, mod_dir), f.line)
    for f in low:
        # F7's dead-attr class gets its OWN CATEGORY here but stays INFO — never
        # promote it to error on the MD side. Deliberate asymmetry with
        # check_effective_schema: MD attributes can be spawn-time engine features
        # that neither debug.txt nor a static pass can settle (the four
        # kuertee_atd attributes, audit F8, deliberately left unresolved by the
        # user). The distinct category makes the class queryable without gating
        # a working released mod on an unsettleable question.
        gap = _xsd.schema_element_gap(f.message)
        if gap:
            # Say WHY it was demoted. A silent demotion is indistinguishable from
            # the checker simply not noticing, and it is the shape that would let
            # a real removed-element break hide in the advisory bucket.
            report.add("info", "xsd-schema-gap", f"{f.message}  [{gap}]",
                       _relpath(f.file, mod_dir), f.line)
            continue
        cat = ("xsd-unknown-attr"
               if _xsd.dead_attr(f.message, config) else "xsd-strict")
        report.add("info", cat, f.message, _relpath(f.file, mod_dir), f.line)


def _faction_defs(config: _merge.Config, extra_overlays=None, report=None) -> set[str]:
    f = _merge.build_effective(FACTIONS_FILE, config, extra_overlays=list(extra_overlays or []))
    note_dropped_overlays(f, "faction definitions", report)
    if f.tree is None:
        return set()
    return set(f.tree.xpath("//faction/@id"))


def _schema_gates(msg: str) -> bool:
    """Same evidence-based split `check_xsd` uses, applied to merged data files.

    GATE on structural breakage — a missing required attribute or an element the
    content model does not admit. Those are what a bad diff produces, and the
    measured examples are all real (an orphaned `<limits>`, a `<filter>` placed
    under `<sound>` when every one of vanilla's 309 sits under `<effects>`).

    ADVISORY for attribute-not-allowed, facet failures and key-constraint
    cascades: these are where the bundled XSDs lag the engine. Nothing is hidden
    either way — this only sets severity and the exit code.
    """
    return "is required but missing" in msg or "is not expected" in msg


def check_effective_schema(mod_dir: Path, config: _merge.Config, report: Report) -> None:
    """Schema-validate the EFFECTIVE merged tree for every data file the mod patches.

    Differential: baseline (everything but this mod) vs baseline+mod, and only the
    delta is reported. `_xsd.introduced` documents why that is mandatory rather
    than merely tidy.

    Under Tier B the baseline already contains the other installed mods, so their
    damage is subtracted out too and what remains is this mod's own contribution.
    """
    lib = config.reference / "libraries"
    defs: dict[str, set[str]] = {}
    checked = suppressed = 0
    #: Counted rather than silently `continue`d, so the note can say WHY nothing was
    #: validated. `new_files` is the known gap: a file the mod introduces has no base
    #: to difference against, so it gets no schema check at all.
    new_files = no_schema = 0
    seen: set[str] = set()

    for vpath, _root in iter_mod_xml_roots(mod_dir):
        v = vpath.replace("\\", "/")
        if v.lower() in seen or not _xsd.eligible(v):
            continue
        seen.add(v.lower())

        base = _merge.build_effective(v, config)
        note_dropped_overlays(base, f"schema check for {v}", report)
        if base.tree is None:
            new_files += 1  # brand-new file: nothing to difference against
            continue
        schema_path, why_not = _xsd.schema_of(base.tree, lib, v)
        if why_not:
            report.skip(f"schema check for {v}", why_not)
            continue
        if schema_path is None:
            no_schema += 1  # macros, components, wares.xml — not a finding
            continue

        before, why_not = _xsd.errors_against(base.tree, schema_path)
        merged = _merge.build_effective(v, config, extra_overlays=[mod_dir])
        note_dropped_overlays(merged, f"schema check for {v}", report)
        if why_not or merged.tree is None:
            report.skip(f"schema check for {v}",
                        why_not or "the merged tree could not be built")
            continue
        after, why_not = _xsd.errors_against(merged.tree, schema_path)
        if why_not or before is None or after is None:
            report.skip(f"schema check for {v}", why_not or "the schema could not be applied")
            continue
        checked += 1

        for msg in _xsd.introduced(before, after):
            if not defs:  # built once, and only if something actually needs it
                defs = {"race": _race_defs(config, [mod_dir], report),
                        "faction": _faction_defs(config, [mod_dir], report)}
            if _xsd.open_lookup_ok(msg, defs):
                suppressed += 1
                continue
            # Severity ladder (audit F7+F14, measured 2026-08-02 before written:
            # exactly 15 findings move INFO->ERROR across 3 mods, each verified
            # individually real — see gates/schema_sweep.py's re-measurement table).
            # The "md.xsd is stricter than the engine" excuse only covers what the
            # engine demonstrably tolerates; a value defined NOWHERE (enum-undefined)
            # or an (element, attribute) pair vanilla never uses (dead-attr) has
            # nothing to lag, so those gate.
            if _schema_gates(msg):
                sev, cat = "error", "schema"
            elif _xsd.enum_undefined(msg, defs):
                sev, cat = "error", "schema-enum-undefined"
            elif _xsd.dead_attr(msg, config):
                sev, cat = "error", "schema-dead-attr"
            else:
                sev, cat = "info", "schema-strict"
            report.add(sev, cat,
                       f"{msg} (introduced by this mod into the merged {v})", v, 0)

    # Unconditional, exactly like check_xsd's note 40 lines above. Guarding this on
    # `if checked:` meant a mod where nothing was validated printed NO schema line at
    # all, so "we validated 0 files" and "we did not run" looked identical — measured
    # on atd_ejection_router, which produced a bare "OK: no issues found".
    # F52 (fixed 2026-08-25): this used to be `if not checked and (...)`, so the
    # reason was emitted only when ZERO files were validated. A mod that validated
    # one file and skipped two reported "1 merged data file(s) validated" and said
    # NOTHING about the other two. MEASURED on three mods deployed the same day:
    # `a personal overlay` correctly said "0 validated — 2 declaring no
    # schema", while `Synthetium_Music` said "1 validated" and stayed silent about
    # its other two eligible files.
    #
    # The guard was added for a real reason (`if checked:` made "validated 0" and
    # "did not run" indistinguishable) but it over-corrected from silent-on-zero to
    # silent-on-any-success. This register's own rule is that a step which narrows
    # the data announces it EVEN WHEN it also succeeded at something.
    #
    # ⚠ The leading `effective-schema: {checked} ` tokens must not move:
    # `gates/schema_sweep.py:427` reads the pair count with `int(note.split()[1])`.
    # The detail is a SUFFIX for exactly that reason.
    detail = ""
    if new_files or no_schema:
        why = []
        if new_files:
            why.append(f"{new_files} brand-new (no base to difference against)")
        if no_schema:
            why.append(f"{no_schema} declaring no schema")
        detail = " — " + ", ".join(why)
    report.notes.append(
        f"effective-schema: {checked} merged data file(s) validated against their declared "
        f"schema, differenced against the same tree without this mod{detail}"
        + (f"; {suppressed} enumeration failure(s) suppressed as mod-defined races/factions"
           if suppressed else ""))


def check_packed_dlc_available(config: _merge.Config, report: Report) -> None:
    r"""Report when DLC that exist only PACKED cannot be reached.

    `reference/` holds only the DLC that were unpacked; the mini-DLC
    (`ego_dlc_mini_01` Hyperion Pack, `ego_dlc_mini_02` Envoy Pack) never were, so
    `dlc_dirs()` reaches them through the live game install instead. When the game
    root is not configured that silently drops from **8 DLC to 6** (measured
    2026-07-29) and `packed_dlc_names()` returns empty — every patch against
    Hyperion/Envoy content then falls back to "installed but never unpacked — cannot
    verify", which reads as *does not apply to me* rather than *I could not look*.

    This is the same shape B2 fixed one level down: B2 taught `dlc_dirs()` to SEE the
    packed DLC but gave it no channel for "I couldn't". Not `degraded` — most mods
    touch no mini-DLC content, so this is a partial skip, not a failed run.
    """
    if not config.include_packed_dlc or not _merge.is_configured_reference(config.reference):
        return  # deliberately isolated (hermetic run / foreign --reference)
    game_ext = _merge.GAME_ROOT / "extensions"
    if game_ext.is_dir():
        return
    report.skip("packed-DLC content",
                f"the live game install is not reachable at '{_merge.GAME_ROOT}', so DLC that "
                "were never unpacked into reference/ (the Hyperion and Envoy mini-DLC) are NOT "
                "in the tree. Patches targeting their content cannot be verified. Set $X4_GAME "
                "(see x4-paths.env) or run `x4validate --paths` to see what resolved")


def reference_ready(config: _merge.Config, report: Report) -> bool:
    """Guard: the reference tree must actually be loaded.

    Without it EVERY check degrades to a meaningless pass -- a sel matches
    nothing because there is nothing there, refs dangle against an empty
    catalog, completeness has no analogue. The tool's whole reason to exist
    (catch the silent no-op) would itself silently no-op and report 'OK'.
    So fail loud and non-zero instead of falsely passing.

    `libraries/wares.xml` is always present in the base game, so if it cannot
    be resolved the reference tree is missing or empty.
    """
    ref = config.reference
    if not ref.is_dir():
        report.add("error", "reference",
                   f"reference tree not found at '{ref}' -- unpack the base game first "
                   "($X4_REFERENCE / --reference). Validation is meaningless without it.",
                   str(ref))
        return False
    _ready = _merge.build_effective(WARES_FILE, config)
    note_dropped_overlays(_ready, "reference readiness", report)
    if _ready.tree is None:
        report.add("error", "reference",
                   f"reference tree at '{ref}' is empty or incomplete "
                   f"(base '{WARES_FILE}' not found) -- re-unpack the base game; "
                   "results are meaningless without a real reference tree.",
                   str(ref))
        return False
    return True


def validate(
    mod_dir: Path,
    config: _merge.Config | None = None,
    entity: str | None = None,
    like: str | None = None,
    only_file: str | Path | None = None,
    update: bool = False,
    debug: str | Path | None = None,
    tier: str = "a",
    xsd_fast: bool = False,
    sel_only: bool = False,
) -> Report:
    config = config or _merge.Config()
    report = Report()
    if not reference_ready(config, report):
        return report  # empty/missing reference -> a clean run would be a false 'OK'
    check_packed_dlc_available(config, report)
    if tier == "b" and not config.overlays:
        trees = tier_b_trees(mod_dir, report)
        config = replace(config, overlays=trees.patch_time, final_overlays=trees.final)
        report.notes.extend(trees.notes)
    if only_file is not None or sel_only:
        # Fast paths. `--file` (the per-edit hook): sel-resolution for ONE file.
        # `--sel-only`: readability + sel-resolution for the whole mod -- it was
        # parsed and never read, so every other check still ran and gated
        # (AUDIT-2026-09-24 VA-8).
        mode = "--file" if only_file is not None else "--sel-only"
        # A check the caller ASKED for and this mode does not run is DEGRADED, never
        # silently dropped: `--file` used to swallow --debug/--entity/--like/--update
        # and print a clean result, rc 0 (AUDIT-2026-09-24 VA-3). Disclosing rather
        # than refusing keeps the per-edit hook (`--file --tier --json` only) as is.
        requested = []
        if entity or like:
            requested.append("completeness (--entity/--like)")
        if update:
            requested.append("9.0 mechanical-port checks (--update)")
        if debug is not None:
            requested.append("engine debug.txt correlation (--debug)")
        for what in requested:
            report.skip(what, f"{mode} runs sel-resolution only, so this REQUESTED check "
                        f"did not run -- rerun without {mode}", degraded=True)
        if only_file is not None:
            check_sel_resolution_one(Path(only_file), mod_dir, config, report)
        else:
            check_readability(mod_dir, config, report)
            check_sel_resolution(mod_dir, config, report)
        report.skip(f"every other check ({mode})",
                    "references, file existence, connections, module groups, text, "
                    "identity values, variants, expression lint, required attributes "
                    f"and schema were NOT run -- {mode} runs sel-resolution only")
        return report
    check_readability(mod_dir, config, report)  # first: names what every other check will miss
    # PATCH-TIME tree: a selector only sees what has loaded by this mod's turn.
    check_sel_resolution(mod_dir, config, report)
    # RUNTIME tree: "does this id resolve?" is answered after every extension loads,
    # so these must also see mods that load LATER. Same tree under Tier A — see
    # Config.for_runtime. Getting this backwards costs a real finding in each
    # direction; `tier_b_trees` documents the measured case.
    runtime = config.for_runtime()
    check_references(mod_dir, runtime, report)
    check_file_existence(mod_dir, runtime, report)
    check_connections(mod_dir, runtime, report)
    check_module_groups(mod_dir, runtime, report)  # runtime: a later mod's group is still real
    check_page_collisions(mod_dir, config, report)
    check_text_sanity(mod_dir, config, report)
    check_identity_values(mod_dir, config, report)
    check_engine_connection_tags(mod_dir, config, report)
    check_variant_consistency(mod_dir, config, report)
    check_exprlint(mod_dir, config, report)  # cheap, always-on: expression-grammar heuristic
    if entity and like:
        check_completeness(mod_dir, runtime, report, entity, like)  # runtime: needs macro defs
    elif entity or like:
        # ONE FLAG IS A REQUEST THAT CANNOT BE HONOURED, not an absent request.
        # `--entity` and `--like` are two independent optional arguments with no
        # mutual requirement, so half a request fell off the end of this `if` and
        # the run printed "OK: no issues found" with rc 0 -- no note, no NOT
        # CHECKED, no warning. MEASURED on one mod, three invocations: with both
        # flags, `completeness checked kinds: component, definition, ...` plus an
        # INFO finding; with either flag alone, every trace of the requested check
        # is ABSENT -- not downgraded, not skipped.
        #
        # README: "3 degraded -- a check you asked for could not run, so a clean
        # result proves nothing", and it names "a --like analogue that does not
        # exist" as a case that reaches it. A --like that was never SUPPLIED is a
        # stronger form of the same condition. `check_completeness` itself already
        # refuses the vacuous comparison one layer down, for the same reason: an
        # all-False footprint means nothing is ever "missing" and the result reads
        # as a clean pass.
        report.skip("completeness",
                    "--entity and --like must be given together; got only %s. "
                    "A footprint comparison needs both the new entity and the "
                    "vanilla analogue to compare it against, so no completeness "
                    "check ran." % ("--entity" if entity else "--like"),
                    degraded=True)
    if debug is not None:  # authoritative: fold the engine's own errors for this mod (gates)
        check_debug_correlation(mod_dir, config, report, debug)
    # Runs in EVERY mode. No schema compile, so it costs ~0.1s on the heaviest
    # script mod in a real install (60 files) and 6.3s across all 125 -- and its
    # corpus-wide parity with libxml2 is proven by gates/xsd_fast_parity.py (555
    # script files, 0 false positives, 0 misses). It sat behind --update only by
    # association with check_xsd, which compiles md.xsd at ~102s.
    #
    # This is what stops a script-only default run from examining NOTHING, which
    # is the honest way to satisfy README's "0 = clean AND something was actually
    # examined". The alternative considered and rejected was degrading those mods
    # to exit 3: MEASURED 18 of 125, permanently, clearable only by paying 102s
    # every run -- which turns exit 3 from "investigate" into "ignore".
    #
    # MEASURED by a per-mod exit-code census across all 125 installed mods,
    # both tiers: EXACTLY ONE mod changes exit code, 0 -> 1, and it is a TRUE
    # POSITIVE -- `xenon e class ship` has `find_ship` missing the required
    # `space` attribute (md/xenone_resistant_system.xml:17), a real 9.0
    # breakage that was invisible without --update.
    #
    # An earlier measurement claimed ZERO and was wrong: it filtered findings
    # on `f.level`, and the field is `f.severity`. `getattr(f, 'level', '')`
    # returns '' rather than raising, so the filter matched nothing and
    # reported a confident 0 -- the silent-default shape (CLAUDE.md's
    # `el.get("id")` row). Use `Report.errors`, which cannot miss this way.
    already = check_required_attrs(mod_dir, config, report)
    if not update:
        # Discloses what is STILL not checked now that the cheap half has run.
        check_script_validation_scope(mod_dir, config, report)
    if update:  # mechanical-port extras (9.0 migration): runtime heuristic + XSD (slow, last)
        check_migration(mod_dir, config, report)
        if not xsd_fast:
            check_xsd(mod_dir, config, report, already)   # script files, as written
            check_effective_schema(mod_dir, config, report)  # data files, as merged
        else:
            # THE DISCLOSURE SET MUST BE MONOTONIC. Adding `--xsd-fast` skips both
            # `check_xsd` and `check_effective_schema`, which reopens exactly the
            # holes the DEFAULT run discloses -- but the disclosure lived behind
            # `if not update:`, so the MORE thorough invocation emitted FEWER NOT
            # CHECKED entries than the less thorough one. MEASURED on one fixture:
            #   default             -> script-schema + effective-schema disclosed
            #   --update --xsd-fast -> no NOT CHECKED section at all
            # while the note claimed its gating coverage was COMPLETE.
            #
            # The remedy differs in this mode -- the caller already passed
            # `--update` -- so it is named rather than hardcoded.
            check_script_validation_scope(
                mod_dir, config, report, remedy="`--update` without `--xsd-fast`")
            report.notes.append(
                "--xsd-fast: skipped the compiled-schema pass (~100-122s). The "
                "required-attribute class is COMPLETE FOR DIRECT CHILDREN of md/ "
                "and aiscripts/; what is skipped is the advisory set, the "
                "'element not expected' class, the merged data-file schema pass "
                "(check_effective_schema), and nested cross-mod script patches, "
                "which only the compiled pass reaches. See NOT CHECKED above.")
    return report
