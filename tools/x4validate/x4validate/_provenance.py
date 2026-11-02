"""Per-attribute provenance capture for the effective-tree merge.

A :class:`Recorder` rides along with `_merge` (opt-in via the ``recorder=``
kwarg) and records, for every element/attribute the merge mutates, the chain
of :class:`Origin`\\ s that produced the surviving value — oldest first, last
entry wins. Values never touched by any overlay carry no per-node record and
inherit the recorder's ``default_origin`` (normally ``base``), so memory stays
proportional to the number of *modified* nodes, not tree size.

Identity design: chains are keyed by the *live lxml element proxies* (held
strongly in the dicts, which pins proxy identity for the tree's lifetime) —
never by xpath strings, which shift as the tree mutates. Consumers must query
chains while the merged tree and recorder are both alive; element keys are
never persisted. Paths appear only in :attr:`Recorder.removed`, captured at
removal time for display purposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

BASE = "base"


@dataclass(frozen=True)
class Origin:
    """One provenance event: *source* overlay applied *op* (at *line* of the patching file)."""
    source: str          # "base" | "ego_dlc_split" | mod folder name
    op: str              # base | add | replace | replace-attr | replace-text
                         # | union-add | union-replace | full-override | remove
    line: int = 0        # sourceline of the op in the PATCHING file (0 = n/a)

    def short(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return f"{self.source} {self.op}{loc}"


@dataclass
class Recorder:
    """Collects provenance during one build_effective() run for one vpath."""
    default_origin: Origin = field(default_factory=lambda: Origin(BASE, BASE))
    file_chain: list[Origin] = field(default_factory=list)   # file-level events, in order
    removed: list[tuple[str, Origin]] = field(default_factory=list)  # (path-at-removal, origin)
    # DISPLAY ONLY (annotate_tree): the last whole-file layer replaced nothing / the owner mod of a nested path.
    sole_provider: bool = False
    owner: str = ""
    # Element proxies are held strongly => identity-stable for the tree's lifetime.
    _elem: dict = field(default_factory=dict)   # etree._Element -> list[Origin]
    _attr: dict = field(default_factory=dict)   # (etree._Element, attr) -> list[Origin]

    # --- capture (called from _merge at mutation time) --------------------------

    def elem_created(self, el: etree._Element, origin: Origin,
                     prior_chain: list[Origin] | None = None) -> None:
        self._elem[el] = list(prior_chain or []) + [origin]

    def elem_replaced(self, old: etree._Element, new: etree._Element, origin: Origin) -> None:
        """*new* took *old*'s place: new's chain continues old's."""
        prior = self._elem.pop(old, None) or self._implicit(old)
        self._elem[new] = prior + [origin]
        # attr-level history inside the clobbered subtree is intentionally dropped
        # (documented: chains restart at a union-replace / element-replace).
        stale = [k for k in self._attr if k[0] is old]
        for k in stale:
            del self._attr[k]

    def attr_set(self, el: etree._Element, attr: str, origin: Origin) -> None:
        key = (el, attr)
        self._attr[key] = self._attr.get(key, self._implicit_attr(el, attr)) + [origin]

    def node_removed(self, path_at_removal: str, origin: Origin) -> None:
        self.removed.append((path_at_removal, origin))

    def full_override(self, origin: Origin, had_base: bool = True) -> None:
        """Whole file replaced: all prior lineage is gone; new default for every node.
        had_base=False: nothing was in place (a mod-only or DLC-only file), so this layer is the sole provider."""
        self.sole_provider = not had_base
        self._elem.clear()
        self._attr.clear()
        self.default_origin = origin
        self.file_chain.append(origin)

    # --- lookup (called by extractors post-merge, tree still live) ---------------

    def elem_chain(self, el: etree._Element) -> list[Origin]:
        """Chain for *el*: own record, else nearest recorded ancestor, else default."""
        node = el
        while node is not None:
            chain = self._elem.get(node)
            if chain is not None:
                return chain
            node = node.getparent()
        return [self.default_origin]

    def attr_chain(self, el: etree._Element, attr: str) -> list[Origin]:
        chain = self._attr.get((el, attr))
        if chain is not None:
            return chain
        return self.elem_chain(el)

    def winner(self, el: etree._Element, attr: str | None = None) -> Origin:
        chain = self.attr_chain(el, attr) if attr else self.elem_chain(el)
        return chain[-1]

    def is_base(self, el: etree._Element, attr: str | None = None) -> bool:
        """True when the value was never touched by any overlay (pure default lineage)."""
        chain = self.attr_chain(el, attr) if attr else self.elem_chain(el)
        return len(chain) == 1 and chain[0] == self.default_origin

    # --- internals ---------------------------------------------------------------

    def _implicit(self, el: etree._Element) -> list[Origin]:
        # An unrecorded element inherits from its nearest recorded ancestor
        # (e.g. base ware later replaced: prior chain is [base]).
        parent = el.getparent()
        if parent is not None:
            return list(self.elem_chain(parent))
        return [self.default_origin]

    def _implicit_attr(self, el: etree._Element, attr: str) -> list[Origin]:
        return list(self.elem_chain(el))

    # --- own-record lookup (no ancestor fallback) -------------------------------

    def own_chain(self, el: etree._Element) -> list[Origin] | None:
        """The chain recorded for *el* itself, or None. Unlike elem_chain, never inherited:
        a descendant of an added subtree has no own record, so it is not reported twice."""
        return self._elem.get(el)


def _fmt_origin(o: Origin) -> str:
    # The default origin reads "base base" through Origin.short(); say it once.
    return o.source if o.op == BASE else o.short()


def comment_safe(text: str) -> str:
    # "--" is illegal inside an XML comment, and a mod folder name may contain it.
    while "--" in text:
        text = text.replace("--", "- -")
    return text


def annotate_tree(tree: etree._Element, rec: Recorder) -> tuple[list[str], int]:
    """Insert a provenance comment before every node an overlay changed; return the
    header lines (root-level records and removals, which have no node to sit before)
    and the number of annotated nodes.

    Reported: a node's OWN record (added / replaced / union-touched) and its own attribute
    records, each only when the surviving value did not come from the default origin.
    Descendants of an added subtree inherit their root's record and are not repeated.
    Limits, inherited from the Recorder: attribute history inside a REPLACED subtree is
    dropped at the replace; after a whole-file replace no node is annotated (the header names
    the replacing origin instead), and removal paths are positional at the time of removal.
    """
    default = rec.default_origin
    # One pass over the attribute records: a per-element scan would be quadratic.
    attrs_by_el: dict = {}
    for (e, a), chain in rec._attr.items():
        attrs_by_el.setdefault(e, {})[a] = chain
    notes: list[tuple[etree._Element, str]] = []
    for el in tree.iter():
        if not isinstance(el.tag, str):          # comments / processing instructions
            continue
        parts = []
        own = rec.own_chain(el)
        if own and own[-1] != default:
            parts.append(" -> ".join(_fmt_origin(o) for o in own))
        for attr, chain in sorted(attrs_by_el.get(el, {}).items()):
            if attr not in el.attrib:            # set, then removed by a later overlay
                continue
            if chain and chain[-1] != default:
                parts.append(f"@{attr}: " + " -> ".join(_fmt_origin(o) for o in chain))
        if parts:
            notes.append((el, comment_safe("; ".join(parts))))
    header: list[str] = []
    overridden = default.op != BASE
    sole = overridden and rec.sole_provider
    if sole:
        # The first layer with no base is not a replacement: no earlier layer existed, so no history is gone.
        header.append(comment_safe(f"unannotated nodes come from: {_fmt_origin(default)} "
                                   f"(sole provider of this file; no earlier layer existed)"))
    elif overridden:
        # A whole-file replace resets every record: nothing below it can be annotated, so the
        # tree would otherwise read as untouched vanilla.
        header.append(comment_safe(f"unannotated nodes come from: {_fmt_origin(default)} "
                                   f"(whole-file replace; per-node history before it is gone)"))
    elif rec.owner:
        # A nested extensions/<owner>/... path: the owner's file is the base and is not a recorded layer, so
        # its nodes would otherwise read as game "base".
        header.append(comment_safe(f"unannotated nodes come from: {rec.owner} (the owner mod's file; "
                                   f"the nested patches below were applied over it)"))
    # Collect first, insert after: inserting while iterating would change the walk.
    for el, text in notes:
        if el is tree:
            header.append(comment_safe(f"<{el.tag}>: {text}"))
        else:
            el.addprevious(etree.Comment(f" prov: {text} "))
    for path, origin in rec.removed:
        line = f"removed: {path} ({_fmt_origin(origin)})"
        if overridden and not sole:
            line += f"; may predate the whole-file replace by {_fmt_origin(default)}"
        header.append(comment_safe(line))
    return header, len(notes)
