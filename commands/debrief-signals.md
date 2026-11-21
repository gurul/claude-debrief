---
description: Show what the transcripts show by rule — claims that were then corrected, standing rules the human stated, and promoted principles that still recur
argument-hint: "landed [--project P] [--since YYYY-MM-DD] | rules [--project P] [--since YYYY-MM-DD] | principles"
---

# /debrief-signals — the signal reports

Arguments: `$ARGUMENTS`

**Store resolution (check first):** the store is `debrief/` in the current project root when `debrief/INDEX.md` exists there. Otherwise it is the directory in `$DEBRIEF_DIR`. If neither exists, say so ("no debrief store here") and stop. In shell, resolve it once:

```bash
D=debrief; [ -f debrief/INDEX.md ] || D="${DEBRIEF_DIR:?no debrief store here}"
```

Signals are read from each session's transcript by pattern, with no model, when the session ends. See `debrief/docs/signals.md`.

## Mode 1: `landed …` → claimed or checked, then corrected

```bash
python3 "$D/.system/debrief-signals.py" landed --dir "$D" [--project P] [--since YYYY-MM-DD]
```

Each entry is a stretch of work that ended on a claim or a passing check, followed by a reaction that one reader labelled negative or mixed. Present both quotes as printed.

## Mode 2: `rules …` → standing rules the human stated

```bash
python3 "$D/.system/debrief-signals.py" rules --dir "$D" [--project P] [--since YYYY-MM-DD]
```

Most repeated first. A rule marked "points back at its turn" needs its transcript line to be read.

## Mode 3: `principles` (or no arguments) → has each promoted principle held

```bash
python3 "$D/.system/debrief-signals.py" principles --dir "$D"
```

Present the groups as printed: still recurring, enforced, quiet.

## Rules

- **The words are the record.** Quote the human's words and the agent's claim as printed. Never paraphrase either.
- **A pattern is not a judgment.** A stated rule, a claim and a check are found by pattern. Say so, and open the transcript line when the match is doubtful.
- **Counts are floors.** A reaction nobody recorded or found is not counted. Never report a rate as the whole truth.
- **Draw no conclusion alone.** Do not write a rule, a memory, a principle or a hook from a report. For a principle that still recurs, propose one mechanism (a hook, a CI check, a test) and let the human decide.
- **Never remove a principle.** A principle with an `— enforced by:` line and no receipt since may be proposed for removal from `INDEX.md`. The human decides.
- This command is read-only.
