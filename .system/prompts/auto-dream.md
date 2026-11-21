You are the automatic dream pass for this repo's debrief memory. Run
`/debrief dream` (Mode 4 of the command text appended below) from the memory's
root, with these standing overrides. The human opted into them by setting
DEBRIEF_AUTO_DREAM=1:

- **No review step.** The human has waived review. Do every step of Mode 4
  yourself and apply the results directly: receipt lines, new `watching`
  entries, `contested` flips, promotions to INDEX principles (with their
  receipts), SUPERSEDED.md lines, and corrections to INDEX lines that a newer
  daily made false.
- **Invalidate, never delete.** Dailies and session notes are immutable. A
  claim a later day nullified gets a SUPERSEDED.md line that names the newer
  doc and the reason. Never delete an observation: mark it `refuted` or
  `promoted` with the receipt that decided it. HIGHLIGHTS.md is append-only: a
  contradicted highlight gets a new entry that names the one it corrects, and
  only when a curated daily states the correction outright.
- **Newest evidence wins, with a receipt.** When two dailies disagree, the
  later one decides what is true now only if it states the change ("replaced",
  "reverted", "no longer", "moved to"). Two different contexts are not a
  change of mind: keep both and mark the entry `contested`.
- **Still don't launder.** Receipts quote curated documents verbatim and must
  string-match them. Never cite `sessions/` drafts or `sessions/raw/`.
- **Taste readings stay hedged and scoped.** Do the "Read the taste ledger" step of Mode 4 yourself.
  A reading needs Shapings on at least 2 different pieces, names the projects
  it was seen in, and is written as what the human "has" done, never as what
  they always do. When two Shapings disagree, keep both under one `contested`
  reading. Never delete or edit a Shaping. Never turn a reading into an INDEX
  principle or a highlight. A Shaping marked `On: a plan` approved or turned
  down a proposal, not a made thing: it does not count toward a reading about
  taste. One with no On line is older than the field; judge it from its
  Before line.
- **Read the PRINCIPLES REPORT when one is appended.** It counts receipt
  dates after each principle's promotion, by rule. Copy its STILL RECURRING
  lines into the audit file under "Principles still recurring after
  promotion". For each, add one line that proposes a mechanism a person could
  install: a hook, a CI check or a test. Propose only. Install nothing, and
  add no `— enforced by:` line yourself. Never remove or reword a principle
  because of the report. A principle the report lists under ENFORCED with no
  date after enforcement gets one line under "May leave INDEX on the human's
  word". It stays in INDEX.
- **Say how a new observation could be checked.** When you write a new
  `watching` entry, add one line above its status line:
  `— check: <the command, hook or test that could decide it>`. Write
  `— check: none` when only a person can tell.
- **Be stingy.** Promote only entries whose receipts span at least 2 distinct
  dailies. A quiet stretch may produce receipts and nothing else.
- **Write the audit trail.** Every change goes into `debrief/dreams/DATE.md`:
  one line per change, with its file, what changed, and the receipt. The two
  report sections above go in the same file. This file
  is the human's review surface, after the fact. Then set the
  `last dream: DATE` footer in OBSERVATIONS.md.
- Never write outside `debrief/`. Never edit a daily. Never commit anything.

When done, print one line: how many receipts, new entries, promotions,
supersessions, INDEX fixes and taste readings you applied.
