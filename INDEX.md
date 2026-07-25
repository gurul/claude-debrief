# Debrief Index — <project> memory

> **Template.** This is the semantic layer: the living, prunable view of what the
> project currently understands. Replace this blockquote and the example rows with
> your own. Everything else in `debrief/` is episodic and immutable; this file is
> the one that gets *rewired*.

## How this memory works (three layers)

- **Episodic** — immutable records of what happened and why: daily debriefs
  (`YYYY-MM-DD-<slug>.md`) and, below them, per-session notes in `sessions/`.
  Never edited after the fact. If understanding changes, the episodic record still
  says what was believed at the time — that's the point.
- **Semantic** — this file. A table of debriefs, a glossary of `[[entities]]`, and
  a short list of principles. Pruned and rewired toward current best understanding.
- **Starred** — [`HIGHLIGHTS.md`](HIGHLIGHTS.md). The `★` must-know-forever list.
  Append-only and **never pruned**.

Corrections happen *here*, by rewiring — not by editing history.

This file is deliberately lossy: it answers "what is true *now*", so entries leave
when superseded. That makes it the wrong home for a lesson you must never relearn
— those get starred instead. The bar differs on purpose: principles below need
**≥2 debriefs**; a highlight needs only a human saying "never again", once.

## Principles (attractors)

Promote a principle only once it has recurred in **≥2 debriefs**. One-offs are
observations, not principles; the bar exists so this list stays short enough to
actually carry into a session. A one-off that must never be relearned isn't a
principle — star it in [`HIGHLIGHTS.md`](HIGHLIGHTS.md) instead.

1. *(example)* **Verify at the boundary you claim.** A green type-check is not a
   working feature — exercise the real path before calling it done.
2. *(example)* **Root cause before patch.** A retry that hides a deadlock is debt
   with a due date.

## Debriefs (newest first)

| Date | Debrief | TL;DR | Key entities |
|---|---|---|---|
| 2026-01-05 | [Payments retry loop](2026-01-05-payments-retry-loop.md) | Retries were double-charging; idempotency keys had to come first. | `[[chargeCard]]`, `[[idempotency-key]]` |
| 2026-01-04 | [Auth session cutover](2026-01-04-auth-session-cutover.md) | Moved sessions to httpOnly cookies; localStorage tokens retired. | `[[requireSession]]`, `[[session-cookie]]` |

## Glossary (shared graph nodes)

`[[Entity]]` names are the graph's edges — the same name in two debriefs links
them. Reuse exact names; fold corrections into the existing node rather than
adding a near-duplicate.

### Systems / repos

- `[[acme-api]]` — *(example)* the HTTP API; owns auth + payments.
- `[[acme-worker]]` — *(example)* background jobs; reconciliation, retries.

### Concepts / contracts

- `[[idempotency-key]]` — *(example)* per-charge key making a retry a no-op rather
  than a second charge. Derived from order id + attempt window.
- `[[session-cookie]]` — *(example)* httpOnly, SameSite=Lax. Replaced the
  localStorage bearer token on 2026-01-04.

### Code landmarks

- `[[chargeCard]]` — *(example)* `src/payments/charge.ts`. The one place a card is
  charged; all retry logic wraps this.
- `[[requireSession]]` — *(example)* `src/auth/middleware.ts`. The auth boundary.

## The one meta-principle (carry this into every session)

*(example)* **Write down what surprised you.** The debrief is worth keeping only for
the parts that weren't obvious in advance — the wrong turns, the reason the obvious
fix failed. Outcomes are recoverable from git; reasoning isn't.
