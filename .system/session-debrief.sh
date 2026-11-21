#!/usr/bin/env bash
# SessionEnd hook for the debrief system (github.com/gurul/claude-debrief).
# Receives Claude Code SessionEnd JSON on stdin, queues the session in
# sessions/queue.jsonl, and spawns a detached headless drafter that writes a
# machine-draft session debrief into sessions/<date>/.
# Must always exit 0 — SessionEnd is non-blocking and errors are ours to log.
set -u

SYSTEM_DIR="$(cd "$(dirname "$0")" && pwd)"
DEBRIEF_DIR="$(dirname "$SYSTEM_DIR")"
LOG="$SYSTEM_DIR/hook.log"
QUEUE="$DEBRIEF_DIR/sessions/queue.jsonl"
# As a global hook this fires from any project, so the drafter and curator
# run from the memory's root, where every debrief/ path resolves.
cd "$(dirname "$DEBRIEF_DIR")" || exit 0

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$LOG"; }
# Rotate past 1 MB: keep one previous file, so the log never grows without bound.
[ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 1048576 ] && mv -f "$LOG" "$LOG.1"

# Each stage records ok or fail in .system/capture_status.json, and the
# SessionStart hook says so while one is failing. hook.log alone was too
# loud to notice a stage that failed for weeks. A missing helper is a no-op.
mark() {
  [ -f "$SYSTEM_DIR/capture-status.py" ] \
    && python3 "$SYSTEM_DIR/capture-status.py" mark "$@" >/dev/null 2>&1
  return 0
}

# Cold archive (DEBRIEF_RAW_ARCHIVE=1). Defined here and called BEFORE the
# triviality gate: every session that produced a transcript is archived, even
# the short ones. Triviality decides whether a session is worth DRAFTING, not
# whether it is worth keeping — a three-line session that broke production is
# exactly the one you want the verbatim record of.
archive_raw() {
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
      mark archive fail "redactor missing"
    else
      mkdir -p "$RAW_DIR"
      # Pipe, but check the redactor's status rather than gzip's: with a plain
      # pipe gzip happily succeeds on a truncated stream, which would bank a
      # partial archive as if it were whole.
      if REDACT_LOG=$(python3 "$REDACTOR" "$TRANSCRIPT" 2>&1 >"$RAW_DIR/.tmp.$$"); then
        if gzip -c "$RAW_DIR/.tmp.$$" >"$RAW_OUT" 2>>"$LOG"; then
          log "raw-archive: $(basename "$RAW_OUT") ($DATE) [${REDACT_LOG:-no matches}]"
          ARCHIVE_STATE=ok
          # Subagent transcripts sit in <transcript minus .jsonl>/subagents/,
          # nested for workflows. The extractor reads them, and Claude Code
          # deletes them with the main transcript, so they go into the archive
          # too: redacted file by file, beside the main copy, same retention.
          # DEBRIEF_RAW_SUBAGENTS=0 leaves them out (they can be large).
          SUB_SRC="${TRANSCRIPT%.jsonl}/subagents"
          SUB_OUT=""
          if [ -d "$SUB_SRC" ] && [ "${DEBRIEF_RAW_SUBAGENTS:-1}" != 0 ]; then
            SUB_OUT="${RAW_OUT%.jsonl.gz}.subagents"
            SUB_LOG=$(python3 "$REDACTOR" --tree "$SUB_SRC" "$SUB_OUT.tmp.$$" 2>&1)
            SUB_RC=$?
            # Files that failed were left out, never copied raw; keep the rest.
            rm -rf "$SUB_OUT"
            if [ -d "$SUB_OUT.tmp.$$" ]; then mv "$SUB_OUT.tmp.$$" "$SUB_OUT"; else SUB_OUT=""; fi
            log "raw-archive: subagents ${SUB_LOG//$'\n'/ | }"
            [ "$SUB_RC" = 0 ] || { ARCHIVE_STATE=fail; mark archive fail "subagent redaction: $SUB_LOG"; }
          fi
          # A resumed session archives again at each exit, and each copy holds the
          # last one. Drop an older copy only when it is a byte prefix of this one,
          # so the newest archive still holds every byte. Anything else is kept.
          # An older subagent folder goes only when every file in it is a prefix
          # of the same file in the new folder.
          DEDUP=$(python3 - "$DEBRIEF_DIR/sessions/raw" "${SESSION_ID:0:8}" "$RAW_OUT" "$RAW_DIR/.tmp.$$" "$SUB_OUT" <<'PY' 2>&1
import glob, gzip, os, shutil, sys
raw, sid, keep, new, sub = sys.argv[1:6]
data = open(new, "rb").read()
for f in glob.glob(os.path.join(raw, "*", "*-%s.jsonl.gz" % sid)):
    if os.path.abspath(f) == os.path.abspath(keep):
        continue
    try:
        if data.startswith(gzip.open(f).read()):
            os.remove(f)
            print("dropped " + os.path.basename(f))
    except OSError:
        pass
if sub:
    for d in glob.glob(os.path.join(raw, "*", "*-%s.subagents" % sid)):
        if os.path.abspath(d) == os.path.abspath(sub):
            continue
        try:
            contained = True
            for root, _, names in os.walk(d):
                for n in names:
                    rel = os.path.relpath(os.path.join(root, n), d)
                    mine = os.path.join(sub, rel)
                    if not (os.path.exists(mine) and gzip.open(mine).read().startswith(
                            gzip.open(os.path.join(root, n)).read())):
                        contained = False
            if contained:
                shutil.rmtree(d)
                print("dropped " + os.path.basename(d))
        except (OSError, EOFError):
            pass
PY
) || true
          [ -n "$DEDUP" ] && log "raw-archive: ${DEDUP//$'\n'/, } (prefix of $(basename "$RAW_OUT"))"
          [ "$ARCHIVE_STATE" = ok ] && mark archive ok
        else
          log "raw-archive: gzip failed for $SESSION_ID"
          mark archive fail "gzip failed"
          rm -f "$RAW_OUT"
        fi
      else
        log "raw-archive: SKIPPED — redactor failed for $SESSION_ID: $REDACT_LOG"
        mark archive fail "redactor failed: $REDACT_LOG"
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
}

# Material (DEBRIEF_MATERIAL=1): lift the session's originals out of the
# transcript into sessions/material/<sid8>/ as plain files: the images it saw,
# each version of a design file it wrote, the pages it published, long text
# and links the human gave it. A summary is one reading of a session, and the
# next agent can only check that reading against something it can open.
# Read from the live transcript, before redaction: a redaction pattern can
# land inside base64 and damage an image. Text is redacted on the way out by
# extract-material.py itself, which writes no text when the redactor is gone.
# Never indexed: reached from a curated note through `debrief-search.py source`.
keep_material() {
  [ -n "${DEBRIEF_MATERIAL:-}" ] || return 0
  local tool="$SYSTEM_DIR/extract-material.py" snap="" out
  [ -f "$tool" ] || { log "material: SKIPPED, extract-material.py missing"; mark material fail "extract-material.py missing"; return 0; }
  # The files on disk belong to this session only while it is fresh. The
  # extractor decides that itself, from the last real message in the main
  # and subagent transcripts: the file's modified time drifts, because the
  # harness appends bookkeeping records after the last message.
  snap="--snapshot"
  if out=$(python3 "$tool" "$TRANSCRIPT" "$DEBRIEF_DIR/sessions/material" --session "$SESSION_ID" $snap 2>&1); then
    log "material: ${SESSION_ID:0:8} ${out}"
    mark material ok
  else
    log "material: FAILED for ${SESSION_ID:0:8}: ${out//$'\n'/ | }"
    mark material fail "$out"
  fi
}

# Memory-note history (with DEBRIEF_MATERIAL): each changed auto-memory note
# of this session's project, redacted, into sessions/material/<sid8>/memory/.
# Same freshness rule as the snapshot above: days later, the folder holds
# what later sessions wrote, not what this one left. Other tools may add
# their own folders beside memory/; nothing here assumes a fixed set.
keep_memory() {
  [ -n "${DEBRIEF_MATERIAL:-}" ] || return 0
  local tool="$SYSTEM_DIR/memory-snapshot.py" age out
  [ -f "$tool" ] || return 0
  age=$(( $(date +%s) - $(date -r "$TRANSCRIPT" +%s) ))
  [ "$age" -lt 3600 ] || return 0
  if out=$(python3 "$tool" "$TRANSCRIPT" "$DEBRIEF_DIR/sessions/material/${SESSION_ID:0:8}" 2>&1); then
    case "$out" in
      SKIPPED*) log "memory: ${SESSION_ID:0:8} $out"; mark memory fail "$out" ;;
      unchanged|"no memory folder") mark memory ok ;;
      *) log "memory: ${SESSION_ID:0:8} $out"; mark memory ok ;;
    esac
  else
    log "memory: FAILED for ${SESSION_ID:0:8}: ${out//$'\n'/ | }"
    mark memory fail "$out"
  fi
}

# Reactions: debrief-react.py checks the reactions captured during the
# session against the transcript and leaves the verified ones in
# sessions/reactions/<sid8>.jsonl for the drafter. Absent script: no stage.
verify_reactions() {
  local tool="$SYSTEM_DIR/debrief-react.py" out
  [ -f "$tool" ] || return 0
  if out=$(python3 "$tool" verify --transcript "$TRANSCRIPT" --session "$SESSION_ID" --dir "$DEBRIEF_DIR" 2>&1); then
    [ -n "$out" ] && log "reactions: ${SESSION_ID:0:8} ${out//$'\n'/ | }"
    mark reactions ok
  else
    log "reactions: FAILED for ${SESSION_ID:0:8}: ${out//$'\n'/ | }"
    mark reactions fail "$out"
  fi
}

# Signals: debrief-signals.py reads every human turn by rule and leaves
# sessions/signals/<sid8>.json: which turns no recorded reaction covers, the
# standing rules the human stated, and what the agent claimed or checked just
# before he spoke. The drafter gets the block; `adopt` then checks what the
# drafter found against the human's own turns. A headless session has no
# human turns, so it is not read. Absent script: no stage.
read_signals() {
  local tool="$SYSTEM_DIR/debrief-signals.py" out
  [ -f "$tool" ] || return 0
  [ "$(session_origin "$TRANSCRIPT")" = headless ] && return 0
  out=$(python3 "$tool" read --transcript "$TRANSCRIPT" --session "$SESSION_ID" --dir "$DEBRIEF_DIR" 2>&1)
  case "$out" in
    "signals: FAILED"*|"")
      log "signals: FAILED for ${SESSION_ID:0:8}: ${out//$'\n'/ | }"
      mark signals fail "${out:-no output}" ;;
    *) log "signals: ${SESSION_ID:0:8} ${out#signals: }"; mark signals ok ;;
  esac
}

# Who wrote the prompts. The first user record's `entrypoint` names the
# client: `cli` and `claude-desktop` are a person at a keyboard; `sdk-cli`
# (claude -p) and `sdk-ts` (the agent SDK) are a script. A record that says
# origin.kind "human" is a person whatever the client. No entrypoint (older
# transcripts): unknown, so no tag. Prints "headless" or nothing.
session_origin() {
  python3 - "$1" <<'PY' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1], errors="replace") as f:
        for i, line in enumerate(f):
            if i > 400:
                break
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "user":
                continue
            ep = d.get("entrypoint")
            origin = d.get("origin") if isinstance(d.get("origin"), dict) else {}
            if ep and ep not in ("cli", "claude-desktop") and origin.get("kind") != "human":
                print("headless")
            break
except Exception:
    pass
PY
}

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
  # The drafter links the originals it can see. No folder, no parameter.
  local material="$DEBRIEF_DIR/sessions/material/${sid:0:8}"
  [ -f "$material/manifest.json" ] && prompt="$prompt
MATERIAL: $material"
  [ "$(session_origin "$transcript")" = headless ] && prompt="$prompt
SYNTHETIC: headless"
  # The human's verified reactions, inline, so the drafter cannot miss them.
  local reactions="$DEBRIEF_DIR/sessions/reactions/${sid:0:8}.jsonl" verified=""
  if [ -s "$reactions" ]; then
    verified="$(python3 - "$reactions" <<'PY' 2>/dev/null
import json, sys
keep = ("id", "words", "about", "sentiment", "kind", "before", "after", "project", "at")
n = 0
for line in open(sys.argv[1], errors="replace"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    # What the drafter itself found on an earlier attempt is not handed back to it.
    if d.get("verified") is True and d.get("words") and d.get("source") != "drafter" and n < 60:
        print(json.dumps({k: d.get(k) for k in keep}, ensure_ascii=False))
        n += 1
PY
)"
  fi
  [ -n "$verified" ] && prompt="$prompt
REACTIONS: $reactions
VERIFIED REACTIONS (one JSON object per line):
$verified"
  # What the transcript shows by rule: turns no reaction covers, stated rules,
  # and what the agent claimed or checked before each turn.
  local signals=""
  [ -f "$SYSTEM_DIR/debrief-signals.py" ] && [ "$(session_origin "$transcript")" != headless ] \
    && signals="$(python3 "$SYSTEM_DIR/debrief-signals.py" prompt --session "$sid" --dir "$DEBRIEF_DIR" 2>/dev/null)"
  [ -n "$signals" ] && prompt="$prompt
$signals"
  # Opt-in auto-curation (DEBRIEF_AUTO_CURATE=1): once the draft lands,
  # curate-day.sh folds it into its day with no review step. If that never
  # runs (shutdown, crash), debrief-daemon.sh catches the day up.
  nohup env DEBRIEF_GENERATION=1 bash -c '
    claude -p "$1" --allowedTools "Read,Glob,Grep,Write" >>"$2" 2>&1
    rc=$?
    state=MISSING; [ -f "$4" ] && state=present
    printf "%s drafter for %s exited rc=%s (draft %s)\n" \
      "$(date "+%Y-%m-%dT%H:%M:%S%z")" "$3" "$rc" "$state" >>"$2"
    if [ -f "$5/capture-status.py" ]; then
      if [ "$state" = present ]; then
        python3 "$5/capture-status.py" mark draft ok >/dev/null 2>&1
      else
        python3 "$5/capture-status.py" mark draft fail "drafter exited rc=$rc, no draft" >/dev/null 2>&1
      fi
    fi
    # The reactions the drafter found count only when their words are in a
    # human turn: adopt checks that before the curator reads the draft.
    if [ "$state" = present ] && [ -f "$5/debrief-signals.py" ] && [ -f "$9/sessions/signals/${3:0:8}.json" ]; then
      got="$(python3 "$5/debrief-signals.py" adopt --draft "$4" --transcript "$8" --session "$3" --dir "$9" 2>&1)"
      printf "%s %s for %s\n" "$(date "+%Y-%m-%dT%H:%M:%S%z")" "${got:-signals: adopt printed nothing}" "${3:0:8}" >>"$2"
    fi
    [ "$state" = present ] && [ "$7" = 1 ] && bash "$5/curate-day.sh" "$6"
  ' _ "$prompt" "$LOG" "$sid" "$out" "$SYSTEM_DIR" "${ended%%T*}" "${DEBRIEF_AUTO_CURATE:-}" "$transcript" "$DEBRIEF_DIR" >/dev/null 2>&1 &
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
            ended = datetime.strptime(d.get("queued_at") or d["ended_at"], "%Y-%m-%dT%H:%M:%S%z")
            unread = (now - ended) > timedelta(hours=24)
        except Exception:
            unread = True
        if unread:
            d["status"] = "unconsumed"
    if d.get("status") == "pending" and not os.path.exists(draft):
        try:
            ended = datetime.strptime(d.get("queued_at") or d["ended_at"], "%Y-%m-%dT%H:%M:%S%z")
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

# The daemon runs this hook every 15 minutes for the sweep alone, with no
# input. Stop here quietly: logging that as a parse failure was most of
# hook.log.
[ -n "${DEBRIEF_SWEEP_ONLY:-}" ] && exit 0

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

# Optional scope (DEBRIEF_PROJECT_MATCH): a shell glob on the transcript's
# ~/.claude/projects/<dir> name. A session from any other project is ignored.
if [ -n "${DEBRIEF_PROJECT_MATCH:-}" ]; then
  # shellcheck disable=SC2254
  case "$(basename "$(dirname "$TRANSCRIPT")")" in
    $DEBRIEF_PROJECT_MATCH) ;;
    *) exit 0 ;;
  esac
fi

# Stamp from the transcript's last write, not the clock: the same at a live
# SessionEnd, and the right day when the daemon backfills an old session.
ENDED_AT="$(date -r "$TRANSCRIPT" '+%Y-%m-%dT%H:%M:%S%z')"
DATE="$(date -r "$TRANSCRIPT" +%Y-%m-%d)"
TIME="$(date -r "$TRANSCRIPT" +%H%M)"

# Archive first, unconditionally. Nothing below this line may cause a session
# to go unrecorded.
archive_raw
keep_material
keep_memory
verify_reactions
read_signals
SYNTHETIC="$(session_origin "$TRANSCRIPT")"

LINES=$(wc -l <"$TRANSCRIPT" | tr -d ' ')
if [ "$LINES" -lt 30 ]; then
  log "skip-draft: trivial session ($SESSION_ID, $LINES lines) — archived, not drafted"
  exit 0
fi
OUT_DIR="$DEBRIEF_DIR/sessions/$DATE"
OUT="$OUT_DIR/${TIME}-${SESSION_ID:0:8}.md"
mkdir -p "$OUT_DIR"

# Durable record even if the drafter fails. One JSON object per line.
SESSION_ID="$SESSION_ID" TRANSCRIPT="$TRANSCRIPT" REASON="$REASON" \
ENDED_AT="$ENDED_AT" QUEUED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')" DRAFT="$OUT" \
SYNTHETIC="$SYNTHETIC" python3 -c '
import json, os
d = {
    "session_id": os.environ["SESSION_ID"],
    "transcript_path": os.environ["TRANSCRIPT"],
    "ended_at": os.environ["ENDED_AT"],
    "queued_at": os.environ["QUEUED_AT"],
    "reason": os.environ["REASON"],
    "draft": os.environ["DRAFT"],
    "status": "pending",
}
# A script wrote this session'"'"'s prompts; the drafter is told the same.
if os.environ.get("SYNTHETIC"):
    d["synthetic"] = os.environ["SYNTHETIC"]
print(json.dumps(d))
' >>"$QUEUE"


spawn_drafter "$TRANSCRIPT" "$SESSION_ID" "$ENDED_AT" "$REASON" "$OUT"
log "spawned drafter for $SESSION_ID ($LINES lines, reason=$REASON) -> $OUT"
exit 0
