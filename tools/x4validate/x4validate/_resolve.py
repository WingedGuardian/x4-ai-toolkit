"""Resolve X4's macro/component/connection graph to real files on disk.

Powers two v1.1 checks:
  - file-existence: <component ref="X_macro"> -> index/macros.xml value -> macro
    file exists? -> macro's <component ref> -> index/components.xml -> file exists?
  - connection-validation: a <loadout> entry `path="../con_engine_01"` must match a
    <connection name="con_engine_01"> in the ship's component.

Index `value` is a path WITHOUT `.xml`, backslash-separated. A value written
game-root-relative as `extensions\\<X>\\<rest>` (DLC always, mods conventionally —
matching the engine) resolves as <rest> inside folder X: the registering DLC/mod's
own root when X is its own folder, otherwise the DLC or sibling mod named X (see
`_Roots`). Other values resolve against the reference root (base/DLC entries) or
the mod root (mod entries). Both index and payload reads are packed-aware.
Wildcard entries (`character_*`) are skipped — they're patterns, not files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from . import _cat, _merge

MACRO_INDEX = "index/macros.xml"
COMPONENT_INDEX = "index/components.xml"


def _index_entries(root: etree._Element):
    for entry in root.xpath("//entry[@name]"):
        name = entry.get("name")
        value = entry.get("value")
        if not value or "*" in name:  # skip wildcard patterns
            continue
        yield name, value


def _extension_prefix(value: str) -> tuple[str, str] | None:
    """(folder, rest) for a value written `extensions/<folder>/<rest>`, else None."""
    parts = value.replace("\\", "/").lstrip("/").split("/")
    if len(parts) > 2 and parts[0].lower() == "extensions":
        return parts[1], "/".join(parts[2:])
    return None


def _strip_mod_index_prefix(value: str, own_folder: str) -> str:
    """Drop a leading `extensions/<own_folder>/` from a MOD index value so it
    resolves relative to the mod root (the engine resolves these game-root-relative;
    here the mod root already IS that `extensions/<mod>/` dir).

    ONLY the registering mod's OWN folder is stripped (case-insensitive, as the
    engine's folder lookup is). A value naming ANOTHER folder points into that
    folder, and stripping it anyway resolved the path inside the REGISTERING mod —
    reading the wrong mod's file whenever it happened to have one at the same
    relative path (AUDIT-2026-09-24 MG-4). Such values are resolved by
    `_index_target`, never here.
    """
    split = _extension_prefix(value)
    if split is not None and split[0].lower() == own_folder.lower():
        return split[1]
    return value


class _Roots:
    """Where an `extensions/<X>/` index value lands: the DLC or mod folder X.

    Looked up case-insensitively among the DLC roots (`Config.dlc_dirs()`, packed
    and unpacked alike), every overlay the config names, then the folder beside the
    registering mod and the live install's `extensions/`. An X found nowhere is
    RECORDED on the report (once) and resolved to where the engine would look, so
    the file checks report it missing — never silently read from the registering
    mod.
    """

    def __init__(self, config: _merge.Config, extra_overlays, report, index_rel: str):
        self.config, self.report, self.index_rel = config, report, index_rel
        self.dlc = {p.name.lower(): p for p in config.dlc_dirs()}
        self.mods: dict[str, Path] = {}
        for ov in (list(config.overlays) + list(config.final_overlays)
                   + list(extra_overlays or [])):
            self.mods.setdefault(ov.name.lower(), ov)

    def folder(self, name: str, beside: Path) -> Path | None:
        key = name.lower()
        if _merge.is_dlc_folder(key):
            return self.dlc.get(key)
        if key in self.mods:
            return self.mods[key]
        for cand in (beside.parent / name, _merge.GAME_ROOT / "extensions" / name):
            if cand.is_dir():
                return cand
        return None

    def target(self, value: str, own_root: Path, default_root: Path) -> tuple[Path, str]:
        """(resolution_root, value) for an entry registered by *own_root*.

        A value without an `extensions/<X>/` prefix resolves against *default_root*
        (the reference tree for base/DLC entries, the mod itself for a mod's)."""
        split = _extension_prefix(value)
        if split is None:
            return default_root, value
        own = _strip_mod_index_prefix(value, own_root.name)
        if own is not value:
            return own_root, own
        folder, rest = split
        found = self.folder(folder, own_root)
        if found is not None:
            return found, rest
        where = (self.config.reference / "extensions" / folder
                 if folder.lower().startswith("ego_dlc_")
                 else _merge.GAME_ROOT / "extensions" / folder)
        if self.report is not None:
            what = f"{self.index_rel} resolution"
            why = (f"{own_root.name}: registers entries in extensions/{folder}/, which is "
                   "not installed — those entries resolve to files that do not exist")
            if not any(s.what == what and s.why == why for s in self.report.skipped):
                self.report.skip(what, why)
        return where, rest


def _read_source(root: Path, rel: str) -> etree._Element | None:
    """Parse *rel* under *root*, LOOSE first then inside the root's .cat catalogs.

    A packed mod has no loose XML at all, so a `.is_file()`-only read returns
    None for every file it owns — indistinguishable from "this mod has no index".
    """
    f = root / rel
    if f.is_file():
        return _merge.parse_file(f)
    data = _cat.read_path(root, rel)
    return _merge.parse_bytes(data) if data is not None else None


def _skip_once(report, what: str, why: str, degraded: bool = False) -> None:
    """Record a skip unless the same (what, why) is already on the report.

    `build_index` runs once per index per check, and three checks (file existence, connections,
    fitting) each build both indexes, so an unguarded record turned ONE missing reference index
    into three identical lines (six degraded skips for two causes). One cause reads as one line.
    """
    if report is None:
        return
    if not any(s.what == what and s.why == why for s in report.skipped):
        report.skip(what, why, degraded=degraded)


def ordered_roots(config: _merge.Config, extra_overlays) -> tuple[list[Path], bool]:
    """(every root in RUNTIME load order, whether the mod-under-test position is known).

    *extra_overlays* (the mod under test) go at `config.runtime_position` -- set by
    `Config.for_runtime()` from the Tier B trees -- so its own index edits apply before the
    mods that load after it (issue #23 review I-2: the copier was placed LAST, after the mod
    that re-points its entry, and read its own file where the engine uses the game's). With
    identical patch-time and runtime trees the mod loads after all of them. Otherwise the
    position is unknown: the extras go LAST and the caller discloses it."""
    base = list(config.overlays)
    have = {b.name.lower() for b in base}
    extra = [e for e in (extra_overlays or []) if e.name.lower() not in have]
    pos = config.runtime_position
    if pos is None and config.final_overlays and tuple(config.final_overlays) == tuple(base):
        pos = len(base)
    if pos is None:
        return base + extra, not (base and extra)
    pos = min(pos, len(base))
    return base[:pos] + extra + base[pos:], True


def _bare_value_root(value: str, writer: Path, ordered: list[Path],
                     config: _merge.Config) -> Path:
    """Where a MOD-written value without `extensions/<X>/` lands, in four steps:

    1. the writer's own COMPLETE file (INFERRED: the ordinary case, a mod registering a macro it
       ships; the pre-F384 reader's only rule);
    2. else a DLC's complete file at that path (INFERRED: the VFS puts a DLC over the base);
    3. else the reference tree's file (MEASURED, BLIND-SPOTS F383: 7 of 7 names a mod re-pointed
       at a bare base path were loaded from the plain path, the game's node first -- another
       mod's ship-macro copy merges behind it, F382, read by `_check.EngineDocs`, and the 3
       weapon copies of that shape were refused outright, "Skipping file");
    4. else the last mod in load order that ships a complete file there (INFERRED).

    A writer that ships only a `<diff>` there owns nothing at step 1. When the file chosen does
    not define the name -- the writer's diff ADDS it into the game's file -- `build_index`
    serves the MERGED document instead (`_with_merged`, round-5 review)."""
    rel = value.replace("\\", "/").lstrip("/") + ".xml"

    def complete(root: Path) -> bool:
        try:
            found = _merge.overlay_root(root, rel, [])
        except (etree.LxmlError, OSError, ValueError):
            return False   # silent-ok: an unreadable candidate is simply not chosen
        return found is not None and found.tag != "diff"

    if complete(writer):
        # The writer's own DOCUMENT. A writer that only ships a <diff> there (the measured
        # re-pointing overlay patches the same file) owns nothing: its diff is no document.
        return writer
    for d in reversed(config.dlc_dirs()):
        if complete(d):
            return d
    if (config.reference / rel).is_file():
        return config.reference
    for root in reversed(ordered):
        if root != writer and complete(root):
            return root
    return config.reference


class _Entry(tuple):
    """An index entry `(root, value)` that may also carry the MERGED document's bytes, for a name
    a mod's diff adds into another layer's file (`read_indexed` serves them). Still a 2-tuple
    for every `root, value = entry` / `resolve_path(*entry)` caller."""
    merged: bytes | None = None


def _names_in(data: bytes | None) -> set[str] | None:
    """Every element @name in the document *data*, lowercased (the engine resolves
    macro/component names ignoring case); None when there is no document or it will not parse."""
    if data is None:
        return None
    try:
        root = _merge.parse_bytes(data)
    except etree.XMLSyntaxError:
        return None   # silent-ok: an unparseable file defines nothing here; the readers report it
    return {(el.get("name") or "").lower() for el in root.iter() if isinstance(el.tag, str)} - {""}


def _with_merged(name: str, root: Path, value: str, ordered: list[Path],
                 config: _merge.Config, report, index_rel: str, cache: dict):
    """*(root, value)* as an `_Entry`, carrying the MERGED document at that path when the file
    *root* holds does not define *name* but the merged document does (a mod's `<diff>` adds the
    macro into the game's file: the engine loads the merged document; round-5 review, ror's
    clusters). Reading the raw file instead narrowed the data to nothing and reported success.

    *cache* lives for ONE `build_index` call: each path is read, parsed and merged at most once,
    however many names it serves (a map mod adding hundreds of macros by diff would otherwise
    rebuild its file once per name). Never silent: an unreadable file, an unbuildable merged
    document, and a name that NEITHER document defines are each a degraded skip naming the name."""
    rel = value.replace("\\", "/").lstrip("/") + ".xml"
    entry = _Entry((root, value))
    want = name.lower()

    def skip(why: str) -> None:
        if report is not None:
            _skip_once(report, f"{index_rel} resolution",
                       f"'{name}' is registered at {rel}: {why} -- what hangs off it was not resolved",
                       degraded=True)

    rkey = ("raw", str(root).lower(), rel.lower())
    if rkey not in cache:
        p = resolve_path(root, value)
        try:
            raw = p.read_bytes() if p.is_file() else _cat.read_path(root, rel)
        except (OSError, ValueError) as exc:
            cache[rkey] = exc
        else:
            cache[rkey] = (raw is not None, _names_in(raw))
    got = cache[rkey]
    if isinstance(got, Exception):
        skip(f"its file could not be read ({got})")
        return entry
    present, raw_names = got
    if raw_names is not None and want in raw_names:
        return entry
    mkey = ("merged", rel.lower())
    if mkey not in cache:
        built = _merge.build_effective(
            rel, _merge.replace(config, overlays=tuple(ordered), final_overlays=()))
        if built.skipped:
            cache[mkey] = ("skipped", built.skipped[0], None)
        elif built.tree is None:
            cache[mkey] = ("none", None, None)
        else:
            data = etree.tostring(built.tree)
            cache[mkey] = ("ok", data, _names_in(data))
    state, data, merged_names = cache[mkey]
    if state == "skipped":
        skip(f"its file does not define it and its merged document could not be built completely ({data})")
        return entry
    if state == "ok" and merged_names is not None and want in merged_names:
        entry.merged = data
        return entry
    if present:
        # A file is there and defines nothing of that name, merged or not: never a plain entry
        # whose readers then find nothing and report success. (No file at all is left to the
        # readers, which already report "registered but file missing".)
        skip("neither its file nor the merged document at that path defines it")
    return entry


def build_index(config: _merge.Config, extra_overlays, index_rel: str,
                report=None) -> dict[str, tuple[Path, str]]:
    """name -> (resolution_root, value), from the EFFECTIVE index document.

    The index is built the way the engine builds it: base + DLC + every mod in RUNTIME load
    order through `_merge.build_effective`, so a `<remove>` unregisters a name and a
    `<replace>` of an entry's `@value` re-points it (issue #23 review I-3). This used to
    collect every `<entry>` of every file, a `<diff>`'s payload included, and ignore the ops:
    MEASURED on 230 active roots, 9 of 14 op-touched names in `index/macros.xml` and 3 of 4 in
    `index/components.xml` disagreed with the effective index. For two entries of one name the
    LAST in document order wins, as `_merge.index_loaded_path` reads it.

    Each value resolves against the root of the layer that WROTE it (`Recorder.winner` on
    `@value`): base/DLC against the reference tree, a mod's against that mod
    (`_bare_value_root`), and an `extensions/<X>/` value inside X (`_Roots`).

    Unreadable layers keep their degraded skips: a base/DLC index that is missing or will not
    parse, and a mod index that will not parse, are recorded per source (and left out of the
    effective document by `build_effective`, as before)."""
    index: dict[str, tuple[Path, str]] = {}
    roots = _Roots(config, extra_overlays, report, index_rel)
    ordered, position_known = ordered_roots(config, extra_overlays)
    if not position_known and report is not None:
        note = ("runtime index: the mod under test's load position among the other roots is "
                "not known (no Tier B tree), so its index edits were applied LAST")
        if note not in report.notes:
            report.notes.append(note)
    # Per-source diagnostics, the same skips as before (BLIND-SPOTS F364, AUDIT VA-12).
    for src in [config.reference] + config.dlc_dirs():
        try:
            root = _read_source(src, index_rel)
        except (etree.XMLSyntaxError, OSError) as exc:
            if report is not None:
                _skip_once(report, f"{index_rel} resolution",
                            f"base/DLC index {src / index_rel} is unreadable ({exc}) — the "
                            "entries it defines were not registered", degraded=True)
            continue
        if root is None and report is not None:
            if src == config.reference and not (src / "index").is_dir():
                _skip_once(report, f"{index_rel} resolution",
                            f"base index {src / index_rel} is missing and {src} has no index folder -- no vanilla "
                            "entry was registered; every base-game macro/component now reads as unregistered. "
                            "Check the reference tree (x4-paths.env / --reference)", degraded=True)
            elif src == config.reference:
                _skip_once(report, f"{index_rel} resolution",
                            f"base index {src / index_rel} is missing (the index folder exists) -- its entries "
                            "were not registered")
            else:
                _skip_once(report, f"{index_rel} resolution",
                            f"DLC {src.name} has no {index_rel} (loose or packed) -- its entries were not registered")
    for ov in ordered:
        try:
            _read_source(ov, index_rel)
        except (etree.XMLSyntaxError, OSError) as exc:
            if report is not None:
                _skip_once(report, f"{index_rel} resolution",
                           f"{ov.name}: index unreadable ({exc}) — macros it registers will "
                           "read as unregistered", degraded=True)
    recorder = _merge.Recorder()
    merged = _merge.build_effective(
        index_rel, _merge.replace(config, overlays=tuple(ordered), final_overlays=()),
        recorder=recorder)
    if merged.tree is None:
        return index
    by_name = {r.name.lower(): r for r in ordered}
    merged_cache: dict = {}   # one build_index call: each path read and merged once
    dlc = {d.name.lower(): d for d in config.dlc_dirs()}
    for entry in merged.tree.xpath("//entry[@name]"):
        name, value = entry.get("name"), entry.get("value")
        if not value or "*" in name:   # skip wildcard patterns
            continue
        source = recorder.winner(entry, "value").source.lower()
        if source in by_name:
            writer = by_name[source]
            if _extension_prefix(value) is None:
                root = _bare_value_root(value, writer, ordered, config)
                index[name] = (_with_merged(name, root, value, ordered, config, report, index_rel,
                                            merged_cache)
                               if root != writer else (root, value))
            else:
                index[name] = roots.target(value, writer, writer)
        else:
            own = dlc.get(source, config.reference)
            index[name] = roots.target(value, own, config.reference)
    return index


def resolve_path(root: Path, value: str) -> Path:
    return root / (value.replace("\\", "/").lstrip("/") + ".xml")


@dataclass
class Located:
    """A file an index entry points at, wherever it actually lives."""
    display: Path       #: for messages — the on-disk path, or root/vpath if packed
    data: bytes
    packed: bool = False


def _rel_of(value: str) -> str:
    return value.replace("\\", "/").lstrip("/") + ".xml"


def read_indexed(index: dict[str, tuple[Path, str]], name: str) -> Located | None:
    """Contents of the file *name* is registered to, loose or inside a .cat.

    None means "not registered, or registered to a file that does not exist" —
    the caller distinguishes those by checking `name in index` first.

    Packed-awareness is not optional here. `code_vgr_battlecruiser` registers
    `ship_vgr_battlecruiser_01_a_macro` and ships the macro inside its `.cat`;
    a loose-only check calls that file missing and reports an error about a mod
    that is perfectly fine. Measured 2026-07-28: 11 such false errors across two
    installed mods.
    """
    if name not in index:
        return None
    merged = getattr(index[name], "merged", None)
    root, value = index[name]
    p = resolve_path(root, value)
    if merged is not None:
        # The MERGED document at the path (`_with_merged`): the file there lacks the name.
        return Located(p, merged)
    if p.is_file():
        try:
            return Located(p, p.read_bytes())
        except OSError:
            # silent-ok: an unreadable file is treated as absent, which is what the
            # caller then reports — "registered but file missing" names the path.
            return None
    data = _cat.read_path(root, _rel_of(value))
    if data is None:
        return None
    return Located(p, data, packed=True)


def file_present(index: dict[str, tuple[Path, str]], name: str) -> Path | None:
    """Resolved LOOSE file path if the index has *name* and the file exists.

    Kept for callers that genuinely need a real on-disk path. Anything that only
    wants to READ the file must use `read_indexed`, which also sees packed mods.
    """
    if name not in index:
        return None
    p = resolve_path(*index[name])
    return p if p.is_file() else None


def macro_component_links(
    macro_name: str,
    macro_index: dict[str, tuple[Path, str]],
    component_index: dict[str, tuple[Path, str]],
) -> list[str]:
    """Walk macro->file->component->file; return human messages for broken links.

    Assumes *macro_name* IS registered (unregistered macros are caught upstream as
    dangling refs). Empty list = the whole chain resolves to real files."""
    out: list[str] = []
    if macro_name not in macro_index:
        return out  # not our job here; dangling-ref check covers unregistered
    located = read_indexed(macro_index, macro_name)
    if located is None:
        out.append(f"macro '{macro_name}' registered but file missing: "
                   f"{resolve_path(*macro_index[macro_name])}")
        return out
    try:
        mroot = _merge.parse_bytes(located.data)
    except etree.XMLSyntaxError as exc:
        out.append(f"macro '{macro_name}' file unparseable: {exc}")
        return out
    comps = mroot.xpath(f"//macro[@name={_xq(macro_name)}]/component/@ref")
    if not comps:
        return out  # some macros legitimately have no component
    return out + _component_link(macro_name, comps[0], component_index)


def _component_link(macro_name: str, comp_ref: str,
                    component_index: dict[str, tuple[Path, str]]) -> list[str]:
    """The component half of `macro_component_links`."""
    if comp_ref not in component_index:
        return [f"macro '{macro_name}' -> component '{comp_ref}' not registered in index/components.xml"]
    if read_indexed(component_index, comp_ref) is None:
        return [f"component '{comp_ref}' registered but file missing: "
                f"{resolve_path(*component_index[comp_ref])}"]
    return []


def connections_of(data: bytes) -> set[str] | None:
    """Connection names in a component document, or None if it will not parse."""
    try:
        root = _merge.parse_bytes(data)
    except etree.XMLSyntaxError:
        # silent-ok: None is this function's whole point — see the docstring on
        # component_connections. The caller reports it via Report.skip.
        return None
    return set(root.xpath("//connection/@name"))


def connection_tags_of(data: bytes) -> dict[str, tuple[frozenset[str], str]] | None:
    """connection name -> (tag set, group) for a component document, or None if it will not parse.

    The group is stripped: the corpus writes `group="group_back_engines "` with a trailing space in
    107 of 168 base and 82 of 149 DLC unit components (MEASURED 2026-11-08), so an unstripped compare
    would call most of vanilla mismatched. Case is NOT folded here -- whether the engine folds it is
    unmeasured, and `check_fitting_compatibility` reports a case-only match as its own finding.
    """
    try:
        root = _merge.parse_bytes(data)
    except etree.XMLSyntaxError:
        # silent-ok: the same un-evaluable sentinel as connections_of; the caller skips.
        return None
    out: dict[str, tuple[frozenset[str], str]] = {}
    for c in root.xpath("//connection[@name]"):
        out[c.get("name")] = (frozenset((c.get("tags") or "").split()), (c.get("group") or "").strip())
    return out


def component_connections(component_file: Path) -> set[str] | None:
    """Connection names on a component, or **None** if the file can't be read.

    None is the caller's established skip sentinel (`conns_for_component` returns
    it for an unresolved component and `check_loadout` bails on it). Returning an
    empty set instead — as this did until 2026-07-27 — reads as "this component
    has zero connections", so a single unparseable component file turned every
    loadout entry pointing at it into a false 'references connection not on
    component' ERROR. Un-evaluable is not the same as empty.
    """
    try:
        root = _merge.parse_file(component_file)
    except (etree.XMLSyntaxError, OSError):
        # silent-ok: the documented un-evaluable sentinel (see docstring above);
        # returning set() instead is the exact bug this function was fixed for.
        return None
    return set(root.xpath("//connection/@name"))


def loadout_targets(loadout_el: etree._Element) -> list[tuple[str, int]]:
    """(connection_name, sourceline) for each loadout entry with a `path` attr."""
    out = []
    for el in loadout_el.xpath(".//*[@path]"):
        path = el.get("path", "")
        conn = path.rsplit("/", 1)[-1]  # strip leading ../ (or any prefix)
        # `..`/`.` are the parent/self (ship root), not a named connection — e.g.
        # <groups> entries use path=".." (vanilla does this 64x). Only a trailing
        # connection NAME is checkable against the component's connections.
        if conn and conn not in ("..", "."):
            out.append((conn, el.sourceline or 0))
    return out


def _xq(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"
