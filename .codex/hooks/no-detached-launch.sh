#!/bin/bash
# PreToolUse (Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell|Monitor): refuse a tool call that would launch a
# process DETACHED (Windows DETACHED_PROCESS, 0x8). A detached process has no console, so every console program it
# starts without CREATE_NO_WINDOW gets a visible window on the user's desktop. The logic is no-detached-launch.py
# (see its docstring for the scope and the evidence); this wrapper only finds Python and passes stdin through.
#
# FAIL OPEN, by design (unlike the other guards here, which ask or deny when Python is missing): the scope is narrow
# and the check is a convenience against one measured nuisance, so no Python, a crashed hook or unreadable input
# allows the call and prints nothing. Registered for Claude Code only; Codex and OpenCode are not wired (see the
# docstring of no-detached-launch.py).
# Parameter expansion for HOOK_DIR, both separators: see search-scope.sh.
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"
x4_resolve_python
[ -z "$X4_PY" ] && exit 0
# The hook's stdin is the payload; `-I` through X4_PYI (see _x4-env.sh). Its decision, if any, is printed by the
# hook; a nonzero exit (a crash) is swallowed, and so is anything it printed, which could be half a verdict.
ANSWER=$("$X4_PY" "${X4_PYI[@]}" "$HOOK_DIR/no-detached-launch.py" 2>/dev/null) || exit 0
[ -n "$ANSWER" ] && printf '%s' "$ANSWER"
exit 0
