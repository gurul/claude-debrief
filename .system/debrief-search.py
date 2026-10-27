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
"""

import argparse
import array
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
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
"""
SCHEMA_V = "v3"  # bump when SCHEMA or ranking changes: forces cached dbs to rebuild


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
    fp = SCHEMA_V + "|" + corpus_fingerprint(debrief, docs)
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
    os.path.exists(dbpath) and os.remove(dbpath)
    db = sqlite3.connect(dbpath)
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
    db.execute("INSERT INTO meta VALUES ('fp', ?)", (fp,))
    db.commit()
    print(f"(reindexed: {len(docs)} docs, {n} chunks)", file=sys.stderr)
    return db


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
SEM_MIN = 0.03
RRF_K = 60  # reciprocal-rank fusion constant (the standard one, untuned)


class EmbedUnavailable(Exception):
    pass


def unit(v, dim):
    """Matryoshka truncation: keep the leading dims, then re-normalize."""
    v = v[:dim]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def ollama_embed(texts):
    """Embed texts through Ollama's /api/embed. stdlib only."""
    body = json.dumps({"model": EMBED_MODEL, "input": texts, "truncate": True,
                       "keep_alive": EMBED_KEEP_ALIVE})
    req = urllib.request.Request(
        EMBED_URL + "/api/embed", body.encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise EmbedUnavailable(f"{EMBED_MODEL}: HTTP {e.code} {e.read()[:120]!r}")
    except (urllib.error.URLError, OSError) as e:
        raise EmbedUnavailable(f"no embedder at {EMBED_URL} ({e})")
    vecs = out.get("embeddings")
    if not vecs or len(vecs) != len(texts):
        raise EmbedUnavailable(f"{EMBED_MODEL}: bad response")
    return vecs


# EmbeddingGemma 2's retrieval prompts (model card, "SearchQuery" task).
def doc_prompt(doc, section, text):
    return f"title: {section} ({doc}) | text: {text}"


def query_prompt(q):
    return f"task: search result | query: {q}"


def chunk_vectors(debrief, db, embed, dim=None, batch=16):
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
    for i in range(0, len(missing), batch):
        part = missing[i : i + batch]
        for (k, _), v in zip(part, embed([p for _, p in part])):
            blob = array.array("f", unit(v, dim)).tobytes()
            cache.execute("INSERT OR REPLACE INTO vec VALUES (?, ?)", (k, blob))
            have[k] = blob
        cache.commit()
    if missing:
        print(f"(embedded {len(missing)} new chunks with {EMBED_MODEL})", file=sys.stderr)
    cache.close()
    return {rowid: array.array("f", have[k]) for rowid, k, _ in keyed}


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
    vecs = chunk_vectors(debrief, db, embed, dim)
    null = unit(embed([query_prompt("")])[0], dim)
    if kinds:
        keep = {r for (r,) in db.execute(
            "SELECT rowid FROM chunks WHERE kind IN (%s)" % ",".join("?" * len(kinds)),
            list(kinds),
        )}
        vecs = {r: v for r, v in vecs.items() if r in keep}
    qv = unit(embed([query_prompt(query)])[0], dim)
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


def ranked(debrief, db, query, limit, starred=False, embed=None, dim=None):
    """[(row, how)] where how is 'kw', 'both' or 'sem'.

    No embedder: exactly the keyword ranking. With one, reciprocal-rank
    fusion of the bm25 list and the cosine list. Fusion uses ranks, not raw
    scores, so the two scales never need calibrating against each other.
    The ★ sort key still holds for every keyword-matched highlight. A
    highlight found by meaning alone ranks by fused score, because "matches
    at all" is not a meaningful test for a similarity that is never zero.
    """
    pool = max(limit * 4, 40)
    kw = keyword_rows(db, query, pool if embed else limit, starred)
    if not embed:
        return [(r, "kw") for r in kw[:limit]]
    kinds = ["highlight"] if starred else None
    sem = semantic_top(debrief, db, query, pool, embed, kinds, dim)
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
    star_first = lambda r: 0 if rows[r][3] == "highlight" and how[r] != "sem" else 1
    order = sorted(score, key=lambda r: (star_first(r), -score[r]))
    return [(rows[r], how[r]) for r in order[:limit]]


def cmd_search(debrief, db, args):
    embed = None
    if not args.keyword and os.environ.get("DEBRIEF_EMBED", "on").lower() not in ("0", "off", "no"):
        embed = ollama_embed
    try:
        hits = ranked(debrief, db, args.query, args.limit, args.starred, embed)
    except EmbedUnavailable as e:
        print(f"(semantic off: {e}; keyword only)", file=sys.stderr)
        hits = ranked(debrief, db, args.query, args.limit, args.starred, None)
    if not hits:
        hint = "no starred entries match" if args.starred else \
            "no hits — try fewer/other terms (index covers curated memory only)"
        print(hint)
        return
    for (rowid, doc, date, kind, section, snip), how in hits:
        snip = " ".join(snip.split())
        label = date or kind
        star = "★ " if kind == "highlight" else ""
        # "~": matched by meaning only — no query term is in the chunk.
        mark = "~" if how == "sem" else ""
        print(f"[{rowid}]{mark} {star}{label} {doc} §{section} — {snip}")
    legend = "; ~ = meaning match, no keyword" if any(h == "sem" for _, h in hits) else ""
    print(f"\n({len(hits)} hits; `get <id>` for full text{legend})")


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
    entries = [r for r in rows if ENTRY.match(r[2])]
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

        plain = ranked(td, db, "payments", 5)
        check("semantic: no embedder means exact keyword ranking",
              [r[0] for r, _ in plain] == [r[0] for r in keyword_rows(db, "payments", 5)])

        hits = ranked(td, db, "thermal", 5, embed=fake_embed, dim=64)
        check("semantic: meaning-only hit surfaces, marked sem",
              any(h == "sem" and "temperature" in db.execute(
                  "SELECT text FROM chunks WHERE rowid=?", (r[0],)).fetchone()[0]
                  for r, h in hits),
              f"got {[(r[1], r[4], h) for r, h in hits]}")

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
    st = sub.add_parser("stars", help="print the ★ starred layer whole (no query)")
    st.add_argument("-n", "--limit", type=int, default=0, help="unused; kept for symmetry")
    g = sub.add_parser("get", help="full text for chosen ids")
    g.add_argument("ids", nargs="+", type=int)
    g.add_argument(
        "-C", "--context", type=int, default=0,
        help="also print N neighboring sections from the same doc",
    )
    sub.add_parser("selftest", help="pass/fail invariants over a synthetic corpus")
    args = ap.parse_args()
    if args.cmd == "selftest":
        cmd_selftest()  # needs no real corpus; exits
    debrief = find_debrief_dir(args.dir)
    db = open_index(debrief)
    if args.cmd == "search":
        cmd_search(debrief, db, args)
    else:
        {"get": cmd_get, "stars": cmd_stars}[args.cmd](db, args)


if __name__ == "__main__":
    main()
