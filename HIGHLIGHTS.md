# ★ Highlights — must-know, forever

> **Template.** This is the starred layer: the handful of things you would want
> tattooed on the inside of your eyelids. Replace this blockquote and the example
> entries with your own.

Highlighting a book works because the marks are *rare* and *permanent*. You come
back years later and the margin star still means "this one mattered." That is
exactly this file, and nothing else in the system does the job:

| Layer | What it is | What happens to it |
|---|---|---|
| Episodic — dailies, `sessions/` | what happened, and why you thought so | immutable; never edited |
| Semantic — [`INDEX.md`](INDEX.md) | current best understanding | **pruned and rewired** — entries leave when superseded |
| **Starred — this file** | must-know, forever | **never pruned** |

`INDEX.md` is deliberately lossy: it tracks what is true *now*, so a fact that
stops being load-bearing gets rewired away. Highlights are the opposite promise.
Once starred, an entry stays — even when the code it describes is long gone,
because the *lesson* outlives the code.

## The two rules

1. **A human stars it. Always.** No hook, no drafter, and no agent may add an
   entry on its own initiative — same gate as `INDEX.md`, for the same reason.
   Machines can *propose* (a session note marks a `★` candidate); a person
   promotes.
2. **No recurrence bar — and that is the point.** `INDEX.md` principles require
   ≥2 debriefs precisely so the list stays short. A highlight is the escape hatch
   for the one-shot lesson you must not relearn: the 3am outage cause, the
   footgun that cost a day, the constraint that is invisible in the code. If it
   only ever happens once and you must never forget it, star it.

Keep the list short by *raising the bar for adding*, never by deleting. If it
grows past what you can reread in a couple of minutes, the bar was too low — but
the fix is stricter starring going forward, not pruning what is already here.

## How to star something

**In situ** — a `★` at the start of a line, in any daily or session note:

```markdown
★ Cloud Armor CRS 932140 matches the Windows batch `IF x==y` signature, so any
  body carrying real code trips the RCE rule. — cost a day of chasing the wrong
  service; the WAF was never in the hypothesis list.
```

That is the "highlighter in the margin" form: it lives next to the reasoning that
earned it, and `/debrief day` collects it into this file with provenance.

**On demand** — `/debrief highlight <the thing>` appends here immediately. Use it
the moment you think "I must not forget this," without waiting for a day pass.

Write the entry so it survives without its context. `★ the timeout was wrong` is
useless in six months; name the system, the mechanism, and the consequence.

## The starred list

Newest first. Every entry carries where it came from, so the full reasoning is
always one hop away. Add `#tags` if you want cheap grouping — but resist
inventing a taxonomy before the list is long enough to need one.

### ★ Verify the mechanism exists before believing the cause

*(example)* A concurrency explanation requires concurrency. The suite that was
blamed for "parallel load flake" had `workers: 1` and `fullyParallel: false` — one
config read killed the theory. Forming a plausible story feels like progress and
is the most common way an investigation goes wrong.

— from [2026-01-05 payments retry loop](2026-01-05-payments-retry-loop.md) · starred 2026-01-05

### ★ `[[idempotency-key]]` must be derived, never generated at call time

*(example)* A key minted inside the retry path is a new key every attempt, which
makes the guard a no-op and the retry a second charge. Derive it from order id +
attempt window, upstream of anything that can retry.

— from [2026-01-05 payments retry loop](2026-01-05-payments-retry-loop.md) · starred 2026-01-05

## Recall

- `/debrief-highlights` — print this file (cheapest possible recall).
- `/debrief-search <query> --starred` — search highlights only.
- Plain `/debrief-search <query>` ranks highlights above other layers, so a
  starred entry surfaces first when it is relevant at all.
