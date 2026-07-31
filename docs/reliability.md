# Reliability

Drafters die — observed in the wild as "API Error: Connection closed
mid-response" and a logged-out CLI. The queue receipt outlives the drafter, so
every session end runs a **self-repair sweep**: pending entries older than 15
minutes whose draft never landed get respawned (max 3 attempts, then
`status: failed`). A lock dir serializes concurrent session ends. The hook always
exits 0 and logs to `.system/hook.log` — check there first when a draft doesn't
appear.

Guards: recursion (the drafter is itself a Claude session whose exit fires this
hook — `DEBRIEF_GENERATION=1` breaks the loop), missing transcript, and trivial
sessions (<30 transcript lines).

## Archived drafts are not dead drafters

The sweep treats an **archived** draft as proof of aggregation rather than a
dead drafter. Day mode *moves* the drafts it consumes into
`sessions/archive/<date>/`, which empties the original path — so without that
check an aggregated session is indistinguishable from a failed one, and the
sweep re-drafts already-curated work (one headless run per session end, three
times, then marks it `failed` while its draft sits in the archive). It heals
the status instead.

## Queue statuses

`pending` means one thing only: **awaiting the drafter.** A draft that landed
but that no `/debrief day` ever consumed used to keep that status forever, so
an unread backlog grew invisibly (16 entries over two weeks in a live install).
Entries whose draft is on disk and older than 24h now become `unconsumed`, and
every sweep logs the count and the dates:

```
unconsumed-backlog: 8 drafts await a day-debrief (dates: 2026-07-06,2026-07-08,…)
```

Only `/debrief day <date>` clears it — archiving the draft flips the entry to
`aggregated` via the heal above. Queue statuses in full: `pending` (drafter
running) → `unconsumed` (draft waiting on curation) → `aggregated` (consumed by
a daily), or `failed` (drafter died 3×, or its transcript is gone).

## Surfacing the backlog

That log line is the **detection** end of the loop; the **surfacing** end is a
SessionStart hook, `.system/backlog-nudge.sh`, which on a fresh start injects a
one-line backlog summary into the session (once per day, silent on a clean
queue). `/debrief-backlog` is the human-facing view of the same queue —
`.system/debrief-backlog.py` prints the exact `/debrief day <date>` command for
each stranded date, oldest first. Both are strictly read-only: they never write
a daily, `INDEX.md`, or a queue status. Draining stays a human `/debrief day`
pass — the curation gate, unchanged.

## `failed` is not terminal

`failed` is **not terminal** when the material survives. `/debrief day` reads a
failed entry's `transcript_path` directly, falling back to the raw cold archive;
`debrief-backlog.py` labels each failed entry `recoverable` (transcript or raw
on disk) or `terminal` (both gone) so you know which are worth a curation pass.
`debrief-backlog.py selftest` and `backlog-selftest.sh` check these invariants —
including that a scan leaves the queue byte-identical, and that the tools resolve
their debrief dir through a symlinked install rather than back to this clone.
