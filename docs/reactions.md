# Reactions (`sessions/reactions/`) — how the work landed, in the human's words

## What it is for

A daily says "the header got calmer". It does not say that the human called
the first version "too loud", or that two days later he said "this is the one".
The drafter reads the transcript after the session and has to guess which piece
those words were about and where its before and after live. The agent in the
session does not have to guess. It knows.

So the agent records a reaction when it happens. The session-end hook then
checks the words against the transcript. The result is a sentiment record for
all of debrief, not only design: how each piece of work landed with the human,
in his words, at the moment he said them.

This changes one earlier rule. An agent inside a session used to write nothing
to memory. It may now write one thing: a reaction record to the inbox. The
record is not memory until the hook verifies it, and it never becomes a rule.

## File layout

```
sessions/reactions/
  inbox.jsonl          recorded in a session, not yet checked
  <sid8>.jsonl         checked against that session's transcript (verified true or false)
  unmatched.jsonl      older than 48 hours, and no session claimed it
  .lock                every writer takes it
```

One JSON object per line:

| Field | Written by | What it holds |
|---|---|---|
| `id` | record | 12 hex characters |
| `at` | record | UTC time of the record |
| `words` | record | the human's exact words; fragments joined by ` / ` |
| `about` | record | the piece or thing the words were about |
| `sentiment` | record | `positive`, `negative`, `mixed` or `neutral`: the agent's reading |
| `kind` | record | `kept`, `changed`, `rejected`, `returned-to`, `praise`, `frustration`, `correction`, `preference`, `question`, or null: the agent's reading |
| `before`, `after` | record | a store path under `sessions/material/`, a file path or a commit |
| `project` | record | `--project`, else the git top-level name of the cwd, else the cwd's base name |
| `agent_note` | record | one line of context from the agent, labelled as the agent's |
| `cwd` | record | the folder the record was made from |
| `session_hint` | record | the session id from the environment, when the harness sets one |
| `verified` | verify | `null` in the inbox, then `true` or `false` |
| `session`, `line`, `lines`, `verified_at` | verify | where the words were found |
| `why` | verify | why a record is not verified |
| `source` | adopt | `"drafter"` when the drafter found the words after the session ([signals](signals.md)); absent when the agent in the session recorded them |

## The rules

1. **The words are the record.** They are stored as given. Outer whitespace is
   trimmed. Nothing else changes: no typo fixes, no paraphrase.
2. **Labels are a reading.** Sentiment and kind are always shown as
   "(agent's reading)", or "(drafter's reading)" for a reaction the drafter
   found. A label never becomes a rule, and it is never a reason to hold work
   back from the human.
3. **Verification is mechanical.** Only words found in the human's own turns
   count as his. The check is a string match, never a judgment.
4. **Append-only.** A change of mind is a new record. Verification moves a
   record out of the inbox and adds fields. It never edits the words.
5. **Evidence, not memory.** `debrief-search.py` does not index this folder.
   The way in is `list`, `summary`, and the drafter. The drafter may turn a
   verified reaction about a piece of work into a Shaping.
6. **Record reactions to work only.** Record when the human reacts to work:
   approval, rejection, delight, frustration, a correction, a stated
   preference. Do not record instructions, questions about facts, or a mood
   inferred from silence. One record per reaction. Never ask the human to
   rate anything.
7. **Text is redacted and fails closed.** Every text field goes through
   `redact-transcript.py`. No redactor means nothing is written.
8. **Nothing leaves the machine.**

## How verification works

The session-end hook runs `verify` with the transcript and the session id.

- **Which records belong to the session.** A record with a `session_hint`
  belongs to the session with that id. A record without one belongs to the
  session when its `at` falls inside the transcript's time span (10 minutes of
  slack each side) and its cwd is the session's cwd or a folder under it.
- **What counts as the human's turn.** A `user` line that is not meta and not
  a sidechain, its text blocks only, and a message the human queued while the
  agent worked. System reminders are cut out. Harness text (skill bodies,
  command output, task notifications) is skipped. For a slash command the
  human typed, only its arguments count. Assistant text, tool input and
  results, subagent prompts and request blobs never count.
- **The match.** Both sides have runs of whitespace collapsed to one space and
  curly quotes made straight. Case counts. The human's turns are redacted the
  same way the record was. `words` is split on ` / `, quotes around each
  fragment are dropped, and each fragment must be found in some human turn.
- **Found:** the record moves to `<sid8>.jsonl` with `verified: true`, the
  session id and the transcript line.
- **Not found, with a session hint:** the record moves to `<sid8>.jsonl` with
  `verified: false` and a `why` that names the missing fragment. It is kept,
  and it never counts as his words.
- **Not found, without a session hint:** the record stays in the inbox. A
  session running in parallel in the same folder may own it.
- **Older than 48 hours, claimed by no session:** `unmatched.jsonl` with
  `verified: false, why: "no session matched"`. A session that ran longer than
  48 hours can still claim its record from there when the words are found.
- Running `verify` twice changes nothing. `.jsonl` and `.jsonl.gz` both work.
  A line that does not parse is skipped.

## Commands

```bash
R="$D/.system/debrief-react.py"
python3 "$R" record --dir "$D" --words "too loud" --about "onboarding header" \
    --sentiment negative --kind rejected --before sessions/material/0000aaaa/images/1f2e.png
python3 "$R" verify --dir "$D" --transcript <path.jsonl[.gz]> --session <id>   # the hook runs this
python3 "$R" list --dir "$D" [topic] [--project P] [--sentiment S] [--since YYYY-MM-DD] [--all]
python3 "$R" summary --dir "$D" [--project P] [--since YYYY-MM-DD]
python3 "$R" --selftest
```

`--dir` defaults to `$DEBRIEF_DIR`.

`list` prints verified reactions whole, newest first: date, project, piece,
sentiment and kind, the words in quotes, before and after, session and line.
`--all` adds the unverified ones under a `NOT CONFIRMED` marker.

`summary` prints counts by project and sentiment and by ISO week, then the
five most recent negative and positive verified reactions with their words.
It also says how many reactions the drafter found afterwards, and how many
labels the drafter read differently.
It counts only. It draws no conclusion and writes no rule.

### Projects and groups

`--project` reads an optional registry at `<store>/.system/projects.json`:

```json
{"groups": {"studio": ["shop-web", "shop-ios", "kiosk"]},
 "aliases": {"shop-old": "shop-web"}}
```

Matching ignores case. A name resolves to its alias target, to a group (the
group name and every member), or to one project. `group/member` names one
member. With no registry, `--project` matches one project name. `summary`
without `--project` groups its counts under the registry's groups, then an
`Ungrouped` block.

Slash commands: `/debrief-react` records one reaction. `/debrief-sentiment`
wraps `list` and `summary`.

## Reactions nobody recorded, and a second reading

An agent records a reaction only when it stops to do so. After the session,
`debrief-signals.py` gives the drafter every human turn that no recorded
reaction covers. The drafter names the ones that judge a piece of work, and
`debrief-signals.py adopt` runs the same string match on each. The words that
are found go into `<sid8>.jsonl` with `verified: true` and
`source: "drafter"`. The rest are kept with `verified: false`.

The drafter also gives its own sentiment for each recorded reaction. The
first label is never changed. Where the two differ, `list` prints both and
`summary` counts them. See [signals](signals.md).

## What is not built

- **No return signal.** A reaction says what the human said, not what he did
  later with the piece.
- **Subagents.** A subagent's record carries the session id its harness sets.
  Whether that is the parent session's id was not checked. Without a match,
  the record waits in the inbox and goes to `unmatched` after 48 hours.

## A paragraph for `CLAUDE.md`

Agents record reactions only when they are told to. Add this to the
instructions your agents read:

```markdown
## Reactions: record how the work landed

When the human reacts to a piece of work in a session (approval, rejection,
delight, frustration, a correction, a stated preference), record it once, at
that moment:
`python3 "$DEBRIEF_DIR/.system/debrief-react.py" record --words "<their exact words>" --about "<the piece>" --sentiment positive|negative|mixed|neutral [--kind <kind>] [--before <path or commit>] [--after <path or commit>]`.
Copy the words exactly from their message: no paraphrase, no typo fixes. Join
two parts of one message with " / ". Do not record instructions, questions
about facts, or a mood you infer. Never ask them to rate anything. The
sentiment label is your reading. It is never a rule and never a reason to hold
work back. The session-end hook keeps only words found in their own turns.
```
