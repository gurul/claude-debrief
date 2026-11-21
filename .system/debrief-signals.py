#!/usr/bin/env python3
"""debrief-signals — what a session's transcript shows, read by rule, after it ends.

A reaction is recorded only when the agent in the session records it. This
pass reads every human turn afterwards, with no model, and leaves one file
per session:

    sessions/signals/<sid8>.json

It holds, for each of the human's turns: the transcript line, the text, the
recorded reactions that cover it, a sentence of his that reads as a standing
rule, and what the agent did just before he spoke (the last claim it made,
the checks that passed and failed). The drafter is given the turns no
reaction covers and may name the ones that judge a piece of work. `adopt`
then checks each of those against the human's own turns by string match, the
same check a recorded reaction gets, and only then adds it to the record.

Rules:
  * By rule, never by judgment. A pattern finds a rule sentence, a check and
    a claim. Whether a turn is a reaction is the drafter's reading, and the
    string match decides whether the words are the human's.
  * Evidence, not memory: debrief-search.py never indexes this folder.
  * Text is redacted by redact-transcript.py before it lands. No redactor
    means nothing is written (fail closed).
  * Counts only. No command here writes a rule, a principle or a memory.
  * The transcript format is internal and unstable. The walk needs no field
    to be present, and a line that does not parse is skipped.

Usage:
    debrief-signals.py read --transcript <path.jsonl[.gz]> --session <id> [--dir D]
    debrief-signals.py prompt --session <id> [--dir D]
    debrief-signals.py adopt --draft <draft.md> --transcript <path> --session <id> [--dir D]
    debrief-signals.py landed [--project P] [--since YYYY-MM-DD] [--dir D]
    debrief-signals.py rules [--project P] [--since YYYY-MM-DD] [--dir D]
    debrief-signals.py principles [--dir D]
    debrief-signals.py echoes --file <text> [--against <prompt.md> ...]
    debrief-signals.py --selftest
"""

import argparse
import difflib
import glob
import gzip
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = 1
TURN_CHARS = 600
PROMPT_TURNS = 80
PROMPT_TURN_CHARS = 320
CLAIM_CHARS = 240
NEGATIVE = ("negative", "mixed")


class Refused(Exception):
    pass


def load_react(here=HERE):
    """The sibling debrief-react.py as a module. It owns the human-turn rules,
    the string match and the reaction files, so both tools agree on them."""
    p = os.path.join(here, "debrief-react.py")
    spec = importlib.util.spec_from_file_location("debrief_react", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sdir(base):
    return os.path.join(base, "sessions", "signals")


def spath(base, session):
    return os.path.join(sdir(base), "%s.json" % session[:8])


def one_line(s, limit):
    s = " ".join((s or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- patterns

SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
APOS = str.maketrans({"’": "'", "‘": "'"})

# A standing rule says it is meant to last. It opens like one, or it names
# its own reach. "Use the staging key" is an instruction for the task at hand.
RULE_OPEN = re.compile(r"^(please\s+|and\s+|also\s+|ok\s+|okay\s+)*(always|never|don'?t ever|do not ever|from now on|going forward|whenever)\b", re.I)
RULE_REACH = re.compile(
    r"\b(from now on|going forward|every time|each time|(?:before|after|on|for|with|in) every (?:push|commit|change|session|pr)|"
    r"as a rule|this is a rule|make (?:it|this) (?:a )?(?:rule|standard|standard practice|mandatory|the default)|"
    r"standard practice|never again|"
    r"(?:so|make sure|ensure) (?:it|this|that) (?:doesn'?t|does not|never|wont|won'?t) happens? again|"
    r"whenever\b[^.!?]{3,120}\b(?:should|shoul\w*|must|always|never|needs? to|has to|have to)\b)",
    re.I,
)
# An order not to repeat something: "dont rename a column like this again".
RULE_AGAIN = re.compile(r"^(please\s+)?(don'?t|do not|never)\b[^.!?]{3,80}\bagain\b", re.I)
RULE_TURN_CHARS = 1200  # a longer turn is a paste or a brief, not a rule said in passing
# Words that point back at the conversation. Such a rule needs its turn to be read.
DEICTIC = re.compile(r"\b(like this|like that|this again|that again|do that|do this|this one|that one|whatever that)\b", re.I)

# Commands that check the work. A read, a search or a listing says nothing about it.
CHECK = re.compile(
    # At the start of a command: the line's start, or after && ; || ( or a newline,
    # past any VAR=value words and a runner. `cat pytest.ini` and `grep jest` check nothing.
    r"(?:^|&&|;|\|\||\(|\n)\s*(?:\w+=\S+\s+)*(?:time\s+|uv run\s+|poetry run\s+)?("
    r"pytest|python3? -m (?:pytest|unittest)|"
    r"(?:npm|pnpm|yarn|bun)(?: run)? (?:test|typecheck|lint|build|check)|"
    r"npx (?:tsc|vitest|jest|eslint|playwright test)|tsc|vitest|jest|"
    r"cargo (?:test|build|check|clippy)|go (?:test|build|vet)|make|xcodebuild|swift (?:test|build)|"
    r"node --test|ruff|eslint|mypy|shellcheck|"
    r"(?:python3?|node|bash|sh) \S+ --selftest|(?:ba)?sh \S*selftest\S*"
    r")(?![\w.-])"
)
FAILURE = re.compile(
    r"\b[1-9]\d* (?:failed|failing|failures?|errors?)\b|\bFAILED\b|^FAIL\b|Traceback \(most recent call last\)|"
    r"^error(\[\w+\])?:|\bnot ok \d+|\bpanicked at\b|\bAssertionError\b|BUILD FAILED|\*\* TEST FAILED|Exit code:? [1-9]",
    re.M,
)
CLAIM = re.compile(
    r"(?<![-\w])(done|fixed|works now|now works|working|passes|passing|(?:tests?|checks?|gates?|suite) pass(?:ed)?|all green|verified|confirmed|completed?|"
    r"shipped|deployed|pushed(?! (?:head|commit|branch|sha|tree))|merged|is live|resolved)\b", re.I)
# A sentence that hedges, plans or denies is not a claim that the work is finished.
NOT_CLAIM = re.compile(
    r"\b(not|never|no longer|cannot|failed|fails?|failing|still|pending|todo|should|will|would|could|might|may|"
    r"if|once|until|unless|before i|needs?|want|plan|next|try|trying|let me)\b|n't\b|\?\s*$", re.I)


def sentences(text):
    return [s.strip() for s in SENTENCE.split(text or "") if s and s.strip()]


def rule_sentence(text):
    """The first sentence of the human's that reads as a standing rule, or None."""
    if len(text or "") > RULE_TURN_CHARS:
        return None
    for s in sentences((text or "").translate(APOS)):
        s = " ".join(s.split())
        if not 12 <= len(s) <= 240 or len(s.split()) < 4 or s.endswith("?"):
            continue
        if RULE_OPEN.search(s) or RULE_REACH.search(s) or RULE_AGAIN.search(s):
            return s
    return None


def check_kind(command):
    """What kind of check a shell command is, or None when it checks nothing."""
    m = CHECK.search(command or "")
    if not m:
        return None
    k = " ".join(m.group(1).split())
    if "selftest" in k:
        return "selftest"
    return k


def claim_sentence(text):
    """The last sentence of the agent's that says the work is finished, or None."""
    found = None
    for s in sentences(text):
        s = " ".join(s.split())
        if len(s) < 6 or not CLAIM.search(s) or NOT_CLAIM.search(s):
            continue
        found = s
    return found


# ---------------------------------------------------------------- the walk


def result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def walk(path, react):
    """The session as ordered events, numbered by transcript line the way
    debrief-react numbers them: ('human', line, text, ts), ('say', line, text),
    ('call', line, id, command), ('result', line, id, text, is_error).
    Also the first cwd and the time span."""
    events, meta = [], {"cwd": None, "first": None, "last": None}
    queued = []
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8", errors="replace") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("isSidechain"):
                continue
            ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None
            if ts:
                meta["first"] = meta["first"] or ts
                meta["last"] = ts
            if meta["cwd"] is None and isinstance(obj.get("cwd"), str):
                meta["cwd"] = obj["cwd"]
            is_queue = obj.get("type") == "queue-operation"
            for txt in react.human_texts(obj):
                txt = react.clean(txt).strip()
                if not txt:
                    continue
                key = react.norm(txt)
                # A message sent mid-turn is queued, then delivered as a turn. One turn.
                if not is_queue and key in queued:
                    queued.remove(key)
                    continue
                if is_queue:
                    queued.append(key)
                events.append(("human", n, txt, ts))
            msg = obj.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                continue
            for b in msg["content"]:
                if not isinstance(b, dict):
                    continue
                if obj.get("type") == "assistant" and b.get("type") == "text" and isinstance(b.get("text"), str):
                    if b["text"].strip():
                        events.append(("say", n, b["text"]))
                elif obj.get("type") == "assistant" and b.get("type") == "tool_use":
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    if isinstance(inp.get("command"), str):
                        events.append(("call", n, b.get("id"), inp["command"]))
                elif obj.get("type") == "user" and b.get("type") == "tool_result":
                    events.append(("result", n, b.get("tool_use_id"), result_text(b), bool(b.get("is_error"))))
    return events, meta


def spans(events, redact):
    """One record per human turn, with what the agent did since the turn before."""
    turns, calls = [], {}
    say, say_line, checks = None, None, []
    for ev in events:
        if ev[0] == "say":
            say, say_line = ev[2], ev[1]
        elif ev[0] == "call":
            kind = check_kind(ev[3])
            if kind and ev[2]:
                calls[ev[2]] = kind
        elif ev[0] == "result":
            kind = calls.pop(ev[2], None)
            if kind:
                failed = ev[4] or bool(FAILURE.search(ev[3] or ""))
                checks.append((kind, "failed" if failed else "passed"))
        elif ev[0] == "human":
            after = None
            if turns:
                claim = claim_sentence(say) if say else None
                passed = sorted({k for k, r in checks if r == "passed"})
                failed = sorted({k for k, r in checks if r == "failed"})
                seen, again = set(), set()
                for k, r in checks:
                    if r == "passed":
                        seen.add(k)
                    elif k in seen:
                        again.add(k)
                if claim or passed or failed:
                    after = {
                        "claim": one_line(redact(claim)[0], CLAIM_CHARS) if claim else None,
                        "claim_line": say_line if claim else None,
                        "passed": passed, "failed": failed, "failed_again": sorted(again),
                    }
            rule = rule_sentence(ev[2])
            turns.append({
                "n": len(turns) + 1, "line": ev[1], "at": ev[3],
                "text": one_line(redact(ev[2])[0], TURN_CHARS),
                "covered": [],
                "rule": redact(rule)[0] if rule else None,
                "rule_needs_its_turn": bool(rule and DEICTIC.search(rule)),
                "after": after,
            })
            say, say_line, checks = None, None, []
    return turns


def session_reactions(base, session, react):
    recs = react.read_jsonl(os.path.join(react.rdir(base), "%s.jsonl" % session[:8]))[0]
    return [r for r in recs if r.get("verified") is True and r.get("words")]


def cover(turns, recs):
    by_line = {t["line"]: t for t in turns}
    for t in turns:
        t["covered"] = []
    for r in recs:
        for ln in r.get("lines") or ([r["line"]] if r.get("line") else []):
            if ln in by_line and r.get("id") not in by_line[ln]["covered"]:
                by_line[ln]["covered"].append(r.get("id"))


def read(base, transcript, session, react=None, redact="load", now=None):
    react = react or load_react()
    if redact == "load":
        redact = react.load_redactor(HERE)
    if redact is None:
        raise Refused("redact-transcript.py did not load next to this script; nothing written")
    events, meta = walk(transcript, react)
    turns = spans(events, redact)
    cover(turns, session_reactions(base, session, react))
    old = load_json(spath(base, session)) or {}
    cwd = meta["cwd"]
    data = {
        "version": VERSION,
        "session": session,
        "project": react.default_project(cwd) if cwd else None,
        "cwd": redact(cwd)[0] if cwd else None,
        "first": meta["first"], "last": meta["last"],
        "read_at": react.iso(now or react.now_utc()),
        "turns": turns,
        # What a later step added is kept when the session is read again.
        "label_checks": old.get("label_checks") or {},
        "echoes": old.get("echoes") or [],
    }
    save_json(spath(base, session), data)
    return data


# ---------------------------------------------------------------- the drafter's block


def prompt_block(base, session):
    data = load_json(spath(base, session))
    if not data:
        return ""
    turns = data.get("turns") or []
    out = []
    # The first turn is the request. A reaction answers work, so it starts at turn 2.
    open_turns = [t for t in turns[1:] if not t.get("covered")][-PROMPT_TURNS:]
    if open_turns:
        out.append("UNRECORDED TURNS (the human's turns that no recorded reaction covers; transcript line, then his text):")
        out += ["L%s: %s" % (t["line"], one_line(t["text"], PROMPT_TURN_CHARS)) for t in open_turns]
    rules = [t for t in turns if t.get("rule")]
    if rules:
        out.append("STATED RULES (sentences of the human's that read as standing rules; found by pattern, not by judgment):")
        out += ['L%s: "%s"' % (t["line"], t["rule"]) for t in rules]
    before = [t for t in turns if t.get("after")]
    if before:
        out.append("BEFORE A TURN (what the agent claimed or checked just before the human spoke):")
        for t in before[-PROMPT_TURNS:]:
            a, bits = t["after"], []
            if a.get("claim"):
                bits.append('the agent said: "%s"' % a["claim"])
            if a.get("passed"):
                bits.append("checks passed: " + ", ".join(a["passed"]))
            if a.get("failed"):
                bits.append("checks failed: " + ", ".join(a["failed"]))
            out.append("L%s: %s" % (t["line"], "; ".join(bits)))
    if not out:
        return ""
    return "TURN SIGNALS: %s\n%s" % (spath(base, session), "\n".join(out))


# ---------------------------------------------------------------- adopt

UNRECORDED_LINE = re.compile(r'^\s*-\s*"(.+)"\s*\|\s*about:\s*(.+?)\s*\|\s*sentiment:\s*([a-z]+)\s*\|\s*kind:\s*([a-z-]+)\s*$')
LABEL_LINE = re.compile(r"^\s*-\s*`?([0-9a-f]{12})`?\s*\|\s*sentiment:\s*([a-z]+)\s*$")


def section(text, heading):
    """The lines under one `## heading`, up to the next `## `."""
    out, on = [], False
    for ln in text.splitlines():
        if ln.startswith("## "):
            on = ln[3:].strip().lower() == heading.lower()
            continue
        if on:
            out.append(ln)
    return out


def echo_lines(text, against):
    """Lines of text that repeat an example printed in a prompt file. A drafter
    that copies the prompt's own example wrote nothing about the session."""
    examples = []
    for p in against:
        try:
            body = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for q in re.findall(r'"([^"\n]{30,300})"', body.translate(APOS)):
            if "<" not in q and ">" not in q:
                examples.append(" ".join(q.lower().split()))
    hits = []
    if not examples:
        return hits
    for ln in text.translate(APOS).splitlines():
        for q in re.findall(r'"([^"\n]{30,300})"', ln):
            low = " ".join(q.lower().split())
            for e in examples:
                if low == e or (0.6 < len(low) / len(e) < 1.6
                                and difflib.SequenceMatcher(None, low, e).ratio() > 0.85):
                    hits.append(one_line(q, 200))
                    break
    return sorted(set(hits))


def adopt(base, draft, transcript, session, react=None, redact="load", now=None, prompts=None):
    """Take the reactions the drafter found, keep the ones whose words are in a
    human turn, and note where the drafter read a recorded label differently."""
    react = react or load_react()
    if redact == "load":
        redact = react.load_redactor(HERE)
    if redact is None:
        raise Refused("redact-transcript.py did not load; nothing adopted")
    data = load_json(spath(base, session))
    if not data:
        raise Refused("no signals file for this session; run read first")
    now = now or react.now_utc()
    text = open(draft, encoding="utf-8", errors="replace").read()
    t = react.read_transcript(transcript, redact)
    turn_at = {tr["line"]: tr.get("at") for tr in data.get("turns") or []}
    counts = {"adopted": 0, "not_found": 0, "already_recorded": 0, "malformed": 0,
              "labels_checked": 0, "labels_differ": 0, "echoes": 0}
    sess_p = os.path.join(react.rdir(base), "%s.jsonl" % session[:8])
    with react.Lock(base):
        have = react.read_jsonl(sess_p)[0]
        ids = {r.get("id") for r in have}
        known = [react.norm(r.get("words") or "") for r in have if r.get("verified") is True]
        new = []
        for ln in section(text, "Unrecorded reactions"):
            if not ln.strip().startswith("-"):
                continue
            m = UNRECORDED_LINE.match(ln)
            if not m or m.group(3) not in react.SENTIMENTS:
                counts["malformed"] += 1
                continue
            words = redact(m.group(1).strip())[0]
            key = react.norm(words)
            rid = hashlib.sha256(("%s\n%s" % (session, key)).encode()).hexdigest()[:12]
            if rid in ids:
                continue
            if any(key and (key in k or k in key) for k in known if k):
                counts["already_recorded"] += 1
                continue
            rec = {
                "id": rid, "at": None, "words": words, "about": redact(m.group(2).strip())[0],
                "sentiment": m.group(3), "kind": m.group(4) if m.group(4) in react.KINDS else None,
                "before": None, "after": None, "project": data.get("project"),
                "agent_note": None, "cwd": data.get("cwd"), "source": "drafter", "verified": None,
            }
            lines, missing = react.find(rec, t["turns"])
            rec["at"] = (turn_at.get(lines[0]) if lines else None) or data.get("last") or react.iso(now)
            new.append(react.settle(rec, session, lines, missing, now))
            ids.add(rid)
            if not missing:
                known.append(key)
            counts["adopted" if not missing else "not_found"] += 1
        react.append(sess_p, new)
        recorded = {r.get("id"): r for r in have if r.get("verified") is True and r.get("source") != "drafter"}
    checks = dict(data.get("label_checks") or {})
    for ln in section(text, "Label check"):
        m = LABEL_LINE.match(ln)
        if not m or m.group(1) not in recorded or m.group(2) not in react.SENTIMENTS:
            continue
        agrees = m.group(2) == recorded[m.group(1)].get("sentiment")
        checks[m.group(1)] = {"sentiment": m.group(2), "by": "drafter", "agrees": agrees}
    counts["labels_checked"] = len(checks)
    counts["labels_differ"] = sum(1 for c in checks.values() if not c.get("agrees"))
    if prompts is None:
        prompts = sorted(glob.glob(os.path.join(HERE, "prompts", "*.md")))
    echoes = echo_lines(text, prompts)
    counts["echoes"] = len(echoes)
    data["label_checks"], data["echoes"] = checks, echoes
    cover(data.get("turns") or [], session_reactions(base, session, react))
    save_json(spath(base, session), data)
    return counts


# ---------------------------------------------------------------- reports


def all_signals(base, react, project=None, since=None):
    reg = react.load_registry(base)
    want = react.resolve(project, reg) if project else None
    out = []
    for p in sorted(glob.glob(os.path.join(sdir(base), "*.json"))):
        d = load_json(p)
        if not d or not isinstance(d.get("turns"), list):
            continue
        if want is not None and react.canon(d.get("project"), reg) not in want:
            continue
        if since and (d.get("last") or d.get("read_at") or "")[:10] < since:
            continue
        out.append(d)
    return sorted(out, key=lambda d: d.get("last") or d.get("read_at") or "", reverse=True)


def reading_of(r):
    return "drafter's reading" if r.get("source") == "drafter" else "agent's reading"


def cmd_landed(base, project=None, since=None, react=None):
    """Claims and passing checks that a negative reaction followed."""
    react = react or load_react()
    rows, claims, again = [], 0, {}
    sessions = all_signals(base, react, project, since)
    for d in sessions:
        recs = session_reactions(base, d.get("session") or "", react)
        for t in d["turns"]:
            a = t.get("after")
            if not a:
                continue
            for k in a.get("failed_again") or []:
                again.setdefault(d.get("project") or "?", {}).setdefault(k, 0)
                again[d.get("project") or "?"][k] += 1
            if not (a.get("claim") or a.get("passed")):
                continue
            claims += 1
            second = d.get("label_checks") or {}
            neg = [r for r in recs if t["line"] in (r.get("lines") or [r.get("line")])
                   and (r.get("sentiment") in NEGATIVE
                        or (second.get(r.get("id")) or {}).get("sentiment") in NEGATIVE)]
            if neg:
                rows.append((d, t, neg))
    print("Claimed or checked, then corrected: %d of %d stretches of work that ended on a claim or a passing check, "
          "in %d sessions read." % (len(rows), claims, len(sessions)))
    print("Counts only. A reaction is counted only when it was recorded or found, so the first number is a floor. "
          "A reaction counts here when either reading of it is negative or mixed. A label is a reading, not a rule.")
    by = {}
    for d, _, _ in rows:
        by[d.get("project") or "?"] = by.get(d.get("project") or "?", 0) + 1
    if by:
        print()
        print("By project:")
        for k in sorted(by):
            print("  %-28s %d" % (k, by[k]))
    for d, t, neg in rows:
        a = t["after"]
        print()
        print("%s · %s · session %s · transcript line %s" % (
            (d.get("last") or "")[:10], d.get("project") or "?", (d.get("session") or "")[:8], t["line"]))
        if a.get("claim"):
            print('  before, the agent: "%s"' % a["claim"])
        if a.get("passed"):
            print("  checks passed before he spoke: %s" % ", ".join(a["passed"]))
        for r in neg:
            two = (d.get("label_checks") or {}).get(r.get("id"))
            also = "; %s, drafter's reading" % two["sentiment"] if two and not two.get("agrees") else ""
            print('  then, the human: "%s" (%s, %s%s)' % (r.get("words"), r.get("sentiment"), reading_of(r), also))
    if again:
        print()
        print("A check that passed, then failed again inside one stretch of work:")
        for p in sorted(again):
            print("  %-28s %s" % (p, ", ".join("%s %d" % kv for kv in sorted(again[p].items()))))
    return rows


def cmd_rules(base, project=None, since=None, react=None):
    """Standing rules the human stated, and how many sessions each was said in."""
    react = react or load_react()
    seen = {}
    for d in all_signals(base, react, project, since):
        for t in d["turns"]:
            if not t.get("rule"):
                continue
            key = " ".join(re.findall(r"[a-z0-9']+", t["rule"].lower().translate(APOS)))
            e = seen.setdefault(key, {"rule": t["rule"], "where": [], "needs": t.get("rule_needs_its_turn")})
            e["where"].append((d.get("last") or "", d.get("project") or "?", (d.get("session") or "")[:8], t["line"]))
    rows = list(seen.values())
    rows.sort(key=lambda e: (len({w[2] for w in e["where"]}), max(w[0] for w in e["where"])), reverse=True)
    print("%d stated rules in the sessions read. Found by pattern in the human's own turns; the words are exact." % len(rows))
    print("A rule said in more than one session was said again after it was first given.")
    for e in rows:
        n = len({w[2] for w in e["where"]})
        print()
        print('"%s"' % e["rule"])
        note = " · points back at its turn, open it to read the rule" if e["needs"] else ""
        print("  said in %d session%s%s" % (n, "" if n == 1 else "s", note))
        for at, proj, sid, line in sorted(e["where"], reverse=True)[:6]:
            print("  %s · %s · session %s · transcript line %s" % (at[:10], proj, sid, line))
    return rows


DATE = r"(\d{4}-\d{2}-\d{2})"
RECEIPT = re.compile(r"^— (?:seen|contra) in \[[^\]]*\]\((" + DATE[1:-1] + r")[^)]*\)", re.M)
STATUS = re.compile(r"^— status: promoted · INDEX principle (\d+)(?: · dream " + DATE + r")?", re.M)
ENFORCED = re.compile(r"— enforced by:\s*(.+?)(?:\s*·\s*since " + DATE + r")?\s*$", re.M)


def read_principles(base):
    """Each numbered INDEX principle, joined to its promoted observation."""
    try:
        index = open(os.path.join(base, "INDEX.md"), encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    m = re.search(r"^## Principles.*?$(.*?)(?=^## |\Z)", index, re.M | re.S)
    items = {}
    if m:
        for block in re.split(r"(?m)^(?=\d+\. )", m.group(1)):
            head = re.match(r"(\d+)\. (.*)", block, re.S)
            if not head or "*(example)*" in block[:40]:
                continue
            title = re.search(r"\*\*(.+?)\*\*", block, re.S)
            enf = ENFORCED.search(block)
            said = re.search(r"Promoted by the dream\s+pass,\s+" + DATE, block)
            from_dates = re.findall(r"\]\(" + DATE, block)
            items[int(head.group(1))] = {
                "n": int(head.group(1)),
                "title": one_line(title.group(1), 110) if title else one_line(head.group(2), 110),
                "enforced_by": enf.group(1).strip() if enf else None,
                "enforced_since": enf.group(2) if enf else None,
                "promoted": said.group(1) if said else None,
                "dates": set(from_dates),
            }
    try:
        obs = open(os.path.join(base, "OBSERVATIONS.md"), encoding="utf-8", errors="replace").read()
    except OSError:
        obs = ""
    for entry in re.split(r"(?m)^(?=### )", obs):
        st = STATUS.search(entry)
        if not st or int(st.group(1)) not in items:
            continue
        it = items[int(st.group(1))]
        it["dates"] |= set(RECEIPT.findall(entry))
        it["promoted"] = st.group(2) or it["promoted"]
    return [items[k] for k in sorted(items)]


def cmd_principles(base):
    """Has a promoted principle stopped the thing it names? Dates only."""
    rows = read_principles(base)
    recurring, enforced, quiet, undated = [], [], [], []
    for it in rows:
        if it["enforced_by"]:
            since = it["enforced_since"] or it["promoted"]
            it["after"] = sorted(d for d in it["dates"] if since and d > since)
            enforced.append(it)
        elif not it["promoted"]:
            it["after"] = []
            undated.append(it)
        else:
            it["after"] = sorted(d for d in it["dates"] if d > it["promoted"])
            (recurring if len(it["after"]) >= 2 else quiet).append(it)
    print("%d principles: %d still recurring after promotion, %d enforced by a hook or a check, %d quiet, %d with no promotion date on record."
          % (len(rows), len(recurring), len(enforced), len(quiet), len(undated)))
    print("Dates only: a receipt date after the promotion date is counted. It does not say the receipt is the same shape. A person decides what follows.")

    def line(it):
        tail = "last %s" % it["after"][-1] if it["after"] else "none"
        return "Principle %d · %s\n  promoted %s · receipts on %d dates · on %d dates after %s (%s)" % (
            it["n"], it["title"], it["promoted"] or "?", len(it["dates"]), len(it["after"]),
            "enforcement" if it["enforced_by"] else "promotion", tail)

    if recurring:
        print()
        print("STILL RECURRING: the text has not stopped it. A hook, a CI check or a test may.")
        for it in sorted(recurring, key=lambda i: -len(i["after"])):
            print(line(it))
    if enforced:
        print()
        print("ENFORCED: a mechanism holds it. With no receipt since, the text may leave INDEX on the human's word.")
        for it in enforced:
            print(line(it))
            print("  enforced by: %s%s" % (it["enforced_by"], " · since %s" % it["enforced_since"] if it["enforced_since"] else ""))
    if quiet:
        print()
        print("QUIET: one receipt date or none after promotion.")
        for it in quiet:
            print(line(it))
    if undated:
        print()
        print("NO PROMOTION DATE ON RECORD: cannot be counted.")
        for it in undated:
            print("Principle %d · %s" % (it["n"], it["title"]))
    return {"recurring": recurring, "enforced": enforced, "quiet": quiet, "undated": undated}


# ---------------------------------------------------------------- selftest


def selftest():
    import contextlib
    import io
    import tempfile
    from datetime import timedelta

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

    react = load_react()
    redact = react.load_redactor(HERE)
    check("the sibling redactor loads", redact is not None)
    if redact is None:
        print("1 FAILED")
        sys.exit(1)

    # Patterns, each with a control that must not match.
    for s in ("run the linter before you commit, this is a rule.",
              "make it a rule to bump the version in the same change.",
              "going forward, tag the build first",
              "Never commit a generated file.",
              "dont rename a column like this again",
              "from now on use pnpm here",
              "whenever the schema changes the fixtures shoud change too",
              "add a test to make sure this never happens again"):
        check("rule: found in %r" % s[:38], rule_sentence(s) is not None)
    for s in ("use the staging key for this run", "can you always see the header?", "push it", "why is it never green",
              "fix the overlap then push",
              "We can look at it whenever the build finishes.",
              "The cache is warm now, so you don't see the slow path again.",
              "After the migration, the old table never gets a write again.",
              "Two seats for every guest", "there may be more tiers in the future",
              "we can sort the rest out next time", "I want a separate page for all the projects",
              "Always " + "x " * 700):
        check("rule control: not found in %r" % s[:38], rule_sentence(s) is None, rule_sentence(s))
    check("rule: only the rule sentence is kept, exact",
          rule_sentence("ok that works. Always run the selftest before you push. thanks") == "Always run the selftest before you push.")
    for c, k in (("cd app && pnpm test", "pnpm test"), ("python3 x.py --selftest", "selftest"),
                 ("pytest -q tests/", "pytest"), ("bash .system/dream-selftest.sh", "selftest"),
                 ("CI=1 npm run build 2>&1 | tail -5", "npm run build"), ("git pull\nmake test", "make")):
        check("check: %r is a %s check" % (c, k), check_kind(c) == k, check_kind(c))
    for c in ("git status", "ls -la", "cat pytest.ini.bak | head", "grep -rn test src", "grep jest package.json",
              "cat Makefile", "echo make it so"):
        check("check control: %r checks nothing" % c, check_kind(c) is None, check_kind(c))
    check("claim: a finished sentence is found", claim_sentence("I ran it. The overlap is fixed and pushed.") == "The overlap is fixed and pushed.")
    for s in ("It is not fixed yet.", "I will push once you confirm.", "Should I mark it done?", "The test still fails.",
              "Three reviewers for the live-verified map tiles.", "The job has picked up the pushed commit."):
        check("claim control: %r is no claim" % s, claim_sentence(s) is None, claim_sentence(s))

    now = react.now_utc()
    ts = lambda m: react.iso(now + timedelta(minutes=m))
    sid = "abcd1234ffff0000"
    secret = "sk-ant-api03-" + "C" * 40

    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "store")
        work = os.path.join(td, "proj")
        os.makedirs(base)
        os.makedirs(work)
        U = lambda content, m, **kw: dict({"type": "user", "cwd": work, "timestamp": ts(m),
                                           "message": {"role": "user", "content": content}}, **kw)
        A = lambda blocks, m: {"type": "assistant", "cwd": work, "timestamp": ts(m),
                               "message": {"role": "assistant", "content": blocks}}
        say = lambda t: {"type": "text", "text": t}
        bash = lambda i, c: {"type": "tool_use", "id": i, "name": "Bash", "input": {"command": c}}
        res = lambda i, t, err=False: [{"type": "tool_result", "tool_use_id": i, "content": t, "is_error": err}]
        lines = [
            U("make the header calmer", -60),                                             # 1
            A([say("Working on it."), bash("c1", "pnpm test")], -59),                     # 2
            U(res("c1", "12 passed"), -58),                                               # 3
            A([say("The header is calmer now. All 12 tests pass and it is pushed.")], -57),  # 4
            U("its still too loud, drop the gradient", -56),                              # 5
            A([bash("c2", "pnpm test"), bash("c3", "git status")], -55),                  # 6
            U(res("c2", "2 failed, 10 passed", True), -54),                               # 7
            U(res("c3", "clean"), -54),                                                   # 8
            A([bash("c4", "pnpm test")], -53),                                            # 9
            U(res("c4", "12 passed"), -52),                                               # 10
            A([bash("c5", "pnpm test")], -51),                                            # 11
            U(res("c5", "1 failed"), -50),                                                # 12
            A([say("I will look at the last failure next.")], -49),                       # 13
            {"type": "queue-operation", "operation": "enqueue", "timestamp": ts(-48),
             "content": "from now on run the selftest before every push"},                # 14
            U("from now on run the selftest before every push", -47),                     # 15 (same turn, delivered)
            A([say("Understood. The gradient is removed and verified in the browser.")], -46),  # 16
            U("ok that one i love, keep it. my key is %s" % secret, -45),                 # 17
            U("<system-reminder>never said by a person, always injected</system-reminder>", -44),  # 18
            U("always do this in a subagent prompt", -43, isSidechain=True),              # 19
            A([say("Done.")], -42),                                                       # 20
            U("what does the gradient flag do?", -41),                                    # 21
        ]
        tp = os.path.join(td, "t.jsonl")
        with open(tp, "w") as f:
            for d in lines:
                f.write(json.dumps(d) + "\n")
            f.write("{not json\n")

        # One reaction the agent recorded in the session, verified the normal way.
        rec = react.record(base, "its still too loud, drop the gradient", "header", "positive", kind="rejected",
                           cwd=work, project="shop-web", redact=redact, session_hint=sid)
        react.verify(base, tp, sid, now=now, redact=redact)

        data = read(base, tp, sid, react=react, redact=redact, now=now)
        turns = data["turns"]
        check("read: one record per human turn, queued and delivered counted once",
              [t["line"] for t in turns] == [1, 5, 14, 17, 21], [t["line"] for t in turns])
        check("read: injected, sidechain and tool-result lines are not human turns",
              not any("injected" in t["text"] or "subagent" in t["text"] for t in turns))
        check("read: the recorded reaction covers its turn", turns[1]["covered"] == [rec["id"]], turns[1])
        check("read: a turn with no reaction is not covered", turns[3]["covered"] == [] and turns[4]["covered"] == [])
        check("read: the claim before a turn is kept, exact",
              turns[1]["after"]["claim"] == "All 12 tests pass and it is pushed."
              and turns[1]["after"]["claim_line"] == 4, turns[1]["after"])
        check("read: checks before a turn: passed", turns[1]["after"]["passed"] == ["pnpm test"] and turns[1]["after"]["failed"] == [])
        check("read: a check that passed then failed again in one stretch",
              turns[2]["after"]["failed_again"] == ["pnpm test"] and turns[2]["after"]["claim"] is None, turns[2]["after"])
        check("read: a plan is not kept as a claim", turns[2]["after"]["claim"] is None)
        check("read: git status is not a check", "git status" not in json.dumps(data))
        check("read: the stated rule is found, exact",
              turns[2]["rule"] == "from now on run the selftest before every push", turns[2]["rule"])
        check("read: a question is no rule", turns[4]["rule"] is None)
        body = open(spath(base, sid)).read()
        check("read: a key in a turn is redacted on disk", secret not in body and "[REDACTED:" in body)
        check("read: project and session kept", data["project"] == "proj" and data["session"] == sid)
        try:
            read(base, tp, sid, react=react, redact=None)
            check("read: no redactor refused", False)
        except Refused:
            check("read: no redactor refused", True)

        block = prompt_block(base, sid)
        check("prompt: uncovered turns listed with their lines",
              "UNRECORDED TURNS" in block and "L17: ok that one i love" in block and "L21:" in block)
        check("prompt: the covered turn and the request are left out",
              "L5: its still" not in block and "L1: make the header" not in block, block)
        check("prompt: stated rules listed", 'L14: "from now on run the selftest before every push"' in block)
        check("prompt: what came before a turn", 'L5: the agent said: "All 12 tests pass and it is pushed."; checks passed: pnpm test' in block, block)
        check("prompt: no key reaches the drafter", secret not in block)
        check("prompt: empty for a session never read", prompt_block(base, "nope0000") == "")

        prompt_file = os.path.join(td, "prompt.md")
        with open(prompt_file, "w") as f:
            f.write('Write a hedge, as in "we tuned the backoff twice and it looked better".\n- "<their words, exactly>" | about: <piece>\n')
        draft = os.path.join(td, "draft.md")
        with open(draft, "w") as f:
            f.write("\n".join([
                "# Session draft", "", "## How it landed", '"its still too loud, drop the gradient" — header', "",
                "## Unrecorded reactions",
                '- "ok that one i love, keep it" | about: header without the gradient | sentiment: positive | kind: kept',
                '- "the agent said this, he never did" | about: header | sentiment: negative | kind: rejected',
                '- "drop the gradient" | about: header | sentiment: negative | kind: rejected',
                '- "what does the gradient flag do?" | about: header | sentiment: curious | kind: question',
                "- a line in no known form",
                "", "## Label check", "- %s | sentiment: negative" % rec["id"], "- 000000000000 | sentiment: positive", "",
                "## Candidate principle observations",
                '- It seemed to hold: "We tuned the backoff twice and it looked better".', "",
            ]))
        c = adopt(base, draft, tp, sid, react=react, redact=redact, now=now, prompts=[prompt_file])
        sess = react.read_jsonl(os.path.join(react.rdir(base), "abcd1234.jsonl"))[0]
        got = {r["words"]: r for r in sess}
        love = got.get("ok that one i love, keep it", {})
        check("adopt: words found in a human turn are added, verified, marked as the drafter's",
              love.get("verified") is True and love.get("source") == "drafter" and love.get("line") == 17
              and love.get("sentiment") == "positive" and love.get("kind") == "kept", love)
        check("adopt: the reaction is dated at its turn", love.get("at") == ts(-45), love.get("at"))
        ghost = got.get("the agent said this, he never did", {})
        check("adopt: words in no human turn are kept as not his", ghost.get("verified") is False and "why" in ghost, ghost)
        check("adopt: words already in a recorded reaction are not added twice",
              "drop the gradient" not in got and c["already_recorded"] == 1, c)
        check("adopt: an unknown sentiment and a free line are malformed, not guessed", c["malformed"] == 2, c)
        check("adopt: counts", c["adopted"] == 1 and c["not_found"] == 1, c)
        data = load_json(spath(base, sid))
        check("adopt: the drafter's different reading of a recorded label is noted",
              data["label_checks"].get(rec["id"]) == {"sentiment": "negative", "by": "drafter", "agrees": False}
              and c["labels_differ"] == 1 and "000000000000" not in data["label_checks"], data["label_checks"])
        check("adopt: the adopted reaction now covers its turn", love.get("id") in data["turns"][3]["covered"], data["turns"][3])
        check("adopt: a line that repeats the prompt's own example is flagged",
              data["echoes"] == ["We tuned the backoff twice and it looked better"], data["echoes"])
        check("echo control: a placeholder in the prompt is not an example", not any("their words" in e for e in data["echoes"]))
        snap = open(os.path.join(react.rdir(base), "abcd1234.jsonl"), "rb").read()
        c2 = adopt(base, draft, tp, sid, react=react, redact=redact, now=now, prompts=[prompt_file])
        check("adopt: a second run adds nothing",
              snap == open(os.path.join(react.rdir(base), "abcd1234.jsonl"), "rb").read() and c2["adopted"] == 0, c2)
        data2 = read(base, tp, sid, react=react, redact=redact, now=now)
        check("read again: label checks and echoes are kept",
              data2["label_checks"] == data["label_checks"] and data2["echoes"] == data["echoes"])
        try:
            adopt(base, draft, tp, "ffff9999", react=react, redact=redact)
            check("adopt: no signals file refused", False)
        except Refused:
            check("adopt: no signals file refused", True)

        # A second session restates the rule, and has a claim nobody corrected.
        sid2 = "beef5678aaaa1111"
        lines2 = [U("add a footer", -30), A([say("The footer is done.")], -29), U("cool, next: the nav", -28),
                  A([say("Nav is deployed.")], -27), U("From now on run the selftest before every push.", -26)]
        tp2 = os.path.join(td, "t2.jsonl")
        with open(tp2, "w") as f:
            for d in lines2:
                f.write(json.dumps(d) + "\n")
        read(base, tp2, sid2, react=react, redact=redact, now=now)

        o = out_of(cmd_landed, base, react=react)
        check("landed: the corrected claim is listed with both quotes",
              "Claimed or checked, then corrected: 1 of 5" in o and '"All 12 tests pass and it is pushed."' in o
              and "\"its still too loud, drop the gradient\" (positive, agent's reading; negative, drafter's reading)" in o, o)
        check("landed control: a claim followed by approval or no reaction is not listed",
              "verified in the browser" not in o and "footer is done" not in o, o)
        check("landed: says the count is a floor and the label a reading", "is a floor" in o and "agent's reading" in o)
        check("landed: a check that passed then failed again is counted", "pnpm test 1" in o, o)
        check("landed: project filter", "corrected: 0 of 0" in out_of(cmd_landed, base, project="elsewhere", react=react))

        o = out_of(cmd_rules, base, react=react)
        check("rules: the restated rule is one entry, said in 2 sessions",
              "1 stated rules" in o and "said in 2 sessions" in o and "session abcd1234" in o and "session beef5678" in o, o)
        check("rules: since filter", "0 stated rules" in out_of(cmd_rules, base, since="2999-01-01", react=react))

        # Principles: receipts dated after the promotion are counted, by principle.
        with open(os.path.join(base, "INDEX.md"), "w") as f:
            f.write("\n".join([
                "# Index", "", "## Principles (attractors)", "",
                "1. *(example)* **An example principle.** Not counted.",
                "   — from [2026-01-04 a](2026-01-04-a.md)",
                "3. **Check the last hop,", "   every time.** Text. *Promoted by the dream pass, 2026-10-29.*",
                "   — from [2026-09-23](2026-09-23-a.md) · [2026-11-17](2026-11-17-b.md)",
                "4. **Lint before a commit.** Text. *Promoted by the dream",
                "   pass, 2026-10-29.*",
                "   — from [2026-10-26](2026-10-26-c.md)",
                "   — enforced by: .githooks/pre-commit · since 2026-10-26",
                "5. **Quiet one.** Text.", "   — from [2026-10-01](2026-10-01-d.md)",
                "6. **No date on record.** Text.", "", "## Glossary", "", "9. not a principle", "",
            ]))
        with open(os.path.join(base, "OBSERVATIONS.md"), "w") as f:
            f.write("\n".join([
                "# Observations", "",
                "### Watching: the last hop went unchecked", "",
                "— seen in [2026-09-23 a](2026-09-23-a.md) · 2026-09-23", '  "q"',
                "— seen in [2026-11-12 x](2026-11-12-x.md) · 2026-11-12", '  "q"',
                "— seen in [2026-11-17 b](2026-11-17-b.md) · 2026-11-17 (session `0000aaaa`)", '  "q"',
                "— status: promoted · INDEX principle 3 · dream 2026-10-29 · 16 dailies", "",
                "### Watching: quiet", "",
                "— seen in [2026-10-01 d](2026-10-01-d.md) · 2026-10-01",
                "— seen in [2026-10-30 e](2026-10-30-e.md) · 2026-10-30",
                "— status: promoted · INDEX principle 5 · dream 2026-10-29 · 2 dailies", "",
                "### Watching: not promoted", "",
                "— seen in [2026-11-18 f](2026-11-18-f.md) · 2026-11-18",
                "— status: watching · 1 daily", "",
            ]))
        o = out_of(cmd_principles, base)
        check("principles: the example and the glossary are not principles", o.startswith("4 principles:"), o.splitlines()[0])
        check("principles: receipts after promotion counted from both files",
              "STILL RECURRING" in o and "Principle 3 · Check the last hop, every time." in o
              and "promoted 2026-10-29 · receipts on 3 dates · on 2 dates after promotion (last 2026-11-17)" in o, o)
        check("principles: an enforced principle is listed with its mechanism and counted from the enforcement date",
              "ENFORCED" in o and "enforced by: .githooks/pre-commit · since 2026-10-26" in o
              and "on 0 dates after enforcement (none)" in o, o)
        check("principles: one date after promotion is quiet",
              o.index("QUIET") < o.index("Principle 5 · Quiet one.") and "on 1 dates after promotion" in o, o)
        check("principles: no promotion date is said, not guessed",
              o.index("NO PROMOTION DATE ON RECORD") < o.index("Principle 6 · No date on record."), o)
        check("principles: the head line counts each group", "1 still recurring after promotion, 1 enforced by a hook or a check, 1 quiet, 1 with no promotion date" in o, o)
        check("principles: says a person decides", "A person decides" in o)
        check("principles: no store files, no rows", "0 principles" in out_of(cmd_principles, os.path.join(td, "empty")))

        # The CLI from a hook never fails the session.
        import subprocess
        me = os.path.abspath(__file__)
        p = subprocess.run([sys.executable, me, "read", "--dir", base, "--transcript", os.path.join(td, "missing.jsonl"),
                            "--session", "x"], capture_output=True, text=True)
        check("cli: read from a hook never fails the session", p.returncode == 0 and "FAILED" in p.stdout and not p.stderr, (p.returncode, p.stdout, p.stderr))
        p = subprocess.run([sys.executable, me, "adopt", "--dir", base, "--draft", os.path.join(td, "nodraft.md"),
                            "--transcript", tp, "--session", sid], capture_output=True, text=True)
        check("cli: adopt from a hook never fails the session", p.returncode == 0 and not p.stderr, (p.returncode, p.stderr))
        p = subprocess.run([sys.executable, me, "prompt", "--dir", base, "--session", sid], capture_output=True, text=True)
        check("cli: prompt prints the block", p.returncode == 0 and p.stdout.startswith("TURN SIGNALS: "), p.stdout[:80])
        p = subprocess.run([sys.executable, me, "echoes", "--file", draft, "--against", prompt_file], capture_output=True, text=True)
        check("cli: echoes exits non-zero when a line repeats an example", p.returncode == 1 and "backoff" in p.stdout, (p.returncode, p.stdout))
        clean_file = os.path.join(td, "clean.md")
        open(clean_file, "w").write('He said "this is a sentence nobody put in a prompt file".\n')
        p = subprocess.run([sys.executable, me, "echoes", "--file", clean_file, "--against", prompt_file], capture_output=True, text=True)
        check("cli: echoes exits zero on a clean file", p.returncode == 0, (p.returncode, p.stdout))

    print("ALL PASS" if not fails else "%d FAILED" % len(fails))
    sys.exit(1 if fails else 0)


# ---------------------------------------------------------------- cli


def main():
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    ap = argparse.ArgumentParser(prog="debrief-signals", description="What a session's transcript shows, read by rule.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dflt = os.environ.get("DEBRIEF_DIR")

    r = sub.add_parser("read")
    r.add_argument("--transcript", required=True)
    r.add_argument("--session", required=True)
    r.add_argument("--dir", default=dflt)

    p = sub.add_parser("prompt")
    p.add_argument("--session", required=True)
    p.add_argument("--dir", default=dflt)

    a = sub.add_parser("adopt")
    a.add_argument("--draft", required=True)
    a.add_argument("--transcript", required=True)
    a.add_argument("--session", required=True)
    a.add_argument("--dir", default=dflt)

    for name in ("landed", "rules"):
        s = sub.add_parser(name)
        s.add_argument("--project")
        s.add_argument("--since")
        s.add_argument("--dir", default=dflt)

    s = sub.add_parser("principles")
    s.add_argument("--dir", default=dflt)

    e = sub.add_parser("echoes")
    e.add_argument("--file", required=True)
    e.add_argument("--against", nargs="*")

    a = ap.parse_args()
    if a.cmd == "echoes":
        against = a.against or sorted(glob.glob(os.path.join(HERE, "prompts", "*.md")))
        hits = echo_lines(open(a.file, encoding="utf-8", errors="replace").read(), against)
        for h in hits:
            print('repeats a prompt example: "%s"' % h)
        if not hits:
            print("no line repeats a prompt example")
        sys.exit(1 if hits else 0)
    if a.cmd in ("read", "adopt", "prompt"):
        # Called from the session-end hook: one line, and never a failure.
        try:
            if not a.dir:
                print("signals: SKIPPED, no --dir and no DEBRIEF_DIR")
                return
            if a.cmd == "prompt":
                block = prompt_block(a.dir, a.session)
                if block:
                    print(block)
                return
            if a.cmd == "read":
                d = read(a.dir, a.transcript, a.session)
                t = d["turns"]
                print("signals: %d human turns, %d covered by a reaction, %d stated rules, %d after a claim or a check"
                      % (len(t), sum(1 for x in t if x["covered"]), sum(1 for x in t if x["rule"]),
                         sum(1 for x in t if x["after"])))
            else:
                c = adopt(a.dir, a.draft, a.transcript, a.session)
                print("signals: adopt %s" % (", ".join("%d %s" % (n, k) for k, n in c.items() if n) or "nothing"))
        except Exception as ex:
            print("signals: FAILED %s" % (str(ex).splitlines() or [type(ex).__name__])[0])
        return
    if not a.dir:
        sys.exit("debrief-signals: no store. Pass --dir or set DEBRIEF_DIR.")
    if a.cmd == "landed":
        cmd_landed(a.dir, a.project, a.since)
    elif a.cmd == "rules":
        cmd_rules(a.dir, a.project, a.since)
    elif a.cmd == "principles":
        cmd_principles(a.dir)


if __name__ == "__main__":
    main()
