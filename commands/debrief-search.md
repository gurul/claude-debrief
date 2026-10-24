---
description: Search curated debrief memory (ranked index → fetch only what matters)
argument-hint: "<query>"
---

# /debrief-search — retrieval over curated memory

Arguments: `$ARGUMENTS`

**Store resolution (check first):** the store is `debrief/` in the current project root when `debrief/INDEX.md` exists there. Otherwise it is the directory in `$DEBRIEF_DIR` (a shared store serving many repos; see the README's "Shared store"). If neither exists, say so ("no debrief store here") and stop. Never create a `debrief/` folder in a repo that has not opted in — memory is deliberate, not something a command sprays into every checkout. Every `debrief/…` path below means the resolved store. In shell, resolve it once:

```bash
D=debrief; [ -f debrief/INDEX.md ] || D="${DEBRIEF_DIR:?no debrief store here}"
```

Two-step workflow — filter before you fetch, so full sections enter context only when they've earned it:

1. **Search.** Run:

   ```bash
   python3 "$D/.system/debrief-search.py" --dir "$D" search "$ARGUMENTS" -n 8
   ```

   Output is one compact line per hit: `[id] date doc §section — snippet`. The index rebuilds itself when memory changed; a `(reindexed: …)` line on stderr is normal.

   `[id]~` marks a meaning-only hit from EmbeddingGemma 2: none of the query words are in the chunk, so read it before you trust it. `(semantic off: …)` on stderr means the local Ollama is down and the results are keyword-only. That is not an error.

2. **Filter, then get.** Read the index lines and pick only the ids that plausibly answer the question — usually 1–3, not all of them:

   ```bash
   python3 "$D/.system/debrief-search.py" --dir "$D" get 12 15
   ```

   When a hit lands mid-document and needs surrounding context, add `-C 1` (N neighboring sections from the same doc, target marked `▶`) before reaching for a full-file Read — a fraction of the tokens on a large daily or INDEX.md.

3. **Answer from what you fetched**, citing the source doc (`2026-07-14-….md §section`). If a claim traces to a session note rather than a daily, say so — dailies are the reviewed layer.

## What the index covers — and deliberately doesn't

Daily debriefs, `INDEX.md`, `HIGHLIGHTS.md`, and session notes with `status: curated`. **Machine drafts are not indexed**, archived or not: their content becomes searchable only after `/debrief day` has verified or flagged its claims. If a search comes up empty but you suspect the answer is in an uncurated draft, the correct move is to tell the human that date needs a `/debrief day` pass — not to read the draft and present its claims as memory.

Query syntax: for the keyword half, terms are AND-ed; FTS5 syntax (`"exact phrase"`, `OR`, `NOT`) passes through when valid.

## The ★ starred layer ranks first

Hits from `HIGHLIGHTS.md` are printed above everything else, marked `★`, whenever they match at all — ordering on layer, not a weight, so a must-know cannot be buried by a stronger title match elsewhere. Treat a `★` hit as the answer to lead with, then use the other layers for detail.

`--starred` narrows the search to that layer only, for "what must I never forget about X". `stars` prints the layer whole with no query — see `/debrief-highlights`, which wraps both.
