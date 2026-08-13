# Observations — patterns being watched, with receipts

> **Template.** This is the dream layer: cross-session patterns under watch,
> each carrying the receipts that support it. Replace this blockquote and the
> example entries with your own.

Every session draft ends with **Candidate principle observations** — and that
section dies with the archived draft. A pattern seen once in January is
invisible in March; the ≥2-debrief bar for INDEX principles is counted in the
human's head. This file is where a candidate pattern goes to *accumulate
evidence until it earns promotion or refutation*.

| Layer | Question it answers | Mutability |
|---|---|---|
| Episodic — dailies, `sessions/` | what happened | immutable |
| Semantic — [`INDEX.md`](INDEX.md) | what is true now | pruned and rewired |
| Starred — [`HIGHLIGHTS.md`](HIGHLIGHTS.md) | what must never be relearned | append-only |
| Todo — [`TODO.md`](TODO.md) | what is still open now | items open and close |
| **Observations — this file** | **what might be a pattern, and how much evidence it has** | entries accumulate receipts; leave on promotion or refutation |

## The rules

1. **A human admits, promotes, refutes, and prunes every entry.** `/debrief
   dream` and `/debrief day` *propose*; a person approves each item, same gate
   as every other layer. Silence is a no. An agent that promotes its own
   observation manufactures a law from an anecdote; one that refutes silently
   buries evidence a human never weighed. (`/debrief-dreams add <observation>`,
   where the human is the author, writes directly — the gate is already
   satisfied.)
2. **Every entry carries receipts.** One `— seen in` line per sighting, citing
   a *curated* document — a daily or a curated session note — with a short
   verbatim quote where the evidence is load-bearing. Never a machine draft,
   never `sessions/raw/`: an observation that can only be receipted to
   unverified material does not belong here. Evidence is counted in **distinct
   dailies**, not mentions.
3. **One daily is an observation, not a law.** Entries stay `watching` until
   their receipts span ≥2 dailies; only then may promotion to an INDEX
   principle be proposed. A one-shot lesson that must never be relearned is
   not a pattern — propose it as a `★ (candidate)` instead.
4. **Disagreements are preserved, never averaged.** When two dailies conflict,
   the entry becomes `contested` and holds both sides with both receipts until
   a later daily — or the human — resolves it. No last-write-wins.
5. **Keep the hedge.** Write the claim in the register the evidence supports
   ("seemed to", "in one session"). Confidence lives in the status line and
   the receipt count, not in confident phrasing.
6. **Short on purpose.** Admit only patterns that would otherwise be *lost*.
   If this file outgrows a screen, the bar was too low. Promoted and refuted
   entries are pruned once their outcome is recorded where it belongs — the
   INDEX principle, the highlight, or the daily that refuted them.

## Watching

### Watching: *(example)* backoff tuning seemed to mask races rather than fix them

Tuning retries made the double-charge rarer, not gone — the race was the bug.
Candidate INDEX principle once it recurs outside payments.

— seen in [2026-01-05 payments retry loop](2026-01-05-payments-retry-loop.md) · 2026-01-05
  "we tuned the backoff twice and it looked better, because the race got rarer"
— status: watching · 1 daily

## Contested

### Contested: *(example)* is reconciliation a safety net or a crutch?

One daily treats the `[[acme-worker]]` sweep as a guardrail that must never
carry correctness; a later session leaned on it as the fix. Both can't be the
house rule.

— seen in [2026-01-05 payments retry loop](2026-01-05-payments-retry-loop.md) · 2026-01-05
  "it must never become the thing that makes correctness work"
— seen in *(a later daily would be cited here)*
— status: contested · 2 dailies

## Recall

- `/debrief-dreams` — print this file (cheapest possible recall).
- `/debrief-dreams add <observation>` — human-authored, writes directly.
- `/debrief dream` — the cross-session pass: matches new dailies against these
  entries, proposes new ones, promotions, and refutations — all human-approved.
- This file is deliberately **not** indexed by `/debrief-search`: it is a
  living surface read whole, and a stale `watching` entry surfacing in search
  would misreport the evidence state. The dailies behind every receipt *are*
  indexed — the reasoning is searchable; the watchlist is not.

---

last dream: never
