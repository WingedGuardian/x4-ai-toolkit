r"""Heuristic linter for X4 *script-expression* breakages the XSD can't see.

The bundled `md.xsd`/`aiscripts.xsd` validate XML *structure* — element/attribute
shape — but treat every attribute VALUE as an opaque `xs:string`. X4's script
mini-language lives inside those values (`exact="$list.random"`,
`text="'%s'.[$a]"`, `in="[a, b]"`), so a broken expression sails through schema
validation and only fails when the *engine* parses it at load (-> debug.txt).

This module is the pattern-detectable half of that gap (the authoritative half is
the `--debug` debug.txt correlation): a small, measured rule set over attribute
values, seeded from KNOWLEDGEBASE.md "Version Migration Map". It is ADVISORY by
design (findings are `warn`/`info`, never gating) — a regex heuristic should flag
for review, not fail a build. Every rule below was measured against vanilla 9.0
`reference\` at ~0 false positives before being added. Grows as we learn; a rule
that proves noisy (e.g. the dropped `{a.b, c.d}` catch, which fires on valid
`.{[list]}` accessors in md/) is removed, not shipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from x4validate import _xref


# Diff-patch attributes carry XPath, NOT the X4 script mini-language — a different
# grammar where `'...'[` (string literal before a predicate) is valid. Never lint them.
_SKIP_ATTRS = frozenset({"sel", "if"})


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    attr: str | None  # None = match any attribute; else only this attribute name
    pattern: re.Pattern
    severity: str  # "warn" | "info"  (never "error" — this is a heuristic)
    note: str


# Keep in sync with KB "Version Migration Map" (expression-grammar breaks).
# Measured vs vanilla 9.0 reference\ : each pattern flags 0 legitimate lines.
RULES: list[_Rule] = [
    _Rule("random_call", None, re.compile(r"\brandom\s*\("), "warn",
          "9.0 removed the random(min,max) call form -- use `$list.random` "
          "(random element) or `random.range.{min,max}`"),
    _Rule("fmt_missing_dot", None, re.compile(r"'[^']*'\["), "warn",
          "interpolation string missing the '.' before '[' -- must be `'...'.[...]`"),
    _Rule("list_literal_in_braces", "in", re.compile(r"^\s*\{"), "warn",
          "list iterables use `[...]`; `{...}` now parses as a {page,line} textref "
          "-> evaluates to null -> `'null' is not a list` at runtime"),
    _Rule("keys_list_count", None, re.compile(r"\.keys\.list\.count\b"), "info",
          "deprecated lookup -- use `.keys.count` (engine warns 'inefficient pattern')"),
]


@dataclass
class ExprFinding:
    vpath: str
    line: int
    rule_id: str
    note: str
    snippet: str
    severity: str


def _literal_tokens(value: str):
    """(code with literals masked, missing-format-dot) without crossing literals."""
    code = list(value)
    missing_dot = False
    index = 0
    while index < len(value):
        if value[index] not in ("'", '"'):
            index += 1
            continue
        quote, start = value[index], index
        index += 1
        while index < len(value):
            if value[index] == "\\":
                index += 2
                continue
            if value[index] == quote:
                index += 1
                following = index
                while following < len(value) and value[following].isspace():
                    following += 1
                missing_dot |= following < len(value) and value[following] == "["
                break
            index += 1
        code[start:min(index, len(value))] = " " * (min(index, len(value)) - start)
    return "".join(code), missing_dot


def scan_tree(root, vpath: str) -> list[ExprFinding]:
    """Apply every rule to every attribute value in one parsed script tree.

    Operates on parsed attributes (not raw lines) so a match inside an XML
    `<!-- comment -->` or a non-expression context can't false-fire. lxml gives
    a line per element (`sourceline`), not per attribute — that matches how every
    other x4validate check reports (`.sourceline`)."""
    out: list[ExprFinding] = []
    for el in root.iter():
        if not isinstance(el.tag, str):  # skip comments / PIs
            continue
        line = el.sourceline or 0
        for attr, val in el.attrib.items():
            if attr in _SKIP_ATTRS:  # diff XPath selector, not a script expression
                continue
            code, missing_dot = _literal_tokens(val)
            for rule in RULES:
                if rule.attr is not None and rule.attr != attr:
                    continue
                matches = missing_dot if rule.rule_id == "fmt_missing_dot" else rule.pattern.search(code)
                if matches:
                    out.append(ExprFinding(vpath, line, rule.rule_id, rule.note,
                                           val.strip()[:120], rule.severity))
    return out


def scan_mod(mod_dir) -> list[ExprFinding]:
    """Lint every md/ + aiscripts/ file in a mod (packed or loose)."""
    out: list[ExprFinding] = []
    for vpath, root in _xref._iter_mod_files(mod_dir):
        out.extend(scan_tree(root, vpath))
    return out
