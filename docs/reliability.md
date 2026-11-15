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

## Capture status

Each SessionEnd stage marks its outcome in `.system/capture_status.json`
(machine state, gitignored) through `.system/capture-status.py`: `archive`,
`material`, `draft`, `memory`, `reactions`, and any stage another hook adds.
Each stage keeps `last_ok_at`, `failed_since_ok`, `first_failed_at` and
`last_error`. Writes take a file lock and replace the file whole, so two
session ends at once lose no mark.

While a stage is failing, the SessionStart hook adds one clause to what it
prints, for example:

```
Debrief capture is failing: material (3 failed runs since 2026-11-12: extract-material.py missing).
```

It prints nothing while every stage is fine. Before this, a failure was one
line in `hook.log`, among thousands of routine lines.

The daemon runs the hook every 15 minutes for the repair sweep only. It sets
`DEBRIEF_SWEEP_ONLY=1`, and the hook stops after the sweep without logging.
That call used to log "could not parse hook input" each time, which was 2158
of 4935 lines in a live `hook.log`. Input that is really bad is still logged.

`python3 .system/capture-status.py --selftest` checks ok, fail, recovery,
concurrent marks and silence on a clean store.

## Other session-end stages

- **Subagent transcripts.** With `DEBRIEF_RAW_ARCHIVE=1`, the transcripts in
  `<session>/subagents/` (nested for workflows) are redacted file by file
  into `sessions/raw/<date>/<HHMM>-<sid8>.subagents/`, with the same
  retention as the main copy. `DEBRIEF_RAW_SUBAGENTS=0` leaves them out;
  they can be large. Claude Code deletes them together with the main
  transcript, so the daemon's late path still finds them while the main
  transcript is there.
- **Memory notes.** With `DEBRIEF_MATERIAL=1`, `.system/memory-snapshot.py`
  stores each changed auto-memory note of the session's project, redacted,
  in `sessions/material/<sid8>/memory/` with an `index.json`. The last state
  per project is in `.system/memory-state/` (gitignored). It runs only while
  the transcript is less than an hour old, like the file snapshot.
- **Reactions.** When `.system/debrief-react.py` exists, the hook runs its
  `verify` step and the drafter gets the verified reactions.
- **Headless sessions.** When the transcript's first prompt came from a
  script, the queue entry gets `"synthetic": "headless"` and the drafter is
  told so.

Each helper is optional. When one is missing, the hook skips that stage and
still queues the session; `archive-selftest.sh` checks this.

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
