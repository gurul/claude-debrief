# Todo — open threads being tracked

> **Template.** This is the todo layer: the open threads the project is actually
> tracking. Replace this blockquote and the example items with your own.

Every session draft and daily debrief records **Open threads** — unfinished
work, promised follow-ups, questions nobody answered. But episodic files are
immutable and read by date: a thread opened on Tuesday is invisible by Friday
unless someone rereads Tuesday. This file is where a thread goes to *stay
visible until it is resolved*.

| Layer | Question it answers | Mutability |
|---|---|---|
| Episodic — dailies, `sessions/` | what happened | immutable |
| Semantic — [`INDEX.md`](INDEX.md) | what is true now | pruned and rewired |
| Starred — [`HIGHLIGHTS.md`](HIGHLIGHTS.md) | what must never be relearned | append-only |
| **Todo — this file** | **what is still open now** | items open and close; closed items are pruned once their closing daily records the outcome |

## The rules

1. **A human admits every item.** During `/debrief day`, open threads from the
   day's drafts are *proposed* as todo candidates — a person approves each one
   into this file, same gate as `INDEX.md` and `HIGHLIGHTS.md`. An agent that
   files its own todos manufactures work the human never agreed to track.
   (`/debrief-todos add <item>`, where the human is the author, writes
   directly — the gate is already satisfied.)
2. **Closing goes through the same gate.** Day mode proposes closures when the
   day's work plausibly resolved an item; a person confirms. An item closes with
   a date and a pointer to the daily that resolved it, so "why is this done"
   is always one hop away.
3. **Not every open thread belongs here.** Most threads resolve within a
   session or two and never need tracking — admit only what would otherwise be
   *lost*: the follow-up nobody will remember next week. If this list grows past
   a screen, the bar was too low.
4. **History lives in the episodic layer, not here.** Closed items may be
   pruned once their closing daily exists — the daily is the durable record.
   This file stays short on purpose; it is a working surface, not an archive.

Write items to survive without their context: name the system, the file, the
exact thing left undone. Reuse `[[entity]]` names from the INDEX glossary.

## Open

- [ ] *(example)* Backfill `[[idempotency-key]]` for charges created before the
  cutover — reconciliation still double-counts them.
  — from [2026-01-05 payments retry loop](2026-01-05-payments-retry-loop.md) · opened 2026-01-05

## Closed (recent)

- [x] *(example)* Retire localStorage bearer tokens after the `[[session-cookie]]`
  cutover bake period.
  — closed 2026-01-05 by [2026-01-05 payments retry loop](2026-01-05-payments-retry-loop.md) · opened 2026-01-04

## Recall

- `/debrief-todos` — print the open list (cheapest possible recall).
- `/debrief-todos add <item>` / `/debrief-todos done <item>` — human-authored
  writes, immediate.
- This file is deliberately **not** indexed by `/debrief-search`: it is a short
  living surface meant to be read whole, and stale closed items surfacing in
  search results would misreport what is open. The dailies that opened and
  closed each item are indexed — the reasoning is searchable; the checklist is
  not.
