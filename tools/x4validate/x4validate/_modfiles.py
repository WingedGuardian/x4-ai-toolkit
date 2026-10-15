"""Which XML documents a mod (or the overlay set) supplies -- the ENUMERATION half of a build.

WHY THIS MODULE EXISTS (AUDIT-2026-09-24 FR-2). Two functions that decide which documents
the effective store and BaseX `x4eff` contain lived where the engine freshness axis could
not watch them:

* `mod_xml_paths` was `_compat._mod_xml_paths`. `_effective.build_touch_map` calls it to
  decide which vpaths each mod touches -- which is provenance in the store -- but `_compat`
  carries the x4compat CLI, and F69 (`tests/test_engine_sources_carry_no_cli.py`) keeps CLI
  modules out of `_freshness.ENGINE_SOURCES`, because a help-text edit would otherwise mark
  every artifact stale for nothing.
* `overlay_vpaths` was `all_vpaths` in `tools/basex/build-effective.py`, outside the package,
  where no engine hash could see it at all.

An edit to either changes a built artifact for byte-identical inputs, which is exactly what
the engine axis exists to detect. The remedy is F69's own: make the POPULATION right -- lift
them into a module with no CLI and name that module. `_compat._mod_xml_paths` is kept as an
alias, so every existing caller is unchanged.

This module imports no argparse, defines no entry point and prints nothing.
"""
from __future__ import annotations

from pathlib import Path

from x4validate import _cat


def logical_vpath(vpath: str, owner_folders: set[str]) -> str:
    """Map a single-level mod-on-mod patch to the owner's logical document.

    DLC paths and double nesting retain their identity; transitive nesting is
    not engine-qualified. Shared by provenance enumeration and definition lookup.
    """
    parts = vpath.replace("\\", "/").split("/")
    if (len(parts) >= 3 and parts[0].lower() == "extensions"
            and parts[1].lower() in owner_folders and parts[2].lower() != "extensions"):
        return "/".join(parts[2:])
    return vpath


def mod_xml_paths(mod_path: Path) -> dict[str, str]:
    """Return ``{lowercased_vpath: real_vpath}`` for a mod's XML (packed + loose)."""
    out: dict[str, str] = {}
    for vpath in _cat.mod_vfs(mod_path, packed_only=True):  # packed-ok: loose added below
        out[vpath.lower()] = vpath
    for f in mod_path.rglob("*.xml"):
        if f.is_file():
            vpath = f.relative_to(mod_path).as_posix()
            out[vpath.lower()] = vpath
    out.pop("content.xml", None)
    return out


def overlay_vpaths(config, overlays: list[Path], failures: list[str] | None = None
                   ) -> tuple[set[str], set[str], dict[str, str]]:
    """(contested, untouched, base) virtual paths for an `x4eff` build.

    contested = shipped by at least one overlay (so a merge is required)
    untouched = base/DLC only (the base file already IS the effective file)

    The base set comes from `_effective.base_vpaths`, NOT a local `reference.rglob`: the
    rglob form is loose-only and cannot see packed DLC -- MEASURED 2026-08-22, that cost the
    index 119 of 142 mini-DLC documents (BLIND-SPOTS F34).

    An overlay whose catalog cannot be read used to contribute NOTHING with no record (a
    bare `except: pass`). It still contributes its loose files, and the failure is now
    appended to *failures* -- the build reports it -- because a narrowing step must say so
    (AUDIT-2026-09-24 BX-6).

    Behaviour is otherwise what `build-effective.py` did, including counting each overlay's
    own `content.xml` as contested -- except that, since issue #6, paths differing only by
    case are ONE contested vpath (see below).
    """
    from x4validate import _effective  # deferred: _effective imports _compat imports this

    base = _effective.base_vpaths(config, "*.xml")
    # ONE contested vpath per CASE-FOLDED path (issue #6). The engine reads virtual paths
    # case-insensitively (MEASURED: debug.txt I/O paths are lowercase), so two spellings
    # are one file. A set of original spellings made the x4eff build merge and write that
    # document twice: on Windows the second write landed on the first and the counted
    # total exceeded the files on disk. Spelling: the base tree's if it has one, else the
    # first spelling in LOAD ORDER (sorted within one overlay, so it is deterministic).
    chosen: dict[str, str] = {}
    for d in overlays:
        vps = {p.relative_to(d).as_posix() for p in d.rglob("*.xml")}
        try:
            # packed-ok: the loose half is the rglob directly above.
            vps |= {v for v in _cat.mod_vfs(d, packed_only=True)
                    if v.lower().endswith(".xml")}
        except Exception as exc:  # noqa: BLE001 - recorded below, never silent
            if failures is not None:
                failures.append(f"{d.name}: catalog unreadable ({type(exc).__name__}: "
                                f"{exc}) -- only its LOOSE files were enumerated")
        for v in sorted(vps):
            # Prefer the base tree's casing so both sides agree on one spelling.
            chosen.setdefault(v.lower(), base.get(v.lower(), v))
    contested = set(chosen.values())
    untouched = {v for low, v in base.items() if v not in contested}
    return contested, untouched, base
