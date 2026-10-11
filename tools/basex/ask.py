r"""Ask the corpus a question and get an answer that carries its own denominator.

The whole point
---------------
A discovery tool that cannot prove a negative is just a faster way to guess. Two
things stood between BaseX and a usable "nobody references X":

  Gap 1  62% of mod XML was PACKED and invisible  -> fixed by stage.py
  Gap 2  the index held files AS WRITTEN, not the effective tree -> fixed by x4eff
  Gap 3  SKIPCORRUPT drops files SILENTLY          -> fixed by coverage.py + this

Gap 3 is the one that turns "more complete" into "proof". A zero-result is only
a finding if you can say what it is zero *over*:

    "nothing references turret_x"                       <- not a claim
    "0 hits over 13,672 of 13,684 documents; the 12
     exclusions are malformed XML the engine also
     cannot read, listed by name"                       <- a claim

So this REFUSES to render a zero-result as a negative finding unless coverage.json
says the index is complete or fully accounted. A positive result needs no such
guard — one hit is one hit regardless of what else was missed.

The denominator counts every document in the database, so a zero is certified only for a
query that searched all of them:

  * A query addressing PART of a database inside a reach call's own arguments --
    doc(...), db:get('<db>', '<path>'), collection('<db>/<path>') -- or naming its
    database through a non-literal, or naming a database other than --db, is refused
    before it runs (rc 2).
  * Raw xq zeros require the restricted whole-database content-search grammar:
    a literal collection/db:get/db:open root, child/descendant/attribute paths,
    relative content predicates, comparisons to literals, and/or, exists/empty/not.
    Unrecognized expressions still RUN and report positive results; their zeros
    are non-answers (rc 4). Variables, FLWOR, numeric predicates and conditional
    expressions are not certified. Token scans supply diagnostics only.
    refs/attr use internal whole-database queries; attr accepts lexical QNames only.

Which DB
--------
  --db x4raw  (default)  files as written  -> "who WROTE this, in which mod"
  --db x4eff             effective tree    -> "what does the ENGINE see"

Prefer x4eff for a claim about what is live; x4raw for provenance/authorship.

Usage
-----
  uv run python ask.py refs <macro_or_ware_id> [--db x4eff]
      exact forms: @ref/@name/@component = id; @macro = id|macro.id; @ware = id|ware.id;
      bullet/@class = id; add_blueprints/@wares|@macros list tokens. $variable expressions
      and other expression attributes (@exact, @value) are NOT searched.
  uv run python ask.py attr <attribute-name>
  uv run python ask.py xq   '<raw xquery>'
  uv run python ask.py xq   --file <query.xq>
  uv run python ask.py attr name --limit 50 --offset 50

Paging is optional; omitted/--limit 0 means unlimited, offset defaults to 0.
Limits display whole result items while totals and semantic checks cover the full
query. Unsupported requested paging (for example, a prolog) refuses with rc 2;
omit paging flags to use the existing unlimited fallback. Paging bounds output,
not query work. See QUERIES.md for the exact certification subset.

From Git Bash, pass an xq query with --file: MSYS rewrites path-like parts of command-line
arguments before Python sees them (`//` becomes `/`; a leading `/ware` becomes
`C:/Program Files/Git/ware`), and a zero from an argument there is refused.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import basex_run
import preflight
from content_query import certification_issue, is_qname

HERE = Path(__file__).resolve().parent
BASEX_DIR = HERE / "basex"



def run_xq(xquery: str) -> str:
    # UTF-8 both ways, the query passed as a file: see basex_run (issue #7).
    rc, stdout, stderr = basex_run.run_query(xquery, BASEX_DIR)
    if rc != 0:
        raise RuntimeError(stderr.strip()[:500])
    return stdout.rstrip("\n")


#: Separates the item count from the payload in a wrapped query. Printable on
#: purpose: U+0001 is not a legal XML character, so a control byte makes the
#: wrapper fail to compile and silently costs every count.
_SEP = "@@ASK-COUNT-SEP@@"


def run_counted(xquery: str) -> tuple[str, int | None]:
    """Run *xquery* and return (output, item_count).

    The count is the number of items in the RESULT SEQUENCE. Until 2026-08-01
    this module reported `len(output_lines)` as "hits", which is a different
    number entirely: 847 occurrences across 4 documents printed as "32 hit(s)",
    because BaseX had wrapped the serialized sequence over 32 lines.

    That mattered far beyond a cosmetic miscount — the zero-result guard, the
    whole point of this tool, keyed off the line list being empty. A `count()`
    query returning 0 emits the single line "0", so the guard never ran and a
    zero result rendered as "1 hit(s)".

    *item_count* is None when the wrapper will not compile (a query with its own
    prolog, say). The caller must then say the count is unavailable rather than
    quote the line count as though it were meaningful.
    """
    wrapped = f'let $__ask := ( {xquery} ) return (count($__ask), "{_SEP}", $__ask)'
    try:
        raw = run_xq(wrapped)
    except RuntimeError:
        return run_xq(xquery), None
    head, sep, body = raw.partition(_SEP)
    if not sep:
        return raw, None
    try:
        return body.lstrip("\n"), int(head.strip())
    except ValueError:
        return raw, None


@dataclass(frozen=True)
class Page:
    output: str
    total: int
    shown: int
    meaning: str


def run_page(xquery: str, limit: int, offset: int) -> Page:
    """Measure the FULL sequence once, then display whole items; never fall back."""
    selected = (f'subsequence($__ask, {offset+1}, {limit})' if limit
                else f'subsequence($__ask, {offset+1})')
    # An ATTRIBUTE (or namespace) node cannot be serialized standalone (SENR0001), so
    # serialize() of one raised and every `--limit` over an `//@attr` query was refused
    # (v4.0.0 review R5-5). Its string value is what a reader sees, so that is measured.
    wrapped = f'''let $__ask := ( {xquery} )
let $__total := count($__ask)
let $__page := {selected}
let $__ser := function($__i) {{ serialize(
  if ($__i instance of attribute() or $__i instance of namespace-node()) then string($__i) else $__i) }}
let $__meaning :=
  if ($__total = 0) then "empty"
  else if (every $__item in $__ask satisfies normalize-space($__ser($__item)) = "")
       then "empty-serialization"
  else if ($__total = 1 and normalize-space($__ser($__ask)) = "false") then "false"
  else if ($__total = 1 and normalize-space($__ser($__ask)) = "0") then "zero"
  else "positive"
return (concat($__total, ":", count($__page), ":", $__meaning), "{_SEP}", $__page)'''
    try:
        raw = run_xq(wrapped)
        head, separator, body = raw.partition(_SEP)
        total, shown, meaning = head.strip().split(':')
        if not separator or meaning not in ('empty', 'empty-serialization', 'false', 'zero', 'positive'):
            raise ValueError('missing or invalid page metadata')
        return Page(body.lstrip('\n'), int(total), int(shown), meaning)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError('cannot safely count and page this query; omit --limit/--offset '
                           f'to run unlimited. No unlimited fallback was executed. {exc}') from exc


def _nonnegative(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('expected a nonnegative integer') from None
    if n < 0:
        raise argparse.ArgumentTypeError('expected a nonnegative integer')
    return n


def _looks_like_zero_count(lines: list[str]) -> bool:
    """A single serialized `0` — i.e. count(...) that counted nothing."""
    return len(lines) == 1 and lines[0].strip() == "0"


def load_coverage(db: str) -> dict:
    """Per-DB coverage report. x4raw and x4eff have different denominators, so
    they must never share one — a claim about the effective tree cannot borrow
    the raw index's completeness."""
    path = BASEX_DIR / f"coverage-{db}.json"
    if not path.is_file():
        return {}
    try:
        cov = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # A truncated or half-written coverage file used to escape as a raw
        # traceback with rc 1, which in this toolkit means "the thing you asked
        # about has findings" — when the truth is "this artifact
        # is unreadable". F39 removed exactly that confusion from the CLIs; this
        # reader was missed, and it sits on the path every query takes.
        #
        # {} is the SAFE value, not a shrug: with no coverage,
        # supports_negative_claim is falsy and the negative is REFUSED. It is
        # named because a silent {} looks like an index that was never stamped.
        print(f"  coverage-{db}.json is unreadable ({type(exc).__name__}: {exc}); "
              f"treating this index as carrying NO coverage, so no negative "
              f"claim can be made from it.", file=sys.stderr)
        return {}
    if not isinstance(cov, dict):
        print(f"  coverage-{db}.json does not contain a JSON object; treating "
              f"this index as carrying NO coverage.", file=sys.stderr)
        return {}
    return cov if cov.get("db") == db else {}


def staleness_verdict(db: str):
    """Does this index still describe the current world? (see staleness.py)

    Coverage answers "how much was indexed"; this answers "as of when". A stale
    index is neither an absence nor a non-answer — it is an answer about a world
    that has moved on, and x4eff served 858 wrong values for eleven days because
    nothing asked. Monkeypatched in tests.
    """
    import staleness
    try:
        reference, extensions, engine = staleness._defaults()
    except (staleness.EngineUnavailable, ImportError) as exc:
        # MEASURED 2026-08-24 on a proven-cold checkout: this used to escape as a
        # raw traceback with rc **1** — which in this toolkit means "the thing you
        # asked about has findings", when the truth was "this toolkit is not set
        # up". Opposite responses from whoever reads the code, and exactly the
        # confusion F39 removed from the CLIs. Reported as UNDETERMINABLE rather
        # than STALE: the query itself still ran and its positive answers stand;
        # what nobody established is whether the index still describes the world.
        return staleness.Verdict(False, [str(exc)], db, determinable=False)
    # THE GUARD ABOVE COVERS `_defaults()` ONLY, AND THERE ARE TWO CALLERS.
    # `check()` calls `fingerprint()` -> `_core()` -> `from x4validate import
    # _freshness`, which can raise EngineUnavailable on a HALF-INSTALL even when
    # `_paths` imported fine. `staleness.check` deliberately re-raises that one
    # ("NOT OURS TO CATCH -- main() owns this") and it is right about
    # `staleness.main()`; ask.py is the other caller and was catching nothing.
    #
    # VERIFIED by the v3.1.0 reviewer with `_defaults` stubbed to succeed and
    # `_core` raising: the exception escaped `staleness_verdict` and reached the
    # user as a raw traceback with rc 1 — "the thing you asked about has
    # findings", when the truth is "this toolkit is not set up". That is exactly
    # the F39 confusion the block above documents, one call deeper. A 243-file
    # half-install really happened, which is why the installers have a CI step.
    try:
        return staleness.check(BASEX_DIR / f"coverage-{db}.json",
                               reference, extensions, engine, db)
    except (staleness.EngineUnavailable, ImportError) as exc:
        return staleness.Verdict(False, [str(exc)], db, determinable=False)


def _xq_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _strip_xq_comments(text: str) -> str:
    """The query with each XQuery comment (they nest) replaced by one space.

    Lexed in ONE pass with string literals (`_xq_lex`): a regex over the raw text used to
    read `'(:' || ... || ':)'` -- two string literals -- as a comment and delete the code
    between them, so `_db_reaches` and the token scan never saw it.
    """
    return _xq_lex(text, blank_strings=False)


#: The databases `--db` accepts, i.e. the only ones ask.py holds a coverage report for.
DBS = ("x4raw", "x4eff")

# --- what does the query ADDRESS? (AUDIT-2026-09-24 BX-1) ----------------------
#
# The coverage denominator describes a WHOLE database. A zero is a negative over that
# denominator only if the query searched that whole database, so ask.py reads every
# call that reaches a database and classifies it. MEASURED by the audit:
# `db:get('x4eff','no/such/typo.xml')//ware` printed "NEGATIVE CONFIRMED over 10970 of
# 10970 documents", rc 0 -- a zero over ONE path that does not exist, certified with the
# whole database's count. The old guard matched only `name('<db>')` with nothing after
# the literal, so a second argument, a variable db name and doc() all walked past it.
#
# Calls whose first argument names a database (BaseX's db module, and fn:collection /
# fn:uri-collection / fn:doc whose argument is `<db>[/<path>]`):
#: Always addresses a DOCUMENT or a node, never the whole database.
_DOC_FNS = ("doc", "db:get-id", "db:get-pre", "db:open-id", "db:open-pre",
            "db:get-value", "db:get-binary", "db:retrieve")
#: Whole database with one argument; a SECOND argument is a path inside it.
_PATH_ARG_FNS = ("db:get", "db:open", "db:list", "db:list-details", "db:dir", "db:exists")
#: Whole database whatever follows (index lookups, metadata).
_WHOLE_DB_FNS = ("db:text", "db:text-range", "db:attribute", "db:attribute-range",
                 "db:token", "db:info", "db:property")
#: `<db>` or `<db>/<path>` in the single literal argument.
_URI_FNS = ("collection", "uri-collection")
_REACH_FNS = _DOC_FNS + _PATH_ARG_FNS + _WHOLE_DB_FNS + _URI_FNS
_REACH_CALL = re.compile(
    r"(?<![\w:.\-])(?:fn:)?("
    + "|".join(re.escape(f) for f in sorted(_REACH_FNS, key=len, reverse=True))
    + r")\s*\(")
_LITERAL_ARG = re.compile(r"\s*(['\"])((?:(?!\1).|\1\1)*)\1\s*([,)])", re.S)


def _db_reaches(query: str) -> list[dict]:
    """Every call in *query* (comments already stripped) that reaches a database.

    Each is {"call", "db", "scoped"}: `db` is None when the name is not a string
    literal (a variable, a concatenation, no argument) -- then nobody can say which
    database was searched; `scoped` is True when the call addresses less than the
    whole database (a document, a node, a path inside it).
    """
    reaches = []
    for m in _REACH_CALL.finditer(query):
        fn = m.group(1)
        lit = _LITERAL_ARG.match(query, m.end())
        if lit is None:
            close = query.find(")", m.end())
            reaches.append({"call": query[m.start():close + 1 if close >= 0 else m.end()],
                            "db": None, "scoped": True})
            continue
        quote, after = lit.group(1), lit.group(3)
        value = lit.group(2).replace(quote * 2, quote)       # XQuery escapes '' / ""
        call = query[m.start():lit.end()] + ("" if after == ")" else "...)")
        if fn in _URI_FNS or fn == "doc":
            parts = [p for p in value.strip().split("/") if p]
            db = parts[0] if parts else None
            scoped = fn == "doc" or len(parts) > 1
        else:
            db = value.strip() or None
            scoped = fn in _DOC_FNS or (fn in _PATH_ARG_FNS and after == ",")
        reaches.append({"call": call, "db": db, "scoped": scoped or db is None})
    return reaches


# --- can the query narrow WHICH documents it addresses? (v3.3.0 release review) ------
#
# `_db_reaches` above reads INSIDE a reach call's own argument list. A query can narrow its
# scope just as well OUTSIDE it, and fu-ask (2026-09-26) answered with a list of narrowing
# SHAPES: an identity function tested in a predicate, a `where` clause or a comparison.
# MEASURED by the v3.3.0 release reviewer: 11 of 12 rewrites walked past that list -- a `let`
# then a `where` on the variable, an arrow into contains(), tokenize(...)[3], a switch, a
# filter() callback, subsequence(), `[position() le 9]`, db:node-pre, `collection#1(...)` --
# each printing "NEGATIVE CONFIRMED over the whole database", rc 0. A blocklist of SHAPES
# cannot converge: every way of USING a value is a new shape.
#
# So the rule is about TOKENS, not shapes. The query (comments removed, string-literal
# contents blanked) is searched ANYWHERE for a small set of things that can select documents
# by WHERE THEY ARE rather than by WHAT THEY CONTAIN. If one is present the query still runs
# and every hit is reported; only a ZERO loses its certificate (rc 4, "not a finding"). The
# asymmetry is deliberate: a token present in an innocent query costs one uncertified zero,
# printed with its reason; a token missed costs a false negative quoted as proof.
#
# Selecting by CONTENT -- `collection('x4eff')[.//ware]//x` -- is NOT in the set: every
# document was read and judged by what it holds, so the zero is a negative over all of them.
#
# Each class, and why it is in:
#   identity  document-uri base-uri path node-pre node-id generate-id -- a value that names a
#             document (or a node's place in one) instead of its content. `path` covers both
#             db:path and fn:path; fn:path encodes sibling POSITIONS, which is the next class.
#   position  position last subsequence(-where) head tail foot trunk slice items-at/-before/
#             -after/-starting-where/-ending-where take-while remove index-of index-where
#             partition, plus filter for-each for-each-pair fold-left fold-right: BaseX 12
#             implements XQuery 4, whose callbacks receive the item's POSITION as a second
#             argument, so a callback can select by position without spelling position().
#             Also the FLWOR `at $i` and `count $c` clauses and tumbling/sliding `window`.
#   numeric   a predicate that is a number, a variable, arithmetic over those, or a numeric
#   predicate function with no comparison in it (`[1]`, `[$n]`, `[count(.//x)]`) -- a
#             positional predicate. Anywhere, not only directly on a collection() call: once
#             a sequence is bound to a variable or parenthesised, a text scan cannot tell
#             documents from elements. `//ware[1]` therefore also loses its certificate.
#   order     `<<` `>>` `is` -- node identity and document order.
#   modules   util: hof: array: map: (positional and collision-deduplicating helpers; a map
#             keeps ONE value per key), random: (selection by chance), and `array {` / a
#             `?1` lookup (positional access into an array built from the documents).
#   indirect  `#` (a function reference: `collection#1('x4eff/libraries')` reaches a path
#             `_db_reaches` never sees), `Q{` (an EQName spells any function under its
#             namespace URI, past every name above), and evaluation of query TEXT held in a
#             string -- xquery:, function-lookup, load-xquery-module, transform, eval.
#
# NOT scanned: the queries ask.py builds itself (`refs`, `attr`). `q_refs` returns
# document-uri(root($n)) to NAME each hit's file, it addresses every document, and scanning
# it would withdraw the certificate from the tool's most-used negative.
#
# Named residuals (a text scan's limits, not handled): a quote character in a direct element
# constructor's TEXT content (`<r>say 'hi</r>`) can desynchronise the string blanking; a
# user-defined function in a prolog is not followed -- but a prolog already makes the count
# wrapper fail to compile, and that zero is refused on its own (n_items is None).

# Historical blacklist retained only for explanatory diagnostics and regression
# coverage. Only content_query.certification_issue can authorize raw-query zeros.
_TOKEN_FNS = {
    "identity": ("document-uri", "base-uri", "path", "node-pre", "node-id", "generate-id"),
    "position": ("position", "last", "subsequence", "subsequence-where", "head", "tail",
                 "foot", "trunk", "slice", "items-at", "items-before", "items-after",
                 "items-starting-where", "items-ending-where", "take-while", "remove",
                 "index-of", "index-where", "partition", "filter", "for-each",
                 "for-each-pair", "fold-left", "fold-right"),
    "indirect": ("function-lookup", "load-xquery-module", "transform", "eval",
                 "random-number-generator"),
}
_NAME_START = r"(?<![\w.\-$])"          # not the tail of a longer name, not a $variable
_TOKEN_FN = re.compile(
    _NAME_START + r"(?:[A-Za-z_][\w.\-]*:)?(?:"
    + "|".join(re.escape(f) for f in sorted(
        {f for fs in _TOKEN_FNS.values() for f in fs}, key=len, reverse=True))
    + r")\s*[(#]")
_TOKEN_MODULE = re.compile(_NAME_START + r"(?:util|hof|array|map|xquery|random):"
                                         r"[A-Za-z_][\w.\-]*\s*[(#]")
_TOKEN_OTHER = re.compile(
    _NAME_START + r"(?:at|count)\s+\$"                         # FLWOR positional / count
    + r"|" + _NAME_START + r"(?:tumbling|sliding)\s+window(?![\w.\-])"
    + r"|<<|>>"                                                # document order
    + r"|(?<![\w.\-$@/:])is(?![\w.\-:])"                       # node identity
    + r"|" + _NAME_START + r"array\s*\{"                       # curly array constructor
    + r"|\?\s*[\d$(]"                                          # positional lookup
    + r"|[\w.\-:]*#"                                           # function reference / pragma
    + r"|Q\{")                                                 # EQName
_VAR = r"\$[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?"
_NUM = r"(?:\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+)"
_NUMERIC_PRED = re.compile(
    rf"[-+]?\s*(?:{_NUM}|{_VAR})(?:\s*(?:[-+*]|div|idiv|mod)\s*[-+]?\s*(?:{_NUM}|{_VAR}))*")
_NUMERIC_FN_PRED = re.compile(
    r"(?:[A-Za-z_][\w.\-]*:)?(?:number|round|round-half-to-even|floor|ceiling|abs|count|sum"
    r"|avg|min|max|string-length|integer|int|decimal|double|float|long|short|byte)\s*\(")
_BOOLEANISH = re.compile(r"=|<|>|(?<![\w.\-$])(?:eq|ne|lt|le|gt|ge|and|or)(?![\w.\-])")


def _xq_lex(text: str, *, blank_strings: bool) -> str:
    """One left-to-right pass over XQuery text, so a comment and a string literal are each
    recognised only where the OTHER is not open.

    Every comment (they nest) becomes one space. With *blank_strings*, a string literal keeps
    its quotes but its contents become 'x' -- EXCEPT inside `{...}`, which in a direct
    constructor's attribute value is an enclosed EXPRESSION (`<e a='{document-uri($d)}'/>`),
    i.e. code; `{{`/`}}` are literal braces. Keeping braces in an ordinary string only ever
    over-reports.

    A quote directly after a word character is not taken to open a string: in code a literal
    never touches a name, while in a constructor's text content (`<r>it's</r>`) the apostrophe
    would otherwise swallow the query up to the next quote. Declining to open one only ever
    leaves MORE text visible to the scan.

    Why one pass: stripping comments by regex first saw `'(:' = ':)'` -- two STRING literals --
    as a comment and deleted everything between them, a token included.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("(:", i):
            depth = 0
            while i < n:
                if text.startswith("(:", i):
                    depth += 1
                    i += 2
                elif text.startswith(":)", i):
                    depth -= 1
                    i += 2
                    if depth == 0:
                        break
                else:
                    i += 1
            out.append(" ")
            continue
        ch = text[i]
        if ch in "'\"" and not (i and (text[i - 1].isalnum() or text[i - 1] == "_")):
            j, depth = i + 1, 0
            body: list[str] = []
            while j < n:
                c = text[j]
                if c == ch:
                    if text.startswith(ch * 2, j):          # '' / "" is an escaped quote
                        body.append(ch * 2 if (depth or not blank_strings) else "xx")
                        j += 2
                        continue
                    break
                if c in "{}" and text.startswith(c * 2, j) and not depth:
                    body.append(c * 2 if not blank_strings else "xx")
                    j += 2
                    continue
                if c == "{":
                    depth += 1
                elif c == "}" and depth:
                    depth -= 1
                    body.append(c)
                    j += 1
                    continue
                if depth and c in "'\"":                    # a nested literal inside {...}
                    k = text.find(c, j + 1)
                    k = n - 1 if k < 0 else k
                    seg = text[j:k + 1]
                    body.append(seg[0] + "x" * (len(seg) - 2) + seg[-1]
                                if blank_strings and len(seg) >= 2 else seg)
                    j = k + 1
                    continue
                body.append(c if (depth or not blank_strings or c == "{") else "x")
                j += 1
            out.append(ch + "".join(body) + (ch if j < n else ""))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _predicates(text: str) -> list[str]:
    """The contents of every `[...]` in *text* (strings already blanked), innermost too."""
    found, stack = [], []
    for i, ch in enumerate(text):
        if ch == "[":
            stack.append(i)
        elif ch == "]" and stack:
            found.append(text[stack.pop() + 1:i])
    return found


def _scope_tokens(query: str) -> list[str]:
    """Every token in *query* that can narrow which documents it addresses, in order of
    appearance, deduplicated -- [] means a zero from it may be certified. See the block
    comment above for each class and why it is in."""
    scan = _xq_lex(query, blank_strings=True)
    hits: list[tuple[int, str]] = []
    for rx in (_TOKEN_FN, _TOKEN_MODULE, _TOKEN_OTHER):
        hits += [(m.start(), re.sub(r"\s+", " ", m.group(0).strip())) for m in rx.finditer(scan)]
    for body in _predicates(scan):
        b = body.strip()
        if _NUMERIC_PRED.fullmatch(b) or (_NUMERIC_FN_PRED.match(b) and not _BOOLEANISH.search(b)):
            hits.append((scan.find("[" + body + "]"), "[" + re.sub(r"\s+", " ", b)[:40] + "]"))
    seen, out = set(), []
    for _pos, label in sorted(hits):
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _git_bash_argv_state() -> tuple[str | None, bool]:
    """(MSYSTEM, conversion switched off) for THIS process; see `argv_under_git_bash` in main().

    These describe the shell that launched this process and whether it rewrote our arguments.
    They are not configuration, so `_paths` is the wrong door: a value in x4-paths.env cannot
    say whether THIS invocation's argv was rewritten.
    """
    # env-ok: reads the launching shell's own markers (MSYSTEM, MSYS_NO_PATHCONV,
    # MSYS2_ARG_CONV_EXCL) -- a fact about this process, not configuration.
    env = os.environ
    # PRESENCE, not truthiness, on both -- MEASURED 2026-09-20 from Git Bash. With
    # `MSYSTEM=` (defined, empty) the runtime STILL rewrote argv (`//ware` arrived as
    # `/ware`), so `or None` switched this guard off while the rewrite continued: F122
    # reopened. And MSYS treats MSYS_NO_PATHCONV as SET when merely defined, so an empty
    # value means conversion is OFF and a zero is a REAL negative -- bool() read that as
    # "on" and refused it. Twins for both directions are in test_ask.py.
    msystem = env["MSYSTEM"] if "MSYSTEM" in env else None
    off = "MSYS_NO_PATHCONV" in env or env.get("MSYS2_ARG_CONV_EXCL", "").strip() == "*"
    return msystem, off


#: What `refs` searches, stated wherever it answers (issue #5). Each form is EXPLICIT --
#: never a global regex and never a global @class, which also names AI classes and
#: component kinds (`class="player"`).
REFS_SCOPE = (
    "refs searches exactly: @ref, @name, @component = <id>; @macro = <id> or macro.<id>; "
    "@ware = <id> or ware.<id>; bullet/@class = <id>; add_blueprints/@wares and @macros "
    "as a [list, ...] of tokens, each <id> or its ware./macro. form. NOT searched: "
    "$variable expressions (never resolved) and other expression attributes "
    "(@exact, @value, ...), so a zero is over these forms only.")

#: Tokenise an add_blueprints list: `[ware.a, ware.b]`, `ware.a`, `macro.a,macro.b`.
_REFS_TOKENS = "tokenize(normalize-space(translate({attr}, '[],', '   ')), ' ')"


def q_refs(db: str, ident: str) -> str:
    lit = _xq_literal(ident)
    mlit = _xq_literal("macro." + ident)
    wlit = _xq_literal("ware." + ident)
    wares = _REFS_TOKENS.format(attr="@wares")
    macros = _REFS_TOKENS.format(attr="@macros")
    return f"""
for $n in collection('{db}')//*[@ref = {lit} or @name = {lit} or @component = {lit}
                              or @macro = ({lit}, {mlit}) or @ware = ({lit}, {wlit})
                              or (self::bullet and @class = {lit})
                              or (self::add_blueprints and ({wares} = ({lit}, {wlit})
                                                            or {macros} = ({lit}, {mlit})))]
let $u := substring-after(document-uri(root($n)), '/{db}/')
order by $u
return $u || '  <' || name($n) || '>'
"""


def q_attr(db: str, attr: str) -> str:
    return f"""
for $v in distinct-values(collection('{db}')//@{attr})
let $n := count(collection('{db}')//*[@{attr} = $v])
order by $n descending
return $v || ' : ' || $n
"""


def _lossless_streams() -> None:
    """A piped stdout on Windows is cp1252: printing a corpus value outside it raised
    UnicodeEncodeError after the query had succeeded (issue #7). backslashreplace keeps
    the encoding the reader expects and writes an exact, visible `\\u706b` instead."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _lossless_streams()
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["refs", "attr", "xq"])
    p.add_argument("arg", nargs="?",
                   help="the id (refs), the attribute name (attr) or the XQuery text (xq). "
                        + REFS_SCOPE.replace("%", "%%"))
    p.add_argument("--file", default=None,
                   help="xq only: read the query from this file. Git Bash rewrites path-like parts "
                        "of command-line arguments (`//` becomes `/`) before Python sees them; a "
                        "file is not rewritten")
    p.add_argument("--db", default=None, choices=["x4raw", "x4eff"],
                   help="default: x4raw, the files AS WRITTEN (who wrote this, in which mod). "
                        "x4eff is the effective merged tree the engine sees: use it for any "
                        "claim about what is live")
    p.add_argument('--limit', type=_nonnegative, default=0,
                   help='optional maximum whole result items to display; omitted/0 means unlimited. '
                        'The total and negative checks still use the full query')
    p.add_argument('--offset', type=_nonnegative, default=0,
                   help='optional number of result items to skip (default: 0); requires a safely countable query')
    args = p.parse_args(argv)
    # Resolved HERE, from an explicit None, so the output can say when the as-written
    # database was searched only because nobody chose one (cold E2E agent, 2026-09-14).
    db_defaulted = args.db is None
    if db_defaulted:
        args.db = "x4raw"

    if args.mode == "xq":
        if (args.arg is None) == (args.file is None):
            p.error("xq takes its query EITHER as an argument OR with --file -- exactly one")
    else:
        if args.file is not None:
            p.error(f"--file is for xq only; {args.mode} takes its value as an argument")
        if args.arg is None or not args.arg.strip():
            p.error(f"{args.mode} needs a non-empty argument")
    if args.mode == 'attr' and not is_qname(args.arg):
        print('error: attribute must be a lexical XML QName (name or prefix:name), '
              'without XPath syntax.', file=sys.stderr)
        return 2
    query_text = args.arg
    if args.file is not None:
        try:
            # utf-8-sig: Notepad and Windows PowerShell 5.1 write a BOM, and BaseX rejects
            # U+FEFF with a context error that names the wrong cause (review, 2026-09-14).
            query_text = Path(args.file).read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: cannot read the query file {args.file}: {exc}", file=sys.stderr)
            return 2
    # MEASURED 2026-09-14: from Git Bash, `ask.py xq 'count(collection("x4raw")//ware)'` reached
    # Python as `.../ware`, and `'//ware'` as `/ware` -- MSYS path conversion rewrites the
    # ARGUMENT before any of this runs, and nothing here can see what was typed. The zero
    # guard below then certified a negative over a query nobody wrote. Git Bash exports
    # MSYSTEM to its native children; a PowerShell started on its own does not (one started FROM
    # Git Bash inherits it and refuses too -- the safe direction, and --file still works).
    # refs/attr build their query here from an id or attribute name, so nothing crosses argv.
    # The conversion is wider than `//` -- a leading `/ware` arrives as `C:/Program Files/Git/ware`
    # -- and MSYS_NO_PATHCONV, or MSYS2_ARG_CONV_EXCL=*, switches it off, measured both ways
    # (review, 2026-09-14). The guard keys on the CHANNEL, never on a pattern in the query.
    if args.mode == "xq" and not _strip_xq_comments(query_text).strip():
        # An empty query returns an empty sequence, and the zero guard would certify it -- a
        # truncated or unsaved query file printed NEGATIVE CONFIRMED rc 0 (review, 2026-09-14).
        print("error: the xq query is empty (or only comments), so there is nothing to run; "
              "an empty result from it would not be a negative", file=sys.stderr)
        return 2
    msystem, conversion_off = _git_bash_argv_state()
    argv_under_git_bash = (args.mode == "xq" and args.file is None
                           and bool(msystem) and not conversion_off)

    xq = {"refs": q_refs, "attr": q_attr}.get(args.mode)
    query = xq(args.db, args.arg) if xq else query_text

    # `--db` chooses the coverage AND freshness denominator; the query text
    # chooses what is actually searched. If they disagree the result is scored
    # against the wrong world — which is exactly what this module's own
    # `load_coverage` docstring forbids ("they must never share one"). Found
    # end-to-end 2026-08-13: `xq "count(collection('x4eff')//macro)"` searched
    # x4eff, reported "in x4raw", and a STALE x4eff raised no warning because
    # x4raw happened to be fresh.
    # `collection(...)` is not the only spelling. BaseX addresses a database
    # directly with db:get('<name>') -- db:open before BaseX 10, and the vendored
    # jar is 12.4 -- and those walked past this guard entirely: a db:get('x4eff')
    # query ran against x4eff while being scored against x4raw's coverage AND
    # x4raw's freshness. That is the very failure this block exists to stop,
    # reached through a different spelling of the same intent.
    #
    # AUDIT-2026-09-24 BX-1 widened this from one regex to `_db_reaches`: a second
    # argument (a path), a db name held in a variable, and doc() all escaped it. Three
    # refusals, all rc 2 and all BEFORE the run, because each makes the per-database
    # coverage and freshness the wrong yardstick for the answer, positive or zero.
    #
    # Scope narrowed OUTSIDE a reach call's arguments is NOT refused here: it is read by
    # `_scope_tokens` and withholds only the zero's certificate, after the run (below).
    stripped_query = _strip_xq_comments(query)
    reaches = _db_reaches(stripped_query)
    unknown = [r["call"] for r in reaches if r["db"] is None]
    foreign = sorted({r["db"] for r in reaches if r["db"] and r["db"] != args.db})
    scoped = [r["call"] for r in reaches if r["db"] and r["scoped"]]
    if unknown or foreign or scoped:
        if unknown:
            print(f"error: the query names its database through an expression ask.py "
                  f"cannot read: {'; '.join(unknown)}", file=sys.stderr)
            print("       Which database was searched decides which coverage and "
                  "staleness apply, so it must be a string literal.", file=sys.stderr)
        if foreign:
            print(f"error: the query searches {', '.join(foreign)} but --db is "
                  f"'{args.db}'.", file=sys.stderr)
            print(f"       Coverage and staleness would be judged against "
                  f"'{args.db}', which is not what you queried.", file=sys.stderr)
        if scoped:
            print(f"error: the query addresses PART of a database: "
                  f"{'; '.join(scoped)}", file=sys.stderr)
            print("       The coverage denominator counts every document in the "
                  "database; a zero over one path is not a zero over those, and a "
                  "mistyped path matches nothing at all.", file=sys.stderr)
            print("       Search the whole database instead -- a negative over all of "
                  "it covers every path in it.", file=sys.stderr)
        # Advise only what argparse will accept: `--db x4eff/libraries` was once
        # printed here and following it was an argparse error (AUDIT-2026-09-24 BX-5).
        targets = [d for d in foreign if d in DBS] or ([args.db] if not foreign else [])
        bad = [d for d in foreign if d not in DBS]
        if bad:
            print(f"       ask.py holds coverage only for {' and '.join(DBS)}; it cannot "
                  f"score a query over {', '.join(bad)}.", file=sys.stderr)
        if len(targets) == 1 and len(set(foreign) - set(targets)) == 0:
            print(f"       Re-run over collection('{targets[0]}') with --db {targets[0]}.",
                  file=sys.stderr)
        return 2

    # Cheap preconditions first -- filesystem only, no JVM start. A missing jar or
    # an unbuilt database is knowable before spending anything, and BaseX's own
    # words for both name neither the cause nor the cure: an unbuilt DB reports
    # "[FODC0002] Resource '<abs path>/x4raw' not found" and never mentions
    # build-corpus.sh, because the one line that does sits on the ZERO-RESULT
    # path an unbuilt DB can never reach.
    problems = preflight.check(["jar", "db"], db=args.db)
    if problems:
        print(preflight.render(problems), file=sys.stderr)
        return 2

    page = None
    try:
        if args.limit or args.offset:
            page = run_page(query, args.limit, args.offset)
            out, n_items = page.output, page.total
        else:
            out, n_items = run_counted(query)
    except (RuntimeError, FileNotFoundError) as exc:
        # Before quoting BaseX's own error, ask whether the ENVIRONMENT explains
        # it. MEASURED 2026-08-24 with java off PATH: this printed "error: BaseX
        # query failed: [WinError 2] The system cannot find the file specified"
        # -- blaming BaseX for a missing JVM, and not even naming the file. The
        # full check (which does start a JVM to read its version) runs only here,
        # on the path that has already failed.
        problems = preflight.check(["java", "jar", "db"], db=args.db)
        if problems:
            print(preflight.render(problems), file=sys.stderr)
            return 2
        print(f"error: BaseX query failed: {exc}", file=sys.stderr)
        # The CHANGELOG promises every argument-query result shows what ARRIVED, and this
        # path owed it too: a leading `/` rewritten to `C:/Program Files/Git/...` is the
        # MSYS outcome most likely to ERROR rather than return a zero, and the error then
        # quotes the rewritten text without saying it was rewritten.
        if argv_under_git_bash:
            print(f"       query as received (Git Bash, MSYSTEM={msystem!r}): {query}",
                  file=sys.stderr)
            print("       Git Bash rewrites path-like parts of arguments; if that is not "
                  "what you typed, re-run with --file.", file=sys.stderr)
        # BaseX's own verdict stays above as the evidence; these lines add what it does
        # not say. MEASURED 2026-09-19: `xq --file` with `count(//ware)` reports
        # "[XPDY0002] .: Context value is undefined" -- true, and naming neither the
        # cause (an xq query addresses its own database; there is no implicit context
        # node) nor the cure. It is the same translation `preflight` already does for an
        # unbuilt DB's "[FODC0002] Resource not found", which never mentions
        # build-corpus.sh. Found by a cold, docs-only agent that reached for `//ware`
        # because CLAUDE.md sends you here without saying a query names its collection,
        # and the game-root file has 37 characters of budget left.
        #
        # Keyed to BaseX'S VERDICT, never to the query text. An earlier attempt read the
        # query for a `collection(` literal and pre-empted the run: it refused `1+1` --
        # legal, and needing no database -- plus every placeholder query in this module's
        # own tests, 22 red at once. Only the engine knows whether a context was actually
        # needed. Twins for both directions are in test_ask.py.
        if "XPDY0002" in str(exc) or "Context value is undefined" in str(exc):
            print("       That means the query names no database, so it had nothing "
                  "to search.", file=sys.stderr)
            print(f"       An xq query addresses its own: "
                  f"collection('{args.db}')//ware, not //ware.", file=sys.stderr)
            print(f"       --db {args.db} sets the coverage denominator, not the input.",
                  file=sys.stderr)
        return 2

    lines = [ln for ln in out.splitlines() if ln.strip()]
    cov = load_coverage(args.db)
    indexed = cov.get("indexed", {}).get("total")
    expected = cov.get("expected", {}).get("total")
    status = cov.get("status", "unknown")

    # `n_items is None` = the wrap would not compile, so we are back to counting
    # LINES and must say so rather than print a number that looks authoritative.
    hits = len(lines) if n_items is None else n_items
    unit = "output line(s), item count unavailable" if n_items is None else "item(s)"

    # Printed on EVERY run until the index is rebuilt — including alongside a
    # POSITIVE result, which is still an answer about a superseded world.
    stale = staleness_verdict(args.db)
    if not stale.fresh:
        print(stale.banner())

    # A VALUE COMPUTED FROM NOTHING IS NOT A HIT (AUDIT-2026-09-24 BX-5). `false` from
    # exists(...) and "" from string(...) over an empty sequence are one item each, and
    # were printed as "1 item(s)", rc 0 -- a positive answer to a question whose answer
    # was "none". Same family as the count()-of-nothing refusal below: one atomic value
    # that says "nothing", and the empty-sequence guard never ran. Items that all
    # serialize to nothing (n_items > 0, no output line) cannot be told apart from
    # such values either, so they are not a hit -- and not a negative.
    if (page.meaning == 'empty-serialization' if page else n_items and not lines):
        print(f"{n_items} item(s) in {args.db}, every one of them serializing to an "
              f"empty string.")
        print("\n  ** NOT A FINDING EITHER WAY. ** An empty string is a VALUE, not a")
        print("  match: string(...) of nothing returns one. It is not an empty")
        print("  sequence either, so the denominator guard did not run. Re-run returning")
        print("  the nodes themselves for an answer with coverage behind it.")
        return 4
    if (page.meaning == 'false' if page else n_items == 1 and [ln.strip() for ln in lines] == ["false"]):
        print("false")
        print(f"\n1 item(s) in {args.db}.")
        print("\n  ** NOT A NEGATIVE FINDING. ** That is one boolean, not one match:")
        print("  exists(...)/boolean(...) return false when they found nothing, and the")
        print("  denominator guard applies to an EMPTY SEQUENCE, so it did not run.")
        print("  Re-run returning the nodes themselves -- drop the exists(...) wrapper --")
        print("  for a claim with coverage behind it.")
        return 4

    if hits:
        print("\n".join(lines))
        print(f"\n{hits} {unit} in {args.db}.")
        if page:
            print(f'  {page.shown} displayed of {page.total} total item(s) '
                  f'(offset {args.offset}; limit {args.limit or "unlimited"}).')
        # What the number counts. A docs-only agent read "3541 item(s) in x4eff." as wares
        # or files; it is XQuery items -- for `refs`, one matching element per line.
        if n_items is not None:
            print("  (an item is one node or value the query returned -- not a count of "
                  "files or entities)")
        # Not for `xq`: that query names its own collection, and the foreign-collection
        # guard above refuses a --db that disagrees, so "add --db x4eff" would be advice
        # that fails if followed (review, 2026-09-14).
        if argv_under_git_bash:
            print(f"  query as received (Git Bash, MSYSTEM={msystem}): {query}")
            print("  Git Bash rewrites path-like parts of arguments (`//` becomes `/`); if that is "
                  "not what you typed, "
                  "re-run with --file.")
        if args.mode == "refs":
            print(f"  {REFS_SCOPE}")
        if db_defaulted and args.mode in ("refs", "attr"):
            print("  searched x4raw, the default: the files AS WRITTEN. For what the game "
                  "actually loads, add --db x4eff.")
        # A count()-shaped query returns ONE item — the number — even when it
        # counted nothing. Before 2026-08-01 that printed "1 hit(s)" and skipped
        # the guard below entirely, which is the exact false positive this whole
        # tool exists to prevent, reached through its most natural phrasing.
        if (page.meaning == 'zero' if page else n_items == 1 and lines and _looks_like_zero_count(lines)):
            print("\n  ** NOT A NEGATIVE FINDING. ** That is one atomic value, not one")
            print("  match: a count()-shaped query returns a number even when it counted")
            print("  nothing. The denominator guard applies to an EMPTY SEQUENCE, so it")
            print("  did not run here. Re-run returning the nodes themselves — drop the")
            print("  count(...) wrapper — for a claim with coverage behind it.")
            return 4
        return 0

    # --- the zero-result path: this is where a denominator is mandatory -------
    print(f"0 items in {args.db}.")
    if args.mode == "refs":
        # The coverage denominator counts DOCUMENTS; it says nothing about which
        # reference FORMS were searched in them (issue #5), so the zero names them.
        print(f"  {REFS_SCOPE}")

    # Prior to every other refusal: they all judge the RESULT of a query, and this one says
    # the query that ran may not be the query that was typed (see argv_under_git_bash).
    if argv_under_git_bash:
        print("\n  ** NOT A NEGATIVE FINDING. ** This query arrived as a command-line argument")
        print(f"  under Git Bash (MSYSTEM={msystem}), which rewrites path-like parts")
        print("  of it (`//` becomes `/`, a leading `/x` becomes a Windows path)")
        print("  before Python sees it, so the query that ran may not be the one you typed:")
        print(f"    as received: {query}")
        print("  Put the query in a file and re-run with --file <path>; a file is not rewritten.")
        return 4

    # AN EMPTY SERIALIZATION IS NOT AN EMPTY SEQUENCE. `run_counted` returns
    # (output, None) when its count wrapper will not compile -- the documented
    # case being a query carrying its own prolog, so any query needing a
    # namespace declaration or a user-defined function. `hits` then falls back to
    # the LINE count, and three genuine matches that each serialize to a
    # zero-length string produce zero lines.
    #
    # MEASURED against real BaseX: that query printed
    #     NEGATIVE CONFIRMED over 2 of 2 documents (complete).   rc 0
    # while the identical query WITHOUT the prolog reported `3 item(s)`.
    #
    # The positive branch already says `item count unavailable` (that is what
    # `unit` is for); the zero branch computed `unit` and never used it, then
    # issued the guarantee. run_counted's own docstring required otherwise:
    # "The caller must then say the count is unavailable rather than quote the
    # line count as though it were meaningful."
    #
    # First of the refusals on this path, because it is prior to all of them: if
    # the count is unknown we do not know the result is empty at all, and a
    # denominator cannot help with a numerator nobody measured.
    if n_items is None:
        print("\n  ** NOT A NEGATIVE FINDING. ** 0 output lines, but the ITEM")
        print("  COUNT could not be obtained -- the count wrapper would not compile,")
        print("  which happens when the query carries its own prolog. An empty")
        print("  serialization is not an empty sequence: matches that serialize to")
        print("  zero-length strings produce no lines and are still matches.")
        print("  Re-run as count(...), or without the prolog, for a claim with a")
        print("  numerator behind it as well as a denominator.")
        return 4
    if not cov:
        print("\n  ** NOT A NEGATIVE FINDING. ** No coverage report for this database")
        print("  (run build-corpus.sh / build-effective.sh). Without a denominator this")
        print("  means only 'not found in whatever happens to be indexed'.")
        return 4
    if not cov.get("supports_negative_claim"):
        print(f"\n  ** NOT A NEGATIVE FINDING. ** Coverage status: {status}.")
        print(f"  {indexed} of {expected} documents indexed, and the shortfall is")
        print("  UNEXPLAINED — something is wrong with the build, so 'zero hits' here")
        print("  cannot be distinguished from 'we never looked'.")
        # An indexed count that matches the expected one says nothing is missing; an
        # unreadable CATALOG is missing from BOTH sides, so name it (v3.3.0 review).
        catalogs = cov.get("unreadable_catalogs") or []
        if catalogs:
            print(f"  {len(catalogs)} overlay catalog(s) could not be read; their packed members")
            print("  were never enumerated, so they are absent from both counts above:")
            for c in catalogs[:15]:
                print(f"    - {c}")
        return 4

    # Coverage is satisfied — the build indexed what it claimed. Currency is a
    # SEPARATE question, checked last so the coverage diagnostics above still
    # speak for themselves: a denominator taken from a world that has since
    # changed is not a denominator for the question being asked now.
    if not stale.fresh:
        print("\n  ** NOT A NEGATIVE FINDING. ** Coverage is complete, but the index is")
        print("  STALE (see the banner above), so 'zero hits' describes the world as of")
        print("  the build, not the world now. Rebuild before making this claim.")
        return 4

    # A DENOMINATOR OF ZERO IS NOT A DENOMINATOR, and neither is an absent one.
    # The three guards above ask whether a coverage report EXISTS, whether it
    # CLAIMS to support a negative, and whether it is FRESH. None of them asks
    # whether it counted anything -- so a report answering yes to all three
    # printed the bare zero this tool exists to refuse, wearing the one sentence
    # that means it was checked:
    #
    #     NEGATIVE CONFIRMED over 0 of 0 documents (complete).      rc 0
    #     NEGATIVE CONFIRMED over None of None documents (complete). rc 0
    #
    # Both are reachable. None arrives whenever the key is absent, because
    # `cov.get('indexed', {}).get('total')` yields it and nothing re-checks.
    # Zero arrives from coverage.py, which validates its roots for NON-EMPTINESS
    # and never for EXISTENCE: a typo'd --reference makes count_disk_xml return 0
    # and publishes status 'complete' over nothing at all.
    #
    # `(expected or 0) - (indexed or 0)` below is what hid it: it turns None into
    # 0, so `missing` is falsey and the sentence ends '(complete).'
    if not isinstance(expected, int) or not isinstance(indexed, int) \
            or expected <= 0 or indexed <= 0:
        print("\n  ** NOT A NEGATIVE FINDING. ** The coverage report claims to")
        print(f"  support a negative claim, but its denominator is {indexed} of")
        print(f"  {expected} -- nothing was indexed, so 'zero hits' here cannot be")
        print("  distinguished from 'we never looked', which is the one distinction")
        print("  this tool exists to make.")
        print("  Rebuild the index (build-corpus.sh / build-effective.sh), and check")
        print("  that coverage.py's --reference and --extensions name directories")
        print("  that actually exist.")
        return 4

    # A QUERY THAT NAMES NO DATABASE SEARCHED NONE (AUDIT-2026-09-24 BX-1). `()` or
    # `let $x := () return $x` returns an empty sequence without reading one document,
    # and the denominator below describes a database it never touched. refs/attr always
    # name --db; an xq query must name it itself (`collection('<db>')`). Checked LAST so
    # every refusal above still speaks for its own reason.
    if not reaches:
        print("\n  ** NOT A NEGATIVE FINDING. ** The query names no database -- no")
        print(f"  collection('{args.db}'), no db:get('{args.db}') -- so its empty result")
        print(f"  is not a search of {args.db}, and {indexed} of {expected} documents is not")
        print("  its denominator. Address the database in the query and re-run.")
        return 4

    # A QUERY THAT CAN NARROW WHICH DOCUMENTS IT ADDRESSES is not scored against all of them
    # (v3.3.0 release review; see `_scope_tokens`). Only `xq`: refs/attr are ask.py's own
    # queries, which address every document. Checked after "names no database", whose reason
    # is the more basic one.
    scope_tokens = _scope_tokens(stripped_query) if args.mode == "xq" else []
    issue = certification_issue(query, args.db) if args.mode == 'xq' else None
    if issue:
        print('\n  ** NOT A NEGATIVE FINDING. ** The query is outside the restricted')
        print('  whole-database content-search grammar; unsupported expressions can narrow')
        print('  the documents searched. This is a non-answer, not confirmed absence.')
        print(f'  Reason: {issue}')
        if scope_tokens:
            print(f"  Diagnostic tokens: {', '.join(scope_tokens)}")
        print('  Use refs/attr, or a literal whole-database path with content predicates.')
        return 4

    missing = (expected or 0) - (indexed or 0)
    # THE EXCLUSIONS ARE A DIFFERENT AXIS FROM THE DOCUMENT COUNT, and reading only
    # the count is how a negative over an INCOMPLETE tree printed as complete. A
    # malformed overlay does not reduce `documents_total` -- that is written+copied,
    # and the document still exists, merely without that overlay applied -- so
    # `missing` is 0, the unparseable list never prints, and the sentence ends
    # "(complete)" while coverage.json carries a non-zero
    # negative_claim_excludes.unparseable_overlays.
    #
    # coverage.py publishes that field precisely so a caller can "RENDER the caveat
    # instead of reading a bare boolean" -- and no shipped caller asked. A disclosure
    # with no reader is decoration.
    excl = cov.get("negative_claim_excludes") or {}
    n_unparseable = excl.get("unparseable_overlays") or 0
    n_unbuilt = excl.get("vpaths_without_effective_tree") or 0
    print(f"\n  NEGATIVE CONFIRMED over {indexed} of {expected} documents"
          + (f" ({missing} excluded)." if missing
             else ("" if (n_unparseable or n_unbuilt) else " (complete).")))
    if n_unparseable or n_unbuilt:
        print("  ** NOT COMPLETE. ** This tree EXCLUDES:")
        if n_unparseable:
            print(f"      {n_unparseable} overlay(s) that would not parse")
        if n_unbuilt:
            print(f"      {n_unbuilt} vpath(s) with no effective tree")
        print("  so a zero here is a negative over the tree MINUS those, which is a")
        print("  narrower claim than the sentence above reads as. coverage.json ->")
        print("  negative_claim_excludes carries the counts; -> unparseable names them.")
    if missing:
        print("  The exclusions are malformed XML the ENGINE cannot read either, so they")
        print("  hold no live content. Named in coverage.json -> unparseable:")
        for u in cov.get("unparseable", [])[:15]:
            print(f"    - {u}")
    if args.db == "x4raw":
        print("\n  NOTE: x4raw is files AS WRITTEN. For a claim about what is LIVE, re-run")
        print("  with --db x4eff (diffs applied, load order resolved).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
