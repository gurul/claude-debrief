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

## Semantic layer (EmbeddingGemma 2)

Keyword search misses a note that uses another word for the same thing. When a
local [Ollama](https://ollama.com) serves
[EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2), `search`
also ranks chunks by meaning and fuses the two lists with reciprocal-rank
fusion (k = 60). The fusion uses ranks only, so the bm25 scale and the cosine
scale never need calibration against each other.

```bash
brew install ollama && brew services start ollama
ollama pull embeddinggemma-2
python3 debrief/.system/debrief-search.py search "thermal throttling"
# [212]~ 2026-10-13 2026-10-13-eve-daemon-….md §CPU embedder — …
```

- A `~` after the id marks a meaning-only hit: no query term is in the chunk.
- The vectors cover exactly the chunks FTS indexes. The curation gate is
  unchanged, and the selftest asserts that the two sets are equal.
- Chunks use the model's retrieval prompts: `title: {section} ({doc}) | text: …`
  for documents, `task: search result | query: …` for queries.
- Vectors live in `.system/embed-cache.db` (gitignored), keyed by model,
  dimension and prompt text. A reindex embeds only the sections that changed.
- The semantic score is centered: `cos(query, chunk) − cos(empty query, chunk)`.
  Raw cosines from this model sit in a narrow band (about 0.62 ± 0.03 on a
  576-chunk store) and a few hub chunks score high against every query, so the
  empty-query term is subtracted. A meaning-only hit needs a centered score of
  at least 0.03. Measured: real queries put their best chunk at 0.03–0.11,
  nonsense and single generic words at 0.02 or below, so those return no hits.
- The ★ sort key still applies to every highlight that matches a keyword. A
  highlight found by meaning alone ranks by fused score.
- No Ollama, or no model: `search` prints `(semantic off: …; keyword only)` on
  stderr and returns the plain bm25 ranking.

| Setting | Default | Effect |
|---|---|---|
| `--keyword` / `DEBRIEF_EMBED=off` | on | bm25 only |
| `DEBRIEF_EMBED_URL` | `http://127.0.0.1:11434` | Ollama base URL |
| `DEBRIEF_EMBED_MODEL` | `embeddinggemma-2` | any Ollama embedding model |
| `DEBRIEF_EMBED_KEEP_ALIVE` | `30m` | how long Ollama keeps the model loaded after a search |
| `DEBRIEF_EMBED_DIM` | `768` | Matryoshka truncation: 512, 256 or 128 |

The script stays stdlib-only: it talks to Ollama over `urllib`.

## Selftest

`debrief-search.py selftest` checks the invariants on a synthetic corpus —
pass/fail only, never a score. The central assertion is the gate itself:
machine-draft content must be unsearchable. The starred invariants assert against
the *hard* case (a highlight must outrank a competing doc-title match), because
an earlier multiplier-based implementation passed a synthetic test where the term
lived only in `HIGHLIGHTS.md`, then lost on the real corpus. The semantic
invariants run offline against a fake embedder: a meaning-only hit surfaces,
the cache prevents re-embedding, the vector set equals the gated chunk set,
and an unreachable embedder falls back to keyword search.

## The curation gate applies to retrieval too

The index holds dailies, `INDEX.md`, and `status: curated` session notes —
never machine drafts, archived or not. A draft's content becomes searchable
only by surviving `/debrief day`. Searching drafts directly would hand back
exactly the confidently-wrong memory this system exists to prevent; an empty
result plus "that date needs a `/debrief day` pass" is the correct behavior,
and `commands/debrief-search.md` (install next to `debrief.md`) says so to the
agent in as many words.

The living surfaces are also deliberately unindexed: `TODO.md` and
`OBSERVATIONS.md` are excluded by construction (top-level, no date prefix) and
by intent. Both answer *now* questions read whole — `/debrief-todos` and
`/debrief-dreams` are their recall paths — and both would misreport through
search snapshots: a pruned todo surfacing as open, a 1-daily `watching` entry
reading like settled truth. The dailies that opened, closed, and receipted
every entry *are* indexed; the reasoning is searchable, the checklists are
not. The selftest plants markers in both files and asserts they never match.

Query terms are AND-ed; valid FTS5 syntax (`"exact phrase"`, `OR`, `NOT`)
passes through. `--dir` / `$DEBRIEF_DIR` override discovery, same as the
provenance builder.
