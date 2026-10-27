# claude-debrief

Automatic project memory for Claude Code sessions, plus a
`session → repo → file → function` graph of what each session actually touched,
derived from git.

It runs with nobody at the wheel. A `SessionEnd` hook queues each session and
spawns a detached headless `claude -p` that drafts a note on what happened. A
second headless run folds that draft into the day's debrief, updates the index
and the other layers, and archives the draft. A launchd daemon catches the
sessions the hook missed: a closed lid, a killed terminal, a machine powered
off straight after work. Nothing waits on a human, and a human can still read,
correct or curate by hand at any time.

Four pieces, all dependency-light (bash + python3 stdlib + node):

- **Capture** — SessionEnd hook, durable queue receipt, detached drafter,
  self-repair sweep for drafters that die, and a catch-up daemon for sessions
  that never fired the hook
- **Curation** — a headless `/debrief day` pass after every draft folds it into
  the daily, `INDEX.md` and the layers below; the same command run by hand,
  with a review step, is the manual path
- **Retrieval** — `debrief-search.py`, sqlite FTS5 over *curated content only*,
  search → get progressive disclosure, plus optional meaning-based ranking with
  EmbeddingGemma 2 through a local Ollama ([search](docs/search.md#semantic-layer-embeddinggemma-2))
- **Provenance graph** — git-derived `session → repo → file → function`
  drill-down, rendered by a small Vite/React viewer

![The viewer's graph tab: session → repo → file → function drill-down, with the debrief open alongside](docs/assets/viewer-graph.jpg)

---

## The problem this solves

Two failure modes, pulling opposite directions:

- **Sessions that end without a debrief are lost.** The reasoning — why the
  obvious fix didn't work, what you ruled out — evaporates. Git keeps the
  outcome, never the argument.
- **Auto-capture records wrong conclusions as confidently as right ones.** A
  transcript summarizer can't tell "we verified this" from "we said this and
  stopped". Memory that lies is worse than no memory, because you *trust* it.

**Resolution: automate both ends, and keep the draft apart from the record.**
Capture is automatic so nothing is lost. Curation is automatic too, so nothing
piles up waiting for a person who never gets back to it. What stops a wrong
conclusion from becoming a confident fact is the curator's rules, not a review
step:

- a claim keeps the register its draft supports, and whatever the draft calls
  unverified stays marked unverified in the daily;
- episodic files are never rewritten, so a correction is a new entry rather
  than a quiet edit;
- receipts quote the daily verbatim and must string-match it;
- retrieval indexes the curated record only, never raw drafts or transcripts.

> If you take one idea from this repo, take that. The hook and the viewer are
> implementation; the line between draft and record is the point.

## Why not CLAUDE.md, mem0, or Basic Memory?

Those tools store memory; none of them separate a draft from the record.

- **CLAUDE.md and auto-memory files** hold standing instructions, loaded into
  every session whole. There is no episodic record, no provenance, and no way
  to tell a verified conclusion from a confident guess that got written down.
- **Automatic memory layers** (mem0-style) extract "facts" from conversations
  and index them immediately — exactly the failure mode above: wrong
  conclusions recorded as confidently as right ones, then served back with
  authority.
- **Notes-first tools** (Basic Memory–style) get the human-written part right
  but capture nothing on their own; a session that ends without a note is
  simply gone.

claude-debrief is the combination: capture and curation are automatic so
nothing is lost or left in a pile, drafts stay apart from the record so a guess
is never filed as a finding, and retrieval indexes only the curated record. If
you already use one of the tools above, that separation is the part you're
missing, not the storage.

## Five layers

| Layer | What | Mutability |
|---|---|---|
| **Episodic** | Daily debriefs (`YYYY-MM-DD-<slug>.md`) and per-session notes (`sessions/`) | Immutable once written |
| **Semantic** | `INDEX.md` — table + `[[entity]]` glossary + principles | Pruned and **rewired** toward current understanding |
| **Starred** | `HIGHLIGHTS.md` — `★` must-know-forever entries | Append-only; **never pruned** |
| **Todo** | `TODO.md` — open threads a day pass chose to track until resolved | Items open and close; closed items pruned once their closing daily records the outcome |
| **Observations** | `OBSERVATIONS.md` — cross-session patterns under watch, each with receipts | Entries accumulate receipts; leave on promotion or refutation |

Episodic files say what was believed at the time, and stay wrong on purpose.
Corrections happen in the semantic layer, by rewiring — never by editing history.

The starred layer exists because `INDEX.md` answers "what is true *now*" — an
entry that stops being load-bearing gets rewired away, which is the wrong
behavior for a lesson you paid for once and must never relearn. `HIGHLIGHTS.md`
holds rare, append-only `★` entries with no recurrence bar: the 3am root cause
qualifies on first occurrence. The automatic curator stars sparingly, only
lessons that would cost real time or money to relearn. A human stars with
`/debrief highlight`. An agent working inside a session may only propose
`★ (candidate)`. Rationale and format:
[docs/starred-layer.md](docs/starred-layer.md).

The todo layer exists because episodic files are immutable and read by date:
every draft and daily records **Open threads**, but a thread opened Tuesday is
invisible by Friday unless someone rereads Tuesday. `TODO.md` holds the ones a
day pass chose to track, visible until resolved. The automatic curator admits
and closes items; `/debrief-todos add`/`done` is the human-authored path. Admit only
what would otherwise be lost — if the list outgrows a screen, the bar was too
low. Rationale: [docs/todo-layer.md](docs/todo-layer.md).

The observations layer (the *dream layer*) exists because recurrence is
currently counted in the human's head: every draft ends with candidate
principle observations, and they die with the archived draft — a pattern seen
once in January is invisible in March. `OBSERVATIONS.md` holds patterns under
watch, each entry carrying **receipts** (one `— seen in` line per sighting,
citing a curated daily with a verbatim quote) and a status (`watching` /
`contested` / promoted / refuted). Evidence is counted in distinct dailies; at
≥2, `/debrief dream` — the manual cross-session pass — may propose promotion
to an INDEX principle, receipts attached. Disagreements between dailies are
preserved as `contested` pairs, never resolved last-write-wins. The automatic
curator records sightings and new entries after each session. Promotion to an
INDEX principle stays with the manual `/debrief dream` pass, where a person
approves item by item; `/debrief-dreams add` is the human-authored path. Rationale:
[docs/dream-layer.md](docs/dream-layer.md).

## Lifecycle

```
session ends ──SessionEnd hook──▶ .system/session-debrief.sh
                                    ├─ appends sessions/queue.jsonl   (durable receipt)
                                    └─ spawns headless `claude -p`    (detached)
                                         └─▶ sessions/<date>/<HHMM>-<sid>.md   status: machine-draft
                                              └─ .system/curate-day.sh   (headless /debrief day, no review)
                                                   ├─▶ <date>-<slug>.md, or a late-day addendum to it
                                                   ├─▶ INDEX.md, HIGHLIGHTS.md, TODO.md, OBSERVATIONS.md
                                                   └─▶ archives the draft, marks the receipt aggregated

launchd daemon (login, wake, every 15 min) ──▶ .system/debrief-daemon.sh
                                                ├─ clears orphaned locks, re-runs dead drafters
                                                ├─ queues idle transcripts the hook never saw
                                                └─ curates any day whose drafts nobody consumed

by hand, any time, all optional:
/debrief                     (in-session)    ─▶ sessions/<date>/<HHMM>-<slug>.md   status: curated
/debrief day                 (manual mode)   ─▶ the same day pass, with a review step
/debrief highlight <thing>                   ─▶ appends ★ entry to HIGHLIGHTS.md   (never pruned)
/debrief-todos [add|done]                    ─▶ shows / updates TODO.md
/debrief dream               (cross-session) ─▶ proposes sightings, contested pairs, ≥2-daily
                                                promotions ─▶ human approves ─▶ OBSERVATIONS.md
/debrief-dreams [add]                        ─▶ shows / appends to OBSERVATIONS.md
```

The queue receipt is written **before** the drafter runs, so a drafter that dies
still leaves a record, and the day pass can fall back to the raw transcript.

The backlog drains itself. Each draft is curated within minutes of landing, and
the daemon sweeps up whatever a shutdown interrupted. `/debrief-backlog` shows
anything still waiting, which should only ever be a failed run.

## Install

The memory lives in one store outside every repo, so sessions from every
project land in the same place. The commands and the search script find it
through `$DEBRIEF_DIR`.

```bash
git clone https://github.com/gurul/claude-debrief.git
mkdir -p ~/claude-memory
cp -R claude-debrief ~/claude-memory/debrief          # the store, not a checkout
cp claude-debrief/commands/*.md ~/.claude/commands/    # /debrief + /debrief-search + /debrief-backlog + /debrief-highlights + /debrief-todos + /debrief-dreams
```

Keep `INDEX.md`, `TODO.md` and `OBSERVATIONS.md`, and delete the two example
dailies plus `provenance.json` once real notes arrive.

**1. Wire the hook and the store** in `~/.claude/settings.json`, so every
session and every subagent sees them:

```jsonc
{
  "env": { "DEBRIEF_DIR": "/Users/<you>/claude-memory/debrief" },
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "DEBRIEF_AUTO_CURATE=1 $HOME/claude-memory/debrief/.system/session-debrief.sh",
            "async": true
          }
        ]
      }
    ]
  }
}
```

`DEBRIEF_AUTO_CURATE=1` is what makes curation automatic; the scripts leave it
off unless you set it. `async: true` matters, because a drafter should never
delay session exit.

**2. Install the daemon** (macOS):

```bash
D=~/claude-memory/debrief
sed -e "s#@@DEBRIEF_DIR@@#$D#g" -e "s#@@HOME@@#$HOME#g" -e "s#@@AUTO_CURATE@@#1#" \
    -e "s#@@RAW_ARCHIVE@@#1#" -e "s#@@RETAIN_DAYS@@#0#" -e "s#@@PROJECT_MATCH@@##" \
    $D/deploy/co.claude-debrief.daemon.plist.template > ~/Library/LaunchAgents/co.claude-debrief.daemon.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/co.claude-debrief.daemon.plist
```

**3. Scope it** (optional). To capture only some projects, set
`DEBRIEF_PROJECT_MATCH` to a shell glob on the `~/.claude/projects/<dir>` name,
both in the hook command and in place of `@@PROJECT_MATCH@@`. For example,
`-Users-me-Documents-personal*` keeps the sessions under `~/Documents/personal`.
Empty means every project.

**4. Keep transcripts** (optional). Add `DEBRIEF_RAW_ARCHIVE=1` to the hook
command to keep redacted, gzipped transcripts past Claude Code's ~30-day
deletion; `DEBRIEF_RAW_RETAIN_DAYS=0` keeps them forever. When a resumed
session archives again, the hook drops an older copy only if the new copy
contains every byte of it. Read
[docs/verbatim-archive.md](docs/verbatim-archive.md) before turning it on.

**5. Configure the graph** (optional):

```bash
cp ~/claude-memory/debrief/.system/repos.example.json ~/claude-memory/debrief/.system/repos.json  # then edit
```

Recall is by instruction, not by hook. Subagents (`~/.claude/agents/*.md`) get
a short "memory first" section that runs `debrief-search.py --dir "$DEBRIEF_DIR"
search` before any repo or web sweep.

## How the automatic pieces work

**The curator** (`.system/curate-day.sh`): after a draft lands, it runs
`/debrief day` headless for that session's date with the review step waived
(`.system/prompts/auto-day.md`). It writes the daily, the INDEX row and
glossary, ★ entries, todo admissions and closures, and observation receipts.
It keeps draft claims marked unverified, appends late-day addenda rather than
rewriting, and never writes outside the store or commits. One curator runs at
a time. Its lock holds a PID, so a lock left by a machine that powered off is
taken over.

**The daemon** (`.system/debrief-daemon.sh`, a launchd agent): the
`SessionEnd` hook never fires when you close the lid, kill the terminal or
power off straight after working. The daemon runs at login, on wake and
every 15 min, and repairs whatever was left unfinished:

1. clears locks orphaned by a shutdown;
2. runs the hook's repair sweep (drafters that never wrote);
3. hands every transcript in `~/.claude/projects/*`, idle for 30 min and
   never queued, to the `SessionEnd` pipeline. This is also how a backlog of
   past sessions is worked off, 3 per run, each filed under the day it
   happened (session stamps come from the transcript, not the clock);
4. curates any day whose drafts no curator consumed.

The daemon's own headless sessions (drafter, curator) are recognised by their
opening prompt and skipped.

The plist sets `AbandonProcessGroup`: without it launchd kills the
background drafters and curators the moment the daemon script exits. Logs:
`.system/daemon.log` (what the daemon queued) and `.system/hook.log` (drafters
and curators).
Each log rotates to `<name>.1` once it passes 1 MB, so one previous file is kept.

## Manual mode: a reviewed memory inside one repo

If you would rather approve every entry, leave `DEBRIEF_AUTO_CURATE` unset and
skip the daemon. Drafts then wait in `sessions/` until you run `/debrief day`,
which reviews each change with you before it writes.

Manual mode usually lives inside a project, as `debrief/`:

```bash
mkdir -p /path/to/your-repo/debrief
cp -R claude-debrief/{.system,viewer,INDEX.md,HIGHLIGHTS.md,TODO.md,OBSERVATIONS.md,README.md} /path/to/your-repo/debrief/
```

The layout is load-bearing: the machinery resolves paths relative to `debrief/`
(`..` is your repo root), so it must live at `<your-repo>/debrief/`. Add
`debrief/` to the repo's `.gitignore`, because memory is personal and should
never land in a PR.

Wire both hooks in `.claude/settings.local.json` (project-local, not a shared
`settings.json`). The SessionStart nudge closes the loop: on a fresh start it
surfaces any uncurated backlog, so the pile can't grow unseen.

```jsonc
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/debrief/.system/session-debrief.sh",
            "async": true
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/debrief/.system/backlog-nudge.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

`matcher: "startup"` restricts the nudge to fresh launches, not resume, compact
or clear. It is **not** `async`, because an async `SessionStart` hook cannot
contribute context. It nudges at most once per day and stays silent on a clean
queue. Surfacing is not curation: neither the nudge nor `/debrief-backlog`
ever writes a daily.

Resolution order in every `/debrief*` command: `debrief/` in the project root
when `debrief/INDEX.md` exists there, otherwise `$DEBRIEF_DIR`, otherwise stop.
So a repo with its own store keeps it, even with a shared store installed. The
commands pass `--dir` explicitly, so the script never has to guess.
`build-provenance.mjs` reads `$DEBRIEF_DIR` directly; pass its own `--dir` when
a repo-local store should win.

## Viewer

```bash
cd debrief/viewer
npm install
npm run dev        # http://localhost:5199
```

Vite + React. The graph tab is a vertically stacked drill-down tree —
session → repo → file → function, newest first, spread open on first load —
not a force simulation: dates read top-to-bottom and every level is a block
you click into. Markdown is pulled in via `import.meta.glob` at dev time, so
editing a note hot-reloads the graph. A fresh clone renders the shipped example
fixture; point `repos.json` at real repos and it renders yours. A **verbatim**
tab reads the cold-storage archive on explicit click, without ever indexing it.

## Going deeper

The design write-ups live in [`docs/`](docs/):

- **[Provenance graph](docs/provenance.md)** — declare commits in debrief
  frontmatter (`commits:` / `touched:`, SHAs and `a..b` ranges); the builder
  verifies every SHA against git before it becomes a node.
- **[Search](docs/search.md)** — two-step `search` → `get` retrieval, the
  curation-aware ranking, and why `★` hits are a sort key rather than a weight.
- **[Verbatim archive](docs/verbatim-archive.md)** — why summaries aren't
  enough for sessions you didn't watch, the fail-closed redactor, retention,
  and why the archive is never indexed.
- **[Reliability](docs/reliability.md)** — the self-repair sweep, the
  `pending → unconsumed → aggregated / failed` queue lifecycle, and backlog
  surfacing.
- **[Starred layer](docs/starred-layer.md)** — the append-only `★` contract.
- **[Todo layer](docs/todo-layer.md)** — open threads tracked until resolved,
  who may admit and close items, and why search doesn't index the checklist.
- **[Dream layer](docs/dream-layer.md)** — cross-session patterns with
  receipts, the `watching`/`contested` statuses, why disagreements are
  preserved rather than resolved, and the inceptions future-work sketch.

## Rules

- **Machine drafts never touch `INDEX.md` or daily files.** Only a day pass
  writes the semantic layer: the automatic curator, or `/debrief day` by hand.
- Drafts carry `status: machine-draft` and an unverified-claims banner. The day
  pass **keeps** those claims flagged. It does not launder them into fact.
- Episodic files are immutable; corrections happen by rewiring INDEX. A daily
  that already exists gets a late-day addendum, never a rewrite.
- **The curator is the only automatic writer.** An agent working inside a
  session never stars, files or closes a todo, or admits, promotes or refutes
  an observation on its own initiative; it may propose `★ (candidate)`. An
  agent filing its own todos manufactures work, one closing them declares work
  done that nobody verified, and one promoting its own observation makes a law
  from an anecdote.
- `HIGHLIGHTS.md` is append-only: a wrong highlight is superseded by a new
  entry naming it, never edited.
- Observation receipts cite curated dailies only, with quotes that
  string-match the source; nothing seen in only one daily is proposed for
  promotion.
- The curator never writes outside the store and never commits.
- Multiple drafts for one `session_id`: newest wins; `curated` beats
  `machine-draft`.
- `sessions/raw/` is cold storage — **never indexed, never auto-read**, redacted
  before it lands (fail-closed), pruned on a retention horizon. Indexing it
  would collapse the curation gate.
- Memory is gitignored. Never commit it.

## Cost / tuning

Each substantive session exit spawns two headless `claude -p` runs at the
default model: the drafter and the curator. To cheapen them, add
`--model haiku` (or `sonnet`) to the `claude -p` invocations in
`.system/session-debrief.sh` and `.system/curate-day.sh`. To change what counts as
"substantive", adjust the 30-line transcript guard there.
The drafter reads only the first line of each glossary entry in `INDEX.md`,
not the whole file, so its cost stays flat as the index grows.

## Layout

```
debrief/
  INDEX.md                  # semantic layer (template — edit for your project)
  HIGHLIGHTS.md             # starred layer — ★ must-know-forever (append-only, never pruned)
  TODO.md                   # todo layer — open threads tracked until resolved
  OBSERVATIONS.md           # dream layer — patterns under watch, with receipts
  README.md                 # this file
  YYYY-MM-DD-<slug>.md      # daily debriefs (episodic)
  provenance.json           # generated; example fixture ships with the repo
  sessions/
    queue.jsonl             # one receipt per substantive session
    <date>/                 # session drafts awaiting curation
    archive/<date>/         # drafts consumed by a daily
    raw/<date>/             # opt-in REDACTED gzipped transcripts (cold storage, never indexed, pruned)
  .system/
    session-debrief.sh      # the SessionEnd hook (queue + drafter + self-repair sweep)
    curate-day.sh           # the automatic curator (headless /debrief day, no review)
    debrief-daemon.sh       # the launchd catch-up daemon
    backlog-nudge.sh        # the SessionStart hook (surfaces the backlog, once/day)
    debrief-backlog.py      # read-only backlog report (full + --nudge); source of truth
    backlog-selftest.sh     # integration selftest: sweep heal + nudge + gate invariants
    archive-selftest.sh     # integration selftest: redaction + fail-closed + retention
    build-provenance.mjs    # git → provenance.json
    debrief-search.py       # FTS5 + EmbeddingGemma 2 retrieval over curated memory (+ stars / --starred)
    redact-transcript.py    # scrubs secrets out of raw archives (fail-closed); --selftest
    repos.example.json      # copy to repos.json
    prompts/session-draft.md
    prompts/auto-day.md     # the curator's standing overrides
  viewer/                   # Vite + React memory viewer
deploy/
  co.claude-debrief.daemon.plist.template   # launchd agent for the daemon
docs/                       # design write-ups (provenance, search, archive, reliability, ★)
commands/
  debrief.md                # install to ~/.claude/commands/
  debrief-backlog.md        # install alongside — /debrief-backlog (what needs curating)
  debrief-search.md         # install alongside — /debrief-search <query>
  debrief-highlights.md     # install alongside — /debrief-highlights [query] (the ★ layer)
  debrief-todos.md          # install alongside — /debrief-todos [add|done] (the todo layer)
  debrief-dreams.md         # install alongside — /debrief-dreams [add] (the dream layer)
```

## Known edges

- **macOS/Linux only.** The hook is bash and shells out to `python3` for JSON.
- **The transcript format is internal and unstable.** The drafter reads it as
  *text*, never parses it. Don't be tempted.
- **Automatic curation trusts the curator.** A wrong call by the headless day
  pass lands in the daily with nobody checking it. The register rules and the
  unverified flags limit the damage, and the daily records what was believed
  at the time, so a later correction rewires INDEX rather than erasing it. If
  you want every entry approved, run manual mode.

## License

[MIT](LICENSE)
