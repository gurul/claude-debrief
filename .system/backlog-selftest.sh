#!/usr/bin/env bash
# Integration selftest for the backlog-surfacing loop. The pure-computation
# invariants live in `debrief-backlog.py selftest` (run first, below); this
# script covers what only a shell can drive end to end: the SessionEnd sweep's
# archived-draft heal, and the SessionStart nudge's source guard, once-per-day
# stamp, silence-on-clean-queue, and additionalContext JSON.
#
# Everything runs against a synthetic debrief tree in a tempdir with COPIES of
# the real scripts placed at <tmp>/.system/, so each resolves DEBRIEF_DIR to the
# tempdir from its own location — exactly as a live install does. No network,
# no `claude` spawn (empty stdin makes the SessionEnd hook skip its main body).
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
fails=0
ok()  { printf 'ok %s\n' "$1"; }
bad() { printf 'FAIL %s%s\n' "$1" "${2:+: $2}"; fails=$((fails + 1)); }

# ---- 1. pure-computation invariants ---------------------------------------
echo "== debrief-backlog.py selftest =="
if python3 "$HERE/debrief-backlog.py" selftest; then
  ok "python invariants"
else
  bad "python invariants"
fi
echo

echo "== integration =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.system" "$TMP/sessions"
cp "$HERE/debrief-backlog.py" "$HERE/backlog-nudge.sh" "$HERE/session-debrief.sh" "$TMP/.system/"
NUDGE="$TMP/.system/backlog-nudge.sh"
HOOK="$TMP/.system/session-debrief.sh"
STAMP="$TMP/.system/.backlog-nudge"
QUEUE="$TMP/sessions/queue.jsonl"
status_of() { python3 -c 'import json,sys;print(json.loads(open(sys.argv[1]).read().strip().splitlines()[0])["status"])' "$1"; }

# ---- 2. SessionEnd sweep heals an archived draft to aggregated -------------
# Day mode MOVES a consumed draft into archive/, emptying the original path.
# The sweep must read that as "aggregated", not respawn a dead drafter.
D=2026-02-01
mkdir -p "$TMP/sessions/$D" "$TMP/sessions/archive/$D"
echo "archived body" > "$TMP/sessions/archive/$D/1200-deadbeef.md"   # draft path itself absent
printf '{"status":"pending","ended_at":"%sT12:00:00-0700","draft":"%s","transcript_path":"%s","session_id":"deadbeef","reason":"t"}\n' \
  "$D" "$TMP/sessions/$D/1200-deadbeef.md" "$TMP/nope.jsonl" > "$QUEUE"
printf '' | bash "$HOOK" >/dev/null 2>&1 || true   # empty stdin -> main body skips; sweep runs
healed="$(status_of "$QUEUE")"
if [ "$healed" = "aggregated" ]; then ok "sweep heals archived draft -> aggregated"; else bad "heal" "status=$healed"; fi

# ---- 3. clean queue: nudge silent, exit 0, no stamp (stays armed) ----------
rm -f "$STAMP"
printf '{"status":"aggregated","ended_at":"2026-02-01T12:00:00-0700","draft":"%s/sessions/2026-02-01/x.md"}\n' "$TMP" > "$QUEUE"
out="$(printf '{"source":"startup"}' | bash "$NUDGE" 2>/dev/null)"
if [ -z "$out" ]; then ok "clean queue: nudge silent"; else bad "clean-silent" "$out"; fi
if [ ! -f "$STAMP" ]; then ok "clean queue: no stamp written (stays armed)"; else bad "clean-nostamp"; fi

# ---- 4. backlog + startup: valid additionalContext JSON, stamped ----------
mkdir -p "$TMP/sessions/2026-02-02"
echo body > "$TMP/sessions/2026-02-02/0900-cafef00d.md"
printf '{"status":"unconsumed","ended_at":"2026-02-02T09:00:00-0700","draft":"%s/sessions/2026-02-02/0900-cafef00d.md"}\n' "$TMP" > "$QUEUE"
rm -f "$STAMP"
before="$(cksum "$QUEUE")"
out="$(printf '{"source":"startup"}' | bash "$NUDGE" 2>/dev/null)"
if printf '%s' "$out" | python3 -c '
import json,sys
d=json.load(sys.stdin)
o=d["hookSpecificOutput"]
assert o["hookEventName"]=="SessionStart", o
assert "DEBRIEF BACKLOG" in o["additionalContext"], o
assert "2026-02-02" in o["additionalContext"], o
' 2>/dev/null; then ok "startup: additionalContext is well-formed and names the backlog"; else bad "context" "$out"; fi
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$(date +%Y-%m-%d)" ]; then ok "startup: stamped today"; else bad "stamp"; fi
if [ "$(cksum "$QUEUE")" = "$before" ]; then ok "gate: nudge left queue byte-identical"; else bad "queue-mutated"; fi

# ---- 5. once-per-day: a second startup the same day is silent -------------
out2="$(printf '{"source":"startup"}' | bash "$NUDGE" 2>/dev/null)"
if [ -z "$out2" ]; then ok "once-per-day: second startup silent"; else bad "throttle" "$out2"; fi

# ---- 6. source guard: compact/resume silent even with backlog + no stamp --
rm -f "$STAMP"
outc="$(printf '{"source":"compact"}' | bash "$NUDGE" 2>/dev/null)"
if [ -z "$outc" ]; then ok "source guard: compact silent"; else bad "compact" "$outc"; fi
outr="$(printf '{"source":"resume"}' | bash "$NUDGE" 2>/dev/null)"
if [ -z "$outr" ]; then ok "source guard: resume silent"; else bad "resume" "$outr"; fi
if [ ! -f "$STAMP" ]; then ok "source guard: non-startup writes no stamp"; else bad "guard-stamp"; fi

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "$fails FAILED"; exit 1; fi
