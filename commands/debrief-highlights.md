---
description: Read the ★ starred layer — the must-know-forever entries, optionally filtered by query
argument-hint: "[query]"
---

# /debrief-highlights — the starred layer

Arguments: `$ARGUMENTS`

**Scope guard:** applies only to repos wired for debrief memory. If `debrief/INDEX.md` does not exist in the current project root, say so and stop.

The margin stars: the handful of things a human said must be known **forever**. Cheapest and highest-signal recall in the system — read this before reaching for search, and read it at the start of any session on unfamiliar ground.

## No argument → the whole list

```bash
python3 debrief/.system/debrief-search.py stars
```

Prints every `★` entry (and only the entries — the file's own explanation of how starring works is scaffolding and is filtered out). The list is deliberately short enough to read whole; if it is not, say so, because that means the starring bar has slipped.

If it reports no entries yet, the layer is unused — do not invent entries to fill it. Mention that `/debrief highlight <thing>` is how one gets added.

## With a query → starred entries matching it

```bash
python3 debrief/.system/debrief-search.py search "$ARGUMENTS" --starred -n 8
```

Then `get <id>` for full text, exactly as in `/debrief-search`.

Empty result here is meaningful and worth stating plainly: **nothing about this has been marked must-know.** That is not the same as "nothing is known about it" — fall back to `/debrief-search` without `--starred` for the wider curated corpus, and say which layer the answer came from.

## Reading these correctly

- **Never pruned.** An entry can describe code that no longer exists; the lesson is what was starred, not the snippet. If an entry is now wrong, that is a correction to *append* (naming what it supersedes), never an edit or a delete.
- **No recurrence bar.** Unlike `INDEX.md` principles (≥2 debriefs), a highlight can come from a single incident. A one-off does not make it weaker — the whole point is the expensive lesson you must not relearn.
- **Provenance is one hop away.** Each entry ends with `— from [<source>] · starred <date>`. When you cite a highlight, cite that source too; the full reasoning lives there, and the entry is a pointer with teeth.
- **Do not add entries from this command.** Reading is unprivileged; starring is a human act (`/debrief highlight`). If something in this session looks star-worthy, propose it — do not write it.
