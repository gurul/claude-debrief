# Verbatim archive (`sessions/raw/`) — redacted, retained, never indexed

Off by default. Enable with `DEBRIEF_RAW_ARCHIVE=1` on the SessionEnd hook:

```jsonc
// .claude/settings.local.json → hooks.SessionEnd[].hooks[]
{
  "type": "command",
  "command": "$CLAUDE_PROJECT_DIR/debrief/.system/session-debrief.sh",
  "async": true,
  "env": { "DEBRIEF_RAW_ARCHIVE": "1", "DEBRIEF_RAW_RETAIN_DAYS": "180" }
}
```

It exists because Claude Code deletes transcripts after ~30 days. Day mode may
fall back to it when a live transcript is gone; nothing else touches it.

## Why you'd want it

Dailies and session notes are **summaries** — they keep the conclusions and drop
the evidence. The archive is what you reach for when the conclusion isn't enough:

| You need | Summary gives you | Archive gives you |
|---|---|---|
| **The exact file that broke** | "the WAF rejected plan bodies" | `terraform/environments/staging/main.tf:683`, the line and its value |
| **The exact identifier** | "a CRS rule matched" | `owasp-crs-v030301-id932140-rce`, `priority 1000`, `body_denied_by_security_policy` |
| **The exact command that proved it** | "verified in the cluster" | the literal `kubectl`/`gcloud` invocation and its output |
| **What an overnight run actually did** | the agent's own account of itself | every tool call, in order, including the ones it didn't mention |

That last row is the strongest reason. For autonomous sessions you did not watch,
a self-written summary is the agent grading its own homework; the transcript is
the only independent record. Concretely, `grep -l` over the archive answers *"which
session touched this file, and what did it run?"* — a question the semantic layer
is structurally unable to answer, because it stores understanding rather than acts.

```bash
# which archived session touched a file, and what did it do there?
zgrep -l "SimulateStage.tsx" debrief/sessions/raw/*/*.jsonl.gz
zgrep -h "kubectl -n tensorzero" debrief/sessions/raw/2026-07-24/*.jsonl.gz | head
```

## Redaction is not optional

The transcript contains everything the tool calls echoed, and the leak vector is
**not** credentials handled deliberately — it is third-party output that happens
to carry one. Measured case: a LiveKit `access_token=eyJ…` arrived inside a
Traefik access-log line during an unrelated outage investigation, and a raw
archive would have banked it verbatim.

So the transcript is piped through `.system/redact-transcript.py` before gzip.
It scrubs JWTs, `Bearer` tokens, GitHub/Slack/Stripe/Google/AWS/OpenAI/Anthropic
key shapes, PEM private-key blocks, `user:pass@host` URLs, and a broad
`*password|*secret|*token|*api_key… = value` sweep — replacing each with
`[REDACTED:<kind>]`.

Three properties matter more than the pattern list:

- **Output stays valid JSONL, line for line.** Day mode parses this as its
  fallback; a redactor that corrupts structure destroys the only reason the
  archive exists. Verified against a real 86-line transcript: 86 lines out, zero
  invalid JSON, zero residual tokens.
- **Fail closed.** A missing or failing redactor means **no archive**, not a raw
  one. Same for a truncated pipe — gzip's exit status is not trusted, because
  gzip succeeds happily on a truncated stream.
- **Over-redaction is fine; under-redaction is not.** One deliberate exception:
  `authorization` is spelled out rather than bare `auth`, so git JSON's
  `"author":"…"` survives. Redacting every commit author would gut the audit
  value the archive exists for.

`redact-transcript.py --selftest` asserts all of it — including that a
`DB_PASSWORD=` style key is caught (`\bpass` does **not** match inside
`DB_PASSWORD`, because `_` is a word character; the selftest caught that bug).

## Retention

`DEBRIEF_RAW_RETAIN_DAYS` (default **180**) prunes whole date directories past
the horizon on each hook run. Compared by directory **name**, not mtime, so a
backup pass or filesystem copy cannot silently extend an archive's life. Set `0`
to disable pruning — but "forever" is otherwise a decision you make once, by
accident, and cannot undo.

Volume is not the constraint: measured at ~0.6–3.5 MB/day raw, ~59% after gzip —
roughly **1 MB/day, ~365 MB/year**.

## Still never indexed

Stored as `.jsonl.gz`, structurally invisible to `debrief-search.py`'s
`collect()` (non-`.md`, no curated frontmatter). This is deliberate and load
bearing: the moment raw transcripts become searchable, unverified machine claims
re-enter memory through the back door — exactly what the curation gate exists to
prevent. The archive is insurance and audit, never a search corpus.

## Reading one anyway — the `verbatim` tab

Never-indexed is not never-readable. The viewer has a **verbatim** tab that lists
every archive by day and renders one on click. Decompression happens in the
browser (`DecompressionStream`), on an explicit human action, and nothing is
written back to disk — so the `.jsonl.gz` on disk stays the only copy and stays
invisible to retrieval.

Two things it deliberately does not show:

- **Tool results.** Only the tool *name*, as a chip. Re-reading raw tool output
  is the most common way a refuted intermediate conclusion gets laundered back
  into memory as fact.
- **Reasoning blocks.** Shown as a `reasoning` chip, never as text, for the same
  reason.

The pane carries a standing "unverified, evidence not memory" banner and is
styled plainer than the curated layers, because the styling should not invite a
trust the content has not earned.

**The agent rule.** An agent must not read these archives on its own initiative —
only when a human names a specific session and asks. Everything in raw is
unverified by construction: every abandoned theory and misread file survives
verbatim with nothing marking it wrong. Drop a note stating this into
`sessions/raw/README.md` in your live install (that path is gitignored here, so
it cannot ship from this repo).
