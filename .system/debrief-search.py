#!/usr/bin/env python3
"""debrief-search — token-efficient retrieval over CURATED debrief memory.

Two-step workflow, deliberately mirroring the search→fetch split that keeps
agent context small: `search` returns a compact ranked index (one line per
hit); `get` returns full text only for the ids worth reading. Fetch nothing
you haven't filtered.

    debrief-search.py search "temperature strip" [-n 10]
    debrief-search.py get 12 15

The curation gate applies to retrieval, not just to writing: the index holds
daily debriefs, INDEX.md, and session notes with `status: curated` — never
machine drafts, archived or not. A draft's content becomes searchable only by
surviving `/debrief day`, where its claims get verified or flagged. Searching
drafts directly would hand back exactly the confidently-wrong memory this
system exists to prevent.

Debrief dir resolution: --dir flag, then $DEBRIEF_DIR, then walking up from
cwd for a `debrief/INDEX.md` (or cwd being the debrief dir itself). The index
lives at <debrief>/.system/search.db and rebuilds automatically when any
source file changes — stdlib sqlite3 FTS5, no daemon, no dependencies.

Semantic layer (optional): when a local Ollama serves EmbeddingGemma 2,
`search` also ranks chunks by meaning and fuses that list with bm25, so
"thermal" finds a note that only says "temperature". Vectors cover the same
gated chunks as FTS — nothing new becomes searchable. They are cached by
content hash in <debrief>/.system/embed-cache.db, so a reindex embeds only
changed sections. Ollama down or absent: keyword search, one stderr line.

    DEBRIEF_EMBED=off            keyword only (also: search --keyword)
    DEBRIEF_EMBED_URL            default http://127.0.0.1:11434
    DEBRIEF_EMBED_KEEP_ALIVE     default 30m (how long Ollama keeps the model loaded)
    DEBRIEF_EMBED_MODEL          default embeddinggemma-2
    DEBRIEF_EMBED_DIM            768 (MRL truncation: 512, 256, 128)

Recency: memory goes stale, so a dated chunk's score decays with its age
(half-life DEBRIEF_HALF_LIFE days, default 30, down to a floor so an old exact
match still surfaces). INDEX.md and HIGHLIGHTS.md are undated and never decay:
INDEX is pruned to what is true now, and a highlight is a must-know. Every hit
prints its age. DEBRIEF_RECENCY=off (also: search --no-recency) ranks by
relevance alone, for "what did we think back then" questions.

Supersession: dailies are immutable, so a fact that a later day nullified is
not deleted. It is invalidated in SUPERSEDED.md (written by the dream pass,
or by hand), one line per dead chunk:

    - 2026-09-04-old.md § Section heading → 2026-10-20-new.md — why

Omit `§ Section` to invalidate the whole doc. A superseded chunk sinks to
the bottom and prints `⊘ superseded by <doc>`, so the reader sees the old
claim and where its replacement lives. History is kept, never served as now.
"""

import argparse
import array
import datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- location


def find_debrief_dir(cli_dir):
    if cli_dir:
        return os.path.abspath(cli_dir)
    env = os.environ.get("DEBRIEF_DIR")
    if env:
        return os.path.abspath(env)
    cur = os.getcwd()
    while True:
        if os.path.exists(os.path.join(cur, "INDEX.md")) and os.path.isdir(
            os.path.join(cur, "sessions")
        ):
            return cur  # cwd IS the debrief dir
        cand = os.path.join(cur, "debrief")
        if os.path.exists(os.path.join(cand, "INDEX.md")):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            sys.exit("debrief-search: no debrief/ found (use --dir or DEBRIEF_DIR)")
        cur = parent


# ---------------------------------------------------------------- corpus

FM_STATUS = re.compile(r"^status:\s*(\S+)", re.M)
HEADING = re.compile(r"^(#{1,3})\s+(.+)$", re.M)
DAILY = re.compile(r"^\d{4}-\d{2}-\d{2}.*\.md$")


def frontmatter_status(raw):
    if not raw.startswith("---"):
        return None
    end = raw.find("\n---", 3)
    if end == -1:
        return None
    m = FM_STATUS.search(raw[3:end])
    return m.group(1) if m else None


def collect(debrief):
    """Yield (relpath, date, kind) for every document the gate admits."""
    for n in sorted(os.listdir(debrief)):
        if DAILY.match(n):
            yield n, n[:10], "daily"
    idx = os.path.join(debrief, "INDEX.md")
    if os.path.exists(idx):
        yield "INDEX.md", None, "index"
    # The starred layer. Human-marked and never pruned, so it is the highest-
    # confidence prose in the corpus — ranked accordingly in cmd_search.
    hi = os.path.join(debrief, "HIGHLIGHTS.md")
    if os.path.exists(hi):
        yield "HIGHLIGHTS.md", None, "highlight"
    sess = os.path.join(debrief, "sessions")
    if not os.path.isdir(sess):
        return
    for root, dirs, files in os.walk(sess):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in sorted(files):
            if not n.endswith(".md"):
                continue
            p = os.path.join(root, n)
            raw = open(p, encoding="utf-8", errors="replace").read()
            # The gate: only notes a human curated. Machine drafts reach
            # search exclusively via the daily that reviewed them.
            if frontmatter_status(raw) != "curated":
                continue
            m = re.search(r"/(\d{4}-\d{2}-\d{2})/", p)
            yield os.path.relpath(p, debrief), m.group(1) if m else None, "session"


def chunk(raw, relpath):
    """Split a document on #/##/### headings. Yields (section, text)."""
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            body = raw[end + 4 :]
    marks = list(HEADING.finditer(body))
    if not marks:
        text = body.strip()
        if text:
            yield "(whole file)", text
        return
    preamble = body[: marks[0].start()].strip()
    if preamble:
        yield "(preamble)", preamble
    for i, m in enumerate(marks):
        endpos = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        text = body[m.start() : endpos].strip()
        if text:
            yield m.group(2).strip(), text


# ---------------------------------------------------------------- index

# `kind` is UNINDEXED: it's routing metadata, not prose — indexed, a query
# containing "daily" or "session" would match chunks via their metadata.
# `date` stays indexed on purpose: a bare "2026-07-14" query is the zero-code
# temporal scope. `doc` and `section` stay indexed: filename slugs and heading
# titles are human-curated signal.
SCHEMA = """
CREATE VIRTUAL TABLE chunks USING fts5(doc, date, kind UNINDEXED, section, text);
CREATE TABLE meta (key TEXT PRIMARY KEY, val TEXT);
CREATE TABLE days (date TEXT PRIMARY KEY, doc TEXT, title TEXT, summary TEXT);
CREATE TABLE sessions (sid TEXT, date TEXT, project TEXT, ended TEXT, shape TEXT, doc TEXT,
                       PRIMARY KEY (sid, date));
CREATE TABLE entities (name TEXT PRIMARY KEY, grp TEXT, summary TEXT);
CREATE TABLE mentions (name TEXT, date TEXT, doc TEXT, section TEXT, n INTEGER);
CREATE TABLE todos (text TEXT, names TEXT, opened TEXT, status TEXT);
CREATE TABLE replaced (old_doc TEXT, old_section TEXT, new_doc TEXT, why TEXT);
"""
SCHEMA_V = "v5"  # bump when SCHEMA or ranking changes: forces cached dbs to rebuild


def corpus_fingerprint(debrief, docs):
    parts = []
    for rel, _, _ in docs:
        p = os.path.join(debrief, rel)
        parts.append(f"{rel}:{os.stat(p).st_mtime_ns}")
    return "|".join(parts)


def open_index(debrief):
    dbpath = os.path.join(debrief, ".system", "search.db")
    os.makedirs(os.path.dirname(dbpath), exist_ok=True)
    docs = list(collect(debrief))
    # The script's own size and mtime too: the structure tables are derived by
    # this code, so a new version rebuilds them without a SCHEMA_V bump.
    me = os.stat(os.path.abspath(__file__))
    fp = f"{SCHEMA_V}|{me.st_size}:{me.st_mtime_ns}|" + corpus_fingerprint(debrief, docs)
    db = sqlite3.connect(dbpath)
    try:
        cached = db.execute("SELECT val FROM meta WHERE key='fp'").fetchone()
        if cached and cached[0] == fp:
            return db
    except sqlite3.OperationalError:
        pass  # fresh db
    db.close()
    # Corpus changed (or first run): rebuild from scratch. ~dozens of small
    # markdown files — incremental indexing would be complexity, not speed.
    # Built beside the live file and swapped in whole, so a reader running at
    # the same moment (eve's chat, a Claude Code session) keeps the old index
    # instead of reading a half-built one (2026-11-09: 17 of 20 parallel
    # searches failed mid-rebuild).
    tmppath = f"{dbpath}.{os.getpid()}.tmp"
    os.path.exists(tmppath) and os.remove(tmppath)
    db = sqlite3.connect(tmppath)
    db.executescript(SCHEMA)
    n = 0
    for rel, date, kind in docs:
        raw = open(os.path.join(debrief, rel), encoding="utf-8", errors="replace").read()
        for section, text in chunk(raw, rel):
            db.execute(
                "INSERT INTO chunks VALUES (?,?,?,?,?)",
                (rel, date or "", kind, section, text),
            )
            n += 1
    build_structure(db, debrief, docs)
    db.execute("INSERT INTO meta VALUES ('fp', ?)", (fp,))
    db.commit()
    db.close()
    os.replace(tmppath, dbpath)
    print(f"(reindexed: {len(docs)} docs, {n} chunks)", file=sys.stderr)
    return sqlite3.connect(dbpath)


# ---------------------------------------------------------------- structure
#
# The notes already carry structure: a dated daily per day, INDEX.md's day rows
# and glossary of `[[name]]` entries, a Sessions table and addendum headings
# per daily, TODO.md's open and closed items, SUPERSEDED.md's ledger. The text
# search flattened all of it into chunks, so date and status questions were
# left to ranking, which cannot answer them (2026-11-09: "what was I working on
# yesterday" came back with two older days). These tables are derived from the
# same Markdown at every rebuild; the Markdown stays the source of truth.

DAILY = re.compile(r"^(\d{4}-\d{2}-\d{2})-[^/]+\.md$")
LINK = re.compile(r"\[\[([^\]\[|]+?)\]\]")
SID = re.compile(r"`([0-9a-f]{8})`")


def _cells(line):
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _project(text):
    """A project name from a cwd path or an addendum's parenthetical."""
    text = text.strip()
    m = re.search(r"cwd `?([^`,)]+)", text)
    if not m and text.strip("`").startswith(("/", "~")):
        text = text.strip("`")
    if m or text.startswith(("/", "~")):
        path = (m.group(1) if m else text).strip().strip("`")
        path = re.split(r"[`\s]", path, maxsplit=1)[0].rstrip("/")       # "/x/buddy` (on `main`)" -> "/x/buddy"
        return os.path.basename(path) or path
    for part in re.split(r",\s*", text):
        part = re.split(r"[`(]", part.strip().lstrip("`"), maxsplit=1)[0].strip()
        if part.startswith(("/", "~")):
            return os.path.basename(part.rstrip("/")) or part
        if part and len(part.split()) <= 3 and "→" not in part and not re.match(
                r"(ended|session|started|\d{1,2}:\d\d|\d{4}|[A-Z][a-z]{2} \d)\b|[0-9a-f]{8}$|\d+$", part):
            return part
    return ""


def build_structure(db, debrief, docs):
    """Fill the structure tables from the curated Markdown."""
    def read(rel):
        try:
            return open(os.path.join(debrief, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            return ""

    dailies = {}
    for rel, date, kind in docs:
        if kind == "daily" and date and DAILY.match(os.path.basename(rel)) and "/" not in rel:
            dailies.setdefault(date, rel)
    index = read("INDEX.md")
    rows = {}
    for line in index.splitlines():
        m = re.match(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|", line)
        if m and m.group(1) not in rows:
            cells = _cells(line)
            if len(cells) >= 3:
                title = re.sub(r"^\[(.*)\]\([^)]*\)$", r"\1", cells[1])
                rows[m.group(1)] = (title, " | ".join(cells[2:-1]) if len(cells) > 3 else cells[2])
    for date, rel in dailies.items():
        raw = read(rel)
        h1 = re.search(r"^# (.+)$", raw, re.M)
        title, summary = rows.get(date, ((h1.group(1) if h1 else rel), ""))
        db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?)", (date, rel, title, summary))
        # sessions: the daily's table, then each addendum heading
        lines = raw.splitlines()
        for at, line in enumerate(lines):
            cells = _cells(line) if line.startswith("|") else []
            if len(cells) >= 3 and SID.search(cells[0]):
                db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,?)",
                           (SID.search(cells[0]).group(1), date, _project(cells[1]),
                            cells[2] if re.match(r"\d{1,2}:\d\d", cells[2]) else "",
                            cells[3] if len(cells) > 3 else "", rel))
            elif line.startswith("## ") and "addendum" in line.lower() and SID.search(line):
                head = line[3:]
                paren = re.search(r"\(([^)]*)\)", head)
                ended = re.search(r"ended (\d{1,2}:\d\d)", head)
                shape = head.split(":", 1)[1].strip() if re.search(r"[)`]\s*:", head) else ""
                proj = _project(paren.group(1)) if paren else ""
                if not proj:     # the heading names none: the addendum's own first lines often give the cwd
                    body = "\n".join(lines[at + 1:at + 25])
                    cwd = re.search(r"cwd:?\s*`([^`]+)`|`((?:~|/Users/[^/`]+)/[^`\s]+)`", body)
                    proj = _project(cwd.group(1) or cwd.group(2)) if cwd else ""
                db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,?)",
                           (SID.search(head).group(1), date, proj.strip("`)( "),
                            ended.group(1) if ended else "", shape, rel))
    # mentions: [[name]] links per chunk of each daily
    for date, rel in dailies.items():
        for doc, section, text in db.execute(
                "SELECT doc, section, text FROM chunks WHERE doc=?", (rel,)).fetchall():
            counts = {}
            for name in LINK.findall(text):
                key = name.strip().lower()
                counts[key] = counts.get(key, 0) + 1
            for name, n in counts.items():
                db.execute("INSERT INTO mentions VALUES (?,?,?,?,?)", (name, date, doc, section, n))
    # entities: INDEX.md's glossary, one `- `[[name]]` — text` entry each, under ### groups
    gloss = re.search(r"(?ms)^## Glossary[^\n]*\n(.*?)(?=^## |\Z)", index)
    if gloss:
        grp, cur = "", None
        def flush():
            if cur:
                db.execute("INSERT OR REPLACE INTO entities VALUES (?,?,?)",
                           (cur[0], cur[1], " ".join(" ".join(cur[2]).split())))
        for line in gloss.group(1).splitlines():
            if line.startswith("### "):
                flush()
                grp, cur = line[4:].strip(), None
                continue
            m = re.match(r"^- `?\[\[([^\]]+)\]\]`?\s*(?:—|-|:)?\s*(.*)$", line)
            if m:
                flush()
                cur = (m.group(1).strip().lower(), grp, [m.group(2)])
            elif cur and line.strip():
                cur[2].append(line.strip())
            elif cur and not line.strip():
                flush()
                cur = None
        flush()
    # todos: TODO.md, open and closed
    todo = read("TODO.md")
    for status, title in (("open", "Open"), ("closed", "Closed")):
        m = re.search(rf"(?ms)^## {title}[^\n]*\n(.*?)(?=^## |\Z)", todo)
        if not m:
            continue
        for item in re.split(r"(?m)^- \[[ x]\] ", m.group(1))[1:]:
            text = " ".join(item.split())
            opened = re.search(r"opened (\d{4}-\d{2}-\d{2})", text)
            names = ",".join(sorted({n.strip().lower() for n in LINK.findall(text)}))
            db.execute("INSERT INTO todos VALUES (?,?,?,?)",
                       (text, names, opened.group(1) if opened else "", status))
    for (old, sec), (new, why) in load_superseded(debrief).items():
        db.execute("INSERT INTO replaced VALUES (?,?,?,?)", (old, sec or "", new, why))


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def resolve_span(word, today):
    """(first, last) dates a time word names, local time, or None.

    A day: YYYY-MM-DD, today, yesterday, day before yesterday, a weekday (its
    most recent past day, never today; "last"/"on" allowed). A span: this
    week and last week (Monday to Sunday), weekend (the most recent past
    Saturday and Sunday), this month, last month."""
    text = " ".join(str(word or "").strip().lower().replace("_", " ").split())
    text = re.sub(r"^(on|the|over the|last|past)\s+(?=(monday|tuesday|wednesday|thursday|friday|"
                  r"saturday|sunday|weekend)\b)", "", text)
    one = {"today": 0, "tonight": 0, "yesterday": 1, "last night": 1,
           "day before yesterday": 2, "the day before yesterday": 2}
    if text in one:
        d = today - datetime.timedelta(days=one[text])
        return d, d
    if text in WEEKDAYS:
        d = today - datetime.timedelta(days=(today.weekday() - WEEKDAYS.index(text)) % 7 or 7)
        return d, d
    monday = today - datetime.timedelta(days=today.weekday())
    if text == "this week":
        return monday, today
    if text == "last week":
        return monday - datetime.timedelta(days=7), monday - datetime.timedelta(days=1)
    if text in ("weekend", "this weekend", "last weekend"):
        sat = today - datetime.timedelta(days=(today.weekday() - 5) % 7 or 7)
        return sat, min(sat + datetime.timedelta(days=1), today)
    if text == "this month":
        return today.replace(day=1), today
    if text == "last month":
        end = today.replace(day=1) - datetime.timedelta(days=1)
        return end.replace(day=1), end
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return None
    try:
        d = datetime.date.fromisoformat(text)
    except ValueError:
        return None
    return d, d


def resolve_names(db, query):
    """Glossary names a spoken or typed name means, best first."""
    q = " ".join(str(query or "").lower().replace("_", " ").replace("-", " ").split())
    if not q:
        return []
    flat = lambda n: n.replace("-", " ").replace("_", " ")
    names = [n for (n,) in db.execute("SELECT name FROM entities")]
    names += [n for (n,) in db.execute("SELECT DISTINCT name FROM mentions") if n not in names]
    exact = [n for n in names if flat(n) == q or n == q.replace(" ", "")]
    if exact:
        return exact
    words = q.split()
    starts = sorted((n for n in names if flat(n).startswith(q)), key=len)
    within = sorted((n for n in names if n not in starts and all(w in flat(n).split() for w in words)), key=len)
    return (starts + within)[:6]


def today_line(today):
    return f"Today is {today.isoformat()} ({today:%A})."


# ---------------------------------------------------------------- semantic

EMBED_URL = os.environ.get("DEBRIEF_EMBED_URL", "http://127.0.0.1:11434").rstrip("/")
# How long Ollama keeps the model loaded after a call. Its own default is 5m,
# which makes most searches in a working session pay the cold load again.
EMBED_KEEP_ALIVE = os.environ.get("DEBRIEF_EMBED_KEEP_ALIVE", "30m")
EMBED_MODEL = os.environ.get("DEBRIEF_EMBED_MODEL", "embeddinggemma-2")
EMBED_DIM = int(os.environ.get("DEBRIEF_EMBED_DIM", "768"))
# Floor for a meaning-only hit, on the CENTERED score (see semantic_top).
# Measured on a 576-chunk store: real queries put their best chunk 0.03-0.11
# above baseline, nonsense and stop-word queries 0.02 or below.
SEM_MIN = float(os.environ.get("DEBRIEF_SEM_MIN", "0.03"))
RRF_K = 60  # reciprocal-rank fusion constant (the standard one, untuned)

# Recency multiplier on the fused score: FLOOR + (1 - FLOOR) * 0.5^(age/HALF_LIFE).
# A 30-day half-life roughly matches how fast project state turns over here.
# The floor keeps a months-old note above unrelated noise: decay reorders
# near-ties toward the newest, it never hides an old strong match.
HALF_LIFE_DAYS = float(os.environ.get("DEBRIEF_HALF_LIFE", "30"))
RECENCY_FLOOR = 0.3
SUPERSEDED_FACTOR = 0.05  # below any live chunk's floor: invalidated sinks
LEDGER_LINE = re.compile(
    r"^\s*-\s*`?([^\s`§]+\.md)`?\s*(?:§\s*(.+?))?\s*(?:→|->)\s*`?([^\s`]+\.md)`?(?:\s+[—-]+\s+(.*))?$"
)


# A search never waits on embedding work it can hand off (2026-11-09: a search
# blocked on a stalled Ollama for the old 120 s timeout). Inside a search: the
# query is one short call (QUERY_TIMEOUT), and chunks the cache lacks are
# embedded only while SEARCH_EMBED_BUDGET lasts; the rest go to one detached
# `embed` run (FILL_LOCK keeps it to one) and stay keyword-findable meanwhile.
QUERY_TIMEOUT = float(os.environ.get("DEBRIEF_QUERY_TIMEOUT", "8"))
SEARCH_EMBED_BUDGET = float(os.environ.get("DEBRIEF_SEARCH_EMBED_BUDGET", "3"))
FILL_TIMEOUT = 120


class EmbedUnavailable(Exception):
    pass


def unit(v, dim):
    """Matryoshka truncation: keep the leading dims, then re-normalize."""
    v = v[:dim]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def ollama_embed(texts, timeout=FILL_TIMEOUT):
    """Embed texts through Ollama's /api/embed. stdlib only."""
    body = json.dumps({"model": EMBED_MODEL, "input": texts, "truncate": True,
                       "keep_alive": EMBED_KEEP_ALIVE})
    req = urllib.request.Request(
        EMBED_URL + "/api/embed", body.encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise EmbedUnavailable(f"{EMBED_MODEL}: HTTP {e.code} {e.read()[:120]!r}")
    except (urllib.error.URLError, OSError) as e:
        raise EmbedUnavailable(f"no embedder at {EMBED_URL} ({e})")
    vecs = out.get("embeddings")
    if not vecs or len(vecs) != len(texts):
        raise EmbedUnavailable(f"{EMBED_MODEL}: bad response")
    return vecs


# Each model's own retrieval prompts, from its model card: a model is asked
# the way it was trained to be asked, never in another model's format.
QWEN_TASK = "Given a question about the owner's past software work, retrieve the notes that answer it"
PROMPTS = {
    # EmbeddingGemma 2 ("SearchQuery" task)
    "embeddinggemma": (lambda doc, sec, text: f"title: {sec} ({doc}) | text: {text}",
                       lambda q: f"task: search result | query: {q}"),
    # Qwen3-Embedding: an instruction on the query side only
    "qwen3-embedding": (lambda doc, sec, text: f"{sec} ({doc})\n{text}",
                        lambda q: f"Instruct: {QWEN_TASK}\nQuery:{q}"),
    # BGE-M3: no instruction on either side
    "bge-m3": (lambda doc, sec, text: f"{sec} ({doc})\n{text}", lambda q: q),
}


def _prompts():
    family = next((k for k in PROMPTS if EMBED_MODEL.startswith(k)), "embeddinggemma")
    return PROMPTS[family]


def doc_prompt(doc, section, text):
    return _prompts()[0](doc, section, text)


def query_prompt(q):
    return _prompts()[1](q)


def _embed(embed, texts, timeout):
    """Call embed with a timeout when it takes one (fake embedders in the selftest do not)."""
    try:
        return embed(texts, timeout=timeout)
    except TypeError:
        return embed(texts)


def chunk_vectors(debrief, db, embed, dim=None, batch=16, budget=None):
    """rowid -> unit vector for every indexed chunk. Embeds only cache misses.

    The cache is a separate file because search.db is thrown away on every
    corpus change. Keyed by model + dim + prompt text, so editing one section
    re-embeds one section, and switching models never mixes vector spaces.
    """
    dim = dim or EMBED_DIM
    cpath = os.path.join(debrief, ".system", "embed-cache.db")
    cache = sqlite3.connect(cpath)
    cache.execute("CREATE TABLE IF NOT EXISTS vec (key TEXT PRIMARY KEY, v BLOB)")
    rows = db.execute("SELECT rowid, doc, section, text FROM chunks").fetchall()
    keyed = []
    for rowid, doc, section, text in rows:
        prompt = doc_prompt(doc, section, text)
        key = hashlib.sha256(f"{EMBED_MODEL}|{dim}|{prompt}".encode()).hexdigest()
        keyed.append((rowid, key, prompt))
    have = {}
    keys = [k for _, k, _ in keyed]
    for i in range(0, len(keys), 500):
        part = keys[i : i + 500]
        q = "SELECT key, v FROM vec WHERE key IN (%s)" % ",".join("?" * len(part))
        have.update(cache.execute(q, part).fetchall())
    missing = [(k, p) for _, k, p in keyed if k not in have]
    deadline = None if budget is None else time.monotonic() + budget
    left = 0
    for i in range(0, len(missing), batch):
        if deadline is not None and time.monotonic() >= deadline:
            left = len(missing) - i
            break
        part = missing[i : i + batch]
        timeout = FILL_TIMEOUT if deadline is None else max(1.0, deadline - time.monotonic())
        for (k, _), v in zip(part, _embed(embed, [p for _, p in part], timeout)):
            blob = array.array("f", unit(v, dim)).tobytes()
            cache.execute("INSERT OR REPLACE INTO vec VALUES (?, ?)", (k, blob))
            have[k] = blob
        cache.commit()
    if missing and len(missing) > left:
        print(f"(embedded {len(missing) - left} new chunks with {EMBED_MODEL})", file=sys.stderr)
    cache.close()
    if left:
        print(f"({left} new chunks left to a background embed; keyword-findable now)", file=sys.stderr)
        fill_in_background(debrief)
    return {rowid: array.array("f", have[k]) for rowid, k, _ in keyed if k in have}


def query_vectors(debrief, embed, prompts, dim):
    """Unit vectors for query prompts in one call; the empty query's is cached."""
    cpath = os.path.join(debrief, ".system", "embed-cache.db")
    cache = sqlite3.connect(cpath)
    cache.execute("CREATE TABLE IF NOT EXISTS vec (key TEXT PRIMARY KEY, v BLOB)")
    keys = [hashlib.sha256(f"{EMBED_MODEL}|{dim}|q|{p}".encode()).hexdigest() for p in prompts]
    got = {}
    for k, p in zip(keys, prompts):
        if p == query_prompt(""):
            row = cache.execute("SELECT v FROM vec WHERE key=?", (k,)).fetchone()
            if row:
                got[p] = array.array("f", row[0])
    need = [p for p in prompts if p not in got]
    if need:
        for p, v in zip(need, _embed(embed, need, QUERY_TIMEOUT)):
            got[p] = array.array("f", unit(v, dim))
            if p == query_prompt(""):
                k = keys[prompts.index(p)]
                cache.execute("INSERT OR REPLACE INTO vec VALUES (?, ?)", (k, got[p].tobytes()))
        cache.commit()
    cache.close()
    return [got[p] for p in prompts]


def fill_in_background(debrief):
    """Start one detached `embed` run for the chunks a search left; never waits on it."""
    lock = os.path.join(debrief, ".system", "embed-fill.lock")
    try:
        if time.time() - os.path.getmtime(lock) < FILL_TIMEOUT * 5:
            return          # one is running (or died recently; the next search retries)
    except OSError:
        pass
    try:
        subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), "--dir", debrief, "embed"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except OSError as e:
        print(f"(could not start the background embed: {e})", file=sys.stderr)


def cmd_embed(debrief, db):
    """Embed every chunk the cache lacks, one run at a time (the lock)."""
    lock = os.path.join(debrief, ".system", "embed-fill.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) < FILL_TIMEOUT * 5:
            print("(an embed run is already going)")
            return
        os.unlink(lock)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        n = len(chunk_vectors(debrief, db, ollama_embed))
        print(f"(embed cache covers {n} chunks)")
    except EmbedUnavailable as e:
        print(f"(embed failed: {e})")
    finally:
        os.close(fd)
        try:
            os.unlink(lock)
        except OSError:
            pass


def semantic_top(debrief, db, query, k, embed, kinds=None, dim=None):
    """[(rowid, score)] best-first, above SEM_MIN.

    score = cos(query, chunk) - cos(empty query, chunk). Raw cosines from
    this model sit in a narrow band (about 0.62 +/- 0.03 on a real store)
    and a few "hub" chunks score high against every query, nonsense
    included. Subtracting each chunk's similarity to an empty query removes
    the hub term, so a floor on what remains separates "about this" from
    "about anything". One extra embed call per search.
    """
    dim = dim or EMBED_DIM
    vecs = chunk_vectors(debrief, db, embed, dim, budget=SEARCH_EMBED_BUDGET)
    null, qv = query_vectors(debrief, embed, [query_prompt(""), query_prompt(query)], dim)
    if kinds:
        keep = {r for (r,) in db.execute(
            "SELECT rowid FROM chunks WHERE kind IN (%s)" % ",".join("?" * len(kinds)),
            list(kinds),
        )}
        vecs = {r: v for r, v in vecs.items() if r in keep}
    scored = [
        (r, sum(a * b for a, b in zip(qv, v)) - sum(a * b for a, b in zip(null, v)))
        for r, v in vecs.items()
    ]
    scored = [x for x in scored if x[1] >= SEM_MIN]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


# ---------------------------------------------------------------- commands


def fts_query(q):
    """Pass advanced FTS syntax through; fall back to quoted-AND on junk."""
    return q, " AND ".join(f'"{t}"' for t in re.findall(r"[\w-]+", q))


KEYWORD_SQL = (
    "SELECT rowid, doc, date, kind, section, "
    "snippet(chunks, 4, '«', '»', '…', 14) "
    "FROM chunks WHERE chunks MATCH ? {scope}"
    # Curation-aware weights (bm25 is negative, lower = better; one
    # hardcoded vector, no tunables). doc=2.0 / section=3.0: filename
    # slugs and headings are the most distilled human signal. kind=0.0
    # holds the UNINDEXED column's position. The CASE multiplier is a
    # mild layer preference — dailies are the reviewed layer, INDEX the
    # current-understanding layer — sized to break ties, never to
    # override a strong body match in a curated session note.
    #
    # The starred layer leads as a SORT KEY, not via a tuned multiplier.
    # A multiplier cannot deliver the promise: a mere 0.8 loses to any
    # competing title match (doc=2.0/section=3.0), so "must-know surfaces
    # first" would hold only when the term appeared nowhere else — the
    # easy case. Ordering on kind first makes it unconditional and
    # honest, and needs no constant to babysit. Within each group, and
    # for everything below, relevance is unchanged.
    "ORDER BY CASE kind WHEN 'highlight' THEN 0 ELSE 1 END, "
    "bm25(chunks, 2.0, 1.0, 0.0, 3.0, 1.0) * "
    "CASE kind WHEN 'daily' THEN 1.15 WHEN 'index' THEN 1.1 ELSE 1.0 END "
    "LIMIT ?"
)


def keyword_rows(db, query, limit, starred=False):
    raw_q, safe_q = fts_query(query)
    # --starred: highlights only. A deliberate escape hatch for "what must I
    # never forget about X", where even a strong body match elsewhere is noise.
    sql = KEYWORD_SQL.format(scope="AND kind='highlight' " if starred else "")
    try:
        return db.execute(sql, (raw_q, limit)).fetchall()
    except sqlite3.OperationalError:
        return db.execute(sql, (safe_q, limit)).fetchall()


def load_superseded(debrief):
    """{(doc, section-lowercase or None): (newer_doc, why)} from SUPERSEDED.md."""
    p = os.path.join(debrief, "SUPERSEDED.md")
    out = {}
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="replace"):
        m = LEDGER_LINE.match(line.rstrip())
        if m:
            old, sec, new, why = m.groups()
            out[(old, sec.strip().lower() if sec else None)] = (new, (why or "").strip())
    return out


def superseded_by(ledger, doc, section):
    return ledger.get((doc, (section or "").strip().lower())) or ledger.get((doc, None))


def age_days(date, today):
    try:
        return max(0, (today - datetime.date.fromisoformat(date)).days)
    except (TypeError, ValueError):
        return None  # undated layer (INDEX, HIGHLIGHTS): current by contract


def recency_factor(date, today):
    age = age_days(date, today)
    if age is None:
        return 1.0
    return RECENCY_FLOOR + (1 - RECENCY_FLOOR) * 0.5 ** (age / HALF_LIFE_DAYS)


def ranked(debrief, db, query, limit, starred=False, embed=None, dim=None,
           recency=True, today=None):
    """[(row, how, dead)] where how is 'kw', 'both' or 'sem' and dead is the
    (newer_doc, why) that superseded the chunk, or None.

    No embedder and recency off: exactly the keyword ranking. Otherwise the
    bm25 list (and, with an embedder, the cosine list) become reciprocal-rank
    scores, fused, then multiplied by the chunk's recency factor. Fusion uses
    ranks, not raw scores, so the scales never need calibrating. A chunk in
    SUPERSEDED.md is multiplied down below every live one, recency or not.
    The ★ sort key still holds for every keyword-matched highlight. A
    highlight found by meaning alone ranks by fused score, because "matches
    at all" is not a meaningful test for a similarity that is never zero.
    """
    today = today or datetime.date.today()
    ledger = load_superseded(debrief)
    pool = max(limit * 4, 40)
    kw = keyword_rows(db, query, pool if (embed or recency or ledger) else limit, starred)
    if not embed and not recency and not ledger:
        return [(r, "kw", None) for r in kw[:limit]]
    kinds = ["highlight"] if starred else None
    sem = semantic_top(debrief, db, query, pool, embed, kinds, dim) if embed else []
    score, how, rows = {}, {}, {r[0]: r for r in kw}
    for i, r in enumerate(kw):
        score[r[0]] = 1.0 / (RRF_K + i + 1)
        how[r[0]] = "kw"
    for i, (rowid, _) in enumerate(sem):
        score[rowid] = score.get(rowid, 0.0) + 1.0 / (RRF_K + i + 1)
        how[rowid] = "both" if rowid in how else "sem"
    for rowid in score:
        if rowid not in rows:
            doc, date, kind, section, text = db.execute(
                "SELECT doc, date, kind, section, text FROM chunks WHERE rowid=?",
                (rowid,),
            ).fetchone()
            words = text.split()
            snip = " ".join(words[:14]) + ("…" if len(words) > 14 else "")
            rows[rowid] = (rowid, doc, date, kind, section, snip)
    dead = {}
    for rowid in score:
        _, doc, date, kind, section, _ = rows[rowid]
        dead[rowid] = superseded_by(ledger, doc, section)
        if recency:
            score[rowid] *= recency_factor(date, today)
        if dead[rowid]:
            score[rowid] *= SUPERSEDED_FACTOR
    star_first = lambda r: 0 if rows[r][3] == "highlight" and how[r] != "sem" else 1
    order = sorted(score, key=lambda r: (star_first(r), -score[r]))
    return [(rows[r], how[r], dead[r]) for r in order[:limit]]


def cmd_search(debrief, db, args):
    embed = None
    if not args.keyword and os.environ.get("DEBRIEF_EMBED", "on").lower() not in ("0", "off", "no"):
        embed = ollama_embed
    recency = not args.no_recency and \
        os.environ.get("DEBRIEF_RECENCY", "on").lower() not in ("0", "off", "no")
    try:
        hits = ranked(debrief, db, args.query, args.limit, args.starred, embed, recency=recency)
    except EmbedUnavailable as e:
        print(f"(semantic off: {e}; keyword only)", file=sys.stderr)
        hits = ranked(debrief, db, args.query, args.limit, args.starred, None, recency=recency)
    if not hits:
        hint = "no starred entries match" if args.starred else \
            "no hits — try fewer/other terms (index covers curated memory only)"
        print(hint)
        return
    today = datetime.date.today()
    for (rowid, doc, date, kind, section, snip), how, dead in hits:
        snip = " ".join(snip.split())
        age = age_days(date, today)
        label = f"{date} ({age}d)" if age is not None else kind
        star = "★ " if kind == "highlight" else ""
        # "~": matched by meaning only — no query term is in the chunk.
        mark = "~" if how == "sem" else ""
        gone = f" ⊘ superseded by {dead[0]}" + (f" ({dead[1]})" if dead[1] else "") if dead else ""
        print(f"[{rowid}]{mark} {star}{label} {doc} §{section}{gone} — {snip}")
    legend = "; ~ = meaning match, no keyword" if any(h == "sem" for _, h, _ in hits) else ""
    if any(d for _, _, d in hits):
        legend += "; ⊘ = nullified by a later day, read the newer doc instead"
    order = "newest-weighted" if recency else "relevance only"
    print(f"\n({len(hits)} hits, {order}; `get <id>` for full text{legend})")


def cmd_day(debrief, db, args):
    """A day or a span, read by date: the day's summary, sessions and parts,
    then its note (one day), or each day's summary (a span)."""
    today = datetime.date.today()
    if args.date.strip().lower() == "latest":
        row = db.execute("SELECT date FROM days WHERE date <= ? ORDER BY date DESC LIMIT 1",
                         (today.isoformat(),)).fetchone()
        if not row:
            print("The debrief has no dated notes yet.")
            return
        args.date, args.until = row[0], None
    span = resolve_span(args.date, today)
    if span is None:
        print("date must be YYYY-MM-DD, today, yesterday, a weekday, latest, this week, last week, "
              "weekend, this month or last month")
        sys.exit(2)
    first, last = span
    if args.until:
        end = resolve_span(args.until, today)
        if end is None:
            print("until must be YYYY-MM-DD, today or yesterday")
            sys.exit(2)
        last = end[1]
    if last < first:
        first, last = last, first
    if (last - first).days >= 62:
        print("a range is at most 62 days")
        sys.exit(2)
    when = (f"{first.isoformat()} ({first:%A})" if first == last
            else f"{first.isoformat()} ({first:%a}) to {last.isoformat()} ({last:%a})")
    days = db.execute("SELECT date, doc, title, summary FROM days WHERE date BETWEEN ? AND ? "
                      "ORDER BY date DESC", (first.isoformat(), last.isoformat())).fetchall()
    head = today_line(today)
    if not days:
        before = db.execute("SELECT max(date) FROM days WHERE date < ?", (first.isoformat(),)).fetchone()[0]
        after = db.execute("SELECT min(date) FROM days WHERE date > ?", (last.isoformat(),)).fetchone()[0]
        near = ", ".join(d for d in (before, after) if d)
        print(f"{head} No debrief notes for {when}: no coding sessions were recorded then."
              + (f" The nearest days with notes: {near}." if near else ""))
        return
    def sessions(date):
        rows = db.execute("SELECT sid, project, ended, shape FROM sessions WHERE date=? ORDER BY ended",
                          (date,)).fetchall()
        return "; ".join(f"{p or 'unknown project'}" + (f", ended {e}" if e else "") + (f": {sh}" if sh else "")
                         + f" ({sid})" for sid, p, e, sh in rows)
    if first == last:
        date, doc, title, summary = days[0]
        print(f"{head} Debrief for {when}.\n")
        print(f"Summary: {title}." + (f" {summary}" if summary else ""))
        s = sessions(date)
        if s:
            print(f"\nSessions: {s}.")
        raw = open(os.path.join(debrief, doc), encoding="utf-8", errors="replace").read()
        parts = [ln[3:].strip() for ln in raw.splitlines() if ln.startswith("## ")]
        if parts:
            print("\nIts parts, in order: " + "; ".join(parts) + ".")
        print(f"\nNote {doc}:\n{raw}")
        return
    have = {d for d, *_ in days}
    gaps = []
    if (last - first).days < 31:
        every = (first + datetime.timedelta(days=i) for i in range((last - first).days + 1))
        gaps = [f"{d:%a %b} {d.day}" for d in every if d.isoformat() not in have]
    print(f"{head} Debrief for {when}: {len(days)} days with notes, newest first. "
          "Ask for one day for its full note.")
    if gaps:
        print("No notes (no coding sessions): " + ", ".join(gaps) + ".")
    for i, (date, doc, title, summary) in enumerate(days):
        if i >= 7 and summary:
            summary = re.split(r"(?<=[.!?])\s", summary, maxsplit=1)[0]
        print(f"\n{date} ({datetime.date.fromisoformat(date):%A}): {title}." + (f" {summary}" if summary else ""))


def cmd_when(debrief, db, args):
    """The days a name is in the notes, newest first, with how often."""
    today = datetime.date.today()
    head = today_line(today)
    # By the words, in every daily: a [[link]] counts (its hyphens match the
    # spaces), and so does plain prose, which names things without linking.
    words = [w for w in re.findall(r"[a-z0-9]+", args.name.lower()) if w]
    if not words:
        print("say what to look for")
        sys.exit(2)
    pat = re.compile(r"\b" + r"[\s_-]*".join(map(re.escape, words)) + r"\b", re.I)
    rows = []
    for date, doc in db.execute("SELECT date, doc FROM days ORDER BY date DESC").fetchall():
        raw = open(os.path.join(debrief, doc), encoding="utf-8", errors="replace").read()
        n = len(pat.findall(raw))
        if n:
            rows.append((date, n))
    if not rows:
        print(f"{head} No debrief note names {args.name!r}. A search also matches by meaning.")
        return
    shown = ", ".join(f"{d} ({datetime.date.fromisoformat(d):%a}) ×{n}" for d, n in rows[:args.limit])
    print(f"{head} {args.name!r} is in the notes on {len(rows)} days, newest first: {shown}. First: {rows[-1][0]}.")


def cmd_status(debrief, db, args):
    """Where a thing stands: its glossary entry (kept current), the days it
    was last worked on, and its open threads."""
    today = datetime.date.today()
    names = resolve_names(db, args.name)
    if not names:
        print(f"{today_line(today)} The glossary has no entry for {args.name!r}. Search the notes instead.")
        return
    name = names[0]
    print(today_line(today))
    if len(names) > 1:
        print(f"Closest entries: {', '.join(names[:6])}; this is [[{name}]].")
    row = db.execute("SELECT grp, summary FROM entities WHERE name=?", (name,)).fetchone()
    if row:
        print(f"\n[[{name}]] ({row[0]}), the glossary entry, which says where it stands now:\n{row[1]}")
    last = db.execute("SELECT m.date, d.title FROM mentions m JOIN days d ON d.date=m.date WHERE m.name=? "
                      "GROUP BY m.date ORDER BY m.date DESC LIMIT 3", (name,)).fetchall()
    if last:
        print("\nLast worked on: " + "; ".join(f"{d} ({t})" for d, t in last) + ".")
    # A later state often sits in another entry (a feature's default kept in
    # the entry of what uses it), so quote the sentences that name it there.
    words = [w for w in re.findall(r"[a-z0-9]+", name.replace("-", " ")) if len(w) > 2]
    key = words[0] if words else name
    pat = re.compile(r"\b" + re.escape(key) + r"\b", re.I)
    also = []
    for other, summary in db.execute("SELECT name, summary FROM entities WHERE name != ?", (name,)):
        # Sentences, with the citation links ("([2026-11-07 addendum](file.md))") taken out first, so a
        # date's dots and a file name never split one.
        plain = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", summary)
        hits = [x for x in re.split(r"(?<=[.;])\s+(?=[A-Z`*(])", plain) if pat.search(x)]
        if hits:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", summary)
            also.append((max(dates) if dates else "", other, " ".join(hits)[:500]))
    if also:
        print(f"\nOther entries that name it, newest first:")
        for _, other, text in sorted(also, reverse=True)[:4]:
            print(f"- [[{other}]]: {text}")
    todos = db.execute("SELECT text, opened FROM todos WHERE status='open' AND (',' || names || ',') LIKE ?",
                       (f"%,{name},%",)).fetchall()
    if todos:
        print(f"\nOpen threads ({len(todos)}):")
        for text, opened in todos[:8]:
            print(f"- {text[:600]}")
    else:
        print("\nNo open threads name it.")


def cmd_todos(debrief, db, args):
    """Open (or recently closed) threads, best match first for a topic."""
    status = "closed" if args.closed else "open"
    rows = db.execute("SELECT text, names, opened FROM todos WHERE status=?", (status,)).fetchall()
    total = len(rows)
    topic = " ".join(args.topic or [])
    if topic:
        names = set(resolve_names(db, topic))
        words = [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if len(w) > 1]
        pat = re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b", re.I) if words else None
        def score(r):
            linked = 10 * len(names & set(filter(None, r[1].split(","))))
            return linked + (len(pat.findall(r[0])) if pat else 0)
        rows = [r for r in sorted(rows, key=lambda r: -score(r)) if score(r)]
    if not rows:
        print(f"No {status} threads match (of {total} {status}).")
        return
    shown = rows[:args.limit]
    print(f"{len(rows)} {status} threads" + (" match" if topic else "") + f" (of {total})"
          + (f"; the first {len(shown)}:" if len(rows) > len(shown) else ":"))
    for text, _, opened in shown:
        print(f"- {text}")


ENTRY = re.compile(r"^#{2,3}\s*★")


def cmd_stars(db, args):
    """Print the starred entries — no query, because recall of a must-know
    list should never depend on guessing its wording.

    Entries only: a `## ★`/`### ★` heading is what marks one. HIGHLIGHTS.md
    also carries the layer's own explanation (how to star, the two rules,
    recall), which is scaffolding — printing it would bury the actual list
    every single time and make the cheap-recall path not cheap.
    """
    rows = db.execute(
        "SELECT rowid, section, text FROM chunks WHERE kind='highlight' ORDER BY rowid"
    ).fetchall()
    if not rows:
        print("no HIGHLIGHTS.md yet — `/debrief highlight <thing>` starts it")
        return
    # The template seeds worked examples marked *(example)*: not the owner's.
    entries = [r for r in rows if ENTRY.match(r[2]) and "*(example)*" not in r[2]]
    if not entries:
        print(
            "HIGHLIGHTS.md has no ★ entries yet "
            "(only the template) — `/debrief highlight <thing>` adds the first"
        )
        return
    for rowid, section, text in entries:
        print(f"[{rowid}] {text.strip()}\n")
    print(f"({len(entries)} starred)")


def cmd_get(db, args):
    for i, rowid in enumerate(args.ids):
        row = db.execute(
            "SELECT doc, section, text FROM chunks WHERE rowid=?", (rowid,)
        ).fetchone()
        if not row:
            print(f"[{rowid}] not found (index may have rebuilt — re-run search)")
            continue
        if i:
            print("\n" + "─" * 60 + "\n")
        if not args.context:
            print(f"[{rowid}] {row[0]} §{row[1]}\n\n{row[2]}")
            continue
        # -C N: the target plus its in-document neighbors, in document order.
        # Rowids ascend in insertion order and open_index inserts per-doc
        # sequentially, so ORDER BY rowid IS document order. Neighbors come
        # only from the same already-curated doc — a fraction of the tokens
        # of Read-ing the whole file when a hit lands mid-document.
        siblings = db.execute(
            "SELECT rowid, section, text FROM chunks "
            "WHERE doc=(SELECT doc FROM chunks WHERE rowid=?) ORDER BY rowid",
            (rowid,),
        ).fetchall()
        pos = next(j for j, s in enumerate(siblings) if s[0] == rowid)
        lo, hi = max(0, pos - args.context), pos + args.context + 1
        print(f"[{rowid}] {row[0]} (±{args.context} sections)")
        for rid, section, text in siblings[lo:hi]:
            mark = "▶" if rid == rowid else " "
            print(f"\n{mark} §{section}\n\n{text}")


# ---------------------------------------------------------------- selftest


def cmd_selftest():
    """Pass/fail invariants over a synthetic corpus — never a score.

    The central assertion is the curation gate itself: machine drafts must be
    invisible to search. A recall number would become a target and invite
    indexing more content to move it; invariants defend the design instead.
    """
    import tempfile

    failures = []

    def check(name, ok, detail=""):
        print(f"{'ok' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "sessions", "2026-01-05"))
        os.makedirs(os.path.join(td, "sessions", "archive", "2026-01-04"))
        w = lambda rel, s: open(os.path.join(td, rel), "w").write(s)
        w(
            "2026-01-05-payments.md",
            "---\ncommits: abc1234\n---\n\n# Payments retry loop\n\n"
            "## What happened\n\nThe payments retry loop double-charged.\n\n"
            "## Open threads\n\nBackoff still unbounded.\n",
        )
        w("INDEX.md", "# Index\n\n## Glossary\n\n- [[retry-loop]] — see 01-05.\n")
        w(
            "HIGHLIGHTS.md",
            "# Highlights\n\n## How to star something\n\nPut a ★ on the line.\n\n"
            "## ★ Derive the idempotency key\n\n"
            "A key minted inside the payments retry path is new every attempt.\n",
        )
        w(
            "sessions/2026-01-05/1200-curated.md",
            "---\nstatus: curated\n---\n\n# Curated note\n\nWebhook secret rotated.\n\n"
            "## Board\n\nBoard temperature stayed high.\n",
        )
        w(
            "sessions/2026-01-05/1300-draft.md",
            "---\nstatus: machine-draft\n---\n\n# Draft\n\nzanzibar unverified claim.\n",
        )
        w(
            "sessions/archive/2026-01-04/1100-old.md",
            "---\nstatus: machine-draft\n---\n\n# Old draft\n\nzanzibar again.\n",
        )
        # Living surfaces stay out of the index by construction (top-level,
        # undated): a stale open item or a 1-daily watching entry surfacing in
        # search would misreport current state. Plant markers to prove it.
        w("TODO.md", "# Todo\n\n## Open\n\n- [ ] xylophone thread still open.\n")
        w(
            "OBSERVATIONS.md",
            "# Observations\n\n## Watching\n\n### Watching: quagga pattern\n\n"
            "— status: watching · 1 daily\n",
        )

        docs = {rel for rel, _, _ in collect(td)}
        check(
            "gate: exactly curated corpus indexed",
            docs == {
                "2026-01-05-payments.md", "INDEX.md", "HIGHLIGHTS.md",
                "sessions/2026-01-05/1200-curated.md",
            },
            f"got {sorted(docs)}",
        )

        secs = [s for s, _ in chunk(open(os.path.join(td, "2026-01-05-payments.md")).read(), "")]
        check(
            "chunking: sections split, frontmatter stripped",
            secs == ["Payments retry loop", "What happened", "Open threads"],
            f"got {secs}",
        )

        db = open_index(td)
        top = db.execute(
            "SELECT doc FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks, 2.0, 1.0, 0.0, 3.0, 1.0) LIMIT 1",
            ('"payments" AND "retry"',),
        ).fetchone()
        check("retrieval: planted query ranks planted doc first", top and top[0] == "2026-01-05-payments.md")

        rt = db.execute("SELECT text FROM chunks WHERE section='Open threads'").fetchone()
        check("retrieval: get round-trips text", rt and "Backoff" in rt[0])

        raw_q, safe_q = fts_query('un"balanced')
        try:
            db.execute("SELECT rowid FROM chunks WHERE chunks MATCH ?", (raw_q,)).fetchall()
        except sqlite3.OperationalError:
            pass
        junk_ok = True
        try:
            db.execute("SELECT rowid FROM chunks WHERE chunks MATCH ?", (safe_q,)).fetchall()
        except sqlite3.OperationalError:
            junk_ok = False
        check("fallback: malformed query survives via quoted-AND", junk_ok)

        leaked = db.execute(
            "SELECT doc FROM chunks WHERE chunks MATCH 'zanzibar'"
        ).fetchall()
        check("gate: draft-only content unsearchable", leaked == [], f"leaked {leaked}")

        living = db.execute(
            "SELECT doc FROM chunks WHERE chunks MATCH 'xylophone OR quagga'"
        ).fetchall()
        check(
            "gate: living surfaces (TODO, OBSERVATIONS) unsearchable",
            living == [],
            f"leaked {living}",
        )

        meta_hit = db.execute(
            "SELECT doc FROM chunks WHERE chunks MATCH 'daily'"
        ).fetchall()
        check("schema: 'daily' cannot match via kind metadata", meta_hit == [], f"hit {meta_hit}")

        # The starred layer's promise is that it leads when it matches AT ALL —
        # so the test has to be the hard case, not the easy one. "payments"
        # appears in the daily's filename AND its H1 (doc=2.0, section=3.0,
        # plus the 1.15 daily multiplier); an earlier multiplier-based boost
        # passed a synthetic test where the term lived only in HIGHLIGHTS.md,
        # then lost to exactly this on the real corpus. Assert against the
        # strongest available competitor instead.
        star_top = db.execute(
            "SELECT doc, kind FROM chunks WHERE chunks MATCH ? "
            "ORDER BY CASE kind WHEN 'highlight' THEN 0 ELSE 1 END, "
            "bm25(chunks, 2.0, 1.0, 0.0, 3.0, 1.0) * "
            "CASE kind WHEN 'daily' THEN 1.15 WHEN 'index' THEN 1.1 ELSE 1.0 END "
            "LIMIT 1",
            ('"payments"',),
        ).fetchone()
        check(
            "starred: highlight leads even against a doc-title match",
            star_top and star_top[1] == "highlight",
            f"got {star_top}",
        )

        # ...and the ordering must not swallow the rest of the ranking: a term
        # absent from the starred layer still returns the best normal hit.
        normal_top = db.execute(
            "SELECT doc FROM chunks WHERE chunks MATCH ? "
            "ORDER BY CASE kind WHEN 'highlight' THEN 0 ELSE 1 END, "
            "bm25(chunks, 2.0, 1.0, 0.0, 3.0, 1.0) LIMIT 1",
            ('"backoff"',),
        ).fetchone()
        check(
            "starred: non-starred queries rank normally",
            normal_top and normal_top[0] == "2026-01-05-payments.md",
            f"got {normal_top}",
        )

        # `stars` prints entries, never the layer's own scaffolding.
        hl_rows = db.execute(
            "SELECT text FROM chunks WHERE kind='highlight'"
        ).fetchall()
        ents = [t for (t,) in hl_rows if ENTRY.match(t)]
        check(
            "starred: entry filter selects ★ headings only",
            len(ents) == 1 and "idempotency key" in ents[0],
            f"got {len(ents)} entries",
        )

        scoped = db.execute(
            "SELECT DISTINCT kind FROM chunks WHERE chunks MATCH ? AND kind='highlight'",
            ('"key"',),
        ).fetchall()
        check(
            "starred: --starred scope returns only highlights",
            all(k == "highlight" for (k,) in scoped),
            f"got {scoped}",
        )

        # Never pruned is a promise about content, but the retrieval-side
        # promise is that recall does not require guessing wording: `stars`
        # returns the layer whole, with no query at all.
        whole = db.execute(
            "SELECT COUNT(*) FROM chunks WHERE kind='highlight'"
        ).fetchone()
        check("starred: layer readable without a query", whole and whole[0] > 0)

        # ------------------------------------------------ semantic layer
        # A fake embedder: words hash into 64 buckets, after a synonym table
        # folds "thermal"/"heat" into "temperature". Deterministic, offline,
        # and enough to prove a meaning-only match can surface.
        syn = {"thermal": "temperature", "heat": "temperature", "hot": "temperature"}
        calls = []

        def fake_embed(texts):
            calls.append(len(texts))
            out = []
            for t in texts:
                body = re.split(r"\| (?:text|query): ", t)[-1]
                v = [0.0] * 64
                for w in re.findall(r"[a-z]+", body.lower()):
                    w = syn.get(w, w)
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
                out.append(v)
            return out

        u = unit([3.0, 4.0, 12.0], 2)
        check("semantic: MRL truncation keeps dims and re-normalizes",
              len(u) == 2 and abs(math.hypot(*u) - 1.0) < 1e-9, f"got {u}")

        plain = ranked(td, db, "payments", 5, recency=False)
        check("semantic: no embedder, no recency means exact keyword ranking",
              [r[0] for r, _, _ in plain] == [r[0] for r in keyword_rows(db, "payments", 5)])

        hits = ranked(td, db, "thermal", 5, embed=fake_embed, dim=64)
        check("semantic: meaning-only hit surfaces, marked sem",
              any(h == "sem" and "temperature" in db.execute(
                  "SELECT text FROM chunks WHERE rowid=?", (r[0],)).fetchone()[0]
                  for r, h, _ in hits),
              f"got {[(r[1], r[4], h) for r, h, _ in hits]}")

        n = len(calls)
        chunk_vectors(td, db, fake_embed, 64)
        check("semantic: cache means a re-run embeds nothing",
              len(calls) == n, f"{len(calls) - n} extra embed calls")

        # The vector universe must be exactly the gated chunk set: the
        # semantic layer may rank differently, never see more.
        vec_ids = set(chunk_vectors(td, db, fake_embed, 64))
        chunk_ids = {r for (r,) in db.execute("SELECT rowid FROM chunks")}
        check("gate: semantic vectors cover exactly the indexed chunks",
              vec_ids == chunk_ids and len(chunk_ids) > 0,
              f"{len(vec_ids)} vecs vs {len(chunk_ids)} chunks")

        star = ranked(td, db, "payments", 5, embed=fake_embed, dim=64)
        check("starred: keyword-matched highlight still leads with fusion on",
              star and star[0][0][3] == "highlight", f"got {star[:1]}")

        # Recency: two dailies match the same term equally; the newer leads.
        # Positive control: with recency off the older one (written first,
        # same bm25) must be able to lead, or the check proves nothing.
        w("2025-06-01-cache-old.md", "# Cache\n\n## Decision\n\nWe chose redis for the cache.\n")
        w("2026-01-04-cache-new.md", "# Cache\n\n## Decision\n\nWe chose redis for the cache.\n")
        db.close()
        db = open_index(td)
        today = datetime.date(2026, 1, 6)
        new = ranked(td, db, "redis", 5, recency=True, today=today)
        old = ranked(td, db, "redis", 5, recency=False, today=today)
        check("recency: newer of two equal matches ranks first",
              new and new[0][0][1] == "2026-01-04-cache-new.md", f"got {[r[1] for r, _, _ in new]}")
        check("recency: control, relevance-only order differs or ties on the old doc",
              old and {r[1] for r, _, _ in old[:2]} == {"2025-06-01-cache-old.md", "2026-01-04-cache-new.md"},
              f"got {[r[1] for r, _, _ in old]}")
        check("recency: undated layers never decay",
              recency_factor(None, today) == 1.0 and recency_factor("", today) == 1.0)
        check("recency: floor holds for very old chunks",
              abs(recency_factor("2000-01-01", today) - RECENCY_FLOOR) < 1e-6)

        # Supersession: a ledger line sinks the dead chunk below a weaker live
        # one, even with recency off, and names its replacement.
        w("SUPERSEDED.md", "# Superseded\n\n- 2026-01-04-cache-new.md § Decision → "
          "2026-01-05-payments.md — moved to memcached\n")
        dead = ranked(td, db, "redis", 5, recency=False, today=today)
        check("supersede: ledgered chunk sinks below the live one",
              dead and dead[0][0][1] == "2025-06-01-cache-old.md" and dead[-1][2]
              and dead[-1][2][0] == "2026-01-05-payments.md",
              f"got {[(r[1], d) for r, _, d in dead]}")
        w("SUPERSEDED.md", "- 2025-06-01-cache-old.md → 2026-01-04-cache-new.md\n")
        whole = ranked(td, db, "redis", 5, today=today)
        check("supersede: a line with no § invalidates every chunk of the doc",
              any(r[1] == "2025-06-01-cache-old.md" and d for r, _, d in whole),
              f"got {[(r[1], d) for r, _, d in whole]}")
        docs = {rel for rel, _, _ in collect(td)}
        check("gate: SUPERSEDED.md is a ledger, never indexed prose",
              "SUPERSEDED.md" not in docs)

        global EMBED_URL
        saved, EMBED_URL = EMBED_URL, "http://127.0.0.1:9"
        try:
            ollama_embed(["x"])
            down = False
        except EmbedUnavailable:
            down = True
        finally:
            EMBED_URL = saved
        check("fallback: unreachable embedder raises EmbedUnavailable", down)

        # A stalled embedder (takes the connection, never answers) costs a
        # search QUERY_TIMEOUT, not the fill timeout (2026-11-09).
        import socket
        import threading
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        held = []
        threading.Thread(target=lambda: held.append(srv.accept()), daemon=True).start()
        global QUERY_TIMEOUT
        saved_url, saved_q = EMBED_URL, QUERY_TIMEOUT
        EMBED_URL, QUERY_TIMEOUT = f"http://127.0.0.1:{srv.getsockname()[1]}", 0.5
        started = time.monotonic()
        try:
            query_vectors(td, ollama_embed, [query_prompt("stall")], 64)
            stalled = False
        except EmbedUnavailable:
            stalled = True
        finally:
            EMBED_URL, QUERY_TIMEOUT = saved_url, saved_q
            srv.close()
        took = time.monotonic() - started
        check(f"stall: a silent embedder fails the query in QUERY_TIMEOUT, took {took:.1f}s",
              stalled and took < 3)

        # Chunks past the in-search budget go to one background run, and the
        # search answers with the ones it has.
        fills = []
        saved_fill = globals()["fill_in_background"]
        globals()["fill_in_background"] = fills.append

        def slow_embed(texts):
            time.sleep(0.15)
            return fake_embed(texts)

        try:
            part = chunk_vectors(td, db, slow_embed, 32, batch=1, budget=0.2)
        finally:
            globals()["fill_in_background"] = saved_fill
        total = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        check(f"budget: a search embeds what fits ({len(part)} of {total}) and hands off the rest",
              0 < len(part) < total and fills == [td])

    # The structure tables (2026-11-09): dates, sessions, glossary, threads.
    import contextlib
    import io
    import types

    with tempfile.TemporaryDirectory() as td2:
        today = datetime.date.today()
        y = (today - datetime.timedelta(days=1)).isoformat()
        old = (today - datetime.timedelta(days=9)).isoformat()
        w2 = lambda rel, text: open(os.path.join(td2, rel), "w").write(text)
        w2(f"{y}-matcher.md",
           f"# {y} matcher\n\n## Sessions\n\n| Session | Project (cwd) | Ended | Shape |\n|---|---|---|---|\n"
           "| `aaaa1111` | `/home/me/eve` | 17:24 | bug fix |\n\n## What happened\n\nBuilt the "
           "[[spoken-names]] matcher for the [[voice-pe]].\n\n"
           "## Late-day addendum — session `bbbb2222` (portfolio, ended 19:13)\n\nLinks for agents.\n")
        w2(f"{old}-voice.md", f"# {old} voice\n\n## What happened\n\nThe Voice PE got louder, voice-pe.\n")
        w2("INDEX.md",
           "# Index\n\n## Debriefs\n\n| Date | Debrief | TL;DR | Key |\n|---|---|---|---|\n"
           f"| {y} | [Matcher day]({y}-matcher.md) | Built the matcher; portfolio links. | x |\n"
           "| 2020-01-04 | [Template row](none.md) | An example. | x |\n\n"
           "## Glossary\n\n### Systems\n\n- `[[kokoro-voice]]` — local voice, opt-in since the reversal.\n"
           "- `[[phone-call]]` — the call; the default is aura again, kokoro is opt-in.\n\n"
           "- `[[voice-pe]]` — the desk speaker board.\n")
        w2("TODO.md", "# Todo\n\n## Open\n\n- [ ] `[[voice-pe]]`: hear the limiter. — opened 2026-11-07\n"
           "- [ ] `[[circles]]`: check the map. — opened 2026-10-29\n\n## Closed (recent)\n\n- [x] done thing\n")
        db2 = open_index(td2)

        def run(fn, **kw):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                fn(td2, db2, types.SimpleNamespace(**kw))
            return out.getvalue()

        got = run(cmd_day, date="yesterday", until=None)
        check("structure: yesterday reads that day's summary, sessions and parts before the note",
              f"Debrief for {y}" in got and got.index("Built the matcher") < got.index("Note ")
              and "eve, ended 17:24: bug fix (aaaa1111)" in got and "portfolio, ended 19:13" in got
              and "Its parts, in order: Sessions; What happened; Late-day addendum" in got, got[:300])
        got = run(cmd_day, date=old, until="today")
        check("structure: a span lists days with notes, never template rows, and names empty days",
              "2 days with notes" in got and "Template row" not in got and "No notes" in got, got[:300])
        got = run(cmd_day, date=(today - datetime.timedelta(days=3)).isoformat(), until=None)
        check("structure: an empty day says so and names the nearest", "No debrief notes" in got
              and y in got and old in got, got)
        got = run(cmd_when, name="Voice PE", limit=12)
        check("structure: when counts links and prose, newest first",
              "on 2 days" in got and got.index(y) < got.index(old), got)
        got = run(cmd_status, name="kokoro")
        check("structure: status quotes the entry and the later state kept in another entry",
              "[[kokoro-voice]]" in got and "default is aura again" in got, got)
        got = run(cmd_todos, topic=["voice", "pe"], closed=False, limit=12)
        check("structure: todos keep the open threads about a topic",
              "hear the limiter" in got and "check the map" not in got and "done thing" not in got, got)
        span = resolve_span("last week", datetime.date(2026, 11, 9))
        check("structure: last week is the Monday-to-Sunday before",
              span == (datetime.date(2026, 11, 2), datetime.date(2026, 11, 8)))
        check("structure: a weekday is its most recent past day, never today",
              resolve_span("monday", datetime.date(2026, 11, 9))[0] == datetime.date(2026, 11, 2))
        names = resolve_names(db2, "voice pe")
        check("structure: a spoken name finds its glossary entry", names[:1] == ["voice-pe"], names)
        db2.close()

    sys.exit(1 if failures else 0)


def main():
    ap = argparse.ArgumentParser(prog="debrief-search")
    ap.add_argument("--dir", help="debrief directory (default: $DEBRIEF_DIR or auto)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="ranked compact index of matching chunks")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=8)
    s.add_argument(
        "--starred", action="store_true",
        help="search the ★ starred layer only (HIGHLIGHTS.md)",
    )
    s.add_argument(
        "--keyword", action="store_true",
        help="skip the EmbeddingGemma 2 semantic layer (bm25 only)",
    )
    s.add_argument(
        "--no-recency", action="store_true",
        help="rank by relevance alone, no age decay (superseded chunks still sink)",
    )
    st = sub.add_parser("stars", help="print the ★ starred layer whole (no query)")
    st.add_argument("-n", "--limit", type=int, default=0, help="unused; kept for symmetry")
    g = sub.add_parser("get", help="full text for chosen ids")
    g.add_argument("ids", nargs="+", type=int)
    g.add_argument(
        "-C", "--context", type=int, default=0,
        help="also print N neighboring sections from the same doc",
    )
    sub.add_parser("selftest", help="pass/fail invariants over a synthetic corpus")
    sub.add_parser("embed", help="embed every chunk the cache lacks (searches start this in the background)")
    d = sub.add_parser("day", help="a day or span by date: today, yesterday, a weekday, last week, YYYY-MM-DD…")
    d.add_argument("date")
    d.add_argument("--until", help="a range's last day")
    w = sub.add_parser("when", help="the days a project or thing is in the notes, newest first")
    w.add_argument("name")
    w.add_argument("-n", "--limit", type=int, default=12)
    stt = sub.add_parser("status", help="where a project or thing stands: glossary entry, last days, open threads")
    stt.add_argument("name")
    td_ = sub.add_parser("todos", help="open threads (or --closed), best match first for a topic")
    td_.add_argument("topic", nargs="*")
    td_.add_argument("--closed", action="store_true")
    td_.add_argument("-n", "--limit", type=int, default=12)
    args = ap.parse_args()
    if args.cmd == "selftest":
        cmd_selftest()  # needs no real corpus; exits
    debrief = find_debrief_dir(args.dir)
    db = open_index(debrief)
    if args.cmd == "search":
        cmd_search(debrief, db, args)
    elif args.cmd == "embed":
        cmd_embed(debrief, db)
    elif args.cmd in ("day", "when", "status", "todos"):
        {"day": cmd_day, "when": cmd_when, "status": cmd_status, "todos": cmd_todos}[args.cmd](debrief, db, args)
    else:
        {"get": cmd_get, "stars": cmd_stars}[args.cmd](db, args)


if __name__ == "__main__":
    main()
