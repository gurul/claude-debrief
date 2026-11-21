# Signals (`sessions/signals/`) — what the transcript shows, read by rule

## The gap

A reaction is recorded only when the agent in the session records it
([reactions](reactions.md)). An agent that is busy fixing the thing the human
complained about does not stop to record the complaint. In one live store,
across 60 sessions, the recorded reactions covered 23 of 959 human turns.

Three more things were in every transcript and in no layer:

- **A standing rule the human stated.** "From now on …" is said once, in the
  middle of other work. Nothing counted how often the same rule was said
  again.
- **What the agent claimed just before the human spoke.** "It is fixed and
  pushed", then "it still does not work". Each daily tells that story in
  prose. Nothing counted it.
- **Whether a promoted principle stopped the thing it names.** A principle
  goes into `INDEX.md` and keeps its place. Receipts keep arriving under it,
  and no one reads the dates.

## What runs, and when

```
session ends ─▶ debrief-react.py verify      recorded reactions, checked
             ─▶ debrief-signals.py read      sessions/signals/<sid8>.json
             ─▶ the drafter                  gets the block; may name reactions nobody recorded
             ─▶ debrief-signals.py adopt     keeps the ones whose words are in a human turn
             ─▶ the curator                  reads the draft and the checked record
```

`read` uses no model. It walks the transcript once and writes one record per
human turn:

| Field | What it holds |
|---|---|
| `line`, `at` | the transcript line and time of the turn (the same numbering `debrief-react.py` uses) |
| `text` | the turn, redacted, cut at 600 characters |
| `covered` | ids of the verified reactions whose words are in this turn |
| `rule` | the first sentence that reads as a standing rule, exact, or null |
| `rule_needs_its_turn` | true when the rule points back at the conversation ("like this") |
| `after.claim`, `after.claim_line` | the last sentence the agent wrote before this turn that says the work is finished |
| `after.passed`, `after.failed` | the kinds of check command that passed or failed since the turn before |
| `after.failed_again` | a check that passed and then failed inside that one stretch |

A headless session has no human turns, so the hook does not read it.

## The three patterns, and their limits

**A stated rule** is a sentence in a human turn that opens with "always",
"never", "from now on", "going forward" or "whenever", or that names its own
reach ("this is a rule", "make it standard practice", "every time", "so it
doesn't happen again"), or that orders something not to happen again. A
question is not a rule. A turn over 1200 characters is a paste, and is not
read for rules. The pattern is narrow on purpose. On the same 60 sessions a
wider one matched plain statements that only held the words "whenever",
"for every" or "in the future".

**A check** is a shell command that starts with a test, build, lint or
type-check runner (`pytest`, `pnpm test`, `npx tsc`, `cargo test`, `make`,
`… --selftest`, and the like). It failed when the tool result is an error or
its output has a failure line. `cat pytest.ini` and `grep jest` check
nothing.

**A claim** is a sentence with a word such as "fixed", "verified", "pushed"
or "tests pass" and with no word that hedges, plans or denies ("not",
"still", "will", "should", "once", a question mark). The quote is kept, so
the reader judges it. The pattern does not.

None of these is a judgment. A pattern can miss, and it can match a sentence
that means something else. Every report prints the exact words and the
transcript line, so a wrong match costs one look.

## Reactions the session did not record

The drafter gets the uncovered turns under `UNRECORDED TURNS` and writes the
ones that judge a piece of work under `## Unrecorded reactions`, one line
each in a fixed form. `adopt` then does to each line what `verify` does to a
recorded reaction:

- **Words found in a human turn:** added to `sessions/reactions/<sid8>.jsonl`
  with `verified: true` and `source: "drafter"`, dated at the turn.
- **Words not found:** added with `verified: false` and the reason. They
  never count as the human's words.
- **Words already inside a recorded reaction:** not added a second time.
- **A line in another form, or a sentiment outside the four:** counted as
  malformed and dropped. Nothing is guessed.

A second run adds nothing: the id is a hash of the session and the words.

`/debrief-sentiment` shows these reactions with "(drafter's reading)" and the
line "found by the drafter after the session".

## A second reading of each label

`docs/reactions.md` used to list "no check of the label" as not built. The
drafter now gives its own sentiment for each recorded reaction under
`## Label check`. `adopt` stores it in the signals file. The first label is
never changed. Where the two differ, `list` prints both, and `summary` counts
how many were read differently. Two readings that disagree are a reason to
read the words. Neither one wins.

## The reports

```bash
S="$D/.system/debrief-signals.py"
python3 "$S" landed --dir "$D" [--project P] [--since YYYY-MM-DD]
python3 "$S" rules --dir "$D" [--project P] [--since YYYY-MM-DD]
python3 "$S" principles --dir "$D"
python3 "$S" echoes --file <draft or note> [--against <prompt.md> …]
python3 "$S" --selftest
```

`landed` lists each stretch of work that ended on a claim or a passing check
and was followed by a reaction that either reader labelled negative or mixed.
It prints both quotes. The count is a floor, because a reaction nobody
recorded or found is not counted. It also counts checks that passed and then
failed again inside one stretch.

`rules` lists the stated rules, most repeated first, with the sessions each
was said in. A rule said in two sessions was said again after it was first
given.

`principles` reads `INDEX.md` and `OBSERVATIONS.md` and counts, for each
promoted principle, the receipt dates after its promotion date:

- **Still recurring:** two or more dates after promotion. The text has not
  stopped it. A hook, a CI check or a test may.
- **Enforced:** the principle carries a line
  `— enforced by: <the hook, check or test> · since <date>`. Dates are
  counted from the enforcement date. With none, the text may leave `INDEX.md`
  on the human's word.
- **Quiet:** one date or none after promotion.

It counts dates only. A receipt after promotion may be a related shape and
not the same one, and the report says so. `dream.sh` runs it and gives the
text to the dream pass, which copies the still-recurring lines into the audit
file and proposes one mechanism for each. The dream pass never removes a
principle.

`echoes` finds a quoted line that repeats an example printed in a prompt
file. A drafter that copies the prompt's own example wrote nothing about the
session. `adopt` runs it on every draft and stores the hits in the signals
file.

## The rules

1. **By rule, never by judgment.** A pattern finds a rule sentence, a check
   and a claim. Whether a turn is a reaction is the drafter's reading. The
   string match decides whether the words are the human's.
2. **Counts only.** No command here writes a rule, a principle, a Shaping or
   a memory. Promotion and removal stay behind the gates they had.
3. **Evidence, not memory.** `debrief-search.py` does not index this folder.
4. **Text is redacted and fails closed.** No redactor means nothing is
   written.
5. **The hook never fails a session.** `read`, `prompt` and `adopt` print one
   line and exit zero. A failed read marks the `signals` stage in
   `capture_status.json`.
6. **Nothing leaves the machine.**

## What is not built

- **A model reading of each turn.** The split of a turn into feedback, a new
  task and a question is left to the drafter. No separate call does it.
- **Which step caused the reaction.** The report gives the last claim and the
  checks before the turn, not the action that was wrong.
- **A replay.** Nothing runs a past moment again with and without a lesson.
  The transcript format is internal, and this repo reads it and never
  replays it ([dream layer](dream-layer.md), "inceptions").
- **Enforcement.** The report names a principle that still recurs. Writing
  the hook or the check is a person's decision.

## Prior art

The reading half of [Songe](https://github.com/sous-labs/songe) (Apache-2.0)
reads a local model's conversations for corrections, stated rules and tool
results, and trains the model on them. Three ideas here come from its design:
read every user reply after the fact, treat a success as confirmed only until
something contradicts it, and reject an output that repeats the prompt's own
example. No code was copied. Songe then puts lessons into the prompt and the
weights with no person in between. This repo does not: a pattern here ends in
a count and a quote.
