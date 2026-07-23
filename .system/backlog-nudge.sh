#!/usr/bin/env bash
# SessionStart hook for the debrief system (github.com/Era-Laboratories/claude-debrief).
# Receives Claude Code SessionStart JSON on stdin and, on a genuine startup with
# a non-empty backlog, injects a one-line nudge into the session's context so
# the human learns the queue needs a `/debrief day` pass — the surfacing end of
# a loop the SessionEnd sweep only ever logged to hook.log.
#
# Read-only w.r.t. memory. The computation is delegated to debrief-backlog.py,
# which writes nothing. The ONLY thing this script writes is its own
# once-per-day throttle stamp (.system/.backlog-nudge, gitignored). No daily,
# no INDEX.md, no queue mutation — the curation gate stays intact.
#
# NOT async: async SessionStart hooks cannot contribute additionalContext
# (opposite of the SessionEnd drafter, which must be async). A stdlib read of a
# few-dozen-line jsonl is milliseconds, so a synchronous hook is fine.
set -u

# Symlink-safe path resolution, identical to session-debrief.sh: $0/dirname/pwd
# keep the era-maker install path; realpath would jump to the clone.
SYSTEM_DIR="$(cd "$(dirname "$0")" && pwd)"
DEBRIEF_DIR="$(dirname "$SYSTEM_DIR")"
STAMP="$SYSTEM_DIR/.backlog-nudge"

# Only fire on a real session start. resume/compact/clear re-inject context
# mid-work; nudging there is noise. Defence-in-depth with the settings matcher.
SOURCE="$(cat | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("source", ""))
except Exception:
    print("")
' 2>/dev/null)" || SOURCE=""
[ "$SOURCE" = "startup" ] || exit 0

# Once per day: if the stamp already holds today, stay silent.
TODAY="$(date +%Y-%m-%d)"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ]; then
  exit 0
fi

# DEBRIEF_DIR is passed explicitly — bash already resolved it correctly through
# the symlink, so the CLI needn't re-derive it from its own (symlinked) path.
LINE="$(DEBRIEF_DIR="$DEBRIEF_DIR" python3 "$SYSTEM_DIR/debrief-backlog.py" --nudge 2>/dev/null)" || LINE=""

# Clean queue: emit nothing and DON'T stamp — stay armed so a backlog that
# appears later today still surfaces on the next startup.
[ -n "$LINE" ] || exit 0

# additionalContext lands in Claude's context, not on the user's screen; phrase
# it as an imperative Claude reliably relays, and restate the gate so no
# downstream step is tempted to auto-drain the backlog into memory.
CONTEXT="Before your first reply, tell the user this debrief backlog: ${LINE} Only a human /debrief day pass promotes drafts into durable memory — never auto-write a daily or touch INDEX.md."
python3 - "$CONTEXT" <<'PY'
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": sys.argv[1],
}}))
PY

printf '%s' "$TODAY" >"$STAMP"
exit 0
