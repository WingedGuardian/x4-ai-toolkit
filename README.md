# X4 AI Assistant Toolkit

An AI-assisted **X4: Foundations** modding environment for coding agents — **Claude Code and
OpenAI Codex** first-class, **OpenCode** best effort, and any other agent through
[`ADAPTING.md`](ADAPTING.md). It handles the tedious, error-prone work of modding — coordinated
multi-file XML edits, porting mods across game versions, validating diff patches, checking how a
mod interacts with everything else you've installed, triaging your mod list, and reading debug
logs — with safety guards, pre-loaded engine knowledge, and a bundled cross-file validator.

Built from hands-on X4 v9.0 mod development. **The agent is the brain; this is the
environment with the setup prework already done.**

> Independent fan project. Not affiliated with or endorsed by Egosoft, Anthropic or OpenAI.
> Ships **no** game data — you unpack your own legally owned copy locally.

> Until v4.0 this was the "X4 Foundations Claude Code Modding Toolkit" (repository `x4-claude-toolkit`);
> GitHub redirects the old address.

---

## What Is a Coding Agent?

A coding agent — [Claude Code](https://claude.ai/code) by Anthropic,
[Codex](https://github.com/openai/codex) by OpenAI, [OpenCode](https://opencode.ai), and
others — is an AI assistant that runs on your computer. Unlike a chat window, it can **read your
files, run commands, edit configs, and run scripts** — with your permission. For modding, that
means it can actually do the mechanical work: write the diff patches, trace the cross-file
fan-out, run the validator, and read the debug log back to you.

X4 modding is full of silent failure modes — a diff `sel=` that matches nothing, a forgotten
file in a multi-file change, a script attribute a game update made mandatory. This toolkit
ships with those footguns already documented and guarded against.

## Which agents, and how well each one is protected

Every agent gets the same knowledge and tools. What differs is how much of the toolkit's
**guarding** it can actually run, and the honest answer is not the same for each:

| | Claude Code | Codex | OpenCode (best effort) | Any other agent |
|---|---|---|---|---|
| Instructions | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` + `.opencode/X4-OPENCODE.md` | `AGENTS.md` (`--agent generic`) |
| Skills | `.claude/skills/` (+ two subagents) | `.agents/skills/` | `.opencode/skills/` | `.agents/skills/` |
| Command + file guards | hooks on Bash, PowerShell, Edit, Write, NotebookEdit; a guard that crashes **asks** | hooks, **only after you review them in `/hooks`** (silently off otherwise); a crash is turned into a deny unless the interpreter cannot start; seven disclosed gaps, listed under Setup | a plugin plus deny rules, **read from OpenCode's docs and source, not measured**; CLI only | **none**, until an adapter passes `x4guard conformance` (see [`ADAPTING.md`](ADAPTING.md)) |
| OS-level lock on `reference/` (`x4refguard`, once applied) | yes | yes | yes | yes |
| How to check it is live | `python scripts/x4doctor.py` | the `X4 GUARDS LIVE (codex hooks v1)` session banner + `x4doctor --agent codex` | the `X4 GUARDS LIVE` system-prompt line + `x4doctor --agent opencode` | -- |

Codex on Linux/macOS and the OS lock on Linux/macOS are best effort and not device-tested.
The per-agent details are under [Setup](#setup): *What each class of agent gets*, *What the
Codex hooks cannot see*, and *OpenCode: best effort*.

---

## What You Get

### The X4-specific knowledge, pre-loaded
- **`KNOWLEDGEBASE.md`** — XML schema patterns, the diff-patch idioms, the **extension
  merge/load-order model** (what overrides vs unions), the **7.x→9.0 version migration map**
  (the `space=` requirement, the dead Lua_Loader, Protected UI Mode), a **mechanics interlock
  map** for reasoning about balance ripples, and tool notes. The instruction file (which IS
  loaded automatically) tells the agent to consult it before making changes.
- **`CLAUDE.md`** (Claude Code) / **`AGENTS.md`** (Codex, OpenCode and other agents) — the
  workflow: diff-patch-first, confidence levels (the agent rates 0–100% and
  lists assumptions before any change), "vanilla as frame of reference," native-engine-solutions
  first, and a cognitive-co-pilot stance (surfaces what you *didn't* ask about).

### x4validate ⭐ — the bundled cross-file validator
The flagship tool. X4's hardest bugs come from a change that fans out across many files that
must cross-reference each other correctly. No off-the-shelf tool reproduces X4's *effective
merged tree* (base + all DLC + enabled mods) plus its typed cross-reference graph — so this one
was built (Python / lxml). It checks:
- **Every diff `sel=` resolves** against the real merged tree — catches the silent no-op.
- **References resolve** — ware / macro / `{page,t}` the mod introduces point at real definitions.
- **Completeness** — a new ware/ship/module's footprint vs a vanilla analogue ("did I forget a spot?").

It also ships **`x4modlist`** (mod-registry triage via the Nexus API) and an **XSD-based
7.x→9.0 migration checker**.

```bash
cd tools/x4validate
uv run x4validate --paths                  # where did it resolve everything? read this first
uv run x4validate --tier b /path/to/mymod  # validate against your installed modlist
```

**`x4validate --paths`** prints where it resolved the game, reference, profile and registry, and
which config file it read. Run it first whenever a result looks impossible — silent
misconfiguration is this tool's worst failure mode, because "found nothing" and "looked in the
wrong place" otherwise print the same way. Full configuration model:
[`tools/x4validate/README.md`](tools/x4validate/README.md#configuration--where-it-looks-for-things).

**`--tier b`** merges your *active* extension set (what the engine loads) in load order, so cross-mod patches resolve
for real — and it catches the failure that looks like success: content **another mod removed**.
(Dogfooding case: one mod `<remove>`s a vanilla macro from `index/macros.xml` and never re-adds
it, orphaning it for six other mods — 415 engine errors that base+DLC validation reports as fine.)

x4validate models three X4 patch rules a naive XML merge gets wrong, each of which otherwise
produces a silent no-op or a false alarm:
- **`sel=` must match exactly one node** (RFC 5261). On multiple matches X4 logs
  `Multiple matching nodes ... Skipping node` and applies **nothing** — the patch validates clean
  and does nothing. 236 such ops were being skipped across one real modlist.
- **`if=` guards** gate an op before `sel=` is evaluated, so a guarded no-op is by design, not an error.
- **`extensions/<target>/<rel>`** paths are owned by `<target>`, not the base game.

> **A companion for your editor (third-party, new, not part of this toolkit):**
> [X4CodeSense](https://www.nexusmods.com/x4foundations/mods/2422) by ChemODun (Apache-2.0, v1.0.0
> released 2026-10-05) is a VS Code / Open VSX extension with live schema, expression and
> reference checks for MD, aiscript and diff XML as you type; it also has a CLI
> (`x4-script-check`) and an MCP server. It complements x4validate; nothing here uses or needs it.

### `x4effective` — see every final value, and who set it
An "xEdit for X4": the effective value of every ware/macro/job across base + DLC + all your mods,
with **per-attribute provenance** (`base → modA replace-attr:12 → modB`), in a SQLite store you can
query directly. `show` gives the full record view, `attr` reads one column across everything
("all missiles and their damage"), `who-sets` gives the chain (and, for a whole entity, which origins changed its properties), `diff-mod` shows everything a
mod wins, and `dump` prints the live merged XML for any path.

**`x4diff`** does a semantic XML diff between two versions of a mod, with multi-baseline support —
comparing attributes and element text (shown as `@text()`, pretty-print whitespace ignored), built for separating *your* edits from the author's when recovering personal modifications.

Give it `--base` and it becomes a **three-way** diff, which answers the question a two-way one
cannot: *of these changes, which are the author's and which are drift that upstream has made since?*
Two-way, an archived mod against the current release looks like hundreds of edits — measured on one
real 2021 mod, **~440 attribute deltas, of which 15 were the author's**; 340 were upstream's own
work and **124 of 135 documents were verbatim copies of the baseline**. Porting from that number
means re-applying someone else's changes as if they were yours, and reverting the current release
across most of the tree.

With a common ancestor each attribute lands in exactly one bucket — author edit, upstream drift,
converged, or **BOTH-MOVED**, the only rows that are actually a decision. Documents the baseline
does not contain are *named and excluded*, never guessed at: an attribute present today and absent
in an old file is upstream **addition** far more often than author deletion, and a two-way diff
cannot tell those apart. Exit 1 when there is a decision to make, 0 when the port is mechanical.

### The cross-mod interaction suite — how does a mod behave against everything else installed?
Validating a mod in isolation isn't the same question as "how does this play with my other 40
mods?" No published tool answers that for X4 — the interaction suite does, reading packed mods
(e.g. VRO) as well as loose ones:
- **`x4compat`** — collision detection over the *effective* tree: two mods editing the same node
  (one silently loses), defining the same registry id, or fully overriding the same file. A
  candidate mode answers "what would break if I added this mod?" before you install it.
- **`x4xref`** — a who-calls/who-listens/cue index over every MD/aiscript in base+DLC+your mods.
  Answers "does anything else react to this event/action?" in one query — the kind of question
  that otherwise takes many rounds of grepping for tokens that share no keyword with what you're
  actually asking about.
- **`x4stats`** — advisory: how does a mod's ware/weapon pricing compare to everything else in
  *your* effective game (including an installed overhaul's rescaled values)? Grounds a balance
  discussion; doesn't settle one.
- **`x4similar`** — advisory: flags a mod's ship as a likely near-duplicate of one you already
  have under a different name (e.g. two mods independently adding "the same" ship).

The `/x4-mod-interaction` skill ties all four together into one interaction brief.

### BaseX corpus search — ask questions across every file at once

New in **v2.6.0**, and *optional*: the tools above answer questions about a mod. This answers
questions about the whole corpus — 36M nodes across the base game, every DLC and every installed
mod — in about a second.

```bash
cd tools/basex
bash build-corpus.sh        # one-off index of every file AS WRITTEN
bash build-effective.sh     # one-off index of the merged, live tree
uv run python ask.py attr drag          # every value this attribute takes
uv run python ask.py refs turret_x --db x4eff   # who references it, in the LIVE tree
```

Use it for the questions a grep cannot answer honestly: *what values does this attribute take across
the corpus?* · *who references this macro?* · *which mods define a ware nobody produces?*

**The reason it is worth a JVM: it can back a negative.** A recursive grep that finds nothing tells
you nothing — it cannot distinguish "this does not exist" from "I did not look everywhere", and 62%
of mod XML is inside packed archives a grep never opens. `ask.py` refuses to print a zero as a
finding unless it can state its denominator, and then says *"NEGATIVE CONFIRMED over N of M
documents"* with every exclusion named. It also refuses to answer at all from a stale index rather
than serving you a confidently wrong number.

**Be honest with yourself about the cost before you start:** Java 17+, roughly 3 GB of disk, and a
build measured in minutes, not seconds. BaseX itself is bundled (BSD-3-Clause, 5.2 MB) so there is
nothing extra to download. See [`tools/basex/README.md`](tools/basex/README.md) for the install, the
freshness contract and the exit codes, and `tools/basex/QUERIES.md` for worked queries.

### `x4live` — ask the RUNNING game, instead of quitting to read a log

> ### ⚠ EXPERIMENTAL — use a throwaway save, not one you care about
>
> `x4live` is the least settled thing in this toolkit. **Do not use it on a save you
> are actually playing.** Make a fresh save, or a copy, and use that.
>
> Being precise about why, because an overstated warning gets ignored: **it changes one
> thing, and only when you ask.** `x4live pause` and `x4live unpause` pause and unpause
> the running game; every other verb only reads. Nothing here changes a ship, a station,
> the economy or a savegame, and the addon declares `save="false"` so it cannot bake
> into a save. The reasons to be careful are narrower and real:
>
> * `pause` and `unpause` change the game you have open. `unpause` only undoes a pause
>   this channel made, and a UI reload (alt-enter, loading a save) forgets that, so such
>   a pause is then undone in game;
> * it loads a mod into your running game, and the snapshot probe runs automatically
>   at load;
> * that probe calls `SaveUIUserData()` — it writes your profile's **UI userdata**,
>   which is not your save but is not nothing either;
> * **its answers have been wrong.** Until 2026-09-02 a failed engine call was reported
>   as "no such macro" rather than as an error, and answers like that feed groundtruth
>   fixtures. Treat what it tells you as evidence, not as truth;
> * removing any mod from a save lets the engine silently delete the content that mod
>   owned — no dialog, and usually no error line.
>
> None of that is a reason to avoid it on a scratch save, which is what it is for.

Every other tool here reads files. `x4live` reads the **live engine**: what objects exist right
now, what a macro's real values resolved to, whether an extension actually loaded — without a
quit-and-relaunch cycle for each question.

It needs one thing inside the game, and **this toolkit ships it**: `mods/x4_toolkit_helper/`.
Copy that folder into `{game}/extensions/`, or from `tools/x4validate` run
`uv run python scripts/deploy-mod.py x4_toolkit_helper --apply`, which refuses a wrong target
and re-reads what it wrote. It answers a fixed, enumerated vocabulary: read
verbs, plus exactly two write verbs, `pause` and `unpause`, which take no arguments and report
the pause state the engine reads back. It declares `save="false"` so it cannot bake into a save,
and it adds no content, no menu and no MD script. Its one third-party dependency
(**Mod Support APIs**, `ws_2042901274`, from Steam Workshop or Nexus) is declared **optional**: the
addon still loads without it and the snapshot half keeps working, while only the named-pipe verbs
report the api unavailable. The two halves fail independently, deliberately.

⚠ **Enumeration is not a census, and the tool says so rather than rounding it away.** Ownerless
objects belong to no faction and are invisible to any owner query, hidden factions are opt-in, and
name and sector are *player knowledge* — so many rows come back `Unknown` while ids, positions,
class and flags stay exact. Object ids also do not survive a UI reload, which the engine performs on
a save load and on an alt-enter graphics change.

See [`tools/x4validate/README.md`](tools/x4validate/README.md) for the verb list and the evidence
tiers behind each answer.

### Skills & subagents
- `/x4-balance` — ground a stat or balance change in measured values before proposing it:
  the three-values rule (vanilla, effective, proposed) and the in-sector vs out-of-sector check.
- `/x4-cli-reference` — the exact subcommands, flags and defaults of every toolkit CLI,
  GENERATED from each CLI's own `--help` by `tools/x4validate/scripts/gen-cli-reference.py`
  so it cannot drift; the suite fails if it is stale.
- `/x4-debug` — read the active profile's `debug.txt`, filter benign noise, surface real errors.
- `/x4-live` — query the RUNNING game over the live channel, and read a refused id, a short
  count or a zero correctly (the traps a live enumeration hides).
- `/x4-mod-interaction` — analyze how a mod interacts with your installed set: collisions,
  shared event/action hooks, advisory balance fit, and same-ship redundancy.
- `/x4-mod-release-review` — before publishing a new version of your own mod: review only what
  changed since the last published version, change nothing while reviewing, and bring every finding
  to you, one suspected finding at a time, before any fix.
- `/x4-modlist-review` — triage your mod registry against the Nexus API.
- `/x4-probe` — build a temporary in-game instrument (a test harness or diagnostic mod), and
  work out which of "did not load / never fired / logic failed" produced a silent result.
- `/x4-scaffold` — scaffold the full cross-file footprint for new content from a vanilla analogue.
- `/x4-toolkit-dev` — for working ON the toolkit itself (its code, gates, hooks, generator or
  `agent/` source): derived-artifact freshness, narrowing steps, the bug funnel, concurrent
  sessions. A player or modder using the tools does not need it.
- `/x4-update-mod` — port a mod to a newer game version (mechanical checks + design brief).
- `/x4-xml-patching` — the selector, merge-tree and load-order gotchas that make an X4 patch
  silently no-op, where a fix belongs, and how to validate it; invoke before the first XML edit.
- `cross-file-impact` / `mod-research` subagents (Claude Code only) — trace the fan-out / research a mod before editing.

The `/name` form is Claude Code's. The same skills are installed for Codex and generic agents
in `.agents/skills/` and for OpenCode in `.opencode/skills/`; ask for one by name.

### Safety, built in
- **Command + file guards** (as Claude Code runs them; Codex and OpenCode reach the same guards
  through an adapter, see the agent table above) — block writes to `reference\` and direct `.cat`/`.dat` edits; confirm non-save profile changes. Recognized mutations of one or two saves first get permanent verified preservation; bulk save access needs bound human approval. A mod manifest is ADVISED rather than confirmed
  (a deliberate 2026-08-29 choice: the note is worth having, the interruption is not).
  They cover every tool that can change a file: Bash, **PowerShell** (parsed by PowerShell's
  own parser and judged by the same rules as Bash — it needs `pwsh` or Windows PowerShell,
  and asks rather than guesses without one), Edit, Write and NotebookEdit.
- **Auto-backup** — every edited file is copied to `.claude\backups\` with an audit log.
- **Confidence system** — no guessing; the agent rates confidence and lists assumptions first.
- **Baseline capture** — `scripts/generate-baseline.sh` records a known-good snapshot (game version, installed-mod hashes, a normalized debug.txt error fingerprint) to diff against later.
- **Loss canary** — `python scripts/x4canary.py` refuses if a tracked file in a watched
  repository has been DELETED, EMPTIED or lost more than half its bytes. It runs
  automatically at session start, so this is the one script here that can interrupt you
  without being asked for: a `*** DATA LOSS ***` banner naming the file and the
  `git checkout` that recovers it. It exists because a mod registry went from 196,363
  bytes to 46 and nobody noticed for six hours — `git status` had shown it immediately.
  "Could not check" is exit 2 and never reported as loss.
- **Write lock** — `python scripts/x4lock.py status | lock | unlock` marks the files you
  cannot afford to lose by accident (the hooks, the skills, `CLAUDE.md`,
  `KNOWLEDGEBASE.md`, your paths config) read-only, so a stray write fails loudly
  instead of succeeding quietly. Unlock, edit, relock. Run from a linked git worktree,
  it checks the main checkout's paths config (and `$X4_TOOLKIT`'s, when set) instead of the
  worktree's own, which a worktree never has, and names what it checked.

  > ⚠ **Unlock before re-running the installer, then lock again.** The installer will
  > not write over a read-only file: it REFUSES up front, names the files it would
  > have overwritten -- the first 8, then a count of the rest -- and changes
  > nothing. That is `x4lock` working — it cannot tell
  > an installer from any other process. An upgrade that does not need to change a
  > locked file does not touch it, so this only comes up when something you locked
  > has genuinely changed upstream.
- **Reference lock (Layer 2)** — `python scripts/x4refguard.py status [--json] [--full] | apply | remove`
  protects the unpacked `reference/` tree at the OS level against ordinary processes,
  independently of hooks, with the privileged-access exceptions below. **`apply` and `remove` show the target folder and count the
  files under it (with progress), then ask you to confirm**; `--yes` confirms up front, and
  without a terminal to ask on they refuse rather than act (`--yes` is how an agent runs it
  once you have agreed). It is not instant: about 8 s per 100,000 files was measured on
  scratch trees, and one apply over a full ~510,000-file tree was still running after 2
  minutes, so a "still applying" line appears every 10 s. Like every script here it acts for the toolkit it lives in: if
  `X4_TOOLKIT` names a different toolkit it says so and `apply`/`remove` refuse until you
  pass `--toolkit <folder>`. `bin/unpack-reference.sh` applies it after a verified unpack.
  The installers do not apply it (it is an ACL change, yours to make), so a `reference/`
  unpacked before 4.0 stays unprotected until you run `apply`; `x4doctor`'s
  `layer2.reference` row shows whether it is on. `apply` acts only on a FINISHED unpack: one
  carrying the `.unpacked-and-locked` sentinel, which `bin/unpack-reference.sh` writes as its
  last step (every release since 1.0), so a tree the toolkit unpacked before 4.0 qualifies. A
  tree without it (unpacked by hand, or unfinished) is refused, and the refusal prints both
  ways out: unpack it with the toolkit, or, only for a complete unpack you made yourself, the
  one-line command (bash or PowerShell) that writes the sentinel.
  - **Windows:** one inherited deny for your own account,
    `(OI)(CI)(DE,DC,WD,AD)` (mask 65606). It blocks deleting, renaming, overwriting,
    appending and creating anything inside the tree. Reads and copies OUT of it still
    work. Measured on scratch trees: 15 of 15 delete/rename and 20 of 20 write
    primitives blocked, 12 of 12 reads unaffected, about 8 s per 100,000 files.
  - **Linux/macOS: best effort, not device-tested.** Linux uses `chattr +i` when run as
    root, otherwise `chmod a-w` on every directory and file. macOS uses `chflags uchg`,
    otherwise the same `chmod`. `status` names the mechanism it found, what it stops and
    what it does not. It never reports "protected" for something it could not confirm.
  - **What it does not stop:** renaming the `reference/` folder itself (its parent allows
    that, and `status` then reports the root missing); you lifting it on purpose; an
    Administrator or root. Measured on elevated hosted Windows: Git Bash changed
    protected scratch trees in three of three probes when backup/restore privileges
    were available; the same three probes were blocked when disposable children
    removed both privileges. Disabled privileges can still be available to a process.
  - **To re-unpack after a game update**, lift it first. Each step is yours to take; an agent should not take it on its own:
    1. `python scripts/x4refguard.py remove` (it asks you to confirm; `--yes` skips the question)
    2. root moved since? `python scripts/x4refguard.py remove --path <old root>` (only
       accepted on a folder that carries this tool's exact protection)
    3. tool broken? from **cmd.exe**: `icacls "<root>" /remove:d *<your SID>`
       (`whoami /user` prints the SID); Linux `sudo chattr -R -i` / `chmod -R u+w`;
       macOS `chflags -R nouchg`
    4. last resort, elevated: `icacls "<root>" /reset /T /C`

    Then `rm reference/.unpacked-and-locked` and run `bin/unpack-reference.sh`.
- **Recovery** — `bash scripts/restore-from-backup.sh` LISTS the timestamped
  auto-backup trail above; restoring is a second, deliberate step:
  `RESTORE=1 bash scripts/restore-from-backup.sh <backup-filename> <dest-path>`.
- **The guards are tested** — `bash scripts/test-hooks.sh` feeds every hook synthetic tool-call JSON and asserts the decision it returns, across both the in-game and separate layouts. `python .claude/hooks/test_hook_facts.py` adds unit tests over the command parser in well under a second. Edit `agent/guards/claude-hooks/`, regenerate, then run both checks. This exists because a silent guard is worse than no guard: several hooks were inert for entire releases and code review never caught it. Coverage is *verified* rather than claimed: `python scripts/verify-hook-tests.py` plants a specific defect and requires the **named** test for it to go red, then pins each rule true and false in turn and requires a must-fire / must-not-fire test to break each way. A suite that cannot go red is decoration, and several guards here were inert for entire releases while their suite was green.

### Framework checks and maintenance

Edit hook sources in `agent/guards/claude-hooks/`, then regenerate from `tools/x4validate`
with `uv run python scripts/gen-agent-trees.py`. Run the hook suites against the generated
copy. Do not edit `.claude/hooks/` directly.

`python .claude/hooks/x4guard.py check` lets another agent request a verdict without taking
backups or executing its command. Supply `--kind shell --shell bash|powershell --command ...`
or `--kind write|delete --path ...`. A delete check applies both file protection and deletion
confirmation; the stricter verdict wins. An evaluation failure is `deny` with `inert: true`.
An ordinary `ask` requires approval. Neither exit 0 nor a passing check installs enforcement
in an agent host. On Windows the guards find Git Bash themselves: `X4_BASH` if set, then
Git for Windows' standard install locations, then `PATH` walked past the WSL stub. Set
`X4_BASH` only when Git Bash is installed somewhere else (see Prerequisites).
The current wrapper resolves relative file paths from the caller's working directory before
checking them. One time budget covers the whole check (`X4_GUARD_TIMEOUT_S`, default 25 s),
shared by a delete's two guards; a check that runs out is an inert deny. The Codex hook path
uses its own budget instead (`X4_CODEX_BUDGET_S`, 45 s).

**`python scripts/x4doctor.py [--root DIR] [--agent NAME] [--json] [--toolkit DIR]` -- are the guards live
here?** A read-only health check, per installed agent target. It runs on Python 3.10 with no
dependencies, so a broken `uv` cannot take it down. It reports:
- the bash, python and jq the guards actually resolve, each one executed;
- the reference and game roots the guards see, compared with the ones the tools see;
- deployed-vs-source parity;
- a self-test through `x4guard check`, with controls that must deny and controls that must
  allow, so a guard that denies everything cannot pass;
- Claude's hook wiring and `disableAllHooks`;
- Codex's project trust and per-hook review state;
- OpenCode's plugin, adapter and rendered deny rules (in place and current, never "loaded");
- `X4_GUARD`, the OS-level `reference\` protection and the x4lock state.

Every row is OK, FAIL, UNKNOWN, TODO or N/A, and a check that cannot answer says UNKNOWN.
**TODO means "installed correctly; YOUR step is pending"** -- trusting the folder in Codex,
approving its hooks in `/hooks`, unpacking `reference/` when it is not there yet
(`bin/unpack-reference.sh`, which then applies the OS protection), applying the `reference/` OS
protection to a tree you already have (`python scripts/x4refguard.py apply`): steps no installer
may take for you. The `reference/` rows print their command by absolute path, ready to paste. Exit codes:
0 all OK; 1 any FAIL; 3 UNKNOWN without FAIL; **4 only TODO rows pending** (a fresh install,
before your steps); 2 nothing checked. Precedence is FAIL > UNKNOWN > TODO, so a pending step
never hides a defect, and a run that checked nothing never exits 0. It acts for the toolkit it
lives in (`--toolkit DIR` to choose another) and says so when `X4_TOOLKIT` names a different one.

These hooks inspect known command forms; they do not sandbox arbitrary interpreter programs.
The loss canary detects file loss. Persistent process receipts are available through
`python tools/agent-harness.py coverage --session-id ID`; they describe checks that reached
the toolkit and do not prove host interception. Independent `reference/` filesystem
protection is available through `x4refguard`, once explicitly applied and verified.

> The rest of `scripts/` is maintainer tooling that ships because the bundle is the
> repository: `fuzz-guard.py` and `verify-hook-tests.py` prove the guards can fail,
> `scan-identifiers.py` is a CI gate, `audit-coverage.py` is a local coverage-ledger
> check, `build-release.sh` builds this bundle from a git tag, and `gitbash.py`
> resolves a real bash on Windows for the scripts and tests that need one.
> None of them is needed to use the toolkit.

---

## Setup

### 1. Install an AI agent
- **Claude Code** — subscribe to Claude (Pro/Max), then install the desktop app from
  [claude.ai/code](https://claude.ai/code), or the CLI: install [Node.js](https://nodejs.org/) and
  run `npm install -g @anthropic-ai/claude-code`.
- **Codex** — install OpenAI's Codex CLI ([github.com/openai/codex](https://github.com/openai/codex)).
- **OpenCode** — the CLI only (the desktop app is not supported; see below).
- **Anything else** — install it the way its maker says, then read [`ADAPTING.md`](ADAPTING.md):
  the toolkit's guards do not protect an agent it has no adapter for.

### 2. Get the toolkit and run the installer
Download the latest release zip (from [Releases](https://github.com/WingedGuardian/x4-ai-toolkit/releases)
or Nexus) and extract it, then run the guided installer. **On Windows, pick a SHORT folder**
(e.g. `C:\X4AI`): Windows' classic 260-character path limit is reached once the folder's own
path is longer than about 165 characters, because the deepest file in the toolkit sits 92
characters below its root -- extracting under a long Downloads or OneDrive path can fail for
the deepest files (a 2026-10-04 install test hit this with 2 test fixtures).

```bash
bash install.sh          # Linux / macOS / Windows (Git Bash)
```
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1    # Windows PowerShell
```

(The explicit `-ExecutionPolicy Bypass` form is given because a stock Windows install ships
with scripts disabled — a bare `.\install.ps1` would be refused before it ran anything.)

It asks which **layout** you want (see the table below), auto-detects your game, profile and
XRCatTool, and writes the result to `<toolkit>/x4-paths.env`. Nothing is hardcoded.

> **Upgrading from 3.x:** 3.x kept the config at `<toolkit>/.claude/x4-paths.env`. 4.0 still
> reads it there (deprecated, with a notice), and the installers and
> `python scripts/x4config.py migrate --apply` MOVE it to the toolkit root (a read-only,
> x4lock'd file moves too and stays locked). Two copies that differ are refused, never merged.
> To go back to 3.x, move the file back.

> **Which layout?** If you're unsure, pick **separate** — it keeps the game folder untouched and
> avoids needing write access to `C:\Program Files`. Pick **in-game** only if you want the
> single-folder model. Pick **global** if you have several mod repos.

> Contributing or just reading the source? Clone it standalone instead:
> `git clone https://github.com/WingedGuardian/x4-ai-toolkit.git`

### 3. Open your AI agent in the toolkit folder and paste the setup prompt
Paste the contents of `SETUP_PROMPT.txt` into Claude Code, Codex or any other agent. The agent
says which agent it is and checks whether the toolkit has an adapter for it, runs
`bash setup.sh`, checks prerequisites (bash, jq, uv/Python 3.13), wires up x4validate, runs
`x4doctor` to report which guards are live, and walks you through unpacking your own
`reference/` and (optionally) adding your Nexus API key.
To run x4validate's test suite yourself, from the toolkit folder: in Git Bash, Linux or macOS
`cd tools/x4validate && uv run pytest -q`; in Windows PowerShell (5.1 rejects `&&`)
`cd tools/x4validate; uv run pytest -q`. Passing means **0 failed and 0 errors**; skips are
expected. What was measured (2026-10-05, on Windows: a fresh `--agent all` install by each
installer, separate method, `--no-env` / `-NoEnv`, then the full installed suite): the `install.ps1` install passed with 4,018 passed, 377 skipped, 0 failed;
the `install.sh` install ended 4,017 passed, 366 skipped, 6 failed, from three causes that were
then fixed and re-run individually (no second full run; BLIND-SPOTS F161). Installs with fewer
agents were not measured. Each skip names its reason: an installed toolkit does not carry the repository-only
`agent/` source, so the tests that read it skip as `REPO-ONLY`. Answer any questions it asks. An agent
with no adapter is pointed at [`ADAPTING.md`](ADAPTING.md), which shows how to build and prove
one; until it has one, the toolkit's guards do not protect it.

### Prerequisites it will check for
- **bash** — required. Every safety hook and both setup scripts run under it. Linux/macOS have it;
  on **Windows install [Git for Windows](https://git-scm.com/download/win)** (Git Bash) — without
  it the hooks silently do nothing, so the safety guards below would not be active. **Git Bash
  must be FOUND**, and a stock Windows `PATH` does not find it: Git for Windows adds only
  `<Git>\cmd` to `PATH`, and `bash` there is the WSL stub in `System32`, which cannot run the
  toolkit. The toolkit looks in the standard Git for Windows locations (`Program Files`,
  `Program Files (x86)`, `%LOCALAPPDATA%\Programs`) and then along `PATH` past the stub. If Git
  lives anywhere else, set `X4_BASH` (new shells only):
  `setx X4_BASH "C:\Program Files\Git\bin\bash.exe"` (with your path), or put Git's `bin`
  folder ahead of `System32` on `PATH`. `python scripts/x4doctor.py` shows which bash was found.
- **jq** — Windows `winget install jqlang.jq` · Linux `sudo pacman -S jq` / `apt install jq` · macOS `brew install jq`
- **Python 3** — required, and **not only for the tools**: the Bash guard (`protect-bash.sh`) analyses
  each command with `hook_facts.py`, so without an interpreter on `PATH` (or `X4_PYTHON` pointing at
  one) it **asks** on every command instead of checking it. Any `python`/`python3`/`py` will do; the
  parse pass has no third-party dependencies.
- **uv** (+ Python 3.13) — for x4validate (https://docs.astral.sh/uv/)
- **XRCatTool** (from Egosoft) — to unpack your own game to `reference/` (run via `bin/xrcat`)
- **Wine** — only on **Linux/macOS**, to run XRCatTool (a Windows `.exe`)
- **Java 17+** — *optional*, only for the BaseX corpus search described above. Nothing else needs a
  JVM, and the rest of the toolkit works without it.

### Platform support
Runs on **Linux, macOS, and Windows (Git Bash)**. All locations are configurable via
`x4-paths.env` in the toolkit root (no hardcoded OS paths); the hooks accept both `/` and `\` styles.
On Linux/macOS, XRCatTool is invoked through Wine automatically by `bin/xrcat`.

As of **v2.01** that is true of the Python tools too — they read the same
`x4-paths.env` the installer writes. Before v2.01 they read a *different* set of variable
names, so on the `separate` and `global` layouts a successful install still left the cross-mod
commands pointed at CWD-relative paths. If you installed v2.0, take this update.

**The installers set `X4_TOOLKIT` in your user environment** (opt out with `--no-env` /
`-NoEnv`). Without it, the config file is found only by walking up from the current directory,
and the tools are often run from the game folder, which may hold no `x4-paths.env`. You would see `(unresolved)` locations with a perfectly good config sitting one
directory tree away.

| Your current `X4_TOOLKIT` | What the installer does |
|---|---|
| not set | sets it to this toolkit (new terminals only) |
| already this toolkit (any spelling: `/` or `\`, case, trailing separator) | nothing |
| a **different** toolkit | **nothing**: it prints a `[WARNING]` naming both, and the command to repoint it |
| `--no-env` / `-NoEnv` | nothing; it prints the command to run yourself |

Where it is written:

- **Windows** (both installers): your user environment (`HKCU\Environment`), through
  `scripts/x4-userenv.ps1` (`[Environment]::SetEnvironmentVariable(..., 'User')`). Open a
  new terminal to see it.
- **Linux / macOS** (`install.sh`): one marked block appended to your shell's startup file:
  `~/.zshenv` (or `$ZDOTDIR/.zshenv`) for zsh, `~/.bash_profile` for bash on macOS, `~/.bashrc`
  for bash elsewhere. Delete the block to undo it. Any other shell (fish, ksh) gets no file
  edit, only the line to add yourself. `install.ps1` sets it on Windows only. The POSIX paths
  are best effort and not device-tested.

**A later re-run with no `--toolkit` installs into the folder `X4_TOOLKIT` names**, because both
installers have always read an inherited `X4_TOOLKIT` as the destination. That is the upgrade
in place you normally want. Pass `--toolkit` to install somewhere else.

```bash
setx X4_TOOLKIT "C:\path\to\toolkit"                      # Windows, by hand (new shells only)
echo 'export X4_TOOLKIT=/path/to/toolkit' >> ~/.bashrc    # Linux / macOS, by hand
```

### OpenCode: best effort, CLI only, from docs, not measured

`--agent opencode` (and `all`, the default) installs an OpenCode target. **Everything about
OpenCode's behaviour below was READ from its docs and source (v1.18.34, `anomalyco/opencode`),
not measured against a running OpenCode.** The reading is recorded in
`docs/superpowers/measurements/2026-10-02-opencode-read.md`.

- **The OpenCode desktop app is not supported.** Its plugin hooks never fire
  ([anomalyco/opencode#38604](https://github.com/anomalyco/opencode/issues/38604), closed as not
  planned). Use the OpenCode CLI.
- **Two layers.** (1) Deny rules in `.opencode/opencode.jsonc`, rendered per machine at install
  from the roots the guards resolve: edits into `reference/`, `.cat`/`.dat` writes under the game,
  extensions, mods and toolkit folders, and deletion commands aimed at `reference/` by absolute
  path. (2) A plugin, `.opencode/plugins/x4guard.js`, that asks the toolkit's guards before every
  `bash`, `edit`, `write` and `apply_patch` and blocks the call when they refuse. It fails closed
  when Python, the adapter or the guards cannot answer.
- **The game-install block is plugin-only.** The guards allow a whitelist inside the game folder.
  A deny-only rule list cannot express that without allow rules, and allow rules would loosen
  your own OpenCode config.
- **A plugin that fails to load is skipped** (READ: OpenCode writes a log line and carries on,
  so a broken plugin fails open). When it is loaded, the system prompt
  carries a line starting `X4 GUARDS LIVE`. If that line is missing, only the deny rules apply.
- **A deny is final only with OpenCode's defaults** (INFERRED from OpenCode's permission code,
  not run). If an edit or command ever *asks* and
  you answer "always", that approval overrides a matching deny for the rest of the session. A
  per-agent `permission` rule of yours can override it too. The plugin still applies.
- **Subagents.** The plugin may not run inside a subagent session
  ([#5894](https://github.com/anomalyco/opencode/issues/5894)); the deny rules do.
- **No session-start banner.** OpenCode has no session-start hook, so a missing, deprecated or
  doubled path config is reported only by `python scripts/x4doctor.py` (Claude Code and Codex
  are told at session start).
- **Start OpenCode in the toolkit folder.** Its config and plugins are found walking up from where
  it starts. `OPENCODE_DISABLE_PROJECT_CONFIG` turns BOTH layers off.
- **Windows shell.** OpenCode runs commands in PowerShell unless you set `shell` in its config;
  the plugin reads that setting itself. If you set it to bash, also set `X4_OPENCODE_SHELL=bash`
  before installing so the skills are rendered for bash -- the plugin refuses every command while
  the two disagree. A shell the guards cannot judge (`cmd`) is refused: set OpenCode's `shell` to
  pwsh, powershell or bash.
- **Your own `permission.edit` or `permission.bash` written as a string** (`"edit": "ask"`) is
  replaced by the toolkit's deny object when OpenCode merges configs (READ). Write it as
  `{"*": "ask"}`. `x4doctor` flags it.
- After moving `reference/` or the game, re-render the deny rules:
  `python .opencode/hooks/opencode_config.py write --root .` An existing `opencode.jsonc` that
  the toolkit did not write is never overwritten.
- Verify with `python scripts/x4doctor.py --agent opencode`. **Not judged** (READ, OpenCode v1.18.34
  `tool/registry.ts`): MCP tools, custom tools you add as `tool/*.ts` / `tools/*.ts` in an OpenCode
  config folder, and tools another plugin defines. Only the four built-in tools that write a file
  or run a command (`bash`, `edit`, `write`, `apply_patch`) are checked. See
  `docs/superpowers/measurements/2026-10-02-opencode-read.md` (R15).

### Install methods (`install.sh` / `install.ps1`)
One guided installer, three layouts — pick what fits. Every path is auto-detected where
possible and overridable (`--game`, `--profile`, `--toolkit`, `--mods`, `--reference`,
`--extensions`, `--xrcattool`; `--unpack` to build `reference/` immediately; `--yes` non-interactive;
`--dry-run` to see the destination and item list without writing).

> **Upgrading an existing install needs `--over-existing`** (`-OverExisting` on PowerShell).
> Without it the installer refuses, names what it found, and tells you what to type. Installing
> over a toolkit replaces shipped reference files, skills and agents. A changed
> `KNOWLEDGEBASE.md` is independently retained before replacement; local notes,
> `x4-paths.env` and `.claude/settings.local.json` stay in place.
>
> **Upgrading from 3.x: an edited `CLAUDE.md` is kept on disk, but no longer loaded.** If your
> `CLAUDE.md` is not one the toolkit ever shipped (it is compared, by hash, with the `CLAUDE.md`
> of every release tag, listed in `scripts/shipped-instruction-hashes.txt`), it is moved to
> `X4-NOTES.pre-4.0.md` (or a dated name, if that exists) before the 4.0 file is copied, and the
> installer says so. **No agent reads that copy**: the 4.0 `CLAUDE.md` replaces it. To bring your
> notes back, review that copy and merge learned facts and explicit preferences into
> **`KNOWLEDGEBASE.local.md`**. Existing `X4-NOTES.md` remains preserved and is also read.
> Unclassified imported notes are evidence, not standing instructions. An unedited shipped copy (whatever its
> line endings) is simply replaced. `--dry-run` names the move without making it.
>
> `--yes` will also refuse an **auto-detected** destination: nothing named it and nobody is
> watching, so name it with `--game` or `--toolkit`.

Personal `.claude/settings.json` edits need a reviewed upgrade. Both installers
refuse before copying or moving instructions if that file differs from the
current source and every verified shipped version. This protects permissions,
custom hooks and changed matcher/timeout settings. Use the private
`upgrade-stage` / `upgrade-apply` workflow in [ADAPTING.md](ADAPTING.md), which
prepares merged settings and independent rollback bytes. Unresolved custom
pre-tool policies still require ordered integration. Generic-only installs
leave Claude settings alone.

Customized `.codex/hooks.json` also uses reviewed migration. An unchanged JSON
definition stays byte-exact, including formatting and read-only protection.
Codex installs require a root without `%`, `!`, `$`, backticks, double quotes or line
breaks, which its shell command definitions cannot safely carry. Generic-only
installs do not acquire that Codex restriction.

The chosen paths are written to `<toolkit>/x4-paths.env`.

| Method | What it does | When to use |
|--------|--------------|-------------|
| **in-game** | Copies the toolkit into your X4 game folder (the original model). | One game, one workspace. |
| **separate** | Toolkit lives in its own folder, pointed at the game via config. | Keep the game folder clean. |
| **global** | Installs the skills/agents into `~/.claude` and writes the `X4_*` paths into your global Claude settings. **Skills and agents only — the safety guards are not installed.** | **Several mod repos** — the skills/validator then work from any of them. |

```bash
# Linux / macOS / Windows (Git Bash)
bash install.sh --method separate --game "/path/to/X4 Foundations" --unpack
bash install.sh --method global            # multi-repo: skills+paths into ~/.claude
```
```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File install.ps1 -Method global
```
> Windows note: the hooks/scripts are bash, so running the toolkit needs **Git Bash** — found in
> its standard install location, or named by `X4_BASH` (see Prerequisites). The PowerShell
> installer finds it, runs `setup.sh` through it for you, and prints the `setx X4_BASH` line when
> it cannot.

#### Which agent: `--agent claude | codex | generic | opencode | all | auto` (default `all`)

The installer ships each agent's own files and nothing else (`-Agent` on PowerShell). A comma
list picks several, e.g. `--agent claude,codex` (each name is checked before anything is
written; `all` and `auto` stand alone):

| `--agent` | Instructions | Guards | Skills |
|---|---|---|---|
| `claude` | `CLAUDE.md` | `.claude/` hooks, registered in `.claude/settings.json` | `.claude/skills/` |
| `codex` | `AGENTS.md` | `.codex/hooks.json` + a guard copy in `.codex/hooks/`; execpolicy rules in `.codex/rules/` | `.agents/skills/` |
| `generic` | `AGENTS.md` | none -- a generic agent runs no hooks | `.agents/skills/` |
| `opencode` | `AGENTS.md` + `.opencode/X4-OPENCODE.md` | deny rules in `.opencode/opencode.jsonc` + the `.opencode/plugins/x4guard.js` plugin over a guard copy in `.opencode/hooks/` (best effort, CLI only) | `.opencode/skills/` |
| `all` | all of the above, OpenCode included | | |

- **`--agent auto` installs the agents it finds:** `claude` / `codex` on your `PATH`, or a
  marker already in the destination (`CLAUDE.md` or `.claude/settings.json` for Claude;
  `.codex/` for Codex; `.opencode/` or an `opencode.json(c)` for OpenCode, which is also
  detected as `opencode` on `PATH`; a bare `.claude/` does not count, because every install creates one).
  It prints each one it found and why. If it finds none, it installs `all` and says nothing was
  detected. With `--method global` it behaves as `all` does there.
- **`--codex-doc-max-bytes N`** (`-CodexDocMaxBytes N`, opt-in, Codex target only) writes
  `project_doc_max_bytes = N` (32768..1048576) as the first line of the project's
  `.codex/config.toml`. Why: Codex reads the root `AGENTS.md` and every nested one into ONE
  32,768-byte budget (MEASURED), so an `AGENTS.md` of your own lower in the tree can silently
  cut the end of the toolkit's. An existing different value is reported and left alone, and
  the file never travels between installs. It takes effect once you trust the folder in Codex.
- **Codex runs no hook you have not reviewed, and says nothing when it skips one.** After a
  Codex install: run `codex` in the folder, trust it, open `/hooks` and approve each X4 hook,
  then run `python scripts/x4doctor.py`. The installer never trusts a folder or approves a
  hook for you, and it prints these steps.
- **An `AGENTS.md` you wrote is never overwritten.** If the destination already has one that
  differs from the shipped file and from every `AGENTS.md` a release shipped, it is moved aside
  to `AGENTS.pre-4.0.md` (or a dated name, if that exists) and the installer says so.
  `--dry-run` names the move without making it.
- `--method global` is a Claude-only layout: `--agent codex`, `generic` or `opencode` there is refused,
  and the default installs the Claude target only.
- An installed toolkit is runtime-only: the `agent/` source the generator reads is not
  copied (the release zip still carries it).
- Skills for Codex and generic agents name the toolkit as `{{TOOLKIT}}`; the installer
  renders that as `$env:X4_TOOLKIT` on Windows (Codex runs PowerShell there) and as
  `$X4_TOOLKIT` elsewhere. Linux/macOS support for the Codex target is best effort and
  not device-tested.

**What each class of agent gets:**

| | Claude Code | Codex | Unknown agent |
|---|---|---|---|
| Deletes in `reference\` | blocked (hook; and the OS lock, once applied) | blocked when the hook is live; by the OS lock, once applied | blocked only by the OS lock, once applied |
| Overwrites in `reference\` | blocked (hook; and the OS lock, once applied) | blocked when the hook is live; the OS lock, once applied, stops accidental ones | only the OS lock, once applied, stops accidental ones |
| Destructive shell commands elsewhere | blocked or asked (hook) | blocked when the hook is live; the worst prefix cases by rules regardless | not blocked |
| Non-save profile changes | asks (manifest edits advise) | **deny with instructions** when the hook is live | through a qualified adapter |
| Recognized save mutations | 1–2 saves preserved first; bulk needs bound approval | same policy when the hook is live | 1–2 saves preserved first; bulk refused through the neutral CLI, which reads no typed approval |
| A guard that crashes | asks | **denies** (our wrapper), unless the interpreter cannot start: then **Codex runs the command** | -- |
| Hooks not reviewed or changed | n/a | **guards off, silently**: `x4doctor` and the session instructions flag it | -- |
| Post-edit validator feedback | yes | yes (when the hook is live) | no |

OpenCode's CLI plugin uses the same save policy and renders asks as refusals with
approval instructions. Its runtime and matcher fixtures are tested; enforcement in a
running OpenCode host remains unmeasured. For unknown hosts, Action v1 supplies policy
and preservation, while the integrating host must honor the complete result before execution.

**What the Codex hooks cannot see.** Each of these was measured on Codex 0.160 unless it says
otherwise:

1. **A shell `workdir`.** The measured hook payload supplied the session directory while
   Codex could execute in another folder. The guard now refuses recognized mutations whose
   target still depends on that missing execution directory; reads remain available. Use
   absolute mutation paths. On Windows, include a native drive or UNC share; `/c/...` is
   insufficient. An in-command directory change does not prove the omitted workdir. Policy conformance supplies synthetic workdir explicitly and is separate from
   observed native-host coverage.
2. **Typing into a running shell (`write_stdin`).** A shell or REPL started with a harmless
   command can then be sent any command, and those lines reach no guard (0 hook events for
   2 calls; a direct call is untested). This is disclosed, not blocked.
3. **Bare `git clean` / `git reset --hard` (both agents).** These name no folder. From the
   game folder or another X4 folder, `git clean` with `-x`, `-X` or `-d` and
   `git reset --hard` are denied with a reason. Naming the folder (`git -C "<folder>" ...`)
   asks you first.
4. **New tools, MCP tools and subagent controls are allowed.** Their names are logged to
   `.codex/x4-unknown-tools.log`. A subagent's own shell commands are still checked.
5. **The `.codex/rules` fallback is narrow.** It matches command prefixes only, so it
   repeats just two of the command guard's rules: `git add -A` / `--all` / `.` (blocked) and
   `python scripts/x4refguard.py remove` (asks; `python3` or `py` too, but only with that
   relative script path -- an absolute path does not match). Whether Codex loads it in a project you have not trusted is
   untested.
6. **Moving the folder turns the hooks off** until you review them again in `/hooks`, because
   the hook definitions contain the folder's absolute path.
7. **A patch applied through the shell gets no validator feedback.** It is checked before
   it runs, but the post-edit validator runs for `apply_patch` only.

> **`global` installs no guards.** The command guard, the file guard and the automatic
> backup described under *Safety, built in* are registered in a project's
> `.claude/settings.json`, and each hook resolves its paths from the project it runs
> in. `--method global` does not copy that file, so a global install gives you the
> skills and agents and none of the protection. This is deliberate rather than an
> omission: registering them globally would run them in every unrelated repository
> you open, on Claude's blocking pre-tool path, to guard X4 paths that are not there.
> To get the guards, install with `in-game` or `separate` **in the mod repo you want
> them in** — the two layouts can coexist, and the installer prints this note too.

---

## Using It

Open your agent in the toolkit folder and just talk. Some examples:

**Editing & balance**
- *"Raise all L/XL shield regen by 15% — show me the dry-run first, then validate."*
- *"Add a new tradeable ware modeled on Energy Cells, with all the files it needs."*

**Porting & debugging**
- *"This mod was made for 7.x. Run the migration checker and fix every 9.0 break."*
- *"Read my debug.txt and tell me which errors are real vs benign noise."*
- *"My diff patch isn't doing anything in-game — check whether the sel= actually matches."*

**Mod-list & research**

`uv run x4modlist search "name" --json` (from `tools/x4validate`) discovers Nexus
candidates (names only) with version, date and status, marked `have` / `have?` / `new`
against your active registry mods. It needs `X4_NEXUS_KEY`, reports capped/partial results,
unavailable evidence and its own coverage, and never changes your registry.

- *"Triage my mod list against Nexus — what's updated, obsolete, or abandoned for 9.0?"*
- *"What does this Nexus mod do, and are there known 9.0 issues, before I edit it?"*

**Interaction analysis**
- *"Before I add this weapon mod — does it conflict with anything I have, and is it balanced for VRO?"*
- *"Why does my death-alternative mod stop the vanilla eject sequence, and what would happen if I added an escape-pod mod too?"*

If it involves X4 XML, diff patches, MD/Lua scripts, the economy, or mod files, ask. The agent has
the engine context loaded and will figure out the path — and validate before you burn an
in-game test cycle.

---

## Important: game data & keys

- **This toolkit ships no Egosoft content.** You unpack your own `reference\` from your own copy
  with XRCatTool. `reference\`, `.cat`, `.dat`, saves, and `debug.txt` are all gitignored.
- **Nexus access uses your own free API key** (`X4_NEXUS_KEY` env var). No key is bundled; never
  commit or share one.

---

## Contributing
Found a new X4 quirk or a 9.0 migration gotcha? PRs welcome — especially additions to
`KNOWLEDGEBASE.md`.

## License
MIT — see [LICENSE](LICENSE). X4: Foundations is a trademark of Egosoft GmbH.

## Credits
- [Claude Code](https://claude.ai/code) by Anthropic, [Codex](https://github.com/openai/codex) by OpenAI, [OpenCode](https://opencode.ai)
- x4validate built on [lxml](https://lxml.de/); mod metadata via the [Nexus Mods API](https://api-docs.nexusmods.com/)
- Sibling project: [skyrimvr-claude-toolkit](https://github.com/WingedGuardian/skyrimvr-claude-toolkit)
