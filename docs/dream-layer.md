# The dream layer — cross-session patterns, with receipts

Every session draft ends with **Candidate principle observations**. Day mode
reads them once, and then the draft is archived — and the section dies with it.
The episodic layer faithfully records a pattern's first sighting; nothing
*accumulates* sightings. A pattern seen once in January is invisible in March,
because the ≥2-debrief bar for INDEX principles is counted in the only place
that forgets: the human's head.

`OBSERVATIONS.md` is the fix: a fifth layer where a candidate pattern waits,
visibly, with its evidence attached, until it earns promotion or refutation.
The name "dream layer" comes from the loop this implements — reflections per
session, dreams across sessions — but the mechanism is deliberately boring:
a watchlist with citations.

## Where it sits

| Layer | Question it answers | Mutability |
|---|---|---|
| Episodic | what happened | immutable |
| Semantic — `INDEX.md` | what is true now | pruned and rewired |
| Starred — `HIGHLIGHTS.md` | what must never be relearned | append-only |
| Todo — `TODO.md` | what is still open now | items open and close |
| **Observations — `OBSERVATIONS.md`** | **what might be a pattern, and how much evidence it has** | entries accumulate receipts; leave on promotion or refutation |

Like `TODO.md`, it answers a *now* question and stays short on purpose. Unlike
every other layer, its entries are explicitly **provisional** — the file's
whole job is to hold claims that are not yet load-bearing, without letting
them either evaporate or masquerade as settled truth.

## Receipts, or it didn't happen

Each entry carries one `— seen in` line per sighting:

```markdown
— seen in [2026-01-05 payments retry loop](2026-01-05-payments-retry-loop.md) · 2026-01-05
  "we tuned the backoff twice and it looked better, because the race got rarer"
— status: watching · 1 daily
```

Three deliberate constraints:

- **Receipts cite curated documents only** — dailies and curated session
  notes, never machine drafts, never `sessions/raw/`. The dream pass reads
  material that survived the gate; anything else would launder unverified
  draft claims into a layer that feeds INDEX. This is the same rule that keeps
  search from indexing drafts, applied one layer up.
- **The quote must be findable in the source.** A receipt whose quote doesn't
  string-match its daily is rejected at review. Citation here is validation,
  not formatting — a claim the dream pass cannot quote does not get proposed.
- **Evidence is counted in distinct dailies, not mentions.** Three sentences
  in one debrief is one sighting. Redundant *sources* are what make a pattern
  trustworthy; repetition within a source is just emphasis.

And one stylistic rule that is actually structural: **keep the hedge**. A
claim seen once is written "seemed to", not "always". Consolidation systems
measurably rewrite hedged observations into flat assertions that downstream
readers obey as fact; here, confidence lives in the status line and the
receipt count, so the prose never has to overclaim.

## Statuses

- `watching` — accumulating evidence. At ≥2 distinct dailies, the dream pass
  may propose promotion to an INDEX principle (the receipt lines travel with
  it). With 1 daily, promotion may not even be proposed.
- `contested` — two dailies disagree. Both sides stay in the entry with both
  receipts until a later daily or the human resolves it. Preserved, never
  averaged, never last-write-wins.
- `promoted` / `refuted` — terminal. The entry is pruned once the outcome is
  recorded where it belongs: the INDEX principle (with receipts), the
  highlight, or the daily that refuted it. Supersede, never silently delete.

Staleness is surfaced, not enforced: a `watching` entry with no new sighting
in ~90 days gets a "reconfirm or drop?" proposal at the next dream pass. The
human decides; nothing ages out on its own.

## The gate applies here too

The failure mode is specific: an agent that promotes its own observation
**manufactures a law from an anecdote**, and one that refutes silently
**buries evidence a human never weighed**. So the paths in are exactly the
todo layer's:

- **Batch, via `/debrief dream`** — the cross-session pass — and via
  `/debrief day`, which checks the day's candidate observations against the
  ledger (a new sighting of a `watching` entry, or a novel would-be-lost
  pattern). Every proposal is approved item by item. Silence is a no.
- **Immediate, via `/debrief-dreams add`.** The human is the author, so the
  gate is already satisfied.

The dream pass itself never touches `INDEX.md`, `HIGHLIGHTS.md`, or `TODO.md`
except through their existing gates, and never writes a daily. Its inputs are
immutable, so a pass is idempotent: proposals are disposable, and a rejected
pass leaves no residue.

## Running it automatically

With `DEBRIEF_AUTO_DREAM=1` the daemon runs the pass itself (`.system/dream.sh`).
The design takes four things from systems that already do this, and refuses
one:

- **Gate on time and on new evidence**, like Claude Code's Auto Dream (24 h
  and 5 sessions since the last consolidation). Here it is ≥24 h and ≥3 new
  dailies, because a daily is the unit evidence is counted in. Both are
  settings: `DEBRIEF_DREAM_MIN_HOURS`, `DEBRIEF_DREAM_MIN_DAILIES`.
- **One dreamer at a time.** A PID lock, taken over when its owner died with
  the machine. The pass never starts while a curator is writing the dailies
  it reads.
- **Run in the background, off the hot path**, like Letta's sleep-time agents.
  A session never waits for a dream.
- **Invalidate, never delete**, like Graphiti's bi-temporal edges. A claim a
  later day nullified gets a line in `SUPERSEDED.md` that names its
  replacement. Search sinks it and points at the newer doc. The old daily stays
  as written, so "what did we believe in September" still has an answer.
- **Refused: last-write-wins.** Auto Dream and Mem0 delete the contradicted
  fact. That loses the case where two dailies describe two contexts rather
  than a change of mind. A later daily wins here only when it states the
  change ("replaced", "reverted", "no longer"). Otherwise the entry goes
  `contested` with both receipts.

Review is waived, as with the automatic curator. In its place, every change is
listed in `dreams/<date>.md` with its receipt. That file is the review surface,
read after the fact. Leave the variable unset to keep the human gate:
`/debrief dream` then proposes and a person approves item by item.

## Why search doesn't index it

Same argument as the todo layer, sharpened: this is a living surface meant to
be read whole, and its entries are *provisional by design*. A `watching` claim
surfacing in a search result reads exactly like a settled one — which is the
misreporting this layer exists to prevent. The dailies behind every receipt
are indexed; the reasoning is searchable, the watchlist is not.

## Prior art, briefly

Nobody has shipped this shape, which is why the constraints above are strict.
Anthropic's Dreams preview synthesizes a new memory store from transcripts but
resolves contradictions last-write-wins and gates at the whole-store level;
Claude Code's auto memory is machine-written with post-hoc audit and no links
from entries back to sessions. Mastra's Observer/Reflector compresses history
into observations, then rewrites them lossily — no audit trail survives a
reflection. Mem0's LLM conflict resolver is documented to silently delete
memories that were never in conflict (it can't tell "changed their mind" from
"two contexts"). Generative Agents (Park et al. 2023) is the origin of the
citation form — insights stored with pointers to the evidence — but the
pointers were never used to verify anything. And "Manufactured Confidence"
(arXiv 2606.29279) shows the laundering failure directly: consolidation turns
hedged hearsay into confidently-phrased facts that downstream agents obey,
and the effective defense is redundant sources, not better phrasing. Every
rule in this file is one of those failure modes, inverted.

## Future work: inceptions

The loop this layer comes from has a fourth stage — *inceptions*: replay a
past session under varied conditions to test whether a lesson actually helps.
Transcript replay is out for good: the transcript format is internal and
unstable, and this repo's standing rule is to read it as text, never parse it.

The workable version, recorded here for when it's wanted: **task
re-execution, not transcript replay.** For a session with a recoverable start
SHA and a checkable outcome (tests pass, diff produced), generate a small
manifest — prompt, SHA, check command — and run a matrix of headless
`claude -p` in disposable worktrees: {with the lesson injected, without} ×
{model or effort tier}, grading outcome *and* cost. A lesson that only helps
at maximum effort isn't worth a line of every future session's context. An
inception result would be the strongest receipt class an observation can
carry. Opt-in per session, never a pipeline stage — most sessions (dirty
state, external services, mid-session steering) simply aren't inceptable, and
that's fine.
