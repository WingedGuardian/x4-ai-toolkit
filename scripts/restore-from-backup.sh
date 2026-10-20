#!/bin/bash
# Restore a file that Claude auto-backed-up before editing.
# Backups live in .claude/backups/ as <timestamp>__<flattened-path>.<unique-suffix>.
# AUDIT_LOG.txt names each snapshot; older unsuffixed backups can still be restored.
#
# Usage:
#   bash scripts/restore-from-backup.sh            # list recent backups
#   bash scripts/restore-from-backup.sh <pattern>  # show matching backups
#   RESTORE=1 bash scripts/restore-from-backup.sh <backup-filename> <dest-path>

set -euo pipefail
BACKUP_DIR="${CLAUDE_PROJECT_DIR:-.}/.claude/backups"

if [ ! -d "$BACKUP_DIR" ]; then
  echo "No backup dir at $BACKUP_DIR"; exit 0
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=()
if [ -n "${X4_PYTHON:-}" ]; then PY=("$X4_PYTHON"); fi
if [ "${#PY[@]}" -eq 0 ] && command -v py >/dev/null 2>&1 && py -3 -c 'import sys;sys.exit(sys.version_info < (3,10))'; then PY=(py -3); fi
if [ "${#PY[@]}" -eq 0 ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys;sys.exit(sys.version_info < (3,10))'; then PY=("$candidate"); break; fi
  done
fi
[ "${#PY[@]}" -gt 0 ] || { echo "REFUSING: restore verification needs Python >= 3.10" >&2; exit 2; }
if [ "${RESTORE:-0}" = "1" ]; then
  [ "$#" -eq 2 ] || { echo "Expected a literal backup filename and destination" >&2; exit 2; }
  exec "${PY[@]}" "$HERE/restore-from-backup.py" "$BACKUP_DIR" restore "$1" "$2"
fi
exec "${PY[@]}" "$HERE/restore-from-backup.py" "$BACKUP_DIR" list "${1:-}"
