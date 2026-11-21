#!/usr/bin/env bash
# Integration selftest for the signals stage of the SessionEnd hook.
#
# The pattern and file rules live in `debrief-signals.py --selftest` (run
# first, below). This script covers what only the hook can show end to end:
# that a session end writes sessions/signals/<sid8>.json, that the drafter's
# prompt carries the block, that a reaction the drafter finds is added only
# when its words are in a human turn, that a headless session is not read,
# that a store without the script still drafts, and that dream.sh hands the
# principles report to the dream pass.
#
# Same harness as archive-selftest.sh: copies of the real scripts in a
# tempdir, and a stub `claude` on PATH. The stub keeps the prompt it was
# given and writes a draft, so no model is called.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
fails=0
ok()  { printf 'ok %s\n' "$1"; }
bad() { printf 'FAIL %s%s\n' "$1" "${2:+: $2}"; fails=$((fails + 1)); }
has() { if grep -qF -- "$2" "$3" 2>/dev/null; then ok "$1"; else bad "$1" "no \"$2\" in $(basename "$3")"; fi; }
hasnt() { if grep -qF -- "$2" "$3" 2>/dev/null; then bad "$1" "found \"$2\" in $(basename "$3")"; else ok "$1"; fi; }

echo "== debrief-signals.py selftest =="
if python3 "$HERE/debrief-signals.py" --selftest >/dev/null 2>&1; then
  ok "pattern and file rules"
else
  bad "pattern and file rules" "run: python3 .system/debrief-signals.py --selftest"
fi
echo

echo "== integration =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.system/prompts" "$TMP/sessions" "$TMP/bin" "$TMP/work" "$TMP/home/.local/bin" "$TMP/home/.claude/commands"
cp "$HERE/session-debrief.sh" "$HERE/redact-transcript.py" "$HERE/capture-status.py" \
   "$HERE/debrief-react.py" "$HERE/debrief-signals.py" "$HERE/dream.sh" "$TMP/.system/"
cp "$HERE/prompts/"*.md "$TMP/.system/prompts/"
HOOK="$TMP/.system/session-debrief.sh"
LOG="$TMP/.system/hook.log"

# A stub drafter: keeps its prompt, and writes a draft that names two
# reactions. One is in a human turn of the session. One is not.
cat > "$TMP/bin/claude" <<STUB
#!/usr/bin/env python3
import re, sys
prompt = sys.argv[2] if len(sys.argv) > 2 else ""
sid = re.search(r"^SESSION_ID: (\S+)", prompt, re.M)
out = re.search(r"^OUTPUT FILE: (.+)$", prompt, re.M)
open("$TMP/prompt-%s.txt" % (sid.group(1)[:8] if sid else "none"), "w").write(prompt)
rid = re.search(r'^\{"id": "([0-9a-f]{12})"', prompt, re.M)
if out:
    open(out.group(1), "w").write("\n".join([
        "# Session draft", "", "## Unrecorded reactions",
        '- "yes this one, ship it" | about: the calmer header | sentiment: positive | kind: kept',
        '- "words the human never typed" | about: the header | sentiment: negative | kind: rejected',
        "", "## Label check", "- %s | sentiment: negative" % (rid.group(1) if rid else "000000000000"), ""]))
STUB
chmod +x "$TMP/bin/claude"
cp "$TMP/bin/claude" "$TMP/home/.local/bin/claude"
export PATH="$TMP/bin:$PATH"

# A transcript of more than 30 lines: a request, a claim, a correction the
# agent recorded, a stated rule, and an approval nobody recorded.
mk_transcript() {  # path entrypoint
  python3 - "$1" "$2" "$TMP/work" <<'PY'
import json, sys
path, entry, cwd = sys.argv[1:4]
U = lambda c, **kw: dict({"type": "user", "cwd": cwd, "entrypoint": entry, "timestamp": "2026-01-05T10:00:00Z",
                          "message": {"role": "user", "content": c}}, **kw)
A = lambda blocks: {"type": "assistant", "cwd": cwd, "timestamp": "2026-01-05T10:01:00Z",
                    "message": {"role": "assistant", "content": blocks}}
rows = [U("make the header calmer"),
        A([{"type": "text", "text": "On it."}, {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "pnpm test"}}]),
        U([{"type": "tool_result", "tool_use_id": "c1", "content": "12 passed"}]),
        A([{"type": "text", "text": "The header is calmer and all tests pass."}]),
        U("still too loud, drop the gradient"),
        A([{"type": "text", "text": "The gradient is removed."}]),
        U("from now on show me a screenshot before every push"),
        A([{"type": "text", "text": "Here is the screenshot."}]),
        U("yes this one, ship it")]
rows += [A([{"type": "text", "text": "filler line %d" % i}]) for i in range(28)]
open(path, "w").write("".join(json.dumps(r) + "\n" for r in rows))
PY
}

run_hook() {  # session transcript
  printf '{"session_id":"%s","transcript_path":"%s","reason":"other"}' "$1" "$2" | bash "$HOOK" >/dev/null 2>&1
}
wait_for() {  # a line in the hook log
  for _ in $(seq 1 60); do grep -qF -- "$1" "$LOG" 2>/dev/null && return 0; sleep 0.25; done
  return 1
}

# ---- 1. a human session ----------------------------------------------------
SID="5151aaaa0000beef"
T="$TMP/human.jsonl"; mk_transcript "$T" cli
( cd "$TMP/work" && CLAUDE_CODE_SESSION_ID="$SID" python3 "$TMP/.system/debrief-react.py" record --dir "$TMP" \
    --words "still too loud, drop the gradient" --about "the header" --sentiment positive --kind rejected >/dev/null )
run_hook "$SID" "$T"
wait_for "adopt" || bad "the adopt step ran after the draft" "no adopt line in hook.log"
SIG="$TMP/sessions/signals/5151aaaa.json"
REC="$TMP/sessions/reactions/5151aaaa.jsonl"
P="$TMP/prompt-5151aaaa.txt"
[ -s "$SIG" ] && ok "the hook wrote the signals file" || bad "the hook wrote the signals file"
has "the hook log names the stage" "signals: 5151aaaa 4 human turns, 1 covered by a reaction, 1 stated rules" "$LOG"
has "the stage is marked ok" '"signals"' "$TMP/.system/capture_status.json"
has "prompt: the signals block is given to the drafter" "TURN SIGNALS: $SIG" "$P"
has "prompt: an uncovered turn is listed with its line" "L9: yes this one, ship it" "$P"
hasnt "prompt: the turn a reaction covers is not listed as unrecorded" "L5: still too loud" "$P"
has "prompt: the stated rule is listed" 'L7: "from now on show me a screenshot before every push"' "$P"
has "prompt: the claim before the correction is listed" 'L5: the agent said: "The header is calmer and all tests pass."; checks passed: pnpm test' "$P"
has "prompt: a verified reaction carries its id" '{"id": "' "$P"
has "adopt: the hook log counts what was adopted" "signals: adopt 1 adopted, 1 not_found, 1 labels_checked, 1 labels_differ for 5151aaaa" "$LOG"
if python3 - "$REC" "$SIG" <<'PY'
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
by = {r["words"]: r for r in recs}
a, g = by["yes this one, ship it"], by["words the human never typed"]
assert a["verified"] is True and a["source"] == "drafter" and a["line"] == 9, a
assert g["verified"] is False and "why" in g, g
sig = json.load(open(sys.argv[2]))
first = by["still too loud, drop the gradient"]
assert sig["label_checks"][first["id"]] == {"sentiment": "negative", "by": "drafter", "agrees": False}, sig["label_checks"]
assert a["id"] in sig["turns"][3]["covered"], sig["turns"][3]
PY
then ok "adopt: found words verified as the drafter's, unfound words kept as not his, second reading noted"
else bad "adopt: records and second reading"; fi
OUT="$(python3 "$TMP/.system/debrief-signals.py" landed --dir "$TMP")"
case "$OUT" in *"corrected: 1 of "*'"still too loud, drop the gradient" (positive, agent'"'"'s reading; negative, drafter'"'"'s reading)'*) ok "landed: the corrected claim is reported from the hook's files" ;; *) bad "landed: the corrected claim is reported" "$OUT" ;; esac

# ---- 2. a headless session is not read -------------------------------------
SID2="6262bbbb0000beef"
T2="$TMP/headless.jsonl"; mk_transcript "$T2" sdk-cli
run_hook "$SID2" "$T2"
wait_for "drafter for $SID2 exited" || bad "the headless drafter ran"
[ ! -e "$TMP/sessions/signals/6262bbbb.json" ] && ok "headless: no signals file" || bad "headless: no signals file"
# The prompt text names TURN SIGNALS as a parameter. The block itself starts with its path.
hasnt "headless: no signals block in the prompt" "TURN SIGNALS: $TMP" "$TMP/prompt-6262bbbb.txt"
hasnt "headless: no turn of the script is listed" "UNRECORDED TURNS (the human" "$TMP/prompt-6262bbbb.txt"
has "headless: the drafter is still told" "SYNTHETIC: headless" "$TMP/prompt-6262bbbb.txt"

# ---- 3. a store without the script still drafts ----------------------------
mv "$TMP/.system/debrief-signals.py" "$TMP/debrief-signals.py.away"
SID3="7373cccc0000beef"
run_hook "$SID3" "$T"
wait_for "drafter for $SID3 exited" || bad "the drafter ran without the script"
has "no script: the draft still lands" "drafter for $SID3 exited rc=0 (draft present)" "$LOG"
[ ! -e "$TMP/sessions/signals/7373cccc.json" ] && ok "no script: no signals file, no failure" || bad "no script: no signals file"
hasnt "no script: nothing marked failed" "signals: FAILED" "$LOG"
mv "$TMP/debrief-signals.py.away" "$TMP/.system/debrief-signals.py"

# ---- 4. dream.sh hands the principles report to the dream pass -------------
D="$TMP/mem/debrief"
mkdir -p "$D/.system/prompts" "$D/dreams"
cp "$HERE/dream.sh" "$HERE/debrief-signals.py" "$HERE/debrief-react.py" "$HERE/redact-transcript.py" "$D/.system/"
cp "$HERE/prompts/auto-dream.md" "$D/.system/prompts/"
printf 'x\n\nlast dream: never\n' > "$D/OBSERVATIONS.md"
printf '# Index\n\n## Principles\n\n1. **Check the last hop.** Text. *Promoted by the dream pass, 2026-01-02.*\n   — from [2026-01-04](2026-01-04-a.md) · [2026-01-05](2026-01-05-b.md)\n' > "$D/INDEX.md"
HOME="$TMP/home" bash "$D/.system/dream.sh" --force
DP="$TMP/prompt-none.txt"
has "dream: the report reaches the dream pass" "PRINCIPLES REPORT" "$DP"
has "dream: the report counts dates after promotion" "Principle 1 · Check the last hop." "$DP"
has "dream: the principle is in the still-recurring group" "STILL RECURRING" "$DP"

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "$fails FAILED"; exit 1; fi
