---
description: Show the debrief dream layer — cross-session patterns under watch, with receipts — or add an observation on the human's word
argument-hint: "[add <the observation>]"
---

# /debrief-dreams — what might be a pattern

Arguments: `$ARGUMENTS`

**Scope guard:** applies only to repos wired for debrief memory. If `debrief/INDEX.md` does not exist in the current project root, say so ("this repo has no debrief system") and stop.

`debrief/OBSERVATIONS.md` is the dream layer: cross-session patterns a human chose to watch, each carrying receipts — `— seen in` lines citing the curated dailies that support it. Read `debrief/OBSERVATIONS.md` (the rules at the top especially) before acting. Two modes, selected by the arguments above:

## Mode 1: no arguments → show the watchlist

1. Read `debrief/OBSERVATIONS.md`. If it does not exist, say so and point at `/debrief-dreams add <observation>` or `/debrief dream` — never create the file just to show an empty list.
2. Present the **Watching** and **Contested** sections verbatim, each entry with its receipt lines and status line. Mention the `last dream:` footer date — if it is old and dailies have accumulated since, note that a `/debrief dream` pass is available; the human decides.
3. This mode is read-only. Do not add receipts, do not editorialize about which entries look promotable or stale — surfacing is not synthesis.

## Mode 2: `add <the observation>` → admit an entry, immediately

The human is the author here, so the curation gate is already satisfied (same reasoning as `/debrief highlight` and `/debrief-todos add`).

1. If `debrief/OBSERVATIONS.md` does not exist, recreate it in the standard shape — title, the layer table and rules (see `debrief/README.md` "Five layers" and `docs/dream-layer.md`), then `## Watching` / `## Contested` / `## Recall`, and a `last dream: never` footer.
2. Append to `## Watching`: a `### Watching: <claim>` entry written in the register the evidence supports — keep the hedge; confidence lives in the status line. Reuse `[[entity]]` names from `debrief/INDEX.md`. Receipt: `— seen in [<daily>](<link>) · <date>` when a curated daily records it (add a short verbatim quote from that daily where the evidence is load-bearing), or `— noted <date>` when it came straight from conversation and no daily exists yet. Close with `— status: watching · <n> daily/dailies` (count distinct dailies cited, not mentions; a conversation-only entry counts 0).
3. If the observation restates an existing entry, say so and offer to append a receipt to that entry instead of duplicating.
4. Confirm back with the one-line claim you recorded, so a bad phrasing gets caught immediately.

## All modes

- Everything under `debrief/` is gitignored personal memory — never commit any of it.
- **Never admit, receipt, contest, promote, refute, or prune an entry on your own initiative.** In-session you may *propose* ("this looks like a pattern worth watching — want me to add it?"); only the human's explicit word, here or during a `/debrief dream` or `/debrief day` review, writes to the file. An agent that promotes its own observation manufactures a law from an anecdote.
- Promotion to an INDEX principle is never done from this command — that proposal belongs to `/debrief dream` (≥2 distinct dailies required) or day mode's INDEX step, both human-reviewed.
- `/debrief dream` is the batch path: it matches new dailies against the watchlist, proposes new entries, receipts, contested pairs, promotions, and refutations. This command is the immediate path. Both end at the same human gate.
