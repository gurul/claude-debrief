---
description: Write a curated session debrief, aggregate a day's sessions into the daily debrief (day mode), or star a must-know into HIGHLIGHTS.md (highlight mode)
argument-hint: "[day [YYYY-MM-DD]] | [highlight <the must-know>]"
---

# /debrief — per-repo debrief memory system

Arguments: `$ARGUMENTS`

**Scope guard (check first):** this is a personal, user-level command that applies only to repos wired for debrief memory. If `debrief/INDEX.md` does not exist in the current project root, say so ("this repo has no debrief system") and stop. Never create a `debrief/` folder in a repo that hasn't opted in — memory is deliberate, not something a command sprays into every checkout.

The `debrief/` folder is this repo's curated project memory. Read `debrief/README.md` for the full system. Three modes, selected by the arguments above:

## Mode 1: no arguments → curated session debrief (current session)

Write a debrief of THIS conversation from live context (richer than any transcript reconstruction):

1. Read `debrief/INDEX.md` glossary to reuse exact `[[entity]]` names.
2. Write `debrief/sessions/<today>/<HHMM>-<slug>.md` (create the dir if needed) with frontmatter `status: curated`, `session_id` (if known), `ended: <now>`, a **`commits:` line listing every SHA this session committed** (bare SHAs, space-separated — repos are auto-resolved across whatever `.system/repos.json` lists; this is what makes the session appear in the graph's session→repo→file→function drill-down), and the same sections as machine drafts: What was attempted / What actually happened / Open threads / Candidate glossary entities / Candidate principle observations. Since you have live context, be precise about what was *verified working* vs merely written. See `debrief/README.md` "Code-provenance layer".
3. If `debrief/sessions/queue.jsonl` has a pending entry for this session, that's fine — day aggregation prefers `status: curated` over `status: machine-draft` for the same session_id.
4. Do NOT touch `INDEX.md` or daily debrief files — that happens only in day mode.

## Mode 2: `day [YYYY-MM-DD]` → aggregate into the daily debrief

Date defaults to today. This is the **curation gate** — the human-quality pass where machine drafts become durable memory.

1. **Collect.** Read every file in `debrief/sessions/<date>/`. Cross-check `debrief/sessions/queue.jsonl` for entries dated `<date>` with `status: pending` **or** `status: failed` that have NO corresponding draft file — for those, read the raw transcript at `transcript_path` directly — falling back to `debrief/sessions/raw/<date>/*.jsonl.gz` if the live transcript is gone (Claude Code deletes them after ~30 days); either way read as text, chunked ≤2000 lines, first + last chunks priority. `failed` is the settled form of a dead drafter (the sweep flips `pending → failed` after 15 min + 3 attempts), not a terminal state: recover it from `transcript_path`, then the raw archive. Only when both are gone is the episode unrecoverable — say so and move on. If multiple drafts share a `session_id`, use the newest; prefer `curated` over `machine-draft`. Then check the queue for `status: unconsumed` entries on OTHER dates — drafts that landed and were never curated. Name those dates to the human at the end of the run so they can `/debrief day <that-date>`; don't fold another day's sessions into today's daily.
2. **Synthesize** one daily debrief at `debrief/<date>-<slug>.md`, matching the established house format — study the existing dailies in `debrief/` as references, and keep new ones consistent with them. Capture *reasoning and tradeoffs*, not just outcomes. Use `[[entity]]` links consistent with the INDEX glossary. Ground file:line claims only where you can verify them against the repo now. Flag every claim inherited from a machine draft that was never verified — do not launder drafts into fact. Add a `commits:` frontmatter line (bare SHAs, the union of the day's session commits) so the daily appears in the graph's session→repo→file→function drill-down (see `debrief/README.md`).
3. **Review with the human.** Present the synthesized debrief and iterate on corrections before finalizing. Ask specifically about anything the drafts marked unverified. This step *is* the system — do not skip it.
4. **Update `debrief/INDEX.md`** per its own convention: add a newest-first table row (date, slug link, TL;DR hook, key entities); fold new entities into the glossary (rewire existing nodes toward corrected understanding rather than duplicating); promote a principle only if it now recurs in ≥2 debriefs.
5. **Collect the ★ candidates.** Grep the day's drafts and the new daily for lines beginning `★`. Each is a *proposal*, never a promotion. Present them to the human as a short list alongside the debrief review and ask which deserve starring; append the approved ones to `debrief/HIGHLIGHTS.md` per Mode 3's format, with provenance pointing at today's daily. Silence is a no — an unanswered candidate stays unstarred. If a candidate restates something already starred, say so and skip it rather than duplicating; the layer's value is inversely proportional to its length.
6. **Harvest the todo layer.** From the day's drafts and the new daily, collect Open threads that would otherwise be *lost* — follow-ups nobody will remember next week, not threads the next session will naturally pick up. Present them alongside the debrief review as todo *candidates*; the human approves each into `debrief/TODO.md`'s `## Open` section (format per that file's rules, provenance pointing at today's daily). In the same pass, reread the existing `## Open` items and propose closing any the day's work resolved — a person confirms, and the item moves to `## Closed (recent)` with a pointer to today's daily. Silence is a no on both counts. If `debrief/TODO.md` does not exist and there are approved candidates, recreate it in the standard shape — title, the layer table and rules (see `debrief/README.md` "The todo layer"), then `## Open` / `## Closed (recent)` / `## Recall`.
7. **Archive.** Move consumed session drafts to `debrief/sessions/archive/<date>/` and rewrite the matching `queue.jsonl` entries with `"status": "aggregated"`. A consumed `failed` entry has no draft file to move — still rewrite its queue entry to `"status": "aggregated"` so it stops surfacing as recoverable; otherwise `/debrief-backlog` re-reports it every day after you've already curated it.
8. **Report the backlog.** Count queue entries with `status: unconsumed` (or `status: pending` with a draft file on disk) across ALL dates, and state the number in your day-debrief summary — e.g. "unconsumed-draft backlog: N (oldest: <date>)". These are landed drafts no daily ever read. If any belong to dates with no daily at all, flag those dates explicitly — they are missing episodic memory, not just unread detail.
9. **A daily already existing for `<date>` is NOT a reason to skip.** Dailies written mid-day go stale by evening — sessions that end after the daily was written are exactly the ones most likely to stay unmined. If the daily predates any session draft for the date, APPEND a clearly-marked "Late-day addendum" section — never overwrite or rewrite existing daily prose (dailies are immutable episodic records; addenda are new episodes, allowed).
10. **Corrections rewire INDEX, not just the new daily.** When a draft or session corrects a claim a previous daily or INDEX row states as fact (a push state, a measurement, a root cause), do not only record the correction in today's file: rewire the affected INDEX row/glossary node to the corrected claim with a pointer to the correcting record. Corrections that stay buried in a new daily leave the semantic layer confidently stale — the exact failure the INDEX exists to prevent.

## Mode 3: `highlight <the must-know>` → star it into `HIGHLIGHTS.md`

The margin-star: something that must be known **forever**, recorded the moment the human says so. Unlike a principle, it needs no recurrence — a one-shot lesson you must never relearn is exactly the case this exists for.

1. If `debrief/HIGHLIGHTS.md` does not exist, create it from the template shape in `debrief/README.md` ("The starred layer") — title, the two rules, then `## The starred list`.
2. Append a new `### ★ <one-line claim>` section under `## The starred list`, **newest first**. Body: 2–4 sentences on the mechanism and the consequence. Then a provenance line: `— from [<source>](<link>) · starred <date>`, pointing at the daily/session that earned it, or `— starred <date>` when it came straight from conversation.
3. **Write it to survive without its context.** `★ the timeout was wrong` is useless in six months. Name the system, the mechanism, and what it cost. Assume the reader has forgotten the incident entirely.
4. Do not paraphrase away specifics — exact rule ids, exact flag names, exact file paths are the whole value.
5. This mode **may write directly**, with no review step. That is not an exception to the curation gate: the human is the author here, so the gate is already satisfied. What is still forbidden is *you* deciding something deserves a star — see "Both modes" below.
6. Confirm back with the one-line claim you recorded, so a bad phrasing gets caught immediately.

## Both modes

- Everything under `debrief/` is gitignored personal memory — never commit any of it.
- Episodic files (session drafts, daily debriefs) are immutable once written; only the INDEX semantic layer gets rewired.
- **`HIGHLIGHTS.md` is append-only and never pruned.** `INDEX.md` is deliberately lossy — it tracks what is true now — so it is the wrong home for a permanent must-know. Corrections to a highlight are made by appending a superseding entry that names the one it corrects, never by editing or deleting the original.
- **Never star anything on your own initiative.** In session mode you may *propose* by marking a line `★ (candidate)` in the session note; only a human running `/debrief highlight`, or approving one during a day pass, promotes it. An agent that stars its own conclusions is laundering, which is the failure the whole gate exists to prevent.
- **The same gate covers `TODO.md`.** Open threads become tracked todos only through the day-mode review (step 6) or the human's explicit word via `/debrief-todos` — never on your own initiative, and closing an item is gated identically.
