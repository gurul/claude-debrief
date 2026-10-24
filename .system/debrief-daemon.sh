#!/usr/bin/env bash
# Debrief daemon: the catch-up loop. Optional; installed as a launchd agent
# from deploy/co.claude-debrief.daemon.plist.template (see README "Daemon").
#
# launchd runs this at login/boot, on wake (missed intervals are coalesced
# into one run) and every 15 minutes. Each run repairs whatever the last
# shutdown, crash or closed terminal left unfinished, in this order:
#
#   1. stale locks    lock dirs left by a process that died with the machine
#   2. repair sweep   the hook's own sweep: respawn drafters that never wrote
#   3. new sessions   any transcript, in any project, idle IDLE_MIN and never
#                     queued, goes through the SessionEnd pipeline (this is
#                     also how the backlog of past sessions is worked off)
#   4. lost curators  drafts that landed but were never folded into a day
#                     (only with DEBRIEF_AUTO_CURATE=1; otherwise they wait
#                     for a human /debrief day, as designed)
#
# Settings come from the environment (the launchd job sets them):
#   DEBRIEF_AUTO_CURATE=1     fold drafts into dailies with no review step
#   DEBRIEF_RAW_ARCHIVE=1     keep a redacted verbatim copy of each transcript
#   DEBRIEF_RAW_RETAIN_DAYS   0 = keep forever (default 180)
#   DEBRIEF_IDLE_MIN (30), DEBRIEF_MAX_PER_RUN (3), DEBRIEF_CURATE_AFTER_MIN (20)
#
# Every step is idempotent: running twice, or after a reboot mid-step, never
# double-records a session (the queue and .daemon-seen are the ledger).

set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SYSTEM_DIR="$(cd "$(dirname "$0")" && pwd)"
DEBRIEF_DIR="$(dirname "$SYSTEM_DIR")"
QUEUE="$DEBRIEF_DIR/sessions/queue.jsonl"
LOG="$SYSTEM_DIR/daemon.log"
SEEN="$SYSTEM_DIR/.daemon-seen"  # session ids handed off, or judged trivial / our own
LOCK="$SYSTEM_DIR/.daemon.lock"
IDLE_MIN="${DEBRIEF_IDLE_MIN:-30}"
MAX_PER_RUN="${DEBRIEF_MAX_PER_RUN:-3}"
CURATE_AFTER_MIN="${DEBRIEF_CURATE_AFTER_MIN:-20}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$LOG"; }

# One daemon at a time; a lock whose PID is gone is from before a reboot.
if ! mkdir "$LOCK" 2>/dev/null; then
  owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then exit 0; fi
  rm -rf "$LOCK"; mkdir "$LOCK" || exit 0
fi
echo $$ >"$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# 1. Stale locks. The hook's repair lock has no PID; its sweep takes seconds,
#    so one older than 10 minutes was orphaned. curate-day.sh checks its own.
if [ -d "$SYSTEM_DIR/.repair.lock" ] && [ -n "$(find "$SYSTEM_DIR/.repair.lock" -maxdepth 0 -mmin +10)" ]; then
  rmdir "$SYSTEM_DIR/.repair.lock" 2>/dev/null && log "cleared stale repair lock"
fi

# 2. Repair sweep: the hook runs it before reading its input; empty input
#    then makes it stop there.
bash "$SYSTEM_DIR/session-debrief.sh" </dev/null >/dev/null 2>&1

# 3. New sessions, every project. Scratch and temp working dirs are skipped.
handled=0
for proj in "$HOME"/.claude/projects/*/; do
  case "$(basename "$proj")" in -private-tmp-*|-private-var-*|-tmp-*) continue ;; esac
  if [ -n "${DEBRIEF_PROJECT_MATCH:-}" ]; then
    # shellcheck disable=SC2254
    case "$(basename "$proj")" in $DEBRIEF_PROJECT_MATCH) ;; *) continue ;; esac
  fi
  while IFS= read -r t; do
    [ "$handled" -ge "$MAX_PER_RUN" ] && break 2
    sid="$(basename "$t" .jsonl)"
    grep -qx "$sid" "$SEEN" 2>/dev/null && continue
    if { [ -f "$QUEUE" ] && grep -q "\"session_id\": \"$sid\"" "$QUEUE"; } \
       || head -c 20000 "$t" | grep -qE 'You are the (debrief drafter|automatic day curator)' \
       || [ "$(wc -l <"$t" | tr -d ' ')" -lt 30 ]; then
      echo "$sid" >>"$SEEN"
      continue
    fi
    echo "$sid" >>"$SEEN"
    printf '{"session_id":"%s","transcript_path":"%s","reason":"daemon"}' "$sid" "$t" \
      | bash "$SYSTEM_DIR/session-debrief.sh"
    log "queued $sid from $(basename "$proj")"
    handled=$((handled + 1))
  done < <(find "$proj" -maxdepth 1 -name '*.jsonl' -mmin "+$IDLE_MIN" 2>/dev/null | sort)
done

# 4. Lost curators: a day folder still holding drafts older than
#    CURATE_AFTER_MIN means its curator never ran or died. One day per run,
#    oldest first, in the background (curate-day.sh takes the lock).
[ "${DEBRIEF_AUTO_CURATE:-}" = 1 ] || exit 0
for day_dir in "$DEBRIEF_DIR"/sessions/20??-??-??/; do
  [ -d "$day_dir" ] || continue
  if [ -n "$(find "$day_dir" -maxdepth 1 -name '*.md' -mmin "+$CURATE_AFTER_MIN" | head -1)" ]; then
    day="$(basename "$day_dir")"
    log "catch-up: curating $day"
    nohup bash "$SYSTEM_DIR/curate-day.sh" "$day" 0 >/dev/null 2>&1 &
    break
  fi
done
