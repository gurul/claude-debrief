---
description: Show the debrief todo layer — open threads being tracked across sessions — or add/close an item on the human's word
argument-hint: "[add <the item>] | [done <which item>]"
---

# /debrief-todos — what is still open

Arguments: `$ARGUMENTS`

**Scope guard:** applies only to repos wired for debrief memory. If `debrief/INDEX.md` does not exist in the current project root, say so ("this repo has no debrief system") and stop.

`debrief/TODO.md` is the todo layer: open threads a human chose to track until resolved. Read `debrief/TODO.md` (the rules at the top especially) before acting. Three modes, selected by the arguments above:

## Mode 1: no arguments → show the open list

1. Read `debrief/TODO.md`. If it does not exist, say so and point at `/debrief-todos add <item>` — never create the file just to show an empty list.
2. Present the **Open** section verbatim, oldest first, each with its provenance line. Then a one-line count of recently closed items — do not print the closed section unless asked.
3. This mode is read-only. Do not check anything off, do not editorialize about which items look stale — surfacing is not resolution.

## Mode 2: `add <the item>` → admit an item, immediately

The human is the author here, so the curation gate is already satisfied (same reasoning as `/debrief highlight`).

1. If `debrief/TODO.md` does not exist, recreate it in the standard shape — title, the layer table and rules (see `debrief/README.md` "The todo layer"), then `## Open` / `## Closed (recent)` / `## Recall`.
2. Append to `## Open`: a `- [ ]` item written to survive without its context — name the system, the file, the exact thing left undone; reuse `[[entity]]` names from `debrief/INDEX.md`. Provenance line: `— opened <today>`, plus a link to the relevant daily/session if one exists.
3. If the item restates an existing open item, say so and skip it rather than duplicating.
4. Confirm back with the one-line item you recorded, so a bad phrasing gets caught immediately.

## Mode 3: `done <which item>` → close an item, on the human's word

1. Find the matching open item; if the reference is ambiguous, list the candidates and ask — never guess which thread the human means.
2. Move it to `## Closed (recent)` as `- [x]`, appending `— closed <today> by [<daily>](<link>)` when a daily records the resolution, or `— closed <today>` when it came straight from conversation. Keep the original `opened` provenance.
3. If `## Closed (recent)` has grown past a handful of entries whose closing dailies exist, offer to prune the oldest — the daily is the durable record, this file is a working surface.

## All modes

- Everything under `debrief/` is gitignored personal memory — never commit any of it.
- **Never add or close an item on your own initiative.** In-session you may *propose* ("this looks like a todo candidate — want me to add it?"); only the human's explicit word, here or during a `/debrief day` review, writes to the file. An agent that files or closes its own todos is laundering, same as an agent that stars its own conclusions.
- Day mode (`/debrief day`) is the batch path: it harvests the day's open threads as candidates and proposes closures during the review step. This command is the immediate path. Both end at the same human gate.
