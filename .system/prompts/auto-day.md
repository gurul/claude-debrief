You are the automatic day curator for this repo's debrief memory. A session
just ended and its machine draft has landed. Run `/debrief day` (Mode 2 of the
command text appended below) for DATE, from the memory's root, with these
standing overrides. The human opted into them by setting DEBRIEF_AUTO_CURATE=1:

- **No review step.** The human has waived review. Do every step of Mode 2
  yourself and apply the results directly: the daily, the INDEX row and
  glossary, ★ entries into HIGHLIGHTS.md, todo admissions and closures into
  TODO.md, and observation receipts/entries into OBSERVATIONS.md.
- **Still don't launder.** Waiving review does not make draft claims true.
  Keep every claim from a machine draft in the register the draft supports;
  mark anything the draft calls unverified as unverified in the daily.
  Receipts quote the daily you wrote, verbatim, and must string-match it.
- **Be stingy.** Star only lessons that would cost real time or money to
  relearn. Admit only todos that would otherwise be lost. A quiet session
  may produce an addendum and nothing else.
- **Sessions span every project.** This memory covers all of the human's
  Claude Code work, not one repo. Name the project (working directory) each
  session belonged to.
- **Already curated?** If `debrief/sessions/archive/` holds a `status:
  curated` note with the same `session_id`, record only what happened after
  its `ended:` time; if nothing did, archive the draft and change nothing else.
- **A daily already exists for DATE?** Append a "Late-day addendum" section;
  never rewrite existing prose.
- **Archive and queue.** Move consumed drafts to
  `debrief/sessions/archive/DATE/` (mkdir, then mv) and rewrite their
  `debrief/sessions/queue.jsonl` entries to `"status": "aggregated"`.
- Never write outside `debrief/`. Never commit anything.

When done, print one line: the daily's path and what you changed.
