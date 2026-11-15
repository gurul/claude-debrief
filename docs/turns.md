# Turns and screens — every version tried, and a picture of it

Off by default. `DEBRIEF_TURNS=1` turns on the patches. `DEBRIEF_SCREENS=1`
also turns on the screenshots. Unset or `0` means the script does nothing.

`extract-material.py` learns about code only from Write and Edit calls, at
session end. It misses two things:

- A design file that changed through a shell command (a `sed`, a codemod, a
  generator, `git checkout -- file`). We estimate 109 design files in 38
  sessions changed only this way.
- Each middle version that the session tried and then replaced. The end state
  is kept; the attempts between are lost.

A picture of the work exists only if someone pasted one or Read one.

`turn-capture.py` takes a snapshot of the design files at each prompt and at
each Stop, and keeps the difference as a patch. `screen-capture.py` takes
screenshots at Stop from the dev server that the repo already runs.

## Layout

```
sessions/material/<first 8 of the session id>/
  turns/
    <NNN>-agent.patch      what changed while the agent worked on one prompt
    <NNN>-between.patch    what changed while the agent was idle (unverified)
    index.jsonl            one line for each patch, or for a turn with no patch
  screens/
    <NNN>-<HHMMSS>-<desktop|phone>-<route slug>.png
    index.jsonl            one line for each picture, or one status line

$DEBRIEF_DIR/.system/turns/       private working state; never read as memory
  objects/<repo key>/      private git object store (the repo's store is an alternate)
  index/<session>-<repo key>.idx   private git index files
  state/  repos/  locks/  screens/  turns.log
```

`NNN` counts up for each session, across both kinds. `.system/turns/` must be
gitignored when the store is a checkout of this repo.

### `turns/index.jsonl`

```json
{"n": 2, "kind": "between", "file": "turns/002-between.patch", "status": "ok",
 "repo": "/path/to/repo", "base": "<tree>", "head": "<tree>", "head_commit": "<sha>",
 "files": [{"path": "src/Card.tsx", "added": 1, "removed": 1}],
 "files_truncated": null, "patch_truncated": null, "at": "2026-11-15T19:54:46Z",
 "attribution": "unverified", "doubts": ["head_moved"]}
```

- `base` and `head` are tree hashes of the two snapshots. `head_commit` is
  HEAD at the later one.
- `files` lists the first 50 files. When more changed, `files_truncated` has
  the full count.
- A patch over 256 KB is cut at a line boundary. `patch_truncated` has the
  full size in bytes, and the last line of the patch says it was cut.
- `status` is `ok`, `no_start_snapshot` (a Stop came with no snapshot from the
  start of the turn; `file` is null), or `no_redactor` (see below).

### What a between patch means

A between patch is **not** proof that the human edited a file by hand.
Another session, a formatter, a file watcher, a background task or an
interrupted turn can all write in that gap. So every between line has
`attribution: "unverified"`, and `doubts` lists each reason we know of:

| Doubt | Meaning |
|---|---|
| `interrupted_turn` | No Stop came for the previous prompt. The agent's unfinished work can be in this patch. Its base is the previous prompt's snapshot. |
| `head_moved` | HEAD changed in the gap: a commit, a checkout, a rebase. |
| `other_session` | Another session ran a hook in this repo in the gap, or had a turn open. |
| `background_task` | At the last Stop, a task the agent started in the background had no completion notice in the transcript. This reads an unstable transcript format and is a guess. |

An empty `doubts` list says only that none of these was seen.

## How a snapshot works

1. `git ls-files -m -d -o --exclude-standard` and
   `git diff-index --cached HEAD` list the changed, deleted, untracked and
   staged files. Both read the repo's index and never write it.
2. Keep only design files: exactly the set `extract-material.py`'s
   `is_design()` accepts (see docs/material.md), which includes `.swift`,
   `.svg`, storyboards and token files. Lockfiles, `.env*` files and
   `node_modules/`, `.next/`, `.svelte-kit/`, `.turbo/` are never kept.
3. In a private index file (`GIT_INDEX_FILE`), read HEAD's tree, lay those
   files over it by name, and `git write-tree`. New blobs and trees go to a
   private object store (`GIT_OBJECT_DIRECTORY`). The repo's own store is read
   as `GIT_ALTERNATE_OBJECT_DIRECTORIES`.
4. With no design file changed, the snapshot is HEAD's tree and nothing is
   written.

Nothing goes into the repo: not its index, not its objects, not a ref. Git
runs with split index, fsmonitor, the untracked cache, auto gc and hooks off,
and with literal pathspecs (so `app/[id]/page.tsx` is a file name, not a
pattern). The selftest checks that the repo's `.git` is byte-identical after a
turn.

At UserPromptSubmit the hook takes a snapshot. If the tree differs from the
last Stop's snapshot, it writes a between patch. At Stop, a detached child
takes a snapshot and writes the agent patch from the turn's start. A second
Stop in the same turn (a Stop hook that sent the agent back to work) diffs
from the first Stop, so no change is counted twice.

State is kept per session and repo, under `fcntl.flock`. A session can work
in more than one repo: each Stop also closes every repo where this session
has a turn open.

## Screenshots

At Stop, when the agent patch changed a web design file (`.tsx .jsx .vue
.svelte .astro .html .css .scss .sass .less .svg .mdx`, Tailwind config, theme
and token files), the detached child runs `screen-capture.py`. It also runs by
hand:

```bash
DEBRIEF_SCREENS=1 python3 .system/screen-capture.py run --cwd ~/code/app --out /tmp/shots [--url http://localhost:3000] [--route /settings] [--force]
```

1. **The dev server.** `.debrief/runtime.json` in the repo
   (`{"url": "http://localhost:3000", "routes": ["/", "/settings"]}`) wins.
   Else `lsof` lists listening TCP ports. A port is used only when the
   process's cwd is inside the repo and `GET /` answers with HTML. It never
   starts a server. A URL that is not loopback is refused (`url_not_local`).
   No server gives the status `no_dev_server`.
2. **The routes**, at most 3. A changed Next.js `app/**/page.*` or
   `app/**/layout.*` gives its route: route groups `(x)` and parallel slots
   `@x` are removed. A changed `pages/**` file gives its route, `pages/_app`
   gives `/`. An `app` or `pages` folder counts only at a Next.js project root
   (a folder with `next.config.*` or a `next` dependency) or in its `src/`.
   In a monorepo, only the app whose folder the server runs in counts. Links
   to this server already in the session's material manifest are added.

   **These edits only ever capture `/`:** a component, a style sheet, a token
   file or any other file that is not a page; a dynamic segment (`[id]`,
   `[...slug]`); a private folder (`_x`); an intercepting route `(.)x`; an API
   route; and every framework other than Next.js (Vite, SvelteKit, Astro,
   Remix, plain HTML). Use `.debrief/runtime.json` to name routes for those.
3. **The browser.** It uses a Playwright that is already installed: the
   repo's (or the server's app folder's) `node_modules/playwright` or
   `@playwright/test`, then a global install, then `DEBRIEF_PLAYWRIGHT` (a
   `node_modules` folder). The browser must already be in the Playwright cache.
   It prefers an install that also has WebKit. It never downloads. With none,
   the status is `no_browser`, and `hint` gives the one command to run.
4. **The pictures.** For each route: one desktop picture (Chromium, 1440×900,
   full page up to 6000 px high, `capped: true` when cut) and one phone
   picture (WebKit with an iPhone profile when WebKit is installed, else
   Chromium at the iPhone size; `engine` says which). A page that shows a
   Vite or Next.js error overlay, or whose document, script or style sheet
   answered 500 or higher, gets `status: "build_error"` with the overlay's
   kind and its text (redacted; dropped if no redactor loads). The picture is
   kept, but it is not filed as the page.
5. **Stale.** The design tree hash is taken before and after. If it changed
   during the capture, every line gets `stale: true`. When the hash equals the
   last captured one for this repo, nothing is taken (`--force` overrides).

One capture runs per repo at a time (flock). A request that finds one running
waits in a one-slot queue; a newer request replaces it. The capture is killed
after 45 seconds, browsers included, and records `timeout`.

### `screens/index.jsonl`

```json
{"n": 1, "kind": "screen", "file": "screens/001-115641-desktop-settings.png",
 "route": "/settings", "url": "http://localhost:3000/settings", "viewport": "1440x900",
 "engine": "chromium", "http_status": 200, "build_error": null, "stale": false,
 "tree": "<tree>", "turn": 3, "at": "...", "status": "ok",
 "blocked": {"blocked_method": 1, "blocked_origin": 2, "blocked_ws": 1}}
```

`turn` is the `n` of the agent patch that asked for the pictures. `status` is
`ok`, `build_error`, `error`, `timeout`, `no_dev_server`, `no_browser` or
`url_not_local`. A status line that repeats the previous one is not written
again. `blocked` counts what the guard stopped in the whole capture.

### Read-only, and its limits

The browser gets a fresh context with no stored cookies or session. It aborts
every request whose method is not GET, HEAD or OPTIONS; every request to any
origin other than the dev server's (no third-party hosts, remote images,
fonts or analytics); and every WebSocket, the dev server's own HMR socket too.
Service workers are blocked. Popups are closed and dialogs dismissed. Nothing
is clicked, typed or hovered. Proxy settings are ignored for the discovery
request. The selftest serves a page that tries a POST, a beacon, a
cross-origin GET, a remote image and a WebSocket, and checks that none
reached a server.

What this does not cover:

- **A GET with side effects still runs.** If loading a page writes to a
  database or sends a message, the capture does that. The discovery request
  to `/` is a GET too, and on a dev server it starts a compile.
- **An app behind a login is captured as its login page.** No session is
  carried in.
- **A page that needs its HMR socket to render** shows what it renders
  without it. Vite and Next.js pages render; a build-error overlay that only
  arrives over the socket should be caught by the 500 check instead. This was
  tested on synthetic pages, not on a real Vite or Next.js dev server.
- **Images cannot be redacted.** A page that shows a key keeps it in the
  picture. The store is local and gitignored. Delete the file if needed.

## Redaction

Patch text and the file names in `files` go through `redact-transcript.py`.
When the redactor does not load, no patch is written and the index line has
`status: "no_redactor"` with no file names. This fails closed.

## Hooks

Add these to `~/.claude/settings.json` under `"hooks"` (next to the existing
SessionEnd entry for `session-debrief.sh`, which stays):

```json
"UserPromptSubmit": [
  {"hooks": [{"type": "command", "timeout": 5,
    "command": "DEBRIEF_TURNS=1 python3 \"$HOME/claude-memory/debrief/.system/turn-capture.py\" --event UserPromptSubmit"}]}
],
"Stop": [
  {"hooks": [{"type": "command", "timeout": 5,
    "command": "DEBRIEF_TURNS=1 DEBRIEF_SCREENS=1 python3 \"$HOME/claude-memory/debrief/.system/turn-capture.py\" --event Stop"}]}
],
"SessionEnd": [
  {"hooks": [{"type": "command", "timeout": 10,
    "command": "DEBRIEF_TURNS=1 python3 \"$HOME/claude-memory/debrief/.system/turn-capture.py\" --event SessionEnd"}]}
]
```

Leave out `DEBRIEF_SCREENS=1` to keep patches and take no pictures. The hook
prints nothing on stdout on any event (UserPromptSubmit stdout would go into
the model's context) and always exits 0, on bad input, outside a repo and
when git fails. Errors go to `.system/turns/turns.log`. Each run marks the
`turns` and `screens` stages through `capture-status.py` when that script is
installed.

## Cost

Measured with the store in a temp dir, three runs each, the whole
UserPromptSubmit hook including Python start:

| Repo | Tracked files | Wall time |
|---|---|---|
| claude-debrief | 62 | 0.23 s |
| shop-web | 1,101 | 0.19–0.20 s |
| a larger repo | 3,828 | 0.27–0.55 s (first run cold) |
| a copy of shop-web with 60 design files changed | 1,101 | 0.31–0.34 s |

The git work has a 0.8 s budget and 0.5 s for each call. When it runs out,
the hook gives up with no snapshot, and the next Stop records
`no_start_snapshot`. The Stop hook returns at once; its child takes about as
long again, plus the capture. A capture of 2 routes × 2 viewports took 4–7 s.

Disk: a patch is a few KB; the cap is 256 KB. The private object store holds
only the blobs of changed design files. Pictures are 50 KB to 1 MB each.

## Cleanup

`--event SessionEnd` (or `turn-capture.py cleanup --session <id>`) deletes
the session's private index files and locks, and prunes private object stores
and state not used for 14 days. The patches do not depend on the private
store.

## Not built

- A session whose cwd is not inside a repo, but which works in repos below
  it: v1 does nothing. A file the agent edits by absolute path in a repo other
  than the cwd's is seen only when a turn was opened in that repo.
- Motion and timing: pictures are stills after load. No video, no transitions.
- The iOS Simulator: `.swift` files get patches, but no pictures.
- Frameworks other than Next.js get `/` only, unless `runtime.json` names
  routes.

## Selftests

```bash
python3 .system/turn-capture.py --selftest
python3 .system/screen-capture.py --selftest   # browser parts skip when no Playwright browser is cached
```
