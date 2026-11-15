#!/usr/bin/env python3
"""debrief-react — how a piece of work landed with the human, in his words.

An agent inside a session knows things a drafter reading the transcript later
has to guess: which piece the words were about, and where its before and after
live. So the agent records the reaction when it happens, and the session-end
hook checks it against the transcript:

    sessions/reactions/
      inbox.jsonl          recorded in a session, not yet checked
      <sid8>.jsonl         checked against that session's transcript
      unmatched.jsonl      older than 48 hours and no session claimed it
      .lock                taken by every writer

The words are the record. A record counts as the human's words only when
every fragment of them is found in a real human turn of the session it came
from: verification is a string match, never a judgment. The sentiment and
kind labels are the recording agent's reading and are always shown as that.

Rules:
  * Append-only. A change of mind is a new record. Verification moves a
    record out of the inbox and adds fields; it never edits the words.
  * Text is redacted by redact-transcript.py before it lands. No redactor
    means nothing is written (fail closed).
  * The transcript format is internal and unstable. The walk needs no field
    to be present, and a line that does not parse is skipped.
  * Evidence, not memory: debrief-search.py never indexes this folder.

Usage:
    debrief-react.py record --words "<exact words>" --about "<piece>" --sentiment positive|negative|mixed|neutral
                            [--kind K] [--before P] [--after P] [--project N] [--note "<agent's context>"] [--dir D]
    debrief-react.py verify --transcript <path.jsonl[.gz]> --session <id> [--dir D]
    debrief-react.py list [topic] [--project P] [--sentiment S] [--since YYYY-MM-DD] [--all] [--dir D]
    debrief-react.py summary [--project P] [--since YYYY-MM-DD] [--dir D]
    debrief-react.py --selftest
"""

import argparse
import fcntl
import gzip
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

SENTIMENTS = ("positive", "negative", "mixed", "neutral")
KINDS = ("kept", "changed", "rejected", "returned-to", "praise", "frustration",
         "correction", "preference", "question")
SLACK = timedelta(minutes=10)
ORPHAN_AGE = timedelta(hours=48)
LABEL_NOTE = "sentiment and kind are the recording agent's reading; the words are the record"

# Text the harness injects into a user turn. Same set as extract-material.py.
INJECTED = (
    "<system-reminder>", "Base directory for this skill", "<local-command", "<command-",
    "<task-notification", "This session is being continued", "Caveat:", "[Request interrupted",
)
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "«": '"', "»": '"',
})


class Refused(Exception):
    pass


def load_redactor(here):
    """The sibling redactor's redact(text) -> (text, counts), or None."""
    p = os.path.join(here, "redact-transcript.py")
    try:
        spec = importlib.util.spec_from_file_location("redact_transcript", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.redact("probe")
        return mod.redact
    except Exception:
        return None


HERE = os.path.dirname(os.path.abspath(__file__))


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rdir(base):
    return os.path.join(base, "sessions", "reactions")


class Lock:
    """One lock for every writer. Verify replaces the inbox, so a lock held on
    the inbox's own handle would let a writer append to an unlinked file."""

    def __init__(self, base):
        os.makedirs(rdir(base), exist_ok=True)
        self.path = os.path.join(rdir(base), ".lock")

    def __enter__(self):
        self.fh = open(self.path, "a")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def read_jsonl(path):
    """(records, lines that did not parse). A bad line is kept, never dropped."""
    recs, bad = [], []
    if not os.path.exists(path):
        return recs, bad
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                bad.append(ln if ln.endswith("\n") else ln + "\n")
                continue
            if isinstance(d, dict):
                recs.append(d)
            else:
                bad.append(ln if ln.endswith("\n") else ln + "\n")
    return recs, bad


def append(path, recs):
    if not recs:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def rewrite(path, recs, bad=()):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.writelines(bad)
    os.replace(tmp, path)


def default_project(cwd):
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return os.path.basename(r.stdout.strip())
    except Exception:
        pass
    return os.path.basename(os.path.normpath(cwd)) or cwd


# ---------------------------------------------------------------- record


def record(base, words, about, sentiment, kind=None, before=None, after=None, project=None,
           note=None, cwd=None, at=None, session_hint=None, redact="load"):
    if redact == "load":
        redact = load_redactor(HERE)
    if redact is None:
        raise Refused("redact-transcript.py did not load next to this script; nothing written")
    words = (words or "").strip()
    if not words:
        raise Refused("empty words; record the human's exact words")
    about = (about or "").strip()
    if not about:
        raise Refused("empty --about; name the piece or thing the words were about")
    if sentiment not in SENTIMENTS:
        raise Refused("sentiment must be one of: " + ", ".join(SENTIMENTS))
    if kind is not None and kind not in KINDS:
        raise Refused("kind must be one of: " + ", ".join(KINDS))
    cwd = cwd or os.getcwd()
    rec = {
        "id": uuid.uuid4().hex[:12],
        "at": iso(at or now_utc()),
        "words": words,
        "about": about,
        "sentiment": sentiment,
        "kind": kind,
        "before": before,
        "after": after,
        "project": project or default_project(cwd),
        "agent_note": note,
        "cwd": cwd,
        "verified": None,
    }
    if session_hint:
        rec["session_hint"] = session_hint
    for k in ("words", "about", "before", "after", "project", "agent_note", "cwd"):
        if isinstance(rec[k], str):
            rec[k], _ = redact(rec[k])
    with Lock(base):
        append(os.path.join(rdir(base), "inbox.jsonl"), [rec])
    return rec


# ---------------------------------------------------------------- verify


def norm(s):
    return re.sub(r"\s+", " ", s.translate(QUOTES)).strip()


def fragments(words):
    out = []
    for p in norm(words).split(" / "):
        p = p.strip().strip("\"'").strip()
        if p:
            out.append(p)
    return out


def human_texts(obj):
    """The human's own text in one transcript line, or []."""
    if not isinstance(obj, dict):
        return []
    # A message sent while the agent was working is queued, not a user turn.
    if obj.get("type") == "queue-operation" and obj.get("operation") == "enqueue":
        c = obj.get("content")
        return [c] if isinstance(c, str) else []
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return []
    role = msg.get("role") or obj.get("type")
    if not (obj.get("type") == "user" and role == "user"
            and not obj.get("isMeta") and not obj.get("isSidechain")):
        return []
    c = msg.get("content")
    if isinstance(c, str):
        return [c]
    if isinstance(c, list):
        return [b["text"] for b in c
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return []


def clean(text):
    text = REMINDER.sub("", text)
    s = text.lstrip()
    # A slash command the human typed: its arguments are his words, the rest is harness.
    if s.startswith("<command-"):
        return " ".join(COMMAND_ARGS.findall(text))
    if s.startswith(INJECTED):
        return ""
    return text


def read_transcript(path, redact):
    """{turns: [(line, normalised text)], first, last, cwds}."""
    t = {"turns": [], "first": None, "last": None, "cwds": set()}
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8", errors="replace") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            ts = parse_ts(obj.get("timestamp"))
            if ts:
                t["first"] = ts if t["first"] is None or ts < t["first"] else t["first"]
                t["last"] = ts if t["last"] is None or ts > t["last"] else t["last"]
            if isinstance(obj.get("cwd"), str):
                t["cwds"].add(os.path.normpath(obj["cwd"]))
            for txt in human_texts(obj):
                txt = clean(txt)
                if txt.strip():
                    # Recorded words were redacted, so compare against redacted turns.
                    t["turns"].append((n, norm(redact(txt)[0])))
    return t


def find(rec, turns):
    """(lines, missing fragments)."""
    frags = fragments(rec.get("words") or "")
    if not frags:
        return [], ["(no words)"]
    lines, missing = [], []
    for f in frags:
        hit = next((n for n, txt in turns if f in txt), None)
        if hit is None:
            missing.append(f)
        else:
            lines.append(hit)
    return lines, missing


def hint_match(hint, session):
    return bool(hint) and (session.startswith(hint) or hint.startswith(session))


def in_window(rec, t):
    at = parse_ts(rec.get("at"))
    if at is None or t["first"] is None:
        return False
    return t["first"] - SLACK <= at <= t["last"] + SLACK


def cwd_match(rec, t):
    c = rec.get("cwd")
    if not isinstance(c, str):
        return False
    c = os.path.normpath(c)
    return any(c == w or c.startswith(w.rstrip("/") + "/") for w in t["cwds"])


def settle(rec, session, lines, missing, now):
    out = dict(rec)
    out["session"] = session
    out["verified_at"] = iso(now)
    if missing:
        out["verified"] = False
        out["why"] = "not found in a human turn of this session: " + " / ".join('"%s"' % m for m in missing)
    else:
        out["verified"] = True
        out["line"] = lines[0]
        out["lines"] = lines
    return out


def verify(base, transcript, session, now=None, redact="load"):
    if redact == "load":
        redact = load_redactor(HERE)
    if redact is None:
        raise Refused("redact-transcript.py did not load; nothing checked")
    now = now or now_utc()
    t = read_transcript(transcript, redact)
    rd = rdir(base)
    counts = {"verified": 0, "not_found": 0, "unmatched": 0, "rescued": 0, "left": 0}
    with Lock(base):
        inbox_p = os.path.join(rd, "inbox.jsonl")
        un_p = os.path.join(rd, "unmatched.jsonl")
        sess_p = os.path.join(rd, "%s.jsonl" % session[:8])
        inbox, inbox_bad = read_jsonl(inbox_p)
        unmatched, un_bad = read_jsonl(un_p)
        done = {r.get("id") for r in read_jsonl(sess_p)[0]}
        to_sess, to_un, keep = [], [], []
        for rec in inbox:
            hint = rec.get("session_hint")
            if rec.get("id") in done:
                continue  # settled by a run that stopped before rewriting the inbox
            mine = hint_match(hint, session) or (not hint and in_window(rec, t) and cwd_match(rec, t))
            if mine:
                lines, missing = find(rec, t["turns"])
                # Without a session hint, a miss may belong to a session running
                # in parallel in the same folder. It waits for that one.
                if not missing or hint:
                    to_sess.append(settle(rec, session, lines, missing, now))
                    counts["verified" if not missing else "not_found"] += 1
                    continue
            at = parse_ts(rec.get("at"))
            if at is None or now - at > ORPHAN_AGE:
                o = dict(rec)
                o.update(verified=False, why="no session matched", verified_at=iso(now))
                to_un.append(o)
                counts["unmatched"] += 1
            else:
                keep.append(rec)
                counts["left"] += 1
        # A session that ran past 48 hours can still claim its early records.
        un_keep = []
        for rec in unmatched:
            hint = rec.get("session_hint")
            mine = hint_match(hint, session) or (not hint and in_window(rec, t) and cwd_match(rec, t))
            if mine and rec.get("id") not in done:
                lines, missing = find(rec, t["turns"])
                if not missing:
                    r = {k: v for k, v in rec.items() if k != "why"}
                    to_sess.append(settle(r, session, lines, missing, now))
                    counts["rescued"] += 1
                    continue
            un_keep.append(rec)
        if to_sess:
            append(sess_p, to_sess)
        if to_un:
            append(un_p, to_un)
        if len(un_keep) != len(unmatched):
            rewrite(un_p, un_keep, un_bad)
        if len(keep) != len(inbox):
            rewrite(inbox_p, keep, inbox_bad)
    return counts


# ---------------------------------------------------------------- read


def load_registry(base):
    try:
        d = json.load(open(os.path.join(base, ".system", "projects.json"), encoding="utf-8"))
    except Exception:
        return {"groups": {}, "aliases": {}}
    groups = {str(g).lower(): [str(m).lower() for m in ms]
              for g, ms in (d.get("groups") or {}).items() if isinstance(ms, list)}
    aliases = {str(a).lower(): str(t).lower() for a, t in (d.get("aliases") or {}).items()}
    return {"groups": groups, "aliases": aliases}


def canon(name, reg):
    n = (name or "").lower()
    return reg["aliases"].get(n, n)


def resolve(name, reg):
    """The set of project names a --project value stands for."""
    n = (name or "").lower()
    if "/" in n:
        g, m = n.split("/", 1)
        return {canon(m, reg)}
    n = canon(n, reg)
    if n in reg["groups"]:
        return {n} | {canon(m, reg) for m in reg["groups"][n]}
    return {n}


def group_of(project, reg):
    p = canon(project, reg)
    for g, ms in reg["groups"].items():
        if p == g or p in {canon(m, reg) for m in ms}:
            return g
    return None


def load_all(base, include_inbox):
    rd = rdir(base)
    if not os.path.isdir(rd):
        return []
    by_id = {}
    names = sorted(n for n in os.listdir(rd) if n.endswith(".jsonl") and n != "inbox.jsonl")
    if include_inbox:
        names.append("inbox.jsonl")
    for name in names:
        for r in read_jsonl(os.path.join(rd, name))[0]:
            k = r.get("id") or id(r)
            old = by_id.get(k)
            if old is None or (r.get("verified") is True and old.get("verified") is not True):
                by_id[k] = r
    return sorted(by_id.values(), key=lambda r: r.get("at") or "", reverse=True)


def select(recs, base, topic=None, project=None, sentiment=None, since=None, verified_only=True):
    reg = load_registry(base)
    want = resolve(project, reg) if project else None
    out = []
    for r in recs:
        if verified_only and r.get("verified") is not True:
            continue
        if want is not None and canon(r.get("project"), reg) not in want:
            continue
        if sentiment and r.get("sentiment") != sentiment:
            continue
        if since and (r.get("at") or "")[:10] < since:
            continue
        if topic:
            hay = " ".join(str(r.get(k) or "") for k in ("words", "about", "project", "agent_note", "kind")).lower()
            if not all(w in hay for w in topic.lower().split()):
                continue
        out.append(r)
    return out


def show(r):
    head = " · ".join(x for x in (
        (r.get("at") or "")[:10], r.get("project") or "?", r.get("about") or "?",
        "%s (agent's reading)" % r.get("sentiment"), r.get("kind") or "") if x)
    lines = []
    if r.get("verified") is not True:
        why = r.get("why") or "not yet verified"
        lines.append("[NOT CONFIRMED as the human's words: %s]" % why)
    lines.append(head)
    lines.append('  "%s"' % r.get("words"))
    if r.get("before") or r.get("after"):
        lines.append("  before: %s   after: %s" % (r.get("before") or "-", r.get("after") or "-"))
    if r.get("agent_note"):
        lines.append("  agent's note: %s" % r["agent_note"])
    where = "session %s" % (r.get("session") or "")[:8] if r.get("session") else "no session yet"
    if r.get("line"):
        where += " · transcript line %s" % r["line"]
    lines.append("  " + where)
    return "\n".join(lines)


def cmd_list(base, topic=None, project=None, sentiment=None, since=None, all_=False):
    recs = select(load_all(base, all_), base, topic, project, sentiment, since, verified_only=not all_)
    nv = sum(1 for r in recs if r.get("verified") is True)
    print("%d reactions (%d verified). %s." % (len(recs), nv, LABEL_NOTE.capitalize()))
    for r in recs:
        print()
        print(show(r))
    return recs


def week(at):
    dt = parse_ts(at)
    if not dt:
        return "?"
    y, w, _ = dt.isocalendar()
    return "%d-W%02d" % (y, w)


def table(title, rows):
    print(title)
    for key, c in sorted(rows.items()):
        print("  %-28s %s" % (key, "  ".join("%s %d" % (s, c.get(s, 0)) for s in SENTIMENTS)))


def tally(recs, key):
    rows = {}
    for r in recs:
        rows.setdefault(key(r), {}).setdefault(r.get("sentiment"), 0)
        rows[key(r)][r.get("sentiment")] += 1
    return rows


def cmd_summary(base, project=None, since=None):
    reg = load_registry(base)
    every = load_all(base, True)
    recs = select(every, base, project=project, since=since)
    pending = len(select(every, base, project=project, since=since, verified_only=False)) - len(recs)
    print("Verified reactions: %d. Not counted (unverified or pending): %d." % (len(recs), pending))
    print("Counts only. The sentiment label is the recording agent's guess; it is not a rule.")
    print()
    if project or not reg["groups"]:
        table("By project:", tally(recs, lambda r: r.get("project") or "?"))
    else:
        for g in sorted(reg["groups"]):
            sub = [r for r in recs if group_of(r.get("project"), reg) == g]
            if sub:
                table("Group %s:" % g, tally(sub, lambda r: r.get("project") or "?"))
        rest = [r for r in recs if group_of(r.get("project"), reg) is None]
        if rest:
            table("Ungrouped:", tally(rest, lambda r: r.get("project") or "?"))
    print()
    table("By week:", tally(recs, lambda r: week(r.get("at"))))
    for s in ("negative", "positive"):
        print()
        print("Most recent %s (agent's reading):" % s)
        top = [r for r in recs if r.get("sentiment") == s][:5]
        if not top:
            print("  none")
        for r in top:
            print('  %s · %s · %s — "%s"' % ((r.get("at") or "")[:10], r.get("project"), r.get("about"), r.get("words")))
    return recs


# ---------------------------------------------------------------- selftest


def selftest():
    import contextlib
    import io
    import shutil
    import tempfile

    fails = []

    def check(name, ok, detail=""):
        print(("ok " if ok else "FAIL ") + name + (": %s" % (detail,) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    def out_of(fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a, **kw)
        return buf.getvalue()

    redact = load_redactor(HERE)
    check("the sibling redactor loads", redact is not None)
    if redact is None:
        print("1 FAILED")
        sys.exit(1)
    now = now_utc()
    ts = lambda m: iso(now + timedelta(minutes=m))
    sid = "aaaa1111bbbb2222"

    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "store")
        os.makedirs(base)
        work = os.path.join(td, "proj")
        os.makedirs(work)
        U = lambda content, m, **kw: dict({"type": "user", "cwd": work, "timestamp": ts(m),
                                           "message": {"role": "user", "content": content}}, **kw)
        lines = [
            U("the header is “finally   calm”.\nI’d keep\n  it exactly", -30),
            {"type": "assistant", "cwd": work, "timestamp": ts(-29), "message": {"role": "assistant", "content": [
                {"type": "text", "text": "assistant says ochre banner sings"},
                {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"prompt": "subagent prompt says teal grid hums"}}]}},
            U([{"type": "tool_result", "tool_use_id": "a1", "content": [{"type": "text", "text": "tool result says violet card glows"}]}], -28),
            U("<system-reminder>reminder says amber footer rests</system-reminder>", -27),
            U("subagent prompt says sidechain words here", -26, isSidechain=True),
            U("skill text says meta words here", -25, isMeta=True),
            {"type": "api-request-blob", "message": {"role": "user", "content": "blob says crimson words"}},
            {"type": "queue-operation", "operation": "enqueue", "timestamp": ts(-20), "content": "too loud, drop the gradient"},
            U("<command-message>debrief-react</command-message>\n<command-name>/debrief-react</command-name>\n"
              "<command-args>I love the spacing on this one</command-args>", -15),
            U([{"type": "text", "text": "honestly the second version"}], -10),
        ]
        tp = os.path.join(td, "t.jsonl")
        with open(tp, "w") as f:
            for d in lines:
                f.write(json.dumps(d) + "\n")
            f.write("{not json\n")
        raw = open(tp).read()

        R = lambda words, **kw: record(base, words, kw.pop("about", "header"), kw.pop("sentiment", "positive"),
                                       cwd=kw.pop("cwd", work), project=kw.pop("project", "shop-web"),
                                       redact=redact, **kw)
        found = R('the header is "finally calm". I\'d keep it exactly', kind="kept",
                  before="sessions/material/aaaa1111/images/a.png", after="commit abc1234",
                  at=now - timedelta(minutes=20))
        found2 = R("too loud, drop the gradient", sentiment="negative", kind="rejected", project="shop-old")
        cmdargs = R("I love the spacing on this one", project="bot")
        multi_ok = R('"honestly" / "the second version"', sentiment="mixed", kind="returned-to", project="kiosk")
        negs = {}
        for label, w in (("assistant turn", "ochre banner sings"), ("tool result", "violet card glows"),
                         ("system reminder", "amber footer rests"), ("subagent prompt", "teal grid hums"),
                         ("sidechain turn", "sidechain words here"), ("meta skill text", "meta words here"),
                         ("request blob", "crimson words")):
            check("control: %s words are in the raw transcript" % label, w in raw)
            negs[label] = R(w, session_hint=sid, project="misc")
        multi_bad = R("honestly / never said this", session_hint=sid, project="misc")
        nohint_miss = R("words from a parallel session", project="misc")
        other_cwd = R("too loud, drop the gradient", cwd="/elsewhere/repo", project="misc")
        other_sess = R("too loud, drop the gradient", session_hint="ffff0000", project="misc")
        orphan = R("an old reaction", cwd="/elsewhere/old", project="misc", at=now - timedelta(hours=49))
        secret = "sk-ant-api03-" + "B" * 40
        red = R("finally", note="context with key %s inside" % secret, project="misc")

        inbox = os.path.join(rdir(base), "inbox.jsonl")
        stored = {r["id"]: r for r in read_jsonl(inbox)[0]}
        check("record: verified is null in the inbox", stored[found["id"]]["verified"] is None)
        check("record: words stored exactly", stored[found["id"]]["words"] == 'the header is "finally calm". I\'d keep it exactly')
        check("record: a key inside --note is redacted",
              secret not in open(inbox).read() and "[REDACTED:" in stored[red["id"]]["agent_note"])
        check("record: at is UTC", stored[found["id"]]["at"].endswith("Z"))
        try:
            R("   ")
            check("record: empty words refused", False)
        except Refused:
            check("record: empty words refused", True)
        try:
            record(base, "words", "x", "positive", cwd=work, redact=None)
            check("record: no redactor refused", False)
        except Refused:
            check("record: no redactor refused", True)

        before_lines = len(open(inbox).readlines())
        c = verify(base, tp, sid, now=now, redact=redact)
        sess = {r["id"]: r for r in read_jsonl(os.path.join(rdir(base), "aaaa1111.jsonl"))[0]}
        left = {r["id"] for r in read_jsonl(inbox)[0]}
        un = {r["id"]: r for r in read_jsonl(os.path.join(rdir(base), "unmatched.jsonl"))[0]}
        check("verify: exact words found, with session and line",
              sess.get(found["id"], {}).get("verified") is True and sess[found["id"]]["session"] == sid
              and sess[found["id"]]["line"] == 1, sess.get(found["id"]))
        check("verify: whitespace and curly quotes do not block a match", sess.get(found["id"], {}).get("verified") is True)
        check("verify: words sent mid-turn (queued) are found", sess.get(found2["id"], {}).get("verified") is True)
        check("verify: a slash command's arguments are the human's", sess.get(cmdargs["id"], {}).get("verified") is True)
        check("verify: every fragment found across turns", sess.get(multi_ok["id"], {}).get("verified") is True
              and len(sess[multi_ok["id"]]["lines"]) == 2)
        for label, r in negs.items():
            check("verify: words only in a %s are not verified" % label,
                  sess.get(r["id"], {}).get("verified") is False and "why" in sess[r["id"]], sess.get(r["id"]))
        check("verify: one missing fragment fails the record, and names it",
              sess.get(multi_bad["id"], {}).get("verified") is False and "never said this" in sess[multi_bad["id"]]["why"])
        check("verify: a miss without a session hint waits in the inbox", nohint_miss["id"] in left)
        check("verify: a record from another cwd stays in the inbox", other_cwd["id"] in left)
        check("verify: a record hinted to another session stays in the inbox", other_sess["id"] in left)
        check("verify: a 48-hour orphan goes to unmatched",
              un.get(orphan["id"], {}).get("why") == "no session matched" and un[orphan["id"]]["verified"] is False)
        check("verify: nothing lost", len(sess) + len(left) + len(un) == before_lines, (len(sess), len(left), len(un), before_lines))
        check("verify: counts", c["verified"] == 5 and c["not_found"] == 8 and c["unmatched"] == 1, c)

        snap = {n: open(os.path.join(rdir(base), n), "rb").read() for n in os.listdir(rdir(base)) if n.endswith(".jsonl")}
        c2 = verify(base, tp, sid, now=now, redact=redact)
        snap2 = {n: open(os.path.join(rdir(base), n), "rb").read() for n in os.listdir(rdir(base)) if n.endswith(".jsonl")}
        check("verify: a second run changes nothing", snap == snap2 and c2["verified"] == 0, c2)

        gzp = os.path.join(td, "t.jsonl.gz")
        with gzip.open(gzp, "wt") as f:
            f.write(raw)
        g = R("drop the gradient", project="misc")
        verify(base, gzp, "cccc3333dddd", now=now, redact=redact)
        gz = {r["id"]: r for r in read_jsonl(os.path.join(rdir(base), "cccc3333.jsonl"))[0]}
        check("verify: a .jsonl.gz transcript works", gz.get(g["id"], {}).get("verified") is True)

        # A session that runs past 48 hours still claims its early records.
        late = record(base, "the second version", "card", "positive", cwd=work, project="misc",
                      at=now - timedelta(hours=50), redact=redact)
        verify(base, tp, "eeee4444", now=now, redact=redact)
        long_t = os.path.join(td, "long.jsonl")
        with open(long_t, "w") as f:
            f.write(json.dumps(dict(lines[-1], timestamp=iso(now - timedelta(hours=51)))) + "\n")
            f.write(json.dumps(dict(lines[-1], timestamp=ts(0))) + "\n")
        verify(base, long_t, "eeee5555", now=now, redact=redact)
        res = {r["id"]: r for r in read_jsonl(os.path.join(rdir(base), "eeee5555.jsonl"))[0]}
        unl = {r["id"] for r in read_jsonl(os.path.join(rdir(base), "unmatched.jsonl"))[0]}
        check("verify: a long session rescues its record from unmatched",
              res.get(late["id"], {}).get("verified") is True and late["id"] not in unl and "why" not in res[late["id"]])

        # list and summary
        out = out_of(cmd_list, base)
        check("list: verified only by default", '"too loud, drop the gradient"' in out
              and "ochre banner" not in out and "NOT CONFIRMED" not in out)
        check("list: labels shown as the agent's reading", "(agent's reading)" in out)
        check("list: newest first", out.index("I love the spacing") < out.index("finally calm"))
        out = out_of(cmd_list, base, all_=True)
        check("list --all: unverified marked not confirmed", "ochre banner" in out and "NOT CONFIRMED" in out)
        check("list: sentiment filter", "too loud" in out_of(cmd_list, base, sentiment="negative")
              and "finally calm" not in out_of(cmd_list, base, sentiment="negative"))
        check("list: topic filter", "finally calm" in out_of(cmd_list, base, "calm")
              and "too loud" not in out_of(cmd_list, base, "calm"))
        check("list: since filter", "0 reactions" in out_of(cmd_list, base, since="2999-01-01"))

        # projects: no registry, then group, alias and group/member
        check("project: no registry matches the single name",
              "finally calm" in out_of(cmd_list, base, project="Shop-Web")
              and "too loud" not in out_of(cmd_list, base, project="shop-web"))
        check("project: no registry, alias does not resolve", "too loud" not in out_of(cmd_list, base, project="shop-web"))
        os.makedirs(os.path.join(base, ".system"), exist_ok=True)
        json.dump({"groups": {"studio": ["shop-web", "shop-ios", "kiosk"], "bot": ["bot"]},
                   "aliases": {"shop-old": "shop-web"}}, open(os.path.join(base, ".system", "projects.json"), "w"))
        o = out_of(cmd_list, base, project="Studio")
        check("project: a group is every member", "finally calm" in o and "honestly" in o and "too loud" in o
              and "I love the spacing" not in o, o)
        o = out_of(cmd_list, base, project="shop-old")
        check("project: an alias resolves to its target", "finally calm" in o and "too loud" in o and "honestly" not in o, o)
        o = out_of(cmd_list, base, project="studio/kiosk")
        check("project: group/member is the member", "honestly" in o and "finally calm" not in o, o)

        s = out_of(cmd_summary, base)
        check("summary: says the label is the agent's guess", "recording agent's guess" in s)
        check("summary: grouped, then ungrouped", s.index("Group bot:") < s.index("Group studio:") < s.index("Ungrouped:"), s)
        check("summary: words exact in recent negatives", '"too loud, drop the gradient"' in s)
        check("summary: counts by week", "By week:" in s and week(iso(now)) in s)
        s = out_of(cmd_summary, base, project="bot")
        check("summary: project filter", "Verified reactions: 1." in s and "too loud" not in s, s)

        # Concurrent records do not interleave.
        cbase = os.path.join(td, "conc")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        big = "x" * 30000
        procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "record", "--dir", cbase,
                                   "--words", "%d %s %d" % (i, big, i), "--about", "load", "--sentiment", "neutral",
                                   "--project", "p"], env=env, cwd=work, stdout=subprocess.DEVNULL)
                 for i in range(12)]
        rcs = [p.wait() for p in procs]
        recs, bad = read_jsonl(os.path.join(rdir(cbase), "inbox.jsonl"))
        check("concurrent: twelve whole records, no torn line",
              rcs == [0] * 12 and len(recs) == 12 and not bad
              and all(r["words"].split(" ")[0] == r["words"].split(" ")[-1] for r in recs), (rcs, len(recs), len(bad)))

        # The CLI with no sibling redactor refuses and writes nothing.
        lone = os.path.join(td, "lone")
        os.makedirs(lone)
        shutil.copy(os.path.abspath(__file__), lone)
        nb = os.path.join(td, "nobase")
        p = subprocess.run([sys.executable, os.path.join(lone, os.path.basename(__file__)), "record", "--dir", nb,
                            "--words", "w", "--about", "a", "--sentiment", "positive"],
                           env=env, cwd=work, capture_output=True, text=True)
        check("refuse: no redactor, non-zero exit and nothing written",
              p.returncode != 0 and "redact" in p.stderr and not os.path.exists(os.path.join(rdir(nb), "inbox.jsonl")),
              (p.returncode, p.stderr))
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "record", "--dir", nb,
                            "--words", "  ", "--about", "a", "--sentiment", "positive"],
                           env=env, cwd=work, capture_output=True, text=True)
        check("refuse: CLI empty words", p.returncode != 0)
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "verify", "--dir", nb,
                            "--transcript", os.path.join(td, "missing.jsonl"), "--session", "x"],
                           env=env, capture_output=True, text=True)
        check("verify from a hook never fails the session", p.returncode == 0 and not p.stderr, (p.returncode, p.stderr))

    print("ALL PASS" if not fails else "%d FAILED" % len(fails))
    sys.exit(1 if fails else 0)


# ---------------------------------------------------------------- cli


def main():
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    ap = argparse.ArgumentParser(prog="debrief-react", description="The human's reactions to work, in his words.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dflt = os.environ.get("DEBRIEF_DIR")

    r = sub.add_parser("record")
    r.add_argument("--words", required=True)
    r.add_argument("--about", required=True)
    r.add_argument("--sentiment", required=True, choices=SENTIMENTS)
    r.add_argument("--kind", choices=KINDS)
    r.add_argument("--before")
    r.add_argument("--after")
    r.add_argument("--project")
    r.add_argument("--note")
    r.add_argument("--dir", default=dflt)

    v = sub.add_parser("verify")
    v.add_argument("--transcript", required=True)
    v.add_argument("--session", required=True)
    v.add_argument("--dir", default=dflt)

    l = sub.add_parser("list")
    l.add_argument("topic", nargs="*")
    l.add_argument("--project")
    l.add_argument("--sentiment", choices=SENTIMENTS)
    l.add_argument("--since")
    l.add_argument("--all", action="store_true")
    l.add_argument("--dir", default=dflt)

    s = sub.add_parser("summary")
    s.add_argument("--project")
    s.add_argument("--since")
    s.add_argument("--dir", default=dflt)

    a = ap.parse_args()
    if a.cmd == "verify":
        # Called from the session-end hook: one line, and never a failure.
        try:
            if not a.dir:
                print("reactions: SKIPPED, no --dir and no DEBRIEF_DIR")
                return
            c = verify(a.dir, a.transcript, a.session)
            print("reactions: %s" % ", ".join("%d %s" % (n, k) for k, n in c.items() if n) or "reactions: none")
        except Exception as e:
            print("reactions: FAILED %s" % (str(e).splitlines() or [type(e).__name__])[0])
        return
    if not a.dir:
        sys.exit("debrief-react: no store. Pass --dir or set DEBRIEF_DIR.")
    if a.cmd == "record":
        try:
            rec = record(a.dir, a.words, a.about, a.sentiment, a.kind, a.before, a.after, a.project, a.note,
                         session_hint=os.environ.get("CLAUDE_CODE_SESSION_ID") or None)
        except Refused as e:
            sys.exit("debrief-react: refused: %s" % e)
        print("recorded %s: %s on %s (%s); checked against the transcript at session end."
              % (rec["id"], rec["sentiment"], rec["about"], rec["project"]))
    elif a.cmd == "list":
        cmd_list(a.dir, " ".join(a.topic) or None, a.project, a.sentiment, a.since, a.all)
    elif a.cmd == "summary":
        cmd_summary(a.dir, a.project, a.since)


if __name__ == "__main__":
    main()
