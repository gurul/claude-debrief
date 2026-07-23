#!/usr/bin/env python3
"""debrief-backlog — surface the uncurated debrief backlog to a human.

The SessionEnd sweep in session-debrief.sh already *detects* the backlog: it
flips landed-but-uncurated drafts to `unconsumed`, marks dead drafters
`failed`, and logs a one-line count to .system/hook.log on every sweep. But
hook.log is a file no human opens, so the backlog is counted and then stranded.
This is the surfacing end of that loop: a read-only report the SessionStart
nudge (backlog-nudge.sh) and the /debrief-backlog slash command both consume.

    debrief-backlog.py            # full per-date report; silent on a clean queue
    debrief-backlog.py --nudge    # one line for the hook; silent on a clean queue
    debrief-backlog.py selftest   # pass/fail invariants over a synthetic queue

The curation gate is absolute here: this reads sessions/queue.jsonl and stat()s
transcript/raw files, and writes NOTHING — no daily, no INDEX.md, no queue
status, no archive. Draining the backlog stays a human `/debrief day` pass.
That is the whole point of the system; a surfacing tool that could write memory
would be the exact laundering the gate exists to prevent.

Debrief dir resolution: --dir flag, then $DEBRIEF_DIR, then the script's own
location (<debrief>/.system/debrief-backlog.py -> <debrief>). NEVER
os.path.realpath: a live install symlinks .system/*.py from this clone, and
realpath would jump to the clone's gitignored-empty sessions/ instead of the
install's real queue. abspath preserves the symlink path. Same foot-gun the
provenance builder documents.
"""

import argparse
import json
import os
import sys

# ---------------------------------------------------------------- location


def find_debrief_dir(cli_dir):
    if cli_dir:
        return os.path.abspath(cli_dir)
    env = os.environ.get("DEBRIEF_DIR")
    if env:
        return os.path.abspath(env)
    # abspath, NOT realpath — see module docstring. The script sits at
    # <debrief>/.system/debrief-backlog.py, so two dirnames up is <debrief>.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- scan


def _raw_archive_path(debrief, draft):
    """Where the opt-in cold archive would be for a given draft path.

    Draft lands at <debrief>/sessions/<date>/<stem>.md; the raw transcript, if
    DEBRIEF_RAW_ARCHIVE was on, is <debrief>/sessions/raw/<date>/<stem>.jsonl.gz
    — same stem, mirrored under raw/. Mirrors session-debrief.sh's naming.
    """
    if not draft:
        return None
    date = os.path.basename(os.path.dirname(draft))
    stem = os.path.splitext(os.path.basename(draft))[0]
    return os.path.join(debrief, "sessions", "raw", date, stem + ".jsonl.gz")


def _failed_recovery(debrief, d):
    """(recoverable: bool, reason: str) for a failed queue entry.

    A `failed` drafter is not terminal if its material still exists: the live
    transcript (day mode reads transcript_path directly) or the raw cold
    archive (day mode's fallback when Claude Code has deleted the transcript
    after ~30 days). Only when both are gone is the episode unrecoverable.
    """
    tp = d.get("transcript_path", "")
    if tp and os.path.exists(tp):
        return True, "transcript on disk"
    raw = _raw_archive_path(debrief, d.get("draft", ""))
    if raw and os.path.exists(raw):
        return True, "raw archive on disk"
    return False, "transcript & raw gone"


def scan(debrief):
    """Read the queue read-only. Returns (unconsumed_by_date, failed).

    unconsumed_by_date: {date: count} for status == "unconsumed".
    failed: [(date, recoverable, reason)] for status == "failed".
    Dates come from ended_at[:10] — the local date the sweep also groups on.
    """
    queue = os.path.join(debrief, "sessions", "queue.jsonl")
    unconsumed, failed = {}, []
    if not os.path.exists(queue):
        return unconsumed, failed
    with open(queue, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            status = d.get("status")
            ended = d.get("ended_at", "")
            date = ended[:10] if len(ended) >= 10 else "unknown"
            if status == "unconsumed":
                unconsumed[date] = unconsumed.get(date, 0) + 1
            elif status == "failed":
                ok, reason = _failed_recovery(debrief, d)
                failed.append((date, ok, reason))
    return unconsumed, failed


# ---------------------------------------------------------------- render


def _plural(n):
    return "" if n == 1 else "s"


def render_nudge(unconsumed, failed):
    """One line for the SessionStart hook; empty string on a clean queue."""
    n = sum(unconsumed.values())
    recoverable = sum(1 for _, ok, _ in failed if ok)
    terminal = sum(1 for _, ok, _ in failed if not ok)
    segs = []
    if n:
        verb = "awaits" if n == 1 else "await"
        segs.append(f"{n} draft{_plural(n)} {verb} curation (oldest {min(unconsumed)})")
    if recoverable:
        segs.append(f"{recoverable} failed still recoverable")
    if terminal:
        segs.append(f"{terminal} failed terminal")
    if not segs:
        return ""
    return "DEBRIEF BACKLOG: " + "; ".join(segs) + ". Run /debrief-backlog."


def render_full(unconsumed, failed):
    """Per-date report with the exact /debrief day commands; empty when clean."""
    n = sum(unconsumed.values())
    if not n and not failed:
        return ""
    lines = ["DEBRIEF BACKLOG"]
    if n:
        dates = len(unconsumed)
        lines.append(
            f"{n} draft{_plural(n)} awaiting curation across "
            f"{dates} date{_plural(dates)} (oldest {min(unconsumed)}):"
        )
        for date in sorted(unconsumed):
            c = unconsumed[date]
            lines.append(f"  {date}  {c} draft{_plural(c)}   -> /debrief day {date}")
    if failed:
        lines.append("")
        lines.append(f"Failed drafters ({len(failed)}):")
        for date, ok, reason in sorted(failed):
            if ok:
                lines.append(
                    f"  {date}  recoverable via /debrief day {date}  ({reason})"
                )
            else:
                lines.append(f"  {date}  terminal — {reason}")
    lines.append("")
    lines.append(
        "Only a human /debrief day pass promotes drafts into memory — "
        "this report never writes a daily or touches INDEX.md."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest


def cmd_selftest():
    """Pass/fail invariants over a synthetic queue — never a score.

    The central assertion is the curation gate: this tool surfaces the backlog
    and NEVER mutates the queue. The rest defends the surfacing itself
    (unconsumed dates appear, a clean queue stays silent, failed recovery is
    classified from real files) and the symlink-safe path resolution a live
    install depends on.
    """
    import hashlib
    import subprocess
    import tempfile

    failures = []

    def check(name, ok, detail=""):
        print(f"{'ok' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    def write_queue(path, rows):
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "sessions", "2026-01-05"))
        os.makedirs(os.path.join(td, "sessions", "2026-01-08"))
        os.makedirs(os.path.join(td, "sessions", "raw", "2026-01-06"))
        queue = os.path.join(td, "sessions", "queue.jsonl")

        # --- clean queue: everything aggregated -> both renders silent -------
        write_queue(queue, [
            {"status": "aggregated", "ended_at": "2026-01-05T10:00:00-0700",
             "draft": f"{td}/sessions/2026-01-05/1000-aaaaaaaa.md"},
        ])
        un, fa = scan(td)
        check("clean queue: nudge silent", render_nudge(un, fa) == "")
        check("clean queue: full report silent", render_full(un, fa) == "")

        # --- backlog: two unconsumed dates + three failed shapes ------------
        # transcript-surviving failed (recoverable), raw-surviving failed
        # (recoverable), and neither (terminal).
        open(f"{td}/sessions/2026-01-05/1200-bbbbbbbb.md", "w").write("draft\n")
        open(f"{td}/sessions/2026-01-08/0900-cccccccc.md", "w").write("draft\n")
        live_transcript = f"{td}/transcript-live.jsonl"
        open(live_transcript, "w").write("{}\n")
        open(f"{td}/sessions/raw/2026-01-06/0800-dddddddd.jsonl.gz", "w").write("gz\n")
        rows = [
            {"status": "aggregated", "ended_at": "2026-01-04T10:00:00-0700",
             "draft": f"{td}/sessions/2026-01-04/1000-eeeeeeee.md"},
            {"status": "unconsumed", "ended_at": "2026-01-05T12:00:00-0700",
             "draft": f"{td}/sessions/2026-01-05/1200-bbbbbbbb.md"},
            {"status": "unconsumed", "ended_at": "2026-01-08T09:00:00-0700",
             "draft": f"{td}/sessions/2026-01-08/0900-cccccccc.md"},
            # failed, transcript still on disk -> recoverable
            {"status": "failed", "ended_at": "2026-01-06T07:58:00-0700",
             "transcript_path": live_transcript,
             "draft": f"{td}/sessions/2026-01-06/0758-ffffffff.md"},
            # failed, transcript gone but raw archived -> recoverable
            {"status": "failed", "ended_at": "2026-01-06T08:00:00-0700",
             "transcript_path": f"{td}/gone-transcript.jsonl",
             "draft": f"{td}/sessions/2026-01-06/0800-dddddddd.md"},
            # failed, neither transcript nor raw -> terminal
            {"status": "failed", "ended_at": "2026-01-07T08:00:00-0700",
             "transcript_path": f"{td}/also-gone.jsonl",
             "draft": f"{td}/sessions/2026-01-07/0800-99999999.md"},
        ]
        write_queue(queue, rows)
        before = hashlib.sha256(open(queue, "rb").read()).hexdigest()

        un, fa = scan(td)
        check(
            "unconsumed: grouped by date, aggregated excluded",
            un == {"2026-01-05": 1, "2026-01-08": 1},
            f"got {un}",
        )
        recov = [(d, r) for d, ok, r in fa if ok]
        term = [(d, r) for d, ok, r in fa if not ok]
        check(
            "failed: transcript-on-disk classified recoverable",
            ("2026-01-06", "transcript on disk") in recov,
            f"recoverable={recov}",
        )
        check(
            "failed: raw-archive-only classified recoverable",
            ("2026-01-06", "raw archive on disk") in recov,
            f"recoverable={recov}",
        )
        check(
            "failed: no transcript & no raw classified terminal",
            term == [("2026-01-07", "transcript & raw gone")],
            f"terminal={term}",
        )

        nudge = render_nudge(un, fa)
        check(
            "nudge: single non-empty line naming oldest date",
            nudge.count("\n") == 0 and "oldest 2026-01-05" in nudge and nudge != "",
            repr(nudge),
        )
        check(
            "nudge: separates recoverable from terminal failed",
            "2 failed still recoverable" in nudge and "1 failed terminal" in nudge,
            repr(nudge),
        )
        full = render_full(un, fa)
        check(
            "full: emits exact /debrief day command per stranded date, oldest first",
            "/debrief day 2026-01-05" in full
            and "/debrief day 2026-01-08" in full
            and full.index("2026-01-05") < full.index("2026-01-08"),
        )
        check(
            "full: recoverable failed points at /debrief day; terminal does not",
            "recoverable via /debrief day 2026-01-06" in full
            and "2026-01-07  terminal" in full,
        )

        after = hashlib.sha256(open(queue, "rb").read()).hexdigest()
        check("gate: queue byte-identical after scan (read-only)", before == after)

        # --- symlink correctness: invoked THROUGH a symlink, the CLI must read
        # the symlinked install's queue, not the clone the symlink points into.
        # abspath preserves the symlink path; realpath would read the wrong dir.
        install = os.path.join(td, "install", "debrief")
        os.makedirs(os.path.join(install, ".system"))
        os.makedirs(os.path.join(install, "sessions"))
        write_queue(os.path.join(install, "sessions", "queue.jsonl"), [
            {"status": "unconsumed", "ended_at": "2099-12-31T23:59:00-0700",
             "draft": f"{install}/sessions/2099-12-31/2359-abcdef01.md"},
        ])
        real_script = os.path.abspath(__file__)
        link = os.path.join(install, ".system", "debrief-backlog.py")
        os.symlink(real_script, link)
        env = dict(os.environ)
        env.pop("DEBRIEF_DIR", None)  # force __file__-based resolution
        out = subprocess.run(
            [sys.executable, link, "--nudge"],
            capture_output=True, text=True, env=env, cwd="/",
        ).stdout
        check(
            "symlink: CLI reads the install's queue (abspath), not the clone",
            "oldest 2099-12-31" in out,
            repr(out),
        )
        # Guard the regression directly: realpath resolution would resolve the
        # symlink to real_script and land two dirs up from THAT — a different
        # dir than the install. Assert they genuinely differ.
        abspath_dir = os.path.dirname(os.path.dirname(link))
        realpath_dir = os.path.dirname(os.path.dirname(os.path.realpath(link)))
        check(
            "symlink: realpath would resolve to a different (wrong) dir",
            os.path.abspath(abspath_dir) != os.path.abspath(realpath_dir)
            and os.path.abspath(abspath_dir) == os.path.abspath(install),
        )

    return 1 if failures else 0


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(
        prog="debrief-backlog",
        description="Read-only report of the uncurated debrief backlog.",
    )
    ap.add_argument("--dir", help="debrief directory (default: $DEBRIEF_DIR or script location)")
    ap.add_argument(
        "--nudge", action="store_true",
        help="one-line summary for the SessionStart hook; silent when clean",
    )
    ap.add_argument("mode", nargs="?", choices=["selftest"], help="run invariant selftest")
    args = ap.parse_args()

    if args.mode == "selftest":
        sys.exit(cmd_selftest())

    debrief = find_debrief_dir(args.dir)
    unconsumed, failed = scan(debrief)
    out = render_nudge(unconsumed, failed) if args.nudge else render_full(unconsumed, failed)
    if out:
        print(out)
    sys.exit(0)


if __name__ == "__main__":
    main()
