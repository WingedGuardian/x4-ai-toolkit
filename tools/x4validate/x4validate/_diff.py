r"""x4diff: semantic XML diff between two versions of a mod (pristine vs edited).

Isolates a user's personal edits from author content by comparing two mod trees
node/attribute-wise (not textually). Handles packed (cat/dat) and loose on either
side via _merge.overlay_root. Powers the personal-edit recovery: pristine -> edited
= your edit-set; edited -> newer-upstream = what the author has since changed.

Change model per common file: element identity = its tag-path with id/name/ref
disambiguation; report added / removed / changed attributes and added/removed nodes.

Element TEXT is compared too, as the reserved pseudo-attribute ``text()`` (an XML
attribute name cannot contain parentheses, so it can never collide with a real
one). ~48% of installed-mod ops carry their value in text --
``<replace sel=".../@min">999</replace>``, a t-file ``<t id="1">...</t>`` -- and
comparing attributes alone read a 5 -> 999 edit as "changed files: 0"
(AUDIT-2026-09-24 DF-1). Text is compared with every whitespace run collapsed to
one space and the ends stripped, so re-indenting a document -- a multi-line
string included -- changes nothing while any change to the words does. Mixed
content stays POSITIONAL: the text on either side of each child (element or
comment) is its own segment, so moving text across a child is a change. An
element with no non-whitespace text has no `text()` at all.

The top-level ops of a `<diff>` are keyed by what they install (their first
payload child's id/name/macro/ref), else by `sel`; see `_op_key`.

The mod's own `content.xml` is not one of those files: the shared enumerator drops
it, rightly, for the store and x4compat. It is compared separately by
`compare_manifests` and reported as `ModDiff.manifest`, outside every file count.
"""

from __future__ import annotations

import copy
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from x4validate import _paths, _compat, _merge, _input, _scan
from x4validate import __version__


def mod_xml_vpaths(mod_dir: Path) -> dict[str, str]:
    """lower(vpath) -> real vpath for a mod's XML (packed + loose). content.xml dropped:
    `compare_manifests` is what looks at that one."""
    return _compat._mod_xml_paths(mod_dir)


def read_vpath(mod_dir: Path, vpath: str) -> etree._Element | None:
    try:
        return _merge.overlay_root(mod_dir, vpath)
    except etree.LxmlError:
        # silent-ok: None is this function's documented "could not read" sentinel
        # and every caller branches on it (diff_mods records it in
        # ModDiff.unreadable). Absent != unreadable is preserved by the caller.
        return None


def read_merged(dirs: list[Path], vpath: str,
                unreadable: list[str] | None = None) -> etree._Element | None:
    """Effective root for *vpath* after applying *dirs* in order (later wins).

    Lets the baseline be a stack (e.g. pristine core + pristine VRO submod), so a
    diff isolates ONLY the user's edits on top of the official merged content."""
    tree: etree._Element | None = None
    for d in dirs:
        try:
            oroot = _merge.overlay_root(d, vpath)
        except etree.LxmlError as exc:
            if unreadable is not None:
                unreadable.append(f"{d.name}/{vpath}: {exc}")
            continue
        if oroot is None:
            continue
        if tree is None:
            # The first layer that supplies the document IS the baseline, whatever
            # its shape. A `<diff>` here used to be discarded, so a stack whose
            # first supplier patches the file (the normal shape for a mod: its own
            # files are diffs against vanilla) returned None and the caller
            # reported "the OLD copy would not parse" about a well-formed file
            # (AUDIT-2026-09-24 DF-2). The single-layer path (`read_vpath`)
            # already compares a diff document as a diff document; this matches it.
            tree = oroot
        elif tree.tag == "diff":
            # No layer supplied a base document, so the baseline so far is a
            # PATCH SET. A further diff layer extends it -- ops apply in load
            # order, which is document order here -- and a full document from a
            # later layer supersedes the patches (there is no base in this stack
            # for them to have applied to).
            if oroot.tag == "diff":
                tree.extend(copy.deepcopy(op) for op in oroot)
            else:
                tree = oroot
        else:
            tree, _ = _merge.apply_overlay(tree, oroot, vpath, d.name)
    return tree


def merged_vpaths(dirs: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in dirs:
        out.update(mod_xml_vpaths(d))
    return out


#: Identity attributes, most-discriminating FIRST. `id` leads because it is what
#: X4 keys entities on; `name` came first before AUDIT-2026-09-24 DF-3, and
#: vanilla `wares.xml` carries 101 wares in 49 duplicated-name groups (name is
#: often a t-file reference like "{20201,301}"), so the positional suffix those
#: groups needed made ONE inserted ware shift the key of every untouched sibling
#: and read as phantom attribute edits. Diff ops are keyed separately (`_op_key`).
_KEY_ATTRS = ("id", "name", "ref", "macro", "method", "ware", "class")

#: What identifies a diff op's PAYLOAD (its first element child).
_PAYLOAD_KEY_ATTRS = ("id", "name", "macro", "ref")

_OP_TAGS = frozenset({"add", "replace", "remove"})

#: Reserved pseudo-attribute carrying an element's text (see the module docstring).
TEXT_ATTR = "text()"

#: Marks a child-element boundary inside a `text()` value (mixed content).
_SEGMENT_SEP = " ‖ "


def _node_key(el: etree._Element) -> str:
    """Stable identity: tag plus the first discriminating attribute present.

    Only an element with NONE of `_KEY_ATTRS` falls back to a positional key
    (assigned by `_index`), as do genuine duplicates of one key."""
    for a in _KEY_ATTRS:
        v = el.get(a)
        if v is not None:
            return f"{el.tag}[@{a}={v}]"
    return el.tag


def _payload_key(op: etree._Element) -> str | None:
    """`<ware@id=x>` for an op whose first element child carries an identity."""
    for child in op:
        if isinstance(child.tag, str):
            for a in _PAYLOAD_KEY_ATTRS:
                v = child.get(a)
                if v is not None:
                    return f"{child.tag}@{a}={v}"
            return None
    return None


def _op_key(op: etree._Element) -> str:
    """Identity of a top-level diff op: its tag plus WHAT it installs, else WHERE.

    Keyed on (tag, sel) alone, 839 of 7,022 installed ops (12%) sat in groups that
    share both -- several `<add sel="/wares">` -- and fell to a positional suffix,
    so inserting one op re-keyed its untouched siblings as removed + added. The
    payload's identity tells them apart. It also keeps the op's identity across a
    selector-only edit, so a changed `sel` reads as an `@sel` change the three-way
    can classify, not a node removal plus an addition it cannot. An op with no
    identifiable payload (a `<remove>`, a text-valued `<replace>`) is keyed by its
    `sel`; `_index` adds `sel`, then position, only to break a remaining tie."""
    pk = _payload_key(op)
    if pk is not None:
        return f"{op.tag}[{pk}]"
    sel = op.get("sel")
    return f"{op.tag}[@sel={sel}]" if sel is not None else op.tag


#: ASCII formatting whitespace only -- the characters a pretty-printer inserts for
#: indentation. `str.split()` (what this used to use) treats ANY Unicode whitespace
#: the same way, including U+00A0 (NBSP) and U+3000 (ideographic space): an edit
#: that swapped one of those for a real space, or removed it, read as VERBATIM.
_ASCII_WS = " \t\n\r\f\v"
_ASCII_WS_RUN = re.compile(f"[{_ASCII_WS}]+")


def _norm(text: str | None) -> str:
    """ASCII whitespace runs collapsed to one space; ends stripped ONLY for a
    multi-line segment.

    Re-indenting a multi-line string (real newlines, real pretty-print indentation)
    must still change nothing -- that is what this exists to prevent flooding every
    re-indented file with phantom text changes. But a leading/trailing space on an
    otherwise SINGLE-LINE value is not indentation, it is content (a padding-
    sensitive string, or simply someone's edit), and must not be silently dropped.
    Non-ASCII whitespace (NBSP, U+3000, ...) is never touched either way -- it is an
    ordinary character here, never interchangeable with a real space.
    """
    t = text or ""
    collapsed = _ASCII_WS_RUN.sub(" ", t)
    if "\n" in t or "\r" in t:
        return collapsed.strip(_ASCII_WS)
    return collapsed


def _text_value(el: etree._Element) -> str | None:
    """The element's text content, or None when it has none.

    POSITIONAL across REAL child ELEMENTS: the text before the first one and after
    each one is a separate segment, joined with `_SEGMENT_SEP`. Folding them
    together hid real changes -- `a b<br/>` equalled `a<br/> b`. A comment or PI
    child is NOT a boundary -- `.tag` is not a `str` for either (the same test
    `_OPS` filtering already uses) -- so its own tail text is folded into the
    segment already running instead of starting a new one; otherwise merely
    inserting a comment before unchanged text split it into a leading empty
    segment plus the text, producing a phantom `text()` change ("5" -> " ‖ 5",
    with the separator itself now inside the reported value). Each segment is
    `_norm`-alised; trailing empty segments (the pretty-print tail after the last
    child) are dropped, and an element whose every segment is empty has no text at
    all."""
    segs: list[str] = []
    current = el.text or ""
    for c in el:
        if not isinstance(c.tag, str):
            current += c.tail or ""
            continue
        segs.append(_norm(current))
        current = c.tail or ""
    segs.append(_norm(current))
    while segs and not segs[-1]:
        segs.pop()
    return _SEGMENT_SEP.join(segs) if segs else None


def _values(el: etree._Element) -> dict[str, str]:
    """Attributes plus the `text()` pseudo-attribute when the element has text."""
    out = dict(el.attrib)
    txt = _text_value(el)
    if txt is not None:
        out[TEXT_ATTR] = txt
    return out


def _index(root: etree._Element) -> dict[str, dict[str, str]]:
    """Canonical path -> {attr: value} for every element in the tree.

    The value map includes the `text()` pseudo-attribute. The top-level ops of a
    `<diff>` document are keyed by `_op_key`; everything else by `_node_key`.
    Remaining ties get `[@sel=...]` (ops only) and then a positional suffix."""
    out: dict[str, dict[str, str]] = {}

    def keys_for(el, ops: bool) -> list[tuple[etree._Element, str]]:
        kids = [c for c in el if isinstance(c.tag, str)]
        keys = [(_op_key(c) if ops and c.tag in _OP_TAGS else _node_key(c)) for c in kids]
        if ops:
            tally = Counter(keys)
            dup = {k for k, n in tally.items() if n > 1}
            keys = [(f"{k}[@sel={c.get('sel')}]"
                     if k in dup and c.get("sel") is not None and "[@sel=" not in k
                     else k) for c, k in zip(kids, keys)]
        counts = Counter(keys)
        seen: dict[str, int] = {}
        final = []
        for c, k in zip(kids, keys):
            if counts[k] > 1:
                seen[k] = seen.get(k, -1) + 1
                k = f"{k}#{seen[k]}"
            final.append((c, k))
        return final

    def walk(el, prefix, ops=False):
        for child, key in keys_for(el, ops):
            path = f"{prefix}/{key}"
            out[path] = _values(child)
            walk(child, path)

    out["/" + _node_key(root)] = _values(root)
    walk(root, "/" + _node_key(root), ops=root.tag == "diff")
    return out


@dataclass
class FileDiff:
    vpath: str
    status: str                       # added | removed | changed
    #: (path, attr, old, new); attr may be the `text()` pseudo-attribute.
    attr_changes: list[tuple[str, str, str, str]] = field(default_factory=list)
    nodes_added: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)

    @property
    def weight(self) -> int:
        return len(self.attr_changes) + len(self.nodes_added) + len(self.nodes_removed)


def diff_file(old: etree._Element, new: etree._Element, vpath: str) -> FileDiff:
    fd = FileDiff(vpath, "changed")
    oi, ni = _index(old), _index(new)
    for path in ni.keys() - oi.keys():
        fd.nodes_added.append(path)
    for path in oi.keys() - ni.keys():
        fd.nodes_removed.append(path)
    for path in oi.keys() & ni.keys():
        oa, na = oi[path], ni[path]
        for attr in oa.keys() | na.keys():
            ov, nv = oa.get(attr), na.get(attr)
            if ov != nv:
                fd.attr_changes.append((path, attr, ov if ov is not None else "∅",
                                        nv if nv is not None else "∅"))
    return fd


#: Document types `diff_mods` skips by default.
IGNORED_SUFFIXES = (".xsd",)

#: The mod-root manifest, which `mod_xml_vpaths` leaves out.
MANIFEST = "content.xml"

#: Root attributes a reader looks for first; the rest follow alphabetically.
_MANIFEST_ATTR_ORDER = ("id", "version", "date")


@dataclass
class ManifestDiff:
    """The manifest comparison. Each state is a different answer, so none collapses
    into another: identical | differs | absent-old | absent-new | absent-both |
    unreadable. `not-compared` is the default until a comparison has run."""
    state: str = "not-compared"
    old_label: str = "OLD"
    new_label: str = "NEW"
    #: (attr, old, new) for the root element, `_MANIFEST_ATTR_ORDER` first.
    root_changes: list[tuple[str, str, str]] = field(default_factory=list)
    #: Every row, root attributes included. None unless there is something to list.
    detail: FileDiff | None = None
    #: Why it could not be read (state `unreadable`).
    reason: str = ""
    #: A limit on what was compared, stated next to the answer.
    note: str = ""

    @property
    def not_read(self) -> bool:
        return self.state in ("unreadable", "not-compared")


def compare_manifests(old_dir: Path, new_dir: Path,
                      old_label: str = "OLD", new_label: str = "NEW") -> ManifestDiff:
    """Compare two mod folders' `content.xml`, semantically like any other document.

    Root attributes are compared directly, with `diff_file` given attribute-less roots
    for everything below them: `_node_key` keys a root on its `id`, so a changed id
    would otherwise read as the whole document removed and added, with no id row.
    """
    m = ManifestDiff(old_label=old_label, new_label=new_label)
    problems: list[str] = []

    def _read(mod_dir: Path, label: str) -> etree._Element | None:
        # Passing `skipped` is what separates "could not read" from "not there".
        skipped: list[str] = []
        root = _merge.overlay_root(mod_dir, MANIFEST, skipped)
        problems.extend(f"{label}: {s}" for s in skipped)
        return root

    o, n = _read(old_dir, old_label), _read(new_dir, new_label)
    if problems:
        m.state, m.reason = "unreadable", "; ".join(problems)
    elif o is None and n is None:
        m.state = "absent-both"
    elif o is None:
        m.state, m.detail = "absent-old", FileDiff(MANIFEST, "added")
    elif n is None:
        m.state, m.detail = "absent-new", FileDiff(MANIFEST, "removed")
    else:
        attrs = sorted(set(o.attrib) | set(n.attrib), key=lambda a: (
            _MANIFEST_ATTR_ORDER.index(a) if a in _MANIFEST_ATTR_ORDER
            else len(_MANIFEST_ATTR_ORDER), a))
        m.root_changes = [(a, o.get(a, "∅"), n.get(a, "∅")) for a in attrs
                          if o.get(a) != n.get(a)]
        bare_o, bare_n = copy.deepcopy(o), copy.deepcopy(n)
        bare_o.attrib.clear()
        bare_n.attrib.clear()
        fd = diff_file(bare_o, bare_n, MANIFEST)
        fd.attr_changes[:0] = [(f"/{n.tag}", a, ov, nv) for a, ov, nv in m.root_changes]
        m.state = "differs" if fd.weight else "identical"
        m.detail = fd if fd.weight else None
    return m


@dataclass
class ModDiff:
    old: str
    new: str
    files: list[FileDiff] = field(default_factory=list)
    #: vpaths present on BOTH sides that could not be compared because one side
    #: would not parse. Without this they fell out of `files` entirely and read
    #: as UNCHANGED — the one verdict a diff must never invent.
    unreadable: list[str] = field(default_factory=list)
    #: Reported on its own line and never counted in `files`: "changed files" is the
    #: game documents, and gates/diff_truth.py pins it to the files it mutates.
    manifest: ManifestDiff = field(default_factory=ManifestDiff)

    def changed(self):
        return [f for f in self.files if f.status == "changed" and f.weight]

    def added(self):
        return [f for f in self.files if f.status == "added"]

    def removed(self):
        return [f for f in self.files if f.status == "removed"]


def diff_mods(old_dirs: Path | list[Path], new_dir: Path,
              ignore_suffixes: tuple[str, ...] = IGNORED_SUFFIXES) -> ModDiff:
    """Diff *new_dir* against a baseline of one or more *old_dirs* (merged in order)."""
    old_list = [old_dirs] if isinstance(old_dirs, Path) else list(old_dirs)
    md = ModDiff(" + ".join(d.name for d in old_list), str(new_dir))
    md.manifest = compare_manifests(old_list[0], new_dir)
    if len(old_list) > 1:
        md.manifest.note = (f"compared against {old_list[0].name} only; "
                            "baseline layers' manifests are not merged")
    ov, nv = merged_vpaths(old_list), mod_xml_vpaths(new_dir)
    drop = lambda low: low.endswith(ignore_suffixes)
    for low in nv.keys() - ov.keys():
        if not drop(low):
            md.files.append(FileDiff(nv[low], "added"))
    for low in ov.keys() - nv.keys():
        if not drop(low):
            md.files.append(FileDiff(ov[low], "removed"))
    for low in ov.keys() & nv.keys():
        if drop(low):
            continue
        o = (read_merged(old_list, ov[low], md.unreadable) if len(old_list) > 1
             else read_vpath(old_list[0], ov[low]))
        n = read_vpath(new_dir, nv[low])
        if o is None or n is None:
            side = "OLD" if o is None else "NEW"
            md.unreadable.append(f"{nv[low]}: the {side} copy would not parse — "
                                 "not compared, and NOT counted as unchanged")
            continue
        fd = diff_file(o, n, nv[low])
        if fd.weight:
            md.files.append(fd)
    return md
