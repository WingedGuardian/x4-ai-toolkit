# BaseX — XQuery over the X4 corpus

Generated BaseX databases and staging/merged trees are disposable caches. Rebuild
with the supplied scripts: do not copy, back up or recycle the previous database.
The scripts build into `<db>__next` and swap it in only once it is verified
(`dbswap.py`), so a failed, killed or refused build leaves the previous database
and its coverage report in place: STALE, never MISSING. The previous generation is
then dropped with native `DROP DB`; staging trees get checked permanent workspace
disposal after verifying current inputs. New workspaces get creation-time disposition receipts;
unknown files, links, invalid receipts and disposal failures stop the build.
Manifests and coverage still gate freshness and negative claims. Keep logs and
hand-written evidence outside disposable cache directories.

A **discovery** instrument. It answers *"where does this appear, what values exist,
who mentions X?"* across every XML file the game and your mods ship — base game,
all DLC, loose mod files and **packed** ones — in about a second.

It is not the authority on correctness. `x4validate` is. See
[Discovery vs. proof](#discovery-vs-proof) before quoting anything from here.

> **Numbers below are MEASURED on one real install as of 2026-08-24** and are there
> to set expectations, not as guarantees. Yours will differ with your modlist.

---

## Prerequisites

| | why |
|---|---|
| **Java 21 or newer** | BaseX 13.0's classes are bytecode **major 65**. An older JVM does not run slowly — it refuses to load them (`UnsupportedClassVersionError`). Read out of the shipped jar: `Build-Jdk-Spec: 21`. (Toolkits up to 4.1.0 shipped BaseX 12.4, which needed Java 17.) |
| **`uv`** (with Python 3.13) | the build scripts drive `x4validate` through `uv run python` |
| **~3 GB free disk, plus room for a second copy** | the index is the durable artifact — measured **942 MB** (`x4raw`) + **949 MB** (`x4eff`) — plus a transient staging tree during the build. A rebuild holds the old and the new database at once until the swap; `dbswap.py prepare` refuses up front (nothing dropped) if that would leave less than min(pagefile, 50 GiB) free on the system drive, or min(5%, 50 GiB) on another drive |
| **a configured toolkit** | `$X4_REFERENCE` and `$X4_EXTENSIONS`, or `x4-paths.env` (toolkit root). The scripts **refuse** rather than guess: a corpus built over a path that does not exist indexes zero documents and would otherwise still exit 0. |

`preflight.py` checks all of these and refuses with **exit 2** before any long work
starts. You should never see a raw `[WinError 2]` or `Could not find or load main
class`; if you do, that is a bug worth reporting.

## Install

Put `BaseX.jar` in `basex/`. That is the whole installation — **`lib/` is not
needed** (verified: a real `collection()` query runs from a directory holding
only the jar). The toolkit ships BaseX **13.0**'s jar there. If it is missing,
download <https://files.basex.org/releases/13.0/BaseX130.jar> (listed at
<https://basex.org/download/>) and save it as `basex/BaseX.jar`. The jar the
toolkit ships has SHA-256
`022bc5a4ec057591d6c577b3e0c1ef6f0c71373f68ddb5cad3d3b2afb2c83a8d`; basex.org
publishes no checksum, so that is the only one to compare against.

`preflight.py` reads the jar's manifest and accepts **exactly** 13.0. Any other
version — including the 12.4 jar of toolkits up to 4.1.0 — is refused with exit 2
and the download link, because every BaseX behaviour the scripts rely on was
measured on 13.0. Databases built by 12.4 open in 13.0 unchanged (measured), so
replacing the jar needs no rebuild.

Every caller invokes it as:

```bash
java -cp BaseX.jar org.basex.BaseX -q "<xquery>"
```

**not** `java -jar` and not `bin/basex` — the jar's `Main-Class` is `BaseXGUI`,
so `java -jar` opens a GUI.

## Build

```bash
bash build-corpus.sh      # x4raw — every file AS WRITTEN
bash build-effective.sh   # x4eff — X4's EFFECTIVE merged tree
```

Both are long jobs. Run them in the background rather than raising a timeout.

**Build-then-swap.** Each build writes `x4raw__next` / `x4eff__next` beside the live
database, reconciles and stamps `coverage-<db>__next.json`, and only then runs
`dbswap.py swap`: it checks the new database holds exactly the documents its report
counts, renames the live one to `<db>__old`, renames the new one into place, writes
`coverage-<db>.json` and drops the old one. Until that moment `ask.py` keeps
answering from the previous database (STALE if the world moved), never "not found".

- **A reader holds the database** (an `ask.py` query, a BaseX GUI): BaseX refuses the
  rename, nothing is swapped, the build exits `7`. Retry the swap alone when the
  reader finishes: `cd ../x4validate && uv run python ../basex/dbswap.py swap --db x4raw`.
- **A build or swap was interrupted**: the next build recovers it first (forward if
  the new database was already verified, otherwise back to the previous one), or run
  `dbswap.py recover --db <db>`. `dbswap.py status --db <db>` and `x4fresh status`
  describe any leftover generation.
- **One build per database at a time.** A build holds `basex/build-<db>.lock` (with
  its process id) from before `prepare` until after the swap; a second build of the
  same database, or a `dbswap.py` call, exits `7` while it runs. A lock left by a
  killed build (its process is no longer running) is taken over and reported.
- BaseX `ALTER DB` onto an existing name **replaces** it (MEASURED, BaseX 12.4 and 13.0), so
  `dbswap.py` checks every rename target is absent and never renames by hand.

## Ask

```bash
cd ../x4validate
uv run python ../basex/ask.py refs <macro_or_ware_id> [--db x4eff]
uv run python ../basex/ask.py attr <attribute-name>
uv run python ../basex/ask.py xq   '<raw xquery>'
uv run python ../basex/ask.py xq   --file <query.xq>   # from Git Bash: see QUERIES.md, "From Git Bash"
```

A query over the real corpus takes roughly **1.6 s**.

See **[QUERIES.md](QUERIES.md)** for the two-database model and a cookbook.

---

## Freshness: why it sometimes refuses to answer

This is the part with no equivalent anywhere else in the toolkit, and the reason
a refusal here is a **feature rather than a fault**.

Coverage answers *how much* was indexed. It does not answer *as of when*. An index
that no longer describes the world is a third state beside "found" and "not found":
**an answer about a world that has moved on.** That is not hypothetical — `x4eff`
spent eleven days serving pre-merge-fix values, and on rebuild **140 of 194** engine
thrust rows changed with **not one input file altered**.

So every index carries a **two-axis fingerprint**:

| axis | covers | why it must be separate |
|---|---|---|
| `content` | the installed extension set, each manifest's mtime/size, a reference marker | mods added, removed or updated |
| `engine` | a hash of the merge/enumeration source **bytes** | a merge fix changes the right answer for identical inputs |

`ask.py` prints a banner on every run against a stale index — including alongside
a **positive** result, which is still an answer about a superseded world — and
**refuses** to render a zero-result as a negative finding.

Three verdicts, and the third is the one that matters:

- **FRESH** — both axes match. Answers stand.
- **STALE** — an axis moved. Rebuild before making any claim.
- **FRESHNESS UNKNOWN** — freshness could not be *computed* at all (for example
  `x4validate` is not importable here). This is **not** "stale", and printing
  STALE would be asserting something nobody established. It is reported as
  undeterminable, and an absent fingerprint counts as UNKNOWN — never as fresh.

The shared freshness implementation includes profile enable decisions in the
content axis. Rebuild after installing, removing, updating, enabling or disabling
mods; a fingerprint is not a full byte hash of every game/archive file.

Both builders record the fingerprint before their first read and stamp that, so a world
that moved while one ran reads STALE afterwards, however it was started.

Rebuild with `build-corpus.sh` / `build-effective.sh`; inspect without rebuilding:

```bash
cd ../x4validate && uv run python ../basex/staleness.py --db x4eff --check   # 0 fresh, 5 stale, 6 undeterminable
```

---

## Exit codes

Every code below is **read from the source**, not assumed. Note that `2` always
means *"this could not run as asked"* (not set up, or a usage or input error) and never *"your corpus has findings"* — the same
distinction the `x4validate` CLIs make.

| tool | code | meaning |
|---|---|---|
| **`ask.py`** | 0 | answered (a positive result, or a negative WITH a denominator) |
| | 2 | environment, usage or query error; invalid attribute QName; database mismatch or scoped/nonliteral reach; unreadable/empty query file; negative/noninteger paging argument; or requested paging that cannot be safely counted (omit flags to run unlimited) |
| | 4 | **cannot back a finding** — a zero lacks valid coverage/freshness or a whole-database content-search proof; raw-query syntax is outside the restricted grammar; result is only `0`, `false`, or empty serialization; or Git Bash may have rewritten an argument query (use `--file`) |
| **`coverage.py`** | 0 | complete |
| | 2 | refused — a required root was not supplied (an empty root resolves to the *current directory*, which would publish a denominator measured over the wrong population) |
| | 3 | **accounted** — a deficit exists but every missing document is named, root by root (`base`, `mods`: each root's shortfall equals its own malformed files). Still supports a negative claim. *`x4raw` path only.* |
| | 4 | unexplained deficit, or (`x4eff` path) an overlay catalog that could not be read — its packed members were never enumerated, so no count of them exists; named in `coverage-x4eff.json` → `unreadable_catalogs` — cannot support a negative claim |
| **`staleness.py`** | 0 | fresh |
| | 5 | stale |
| | 6 | freshness undeterminable |
| **`preflight.py`** | 0 / 2 | ready / not ready |
| **`dbswap.py`** | 0 | swapped / prepared / recovered / status printed |
| | 2 | refused: verification failed, disk below the floor, or an unknown database under a reserved name; nothing was swapped |
| | 7 | refused: a reader holds the database open, or another build of it is running; nothing was swapped, retry the same command |
| **`build-effective.py`** | 0 / 3 | all vpaths merged / some failed (the manifest names them; the tree is still built) |

⚠ **The two `coverage.py` paths do not share a scale.** The `x4raw` path has three
tiers (`complete` 0 / `accounted` 3 / `unexplained` 4); the `--eff-manifest` path
for `x4eff` has **no `accounted` tier** and returns only 0 or 4. Do not carry one
path's wording onto the other — that mistake was made while writing this table.

`build-corpus.sh` and `build-effective.sh` propagate their coverage verdict. A
`3` or `4` there means *"the index is built, swapped in and usable, but it cannot yet back
a negative claim"* — not "the build failed". A `7` is the swap refused by a reader:
the new database is built and stamped as `<db>__next` and the previous one still serves.

---

## Discovery vs proof

### Optional result paging

`ask.py refs ore --limit 50 --offset 50` displays at most 50 whole result items
after skipping 50. The same flags work for `attr` and `xq`. Omitted flags retain
unlimited output; `--limit 0` is unlimited and offset defaults to zero. The total
and zero-result checks always cover the full query, not the page. An offset past
the last item reports zero displayed with the positive total. Paging does not
promise faster query evaluation. Raw queries with unsupported count wrappers
(including prologs) refuse requested paging with rc 2, without unbounded fallback.

Raw zeros are certified only for the [documented grammar](QUERIES.md), even when
an unrecognized query happens to search all documents. Positive queries still run.
`attr` requires a lexical QName such as `name` or `xml:lang`; undeclared prefixes
remain BaseX query errors. Predicates and XPath expressions are invalid input.

| question | tool |
|---|---|
| "what values does this attribute take across the corpus?" | **BaseX** |
| "who references / who calls X?" | **BaseX**, or `x4xref` for MD + aiscript cues |
| "what is the LIVE value, and which mod set it?" | **`x4effective`** |
| "do this mod's selectors resolve / is it correct?" | **`x4validate`** |

**A bare "0 hits" is a lead, never a fact.** `ask.py` will not print one as a
finding without a denominator — that guard is the reason this tool is quotable at
all. Prefer `--db x4eff` for any claim about what is live: `x4raw` holds files *as
written* and will happily quote a vanilla value your modlist overwrote.

**Load order is MEASURED from the engine, not documented by Egosoft** (signature-check
order from the log; that it is also apply order was measured by the in-game load-order probe
2026-09-26 -- BLIND-SPOTS F128). The profile's `extensions\` root is measured too (it applies
after the game root; a dependency does not cross between them -- F141); the Steam Workshop
root is unobserved.

## Housekeeping

`basex/data/` may contain databases no current script builds — an early index from
before packed staging existed, for instance. They are inert but not free (one such
fossil measured **908 MB**). `ls basex/data/` to see what is there; only `x4raw`
and `x4eff` are live.

BaseX writes a `.basex` config beside the jar on first use. It records absolute
paths, so it is gitignored — expect to generate your own.

## Licence

The BaseX engine bundled here (`basex/BaseX.jar`) is **third-party software**:
Copyright (c) BaseX Team, licensed under the
**BSD 3-Clause License**. Its full licence text ships beside it at
[`basex/LICENSE`](basex/LICENSE) and must be retained in any redistribution.

It is vendored so this tool installs with no separate download. Everything else in
this directory is part of the X4 Community Toolkit and carries the toolkit's own
licence (see the repository root `LICENSE`).
