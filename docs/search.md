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

## Recency and supersession

Memory goes stale. A note that says "we use X" is wrong the week after X is
replaced, and plain relevance ranking serves it with full confidence.

**Recency.** After fusion, each dated chunk's score is multiplied by
`0.3 + 0.7 × 0.5^(age / 30 days)`. Two equal matches now rank newest first,
and a months-old note keeps 30% of its score, so an old exact match still
beats unrelated noise. `INDEX.md` and `HIGHLIGHTS.md` are undated and never
decay: INDEX is pruned to what is true now, and a highlight is a must-know.
Every hit prints its age, `2026-09-25 (34d)`. The shape follows the
Generative Agents retrieval score (relevance × exponential recency decay).

**Supersession.** Decay only reorders. A claim that a later day explicitly
nullified must not be served as current at all. Dailies are immutable, so
such a claim is invalidated in `SUPERSEDED.md` instead of deleted, the way
Graphiti invalidates a superseded graph edge and keeps its history:

```
- 2026-09-04-old.md § Section heading → 2026-10-20-new.md — moved to memcached
```

A ledgered chunk's score is multiplied by 0.05, which puts it below every live
chunk with or without recency, and the hit prints
`⊘ superseded by 2026-10-20-new.md (moved to memcached)`. Omit `§ …` to
invalidate a whole doc. The dream pass writes the ledger; a person can too.
The ledger is never indexed.

| Setting | Default | Effect |
|---|---|---|
| `--no-recency` / `DEBRIEF_RECENCY=off` | on | relevance only ("what did we think back then"); superseded chunks still sink |
| `DEBRIEF_HALF_LIFE` | `30` | days for a dated chunk to lose half its decayable score |

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
- **A search never waits on embedding work.** The query and the empty-query
  baseline go in one call with an 8-second limit (the baseline is cached), so a
  stalled Ollama, one that takes the connection and never answers, costs 8
  seconds and then the bm25 ranking, not the 120 the fill allows. Chunks the
  cache lacks (new notes, or a new model) are embedded inside a search only for
  3 seconds; the rest go to one detached `debrief-search.py embed` run (a lock
  file keeps it to one) and stay findable by keyword meanwhile. Measured on the
  live store with the cache deleted: the first search took 3.6 s instead of
  about 30 s, the background run filled the cache within 40 s, and the next
  search took 0.2 s.

| Setting | Default | Effect |
|---|---|---|
| `--keyword` / `DEBRIEF_EMBED=off` | on | bm25 only |
| `DEBRIEF_EMBED_URL` | `http://127.0.0.1:11434` | Ollama base URL |
| `DEBRIEF_EMBED_MODEL` | `embeddinggemma-2` | any Ollama embedding model |
| `DEBRIEF_EMBED_KEEP_ALIVE` | `30m` | how long Ollama keeps the model loaded after a search |
| `DEBRIEF_EMBED_DIM` | `768` | Matryoshka truncation: 512, 256 or 128 |
| `DEBRIEF_QUERY_TIMEOUT` | `8` | seconds a search waits for the query's embedding |
| `DEBRIEF_SEARCH_EMBED_BUDGET` | `3` | seconds a search spends embedding new chunks before handing the rest to `embed` |

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

## Structure tables

The search ranks by words, meaning and recency, never by date. On 2026-11-09 a
personal assistant asked it "what was I working on yesterday" and got two older
days; even a query holding the date did not reliably surface that day. "When did I
last work on X", "where does X stand" and "what is left to do" failed the same way.

The notes already carry the structure those questions need, so every rebuild of
`search.db` also fills these tables from the same Markdown (the Markdown stays the
source of truth; nothing is written back):

| Table | From | Holds |
|---|---|---|
| `days` | each root daily, plus its `INDEX.md` row | date, file, title, summary |
| `sessions` | each daily's Sessions table and `Late-day addendum` headings | session id, day, project (from the cwd or the heading), end time, shape |
| `entities` | `INDEX.md`'s Glossary, one `[[name]]` entry each | name, group, entry text |
| `mentions` | `[[name]]` links in each daily section | name, day, section, count |
| `todos` | `TODO.md` Open and Closed | text, linked names, opened date, status |
| `replaced` | `SUPERSEDED.md` | old doc and section, newer doc, why |

The commands that read them:

- `day <when> [--until <when>]`: one day answers its summary, its sessions, an
  outline of its parts, then the note; a span answers each day's summary and names
  the days with no note. `<when>` is `YYYY-MM-DD`, `today`, `yesterday`, `day
  before yesterday`, a weekday (its most recent past day), `latest`, `this week`,
  `last week`, `weekend`, `this month` or `last month`. Template rows in `INDEX.md`
  never count: a day needs a daily.
- `when <name>`: the days a name is in the dailies, newest first, links and prose
  alike (`voice pe`, `voice-pe` and `voicepe` match the same).
- `status <name>`: the glossary entry a spoken name means, the sentences in other
  entries that name it (a later state is often recorded there), the last days it
  was worked on, and its open threads.
- `todos [topic] [--closed]`: open threads, linked ones first for a topic.

The fingerprint includes the script itself, so a new version of the script
rebuilds the tables. The rebuild writes a new file and swaps it in, so a reader at
the same moment keeps the old index instead of reading a half-built one.


## Which embedding model (blind eval, 2026-11-09)

EmbeddingGemma 2 stays the default. A sealed set of 40 past-work questions (35 with
an answer in the notes, 5 about things that never happened) was written by an agent
that read the notes and never ran a search, with the chunks that answer each one as
gold. Each model was prompted in its own format (`PROMPTS`), embedded the same
802-chunk store, and was scored mechanically: is a gold chunk in the top 6. Each
model's meaning floor was set on nonsense queries only, never on the set, so nothing
was tuned on it. The questions are the owner's and are not in this repository.

| Model | Found in top 6 | Top 1 | MRR | Dated notes only | Misheard names | Embed all | Search |
|---|---|---|---|---|---|---|---|
| keyword only | 8/35 | 6 | 0.20 | 2/34 | 0/7 | — | 0 ms |
| **EmbeddingGemma 2, floor 0.03** | **27/35** | 12 | **0.50** | **26/34** | **5/7** | **31 s** | 70 ms |
| EmbeddingGemma 2, calibrated floor | 25/35 | 10 | 0.45 | 23/34 | 5/7 | 31 s | 72 ms |
| Qwen3-Embedding 0.6B | 26/35 | 13 | 0.50 | 25/34 | 3/7 | 106 s | 79 ms |
| Qwen3-Embedding 4B | 24/35 | 11 | 0.46 | 24/34 | 2/7 | 174 s | 149 ms |
| BGE-M3 | 21/35 | 10 | 0.41 | 21/34 | 2/7 | 48 s | 76 ms |

Single runs on one Mac. No candidate beat the default by more than noise, every one
was weaker on misheard names, and the larger ones embed several times slower (the 4B
model timed out at 120 s on one batch of long chunks). The calibrated floor (0.013)
did worse than the hand-set 0.03, so 0.03 stays.

The same run showed that a result made only of meaning hits (`~`) is not a sign
of "no record": 20 of the 35 answerable questions came back all `~`, and 14 of those
held the answer. Read a `~` hit before trusting or dismissing it. And none of the
models returned nothing for the 5 questions about things that never happened, so the
reader, not the search, has to say "no record".
