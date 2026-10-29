#!/usr/bin/env bash
# Automatic dream pass: the cross-session consolidation over OBSERVATIONS.md.
# Usage: dream.sh [--force]
#
# Called by the daemon when DEBRIEF_AUTO_DREAM=1. Runs only when both gates
# hold, the way Claude Code's own memory consolidation is gated:
#   - the last dream is at least DEBRIEF_DREAM_MIN_HOURS old (default 24)
#   - at least DEBRIEF_DREAM_MIN_DAILIES new dailies landed since (default 3)
# `last dream: never` passes both. --force skips the gates, not the lock.

set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
SYSTEM_DIR="$(cd "$(dirname "$0")" && pwd)"
DEBRIEF_DIR="$(dirname "$SYSTEM_DIR")"
LOG="$SYSTEM_DIR/hook.log"
LOCK="$SYSTEM_DIR/.dream.lock"
OBS="$DEBRIEF_DIR/OBSERVATIONS.md"
MIN_HOURS="${DEBRIEF_DREAM_MIN_HOURS:-24}"
MIN_DAILIES="${DEBRIEF_DREAM_MIN_DAILIES:-3}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$LOG"; }

[ -f "$OBS" ] || { log "dream: no OBSERVATIONS.md, skipped"; exit 0; }
last="$(grep -oE '^last dream: (never|[0-9]{4}-[0-9]{2}-[0-9]{2})' "$OBS" | tail -1 | cut -d' ' -f3)"
last="${last:-never}"

if [ "${1:-}" != "--force" ] && [ "$last" != never ]; then
  # Hours since the footer date, and since the last run's audit file (the
  # footer has day resolution; the audit file's mtime has the real time).
  audit="$DEBRIEF_DIR/dreams/$last.md"
  if [ -f "$audit" ] && [ -n "$(find "$audit" -mmin "-$((MIN_HOURS * 60))")" ]; then
    exit 0
  fi
  new=0
  for f in "$DEBRIEF_DIR"/20??-??-??-*.md; do
    [ -f "$f" ] || continue
    d="$(basename "$f" | cut -c1-10)"
    [[ "$d" > "$last" ]] && new=$((new + 1))
  done
  [ "$new" -ge "$MIN_DAILIES" ] || exit 0
fi

# One dream at a time; a lock whose PID is gone died with the machine.
if ! mkdir "$LOCK" 2>/dev/null; then
  owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then exit 0; fi
  rm -rf "$LOCK"; mkdir "$LOCK" || exit 0
fi
echo $$ >"$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

today="$(date +%F)"
mkdir -p "$DEBRIEF_DIR/dreams"
log "dream: starting (last dream: $last)"
prompt="$(cat "$SYSTEM_DIR/prompts/auto-dream.md")

DATE: $today

--- /debrief command text ---
$(cat "$HOME/.claude/commands/debrief.md" 2>/dev/null)"

cd "$(dirname "$DEBRIEF_DIR")" || exit 1
DEBRIEF_GENERATION=1 claude -p "$prompt" \
  --allowedTools "Read,Glob,Grep,Write,Edit,Bash(mkdir:*)" >>"$LOG" 2>&1
log "dream exited rc=$?"
