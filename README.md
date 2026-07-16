# claude-debrief

Auto-drafted, human-curated project memory for Claude Code sessions — plus a
`session → repo → file → function` graph of what each session actually touched,
derived from git.

A `SessionEnd` hook fires when a Claude Code session exits, queues the session,
and spawns a detached headless `claude -p` that drafts a note on what happened.
Drafts are **staging material**: nothing reaches durable memory without a human
pass.

---

## The problem this solves

Two failure modes, pulling opposite directions:

- **Sessions that end without a debrief are lost.** The reasoning — why the
  obvious fix didn't work, what you ruled out — evaporates. Git keeps the
  outcome, never the argument.
- **Auto-capture records wrong conclusions as confidently as right ones.** A
  transcript summarizer can't tell "we verified this" from "we said this and
  stopped". Memory that lies is worse than no memory, because you *trust* it.

**Resolution: auto-draft at session end, curate at day level.** Capture is
automatic so nothing is lost. Promotion is manual so nothing is laundered. The
gate between them is the entire design.

> If you take one idea from this repo, take that. The hook and the viewer are
> implementation; the gate is the point.

## Two layers

| Layer | What | Mutability |
|---|---|---|
| **Episodic** | Daily debriefs (`YYYY-MM-DD-<slug>.md`) and per-session notes (`sessions/`) | Immutable once written |
| **Semantic** | `INDEX.md` — table + `[[entity]]` glossary + principles | Pruned and **rewired** toward current understanding |

Episodic files say what was believed at the time, and stay wrong on purpose.
Corrections happen in the semantic layer, by rewiring — never by editing history.

## Lifecycle

```
session ends ──SessionEnd hook──▶ .system/session-debrief.sh
                                    ├─ appends sessions/queue.jsonl   (durable receipt)
                                    └─ spawns headless `claude -p`    (detached)
                                         └─▶ sessions/<date>/<HHMM>-<sid>.md   status: machine-draft

/debrief       (optional, in-session) ─▶ sessions/<date>/<HHMM>-<slug>.md      status: curated
/debrief day   (end of day, manual)   ─▶ synthesize <date>-<slug>.md  ─▶ review with the human
                                       ─▶ update INDEX.md  ─▶ archive drafts to sessions/archive/<date>/
```

The queue receipt is written **before** the drafter runs, so a drafter that dies
still leaves a record — and day mode can fall back to the raw transcript.

## Install

Copy this repo's contents into your project as `debrief/`:

```bash
git clone git@github.com:Era-Laboratories/claude-debrief.git
mkdir -p /path/to/your-repo/debrief
cp -R claude-debrief/{.system,viewer,INDEX.md,README.md} /path/to/your-repo/debrief/
cp claude-debrief/commands/debrief.md ~/.claude/commands/    # user-level slash command
```

The layout is load-bearing: the machinery resolves paths relative to `debrief/`
(`..` is your repo root), so it must live at `<your-repo>/debrief/`.

**1. Gitignore your memory.** In your repo's `.gitignore`:

```
debrief/
```

Memory is personal and unreviewed. It should never land in a PR.

**2. Wire the hook** in `.claude/settings.local.json` (project-local, not a
shared `settings.json`):

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
    ]
  }
}
```

`async: true` matters — `SessionEnd` is non-blocking, and you don't want a
drafter delaying session exit.

**3. Seed the memory.** Keep `INDEX.md` (edit the template for your project) and
delete the two example dailies plus `provenance.json` once you have real notes.

**4. Configure the graph** (optional):

```bash
cp debrief/.system/repos.example.json debrief/.system/repos.json  # then edit
```

## Configuration

`.system/repos.json` — which working copies this memory spans. Paths resolve
relative to `debrief/`, so `..` is the host repo and `../../peer` a sibling
checkout:

```json
{
  "repos": {
    "my-app": "..",
    "my-api": "../../my-api"
  }
}
```

Repos listed but missing on disk are skipped, so one config can cover a team
whose members have different subsets checked out. **No `repos.json` → the
builder no-ops** and leaves any existing `provenance.json` alone.

## Code-provenance layer (the graph)

The viewer's graph tab is a `session → repo → file → function` drill-down of the
*actual files and functions* each debrief touched — not just concept
`[[wikilinks]]`. `.system/build-provenance.mjs` derives it straight from git
(`numstat` for files, hunk-header context for enclosing symbols) across every
repo in `repos.json`, and writes `provenance.json`.

**To appear in the graph, a debrief must declare its commits in frontmatter.**
Bare SHAs (repo auto-resolved by searching each configured repo):

```yaml
---
commits: 38de2b6 8a1f902 ba606d6
---
```

or the explicit form, when you want to name repos and PRs:

```yaml
---
touched:
  my-api: 9c81346 9bdcdc9
prs:
  my-api: 487 488
---
```

Two layers, deliberately: the **auto** layer is whatever git reports; the
**curated** layer is the SHAs a human decided count as this session's work. A
debrief with no `commits:`/`touched:` simply won't appear — orientation days with
no commits are legitimately absent.

Rebuild on demand, or let the viewer's `predev`/`prebuild` hook do it:

```bash
node debrief/.system/build-provenance.mjs
```

## Viewer

```bash
cd debrief/viewer
npm install
npm run dev        # http://localhost:5199
```

Vite + React + `react-force-graph-3d`. Markdown is pulled in via
`import.meta.glob` at dev time, so editing a note hot-reloads the graph. A fresh
clone renders the shipped example fixture; point `repos.json` at real repos and
it renders yours.

## Rules

- **Machine drafts never touch `INDEX.md` or daily files.** The semantic layer is
  human-gated; `/debrief day` is the only writer, and it reviews first.
- Drafts carry `status: machine-draft` and an unverified-claims banner. Day mode
  **flags** those claims — it does not launder them into fact.
- Episodic files are immutable; corrections happen by rewiring INDEX.
- Multiple drafts for one `session_id`: newest wins; `curated` beats
  `machine-draft`.
- Memory is gitignored. Never commit it.

## Reliability

Drafters die — observed in the wild as "API Error: Connection closed
mid-response" and a logged-out CLI. The queue receipt outlives the drafter, so
every session end runs a **self-repair sweep**: pending entries older than 15
minutes whose draft never landed get respawned (max 3 attempts, then
`status: failed`). A lock dir serializes concurrent session ends. The hook always
exits 0 and logs to `.system/hook.log` — check there first when a draft doesn't
appear.

Guards: recursion (the drafter is itself a Claude session whose exit fires this
hook — `DEBRIEF_GENERATION=1` breaks the loop), missing transcript, and trivial
sessions (<30 transcript lines).

The sweep treats an **archived** draft as proof of aggregation rather than a
dead drafter. Day mode *moves* the drafts it consumes into
`sessions/archive/<date>/`, which empties the original path — so without that
check an aggregated session is indistinguishable from a failed one, and the
sweep re-drafts already-curated work (one headless run per session end, three
times, then marks it `failed` while its draft sits in the archive). It heals
the status instead.

## Cost / tuning

Each substantive session exit spawns one headless `claude -p` run at the default
model. To cheapen it, add `--model haiku` (or `sonnet`) to the `claude -p`
invocation in `.system/session-debrief.sh`. To change what counts as
"substantive", adjust the 30-line transcript guard there.

## Layout

```
debrief/
  INDEX.md                  # semantic layer (template — edit for your project)
  README.md                 # this file
  YYYY-MM-DD-<slug>.md      # daily debriefs (episodic)
  provenance.json           # generated; example fixture ships with the repo
  sessions/
    queue.jsonl             # one receipt per substantive session
    <date>/                 # session drafts awaiting curation
    archive/<date>/         # drafts consumed by a daily
  .system/
    session-debrief.sh      # the SessionEnd hook
    build-provenance.mjs    # git → provenance.json
    repos.example.json      # copy to repos.json
    prompts/session-draft.md
  viewer/                   # Vite + React memory viewer
commands/
  debrief.md                # install to ~/.claude/commands/
```

## Known edges

- **macOS/Linux only.** The hook is bash and shells out to `python3` for JSON.
- **The transcript format is internal and unstable.** The drafter reads it as
  *text*, never parses it. Don't be tempted.
- **Curation doesn't scale by itself.** `/debrief day` is a real human pass. If
  you skip it for a week, you have a pile of drafts, not memory — which is the
  honest failure mode, and better than a confident wrong index.
