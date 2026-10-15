"""Actual affected-node identities while applying a document's overlays in order.

XPath strings are positions, not identities. Retain element proxies and give new
nodes distinct identities; one-for-one replacements continue the replaced slot,
while a replacement that inserts siblings records each new contribution separately.
"""
from __future__ import annotations

import copy
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


class Nodes(Recorder):
    def __init__(self, tree, union=False):
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
        self.removed_keys = {}
        self.revived = set()

    def node_removed(self, path, origin):
        super().node_removed(path, origin)
        if self.union:
            for el in self.tree.xpath(path):
                if isinstance(el, etree._Element) and el.getparent() is self.tree:
                    key = _merge._child_key(el)
                    if key is not None:
                        self.removed_keys[key] = self.element_id(el)

    def element_id(self, node):
        if node not in self.ids:
            parent = node.getparent()
            key = _merge._child_key(node) if self.union and parent is self.tree else None
            if key is not None and key in self.removed_keys:
                identity = self.removed_keys.pop(key)
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
                identity = self.element_id(el)
                if identity not in self.revived:
                    self.created.add(identity)
                self.created.update(identity + "/@" + attr for attr in el.attrib)

    def elem_created(self, el, origin, prior_chain=None):
        super().elem_created(el, origin, prior_chain)
        self.register(el)

    def elem_replaced(self, old, new, origin):
        super().elem_replaced(old, new, origin)
        if not self.split:
            self.ids[new] = self.element_id(old)
        self.register(new)

    def attr_set(self, el, attr, origin):
        super().attr_set(el, attr, origin)
        self.created.add(self.element_id(el) + "/@" + attr)

    def full_override(self, origin):
        super().full_override(origin)
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


def trace(vpath, base, roots, expected=None):
    """({(source, op index): OpTargets}, final identity->XPath, qualified).

    The final byte comparison is against the normal merge entry, rather than
    trusting this instrument merely because its own per-op calls succeeded.
    """
    if base is None:
        return {}, {}, False
    tree = copy.deepcopy(base)
    nodes = Nodes(tree, vpath.lower().startswith(("libraries/", "index/", "t/")))
    records = {}
    for source, root in roots:
        nodes.source = source
        if root.tag != "diff":
            tree, _mode = _merge.apply_overlay(tree, root, vpath, source, recorder=nodes)
            if tree is None:
                return {}, {}, False
            nodes.tree = tree
            if nodes.pending_root:
                nodes.register(tree)
                nodes.pending_root = False
            continue
        for index, op in enumerate(o for o in root if isinstance(o.tag, str)):
            nodes.op_index = index
            nodes.created.clear()
            nodes.split = len([c for c in op if isinstance(c.tag, str)]) > 1
            try:
                selected = tree.xpath(op.get("sel", ""))
                ids = tuple(k for item in selected for k in [nodes.target_id(item)] if k is not None) if isinstance(selected, list) else ()
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
                removed if active else ())
    qualified = expected is not None and etree.tostring(tree) == etree.tostring(expected)
    return records, nodes.final_paths(tree), qualified
