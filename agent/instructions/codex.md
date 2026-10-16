# AGENTS.md — X4 Foundations Modding (X4 AI Assistant Toolkit)

### How the safety rules reach you (Codex only: another agent may skip this part)

Under Claude Code these rules are enforced by tested hooks. **Under Codex they may not be:
Codex hooks fail OPEN** (measured, Codex 0.159.2): if a hook crashes, times out or was never
reviewed by the user, Codex runs the command anyway, and unreviewed or changed hooks are
skipped silently. So keep every rule below by your own discipline (and never write into the
game installation). For manual preflight, send complete Action v1 JSON on stdin to
`python tools/agent-harness.py check` immediately before the exact operation; see
`ADAPTING.md` for examples. Include actual `cwd`, session/call IDs, every affected path
and parallel operation; a move includes source delete and destination write together.
Shell requests include the actual shell and complete command. Do not split bulk requests.
Parse the complete reply's inner `result`: `api_version`, `decision`, `evaluation`,
`preservation`, `validation`, `reason` and `warnings`; retain `transactions`. Stop on
ask/deny, failed evaluation, malformed output or transport failure. Exit 0 alone is
insufficient. Normal checks preserve recognized saves; `--preview` and legacy
`x4guard.py check` are policy previews and cannot replace permanent preservation.

On Windows Codex runs shell commands in **PowerShell**: use `"shell":"powershell"` (judged as
bash, a PowerShell write into `reference\` was measured to pass), and write the toolkit root
as `$env:X4_TOOLKIT` wherever these instructions say `$X4_TOOLKIT` (in PowerShell that form
expands to nothing). Installed skills already say `$env:X4_TOOLKIT` on Windows (the installer
renders them); a skill in an uninstalled copy says `TOOLKIT` in double curly braces, meaning
the same folder. On Linux/macOS use
`"shell":"bash"`, `python3` and `$X4_TOOLKIT`. Treat `deny` and `ask` as a stop and ask the user; treat
`inert: true` as "nothing was checked", never a pass.

**Check for the banner.** When the X4 hooks are live, your session context starts with
`X4 GUARDS LIVE (codex hooks v1)`. If you do not see that line, the hooks are not running
here (never reviewed, changed since review, or disabled) and nothing is enforced for you:
tell the user before any write or delete, and suggest they run
`python scripts/x4doctor.py --agent codex` from the toolkit folder.

Skills live in `.agents/skills/<name>/SKILL.md`; open the one this file names before the task.
Never `git add -A` / `git add .`: stage explicit paths.
