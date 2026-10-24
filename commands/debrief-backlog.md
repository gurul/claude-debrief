---
description: Show the uncurated debrief backlog — which dates need a /debrief day pass
---

# /debrief-backlog — what memory is waiting to be curated

**Store resolution (check first):** the store is `debrief/` in the current project root when `debrief/INDEX.md` exists there. Otherwise it is the directory in `$DEBRIEF_DIR` (a shared store serving many repos; see the README's "Shared store"). If neither exists, say so ("no debrief store here") and stop. Never create a `debrief/` folder in a repo that has not opted in — memory is deliberate, not something a command sprays into every checkout. Every `debrief/…` path below means the resolved store. In shell, resolve it once:

```bash
D=debrief; [ -f debrief/INDEX.md ] || D="${DEBRIEF_DIR:?no debrief store here}"
```

The SessionEnd sweep counts the backlog into `.system/hook.log`, which no human opens. This command is the human-facing view of the same queue: landed drafts no `/debrief day` ever consumed (`unconsumed`), plus dead drafters (`failed`) split into still-recoverable and truly-terminal.

1. **Run the report** (read-only — it never writes a daily, INDEX.md, or the queue):

   ```bash
   python3 "$D/.system/debrief-backlog.py" --dir "$D"
   ```

   Output is a per-date list, oldest first, with the exact `/debrief day <date>` command for each stranded date, followed by any failed drafters labelled `recoverable via /debrief day <date>` (its transcript or raw archive survives) or `terminal` (both gone).

2. **Present it to the human verbatim**, oldest date first. A `failed` entry marked recoverable is not dead: `/debrief day <date>` reads its `transcript_path` directly, falling back to `sessions/raw/<date>/*.jsonl.gz`.

3. **Do NOT run `/debrief day` yourself.** Draining the backlog is the curation gate — a human pass that reviews each draft before it becomes durable memory. Surface the commands and let the human choose which dates to curate; auto-running day mode would launder unreviewed machine drafts into memory, the exact failure this whole system exists to prevent.

A clean queue prints nothing — that is the success state, not an error.
