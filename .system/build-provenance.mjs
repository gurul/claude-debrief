#!/usr/bin/env node
// build-provenance.mjs — derive the code-provenance layer for the memory graph.
//
// For every debrief/session note that carries a `touched:` frontmatter block
// (curated: which commits, per repo), resolve — straight from git, across all
// three repos — the files and the enclosing functions each commit changed, and
// emit debrief/.system/provenance.json. The viewer reads it to render the
// session → repo → file → function drill-down.
//
// Auto layer  = the files/functions git reports (numstat + hunk-header context).
// Curated layer = the `touched:` SHAs the human put in frontmatter (what counts
//                 as "this session's work"), plus optional `highlight:` symbols.
//
// Frontmatter shape (dependency-free, one line per repo):
//   ---
//   touched:
//     my-api: 9c81346 9bdcdc9 3d46771
//     my-worker: 68a6083
//   ---
//
// Run: node debrief/.system/build-provenance.mjs   (re-run after new commits)

import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url)); // debrief/.system
const DEBRIEF = join(HERE, '..'); // debrief/

// Which working copies this memory spans: repo name → path, from
// .system/repos.json (see repos.example.json). Paths are resolved relative to
// debrief/, so ".." is the host repo and "../../peer" is a sibling checkout.
//
// No config → no-op. A fresh clone has no repos to read, and silently
// rewriting provenance.json to an empty graph would blank the shipped example
// before anyone had a chance to see the viewer work. Exit 0, not 1: this runs
// as a predev/prebuild hook, and a missing optional config must not fail the
// dev server.
const CONFIG = join(HERE, 'repos.json');
if (!existsSync(CONFIG)) {
  console.log(
    'build-provenance: no .system/repos.json — skipping (copy repos.example.json to repos.json to enable the graph).',
  );
  process.exit(0);
}
const REPOS = Object.fromEntries(
  Object.entries(JSON.parse(readFileSync(CONFIG, 'utf8')).repos ?? {}).map(
    ([name, p]) => [name, isAbsolute(p) ? p : resolve(DEBRIEF, p)],
  ),
);
if (Object.keys(REPOS).length === 0) {
  console.log('build-provenance: repos.json lists no repos — skipping.');
  process.exit(0);
}

function git(repo, args) {
  return execFileSync('git', ['-C', REPOS[repo], ...args], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
}

// Which of the three repos contains this commit? (first match wins.)
// Lets a debrief list bare SHAs (`commits:`) without naming the repo.
function resolveRepo(sha) {
  for (const name of Object.keys(REPOS)) {
    if (!existsSync(REPOS[name])) continue;
    try {
      execFileSync('git', ['-C', REPOS[name], 'cat-file', '-e', `${sha}^{commit}`], {
        stdio: 'ignore',
      });
      return name;
    } catch {
      // not in this repo
    }
  }
  return null;
}

// Pull a symbol name out of a git hunk-header context string
// (`@@ -a,b +c,d @@ <context>`). Falls back to a trimmed context line.
function symbolFromContext(ctx) {
  const c = ctx.trim();
  if (!c) return null;
  // import / re-export lines aren't functions — drop them from the label set
  if (/^import\b/.test(c) || /^export\s*\{/.test(c)) return null;
  const pats = [
    /(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/,
    /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/,
    /(?:class|interface|type|enum)\s+([A-Za-z_$][\w$]*)/,
    /(?:export\s+)?(?:default\s+)?([A-Za-z_$][\w$]*)\s*\(/,
    /\[([a-z_.]+)\]/i, // toml table header, e.g. [functions.plan_to_rhai...]
  ];
  for (const p of pats) {
    const m = p.exec(c);
    if (m) return m[1];
  }
  return c.length > 48 ? `${c.slice(0, 45)}…` : c;
}

// files + per-file functions a single commit changed, scoped to that commit.
function filesForCommit(repo, sha) {
  const numstat = git(repo, ['show', '--numstat', '--format=', sha]).trim();
  const files = [];
  for (const line of numstat.split('\n')) {
    if (!line.trim()) continue;
    const [adds, dels, path] = line.split('\t');
    if (!path) continue;
    // hunk-header contexts give the enclosing declaration per changed region
    let hunks = '';
    try {
      hunks = git(repo, ['show', '-U0', '--format=', sha, '--', path]);
    } catch {
      hunks = '';
    }
    const fns = new Set();
    for (const h of hunks.split('\n')) {
      const m = /^@@ .* @@\s?(.*)$/.exec(h);
      if (!m) continue;
      const sym = symbolFromContext(m[1]);
      if (sym) fns.add(sym);
    }
    files.push({
      path,
      adds: adds === '-' ? null : Number(adds),
      dels: dels === '-' ? null : Number(dels),
      functions: [...fns].sort(),
    });
  }
  return files;
}

// merge the same file across multiple commits (union functions, sum adds/dels)
function mergeFiles(fileLists) {
  const byPath = new Map();
  for (const f of fileLists.flat()) {
    const cur = byPath.get(f.path) ?? {
      path: f.path,
      adds: 0,
      dels: 0,
      functions: new Set(),
    };
    cur.adds += f.adds ?? 0;
    cur.dels += f.dels ?? 0;
    for (const fn of f.functions) cur.functions.add(fn);
    byPath.set(f.path, cur);
  }
  return [...byPath.values()]
    .map((f) => ({ ...f, functions: [...f.functions].sort() }))
    .sort((a, b) => b.adds + b.dels - (a.adds + a.dels));
}

function parseFrontmatter(raw) {
  if (!raw.startsWith('---')) return {};
  const end = raw.indexOf('\n---', 3);
  if (end === -1) return {};
  const fm = raw.slice(3, end);
  const out = { touched: {}, prs: {}, commits: [] };
  let section = null;
  for (const line of fm.split('\n')) {
    if (/^touched:\s*$/.test(line)) {
      section = 'touched';
      continue;
    }
    if (/^prs:\s*$/.test(line)) {
      section = 'prs';
      continue;
    }
    // flat `commits: <sha> <sha> …` — repo auto-resolved later
    const cm = /^commits:\s*(.+)$/.exec(line);
    if (cm) {
      out.commits = cm[1].trim().split(/\s+/);
      section = null;
      continue;
    }
    if (/^\S/.test(line)) section = null; // left a block
    const m = /^\s+([\w-]+):\s*(.+)$/.exec(line);
    if (m && section) out[section][m[1]] = m[2].trim().split(/\s+/);
  }
  return out;
}

function titleOf(body) {
  const m = /^#\s+(.+)$/m.exec(body);
  return m ? m[1].trim() : null;
}

function collectDocs() {
  const paths = [];
  for (const n of readdirSync(DEBRIEF)) {
    if (/^\d{4}-\d{2}-\d{2}.*\.md$/.test(n)) paths.push(join(DEBRIEF, n));
  }
  const sessRoots = [join(DEBRIEF, 'sessions'), join(DEBRIEF, 'sessions', 'archive')];
  for (const root of sessRoots) {
    if (!existsSync(root)) continue;
    for (const d of readdirSync(root, { withFileTypes: true })) {
      if (!d.isDirectory()) continue;
      const dir = join(root, d.name);
      for (const n of readdirSync(dir)) {
        if (n.endsWith('.md')) paths.push(join(dir, n));
      }
    }
  }
  return paths;
}

const allPaths = collectDocs();
// dates that already have a top-level daily debrief — those supersede their
// per-session notes so the graph shows one node per day, not two.
const dailyDates = new Set(
  allPaths
    .filter((p) => !p.includes('/sessions/'))
    .map((p) => /(\d{4}-\d{2}-\d{2})/.exec(p.split('/').pop() ?? '')?.[1])
    .filter(Boolean),
);
const sessions = [];
for (const p of allPaths) {
  const raw = readFileSync(p, 'utf8');
  const fm = parseFrontmatter(raw);
  const hasTouched = fm.touched && Object.keys(fm.touched).length > 0;
  const hasCommits = fm.commits && fm.commits.length > 0;
  if (!hasTouched && !hasCommits) continue;
  const id = p.split('/').pop();
  // Date resolution: daily filenames are `YYYY-MM-DD-*.md`, but per-session
  // notes live in `sessions/YYYY-MM-DD/HHMM-*.md` (filename starts with the
  // time, not the date). Fall back to the session directory, then a
  // frontmatter `date:`. Without this a session note gets date=null → it's
  // labelled by its title in the graph AND escapes the daily-supersede dedup
  // below (which requires a truthy date), leaking a duplicate node.
  const date =
    /^(\d{4}-\d{2}-\d{2})/.exec(id)?.[1] ??
    /\/sessions\/(?:archive\/)?(\d{4}-\d{2}-\d{2})\//.exec(p)?.[1] ??
    (typeof fm.date === 'string' ? fm.date.slice(0, 10) : null);
  // A daily supersedes its per-session notes — avoid double nodes per day.
  if (p.includes('/sessions/') && date && dailyDates.has(date)) continue;

  // Union of explicit (touched: repo→shas) and auto-resolved (commits: shas).
  const perRepo = new Map();
  const add = (repo, sha) => {
    const a = perRepo.get(repo) ?? [];
    if (!a.includes(sha)) a.push(sha);
    perRepo.set(repo, a);
  };
  for (const [repo, shas] of Object.entries(fm.touched ?? {}))
    for (const sha of shas) add(repo, sha);
  for (const sha of fm.commits ?? []) {
    const repo = resolveRepo(sha);
    if (repo) add(repo, sha);
    else console.warn(`  unresolved commit ${sha} in ${id}`);
  }

  const repos = [];
  for (const [repo, shas] of perRepo) {
    if (!existsSync(REPOS[repo])) continue;
    const files = mergeFiles(shas.map((sha) => filesForCommit(repo, sha)));
    const fnCount = files.reduce((n, f) => n + f.functions.length, 0);
    repos.push({ repo, shas, files, fileCount: files.length, fnCount });
  }
  if (repos.length === 0) continue;
  sessions.push({
    docId: id,
    date,
    title: titleOf(raw.slice(raw.indexOf('\n---', 3) + 4)) ?? id,
    prs: fm.prs ?? {},
    repos,
  });
}

sessions.sort((a, b) => (b.date ?? '').localeCompare(a.date ?? ''));
const out = {
  generatedNote:
    'AUTO-DERIVED from git (numstat + hunk-header function context) via debrief/.system/build-provenance.mjs. Curated by the `touched:` frontmatter in each debrief. Re-run after new commits.',
  builtFrom: 'git',
  sessions,
};
const dest = join(DEBRIEF, 'provenance.json');
writeFileSync(dest, `${JSON.stringify(out, null, 2)}\n`);
const totFiles = sessions.reduce(
  (n, s) => n + s.repos.reduce((m, r) => m + r.fileCount, 0),
  0,
);
console.log(
  `wrote ${dest}: ${sessions.length} session(s), ${totFiles} file node(s)`,
);
