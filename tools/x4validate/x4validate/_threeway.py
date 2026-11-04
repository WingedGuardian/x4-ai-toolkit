"""Three-way diff: which changes are the AUTHOR's, and which are upstream drift?

A two-way diff between an archived mod and the current tree answers the wrong
question, and it answers it confidently. MEASURED by a parallel session over a
real 2021 mod, 135 documents: **~440 attribute deltas two-way, 15 author edits
three-way**, 340 upstream drift, and **124 of 135 documents byte-verbatim copies
of the baseline**. Acting on the two-way figure would have re-applied 340 of the
upstream author's changes as if they were the user's, and reverted the current
upstream release across 124 files.

The mechanism is a join, not a new differ. Two ordinary two-way diffs share a
common ancestor, so their attribute changes can be keyed on
``(vpath, node-path, attr)`` and compared::

    D1 = diff(base -> archived)   what the AUTHOR did
    D2 = diff(base -> current)    what UPSTREAM did since

    in D1 only ................. author-edit
    in D2 only ................. upstream-drift
    in both, same new value .... converged
    in both, different values .. BOTH-MOVED   <- the only real decision

Reusing `_diff` rather than re-implementing it is deliberate: a second
implementation of the same normalisation is what made an independent measurement
of F64 report 2.6% where the truth was 65.4%.

⚠ **A one-sided absence is UNKNOWN, never a removal.** This is the rule the
workspace paid for: 16 macros appeared to have lost ``missile.targetable`` when
upstream had simply added it after 2021. A three-way diff *can* tell those apart
-- that is its whole purpose -- but only where the baseline actually contains the
document. Where it does not, the document goes to :attr:`ThreeWay.no_base` and is
excluded from every verdict, counted rather than quietly dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from x4validate import _diff, _merge


@dataclass(frozen=True)
class Change:
    """One attribute that moved on exactly one side."""
    vpath: str
    node: str
    attr: str
    base: str
    value: str
    # "author-edit" | "author-addition" | "author-removal" | "upstream-drift" |
    # "upstream-addition" | "upstream-removal" (from `_kind`) | "converged"
    kind: str


@dataclass(frozen=True)
class Conflict:
    """One attribute both sides moved, to different values. The decision."""
    vpath: str
    node: str
    attr: str
    base: str
    archived: str
    current: str
    kind: str = "both-moved"


@dataclass
class ThreeWay:
    base: str
    archived: str
    current: str
    author_edits: list[Change] = field(default_factory=list)
    upstream_drift: list[Change] = field(default_factory=list)
    converged: list[Change] = field(default_factory=list)
    both_moved: list[Conflict] = field(default_factory=list)

    #: documents present in the archived mod but NOT in the baseline. Direction
    #: is unknowable for these, so they are excluded from every bucket above --
    #: and reported, because a silent exclusion is the narrowing step.
    no_base: list[str] = field(default_factory=list)
    #: documents in the baseline that the archived mod does not have.
    dropped_by_author: list[str] = field(default_factory=list)
    #: could not be parsed on some side; NOT the same as unchanged.
    unreadable: list[str] = field(default_factory=list)
    #: node-level adds/removes are surfaced, not classified -- stated, not hidden.
    node_level: list[str] = field(default_factory=list)
    #: `extensions/<baseline>/` prefixes removed so a nested overlay joins with
    #: the mod it patches. Rewriting a path is a transforming step, so it is
    #: reported, never applied silently.
    unwrapped: list[str] = field(default_factory=list)
    #: two source paths that collapsed onto one join key -- reported rather
    #: than silently dropping one of them.
    key_collisions: list[str] = field(default_factory=list)

    documents_compared: int = 0
    author_edited_docs: list[str] = field(default_factory=list)

    #: content.xml, base -> each side. Reported and never classified: the version moves
    #: on nearly every release, so as an attribute it would be BOTH-MOVED on every port.
    manifest_archived: _diff.ManifestDiff = field(default_factory=_diff.ManifestDiff)
    manifest_current: _diff.ManifestDiff = field(default_factory=_diff.ManifestDiff)

    @property
    def verbatim(self) -> int:
        """Documents the author did not touch at all. Usually the headline."""
        return self.documents_compared - len(self.author_edited_docs)

    @property
    def attributes_classified(self) -> int:
        return (len(self.author_edits) + len(self.upstream_drift)
                + len(self.converged) + len(self.both_moved))

ABSENT = "\u2205"


def _unwrap_root_replace(root, label: str, vpath: str, log: list[str]):
    """`<diff><replace sel="//macros">PAYLOAD</replace></diff>` -> PAYLOAD.

    The whole-file override idiom (CLAUDE.md #10; VRO alone ships 848 of them).
    A mod supplies a document this way while the mod it patches supplies the same
    document plainly, so across a three-way join every attribute path on one side
    carries a `/diff/replace/` prefix the other lacks and NOTHING matches. MEASURED
    on a real overlay: 0 BOTH-MOVED, with the four files of interest falling to
    node-level counts (+14/-12 and the like) -- honest, and useless.

    Deliberately narrow, and narrowed by SELF-CONSISTENCY rather than a hard-coded
    list of selectors: the diff must hold exactly one element op, it must be a
    `replace`, it must carry exactly one payload element, and the selector must
    name that payload's own tag (`//macros` for a `<macros>` payload). A targeted
    patch such as `<replace sel="//ware[@id='x']/@damage">` therefore never
    matches, which is the point -- unwrapping one would discard the diff structure
    and invent a comparison.

    Mirrors `_merge`'s own rule for the same construct: "replace on the document
    root needs exactly one payload element (a document has one root)".
    """
    if root is None or root.tag != "diff":
        return root
    ops = [op for op in root if isinstance(op.tag, str)]
    if len(ops) != 1 or ops[0].tag != "replace":
        return root
    sel = (ops[0].get("sel") or "").strip()
    kids = [c for c in ops[0] if isinstance(c.tag, str)]
    if len(kids) != 1:
        return root
    if sel not in (f"//{kids[0].tag}", f"/{kids[0].tag}"):
        return root
    log.append(f"{label}: {vpath}  ->  root <replace sel=\"{sel}\"> payload unwrapped "
               f"to <{kids[0].tag}>")
    return kids[0]


def _kind(side: str, basev: str, newv: str) -> str:
    """Distinguish a value change from an ADDITION or a REMOVAL.

    The workspace's most expensive rule is that a one-sided absence in an old
    document is upstream ADDITION far more often than author deletion. A three-way
    diff can tell them apart, so it should SAY which it found rather than leave a
    reader to infer it from a sentinel. And a consumer applying an "author edit"
    whose new value is the absence sentinel would write that sentinel into an
    attribute instead of deleting it.
    """
    if basev == ABSENT:
        return f"{side}-addition"
    if newv == ABSENT:
        return f"{side}-removal"
    return "author-edit" if side == "author" else "upstream-drift"

def _identities(base_dirs: list[Path]) -> set[str]:
    """Every name the baseline answers to: folder name AND content.xml id.

    The nested FOLDER uses the target's folder name, but the `<dependency id=>`
    a mod declares uses its content.xml id, and those differ often enough that
    CLAUDE.md #6 calls it out. Accept either, case-folded.
    """
    out: set[str] = set()
    for d in base_dirs:
        out.add(d.name.casefold())
        cx = d / "content.xml"
        if not cx.is_file():
            continue
        try:
            mid = _merge.parse_file(cx).get("id")
        except (OSError, etree.LxmlError):
            # silent-ok: an unreadable manifest costs us one ALIAS, never a
            # comparison -- the folder name is already in `out`. It is not an
            # absence of data, and nothing downstream reads a verdict from it.
            continue
        if mid:
            out.add(mid.casefold())
    return out


def _join_keys(vmap: dict[str, str], ids: set[str], label: str,
               unwrapped: list[str], collisions: list[str]) -> dict[str, str]:
    """low vpath -> real vpath, with `extensions/<baseline>/` prefixes removed.

    A mod patching another mod puts its files at `<mymod>/extensions/<target>/...`
    (CLAUDE.md #6), so an overlay's vpaths never join with the vpaths of the mod
    it patches. That is the normal shape for a personal overlay, and it made the
    three-way diff report 0 documents compared on its first real use, with the
    SAME logical file listed under both exclusions at once.

    Unwrapping is deliberately NARROW: only when `<target>` is a name the baseline
    answers to. A patch aimed at some third mod is genuinely outside this
    comparison and stays excluded -- trading a false negative for a false positive
    would be no improvement.
    """
    out: dict[str, str] = {}
    for low, real in sorted(vmap.items()):
        nt = _merge._nested_target(real)
        if nt and nt[0].casefold() in ids:
            key = nt[1].lower()
            unwrapped.append(f"{label}: {real}  ->  {nt[1]}")
        else:
            key = low
        if key in out:
            # Two source paths collapsing onto one key would silently drop a
            # document. Report it and keep the first, rather than lose one.
            collisions.append(f"{label}: {real} collides with {out[key]} on {key}")
            continue
        out[key] = real
    return out


def three_way(base: Path | list[Path], archived: Path, current: Path) -> ThreeWay:
    """Classify *archived* vs *current* changes using *base* as the common ancestor.

    *base* may be a list, so a baseline can be a stack (pristine core + pristine
    submod), matching `_diff.diff_mods`.
    """
    base_dirs = [base] if isinstance(base, Path) else list(base)
    r = ThreeWay(base=" + ".join(d.name for d in base_dirs),
                 archived=str(archived), current=str(current))
    ids = _identities(base_dirs)

    r.manifest_archived = _diff.compare_manifests(base_dirs[0], archived, "BASE", "ARCHIVED")
    r.manifest_current = _diff.compare_manifests(base_dirs[0], current, "BASE", "CURRENT")
    if len(base_dirs) > 1:
        r.manifest_archived.note = r.manifest_current.note = (
            f"compared against {base_dirs[0].name} only; "
            "baseline layers' manifests are not merged")

    b_map = _join_keys(_diff.merged_vpaths(base_dirs), ids, "base",
                       r.unwrapped, r.key_collisions)
    a_map = _join_keys(_diff.mod_xml_vpaths(archived), ids, "archived",
                       r.unwrapped, r.key_collisions)
    c_map = _join_keys(_diff.mod_xml_vpaths(current), ids, "current",
                       r.unwrapped, r.key_collisions)

    r.no_base = sorted(a_map[k] for k in a_map.keys() - b_map.keys())
    r.dropped_by_author = sorted(b_map[k] for k in b_map.keys() - a_map.keys())

    def _read_base(real: str):
        if len(base_dirs) > 1:
            root = _diff.read_merged(base_dirs, real, r.unreadable)
        else:
            root = _diff.read_vpath(base_dirs[0], real)
        return _unwrap_root_replace(root, "base", real, r.unwrapped)

    # The classification population is the documents the BASELINE and the ARCHIVE
    # share. A document the archive never had is not "drift the author is behind
    # on" -- the author never possessed the file -- and letting those rows in
    # inflates the very number this tool exists to deflate. A planted mutant
    # (`excluded = set()`) once survived here because the first test asserted the
    # right outcome for the wrong reason; this restriction is what it guards.
    shared = b_map.keys() & a_map.keys()

    def _index(other_map: dict[str, str], other_dir: Path, side: str):
        """(key, node, attr) -> (base value, other value) for shared documents."""
        idx: dict[tuple[str, str, str], tuple[str, str]] = {}
        touched: set[str] = set()
        # What was ACTUALLY compared. `documents_compared` used to be the shared-key
        # intersection, which includes every document the loop below `continue`s past
        # -- so an UNPARSEABLE file landed in `verbatim`, on the same screen that says
        # "NOT counted as unchanged". MEASURED 2026-09-04: 3 of 3 shared documents
        # malformed in the archive reported "VERBATIM (author touched nothing): 3",
        # rc 0. That is the number this module calls the headline, and it drives a
        # destructive action -- "take upstream wholesale for these files".
        compared: set[str] = set()
        for key in sorted(shared & other_map.keys()):
            if key.endswith(".xsd"):
                continue
            o = _read_base(b_map[key])
            n = _unwrap_root_replace(
                _diff.read_vpath(other_dir, other_map[key]),
                side, other_map[key], r.unwrapped)
            if o is None or n is None:
                bad = "BASE" if o is None else side.upper()
                r.unreadable.append(
                    f"{other_map[key]}: the {bad} copy would not parse - not "
                    f"compared, and NOT counted as unchanged")
                continue
            fd = _diff.diff_file(o, n, other_map[key])
            compared.add(key)
            if fd.weight:
                touched.add(other_map[key])
            for node, attr, old, new in fd.attr_changes:
                idx[(key, node, attr)] = (old, new)
            if side == "archived" and (fd.nodes_added or fd.nodes_removed):
                r.node_level.append(
                    f"{other_map[key]}: +{len(fd.nodes_added)} / "
                    f"-{len(fd.nodes_removed)} node(s) (author side; node-level "
                    f"changes are reported, not classified)")
        return idx, touched, compared

    a_idx, a_touched, a_compared = _index(a_map, archived, "archived")
    u_idx, _, _ = _index(c_map, current, "current")

    for key in sorted(a_idx.keys() | u_idx.keys()):
        joinkey, node, attr = key
        vpath = a_map.get(joinkey) or c_map.get(joinkey) or joinkey
        in_a, in_u = key in a_idx, key in u_idx
        basev = (a_idx.get(key) or u_idx.get(key))[0]
        if in_a and not in_u:
            r.author_edits.append(Change(vpath, node, attr, basev, a_idx[key][1],
                                         _kind("author", basev, a_idx[key][1])))
        elif in_u and not in_a:
            r.upstream_drift.append(Change(vpath, node, attr, basev, u_idx[key][1],
                                           _kind("upstream", basev, u_idx[key][1])))
        elif a_idx[key][1] == u_idx[key][1]:
            r.converged.append(Change(vpath, node, attr, basev, a_idx[key][1], "converged"))
        else:
            r.both_moved.append(
                Conflict(vpath, node, attr, basev, a_idx[key][1], u_idx[key][1]))

    # The AUTHOR side's successful comparisons, not the shared-key count.
    r.documents_compared = len(a_compared)
    r.author_edited_docs = sorted(a_touched)
    return r
