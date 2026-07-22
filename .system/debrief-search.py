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
"""

import argparse
import os
import re
import sqlite3
import sys

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
SCHEMA_V = "v2"  # bump when SCHEMA or ranking changes: forces cached dbs to rebuild


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


# ---------------------------------------------------------------- commands


def fts_query(q):
    """Pass advanced FTS syntax through; fall back to quoted-AND on junk."""
    return q, " AND ".join(f'"{t}"' for t in re.findall(r"[\w-]+", q))


def cmd_search(db, args):
    raw_q, safe_q = fts_query(args.query)
    sql = (
        "SELECT rowid, doc, date, kind, section, "
        "snippet(chunks, 4, '«', '»', '…', 14) "
        "FROM chunks WHERE chunks MATCH ? "
        # Curation-aware weights (bm25 is negative, lower = better; one
        # hardcoded vector, no tunables). doc=2.0 / section=3.0: filename
        # slugs and headings are the most distilled human signal. kind=0.0
        # holds the UNINDEXED column's position. The CASE multiplier is a
        # mild layer preference — dailies are the reviewed layer, INDEX the
        # current-understanding layer — sized to break ties, never to
        # override a strong body match in a curated session note.
        "ORDER BY bm25(chunks, 2.0, 1.0, 0.0, 3.0, 1.0) * "
        "CASE kind WHEN 'daily' THEN 1.15 WHEN 'index' THEN 1.1 ELSE 1.0 END "
        "LIMIT ?"
    )
    try:
        rows = db.execute(sql, (raw_q, args.limit)).fetchall()
    except sqlite3.OperationalError:
        rows = db.execute(sql, (safe_q, args.limit)).fetchall()
    if not rows:
        print("no hits — try fewer/other terms (index covers curated memory only)")
        return
    for rowid, doc, date, kind, section, snip in rows:
        snip = " ".join(snip.split())
        label = date or kind
        print(f"[{rowid}] {label} {doc} §{section} — {snip}")
    print(f"\n({len(rows)} hits; `get <id>` for full text)")


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
            "sessions/2026-01-05/1200-curated.md",
            "---\nstatus: curated\n---\n\n# Curated note\n\nWebhook secret rotated.\n",
        )
        w(
            "sessions/2026-01-05/1300-draft.md",
            "---\nstatus: machine-draft\n---\n\n# Draft\n\nzanzibar unverified claim.\n",
        )
        w(
            "sessions/archive/2026-01-04/1100-old.md",
            "---\nstatus: machine-draft\n---\n\n# Old draft\n\nzanzibar again.\n",
        )

        docs = {rel for rel, _, _ in collect(td)}
        check(
            "gate: exactly curated corpus indexed",
            docs == {"2026-01-05-payments.md", "INDEX.md", "sessions/2026-01-05/1200-curated.md"},
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

        meta_hit = db.execute(
            "SELECT doc FROM chunks WHERE chunks MATCH 'daily'"
        ).fetchall()
        check("schema: 'daily' cannot match via kind metadata", meta_hit == [], f"hit {meta_hit}")

    sys.exit(1 if failures else 0)


def main():
    ap = argparse.ArgumentParser(prog="debrief-search")
    ap.add_argument("--dir", help="debrief directory (default: $DEBRIEF_DIR or auto)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="ranked compact index of matching chunks")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=8)
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
    db = open_index(find_debrief_dir(args.dir))
    (cmd_search if args.cmd == "search" else cmd_get)(db, args)


if __name__ == "__main__":
    main()
