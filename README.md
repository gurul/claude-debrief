# claude-debrief

Auto-drafted, human-curated project memory for Claude Code sessions — plus a
`session → repo → file → function` graph of what each session actually touched,
derived from git.

A `SessionEnd` hook fires when a Claude Code session exits, queues the session,
and spawns a detached headless `claude -p` that drafts a note on what happened.
Drafts are **staging material**: nothing reaches durable memory without a human
pass.

Four pieces, all dependency-light (bash + python3 stdlib + node):

- **Capture** — SessionEnd hook, durable queue receipt, detached drafter,
  self-repair sweep for drafters that die
- **Curation gate** — `/debrief` and `/debrief day` slash commands; machine
  drafts become durable memory only through a reviewed daily
- **Retrieval** — `debrief-search.py`, sqlite FTS5 over *curated content only*,
  search → get progressive disclosure
- **Provenance graph** — git-derived `session → repo → file → function`
  drill-down, rendered by a small Vite/React viewer

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

## Three layers

| Layer | What | Mutability |
|---|---|---|
| **Episodic** | Daily debriefs (`YYYY-MM-DD-<slug>.md`) and per-session notes (`sessions/`) | Immutable once written |
| **Semantic** | `INDEX.md` — table + `[[entity]]` glossary + principles | Pruned and **rewired** toward current understanding |
| **Starred** | `HIGHLIGHTS.md` — `★` must-know-forever entries | Append-only; **never pruned** |

Episodic files say what was believed at the time, and stay wrong on purpose.
Corrections happen in the semantic layer, by rewiring — never by editing history.

### The starred layer

`INDEX.md` is deliberately lossy: it answers "what is true *now*", so an entry
that stops being load-bearing gets rewired away. That is the right behavior for
current understanding and the wrong behavior for a lesson you paid for once and
must never relearn. Hence a third layer with the opposite promise.

A highlight is a margin star in a book: rare, in-situ, and permanent. Two rules
carry the whole design:

1. **A human stars it.** Hooks and drafters may *propose* (`★ (candidate)` in a
   session note); only `/debrief highlight`, or a human approving a candidate
   during a day pass, promotes. An agent starring its own conclusions is the
   laundering the curation gate exists to prevent.
2. **No recurrence bar** — and that is the point. `INDEX.md` principles need ≥2
   debriefs so the list stays short. Highlights are the escape hatch for the
   one-shot lesson: the 3am root cause, the footgun that cost a day, the
   constraint invisible in the code.

Keep it short by raising the bar for *adding*, never by deleting. A wrong
highlight is corrected by appending a superseding entry that names the one it
replaces — the original stays, because what you believed and why is the record.

Format is one `### ★ <one-line claim>` per entry under `## The starred list`,
newest first, each ending `— from [<source>](<link>) · starred <date>`. Write
entries to survive without their context: name the system, the mechanism and the
consequence, and assume the reader has forgotten the incident entirely.

Recall: `/debrief-highlights` (whole list, or `--starred` search), and plain
`/debrief-search` ranks `★` hits above every other layer.

## Lifecycle

```
session ends ──SessionEnd hook──▶ .system/session-debrief.sh
                                    ├─ appends sessions/queue.jsonl   (durable receipt)
                                    └─ spawns headless `claude -p`    (detached)
                                         └─▶ sessions/<date>/<HHMM>-<sid>.md   status: machine-draft

/debrief       (optional, in-session) ─▶ sessions/<date>/<HHMM>-<slug>.md      status: curated
/debrief day   (end of day, manual)   ─▶ synthesize <date>-<slug>.md  ─▶ review with the human
                                       ─▶ update INDEX.md  ─▶ offer ★ candidates  ─▶ archive drafts

/debrief highlight <thing>  (human, any time) ─▶ appends ★ entry to HIGHLIGHTS.md   (never pruned)

session starts ──SessionStart hook──▶ .system/backlog-nudge.sh
                                        └─ once/day, if the queue is non-empty: nudges the human to
                                           run /debrief day — read-only, never drains the backlog itself
```

The queue receipt is written **before** the drafter runs, so a drafter that dies
still leaves a record — and day mode can fall back to the raw transcript.

Capture is automatic and draining is manual, so a backlog of uncurated drafts
is the expected steady state, not a bug. The SessionStart nudge and the
`/debrief-backlog` command exist to keep that backlog **in front of the human**
(the SessionEnd sweep only ever logged it to `hook.log`, which nobody opens).
Neither ever writes a daily — surfacing is not curation.

## Install

Copy this repo's contents into your project as `debrief/`:

```bash
git clone https://github.com/gurul/claude-debrief.git
mkdir -p /path/to/your-repo/debrief
cp -R claude-debrief/{.system,viewer,INDEX.md,HIGHLIGHTS.md,README.md} /path/to/your-repo/debrief/
cp claude-debrief/commands/*.md ~/.claude/commands/    # /debrief + /debrief-search + /debrief-backlog + /debrief-highlights
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

**3. Wire the backlog nudge** in the same `.claude/settings.local.json`. This
is what closes the loop: on a fresh session start it surfaces any uncurated
backlog into the conversation, so the pile can't grow unseen the way it did
before:

```jsonc
{
  "hooks": {
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

`matcher: "startup"` restricts it to fresh launches (not resume/compact/clear);
the script re-checks `source` as defence-in-depth. **Not** `async` — an async
`SessionStart` hook cannot contribute context, which is the opposite of the
SessionEnd drafter. `SessionStart` hook arrays compose across every settings
file, so this runs alongside any other start hook you already have. It nudges at
most once per day and stays silent on a clean queue.

**4. Seed the memory.** Keep `INDEX.md` (edit the template for your project) and
delete the two example dailies plus `provenance.json` once you have real notes.

**5. Configure the graph** (optional):

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

Both blocks accept the same grammar: bare SHAs, `a..b` / `a...b` ranges, and a
`(repo)` annotation naming the repo for the entry it follows. Commas are
separators. Ranges use **git semantics — `a..b` excludes `a`**; write `a^..b`
to include it.

```yaml
---
commits: 09eca51..b90fce7 (era-maker), 07620fb..0ae57bb (era-device-api), f317a7c
---
```

Two layers, deliberately: the **auto** layer is whatever git reports; the
**curated** layer is the SHAs a human decided count as this session's work. A
debrief with no `commits:`/`touched:` simply won't appear — orientation days with
no commits are legitimately absent.

**Frontmatter is hand-written, so the builder never trusts it.** Every SHA is
confirmed with `git cat-file` before any plumbing runs; anything that isn't a
SHA, a range or an annotation is reported and skipped. A placeholder like
`temperature-strip fix (hash not captured in draft)` costs you one warning and
one absent node — it does not abort the build. Warnings name the document and
the field, so an unresolved SHA points at the repo you forgot to configure:

```
2026-07-15-fluid-canvas.md [touched.era-firmware-rs]: unresolved commit 5d6fde7 (declared era-firmware-rs)
```

Rebuild on demand, or let the viewer's `predev`/`prebuild` hook do it:

```bash
node debrief/.system/build-provenance.mjs
```

Set `DEBRIEF_DIR` when the script runs from outside the memory it's building —
a symlinked or vendored copy, or a monorepo task runner. Node resolves symlinks
when computing a module's own path, so without it a symlinked builder reads the
*clone's* `repos.json` and writes the *clone's* `provenance.json`:

```bash
DEBRIEF_DIR=/path/to/project/debrief node .system/build-provenance.mjs
```

## Search (the retrieval layer)

`INDEX.md` is orientation; search is recall. `.system/debrief-search.py` gives
ranked full-text retrieval over curated memory — stdlib `sqlite3` FTS5, no
daemon, no dependencies. Two steps, so an agent fetches full text only for the
hits that earned it:

```bash
python3 debrief/.system/debrief-search.py search "temperature opus" -n 8
# [100] 2026-07-14 2026-07-14-explorer-….md §The EXP-28 staging outage — …«temperature» param left on the «Opus» variant…
python3 debrief/.system/debrief-search.py get 100
```

Documents are chunked by heading; results are one line each (`[id] date doc
§section — snippet`). The index (`.system/search.db`) rebuilds automatically
whenever any source file changes. `get -C 1` adds neighboring sections from
the same doc (target marked `▶`) when a hit lands mid-document — cheaper than
Read-ing the whole file. Ranking is bm25 with one hardcoded, curation-aware
weight vector: filename slugs and section headings (the most distilled human
signal) weigh more than body text, metadata columns don't match at all, and
dailies/INDEX get a mild tie-break multiplier over session notes. No knobs.

The one exception is the starred layer, and it is a **sort key rather than a
weight**: `HIGHLIGHTS.md` hits are ordered ahead of every other layer whenever
they match, marked `★`. A multiplier cannot deliver that promise — a mild boost
still loses to any competing filename or heading match, so "must-know surfaces
first" would hold only when the term appeared nowhere else, i.e. the case that
never needed help. Ordering on layer makes it unconditional and leaves the rest
of the ranking untouched.

```bash
python3 debrief/.system/debrief-search.py stars                        # the ★ layer, whole, no query
python3 debrief/.system/debrief-search.py search "waf" --starred       # ★ entries only
```

`stars` prints entries only — the file's own explanation of how starring works is
scaffolding and is filtered out, so the cheap-recall path stays cheap.

`debrief-search.py selftest` checks the invariants on a synthetic corpus —
pass/fail only, never a score. The central assertion is the gate itself:
machine-draft content must be unsearchable. The starred invariants assert against
the *hard* case (a highlight must outrank a competing doc-title match), because
an earlier multiplier-based implementation passed a synthetic test where the term
lived only in `HIGHLIGHTS.md`, then lost on the real corpus.

**The curation gate applies to retrieval too.** The index holds dailies,
`INDEX.md`, and `status: curated` session notes — never machine drafts,
archived or not. A draft's content becomes searchable only by surviving
`/debrief day`. Searching drafts directly would hand back exactly the
confidently-wrong memory this system exists to prevent; an empty result plus
"that date needs a `/debrief day` pass" is the correct behavior, and
`commands/debrief-search.md` (install next to `debrief.md`) says so to the
agent in as many words.

Query terms are AND-ed; valid FTS5 syntax (`"exact phrase"`, `OR`, `NOT`)
passes through. `--dir` / `$DEBRIEF_DIR` override discovery, same as the
provenance builder.

## Viewer

```bash
cd debrief/viewer
npm install
npm run dev        # http://localhost:5199
```

Vite + React. The graph tab is a vertically stacked drill-down tree —
session → repo → file → function, newest first, spread open on first load —
not a force simulation: dates read top-to-bottom and every level is a block
you click into. Markdown is pulled in via
`import.meta.glob` at dev time, so editing a note hot-reloads the graph. A fresh
clone renders the shipped example fixture; point `repos.json` at real repos and
it renders yours.

## Rules

- **Machine drafts never touch `INDEX.md` or daily files.** The semantic layer is
  human-gated; `/debrief day` is the only writer, and it reviews first.
- Drafts carry `status: machine-draft` and an unverified-claims banner. Day mode
  **flags** those claims — it does not launder them into fact.
- Episodic files are immutable; corrections happen by rewiring INDEX.
- **Never star anything on your own initiative.** `HIGHLIGHTS.md` is human-written
  by definition — propose with `★ (candidate)`, never promote. And it is
  append-only: a wrong highlight is superseded by a new entry naming it, never
  edited or deleted.
- Multiple drafts for one `session_id`: newest wins; `curated` beats
  `machine-draft`.
- `sessions/raw/` is cold storage — **never indexed, never auto-read**, and
  **redacted before it lands** (`redact-transcript.py`, fail-closed) with a
  `DEBRIEF_RAW_RETAIN_DAYS` horizon. See "Verbatim archive" below. It
  exists solely because Claude Code deletes transcripts after ~30 days
  (opt-in: `DEBRIEF_RAW_ARCHIVE=1` on the hook). Day mode may fall back to it
  when a live transcript is gone; nothing else touches it. Indexing it would
  collapse the curation gate.
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

`pending` means one thing only: **awaiting the drafter.** A draft that landed
but that no `/debrief day` ever consumed used to keep that status forever, so
an unread backlog grew invisibly (16 entries over two weeks in a live install).
Entries whose draft is on disk and older than 24h now become `unconsumed`, and
every sweep logs the count and the dates:

```
unconsumed-backlog: 8 drafts await a day-debrief (dates: 2026-07-06,2026-07-08,…)
```

Only `/debrief day <date>` clears it — archiving the draft flips the entry to
`aggregated` via the heal above. Queue statuses in full: `pending` (drafter
running) → `unconsumed` (draft waiting on curation) → `aggregated` (consumed by
a daily), or `failed` (drafter died 3×, or its transcript is gone).

That log line is the **detection** end of the loop; the **surfacing** end is a
SessionStart hook, `.system/backlog-nudge.sh`, which on a fresh start injects a
one-line backlog summary into the session (once per day, silent on a clean
queue). `/debrief-backlog` is the human-facing view of the same queue —
`.system/debrief-backlog.py` prints the exact `/debrief day <date>` command for
each stranded date, oldest first. Both are strictly read-only: they never write
a daily, `INDEX.md`, or a queue status. Draining stays a human `/debrief day`
pass — the curation gate, unchanged.

`failed` is **not terminal** when the material survives. `/debrief day` reads a
failed entry's `transcript_path` directly, falling back to the raw cold archive;
`debrief-backlog.py` labels each failed entry `recoverable` (transcript or raw
on disk) or `terminal` (both gone) so you know which are worth a curation pass.
`debrief-backlog.py selftest` and `backlog-selftest.sh` check these invariants —
including that a scan leaves the queue byte-identical, and that the tools resolve
their debrief dir through a symlinked install rather than back to this clone.

## Verbatim archive (`sessions/raw/`) — redacted, retained, never indexed

Off by default. Enable with `DEBRIEF_RAW_ARCHIVE=1` on the SessionEnd hook:

```jsonc
// ~/.claude/settings.local.json → hooks.SessionEnd[].hooks[]
{
  "type": "command",
  "command": "$CLAUDE_PROJECT_DIR/_system/debrief/.system/session-debrief.sh",
  "async": true,
  "env": { "DEBRIEF_RAW_ARCHIVE": "1", "DEBRIEF_RAW_RETAIN_DAYS": "180" }
}
```

### Why you'd want it

Dailies and session notes are **summaries** — they keep the conclusions and drop
the evidence. The archive is what you reach for when the conclusion isn't enough:

| You need | Summary gives you | Archive gives you |
|---|---|---|
| **The exact file that broke** | "the WAF rejected plan bodies" | `terraform/environments/staging/main.tf:683`, the line and its value |
| **The exact identifier** | "a CRS rule matched" | `owasp-crs-v030301-id932140-rce`, `priority 1000`, `body_denied_by_security_policy` |
| **The exact command that proved it** | "verified in the cluster" | the literal `kubectl`/`gcloud` invocation and its output |
| **What an overnight run actually did** | the agent's own account of itself | every tool call, in order, including the ones it didn't mention |

That last row is the strongest reason. For autonomous sessions you did not watch,
a self-written summary is the agent grading its own homework; the transcript is
the only independent record. Concretely, `grep -l` over the archive answers *"which
session touched this file, and what did it run?"* — a question the semantic layer
is structurally unable to answer, because it stores understanding rather than acts.

```bash
# which archived session touched a file, and what did it do there?
zgrep -l "SimulateStage.tsx" debrief/sessions/raw/*/*.jsonl.gz
zgrep -h "kubectl -n tensorzero" debrief/sessions/raw/2026-07-24/*.jsonl.gz | head
```

### Redaction is not optional

The transcript contains everything the tool calls echoed, and the leak vector is
**not** credentials handled deliberately — it is third-party output that happens
to carry one. Measured case: a LiveKit `access_token=eyJ…` arrived inside a
Traefik access-log line during an unrelated outage investigation, and a raw
archive would have banked it verbatim.

So the transcript is piped through `.system/redact-transcript.py` before gzip.
It scrubs JWTs, `Bearer` tokens, GitHub/Slack/Stripe/Google/AWS/OpenAI/Anthropic
key shapes, PEM private-key blocks, `user:pass@host` URLs, and a broad
`*password|*secret|*token|*api_key… = value` sweep — replacing each with
`[REDACTED:<kind>]`.

Three properties matter more than the pattern list:

- **Output stays valid JSONL, line for line.** Day mode parses this as its
  fallback; a redactor that corrupts structure destroys the only reason the
  archive exists. Verified against a real 86-line transcript: 86 lines out, zero
  invalid JSON, zero residual tokens.
- **Fail closed.** A missing or failing redactor means **no archive**, not a raw
  one. Same for a truncated pipe — gzip's exit status is not trusted, because
  gzip succeeds happily on a truncated stream.
- **Over-redaction is fine; under-redaction is not.** One deliberate exception:
  `authorization` is spelled out rather than bare `auth`, so git JSON's
  `"author":"…"` survives. Redacting every commit author would gut the audit
  value the archive exists for.

`redact-transcript.py --selftest` asserts all of it — including that a
`DB_PASSWORD=` style key is caught (`\bpass` does **not** match inside
`DB_PASSWORD`, because `_` is a word character; the selftest caught that bug).

### Retention

`DEBRIEF_RAW_RETAIN_DAYS` (default **180**) prunes whole date directories past
the horizon on each hook run. Compared by directory **name**, not mtime, so a
backup pass or filesystem copy cannot silently extend an archive's life. Set `0`
to disable pruning — but "forever" is otherwise a decision you make once, by
accident, and cannot undo.

Volume is not the constraint: measured at ~0.6–3.5 MB/day raw, ~59% after gzip —
roughly **1 MB/day, ~365 MB/year**.

### Still never indexed

Stored as `.jsonl.gz`, structurally invisible to `debrief-search.py`'s
`collect()` (non-`.md`, no curated frontmatter). This is deliberate and load
bearing: the moment raw transcripts become searchable, unverified machine claims
re-enter memory through the back door — exactly what the curation gate exists to
prevent. The archive is insurance and audit, never a search corpus.

## Cost / tuning

Each substantive session exit spawns one headless `claude -p` run at the default
model. To cheapen it, add `--model haiku` (or `sonnet`) to the `claude -p`
invocation in `.system/session-debrief.sh`. To change what counts as
"substantive", adjust the 30-line transcript guard there.

## Layout

```
debrief/
  INDEX.md                  # semantic layer (template — edit for your project)
  HIGHLIGHTS.md             # starred layer — ★ must-know-forever (append-only, never pruned)
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
    backlog-nudge.sh        # the SessionStart hook (surfaces the backlog, once/day)
    debrief-backlog.py      # read-only backlog report (full + --nudge); source of truth
    backlog-selftest.sh     # integration selftest: sweep heal + nudge + gate invariants
    archive-selftest.sh     # integration selftest: redaction + fail-closed + retention
    build-provenance.mjs    # git → provenance.json
    debrief-search.py       # FTS5 retrieval over curated memory (+ stars / --starred)
    redact-transcript.py    # scrubs secrets out of raw archives (fail-closed); --selftest
    search.db               # generated by debrief-search.py; gitignored
    .backlog-nudge          # generated: once-per-day nudge stamp; gitignored
    repos.example.json      # copy to repos.json
    prompts/session-draft.md
  viewer/                   # Vite + React memory viewer
commands/
  debrief.md                # install to ~/.claude/commands/
  debrief-backlog.md        # install alongside — /debrief-backlog (what needs curating)
  debrief-search.md         # install alongside — /debrief-search <query>
  debrief-highlights.md     # install alongside — /debrief-highlights [query] (the ★ layer)
```

## Known edges

- **macOS/Linux only.** The hook is bash and shells out to `python3` for JSON.
- **The transcript format is internal and unstable.** The drafter reads it as
  *text*, never parses it. Don't be tempted.
- **Curation doesn't scale by itself.** `/debrief day` is a real human pass. If
  you skip it for a week, you have a pile of drafts, not memory — which is the
  honest failure mode, and better than a confident wrong index.
