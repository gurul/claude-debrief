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
            r"([^\s\"',}&]{6,})"
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


def selftest():
    failures = []

    def check(name, ok, detail=""):
        print(f"{'ok' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    import json

    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
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

    keep = "the compile failed because CRS 932140 matched IF x==y in the plan body"
    red, _ = redact(keep)
    check("preserves: ordinary prose untouched", red == keep, f"got {red!r}")

    # Structure: a realistic transcript line survives as loadable JSON.
    line = json.dumps({
        "type": "tool_result",
        "content": f"Authorization: Bearer {jwt}\nDB_PASSWORD=letmein123",
        "ok": True,
    }) + "\n"
    import io
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

    sys.exit(1 if failures else 0)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
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
