---
description: Show how work has landed with the human — verified reactions in his words, or counts by project, sentiment and week
argument-hint: "[<topic>] [--project P] [--sentiment S] [--since YYYY-MM-DD] [--all] | summary [--project P] [--since YYYY-MM-DD]"
---

# /debrief-sentiment — the reaction record

Arguments: `$ARGUMENTS`

**Store resolution (check first):** the store is `debrief/` in the current project root when `debrief/INDEX.md` exists there. Otherwise it is the directory in `$DEBRIEF_DIR`. If neither exists, say so ("no debrief store here") and stop. In shell, resolve it once:

```bash
D=debrief; [ -f debrief/INDEX.md ] || D="${DEBRIEF_DIR:?no debrief store here}"
```

Reactions are the human's words about pieces of work, recorded in a session and checked against its transcript. See `debrief/docs/reactions.md`.

## Mode 1: `summary …` → counts

```bash
python3 "$D/.system/debrief-react.py" summary --dir "$D" [--project P] [--since YYYY-MM-DD]
```

Present it as printed. It counts verified reactions by project and sentiment and by week, and shows the five most recent negative and positive ones with the words exact. `--project` takes a project, an alias or a group from `$D/.system/projects.json`; `group/member` names one member.

## Mode 2: anything else → the reactions themselves

```bash
python3 "$D/.system/debrief-react.py" list --dir "$D" $ARGUMENTS
```

Present each reaction whole. When the question is about a piece, open its before and after paths.

## Rules

- **The words are the record.** Quote them as printed. Never paraphrase or summarise them as his view.
- **Labels are the recording agent's reading.** Say so when you show a sentiment or a kind.
- **Draw no conclusion.** Do not turn counts into a rule, a preference or a reason to hold work back. If the human asks what the record suggests, show the reactions and let him read them.
- `--all` adds unverified records. They are marked NOT CONFIRMED and are not his words until verified. Never quote one as his.
- This command is read-only.
