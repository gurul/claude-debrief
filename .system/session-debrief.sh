#!/usr/bin/env bash
# SessionEnd hook for the debrief system (github.com/Era-Laboratories/claude-debrief).
# Receives Claude Code SessionEnd JSON on stdin, queues the session in
# sessions/queue.jsonl, and spawns a detached headless drafter that writes a
# machine-draft session debrief into sessions/<date>/.
# Must always exit 0 — SessionEnd is non-blocking and errors are ours to log.
set -u

SYSTEM_DIR="$(cd "$(dirname "$0")" && pwd)"
DEBRIEF_DIR="$(dirname "$SYSTEM_DIR")"
LOG="$SYSTEM_DIR/hook.log"
QUEUE="$DEBRIEF_DIR/sessions/queue.jsonl"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$LOG"; }

# Recursion guard: the headless drafter is itself a claude session whose exit
# fires this same hook. It runs with DEBRIEF_GENERATION=1 in its environment.
if [ -n "${DEBRIEF_GENERATION:-}" ]; then
  log "skip: drafter session (DEBRIEF_GENERATION set)"
  exit 0
fi

# Spawn a detached drafter. Logs the drafter's exit code and whether the
# draft file actually landed — silent drafter deaths (API blips, logged-out
# CLI) were previously invisible except as stray output in this log.
spawn_drafter() {
  # args: transcript session_id ended_at reason out_file
  local transcript="$1" sid="$2" ended="$3" reason="$4" out="$5"
  local prompt
  prompt="$(cat "$SYSTEM_DIR/prompts/session-draft.md")

TRANSCRIPT: $transcript
SESSION_ID: $sid
ENDED: $ended (reason: $reason)
OUTPUT FILE: $out"
  nohup env DEBRIEF_GENERATION=1 bash -c '
    claude -p "$1" --allowedTools "Read,Glob,Grep,Write" >>"$2" 2>&1
    rc=$?
    state=MISSING; [ -f "$4" ] && state=present
    printf "%s drafter for %s exited rc=%s (draft %s)\n" \
      "$(date "+%Y-%m-%dT%H:%M:%S%z")" "$3" "$rc" "$state" >>"$2"
  ' _ "$prompt" "$LOG" "$sid" "$out" >/dev/null 2>&1 &
}

# --- Self-repair sweep -------------------------------------------------
# A drafter can die without producing its file (observed: "API Error:
# Connection closed mid-response" 07-02; "Not logged in" 07-06). The queue
# receipt survives, so every session end re-checks: pending entries older
# than 15 min whose draft file is missing get their drafter respawned
# (max 3 attempts, then status=failed). Lock dir serializes concurrent
# session ends (two sessions closed within 1s on 07-06).
REPAIR_LOCK="$SYSTEM_DIR/.repair.lock"
if [ -f "$QUEUE" ] && mkdir "$REPAIR_LOCK" 2>/dev/null; then
  REPAIRS="$(QUEUE="$QUEUE" python3 <<'PY'
import json, os
from datetime import datetime, timedelta, timezone
queue = os.environ["QUEUE"]
now = datetime.now(timezone.utc)
out_lines, respawn = [], []
for line in open(queue):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    draft = d.get("draft", "")
    # A missing draft does NOT mean the drafter died. `/debrief day` MOVES the
    # drafts it consumes into sessions/archive/<date>/, which empties the
    # original path — so an aggregated session looks identical to a failed one
    # from here. Respawning then re-drafts already-curated work: one
    # headless run per sweep, three times, then the entry is marked `failed`
    # even though its draft is sitting in the archive. Observed in a live
    # install: one session marked `failed` with its draft archived and
    # aggregated, and another handed a second, *different* draft written
    # over the top of an already-curated one.
    # An archived copy is proof day mode consumed it, so heal the status
    # rather than respawn. Only day mode writes the archive.
    if draft and not os.path.exists(draft):
        sess_dir, name = os.path.split(draft)
        archived = os.path.join(
            os.path.dirname(sess_dir), "archive", os.path.basename(sess_dir), name
        )
        if os.path.exists(archived):
            if d.get("status") != "aggregated":
                d["status"] = "aggregated"
                d.pop("failure", None)
            out_lines.append(json.dumps(d))
            continue
    # The OTHER half of the pending ambiguity: a draft that LANDED but that no
    # day-debrief ever consumed used to stay `pending` forever — invisible and
    # unbounded (a live install had accumulated 16 such entries over two
    # weeks). `pending` now strictly means "awaiting drafter"; draft-on-disk
    # entries older than a day become `unconsumed` — a visible, countable
    # backlog that only `/debrief day` clears (it archives the draft, which
    # flips the entry to `aggregated` via the heal above).
    if d.get("status") == "pending" and os.path.exists(draft):
        try:
            ended = datetime.strptime(d["ended_at"], "%Y-%m-%dT%H:%M:%S%z")
            unread = (now - ended) > timedelta(hours=24)
        except Exception:
            unread = True
        if unread:
            d["status"] = "unconsumed"
    if d.get("status") == "pending" and not os.path.exists(draft):
        try:
            ended = datetime.strptime(d["ended_at"], "%Y-%m-%dT%H:%M:%S%z")
            stale = (now - ended) > timedelta(minutes=15)
        except Exception:
            stale = True
        if stale:
            if not os.path.exists(d.get("transcript_path", "")):
                d["status"] = "failed"
                d["failure"] = "transcript missing at repair time"
            elif d.get("attempts", 0) >= 3:
                d["status"] = "failed"
                d["failure"] = "drafter failed 3 times"
            else:
                d["attempts"] = d.get("attempts", 0) + 1
                respawn.append("\t".join([
                    d["transcript_path"], d["session_id"],
                    d["ended_at"], d.get("reason", ""), d["draft"],
                ]))
    out_lines.append(json.dumps(d))
tmp = queue + ".tmp"
with open(tmp, "w") as f:
    f.write("\n".join(out_lines) + "\n")
os.replace(tmp, queue)
print("\n".join(respawn))
PY
)" || REPAIRS=""
  if [ -n "$REPAIRS" ]; then
    while IFS=$'\t' read -r R_TRANSCRIPT R_SID R_ENDED R_REASON R_OUT; do
      [ -z "${R_SID:-}" ] && continue
      mkdir -p "$(dirname "$R_OUT")"
      spawn_drafter "$R_TRANSCRIPT" "$R_SID" "$R_ENDED" "$R_REASON" "$R_OUT"
      log "repair: respawned drafter for $R_SID -> $R_OUT"
    done <<<"$REPAIRS"
  fi
  # Surface the unconsumed backlog on every sweep so it can never silently
  # grow again — visibility is the whole point. Dates are named so a missing
  # daily is diagnosable from the log line alone.
  UNCONSUMED_DATES=$(grep '"status": "unconsumed"' "$QUEUE" 2>/dev/null \
    | grep -o '"ended_at": "[0-9-]*' | cut -d'"' -f4 | sort -u | paste -sd, -)
  if [ -n "${UNCONSUMED_DATES:-}" ]; then
    UNCONSUMED=$(grep -c '"status": "unconsumed"' "$QUEUE" 2>/dev/null || true)
    log "unconsumed-backlog: $UNCONSUMED drafts await a day-debrief (dates: $UNCONSUMED_DATES)"
  fi
  rmdir "$REPAIR_LOCK" 2>/dev/null
fi

INPUT="$(cat)"
IFS=$'\t' read -r SESSION_ID TRANSCRIPT REASON < <(
  printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
print("\t".join(str(d.get(k, "")) for k in ("session_id", "transcript_path", "reason")))
'
) || true

if [ -z "${SESSION_ID:-}" ]; then
  log "skip: could not parse hook input"
  exit 0
fi

if [ ! -f "${TRANSCRIPT:-}" ]; then
  log "skip: transcript missing ($SESSION_ID: ${TRANSCRIPT:-<none>})"
  exit 0
fi

LINES=$(wc -l <"$TRANSCRIPT" | tr -d ' ')
if [ "$LINES" -lt 30 ]; then
  log "skip: trivial session ($SESSION_ID, $LINES transcript lines)"
  exit 0
fi

ENDED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
DATE="$(date +%Y-%m-%d)"
TIME="$(date +%H%M)"
OUT_DIR="$DEBRIEF_DIR/sessions/$DATE"
OUT="$OUT_DIR/${TIME}-${SESSION_ID:0:8}.md"
mkdir -p "$OUT_DIR"

# Durable record even if the drafter fails. One JSON object per line.
SESSION_ID="$SESSION_ID" TRANSCRIPT="$TRANSCRIPT" REASON="$REASON" \
ENDED_AT="$ENDED_AT" DRAFT="$OUT" python3 -c '
import json, os
print(json.dumps({
    "session_id": os.environ["SESSION_ID"],
    "transcript_path": os.environ["TRANSCRIPT"],
    "ended_at": os.environ["ENDED_AT"],
    "reason": os.environ["REASON"],
    "draft": os.environ["DRAFT"],
    "status": "pending",
}))
' >>"$QUEUE"

# Opt-in cold archive (DEBRIEF_RAW_ARCHIVE=1): gzip the raw transcript next to
# the queue receipt. Claude Code deletes transcripts after ~30 days, which
# kills day-mode's raw-transcript fallback for anything older — the queue in a
# live install already carries entries failed with "transcript missing".
# Stored as .jsonl.gz so it is structurally invisible to debrief-search.py's
# collect() (non-.md, no curated frontmatter): this is insurance, NEVER a
# search corpus. Indexing it would collapse the curation gate.
#
# Redaction is not optional. Walling the archive off from *search* says nothing
# about the bytes at rest, and the leak vector is not credentials handled
# deliberately — it is third-party output that happens to carry one (measured
# case: a LiveKit `access_token=eyJ…` inside a Traefik access-log line during an
# unrelated investigation). So the transcript goes through redact-transcript.py
# on the way to gzip, and a missing/failing redactor means NO archive rather
# than a raw one: fail closed, because a silently-unredacted archive is worse
# than no archive at all.
#
# Retention: DEBRIEF_RAW_RETAIN_DAYS (default 180). "Forever" is a decision you
# otherwise make once, by accident, and cannot undo.
if [ -n "${DEBRIEF_RAW_ARCHIVE:-}" ]; then
  REDACTOR="$(dirname "$0")/redact-transcript.py"
  RAW_DIR="$DEBRIEF_DIR/sessions/raw/$DATE"
  RAW_OUT="$RAW_DIR/${TIME}-${SESSION_ID:0:8}.jsonl.gz"
  if [ ! -f "$REDACTOR" ]; then
    log "raw-archive: SKIPPED — redactor missing at $REDACTOR (failing closed)"
  else
    mkdir -p "$RAW_DIR"
    # Pipe, but check the redactor's status rather than gzip's: with a plain
    # pipe gzip happily succeeds on a truncated stream, which would bank a
    # partial archive as if it were whole.
    if REDACT_LOG=$(python3 "$REDACTOR" "$TRANSCRIPT" 2>&1 >"$RAW_DIR/.tmp.$$"); then
      if gzip -c "$RAW_DIR/.tmp.$$" >"$RAW_OUT" 2>>"$LOG"; then
        log "raw-archive: $(basename "$RAW_OUT") ($DATE) [${REDACT_LOG:-no matches}]"
      else
        log "raw-archive: gzip failed for $SESSION_ID"
        rm -f "$RAW_OUT"
      fi
    else
      log "raw-archive: SKIPPED — redactor failed for $SESSION_ID: $REDACT_LOG"
    fi
    rm -f "$RAW_DIR/.tmp.$$"
  fi

  # Prune whole date directories past the retention horizon. Compared by NAME
  # (dirs are YYYY-MM-DD) not mtime, so re-touching an old archive — a backup
  # pass, a filesystem copy — cannot silently extend its life.
  RETAIN="${DEBRIEF_RAW_RETAIN_DAYS:-180}"
  if [ "$RETAIN" -gt 0 ] 2>/dev/null; then
    CUTOFF=$(python3 -c "import datetime,sys; print((datetime.date.today()-datetime.timedelta(days=int(sys.argv[1]))).isoformat())" "$RETAIN" 2>/dev/null)
    if [ -n "$CUTOFF" ] && [ -d "$DEBRIEF_DIR/sessions/raw" ]; then
      for d in "$DEBRIEF_DIR/sessions/raw"/*; do
        [ -d "$d" ] || continue
        b=$(basename "$d")
        case "$b" in
          [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
            if [ "$b" \< "$CUTOFF" ]; then
              rm -rf "$d" && log "raw-archive: pruned $b (retain ${RETAIN}d, cutoff $CUTOFF)"
            fi
            ;;
        esac
      done
    fi
  fi
fi

spawn_drafter "$TRANSCRIPT" "$SESSION_ID" "$ENDED_AT" "$REASON" "$OUT"
log "spawned drafter for $SESSION_ID ($LINES lines, reason=$REASON) -> $OUT"
exit 0
