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
  code/<NNN>-<name>.patch       each Edit to a design file, as a diff
  code/final/<sha8>-<name>      the file on disk when the session ended
  code/<sha8>-<repo>.diff       uncommitted changes to those files at the end
  artifacts/<NNN>-<sha8>.html   pages the session published
  references/<NNN>-<sha8>.md    long text the human pasted in
```

The manifest also lists the links the human gave and the links tools opened.

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
- **Text is redacted, and fails closed.** Code, pages, references and links go
  through `redact-transcript.py`. With no redactor, no text is written.
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
  uncommitted diff are taken only when the transcript is under an hour old.
  A transcript the daemon finds days later gets none: the tree has moved on.
- **An empty session leaves no folder.**

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

Walks `sessions/raw/`, oldest first. It reads the live transcript when the
queue still has one, else the redacted archive. It is safe to run again.

## Size

Measured on three real sessions: 3.2 MB for 17 images, 6 code files and 3
references. Images are stored once per session by content hash.
