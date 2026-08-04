# The todo layer — open threads that stay visible

Every session draft and daily debrief already records **Open threads**. The
problem is where they live: episodic files are immutable and read by date. A
thread opened Tuesday is faithfully recorded — and invisible by Friday, because
nobody rereads Tuesday. The episodic layer remembers; it doesn't *remind*.

`TODO.md` is the fix: a fourth layer holding the open threads a human chose to
track, visible until each one is resolved.

## Where it sits

| Layer | Question it answers | Mutability |
|---|---|---|
| Episodic | what happened | immutable |
| Semantic — `INDEX.md` | what is true now | pruned and rewired |
| Starred — `HIGHLIGHTS.md` | what must never be relearned | append-only |
| **Todo — `TODO.md`** | **what is still open now** | items open and close; closed items pruned once their closing daily exists |

Like `INDEX.md`, it is deliberately lossy — it answers a *now* question, so
resolved items eventually leave. Unlike `INDEX.md`, nothing is rewired: an item
is either open or closed, and the reasoning behind both transitions lives in
the dailies it links to.

## The gate applies here too

The whole system turns on one rule: machines capture, humans promote. Todos are
no exception, and the failure mode is specific — an agent that files its own
todos **manufactures work the human never agreed to track**, and an agent that
closes them **declares work done that nobody verified**. Both are the laundering
the curation gate exists to prevent.

So the paths in are exactly analogous to the starred layer's:

- **Batch, via `/debrief day`.** The day pass harvests the day's Open threads
  as *candidates*, and proposes closures for open items the day's work
  plausibly resolved. The human approves each, item by item, during the same
  review that gates the daily itself. Silence is a no.
- **Immediate, via `/debrief-todos add` / `done`.** The human is the author,
  so the gate is already satisfied — same reasoning that lets
  `/debrief highlight` write directly.

## The admission bar

Most open threads should **not** become todos. A thread the next session will
naturally pick up needs no tracking; tracking it anyway buries the ones that do.
The bar: admit only what would otherwise be *lost* — the follow-up nobody will
remember next week, the "we should check X eventually" that no session owns.
If the open list grows past a screen, the bar was too low. The fix, as with
highlights, is a stricter bar going forward — the list stays useful by being
short.

## Why search doesn't index it

`/debrief-search` deliberately skips `TODO.md`:

- It is a short living surface meant to be read **whole** — `/debrief-todos`
  is the recall path, and it costs one file read.
- Search results are snapshots; a closed (or pruned) item surfacing for a
  matching query would misreport what is open. The checklist's value is its
  currency, which FTS chunks can't promise.
- Nothing is lost: every item links the daily that opened it and the daily
  that closed it, and *those* are indexed. The reasoning is searchable; the
  checklist is not.

## Provenance, both directions

An open item carries `— from [<daily>](<link>) · opened <date>`; a closed item
adds `— closed <date> by [<daily>](<link>)`. That keeps the file honest in the
same way `HIGHLIGHTS.md` is: every line is one hop from the full reasoning
that earned it, so "why are we tracking this" and "why is this done" never
depend on anyone's memory.
