
Guidance for the AI coding agent working in an X4: Foundations modding environment.
This file is loaded automatically every session.
An agent this toolkit has no adapter for: read `ADAPTING.md` before relying on any guard.

Keep your learned facts in `KNOWLEDGEBASE.local.md`; toolkit updates preserve it.
Read it each session, including its `## My preferences` section. Imported legacy
notes remain evidence to review; do not turn unclassified notes into standing orders.

## What This Is

An AI-assisted X4 (v7.x–v9.x) modding workspace. The goal is coordinated multi-file XML
editing — adding and modifying wares, ships, stations, and balance values consistently
across every file a feature touches. The agent's role is to handle the tedious, error-prone
cross-file work and catch the silent-failure bugs before an in-game test cycle is wasted.

## Key Paths (personalize during setup)

The toolkit/project root ({{PROJECT_DIR}} — wherever you put this folder) holds the
working dirs `reference\`, `dev\`, `dist\`, and `tools\`. The safety hooks anchor on it, so
nothing here hardcodes a user path — `setup.sh` and the environment variables below personalize
the rest.

| Location | What |
|----------|------|
| Game root | your Steam/GOG `X4 Foundations\` folder (holds `01.cat`–`09.cat`, `extensions\`) |
| Base game archives | `{game root}\01.cat` … `09.cat` (+ DLC under `extensions\ego_dlc_*`) |
| **Reference (read-only)** | `{project root}\reference\`, unpacked from your OWN game with XRCatTool (`X4_REFERENCE`) |
| Mod dev workspace | `{project root}\dev\{mod_name}\`, one folder per mod |
| User profile | `Documents\Egosoft\X4\<profile-id>\` — the active one has the newest `debug.txt`/saves |
| Active mod list | `{user profile}\content.xml` |
| Testing (in-game) - deploy HERE | `{game root}\extensions\{mod_name}\` (game-root, **never** the profile) |

> **Never redistribute Egosoft game data.** `reference\` is unpacked from your own copy and
> is gitignored. The toolkit ships no `.cat`/`.dat`/XML game content.

### Cross-platform & configurable paths
Works on **Linux, macOS, and Windows (Git Bash)**. None of the paths above are hardcoded — they
are read from `x4-paths.env` in the toolkit root (copy from `x4-paths.env.example`; the 3.x
location is still read, deprecated) or matching env vars, in this order: **env var > `x4-paths.env` > default**. The hooks normalize `\` vs `/` and
case, so either path style works. Keys: `X4_TOOLKIT`, `X4_GAME`, `X4_REFERENCE`, `X4_PROFILE`,
`X4_DEBUGLOG`, `X4_MODS`, `X4_EXTENSIONS`, `XRCATTOOL`, `X4_APPMANIFEST`, `X4_NEXUS_KEY`.
- **XRCatTool runs through `bin/xrcat`** — directly on Windows, via **Wine** on Linux/macOS (it
  translates unix paths to `Z:\` so a leading `/` isn't read as a switch). `bin/unpack-reference.sh`
  unpacks base+DLC (text-only) into `reference/`.
- **Install methods** (`install.sh` / `install.ps1`): in the game folder, in a separate dir, or
  globally across multiple mod repos. See README.

## Bundled Tools

| Tool | Purpose |
|------|---------|
| **x4validate** ⭐ | Cross-file validator: every diff `sel=` resolves against the real base+DLC merged tree, ware/macro/`{page,t}` references resolve, and new content is complete against a vanilla analogue. **Run on every mod before deploying.** `cd tools\x4validate && uv run x4validate <dev\mod>` |
| **x4modlist** | Mod-registry triage via the Nexus API. The INSTALLED extension folders are the primary source of truth; `content.xml` is a secondary cross-check. |
| **x4compat / x4xref / x4stats / x4similar** ⭐ | Cross-mod suite, one package: collisions over the effective tree (packed mods included), a who-calls/who-listens index over MD+aiscripts, advisory numeric comparison, and fuzzy same-ship detection. → the `x4-mod-interaction` skill. |
| **XRCatTool** (Egosoft) | Unpack base CAT/DAT → `reference\`; pack `dev\` for distribution. You supply it. |

## Mod Structure

Every mod lives in its own folder. Never merge mods into a single mega-file.

```
dev\{mod_name}\
├── content.xml                  ← mod manifest (id, version, dependencies)
└── {game-path-mirrored}\        ← folder structure mirrors the game's internal paths
    ├── assets\wares\            ← ware patches/additions
    ├── libraries\              ← economy, factions, etc.
    └── ...
```

**Deploy for testing:** copy `dev\{mod_name}\` to the **game-root** `{game root}\extensions\{mod_name}\`,
**never** the user profile's `extensions\`. Dependencies resolve only within the same
extensions root, so a mod deployed to the profile makes X4 report every dependency it
declares as MISSING.
**Distribute:** pack with XRCatTool → `ext_01.cat` + `ext_01.dat`.

## Silent No-Op Traps (Mandatory)

The failures that log nothing. These bite before any skill would fire, so they live here.

1. **A diff path must mirror the game path EXACTLY.** One wrong folder and the patch does nothing.
2. **A `sel=` matching MULTIPLE nodes applies NOTHING.** RFC 5261 wants exactly one match; X4 logs
   *"Multiple matching nodes ... Skipping node"*. Disambiguate with a content predicate, and run
   `x4validate` — it flags this.
3. **Patching another MOD uses a NESTED path**, `<your_mod>/extensions/<target>/<mirrored path>`.
   The `<dependency id=>` is the target's `content.xml` **id**, which can differ from its folder.
4. **`content.xml save="1"` bakes the mod into saves** — removing it later can corrupt them.

→ **the `x4-xml-patching` skill** carries the rest: the diff idiom, merge and op-order semantics,
load order, cat/dat, t-files, and the overlay decision table (which fix belongs in which overlay).
**`x4-update-mod`** carries the 7.x→9.0 migration, including the `space=` family that now refuses.
## Validation Convention (Standing Rule)

**Running `x4validate` is routine and non-optional** — like checking `debug.txt`. Run it after
editing any patch and BEFORE deploying for an in-game test, and again after a game update. It
catches the two most expensive bugs statically: a `sel=` that silently matches nothing, and a
file the change forgot. **A clean run is necessary, not sufficient** — still test in-game.

→ **the `x4-xml-patching` skill** has the flags: `--tier b` for cross-mod work, `--entity/--like`
for completeness, and why an `if=`-guarded op reports INFO while a passing guard over a missing
`sel=` is a real error.

## Dry-Run Convention

For any bulk XML operation (mass stat changes, adding content to many files):
1. **Read-only pass** — log every file and value that would change; do NOT write.
2. **User reviews** the proposed changes.
3. **Write pass** — only after approval.

## Safety Rules

{{AGENT_ADDENDUM}}

### Hard blocked
- Writing to `reference\` (read-only base game data, ever)
- Directly writing `.cat` / `.dat` files (use XRCatTool)
- `sed -i` on a game or profile file (edit through your file-edit tool, which is backed up first)
- Deleting the game installation folder itself (or its whole `extensions` folder), or anything in
  `reference\`
- A bare `git clean` with `-x`/`-X`/`-d`, or `git reset --hard`, run from an X4 directory (no
  folder named): in the game folder git ignores every file, so it would delete the installation's
  own files

### Requires confirmation
- Changes to non-save user-profile files (`Documents\Egosoft\X4\`); a `content.xml` edit is only ADVISED
- Deleting other files in the X4 profile; other deletes in an X4 directory get an
  advisory, not a prompt
- `git -C "<X4 folder>" clean` / `reset --hard` (the folder named): it deletes untracked files

Recognized mutations of one or two existing saves first preserve a permanent verified
transaction automatically. Bulk save access requires qualified human approval bound to
the complete canonical paths, logical count and command hash. Never split a batch to
evade this policy. Missing preservation or evaluation is a refusal, not permission.

### General
- One mod = one named folder, never a mega-file
- `reference\` is never edited — it is source-of-truth for base game XML
- **Deletions go to the Recycle Bin / Trash, always** (Windows: `Microsoft.VisualBasic.FileIO.FileSystem`
  `DeleteFile`/`DeleteDirectory` with `SendToRecycleBin`; Linux: `gio trash`; macOS: Finder Trash).
  A permanent delete needs the user's OK for that deletion. Report what went and what failed.
  The guards judge a move to the trash exactly as a delete (same blocks and confirmations).
- Retention: `RETENTION.md`, `scripts/x4housekeep.py`. `cleanupPeriodDays`: user settings only.
- **Resources: limit work you start yourself** (test suites, corpus builds, load tests, parallel
  agents): together at most 40% of CPU and 25% of RAM, low priority, at most eight test workers;
  load probes idle and time-boxed. The game and work the user directs are exempt. Hard floor: the
  system drive keeps min(pagefile, 50 GB) free, other drives min(5%, 50 GB). Raise a concern only
  when a job could reach a floor; never terminate another session's processes.
  Memory limits refer to **physical RAM**: admission leaves 20% available. Commit uses a fixed
  512 MiB reserve after job reservations, never a percentage; stop our job below 256 MiB commit
  for 10 seconds or 128 MiB immediately. Physical-RAM emergency stops remain 2 GiB/10 seconds
  and 1 GiB immediately. User-directed work is exempt from admission shares, not emergency floors.

### Iteration snapshots (standing process)
Before experimenting on a working state, snapshot it to `.claude\backups\known-good-<name>\`.
After confirming a state works in-game, snapshot it named for *what works*. Especially
important for large files iterated many times.

## Confidence Levels (Mandatory)

Before proposing ANY change to mod files, game XML, or profile files:
1. **State a confidence level** (0–100%) for each proposed change.
2. **List assumptions** it depends on.
3. **Investigate first** — check `KNOWLEDGEBASE.md`, read the actual files in `reference\`, do Nexus research.
4. **Target ≥ 90%** before writing. Below that, document what's uncertain and what research would raise it.

| Range | Meaning | Action |
|-------|---------|--------|
| 95–100% | Verified via testing/docs/authoritative source | Proceed with user confirmation |
| 80–94% | Strong evidence, not fully verified | Proceed with caveats |
| 60–79% | Reasonable assumption, some unknowns | Research more first |
| < 60% | Speculative | Do NOT proceed — investigate |

## Core Principle: Tooling Comes FIRST — Everything Else Is Downstream

**Never frame tool work as time taken away from "the real work."** Work produced on an untrusted
instrument is not wasted, it is **negative**: it is confident, it compounds, and it gets written
where the next session reads it as truth. When a tool defect and downstream work compete, **the
tool wins** — stated as a reason, never as an apology.

**A tool that cannot distinguish a GUESS from a MEASUREMENT is a defect, not a limitation.**
Provenance travels with the value: a guessed field must never occupy the same slot, in the same
grammar, as a verified one, and nothing derived from a guess may be promoted into a confident
state. That applies to our own output too — a report, a registry row and a knowledgebase line
each carry their tier or they do not ship. The registry once stored a GUESSED Nexus id beside
`settled: stable`, so "is my copy the old version?" could not be answered from it at all.

## Core Principle: Do Your Homework (Due Diligence Before Acting)

Do enough due diligence before changing anything that the user has to do as little
trial-and-error and manual verification as possible. This does NOT mean cut corners, and it
does NOT mean skip steps where the user is genuinely needed (in-game testing only they can
do). It means: verify formats, read the actual `reference\` files, research the established
technique (web + Nexus), confirm tool/API capabilities — *then* make the change. Every
in-game test cycle costs the user real time; burn your own tokens on verification so theirs
aren't wasted.

## Core Modding Principle: Copy a Working Example, and Use the Engine's Own Mechanisms

**Before implementing any change — even a novel one — find how the base game (in `reference\`) or
an installed mod already does the closest thing, and model yours on that.** The unpacked game is
proof-of-concept; where vanilla does not do it that way, ask *why* first.

★ **COPY IT. DO NOT COMPOSE FROM THE SCHEMA.** A schema says what is well-formed, never what is
*wired up*: an MD harness composed from `md.xsd` validated clean and burned three play sessions.
Paste a working example and change the values.

**"Simple" means simple from the ENGINE's perspective, not fewest lines.** Prefer native MD actions
and script properties, a `<diff>` over a rewritten file, and the game's own events over polling.
Custom MD/Lua is a supplement, not a replacement.

→ **the `x4-xml-patching` skill** carries the analogue procedure, the four schema traps that cost
those sessions, and the packed-inclusive mod search that finds the modder-side analogue.
## Core Principle: Assume Existing Content Is REAL AND LIVE Until Proven Dead

**Anything present in a mod or script is there because it worked.** The burden of proof is on
"this is dead". The failure this prevents is pattern-matching a symptom onto a plausible story
("the new version removed this") and then deleting working content on it — the one action whose
damage is invisible in a clean validate run.

Before calling anything obsolete: **read the error literally** (it usually names the scope —
`Order 'MiningRoutine': Parameter 'stayinspace' was not expected` scopes to ONE order, and that
parameter is still valid in 9.0 elsewhere); **grep `reference\` for live uses** (any vanilla or
DLC use means your call is wrong); **date the change** (a thing missing from an old file is
usually old, not new); and **check who else references it**.

**Destructive changes — `<remove>`, deleting files, stripping attributes, "cleanup" — require
explicit user approval**, a higher evidence bar than additive ones, and a snapshot first. Prefer
additive repairs: restoring an orphaned index entry is reversible and provably scoped; deleting a
reference is not.

## Core Principle: Cognitive Co-Pilot, Not Order-Taker

On every task, ask: **"what else is wrong here that nobody asked about?"** — and surface it.
Find related issues, challenge assumptions, suggest what the user hasn't thought of. Treat the
user's examples as a SAMPLE, not the spec — enumerate the broader class yourself, and flag
scope-expanding *actions* before taking them.

## Knowledgebase (Standing Instruction)

`KNOWLEDGEBASE.md` is the master reference for discovered quirks, XML schema patterns,
cross-file dependency maps, the version migration map, and tool notes. **Consult it before
making changes.** After every session, bug, or research task, extract new facts and add them.
The environment gets smarter the more you use it. In a git repo, commit each edited
knowledgebase/notes file alone (explicit path); never push.

## x4live is EXPERIMENTAL — say so before you use it

**Before running any `x4live` command against the user's game, tell them it is experimental and
recommend a throwaway save** — unprompted. Only `pause`/`unpause` write, and only when asked;
its answers are EVIDENCE, not truth (they have been wrong). → the **`x4-live`** skill has the
detail and the traps.

## Nexus Mod Research (Standing Rule)

**Always search a mod's Nexus page before investigating or editing it** — description, articles,
changelogs, comments, bug reports. Most issues have been seen by another user already.

**★ API-FIRST: reach Nexus ONLY through the API, NEVER by scraping** (pages 403 automated
fetches; Steam pages are scrapeable, Nexus is not). Each user supplies their OWN key in
`X4_NEXUS_KEY` (or `x4-paths.env`): never bundle, commit or log one.

→ endpoints, the rate budget, the local-first resolution cascade and how to get a key live in
`x4validate/_nexus.py`, next to the code that calls them; **`x4-modlist-review`** drives the triage.
## Modding Workflow (Per Change)

1. **Research first:** the mod's Nexus page, any mod touching the same files, and the vanilla
   structure in `reference\`. Name EVERY file the change must touch before writing one.
2. **Implement:** a diff patch for existing content, a complete file only for new content,
   mirroring the game's folder structure inside `dev\{mod_name}\`.
3. **Validate, then test:** `x4validate` before any in-game cycle, deploy to the game-root
   `extensions\`, then read `debug.txt`.

## Core Principle: PROVE IT RAN BEFORE DEBUGGING WHAT IT DID (Mandatory)

**A change that produces no output has THREE indistinguishable causes, eliminated in this
order:** it did not load · it loaded but never triggered · it triggered and the logic
failed. Debugging the third while the first or second is true is unbounded, because every
observation is consistent with every theory. One such harness cost **three play sessions**,
none of them spent on the experiment.

1. **Did it LOAD?** A signature line in `debug.txt` naming the file. Absent = not installed
   or not enabled, and nothing else matters.
2. **Did it TRIGGER?** An unconditional marker as the FIRST action, before any logic. X4
   specifically: a top-level cue with **no `<conditions>`** fires on new game AND save load,
   while `event_game_loaded` fires on a SAVE LOAD only — which silently wasted one of
   those three sessions.
3. **Only then** debug what it did.

⚠ **Static validation cannot see runtime wiring.** `x4validate --update` returned `OK: no
issues found` on a script the engine rejected three times: cue-trigger semantics and order
signatures (`Attack` takes `primarytarget`, not `target`) are not expressible in a schema.
**For a new script the engine log is the FIRST real check, not the last.** And instrument
where the USER is looking — `debug.txt` is invisible during play, so an in-game harness
reports through `<show_notification>` AND `<write_to_logbook>`, and states elapsed time from
a landmark they can SEE, never from process start.

## Core Principle: Evidence Must Match the Scope of the Claim (Mandatory)

**The user must never have to ask "did you actually verify that?" If they have to ask, the process
already failed.** Two rules, both non-negotiable.

### 1. The scope of your evidence must match the scope of your claim

| Evidence you have | The ONLY claim it supports |
|---|---|
| You read **one file** | *"This file does X."* |
| You read **one mod** | *"This mod does X."* |
| You measured **the corpus, with a denominator** | *"X is how it works"* / *"N of M do X"* |

**One file NEVER supports a statement about the engine, the schema, or "how it works now."** A
real engine change shows up across the whole corpus, and checking the corpus is one query. From
ONE macro file carrying `<explosiondamage value=...>` with no `@shield` I once asserted that 9.0
had consolidated the attribute — into the knowledgebase and an upstream report. Measured
afterwards: **488 of 610** occurrences still carry `@shield`, and **183 of 185** among ship
macros. It was a mod-vs-mod convention mismatch, and the query that would have caught it took
seconds. **A surprising observation is a QUESTION, not an ANSWER.**

### 1b. An AGGREGATE can hide the very thing you are measuring — compare PER ITEM

A performance run over 115 mods totalled **594.4 s → 595.7 s = 1.00x**, clean by any reading,
while two mods had gone **2.8 s → 112 s (39x)** and **2.4 s → 121 s (51x)** — hidden because a
third got faster and cancelled them out. Whenever you compare two states — timings, counts,
findings, collision rows — **diff the ITEMS, not the totals**, and quote the aggregate only as
context. The same holds for a COUNT of findings: "42 added, 0 removed" is reassuring only once
every one of the 42 is attributed to an intended cause.

### 2. Label the evidence tier — in prose and in permanent record

- **MEASURED** — "I ran X; 488 of 610." State the number and the denominator.
- **READ** — "`reference\md\foo.xml:942` has this node." Cite file and line.
- **INFERRED** — **must** be hedged out loud: *"I think"*, *"this looks like"*, *"unverified, but"*.
- **ASSUMED** — say so, and say what would confirm it.

**Never write an INFERRED claim into permanent record in the grammar of a fact.** Permanent record
has no tone of voice: the next session reads a confident sentence as measured truth and builds on
it. *"`hunterpack_small_turrets` is the foundation dependency of the hunterpack ship family"* was
an inference written as a fact, then re-quoted as a fact later; measured, it defines 22 S-turret
macros that the ships reference **zero** of. If verifying is too expensive right now, hedge it and
flag it as open — what is never fine is an unverified claim wearing the grammar of a verified one.

## Discovery vs. Proof (Standing Rule — which tool answers which question)

**Route BEFORE you search:** ask *"which tool answers THIS question?"* before a search command,
not after a confusing result. Reaching for `grep`/`find` by reflex when a purpose-built tool
exists is a recurring, expensive failure; this governs which tool you REACH FOR, not only what you
may claim. Flags and subcommands: the generated `x4-cli-reference` skill.

| Question shape | Tool | Not this |
|---|---|---|
| values of attribute X across the corpus? | **BaseX** (`tools\basex\ask.py`) | `grep -r` over `reference\` (60 GB) |
| who references / calls / listens to X? | **BaseX**, or **x4xref** for MD+aiscript cues | recursive grep |
| the LIVE value, and which mod set it? | **x4effective** | `reference\` (vanilla only) |
| do this mod's selectors resolve? | **x4validate** (`--tier b` for cross-mod) | eyeballing the diff |
| **is my new MD/aiscript SCHEMA-valid?** | **`x4validate <mod> --update`**: the schema pass is GATED behind it (~102 s `md.xsd` compile); `--xsd-fast` skips it but loses "element not expected" (element-ORDERING errors) | a default run (it says `OK: no issues found` unchecked); hand-rolled lxml |
| **what did the ENGINE complain about?** | **x4debug triage** | `grep \| sort \| uniq -c` (cannot see what it dropped) |
| **did we PREDICT what the engine hit?** | **x4debug crosscheck** | comparing TOTALS (different populations) |
| collides with the modlist / who wins? | **x4compat** | guessing at load order |
| near-duplicate of a ship I own? | **x4similar** | — |
| **does this vpath exist LIVE, and WHO supplies it?** | **`x4effective dump --chain <vpath>`** (rc 0 names every source, rc 1 absent) | `_effective.base_has` (base+DLC only: a mod's file reads ABSENT) |
| installed / updates / abandoned? | **x4modlist** | the profile `content.xml` (a DECISION LOG) |
| a mod's numbers vs the live tree? | **x4stats** (advisory, never a verdict) | a vanilla number quoted as effective |
| what changed between two mod versions? | **x4diff** | `diff -rq` (line endings swamp it) |
| what a savegame bakes in and references? | **x4save** | assuming dangling refs (the engine deletes them silently) |
| the RUNNING game's state? | **x4live** (needs the game running) | inferring it from files on disk |
| **does a file with this NAME exist?** | a file-NAME search (glob/find) | a CONTENT search (a file need not contain its own name) |
| find text in one known area | a CONTENT search (ripgrep) | `grep -r` via the shell |

★ **A negative from ONE invocation MODE is about that mode, not the tool:** a gated check and a
missing check look identical (x4validate's schema pass, above), so check for a FLAG before
saying the tool can't. **Never state a negative from a tool that cannot see the whole picture.**
*Discovery* (where X appears, what values exist) is **BaseX**: fast, packed mods included, but
`x4raw` is files as written, with no diffs or load order; `x4eff` is the merged tree. *Proof*
(what the game actually sees) is **x4validate / x4effective**: packed `.cat`, diffs in load
order, the merged tree.

**A BaseX negative is admissible — but only with a denominator.** A **bare** "0 hits" is only a
lead; `tools\basex\ask.py` refuses to render a zero as a finding unless `coverage-<db>.json`
says coverage is complete or accounted, printing *"NEGATIVE CONFIRMED over N of M documents"*
with every exclusion named. Prefer **`--db x4eff`** for any claim about what is LIVE. Load
order is MEASURED against the engine (F128). x4validate is the authority. A raw-query zero is
certified only in the grammar of `tools/basex/QUERIES.md` (or via `refs`/`attr`); others
return 4. That recognizer is no sandbox: run read-only XQuery only.

**Validate the DEPLOYED copy whenever load order could matter.** Tier B places an uninstalled
copy by its folder NAME, so a differently named dev folder lands elsewhere.

## Core Working Principle: Deductive Iteration — Work Backward from the Outcome

Never iterate blind. Before the FIRST attempt: state the outcome as **observable acceptance
criteria**; enumerate the **assumption chain** with a confidence **per link**, not one blended
number that hides the weak one; design tests that each confirm or kill a specific link,
cheapest-first and self-driven rather than by user playtesting; and **pre-commit a fallback for
every shaky link**, so a failed test advances the plan instead of starting a new guess.

**A test whose result would not change the next action is not a test.** Batch verification to
minimise user cycles, and when a symptom report contradicts the model, STOP and re-derive the
model from evidence — never re-tune parameters inside a broken model.

**The anti-pattern this kills:** attempt N motivated only by the failure of attempt N-1 —
parameter tweaks with no model of why THIS one reaches the outcome.

## Core Balance Principle: Three Values, and IN-SECTOR vs OUT-OF-SECTOR (Mandatory)

**Never quote a bare number.** For every value you propose changing state the **vanilla** value,
the **effective** one (what it is right now, with the winning mod NAMED, or "no override"), the
**proposed** one, and the **in-game effect** in plain terms. A selector written against the
*vanilla* value silently matches nothing when something else already changed it — the single
most expensive bug class here. Get the effective value from `x4effective` or `x4validate --tier b`,
never from `reference\` alone.

**Every combat or balance change is checked for whether it lands disproportionately in-sector (IS)
or out-of-sector (OOS); the goal is that the two feel as close to 1:1 as possible.** They diverge by
construction: OOS is pure arithmetic — a factor absent from the formula does not exist — while IS is
physics, travel time, turret traverse, point-defence, terrain, RNG and the player. So the danger is a
factor decisive IS and invisible OOS, or the reverse: when a change's cost is paid by a mechanic,
ask what pays it OOS. Often nothing does. ⚠ **Never assume OOS models a mechanic because IS
does** — verify against the OOS scripts; an assumed formula is an ASSUMED-tier claim.

**Label every combat claim IS, OOS, BOTH or UNKNOWN, in the sentence that carries the number.**

→ **the `x4-balance` skill** carries the regime table, the `<attention min=>` branch markers that
say which regime a formula belongs to, and the packed-inclusive scoping rule.
## Working ON the Toolkit

**Working ON the toolkit** — its code, gates, hooks, generator or `agent/` source — **load the `x4-toolkit-dev` skill first**: it holds the rules for derived-artifact freshness, narrowing steps, the bug funnel, concurrent sessions, and trusting tools before a modlist lock.

**A CLI banner saying UNKNOWN or STALE means the artifact may not describe your files: rebuild** (`uv run x4effective build` · `uv run x4xref build` · `cd tools/basex; bash build-corpus.sh; bash build-effective.sh`) before you trust a number from it. An absent fingerprint is UNKNOWN, never fresh.

**BaseX staleness is a maintenance task, never a reason to skip BaseX.** When freshness is stale or unknown, refresh the required database, verify its freshness and coverage, then use it. If refresh is blocked, resolve or explicitly report the blocker; never silently substitute an old result or a narrower search as current corpus evidence.

**Learned facts and preferences go in `KNOWLEDGEBASE.local.md`** as above. Also read an existing `X4-NOTES.md`; it remains preserved legacy evidence. Never edit {{GENERATED_FILES}}: they are regenerated and your edit is lost.

**`KNOWLEDGEBASE.md` and the other protected files are kept read-only by x4lock**, so a write to one fails until you unlock it: `python <toolkit>/scripts/x4lock.py unlock <file>`, edit, `python <toolkit>/scripts/x4lock.py lock`, then commit it. The guard prints the exact commands.

