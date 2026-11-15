# Material (`sessions/material/`) — the originals, as files

Off by default. Enable with `DEBRIEF_MATERIAL=1` on the SessionEnd hook and in
the daemon's environment.

A daily is one reading of a session. It says "the header got calmer". The
screenshot that showed it, the version that was rejected, the essay that was
pasted in and the link that was pointed at are not in the daily. They are in
the transcript, as base64 and tool input, where nobody can look at them. So a
later agent gets the sentence and has to imagine the rest.

`.system/extract-material.py` lifts them out at session end:

```
sessions/material/<first 8 of the session id>/
  manifest.json                 what each file is, and its line in the transcript
  images/<sha12>.<ext>          every image the session saw: pasted, screenshot, Read
  code/<NNN>-<name>             each version a Write produced of a design file
  code/<NNN>-<name>.patch       each Edit, MultiEdit or NotebookEdit to a design file, as a diff
  code/final/<sha8>-<name>      the file on disk when the session ended
  code/<sha8>-<repo>.diff       uncommitted changes to those files at the end, untracked files included
  artifacts/<NNN>-<sha8>.html   pages the session published
  <page>.bundled.html           a stored page with its local assets inlined, as one file
  references/<NNN>-<sha8>.md    long text the human pasted in
```

The manifest also lists the links the human gave and the links tools opened.
Other writers add `turns/`, `screens/` and `memory/` to the same folder. The
extractor never deletes, moves or rewrites them, and keeps manifest keys it
does not own.

## Subagents

A session's subagents write their own transcripts beside the main one:

```
~/.claude/projects/<project>/<session>.jsonl
~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl
~/.claude/projects/<project>/<session>/subagents/workflows/<wf>/agent-<id>.jsonl
```

The extractor reads all of them, merged in time order with the main one. An
entry that came from a subagent has `"agent": "agent-<id>"` (or
`"workflows/<wf>/agent-<id>"`), and its `line` is the line in that file. No
`agent` means the main transcript. `journal.jsonl` in a workflow folder is the
runner's log and is not read.

A subagent's "user" turn is the prompt its parent wrote. It is never the
human's words, so it gives no reference and no human link. Links its tools
opened are kept.

A redacted archive under `sessions/raw/` holds the main transcript. The
backfill reads subagent transcripts beside it when they are there, under
`<HHMM>-<sid8>.subagents/` or `<HHMM>-<sid8>/`, gzipped or not.

## Published pages

A page is kept from every publish call: the artifact tools, `publish_files`
and `publish_version`. Each entry in `artifacts` records `title`, `tool`,
`path` (and `as`, the served name) and `copy`, which says where the text came
from:

| `copy` | Meaning |
|---|---|
| `inline` | the HTML was in the tool call itself |
| `write-time` | the page as the session's own Write, Edit and MultiEdit calls left it at the moment of that publish |
| `session-end` | read from disk after the session ended, while it was fresh |
| `later` | read from disk by a run more than an hour after the session's last message: it may differ from what was published |

A write-time copy assumes the file changed only through those tools. A
shell command that names the file drops the write-time copy, and the page is
read from disk instead. Two publishes of the same bytes share one file.

A published page that could not be read (deleted, not a page, too large) is
listed under `unkept` with the reason, so the reader knows it existed.

## Bundled pages

A stored page loses its local images, stylesheets, scripts and fonts. So
beside each stored HTML page (a published page, and a design file ending in
`.html`), the extractor writes `<name>.bundled.html`: one file with local
assets inlined. Images and fonts become `data:` URIs, stylesheets become
`<style>` blocks, `@import` and CSS `url()` are followed, scripts become inline
`<script>` blocks. `http(s)` references stay as written. Imports inside a
script are not followed.

Hard limits:

- **Its own tree only.** An asset is read only when its real path (symlinks
  resolved) is inside the page's folder or the page's git repo. An absolute
  path or `file://` that lands outside is refused. The home folder and `/`
  never count as a page's folder. `.git`, `.ssh`, `.aws`, `.gnupg` and `.env*`
  are refused even inside the tree.
- **Asset types only:** images, SVG, CSS, JS and fonts. Anything else is
  refused.
- **Caps:** 8 MB per asset, 24 MB per page, 96 MB of bundles per run.
- The manifest entry's `bundle` lists what was `bundled`, what was `missing`
  and what was `refused` (with the reason), and `assets_read`: `session end`
  or `later`, like `copy` above.

CSS, JS and SVG text goes through the redactor. With no redactor, no bundle
is written. **Binary assets (images, fonts) cannot be redacted.** A bundled
page carries every image its page showed, exactly as on disk.

## What this is for

| You need | The daily gives you | Material gives you |
|---|---|---|
| **What the rejected version looked like** | "the first banner was too loud" | the screenshot the human was looking at when they said it |
| **A direction that was dropped** | "tried a grid layout, reverted" | the component as it was written, before the revert |
| **Work that never reached git** | nothing | the file on disk at session end and its uncommitted diff |
| **The reference the work started from** | "the human shared an essay" | the essay |

The second and third rows matter most. An attempt that was abandoned is usually
never committed, so git does not have it, and the transcript that has it is
deleted in about 30 days. A dead end in one project is often the start of
another, and it can only be that if somebody can still open it.

## The design set

Code is kept for design files only: `.tsx .jsx .vue .svelte .astro .html .css
.scss .sass .less .svg .swift .dart .mdx`, plus `design*.md`, token files,
`tailwind.config.*` and `theme.*`. Set `DEBRIEF_MATERIAL_EXT` to a
comma-separated list to replace the extensions. Backend code is not kept: git
has it, and the provenance graph already links a session to its commits.

## Rules the extractor keeps

- **Tolerant, never a parser of record.** The transcript format is internal and
  unstable. Every block is found by a walk that needs no field to be present.
  A line that does not parse is skipped and counted.
- **Read before redaction.** The hook extracts from the live transcript. A
  redaction pattern can land inside base64 and damage an image. A PNG is
  checked chunk by chunk (CRC) and a damaged image is dropped, because a
  damaged image still opens and shows the wrong thing.
- **Text is redacted, and fails closed.** Code, pages, bundled CSS and JS,
  references and links go through `redact-transcript.py`. With no redactor,
  no text is written.
- **Images cannot be redacted.** A screenshot of a terminal can show a key. The
  store is local and gitignored. If a session showed a secret on screen,
  delete that image file: the manifest entry then points at nothing, which is
  correct.
- **Only the human's turns are the human's.** Harness reminders, skill text
  and request blobs are skipped, so a link in a reminder is not recorded as a
  link the human gave.
- **A resumed session adds, never rewrites.** Files are named by content hash
  or by order. Running again on a longer copy of the session adds what is new.
- **A snapshot belongs to a fresh session.** The file on disk and the
  uncommitted diff are taken only when the session's last message, in the
  main transcript or any subagent's, is under an hour old. The transcript's
  modified time is not that clock: the harness appends bookkeeping records
  after the last message, and a subagent can write after the main
  transcript. A transcript the daemon finds days later gets none: the tree
  has moved on. `--snapshot` allows a snapshot; the extractor still checks.
- **The snapshot covers every file the session touched.** Write, Edit,
  MultiEdit and NotebookEdit, in the main transcript and in subagents. A
  design file git has never seen goes into the diff as a new file, and its
  final copy is kept.
- **A headless run is tagged, never dropped.** When the first user record's
  `entrypoint` starts with `sdk` (`sdk-cli`, `sdk-ts`), the manifest says
  `"synthetic": "headless"` and keeps the entry point. `cli` and
  `claude-desktop` are interactive.
- **An empty session leaves no folder.** A folder that another writer already
  made (`turns/`, `screens/`, `memory/`) stays, and gets a manifest.
- **A re-run adds nothing and rewrites nothing.** The manifest is written
  only when its bytes change.

## Never indexed, reached from a note

Material is evidence, not memory. `debrief-search.py` never walks
`sessions/material/` or `sessions/raw/`, and a selftest holds that line. The
way in is from a curated note:

```bash
debrief-search.py get 412          # ends with: (originals kept for 0000aaaa — `source 412` lists them)
debrief-search.py source 412       # or: source 0000aaaa
```

`source` prints the transcript archive, the note, and every kept file with its
size, its line in the transcript and where it came from. Then Read the file.

**The agent rule.** An agent may open what a curated note points at: to look
at a piece before working from it, or to check a claim it doubts. It reads the
part it needs. What it reads is what one session saw, with every abandoned
theory still in it and nothing marking which. So it is never quoted as a fact
about the project, and an agent never searches the originals for an answer
that curated memory did not give. If the note and the original disagree, say
so, and say which one you opened.

## Backfill

```bash
python3 .system/extract-material.py --backfill "$DEBRIEF_DIR"
```

Walks `sessions/raw/`, oldest first. It reads the live transcript (and its
subagents) when the queue still has one, else the redacted archive. It is safe
to run again: a second run adds nothing and rewrites nothing. It takes no
snapshot, and a page it reads from disk is marked `later`.

## Size

Measured on three real sessions: 3.2 MB for 17 images, 6 code files and 3
references. Images are stored once per session by content hash.
