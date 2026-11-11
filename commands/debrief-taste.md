---
description: Show the debrief taste layer — how the human has shaped work, with their exact words and the pieces themselves — or record a Shaping on the human's word
argument-hint: "[<topic>] | [add <what the human said about a piece>]"
---

# /debrief-taste — what the human did with the work

Arguments: `$ARGUMENTS`

**Store resolution (check first):** the store is `debrief/` in the current project root when `debrief/INDEX.md` exists there. Otherwise it is the directory in `$DEBRIEF_DIR`. If neither exists, say so ("no debrief store here") and stop. In shell, resolve it once:

```bash
D=debrief; [ -f debrief/INDEX.md ] || D="${DEBRIEF_DIR:?no debrief store here}"
```

`debrief/TASTE.md` is the taste layer. A **Shaping** is one moment the human judged or changed a piece of work: their exact words, the scope, the reason, and a path to the piece. A **Reading** is a guess about what they look for, with the Shapings that support it. Read the rules at the top of `debrief/TASTE.md` before acting.

## Mode 1: no arguments, or a topic → show

```bash
python3 "$D/.system/debrief-search.py" --dir "$D" taste              # Readings, then the newest Shapings
python3 "$D/.system/debrief-search.py" --dir "$D" taste $ARGUMENTS   # whole entries on a topic
```

1. Present what it prints. Entries are whole on purpose: do not shorten a Shaping, because its Scope and Reason lines are what stop it being misused.
2. When the question is about a piece ("what did I say about the empty state"), open the piece. Each Shaping that has kept originals ends with `source <session id>`. Run it, then Read the before and the after:

   ```bash
   python3 "$D/.system/debrief-search.py" --dir "$D" source <session id>
   ```

3. This mode is read-only.

## Mode 2: `add <what the human said about a piece>` → record a Shaping

The human is the author here, so the gate is already satisfied.

1. If `debrief/TASTE.md` does not exist, create it in the standard shape (see `debrief/docs/taste-layer.md`): title, the rules, `## Readings`, `## Shapings`, `## Recall`, and a `last reading pass: never` footer.
2. Add an entry at the top of `## Shapings`: `### <today> · <project> · <piece>`, then the Before / The human / After / Kind / Scope / Reason lines. **The human** line is their words exactly as typed. Scope is "this piece" unless their words made it wider. Reason is quoted from them or "not stated". Ask for the piece or the project if you cannot tell which one they mean. Do not ask for a reason.
3. If this session has originals at `debrief/sessions/material/<first 8 characters of the session id>/`, give the Before and After lines their store paths.
4. Close with `— recorded <today> · session \`<sid8>\`` when the session id is known.
5. Confirm back with the entry's heading and the quoted words.

## Using the layer when you make something

- Before you design, write or name something for the human, run Mode 1 on the topic and open the pieces the Shapings point at. Say which ones you worked from.
- A Shaping covers its piece. Do not apply it to another piece as a rule.
- A Reading is where you start, never a reason to withhold. When you expect the human to dislike a direction, show the work and name the Reading.
- **Never record a Shaping, and never write or change a Reading, on your own initiative.** The day pass records Shapings. The dream pass writes Readings.
