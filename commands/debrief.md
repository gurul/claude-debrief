---
description: Write a curated session debrief, or aggregate a day's sessions into the daily debrief (day mode)
argument-hint: "[day [YYYY-MM-DD]]"
---

# /debrief — per-repo debrief memory system

Arguments: `$ARGUMENTS`

**Scope guard (check first):** this is a personal, user-level command that applies only to repos wired for debrief memory. If `debrief/INDEX.md` does not exist in the current project root, say so ("this repo has no debrief system") and stop. Never create a `debrief/` folder in a repo that hasn't opted in — memory is deliberate, not something a command sprays into every checkout.

The `debrief/` folder is this repo's curated project memory. Read `debrief/README.md` for the full system. Two modes, selected by the arguments above:

## Mode 1: no arguments → curated session debrief (current session)

Write a debrief of THIS conversation from live context (richer than any transcript reconstruction):

1. Read `debrief/INDEX.md` glossary to reuse exact `[[entity]]` names.
2. Write `debrief/sessions/<today>/<HHMM>-<slug>.md` (create the dir if needed) with frontmatter `status: curated`, `session_id` (if known), `ended: <now>`, a **`commits:` line listing every SHA this session committed** (bare SHAs, space-separated — repos are auto-resolved across whatever `.system/repos.json` lists; this is what makes the session appear in the graph's session→repo→file→function drill-down), and the same sections as machine drafts: What was attempted / What actually happened / Open threads / Candidate glossary entities / Candidate principle observations. Since you have live context, be precise about what was *verified working* vs merely written. See `debrief/README.md` "Code-provenance layer".
3. If `debrief/sessions/queue.jsonl` has a pending entry for this session, that's fine — day aggregation prefers `status: curated` over `status: machine-draft` for the same session_id.
4. Do NOT touch `INDEX.md` or daily debrief files — that happens only in day mode.

## Mode 2: `day [YYYY-MM-DD]` → aggregate into the daily debrief

Date defaults to today. This is the **curation gate** — the human-quality pass where machine drafts become durable memory.

1. **Collect.** Read every file in `debrief/sessions/<date>/`. Cross-check `debrief/sessions/queue.jsonl` for entries dated `<date>` with `status: pending` that have NO corresponding draft file (failed drafter run) — for those, read the raw transcript at `transcript_path` directly (as text, chunked ≤2000 lines, first + last chunks priority). If multiple drafts share a `session_id`, use the newest; prefer `curated` over `machine-draft`. Then check the queue for `status: unconsumed` entries on OTHER dates — drafts that landed and were never curated. Name those dates to the human at the end of the run so they can `/debrief day <that-date>`; don't fold another day's sessions into today's daily.
2. **Synthesize** one daily debrief at `debrief/<date>-<slug>.md`, matching the established house format — study the existing dailies in `debrief/` as references, and keep new ones consistent with them. Capture *reasoning and tradeoffs*, not just outcomes. Use `[[entity]]` links consistent with the INDEX glossary. Ground file:line claims only where you can verify them against the repo now. Flag every claim inherited from a machine draft that was never verified — do not launder drafts into fact. Add a `commits:` frontmatter line (bare SHAs, the union of the day's session commits) so the daily appears in the graph's session→repo→file→function drill-down (see `debrief/README.md`).
3. **Review with the human.** Present the synthesized debrief and iterate on corrections before finalizing. Ask specifically about anything the drafts marked unverified. This step *is* the system — do not skip it.
4. **Update `debrief/INDEX.md`** per its own convention: add a newest-first table row (date, slug link, TL;DR hook, key entities); fold new entities into the glossary (rewire existing nodes toward corrected understanding rather than duplicating); promote a principle only if it now recurs in ≥2 debriefs.
5. **Archive.** Move consumed session drafts to `debrief/sessions/archive/<date>/` and rewrite the matching `queue.jsonl` entries with `"status": "aggregated"`.
6. **Report the backlog.** Count queue entries with `status: unconsumed` (or `status: pending` with a draft file on disk) across ALL dates, and state the number in your day-debrief summary — e.g. "unconsumed-draft backlog: N (oldest: <date>)". These are landed drafts no daily ever read. If any belong to dates with no daily at all, flag those dates explicitly — they are missing episodic memory, not just unread detail.
7. **A daily already existing for `<date>` is NOT a reason to skip.** Dailies written mid-day go stale by evening — sessions that end after the daily was written are exactly the ones most likely to stay unmined. If the daily predates any session draft for the date, APPEND a clearly-marked "Late-day addendum" section — never overwrite or rewrite existing daily prose (dailies are immutable episodic records; addenda are new episodes, allowed).
8. **Corrections rewire INDEX, not just the new daily.** When a draft or session corrects a claim a previous daily or INDEX row states as fact (a push state, a measurement, a root cause), do not only record the correction in today's file: rewire the affected INDEX row/glossary node to the corrected claim with a pointer to the correcting record. Corrections that stay buried in a new daily leave the semantic layer confidently stale — the exact failure the INDEX exists to prevent.

## Both modes

- Everything under `debrief/` is gitignored personal memory — never commit any of it.
- Episodic files (session drafts, daily debriefs) are immutable once written; only the INDEX semantic layer gets rewired.
