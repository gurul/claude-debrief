# Code-provenance layer (the graph)

The viewer's graph tab is a `session → repo → file → function` drill-down of the
*actual files and functions* each debrief touched — not just concept
`[[wikilinks]]`. `.system/build-provenance.mjs` derives it straight from git
(`numstat` for files, hunk-header context for enclosing symbols) across every
repo in `repos.json`, and writes `provenance.json`.

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

## Declaring commits in frontmatter

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

## Trust and failure behavior

**Frontmatter is hand-written, so the builder never trusts it.** Every SHA is
confirmed with `git cat-file` before any plumbing runs; anything that isn't a
SHA, a range or an annotation is reported and skipped. A placeholder like
`temperature-strip fix (hash not captured in draft)` costs you one warning and
one absent node — it does not abort the build. Warnings name the document and
the field, so an unresolved SHA points at the repo you forgot to configure:

```
2026-07-15-fluid-canvas.md [touched.era-firmware-rs]: unresolved commit 5d6fde7 (declared era-firmware-rs)
```

## Building

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
