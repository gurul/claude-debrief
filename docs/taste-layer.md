# The taste layer (`TASTE.md`) — what the human did with the work

## The gap

The human works with agents the way an editor works with drafts. An agent
proposes, goes off on a tangent, makes three versions. The human keeps one,
changes another, rejects the third, and a week later goes back to the one they
rejected. That shaping is where their taste is. None of the other layers keeps
it:

- a **daily** records the outcome ("the banner is one line now") and loses
  what they said and what they were looking at;
- an **INDEX principle** is a rule, and a judgment on one piece is not a rule;
- an **auto-memory** line such as "X was rejected, do not propose it again"
  keeps the verdict, drops the reason and the piece, and applies it everywhere.

The last one is the real harm. A local decision becomes a standing order, and
then no agent ever shows the human the thing that might have changed their
mind.

## Two kinds of entry

**A Shaping** is one moment: the piece, what was there before and who made it,
the human's exact words, what it became, the kind (`kept`, `changed`,
`rejected`, `returned to`, `made by the human`), the scope, and the reason.
Before and After carry a path under `sessions/material/` or a commit, so the
piece can be opened ([material](material.md)).

**A Reading** is a guess about what the human looks for, with the Shapings
that support it listed under it.

## The rules, and why each one is there

1. **Exact words.** A paraphrase is the writer's judgment under the human's
   name. "Too loud" and "reduce visual weight" are different instructions.
2. **Scope is the piece.** Wider only when the human's words made it wider,
   and those words are quoted. This is the rule that stops one rejection
   becoming a ban.
3. **A reason is quoted or "not stated".** A plausible reason that nobody gave
   is the most convincing kind of wrong.
4. **Shapings are append-only.** A change of mind is a new Shaping that names
   the old one. The pair is worth more than either: the first judgment, the
   return, and what happened between them.
5. **A Reading needs 2 different pieces**, names its projects, and says what
   the human *has* done. Shapings that disagree stay together under one
   `contested` Reading.
6. **A Reading is never a reason to withhold work.** An agent that expects a
   direction to be disliked shows it anyway and names the Reading. A memory
   that only predicts approval narrows what the human sees to what they
   already chose. Work that could change their taste has to reach them.
7. **Open the piece.** The words were said about an image or a file.

## `returned to`

The strongest signal in the file. The human dismisses a study as unfinished,
gets the polished version, and keeps opening the study. A system that learned
only from the dismissal learned the opposite of the truth. So going back to an
earlier version, or picking up an idea that was passed over, is its own kind,
and the drafter is told to look for it.

## Ideas not taken

Every draft and daily also has an **Ideas not taken** section: the tangent,
the second layout, the reverted attempt, each with where it still lives. It
is in the daily, so search finds it. It is not in `TASTE.md`, because a
direction nobody judged says nothing about taste. It is kept because it is a
starting point.

## Who writes

| Writer | May write |
|---|---|
| The day pass (curator) | Shapings, copied from the day's drafts unchanged |
| The dream pass | Readings: new, supported, contested, set aside |
| The human, `/debrief-taste add` | a Shaping, directly |
| An agent inside a session | nothing |

The day pass does not interpret, and the dream pass does not edit Shapings.
Keeping the record and the guess in different hands is the same separation as
draft and record elsewhere in this system.

## Using it

Before designing, writing or naming something for the human:

```bash
debrief-search.py taste                  # Readings, then the newest Shapings
debrief-search.py taste "empty state"    # whole entries on a topic
debrief-search.py source 0000aaaa        # the before and after of a Shaping
```

`taste` prints whole entries, never snippets. A Shaping cut short loses its
scope and reason, which are the parts that stop it being misused.

## What is not built

- **Return signals from use.** Which pieces the human reopens, reuses or
  builds on, measured from later sessions and not from what they said. The
  manifest's content hashes make this countable (the same image hash in a
  later session is a return). Nothing reads it yet.
- **A reserved budget for directions a Reading argues against.** Rule 6 says
  an agent must not withhold. Nothing yet makes an agent spend effort on such
  a direction unprompted.
