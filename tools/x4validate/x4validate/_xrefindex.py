r"""The x4xref INDEX BUILD: which script files are read and which rows they become.

Split out of `_xref.py` (the CLI) so the code that decides an index's rows can be
hashed without its CLI (F69): `_freshness.XREF_SOURCES` lists this module and the
others a traced build runs, and `x4xref build` stamps their digest. An index whose
digest differs from the current code's reads STALE, so an edit to `_walk`,
`_SKIP_TAGS` or the enumeration can no longer leave an old index certifying
negatives the current code would not produce. Every name here is re-exported by
`_xref`, which callers keep importing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from x4validate import _merge, _registry, _scan

# Excluded from the `action` index: structural containers, control flow, and
# variable/debug plumbing whose element TAG carries no behavioral meaning (the
# interesting part, if any, is an attribute we index separately or not at all).
_SKIP_TAGS = frozenset({
    "actions", "conditions", "cues", "library", "params", "param",
    "do_if", "do_else", "do_elseif", "do_all", "do_while", "do_for_each",
    "check_all", "check_any", "check_value", "check_age",
    "set_value", "remove_value", "append_to_list", "insert_in_list", "remove_from_list",
    "debug_text", "run_actions", "include_actions", "patch", "delay", "aiscript", "mdscript",
})
# Cue-edge actions: their `cue=` attribute names the target cue.
_CUE_EDGE_TAGS = frozenset({
    "signal_cue", "signal_cue_instantly", "cancel_cue", "reset_cue",
    "enable_cue", "disable_cue", "complete_cue",
})
_SCRIPT_DIRS = ("md/", "aiscripts/")
#: WHICH script files an index covers, recorded in its freshness sidecar. An index built
#: before nested patches were enumerated reads FRESH on the content axis (no mod changed).
#: The `index_code` digest (`_freshness.XREF_SOURCES`) also catches that; this label stays
#: so the STALE reason can NAME the change. Change the value whenever the enumerated set
#: changes; `check_index` reads a mismatch as STALE.
SCRIPT_SCOPE = "md+aiscripts; mods and DLC also behind extensions/<owner>/ (v2)"


@dataclass(frozen=True)
class XrefRow:
    kind: str     # event | signal | cuedef | action
    name: str     # event tag / action tag / cue name
    source: str   # base | dlc:<name> | <mod folder>
    file: str     # virtual path
    cue: str      # enclosing cue (MD) or aiscript name
    line: int
    target: str = ""  # signal -> cue=; event -> object=/by= if present


#: A `<diff>` document's operation elements. Patch STRUCTURE, not behaviour -- but
#: `add`, `replace` and `remove` are ALSO real md/aiscripts elements (md.xsd/aiscripts.xsd:
#: people selections and a string replacement), so they are skipped only in the op
#: position (direct children of a `<diff>` root), never through `_SKIP_TAGS`.
_DIFF_OPS = frozenset({"add", "replace", "remove"})
#: A literal element name test (an XML NCName: no `*`, no `()`, no `::` axis, not `.`).
_ELEMENT = re.compile(r"[A-Za-z_][\w.-]*")
#: A literal attribute step, `@name`.
_ATTRIBUTE = re.compile(r"@[A-Za-z_][\w.-]*")
#: The ONE predicate shape that names a cue: `[@name='literal']`, either quote style.
_NAME_PRED = re.compile(r"""\s*@name\s*=\s*(?:'([^']*)'|"([^"]*)")\s*""")


@dataclass(frozen=True)
class _Step:
    test: str                  # node test, e.g. "cue", "actions", "..", "@value"
    preds: tuple[str, ...]     # top-level predicate bodies, brackets stripped
    descendant: bool           # reached through `//`


def _sel_steps(sel: str) -> list[_Step] | None:
    """Split an XPath selector into location steps, respecting quotes and brackets.

    None for a selector this cannot split cleanly (unbalanced brackets or quotes, a union
    `|` or a parenthesised expression outside a predicate): the
    caller then names no cue, rather than guessing."""
    raw: list[str] = []
    cur: list[str] = []
    depth, quote = 0, ""
    for ch in sel:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch == "[":
            depth += 1
            cur.append(ch)
        elif ch == "]":
            depth -= 1
            if depth < 0:
                return None
            cur.append(ch)
        elif ch == "/" and depth == 0:
            raw.append("".join(cur))
            cur = []
        elif ch in "|()" and depth == 0:
            return None  # a union or a parenthesised expression: not ONE location path
        else:
            cur.append(ch)
    if quote or depth:
        return None
    raw.append("".join(cur))
    steps: list[_Step] = []
    descendant = False
    for i, text in enumerate(raw):
        text = text.strip()
        if not text:
            # A leading "/" gives one empty segment; a "//" gives an empty one before
            # the step it introduces, which is then reached as a descendant.
            if i > 0:
                descendant = True
            continue
        test, preds, buf, depth, quote = "", [], [], 0, ""
        for ch in text:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = ""
            elif ch in ("'", '"') and depth:
                quote = ch
                buf.append(ch)
            elif ch == "[":
                if depth:
                    buf.append(ch)
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth:
                    buf.append(ch)
                else:
                    preds.append("".join(buf))
                    buf = []
            elif depth:
                buf.append(ch)
            else:
                test += ch
        steps.append(_Step(test.strip(), tuple(preds), descendant))
        descendant = False
    return steps


def _cue_name(step: _Step) -> str | None:
    """The literal of a `cue[@name='x']` step with exactly that one predicate, else None."""
    if step.test != "cue" or len(step.preds) != 1:
        return None
    m = _NAME_PRED.fullmatch(step.preds[0])
    if m is None:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _op_context(op: etree._Element) -> str:
    """The cue that ENCLOSES what a diff op inserts, read from its `sel`, or "".

    A WRONG name is worse than none (`cue X` would list the row under a cue it is not
    in), so a name is returned only when the selector states it unambiguously. The
    rule is a WHITELIST -- every shape it does not list gives "":

    * The content's container is the selected node -- or, for a `replace` or a
      `pos="before"/"after"` insert, the selected node's PARENT, so the final step is
      dropped. That dropped step must itself be a child step (no `//`, no axis, not
      `.`/`..`) naming a literal element, `*` or an attribute; its predicates do not
      matter.
    * Of the container path, the LAST step whose name test is `cue` must be exactly
      `cue[@name='literal']` (either quote style, one predicate).
    * EVERY step after it must be a plain child step whose name test is a literal
      element name other than `cue` (any case), optionally an attribute (`@name`) as
      the very last step. Predicates on those steps are allowed. Anything else -- `*`,
      `node()`, an axis (`self::`, `child::`, ...), `.`, `..`, a `//` step -- could
      lead into another cue, and gives "".
    A selector with no cue step (`/mdscript/cues`, `//attention/actions`,
    `//library[...]`), a union or a parenthesised expression gives "".
    """
    steps = _sel_steps(op.get("sel", ""))
    if not steps:
        return ""
    if op.tag == "replace" or op.get("pos") in ("before", "after"):
        # The content lands in the PARENT of the final node, which is the previous
        # step's node only for a plain child step.
        last = steps[-1]
        if last.descendant or not (last.test == "*" or _ELEMENT.fullmatch(last.test)
                                   or _ATTRIBUTE.fullmatch(last.test)):
            return ""
        steps = steps[:-1]
    cue_at = max((i for i, s in enumerate(steps) if s.test == "cue"), default=None)
    if cue_at is None:
        return ""
    after = steps[cue_at + 1:]
    for i, s in enumerate(after):
        plain = (not s.descendant and _ELEMENT.fullmatch(s.test) is not None
                 and s.test.lower() != "cue")
        attr_last = (i == len(after) - 1 and not s.descendant
                     and _ATTRIBUTE.fullmatch(s.test) is not None)
        if not (plain or attr_last):
            return ""
    return _cue_name(steps[cue_at]) or ""


def _walk(root: etree._Element, source: str, vpath: str, out: list[XrefRow]) -> None:
    """DFS the tree, emitting rows, carrying the nearest enclosing cue/script name.

    A `<diff>` root and its op children (`_DIFF_OPS`) emit nothing; each op's CHILDREN
    are walked with the cue its selector names (`_op_context`)."""
    script_name = root.get("name", "") if root.tag in ("mdscript", "aiscript") else ""

    def rec(el: etree._Element, cue: str) -> None:
        tag = el.tag
        if not isinstance(tag, str):
            return
        here_cue = cue
        if tag == "cue" and el.get("name"):
            here_cue = el.get("name")
            out.append(XrefRow("cuedef", here_cue, source, vpath, cue, el.sourceline or 0))
        elif tag.startswith("event_"):
            target = el.get("object") or el.get("by") or el.get("cue") or ""
            out.append(XrefRow("event", tag, source, vpath, cue, el.sourceline or 0, target))
        elif tag in _CUE_EDGE_TAGS:
            out.append(XrefRow("signal", tag, source, vpath, cue, el.sourceline or 0,
                               el.get("cue", "")))
        elif tag not in _SKIP_TAGS:
            out.append(XrefRow("action", tag, source, vpath, cue, el.sourceline or 0))
        for child in el:
            rec(child, here_cue)

    if root.tag == "diff":
        for op in root:
            if isinstance(op.tag, str) and op.tag in _DIFF_OPS:
                ctx = _op_context(op)
                for child in op:
                    rec(child, ctx)
            else:
                rec(op, "")  # not an op: indexed as before, never silently dropped
        return
    rec(root, script_name)


def _iter_source_files(base: Path, source: str, unreadable: list | None = None):
    """Yield (vpath, parse_root) for md/ + aiscripts/ DIRECTLY under the reference root.

    `under()` narrows the walk to those two subtrees, so pointing this at the
    reference root does not rglob the whole ~13k-file base game. Direct-only BY DESIGN:
    the reference root's own `extensions/` subtree is the DLC, indexed separately under
    `dlc:<name>` by `_iter_mod_files`, so a nested match here would count it twice.
    """
    yield from _scan.iter_mod_xml(base, _scan.under(*_SCRIPT_DIRS), unreadable)


def _iter_mod_files(mod_dir: Path, unreadable: list | None = None):
    """Yield (vpath, root) for a mod's (or DLC's) md/aiscripts — packed and loose, direct
    AND behind repeated `extensions/<owner>/` prefixes (a patch of another extension's
    script, CLAUDE.md gotcha #6). The vpath keeps the full nested path, so the patched
    file's OWNER is visible in every row. See `SCRIPT_SCOPE`."""
    yield from _scan.iter_mod_xml(mod_dir, _scan.under_nested(*_SCRIPT_DIRS), unreadable)


def index_roots(ext_dir: Path) -> list[Path]:
    """The install roots an index built from *ext_dir* covers: the named root first, then
    every other configured root (profile extensions, Steam workshop).

    ONE definition, used by `build_index` AND by the freshness stamp and check
    (AUDIT-2026-09-24 FR-3). The index walked all of these while the stamp covered only
    the named one, so a mod added or edited in the profile or workshop root left the
    index FRESH -- and x4xref exists to make NEGATIVES admissible.
    """
    # An UNCONFIGURED root (None on a machine with no X4) is not a root: including it made
    # every freshness stamp and check call Path(None) and crash (the cold-clone run caught it).
    roots = [ext_dir] if ext_dir is not None else []
    try:
        for extra in _registry.default_installed_dirs():
            if extra not in roots:
                roots.append(extra)
    except Exception:  # silent-ok: a root set that cannot be derived must not
        # empty the index. The NAMED root is still walked, and the caller's own
        # `unreadable` channel still reports whatever that walk could not read -- so
        # the failure mode is a NARROWER population, never a silent zero. Recording it
        # here would fire on every machine with no profile extensions dir, which is
        # the normal state.
        pass
    return roots


def build_index(reference: Path, ext_dir: Path,
                unreadable: list | None = None,
                dlc_dirs: list[Path] | None = None) -> list[XrefRow]:
    """Index base + DLC + every installed mod's MD/aiscripts.

    *unreadable* collects files that would not parse. An index is the
    denominator behind every "nobody references X" answer this tool gives, so a
    file silently missing from it turns a negative into a guess.
    """
    rows: list[XrefRow] = []

    # Base game (reference root, excluding the DLC extensions subtree).
    for vpath, root in _iter_source_files(reference, "base", unreadable):
        _walk(root, "base", vpath, rows)
    # DLC. Ask Config, do NOT re-derive from `reference / "extensions"`: the two
    # mini-DLC are never unpacked into reference\ (their content lives in
    # ext_*.cat inside the game install), so a directory walk there misses them
    # entirely -- 13 md/aiscript files that indexed 0 rows, which silently breaks
    # every NEGATIVE this index exists to make admissible. `_scan.iter_mod_xml`
    # was already packed-aware; only the directory list was wrong.
    if dlc_dirs is None:
        dlc_dirs = _merge.Config(reference=reference).dlc_dirs()
    for dlc in sorted(dlc_dirs, key=lambda p: p.name):
        # Mod-style enumeration: a DLC patching another DLC's script sits at
        # <dlc>/extensions/<other>/md, which the direct-only walk would miss.
        for vpath, root in _iter_mod_files(dlc, unreadable):
            _walk(root, f"dlc:{dlc.name}", vpath, rows)
    # Installed mods.
    #
    # A MISSING extensions dir is a SETUP failure, not an empty modlist, and
    # the two used to be the same wordless `if`. `_registry.default_installed_dirs`
    # already draws that line in its own docstring — "0 installed mods"
    # must not read as "you have no mods" when the truth is "I looked in the
    # wrong place" — and this walk sat on the wrong side of it. Recorded
    # into `unreadable`, which is the channel every caller already renders, so
    # the state becomes visible instead of being skipped in silence.
    if not ext_dir.is_dir() and unreadable is not None:
        unreadable.append(str(ext_dir) + " (the extensions directory does not "
                          "exist, so NO installed mod was indexed at all)")
    # EVERY CONFIGURED ROOT, not just the one named. `_registry.default_installed_dirs`
    # returns up to THREE -- game-root extensions, the profile's extensions, and the
    # Steam workshop content dir -- and this walked only the one it was handed. x4xref
    # exists to make NEGATIVES admissible ("nobody calls X"), so a population missing a
    # whole configured root makes every negative a claim about part of the install
    # while reading as a claim about the install.
    #
    # MEASURED on this machine when the gap was found: the two populations are
    # IDENTICAL (the profile extensions dir is empty and no workshop dir exists), so
    # the index digest does not move here. That is the state in which such a gap
    # survives -- a recorded cost of zero is where a wrong denominator hides.
    roots = index_roots(ext_dir)
    scanned = [r for r in roots if r.is_dir()]
    # NO failure channel entry for an ABSENT candidate root. `default_installed_dirs`
    # returns CANDIDATES -- the profile's extensions dir and the Steam workshop dir
    # are routinely absent (X4 downloads subscribed mods straight into game-root
    # extensions, and this machine has no workshop dir at all), so their absence is a
    # normal state and not a read failure. Recording it filled `unreadable` on every
    # clean build, which a test correctly refused. The widening below IS the fix.
    seen_folders = set()
    for root_dir in scanned:
        for m in _registry.mods("installed", [root_dir]):
            # A mod present in two roots is ONE mod to the engine; index it once and
            # keep the first, which is the order default_installed_dirs declares.
            if m["folder"] in seen_folders:
                continue
            seen_folders.add(m["folder"])
            for vpath, root in _iter_mod_files(Path(m["path"]), unreadable):
                _walk(root, m["folder"], vpath, rows)
    return rows
