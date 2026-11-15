#!/usr/bin/env python3
"""redact-transcript — scrub secret-shaped strings out of a transcript.

Sits between the raw transcript and the cold archive: `DEBRIEF_RAW_ARCHIVE=1`
pipes through this before gzipping, so what lands on disk is an audit record
rather than a credential store.

Why this exists: the archive was already walled off from *search* (stored as
.jsonl.gz, structurally invisible to collect()), but that says nothing about
the bytes at rest. A transcript contains everything an agent's tool calls
echoed — and the leak vector is not credentials handled deliberately, it is
third-party output that happens to carry one. A measured example: a LiveKit
`access_token=eyJ…` arrived inside a Traefik access-log line during an
unrelated outage investigation and would have been archived verbatim.

Design constraints:

  * Output stays valid JSONL, line-for-line. Day mode's raw-transcript
    fallback parses this; a redactor that corrupts structure destroys the
    only reason the archive exists. Replacements contain no quotes, no
    backslashes, no newlines, so they are JSON-safe inside string values.
  * Fail closed. If a line cannot be processed, the line is dropped and
    counted rather than passed through unredacted. An archive that silently
    keeps a token is worse than one with a gap.
  * Over-redaction is acceptable; under-redaction is not. This is an audit
    trail, not a replay buffer — a scrubbed token costs nothing, and the
    reasoning around it is what you came back for.

Usage:
    redact-transcript.py <transcript.jsonl>      # -> stdout
    cat t.jsonl | redact-transcript.py -         # -> stdout
    redact-transcript.py --tree <src> <dest>     # every *.jsonl -> <dest>/<rel>.gz
    redact-transcript.py --selftest
"""

import re
import sys

# Ordered: earlier patterns win, so the specific (labelled) forms redact before
# the generic key=value sweep can produce a vaguer marker.
PATTERNS = [
    # PEM blocks — collapse the whole body, not just the header line.
    (
        "private-key",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
    ),
    # JWTs: three base64url segments. Catches the `eyJ` header form wherever it
    # appears — query param, header, log line, JSON value.
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # A bare `eyJ…` blob with no dots is still a base64 JSON header in practice.
    ("jwt-fragment", re.compile(r"eyJ[A-Za-z0-9_-]{20,}")),
    ("gh-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("slack-token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"AIza[A-Za-z0-9_-]{30,}")),
    ("aws-key-id", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("stripe-key", re.compile(r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("openai-key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("basic-auth-url", re.compile(r"://[^/\s:@\"]{1,64}:[^/\s@\"]{1,120}@")),
    # Prefixed tokens that the generic sweep cannot see: no key name before them.
    # The lookbehind keeps a prefix inside a longer identifier from matching.
    ("npm-token", re.compile(r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{30,}")),
    ("hf-token", re.compile(r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}")),
    ("supabase-secret", re.compile(r"(?<![A-Za-z0-9_])sb_secret_[A-Za-z0-9_-]{16,}")),
    # Bot tokens: a numeric bot id, a colon, then 35 url-safe characters.
    ("telegram-bot-token", re.compile(r"(?<![A-Za-z0-9_])\d{6,12}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    # UPPER_SNAKE env names, case-sensitive: STRIPE_SECRET_KEY=…, API_KEY = '…'.
    # The generic sweep below misses these because the name goes on past the
    # keyword (_KEY) or the value starts with a quote. Case-sensitive on
    # purpose, so design tokens (accentToken, --color-token-primary) and
    # tokenCount stay. The transcript is raw JSONL, so a quote may arrive
    # escaped (\"); the value never takes a backslash, so no escape is split
    # and the line stays valid JSON.
    (
        "env-secret",
        re.compile(
            r"(?:(?<![A-Za-z0-9_+/])|(?<=\\[nrt]))"
            r"([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*)"
            r"(\s*[:=]\s*)"
            r"(?:(\\?[\"'])([^\"'\\\r\n]{3,})|([^\s\"'\\,;}&]{4,}))"
        ),
    ),
    # Generic key=value / "key": "value". Deliberately last and deliberately
    # broad: unknown secret shapes are the ones that bite.
    #
    # The key may carry a prefix (DB_PASSWORD, X_API_KEY, pg.password) — hence
    # the leading char class rather than \b. `\b` fails here precisely because
    # `_` is a word character, so \bpass never matches inside DB_PASSWORD; the
    # selftest caught that. `authorization` is spelled out instead of bare
    # `auth` so git JSON's "author":"…" is not swallowed — over-redaction is
    # acceptable, but redacting every commit author would gut the audit value.
    (
        "secret-value",
        re.compile(
            r"(?i)([A-Za-z0-9_.\-]*(?:pass(?:word|wd)?|secret|token"
            r"|api[_-]?key|access[_-]?key|client[_-]?secret|authorization))"
            r"(\"?\s*[:=]\s*\"?)"
            r"(?!\[REDACTED:)([^\s\"',}&]{6,})"
        ),
    ),
]


def redact(text):
    """Return (redacted_text, {label: count})."""
    counts = {}

    def bump(label, n=1):
        counts[label] = counts.get(label, 0) + n

    for label, rx in PATTERNS:
        if label == "secret-value":
            def sub(m):
                bump(label)
                return f"{m.group(1)}{m.group(2)}[REDACTED:{label}]"
            text, n = rx.subn(sub, text)
        elif label == "env-secret":
            # Keep the name, the separator and an opening quote; the closing
            # quote is never consumed, so it stays in place.
            def sub(m):
                bump(label)
                return f"{m.group(1)}{m.group(2)}{m.group(3) or ''}[REDACTED:{label}]"
            text, n = rx.subn(sub, text)
        elif label == "basic-auth-url":
            def sub(m):
                bump(label)
                return "://[REDACTED:basic-auth]@"
            text, n = rx.subn(sub, text)
        else:
            def sub(m, _l=label):
                bump(_l)
                return f"[REDACTED:{_l}]"
            text, n = rx.subn(sub, text)
    return text, counts


def redact_stream(fh, out):
    """Line-oriented so one unparseable line cannot poison the whole archive."""
    total, dropped = {}, 0
    for line in fh:
        try:
            red, counts = redact(line)
            # A redaction must never break the line's JSON-ness. Cheap
            # structural check: it stays one line and keeps balanced quoting
            # well enough to load. Only validate lines that started as JSON.
            if line.lstrip().startswith("{"):
                import json

                json.loads(red)
            out.write(red)
        except Exception:
            dropped += 1
            out.write('{"redaction_error":"line dropped by redact-transcript"}\n')
            continue
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
    return total, dropped


def redact_tree(src, dest):
    """Redact every *.jsonl under src into dest/<same relative path>.gz.

    For the subagent transcripts that sit in <session>/subagents/. Each file
    is written to a temp name and renamed, so a crash leaves no partial
    archive under the final name. A file that cannot be read is skipped and
    counted; it is never copied raw.
    """
    import gzip
    import os

    files, failed, dropped, totals = 0, 0, 0, {}
    for root, _dirs, names in os.walk(src):
        for name in sorted(names):
            if not name.endswith(".jsonl"):
                continue
            rel = os.path.relpath(os.path.join(root, name), src)
            out = os.path.join(dest, rel + ".gz")
            tmp = out + ".tmp"
            try:
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh, \
                        gzip.open(tmp, "wt", encoding="utf-8") as gz:
                    t, d = redact_stream(fh, gz)
                os.replace(tmp, out)
            except Exception:
                failed += 1
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                continue
            files += 1
            dropped += d
            for k, v in t.items():
                totals[k] = totals.get(k, 0) + v
    return files, failed, dropped, totals


def selftest():
    failures = []

    def check(name, ok, detail=""):
        print(f"{'ok' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    import io
    import json

    jwt ="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    cases = [
        ("jwt in a query param", f"GET /rtc/v1?access_token={jwt}&x=1", jwt),
        ("github token", "using ghp_abcdefghijklmnopqrstuvwxyz012345", "ghp_abcdefghijklmnopqrstuvwxyz012345"),
        ("aws key id", "AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
        ("bearer header", "Authorization: Bearer abcdef1234567890abcdef1234567890", "abcdef1234567890abcdef1234567890"),
        ("password kv", 'DB_PASSWORD=sup3rs3cretvalue', "sup3rs3cretvalue"),
        ("json secret", '{"api_key": "aaaabbbbccccddddeeee"}', "aaaabbbbccccddddeeee"),
        ("basic auth url", "postgres://user:hunter2hunter2@db.example/x", "hunter2hunter2"),
        ("slack token", "xoxb-1234567890-abcdefghij", "xoxb-1234567890-abcdefghij"),
        ("stripe key", "sk_live_abcdefghijklmnop123456", "sk_live_abcdefghijklmnop123456"),
        ("pem block", "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
    ]
    for name, raw, secret in cases:
        red, _ = redact(raw)
        check(f"redacts: {name}", secret not in red, f"leaked in {red[:70]!r}")

    # Shapes that passed before the env-secret and prefixed-token rules.
    # Synthetic values only; each is built so no real token sits in this file.
    new_cases = [
        ("env: name runs past the keyword", "STRIPE_SECRET_KEY=" + "q7Rz" * 6, "q7Rz" * 6),
        ("env: private key name", "PRIVATE_KEY=" + "m3Kd" * 5, "m3Kd" * 5),
        ("env: credential name", "X_CREDENTIAL=" + "p9Lw" * 5, "p9Lw" * 5),
        ("env: spaces and single quotes", "API_KEY = '" + "v2Hs" * 5 + "'", "v2Hs" * 5),
        ("env: double quotes", 'DB_PASSWD="' + "t4Nb" * 4 + '"', "t4Nb" * 4),
        ("env: access key, colon", "AWS_SECRET_ACCESS_KEY: " + "Zx8/Qw" * 5, "Zx8/Qw" * 5),
        ("token: npm", "auth " + "npm_" + "aB3d" * 9, "aB3d" * 9),
        ("token: hf", "token hf_" + "Kq2w" * 9, "Kq2w" * 9),
        ("token: sb_secret", "key sb_secret_" + "Yt6u" * 7, "Yt6u" * 7),
        ("token: telegram bot", "bot " + "8123456789:" + "AAH" + "x7_P-" * 6 + "q2", "x7_P-" * 6),
    ]
    for name, raw, secret in new_cases:
        red, _ = redact(raw)
        check(f"redacts: {name}", secret not in red, f"leaked in {red[:70]!r}")
    # The same shape inside a raw JSONL line, where quotes arrive escaped.
    jl = json.dumps({"content": "export API_KEY = \"" + "w8Fj" * 5 + "\"\nnext\nSTRIPE_SECRET_KEY=" + "r5Gc" * 6}) + "\n"
    buf = io.StringIO()
    _, dropped_e = redact_stream(io.StringIO(jl), buf)
    check("redacts: escaped-quote value inside JSONL, line still JSON",
          "w8Fj" * 5 not in buf.getvalue() and "r5Gc" * 6 not in buf.getvalue() and dropped_e == 0
          and "\nnext\n" in json.loads(buf.getvalue())["content"],
          f"got {buf.getvalue()[:90]!r}")

    # Positive controls: design tokens, counters, prose and image data are
    # not secrets, and must come out byte-identical.
    png = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    import base64
    blob = base64.b64encode(bytes((i * 37 + 11) % 256 for i in range(3000))).decode()
    controls = [
        "accentToken: '#0e5f68'",
        "--color-token-primary: #fff;",
        "tokens.spacing.md",
        "const tokenCount = 12",
        "Type it into the password field and press enter.",
        "data:image/png;base64," + png,
        json.dumps({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": blob}}),
    ]
    for c in controls:
        red, _ = redact(c)
        check(f"preserves: {c[:40]!r}", red == c, f"got {red[:80]!r}")

    keep = "the compile failed because CRS 932140 matched IF x==y in the plan body"
    red, _ = redact(keep)
    check("preserves: ordinary prose untouched", red == keep, f"got {red!r}")

    # Structure: a realistic transcript line survives as loadable JSON.
    line = json.dumps({
        "type": "tool_result",
        "content": f"Authorization: Bearer {jwt}\nDB_PASSWORD=letmein123",
        "ok": True,
    }) + "\n"
    out = io.StringIO()
    totals, dropped = redact_stream(io.StringIO(line), out)
    got = out.getvalue()
    check("structure: output is one line", got.count("\n") == 1)
    try:
        obj = json.loads(got)
        loadable = True
    except Exception as e:
        obj, loadable = None, False
    check("structure: output is valid JSON", loadable)
    check("structure: no secret survives the round trip",
          loadable and jwt not in got and "letmein123" not in got)
    check("structure: non-secret fields preserved",
          loadable and obj.get("type") == "tool_result" and obj.get("ok") is True)
    check("accounting: redactions counted", bool(totals), f"got {totals}")
    check("accounting: nothing dropped on valid input", dropped == 0)

    # Fail closed: a line that cannot be handled is replaced, never emitted raw.
    out2 = io.StringIO()
    _, dropped2 = redact_stream(io.StringIO('{"broken": '), out2)
    check("fail-closed: unparseable line is dropped, not passed through",
          dropped2 == 1 and "broken" not in out2.getvalue(),
          f"got {out2.getvalue()[:60]!r}")

    # --tree: subagent transcripts, nested, redacted into .gz, nothing else copied.
    import gzip
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src")
        os.makedirs(os.path.join(src, "workflows", "wf_1"))
        with open(os.path.join(src, "agent-a1.jsonl"), "w") as f:
            f.write(json.dumps({"content": "DB_PASSWORD=letmein123"}) + "\n")
        with open(os.path.join(src, "workflows", "wf_1", "agent-b2.jsonl"), "w") as f:
            f.write(json.dumps({"content": "plain " + jwt}) + "\n")
        with open(os.path.join(src, "agent-a1.meta.json"), "w") as f:
            f.write("{}")
        dest = os.path.join(td, "dest")
        n, bad, _, _ = redact_tree(src, dest)
        got = []
        for root, _d, names in os.walk(dest):
            got += [os.path.relpath(os.path.join(root, x), dest) for x in names]
        check("tree: nested jsonl archived, meta skipped",
              n == 2 and bad == 0 and sorted(got) == ["agent-a1.jsonl.gz", os.path.join("workflows", "wf_1", "agent-b2.jsonl.gz")],
              f"got {sorted(got)}")
        body = "".join(gzip.open(os.path.join(dest, g), "rt").read() for g in got if g.endswith(".gz"))
        check("tree: secrets redacted in every file", "letmein123" not in body and jwt not in body)

    if failures:
        print(f"{len(failures)} FAILED")
        sys.exit(1)
    print("redact-transcript selftest: ALL PASS")
    sys.exit(0)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    if len(sys.argv) == 4 and sys.argv[1] == "--tree":
        files, failed, dropped, totals = redact_tree(sys.argv[2], sys.argv[3])
        summary = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()))
        print(f"redact-transcript: {files} files" + (f", {summary}" if summary else "")
              + (f", dropped={dropped}" if dropped else "")
              + (f", failed={failed}" if failed else ""), file=sys.stderr)
        sys.exit(1 if failed else 0)
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    src = sys.argv[1]
    fh = sys.stdin if src == "-" else open(src, encoding="utf-8", errors="replace")
    totals, dropped = redact_stream(fh, sys.stdout)
    if totals or dropped:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()))
        print(
            f"redact-transcript: {summary or 'no matches'}"
            + (f", dropped={dropped}" if dropped else ""),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
