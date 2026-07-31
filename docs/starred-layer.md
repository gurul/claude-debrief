# The starred layer (`HIGHLIGHTS.md`)

`INDEX.md` is deliberately lossy: it answers "what is true *now*", so an entry
that stops being load-bearing gets rewired away. That is the right behavior for
current understanding and the wrong behavior for a lesson you paid for once and
must never relearn. Hence a third layer with the opposite promise.

A highlight is a margin star in a book: rare, in-situ, and permanent. Two rules
carry the whole design:

1. **A human stars it.** Hooks and drafters may *propose* (`★ (candidate)` in a
   session note); only `/debrief highlight`, or a human approving a candidate
   during a day pass, promotes. An agent starring its own conclusions is the
   laundering the curation gate exists to prevent.
2. **No recurrence bar** — and that is the point. `INDEX.md` principles need ≥2
   debriefs so the list stays short. Highlights are the escape hatch for the
   one-shot lesson: the 3am root cause, the footgun that cost a day, the
   constraint invisible in the code.

Keep it short by raising the bar for *adding*, never by deleting. A wrong
highlight is corrected by appending a superseding entry that names the one it
replaces — the original stays, because what you believed and why is the record.

## Format

One `### ★ <one-line claim>` per entry under `## The starred list`, newest
first, each ending `— from [<source>](<link>) · starred <date>`. Write entries
to survive without their context: name the system, the mechanism and the
consequence, and assume the reader has forgotten the incident entirely.

## Recall

`/debrief-highlights` reads the whole list (or `--starred` search), and plain
`/debrief-search` ranks `★` hits above every other layer — see
[search.md](search.md) for why that is a sort key rather than a weight.
