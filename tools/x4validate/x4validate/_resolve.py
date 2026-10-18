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


def build_index(config: _merge.Config, extra_overlays, index_rel: str,
                report=None) -> dict[str, tuple[Path, str]]:
    """name -> (resolution_root, value). Later sources win (mod over base).

    `config.overlays` (the Tier B installed set) is merged in BEFORE
    *extra_overlays* (the mod under test), matching load order — until
    2026-07-28 it was ignored entirely, so `--tier b` ran the file-existence and
    connection checks against a base+DLC-only index while every other check saw
    the merged tree. Both index and payload reads are packed-aware; making only
    the index packed-aware registers a macro whose file then cannot be found,
    turning a missed check into a FALSE 'registered but file missing' error.
    """
    index: dict[str, tuple[Path, str]] = {}
    roots = _Roots(config, extra_overlays, report, index_rel)
    # base + DLC. Read PACKED-AWARE (AUDIT-2026-09-24 MG-3): a DLC that exists only
    # as `ext_*.cat` in the live install (Config.dlc_dirs() includes those) has no
    # loose index, and a `.is_file()`-only read registered nothing from it. Its
    # values carry `extensions\\<dlc>\\`, so they resolve inside that DLC's OWN root
    # — pinning them to `config.reference` pointed at a tree that does not hold them.
    for src in [config.reference] + config.dlc_dirs():
        try:
            root = _read_source(src, index_rel)
        except (etree.XMLSyntaxError, OSError) as exc:
            if report is not None:
                report.skip(f"{index_rel} resolution",
                            f"base/DLC index {src / index_rel} is unreadable ({exc}) — the "
                            "entries it defines were not registered", degraded=True)
            continue
        if root is None:
            continue
        for name, value in _index_entries(root):
            index[name] = roots.target(value, src, config.reference)
    # mod overlays: values are resolved relative to the mod root.
    for ov in list(config.overlays) + list(extra_overlays or []):
        try:
            root = _read_source(ov, index_rel)
        except (etree.XMLSyntaxError, OSError) as exc:
            # DEGRADED, like the base/DLC branch above (AUDIT-2026-09-24 VA-12): the
            # index is incomplete, so every "registered but missing" / connection
            # verdict below is computed against a partial registry. Deduplicated
            # because each check builds this index, and one cause reads as one line.
            if report is not None:
                what = f"{index_rel} resolution"
                why = (f"{ov.name}: index unreadable ({exc}) — macros it registers will "
                       "read as unregistered")
                if not any(s.what == what and s.why == why for s in report.skipped):
                    report.skip(what, why, degraded=True)
            continue
        if root is None:
            continue
        for name, value in _index_entries(root):
            index[name] = roots.target(value, ov, ov)
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
    root, value = index[name]
    p = resolve_path(root, value)
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
    comp_ref = comps[0]
    if comp_ref not in component_index:
        out.append(f"macro '{macro_name}' -> component '{comp_ref}' not registered in index/components.xml")
        return out
    if read_indexed(component_index, comp_ref) is None:
        out.append(f"component '{comp_ref}' registered but file missing: "
                   f"{resolve_path(*component_index[comp_ref])}")
    return out


def connections_of(data: bytes) -> set[str] | None:
    """Connection names in a component document, or None if it will not parse."""
    try:
        root = _merge.parse_bytes(data)
    except etree.XMLSyntaxError:
        # silent-ok: None is this function's whole point — see the docstring on
        # component_connections. The caller reports it via Report.skip.
        return None
    return set(root.xpath("//connection/@name"))


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
