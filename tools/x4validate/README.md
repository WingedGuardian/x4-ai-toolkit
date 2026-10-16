# x4validate

Cross-file validator for X4 Foundations mod diff patches. Built because no
off-the-shelf tool reproduces X4's *effective merged tree* + typed reference
graph (and x4cat's `validate-diff` false-negatives the `//` selector idiom —
see KNOWLEDGEBASE.md "Tool Evaluation").

It answers the three questions that matter when one logical change fans out
across many files:

1. **Will this patch silently do nothing?** — every `<add>/<replace>/<remove>`
   `sel=` (and `if=`) is evaluated against the real base+DLC merged tree (via
   lxml — correct XPath, unlike ElementTree-based matchers).
2. **Do the references resolve?** — ware / `{page,t}` / macro / component
   references must point at a real definition. Two scopes: what the mod
   *introduces* via `<add>` ops in `<diff>` files **gates as an error**, while
   references in **full (non-diff) files** report as INFO for now — that half was
   unchecked entirely until 2026-08-13 (1,625 files across 56 mods, 38% of their
   XML). Definitions are **unioned** across base + DLC + mod: text defs from every
   English/language-neutral t-file at any depth (not a fixed two-path list), and
   macro/component names from `index/` ∪ the libraries ∪ — only on a miss — the
   whole corpus, because the index is *not* the definition set. `@ware` values
   that are script expressions (`$var`, `ware.x`) are excluded and counted.
3. **Did I forget a spot?** — completeness: model a changed entity on a vanilla
   analogue and report which footprint pieces are missing.
   ⚠ **Scope: `<ware>`-WRAPPER fields only** (definition, name/description strings,
   price, production, `<component ref>`, owner, restriction). `ship` and `module`
   targets route through the same check, so the macro's INTERIOR — physics,
   connections/hardpoints, engine/shield/turret slots, storage, hull, software,
   steering curves — is never inspected. A clean completeness result does **not**
   mean the macro is complete; the run says so via a NOT CHECKED entry.

v1.1 adds: **file-existence** (a `<component ref>` macro resolves through
index→macro file→component→file), **connection-validation** (every `<loadout>`
`path` matches a `<connection>` on the ship's component), **variant-set
consistency** (patched `_a` but not its base `_b`/`_c` siblings → warn),
**page-id collision** (a `{page,t}` the mod adds by `<diff>` that base/DLC or, under Tier
B, an earlier mod already defines → warn, naming the definer; a complete t-file is reported
once per file with a count — INFO over base/DLC strings, the usual rename idiom, WARN over
another mod's), and a **`--file` fast mode** (sel-resolution for one edited file, for
the per-edit hook). `--file` and `--sel-only` (readability + sel-resolution over the whole
mod) state their denominator, list every other check under NOT CHECKED, and exit 3 if a
check you asked for (`--debug`, `--entity/--like`, `--update`) is one they do not run.

v1.2 closes the **expression-grammar gap** — the XSDs validate XML *structure* but
treat every attribute *value* as an opaque string, so a broken script expression
(`random(1,n)`, `'…'[…]` missing the dot, `{a,b}` list-literal) sails through schema
validation and only fails when the engine parses it at load. Two complementary layers:
- **`_exprlint`** (always-on, *advisory*): a measured regex heuristic over attribute
  values, seeded from the KB Version Migration Map (0 false positives across the whole
  installed mod corpus). Flags the known-broken forms; never gates (it's a heuristic).
  Skips `sel=`/`if=` (those are diff XPath, a different grammar).
- **`--debug <debug.txt>`** (authoritative, *gates*): folds the engine's own `[=ERROR=]`
  lines for *this* mod into the report — load errors (by file path) **and** runtime
  errors (by script name → file, e.g. the `'null' is not a list` cue error). Engine
  errors gate the exit code; a clean static pass is necessary but not sufficient.

v1.3 adds **loader-sanity checks** learned from live debug logs: literal-newline
TextDB warnings in `t/` files, invalid `identification/@makerrace` values checked
against the effective `libraries/races.xml` race list, and non-identical engine
connection tag sets on a single component. These checks run against loose XML and
packed catalog XML through the shared CAT/DAT reader, with loose files overriding
packed members. Connection validation also uses this packed-aware XML iterator.

v2.01 adds **effective-schema validation for data files** (`--update`). 42 vanilla files
under `libraries/` declare a schema in `xsi:noNamespaceSchemaLocation`, and mods ship them
as `<diff>` — so the patch is not the thing to validate, the *merged document* is. This
catches a class nothing else here can: a diff whose selector matches and whose XML is
well-formed, but which leaves the merged document structurally broken (measured: a mod
that `<remove>`s `<production>` orphans the `<limits>` sibling 30 times).

It is **differential**, and that is not a refinement — it is the only form that works.
Egosoft's own base+DLC data fails Egosoft's own bundled schemas (66 errors across 6 files),
the same stricter-than-the-engine gap already known for `md.xsd`. So the tool validates the
tree *without* your mod, then *with* it, and reports only what you introduced. Enumeration
failures naming a race or faction your modlist actually defines are suppressed and counted
(the XSD list is a vanilla floor, not a closed set); one naming something nothing defines
is reported. Structural breakage gates; value-facet complaints are advisory.

## Usage

```sh
# from tools\x4validate\  (requires uv — https://docs.astral.sh/uv/ — which fetches Python 3.13)
uv run x4validate <path-to-mod-dev-folder>
uv run x4validate <mod> --entity ware:my_new_ware --like ware:ore   # completeness
uv run x4validate <mod> --json                                       # machine-readable
uv run x4validate <mod> --update                                     # + 9.0 XSD/migration/exprlint
uv run x4validate <mod> --debug                                      # correlate the active profile's debug.txt
uv run x4validate <mod> --debug path\to\debug.txt                    # correlate a specific log (gates on engine errors)
```

### `x4debug` — the engine's verdict, bucketed and compared to ours

```sh
uv run x4debug triage                      # bucket + attribute EVERY [=ERROR=] line
uv run x4debug crosscheck <deployed-mod>   # per-item diff: engine-skipped ops vs our predictions
uv run x4debug baseline                    # archive the log with a content fingerprint
```

### `x4save` — what a savegame bakes in, and what it would silently lose

Unreadable gzip saves, including invalid DEFLATE data, return **2** with the
path and reason. This is a non-answer; no save is modified.

```bash
uv run x4save info                         # header + the SAVE-BAKED extensions
uv run x4save check <save>                 # macro refs the live tree no longer defines
```

A save is the only artifact the ENGINE wrote, and it is cheap to read: the largest here
is 1.28 GB expanded and streams in **2.5s**. Two measured facts drive the output.

`<patches>` is **not** a record of what loaded — an extension appears iff its
`content.xml` `save` attribute is absent or `="1"`. MEASURED: **3 of 121 mods (2.5%)**.
It is the save-baked set, i.e. the removals that are dangerous.

### `x4live` — what the RUNNING ENGINE saw, diffed against our model

```bash
uv run x4live dump                         # what the probe captured, with denominators
uv run x4live extensions [--scope active]  # the engine's extension list, diffed PER ITEM
uv run x4live errors                       # the engine's own error log, as captured
uv run x4live oracle [--show-derived]      # engine-resolved macro values vs the store
uv run x4live mappings                     # PROPOSE new field mappings (never auto-applied)

# ---- the LIVE half. Needs the game RUNNING and the query mod deployed.
uv run x4live query ping                   # is it there? (`pausestate` says whether it is paused)
uv run x4live query macro shiptypes_s ship_arg_s_scout_01_a_macro hull
uv run x4live query ext ws_2042901274      # is this extension loaded, per the engine
uv run x4live ramp                         # MEASURE the message-size cap
uv run x4live query macro shiptypes_s ship_arg_s_scout_01_a_macro --contents   # table fields, 2 levels
uv run x4live ffi-census                   # DISABLED by default (F124); X4_LIVE_ALLOW_FFI=1 to enable
uv run x4live groundtruth --macros list.tsv --contents   # harvest engine values for YOUR macro list

# ---- the WRITE half: the only two verbs that change the running game.
uv run x4live pausestate                   # READ: is it paused, and did THIS channel pause it?
uv run x4live pause                        # WRITE: pause it (exit 0 only if the engine reads back paused)
uv run x4live unpause                      # WRITE: undo a pause THIS channel made
```

Every other tool here reasons about **files**. This one reports what X4 itself held in
memory, which makes it the toolkit's only **oracle** — the one thing that can show our
merged tree is wrong rather than merely self-consistent.

The channel needs no pipe, no launch flag and no permissions grant: a read-only lua
probe assigns a TSV payload to a lua global declared as a `<savedvariable>`, and the
engine serialises it into `{profile}/uidata.xml`.

⚠ **`uidata.xml` is only readable while the game is CLOSED.** MEASURED: 342,342 bytes
closed, **61 bytes** (`<uidata version="1"/>`) while running — X4 truncates it at startup
and writes it back on exit. That stub is valid XML and parses to zero of everything, so
a naive reader run mid-session reports "0 extensions" in the grammar of a real answer.
`x4live` refuses it with **rc 2** and says which of the four states it is in.


#### What you must install for the live half

`x4live` is the only tool here that needs anything inside the game. **One extension, and
one third-party dependency you install yourself:**

| what | why | where |
|---|---|---|
| **X4 Toolkit Helper** | ships with this toolkit under `mods/`. Copy it into `{game}/extensions/`, or run `uv run python scripts/deploy-mod.py x4_toolkit_helper --apply`, which refuses a wrong target and re-reads what it wrote. It answers a fixed, enumerated vocabulary: read verbs, plus exactly two argument-free write verbs, `pause` and `unpause`, and no eval of any kind | `mods/x4_toolkit_helper/` |
| **Mod Support APIs** (`ws_2042901274`) | supplies the named-pipe lua the pipe half calls | Steam Workshop / Nexus |

The dependency is declared **optional**, deliberately. Without Mod Support APIs the
addon still loads and the **uidata half keeps working**; only the pipe verbs report that
the api is unavailable. The two halves fail independently.

**Everything else in this toolkit needs neither.** The other ten commands read files and
archives on disk.

If a pipe verb cannot reach the game it refuses with **rc 2** and names what is missing,
including both installs — a refusal that cannot tell you what to install is half a
refusal. Since 2026-09-20 it **measures** rather than surveys: whether X4 is running
(`tasklist`), whether the helper extension is in the set the engine would load
(`mods("active")`), whether it logged its load marker in a *live* `debug.txt`, and whether
its window is minimized (`IsIconic`). Each answers **true / false / could-not-determine**,
and a refusal reports only the ones it actually measured.

⚠ **Do not MINIMIZE the game. Merely unfocused is fine.** Three claims, each with its own
evidence and date — they are kept apart because flattening them is what made the old
advice ("X4 stops executing when it is not in the foreground") wrong:

- **unfocused is fine** — measured 2026-08-30 by sampling the engine's own
  `getElapsedTime()` over a 30 s wall window: unfocused **32.98 s / 32.98 s** and focused
  **32.24 / 32.24**, a ratio of **1.00 in both**. A 370-query harvest completed cleanly
  while unfocused. That clock is real time and keeps running while paused, so this shows
  the frame loop runs, which is what the channel needs; it is not a measurement of
  simulation speed.
- **minimized stops it, in BOTH display modes** — originally one figure from exclusive
  fullscreen only (574.76 s of engine time across 70.4 min, **13.6%**), which never
  separated minimized from merely unfocused and is how the generalisation arose. Measured
  directly 2026-09-20 in both modes: windowed-minimized **0.031** CPU core-s/s and
  fullscreen-alt-tabbed **0.016**, against **0.81–25.9** while active. The window handle
  survives a display-mode change and an alt-tab away, so `IsIconic` is a valid test in
  either mode.
- **a paused game is not a cause** — measured 2026-09-13: it kept answering on an open
  connection and accepted a new one.

Either way it is retryable, not a failure: the channel re-arms itself every ~2 s.

**Not measured: a HUNG game.** `IsHungAppWindow` is not exported by this `pywin32`, and a
hang cannot be induced on demand, so that state is left unreported rather than guessed at.

**Message size**: replies are bounded before sending, because an over-long message does
not truncate — it tears the pipe down. (That is the SEND direction, game to us. The READ
direction fails the other way: `pipes.lua` returns partial data with `ERROR_MORE_DATA`,
which is why `ramp` exists — see below. The two sentences describe opposite ends of the
same pipe, not a contradiction.) MEASURED in game: **524,288 bytes round-trip
intact**, against a self-imposed reply cap of 32,000. The true ceiling is above that and
remains unmeasured; the ramp reports its own limit rather than presenting it as the
engine's.

#### The live half — `query` and `ramp`

Because `uidata.xml` is unreadable mid-session, the offline oracle above can only ever
answer about a game that has been **closed**. `query` is the complement: a fixed
vocabulary of questions asked of the running engine over a named pipe. It only reads, and
it refuses the two write verbs by name; they have subcommands of their own (below).

We create the pipe and the game connects to it, which is why the game-side mod is four
files and **zero MD**: `sn_mod_support_apis`' `pipes.lua` opens a *client* handle and does
not care who served it, so `Pipe_Server_Host`, `Register_Module` and `permissions.json`
are all unnecessary. Requires `pywin32` (a Windows-only dev dependency); without it the
command refuses with rc 2 naming the reason, and every offline subcommand still works.

⚠ **`ramp` exists because the transport truncates silently.** `pipes.lua:698`, verbatim:
*"If the message is larger than the lua side buffer, returns partial data and error
`ERROR_MORE_DATA`. TODO: look into this."* That TODO is unhandled, and a truncated TSV
row is still a well-formed TSV row. The real ceiling is undocumented — python buffers
1 MiB (raised from 64 KB so the ceiling could be probed at all), an earlier note
claimed 2047 bytes from the winpipe DLL with no traceable source,
and the mod's own readme states no limit — so every reply carries its own **byte length
and checksum**, and `ramp` measures the ceiling instead of assuming it.

The three silence states are kept apart, never collapsed into one verdict: **never
connected** (game not running, the mod not deployed, or no save loaded yet — the mod
initialises on game load, so at the main menu it has legitimately not armed),
**connected then silent** (the mod IS loaded, but the game is minimized, or hung), and
answering.
A PAUSED game is not silent: MEASURED 2026-09-13, it answers on an open connection and
accepts a new one. A sibling project shipped a
liveness probe that was wrong for three releases because it hung in exactly the case it
existed to detect.

Two facts the output is built around, both measured the hard way:

- **`GetExtensionList()` is an installed INVENTORY with an `enabled` flag, not the load
  set.** Comparing it to the wrong registry scope is the same category error as reading
  `Collision.winner` as an owner. The scope is explicit and printed.
- **Totals lie.** The first run of this channel produced 132 extensions from the engine
  and 132 from our model — and the membership was wrong on *both* sides, two errors
  cancelling. Everything here compares per item.

Two things the comparison had to learn the hard way. **The engine does not always answer in
the units the XML declares** — rotational thrust arrives in radians/sec against a store holding
degrees/sec (`3.8397243` rad = `220.0000°`), so the map carries a *declared* transform; a
transform inferred at comparison time would build a check that cannot fail. And **a field NAME
does not determine its meaning**: `shield` on a shield generator is that generator's capacity,
while on a *ship* it is the loadout-derived total — so the map is keyed by library type.

`oracle` **refuses a stale store (rc 3)** rather than warning like the other tools:
elsewhere a stale answer is still an answer about a slightly older world, but here the
whole output is a verdict on whether our model matches the engine, so staleness does not
degrade the result — it inverts what it means.

#### The write half — `pause`, `unpause`, and `pausestate`

Exactly two verbs change the running game. They exist because a live measurement drifts
while the game runs — a faction gains tens of objects a minute — so two questions asked
seconds apart cannot be compared unless the simulation is held still between them.

**A paused game still answers.** Every `x4live` command opens its own connection, so
`x4live unpause` after `x4live pause` depends on a paused game accepting a new one.
MEASURED 2026-09-13: `x4live pausestate` from a fresh process read `paused=true owner=us`.
The same session showed the player's pause key and this channel's pause acting as separate
holds: with both held, `unpause` read back still-paused, gave up its claim, and did not retry.

**A menu clears our pause — the player's survives.** MEASURED in-game 2026-09-15: X4 has two
pause levels. The player's manual pause survives opening and closing a pause-on-open menu
(Options); this channel's `pause` (a bare `Pause()`) is UNPAUSED by that same menu cycle
(`true owner=us` → open+close Esc → `false owner=none`). The engine's menus gate their
close-time `Unpause()` on the user-pause flag, which the manual pause sets and the raw call does
not. So `x4live pause` is durable only for a brief **menu-free** window (pause → measure →
unpause); if the player opens any pausing menu, our pause silently drops. `pausestate` cannot
tell a player or menu pause from another — all read `owner=other`; only ours reads `owner=us`.
See KNOWLEDGEBASE.md 2026-09-15.

- **Ownership, not a toggle.** `unpause` undoes only a pause this channel made, and refuses
  the player's or another mod's — the discipline vanilla's own menus keep. A UI reload
  (alt-enter, loading a save) re-creates the mod's lua, which then cannot prove the pause is
  its own, so that pause is undone in game.
- **The read-back is the result.** Each reply reports what the engine says *afterwards*.
  Exit `0` only when that agrees with what was asked; `1` means refused and nothing
  changed; `3` means the state is not what was asked, or could not be verified.
- **A lost reply is not a lost write.** The command is sent before the reply is read and is
  never resent. After a write exits `2` or `3`, or is interrupted with Ctrl-C, run
  `pausestate` before anything else.
- **`query` refuses both names**, so its free-text passthrough is not a second door, and
  each write prints a banner on stderr, never on stdout.

### `x4save` — removing a mod loses content silently

And removing a mod leaves **no** dangling references — the engine deletes the orphaned
content **silently**. MEASURED on one removal: 37 macros went to 0, the engine logged
**one** error line naming a galaxy connection rather than any of the lost content, there
was no dialog, and the net error count went *down* because the mod's own errors left with
it. `check` is worth running precisely because `debug.txt` will not tell you.


`triage` states the log's mtime and whether it was a **new game** or a save load — error
counts are not comparable across that boundary — and its rows must sum to the lines read,
with the unclassified row printed **even when zero**. A row that appears only when non-zero
teaches you to read its absence as "nothing there" rather than "not measured".

`crosscheck` reports three buckets, never two totals. `observed-not-predicted` is a
**validator blind spot**, and is the reason the command exists: it is what found that a
diff's ops apply in order to a mutating tree (see `docs/BLIND-SPOTS.md`).

The expression linter runs on **every** invocation (cheap, advisory). `--debug`
is the authoritative gate — run it against a `debug.txt` captured *after* your latest
edits (a gate on a stale log is a false failure).

`--update` is deliberately slow: schema compilation dominates. Budget ~100s for the
md/aiscripts schemas, and a further **~122s if your mod patches `libraries/diplomacy.xml`**
— that one schema pulls in the 40k-line `common.xsd`, where every other data schema
measured ≤0.1s. It looks like a hang and is not.

**The cheap half of the script check runs by default**, since it needs no schema compile: the
required-attribute class — the KB's most reliable 9.0 migration signal — is checked on every run,
with corpus-wide parity against libxml2 proven by `gates/xsd_fast_parity.py` (555 script files, 0
false positives, 0 misses) and a measured cost of ~0.1s on the heaviest script mod in a 125-mod
install. What still needs `--update` is the `element not expected` class, where element-ordering
errors live, plus the schema-strict advisories — and a default run now says so by name rather than
reporting a bare "OK".

### Index and store integrity

`x4xref build` now records SHA-256 digests for the TSV and its exclusion list
in the freshness sidecar. Lookups validate the exact header and every row before
answering; malformed rows are never silently dropped. Damaged or digest-mismatched
artifacts return **2** with rebuild guidance. Valid older unsigned indexes can
still return positive results with an integrity warning; absence requires valid
digests and current source freshness, otherwise **2**. Rebuild explicitly with
`uv run x4xref build`; queries never rebuild automatically.

`x4effective` opens stores read-only and checks the schema version and required
table/column structure. Missing, corrupt or incompatible stores return **2** with
the path and rebuild guidance. Malformed freshness vectors that fail comparison
also return **2**; valid stale stores remain readable with their warning.
Ordinary invalid SELECT queries retain **1**;
disallowed writes remain refused. Queries never repair or rebuild stores.

### `x4modlist changed` — which mod moved, when a fingerprint does

```sh
uv run x4modlist changed                       # vs the effective store (default)
uv run x4modlist changed --since latest --files  # vs the newest snapshot, naming files
uv run x4modlist snapshot --label pre-wave     # ~0.1s baseline, no rebuild needed
```

Every persisted artifact carries a two-axis fingerprint saying *when* it was true. When the
**content** axis moves, this says **what moved**: added / removed / version / content /
`touched` (rewritten with identical bytes) / `toggled` (enabled or disabled in the profile).

It exists because localising ONE changed mod once took nine investigative steps, and only
worked because that mod happened to bump its `version`. Three of those steps were the wrong
instrument — notably `find -newermt`, which is **structurally blind to a back-dated write**,
and the mod in question had back-dated all 26 of its files. Per-file identity is therefore
hashed as a **set** of `(relpath, mtime, size)`, never `max(mtime)`.

`snapshot` fills the gap between expensive rebuilds: the store and the xref index only
advance when something slow runs, so several days of drops otherwise collapse into one
unsequenced delta. Snapshots are named by **content**, so repeats of an unchanged world
collapse onto one file.

**A baseline with no vector exits 3 and says so.** Artifacts built before the vector existed
cannot answer "what changed", and reporting *no change* for one would be a wrong answer where
a non-answer is the honest one.

⚠ **Scope limit.** Per-file identity is `(mtime, size)`, not a content hash — hashing every
file would mean reading multi-hundred-MB `.cat` archives on every run. An edit changing
neither size nor mtime is invisible, and a same-size edit reads as `touched` rather than
`content`. Manifests *are* hashed. `--usn` adds NTFS change-journal corroboration (immune to
back-dating and to NTFS tunneling) but needs an elevated shell, so it degrades to a message
rather than failing.

⚠ MEASURED on an unelevated shell: **`fsutil usn queryjournal` SUCCEEDS while
`fsutil usn readjournal` returns `Error 5: Access is denied`.** The probe therefore tests
`readjournal` — the operation actually needed — because probing with `queryjournal` reported
the journal as readable when it was not. A tool claiming a capability it lacks is the
false-positive this package exists to refuse.

### Exit codes

| code | meaning |
|---|---|
| **0** | clean — and something was actually examined |
| **1** | error-level findings (use as the pre-deploy / pre-pack gate) |
| **2** | could not run: bad path, missing reference tree, usage error |
| **3** | **degraded** — a check you asked for could not run, so a clean result proves nothing |

**3 is separate from 1 on purpose:** a gate needs to tell "your mod is broken" from "the validator
was blindfolded". Cases that reach it: no `content.xml` (X4 would never load the folder), a packed
mod whose catalog will not open, an overlay that would not parse so the comparison tree is
incomplete, a `--like` analogue that does not exist, a `--debug` log that cannot be read, or a
check you asked for that `--file`/`--sel-only` does not run.

A mod with **no `<diff>` files is not degraded** — additive-only and asset-only mods are normal and
are reported as notes. Measured on a ~120-mod install: 16 additive-only, 1 asset-only, 0 unreadable.

Every run states its denominator (`sel-resolution: N diff file(s) checked across M payload XML
file(s)`), because "checked 14 files, all fine" and "checked 1 file, all fine" must not print the
same way. "Checked" means the ops were EVALUATED: a diff with no base, an inactive target (not
installed, or installed but disabled) or an inert bare path is counted separately.

Default merge tier is **A** (base + DLC, deterministic); `--tier b` also folds in the ACTIVE mods
(installed and enabled) in the engine's measured load order.

## Configuration — where it looks for things

Run **`x4validate --paths`** to see what resolved, and which config file was read.
Do that first whenever a result looks impossible: silent misconfiguration is this
tool's worst failure mode, because "found nothing" and "looked in the wrong place"
print the same way.

Every location resolves in **layers** — for each one, all aliases *and* derivations
in a layer are tried before dropping to the next:

1. **Environment.** The installer's names first — `X4_GAME`, `X4_EXTENSIONS`,
   `X4_PROFILE`, `X4_MODS`, `X4_REFERENCE`, `X4_DEBUGLOG` — then the older
   `X4_GAME_ROOT`, `X4_GAME_EXTENSIONS`, `X4_PROFILE_CONTENT`,
   `X4_PROFILE_EXTENSIONS`, `X4_WORKSHOP_CONTENT`, `X4_REGISTRY`.
2. **`x4-paths.env`** in the toolkit root — the file `install.sh` / `install.ps1` write.
   `$X4_CONFIG` names it explicitly (naming no file means none is read); else it is
   the acting toolkit's `x4-paths.env`, else its 3.x `.claude/x4-paths.env` (deprecated,
   one notice per run; `python scripts/x4config.py migrate --apply` moves it).
   The acting root is explicit `--toolkit` where supported, then the toolkit this
   Python code lives in, then `$X4_TOOLKIT` when the code lives outside a toolkit.
   Only with no acting root does config lookup walk up from the current directory.
3. `_paths._LOCAL_FALLBACK` — deliberately empty; the documented seam for a local
   override, so nobody hardcodes one somewhere else.

**`X4_TOOLKIT` is useful for external invocations.** The installers set it (unless
`--no-env` / `-NoEnv`); it is not required when running a toolkit's own Python code:

```sh
setx X4_TOOLKIT "C:\path\to\toolkit"           # Windows (new shells only)
echo 'export X4_TOOLKIT=/path/to/toolkit' >> ~/.bashrc   # Linux / macOS
```

An inherited `X4_TOOLKIT` naming another install does not redirect this copy of the
Python tools: it prints both roots. System-changing commands require an explicit
root choice before acting across that conflict. Individual `X4_*` path overrides
still have the environment precedence listed above.

Prefer the **installer names**. The two schemes exist because until v2.01 the
installers wrote one set and the Python read another, overlapping on a single name
(`X4_REFERENCE`) — so a successful install still left every cross-mod command
pointed at CWD-relative paths. The old names keep working; they are not the ones to
teach.

Values may reference each other — `X4_REFERENCE="$X4_TOOLKIT/reference"` and
`${X4_TOOLKIT}/reference` both expand, matching what the shell half does with the
same file. The file is parsed, never executed, so only `$VAR` expansion happens: no
command substitution, no subshell.

**Git Bash / WSL drive paths are translated on Windows.** `install.sh` writes
`/c/Program Files (x86)/...` under Git Bash; `/c/...` and `/mnt/c/...` become
`C:/...` so Python can open them. On Linux those are left exactly as written,
because there they are legitimate absolute paths.

Derived when not set explicitly: `$X4_GAME/extensions`;
`$X4_PROFILE/{content.xml,extensions,debug.txt}`; `$X4_MODS/_registry/modlist.yaml`;
and the Steam workshop directory from `steamapps/common/<game>` — **only** when the
install really has that shape, since a guessed path scans nothing and would report
"no mods" as though it were a finding.

Nothing is guessed: an unresolved location prints `(unresolved)` and the run says so
rather than quietly substituting a default. `$X4_NEXUS_KEY` is deliberately NOT
handled here — it is a secret, not a path, and must never be written to a file.

## Layout
- `x4validate/_paths.py` — path resolution (the layers above) + `--paths` report.
- `x4validate/_merge.py` — effective-tree assembly (diff apply: add/replace/remove,
  pos/if/silent; full-file override by root element).
- `x4validate/_xpath.py` — lxml XPath wrapper (genuine no-match vs invalid-expr).
- `x4validate/_refs.py` — reference graph, dangling-ref detection, completeness.
- `x4validate/_exprlint.py` — expression-grammar heuristic (attribute-value rules).
- `x4validate/_debuglog.py` — `debug.txt` parser. 7 mod-identifying shapes (incl. diff-op
  cardinality and index-lookup misses) plus 17 engine-SUBSYSTEM shapes that name a game
  entity rather than a mod. `parse_log` accounts for every `[=ERROR=]` line: classified,
  or labelled `unclassified` and counted — never dropped.
- `x4validate/_debugcli.py` — `x4debug`: triage / crosscheck / baseline.
- `x4validate/_savecli.py` — `x4save`: savegame header + save-vs-live-tree
  reference check. Streams gzip; never builds a tree.
- `x4validate/_livedump.py` — decodes the in-game probe's payload out of
  `{profile}/uidata.xml`. Owns the THREE escaping layers (XML entities → lua escapes,
  where a tab is `\9` but `\009` before a digit → the probe's own field escaping) and
  the four-outcome contract that keeps a running-game stub from reading as an empty
  result. Six independent guard clauses, each with its own fixture and mutant.
- `x4validate/_livepipe.py` — the live query channel: named-pipe transport plus an
  eight-clause frame contract (truncation, corruption, protocol skew, FIFO desync).
- `x4validate/_livecli.py` — `x4live`: dump / extensions / errors / oracle / archive /
  mappings / query / pausestate / pause / unpause / harvest / ffi-census / groundtruth /
  ramp (the 14 subcommands of `x4live --help`, 2026-09-26). The only
  tool that compares our model against the ENGINE rather than against more files.
- `x4validate/_freshness.py` — the two-axis fingerprint every persisted artifact carries, and
  the per-folder **content vector** it is folded from. Deliberately NOT in `ENGINE_SOURCES`:
  widening the content axis must not claim the merge code changed.
- `x4validate/_changed.py` — `x4modlist changed` / `snapshot`: localise a moved fingerprint by
  diffing two vectors instead of investigating. Owns no enumeration of its own.
- `x4validate/_xsd.py` — schema validation: script files as written, data files as merged
  (differential — see `introduced`).
- `x4validate/_check.py` — orchestration + t-file union; `_cli.py` — CLI.
- `tests/` — `uv run pytest` (the count is deliberately not written here; it rots).
- `gates/` — measured against the engine / the real modlist, not fixtures. Four engine
  gates: `oracle.py` (diff layer, 0 FALSE OK), `oracle_index.py` (index layer),
  `regress.py` (per-mod Tier A/B sweep), `schema_sweep.py` (effective-schema
  composition), plus `oracle_reverse.py` (the direction the first two cannot test:
  the engine complained — do we notice?). Plus eleven corpus audits added in v2.1.0 —
  `noop_audit.py` (reported ≡ actual, per op), `provenance_audit.py` (a changed value
  names its mod), `consistency_audit.py` (store ≡ merge ≡ dump), `corpus_sweep.py`
  (every installed mod, both tiers), `qa_sweep.py` (every CLI × subcommand),
  `edge_sweep.py` (hostile inputs), `reader_edges.py` (damaged archives/encodings),
  `determinism_audit.py` (identical input ⇒ identical output), `mutation_probe.py`
  (break it and require the suite to notice), `fuzz_diff.py` (random legal ops vs
  invariants) and `stress_sweep.py` (unseen corpora, chained tools, pathological XML).
  v2.1.1 adds eight output-truth and parity gates — `tool_properties.py` (properties for
  the thinnest-covered tools), `cross_tool.py` (two tools must agree; a planted delta must
  be detected), `diff_truth.py` (planted mutations, reported exactly), `similar_audit.py`
  (every pair recomputed), `update_corpus.py` (every documented 9.0 break planted and
  found), `xsd_fast_parity.py` (the fast path must be set-equal to libxml2, corpus-wide),
  `perf_guard.py` (per-mod runtime, never an aggregate) and `nexus_fixture.py` (the
  network path, replayed offline).
  v2.2.0 adds `registry_provenance.py` (a guessed mod identity may never produce a
  confident verdict).
  See `gates/README.md` for the roster and the bar each one holds; `bash scripts/run-gates.sh --list` prints the live count.
- `docs/TRUST.md` — **start here if you are deciding whether to rely on this.** Every
  defect *shape* ever found in the toolkit, the measured cost when it happened, and
  the test or gate that now bans it — so *"is this shape guarded?"* is a lookup, not a
  matter of confidence. It also states plainly where the tools cannot be trusted.
- `docs/QA-PROCESS.md` — the process these gates came out of: what to test, in what
  order, and when it is honest to call a tool releasable. Read it before adding a tool.
- `docs/BLIND-SPOTS.md` — the per-finding register: every tool limit and defect found,
  each with a measured denominator and the check that now re-derives it
  (`gates/register_rederivation.py` fails a FIXED entry that names none). Its evidence
  cites a real installed modlist; personal overlays appear only as
  `zzz_personal_overlay_A` to `_G`.
- The repository ROOT's `CHANGELOG.md` covers this package too — there is no separate
  one here. Read the **2.3.0** entry before upgrading from below it: it carries a
  **breaking change to `x4compat --json`** (SUBTREE rows no longer populate `winner`;
  use `wiped_by`, or `live_value_owner()`) shipped under a MINOR version bump, so the
  version number alone does not warn you.

## Extending
Add reference types by extending the catalog in `_refs.py`; add completeness
recipes per content type (ship, module, …) alongside `ware_completeness`. Add an
expression-grammar rule by appending to `_exprlint.RULES` — **measure it against
vanilla `reference\` first** (a rule that flags valid code is dropped, not shipped;
see the removed `{a.b, c.d}` catch that fired on valid `.{[list]}` accessors).
