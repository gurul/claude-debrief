#!/usr/bin/env bash
# Fold one day's session drafts into the daily (auto-day, no review).
# Usage: curate-day.sh <YYYY-MM-DD> [wait-seconds]
#
# Called by the SessionEnd hook after a draft lands, and by the daemon to
# catch up on drafts a curator never consumed (shutdown, crash, API error).
# One curator at a time: the lock dir holds the owner's PID, so a lock left
# by a curator that died with the machine is detected and taken over.

set -u
SYSTEM_DIR="$(cd "$(dirname "$0")" && pwd)"
DEBRIEF_DIR="$(dirname "$SYSTEM_DIR")"
LOG="$SYSTEM_DIR/hook.log"
LOCK="$SYSTEM_DIR/.curate.lock"
DAY="${1:?usage: curate-day.sh YYYY-MM-DD [wait-seconds]}"
WAIT="${2:-600}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$LOG"; }
# Rotate past 1 MB: keep one previous file, so the log never grows without bound.
[ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 1048576 ] && mv -f "$LOG" "$LOG.1"

take_lock() {
  if mkdir "$LOCK" 2>/dev/null; then
    echo $$ >"$LOCK/pid"
    return 0
  fi
  local owner
  owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -z "$owner" ] || ! kill -0 "$owner" 2>/dev/null; then
    log "curator: stale lock (pid ${owner:-none} gone), taking over"
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null && echo $$ >"$LOCK/pid" && return 0
  fi
  return 1
}

waited=0
until take_lock; do
  [ "$waited" -ge "$WAIT" ] && { log "curator for $DAY: lock busy, deferred to the daemon"; exit 0; }
  sleep 15; waited=$((waited + 15))
done
trap 'rm -rf "$LOCK"' EXIT

ls "$DEBRIEF_DIR/sessions/$DAY/"*.md >/dev/null 2>&1 || { log "curator for $DAY: nothing to fold in"; exit 0; }

prompt="$(cat "$SYSTEM_DIR/prompts/auto-day.md")

DATE: $DAY

--- /debrief command text ---
$(cat "$HOME/.claude/commands/debrief.md" 2>/dev/null)"

cd "$(dirname "$DEBRIEF_DIR")" || exit 1
DEBRIEF_GENERATION=1 claude -p "$prompt" \
  --allowedTools "Read,Glob,Grep,Write,Edit,Bash(mkdir:*),Bash(mv:*)" >>"$LOG" 2>&1
log "curator for $DAY exited rc=$?"
