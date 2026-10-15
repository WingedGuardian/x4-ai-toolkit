# X4-OPENCODE.md — OpenCode addendum (X4 AI Assistant Toolkit)

**OpenCode support is BEST EFFORT, CLI only: built from OpenCode's docs and source, not measured
against a running OpenCode.** The OpenCode **desktop app is NOT supported**: its plugin hooks
never fire (anomalyco/opencode#38604, closed as not planned), so nothing below would guard you.

Two layers guard this project under the OpenCode CLI:
1. **Deny rules** in `.opencode/opencode.jsonc` (rendered per machine at install): edits into
   `reference/`, `.cat`/`.dat` writes, and deletion commands aimed at `reference/`.
2. **A plugin** (`.opencode/plugins/x4guard.js`) that asks the toolkit's guards before every
   `bash`, `edit`, `write` and `apply_patch`, and blocks the call when they refuse. When it is
   loaded, your system prompt carries a line starting `X4 GUARDS LIVE`. **If that line is
   missing, the plugin did not load and only layer 1 is active** -- say so to the user.

Keep every rule in AGENTS.md by your own discipline as well, and never write into the game
installation. Where AGENTS.md shows a guard command, run the copy in `.opencode/hooks/`:

    python .opencode/hooks/x4guard.py check --kind shell --shell powershell --command "<cmd>"
    python .opencode/hooks/x4guard.py check --kind write --path "<file>"
    python .opencode/hooks/x4guard.py check --kind delete --path "<file>"

On Windows OpenCode runs shell commands in **PowerShell** unless it was configured for bash:
pass `--shell powershell` there. Skills and AGENTS.md name the toolkit root as `$env:X4_TOOLKIT`,
`$X4_TOOLKIT`, or (in a copy the installer has not rendered) `TOOLKIT` in double curly braces:
all three mean the folder in the X4_TOOLKIT environment variable. Write `$env:X4_TOOLKIT` in
PowerShell and `$X4_TOOLKIT` in bash, whichever form the text shows. On Linux/macOS pass
`--shell bash` and use `python3`.

Treat `deny` and `ask` as a stop: ask the user before going on. Treat `inert: true` as "nothing
was checked", never a pass. A refusal that starts `NEEDS YOUR APPROVAL` is the guards asking
the user, not you: ask them; do not retry until they agree.

**Subagents:** the plugin may not run inside a subagent session (anomalyco/opencode#5894), while
the deny rules do. From a subagent, ask the guards yourself (the commands above) before any
write or delete.

Skills live in `.opencode/skills/<name>/SKILL.md`; open the one AGENTS.md names before the task.
Never `git add -A` / `git add .`: stage explicit paths.

The plugin imports the pinned transport in `tools/modding_harness/`. Unknown
tools warn and allow; every observed call gets a coverage receipt when Python is
available, with warnings at counts 1, 2, 4, 8 and so on. If receipts cannot be
written, the warning says so. `python tools/agent-harness.py coverage --session-id ID`
reports pre/post correlation; a receipt or banner does not prove host blocking.
The default 60-second transport budget includes Python discovery and cleanup.
Post receipts alone do not mean validation passed. Desktop and V2 are unqualified.
