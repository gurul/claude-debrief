# Search (the retrieval layer)

`INDEX.md` is orientation; search is recall. `.system/debrief-search.py` gives
ranked full-text retrieval over curated memory — stdlib `sqlite3` FTS5, no
daemon, no dependencies. Two steps, so an agent fetches full text only for the
hits that earned it:

```bash
python3 debrief/.system/debrief-search.py search "temperature opus" -n 8
# [100] 2026-07-14 2026-07-14-explorer-….md §The EXP-28 staging outage — …«temperature» param left on the «Opus» variant…
python3 debrief/.system/debrief-search.py get 100
```

Documents are chunked by heading; results are one line each (`[id] date doc
§section — snippet`). The index (`.system/search.db`) rebuilds automatically
whenever any source file changes. `get -C 1` adds neighboring sections from
the same doc (target marked `▶`) when a hit lands mid-document — cheaper than
Read-ing the whole file.

## Ranking

Ranking is bm25 with one hardcoded, curation-aware weight vector: filename
slugs and section headings (the most distilled human signal) weigh more than
body text, metadata columns don't match at all, and dailies/INDEX get a mild
tie-break multiplier over session notes. No knobs.

The one exception is the starred layer, and it is a **sort key rather than a
weight**: `HIGHLIGHTS.md` hits are ordered ahead of every other layer whenever
they match, marked `★`. A multiplier cannot deliver that promise — a mild boost
still loses to any competing filename or heading match, so "must-know surfaces
first" would hold only when the term appeared nowhere else, i.e. the case that
never needed help. Ordering on layer makes it unconditional and leaves the rest
of the ranking untouched.

```bash
python3 debrief/.system/debrief-search.py stars                        # the ★ layer, whole, no query
python3 debrief/.system/debrief-search.py search "waf" --starred       # ★ entries only
```

`stars` prints entries only — the file's own explanation of how starring works is
scaffolding and is filtered out, so the cheap-recall path stays cheap.

## Selftest

`debrief-search.py selftest` checks the invariants on a synthetic corpus —
pass/fail only, never a score. The central assertion is the gate itself:
machine-draft content must be unsearchable. The starred invariants assert against
the *hard* case (a highlight must outrank a competing doc-title match), because
an earlier multiplier-based implementation passed a synthetic test where the term
lived only in `HIGHLIGHTS.md`, then lost on the real corpus.

## The curation gate applies to retrieval too

The index holds dailies, `INDEX.md`, and `status: curated` session notes —
never machine drafts, archived or not. A draft's content becomes searchable
only by surviving `/debrief day`. Searching drafts directly would hand back
exactly the confidently-wrong memory this system exists to prevent; an empty
result plus "that date needs a `/debrief day` pass" is the correct behavior,
and `commands/debrief-search.md` (install next to `debrief.md`) says so to the
agent in as many words.

Query terms are AND-ed; valid FTS5 syntax (`"exact phrase"`, `OR`, `NOT`)
passes through. `--dir` / `$DEBRIEF_DIR` override discovery, same as the
provenance builder.
