"""Reproduce X4's *effective* XML for a virtual path: base + DLC (+ mods).

Per-overlay strategy:
  - root <diff>                         -> apply add/replace/remove ops (pos/if/silent)
  - non-diff under a shared-registry
    dir (libraries/, index/, t/) with
    the same root tag as the base       -> UNION direct children (dedupe by @id/@name,
                                           later-overlay-wins) — engine merges these
  - any other non-diff root             -> full-file override (asset macro/component files)

The union case matters: every DLC ships libraries/ships.xml, character_macros.xml,
wares.xml, loadouts.xml, ... as full files, and the engine merges their entries with
the base. Treating them as full-file overrides (the old behavior) clobbered base-game
ships/macros out of the effective tree, producing phantom "sel matched nothing" errors
for any mod patching a base entry.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from lxml import etree

from x4validate import _cat, _loadorder, _paths
from x4validate._provenance import Origin, Recorder

# --- Workspace defaults (overridable via Config / --reference / $X4_REFERENCE) ---
# Keep this injectable: no hardcoded user path should be the only way to point at
# the reference tree (community-release portability).
#: None when nothing is configured. It used to fall back to the RELATIVE path
#: `reference`, i.e. whatever happened to sit under the current directory — so a
#: cold run validated against a tree that does not exist, reported every missing
#: base-game file as a mod finding, and exited 1 ("your mod is broken") when the
#: truth was "your toolkit is not set up". `ae79fcd` had already named
#: CWD-relative fallback as a defect; this was the last survivor.
REFERENCE = _paths.reference()
_PARSER = etree.XMLParser(remove_blank_text=False, recover=False, resolve_entities=False)


def _default_game_root() -> Path:
    """Where the LIVE game's DLC live, for DLC never unpacked into `reference/`.

    Delegates to `_paths`, which accepts the installer's `$X4_GAME` / `$X4_EXTENSIONS`,
    the legacy `$X4_GAME_ROOT` / `$X4_GAME_EXTENSIONS`, and `.claude/x4-paths.env`.
    Before that existed this read only the legacy names, so a user who configured the
    documented ones got packed-DLC support pointed at the wrong place — and it
    degrades quietly back to "cannot verify", which reads like the feature simply
    does not apply to them.

    Falls back to a non-existent relative path rather than raising: `dlc_dirs()`
    treats a missing game root as "no packed DLC", which is the honest answer when
    nothing is configured.
    """
    return _paths.game_root() or Path("x4-game-root-not-configured")


#: Read through `_cat`, never modified.
GAME_ROOT = _default_game_root()


def is_configured_reference(ref: Path | None) -> bool:
    """Is *ref* the reference tree this toolkit is configured for?

    The packed-DLC supplement may only be added on top of the CONFIGURED tree: a
    caller who passed an ad-hoc tree (a test fixture, a second checkout) asked for
    exactly that tree, and quietly folding the live game's DLC into it would answer
    a question nobody posed.

    `REFERENCE` stays a module global on purpose -- it is the monkeypatch handle
    four test modules and `conftest` use to simulate an unconfigured or empty
    install, so resolving it at call time here would leave those levers controlling
    nothing. What was wrong was the IDENTITY test: `config.reference != REFERENCE`
    compares raw `Path` objects, so two spellings of one directory -- a trailing
    separator, `./`, a relative path, or on a case-sensitive filesystem a differing
    case -- read as a DIFFERENT tree and silently dropped the supplement, with no
    line anywhere saying DLC had been left out. Inert on Windows, where `Path`
    comparison is already case-insensitive; this is the POSIX half, and X4 ships a
    Linux build.

    `_check.py` asked the same question with its own copy of the comparison. One
    implementation, because a comment in one file has never stopped the next file.
    """
    if ref is None or REFERENCE is None:
        return False
    try:
        return Path(ref).resolve() == Path(REFERENCE).resolve()
    except OSError:
        # silent-ok: an unanswerable question is not a yes. `resolve()` can raise
        # on a path whose parents are unreachable, and the ONLY consequence of
        # returning False is that the packed-DLC supplement is not added -- which
        # `check_packed_dlc_available` already reports to the user as its own
        # finding, so the omission has a channel and it is not this one.
        return False


@dataclass
class Config:
    #: Defaults to the configured reference tree, resolved when the Config is
    #: BUILT rather than when this class is defined. An import-time default would
    #: be a second snapshot of configuration (the shape that made `X4_EFFECTIVE_DB`
    #: unreadable from the config file) and would make the refusal below
    #: untestable, since the captured value survives any later change.
    reference: Path | None = None
    #: Tier B — other INSTALLED extension roots to merge in after DLC, in load
    #: order. Empty (Tier A) = base+DLC only, which cannot see content that
    #: another mod adds, removes or overrides. Threaded automatically into every
    #: build_effective() call that receives this Config.
    overlays: tuple[Path, ...] = ()
    #: The RUNTIME tree: every installed extension, including those loading AFTER
    #: the mod under test. `overlays` is truncated at the mod's own position, which
    #: is right for `sel=` (a node a later mod adds is genuinely not there yet) and
    #: wrong for existence questions (does macro X resolve?), which the engine
    #: answers once everything has loaded. Empty = "same as `overlays`", so Tier A
    #: and every existing caller are unaffected. Use via `for_runtime()`.
    final_overlays: tuple[Path, ...] = ()
    #: Also treat DLC that exist only PACKED in the live game install as part of
    #: the Tier A reference tree. Set False for a hermetic, reference-only run.
    include_packed_dlc: bool = True

    def __post_init__(self) -> None:
        """Resolve the reference tree, or REFUSE — never guess.

        The refusal fires only when the reference is UNRESOLVED. A caller that
        names a tree which happens not to exist is left alone on purpose:
        12 tests pass `reference=tmp_path / "does_not_exist"` because they
        exercise paths that never read it (`test_exprlint.py:80` says so in as
        many words). "You never told me where it is" and "the path you gave me is
        empty" are different questions, and only the first is a misconfiguration.
        """
        if self.reference is None:
            self.reference = REFERENCE
        if self.reference is None:
            raise _paths.Unconfigured(
                "no reference tree is configured. Set $X4_REFERENCE (or $X4_TOOLKIT) "
                "in the environment or in <toolkit>/x4-paths.env, or pass --reference. "
                "Refusing to guess: a relative fallback validates against a tree that "
                "does not exist and reports every base-game file as a mod error.")

    def for_runtime(self) -> "Config":
        """This config with the runtime tree swapped in as `overlays`.

        Returns `self` unchanged when no separate runtime tree was supplied, so a
        Tier A run and any caller predating the split behave exactly as before.
        """
        if not self.final_overlays or self.final_overlays == self.overlays:
            return self
        return replace(self, overlays=self.final_overlays)

    def dlc_dirs(self) -> list[Path]:
        """Every DLC root, unpacked-in-reference first, then packed-in-game.

        `reference/` only holds the DLC that were actually unpacked. The two
        mini-DLC (`ego_dlc_mini_01` Hyperion Pack, `ego_dlc_mini_02` Envoy Pack)
        never were — so a patch targeting their content used to report
        "installed but never unpacked — cannot verify". No unpack is needed to
        fix that: `_cat` reads their archives directly (55 and 104 XML members),
        which is exactly how `tools\\basex\\stage.py` already indexes them.

        A packed DLC is included only when reference/ does NOT already have it,
        so an unpacked copy always wins and this can never double-count.

        The supplement applies ONLY to the configured workspace reference
        (`REFERENCE` / `$X4_REFERENCE`), because that is the one tree we know
        mirrors this game install. A caller who passes some other `--reference`
        is deliberately isolating, and grafting the live game's DLC onto their
        tree would inject content that is not part of what they asked about.
        """
        ext = self.reference / "extensions"
        # Deterministic order; inter-DLC order is order-independent in practice
        # (DLC diffs guard with if="not(...)"). The ENGINE's folder key on every return
        # path: the two early returns below used a plain sort, which puts `_` before the
        # letters (release review, finding 9).
        dirs = (sorted((p for p in ext.iterdir()
                        if p.is_dir() and p.name.startswith("ego_dlc_")),
                       key=lambda p: _loadorder.sort_key(p.name))
                if ext.is_dir() else [])
        if not self.include_packed_dlc or not is_configured_reference(self.reference):
            return dirs
        have = {p.name.lower() for p in dirs}
        game_ext = GAME_ROOT / "extensions"
        if not game_ext.is_dir():
            return dirs
        packed = [p for p in game_ext.iterdir()
                  if p.is_dir() and p.name.startswith("ego_dlc_")
                  and p.name.lower() not in have and _cat.is_packed(p)]
        # ONE order across both kinds (AUDIT-2026-09-24 LO-6): packed DLC used to be
        # appended AFTER every unpacked one whatever their names, a second ordering rule
        # beside the load order. The engine's folder key is used for both.
        return sorted(dirs + packed, key=lambda p: _loadorder.sort_key(p.name))

    def packed_dlc_names(self) -> set[str]:
        """Lowercased names of DLC that exist only PACKED, outside reference/.

        Their content is reached through the DLC's own root (like any packed
        mod), NOT as a plain path under the reference tree — so a vpath of
        `extensions/<name>/<rel>` has to be resolved as OWNED by <name>. Getting
        this wrong turns an honest "cannot verify" into a false
        "no base game file" on every mod that patches them (measured: 4 mods).
        """
        ext = self.reference / "extensions"
        return {p.name.lower() for p in self.dlc_dirs()
                if not (ext / p.name).is_dir()}


@dataclass
class AppliedOp:
    tag: str
    sel: str
    line: int
    ok: bool
    detail: str
    silent: bool = False
    skipped_if: bool = False
    ambiguous: bool = False
    #: For each node `sel=` resolved to, the nearest ancestor-or-self carrying an
    #: `@id`, as (tag, id). EMPTY unless apply_diff was called with want_targets.
    #:
    #: Captured HERE, at resolution time, and not returned as elements: later ops in
    #: the same diff mutate the tree (gotcha #17), so a `<remove>` can detach the very
    #: node an earlier op selected and its ancestor chain is gone by the time the
    #: caller looks. The identity has to be taken while the node is still in place.
    #:
    #: Generic on purpose. `_merge` does not know what a ware is, and should not: the
    #: caller matches on the tag it cares about. This is what lets x4stats attribute
    #: `<replace sel="//ware[@id='ore']/@price_average">` to the ware `ore`, which no
    #: amount of reading the op PAYLOAD can do -- there is no <ware> element in it.
    target_keys: tuple = ()


@dataclass
class MergeResult:
    tree: etree._Element | None
    sources: list[str] = field(default_factory=list)  # which overlays contributed
    base_found: bool = False
    # Did the base come from the GAME (reference/ or a DLC layer), as opposed to from
    # another mod? `base_found` deliberately cannot answer that: it degrades to
    # "somebody supplied a tree", so under Tier B an installed mod's full file makes it
    # True and a bare-path cross-mod patch — which the engine never loads at all — looks
    # perfectly well-founded. Keeping the distinction here rather than recomputing it in
    # callers keeps ONE definition of "the game has this file", including the synthetic
    # <language> root for t/*.xml (see build_effective).
    base_from_game: bool = False
    # Work NOT done: overlays that could not be parsed and were left out of the tree.
    # Without this channel a malformed overlay is indistinguishable from an absent
    # one, and the resulting tree looks complete when it is not.
    skipped: list[str] = field(default_factory=list)


def parse_file(path: Path) -> etree._Element:
    return etree.parse(str(path), _PARSER).getroot()


def parse_bytes(data: bytes) -> etree._Element:
    return etree.fromstring(data, _PARSER)


def _loose_case_sensitive() -> bool:
    """True where a loose lookup by path is case-SENSITIVE and needs the folded index.

    Windows (`nt`) answers `is_file()` case-insensitively already, so it never builds or
    consults the index: Windows behaviour is unchanged by issue #6. Elsewhere the index is
    used; on a case-insensitive macOS volume it only ever confirms what `is_file()` found.
    """
    return os.name != "nt"


@lru_cache(maxsize=256)
def _folded_loose(odir_str: str) -> dict[str, tuple[str, ...]]:
    """Case-folded index of an overlay's LOOSE files: folded vpath -> on-disk spellings.

    Built once per overlay per process -- the same lifetime as `_cat._folded_vfs`, the
    packed half's index -- because a miss is the NORMAL outcome of `overlay_root`."""
    odir = Path(odir_str)
    index: dict[str, list[str]] = {}
    # An OVERLAY's loose half only; its packed half is `_cat.read_path`, already folded.
    for p in sorted(odir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(odir).as_posix()
            index.setdefault(rel.lower(), []).append(rel)
    return {k: tuple(v) for k, v in index.items()}


def overlay_root(odir: Path, vpath: str,
                 skipped: list[str] | None = None) -> etree._Element | None:
    """Resolve *vpath* within an overlay dir to a parsed root, or None if absent.

    Loose files take priority over the mod's packed catalog (they do in the engine
    too, and during development you edit loose). Falls back to reading the member
    from the mod's ext_*/subst_* catalogs so packed mods (e.g. VRO) are visible.

    A MALFORMED overlay file is recorded in *skipped* and treated as absent — it must
    be neither a crash nor a silent pass. Before 2026-07-26 the XMLSyntaxError escaped
    and killed the whole run: `cpsdo_faction/t/0001-l088.xml` has a mismatched tag, so
    validating `ship_variation_expansion_vro` (which touches the same vpath) died with
    a traceback. Exit code 1 made that indistinguishable from "found errors".
    Returning a bare None instead would be the opposite failure — a file the engine
    also refuses to load, silently counted as "nothing here".
    """
    loose = odir / vpath
    try:
        if _loose_case_sensitive():
            # Linux parity (issue #6). The engine reads virtual paths case-insensitively;
            # `is_file()` here does not, so a loose `md/CaseProbe.xml` asked for as
            # `md/caseprobe.xml` would be silently absent. Fold case -- and REFUSE a
            # folder holding two spellings of one file: which one the engine loads is
            # not measured, so neither is picked. (Behaviour on Linux is INFERRED from
            # the lookup, not observed in a running Linux game.)
            spellings = _folded_loose(str(odir)).get(vpath.replace("\\", "/").lower(), ())
            if len(spellings) > 1:
                raise ValueError(f"{len(spellings)} loose files differ only by case "
                                 f"({', '.join(spellings)}); the engine reads them as one "
                                 f"file and which one it loads is not measured")
            if not loose.is_file() and spellings:
                loose = odir / spellings[0]
        if loose.is_file():
            return parse_file(loose)
        data = _cat.read_path(odir, vpath)
        if data is not None:
            return parse_bytes(data)
    except etree.XMLSyntaxError as exc:
        # NO CHANNEL, NO SWALLOW — the SAME rule the OSError/ValueError branch below
        # states and obeys, which this branch did not. It returned None whether or
        # not a `skipped` list was passed, so for the SIX call sites that pass none
        # (`_compat` x2, `_diff` x2, `_stats` x2) a malformed document became a
        # silent "absent".
        #
        # MEASURED 2026-09-08 on two fixtures differing ONLY by a `--` inside an XML
        # comment: the well-formed one reports the ware and its price; the malformed
        # one prints "candidate introduces/changes no wares." at rc 0. The v3.1.0 arc
        # RECRUITS for this — it rebuilt `_stats` around that exact sentence and added
        # a guard at _stats.py:445 reading "AN ABSENCE AND A NON-ANSWER MUST NOT PRINT
        # THE SAME SENTENCE", and the malformed path walks around that guard. The
        # release therefore made the wrong answer more authoritative than it was.
        #
        # (The comment below says FIVE callers; measured, it is six — `_stats` has two
        # call sites, 172 and 228. A denominator in prose, counted once and not again.)
        # (RE-COUNTED 2026-09-26: SEVEN call sites pass no `skipped` -- `_compat` x2,
        # `_diff` x2, `_stats` x3. Both counts above were history by then; grep
        # `overlay_root(` rather than trusting any of the three.)
        if skipped is None:
            raise
        skipped.append(f"{odir.name}/{vpath}: malformed XML, overlay skipped ({exc})")
        return None
    except (OSError, ValueError) as exc:
        # The OTHER half of "neither a crash nor a silent pass". Only
        # XMLSyntaxError was routed here, so a bad ARCHIVE -- rather than bad XML
        # inside a good one -- still escaped and killed the run, which is the exact
        # failure this channel was built in 2026-07-26 to stop, surviving in the
        # branch nobody reproduced. `_cat.read_member` raises OSError for a
        # truncated or unreadable .dat member and ValueError for a malformed
        # catalog line; `_scan.iter_mod_xml_bytes` has caught precisely this pair
        # from the same call since it was written, so this is a divergence between
        # two readers of one source, not a new claim about what can be raised.
        # `parse_file` can raise OSError too: `loose.is_file()` is a separate
        # syscall from the read, and a mod folder can move between them.
        #
        # NO CHANNEL, NO SWALLOW. The first cut of this handler returned None
        # whether or not a `skipped` list was passed, and FIVE callers pass none
        # (`_compat` x2, `_diff` x2, `_stats`). For them a corrupt archive went
        # from a loud crash to a silent "absent": x4stats printed "candidate
        # introduces no wares" with rc 0, and `_diff.read_merged` built its
        # baseline from the good layers while its own `unreadable` list stayed
        # EMPTY. That is the narrowing-step-that-reports-success shape, introduced
        # by a fix for the same shape. MEASURED by the 2026-09-05 delta review on
        # a mod with a truncated .dat, pre-fix vs fixed. So: a caller that gave us
        # somewhere to record the failure gets it recorded; a caller that did not
        # gets the exception, exactly as before this handler existed.
        if skipped is None:
            raise
        skipped.append(f"{odir.name}/{vpath}: unreadable, overlay skipped ({exc})")
        return None
    return None


def _truthy(val: str | None) -> bool:
    return str(val).lower() in {"true", "1", "yes"}


# --- Diff application ---------------------------------------------------------

_OPS = {"add", "replace", "remove"}


def _scalar_kind(value) -> str:
    """A name a mod author recognises. lxml hands back `_ElementUnicodeResult` for a
    string result, which names an implementation detail rather than the mistake."""
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    return type(value).__name__


def _id_key(node) -> tuple | None:
    """(tag, @id) of the nearest ancestor-or-self carrying an id, or None.

    Handles an ATTRIBUTE target too: `sel="//ware[@id='ore']/@price_average"` resolves
    to an lxml smart string, whose getparent() is the element that owns it. Reading
    only element targets would miss the single most common X4 patch idiom -- it is the
    example in this project's own CLAUDE.md.
    """
    el = node if hasattr(node, "tag") else getattr(node, "getparent", lambda: None)()
    while el is not None and hasattr(el, "tag"):
        if isinstance(el.tag, str) and el.get("id"):
            return (el.tag, el.get("id"))
        el = el.getparent()
    return None


def apply_diff(tree: etree._Element, diff_root: etree._Element,
               recorder: Recorder | None = None, source: str = "",
               want_targets: bool = False) -> list[AppliedOp]:
    """Apply every op in *diff_root* to *tree* in document order. Mutates tree.

    With *recorder*, every mutation is stamped with an Origin(source, op, line)
    at mutation time (see _provenance for the identity model)."""
    # The third positional is `recorder`, not `source`, and passing a str for it
    # failed FOUR frames down inside _do_replace/_record with "'str' object has no
    # attribute 'elem_replaced'" -- a message naming an internal protocol method
    # rather than the bad argument. Worse, WHICH method it named depended on which
    # op the diff happened to contain, and a diff of pure <remove> ops recorded
    # nothing and sailed through. Fail here, naming both parameters.
    if recorder is not None and not hasattr(recorder, "elem_created"):
        raise TypeError(
            f"apply_diff(recorder=...) expects a _provenance.Recorder (or None), got "
            f"{type(recorder).__name__}; the third positional is `recorder` — if you "
            f"meant the filename, pass it as source=...")
    applied: list[AppliedOp] = []
    for op in diff_root:
        # Comments and processing instructions are legitimately not ops — skip
        # them silently. An ELEMENT that is not add/replace/remove is a different
        # thing: libraries/diff.xsd admits exactly those three and nothing else,
        # so <relace sel="..."> or <modify> is a schema violation the engine
        # ignores. Lumping both into one silent `continue` meant a typo'd op tag
        # vanished without a trace and the file still reported OK.
        if not isinstance(op.tag, str):
            continue
        if op.tag not in _OPS:
            applied.append(AppliedOp(op.tag, op.get("sel", ""), op.sourceline or 0, False,
                                     f"unknown op <{op.tag}> — diff.xsd allows only "
                                     f"{'/'.join(sorted(_OPS))}; the engine ignores it",
                                     _truthy(op.get("silent"))))
            continue
        sel = op.get("sel", "")
        line = op.sourceline or 0
        silent = _truthy(op.get("silent"))

        # if= gate: evaluate against the current tree; falsy -> skip the op.
        cond = op.get("if")
        if cond:
            try:
                if not tree.xpath(cond):
                    applied.append(AppliedOp(op.tag, sel, line, True, "if= false: skipped",
                                             silent, skipped_if=True))
                    continue
            except etree.XPathEvalError as exc:
                applied.append(AppliedOp(op.tag, sel, line, False, f"invalid if=: {exc}", silent))
                continue

        try:
            targets = tree.xpath(sel)
        except etree.XPathEvalError as exc:
            applied.append(AppliedOp(op.tag, sel, line, False, f"invalid sel=: {exc}", silent))
            continue

        # `xpath()` returns a LIST for a node selector but a float / bool / string for
        # an expression -- `count(//ware)`, `true()`, `string(//ware/@id)`. Falling
        # through to `len(targets)` produced two failures from one cause, and the
        # quieter one is the worse of the two:
        #   count(...)  -> TypeError, a raw traceback at rc 1 -- the SAME rc as "your
        #                  mod has errors" -- and `--json` emitting 0 bytes
        #   string(...) -> "sel matched 18 nodes", where 18 is the LENGTH OF THE
        #                  STRING. No crash, a confident wrong number.
        # A falsey scalar (`count(//nope)` is 0.0, `false()` is False) also has to be
        # caught HERE: the `if not targets` branch below would otherwise report a
        # malformed selector as an empty match, which is a different finding.
        if not isinstance(targets, list):
            applied.append(AppliedOp(
                op.tag, sel, line, False,
                f"sel= is not a node-set: it evaluates to "
                f"{_scalar_kind(targets)} ({targets!r}). An X4 patch selector must "
                f"select nodes, not compute a value", silent))
            continue

        if not targets:
            applied.append(AppliedOp(op.tag, sel, line, False, "sel matched nothing", silent))
            continue

        # RFC 5261: sel must select exactly ONE node. X4 enforces this — it logs
        # "Multiple matching nodes for path '<sel>' in patch file '<f>'. Skipping
        # node." and applies NOTHING. Modelling that faithfully matters: applying
        # to every match instead would produce an effective tree the game never
        # has (e.g. a material added to both collections named "map").
        if len(targets) > 1:
            applied.append(AppliedOp(op.tag, sel, line, False,
                                     f"sel matched {len(targets)} nodes — engine skips "
                                     "ambiguous ops (RFC 5261: must match exactly one)",
                                     silent, ambiguous=True))
            continue

        # A TEXT NODE target (`.../text()`). lxml returns it as a smart string that is
        # neither an element nor an attribute, and every helper below assumed one or the
        # other -- replace raised AttributeError, remove and add raised TypeError, as a
        # raw traceback at rc 1 (AUDIT-2026-09-24 MG-1). RFC 5261 allows text targets, but
        # how X4 applies them is unmeasured (0 of ~7.1k installed selectors use one), so
        # this REFUSES with a reason rather than guessing a result. Comments and
        # processing instructions are elements to lxml and are handled as before.
        tgt = targets[0]
        if not isinstance(tgt, etree._Element) and not _is_attr(tgt):
            applied.append(AppliedOp(
                op.tag, sel, line, False,
                "sel selects a text node (text()); how the engine applies a patch to a "
                "text node is not modelled, so the result is not predicted", silent))
            continue

        # An <add type=...> we do not implement must not be reported as applied.
        # Anything other than RFC 5261's "@attr" form (e.g. a namespace add) would
        # silently change nothing here while the engine acts on it.
        typ = op.get("type", "")
        if op.tag == "add" and typ and not typ.startswith("@"):
            applied.append(AppliedOp(op.tag, sel, line, False,
                                     f"unsupported add type={typ!r} — only RFC 5261 "
                                     "type=\"@attr\" is modelled", silent))
            continue

        # BEFORE the helpers run, because `_do_remove` detaches its target and a
        # detached node has no ancestor chain left to climb (gotcha #17: the ops
        # apply IN ORDER to a mutating tree). Opt-in, so the corpus-wide store build
        # -- which never asks for this -- pays nothing for it.
        keys = ()
        if want_targets:
            keys = tuple(k for k in (_id_key(t) for t in targets) if k is not None)

        origin = Origin(source, op.tag, line) if recorder is not None else None
        # The helpers return a reason string when they could NOT apply the op, else
        # None. Deriving AppliedOp.ok from that (rather than hard-coding True) is
        # what keeps a silent no-op from being reported as success — the failure
        # class that hid 858 dropped ops behind "applied=True".
        if op.tag == "remove":
            reason = _do_remove(targets, recorder, origin)
        elif op.tag == "replace":
            reason = _do_replace(targets, op, recorder, origin)
        elif op.tag == "add":
            reason = _do_add(targets, op, recorder, origin)
        else:
            reason = None
        applied.append(AppliedOp(op.tag, sel, line, reason is None,
                                 reason or f"{len(targets)} target(s)", silent,
                                 target_keys=keys))
    return applied


def _is_attr(node) -> bool:
    return getattr(node, "is_attribute", False)


def _path_of(node) -> str:
    """Display path for a node/attribute at this instant (removal records only)."""
    if _is_attr(node):
        parent = node.getparent()
        base = parent.getroottree().getpath(parent) if parent is not None else ""
        return f"{base}/@{node.attrname}"
    return node.getroottree().getpath(node)


def _do_remove(targets, recorder: Recorder | None = None,
               origin: Origin | None = None) -> str | None:
    for t in targets:
        parent = t.getparent()
        # The record is written AFTER the refusal below, not before it. A
        # root-targeted <remove> is correctly reported as NOT applied, and the
        # recorder was nonetheless handed a provenance row for it -- the two
        # channels of one op disagreeing, in the module whose job is to make an
        # unapplied op say why. No current mod does this, so it is a latent
        # divergence rather than a wrong number on disk today.
        if parent is None:
            # No current mod does this, but a bare skip here would be the same
            # silent-no-op class as the root <replace> was. Report it instead.
            return ("remove targets the document root — a document cannot be "
                    "left without one")
        if recorder is not None:
            recorder.node_removed(_path_of(t), origin)
        if _is_attr(t):
            parent.attrib.pop(t.attrname, None)
        else:
            _keep_tail(t)
            parent.remove(t)
    return None


def _keep_tail(t) -> None:
    """Hand *t*'s tail text to what precedes it, because lxml's `remove` drops the tail
    WITH the element -- the text AFTER a removed or replaced node is not part of it
    (AUDIT-2026-09-24 MG-5; whitespace-only in real data, but a text node the engine
    keeps must not vanish from the effective tree)."""
    if not t.tail:
        return
    prev = t.getprevious()
    parent = t.getparent()
    if prev is not None:
        prev.tail = (prev.tail or "") + t.tail
    elif parent is not None:
        parent.text = (parent.text or "") + t.tail
    t.tail = None


def _do_replace(targets, op, recorder: Recorder | None = None,
                origin: Origin | None = None) -> str | None:
    new_children = [c for c in op if isinstance(c.tag, str)]
    for t in targets:
        if _is_attr(t):
            parent = t.getparent()
            if parent is None:
                return "attribute target has no owning element"
            if new_children:
                # An attribute can only hold text. Given element children we used
                # to set it to `op.text or ""` — silently BLANKING the attribute
                # and discarding the payload, then reporting success. Found by
                # gates/fuzz_diff.py, which flagged the case where the attribute
                # was already empty so "applied" changed nothing at all.
                return ("replace on an attribute takes text, but the op carries "
                        f"{len(new_children)} element(s)")
            parent.set(t.attrname, op.text or "")
            if recorder is not None:
                recorder.attr_set(parent, t.attrname,
                                  Origin(origin.source, "replace-attr", origin.line))
        elif new_children:
            parent = t.getparent()
            if parent is None:
                # The target IS the document root. lxml cannot swap a root through
                # its parent, so mutate it in place — the ElementTree identity and
                # every caller's handle stay valid. The engine DOES apply these:
                # `<replace sel="//macros">` is the standard whole-file override
                # idiom (VRO alone ships 848 of them) and X4 logs no complaint,
                # while it does log every other patch failure. Before this, the op
                # was dropped with a bare `continue` yet still reported applied.
                if len(new_children) != 1:
                    return (f"replace on the document root needs exactly one payload "
                            f"element (a document has one root), got {len(new_children)}")
                nc = copy.deepcopy(new_children[0])
                tail = t.tail
                t.clear()
                t.tag = nc.tag
                t.text, t.tail = nc.text, tail
                for k, v in nc.attrib.items():
                    t.set(k, v)
                t.extend(list(nc))
                if recorder is not None:
                    # Semantically identical to a full-file override: all prior
                    # lineage is gone and this source becomes the default origin.
                    recorder.full_override(Origin(origin.source, "replace-root", origin.line))
                continue
            idx = parent.index(t)
            tail = t.tail
            parent.remove(t)
            for off, child in enumerate(new_children):
                new = copy.deepcopy(child)
                if off == len(new_children) - 1:
                    # The replaced node's tail belongs AFTER the replacement, not to the
                    # payload's own formatting whitespace (MG-5).
                    new.tail = tail
                parent.insert(idx + off, new)
                if recorder is not None:
                    if off == 0:  # first inserted child carries the replaced node's lineage
                        recorder.elem_replaced(t, new, origin)
                    else:
                        recorder.elem_created(new, origin)
        else:
            # Replace element's inner text/content.
            t.text = op.text
            for c in list(t):
                t.remove(c)
            if recorder is not None:
                recorder.elem_created(t, Origin(origin.source, "replace-text", origin.line),
                                      prior_chain=recorder.elem_chain(t))
    return None


def _do_add(targets, op, recorder: Recorder | None = None,
            origin: Origin | None = None) -> str | None:
    pos = op.get("pos", "")
    typ = op.get("type", "")
    new_children = [c for c in op if isinstance(c.tag, str)]

    def _record(new):
        if recorder is not None:
            recorder.elem_created(new, origin)

    # RFC 5261 §4.3: type="@name" adds an ATTRIBUTE to each target, valued from the
    # op's text. X4 supports this and installed mods rely on it (da_ku_ai_tweaks,
    # axes10k20kscanshiprange). It is the right tool when another mod owns a sibling
    # attribute you must not clobber — a whole-node <replace> would bake in whatever
    # value happened to be winning at authoring time.
    #
    # Without this branch the op fell through to "append children"; with no element
    # children to append it mutated nothing yet still reported "1 target(s)" — a
    # false OK on precisely the silent-no-op class this tool exists to catch.
    if typ.startswith("@"):
        name = typ[1:]
        if not name:
            return "add type=\"@\" names no attribute"
        if new_children:
            # Mirrors `_do_replace`: an attribute holds text. With element children this
            # set the attribute to `op.text or ""` -- BLANKING it -- dropped the payload
            # and reported success (AUDIT-2026-09-24 MG-2).
            return ("add type=\"@\" takes text, but the op carries "
                    f"{len(new_children)} element(s)")
        for t in targets:
            if _is_attr(t):
                return "cannot hang an attribute off an attribute"
            t.set(name, op.text or "")
            if recorder is not None and origin is not None:
                recorder.attr_set(t, name, Origin(origin.source, "add-attr", origin.line))
        return None

    # A TEXT-ONLY payload (`<add sel="...">text</add>`) is an RFC 5261 text-node add. It
    # changed nothing here while reporting success (AUDIT-2026-09-24 MG-2); 6 installed
    # ops ship it. Modelled at the same positions an element would take. A payload with
    # neither elements nor text (88 installed ops, plus 2 holding only comments) adds
    # nothing -- in the engine too -- and stays an applied no-op.
    text = op.text if (op.text or "").strip() else None
    if text is not None and not new_children:
        for t in targets:
            if _is_attr(t):
                return "add cannot target an attribute"
            if pos in {"before", "after"} and t.getparent() is None:
                return (f"add pos={pos!r} targets the document root, which has no "
                        "siblings")
            # A separate name (`where`) on purpose: gates/mutation_probe.py anchors its
            # prepend mutant on the ELEMENT path's exact source line, which must stay unique.
            where = pos or "append"
            if where == "prepend":
                t.text = text + (t.text or "")
            elif where == "before":
                prev = t.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + text
                else:
                    t.getparent().text = (t.getparent().text or "") + text
            elif where == "after":
                t.tail = text + (t.tail or "")
            elif len(t):
                t[-1].tail = (t[-1].tail or "") + text
            else:
                t.text = (t.text or "") + text
        return None

    for t in targets:
        if _is_attr(t):
            return "add cannot target an attribute"
        if pos == "prepend":
            for i, child in enumerate(new_children):
                new = copy.deepcopy(child)
                t.insert(i, new)
                _record(new)
        elif pos in {"before", "after"}:
            parent = t.getparent()
            if parent is None:
                return (f"add pos={pos!r} targets the document root, which has no "
                        "siblings")
            idx = parent.index(t) + (1 if pos == "after" else 0)
            for off, child in enumerate(new_children):
                new = copy.deepcopy(child)
                parent.insert(idx + off, new)
                _record(new)
        else:  # append (default)
            for child in new_children:
                new = copy.deepcopy(child)
                t.append(new)
                _record(new)
    return None


# --- Effective-tree assembly --------------------------------------------------

# Shared-registry dirs whose same-rooted full files the engine MERGES by entry
# (base + every DLC coexist). Asset files (assets/...) keep full-override semantics.
_ADDITIVE_DIRS = ("libraries/", "index/", "t/")


def _is_text_vpath(vpath: str) -> bool:
    """A language file, in any spelling.

    Case-INSENSITIVE because X4 vpaths are: the corpus genuinely mixes case and
    Windows resolves either spelling to the same file, so `T/0001.xml` and
    `t/0001.xml` are one file to the engine. A case-sensitive test made the two take
    different merge paths -- and on Linux (CI) the filesystem does not forgive it
    either, so the same mod that merely merged oddly here would fail outright there.
    """
    low = vpath.lower()
    return low.startswith("t/") and low.endswith(".xml")

#: Dirs where the engine keys its script registry on FILENAME, so a complete
#: (non-diff) file at an already-supplied vpath is INERT rather than an override.
#:
#: ⚠ Deliberately `md/` ONLY. That is where the rule is engine-proven (F27, a
#: controlled experiment on 2026-08-22). `aiscripts/` is *believed* to behave the
#: same way, but MEASURED over 115 installed mods / 4,391 XML files there are
#: **zero** complete-file-at-a-vanilla-vpath instances under `aiscripts/` — so
#: there is nothing to test the generalisation against and no observable
#: difference either way. Widening this tuple without an instance to verify would
#: encode an inference as a fact, which is what BLIND-SPOTS exists to prevent.
#: `gates/tool_properties.py` carries a tripwire that fails the moment a real
#: `aiscripts/` instance appears in the corpus.
_SCRIPT_REGISTRY_DIRS = ("md/",)


def _child_key(el: etree._Element) -> tuple[str, str] | None:
    """Dedupe key for a registry entry: (tag, id|name). None if neither attr."""
    for attr in ("id", "name"):
        v = el.get(attr)
        if v is not None:
            return (el.tag, f"{attr}={v}")
    return None


def _union_children(tree: etree._Element, overlay: etree._Element,
                    recorder: Recorder | None = None, source: str = "") -> None:
    """Merge *overlay*'s direct children into *tree* in place.

    Dedupe by _child_key with later-overlay-wins (same id/name -> overlay's element
    replaces the base's); keyless children (e.g. <defaults>, comments) are appended.
    Correct for BOTH additive registries (distinct ids coexist) and a same-vpath
    single-file override (same id/name -> replaced)."""
    index: dict[tuple[str, str], etree._Element] = {}
    for child in tree:
        if isinstance(child.tag, str):
            key = _child_key(child)
            if key is not None:
                index[key] = child
    for child in overlay:
        if not isinstance(child.tag, str):  # comments / PIs
            continue
        key = _child_key(child)
        new = copy.deepcopy(child)
        if key is not None and key in index:
            old = index[key]
            tree.replace(old, new)
            if recorder is not None:
                recorder.elem_replaced(old, new, Origin(source, "union-replace"))
        else:
            tree.append(new)
            if recorder is not None:
                recorder.elem_created(new, Origin(source, "union-add"))
        if key is not None:
            index[key] = new


def _registry_rel(vpath: str) -> str:
    """The path a MERGE-KIND test must see: the document's path within its OWNER.

    `_ADDITIVE_DIRS` and `_SCRIPT_REGISTRY_DIRS` are prefixes of a document's path
    inside the extension that owns it -- `libraries/`, `index/`, `t/`, `md/`. Two of
    the three call sites below hand `apply_overlay` a vpath that still carries an
    `extensions/<owner>/` prefix, and `extensions/ego_dlc_boron/libraries/rooms.xml`
    does not start with `libraries/`. So the union branch was skipped and the base
    document was FULL-OVERRIDDEN -- discarded -- for every shared registry file
    addressed through an owner-nested path.

    MEASURED 2026-09-05 on the live corpus (125 active mods, packed-inclusive):
        vpath                                    base   effective
        ego_dlc_boron/libraries/rooms.xml          19       2
        ego_dlc_boron/libraries/roomgroups.xml     19       2
        ego_dlc_split/libraries/rooms.xml          18       2
        ego_dlc_split/libraries/roomgroups.xml     18       2
        ego_dlc_terran/libraries/rooms.xml         19       2
        ego_dlc_terran/libraries/roomgroups.xml    18       2
    99 base entries discarded, across 6 of the 6 real instances in the corpus (the
    other nested library files are diffs, which take the `oroot.tag == "diff"`
    branch first and are unaffected). With the prefix stripped the first becomes 21
    entries -- 19 + 2 -- and the mode flips `full` -> `union`.

    This also made the TWO DOORS to one logical document disagree, which is the very
    thing the note at the nested loop says was closed in v2.2.1: the plain door
    strips nothing because the REQUESTED vpath is already bare, so it unioned, while
    the nested door full-overrode the same pair.

    ⚠ The sibling of `d368bf3`, this round's own fix for "a mixed-case additive vpath
    made every DLC layer REPLACE instead of union". Same branch, same consequence,
    one variant away -- and that fix's test pinned CASE, not PREFIX.
    """
    parts = vpath.split("/")
    if len(parts) >= 3 and parts[0].lower() == "extensions":
        return "/".join(parts[2:])
    return vpath


def apply_overlay(
    tree: etree._Element | None,
    oroot: etree._Element,
    vpath: str,
    source: str,
    recorder: Recorder | None = None,
) -> tuple[etree._Element | None, str]:
    """Apply one overlay root onto *tree*; returns (new tree, mode).

    Mode is one of "diff", "diff(no-base!)", "union", "script(inert)", "full" —
    mirrors the source-tag suffixes recorded by build_effective. Extracted so callers other
    than build_effective (e.g. x4effective's mod-owned-base path) reuse the exact
    dispatch semantics."""
    if oroot.tag == "diff":
        if tree is None:
            return None, "diff(no-base!)"  # diff with no base — cannot apply
        apply_diff(tree, oroot, recorder=recorder, source=source)
        return tree, "diff"
    # The OWNER-RELATIVE path, never the requested one -- see _registry_rel.
    _kind = _registry_rel(vpath).lower()
    if (tree is not None and oroot.tag == tree.tag
            and _kind.startswith(_ADDITIVE_DIRS)):
        _union_children(tree, oroot, recorder=recorder, source=source)
        return tree, "union"
    if tree is not None and _kind.startswith(_SCRIPT_REGISTRY_DIRS):
        # F27 — the engine registers MD scripts by FILENAME, and a duplicate
        # filename is DISCARDED. A complete <mdscript> at a vpath something
        # already supplied never runs: no override, no merge, no error line.
        #
        # ENGINE-PROVEN 2026-08-22 by a controlled pair differing in one variable
        # (same cue structure, same actions, same load position, both script
        # name= differing from vanilla's `Setup`):
        #   md/setup_probe_unique.xml (new vpath)      -> registers, cues run
        #   md/setup.xml              (vanilla vpath)  -> never takes effect
        # Modelling it as a full override made `x4effective dump md/setup.xml`
        # return a mod's 44-line file while the engine was demonstrably running
        # vanilla's 1,795-line `Setup`.
        #
        # Note the test is `tree is not None`, not "vanilla owns it": first
        # registration wins whoever got there first, so a second mod shipping the
        # same script filename is inert for the same reason.
        return tree, "script(inert)"
    if recorder is not None:
        recorder.full_override(Origin(source, "full-override"))
    return oroot, "full"  # full-file override (asset files, or no/other base)


def _nested_target(vpath: str, owned_dlc: set[str] | None = None) -> tuple[str, str] | None:
    """'extensions/<target>/<rel>' -> ('<target>', '<rel>'); else None.

    This is the cross-mod patch idiom: the path is owned by <target>, not by the
    base game. An UNPACKED `ego_dlc_*` is excluded — it lives under
    reference/extensions/ and is already ordinary base content.

    *owned_dlc* names the DLC that exist only packed in the live install (see
    `Config.packed_dlc_names`). Those have no reference/ copy, so their content
    has to be reached through their own root exactly like a packed mod's.
    """
    parts = vpath.split("/")
    if len(parts) < 3 or parts[0].lower() != "extensions":
        return None
    if parts[1].lower().startswith("ego_dlc_") and parts[1].lower() not in (owned_dlc or set()):
        return None
    return parts[1], "/".join(parts[2:])


def _build_owned(owner_name: str, rel: str, vpath: str,
                 overlay_dirs: list[Path],
                 recorder: Recorder | None,
                 owner_is_dlc: bool = False) -> MergeResult:
    """Merge a file OWNED by another mod: that mod supplies the base at *rel*,
    every other mod patches it at the nested *vpath*.

    *owner_is_dlc* marks a packed-only DLC owner, where a `<diff>` root IS the
    base. Every DLC ships `libraries/god.xml` as a diff, and for an UNPACKED DLC
    the ordinary reference-path branch already uses that diff as the base tree.
    Refusing it here purely because the DLC happens to be packed would make the
    same mod's same patch an ERROR for mini_01 and fine for split — a difference
    with no counterpart in the engine. (A third-party MOD's diff at *rel* is a
    different matter and still not a base: it is a patch, and the file it patches
    lives elsewhere.)"""
    sources: list[str] = []
    skipped: list[str] = []
    tree: etree._Element | None = None

    owner_dir = next((d for d in overlay_dirs if d.name.lower() == owner_name.lower()), None)
    if owner_dir is not None:
        oroot = overlay_root(owner_dir, rel, skipped)
        if oroot is not None and (owner_is_dlc or oroot.tag != "diff"):
            tree = oroot
            sources.append(f"{owner_dir.name}:owner")

    for odir in overlay_dirs:
        if odir is owner_dir:
            continue
        oroot = overlay_root(odir, vpath, skipped)
        if oroot is None:
            continue
        tree, mode = apply_overlay(tree, oroot, vpath, odir.name, recorder=recorder)
        if recorder is not None and mode != "full":
            recorder.file_chain.append(Origin(odir.name, mode))
        sources.append(f"{odir.name}:{mode}")

    # A nested patch's base legitimately comes from another MOD, so "from the game"
    # is only true for a packed-DLC owner. No caller keys the inert-bare-path check
    # off this branch (it requires a non-nested path), but leaving the field False
    # for a DLC owner would be a lie waiting for the next caller.
    return MergeResult(tree=tree, sources=sources, base_found=tree is not None,
                       base_from_game=owner_is_dlc and tree is not None,
                       skipped=skipped)


def build_effective(
    virtual_path: str,
    config: Config | None = None,
    extra_overlays: list[Path] | None = None,
    recorder: Recorder | None = None,
) -> MergeResult:
    """Build the effective tree for *virtual_path* = base + DLC + extra_overlays.

    *extra_overlays* are extension ROOT dirs (e.g. a mod under test, or Tier-B
    enabled mods) applied in the given order, after all DLC.
    """
    config = config or Config()
    vpath = virtual_path.replace("\\", "/").lstrip("/")
    sources: list[str] = []
    skipped: list[str] = []

    # DLC, then the Tier-B installed set (load order), then any explicit extras
    # (e.g. the mod under test) last.
    dlc_layers = config.dlc_dirs()
    overlay_dirs = dlc_layers + list(config.overlays) + list(extra_overlays or [])

    # Cross-mod nesting: a file at  <mymod>/extensions/<target>/<rel>  patches
    # <target>'s OWN <rel>. The base therefore lives inside <target>, not in
    # reference/. Without this, every such patch reports "no base game file"
    # even though the engine applies it (proven: ship_variation_expansion_vro
    # patches ship_variation_expansion this way and its ops take effect).
    owner_rel = _nested_target(vpath, config.packed_dlc_names())
    if owner_rel is not None:
        owner_name, rel = owner_rel
        return _build_owned(owner_name, rel, vpath, overlay_dirs, recorder,
                            owner_is_dlc=owner_name.lower() in config.packed_dlc_names())

    base_path = config.reference / vpath
    tree: etree._Element | None = None
    base_found = base_path.is_file()
    # Which layers count as "the game has this file" — see MergeResult.base_from_game.
    game_dirs = {d.resolve() for d in dlc_layers}
    # The ENGINE always supplies a language tree at t/*.xml, so a diff there is
    # well-founded no matter who else ships the path — this keys off the PATH, never
    # off `tree is None`. Getting that wrong is not theoretical: the first cut set it
    # after the loop inside the tree-is-None branch, and any mod whose t/ file another
    # mod also ships (which fills `tree` first) was then reported as an inert
    # bare-path patch. 33 of the 101 installed mods ship a t/ diff — the single
    # largest false-positive class this distinction exists to avoid.
    is_text_file = _is_text_vpath(vpath)
    from_game = base_found or is_text_file
    if base_found:
        tree = parse_file(base_path)
        sources.append("base")
    elif is_text_file:
        # BEFORE the overlay loop, not after it. `t/0001.xml` has no base file in
        # reference -- the engine overlays t-files onto the language tree -- so a
        # mod's t-DIFF needs a `<language>` root to resolve its /language-targeted
        # ops against. This synthesis used to run AFTER the loop, by which time
        # apply_overlay had already returned `diff(no-base!)` and DROPPED the ops,
        # and the comment explaining why the root exists sat beside a root that
        # could no longer serve anyone.
        #
        # MEASURED 2026-09-05, two mods each shipping a t/0001.xml diff and no base:
        #     sources ['A:diff(no-base!)', 'B:diff(no-base!)', 'synthetic:language']
        #     tree <language>, pages []   base_found True, skipped EMPTY
        # A well-formed empty answer with nothing anywhere saying two overlays' ops
        # had been discarded. 62 of 125 active mods ship t/0001.xml (40 as diffs), and
        # the loss is load-order dependent: today exactly 1 is hit, because a complete
        # file at index 1 happens to supply a tree for everyone after it.
        tree = etree.Element("language")
        sources.append("synthetic:language")

    # Overlays that SUPPLIED this file (full/union): later mods may patch the same
    # logical file at the NESTED path `extensions/<that folder>/<vpath>`. Those
    # nested patches used to be invisible from this door — build_effective(plain
    # vpath) consulted only `odir/<vpath>`, while build_effective(nested vpath)
    # merged owner+patches via _build_owned. Two doors to one logical file, giving
    # two different answers: Tier B (nested door) said cpsdo_vro's 27 bullet
    # overrides resolve; the store (this door) said cpsdo_zb_modpack still owns
    # every value. The engine has ONE document, and F19's debug.txt evidence shows
    # nested ops ARE evaluated — so this door was the wrong one.
    owner_folders: list[str] = []

    for odir in overlay_dirs:
        oroot = overlay_root(odir, vpath, skipped)
        if oroot is not None:
            # The engine only loads a bare-path <diff> when the GAME supplies the
            # file; over another mod's file it never even opens it (proven from
            # debug.txt's per-file signature lines — same rel path, owner logged,
            # nested patcher logged with its op evaluated, bare-path patcher
            # absent). Applying it anyway put 14 attribute values in the effective
            # tree that the engine never sees. By this point `from_game` is FINAL
            # for mod overlays: reference/ and t/ are decided upfront and DLC
            # layers always precede mods in overlay_dirs, so the refusal is
            # load-order-independent. DLC-shipped diffs are exempt — the engine
            # trusts its own content, and a DLC diff with no base is already the
            # diff(no-base!) case.
            if (oroot.tag == "diff" and not from_game
                    and odir.resolve() not in game_dirs):
                sources.append(f"{odir.name}:diff(inert)")
            else:
                tree, mode = apply_overlay(tree, oroot, vpath, odir.name,
                                           recorder=recorder)
                if mode != "diff(no-base!)":
                    base_found = base_found or mode in {"union", "full"}
                    if mode in {"union", "full"} and odir.resolve() in game_dirs:
                        from_game = True
                if mode in {"union", "full"} and odir.resolve() not in game_dirs:
                    owner_folders.append(odir.name)
                if recorder is not None and mode != "full":
                    # full-override already appended to file_chain in apply_overlay
                    recorder.file_chain.append(Origin(odir.name, mode))
                sources.append(f"{odir.name}:{mode}")

        # Nested candidates: this overlay patching an EARLIER overlay's file at
        # `extensions/<owner>/<vpath>`. The inert-bare-diff rule does NOT apply —
        # a nested diff over another mod's file is exactly the form the engine
        # loads (KB gotcha #6; engine-proven). A mod is never its own nested
        # patcher -- enforced by the `continue` below.
        #
        # There is deliberately NO guard against an overlay that ships BOTH the
        # bare vpath and `extensions/<owner>/<vpath>`: those are two distinct
        # files on disk and the engine evaluates both, so applying both is the
        # faithful answer. This comment used to claim "a file already applied bare
        # is not applied again", which nothing enforced -- a dead
        # `applied_here = True` sat where the guard would have gone, and anyone
        # reconciling the code to the comment would have ADDED a guard that drops
        # real ops. MEASURED 2026-09-05 over 125 installed mods: 13 bare+nested
        # pairs exist (3 mods; `distances` and `moreroomsforships` nest under DLC,
        # which never enters `owner_folders`, and `ebi_m0_vro` nests under a mod
        # that supplies its vpath as a `diff`, which does not either). So the case
        # is LATENT, not live -- it needs an owner supplying full/union -- and a
        # guard here would have been wrong the day it first fired.
        for owner in owner_folders:
            if odir.name.lower() == owner.lower():
                continue
            nroot = overlay_root(odir, f"extensions/{owner}/{vpath}", skipped)
            if nroot is None:
                continue
            tree, mode = apply_overlay(tree, nroot, vpath, odir.name,
                                       recorder=recorder)
            if recorder is not None and mode != "full":
                recorder.file_chain.append(Origin(odir.name, mode))
            sources.append(f"{odir.name}:{mode}(nested:{owner})")

    # Text files (t/0001.xml, t/0001-lNNN.xml) have no single base file in reference
    # — the engine overlays them onto the language tree. A mod's t-diff adds <page>s
    # to /language; synthesize an empty <language> root so /language-targeted ops
    # resolve (page/string collisions are covered separately by check_text).
    # (the <language> synthesis now happens BEFORE the overlay loop -- see above)

    return MergeResult(tree=tree, sources=sources,
                       base_found=base_found or tree is not None,
                       base_from_game=from_game, skipped=skipped)
