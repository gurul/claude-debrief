#!/usr/bin/env bash
# Integration selftest for the verbatim-archive path (DEBRIEF_RAW_ARCHIVE=1).
#
# The pure redaction invariants live in `redact-transcript.py --selftest` (run
# first, below). This script covers what only a shell can drive end to end:
# that the SessionEnd hook actually pipes through the redactor before gzip,
# that it fails CLOSED when the redactor is unavailable, and that the retention
# horizon prunes by directory name.
#
# Same harness as backlog-selftest.sh: a synthetic debrief tree in a tempdir
# with COPIES of the real scripts at <tmp>/.system/, so the hook resolves
# DEBRIEF_DIR to the tempdir from its own location, exactly as a live install
# does. `claude` is stubbed on PATH so the drafter spawn is inert — this test is
# about bytes on disk, not about drafting.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
fails=0
ok()  { printf 'ok %s\n' "$1"; }
bad() { printf 'FAIL %s%s\n' "$1" "${2:+: $2}"; fails=$((fails + 1)); }

# ---- 1. pure redaction invariants -----------------------------------------
echo "== redact-transcript.py selftest =="
if python3 "$HERE/redact-transcript.py" --selftest >/dev/null 2>&1; then
  ok "redaction invariants"
else
  bad "redaction invariants" "run: python3 .system/redact-transcript.py --selftest"
fi
echo

echo "== integration =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.system" "$TMP/sessions" "$TMP/bin"
cp "$HERE/session-debrief.sh" "$HERE/redact-transcript.py" "$TMP/.system/"
cp "$HERE/prompts/session-draft.md" "$TMP/.system/" 2>/dev/null || true
mkdir -p "$TMP/.system/prompts"
cp "$HERE/prompts/session-draft.md" "$TMP/.system/prompts/" 2>/dev/null \
  || echo "draft prompt" > "$TMP/.system/prompts/session-draft.md"
HOOK="$TMP/.system/session-debrief.sh"

# Inert `claude`, so spawn_drafter cannot reach the network or cost anything.
printf '#!/bin/sh\nexit 0\n' > "$TMP/bin/claude"
chmod +x "$TMP/bin/claude"
export PATH="$TMP/bin:$PATH"

# A transcript that is (a) >=30 lines so the hook treats it as substantive, and
# (b) carrying the exact leak shape observed in the wild: a JWT inside a
# third-party log line, plus a password-style env assignment.
JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
T="$TMP/transcript.jsonl"
: > "$T"
for i in $(seq 1 32); do
  printf '{"type":"assistant","i":%s,"content":"ordinary reasoning line %s"}\n' "$i" "$i" >> "$T"
done
printf '{"type":"tool_result","content":"GET /rtc/v1?access_token=%s"}\n' "$JWT" >> "$T"
printf '{"type":"tool_result","content":"DB_PASSWORD=sup3rs3cretvalue"}\n' >> "$T"

run_hook() {
  printf '{"session_id":"%s","transcript_path":"%s","reason":"other"}' "$1" "$T" \
    | DEBRIEF_RAW_ARCHIVE=1 DEBRIEF_RAW_RETAIN_DAYS="${2:-180}" bash "$HOOK" >/dev/null 2>&1
}

# ---- 2. the archive lands, redacted ---------------------------------------
run_hook aaaaaaaa
GZ="$(find "$TMP/sessions/raw" -name '*.jsonl.gz' 2>/dev/null | head -1)"
if [ -n "$GZ" ]; then
  ok "archive: gzipped transcript written"
else
  bad "archive: gzipped transcript written" "nothing under sessions/raw/"
fi

if [ -n "$GZ" ]; then
  BODY="$(gzip -dc "$GZ")"
  case "$BODY" in
    *"$JWT"*) bad "archive: JWT redacted" "token survived into the archive" ;;
    *) ok "archive: JWT redacted" ;;
  esac
  case "$BODY" in
    *sup3rs3cretvalue*) bad "archive: password redacted" "value survived" ;;
    *) ok "archive: password redacted" ;;
  esac
  case "$BODY" in
    *"[REDACTED:"*) ok "archive: redaction markers present" ;;
    *) bad "archive: redaction markers present" ;;
  esac
  case "$BODY" in
    *"ordinary reasoning line 7"*) ok "archive: non-secret content preserved" ;;
    *) bad "archive: non-secret content preserved" ;;
  esac
  # Structure is the whole reason the archive exists (day-mode fallback parses it).
  if printf '%s\n' "$BODY" | python3 -c '
import json,sys
bad=0
for l in sys.stdin:
    l=l.strip()
    if not l: continue
    try: json.loads(l)
    except Exception: bad+=1
sys.exit(1 if bad else 0)'; then
    ok "archive: every line is valid JSON"
  else
    bad "archive: every line is valid JSON"
  fi
fi

# ---- 3. fail closed when the redactor is unavailable ----------------------
rm -rf "$TMP/sessions/raw"
mv "$TMP/.system/redact-transcript.py" "$TMP/.system/redact-transcript.py.disabled"
run_hook bbbbbbbb
if [ -z "$(find "$TMP/sessions/raw" -name '*.jsonl.gz' 2>/dev/null)" ]; then
  ok "fail-closed: no redactor ⇒ no archive"
else
  bad "fail-closed: no redactor ⇒ no archive" "an UNREDACTED archive was written"
fi
mv "$TMP/.system/redact-transcript.py.disabled" "$TMP/.system/redact-transcript.py"

# ---- 4. retention prunes by directory name -------------------------------
rm -rf "$TMP/sessions/raw"
mkdir -p "$TMP/sessions/raw/2020-01-01" "$TMP/sessions/raw/$(date +%Y-%m-%d)"
echo x > "$TMP/sessions/raw/2020-01-01/old.jsonl.gz"
run_hook cccccccc 30
if [ ! -d "$TMP/sessions/raw/2020-01-01" ]; then
  ok "retention: stale date dir pruned"
else
  bad "retention: stale date dir pruned" "2020-01-01 survived"
fi
if [ -d "$TMP/sessions/raw/$(date +%Y-%m-%d)" ]; then
  ok "retention: today's dir kept"
else
  bad "retention: today's dir kept"
fi

# Name-based, not mtime-based: touching an old archive must not extend its life.
rm -rf "$TMP/sessions/raw"
mkdir -p "$TMP/sessions/raw/2020-01-02"
echo x > "$TMP/sessions/raw/2020-01-02/old.jsonl.gz"
touch "$TMP/sessions/raw/2020-01-02" "$TMP/sessions/raw/2020-01-02/old.jsonl.gz"
run_hook dddddddd 30
if [ ! -d "$TMP/sessions/raw/2020-01-02" ]; then
  ok "retention: freshly-touched stale dir still pruned (name, not mtime)"
else
  bad "retention: freshly-touched stale dir still pruned (name, not mtime)"
fi

# Retention 0 disables pruning entirely.
rm -rf "$TMP/sessions/raw"
mkdir -p "$TMP/sessions/raw/2020-01-03"
echo x > "$TMP/sessions/raw/2020-01-03/old.jsonl.gz"
run_hook eeeeeeee 0
if [ -d "$TMP/sessions/raw/2020-01-03" ]; then
  ok "retention: 0 disables pruning"
else
  bad "retention: 0 disables pruning" "pruned despite RETAIN=0"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "ALL PASS"
  exit 0
fi
echo "$fails FAILED"
exit 1
