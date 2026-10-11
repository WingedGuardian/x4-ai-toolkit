# BaseX / XQuery over the X4 corpus

Discovery tool over the X4 corpus. Ships with the toolkit as of v2.6.0; it needs a
JVM (Java 17+) and a one-off index build. See [README.md](README.md) for install,
the freshness contract, and the exit codes.

```bash
bash build-corpus.sh      # x4raw  — every file AS WRITTEN      (~4 min)
bash build-effective.sh   # x4eff  — X4's EFFECTIVE merged tree (~1.5 min)
cd ../x4validate && uv run python ../basex/ask.py refs <id> [--db x4eff]
```

## Two databases, two questions — do not mix them

| DB | contents | answers |
|---|---|---|
| **`x4raw`** | every file as written, per mod | *who **wrote** this, in which mod* |
| **`x4eff`** | `_merge.build_effective` per vpath | *what does the **engine** see* |

Document counts are a fact about one install at one build, so none is quoted here: every
zero-result `ask.py` prints the current denominator, and `coverage-<db>.json`
(`indexed.total` / `expected.total`) holds it between runs.

`ask.py` searches **`x4raw` unless you pass `--db`**, and for `refs` and `attr` says so under the
result (an `xq` query names its own collection). **From Git Bash, give an `xq` query with
`--file`:** MSYS rewrites path-like parts of command-line arguments (`//` becomes `/`) before
Python sees them, so
`ask.py` refuses to certify a zero from an argument there and shows the query as received. What it
counts are XQuery items -- for `refs`, one matching element per line -- not files or entities.

`x4raw` will happily tell you vanilla sets a value that no longer survives the
modlist. For a claim about what is **live**, use `x4eff` — it applies diffs in
load order and resolves conflict winners. Demonstrated once on one modlist: `hullparts`'
price read as the vanilla value in `x4raw` and as a mod's override in `x4eff`. The two values
depend on the modlist, so re-derive them (`collection('<db>')//ware[@id='hullparts']/price`
against each database, with the matching `--db`) rather than quote them.

**Advisory limit:** inter-mod load order is the engine's MEASURED signature-check
order (case-insensitive folder order in repeated dependency passes; see
`tools/x4validate/docs/BLIND-SPOTS.md` F128), not documented by Egosoft; that it is
also the patch-apply order was MEASURED by the in-game load-order probe (2026-09-26).
The profile's `extensions\` root was MEASURED too (it applies after the game root, and a
dependency does not cross between the two; F141); the Steam Workshop root is unobserved.

## A negative claim needs a denominator (this is the point)

A discovery tool that cannot prove a negative is just a faster way to guess.
Three gaps stood in the way; all three are closed:

| Gap | Was | Now |
|---|---|---|
| **1. Packed content** | 62% of mod XML lives in `.cat`/`.dat` and was invisible (vro alone = 1,613 files) | `stage.py` extracts via `_cat` into transient staging, indexed, then discarded |
| **2. Files as written** | no diff application, load order, or conflict winner | second DB `x4eff` from `_merge.build_effective` |
| **3. Silent drops** | `SET SKIPCORRUPT true` is required, but drops files **silently** | `coverage.py` reconciles indexed vs disk and **names every exclusion** |

**Never quote a bare "0 hits".** `ask.py` refuses to render a zero-result as a
negative finding unless `coverage-<db>.json` says coverage is complete or fully
accounted, and prints the denominator when it does:

```
0 items in x4eff.
  NEGATIVE CONFIRMED over N of N documents (complete).
```
(`N` is the current `indexed.total`; the shape is illustrative, the number is whatever your
build holds.) The denominator describes the WHOLE database, so it is only printed for a query
that searched all of it: a query addressing a document or a path (`doc(...)`,
`db:get('<db>', '<path>')`, `collection('<db>/<path>')`), naming its database through a
variable, or naming a database other than `--db` is refused with rc 2, and a zero from a query
that names no database at all is refused with rc 4. A negative over the whole database covers
every path in it, so search the whole thing.

### Restricted grammar for raw-query zero certification

The old token blacklist missed conditional numeric predicates. Certification now
requires parsing the entire query as this deliberately small subset:

- One literal `collection('x4eff')`, `fn:collection('x4eff')`, `db:get('x4eff')`,
  or `db:open('x4eff')` root matching `--db`; root parentheses are allowed.
- `/` and `//` child/descendant paths, lexical QName or `*` steps, `@` attribute
  steps, and `.`. No axes, parent steps, or absolute paths inside predicates.
- Predicates consisting of relative node paths, paths compared to string/numeric
  literals (`= != < <= > >= eq ne lt le gt ge`), boolean `and`/`or`/parentheses,
  `exists(path)`, `empty(path)`, or `not(boolean-expression)`, optionally `fn:`.
- Quoted strings with doubled-quote escapes and nested XQuery comments.

For example, `collection('x4eff')[.//ware]//x` and
`collection('x4eff')//ware[@id='x']` qualify. Variables, FLWOR, constructors,
prologs, numeric predicates (`[1]`, `[if (...) then 1 else 2]`), positional or
identity functions, dynamic evaluation, and other unrecognized syntax do not.
Even an innocent FLWOR zero is now **rc 4**. Unrecognized queries still execute;
positive results stand. Diagnostics explain the restriction; a token scan never
authorizes a certificate. `refs`/`attr` use internal whole-database searches;
`attr` validates its input as a lexical QName before generating query syntax.

### Optional paging

Add `--limit N` and/or `--offset N` to any mode. Defaults are unlimited output
(`--limit 0`) and zero offset. Counts and semantic/negative guards apply to the
full sequence before selecting whole result items for display, so multiline XML
is never cut into partial lines and an empty page is not mistaken for absence.
Unsupported requested paging returns rc 2 with instructions to omit the flags;
unlimited raw prolog queries retain their existing count-unavailable fallback.
Paging limits displayed output, not the cost of evaluating the full query.

### Gap 4 — a fourth was found on 2026-08-01, and it was in the guard itself

The count printed as "hits" was `len(output_lines)`, not the number of matches.
Measured: 847 occurrences across 4 documents printed as **"32 hit(s)"**, because
BaseX wrapped the serialized sequence over 32 lines.

The serious part was not the miscount. The zero-result guard keyed off that same
line list being empty, and a `count(...)` query returning zero emits the single
line `0` — so **the guard never ran, and a zero result rendered as "1 hit(s)"
with exit 0.** `count()` is the most natural way to ask "how many", and it
bypassed the entire mechanism this tool exists to provide.

Now the query is wrapped (`let $__ask := ( … ) return (count($__ask), SEP, $__ask)`)
so the count is the **result-sequence item count**, and the guard keys off that.
A count-shaped query that counted nothing is refused explicitly:

```
$ echo 'count(collection("x4raw")//ware[@id="nope"])' > q.xq
$ ask.py xq --file q.xq
0
1 item(s) in x4raw.
  (an item is one node or value the query returned -- not a count of files or entities)
  ** NOT A NEGATIVE FINDING. ** That is one atomic value, not one match …
  Re-run returning the nodes themselves — drop the count(...) wrapper.
```
exit **4**. If the wrapper will not compile (a query with its own prolog), the
output says *"item count unavailable"* rather than quoting the line count as
though it meant something.

**Practical rule: ask for the nodes, not for `count()`.** Only the node form can
carry a coverage-backed negative. `test_ask.py` pins both directions and needs
neither BaseX nor a JVM.

The same goes for other values computed from nothing: `exists(...)` returning `false` and
`string(...)` of an empty sequence returning `""` are refused with rc 4, never counted as a hit.

**Coverage figures are not quoted here** -- every run prints the current ones, and
`coverage-<db>.json` holds them. To read them: `status`, `indexed.total` of `expected.total`,
and for x4raw the named exclusions under `unparseable` (malformed XML the *engine* cannot read
either); for x4eff `negative_claim_excludes` (vpaths with no effective tree, malformed
overlays), enumerated in `effective-manifest.json`. x4raw is judged **per root** (`base`,
`mods`): each root's shortfall must equal its own malformed files, so a surplus in one cannot
offset a gap in another. ⚠ Figures recorded before the 2026-09-24 audit (BX-2) counted both
mini-DLC twice in x4raw -- once from `reference\` and once staged from their archives -- so
an older x4raw total is inflated by those duplicates.

### Why the deficit explainer matters — it caught a real bug

The first reconciled build reported an **unexplained** deficit of 30 against 12
known-malformed files. Cause: `_cat.mod_vfs(xml_only=True)` also admits `.xsd`,
but BaseX builds with `CREATEFILTER *.xml` — so 18 schemas were staged, never
indexed, and read as missing. Without the reconciler that would have silently
poisoned every negative claim forever. **The index looking complete is not
evidence that it is.**

## Queries that earned their keep

### Who references this macro? (packed content included since 2026-07-27)
```bash
uv run python ../basex/ask.py refs turret_xen_m_beam_02_mk1_macro
```
**What `refs` searches -- exactly these forms, and its zero is over these only** (issue #5):

| form | matches |
|---|---|
| `@ref`, `@name`, `@component` | `<id>` |
| `@macro` | `<id>` or `macro.<id>` |
| `@ware` | `<id>` or `ware.<id>` |
| `bullet/@class` (projectile macros; never a global `@class`, which also holds `player`-style classes) | `<id>` |
| `add_blueprints/@wares`, `add_blueprints/@macros` | any token of `[a, b]` / `a,b` / `a` that is `<id>` or its `ware.`/`macro.` form |

**Not searched:** `$variable` expressions (never resolved -- `wares="$BlueprintWare"` names no
ware a query can read) and other expression attributes such as `@exact` and `@value`. For those,
write an `xq` query; its zero is not certified unless it fits the restricted grammar above.
When first run (2026-07-27) this returned hits overwhelmingly inside PACKED mods -- every
one invisible before staging -- where the KB had said only "six other mods reference it".
The count depends on the installed modlist, so it is not quoted; run it for today's.

### Every value in use for an attribute, with counts
```bash
uv run python ../basex/ask.py attr roomtype
```
Also surfaces *unresolved variables* (`$key.$roomType`) beside literals — the
dynamic call sites a literal grep silently misses.

### Where is a variable DEFINED? (the `$HQ` class)
```xquery
for $n in collection('x4raw')//set_value[@name='$HQ']
return concat(document-uri(root($n)), '  ::  ', $n/../../@name, '/', $n/../@name)
```
The question both rejected `_exprlint` regex rules failed at (718 vanilla false
positives); here the cue context comes free from the tree structure.

### Cross-file JOIN: dangling references
```xquery
let $defined := distinct-values(collection('x4eff')//macro/@name)
for $r in distinct-values(collection('x4eff')//ware/component/@ref)
where not($r = $defined)
return $r
```
**Always sanity-check an empty result by counting both sides** — an empty join
and a broken query look identical.

## Gotchas

- **Invoke as `java -cp BaseX.jar org.basex.BaseX`.** The bundled `bin/basex`
  wrapper fails here with `ClassNotFoundException`.
- **Java is a native Windows process** and does not understand Git Bash's
  `/c/...` paths. Passing one made BaseX look for `C:/c/Users/...` — and it
  still **exited 0**. The build scripts convert only the paths they CREATE (the
  staging tree, the serialized effective tree) with `cygpath -m`, and fall back to
  the path unchanged where `cygpath` is absent; `$X4_REFERENCE` and `$X4_EXTENSIONS`
  reach BaseX exactly as resolved, so set them in Windows form (`C:/...`). What
  catches a wrong form is the grep of BaseX's output for `not found`, because
  BaseX's exit code is not a usable gate.
- **Staging and the serialized effective tree are transient** by design; the
  ~2.8 GB index is the durable artifact. `KEEP_STAGE=1` / `KEEP_EFF=1` to keep
  them while debugging. The *manifests* always persist — a coverage report you
  can only run mid-build is one nobody runs.

- **An `xq` query names its own database.** `count(//ware)` has no context to walk and BaseX
  answers `[XPDY0002] .: Context value is undefined`; `--db` sets the coverage and freshness
  denominator, not the input. Write `count(collection('x4eff')//ware)` and keep the two the
  same -- naming a DIFFERENT database than `--db` is refused, because the answer would be
  scored against the wrong world. `ask.py` says all of this when the error appears.

## What this still does NOT replace

`x4validate` remains the authority for *correctness against the engine* — it has
the oracle (agreement with the engine's own `debug.txt`: 234/234 ops, 0 false
OK). BaseX is for *discovery and structural questions across many files*. The
CLAUDE.md **Discovery vs. Proof** rule now has a third state: a BaseX negative
**with a stated denominator** is admissible; a bare one is still just a lead.
