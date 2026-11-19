"""Actual affected-node identities while applying a document's overlays in order.

XPath strings are positions, not identities. Retain element proxies and give new
nodes distinct identities; one-for-one replacements continue the replaced slot,
while a replacement that inserts siblings records each new contribution separately.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from lxml import etree

from . import _merge
from ._provenance import Recorder


@dataclass(frozen=True)
class OpTargets:
    targets: tuple[str, ...] | None
    created: tuple[str, ...] = ()
    applied: bool = False
    detail: str = ""
    potential: tuple[str, ...] | None = ()
    removed_paths: tuple[tuple[str, str], ...] = ()
    #: The NEW top-level elements this op inserted, in document order: an add's
    #: children, or every sibling a multi-element replace put in the replaced slot. A
    #: one-for-one replace continues the old identity, so it inserts nothing. Lets a
    #: later op on ONE of k inserted siblings be told apart from a whole overwrite.
    inserted: tuple[str, ...] = ()


#: The identity key of a node, per file class (F387; the 4.3 plan's defaults, user-approved):
#: `id`/`name` at ANY depth in libraries/, index/, t/; `connection@name` and `macro@name` in
#: macro/component files; `cue`/`library`/`label` names in md/ and aiscripts/. A node with
#: no key (a nested `<price>`) has no identity to revive, by design.
_SCRIPT_KEYED = frozenset({"cue", "library", "label"})
_TEXTREF = re.compile(r"\{\s*\d+\s*,\s*\d+\s*\}$")
_ASSET_KEYED = frozenset({"connection", "macro"})


def identity_key(vpath: str, el) -> tuple[str, str] | None:
    """`(tag, "attr=value")` naming *el* among its siblings in *vpath*'s class, or None."""
    if not isinstance(el.tag, str):
        return None
    low = _merge._registry_rel(vpath).lower()
    if low.startswith(("libraries/", "index/", "t/")):
        key = _merge._child_key(el)
        parent = el.getparent()
        # User decision 2026-11-18: a NESTED node named by a game-text reference
        # (name="{page,id}", the `_compat._TEXTREF_KEY` shape) is display text, not an
        # identity. A top-level entry keeps F385's key (id first, as before).
        if (key is not None and key[1].startswith("name=") and _TEXTREF.match(key[1][5:])
                and parent is not None and parent.getparent() is not None):
            return None
        return key
    if low.startswith(("md/", "aiscripts/")):
        keyed = _SCRIPT_KEYED
    elif low.startswith("assets/") or "/macros/" in low:
        keyed = _ASSET_KEYED
    else:
        return None
    name = el.get("name")
    return (el.tag, f"name={name}") if el.tag in keyed and name is not None else None


class Nodes(Recorder):
    def __init__(self, tree, union=False, vpath=""):
        super().__init__()
        self.ids = {el: tree.getroottree().getpath(el) for el in tree.iter() if isinstance(el.tag, str)}
        self.created = set()
        self.source = ""
        self.op_index = 0
        self.serial = 0
        self.split = False
        self.pending_root = False
        self.tree = tree
        self.union = union
        self.vpath = vpath
        #: (parent identity, tag, key) -> the removed node's identity (F387: any depth).
        self.removed_keys = {}
        self.revived = set()
        self.inserted = []

    def _key(self, el):
        if self.vpath:
            return identity_key(self.vpath, el)
        # No vpath (a direct caller): the F385 rule, a union file's top-level children.
        return _merge._child_key(el) if self.union and el.getparent() is self.tree else None

    def node_removed(self, path, origin):
        super().node_removed(path, origin)
        for el in self.tree.xpath(path):
            if not isinstance(el, etree._Element):
                continue
            # The removed node AND its keyed descendants: re-adding the whole subtree
            # later continues every one of their identities (F387).
            for node in el.iter():
                parent = node.getparent()
                key = self._key(node) if parent is not None else None
                if key is not None:
                    self.removed_keys[(self.element_id(parent), *key)] = self.element_id(node)

    def element_id(self, node):
        if node not in self.ids:
            parent = node.getparent()
            key = self._key(node) if parent is not None else None
            slot = (self.element_id(parent), *key) if key is not None else None
            if slot is not None and slot in self.removed_keys:
                identity = self.removed_keys.pop(slot)
                self.ids[node] = identity
                self.revived.add(identity)
                return identity
            prefix = self.element_id(parent) if parent is not None else ""
            self.serial += 1
            self.ids[node] = "{}/{}~{}:{}:{}".format(prefix, node.tag, self.source, self.op_index, self.serial)
            self.created.add(self.ids[node])
        return self.ids[node]

    def target_id(self, node):
        if isinstance(node, etree._Element):
            return self.element_id(node)
        if getattr(node, "is_attribute", False) and node.getparent() is not None:
            return self.element_id(node.getparent()) + "/@" + node.attrname
        return None

    def register(self, node):
        for el in node.iter():
            if isinstance(el.tag, str):
                # Every node an op registers is its contribution ("supply") -- a REVIVED
                # one too: an insertion re-supplying a removed key continues the removed
                # identity, and this op is what made it exist again. Leaving revived
                # identities out dropped the re-adder from the node's row, so an EARLIER
                # remover was named the live owner of a node the engine has with the
                # re-adder's definition (MEASURED 2026-11-17, gates/cross_tool.py).
                identity = self.element_id(el)
                self.created.add(identity)
                self.created.update(identity + "/@" + attr for attr in el.attrib)

    def elem_created(self, el, origin, prior_chain=None):
        fresh = el not in self.ids
        super().elem_created(el, origin, prior_chain)
        self.register(el)
        if fresh and self.element_id(el) not in self.revived:
            self.inserted.append(self.element_id(el))

    def elem_replaced(self, old, new, origin):
        super().elem_replaced(old, new, origin)
        if not self.split:
            self.ids[new] = self.element_id(old)
        self.register(new)
        if self.split:
            self.inserted.append(self.element_id(new))

    def attr_set(self, el, attr, origin):
        super().attr_set(el, attr, origin)
        self.created.add(self.element_id(el) + "/@" + attr)

    def full_override(self, origin, *args, **kwargs):
        super().full_override(origin, *args, **kwargs)
        self.pending_root = True

    def final_paths(self, tree):
        result = {}
        for el in tree.iter():
            if not isinstance(el.tag, str):
                continue
            identity = self.element_id(el)
            actual = tree.getroottree().getpath(el)
            result[identity] = actual
            for attr in el.attrib:
                result[identity + "/@" + attr] = actual + "/@" + attr
        return result


def trace(vpath, base, roots, expected=None, nested=None, plain_on_game=False):
    """({(source, op index): OpTargets}, final identity->XPath, qualified).

    The final byte comparison is against the normal merge entry, rather than
    trusting this instrument merely because its own per-op calls succeeded.

    *nested*, when given, is ``nested(source, owner) -> root | None``: *source*'s
    patch of *owner*'s file at `extensions/<owner>/<vpath>`. It mirrors
    `_merge.build_effective`'s plain door, which applies such a patch onto a
    full/union supplier -- right after the owner for a mod loading EARLIER (MEASURED
    2026-10-12), after the patcher's own file for one loading later. Those ops are
    applied (so later selectors see their effect and the byte check can pass) but not
    recorded per op: they belong to the nested vpath's own analysis. Without it, the
    final comparison disagreed and the whole file lost collision analysis.

    *plain_on_game*: *vpath* is a plain path whose document the GAME supplies and every
    root in *roots* is a MOD's, so a complete macro/component copy MERGES (issue #23),
    as in the plain door. Explicit, like `apply_overlay`'s flag.
    """
    if base is None:
        return {}, {}, False
    tree = copy.deepcopy(base)
    nodes = Nodes(tree, vpath.lower().startswith(("libraries/", "index/", "t/")), vpath)
    records = {}
    owners: list[str] = []
    seen: list[str] = []
    on_mod = _merge._nested_on_mod(vpath)

    def apply_nested(source, owner):
        nonlocal tree
        nroot = nested(source, owner)
        if nroot is None:
            return True
        nodes.source, nodes.op_index, nodes.split = source, f"nested:{owner}", False
        tree, _m = _merge.apply_overlay(tree, nroot, vpath, source, recorder=nodes,
                                          nested_on_mod=True)
        if tree is None:
            return False
        nodes.tree = tree
        if nodes.pending_root:
            nodes.register(tree)
            nodes.pending_root = False
        return True

    def after(source):
        seen.append(source)
        if nested is None:
            return True
        return all(apply_nested(source, owner) for owner in owners
                   if owner.lower() != source.lower())

    for source, root in roots:
        nodes.source = source
        nodes.split = False
        if root.tag != "diff":
            # At a nested vpath every root is a patch on the owner's file (issue #17).
            nodes.op_index = "file"
            revived_before = set(nodes.revived)
            tree, mode = _merge.apply_overlay(tree, root, vpath, source, recorder=nodes,
                                              nested_on_mod=on_mod,
                                              plain_on_game=plain_on_game and not on_mod)
            if tree is None:
                return {}, {}, False
            nodes.tree = tree
            if nodes.pending_root:
                nodes.register(tree)
                nodes.pending_root = False
            # F387: a COMPLETE file re-defining a removed entry continues its identity; the
            # revived ids are this file's contribution (`_compat` makes them "supply").
            again = tuple(sorted(nodes.revived - revived_before))
            if again:
                records[(source, "file")] = OpTargets((), again, True, mode, ())
            if nested is not None and mode in ("union", "full"):
                owners.append(source)
                if not all(apply_nested(prior, source) for prior in seen
                           if prior.lower() != source.lower()):
                    return {}, {}, False
            if not after(source):
                return {}, {}, False
            continue
        for index, op in enumerate(o for o in root if isinstance(o.tag, str)):
            nodes.op_index = index
            nodes.created.clear()
            nodes.inserted = []
            nodes.split = len([c for c in op if isinstance(c.tag, str)]) > 1
            try:
                selected = tree.xpath(op.get("sel", ""))
                candidates = tuple(nodes.target_id(item) for item in selected) if isinstance(selected, list) else (None,)
                ids = None if any(k is None for k in candidates) else candidates
                removed = tuple((nodes.target_id(item), _merge._path_of(item)) for item in selected
                                if nodes.target_id(item) is not None) if op.tag == "remove" and isinstance(selected, list) else ()
            except etree.XPathEvalError:
                ids = None
                removed = ()
            one = etree.Element("diff")
            one.append(copy.deepcopy(op))
            applied = _merge.apply_diff(tree, one, recorder=nodes, source=source)
            if len(applied) != 1:
                return {}, {}, False
            verdict = applied[0]
            if nodes.pending_root:
                nodes.register(tree)
                nodes.pending_root = False
            active = verdict.ok and not verdict.skipped_if
            records[(source, index)] = OpTargets(ids if active or ids is None else (),
                tuple(sorted(nodes.created)) if active else (), active, verdict.detail, ids,
                removed if active else (), tuple(nodes.inserted) if active else ())
        if not after(source):
            return {}, {}, False
    qualified = expected is not None and etree.tostring(tree) == etree.tostring(expected)
    return records, nodes.final_paths(tree), qualified
