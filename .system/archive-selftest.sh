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
cp "$HERE/session-debrief.sh" "$HERE/redact-transcript.py" "$HERE/extract-material.py" \
   "$HERE/capture-status.py" "$HERE/memory-snapshot.py" "$TMP/.system/"
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

# ---- 5. a resumed session's older archive is dropped only when contained ----
rm -rf "$TMP/sessions/raw"
run_hook ffffffff 0
FULL="$(find "$TMP/sessions/raw" -name '*-ffffffff.jsonl.gz' | head -1)"
mkdir -p "$TMP/sessions/raw/2020-01-05"
gzip -dc "$FULL" | head -n 5 | gzip -c > "$TMP/sessions/raw/2020-01-05/0000-ffffffff.jsonl.gz"
echo '{"other":"history"}' | gzip -c > "$TMP/sessions/raw/2020-01-05/0001-ffffffff.jsonl.gz"
gzip -dc "$FULL" | head -n 5 | gzip -c > "$TMP/sessions/raw/2020-01-05/0002-99999999.jsonl.gz"
run_hook ffffffff 0
if [ ! -f "$TMP/sessions/raw/2020-01-05/0000-ffffffff.jsonl.gz" ]; then
  ok "dedupe: older prefix copy dropped"
else
  bad "dedupe: older prefix copy dropped" "prefix copy survived"
fi
if [ -f "$TMP/sessions/raw/2020-01-05/0001-ffffffff.jsonl.gz" ]; then
  ok "dedupe: non-prefix copy kept"
else
  bad "dedupe: non-prefix copy kept" "a copy with other bytes was deleted"
fi
if [ -f "$TMP/sessions/raw/2020-01-05/0002-99999999.jsonl.gz" ]; then
  ok "dedupe: other session untouched"
else
  bad "dedupe: other session untouched"
fi
if [ -n "$(find "$TMP/sessions/raw" -name '*-ffffffff.jsonl.gz' -newer "$TMP/sessions/raw/2020-01-05/0001-ffffffff.jsonl.gz")" ]; then
  ok "dedupe: newest archive kept"
else
  bad "dedupe: newest archive kept"
fi

# ---- 6. material: the originals leave the transcript as files ----------------
# A 1x1 PNG pasted by the human, and a design file the session wrote.
PNG="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
T="$TMP/design.jsonl"
: > "$T"
for i in $(seq 1 32); do
  printf '{"type":"assistant","i":%s,"content":"line %s"}\n' "$i" "$i" >> "$T"
done
printf '{"type":"user","message":{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"%s"}}]}}\n' "$PNG" >> "$T"
printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"w1","name":"Write","input":{"file_path":"/w/Header.tsx","content":"export const Header = () => null"}}]}}\n' >> "$T"
# A claude stub that keeps the prompt it was given.
printf '#!/bin/sh\nprintf "%%s" "$2" > "%s/prompt.txt"\nexit 0\n' "$TMP" > "$TMP/bin/claude"

printf '{"session_id":"%s","transcript_path":"%s","reason":"other"}' 12121212 "$T" \
  | bash "$HOOK" >/dev/null 2>&1
if [ ! -d "$TMP/sessions/material" ]; then
  ok "material: off unless DEBRIEF_MATERIAL is set"
else
  bad "material: off unless DEBRIEF_MATERIAL is set" "a folder was written"
fi

printf '{"session_id":"%s","transcript_path":"%s","reason":"other"}' 34343434 "$T" \
  | DEBRIEF_MATERIAL=1 bash "$HOOK" >/dev/null 2>&1
M="$TMP/sessions/material/34343434"
if [ -f "$M/manifest.json" ] && [ -n "$(find "$M/images" -name '*.png' 2>/dev/null)" ]; then
  ok "material: the pasted image is a file"
else
  bad "material: the pasted image is a file" "$(tail -3 "$TMP/.system/hook.log")"
fi
if grep -q "export const Header" "$M"/code/*Header.tsx 2>/dev/null; then
  ok "material: the design file's version is a file"
else
  bad "material: the design file's version is a file"
fi
# Both drafters run detached and write the same file: wait for this session's.
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  grep -q "^SESSION_ID: 34343434\$" "$TMP/prompt.txt" 2>/dev/null && break
  sleep 0.3
done
if grep -q "^MATERIAL: $M\$" "$TMP/prompt.txt" 2>/dev/null; then
  ok "material: the drafter is told where the originals are"
else
  bad "material: the drafter is told where the originals are" "$(tail -4 "$TMP/prompt.txt" 2>/dev/null)"
fi

# Wait until the detached drafter stub has written this session's prompt.
wait_prompt() {
  for _ in $(seq 1 30); do
    grep -q "^SESSION_ID: $1\$" "$TMP/prompt.txt" 2>/dev/null && return 0
    sleep 0.3
  done
  return 1
}
CAPTURE="$TMP/.system/capture_status.json"
stage_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],{}).get(sys.argv[3]))' "$CAPTURE" "$1" "$2" 2>/dev/null
}

# ---- 7. capture status: each stage marks itself -----------------------------
if [ "$(stage_field material failed_since_ok)" = 0 ] && [ "$(stage_field archive failed_since_ok)" = 0 ]; then
  ok "capture: archive and material marked ok"
else
  bad "capture: archive and material marked ok" "$(cat "$CAPTURE" 2>/dev/null)"
fi
# The stub drafter writes no draft, so the draft stage fails here, as it should.
case "$(python3 "$TMP/.system/capture-status.py" report)" in
  *archive*|*material*) bad "capture: report silent on stages that are fine" ;;
  *"draft ("*) ok "capture: report silent on stages that are fine, names the failing draft stage" ;;
  *) bad "capture: report names the failing draft stage" "$(python3 "$TMP/.system/capture-status.py" report)" ;;
esac
mv "$TMP/.system/redact-transcript.py" "$TMP/.system/redact-transcript.py.disabled"
run_hook 56565656
mv "$TMP/.system/redact-transcript.py.disabled" "$TMP/.system/redact-transcript.py"
case "$(python3 "$TMP/.system/capture-status.py" report)" in
  *"archive (1 failed run"*) ok "capture: a missing redactor reports the archive stage" ;;
  *) bad "capture: a missing redactor reports the archive stage" "$(python3 "$TMP/.system/capture-status.py" report)" ;;
esac
run_hook 57575757
if ! python3 "$TMP/.system/capture-status.py" report | grep -q archive; then
  ok "capture: the next good run clears it"
else
  bad "capture: the next good run clears it"
fi

# ---- 8. subagent transcripts go into the archive, redacted -------------------
P="$TMP/proj"
mkdir -p "$P/78787878/subagents/workflows/wf_1"
cp "$T" "$P/78787878.jsonl"
printf '{"type":"assistant","content":"DB_PASSWORD=subagentpw123"}\n' > "$P/78787878/subagents/agent-a1.jsonl"
printf '{"type":"assistant","content":"STRIPE_SECRET_KEY=wfagentkey12345"}\n' > "$P/78787878/subagents/workflows/wf_1/agent-b2.jsonl"
printf '{}' > "$P/78787878/subagents/agent-a1.meta.json"
printf '{"session_id":"78787878","transcript_path":"%s","reason":"other"}' "$P/78787878.jsonl" \
  | DEBRIEF_RAW_ARCHIVE=1 DEBRIEF_RAW_RETAIN_DAYS=0 bash "$HOOK" >/dev/null 2>&1
SUBD="$(find "$TMP/sessions/raw" -type d -name '*-78787878.subagents' | head -1)"
if [ -n "$SUBD" ] && [ -f "$SUBD/agent-a1.jsonl.gz" ] && [ -f "$SUBD/workflows/wf_1/agent-b2.jsonl.gz" ]; then
  ok "subagents: nested transcripts archived beside the main copy"
else
  bad "subagents: nested transcripts archived beside the main copy" "$(find "$TMP/sessions/raw" | tail -5)"
fi
if [ -n "$SUBD" ] && ! gzip -dc "$SUBD"/agent-a1.jsonl.gz "$SUBD"/workflows/wf_1/agent-b2.jsonl.gz | grep -qE 'subagentpw123|wfagentkey12345'; then
  ok "subagents: secrets redacted"
else
  bad "subagents: secrets redacted"
fi
# Same session again, one subagent grew: the older folder is contained, so dropped.
printf '{"type":"assistant","content":"more"}\n' >> "$P/78787878/subagents/agent-a1.jsonl"
mkdir -p "$TMP/sessions/raw/2020-01-07"
mv "$SUBD" "$TMP/sessions/raw/2020-01-07/0000-78787878.subagents"
printf '{"session_id":"78787878","transcript_path":"%s","reason":"other"}' "$P/78787878.jsonl" \
  | DEBRIEF_RAW_ARCHIVE=1 DEBRIEF_RAW_RETAIN_DAYS=0 bash "$HOOK" >/dev/null 2>&1
if [ ! -d "$TMP/sessions/raw/2020-01-07/0000-78787878.subagents" ] \
   && [ -n "$(find "$TMP/sessions/raw" -type d -name '*-78787878.subagents')" ]; then
  ok "subagents: an older contained copy is dropped, the new one kept"
else
  bad "subagents: an older contained copy is dropped, the new one kept"
fi

# ---- 9. memory notes: changed ones land in the session's material -------------
mkdir -p "$P/memory"
printf '# Memory Index\n' > "$P/memory/MEMORY.md"
printf 'Keep it short.\n' > "$P/memory/a-note.md"
cp "$T" "$P/90909090.jsonl"
printf '{"session_id":"90909090","transcript_path":"%s","reason":"other"}' "$P/90909090.jsonl" \
  | DEBRIEF_MATERIAL=1 bash "$HOOK" >/dev/null 2>&1
if python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["changed"]==["MEMORY.md","a-note.md"]' \
     "$TMP/sessions/material/90909090/memory/index.json" 2>/dev/null; then
  ok "memory: first session stores every note"
else
  bad "memory: first session stores every note" "$(tail -2 "$TMP/.system/hook.log")"
fi
cp "$T" "$P/91919191.jsonl"
printf '{"session_id":"91919191","transcript_path":"%s","reason":"other"}' "$P/91919191.jsonl" \
  | DEBRIEF_MATERIAL=1 bash "$HOOK" >/dev/null 2>&1
if [ ! -d "$TMP/sessions/material/91919191/memory" ]; then
  ok "memory: unchanged notes write nothing"
else
  bad "memory: unchanged notes write nothing"
fi

# ---- 10. headless sessions are tagged; interactive ones are not ---------------
mk_session() {  # sid entrypoint
  local f="$P/$1.jsonl"
  printf '{"type":"user","entrypoint":"%s","message":{"role":"user","content":"do the thing"}}\n' "$2" > "$f"
  cat "$T" >> "$f"
  printf '{"session_id":"%s","transcript_path":"%s","reason":"other"}' "$1" "$f" | bash "$HOOK" >/dev/null 2>&1
}
mk_session a1a1a1a1 sdk-cli
wait_prompt a1a1a1a1
if grep -q '^SYNTHETIC: headless$' "$TMP/prompt.txt" \
   && grep '"session_id": "a1a1a1a1"' "$TMP/sessions/queue.jsonl" | grep -q '"synthetic": "headless"'; then
  ok "headless: queue entry and drafter input carry synthetic=headless"
else
  bad "headless: queue entry and drafter input carry synthetic=headless"
fi
mk_session b2b2b2b2 cli
wait_prompt b2b2b2b2
if ! grep -q '^SYNTHETIC:' "$TMP/prompt.txt" \
   && ! grep '"session_id": "b2b2b2b2"' "$TMP/sessions/queue.jsonl" | grep -q synthetic; then
  ok "headless: an interactive session is not tagged"
else
  bad "headless: an interactive session is not tagged"
fi

# ---- 11. verified reactions reach the drafter ---------------------------------
cat > "$TMP/.system/debrief-react.py" <<'PY'
import json, os, sys
a = sys.argv[1:]
sid, d = a[a.index("--session") + 1], a[a.index("--dir") + 1]
os.makedirs(os.path.join(d, "sessions", "reactions"), exist_ok=True)
with open(os.path.join(d, "sessions", "reactions", sid[:8] + ".jsonl"), "w") as f:
    f.write(json.dumps({"words": "this one is right", "about": "the banner", "sentiment": "positive",
                        "kind": "kept", "verified": True}) + "\n")
    f.write(json.dumps({"words": "never said this", "verified": False}) + "\n")
PY
mk_session c3c3c3c3 cli
wait_prompt c3c3c3c3
if grep -q 'this one is right' "$TMP/prompt.txt" && ! grep -q 'never said this' "$TMP/prompt.txt" \
   && [ "$(stage_field reactions failed_since_ok)" = 0 ]; then
  ok "reactions: verified ones inline, unverified left out, stage marked"
else
  bad "reactions: verified ones inline, unverified left out, stage marked" "$(tail -5 "$TMP/prompt.txt")"
fi
rm -f "$TMP/.system/debrief-react.py"

# ---- 12. every helper missing: the session is still queued ------------------
for h in capture-status.py memory-snapshot.py extract-material.py; do mv "$TMP/.system/$h" "$TMP/.system/$h.off"; done
mk_session d4d4d4d4 cli
for h in capture-status.py memory-snapshot.py extract-material.py; do mv "$TMP/.system/$h.off" "$TMP/.system/$h"; done
if grep -q '"session_id": "d4d4d4d4"' "$TMP/sessions/queue.jsonl"; then
  ok "missing helpers: the session is still queued"
else
  bad "missing helpers: the session is still queued" "$(tail -3 "$TMP/.system/hook.log")"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "ALL PASS"
  exit 0
fi
echo "$fails FAILED"
exit 1
