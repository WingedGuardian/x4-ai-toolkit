# ADAPTING.md -- connecting an agent the X4 AI Assistant Toolkit does not support yet

**Read this if you are an AI agent and this toolkit ships no adapter for you.**
`bash install.sh --help` (in Windows PowerShell:
`powershell -ExecutionPolicy Bypass -File .\install.ps1 -Help`) lists the agents it has adapters
for; the two worked examples below are Claude Code (the guards ARE its hooks) and OpenAI Codex.

Until you have an adapter that passes the proof in section 5, **none of the toolkit's guards
protect anything you do.** The only protection left is the operating system's, and only once
it has been applied: `python scripts/x4refguard.py apply` (it shows the folder and a file count
and asks first; `--yes` confirms once your user has agreed) puts a deny on the `reference/` tree
that blocks deleting, renaming, overwriting and creating files in it (Windows; Linux and macOS
get a best-effort equivalent), while reads still work. `bin/unpack-reference.sh` applies it
after an unpack; the installers do not. `python scripts/x4doctor.py` shows whether it is on
(row `layer2.reference`). Tell your user that, in those words, before you start modding -- and
do not tell them the guards are live because the files are present, or that `reference/` is
protected before `x4doctor` says so.

What an adapter is: a small program your agent runs as its "before a tool call" hook. It
TRANSLATES your agent's payload into one question for the toolkit's guards, and TRANSLATES the
answer back into the shape your agent obeys. It decides nothing itself. The guards are the
policy; your adapter only carries it.

Evidence tiers used below: **MEASURED** (someone ran it and counted), **READ** (it is in the
code, file named), **INFERRED** (reasoned, not run). Write your own findings the same way.

## 0. Before you start

- You need a guard tree: `.claude/hooks/`, `.codex/hooks/` or `.opencode/hooks/` (each carries
  the same guards and an `x4guard.py` whose `check` answers), from an install that included
  Claude Code, Codex or OpenCode (`--agent all` includes all three), from the release zip, or
  from a git clone. An install made with `--agent generic` ships no guard tree at all (READ:
  `install.sh`, `X4_AGENT_ITEMS_generic`). The 4.0 neutral runtime is in
  `tools/x4_harness/` and `tools/modding_harness/`; it needs none of the named
  agent directories. `python tools/agent-harness.py capabilities` describes its
  contract, `check` accepts Action v1 JSON on stdin, and `coverage --session-id ID`
  reports private process receipts. An integrating host must honor refusals before
  execution; these commands do not establish interception or host trust.

  `save-list` lists permanent neutral transactions. `save-restore ID --to DIR`
  restores to a new directory by default; `--in-place` preserves the current save
  first and applies the same bulk approval policy. A bulk helper needs canonical
  paths, logical count and command hash from a qualified human reader; an unknown
  host cannot grant that approval. Both commands use the configured save store.
- `x4guard conformance` (section 5) needs more than `check` does: the toolkit's `scripts/`
  folder, a **`.claude/hooks/`** folder at the toolkit root (the reference guards it replays
  against, and what `{HOOKS}` names in a profile), Git Bash (on Windows) and `jq`. So run it from
  an install that includes the Claude target (`--agent all`, or a list naming `claude`) or from a
  git clone: in a `--agent codex` or `--agent opencode` install it refuses with exit 2
  (`.claude/hooks is missing`) whichever guard tree's `x4guard.py` you start it from. Every
  install carries `scripts/`; neither installer provides Git Bash or `jq` -- they are yours to
  install (`setup.sh` warns when `jq` is missing). Check the two programs first:
  `command -v bash jq` (bash) or `Get-Command bash, jq`
  (PowerShell) must name both. If one is missing, the run stops with exit 2 before anything is
  replayed (both are always required, whatever the profile's `requires` lists). On Windows, make sure the `bash` found is Git Bash: a
  `bash.exe` in `C:\Windows\System32` is the WSL launcher, which fails without a distribution.
- Python 3.10 or newer for the guard front door; it is stdlib only.
- Never edit a guard, the test corpus or another agent's adapter to make yours pass (section 6).

## 1. Self-assessment

Fill this capability report for YOUR agent first. Every row carries its evidence tier; a row
you could only guess is marked INFERRED or ASSUMED and becomes a gap you report. The same
fields are used in section 7, so a submission is this block, filled in.

```
AGENT: name of the agent
VERSION: exact version you measured
OPERATING SYSTEM: and the shell your agent's shell tool really executes there
HOOK EVENTS: which events exist; which one runs BEFORE a tool call
PAYLOAD CAPTURE: fixtures captured from a LIVE run (never hand-written), and where they are stored
TOOLS THAT WRITE: every tool that can change a file or run a command; which of them fire the hook
SHELL ROUTING: which shell executes the shell tool, per OS (a tool called "Bash" may run PowerShell)
VERDICT SHAPES HONOURED: deny / ask / add-context -- each one MEASURED as honoured or not
FAIL MODE: the five rows of the table below, each "blocked" or "ran", MEASURED
HOOK TIMEOUT: the agent's hook timeout in seconds, READ from its docs and bracketed by a measurement
HOOK APPROVAL: can a changed hook definition silently stop running until someone re-approves it?
CONTEXT CAP: how much hook text your agent shows the model before truncating it
NATIVE POLICY LAYER: any built-in permission rules you can also use (deny-lists, sandboxes)
INSTRUCTION FILE AND BYTE CAP: the file your agent reads at start, and where it truncates it
EVIDENCE: the tier of every row above, and the commands or transcripts behind each MEASURED one
```

**A row whose answer is "none" is still filled in, with its tier and its source.** For example:
`INSTRUCTION FILE AND BYTE CAP: none (READ: the agent's hook docs say it has no instruction
file)`, or `VERDICT SHAPES HONOURED: deny MEASURED (row a below); ask: none, the docs say there
is no ask; add-context MEASURED`. Never leave a row blank: a blank row cannot be told apart from
one you skipped. A "none" that matters for safety (HOOK APPROVAL: "never re-approved") is worth
one measurement when it is cheap: change the hook definition, restart, and see that it still
runs.
Two examples of why each row matters (MEASURED on Codex 0.160.0, 2026-09-30, in
`docs/superpowers/measurements/2026-09-30-codex-spike.md`): Codex labels PowerShell commands
"Bash" on Windows, so an adapter that judged them as bash let a PowerShell write into
`reference/` through; and Codex ignores an "ask" answer entirely and runs the call.

### Does your hook system fail open or closed?

This is the most important row, and it must be MEASURED, never read from documentation. Make a
scratch folder with one decoy file in it. Install, one at a time, a hook that does each thing
below, and each time ask your agent to overwrite the decoy:

| row | the hook... | if the decoy was overwritten |
|---|---|---|
| a | prints your agent's documented DENY answer and exits 0 (the control) | STOP: nothing in this document can protect you; your agent does not honour a deny |
| b | exits 2 and writes a reason to stderr | exit codes fail open |
| c | crashes (an uncaught exception, any non-zero exit) | crashes fail open |
| d | sleeps longer than your agent's hook timeout | timeouts fail open |
| e | prints garbage (not your agent's answer format) | unreadable output fails open |

Record "blocked" or "ran" for every row. Row (a) must say "blocked" or there is no point
continuing. **Any other "ran" means your agent fails open on that path**, and your adapter must
never take it: every failure inside the adapter has to end as a printed deny, exit 0, in time.
If your adapter itself can crash (it can), put a small fail-closed WRAPPER in front of it that
turns anything unexpected into a deny -- the Codex example in section 3 does exactly that.

**Your agent's hook timeout.** READ the number from your agent's own docs first; you need it for
row (d) and for the time budget in section 2. Then confirm it with two hooks, since docs can be
wrong: one that sleeps 3 seconds LESS than the documented timeout and then prints the deny (it
must be "blocked", so the timeout is at least that long), and row (d), which sleeps 10 seconds
MORE (it shows what happens past it). If the first one is not blocked, the real timeout is shorter
than the docs say: lower the sleep until it is blocked, and use that measured number.

**How your agent launches the hook command.** Find out from the agent's docs (or its code) whether
it runs the hook command through a shell, and which one, because that decides how a path with
spaces must be quoted. A command run through a shell on Windows usually goes to `cmd.exe` (Python's
`subprocess.run(cmd, shell=True)` does, and so does the toy agent): there, only DOUBLE quotes
group a path. MEASURED 2026-10-05 on Windows 11, a script in a folder with a space, through
`shell=True`: `"<python>" "<script>"` ran; the same with single quotes failed ("The filename,
directory name, or volume label syntax is incorrect"); unquoted, Python could not open the
file. On Linux and macOS the shell is usually `/bin/sh`, where both quote styles work. If a hook
silently never runs, check the quoting before anything else -- and remember that a hook that
cannot start is one more fail-open path on many agents.

## 2. The guard contract

The toolkit's front door is `x4guard.py`, in `.claude/hooks/` (or `.codex/hooks/`,
`.opencode/hooks/`). It takes
ARGUMENTS, not stdin, and there is no agent option -- describe the action, not yourself:

```
python .claude/hooks/x4guard.py check --kind shell --shell bash --command "<command>"
python .claude/hooks/x4guard.py check --kind shell --shell powershell --command "<command>"
python .claude/hooks/x4guard.py check --kind write --path "<file>"
python .claude/hooks/x4guard.py check --kind write --path "<file>" --content "<whole new file>"
python .claude/hooks/x4guard.py check --kind delete --path "<file>"
```

It prints ONE line of JSON and exits 0 for every verdict (READ: `agent/guards/claude-hooks/x4guard.py`):

```json
{"v": 1, "decision": "allow|advise|ask|deny", "reason": "...", "context": "...", "inert": false, "guards": ["..."]}
```

Exit 2 means a usage error (a wrong option). The rules for calling it:

- **Run it in the call's working directory** -- the directory your agent's payload says the call
  runs in (often a field named `cwd` or `workdir`), NOT the directory your hook process was
  started in; many agents start hooks somewhere else. A relative path, and a relative operand
  inside a shell command, are judged from x4guard's own working directory (READ: x4guard
  resolves `--path` with `abspath` and puts `os.getcwd()` into the shell payload). Set the
  subprocess `cwd`; do not rewrite the paths yourself.
- **`--shell` names the shell that will EXECUTE the command**, not what your agent calls the
  tool. If you cannot tell, measure it (section 1, SHELL ROUTING).
- **`--kind write`** for any tool that creates a file OR changes an existing one (an edit is a
  write).
- **Pass the written TEXT for a Claude Code settings file** (a path matching
  `.claude/settings*.json`, any case and slash). Without it every such write is DENIED: an `env`
  block there reaches every hook, so the guard must see whether it would set X4_GUARD. A tool that
  writes the WHOLE file passes it as `--content "<the new file>"` (judged exactly as Claude Code's
  Write); a patch or any partial text passes it as `--command "<the patch>"` (judged more
  strictly: a patch cannot prove an existing key is removed); add `--patch` when that text IS an
  apply_patch, so an add-only patch is judged on the file it writes (without the flag the text
  is never read as a patch). Never `--content` with `--command`. The shipped adapters
  pass it only for settings files (READ: `agent/guards/adapters/codex.py`, `opencode.py`); a
  large file through the command line can exceed the OS argument limit.
- **`--kind delete`** for anything that removes or moves a file away; x4guard judges it as the
  stricter of a write and an `rm -rf` of that path. A move is a delete of the source plus a
  write of the destination. A tool that touches several files is several checks; the worst
  verdict wins (deny > ask > advise > allow). `--kind delete` is for a DEDICATED delete or move
  tool. If your agent has none, and deletes only through its shell tool (`rm`, `mv`,
  `Remove-Item`), you need nothing extra: send the whole command as `--kind shell`, and the shell
  guard judges the deletion inside it (READ: the corpus in `scripts/test-hooks.sh` has `rm` and
  `Remove-Item` cases, and conformance replays them as `shell-bash` and `shell-powershell`).
- **`inert: true` means the guard could not run** (no bash, a timeout, a missing script). It
  always arrives as `decision: "deny"`. Render it as a deny -- or as a question to the user if
  your agent can MEASURABLY ask -- and never as an allow.
- **Treat everything unexpected as an inert deny**: a non-zero exit, empty output, output that is
  not JSON, a `v` other than 1, or a timeout. The same for a tool name, field or shell your
  adapter does not recognise -- unless your capability report MEASURED that tool as unable to
  write files or run commands (then let it through, as Claude Code lets unhooked tools through).
- **Time budget.** See "Choosing the time budget" below: get it wrong and your adapter turns
  real verdicts into inert denies under load.
- **Finding x4guard.py.** Locate it relative to your adapter's own file, or from a path the
  install configured. Do not build it from `X4_TOOLKIT` alone: under `x4guard conformance` that
  variable names a SANDBOX toolkit with an empty hooks folder, and in an install it may be unset.
  `X4_GUARD_PY`, which the section 4 example profile sets, is NOT read by the toolkit: it is a
  convention between a profile and its own adapter (the committed toy adapter reads it first,
  then falls back). Under conformance, read it from the profile's `env`; in a live install, fall
  back to the path relative to your adapter's file. If neither resolves, answer with an inert
  deny that names the missing path -- never an allow.
- **`X4_GUARD=off`** is the user's launch-time escape hatch. x4guard handles it; your adapter
  does nothing special. Under it a deny or an ask becomes an advisory naming what it would have
  been, and an allow comes back as an advisory carrying the GUARDS OFF note. Two answers are NOT
  relaxed: a guard that could not check the call still asks, and an inert verdict stays an inert
  deny (a guard that could not run judged nothing to relax).
- **`check` has no side effects**: it never makes the backup that the Claude hooks make before an
  edit. An adapter that wants backups runs `.claude/hooks/backup-before-edit.sh` itself after an
  allow, as the Codex adapter does.
- **Context size.** Bound any `reason` or `context` you inject to `X4_HOOK_MAX_CHARS` characters,
  keeping the FIRST part: the directive comes first. Print its value, from the toolkit folder,
  with `bash -c '. .claude/hooks/_x4-env.sh; echo "$X4_HOOK_MAX_CHARS"'`. If your agent reads
  only one line, flatten newlines to spaces.
- **Characters.** Guard reasons contain non-ASCII text. MEASURED 2026-10-05 over the 9 guard
  scripts: em dash, middle dot, rightwards arrow, warning sign and ellipsis. Two things can break,
  and either one can be a fail-open crash:
  - **your adapter's own output.** A Python adapter on Windows whose stdout is a pipe writes in
    the ANSI code page, not UTF-8 (MEASURED 2026-10-05: `sys.stdout.encoding` was `cp1252`;
    printing an em dash wrote the single byte `0x97`, and printing an arrow raised
    `UnicodeEncodeError`). Never `print()` a reason as it is: encode it yourself and write bytes
    (`sys.stdout.buffer.write(line.encode(ENCODING, "replace"))`).
  - **your agent's reading of it.** MEASURED on the toy agent (2026-10-03): one em dash crashed
    the agent's own console printing.

  Choose the encoding by measurement. **UTF-8** keeps every character, but only if your agent
  decodes hook output as UTF-8 AND shows it without crashing: test it by sending a deny whose
  reason contains an em dash and an arrow through the real agent, and check the call was blocked
  and the model saw the reason intact. **ASCII** (the fallback when you cannot test, or the test
  fails) cannot crash anything but loses characters; transliterate the common ones first (an em
  dash to `--`, an arrow to `->`, an ellipsis to `...`) and only then replace what is left with
  `?`. The words that carry the verdict (`X4 GUARD INERT`, `NEEDS YOUR APPROVAL:`, `BLOCKED`)
  are ASCII, so an ASCII adapter loses no decision, only some punctuation.

Rendering -- from x4guard's decision to your agent's native answer:

| x4guard says | render as |
|---|---|
| `allow` | your agent's "no objection" (often: print nothing) |
| `advise` | added context for the model, carrying `context` |
| `ask` | a native ask ONLY if you MEASURED that your agent honours one. Otherwise a DENY whose reason starts with `NEEDS YOUR APPROVAL:` followed by the reason, so the model stops and asks its user |
| `deny` | a native deny carrying `reason` |
| `deny` with `inert: true` | a deny carrying `reason` (it starts `X4 GUARD INERT`); never an allow |

**If your agent has no ask at all** (its docs say so, or row (a)-style testing of an ask showed it
runs the call anyway), use the `NEEDS YOUR APPROVAL:` deny for every `ask` -- that needs no
measurement of an ask, because it is a deny, and row (a) already measured that your agent honours
a deny. Record it in the capability report as `ask: none (READ: <where the docs say so>)`. The
measurement is needed only for the opposite choice: rendering `ask` as a NATIVE ask.

### Choosing the time budget

These numbers are READ from `agent/guards/claude-hooks/x4guard.py` (the source of every deployed
`x4guard.py`) at v4.0.0:

| constant | default | what it bounds |
|---|---|---|
| `TIMEOUT_S` | 25 s | the guards' budget for ONE check, set by `X4_GUARD_TIMEOUT_S` (default 25 seconds); a delete's two guards share it |
| `KILL_WAIT_S` | 5 s | after a timeout: how long killing the guard's process tree may take |
| `DRAIN_GRACE_S` | 3 s | after the kill: how long x4guard waits for the guard's output pipes to close |

**The worst-case wall clock of one check is `TIMEOUT_S + KILL_WAIT_S + DRAIN_GRACE_S`: 25 + 5 + 3
= 33 s with the defaults**, plus the time to start Python (MEASURED 2026-10-05: a `check` that
refuses at once, on a bad `X4_GUARD_TIMEOUT_S`, took 0.05 s end to end). To print the values
that are in force -- the defaults, or what `X4_GUARD_TIMEOUT_S` in your environment makes of the
first one -- without opening the file, run from the toolkit folder:

```
python -c "import sys; sys.path.insert(0, '.claude/hooks'); import x4guard as g; print(g.TIMEOUT_S, g.KILL_WAIT_S, g.DRAIN_GRACE_S)"
```

(MEASURED 2026-10-05: it prints `25.0 5 3` with the variable unset.) Use `.codex/hooks` or
`.opencode/hooks` in place of `.claude/hooks` if that is the guard tree you have.

**Choose three numbers, in this order:**

1. **H, your agent's hook timeout** -- READ, then bracketed (section 1). With no timeout at all,
   H is however long you are willing to let a tool call wait.
2. **D, your adapter's own deadline**, a few seconds below H: `D = H - 3` or lower. When D runs
   out, YOUR adapter prints the inert deny, so your agent never reaches its own timeout (which
   fails open on many agents).
3. **B, x4guard's budget** (`X4_GUARD_TIMEOUT_S`). If `D >= 33 + 3`, leave the variable unset:
   the default fits. Otherwise set `B = D - KILL_WAIT_S - DRAIN_GRACE_S - 3`, i.e. `B = D - 11`,
   so that x4guard's own worst case ends at least 3 s before your deadline. Example, a 30 s hook
   timeout (the toy agent's): D = 26, B = 15, x4guard's worst case 23 s. Those are the numbers of
   the committed toy adapter (`tools/x4validate/tests/fixtures/toy_agent/toy_adapter.py`), which
   passes the full conformance replay. If B comes out at zero or below (a hook timeout under
   about 15 s), your agent's timeout is too short for the guards' kill-and-drain bounds: report
   that as a gap rather than squeezing B.

**Take the LARGEST budget that fits, never a smaller one "for safety".** A budget that is too
small does not fail safe in a useful way: every check that overruns becomes an inert deny,
blocking a call the guards would have judged. How much headroom the typical latency leaves, on
one machine (Windows 11, 28 logical CPUs, Git Bash), MEASURED 2026-10-05 with x4guard's
`check`, environment pinned to a scratch toolkit:

| load | checks | max | median range |
|---|---|---|---|
| quiet, one at a time | 30 each of write (deny), write (allow), bash shell, delete; 10 PowerShell shell | 0.81 s | 0.25-0.70 s |
| 2 idle-priority CPU burners running | 43 write, 43 bash shell | 0.79 s | 0.25-0.29 s |
| 4 checks at a time (the conformance replay's `--workers 4` shape) | 10 each of write, write (allow), shell, delete | 0.87 s | 0.39-0.72 s |

**Do not size the budget from that table.** On the same machine, the day before, a full
conformance replay with a cold adapter whose budget was 10 s had ONE check run past it
(MEASURED: 1 of 151 cases, `protect-files.sh timed out: this check's 10s budget
(X4_GUARD_TIMEOUT_S) ran out`), while no check in the table took even 1 s: the tail under real
load is more than ten times the worst we could provoke on purpose. So the evidence does NOT say a
10 s budget is too tight for every machine -- typical checks take under a second -- but it does say
10 s is too tight to pass conformance reliably on this one. 15 s has passed the full replay (the
committed toy adapter, MEASURED 2026-10-03 in
`docs/superpowers/measurements/2026-10-03-adapting-cold-test.md`); below that you are guessing,
and if your agent's timeout forces you there, say so in your report.

**The ADAPTER sets `X4_GUARD_TIMEOUT_S`**, in the environment of the x4guard process it starts
(copy its own environment and add the variable). Not your user's shell: the variable would then
depend on how the agent was launched. Verify that x4guard honours it with a budget too small to
finish (MEASURED 2026-10-05, both forms below). In bash:

```
X4_GUARD_TIMEOUT_S=0.05 python .claude/hooks/x4guard.py check --kind write --path x.txt
```

In PowerShell:

```
$env:X4_GUARD_TIMEOUT_S = '0.05'
python .claude/hooks/x4guard.py check --kind write --path x.txt
Remove-Item Env:X4_GUARD_TIMEOUT_S
```

It must answer an inert deny whose reason names the budget you set: `... timed out: this
check's 0.05s budget (X4_GUARD_TIMEOUT_S) ran out`. A value that is not a positive number gives
an inert deny saying so. Then do the same THROUGH YOUR ADAPTER (temporarily set its budget to
0.05) and check that it renders that inert deny -- that is what proves your adapter really passes
the variable on.

**How a budget that is too small shows up in conformance:** a line like `DISAGREE #9 ... guards
say deny, adapter says inert -- X4 GUARD INERT: protect-files.sh timed out: this check's 10s
budget (X4_GUARD_TIMEOUT_S) ran out`. The fix is a larger budget (within your deadline), never
fewer `--workers`: a replay that passes only on a quiet machine will fail your user on a busy one.

## 3. Worked examples

### Worked example: Claude Code

Claude Code is the identity case: the guards ARE its hooks, registered in
`agent/targets/claude/settings.json` and rendered into `.claude/`. There is no translation, so
there is no adapter. `scripts/conformance-profiles/claude.json` runs each case's own hook on its
own payload; it is the engine's IDENTITY CONTROL -- if that profile ever disagrees with the
guards, the conformance engine is broken, not an adapter.

### Worked example: Codex

`agent/guards/adapters/codex.py` (rendered to `.codex/hooks/codex_adapter.py`) is the full pattern.
What it had to handle, all MEASURED on Codex 0.160.0:

- Codex fails open on everything except a parsed JSON deny, and rejects any JSON key it does not
  know. So the adapter prints one line, `X4OK <json>`, and a wrapper
  (`agent/guards/adapters/codex-entry.ps1` on Windows, `agent/guards/adapters/codex-entry.sh`
  elsewhere) prints the JSON only if it is well-formed, and a fail-closed deny otherwise.
- Codex has no working ask, so an ask is a deny reading `NEEDS YOUR APPROVAL: ...`.
- Codex's "Bash" tool runs PowerShell on Windows: the adapter judges it with `--shell powershell`
  there.
- `apply_patch` names several files, with paths relative to the session cwd: the adapter parses
  the patch (`agent/guards/adapters/patch_paths.py`), turns add/update/move-to into writes and
  delete/move-from into deletes, and judges every path from the payload's `cwd`.
- Codex silently disables a hook whose definition changed until the user re-approves it, so
  `agent/targets/codex/hooks.json.tmpl` is frozen: logic changes go into the adapter, never into
  the hook definition.

Its conformance profile is `scripts/conformance-profiles/codex.json`; it feeds the adapter Codex
payloads captured from a live run (`tools/x4validate/tests/fixtures/codex/0.160.0/`).

## 4. Where your adapter goes

**Locally first.** Put your adapter and its profile anywhere (a folder of your own beside the
toolkit is fine) and run conformance against them with `--profile <path>`. Nothing in the
toolkit has to change for that.

**Upstream, once it passes** (section 7), the files go here:

- the adapter: `agent/guards/adapters/<agent>.py`
- a generator target in `tools/x4validate/scripts/gen-agent-trees.py` (the model to copy:
  `CODEX_ADAPTERS` and `render_codex_hooks`), and your agent's own files under
  `agent/targets/<agent>/`
- an installer item set `X4_AGENT_ITEMS_<agent>` in `install.sh` AND the same set in
  `install.ps1` (a test checks that the two agree)
- the profile: `scripts/conformance-profiles/<agent>.json`
- the captured payloads: `tools/x4validate/tests/fixtures/<agent>/<version>/`

### Writing a conformance profile

`x4guard conformance` replays every guard case of the toolkit's own hook test suite, plus the
neutral extras in `scripts/conformance-extra-cases.json`, through your adapter, and compares
each answer with what the guards decide. Each case is one of four KINDS: `shell-bash`,
`shell-powershell`, `edit` (change an existing file) and `write` (create a file with content).
Your profile tells the engine how to build YOUR agent's payload for each kind, how to run your
adapter, and how to read its answer. A minimal one (the shape the toy agent's adapter uses):

```json
{
  "v": 1,
  "agent": "toy",
  "command": ["{PYTHON}", "my_adapter.py"],
  "requires": ["bash", "jq"],
  "run_cwd": "neutral",
  "timeout_s": 120,
  "env": {"X4_GUARD_PY": "{HOOKS}/x4guard.py"},
  "cases": {
    "shell-bash":       {"payload": "payloads/run_shell.json",
                         "set": {"/args/cmdline": "{{COMMAND}}", "/args/interpreter": "bash", "/workdir": "{{CWD}}"}},
    "shell-powershell": {"payload": "payloads/run_shell.json",
                         "set": {"/args/cmdline": "{{COMMAND}}", "/args/interpreter": "powershell", "/workdir": "{{CWD}}"}},
    "edit":  {"payload": "payloads/edit_file.json",  "set": {"/args/target": "{{PATH}}", "/workdir": "{{CWD}}"}},
    "write": {"payload": "payloads/write_file.json", "set": {"/args/target": "{{PATH}}", "/args/text": "{{CONTENT}}", "/workdir": "{{CWD}}"}}
  },
  "unsupported": {},
  "output": {
    "format": "text",
    "exit_codes": {"0": "decode"},
    "empty": "allow",
    "rules": [
      {"regex": "^TOY-BLOCK (?P<text>NEEDS YOUR APPROVAL:.*)$", "decision": "ask"},
      {"regex": "^TOY-BLOCK (?P<text>.*)$", "decision": "deny"},
      {"regex": "^TOY-NOTE (?P<text>.*)$",  "decision": "advise"}
    ]
  }
}
```

Field by field:

- `command`: your adapter's argv. Placeholders: `{TOOLKIT}` (the toolkit running the engine),
  `{HOOKS}` (its `.claude/hooks`), `{PYTHON}` (the engine's interpreter), `{BASH}`, `{PWSH}`.
  A relative path in it is relative to the directory the adapter runs in (see `run_cwd`), so use
  an absolute path or pass the command after `--` instead (forward slashes work on Windows too).
  **Anything after `--` on the command line replaces `command` entirely** -- the profile's
  `command` is then not used at all (the placeholders work after `--` too). So `command` is REQUIRED only
  when you run conformance without `--`; with neither, the run stops with exit 2 ("no adapter
  command") (READ: `scripts/x4conformance.py`, `adapter_command` and `main`). Keep a `command`
  in the profile anyway, written with `{PYTHON}` and `{TOOLKIT}`: an upstreamed profile is run by
  NAME, without `--`. The FIRST line conformance prints, `adapter: ...`, is the argv it really
  uses -- check it to see which one won.
- `requires`: programs that must exist, or the run stops with exit 2.
- `run_cwd`: `"neutral"` (the default, and what you want) runs your adapter in an EMPTY scratch
  folder, exactly like an agent that starts hooks somewhere unrelated. An adapter that ignores
  the payload's working directory then fails the relative-path cases, as it would in real use.
- `env`: added to the environment your adapter runs with. Each case already runs with the
  sandbox's `X4_TOOLKIT`, `X4_GAME`, `X4_REFERENCE` and friends -- pass them through to x4guard
  untouched (inheriting your environment does that).
- `cases`: per KIND, a `payload` template file (a captured payload of YOUR agent, path relative
  to the profile file) and `set`: JSON pointers into it, filled per case after parsing, so no
  escaping is needed. Tokens: `{{COMMAND}}` (the shell command), `{{PATH}}` (the file),
  `{{CONTENT}}` (a write's content), `{{CWD}}` (the call's working directory), and
  `{{CONTENT|lines:+}}` (each line prefixed with `+` and ended with a newline, for patch formats).
  A case may also carry its own `env`. An unknown token stops the run with exit 2.
- `unsupported`: `{"KIND": "why"}` for a kind your agent truly cannot produce. Every kind must be
  in `cases` or here. An unsupported kind is printed as a GAP on every run; its cases are not
  replayed. It is a gap you report, never a pass.
- `output`: how to read your adapter's stdout. `exit_codes` maps an exit status to `"decode"`
  (read stdout) or to a decision; any status not listed is unreadable. `empty`: the decision an
  empty stdout means (leave it out and empty output is unreadable). `format` `"text"`: `rules`
  are regexes tried in order, the first match wins, a named group `text` carries the reason.
  `format` `"json"`: each rule is `{"when": {pointer: value or {"present": true}}, "absent":
  [pointer], "text": pointer, "decision": D, "prefix_decisions": {"NEEDS YOUR APPROVAL:": "ask"}}`;
  `allowed_keys` (`{"": [...], "/inner": [...]}`) makes an unexpected key unreadable -- use it
  when your agent fails open on unknown keys. Optional: `strip_prefix`, `single_line`. Output no
  rule matches is `unreadable`, which counts as a disagreement, never as an allow.

  **Inert answers need no rule of their own.** After a rule matches, a decoded `deny` or `ask`
  whose TEXT contains `X4 GUARD INERT` (or the regex in the optional `inert_pattern`) becomes
  `inert` (READ: `scripts/x4conformance.py`, `_finish`). So in the text example above, the
  generic `TOY-BLOCK` deny rule already reads an inert deny correctly -- PROVIDED its named group
  `text` captures the reason. A rule without a `text` group (or a JSON rule without `text`)
  decodes every inert deny as a plain `deny`, and every case the guards could not check then
  disagrees. An `exit_codes` entry may also map a status straight to `"inert"`. Whether your
  inert reading works is shown by the run itself: see the INERT summary line in section 5(a).

## 5. Required proof

Your adapter is supported when, and only when, all four of these hold:

**(a) Conformance exits 0.**

```
python .claude/hooks/x4guard.py conformance --profile path/to/profile.json -- python path/to/adapter.py
```

It dumps the corpus fresh, replays it, and prints every disagreement by name. **It is slow**:
the dump alone takes several minutes, and every case then runs the guards twice and your
adapter once -- budget up to an hour on Windows, so run it in the background or with a long
timeout, one at a time. How long it takes depends on the machine and its load: one cold run of
a Python adapter took about 40 minutes on 2026-10-03, and on 2026-10-04 two full runs on the
same machine each finished in under 8 minutes (MEASURED from file timestamps: the adapter file
to the first run's log, then that log to the second run's). The output is NOT held back until
the end: each progress line is flushed as it happens (READ: `say(..., flush=True)` in the
engine) -- `adapter: ...`, `dumping the guard corpus ...`, `extras: ...`, `replaying N case(s)
...` -- and then nothing appears during the replay, which is the long part; the summary comes at
the end. Silence after `replaying` is normal, not a hang. To know it has FINISHED, redirect
its output to a file and wait for the summary line (`OK: ...` or the list of disagreements)
and the exit code -- not for your own tool's "background job done": a `&` inside an already
backgrounded call detaches conformance, and the call then reports done while it still runs. A run that is killed leaves its sandbox
in the toolkit's `.test-sandbox/conf-<number>/` folder: delete that folder.

How to read the summary lines (READ: `summarise` in `scripts/x4conformance.py`):

- `conformance: N replayed of M cases; buckets: ...` -- what was replayed, and why the rest was not.
- `GAP: <kind> is not covered by this adapter ...` -- a kind your profile declares `unsupported`.
- `DISAGREE #<n> <case> [<kind>]: guards say X, adapter says Y -- <text>` -- one per disagreement,
  then `FAIL: ...`. When there is any disagreement, the lines below are not printed.
- `K replayed case(s) were INERT in the guards (checked nothing): they agree, and prove nothing`
  -- in K cases the GUARDS THEMSELVES could not check the command (for instance a command they
  cannot analyse), so the right answer was an inert deny, and your adapter gave one. It is
  EXPECTED, not caused by your adapter, and it is good news about your inert reading: each of
  those cases agreed only because your adapter's answer decoded as `inert`. They still had to
  agree -- an adapter that turned one into a plain deny or an allow would have failed -- but they
  do NOT count toward `--min-cases`, because agreeing about "checked nothing" proves no rule.
  (MEASURED: the 2026-10-04 cold run had 4 of 151.)
- `OK: all N replayed cases agree (C checked by the guards)` -- the pass. C is N minus the INERT
  cases, and C is what must reach `--min-cases`.

The summary counts cases `no_native_analogue`: guard cases that carry no shell command and no
file path (searches such as Grep and Glob), which no adapter could be shown -- those are not your
gap -- AND the cases of every kind your profile declares `unsupported`, which are. The count does
not tell the two apart: the GAP line printed for each `unsupported` kind is what names your gap.
Off Windows it
also counts `windows_path_dialect`: file-path cases spelled with backslashes, which on Linux and
macOS name a single file in the current folder rather than the path the guards judge, so they are
not replayed there.

Exit codes: **0** every replayed case agrees and at least `--min-cases` (default 80) of them were
CHECKED by the guards (not inert); **1** at least one disagreement or unreadable answer -- fix the ADAPTER; **2** it could
not evaluate (a bad profile, a missing program, the corpus could not be built, or a reference
control failed) -- nothing was proven either way; **3** too little was examined. Only 0 is a pass,
and a pass with a GAP line is a partial pass.

**(b) A live canary in the real agent.** In a scratch copy, ask your agent to write into a decoy
`reference/` file: the write must be blocked and the model must have seen the reason. Then the
control: the same write into `dev/` must succeed. A block with no working control proves nothing.
The guards find `reference/` and the game install through environment variables, so start your
agent with them pointing at the decoy: `X4_TOOLKIT` at the scratch folder holding `dev/` and
`reference/`, `X4_REFERENCE` at that `reference/`, and `X4_GAME` at a SEPARATE empty folder (a
`dev/` inside `X4_GAME` is part of the game install, and is refused too), plus `X4_PROFILE` at
another empty scratch folder and `X4_CONFIG` at an EMPTY scratch file, so nothing from your real
install's config or game profile is read (all five, see below). With none of them set,
both writes are allowed (MEASURED on the toy agent) -- which is why the control matters.

The scratch folder needs ONLY `dev/` and `reference/` (with the decoy file in it): no `.claude/`,
no copy of the toolkit. Your adapter keeps running the toolkit's REAL `x4guard.py`; only the
environment points the guards at the scratch tree, and the environment wins over the toolkit's
`x4-paths.env` (READ: `_x4-env.sh`). MEASURED both ways: the 2026-10-04 cold run (`X4_TOOLKIT`,
`X4_REFERENCE`, `X4_GAME`, through the toy agent) and a 2026-10-05 probe of x4guard's `check`
that also set `X4_PROFILE` to a scratch folder and `X4_CONFIG` to an EMPTY scratch file both
blocked the `reference/` write and allowed the `dev/` one, with no `.claude/` in the scratch
tree. Setting those last two as well is the safer
canary: then nothing from your real install's config or game profile is read.

**(c) x4doctor shows the layer live.** `python scripts/x4doctor.py` reports per agent target. For
an agent it does not know yet it reports no target for you: say so plainly. That is a gap until a
doctor probe for your agent is upstreamed. **This is the expected state of EVERY new adapter**,
not a fault in yours. Run x4doctor once, record (c) as `x4doctor: no target for <agent> (gap
until a doctor probe is upstreamed)`, quote its `layer2.reference` row as it is, and carry on
with (d). A doctor probe is part of upstreaming (section 7), not of passing locally.

**(d) The fail-mode table** of section 1, every row MEASURED.

Until all four hold, your agent is "partially supported, with these gaps: ..." -- name each gap
to your user. With (a), (b) and (d) done and only (c) missing, that sentence is: "partially
supported, with this gap: x4doctor has no probe for <agent> yet, so it cannot show whether the
hook is live."

## 6. Rules

- Never edit the corpus (`scripts/test-hooks.sh`, `scripts/conformance-extra-cases.json`), a
  guard, or another agent's profile or adapter to make yours pass. A disagreement is a bug in
  your adapter, or a finding to report -- never a reason to move the target.
- Adapters translate; they decide nothing. No allow-list, no "this one is probably fine".
- Never emit an answer shape your agent was not MEASURED to honour.
- Never tell your user they are protected without the section 5 proof.
- Report every gap, an `unsupported` kind included. A gap is not a pass.
- Never approve, trust or re-enable hooks on your user's behalf where your agent asks a human to.

## 7. Upstream submission

Open an issue or pull request on the toolkit's repository with this body, filled in:

```
AGENT:
VERSION:
OPERATING SYSTEM:
HOOK EVENTS:
PAYLOAD CAPTURE:
TOOLS THAT WRITE:
SHELL ROUTING:
VERDICT SHAPES HONOURED:
FAIL MODE:
HOOK TIMEOUT:
HOOK APPROVAL:
CONTEXT CAP:
NATIVE POLICY LAYER:
INSTRUCTION FILE AND BYTE CAP:
EVIDENCE:
```

Then, below it: the conformance summary (its first line, the bucket counts and every GAP line),
the live canary transcript and its control, the fail-mode table, the exact versions of the agent
and the toolkit, and the list of files the pull request adds.
