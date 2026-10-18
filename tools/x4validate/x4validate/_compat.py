r"""x4compat: detect how the ACTIVE (enabled) mods collide over the effective XML tree.

Unlike a naive file-overlap check (which flags every mod that touches, say,
``t/0001-l007.xml`` — 15 of them, all harmlessly union-merged), this resolves each
mod's intent against the tree at each operation's actual position and dispatches by the engine's
actual merge semantics:

- ``libraries/`` ``index/`` ``t/`` are additively UNIONED by ``@id``/``@name`` — two
  mods co-existing there is normal; only two mods defining the SAME key collide
  (UNION-KEY: later load-order wins).
- Everywhere else, a mod's ``<diff>`` ops resolve to concrete nodes; two mods
  resolving to the same node where at least one replaces/removes it is a HARD
  collision (later wins, the earlier effect is lost or compounded). Two mods only
  ``<add>``-ing under the same parent is SOFT (they coexist); each child KEY two of
  them add there is additionally a HARD row naming only that key's providers (which
  duplicate survives is not engine-measured). A wildcard icon key (``icon#ship_*``) is
  compared by its EXPANSION instead -- one ID per matching texture file, loose and
  packed -- so only an ID two expansions share is HARD; a texture set that cannot be
  enumerated is announced as NOT CHECKED (F276). Full union files declaring one wildcard
  are compared the same way, as one UNION-KEY row per shared expanded ID.
- Two non-diff full files at the same non-union path FULL-OVERRIDE (later clobbers).
- An op guarded by ``if=`` is judged by its guard as `_merge.apply_diff` evaluates it: on
  the file as it stands when that op applies (base+DLC, every earlier mod, the earlier ops
  of its own file). FALSE there: the op changes nothing, joins no row of any kind, and is
  listed in `CompatReport.guarded_off` instead (issue #8). TRUE: it takes part, and its rows
  say the answer depends on this load order. UNEVALUATED (e.g. invalid XPath): it is kept,
  labelled, and counted as a skip -- never silently treated as false.

Load order (who wins): the engine's MEASURED order -- case-insensitive folder order,
dependencies loaded in repeated passes (see `_loadorder.compute_load_order` and
`gates/load_order_oracle.py`). Later loads win.
"""

from __future__ import annotations

import copy
import re

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from x4validate import (_paths, _cat, _merge, _registry, _scan, _xpath, _input,
                        _loadorder, _modfiles, _compat_state)
from x4validate import __version__

#: `{page,text}` localized reference - display text, never a registry identity.
_TEXTREF_KEY = re.compile(r"#\{\d+,\s*\d+\}$")

UNION_DIRS = ("libraries/", "index/", "t/")
_OPS = ("add", "replace", "remove")
# Files the engine loads once PER EXTENSION (never shared/overridden across mods),
# so multiple mods "shipping" one is normal, not a full-file-override collision.
_PER_EXTENSION_FILES = {"ui.xml"}


@dataclass
class Collision:
    """One way two mods contend over the same effective XML.

    ⚠ **`winner` does NOT mean the same thing for every kind, and for two kinds it
    is deliberately empty.** Reading it as "the mod whose value is live" is wrong
    for SUBTREE and NAME-CLASH; use :meth:`live_value_owner` instead, which cannot
    give a confident wrong answer.
    """
    vpath: str
    kind: str            # HARD | UNION-KEY | FULL-OVERRIDE | SUBTREE | NAME-CLASH | SOFT
    target: str          # node identity / registry key / file path
    mods: list[str]      # involved mod folders, in load order
    winner: str          # the mod whose value is LIVE — empty for SUBTREE/NAME-CLASH
    detail: str = ""
    #: SUBTREE only: the mod that did the WIPING. It is not the owner of the final
    #: value — a third mod loading later can re-supply what was wiped — which is
    #: exactly why it is not called `winner`.
    wiped_by: str = ""
    #: HARD only: set when the live state of the node is a REMOVAL -- the last mod
    #: whose op still matches the node at its own load position removed it, so the
    #: later mods' ops match nothing (AUDIT-2026-09-24 AN-5; a re-add by another mod
    #: in between is seen, and then this stays empty). `winner` is this remover, and
    #: the live state is the node's ABSENCE -- which a consumer comparing against
    #: stored values must look for among removals, not among surviving values.
    removed_by: str = ""
    #: Stable identities are not XPath positions. Consumers use this path only
    #: against the qualified final tree; removed base identities retain base scope.
    target_path: str = ""
    target_tree: str = "base"  # base | final | removed | unavailable
    removal_path: str = ""
    winner_qualified: bool = True

    def live_value_owner(self) -> str | None:
        """The mod whose value is live, or ``None`` when that is unknowable here.

        =============== ==========================================================
        kind            meaning
        =============== ==========================================================
        FULL-OVERRIDE   the mod that clobbers — its file is the document
        HARD            the mod that loads last at the clashing node. When an
                    EARLIER mod removes the node (AUDIT-2026-09-24 AN-5): the
                    last mod whose op on it still MATCHES at its own load
                    position -- the remover itself if nothing re-supplies the
                    node, a later writer if another mod re-adds it first.
                    Covers the SAME node only; an edit INSIDE a node removed
                    earlier is `CompatReport.removed_first`, not a row
        UNION-KEY       the mod that defines the surviving registry entry
        SUBTREE         ``None`` — `winner` was the WIPER, and a later mod may
                        have restored the values (MEASURED: 3 of 148, 2.0%)
        NAME-CLASH      ``None`` — ``index/macros.xml`` decides, not load order
        SOFT            ``None`` — benign coexistence, nobody "wins"
        =============== ==========================================================

        Added 2026-08-13 after an inbound report compared a SUBTREE `winner`
        against `x4effective`'s per-attribute origin and concluded x4compat was
        wrong. It was not: the two answer different questions. This method is the
        one place that distinction lives. See CLAUDE.md gotcha #18.
        """
        if self.kind in ("SUBTREE", "NAME-CLASH", "SOFT") or self.target_tree == "unavailable" or not self.winner_qualified:
            return None
        return self.winner or None


@dataclass
class Skipped:
    """A file x4compat could NOT analyse. Mirrors `_check.Skipped`'s contract.

    Defined here rather than imported because `_check` imports `_compat`, so the
    dependency only runs one way. Keep the two in step: *degraded* means a whole
    file contributed ZERO collisions, so a clean report is not evidence of
    compatibility and the CLI exits 3.
    """
    what: str       # the vpath / input that could not be analysed
    why: str        # the concrete reason
    degraded: bool = False


@dataclass
class OrderMiss:
    """A `<diff>` op whose selector only a mod loading AFTER it can satisfy.

    AUDIT-2026-09-24 AN-10. Every op is resolved against the UNPATCHED base, so an op
    on a node another mod ADDS matched nothing there and contributed no target: the
    run said "No collisions" while the engine -- which applies this mod's ops BEFORE
    the later mod adds the node -- skips the op. Not a collision (nothing contends),
    so it is not in `collisions`; it is DISCLOSED, as a change that does not happen.
    """
    vpath: str
    mod: str        # the patching mod, which loads first
    line: int       # the op's source line in that mod's file
    sel: str
    added_by: str   # the first later mod after whose ops the selector matches
    #: How the op is GUARDED (`if=...` / `silent`), or "". A guarded op is the optional-
    #: compat idiom -- apply if the other mod is there, stay quiet if not -- so its
    #: skip is intentional, not a mistake; it is still disclosed (review of AN-10).
    guard: str = ""
    #: True when the op's `if=` guard is ALSO false at its load position (issue #8): the
    #: same op is then listed in `CompatReport.guarded_off` too, and load order is not
    #: necessarily the only reason it is skipped -- its sel= and its guard both fail.
    guard_false: bool = False


@dataclass
class RemovedFirst:
    """A `<diff>` op on a node INSIDE one an EARLIER mod removed, still absent when the
    op's own mod loads -- so the engine skips it (review of AN-5). The mirror image of
    SUBTREE, where the wipe loads AFTER the edit; there the edit applied and was
    wiped, here it never applied. Disclosed, not a collision."""
    vpath: str
    mod: str          # the mod whose op matches nothing at its own position
    line: int
    sel: str
    removed_by: str   # the earlier mod that removed the node or its ancestor
    guard: str = ""   # as `OrderMiss.guard`


@dataclass
class GuardedOff:
    """A `<diff>` op whose `if=` guard is FALSE when its mod loads (issue #8).

    `_merge.apply_diff` -- the merge model every other tool trusts -- evaluates `if=`
    against the tree as it stands when the op applies (base+DLC, every earlier mod, the
    earlier ops of the same file) and SKIPS the op when it is false. Such an op changes
    nothing, so it takes no part in any collision row; it is listed here instead, so the
    narrowing is ANNOUNCED rather than silent. Not a degraded run: the op was evaluated,
    and the answer is that it does nothing in this load order.
    """
    vpath: str
    mod: str
    line: int
    sel: str
    guard: str        # as written (`if=...`, plus ` silent` when set)
    #: Other mods whose live ops on this file target the same node / key (or a node
    #: inside or around it): the rows this op WOULD have joined had its guard been true.
    #: For an op whose target is ABSENT from the base tree, the later mod the order-miss
    #: pass names as adding it. **None = UNKNOWN** -- the target is not in the base and no
    #: mod was named as supplying it -- which is NOT the same as `[]` (nobody else).
    shared_with: list[str] | None = field(default_factory=list)
    #: True when the same op is also in `CompatReport.order_misses` (one op, two lists).
    also_order_miss: bool = False


@dataclass
class CompatReport:
    collisions: list[Collision] = field(default_factory=list)
    mods_scanned: int = 0
    files_examined: int = 0
    load_order: list[str] = field(default_factory=list)
    #: Ops whose sel= could not be EVALUATED (malformed XPath), so the mod
    #: contributed no targets and cannot participate in collision detection.
    #: For a conflict detector, silently contributing nothing is the worst
    #: failure mode there is: it renders as "these mods do not collide".
    unresolvable: list[str] = field(default_factory=list)
    #: Known operations that the sequential merge did not apply. A no-op never
    #: becomes a live winner merely because its selector matched the old base.
    unapplied: list[str] = field(default_factory=list)
    #: Files that produced no comparison at all. Until 2026-08-01 `_analyze_vpath`
    #: simply `continue`d past every mod when the base tree could not be built,
    #: so 140 of 523 examined files contributed zero collisions and rendered
    #: identically to "analysed, no conflicts". Same failure family as F4.
    skipped: list[Skipped] = field(default_factory=list)
    #: Candidate mode only: the folder whose files were ANALYSED as the candidate,
    #: and every enabled copy left out in its favour (AUDIT-2026-09-24 AN-1). Until
    #: then a candidate path whose folder name was already enabled was silently
    #: swapped for the enabled copy, so a staged update was never read at all.
    candidate_path: str = ""
    excluded_copies: list[str] = field(default_factory=list)
    #: Ops that only a LATER-loading mod's content can satisfy -- see `OrderMiss`.
    order_misses: list[OrderMiss] = field(default_factory=list)
    #: Ops inside a node an EARLIER mod removed -- see `RemovedFirst`.
    removed_first: list[RemovedFirst] = field(default_factory=list)
    #: Ops whose `if=` guard is false at their load position -- see `GuardedOff`.
    guarded_off: list[GuardedOff] = field(default_factory=list)
    #: WHICH roots were scanned, in words -- "" means every configured extensions root
    #: (the set the engine loads). An --ext-dir run scans that folder ONLY and says so
    #: here, because a mod in another root is then absent with nothing else to show it.
    scope_note: str = ""

    def by_kind(self, kind: str) -> list[Collision]:
        """Collisions of *kind*, in a STABLE order.

        Sorted here rather than at each call site because every consumer — the
        renderer, the JSON dump, a saved baseline — must agree run to run. An
        upstream set-iteration made two identical runs emit the same collisions
        in different orders, which turns a baseline diff into noise and lets a
        real change hide in it.
        """
        return sorted((c for c in self.collisions if c.kind == kind),
                      key=lambda c: (c.vpath, c.target, tuple(c.mods)))

    @property
    def hard(self) -> list[Collision]:
        # SUBTREE counts as hard-ish by user decision (2026-08-02): a later mod
        # provably wiping an earlier mod's applied change gates. The order is the
        # engine's MEASURED one (signature order from the log; apply order by the
        # in-game load-order probe, 2026-09-26), no longer a convention.
        return (self.by_kind("HARD") + self.by_kind("FULL-OVERRIDE")
                + self.by_kind("SUBTREE"))

    @property
    def degraded(self) -> list[Skipped]:
        """Skips that cost a whole file — the clean parts prove nothing about them."""
        return [s for s in self.skipped if s.degraded]

    def skip(self, what: str, why: str, degraded: bool = False) -> None:
        self.skipped.append(Skipped(what, why, degraded))


# --- mod discovery + load order ----------------------------------------------

#: LIFTED to `_loadorder.py` 2026-09-06, F69's own named remedy. Load order
#: decides every collision winner, so editing it changes the merged answer for
#: byte-identical inputs -- which is what `_freshness`'s ENGINE axis is for. It
#: could not watch this file, because this file also carries the `x4compat` CLI
#: and naming it there made a CLI-text or DOCSTRING change invalidate the
#: effective store and BaseX `x4eff` for a rebuild that could not move one row.
#:
#: Re-exported rather than moved outright: six call sites and
#: `gates/mutation_probe.py`'s source-string mutant name `_compat.compute_load_order`,
#: and a rename would be churn with a mutation gate attached to it.
_mod_deps = _loadorder.mod_deps
compute_load_order = _loadorder.compute_load_order


#: Lifted into the CLI-free `_modfiles` so the engine freshness axis can watch it (it
#: decides which vpaths `_effective.build_touch_map` attributes to each mod -- store
#: provenance). Kept as an alias: every caller of `_compat._mod_xml_paths` is unchanged.
_mod_xml_paths = _modfiles.mod_xml_paths


# --- node-identity resolution -------------------------------------------------

def _canonical(node) -> str | None:
    """Stable identity of an xpath result within a fixed tree.

    Element -> its absolute getpath; attribute -> parent getpath + '/@name'.
    Two mods selecting the same node via different sel syntax resolve to the same
    element object in the shared tree, hence the same string.
    """
    if isinstance(node, etree._Element):
        return node.getroottree().getpath(node)
    # lxml attribute / text smart-string
    if getattr(node, "is_attribute", False):
        parent = node.getparent()
        if parent is not None:
            return parent.getroottree().getpath(parent) + "/@" + node.attrname
    return None


def _resolve_op_targets(tree: etree._Element, sel: str) -> list[str] | None:
    """Canonical ids the sel resolves to in *tree*, or **None** if un-evaluable.

    Uses _xpath.evaluate so a malformed expression raises rather than passing as
    a no-match. The old contract was literally "empty on no-match/invalid" — one
    value for two states, which in a COLLISION detector means an unparseable sel
    contributes no targets and the mods silently read as compatible.
    """
    try:
        results = _xpath.evaluate(tree, sel)
    except etree.XPathEvalError:
        # silent-ok: None is this function's documented sentinel for "un-evaluable",
        # distinct from [] for "evaluated, matched nothing". _analyze_vpath records
        # every None in CompatReport.unresolvable and render() prints them.
        return None
    if not isinstance(results, list):  # scalar result (count(), name(), ...)
        return []
    ids = []
    for r in results:
        cid = _canonical(r)
        if cid is not None:
            ids.append(cid)
    return ids


def _added_child_keys(op: etree._Element) -> list[str]:
    """@id/@name of an <add>'s child elements (for duplicate-add detection).

    ID FIRST, matching `_child_keys` — this was `name or id` until 2026-08-02,
    which is the wrong identity for the two biggest registries: a ware's or
    job's `name=` is a localized `{page,t}` TEXT REFERENCE, not its key. The
    measured consequence of the mismatch: the engine-confirmed duplicate
    `ware#shield_xen_xl_standard_02_mk1` (`WareDB::Import(): Duplicate
    definition`, two log runs) was invisible, because the diff-add side keyed it
    `ware#{20204,...}` while the union side keyed it `ware#<id>` — and jobs
    compared by display name, which collides across UNRELATED jobs."""
    keys = []
    for child in op:
        if isinstance(child.tag, str):
            k = child.get("id") or child.get("name")
            if k:
                keys.append(f"{child.tag}#{k}")
    return keys


def _child_keys(root: etree._Element) -> set[str]:
    """@id/@name keys of a full-file registry root's direct children (union case)."""
    keys = set()
    for child in root:
        if not isinstance(child.tag, str):
            continue
        k = child.get("id") or child.get("name")
        if k:
            keys.add(f"{child.tag}#{k}")
    return keys


# --- analysis -----------------------------------------------------------------

def _owner_aware_config(vpath: str, folder_to_path: dict[str, Path],
                        config: _merge.Config) -> _merge.Config:
    """Add the OWNER mod to *config*.overlays when *vpath* is a cross-mod patch.

    For `extensions/<owner>/<rel>` the base is `<owner>`'s own `<rel>` — it is not
    in `reference/`. `_merge._build_owned` finds the owner by scanning
    `dlc_dirs() + config.overlays`, so with the bare `Config()` x4compat used to
    pass, the owner was NEVER in that list: `owner_dir` came back None, the tree
    came back None, and `_analyze_vpath` dropped every mod on the file.

    Measured before this fix: **140 of 523 examined files** produced no comparison,
    silently discarding 144 `<diff>` mods across 10 mods — i.e. x4compat could not
    analyse a single cross-mod nested patch, the construct its own module docstring
    calls the most collision-prone in X4 modding. F10 (wave 2) made those files
    visible; they all landed in the drop.

    Only the owner is added, never the other patchers: every mod's `sel=` must
    resolve against the same UNPATCHED base, exactly as the non-nested path does
    (base+DLC, no mods). Folding the patchers in would let one mod's `<remove>`
    hide the very node another mod collides on.
    """
    nested = _merge._nested_target(vpath, config.packed_dlc_names())
    if nested is None:
        return config
    owner = nested[0].lower()
    owner_dir = next((p for f, p in folder_to_path.items() if f.lower() == owner), None)
    if owner_dir is None or owner_dir in tuple(config.overlays):
        return config
    return _merge.replace(config, overlays=tuple(config.overlays) + (owner_dir,))


def base_tree_for(vpath: str, folder_to_path: dict[str, Path],
                  config: _merge.Config):
    """THE base tree x4compat's targets index into: base+DLC plus, for a nested
    `extensions/<owner>/<rel>`, the OWNER mod (`_owner_aware_config`) -- never the
    other patchers, and never a later merged tree. None = no base exists.

    The single definition: `_analyze_vpath` and `gates/cross_tool.py` both call it, so
    the positional targets x4compat emits are always resolved by the gate in the tree
    they were computed against. The gate used to build a bare `Config()` tree, which is
    None for every nested vpath -- MEASURED 2026-10-12: 9 of 50 mappable HARD rows
    UNRESOLVED, over its 10% floor (FX-H8)."""
    return _merge.build_effective(
        vpath, _owner_aware_config(vpath, folder_to_path, config)).tree


def _no_base_reason(vpath: str, folder_to_path: dict[str, Path],
                    config: _merge.Config) -> str:
    """Say WHY no base tree exists, computed — never guessed.

    An earlier draft of this message asserted "the owning mod is not installed"
    for every nested path. That is only one of three causes, and stating an
    unverified one is worse than stating none: a wrong reason is what stops the
    next person checking (the F1 lesson).
    """
    nested = _merge._nested_target(vpath, config.packed_dlc_names())
    if nested is None:
        return f"no file at {vpath} in the base+DLC reference tree"
    owner, rel = nested
    owner_dir = next((p for f, p in folder_to_path.items() if f.lower() == owner.lower()),
                     None)
    if owner_dir is None:
        return f"the owning mod '{owner}' is not an active mod (not installed, or disabled)"
    # Same rule: a malformed owner file must produce the EXPLANATION this function
    # exists to return, not an exception through a caller that only wanted a reason.
    try:
        oroot = _merge.overlay_root(owner_dir, rel)
    except etree.LxmlError as exc:
        return f"'{owner}' ships {rel} but it is malformed XML: {exc}"
    except (OSError, ValueError) as exc:
        # M-b (v4.0.0 delta review): overlay_root's OTHER refusals -- two loose spellings of
        # one file on a case-sensitive filesystem (ValueError), an unreadable archive member
        # (OSError / ValueError) -- are reasons too, never a traceback through this caller.
        return f"'{owner}' ships {rel} but it could not be read: {exc}"
    if oroot is None:
        return f"'{owner}' does not ship {rel}, or it could not be parsed"
    if oroot.tag == "diff":
        return (f"'{owner}' ships {rel} as a <diff> itself, so it is a patch rather "
                "than a base")
    return f"'{owner}' supplies {rel} but the merge produced no tree"


def _analyze_vpath(
    vpath: str,
    mod_folders: list[str],
    folder_to_path: dict[str, Path],
    rank: dict[str, int],
    config: _merge.Config,
    unresolvable: list[str] | None = None,
    per_mod_vpath: dict[str, str] | None = None,
    report: CompatReport | None = None,
    cand_folder: str | None = None,
) -> list[Collision]:
    """Classify collisions among mods touching a single virtual path.

    *unresolvable* accumulates ops whose sel= could not be evaluated — they
    contribute no targets, so without this channel they read as "no collision".

    *per_mod_vpath* gives each mod's OWN path to the same effective file, which
    differ for a cross-mod nested patch: the overlay ships
    `extensions/<target>/<rel>` while the target itself ships `<rel>`. Reading
    every mod at one shared path missed that collision entirely — and it is the
    most collision-prone construct in X4 modding, one mod deliberately
    overwriting another's file. Defaults to *vpath* for every mod.

    *report* receives the files that could not be analysed at all.
    """
    config = _owner_aware_config(vpath, folder_to_path, config)   # idempotent
    base_tree = base_tree_for(vpath, folder_to_path, config)
    is_union = vpath.lower().startswith(UNION_DIRS)
    at = per_mod_vpath or {}

    # Per mod: resolved diff-target ids (tag -> ids), union keys, and full-override flag.
    diff_targets: dict[str, dict[str, list[str]]] = {}   # folder -> {node_id: [tags]}
    add_child_keys: dict[str, dict[str, list[str]]] = {}  # folder -> {parent_id: [childkeys]}
    union_keys: dict[str, set[str]] = {}
    union_roots: dict[str, etree._Element] = {}   # folder -> its full union file (F276)
    diff_add_doc_keys: dict[str, set[str]] = {}  # folder -> doc-wide added keys (F12)
    overriders: list[str] = []
    no_base_reported: set[str] = set()  # one skip per mod, not one per op
    unmatched: dict[str, list[etree._Element]] = defaultdict(list)  # AN-10 candidates
    ops_at: dict[str, dict[str, list[etree._Element]]] = defaultdict(
        lambda: defaultdict(list))                    # folder -> {cid: [ops]} (AN-5)
    ordered = sorted(mod_folders, key=lambda f: rank[f])

    read: list[tuple[str, etree._Element]] = []
    for folder in ordered:
        mod_vpath = at.get(folder, vpath)
        # `overlay_root` RAISES on a malformed document for a caller that passes no
        # `skipped` list -- its "NO CHANNEL, NO SWALLOW" rule, which its
        # XMLSyntaxError branch only started obeying in this arc. This call site has
        # a channel two lines below, so route the parse failure into it rather than
        # letting it escape: MEASURED 2026-09-08, an unhandled raise here took
        # `gates/tool_properties` down with a traceback on the live corpus, because
        # cpsdo_faction/t/0001-l088.xml is genuinely malformed.
        try:
            root = _merge.overlay_root(folder_to_path[folder], mod_vpath)
        except etree.LxmlError as exc:
            if report is not None:
                report.skip(f"{folder} at {mod_vpath}",
                            f"malformed XML, so this mod took no part in the "
                            f"comparison: {exc}")
            continue
        except (OSError, ValueError) as exc:
            # M-b (v4.0.0 delta review): the same channel for overlay_root's other refusals
            # -- two loose spellings of one file on a case-sensitive filesystem (Linux,
            # issue #6), or an unreadable archive member. Uncaught, they ended the run with
            # a traceback; counted here, the clean-looking report is visibly incomplete.
            if report is not None:
                report.skip(f"{folder} at {mod_vpath}",
                            f"could not be read, so this mod took no part in the "
                            f"comparison: {exc}")
            continue
        if root is None:
            if report is not None:
                report.skip(
                    f"{folder} at {mod_vpath}",
                    "the mod lists this file but it could not be read or parsed, so "
                    "this mod took no part in the comparison")
            continue
        read.append((folder, root))

    # Issue #8: an `if=` guard is evaluated where the engine evaluates it -- on the tree
    # as it stands when the op applies. FALSE: the op does nothing, so it joins no row
    # (it is announced in `guarded_off` instead). TRUE: it participates, and every row
    # it joins says the answer depends on this load order. UNEVALUATED: it is KEPT,
    # labelled, and announced -- an unevaluable guard is never read as false.
    full: dict[str, etree._Element | None] = {}

    def full_tree() -> etree._Element | None:
        """The finished tree over every mod on this file -- built ONCE and shared by
        the guard pass and the AN-10 pass, which both self-check against it."""
        if "t" not in full:
            full["t"] = _prefix_tree(vpath, ordered, folder_to_path, config)
        return full["t"]

    outcomes: dict[tuple[str, int], tuple[str, str]] = {}
    if base_tree is not None:
        outcomes = _guard_outcomes(vpath, ordered,
                                   {f: r for f, r in read if r.tag == "diff"},
                                   folder_to_path, config, full_tree=full_tree)
    notes: dict[tuple[str, str], list[str]] = defaultdict(list)  # (folder, cid|key)
    off: list[tuple[str, etree._Element, list[str], list[str]]] = []  # guarded off
    base_owners = set(config.overlays)
    # The plain door also applies `extensions/<owner>/<vpath>` patches onto a full/union
    # supplier among these mods -- including from a mod loading BEFORE it (MEASURED
    # 2026-10-12) -- so the sequential instrument must apply them too, or its byte
    # check against full_tree() fails and the whole file loses collision analysis.
    nested_door = (None if _merge._nested_target(vpath, config.packed_dlc_names())
                   is not None else
                   lambda f, owner: _merge.overlay_root(
                       folder_to_path[f], f"extensions/{owner}/{vpath}", []))
    traced, final_paths, qualified = _compat_state.trace(vpath, base_tree,
        [(f, r) for f, r in read if folder_to_path[f] not in base_owners], full_tree(),
        nested=nested_door)
    if base_tree is not None and not qualified:
        if report is not None:
            report.skip(vpath, "sequential selector instrument disagreed with the normal merge; "
                        "node collision analysis is unavailable", degraded=True)
        return []
    actual_ops: dict[tuple[str, int], bool] = {}
    removals = [(rank[folder], index, identity)
                for (folder, index), record in traced.items() for identity, _path in record.removed_paths]

    for folder, root in read:
        if folder_to_path[folder] in base_owners:
            continue  # already supplies the owner-aware base, never apply twice
        if root.tag == "diff":
            if base_tree is None:
                if report is not None and folder not in no_base_reported:
                    no_base_reported.add(folder)
                    report.skip(
                        vpath,
                        f"{folder} ships a <diff> here but no base tree could be built "
                        f"({_no_base_reason(vpath, folder_to_path, config)}), so its ops "
                        "could not be resolved to nodes",
                        degraded=True)
                continue
            node_map: dict[str, list[str]] = defaultdict(list)
            child_map: dict[str, list[str]] = defaultdict(list)
            doc_keys: set[str] = set()
            for idx, op in enumerate(_op_list(root)):
                if op.tag not in _OPS:
                    continue
                sel = op.get("sel", "")
                state, why = ((outcomes.get((folder, idx))
                               or (_G_UNEVAL, "the guard was not evaluated"))
                              if op.get("if") else ("", ""))
                if state == _G_FALSE:
                    # The engine skips it before ever reading sel=, so an invalid sel=
                    # here is not a NOT CHECKED op either. It stays an AN-10 candidate:
                    # when its target is one only a LATER mod adds, the order-miss
                    # disclosure still names that mod (marked guarded, as before).
                    cids = list(traced[(folder, idx)].potential or ())
                    if cids == []:
                        unmatched[folder].append(op)
                    off.append((folder, op, cids or [],
                                _added_child_keys(op) if op.tag == "add" else []))
                    continue
                note = _guard_note(folder, op, state, why) if state else ""
                if state == _G_UNEVAL and report is not None:
                    report.skip(f"{folder} at {vpath}:{op.sourceline or 0}",
                                _guard_skip_why(op, why))
                # Document-wide registry keys (F12): the engine's uniqueness is
                # per-DOCUMENT (WareDB/GroupDB import), but the same-anchor check
                # below only compares adds under one parent cid — two mods adding
                # the same ware anchored at DIFFERENT siblings never met. A guarded
                # add takes part exactly when its guard does not switch it off
                # (issue #8: it used to be excluded outright, so a TRUE guard was a
                # false negative).
                tracked = traced[(folder, idx)]
                if op.tag == "add" and (tracked.applied or state == _G_UNEVAL):
                    for k in _added_child_keys(op):
                        doc_keys.add(k)
                        if note:
                            notes[(folder, k)].append(note)
                actual_ops[(folder, id(op))] = tracked.applied
                cids = None if tracked.targets is None else list(tracked.targets)
                if cids is None:
                    if unresolvable is not None:
                        unresolvable.append(
                            f"{folder}/{vpath}:{op.sourceline or 0}: sel={sel!r} is not "
                            "trackable node XPath — this op was excluded from collision detection")
                    continue
                if not cids:
                    unmatched[folder].append(op)
                    # Keep the old base target solely to disclose an attempted edit
                    # after removal. It never becomes an applied op or a live winner.
                    old = _resolve_op_targets(base_tree, sel) or []
                    cids = [cid for cid in old if state == _G_UNEVAL or any(
                        (r, i) < (rank[folder], idx) and (cid == removed or cid.startswith(removed + "/"))
                        for r, i, removed in removals)]
                if not tracked.applied and state != _G_UNEVAL and report is not None and cand_folder in (None, folder):
                    report.unapplied.append(f"{folder}/{vpath}:{op.sourceline or 0}: sel={sel!r} "
                                            f"was not applied: {tracked.detail}" +
                                            (f" ({_guard(op)})" if _guard(op) else ""))
                for cid in cids:
                    node_map[cid].append(op.tag)
                    ops_at[folder][cid].append(op)
                    if note:
                        notes[(folder, cid)].append(note)
                    if op.tag == "add":
                        child_map[cid].extend(_added_child_keys(op))
                for cid in tracked.created:
                    if cid not in cids:
                        node_map[cid].append("supply")
                        ops_at[folder][cid].append(op)
                        if note:
                            notes[(folder, cid)].append(note)
            if node_map:
                diff_targets[folder] = dict(node_map)
            if child_map:
                add_child_keys[folder] = dict(child_map)
            # Diff-adds join the registry-key comparison for union files. t/ is
            # check_text's domain; wildcard keys (icon#upgrade_*, icon#ship_*)
            # are the generic-icon idiom — 32 of 37 measured document-wide
            # duplicates, none engine-complained — so they are excluded HERE
            # (the new mechanism) while full-file-vs-full-file keys keep their
            # existing behavior unchanged.
            if (is_union and doc_keys
                    and not vpath.lower().lstrip("/").startswith("t/")):
                # Two key classes are NOT identities and must not join:
                # wildcards (icon#upgrade_* — the generic-icon idiom) and
                # {page,text} references (a <production>'s name= is localized
                # display text; keying on it matched production stages of
                # UNRELATED wares in the measurement).
                diff_add_doc_keys[folder] = {
                    k for k in doc_keys
                    if "*" not in k and not _TEXTREF_KEY.search(k)}
        elif is_union and base_tree is not None and root.tag == base_tree.tag:
            union_keys[folder] = _child_keys(root)
            union_roots[folder] = root
        elif vpath.lower() not in _PER_EXTENSION_FILES:
            overriders.append(folder)

    collisions: list[Collision] = []
    contested: list[tuple[str, list[str], str, dict]] = []   # AN-5 rows, decided below
    # Keys pass 1 already reported as HARD duplicates, with the mods of that
    # row: a union-key row is folded only when it names NO mod the hard row
    # missed (icons: the full-file shippers of icon#upgrade_* are different
    # mods from the same-anchor adders — folding them would lose information).
    hard_dup_keys: dict[str, set[str]] = {}

    def winner(fs: list[str]) -> str:
        return max(fs, key=lambda f: rank[f])

    def gnote(pairs) -> str:
        """The guard clauses (issue #8) of the ops behind a row, each once."""
        out: list[str] = []
        for pair in pairs:
            out += [n for n in notes.get(pair, ()) if n not in out]
        return ("; " + "; ".join(out)) if out else ""

    # 1. diff-target node collisions
    node_to_mods: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for folder, nmap in diff_targets.items():
        for cid, tags in nmap.items():
            node_to_mods[cid][folder] = tags
    for cid, per_mod in node_to_mods.items():
        if len(per_mod) < 2:
            continue
        if all(tags == ["supply"] for tags in per_mod.values()):
            continue  # descendant bookkeeping of an already contested replacement
        all_tags = {t for tags in per_mod.values() for t in tags}
        fs = sorted(per_mod, key=lambda f: rank[f])
        if all_tags == {"add"}:
            # Same-parent adds coexist: one SOFT row naming EVERY adder. Each child key
            # two or more of them add is ALSO a HARD row -- naming only THAT key's
            # providers (issue #4: the whole parent-adder list used to be named, so an
            # unrelated co-adder became the duplicate's "winner" and kept the HARD row in
            # its own candidate report; only the first duplicated key was reported, and
            # the SOFT row disappeared). The engine logs `Duplicate definition of '<id>'`
            # (MEASURED); which definition survives is NOT measured, so the detail says
            # so and `winner` is the last-loading provider by the usual convention.
            unchecked_wild: list[str] = []
            for dup, providers in _dup_add_keys(cid, fs, add_child_keys).items():
                if _is_wildcard_icon(dup):
                    # F276: the engine EXPANDS a wildcard icon per texture file and
                    # logs a duplicate only where two expansions share an ID -- so the
                    # comparison is per expanded ID, never per declared key.
                    shared, unchecked = _wildcard_icon_overlaps(
                        dup, {f: _icon_decls([op for op in ops_at.get(f, {}).get(cid, [])
                                              if op.tag == "add"], dup.split("#", 1)[1])
                              for f in providers},
                        folder_to_path)
                    # Not entered in hard_dup_keys: an expanded ID is not a declared key,
                    # so no UNION-KEY row is the same fact as this one.
                    for icon_id, holders in shared:
                        last = winner(holders)
                        collisions.append(Collision(
                            vpath, "HARD", cid, holders, last,
                            f"{len(holders)} mods' wildcard {dup} expand to icon ID "
                            f"'{icon_id}' (duplicate); '{last}' loads last -- survivor "
                            "not engine-measured" + gnote((f, cid) for f in holders)))
                    if unchecked and (cand_folder is None or cand_folder in providers):
                        unchecked_wild.append(
                            f"{dup} of {', '.join(f for f, _ in unchecked)}")
                        for f, why in unchecked:
                            if report is not None:
                                report.skip(
                                    f"{f}: wildcard {dup} at {vpath}",
                                    f"its texture set could not be enumerated ({why}), so "
                                    f"whether its expansion duplicates another mod's {dup} "
                                    "expansion is NOT CHECKED")
                    continue
                hard_dup_keys.setdefault(dup, set()).update(providers)
                last = winner(providers)
                collisions.append(Collision(
                    vpath, "HARD", cid, providers, last,
                    f"{len(providers)} mods add {dup} under the same parent (duplicate); "
                    f"'{last}' loads last -- survivor not engine-measured"
                    + gnote((f, cid) for f in providers)))
            collisions.append(Collision(
                vpath, "SOFT", cid, fs, winner(fs),
                "multiple mods <add> under the same node (usually coexist)"
                + (f"; NOT CHECKED: wildcard expansion of {'; '.join(unchecked_wild)}"
                   if unchecked_wild else "") + gnote((f, cid) for f in fs)))
        else:
            ops_desc = "; ".join(f"{f}:{'/'.join(per_mod[f])}" for f in fs)
            # AN EARLIER <remove> CAN END THE CONTEST (AUDIT-2026-09-24 AN-5): once a
            # mod removes this node, a later op on it matches NOTHING and the engine
            # skips it -- UNLESS some mod re-supplies the node in between. Which ops
            # still match is decided below, per op AT ITS OWN LOAD POSITION.
            if any("remove" in per_mod[f] for f in fs[:-1]):
                contested.append((cid, fs, ops_desc, dict(per_mod)))
                continue
            collisions.append(Collision(
                vpath, "HARD", cid, fs, winner(fs),
                f"{ops_desc} — '{winner(fs)}' loads last and wins"
                + gnote((f, cid) for f in fs)))

    # 2. union-key collisions (two mods define the same registry entry).
    # Since 2026-08-02 diff-ADDED keys participate too (document-wide, any
    # anchor): registry uniqueness is per-document, and the engine-confirmed
    # `ware#shield_xen_xl_standard_02_mk1` duplicate was an add-vs-add pair
    # anchored at different siblings — invisible to both the same-anchor HARD
    # check and the full-file-only comparison here. Keys pass 1 already
    # reported as HARD duplicates are skipped (one finding per fact).
    key_to_mods: dict[str, list[str]] = defaultdict(list)
    for source in (union_keys, diff_add_doc_keys):
        for folder, keys in source.items():
            # `keys` is a set, and set iteration order varies between processes
            # (hash randomization). Unsorted, that leaked into key_to_mods'
            # insertion order and out into the report, so two identical runs
            # emitted the same 419 collisions in different orders — enough to
            # make a baseline diff look like a change.
            for k in sorted(keys):
                if folder not in key_to_mods[k]:
                    key_to_mods[k].append(folder)
    for k, fs_ in sorted(key_to_mods.items()):
        if len(fs_) < 2 or set(fs_) <= hard_dup_keys.get(k, set()):
            continue
        fs = sorted(fs_, key=lambda f: rank[f])
        if _is_wildcard_icon(k):
            # F276, full-file shippers: as for same-parent adds, the engine compares the
            # EXPANDED IDs, so a row is due only for an ID two expansions share. Wildcard
            # keys reach here only from full union files (the diff-add doc keys drop them).
            name = k.split("#", 1)[1]
            shared, unchecked = _wildcard_icon_overlaps(
                k, {f: (_icon_decls([union_roots[f]], name) if f in union_roots else [None])
                    for f in fs},
                folder_to_path)
            for icon_id, holders in shared:
                collisions.append(Collision(
                    vpath, "UNION-KEY", f"icon#{icon_id}", holders, winner(holders),
                    f"same icon ID '{icon_id}' from wildcard {k} of {len(holders)} mods "
                    f"— '{winner(holders)}' wins"))
            if report is not None and (cand_folder is None or cand_folder in fs):
                for f, why in unchecked:
                    report.skip(
                        f"{f}: wildcard {k} at {vpath}",
                        f"its texture set could not be enumerated ({why}), so whether its "
                        f"expansion duplicates another mod's {k} expansion is NOT CHECKED")
            continue
        collisions.append(Collision(
            vpath, "UNION-KEY", k, fs, winner(fs),
            f"same registry entry {k} defined by {len(fs)} mods — '{winner(fs)}' wins"
            + gnote((f, k) for f in fs)))

    # 3. full-file override collisions
    if len(overriders) >= 2:
        fs = sorted(overriders, key=lambda f: rank[f])
        collisions.append(Collision(
            vpath, "FULL-OVERRIDE", vpath, fs, winner(fs),
            f"{len(fs)} mods ship a full non-diff file here — '{winner(fs)}' clobbers the rest"))

    # 4. subtree clobbers (F18): _canonical gives an element and its own
    # attribute unrelated ids, so a mod replacing an ELEMENT and a mod patching
    # INSIDE that element never registered as colliding — though the later
    # replace plainly wipes the earlier edit. Order-aware by construction: only
    # a wipe that loads AFTER the victim destroys an applied change (a wipe
    # that loads first merely changes what the victim's sel sees, which the
    # Tier B sel check already covers). Measured 2026-08-02 on the 101-mod
    # install: 184 raw pair-hits → 165 real clobbers, and the order filter
    # proved itself by correctly clearing ebi_m0_vro (its ws_ dependency on VRO
    # resolves, so it loads after the wipe it would otherwise be a victim of).
    for a, amap in diff_targets.items():
        wipes = [cid for cid, tags in amap.items()
                 if ("replace" in tags or "remove" in tags) and "/@" not in cid]
        if not wipes:
            continue
        for b, bmap in diff_targets.items():
            if b == a or rank[a] < rank[b]:
                continue
            hits = [(w, cb) for w in wipes for cb in bmap if cb.startswith(w + "/")
                    and not (bmap[cb] == ["supply"] and w in bmap)]
            if hits:
                w0, cb0 = hits[0]
                # winner is deliberately EMPTY, exactly as NAME-CLASH is: for a
                # SUBTREE the load-order "winner" is the mod that WIPED, which is
                # not the owner of the final value, and reporting it in the winner
                # field is a confident wrong answer waiting to be compared against
                # x4effective's origin. The wiper keeps its own field.
                collisions.append(Collision(
                    vpath, "SUBTREE", w0, [b, a], "",
                    f"'{a}' loads after '{b}' and replace/removes {w0}, wiping "
                    f"{len(hits)} of '{b}'s change(s) inside it (e.g. {cb0}) — "
                    "load order is the engine's measured order; advisory where this "
                    "run's load-order notes name an UNMEASURED assumption"
                    + gnote([(a, w0)] + [(b, cb) for _w, cb in hits]),
                    wiped_by=a))

    # 5. REMOVALS, decided per op at its own load position (AUDIT-2026-09-24 AN-5 and
    # its review). (a) A HARD row where an earlier mod removes the node: its winner is
    # the LAST mod whose op on the node still matches when that mod loads -- a re-add
    # by another mod in between makes a later writer live again. (b) An op INSIDE a
    # node an earlier mod removed (SUBTREE's mirror image): if it matches nothing at
    # its position, the engine skips it, and that is disclosed.
    inside: list[tuple[str, str, str]] = []          # (remover, editor, cid)
    for a, amap in diff_targets.items():
        for w in (c for c, tags in amap.items() if "remove" in tags and "/@" not in c):
            for b, bmap in diff_targets.items():
                if b != a and rank[b] > rank[a]:
                    inside += [(a, b, cb) for cb in bmap if cb.startswith(w + "/")]
    if (contested or inside) and base_tree is not None:
        queries = ([(f, op) for cid, fs, _d, _t in contested for f in fs
                    for op in ops_at[f][cid]]
                   + [(b, op) for _a, b, cb in inside for op in ops_at[b][cb]])
        at_pos = {(f, id(op)): actual_ops.get((f, id(op)), False) for f, op in queries}
        for cid, fs, ops_desc, tags in contested:
            alive = [f for f in fs if any(at_pos[(f, id(op))] for op in ops_at[f][cid])]
            w = alive[-1] if alive else next(f for f in fs if "remove" in tags[f])
            dead = [f for f in fs if f not in alive and rank[f] > rank[w]]
            if "remove" in tags[w]:
                collisions.append(Collision(
                    vpath, "HARD", cid, fs, w,
                    f"{ops_desc} — '{w}' REMOVES this node, and the later op(s) from "
                    f"{', '.join(repr(f) for f in dead) or 'no mod'} match nothing when "
                    "they load, so the engine skips them: the removal is what is live"
                    + gnote((f, cid) for f in fs),
                    removed_by=w))
            else:
                gone = [f for f in fs if rank[f] < rank[w] and "remove" in tags[f]]
                collisions.append(Collision(
                    vpath, "HARD", cid, fs, w,
                    f"{ops_desc} — {', '.join(repr(f) for f in gone)} removes this node, "
                    f"but it exists again when '{w}' loads (another mod re-supplies it), "
                    f"so '{w}' applies and wins"
                    + (f"; later op(s) from {', '.join(repr(f) for f in dead)} match "
                       "nothing" if dead else "") + gnote((f, cid) for f in fs)))
        if report is not None:
            seen: set[tuple[str, int]] = set()
            for a, b, cb in inside:
                for op in ops_at[b][cb]:
                    if at_pos[(b, id(op))] or (b, id(op)) in seen:
                        continue
                    seen.add((b, id(op)))
                    report.removed_first.append(RemovedFirst(
                        vpath, b, op.sourceline or 0, op.get("sel", ""), a, _guard(op)))

    offs: list[GuardedOff] = []
    for folder, op, cids, keys in off:
        near: list[str] | None = _would_share(folder, op.tag, cids, keys, diff_targets,
                                               key_to_mods, rank)
        if not cids and not near:
            near = None   # target absent from the base: unknown until the AN-10 pass
        offs.append(GuardedOff(vpath, folder, op.sourceline or 0, op.get("sel", ""),
                               _guard(op), near))

    misses: list[OrderMiss] = []
    if report is not None and unmatched and base_tree is not None:
        misses = _order_misses(vpath, ordered, unmatched, folder_to_path, config,
                               only=cand_folder, full_tree=full_tree)
    # One op, two lists (release review of #8): a guard can be false precisely because
    # its target is not there yet. Each side says so, and the later mod that adds the
    # target is what the guarded-off op would have met.
    by_op = {(g.mod, g.line, g.sel): g for g in offs}
    for m in misses:
        g = by_op.get((m.mod, m.line, m.sel))
        if g is not None:
            m.guard_false = g.also_order_miss = True
            g.shared_with = sorted(set(g.shared_with or ()) | {m.added_by},
                                   key=lambda f: rank[f])
    if report is not None:
        report.guarded_off.extend(offs)
        report.order_misses.extend(misses)
    for collision in collisions:
        if "guard UNEVALUATED" in collision.detail:
            collision.winner_qualified = False
        if collision.target in final_paths:
            collision.target_path = final_paths[collision.target]
            collision.target_tree = "final"
        elif collision.kind in ("HARD", "SUBTREE", "SOFT"):
            removed = [(rank[folder], index, folder, path) for (folder, index), record in traced.items()
                       if folder in collision.mods
                       for identity, path in record.removed_paths if identity == collision.target]
            if removed:
                _rank, _index, remover, collision.removal_path = max(removed)
                collision.target_tree = "removed"
                if collision.kind == "HARD":
                    collision.removed_by = remover
                    if collision.winner_qualified:
                        collision.winner = remover
            else:
                collision.target_tree = "unavailable"
                collision.winner_qualified = False
                collision.detail += "; this former node has no single surviving value scope"
        if not collision.winner_qualified:
            collision.detail = collision.detail.replace("loads last and wins", "loads last among potential contenders")
            collision.detail += "; no qualified live winner is claimed"
    return collisions


def _would_share(folder: str, tag: str, cids: list[str], keys: list[str],
                 diff_targets: dict[str, dict[str, list[str]]],
                 key_to_mods: dict[str, list[str]], rank: dict[str, int]) -> list[str]:
    """The other mods a guarded-off op WOULD have met (issue #8), in load order: a live
    op on the same node; for a replace/remove also one inside or around it (SUBTREE);
    for an add, a mod defining one of its child keys (the document-wide pass). An add's
    anchor is compared by equality only -- every op in a file sits inside its root."""
    near = set()
    for f, nmap in diff_targets.items():
        if f == folder:
            continue
        for c in cids:
            if c in nmap or (tag != "add" and any(
                    n.startswith(c + "/") or c.startswith(n + "/") for n in nmap)):
                near.add(f)
                break
    for k in keys:
        near.update(f for f in key_to_mods.get(k, ()) if f != folder)
    return sorted(near, key=lambda f: rank[f])


def _matches(tree: etree._Element | None, sel: str) -> bool:
    if tree is None:
        return False
    try:
        res = _xpath.evaluate(tree, sel)
    except etree.XPathEvalError:
        return False       # silent-ok: already recorded in `unresolvable` by the caller
    return isinstance(res, list) and bool(res)


def _guard(op: etree._Element) -> str:
    """`if=<expr>` and/or `silent`, as written on *op*; "" when it is unguarded."""
    parts = []
    if op.get("if"):
        parts.append(f"if={op.get('if')}")
    if (op.get("silent") or "").lower() in ("true", "1"):
        parts.append("silent")
    return " ".join(parts)


# --- issue #8: `if=` guards, evaluated where the engine evaluates them ----------------

_G_TRUE, _G_FALSE, _G_UNEVAL = "TRUE", "FALSE", "UNEVALUATED"


def _op_list(root: etree._Element) -> list[etree._Element]:
    """The children `_merge.apply_diff` returns one verdict for, in order: every element
    (comments and PIs are skipped there without a verdict, so they are skipped here)."""
    return [o for o in root if isinstance(o.tag, str)]


def _verdicts(ops: list[etree._Element],
              applied: list) -> dict[int, tuple[str, str]] | None:
    """`{op index: (state, reason)}` for every op carrying `if=`, read off
    `_merge.apply_diff`'s verdicts; **None** when the two lists cannot be paired (the
    caller must then treat every guard as UNEVALUATED, never as false)."""
    if len(applied) != len(ops):
        return None
    out: dict[int, tuple[str, str]] = {}
    for i, (op, a) in enumerate(zip(ops, applied)):
        if not op.get("if"):
            continue
        if a.skipped_if:
            out[i] = (_G_FALSE, "")
        elif a.detail.startswith("invalid if="):
            out[i] = (_G_UNEVAL, "the if= is not valid XPath ("
                      + a.detail.split(": ", 1)[-1] + ")")
        else:
            out[i] = (_G_TRUE, "")
    return out


def _verdicts_at(vpath: str, folder: str, root: etree._Element, earlier: list[str],
                 folder_to_path: dict[str, Path],
                 config: _merge.Config) -> dict[int, tuple[str, str]]:
    """The REBUILD form: apply *root* to a copy of the tree built from base+DLC and the
    *earlier* mods, exactly as Tier B pairs `apply_diff` verdicts with ops. Every guard it
    cannot answer comes back UNEVALUATED with the reason -- never silently false."""
    guarded = [i for i, o in enumerate(_op_list(root)) if o.get("if")]
    tree = _prefix_tree(vpath, earlier, folder_to_path, config)
    if tree is None:
        why = "no tree could be built at its load position to evaluate the guard against"
        return {i: (_G_UNEVAL, why) for i in guarded}
    ops = _op_list(root)
    got = _verdicts(ops, _merge.apply_diff(copy.deepcopy(tree), root, source=folder))
    if got is None:
        why = f"the merge model's verdicts could not be paired with the {len(ops)} ops"
        return {i: (_G_UNEVAL, why) for i in guarded}
    return got


def _guard_outcomes(vpath: str, ordered: list[str], roots: dict[str, etree._Element],
                    folder_to_path: dict[str, Path], config: _merge.Config,
                    full_tree=None) -> dict[tuple[str, int], tuple[str, str]]:
    """`{(folder, op index): (state, reason)}` for every `if=`-guarded op in *roots*
    (folder -> its `<diff>` root on *vpath*), each evaluated by `_merge.apply_diff` on
    the tree as it stands when that op applies: base+DLC, every mod loading earlier,
    and the earlier ops of its own file.

    Costs nothing when no op carries `if=`. Otherwise ONE incremental pass
    (`_walk_positions`, which applies each `<diff>` with `apply_diff` directly -- what
    `apply_overlay` does for a diff on an existing tree -- and is CHECKED against
    `build_effective`); a nested (mod-owned) vpath, a failed self-check or a file the
    pass did not apply falls back to rebuilding the tree at each guarded mod's position.
    Keyed by op INDEX, never `id(op)`: the pass parses its own copy of each file.
    *full_tree* (a zero-argument callable) supplies the self-check's finished tree
    when the caller already has, or will need, one.
    """
    want = {f for f, r in roots.items() if any(o.get("if") for o in _op_list(r))}
    if not want:
        return {}
    got: dict[tuple[str, int], tuple[str, str]] = {}
    seen: set[str] = set()

    def on_applied(pos, oroot, applied):
        folder = ordered[pos]
        if folder not in want:
            return
        v = _verdicts(_op_list(oroot), applied)
        # Unpaired, or not the op list the rows are built from: this folder is
        # left out of `seen`, so the rebuild below answers it instead.
        if v is None or len(_op_list(oroot)) != len(_op_list(roots[folder])):
            return
        seen.add(folder)
        got.update({(folder, i): sv for i, sv in v.items()})

    want_tree = None
    if full_tree is not None and not (config.overlays or _merge._nested_target(
            vpath, config.packed_dlc_names()) is not None):
        want_tree = full_tree()         # the walk refuses those two shapes unbuilt
    if not _walk_positions(vpath, ordered, folder_to_path, config,
                           lambda *_a: None, want=want_tree, on_applied=on_applied):
        got.clear()
        seen.clear()
    for folder in sorted(want - seen, key=ordered.index):
        v = _verdicts_at(vpath, folder, roots[folder], ordered[:ordered.index(folder)],
                         folder_to_path, config)
        got.update({(folder, i): sv for i, sv in v.items()})
    return got


def _guard_note(folder: str, op: etree._Element, state: str, why: str) -> str:
    """The row-detail clause a TRUE or UNEVALUATED guard adds (issue #8)."""
    g = _guard(op)
    if state == _G_TRUE:
        return (f"({folder}'s op guarded `{g}`: TRUE at its load position -- depends on "
                "this run's load order)")
    tail = ("the merge model skips an invalid if=, engine behaviour UNMEASURED"
            if why.startswith("the if= is not valid") else
            "kept in the comparison as if it applies")
    return f"({folder}'s op guarded `{g}`: guard UNEVALUATED: {why}; {tail})"


def _guard_skip_why(op: etree._Element, why: str) -> str:
    return (f"its guard `{_guard(op)}` could not be evaluated ({why}), so the op was KEPT "
            "in the comparison as if it applies; the merge model skips an invalid if=, "
            "and what the engine does is UNMEASURED")


def _xp_hit(tree: etree._Element | None, sel: str,
            cache: dict[str, "etree.XPath | None"]) -> bool:
    """Does *sel* select at least one node of *tree*? Compiled once per selector."""
    if sel not in cache:
        try:
            cache[sel] = etree.XPath(sel)
        except etree.XPathSyntaxError:
            cache[sel] = None   # silent-ok: an invalid sel is recorded in `unresolvable`
    xp = cache[sel]
    if xp is None or tree is None:
        return False
    try:
        res = xp(tree)
    except etree.XPathEvalError:
        return False            # silent-ok: same channel as above
    return isinstance(res, list) and bool(res)


def _walk_positions(vpath: str, ordered: list[str], folder_to_path: dict[str, Path],
                    config: _merge.Config, visit,
                    want: etree._Element | None = None, on_applied=None) -> bool:
    """ONE INCREMENTAL PASS over the mods on one file, calling
    ``visit(pos, tree, applied)`` with the tree as it stands BEFORE (applied=False) and
    AFTER (applied=True) ``ordered[pos]`` loads. N applies, not a rebuild per question.

    The per-overlay step mirrors `_merge.build_effective`'s loop (inert bare-path diff
    over a non-game base; nested `extensions/<owner>/` patches onto an earlier
    full/union supplier, and an EARLIER mod's nested patch applied as soon as its
    owner supplies the file) through `_merge`'s own `overlay_root` / `apply_overlay`. It is
    CHECKED, not trusted: returns True only when the finished tree serializes exactly
    as `build_effective` over the same mods (*want*, built here if not given). False --
    unsupported shape (a nested, mod-owned vpath), unbuildable, or a drifted pass --
    means every answer `visit` saw must be discarded and re-derived by rebuilding.

    *on_applied* (issue #8): when given, a `<diff>` on an existing tree is applied with
    `_merge.apply_diff` directly -- exactly what `apply_overlay` does for that case -- and
    ``on_applied(pos, oroot, verdicts)`` receives its per-op verdicts. The self-check
    covers this path too; a False return discards the verdicts as well.
    """
    if (config.overlays
            or _merge._nested_target(vpath, config.packed_dlc_names()) is not None):
        return False
    try:
        start = _merge.build_effective(vpath, config)
    except etree.LxmlError:
        return False   # silent-ok: False IS the channel -- the caller rebuilds instead
    tree, from_game = start.tree, start.base_from_game
    owners: list[str] = []
    for pos, folder in enumerate(ordered):
        visit(pos, tree, False)
        odir = folder_to_path[folder]
        oroot = _merge.overlay_root(odir, vpath, [])
        if oroot is not None and not (oroot.tag == "diff" and not from_game):
            # (`tree` is never None here: a diff only reaches this line over a
            # game base, which `build_effective` returned as a tree.)
            if on_applied is not None and oroot.tag == "diff":
                on_applied(pos, oroot, _merge.apply_diff(tree, oroot, source=odir.name))
            else:
                tree, mode = _merge.apply_overlay(tree, oroot, vpath, odir.name)
                if mode in ("union", "full"):
                    owners.append(odir.name)
                    # Mirrors build_effective: an EARLIER mod's nested patch of this
                    # owner applies right after the owner's file (MEASURED 2026-10-12).
                    for prior in ordered[:pos]:
                        if prior.lower() == odir.name.lower():
                            continue
                        nroot = _merge.overlay_root(folder_to_path[prior],
                                                    f"extensions/{odir.name}/{vpath}", [])
                        if nroot is not None:
                            tree, _mode = _merge.apply_overlay(tree, nroot, vpath, prior)
        for owner in owners:
            if odir.name.lower() == owner.lower():
                continue
            nroot = _merge.overlay_root(odir, f"extensions/{owner}/{vpath}", [])
            if nroot is not None:
                tree, _mode = _merge.apply_overlay(tree, nroot, vpath, odir.name)
        visit(pos, tree, True)
    if want is None:
        try:
            want = _merge.build_effective(
                vpath, config, extra_overlays=[folder_to_path[f] for f in ordered]).tree
        except etree.LxmlError:
            return False   # silent-ok: False IS the channel -- the caller rebuilds
    return (tree is not None and want is not None
            and etree.tostring(tree) == etree.tostring(want))


def _prefix_tree(vpath: str, folders: list[str], folder_to_path: dict[str, Path],
                 config: _merge.Config) -> etree._Element | None:
    """`build_effective` over base+DLC and *folders* -- the SLOW, authoritative form."""
    owned = set(config.overlays)
    dirs = [folder_to_path[f] for f in folders if folder_to_path[f] not in owned]
    try:
        return _merge.build_effective(vpath, config, extra_overlays=dirs).tree
    except etree.LxmlError:
        return None        # silent-ok: an unbuildable tree cannot CLAIM a match; the
        # per-mod parse failures are already recorded as NOT ANALYSED rows


def _matches_at_positions(vpath: str, ordered: list[str],
                          queries: list[tuple[str, etree._Element]],
                          folder_to_path: dict[str, Path],
                          config: _merge.Config) -> list[bool]:
    """For each (folder, op): does the op's selector match the tree as it stands when
    that folder LOADS (every earlier mod applied, this one not yet)?"""
    cache: dict = {}
    by_pos: dict[int, list[int]] = defaultdict(list)
    for q, (folder, _op) in enumerate(queries):
        by_pos[ordered.index(folder)].append(q)
    out = [False] * len(queries)

    def visit(pos, tree, applied):
        if not applied:
            for q in by_pos.get(pos, ()):
                out[q] = _xp_hit(tree, queries[q][1].get("sel", ""), cache)

    if _walk_positions(vpath, ordered, folder_to_path, config, visit):
        return out
    for pos, qs in by_pos.items():                       # the rebuild form
        tree = _prefix_tree(vpath, ordered[:pos], folder_to_path, config)
        for q in qs:
            out[q] = _xp_hit(tree, queries[q][1].get("sel", ""), cache)
    return out


def _order_misses(vpath: str, ordered: list[str],
                  unmatched: dict[str, list[etree._Element]],
                  folder_to_path: dict[str, Path],
                  config: _merge.Config,
                  only: str | None = None, full_tree=None) -> list[OrderMiss]:
    """Ops that match nothing in base but WOULD match once a LATER mod has loaded.

    *full_tree*: as `_guard_outcomes` -- the finished tree, shared rather than rebuilt.

    Answered in ONE incremental pass per file (`_walk_positions`). The rebuild form,
    `_order_misses_rebuild`, re-merged the whole prefix for every probe: MEASURED on
    this install, 145 mods, `analyze()` went ~11 s -> ~64 s, almost all of it
    re-merging libraries/wares.xml, 57 mods. This form: ~31 s, same 39 findings --
    about two full merges per file, one of them the self-check.

    For an op of mod M at position i: a match BEFORE M applies means an earlier mod
    supplies the node (the engine has it in time); a match right AFTER M applies
    means M's own content supplies it; otherwise the first later position j at which
    it matches names the adder, ordered[j]. Never matching = a dead selector, the
    validator's finding, not this one. A pass that fails its self-check, or a nested
    (mod-owned) vpath, is answered by the rebuild form instead.

    Every pending op rides the SAME incremental pass (`_walk_positions` visits every
    position regardless of how many ops are still live), so there is no tree-building
    cost to save by filtering ops out beforehand -- only a handful of extra XPath
    evaluations against trees already built. A prior version filtered pending ops
    against the FINISHED tree first as exactly that (unmeasured) optimisation, which
    made a node a later mod adds and a still-later one removes again read as a dead
    selector and drop silently -- it matched for real, briefly, between the add and
    the remove, and the engine sees that too. MEASURED (150 mods, one third of them
    carrying a genuinely dead selector): dropping the pre-filter cost no observable
    time (a difference smaller than run-to-run noise).
    """
    pending: list[tuple[int, etree._Element]] = []
    for folder, ops in unmatched.items():
        i = ordered.index(folder)
        if i == len(ordered) - 1:
            continue
        if only is not None and folder != only and only not in ordered[i + 1:]:
            continue
        pending += [(i, op) for op in ops]
    if not pending:
        return []
    want = (full_tree() if full_tree is not None
            else _prefix_tree(vpath, ordered, folder_to_path, config))
    if want is None or config.overlays or _merge._nested_target(
            vpath, config.packed_dlc_names()) is not None:
        return _order_misses_rebuild(vpath, ordered, unmatched, folder_to_path,
                                     config, only)
    cache: dict = {}
    state = {"live": list(pending)}
    out: list[OrderMiss] = []

    def visit(pos, tree, applied):
        live = state["live"]
        if not applied:
            # Ops of THIS folder that already match are supplied by an earlier mod.
            state["live"] = [(i, op) for i, op in live
                             if not (i == pos and _xp_hit(tree, op.get("sel", ""), cache))]
            return
        keep = []
        for i, op in live:
            if i > pos:
                keep.append((i, op))                   # its own position is ahead
            elif _xp_hit(tree, op.get("sel", ""), cache):
                if i < pos:                            # first match after a LATER mod
                    out.append(OrderMiss(vpath, ordered[i], op.sourceline or 0,
                                         op.get("sel", ""), ordered[pos], _guard(op)))
                # i == pos: the mod's OWN content supplies the node -- not a miss
            else:
                keep.append((i, op))
        state["live"] = keep

    if not _walk_positions(vpath, ordered, folder_to_path, config, visit, want=want):
        return _order_misses_rebuild(vpath, ordered, unmatched, folder_to_path,
                                     config, only)
    return out


def _order_misses_rebuild(vpath: str, ordered: list[str],
                  unmatched: dict[str, list[etree._Element]],
                  folder_to_path: dict[str, Path],
                  config: _merge.Config,
                  only: str | None = None) -> list[OrderMiss]:
    """Ops that match nothing in base but WOULD match once a LATER mod has loaded.

    Trees are merged only when needed, cheapest filter first (a merge of a heavily
    patched libraries/wares.xml costs seconds -- MEASURED ~3.7 s here): ONE tree of
    every mod on this file (no match there = a dead selector, the validator's
    finding, not this one); per surviving mod, the mods loading BEFORE it (a match
    there = the engine has the node in time, nothing is skipped); then a bisection
    over the later mods to NAME the first one after which it matches. The patching
    mod is left out of those trees, so an op on a node its own mod adds never finds
    a later "adder" and is never reported.

    The SLOW form, kept as the fallback for a nested (mod-owned) vpath and for a file
    whose incremental pass fails its self-check: on this install it cost ~64 s per
    full run where `analyze()` alone is ~11 s.

    DISCLOSED, unlike `_order_misses`'s twin gap (finding 3): the initial "one tree
    of every mod" filter here still reads a node a later mod adds and a still-later
    one removes again as a dead selector, and this form's bisection assumes a
    selector's match state is MONOTONIC in the later mods (never fixed to true,
    never true again) -- exactly what a later removal breaks. Rebinding it to a
    linear scan is a materially different, riskier change to a path only reached on
    a self-check failure or a nested vpath; out of scope here. Proven, not merely
    asserted: `test_the_rebuild_FALLBACK_still_shares_the_masking_limit` in
    test_compat.py forces this path and pins the gap.
    """
    owned = set(config.overlays)

    def tree_of(folders: list[str]) -> etree._Element | None:
        dirs = [folder_to_path[f] for f in folders if folder_to_path[f] not in owned]
        try:
            return _merge.build_effective(vpath, config, extra_overlays=dirs).tree
        except etree.LxmlError:
            return None    # silent-ok: an unbuildable tree cannot CLAIM a miss; the
            # per-mod parse failures are already recorded as NOT ANALYSED rows

    out: list[OrderMiss] = []
    everyone: list[etree._Element | None] = []
    for folder, ops in unmatched.items():
        i = ordered.index(folder)
        before, later = ordered[:i], ordered[i + 1:]
        if not later:
            continue
        if only is not None and folder != only and only not in later:
            continue          # candidate mode: a miss must involve the candidate
        if not everyone:
            everyone.append(tree_of(ordered))
        live = [op for op in ops if _matches(everyone[0], op.get("sel", ""))]
        if not live:
            continue
        head = tree_of(before)
        live = [op for op in live if not _matches(head, op.get("sel", ""))]
        if not live:
            continue
        # prefix[k] = before + later[:k] (this mod left out); prefix[0] is `head`.
        # BISECT for the first k that matches -- log2(n) merges, not n -- then
        # CONFIRM k matches and k-1 does not. An op only this mod's OWN content
        # satisfies matches at no k, fails the confirmation and is never reported;
        # a node added and removed again cannot name the wrong adder silently.
        prefix: dict[int, etree._Element | None] = {0: head}

        def at(k: int) -> etree._Element | None:
            if k not in prefix:
                prefix[k] = tree_of(before + later[:k])
            return prefix[k]

        for op in live:
            sel = op.get("sel", "")
            lo, hi = 0, len(later)          # invariant: no match at lo, match at hi
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if _matches(at(mid), sel):
                    hi = mid
                else:
                    lo = mid
            if _matches(at(hi), sel) and not _matches(at(hi - 1), sel):
                out.append(OrderMiss(vpath, folder, op.sourceline or 0, sel,
                                     later[hi - 1], _guard(op)))
    return out


def _dup_add_keys(parent_id: str, folders: list[str],
                  add_child_keys: dict[str, dict[str, list[str]]]) -> dict[str, list[str]]:
    """Every child key two or more mods add under *parent_id* -> its providers.

    Providers keep the order of *folders* (the caller passes load order); keys are
    returned sorted, so the report is deterministic. A key one mod adds twice is not a
    cross-mod duplicate and is not returned."""
    providers: dict[str, list[str]] = {}
    for f in folders:
        for k in add_child_keys.get(f, {}).get(parent_id, []):
            if f not in providers.setdefault(k, []):
                providers[k].append(f)
    return {k: fs for k, fs in sorted(providers.items()) if len(fs) > 1}


#: Texture files the engine accepts for a declared texture extension, besides the
#: declared one. `.gz` is MEASURED 2026-10-10: every vanilla and DLC wildcard icon declares
#: `*.tga` while every file in those directories is `.gz` (vanilla `ui/ships` 166 of 166;
#: boron/split/terran DLC 68 of 68) -- the shipped icons only exist if `.gz` answers the
#: pattern. `.dds` is INFERRED from the engine's texture-substitution convention, unmeasured.
_TEXTURE_ALIASES = (".gz", ".dds")
_EXT_QUALIFIED = re.compile(r"extensions/([^/]+)/(.+)", re.IGNORECASE)


def _is_wildcard_icon(key: str) -> bool:
    return key.startswith("icon#") and "*" in key


def _wildcard_icon_ids(name: str, texture: str,
                       folder_to_path: dict[str, Path]) -> tuple[set[str] | None, str]:
    """Expand one wildcard icon declaration the way the engine does, or say why not.

    ``<icon name="ship_*" texture="extensions\\m\\dir\\*.tga">`` defines one ID per file in
    mod ``m``'s ``dir`` (loose AND packed) matching the pattern: the name's ``*`` replaced by
    the text the texture's ``*`` matched. READ, engine log 2026-10-08: wildcard 'ship_*' with
    texture path 'extensions\\vro\\...\\ships/ship_arg_xl_battleship_lc4_02_macro.tga' is ID
    'ship_ship_arg_xl_battleship_lc4_02_macro'. IDs are lowercased (X4 paths are
    case-insensitive). Returns ``(None, reason)`` -- never an empty set standing in for an
    answer -- when the set cannot be enumerated: a bare (not ``extensions\\<mod>\\``) path,
    whose resolution is not measured; an owner that is not an active mod; an unsupported
    pattern; an unreadable directory or catalog; or no matching file at all."""
    t = texture.replace("\\", "/").strip().lstrip("/")
    m = _EXT_QUALIFIED.fullmatch(t)
    if m is None:
        return None, (f"bare texture path {texture!r} -- how the engine resolves a path "
                      "that is not extensions\\<mod>\\... is not measured")
    owner = m.group(1).lower()
    owner_dir = next((p for f, p in folder_to_path.items() if f.lower() == owner), None)
    if owner_dir is None:
        return None, f"texture owner '{m.group(1)}' is not an active mod"
    d, _, pat = m.group(2).rpartition("/")
    if (name.count("*") != 1 or pat.count("*") != 1 or "*" in d or not d
            or "." not in pat.split("*", 1)[1]):
        return None, f"unsupported wildcard pattern name={name!r} texture={texture!r}"
    pre, post = pat.split("*", 1)
    stem_post = post.rsplit(".", 1)[0]
    pats = [re.compile(re.escape(pre) + "(.*)" + re.escape(p), re.IGNORECASE)
            for p in (post, *(stem_post + a for a in _TEXTURE_ALIASES))]
    dl = d.lower() + "/"
    files: set[str] = set()
    try:
        for k in _cat.mod_vfs(owner_dir, xml_only=False, packed_only=True):
            rest = k[len(dl):]
            if k.lower().startswith(dl) and rest and "/" not in rest:
                files.add(rest)
        loose = owner_dir / d
        if loose.is_dir():
            files.update(p.name for p in loose.iterdir() if p.is_file())
    except OSError as exc:
        return None, f"could not read {m.group(1)}'s textures: {exc}"
    ids: set[str] = set()
    for fname in files:
        for rx in pats:
            hit = rx.fullmatch(fname)
            if hit:
                ids.add(name.replace("*", hit.group(1)).lower())
                break
    if not ids:
        return None, f"no texture file under {m.group(1)}/{d} matches {pat!r} (loose or packed)"
    return ids, ""


def _icon_decls(parents, name: str) -> list[str | None]:
    """``texture=`` of every ``<icon>`` keyed *name* among the children of *parents*
    (the ``<add>`` ops of a diff, or a full file's root); ``None`` where it is absent."""
    return [child.get("texture") for parent in parents for child in parent
            if isinstance(child.tag, str) and child.tag == "icon"
            and (child.get("id") or child.get("name")) == name]


def _wildcard_icon_overlaps(
        key: str, decls: dict[str, list[str | None]],
        folder_to_path: dict[str, Path],
) -> tuple[list[tuple[str, list[str]]], list[tuple[str, str]]]:
    """F276: compare the providers of a duplicated wildcard icon *key* by EXPANSION.

    *decls* maps each provider, IN LOAD ORDER, to the ``texture=`` of its declarations
    of *key* (see :func:`_icon_decls`). Returns ``(shared, unchecked)``: ``shared`` =
    ``[(icon_id, holders in load order)]`` for every expanded ID two or more providers'
    expansions contain; ``unchecked`` = ``[(provider, reason)]`` for each declaration
    whose texture set could not be enumerated (its IDs, if another declaration of the
    same provider expanded, still take part)."""
    name = key.split("#", 1)[1]
    holders: dict[str, list[str]] = {}
    unchecked: list[tuple[str, str]] = []
    for f, textures in decls.items():               # load order, kept in `holders`
        for tex in textures:
            if tex is None:
                unchecked.append((f, "the declaration has no texture= attribute"))
                continue
            ids, why = _wildcard_icon_ids(name, tex, folder_to_path)
            if ids is None:
                unchecked.append((f, why))
                continue
            for i in ids:
                if f not in holders.setdefault(i, []):
                    holders[i].append(f)
    shared = [(i, fs) for i, fs in sorted(holders.items()) if len(fs) > 1]
    return shared, unchecked


def _macro_defs(mod_path: Path) -> dict[str, str]:
    """`{macro_name_lower: vpath}` for macros this mod DEFINES (not diffs) -- whatever
    their `if=` guards say (`_name_clashes` evaluates those; see `_macro_def_entries`).

    Narrowed to files under a `macros/` directory — the engine-wide convention
    (`assets/.../macros/<name>_macro.xml`) that every observed case follows.
    Parsing every XML of every mod would roughly double `analyze`'s runtime for
    definitions that do not live there.
    """
    out: dict[str, str] = {}
    for name, vpath, _root, _idx in _macro_def_entries(mod_path):
        out.setdefault(name, vpath)
    return out


def _macro_def_entries(
        mod_path: Path) -> list[tuple[str, str, etree._Element | None, int]]:
    """Every macro definition of this mod as ``(name_lower, vpath, diff_root, op_index)``
    in file order. *diff_root* is set only for a definition supplied by an `if=`-GUARDED
    `<diff>` op (issue #8) -- the caller decides at the mod's load position whether the
    guard lets it through; for every other definition it is None and *op_index* -1."""
    out: list[tuple[str, str, etree._Element | None, int]] = []
    for vpath, root in _scan.iter_mod_xml(
            mod_path, lambda v: "/macros/" in v.replace("\\", "/").lower(), None):
        if root.tag == "diff":
            # A <diff> can still DEFINE macros: `<replace sel="//macros">` with a
            # fresh <macros> payload is the standard whole-file override idiom
            # (VRO ships 848 of them). Skipping all diffs missed the very case
            # this check was built for — `missile_flagship_light_mk1_macro`,
            # where VRO's definition arrives exactly that way. Only count a macro
            # the op actually SUPPLIES; a bare `<replace sel=".../@attr">` tweak
            # names no macro and defines nothing.
            for idx, op in enumerate(_op_list(root)):
                if op.tag not in ("add", "replace"):
                    continue
                guarded = bool(op.get("if"))
                for m in op.iter("macro"):
                    name = m.get("name")
                    if name:
                        out.append((name.lower(), vpath, root if guarded else None,
                                    idx if guarded else -1))
            continue
        for m in root.iter("macro"):
            name = m.get("name")
            if name:
                out.append((name.lower(), vpath, None, -1))
    return out


def _strip_nesting(vpath: str) -> str:
    """`extensions/<owner>/<rel>` -> `<rel>`; anything else unchanged (lowercased).

    The nested form is a PATCH INTO <owner>'s file, so both spellings name the
    same logical file. Unpacked `ego_dlc_*` content genuinely lives under
    `extensions/`, so it is left alone.
    """
    v = vpath.replace("\\", "/").lower().lstrip("/")
    parts = v.split("/")
    if len(parts) > 2 and parts[0] == "extensions" and not _merge.is_dlc_folder(parts[1]):
        return "/".join(parts[2:])
    return v


def _name_clashes(mods: list[dict], rank: dict[str, int],
                  cand_folder: str | None,
                  config: _merge.Config | None = None,
                  report: CompatReport | None = None) -> list[Collision]:
    """Macro NAMES defined by 2+ mods in DIFFERENT files.

    A collision class the per-vpath scan structurally cannot see: the two mods
    never share a path, so nothing ever compares them — yet X4 resolves macros by
    NAME through `index/macros.xml`, so only ONE definition is ever loaded and the
    other is dead content the author cannot tell is dead.

    Found 2026-08-09 by re-deriving the missile roster:
    `missile_flagship_light_mk1_macro` is defined by both `rackham` and `vro` at
    different paths, and the effective index points at VRO's — so rackham's file
    is never read. Measured extent: 22 names across the installed set.

    Base/DLC are excluded: vanilla itself ships `cluster_sm3_background_macro` at
    six paths, so the pattern is legal and only becomes a question between mods.

    **The winner is NOT decided by load order** — `index/macros.xml` decides, and
    that file is itself patchable. So `winner` is left empty and the detail says
    to resolve it with `x4effective dump index/macros.xml`. Naming a load-order
    winner here would be a confident wrong answer.

    A definition supplied by an `if=`-guarded `<diff>` op (issue #8) counts only when
    its guard is not FALSE on the file as it stands when its mod loads; a guarded-off
    one is announced in *report*.guarded_off when it alone kept a clash from showing.
    """
    defs: dict[str, dict[str, list[tuple[str, etree._Element | None, int]]]] = \
        defaultdict(lambda: defaultdict(list))
    folder_to_path = {m["folder"]: Path(m["path"]) for m in mods}
    for m in mods:
        folder = m["folder"]
        if folder.lower().startswith("ego_dlc_"):
            continue
        for name, vpath, root, idx in _macro_def_entries(Path(m["path"])):
            defs[name][folder].append((vpath, root, idx))

    ordered = sorted(folder_to_path, key=lambda f: rank.get(f, len(rank)))
    verdicts: dict[tuple[str, str], dict[int, tuple[str, str]]] = {}

    def verdict(folder, vpath, root, idx) -> tuple[str, str]:
        if (folder, vpath) not in verdicts:
            cfg = _owner_aware_config(vpath, folder_to_path, config or _merge.Config())
            verdicts[(folder, vpath)] = _verdicts_at(
                vpath, folder, root, ordered[:ordered.index(folder)], folder_to_path, cfg)
        return verdicts[(folder, vpath)].get(idx, (_G_UNEVAL, "the guard was not evaluated"))

    out: list[Collision] = []
    for name, entries in sorted(defs.items()):
        if len(entries) < 2:
            continue
        # Compare LOGICAL files. `extensions/<owner>/<rel>` is the nested
        # cross-mod patch form of `<rel>` — the engine merges it INTO the owner's
        # file, so it is the same file, not a rival definition. Without this
        # every cross-mod patch pair looked like a name clash (measured: 57 rows,
        # of which 35 were this).
        if len({_strip_nesting(v) for es in entries.values() for v, _r, _i in es}) < 2:
            continue          # same logical file -> already a file-level collision
        per_mod: dict[str, str] = {}
        gnotes: list[str] = []
        dropped: list[tuple[str, str, etree._Element]] = []
        for folder, es in entries.items():
            for vpath, root, idx in es:
                if root is None:
                    per_mod.setdefault(folder, vpath)
                    continue
                op = _op_list(root)[idx]
                state, why = verdict(folder, vpath, root, idx)
                if state == _G_FALSE:
                    dropped.append((folder, vpath, op))
                    continue
                per_mod.setdefault(folder, vpath)
                note = _guard_note(folder, op, state, why)
                if note not in gnotes:
                    gnotes.append(note)
                if state == _G_UNEVAL and report is not None:
                    report.skip(f"{folder} at {vpath}:{op.sourceline or 0}",
                                _guard_skip_why(op, why))
        # (Two logical files imply two mods: `per_mod` holds one file per mod.)
        live = len({_strip_nesting(v) for v in per_mod.values()}) >= 2
        if report is not None:
            for folder, vpath, op in dropped:
                if folder in per_mod:
                    continue      # the mod still defines it elsewhere: nothing narrowed
                report.guarded_off.append(GuardedOff(
                    vpath, folder, op.sourceline or 0, op.get("sel", ""), _guard(op),
                    [f for f in ordered if f in per_mod and f != folder]))
        if not live:
            continue
        if cand_folder is not None and cand_folder not in per_mod:
            continue
        folders = sorted(per_mod, key=lambda f: rank.get(f, len(rank)))
        out.append(Collision(
            vpath=" | ".join(f"{f}:{per_mod[f]}" for f in folders),
            kind="NAME-CLASH", target=name, mods=folders, winner="",
            detail=("same macro name defined in DIFFERENT files; X4 loads only the one "
                    "index/macros.xml points at, so the others are dead. Resolve with "
                    "`x4effective dump index/macros.xml` — load order does NOT decide this."
                    + ("; " + "; ".join(gnotes) if gnotes else "")),
        ))
    return out


def analyze(
    ext_dir: Path | None,
    candidate: Path | None = None,
    config: _merge.Config | None = None,
) -> CompatReport:
    """Analyze collisions across the ACTIVE (enabled) mods, optionally focused on
    *candidate*.

    *ext_dir* None = every configured extensions root (game root, profile root,
    Steam Workshop), which is the set the engine loads; a folder = that folder ONLY,
    recorded in `CompatReport.scope_note` (release review, finding 5: this used to
    scan the game root alone, so a profile-root mod was silently absent).

    If *candidate* is given, only collisions that involve it are reported (the
    "before I add this mod" mode). THE CANDIDATE IS THE COPY AT *candidate*
    (AUDIT-2026-09-24 AN-1): an enabled mod with the same folder name or manifest id
    is EXCLUDED so the two are never counted twice, and is recorded in
    `CompatReport.excluded_copies`. The candidate is placed in the load order by the
    engine's rule -- its folder name and its own manifest's dependencies.
    """
    config = config or _merge.Config()
    # ACTIVE: two mods only collide if the engine loads both. The on-disk set
    # named a disabled mod as a participant in 4 collision rows of the 2026-08-22
    # baseline. The "what if I added this" case is *candidate*, below -- an
    # explicit opt-in, not a side effect of how the world is enumerated.
    not_loaded: list[str] = []
    mods = _registry.mods("active", _scan_dirs(ext_dir), dropped=not_loaded,
                          dlc_config=config)

    cand_folder = None
    excluded: list[str] = []
    if candidate is not None:
        candidate = Path(candidate)
        # ONE placement rule with Tier B and x4stats (`_loadorder.place_candidate`):
        # key = the installed copy's folder when it matches by folder or id (either
        # case), dependencies from the CANDIDATE's manifest. A DIFFERENT enabled copy
        # (a staged update, a dev folder) is left out rather than counted as a second
        # mod colliding with itself; the path the user named wins.
        placement = _loadorder.place_candidate(mods, candidate)
        cand_folder = placement.entry["folder"]
        excluded = [str(m["path"]) for m in placement.excluded
                    if Path(m["path"]).resolve() != candidate.resolve()]
        mods = placement.mods
    folder_to_path = {m["folder"]: Path(m["path"]) for m in mods}

    order_dropped: list[str] = []
    order = compute_load_order(mods, order_dropped)
    rank = {f: i for i, f in enumerate(order)}

    # Invert: lowercased vpath -> {folder: that mod's OWN real vpath}
    #
    # Each owned file is ALSO registered under extensions/<owner>/<rel>, the form
    # a cross-mod nested patch uses to target it. Without the alias the two never
    # share a key — the patching mod ships `extensions/<owner>/md/somefile.xml`
    # while the owner ships `md/SomeFile.xml` (note the case, which the engine
    # ignores and a naive key does not) — so a mod that exists purely to patch
    # another reported "0 shared files examined … no collisions" and exit 0.
    inv: dict[str, dict[str, str]] = defaultdict(dict)
    for m in mods:
        folder = m["folder"]
        for low, real in _mod_xml_paths(Path(m["path"])).items():
            inv[low][folder] = real
            if not low.startswith("extensions/"):
                inv[f"extensions/{folder.lower()}/{low}"][folder] = real

    report = CompatReport(mods_scanned=len(mods), load_order=order,
                          candidate_path=(str(Path(candidate).absolute())
                                          if candidate is not None else ""),
                          excluded_copies=excluded,
                          scope_note=ext_dir_only_note(ext_dir))
    for msg in not_loaded:
        # Not degraded: the engine does not load this mod, so it collides with
        # nothing -- but a collision it WOULD cause if fixed is not shown, so say so.
        report.skip("not in the analysis: a mod the engine does not load", msg)
    for msg in order_dropped:
        # degraded=True: an unreadable manifest costs this mod its dependency
        # edges, so its load-order position — and therefore every collision
        # winner involving it — is a guess. The clean rows prove nothing about it.
        report.skip(msg, "load order degraded to folder order alone for this mod",
                    degraded=True)
    for msg in getattr(mods, "notes", ()):
        # What the mod SET assumes (a Workshop-root mod present, a case-only
        # dependency match) rides on the ModList, not on the order's records above.
        # Not degraded: nothing was skipped; the model's guess is NAMED.
        if msg not in order_dropped:
            report.skip("load-order model", msg)
    for low, per_mod in inv.items():
        if len(per_mod) < 2:
            continue
        if cand_folder is not None and cand_folder not in per_mod:
            continue  # candidate-focused: only files the candidate also touches
        report.files_examined += 1
        # The canonical path is the NESTED form when one exists: build_effective
        # understands extensions/<owner>/<rel> and resolves it to the owner's own
        # file, which is the base every op here is really patching.
        real_vpath = next((v for v in per_mod.values() if v.lower().startswith("extensions/")),
                          next(iter(per_mod.values())))
        found = _analyze_vpath(real_vpath, list(per_mod), folder_to_path, rank, config,
                               report.unresolvable, per_mod_vpath=per_mod,
                               report=report, cand_folder=cand_folder)
        if cand_folder is not None:
            found = [c for c in found if cand_folder in c.mods]
        report.collisions.extend(found)

    if cand_folder is not None:
        report.order_misses = [m for m in report.order_misses
                               if cand_folder in (m.mod, m.added_by)]
        report.removed_first = [m for m in report.removed_first
                                if cand_folder in (m.mod, m.removed_by)]

    # Entity-level pass: same macro NAME, different files. Structurally invisible
    # to the loop above, which keys on vpath.
    report.collisions.extend(_name_clashes(mods, rank, cand_folder, config, report))
    # One record per op (a guarded macro definition can be met by both passes), and in
    # candidate mode only the ops the candidate takes part in.
    seen_off: set[tuple[str, str, int]] = set()
    kept_off: list[GuardedOff] = []
    for g in report.guarded_off:
        if (g.vpath, g.mod, g.line) in seen_off:
            continue
        seen_off.add((g.vpath, g.mod, g.line))
        if (cand_folder is None or cand_folder == g.mod
                or cand_folder in (g.shared_with or ())):
            kept_off.append(g)
    report.guarded_off = kept_off
    return report


# --- CLI ----------------------------------------------------------------------

_KIND_ORDER = ["HARD", "FULL-OVERRIDE", "SUBTREE", "NAME-CLASH", "UNION-KEY", "SOFT"]


def render(report: CompatReport, show_soft: bool = False) -> str:
    lines = [
        f"x4compat: {report.mods_scanned} mods, {report.files_examined} shared files examined.",
        "Load order (winner = last): the engine's measured order (case-insensitive folders, "
        "dependencies in passes).",
    ]
    if report.scope_note:
        lines.append(f"Scope: {report.scope_note}")
    if report.candidate_path:
        # Always name the copy: two copies of one mod (enabled + staged) are the
        # normal case when checking an update, and the answer differs per copy.
        lines.append(f"Candidate analysed: {report.candidate_path}")
        for ex in report.excluded_copies:
            lines.append(f"  excluded (same mod, a different copy): {ex}")
    lines.append("")
    shown_any = False
    for kind in _KIND_ORDER:
        group = report.by_kind(kind)
        if kind == "SOFT" and not show_soft:
            if group:
                lines.append(f"[SOFT]  {len(group)} benign same-parent <add> overlaps "
                             "(coexist; use --soft to list).\n")
            continue
        if not group:
            continue
        shown_any = True
        lines.append(f"=== {kind}  ({len(group)}) ===")
        for c in sorted(group, key=lambda x: x.vpath):
            lines.append(f"  {c.vpath}")
            lines.append(f"     node/key : {c.target}")
            lines.append(f"     mods     : {', '.join(c.mods)}")
            # A SUBTREE has no live-value winner; showing the WIPER under a
            # "winner" label is what made an inbound report mis-read this row.
            if c.wiped_by:
                lines.append(f"     wiped by : {c.wiped_by}  (NOT the owner of the "
                             f"final value — a later mod may re-supply it)")
            elif c.removed_by:
                lines.append(f"     removed  : by {c.removed_by}, first -- the node is "
                             f"ABSENT; later ops on it match nothing")
            elif not c.winner_qualified:
                lines.append(f"     potential: {c.winner or '(unavailable)'}  (live winner not qualified)")
            else:
                lines.append(f"     winner   : {c.winner}")
            if c.detail:
                lines.append(f"     note     : {c.detail}")
        lines.append("")
    if report.removed_first:
        shown_any = True
        lines.append(f"=== EDITS INSIDE A NODE AN EARLIER MOD REMOVED  "
                     f"({len(report.removed_first)}) ===")
        lines.append("  Not a collision: when these ops' mod loads, the node (or an "
                     "ancestor) they edit")
        lines.append("  has already been removed by an earlier mod and nothing has "
                     "re-added it. The engine skips them.")
        for m in sorted(report.removed_first, key=lambda x: (x.vpath, x.mod, x.line)):
            lines.append(f"  {m.vpath}")
            lines.append(f"     mod      : {m.mod} (line {m.line})  sel={m.sel}")
            if m.guard:
                lines.append(f"     guarded  : {m.guard} -- optional compat by design, "
                             "the skip is intended")
            lines.append(f"     removed  : by {m.removed_by}, which loads before it")
        lines.append("")
    if report.order_misses:
        shown_any = True
        lines.append(f"=== PATCHES A NODE ONLY A LATER MOD ADDS  "
                     f"({len(report.order_misses)}) ===")
        lines.append("  Not a collision: these ops match nothing when their mod loads, "
                     "because the node")
        lines.append("  they target is added by a mod that loads AFTER it. The engine "
                     "skips them.")
        if any(m.guard_false for m in report.order_misses):
            lines.append("  Ops marked 'both fail' also have an if= guard that is false "
                         "there, so load order is")
            lines.append("  not necessarily the only reason they are skipped.")
        for m in sorted(report.order_misses, key=lambda x: (x.vpath, x.mod, x.line)):
            lines.append(f"  {m.vpath}")
            lines.append(f"     mod      : {m.mod} (line {m.line})  sel={m.sel}")
            if m.guard_false:
                lines.append(f"     guarded  : {m.guard} -- its sel= and its guard both fail "
                             "at its load position; also listed under GUARDED OFF")
                lines.append(f"     target   : added by {m.added_by}, which loads after it")
                continue
            if m.guard:
                lines.append(f"     guarded  : {m.guard} -- optional compat by design, "
                             "the skip is intended")
            lines.append(f"     needs    : {m.added_by}, which loads after it")
        lines.append("")
    if report.guarded_off:
        met = [g for g in report.guarded_off if g.shared_with is None or g.shared_with]
        lines.append(f"=== GUARDED OFF: if= FALSE AT ITS LOAD POSITION  "
                     f"({len(report.guarded_off)}, {len(met)} would have met another "
                     "mod) ===")
        lines.append("  Not a collision: when these ops' mod loads, their if= guard is "
                     "false, so the")
        lines.append("  engine skips them and they take part in no row above. Listed: "
                     "the ones that")
        lines.append("  would have shared a target with another mod (a different load "
                     "order can flip a guard).")
        for g in sorted(met, key=lambda x: (x.vpath, x.mod, x.line)):
            lines.append(f"  {g.vpath}")
            lines.append(f"     mod      : {g.mod} (line {g.line})  sel={g.sel}")
            lines.append(f"     guarded  : {g.guard}")
            lines.append("     would meet: " + (
                "unknown -- its target is not in the base and no mod was named as "
                "adding it" if g.shared_with is None else ", ".join(g.shared_with))
                + ("  (also an order miss, above)" if g.also_order_miss else ""))
        lines.append("")
    hard = report.hard
    if not hard and not shown_any:
        lines.append("No HARD / FULL-OVERRIDE / SUBTREE / UNION-KEY collisions found."
                     + (" (but see NOT CHECKED below — that clean result is partial)"
                        if report.unresolvable or report.skipped else ""))
    if report.skipped:
        deg = report.degraded
        lines.append(f"\n=== NOT ANALYSED  ({len(report.skipped)}, "
                     f"{len(deg)} cost a whole file) ===")
        lines.append("  These produced no comparison at all. A collision here would NOT")
        lines.append("  appear above — this is not the same as 'checked, no conflict'.")
        for s in report.skipped:
            lines.append(f"  {'!!' if s.degraded else ' -'} {s.what}")
            lines.append(f"       {s.why}")
    if report.unresolvable:
        lines.append(f"\n=== NOT CHECKED  ({len(report.unresolvable)}) ===")
        lines.append("  These ops could not be resolved, so they took no part in collision")
        lines.append("  detection. A conflict involving them would NOT appear above.")
        for u in report.unresolvable:
            lines.append(f"  !! {u}")
    if report.unapplied:
        lines.append(f"\n=== NOT APPLIED BY THE SEQUENTIAL MERGE ({len(report.unapplied)}) ===")
        lines.append("  These ops changed nothing at their actual position; they cannot supply a live winner.")
        lines.extend("  " + item for item in report.unapplied)
    lines.append(f"\nSummary: {len(report.hard)} hard-ish "
                 f"(HARD+FULL-OVERRIDE+SUBTREE), {len(report.by_kind('UNION-KEY'))} union-key, "
                 f"{len(report.by_kind('SOFT'))} soft, "
                 f"{len(report.order_misses)} op(s) needing a later mod, "
                 f"{len(report.removed_first)} op(s) inside an earlier removal, "
                 f"{len(report.guarded_off)} op(s) guarded off (if= false"
                 + (f"; {k} of them also an order miss"
                    if (k := sum(g.also_order_miss for g in report.guarded_off)) else "")
                 + "), "
                 f"{len(report.degraded)} file(s) not analysed.")
    if report.degraded:
        lines.append("DEGRADED: some files yielded no comparison — see NOT ANALYSED "
                     "above. Exit 3.")
    return "\n".join(lines)


def _scan_dirs(ext_dir: Path | None) -> list[Path] | None:
    """The `dirs` for a `_registry.mods` read: None (every configured root) unless an
    --ext-dir narrowed the run to one folder."""
    return None if ext_dir is None else [ext_dir]


def ext_dir_only_note(ext_dir: Path | None) -> str:
    """The disclosure an --ext-dir run owes: it scanned that folder ONLY.

    Shared by x4compat, x4stats and x4similar (release review, finding 5)."""
    if ext_dir is None:
        return ""
    return (f"scanned ONLY --ext-dir {ext_dir}; mods in any other extensions root "
            "(the profile's extensions folder, the Steam Workshop) are not in this run -- "
            "omit --ext-dir to scan every configured root, the set the engine loads")


def _resolve_candidate(arg: str, ext_dir: Path | None) -> Path:
    """Which copy of the candidate `check <arg>` means (AUDIT-2026-09-24 AN-1).

    An argument containing a path separator, an absolute path, or one starting with
    "." is a PATH and means exactly that copy, even when a same-named mod sits in
    the extensions dir -- that is the "check this staged update" case. A BARE name
    (none of the above) ALWAYS means the copy in the extensions dir, looked up among
    its mod folders, so `check some_mod` means the copy the game has -- even when a
    same-named directory happens to exist relative to the current working directory.
    Formerly a bare name was tested with `p.exists()` FIRST, so running from inside a
    directory that itself held a same-named copy silently resolved to THAT copy
    instead, contradicting this exact promise (AN-1). Anything that resolves to
    neither is returned as given and refused by `_input.require_mod_dir` as a path
    that does not exist.
    """
    p = Path(arg)
    if "/" in arg or "\\" in arg or arg.startswith(".") or p.is_absolute():
        return p
    # "installed", not "active": naming a disabled mod is the "what if I switch it
    # on" question, and analyze() adds the candidate to the active set itself.
    for m in _registry.mods("installed", _scan_dirs(ext_dir)):
        if m["folder"].lower() == arg.lower():
            return Path(m["path"])
    return p


@_paths.refuses_unconfigured
def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # silent-ok: console encoding shim. Failure means the default codec
        # stays; it affects how output LOOKS, never what was examined.

    p = argparse.ArgumentParser(
        prog="x4compat",
        description="Detect how the ENABLED (active) X4 mods collide over the effective "
                    "XML tree -- the set the engine loads: on disk in any configured "
                    "extensions root and enabled (the profile entry decides; the manifest's "
                    "enabled= is only the default when the profile has none), with every "
                    "required dependency loading.")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check", help="analyze collisions across the enabled (active) "
                                      "modlist")
    pc.add_argument("candidate", nargs="?",
                    help="the mod to focus on ('before I add this'): an existing folder "
                         "PATH is the copy analysed (a same-named copy in the extensions "
                         "dir is then left out); a bare NAME means the copy in the "
                         "extensions dir. Omit it to analyse every enabled mod")
    pc.add_argument("--ext-dir", help="scan ONLY this extensions dir (default: every "
                    "configured root -- game-root extensions\\, the profile's extensions\\ "
                    "and the Steam Workshop folder, from _registry)")
    pc.add_argument("--reference", help="unpacked base+DLC reference tree ($X4_REFERENCE)")
    pc.add_argument("--soft", action="store_true", help="also list benign SOFT overlaps")
    pc.add_argument("--json", action="store_true", help="machine-readable output")

    args = p.parse_args(argv)

    # No --ext-dir: EVERY configured root (the set the engine loads). The game root
    # must still be configured -- without it there is no install to model.
    ext_dir = Path(args.ext_dir) if args.ext_dir else None
    check_dir = ext_dir if ext_dir is not None else _registry.require(
        _registry.GAME_EXTENSIONS, "the game extensions dir",
        "set X4_GAME (or X4_EXTENSIONS), or pass --ext-dir")
    if not check_dir.is_dir():
        print(f"error: extensions dir not found: {check_dir}", file=sys.stderr)
        return 2
    config = _merge.Config(reference=Path(args.reference)) if args.reference else _merge.Config()
    candidate = _resolve_candidate(args.candidate, ext_dir) if args.candidate else None
    if candidate is not None:
        _input.require_mod_dir(candidate, "candidate mod folder")

    report = analyze(ext_dir, candidate=candidate, config=config)

    if args.json:
        import dataclasses
        import json
        payload = {
            "mods_scanned": report.mods_scanned,
            "files_examined": report.files_examined,
            "load_order": report.load_order,
            "collisions": [dataclasses.asdict(c) for c in report.collisions],
            "skipped": [dataclasses.asdict(s) for s in report.skipped],
            "order_misses": [dataclasses.asdict(m) for m in report.order_misses],
            "removed_first": [dataclasses.asdict(m) for m in report.removed_first],
            # Issue #8: ops whose if= guard is false at their load position -- they take
            # part in no collision row, and this says so rather than dropping them quietly.
            # Each row: vpath, mod, line, sel, guard, shared_with (a list of mods; null =
            # UNKNOWN, never the same as [] = nobody), also_order_miss (the same op is in
            # order_misses too, whose rows carry guard_false -- one op, not two).
            "guarded_off": [dataclasses.asdict(g) for g in report.guarded_off],
            # NOT CHECKED (the text render's own section, above): an op whose sel=
            # could not be evaluated contributed no target and cannot participate in
            # collision detection -- omitting it here made a JSON consumer read
            # "clean" when the run had silently checked less than it claims.
            "unresolvable": report.unresolvable,
            "unapplied": report.unapplied,
            "candidate_path": report.candidate_path,
            "excluded_copies": report.excluded_copies,
            "scope_note": report.scope_note,
            "degraded": bool(report.degraded),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render(report, show_soft=args.soft))

    # Same contract as x4validate: 1 = real findings, 3 = a check could not run so a
    # clean result proves nothing, 0 = clean AND something was actually compared.
    # Findings outrank degradation — you fix what is known broken first.
    if report.hard:
        return 1
    return 3 if report.degraded else 0
