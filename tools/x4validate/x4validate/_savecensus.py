"""`x4save census`: count a save's <component> elements, grouped by the keys asked for.

WHY IT EXISTS. "How many Xenon ships of each macro are in this save?" used to mean a gzip+expat
script run against the save PATH, which the save guard refuses as an opaque program (#19).
This is the supported route: read-only, given a save NAME, streamed.

WHAT IT COUNTS. Every <component> element, at any depth. MEASURED layout of a 9.0 save:
components nest through <connections><connection><component>; `class=`, `macro=` and, on the
object that owns them, `owner=`. A component's OWNER is its own `owner=`, else the nearest
ancestor component's (a turret carries none and belongs to its station or ship). The rows
that took an inherited owner are counted and printed.

WHAT "PARENT" MEANS. The nearest ANCESTOR component (never the component itself) whose class
matches `--parent-class` (globs; default `station,ship_*`; `@module` names the station-module
classes). The search stops at the first station or ship_* ancestor -- the object the component
belongs to -- so a turret on a ship docked at a station finds the ship, not the station's dock
module. `--parent-anywhere` lifts that stop. `station-macro` is the nearest station ancestor
under the same rule. A component with no such ancestor groups as "(none)".

DENOMINATORS. Every run prints how many components were seen, how many matched, how many had no
`class=` (they can never match `--class` and group as "(no class)") or no `macro=`, and how many
decompressed bytes were read. A zero is printed against that count, so an ABSENCE over the whole
save cannot be mistaken for a NON-ANSWER; an unreadable or truncated save is rc 2, never a count.

MEMORY is bounded by the number of distinct groups plus the nesting depth, not by save size:
nothing per component is kept.
"""
from __future__ import annotations

import fnmatch
import gzip
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from xml.parsers import expat

from . import _savecli

#: The keys `--by` accepts, in the order the help lists them.
KEYS = ("macro", "owner", "class", "parent-class", "parent-macro", "station-macro")

#: Station-module component classes, for `--parent-class @module`. MEASURED 2026-11-06 as the
#: `class=` values of macros under assets/structures (base + DLC) that are station modules:
#: buildmodule, connectionmodule, defencemodule, dockarea, habitation, pier, processingmodule,
#: production, radar, storage, ventureplatform, welfaremodule.
MODULE_CLASSES = ("buildmodule", "connectionmodule", "defencemodule", "dockarea", "habitation", "pier",
                  "processingmodule", "production", "radar", "storage", "ventureplatform", "welfaremodule")
ALIASES = {"@module": MODULE_CLASSES}
DEFAULT_PARENT = ("station", "ship_*")

NONE = "(none)"
NO_CLASS = "(no class)"
NO_MACRO = "(no macro)"


class CensusArgumentError(ValueError):
    """A census argument that cannot be honoured: rc 2, a refusal, never a count."""


def split_list(values) -> list[str]:
    """`a,b` and repeated flags alike -> ['a', 'b']; blanks dropped."""
    out: list[str] = []
    for v in values or ():
        out.extend(p.strip() for p in str(v).split(",") if p.strip())
    return out


def parse_by(text: str) -> list[str]:
    keys = split_list([text])
    if not keys:
        raise CensusArgumentError("--by needs at least one key: " + ",".join(KEYS))
    bad = [k for k in keys if k not in KEYS]
    if bad:
        raise CensusArgumentError(f"--by: unknown key(s) {', '.join(bad)}; known: {', '.join(KEYS)}")
    if len(set(keys)) != len(keys):
        raise CensusArgumentError("--by: a key is repeated")
    return keys


def expand_parent(patterns) -> list[str]:
    out: list[str] = []
    for p in patterns:
        if p.startswith("@"):
            if p.lower() not in ALIASES:
                raise CensusArgumentError(f"--parent-class: unknown alias {p}; known: {', '.join(ALIASES)}")
            out.extend(ALIASES[p.lower()])
        else:
            out.append(p)
    if not out:
        raise CensusArgumentError("--parent-class needs at least one class pattern")
    return out


class _Glob:
    """Case-insensitive fnmatch over a list of patterns (any may match), memoised per value:
    the values (classes, owners, macros) repeat, so the cache is bounded by distinct values."""

    def __init__(self, patterns):
        self.patterns = list(patterns or ())
        self._rx = (re.compile("|".join(fnmatch.translate(p) for p in self.patterns), re.IGNORECASE)
                    if self.patterns else None)
        self._memo: dict[str, bool] = {}

    def __call__(self, value: str) -> bool:
        if self._rx is None:
            return True
        hit = self._memo.get(value)
        if hit is None:
            hit = self._memo[value] = bool(self._rx.fullmatch(value))
        return hit


@dataclass
class CensusResult:
    groups: Counter = field(default_factory=Counter)   # key tuple -> count
    seen: int = 0               # every <component> element: the denominator
    matched: int = 0
    no_class: int = 0           # components with no class= (never class-matched), over ALL seen
    no_macro: int = 0           # of MATCHED components, those with no macro=
    owner_inherited: int = 0    # of MATCHED components, those whose owner came from an ancestor
    scanned_bytes: int = 0


def census(path: Path, *, classes=None, owners=None, macros=None, parent_classes=None,
           parent_anywhere: bool = False, by=("macro",)) -> CensusResult:
    """Stream *path* and count matching components grouped by *by*. Raises SaveUnreadable."""
    by = list(by)
    want_class, want_owner, want_macro = _Glob(classes), _Glob(owners), _Glob(macros)
    is_parent = _Glob(expand_parent(parent_classes) if parent_classes else DEFAULT_PARENT)
    need_parent = "parent-class" in by or "parent-macro" in by
    need_station = "station-macro" in by
    res = CensusResult()
    # One entry per OPEN component: (class, macro, owner, is_parent, is_station, is_object).
    stack: list[tuple[str, str, str, bool, bool, bool]] = []
    depth_marks: list[bool] = []     # per open element: was it a component (so `end` knows to pop)
    parser = expat.ParserCreate()

    def nearest(test, value_of):
        for entry in reversed(stack):
            if test(entry):
                return value_of(entry)
            if entry[5] and not parent_anywhere:
                break
        return NONE

    def start(name, attrs):
        if name != "component":
            depth_marks.append(False)
            return
        depth_marks.append(True)
        res.seen += 1
        cls = attrs.get("class", "")
        macro = attrs.get("macro", "")
        own = attrs.get("owner", "")
        inherited = False
        if not own and stack:
            own, inherited = stack[-1][2], bool(stack[-1][2])
        if not cls:
            res.no_class += 1
        lc = cls.lower()
        if (cls or not want_class.patterns) and want_class(cls) and want_owner(own) and want_macro(macro):
            res.matched += 1
            res.no_macro += not macro
            res.owner_inherited += inherited
            key = []
            for k in by:
                if k == "macro":
                    key.append(macro or NO_MACRO)
                elif k == "owner":
                    key.append(own or NONE)
                elif k == "class":
                    key.append(cls or NO_CLASS)
                elif k == "parent-class":
                    key.append(nearest(lambda e: e[3], lambda e: e[0] or NO_CLASS))
                elif k == "parent-macro":
                    key.append(nearest(lambda e: e[3], lambda e: e[1] or NO_MACRO))
                else:   # station-macro
                    key.append(nearest(lambda e: e[4], lambda e: e[1] or NO_MACRO))
            res.groups[tuple(key)] += 1
        stack.append((cls, macro, own, bool(cls) and (need_parent and is_parent(cls)),
                      lc == "station" and need_station, lc == "station" or lc.startswith("ship_")))

    def end(name):
        if depth_marks.pop():
            stack.pop()

    def doctype(*args):
        raise _savecli.SaveUnreadable(f"{path}: DTD/entity declarations are not supported in save XML")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = doctype
    try:
        with gzip.open(path, "rb") as fh:
            while True:
                chunk = fh.read(_savecli._CHUNK)
                if not chunk:
                    break
                res.scanned_bytes += len(chunk)
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error, expat.ExpatError) as exc:
        raise _savecli.SaveUnreadable(f"{path}: decompression/XML parse failed ({exc})") from exc
    return res


def _filters_text(classes, owners, macros) -> str:
    parts = [f"--{n} {','.join(v)}" for n, v in (("class", classes), ("owner", owners), ("macro", macros)) if v]
    return " ".join(parts) if parts else "(no filter: every component)"


def ordered(groups: Counter) -> list[tuple[tuple, int]]:
    return sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))


def cmd_census(path: Path, *, classes, owners, macros, parent_classes, parent_anywhere, by,
               limit: int = 0, as_json: bool = False, out=None) -> int:
    import json
    import sys
    from . import _scan

    out = sys.stdout if out is None else out
    parents = expand_parent(parent_classes) if parent_classes else list(DEFAULT_PARENT)
    res = census(path, classes=classes, owners=owners, macros=macros, parent_classes=parents,
                 parent_anywhere=parent_anywhere, by=by)
    rows = ordered(res.groups)
    if as_json:
        doc = {
            "schema": "x4save.census/1",
            "save": str(path),
            "filters": {"class": classes or [], "owner": owners or [], "macro": macros or []},
            "by": by,
            "parent_class": parents,
            "parent_anywhere": parent_anywhere,
            "components_seen": res.seen,
            "matched": res.matched,
            "no_class": res.no_class,
            "no_macro": res.no_macro,
            "owner_inherited": res.owner_inherited,
            "scanned_bytes": res.scanned_bytes,
            "groups": [{"key": dict(zip(by, k)), "count": n} for k, n in rows],
        }
        print(json.dumps(doc, indent=1), file=out)
        return 0 if res.matched else 1

    filt = _filters_text(classes, owners, macros)
    print(f"save: {path}", file=out)
    print(f"  filter     {filt}", file=out)
    print(f"  grouped by {','.join(by)}", file=out)
    if any(k in by for k in ("parent-class", "parent-macro", "station-macro")):
        stop = "anywhere above it" if parent_anywhere else "up to its own station or ship_*"
        print(f"  parent     nearest ancestor of class {','.join(parents)}, searched {stop}", file=out)
    if not res.matched:
        print(f"NO MATCH: 0 of {res.seen:,} components matched {filt} over {res.scanned_bytes:,} "
              "decompressed bytes -- the whole save was read, so this is an absence, not a non-answer",
              file=out)
        if res.no_class:
            print(f"  {res.no_class:,} component(s) had no class= and can never match --class", file=out)
        return 1
    shown = rows if limit == 0 else rows[:limit]
    widths = [max(len(k), *(len(key[i]) for key, _ in shown)) for i, k in enumerate(by)]
    print("", file=out)
    print(f"  {'count':>8}  " + "  ".join(k.ljust(w) for k, w in zip(by, widths)).rstrip(), file=out)
    for key, n in shown:
        print(f"  {n:8,d}  " + "  ".join(v.ljust(w) for v, w in zip(key, widths)).rstrip(), file=out)
    print("  " + _scan.count_line(len(shown), len(rows), "groups"), file=out)
    print(f"\n  {res.matched:,} of {res.seen:,} components matched, over {res.scanned_bytes:,} decompressed bytes",
          file=out)
    print(f"  {res.no_class:,} component(s) had no class= (never matched by --class; grouped as "
          f"'{NO_CLASS}')", file=out)
    print(f"  {res.no_macro:,} matched component(s) had no macro= (grouped as '{NO_MACRO}')", file=out)
    print(f"  {res.owner_inherited:,} matched component(s) had no owner= and took their nearest ancestor "
          "component's", file=out)
    return 0
