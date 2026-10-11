# Changelog

## v4.0.0 — 2026-10-03

**The toolkit is now the "X4 AI Assistant Toolkit"** and works with more than one coding agent:
**Claude Code and OpenAI Codex** are first-class, **OpenCode** is best effort (CLI only, built from
its docs and source, not measured), and any other agent can be connected through `ADAPTING.md`.
The repository becomes `x4-ai-toolkit` (GitHub redirects the old `x4-claude-toolkit` address), and
the release asset is `X4.Foundations.AI.Assistant.Toolkit-v4.0.0.zip`.

### Upgrading from 3.x

1. **The path config moves to the toolkit root**: `<toolkit>/x4-paths.env` (for `--method
   in-game`, the game folder) instead of `<toolkit>/.claude/x4-paths.env`. The installers MOVE it
   on upgrade; after a `git pull`, run `python scripts/x4config.py migrate --apply` (`status` shows
   which file is read). The 3.x location is still read for all of 4.x, with a one-line notice. A
   read-only (x4lock'd) config moves too and stays locked. Two copies that differ are refused,
   naming the differing keys, never their values. To go back to 3.x, move the file back.
2. **The installers set `X4_TOOLKIT` for you** at OS level (Windows: your user environment;
   Linux/macOS: one marked block in your shell's startup file). A value that already names a
   different toolkit is reported and left alone. `--no-env` / `-NoEnv` opts out and prints the
   command to run yourself. Open a new terminal afterwards.
3. **An edited `CLAUDE.md` is kept on disk, but no longer loaded**: if its hash matches no
   `CLAUDE.md` a release tag shipped, it is moved to `X4-NOTES.pre-4.0.md` (or a dated name)
   before the 4.0 file is copied. No agent reads that copy; to bring your notes back, merge what
   you want into **`X4-NOTES.md`**, which every agent's instructions read and the toolkit never
   writes. An unedited 3.x `.claude/x4-paths.env.example` is removed (an edited one is kept).
4. **`AGENTS.md` is new** (the instruction file for Codex, OpenCode and other agents). No release
   before 4.0 shipped one (0 of 23 tags), so an `AGENTS.md` already in the destination is yours:
   it is moved aside to `AGENTS.pre-4.0.md` (or a dated name), never overwritten.
5. **Codex users must review the hooks.** Codex runs a project hook only after you review it, and
   says nothing when it skips one. After installing: open `codex` in the folder, trust it, review
   the X4 hooks in `/hooks`, then run `python scripts/x4doctor.py --agent codex`. See below.
6. `--dry-run` names the `CLAUDE.md` and `AGENTS.md` moves without making them.
7. **A `reference/` you already have is NOT OS-protected until you apply it.** The installers do
   not change its permissions (an ACL change is yours to make); every install summary now says
   which state it is in. Run `python scripts/x4refguard.py status`, then
   `python scripts/x4refguard.py apply` -- it shows the folder and a file count and asks first
   (`--yes` confirms up front); `python scripts/x4doctor.py` shows the state in its
   `layer2.reference` row. A fresh `bin/unpack-reference.sh` applies it for you. What it blocks
   is under *Safety*.

### Fixed before release: the install red-team (2026-10-04)

A cold, docs-only install test on a fresh clone found three blockers and six smaller defects.
All are fixed:

- **A toolkit copy acted for ANOTHER toolkit.** Scripts and tools located their config through an
  inherited `X4_TOOLKIT`, so a second copy's `x4refguard apply` targeted the first copy's
  reference tree (stopped after ~2 minutes; nothing was applied). Every script and tool now acts
  for the toolkit it **lives in**; an `X4_TOOLKIT` naming a different one gets one line naming
  both, and `x4refguard apply/remove`, `x4config migrate --apply`, `x4lock lock/unlock` and
  `bin/unpack-reference.sh` **refuse** unless you pass `--toolkit <folder>`. `x4doctor` compares
  against its own toolkit (`--toolkit` to choose). `x4refguard`, `x4lock` and the unpack also
  refuse an exported root or an inherited `X4_CONFIG` that would point them at another toolkit's
  files (see *Release-review fixes*).
- **`x4refguard apply/remove` ask first.** They print the target folder, count the files (with
  progress), then ask; `--yes` confirms; with no terminal and no `--yes` they refuse (exit 2). A
  "still applying" line appears every 10 s while the OS call runs.
- **Git Bash is found on a stock Windows `PATH`.** `bash` there is the WSL stub, so
  `install.ps1 -Agent all` ended INCOMPLETE and `x4doctor` failed six rows. One resolver now looks
  in Git for Windows' install locations, then along `PATH` past the stub; `install.ps1` passes
  what it finds to everything it runs, and prints the exact `setx X4_BASH "..."` line when it
  finds nothing. `SETUP_PROMPT.txt` no longer has a PowerShell agent run `bash setup.sh` (the
  PowerShell installer already ran it).
- **`--agent` / `-Agent` take a comma list** (`claude,codex`), every name checked before
  anything is written.
- **`x4doctor` has a TODO row status and exit 4**: "installed correctly; only YOUR steps are
  pending" (trusting the folder in Codex, approving its hooks, applying the `reference/`
  protection). A TODO never hides a FAIL (exit 1) or an UNKNOWN (exit 3).
- Install summaries say only true things: `Next:` asks for `X4_GAME` only when it is blank;
  `setup.sh` no longer calls the config it was just given "already present"; a stale OpenCode
  deny-rule file says what differs and why; `x4refguard`'s "unconfigured" says how to fix it;
  `x4doctor`'s targets line no longer lists "generic" beside Codex. On Windows, extract to a
  short folder: the deepest file is 92 characters below the toolkit root.

### Fixed before release: the second install red-team (2026-10-04)

A second cold, docs-only install test (fresh clone, both installers) found two blockers, eight
confusing messages and six cosmetic ones. All are fixed:

- **The installed toolkit's own test suite could not run.** `SETUP_PROMPT.txt` says to check it,
  and in an installed toolkit `uv run pytest -q` stopped in collection (3 errors: tests that read
  the repository-only `agent/` source, which no install copies). Every test that needs
  repository-only content now SKIPS there, counted and with a `REPO-ONLY` reason; a checkout that
  lost such a file still fails. Measured on real installs from both installers (2026-10-05,
  Windows, `--agent all`, separate method, `--no-env`, the full installed suite): the
  `install.ps1` install passed, 4,018 passed, 377 skipped, 0 failed; the `install.sh` install
  ended 4,017 passed, 366 skipped, 6 failed, from three causes fixed afterwards and re-run
  individually (no second full run). A real install by each installer collects with no error.
  `SETUP_PROMPT.txt`, `setup.sh` and the README give the command per shell (Windows PowerShell 5.1
  rejects `&&`) and say what passing means: 0 failed and 0 errors; skips are expected.
- **`x4doctor`'s `bash.path` row failed every stock Windows install.** It failed whenever the first
  `bash` on `PATH` was the WSL stub, but no installed hook starts bash from that `PATH`: Claude
  Code runs its hooks in Git Bash, Codex uses its PowerShell `commandWindows`, and the guards find
  Git Bash themselves. The row now reads the hook definitions. It fails only when a hook would
  start a bare `bash` from that `PATH`, or when no Git Bash can be found (the fix is printed:
  `setx X4_BASH ...`, or re-run the installer). Otherwise it names the stub as information. This
  was also the whole difference between `x4doctor` exiting 1 from PowerShell and 3 from Git Bash.
- `x4refguard apply` on a `reference/` without `.unpacked-and-locked` now says what that file is
  (the last thing `bin/unpack-reference.sh` writes, every release since 1.0) and prints both ways
  out, per shell: unpack it with the toolkit, or mark a complete unpack you made yourself. The
  README says the same. `status` names the folder it checked, says "not applied" instead of
  "absent", and prints the long "To lift it" block only when there is something to lift. `apply`
  and `status` count the same sample (root included).
- Install summaries print plain `python scripts/x4refguard.py apply` (`x4doctor` prints it by
  absolute path, with `--toolkit` and `--reference`), which shows the folder and asks; `--yes` is
  explained as the agent's form. A `reference/` without the
  sentinel is named as unfinished instead of being sent to `apply`.
- A personalised `CLAUDE.md` kept as `X4-NOTES.pre-4.0.md`: the installer now points at the
  `CLAUDE.md` your previous version shipped (from its `CHANGELOG.md`) and prints a
  `git diff --no-index` line, so you can find your own edits.
- The `--over-existing` / `-OverExisting` refusal prints your full command, plus the missing
  flag, instead of ending in `...`. When the destination came from an inherited `X4_TOOLKIT`
  rather than your command line, it offers no rerun line at all (see below).
- `setup.sh` gives the `reference/` unpack command for PowerShell too, and no longer asks you to
  set `X4_GAME` after the installer set it, or calls a config it was just given "left as it
  is". The Git Bash installer prints its source as a Windows path, and `install.ps1` prints
  one path separator in its summary.

### Fixed before release: the final review (2026-10-10)

- **`ask.py` / `coverage.py` (#7):** BaseX queries now travel UTF-8 both ways — the query is
  passed to BaseX as a file. Non-ASCII results no longer crash, error text is no longer mangled,
  and a literal outside Windows-1252 (e.g. `"火"`) no longer silently becomes `?`, which used to
  make a zero result describe a query nobody wrote (BLIND-SPOTS F271). The query file is passed
  by its name, so a toolkit installed under a non-ASCII folder no longer sends that folder name
  through the same lossy channel, and a BaseX folder you cannot write to is refused with a
  message naming it instead of a traceback.
- **`ask.py refs` (#5):** now also searches `bullet/@class`, `add_blueprints/@wares` and
  `@macros` lists, and the `macro.X` / `ware.X` spellings, and states the exact forms searched in
  `--help`, `QUERIES.md` and every result. `$variable` expressions and other expression
  attributes are not searched, and it says so (F272).
- **x4eff (#6):** a path that two mods spell with different letter case is one document, as it is
  to the game; this fixes the 11,302-vs-11,301 coverage refusal. On a case-sensitive filesystem a
  loose overlay is now found regardless of case, and a folder holding two spellings of one file is
  refused with a reason instead of one being picked (F273; Linux behaviour inferred, not measured).
  `x4compat` reports that refusal (and an unreadable overlay file) as a counted skip naming the
  mod; it ended the run with a traceback.
- **x4compat (#4):** a duplicate-add HARD row names only the mods that add that key (an unrelated
  co-adder is no longer named, or "winning"), every duplicated key is reported, and the parent's
  SOFT "coexist" row is always kept. Which duplicate the game keeps is not engine-measured, and
  the row says so (F274).
- **x4compat wildcard icons:** an icon like `icon#ship_*` that two mods declare is compared by its
  expanded IDs (one per texture file, loose or packed; `.gz` answers `*.tga`), both for diff adds
  and for complete `icons.xml` files. A row appears only for an ID both expansions hold; a texture
  set that can't be listed is a counted NOT CHECKED. On the maintainer's modlist this removed 5
  false rows (F276).
- **Effective tree: wildcard icons (#9):** same-named wildcard icons in `libraries/icons.xml`
  (`upgrade_*`, `ship_*`, `module_*`, `*`) are all kept, each with its own texture folder;
  each layer used to replace the previous one's. On base + 8 DLC the effective file kept 17 of
  38 wildcard declarations and now keeps all 38. `x4effective show` names each same-named row
  by class and origin. Plain icon names and other files still let the later declaration win.
  A patch that selects such an icon by name alone now matches several and is reported as
  ambiguous, as the game skips it. **Rebuild the effective store and x4eff** to pick this up (F279).
- **Effective tree: `if=` guards:** an `if=` that evaluates to NaN (e.g. `number(@missing)`)
  now counts as false, as it does in XPath; it used to apply the op (F279).
- **Builders over a missing or empty reference (#10):** `x4effective build`, `x4xref build` and
  `tools/basex/build-effective.py` now refuse with exit 2, naming the reference folder and how to
  set it, when that folder does not exist or holds no base-game XML. DLC files do not count,
  including DLC read packed from the game install, so an empty reference on a machine with the
  game installed is refused too. Each used to build an empty
  store, index or tree, replacing the previous one, and exit 0. The refusal comes before anything
  is written, so an existing store or index is left as it was. `build-corpus.sh` and
  `build-effective.sh` run the same check first, before Java and before dropping `x4raw` or
  deleting the previous x4eff tree; `build-corpus.sh` used to drop `x4raw` before noticing (F280).
- **x4compat (#8):** an op guarded by `if=` is judged by its guard, read the way the merge
  model applies it: on the file as it stands when that mod loads (base game, DLC, every earlier
  mod, and the op's own file up to that point). An op whose guard is false there changes nothing,
  so it is no longer reported as a HARD, duplicate, SOFT, SUBTREE or removal collision, and is
  never named the winner; it is listed in a new GUARDED OFF section (`guarded_off` in `--json`,
  counted in the summary). A row involving a true guard says the result depends on this load
  order. A guard that cannot be evaluated (invalid XPath) keeps its row, labelled, and is
  reported as not analysed. Two related cases are fixed too: a guarded add with a true guard now
  takes part in the duplicate-registry-entry check (it used to be skipped), and a macro defined
  by a guarded `<diff>` counts for NAME-CLASH only when its guard is true. On the maintainer's
  modlist this removed 12 false rows (6 HARD, 6 SUBTREE) and listed 23 guarded-off ops; a full
  check runs about 9% longer (55 s to 61 s) (F278).
- The `icacls` lifts, the release-gate exit codes, the gate report folders and the restored
  resource rule are listed under *Safety*, *Maintainers* and *Every agent* below
  (BLIND-SPOTS F265-F268).

### Every agent: one source, per-agent files

- **Agent-facing files are generated from one neutral source tree, `agent/`**, by
  `tools/x4validate/scripts/gen-agent-trees.py`: `CLAUDE.md`, `AGENTS.md`, `.claude/`
  (agents, skills, `settings.json`, hooks), `.codex/`, `.agents/skills/` and `.opencode/`. Edit
  `agent/`, then regenerate; a test fails if a generated file drifts from its source. The
  generator rewrites only stale files and names each one, never deletes, and refuses (rc 2)
  rather than truncating or guessing: `AGENTS.md` over 32,768 bytes (where Codex cuts it,
  MEASURED), `CLAUDE.md` over 40,000 characters, a shared core or a skill naming a Claude-only
  mechanism (skills reach every agent), an `agent.yaml` key it does not know (a typo `tool:`
  used to be dropped, which gives a Claude Code subagent every tool), a `read_only` agent
  granted a file-editing tool or no tools list, a leftover `{{TOKEN}}`, a non-UTF-8 file,
  invalid YAML or an unclosed frontmatter.
- **`AGENTS.md` carries the full shared instructions**, built from the same agent-neutral core as
  `CLAUDE.md` plus a per-agent addendum. Maintainer-only guidance moved verbatim into a new
  **`x4-toolkit-dev`** skill.
- **The 11 skills are installed for every agent**: `.claude/skills/` (Claude Code),
  `.agents/skills/` (Codex and generic agents), `.opencode/skills/` (OpenCode). Outside Claude
  Code they name the toolkit `{{TOOLKIT}}`, which the installer renders as `$env:X4_TOOLKIT` on
  Windows (Codex runs PowerShell there) and `$X4_TOOLKIT` elsewhere.
- **The installers ship each agent's own files: `--agent claude | codex | generic | opencode |
  all | auto`** (default `all`, which includes OpenCode). `auto` installs the agents it finds on
  `PATH` or already in the destination, says which and why, and installs `all` (saying so) when
  it finds none. Both installers hold the same item sets (a test requires it). `agent/` is never
  installed. `--method global` stays a Claude-only layout; another agent there is refused.
- **`--codex-doc-max-bytes N`** (opt-in) writes `project_doc_max_bytes = N` into the project's
  `.codex/config.toml`, because the root and every nested `AGENTS.md` share one 32 KiB budget
  (MEASURED). An existing different value is reported and left alone.
- **Two new standing instructions for every agent** (`agent/instructions/core.md`, *Safety Rules >
  General*, so both `CLAUDE.md` and `AGENTS.md` carry them): deletions go to the Recycle Bin or
  Trash (Windows: `Microsoft.VisualBasic.FileIO.FileSystem` with `SendToRecycleBin`; Linux:
  `gio trash`; macOS: the Finder Trash), and a permanent delete needs your OK for that deletion;
  and the agent never saturates the machine -- before a heavy job (full test suites, corpus
  builds, load tests, parallel workers) it measures what is already running and keeps CPU, RAM,
  free disk on each drive it writes to and GPU at or under 80% of the total, or waits, shrinks
  the job or tells you before starting; it uses at most eight test workers and never terminates
  another session's processes. **The guards judge a move to the Trash exactly as `rm`
  of the same paths** (`gio trash`, `trash-put`, `gvfs-trash`, `kioclient` move to `trash:/`, a
  Finder delete through `osascript`): before, `gio trash <game>` or a trashed file in
  `reference/` was allowed (16 of 24 probes wrong; BLIND-SPOTS F189). The Windows Recycle Bin
  form was already judged correctly.

### Codex

- **Codex users must re-review hooks** (first Codex hook definitions, template v1).
  codex-hooks-template: d6fef82e09d3f54afbda775d1646bd8968a02b2893de8c867476aca17eef4d19
  Codex runs a project hook only after you review it, and silently skips one whose definition
  changed (MEASURED). After installing or updating, open Codex in the game folder, review the X4
  hooks (`/hooks`), and check that each session starts with `X4 GUARDS LIVE (codex hooks v1)`.
  `AGENTS.md` tells Codex to look for that banner and, if it is missing, to warn you before any
  write or delete. The banner says what is checked: `shell commands and apply_patch are checked
  (input typed into a running shell is not)`.
- **Three layers.** (1) `.codex/hooks/` carries every guard byte for byte plus an adapter that
  translates Codex's hook payloads to the same guards Claude Code runs. Shell commands are judged
  as the shell that runs them (PowerShell on Windows, where Codex labels them "Bash");
  `apply_patch`, also when run through the shell, by every file it adds, updates, deletes or
  moves. Only a JSON deny blocks in Codex (MEASURED: a crash or `exit 2` lets the command run), so
  the entry wrappers turn a guard's failure into a deny -- but not their own: when the
  interpreter that runs the wrapper cannot start, Codex runs the command (README, *What each
  class of agent gets*). One deadline bounds a whole patch. (2) `.codex/rules/x4.rules` blocks
  `git add -A` and asks before `python scripts/x4refguard.py remove` even when the hooks are not
  running. The rules match a command PREFIX only: `python`, `python3` or `py` followed by exactly
  `scripts/x4refguard.py remove`; an absolute script path or another interpreter does not match. (3) The OS-level protection of `reference/` (below). A conformance suite
  replays every Claude hook case through the Codex chain and requires equal verdicts.
- **A patch is read exactly as Codex reads it.** The guard's apply_patch parser is a port of Codex 0.160.0's own, held to a recorded run of Codex's apply_patch over 67 patch shapes (`scripts/capture-codex-patch-oracle.py`). Found in review before release: an INDENTED `*** Delete File:` line after an added file was a delete to Codex and file content to the guard, so a delete inside `reference/` was allowed (BLIND-SPOTS F153).
- The installer renders `.codex/hooks.json` for the destination's absolute folder. It never trusts
  a folder or approves a hook for you; it prints the review steps.
- `codex_trust.py report` tells whether Codex is actually running the hooks (trusted, untrusted,
  modified, disabled), reproducing Codex's own trust hash.
- **What the Codex hooks cannot see is disclosed in the README**: a shell `workdir`, input typed
  into a running shell, new and MCP tools (logged, allowed), the narrow rules fallback, a moved
  folder (hooks off until re-reviewed), and validator feedback for a patch applied through the
  shell. Linux and macOS support for Codex is best effort and not device-tested.

### OpenCode (best effort, CLI only, from docs, not measured)

- `--agent opencode` installs two layers: deny rules in a per-machine `.opencode/opencode.jsonc`,
  rendered at install from the roots the guards resolve (edits into `reference/`, `.cat`/`.dat`
  writes, deletion commands aimed at `reference/`), and a plugin, `.opencode/plugins/x4guard.js`,
  that asks the same guards before every `bash`, `edit`, `write` and `apply_patch` and blocks on a
  refusal. It fails closed when Python, the adapter or the guards cannot answer. The game-install
  block is plugin-only.
- **The plugin judges a `bash` call in the grammar of the shell OpenCode runs it in**:
  OpenCode's configured `shell`, else `$SHELL`; a shell OpenCode itself refuses (fish, nu) falls
  back to the platform default (PowerShell on Windows, bash elsewhere), as OpenCode does. It used
  to assume bash everywhere off Windows and PowerShell on Windows, so under a `pwsh` shell a
  PowerShell `Remove-Item` into `reference/` was judged as bash and allowed (BLIND-SPOTS F176).
  **With any other shell -- `cmd`, or anything but bash, sh, dash, ksh, zsh, pwsh or powershell --
  every `bash` call is refused** (fail-closed: the guards have no grammar for it), and the refusal
  says to set OpenCode's own `shell` to pwsh, powershell or bash. A configured shell that does not
  resolve to a program (OpenCode would silently fall back to another) is refused too, and so is
  every `bash` call when `X4_OPENCODE_SHELL` disagrees with the detected shell: it is a
  declaration the installers read, never an override of the grammar (following the old advice to
  set it made a `cmd` delete into `reference/` read as bash and pass). Each OpenCode instance
  keeps its own configured shell (BLIND-SPOTS F213-F215).
- A patch is read with **OpenCode's own grammar** (column-0 headers, unknown lines skipped), not Codex's, checked against OpenCode v1.18.34's parser run under node (BLIND-SPOTS F153).
- The OpenCode **desktop app is not supported** (its plugin hooks never fire,
  anomalyco/opencode#38604). Nothing here was run inside OpenCode: the README's OpenCode section
  lists what was read and what can switch the layers off.

### Any other agent

- **`ADAPTING.md`** is written for an agent the toolkit has no adapter for: how to measure whether
  its hooks fail open or closed, the guard contract, worked Claude Code and Codex examples, where
  an adapter goes, the proof required before claiming protection, and an upstream template.
- **`x4guard check`** (`.claude/hooks/x4guard.py`, also under `.codex/hooks/` and
  `.opencode/hooks/`) asks the guards for a verdict with no side effects. `--shell` names the
  shell that will EXECUTE the command. A relative `--path` resolves from the caller's working
  directory; a delete is judged as the stricter of a write and an `rm -rf --` of that path and
  keeps every guard's advisory. A guard that cannot run, reports it checked nothing, or a copy outside
  the agent hook folders with `X4_TOOLKIT` unset gives an inert DENY naming the reason, never an allow. One budget
  (`X4_GUARD_TIMEOUT_S`, default 25 s) covers the whole check; a hung guard's process tree is
  killed and the check returns within that budget + 8 s even when the kill fails (a guard's child
  once stretched a 2 s budget to 10.9 s). A timeout names the budget that ran out.
- **`x4guard conformance`** is the proof: it replays every guard case of the hook test suite,
  plus neutral PowerShell, space-in-path and drive-path cases, through any adapter and compares
  each answer with the guards' own, per case (exit 0 agree, 1 disagree, 2 cannot evaluate, 3 too
  little examined -- never 0 on nothing). A toy agent whose adapter was written cold from
  `ADAPTING.md` alone runs through it in the test suite, and `docs/ADAPTING-COLD-TEST.md` repeats
  that cold exercise once per release.
- **`SETUP_PROMPT.txt` works for any agent**: it asks the agent to identify itself, check for an
  adapter, never approve hooks on your behalf, read `ADAPTING.md` when it has none, and report
  `x4doctor`'s live and not-live layers honestly.

### New: `x4doctor` -- are the guards live here?

- **`python scripts/x4doctor.py [--root DIR] [--agent NAME] [--json]`** is a read-only health
  check per installed agent target, on Python 3.10 with no dependencies: the bash, python and jq
  the guards resolve (each executed), the roots the guards see vs the tools, deployed-vs-source
  parity, a guard self-test with controls that must deny and controls that must allow (so a guard
  that denies everything cannot pass), Claude's hook wiring and `disableAllHooks`, Codex's trust
  and per-hook review state, OpenCode's plugin and rendered rules, `X4_GUARD`, the OS-level
  `reference/` protection and the x4lock state. Every row is OK, FAIL, UNKNOWN, TODO (installed
  correctly; a step only you may take is pending) or N/A. Exit 0 every applicable row OK, 1 any
  FAIL, 3 an UNKNOWN and no FAIL, 4 only TODO rows pending, 2 could not run; a run that checked
  nothing never exits 0, and a TODO never hides a FAIL or an UNKNOWN.
- **If you script against `x4doctor`, check exit 4 (or the TODO rows), not only exit 1.** Three
  row states changed on purpose during the 4.0 release work (`x4doctor` shipped in no earlier
  release): Codex's `codex.trusted` (folder not trusted) and `codex.reviewed` (hooks not reviewed,
  so Codex skips them silently) moved from FAIL to **TODO**; `layer2.reference` is never OK
  while `reference/` lacks the OS-level protection: none is **TODO** on every root, a Claude-only
  one included (that root read OK before, "the Claude hooks cover deletes"), and partial
  protection is FAIL; the x4lock row with unlocked files moved from UNKNOWN to **OK**
  (informational: locking is your choice). So **a fresh install whose `reference/` is unpacked
  but not yet protected exits 4**, whichever agents it has, until you run `x4refguard.py apply`.
  With no `reference/` at all the row is a TODO too, naming the unpack command -- or UNKNOWN when
  something says the tree was moved rather than never unpacked (below). A caller
  that tests only for exit 1 no longer sees "Codex hooks are not running": that state now exits
  4, and `--json` lists those rows with status `TODO`.
- **Every command `x4doctor` prints runs where it is printed.** The `reference/` rows print the
  unpack and `x4refguard apply`/`remove` by absolute path, with `--toolkit` and `--reference`
  naming the tree the row judged: without them, an exported `X4_REFERENCE` differing from the
  config, or an inherited `X4_CONFIG` outside the toolkit, made the printed command refuse in the
  very shell the doctor had checked. A `reference/` without `.unpacked-and-locked` (which `apply`
  refuses) names the unpack instead; where no flag lifts a refusal, the hint says to clear
  `X4_CONFIG` first. A missing tree that looks MOVED -- `.claude/.reference-buildid` exists, or a
  finished unpack sits at `<toolkit>/reference` -- is UNKNOWN (exit 3), not "run the ~27 GB
  unpack"; a move that leaves neither sign still reads as not unpacked (BLIND-SPOTS F222, F226).
- **`roots.config` FAILs on a path-config line every loader ignores** (not KEY=value, a `$( )` or
  backtick, an unquoted `; & | < >`, or non-ASCII whitespace), naming line and reason, never the
  value: what that line would set had silently fallen back to a default. The Python tools say the
  same on stderr, once per process, and `x4validate --paths` lists the lines (BLIND-SPOTS F178,
  F190).
- Measured while building it: with `X4_TOOLKIT` unset and no path config in a game root, the
  guards fell back to `<game>/reference` and ALLOWED a write and a delete into the configured
  reference tree (4 of 4 deny controls, check-only). The verdicts are unchanged in 4.0, but the
  state is no longer silent: `x4doctor`'s `roots.config` FAILs, and Claude Code and Codex name it
  once per session (OpenCode has no session start, so only `x4doctor` reports it there). A
  deprecated or doubled config is named the same way.

### Safety

- **Linux/macOS: a path config saved with Windows line endings no longer weakens the guards.** Bash
  kept the trailing carriage return on every configured folder, `reference/` included, so the guards'
  folder rules could fail to match (BLIND-SPOTS F152; the effect on verdicts is inferred, the CR is
  measured). Windows was never affected. This was present in 3.x too.
- **`reference/` can be protected at the OS level, once you apply it** (`bin/unpack-reference.sh`
  does after an unpack; the installers do not). `python scripts/x4refguard.py status | apply |
  remove`: on Windows one inherited deny for your own account, `(OI)(CI)(DE,DC,WD,AD)`, blocks
  deleting, renaming, overwriting and creating anything inside the tree; reads still work, and you
  can lift it yourself without elevation. Linux and macOS get `chattr +i` / `chflags uchg`, or a
  `chmod a-w` fallback: best effort, not device-tested. `bin/unpack-reference.sh` applies it after
  a verified unpack and refuses a forced re-unpack while it is on; every recovery message names
  the lift first. It works from PowerShell 7 too (a pwsh terminal, Codex on Windows, CI).
- **Lifting that protection asks you first**: `x4refguard.py remove`, and a raw `icacls ...
  /remove` or `/reset` aimed at the reference root (or, with `/T`, a folder above it).
- **A relative path in a shell command is judged where the shell runs.** The guards ignored the
  `cwd` Claude Code and Codex send with every call, so `rm -f reference/...` run from the folder
  holding `reference/` was allowed while the absolute form was blocked (a live Codex run
  overwrote a file that way). Measured over 53,828 historical commands before shipping: no new
  confirmation prompt and no new block; three commands change, two of them gaining an advisory
  (BLIND-SPOTS F147).
- **Fewer approval prompts: the guards ask only what is genuinely yours.** Measured over the
  maintainer's own Claude Code transcripts (793 of them, 82,585 tool calls; commit `bd45eef`).
  These figures cannot be reproduced from this repository: neither the transcripts nor the
  counting script are in it. 54 hook prompts in 5 weeks, all 54 approved -- an ask that
  is always approved protects nothing. A command `bash -n` rejects, or one nested past the guard's expansion bound,
  is now a DENY with a reason the agent can act on. Deletes in X4 folders and writes under
  Documents are ADVISORIES. Still asks: the X4 profile, save games, a guard that cannot run, a
  failed backup. Still hard-blocked: the game install and `reference/`.
- **A bare `git clean` with `-x`, `-X` or `-d`, or `git reset --hard`, run from the game folder
  or another X4 folder is blocked** (a deny with a reason, not a prompt). The game folder's git
  repo ignores every file except its own, so `git clean -fdx` there would delete the
  installation's untracked files. Naming the folder (`git -C "<folder>" ...`) still asks.
- **`X4_GUARD=off`**, the launch-time escape hatch: set to exactly `off`, the Bash and file guards
  turn a deny or an ask into an advisory naming what it would have been, log every override to
  `GUARDS-OFF.log`, and the session starts with a GUARDS OFF banner. A guard that could not check
  still asks.
- **An agent can save its notes, and is told how to edit a locked file.** Exactly
  `<project root>/X4-NOTES.md` (and `X4-NOTES.pre-4.0.md`) is allowed; the same name deeper in the
  game folder is still blocked. A write to a file x4lock has locked gets a note naming the unlock,
  edit and relock commands; the lock stays. The game-folder block now says where notes, game
  facts and mod files go. New: `x4lock.py protected <path>`.
- The file guard allowed `CLAUDE.md` in the game root but denied **`AGENTS.md`**; it now allows
  `AGENTS.md` at the project root only (the resolved path must equal a root plus the name, as for
  `X4-NOTES.md`); the same name deeper in the game folder is still blocked.
- **x4lock protects `AGENTS.md`, the Codex and OpenCode trees and `.agents/skills`** where that
  agent is installed (marked by its guard directory), so a Claude-only root never reports
  `AGENTS.md` missing.
- **A Grep over a git-ignored folder is denied** (BLIND-SPOTS F146): Grep and Glob honour
  `.gitignore`, and a game root kept under git with a whitelist `.gitignore` hid all 133 installed
  mods' manifests from Grep, which answered "No files found". The deny names `rg --no-ignore` or a
  single file; a Glob there gets an "unreliable zero" advisory.
- **Backups no longer overwrite each other**: two edits in the same second shared one backup
  name. Each backup now reserves a unique file; older names still restore.
- **The post-edit validator says when it could not validate** ("VALIDATION NOT COMPLETED") instead
  of staying silent on a missing or crashing `uv`, unreadable output or a DEGRADED result. A
  routine `--file` skip stays silent (it is on every edit: 50 of 50 measured).
- The timeout guard judges a background command against its own 2-hour cap, not the foreground
  one.
- **A `cd` to a root variable is judged as that root** (BLIND-SPOTS F154, present in 3.x too):
  `cd "$X4_REFERENCE" && rm -rf libraries` was allowed while `rm -rf "$X4_REFERENCE/libraries"` was
  refused. Replayed over 52,276 past commands: 2 verdicts change, each now matching the same
  command spelled with the literal folder.
- The ask before lifting the `reference/` protection also sees `icacls "$X4_REFERENCE" /reset /T`,
  `icacls reference /reset /T` from the folder above it, and `"$X4_PYTHON" scripts/x4refguard.py
  remove` (BLIND-SPOTS F155).
- A bare `git clean -fdx` from the game folder is blocked even when the same command also names
  another folder (`git -C <mods> clean -fdx; git clean -fdx` turned the block into a question).
- **The path config is read as data, never run** (BLIND-SPOTS F156; present in 3.x too). The
  guards ran `x4-paths.env` as shell code on every tool call: a line `exit 0` in it made every
  hook end before it decided anything, which reads as "allowed", and any other command in it ran
  each time. It is now parsed by one rule that the guards and the Python tools share. A line
  that is not configuration is ignored, and the session start names it (line number and reason,
  never a value).
- **`X4_GUARD` is read only from the environment the agent is launched from.** Written into
  `x4-paths.env`, it switched every guard off under a banner saying it had been set at launch.
  A config line naming it is now ignored, and the session start says so.
- **Agents may not write or delete `x4-paths.env`** (either location, or the file `X4_CONFIG`
  names), by a file edit or a shell command: every guard reads its folders from it. Edit it
  yourself, or re-run the installer. Reading it is unchanged.
- **With `X4_GUARD=off`, a guard that could not check still asks**, as promised above. A
  command that does not parse, a command name built by substitution, PowerShell that could not
  be translated and an unreadable file-edit payload had become advisories. The Grep deny on a
  git-ignored folder now honours `X4_GUARD=off` like every other verdict.
- **Grep under a folder git ignores AS A FOLDER works again** (`reference/`, `build/`): ripgrep
  still reads every file under such a root, so the F146 deny was wrong there. The toolkit's own
  `reference/libraries` could not be searched. Only a pattern matching every name under the root
  (the game folder's `*`) hides its files, and only that is denied (BLIND-SPOTS F157).
- **The X4 profile asks again with `X4_PROFILE` unset**, for shell deletes and writes under
  `Documents/Egosoft/X4` (or `~/.config/EgoSoft/X4`). They had become advisories along with the
  rest of Documents.
- **`CLAUDE.md`, `AGENTS.md` and `KNOWLEDGEBASE.md` are allowed at the project root only**,
  like `X4-NOTES.md`. The same names deeper in the game folder are game files again. All three
  were allowed anywhere (the first two since 3.x).
- The Bash guard's refusals name remedies every agent has ("your file-edit tool (Edit,
  apply_patch)") and sections that exist, instead of "the Edit tool" and `CLAUDE.md #22`.
- A backup that fails no longer writes its audit line outside the trail, or to wherever an
  inherited `AUDIT_LOG` variable pointed, and no longer prints a write error beside its verdict.
- **`git stash --all` / `-a` is treated as the wipe it is** (present in 3.x too): it stashes the
  ignored files and then deletes them, and in the game folder the ignored files are the
  installation. Bare from an X4 folder it is blocked like `git clean -fdx`; with the folder named
  (`git -C <folder> stash -a`) it asks. `git stash -u` asks with the folder named, like
  `git clean -f`. A plain `git stash`, `stash list`, `pop` and friends are unaffected.
- **A commit message written with `git commit -m "$(cat <<'EOF' ... EOF)"` is text again**
  (present in 3.x too): a message that mentioned `> KNOWLEDGEBASE.md` was refused as a write to
  it, because a heredoc inside `"$(...)"` had its lines read as commands. The reverse gap is
  closed with it: `x=$(bash <<'EOF' ... EOF)` really runs its body, and the guards now check it.
- **An unquoted heredoc's `$(...)` and backticks are checked** (present in 3.x too): with
  `<<EOF` (not `<<'EOF'`) the shell runs them while writing the text, and a game-folder delete
  hidden there was allowed. The rest of the body is still treated as text.
- **Security: five ways past the hard blocks are closed** (present in 3.x too; found in the
  release review, each reproduced first). Each let `rm -rf <game>` run as ALLOWED: (1) a heredoc
  marker (`<<EOF`) inside a quoted string spanning lines, which hid every later line; (2) a
  heredoc inside `$(` ended by `EOF)`, after which bash runs the next line; (3) a heredoc piped
  into a shell on a LATER line (`cat <<EOF |` ... `bash`); (4) `cmd` with no `/c`, which reads its
  program from stdin; (5) an ANSI-C string with an escaped quote (`$'it\'s'`), which no scanner
  closed (BLIND-SPOTS F191).
- **Security: nine more ways past the hard blocks and asks are closed** (found in the delta
  review, each reproduced first; (1), (2), (3) and (7) present in 3.x too). (1) `<<` inside `(( ))`,
  `for ((;;))`, `$[ ]`, `${ }` or `a[ ]=` -- a shift -- opened a heredoc that hid every later line;
  (2) `echo rm -rf <game> | bash`: what `echo`/`printf` prints into a shell is now its program, and
  a format that cannot be reproduced asks; (3) `bash <(echo ...)`, `source <(...)`: a process
  substitution that IS the script is its program; (4) `cmd /k <cmd>` and `cmd /c cmd` read further
  commands from stdin; (5) a root variable under a brace operator (`${X4_GAME:-/x}`) is its root;
  (6) git configuration from the environment (`GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`,
  `--config-env=...`) makes a `git clean` forced; (7) `x=$(printf rm); $x -rf <reference>`, a verb
  from a variable holding a substitution, is an unresolved verb; (8) `takeown` of the reference
  tree asks, like `icacls /setowner`; (9) a `file://` operand is percent-decoded (`X4%20Foundations`
  hid the game folder). A substituted delete verb below the game folder gets the X4-folder delete
  advisory back, and `x4refguard.py apply --toolkit "$X4_TOOLKIT"` no longer asks (only the action
  word decides; `remove` still asks). A process substitution fed by any other program (`bash <(curl
  ...)`) runs unjudged, like a script file (the maintainer's decision, 2026-10-06; 4 of 44,009 past commands),
  and `bash -n <(...)` never asks (BLIND-SPOTS F201, F202, F206-F212, F183, F219).
- **Security: six more ways past the hard blocks and asks are closed** (fix lane FX-G5, each
  reproduced first; (1) and (6) present in 3.x too). (1) `A=$(echo a b) rm -rf <game>`: an unquoted
  multi-word substitution in a prefix assignment no longer names the command; (2) `A=$(true) $x`
  no longer hides a substituted verb; (3) `bash <(echo -n ...)`, `bash <(...) -n` and `bash -o
  pipefail <(...)`: only the shell's own options count, and an option's value is not the script;
  (4) a `cd ''$X4_REFERENCE''` before a shell patch is an expanded `cd`, refused; (5) `git -c
  include.path=...`, any `--config-env`, or `git config clean.requireForce false` in the same command
  make a `git clean` forced (ask); (6) `rm -rf "${PATH:+$X4_GAME}"`: the alternate word of a
  variable the command did not assign is judged (BLIND-SPOTS F230-F235). Two of these fixes opened
  holes of their own, closed in the next entry: (6) judged the alternate word ALONE and lost the
  empty one, and (3) took a lone `-` as the script.
- **Security: nine more ways past the guards are closed** (fix lane FX-G6, reviewer K, each
  reproduced first). (1) `rm -rf "${NOPE:+zz}<reference>"` (and `${NOPE+zz}`, `"${NOPE:+./junk}<game>"`)
  was ALLOWED after FX-G5: a variable the command did not assign is now judged under EVERY value it
  can have -- unset, empty, set -- for `+`, `-` and `=` with or without the colon, each name on its
  own, also inside `bash -c`, `eval` and `cmd /c`; (2) `bash - <(echo rm -rf <game>)`, `sh - <(...)`, `bash -x - <(...)`: a lone `-` ends the
  options and the next word is the script (also allowed after FX-G5); (3) `bash -n +n <(...)`: `+n`
  (and `+o noexec`) turns `-n` back off, so only the last toggle counts; (4) `A=${X:-echo a} rm -rf
  <game>`, `A=$(echo a; echo b) rm -rf <game>` and `env -C $(echo /tmp ) rm -rf <game>`: a word
  holding a `$(...)`, backtick or `${...}` span is one word, and a substitution the command
  splitter cut no longer hides the command after it; (5) a shell patch after `cd '<reference>'/sub`
  or `cd "<reference>"'/sub'` (mixed quoting) is refused, not read with its quotes; (6) ANY `git
  config` that may write (`"clean.require""Force"`, `clean.require\Force`, a key in a variable)
  and a `HOME=` / `XDG_CONFIG_HOME=` prefix make a following `git clean` forced (ask); a read
  (`--get`, `--list`, `--show-*`, `get`, `list`) does not; (7) an OpenCode `edit` of a Claude Code
  settings file is applied to the file and the RESULT judged, so two innocent edits can no longer
  build `X4_GUARD=off`; (8) the settings rule gives a text apply_patch semantics only when the
  caller says it is a patch (`x4guard check --patch`), never by spotting `*** Begin Patch` in it;
  (9) an `X4_CONFIG` with a `::` stream suffix other than exactly one trailing `::$DATA`
  (`x4-paths.env::$DATA/.`) names no config in the tools and `x4doctor`, as in the guards
  (BLIND-SPOTS F241-F249).
- **Security: three more ways past the guards are closed** (fix lane FX-G7, found by replaying
  432 earlier probe inputs through the hooks before and after). (1) `cd "${NOPE:+x}<reference>"
  && rm -rf libraries` was only an advisory after FX-G6 when run from the toolkit or the game
  folder: a `cd` whose target has several possible values now keeps EVERY resulting directory
  (also `pushd`, and inside `bash -c` / `eval`) and a relative path is judged against each, the
  strictest verdict winning; (2) `export GIT_WORK_TREE=<X4 folder>; git clean -fdx` (or
  `declare -x`, or `GIT_DIR=`) from a folder outside the X4 folders was allowed: it now asks, like
  every other way of naming the folder (`git -C`, `--work-tree`, a `GIT_WORK_TREE=` prefix);
  (3) `git clean -fdx -n --no-dry-run`, `-n --no-d`, `-e -n` and `--exclude -n` delete (measured)
  and were read as dry runs: the last of `-n` / `--dry-run` / `--no-dry-run` now counts, and a
  `-n` that is the pattern of `-e` / `--exclude` is a pattern. `git clean --dry` / `--d` stay
  allowed: git takes a long option's prefix only when it names exactly one option
  (BLIND-SPOTS F250-F252).
- **A Codex or OpenCode patch run through the shell after `cd $X4_REFERENCE`** (or `$env:...`, or
  any `cd` whose target the shell expands) is refused with a reason; it was judged against the
  literal text `$X4_REFERENCE` and allowed (BLIND-SPOTS F203).
- **An agent may not switch the guards off through a Claude Code settings file** (the maintainer's decision,
  2026-10-05). A `.claude/settings.json` `env` block reaches every hook (measured on Claude Code
  2.1.290), so `"env": {"X4_GUARD": "off"}` written there turned the guards off at the next
  launch. Now: a file-edit tool's write is judged on the RESULTING file, and setting `X4_GUARD` or
  `X4_GUARD_CHECK` anywhere in the file is refused -- in `env`, in a hooks command
  (`X4_GUARD=off bash .../protect-bash.sh` switched one hook off) or any other key or string --
  except inside the top-level `permissions` block, where a rule string sets nothing (the
  maintainer's decisions, 2026-10-06). An Edit whose `old_string` does not match the file is judged on its new
  text. A shell write to `.claude/settings*.json` is refused (use the file-edit tool, whose
  content can be checked); Codex and OpenCode patches are judged on their raw text, so a patch
  adding a `permissions` entry that mentions `X4_GUARD` is refused too; every other settings edit
  is unchanged. The session start names any settings file that sets it. Not seen: a settings file
  written from inside an interpreter (`python -c`) or by a relative write from an unknown folder;
  for other settings changes see *Known limitations* (BLIND-SPOTS F193, F198, F204, F205, F220).
- **A root variable is its root for every rule**, not only after `cd` (F154): writes such as
  `> "$X4_TOOLKIT/x4-paths.env"` or `cp x "$X4_REFERENCE/..."` (and `$env:X4_REFERENCE` in
  PowerShell) were allowed where the literal folder was refused -- 34 of 40 Bash and 6 of 8
  PowerShell probes; now 0 of 48, and the two spellings agree on 126 of 126 probes. `rm -rf
  "$X4_GAME"` gets the game hard block instead of an advisory (BLIND-SPOTS F184).
- **Windows path aliases are the file they open**: trailing dots or spaces, NTFS stream suffixes
  (`::$DATA`, `:alt`) and 8.3 short names (`PROGRA~2`) reached `x4-paths.env`, `reference/` and
  the game folder past the guards (19 of 28 probes; now 0; BLIND-SPOTS F185). An 8.3 alias is
  also expanded the same way on BOTH sides of a comparison: a write target qualified by a
  provider (`FileSystem::`), an unresolved write stem or a pipeline target kept its short form
  while the configured root was expanded, and missed the protected folder (BLIND-SPOTS F260).
- A command substitution inside arithmetic (`$(( $(rm -rf <game>) + 0 ))`) now meets the hard
  block (F186), and an abbreviated git option (`git stash --a`, `git clean --fo -X`, `--har`) is
  the option it abbreviates (F187).
- **A patch run is judged however it is spelled**: `codex --codex-run-as-apply-patch '<patch>'`
  is read as a patch, and every other spelling of a patch run (`{ apply_patch`, `FOO=1
  apply_patch`, `bash -c`, quoted names) is refused rather than passed (11 of 11 forms were
  unseen; BLIND-SPOTS F188). A runner name built from a variable is still not recognised (F199).
- `git --work-tree`/`GIT_WORK_TREE`, `--git-dir`/`GIT_DIR` and `-c clean.requireForce=false` name
  or force a git wipe; `icacls /grant`, `/inheritance`, `/restore`, `/setowner` and `/substitute`
  on the reference tree, and an x4refguard action passed through `xargs` or a variable, ask
  like `/remove` (BLIND-SPOTS F192). The folder may be spelled as an absolute POSIX or Git Bash
  path (`/home/<you>/toolkit/reference`, `/c/...`): every token starting with `/` used to be dropped as
  a switch, so `icacls /c/.../reference /reset /T` was allowed (BLIND-SPOTS F257). Git Bash's
  doubled-slash switches (`//reset //T`, which icacls receives as `/reset /T`) and every
  `/inheritance` spelling icacls accepts (it takes any switch that begins with `/inheritance`,
  e.g. `/inheritancezzz:r`, MEASURED) ask the same way (BLIND-SPOTS F265).
- A path-config line holding non-ASCII whitespace (a no-break space) is refused by both loaders;
  it used to configure the tools while the guards ignored it (BLIND-SPOTS F190).
- The Windows Codex wrapper refuses an array-valued decision, as the bash wrapper already did
  (BLIND-SPOTS F195).

### Configuration

- With no path config the guards no longer assume `<toolkit>/reference` silently (see `x4doctor`
  above). The bash and Python loaders now pick the config file by one rule, pinned by a matrix
  test: Python honours `$X4_CONFIG` (naming no file reads none) and no longer walks up past a
  named `$X4_TOOLKIT`. `scripts/generate-baseline.sh` uses the shared loader instead of its own,
  in which the file beat the environment.
- `setup.sh` never creates a blank root config that would shadow a 3.x one, and
  `bin/unpack-reference.sh` works in a Codex-only or OpenCode-only install.
- **How `x4-paths.env` is read** (the guards and the Python tools, one rule):
  `KEY="value"`, `KEY='value'` or `export KEY=value`, with `KEY` an `X4_*` name or `XRCATTOOL`.
  `$NAME` and `${NAME}` still expand, from earlier lines and then the environment; inside single
  quotes, or written `\$`, they stay literal. A Windows line ending and a leading byte-order mark
  are ignored. A line with `$(`, a backtick or an unquoted `;` `&` `|` `<` `>` is ignored.
  Everything the installers, `setup.sh` and `x4-paths.env.example` write reads as before. An
  exported value now beats the file for every key in the guards too, as it already did in Python.
- `X4_CONFIG` spellings with `.`/`..` components (`x4-paths.env/.`, `sub/../x4-paths.env`) or a
  `::$DATA` suffix now name the same file in the guards, the tools and `x4doctor` (BLIND-SPOTS F236).
- The installers no longer copy a source machine's `.claude/.reference-buildid`, which made
  `x4doctor` read a fresh install's missing reference tree as MOVED (BLIND-SPOTS F237).
- `x4guard check --kind write --content "<whole file>"`: a whole-file write tool passes the new file,
  judged exactly as Claude Code's Write. ADAPTING.md now says a `.claude/settings*.json` write must
  carry its text (`--content`, or `--command` for a patch); a Codex `Add File` patch to a settings
  file is judged on what it writes (BLIND-SPOTS F239).

### Tools

- BaseX validates attribute QNames and certifies raw zeros only for a restricted whole-database
  content-search grammar; other raw queries still report positives, and unrecognized zeros return
  4. `refs`, `attr` and `xq` accept optional `--limit` / `--offset` over whole items (unlimited by
  default, totals and negative checks over the full result); unsupported paging returns 2.
- The xref index validates every TSV row and binds newly built indexes and exclusions to SHA-256
  digests. Unsigned legacy positives warn; uncertifiable absence returns 2.
- Invalid DEFLATE saves and corrupt or incompatible effective SQLite stores return 2 without
  tracebacks; SQLite filenames escape URI characters and stay read-only; malformed freshness
  vectors return 2. These reader changes invalidate existing engine freshness fingerprints:
  rebuild the artifacts before making fresh claims.
- `gen-cli-reference.py` refuses (rc 2) on a committed reference file that is not UTF-8, in both
  modes, instead of a traceback that read as "stale". In write mode a leftover ("ghost") file
  in the generated reference folder is named, with the `git rm` to run, and the run exits 1; it
  used to delete the file.
- `run-gates.sh` keeps every gate's full output in `<logdir>/<gate>.log` and the machine's free
  memory, load and CPU count in `<logdir>/system.txt` (GitHub issue #3). Set `X4_GATE_LOG_DIR` to
  choose the folder; one inside `X4_GAME` or `X4_REFERENCE` is refused, also when reached
  through a symlink or junction (BLIND-SPOTS F181).
- CI checks that `uv.lock` matches `pyproject.toml`.
- `x4debug triage` no longer calls a session a save load just because the log has no
  galaxy-generation marker: a menu-only startup has none either. Such a session is now
  "UNKNOWN (menu or save load)"; a confirmed new game keeps its label (BLIND-SPOTS F258).
- The `cross-file-impact` subagent names the real `x4-balance` and `x4-mod-interaction` skills
  instead of an advisor that does not exist; both subagents run on `sonnet`. `x4-scaffold` treats
  `content.xml` edits as advisory, matching `CLAUDE.md`. `.gitignore` no longer swallows a skill's
  `reference/` folder under `agent/skills/`.

### Release-review fixes: installers, x4doctor, tools

- `x4doctor` no longer reports UNKNOWN rows on a healthy fresh install (both installers, separate
  and in-game, `--agent claude`, `--no-env`, an unpacked `reference/`: exit 3 before; now exit 4,
  its one open row the `layer2.reference` TODO, your step). On an installed toolkit, `parity.*`
  is N/A, because parity checks a tree `deploy-claude-dir.py` copied from a source checkout. A
  target that no deploy workflow ships (`.codex/`) is N/A, and the row says why. An unlocked
  x4lock manifest is OK and marked as your choice. The x4lock row now asks the real
  `x4lock.py` with the root's environment. `layer2.reference` judges every root alike, whichever
  agents it has: no protection is a TODO row (your step; exit 4), only partial protection a
  FAIL; it says the Claude hooks cover deletes made through Claude only when
  `.claude/settings.json` really wires the delete guard, and prints `x4refguard.py apply` by
  absolute path (the x4lock hints name the destination's own `x4lock.py`). An `X4_GUARD` line in a path config fails as **ignored**: the guards take
  `X4_GUARD` only from the launch environment.
- `x4lock` locks a **separate** install's guards, `CLAUDE.md` and settings in the toolkit
  folder, and no longer expects them in that install's game folder. A source checkout is never
  locked this way. The mod registry is expected only once its folder exists.
- `install.ps1 -Help` exists and prints the same options as `install.sh --help`. Windows
  refuses a bare `.\install.ps1` by default, so run it as
  `powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Help`.
- The installers never change OS permissions for you. When an existing `reference/` is not
  OS-protected, they print the state and `python scripts/x4refguard.py apply`.
- Before setting `X4_TOOLKIT`, the installers read every place it may already be set: the
  user and machine environment on Windows, and every bash/zsh startup file (Git Bash's on
  Windows). They report each value found, and they write nothing next to a value that
  differs.
- A dry run now also lists the Codex `hooks.json` render, the OpenCode deny-rule render, the
  `.codex/config.toml` prepend and, in every layout, what would happen to `X4_TOOLKIT`. A
  read-only, out-of-date `.opencode/opencode.jsonc` now stops the install before anything is
  written.
- The OpenCode skills' `{{TOOLKIT}}` becomes `$X4_TOOLKIT` on Windows when
  `X4_OPENCODE_SHELL=bash` (set it only when OpenCode's own `shell` is bash: the plugin refuses
  every `bash` call while the two disagree). Codex and generic skills keep `$env:X4_TOOLKIT`. Both installers
  now write derived paths (`<toolkit>/reference`, `<profile>/debug.txt`, `<game>/extensions`)
  with `/`.
- `deploy-claude-dir.py` exits 1 when two path configs differ; it used to report them and
  exit 0. Every message about two differing configs now names
  `x4config.py status --root <that toolkit>`.
- `bin/unpack-reference.sh` stops when x4refguard cannot read the protection state (`error`),
  and also when it gives no answer or one that is not a known state (no Python, a crash,
  unparseable output); those used to proceed as though the tree were unprotected.
- `x4xref`: a negative result over an index that is missing installed DLC exits 3 (degraded)
  and is never presented as a certified absence.
- `x4effective`: a broken pipe or a command's own error is no longer reported as "store
  unreadable, rebuild". A store on a UNC path (`\\server\share\...`) now opens. `x4live
  mappings` and the `cross_tool` gate refuse an unusable store instead of crashing with a
  traceback. `x4live mappings` and `oracle` close the store's connection on every path, and
  `oracle` refuses (exit 2) instead of a traceback when the store's freshness cannot be read.
  The `cross_tool` gate exits 2, not 0, when the store cannot be opened for its read-only check
  (it printed "NOT RUN" and passed; BLIND-SPOTS F164).
- `tools/basex/ask.py --help` runs without `lxml`. A `--limit`/`--offset` query that returns
  attribute nodes is now counted and paged instead of refused.
- Python tools say when `X4_CONFIG` names a missing file. The `_merge`/`_registry` refusals
  name `<toolkit>/x4-paths.env`. Both modules are freshness engine sources, so this changes
  the engine hash; this release already invalidates built stores (34d3fde), so nothing extra
  needs rebuilding.
- Gates: `run-gates.sh` checks `X4_GATE_LOG_DIR` against the roots the tools resolve,
  including roots named only in the config, and no longer needs `realpath` (missing on
  macOS). `edge_sweep`'s unconfigured cells blank every path variable and select an explicit,
  EMPTY `X4_CONFIG` (blanking alone still found the toolkit's own config, BLIND-SPOTS F254); the
  state with no config file at all is `verify-cold.sh`'s. `hook_false_positives` hashes the `X4_CONFIG`
  file, under labels that name each file's location. The internal audit harness
  (`tools/x4validate/audit/`) is left out of the release zip. `scan-identifiers.py` also
  catches a bare X4 profile id. The ids come from the machine it runs on and are never
  printed: Windows `Documents` (plain and OneDrive) and the native Linux client's
  `~/.config/EgoSoft/X4`; a location that exists but cannot be read makes the scan say PARTIAL
  and exit 3 when it found nothing (it exited 0 -- a partial walk reported clean); its advice
  names only a remedy that works. Every identifier on a line is checked: a placeholder earlier on
  the line hid a real one later (BLIND-SPOTS F175, F182). On macOS a privacy-protected
  `Documents` may read as unreadable (exit 3; not verified on a Mac, F228).
- **The commands that change your system also refuse an exported ROOT that differs from the
  toolkit's config**: `x4refguard apply/remove`, `x4lock lock/unlock` and
  `bin/unpack-reference.sh`, when an exported `X4_REFERENCE`, `X4_GAME` or `X4_MODS` names a
  different folder than the acting toolkit's `x4-paths.env`. The environment outranks the
  config, so `x4refguard apply --toolkit B --yes` with A's `X4_REFERENCE` exported protected A's
  tree, exit 0. The refusal names both folders and the flag that chooses one (`--reference`,
  `--game`, `--registry`); `status` prints one line. **The same commands refuse while an
  inherited `X4_CONFIG` names a config outside the acting toolkit, or a file that does not
  exist** (the maintainer's decision, 2026-10-06: keep the refusal); `status` prints one notice line.
  `--reference` lifts it for `x4refguard`, which acts on that one tree. Nothing lifts it for
  `x4lock` (its manifest locks the config `X4_CONFIG` picks) or for `bin/unpack-reference.sh`,
  which reads six keys from the config that no flag can choose (with `--reference` alone it
  filled the tree from the OTHER config's game). The refusal names the way out: clear
  `X4_CONFIG` (BLIND-SPOTS F177).
- **Bash and Python read the same file for every `X4_CONFIG` spelling.** `x4-paths.env/` and, on
  Windows, `x4-paths.env.` or `x4-paths.env ` were read by the Python tools and "missing" to the
  guards, which fell back to the default reference root (6 of 16 spellings disagreed; now 0).
  `bin/unpack-reference.sh` resolves a relative `X4_CONFIG` or `X4_REFERENCE` against the
  current folder; it turned one into `/<name>` and refused it (BLIND-SPOTS F224, F225).
- **`install.ps1` works under Windows PowerShell 5.1** when a Python step writes a warning: 5.1
  made redirected native stderr a terminating error, so the read-only OpenCode check died with a
  raw error (exit 1) instead of its refusal, the OpenCode render failed on any warning, and the
  summary said "no reference/ tree yet" when the protection state could not be read (now said as
  such, by both installers). A completed `install.ps1` exits 0 explicitly; it used to leave the
  last native command's code (2) in `$LASTEXITCODE` (BLIND-SPOTS F179).
- **An `X4_TOOLKIT` inherited from the environment is never a destination you named.** The
  installers say where the default came from; `--yes` / `-Yes`, or no terminal, refuses and
  names its source; the over-existing refusal offers no rerun line for it; `--method global`
  refuses one that names another toolkit. Every inherited path variable used is printed before
  it is written. The check for an existing `X4_TOOLKIT` also reads the global Claude
  `settings.json` env block and, on Windows, the process environment, each reported only when
  it differs. **An inherited `X4_GAME` is not a named in-game destination either**: under `--yes`
  or with no terminal the installer refuses without `--game`, naming `X4_GAME` (BLIND-SPOTS F223).
- `install --unpack` runs the unpack against this run's own config, game and reference
  (`--reference` passed), not whatever the shell inherited. Every refusal and lift step of
  `bin/unpack-reference.sh` prints an absolute command with `--toolkit` (and `--reference`), and
  the stale-reference message at session start gives its steps the same way (BLIND-SPOTS F222).
- Both installers pass `--toolkit` to `bin/unpack-reference.sh`. `setup.sh` reads the config
  with the guards' own loader (one grammar). `--agent` also accepts a whitespace-separated list,
  like `-Agent`. `scripts/codex-e2e.py` fails the validator row on "X4 VALIDATION DID NOT RUN"
  instead of passing it.

### Known limitations (recorded, not fixed in 4.0)

- **Settings and the `.claude` folder can still be used to weaken the guards** (the maintainer's
  decision, 2026-10-06: record only for 4.0). Measured allowed: `"disableAllHooks": true` in a settings
  file; `X4_CONFIG`, `X4_GAME`, `X4_PYTHON` or `PATH` in a settings `env` block, which moves every
  root or the guards' interpreter; a write through a directory junction to `.claude`; and replacing
  `.claude` from the shell (`mv`, `cp -r`, `ln -s`). The OS-level `reference/` protection, once
  applied, still blocks a delete inside it from an ordinary (unelevated) process, whatever the
  guards do; it does not stop an Administrator (next item; BLIND-SPOTS F221).
- **The `reference/` protection does not stop an Administrator or root.** Measured on elevated
  hosted Windows: Git Bash changed protected scratch trees in three of three probes while the
  backup and restore privileges were available to the process; the same three probes were
  blocked once both privileges were removed from it. A disabled privilege can still be available
  to a process. Run your agent unelevated (BLIND-SPOTS F264).
- Two false refusals fail closed: a `..` in a not-yet-existing part of a path given to the unpack,
  and an `X4_REFERENCE` ending in a dot, can read as a different tree; the refusal prints both
  paths (BLIND-SPOTS F229).

### Maintainers

- `scripts/run-release-gates.py` exits **3** while any stage is pending (deployment parity) or
  was not run (`--through vht`), computed from the checkpoint so `--resume` cannot turn a
  pending stage into a pass; 0 means every stage is recorded passed (it exited 0 in both cases;
  BLIND-SPOTS F266). Its report directory, and `run-pytest-batches.py`'s, is refused inside the
  game, reference or profile root as the tools resolve them (environment, then `x4-paths.env`);
  only the exported variables were checked, and a root that cannot be resolved now refuses
  (BLIND-SPOTS F267).
- `scripts/verify-cold.sh --archive` runs the cold suite over a `git archive` extract with no
  repository visible (`GIT_CEILING_DIRECTORIES`, refused otherwise): the release zip's
  population, which F262's move to a clone had dropped. The CI workflow runs both legs, on
  Linux (BLIND-SPOTS F269).
- **The suite an in-place install of the release zip runs (SETUP_PROMPT) no longer fails on a
  fresh install**: the test layout check took the zip's `agent/` folder for a source checkout,
  so 26 "not unpacked / not set up yet" cases failed instead of skipping, and three tests needed
  a Git repository the zip does not have (MEASURED: 29 failed, 8 errors over 238 of 239 test
  files). A checkout is now a `.git` whose HEAD carries `agent/`; the three ask the shipped
  `.gitignore`/`.gitattributes` without Git metadata (BLIND-SPOTS F269).
- **An in-place install's guards are locked by `x4lock`, and `x4doctor` reports it as an install.**
  Both took the zip's `agent/` folder for a source checkout: `x4lock lock` left the toolkit's own
  guards unlocked (MEASURED: 1 file locked, now 131 including the 14 hook files) and `x4doctor`
  ran its source-checkout parity check instead of N/A. A source checkout is now a `.git` whose
  HEAD carries `agent/`, decided in one place (`scripts/toolkit_layout.py`); a real checkout
  behaves as before (BLIND-SPOTS F269). That decision asks Git about the folder itself, whatever
  `GIT_DIR` and similar variables say and whoever owns the folder: either one made a real
  checkout read as an install. When Git cannot answer at all, `x4lock` still locks but says why,
  and `x4doctor` reports parity UNKNOWN rather than N/A (F277).
- `run-gates.sh` expands its possibly-empty arrays as `${a[@]+"${a[@]}"}`, which bash before
  4.4 (macOS's 3.2) requires under `set -u` (READ, not reproduced; BLIND-SPOTS F268).
- `gates/edge_sweep.py` says what its "unconfigured" cells are: every path variable blank and an
  explicit EMPTY config, not the no-config state, which `verify-cold.sh` covers.
  `gates/instrument_hygiene.py` documents how to re-measure the SAME baseline population after a
  matcher change (it refuses until then; no new tool).
- `verify-hook-tests.py`'s silent? column keys test sources by `Class.method`, so two classes
  sharing a test name no longer pool their sources (BLIND-SPOTS F238).
- After tagging a release, run `tools/x4validate/scripts/gen-shipped-hashes.py --write` and commit
  the result; `test_shipped_instruction_hashes.py` fails until you do. v4.0.0 is the first tag to
  ship an `AGENTS.md`, so this is what lets the next release replace it instead of moving it
  aside.
- `scripts/build-release.sh <tag>` now writes `dist/X4.Foundations.AI.Assistant.Toolkit-<tag>.zip`.
- The release bundle leaves out the maintainer's implementation plans and session handoffs
  (`docs/superpowers/plans/`, `docs/handoffs/`; still in the repository). The measurements the
  docs cite as evidence still ship, now including the 53,828-command replay behind the
  relative-path fix (`docs/superpowers/measurements/2026-10-02-cwd-seed-replay.md`).
- `KNOWLEDGEBASE.md` gained no toolkit development notes in this release: five that the 4.0
  work had added to its Session Log moved to `docs/AUDIT-framework-2026-10-01.md` and
  `docs/audits/2026-10-02-tool-fixes.md`.
- `x4guard conformance` refuses (exit 3) when the guards checked nothing -- an inert guard agreeing
  with an inert adapter is not a pass -- and refuses (exit 2) when the extra cases were wanted but
  could not be built, instead of passing without them. It also refuses (exit 2), naming each
  case, when a reference guard hook cannot start, times out or exits non-zero: such a run had
  decoded as a checked allow and could pass (BLIND-SPOTS F174). A re-run under `X4_GUARD_CHECK`
  that fails with anything but exit 2 is an error too; it used to fall back to the first run's
  verdict and count as checked (F216). It finds Git Bash with the one resolver the guards use.
- Codex: a timed-out guard's MSYS descendants (a backgrounded `sleep` under Git Bash) are now killed
  too, through a Windows Job Object; the adapter's backup, validator and session hooks use the same
  bounded runner; the session-start hooks share one 18 s budget inside the wrapper's 25 s; the
  Windows wrapper checks Codex's output keys case-sensitively, as Codex does; a patch sent under
  `input` is backed up and validated like one under `command`.
- `scripts/capture-codex-patch-oracle.py` re-records Codex's own apply_patch behaviour; run it on
  a Codex upgrade.
- **The test suite can no longer pass a broken checkout by skipping.** A skip for a configured
  but missing root, or for a reference that is named but not unpacked, now happens only in an
  INSTALLED toolkit; in a checkout a gate's import error is an error and a broken named
  reference fails. "Installed" no longer means just "no `agent/`": a checkout that lost `agent/`
  (it has `.git` and `git cat-file -e HEAD:agent` succeeds) is a checkout, while an install inside
  the game folder's own git repo is still an install, with no false failures from that repo. A
  missing `scripts/codex-e2e.py` or `scan-identifiers.py` fails instead of skipping; the ADAPTING
  worked example can no longer be dropped silently, and a bare `install.ps1` run in the user docs
  is caught (BLIND-SPOTS F165-F170).
- Tests: the installed-suite test says it proves collection only, with its measured cost (~31 s,
  ~0.1 GB; F171); the Codex adapter test requires the hook's grandchild process to be reaped; the
  toy-conformance mutants use only cases this platform can replay (F172); the WSL-bash test
  asserts the platform's own reason; the x4doctor and x4refguard tests no longer read the
  checkout's per-machine config (F180). No CI install leg that runs on Windows -- PowerShell or
  bash -- writes the runner's real user environment any more: each uses its own test registry key
  and drops the inherited `X4_TOOLKIT` (F227).
- `scripts/verify-hook-tests.py` credits a test to a predicate only when the test names it (one
  catch-all test had been credited to all 31; F196), and credits a mutant only when the
  qualified `Class.method` it targets fails (3 of 618 test names exist in two classes; F197).
  `scripts/test-hooks.sh` keeps its decision dump on for the path-config section, so the Codex
  conformance replay sees those denies too (F194). Its silent? column found a test for none of
  the 32 predicates (a `Class.method` name looked up in a bare-name index; F217), now fixed.
  `scripts/fuzz-guard.py` gained 7 seeds and 6 syntax-class mutators for the delta-review rules;
  their first run found 13 weakenings, all fixed; the second, 5,311 mutants over 47 seeds x 113
  mutators, found none.

## v3.3.1 — 2026-09-29

A CI-and-tests release: **no tool changes behaviour.** v3.3.0's own CI went red on three
jobs, none of them a defect in a tool, all of them in how the release was tested:

- **The history identifier scan checked nothing on a tagged release commit.** Its base was
  `git describe --tags`, which on the release commit IS the new tag, so the range was empty
  and the scan refused to certify it. The base is now the last tag before HEAD.
- **16 new tests of gate logic never ran on a machine with no X4.** They imported a gate that
  refuses at import without an install, so on CI they SKIPPED -- the job went over its skip
  ceiling (69 > 56), which exists to catch exactly that. They now run against a hermetic
  fake install (`conftest.gate_install` / `hermetic_gate`); v3.3.0's Windows CI skipped 69,
  this release's skips 50. The fake install no longer leaks into the rest of the session.
- Two tests assumed Windows: one expected `Set-Acl` (a Windows-only cmdlet) to be denied on
  Linux, where the guard fails closed to ASK because the cmdlet cannot be bound; one faked
  `os.name = "nt"` process-wide, which crashed pathlib on Linux.
- A provenance-floor test could never reach the floor it names (a stale-store refusal fired
  first); it now does, and a mutant disabling the floor goes red.

## v3.3.0 — 2026-09-27

The remediation of the 2026-09-24 correctness audit. Every finding, its status and its commit
are in `tools/x4validate/AUDIT-2026-09-24.md`; the defect classes are BLIND-SPOTS F128-F145
(F141's addendum and F142-F144 come from this release's own pre-release review, summarised
in the same file). Numbers below are taken from the commit that made the change.

### ⚠ Load order now follows the ENGINE — collision winners can change

- **`compute_load_order` implements the order the engine actually uses**, and this changes
  which mod wins a collision in `x4compat`, `x4effective`, the effective store, BaseX `x4eff`
  and Tier B. The engine walks extension folders in **case-insensitive UPPERCASE order** (`_`
  sorts after letters, a space before `_`) in **repeated passes**: each pass loads every mod
  whose installed dependencies have already loaded, including earlier in the same pass. The
  previous rule (ASCII case-sensitive sort, each mod as early as its dependencies allow) was
  the community convention and had never been compared with the engine. Measured against the
  engine's own `debug.txt` signature-check order over two launches: **372 file classes, 5,134
  ordered pairs — the old rule inverted 685, the new one 0.** Every existing winner test passed
  under both rules, so new tests pin the difference.
- **Tier B places a mod that is not installed by the same rule** (its folder name + its own
  manifest's dependencies) instead of assuming it loads LAST, which hid ops the engine skips.
  Packed and unpacked DLC now sort by the same key.
- **Apply order is MEASURED too.** The in-game `scripts/load-order-probe.py` run (2026-09-26)
  passed: signature order 12/12 as predicted, and **apply order 3/3** -- its collision pairs
  `<replace>` an attribute another probe `<add>`s, and the replace succeeded exactly when the
  adder loads earlier. A folder with `ß` loads after one with `sz`; all DLC load before
  mods. The profile's `extensions\` root is measured too (below). Still unobserved: the
  Steam Workshop root, non-NTFS filesystems.
- **Which mods count as LOADED now follows the engine** (`mods("active")`, used by Tier B,
  x4compat, x4stats, x4effective, BaseX `x4eff` and the gates), MEASURED by the same probe:
  a mod whose **REQUIRED dependency is missing, disabled or part of a cycle is NOT loaded**
  (an optional one never excludes; a `<dependency>` naming only a game version is ignored);
  a profile entry **without an `enabled` attribute loads**; a manifest `enabled="0"` with a
  profile entry `enabled="true"` **loads** (the profile decides, the manifest is only the
  default); two folders sharing one id both load. Installed DLC count as dependency
  providers, taken from the configured reference and the packed DLC of the install -- so
  `--ext-dir <a folder holding no ego_dlc_*>` no longer silently drops every mod that needs
  a DLC. **Every tool and gate that uses this set now NAMES each mod left out and why**
  (BLIND-SPOTS F139): Tier B, x4compat and the variant / nested-script checks as a NOT
  CHECKED line, x4save check likewise, `x4effective build`/`dump`, x4similar, x4stats and
  BaseX build-effective on stderr, `x4live extensions` and the x4live no-connection refusal
  in their output, and the seven gates that read it. Previously 16 of the 20 call sites said
  nothing. A nested patch aimed at a mod the engine leaves out for a missing dependency is
  no longer described as aimed at a "DISABLED" mod -- it names the dependency.
- **Tier B, x4compat and x4stats place the mod under test by ONE rule**: the installed
  copy's folder name when it matches by folder or id (case-insensitive), else its own folder
  name; its dependencies from the copy under test; the installed copy left out. Tier B used
  to match case-sensitively (a case-different dev copy validated against its own installed
  copy) and to order by the installed manifest.
- New: **`gates/load_order_oracle.py`** holds `compute_load_order` to the engine's order on
  every new log (rc 2 when the log describes another modlist). **`scripts/load-order-probe.py`**
  builds throwaway one-op mods with a PREDICTION written before launch, to settle the
  unobserved shapes in game.
- **Rebuild your persisted artifacts once after updating.** The merge engine and the
  freshness fingerprint both changed, so every store and index reads STALE until rebuilt.

- **The game root and the profile root are modelled as the engine treats them** (BLIND-SPOTS
  F141, measured by probe rounds 2-3): mods in the profile's `extensions\` apply AFTER every
  game-root mod, and a required dependency never resolves between the game root and the
  profile root (the engine refuses it in both directions; the model used to count it as
  satisfied). That rule applies ONLY between those two roots: nothing about the Steam
  Workshop root was measured, so Workshop mods keep the earlier pooled model -- dependencies
  resolve to and from them in both directions -- and a run with a Workshop mod present says
  the model is UNMEASURED there. Where a game-root mod that waits a dependency pass falls
  relative to profile-root mods is unmeasured and is disclosed when it applies. No change
  for a single-root install.
- **A dependency id that differs only in CASE is treated as satisfied**, with a note naming
  both spellings (whether the engine compares ids case-sensitively is unmeasured); it used to
  leave the mod out.
- **Load-order notes reach every tool that orders the mod set.** Unmeasured cross-root
  placement, duplicate ids or folders, dependency cycles, a Workshop-root mod and a case-only
  match are shown by Tier B, `x4effective build`/`dump`, BaseX build-effective, x4stats and
  x4similar (x4compat already did), not by x4compat alone.
- **x4compat, x4stats and x4similar scan every configured extensions root by default.** They
  scanned only the game root, so a profile-root mod -- which the engine loads -- was silently
  absent. `--ext-dir` now scans that folder ONLY and says so.
- Help text: an extension is active when its PROFILE entry says so; the manifest's `enabled`
  is only the default (x4compat, `x4validate --tier b`). The x4effective and x4compat
  advisory lines name what is really unmeasured (the Workshop root, case-only ids, a
  later-pass game-root mod beside profile-root mods) instead of calling the measured apply
  order and dependency rules "inferred" or "assumptions".

### Safety hooks

- **PowerShell and cmd.exe carriers closed** (found by the release review; v3.2.0 had no
  PowerShell guard at all). A delete reached through a pipeline, a variable, a loop variable,
  `$_`, `.FullName`, or command output (`Join-Path`, `Get-Item`, `gci`, `Resolve-Path`) now
  DENIES exactly like the literal form, and ASKS when its target cannot be resolved (it used to
  ALLOW). `gci <root> | Remove-Item` denies like `rm -rf <root>/*`. .NET writers
  (`StreamWriter`, `FileStream`, `File.Open` by mode) are modelled, and scriptblock text
  (`[scriptblock]::Create`, `InvokeScript`) is translated like `iex`. `pwsh -cwa`, `-File -`
  and a program piped or here-stringed into pwsh/powershell are translated as PowerShell.
  cmd.exe `/R`, `^`-escaped verbs, `cd /d`/`pushd`, and an UNQUOTED path containing spaces
  (`cmd //c "rd /s /q C:\...\X4 Foundations"`) are handled. A PowerShell command that does not
  parse is a DENY naming the parser error, not a prompt. Bash `xargs`, for-loops, `$(realpath
  ...)` targets, `rsync --delete`, `robocopy /MIR`, and `touch`/`chmod`/`ln` into `reference/` are
  guarded. MEASURED over 50,061 historical commands before shipping: 273 verdicts change, every
  one classified -- 254 are false-positive bare-python denies REMOVED, the rest intended or true
  positives; failing closed on unresolvable WRITE targets was measured (+28 false asks, 0 catches)
  and NOT shipped -- a write target is judged by its text, as Bash's is (BLIND-SPOTS F145).
- **Bare system `python` on toolkit code is denied**, with the `uv run` form to use instead.
  On a machine whose `python` has none of the toolkit's dependencies it fails with
  ModuleNotFoundError or SyntaxError, which reads exactly like a real test failure.
  Scoped to `tools/x4validate/` code, and to `-m pytest` / `-m x4validate` run from a directory
  under `tools/x4validate/` (never another project); `python`, `python3`, `python3.X`, `py`, and an
  ABSOLUTE path to a non-virtual-environment interpreter all count, a venv's interpreter
  (`pyvenv.cfg` beside it) does not. `-c`, stdin, flag-only calls, stdlib-only `scripts/` and
  `.claude/hooks/` are untouched.
- **The PowerShell tool and NotebookEdit are guarded.** Before this, the hooks had no matcher
  for either, so every guard could be bypassed by choosing PowerShell (MEASURED:
  `Remove-Item -Recurse <reference>` ran unguarded). A PowerShell command is parsed with
  PowerShell's own parser and translated onto the same rules Bash uses; a command that does
  not parse, an unmodelled writing cmdlet, an unresolvable splat or `iex` text, or no
  PowerShell on the machine ASKS rather than allows.
- **Bash: a filtered `find -delete` is judged, not exempt** (`find <saves> -name '*.xml.gz'
  -delete` was allowed); `truncate`, `dd of=`, and nested `cmd //c` / `powershell -c`
  commands are judged too; a `find` behind a wrapper with flags (`stdbuf -o L find …`) now
  has its path read. A filter naming only a regenerable cache stays exempt.
- **Hook timeout 5 s → 30 s.** A hook that times out does not block, and the hooks measured
  4.1-7.6 s under load. Per-call process spawns were cut (protect-bash 221-236 ms → 124-126 ms,
  protect-files 726-810 ms → 279 ms); PowerShell translation shares one 15 s budget per call.
- `protect-files` resolves `..` before its `.claude/` whitelist
  (`<game>/.claude/hooks/../../libraries/wares.xml` was allowed); the backup hook handles
  paths too long for a file name; `sed -i` on a game/profile file is a DENY whose message now
  says so (it said "confirm"); `session-canary.sh` uses the shared Python lookup and prints its
  NOT CHECKED line on stdout; the post-edit validator also sees NotebookEdit.

### x4validate

- **Exit code 3 (not "OK", rc 0)** for `--debug <missing file>`, and when `--file` /
  `--sel-only` is given a flag it does not run (`--entity/--like/--update/--debug`) — disclosed
  as a degraded skip, not silently dropped.
- **`--file` (what the per-edit hook runs) now agrees with the full run**: it shares the full
  run's sel check, states its denominator, and lists every check it did not run. It used to
  report a nested patch for an uninstalled target as ERROR rc 1 where the full run said INFO.
- **New ERROR, category `diff`:** a `<diff>` nested inside a complete file (the engine only
  reads a patch at the document root, so those ops never apply). 0 of 7,516 installed files
  carry one.
- Tier B: a nested patch aimed at an uninstalled or installed-but-DISABLED mod is INFO
  "inactive", and references inside it are INFO. Refs in patches on packed-only DLC gate again.
- Tier B: a nested patch on an ACTIVE mod that ships the file but loads LATER is a WARN
  (category `load-order`) naming that mod, saying the engine behaviour is unmeasured and
  advising an optional `<dependency>` on it -- not a "no base game file" path ERROR. The
  Tier B note counts "of N other active" mods (it said "installed").
- A complete t-file overriding strings gives ONE finding per file: INFO over base/DLC strings
  (the rename idiom), WARN per defining mod over another mod's strings.
- Unreadable overlay indexes and module groups that did not merge are degraded skips (exit 3)
  instead of silent; unattributable engine lookup errors are disclosed, not dropped.

### Merge engine (x4validate, x4effective, the store)

- A `text()` selector is refused with a reason instead of a traceback; a text-only `<add>` is
  modelled; `<add type="@q">` with element children is refused instead of blanking `q`; text
  after a removed or replaced element is kept.
- Mod index entries: `extensions/<X>/…` resolves inside mod `X` (it resolved in the
  registering mod); packed-only DLC index files are read.

### x4compat

- **`--all` is removed** (omitting the candidate already means the whole ACTIVE set); help
  says "active", not "installed".
- `check <path>` analyses THAT copy (an enabled copy with the same folder name or id is
  excluded and named); output starts with `Candidate analysed: <path>`.
- When an earlier mod REMOVES a node a later mod replaces, the row now says `removed : by X`
  (the node is absent) instead of naming the later mod as winner. New `--json` fields:
  `removed_by`, `order_misses`, `removed_first`, `candidate_path`, `excluded_copies`, `guard`.
- New sections: **PATCHES A NODE ONLY A LATER MOD ADDS** (an op the engine skips because the
  node arrives later — 39 on the audit install) and **EDITS INSIDE A NODE AN EARLIER MOD
  REMOVED**; `if=`/`silent` ops are marked as intended. Cost, measured on the audit install:
  `analyze()` takes 30.7-32.7 s, against 11.5-12.6 s without this analysis. The order-miss
  detector no longer masks a node one mod adds and a later one removes as a dead selector
  (the bisecting fallback for nested paths keeps that limit, disclosed in its docstring).
- `check <name>`: a bare name always means the installed copy. A path is recognised by its
  shape (a separator, a leading `.`, or absolute), not by whether it exists in the current
  directory.
- `--json` includes `unresolvable` -- the ops whose `sel=` could not be evaluated (the NOT
  CHECKED section the text output already printed).

### x4stats, x4effective, x4similar, x4xref

- x4xref records the `--reference` it was built from and checks freshness against it (an
  index built with another reference used to read STALE forever); x4similar orders DLC by
  the engine's folder key.

- `x4stats wares`: a ware the candidate REMOVES is reported (it printed "changes no wares");
  the candidate resolves against the mods loading BEFORE it, not the whole set.
- `x4effective ls --modified-only` and the `ls` marker mean "a MOD anywhere in the entity's
  provenance" (attribute-only mod changes were hidden, DLC-only entities shown); `who-sets`
  says "WON, not introduced" only when base/DLC define the entity.
- `x4similar` scores ships at their EFFECTIVE (merged) values — `<diff>` ship patches were
  skipped, so 300 of 472 vectors change their scored stats on the audit install; `cargo.max`
  (carried by 0 ships) leaves the weights; unscorable ships are counted in the header.
- `x4xref who-calls ''` is refused (rc 2); cue-edge actions (`signal_cue`, …) are searchable
  by `who-calls`.
- `x4effective ls/attr/diff-mod --limit 0` means unlimited, as the truncation message
  advises (it returned zero rows).

### x4diff

- **Element TEXT is compared** (as a `text()` pseudo-attribute): an edit whose value is text —
  e.g. `<replace sel="…/@min">5</replace>` → 999 — read "changed files: 0". Re-indenting is
  not an edit.
- Diff ops are keyed by payload identity, so inserting one op no longer shows its siblings as
  removed + added, and a selector-only edit is an `@sel` change.
- A stack whose first supplier ships the file as a `<diff>` has a baseline (it read "would not
  parse"); `--file <added file>` says ADDED/REMOVED; "total attr changes" counts attribute
  changes only.
- Text comparison keeps NBSP, ideographic space and other non-ASCII whitespace distinct from
  formatting whitespace, and a single-line value's leading/trailing space is an edit (only a
  segment containing a newline has its ends trimmed); a comment or processing instruction inside text
  no longer splits it into a phantom change.

### x4modlist

- **`refresh` says which mods HAVE AN UPDATE:** upstream's newest MAIN file uploaded after the
  installed manifest's date (version strings are not comparable — 77 rows had an upstream
  version, 1 matched as a string). Verdicts: `available`, `same-release?` (uploaded within
  **3 days** after the manifest date — authors often date a manifest before uploading; the
  window's error rate is unmeasured), `none`, `unknown` (a date missing — never "no update"),
  `unconfirmed` (guessed identity). Both dates are printed; a row pinned to a file follows
  that file's successors; verdicts not re-checked this run are labelled carried-over. The
  dashboard gets an Updates tally and an UPDATE AVAILABLE table.
- **Network failures no longer lose a run:** every transport failure is mapped, the registry
  is saved on any exit, and a run-wide failure (bad key, rate limit, network) stops with exit
  2 and leaves unfetched rows untouched.
- `--registry <typo>` is refused (it created an empty registry); a malformed profile
  `content.xml` is reported (rc 2), not a crash; `verify --rescore` works offline.
- `x4-paths.env`: a quoted value may contain `#`, and adjacent quoted/unquoted parts join as
  in the shell.
- A Steam outage while resolving a Workshop mod's identity no longer overwrites the row's
  existing verdict with "unmatched"; the outage is counted in the run's errors.
  `x4modlist tracked` reports a Nexus failure (rc 2) instead of a traceback.

### x4debug, x4save, x4live

- `x4debug crosscheck` keys each op on patch file + selector (the same selector failing in two
  files collapsed into one; 17 collapses in one real mod), and refuses (rc 2) naming the
  read error when the log is unreadable.
- `x4save info` compares history by extension name and no longer claims what loaded;
  `x4save check` no longer double-counts references at 4 MiB read boundaries.
- `x4live harvest` says "N of M fetched" and marks a capped walk INCOMPLETE; `groundtruth`
  reads escaped values containing `\t`; `oracle` lists mapped fields absent from the store
  separately; the shot multiplier in the (still held-out) dps derivation is
  `max(amount, barrelamount)`; stale help text corrected -- including `--with-ramp`, which
  said the game runs only in the foreground (measured: only MINIMIZING the window stops it).

### BaseX

- **`ask.py` scores a zero only against what the query addressed:**
  `db:get('x4eff','no/such/typo.xml')//ware` printed "NEGATIVE CONFIRMED over 10970 of 10970
  documents". A partial, foreign or non-literal database address is refused (rc 2); a query
  naming no database, or a lone `false`/`""`, is not a finding (rc 4).
- **…including a scope narrowed INSIDE the query** (BLIND-SPOTS F142).
  `collection('x4eff')[matches(document-uri(.),'libraries/wares')]//…` addressed 9 documents
  and printed "NEGATIVE CONFIRMED over 10970 of 10970", rc 0. A first fix listed the
  narrowing shapes it had seen, and the pre-release review walked 11 of 12 rewrites past that
  list (`[position() le 9]`, `subsequence()`, a `let` then a `where`, `collection#1(...)`,
  ...), each "NEGATIVE CONFIRMED" over the whole database. Now any `xq` query containing a
  token that can select documents by identity, position, node order or an unreadable route
  (`document-uri`, `position()`, a numeric predicate such as `[1]`, `#`, `Q{...}`, ...) still
  runs and reports its hits, but its zero is withheld (rc 4, naming the token). Selecting by
  content is unaffected. `$d => document-uri()` is no longer refused. The rule over-withholds
  on purpose: `//ware[1]` loses its certificate too.
- **`coverage.py`: an overlay catalog that cannot be read makes `x4eff` coverage
  `unexplained`** (it was counted as one missing vpath and the coverage stayed `complete`);
  `unreadable_catalogs` names it and `ask.py`'s refusal says so.
- A rebuild revokes the old `coverage-<db>.json` before dropping the database; "accounted" is
  judged per root.
- `stage.py` no longer indexes both mini-DLC twice when every DLC is unpacked (142 duplicate
  documents in x4raw — rebuild x4raw to drop them); a failure to ask refuses rc 2.
- BaseX staleness fingerprints every install root; an unreadable catalog in the x4eff build is
  recorded.

### Test harness and scripts

- **`scripts/test-hooks.sh` can no longer hang for hours on Windows** (BLIND-SPOTS F140). It
  assigned its sandbox to `TMP`, which is exported on Windows, and a native Windows process
  whose `TMP` is longer than 260 characters spins forever inside `CreateProcessW` the first
  time it starts a child (MEASURED: 260 starts, 261 hangs). With a long `X4_TEST_SANDBOX` the
  identifier scanner spun for 15 hours. The variable is renamed in `test-hooks.sh` and
  `smoke-basex.sh`, a test bans assigning `TMP`/`TEMP`/`TMPDIR` in any shipped shell script,
  and `scan-identifiers.py` refuses (rc 2) instead of hanging when `TMP` is too long.
- A killed identifier scanner is no longer reported as "a personal identifier reached a
  tracked file": a leak now needs the scanner's own `::error file=` line, not exit code 1.
- `scripts/load-order-probe.py` round 2: an OPTIONAL dependency on a mod disabled in its
  manifest or in the profile (with a required-dependency control), and a second extensions
  root -- an apply chain that tells an interleaved walk from game-root-first or
  profile-first by its No-matching-node lines alone, plus a required dependency across the
  roots in each direction. Profile probes are written by a narrow writer (new marked folders
  only, every file re-read) because `deploy-mod.py` refuses a profile root by design.
  `remove` no longer crashes when the profile and game roots are the same folder; `deploy`'s
  dry run lists the profile writes even before that folder exists; `deploy --apply` writes
  no profile probe after a game-root deploy fails to verify (rc 1).
- **Installing from a git checkout copies only the files git tracks** (BLIND-SPOTS F144).
  The installers' prune lists had drifted from `.gitignore`, so built BaseX databases,
  caches and scratch output travelled too (one checkout carried 33 untracked files, 2.3 GB).
  A release-zip install is unchanged. BaseX build products are never copied in and never
  deleted from the destination.
- `verify-nexus-pack.py`: a blank-only changelog FAILS instead of crashing; a wrong argument
  count prints the Usage line (it printed a blank line).
- `test-hooks.sh`: a scanner selftest that could not run (e.g. refused over a long `TMP`) is
  reported as a SKIP with the scanner's reason, not "selftest FAILED".

### Freshness

- **A machine with no X4 configured no longer crashes `x4xref`.** An unconfigured
  extensions root became `Path(None)` inside the freshness stamp (`TypeError` from
  `x4xref who-calls`); an unconfigured root is now simply not a root. Found by the
  release's cold-clone verification.

- An artifact goes STALE when a mod moves between install roots, when code that shapes the
  store changes (`ENGINE_SOURCES` re-derived by tracing a build), and after a game update —
  which the banner and `x4modlist changed` now name as a reference change instead of blaming
  mods. x4xref stamps every root it indexes.

### Gates

- **A gate that examined nothing no longer passes.** Shared floors and a stale-store refusal
  (rc 2 = could not judge; a real finding still outranks it, rc 1) in `similar_audit`,
  `provenance_audit`, `registry_provenance`, `xsd_fast_parity`, `noop_audit`,
  `consistency_audit`, `cross_tool`, `diff_truth` and `toolkit_usage`; `claims_audit` returns
  2, not 5, on a stale store.
- Exit codes are judged per case in `edge_sweep` (39 cells), `stress_sweep`,
  `determinism_audit` (a crash repeated twice is not "stable"), `control_bytes`,
  `oracle_index` and `hook_false_positives`.
- Per-item comparison where totals hid movement: `schema_sweep` gains a local per-mod
  baseline (`--record`; until recorded it reads rc 2); `cross_tool` checks the HARD winner on
  the collided node; `update_corpus` credits a planted case only from its own file;
  `register_rederivation` names every dead citation on each run.
- **Re-baselines, attributed per item.** `schema_sweep`: 71 -> **74** advisory, 41 -> **42**
  mods flagged -- +3 in ONE mod, a real authoring defect (three text-only `<add>`s into
  `<area>`) that the old merge silently dropped and the MG-2 merge fix now models; pairs,
  gating, suppressed and NOT-checked unchanged. `obtainability_audit`: unreadable files are
  compared by NAME, and a local baseline recorded before 2026-09-25 lacks those names, so it
  reads rc 2 NOT COMPARABLE -- re-record it once with `--record`.
- `claude_md_budget` is described as what it is — a recorded ceiling lowered only by
  `--record`, not a ratchet.
- **A finding outranks a refusal in `cross_tool` and `consistency_audit` too:** a HARD
  disagreement is a FAIL even when the coverage floor refuses, and store-vs-merge
  disagreements print (rc 1) before the dump-channel refusal. `cross_tool` checks
  `ENTITY_REMOVED` rows against the store's removals by replaying them in order, because a
  removal is recorded at its path at that instant, not its base-tree position (BLIND-SPOTS
  F143; HARD rows checked 32 -> 37 on the audit install, all agreeing).
- `load_order_oracle` decides whether the log describes the current modlist BEFORE any
  verdict (a mismatched log with 0 inversions used to PASS), and a mod we exclude that the
  engine logged is a finding (rc 1).
- `nexus_fixture` asserts the update verdict; `instrument_hygiene` excuses a half-written
  last line only in a transcript written in the last 10 minutes; `oracle_index` returns rc 2
  for a broken attribution scan; `diff_truth` counts planted changes as a multiset;
  `claims_audit` and `tool_properties` honour `$X4_EFFECTIVE_DB`; `stress_sweep` says the
  unseen-corpus axis was NOT CHECKED and proves the XXE entity was not expanded.

## v3.2.0 — 2026-09-21

### Deliberately NOT shipped in this release

- **Weapon DPS stays an unmodelled gap.** It was promoted to a computed field during this
  release cycle and **demoted again before shipping**. `dps` and its four per-channel siblings
  remain in the `_DERIVED` (honest gap) registry, so the tool reports *"we cannot compute this"*
  rather than a number it cannot vouch for. **No shipped release has ever emitted a weapon
  `dps`, and this one does not either** — so nothing regresses for users.
  **Why it was pulled:** a live sweep of the whole population (345 comparable weapon/turret
  macros, against the 39 the promotion was measured on) found the per-channel derivation
  disagreeing with the engine on **20 of 345**. Promoting it now would have made this the first
  release to emit a weapon `dps` *and* the first to emit a wrong one — an in-arc defect, and a
  guess wearing the grammar of a measurement.
  **The work is kept, not reverted:** `_derive_dps` and `_derive_channel` remain in the source
  and under test, including the aggregation rule below, which is exact. Re-promotion is two
  lines in `_DERIVE` once the 20 are explained.
  The aggregation rule itself was corrected and is worth recording:
  `hullshielddps + max(0, shieldonlydps, hullnoshielddps) + hullonlydps`, **exact on 345 of 345**
  against the engine's own channel values, where the summing rule it replaces scores 338 of 345.
  ⚠ That 345/345 is the **aggregation formula**, not end-to-end agreement: our derived `dps`
  would match the engine on **325 of 345**. Two distinct failures of the sum, both silent:
  **(1)** a bullet carrying *both* `damage.shield` and `damage.noshield` — only the larger
  channel counts, because a shot cannot hit a shielded and an unshielded target at once. The
  two such macros omit **opposite** channels (`turret_xenon_xl_station_01_macro` engine
  1866.6667 vs sum 2133.3334; `weapon_cpsdo_s_phase_laser_01_mk4_macro` engine 770.7129 vs sum
  886.3198), which is why no single-omission rule could fit both.
  **(2)** a **negative** channel — 5 macros with `shieldonlydps` from -80.0 to -621.43. The
  engine never lets it reduce the total; the sum subtracted it. These never reached the old
  refusal, which keyed on both properties being present.
  The previous release **refused** this shape rather than guessing it (n=1 draws no rule); that
  refusal is what kept the disagreement visible until the population could settle it, and it is
  now retired.
  ⚠ The `0` in the max is a **choice, not a measurement**: 0 of 345 macros have both specialised
  channels negative, so the corpus does not distinguish `max(0,a,b)` from `max(a,b)`. A mutation
  probe found the same gap in the tests — dropping the `0` left every test green — so one test
  now pins the decision explicitly and is labelled as unverified.

### Known issues

- **Per-channel DPS derivation disagrees with the engine on 20 of 345 weapon/turret macros**
  (found 2026-09-20 by `x4live oracle` against a fresh 348-macro sweep). ⚠ **This does not
  affect users of this release** — the field is held out (see above), so the tool reports a gap
  rather than a wrong number. It is recorded here because it blocks re-promotion. All 20
  fail on `hullshielddps` and therefore on `dps`. **8 are shotguns**
  (`turret_{arg,par,spl,tel}_m_shotgun_0{1,2}_mk1_macro`), all carrying `bullet.amount=8` and
  `barrelamount=2`, and all **exactly 2×** the engine. The other 12 (flak, ion, mining,
  battleship, scenario/scimitar beam, tribeam) are unexplained. Two candidate causes were
  tested and **falsified with denominators**: `barrelamount > 1` does not predict it (46 macros
  agree while carrying it), and neither does beam-vs-not (6 of 106 beams vs 14 of 239
  non-beams). Root cause not established, so nothing was changed.

  Three further per-item facts, re-derived 2026-09-21 over the 340 macros the store and the
  capture BOTH cover (a narrower population than the oracle's 345, which is why the shared
  figure is the disagreement count of 20 and not the denominator):
  - **9 of the 20 are off by exactly `bullet.barrelamount`** — the 8 shotguns *and*
    `turret_cpsdo_l_mining_01_mk4_macro`, which the list above files under "unexplained". So the
    multiplier, not the channel aggregation, is the live suspect for nearly half the gap.
  - **The narrower predicate does correlate where the falsified one did not:** of the 9 macros
    carrying `bullet.amount > 1` **and** `barrelamount > 1`, **8 disagree**, against **12 of the
    remaining 331**. `barrelamount > 1` alone predicts nothing; both-at-once predicts strongly.
    Not a root cause — `turret_par_l_tribeam_macro` carries `barrelamount=2` and is off by
    1.0341×, which no multiplier explains.
  - ⚠ **One is a fabricated zero, and it is the worst shape in the set:**
    `weapon_kha_l_scenariobeam_01_mk1_macro` derives **0.0** where the engine reports
    **30,315.79**. A zero is not a visibly wrong number, so had this field shipped it would have
    read as "this weapon does no damage" rather than as a defect. Counting the whole set by
    direction: **18 over-derive** (1.03× to 4.0×), **one under-derives**
    (`turret_cpsdo_xl_ion_01_mk6_macro`, 0.8×), and **one is that zero** — so no single clamp or
    scale factor fits, which is why the field is held out rather than corrected in place.

### Added

- **A "nothing connected" refusal now MEASURES the cause instead of surveying them.** Three
  probes, each answering **true / false / could-not-determine** and none of them deciding an
  outcome — they only make the message specific:
  `_livepipe.helper_is_deployed()` asks `_registry.mods("active")` whether the helper extension
  is in the set the ENGINE would load (never `"installed"` — a profile-disabled folder on disk
  is not loaded, CLAUDE.md #24); `helper_loaded_this_session()` reads the load marker from a
  *live* `debug.txt`; and `game_is_minimized()` resolves X4's pids (`_livedump.game_pids()`,
  `tasklist /FO CSV`) and asks `IsIconic` about its visible windows. **No visible window is
  `None`, not False** — that is a fact about the search, not about the window.
  ⚠ **The defect this fixes, found in game 2026-09-20 in code merged the same day:** the helper
  initialises on GAME LOAD, so at the MAIN MENU its marker is legitimately absent — and the
  refusal concluded *"most likely not installed or not enabled"*, sending users to check an
  install that was fine. The marker alone cannot separate those two causes because it only
  reports a consequence of the install; the deployment read measures it directly. With
  deployment unknown the message now names BOTH causes rather than picking one.

- **A hard request-size cap on the live channel (`_livepipe.MAX_REQUEST_BYTES`, 1900 bytes).**
  `LivePipe.ask()` — the one place every client request is written — refuses any request over the
  cap with `LiveRequestTooLarge` **before** the write, so no verb, passthrough or ramp can send an
  over-long request from any path. MEASURED in a live session: a request over ~1997 bytes tears the
  game-side pipe read down (`pipes.lua` hands an over-long read to `error()` → `Close_Pipe`), and
  that teardown was followed by a game crash whose minidump faults in X4's own UI event dispatch.
  The cap sits below the 1997-byte measured-OK ceiling; `LiveRequestTooLarge` is a `ValueError`,
  not a transport error, so the re-arm/retry paths cannot swallow it silently. See F124.

- **`x4live pause` / `x4live unpause` — the first WRITE verbs — and `x4live pausestate`,
  their read half.** A live measurement drifts while the game runs (a faction gains tens of
  objects a minute), so holding the simulation still is what makes two questions comparable.
  The design, each clause pinned by its own test and mutant: the write vocabulary sits in a
  separate `WRITE_VERBS` table on BOTH sides, so "is this a write?" is a lookup, not a name;
  both verbs are argument-free and call `Pause()`/`Unpause()` bare, the shape vanilla uses;
  every write reply leads with a `write=yes` advisory row, enforced once at the reply choke
  point so a verb that forgets cannot ship an unmarked frame; the result is the engine's
  READ-BACK, never the verb's intention; and `unpause` undoes only a pause this channel made,
  the discipline vanilla's own menus keep. Exit 0 only when the read-back agrees, 1 refused
  with nothing changed, 3 untrusted. `query` refuses both names, so its free-text passthrough
  is not a second door. **This retires a promise made to users:** the helper mod's manifest,
  both READMEs and the shipped CLAUDE.md said there was no write verb. Each is rewritten, and
  `tests/test_write_verb_promises_are_consistent.py` fails if one survives. Gated raw lua eval
  was considered and withdrawn: the engine's debug-console gate has no discoverable switch, and
  it guards a capability UI lua already has, so routing through it would have contained nothing.
  MEASURED live on 2026-09-13: a PAUSED game answers on an open connection and accepts a new
  one, which is what lets `unpause` run as a separate command after `pause`. Every unpause
  attempt RELEASES the claim, so one whose read-back still shows paused never retries; in that session the
  player's own pause was holding, and the rule left it alone. This also corrects the shipped
  hint that "a PAUSED game goes silent" and the `ping` comment that equal elapsed times mean
  paused: `getElapsedTime` is real time and keeps advancing while paused.
  ⚠ **MEASURED in-game 2026-09-15 — a pausing MENU clears our pause; the player's survives it.**
  X4 has two pause levels: the manual pause sets the engine's user-pause flag and survives opening
  and closing a pause-on-open menu (Options), while `pause`'s bare `Pause()` is UNPAUSED by that
  menu cycle (`true owner=us` → open+close Esc → `false owner=none`). So `x4live pause` is durable
  only for a brief MENU-FREE window (pause → measure → unpause); a menu the player opens silently
  drops it. Documented in the README, the x4-live skill, and KNOWLEDGEBASE.md. OPEN: whether `pause`
  can set the user-pause flag to become menu-durable.
- **`scripts/deploy-mod.py`** — a guarded deploy for the mods this repo ships. The public repo
  shipped the helper mod with no safe way to install it: the obvious `rm -rf && cp -r` is one
  empty variable away from deleting the wrong tree inside the game install. It refuses a mod
  the repo does not ship, a missing extensions folder, one inside (or shaped like) the user
  profile, one whose parent is not a game root, a destination with a different manifest id,
  and a destination folder or file that is a link; it runs every guard for every mod before
  writing any, never removes a directory, and re-reads
  every file by sha256 after writing.
- **`gates/routing_coverage.py`** — fails when a CLI in `[project.scripts]` is absent from
  CLAUDE.md's routing-table `Use` column. MEASURED: 5 of 11 were absent (`x4modlist`, `x4stats`,
  `x4diff`, `x4save`, `x4live`), because whether a tool is routed was a prose property nothing
  tested. It enforces *resident routing* — a cold agent found an unrouted CLI and used it
  correctly in 193 s via other docs, so the cost was search, not unreachability. It checks
  BOTH the shipped `CLAUDE.md` at the repo root and the configured game root's copy; the first
  version read only the game root's, and was green solely because the author's personal copy
  had been hand-edited while the shipped one stayed 5-unrouted (review, 2026-09-13). The
  pre-landing file is a committed fixture, so the "it goes red" test runs on every clone
  instead of skipping everywhere but one machine.
- **`gates/claude_md_budget.py`** — a per-file recorded ceiling: no CLAUDE.md may exceed its
  recorded size, and growth must name what it displaces. *(Corrected 2026-09-25: this line
  first called it "a ratchet". It does not tighten by itself -- the size is lowered only by a
  deliberate `--record`, by user decision; AUDIT-2026-09-24 GT-6.)* MEASURED: the two CLAUDE.md files on
  the author's machine moved in opposite directions, one shrinking 75% while the other grew 3.8x
  in a single unannounced commit. Baseline is local and gitignored; absent is reported as "drift
  is NOT being checked", never as a pass.
- **`x4validate/_surface.py`** — the CLI/subcommand surface, asked of the programs via `--help`
  and argparse's invalid-choice listing rather than parsed from source. A static `add_parser`
  scan found 39 of the 43 real subcommands when this was measured (2026-09-12; the surface is
  47 now); names built in a loop are invisible to it. Raises
  `SurfaceUnavailable` rather than `SystemExit`, so a fresh clone cannot abort pytest collection.
- **`tests/test_no_stale_bytecode.py`** — refuses when cached bytecode disagrees with its source
  while CPython would still reuse it. A same-length mutant restored within one mtime *second*
  leaves its `.pyc` behind; the source then reads correct and the next import executes the
  mutant. Four mutation verdicts were reported before this was caught.
- **`/x4-cli-reference` skill, GENERATED** — the exact subcommands, flags and defaults of all 11
  CLIs (47 subcommands), written by `tools/x4validate/scripts/gen-cli-reference.py` from each
  CLI's own argparse help; `tests/test_cli_reference.py` fails the suite when it is stale, missing
  a file, or carries a ghost. Chosen over an MCP server after measuring the case for one: 2
  malformed calls in 2,965 invocations, and MCP tool descriptions load name-only in Claude Code
  while the routing table is in context every turn. Which tool answers which question stays in
  CLAUDE.md's routing table. Help is captured in-process because a subprocess capture mangles
  non-ASCII on Windows, and at a pinned width so every machine generates the same bytes.
- **`gates/deploy_parity.py`** — fails when a configured game root's `.claude/` differs from this
  repo's, which is its source. MEASURED on the author's machine: 5 of 25 deployed files had drifted
  in three directions, each from an edit made in the deployed copy. Line endings and the installer's
  own rewrite (skills and agents only) are not drift; every difference is reported with its
  direction, and there is deliberately no accept-baseline.
- **`tools/x4validate/scripts/deploy-claude-dir.py`** — deploys `.claude/` into the game root, one
  direction, dry-run by default. It refuses to overwrite a deployed file that matches no version
  this repo ever committed (an edit never ported back; `--force <name>` for one file), never deletes,
  keeps each file's line endings, and re-locks x4lock'd files. Its first real dry run caught its own
  defect: an unscoped rewrite would have pointed a game root's `settings.json` at the repo's hooks.
- **An edit-time advisory** in `.claude/hooks/protect-files.sh`: an Edit landing in a deployed
  `.claude/` is pointed at the source. Silent in the in-game layout, where the two are one directory.
- **Three skills now ship** that existed only in one deployment: **`/x4-xml-patching`** (the
  selector, merge-tree and load-order gotchas; installed-mod names generalised, gotcha numbers kept),
  **`/x4-live`**, and the newer **`/x4-probe`** with its "can it RECORD?" rung.
- **The blind-spots register, the 2026-08 audit and their tooling now ship.** `docs/BLIND-SPOTS.md`
  (F1 to F125), `AUDIT-2026-08.md`, `scripts/next-blind-spot-id.py`, and four gates:
  `register_rederivation` (every FIXED entry must name a check that exists), and
  `toolkit_usage`, `instrument_hygiene` and `hook_false_positives`, which read this machine's
  session transcripts and refuse without them. They lived in a separate development repository,
  now retired: two repositories with disjoint histories could not be kept in step, and on
  2026-09-13 they had 79 differing files and 24 held only in the second. Personal overlay folder
  names are scrubbed to `zzz_personal_overlay_A` to `_G`. F106's write-up, which existed only on
  an unmerged branch of that repository, is restored. 13 tests that skipped on every clone
  because those files were absent now run. `next-blind-spot-id.py` reads the register where this
  repository nests it (it would otherwise have refused every request), and `toolkit_usage` asks
  `x4validate/_surface.py` instead of keeping a copy that had fallen behind it.
- **`x4live query macro ... --contents` and `x4live query recon ... --contents --deep`.** A
  library entry's table-valued fields (a ship's `weapons` and `storagetags`) came back as a
  bare `<table>`, so they could never be compared. `macro --contents` renders them two levels
  deep; `recon --deep` adds one level to `--contents`. Both are opt-in -- a `macro` reply without the
  flag is byte-identical to before, and so are recon's rows (its header gains `deep=no`) --
  bounded per value (a cut says `+N-more`, a string is cut on a whole UTF-8 character, and nested
  tables share what their scalar siblings leave, so one fat table cannot hide them), depth-bounded so
  a cyclic engine table cannot hang the game, and scrubbed per key and per string so a value
  cannot split the reply's fields. The helper mod's BUILD moves; a running game needs a reload
  to pick it up.
- **`x4live groundtruth --macros FILE` and `--contents`.** The built-in harvest list holds one
  macro per library type, so props that happen to share a value (a ship's horizontal and
  vertical drag, 5 of 5) cannot be told apart, and `x4live mappings` reports them AMBIGUOUS.
  `--macros` harvests a list you choose -- `<librarytype> <macro>` per line, `#` comments
  allowed -- and refuses, before contacting the game, a malformed line, an empty list, or any
  macro the effective store does not hold (an invented name comes back ABSENT and reads as "the
  engine exposes nothing"). `--contents` sends the all-fields call with `--contents` and never
  the per-field calls. Every harvest file now records its list and mode in its header.
  `--contents` against a helper build that predates the flag is refused rather than harvested
  empty, a listed macro whose all-fields call answers ABSENT is named, and a duplicate line is
  asked once.
- **`x4live ffi-census` and the `ffisyms` verb: the engine surface `query globals` cannot see.**
  `globals` walks the lua global table, but vanilla's ui lua declares 2,066 C functions in
  `ffi.cdef` blocks (2,074 with the Ventures DLC) that never appear there, so a negative from
  `globals` covered about a third of the surface. `ffi-census` reads those names from your own
  game files -- literal `ffi.cdef` blocks only; any other `ffi.cdef` call is listed as unparsed --
  and asks the running game about each one: `exported` (resolvable in the game process), `notexported` (declared, not resolvable),
  `undeclared` (nothing loaded this session declares it), `other` (quoted), or `invalid`. The verb
  only looks symbols up: it never calls one, and never declares one, because a second declaration
  with a different signature is silently ignored by LuaJIT and could break the game's own later
  calls. Names go in small batches (`--batch-bytes`, default 1000). A batch that is not
  answered, or whose rows do not line up name for name, is recorded as `errored` and the command
  exits 3, as does an invalid name, which is never sent. If the channel drops mid-run, the batches
  already answered are kept. Output is a `.tsv` with the declaring file(s) for every name. The helper mod's BUILD
  moves; a running game needs a reload.
  **⚠ SHIPS DISABLED BY DEFAULT (crash containment, F124).** `ffi-census`, and `ffisyms` via
  `query`, now refuse unless `X4_LIVE_ALLOW_FFI=1` is set. During this live session an over-long
  request was MEASURED to tear the game-side pipe read down (teardown floor in (1997, 3998] bytes),
  and a game crash followed the same session; the minidump put the fault in X4's own UI event
  dispatch, NOT in this FFI-index path, so the gate is precaution rather than a proven cause, and
  is trivially reversible. The batching/alignment logic is unchanged behind the gate.

### Changed

- **Weapon `dps` is now COMPUTED, not listed as an unmodelled gap.** `dps` and its four
  per-channel siblings (`hullshielddps`, `shieldonlydps`, `hullnoshielddps`, `hullonlydps`)
  move out of `_DERIVED` and into `_DERIVE` — the same move `storagecapacity` made in
  2026-08-30, at a higher evidence bar. **MEASURED against a live engine harvest: 38 of 39
  weapon macros exact** at 1e-6 relative (beam 15/15, continuous 9/9, clip 14/15;
  `reloadrate` 39/39). Run through the real oracle, **195 fields move from "our store cannot
  produce these" into real comparisons and 194 of them agree.**
  ⚠ **Provenance: the 45-macro and 55-macro harvests these figures were read from are
  not archived anywhere in this repo or the reports directory** (the newest committed
  fixture is an 18-macro harvest from 2026-09-13), so nobody can re-derive them. The
  traversal itself IS reproducible: every dps-family field in that fixture agrees with
  the store to 1e-6, re-measured 2026-09-20. Treat the two counts as recorded history,
  not as something you can check.
  What had made DPS look weapon-type-dependent across six irreducible variants was **three
  missing inputs, not three physics**: `reload.time` is the reciprocal spelling of
  `reload.rate` (of 299 bullet macros, 174 carry one and 119 the other, 0 carry both — so
  reading a single spelling silently drops 40% of bullets); `bullet.chargetime` is part of
  the firing period; and area damage folds into the *same* channel as its direct damage.
  With all three it is one formula whose only branch is beam-vs-not.
  ⚠ **That branch reads `bullet.attach`, never the engine's `isbeamweapon`.** `_DERIVE`
  feeds the oracle that compares our value *against* the engine, so a model taking an engine
  input would be circular — it would score well and prove nothing. `bullet.attach` is the
  store-side discriminator, measured to agree with `isbeamweapon` **39 of 39**.
  The single disagreement is reported, not suppressed: it is the only macro in the
  population carrying both `damage.shield` and `damage.noshield`, and there the engine omits
  the shield-only channel the other 38 include. **n=1 draws no rule.** `sustaineddps` stays
  in `_DERIVED` — it folds in the heat model, which this formula does not reproduce.

- **`x4live oracle` names a THIRD outcome: `derivation REFUSED`.** A traversal that looked
  and declined is not "the engine derives this" and not "nobody has mapped it yet".
  MEASURED while promoting `dps`: 30 rows — the six decorative `*_video_macro` entries,
  which carry no bullet at all, × five channels — silently moved out of the *named* derived
  bucket into the generic unmapped one, which is exactly the hiding `_DERIVED` exists to
  prevent, arriving by the back door the moment a field was promoted. `not mapped yet` is
  back to its pre-change 1484 and the buckets still sum to the population.

- **The 1,798-character `MINIMIZED_HINT` essay appended to EVERY live refusal is replaced by a
  one-line diagnosis.** It existed because nothing could measure which cause applied, so it
  hedged across all of them on every failure. `game_is_minimized()` measures the main one, so a
  refusal now says either *"X4's window is MINIMIZED … restore it and run it again"* or *"X4's
  window is NOT minimized, so this is not the usual cause — do not go and un-minimize
  anything"*, with the survey kept only for the could-not-determine case. It also sheds two
  things that stopped being its job: *"grep debug.txt for the load marker"* (now read for the
  user) and *"the mod is probably not installed"* (now measured, in its own branch, which is
  where the extension and its `ws_2042901274` dependency are named). The full provenance of
  every measurement moved into the module comment above the constant, out of user output —
  `test_the_hint_provenance_is_not_lost_from_the_source` fails if a figure is dropped, so
  "shorten the text" cannot quietly become "drop the evidence".
  **MEASURED 2026-09-20, and it is a new engine fact:** minimized stops the frame loop in
  *both* display modes (windowed-minimized 0.031 CPU core-s/s, fullscreen-alt-tabbed 0.016,
  against 0.81–25.9 active) — the prior figure covered exclusive fullscreen only. The window
  handle survives a display-mode change and an alt-tab away, which is what makes `IsIconic`
  valid in either mode. A HUNG game stays unreported: `IsHungAppWindow` is not exported by this
  `pywin32` and a hang cannot be induced on demand, so it is left unmeasured rather than guessed.

- The X4 Toolkit Helper mod is version 101. Its `IsGamePaused` declaration is guarded
  separately from the shared ffi block, so a failure there disables only the pause verbs and
  leaves every read verb working.
- `tests/conftest.py` sets `sys.dont_write_bytecode`, so the suite stops manufacturing the
  stale-bytecode hazard in-process. `run-gates.sh`, `verify-cold.sh` and CI now run with
  `PYTHONDONTWRITEBYTECODE=1`, which is the real boundary: conftest's own cache, `tests/`,
  `scripts/` and gate runs outside pytest are all outside what the conftest line can reach.
- The shipped `CLAUDE.md` routing table gains the five rows above plus one routing
  `gates/mutation_probe.py` — and stating that it covers ONLY the nine `x4validate/_*.py`
  files it names. `install.sh` / `install.ps1` prune the local ratchet baseline like the other
  per-machine baselines.
- `scripts/x4lock.py` also locks `.claude/skills/*/reference/*.md`, so a multi-file skill is
  protected whole.
- `x4diff --overlay` help no longer names a specific mod.
- Nexus release packs under `release/nexus/` are untracked and gitignored: they are a maintainer's
  local artifact, deleted once posted, and tracking them held the suite at two standing failures.
- Fixes from an independent review of the above, each with a test that goes red without it:
  - `.gitignore`'s `reference/` rule (for unpacked game data) also swallowed
    `.claude/skills/x4-cli-reference/reference/`, so the generated help files were never committed and
    the reference was fresh only on the machine that generated it. Skill `reference/` folders are now
    re-included, and a test asks git that every generated file is committable.
  - `routing_coverage` counts a CLI only as a whole tool name: `x4validate/_*.py` in a table cell was
    being read as routing `x4validate`.
  - `claude_md_budget` names every file it is NOT checking (no baseline entry) and refuses when no
    measured file has one, instead of passing having compared nothing.
  - `deploy-claude-dir.py` exits 1 when its own files are not at parity after applying, and locks a
    newly created file that x4lock's manifest covers.
  - A malformed `pyproject.toml` is a refusal from `_surface.cli_roster`, not a traceback.
  - The Nexus-packs test only trusts `git rev-parse` when the checkout IS this toolkit, so a toolkit
    installed inside another git repository no longer fails it.
  - `/x4-xml-patching` no longer points at files and sections the public repo does not contain.
- **Every CLI argument now has help text, and a test refuses one that does not.** A cold agent
  answering from the generated `/x4-cli-reference` tripped on three undocumented arguments; walking
  every parser found **41** -- 34 with no help at all (22 in `x4effective`) and 7 whose default was
  never stated. Each string was written from the code it describes. Defaults that are machine paths
  or pinned ids are stated in words, so no personal path or 8-digit id reaches the shipped skill.
- **`x4effective who-sets <kind> <name>` with no property now says which properties something other
  than base set, by origin.** It printed only the ENTITY's origin, so asked who set `energycells` it
  answered `base` while `show` revealed a mod setting `price.min` and `price.max` -- found by a cold,
  docs-only agent. It names ORIGINS, never "a mod": measured over a real store, 42% of attributes with a
  non-base origin were last set by a DLC. An all-base entity is stated as such, and removals
  recorded against the entity's file are reported too: a removed value is not stored as a property.
- **BaseX `ask.py` says what its count counts, and when it searched the as-written database only by
  default.** A positive result now adds that an item is one node or value the query returned -- not a
  count of files or entities -- and, when `--db` was omitted for `refs` or `attr`, that it searched `x4raw` (files as written)
  and to add `--db x4eff` for what the game actually loads. `--help` states the default. The count line
  itself and the default are unchanged. Both gaps were found by a cold, docs-only agent.
- **CI's skip ceiling is tight again: 68 on ubuntu, 56 on windows (was 74 and 62).** The measured
  skip floor had fallen to 65 and 53 -- the blind-spots register now ships, so 13 tests that skipped
  for its absence run -- which left 9 skips of slack on each leg: room for tests to go silently
  dormant without failing the build. Every one of the 65 skips was classified first (49 need an X4
  install, reference tree, game root or registry the runner lacks; 12 are Windows-only or need a
  case-insensitive filesystem; 4 need a tag the shallow checkout lacks), and the margin stays +3.

### Fixed

- **⚠ The guard allowed a recursive search rooted ABOVE the 60 GB `reference\` tree, and this
  release is what broke it (F127).** Two rules cover recursive text searches: one fires when the
  searched path IS `reference\`, the other when it is the toolkit or game root — both on an
  EXACT path match, deliberately, so a search scoped *into* a big tree stays allowed. But
  `reference\` sits inside `<...>/Desktop/Modding/X4`, and that parent **was** `X4_TOOLKIT`
  until this arc retired the dev repo. The ancestor was therefore covered only **by
  coincidence**, through the workspace rule; when `X4_TOOLKIT` moved, a recursive walk from the
  parent — which necessarily traverses the whole 60 GB — matched no rule at all.
  MEASURED through the deployed hook: rooted at that parent → **ALLOW**, while the same shape at
  the toolkit root, at `reference\` itself and at the game root all denied. Three controls still
  denying is what makes it a hole rather than a rule change.
  **Why nothing caught it:** `test_hook_facts.py`'s fixture set the toolkit root to the parent of
  `reference\`, so it encoded the very coincidence — no test there could vary the *relationship*
  between two roots, only their values (CLAUDE.md #37). It was found by
  `gates/hook_false_positives.py`, which replays every historical command through the real hook:
  **146 of 17,133 shared commands moved**, and classifying the 38 that moved into `allow` per
  item isolated it. ⚠ That gate had been red and unobserved — it is slow and was not in the
  previous verification's four-gate sweep.
  **The hole was twice the size the first pass reported:** re-running the comparison after the
  fix and accounting for every departure showed **6 commands left `allow`, not 3, and 0 entered
  it**. The three missed were ancestor walks whose text never contains the string `reference`, so
  a text heuristic could not see them while a path predicate could. What made that safe is that
  the fix was written to the *axis* (proper ancestry) rather than to the commands in hand — keyed
  to the observed three, it would have shipped covering half the defect with a green gate.
  **Fix:** `contains_root()` treats a PROPER-ANCESTOR search as the same refusal, compared with a
  trailing separator so `/a/bc` does not read as living under `/a/b`. 11 new tests including a
  split-root fixture that breaks the coincidence, 3 of 3 clause mutants killed, and an 8-case
  end-to-end probe on the deployed hook confirming the hole denies while every allowed control
  still allows. A fourth clause was written and **deleted because its mutant survived** — it
  could never change an answer, so it was decoration.

- **A Windows clone checked the shipped mod's XML out as CRLF (F118).** `* text=auto` uses the
  platform's native line ending, so `mods/x4_toolkit_helper/*.xml` arrived CRLF while the
  committed and deployed copies are LF, and `scripts/deploy-mod.py` listed changes nobody made.
  `.gitattributes` now pins `mods/**/*.xml` to LF, as the lua already was; the blobs are
  unchanged. **If you already have a Windows clone**, `tests/test_line_ending_pin_is_obeyed.py`
  will fail on those two files until they are checked out again. First check that you have not
  edited them: `git status --short -- mods/x4_toolkit_helper` must print nothing. Then delete
  the two files and run
  `git checkout -- mods/x4_toolkit_helper/content.xml mods/x4_toolkit_helper/ui.xml`; they come
  back LF and no diff results. If you deployed the helper from a CRLF clone, the next
  `deploy-mod.py` run lists those files as CHANGED once; that is the fix arriving. A release
  bundle built on Windows now ships them LF as well.
- **`scripts/x4lock.py status` failed in every git worktree (F119).** It demanded the checkout's
  own `.claude/x4-paths.env`, a per-machine file a linked worktree never has. It now waives that
  file only in a linked worktree (identified by git's own `commondir` metadata, so a submodule does
  not count), says so in its output, and demands the main checkout's copy instead, plus
  `$X4_TOOLKIT`'s when set, so a deleted configuration is still reported. Where nothing can stand
  in (a worktree of a submodule, or a `--separate-git-dir` repository, with `$X4_TOOLKIT` unset),
  the worktree's own file is still required.
- **BaseX `ask.py xq` no longer certifies a negative over a query Git Bash rewrote.** MSYS path
  conversion rewrites path-like parts of command-line arguments before Python sees them: measured,
  `'count(collection("x4raw")//ware)'` arrived as `.../ware` and `'//ware'` as `/ware`, and the
  zero guard then printed `NEGATIVE CONFIRMED` with exit 0 where PowerShell counted 14,068. `xq`
  now takes `--file <query.xq>`, which is not rewritten; under Git Bash a zero from an argument
  query is refused (exit 4) and every argument-query result shows the query as received.
  `refs` and `attr` are unaffected -- they build their query in Python. Found by a cold,
  docs-only agent following `QUERIES.md`'s own example. BLIND-SPOTS F122. A second review added:
  an empty or comment-only query refuses (exit 2), a UTF-8 BOM in a query file is dropped, and
  `MSYS_NO_PATHCONV` or `MSYS2_ARG_CONV_EXCL=*` turns the refusal off along with the conversion.
- **Docs no longer say BaseX sees loose XML only.** `x4raw` has indexed packed archives since `stage.py`
  (a rebuild on 2026-09-14 listed `[packed] vro/...` exclusions); the shipped CLAUDE.md's Discovery row
  said packed content was invisible. Also: `tools/basex/README.md`'s freshness command lacked the
  required `--check` and exited 2, and QUERIES.md's "current" coverage figures were three weeks old --
  now dated and derived from `coverage-<db>.json`. Found by a cold, docs-only agent.
- **`gates/register_rederivation.py` sees the BaseX tests.** A FIXED register entry citing
  `tools/basex/test_*.py` read as naming no check, so F122 had to use the opt-out. Those files now
  resolve -- test files only (F46 cites `tools/basex/ask.py`, the tool, which is not a check), and a
  bare test name defined only there counts only when the entry cites the file: adding them to the
  bare-name pool was measured to resolve F46 on a test it names as a casualty. Per-entry verdicts
  before and after are identical; F122 now cites its checks.
- **`bin/unpack-reference.sh` recorded a beta branch's build instead of the installed one.** A
  Steam app manifest carries a `"buildid"` for the installed build and one for each beta branch;
  the script took the last one found, which on the reference machine was `public_beta` (23524486)
  rather than the installed 23660954. A re-unpack would have written that into
  `reference/.unpacked-and-locked`, and the SessionStart stale-reference hook would then have
  reported a current `reference/` as stale at every session start. Both now read the build through
  one shared helper, `x4_acf_buildid` in `.claude/hooks/_x4-env.sh`, which takes the installed
  build whatever order the keys are in. The last-match read dates from v2.0; a manifest lists
  beta branches even when you are on the public branch (measured). **If you have unpacked with
  any earlier version**, compare the build named in `reference/.unpacked-and-locked` (or, before
  v3.1.0, `.claude/.reference-buildid`) with the `"buildid"` directly under `"AppState"` in
  `appmanifest_392160.acf`. If they differ only because of this, correct the number in that file
  rather than re-unpacking.

- **BaseX `ask.py` explains the error a query with no database gets.** `count(//ware)` reached
  BaseX's own `[XPDY0002] .: Context value is undefined` and exit 2 -- true, and naming
  neither the cause (an `xq` query addresses its own database; `--db` sets the coverage
  denominator, not the input) nor the cure. BaseX's words are kept as evidence and the
  translation adds `collection('<db>')//ware`, the same service `preflight` already does for
  an unbuilt database. Keyed to BaseX's verdict, never to the query text: reading the query
  for a `collection(` literal refused `1+1`, which is legal and needs no database. QUERIES.md
  gains the gotcha. Found by a cold, docs-only agent.

- **`gates/register_rederivation.py` matched a cited path's SUFFIX.** With no left boundary, an
  entry citing `mytools/basex/test_ask.py` -- no such file -- resolved to the real
  `tools/basex/test_ask.py` and read as covered by a file it never cited: the F112 worst case,
  a citation that resolves while not covering the claim. Every path alternative now starts at a
  boundary that forbids a preceding word character, dot or dash and allows `/`, which is what
  keeps real nested citations (`tools/x4validate/tests/test_x.py`, matching through `tests/`)
  resolving at the package root. MEASURED per entry on the real register: all 77 closed entries
  keep their verdict (67 citing a check, 2 opted out, 8 the accepted backlog), and the gate
  reports no new findings. 4 mutants, one per clause and one per over-firing direction, each
  KILLED.

- **The release review of `v3.1.1..master` found and fixed sixteen defects.** Every changed
  file in the range was read in full by an independent reviewer per area, each finding blamed
  in-arc or pre-arc, and each fix carries its own falsification twin. The ones a user can
  see: `ask.py` read the MSYS markers for truthiness, so a defined-but-empty `MSYSTEM` turned
  the Git Bash refusal off while the rewrite continued (F122 reopened) and an empty
  `MSYS_NO_PATHCONV` refused a legal zero; the BaseX-error path printed no "as received"
  line; `x4live oracle` reported a false disagreement because `explosiondamage` was mapped
  as identity when the engine totals a SALVO (1680 = 8 × 210) — it exits 0 again;
  `ffi-census --batch-bytes` above the pipe's cap died with a traceback and lost every
  answered batch; `x4live groundtruth` had silently stopped harvesting five fields;
  `x4modlist ingest --build` wrote a hard-coded build id into every registry as if measured;
  and the deployed-`.claude/` advisory cost **+182 ms on every edit** (measured 1252 vs 1070
  ms/call). Instruments: `instrument_hygiene` went from **13–15 minutes to 5 seconds** (two
  unanchored lookaheads, O(n²) per command) and now judges the INCREMENTAL rate, which
  surfaced two regressions a lifetime average had diluted away; `hook_false_positives`
  reported green when baseline and run shared ZERO commands; `claude_md_budget --record`
  overwrote floors without naming what it displaced; `x4live mappings` promised "one
  disagreement disqualifies a candidate" and never recorded one; the acf hand-rolled-reader
  pattern caught 2 of 6 realistic re-implementations; `uv.lock` was a third, unchecked copy
  of the version; and 211 Lua tests — the only coverage of the write verbs — sat behind one
  skip the ceiling absorbs. Stale figures corrected: the register is F125 (not F120), the CLI
  reference carries 47 subcommands (not 43), `mutation_probe` has 54 mutants (not 48), and
  `x4-xml-patching` pointed at a KNOWLEDGEBASE section the shipped file does not contain.

## v3.1.1 — 2026-09-09

**CI was RED on the v3.1.0 tag, on both legs, and the release went out anyway.** One
step failed — `scripts/fuzz-guard.py`, the differential fuzzer the v3.1.0 notes name
to users as evidence they can run themselves. It exited **2 for everyone**. The local
sweep that gated the release ran **28 of the 29 gates** — `run-gates.sh --all` excludes
`mutation_probe`, which rewrites a source file in place and so never runs beside a tree
read — plus 1,716 tests and 160 hook probes. None of them is what CI runs; nobody read
the run for the released SHA. A local green is not CI's green.

The fuzzer was right to refuse, and each refusal unmasked the next defect behind it.

### Fixed — `install.ps1` was 100% dead on Linux and macOS, and v3.1.0 shipped it

Unmasked by the fuzzer fix above: with CI able to get past step 11, the ubuntu leg ran
"Run the suite with NO game installed" for the first time this arc and **21 tests
failed**, `test_the_harness_can_install_at_all` — the denominator test — among them.
Every one was `install.ps1`, both methods, rc 1:

    install.ps1: Cannot bind argument to parameter 'Path' because it is null.

`Find-GitBash` built its candidate list as

    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)},
                        (Join-Path $env:LOCALAPPDATA 'Programs'))) {
      if ($base) { ... }

**The guard on the next line is correct and it is one level too late.** The ARRAY IS
BUILT FIRST, so `Join-Path $env:LOCALAPPDATA 'Programs'` is evaluated before `if
($base)` can skip it — and `LOCALAPPDATA` is Windows-only, i.e. `$null` under
PowerShell on Linux and macOS. It protected the loop VARIABLE, not the EXPRESSION that
produced it. `Find-GitBash` runs for every method, so the installer never reached its
first decision on POSIX.

Two more of the same class, found by reading the rest rather than by waiting for CI:
`Get-GlobalClaudeDir` and `Detect-Profile` both built paths from bare
`$env:USERPROFILE`. `install.sh` has always written `${USERPROFILE:-$HOME}`; a single
`Get-UserHome` now carries that precedence, placed above its first caller because
PowerShell defines a function when execution REACHES it.

**How it stayed invisible is the more useful half.** 21 tests already covered this —
on the ubuntu leg only, which is `continue-on-error: true`. So the run still concludes
**success**, and `gh run watch --exit-status` returns **0**. That is why v3.1.0's own
red legs read as a green run, and it is why this arc reads CI **per job**. Two tests
now assert it with no POSIX runner at all: one drives both installers with the
Windows-only variables scrubbed from the environment, and one parses `install.ps1`
with PowerShell's own parser and CALLS its four path-deriving helpers directly, with a
denominator so it cannot pass over having exercised nothing. Every clause of both was
driven red against the exact defect it names.

⚠ **If you are on Linux or macOS, `install.ps1` from v3.1.0 cannot have worked.**
`install.sh` was never affected.

### Fixed — the CI skip ceiling failed a leg that had zero failures

With the installer fixed, ubuntu ran clean — **1,631 passed, 0 failed** — and the step
still exited 1. The ceiling: `X4_MAX_SKIPS` is 70 for ubuntu and the run legitimately
skips **71**. Windows sat at exactly **59 of 59**, i.e. one further skip from failing
the gating leg on a ceiling rather than on a defect.

That is the second time for this pair, and its own comment describes the first — *"THE
OLD 56 WAS EXACTLY AT THE MEASUREMENT, so the gating Windows leg had ZERO headroom…
which is the reason the margin is stated rather than assumed."* Both times the numbers
were **inferred** (`56 + 11` for ubuntu) rather than read off a run, and the same
comment already said to *"take CI's numbers over these"*.

Both breaches shipped in v3.1.0 unseen, for the same reason as the installer: this step
never executed, because the fuzzer failed eleven steps earlier.

**Every one of ubuntu's 71 skips was classified before anything was raised**, buckets
summing to 71 of 71 — 38 no X4 install or reference tree, 12 Windows-only behaviour,
18 a dev-only file absent from the public mirror, 3 no registry configured. None is a
test going dormant, which is the 125-at-once failure the ceiling exists to catch. So
the ceiling was raised because the measurement moved, not to silence a signal.

Now **measured + 3** on both legs, where measured means read off a run: windows 62,
ubuntu 74. Ubuntu is no longer inferred from windows.

### Changed — the Linux leg can fail a build now, for the first time

`ubuntu-latest` was `experimental: true`, and `continue-on-error` is keyed on that
flag. So its verdict was a number nobody read: the **run** concludes `success` with
that job red, and `gh run watch --exit-status` returns **0**. Every defect in this
release hid behind exactly that.

It is now `experimental: false`, on the condition its own comment set — *"promote only
once ubuntu is observed green on this code"* — which took three rounds to satisfy, and
each round is the argument:

1. the differential fuzzer exited 2 on an orphaned control anchor, which also masked
   every step after it for an entire release;
2. `install.ps1` was 100% dead on POSIX, 21 tests red — caught **here and only here**;
3. the skip ceiling failed a leg that had 1,631 passed and 0 failed.

Two Linux-only checks are consequently **enforced for the first time**: `--method
global` in bash, and the cold-clone verification. Both were previously "green that
could not go red" — and one of them is the bash twin of the very method whose
PowerShell side shipped v3.0.0 completely broken because no leg could say so.

### Fixed — the bundle verifier refused a correct bundle over `export-ignore`

`build-release.sh` checks the zip's member set against the ref's tracked set, in both
directions, because "did a zip appear" is not "is what is in it what the tag says".
Adding `release/ export-ignore` made `git archive` legitimately omit 14 files, and the
verifier called every one of them **MISSING** and refused to ship — rc 1 on a bundle
that was exactly right.

The tempting fix is circular: comparing the zip against `git archive` output would
compare the tool to itself and could never go red. So the tracked set stays the
reference, minus what the ref says to omit.

Two things had to be right, and the first was an adjacent-question trap of the kind
this codebase keeps paying for. `git check-attr export-ignore -- <file>` answers
**"unspecified"** for every file under `release/`: the pattern names the DIRECTORY,
and `git archive` prunes it before descending. Querying files alone would have
excluded nothing and refused everything, while looking like a fix. Ancestors are now
queried too, in the trailing-slash form the pattern actually matches. Second,
`--source=<ref>` reads the attributes **as of the ref** — the working tree's may
legitimately differ, and does whenever an older tag is verified, since v3.0.0 predates
this rule entirely. Too old a git REFUSES rather than guessing.

The control still holds: `--selftest` reproduces the published v3.0.0 asset byte for
byte, 258 of 258 members, export-ignore excluding 0 there.

### Fixed — the fuzzer's control anchor died in an ordinary refactor, for the third time

`plant_known_hole()` re-creates the pre-fix scanner so the run can prove it is capable
of failing, and it REFUSES if any anchor has moved — correctly, because a control that
plants three of four holes is not a control. v3.1.0's own change moved
`durable_python_open_w` onto resolved segments, the whole-file anchor
`for c in all_cmds for sg in segments(c)),` went to **0 occurrences**, and the tool
stopped running.

That is the THIRD anchor to die this way — `unparseable_command` (09-02), the longjob
`stripped` locals (09-06), this one (09-09) — so the shape belongs to the anchor, not
to the refactor. A whole-file anchor has to be long enough to be unique, so it ends up
carrying neighbouring COMMENT text and dies whenever a comment is rewritten. Anchors
are now **scoped to the rule they are about** (`(label, scope, old, new)`), needing only
to be unique inside it. `STRUCTURAL` moves to module level so the suite asserts the
floor against the same exemption set the run uses.

**The check that was missing is the point.** Both refusals were reachable in
milliseconds and nothing asserted them at commit time, which is why the only thing that
noticed was a CI leg nobody read. Two tests now do, in
`tests/test_fuzzer_policy_matches_hook.py`, each proven red against the exact v3.1.0
state.

### Fixed — `verb_unresolved` shipped with no seed, and adding one found a live bypass

The rule added in v3.1.0 to close a total bypass of all three hard blocks was the one
rule no mutant ever exercised. v3.1.0's own seed-coverage floor said so by REFUSING —
but only behind the anchor failure above, so nothing ever printed it.

With a seed, the fuzzer found a real hole in seconds:

| command | before | after |
|---|---|---|
| `$(echo rm) -rf <game>` | deny | deny |
| `/usr/bin/$(which rm) -rf <game>` | **ALLOW** | deny |

`_subst_verb_at_root` tested `t.startswith("$(")`, so a substitution **embedded** in the
command name escaped it, and an absolute path in front was enough. A substitution
anywhere in the name makes the command exactly as unknowable as one that opens it —
which is what `_SUBST`, this module's own predicate and already the body of
`has_unresolved`, has always said. Two independent paths answering one question, three
definitions apart: the same shape as the verb-resolver defects v3.1.0 fixed.

The narrow conjunct is untouched — a ROOT operand in the same segment is still
required, which is what priced this rule at 4 hits in 28,989 real commands — so the
widening is confined to segments that already carry a rooted operand.

> ⚠ **That "4 in 28,989" is a number we could not reproduce.** The v3.1.1 release
> reviewer replicated the pricing independently over **38,763 unique commands from 560
> session transcripts**, under two different root sets, and measured **0 hits for the
> old predicate and 0 for the new one — 0 gained, 0 lost.** Its instrument was checked
> the right way round: the same harness returns True for the seed and for five
> hand-built bypass shapes, so the zero is an ABSENCE and not a non-answer.
>
> The DIRECTION agrees — vanishingly rare either way — and a measured 0 gained is a
> stronger false-denial argument than 4, not a weaker one. But the original figure was
> taken against a different corpus and a different root set, both private to this
> machine, and neither is recoverable. **Treat "4 in 28,989" as testimony, not as a
> check you can run**, and re-derive it with its own roots before citing it again.
> The same caveat the v3.1.0 notes already attach to every 17,268-command replay
> applies here, and for the same reason. Named tests
cover both halves, the firing and the must-not-fire twin.

Fuzzer after: **rc 0**, 2,675 mutants, 25 seeds x 107 mutators, control planted and
rediscovering 126 bypasses, coverage **22 of 24** rules with the two unreachable ones
named. Before: rc 2, nothing exercised.

## v3.1.0 — 2026-09-09

**Upgrading DELETED your `.claude/backups/` -- every pre-edit backup, the audit
trail, and every known-good snapshot -- silently, rc 0.** On the machine where it
was found that was 982 files, 60 MB and 33 named snapshots. If you have run a
v3.1.0 pre-release installer with `--over-existing`, check that directory before
anything else.

Beyond that: a command name arriving through substitution walked past all three
hard blocks; `--dry-run` performed a real install; the source machine's secrets
travelled into the destination; the guard could be hung by ordinary shell; fourteen
of its rules had never been fuzzed at all; and `x4stats` reported "changes no
wares" over a 1,443-op economy overhaul. Four already-fixed defects could be
reinstated with the whole suite green.

This release is the close-out of the whole-codebase audit that followed v3.0.0,
plus a second review round of eight reviewers over 92 files and 50,296 lines.

### Fixed — the upgrade DELETED your recovery store, both installers, rc 0

`.claude/backups/` holds every pre-edit backup, the audit trail, and the known-good
snapshots CLAUDE.md mandates before any experiment. An upgrade with `--over-existing`
erased all of it, silently: rc 0, "install complete", and the word "backup" appearing
nowhere in the output.

MEASURED on the live install when this was found: **982 files, 60 MB, 33 named
snapshots reaching back to 2026-06-22.** Both `--method separate` and `--method
in-game`, both installers, reproduced independently by a second session with a
control and a twin.

`X4_COPY_PRUNE` is dual-purpose — skipped on the way IN and removed from the
DESTINATION on the way out, twice. v3.0.0's list held three build artifacts. The fix
that stopped the SOURCE machine's backups travelling added `.claude/backups` to it,
and the path silently inherited the destructive second meaning. install.sh states
that exact hazard in its own words — *"a pruned path is also DELETED from the
destination, which is right for a stale .venv and catastrophic for a config"* —
directly above the entry that put the recovery store on the pruned side. x4lock
deliberately leaves that directory UNLOCKED, so the lock guard could not catch it
either, and the `--over-existing` warning lists only toolkit files, so nothing
signalled the store was at risk.

Moved to `X4_KEEP_LOCAL`, whose documented semantics were already exactly right:
skipped on the way in, left alone on the way out. The test asserts SUBJECT and TRAVEL
as one exact set, so the destination's own store surviving and the source's files not
arriving cannot be satisfied by the other going wrong — plus a CONTROL (a real build
artifact is still pruned, or the test passes for an installer that prunes nothing) and
a TWIN (an unrelated user directory under `.claude/` survives).

### Fixed — three installer CRITICALs these notes previously did not mention

⚠ **These are in v3.1.0 and were absent from this file until 2026-09-08.** A user
reading it could not learn that an installer had leaked an API key.

- **`--dry-run` performed a REAL install in place, and ran `setup.sh` doing it.** Both
  installers, both methods: rc 0, no dry-run banner, "install complete" printed, and
  `setup.sh` synced dependencies. With `--unpack` it would also have run
  `bin/unpack-reference.sh`. A regression of this arc.
- **The source machine's secrets travelled.** `X4_KEEP_LOCAL` matched two exact names,
  so the `.bak-<stamp>` and `.tmp<pid>` siblings of the path config — which hold
  `X4_NEXUS_KEY` by construction — were copied into the destination. Reproduced on
  both installers, rc 0 and silent.
- **`--method global` had no locked-target precheck**, so PowerShell clobbered a
  read-only user skill with a forced copy and returned 0.

### Fixed — the rest of the installer round

- **The global gate named skills this toolkit does not ship.** It listed every
  `~/.claude/skills/x4-*`, so a user's own `x4-mycustom/` was reported as a file the
  install "would REPLACE" and the run refused until `--over-existing` was passed —
  against a directory the installer never touches. The comment above it already
  claimed the correct property, and the AGENTS leg beside it already enumerated from
  the toolkit. Both legs are source-derived now. The parity test ASSERTED THE DEFECT —
  it required the literal destination glob — so the fix turned a named test red; that
  assertion is inverted in the same commit, with the reason written at it.
- **The read-only guard was inert in bash, and both installers refused after
  writing.** bash tested the skill DIRECTORY, so a read-only file inside a writable
  directory was invisible: the run reached the copy, failed with a permission error,
  and ended "a partial install is not recoverable". In BOTH, the check ran after the
  config writer, so both printed "wrote x4-paths.env" before refusing and PowerShell
  then said "Nothing has been changed." Hoisted ahead of every write in both, and
  asserted on the ARTIFACT rather than the wording — the wording was the untrue part.
- **An INDENTED owned key won over the real one.** The key extraction kept leading
  whitespace, so an indented X4_GAME assignment was not recognised as owned, was
  carried over, and was emitted AFTER the authoritative line — and bash sources the
  last assignment. A re-install with a new game path produced a config whose effective
  game root was the STALE one, rc 0, silent. Three sites shared that extraction,
  including the precondition this arc added, so the new precondition was blind to it.
- **The refusal ORDER was reversed between the installers**, so PowerShell told the
  user to unlock and re-run against a destination they had not authorised replacing —
  advice that leads back to a refusal for the real reason.
- **An unparseable global settings.json threw a raw interpreter dump** with no backup
  and no crafted message, where bash degrades cleanly. Empty refuses too: an empty
  file cannot be told from a truncated one, and overwriting a user's GLOBAL config on
  that guess is the wrong call.
- **PowerShell rewrote only markdown** where bash rewrote every shipped file — a
  divergence created by this arc's own fix. Zero cost today, latent from the first
  skill that ships a script.

### Fixed — a substituted command name walked past all three hard blocks

A command whose NAME arrives through substitution returned ALLOW where the plain
spelling denies. An unknown OPERAND still reaches the conservative branch; an unknown
VERB reached no rule at all, so it took every verb-keyed rule with it. The verb
resolver only ever splices a bare command name out of the assignment table, so a
substitution is unresolvable by construction.

The guard fuzzer could not find it: its VERB axis has four mutators and every one is a
SPELLING of a literal name — .exe, absolute path, ANSI-C, Windows path. None is a
SUBSTITUTION. A bug history is not a grammar.

Priced before shipping, over 28,989 real commands from 110 session transcripts,
because the obvious rule is far too broad:

| rule | corpus hits |
|---|---|
| any unresolved verb token | 679 (2.3%) — mostly a variable holding a PATH |
| a substituted verb, any target | 834 (2.9%) |
| **a substituted verb WITH a rooted operand in the same segment** | **4 (0.014%)** |

The last is what shipped, and the root test is on that segment's OWN operands, never on
a root appearing anywhere in the command.

### Fixed — three more guard holes and two silent channels

- The verb resolver reached only two thirds of the rules: three re-derived their own
  segment walks and never saw a resolved verb, so the variable spelling of a rooted
  SEARCH was False while the same spelling of a rooted DELETE was True. Two
  independent paths answering one question.
- An APPEND into the reference tree was ALLOW while a truncate hard-blocked — the same
  primitive on the same file, and the tee spelling was already caught, so the two
  forms of one append disagreed. The documents rule beside it already used the wider
  set, so the less valuable tree had the wider channel and the read-only one the
  narrower.
- The re-unpack rule ANDed three predicates over the RAW command text, so a comment or
  a quoted string naming the tool, the flag and the reference path was a
  NON-OVERRIDABLE DENY on a command that unpacks nothing. It refused a probe that
  merely LISTED the cases while the finding was being reproduced.
- The canary's NOT-CHECKED disclosure was invisible on rc 0 — the one branch the hook
  discards, and the common case (one root configured, one not, nothing lost).
- The identifier scan dropped unreadable files on a bare continue and then reported a
  file count that INCLUDED every file it never opened. Now counted, named, and a
  refusal rather than a green over files nobody read.

### Fixed — live_query: compare refused the ids it printed

One helper canonicalises an id; its sibling canonicalises AND records it in the
allowlist. Two verbs record; compare alone did not, so every id it PRINTED was absent
from the allowlist, and feeding one straight back to the component verb was refused
with "most likely mistyped, or left over from before a UI reload". Both named causes
are false — the tool printed the id one query earlier. That verb exists to name the
objects two enumerations disagree about, so refusing to follow them defeats it.

And one unconvertible object discarded the whole enumeration: the id canonicaliser
raises above 2^53, contained only by the verb's single outer pcall, so four good rows
and every denominator went with it. The rule is right; the containment was at the
wrong granularity, and the flag reader beside it already had the correct shape — per
object, counted, announced. Now disclosed in the header.

Recorded and NOT repaired: each UI reload adds a load-hook listener in the test
harness. The code's defence is that the global table does not persist, which is
measured for that table and NOT for the engine's own UI event registry. The real-engine
behaviour is UNKNOWN and the settlement is five seconds in game; it has not been run.

### Fixed — nine tests that could not fail, four of them holding this arc's own fixes

A review pass mutated production and MEASURED that four already-fixed defects could be
reinstated with the ENTIRE SUITE GREEN. The suite was not merely missing new bugs; it
was not holding this release's own repairs in place.

| mutation | before | after |
|---|---|---|
| a release gate's rc-2 floor turned into a pass (named in no other test file) | green | RED |
| delete the dedup guard in the dropped-overlay reporter | green | RED |
| rename the entry point so the skip-channel pin examines nothing | green | RED |
| an unwired call site that merely MENTIONS the channel it ignores | green | RED |
| revert the canary parse fix, so an ordinary rename reads as DATA LOSS | green (34 tests) | RED |
| re-declare a shared constant as a literal (identity on a small int is satisfied by interning) | green | RED |
| move a list's clear below the walks that fill it | green | RED |
| replace the derived depth fixtures with a literal | green | RED |
| rename a scanned root away from the bare-return guard | green | RED |

The gate census matched any SystemExit raise without checking its argument, and every
gate ends by raising SystemExit around its main() — so boilerplate satisfied it for all
31.

Three assertions written earlier in this same arc went the same way and are fixed
here: a refusal-list count keyed on a drive letter, which counts ZERO and passes
without examining a line on a runner whose temp is elsewhere — the leg being promoted
to gating; and two disjunction forms where the assertion was satisfied by a word
appearing anywhere in the output, including the tool's own help text.

### Fixed — the guard could be made to do unbounded work

`hook_facts.resolve()` substitutes what a command assigned to itself. It loops five
times, under a comment reading *"bounded: nested vars, never a loop"*. That is true,
and it is about the wrong axis: the ITERATION COUNT was bounded and the STRING was not.

A value that names its own variable re-expands every reference on every pass. MEASURED
at a clean **×9 per pass**:

| pass | chars | factor |
|---|---|---|
| 1 | 916 | 229 |
| 2 | 10,316 | 11.3 |
| 3 | 94,916 | 9.2 |
| 4 | 856,316 | 9.0 |
| 5 | 7,708,916 | 9.0 |

so the four-character token `${B}` reached **7.7 MB** inside the five passes the loop
already allowed. For one **949-character** command out of real session history that
became **23,128,230 characters** of carried command, **531,448 segments**, and an
**18.3 GB working set** — 0.7 GB free of 31.8 GB, with a game running. This is the
blocking PreToolUse path, so it is a hang of the whole session, and it needs no
adversary: a self-referential assignment is ordinary shell.

Every neighbouring bound was sensible ALONE — `_MAX_CARRIER_DEPTH` 4, `_MAX_CARRIED`
250 (10× the observed maximum, with a census behind it), the five-pass loop. None
bounded SIZE, and nothing multiplied them together. **A product of individually
sensible bounds is not a bound.**

`_MAX_RESOLVED = 65536` (3.3× the longest of 17,268 real commands). On exceeding it,
`resolve` returns the PREVIOUS COMPLETE PASS with the references still standing, which
`has_unresolved()` already reports as unresolved. Truncating to the ceiling would be
equally "bounded" and would hand every path rule a SHORTER OPERAND while looking
successful — the narrowing-step defect the file exists to catch.

> ⚠ **ABOUT THE "17,268 real historical commands" FIGURES BELOW, AND YOU CANNOT
> REPRODUCE THEM.** That corpus is this machine's own session history. It is not in
> the repository and cannot be — it is a private command log — so no harness ships
> that replays it and no CI leg runs one. Every per-item replay number in this
> section ("5 verdicts moved", "14 moved, 0 loosened", "1 of 17,268", "297 s — 47
> s") was measured with an ad-hoc instrument against private data, by us, once.
>
> They are reported because a guard change with no per-item evidence is worse, and
> because the DIRECTION ("none loosened") is the claim that matters. But they are
> **our measurements, not your checks**, and the corpus has since grown to 18,632,
> so even we cannot re-run the same population. Treat them as testimony. The
> reproducible evidence for these fixes is the unit tests, `fuzz-guard.py` and
> `verify-hook-tests.py`, all of which ship and all of which you can run.

Verified by replaying **17,268 real historical commands** against the previous
guard: **1 verdict moved**, and it is the incident command itself, going from
`MemoryError` — no verdict at all — to a real one. **0 loosened.** The whole
corpus also stops taking **297 s** and takes **47 s**, and exactly **1 of 17,268**
commands reaches the new ceiling.

### Fixed — TWO total bypasses of every hard block, both in verb resolution

**Upgrade for these two if for nothing else.** A rule keyed on the command NAME is
only as good as the resolution of that name, and both defects made `verb()` return
something that is not the command. A wrong verb takes all three HARD BLOCKS with it at
once, so the blast radius is not one rule — it is the whole verb-keyed half of the
guard. Both are PRE-ARC: the same behaviour shipped in v3.0.0.

**1. A wrapper flag whose VALUE is a word became the command name.** `_verb_token`
skips any `-flag`, and after a wrapper it also skips a token matching `_WRAPPER_ARG` —
which matches only a number, `{}` or `+`. A flag whose value is a WORD therefore left
that word standing as the verb:

| command | resolved verb | verdict |
|---|---|---|
| `env -u X4_GAME rm -rf <game>` | `x4_game` | **ALLOW** |
| `sudo -u root rm -rf <game>` | `root` | **ALLOW** |
| `env -C /tmp rm -rf <game>` | `tmp` | **ALLOW** |
| `timeout -s KILL 5 rm -rf <game>` | `kill` | **ALLOW** |
| `nice -n 5 rm -rf <game>` | `rm` | deny |
| `xargs -I{} rm -rf <game>` | `rm` | deny |

The last two worked **by accident of their argument shape**, which is what made the gap
invisible — the wrapper handling looked exercised. `env -u VAR cmd` is not an
adversarial spelling either: it is the documented way to clear a root in this
workspace. Fixed with `_WRAPPER_VALUE_OPTS`, a per-wrapper table rather than a union
set, because honouring `-u` for a wrapper that has no such flag would consume the REAL
verb and return its first argument — a MISS, strictly less safe than the bug.

**2. A QUOTED token in verb position short-circuited the whole resolver.**
`for t, quoted in tokens(seg): if quoted: return t` ran BEFORE the assignment, wrapper
and flag-value skips, so quoting one token in the prefix decided the command name.
MEASURED per item over 9 seeds: **7 of 9 rules flipped from fire to ALLOW** —
`rm_hits_game`, `rm_targets_reference`, `rm_saves`, `git_add_all`,
`git_wipes_x4_dir`, `writes_reference`, `sed_i_in_game_or_profile`.

    FOO="bar" rm -rf <game>          -> verb `foo=bar`   -> ALLOW
    env -u "X4_GAME" rm -rf <game>   -> verb `x4_game`   -> ALLOW

★ **The two rules that did NOT flip are what pin the cause.** `dollarq_after_pipe` and
`longjob_foreground` are the only two that are not verb-keyed, so their immunity says
the defect is in verb resolution and not in any predicate — worth more than the seven
hits. This one also defeated fix (1) above, landed earlier in the same arc: one quote
was enough to walk past it.

Neither was reachable by the fuzzer, and it says why in its own docstring: it mutates
"the SYNTAX AROUND the dangerous operation", holding the operand fixed. Five mutators
derived from the GRAMMAR of a wrapper prefix now cover the shape and found ten further
bypasses within seconds of existing — `git_wipes_x4_dir` and `git_discards_x4_files`
run their own token scan, so fixing `_verb_token` alone left them behind.

Per-item replay over **17,268** real commands after fix (1): 5 verdicts moved, none
tightened, none loosened.

### Fixed — 61% of the guard had never been fuzzed, and the run said nothing about it

`scripts/fuzz-guard.py` derives its verdict map from `protect-bash.sh` and refuses
below a rule-count floor. It never asked how many of those rules any SEED exercises.
MEASURED: **9 of 23 (39%)**. Fourteen rules had no seed at all while the run printed
a healthy mutant count and *"no bypass found"*. ⚠ This line described those
fourteen as "six DENIES and one HARD BLOCK", which does not add up and understated
the composition; the release reviewer measured **9 deny, 3 advise, 2 ask, with two
unseeded HARD BLOCKS**. The corrected point is worse than the original: more than
one hard block was unfuzzed, and this same section elsewhere says "all three HARD
BLOCKS".

Twelve seeds later (12 — 24; the tool reports **24 seeds** today), coverage is
**21 of 23**, with the two unreachable ones NAMED
(a syntax fuzzer cannot construct a pathological input size or a numeric payload
field; both are unit-tested instead). Two refusals were added: a reachable rule with
no seed, and a seed that makes no policy rule true.

**Those seeds immediately found bypasses** in rules that had shipped and were being
relied on. ⚠ This entry said **36**; the v3.1.0 release reviewer re-ran the gate at
that commit and measured **68**. The larger figure is the reviewer's and is not
re-derived here — what is certain is that the original number was wrong and too
small. The named classes below are what matter:

- **ANSI-C quoting** — `$'\x72\x6d'` and `$'\162\155'` both spell `rm` and reached the
  verb resolver as literal escape text, coming out as `x6d` and `155`. A total bypass
  of all three hard blocks. `$"…"` is left literal, as bash leaves it.
- **`durable_python_open_w` read `segments(body)`** while the carrier list existed —
  21 bypasses; `bash -c` alone was enough to overwrite `KNOWLEDGEBASE.md` unseen.
- **the git subcommand scan stepped over leading assignments but not wrappers**, so
  `exec`, `setsid`, `nice -n 5` and `timeout 5` each turned a destructive `git` inside
  the game root into an ALLOW. `timeout` is the prefix this workspace types on purpose.
- **`dollarq_after_pipe` called `strip_heredocs` on already-stripped input.** That
  function keeps the OPENER, so the second pass met a dangling `<<X` with no terminator
  and consumed everything after it — including the command being judged.
- **a parameter expansion could carry the exit status and a file mode out of sight.**
  `_apply_op` already modelled suffix-strip, default-value and array-index; the rules
  never asked.
- **`longjob_foreground` and `dollarq_after_pipe` read `body`**, wrappers intact, while
  every path rule reads the unwrapped carrier list.

Verified per item over **17,268 real historical commands**: 14 verdicts moved, all
tightening, **0 loosened**. Every one is a `dollarq_after_pipe` hit and every one
was hand-checked as a genuine `$?` immediately after a pipeline — a deny that
fired on correct work would be worse than the bug it fixes.

### Added — `x4stats` resolves a `sel=` to the ware it acts on

`candidate_wares` found a ware only when a `<ware>` element was present as an op
payload, so it saw nothing for `<replace sel="//ware[@id='ore']/@price_average">` —
the default X4 idiom, and the example in the project's own CLAUDE.md. MEASURED over a
125-mod install: 37 mods supply `libraries/wares.xml` and **5 reported "introduces/
changes no wares" — all five wrong**, the largest hiding 1,443 ops.

`_merge.AppliedOp` gained `target_keys`: for each node a selector resolved to, the
nearest ancestor-or-self carrying an `@id`. Opt-in, so the corpus-wide store build pays
nothing; captured at resolution time, because a `<remove>` detaches its target and a
detached node has no ancestor chain left.

| | before | after |
|---|---|---|
| mods reporting zero wares | 5 | **1** |
| unattributed ops | 3,414 | **190** |
| the 1,443-op economy overhaul | 0 wares | **350 wares** |

The residue is explained rather than rounded away: of **190** residual ops — the
figure the table directly above already gives; this line said 192, double-counting
`sve_vro_trim`'s 3 — **187 are ops
the engine itself would not apply** — 116 ambiguous selectors (RFC 5261) and 73
matching nothing. Attributing those would claim a change the game never makes.

The one mod still reporting zero is correct too: `sve_vro_trim` carries **23 ops and
every one is a `<remove>`**. Twenty resolve to a ware — which is why its
unattributed count falls 23 → 3 — but a removed ware has no post-state, so there is
nothing left to compare a price against. Attributed and not comparable are two
different answers, and they are kept apart.

### Fixed — the reference marker did not travel with the tree it describes

`check-reference-version.sh` compared the live build against a DETACHED
`.reference-buildid`. The copies had diverged (23524486 vs 23660954) while the sentinel
inside `reference/` and the live game both said 23660954, so it announced a stale
`reference/` **at every session start** — recommending a ~60 GB re-unpack. It was also
**silent on a genuinely stale tree** when the detached file happened to be current.

It now reads the sentinel, which is written into the tree by the unpack and cannot
drift from it. MEASURED: 1 of 510,711 files under `reference/` has a newer mtime, and
it IS the sentinel — XRCatTool preserves catalog timestamps.

The same stale mtime had produced a wrong root cause inside `gates/schema_sweep.py`,
now corrected in place to an honest UNKNOWN that names what it is not.

### Fixed — `install.ps1 -Method global` was 100% broken, and it shipped

`TrimStart('', '/')` on the install path. Every Windows user choosing the global
install method got a broken toolkit, and the arm reached no gate at all. The missing
over-existing gate is added; the installer CI legs go 3 → 7.

### Fixed — checks that returned a clean zero over having examined nothing

The audit's largest single class, and all of it PRE-ARC:

- **`_check`: 11 of 14 `build_effective` call sites discarded the dropped-overlay
  channel.** An overlay the merge could not READ left the store recording
  `origin='base'` — "still vanilla" — in the same grammar as a verified value, with 11
  unreadable files in the live corpus. ⚠ An earlier draft of this line said those 11
  were "reaching it"; the fixing commit `002ebee` had priced the live impact at
  **ZERO** ("this changes no verdict now"). A deliberately measured null inverted
  into a live-impact claim — the structural defect is real, its live cost today
  is not.
- **`_check`: three places the validator reported OK over work it had not done**, and
  a fourth where adding `--update --xsd-fast` REMOVED disclosures the default run
  emits.
- **`_refs`: a `<t>` added INTO an existing page was not counted as a definition**, so
  a reference that resolves in game read as unresolved.
- **`gates`: 9 of 15 `reader_edges` cells asserted nothing**, and "can end non-zero" is
  not "can report a finding" — plus four more gates returning a clean 0 over an empty
  examination.
- **`perf_guard`: a DETECTED regression reached a `NameError`** instead of a verdict.
- **`lockstep`: the working tree chose WHICH repo it judged**, and `stamp`'s
  denominator was its own glob.
- **`basex`: four ways the negative-claim pipeline confirmed a negative it had not
  checked.**
- **`x4debug baseline`: an unresolved extensions root fingerprinted the CURRENT
  DIRECTORY.**
- **`x4xref`: the "real negative" hint was case-SENSITIVE while its query was not.**
- **`x4canary`: a canary that could not enumerate its repos, or could not LOAD, claimed
  the user had lost a file.** Both are now rc 2 — "could not check" — never DATA LOSS.
- **`generate-baseline`: an unconfigured `PROFILE_DIR` is rc 2**, like every other
  refusal.

### Fixed — `_paths`: an env-layer derivation shadowed an explicit setting

`X4_TOOLKIT` is now a LAST-RESORT source for `reference()` rather than one that
shadows an explicit `x4-paths.env`. `install.sh` tells every Windows user to
`setx X4_TOOLKIT`, which is exactly the trigger.

### Fixed — the mod's enumeration bound existed and three verbs never reached it

`MAX_ENUMERATE` lived only in `render`, so `compare`, `containerprobe` and
`censusprobe` walked past it. MEASURED at N=5,000: compare made **30,008** engine
calls and containerprobe **25,012**.

**Two of the three now REFUSE; the third reports its cost instead, and that is a
deliberate difference rather than an omission.** `compare` and `containerprobe` walk
per object at roughly five engine calls each, so a bound is the only safe answer.
`censusprobe` has no per-object call at all — every entry is one call returning a
whole list — so there is nothing for a bound to protect, and it emits a `COST` row
naming its own enumerations, objects touched and scope. An earlier draft of this
entry said "all three now refuse", which the release reviewer measured as false.

### Fixed — a hook's warning was FILED above 10,000 characters, worst-first

Claude Code files a hook's model-facing output above **10,000 CHARACTERS** and shows the
model only a PREVIEW. No error, exit code unchanged — indistinguishable from
success. What was measured here is that the HEAD sentinel survives and the TAIL does
not; the preview's SIZE was not measured and is deliberately not quoted.
MEASURED on CC 2.1.263, four arms per channel with head+tail sentinels, discriminating
on whether the TAIL survives:

| channel | 500 | ~10,000 | 10,001 | 30,000 |
|---|---|---|---|---|
| bare stdout | both | both | head only | head only |
| `additionalContext` | both | both (9,950) | head only | head only |

The 9,950 arm is load-bearing: its raw stdout was 10,032 characters and still arrived
whole, so **the cap is on the CONTENT the model receives and the JSON envelope does not
count** — budget at 10,000 flat, never "10,000 minus envelope". The constant has already
moved once across a CC bump — REPORTED by an outside audit, not measured here;
what we measured is 10,000 on 2.1.263 — so re-deriving
it is a named step, and this is the only place it is written down.

**The SessionStart canary crossed it, and the failure was INVERTED AGAINST SEVERITY.**
It wrote one unbounded line per lost file — a 400-item report measures 36,638
characters. One lost file sails under the cap; a directory-level loss, the case the tool
exists for, is the one that gets filed. **Worse than the cap was the ORDER**: the preview
keeps the HEAD, so what survived was the inventory and what was cut was *"do not re-run
whatever wrote it"* — the one line that stops the recoverable state being destroyed. That
ordering was backwards independently in both layers. The directive now prints BEFORE the
list, all three lists route through one `_emit()` that DISCLOSES its own bound (a bare
slice makes the number wrong, not just the list), and `session-canary.sh` names WHICH
`x4canary` it resolved — two copies exist on THIS install (n=1) and the hook silently
picked the first it found.

A `deny` is honoured at any reason length (500 / 15,000 / 200,000 all blocked against a
no-hook control), so the cap cannot weaken a hard block.

### Fixed — hooks reading a DEAD input contract would have failed OPEN

`CLAUDE_TOOL_INPUT`, `CLAUDE_TOOL_USE_RESULT` and `CLAUDE_SESSION_ID` are a legacy
contract current Claude Code does not set. A hook reading one sees an EMPTY value, falls
through its first guard clause and exits 0 — byte-identical to deciding "this is fine",
which is F79's exact shape one interface over. MEASURED here: **zero hits**, so this
pins a property that currently HOLDS rather than fixing a live defect. It is a static
ban with a coverage floor, because the mutation gate structurally cannot reach the class:
a dead input read means the hook never blocks, so there is no behaviour for a mutant to
weaken.

### Fixed — `_effective._write_db` leaked its temp file, and the reclaim could never collect one

`<db>.<pid>.tmp` is created at `sqlite3.connect`, and the `try:` carried
`finally: con.close()` and nothing else — it closed the HANDLE and never removed the
FILE. Every exception between creation and `os.replace` leaked a fully materialised temp,
permanently; the only cleanup sat in the `os.replace` error handler, the one failure mode
somebody had already imagined. The sharpest case is architectural rather than exotic:
`refuse_if_mutating()` is called INSIDE that block by design, so any store build
overlapping a mutating gate deposits one.

MEASURED: three orphans in `dev/_registry/` from 2026-08-26, each 53,248 bytes with
**zero rows in every table including `meta`** — so they died before `commit()`, which is
what makes them a leak rather than a half-written store. Build-then-replace is atomic and
the installed store was never at risk.

★ **The generalisable half is the RECLAIM, not the missing `finally`.** The startup sweep
was `os.getpid()`-keyed, so it could only ever match a leftover from THIS process — and
an orphan's creator is by definition gone. **A reclaim scoped to the current process
looks present in review and is unreachable in fact**, which is why reading the code never
found it. The contrast lives in the same package: `gates/mutation_probe.py` also records
a pid and restores BY NAME from a pristine directory. Same ingredient, load-bearing in
one and decorative in the other. Fixed with the `except BaseException` → unlink → raise
shape copied unchanged from `_registry.save`, nested so the handle closes before the
unlink (the documented rationale behind `_registry.save`'s shape: Windows refuses to
unlink a file it holds open — inherited with the code rather than re-probed).
Reclaiming a FOREIGN orphan is
deliberately left explicit and manual: deciding a stranger's temp is abandoned means
deciding its pid is dead, and on Windows `os.kill(pid, 0)` maps to TerminateProcess.

### Fixed — the release review's own round: more verdicts with no reachable red

Four reviewers read the whole `v3.0.0..HEAD` range in that round; a LATER round of
EIGHT read it again (92 files, 50,296 lines, denominators declared per reviewer)
and found the installer criticals and the suite holes above, so what follows was
true of that round rather than of the release. Everything THAT round raised is
closed, in-arc and pre-arc alike; the later round's findings are the sections at
the top of this release.
almost throughout, which is the same shape the audit above kept finding — a verdict
whose PASS branch is reachable from an input nothing looked at. It is worth stating
that the round which fixed that class contained eleven more of it.

**`x4canary`, and three of the four fail in the DATA LOSS direction.** A tracked file
with a NON-ASCII name that was merely EDITED reported as `DELETED (tracked, now
missing)` with rc 1 — the SessionStart banner. TWO layers, and fixing either alone
left it broken: `core.quotePath` defaults to true so the path arrives octal-escaped,
and underneath that `_git` used `text=True` with no `encoding=`, so git's UTF-8 was
decoded with the locale codepage (cp1252) and the path was still mangled once
unescaped. The second layer was found only by re-running the sandbox AFTER the first
fix was declared done and reading the CODEPOINTS, which disagreed with the rendering.
Also: a rename compared against `HEAD:<new path>`, which does not exist, so a file
renamed and then EMPTIED read as benign drift; an allow-list of four status codes
where the question is binary, so `RM`/`MT`/`AT` were never size-checked; and an
unreadable repo DOWNGRADING a found loss in a different repo from rc 1 to rc 2, which
is the code the hook reads, so the banner never fired.

**`perf_guard`: a mod that CRASHES left the comparison entirely.** `measure()` caught
every exception and `continue`d, directly beneath a comment reading "a crash is a
finding, not a timing" which it then made neither — so the gate printed "No per-mod
regression beyond tolerance" and returned 0. A `validate()` that RAISES where it used
to complete is the harshest regression this gate can meet. Crashes are now a second
channel: rc 1 for a baselined mod, rc 3 for one the baseline never covered, and a mod
that is simply no longer installed stays the benign note it always was.

**`oracle_reverse`: agreement needs two sides.** Both disagreement sets are
intersections with our own tree, so a tree that failed to build made them empty BY
CONSTRUCTION — the engine named the misses, we examined nothing, and the gate printed
"We agree with the engine on every checkable complaint in this log". The existing
refusal covered the other empty input (a log with no complaints), which is exactly why
this one looked handled.

**`x4debug crosscheck`: rc 0 over nothing compared**, which reads identically to a mod
the engine and the validator concur on and is equally what a log from a session where
the mod never loaded looks like. And a DEGRADED validator run — where the command
already prints "'predicted' is not a complete prediction" — no longer returns the code
that means "we agree".

**`x4xref`: a sidecar nothing ever wrote.** `_sidecar()` had exactly one caller,
`_exclusions()`, a READER. Unreadable files were printed once at build time to stderr
and discarded, so every later "nobody references X" was rendered with no exclusion list
at all — what `_exclusions`' own docstring calls "the most confidently wrong answer this
tool can give". It is now written at build, empty file included, so a leftover cannot
attach stale exclusions to a clean index. Separately, a MISSING extensions directory
skipped the whole mod walk wordlessly and returned 0 over a base+DLC-only index; that
is now rc 2, while an existing but EMPTY one stays accepted.

**`staleness`: three UNKNOWNs wearing STALE's banner.** `Verdict.determinable`, its
docstring and the UNKNOWN banner all existed — "a banner that says STALE when it means
UNKNOWN sends the reader to rebuild an index that may have been perfectly current" —
and `check()` never set the field. One of the three even carried a comment reading
"Absent is UNKNOWN, never fresh" directly above a return that said STALE. A fourth case
is new: the CURRENT fingerprint can fail to compute on a configured machine whose roots
moved. And `main()` now exits 6 rather than 5 for an undeterminable verdict, because it
was printing "FRESHNESS UNKNOWN" while returning the code for "the world moved".

**`ask.py`: an unguarded `json.loads` on the path every query takes**, so a truncated
coverage file escaped as a traceback with rc 1 — which in this toolkit means "the thing
you asked about has findings".

**`_refs`/`_check`: the into-page COLLISION half**, which the reference-half fix's own
docstring recorded as still open and did not say so. `check_page_collisions` fed
`text_defs` throwaway holders that DISCARD the selector — the one place an into-page
op's page id appears — and matched `<add>` alone, never `<replace>`. So the two forms
that can actually clobber a base string were the two the warning could not see.

**The skip ceiling: an unusable `X4_MAX_SKIPS` printed "that is a NON-ANSWER, not a
pass" and then left the run GREEN.** The realistic accident is emptiness rather than
garbage — a workflow writing the variable from an expression that evaluates to nothing
sets `""`, which is not `None`, so a CI leg loses its ceiling while every log line still
says it ran with one. That is the 125-dormant-tests failure with an extra step. The
ceiling had never been pinned by any test; it is now, all five branches.

**`generate-baseline` and `verify-hook-tests`: three more passes over an empty
population.** A missing `extensions/` wrote a header-only mod list and still printed
"Baseline written to: …" — in a RECOVERY artifact, whose own comments already make this
argument about the game directory and not about its contents. And both halves of
`verify-hook-tests` scored a perfect zero over an empty set: "0 of 0 uncaught" and "0 of
0 coverage gaps" each read as a clean run, and the coverage half is reachable without
anyone editing a list, because its predicate list comes from a live call.

Every fix in this round was mutation-checked by reverting only its subject with the
tests kept, and every subject restored byte-identical. Two of the fixes were WRONG
first and their own twins caught them: a broad `except` in `staleness.check` swallowed
the `EngineUnavailable` that `main()` owns, reintroducing the confusion the change
removes; and the canary's loss-outranks-unreadable rule fired on an unreadable repo's
own EXPLANATION, because `check()` returns its reason in the loss slot.

### Fixed — the installers walked into a lock this release is the first to recommend

`scripts/x4lock.py` marks the files you cannot afford to lose read-only, and this
release's README is the first to tell you to run it. Neither installer knew the lock
existed. An upgrade over a locked install died PART WAY THROUGH — `cp: cannot create
regular file '.../.claude/./x4-paths.env': Permission denied` on bash, `Access to the
path ... is denied` on PowerShell — and left orphaned `.bak` files behind, TWO of them
on the PowerShell side (one from the copy, one from the config writer).

★ **The defect is PRE-ARC and the TRAP is not.** `install.sh`'s backup/restore block
predates v3.0.0 and `x4lock` shipped IN v3.0.0, so by the usual rule this is "a defect
the users already have". But v3.0.0's README mentions `x4lock` **zero times**: this
release is the first that instructs people into the state that breaks the upgrade. That
distinction is now written into `docs/REVIEW-SCOPE.md` as a third triage bucket, because
both sessions reviewing this reasoned correctly from the old rule and both nearly waved
it through.

**What it does now, stated as behaviour rather than as a repair:** an install that
would OVERWRITE any read-only file REFUSES before writing anything, names the files
and gives the unlock / re-run / lock sequence. An install that does not need to change
a file never touches it, so a locked config on an unchanged upgrade is a non-event.
Both installers reach the SAME verdict on the same input.

⚠ So it still cannot upgrade a LOCKED install, and should not: it refuses cleanly and
tells you how to proceed. An earlier draft of this entry claimed the upgrade now works,
which was false — `--method in-game` could not, and the release reviewer caught the
note before it shipped.

The fix removes the mechanism rather than handling it. `x4-paths.env` and
`settings.local.json` are simply never copied from the source — a new `X4_KEEP_LOCAL`,
deliberately distinct from `X4_COPY_PRUNE`, which also DELETES from the destination
(right for a stale `.venv`, catastrophic for a config). The backup/restore round trip is
DELETED: it backed up a file, overwrote it, and restored it — three operations to reach
the state of having done nothing, and the source of the missing-`trap` hazard.
`write_paths_env` now skips an unchanged config, so an upgrade that resolves the same
paths never writes, which also stops hand-written comments being lost on every run. And
`precheck_config` refuses UP FRONT when the config must change and is read-only, naming
the unlock command, including under `--dry-run`.

⚠ **Half the fix was no fix, and only the harness said so.** With the copy skipped but
the round trip still present, the upgrade STILL failed — at the restore, which also
writes. And on PowerShell the skip had to sit ABOVE the config writer's own backup, not
merely inside the function: placed below, the install SUCCEEDED and still left a `.bak`.

`test_install_over_existing.py` drives a real install over a real fixture install and is
parameterised over **both** installers, 58 collected cases as of this release --
same cases, same assertions -- so a
divergence in VERDICT fails by name. That matters because `test_installers_agree.py` is
entirely static: it compares item lists and would have passed on two installers that
agree about structure and disagree about whether they clean up, which is exactly what
was true — PowerShell had `finally` where bash had none, **and both still failed this
path**.

### Fixed — the content axis could report a spurious STALE

`_freshness._reference_survey` was NON-DETERMINISTIC over an unchanged tree. MEASURED
over 400 freshly built trees, two surveys taken back to back with no filesystem change
between them:

| | unstable | add@3 | del@3 | edit@1 |
|---|---|---|---|---|
| with directory mtime | **1** | 400 | 400 | 400 |
| without | **0** | 400 | 400 | 400 |

The directory mtime was the only value in the fold that could move without the tree
moving, and it was redundant: a directory's mtime changes when a child is added, removed
or renamed — all of which also move the child count or the name list — and does not
change for a child's in-place edit. Identical detection, zero instability.

It surfaced as a test failing 1 of 2 COLD runs on `windows-latest`, the leg that GATES
the release, so it would have reddened a release for a reason unrelated to it. The
failing test was never the defect: the depth-4 add it asserts about moved the survey **0
times in 400**.

### Fixed — a count quoted as if it were the whole file

`x4effective ls region` answered a confident number over a file it had only partly
indexed. `_extract_registry` indexes one child tag, and a registry may hold others; the
anti-vacuity guard fires only when children exist and none are keyed, so it catches a
wrong key attribute and is blind to a wrong TAG. MEASURED on the effective tree once the
denominator was reported:

    libraries/wares.xml             2492 <ware>   of 2497   (5 not indexed)
    libraries/jobs.xml              2082 <job>    of 2083   (1)
    libraries/sound_library.xml     2873 <sound>  of 2935   (62)
    libraries/region_definitions    318  <region> of 469    (151)

Four registries, not the one the review found — including `sound_library`, which the
reviewer checked by hand and judged hypothetical. This is the toolkit's own rule one
level up: an instrument that returns nothing must say whether that is an absence or a
non-answer, and an instrument that returns a NUMBER must say what it is a number OF.

### Also — the rest of the review round

- **The hook output cap was defeatable three ways**, and one was a fail-open inside the
  guard written to close a fail-open: `x4_len` returned success whatever its python arm
  did, so a python that resolved but failed printed nothing, the length came back empty,
  and 25,000 characters passed through WHOLE. A cap below its own notice width inverted
  the bound (a 200-char cap returned 5,097). And `emit()` was never bounded at all while
  seven rules interpolate the whole command — measured at 20,126 characters of
  `permissionDecisionReason`.
- `scan-identifiers.py --history` now reads the commit MESSAGE as well as the diff, uses
  a word-boundary test (the substring form ran at an 80% false-positive rate over
  history), and is WIRED INTO CI — it shipped invoked by no step at all.
- A push to `ci/pre-3.1` triggered no run whatsoever; `ci/**` is now a trigger, so the
  documented pre-release observation actually observes something.
- `_ext_root` fingerprinted one install root while the build enumerates three.
- `base_has` returned TRUE for a bare filename, a false positive in a helper whose whole
  purpose is preventing false negatives.
- x4canary: one un-sizeable file discarded every confirmed loss in that repository, and
  a rename with both paths quoted read as DATA LOSS.
- The bare-`return` guard scanned 114 of 121 test files; the seven it missed had neither
  that guard nor the skip ceiling.
- A literal `0x08` byte reached a tracked file, caught by `gates/control_bytes.py`.

### Also — instruments that can now say when they were true

- `compute_load_order` lifted into `_loadorder.py` and added to `ENGINE_SOURCES`: the
  function that decides every collision winner is now WATCHED by the freshness axis.
- `MAX_PROP_DEPTH` is one constant with one definition, and truncation is recorded
  rather than silent.
- `audit-coverage.py` derives the Track 2 population from `git ls-tree` and REFUSES a
  ledger that disagrees; it reads its pinned rev from the ledger header.
- `verify-cold.sh` wired into CI, and the LF guard widened from 178 files. ⚠ The
  count moves as files are added — it was written as 221 and the guard's own
  predicate matches **231** today. Re-derive it rather than quoting this line; a
  number in a release note is a snapshot, and this one rotted inside the release
  that stated it.
- **`scan-identifiers.py --history <range>` — because `git push` publishes COMMITS.**
  Every identifier check here scanned the working TREE, so all of them returned clean on
  exactly the case that matters: an identifier that was committed, noticed, and removed.
  It is gone from the tree and still in the objects. The new mode scans the diff each
  commit INTRODUCES (added lines only — the commit that removes an identifier is the
  fix), reports the SHA and never the line, and REFUSES on an unresolvable or empty
  range, and reads the commit MESSAGE as well as the diff — a message is part of
  the commit a push publishes, and the first version of this mode read only the
  diff. It also uses a WORD-BOUNDARY test rather than a substring one: over history
  a false positive is permanent, and the substring form ran at an 80% false-positive
  rate (4 of 5 hits were a 5-character token inside a 22-25 character snake_case
  identifier).

  MEASURED across THIS RELEASE (`v3.0.0..HEAD`, **111** commits at the time of writing -- and the
  tool prints its own denominator on every run, so read that rather than this): **zero**
  occurrences of a private identifier, messages included. ⚠ An earlier draft of this
  line said "any of the 274 commits", which named a DIFFERENT population — 274 was
  a whole-history count taken mid-arc, while "this release" is the tag range. Over
  the FULL history the same tool reports **2** flagged commits, both predating
  v3.0.0 and therefore already public: one an absolute user path in a commit
  MESSAGE, one an illustration inside `scan-identifiers.py` that its own comment
  now documents. Neither is a new disclosure; both are why the mode exists.
- **`scripts/build-release.sh <ref>`** builds the release bundle from `git archive` —
  the committed object store, so it cannot pick up a working-tree edit — verifies the
  member set against the ref in both directions, and carries a `--selftest` that
  re-proves v3.0.0's published sha256. The v3.0.0 asset had no build script at all.
- six documentation numbers corrected, with the line-counting convention stated so
  they cannot drift again.
- **30 of this arc's own tests were run by NOTHING.** `test_hook_facts.py` mixes
  `unittest.TestCase` classes with plain `test_*` functions, and `unittest` collects
  only the former: it reported **403** tests where pytest reported **433**. Every
  bare-function test added during the guard work was invisible to the runner that
  `scripts/test-hooks.sh` and `verify-hook-tests.py` both drive. Closed with a
  `load_tests` protocol hook, so both runners now report the same number — which is
  itself the check: if they ever disagree again, something is uncollected.
- **`tool_properties`: the cell that proves the store and `x4eff` agree had been
  SKIPPING.** Its manifest path was resolved self-relative only, so on any layout
  where the gate is not beside the artifact it found nothing — and a missing manifest
  with a store present was recorded as a PASS. It now also asks
  `_paths.path_value("X4_TOOLKIT")`, and the missing-manifest case is a finding.

### Also

- `verify-hook-tests` went from 3 of 82 mutations uncaught to **0**; two dead
  locals a fix had orphaned are gone, and two stale mutants re-anchored.
- the two BaseX installs were reconciled during this arc. ⚠ They are NOT
  byte-identical at release: this arc's own later commits (`67d55da`, `a368e03`)
  changed `ask.py` and `staleness.py` and added `test_unknown_is_not_stale.py` in
  the repo only. RE-MEASURED at HEAD: **3 of 16 files differ**. The claim was true
  when written and the arc falsified it three commits later — which is why a
  release note asserting a live-state equality needs re-measuring at the tag, not
  at the commit that earned it. The SHIPPED bundle is unaffected: it comes from
  `git archive`, so it carries the repo's copy.

## v3.0.0 — 2026-09-05

Three things make this a major version rather than a point release: the Bash guard was
rebuilt around a single parse pass, the toolkit gained a **live channel to the running
game**, and it now ships a game extension of its own. The first changes how every command
you run is checked; the second and third change what the toolkit can see.

⚠ **`protect-bash.sh` now requires Python** (see below). Without it the guard **asks**
rather than silently allowing.

### Fixed — a case WORD containing "in" was a TOTAL bypass of every hard block

```python
toks = rest.split()
if "in" in toks:                 # correct: TOKEN membership
    cut = rest.index("in") + 2   # WRONG: SUBSTRING search
```

`case $string in *) rm -rf "<game>";; esac` cut inside the WORD `string`, leaving
`ary in *) rm -rf …`. The case-arm pattern cannot match that (it forbids spaces), so
the segment's verb came out as `ary` and **every verb-keyed rule missed at once** —
including all three hard blocks. The name backstop died with it, because the `rm`
target list was empty.

MEASURED end to end against both hook copies, which were byte-identical at the time:

| command | before | after |
|---|---|---|
| `rm -rf "<game>"` | deny | deny |
| `case $string in *) rm -rf "<game>";; esac` | **ALLOW** | deny |

Three more spellings of the same arm were bypasses, plus a fourth in the use-site
guard: `*)rm` (no space after the paren), the POSIX parenthesised form, and a
double- or single-quoted label. The trailing whitespace was never the safety
property — the no-whitespace character class is, which is why a process
substitution's `rm -rf extensions)` tail still cannot match it.

And `strip_heredocs` truncated any marker that was not a bare identifier, so a
quoted or dotted marker never found its terminator and blanked the rest of the
command — it reached no rule at all.

### Fixed — the fuzzer could not reach the bug, and said so in its own docstring

`scripts/fuzz-guard.py` states one invariant: the dangerous **operand** is
byte-identical in every mutant. There was a **second, unstated** one — every
compound-command mutator was a FIXED string, always a one-character word and a
one-character label. **So the word and the label were unreachable by construction,
exactly as the operand is.**

That is why 996 mutants, 82 mutation probes, 23 predicate checks and 388 unit tests
were all green while the bypass above was live in both hook copies.

11 parameter-varied mutators were added, each checked with `bash -n` first so a
mutant the parser rejects is dropped rather than counted. MEASURED: **1,140 mutants,
80 bypasses** on the first run, **0** after the fixes. Two of the four case-arm
spellings and all three heredoc markers were found by the new mutators and by nothing
else.

**An instrument's stated invariant is its blind spot — and so is the one it does not
state.**

### Fixed — rule precedence was decided by LINE NUMBER, and three hooks failed open

`ask` **exited**, so every `deny` written below it was unreachable and severity was
decided by position in the file rather than by seriousness. Three separate findings
turned out to be this one defect:

* 300 trivial command substitutions turned the game-delete **hard block** into an
  `ask`, because the carrier-truncation refusal sits above it. The blocking fact was
  computed correctly and then discarded by ordering alone.
* `rm "<mods>/t.txt" && git add -A` asked, where `git add -A` on its own denies.
* the save-directory rule was unreachable in the default layout (saves live under the
  profile), so the warning its own comment exists for never reached anyone.

Precedence is now by **value**: deny > ask > advise. `deny` still exits — nothing
outranks it — while `ask` records its reason and lets the remaining rules run.

Re-priced per item over 13,503 real commands with the prediction written first:
**8 changed, all tightening, 0 loosening** — four temp-directory writes, two
`git add -A`, and two deploy commands running a recursive delete inside the game
install that had been silent allows.

### Fixed — a git config option silenced the destructive-git rule

The option scanner skipped the value token only for `-C`, so `-c` was read as a bare
flag and its **value** became the "subcommand":

```
cd "<x4 dir>" && git clean -fdx                          ask
cd "<x4 dir>" && git -c core.fileMode=false clean -fdx   ALLOW
cd "<x4 dir>" && git -c a.b=c reset --hard               ALLOW
```

One config option silenced the only layer that can see a destructive git — in the
rule whose own docstring records that **git ignores the read-only attribute**, so
`x4lock` stops none of these. Every pre-subcommand option that consumes the next
token is now skipped; only `-C` still names the directory acted on.

A second rule was killed by a closing parenthesis anywhere in a path, and the
durable-record write guard used a pattern that could not cross one — dead for exactly
the two files it names, because the game root lives under `Program Files (x86)`.

### Fixed — an owner-nested registry file was FULL-OVERRIDDEN instead of unioned

The additive directories (`libraries/`, `index/`, `t/`) are paths inside the extension
that **owns** a document. Two of the three call sites pass a vpath that still carries
`extensions/<owner>/`, and `extensions/ego_dlc_boron/libraries/rooms.xml` does not
start with `libraries/` — so the union branch was skipped and **the base document was
discarded**.

MEASURED on the live corpus (125 active mods, packed-inclusive):

| vpath | base entries | effective, before |
|---|---|---|
| `ego_dlc_boron/libraries/rooms.xml` | 19 | 2 |
| `ego_dlc_boron/libraries/roomgroups.xml` | 19 | 2 |
| `ego_dlc_split/libraries/rooms.xml` | 18 | 2 |

**99 base registry entries in total were being thrown away** and the effective tree
served the overlay's handful in their place. This is a user-facing data defect, not a
reporting one: anything asking "what rooms exist" got the wrong answer.

Separately, the synthetic `<language>` root for a mod's `t/` diff was created **after**
the overlay loop it exists to serve, by which time the ops had already been dropped —
a well-formed empty answer with nothing saying two overlays' ops had been discarded.

### Fixed — four more defects in the merge engine

* **an unreadable ARCHIVE still killed the run.** `overlay_root` routed only
  `XMLSyntaxError` into the skipped channel, so bad XML inside a good archive was
  recorded while a bad archive escaped — the exact failure that channel was built to
  stop, surviving in the branch nobody reproduced. Both `OSError` and `ValueError` now
  land in `skipped`, and a merely **absent** file is still a silent `None`.
* **a dead guard the comment claimed existed.** A flag was assigned and never read,
  sitting where a guard for "a file already applied bare is not applied again" would
  have gone. Nothing enforced that, and enforcing it would have been **wrong** — the
  bare and nested paths are two distinct files and the engine evaluates both.
  MEASURED: 13 bare+nested pairs across 3 of 125 installed mods, all latent.
* **an identity test on unresolved paths.** The packed-DLC supplement was gated on an
  object comparison against an import-time snapshot, so one directory spelled
  differently read as a different tree and the DLC were silently dropped. Now one
  shared helper comparing resolved paths, used by both callers that had their own copy.
* **a provenance row for an op that did not happen.** A root-targeted `<remove>` was
  correctly reported as not applied and left a removal record anyway.

### Fixed — `x4modlist tracked` called a duplicate row another game's

The "belongs to other games" figure was `total - kept`, which mixes two populations:
`kept` counts **unique ids** while `total` counts **rows**, so a second row for the
same mod in the same game was attributed to a different game. The printed parts now
sum to the denominator.

Underneath it, `fetch_tracked` dropped rows between both of its channels: a row that
is not a dict, or whose domain name is absent, was counted in neither the per-game
tally nor the malformed count, leaving an unexplained remainder with no field to name
it — in the one dataclass that exists to make a narrowing step legible. There is now a
`no_domain` count, and it is announced.

### Fixed — the search guard's coverage was INVERTED with respect to breadth

A search rooted **at** `extensions/` advised, while a search with **no path at all**
(which runs from the working directory, a strict superset) and one rooted **above**
`extensions/` were both silent. MEASURED over 26 transcripts and 397 Grep/Glob calls:
6 no-path and 4 above-extensions, all unguarded, against 15 that fired.

The mod probe also built its archive glob from the **normalised** (lowercased) path
while its own comment claimed it looked at the actual directory — only the root had
been un-normalised. Inert on Windows; on a case-sensitive filesystem the guard was
silently inert for exactly those mods, in the worst direction, since a packed mod then
reads as loose-only. MEASURED on the reference machine: 133 mod folders, 54 ship a
`.cat`, 10 have an uppercase letter, **3 of the 54 (5.6%)** are both.

`search-scope.sh` was also the only hook in `settings.json` with no `timeout`.

### Fixed — instruments that could not report what they were built to find

* **the oracle enforced half its contract.** The half it dropped was the narrowing
  half: an unseen population did not gate, and absent lines were neither counted nor
  reported.
* **nine dead sweep entries were a second gate absorbing coverage loss.**
* **unparseable documents counted as VERBATIM** in the three-way and diff CLIs, while
  the same screen said they were not — and the exit code ignored the unreadable,
  no-base, dropped-by-author and key-collision channels entirely.
* **a zero-coverage baseline stayed green forever** once recorded, with no floor.
* **a nested `ui/lib` Lua file was neither stamped nor announced**, because the
  not-covered announcement globbed the same pattern as the coverage.
* **the lockstep gate read its population from the working tree** its own docstring
  promises it never reads, and compared only the first saved variable per file.
* **a NUL byte removed a file from the control-byte sweep** — and NUL is a member of
  the class that sweep hunts. The existing test pinned the blind spot as intended
  behaviour, so it had to be re-reasoned rather than updated.
* **the reference survey was blind below depth 2** and had lost a case it used to
  catch. Depth was chosen by measurement (0.002 s / 0.018 s / 0.441 s at depths 1/2/3),
  after a first attempt timed a proxy rather than the real function.
* **the account-path scrub was drive-letter-only**, and its selftest could not have
  seen that. Git Bash, cygwin, UNC and home-directory forms are now covered, each with
  a twin.

### Fixed — the safety layer reported a clean sweep over a real hole

`x4lock` dropped a **deleted** protected file from its manifest, so deleting one made
the manifest smaller and `status` printed a clean total — and deletion is the
documented residual risk the read-only attribute does not cover. Its import fallback
produced a 1-of-26 manifest reported as complete and healthy. `x4canary` passed clean
on an unborn repository (so initialising one was a permanent green) and read a staged
rename as data loss.

### Fixed — the mod's reply cap bounded the ANSWER, not the WORK

MEASURED with the repository's own Lua harness: at N=5,000 the probe showed 489 rows
while matching all 5,000 and making **40,492 engine calls (8.10 per object)** in a
single frame — roughly half of it building rows that were then discarded. The reply is
byte-identical after the fix.

Three quieter defects alongside it: an uncapped extension dump (30.0% of the live
`uidata.xml`), a wholly failed enumeration reporting four confident zeros with status
OK, and a `content.xml` claiming twice that it changes no game state while writing
profile userdata twice per load.

### Fixed — installer defects found by reading the diff rather than running it

The global install method was ungated and kept no backup. A PowerShell dry run created
a directory; a rewrite list was built from the destination rather than the source; and
a settings merge was announced as done without being verified. A failed backup became
a **delete**, and the message "Nothing has been changed" was false when it was.

### Changed — a release review is scoped to what the release CHANGES

`docs/REVIEW-SCOPE.md` splits the work in two. **Track 1** is the release review,
scoped to the diff between the previous tag and HEAD — every line this release
actually changes. **Track 2** is a whole-codebase audit that gates nothing, because
its findings already ship in the previous release.

This was written after measuring that reviewers pointed at "the repository" kept
returning findings that **pre-date the release**: of one round's 19 findings, **15
pre-dated it and both CRITICALs ship in v2.8.0 byte-identical**. Those are worth
fixing; they are not release blockers, and treating them as such is how a release
never ships.

### Changed — CI's cold-skip ceilings raised

This release's own tests pushed the skip floor past the ceiling, and the leg named
"Run the suite with NO game installed" — which is the release gate — would have failed
on push.

The floor was re-measured against a real clone with every `X4_*` cleared and the roots
**asserted** to resolve to nothing. The instrument was then validated rather than
trusted: pointed at the exact commit CI last measured, it returns **42** skips, CI's
own figure to the test. All four added skips are accounted for, each +1, buckets
summing to the total; none is a test gone dormant, which is the only thing this ceiling
is for. The Linux figure is labelled INFERRED, because there is no Linux machine here.

### Fixed — a failed or wrong save could destroy the mod registry

`save_registry` opened the destination `"w"` and dumped into it. Two ways to lose the
file, both MEASURED in anger on this machine:

- **A dump that raised left it EMPTY.** `"w"` truncates at open, so an encode error
  mid-write emptied the registry while the traceback read like nothing had happened
  (213 -> 7 bytes, 0 of 2 human decisions surviving).
- **A dump that SUCCEEDED could still be a loss.** A caller that loaded a registry
  which was not there — or was already truncated — and saved the fresh one over the
  real one replaced **258 triaged entries with 46 bytes**. Every layer worked exactly
  as designed; atomicity does not help, because nothing failed.

`save_registry` now serialises to memory first (so a raising dump never reaches the
file), **refuses to write fewer entries than the destination already holds**, and
replaces via a sibling temp plus `os.replace`. The registry is append-only in practice
— `merge_installed` marks `installed: false` and never removes — and three recovered
copies bear that out (243 -> 258 -> 266, a strict superset each time). A caller that
means to shrink it passes `allow_shrink=True`.

The shrink guard has two independent clauses: an entry count (exact on our own output,
and 4,000x cheaper than parsing — 0.1 ms vs 464 ms on a 202 KB registry, which matters
because `refresh` saves once per mod) and a size floor, which cannot be fooled by a
file someone reformatted by hand.

Line endings and encoding on disk are unchanged, so this does not rewrite your file in
git. Verified by replaying both incidents against a copy of a real 266-entry registry:
both refused, the bytes untouched, and a routine round trip unaffected.

### Added — `x4lock`: the irreplaceable files can be made unwritable

`scripts/x4lock.py` marks a small, configured set of files read-only: the game-root
`CLAUDE.md` and `KNOWLEDGEBASE.md`, `.claude/settings.json`, the hooks, skills and
agents, every `x4-paths.env`, and the mod registry. `status` / `lock` /
`unlock <path>` / `unlock --all`.

This exists because a command-string hook cannot see a write that happens INSIDE
another process, and that is where the damage has come from. The read-only bit is the
only layer that can.

MEASURED over 14 write primitives, each paired with a control proving the same
primitive does change the file when unlocked: **11 are blocked on Windows** (9 on
Linux, MEASURED on CI; macOS INFERRED from the same POSIX rule, no CI leg runs it:
`os.remove` and `os.replace` are delete and rename-over, which POSIX authorises by
the directory's permission). Python's `open("w")`,
`write_text`, `write_bytes`, `os.truncate`, `os.replace`, `shutil.copy2` and
`os.remove`; bash `>`, `cp` and `tee`; PowerShell `Set-Content`. Three are not on
Windows (five on POSIX, the two above added), and
they are documented rather than glossed: `bash rm -f` (POSIX `unlink` is authorised by
the DIRECTORY, not the file) and PowerShell `Copy-Item -Force` / `Remove-Item -Force`
(`-Force` clears the attribute). Those are deletes and force-overwrites, covered a
layer up by the Bash guard.

**There is deliberately no ACL.** A first version also applied
`icacls /deny <user>:(W,D,WDAC,WO)`, which does close the `-Force` gap. It was
withdrawn after it locked this machine out of two of its own files: icacls `W` is
`FILE_GENERIC_WRITE`, which includes `SYNCHRONIZE`, so a "write deny" denies READING
too — and because the deny includes `WDAC` it removes the permission needed to remove
itself, which on a file owned by `BUILTIN\Administrators` (ordinary under Program
Files) cannot be undone without elevation. A protection whose failure mode is "you can
no longer read your own file, and cannot fix it" is worse than the accident it
prevents.

`save_registry` now turns the resulting `PermissionError` into a message naming the
guard and the command that lifts it, instead of a bare `WinError 5`.

### Added — `x4lock` and `x4canary`: the irreplaceable files are protected and watched

Two small tools, both born from data actually being lost here rather than from a risk
assessment. `scripts/x4lock.py` marks a configured set read-only (the game-root
`CLAUDE.md` and `KNOWLEDGEBASE.md`, `.claude/settings.json`, the hooks, skills and
agents, every `x4-paths.env`, the mod registry). `scripts/x4canary.py` refuses when a
tracked irreplaceable file has been emptied, deleted or lost half its bytes, and runs
from a SessionStart hook so it happens without anyone remembering.

MEASURED over 14 write primitives, each paired with a control proving the same
primitive DOES change the file when unlocked: **11 are blocked on Windows** (9 on
POSIX — `os.remove` and `os.replace` are delete and rename-over, which POSIX
authorises by the DIRECTORY's permission, not the file's), including every one of
the four real incidents. The three that are not on Windows — `bash rm -f` and
PowerShell's two `-Force` forms — are documented rather than glossed, and covered a
layer up; on POSIX it is five, the two above included.

**There is deliberately no ACL.** A first version also applied
`icacls /deny <user>:(W,D,WDAC,WO)`, which does close the `-Force` gap. It was withdrawn
after it locked this machine out of two of its own files: icacls `W` is
`FILE_GENERIC_WRITE`, which includes `SYNCHRONIZE`, so a "write deny" denies READING —
and because the deny includes `WDAC` it removes the permission needed to remove itself,
which on a file owned by `BUILTIN\Administrators` cannot be undone without elevation.

### Fixed — the guard: a glob operand walked past every root HARD BLOCK

`rm -rf "<game>"*` was ALLOW where the identical literal path is DENY, and so were
`[X]4 Foundations`, `X4?Foundations` and `X4 Foundation{s,}` — six bypasses of eleven
probes, every one of which really does delete the installation. The root rules compared
operands with `==` and `under()`, which see a literal path; a sibling PATTERN that
expands to include the root is neither.

`scripts/fuzz-guard.py` could not have found it. Its own docstring says it mutates "the
SYNTAX AROUND the dangerous operation… the dangerous operand is byte-identical in every
mutant" — so the operand is the single axis 996 mutants hold fixed by construction.

Per-item over 13,503 real commands: 8,631 contain a glob character, **0 changed
verdict**, 0 of a 500-command control.

### Fixed — the guard: two rules that refused real work

An escaped `\$?` does not expand, so `git commit -m "fix \$? after a pipe"` is prose —
it was a non-overridable DENY on a commit message describing the trap the rule enforces.
And the wrong-scope search refusal listed the mod source tree among its roots while its
message cites 300 s and "GBs of binary database pages"; MEASURED over that tree, 230 ms
and 1,190 files. The toolkit, game and reference roots keep theirs.

4 of 6,526 affected commands changed, all `deny -> allow`, each read individually.

### Fixed — `git` ignores the read-only lock, so the hook now sees it

MEASURED: `git checkout HEAD~1 -- <locked file>` overwrote the file AND left it
unlocked; `git clean -fdx` deleted a locked one. Two predicates, split by consequence:
`clean -f` and `reset --hard` name no paths and so reach UNTRACKED files with no other
copy → **ask**; `checkout -- <path>` and `restore <path>` can only name tracked content →
**advise**. The split took user-facing prompts from 4 to **0** across the corpus while
keeping the bypass closed.

### Fixed — the mod probe ignored ALL player input for 12 seconds on every game load

`engine_probe.lua` passed `blockinput=true` with a `+12 s` delay to
`Helper.addDelayedOneTimeCallbackOnUpdate`, which calls `C.SetAllUIInputIgnored(true)`
until the callback fires. MEASURED against vanilla: all 17 base-game callers that pass
`true` use `+0.1 s`, covering a transition. Ours was 120x the longest, in an addon whose
`content.xml` states twice that it changes no game state.

### Fixed — a mixed-case additive vpath made every DLC layer REPLACE instead of union

`Index/macros.xml` and `index/macros.xml` took different merge paths, though to the
engine they are one file. Every DLC ships an `<index>` root there, so with the union test
failing each DLC full-overrode the previous one: **4,733 entries became 17**, and a mod
patching the index saw a tree missing 99.7% of the game's content. The `t/` language-file
test had the same flaw, which is a hard failure on Linux rather than a quiet one.

### Fixed — a `sel=` that computes a value crashed, or reported a wrong node count

`sel="count(//ware)"` raised `TypeError: object of type 'float' has no len()` — a raw
traceback at rc 1, the same code as "your mod has errors", with `--json` emitting 0
bytes. Worse and quieter: `sel="string(//ware/@id)"` reported **"sel matched 18 nodes"**,
where 18 is the length of the string.

### Fixed — the freshness content axis stood 27 GB on one file's mtime

`hash_content` folded in `(mtime, size)` of `libraries/wares.xml` alone as the whole
reference tree's contribution — 510,711 files and 27 GB. Now a top-level survey plus the
recorded `.reference-buildid`, measured at 0.001 s against 17.6 s for a full stat walk.

⚠ Known gap, named rather than closed: `_compat.compute_load_order` decides every
collision winner and is not in the engine axis, because `_compat.py` also carries the
`x4compat` CLI and F69 measured that a CLI-text or docstring edit in an engine source
invalidates the store for a rebuild that cannot change one row. The remedy is to lift
that function into a CLI-free module.

### Fixed — tests and gates that reported success over nothing

Three tests bailed with a bare `return`, which pytest counts as a PASS and `X4_MAX_SKIPS`
is structurally blind to — two of them the load-order invariants the whole
collision-winner report rests on, running green in the CI step named "Run the suite with
NO game installed". They now run against a staged reference; a repo-wide AST ban stops
the shape returning. `oracle_index.py` computed a FALSE OK, printed it, and returned
None under a bare `main()`, so the gate could not fail. `run-gates.sh` exited 0 whenever
one gate passed, however many could not run — now exit 3, because *could not look* is
not *nothing wrong*.

### Fixed — the PowerShell installer had one direction trigger where bash has two

`install.ps1` refused an auto-detected destination only under `-Yes`; a non-interactive
run without it auto-detected and wrote. MEASURED: `[Environment]::UserInteractive` is
**True** with stdin from a file, a pipe, and a tool-driven launch, so a guard keyed on it
would never fire — `[Console]::IsInputRedirected` is the real analogue of `[ -t 0 ]`.
The CI installer smoke step also asserted the destination has no `.venv`, which cannot
pass: `install.sh` runs `setup.sh`, which runs `uv sync` there. It now plants a sentinel
in the source venv, so the question becomes whether the SOURCE's venv travelled.

### ⚠ Changed — an exported `X4_*` variable now WINS over `x4-paths.env`

**This changes the outcome for anyone who exports one.** Previously the file won in the
bash half, so a `setx X4_GAME ...` was silently ignored by every hook.

There were three statements of precedence and one disagreed:

| | said |
|---|---|
| `CLAUDE.md` | env var > `x4-paths.env` > default |
| `_paths._layers()` (Python) | `[env, file, fallback]` — env wins |
| `.claude/hooks/_x4-env.sh` | `set -a; . "$cfg"; set +a` — the **file** won |

Two of three agreed; the third is the one every hook runs on every tool call. The
inconsistency was the dangerous half rather than an untidiness: both halves resolve
paths for the same machine, so exporting `X4_GAME` pointed `x4validate` at one install
while the guards protecting the game folder read another — protection and work aimed
at different trees, with nothing saying so.

Bash now matches the documented order. If you were relying on the file to override an
exported variable, unset the variable or remove the entry.

### Fixed — `-DryRun` wrote for real (PowerShell)

`$DryRun` was consulted in exactly ONE place, inside `Show-Target`, which only the
copying arms reach. So `-Method global -DryRun` overwrote `x4-paths.env` and printed
`=== install complete (global) ===`; an in-place `-Method separate -DryRun` did the
same. The gate now lives inside all three writers, as `refuse_if_dry_run` already did
on the bash side, so an arm added later cannot write without passing one.

### Fixed — a destination that IS the source was copied onto itself

`$SRC` is MSYS-style under Git Bash (`/tmp/...`) while every documented path is
Windows-style (`C:/...`), so a string comparison of the two spellings of one directory
said COPY. `cp` reported "are the same file" and `set -e` killed the script before the
FAILED accounting existed — no INCOMPLETE banner, no `failed:` line, no config
written. A trailing separator on `--toolkit` produced the same result. Both installers
now compare canonically.

### Fixed — `-Method global` kept no backup of `x4-paths.env` (PowerShell)

`install.sh` moved this backup into `write_paths_env` so a caller could not be added
without one; `Write-PathsEnv`, which all three methods call, never got it.

### ⚠ Changed — `setup.sh` can now FAIL, and `install.sh` can now notice

**This changes the outcome for existing users.** An install that "succeeded with
warnings" will now stop and tell you what is missing.

`setup.sh` had zero `exit` statements and zero `return` statements, and ran under
`set -uo pipefail` with no `-e` — so its status was the status of its final `echo`.
MEASURED cold, with jq/uv/python all absent from `PATH`: it printed four warnings,
said `=== setup complete ===`, and exited **0**.

That made the caller's accounting dead code. `install.sh:277` reads
`( ... bash setup.sh ) || FAILED="setup.sh"`, which was the ONLY setter of `FAILED`,
so the entire `=== install INCOMPLETE ===` branch could never fire. A cold
`install.sh --method separate` exited 0 having wired up nothing.

Scoped deliberately narrowly, because this is a behaviour change: **hard** for `uv`
missing, `uv sync` failing, x4validate not wired up, or no Python interpreter at all
(without one the Bash guard degrades to ASK on every command, which in a safety
toolkit is a broken install). **Warning** for jq — the hooks carry a documented Python
fallback — and for java and wine, which are optional.

### Fixed — four more installer defects, all reproduced end to end

* **`--method global` exited 1 SILENTLY.** The producer group's status is its LAST
  `for` loop; with no `agents/*.md` match that loop returns 1, `pipefail` promotes it,
  and `set -e` killed the script **after** copying and **before** the jq merge — no
  message, no `settings.json`, and every skill it had just rewritten pointing at an
  undefined `$X4_TOOLKIT`. A per-clause control pinned the cause: removing the
  *skills* instead exits 0.
* **…and the same control exposed a live silent success:** with 0 skills copied it
  still printed `installed x4 skills + agents` and exited 0. `install.ps1` has had a
  zero-count refusal for a while; bash had none. It does now, and the message names
  the count.
* **Both installers destroyed your config on the only upgrade path there is.** They
  deleted `.claude/settings.local.json` and `.claude/x4-paths.env` — the file
  `setup.sh` itself tells you to keep `X4_NEXUS_KEY` in — and `setup.sh` then logged
  the recreation as `[ok] created ... from example`, so the run read as though nothing
  had been touched. MEASURED with planted markers: gone after a second install.

  ⚠ The first attempt at this fix, earlier the same day, was **wrong in two ways**
  and is corrected below: it was applied to `install.sh` only while the entry claimed
  “both”, and its backup ran **after** the copy had already overwritten the file, so
  the `.bak` held the SOURCE machine's config while the run printed “kept your
  existing”. Both are fixed and covered by tests on each installer.
* **`--help` printed live shell source** (`sed -n '2,16p'` over a header ending at
  line 12), and a flag given with no value died with `install.sh: line 44: $2: unbound
  variable` instead of a usage message.

### ⚠ Changed — installing over an existing installation now requires DIRECTION

**This is a behaviour change, and it is deliberate.** Upgrading in place now needs
`--over-existing` (`-OverExisting` on PowerShell). Without it the installer refuses,
names what it found, and tells you what to type.

The rule is the user's, after an installer overwrote seven files in a live game folder:
*a new install must not proceed over an existing one without strict user DIRECTION —
not merely approval.* A prompt can be clicked through; a flag has to be typed.

What happened, MEASURED: `install.sh --method in-game --yes` with no `--game` wrote
**1,642 files** into a real Steam install and exited 0. Every `X4_*` variable had been
cleared first and none of it mattered — `steam_roots()` is hardcoded, so detection found
the game directly, and `--yes` accepted that destination without ever printing it. A
145 KB `CLAUDE.md` and a 631 KB `KNOWLEDGEBASE.md` were among the casualties, recovered
only from a Volume Shadow Copy taken 22 minutes earlier.

Four changes, each with a test on both installers:

* **An auto-detected destination is refused under `--yes`.** Nobody named it and nobody
  is watching. Name it with `--game`/`--toolkit`, or run interactively.
* **An existing installation is refused without `--over-existing`.** In interactive mode
  too — that is the difference between direction and approval.
* **The destination is printed before the first write**, in every mode.
* **`--dry-run`** prints the destination and the item list and writes nothing.

### Fixed — the config backup ran AFTER the copy that destroyed it

`copy_toolkit` took its backup below the copy loop, and `cp -r "$SRC/.claude"` had
already overwritten the destination's config by then. So the `.bak` preserved the
SOURCE machine's file, the user's was gone from every file, and the run printed
`[note] kept your existing x4-paths.env` — a reassurance that fires exactly when the
thing it names has been destroyed.

Reachable whenever the source tree carries its own config, which is what any toolkit
folder someone has run `setup.sh` in looks like. The first fix's own test missed it
because a `git archive` source has no `x4-paths.env` — it is gitignored.

Now: the backup is taken **before** the copy, and the user's file is **restored**
afterwards rather than deleted. `write_paths_env` also **carries over keys it does not
own**, so `X4_NEXUS_KEY` survives in the LIVE file instead of only in a `.bak` nobody
opens — and it **verifies the artifact**, refusing to report success if bash cannot
source what it just wrote. A path ending in a separator used to produce
`X4_TOOLKIT="C:\path\"`, whose quote never closes: every `X4_*` came out unset while
the installer said `install complete`.

**A virtualenv no longer travels.** `uv` hardlinks package files from a shared cache, so
once source and destination have each been synced the same file has the same inode in
both — and a recursive copy aborts with *"are the same file"*, 1,334 times, part way
through, leaving a half-copied destination and (because `set -e` kills the script there)
no backup at all.

### Fixed — `bin/unpack-reference.sh` never checked its own lock

The sentinel `reference/.unpacked-and-locked` was **written** by this script and read
**nowhere in it**. Its own text — *"reference/ is read-only; remove this file manually
to re-unpack"* — was false for the one path that actually unpacks.

The lock did exist, but only in `protect-bash.sh`, which guards commands the assistant
runs through its Bash tool. It cannot see an XRCatTool invocation made by this script
in a child process, and it does not run at all for a user typing
`bash bin/unpack-reference.sh` or `bash install.sh --unpack`. **The protection covered
the assistant's path and not the user's.**

Found the hard way: an `install.sh --unpack` re-unpacked a locked reference tree
without a word. The script now refuses (rc 2) and names the override,
`X4_FORCE_UNPACK=1`.

### Fixed — `--method global` rewrote your config with no backup

The third instance of "installing destroys the user's config" in this release, and the one
that got past the first two fixes because each of those was applied at a **call site**.

`copy_toolkit` backs `x4-paths.env` up and puts it back, which covers `in-game` and
`separate`. `--method global` never calls `copy_toolkit` — it goes straight to
`write_paths_env`, outside that wrapper — so it rewrote the file with no backup at all.

Found by accident, on this repository: a `--method global` smoke run with
`--toolkit <the checkout>` replaced that checkout's own config with sandbox paths and left
no `.bak` beside it. Five cold-clone tests then failed, because the reference path resolved
to a directory that does not exist and the `needs_reference` skip guarding them therefore
did not fire — a nice demonstration that a skip condition is a claim about the
environment, not a constant.

The backup now lives **inside `write_paths_env`**, where every caller gets it and a fourth
call site cannot be added without one — the same reasoning that already put the
carry-over of unowned keys (notably `X4_NEXUS_KEY`) in that function rather than at its
callers. A backup that cannot be written now warns instead of staying silent, because the
message saying it was kept is worse than no message if it was not.

### Fixed — six spellings of an action the guard already refused, and one false positive

None of these needed a new concept. Each is a set or a comparison that had been written
against one example.

* **`find -execdir`, `-ok`, `-okdir` and an absolute delete walked past.** The rule tested
  `t == "-exec"` and compared the following token verbatim — three functions below the
  `_verb_name()` that folds `/bin/rm` and `rm.exe` onto `rm`. `find`'s own documentation
  recommends `-execdir` **over** `-exec`. A shell under `-exec` is now followed through the
  same carrier walk used everywhere else. A genuinely scoped cleanup
  (`find . -name __pycache__ -exec rm -rf {} +`) is untouched — that exemption exists
  because removing it added 40 prompts over 13,282 commands.
* **Moving a protected root away was silent while deleting it was a hard block.** Only the
  *destination* of a `cp`/`mv` was ever inspected. `mv <game> /tmp/x` destroys the install
  as surely as a delete does. A `mv` **source** that is a protected root now counts as a
  delete of it; `cp` is deliberately excluded (it reads and leaves the original in place),
  and a source *inside* a root keeps the existing confirmation, so the deploy path is
  unaffected.
* **Four spellings of staging everything.** `GIT_ADD_ALL` was three exact tokens, so
  `git add ./`, `git add *`, `git stage -A` and — worst — `git add :/` were allowed. `:/`
  stages from the repository root regardless of the current directory, i.e. it is strictly
  broader than the `.` the rule was written for. Explicit paths and `git add -p` still pass.
* **A command carried in a variable reached no rule.** The carrier walk appended the token
  `$C` verbatim, although `assignments()` had already computed `C` from the same command
  and the delete rules were using it. It was the one consumer not resolving.
* **An extended-length Windows path defeated two guards at once.** `\\\\?\\C:\\…` and
  `//?/C:/…` are spellings the Write tool accepts and Python opens; they normalised to a
  path under no configured root, so `protect-files.sh`'s reference hard block never fired
  — **and** `backup-before-edit.sh` could not stat the path, so it exited 0 with no backup
  and no audit line. The edit was both allowed and unrecoverable. The prefix is stripped
  first, *before* the drive-dialect rule, and the ordering is the fix rather than a detail:
  stripping afterwards turns `//?/c:/users` into `//?//c/users`, which still matches nothing.

**And a false positive, which is the costlier direction.** `durable_python_open_w` ANDed
two independent predicates over the **raw** command — the last rule in the file still doing
what the 2026-09-01 rewrite existed to remove. A comment naming a durable record, plus an
unrelated write call anywhere else in the text, was a **non-overridable deny** on writing a
scratch file. It fired on the reviewer twice, on me twice, and then on the very shell
command that applied its own fix.

It now reads `body` (comments and heredoc bodies stripped) and additionally requires the
command to actually run a python interpreter — a sentence *about* the rule is not a write.
The two-predicate AND is **kept** rather than tightened into a single regex demanding the
filename inside the call, because the case this rule exists for usually writes through a
variable, and that regex would miss precisely it.

**Measured per item over 13,503 real historical commands: 144 refusals removed, 16 added,
and both directions were read rather than totalled.** All 144 are the false positive — in
every one the record name or the call appears only in a comment or a heredoc body, and
**zero** were a real python write to a durable record. The 16 additions are real moves of
real files out of protected directories. Unit tests 265 → **294**, mutants 67 → **75**, all
caught; the fuzzer still finds 0 bypasses over 996 mutants with its control at 58.

### Fixed — five of this release's own fixes were guarded by nothing

A review planted mutants over the round's own work and ran the full 1208-test suite
against each. Five reversions — every one of them the exact defect that had just been
fixed — passed the suite unchanged. Each now has a test that goes red, and each test was
verified against the mutant rather than merely written.

* **The byte-domain frame contract had no byte-domain fixtures.** Every test passed a
  `str` to `decode_reply`, which is encoded losslessly, so clause 9's strict-decode branch
  was structurally unreachable from the suite. Both a lenient clause 9 and a `_read_raw`
  that decodes in transit survived. That is the original defect: each replacement
  character adds +2 bytes, so a lenient decode both invents truncations and **cancels real
  ones** — a corrupt frame served as a clean answer. Added a `frame_bytes()` helper and
  fixtures for invalid UTF-8 that passes length and checksum, a multibyte character cut at
  the buffer edge (`TRUNCATED`) and the same cut self-consistently declared
  (`UNDECODABLE`), plus a `_read_raw` type-and-property test that stubs the lazy win32
  seam so it runs on Linux CI too.
* **`verbs.ext`'s cap and denominator had no test, while the identical `verbs.macro` code
  had four.** Both verbs were named in the same comment as the defect — "neither verb had
  a single test at any size" — and only one got tests. The shipped `content.xml`
  description tells users *every* enumeration caps itself and reports what it omitted;
  that claim was enforced for one verb and by nothing for the other.
* **`engine_probe.lua` was exercised by nothing, and the stamper that covers it ran
  nowhere.** The round extended `scripts/stamp-mod-build.py` to see both lua files, and
  that script is invoked by neither CI nor `run-gates.sh` (which discovers `gates/*.py`
  only) — so the fix for "a change to engine_probe.lua shipped undetected" was inert in
  automation. The BUILD test now iterates every stamped file, `--check` runs as a CI step,
  and `KNOWN_KINDS` is pinned to the Lua in **both** directions: a kind the probe gains
  makes `x4live dump` refuse a good dump, and a kind it loses was noticed by nothing.
* **An exit-code contract with no test.** `_livecli` turning `LiveDumpFatal` into rc 3 was
  covered nowhere, so a mutant returning 0 passed. rc 0 means "checked and clean", so a
  probe that ran and died would have been reported as a clean capture. The twin pins that
  a genuinely truncated dump is *also* rc 3 — the exit code cannot separate them, so the
  message has to.
* **A reproduced user-facing bug fixed without a regression test.** `check_completeness`
  passed the macro **index** into the code answering the `<component ref>` question, while
  `check_references` had already been corrected to pass the index-union-corpus oracle. A
  macro defined in an asset file and never registered in `index/macros.xml` is legal and
  common, and this function exists for a mod *under development*, which is exactly where
  one lives.

**One of the new tests was a false alarm on its first run and is worth recording.** The
`KNOWN_KINDS` check reported `EXT` as listed-but-never-emitted. `EXT` *is* emitted — via
`local vals = { "EXT" }` … `row(unpack(vals))`, a table-driven call the direct
`row("KIND"` regex cannot see. A search finding nothing is a lead, not a fact; the second,
differently-shaped search settled it, and the test now asserts it found **both** emission
forms so it cannot quietly see less than it claims.

Suite 1208 → **1228** passed, 14 skipped; basex 69; both shipped lua BUILD stamps current.

### Fixed — the freshness stamp reported FRESH forever over a tree that was not there

The toolkit's headline safety property is that a negative is admissible only with a
denominator **and** a freshness stamp. That stamp was defeated on two of its three axes,
silently, in the one direction that produces a confident wrong answer rather than a
refusal.

`staleness._defaults()` checked that `reference` and `extensions` **resolve**, never that
they **exist** — and `_paths` returns a path for any non-empty setting. The engine axis
gained a real existence check this release, carrying a comment saying it was being applied
"exactly like reference and extensions two lines up". It was not. The comment asserted a
validation that did not exist, which is why nobody looking at that line had reason to
doubt it.

MEASURED, with a control that goes red:

```
two DIFFERENT nonexistent trees   -> fb2018359186a6be, fb2018359186a6be   IDENTICAL
a nonexistent vs an EMPTY dir     -> fb2018359186a6be, fb2018359186a6be   IDENTICAL
a REAL tree (the control)         -> fad17ba6a627f73c                      differs
_defaults() with both missing     -> ACCEPTED, exists=False for each
```

`_fold` writes `<NO-EXTENSIONS-DIR>` for an empty vector, which is exactly the collision:
every broken world folds to one constant, so an index stamped in that state stays fresh
against any other broken world forever — and `ask.py` prints
`NEGATIVE CONFIRMED over N of M documents` on precisely that verdict.

**Reachable without contrivance.** `install.sh` writes `X4_REFERENCE` unconditionally and
then tells you to run `bin/unpack-reference.sh` *to create that directory*. The documented
post-install state points the variable at a tree that does not exist yet. A moved install,
a changed drive letter or a typo reach the same place later.

Both axes now refuse, naming what was expected. The reference probe is
`libraries/wares.xml` — `_fold`'s own marker, the file whose absence silently drops the
reference axis from the digest — so it refuses in exactly the case the fold would have
gone blind, rather than merely when the directory is missing. **An empty `extensions/` is
still accepted**: it means "no mods installed", it fingerprints honestly, and refusing it
would break a new user's install to fix a misconfiguration they do not have. That line has
its own test, and reverting any of the four clauses turns its named test red.

### Fixed — four more ways a command reached the shell unseen

The carrier work below enumerated what *carries* a command. A further review found four
classes it had not reached, and the honest summary is that three of them are shapes a
person types by habit rather than shapes an attacker constructs.

MEASURED end to end through `protect-bash.sh`, each against a delete the guard refuses
when it is written plainly: **14 probes returned a silent ALLOW; 0 do now**, with all
12 must-not-fire controls unchanged.

| carried by | before | after |
|---|---|---|
| a backslash-newline **line continuation** before the operand | allow | caught |
| `echo '<cmd>' \| bash`, `printf '%s' '<cmd>' \| sh` | allow | caught |
| a heredoc **piped** into a shell (`cat <<EOF \| bash`) | allow | caught |
| `bash <<< '<cmd>'`, `bash -s <<< '<cmd>'` | allow | caught |
| `source /dev/stdin <<EOF` | allow | caught |
| `${VAR%x}`, `${VAR:-x}`, `${VAR//a/b}`, `${ARR[0]}` as an operand | allow | caught |
| `coproc { <cmd>; }` | allow | caught |

**The line continuation is the one worth reading twice.** bash removes backslash-newline
before it parses anything; `segments()` treated it as a hard boundary. So a delete
continued onto a second line became two segments — verb `rm` holding a lone backslash,
and the path alone with no verb — and **every verb-keyed rule lost its operand at once,
all three hard blocks included**. It needs no cleverness at all: it is how anyone writes
a long command, and 6 of 12 fuzz seeds reproduced it unaided.

**The expansion case failed in the dangerous direction.** `_VAR` exists to *extract* a
variable's name and matches a braced form only when the brace closes straight after the
name — so `${DST%/}` and friends matched nothing, and `has_unresolved` reported the
operand **resolved**. The conservative branch exists precisely so an operand the hook
cannot resolve still refuses; believing we had resolved it skips that branch, which left
the guard most confident exactly where it knew least. "Is there an expansion here" and
"what is it called" are now separate predicates.

**Two of the four fixes were themselves wrong first, and both failures had one shape:**
a correct check that could not be reached.

* `_reads_stdin_program` refuses on any non-flag token, reading it as a script file — and
  the tokens it tripped over were the here-string operator and its word. The one carrier
  whose payload is plainly visible in the command text was the one the check declined to
  look at. A redirect is not an operand, and is now skipped with the word it consumes.
* The here-string handler was then added as an `elif` in `_inner_commands`. `bash` matches
  the `-c` arm first, that arm appends nothing when there is no `-c`, and **the new arm
  never ran** — every part of it tested green in isolation; only the wiring was wrong.

That is the same defect class this release is largely about, appearing twice more inside
the fix for it. It is recorded rather than tidied away, because "the parts are
individually tested" is exactly the reassurance that let it through.

**Scoped to avoid false denials, which are the costlier failure here.** The pipe pairing
requires the consuming shell to have **no** script operand — the only shape that actually
reads stdin — and the producer to be an `echo` or `printf` of a literal, so
`echo 'note' > n.md && bash script.sh` cannot pair by accident. Heredoc routing still keys
on the **opener's** verb, so `cat > notes.md <<X` stays file payload and a `python - <<PY`
body is never parsed as shell.

### Fixed — the guard modelled shell grammar, then stopped resolving

Adding twelve mutators for the classes above — the fuzzer could not previously *emit* any
of those shapes — immediately found **seven more bypasses**. All seven are
**pre-existing**: measured against the previous commit, it allows every one identically,
so this is reach the fuzzer gained rather than anything the fixes above introduced.

* **`cd` through a variable defeated a hard block.** `cwd_track` handed the RAW token to
  `join_cwd`, so `FZ="<game>"; cd "$FZ" && rm -rf extensions` was a silent allow — the
  most ordinary idiom there is. The damage is not confined to that token: the directory
  it computes is what every later *relative* operand is judged against, so one opaque `cd`
  argument disarms the path rules for the whole rest of the command.
* **`resolve()` gave up at the first operator**, so `${V%QQ}`, `${V:-…}`, `${V//a/b}` and
  `${A[0]}` never resolved even when the command itself assigned the variable.
* **An array lost its quoting before it was read.** `tokens()` strips quotes, so
  `A=("C:/Program Files (x86)/…")` split on the spaces and `${A[0]}` resolved to
  `C:/Program`. That is a *wrong* answer, which is worse than a missing one: an unresolved
  operand takes the conservative path, while a wrongly resolved one is compared against
  the roots and cleared. Arrays are now read from the raw segment.

Where text alone cannot reproduce bash's semantics — a glob in the pattern (`${V%/*}`, the
dirname idiom), a substring offset, an unknown variable with no default — the expansion is
left **unresolved** on purpose, which is the existing conservative path rather than a guess.

**One of these fixes was a loosening and was reverted before it shipped.** Blanking the
directory when a `cd` target stays unresolved reads as the careful choice; measured, it
took `cd <game> && cd "$NOPE" && rm -rf extensions` from deny to allow, because the sticky
join is what keeps the operand under the root we last knew about. It was caught by a
mutant that refused to go red — the only reason it was looked at.

**Cost, measured per item against 13,503 real historical commands: 1 changed verdict,
allow → ask, 0 loosened.** That row is `DEV="<toolkit>"; cd "$DEV" && rm -f …` — a real
delete of real files inside the workspace, previously unattributable. Latency is unchanged:
−5.6% / +4.7% / +0.2% against the previous build, sampled interleaved so machine-load drift
lands on both arms.

**The fuzzer is why any of this was found, so it grew too:** 71 → **83 mutators**, 852 →
996 mutants, and the planted pre-fix control now rediscovers **58** bypasses instead of 36
— the new axes strengthen the control rather than decorating it. Unit tests 217 → **265**,
mutants 57 → **67**, all caught, 0 predicate coverage gaps.

`scripts/verify-hook-tests.py` also gained a refusal of its own: a mutant naming a test
that does not exist reported "NOT CAUGHT by its target test" — the same words as a genuine
coverage hole, and the message you would act on by writing a test that is already there.
Five mutants added that day named test *classes*, which unittest never prints in a `FAIL:`
line.

### Fixed — the guard could not see a command that was CARRIED

One root cause behind several total bypasses: the parse pass models shell **grammar**
well and shell **command resolution** not at all. Two halves — what a verb token
*names*, and which constructs *carry* a command.

MEASURED end to end through `protect-bash.sh`, each against a game-root delete the
guard catches when written bare: **14 of 16 probes returned a silent ALLOW.**

| carried by | before | after |
|---|---|---|
| `/bin/rm`, `rm.exe`, a quoted Windows path, `$'rm'` | allow | caught |
| `timeout 5`, `exec`, `setsid`, `nice -n 5` (and their VALUE arguments) | allow | caught |
| `$( )`, backticks, `<( )` | allow | caught |
| a heredoc body fed to a shell | allow | caught |
| `trap '<cmd>' EXIT` | allow | caught |
| `eval` when it is not token 0 (`time eval …`) | allow | caught |
| a nested `-c`, to depth 4 | allow | caught |
| `find <root> -name '*' -delete` (a filter matching everything) | allow | caught |

**What it cost, measured per item against 13,503 real historical commands: 3 changed
verdict, all TIGHTENED, 0 loosened.** All three are correct — two recursive greps over
the dev tree that `timeout` had hidden, and one over the 60 GB reference tree nested
inside `$( )`. Every must-not-fire control held, including `cat > f <<X` and
`python - <<PY` heredoc bodies, which must NOT be routed through the shell parser.

**The walk is bounded, and it says when it stops.** Following carriers is not free:
a 128 KB command with unique text at every nesting level produced 9,841 command
strings and 4.1 s — on the blocking `PreToolUse` path, in a file whose header records
an 11.3× latency regression. It now caps at 250 carried commands (measured: the
largest of the 13,503 real commands produced **25**) and emits a fact that makes the
guard **ask**, because a command too tangled to analyse in full is not a clean pass.
Latency is unchanged: ~188 ms benign, ~219 ms on a refusal.

`scripts/fuzz-guard.py` gained a second axis for this class — 55 → **71 mutators**,
852 mutants. Proof it is not decoration: run against the PRE-FIX parse pass it finds
**157 bypasses, all 157 from the new axis and 0 from the old one.**

### Fixed — four instruments that could not report what they were built to find

Each is a check that could not go red. They were repaired before anything was measured
with them, and each repair has a falsification twin per clause.

* **`gates/mutation_probe.py` reported EVERY survivor as `killed`.** Three independent
  causes, and the third was invisible until the first two were fixed and it still
  said 45/45 — *which is exactly the shape the broken gate produced*. (1) A
  self-referential anchor test that a mutated tree necessarily fails. (2) `write_text` rewrote
  the LF-pinned target as CRLF (measured: 0 → 790 in `_merge.py`). (3) The probe's OWN
  marker makes five artifact-writing tests refuse — correctly — so the full suite can
  never be green during a probe. The escalation now compares failure **sets** against
  a baseline taken with the marker present. Proven by planting an inert comment edit
  and requiring `LIVED`. **It now finds 3 real survivors**, each guarding a documented
  trap; all three are now killed by new tests.
* **`scripts/verify-hook-tests.py` returned wrong verdicts** — stale `__pycache__`.
  CPython invalidates on (source mtime in **seconds**, size), and successive mutants
  land in the same second, so when two leave the file the same size the second run
  imports the first one's bytecode. Measured: 2 of 54 reported NOT CAUGHT while both,
  by hand, turned their target test red — and the tests named belonged to the previous
  mutant. The silent direction is worse and equally reachable.
* **`gates/control_bytes.py` scanned 171 of 241 tracked files.** `git ls-files` ran in
  the package directory, so the sweep that hunts collapsed-escape control bytes could
  not see `.claude/hooks/` or `scripts/` — the very code where content gets written
  through interpreter strings. It was blind to the file carrying a literal `0x08`.
  Now 240 of 241, with the one binary named rather than silently skipped, and the
  extension allowlist replaced by a content sniff. It had zero tests; it has 11.
* **`gates/obtainability_audit.py` recorded a denominator it never compared or
  printed.** A coverage collapse could only ever have surfaced disguised as a change
  in the findings.

### Fixed — six toolkit defects, and one that made a guarantee backwards

* **`_livepipe` decoded the frame before validating it.** `_read_raw` used
  `errors="replace"`, so the length and checksum clauses measured a **repaired**
  string. U+FFFD is 3 bytes, so each bad byte added +2 — inventing truncations *and
  cancelling real ones*: a genuinely truncated frame could pass the length check and
  be reported as corruption instead. Now measured in the byte domain and decoded
  strictly at the end, so an undecodable payload gets its own third diagnosis.
* **`_livedump.accounts_for_every_row()` was a tautology** — its Counter was built
  from the rows it compared against. 20,000 randomised dumps: never False, never
  raised. `_livecli`'s error branch and its `return 3` were unreachable, and three
  tests used it as a control that "must PASS". It now asks whether every row's KIND is
  one the reader models, which can genuinely be answered no.
* **A `FATAL` frame was reported as a truncated one.** `engine_probe` writes its build
  error into the dump; nothing read it, so the single-row frame tripped the
  last-row-is-not-END clause and the user was told *"the dump is TRUNCATED and the true
  length is unknown"* — a guess about the transport, printed over the probe's own
  account of the failure.
* **`x4debug baseline` crashed on the degraded metadata it had just written.**
  `archive()` sets five keys atomically inside a try, so any raise leaves none of them
  and records `degraded` instead — and the consumer read all five unconditionally.
  Reproduced through the real CLI: `KeyError: 'total_errors'`.
* **`check_completeness` asked a definition question with the wrong oracle** — the
  macro index alone, where `check_references` uses the union and records that the
  index-only form once cost 23 false gating errors. A macro defined in an asset file
  and never registered is legal and common, and that is exactly what a mod under
  development has. 0 of 1,614 shipped component refs hit it; a synthetic mod hit it
  immediately, with the two checks contradicting each other about the same attribute.
* **`x4v_announce` had 0 of 4 mutants killed** — announcing the wrong tree, inverting
  its own explanation, dropping the git revision, and being deleted entirely all left
  the suite green. It states the provenance of every derived artifact.
* **`staleness.py` fingerprinted a nonexistent engine tree to a CONSTANT.** Every
  missing path folds to the same digest, so the artifact reads FRESH against any of
  them, forever. Two of the three axes already refused for exactly this reason.

### Fixed — the game-side mod capped nothing, and said the wrong thing when it failed

`verbs.ext` and `verbs.macro` concatenated every field with no budget: **40,025 and
40,017 bytes** against a 32,000-byte cap. An over-long message does not truncate — it
tears the pipe down. That contradicted three shipped claims, including the user-facing
`content.xml` description. Both now cap and report `__shown` / `__total`, and
`reply()` enforces the bound at the one choke point every verb passes through, so a
future verb cannot reintroduce it.

`verbs.macro` also reported a **failed engine call as `ABSENT`** — "we could not ask"
in the grammar of "the engine has nothing here" — discarding the error message. The
harvest still balanced, because `_livecli` counts ABSENT as a real answer, and such
answers feed groundtruth fixtures.

### ⚠ `x4live` is EXPERIMENTAL — use a throwaway save

Flagged in the README, in the mod's in-game description, and in the assistant-facing
`CLAUDE.md`, so it reaches you whichever way you meet it.

Accurate rather than alarming, because an overstated warning gets ignored. MEASURED
across both shipped lua files: the query channel makes **zero** state-changing engine
calls, there is no write verb of any kind, and the mod declares `save="false"` so it
cannot bake into a save. The real reasons are narrower: it loads into a **running**
game, `engine_probe` runs automatically at load and calls `SaveUIUserData()` (your
profile's UI userdata, not your save), and **its answers have been wrong** — see the
`ABSENT` defect above. Treat what it says as evidence, not truth.

### Changed — CI now runs the installers, and the tests nothing collected

No step ran `install.sh`, `install.ps1` or `setup.sh` on any OS, which is why every
installer defect above was structurally invisible to it. There is now a smoke run on
both legs that asserts the **artifacts** — `x4-paths.env`, `tools/x4validate`, `mods/`
— and re-runs the installer over the top to prove a planted config marker survives.

(An earlier draft of this entry claimed `tools/basex/`'s 64 tests "had never run in
CI". That was wrong, and checking it is what found the mistake:
`tests/test_basex_tests_are_not_orphaned.py` already runs them in a subprocess from
inside the main suite — deliberately, because they are cwd-sensitive — so they have
been running all along. The gap it closes was real and was closed in v2.x; there was
nothing left here to fix.)

`X4_MAX_SKIPS` went 40 → **45**. The 40 had never been exercised — it does not exist
in the `ci.yml` on `origin/master` — and a true cold clone skips **43**, so its first
run would have failed the gating leg.

### Fixed — six parser defects that let commands past the guard entirely

All six were in the single parse pass and **none was
found by the test suite** — 151 unit tests, 35 planted mutations, 19 predicate probes and
a 13,500-command replay were green throughout, and the coverage report read *"0 of 19
predicates uncovered."* It was telling the truth: every one of those instruments starts
from a command the parser has already identified, so all of them test the RULES and none
tests the parser underneath them. They were found by an adversarial code review, by
`bash -n`, and by a fuzzer — three external oracles, no suite.

The first three are below. The last three were found after the fuzzer was extended to walk
shell syntax CLASSES rather than shapes that had already caused a bug:

**1. One apostrophe in prose disabled every rule.** Every helper is quote-aware, so an
unbalanced quote turned the rest of a command into "quoted" text that no rule could see —
and a rule that sees nothing returns false, which is indistinguishable from *this is fine*.
Measured against the last release where these rules ran, which refuses **both** members of
every pair:

| command | before | with `doesn't` in a comment |
|---|---|---|
| delete the game install (hard block) | deny | **allow** |
| delete `reference/` (hard block) | deny | **allow** |
| delete a savegame | ask | **allow** |
| recursive search at `reference/` | deny | **allow** |
| `git add -A` | deny | **allow** |

**2. A quoted Windows path walked straight past the hard block.** Inside double quotes
bash keeps a backslash literal except before `` $ ` " \ `` and newline; the parser
unescaped unconditionally, so `C:\Program Files\...` lost its separators and matched no
root. `rm -rf "C:\…\X4 Foundations"` fired **nothing**. That is the most natural way a
Windows user writes a path, and the README tells you that you may write roots that way.

**3. The first fix for (1) was itself wrong in both directions** — 13 false positives and
6 false negatives over 13,203 real commands, because command substitution inside double
quotes resets the quoting context and a flat scanner cannot model that nesting.

**So `bash -n` is now the judge of whether a command parses.** It executes nothing, costs
~14 ms against a ~190 ms budget, and needs no dependency the hook lacks — the hook *is*
bash. Three defects from hand-rolling shell tokenisation was enough.

Also fixed, same root: heredoc **bodies** were parsed as commands (a body line reading like
a delete produced a non-overridable deny on a command that only writes a file), and five
rules matched raw command text rather than parsed operands — which is both a
false-positive engine and a bypass, since `( git add -A )` and an env-assignment prefix
slipped past the old anchor.

**Cost, measured per rule over 13,285 distinct historical commands** — aggregates hide
exactly this, so the deltas are per rule and not a total:

| | before | after |
|---|---|---|
| commands **denied** (work blocked) | 844 | **780** |
| commands **asked** (you are prompted) | 152 | 184 |

Sixty-four fewer denials, all verified false positives: the new rules require the
*operand* to be under a root, so a drop means the old rule matched text rather than an
operand. ⚠ **Seven facts did stop firing**, and every one was checked by hand: all seven
are heredoc bodies — a git commit message, a table row, a script being authored — six keep
their verdict through another rule and one correctly relaxes. That number is recorded here
because the commit that caused it did not report it.

The three bypasses below moved those totals again, measured the same way: **+13 commands,
every one stricter, none looser** — 10 now carry an advisory and 3 now ask. Denials are
unchanged at 780. The three new prompts are a `rmdir` inside the game folder and two loops
copying into the game install's own `.claude/hooks`; all three used to pass in silence.

A guard that reads commands has to parse them, and a parser is where this release kept
finding holes. Three more, all of them silent — the hook returned nothing, which means
*allow* — and all three closed here:

| what walked past the guard | why |
|---|---|
| `if true; then rm -rf "<game>"; fi` — also `for`, `until`, `else`, `elif`, `case`, `!`, and a function body | the command is split on `;`, so the segment began with the reserved word `then`, and the guard read **that** as the command being run |
| `cat <<< x` · `# a << b` · `n=$((1 << 2))` before anything | each was mistaken for the start of a here-document, so the rest of the command was treated as file content, not as commands |
| `bash -lc '…'` and `eval '…'` | the unwrapper looked for the exact flag `-c`, so a login shell (`-lc`) slipped through; `sh -c` was caught the whole time |

The first is the serious one: **90 bypasses over 10 compound forms**, and it defeated the
three hard blocks — the game directory, `extensions/` wholesale, and the reference tree.

**None of this was found by testing.** 151 unit tests, 35 mutants, 19 predicate probes and
a replay of 13,500 real commands were green throughout, and the coverage report said *"0 of
19 predicates uncovered."* It was telling the truth: every one of those instruments starts
from a command the parser has already identified, so all of them tested the rules and none
tested the parser underneath. What found it was `scripts/fuzz-guard.py` extended to walk
**shell syntax classes** — here-strings, arithmetic, comments, parameter expansion, process
substitution, redirects, brace expansion, line continuations, compound commands, wrappers —
rather than re-testing shapes that had already caused a bug. It now carries **71 mutators / 852 mutants**, after a second axis was added on 2026-09-02 for the constructs that CARRY a command rather than sit beside one.

**What it cost in practice, measured against 13,503 real commands:** 13 changed verdict,
all of them *stricter*, none looser. Three were real: a `rmdir` inside the game folder and
two loops copying files into the game install's own `.claude/hooks`, all previously waved
through without a word.

⚠ **One fix was briefly worse than the bug it fixed.** The first version of the `case`-arm
rule also matched the tail of a process substitution, so
`diff <(cd "$GAME" && rm -rf extensions) <(echo b)` lost its delete — a command the old
code had handled correctly. No test caught that; comparing old and new behaviour
command-by-command over real history did.

### Also in this release

- **The global install did nothing, silently, if your toolkit path contained `[` or `]`.**
  PowerShell reads those as a wildcard character class, so `Get-ChildItem` matched nothing,
  `-ErrorAction SilentlyContinue` hid it, and the installer printed *"installed x4 skills +
  agents"* over an empty directory. Every path read now uses `-LiteralPath`, and the
  installer **fails loudly** if it copied nothing. Verified by running it end to end from a
  folder named `toolkit [v3]`.
- **`bash` on Windows meant the WSL stub.** `scripts/fuzz-guard.py` carried
  `which("bash.exe") or which("bash")` with a comment saying it avoided that stub — but the
  stub *is* named `bash.exe`, so from PowerShell both calls returned
  `C:\Windows\System32\bash.exe`. The guard fuzzer would have run every mutant under Linux,
  where the Windows paths its cases are built from do not exist. One resolver,
  `scripts/gitbash.py`, now serves every caller and returns nothing rather than a stub.
- **`scripts/test-hooks.sh` failed on a fresh checkout** — it snapshotted the directory
  *before* creating its own sandbox, then reported that sandbox as litter. It passed on
  every second and later run, and would have gone red the first time CI ever ran it.
- **An error is no longer reported as a finding.** The identifier scan returns 2 when it
  *cannot* run (no `.git`, e.g. inside a release tarball); the suite read that as "an
  identifier was found". Skips are now counted and named in the result line.
- **The mod's build stamp was stale in the shipped tree, and nothing could see it.**
  `stamp-mod-build.py` looked only in `dev/`, which does not exist in the public repo, so
  `--check` printed "nothing to stamp" and exited 0 — a gate passing by never running. It
  searches `mods/` first, refuses (exit 2) when a root exists but holds no mod, and a test
  now pins it to the same lookup the test suite uses.
- **Two shipped skills were undocumented.** `/x4-balance` and `/x4-probe` existed but were
  not in the README, so there was no way to know they were there. A test now derives the
  list from the directory.
- **`~` and `$HOME` were never resolved, so save deletes written the ordinary way were
  invisible.** `rm -f "C:/Users/.../save/s.xml.gz"` asked for confirmation;
  `rm -f ~/Documents/.../save/s.xml.gz` did not. Saves are the one thing here with no
  backup and no undo. Every other way of writing a path was already handled — absolute,
  relative after a `cd`, the Git-Bash `/c/...` form, backslashes — so this adds an
  expansion, not a new guess: only a leading `~`, `$HOME` or `$USERPROFILE` is touched.
  Priced over 13,503 real commands: one verdict changed, and it *relaxed* — read in full,
  its only delete targets a path outside every X4 folder, so the old prompt was a false
  positive.
- **The guard fuzzer was blind to a quarter of the rules it was meant to test.** It kept
  its own copy of "which rule means deny, which means ask", and that copy had drifted:
  the guard has 19 rules, the fuzzer knew 14. A rule it doesn't know about looks like
  "nothing to test here", so two of its twelve test cases were being dropped from every
  run in silence. It now reads the list from the guard itself and refuses to run if that
  read comes back short — an empty list would let it report success over testing nothing.
- **Skipped tests are no longer invisible.** A test suite that says "1159 passed, 14
  skipped" never says *which* 14, and that is how 125 tests sat dormant for weeks. Every
  skip is now named with its reason, the count gets its own line, and CI fails if it
  jumps.


### Added — `scripts/fuzz-guard.py`, a differential fuzzer for the guard

Takes a command the guard refuses, mutates only the **surrounding syntax** (comments,
heredocs, substitution, escapes, subshells, prefixes) leaving the dangerous operand
byte-identical, and requires the verdict not to weaken. It found two real bypasses on its
first run.

Every run first plants the pre-fix scanner and **requires the known bypass to be
rediscovered** before it will report anything: a fuzzer that cannot find a known hole
cannot be trusted to report the absence of one. With no X4 installed it uses synthetic
roots rather than skipping — nothing in it touches the filesystem, and a skip in CI is
indistinguishable from a pass.

### Added — `x4live`: ask the RUNNING game, and a game extension to make that possible

Every other tool here reads files. `x4live` reads the live engine — what objects exist right now,
what a value actually resolved to, whether an extension really loaded — without a quit-and-relaunch
for each question.

**This release ships a game extension for the first time**: `mods/x4_toolkit_helper/`. Copy it into
`{game}/extensions/`. It is read-only — a fixed, enumerated vocabulary with **no write verb, gated
or otherwise** — declares `save="false"` so it cannot bake into a save, and adds no content, no menu
and no MD script. Its third-party dependency (**Mod Support APIs**, `ws_2042901274`) is declared
**optional**: without it the addon still loads and the snapshot half keeps working, while only the
named-pipe verbs report the api unavailable. The two halves fail independently, deliberately.

⚠ **Enumeration is not a census, and the tool says so rather than rounding it away.** Ownerless
objects belong to no faction and are invisible to any owner query, hidden factions are opt-in, and
name and sector are *player knowledge* — so many rows come back `Unknown` while ids, positions,
class and flags stay exact. Object ids do not survive a UI reload, which the engine performs on a
save load and on an alt-enter graphics change; the channel reports its load time so a refusal can
say whether a reload is the likely cause.

### Changed — `protect-files.sh` is ~2x faster on every Edit/Write

It cost ~2.0 s per file edit against a floor of ~170 ms — roughly 41 subprocess spawns, because
`x4_under` canonicalised **both** of its arguments on all 11 call sites while argument one was the
same path every time. `x4_norm` now uses one subprocess instead of two, and `x4_canon` is memoised.
Median 3,687 → 1,833 ms interleaved (**2.01x** overall, **2.23x** on the rules); the hook suite
itself 241 s → 188 s.

Behaviour is unchanged and that was proven rather than assumed: 43 of 43 path shapes agree with the
old implementation, 18 paths plus `Write`/`MultiEdit` produce identical verdicts, and the
differential harness was shown able to detect a deliberately-wrong implementation.

### Added — `gates/control_bytes.py`

Finds collapsed-escape control bytes (0x07/08/0B/0C/1B) in text files — the defect where content
written through an inline interpreter string turns `\b` or `\a` into an invisible byte. Three files
in two days here, one of them *inside the sentence documenting an escaping bug*, and none visible in
any normal view of the file. Exits 2 on a zero-file sweep, because a sweep over nothing is not a
clean sweep.


### Changed — ⚠ `protect-bash.sh` now requires Python, and is 12× faster for it

**If you install this toolkit, the Bash guard now needs a Python interpreter on PATH** (or
`X4_PYTHON` pointing at one). Python was already a prerequisite for the tools; it is now one for the
hooks too. Without it the hook **asks** rather than silently allowing.

The guard rules used to hand-roll quote-aware shell parsing in bash, once per rule. MEASURED on a
clean machine, PreToolUse being on the critical path of every Bash call:

| payload | before the rules were re-scoped | after | with the parse pass |
|---|---|---|---|
| `echo hi` | 978 ms | 3,379 ms | **~380 ms** |
| a 201-char command | 1,205 ms | 13,585 ms (max 18,713) | **~700 ms** |

Attributed by profiling each helper: one helper cost **236 ms for a single token** and ran per-token
inside per-segment loops, while two others were re-invoked five and four times, each re-tokenising
from scratch.

`.claude/hooks/hook_facts.py` now does ONE parse pass and answers every rule's question;
`protect-bash.sh` keeps the policy and the prose. Every verdict and every explanation is unchanged —
19 verdict calls before, 19 after, no rule lost and no verdict kind changed.

### Fixed — six guard gaps that all lived in that duplicated parsing

Each was demonstrated against the old hook, not inferred:

- **`rg` and `ag` recurse by default.** The reference-tree and workspace-root rules gated on a
  recursive *flag*, so `rg <pattern> <reference>` — the exact full-tree search the rule exists to
  stop — was allowed.
- **Wrapper verbs hid the command.** `time grep -rn …`, `echo x | sudo tee <path>`, `nice cp …` and
  `env mv …` all escaped, because the rules required the verb to be the segment's first word.
- **`mv -t <dir> a b`** wrote to a destination no rule could see (it took the last operand).
- **`>|`** (the noclobber override) never matched the redirect pattern, so it truncated unwatched.
- **`grep -r <root> -e <pattern>`** consumed the root as "the pattern" and the search rules missed it.
- **A heredoc marker inside a quoted string** opened a body-skip region, hiding every following
  command from three refusal rules.

Also fixed: `.`/`..` path segments are now canonicalised (`<root>/./X4 Foundations` compared unequal
to the root), the game-delete hard block regained its name backstop — which is the only protection an
installation with no configured paths has — and `bash -c "<command>"` is now parsed rather than
treated as opaque.

### Fixed — an operand is now classified WHERE IT RUNS, not as written

The parse pass traded whole-string grepping for structured operands, and in doing so went blind to
every *indirect* way of naming a path. `cwd_of()` already existed and only the search rules consumed
it, so a path named relative to a `cd` reached no other rule at all.

Measured against `c400a05` (the last state where these rules ran — not `v2.9.0`, whose hook predates
the F79 fix and allows everything, which would have made "no regressions" unfalsifiable):

| command | before | now |
|---|---|---|
| `cd <saves> && rm -f *.xml.gz` | allow | **ask** |
| `cd <game> && rm -rf extensions` | allow | **deny** |
| `cd <game> && rm -rf .` | allow | **deny** |
| `cd <reference> && rm -rf assets` | allow | **deny** |
| `pushd` / subshell `(cd …)` variants | allow | **deny** |
| `cd <game>/extensions && rm -rf <mod>` | allow | **ask** |
| `rm -rf "$(echo <game>)"` | allow | **ask** |
| `rm -rf "$X4_GAME"` | allow | **ask** |

Three of those were verdicts the old hook produced and the rewrite had lost; the rest were gaps in
both. The savegame one mattered most: nothing backs saves up, and the guard had stopped mentioning
them.

The fix is one change, not eight — every operand (`rm`, `cp`/`mv`/`tee`, redirects) is resolved
against the directory in force for its own segment, tracking `cd`, `pushd` and `popd`. Two supporting
repairs: `$(…)` and backticks now count as *unresolvable* (they read as literal paths before, so a
delete through one matched no root), and a root named only by its environment variable —
`rm -rf "$X4_GAME"`, whose text never contains the path — is recognised by the variable name.

**Unresolvable operands are treated conservatively for DELETES only.** A delete is the one channel
with nothing behind it. Writes keep their existing verdicts, so this cannot add prompts to routine
work — and that was measured, not assumed, by replaying **13,041 distinct historical commands**:

| | before | after |
|---|---|---|
| commands **denied** (work blocked) | 836 | **836** — no change |
| commands **asked** (you are prompted) | 152 | 187 |

**+35 prompts across 13,041 commands (1.16% → 1.43%), 0 additional work blocked, and 0 facts that
went from true to false** — no protection was traded away for this. Every one of the 43 commands that
gained an ask-level fact is accounted for: 8 were already being denied, 35 newly move allow→ask.

### Fixed — `install.ps1` did not parse at all on Windows PowerShell 5.1

The file held nine UTF-8 em-dashes and **no BOM**. Windows PowerShell 5.1 — the default shell on
Windows 10/11, and the one this README tells you to run — reads a BOM-less `.ps1` as the ANSI
codepage, so each `—` became three mojibake characters. One sat inside an interpolated string and the
parser derailed: **3 parse errors, dead before its first statement.** `pwsh` 7 parsed it fine, which
is how it survived this long.

Two further installer defects were found behind it, each masked by the one before:

- **Neither installer copied `mods/`**, so the README's "copy that folder into `{game}/extensions/`"
  pointed at a directory the installer never created. (`.gitignore`'s bare `content.xml` rule also
  excluded the mod's own manifest, so `git archive` shipped 3 of its 4 files — an X4 extension
  without a manifest does not load, silently.)
- **`Get-Command bash` resolved to the WSL stub** (`C:\Windows\System32\bash.exe`), present on every
  machine with WSL enabled — including every Docker Desktop install. `setup.sh` died with
  `execvpe(/bin/bash) failed` and the install reported INCOMPLETE. On a machine *with* a real distro
  it would have been worse: setup would have run inside Linux, where `C:\…` paths do not resolve at
  all — a silent wrong install rather than a loud failure. The installer now prefers a real Git Bash
  and refuses the known stubs by path.

All three were found by *executing* the installer rather than reading it — `separate` and `in-game`
now complete with exit 0 under both PowerShell 5.1 and pwsh 7, with all four mod files present.

### Fixed — `protect-files.sh` failed open when `jq` was broken

It emitted every verdict through `jq` with no fallback, and empty stdout from a hook means **allow**.
MEASURED with a live baseline: with `JQ=no_such_binary` a blocked edit to `reference/` produced **0
bytes — a silent allow**; it now emits the deny through a Python fallback. It also could not tell
"this payload has no file path" from "jq failed to read it", and treated both as an allow; the second
now asks. `protect-bash.sh` gained this in `bccffc1`; this file never did.

### Fixed — the game-delete block is scoped to what is actually catastrophic

The hard block covered anything *under* the game folder. That was survivable while the guard could
not resolve `$VAR`; once the parse pass could, it began hard-denying `rm -rf "$DST"` where `DST`
resolved to `extensions/<one mod>` — the documented deploy path, which `deploy.py` performs itself.
MEASURED over a 1,000-command replay of real history: **all 4 hits of the rule were exactly that.**

**A capability improvement widened a guard nobody re-scoped for it**, and no probe caught it because
no probe used a variable-resolved deploy path.

The hard block is now the install **root**, or `extensions/` **wholesale** (which destroys every
deployed mod). Anything inside the tree falls through to the confirmation that was always meant for
it. The name backstop for unconfigured installs is anchored the same way — a path merely *containing*
the game's name is not the install. Across all 12,482 distinct historical commands: **0 hard blocks,
48 confirmations.**

(The archive exclusion that used to sit on that backstop is gone: anchoring the name test at the end
of the path and requiring an archive extension at the end of the path are mutually exclusive, so it
had become unreachable — which is what the mutation gate reports as a term no mutation can kill.)

### Fixed — two ways the guard could go silently dead on a whole platform

Both were introduced by the rewrite above and caught by its own checks, not by reading:

- **MSYS translates a POSIX path in an environment variable** when handing it to a *native* Windows
  process. Bash exported `X4_DOCUMENTS=/tmp/x/docs`; Python received
  `C:/Users/…/AppData/Local/Temp/x/docs`, while the command text it was compared against still said
  `/tmp/x/docs`. They can never match, so the Documents confirm, the save-game confirm, the
  `reference/` hard block, the mod-source delete and the deploy advisory **all stopped firing**. This
  is not test-only: the README tells you that you may write roots as `C:\…` *or* `/c/…`. The
  configured roots now travel on **stdin**, which is a byte stream and is not translated.
  (Independently, `_x4-env.sh` only *exports* what came from `x4-paths.env`, so `X4_DOCUMENTS`,
  `X4_SAVES` and `X4_REFERENCE` were never exported at all — two separate causes of the same silence,
  in one line of code.)
- **`declare -A` is a bash 4 feature and macOS ships bash 3.2 as `/bin/bash`.** There it does not
  abort the script: the declare fails, the next assignment becomes an *arithmetic* index into an
  ordinary array, every rule collapses into one slot, and the hook **allows everything while looking
  healthy**. Replaced with a newline-delimited membership test that works on bash 3.2, plus a static
  check in the suite — this machine cannot run bash 3.2, so a static check is the only reachable one,
  and it is verified to fire against a planted feature.

### Fixed — a broken `jq` turned every refusal into an allow

`protect-bash.sh` no longer parses with jq, only emits with it. That made a missing or broken jq far
more dangerous than before: a `deny` became empty stdout, which is read as **allow**. There is now a
Python fallback emitter, so a broken jq still produces the correct verdict.

### Added — every guard rule is now probed in both directions, and proven able to fail

`.claude/hooks/test_hook_facts.py` — unit tests over the parse pass, run in well under a second with no
dependencies (`python .claude/hooks/test_hook_facts.py`).

Coverage is *verified*, not asserted: pinning each predicate false must break a must-fire test, and
pinning it true must break a must-not-fire test. **All 19 rule predicates pass both**, where a review
had found several rules with no probe at all (`sed -i`, XRCatTool re-unpack, the durable-record
rules, the timeout cap, the workspace-root search). Every planted mutation is caught by its
specific target test — the first run caught **three tests that were passing for the wrong reason**,
including one shadowed by a guard clause in front of the thing it meant to test.

### Fixed — ⚠ every hook was INERT: they read stdin in a way that returns nothing

**If you have been relying on these hooks, they have not been protecting you.** All five read their
JSON payload with `INPUT=$(cat /dev/stdin)`. In the Claude Code hook environment that returns **zero
bytes**; a bare `cat` returns the payload. Measured across seven consecutive probes: 0 bytes versus
641–2,840 bytes, on both PreToolUse and PostToolUse.

The failure is invisible by design. A hook that reads nothing falls through its first guard clause
and exits 0 — which is byte-identical to deciding the command is fine. No error, no log line, no
behavioural difference from a hook that looked and approved.

So `protect-bash.sh`, `protect-files.sh`, `backup-before-edit.sh`, `search-scope.sh` and
`x4validate-on-edit.sh` were all doing nothing.

**The test suite was green throughout**, because it pipes stdin explicitly and `/dev/stdin` resolves
fine that way. Independent confirmation on the reference machine: no `AUDIT_LOG.txt` existed at all,
and the only one found anywhere contained 17 entries — every one of them the suite's own synthetic
`/tmp` fixture. Not a single real edit in five weeks.

Fixed with one shared `x4_hook_input()` in `_x4-env.sh`. The suite gained probes that **close** stdin
rather than piping it — reproducing the production condition a pipe never can — plus a static rule
that fails if any hook reads `/dev/stdin` again.

### Added — a hook that receives no input now says so instead of falling silent

The read method was only the proximate cause. The real defect: **a hook could not distinguish "I
received no input" from "the input said this is fine."** Silence was consent, which is exactly how
this hid for weeks. `x4_require_input` closes that — a guard that cannot see its input now says so.

### Fixed — an ADVISORY suppressed every rule below it, hiding 298 real refusals

An advisory is an *allow that carries a note*, but it ended by exiting, exactly like a refusal or a
confirmation — and it sat eighth of nineteen rules. Every rule below it was unreachable for any
command that earned one.

MEASURED before changing anything, by disabling the six advisory/confirmation rules and replaying the
1,846 historical commands they catch: 1,548 are plain allows, and **298 are refused by a rule further
down** — 130 timeout-above-the-cap, 64 shared-`/tmp`, 35 truncating-open on a durable record, 27
exit-status-after-a-pipeline, 26 profile-manifest-searched-by-name, 11 stage-everything. So the noisy
rules were not only noisy: they were **suppressing correct refusals**, the largest being the guard
against a timeout the harness silently clamps.

The file guard had it worse. Its manifest advisory sits above the profile confirmation, the
deployed-copy rule and the game-install block, so editing a **deployed mod's `content.xml` was advised
and never confirmed** — and the deployed-copy rule's three branches were unreachable for a manifest.

Advisories now accumulate and are emitted once, on every exit path (`trap`), while refusals and
confirmations stay terminal. Two of the new probe EXPECTATIONS turned out to be wrong
rather than the code, and both had only ever passed because the advisory masked them.

### Fixed — the same silence one layer in: a failed jq parse, and a refusal that ran through jq

Found by a code-review probe on 2026-08-30 and reproduced before fixing. `protect-bash.sh` read its
fields through `< <(jq ...)`, which loses jq's exit status: with jq unavailable the command came back
empty and the hook exited 0 — *could not parse* read as *nothing to check*. And the first fix reported
that failure through `x4_require_input`, which emits its verdict **with jq**, so a broken jq made the
refusal itself silent. Both now fall back to a static JSON literal. The suite gained a parser-contract
probe and four empty-input-with-broken-jq probes (62 → 67), every one watched fail first.

### Changed — hooks now advise or deny CLAUDE rather than prompting YOU

Bringing five never-exercised guards to life at once made the problem obvious: **17 rules could
interrupt the user**, most of them about nothing more than the assistant's own command hygiene.

Your attention is the scarce resource. A prompt spends yours; a deny or an advisory spends the
assistant's — it reads the reason, fixes the command and retries, and you never see it. So:

| verdict | when |
|---|---|
| **deny** | there is a correct alternative the assistant can simply take — `git add -A`, `$?` after a pipeline, a long job in the foreground, searching the profile by name |
| **advise** | no alternative to offer, just a caveat worth knowing — editing a mod's `content.xml`, or a search that can only return a partial answer |
| **ask** | genuinely yours to decide: editing profile files and save games, editing a deployed mod, and writing or deleting inside the game directory |

**17 user-facing rules → 2 categories** (your profile and saves, and the game directory). One rule was dropped entirely as too broad: it fired on any command
whose *text* mentioned a `.cat`, including an `echo` that merely discussed one. Writing a `.cat` is
still blocked by `protect-files.sh`, which checks the actual target path.

⚠ **Note for anyone who had these hooks installed:** no rule in them had ever run in production, so
none of their false-positive rates were known. One surfaced within two commands of the fix — the
`rm` rule matched *"an rm appears anywhere"* and *"the game path appears anywhere"* independently,
so deleting a temp file was blocked because the command mentioned the game directory elsewhere.

### Added — a search under the game's `extensions/` folder now warns that the answer is partial

Grep and Glob read **loose files only**. X4 mods ship as packed `.cat` archives, loose XML, or
both — so a search over a packed mod does not fail and does not say anything was unreadable. It
returns the loose subset, silently, in the same shape a complete answer would have. Measured on
the reference machine: **54 of 133 installed mods (41%) ship both**, so the misleading case is the
common one rather than an edge.

A new `Grep|Glob` hook asks for confirmation when a search targets the `extensions/` root, or a
directory inside a mod that actually ships a `.cat`, and names the packed-inclusive alternative.
It is deliberately narrow — a single named file is not a survey and never prompts, and a
loose-only mod is fully visible so it never prompts either. Measured against 21 sessions of real
history: it fires on 4 searches out of 311.

### Fixed — `x4modlist --registry <path> snapshot` wrote to the DEFAULT registry

Every read honoured the override; the one path that WRITES did not. `snapshots_dir()` resolved the
default registry unconditionally, so a snapshot taken against a throwaway registry landed in the
real one — and reported success. The read side (`changed --since latest`) looked in the same wrong
place and was fixed with it, because a lookup chain taught in only one direction is a subtler bug
than the one it replaces.

### Changed — the CLI sweep covers all 41 capabilities, and names what it skips

`gates/qa_sweep.py` exercised 28 of 41. The remaining 13 are now cells: 11 in the default run, and
two real builds behind `--all`, each redirected at a throwaway output so no shared artifact is
written. The default run prints the slow cells it skipped rather than reporting a clean total over
a subset.

### Added — the BaseX builds name the engine tree they used, and refuse an ambiguous one

`build-effective.sh` and `build-corpus.sh` defaulted `X4VALIDATE_DIR` to `$HERE/../x4validate`.
That is a *position*, not an identity. The freshness `engine` axis hashes the bytes of the engine
sources, so when more than one checkout sits side by side — one git worktree per concurrent
session — a build can stamp the artifact with a different tree's engine hash. A wrong engine hash
reads as **fresh**, not as an error.

Both builds now print the resolved tree with its git branch and HEAD, every run. That half has no
failure mode: a build whose provenance is in its own output can be audited afterwards.

The refusal fires **only** when the choice is genuinely ambiguous — more than one sibling that
actually contains `x4validate/_merge.py`, and no `X4VALIDATE_DIR`. A single-checkout install can
never trip it, and a stray `x4validate-backup/` is not mistaken for a rival.

### Changed — a deployed-mod edit is a hard block when the source lives elsewhere

Writing to a file under the live `extensions/` folder used to always ask. It now depends on
whether a source copy exists somewhere else, because that is what decides whether the edit is
recoverable:

| your setup | writing under `extensions/` |
|---|---|
| a separate mods root outside the game folder | **denied**, naming where the source is — every deploy overwrites this copy, so the edit would be silently lost |
| mods root IS the extensions folder | **allowed** — the deployed copy *is* the source; this is ordinary work |
| no mods root configured | **ask** — nothing says where the source is, so it cannot be decided for you |

All three branches have their own probe. The first two are reachable only in different
configurations, so testing one of them would have left the others as dead code.

### Fixed — `scripts/test-hooks.sh` counted a dropped probe as no probe at all

A probe line that fails to parse increments neither the pass nor the fail counter: it vanishes,
and the run still prints a cheerful total. That happened while adding the probes above — bash
printed `n: command not found` and the suite reported `33 passed, 0 failed`. The total is now
asserted against an expected count that has to be updated deliberately.

### Added — `X4_BACKUPS` documented in `x4-paths.env.example`

`backup-before-edit.sh` has always honoured it; nothing said so. It matters when the toolkit root
is not where an existing audit trail lives, because otherwise changing `X4_TOOLKIT` quietly starts
a second trail and the history splits in two.

### Changed — the advisory validate hook routes cross-mod files to Tier B

A file at `<mod>/extensions/<target>/...` is a cross-mod patch, and Tier A builds base+DLC only,
so it reports "no base game file — can never apply" for every such file. Measured on a real
cross-mod overlay: Tier A `error_count=1`, Tier B `error_count=0`, same correct file. Left on
Tier A the hook cries wolf on every edit to a cross-mod overlay, which is worse than not running.

### Fixed — the identifier scan could not see your newest file

`scripts/scan-identifiers.py` built its population from `git ls-files`, which reports the **index**.
A file you have created but not yet staged is therefore invisible to it by construction — and that
is the file most likely to carry something you have not thought about. It printed *"scanning 200
tracked file(s) ... clean"* over exactly such a file; staging it took the population to 201 and the
scan then meant something.

Both halves are now scanned, and the population line names both, so a zero is visible rather than
implied. Ignored build output stays out. A hit in an untracked file is a full finding, because that
is the case being prevented.

`--selftest` is new and CI runs it **before** the scan. In CI the untracked list is always empty, so
CI could never have caught this defect and cannot demonstrate the fix either; the selftest is the
only thing there that can go red over it.

### Added — the hooks that were only ever running locally, and a suite that proves them

`.claude/hooks/protect-bash.sh` shipped at **3,445 bytes** while the version in daily use was
**17,722**, and it was missing every rule added over the past month. `test-protect-bash.sh` — the
regression suite that makes those rules trustworthy — had never shipped at all.

Nine rule families are now here, each keeping the MEASURED cost of the failure it prevents, because a
guard whose reason has been trimmed away is the first one someone deletes:

| guard | what it cost when it happened |
|---|---|
| `git add -A` / `.` in a shared tree | 4 untracked files from a concurrent session, one commit-timing away from being committed |
| `timeout` above the harness cap | 600000 ms is silently clamped; four separate 10-minute losses in one session |
| a known long job in the foreground | the same clamp, reached by a different route |
| truncating redirect onto a durable record | `>` gets no backup; this wiped a file to 0 bytes |
| recursive search over the reference tree | route it to a tool that returns a denominator |
| recursive search rooted at a workspace root | `grep -r` killed at 300 s; ripgrep timed out at 20 s |
| profile `content.xml` searched by NAME | it is keyed by manifest id — a name-shaped zero is the wrong query, not an answer |
| `$?` read after a pipeline | reported a real exit 5 as "exit 0" |
| measurement output into shared `/tmp` | another session's stale files nearly reported as this run's results |

All paths resolve through `_x4-env.sh`; nothing is hardcoded. Path-dependent probes **SKIP loudly**
when a location is unconfigured and the tally names the skip count — otherwise a fresh clone would
print the same cheerful line as a real run, over rules that were never exercised.

**The suite immediately earned its place** by catching two regressions the port itself introduced: an
over-block that denied a deliberately *scoped* search, and an under-block where an unexpanded
`$X4_PROFILE` stopped matching. CI now runs it before the test suite.

### Changed — the six skills, merged rather than replaced

The shipped skills were behind the ones in use (one at 2,777 bytes against 5,131) but were **not
simply stale**: they carried cross-platform work the newer copies lacked — registry resolution
through `$X4_MODS`/`$X4_REGISTRY`, Linux profile locations, and an interpreter pin. Each file was
merged in both directions and every dropped line reviewed, so nothing regressed to a
machine-specific path.

Adds **`x4-balance`**, which grounds a stat-tuning discussion before any value is proposed: it names
which instrument answers which question and **what a zero from each one means**, then enforces the
three-values rule (vanilla / effective / proposed) and an in-sector vs out-of-sector check.

### Fixed — two named mods in published skill descriptions

Skills are generic procedure and must never name a specific mod. `x4-mod-interaction`'s description
referred to a particular overhaul by name, and `x4-update-mod` named specific mod APIs as its
migration examples. Both are generalised. Measured across all six skills: 0 named mods, 0 personal
paths.

## v2.9.0 — 2026-08-28

### Added — nested cross-mod script patches are now validated, through the document the engine builds (F70)

A patch at `<mymod>/extensions/<target>/md/foo.xml` was validated by **nothing**: both halves of the
script pass check direct children only. `--update` now validates each one through its **merged
result** — target + patch — rather than the patch file, which is a `<diff>` the engine never parses
on its own.

**It reports only what the patch is responsible for.** Validating the merged document alone would
blame a mod for its target's pre-existing schema violations. MEASURED over all 16 nested patches in
a real 125-mod install: **182 findings, of which 167 (91.8%) belong to the target** — a mod named
`moreroomsforships` fails `md.xsd`'s capital-letter pattern because its own author named it that, and
the patching mod can neither cause nor fix it. So both sides are validated and diffed per finding:

```
- XSD(nested): 1 cross-mod script patch(es) validated via their merged result
  — 0 introduced, 1 fixed, 0 not checkable
```

`fixed` is reported as well as `introduced`, because a check that can only go one way cannot be
trusted to go the other.

**A file that could not be checked never reads as one that came back clean**, and the two reasons are
kept apart:

- *target mod is not installed* — the engine no-ops the patch too; not a defect.
- *target is installed but does not supply that file* — a **stale patch that silently never applies**.
  One real instance in the live set: `ship_variation_expansion_vro` patches `md/spawnclaymore.xml`,
  and its target ships seven md files, none of them that one. No engine error is produced for this.

Findings are matched by message, not line number, so a patch shifting lines does not report every
inherited finding as both removed and added.

*Known limit, stated because it is easier to trust a tool that names its edges:* the attribution
compares the target alone against the target plus this mod. A finding that appears only in
combination with a **third** mod's patch is outside what it can see.

### Fixed — `x4effective dump` no longer reports a wrong FORM as a confident ABSENCE (F71)

`dump` keys documents by their **logical** vpath — the one the engine builds. Addressing a mod's
document by its path on disk, `extensions/<mod>/md/foo.xml`, missed and printed
`no effective content`. That reads as *the file is not in the live tree*, when the truth was *right
file, wrong form*. **1,713 of 3,257 touched vpaths are mod-owned**, so this covered a third of the
surface.

It now resolves the logical vpath and **says so**, because resolving silently would swap one
confident-wrong answer for another:

```
$ x4effective dump --chain extensions/moreroomsforships/md/morerooms.xml
<!-- note: 'moreroomsforships' is an installed MOD, not a DLC, so that is a disk path,
     not a game vpath; interpreted as logical vpath 'md/morerooms.xml' -->
<!-- sources: moreroomsforships:full, zzz_fixer_morerooms:diff(nested:moreroomsforships) -->
```

Deliberately narrow, and each limit is tested:

- The retry runs **only after the literal lookup genuinely fails**, so every path that resolves
  today resolves by the same route it does now.
- **DLC paths are never rewritten.** `extensions/ego_dlc_split/...` *is* a real game vpath; that
  case was already correct and is unchanged.
- **Double-nested paths are never rewritten**, mirroring `_effective.build_touch_map`, which
  refuses the same shape because patch-on-a-patch is not engine-proven.
- **A genuine absence is still `rc 1`.**

DLC-ness is asked of `Config.dlc_dirs()`, never guessed from an `ego_dlc_` name prefix — the
existing `test_dlc_enumeration` guard rejected the prefix form, correctly, on its first run.

### Correction — the "0 mods newly report an error" figure was wrong; it is 1, and it is a real bug found

The entry below claimed a per-mod census showed **0** mods changing exit code. A proper per-mod
census — the same run, both tiers, all 125 installed mods — shows **exactly one**, and it is a
**true positive**:

```
xenon e class ship   0 -> 1   md/xenone_resistant_system.xml:17
                             Element 'find_ship': attribute 'space' is required but missing
```

That is a genuine 9.0 breakage (`find_ship`/`find_station` gained a required `space` in 9.0), of the
class the knowledgebase calls the only reliable 9.0 migration signal — and it was **invisible in a
default run** until this change. So the change is doing exactly what it was built to do; the claim
about its blast radius was simply false.

**Why the first measurement said 0:** it filtered findings on `f.level`, and the field is
`f.severity`. `getattr(f, "level", "")` returns `""` rather than raising, so the filter matched
nothing and produced a confident zero. Use `Report.errors`, which cannot fail that way.

**One other row moved and is not a code change:** a mod folder was deleted between the two census
runs, so it went from validating cleanly to "mod folder not found" (exit 2). Corpus drift, named
here rather than absorbed.

### Changed — the cheap script check now runs on every validate, not only under `--update`

A mod whose payload is `md/` or `aiscripts/` files used to have **no script check at all** in a
default run, because every script check sat behind `--update` — which compiles `md.xsd` at ~102s.
For an additive-only script mod that meant the one check capable of failing was the one not run,
while the summary said "OK".

The required-attribute pass needs no schema compile, so it now runs always. MEASURED on a real
125-mod install: **6.3s total, 0.100s for the heaviest single mod, and 0 mods newly report an
error** — so no real modlist changes exit code. Its corpus-wide parity with libxml2 is proven by
`gates/xsd_fast_parity.py`: **555 script files, 0 false positives, 0 misses.**

What still needs `--update` is now named rather than implied:

```
 - required-attrs: 5 script file(s) checked without compiling a schema (0 gating breakage(s))
 - script-schema: 5 md/aiscripts file(s): the required-attribute class was checked and is
   COMPLETE, but the 'element not expected' class - where element ORDERING errors live - and
   the schema-strict advisories were not. Those need the ~102s schema compile: `--update`
```

That class matters: of the three real violations in the report that prompted this work, **two were
element-ordering errors**, and `--update` reports them as errors with exit 1.

⚠ **Considered and rejected: degrading those mods to exit 3.** It would have fired on 18 of 125 mods
permanently, clearable only by paying 102s every run — which turns exit 3 from "investigate" into
"ignore". The right answer to "a check did not run" is to run the check.

A mod with no script files gets no script commentary at all, in either direction.

### Fixed — cross-mod script patches were missed by the disclosure, and are validated by nothing

Found by red-teaming the follow-up plan rather than by a failure.

A mod that patches another mod puts its files at `<mymod>/extensions/<target>/md/foo.xml`, which
does not start with `md/`. The disclosure added in the previous release matched on that bare prefix
and so **missed every cross-mod script patch — 15 files across 7 mods**, which means the figures
published with it (77 of 124 mods, 17 script-only) were wrong. The true figures are **79 of 125 and
18**. The nesting strip is now a single shared helper used by both call sites, so they agree by
construction rather than by both being edited correctly.

**The larger half:** those nested files are validated by *nothing*. Both halves of the script pass
check direct children only — deliberately, so the loose and packed halves cover the same population
— while the effective-tree check skipped nested scripts *on the stated grounds that the script pass
already covered them*. It does not. That justification has been corrected in place, because a false
justification is how a gap stays invisible.

So the two populations are now reported **separately, because the advice differs**:

```
 - script-schema: N md/aiscripts file(s) were NOT validated ... runs only under `--update`
 - script-schema-nested: M nested cross-mod script patch(es) ... are NOT VALIDATED BY ANYTHING
   - not by this run and not by `--update` either, which checks direct children only
```

Simply counting the nested files into the existing message would have made the number complete and
the **advice false**, which is worse than the under-count it replaced.

⚠ **Coverage is unchanged and that is deliberate.** Widening the script pass to nested files alters
what `--update` validates and needs its own measurement; it is registered rather than done. The
evidence that these files are live: all 15 are `<diff>`, the nested path is the engine's documented
cross-mod patch form, and the engine demonstrably loads their targets.

### Fixed — a patched data file that declares a schema is disclosed as unvalidated too

Found by sweeping for the shape rather than by tripping over it. `check_effective_schema` validates
the *merged* result of every data file a mod patches against the schema that file declares, and it
sits behind `--update` exactly like the script pass. So a mod patching `libraries/god.xml` — which
declares a schema — reported `OK: no issues found` with no mention that the check existed and had
not run.

Now disclosed in the same NOT CHECKED channel, naming the count and an example file. Exit code
unchanged: unlike the script-only case, the diff itself genuinely *was* examined, so a clean result
is defensible — it just was not the whole story.

Eligibility and the schema lookup come from the existing `_xsd` helpers rather than being re-derived,
because a second implementation of the same normalisation is how these gaps get made in the first
place.

### Fixed — a mod shipping `md/` or `aiscripts/` no longer reads as a clean pass

An additive-only `<mdscript>` with three `md.xsd` violations returned **"OK: no issues found"**,
exit 0.

The schema check was not missing — `--update` reports two of those three as **errors** (element
ordering) and the third as an advisory. What was missing was the *sentence*. Compiling `md.xsd`
takes about 102 seconds, so every script check deliberately sits behind `--update`; in the default
run nothing examined the file, and the summary still said OK. For an additive-only script mod that
is the whole story: no diffs, no selectors, nothing else to resolve — **the one check that could
have failed was the one not run.**

The default run now records it in the existing NOT CHECKED channel, and the summary line changes
itself accordingly:

```
  no issues found in what could be checked

  NOT CHECKED:
   - script-schema: 1 md/aiscripts file(s) were NOT validated against their schema and are this
     mod's ONLY payload, so nothing that can fail was examined — the schema pass costs ~102s to
     compile and runs only under `--update`
```

**The exit code is unchanged.** MEASURED over the installed set, **79 of 125 mods ship script XML
and 18 are script-only**, so degrading the run here would fire on most of the corpus by default —
and a check that floods is worse than no check, because it teaches you to skip the output. Whether
the script-only case should degrade is left as a deliberate decision.

### Fixed — three-way diff: a whole-file override now joins the plain document it overrides

Found on the second real use, once the nested-path fix let the comparison get far enough to fail here
instead.

A mod supplies a document as `<diff><replace sel="//macros">PAYLOAD</replace></diff>` — the standard
whole-file override idiom, of which VRO alone ships 848 — while the mod it patches supplies the same
document plainly. Across the join every attribute path on one side then carried a `/diff/replace/`
prefix the other lacked, **nothing matched, and the whole document fell to node-level counts** like
`+14 / -12 node(s)`. That output was honest — node-level changes are reported, not classified — but
the files most worth classifying were exactly the ones it could not classify.

The payload is now unwrapped on each side independently, and the unwrapping is listed under
`UNWRAPPED` like any other path rewrite. It is narrowed by **self-consistency rather than a list of
known selectors**: the diff must hold exactly one element op, it must be a `replace`, it must carry
exactly one payload element, and the selector must name that payload's own tag. So
`<replace sel="//ware[@id='gun']/@damage">`, an `<add>`, and a two-op diff are all left alone.

### Added — additions and removals are labelled, not left to be inferred

An attribute the author **removed** and one upstream **added** are not value edits, and a consumer
applying either as a value edit would write the absence sentinel into the attribute rather than
deleting it. Both are now named in the listing:

```
  author edits (1):
    .../bullet_x.xml  ...damage@shield: 7500 -> ∅  [author-removal]

  upstream drift (1):
    libraries/wares.xml  ...@targetable: ∅ -> 1  [upstream-addition]
```

The second is the case this whole tool exists for: an attribute present today and missing from an
old file is upstream *addition* far more often than author *removal*, and it is now stated by the
tool rather than inferred by a reader. Bucket totals are unchanged — only the label is new.

### Fixed — three-way diff: a nested overlay is now compared against the mod it patches

Both defects below were found on the tool's **first real use**, hours after it shipped.

A mod that patches another mod puts its files at `<mymod>/extensions/<target>/<mirrored path>`, and
that is the normal shape for a personal overlay — which is precisely the porting job this tool
exists for. The overlay's vpaths therefore never joined with the vpaths of the mod it patches:
**0 documents compared**, with the *same logical file* listed under both exclusions at once, once as
`NO BASELINE` under the nested path and once as `NOT IN THE ARCHIVE` under the plain one. On the real
mod that surfaced this, all four files of interest were excluded and the comparison was empty.

The prefix is now removed when — and only when — `<target>` is a name the baseline answers to, by
folder name *or* `content.xml` id, since those differ often enough to matter. A patch aimed at some
third mod stays excluded: trading a false negative for a false positive would be no improvement.
Every rewrite is listed under `UNWRAPPED`, because silently rewriting a path is a transforming step,
and two paths collapsing onto one join key are reported rather than one being dropped.

### Fixed — three-way diff: `--file` was ignored

`--file` filtered nothing in three-way mode: two different files and no filter at all produced
byte-identical output. It now filters the listings while the **counts stay the whole comparison**,
said so explicitly, so the denominator is never quietly narrowed by a display option. A `--file`
that matches no classified attribute now says that this is an absence *only if the file is in the
comparison*, and points at the exclusion lists — rather than reading as "no differences".

### Added — `x4diff --base`: three-way diff, separating your edits from upstream drift

A two-way diff between an archived mod and the current release answers the wrong question and
answers it confidently. Measured on one real 2021 mod of 135 documents: **~440 attribute deltas
two-way, but only 15 were the author's.** 340 were upstream's own work since, and **124 of 135
documents were verbatim copies of the baseline** — so **96% of what the two-way diff called "the
port" was someone else's work.** Acting on it would have re-applied 340 upstream changes as if they
were the user's, and reverted the current release across 124 files.

Supply the common ancestor with `--base` and every attribute lands in exactly one bucket:

```
  documents shared with the baseline : 135
    the author edited                : 11
    VERBATIM (author touched nothing): 124
  attributes classified              : 356
    author edits ....... 15
    upstream drift ..... 340
    converged .......... 0   (both sides, same change)
    BOTH-MOVED ......... 1   <-- the decisions
```

**`BOTH-MOVED` is the point.** One conflict is a decision you can make; 440 undifferentiated deltas
are a wall you cannot. Exit 1 when there is one, 0 when the port is mechanical.

⚠ **A one-sided absence is reported as unknown, never as a deletion.** An attribute present in the
current tree and missing from an old file is upstream *addition* far more often than author
*removal*, and a two-way diff cannot distinguish them — 16 macros in that same mod appeared to have
lost an attribute nobody had touched. A three-way diff can tell them apart wherever a baseline
exists; where one does not, the document is **named and excluded from every bucket** rather than
guessed at. Documents present in the baseline but absent from the archive are likewise excluded and
never reported as drift.

The classification is a join over two ordinary two-way diffs, not a second differ — a duplicate
implementation of the same normalisation is exactly what once made an independent measurement report
2.6% where the truth was 65.4%.

### Changed — the CLI no longer lives inside the freshness-hashed engine sources

The `engine` freshness axis hashes the whole bytes of seven source files, so **editing an error
message invalidated the effective store and BaseX `x4eff`** exactly as a merge-semantics change
would — while the stale banner asserted *"the SAME inputs would now merge differently"*. Measured
twice in one session: a guard's CLI text moved the hash, and then a pure **docstring** (10 lines
added, 0 executable) moved it again.

The CLI surface is now `_effectivecli.py` and `_diffcli.py`, neither of which is hashed.
`_effective.py` drops 1,360 → 897 lines and `_diff.py` 276 → 188. Nothing about the merge changed,
and nothing you run changed — `x4effective` and `x4diff` behave identically.

Deliberately **not** done: making the hash cleverer. An AST- or token-level digest would fail in the
unsafe direction, where one missed merge change lets a stale artifact report itself fresh. Six tests
now pin the boundary: no hashed source may parse arguments, define a command entry point, or print
beyond a stated two-call allowance for the unconfigured-install refusal. Verified in both directions
— a CLI edit leaves the hash alone, an engine edit still moves it.

### Fixed — a mod-set mismatch reported the wrong direction

The check that BaseX `x4eff` models the *active* mod set listed only mods `x4eff` carries that the
engine will not load. In the common case — an index built before you added a mod — that list is
empty, so the failure printed an empty set and named a direction that was not the problem. It now
reports both, and names the mods that are active but not indexed.

### Fixed — `x4effective attr` no longer answers a bad question with a clean zero

`attr` took two free-form arguments and validated neither, so a query that could never match
anything printed `0 value(s)` and exited **0** — indistinguishable from a real, informative absence.

The guard for this already existed and was wired into `ls`, `show` and `who-sets`; `attr` was the
one command it never reached, and the only one where **both arguments can be individually valid
while the pair matches nothing**. `attr ship hull.max` is that case: `ship` is a real kind (514
entities) and `hull.max` is a real property (1,973 values) — they just live in different kinds.

`attr` now rejects an unknown kind (**exit 2**) or an unknown property (**exit 1**), states the
denominator, and points somewhere useful:

```
$ x4effective attr macro properties.hull.max
no macro carries prop 'properties.hull.max' - that kind has 8672 distinct prop(s)
       did you mean 'hull.max'? (1973 value(s))

$ x4effective attr ship hull.max
no ship carries prop 'hull.max' - that kind has 31 distinct prop(s)
       'hull.max' is carried by kind 'macro' (1973 value(s))
```

Suggestions are looked up **in the store**, never derived from a rule. Property names for *macros*
are stored with the `<properties>` wrapper stripped (`hull.max`, not `properties.hull.max`) — but
that is a macro convention, not a store-wide one: **6,842 rows legitimately keep the prefix**, so a
blanket rule would be wrong for every one of them. A genuine zero — the property exists for the kind
and a `--class` filter excludes every row — is still **exit 0**.

### Fixed — `apply_diff` names the argument you got wrong

The third positional parameter is `recorder`, not `source`. Passing a filename there failed four
frames down with `AttributeError: 'str' object has no attribute 'elem_replaced'`, naming an internal
method rather than the bad argument — and *which* method it named depended on which operation the
diff happened to contain, so a diff of pure `<remove>` ops recorded nothing and sailed through. It
now raises `TypeError` at the call, naming both `recorder` and `source`.

### Changed — a docstring that had rotted

`_registry.mods()` did not mention that **neither** scope includes the DLC — `ego_dlc_*` is
base-game content, and counting it would double-count against the unpacked reference tree. The
caveat was documented on `scan_installed`, but not on the function the documentation tells you to
prefer instead, so a dependency check asked `mods("installed")` whether a DLC was present and was
told no. For that question use `Config().dlc_dirs()` or `.packed_dlc_names()`.


### Added — `x4modlist tracked`: what the account FOLLOWS, against what is installed

Tracking is a **third population**, distinct from *installed* (on disk) and *active*
(engine-loadable): it is what you asked Nexus to watch. The two mismatches are the point —
**followed but not installed** (candidates you already showed interest in) and **installed but not
followed**, which means no update notification, and that one costs something silently. MEASURED on a
live account: **36 of 73** installed mods with a known Nexus id were untracked.

⚠ The endpoint is **account-wide** (1,616 rows across 9 games for 413 X4 ones), so filtering to one
game is a narrowing step and the denominator is printed first, always. A payload that is not a list
**raises** rather than reporting "0 tracked" — an upstream shape change is a non-answer, not an
absence.

## v2.8.0 — 2026-08-26

### Added — `x4save`: read a savegame, and say what it would silently lose

A save is the only artifact the ENGINE wrote, and it answers two questions no manifest can.
Both measured in-game.

`uv run x4save info` — the header, and the **save-baked** extensions. **`<patches>` is not a
record of what loaded.** An extension appears iff its `content.xml` `save` attribute is ABSENT
or `="1"`: of 129 installed extensions 11 qualify, 10 were recorded (the gap is an online DLC
not loaded as a content patch), and **0 of the 118** declaring `"0"`/`"false"` appeared. For
mods that is **3 of 121 (2.5%)** — *less* complete than the profile `content.xml`. The command
prints that denominator, so the list cannot be misread as coverage. What it does tell you is
which mods are welded into the save, i.e. which removals are dangerous.

`uv run x4save check <save>` — macro references the live tree no longer defines.

**This exists because the engine is silent.** Removing a mod leaves no dangling references: X4
**deletes the orphaned content**. Disabling one mod took its 37 macros / 46 references to zero;
the engine logged **one** error line, and it named a galaxy connection rather than the station,
the ship, the production modules or the 36 other macros that vanished. No dialog. And the net
error count went **down by 3**, because the mod's own 4 errors left with it — judged by
`debug.txt`, the removal looked like an improvement. `check` names 37 items where the log names
one. (Scope: n=1 mod, one save. The tool reports what a save references; it does not assert the
general rule.)

Cheap, despite the file size: the largest save here is 140 MB compressed and **1.28 GB**
expanded, and streams in **2.5 s**. Everything is streamed; nothing builds a tree.

### Fixed — a guard that caught a vanishing CLI but not a new one

`tests/test_unconfigured_refusal.py` asserted `len(entries) >= 9`. That catches an entry point
*disappearing* and silently accepts one being *added* — so shipping a new CLI passed a check
written to notice exactly that. Now `== 10`, changed deliberately.

### Fixed — CI had never once started a JVM (the vendored BaseX jar was untested)

The 24 BaseX tests pass on a machine with no Java, **because they mock**: `test_preflight.py`
monkeypatches `shutil.which` and writes a jar that is literally
`b"not really a jar, but it is a file"`. Since the jar was vendored in v2.6.0, no CI job on any
OS had executed it. A bad jar, a classpath change or an untracked `.basexhome` would have
shipped in silence — a green that could not have gone red.

CI now installs Temurin 17 and runs `tools/basex/smoke-basex.sh`, which builds a three-document
database and runs a real `collection()` query against the real jar. It is deliberately **not** a
pytest that skips when Java is absent, because a skip in CI is indistinguishable from a pass.
Proven able to fail in three ways: remove `.basexhome` → red, corrupt the jar → red, hide `java`
→ red.

### Fixed — `setup.sh` never checked for Java, while `README.md` promised it

`README.md` states "Java 17+ — optional, only for the BaseX corpus search", and the guided
install flow (`setup.sh`, `install.sh`, `install.ps1`, `SETUP_PROMPT.txt`) contained **zero**
mentions of it. Users met the requirement only when a corpus build refused with exit 2 — correct
behaviour, but the only mechanism. `setup.sh` now probes for it alongside `jq`/`uv`/`wine`, as a
warning rather than a failure since BaseX is optional. The floor is measured, not chosen: BaseX
12.4's classes are bytecode major 61.

### Added — `EntityDefs.all_names()`, and BLIND-SPOTS **F65**

`EntityDefs.__contains__` answers a per-NAME question at per-name cost, justified by a stated
population: *"7 distinct references across 114 mods miss the eager tiers."* True for a mod —
which is a property of the caller, not of the tool, and nothing enforced it. A savegame carries
**5,022** references, ~2,500 of which miss the eager index; the lazy tier blew a 600 s cap with
no result. Built in bulk the same answer takes ~19 s. No shipped caller was affected.

### Changed — provenance now says which source WON, not which INTRODUCED

`x4effective who-sets` and `dump --chain` report which overlays produced a value. A root
`<replace sel="//macros">` swaps the whole document, so **base contributes no chain entry at all** —
VRO's dominant idiom, 848 of them. A single-entry chain therefore read as *"this mod introduced this"*
when it usually means *"this mod re-supplied what vanilla already had."*

MEASURED: of **35,423** single-op root-replace attributes, **23,182 (65.4%)** also exist in vanilla
with the chain hiding it; only **39** vpaths are genuinely mod-added. `bullet_arg_m_ion_01_mk1_macro`
is the sharpest — vanilla 10, live 10, chain `[vro]`: identical value, sole credit, no hint a base
file exists. It cost a real design conclusion.

Both commands now disclose when base+DLC also supply the vpath, and stay **silent** for genuinely
mod-added vpaths, pure-base values, and chains that already name base. Scope is existence, not the
vanilla value — reporting the value would mean a second implementation of the property flatten, and
that is what made the original measurement of this defect report 2.6%. BLIND-SPOTS **F64**.

### Fixed — a store could be stamped with a world it was never built from

`_ext_root()` returned the real extensions directory unconditionally, behind an `except
AttributeError` that can never fire — the attribute exists and its value is `None`. A store built
over throwaway directories was stamped with a fingerprint describing the **real** installed game.
It now records an **UNKNOWN** content axis instead, which never reads as fresh. **F63 symptom 2.**

### Fixed — the line-ending pin was never checked against the bytes it governs

`.gitattributes` pins shell scripts and Python to LF. **A pin governs CHECKOUT; it does not repair a
file already in a working tree**, and nothing counted the bytes. MEASURED: **14 of 155** pinned files
held CRLF here — including `bin/xrcat` and `.claude/x4-paths.env.example`, where CRLF is
**functionally fatal** rather than cosmetic, because both are sourced by bash and a trailing CR lands
inside every value.

A fresh clone was never affected — checkout applies the pin — but nothing would have caught the next
file to drift. The new guard asks **git** what the pin resolves to and reads the **working tree**,
with no allow-list. BLIND-SPOTS **F67**.

### Internal — the checks that check the checks

**Mutation coverage 1 module → 6, 15 mutants, `killed 15/15, survivors 0`.** Every detector it names
is now provably killable rather than assumed so: the bare-vs-nested door, load order replaced by
alphabetical, property recursion truncated to depth 1, and the packed half of a scan never entered
(**F54**). `all_names()` gained the tests it shipped without — the property that matters is that it
is **never narrower** than the per-name door, since a narrower set does not crash, it reports defined
names as dangling. And **F66** records an investigation that found *nothing*: producer/consumer key
normalisation, three pairs, no disagreement — filed so the line is not silently re-opened.

## v2.7.0 — 2026-08-26

### Fixed — five tests failed on any machine with no game installed (F63)

`_effective._ext_root()` returns `_registry.GAME_EXTENSIONS`, guarded by `except AttributeError`.
**That guard never fires** — the attribute exists and its *value* is `None` when nothing is
configured. `_freshness.fingerprint()` then refused, correctly (it must never guess an extensions
root and hash whatever directory you are standing in), but it could not tell *"the caller forgot"*
from *"the caller looked and there is none"*.

Every fresh clone and every CI runner is the second case. Both now have spellings:
`fingerprint(config)` still **raises**; `fingerprint(config, extensions=None)` records the content
axis as **UNKNOWN**.

⚠ And UNKNOWN never reads as fresh. `None == None`, so a naive comparison would have made two
unknown content axes *match* and report FRESH — an artifact that cannot say which world it describes,
declaring that it still describes it. `compare()` now says freshness could not be **established**,
which is neither fresh nor known-stale.

`_freshness.py` is deliberately not among the sources the engine fingerprint hashes, so this changes
no artifact's fingerprint and nothing needs rebuilding.

### Fixed — the mutation gate ran a narrower scope than it reported (F62)

`gates/mutation_probe.py` drops a missing test path from a mutant's scope before invoking pytest.
Filtering a vanished path is right; doing it **silently** is not — and it was happening inside the
one gate built to detect vacuous assertions.

MEASURED: this gate's `_registry.py` scope lists four test files. In one checkout only three exist,
so it ran **3 of 4** and printed the same `killed` line as a full scope.

Two more in the same function. An **empty** scope returned the verdict `"hang"` — an absence
rendered as a timeout that was never measured, which the caller then "confirms" by re-running,
against a file that is still not there. And the test meant to catch all of this,
`test_every_target_has_tests_that_exist`, asserted `any(... exists ...)`: its NAME promises every
file, its ASSERTION accepted one, so three of four present passed.

Now: a partial scope **names what it dropped**; an empty scope returns a new verdict `noscope` with
its own bucket, excluded from the killed count and failing the gate; an empty BASELINE scope returns
**rc 2** (cannot run), never rc 1 (has findings). The check is `mp_scope_gaps()`, which returns the
**named gaps** rather than a boolean.

The asymmetry is the proof: it is GREEN against a complete tree and RED against an incomplete one,
naming the exact missing file. Six tests written first and each watched fail for the right reason —
one printing `killed 1/1` for a mutant that was never challenged. Probe re-run afterwards:
**11/11 killed, 0 survivors, 0 hangs, 0 unmeasured.**

### Changed — `docs/QA-PROCESS.md`: how to prove a port

Rule 2 said to prove a port with `diff -rq`. MEASURED on a real pair of trees, that reports **52
differences where 12 are real** — the other 40 are line endings, from a working tree checked out
before an LF pin, while both repositories store identical bytes. A proof that buries findings under
non-findings teaches you to skip it. The rule now says to compare **committed blobs**.

### Fixed — a mutating gate could not put the tree back if it died (F59)

`gates/mutation_probe.py` breaks a source file on purpose, runs the tests, and restores it. The
restore lived in a `finally:` — and **`finally` does not run on SIGKILL**. A killed probe therefore
left a deliberately-broken source on disk, and because that file is **tracked**, `git status` shows
an ordinary modification. **v2.5.0 shipped exactly that way once**: ambiguous-`sel` detection
silently disabled in a public release.

Not hypothetical. A stop command that **reported success and did not stop** left three gate sweeps
racing, so "never run a mutating gate against a tree in use" can be broken by accident.

**Reproduced rather than simulated** — launched the probe, polled until a target really differed from
HEAD, hard-killed it at t=7.5 s. It left `if len(new_children) != 1 and False:` on disk. Re-running
refused with **rc 2**; `--recover` restored it and named the mutated file.

Byte-for-byte pristine copies plus a `.mutation-probe-active` marker written **before** the first
mutation, so recovery never depends on the thing that failed. Recovery is explicit, never automatic:
auto-restore would need pid liveness, and on Windows `os.kill(pid, 0)` maps to `TerminateProcess`.

### Added — the mutation window is visible to every tool, and refuses writers

During a window every CLI answers from broken code with nothing on screen saying so. Reads now get a
**banner**; writes **refuse with rc 2**. The asymmetry is the point: a wrong read can be re-taken
once the ~70 s window closes, but a poisoned **artifact** outlives it and is trusted afterwards.

The banner rides on the decorator every entry point already carries, so all 9 CLIs are covered by
construction. The refusal sits at the two **stamping sites**, not at argument parsing, so no other
entry path can slip past it. ⚠ The marker resolves from `__file__`, never the CWD — these tools are
normally run from the game directory, where a CWD-relative lookup would report all-clear from the one
place it matters most.

### Changed — mutation coverage 1 file → 3, and a hang is its own verdict (F54)

`_merge` (6 mutants), `_registry` (3), `_compat` (2) — **11/11 killed**. Every mutant inverts a guard
whose failure has a **measured** cost, so a contrived mutant nothing kills cannot pad the number.
Per-mutant timeout **1800 s → 120 s**, and **HUNG** is now distinct from both killed and survived: a
mutant that hangs is a finding about the suite, not a pass.

### Changed — a stale verdict says how far it moved

Both freshness axes now quote `[stored -> now]`. "Moved how far, and by whose change?" is the first
question on hitting a stale artifact, and answering it previously needed a hand-written script.

### Fixed — a capability that existed, was correct, and was never reached (F58)

`x4effective dump --chain` answers whether a vpath exists in the live tree, **who supplies it**, and
whether they supply or merely patch it (`vro:full` vs `base, ego_dlc_x:diff`). A gap was filed
against it anyway, and a hand-rolled base-only substitute labelled **65 of 241 vpaths** as
"paths Egosoft renamed" — they are mod-supplied files that actually **win** those paths, so the error
pointed toward deleting them.

Fixed with a routing-table row, and **deliberately not a new helper**: the one added for this exact
trap the day before had **zero callers**. Also deliberately not a lint — the failure was in a
throwaway script, which no linter covers. MEASURED alongside: **21 of 30** CLI subcommands appear in
no routing surface.

### Fixed — the freshness content axis was blind to a mod's files changing (F57)

`_freshness.hash_content` stat'd **only** each mod folder's `content.xml`. The ordinary
overlay-deploy workflow — edit a file, copy it in, manifest untouched — moved nothing, so every
artifact reported **FRESH** while describing a different tree.

MEASURED: **72 of 121 non-DLC mods (59.5%)** have at least one file newer than their own manifest,
and the old code returned the identical digest `03122df005f47fbd` before and after a file-only edit.
This is a false **FRESH** — it fails in the unsafe direction, unlike F53's false STALE.

The axis now also hashes a per-mod `tree_sha` over sorted `(relpath, mtime, size)` for
`.xml/.cat/.dat/.lua`. Cost on a live 129-mod install: **0.10 s / 2,170 files**.

⚠ The suffix filter is **correctness, not speed**: 8 mods would otherwise move the fingerprint on
README/CHANGES/LICENCE churn alone.

⚠ **Scope limit**: `(mtime, size)`, not a content hash. An edit changing neither is invisible; a
same-size edit reads as `touched` rather than `content`. Manifests *are* hashed.

### Added — `x4modlist changed` / `x4modlist snapshot` (F56)

The digest said THAT the world moved, never WHAT. Localising one changed mod took nine investigative
steps and three false leads, and only worked because it happened to bump its `version`.

`content_detail()` now returns the per-folder vector, `hash_content()` folds it through a single
`_fold()`, and the vector is **persisted** beside the digest (190 KB) so a move is localised by
diffing rather than by forensics.

```sh
x4modlist changed [--since store|xref|latest|<path>] [--files] [--usn]
x4modlist snapshot [--label <name>]
```

- Triples are hashed as a **set**, never `max(mtime)` — the incident mod had **back-dated** its 26
  files, which is exactly what defeats a max and what made `find -newermt` useless.
- A baseline with **no vector raises `NoBaseline` and exits 3** — a non-answer, never "no change".
  Reading is optional, so no existing artifact flips to UNKNOWN.
- `--usn` needs Administrator (Microsoft's documented requirement for change-journal reads) and
  **degrades to a message** rather than failing the command.

### Fixed

- **F43** — the content axis could not see a mod being enabled or disabled in-game, because toggling
  rewrites only the PROFILE manifest. Now carried per mod, joined by **manifest id, never folder
  name**, defaulting to enabled for a mod the profile has never seen. Verified against the live
  profile: it finds exactly one disabled mod, matching independently-recorded ground truth.
- **F52** — `check_effective_schema`'s note went silent about files it skipped as soon as it
  validated anything. The `effective-schema: {checked} ` prefix is preserved; `gates/schema_sweep.py`
  parses it positionally.
- **F46 (swept)** — `fingerprint()` fell back to `Path("")` = `Path(".")`, hashing the current
  directory. It now refuses. All four production callers already passed the root explicitly.
- **F53** — the working tree is renormalised, so the untouched `ENGINE_SOURCES` are byte-identical to
  HEAD and this tree hashes like a fresh checkout.
- `_freshness` now walks **every** configured install root, not just one, so a mod deployed to the
  profile extensions root is no longer invisible.
- `_effective.base_has()` encapsulates the base-bare / DLC-prefixed vpath rule that made an audit
  report 58 DLC targets GONE when all 58 exist.

### Notes

- Tests **664 → 697**.
- The content and engine fingerprints each move **once**, deliberately. **The merge code has not
  changed** — `_freshness.py` is intentionally not in `ENGINE_SOURCES`.

---

## v2.6.0

**BaseX ships.** The corpus-search tool has been dev-only since it was written; it is now part of the
bundle, jar included. It exists to answer the one class of question a recursive grep answers
*dishonestly* — "nothing references X" — because a grep cannot distinguish *this does not exist* from
*I did not look everywhere*, and 62% of mod XML sits inside packed archives a grep never opens.
`ask.py` refuses to print a zero as a finding unless it can state the denominator it looked over, and
then says **"NEGATIVE CONFIRMED over N of M documents"** with every exclusion named.

It is **optional** and it is not free: Java 17+, roughly 3 GB of disk, and a build measured in
minutes. That is said up front rather than discovered on your first run.

Everything else here is the cost of making it safe to hand to someone else — plus five defects the
gates found while we did.

### Added

- **`tools/basex/`** — two indexes over the corpus: `x4raw` (every file *as written*) and `x4eff`
  (the merged, live tree, diffs applied in load order). Use `x4eff` for any claim about what the game
  actually sees; `x4raw` will happily quote a vanilla value your mod list overwrote.
- **BaseX itself is bundled** (5.2 MB, BSD-3-Clause, notice reproduced in `tools/basex/basex/LICENSE`)
  so there is nothing extra to download. Only the JVM is yours to install.
- **`tools/basex/README.md`** — install, and the two things that were nowhere in writing before: the
  **freshness refusal contract** (without it, a tool declining to answer reads as a bug rather than
  the feature it is) and the **exit codes** for all four entry points.
- **A preflight (F47).** Every entry point now checks the JVM, the jar, the index, `uv` and free disk
  **before** any long work, and refuses with exit **2** ("not configured"), never 1 ("your corpus has
  findings"). Previously: a missing JVM surfaced as `BaseX query failed: [WinError 2]` — blaming
  BaseX for a missing Java — and an unbuilt index printed a raw resource error that **never named the
  build script**, because the one line that does sits on a code path an unbuilt index cannot reach.
  The Java floor is read from the shipped jar's own bytecode level, not chosen.

### Fixed

- **A build that SUCCEEDED and was then reported as never built (F51).** `.basexhome` is a 0-byte
  marker in the BaseX archive, and it is what tells BaseX that a directory is its home. Without it
  BaseX relocates its whole home to `$HOME/basex` and builds there — successfully, and queryably —
  while every check looks in the tool's own directory and finds nothing. The first run would have
  been: run the multi-minute build, get told to run the build, forever. Caught before shipping;
  the marker is vendored and the preflight now names the relocation.
- **A crashed build reported as success (F48).** `build-effective.sh` swallowed its builder's exit
  code and wrote its manifest only at the end, so a crash left the *previous* run's manifest in place
  and coverage reconciled the new index against the old one — and could print `COVERAGE COMPLETE`.
  It now fails fast and deletes the manifest before rebuilding.
- **`ask.py` gave a traceback where it owed an answer.** An undeterminable freshness verdict escaped
  as a raw `EngineUnavailable` and exited **1**. "Could not check" is not "stale", and printing STALE
  would be an assertion where the honest answer is that nobody checked: it now reports
  **FRESHNESS UNKNOWN** and exits 4.
- **A guard blind to additions (F49).** The check that stops BaseX tests being orphaned pinned a
  *shrinking* file list but not a growing one — so adding a test file created 22 tests that passed by
  hand and were collected by nothing. A guard whose denominator comes from its own hand-maintained
  list can only ever be as complete as that list; it now discovers the files on disk.
- **A performance gate that could not tell a slow run from a sleeping machine (F50).**
  `time.perf_counter()` advances while Windows is suspended, so a sweep left running overnight
  charged the entire suspend to whichever item was being timed — reported as a **814× regression**
  with "investigate before shipping", on an item that re-timed *faster than its baseline*. A
  suspected regression is now re-timed once and reported only if it reproduces; one that **cannot**
  be re-timed is reported UNCONFIRMED and still fails, because "could not check" is not "not a
  regression".
- **The environment guard could not see a hardcoded path (F44).** v2.5.0's theme was "an executable
  that resolves its own environment instead of delegating", and the AST test that bans it detected
  only `os.environ` / `os.getenv` — so a hardcoded absolute path, the form that defect actually
  shipped in, sailed through. `docs/TRUST.md` named that test as what banned the shape, which means
  **the shipped trust document was overclaiming**. Both are corrected.
- **A corpus-wide crash check that could not see a crash.** `gates/corpus_sweep.py` decided whether a
  run had crashed by looking for a Python traceback in its output. A process killed by the OS loader
  never starts Python, so it prints nothing at all — and in a real run **173 of 242 invocations (71.5%)**
  died that way while the gate printed `CRASHES/HANGS: 0` and passed. The exit codes were in its own
  output, four lines above the verdict that ignored them. It now treats any code outside the documented
  `{0, 1, 3}` as a crash.
- **A coverage denominator taken from the current directory (F46).** `coverage.py`'s `--reference`
  and `--extensions` defaulted to `""`, and `Path("")` is `Path(".")` — so a bare run counted the
  directory it was standing in and published that as the denominator. It now refuses and writes
  nothing.

### Changed

- **POSIX: the 8 Linux CI failures are fixed** — all were test-side, none a library defect. Tests no
  longer patch the global `os` module to fake Windows (that steers `pathlib`'s flavour dispatch and
  raises `UnsupportedOperation`); `_paths._IS_WINDOWS` is a real seam instead. `x4validate --debug`
  now finds the log on POSIX. **Ubuntu deliberately stays informational, not a gate** — see below.
- **`docs/TRUST.md` gained a stated hole rather than a stronger claim.** The freshness fingerprint
  hashes each installed mod's manifest but not the *profile* manifest, so **enabling or disabling** a
  mod cannot move it and an artifact reports FRESH across that change. Installing or removing one
  does move it. Also newly stated: on Linux, four path lookups compare case-sensitively and **one of
  them fails silently**, which is why the Linux run stays informational — passing tests do not close
  that gap, and a green badge should not imply they did.
- **Java 17+** added to the prerequisite list, marked optional and scoped to BaseX.

### Verification

638 tests in the main suite plus 50 run as a subprocess in `tools/basex`, all 26 gates green, and the
suite verified on a genuinely cold checkout — one with every `X4_*` variable cleared and *proven*
unresolved first, because a partially-configured machine will otherwise masquerade as a clean one and
hand you a green that means nothing.

## v2.5.0

Three defects, one shape: **an executable that resolves its own environment instead of delegating to
the one resolver.** A probe of the whole population (9 CLIs, 26 gates, 7 BaseX scripts, 3 installers,
`bin/`, `scripts/`, 2 harnesses) found the CLIs already clean via `_paths` and the gates clean via
`gates/_env.py` — every defect was in something that hand-rolled its own lookup.

### Fixed

- **An unconfigured toolkit refuses instead of guessing (F39).** `_merge.REFERENCE` fell back to the
  *relative* path `reference`, so on a machine with nothing configured `x4validate <mod>` validated
  against a tree that does not exist, reported the whole base game missing **as findings about your
  mod**, and exited **1** — the code that means "your mod is broken". It now exits **2** and names
  `$X4_REFERENCE` and `.claude/x4-paths.env`.
  The refusal fires on *unresolved*, never on "you named a tree that happens not to exist".
- **Configuration is read through one door (F40).** `X4_NEXUS_KEY` placed in
  `.claude/x4-paths.env` — the placement `setup.sh` documents — was invisible, because `_nexus` read
  `os.environ` directly. `_effective` bound `X4_EFFECTIVE_DB` at *import* time into an argparse
  default while `gates/_env.py` resolved the same variable through `_paths`. New `_paths.value()` /
  `path_value()`; the split matters because the path form rewrites `/c/x` to `C:/x`, which would
  silently corrupt a credential. The Nexus key remains **optional** — callers catch and degrade to
  local facts, so offline work is unaffected.
- **Shipped scripts refuse instead of guessing (F41).** `scripts/generate-baseline.sh` defaulted its
  game directory to `$(pwd)`; a baseline is a *recovery* artifact, so that silently snapshotted
  whatever install you happened to be standing in. `bin/unpack-reference.sh` reported "not
  configured" as exit 1. Both exit **2** now.
- **24 tests that never ran on a fresh clone now run (F42).** Two test modules were skipped wholesale
  because the gate modules they import resolved configuration at import time — and a module-level
  skip collapses N tests into ONE line, so the summary read "586 passed, 11 skipped" while 24 tests
  had never been collected. Cold collection now equals warm: **619** either way.

### Added

- **`docs/TRUST.md`** — the trust register: every defect *shape* found in this toolkit, the measured
  cost when it happened, and the test or gate that now bans it. So "is this shape guarded?" is a
  lookup rather than a matter of confidence.
- **AST guard** (`tests/test_env_resolution_is_delegated.py`) banning direct `os.environ` reads
  outside `_paths`, with an `# env-ok:` escape hatch that cannot become a blanket amnesty.
- **A mechanical check that every entry point in `pyproject.toml`** carries the refusal wrapper, so a
  tenth CLI cannot be added without one.
- **A cold CLI matrix in `scripts/verify-cold.sh`** — every configuration-dependent executable is run
  on a genuinely cold checkout and must exit 2 with no traceback.

### Contract

Exit codes are uniform across the toolkit: **0** clean · **1** findings · **2** not configured or
usage error · **3** degraded · **5** stale artifact.

## v2.4.0

Four defects, all one shape: **a set was enumerated twice, by two different rules, and nothing
compared them.** Three were found by cross-tool disagreement rather than by a user; the fourth by
chasing three stray numbers left over from a coverage refresh.

### ⚠ BREAKING (output schema) — prop keys gain a collision suffix

When several sibling elements share a bracket discriminator, the 2nd and later now carry `#n`:
`licences.licence[generaluseequipment].factions`, then `licence[generaluseequipment#1]…`. The first
claimant keeps its original key, so **99.8% of prop keys are byte-identical** and stored baselines
mostly survive.

Why it had to move: the key was not unique, and `select value … where prop=?` with `fetchone()`
returned an arbitrary one of several genuinely different values. `faction/player` held **eight
distinct faction lists under one key**. MEASURED: 627 duplicate `(entity_id, prop)` groups → **0**;
1,153 rows changed prop and nothing else; 582,107 attr rows, 22,966 entities and the entire
entity+value multiset **unchanged**. `dev/_registry/CLAIMS.tsv` uses no bracket props, so no recorded
claim was invalidated. (BLIND-SPOTS **F33**, attr axis. The **entity** axis stays open on purpose —
`index/macros.xml` decides, not load order.)

### Fixed

- **x4eff was blind to the packed mini-DLC** (F34). `build-effective.py` walked `reference/`
  loose-only, so the index held **23 of 142 mini-DLC documents (16%)** — and the 23 only arrived
  because two unrelated mods nest patches under `extensions/ego_dlc_mini_0X/`. New
  `_effective.base_vpaths()` enumerates loose THEN packed, and the copy branch materializes packed
  members via `_cat` (without that half, the fix would have produced 119 *silent copy failures*).
  Now **142/142**. `reference_vpaths` is re-expressed as `base_vpaths` + an explicit `assets/` filter,
  proven set-equal beforehand: 4,002 and 7,551 vpaths, **0 added / 0 removed / 0 changed**.

- **Coverage took its denominator from the artifact it audited** (F35). `coverage.py` reconciled
  "documents produced" against "documents indexed" — both from the same build — so a vpath never
  enumerated could not fail, and it printed COVERAGE COMPLETE while missing 119 documents. The
  manifest now records the **scanned source set** (every configured source, its contributed count,
  loose or packed) and coverage fails when any contributed zero. The exclusions are carried in the
  JSON so a caller can render the caveat instead of reading a bare boolean.

- **The freshness fingerprint covered merge but not enumeration** (F36). `ENGINE_SOURCES` gained
  `_effective.py` and `_registry.py` — the modules deciding which documents, entities and *mods*
  exist. Near-miss that found it: F34's fix edited `reference_vpaths` and `claims_audit` returned
  21/21 green against a store built by the old enumeration. A dead duplicate of the list in
  `staleness.py` was deleted.

- **"The mod list" was two sets with no name for either** (F37). `_registry.mods(scope)` — `scope`
  positional and required, `"active"` (what the engine loads) vs `"installed"` (what is on disk).
  Across 13 call sites, 4 were wrong the same way. With one disabled mod present, x4eff carried its
  macros as live, x4compat listed it in 4 collision rows, and **Tier B would resolve a cross-mod
  selector against it and report OK — a false pass in the mode built to catch silent no-ops.**
  Verified per item: compat 445→445 rows, the same 4 rows minus that mod, **0 winner changes**;
  x4eff 10,799→10,789; x4raw unchanged (it correctly asks the other question); Tier B findings
  byte-identical across the 6 highest-overlap mods, so that repair is preventive, not corrective.

- **The variant-sibling check was blind to packed mods** (F22, re-scoped from "scope gap costing 0").
  All three of its enumerations walked loose files on disk. Of **378** variant-macro files it could
  reach **14 (3.7%)**. Now 378 are examined and it reports **3 real findings**. The identical defect
  was fixed in the function directly above it on 2026-07-26 and never carried across; an
  unresolvable owner is now `report.skip`, not silence.

- `build-effective.py` no longer emits **70+ spurious warning lines** per build (`_cat.mod_vfs`
  without `packed_only=True`, at a call site where the loose half is directly above).

### Added

- `tests/test_no_loose_only_reference_walk.py` — bans a loose-only `reference/` walk across
  `x4validate/`, `gates/` and `tools/basex/`. All 6 surviving sites were hand-verified before it was
  allowed to gate. Raises on a parse failure rather than skipping the file.
- `tests/test_mod_scope_is_explicit.py` — bans bare `scan_installed`, requires the scope to be a
  **literal** (a computed scope puts the choice back out of sight), and asserts both scopes remain in
  use so the API cannot become decorative.
- `tests/test_similarity_weights_pinned.py` — pins x4similar's weights equal to its oracle's
  hand-duplicated copy. The duplication is correct and stays; the silence about drift does not.
  Read by AST, never imported, so it works with no game installed.
- `gates/tool_properties.py` — mod-scope agreement: the store's mod set and x4eff's manifest must
  match **and** both be the ACTIVE set (two artifacts can agree while both modelling the wrong world).
- `gates/README.md` now documents `claims_audit.py`, which was undocumented since it was added.
- `tests/test_basex_tests_are_not_orphaned.py` — the 23 tests under `tools/basex/` PASSED but were
  never collected (`testpaths = ["tests"]`), so the suite reported green while saying nothing
  about `ask.py` (which gates every negative claim) and `staleness.py` (the freshness contract).
  They now run, and SKIP with a reason where the dev-only BaseX tooling is absent.
- `_effective.base_vpaths` is memoized (**2.25 s → 0.004 s** warm). Uncached it was re-derived per
  mod: the variant sweep over 115 mods went **47.6 s → 2.4 s**, same 3 findings.

### Changed

- `gates/tool_properties.py` re-pins F33's attr axis at **0** (a regression now fails); the entity
  axis stays pinned at 63 with its reason.
- `docs/BLIND-SPOTS.md`: F34/F35/F36/F37 registered; **F3's figures refreshed** (balance-relevant
  coverage **99.8%**, 3 of 1,916) and its scope confirmed rather than widened; F8 closed WONTFIX with
  a drift guard; F22 corrected with its wrong denominator called out; F33 split into a fixed axis and
  an open one.
- Suite **577 → 595**.

### Note on how these were found

None came from a user report. F34 and F35 came from two tools disagreeing about the same corpus; F37
came from F3's refreshed coverage leaving exactly three macros unaccounted for, and chasing those
three to ground. The alternative — writing "3 unexplained misses" into the register and moving on —
was one keystroke away, and would have left a false-pass path in Tier B in place.

Sixteen checking-step errors were made and caught during this work, and in **16 of 16 the checker was
wrong, not the finding** — including counting a *set* where a multiset was needed, comparing against
`kind='macro'` when ship macros are filed under `ship`, and running an AST scan on Python 3.10 that
could not parse a PEP 701 f-string. That base rate is the reason every number above was diffed per
item and predicted before it was measured.

---

## v2.3.0

### ⚠ BREAKING (output schema) — `Collision.winner` for SUBTREE rows

**This is a breaking change to tool OUTPUT shipped under a MINOR version bump.** SemVer is being
applied to the CLI *contract* (flags, exit codes), which is unchanged; the JSON payload moved. The
version number therefore does **not** signal this, and that is precisely why it is the first entry.

`Collision.winner` never meant one thing. For FULL-OVERRIDE / HARD / UNION-KEY it names the mod
whose value is **live**. For **SUBTREE it named the mod that did the WIPING** — which is not the
owner of the final value, because a third mod loading later can re-supply what was wiped.

    SUBTREE rows:   winner: "<mod>"   ->   winner: ""      + new key  wiped_by: "<mod>"

**Migration:**

| you want | before | now |
|---|---|---|
| the mod that wiped the subtree | `winner` | `wiped_by` |
| the mod whose value is actually live | `winner` (WRONG for SUBTREE) | `live_value_owner()` — returns `None` for SUBTREE / NAME-CLASH / SOFT, where naming one would be a guess |

`live_value_owner()` returning `None` is an answer, not a failure: it is the tool declining to
invent a winner it cannot know.

**Measured drift on the live modlist (115 mods), diffed PER ROW, not by totals:**

- 445 collision rows before, **445 after** — 0 added, 0 removed
- `winner` changed on **exactly 148 rows**, and all 148 are SUBTREE
- `detail` changed on **0** rows; every other field **0**
- a wipe later undone by another mod: **3 of 148 (2.0%)** — so the advisory is not noise

**Why it was worth breaking.** An inbound bug report claimed x4compat's winner was wrong for ~7
attributes across 2 Kha'ak ships and 1 engine. Re-measured: **x4compat was correct** and the report
had compared a SUBTREE `winner` against `x4effective`'s `origin` — two different questions. One
field answering two questions is a defect even when every value in it is right. See BLIND-SPOTS
**F25** (the report) and **F30** (the fix), and CLAUDE.md gotcha #18.

### Fixed

- **F27 — `md/` scripts were merged as if they were assets.** The engine registers MD scripts by
  **filename**, so a complete `<mdscript>` shipped at a vpath the base game already supplies is
  **inert** — it loads and its cues never take effect, with no error line anywhere. Our merge
  returned the mod's file; the engine was running vanilla's. `_merge` now returns mode
  `script(inert)` for that case, so `x4effective dump md/setup.xml` returns vanilla's **1,784-line**
  `Setup` instead of a mod's 44-line file. **Exactly 1 vpath of 299 changed hands.**

  *(Two line counts appear in the records and both are correct: the raw
  `reference\md\setup.xml` on disk is **1,795** lines; `dump` re-serialises through lxml and emits
  **1,784**. Neither is a typo for the other — MEASURED 2026-08-22.)*

  Mechanism **proven by controlled experiment**, not inferred: a uniquely-named mdscript in an
  overlay registers and runs, while a functionally identical script at the colliding vpath does not
  — same cue structure, same actions, same load position, both script `name=` differing from
  vanilla's, leaving the FILE PATH as the only variable.

  Scoped to `md/` **only**. `aiscripts/` is deliberately excluded: the corpus contains **zero**
  instances to verify a generalisation against, and `gates/tool_properties.py` carries a tripwire
  that fails the moment a real one appears.

- **F26 — `gates/cross_tool.py` verified one collision kind of five.** It checked FULL-OVERRIDE
  only: **14 of 445 rows (3.1%)**, with HARD / SUBTREE / UNION-KEY never checked against the store
  at all. Now asserts per kind, over the full population: FULL-OVERRIDE 14/14 · HARD 40/40 ·
  UNION-KEY 2/2 · SUBTREE 148/148 · NAME-CLASH 20/20 — **0 disagreements**.

  A single blanket assertion would have been wrong three different ways, and each wrong form was
  written and measured before being discarded — see F26 for the two checker bugs it produced.

- **F28 — a known-partial checker was presented as a completeness GATE.** `--entity ship:x
  --like ship:y` compares the `<ware>` wrapper only; the macro interior (physics, connections,
  engine/shield/turret slots, storage, hull, software, steering curves) was never examined, and a
  clean result read as "the ship is complete". It now declares the gap through the skip channel.

- **F29 — `_cat.mod_vfs` returned `{}` for a loose mod and said nothing.** Catalogs-only by nature,
  but silent about it: one corpus scan read **2,681 files instead of 4,401** and reported NOT FOUND.
  Now takes `packed_only=` and warns when a caller has not acknowledged the limit, enforced by an
  AST guard (`tests/test_no_packed_only_scan.py`) that accepts an inline `# packed-ok:` marker.

- **F31 — `<module group=>` was checked by nothing.** `modulegroups` was not an indexed registry and
  `module/@group` was unknown to `_refs`, so the engine rejected 3 references **43× per launch**
  while we reported the mod clean. Added as the 21st registry plus `check_module_groups`;
  engine-verified. Corpus: 146 groups, 22 references, **3 dangling in 1 of 115 mods**.

### Added

- `CHANGELOG.md` (this file).
- `tests/test_blind_spots_ids.py` — the register may not hand the same F-id to two findings. Written
  after two **concurrent sessions** each created a `## F30`, caught only by eye. Also pins the
  register's known bookkeeping gaps so new drift fails loudly.
- `_scan.iter_corpus_xml` + `CorpusScan` — one way to sweep every installed mod's XML, excluding
  `ego_dlc_*`, recording unreadable files, and **raising rather than rendering a zero** when nothing
  parsed. The hand-rolled form of this loop had been written **7 times** and was wrong in at least 3.
- **Summary-table integrity check** in `tests/test_blind_spots_ids.py`. A markdown row with fewer
  cells than the header does not fail to render — it renders **wrong**, shifting cells left and
  blanking the tail. That is how 15 register rows displayed an empty Status for weeks. The
  escape-aware cell splitter lives here once, with its own proven-to-fail test, so nobody hand-rolls
  `awk -F'|'` again (which cannot see a `\|` escape and calls a valid row malformed).
- **`check_store_key_uniqueness`** in `gates/tool_properties.py` — pins F33's measured duplicate-key
  counts (63 entity groups, 627 attr groups) so a known-open defect cannot grow in silence. A stale
  or absent store **SKIPS with a reason** rather than reading as a pass.

### Documentation

- `docs/BLIND-SPOTS.md`: every entry **re-verified against the code** and given an explicit STATE.
  The summary table was repaired in the same pass — it was missing **6 findings outright**
  (F21, F25, F26, F28, F29, F30) and **15 rows carried 4 of the header's 5 cells**, so their Status
  column rendered blank.
- **F33 (new)** — a non-unique key read with a singular read, measured and **left open**: 63
  duplicate `(kind,name)` entity groups (23 diverge) and 627 duplicate `(entity_id,prop)` groups
  (**201 diverge**). Identical pre- and post-rebuild, so pre-existing. **0 of 21** recorded claims
  resolve through an ambiguous key, so `claims_audit`'s 21/21 PASS is sound.
- **F32** — a dev-only *variant* is validated against a tree containing its own ops; documented as a
  scope limit and deliberately not "fixed", because the obvious relaxation trades a visible false
  alarm for an invisible false OK.

### Notes

- Suite **561 passing**. `gates/qa_sweep.py` 45 GREEN / 0 YELLOW / 0 RED.
- Effective store rebuilt 2026-08-22. Both freshness axes had moved (engine, via the F27 `_merge`
  change; content, via a redeployed overlay). Diffed per item against the pre-rebuild store:
  **0 entities added/removed, 0 attrs added/removed, 0 values changed, 0 origins changed** —
  confirming the F27 change has no blast radius outside `md/`.

## v2.2.1

**Nested cross-mod patches now apply through the owner's-file door too.**

`build_effective` handled `extensions/<owner>/<rel>` only when the *requested* vpath was
itself the nested path. Building the OWNER's plain vpath — which is what the effective
store does for every file — consulted only each overlay's bare `<vpath>`, so a later mod's
nested patch was invisible. Two doors to one logical file, two different answers: Tier B
(nested door) said a real add-on's 27 whole-file bullet overrides resolve; `x4effective`
(plain door) attributed every one of those values to the owner. The engine has ONE
document (F19, engine-proven), so the plain door was wrong.

Found because two of our own tools disagreed about the same value — cross-tool
disagreement is a defect signal, and this time it was real.

- The plain-door build now probes each later overlay for `extensions/<owner>/<vpath>`
  whenever an earlier overlay supplied the file, applying nested diffs (which are exempt
  from the bare-diff inert rule — the engine loads them) and nested full overrides.
- The store's touch map registers a nested patch under its owner's rel, so the patcher is
  handed to the merge and the phantom duplicate entity at the nested vpath disappears.
  **Single-level nesting only** — a double-nested file (a patch on another mod's patch
  file) keeps its old behavior, because the engine's handling of that shape is unproven
  and one installed mod ships both forms (rewriting it double-applied its ops).
- An applied nested `<remove>` now really removes: one installed add-on deletes a ship it
  rebalances elsewhere, and the old code resurrected it in the effective tree.

Measured impact on a real 114-mod install, full old-vs-new differential with **every**
changed row attributed and **0 unexplained**: 834 attribute values changed owner or value,
27 entity origins moved to the real winner, 60 phantom duplicates removed, 21 rows from
the now-honoured `<remove>`. `x4compat` was verified NOT to share the blindness (it
already aliases owner files under the nested key), and the engine oracles, noop/provenance/
consistency audits, determinism and the per-mod perf guard all pass unchanged.

Suite 392 → **397** (5 regression tests pin both doors agreeing, the inert bare-diff rule
surviving, and the nested `<remove>`).

## v2.2.0

**A guessed mod identity can no longer be laundered into a fact.** The registry stored a
fuzzy name match and a human-verified id in the *same field*, so a row whose identity was
invented by a search still read `settled: stable` / `classification: ready`. Measured on a
real 100-mod install: **3 of 100 identities were actually confirmed** — the other 97 were
guesses or blanks, all rendered with the same confidence as the 3.

Guessing is still fine. Guessing *silently* is not.

### Identity provenance

- **`auto.id_state` on every row** — `pinned` · `exact` · `guess` · `ambiguous` ·
  `unmatched` · `off-nexus` · `unsearched`. A stored id now always says how it was obtained.
- **Nothing derived from an untrusted id may reach a confident lane.** `classification` and
  `settled` are computed from upstream data fetched *using* the id, so they are only ever as
  good as the id. A guess is capped at the new `needs-confirmation` lane no matter how
  healthy the fetch looked — the reassuring part may describe somebody else's mod. This is
  the load-bearing invariant, and `gates/registry_provenance.py` proves it over the whole
  real registry (and was verified to FAIL when a single row is tampered).
- **Ambiguity is a real outcome.** `_match_strength` replaces a boolean "plausible?" with
  strong/weak/none, and the resolver now stores **no id at all** when two or more candidates
  are equally weak, instead of taking the first. The case that forced this: one mod name
  shares a single filler-ish token with several unrelated mods, and the true answer was
  unreachable by name search entirely.
- **Migration promotes nothing.** Every historical `auto (spot-check)` becomes `guess`, and
  a confident lane computed *before* the cap existed is withdrawn rather than grandfathered.
  Downgrading a real match costs one spot-check; upgrading a bad one re-creates the defect.

### Human-owned identity (permanent, never overwritten by a refresh)

- **`x4modlist resolve <id> <nexus_id> [--file <file_id>]`** now pins into `human:`.
- **`--file`: a mod is not always a page.** Plenty of add-ons ship as a *file* on someone
  else's mod page, where the page's version tracks a different release cadence entirely.
  Update-detection now compares the **file's** version and upload date (`_nexus.fetch_file`,
  which raises when the file is no longer listed — being superseded is information, not a
  lookup failure).
- **`x4modlist resolve <id> none`** and **`x4modlist source <id> steam:…|local|bundled:…|<url>`**
  record a mod with no Nexus page — Workshop items, bundled add-ons, your own overlays. They
  stop being searched, and move out of `untriaged` (which implied unfinished work) into a new
  `off-nexus` lane.

### Seeing it

- **`x4modlist verify`** — the burn-down list, with the denominator stated. Exit 1 while any
  identity is unconfirmed, so it works as a gate.
- **`verify --rescore`** — offline promotion of `guess` → `exact`, but **only** where the
  installed manifest name is *identical* to the stored upstream title. Containment is
  deliberately not enough: one real row's stored title merely *contains* the mod's name and
  is a different mod.
- The dashboard prints `N/M confirmed`, labels every row's provenance in its own lane table,
  and `refresh` refuses to end on a clean-sounding summary while unconfirmed rows remain.

Suite 379 → **392**. Gates 24 → **25**. QA sweep 38 → 41 cells.

## v2.1.1

**`--update` is no longer a 2-minute wait, and the whole toolkit's output has now been
verified exhaustively against independent ground truth.** 17 findings fixed across the
campaign, suite 363 → 379, 20 → 24 gates.

### Faster

- **`--update --xsd-fast`: ~112 s → ~2.5 s.** The gating class — `attribute X is required
  but missing`, the one reliable 9.0 migration signal — is a flat fact per element and
  needs no schema compile. It is now extracted by plain parsing (~0.05 s), scoped to the
  include-closure of the schema each document declares. Both passes still run by default,
  so nothing is lost; the flag skips only the slow advisory compile.
- The slow compile now **announces itself** instead of sitting silent for two minutes
  (a quiet spell that long reads as a hang), and only for the schemas that genuinely
  cost minutes.
- Corrected a false explanation in docs/comments: the compile cost was never "it includes
  the huge common.xsd" (that compiles in 0.03 s) — it is the recursive `actions` content
  model in `md.xsd`/`aiscripts.xsd`.

### Fixed

- **`--update` was blind to packed mods** in both the XSD and migration-heuristic passes —
  on a packed mod it examined zero script files and reported a clean 9.0 port. 33 installed
  packed mods were affected on the reference modlist.
- **9 false gating ERRORs demoted**: `md.xsd` does not model `<ammunition>` under
  `<create_ship>`, but the engine accepts it (verified against a live engine log). Now an
  advisory with the evidence inline, via an allowlist whose entry bar is engine evidence.
- A DLC-verdict message asserted "is installed" for extensions it never checked; it now
  looks in the game root and distinguishes installed-but-unreadable / not-installed /
  no-game-root.
- Two concurrent `x4effective build`s raced on one temp file and both died; the temp is
  now PID-qualified and the loser reports an actionable message. `build --kinds <typo>`
  no longer writes an empty store and exits 0.
- `x4compat` gained a **NAME-CLASH** collision class: two mods defining the same macro
  name in *different* files. X4 resolves macros by name through `index/macros.xml`, so
  only one definition is ever loaded and the other is dead content — structurally
  invisible to per-path collision scans. No load-order winner is claimed, because the
  index decides, not load order.

### Verified

- **`gates/xsd_fast_parity.py`** — the fast path proven set-equal to libxml2 over every
  script file in the corpus (it caught three would-be false-positive bugs during its own
  development; none reached a release).
- **Exhaustive output audits, shipped as gates**: every xref citation resolves to its
  exact line (packed and DLC included) · every stats value matches a fresh parse · every
  reported similar-pair recomputes to its printed score · planted mutations on real
  content are reported exactly (`tool_properties --exhaustive`, `similar_audit`,
  `diff_truth`).
- **`gates/perf_guard.py`** — per-mod runtime vs a machine-local baseline (aggregates
  hide regressions: a real 39×/51× slowdown summed to a 1.00× total), proven to fire.
- An old-vs-new output differential across the full modlist: zero unexplained changes.

### Docs

- **`docs/QA-PROCESS.md`** — the formal QA process for new tools, distilled from this
  campaign: contracts at design time, property tests, generative/perturbing techniques,
  differential and oracle testing, real-workflow use, the exhaustive output audit, and a
  stopping criterion that is a discovery curve rather than a feeling.

## v2.1.0

**If you run a total-conversion overhaul, take this update.** A `<replace>` whose selector resolved
to a document *root* was silently discarded — and reported as applied. On a 123-mod install that was
**858 mod operations** the tools never applied while telling you they had. Suite 316 → 333, six new
corpus gates, all four engine gates green.

### Fixed — the tools reported values the game does not use

- **`<replace sel="//macros">` was dropped.** That selector matches the document root, and a root
  has no parent to swap it through, so the operation was abandoned by a bare `continue` — while
  `apply_diff` still recorded `applied=True`. It is not an exotic form: it is the standard
  whole-file override idiom. One overhaul alone ships **848** of them, covering ships, weapons,
  engines, shields and bullets; two other mods contributed 10 more.
  **Consequence:** `x4effective`, `x4stats` and anything reading effective *values* reported the
  vanilla number wherever a mod had overridden it. After the fix, one overhaul went from owning
  **0** shield generators to **101** — matching exactly what its archive ships.
  *The engine was never affected*: it applies these operations and logs no complaint (it logs 467
  patch failures of two other shapes, and none of this one).
  **`x4compat` was proven unaffected** — same modlist before and after gives 419 rows, 0 added,
  0 removed, **0 winner changes** — because collision topology and load-order winners do not depend
  on whether a value landed.

- **A helper that could not apply an operation now says so.** `_do_replace` / `_do_remove` /
  `_do_add` return a reason, and `apply_diff` derives `AppliedOp.ok` from it instead of hard-coding
  `True`. That closes the *class*: any future unhandled case surfaces as "not applied, here's why"
  rather than a silent success.

- **`x4validate --file <missing>` crashed** with a raw lxml `OSError` traceback instead of telling
  you the path was wrong. A typo should not produce a stack trace.

- **`x4similar --threshold` accepted values outside 0–1.** A similarity score is a ratio;
  `--threshold -1` matched every ship against every other and emitted 1.7 MB of meaningless output.

- **`install.sh` could not auto-detect your game on Windows or macOS.** The Steam-root loop used
  an unquoted command substitution, so every path containing a space was word-split —
  `/c/Program Files (x86)/Steam` became three fragments. Only Linux (whose default paths have no
  spaces) ever worked, and the `libraryfolders.vdf` fallback was unreachable for the same reason.

- **`install.ps1` printed "install complete" after a failed unpack.** `$ErrorActionPreference` does
  not trap a native exit code, so a failing step was invisible. It now checks each step, reports
  which one failed, and exits non-zero. It also passes `CLAUDE_PROJECT_DIR` (the shell installer
  always did), so running it from a shell that already exports one no longer wires up the wrong
  folder — and the "bash not found" message now names the actual fix, since Git for Windows puts
  `bash.exe` in a directory it does not add to PATH by default.

- **`x4compat check` emitted its findings in a non-deterministic order.** Two identical runs
  produced the same 419 collisions in different orders, because a set's iteration order (which
  varies per process) leaked into the report. The *content* was never wrong — but it made every
  baseline diff noisy, which is how a real change hides. Now sorted at the source and at the
  reporting boundary.

- **No tool could tell you which build it was.** All eight CLIs now accept `--version`, and the
  package version is 2.1.0 (it had been left at 0.1.0).

### Added — gates that hunt the class, not the instance

That defect survived three sessions because nothing ever exercised the path. Six new contributor
gates now run against **your** installed modlist rather than fixtures:

- `noop_audit.py` — every operation of every installed mod applied against its real base document,
  comparing what the tool *reports* to what the tree *does*. **13,332 ops: 0 false OK, 0 false
  alarm.** Also surfaces mod XML that will not parse.
- `provenance_audit.py` — a changed value must name the mod that changed it. **3,709 values,
  917 mod-changed: 0 mis-attributed.**
- `consistency_audit.py` — the store, `build_effective` and `x4effective dump` are three paths to
  one truth and must agree. **120 sampled values: 0 disagreements.**
- `corpus_sweep.py` — `x4validate` over every installed mod, both tiers. **230 runs, 0 crashes.**
- `qa_sweep.py` — every CLI × every subcommand; targets discovered from your install, never named.
- `edge_sweep.py` — hostile inputs (empty mods, malformed manifests, SQL injection, path traversal,
  an unconfigured environment). The bar is *fail well*, and it needs no game paths.

- `determinism_audit.py` — the same command twice must give byte-identical output, and a rebuilt
  store must be logically identical. Without this, every recorded baseline is noise.
- `stress_sweep.py` — unseen mod corpora, chained tools, and deliberately pathological XML
  (entity bombs, XXE, 400-deep nesting, cyclic cross-mod patches, unicode with RTL overrides).

**Known characteristic, measured not guessed:** `apply_diff` is O(n²) in operations-per-file —
doubling the ops multiplies time by ~2.8→3.7×. It is not fixed here because severity is low by
measurement (the worst real file in a ~120-mod install is 1,443 ops, about 0.03 s — roughly 22×
headroom) and the fix would touch selector evaluation, the exact path this release exists to make
trustworthy.

The silent-swallow AST guard now also covers **control-flow** swallows in the diff mutators. The
pre-existing guard only inspected `except` handlers, so it could not have caught this one.

### Changed

- `schema_sweep` re-baselined to 168 pairs · 59 gating · 70 advisory · 42 mods flagged — attributed
  per mod (three added mods contribute 1 gating + 2 advisory; one removed mod took 2 + 9 with it),
  so the arithmetic closes rather than being asserted.

## v2.02

**If you use `--tier b` or the schema checks, take this update.** A red-team audit of the whole
toolkit (19 findings, every one dispositioned with measurements) found two defects that made
*clean results lie*, plus a class of cross-mod collisions nothing detected. All fixed here.
Suite 303 → 316, every fix mutation-verified, all four engine gates green (oracle 234/234).

### Fixed — results you could not trust

- **Schema checks looked up XSDs by basename in `libraries/` only.** Every schema whose XSD lives
  elsewhere (`index/`, `assets/`, cutscenes) was skipped as "not bundled" — all 31 such skips in a
  102-mod install were false. Schema coverage went 127 → 159 element-attribute pairs; the skips
  dropped to 1 (honest: a shader XSD the game truly does not ship).
- **Tier B blessed a cross-mod patch the engine never loads.** A `<diff>` at a BARE mirrored path
  targeting another MOD's file is never opened by X4 — only the nested
  `extensions/<owner>/<rel>` form works (engine-proven twice over: the same 7 files were absent
  from the engine's per-file log at the bare path and present after moving to the nested path,
  zero rejected ops). Tier B used to report such a mod **0 errors, exit 0**. Now it is an ERROR
  naming the owning mod and the exact path to move to, and the effective-tree model refuses the
  dead diff — `x4effective`/`x4stats` no longer show values the engine never sees (14 such lied
  values in the reference install, now 0). Language files (`t/*.xml`) are exempt: the engine
  always supplies the language tree.
- **`x4compat` could not analyze any cross-mod nested patch.** The owner extension was never
  supplied when building the comparison base, so every `extensions/<owner>/...` diff was silently
  discarded — 140 of 523 files in the reference install. They are analyzed now.

### New — collisions that were invisible

- **SUBTREE collisions.** A later-loading mod that `replace`s or `remove`s an element wipes every
  earlier mod's edit inside that subtree. Order-aware (a wipe loading *first* is the other mods'
  sel-check problem, not a collision), reported as hard-ish with the load-order caveat on every
  row. The reference install surfaced 153 — dominated by an overhaul wholesale-replacing engine
  macros another mod had tuned.
- **Duplicate-id detection now keys by `@id` first and spans whole documents.** A ware's `name=`
  is a `{page,text}` reference, not an identity — keying name-first hid an engine-confirmed
  same-id ware collision behind display-text noise, and per-anchor grouping missed same-id adds
  under different anchors. Mod-vs-BASE re-adds stay benign (measured: an engine-tolerated idiom
  used 476 times across major overhauls; the engine's own duplicate detector complained only
  about mod-vs-mod pairs).

### Fixed — found by red-teaming this release's own install flow (a v2.01 tradition)

- **The PowerShell installer wrote its config with a UTF-8 BOM** (Windows PowerShell 5.1's
  `-Encoding UTF8`), which bash reads as a command — so `bin/unpack-reference.sh`, the step that
  builds `reference/`, exited 127 on line 1 of `x4-paths.env`, and with `-Unpack` the installer
  still printed "install complete". The Python half tolerated the BOM, so the README's own verify
  step passed and the break only surfaced later. All installer writes are now BOM-free (and the
  bash-sourced config is written LF).
- **`x4modlist` no longer guesses CWD-relative paths.** Unconfigured, it either ingested whatever
  `content.xml` the working directory happened to hold, or reported "PRIMARY, 0 found" with
  exit 0 — "you have no mods" as a statement about your modlist instead of the missing setting.
  Every unresolved location is now a named refusal (exit 2, tells you the setting and points at
  `x4validate --paths`), the SECONDARY cross-check announces itself when skipped, and ingest
  prints the roots it scanned so "0 found" is auditable. Pinned by tests including a source-level
  guard on the fallback pattern itself.
- **The `global` install method no longer rewrites other people's skills/agents.** The
  `$CLAUDE_PROJECT_DIR` → `$X4_TOOLKIT` rewrite now touches only the files the installer itself
  just copied — a pre-existing agent in `~/.claude` that uses that variable on purpose is left
  alone.
- Windows Setup now leads with `powershell -ExecutionPolicy Bypass -File install.ps1` (a stock
  Windows install refuses a bare `.\install.ps1` before it runs anything) · `bin/xrcat` is pinned
  LF in `.gitattributes` and both OS branches now say "XRCatTool not found — set $XRCATTOOL"
  instead of a bare exec failure · `scripts/generate-baseline.sh` reads `x4-paths.env`
  (`X4_GAME`/`X4_PROFILE`) like everything else · the `x4-debug` and `x4-modlist-review` skills
  now give the configurable paths (and the Linux profile location) instead of hardcoded
  Windows ones · stale claims fixed (test count, "bundled" uv, "read automatically").

### Changed

- **Two advisory classes now gate as errors, on individually-verified evidence:** an enum value
  neither the XSD floor nor the whole effective tree defines (`schema-enum-undefined`), and an
  attribute on an element vanilla never pairs it with (`schema-dead-attr` — pair granularity
  matters: one real attribute on the wrong element was excusable at name level). MD-script
  unknown attributes stay advisory by design: spawn-time behavior cannot be settled statically,
  and a working released mod outranks a schema's opinion.
- **Version catalogs (`ext_vNNN.cat`): the skip is now known-correct.** Researched and
  engine-proven: X4 loads a version catalog only when the game version matches its `vNNN`
  *exactly* — a stale one is dead weight the engine ignores too. The skip warning now states the
  rule instead of guessing.
- Exit-code contract documented in the README (0 clean / 1 findings / 2 could-not-run /
  3 degraded), including why "the validator was blindfolded" is deliberately not "your mod is
  broken".

## v2.01

**If you installed v2.0, take this update.** v2.0 shipped an installer that wrote one set of
environment variables and Python tools that read a different set. They overlapped on exactly one
name (`X4_REFERENCE`), and nothing bridged them — so on the **`separate`** and **`global`**
layouts, two of the three the README documents, a *successful* install still left `--tier b`,
`x4compat`, `x4stats`, `x4similar`, `x4xref`, `x4modlist` and `x4effective` resolving against
CWD-relative paths. They did not error; they looked in the wrong place and reported finding
nothing. The README's claim that "nothing is hardcoded" was true of the shell half and false of
the Python half.

### Fixed — the installed toolkit is actually wired up

- **One resolver for every location** (`x4validate/_paths.py`), layered: real environment →
  `.claude/x4-paths.env` → an explicit (empty) local seam. Within each layer, every alias *and*
  derivation is tried before dropping to the next. Both naming schemes work; the installer's names
  (`X4_GAME`, `X4_EXTENSIONS`, `X4_PROFILE`, `X4_MODS`, `X4_REFERENCE`) are the ones to teach.
- **`x4validate --paths`** — prints what resolved and which config file was read, marking anything
  that does not exist. Run it first when a result looks impossible.
- **Bare `--debug` was Windows-only.** It built `~/Documents/Egosoft/X4/<id>/debug.txt` — the
  Windows layout, hardcoded — so it could never work on Linux (`~/.config/EgoSoft/X4/<id>`) or
  macOS, both of which this toolkit documents and supports.
- **Two silent degradations closed.** With the game root unresolved, the packed mini-DLC dropped
  from 8 DLC to 6 with nothing reported (every patch against Hyperion/Envoy content quietly
  became "cannot verify"); an unresolved registry read as "0 mods" rather than "not configured".
  Both now name the loss.
- **Git Bash paths now work.** `install.sh` detects Steam at `/c/Program Files (x86)/...` under
  Git Bash, and the config file has always promised either style is fine. Python cannot open
  `/c/...` on Windows — it becomes `\c\...`, which does not exist. So the *first command the
  README gives a Windows user* wrote a config the Python silently could not use. `/c/...` and
  `/mnt/c/...` are now translated on Windows and left untouched on Linux, where they are
  legitimate paths. **Found by red-teaming this release's own install flow.**
- **`X4_TOOLKIT` is no longer claimed to be set for you.** The docs said "(the installer does)";
  no installer ever did. Both installers now print the exact `setx` / `export` command at the end,
  and the READMEs give it instead of the claim. Without it the config file is found only by
  walking up from the current directory, which fails from the game folder — the case the docs
  themselves call out as common.
- **Nothing is guessed.** An unresolved location prints `(unresolved)`; the Steam workshop path is
  derived only when the install really has that shape, because a guessed path scans nothing and
  would report "no mods" as though it were a finding.

### Fixed — patch-time and runtime are two different trees

A `sel=` sees only what has loaded by your mod's turn; "does this id exist?" is answered after
*every* extension has loaded. Tier B used one tree for both, which is wrong in both directions —
measured on a real pair of installed mods: 3 false alarms in one direction, and one genuine
runtime defect missed in the other (a loadout referencing a connection that the winning component
does not have). The trees are now separate.

### Added — schema validation of merged data files (`--update`)

42 vanilla files under `libraries/` declare an XSD, and mods ship them as `<diff>`, so the
document that has to be valid is the *merged* one. This catches a class nothing else here can: a
patch whose selector matches and whose XML is well-formed, but which leaves the merged document
structurally broken — measured case, a mod that `<remove>`s `<production>` and orphans the
`<limits>` sibling 30 times.

It is **differential**, which is not a refinement but the only workable form: Egosoft's own
base+DLC data produces 66 errors against Egosoft's own bundled schemas, so an absolute check would
open with 66 false positives on a mod that changed nothing. Enumeration failures naming a race or
faction your modlist actually defines are suppressed and counted; one naming something nothing
defines is reported.

### Added — `gates/`, so the engine-fidelity numbers are reproducible

The four harnesses that measure this tool against the engine rather than against itself:
`oracle.py` (diff layer, 234/234 ops, 0 false OK), `oracle_index.py` (index layer, 12/12),
`regress.py`, `schema_sweep.py`. They take every input from your configuration and **skip with a
named reason (exit 2)** when something is missing, rather than running empty and printing like a
pass. You supply your own captured `debug.txt` via `$X4_ORACLE_LOG` — a real log names your mods
and paths, so none is shipped. What transfers is the bars, not the counts.

### Measured and deliberately NOT shipped

An MD ordering lint (flagging `signal_cue_instantly` to a cue that cannot be listening yet) was
built and dropped. It scored **0 false positives** across 1,094 vanilla call sites and 2,912 in
installed mods — and also **0 on a saved copy of the exact bug it was written for**. The real
defect reaches its target through a variable, from a second cue, so a rule keyed on a literal name
cannot see it; widening it to catch that fires 407 times in vanilla code that demonstrably works.
A rule with no demonstrated true positive is not quiet, it is inert. Recorded here because a
changelog that only ever adds is a sales sheet.

### Also

- 243 → **264** tests.
- Docs: the root README now shows how to actually invoke `x4validate` and links to the
  configuration model; `x4-paths.env.example` states that `$VAR` expands and that the file is
  parsed, never executed; `gates/README.md` says up front that it is for contributors only.
- Every number above traces to a gate run, not to a claim.

## v2.0

**The toolkit is no longer Windows-only, and no longer assumes one folder layout.** Plus two
community contributions, a round of safety-hook fixes found by red-teaming the install flow, and
a `debug.txt`-driven oracle that measured x4validate's verdicts against what the engine actually
did — closing every gap it found.

Major version because the install model changed: there are now three supported layouts and every
X4 location is configurable. Existing v1.x users keep working — the in-game layout is unchanged
and the hooks fall back to the old folder-name patterns when nothing is configured.

### Fixed — x4validate: validated against the ENGINE, not against itself

x4validate's model of the engine had never been checked against the engine's own output. This
built and ran that check: a `debug.txt`-driven oracle that measures x4validate's verdicts against
what X4 actually did, op-for-op. Every defect below was found and fixed using it — not by
inspection. Tests grew to 170, and none of them are cosmetic: each is a real-data regression test
for a real false result, several mutation-verified against the pre-fix code to confirm they
actually catch it.

**The tool was blind to 9 of the 10 mods with the errors that matter, and said "OK":**
- **Packed mods were never sel-checked, and reported a clean pass.** `iter_diff_files` walked
  `mod_dir.rglob("*.xml")`, which finds nothing inside a `.cat`/`.dat` archive — so the core
  sel-resolution check silently examined zero ops on any packed mod and printed
  `OK: no issues found`, exit 0. Sibling code (`iter_mod_xml_roots`) already read packed mods via
  the catalog reader; the core check simply never used it. Measured against a real debug.txt: **9
  of 10 mods with engine-rejected diff ops are packed** — this covered most of the real damage.
  Fixed by delegating to the packed-aware iterator; a mod where nothing could be read is now a
  WARN, never a silent pass.
- **`--debug` correlation missed 74% of the log, and 100% of the class it exists to catch.**
  `_debuglog.parse_debug` recognized 4 error shapes; a real 2463-line `debug.txt` showed it
  returning 645 (26%) — and **zero** of the 453 "diff op matched 0 or >1 nodes" lines, the
  RFC-5261 failure this tool is built around. Two new shapes fixed it (453/453 now captured),
  after finding two parser traps: the engine omits the file extension on these lines (`'…\
  material_library'`, not `…material_library.xml`), and the selector itself contains single
  quotes (`@id='ore'`) in 91% of real cases, which a naive `[^']*` capture group truncates.
- **Tier B applied the mod under test LAST — the engine does not.** Cross-mod validation merged
  every *other* installed mod, then applied the mod under test on top — a tree that never exists
  at the moment the engine actually applies that mod's patches. A node added by a
  **later-loading** mod looked present when it should not have. Fixed by truncating the merge at
  the mod's own load-order position. Measured on a real 192-op case: **27 false "OK" results,
  now 0** — full agreement with the engine, verified op-for-op.
- **One malformed overlay file crashed validation of every OTHER mod that touched the same
  path.** An `XMLSyntaxError` while reading an overlay escaped uncaught; exit code 1 made a crash
  indistinguishable from "found real errors." Now recorded and reported as a WARN naming the mod
  and file, never a crash.
- **A patch targeting an uninstalled mod is a designed no-op, not an error.** Compatibility-patch
  mods commonly ship nested cross-mod patches for dozens of optional targets. Enabling packed
  input surfaced this loudly (one mod went from 0 to 76 reported errors, 72 of them for targets
  simply not installed). Now reported as INFO, the same treatment already given to a
  false `if=` guard. **76 errors → 4 errors + 72 info** on that mod.
- **A DLC installed-but-never-unpacked-into-`reference/` reported a hard ERROR asserting content
  doesn't exist** — something the tool cannot actually know. Now reported as an honest
  "cannot verify" INFO naming the DLC, not a false ERROR.

**x4modlist auto-resolved ~10% of mods to the wrong Nexus page:** `_resolve_identity` accepted
the top search hit unconditionally. Measured on a live 101-mod registry: **7 of 69 resolved
entries pointed at an unrelated mod**, most flagged `settled: stable` — silently tracking someone
else's update history. Root cause: a multi-word Nexus search can return zero hits where a
single-word search returns several correct ones, and the empty-result fallback (drop the leading
word and retry) can land on an unrelated mod sharing only a generic word like "VRO" or "patch."
Now requires at least one shared identity-bearing token (generic modding filler excluded) before
accepting a match; an unresolvable mod is now flagged for manual review instead of silently
mis-tracked.

**x4effective and x4stats gave confident-looking answers that meant "nothing was checked":**
- `x4effective ls ship` printed `0 ship(s)` — reading as "this game has no ships," when the real
  issue is that `ship` isn't a stored entity kind (ships are `kind=macro`). Unknown kinds are now
  rejected with the actual list of valid kinds and a hint for the common ship/equipment aliases.
- x4stats compared ungrouped wares against an unrelated 1386-ware pool. A ware with no `group=`
  attribute (a paint mod, a cosmetic prop) was bucketed with every *other* ungrouped ware in the
  game and given a real-looking percentile against a wildly unrelated price distribution. Now
  reported as "not comparable," never a fabricated percentile.

If you maintain a fork or a similar tool: the single highest-leverage test you can add is this
pattern — ground truth from the engine's own log (`debug.txt`), not from your own model of the
engine, compared op-for-op, not file-for-file. No regressions found: all 10 previously-tracked
dev mods re-validated clean; the corrected oracle count is unchanged after every fix (234/234
ops, 100% agreement, 0 false OK, 0 unclassified, across all mods the reference log names — 9 of
them packed).

### Added — cross-platform support & guided installer (thanks @blablup, #2)
- **Runs on Linux, macOS and Windows (Git Bash).** `.claude/hooks/_x4-env.sh` is a shared
  resolver — **env var > `.claude/x4-paths.env` > default** — with case- and slash-insensitive
  matching that resolves symlinks and `..` via `realpath`, guarded so a Windows `C:\...` path is
  never mangled.
- **`install.sh` / `install.ps1` with three methods:** `in-game` (copy into the X4 folder — the
  original model), `separate` (toolkit in its own folder, pointed at the game via config), and
  `global` (skills/agents into `~/.claude` so the toolkit works across many mod repos). Paths are
  auto-detected from Steam's `libraryfolders.vdf` and overridable by flag.
- **`bin/xrcat`** — OS-aware XRCatTool wrapper (direct on Windows, Wine + `winepath` elsewhere).
  **`bin/unpack-reference.sh`** — config-driven text-only base+DLC unpack into `reference/`.
- **`.claude/x4-paths.env.example`** — one documented source of truth for every path.
  Keys: `X4_TOOLKIT`, `X4_GAME`, `X4_REFERENCE`, `X4_PROFILE`, `X4_DEBUGLOG`, `X4_MODS`,
  `X4_EXTENSIONS`, `XRCATTOOL`, `X4_APPMANIFEST`.
- All five hooks and `setup.sh` are now driven by the configured paths, with the old folder-name
  patterns kept as a backstop so protection still works out of the box.

### Added — engine knowledge (thanks @blablup, #1)
Field-tested MD↔Lua / FFI / UI / persistence patterns in `KNOWLEDGEBASE.md`: the nested-blackboard
`$`-prefix key trap, which container/trade-rule FFI functions actually work from UI-Lua (and the
global-vs-`C.` cdef variant trap), `$ship.$var` not surviving save/load, the
`a and nil or b` Lua footgun, and the kuertee UI-Extensions station-tab gotchas (13-column limit,
`is_valid_for` scope, slider/table widget behaviour).

### Fixed — safety hooks
- **The game-installation hard block never fired.** It used a PCRE lookahead `(?!\.claude)` under
  `grep -E`, which cannot match — so the rule has been silently dead in every release to date.
- **`in-game` layout hard-denied edits to your own mod sources.** With the toolkit installed into
  the game folder, `dev/`, `dist/` and `$X4_MODS` fell through every whitelist to the
  game-installation block. Now whitelisted.
- **`content.xml` edits were silently allowed inside the working dirs.** The manifest confirmation
  sat *below* the workspace whitelist; it now runs above it, so a manifest edit always confirms.
- **`check-reference-version.sh` hardcoded one Steam path** — now derived from `X4_GAME` with
  cross-platform fallbacks, so GOG / second-library / Linux users get the stale-reference warning.
- **`x4validate-on-edit.sh` was anchored to the author's personal folder** (`Modding/X4/dev/`), so
  the advertised auto-validate-on-edit hook could never fire for anyone else. Now it triggers on
  any diff XML outside `reference/`, resolving the mod root by walking up to `content.xml`.

- **Auto-backup was not anchored to the toolkit.** `backup-before-edit.sh` was the only hook that
  never adopted the shared path resolver; it used `${CLAUDE_PROJECT_DIR:-.}`, so with that variable
  unset it wrote backups into whatever directory the shell happened to be in. It now resolves
  through `_x4-env.sh`, and Windows-style paths are normalized before the copy.
- **`_x4-env.sh` no longer falls back to `$(pwd)` for the toolkit root.** It derives it from the
  hook's own location (`<toolkit>/.claude/hooks/` -> `<toolkit>`), so every hook stays anchored
  even when `CLAUDE_PROJECT_DIR` is absent.

### Added — the guards now have tests
- **`scripts/test-hooks.sh`** — feeds every hook synthetic tool-call JSON and asserts the decision
  it returns: **33 assertions** across both the in-game and separate layouts (deny `reference/`,
  `.cat`/`.dat`, base-game files; ask on `content.xml`, profile, deployed `extensions/`; allow
  `dev/`, `$X4_MODS`, toolkit dirs), plus backup creation, audit-log append, backup anchoring, and
  the stale-reference warning in both directions. Run it after any change to `.claude/hooks/`.

  **Four** separate safety features shipped silently inert before this release, and every one of
  them passed code review. A silent guard is worse than no guard, so the guards now have tests.

### Fixed — x4validate reported a false OK on attribute-add ops (found before the oracle existed)
- **`<add sel="…" type="@attr">value</add>` was silently ignored.** RFC 5261 §4.3 defines an
  attribute-add; X4 supports it and real mods use it. `_merge._do_add` never implemented it — the
  op fell through to the append-children branch, found no element children, mutated nothing, and
  still reported `OK 1 target(s)`. That is a **false OK on exactly the silent-no-op class this tool
  exists to catch**, and it survived earlier review because the op *looks* handled when you read
  the code. `_do_add` now sets the attribute, and `apply_diff` reports any `type=` it does not
  model rather than pretending to apply it. Four regression tests.

  Attribute ops matter for cross-mod work: when another mod owns a sibling attribute, a whole-node
  `<replace>` bakes in whatever value was winning when you wrote the patch, while
  `<remove sel="…/@x"/>` + `<add type="@value">` leaves it alone.

### Changed
- `/x4-debug` no longer ships a hardcoded workshop id as a "known-benign" suppression rule — it
  now describes the *class* (an extension id not in your installed set) and warns against
  suppressing ids you haven't confirmed absent. It also documents two engine behaviours that look
  like regressions but aren't: an upstream mod's failed `<replace>` still logs even when a
  load-last overlay supplies the value, and a `sel=` matching multiple nodes is a silent no-op.
- Log comparison guidance: check whether each log is a new game or a save load before comparing
  error counts — they aren't comparable across that boundary.

### Verification
`bash scripts/test-hooks.sh` — **33/33** assertions across both install layouts. Python suite: 170.

## v1.4

Engine-fidelity release. Three ways X4's patch semantics differ from a naive XML merge were
found by dogfooding against a 95-mod install — each one had been silently producing either a
false alarm or, worse, a false OK. Plus the effective-values browser (`x4effective`) and the
version-diff tool (`x4diff`).

### Fixed — three silent-no-op / false-alarm classes

- **A `sel=` matching MULTIPLE nodes is a silent no-op, and was never flagged.** RFC 5261
  requires `sel` to select exactly one node; X4 enforces it by logging
  `Multiple matching nodes for path '<sel>' ... Skipping node` and applying **nothing**. The
  patch looks correct, validates clean, and does nothing. **236 such ops were being skipped
  across one real modlist** — including 11 belonging to a mod whose entire purpose was the
  behaviour those ops implement. `x4validate` now reports this as an ERROR, and the merge
  engine models the skip instead of applying the op to every match (which used to build an
  effective tree the game never has).

- **`if=` guards were unknown to the checker.** `if=` is the idiom for targeting content that
  may not be installed. The merge engine honoured it; the sel-checker did not know it existed
  and reported every deliberately-guarded op as a hard ERROR. Guards are now evaluated first,
  exactly as the engine does: a false guard reports INFO ("guarded no-op") and short-circuits
  before `sel=` is parsed. A guard that *passes* while its `sel=` still misses remains an
  ERROR — the author asserted that target exists.

- **Cross-mod patches at `extensions/<target>/<rel>` reported "no base game file".** That path
  is owned by `<target>`, not by the base game, so the base was never found and the patch's
  selectors went unchecked — while the engine applied them fine. The merge engine now resolves
  the owning mod and layers other mods' nested patches on top.

### Added

- **`--tier b`** — previously a stub that printed a warning and was never passed to the
  validator. It now merges the **installed extension set in load order**, so cross-mod patches
  resolve for real. It also catches the reverse failure Tier A silently passes: content another
  mod has **removed**. Real case that motivated it — one mod's `index/macros.xml` `<remove>`s a
  vanilla macro and never re-adds it, orphaning it for six other mods and generating 415 engine
  errors; Tier A reported that macro as defined. The mod under test is auto-excluded by both
  folder name and `content.xml` id (a dev folder is usually also deployed, and merging that copy
  would pre-apply its own ops and mask real misses). ~8s over 93 extensions.
  Ordering follows the community convention (alphabetical, dependencies first) — advisory.

- **`x4effective`** — the "xEdit for X4" effective-values browser. Every final value with
  per-attribute provenance (`base → modA replace-attr:12 → modB`), backed by SQLite.
  Subcommands: `build`, `ls`, `show`, `attr`, `who-sets`, `diff-mod`, `dump`, `sql`.
  Answers "show me all missiles and their damage" and "who set this, and what did it override".

- **`x4diff`** — semantic XML diff between two versions of a mod, with multi-baseline support
  (`--overlay`). Built for separating *your* edits from an author's when recovering personal
  modifications made to an older release.

- **`_provenance.py`** — provenance capture threaded through the merge (`Origin`/`Recorder`),
  keyed by live element objects rather than XPath strings, since paths shift as the tree mutates.

- **`_debuglog.py` / `--debug`** — folds the engine's own `debug.txt` errors for the mod under
  test into the report, and gates on them. The engine is the authority; static checks are not.

- **`_exprlint.py`** — always-on heuristic lint for MD/aiscript expression grammar.

### Changed

- `collect_macro_defs` now builds the **effective** `index/macros.xml` via the merge engine
  instead of unioning each directory's file. The old approach could see neither `<diff>`/
  `<remove>` ops nor packed `.cat` mods, so it reported macros as defined after they had been
  deleted from the effective index.
- `_merge.Config` gained an `overlays` field, threaded automatically into every
  `build_effective()` call, so Tier B needs no signature changes across the checkers.

### Tests

125 → 152. New `tests/test_tierb.py` encodes all three engine-fidelity rules plus Tier B
overlay resolution as permanent regressions.

## v1.3

New cross-mod interaction suite — the toolkit can now analyze how a mod behaves against your
installed set, not just validate it in isolation.

### Added
- **`x4compat`** — detects how installed mods collide over the effective (base+DLC+mods) tree:
  HARD (two mods replace/remove the same node — one silently loses), UNION-KEY (two mods define
  the same registry entry id), FULL-OVERRIDE (two full-file overrides of one asset), SOFT (benign
  coexisting adds). Dispatches by actual merge semantics, not raw file overlap — a shared-registry
  overlap (`t/`, `libraries/`, `index/`) isn't automatically a conflict. Candidate mode
  (`check <mod-folder>`) answers "what would collide if I added this?" before installing.
- **`_cat.py`** — an independent `.cat`/`.dat` reader so PACKED mods are finally visible to the
  whole toolkit (previously only loose-file mods could be analyzed). MD5-verified, reads both
  `ext_*` and `subst_*` catalogs, case-insensitive virtual-path lookup. Wired into the merge
  engine as a loose-over-packed fallback.
- **`x4xref`** — a who-calls / who-listens / cue cross-index over every MD/aiscript in base+DLC+
  installed mods. Answers behavioral-interaction questions ("who else touches this event/action")
  in one query instead of many exploratory greps — the useful tokens for this class of question
  often share no keyword with the concept you're actually asking about.
- **`x4stats`** — advisory numeric comparison of a candidate mod's wares/macros against the
  effective tree's same-group peers (so an installed overhaul's rescaled values are the baseline
  you're compared against, not vanilla's). Grounds a balance discussion; does not settle one.
- **`x4similar`** — advisory fuzzy same-ship detection: flags a mod's ship as a probable
  near-duplicate of one already present under a DIFFERENT id/name (an overhaul's rebalanced
  reskin, or two mods independently adding "the same" ship). Hard-filtered by ship class+purpose;
  requires 4+ shared numeric stats to avoid coincidental matches between unrelated small ships.
- **`/x4-mod-interaction` skill** orchestrating all four tools plus README/Nexus context into a
  single interaction brief with per-claim confidence — never loads a whole mod's XML into context.

## v1.2

Fixes and redesign from continued dogfooding on a real 30+ mod install.

### Fixed
- **`libraries/*.xml` (ships, wares, loadouts, ...) are now correctly UNIONED**, not
  full-file-overridden, matching the engine. Every DLC ships its own full `libraries/`
  files; treating them as full overrides silently clobbered base-game entries out of the
  effective tree, producing phantom "sel matched nothing" errors for any mod patching a
  base-game ship/ware/loadout. (`index/` and `t/` were already unioned — `libraries/` is
  now folded into the same additive-merge path.)
- **Mod-authored index entries (`index/macros.xml` etc.) no longer double their own
  path prefix.** Mods conventionally write index values game-root-relative
  (`extensions\<mod>\assets\...`); resolving that literally against the mod root (which
  already IS that `extensions\<mod>\` directory) produced a spurious "file missing".
  The leading `extensions\<mod>\` is now stripped before resolving.
- **Loadout connection-checking no longer flags `path=".."`/`"."` as a missing
  connection name.** Vanilla `<groups>` entries use `path=".."` to mean "the ship root,"
  not a connection — that's not a checkable name.

### Changed — `x4modlist` redesign
- **Installed-folder scan is now the PRIMARY source of truth**, not the profile
  `content.xml`. A registry built content.xml-first drifts from reality over time (see
  "content.xml ≠ what's installed" in KNOWLEDGEBASE.md) — `x4modlist ingest` now scans
  the real extension folders first and treats that as authoritative; content.xml is
  ingested as a secondary backfill/cross-check pass so a historically-tracked mod isn't
  silently lost, surfaced in its own "not currently installed" view instead.
- **Identity resolution now prefers a mod's own manifest name** over a humanized
  folder/id guess, with fallbacks for common author-prefix (`"kuertee: X"` → `"X"`) and
  suffix-qualifier (`"X - Divinity Edition"` / `"X VRO"` → `"X"`) naming patterns —
  substantially fewer wrong Nexus-search top hits.
- **New `custom-local` classification lane**: a mod whose upstream Nexus status is
  `removed`/`hidden` is no longer automatically classified `drop` if the user has
  marked it `custom_edited` — a locally-maintained fork/port isn't "abandoned" just
  because the author's page is temporarily unavailable.

## v1.1

Bug fixes from a cold-start install red-team (a no-context agent following only the shipped
docs), which surfaced gaps invisible from the inside.

### Fixed
- **`x4validate` no longer false-passes when the reference tree is missing or empty.** It used
  to report `OK: no issues found` (exit 0) even on a `sel=` that matched nothing, because there
  was nothing to match against — the exact silent-no-op the tool exists to catch, in the tool
  itself. It now errors and exits non-zero until a real `reference\` is present. (+ regression
  tests.)
- **`protect-files` hook now actually fires.** The game-install block used a PCRE lookahead
  invalid under `grep -E`, so it never triggered; path matching was also tied to one personal
  workspace layout. Reworked to anchor on `$CLAUDE_PROJECT_DIR`, so the `reference\` read-only
  block and the workspace whitelist work at any install location.

### Changed
- `CLAUDE.md` reconciled to the "workspace under the project root" layout and to the
  protections that actually exist; removed a hardcoded x4validate test count from docs.

## v1.0

Initial release. An AI-assisted X4: Foundations modding environment for Claude Code,
distilled from hands-on mod development on X4 v9.0.

### Included
- **~500-line `KNOWLEDGEBASE.md`** — X4 XML schema patterns, diff-patch idioms, the
  extension merge/load-order model, the 7.x→9.0 version migration map, a mechanics
  interlock map, and tool notes. Auto-loaded every session.
- **`CLAUDE.md`** — the modding workflow: diff-patch-first, confidence levels,
  "vanilla as frame of reference," native-engine-solutions-first, and the cognitive
  co-pilot principle.
- **`x4validate`** (bundled, Python/uv/lxml) — cross-file validator: checks every diff
  `sel=` resolves against the real base+DLC merged tree, that ware/macro/`{page,t}`
  references resolve, and completeness of new content vs a vanilla analogue. Also ships
  `x4modlist` (mod-registry triage via the Nexus API) and an XSD-based 7.x→9.0 migration
  checker.
- **4 skills** — `/x4-debug`, `/x4-modlist-review`, `/x4-scaffold`, `/x4-update-mod`.
- **2 subagents** — `cross-file-impact`, `mod-research`.
- **Safety hooks** — protect-bash, protect-files, auto-backup-with-audit-log,
  reference-version check (SessionStart), and advisory x4validate-on-edit.
- **`scripts/generate-baseline.sh`** — capture a known-good baseline (game version,
  installed-mod hashes, a normalized debug.txt error fingerprint) to diff against later.
- **`setup.sh`** — checks prerequisites (jq, uv/Python), wires up x4validate, and
  personalizes local paths.

### Notes
- Ships **no** Egosoft game data. `reference\` is unpacked locally from your own copy.
- All Nexus access is via the official API with **your own** API key (`X4_NEXUS_KEY`);
  no key is bundled.
