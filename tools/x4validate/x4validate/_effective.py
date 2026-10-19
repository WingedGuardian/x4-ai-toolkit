r"""x4effective: the "xEdit for X4" — final effective values + per-attribute provenance.

Builds the effective merged tree of base + DLC + all ACTIVE mods (load order) and
extracts, for every entity (ware / macro / job), each attribute's final value AND
the chain of overlays that produced it (which mod won). Results land in a SQLite
store queried by the CLI, by Claude (`sql`), and by the HTML browser (_effhtml).

Design notes:
- Enumeration is by game-root vpath (= mod-relative path). A mod file at
  ``assets/foo_macro.xml`` maps to game vpath ``assets/foo_macro.xml``; a file at
  ``extensions/ego_dlc_x/...`` patches that DLC's vpath. build_effective already
  resolves both base-owned files (base from reference) and mod-owned files (first
  overlay full-overrides when reference has no base), so one call path serves both.
- ``touchers`` per vpath are precomputed once, so each merge is passed only the
  mods that actually carry the file (not all ~90) — same result, ~90x fewer stats.
- Provenance follows the engine's MEASURED load order (_compat.compute_load_order:
  case-insensitive folder order, dependencies in repeated passes; checked against the
  engine's own log by gates/load_order_oracle.py).
"""

from __future__ import annotations

import fnmatch
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath

from lxml import etree

from x4validate import _cat, _compat, _merge, _mutation, _paths, _registry, _freshness, _resolve, _scan, _modfiles
from x4validate._provenance import BASE, Origin, Recorder
from x4validate import __version__


def effective_db() -> Path | None:
    """Where the effective store lives, or None when nothing is configured.

    Resolved on CALL and through `_paths` — the ONE door. This used to be
    `os.environ.get("X4_EFFECTIVE_DB")` evaluated at IMPORT time into a module
    constant that is also an argparse default, which had two consequences:
    a value in `.claude/x4-paths.env` was invisible, and `gates/_env.py` — which
    resolved the same variable through `_paths` — could disagree with this module
    about which store was configured. Two doors to one question is the shape that
    produced F30; it does not get to exist twice.
    """
    p = _paths.path_value("X4_EFFECTIVE_DB")
    if p is not None:
        return p
    reg = _registry.DEFAULT_REGISTRY
    return (reg.parent / "effective.sqlite") if reg else None


#: Backwards-compatible module constant. Prefer `effective_db()`: this is a
#: snapshot taken at import, so it cannot see configuration set afterwards.
DB_PATH: Path | None = effective_db()

SCHEMA_VERSION = 1
_ADVISORY = ("winner reflects the engine's MEASURED load order (case-insensitive folders, "
             "dependencies in passes, game root before profile root); UNMEASURED and "
             "modelled: Steam Workshop mods' placement and dependencies, dependency ids "
             "differing only in case, and a game-root mod that waits a pass while "
             "profile-root mods are present")


def _count_line(shown: int, total: int, noun: str) -> str:
    """Footer count that NEVER hides truncation.

    `--limit` defaults to 200 and the footer used to print a bare "200 ware(s)"
    while the store holds 2,431 — so the capped number read as the total. A
    silent cap is the same class of lie as a silent skip: the output looks like
    an answer about everything when it is an answer about the first N.

    Lives in `_scan` now so `x4diff --top` shares one implementation; kept here
    as the module-local name every call site already uses.
    """
    return _scan.count_line(shown, total, noun)


# --- active mod set + load order ---------------------------------------------

def active_mods(dirs: list[Path] | None = None, dropped: list[str] | None = None,
                config: "_merge.Config | None" = None) -> list[dict]:
    """What the engine loads -- `_registry.mods("active")`, whose docstring has the rule.

    *dropped* receives every mod left out and why; *config* supplies the DLC that
    count as dependency providers (default: the configured reference)."""
    return _registry.mods("active", dirs, dropped, dlc_config=config)


def ordered_overlays(mods: list[dict]) -> list[tuple[dict, Path]]:
    """(mod, path) pairs in engine load order."""
    order = _compat.compute_load_order(mods)
    by_folder = {m["folder"]: (m, Path(m["path"])) for m in mods}
    return [by_folder[f] for f in order if f in by_folder]


# --- vpath enumeration + touch map -------------------------------------------

def build_touch_map(ordered: list[tuple[dict, Path]]) -> dict[str, list[tuple[str, str]]]:
    """lower(LOGICAL vpath) -> [(mod_folder, real_vpath), ...] in load order.

    A mod-on-mod nested patch — a file at ``extensions/<owner>/<rel>`` where
    <owner> is an INSTALLED MOD folder — is registered under the owner's own
    ``<rel>``, because that is the one document the engine builds. Keying it by
    its literal path gave the same logical file two disconnected keys: the
    owner's entities never saw the patcher (wrong values, wrong provenance), and
    the patcher's key minted a phantom duplicate entity at a vpath the game does
    not have. DLC-nested paths (``extensions/ego_dlc_*``) are NOT rewritten —
    those are genuine game vpaths, and ego_dlc folders never appear in *ordered*.

    A DOUBLY-nested ``extensions/<owner>/extensions/<DLC>/<rel>`` (the owner ships a
    file at a DLC vpath) is registered under ``extensions/<DLC>/<rel>`` (F305; the rule
    and its engine evidence are in `_modfiles.logical_vpath`). It used to keep its
    literal key, so the merge of the DLC vpath never saw the patcher and the store
    kept the owner's values: 10 of 10 live files the engine opens. The reason given
    then -- the rewrite "double-applied ebi_m0_vro (it ships BOTH the direct DLC patch
    and the double-nested form)" -- was the toucher list naming a mod TWICE, so its
    bare diff ran twice; `touchers_for` now hands each mod over once.
    """
    folders = {mod["folder"].lower() for mod, _ in ordered}
    touch: dict[str, list[tuple[str, str]]] = {}
    for mod, path in ordered:
        own = mod["folder"].lower()
        for low, real in _compat._mod_xml_paths(path).items():
            parts = low.split("/")
            # A mod nesting under its OWN folder is not a patch on another mod.
            if len(parts) >= 3 and parts[1] != own:
                low = _modfiles.logical_vpath(low, folders)
            touch.setdefault(low, []).append((mod["folder"], real))
    return touch


def _under_assets(low: str) -> bool:
    """True when *low* is an ``assets/`` path of the game root or of a DLC.

    Deliberately exact rather than ``"assets/" in low``: the substring form also
    matches ``libraries/assets/...``, and a filter that is *nearly* the old walk
    is precisely how a documented scope quietly turns into a different one.
    """
    if low.startswith("assets/"):
        return True
    parts = low.split("/")
    return len(parts) >= 3 and parts[0] == "extensions" and parts[2] == "assets"


@lru_cache(maxsize=16)
def _base_vpaths_cached(reference: Path, dlc: tuple[Path, ...],
                        packed: frozenset[str], pattern: str) -> dict[str, str]:
    """Memoized core of :func:`base_vpaths`.

    MEASURED 2026-08-22: one call is **1.72 s** (rglob over 9,138 loose files plus
    two catalog reads), and the inputs cannot change inside a run. Uncached, every
    caller that loops over mods repaid it -- `check_variant_consistency` alone
    spent ~25-35 s per corpus sweep re-deriving a byte-identical answer.

    Keyed on the CONFIG-DERIVED inputs rather than on the Config object, which is
    not hashable. Callers that monkeypatch `_cat.mod_vfs` under a fixed reference
    path must call `base_vpaths.cache_clear()`; every current test uses a unique
    tmp_path, so the key already distinguishes them.
    """
    out: dict[str, str] = {}
    for f in reference.rglob(pattern):  # reference-scope-ok: THE sanctioned walk; packed pass below
        if f.is_file():
            v = f.relative_to(reference).as_posix()
            out[v.lower()] = v
    for src in dlc:
        if src.name.lower() not in packed:
            continue
        for entry in _cat.mod_vfs(src, packed_only=True):  # packed-ok: loose pass is above
            rel = entry.replace("\\", "/")
            if not fnmatch.fnmatch(rel.rsplit("/", 1)[-1].lower(), pattern.lower()):
                continue
            v = f"extensions/{src.name}/{rel}"
            out[v.lower()] = v
    return out


def base_vpaths(config: _merge.Config, pattern: str = "*.xml") -> dict[str, str]:
    r"""lower(vpath) -> vpath for EVERY base+DLC file matching *pattern*, WHOLE tree.

    THE single enumeration of "what the game itself ships". vpaths are game-root
    relative and a DLC keeps its ``extensions/ego_dlc_x/`` prefix, which is the
    spelling ``_merge._nested_target`` already expects.

    Loose THEN packed, and that ordering is the whole point. Six of the eight DLC
    are unpacked under ``reference\``; the two mini-DLC (Hyperion, Envoy) are
    NEVER unpacked and live only in ``ext_*.cat``. A plain ``reference.rglob``
    therefore cannot see them, and every hand-rolled copy of that walk has lost
    them silently:

      _input.py . _migration.py . _effective.py . _xref.py . _similarity.py .
      stage.py . build-effective.py   -- SEVEN occurrences, MEASURED.

    The last one (2026-08-22) cost BaseX ``x4eff`` **119 of 142 mini-DLC
    documents (84%)**, and the coverage check that gates negative claims over
    that index reported COMPLETE throughout, because a vpath never attempted
    cannot appear in a failure list. See docs/BLIND-SPOTS.md F34 and F35.

    Prose did not stop occurrences two through seven -- ``tests/test_no_loose_only_reference_walk.py``
    is the part that is expected to.
    """
    # Returns the CACHED dict; callers must not mutate it. `reference_vpaths`
    # builds a new dict, and every other caller only reads.
    return _base_vpaths_cached(config.reference, tuple(config.dlc_dirs()),
                               frozenset(config.packed_dlc_names()), pattern)


def reference_vpaths(config: _merge.Config, pattern: str) -> dict[str, str]:
    """base+DLC files matching *pattern*, RESTRICTED TO ``assets/``.

    The restriction is F3's documented scope decision -- galaxy-map macros
    (``maps/xu_ep2_universe/*``) and character macros
    (``libraries/character_macros.xml``) are out of scope for the store, not
    missing from it. MEASURED 2026-08-22 on the two mini-DLC: of 72 macro names,
    **40 are in scope and 35 sit in those two excluded classes**.

    It is written as an explicit FILTER over :func:`base_vpaths` rather than as a
    walk that happens to start at ``assets/``, so the scope is a decision someone
    can find and change -- and so there is exactly one enumeration underneath.
    Set-equality with the pre-refactor walk is pinned by
    ``tests/test_prop_depth.py``.
    """
    return {low: v for low, v in base_vpaths(config, pattern).items() if _under_assets(low)}


#: Test hook: drop the memo when a fixture changes what a fixed path resolves to.
base_vpaths.cache_clear = _base_vpaths_cached.cache_clear  # type: ignore[attr-defined]


class EmptyReference(ValueError):
    """The reference tree a builder was pointed at holds no base-game documents.

    A ValueError on purpose: every builder CLI already maps ValueError to rc 2,
    the toolkit's "cannot run / not set up" code (`_paths.refuses_unconfigured`,
    BLIND-SPOTS F39/F46), which a caller must be able to tell apart from rc 1
    ("findings") and rc 3 ("partial"). Distinct from `_paths.Unconfigured`, which
    means "you never told me where it is"; this is "you told me, and nothing is
    there" -- `Config.__post_init__` deliberately leaves that case alone, so the
    BUILDERS have to ask it themselves.
    """


def require_base_documents(config: _merge.Config, what: str) -> tuple[int, int]:
    """Refuse unless *config*'s reference root exists AND holds BASE-GAME XML.

    Issue #10. `base_vpaths` is an rglob, and an rglob over a missing or empty root
    returns nothing without complaint, so a build over the wrong path (a second
    worktree resolving the reference relative to itself, a typo) merged zero files,
    wrote an EMPTY artifact over the good one and exited 0. Call this BEFORE writing
    anything. Returns ``(base, dlc)`` document counts so a caller can report its
    denominator.

    Two conditions, because either alone can pass: the root must exist, and at least
    one document must lie OUTSIDE ``extensions/`` (compared case-insensitively). DLC
    documents never count: for the CONFIGURED reference `Config.dlc_dirs()` adds every
    DLC the install holds packed, so an existing-but-empty reference on a machine with
    a real game enumerated those DLC and passed (release review, MEASURED: empty
    reference + one packed DLC -> n=1, not refused).
    """
    ref = config.reference
    how = ("Set $X4_REFERENCE (or x4-paths.env) to the unpacked base-game tree, or "
           "pass --reference; `x4validate --paths` shows what resolved.")
    if not ref.is_dir():
        raise EmptyReference(
            f"refusing to {what}: the reference tree {ref} does not exist, so it would "
            f"model a game with no base content and report success. {how}")
    # Keys of base_vpaths are already lower-cased, so the prefix test is case-insensitive.
    found = base_vpaths(config, "*.xml")
    n_dlc = sum(1 for low in found if low.startswith("extensions/"))
    n_base = len(found) - n_dlc
    if n_base == 0:
        raise EmptyReference(
            f"refusing to {what}: the reference tree {ref} exists but contains no "
            f"base-game XML documents (0 outside extensions/; {n_dlc} DLC document(s) "
            f"do not count), so the result would model no base game. {how}")
    return n_base, n_dlc


def base_has(config: _merge.Config, vpath: str, owner: str | None = None) -> bool:
    r"""Does base+DLC ship *vpath*? Handles the DLC prefix so callers need not.

    THE TRAP THIS EXISTS TO REMOVE. :func:`base_vpaths` keys base-game files
    BARE (``libraries/wares.xml``) but keeps a DLC's ``extensions/ego_dlc_x/``
    prefix, because that is the spelling ``_merge._nested_target`` expects. Both
    spellings are correct; what is not correct is a caller that knows only one.

    MEASURED 2026-08-25: an audit looked up 58 DLC targets with the prefix
    STRIPPED and reported **all 58 GONE** when every one of them exists. Nothing
    was wrong with the data or with `base_vpaths`; the lookup asked the wrong
    question and got a confident, uniform, entirely false answer -- the shape
    CLAUDE.md #22 is about.

    No shipped call site had this bug (all three ITERATE the mapping rather than
    look up a key), so this is PREVENTIVE. It is worth the twelve lines because
    the failure mode is a wall of false negatives that looks like a real finding,
    and because ad-hoc analysis scripts are written far more often than call
    sites are.

    *owner*, when given, is a DLC folder name (``ego_dlc_terran``) to try as a
    prefix as well -- so a caller holding a vpath relative to a DLC root can ask
    about it without hand-assembling the prefix.
    """
    low = str(vpath).replace("\\", "/").lstrip("/").lower()
    # Keyed to THIS vpath's suffix, never "*": the enumeration is memoized per
    # pattern, and a bare "*" would walk the whole 60 GB base+DLC tree and cache
    # it -- paying a tree walk to answer one membership question.
    suffix = PurePosixPath(low).suffix
    known = base_vpaths(config, f"*{suffix}" if suffix else "*")
    if low in known:
        return True
    # A caller may hold either spelling; try the other rather than judge them.
    if low.startswith("extensions/"):
        tail = low.split("/", 2)
        if len(tail) == 3 and tail[2] in known:
            return True
    if owner and f"extensions/{owner.lower()}/{low}" in known:
        return True
    # THE SUFFIX FALLBACK ONLY APPLIES TO A MULTI-SEGMENT vpath. Without the guard
    # below a BARE FILENAME matched any DLC path ending in it, which is a false
    # POSITIVE in a helper whose entire docstring is about preventing false
    # NEGATIVES. MEASURED on the live tree:
    #
    #   base_has("wares.xml")            -> True   (matched extensions/ego_dlc_*/
    #   base_has("macros.xml")           -> True    libraries/wares.xml, and so on)
    #   base_has("components.xml")       -> True
    #   base_has("libraries/wares.xml")  -> True   (correct, and unaffected)
    #
    # None of those bare names is a vpath in ANY tree. The documented consumer is
    # the plain-vs-nested decision (gotcha #6), where a wrong True points at the
    # PLAIN form and yields an inert patch. Both shipped call sites pass full
    # vpaths, so the live exposure was `x4effective dump <bare-name> --chain` and
    # ad-hoc scripts -- which the docstring itself says is why this function exists.
    #
    # The fallback stays for the case it was written for: a caller holding
    # `libraries/wares.xml` when the tree has `extensions/<dlc>/libraries/wares.xml`.
    # That needs at least one separator, so requiring one costs nothing.
    if "/" not in low:
        return False
    return any(k.endswith("/" + low) and k.startswith("extensions/") for k in known)


def _defines_macro(folder_to_path: dict[str, Path], touchers: list[tuple[str, str]]) -> bool:
    """True if any toucher's copy of the file actually contains a <macro name=>.

    Cheap byte-level test on files the touch map already located — no XML parse,
    no extra directory walk.
    """
    unreadable = False
    for folder, real in touchers:
        base = folder_to_path.get(folder)
        if base is None:
            continue
        try:
            data = _cat.read_path(base, real)
        except OSError:
            # silent-ok: FAIL OPEN. This helper only decides whether to LOOK at a
            # file; "I could not read it" is not "it holds no macros". Admitting
            # it hands the file to the merge, which parses it for real and reports
            # any failure through `skipped` — so an unreadable file surfaces there
            # instead of vanishing here. Swallowing to `False` would be the exact
            # silent-narrowing defect this change exists to remove.
            unreadable = True
            continue
        if data and b"<macro" in data:
            return True
    return unreadable


def macro_vpaths(config: _merge.Config, touch: dict[str, list[tuple[str, str]]],
                 folder_to_path: dict[str, Path] | None = None) -> dict[str, str]:
    """All effective macro-file vpaths: reference/DLC + mod-provided.

    Admission is by FILENAME (`*_macro.xml`, the cheap fast path) and, when
    *folder_to_path* is supplied, additionally by CONTENT for other
    ``assets/**.xml``. VRO ships macro files that simply are not named
    `*_macro.xml` (`bullet_gen_turret_l_rotor`, `bullet_ter_m_graviton`) and the
    engine loads them — both are in the effective `index/macros.xml` — so a
    filename rule silently loses real, live weapon balance data.

    The ``assets/`` restriction is deliberate and stays: galaxy-map and character
    macros are documented out of scope (docs/BLIND-SPOTS.md F3), not a defect.
    """
    out = reference_vpaths(config, "*_macro.xml")
    for low, touchers in touch.items():
        if not touchers or "assets/" not in low or not low.endswith(".xml"):
            continue
        if low.endswith("_macro.xml"):
            out.setdefault(low, touchers[0][1])
        elif folder_to_path is not None and _defines_macro(folder_to_path, touchers):
            out.setdefault(low, touchers[0][1])
    return out


def component_vpaths(config: _merge.Config, touch: dict[str, list[tuple[str, str]]]) -> dict[str, str]:
    """lower(vpath) -> vpath for every component file: all `assets/**.xml` that is
    not a `*_macro.xml`, from reference + DLC + mod-provided.

    **Not enumerated from `index/components.xml`.** That was the first design and
    it silently lost **487 of 3,959 components (12%), 34 of them vanilla** --
    `ship_xen_xl_mothership_01`, `engine_ter_s_01` and friends simply are not in
    the index. Which is precisely KNOWLEDGEBASE's "the index is NOT the definition
    set", written the same day and then relied on anyway.

    Excluding `*_macro.xml` is safe and measured: **0 files** anywhere define both
    a macro and a component, so the two enumerations cannot double-count.
    Components in `libraries/character_components.xml` stay out, consistent with
    character macros being out.
    """
    out: dict[str, str] = {}
    for low, real in reference_vpaths(config, "*.xml").items():
        if not low.endswith("_macro.xml"):
            out[low] = real
    for low, ts in touch.items():
        # `"assets/" IN low`, not startswith: a mod may ship content nested under
        # a DLC (`extensions/ego_dlc_timelines/assets/units/...`), and startswith
        # silently dropped 12 such components. `macro_vpaths` already uses the
        # substring form -- two enumerations of the same tree must agree.
        if (ts and "assets/" in low and low.endswith(".xml")
                and not low.endswith("_macro.xml")):
            out.setdefault(low, ts[0][1])
    return out


def touchers_for(vpath: str, touch: dict[str, list[tuple[str, str]]],
                 folder_to_path: dict[str, Path]) -> list[Path]:
    """The overlay ROOTS that touch *vpath*, in load order, each ONCE.

    A mod shipping two files that map to one document (a bare DLC patch AND a
    doubly-nested one, F305) appears twice in the touch map. The merge takes overlay
    ROOTS and finds each of a root's files itself, so a root listed twice had its
    files applied twice."""
    out: list[Path] = []
    for f, _ in touch.get(vpath.lower(), []):
        p = folder_to_path.get(f)
        if p is not None and p not in out:
            out.append(p)
    return out


# --- entity model -------------------------------------------------------------

@dataclass
class Entity:
    kind: str
    name: str
    klass: str
    vpath: str
    origin: str
    chain: list[Origin]
    attrs: list[tuple[str, str, float | None, list[Origin]]] = field(default_factory=list)


def _num(val: str) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        # silent-ok: "this attribute is not numeric" is the ANSWER here, not a
        # failure to look. Callers keep the original string either way.
        return None


def _chain_json(chain: list[Origin], default: Origin) -> str | None:
    """JSON list of the chain; None when the value is pure default (base) — saves space."""
    if len(chain) == 1 and chain[0] == default:
        return None
    return json.dumps([[o.source, o.op, o.line] for o in chain])


def _child_ident(el: etree._Element) -> str | None:
    for a in ("id", "name", "method", "ware", "ref", "type"):
        v = el.get(a)
        if v is not None:
            return v
    return None


#: Recursion guard for `flatten_with_prov`. MEASURED 2026-08-12 over the whole
#: reference tree: the deepest <properties> subtree is 5 (character macros; ship
#: macros stop at 2), so 8 is headroom, not a limit anyone should hit. If it IS
#: hit the path is recorded in `truncated_props` and reported by the build --
#: never silently dropped, which is the defect class this whole change exists to
#: kill (see docs/BLIND-SPOTS.md).
#:
#: ⚠ THAT JUSTIFICATION IS STALE FOR THE POPULATION THIS NOW WALKS, and the value is
#: deliberately UNCHANGED until it can be re-derived. The 08-12 census predates F34
#: widening `base_vpaths`, and the build reports 10 subtrees hitting the guard --
#: e.g. `properties.system.planets.planet.moons.moon[...].shaderparams.shaderparam`.
#: MEASURED 2026-09-06 trying to re-derive it: the installed mod corpus,
#: packed-inclusive, tops out at depth 5 (125 mods / 4,618 files parsed) and base+DLC
#: map documents at 4 (177 docs) -- so NOTHING reachable from outside the build
#: reproduces one of those 10. The records named a property path and never a
#: document, so they could not be followed either; they now carry the vpath, which is
#: what makes the next build's report actionable.
#:
#: RAISE IT FROM THAT EVIDENCE, not from this comment. A guard whose true ceiling is
#: unmeasured is the guess this register exists to refuse -- and raising it also adds
#: store rows, so it belongs with a per-item before/after rather than on its own.
MAX_PROP_DEPTH = 8

#: Property paths abandoned at MAX_PROP_DEPTH during the last build.
truncated_props: list[str] = []


def _walk_props(scope: etree._Element, rec: Recorder, prefix: str, depth: int,
                rows: list[tuple[str, str, float | None, list[Origin]]],
                no_recurse: frozenset | tuple = (), where: str = "") -> None:
    """Append `<prefix><tag>[ident].attr` rows for *scope*'s children, recursively.

    The depth-1 key shape is byte-identical to the pre-2026-08-12 implementation
    (that is the golden-vector regression guard); deeper levels simply extend the
    same grammar with another dotted segment. Provenance is per-attribute at every
    depth: `Recorder.attr_chain` is keyed by (element, attr) and falls back to the
    nearest recorded ancestor, so a grandchild an overlay touched carries that
    overlay, and one it did not carries the inherited chain.
    """
    tag_counts: dict[str, int] = {}
    for child in scope:
        if isinstance(child.tag, str):
            tag_counts[child.tag] = tag_counts.get(child.tag, 0) + 1
    seen: dict[str, int] = {}
    #: (tag, ident) -> how many siblings have already claimed that exact bracket.
    #: F33: the bracket discriminator is NOT unique among siblings, so several
    #: collapsed onto one key and a `where prop=?` + `fetchone()` returned an
    #: arbitrary one of them. MEASURED on 582,107 attr rows: 627 duplicate
    #: (entity_id, prop) groups, 1,071 extra rows (0.18%), and 201 of those groups
    #: held genuinely DIFFERENT values under one key. Worst case:
    #: faction/player `licences.licence[generaluseequipment].factions` -- 8 rows,
    #: 8 distinct values.
    ident_seen: dict[tuple[str, str], int] = {}
    for child in scope:
        if not isinstance(child.tag, str):
            continue
        tag = child.tag
        if tag_counts[tag] > 1:
            ident = _child_ident(child)
            if ident is None:
                # Zero-based positional index counting ONLY the ident-less
                # siblings -- preserved exactly from the original.
                seen[tag] = seen.get(tag, -1) + 1
                ident = str(seen[tag])
            # Disambiguate COLLISION-ONLY: the first claimant of a bracket keeps
            # the original key untouched, so 99.8% of rows are byte-identical and
            # the golden-vector guard still means something. Only the 2nd, 3rd...
            # gain `#1`, `#2` -- the same grammar `connection[name#n]` already
            # uses below, rather than a second invented one. Indexing EVERY key
            # positionally would have rewritten 358,415 rows (61.6%) to fix 0.18%.
            n = ident_seen.get((tag, ident), -1) + 1
            ident_seen[(tag, ident)] = n
            key = f"{tag}[{ident}]" if n == 0 else f"{tag}[{ident}#{n}]"
        else:
            key = tag
        full = f"{prefix}{key}"
        for attr, val in child.attrib.items():
            rows.append((f"{full}.{attr}", val, _num(val), rec.attr_chain(child, attr)))
        if len(child) and child not in no_recurse:
            if depth >= MAX_PROP_DEPTH:
                # NAME THE DOCUMENT. Without the vpath this reports a property path
                # and no way to find it -- MEASURED 2026-09-06: the build reported
                # 10 truncations while a packed-inclusive sweep of the mod corpus
                # (deepest 5) and of base+DLC maps (deepest 4) could not reproduce
                # one, so the constant's own justification could not be re-derived
                # from outside the build. A narrowing step must announce WHERE.
                truncated_props.append(f"{where}: {full}" if where else full)
                continue
            _walk_props(child, rec, f"{full}.", depth + 1, rows, no_recurse, where)


def flatten_with_prov(el: etree._Element, rec: Recorder,
                      child_scope: etree._Element | None = None,
                      no_recurse: frozenset | tuple = (),
                      where: str = "",
                      ) -> list[tuple[str, str, float | None, list[Origin]]]:
    """Flatten *el*'s own attrs (``@attr``) + its children at EVERY depth
    (``tag.attr``, ``tag[ident].attr``, ``tag.child.attr``, ...) to
    (prop, value, num, chain).

    Until 2026-08-12 this walked exactly one level of children, which made the
    entire flight model invisible -- `physics/drag` (8 axes), `physics/inertia`,
    `jerk/*`, `steeringcurve/point` -- and dropped ware recipes
    (`production/primary/ware`). MEASURED over 339 vanilla ship macros: 4,094
    attribute occurrences indexed vs 9,197 invisible. The merged tree was always
    complete; only this projection was lossy.

    *child_scope* limits which children are walked (e.g. a macro's <properties>);
    default = direct children of *el*.

    *no_recurse* names child elements whose OWN attrs are still emitted but whose
    subtree is not descended into. `extract_macros` walks a macro twice — once for
    the macro itself and once scoped to <properties> — so without this the
    recursion re-emitted every property a second time under a `properties.` prefix
    (MEASURED: 67,333 duplicate rows, 23.1% of the store, before this guard)."""
    rows: list[tuple[str, str, float | None, list[Origin]]] = []
    for attr, val in el.attrib.items():
        rows.append((f"@{attr}", val, _num(val), rec.attr_chain(el, attr)))
    scope = child_scope if child_scope is not None else el
    _walk_props(scope, rec, "", 1, rows, no_recurse, where)
    return rows


def extract_macros(tree: etree._Element, vpath: str, rec: Recorder) -> list[Entity]:
    out: list[Entity] = []
    macros = tree.iter("macro") if tree.tag != "macro" else [tree]
    for m in macros:
        name = m.get("name")
        if not name:
            continue
        props = m.find("properties")
        # @name/@class + top-level (component, etc.). <properties> is walked by the
        # scoped call below, so exclude its subtree here or every property lands
        # twice (once as `x.y`, once as `properties.x.y`).
        rows = flatten_with_prov(m, rec, no_recurse=(props,) if props is not None else (),
                                 where=vpath)
        if props is not None:
            rows += flatten_with_prov(props, rec, child_scope=props, where=vpath)
        out.append(Entity("macro", name, m.get("class", ""), vpath,
                          rec.winner(m).source, rec.elem_chain(m), rows))
    return out


def extract_components(tree: etree._Element, vpath: str, rec: Recorder) -> list[Entity]:
    """Component identity + its connection SLOTS. Deliberately not the geometry.

    A component is what a macro's ``<connections><connection><macro ref=...>``
    points at: the slot a cockpit, turret, shield or engine actually occupies.
    Macro-side connections became visible on 2026-08-12 (0 -> 19,994 rows); this
    is the other half of that join, and together they answer "what is installed
    in this slot" without hand-parsing files.

    SCOPE: identity + ``connections/connection`` only. ``<layers>`` and
    ``<source>`` are 3D data -- MEASURED, flattening components wholesale yields
    **33,131,780 attribute rows, 148x the entire store**, so the naive design was
    not merely expensive but impossible. This one measures ~204k rows (+91%).
    Keyed by ``@name`` (components have no ``@id``).
    """
    out: list[Entity] = []
    for comp in tree.iter("component"):
        name = comp.get("name")
        if not name:
            continue
        rows: list[tuple[str, str, float | None, list[Origin]]] = []
        for attr, val in comp.attrib.items():
            rows.append((f"@{attr}", val, _num(val), rec.attr_chain(comp, attr)))
        conns = comp.find("connections")
        if conns is not None:
            seen: dict[str, int] = {}
            for conn in conns.findall("connection"):
                cname = conn.get("name")
                if cname is None:
                    continue
                # 11 of 5,011 components repeat a connection name. Two slots must
                # never collapse into one row -- that is the silent-narrowing
                # defect this whole effort exists to remove -- so a repeat takes
                # an explicit ordinal suffix rather than overwriting.
                n = seen.get(cname, 0)
                seen[cname] = n + 1
                key = f"connection[{cname}]" if n == 0 else f"connection[{cname}#{n}]"
                for attr, val in conn.attrib.items():
                    if attr == "name":
                        continue
                    rows.append((f"{key}.{attr}", val, _num(val),
                                 rec.attr_chain(conn, attr)))
        out.append(Entity("component", name, comp.get("class", ""), vpath,
                          rec.winner(comp).source, rec.elem_chain(comp), rows))
    return out


#: kind -> (vpath, child tag, class attr, KEY attr). Measured 2026-08-12 against
#: reference\: every entry verified 100% keyed on the attribute named here, every
#: registry verified to merge, and each is patched by 4-28 installed mods.
#: `god`, `parameters`, `loadoutrules` and `camerasettings` are deliberately absent
#: -- they are structured documents, not flat id-keyed registries, and need a
#: different entity model.
LIBRARY_REGISTRIES: dict[str, tuple[str, str, str, str]] = {
    "ware":        ("libraries/wares.xml", "ware", "group", "id"),
    "job":         ("libraries/jobs.xml", "job", "category", "id"),
    "ship":        ("libraries/ships.xml", "ship", "group", "id"),
    "shipgroup":   ("libraries/shipgroups.xml", "group", "tags", "name"),
    "loadout":     ("libraries/loadouts.xml", "loadout", "name", "id"),
    "station":     ("libraries/stations.xml", "station", "type", "id"),
    "stationgroup": ("libraries/stationgroups.xml", "group", "tags", "name"),
    "module":      ("libraries/modules.xml", "module", "group", "id"),
    # MEASURED 2026-08-21 over the EFFECTIVE tree (base + 8 DLC incl. both mini-DLC +
    # every installed mod): 146 groups, and every one is a bare <group name="...">
    # carrying no second attribute -- so there is no distinct class to record. Reusing
    # the key as the class follows the existing `mapdataset` row rather than inventing a
    # meaning. Added because the engine caught a bad <module group=> reference that
    # x4validate was structurally blind to (cpsdo_faction, 43 FactoryGenerator errors).
    "modulegroup": ("libraries/modulegroups.xml", "group", "name", "name"),
    "plan":        ("libraries/constructionplans.xml", "plan", "name", "id"),
    "basket":      ("libraries/baskets.xml", "basket", "name", "id"),
    "people":      ("libraries/people.xml", "people", "name", "id"),
    "mapdataset":  ("libraries/mapdefaults.xml", "dataset", "macro", "macro"),
    "sound":       ("libraries/sound_library.xml", "sound", "name", "id"),
    "icon":        ("libraries/icons.xml", "icon", "texture", "name"),
    "gfxeffect":   ("libraries/effects.xml", "effect", "type", "name"),
    "roomgroup":   ("libraries/roomgroups.xml", "group", "tags", "name"),
    "room":        ("libraries/rooms.xml", "room", "type", "id"),
    "character":   ("libraries/characters.xml", "character", "race", "id"),
    "region":      ("libraries/region_definitions.xml", "region", "density", "name"),
    "faction":     ("libraries/factions.xml", "faction", "primaryrace", "id"),
}


def _extract_registry(tree: etree._Element, kind: str, child_tag: str,
                      klass_attr: str, vpath: str, rec: Recorder,
                      key_attr: str = "id") -> list[Entity]:
    """Entities from a flat id-keyed registry file.

    *key_attr* exists because this used to hardcode ``el.get("id")`` and skip
    silently when absent. MEASURED: 7 of 18 registries key by ``@name`` and one by
    ``@macro``, so hardcoding would have indexed **zero entities while reporting
    success** for seven of them -- a confident empty answer downstream, with
    nothing anywhere saying the key was wrong.

    Raises when children exist but none are keyed, because that is a
    misconfiguration and not data. A file with no children at all is data, and
    returns empty quietly.
    """
    # ⚠ WHAT THIS GUARD CANNOT SEE. `children` is DIRECT children only and the raise
    # below requires it non-empty, so it catches a wrong `key_attr` -- the case it was
    # written from -- and is structurally blind to a wrong `child_tag` or a nested
    # wrapper: both yield 0 children, 0 entities and a silent success. LATENT, since
    # triggering it needs a future format change; the guard for it is the sibling
    # count returned below, which the caller reports.
    out: list[Entity] = []
    children = tree.findall(child_tag)
    for el in children:
        name = el.get(key_attr)
        if not name:
            continue
        out.append(Entity(kind, name, el.get(klass_attr, ""), vpath,
                          rec.winner(el).source, rec.elem_chain(el),
                          flatten_with_prov(el, rec, where=vpath)))
    if children and not out:
        raise ValueError(
            f"{kind}: {vpath} has {len(children)} <{child_tag}> element(s) but the "
            f"registry yielded no entities — @{key_attr!r} matches none of them. "
            f"Indexing nothing while reporting success is the defect this guard exists "
            f"to catch; fix the key attribute in LIBRARY_REGISTRIES.")
    return out


# --- build --------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE mods(folder TEXT PRIMARY KEY, mod_id TEXT, name TEXT, version TEXT,
                  rank INTEGER, enabled INTEGER, packed INTEGER);
CREATE TABLE entities(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, klass TEXT,
                      vpath TEXT, origin TEXT, chain TEXT);
CREATE TABLE attrs(entity_id INTEGER REFERENCES entities(id), prop TEXT,
                   value TEXT, value_num REAL, origin TEXT, chain TEXT);
CREATE TABLE removed(vpath TEXT, node_path TEXT, source TEXT, op_line INTEGER);
CREATE INDEX idx_ent_kind_klass ON entities(kind, klass);
CREATE INDEX idx_ent_name ON entities(name);
CREATE INDEX idx_attr_entity ON attrs(entity_id);
CREATE INDEX idx_attr_prop ON attrs(prop);
CREATE INDEX idx_attr_origin ON attrs(origin);
"""


def _merge_one(vpath: str, config: _merge.Config,
               overlays: list[Path]) -> tuple[etree._Element | None, Recorder, list[str]]:
    """Merge one vpath, and hand back the WORK NOT DONE alongside the tree.

    `MergeResult.skipped`'s own docstring states the contract: *"overlays that
    could not be parsed and were left out of the tree. Without this channel a
    malformed overlay is indistinguishable from an absent one, and the resulting
    tree looks complete when it is not."* `_check.py` honours it in three places.
    This function returned `(res.tree, rec)` and dropped it on the floor.

    ⚠ AND `build`'s `except etree.LxmlError` COULD NOT COVER FOR IT: `overlay_root`
    catches `XMLSyntaxError` internally, so no exception ever escapes for an
    OVERLAY file -- that handler can only fire for the base file's `parse_file`.
    So an unreadable mod file left the store recording `origin='base'` -- "no
    override, still vanilla" -- in the identical grammar it uses for a verified
    base value. MEASURED: 11 unreadable files in the live corpus reach this."""
    rec = Recorder()
    res = _merge.build_effective(vpath, config, extra_overlays=overlays, recorder=rec)
    return res.tree, rec, res.skipped


class _SkipCount:
    """Two DIFFERENT losses, counted separately on purpose.

    `n` / `add`      -- the whole vpath could not be merged, so it contributes NO
                        entities. The store is missing rows.
    `dropped` / `add_overlay` -- the tree BUILT, but an overlay was left out of
                        it. The store has rows, and some of them are WRONG: an
                        override that could not be read reads as `origin='base'`.

    Summing these into one number would be its own narrowing step -- "absent"
    and "present but unsound" are not the same defect, and only the second one
    produces a confident wrong answer."""

    def __init__(self):
        self.n = 0
        self.samples: list[str] = []
        self.dropped = 0
        self.dropped_samples: list[str] = []

    def add(self, vpath: str, exc: Exception):
        self.n += 1
        if len(self.samples) < 10:
            self.samples.append(f"{vpath}: {exc}")

    def add_overlay(self, vpath: str, whys: list[str]):
        for why in whys:
            self.dropped += 1
            if len(self.dropped_samples) < 10:
                self.dropped_samples.append(f"{vpath}: {why}")


#: The kinds `build` knows how to extract. Kept beside the extractors below so
#: adding one without listing it here fails loudly rather than silently.
#: Derived, never re-typed: every flat registry, plus the two per-file kinds.
BUILDABLE_KINDS = tuple(LIBRARY_REGISTRIES) + ("macro", "component")


def build(config: _merge.Config | None = None, db_path: Path | None = None,
          dirs: list[Path] | None = None, kinds: tuple[str, ...] = BUILDABLE_KINDS,
          progress=lambda s: None) -> Path:
    config = config or _merge.Config()
    # Reject an unknown kind instead of quietly building nothing. `--kinds
    # shieldgenerator` (a *klass*, not a kind) used to write an empty store and
    # exit 0, so every later query answered from an empty database with nothing
    # ever saying the name was wrong. The read side already refuses this — `ls`
    # prints "unknown kind ... stored kinds are: ..." — and the build side must
    # agree, because a false clean here poisons everything downstream.
    unknown = [k for k in kinds if k not in BUILDABLE_KINDS]
    if unknown:
        raise ValueError(
            f"unknown kind(s) {', '.join(sorted(unknown))} — buildable kinds are "
            f"{', '.join(sorted(BUILDABLE_KINDS))}. (Values like 'shieldgenerator' "
            "are entity CLASSES; filter those at query time, e.g. "
            "`x4effective attr macro <prop> --class shieldgenerator`.)"
        )
    db_path = db_path or _registry.require(
        effective_db(), "the effective-store location",
        "set X4_EFFECTIVE_DB or X4_MODS (or X4_REGISTRY), or pass --db")
    # Issue #10: BEFORE anything is merged or written, so a refused build leaves the
    # existing store exactly as it was.
    n_base, n_dlc = require_base_documents(config, "build the effective store")
    progress(f"reference: {n_base} base + {n_dlc} DLC XML document(s) enumerated")
    not_loaded: list[str] = []
    mods = active_mods(dirs, not_loaded, config)
    for msg in not_loaded:
        progress(f"NOT in the store (the engine does not load it): {msg}")
    ordered = ordered_overlays(mods)
    # AFTER ordering: the order's own records ride on `mods` (release review, finding 3).
    model = _registry.model_note(mods)
    if model:
        progress(f"load-order model: {model}")
    folder_to_path = {m["folder"]: p for m, p in ordered}
    overlay_paths = [p for _, p in ordered]
    order_rank = {m["folder"]: i for i, (m, _) in enumerate(ordered)}
    touch = build_touch_map(ordered)
    progress(f"active mods: {len(mods)}; load order computed")

    entities: list[Entity] = []
    removed: list[tuple[str, str, str, int]] = []
    skipped = _SkipCount()
    # Module-level and never reset until now, so two builds in one process reported
    # the first one's truncations again. Only the tests cleared it.
    truncated_props.clear()

    def collect_removed(vpath, rec):
        for path, o in rec.removed:
            removed.append((vpath, path, o.source, o.line))

    # library registries (single union-merged files)
    for kind in kinds:
        if kind in LIBRARY_REGISTRIES:
            vpath, child, klass_attr, key_attr = LIBRARY_REGISTRIES[kind]
            ov = touchers_for(vpath, touch, folder_to_path)
            try:
                tree, rec, dropped = _merge_one(vpath, config, ov)
            except etree.LxmlError as exc:
                skipped.add(vpath, exc)
                continue
            skipped.add_overlay(vpath, dropped)
            if tree is not None:
                entities += _extract_registry(tree, kind, child, klass_attr, vpath,
                                              rec, key_attr=key_attr)
                collect_removed(vpath, rec)
            # A COUNT NEEDS ITS DENOMINATOR. `ls region` answered a confident "160"
            # over `libraries/region_definitions.xml`, which is
            # Counter({"region": 160, "alias": 73}) -- 233 elements, 160 indexed, and
            # nothing anywhere said the other 73 were a different tag. That is a
            # numerator quoted as a whole, which is the shape this file exists to
            # refuse. Reported per registry file rather than per kind, because the
            # ratio is a property of the FILE.
            if tree is not None:
                _kept = len(tree.findall(child))
                _other = len([e for e in tree if isinstance(e.tag, str)]) - _kept
                if _other:
                    progress(f"  {vpath}: {_kept} <{child}> of {_kept + _other} "
                             f"element(s); {_other} are not <{child}> and are NOT indexed")
            progress(f"{kind}s: {sum(1 for e in entities if e.kind == kind)}")

    # macros (per-file)
    if "macro" in kinds:
        mvpaths = macro_vpaths(config, touch, folder_to_path)
        progress(f"macro files to merge: {len(mvpaths)}")
        n = 0
        for low, vpath in sorted(mvpaths.items()):
            # Look up touchers by the map KEY, not the real path: for a nested
            # patch normalized onto its owner's rel, the real path no longer
            # equals the key, and a real-path lookup would silently hand the
            # merge an empty overlay list.
            ov = touchers_for(low, touch, folder_to_path)
            try:
                tree, rec, dropped = _merge_one(vpath, config, ov)
            except etree.LxmlError as exc:
                skipped.add(vpath, exc)
                n += 1
                continue
            skipped.add_overlay(vpath, dropped)
            if tree is not None:
                entities += extract_macros(tree, vpath, rec)
                if rec.removed:
                    collect_removed(vpath, rec)
            n += 1
            if n % 500 == 0:
                progress(f"  merged {n}/{len(mvpaths)} macro files, {len(entities)} entities")

    # components (per-file, enumerated by `component_vpaths` from the asset files --
    # NOT from index/components.xml, which misses 12% of them; see its docstring)
    if "component" in kinds:
        cvpaths = component_vpaths(config, touch)
        progress(f"component files to merge: {len(cvpaths)}")
        n = 0
        for low, vpath in sorted(cvpaths.items()):
            ov = touchers_for(low, touch, folder_to_path)
            try:
                tree, rec, dropped = _merge_one(vpath, config, ov)
            except etree.LxmlError as exc:
                skipped.add(vpath, exc)
                n += 1
                continue
            skipped.add_overlay(vpath, dropped)
            if tree is not None:
                entities += extract_components(tree, vpath, rec)
                if rec.removed:
                    collect_removed(vpath, rec)
            n += 1
            if n % 1000 == 0:
                progress(f"  merged {n}/{len(cvpaths)} component files, "
                         f"{sum(1 for e in entities if e.kind == 'component')} components")
        progress(f"components: {sum(1 for e in entities if e.kind == 'component')}")

    if skipped.n:
        progress(f"skipped {skipped.n} unparseable file(s); e.g. {skipped.samples[0]}")
    if skipped.dropped:
        # NOT the same as the line above, and the wording has to keep them apart:
        # those files are ABSENT from the store, these are PRESENT AND WRONG.
        progress(f"WARNING: {skipped.dropped} overlay(s) could not be read and were "
                 f"left OUT of an otherwise-complete tree, so any attribute they "
                 f"override is recorded as origin='base' -- i.e. 'no override, "
                 f"still vanilla' -- which is NOT what was measured; "
                 f"e.g. {skipped.dropped_samples[0]}")
    if truncated_props:
        # Never silent: a truncated property subtree is exactly the "narrowed the
        # data and reported success" shape this build was fixed to stop doing.
        progress(f"WARNING: {len(truncated_props)} property subtree(s) hit the "
                 f"depth-{MAX_PROP_DEPTH} guard and were NOT indexed; "
                 f"e.g. {truncated_props[0]}. Raise MAX_PROP_DEPTH.")
    _write_db(db_path, config, mods, ordered, order_rank, entities, removed,
              dropped_overlays=skipped.dropped,
              dropped_samples=skipped.dropped_samples)
    progress(f"wrote {len(entities)} entities to {db_path}")
    return db_path


def _write_db(db_path, config, mods, ordered, order_rank, entities, removed,
              dropped_overlays: int = 0, dropped_samples: list[str] | None = None):
    # PID-qualified: two concurrent builds used to pick the SAME `<db>.tmp`, so
    # they raced on os.replace and BOTH died with a raw PermissionError
    # (WinError 32). Build-then-atomically-replace already keeps the store safe
    # — the loser must simply lose quietly and leave a valid db behind.
    tmp = db_path.with_suffix(f"{db_path.suffix}.{os.getpid()}.tmp")
    if tmp.exists():
        tmp.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(tmp))
    # BUILD, THEN INSTALL -- and clean up on EVERY exit, not just the one that was
    # thought of. This block used to carry `finally: con.close()` and nothing
    # else, so it closed the HANDLE and never removed the FILE. Every exception
    # between the connect above and the os.replace below therefore leaked a fully
    # materialised temp, permanently, while the only cleanup sat in the os.replace
    # OSError handler -- the one failure mode somebody had already imagined.
    #
    # The sharpest case is architectural rather than exotic. refuse_if_mutating()
    # is called INSIDE this block by design, at the stamping site, so that no
    # other entry path can slip past it -- which means any store build overlapping
    # a mutating gate deposited an orphan. MEASURED in the live registry: three,
    # from 2026-08-26 at 12:50, 12:52 and 12:54, each with zero rows in every
    # table INCLUDING meta, so they died before the commit. That is what makes
    # them a leak and not a half-written store; build-then-replace is atomic and
    # the installed store was never at risk.
    #
    # AND THE RECLAIM ABOVE CANNOT COLLECT THEM. It is keyed to os.getpid(), so it
    # only ever matches a leftover from THIS process, and an orphan's creator is
    # by definition gone. A reclaim scoped to the current process looks present in
    # review and is unreachable in fact, which is why the fix is "never create
    # one" rather than "sweep them up later".
    #
    # Deliberately NOT an automatic sweep of foreign temps. Deciding whether a
    # stranger's temp is abandoned means deciding whether its pid is alive, and on
    # Windows os.kill(pid, 0) maps to TerminateProcess: guessing wrong either
    # kills a running build or overwrites files underneath it. mutation_probe
    # reached the same conclusion and made its recovery an explicit step; so does
    # this. Pre-existing orphans are removed by hand, once.
    try:
        try:
            con.executescript("PRAGMA journal_mode=MEMORY; PRAGMA synchronous=OFF;" + _SCHEMA)
            con.executemany("INSERT INTO meta VALUES(?,?)", [
                ("schema_version", str(SCHEMA_VERSION)),
                ("reference", str(config.reference)),
                ("active_mods", str(len(mods))),
                ("load_order", json.dumps([m["folder"] for m, _ in ordered])),
                ("advisory", _ADVISORY),
                # PERSISTED, not merely printed: build-time progress scrolls past and
                # is gone, while a store carrying silently-wrong origins outlives the
                # run that made it. A reader can now ask the store itself whether it
                # was built over work that did not happen.
                ("dropped_overlays", str(dropped_overlays)),
                ("dropped_overlay_samples", json.dumps(dropped_samples or [])),
            ])
            # WHEN this store was true, not just how much it holds. Without it the
            # store cannot tell a current answer from one about a superseded world —
            # the exact failure that let BaseX's x4eff serve pre-merge-fix values for
            # eleven days (140 of 194 engine thrust rows wrong).
            _mutation.refuse_if_mutating("build the effective store")
            _freshness.stamp_sqlite(con, _freshness.fingerprint(config, _ext_root(config)))
            con.executemany("INSERT INTO mods VALUES(?,?,?,?,?,?,?)", [
                (m["folder"], m["id"], m["name"], m["version"], order_rank[m["folder"]],
                 int(m["enabled"]), int(_cat.is_packed(p))) for m, p in ordered])
            default = Origin(BASE, BASE)
            eid = 0
            ent_rows, attr_rows = [], []
            for e in entities:
                eid += 1
                ent_rows.append((eid, e.kind, e.name, e.klass, e.vpath, e.origin,
                                 _chain_json(e.chain, default)))
                for prop, value, num, chain in e.attrs:
                    attr_rows.append((eid, prop, value, num,
                                      chain[-1].source, _chain_json(chain, default)))
            con.executemany("INSERT INTO entities VALUES(?,?,?,?,?,?,?)", ent_rows)
            con.executemany("INSERT INTO attrs VALUES(?,?,?,?,?,?)", attr_rows)
            con.executemany("INSERT INTO removed VALUES(?,?,?,?)", removed)
            con.commit()
        finally:
            con.close()
    except BaseException:
        # The handle is closed by the inner finally BEFORE this runs. Windows
        # refuses to unlink a file it still holds open, so that ordering is the
        # entire reason these are nested rather than chained.
        try:
            tmp.unlink()
        except OSError:
            # silent-ok: best-effort removal of OUR OWN temp while an exception is
            # already in flight. The original failure is re-raised on the next
            # line, so nothing is hidden -- and masking it to report a cleanup
            # problem would lose the diagnosis the caller actually needs. Same
            # reasoning, and the same shape, as _registry.save.
            pass
        raise
    try:
        os.replace(tmp, db_path)
    except OSError as exc:
        # Windows refuses the replace while another process holds the target
        # (a concurrent build, or a reader with the store open). The store is
        # untouched and still valid, so say that plainly instead of unwinding a
        # traceback that reads like data loss.
        try:
            os.unlink(tmp)
        except OSError:
            pass  # silent-ok: best-effort cleanup of our own temp; the real error is raised below
        raise OSError(
            f"could not install the rebuilt store at {db_path}: {exc}. "
            "Another x4effective build or an open reader is holding it — "
            "the existing store is unchanged; retry once that finishes."
        ) from exc


# --- queries ------------------------------------------------------------------

def _ext_root(config) -> Path | None:
    """The extensions directory this store was built over, or ``None`` when that
    cannot be established.

    Freshness is judged against the same world the build enumerated, so this asks
    `_paths` rather than guessing from `config.overlays` -- an empty overlay list
    would otherwise fingerprint "no mods" and read as fresh forever. That reason
    is why the original code existed and it still holds: UNKNOWN is the honest
    answer here, never an inferred root.

    RETURNS ``None`` WHEN THE CONFIG DESCRIBES A DIFFERENT WORLD (F63 symptom 2).
    This used to return `_registry.GAME_EXTENSIONS` unconditionally, behind an
    `except AttributeError` that can never fire -- the attribute EXISTS and its
    VALUE is None when nothing is configured. So a store built over throwaway test
    directories was stamped with a fingerprint describing the REAL installed game:
    an artifact claiming provenance it does not have.

    `fingerprint()` already models the three states this needs (F63 symptom 1):
    OMITTED raises (F46's guard -- a caller who forgot must not silently hash the
    current directory), explicit ``None`` records the content axis as UNKNOWN, and
    `compare()` refuses to call an UNKNOWN axis fresh.
    """
    real_ref = _paths.reference()
    cfg_ref = getattr(config, "reference", None)
    if real_ref is None or cfg_ref is None:
        return None
    if Path(cfg_ref) != Path(real_ref):
        return None
    # ALL the roots the BUILD enumerates, not just the game-root one. The build
    # merges `_registry.mods(...)` over `default_installed_dirs()`, which is THREE
    # roots (game-root extensions, the profile's extensions, the Steam Workshop
    # content dir); this returned one. A mod installed in a root that is enumerated
    # but not fingerprinted goes into the store and is then invisible to
    # `store_freshness` — the store reports FRESH over content it never hashed.
    #
    # `_freshness.content_detail` already takes `dirs` PLURAL and its docstring says
    # every consumer must keep the roots apart, so this was the consumer that did
    # not. The F63 guard added to this very function checks `config.reference` and
    # left the mods axis unguarded.
    #
    # MEASURED on this machine: the digest is IDENTICAL either way
    # (0cd79c957de67d9e), because the profile extensions dir is empty and the
    # workshop dir is absent — so this costs no rebuild here. Recorded rather than
    # skipped precisely because the live cost is zero: that is where a wrong
    # denominator hides (gotcha #23), and "no mods in the profile root" is a fact
    # about THIS install, not about the tool.
    # `or None` IS LOAD-BEARING. default_installed_dirs() returns [] when nothing
    # is configured, and this function's docstring promises None for exactly that
    # state -- "UNKNOWN is the honest answer here, never an inferred root". Widening
    # the return from one Path to the three real roots (correct: a mod in the profile
    # or workshop root was invisible before) silently changed the not-configured
    # answer from None to [], and [] digested as a world with no mods in it.
    dirs = _registry.default_installed_dirs()
    return dirs or None


def store_freshness(con, config=None):
    """Does this store still describe the installed world? (see `_freshness`)"""
    config = config or _merge.Config()
    return _freshness.compare(
        _freshness.read_sqlite(con),
        _freshness.fingerprint(config, _ext_root(config)),
        engine_dependent=True)


def _sqlite_ro_uri(p) -> str:
    """A read-only SQLite URI for `p` (a resolved path). `as_uri()` escapes URI-significant
    characters, but spells a UNC path `file://server/share/...`, and SQLite refuses a URI
    authority ("invalid uri authority") -- so a store on a network share could never open
    (v4.0.0 review R5-3). The authority is folded into the path: `file:////server/share/...`,
    which SQLite hands to the OS as `//server/share/...` (MEASURED through the local admin
    share: the plain form fails, this one opens)."""
    uri = p.as_uri()
    if uri.startswith("file://") and not uri.startswith("file:///"):
        uri = "file:////" + uri[len("file://"):]
    return uri + "?mode=ro"


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise ValueError(f"no store at {db_path} — run `x4effective build` first")
    # Escape URI-significant characters (#, ?, %) before adding read-only mode.
    con = sqlite3.connect(_sqlite_ro_uri(db_path.resolve()), uri=True)
    con.row_factory = sqlite3.Row
    try:
        version = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not version or version[0] != str(SCHEMA_VERSION):
            raise ValueError(f'incompatible effective-store schema: {version[0] if version else "unknown"}; '
                             f'expected {SCHEMA_VERSION}')
        # Minimum read contract, no full integrity scan on every CLI invocation.
        for table, columns in {
            'meta': 'key,value',
            'mods': 'folder,mod_id,name,version,rank,enabled,packed',
            'entities': 'id,kind,name,klass,vpath,origin,chain',
            'attrs': 'entity_id,prop,value,value_num,origin,chain',
            'removed': 'vpath,node_path,source,op_line',
        }.items():
            con.execute(f'SELECT {columns} FROM {table} LIMIT 0')
    except Exception:
        con.close()
        raise
    return con
