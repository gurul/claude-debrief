# Superseded — claims a later day nullified

Dailies are immutable, so a fact that stopped being true is not deleted. It is
invalidated here, one line per dead section, pointing at the doc that replaced
it. `/debrief-search` sinks these chunks below every live one and prints
`⊘ superseded by <doc>`, so an agent sees the old claim and where its
replacement lives instead of acting on it.

Written by the dream pass (`/debrief dream`, or the daemon with
`DEBRIEF_AUTO_DREAM=1`) or by hand. Format, one per line:

    - <old-daily>.md § <Section heading> → <newer-daily>.md — what changed

Omit `§ <Section heading>` to invalidate the whole doc. The heading must match
the section exactly (case-insensitive). Lines that do not match the format are
ignored, so prose like this header is safe.

## Ledger

- 2026-01-04-auth-session-cutover.md § Example section → 2026-01-05-payments-retry-loop.md — template example, replace with your own
