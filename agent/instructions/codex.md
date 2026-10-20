# AGENTS.md — X4 Foundations Modding (X4 AI Assistant Toolkit)

### How the safety rules reach you (Codex only: another agent may skip this part)

Under Claude Code these rules are enforced by tested hooks. **Under Codex they may not be:
Codex hooks fail OPEN** (measured, Codex 0.159.2): if a hook crashes, times out or was never
reviewed by the user, Codex runs the command anyway, and unreviewed or changed hooks are
skipped silently. So keep every rule below by your own discipline (and never write into the
game installation). Before each write, delete or move, send the exact operation as complete
Action v1 JSON to `python tools/agent-harness.py check` (fields and reply: `ADAPTING.md` §2).
Stop on ask/deny, a failed evaluation, malformed output or a transport failure; exit 0 alone is
not enough, and `--preview` (like legacy `x4guard.py check`) is not permanent save preservation.

On Windows Codex runs shell commands in **PowerShell**: use `"shell":"powershell"` (a PowerShell
write into `reference\` checked as `"bash"` passed, measured), and write the toolkit root
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

Codex shell hooks may see only the session directory, not the tool's workdir: when it is absent,
give shell writes, deletes and moves absolute paths (drive/UNC on Windows); an in-command cd cannot
prove the workdir. Reads stay allowed; script effects keep the documented coverage limits.
