# Taste — how the work gets shaped

> **Template.** This is the taste layer: a ledger of the moments the human
> judged a piece of work or changed its direction, each with their exact words
> and a path to the piece itself. Replace this blockquote and the example
> entries with your own.

An agent makes something. The human keeps it, changes it, rejects it, or goes
back to a version they passed over. Each of those moments says something about
what they look for, and a daily turns it into a sentence about the outcome.
This file keeps the moment whole: what was there before, what they said, what
it became, and where both versions can be opened.

| Layer | Question it answers | Mutability |
|---|---|---|
| Episodic — dailies, `sessions/` | what happened | immutable |
| Semantic — [`INDEX.md`](INDEX.md) | what is true now | pruned and rewired |
| Starred — [`HIGHLIGHTS.md`](HIGHLIGHTS.md) | what must never be relearned | append-only |
| Todo — [`TODO.md`](TODO.md) | what is still open now | items open and close |
| Observations — [`OBSERVATIONS.md`](OBSERVATIONS.md) | what might be a pattern | entries accumulate receipts |
| **Taste — this file** | **what the human did with a piece of work, and what that might say about what they look for** | Shapings append-only; Readings revised, never deleted |

## The rules

1. **A Shaping is a record, not an opinion.** It holds the human's words
   exactly as said. Nobody paraphrases them, improves them or explains them.
2. **A Shaping covers the piece it was made on.** Its scope is "this piece"
   unless the human's own words made it wider, and then those words are
   quoted. "No doodles on this film" is not "no doodles".
3. **A reason is quoted or it is "not stated".** A supplied reason is the
   writer's taste filed under the human's name.
4. **Shapings are append-only.** A later change of mind is a new Shaping that
   names the earlier one. The earlier one stays, because the pair is the
   evidence: what they first said, what they came back to, and what happened
   between.
5. **A Reading is a guess with receipts.** It needs Shapings on at least 2
   different pieces. It names the projects it was seen in. It says what the
   human *has* done, never what they always do. Shapings that disagree are
   kept together under one `contested` Reading and are never averaged.
6. **A Reading is a place to start from, never a rule to stop at.** It may
   not be used to refuse an idea, to skip an attempt, or to drop a direction.
   An agent that expects the human to dislike something says so and shows the
   Reading. It does not withhold the work. A Reading never becomes an INDEX
   principle or a highlight.
7. **Open the piece before you trust the entry.** Every Shaping carries a
   path under `sessions/material/` or a commit when one exists. The words
   were said about that image or that file, and the entry is one reading of
   them.

Who writes here: the day pass records Shapings from the day's sessions. The
dream pass writes and revises Readings. `/debrief-taste add` records a
Shaping on the human's word. An agent working inside a session writes nothing
here on its own initiative.

## Readings

### Reading: has asked for less on screen when an agent's first version explained itself *(example)*
Seen in two projects. In both, the first version carried helper text and the
version that was kept did not.
— seen in Shaping 2026-01-05 · payments · retry banner: "it should not have to tell me what it is"
— seen in Shaping 2026-01-04 · auth · sign-in screen: "cut the paragraph, the button is enough"
— status: watching · 2 pieces · projects: payments, auth

## Shapings

Newest first.

### 2026-01-05 · payments · retry banner *(example)*
- **Before:** the agent's banner with a title, two lines of explanation and a "Learn more" link — `sessions/material/0000aaaa/images/1f2e3d4c5b6a.png`
- **The human:** "it should not have to tell me what it is"
- **After:** one line and a retry button — `sessions/material/0000aaaa/images/9a8b7c6d5e4f.png`, commit `abc1234`
- **Kind:** changed
- **Scope:** this piece
- **Reason:** not stated
— from [2026-01-05-payments-retry-loop.md](2026-01-05-payments-retry-loop.md) · session `0000aaaa`

### 2026-01-04 · auth · sign-in screen *(example)*
- **Before:** the agent's screen with a paragraph above the form
- **The human:** "cut the paragraph, the button is enough"
- **After:** the form alone, commit `def5678`
- **Kind:** changed
- **Scope:** this piece
- **Reason:** "the button is enough"
— from [2026-01-04-auth-session-cutover.md](2026-01-04-auth-session-cutover.md) · session `0000bbbb`

## Recall

```bash
python3 .system/debrief-search.py taste                 # the Readings, then the newest Shapings
python3 .system/debrief-search.py taste "sign-in screen" # Shapings and Readings on a topic
python3 .system/debrief-search.py source 0000aaaa       # the originals a Shaping points at
```

last reading pass: never
