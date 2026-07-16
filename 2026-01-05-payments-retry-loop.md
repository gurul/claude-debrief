---
commits: 9c81346 9bdcdc9 68a6083
prs:
  acme-api: 412
---

# Payments retry loop — idempotency keys before retries

> **Example debrief.** Ships with the repo so a fresh clone renders a populated
> viewer. Delete it (and its sibling) once you have memory of your own — the
> `commits:` SHAs above are fabricated and won't resolve against your repos.

## What happened

Retrying a failed charge was double-charging a small number of customers. The
retry itself was fine; the charge wasn't idempotent, so a timeout that had
*actually succeeded upstream* got retried into a second charge.

Fix: derive an `[[idempotency-key]]` per order+attempt-window and pass it on every
call, then let `retryWithBackoff` wrap `[[chargeCard]]`. Retry after idempotency,
never before.

## Why it took a day

The first read was "the retry is too aggressive" — we tuned the backoff twice and
it looked better, because the race got rarer. It was never a backoff bug. The
tell we ignored for hours: the duplicate charges had *different* request ids but
identical amounts within ~2s.

## Decisions

- **Key on order id + attempt window, not request id.** Request ids are unique per
  attempt, which is exactly what makes them useless as a dedupe key.
- **Reconciliation stays in `[[acme-worker]]`.** It's a safety net, not the fix —
  it must never become the thing that makes correctness work.

## Open threads

- `reconcileCharges` still sweeps the full day; fine at current volume, won't hold.
- No alert fires on a duplicate charge — we found this from a support ticket.
