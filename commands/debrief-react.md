---
description: Record how the human reacted to a piece of work, in his exact words, for the debrief sentiment record
argument-hint: "<the human's exact words> — about <the piece>"
---

# /debrief-react — record one reaction

Arguments: `$ARGUMENTS`

**Store resolution (check first):** the store is `debrief/` in the current project root when `debrief/INDEX.md` exists there. Otherwise it is the directory in `$DEBRIEF_DIR`. If neither exists, say so ("no debrief store here") and stop. In shell, resolve it once:

```bash
D=debrief; [ -f debrief/INDEX.md ] || D="${DEBRIEF_DIR:?no debrief store here}"
```

A reaction is the human's words about a piece of work, recorded at the moment he said them. The session-end hook checks the words against the transcript. Only words found in his own turns count as his. Read `debrief/docs/reactions.md` for the rules.

## When to record

Record when the human reacts to work: approval, rejection, delight, frustration, a correction, a stated preference. One record per reaction.

Do not record:

- an instruction ("add a test"), or a question about a fact;
- a mood you infer from silence or from what he did not say;
- your own summary of how he seems to feel.

Never ask the human to rate anything. Record what he said; do not prompt for more.

## How

1. **Words.** Copy his words exactly from his message. Do not fix typos, shorten or paraphrase. If you need two parts of one message, join them with ` / ` instead of an ellipsis.
2. **About.** Name the piece: the screen, the component, the sentence, the plan, the name.
3. **Before and after.** Give a store path under `sessions/material/`, a file path or a commit when you know one.
4. **Labels.** Pick a sentiment (`positive`, `negative`, `mixed`, `neutral`) and, if one fits, a kind (`kept`, `changed`, `rejected`, `returned-to`, `praise`, `frustration`, `correction`, `preference`, `question`). They are your reading and are shown as yours.
5. Run:

```bash
python3 "$D/.system/debrief-react.py" record --dir "$D" \
  --words "<his exact words>" --about "<the piece>" --sentiment <label> \
  [--kind <kind>] [--before <path or commit>] [--after <path or commit>] \
  [--project <name>] [--note "<one line of your context>"]
```

6. Say nothing more than the one confirmation line it prints, unless the human invoked this command himself. Then show him the words you recorded.

When the human runs `/debrief-react` himself, his arguments are his words. Split them into the words and the piece. Ask which piece only if you cannot tell.

## Rules

- A label is never a rule, and never a reason to hold work back. A negative reaction covers its piece.
- A change of mind is a new record. Never edit or delete a record.
- The script redacts every field and refuses to write when the redactor is missing. Do not work around a refusal.
