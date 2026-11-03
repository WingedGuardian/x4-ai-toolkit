"""In-game display text: resolve `{page,id}` references the way the game shows them.

The effective store keeps the MERGED English text (`t/0001.xml` overlaid by `t/0001-l044.xml`, every mod's
t-file diffs applied) as raw strings in its `texts` table. This module turns a reference into what the
player reads. The grammar is the corpus's, MEASURED 2026-11-01 over the merged tree (76,983 entries):

* `(...)` is an author comment and is not displayed. 29,031 entries START with one, e.g.
  `(XEN XL Omega Turret Mk1){20202,603} {20111,5041} ...` -- the comment is a label, the refs are the name.
  0 entries have unbalanced parentheses.
* `\\(` and `\\)` are literal parentheses (950 / 953 occurrences), e.g. `(Wyvern \\(Gas\\) Vanguard)`.
* `{page,id}` inside a text is resolved recursively (10,964 entries contain one); a cycle or a reference
  to text that does not exist is left as written and COUNTED, never silently emptied.

MEASURED resolution over the live store: 2,944 of 2,967 macro identification names resolve (99.2%);
505 of 507 ship macros. Spot checks: {20101,72101} -> "M0", {20105,9074} -> "XEN XL Omega Turret Mk1".
"""
from __future__ import annotations

import re

REF = re.compile(r"\{\s*(\d+)\s*,\s*(\d+)\s*\}")
MAX_DEPTH = 10


def strip_comments(s: str) -> str:
    """Drop `(...)` comments (nesting-aware) and unescape `\\(` `\\)`; other escapes pass through."""
    out, depth, i = [], 0, 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if depth == 0:
                out.append(nxt if nxt in "()" else c + nxt)
            i += 2
            continue
        if c == "(":
            depth += 1
        elif c == ")" and depth:
            depth -= 1
        elif depth == 0:
            out.append(c)
        i += 1
    return "".join(out)


class Resolver:
    """Resolve references against one text map; `problems` counts what could not be resolved."""

    def __init__(self, texts: dict[tuple[str, str], str]):
        self.texts = texts
        self.problems = {"dangling": 0, "cycle": 0, "depth": 0}

    def ref(self, page: str, tid: str, _seen: tuple = (), _depth: int = 0) -> tuple[str | None, str]:
        """(display text, "ok") or (None, why): why is dangling / cycle / depth."""
        key = (str(page), str(tid))
        if key in _seen:
            return None, "cycle"
        if _depth > MAX_DEPTH:
            return None, "depth"
        if key not in self.texts:
            return None, "dangling"
        status = ["ok"]

        def sub(m):
            # A failure ANYWHERE below taints this whole text: the inner call can return partial
            # text with a non-ok status (a cycle two levels down), and that must not read as "ok".
            txt, why = self.ref(m.group(1), m.group(2), _seen + (key,), _depth + 1)
            if why != "ok":
                status[0] = why
            return m.group(0) if txt is None else txt
        return REF.sub(sub, strip_comments(self.texts[key])).strip(), status[0]

    def value(self, raw: str) -> tuple[str, str]:
        """Resolve every reference in an attribute value: (display, status). A literal is returned as-is."""
        status = ["ok" if REF.search(raw) else "literal"]

        def sub(m):
            txt, why = self.ref(m.group(1), m.group(2))
            if why != "ok":
                status[0] = why
                self.problems[why] += 1
            return m.group(0) if txt is None else txt
        return REF.sub(sub, raw).strip(), status[0]


def load(con) -> dict[tuple[str, str], str] | None:
    """The store's text map, or None when the store predates the `texts` table (rebuild to enable)."""
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='texts'").fetchone()
    if not has:
        return None
    return {(p, t): v for p, t, v in con.execute("SELECT page, t, text FROM texts")}
